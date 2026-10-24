#!/usr/bin/env python3
"""Host checks for the three-call proposal and canary comparison. No image."""
import copy
import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[4]
REDACTION = ROOT / "scripts" / "authentik-userinfo-redaction"
PROPOSAL = ROOT / ".agents" / "reports" / "portal178-m5c" / "pending" / "authentik-userinfo-log.patch"


def load(name):
    spec = importlib.util.spec_from_file_location(name, REDACTION / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compare_canary = load("compare_canary")
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

    def test_layer_archive_accepts_only_utils(self):
        import io
        import tarfile

        def archive_with(extra=None):
            layer_buf = io.BytesIO()
            with tarfile.open(fileobj=layer_buf, mode="w") as layer:
                for name in ("authentik", "authentik/providers", "authentik/providers/oauth2"):
                    info = tarfile.TarInfo(name)
                    info.type = tarfile.DIRTYPE
                    layer.addfile(info)
                payload = b"patched-utils"
                info = tarfile.TarInfo("authentik/providers/oauth2/utils.py")
                info.size = len(payload)
                layer.addfile(info, io.BytesIO(payload))
                if extra:
                    info = tarfile.TarInfo(extra)
                    info.size = 1
                    layer.addfile(info, io.BytesIO(b"x"))
            config = b'{"created":"pinned"}'
            outer = io.BytesIO()
            with tarfile.open(fileobj=outer, mode="w") as image:
                layer_info = tarfile.TarInfo("layer/layer.tar")
                layer_info.size = len(layer_buf.getvalue())
                image.addfile(layer_info, io.BytesIO(layer_buf.getvalue()))
                config_info = tarfile.TarInfo("config.json")
                config_info.size = len(config)
                image.addfile(config_info, io.BytesIO(config))
                manifest = json.dumps([{
                    "Config": "config.json",
                    "Layers": ["layer/layer.tar"],
                }]).encode()
                manifest_info = tarfile.TarInfo("manifest.json")
                manifest_info.size = len(manifest)
                image.addfile(manifest_info, io.BytesIO(manifest))
            return outer.getvalue(), hashlib_sha(config)

        def hashlib_sha(data):
            import hashlib
            return hashlib.sha256(data).hexdigest()

        good, config_sha = archive_with()
        path = pathlib.Path(self._tmp())
        path.write_bytes(good)
        report = verify_candidate.layer_members(path)
        self.assertEqual(report["layerPaths"], ["authentik/providers/oauth2/utils.py"])
        self.assertEqual(report["configSha256"], config_sha)
        bad, _ignored = archive_with("authentik/providers/oauth2/utils.pyc")
        path.write_bytes(bad)
        with self.assertRaises(SystemExit):
            verify_candidate.layer_members(path)


if __name__ == "__main__":
    unittest.main(verbosity=1)
