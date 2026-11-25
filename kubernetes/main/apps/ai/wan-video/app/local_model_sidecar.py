"""Supervise the one process that owns the local model and its CUDA context.

The backend factory runs in the child. Load and inference must drain GPU work
before returning. Preparation runs on the CPU, including any image decode,
resize, template expansion and token counting. It returns the prepared prompt,
total input token count, and one visual token count per image. Inference returns
text, output tokens and a completion flag used to reject truncated structured
output. The backend must not spawn a process or service with an independent GPU
lifetime.
"""
from contextlib import closing
import csv
import ctypes
import fcntl
import json
from http.client import HTTPException
import multiprocessing
import os
import signal
import socket
import stat
import subprocess
import time
import uuid

import product
import local_model_modes as modes
import product_local as local


PIN_VARIABLES = ("NVIDIA_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES")
STARTUP_LOG = "local-model-listening"
PIN_PROOF_LOG = "local-model-pin-proof"
PIN_UNPROVEN = "gpu_pin_unproven"


def pin_environment_error(card, pins=None):
    if pins is None:
        pins = {name: os.environ.get(name) for name in PIN_VARIABLES}
    for name, label, allowed in (
        ("CUDA_VISIBLE_DEVICES", "cuda", (card,)),
        ("NVIDIA_VISIBLE_DEVICES", "nvidia", (card, "void")),
    ):
        if pins[name] is None:
            return label + "_pin_missing"
        if not pins[name]:
            return label + "_pin_empty"
        if pins[name] not in allowed:
            return label + "_pin_mismatch"


def visible_gpu_uuids():
    # nvidia-smi reads driver visibility through NVML. An unfiltered query is
    # necessary here, since a successful --id query cannot rule out extra cards.
    rows = local.query_gpu("uuid")
    if any(len(row) != 1 or not row[0].strip() for row in rows):
        raise ValueError("Invalid driver GPU identity output")
    return [row[0].strip() for row in rows]


def read_startup_visibility():
    try:
        return visible_gpu_uuids(), None
    except subprocess.TimeoutExpired:
        return None, "gpu_visibility_timeout"
    except (OSError, ValueError, csv.Error, subprocess.SubprocessError):
        return None, "gpu_visibility_unavailable"


def check_startup_pin(card, lock_path, *, lease_verified=False):
    pins = {name: os.environ.get(name) for name in PIN_VARIABLES}
    visible = reason = None
    if card is not None:
        if lease_verified:
            visible, reason = read_startup_visibility()
        else:
            try:
                with lock_path.open("rb") as lease:
                    try:
                        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        reason = PIN_UNPROVEN
                    else:
                        visible, reason = read_startup_visibility()
            except OSError:
                reason = "gpu_pin_lease_unavailable"
        if visible is not None:
            if not visible:
                reason = "gpu_visibility_empty"
            elif len(visible) != 1:
                reason = "gpu_visibility_multiple"
            elif visible[0] != card:
                reason = "gpu_visibility_mismatch"
        reason = pin_environment_error(card, pins) or reason
    return {
        "gpu_uuid": card,
        "pins": {name: value[:128] if value is not None else None for name, value in pins.items()},
        "visible_gpu_uuids": None if visible is None else [identity[:128] for identity in visible],
        "visible_gpu_count": None if visible is None else len(visible),
        "reason_code": reason,
    }


