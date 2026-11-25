"""Exercise the embedded trial with Studio's offline synthetic service."""

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from haynes_studio.matting.profile import GRAPH_NAME, RESPONSE_INVALID, MatteError
from tests.matting.test_backend import GRAPH, PROFILE, load_fake

sys.path.insert(0, str(Path("kubernetes/main/apps/ai/studio/matting-pending/quality").resolve()))
import reserve
import trial


class TrialTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="matting-trial-")))
        self.input = self.root / "input"
        weights = self.root / "weights"
        scratch = self.root / "scratch"
        jobs = self.root / "jobs"
        for directory in (self.input, weights, scratch, jobs):
            directory.mkdir()
        self.name = "studio-matting-quality-0123456789ab"
        self.output = jobs / self.name
        self.enterContext(patch.object(reserve, "JOBS", jobs))
        self.enterContext(patch.multiple(trial, INPUT=self.input, OUTPUT=self.output,
                                         WEIGHTS=weights, SCRATCH=scratch))
        self.enterContext(patch.object(trial.Profile, "installed", return_value=PROFILE))
        engine = trial.Engine
        self.enterContext(patch.object(trial, "Engine", side_effect=lambda *args, **kwargs: (
            engine(*args, factory=load_fake, **kwargs))))
        manifest = PROFILE.manifest()
        self.pin = sha256(manifest).hexdigest()
        (weights / "profile.json").write_bytes(manifest)
        (weights / "profile.sha256").write_text(self.pin + "\n", encoding="utf-8")
        (weights / GRAPH_NAME).write_bytes(GRAPH)
        self.assertEqual(reserve.main([self.name]), 0)

    def fixture(self, value=33):
        buffer = io.BytesIO()
        Image.new("RGB", (256, 256), (value, 12, 7)).save(buffer, format="PNG")
        payload = buffer.getvalue()
        digest = sha256(payload).hexdigest()
        path = self.input / (digest + ".png")
        path.write_bytes(payload)
        return path, [path.name, digest, "asv_" + "0" * 26, "-", "job_" + "1" * 26, "200", self.pin]

    def run_trial(self, arguments):
        output = io.StringIO()
        with patch.object(sys, "argv", ["trial.py", *arguments]), redirect_stdout(output):
            result = trial.main()
        line, = output.getvalue().splitlines()
        self.assertNotIn(str(self.root), line)
        self.assertNotIn(arguments[0], line)
        return result, json.loads(line)

    def assert_failed(self, arguments, code, *, service_code=None):
        result, report = self.run_trial(arguments)
        self.assertEqual(result, 1)
        self.assertFalse(report["passed"])
        self.assertEqual(report["code"], code)
        self.assertEqual(report.get("service_code"), service_code)
        self.assertFalse((self.output / "report.json").exists())

    def test_dry_run_verifies_and_publishes_a_synthetic_matte(self):
        _, arguments = self.fixture()
        result, report = self.run_trial(arguments)
        self.assertEqual(result, 0, report)
        self.assertTrue(report["passed"])
        self.assertEqual(report["source_sha256"], arguments[1])
        self.assertNotIn("source_path_sha256", report)
        self.assertEqual(report["shape"]["scale"], {"numerator": 200, "denominator": 128})
        self.assertGreater(report["worker_memory"]["requests"][0]["peak_rss_bytes"], 0)
        self.assertEqual({path.name for path in self.output.iterdir()},
                         {"cutout.png", "mask.png", "report.json"})
        for kind in ("cutout", "mask"):
            data = (self.output / (kind + ".png")).read_bytes()
            self.assertEqual(sha256(data).hexdigest(), report["artifacts"][kind]["sha256"])
        persisted = json.loads((self.output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted.pop("receipt")["source"]["sha256"], arguments[1])
        self.assertEqual(persisted, report)

    def test_reservation_refuses_existing_output_without_changing_it(self):
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o2770)
        existing = self.output / "mask.png"
        existing.write_bytes(b"original")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(reserve.main([self.name]), 1)
        self.assertEqual(json.loads(output.getvalue()), {"passed": False, "code": "trial.output_exists"})
        _, arguments = self.fixture()
        self.assert_failed(arguments, "trial.output_exists")
        self.assertEqual(existing.read_bytes(), b"original")

    def test_rejects_symlink_multiple_links_nonregular_file_and_invalid_image(self):
        path, arguments = self.fixture()
        other = self.input / "other"
        path.rename(other)
        path.symlink_to(other)
        self.assert_failed(arguments, "trial.input_invalid")
        path.unlink()
        os.link(other, path)
        self.assert_failed(arguments, "trial.input_invalid")
        path.unlink()
        path.mkdir()
        self.assert_failed(arguments, "trial.input_invalid")
        invalid = b"not an image"
        digest = sha256(invalid).hexdigest()
        (self.input / (digest + ".png")).write_bytes(invalid)
        self.assert_failed([digest + ".png", digest, *arguments[2:]], "trial.input_invalid")

    def test_rejects_hash_mismatch(self):
        path, arguments = self.fixture()
        path.write_bytes(b"changed")
        self.assert_failed(arguments, "trial.input_hash_mismatch")

    def test_reports_service_not_ready(self):
        _, arguments = self.fixture()
        arguments[-1] = "f" * 64
        self.assert_failed(arguments, "trial.service_not_ready")

    def test_reports_a_service_refusal_with_its_stable_code(self):
        _, arguments = self.fixture(value=55)
        self.assert_failed(arguments, "trial.service_refused", service_code="adapter.command_failed")

    def test_reports_verification_failure(self):
        _, arguments = self.fixture()
        with patch.object(trial, "verify_result", side_effect=MatteError(RESPONSE_INVALID)):
            self.assert_failed(arguments, "trial.verification_failed")

    def test_reports_timeout(self):
        _, arguments = self.fixture()
        local_call = trial.local_call

        def timeout_post(socket, method, *args):
            if method == "POST":
                raise TimeoutError(str(self.input))
            return local_call(socket, method, *args)

        with patch.object(trial, "local_call", side_effect=timeout_post):
            self.assert_failed(arguments, "trial.timeout")


if __name__ == "__main__":
    unittest.main(verbosity=2)
