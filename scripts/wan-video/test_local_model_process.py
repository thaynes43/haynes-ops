"""Exercise the supervisor in its own session and the model in its real child."""
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_local_model import CARD, INFER, LOAD, PROFILE, UNLOAD, api, local, sidecar


def child_pid(root):
    return int((root / "model.pid").read_text())


class ProcessBackend:
    def __init__(self, root, scenario):
        self.root = root
        self.scenario = scenario

    def load(self):
        (self.root / "model.pid").write_text(str(os.getpid()))
        if api.acquire_compute() is not None:
            raise RuntimeError("Missing compute lease")
        if any("compute.lock" in entry.readlink().name for entry in Path("/proc/self/fd").iterdir()
               if entry.exists()):
            raise RuntimeError("Backend inherited the compute descriptor")
        if self.scenario == "failed-load":
            raise RuntimeError("Load failed after allocating")
        if self.scenario == "hung-load":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.pause()

    def prepare(self, prompt, images, mode):
        return prompt, 1, []

    def infer(self, prepared, cap):
        if self.scenario == "hung-infer":
            (self.root / "infer.entered").touch()
            signal.pause()
        if self.scenario == "hung-descendant":
            # Inherit the model's process group, as a native helper would.
            helper = os.fork()
            if helper == 0:
                signal.pause()
                os._exit(0)
            (self.root / "helper.pid").write_text(str(helper))
            signal.pause()
        if self.scenario == "failed-infer":
            raise RuntimeError("Decode failed")
        if self.scenario == "oversized-infer":
            return "x" * (local.MAX_TEXT_BYTES + 1), 2, True
        if self.scenario == "dead-infer":
            os._exit(17)
        return "cased result", 2, True

    def unload(self):
        if self.scenario == "hung-unload":
            (self.root / "unload.entered").touch()
            signal.pause()


def serve_fixture(root, scenario):
    def sample(card):
        # A supervised child deliberately left the fd table, and memory
        # becomes available only after its process has been reaped.
        pid_file = root / "model.pid"
        allocated = pid_file.exists() and Path(f"/proc/{child_pid(root)}").exists()
        if allocated or (root / "memory.stuck").exists():
            memory = 5000
        elif (root / "memory.residual").exists():
            memory = int((root / "memory.residual").read_text())
        elif scenario == "tight-ceiling" and not pid_file.exists():
            memory = 63
        else:
            memory = 64
        return {"gpu_uuid": card, "memory_mib": memory, "temperature_c": 50, "utilization_percent": 0}

    listen = socket.socket.listen

    def listening(sock, backlog):
        listen(sock, backlog)
        print("ready", flush=True)

    def make_sidecar(root, profile, factory, **kwargs):
        return original(root, profile, factory, sample=sample, **kwargs)

    original = sidecar.Sidecar
    os.environ.update(CUDA_VISIBLE_DEVICES=CARD, NVIDIA_VISIBLE_DEVICES=CARD)
    (root / "compute.lock").touch()
    profile = {**PROFILE, "load_timeout_seconds": 1, "infer_timeout_seconds": 1, "unload_timeout_seconds": 1}
    if scenario == "tight-ceiling":
        profile["unloaded_max_mib"] = 63
    def verify_lease(*args):
        if scenario == "refused-verify-term":
            print("verifying", flush=True)
            signal.pause()
        return verify(*args)

    verify = sidecar.verify_lease
    refusal = "cuda_pin_mismatch" if scenario == "refused-verify-term" else None
    with patch.object(socket.socket, "listen", listening), patch.object(sidecar, "Sidecar", make_sidecar), \
            patch.object(sidecar, "visible_gpu_uuids", return_value=[CARD]), \
            patch.object(sidecar, "verify_lease", verify_lease), \
            patch.object(api, "ROOT", root), patch.object(api, "require_empty_queue", return_value=None), \
            patch.object(api, "engine", return_value=None), \
            patch.dict(sys.modules, {"api": api}):
        try:
            sidecar.serve(root, profile, lambda _: ProcessBackend(root, scenario), startup_error=refusal)
        finally:
            # Keep even a failing watchdog test from leaving adopted zombies.
            try:
                while os.waitpid(-1, os.WNOHANG)[0]:
                    pass
            except ChildProcessError:
                pass