def verify_lease(root, fd):
    """Verify that the passed descriptor refers to the already locked open file."""
    path = root / "compute.lock"
    descriptor = os.fstat(fd)
    with path.open("rb") as independent:
        expected = os.fstat(independent.fileno())
        if (descriptor.st_dev, descriptor.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("Wrong compute lease file")
        try:
            fcntl.flock(independent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise ValueError("Compute lease was not held")
        # A separate open description cannot take the existing owner's flock.
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def infer(backend, root, profile, request):
    images = local.check_images(root, request)
    mode = modes.MODES[request.get("mode", "text")]
    prepared, inputs, visual = backend.prepare(request["prompt"], images, mode)
    if (type(inputs) is not int or inputs < 0 or type(visual) is not list or len(visual) != len(images)
            or any(type(tokens) is not int or not 1 <= tokens <= local.MAX_VISUAL_TOKENS for tokens in visual)
            or sum(visual) > inputs):
        raise ValueError("Invalid input token count")
    cap = request.get("max_output_tokens", profile["max_output_tokens"])
    if inputs + cap > profile["context_tokens"]:
        return {"error": "context_limit"}
    text, outputs, complete = backend.infer(prepared, cap)
    result = {"text": text, "input_tokens": inputs, "output_tokens": outputs}
    if not mode and complete is False:
        result["truncated"] = True
    if not local.valid_result_bounds(profile, request, result):
        return {"error": "output_limit"}
    if mode and complete is not True:
        return {"error": "invalid_result", "reason_code": "incomplete_output"}
    if not local.valid_result_mode(request, result):
        return {"error": "invalid_result", "reason_code": "mode_mismatch"}
    return {"result": result}


def model_worker(conn, parent_pid, parent_fds, root, profile, factory):
    # Linux does not inherit the parent-death signal across fork. Arm it
    # before touching the model and check for parent death during that window.
    for fd in parent_fds:
        os.close(fd)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    pr_set_pdeathsig = 1
    if ctypes.CDLL(None).prctl(pr_set_pdeathsig, signal.SIGKILL, 0, 0, 0) or os.getppid() != parent_pid:
        return
    os.setsid()
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM, signal.SIGINT})
    with conn:
        try:
            backend = factory(profile)
            backend.load()
            local.send_packet(conn, {})
            try:
                while True:
                    message, _ = local.receive_packet(conn)
                    if message["operation"] == "unload":
                        return
                    reply = infer(backend, root, profile, message)
                    if reply.get("error") == "output_limit":
                        break
                    local.send_packet(conn, reply)
            finally:
                backend.unload()
            local.send_packet(conn, reply)
        except Exception:
            try:
                local.send_packet(conn, {"error": "backend_failed"})
            except OSError:
                pass


class ModelProcess:
    def __init__(self, root, profile, factory):
        self.root = root
        self.profile = profile
        self.factory = factory
        self.process = self.conn = None

    def alive(self):
        return self.process is not None and self.process.is_alive()

    def stop(self):
        if self.process is not None:
            group = self.process.pid
            self.kill_group()
            self.process.join()
            # PID 1 (or our subreaper outside a container) adopts descendants.
            # Wait for this group only, after multiprocessing reaps its leader.
            while True:
                try:
                    os.waitpid(-group, 0)
                except ChildProcessError:
                    break
            self.process.close()
            self.conn.close()
            self.process = self.conn = None

    def kill_group(self):
        pid = self.process.pid
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            # On a startup deadline the child may still be before setsid().
            if self.process.exitcode is None:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    @staticmethod
    def remaining(deadline):
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError
        return seconds

    def execute(self, request, deadline, parent_fds):
        operation = request["operation"]
        try:
            self.remaining(deadline)
            if operation == "load":
                if self.process is not None:
                    raise ValueError("Model already owns a process")
                # The serving parent is single threaded and has not initialized
                # CUDA. Close its listener, request socket and lease in the child.
                conn, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
                with peer:
                    process = multiprocessing.get_context("fork").Process(
                        target=model_worker, daemon=True,
                        args=(peer, os.getpid(), (*parent_fds, conn.fileno()),
                              self.root, self.profile, self.factory),
                    )
                    previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
                    try:
                        process.start()
                        self.conn, self.process = conn, process
                    except BaseException:
                        conn.close()
                        raise
                    finally:
                        signal.pthread_sigmask(signal.SIG_SETMASK, previous)
            else:
                self.conn.settimeout(self.remaining(deadline))
                local.send_packet(self.conn, request)
            if operation == "unload":
                self.process.join(self.remaining(deadline))
                if self.process.exitcode is None:
                    raise TimeoutError
                if self.process.exitcode != 0:
                    return {"error": "backend_exited"}
                # A clean exit is the acknowledgement. If unload raised,
                # the child sends an error before exiting.
                self.kill_group()
                packet = self.conn.recv(1, socket.MSG_PEEK)
                return {"error": "backend_failed"} if packet else {}
            self.conn.settimeout(self.remaining(deadline))
            reply, _ = local.receive_packet(self.conn)
            if reply.get("error") in {"backend_failed", "output_limit"}:
                self.stop()
            return reply
        except TimeoutError:
            self.stop()
            return {"error": "operation_timeout"}
        except (OSError, ValueError):
            self.stop()
            return {"error": "backend_exited"}
        finally:
            if operation == "unload":
                self.stop()


