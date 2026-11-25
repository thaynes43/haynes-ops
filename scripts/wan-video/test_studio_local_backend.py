"""CPU-only tests of the Python/native boundary and the isolated weight Job."""
from contextlib import redirect_stdout
import ctypes as c
import fcntl
from email.message import Message
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request

from test_api import APP
from test_local_model import CARD, PROFILE
from test_product import PNG
import local_model_modes as modes
import local_model_sidecar as sidecar
import product
import product_local as local
import studio_local_backend as backend

import sys
WEIGHTS = APP.parent / "local-model-pending" / "weights"
sys.path.insert(0, str(WEIGHTS))
import provision
sys.path.pop(0)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "studio-local-model"))
from startup_smoke import fixture
from startup_check import wait_for_listener
sys.path.pop(0)


class FakeLibrary:
    def __init__(self):
        self.studio_model_open = Mock(return_value=123)
        self.studio_model_close = Mock()
        self.studio_request_free = Mock()
        self.frames = []
        self.input_tokens = 10
        self.output_tokens = 4
        self.answer = b"OK"
        self.complete = True
        self.generate_count = 0

    def studio_prepare(self, model, prompt, images, sizes, count, schema, tool, total, visual):
        self.schema = json.loads(schema) if schema else None
        self.tool = json.loads(tool) if tool else None
        addresses = c.cast(images, c.POINTER(c.c_void_p))
        self.frames = [c.string_at(addresses[i], sizes[i]) for i in range(count)]
        c.cast(total, c.POINTER(c.c_size_t))[0] = self.input_tokens
        for index in range(count):
            visual[index] = 2
        return 456

    def studio_generate(self, model, request, cap, byte_limit, tokens, complete, size):
        self.generate_count += 1
        self.buffer = c.create_string_buffer(self.answer)
        c.cast(tokens, c.POINTER(c.c_int))[0] = self.output_tokens
        c.cast(complete, c.POINTER(c.c_bool))[0] = self.complete
        c.cast(size, c.POINTER(c.c_size_t))[0] = len(self.answer)
        return c.addressof(self.buffer)


class BackendTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        pins = json.loads((WEIGHTS / "weights.json").read_text())
        self.profile = {**PROFILE, "model": pins["model"]}
        self.profile.pop("s2v_gpu_uuid")
        directory = self.root / pins["model"] / pins["revision"]
        directory.mkdir(parents=True)
        for pin in pins["files"]:
            (directory / pin["name"]).write_bytes(b"fixture")
            pin["bytes"] = len(b"fixture")
        (directory / "manifest.json").write_text(json.dumps(pins))
        self.lib = FakeLibrary()
        self.model = backend.LlamaBackend(self.profile, self.root, pins, library=lambda: self.lib)
        with patch.dict(os.environ, {"NVIDIA_VISIBLE_DEVICES": CARD, "CUDA_VISIBLE_DEVICES": CARD}):
            self.model.load()
        self.addCleanup(self.model.unload)
        self.addCleanup(self.check_unload)

    def check_unload(self):
        self.model.unload()
        self.lib.studio_model_close.assert_called_once_with(123)

    def request(self, name, **fields):
        request = {"operation": "infer", "profile": self.profile["model"], "prompt": "Describe the panel.",
                   "mode": name, **fields}
        return local.validate(self.profile, request)

    def infer(self, name, **fields):
        return sidecar.infer(self.model, self.root, self.profile, self.request(name, **fields))

    def stage(self, image):
        digest = hashlib.sha256(image).hexdigest()
        path = product.input_path(self.root, digest, "png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image)
        return digest

    def test_invalid_id_excess_images_and_caller_schema_stop_before_native_call(self):
        self.assertFalse(local.valid_id("../" + "a" * 32))
        with self.assertRaises(product.RequestError):
            self.request("frame_check_v1", image_sha256s=["a" * 64] * 5)
        with self.assertRaises(product.RequestError):
            self.request("plan_v1", schema={"type": "object"})
        self.assertEqual(self.lib.generate_count, 0)

    def test_context_overflow_stops_before_encoding_and_releases_prepared_request(self):
        self.lib.input_tokens = self.profile["context_tokens"]
        self.assertEqual(self.infer("plan_v1"), {"error": "context_limit"})
        self.lib.studio_request_free.assert_called_once_with(456)
        self.assertEqual(self.lib.generate_count, 0)

    def test_invalid_and_truncated_plan_are_refused(self):
        self.lib.answer = b'{"scene_id":"s1","shots":[]}'
        self.assertEqual(self.infer("plan_v1")["reason_code"], "mode_mismatch")
        self.lib.answer = b'{"scene_id":"s1","shots":[{"technical_direction":"Hold."}]}'
        self.lib.complete = False
        self.assertEqual(self.infer("plan_v1")["reason_code"], "incomplete_output")
        self.assertEqual(self.lib.studio_request_free.call_count, 2)

    def test_malformed_unexpected_and_extra_tool_arguments_are_refused(self):
        for answer in (b'<tool_call><bad>', b'{"name":"other","arguments":{}}',
                       b'{"name":"record_plan_v1","arguments":{"scene_id":"s","shot_count":1,'
                       b'"technical_direction":"Hold.","extra":true}}'):
            with self.subTest(answer=answer):
                self.lib.answer = answer
                self.assertEqual(self.infer("plan_tool_v1")["reason_code"], "mode_mismatch")
        self.assertEqual(self.lib.tool, modes.PLAN_TOOL)
        self.assertEqual(self.lib.studio_request_free.call_count, 3)

    def test_oversized_answer_is_rejected_and_request_is_freed(self):
        self.lib.answer = "€".encode() * (local.MAX_TEXT_BYTES // 3 + 1)
        self.assertEqual(self.infer("text"), {"error": "output_limit"})
        self.lib.studio_request_free.assert_called_once_with(456)

    def test_capped_utf8_trims_only_the_partial_suffix_and_marks_text_truncated(self):
        self.lib.answer = "ok€".encode() + b"\xf0\x9f"
        self.lib.complete = False
        self.assertEqual(self.infer("text"), {"result": {
            "text": "ok€", "input_tokens": 10, "output_tokens": 4, "truncated": True,
        }})
        self.lib.complete = True
        self.assertEqual(self.infer("text"), {"error": "output_limit"})
        self.lib.complete = False
        self.lib.answer = b"ok\xffno\xf0"
        self.assertEqual(self.infer("text"), {"error": "output_limit"})

    def test_truncation_flag_is_canonical_and_text_only(self):
        text = {"text": "A result", "input_tokens": 10, "output_tokens": 4}
        for mode, truncated in (("text", False), ("text", 1), ("plan_v1", True)):
            with self.subTest(mode=mode, truncated=truncated):
                self.assertFalse(local.valid_result_bounds(
                    self.profile, self.request(mode), {**text, "truncated": truncated},
                ))

    def test_frame_constraints_and_native_image_order(self):
        second = bytearray(PNG)
        # The staging validator checks the PNG envelope. The fake receives
        # the exact bytes with the alternate IHDR color type.
        second[25] = 2
        first_digest = self.stage(PNG)
        second_digest = self.stage(bytes(second))
        self.lib.answer = (b'{"frames":[{"face":"hidden","code":"B24","wardrobe":"match"},'
                           b'{"face":"visible","code":"A17","wardrobe":"match"}]}')
        result = self.infer("frame_check_v1", image_sha256s=[second_digest, first_digest])
        self.assertEqual([row["code"] for row in json.loads(result["result"]["text"])["frames"]], ["B24", "A17"])
        self.assertEqual(self.lib.frames, [bytes(second), PNG])
        schema = self.lib.schema["properties"]["frames"]
        self.assertEqual((schema["minItems"], schema["maxItems"]), (2, 2))
        self.assertEqual(modes.MODES["frame_check_v1"]["schema"]["properties"]["frames"]["maxItems"], 4)

    def test_ready_checks_listener_inode_without_connecting(self):
        path = local.socket_path(self.root)
        path.parent.mkdir()
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as listener:
            listener.bind(str(path))
            path.chmod(0o600)
            self.assertFalse(backend.listener_ready(self.root, os.getpid()))
            listener.listen(1)
            self.assertTrue(backend.listener_ready(self.root, os.getpid()))
            listener.setblocking(False)
            with self.assertRaises(BlockingIOError):
                listener.accept()
        self.assertFalse(backend.listener_ready(self.root, os.getpid()))


class StartupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.card = fixture(self.root)
        self.profile = self.root / "local-model.json"
        self.workspace = self.root / "work"
        self.workspace.mkdir()
        self.lease_path = self.workspace / "compute.lock"
        self.lease_path.touch()
        self.image = self.root / "app"
        self.image.mkdir()
        for module in APP.glob("*.py"):
            shutil.copyfile(module, self.image / module.name)
        shutil.copyfile(WEIGHTS / "weights.json", self.image / "weights.json")
        self.env = {**os.environ, **dict.fromkeys(sidecar.PIN_VARIABLES, self.card)}
        pins = json.loads((self.image / "weights.json").read_text())
        self.weights = self.root / "weights" / pins["model"] / pins["revision"]
        self.weight = self.weights / pins["files"][0]["name"]

    def check_started(self, reason, *, repair=None):
        command = [sys.executable, "-B", "-m", "studio_local_backend"]
        with subprocess.Popen(
            [*command, "serve", "--workspace", str(self.workspace), "--profile", str(self.profile),
             "--weights-root", str(self.root / "weights")],
            cwd=self.image, env=self.env, stdout=subprocess.PIPE, bufsize=0,
        ) as process:
            try:
                startup = wait_for_listener(process.stdout, sidecar.STARTUP_LOG)
                self.assertEqual(startup["startup_error"], reason)
                self.assertEqual(startup["pins"], {name: self.env.get(name) for name in sidecar.PIN_VARIABLES})
                subprocess.run(
                    [*command, "ready", "--workspace", str(self.workspace), "--pid", str(process.pid)],
                    cwd=self.image, env=self.env, timeout=5, check=True,
                )
                initial = local.exchange(self.workspace, {"action": "residency"})
                self.assertEqual(initial.get("startup_error"), reason)
                self.assertIs(initial["resident"], False)
                self.assertIsNone(initial["receipt"])
                if reason:
                    with self.lease_path.open("rb") as lease:
                        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        for operation in ("load", "infer"):
                            reply = local.exchange(self.workspace, {
                                "action": "run", "id": "a6" * 16, "request": {"operation": operation},
                            }, lease)
                            self.assertEqual(reply, local.StartupUnavailable(reason).body())
                    self.assertEqual(local.exchange(self.workspace, {"action": "status"}), initial)
                if repair:
                    repair()
                    self.assertEqual(local.exchange(self.workspace, {"action": "residency"}), initial)
                self.assertFalse(local.state_path(self.workspace).exists())
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise

    def test_serves_with_both_exact_pins(self):
        self.check_started(None)

    def test_wrong_nvidia_pin(self):
        self.env["NVIDIA_VISIBLE_DEVICES"] = "all"
        self.check_started("gpu_pin_mismatch")

    def test_wrong_cuda_pin(self):
        self.env["CUDA_VISIBLE_DEVICES"] = "0"
        self.check_started("gpu_pin_mismatch")

    def test_missing_nvidia_pin(self):
        del self.env["NVIDIA_VISIBLE_DEVICES"]
        self.check_started("gpu_pin_missing")

    def test_missing_cuda_pin(self):
        del self.env["CUDA_VISIBLE_DEVICES"]
        self.check_started("gpu_pin_missing")

    def test_missing_weights_stays_unavailable_until_restart(self):
        moved = self.weight.with_suffix(".missing")
        self.weight.rename(moved)
        self.check_started("weights_missing", repair=lambda: moved.rename(self.weight))
        self.check_started(None)

    def test_wrong_weight_size(self):
        with self.weight.open("r+b") as weight:
            weight.truncate(1)
        self.check_started("weights_mismatch")

    def test_wrong_weight_manifest(self):
        manifest = self.weights / "manifest.json"
        pins = json.loads(manifest.read_text())
        pins["files"][0]["sha256"] = "0" * 64
        manifest.write_text(json.dumps(pins))
        self.check_started("weights_mismatch")

    def test_weights_manifest_io_error(self):
        manifest = self.weights / "manifest.json"
        manifest.unlink()
        manifest.mkdir()
        self.check_started("weights_unreadable")

    def test_missing_profile(self):
        self.profile.unlink()
        self.check_started("profile_missing")

    def test_invalid_profile(self):
        self.profile.write_text("{}")
        self.check_started("profile_invalid")

    def test_unsupported_profile(self):
        profile = json.loads(self.profile.read_text())
        self.profile.write_text(json.dumps({**profile, "model": "other"}))
        self.check_started("profile_unsupported")


class DownloadResponse(io.BytesIO):
    status = 200

    def __init__(self, data, declared):
        super().__init__(data)
        self.headers = {"Content-Length": str(declared)}


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "weights"
        self.root.mkdir()
        self.pins = json.loads((WEIGHTS / "weights.json").read_text())
        for item, data in zip(self.pins["files"], (b"model", b"projector"), strict=True):
            item.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        self.pins["byte_cap"] = len(b"modelprojector")
        reserve = patch.object(provision, "FREE_BYTES", 0)
        reserve.start()
        self.addCleanup(reserve.stop)

    def opener(self, *responses):
        opener = Mock()
        opener.open.side_effect = responses
        return opener

    def test_download_flushes_at_intervals_so_written_pages_do_not_accumulate(self):
        # The first installed attempt was killed for memory after 570 MB with one
        # fsync planned at the end. With a 2-byte chunk and a 4-byte interval, a
        # 5-byte and a 9-byte file must each be settled inside the loop and once
        # more at the end, and still hash to their pins.
        opener = self.opener(DownloadResponse(b"model", 5), DownloadResponse(b"projector", 9))
        with patch.object(provision, "CHUNK", 2), patch.object(provision, "FLUSH_EVERY", 4), \
                patch.object(provision, "settle", wraps=provision.settle) as settle:
            target = provision.provision(self.root, self.pins, opener)
        self.assertEqual(settle.call_count, (1 + 1) + (2 + 1))
        self.assertEqual((target / self.pins["files"][0]["name"]).read_bytes(), b"model")
        self.assertEqual((target / self.pins["files"][1]["name"]).read_bytes(), b"projector")

    def test_publish_verified_pair_create_only(self):
        opener = self.opener(DownloadResponse(b"model", 5), DownloadResponse(b"projector", 9))
        target = provision.provision(self.root, self.pins, opener)
        self.assertEqual(json.loads((target / "manifest.json").read_text()), self.pins)
        self.assertEqual({p.name for p in target.parent.iterdir()}, {self.pins["revision"]})
        with self.assertRaisesRegex(provision.ProvisionError, "revision_already_exists"):
            provision.provision(self.root, self.pins, opener)
        self.assertEqual(opener.open.call_count, 2)
        competing = self.root / "competing"
        competing.mkdir()
        with self.assertRaises(FileExistsError):
            provision.publish(competing, target)
        self.assertTrue(competing.exists())

    def test_second_file_rechecks_space_and_keeps_partial_quarantined(self):
        opener = self.opener(DownloadResponse(b"model", 5), DownloadResponse(b"projector", 9))
        disk = SimpleNamespace(f_bavail=1 << 20, f_frsize=4096, f_favail=1024 + 16)

        def statvfs(path):
            if opener.open.call_count == 1 and any(path.glob("*.gguf")):
                return SimpleNamespace(f_bavail=disk.f_bavail, f_frsize=4096, f_favail=1024)
            return disk

        with patch.object(provision.os, "statvfs", side_effect=statvfs):
            with self.assertRaisesRegex(provision.ProvisionError, "insufficient_free_inodes"):
                provision.provision(self.root, self.pins, opener)
        self.assertEqual(opener.open.call_count, 1)
        self.assertFalse((self.root / self.pins["model"] / self.pins["revision"]).exists())
        self.assertEqual(len(list(self.root.glob("*/.quarantine-*"))), 1)

    def test_overlong_download_and_mismatched_hash_stay_quarantined(self):
        for data, error in ((b"model-plus", "byte_cap_exceeded"),
                            (b"wrong", "download_hash_mismatch")):
            with self.subTest(error=error):
                with self.assertRaisesRegex(provision.ProvisionError, error):
                    provision.provision(self.root, self.pins, self.opener(DownloadResponse(data, 5)))
        self.assertEqual(len(list(self.root.glob("*/.quarantine-*"))), 2)
        self.assertFalse((self.root / self.pins["model"] / self.pins["revision"]).exists())

    def test_redirect_accepts_hugging_face_cdn_subdomains(self):
        handler = provision.PinnedRedirect()
        request = Request("https://huggingface.co")
        for host in ("us.aws.cdn.hf.co", "us.gcp.cdn.hf.co", "edge.cdn.hf.co"):
            with self.subTest(host=host):
                url = f"https://{host}/blob"
                result = handler.redirect_request(request, None, 302, "", {}, url)
                self.assertEqual(result.full_url, url)

    def test_only_https_cdn_redirects_are_followed_and_hosts_are_logged(self):
        request = Request("https://huggingface.co/resolve/file")
        handler = provision.PinnedRedirect()
        for location, host, allowed in (
            ("https://us.aws.cdn.hf.co/path?signature=secret", "us.aws.cdn.hf.co", True),
            ("https://us.gcp.cdn.hf.co/path?signature=secret", "us.gcp.cdn.hf.co", True),
            ("https://us.aws.cdn.hf.co.example.org/path?signature=secret", "us.aws.cdn.hf.co.example.org", False),
            ("http://us.aws.cdn.hf.co/path?signature=secret", "us.aws.cdn.hf.co", False),
            ("https://user:password@us.aws.cdn.hf.co/path", "us.aws.cdn.hf.co", False),
            ("https://us.aws.cdn.hf.co:443/path", "us.aws.cdn.hf.co", False),
            ("/path?signature=secret", "huggingface.co", False),
            ("https://[invalid", "invalid", False),
        ):
            with self.subTest(location=location):
                headers = Message()
                headers["Location"] = location
                output = io.StringIO()
                with patch.object(provision.HTTPRedirectHandler, "http_error_302") as follow, redirect_stdout(output):
                    if allowed:
                        handler.http_error_307(request, None, 307, "", headers)
                        follow.assert_called_once()
                    else:
                        with self.assertRaisesRegex(provision.ProvisionError, "unexpected_download_redirect"):
                            handler.http_error_307(request, None, 307, "", headers)
                        follow.assert_not_called()
                self.assertEqual(output.getvalue(), f"Download redirect host={host} allowed={str(allowed).lower()}\n")
