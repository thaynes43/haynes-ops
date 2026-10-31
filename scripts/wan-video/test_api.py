"""CPU-only protocol and failure tests; never import torch or submit GPU work."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "kubernetes/main/apps/ai/wan-video/app"
spec = importlib.util.spec_from_file_location("video_api", APP / "api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        api.ROOT = Path(self.tmp.name)
        api.CONFIG = APP
        api.ACTIVE = None
        api.UNSAFE = False

    def test_graph_keeps_both_gpus_and_guidance(self):
        graph = api.make_graph("test", "preview", 43, "a" * 32)
        self.assertEqual(graph["7"]["class_type"], "MultiGPU_WorkUnits")
        self.assertEqual(graph["7"]["inputs"]["max_gpus"], 2)
        self.assertGreater(graph["9"]["inputs"]["cfg"], 1)
        self.assertEqual(graph["9"]["inputs"]["model"], ["7", 0])
        self.assertEqual(graph["8"]["inputs"]["batch_size"], 1)

    def test_paths_cannot_escape_workspace(self):
        with self.assertRaises(ValueError):
            api.job_path("../something")
        with self.assertRaises(ValueError):
            api.output_file({"output": "../private.mp4"})
        with self.assertRaises(ValueError):
            api.output_file({"output": "/tmp/private.mp4"})

    def test_wrong_gpu_pair_is_rejected(self):
        result = type("Result", (), {"stdout": "GPU-other, 1, 0, 10, 40\n"})()
        with patch.object(api.subprocess, "run", return_value=result):
            with self.assertRaises(RuntimeError):
                api.gpu_sample()

    def test_model_downloads_are_revision_and_checksum_pinned(self):
        manifest = json.loads((APP / "models.yaml").read_text())
        self.assertEqual(len(manifest["models"]), 3)
        for model in manifest["models"]:
            self.assertRegex(model["sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(model["url"], r"/resolve/[0-9a-f]{40}/")

    def test_busy_backend_is_not_interrupted(self):
        job = {"id": "a" * 32, "telemetry": [], "preset": "preview", "seed": 43}
        sample = {"gpus": [{"utilization_percent": 0, "memory_mib": 0}]}
        with patch.object(api, "gpu_sample", return_value=sample), patch.object(api, "engine", return_value={"queue_running": [[1]]}) as engine:
            api.generate(job, "test")
        self.assertEqual(job["state"], "failed")
        self.assertEqual(engine.call_args_list, [unittest.mock.call("/queue")])
        self.assertIsNone(api.ACTIVE)

    def test_success_persists_mp4_and_telemetry(self):
        out = api.ROOT / "output/video/result.mp4"
        out.parent.mkdir(parents=True)
        out.write_bytes(b"validity checked separately by a real MP4 decoder")
        job = {"id": "a" * 32, "telemetry": [], "preset": "preview", "seed": 43}
        sample = {"gpus": [{"utilization_percent": 0, "memory_mib": 0}]}
        history = {"p1": {"status": {"status_str": "success"}, "outputs": {"12": {"images": [{"filename": "result.mp4", "subfolder": "video"}]}}}}
        with patch.object(api, "gpu_sample", return_value=sample), patch.object(api, "engine", side_effect=[{}, {"prompt_id": "p1"}, history]):
            api.generate(job, "test")
        saved = json.loads(api.job_path(job["id"]).read_text())
        self.assertEqual(saved["state"], "succeeded")
        self.assertEqual(saved["bytes"], out.stat().st_size)
        self.assertEqual(len(saved["telemetry"]), 1)

    def test_failed_inference_interrupts_only_own_job(self):
        job = {"id": "a" * 32, "telemetry": [], "preset": "preview", "seed": 43}
        sample = {"gpus": [{"utilization_percent": 0, "memory_mib": 0}]}
        replies = [{}, {"prompt_id": "p1"}, {"p1": {"status": {"status_str": "error"}}}, {"queue_running": [[1, "p1"]]}, {}, {"queue_running": []}]
        with patch.object(api, "gpu_sample", return_value=sample), patch.object(api, "engine", side_effect=replies) as engine:
            api.generate(job, "test")
        self.assertEqual(job["state"], "failed")
        self.assertIn(unittest.mock.call("/interrupt", {}), engine.call_args_list)
        self.assertFalse(api.UNSAFE)

    def test_http_rejects_busy_and_invalid_requests(self):
        server = api.ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:" + str(server.server_port)
        for data, busy, status in [({"prompt": "hi"}, "b" * 32, 409), ({"prompt": ""}, None, 400), ({"prompt": "hi", "preset": "unbounded"}, None, 400), ({"prompt": "hi", "seed": True}, None, 400)]:
            api.ACTIVE = busy
            with self.subTest(data=data):
                request = Request(base + "/videos", data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=2)
                self.assertEqual(error.exception.code, status)

    def test_pipeline_title_task_does_not_create_video(self):
        path = REPO / "kubernetes/main/apps/ai/open-webui/app/wan_video.py"
        spec = importlib.util.spec_from_file_location("wan_pipeline", path)
        pipeline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pipeline)
        with patch.object(pipeline, "request") as request:
            self.assertEqual(list(pipeline.Pipeline().pipe("title", "wan_video", [], {"task": "title_generation"})), ["Wan video"])
            request.assert_not_called()

    def test_restart_cancels_owned_job_without_replaying(self):
        job = {"id": "a" * 32, "state": "running", "prompt_id": "p1"}
        api.save(job)
        with patch.object(api, "engine", side_effect=[{"queue_running": [[1, "p1"]]}, {}, {"queue_running": []}]) as engine:
            api.recover()
        self.assertEqual(json.loads(api.job_path(job["id"]).read_text())["state"], "failed")
        self.assertNotIn("/prompt", [call.args[0] for call in engine.call_args_list])
        self.assertFalse(api.UNSAFE)

    def test_restart_preserves_unknown_work_and_fails_closed(self):
        with patch.object(api, "engine", return_value={"queue_running": [[1, "unknown"]]}) as engine:
            api.recover()
        self.assertTrue(api.UNSAFE)
        self.assertEqual(engine.call_count, 1)

    def test_restart_matches_client_when_submission_response_was_lost(self):
        job = {"id": "a" * 32, "state": "queued"}
        api.save(job)
        queue = {"queue_running": [[1, "p-lost", {}, {"client_id": job["id"]}]]}
        with patch.object(api, "engine", side_effect=[queue, {}, {"queue_running": []}]) as engine:
            api.recover()
        self.assertIn(unittest.mock.call("/interrupt", {}), engine.call_args_list)
        self.assertFalse(api.UNSAFE)

    def test_submission_timeout_does_not_orphan_owned_render(self):
        job = {"id": "a" * 32, "telemetry": [], "preset": "preview", "seed": 43}
        queue = {"queue_running": [[1, "p-lost", {}, {"client_id": job["id"]}]]}
        sample = {"gpus": [{"utilization_percent": 0, "memory_mib": 0}]}
        with patch.object(api, "gpu_sample", return_value=sample), patch.object(api, "engine", side_effect=[{}, TimeoutError("submission timeout"), queue, {}, {"queue_running": []}]) as engine:
            api.generate(job, "test")
        self.assertIn(unittest.mock.call("/interrupt", {}), engine.call_args_list)
        self.assertEqual(job["state"], "failed")
        self.assertFalse(api.UNSAFE)


if __name__ == "__main__":
    unittest.main()