class Sidecar:
    def __init__(self, root, profile, backend_factory, *, check_idle, evict_cache,
                 sample=local.sample_card, startup_error=None):
        self.lane = local.Lane(root, profile)
        self.profile = profile
        self.startup_error = startup_error
        self.model = None if startup_error else ModelProcess(root, profile, backend_factory)
        self.backend_factory = backend_factory
        self.sample = sample
        self.check_idle = check_idle
        self.evict_cache = evict_cache
        self.instance = uuid.uuid4().hex
        self.receipt = None

    def prove_pending_pin(self):
        if self.startup_error != PIN_UNPROVEN:
            return
        proof = check_startup_pin(self.profile["gpu_uuid"], self.lane.root / "compute.lock",
                                  lease_verified=True)
        self.startup_error = proof["reason_code"]
        if self.startup_error is None:
            self.model = ModelProcess(self.lane.root, self.profile, self.backend_factory)
        print(json.dumps({"event": PIN_PROOF_LOG, **proof}), flush=True)

    def status(self, *, residency_only=False):
        if self.startup_error:
            return {
                "gpu_uuid": self.profile["gpu_uuid"] if self.profile else None,
                "profile_sha256": self.lane.profile_hash, "instance": self.instance,
                "resident": False, "receipt": None, "startup_error": self.startup_error,
            }
        card = self.profile["gpu_uuid"]
        telemetry = {} if residency_only else self.sample(card)
        return {
            "gpu_uuid": card, **telemetry,
            "profile_sha256": self.lane.profile_hash, "instance": self.instance,
            "resident": self.model.alive(), "receipt": self.receipt,
        }

    def load_sample(self):
        card = self.profile["gpu_uuid"]
        try:
            sample = self.sample(card)
            if not local.valid_card_sample(sample, card):
                raise ValueError("Invalid local-model GPU sample")
        except (OSError, ValueError, csv.Error, subprocess.SubprocessError) as exc:
            raise product.RequestError("local_model_unavailable", "Model card state is unavailable", 503) from exc
        if sample["temperature_c"] >= 62:
            raise product.RequestError("model_card_hot", "Model card is too warm", 503)
        return sample

    def load_baseline(self, deadline):
        """Evict only the idle engine cache with the compute lease held."""
        if not self.lane.own_card_idle(self.load_sample()):
            raise product.RequestError("model_card_busy", "Model card is busy", 503)
        self.check_idle()
        self.evict_cache()
        while (remaining := deadline - time.monotonic()) > 0:
            self.check_idle()
            sample = self.load_sample()
            if self.lane.unloaded(sample):
                return sample["memory_mib"]
            time.sleep(min(1, remaining))
        raise product.RequestError("model_card_busy", "Model card did not drain", 503)

    def run(self, message, fd, *socket_fds):
        job_id = message.get("id")
        if not local.valid_id(job_id) or message.keys() != {"action", "id", "request"}:
            raise ValueError("Invalid local-model dispatch")
        verify_lease(self.lane.root, fd)
        self.prove_pending_pin()
        if self.startup_error:
            raise local.StartupUnavailable(self.startup_error)
        state = self.lane.read_state()
        job = self.lane.read(job_id)
        request = local.validate(self.profile, message["request"])
        if (state is None or state.get("pending") != job_id or state["instance"] != self.instance
                or state["resident"] != self.model.alive() or job is None or job.get("state") != "running"
                or job.get("operation") != request["operation"]
                or job.get("request_sha256") != product.digest(request)):
            raise ValueError("Local-model dispatch has no matching journal entry")
        if self.receipt and self.receipt["id"] == job_id:
            return
        operation = job["operation"]
        # Repeat the queue read at the engine boundary. The API's earlier
        # observation may predate a delayed delivery to the sidecar.
        deadline = time.monotonic() + self.profile[operation + "_timeout_seconds"]
        try:
            self.check_idle()
            if operation == "load":
                baseline = self.load_baseline(deadline)
        except local.UnknownEngineWork as exc:
            outcome = {"error": "engine_work_unknown", "engine_busy": exc.engine_busy}
        except product.RequestError as exc:
            outcome = {"error": exc.code}
        except (OSError, HTTPException):
            outcome = {"error": "engine_queue_unavailable"}
        else:
            if operation == "load":
                self.lane.write_state(self.instance, False, pending=job_id, baseline=baseline)
            try:
                outcome = self.model.execute(request, deadline, (fd, *socket_fds))
            except Exception:
                self.model.stop()
                outcome = {"error": "backend_failed"}
            except BaseException:
                self.model.stop()
                raise
        self.receipt = {"id": job_id, "operation": operation, **outcome}

    def handle(self, conn, listener_fd=None):
        conn.settimeout(local.STATUS_SECONDS)
        try:
            message, fds = local.receive_packet(conn, max_fds=1)
            try:
                if (message == {"action": "residency"} or
                        not fds and message == {"action": "status"}):
                    if fds:
                        verify_lease(self.lane.root, fds[0])
                        self.prove_pending_pin()
                    reply = self.status(residency_only=message["action"] == "residency")
                elif len(fds) == 1 and message.get("action") == "run":
                    socket_fds = (conn.fileno(),) if listener_fd is None else (conn.fileno(), listener_fd)
                    try:
                        self.run(message, fds[0], *socket_fds)
                    except local.StartupUnavailable as exc:
                        reply = exc.body()
                    else:
                        reply = {"id": message["id"]}
                else:
                    raise ValueError("Invalid local-model sidecar call")
            except SystemExit:
                if self.model is not None:
                    self.model.stop()
                raise
            finally:
                # Retain the very same flock until execution and its receipt end,
                # including when the API exits or stops waiting for the result.
                for fd in fds:
                    os.close(fd)
            local.send_packet(conn, reply)
        except (OSError, ValueError, RecursionError, csv.Error, subprocess.SubprocessError):
            # Refuse invalid requests without exposing backend, workspace or input details.
            return


