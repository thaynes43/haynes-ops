"""CPU-only handover tests with the real flock, journal, HTTP and Unix protocol."""
from array import array
import hashlib
from http.client import HTTPException
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from test_api import APP, api
from test_product import EMPTY, IHDR, PIXEL, PNG, call, png_chunk, server
import local_model_sidecar as sidecar
import local_model_modes as modes
import product
import product_foley
import product_local as local


CARD = product_foley.service.CARD
OTHER = next(iter(local.APPROVED_CARDS - {CARD}))
PROFILE = {
    "model": "test-multimodal",
    "gpu_uuid": CARD,
    "s2v_gpu_uuid": OTHER,
    "context_tokens": 64,
    "max_output_tokens": 12,
    "load_timeout_seconds": 4,
    "infer_timeout_seconds": 5,
    "unload_timeout_seconds": 6,
    "unloaded_max_mib": 128,
    "resident_min_mib": 4000,
    "resident_max_mib": 6000,
}
LOAD, INFER, UNLOAD = ("01" * 16, "02" * 16, "03" * 16)


def endpoint(job_id):
    return "/v1/local-model/requests/" + job_id


class FakeModel:
    def __init__(self, fixture):
        self.f = fixture

    def load(self):
        self.f.baseline_at_load = self.f.lane.read_state().get("baseline_memory_mib")
        self.f.record_work("load")
        self.f.memory = self.f.load_memory

    def prepare(self, prompt, images, mode):
        self.f.events.append(("prepare", prompt, [image.read_bytes() for image in images]))
        self.f.events.append(("mode", mode))
        return (prompt, images), self.f.inputs, self.f.visual if self.f.visual is not None else [2] * len(images)

    def infer(self, prepared, cap):
        self.f.record_work("infer")
        self.f.events.append(("token_cap", cap))
        self.f.infer_entered.set()
        if self.f.block_infer:
            self.f.release_infer.wait(5)
        return self.f.text, self.f.outputs, self.f.complete

    def unload(self):
        self.f.record_work("unload")
        if self.f.free_on_unload:
            self.f.memory = self.f.drained_memory


class InlineProcess:
    """Fake process interface for the API and journal tests."""
    def __init__(self, fixture, profile):
        self.f = fixture
        self.profile = profile
        self.backend = FakeModel(fixture)
        self.resident = False

    def execute(self, request, deadline, parent_fds):
        operation = request["operation"]
        self.f.events.append(("deadline", round(deadline - time.monotonic())))
        try:
            if operation == "load":
                self.backend.load()
                self.resident = True
            elif operation == "unload":
                self.stop()
            else:
                reply = sidecar.infer(self.backend, self.f.root, self.profile, request)
                if reply.get("error") == "output_limit":
                    self.stop()
                return reply
        except ValueError:
            self.stop()
            return {"error": "backend_failed"}
        return {}

    def alive(self):
        return self.resident

    def stop(self):
        self.backend.unload()
        self.resident = False


class LocalModelTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = self.root / "config"
        self.config.mkdir()
        self.config.joinpath("workflow.json").write_bytes(APP.joinpath("workflow.json").read_bytes())
        self.config.joinpath("openapi.json").write_bytes(APP.joinpath("openapi.json").read_bytes())
        self.config.joinpath("local-model.json").write_text(json.dumps(PROFILE))
        self.lane = local.Lane(self.root, PROFILE)
        self.events = []
        self.prompts = []
        self.engine_calls = []
        self.memory = self.drained_memory = self.other_memory = 64
        self.wan_cache = None
        self.baseline_at_load = None
        self.load_memory = 5000
        self.report_card = CARD
        self.card_temperature = 50
        self.inputs, self.text, self.outputs = 8, "A short result", 3
        self.visual = None
        self.complete = True
        self.queue = EMPTY
        self.sidecar_queue = None
        self.free_on_unload = True
        self.block_infer = False
        self.infer_entered = threading.Event()
        self.release_infer = threading.Event()
        self.infer_connection_closed = threading.Event()
        self.worker = self.new_sidecar()
        self.http_done = threading.Event()
        self.start_patch(local, sample_card=self.card_sample)
        self.start_patch(api, ROOT=self.root, CONFIG=self.config, ACTIVE=None, UNSAFE=False,
                         engine=self.engine, gpu_sample=self.gpu_sample)
        self.start_patch(api, run_local_request=self.run_http_worker)
        self.start_sidecar()

    def start_patch(self, target, **values):
        patcher = patch.multiple(target, **values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def start_sidecar(self):
        path = local.socket_path(self.root)
        path.parent.mkdir()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        listener.bind(str(path))
        listener.listen(4)
        stopped = threading.Event()
        self.sidecar_errors = []

        def listen():
            while not stopped.is_set():
                conn, _ = listener.accept()
                with conn:
                    if not stopped.is_set():
                        try:
                            self.worker.handle(conn)
                        except Exception as exc:
                            self.sidecar_errors.append(type(exc).__name__)
                        finally:
                            if self.infer_entered.is_set() and self.release_infer.is_set():
                                self.infer_connection_closed.set()

        thread = threading.Thread(target=listen)
        thread.start()

        def stop():
            self.release_infer.set()
            stopped.set()
            with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as wake:
                wake.connect(str(path))
            thread.join(timeout=6)
            listener.close()
            self.assertFalse(thread.is_alive())
            self.assertEqual(self.sidecar_errors, [])

        self.addCleanup(stop)

    def new_sidecar(self, profile=PROFILE):
        def check_idle():
            queue = self.queue if self.sidecar_queue is None else self.sidecar_queue
            self.events.append(("receiver_queue", queue))
            # This fake uses the actual strict queue parser and records its reply.
            if isinstance(queue, Exception):
                engine = patch.object(api, "engine", side_effect=queue)
            else:
                engine = patch.object(api, "engine", return_value=queue)
            with engine:
                api.require_empty_queue()

        def evict_cache():
            self.engine("/free", {"unload_models": True, "free_memory": True})

        worker = sidecar.Sidecar(self.root, profile, None, check_idle=check_idle,
                                 evict_cache=evict_cache, sample=self.card_sample)
        worker.model = InlineProcess(self, profile)
        return worker

    def card_sample(self, card):
        self.events.append(("memory", card, self.memory))
        return {"gpu_uuid": self.report_card, "memory_mib": self.memory,
                "temperature_c": self.card_temperature, "utilization_percent": 0}

    def gpu_sample(self):
        return {"gpus": [self.card_sample(CARD), {
            "gpu_uuid": OTHER, "memory_mib": self.other_memory, "temperature_c": 50, "utilization_percent": 0,
        }]}

    def engine(self, path, body=None):
        self.engine_calls.append((path, body))
        if path == "/queue":
            return self.queue
        if path == "/system_stats":
            return {}
        if path == "/free":
            self.assertEqual(body, {"unload_models": True, "free_memory": True})
            competing = api.acquire_compute()
            if competing is not None:
                competing.close()
                self.fail("Cache eviction requires the compute lease")
            self.events.append(("free_cache", self.memory))
            self.memory = self.drained_memory
            self.other_memory = 64
            return None
        if path == "/prompt":
            self.events.append(("wan_prompt", self.memory))
            prompt_id = body.get("prompt_id", "legacy-prompt")
            filename = body["client_id"] + "_00001_.mp4"
            self.prompts.append((prompt_id, filename))
            if self.wan_cache is not None:
                self.memory, self.other_memory = self.wan_cache
            output = self.root / "output" / "video" / filename
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"synthetic")
            return {"prompt_id": prompt_id}
        prompt_id, filename = self.prompts[-1]
        if path == "/history/" + prompt_id:
            item = {"filename": filename, "subfolder": "video", "type": "output"}
            return {prompt_id: {"outputs": {"12": {"images": [item]}, "15": {"images": [item]}}}}
        return {}

    def record_work(self, operation):
        state = product.read_job(self.lane.path)
        job = self.lane.read(state["pending"]) if state and state.get("pending") else None
        competing = api.acquire_compute()
        self.events.append((operation, job and job["id"], job and job["state"], competing is not None))
        if competing:
            competing.close()

    def run_http_worker(self, *args):
        try:
            self.real_http_worker(*args)
        finally:
            self.http_done.set()

    real_http_worker = staticmethod(api.run_local_request)

    def body(self, operation, **fields):
        if operation == "infer":
            fields = {"prompt": "Describe the board", **fields}
        return {"profile": PROFILE["model"], "operation": operation, **fields}

    def submit(self, url, job_id, operation, **fields):
        return call(url, endpoint(job_id), method="PUT", body=self.body(operation, **fields))

    def perform(self, job_id, operation, **fields):
        request = local.validate(self.lane.profile, self.body(operation, **fields))
        with api.acquire_compute() as lease:
            job = self.lane.create(job_id, request)
            return self.lane.run(job, request, lease, api.check_local_compute)

    def generate(self, kind, job_id="a1" * 16):
        if kind is None:
            job = {"id": job_id, "state": "queued", "preset": "preview", "seed": 1, "telemetry": []}
        else:
            job = {"id": job_id, "type": kind, "api_version": product.VERSION, "state": "queued",
                   "seed": 1, "width": 576, "height": 1024, "frames": 21, "image_sha256": "1" * 64,
                   "audio_sha256": "2" * 64, "telemetry": []}
        api.generate(job, "fake Wan", negative="test")
        return job

    def work(self, *operations):
        return [event for event in self.events if event[0] in operations]

    def test_disabled_routes_and_invalid_profile(self):
        profile_path = self.config / "local-model.json"
        profile_path.unlink()
        with server(api.Handler) as url:
            self.assertEqual(call(url, local.PROFILE_ROUTE)[2]["code"], "local_model_not_installed")
            self.assertEqual(call(url, endpoint(LOAD))[2]["code"], "local_model_not_installed")
            self.assertEqual(self.submit(url, LOAD, "load")[2]["code"], "local_model_not_installed")
            self.assertEqual(call(url, "/v1/profiles")[2]["profiles"], product.PROFILES)
            profile_path.write_text(json.dumps({**PROFILE, "resident_min_mib": 64}))
            self.assertEqual(call(url, local.PROFILE_ROUTE)[2]["code"], "local_model_misconfigured")
        self.assertEqual(self.work("load", "infer", "unload"), [])

    def test_no_profile_keeps_routes_startup_and_health(self):
        self.config.joinpath("local-model.json").unlink()
        with patch.object(local, "exchange", side_effect=AssertionError("sidecar contacted")) as exchange:
            with patch.object(api, "ThreadingHTTPServer") as http, patch.object(api, "acquire_compute") as acquire:
                api.main()
                self.assertEqual(self.engine_calls, [("/queue", None)])
                acquire.assert_not_called()
                http.assert_called_once_with(("0.0.0.0", 8080), api.Handler)
                self.assertEqual(http.return_value.timeout, 10)
                http.return_value.serve_forever.assert_called_once_with()
            with server(api.Handler) as url, patch.object(api, "foley", return_value=(
                    200, {"api_version": "1.0.0", "active_job": None})):
                self.assertEqual(call(url, "/health")[2], {
                    "api": "ok", "accepting_jobs": True, "active_job": None,
                })
                self.assertEqual(call(url, "/ready")[2], {"ready": True})
                self.assertEqual(call(url, "/v1/ready")[2], {
                    "api_version": "1.0.0", "ready": True, "active_video_job": None, "active_foley_job": None,
                })
                self.assertEqual(call(url, "/v1/profiles")[2], {"api_version": "1.0.0", "profiles": product.PROFILES})
                self.assertEqual(call(url, "/v1/local-model/profile")[2]["code"], "local_model_not_installed")
                api.UNSAFE = True
                self.assertEqual(call(url, "/health")[2], {
                    "api": "ok", "accepting_jobs": False, "active_job": None,
                })
                self.assertEqual(call(url, "/ready")[0], 503)
                self.assertEqual(call(url, "/v1/ready")[2]["code"], "worker_held")
                api.UNSAFE = False
                self.queue = {**EMPTY, "queue_pending": [[0, "foreign"]]}
                legacy = self.generate(None)
                self.assertEqual(call(url, "/videos/" + legacy["id"])[2], legacy)
                self.assertEqual(legacy["error"], "RuntimeError: ComfyUI already has a job; refusing a competing request")
                self.assertNotIn("admission_code", legacy)
                self.assertFalse(api.UNSAFE)
                self.queue = EMPTY
                typed = self.generate("i2v", "e0" * 16)
                route = "/v1/jobs/i2v/" + typed["id"]
                self.assertEqual(call(url, route)[2]["state"], "succeeded")
                self.assertEqual(call(url, route + "/file")[2], b"synthetic")
            exchange.assert_not_called()
        self.assertFalse(self.lane.path.exists())

    def test_mounted_outage_refuses_without_latching_even_with_engine_cache(self):
        self.memory = 5000
        with patch.object(local, "exchange", side_effect=ConnectionRefusedError) as exchange:
            api.recover_with_local()
            for kind in ("i2v", "s2v", None):
                with self.subTest(kind=kind):
                    job = self.generate(kind, uuid.uuid4().hex)
                    self.assertEqual((job["state"], job["admission_code"]), ("failed", "local_model_unavailable"))
            with patch.object(product_foley, "CONFIG", self.config), \
                    patch.multiple(product_foley.service, ROOT=self.root, idle=api.check_local_queue), \
                    api.acquire_compute() as lease:
                with self.assertRaises(product.AdmissionRefusal) as refusal:
                    product_foley.prepare_foley(lease)
                self.assertEqual(refusal.exception.code, "local_model_unavailable")
            job = self.perform(LOAD, "load")
            self.assertEqual(job["error"], "local_model_unavailable")
            self.assertEqual(exchange.call_count, 5)
            self.assertFalse(self.lane.path.exists())
        self.assertEqual(self.work("wan_prompt", "free_cache", "load"), [])
        self.assertEqual(self.generate("i2v", "e1" * 16)["state"], "succeeded")

    def test_startup_refusal_keeps_wan_and_foley_available_and_refuses_the_model(self):
        self.memory = 5000
        self.worker = sidecar.Sidecar(self.root, PROFILE, None, check_idle=None, evict_cache=None,
                                      startup_error="gpu_pin_mismatch")
        expected = local.StartupUnavailable("gpu_pin_mismatch").body()
        with server(api.Handler) as url:
            self.assertEqual(call(url, local.PROFILE_ROUTE)[::2], (503, expected))
            for operation, job_id in (("load", LOAD), ("infer", INFER)):
                self.assertEqual(self.submit(url, job_id, operation)[::2], (503, expected))
                self.assertIsNone(self.lane.read(job_id))
        for kind in ("i2v", "s2v", None):
            self.assertEqual(self.generate(kind, uuid.uuid4().hex)["state"], "succeeded")
        with patch.object(product_foley, "CONFIG", self.config), \
                patch.multiple(product_foley.service, ROOT=self.root, idle=api.check_local_queue):
            with api.acquire_compute() as lease:
                product_foley.prepare_foley(lease)
            self.queue = {**EMPTY, "queue_pending": [[0, "foreign"]]}
            with api.acquire_compute() as lease, self.assertRaises(product.AdmissionRefusal) as refusal:
                product_foley.prepare_foley(lease)
            self.assertEqual(refusal.exception.code, "engine_busy")
        self.assertEqual(self.generate("i2v", uuid.uuid4().hex)["admission_code"], "engine_busy")
        self.assertFalse(self.lane.path.exists())
        self.assertFalse(api.UNSAFE)
        self.assertEqual(self.work("load", "infer", "unload", "free_cache"), [])

    def test_missing_active_profile_requires_no_child_proof_for_wan_and_foley(self):
        self.config.joinpath("local-model.json").unlink()
        self.worker = sidecar.Sidecar(self.root, None, None, check_idle=None, evict_cache=None,
                                      startup_error="profile_missing")
        with patch.dict("os.environ", {"WAN_LOCAL_MODEL_ENABLED": "1"}), \
                patch.object(product_foley, "CONFIG", self.config), \
                patch.multiple(product_foley.service, ROOT=self.root, idle=api.check_local_queue):
            with server(api.Handler) as url:
                expected = local.StartupUnavailable("profile_missing").body()
                self.assertEqual(call(url, local.PROFILE_ROUTE)[::2], (503, expected))
                self.assertEqual(self.submit(url, LOAD, "load")[::2], (503, expected))
            self.assertEqual(self.generate("i2v", uuid.uuid4().hex)["state"], "succeeded")
            with api.acquire_compute() as lease:
                product_foley.prepare_foley(lease)
            with patch.object(local, "exchange", side_effect=ConnectionRefusedError):
                self.assertEqual(self.generate("i2v", uuid.uuid4().hex)["admission_code"], "local_model_unavailable")
                with api.acquire_compute() as lease, self.assertRaises(product.AdmissionRefusal) as refusal:
                    product_foley.prepare_foley(lease)
                self.assertEqual(refusal.exception.code, "local_model_unavailable")
        self.assertFalse(self.lane.path.exists())
        self.assertFalse(api.UNSAFE)

    def test_startup_refusal_preserves_a_previous_residency_reservation(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        old_state = self.lane.read_state()
        self.worker = sidecar.Sidecar(self.root, PROFILE, None, check_idle=None, evict_cache=None,
                                      startup_error="weights_mismatch")
        self.assertEqual(self.generate("i2v", uuid.uuid4().hex)["admission_code"], "local_model_unavailable")
        with patch.object(product_foley, "CONFIG", self.config), \
                patch.multiple(product_foley.service, ROOT=self.root, idle=api.check_local_queue), \
                api.acquire_compute() as lease, self.assertRaises(product.AdmissionRefusal) as refusal:
            product_foley.prepare_foley(lease)
        self.assertEqual(refusal.exception.code, "local_model_unavailable")
        self.assertEqual(self.lane.read_state(), old_state)
        self.assertFalse(api.UNSAFE)
        self.assertEqual(self.work("wan_prompt", "unload"), [])

    def test_wan_foley_sequence_keeps_idle_engine_cache_usable(self):
        self.wan_cache = (5000, 8000)
        self.assertEqual(self.generate("i2v", "a1" * 16)["state"], "succeeded")
        with patch.object(self.worker, "sample", side_effect=OSError("No sidecar GPU telemetry")):
            self.assertEqual(self.generate("i2v", "a2" * 16)["state"], "succeeded")
        foley = product_foley.service
        job = {"id": "a3" * 16, "state": "queued"}

        def fake_audio_worker(job, lease):
            with lease:
                self.engine("/free", {"unload_models": True, "free_memory": True})
                self.events.append(("foley", self.memory))
                self.memory, self.other_memory = self.wan_cache
                job["state"] = "succeeded"

        with patch.object(product_foley, "CONFIG", self.config), \
                patch.multiple(foley, ROOT=self.root, idle=api.check_local_queue):
            product_foley.generate(job, api.acquire_compute(), fake_audio_worker)
        self.assertEqual(job["state"], "succeeded")
        self.assertEqual(self.generate("i2v", "a4" * 16)["state"], "succeeded")
        self.assertEqual(self.work("wan_prompt", "foley"), [
            ("wan_prompt", 64), ("wan_prompt", 5000), ("foley", 64), ("wan_prompt", 5000),
        ])
        self.assertFalse(self.lane.path.exists())
        self.assertFalse(api.UNSAFE)
        self.assertEqual(self.work("free_cache"), [("free_cache", 5000)])

    def test_no_profile_foley_startup_and_health_keep_the_pinned_service_contract(self):
        self.config.joinpath("local-model.json").unlink()
        foley = product_foley.service
        weights = self.root / "weights"
        weights.mkdir()
        (weights / "manifest.json").touch()
        with patch.object(product_foley, "CONFIG", self.config), \
                patch.multiple(foley, ROOT=self.root, WEIGHTS=weights, ACTIVE=None), \
                patch.object(foley, "generate") as engine, patch.object(foley, "save"), \
                patch.object(local, "exchange", side_effect=AssertionError("sidecar contacted")) as exchange:
            with patch.object(product_foley, "ThreadingHTTPServer") as http:
                product_foley.main()
                http.assert_called_once_with(("127.0.0.1", 8189), product_foley.Handler)
                http.return_value.serve_forever.assert_called_once_with()
            with server(product_foley.Handler) as url:
                self.assertEqual(call(url, "/health")[2], {
                    "service": "ControlFoley", "active_job": None, "models_present": True,
                })
                self.assertEqual(call(url, "/v1/ready")[2], {"api_version": "1.0.0", "active_job": None})
                self.assertEqual(call(url, "/foley/" + INFER)[0], 404)
            job = {"id": LOAD, "state": "queued"}
            with api.acquire_compute() as lease:
                foley.generate(job, lease)
                engine.assert_called_once_with(job, lease)
            exchange.assert_not_called()

    def test_existing_render_hold_still_refuses_mounted_wan_admission(self):
        api.UNSAFE = True
        job = self.generate("i2v")
        self.assertEqual(job["admission_code"], "lease_held")
        self.assertEqual(self.work("wan_prompt"), [])

    def test_wan_foreign_engine_work_preserves_baseline_error_without_a_hold(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.queue = {**EMPTY, "queue_pending": [[0, "foreign"]]}
        job = self.generate("i2v")
        self.assertEqual((job["error"], job["admission_code"]), (
            "RuntimeError: ComfyUI already has a job; refusing a competing request", "engine_busy",
        ))
        self.assertEqual(product.job_view(product.read_job(api.job_path(job["id"])))["admission_code"], "engine_busy")
        self.assertEqual(self.work("unload", "wan_prompt"), [])
        self.assertFalse(api.UNSAFE)
        self.assertTrue(self.lane.read_state()["resident"])
        self.queue = EMPTY
        self.assertEqual(self.generate("i2v", "e1" * 16)["state"], "succeeded")

    def test_receiver_foreign_work_refuses_wan_handover_without_latching(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.sidecar_queue = {**EMPTY, "queue_running": [[0, "late-foreign"]]}
        job = self.generate("i2v")
        self.assertEqual((job["error"], job["admission_code"]), (
            "RuntimeError: ComfyUI already has a job; refusing a competing request", "engine_busy",
        ))
        self.assertFalse(api.UNSAFE)
        state = self.lane.read_state()
        self.assertTrue(state["resident"])
        self.assertNotIn("pending", state)
        self.assertEqual(self.work("unload", "wan_prompt"), [])
        self.sidecar_queue = None
        self.assertEqual(self.generate("i2v", "e1" * 16)["state"], "succeeded")

    def test_malformed_wan_queue_refuses_with_baseline_error_and_no_hold(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.queue = {"queue_pending": []}
        job = self.generate("i2v")
        self.assertEqual((job["error"], job["admission_code"]), (
            "ValueError: Malformed ComfyUI /queue response; backend work is unknown", "engine_busy",
        ))
        self.assertFalse(api.UNSAFE)
        self.assertTrue(self.lane.read_state()["resident"])

    def test_foley_foreign_work_records_the_baseline_refusal_and_does_not_latch(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        foley = product_foley.service
        job = {"id": "e2" * 16, "type": "foley", "api_version": product.VERSION, "state": "queued"}
        def request(port, *args):
            if port == 8080:
                return {"accepting_jobs": True, "active_job": None}
            return {**EMPTY, "queue_running": [[0, "foreign"]]}
        with patch.object(product_foley, "CONFIG", self.config), patch.multiple(
                foley, ROOT=self.root, request=request, ACTIVE=job["id"]):
            with patch.object(foley, "generate") as engine:
                product_foley.generate(job, api.acquire_compute(), engine)
                engine.assert_not_called()
            self.assertIsNone(foley.ACTIVE)
            self.assertEqual(product.read_job(foley.job_path(job["id"]))["admission_code"], "engine_busy")
        self.assertEqual(job["error"], "RuntimeError: Unknown engine work; preserved")
        self.assertTrue(self.lane.read_state()["resident"])
        self.assertFalse(api.UNSAFE)
        self.assertEqual(self.work("unload"), [])
        with api.acquire_compute() as lease:
            self.assertIsNotNone(lease)

    def test_foley_codes_apply_only_to_refusals_before_inference(self):
        error = "RuntimeError: Unknown engine work; preserved"
        job = {"id": "e2" * 16, "type": "foley", "api_version": product.VERSION, "state": "failed", "error": error}
        after = {**job, "started_at": 0}
        with patch.object(product_foley.service, "ROOT", self.root):
            save = product_foley.service.save
            product_foley.save_job(job, save)
            self.assertEqual(product.read_job(product_foley.service.job_path(job["id"]))["admission_code"], "engine_busy")
            product_foley.save_job(after, save)
            self.assertNotIn("admission_code", product.read_job(product_foley.service.job_path(job["id"])))

    def test_wan_codes_apply_only_before_prompt_submission(self):
        with api.acquire_compute():
            job = self.generate("i2v")
        self.assertEqual(job["admission_code"], "lease_held")
        with patch.object(api, "gpus_idle", return_value=False):
            self.assertEqual(self.generate("i2v", "e1" * 16)["admission_code"], "gpu_busy")
        self.card_temperature = 62
        self.assertEqual(self.generate("i2v", "e2" * 16)["admission_code"], "gpu_warm")
        self.card_temperature = 50
        error = product.AdmissionRefusal("engine_busy", "After submission")
        with patch.object(api, "submit_product_prompt", side_effect=error):
            job = self.generate("i2v", "e3" * 16)
        self.assertEqual(job["state"], "failed")
        self.assertNotIn("admission_code", job)

    def test_typed_text_and_vision_replay_and_staged_png(self):
        digest = hashlib.sha256(PNG).hexdigest()
        with server(api.Handler) as url:
            self.assertEqual(call(url, local.PROFILE_ROUTE)[2]["profile"], PROFILE)
            self.assertEqual(self.submit(url, LOAD, "load")[0], 202)
            self.assertTrue(self.http_done.wait(5))
            self.assertEqual(call(url, endpoint(LOAD))[2]["state"], "succeeded")
            self.assertEqual(self.submit(url, LOAD, "unload")[2]["code"], "id_conflict")
            self.assertEqual(self.submit(url, LOAD, "load")[0], 200)
            self.assertEqual(self.submit(url, INFER, "infer", image_sha256=digest)[2]["code"], "input_missing")
            self.assertEqual(call(url, "/v1/inputs/" + digest + ".png", method="PUT", body=PNG)[0], 201)
            self.http_done.clear()
            self.assertEqual(self.submit(url, INFER, "infer", image_sha256=digest, max_output_tokens=5)[0], 202)
            self.assertTrue(self.http_done.wait(5))
            status = call(url, endpoint(INFER))[2]
            self.assertEqual(status["result"], {"text": self.text, "input_tokens": 8, "output_tokens": 3})
            self.assertNotIn("prompt", status)
            self.assertEqual(self.submit(url, INFER, "infer", image_sha256=digest, max_output_tokens=5)[0], 200)
            self.assertEqual(self.submit(url, INFER, "infer", image_sha256=digest)[2]["code"], "id_conflict")
            self.http_done.clear()
            self.assertEqual(self.submit(url, UNLOAD, "unload")[0], 202)
            self.assertTrue(self.http_done.wait(5))
            self.assertEqual(call(url, endpoint(UNLOAD))[2]["state"], "succeeded")
        self.assertEqual(self.perform("04" * 16, "infer")["error"], "model_not_loaded")
        self.assertEqual(self.work("load", "infer", "unload"), [
            ("load", LOAD, "running", False), ("infer", INFER, "running", False), ("unload", UNLOAD, "running", False),
        ])
        self.assertEqual(self.work("prepare", "token_cap", "deadline"), [
            ("deadline", 4), ("deadline", 5), ("prepare", "Describe the board", [PNG]), ("token_cap", 5), ("deadline", 6),
        ])

    def test_invalid_fields_and_tampered_staging_stop_before_dispatch(self):
        with server(api.Handler) as url:
            for changes in (
                {"operation": ["load"]}, {"operation": "shell"}, {"model_path": "/tmp/model"},
                {"prompt": "\ud800"}, {"prompt": "x" * 4097}, {"prompt": "  "},
                {"max_output_tokens": True}, {"max_output_tokens": 13},
                {"image_sha256": "../image.png"}, {"profile": "other"},
            ):
                with self.subTest(changes=changes):
                    body = {**self.body("infer"), **changes}
                    self.assertEqual(call(url, endpoint(INFER), method="PUT", body=body)[0], 400)
            self.assertEqual(call(url, endpoint(INFER), method="PUT", body=b" " * 65537)[0], 400)
            digest = hashlib.sha256(PNG).hexdigest()
            self.assertEqual(call(url, "/v1/inputs/" + digest + ".png", method="PUT", body=PNG)[0], 201)
            product.input_path(self.root, digest, "png").write_bytes(PNG + b"changed")
            self.assertEqual(self.submit(url, INFER, "infer", image_sha256=digest)[2]["code"], "input_conflict")
            malformed = b"not PNG"
            digest = hashlib.sha256(malformed).hexdigest()
            product.input_path(self.root, digest, "png").write_bytes(malformed)
            self.assertEqual(self.submit(url, INFER, "infer", image_sha256=digest)[2]["code"], "invalid_input")
            with product.input_path(self.root, digest, "png").open("wb") as output:
                output.truncate(product.MAX_UPLOAD + 1)
            self.assertEqual(self.submit(url, INFER, "infer", image_sha256=digest)[2]["code"], "invalid_input")
        self.assertIsNone(self.lane.read(INFER))
        self.assertEqual(self.work("load", "infer", "unload"), [])

    def stage(self, png):
        digest = hashlib.sha256(png).hexdigest()
        path = product.input_path(self.root, digest, "png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        return digest

    def test_ordered_frames_match_result_order_and_bind_the_request(self):
        second = IHDR + png_chunk(b"tEXt", b"fixture\0second") + png_chunk(b"IDAT", PIXEL) + png_chunk(b"IEND")
        digests = [self.stage(second), self.stage(PNG)]
        self.text = json.dumps({"frames": [
            {"face": "hidden", "code": "B24", "wardrobe": "match"},
            {"face": "visible", "code": None, "wardrobe": "uncertain"},
        ]})
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        result = self.perform(INFER, "infer", image_sha256s=digests, mode="frame_check_v1")
        self.assertEqual(result["image_sha256s"], digests)
        self.assertEqual(result["mode"], "frame_check_v1")
        self.assertEqual(result["result"]["text"], self.text)
        self.assertEqual(self.work("prepare", "mode"), [
            ("prepare", "Describe the board", [second, PNG]), ("mode", modes.MODES["frame_check_v1"]),
        ])
        with server(api.Handler) as url:
            self.assertEqual(self.submit(url, INFER, "infer", image_sha256s=digests, mode="frame_check_v1")[0], 200)
            self.assertEqual(self.submit(url, INFER, "infer", image_sha256s=digests[::-1], mode="frame_check_v1")[0], 409)
            self.assertEqual(self.submit(url, INFER, "infer", image_sha256s=digests, mode="plan_v1")[0], 409)

    def test_existing_inference_fingerprint_replays_with_mode_omitted(self):
        request = self.body("infer", image_sha256="a" * 64)
        self.lane.create(INFER, request)
        valid = local.validate(PROFILE, request)
        self.assertNotIn("mode", valid)
        self.assertEqual(api.submit_local_request(self.lane, INFER, valid)[0], 200)
        explicit = local.validate(PROFILE, {**request, "mode": "text"})
        self.assertEqual(api.submit_local_request(self.lane, INFER, explicit)[0], 200)

    def test_multiframe_contract_rejects_ambiguous_or_caller_owned_inputs(self):
        digest = "a" * 64
        with server(api.Handler) as url:
            for fields in ({"image_sha256s": [digest] * 5}, {"image_sha256s": []}, {"image_sha256s": digest},
                           {"image_sha256s": [digest, "B" * 64]}, {"image_sha256s": [digest], "image_sha256": digest},
                           {"mode": "guess"}, {"mode": []}, {"mode": "frame_check_v1"},
                           {"schema": {}}, {"grammar": ""}, {"tools": []}, {"image_url": "http://example.test"}):
                with self.subTest(fields=fields):
                    self.assertEqual(self.submit(url, INFER, "infer", **fields)[2]["code"], "invalid_request")
        self.assertIsNone(self.lane.read(INFER))

    def test_each_ordered_frame_is_checked_again_before_backend_prepare(self):
        first = self.stage(PNG)
        second = IHDR + png_chunk(b"tEXt", b"fixture\0second") + png_chunk(b"IDAT", PIXEL) + png_chunk(b"IEND")
        second_digest = self.stage(second)
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        original = local.exchange

        def replace_staged(root, message, *args):
            if message["action"] == "run":
                product.input_path(self.root, second_digest, "png").write_bytes(PNG)
            return original(root, message, *args)

        with patch.object(local, "exchange", side_effect=replace_staged), server(api.Handler) as url:
            self.assertEqual(self.submit(url, INFER, "infer", image_sha256s=[first, second_digest])[0], 202)
            self.assertTrue(self.http_done.wait(5))
            self.assertEqual(call(url, endpoint(INFER))[2]["error"], "backend_failed")
        self.assertEqual(self.work("prepare", "infer"), [])

    def test_multiframe_envelope_checks_size_dimensions_png_and_staging(self):
        digest = self.stage(PNG)
        missing = "a" * 64
        with server(api.Handler) as url:
            self.assertEqual(self.submit(url, INFER, "infer", image_sha256s=[digest, missing])[2]["code"], "input_missing")
            p = product.input_path(self.root, digest, "png")
            with p.open("r+b") as output:
                output.truncate(local.MAX_IMAGE_BYTES + 1)
            self.assertEqual(self.submit(url, INFER, "infer", image_sha256s=[digest])[2]["code"], "invalid_input")
            malformed = self.stage(b"malformed")
            self.assertEqual(self.submit(url, INFER, "infer", image_sha256s=[malformed])[2]["code"], "invalid_input")
            wide = bytearray(PNG)
            wide[16:20] = (1921).to_bytes(4, "big")
            big = self.stage(wide)
            self.assertEqual(self.submit(url, INFER, "infer", image_sha256s=[big])[2]["code"], "invalid_input")
            self.assertEqual(self.submit(url, INFER, "infer", image_sha256=big)[0], 202)
            self.assertTrue(self.http_done.wait(5))

    def test_full_context_and_per_image_limit_stop_before_encoding(self):
        first = self.stage(PNG)
        images = [first] * 4
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.inputs, self.visual = 53, [7, 8, 9, 10]
        job = self.perform(INFER, "infer", image_sha256s=images)
        self.assertEqual((job["error"], job["admission_code"]), ("context_limit", "pre_admission_failed"))
        self.assertEqual(self.work("infer"), [])
        self.inputs, self.visual = 1040, [1, 2, 3, 1025]
        with self.assertRaisesRegex(ValueError, "Invalid input token count"):
            sidecar.infer(self.worker.model.backend, self.root, {**PROFILE, "context_tokens": 2048},
                          self.body("infer", image_sha256s=images))
        self.assertEqual(self.work("infer"), [])

    def test_backend_reports_full_visual_token_accounting(self):
        digest = self.stage(PNG)
        fake = self.worker.model.backend
        self.visual = []
        request = self.body("infer", image_sha256s=[digest])
        with self.assertRaisesRegex(ValueError, "Invalid input token count"):
            sidecar.infer(fake, self.root, PROFILE, request)
        self.visual = [self.inputs + 1]
        with self.assertRaisesRegex(ValueError, "Invalid input token count"):
            sidecar.infer(fake, self.root, PROFILE, request)
        self.assertEqual(self.work("infer"), [])

    def test_structured_result_schemas_and_tool_arguments_are_checked(self):
        direction = "Use a closeup."
        frame = {"face": "visible", "code": "A17", "wardrobe": "match"}
        plan = {"scene_id": "scene1", "shots": [{"technical_direction": direction}]}
        args = {"scene_id": "scene1", "shot_count": 1, "technical_direction": direction}
        tool = {"name": "record_plan_v1", "arguments": args}
        result = {"input_tokens": 8, "output_tokens": 3}
        cases = [
            ("frame_check_v1", {"frames": [frame]}, True),
            ("frame_check_v1", {"frames": [frame, frame]}, False),
            ("frame_check_v1", {"frames": [{**frame, "face": "maybe"}]}, False),
            ("plan_v1", plan, True),
            ("plan_v1", {"scene_id": "scene1", "shots": []}, False),
            ("plan_v1", {**plan, "extra": 1}, False),
            ("plan_tool_v1", tool, True),
            ("plan_tool_v1", [tool, tool], False),
            ("plan_tool_v1", {**tool, "name": "write_file"}, False),
            ("plan_tool_v1", {**tool, "arguments": {**args, "shot_count": True}}, False),
            ("plan_tool_v1", {**tool, "arguments": {**args, "shot_count": 13}}, False),
            ("plan_tool_v1", {**tool, "arguments": {**args, "path": "/tmp/x"}}, False),
        ]
        for mode, value, expected in cases:
            with self.subTest(mode=mode, value=value):
                self.assertIs(local.valid_result_mode({"mode": mode, "image_sha256s": ["a" * 64]},
                                                 {**result, "text": json.dumps(value)}), expected)
        duplicate = '{"scene_id":"","scene_id":"scene1","shots":[{"technical_direction":"x"}]}'
        self.assertFalse(local.valid_result_mode({"mode": "plan_v1"}, {**result, "text": duplicate}))
        self.assertFalse(local.valid_result_mode({"mode": "plan_v1"}, {**result, "text": "{"}))

    def test_structured_receipt_is_revalidated_before_publication(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        snapshot_status = self.worker.status
        self.text = '{"scene_id":"scene1","shots":[{"technical_direction":"Closeup."}]}'

        def changed(**kwargs):
            snapshot = snapshot_status(**kwargs)
            receipt = snapshot.get("receipt")
            if receipt and receipt["operation"] == "infer":
                return {**snapshot, "receipt": {**receipt, "result": {**receipt["result"], "text": "{}"}}}
            return snapshot

        with patch.object(self.worker, "status", side_effect=changed):
            job = self.perform(INFER, "infer", mode="plan_v1")
        self.assertEqual(job["error"], "invalid_result")
        self.assertNotIn("result", job)
        self.assertTrue(self.lane.read_state()["resident"])

    def test_structured_inference_rejects_incomplete_and_invalid_output_without_a_hold(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.text = '{"scene_id":"scene1","shots":[{"technical_direction":"Closeup."}]}'
        self.complete = False
        job = self.perform(INFER, "infer", mode="plan_v1")
        self.assertEqual(job["error"], "invalid_result")
        self.assertEqual(job["reason_code"], "incomplete_output")
        self.assertNotIn("admission_code", job)
        self.complete = True
        valid_text = self.text
        self.text = "{}"
        bad = self.perform(UNLOAD, "infer", mode="plan_v1")
        self.assertEqual((bad["error"], bad["reason_code"]), ("invalid_result", "mode_mismatch"))
        self.text = valid_text
        self.assertEqual(self.perform("04" * 16, "infer", mode="plan_v1")["result"]["text"], valid_text)
        self.assertEqual(self.perform("05" * 16, "unload")["released_memory_mib"], 64)

    def test_context_cap_counts_prepared_input_and_bounds_backend_output(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.inputs = 53
        self.assertEqual(self.perform(INFER, "infer")["error"], "context_limit")
        self.assertEqual(self.work("prepare"), [("prepare", "Describe the board", [])])
        self.assertEqual(self.work("infer"), [])
        self.inputs, self.outputs = 8, 13
        self.assertEqual(self.perform("04" * 16, "infer")["error"], "output_limit")
        self.assertEqual(self.work("token_cap"), [("token_cap", 12)])
        self.assertFalse(self.lane.path.exists())

    def test_output_byte_budget_is_measured_in_utf8(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.text = "€" * (local.MAX_TEXT_BYTES // 3 + 1)
        self.assertEqual(self.perform(INFER, "infer")["error"], "output_limit")
        self.assertEqual(self.work("infer"), [("infer", INFER, "running", False)])
        self.assertFalse(self.lane.path.exists())

    def test_output_limits_reap_and_leave_lane_usable_after_fresh_proof(self):
        for offset, (text, tokens) in enumerate((
            ('{"scene_id":"s1","shots":[{"technical_direction":"Closeup."}]}', 13),
            ("€" * (local.MAX_TEXT_BYTES // 3 + 1), 3),
            ("\x01" * local.MAX_TEXT_BYTES, 3),
        )):
            with self.subTest(offset=offset):
                self.assertEqual(self.perform(f"{offset + 10:032x}", "load")["state"], "succeeded")
                self.text, self.outputs = text, tokens
                job = self.perform(f"{offset + 20:032x}", "infer", mode="plan_v1")
                self.assertEqual(job["error"], "output_limit")
                self.assertEqual(job["released_memory_mib"], 64)
                self.assertFalse(self.worker.model.alive())
                self.assertFalse(self.lane.path.exists())
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.assertEqual(self.perform(UNLOAD, "unload")["state"], "succeeded")

    def test_unexpected_supervisor_failure_reaps_and_records_release(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        with patch.object(self.worker.model, "execute", side_effect=RuntimeError("Unexpected supervisor error")):
            job = self.perform(INFER, "infer")
        self.assertEqual((job["error"], job["released_memory_mib"]), ("backend_failed", 64))
        self.assertFalse(self.worker.model.alive())
        self.assertFalse(self.lane.path.exists())

    def test_output_limit_with_memory_still_allocated_holds(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.free_on_unload = False
        self.outputs = PROFILE["max_output_tokens"] + 1
        with patch.object(local.time, "sleep"):
            self.assertEqual(self.perform(INFER, "infer")["error"], "residency_unverified")
        self.assertTrue(product.read_job(self.lane.path)["held"])

    def test_structured_receipt_bound_violation_cannot_downgrade_to_invalid_result(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.text = '{"scene_id":"scene1","shots":[{"technical_direction":"Closeup."}]}'
        snapshot_status = self.worker.status

        def changed(**kwargs):
            snapshot = snapshot_status(**kwargs)
            receipt = snapshot.get("receipt")
            if receipt and receipt["operation"] == "infer":
                return {**snapshot, "receipt": {**receipt, "result": {**receipt["result"], "output_tokens": 13}}}
            return snapshot

        with patch.object(self.worker, "status", side_effect=changed):
            job = self.perform(INFER, "infer", mode="plan_v1")
        self.assertEqual(job["error"], "backend_failed")
        self.assertNotIn("result", job)
        self.assertTrue(product.read_job(self.lane.path)["held"])

    def test_one_card_wan_on_other_card_keeps_residency(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.assertEqual(self.generate("s2v")["state"], "succeeded")
        self.assertEqual(self.work("unload", "wan_prompt"), [("wan_prompt", 5000)])
        self.assertEqual(self.perform(INFER, "infer")["result"]["text"], self.text)

    def test_dual_card_wan_unloads_and_samples_before_submit(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.events.clear()
        self.assertEqual(self.generate("i2v")["state"], "succeeded")
        unload, = self.work("unload")
        self.assertEqual(unload[2:], ("running", False))
        dispatch = self.events.index(("wan_prompt", 64))
        self.assertIn(("memory", CARD, 64), self.events[self.events.index(unload) + 1:dispatch])
        self.assertFalse(self.lane.path.exists())
        self.assertEqual(len(self.prompts), 1)

    def test_one_card_wan_on_model_card_unloads_first(self):
        profile = {**PROFILE, "s2v_gpu_uuid": CARD}
        self.config.joinpath("local-model.json").write_text(json.dumps(profile))
        self.worker = self.new_sidecar(profile)
        self.lane = local.Lane(self.root, profile)
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.assertEqual(self.generate("s2v")["state"], "succeeded")
        self.assertEqual([event[0] for event in self.work("unload", "wan_prompt")], ["unload", "wan_prompt"])

    def test_unverified_s2v_placement_and_legacy_reserve_both_cards(self):
        profile = {key: value for key, value in PROFILE.items() if key != "s2v_gpu_uuid"}
        self.config.joinpath("local-model.json").write_text(json.dumps(profile))
        self.worker = self.new_sidecar(profile)
        self.lane = local.Lane(self.root, profile)
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.assertEqual(self.generate("s2v")["state"], "succeeded")
        self.assertEqual(self.perform(INFER, "load")["state"], "succeeded")
        self.assertEqual(self.generate(None, "a2" * 16)["state"], "succeeded")
        self.assertEqual([event[0] for event in self.work("unload", "wan_prompt")],
                         ["unload", "wan_prompt", "unload", "wan_prompt"])

    def test_unload_without_memory_release_holds_wan_and_survives_restart(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.free_on_unload = False
        with patch.object(local.time, "sleep"):
            job = self.generate("i2v")
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["admission_code"], "local_model_handover")
        self.assertEqual(job["error"], "RuntimeError: Rack GPUs are busy; refusing to start a competing job")
        self.assertEqual(len(self.work("unload")), 1)
        self.assertEqual(self.prompts, [])
        self.assertTrue(product.read_job(self.lane.path)["held"])
        api.recover_with_local()
        with server(api.Handler) as url:
            self.assertEqual(self.submit(url, INFER, "infer")[2]["code"], "local_model_held")
            self.assertEqual(self.submit(url, LOAD, "load")[0], 200)
        self.config.joinpath("local-model.json").unlink()
        self.assertEqual(self.generate(None, "a2" * 16)["state"], "failed")
        self.assertEqual(self.prompts, [])

    def test_inference_while_wan_owns_real_lease_and_while_held(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        with server(api.Handler) as url, api.acquire_compute():
            self.assertEqual(self.submit(url, INFER, "infer")[2]["code"], "worker_busy")
        api.UNSAFE = True
        with server(api.Handler) as url:
            self.assertEqual(self.submit(url, INFER, "infer")[2]["code"], "worker_busy")
            self.assertEqual(self.submit(url, UNLOAD, "unload")[2]["code"], "worker_busy")
        self.assertEqual(self.work("infer", "unload"), [])
        self.assertIsNone(self.lane.read(INFER))

    def test_unknown_engine_work_is_preserved_before_any_local_mutation(self):
        for queue in ({"queue_pending": []}, {**EMPTY, "queue_running": [[0, "foreign"]]}):
            with self.subTest(queue=queue):
                self.queue = queue
                api.UNSAFE = False
                self.engine_calls.clear()
                job_id = "a" * 32 if "queue_running" not in queue else "b" * 32
                self.assertEqual(self.perform(job_id, "load")["error"], "worker_busy")
                self.assertEqual(api.UNSAFE, "queue_running" not in queue)
                self.assertEqual(self.engine_calls, [("/queue", None)])
        self.assertEqual(self.work("load", "unload", "infer"), [])

    def test_transient_queue_read_refuses_request_without_latching_hold(self):
        for job_id, error in ((LOAD, TimeoutError()), (INFER, HTTPException())):
            with self.subTest(error=type(error).__name__), patch.object(api, "engine", side_effect=error) as engine:
                self.assertEqual(self.perform(job_id, "load")["error"], "worker_busy")
                self.assertFalse(api.UNSAFE)
                engine.assert_called_once_with("/queue")
        self.assertEqual(self.work("load"), [])
        self.assertFalse(self.lane.path.exists())
        self.assertEqual(self.perform(UNLOAD, "load")["state"], "succeeded")

    def test_transient_receiver_queue_read_does_not_create_residency_hold(self):
        self.sidecar_queue = TimeoutError()
        self.assertEqual(self.perform(LOAD, "load")["error"], "engine_queue_unavailable")
        self.assertEqual(self.work("receiver_queue"), [("receiver_queue", self.sidecar_queue)])
        self.assertEqual(self.work("load"), [])
        self.assertFalse(self.lane.path.exists())
        self.assertFalse(api.UNSAFE)
        self.sidecar_queue = None
        self.assertEqual(self.perform(UNLOAD, "load")["state"], "succeeded")

    def test_local_foreign_work_race_refuses_without_a_hold(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.sidecar_queue = {**EMPTY, "queue_running": [[0, "late-foreign"]]}
        job = self.perform(INFER, "infer")
        self.assertEqual((job["error"], job["admission_code"]), ("engine_work_unknown", "engine_busy"))
        self.assertEqual(self.work("infer"), [])
        self.assertNotIn("pending", self.lane.read_state())
        self.assertTrue(self.lane.read_state()["resident"])
        self.assertFalse(api.UNSAFE)
        self.assertEqual(self.prompts, [])
        self.sidecar_queue = None
        self.assertEqual(self.perform(UNLOAD, "unload")["state"], "succeeded")

    def test_malformed_receiver_queue_still_holds_direct_local_request(self):
        self.sidecar_queue = {"queue_pending": []}
        self.assertEqual(self.perform(LOAD, "load")["error"], "engine_work_unknown")
        self.assertEqual(self.work("load"), [])
        self.assertTrue(product.read_job(self.lane.path)["held"])

    def test_local_card_identity_temperature_and_resident_memory_are_checked(self):
        self.report_card = OTHER
        self.assertEqual(self.perform(LOAD, "load")["error"], "local_model_unavailable")
        self.report_card = CARD
        self.card_temperature = 62
        self.assertEqual(self.perform(INFER, "load")["error"], "model_card_hot")
        self.assertEqual(self.work("load"), [])
        self.card_temperature = 50
        self.load_memory = 3999
        with patch.object(local.time, "sleep"):
            self.assertEqual(self.perform(UNLOAD, "load")["error"], "residency_unverified")
        self.assertEqual(len(self.work("load")), 1)
        self.assertTrue(product.read_job(self.lane.path)["held"])

    def test_live_child_without_state_holds_wan_and_foley_even_with_low_memory(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.lane.path.unlink()
        # An idle GPU reading cannot override proof of the model process.
        self.memory = 64
        job = self.generate("s2v")
        self.assertEqual(job.get("admission_code"), "local_model_handover")
        self.assertEqual(self.prompts, [])
        self.assertTrue(product.read_job(self.lane.path)["held"])
        self.lane.path.unlink()
        with patch.object(product_foley, "CONFIG", self.config), \
                patch.multiple(product_foley.service, ROOT=self.root, idle=api.check_local_queue), \
                api.acquire_compute() as lease:
            with self.assertRaises(product.AdmissionRefusal) as refusal:
                product_foley.prepare_foley(lease)
            self.assertEqual(refusal.exception.code, "local_model_handover")
        self.assertTrue(product.read_job(self.lane.path)["held"])

    def test_stale_get_accepts_cache_and_refuses_sidecar_outage_without_a_hold(self):
        request = self.body("load")
        with api.acquire_compute():
            job = self.lane.create(LOAD, request)
            job.update(state="running")
            local.write_record(local.job_path(self.root, LOAD), job)
        self.memory = 5000
        with server(api.Handler) as url:
            with patch.object(local, "exchange", side_effect=ConnectionRefusedError):
                status, _, response = call(url, endpoint(LOAD))
                self.assertEqual((status, response["code"]), (503, "local_model_unavailable"))
            status, _, response = call(url, endpoint(LOAD))
        self.assertEqual((status, response["error"]), (200, "submission_lost"))
        self.assertEqual(self.memory, 5000)
        self.assertFalse(self.lane.path.exists())
        self.assertEqual(self.work("free_cache", "load"), [])

    def test_load_evicts_idle_cache_and_releases_to_its_drained_baseline(self):
        self.memory, self.drained_memory = 5600, 96
        loaded = self.perform(LOAD, "load")
        self.assertEqual((loaded["state"], loaded["baseline_memory_mib"]), ("succeeded", 96))
        self.assertEqual(self.lane.read_state()["baseline_memory_mib"], 96)
        self.assertEqual(self.baseline_at_load, 96)
        evict = self.events.index(("free_cache", 5600))
        load = self.events.index(("load", LOAD, "running", False))
        self.assertIn(("memory", CARD, 96), self.events[evict + 1:load])
        released = self.perform(UNLOAD, "unload")
        self.assertEqual((released["state"], released["released_memory_mib"]), ("succeeded", 96))
        self.assertEqual(released["baseline_memory_mib"], 96)
        self.assertFalse(self.lane.path.exists())

    def test_unload_with_two_or_four_mib_drift_passes_at_calibrated_ceiling(self):
        # PROFILE's ceiling is 128 MiB for a drained baseline of 124 MiB.
        for drift in (2, 4):
            with self.subTest(drift=drift):
                self.memory = self.drained_memory = 124
                loaded = self.perform(f"{10 + drift:032x}", "load")
                self.assertEqual((loaded["state"], loaded["baseline_memory_mib"]), ("succeeded", 124))
                self.drained_memory = 124 + drift
                with patch.object(local.time, "sleep"):
                    released = self.perform(f"{20 + drift:032x}", "unload")
                self.assertEqual((released["state"], released["released_memory_mib"]), ("succeeded", 124 + drift))
                self.assertFalse(self.lane.path.exists())

    def test_unload_with_five_mib_drift_latches_hold_at_calibrated_ceiling(self):
        self.memory = self.drained_memory = 124
        loaded = self.perform(LOAD, "load")
        self.assertEqual((loaded["state"], loaded["baseline_memory_mib"]), ("succeeded", 124))
        self.drained_memory = 129
        with patch.object(local.time, "sleep"):
            released = self.perform(UNLOAD, "unload")
        self.assertEqual(released["error"], "residency_unverified")
        self.assertFalse(self.worker.model.alive())
        self.assertTrue(product.read_job(self.lane.path)["held"])

    def test_load_rechecks_queue_before_evicting_cache_and_after_post(self):
        self.memory = 5600
        foreign = {**EMPTY, "queue_running": [[0, "late-foreign"]]}
        for index, observations in enumerate(((EMPTY, foreign), (EMPTY, EMPTY, foreign))):
            with self.subTest(index=index), patch.object(self.worker, "check_idle") as check:
                check.side_effect = [
                    None if queue is EMPTY else local.UnknownEngineWork("Foreign work", engine_busy=True)
                    for queue in observations
                ]
                job = self.perform(f"{index + 10:032x}", "load")
                self.assertEqual((job["error"], job["admission_code"]), ("engine_work_unknown", "engine_busy"))
                self.assertFalse(self.lane.path.exists())
                self.assertEqual(len(self.work("free_cache")), index)
        self.assertEqual(self.work("load"), [])

    def test_load_refuses_undrained_card_at_profile_ceiling_without_a_hold(self):
        self.memory = 5600
        self.drained_memory = PROFILE["unloaded_max_mib"] + 1
        # The first sample after eviction exceeds the configured cap.
        with patch.object(sidecar.time, "monotonic", side_effect=[0, 0, PROFILE["load_timeout_seconds"]]), \
                patch.object(sidecar.time, "sleep"):
            job = self.perform(LOAD, "load")
        self.assertEqual((job["error"], job["admission_code"]), ("model_card_busy", "gpu_busy"))
        self.assertEqual(self.work("load"), [])
        self.assertFalse(self.lane.path.exists())

    def test_invalid_baseline_refuses_handover_before_dispatch(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.lane.write_state(self.worker.instance, True, baseline=False)
        self.assertEqual(self.generate("i2v")["admission_code"], "local_model_handover")
        self.assertEqual(self.work("unload", "wan_prompt"), [])

    def test_changed_profile_cannot_discard_a_resident_reservation(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.config.joinpath("local-model.json").write_text(json.dumps({**PROFILE, "max_output_tokens": 10}))
        with server(api.Handler) as url:
            self.assertEqual(self.submit(url, INFER, "infer")[2]["code"], "local_model_held")
        self.assertEqual(self.work("infer", "unload"), [])
        self.assertEqual(self.lane.read_state()["instance"], self.worker.instance)

    def test_api_restarts_with_resident_model_and_keeps_original_epoch(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        api.recover_with_local()
        self.assertEqual(self.lane.read_state()["instance"], self.worker.instance)
        self.assertEqual(self.perform(INFER, "infer")["state"], "succeeded")
        self.assertEqual(len(self.work("load")), 1)

    def test_dead_sidecar_residency_requires_fresh_empty_memory(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.worker = self.new_sidecar()
        self.assertEqual(self.generate("s2v")["state"], "failed")
        self.assertEqual(self.prompts, [])
        self.assertTrue(product.read_job(self.lane.path)["held"])

    def test_unreachable_resident_sidecar_refuses_wan_with_state_intact(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        with patch.object(local, "exchange", side_effect=ConnectionRefusedError):
            self.assertEqual(self.generate("s2v")["state"], "failed")
        self.assertTrue(self.lane.read_state()["resident"])
        self.assertEqual(self.prompts, [])

    def test_new_sidecar_epoch_and_drained_card_reconcile_old_residency(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.worker = self.new_sidecar()
        self.memory = 64
        api.recover_with_local()
        self.assertFalse(self.lane.path.exists())
        self.assertEqual(self.generate("i2v")["state"], "succeeded")
        self.assertEqual(self.work("unload"), [])

    def test_api_restart_during_inference_keeps_lease_and_recovers_result(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        request = self.body("infer")
        lease = api.acquire_compute()
        with lease, socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as conn:
            job = self.lane.create(INFER, request)
            job.update(state="running", started_at=0)
            local.write_record(local.job_path(self.root, INFER), job)
            self.lane.write_state(self.worker.instance, True, pending=INFER, baseline=64)
            self.block_infer = True
            conn.connect(str(local.socket_path(self.root)))
            local.send_packet(conn, {"action": "run", "id": INFER, "request": request}, (lease.fileno(),))
            self.assertTrue(self.infer_entered.wait(5))
        # API died or lost the connection. Only the sidecar's copy remains.
        competing = api.acquire_compute()
        if competing:
            competing.close()
        self.assertIsNone(competing)
        self.assertEqual(api.get_local_request(self.lane, INFER)["state"], "running")
        self.engine_calls.clear()
        waiting = threading.Event()

        def wait(_):
            waiting.set()
            self.infer_connection_closed.wait(5)

        clock = SimpleNamespace(sleep=wait, monotonic=time.monotonic, time=time.time)
        with patch.object(api, "time", clock):
            restart = threading.Thread(target=api.recover_with_local)
            restart.start()
            try:
                self.assertTrue(waiting.wait(5))
                self.assertEqual(self.engine_calls, [])
            finally:
                self.release_infer.set()
                restart.join(timeout=6)
        self.assertFalse(restart.is_alive())
        self.assertEqual(api.get_local_request(self.lane, INFER)["result"]["text"], self.text)
        self.assertEqual(api.submit_local_request(self.lane, INFER, request)[0], 200)
        self.assertEqual(self.work("infer"), [("infer", INFER, "running", False)])

    def test_queued_request_left_by_restart_never_dispatches(self):
        request = self.body("load")
        with api.acquire_compute():
            self.lane.create(LOAD, request)
        self.assertEqual(api.get_local_request(self.lane, LOAD)["error"], "submission_lost")
        self.assertEqual(api.submit_local_request(self.lane, LOAD, request)[0], 200)
        self.assertEqual(self.work("load"), [])

    def test_transport_loss_without_dispatch_is_settled_without_replay(self):
        original = local.exchange

        def lost(root, data, *args):
            if data["action"] == "run":
                self.events.append(("dropped", data["id"]))
                raise TimeoutError
            return original(root, data, *args)

        with patch.object(local, "exchange", lost):
            self.assertEqual(self.perform(LOAD, "load")["state"], "running")
        self.assertEqual(api.get_local_request(self.lane, LOAD)["error"], "submission_lost")
        self.assertEqual(self.work("dropped", "load"), [("dropped", LOAD)])
        self.assertEqual(api.submit_local_request(self.lane, LOAD, self.body("load"))[0], 200)
        self.assertFalse(self.lane.path.exists())

    def test_restart_after_inference_killed_fails_pending_id_without_replay(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        request = self.body("infer")
        with api.acquire_compute():
            job = self.lane.create(INFER, request)
            job.update(state="running", started_at=0)
            local.write_record(local.job_path(self.root, INFER), job)
            self.lane.write_state(self.worker.instance, True, pending=INFER, baseline=64)
        self.worker = self.new_sidecar()
        self.memory = 64
        api.recover_with_local()
        self.assertEqual(api.get_local_request(self.lane, INFER)["error"], "sidecar_restarted")
        self.assertEqual(api.submit_local_request(self.lane, INFER, request)[0], 200)
        self.assertEqual(self.work("infer"), [])
        self.assertFalse(self.lane.path.exists())

    def test_sidecar_requires_held_fd_and_matching_durable_request(self):
        request = self.body("load")
        with api.acquire_compute() as lease:
            job = self.lane.create(LOAD, request)
            job.update(state="running")
            local.write_record(local.job_path(self.root, LOAD), job)
            self.lane.write_state(self.worker.instance, False, pending=LOAD)
            message = {"action": "run", "id": LOAD, "request": request}
            with patch.object(self.worker, "instance", "b" * 32), self.assertRaises(ValueError):
                local.exchange(self.root, message, lease)
            with self.assertRaises(ValueError):
                local.exchange(self.root, {**message, "request": self.body("unload")}, lease)
            with (self.root / "not-the-lease").open("a") as wrong_inode, self.assertRaises(ValueError):
                local.exchange(self.root, message, wrong_inode)
            with (self.root / "compute.lock").open("rb") as unrelated, self.assertRaises(ValueError):
                local.exchange(self.root, message, unrelated)
        with (self.root / "compute.lock").open("rb") as unlocked, self.assertRaises(ValueError):
            local.exchange(self.root, message, unlocked)
        self.assertEqual(self.work("load", "unload"), [])

    def test_private_receiver_rejects_truncated_packet_and_extra_fds(self):
        request = self.body("load")
        with api.acquire_compute() as lease:
            job = self.lane.create(LOAD, request)
            job.update(state="running")
            local.write_record(local.job_path(self.root, LOAD), job)
            self.lane.write_state(self.worker.instance, False, pending=LOAD)
            payload = json.dumps({"action": "run", "id": LOAD, "request": request}).encode()
            fd = lease.fileno()
            for packet, fds in ((payload + b" " * local.MAX_PACKET, [fd]), (payload, [fd, fd])):
                with self.subTest(size=len(packet), fds=len(fds)), \
                        socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as conn:
                    conn.connect(str(local.socket_path(self.root)))
                    conn.sendmsg([packet], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array("i", fds))])
                    with self.assertRaises(ValueError):
                        local.receive_packet(conn)
        self.assertEqual(self.work("load"), [])

    def test_sidecar_rejects_changed_inference_body_before_prepare(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        request = self.body("infer")
        with api.acquire_compute() as lease:
            job = self.lane.create(INFER, request)
            job.update(state="running")
            local.write_record(local.job_path(self.root, INFER), job)
            self.lane.write_state(self.worker.instance, True, pending=INFER, baseline=64)
            changed = {**request, "prompt": "A different request"}
            with self.assertRaises(ValueError):
                local.exchange(self.root, {"action": "run", "id": INFER, "request": changed}, lease)
        self.assertEqual(self.work("prepare", "infer"), [])

    def test_foley_uses_same_lease_and_card_handover(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        with patch.object(product_foley, "CONFIG", self.config), \
                patch.object(product_foley.service, "ROOT", self.root), \
                patch.object(product_foley.service, "idle", api.check_local_queue):
            with api.acquire_compute() as lease:
                product_foley.prepare_foley(lease)
                self.assertIsNone(api.acquire_compute())
            self.assertEqual(len(self.work("unload")), 1)
            self.assertFalse(self.lane.path.exists())
