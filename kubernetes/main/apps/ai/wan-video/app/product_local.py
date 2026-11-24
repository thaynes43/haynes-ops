"""Local-model requests and residency, fenced by the existing Wan compute lease."""
from array import array
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import time
import uuid

import product

APPROVED_CARDS = frozenset({
    "GPU-d8a856f1-f955-f683-bc24-654561496774",
    "GPU-6ff9702a-1b2c-6094-5a85-00aa1924c55a",
})
PROFILE_ROUTE = "/v1/local-model/profile"
REQUEST_ROUTE = re.compile(r"/v1/local-model/requests/([0-9a-f]{32})")
REQUEST_ID = re.compile(r"[0-9a-f]{32}")
OPERATIONS = {"load", "infer", "unload"}
MAX_TEXT_BYTES = 32 * 1024
MAX_PACKET = 64 * 1024
STATUS_SECONDS = 15
PROFILE_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}")


def load_profile(config):
    """A missing file disables the routes. Invalid configuration fails closed."""
    try:
        with (config / "local-model.json").open("rb") as source:
            raw = source.read(4097)
    except FileNotFoundError:
        return None
    try:
        if len(raw) > 4096:
            raise ValueError
        profile = json.loads(raw)
        integers = {
            "context_tokens": (2, 262144), "max_output_tokens": (1, 4096),
            "load_timeout_seconds": (1, 300), "infer_timeout_seconds": (1, 300),
            "unload_timeout_seconds": (1, 300), "unloaded_max_mib": (0, 2048),
            "resident_min_mib": (1, 22000), "resident_max_mib": (1, 22000),
        }
        required = integers.keys() | {"model", "gpu_uuid"}
        if (not isinstance(profile, dict) or not required <= profile.keys()
                or profile.keys() - required - {"s2v_gpu_uuid"}):
            raise ValueError
        if (not isinstance(profile["model"], str) or not PROFILE_NAME.fullmatch(profile["model"])
                or profile["gpu_uuid"] not in APPROVED_CARDS
                or ("s2v_gpu_uuid" in profile and profile["s2v_gpu_uuid"] not in APPROVED_CARDS)
                or any(type(profile[key]) is not int or not low <= profile[key] <= high
                       for key, (low, high) in integers.items())
                or profile["max_output_tokens"] >= profile["context_tokens"]
                or not profile["unloaded_max_mib"] < profile["resident_min_mib"] <= profile["resident_max_mib"]):
            raise ValueError
    except (ValueError, TypeError, RecursionError) as exc:
        raise product.RequestError("local_model_misconfigured", "Invalid local-model profile", 503) from exc
    return profile


def validate(profile, data):
    if not isinstance(data, dict) or type(data.get("operation")) is not str or data["operation"] not in OPERATIONS:
        raise product.RequestError("invalid_request", "Choose load, infer or unload")
    operation = data["operation"]
    required = {"operation", "profile"} | ({"prompt"} if operation == "infer" else set())
    optional = {"image_sha256", "max_output_tokens"} if operation == "infer" else set()
    if not required <= data.keys() or data.keys() - required - optional:
        raise product.RequestError("invalid_request", "Request contains missing or unsupported fields")
    if data["profile"] != profile["model"]:
        raise product.RequestError("unsupported_profile", "Use the configured local-model profile")
    if operation != "infer":
        return data
    prompt = data["prompt"]
    if (not isinstance(prompt, str) or not 1 <= len(prompt) <= 4096 or not prompt.strip()
            or any(char == "\0" or 0xD800 <= ord(char) <= 0xDFFF for char in prompt)):
        raise product.RequestError("invalid_request", "Provide 1–4096 characters of prompt text")
    if "image_sha256" in data:
        digest = data["image_sha256"]
        if not isinstance(digest, str) or not product.SHA256.fullmatch(digest):
            raise product.RequestError("invalid_request", "Use an uploaded PNG's SHA-256")
    if "max_output_tokens" in data:
        count = data["max_output_tokens"]
        if type(count) is not int or not 1 <= count <= profile["max_output_tokens"]:
            raise product.RequestError("invalid_request", "Output token limit exceeds the profile")
    return {**data, "prompt": prompt.strip()}