def serve(root, profile, backend_factory, *, startup_error=None, pin_lock=None):
    """Run one sidecar with no TCP endpoint and a workspace-private Unix socket."""
    def shutdown(signum, _frame):
        raise SystemExit(128 + signum)

    # PID 1 needs explicit termination handlers. A shutdown during dispatch
    # reaps the CUDA owner before handle() releases its copy of the lease.
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    proof = check_startup_pin(profile["gpu_uuid"] if profile else None, pin_lock or root / "compute.lock")
    startup_error = startup_error or proof["reason_code"]
    path = local.socket_path(root)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise ValueError("Local-model socket path is occupied")
        # A killed supervisor can leave a socket name behind. Never
        # replace an endpoint with a live listener.
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as probe:
            probe.settimeout(1)
            try:
                probe.connect(os.fspath(path))
            except ConnectionRefusedError:
                path.unlink()
            else:
                raise RuntimeError("Local-model sidecar is already serving")
    from api import engine, require_empty_queue

    def evict_cache():
        engine("/free", {"unload_models": True, "free_memory": True})

    pr_set_child_subreaper = 36
    if startup_error in (None, PIN_UNPROVEN) and ctypes.CDLL(None).prctl(pr_set_child_subreaper, 1, 0, 0, 0):
        startup_error = "process_supervision_unavailable"
    worker = Sidecar(root, profile, backend_factory, check_idle=require_empty_queue,
                     evict_cache=evict_cache, startup_error=startup_error)

    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as listener:
        listener.bind(os.fspath(path))
        try:
            path.chmod(0o600)
            listener.listen(4)
            print(json.dumps({
                "event": STARTUP_LOG, **proof, "startup_error": startup_error, "reason_code": startup_error,
            }), flush=True)
            while True:
                conn, _ = listener.accept()
                with closing(conn):
                    worker.handle(conn, listener.fileno())
        finally:
            if worker.model is not None:
                worker.model.stop()
            path.unlink(missing_ok=True)
