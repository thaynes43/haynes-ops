"""CPU-only protocol and failure tests; never import torch or submit GPU work."""
import errno
import importlib.util
import json
from pathlib import Path
import socket
import struct
import sys
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


def malformed_queues():
    """Bad answers paired with owned work to catch partial validation/cancellation."""
    for answer in (None, [], "", 0, False, {}):
        yield repr(answer), answer
    for key in ("queue_running", "queue_pending"):
        queue = {"queue_running": [[1, "p1"]], "queue_pending": [[2, "p1"]]}
        yield key + " missing", {k: v for k, v in queue.items() if k != key}
        for label, value in (
            ("null", None), ("object", {}), ("string", ""), ("boolean", False), ("number", 0),
            ("object entry", [[1, "p1"], {1: "p1"}]), ("string entry", ["p1"]),
            ("null entry", [None]), ("empty entry", [[]]), ("short entry", [[1]]),
        ):
            yield key + " " + label, {**queue, key: value}


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        api.ROOT = Path(self.tmp.name)
        api.CONFIG = APP
        api.ACTIVE = None
        api.UNSAFE = False

    def test_restart_rejects_malformed_queue_without_cancelling(self):
        job = {"id": "a" * 32, "state": "running", "prompt_id": "p1"}
        empty = {"queue_running": [], "queue_pending": []}
        for label, queue in malformed_queues():
            with self.subTest(queue=label):
                api.UNSAFE = False
                api.save(job)
                with patch.object(api, "engine", side_effect=[queue, {}, empty]) as engine, \
                        patch.object(api.time, "sleep") as sleep:
                    api.recover()
                self.assertTrue(api.UNSAFE)
                self.assertEqual(engine.call_args_list, [unittest.mock.call("/queue")])
                sleep.assert_not_called()
                self.assertEqual(json.loads(api.job_path(job["id"]).read_text())["state"], "running")

    def test_submit_rejects_malformed_queue_without_submitting(self):
        sample = {"gpus": [{"utilization_percent": 0, "memory_mib": 0}]}
        for label, queue in malformed_queues():
            with self.subTest(queue=label):
                job = {"id": "a" * 32, "telemetry": [], "preset": "preview", "seed": 43}
                api.ACTIVE = job["id"]
                with patch.object(api, "gpu_sample", return_value=sample), \
                        patch.object(api, "engine", side_effect=[queue]) as engine:
                    api.generate(job, "test")
                self.assertEqual(job["state"], "failed")
                self.assertIn("Malformed ComfyUI /queue response; backend work is unknown", job["error"])
                self.assertEqual(engine.call_args_list, [unittest.mock.call("/queue")])
                self.assertIsNone(api.ACTIVE)

    def test_failure_rejects_malformed_queue_without_cancelling(self):
        sample = {"gpus": [{"utilization_percent": 0, "memory_mib": 0}]}
        empty = {"queue_running": [], "queue_pending": []}
        failure = {"p1": {"status": {"status_str": "error"}}}
        for label, queue in malformed_queues():
            with self.subTest(queue=label):
                api.UNSAFE = False
                job = {"id": "a" * 32, "telemetry": [], "preset": "preview", "seed": 43}
                replies = [empty, {"prompt_id": "p1"}, failure, queue, {}, empty]
                with patch.object(api, "gpu_sample", return_value=sample), \
                        patch.object(api, "engine", side_effect=replies) as engine, patch.object(api.time, "sleep"):
                    api.generate(job, "test")
                self.assertEqual(job["state"], "failed")
                self.assertTrue(api.UNSAFE)
                self.assertEqual([call.args[0] for call in engine.call_args_list],
                                 ["/queue", "/prompt", "/history/p1", "/queue"])
                self.assertEqual(engine.call_args_list[-1], unittest.mock.call("/queue"))

    def test_restart_interrupt_wait_rejects_malformed_queue(self):
        job = {"id": "a" * 32, "state": "running", "prompt_id": "p1"}
        owned = {"queue_running": [[1, "p1"]], "queue_pending": []}
        empty = {"queue_running": [], "queue_pending": []}
        for label, queue in malformed_queues():
            with self.subTest(queue=label):
                api.UNSAFE = False
                api.save(job)
                with patch.object(api, "engine", side_effect=[owned, {}, queue, empty]) as engine, \
                        patch.object(api.time, "sleep") as sleep:
                    api.recover()
                self.assertTrue(api.UNSAFE)
                self.assertEqual(engine.call_args_list, [unittest.mock.call("/queue"),
                                 unittest.mock.call("/interrupt", {}), unittest.mock.call("/queue")])
                sleep.assert_not_called()
                self.assertEqual(json.loads(api.job_path(job["id"]).read_text())["state"], "running")

    def test_failure_interrupt_wait_rejects_malformed_queue(self):
        sample = {"gpus": [{"utilization_percent": 0, "memory_mib": 0}]}
        empty = {"queue_running": [], "queue_pending": []}
        owned = {"queue_running": [[1, "p1"]], "queue_pending": []}
        failure = {"p1": {"status": {"status_str": "error"}}}
        for label, queue in malformed_queues():
            with self.subTest(queue=label):
                api.UNSAFE = False
                job = {"id": "a" * 32, "telemetry": [], "preset": "preview", "seed": 43}
                replies = [empty, {"prompt_id": "p1"}, failure, owned, {}, queue, empty]
                with patch.object(api, "gpu_sample", return_value=sample), \
                        patch.object(api, "engine", side_effect=replies) as engine, patch.object(api.time, "sleep") as sleep:
                    api.generate(job, "test")
                self.assertEqual(job["state"], "failed")
                self.assertTrue(api.UNSAFE)
                self.assertEqual([call.args[0] for call in engine.call_args_list],
                                 ["/queue", "/prompt", "/history/p1", "/queue", "/interrupt", "/queue"])
                sleep.assert_not_called()

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
        self.assertEqual(len(manifest["models"]), 7)
        for model in manifest["models"]:
            self.assertRegex(model["sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(model["url"], r"/resolve/[0-9a-f]{40}/")

    def test_busy_backend_is_not_interrupted(self):
        job = {"id": "a" * 32, "telemetry": [], "preset": "preview", "seed": 43}
        sample = {"gpus": [{"utilization_percent": 0, "memory_mib": 0}]}
        with patch.object(api, "gpu_sample", return_value=sample), patch.object(api, "engine", return_value={"queue_running": [[1, "other"]], "queue_pending": []}) as engine:
            api.generate(job, "test")
        self.assertEqual(job["state"], "failed")
        self.assertEqual(engine.call_args_list, [unittest.mock.call("/queue")])
        self.assertIsNone(api.ACTIVE)

    def test_external_audio_lease_blocks_video_without_touching_engine(self):
        lease = api.acquire_compute()
        self.assertIsNotNone(lease)
        job = {"id": "a" * 32, "telemetry": [], "preset": "preview", "seed": 43}
        api.ACTIVE = "b" * 32
        try:
            with patch.object(api, "engine") as engine:
                api.generate(job, "test")
            engine.assert_not_called()
            self.assertEqual(job["state"], "failed")
            self.assertEqual(api.ACTIVE, "b" * 32)
        finally:
            lease.close()
        next_lease = api.acquire_compute()
        self.assertIsNotNone(next_lease)
        next_lease.close()

    def test_success_persists_mp4_and_telemetry(self):
        out = api.ROOT / "output/video/result.mp4"
        out.parent.mkdir(parents=True)
        out.write_bytes(b"validity checked separately by a real MP4 decoder")
        job = {"id": "a" * 32, "telemetry": [], "preset": "preview", "seed": 43}
        sample = {"gpus": [{"utilization_percent": 0, "memory_mib": 0}]}
        history = {"p1": {"status": {"status_str": "success"}, "outputs": {"12": {"images": [{"filename": "result.mp4", "subfolder": "video"}]}}}}
        with patch.object(api, "gpu_sample", return_value=sample), patch.object(api, "engine", side_effect=[{"queue_running": [], "queue_pending": []}, {"prompt_id": "p1"}, history]):
            api.generate(job, "test")
        saved = json.loads(api.job_path(job["id"]).read_text())
        self.assertEqual(saved["state"], "succeeded")
        self.assertEqual(saved["bytes"], out.stat().st_size)
        self.assertEqual(len(saved["telemetry"]), 1)

    def test_slow_history_poll_does_not_kill_the_render(self):
        out = api.ROOT / "output/video/result.mp4"
        out.parent.mkdir(parents=True)
        out.write_bytes(b"validity checked separately by a real MP4 decoder")
        job = {"id": "a" * 32, "telemetry": [], "preset": "preview", "seed": 43}
        sample = {"gpus": [{"utilization_percent": 0, "memory_mib": 0}]}
        history = {"p1": {"status": {"status_str": "success"}, "outputs": {"12": {"images": [{"filename": "result.mp4", "subfolder": "video"}]}}}}
        replies = [{"queue_running": [], "queue_pending": []}, {"prompt_id": "p1"}, TimeoutError("timed out"), TimeoutError("timed out"), history]
        with patch.object(api, "gpu_sample", return_value=sample), patch.object(api, "engine", side_effect=replies) as engine, \
                patch.object(api.time, "sleep"):
            api.generate(job, "test")
        self.assertEqual(job["state"], "succeeded")
        self.assertEqual(job["poll_misses"], 2)
        self.assertNotIn(unittest.mock.call("/interrupt", {}), engine.call_args_list)

    def test_persistent_poll_failure_still_fails_the_job(self):
        job = {"id": "a" * 32, "telemetry": [], "preset": "preview", "seed": 43}
        sample = {"gpus": [{"utilization_percent": 0, "memory_mib": 0}]}
        replies = [{"queue_running": [], "queue_pending": []}, {"prompt_id": "p1"}] + [TimeoutError("timed out")] * 12 + [{"queue_running": [[1, "p1"]], "queue_pending": []}, {}, {"queue_running": [], "queue_pending": []}]
        with patch.object(api, "gpu_sample", return_value=sample), patch.object(api, "engine", side_effect=replies) as engine, \
                patch.object(api.time, "sleep"):
            api.generate(job, "test")
        self.assertEqual(job["state"], "failed")
        self.assertIn(unittest.mock.call("/interrupt", {}), engine.call_args_list)

    def test_slow_poll_window_bounds_the_retries(self):
        job = {"id": "a" * 32, "telemetry": [], "preset": "preview", "seed": 43}
        sample = {"gpus": [{"utilization_percent": 0, "memory_mib": 0}]}
        replies = [{"queue_running": [], "queue_pending": []}, {"prompt_id": "p1"}, TimeoutError("timed out"), TimeoutError("timed out"),
                   {"queue_running": [[1, "p1"]], "queue_pending": []}, {}, {"queue_running": [], "queue_pending": []}]
        clock = iter([0, 0, 10, 10, 200, 200, 200, 200, 200, 200])
        with patch.object(api, "gpu_sample", return_value=sample), patch.object(api, "engine", side_effect=replies), \
                patch.object(api.time, "sleep"), patch.object(api.time, "monotonic", side_effect=lambda: next(clock, 200)):
            api.generate(job, "test")
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["poll_misses"], 2)

    def test_failed_inference_interrupts_only_own_job(self):
        job = {"id": "a" * 32, "telemetry": [], "preset": "preview", "seed": 43}
        sample = {"gpus": [{"utilization_percent": 0, "memory_mib": 0}]}
        replies = [{"queue_running": [], "queue_pending": []}, {"prompt_id": "p1"}, {"p1": {"status": {"status_str": "error"}}}, {"queue_running": [[1, "p1"]], "queue_pending": []}, {}, {"queue_running": [], "queue_pending": []}]
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
        api.UNSAFE = True
        with urlopen(base + "/health", timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertFalse(json.load(response)["accepting_jobs"])
        with self.assertRaises(HTTPError) as error:
            urlopen(base + "/ready", timeout=2)
        self.assertEqual(error.exception.code, 503)

    def serve_file(self, data):
        """A running API with one finished job whose MP4 holds `data`."""
        out = api.ROOT / "output/video/clip.mp4"
        out.parent.mkdir(parents=True)
        out.write_bytes(data)
        api.save({"id": "c" * 32, "state": "succeeded", "output": "video/clip.mp4"})
        errors, closed = [], threading.Event()

        class Server(api.ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                errors.append(sys.exc_info()[1])

            def shutdown_request(self, request):
                super().shutdown_request(request)
                closed.set()

        server = Server(("127.0.0.1", 0), api.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_port, errors, closed

    def test_file_download_honours_byte_ranges(self):
        data = bytes(range(256)) * 10240  # 2.5 MiB, so a range crosses the 1 MiB read size
        size = len(data)
        port, errors, _ = self.serve_file(data)
        url = "http://127.0.0.1:" + str(port) + "/videos/" + "c" * 32 + "/file"
        with urlopen(url, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")
            self.assertIsNone(response.headers["Content-Range"])
            self.assertEqual(response.headers["Content-Length"], str(size))
            self.assertEqual(response.read(), data)
        for header, first, last in [("bytes=0-1", 0, 1), ("bytes=1000000-", 1000000, size - 1),
                                    ("bytes=1048570-2097160", 1048570, 2097160), ("bytes=-500", size - 500, size - 1),
                                    ("bytes=-99999999", 0, size - 1), ("bytes=100-99999999", 100, size - 1),
                                    (" bytes=7-7 ", 7, 7)]:
            with self.subTest(header=header), urlopen(Request(url, headers={"Range": header}), timeout=5) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.headers["Content-Range"], "bytes %d-%d/%d" % (first, last, size))
                self.assertEqual(response.headers["Content-Length"], str(last - first + 1))
                self.assertEqual(response.headers["Content-Type"], "video/mp4")
                self.assertEqual(response.read(), data[first:last + 1])
        # RFC 9110: a header the server does not understand is ignored, and the whole file is sent.
        for header in ["bytes=5-2", "bytes=0-1,4-5", "items=0-1", "bytes=-", "bytes=a-b", "", "bytes=" + "9" * 5000 + "-",
                       "bytes=0-" + "9" * 5000, "bytes=-" + "9" * 5000]:
            with self.subTest(header=header[:20]), urlopen(Request(url, headers={"Range": header}), timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), data)
        # If-Range: no validator is ever sent, so none can match and the range is not honoured.
        with urlopen(Request(url, headers={"Range": "bytes=0-1", "If-Range": '"stale"'}), timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), data)
        for header in ["bytes=%d-" % size, "bytes=99999999-", "bytes=-0"]:
            with self.subTest(header=header), self.assertRaises(HTTPError) as error:
                urlopen(Request(url, headers={"Range": header}), timeout=5)
            self.assertEqual(error.exception.code, 416)
            self.assertEqual(error.exception.headers["Content-Range"], "bytes */%d" % size)
        self.assertEqual(errors, [])

    def test_range_of_an_empty_file(self):
        self.assertIsNone(api.byte_range(None, 0))
        self.assertIsNone(api.byte_range("bytes=-", 0))
        for header in ["bytes=0-", "bytes=0-0", "bytes=-1"]:
            with self.subTest(header=header), self.assertRaises(LookupError):
                api.byte_range(header, 0)

    def test_player_that_hangs_up_mid_file_is_not_answered_twice(self):
        port, errors, closed = self.serve_file(bytes(16 * 1024 * 1024))
        player = socket.socket()
        self.addCleanup(player.close)
        player.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)  # the file cannot fit in the buffers
        player.settimeout(5)
        player.connect(("127.0.0.1", port))
        player.sendall(("GET /videos/" + "c" * 32 + "/file HTTP/1.1\r\nHost: x\r\n\r\n").encode())
        self.assertTrue(player.recv(4096).startswith(b"HTTP/1.0 200"))
        # Close with unread data and no linger: the server's next write fails, as it does
        # when a player seeks or stops.
        player.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        player.close()
        self.assertTrue(closed.wait(20))
        self.assertEqual(errors, [])
        with urlopen("http://127.0.0.1:" + str(port) + "/health", timeout=5) as response:
            self.assertEqual(response.status, 200)

    def test_fault_after_the_answer_started_is_not_answered_twice(self):
        port, errors, closed = self.serve_file(b"0123456789" * 100)
        real = api.output_file

        class Failing:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.handle.close()

            def fileno(self):
                return self.handle.fileno()

            def seek(self, position):
                return self.handle.seek(position)

            def read(self, count):
                raise OSError(errno.EIO, "Input/output error")

        class Source:
            def __init__(self, path):
                self.path = path

            def open(self, mode):
                return Failing(self.path.open(mode))

        with patch.object(api, "output_file", side_effect=lambda job: Source(real(job))):
            player = socket.create_connection(("127.0.0.1", port), timeout=5)
            self.addCleanup(player.close)
            player.sendall(("GET /videos/" + "c" * 32 + "/file HTTP/1.1\r\nHost: x\r\nRange: bytes=0-99\r\n\r\n").encode())
            answer = b""
            while chunk := player.recv(65536):
                answer += chunk
            self.assertTrue(closed.wait(5))
        head, _, body = answer.partition(b"\r\n\r\n")
        self.assertTrue(head.startswith(b"HTTP/1.0 206"))
        self.assertEqual(body, b"")  # a short stream, and no 503 written into it
        self.assertEqual([type(error) for error in errors], [OSError])  # the fault is logged, not hidden

    def test_missing_file_is_a_clean_404(self):
        port, errors, _ = self.serve_file(b"0123456789")
        (api.ROOT / "output/video/clip.mp4").unlink()
        with self.assertRaises(HTTPError) as error:
            urlopen("http://127.0.0.1:" + str(port) + "/videos/" + "c" * 32 + "/file", timeout=5)
        self.assertEqual(error.exception.code, 404)
        self.assertEqual(json.load(error.exception), {"error": "Not found"})
        self.assertEqual(errors, [])

    def test_pipeline_title_task_does_not_create_video(self):
        path = REPO / "kubernetes/main/apps/ai/open-webui/app/wan_video.py"
        spec = importlib.util.spec_from_file_location("wan_pipeline", path)
        pipeline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pipeline)
        with patch.object(pipeline, "request") as request:
            self.assertEqual(list(pipeline.Pipeline().pipe("title", "wan_video", [], {"task": "title_generation"})), ["Wan video"])
            request.assert_not_called()
        with patch.object(pipeline, "request", side_effect=[{"id": "a" * 32}, {"state": "failed"}]) as request:
            result = list(pipeline.Pipeline().pipe("test clip", "wan_video", [], {"metadata": None}))
            self.assertEqual(request.call_count, 2)
            self.assertIn("failed", result[-1])

    def test_restart_cancels_owned_job_without_replaying(self):
        job = {"id": "a" * 32, "state": "running", "prompt_id": "p1"}
        api.save(job)
        with patch.object(api, "engine", side_effect=[{"queue_running": [[1, "p1"]], "queue_pending": []}, {}, {"queue_running": [], "queue_pending": []}]) as engine:
            api.recover()
        self.assertEqual(json.loads(api.job_path(job["id"]).read_text())["state"], "failed")
        self.assertNotIn("/prompt", [call.args[0] for call in engine.call_args_list])
        self.assertFalse(api.UNSAFE)

    def test_restart_preserves_unknown_work_and_fails_closed(self):
        with patch.object(api, "engine", return_value={"queue_running": [[1, "unknown"]], "queue_pending": []}) as engine:
            api.recover()
        self.assertTrue(api.UNSAFE)
        self.assertEqual(engine.call_count, 1)

    def test_restart_matches_client_when_submission_response_was_lost(self):
        job = {"id": "a" * 32, "state": "queued"}
        api.save(job)
        queue = {"queue_running": [[1, "p-lost", {}, {"client_id": job["id"]}]], "queue_pending": []}
        with patch.object(api, "engine", side_effect=[queue, {}, {"queue_running": [], "queue_pending": []}]) as engine:
            api.recover()
        self.assertIn(unittest.mock.call("/interrupt", {}), engine.call_args_list)
        self.assertFalse(api.UNSAFE)

    def test_submission_timeout_does_not_orphan_owned_render(self):
        job = {"id": "a" * 32, "telemetry": [], "preset": "preview", "seed": 43}
        queue = {"queue_running": [[1, "p-lost", {}, {"client_id": job["id"]}]], "queue_pending": []}
        sample = {"gpus": [{"utilization_percent": 0, "memory_mib": 0}]}
        with patch.object(api, "gpu_sample", return_value=sample), patch.object(api, "engine", side_effect=[{"queue_running": [], "queue_pending": []}, TimeoutError("submission timeout"), queue, {}, {"queue_running": [], "queue_pending": []}]) as engine:
            api.generate(job, "test")
        self.assertIn(unittest.mock.call("/interrupt", {}), engine.call_args_list)
        self.assertEqual(job["state"], "failed")
        self.assertFalse(api.UNSAFE)


if __name__ == "__main__":
    unittest.main()
