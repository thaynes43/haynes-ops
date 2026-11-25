"""Verify deployment stages with a stub registry and no downloaded model."""

import importlib.util
import json
from hashlib import sha1, sha256
from io import BytesIO
from pathlib import Path
import os
import runpy
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

STAGE = Path("kubernetes/main/apps/ai/studio/upscaling-pending")
SCRIPT = runpy.run_path(str(STAGE / "policy/provision.py"))
PROVISION = SCRIPT["provision"]
DIGEST = "sha256:" + "a" * 64
GRAPH = b"a small synthetic graph"
ALLOCATION = "upscaling-op461-fsrcnn-x2-v1-01"
NOTICE = b"Synthetic model licence notice"
COMPARE_ALLOCATION = "upscaling-op461-compare-000000000001"


class Response(BytesIO):
    status = 200
    headers = {}


class ProvisionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory(prefix="upscale-provision-")))
        constants = PROVISION.__globals__
        self.blob = sha1(b"blob " + str(len(GRAPH)).encode() + b"\0" + GRAPH, usedforsecurity=False).hexdigest()
        self.enterContext(patch.dict(
            constants, GRAPH_BYTES=len(GRAPH), GRAPH_SHA256=sha256(GRAPH).hexdigest(), GRAPH_BLOB=self.blob,
        ))
        self.free = SimpleNamespace(f_files=100_000, f_favail=100_000, f_bavail=128 * 1024**2, f_frsize=1024)
        self.enterContext(patch("os.statvfs", return_value=self.free))
        self.opener = SimpleNamespace(open=lambda *_args, **_kwargs: Response(GRAPH))

    def test_flushed_and_create_only(self):
        constants = PROVISION.__globals__
        real_settle = constants["settle"]
        flushes = []

        def settle(fd):
            flushes.append(fd)
            real_settle(fd)

        with patch.dict(constants, CHUNK=4, FLUSH_EVERY=8, settle=settle):
            proof = PROVISION(self.root, ALLOCATION, self.opener, b"{}", NOTICE)
        run = self.root / ALLOCATION
        weights = run / "weights"
        try:
            self.assertEqual(proof["graph_git_blob"], self.blob)
            self.assertEqual(proof["graph_sha256"], sha256(GRAPH).hexdigest())
            self.assertEqual(len(flushes), len(GRAPH) // 8 + 1)
            self.assertEqual((weights / "FSRCNN_x2.pb").read_bytes(), GRAPH)
            self.assertEqual((weights / "FSRCNN-NOTICE").read_bytes(), NOTICE)
            self.assertEqual((weights / "FSRCNN-NOTICE").stat().st_mode & 0o777, 0o444)
            self.assertEqual(proof["notice_sha256"], sha256(NOTICE).hexdigest())
            with self.assertRaisesRegex(SCRIPT["ProvisionError"], "allocation_already_exists"):
                PROVISION(self.root, ALLOCATION, self.opener, b"{}", NOTICE)
        finally:
            run.chmod(0o700)
            weights.chmod(0o700)

    def test_bad_git_blob_stays_quarantined(self):
        with patch.dict(PROVISION.__globals__, GRAPH_BLOB="0" * 40):
            with self.assertRaisesRegex(SCRIPT["ProvisionError"], "download_hash_mismatch"):
                PROVISION(self.root, ALLOCATION, self.opener, b"{}", NOTICE)
        self.assertFalse((self.root / ALLOCATION / "weights").exists())

    def test_inode_reserve_refuses_before_write(self):
        self.free.f_favail = 2047
        with self.assertRaisesRegex(SCRIPT["ProvisionError"], "free_inode_reserve_too_small"):
            PROVISION(self.root, ALLOCATION, self.opener, b"{}", NOTICE)
        self.assertFalse((self.root / ALLOCATION).exists())


class OutputReservationTest(unittest.TestCase):
    def test_reserves_only_a_new_group_writable_directory(self):
        script = runpy.run_path(str(STAGE / "compare/reserve.py"))
        main = script["main"]
        root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.dict(main.__globals__, JOBS=root))
        root.chmod(0o700)
        target = root / COMPARE_ALLOCATION
        with patch("sys.stdout"):
            self.assertEqual(main(["../other"]), 1)
            self.assertFalse(list(root.iterdir()))
            self.assertEqual(main([COMPARE_ALLOCATION]), 0)
            self.assertEqual(target.stat().st_mode & 0o7777, 0o2770)
            self.assertEqual(target.stat().st_gid, os.getgid())
            self.assertFalse(list(target.iterdir()))
            self.assertEqual(main([COMPARE_ALLOCATION]), 1)
            self.assertEqual(list(root.iterdir()), [target])

    def test_refuses_a_symlink_at_the_output_name(self):
        script = runpy.run_path(str(STAGE / "compare/reserve.py"))
        main = script["main"]
        root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.dict(main.__globals__, JOBS=root))
        existing = root / "existing"
        existing.mkdir(mode=0o700)
        original_mode = existing.stat().st_mode
        (root / COMPARE_ALLOCATION).symlink_to(existing, target_is_directory=True)
        with patch("sys.stdout"):
            self.assertEqual(main([COMPARE_ALLOCATION]), 1)
        self.assertEqual(existing.stat().st_mode, original_mode)


class ReleaseGuardTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("studio_release_check", Path(__file__).with_name("check.py"))
        self.guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.guard)
        self.studio = STAGE.parent
        self.flux = list(yaml.safe_load_all((self.studio / "ks.yaml").read_text()))

    def configured_image(self, image):
        real_load, real_render = yaml.safe_load, self.guard.render

        def pin(value):
            parsed = real_load(value)
            parsed["data"]["image"] = image
            return parsed

        def render(path):
            docs = real_render(path)
            for doc in docs:
                if doc["kind"] in {"Job", "Deployment"}:
                    pod = doc["spec"]["template"]["spec"]
                    for init in pod.get("initContainers", []):
                        if init["name"] == "reserve-output":
                            init["image"] = image
                    for c in pod["containers"]:
                        if c["name"] == "upscaling":
                            c["image"] = image
                            for entry in c["env"]:
                                if entry["name"] == "STUDIO_UPSCALING_IMAGE":
                                    entry["value"] = image
            return docs

        self.enterContext(patch.object(yaml, "safe_load", side_effect=pin))
        self.enterContext(patch.object(self.guard, "render", side_effect=render))

    def test_placeholder_is_refused_without_registry_lookup(self):
        self.configured_image(f"{self.guard.UPSCALING_IMAGE}@sha256:" + "0" * 64)
        with patch.object(self.guard, "published_labels") as fetch:
            with self.assertRaisesRegex(ValueError, "published Studio main digest"):
                self.guard.check_upscaling(self.studio, self.flux)
            fetch.assert_not_called()

    def test_comparison_job_refuses_unsafe_identities_and_mounts(self):
        pin = yaml.safe_load((STAGE / "release/release.yaml").read_text())["data"]
        policies = self.guard.render(STAGE / "policy")
        actually_render = self.guard.render

        def verify():
            self.guard.check_upscaling_jobs(
                STAGE, pin["image"], pin["allocation"], (pin["memoryRequest"], pin["memoryLimit"]), policies,
            )

        def rewritten_comparison(path, keys, value):
            docs = actually_render(path)
            if path == STAGE / "compare":
                parent = docs[0]["spec"]
                for key in keys[:-1]:
                    parent = parent[key]
                parent[keys[-1]] = value
            return docs

        verify()
        pod = ("template", "spec")
        main = (*pod, "containers", 0)
        init = (*pod, "initContainers", 0)
        mutations = (
            ("TTL", ("ttlSecondsAfterFinished",), 86400, "Job envelope"),
            ("label", ("template", "metadata", "labels"), {"app.kubernetes.io/name": "studio-upscaling-smoke"},
             "Job envelope"),
            ("main UID", (*pod, "securityContext", "runAsUser"), 1000, "Job identity"),
            ("main GID override", (*main, "securityContext", "runAsGroup"), 1000, "UID/GID 10001"),
            ("supplemental group", (*pod, "securityContext", "supplementalGroups"), [], "supplemental group"),
            ("init UID", (*init, "securityContext", "runAsUser"), 10001, "UID/GID 1000 init"),
            ("init GID", (*init, "securityContext", "runAsGroup"), 10001, "UID/GID 1000 init"),
            ("init command", (*init, "command"), ["studio-upscaling", "compare"], "UID/GID 1000 init"),
            ("writable weights mount", (*main, "volumeMounts", 3, "readOnly"), False, "mount paths"),
            ("writable input mount", (*main, "volumeMounts", 1, "readOnly"), False, "mount paths"),
            ("writable weights NFS", (*pod, "volumes", 1, "nfs", "readOnly"), False, "NFS export"),
            ("writable input NFS", (*pod, "volumes", 2, "nfs", "readOnly"), False, "NFS export"),
            ("whole jobs tree", (*main, "volumeMounts", 2, "subPath"), "data/studio/jobs", "mount paths"),
        )
        for name, keys, value, error in mutations:
            with self.subTest(name=name), patch.object(
                self.guard, "render", side_effect=lambda path: rewritten_comparison(path, keys, value),
            ):
                with self.assertRaisesRegex(ValueError, error):
                    verify()

    def test_measured_contract_and_cilium_labels(self):
        self.configured_image(f"{self.guard.UPSCALING_IMAGE}@{DIGEST}")
        published = {
            "org.opencontainers.image.revision": "1" * 40,
            "io.haynes-studio.upscaling.resources": json.dumps({
                "schema": "studio.upscaling-resources/v1",
                "measuring": {"request_gib": 2, "limit_gib": 8},
                "serving": None,
                "quality_peak_gib": 2,
            }),
        }
        with patch.object(self.guard, "published_labels", return_value=published) as fetch:
            self.guard.check_upscaling(self.studio, self.flux)
            fetch.assert_called_once_with(self.guard.UPSCALING_IMAGE, DIGEST)
            published["io.haynes-studio.upscaling.resources"] = "{}"
            with self.assertRaisesRegex(ValueError, "resource contract failed"):
                self.guard.check_upscaling(self.studio, self.flux)

        document = {
            "kind": "CiliumNetworkPolicy", "metadata": {"name": "test"},
            "spec": {"endpointSelector": {}, "egress": [
                {"toEndpoints": [{"matchLabels": {"k8s:statefulset.kubernetes.io/pod-name": "comfyui-0"}}]},
            ]},
        }
        with self.assertRaisesRegex(ValueError, "leaves out of pod identities"):
            self.guard.check_policy_selectors([document])


if __name__ == "__main__":
    unittest.main()
