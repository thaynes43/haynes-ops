"""Private sidecar adapter for an in-process local model provided by OP442.

The backend must finish all GPU activity before returning from load, infer or
unload. Startup, idle and request preparation must never enqueue GPU work.
The backend must not spawn a process or service with its own GPU lifetime.
"""
from contextlib import closing, contextmanager
import csv
import fcntl
from http.client import HTTPException
import io
import os
import signal
import socket
import stat
import subprocess
import uuid

import product
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


@contextmanager
def operation_deadline(seconds):
    # A native call can hang with the GIL held. The kernel's default SIGALRM
    # action terminates this sidecar even when Python cannot run a watcher.
    handler = signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, handler)


class Sidecar:
    def __init__(self, root, profile, backend_factory, *, check_idle, sample=sample_card, deadline=operation_deadline):
        self.lane = local.Lane(root, profile)
        self.profile = profile
        self.backend_factory = backend_factory
        self.backend = None
        self.sample = sample
        self.check_idle = check_idle
        self.deadline = deadline
        self.instance = uuid.uuid4().hex
        self.resident = False
        self.receipt = None

    def status(self):
        return {
            **self.sample(self.profile["gpu_uuid"]),
            "profile_sha256": self.lane.profile_hash, "instance": self.instance,
            "resident": self.resident, "receipt": self.receipt,
        }

    def execute(self, job, request):
        operation = job["operation"]
        if operation == "load":
            if self.resident:
                raise ValueError("Model is already loaded")
            self.backend = self.backend_factory(self.profile)
            self.backend.load()
            self.resident = True
        elif operation == "unload":
            if not self.resident:
                raise ValueError("Model is already unloaded")
            self.backend.unload()
            self.backend = None
            self.resident = False
        else:
            if not self.resident:
                raise ValueError("Model is not loaded")
            image = local.check_image(self.lane.root, request)
            prepared, inputs = self.backend.prepare(request["prompt"], image)
            cap = request.get("max_output_tokens", self.profile["max_output_tokens"])
            if type(inputs) is not int or inputs < 0:
                raise ValueError("Invalid input token count")
            if inputs + cap > self.profile["context_tokens"]:
                return {"error": "context_limit"}
            text, outputs = self.backend.infer(prepared, cap)
            result = {"text": text, "input_tokens": inputs, "output_tokens": outputs}
            if not local.valid_result(self.profile, job, result):
                raise ValueError("Invalid local-model result")
            return {"result": result}
        return {}

    def run(self, message, fd):
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
        with self.deadline(self.profile[operation + "_timeout_seconds"]):
            try:
                self.check_idle()
            except local.UnknownEngineWork:
                outcome = {"error": "engine_work_unknown"}
            except (OSError, HTTPException):
                outcome = {"error": "engine_queue_unavailable"}
            else:
                try:
                    outcome = self.execute(job, request)
                except Exception:
                    outcome = {"error": "backend_failed"}
            self.receipt = {"id": job_id, "operation": operation, **outcome}

    def handle(self, conn):
        conn.settimeout(local.STATUS_SECONDS)
        try:
            message, fds = local.receive_packet(conn, max_fds=1)
            try:
                if not fds and message == {"action": "status"}:
                    reply = self.status()
                elif len(fds) == 1 and message.get("action") == "run":
                    self.run(message, fds[0])
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
        # A process killed by its watchdog leaves a socket name behind. Never
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
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as listener:
        listener.bind(os.fspath(path))
        try:
            path.chmod(0o600)
            listener.listen(4)
            while True:
                conn, _ = listener.accept()
                with closing(conn):
                    worker.handle(conn)
        finally:
            path.unlink(missing_ok=True)
