#!/usr/bin/env python3
"""Host checks for the three-call proposal, certifi overlay, and canary. No image."""
import base64
import copy
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import pathlib
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[4]
REDACTION = ROOT / "scripts" / "authentik-userinfo-redaction"
PROPOSAL = ROOT / ".agents" / "reports" / "portal178-m5c" / "pending" / "authentik-userinfo-log.patch"


def load(name):
    path = str(REDACTION)
    if path not in sys.path:
        sys.path.insert(0, path)
    spec = importlib.util.spec_from_file_location(name, REDACTION / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compare_canary = load("compare_canary")
install_certifi = load("install_certifi")
sys.modules["install_certifi"] = install_certifi
verify_candidate = load("verify_candidate")


def denial(branch, visible, level, artifact):
    debug = level == "DEBUG"
    message = debug or branch == "revoked"
    return {
        "shape": "opaque", "branch": branch, "status": 401,
        "logMessageObserved": message,
        "logCanaryVisible": visible and message,
        "eventCanaryVisible": False, "responseCanaryVisible": False,
        "eventPathExercised": branch == "revoked",
        "formattedJson": message, "severityObserved": message,
        "denialUnchanged": True, "successSubject": False,
    }


def success():
    return {
        "shape": "opaque", "branch": "success", "status": 200,
        "logMessageObserved": False, "logCanaryVisible": False,
        "eventCanaryVisible": False, "responseCanaryVisible": False,
        "eventPathExercised": False, "formattedJson": False,
        "severityObserved": False, "denialUnchanged": False, "successSubject": True,
    }


def matrix(artifact, level):
    cases = []
    for shape in ("opaque", "jwt-shaped"):
        for branch in ("missing", "expired", "revoked"):
            item = denial(branch, artifact == "base", level, artifact)
            item["shape"] = shape
            cases.append(item)
        item = success()
        item["shape"] = shape
        cases.append(item)
    return {
        "proof": True, "artifact": artifact, "logLevel": level,
        "utilsSha256": compare_canary.SOURCE_SHA if artifact == "base" else compare_canary.PATCHED_SHA,
        "debugEnabled": level == "DEBUG", "cases": cases,
        "realTokens": False, "databaseWrites": False,
        "productionTokenLeakProven": False,
    }


class ProposalTests(unittest.TestCase):
    def test_patch_removes_only_three_arguments(self):
        lines = PROPOSAL.read_text().splitlines()
        removed = [line[1:] for line in lines if line.startswith("-") and not line.startswith("---")]
        added = [line[1:] for line in lines if line.startswith("+") and not line.startswith("+++")]
        self.assertEqual(len(removed), 3)
        self.assertEqual(len(added), 3)
        for old, new in zip(removed, added):
            self.assertTrue(old.endswith(', access_token=access_token)'))
            self.assertEqual(new, old.replace(", access_token=access_token", ""))
        self.assertEqual(sum(line.startswith("@@") for line in lines), 1)

    def test_compare_requires_baseline_visibility(self):
        results = {
            ("base", "DEBUG"): matrix("base", "DEBUG"),
            ("patched", "DEBUG"): matrix("patched", "DEBUG"),
            ("base", "INFO"): matrix("base", "INFO"),
            ("patched", "INFO"): matrix("patched", "INFO"),
        }
        report = compare_canary.compare(results)
        self.assertTrue(report["compared"])
        self.assertEqual(report["cases"], 32)
        blinded = copy.deepcopy(results)
        blinded[("base", "DEBUG")]["cases"][0]["logCanaryVisible"] = False
        with self.assertRaises(SystemExit):
            compare_canary.compare(blinded)

    def test_compare_rejects_a_marker_in_output(self):
        raw = json.dumps(matrix("base", "DEBUG")) + compare_canary.MARKERS[0]
        file = pathlib.Path(self._tmp())
        file.write_text(raw)
        with self.assertRaises(SystemExit):
            compare_canary.load_result(file)

    def _tmp(self):
        import tempfile
        handle = tempfile.NamedTemporaryFile(delete=False)
        handle.close()
        self.addCleanup(pathlib.Path(handle.name).unlink)
        return handle.name

    def test_runtime_identity_ignores_environment_values(self):
        def image(layers, env):
            return [{
                "Architecture": "amd64", "Os": "linux", "Id": "sha256:abc",
                "RootFS": {"Layers": layers},
                "Config": {
                    "User": "1000", "Entrypoint": ["dumb-init", "--", "ak"], "Cmd": None,
                    "WorkingDir": "/", "Env": env,
                    "Healthcheck": {"Test": ["CMD", "ak", "healthcheck"]},
                    "Labels": {"org.opencontainers.image.version": "2026.8.5"},
                },
            }]
        base = verify_candidate.runtime_identity(image(["layer-a"], ["PATH=/usr/bin", "TMPDIR=/tmp"]))
        candidate = verify_candidate.runtime_identity(image(["layer-a", "layer-b"], ["PATH=/usr/bin", "TMPDIR=/tmp"]))
        report = verify_candidate.same_runtime(base, candidate)
        self.assertTrue(report["runtimeMatch"])
        self.assertEqual(report["user"], "1000")
        changed = verify_candidate.runtime_identity(image(["layer-a", "layer-b"], ["PATH=/usr/bin", "TMPDIR=/var/tmp"]))
        self.assertFalse(verify_candidate.same_runtime(base, changed)["runtimeMatch"])

    def test_reviewed_pin_matches_config_and_image_id(self):
        pin = json.loads((REDACTION / "reviewed-digests.json").read_text())
        self.assertEqual(pin["candidateImageId"], "sha256:" + pin["candidateConfigSha256"])
        self.assertEqual(len(pin["archiveSha256"]), 64)
        self.assertTrue(pin["buildxManifestDigest"].startswith("sha256:"))

    def test_layer_archive_rejects_utils_only_and_unrelated_files(self):
        path = pathlib.Path(self._tmp())
        path.write_bytes(layer_archive({verify_candidate.UTILS: patched_utils()}))
        with self.assertRaises(SystemExit):
            verify_candidate.layer_members(path)
        files = overlay_files()
        files["authentik/providers/oauth2/utils.pyc"] = b"x"
        path.write_bytes(layer_archive(files))
        with self.assertRaises(SystemExit):
            verify_candidate.layer_members(path)


FIXTURES = ROOT / "scripts" / "tests" / "portal178-m5c" / "authentik" / "fixtures"
OLD_WHEEL = FIXTURES / "certifi-2026.10.19-py3-none-any.whl"
NEW_WHEEL = REDACTION / "certifi-2026.10.23-py3-none-any.whl"
OLD_WHEEL_SHA = "42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def wheel_map(path):
    archive = zipfile.ZipFile(path)
    return {name: archive.read(name) for name in archive.namelist()}


def patched_utils():
    original = (FIXTURES / "utils-2026.8.5.py").read_bytes()
    if sha256(original) != verify_candidate.SOURCE_SHA:
        raise AssertionError("utils fixture is not the 2026.8.5 source")
    text = original.decode()
    lines = PROPOSAL.read_text().splitlines()
    removed = [line[1:] for line in lines if line.startswith("-") and not line.startswith("---")]
    added = [line[1:] for line in lines if line.startswith("+") and not line.startswith("+++")]
    for old, new in zip(removed, added):
        if text.count(old) != 1:
            raise AssertionError("proposal line missing from utils")
        text = text.replace(old, new, 1)
    data = text.encode()
    if sha256(data) != verify_candidate.PATCHED_SHA:
        raise AssertionError("patched utils hash drifted")
    return data


def overlay_files():
    payloads = wheel_map(NEW_WHEEL)
    files = {verify_candidate.UTILS: patched_utils()}
    for rel in install_certifi.OVERLAY_RELS:
        files[verify_candidate.SITE + "/" + rel] = payloads[rel]
    return files


def layer_archive(files, whiteouts=None, extra=None, file_mode=0o644, owner=0):
    if whiteouts is None:
        whiteouts = verify_candidate.WHITEOUTS
    layer_buf = io.BytesIO()
    dirs = set()
    for name in list(files) + list(whiteouts):
        parts = name.split("/")
        for index in range(1, len(parts)):
            dirs.add("/".join(parts[:index]))
    with tarfile.open(fileobj=layer_buf, mode="w") as layer:
        for name in sorted(dirs):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            info.uid = owner
            info.gid = owner
            layer.addfile(info)
        for name, payload in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = file_mode
            info.uid = owner
            info.gid = owner
            layer.addfile(info, io.BytesIO(payload))
        for name in sorted(whiteouts):
            info = tarfile.TarInfo(name)
            info.size = 0
            info.mode = 0
            info.uid = owner
            info.gid = owner
            layer.addfile(info, io.BytesIO(b""))
        if extra:
            kind, name, payload = extra
            info = tarfile.TarInfo(name)
            info.uid = owner
            info.gid = owner
            if kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = payload
            else:
                info.size = len(payload)
                info.mode = 0o644
            layer.addfile(info, None if kind == "symlink" else io.BytesIO(payload))
    config = b'{"created":"pinned"}'
    outer = io.BytesIO()
    with tarfile.open(fileobj=outer, mode="w") as image:
        raw = layer_buf.getvalue()
        layer_info = tarfile.TarInfo("layer/layer.tar")
        layer_info.size = len(raw)
        image.addfile(layer_info, io.BytesIO(raw))
        config_info = tarfile.TarInfo("config.json")
        config_info.size = len(config)
        image.addfile(config_info, io.BytesIO(config))
        manifest = json.dumps([{"Config": "config.json", "Layers": ["layer/layer.tar"]}]).encode()
        manifest_info = tarfile.TarInfo("manifest.json")
        manifest_info.size = len(manifest)
        image.addfile(manifest_info, io.BytesIO(manifest))
    return outer.getvalue()


def seed_site(site, wheel_path):
    payloads = wheel_map(wheel_path)
    for name, payload in payloads.items():
        dest = site / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
    cache = site / "certifi" / "__pycache__"
    cache.mkdir()
    (cache / "__init__.cpython-314.pyc").write_bytes(b"stale")
    (site / "certifi-2026.10.19.dist-info" / "INSTALLER").write_text("uv\n")


class CertifiOverlayTests(unittest.TestCase):
    def setUp(self):
        self._owned = []

    def tearDown(self):
        for path in self._owned:
            if path.is_dir():
                for child in sorted(path.rglob("*"), reverse=True):
                    if child.is_file() or child.is_symlink():
                        child.unlink()
                    elif child.is_dir():
                        child.rmdir()
                path.rmdir()
            elif path.exists():
                path.unlink()

    def _site(self):
        directory = pathlib.Path(tempfile.mkdtemp())
        self._owned.append(directory)
        return directory

    def _file(self):
        handle = tempfile.NamedTemporaryFile(delete=False)
        handle.close()
        path = pathlib.Path(handle.name)
        self._owned.append(path)
        return path

    def test_wheel_delta_is_only_version_metadata_and_bundle(self):
        self.assertEqual(sha256(NEW_WHEEL.read_bytes()), install_certifi.WHEEL_SHA)
        self.assertEqual(sha256(OLD_WHEEL.read_bytes()), OLD_WHEEL_SHA)
        old = {name: sha256(data) for name, data in wheel_map(OLD_WHEEL).items()}
        new = {name: sha256(data) for name, data in wheel_map(NEW_WHEEL).items()}
        expected = {name for name, digest in new.items() if old.get(name) != digest}
        self.assertEqual(set(install_certifi.OVERLAY_RELS), expected)
        self.assertNotIn("certifi/core.py", expected)
        roots = install_certifi.assert_bundle_delta(
            wheel_map(OLD_WHEEL)["certifi/cacert.pem"],
            wheel_map(NEW_WHEEL)["certifi/cacert.pem"],
        )
        self.assertEqual(roots["removedFingerprints"], sorted(install_certifi.REMOVED_FINGERPRINTS))
        self.assertEqual(roots["addedFingerprints"], [])

    def test_tampered_bundle_and_extra_root_are_rejected(self):
        import re
        old = wheel_map(OLD_WHEEL)["certifi/cacert.pem"]
        new = bytearray(wheel_map(NEW_WHEEL)["certifi/cacert.pem"])
        new[80] ^= 0x01
        with self.assertRaises(SystemExit):
            install_certifi.assert_bundle_delta(old, bytes(new))
        extra = base64.b64encode(b"extra-root-der-not-in-the-bundle")
        extra_pem = b"-----BEGIN CERTIFICATE-----\n" + extra + b"\n-----END CERTIFICATE-----\n"
        with self.assertRaises(SystemExit):
            install_certifi.assert_root_set(old, wheel_map(NEW_WHEEL)["certifi/cacert.pem"] + extra_pem)
        c1 = "125609aa301da0a249b97a8239cb6a34216f44dcac9f3954b14292f2e8c8608f"
        block = None
        for match in install_certifi._CERT_RE.finditer(old):
            body = re.sub(br"\s+", b"", match.group(1))
            if hashlib.sha256(base64.b64decode(body)).hexdigest() == c1:
                block = old[match.start():match.end()] + b"\n"
                break
        self.assertIsNotNone(block)
        with self.assertRaises(SystemExit):
            install_certifi.assert_root_set(old, wheel_map(NEW_WHEEL)["certifi/cacert.pem"] + block)

    def test_metadata_version_must_agree_with_the_module(self):
        payloads = wheel_map(NEW_WHEEL)
        install_certifi.assert_distribution_versions(
            payloads["certifi-2026.10.23.dist-info/METADATA"],
            payloads["certifi/__init__.py"],
        )
        stale = payloads["certifi-2026.10.23.dist-info/METADATA"].replace(
            b"Version: 2026.10.23", b"Version: 2026.10.19", 1)
        with self.assertRaises(SystemExit):
            install_certifi.assert_distribution_versions(stale, payloads["certifi/__init__.py"])
        stale_module = payloads["certifi/__init__.py"].replace(b"2026.10.23", b"2026.10.19", 1)
        with self.assertRaises(SystemExit):
            install_certifi.assert_distribution_versions(
                payloads["certifi-2026.10.23.dist-info/METADATA"], stale_module)

    def test_runtime_probe_rejects_a_leftover_distribution(self):
        verify_candidate.assert_runtime_certifi({
            "importlibMetadataVersion": "2026.10.23",
            "moduleVersion": "2026.10.23",
            "distributionVersions": ["2026.10.23"],
            "where": "/ak-root/.venv/lib/python3.14/site-packages/certifi/cacert.pem",
        })
        with self.assertRaises(SystemExit):
            verify_candidate.assert_runtime_certifi({
                "importlibMetadataVersion": "2026.10.19",
                "moduleVersion": "2026.10.23",
                "distributionVersions": ["2026.10.23"],
                "where": "/ak-root/.venv/lib/python3.14/site-packages/certifi/cacert.pem",
            })
        with self.assertRaises(SystemExit):
            verify_candidate.assert_runtime_certifi({
                "importlibMetadataVersion": "2026.10.23",
                "moduleVersion": "2026.10.23",
                "distributionVersions": ["2026.10.19", "2026.10.23"],
                "where": "/ak-root/.venv/lib/python3.14/site-packages/certifi/cacert.pem",
            })

    def test_install_replaces_distribution_metadata(self):
        site = self._site()
        seed_site(site, OLD_WHEEL)
        install_certifi.install(site, NEW_WHEEL, require_root=False)
        dists = [
            dist.version for dist in importlib.metadata.distributions(path=[str(site)])
            if (dist.metadata.get("Name") or "").lower() == "certifi"
        ]
        self.assertEqual(dists, ["2026.10.23"])
        self.assertFalse((site / "certifi-2026.10.19.dist-info").exists())
        self.assertFalse((site / "certifi" / "__pycache__").exists())
        self.assertFalse((site / "certifi-2026.10.23.dist-info" / "INSTALLER").exists())
        self.assertEqual(sha256((site / "certifi" / "core.py").read_bytes()), install_certifi.WHEEL_FILES["certifi/core.py"])
        self.assertEqual((site / "certifi" / "cacert.pem").stat().st_mode & 0o777, 0o644)
        altered = self._site()
        seed_site(altered, OLD_WHEEL)
        pem = altered / "certifi" / "cacert.pem"
        blob = bytearray(pem.read_bytes())
        blob[-2] ^= 0x01
        pem.write_bytes(blob)
        with self.assertRaises(SystemExit):
            install_certifi.install(altered, NEW_WHEEL, require_root=False)

    def test_altered_redaction_is_rejected(self):
        original = (FIXTURES / "utils-2026.8.5.py").read_bytes()
        patched = patched_utils()
        report = verify_candidate.utils_delta(original, patched, PROPOSAL.read_text())
        self.assertTrue(report["utilsDeltaMatch"])
        broken = patched.replace(b'LOGGER.debug("Token does not exist")', b'LOGGER.debug("Token does not exist", access_token=access_token)', 1)
        with self.assertRaises(SystemExit):
            verify_candidate.utils_delta(original, broken, PROPOSAL.read_text())

    def test_layer_accepts_exact_overlay_and_rejects_mode_symlink_and_metadata(self):
        path = self._file()
        blob = layer_archive(overlay_files())
        path.write_bytes(blob)
        report = verify_candidate.layer_members(path)
        self.assertEqual(report["layerWhiteouts"], sorted(verify_candidate.WHITEOUTS))
        self.assertIn(verify_candidate.UTILS, report["layerPaths"])
        self.assertIn(verify_candidate.SITE + "/certifi/cacert.pem", report["layerPaths"])
        path.write_bytes(layer_archive(overlay_files(), file_mode=0o755))
        with self.assertRaises(SystemExit):
            verify_candidate.layer_members(path)
        path.write_bytes(layer_archive(overlay_files(), extra=("symlink", verify_candidate.SITE + "/certifi/link", "cacert.pem")))
        with self.assertRaises(SystemExit):
            verify_candidate.layer_members(path)
        files = overlay_files()
        meta = verify_candidate.SITE + "/certifi-2026.10.23.dist-info/METADATA"
        files[meta] = files[meta].replace(b"Version: 2026.10.23", b"Version: 2026.10.19", 1)
        path.write_bytes(layer_archive(files))
        with self.assertRaises(SystemExit):
            verify_candidate.layer_members(path)
        path.write_bytes(layer_archive(overlay_files(), whiteouts=set(verify_candidate.WHITEOUTS) - {verify_candidate.SITE + "/certifi/.wh.__pycache__"}))
        with self.assertRaises(SystemExit):
            verify_candidate.layer_members(path)

    def test_expect_mismatch_writes_unaccepted_report_before_exit(self):
        report = {
            "archiveSha256": "a" * 64,
            "candidateConfigSha256": "b" * 64,
            "candidateImageId": "sha256:" + ("b" * 64),
            "buildxManifestDigest": "sha256:" + ("c" * 64),
            "qualified": True,
        }
        expect = self._file()
        expect.write_text(json.dumps({
            "archiveSha256": "d" * 64,
            "candidateConfigSha256": "b" * 64,
            "candidateImageId": "sha256:" + ("b" * 64),
            "buildxManifestDigest": "sha256:" + ("c" * 64),
        }))
        out = self._file()
        with self.assertRaises(SystemExit) as caught:
            verify_candidate.enforce_expect(report, expect, out)
        self.assertEqual(caught.exception.code, 2)
        written = json.loads(out.read_text())
        self.assertEqual(written["acceptance"], "UNACCEPTED")
        self.assertFalse(written["expectMatch"])
        self.assertEqual(written["expectMismatches"], ["archiveSha256"])
        self.assertFalse(written["signed"])
        matched = self._file()
        matched.write_text(json.dumps({
            "archiveSha256": "a" * 64,
            "candidateConfigSha256": "b" * 64,
            "candidateImageId": "sha256:" + ("b" * 64),
            "buildxManifestDigest": "sha256:" + ("c" * 64),
        }))
        out.write_text("")
        verify_candidate.enforce_expect(report, matched, out)
        self.assertEqual(json.loads(out.read_text())["acceptance"], "REVIEWED")

    def test_legacy_pins_record_the_accepted_image(self):
        legacy = json.loads((REDACTION / "accepted-legacy-digests.json").read_text())
        self.assertEqual(
            legacy["registryDigest"],
            "sha256:18fc99f13617a4b83dfc470e4f8cda7ed76cf513c5147c90e1de888838325663",
        )
        self.assertEqual(legacy["archiveSha256"], "fd766ce876e872605eb1b2dcc19fd62697393644c29cd6cebcf77c00493baa00")
        self.assertEqual(legacy["candidateConfigSha256"], "27208b5ce501d57daf558c44730ee02fe0f9a4e1f0bc5bb96fbdf5abf247c870")
        self.assertEqual(legacy["candidateImageId"], "sha256:" + legacy["candidateConfigSha256"])
        self.assertEqual(
            legacy["buildxManifestDigest"],
            "sha256:d39684927413cf724566352022711c6e8955c98cbe21ddfffeabd5366f18f452",
        )
        gate = json.loads((REDACTION / "reviewed-digests.json").read_text())
        self.assertEqual(set(gate), {"archiveSha256", "buildxManifestDigest", "candidateConfigSha256", "candidateImageId"})

    def test_dockerfile_keeps_redaction_networkless_wheel_mount(self):
        text = (REDACTION / "Dockerfile").read_text()
        self.assertIn("ghcr.io/goauthentik/server@sha256:602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5", text)
        self.assertIn('LOGGER.debug("Token does not exist", access_token=access_token)', text)
        self.assertIn('LOGGER.debug("Token has expired", access_token=access_token)', text)
        self.assertIn('LOGGER.warning("Revoked token was used", access_token=access_token)', text)
        self.assertIn("--network=none", text)
        self.assertIn("SOURCE_DATE_EPOCH=1792582296", text)
        self.assertEqual(text.count("\nFROM "), 1)
        self.assertEqual(text.count("\nRUN "), 1)
        self.assertTrue(text.endswith("USER 1000\n"))
        self.assertIn("certifi-2026.10.23-py3-none-any.whl", text)
        self.assertNotIn("pip install", text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