class ModelProcessTests(unittest.TestCase):
    def start_supervisor(self, scenario):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.profile = {**PROFILE, "load_timeout_seconds": 1, "infer_timeout_seconds": 1, "unload_timeout_seconds": 1}
        if scenario == "tight-ceiling":
            self.profile["unloaded_max_mib"] = 63
        self.lane = local.Lane(self.root, self.profile)
        patcher = patch.object(api, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.parent = subprocess.Popen(
            [sys.executable, "-B", __file__, str(self.root), scenario],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )

        def stop():
            if self.parent.poll() is None:
                self.parent.terminate()
            try:
                output, error = self.parent.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                self.parent.kill()
                self.parent.communicate(timeout=4)
                self.fail("Supervisor did not terminate")
            self.assertEqual(error, b"", (output, error))
            self.assertEqual(self.parent.returncode, 128 + signal.SIGTERM)

        self.addCleanup(stop)
        ready, _, _ = select.select([self.parent.stdout], [], [], 5)
        self.assertTrue(ready, "Supervisor did not bind")
        self.assertEqual(self.parent.stdout.readline(), b"ready\n")

    def perform(self, job_id, operation):
        request = {"operation": operation, "profile": self.profile["model"]}
        if operation == "infer":
            request["prompt"] = "Describe the shot."
        lease = api.acquire_compute()
        self.assertIsNotNone(lease, "Previous operation kept a copy of the lease")
        with lease:
            return self.lane.run(self.lane.create(job_id, request), request, lease, lambda: None)

    def test_sigterm_during_lease_verification_in_startup_refusal(self):
        self.start_supervisor("refused-verify-term")
        self.assertEqual(self.lane.status(residency_only=True)["startup_error"], "cuda_pin_mismatch")
        # The ready sentinel precedes the structured startup log.
        self.assertIn(sidecar.STARTUP_LOG.encode(), self.parent.stdout.readline())
        with api.acquire_compute() as lease, socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as conn:
            conn.connect(str(local.socket_path(self.root)))
            local.send_packet(conn, {"action": "run", "id": LOAD, "request": {}}, (lease.fileno(),))
            ready, _, _ = select.select([self.parent.stdout], [], [], 5)
            self.assertTrue(ready, "Supervisor did not enter verify_lease")
            self.assertEqual(self.parent.stdout.readline(), b"verifying\n")
            self.parent.terminate()
            self.assertEqual(self.parent.wait(timeout=5), 128 + signal.SIGTERM)
        self.assertFalse(local.socket_path(self.root).exists())

    def assert_reaped(self):
        pid = child_pid(self.root)
        self.assertFalse(Path(f"/proc/{pid}").exists(), "Child was still alive or unreaped")
        self.assertIsNone(self.parent.poll(), "Deadline terminated the supervisor")
        self.assertFalse(self.lane.path.exists())
        self.assertFalse(self.lane.status()["resident"])
        with api.acquire_compute() as lease:
            self.assertIsNotNone(lease, "Reaped child kept the lease")

    def test_model_child_arms_parent_death_signal_before_loading(self):
        factory = Mock()
        with patch.object(sidecar.ctypes, "CDLL") as libc, patch.object(sidecar.signal, "signal"), \
                patch.object(sidecar.os, "setsid") as setsid, patch.object(sidecar.os, "getppid", return_value=10):
            libc.return_value.prctl.return_value = 0
            sidecar.model_worker(None, 11, (), None, None, factory)
            libc.return_value.prctl.assert_called_once_with(1, signal.SIGKILL, 0, 0, 0)
        factory.assert_not_called()
        setsid.assert_not_called()

    def test_hung_load_kills_and_reaps_the_child_with_parent_alive(self):
        self.start_supervisor("hung-load")
        self.assertEqual(self.perform(LOAD, "load")["error"], "operation_timeout")
        self.assert_reaped()

    def test_hung_inference_kills_and_reaps_the_resident_child(self):
        self.start_supervisor("hung-infer")
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.assertEqual(self.perform(INFER, "infer")["error"], "operation_timeout")
        self.assertTrue((self.root / "infer.entered").exists())
        self.assert_reaped()

    def test_watchdog_kills_the_inference_process_group(self):
        self.start_supervisor("hung-descendant")

        def cleanup_helper():
            path = self.root / "helper.pid"
            if path.exists():
                try:
                    os.kill(int(path.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

        self.addCleanup(cleanup_helper)
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.assertEqual(os.getpgid(child_pid(self.root)), child_pid(self.root))
        self.assertEqual(self.perform(INFER, "infer").get("error"), "operation_timeout")
        self.assert_reaped()
        helper = int((self.root / "helper.pid").read_text())
        self.assertFalse(Path(f"/proc/{helper}").exists(), "Watchdog left a live or unreaped descendant")

    def test_residency_reports_the_live_child_and_needs_no_gpu_sample(self):
        self.start_supervisor("normal")
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        pid = child_pid(self.root)
        self.assertTrue(self.lane.status(residency_only=True)["resident"])
        with os.fdopen(os.pidfd_open(pid), "rb") as pidfd:
            os.kill(pid, signal.SIGKILL)
            self.assertEqual(select.select([pidfd], [], [], 1)[0], [pidfd])
        snapshot = self.lane.status(residency_only=True)
        self.assertFalse(snapshot["resident"])
        self.assertNotIn("memory_mib", snapshot)

    def test_output_limit_has_a_reaped_memory_receipt(self):
        self.start_supervisor("oversized-infer")
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        job = self.perform(INFER, "infer")
        self.assertEqual((job["error"], job["released_memory_mib"]), ("output_limit", 64))
        self.assert_reaped()

    def test_unload_uses_profile_ceiling_on_post_exit_sample(self):
        self.start_supervisor("tight-ceiling")
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        # Give the journal a larger historical baseline. The active profile
        # ceiling still independently gates the release.
        self.lane.write_state(self.lane.read_state()["instance"], True, baseline=64)
        with patch.object(local.time, "sleep"):
            job = self.perform(UNLOAD, "unload")
        self.assertEqual(job["error"], "residency_unverified")
        self.assertNotIn("released_memory_mib", job)
        self.assertEqual(self.lane.status()["memory_mib"], 64)
        self.assertFalse(Path(f"/proc/{child_pid(self.root)}").exists())
        self.assertTrue(local.product.read_job(self.lane.path)["held"])

    def test_failed_load_is_reaped_and_records_verified_release(self):
        self.start_supervisor("failed-load")
        job = self.perform(LOAD, "load")
        self.assertEqual((job["error"], job["released_memory_mib"]), ("backend_failed", 64))
        self.assert_reaped()

    def test_failed_inference_is_reaped_and_records_verified_release(self):
        self.start_supervisor("failed-infer")
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        job = self.perform(INFER, "infer")
        self.assertEqual((job["error"], job["released_memory_mib"]), ("backend_failed", 64))
        self.assert_reaped()

    def test_release_accepts_small_accounting_drift_within_ceiling(self):
        self.start_supervisor("normal")
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        (self.root / "memory.residual").write_text("68")
        with patch.object(local.time, "sleep"):
            job = self.perform(UNLOAD, "unload")
        self.assertEqual(job["state"], "succeeded")
        self.assertEqual((job["baseline_memory_mib"], job["released_memory_mib"]), (64, 68))
        self.assert_reaped()

    def test_failed_inference_with_leak_below_ceiling_holds_and_records_reason(self):
        self.start_supervisor("failed-infer")
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        (self.root / "memory.residual").write_text("69")
        with patch.object(local.time, "sleep"):
            job = self.perform(INFER, "infer")
        self.assertEqual((job["error"], job["reason_code"]), ("residency_unverified", "backend_failed"))
        self.assertEqual(job["baseline_memory_mib"], 64)
        self.assertNotIn("released_memory_mib", job)
        self.assertEqual(self.lane.status()["memory_mib"], 69)
        self.assertTrue(local.product.read_job(self.lane.path)["held"])
        self.assertFalse(Path(f"/proc/{child_pid(self.root)}").exists())

    def test_child_death_mid_inference_is_reported_and_reaped(self):
        self.start_supervisor("dead-infer")
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.assertEqual(self.perform(INFER, "infer")["error"], "backend_exited")
        self.assert_reaped()

    def test_unload_that_child_ignores_times_out_and_kills_it(self):
        self.start_supervisor("hung-unload")
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        self.assertEqual(self.perform(UNLOAD, "unload")["error"], "operation_timeout")
        self.assertTrue((self.root / "unload.entered").exists())
        self.assert_reaped()

    def test_real_unload_reaps_child_before_ack_in_new_session(self):
        self.start_supervisor("normal")
        parent = self.parent.pid
        self.assertEqual(os.getsid(parent), parent)
        children = Path(f"/proc/{parent}/task/{parent}/children")
        self.assertEqual(children.read_text().strip(), "")
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        child = child_pid(self.root)
        self.assertEqual(children.read_text().split(), [str(child)])
        self.assertEqual(self.perform(INFER, "infer")["result"]["text"], "cased result")
        receipt = self.perform(UNLOAD, "unload")
        self.assertEqual((receipt["state"], receipt["released_memory_mib"]), ("succeeded", 64))
        self.assertEqual(children.read_text().strip(), "")
        self.assert_reaped()

    def test_deadline_still_requires_fresh_unloaded_memory_proof(self):
        self.start_supervisor("hung-infer")
        self.assertEqual(self.perform(LOAD, "load")["state"], "succeeded")
        (self.root / "memory.stuck").touch()
        with patch.object(local.time, "sleep"):
            self.assertEqual(self.perform(INFER, "infer")["error"], "residency_unverified")
        self.assertTrue(local.product.read_job(self.lane.path)["held"])
        self.assertFalse(Path(f"/proc/{child_pid(self.root)}").exists())


if __name__ == "__main__":
    serve_fixture(Path(sys.argv[1]), sys.argv[2])
