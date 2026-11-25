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
import ctypes
import csv
import fcntl
from http.client import HTTPException
import io
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


def sample_card(card):
    result = subprocess.run(
        ["nvidia-smi", "--id=" + card, "--query-gpu=uuid,memory.used,utilization.gpu,temperature.gpu",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True, timeout=10,
    )
    (row,) = csv.reader(io.StringIO(result.stdout))
    identity, memory, utilization, temperature = (value.strip() for value in row)
    if identity != card:
        raise ValueError("Unexpected local-model GPU UUID")
    return {
        "gpu_uuid": identity, "memory_mib": float(memory),
        "utilization_percent": float(utilization), "temperature_c": float(temperature),
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
    structured = request.get("mode", "text") != "text"
    if (structured and complete is not True) or not local.valid_result(profile, request, result):
        if structured:
            return {"error": "invalid_result"}
        raise ValueError("Invalid local-model result")
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
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM, signal.SIGINT})
    with conn:
        try:
            backend = factory(profile)
            backend.load()
            local.send_packet(conn, {})
            while True:
                message, _ = local.receive_packet(conn)
                if message["operation"] == "unload":
                    backend.unload()
                    return
                local.send_packet(conn, infer(backend, root, profile, message))
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

    def stop(self):
        if self.process is not None:
            if self.process.is_alive():
                self.process.kill()
            self.process.join()
            self.process.close()
            self.conn.close()
            self.process = self.conn = None

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
                packet = self.conn.recv(1, socket.MSG_PEEK)
                return {"error": "backend_failed"} if packet else {}
            self.conn.settimeout(self.remaining(deadline))
            reply, _ = local.receive_packet(self.conn)
            if reply.get("error") == "backend_failed":
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
    def __init__(self, root, profile, backend_factory, *, check_idle, sample=sample_card):
        self.lane = local.Lane(root, profile)
        self.profile = profile
        self.model = ModelProcess(root, profile, backend_factory)
        self.sample = sample
        self.check_idle = check_idle
        self.instance = uuid.uuid4().hex
        self.resident = False
        self.receipt = None

    def status(self):
        return {
            **self.sample(self.profile["gpu_uuid"]),
            "profile_sha256": self.lane.profile_hash, "instance": self.instance,
            "resident": self.resident, "receipt": self.receipt,
        }

    def run(self, message, fd, *socket_fds):
        job_id = message.get("id")
        if not local.valid_id(job_id) or message.keys() != {"action", "id", "request"}:
            raise ValueError("Invalid local-model dispatch")
        verify_lease(self.lane.root, fd)
        state = self.lane.read_state()
        job = self.lane.read(job_id)
        request = local.validate(self.profile, message["request"])
        if (state is None or state.get("pending") != job_id or state["instance"] != self.instance
                or state["resident"] != self.resident or job is None or job.get("state") != "running"
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
        except local.UnknownEngineWork as exc:
            outcome = {"error": "engine_work_unknown", "engine_busy": exc.engine_busy}
        except (OSError, HTTPException):
            outcome = {"error": "engine_queue_unavailable"}
        else:
            try:
                outcome = self.model.execute(request, deadline, (fd, *socket_fds))
            except BaseException:
                self.model.stop()
                raise
            if not outcome.get("error"):
                self.resident = operation != "unload"
            elif outcome["error"] in {"operation_timeout", "backend_exited", "backend_failed"}:
                self.resident = False
        self.receipt = {"id": job_id, "operation": operation, **outcome}

    def handle(self, conn, listener_fd=None):
        conn.settimeout(local.STATUS_SECONDS)
        try:
            message, fds = local.receive_packet(conn, max_fds=1)
            try:
                if not fds and message == {"action": "status"}:
                    reply = self.status()
                elif len(fds) == 1 and message.get("action") == "run":
                    socket_fds = (conn.fileno(),) if listener_fd is None else (conn.fileno(), listener_fd)
                    try:
                        self.run(message, fds[0], *socket_fds)
                    except SystemExit:
                        self.model.stop()
                        raise
                    reply = {"id": message["id"]}
                else:
                    raise ValueError("Invalid local-model sidecar call")
            finally:
                # Retain the very same flock until execution and its receipt end,
                # including when the API exits or stops waiting for the result.
                for fd in fds:
                    os.close(fd)
            local.send_packet(conn, reply)
        except (OSError, ValueError, RecursionError, subprocess.SubprocessError):
            # Refuse invalid requests without exposing backend, workspace or input details.
            return


def serve(root, profile, backend_factory):
    """Run one sidecar with no TCP endpoint and a workspace-private Unix socket."""
    card = profile["gpu_uuid"]
    if any(os.environ.get(key) != card for key in ("NVIDIA_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES")):
        raise ValueError("Local-model sidecar must be pinned to its configured GPU UUID")
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
    from api import require_empty_queue
    worker = Sidecar(root, profile, backend_factory, check_idle=require_empty_queue)
    def shutdown(signum, _frame):
        raise SystemExit(128 + signum)

    # PID 1 needs explicit termination handlers. A shutdown during a request
    # reaps the CUDA owner before handle() releases its copy of the lease.
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as listener:
        listener.bind(os.fspath(path))
        try:
            path.chmod(0o600)
            listener.listen(4)
            while True:
                conn, _ = listener.accept()
                with closing(conn):
                    worker.handle(conn, listener.fileno())
        finally:
            worker.model.stop()
            path.unlink(missing_ok=True)