def check_image(root, request):
    if "image_sha256" not in request:
        return None
    digest = request["image_sha256"]
    path = product.input_path(root, digest, "png")
    try:
        if not 0 < path.stat().st_size <= product.MAX_UPLOAD:
            raise product.RequestError("invalid_input", "Staged PNG exceeds the input limit")
        product.verify_existing_input(path, digest)
        product.validate_png(path)
    except FileNotFoundError as exc:
        raise product.RequestError("input_missing", "Upload the PNG first", 404) from exc
    return path


def bounded_text(text):
    if type(text) is not str or len(text) > MAX_TEXT_BYTES:
        return False
    try:
        return len(text.encode("utf-8")) <= MAX_TEXT_BYTES
    except UnicodeError:
        return False


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_record(path, data):
    """Publish a complete record and sync its rename before sending GPU work."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temp:
        staged = Path(temp.name)
        try:
            json.dump(data, temp)
            temp.flush()
            os.fsync(temp.fileno())
            staged.replace(path)
            sync_directory(path.parent)
        finally:
            staged.unlink(missing_ok=True)


def state_path(root):
    return root / "local-model" / "state.json"


def job_path(root, job_id):
    if not REQUEST_ID.fullmatch(job_id):
        raise ValueError("Invalid local-model request ID")
    return root / "local-model" / "requests" / (job_id + ".json")


def socket_path(root):
    return root / "local-model" / "sidecar.sock"


def send_packet(conn, data, fds=()):
    payload = json.dumps(data, ensure_ascii=False).encode()
    if len(payload) > MAX_PACKET:
        raise ValueError("Local-model packet exceeds its size limit")
    ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array('i', fds))] if fds else []
    if conn.sendmsg([payload], ancillary) != len(payload):
        raise OSError("Incomplete local-model packet")


def receive_packet(conn, max_fds=0):
    fds = array("i")
    control_size = socket.CMSG_SPACE(max_fds * fds.itemsize) if max_fds else 0
    payload, control, flags, _ = conn.recvmsg(MAX_PACKET, control_size, socket.MSG_CMSG_CLOEXEC)
    for level, kind, data in control:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            fds.frombytes(data[:len(data) - len(data) % fds.itemsize])
    try:
        if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or len(fds) > max_fds or not payload:
            raise ValueError("Incomplete local-model packet")
        data = json.loads(payload)
        if type(data) is not dict:
            raise ValueError("Expected a local-model packet object")
        return data, fds
    except BaseException:
        for fd in fds:
            os.close(fd)
        raise


def exchange(root, data, lease=None, timeout=STATUS_SECONDS):
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as conn:
        conn.settimeout(timeout)
        conn.connect(os.fspath(socket_path(root)))
        send_packet(conn, data, () if lease is None else (lease.fileno(),))
        reply, _ = receive_packet(conn)
        return reply


def view(job):
    return {**job, "status_url": "/v1/local-model/requests/" + job["id"]}


class UnknownEngineWork(RuntimeError):
    """A queue reply was malformed or showed work outside this lease."""


def busy(message):
    return product.RequestError("worker_busy", message, 409)


def held():
    return product.RequestError("local_model_held", "Local-model residency requires inspection", 503)


class Lane:
    """All state changes require the caller's locked compute.lock descriptor."""
    def __init__(self, root, profile):
        self.root = root
        self.profile = profile
        self.profile_hash = product.digest(profile)
        self.path = state_path(root)

    def read_state(self):
        state = product.read_job(self.path)
        if state is not None and (
            not isinstance(state, dict) or state.get("profile_sha256") != self.profile_hash
            or type(state.get("resident")) is not bool
            or not valid_id(state.get("instance"))
            or ("pending" in state and not valid_id(state["pending"]))
            or state.get("held")
        ):
            raise held()
        return state

    def write_state(self, instance, resident, *, pending=None, hold=False):
        if not (resident or pending or hold):
            if self.path.exists():
                self.path.unlink()
                sync_directory(self.path.parent)
            return
        state = {"profile_sha256": self.profile_hash, "instance": instance, "resident": resident}
        if pending:
            state["pending"] = pending
        if hold:
            state["held"] = True
        write_record(self.path, state)

    def status(self):
        try:
            snapshot = exchange(self.root, {"action": "status"})
            if (snapshot.get("profile_sha256") != self.profile_hash
                    or not valid_id(snapshot.get("instance"))
                    or type(snapshot.get("resident")) is not bool
                    or snapshot.get("gpu_uuid") != self.profile["gpu_uuid"]
                    or not gpu_range(snapshot.get("memory_mib"), 0, 32768)
                    or not gpu_range(snapshot.get("utilization_percent"), 0, 100)
                    or not gpu_range(snapshot.get("temperature_c"), 0, 120)):
                raise ValueError("Invalid local-model status")
        except (OSError, ValueError, RecursionError) as exc:
            raise product.RequestError("local_model_unavailable", "Local-model sidecar state is unavailable", 503) from exc
        return snapshot

    def own_card_idle(self, snapshot):
        return snapshot["utilization_percent"] <= 5

    def unloaded(self, snapshot):
        return self.own_card_idle(snapshot) and snapshot["memory_mib"] <= self.profile["unloaded_max_mib"]

    def resident(self, snapshot):
        return self.own_card_idle(snapshot) and (
            self.profile["resident_min_mib"] <= snapshot["memory_mib"] <= self.profile["resident_max_mib"]
        )

    def read(self, job_id):
        return product.read_job(job_path(self.root, job_id))

    def create(self, job_id, request, *, internal=False):
        job = {
            "id": job_id, "api_version": product.VERSION, "operation": request["operation"],
            "profile": request["profile"], "request_sha256": product.digest(request),
            "state": "queued", "created_at": time.time(),
        }
        if internal:
            job["internal"] = True
        for key in ("image_sha256", "max_output_tokens"):
            if key in request:
                job[key] = request[key]
        path = job_path(self.root, job_id)
        product.create_job(path, job)
        sync_directory(path.parent)
        return job

    def finish(self, job, *, error=None, result=None):
        if job["state"] in {"succeeded", "failed"}:
            return job
        job.update(state="failed" if error else "succeeded", finished_at=time.time())
        if error:
            job["error"] = error
        elif result is not None:
            job["result"] = result
        write_record(job_path(self.root, job["id"]), job)
        return job

    def reconcile(self):
        """Resolve the only possible outstanding dispatch under a newly held lease."""
        state = self.read_state()
        snapshot = self.status()
        instance = snapshot["instance"]
        was_resident = state["resident"] if state else False
        pending = state.get("pending") if state else None
        job = self.read(pending) if pending else None
        if pending and (not job or job.get("id") != pending):
            raise held()

        if state and state["instance"] != instance:
            # GPU resources from a dead sidecar can outlive its process briefly.
            if snapshot["resident"] or not self.unloaded(snapshot):
                if job:
                    self.finish(job, error="residency_unverified")
                self.write_state(instance, was_resident, pending=pending, hold=True)
                raise held()
            if job:
                self.finish(job, error="sidecar_restarted")
            self.write_state(instance, False)
            return snapshot

        expected = was_resident
        receipt = snapshot.get("receipt") if pending else None
        if receipt is not None and type(receipt) is not dict:
            raise held()
        matched = bool(receipt and receipt.get("id") == pending)
        if matched and receipt.get("operation") != job["operation"]:
            raise held()
        error = result = None
        if matched:
            error = receipt.get("error")
            if error is None:
                expected = job["operation"] != "unload"
                if job["operation"] == "infer":
                    result = receipt.get("result")
                    if not valid_result(self.profile, job, result):
                        error = "backend_failed"
            elif error not in ("context_limit", "engine_work_unknown", "engine_queue_unavailable"):
                error = "backend_failed"
        elif pending:
            error = "submission_lost"

        # An unload receipt must agree with the card. Failed or missing dispatches
        # also need memory proof when their pre-dispatch state was unloaded.
        needs_unloaded_proof = bool(pending and not expected)
        memory_ok = self.resident(snapshot) if expected else (
            not needs_unloaded_proof or self.unloaded(snapshot)
        )
        if matched and not error and snapshot["resident"] == expected:
            for _ in range(3):
                if memory_ok:
                    break
                time.sleep(1)
                snapshot = self.status()
                if snapshot["instance"] != instance or snapshot["resident"] != expected:
                    memory_ok = False
                    break
                memory_ok = self.resident(snapshot) if expected else self.unloaded(snapshot)
        unsafe_error = error in ("backend_failed", "engine_work_unknown")
        if snapshot["resident"] != expected or not memory_ok or unsafe_error:
            if job:
                self.finish(job, error=error if unsafe_error else "residency_unverified")
            self.write_state(instance, snapshot["resident"], pending=pending, hold=True)
            raise held()
        if job:
            self.finish(job, error=error, result=result)
        if pending:
            self.write_state(instance, expected)
        return snapshot

    def run(self, job, request, lease, check_idle, *, ensure_load_memory=None, snapshot=None):
        """Send once. A lost transport answer leaves the pre-dispatch record in place."""
        try:
            check_idle()
            snapshot = self.reconcile() if snapshot is None else snapshot
            operation = job["operation"]
            if operation == "infer" and not snapshot["resident"]:
                return self.finish(job, error="model_not_loaded")
            if operation != "infer" and snapshot["resident"] == (operation == "load"):
                return self.finish(job)
            if operation == "load" and not self.unloaded(snapshot) and ensure_load_memory is not None:
                snapshot = ensure_load_memory(self)
            if operation == "load" and not self.unloaded(snapshot):
                return self.finish(job, error="model_card_busy")
            if operation != "unload" and snapshot["temperature_c"] >= 62:
                return self.finish(job, error="model_card_hot")
            check_idle()
        except (OSError, ValueError) as exc:
            return self.finish(job, error=getattr(exc, "code", "local_model_unavailable"))
        job.update(state="running", started_at=time.time())
        write_record(job_path(self.root, job["id"]), job)
        self.write_state(snapshot["instance"], snapshot["resident"], pending=job["id"])
        try:
            exchange(self.root, {"action": "run", "id": job["id"], "request": request},
                     lease, self.profile[operation + "_timeout_seconds"] + STATUS_SECONDS)
            check_idle()
            self.reconcile()
        except (OSError, ValueError, RecursionError):
            # Resolve the journal on a later read/admission after obtaining the
            # compute lease. The sidecar may still have its copy of the lease.
            pass
        return self.read(job["id"])

    def prepare_other(self, cards, lease, check_idle):
        """Resolve outstanding work and evict conflicting residency before admission."""
        check_idle()
        snapshot = self.reconcile()
        if not snapshot["resident"] or self.profile["gpu_uuid"] not in cards:
            return
        request = {"operation": "unload", "profile": self.profile["model"]}
        job = self.create(uuid.uuid4().hex, request, internal=True)
        completed = self.run(job, request, lease, check_idle, snapshot=snapshot)
        if completed["state"] != "succeeded":
            raise busy("Local-model handover did not verify that the card was released")


def enabled_lane(root, config):
    profile = load_profile(config)
    if profile is not None:
        return Lane(root, profile)
    # Disabling configuration cannot discard a previously reserved card.
    if state_path(root).exists():
        raise held()
    return None


def valid_id(value):
    return isinstance(value, str) and REQUEST_ID.fullmatch(value) is not None


def gpu_range(value, low, high):
    return type(value) in (int, float) and low <= value <= high


def valid_result(profile, job, result):
    if type(result) is not dict or result.keys() != {"text", "input_tokens", "output_tokens"}:
        return False
    inputs, outputs = result["input_tokens"], result["output_tokens"]
    cap = job.get("max_output_tokens", profile["max_output_tokens"])
    return (type(inputs) is int and 0 <= inputs <= profile["context_tokens"] - cap
            and type(outputs) is int and 0 <= outputs <= cap and bounded_text(result["text"]))
