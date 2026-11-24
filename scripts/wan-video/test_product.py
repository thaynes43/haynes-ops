"""Product HTTP, queue and filesystem behavior with CPU-only fake engines."""
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid
import wave
import zlib

from test_api import APP, REPO, api
import product

sys.path.insert(0, str(REPO / "scripts/controlfoley"))
import product_foley


EMPTY = {"queue_running": [], "queue_pending": []}
PNG = (product.PNG + struct.pack(">I4sIIBBBBB", 13, b"IHDR", 1, 1, 8, 2, 0, 0, 0)
       + struct.pack(">I", zlib.crc32(struct.pack(">4sIIBBBBB", b"IHDR", 1, 1, 8, 2, 0, 0, 0))))
VIDEO = b"synthetic MP4 contents"
STORY = "https://work.haynesnetwork.com/work_packages/458"
JOB = "01" * 16
FRAMES = 120


def wav(frames):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setparams((1, 2, 24000, frames, "NONE", "not compressed"))
        writer.writeframes(b"\0\0" * frames)
    return buffer.getvalue()


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

    def test_unconfirmed_prompt_holds_after_watchdog_without_retrying_the_post(self):
        job = {**product.validate("i2v", self.i2v()), "id": JOB, "type": "i2v", "api_version": product.VERSION,
               "state": "queued", "telemetry": []}

        def reply(path, body=None):
            if path == "/queue" and body is None:
                return EMPTY
            if path == "/prompt":
                raise TimeoutError("lost acknowledgment")
            if path.startswith("/history/"):
                return {}
            self.fail("unexpected engine mutation: " + path)

        with patch.object(api, "engine", side_effect=reply) as engine, \
                patch.object(api, "gpu_sample", return_value=self.sample), patch.object(api.time, "sleep"), \
                patch.object(api.time, "monotonic", side_effect=[0, 1, 1200]):
            api.generate(job, "p", negative="n")
        self.assertTrue(api.UNSAFE)
        self.assertEqual(job["state"], "failed")
        self.assertEqual([c.args[0] for c in engine.call_args_list].count("/prompt"), 1)

    def test_explicit_engine_rejection_has_no_prompt_retry_or_hold(self):
        job = {**product.validate("i2v", self.i2v()), "id": JOB, "type": "i2v", "api_version": product.VERSION,
               "state": "queued", "telemetry": []}
        rejection = HTTPError("engine", 400, "rejected", None, None)
        with patch.object(api, "engine", side_effect=[EMPTY, rejection]) as engine, \
                patch.object(api, "gpu_sample", return_value=self.sample):
            api.generate(job, "p", negative="n")
        self.assertFalse(api.UNSAFE)
        self.assertEqual(job["state"], "failed")
        self.assertEqual([c.args[0] for c in engine.call_args_list], ["/queue", "/prompt"])

    def test_foreign_queue_work_remains_and_latches_hold(self):
        job = {**product.validate("i2v", self.i2v()), "id": JOB, "type": "i2v", "api_version": product.VERSION,
               "state": "queued", "telemetry": []}
        future = {"queue_running": [[1, "foreign", {}, {"client_id": "other"}]], "queue_pending": []}

        def reply(path, body=None):
            if path == "/queue":
                return future if job.get("prompt_id") else EMPTY
            if path == "/prompt":
                return {"prompt_id": body["prompt_id"]}
            if path.startswith("/history/"):
                return {job["prompt_id"]: {"status": {"status_str": "error"}}}
            self.fail("unexpected engine mutation: " + path)

        with patch.object(api, "engine", side_effect=reply), patch.object(api, "gpu_sample", return_value=self.sample):
            api.generate(job, "p", negative="n")
        self.assertTrue(api.UNSAFE)
        self.assertEqual(job["unexpected_work_seen"], ["foreign"])
        self.assertEqual(job["state"], "failed")
        self.assertEqual(json.loads(api.job_path(JOB).read_text())["state"], "failed")

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
        product.stage_input(self.root, self.image_sha, "png", io.BytesIO(PNG), len(PNG))
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
