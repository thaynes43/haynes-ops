"""Product HTTP, queue and filesystem behavior with CPU-only fake engines."""
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, call as engine_call, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid
import wave
import zlib

from test_api import APP, REPO, api
import product

sys.path.insert(0, str(REPO / "scripts/controlfoley"))
import product_foley


def png_chunk(kind, data=b""):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


EMPTY = {"queue_running": [], "queue_pending": []}
IHDR = product.PNG + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
PIXEL = zlib.compress(b"\0\0\0\0")
PNG = IHDR + png_chunk(b"IDAT", PIXEL) + png_chunk(b"IEND")
VIDEO = b"synthetic MP4 contents"
STORY = "https://work.haynesnetwork.com/work_packages/458"
JOB = "01" * 16
PROMPT_ID = "ad7c6943-7d8a-45b6-b0b6-5dd6fbc529a7"
FRAMES = 120


def wav(frames, rate=24000, channels=1, width=2):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setparams((channels, width, rate, frames, "NONE", "not compressed"))
        writer.writeframes(bytes(channels * width * frames))
    return buffer.getvalue()


class FakeClock:
    elapsed = 0

    def monotonic(self):
        return self.elapsed

    def advance(self, seconds):
        self.elapsed += seconds


@contextmanager
def server(handler):
    http = api.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=http.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}"
    finally:
        http.shutdown()
        thread.join()
        http.server_close()


def call(url, route, *, body=None, method=None, headers=None):
    if isinstance(body, dict):
        body = json.dumps(body).encode()
    request = Request(url + route, data=body, method=method, headers=headers or {})
    try:
        with urlopen(request, timeout=5) as response:
            status, headers, raw = response.status, response.headers, response.read()
    except HTTPError as response:
        with response:
            status, headers, raw = response.status, response.headers, response.read()
    return status, headers, json.loads(raw) if headers.get_content_type() == "application/json" else raw


class ProductTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.foley = product_foley.service
        for target, attributes in ((api, {"ROOT": self.root, "CONFIG": APP, "ACTIVE": None, "UNSAFE": False}),
                                   (self.foley, {"ROOT": self.root, "WEIGHTS": self.root / "weights", "ACTIVE": None})):
            patcher = patch.multiple(target, **attributes)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sample = {"gpus": [{"utilization_percent": 0, "memory_mib": 1, "temperature_c": 50}]}
        self.image_sha = hashlib.sha256(PNG).hexdigest()
        self.audio = wav(FRAMES * 1000)
        self.audio_sha = hashlib.sha256(self.audio).hexdigest()

    def i2v(self, **fields):
        return {"profile": product.PROFILES["i2v"]["profile"], "prompt": "Make a clip", "seed": 42, "story": STORY,
                "image_sha256": self.image_sha, **fields}

    def s2v(self, **fields):
        return {**self.i2v(profile=product.PROFILES["s2v"]["profile"]),
                "audio_sha256": self.audio_sha, "board_frames": FRAMES, **fields}

    def foley_request(self, **fields):
        return {"profile": product.PROFILES["foley"]["profile"], "prompt": "Soft fabric rustle", "seed": 42,
                "story": STORY, "source_job_id": "02" * 16, **fields}

    def upload(self, url, body, digest, suffix):
        return call(url, f"/v1/inputs/{digest}.{suffix}", method="PUT", body=body)

    def video_job(self):
        return {**product.validate("i2v", self.i2v()), "id": JOB, "type": "i2v", "api_version": product.VERSION,
                "state": "queued", "telemetry": []}

    def assert_engine_mutations(self, engine, *, deletes=(), interrupts=()):
        self.assertEqual([call.args[1]["delete"] for call in engine.call_args_list
                          if call.args[0] == "/queue" and len(call.args) > 1], list(deletes))
        self.assertEqual([call.args[1]["prompt_id"] for call in engine.call_args_list
                          if call.args[0] == "/interrupt"], list(interrupts))

    def test_discovery_and_openapi_agree_on_all_profile_ids(self):
        with server(api.Handler) as url:
            code, _, response = call(url, "/v1/profiles")
            self.assertEqual(code, 200)
            self.assertEqual(response, {"api_version": "1.0.0", "profiles": product.PROFILES})
            code, _, schema = call(url, "/openapi.json")
            self.assertEqual(code, 200)
            for name, kind in (("I2V", "i2v"), ("S2V", "s2v"), ("Foley", "foley")):
                spec = schema["components"]["schemas"][f"Product{name}Request"]
                self.assertEqual(spec["properties"]["profile"]["enum"], [response["profiles"][kind]["profile"]])
                self.assertFalse(spec["additionalProperties"])
                self.assertIn("put", schema["paths"][f"/v1/jobs/{kind}/{{job_id}}"])

    def test_upload_validates_hash_header_and_length_and_never_replaces(self):
        with server(api.Handler) as url:
            self.assertEqual(self.upload(url, PNG, "f" * 64, "png")[2]["code"], "input_hash_mismatch")
            self.assertEqual(self.upload(url, b"x", hashlib.sha256(b"x").hexdigest(), "png")[2]["code"],
                             "invalid_input")
            self.assertEqual(self.upload(url, b"", self.image_sha, "png")[0], 400)
            self.assertEqual(self.upload(url, PNG, self.image_sha, "png")[0], 201)
            self.assertEqual(self.upload(url, PNG, self.image_sha, "png")[0], 200)
            self.assertEqual(self.upload(url, self.audio, self.audio_sha, "wav")[0], 201)
            path = product.input_path(self.root, self.image_sha, "png")
            self.assertEqual(path.read_bytes(), PNG)
            self.assertEqual(len(list(path.parent.iterdir())), 2)
            path.write_bytes(b"modified outside API")
            code, _, error = self.upload(url, PNG, self.image_sha, "png")
            self.assertEqual((code, error["code"]), (409, "input_conflict"))
            self.assertEqual(path.read_bytes(), b"modified outside API")

    def test_upload_reserves_workspace_and_enforces_quota_without_blocking_replay(self):
        audio_path = product.input_path(self.root, self.audio_sha, "wav")
        with server(api.Handler) as url:
            self.assertEqual(self.upload(url, PNG, self.image_sha, "png")[0], 201)
            for limit in ("files", "bytes", "reserve"):
                with self.subTest(limit=limit):
                    if limit == "files":
                        cap = patch.object(product, "MAX_INPUT_FILES", 1)
                    elif limit == "bytes":
                        cap = patch.object(product, "MAX_INPUT_BYTES", len(PNG) + len(self.audio) - 1)
                    else:
                        cap = patch.object(product.shutil, "disk_usage", return_value=unittest.mock.Mock(
                            free=product.MIN_WORKSPACE_FREE))
                    with cap:
                        code, _, error = self.upload(url, self.audio, self.audio_sha, "wav")
                        self.assertEqual((code, error["code"]), (507, "input_storage_full"))
                        self.assertFalse(audio_path.exists())
                        self.assertEqual(self.upload(url, PNG, self.image_sha, "png")[0], 200)
            self.assertEqual(self.upload(url, self.audio, self.audio_sha, "wav")[0], 201)

    def test_upload_accepts_only_supported_wav_formats(self):
        pcm = wav(16)
        cases = {
            "1 Hz": wav(16, rate=1),
            "44,101 Hz": wav(16, rate=44101),
            "1,000,003 Hz": wav(16, rate=1000003),
            "64 channels": wav(16, channels=64),
            "24-bit": wav(16, width=3),
            "12-bit": pcm[:34] + struct.pack("<H", 12) + pcm[36:],
            "compressed": pcm[:20] + struct.pack("<H", 6) + pcm[22:],
        }
        with server(api.Handler) as url:
            for label, body in cases.items():
                with self.subTest(label=label):
                    digest = hashlib.sha256(body).hexdigest()
                    status, _, response = self.upload(url, body, digest, "wav")
                    self.assertEqual((status, response["code"]), (400, "unsupported_wav_format"))
                    self.assertFalse(product.input_path(self.root, digest, "wav").exists())
            for rate, channels in ((16000, 1), (22050, 2), (24000, 1), (44100, 2), (48000, 2)):
                with self.subTest(rate=rate, channels=channels):
                    body = wav(16, rate=rate, channels=channels)
                    self.assertEqual(self.upload(url, body, hashlib.sha256(body).hexdigest(), "wav")[0], 201)
            truncated = pcm[:-1]
            status, _, response = self.upload(url, truncated, hashlib.sha256(truncated).hexdigest(), "wav")
            self.assertEqual((status, response["code"]), (400, "invalid_input"))

    def test_upload_rejects_duplicate_wav_data_and_allows_metadata(self):
        pcm = wav(16)
        format_chunk, data_chunk = pcm[12:36], pcm[36:]
        list_chunk = b"LIST" + struct.pack("<I", 4) + b"INFO"
        fact_chunk = b"fact" + struct.pack("<II", 4, 16)

        def riff(chunks):
            return b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WAVE" + chunks

        duplicate = riff(format_chunk + data_chunk + list_chunk + wav(1024)[36:])
        with server(api.Handler) as url:
            digest = hashlib.sha256(duplicate).hexdigest()
            status, _, response = self.upload(url, duplicate, digest, "wav")
            self.assertEqual((status, response["code"]), (400, "invalid_input"))
            self.assertFalse(product.input_path(self.root, digest, "wav").exists())

            metadata = riff(format_chunk + list_chunk + data_chunk + fact_chunk) + b"trailing bytes"
            digest = hashlib.sha256(metadata).hexdigest()
            self.assertEqual(self.upload(url, metadata, digest, "wav")[0], 201)
            self.assertEqual(product.wav_info(product.input_path(self.root, digest, "wav")), (16, 24000))

            metadata = riff(format_chunk + fact_chunk + data_chunk + list_chunk)
            self.assertEqual(self.upload(url, metadata, hashlib.sha256(metadata).hexdigest(), "wav")[0], 201)

    def test_upload_refuses_animated_png_and_bounds_chunk_walk(self):
        text_chunk = png_chunk(b"tEXt", b"Comment\0acTL is payload here")
        animation_control = png_chunk(b"acTL", struct.pack(">II", 2, 0))
        end_chunk = png_chunk(b"IEND")
        through_image_data = IHDR + png_chunk(b"IDAT", PIXEL)
        animated = (
            IHDR + text_chunk + animation_control
            + png_chunk(b"fcTL", struct.pack(">IIIIIHHBB", 0, 1, 1, 0, 0, 1, 24, 0, 0))
            + png_chunk(b"IDAT", PIXEL)
            + png_chunk(b"fcTL", struct.pack(">IIIIIHHBB", 1, 1, 1, 0, 0, 1, 24, 0, 0))
            + png_chunk(b"fdAT", struct.pack(">I", 2) + PIXEL) + png_chunk(b"IEND")
        )
        with server(api.Handler) as url:
            for body, code in ((animated, "animated_png"), (IHDR, "invalid_input"),
                               (through_image_data + animation_control + end_chunk, "animated_png"),
                               (through_image_data, "invalid_input"),
                               (PNG + animation_control, "invalid_input"),
                               (IHDR + struct.pack(">I4s", 0xffffffff, b"tEXt"), "invalid_input"),
                               (through_image_data + text_chunk * 4095 + end_chunk, "invalid_input")):
                digest = hashlib.sha256(body).hexdigest()
                status, _, response = self.upload(url, body, digest, "png")
                self.assertEqual((status, response["code"]), (400, code))
                self.assertFalse(product.input_path(self.root, digest, "png").exists())
            static = through_image_data + text_chunk * 4094 + end_chunk
            self.assertEqual(self.upload(url, static, hashlib.sha256(static).hexdigest(), "png")[0], 201)

    def test_upload_deadline_cleans_up_slow_body_and_releases_slot(self):
        clock = FakeClock()
        handler = api.Handler.__new__(api.Handler)
        handler.path = f"/v1/inputs/{self.image_sha}.png"
        handler.headers = {"Content-Length": str(len(PNG))}
        handler.connection = Mock()
        handler.respond = Mock()
        handler.rfile = Mock()
        path = product.input_path(self.root, self.image_sha, "png")

        def trickle(count):
            self.assertLessEqual(count, len(PNG))
            self.assertEqual(len(list(path.parent.iterdir())), 1)
            clock.advance(12)
            return b"x"

        handler.rfile.read1.side_effect = trickle
        with patch.object(api.time, "monotonic", clock.monotonic):
            handler.do_PUT()
            handler.respond.assert_called_once_with(408, {"code": "upload_timeout", "error": "Input upload timed out"})
            self.assertTrue(handler.close_connection)
            self.assertEqual(handler.rfile.read1.call_count, 3)
            timeouts = [call.args[0] for call in handler.connection.settimeout.call_args_list]
            self.assertEqual(timeouts[:2], [15, 15])
            self.assertAlmostEqual(timeouts[2], 6 + len(PNG) / (128 * 1024))
            self.assertEqual(timeouts[3:], [15])
            self.assertEqual(list(path.parent.iterdir()), [])
            handler.rfile = io.BytesIO(PNG)
            self.assertEqual(handler.put_input(self.image_sha, "png")[0], 201)

    def test_rejects_wrong_profile_unknown_fields_invalid_inputs_and_graph_route(self):
        with server(api.Handler) as url, patch.object(api, "engine") as engine:
            for kind, body in (("i2v", self.i2v()), ("s2v", self.s2v()), ("foley", self.foley_request())):
                with self.subTest(kind=kind):
                    route = f"/v1/jobs/{kind}/{JOB}"
                    code, _, error = call(url, route, method="PUT", body={**body, "profile": "other"})
                    self.assertEqual((code, error["code"]), (400, "unsupported_profile"))
                    code, _, error = call(url, route, method="PUT", body={**body, "graph": {}})
                    self.assertEqual((code, error["code"]), (400, "invalid_request"))
            self.assertEqual(call(url, "/prompt", method="POST", body={})[0], 404)
            self.assertEqual(call(url, "/v1/graph", method="PUT", body={})[0], 404)
            route = f"/v1/jobs/s2v/{JOB}"
            self.assertEqual(call(url, route, method="PUT", body=self.s2v())[2]["code"], "input_missing")
            self.assertEqual(self.upload(url, PNG, self.image_sha, "png")[0], 201)
            bad = wav(10)
            bad_sha = hashlib.sha256(bad).hexdigest()
            self.assertEqual(self.upload(url, bad, bad_sha, "wav")[0], 201)
            status, _, error = call(url, route, method="PUT", body={**self.s2v(), "audio_sha256": bad_sha})
            self.assertEqual((status, error["code"]), (400, "invalid_input"))
            self.assertEqual(call(url, route)[0], 404)
            engine.assert_not_called()

    def test_profile_bounds_and_chunk_graph(self):
        for kind, body in (("i2v", self.i2v(frames=122)),
                           ("i2v", self.i2v(width=577)),
                           ("s2v", {**self.s2v(), "board_frames": 343}),
                           ("s2v", {**self.s2v(), "seed": 2**53-1}),
                           ("foley", self.foley_request(duration=9)),
                           ("foley", self.foley_request(start_seconds=float("nan"))),
                           ("foley", self.foley_request(source_job_id="../file"))):
            with self.subTest(kind=kind, body=body):
                with self.assertRaises(product.RequestError):
                    product.validate(kind, body)
        for board_frames, chunks in ((1, 1), (115, 2), (342, 3)):
            graph = api.speech_graph({"id": JOB, "seed": 42, "width": 576, "height": 1024,
                                      "frames": product.s2v_length(board_frames), "image_sha256": self.image_sha,
                                      "audio_sha256": self.audio_sha}, "p", "n")
            samplers = [node["inputs"] for node in graph.values() if node["class_type"] == "KSampler"]
            self.assertEqual([s["seed"] for s in samplers], list(range(42, 42 + chunks)))
            self.assertTrue(all(s["steps"] == 4 and s["cfg"] == 1.0 for s in samplers))
            self.assertEqual(graph["14"]["inputs"]["fps"], 16)
            self.assertEqual(graph["14"]["inputs"]["audio"], ["8", 0])
            self.assertNotIn("MultiGPU_WorkUnits", [node["class_type"] for node in graph.values()])

    def exercise_video(self, kind):
        route = f"/v1/jobs/{kind}/{JOB}"
        request = self.i2v() if kind == "i2v" else self.s2v()
        posts = []
        history_reads = []
        accepted = threading.Event()
        release = threading.Event()
        closed = threading.Event()
        real_generate = api.generate
        result_name = JOB + "_00001_.mp4"

        def fake_engine(path, body=None):
            if path == "/queue":
                return EMPTY
            if path == "/prompt":
                posts.append(body)
                self.assertEqual(uuid.UUID(body["prompt_id"]).version, 4)
                written = json.loads(api.job_path(JOB).read_text())
                self.assertEqual(written["prompt_id"], body["prompt_id"])
                self.assertEqual(written["graph_sha256"], product.digest(body["prompt"]))
                accepted.set()
                self.assertTrue(release.wait(5))
                # Simulate ComfyUI accepting /prompt but its answer being lost.
                raise TimeoutError("lost acknowledgment")
            self.assertEqual(path, "/history/" + posts[0]["prompt_id"])
            history_reads.append(path)
            if len(history_reads) == 1:
                raise TimeoutError("slow history decode")
            node = "12" if kind == "i2v" else "15"
            return {posts[0]["prompt_id"]: {"status": {"status_str": "success"}, "outputs": {
                node: {"images": [{"filename": result_name, "type": "output", "subfolder": "video"}]}}}}

        def generate(*args, **kwargs):
            try:
                real_generate(*args, **kwargs)
            finally:
                closed.set()

        output = self.root / "output" / "video" / result_name
        output.parent.mkdir(parents=True)
        output.write_bytes(VIDEO)
        with server(api.Handler) as url, patch.object(api, "engine", side_effect=fake_engine), \
                patch.object(api, "gpu_sample", return_value=self.sample), patch.object(api, "generate", generate), \
                patch.object(api.time, "sleep"):
            self.assertEqual(self.upload(url, PNG, self.image_sha, "png")[0], 201)
            if kind == "s2v":
                self.assertEqual(self.upload(url, self.audio, self.audio_sha, "wav")[0], 201)
            try:
                self.assertEqual(call(url, route, method="PUT", body=request)[0], 202)
                self.assertTrue(accepted.wait(5))
                code, _, status = call(url, route, method="PUT", body={**request, "width": 576})
                self.assertEqual((code, status["id"], status["prompt_id"]), (200, JOB, posts[0]["prompt_id"]))
                self.assertEqual(call(url, route, method="PUT", body={**request, "seed": 43})[2]["code"], "id_conflict")
                self.assertEqual(call(url, route.replace(JOB, "af" * 16), method="PUT", body=request)[2]["code"],
                                 "worker_busy")
                self.assertEqual(call(url, route + "/file")[0], 409)
            finally:
                release.set()
                self.assertTrue(closed.wait(6))
            code, _, status = call(url, route)
            self.assertEqual((code, status["state"], status["poll_misses"]), (200, "succeeded", 1))
            self.assertEqual(status["result"], {
                "file_url": route + "/file", "sha256": hashlib.sha256(VIDEO).hexdigest(),
                "bytes": len(VIDEO), "media_type": "video/mp4"})
            self.assertEqual(call(url, route, method="PUT", body=request)[2], status)
            code, headers, raw = call(url, route + "/file", headers={"Range": "bytes=0-2"})
            self.assertEqual((code, headers["Content-Type"], raw), (206, "video/mp4", VIDEO[:3]))
            self.assertEqual(call(url, "/videos/" + JOB + "/file")[0], 404)
            self.assertEqual(len(posts), 1)
        return posts[0]["prompt"]

    def test_i2v_poll_recovers_lost_replies_and_returns_owned_mp4(self):
        graph = self.exercise_video("i2v")
        self.assertEqual(graph["8"]["inputs"]["start_image"], ["13", 0])
        self.assertEqual(graph["8"]["inputs"]["length"], 121)
        self.assertEqual(graph["9"]["inputs"]["steps"], 20)
        self.assertEqual(graph["11"]["inputs"]["fps"], 24)

    def test_s2v_poll_recovers_lost_replies_with_fixed_lightning_profile(self):
        graph = self.exercise_video("s2v")
        self.assertEqual(graph["11"]["inputs"]["length"], 77)
        self.assertEqual(graph["110"]["inputs"]["length"], 77)
        self.assertEqual(graph["111"]["inputs"]["seed"], 43)
        self.assertEqual(graph["8"]["inputs"]["audio"], f"studio-v1/{self.audio_sha}.wav")

    def poll_missing_prompt(self, *, acknowledge=False, queue_at_five=None, bad_history=None, gpu_at_five=None):
        job = self.video_job()
        clock = FakeClock()
        queue_polls = []
        history_polls = []

        def reply(path, body=None):
            if path == "/queue" and body is None:
                queue_polls.append(clock.elapsed)
                if clock.elapsed == 5 and queue_at_five is not None:
                    return queue_at_five
                return EMPTY
            if path == "/prompt":
                if acknowledge:
                    return {"prompt_id": body["prompt_id"]}
                raise TimeoutError("lost acknowledgment")
            if path.startswith("/history/"):
                history_polls.append(clock.elapsed)
                if clock.elapsed == 5 and bad_history is not None:
                    if isinstance(bad_history, Exception):
                        raise bad_history
                    return bad_history
                return {}

        def sample():
            if clock.elapsed == 5 and gpu_at_five is not None:
                if isinstance(gpu_at_five, Exception):
                    raise gpu_at_five
                return gpu_at_five
            return self.sample

        with patch.object(api, "engine", side_effect=reply) as engine, \
                patch.object(api, "gpu_sample", side_effect=sample), \
                patch.object(api.uuid, "uuid4", return_value=uuid.UUID(PROMPT_ID)), \
                patch.object(api.time, "sleep", clock.advance), patch.object(api.time, "monotonic", clock.monotonic):
            api.ACTIVE = job["id"]
            lease = api.acquire_compute()
            api.generate(job, "p", lease, negative="n")
        self.assertTrue(lease.closed)
        self.assertIsNone(api.ACTIVE)
        self.assertEqual(job["state"], "failed")
        self.assertEqual(json.loads(api.job_path(JOB).read_text())["error"], job["error"])
        prompt_posts = [call.args[1] for call in engine.call_args_list if call.args[0] == "/prompt"]
        self.assertEqual([post["prompt_id"] for post in prompt_posts], [job["prompt_id"]])
        self.assert_engine_mutations(engine)
        return job, queue_polls, history_polls

    def assert_watchdog_timeout(self, job, queue_polls, history_polls):
        self.assertTrue(job["error"].startswith("TimeoutError: Video exceeded"), job["error"])
        self.assertEqual(history_polls, list(range(0, 1200, 5)))
        self.assertEqual(queue_polls, [0] + history_polls + [1200, 1200])

    def test_lost_submission_releases_lease_after_120_seconds_of_clean_idle_polls(self):
        at_idle_limit = {"gpus": [{**self.sample["gpus"][0], "utilization_percent": 5, "memory_mib": 22000}]}
        job, queue_polls, history_polls = self.poll_missing_prompt(gpu_at_five=at_idle_limit)
        self.assertFalse(api.UNSAFE)
        self.assertTrue(job["error"].startswith("PromptSubmissionLost:"))
        self.assertEqual(history_polls, list(range(0, 121, 5)))
        self.assertEqual(queue_polls, [0] + history_polls)
        with api.acquire_compute():
            pass

    def test_acknowledged_submission_keeps_conservative_watchdog(self):
        job, queue_polls, history_polls = self.poll_missing_prompt(acknowledge=True)
        self.assert_watchdog_timeout(job, queue_polls, history_polls)
        self.assertFalse(api.UNSAFE)

    def test_prompt_seen_in_queue_keeps_watchdog_after_it_disappears(self):
        owned = {**EMPTY, "queue_pending": [[1, PROMPT_ID]]}
        job, queue_polls, history_polls = self.poll_missing_prompt(queue_at_five=owned)
        self.assert_watchdog_timeout(job, queue_polls, history_polls)
        self.assertFalse(api.UNSAFE)

    def test_latched_hold_blocks_lost_submission(self):
        foreign = {**EMPTY, "queue_running": [[1, "foreign"]]}
        job, queue_polls, history_polls = self.poll_missing_prompt(queue_at_five=foreign)
        self.assert_watchdog_timeout(job, queue_polls, history_polls)
        self.assertEqual(job["unexpected_work_seen"], ["foreign"])
        self.assertTrue(api.UNSAFE)

    def test_busy_gpu_sample_blocks_lost_submission(self):
        busy = {"gpus": [{**self.sample["gpus"][0], "memory_mib": 22001}]}
        job, queue_polls, history_polls = self.poll_missing_prompt(gpu_at_five=busy)
        self.assert_watchdog_timeout(job, queue_polls, history_polls)
        self.assertTrue(api.UNSAFE)

    def test_unreadable_gpu_sample_blocks_lost_submission(self):
        job, queue_polls, history_polls = self.poll_missing_prompt(
            gpu_at_five=subprocess.TimeoutExpired("nvidia-smi", 10))
        self.assert_watchdog_timeout(job, queue_polls, history_polls)
        self.assertTrue(api.UNSAFE)

    def test_failed_poll_keeps_unconfirmed_submission_on_conservative_watchdog(self):
        job, queue_polls, history_polls = self.poll_missing_prompt(bad_history=TimeoutError("one slow reply"))
        self.assert_watchdog_timeout(job, queue_polls, history_polls)
        self.assertTrue(api.UNSAFE)
        self.assertEqual(job["poll_misses"], 1)

    def test_malformed_queue_or_history_keeps_unconfirmed_submission_held(self):
        for fault in ({"queue_at_five": {"queue_running": []}}, {"bad_history": []},
                      {"bad_history": {"foreign": {}}}):
            with self.subTest(fault=fault), patch.object(api, "UNSAFE", False):
                job, queue_polls, _ = self.poll_missing_prompt(**fault)
                self.assertTrue(api.UNSAFE)
                self.assertTrue(job["error"].startswith("ValueError: Malformed ComfyUI"))
                self.assertEqual(queue_polls[-2:], [5, 5])

    def test_explicit_engine_rejection_has_no_prompt_retry_or_hold(self):
        job = self.video_job()
        rejection = HTTPError("engine", 400, "rejected", None, None)
        with patch.object(api, "engine", side_effect=[EMPTY, rejection]) as engine, \
                patch.object(api, "gpu_sample", return_value=self.sample):
            api.generate(job, "p", negative="n")
        self.assertFalse(api.UNSAFE)
        self.assertEqual(job["state"], "failed")
        self.assertEqual([c.args[0] for c in engine.call_args_list], ["/queue", "/prompt"])

    def fail_product_render(self, *queues):
        job = self.video_job()
        queue_reads = iter((EMPTY, *queues))

        def reply(path, body=None):
            if path == "/queue" and body is None:
                return next(queue_reads, EMPTY)
            if path == "/prompt":
                return {"prompt_id": body["prompt_id"]}
            if path == "/history/" + PROMPT_ID:
                return {job["prompt_id"]: {"status": {"status_str": "error"}}}

        with patch.object(api, "engine", side_effect=reply) as engine, \
                patch.object(api, "gpu_sample", return_value=self.sample), patch.object(api.time, "sleep"), \
                patch.object(api.uuid, "uuid4", return_value=uuid.UUID(PROMPT_ID)):
            api.generate(job, "p", negative="n")
        self.assertEqual(job["state"], "failed")
        self.assertEqual(json.loads(api.job_path(JOB).read_text())["state"], "failed")
        return job, engine

    def test_product_poll_records_foreign_work_and_latches_hold(self):
        foreign = {**EMPTY, "queue_running": [[1, "foreign", {}, {"client_id": "other"}]]}
        job, engine = self.fail_product_render(foreign, EMPTY, EMPTY)
        self.assert_engine_mutations(engine)
        self.assertEqual(job.get("unexpected_work_seen"), ["foreign"])
        self.assertTrue(api.UNSAFE)

    def test_cleanup_never_interrupts_foreign_running_work(self):
        mixed = {**EMPTY, "queue_running": [[1, PROMPT_ID], [2, "foreign", {}, {"client_id": "other"}]]}
        _, engine = self.fail_product_render(EMPTY, mixed)
        self.assert_engine_mutations(engine)
        self.assertTrue(api.UNSAFE)

    def test_cleanup_deletes_only_owned_pending_work_and_holds_for_foreign(self):
        mixed = {**EMPTY, "queue_pending": [[1, PROMPT_ID], [2, "foreign", {}, {"client_id": "other"}]]}
        _, engine = self.fail_product_render(EMPTY, mixed, EMPTY)
        self.assert_engine_mutations(engine, deletes=([PROMPT_ID],))
        self.assertTrue(api.UNSAFE)

    def test_cleanup_holds_for_foreign_work_arriving_after_owned_interrupt(self):
        owned = {**EMPTY, "queue_running": [[1, PROMPT_ID]]}
        foreign = {**EMPTY, "queue_pending": [[2, "foreign"]]}
        _, engine = self.fail_product_render(EMPTY, owned, foreign, EMPTY)
        self.assert_engine_mutations(engine, interrupts=(PROMPT_ID,))
        self.assertEqual(engine.call_args_list[-2:], [engine_call("/interrupt", {"prompt_id": PROMPT_ID}),
                                                      engine_call("/queue")])
        self.assertTrue(api.UNSAFE)

    def test_cleanup_waits_for_owned_pending_work_to_disappear(self):
        owned = {**EMPTY, "queue_pending": [[1, PROMPT_ID]]}
        _, engine = self.fail_product_render(EMPTY, owned, owned, EMPTY)
        self.assert_engine_mutations(engine, deletes=([PROMPT_ID],))
        self.assertEqual(engine.call_args_list[-3:], [engine_call("/queue", {"delete": [PROMPT_ID]}),
                                                      engine_call("/queue"), engine_call("/queue")])
        self.assertFalse(api.UNSAFE)

    def test_restart_marks_typed_jobs_failed_without_replay(self):
        for kind in ("i2v", "s2v", "foley"):
            with self.subTest(kind=kind):
                job_id = uuid.uuid4().hex
                job = {"id": job_id, "type": kind, "api_version": product.VERSION, "state": "running"}
                save = self.foley.save if kind == "foley" else api.save
                path = self.foley.job_path if kind == "foley" else api.job_path
                save(job)
                with patch.object(api, "engine", return_value=EMPTY) as engine:
                    (product_foley.recover if kind == "foley" else api.recover)()
                self.assertEqual(json.loads(path(job_id).read_text())["state"], "failed")
                self.assertEqual([args.args[0] for args in engine.call_args_list], [] if kind == "foley" else ["/queue"])

    def test_hot_gpu_and_worker_start_failure_release_compute(self):
        job = {**product.validate("i2v", self.i2v()), "id": JOB, "type": "i2v", "api_version": product.VERSION,
               "state": "queued", "telemetry": []}
        self.sample["gpus"][0]["temperature_c"] = 62
        with patch.object(api, "engine") as engine, patch.object(api, "gpu_sample", return_value=self.sample):
            api.generate(job, "p", negative="n")
        engine.assert_not_called()
        self.assertEqual(job["state"], "failed")
        retry_id = "0f" * 16
        product.stage_input(self.root, self.image_sha, "png", io.BytesIO(PNG).read, len(PNG))
        with patch.object(api.threading, "Thread", side_effect=RuntimeError("simulated")):
            code, status = api.submit_product_video("i2v", retry_id, product.validate("i2v", self.i2v()))
        self.assertEqual((code, status["state"]), (503, "failed"))
        self.assertIsNone(api.ACTIVE)
        with api.acquire_compute():
            pass

    def test_foley_proxy_uses_shared_lease_and_serves_existing_result_when_sidecar_is_down(self):
        source = self.root / "output" / "input.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(VIDEO)
        request = self.foley_request()
        api.save({"id": request["source_job_id"], "state": "succeeded", "output": source.name})
        self.foley.WEIGHTS.mkdir()
        (self.foley.WEIGHTS / "manifest.json").write_text("{}")
        result = wav(20)
        completed = threading.Event()
        digest = hashlib.sha256(result).hexdigest()
        route = f"/v1/jobs/foley/{JOB}"

        def fake_generate(job, lease):
            output = self.root / "output" / "foley" / job["id"] / "output.wav"
            output.parent.mkdir(parents=True)
            output.write_bytes(result)
            job.update(state="succeeded", output=str(output.relative_to(self.root / "output")), bytes=len(result),
                       provenance={"output_sha256": digest})
            self.foley.save(job)
            lease.close()
            with self.foley.LOCK:
                self.foley.ACTIVE = None
            completed.set()

        with server(api.Handler) as url, patch.object(self.foley, "idle") as idle, \
                patch.object(self.foley, "generate", side_effect=fake_generate) as generate, \
                patch.object(api, "engine", return_value={}), patch.object(api, "gpu_sample", return_value=self.sample):
            with server(product_foley.Handler) as loopback, patch.object(api, "FOLEY", loopback):
                code, _, readiness = call(url, "/v1/ready")
                self.assertEqual((code, readiness["ready"], readiness["api_version"]), (200, True, "1.0.0"))
                with api.acquire_compute():
                    self.assertEqual(call(url, route, method="PUT", body=request)[2]["code"], "worker_busy")
                    self.assertEqual(call(url, route)[0], 404)
                    generate.assert_not_called()
                try:
                    self.assertEqual(call(url, route, method="PUT", body=request)[0], 202)
                    self.assertTrue(completed.wait(6))
                    self.assertEqual(call(url, route, method="PUT", body={**request, "duration": 8.0})[0], 200)
                    self.assertEqual(call(url, route, method="PUT", body={**request, "duration": 5})[2]["code"],
                                     "id_conflict")
                    idle.assert_called_once()
                    generate.assert_called_once()
                finally:
                    if generate.called:
                        self.assertTrue(completed.wait(6))
            with patch.object(api, "FOLEY", loopback):
                self.assertEqual(call(url, "/v1/ready")[0], 503)
                status = call(url, route)[2]
                self.assertEqual(status["result"]["sha256"], digest)
                self.assertEqual(call(url, route, method="PUT", body=request)[2], status)
                code, headers, body = call(url, route + "/file")
                self.assertEqual((code, headers.get_content_type(), body), (200, "audio/wav", result))


if __name__ == "__main__":
    unittest.main()
