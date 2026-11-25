"""Provisioning tests with synthetic bytes and an isolated local allocation."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request

PROVISION = Path("kubernetes/main/apps/ai/studio/matting-pending/policy/provision.py")
SCRIPT = runpy.run_path(str(PROVISION))
provision = SCRIPT["provision"]
constants = provision.__globals__
ProvisionError = SCRIPT["ProvisionError"]
ALLOCATION = "matting-op459-birefnet-lite-v1-01"
GRAPH = b"graph bytes for the provisioning test"
MANIFEST = b'{"profile_id":"fixture"}'
NOTICE = b"test notice"


class Response(BytesIO):
    status = 200

    def __init__(self, data, *, headers=None):
        super().__init__(data)
        self.headers = headers or {}


class Opener:
    def __init__(self, data=GRAPH, *, headers=None):
        self.data, self.headers, self.calls = data, headers, 0

    def open(self, request, *, timeout):
        assert request.full_url == SCRIPT["URL"]
        assert request.get_header("Accept-encoding") == "identity"
        assert timeout == SCRIPT["READ_SECONDS"]
        self.calls += 1
        return Response(self.data, headers=self.headers)


class ProvisioningTest(unittest.TestCase):
    def setUp(self):
        scratch = self.enterContext(TemporaryDirectory(prefix="matting-test-"))
        self.root = Path(scratch)
        self.run_path = self.root / ALLOCATION
        self.enterContext(patch.dict(constants, GRAPH_BYTES=len(GRAPH), GRAPH_SHA256=sha256(GRAPH).hexdigest()))
        self.free = SimpleNamespace(f_bavail=100 * 1024**2, f_frsize=1024)
        self.statvfs = self.enterContext(patch("os.statvfs", return_value=self.free))

    def stage(self, opener):
        return provision(self.root, ALLOCATION, opener, MANIFEST, NOTICE)

    def test_verified_create_only_publication(self):
        proof = self.stage(Opener(headers={"Content-Length": str(len(GRAPH))}))
        self.assertEqual(proof, {
            "allocation": ALLOCATION, "graph_bytes": len(GRAPH), "graph_sha256": sha256(GRAPH).hexdigest(),
            "profile_sha256": sha256(MANIFEST).hexdigest(),
        })
        self.assertEqual(self.run_path.stat().st_mode & 0o777, 0o555)
        published = self.run_path / "weights"
        self.assertEqual((published / "model.onnx").read_bytes(), GRAPH)
        self.assertEqual((published / "profile.json").read_bytes(), MANIFEST)
        self.assertEqual((published / "BiRefNet-LICENSE").read_bytes(), NOTICE)
        self.assertEqual((published / "profile.sha256").read_text().strip(), proof["profile_sha256"])
        self.assertFalse((self.run_path / ".quarantine").exists())
        again = Opener()
        with self.assertRaisesRegex(ProvisionError, "allocation_already_exists"):
            self.stage(again)
        self.assertEqual(again.calls, 0)
        self.assertEqual((published / "model.onnx").read_bytes(), GRAPH)
        # TemporaryDirectory cleanup needs owner write access.
        self.run_path.chmod(0o700)
        published.chmod(0o700)

    def test_bad_downloads_stay_quarantined(self):
        for attempt, (data, headers, code) in enumerate((
            (GRAPH[:-1], {}, "unexpected_download_length"),
            (GRAPH + b"!", {}, "unexpected_download_length"),
            (b"!" + GRAPH[1:], {}, "download_hash_mismatch"),
            (GRAPH, {"Content-Length": "999"}, "unexpected_download_length"),
            (GRAPH, {"Content-Encoding": "gzip"}, "unexpected_download_length"),
        ), start=11):
            with self.subTest(code=code, attempt=attempt):
                allocation = ALLOCATION[:-2] + str(attempt)
                with self.assertRaisesRegex(ProvisionError, code):
                    provision(self.root, allocation, Opener(data, headers=headers), MANIFEST, NOTICE)
                run = self.root / allocation
                self.assertEqual(run.stat().st_mode & 0o777, 0o700)
                self.assertTrue((run / ".quarantine").is_dir())
                self.assertFalse((run / "weights").exists())

    def test_preflight_budget_and_reserve(self):
        opener = Opener()
        with patch.dict(constants, BUDGET=len(GRAPH)):
            with self.assertRaisesRegex(ProvisionError, "byte_budget_exceeded"):
                self.stage(opener)
        self.free.f_bavail = 32 * 1024**2
        with self.assertRaisesRegex(ProvisionError, "free_reserve_too_small"):
            self.stage(opener)
        self.assertEqual(opener.calls, 0)
        self.assertFalse(self.run_path.exists())

    def test_reserve_can_stop_a_write_in_progress(self):
        exhausted = SimpleNamespace(f_bavail=32 * 1024**2, f_frsize=1024)
        self.statvfs.side_effect = (self.free, self.free, exhausted)
        with self.assertRaisesRegex(ProvisionError, "free_reserve_too_small"):
            self.stage(Opener())
        self.assertTrue((self.run_path / ".quarantine/model.onnx").exists())
        self.assertFalse((self.run_path / "weights").exists())

    def test_redirects_allow_only_the_observed_https_cdn(self):
        request = Request(SCRIPT["URL"])
        redirect = SCRIPT["PinnedRedirect"]()
        target = "https://us.aws.cdn.hf.co/object"
        self.assertEqual(redirect.redirect_request(request, None, 302, "", {}, target).full_url, target)
        for other in ("http://us.aws.cdn.hf.co/object", "https://other.cdn.hf.co/object",
                      "https://us.aws.cdn.hf.co:444/object", "https://huggingface.co/another-file"):
            with self.subTest(url=other), self.assertRaisesRegex(ProvisionError, "unexpected_download_redirect"):
                redirect.redirect_request(request, None, 302, "", {}, other)


if __name__ == "__main__":
    unittest.main()
