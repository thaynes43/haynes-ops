"""Bounded launcher regressions; no Kometa/Plex/provider calls."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


LAUNCHER = Path(__file__).resolve().parents[1] / "kubernetes/main/apps/media/kometa/app/config/serial-run.py"
spec = importlib.util.spec_from_file_location("kometa_launcher", LAUNCHER)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def test_dataset_shared_and_missing_ids_do_not_request_service(self):
        source = ("class IMDb:\n" + launcher.UPSTREAM_GET_RATING).encode()
        with mock.patch.object(launcher, "APPROVED_IMDB_SHA256", hashlib.sha256(source).hexdigest()):
            namespace = {}
            exec(launcher.patched_imdb_source(source), namespace)
        imdb = namespace["IMDb"]()
        dataset_loads = []

        def ratings(self):
            if not hasattr(self, "_ratings"):
                dataset_loads.append(True)
                self._ratings = {"movie": "8.1", "show": "7.3"}
            return self._ratings

        namespace["IMDb"].ratings = property(ratings)
        imdb._service_title = mock.Mock(side_effect=AssertionError("per-title service call"))
        self.assertIsNone(imdb.get_rating(None))
        self.assertEqual(dataset_loads, [])
        self.assertEqual(imdb.get_rating("movie"), "8.1")
        self.assertEqual(imdb.get_rating("show"), "7.3")
        self.assertIsNone(imdb.get_rating("unknown"))
        self.assertEqual(len(dataset_loads), 1)
        imdb._service_title.assert_not_called()

    def test_unsupported_source_and_anchor_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "Unsupported Kometa IMDb source"):
            launcher.patched_imdb_source(b"changed upstream source")
        source = b"class IMDb: pass\n"
        with mock.patch.object(launcher, "APPROVED_IMDB_SHA256", hashlib.sha256(source).hexdigest()):
            with self.assertRaisesRegex(RuntimeError, "anchor"):
                launcher.patched_imdb_source(source)

    def test_dataset_restricted_to_operations(self):
        with mock.patch.dict(os.environ, {"HNET_IMDB_RATINGS_SOURCE": "dataset"}):
            with self.assertRaisesRegex(RuntimeError, "restricted to operations"):
                launcher.main(["--run", "--overlays-only"])

    def exercise_lock(self, terminate):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stub = root / "kometa.py"
            stub.write_text("import json,sys\nprint('RUNNING ' + json.dumps(sys.argv[1:]), flush=True)\nsys.stdin.readline()\n")
            runner = root / "runner.py"
            runner.write_text(
                "import runpy\n"
                f"module = runpy.run_path({str(LAUNCHER)!r})\n"
                f"module['main'].__globals__['LOCK_PATH'] = module['Path']({str(root / 'run.lock')!r})\n"
                f"module['main'].__globals__['KOMETA_PATH'] = module['Path']({str(stub)!r})\n"
                "module['main']()\n"
            )
            command = [sys.executable, str(runner), "--run", "--operations-only", "--timings"]
            env = {**os.environ, "HNET_IMDB_RATINGS_SOURCE": "upstream"}
            processes = []

            def line(process):
                ready, _, _ = select.select([process.stdout], [], [], 3)
                self.assertTrue(ready, "launcher failed to make bounded progress")
                return process.stdout.readline().decode().strip()

            try:
                first = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, bufsize=0)
                processes.append(first)
                self.assertIn("Waiting", line(first))
                self.assertIn("Acquired", line(first))
                self.assertEqual(json.loads(line(first).removeprefix("RUNNING ")), ["--run", "--operations-only", "--timings"])
                second = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, bufsize=0)
                processes.append(second)
                self.assertIn("Waiting", line(second))
                self.assertEqual(select.select([second.stdout], [], [], 0.1)[0], [], "second process entered before lock holder exited")
                if terminate:
                    first.terminate()
                else:
                    first.stdin.write(b"exit\n")
                    first.stdin.flush()
                first.wait(timeout=3)
                self.assertIn("Acquired", line(second))
                self.assertIn("RUNNING", line(second))
                second.stdin.write(b"exit\n")
                second.stdin.flush()
                self.assertEqual(second.wait(timeout=3), 0)
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=3)
                    for stream in (process.stdin, process.stdout, process.stderr):
                        stream.close()

    def test_lock_excludes_and_releases_on_exit(self):
        self.exercise_lock(terminate=False)

    def test_lock_releases_on_termination(self):
        self.exercise_lock(terminate=True)


if __name__ == "__main__":
    unittest.main()
