"""CPU-only handover tests with the real flock, journal, HTTP and Unix protocol."""
from array import array
from contextlib import contextmanager
import hashlib
from http.client import HTTPException
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_api import APP, api
from test_product import EMPTY, PNG, call, server
import local_model_sidecar as sidecar
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
        self.f.record_work("load")
        self.f.memory = self.f.load_memory

    def prepare(self, prompt, image):
        self.f.events.append(("prepare", prompt, image.read_bytes() if image else None))
        return (prompt, image), self.f.inputs

    def infer(self, prepared, cap):
        self.f.record_work("infer")
        self.f.events.append(("token_cap", cap))
        self.f.infer_entered.set()
        if self.f.block_infer:
            self.f.release_infer.wait(5)
        return self.f.text, self.f.outputs

    def unload(self):
        self.f.record_work("unload")
        if self.f.free_on_unload:
            self.f.memory = 64


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
        self.memory = 64
        self.load_memory = 5000
        self.report_card = CARD
        self.card_temperature = 50
        self.inputs, self.text, self.outputs = 8, "A short result", 3
        self.queue = EMPTY
        self.sidecar_queue = None
        self.free_on_unload = True
        self.block_infer = False
        self.infer_entered = threading.Event()
        self.release_infer = threading.Event()
        self.infer_connection_closed = threading.Event()
        self.worker = self.new_sidecar()
        self.http_done = threading.Event()
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
        @contextmanager
        def deadline(seconds):
            self.events.append(("deadline", seconds))
            yield

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

        return sidecar.Sidecar(self.root, profile, lambda _: FakeModel(self), check_idle=check_idle,
                               sample=self.card_sample, deadline=deadline)

    def card_sample(self, card):
        self.events.append(("memory", card, self.memory))
        return {"gpu_uuid": self.report_card, "memory_mib": self.memory,
                "temperature_c": self.card_temperature, "utilization_percent": 0}

    def gpu_sample(self):
        return {"gpus": [self.card_sample(CARD), {
            "gpu_uuid": OTHER, "memory_mib": 64, "temperature_c": 50, "utilization_percent": 0,
        }]}

    def engine(self, path, body=None):
        self.engine_calls.append((path, body))
        if path == "/queue":
            return self.queue
        if path == "/free":
            self.events.append(("free_cache", self.memory))
            self.memory = 64
            return None
        if path == "/prompt":
            self.events.append(("wan_prompt", self.memory))
            prompt_id = body.get("prompt_id", "legacy-prompt")
            filename = body["client_id"] + "_00001_.mp4"
            self.prompts.append((prompt_id, filename))
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
            return self.lane.run(job, request, lease, api.check_local_compute,
                                 ensure_load_memory=api.clear_local_load_memory)

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
            ("deadline", 4), ("deadline", 5), ("prepare", "Describe the board", PNG), ("token_cap", 5), ("deadline", 6),
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

    def test_context_cap_counts_prepared_input_and_bounds_backend_output(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.inputs = 53
        self.assertEqual(self.perform(INFER, "infer")["error"], "context_limit")
        self.assertEqual(self.work("prepare"), [("prepare", "Describe the board", None)])
        self.assertEqual(self.work("infer"), [])
        self.inputs, self.outputs = 8, 13
        self.assertEqual(self.perform("04" * 16, "infer")["error"], "backend_failed")
        self.assertEqual(self.work("token_cap"), [("token_cap", 12)])
        self.assertTrue(product.read_job(self.lane.path)["held"])

    def test_output_byte_budget_is_measured_in_utf8(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.text = "€" * (local.MAX_TEXT_BYTES // 3 + 1)
        self.assertEqual(self.perform(INFER, "infer")["error"], "backend_failed")
        self.assertEqual(self.work("infer"), [("infer", INFER, "running", False)])
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
                self.assertTrue(api.UNSAFE)
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

    def test_sidecar_rechecks_engine_queue_at_dispatch(self):
        self.sidecar_queue = {**EMPTY, "queue_running": [[0, "late-foreign"]]}
        self.assertEqual(self.perform(LOAD, "load")["error"], "engine_work_unknown")
        self.assertEqual(self.work("receiver_queue"), [("receiver_queue", self.sidecar_queue)])
        self.assertEqual(self.work("load"), [])
        self.assertTrue(product.read_job(self.lane.path)["held"])
        self.assertEqual(self.prompts, [])

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

    def test_local_load_releases_only_idle_comfy_cache(self):
        self.memory = 3000
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.assertEqual([event[0] for event in self.work("free_cache", "load")], ["free_cache", "load"])
        index = self.engine_calls.index(("/free", {"unload_models": True, "free_memory": True}))
        self.assertEqual(self.engine_calls[index - 1], ("/queue", None))
        self.assertEqual(self.engine_calls[index + 1], ("/queue", None))

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
            self.lane.write_state(self.worker.instance, True, pending=INFER)
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
            self.lane.write_state(self.worker.instance, True, pending=INFER)
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
            self.lane.write_state(self.worker.instance, True, pending=INFER)
            changed = {**request, "prompt": "A different request"}
            with self.assertRaises(ValueError):
                local.exchange(self.root, {"action": "run", "id": INFER, "request": changed}, lease)
        self.assertEqual(self.work("prepare", "infer"), [])

    def test_foley_uses_same_lease_and_card_handover(self):
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        with patch.object(product_foley, "CONFIG", self.config), \
                patch.object(product_foley.service, "ROOT", self.root), \
                patch.object(product_foley.service, "idle", api.check_local_queue):
            with product_foley.acquire_with_local() as lease:
                self.assertIsNotNone(lease)
                self.assertIsNone(api.acquire_compute())
            self.assertEqual(len(self.work("unload")), 1)
            self.assertFalse(self.lane.path.exists())

    def test_sidecar_startup_requires_exact_gpu_pin(self):
        with patch.dict("os.environ", {"NVIDIA_VISIBLE_DEVICES": CARD, "CUDA_VISIBLE_DEVICES": OTHER}):
            with self.assertRaisesRegex(ValueError, "must be pinned"):
                sidecar.serve(self.root, PROFILE, None)

    def test_kernel_deadline_terminates_blocked_sidecar_process(self):
        code = "import time, local_model_sidecar as s\nwith s.operation_deadline(1): time.sleep(2)"
        result = subprocess.run([sys.executable, "-c", code], cwd=APP, timeout=5, capture_output=True)
        self.assertEqual(result.returncode, -signal.SIGALRM)
        self.assertEqual(result.stderr, b"")
