"""Bounded asynchronous video API for the isolated, two-GPU ComfyUI worker."""
import csv
import fcntl
import io
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import product
from product_graphs import image_graph, speech_graph

ENGINE = "http://127.0.0.1:8188"
ROOT = Path(os.environ.get("VIDEO_WORKSPACE", "/workspace"))
CONFIG = Path(os.environ.get("VIDEO_CONFIG", "/config/video"))
EXPECTED = {
    "GPU-d8a856f1-f955-f683-bc24-654561496774",
    "GPU-6ff9702a-1b2c-6094-5a85-00aa1924c55a",
}
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
RANGE = re.compile(r"bytes=(\d{0,19})-(\d{0,19})")
LOCK = threading.Lock()
ACTIVE = None
UNSAFE = False
UPLOAD = threading.BoundedSemaphore(1)
FOLEY = "http://127.0.0.1:8189"
PRESETS = {"preview": (832, 480, 49, 20), "720p": (1280, 704, 81, 20)}


def acquire_compute():
    """Cross-process admission shared with the private Foley sidecar."""
    lease = (ROOT / "compute.lock").open("a")
    try:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lease
    except BlockingIOError:
        lease.close()
        return None


def engine(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = Request(ENGINE + path, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=20) as response:
        raw = response.read()
    if raw == b"" and body is not None and path in ("/queue", "/interrupt"):
        return None
    return json.loads(raw)


def foley(path, data=None):
    request = Request(FOLEY + path, data=data, method="PUT" if data is not None else "GET",
                      headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except HTTPError as response:
        with response:
            return response.code, json.load(response)


def engine_queue():
    """Treat an incomplete or malformed queue answer as unknown backend work."""
    queue = engine("/queue")
    if not isinstance(queue, dict) or any(
        key not in queue or not isinstance(queue[key], list)
        or any(
            not isinstance(entry, list) or len(entry) < 2 or not isinstance(entry[1], str)
            or (len(entry) > 3 and (
                not isinstance(entry[3], dict)
                or ("client_id" in entry[3] and not isinstance(entry[3]["client_id"], str))
            ))
            for entry in queue[key]
        )
        for key in ("queue_running", "queue_pending")
    ):
        raise ValueError("Malformed ComfyUI /queue response; backend work is unknown")
    return queue


def interrupt_running(prompt_ids):
    for prompt_id in sorted(prompt_ids):
        engine("/interrupt", {"prompt_id": prompt_id})


def gpu_sample():
    fields = "uuid,memory.used,utilization.gpu,power.draw,temperature.gpu"
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=" + fields, "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10, check=True,
    )
    rows = list(csv.reader(io.StringIO(result.stdout)))
    samples = [{"uuid": r[0].strip(), "memory_mib": float(r[1]),
                "utilization_percent": float(r[2]), "power_w": float(r[3]),
                "temperature_c": float(r[4])} for r in rows]
    if {s["uuid"] for s in samples} != EXPECTED or len(samples) != 2:
        raise RuntimeError("Expected exactly the two approved rack GPU UUIDs")
    return {"time": time.time(), "gpus": samples}


def make_graph(prompt, preset, seed, job_id):
    width, height, frames, steps = PRESETS[preset]
    graph = json.loads((CONFIG / "workflow.json").read_text())
    graph["4"]["inputs"]["text"] = prompt
    graph["8"]["inputs"].update(width=width, height=height, length=frames)
    graph["9"]["inputs"].update(seed=seed, steps=steps)
    graph["12"]["inputs"]["filename_prefix"] = "video/" + job_id
    return graph


def job_path(job_id):
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid video job ID")
    return ROOT / "jobs" / (job_id + ".json")


def save(job):
    path = job_path(job["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(job, indent=2) + "\n")
    temp.replace(path)


def output_file(job):
    base = (ROOT / "output").resolve()
    path = (base / job["output"]).resolve()
    if not path.is_relative_to(base) or path.suffix.lower() != ".mp4":
        raise ValueError("Invalid output file")
    return path


def byte_range(header, size):
    """The (first, last) bytes one Range header asks for, or None to send the whole file.

    A player fetches a video in parts and seeks with Range requests. RFC 9110 lets a
    server ignore a header it does not understand (another unit, a list of ranges, a
    malformed or backwards range, a number of 20 digits or more), so those return None.
    A well-formed range that lies outside the file raises LookupError, which the caller
    answers with 416.
    """
    match = RANGE.fullmatch(header.strip()) if header else None
    if not match or match.groups() == ("", ""):
        return None
    first, last = match.groups()
    if not first:  # "bytes=-N": the last N bytes
        if not size or not int(last):
            raise LookupError("Range not satisfiable")
        return max(size - int(last), 0), size - 1
    first = int(first)
    if last and int(last) < first:
        return None
    if first >= size:
        raise LookupError("Range not satisfiable")
    return first, min(int(last), size - 1) if last else size - 1


def recover():
    """Do not replay jobs or leave an old API-owned render without its watchdog."""
    global UNSAFE
    directory = ROOT / "jobs"
    directory.mkdir(parents=True, exist_ok=True)
    interrupted = []
    recorded_ids = set()
    for path in directory.glob("*.json"):
        job = json.loads(path.read_text())
        recorded_ids.add(job["id"])
        if job.get("state") in ("queued", "running"):
            interrupted.append(job)
    own_ids = {job["prompt_id"] for job in interrupted if job.get("prompt_id")}
    # App and API start together. Wait for the local engine, never submit work.
    deadline = time.monotonic() + 300
    while True:
        try:
            queue = engine_queue()
            break
        except ValueError:
            UNSAFE = True
            return
        except Exception:
            if time.monotonic() >= deadline:
                UNSAFE = True
                return
            time.sleep(5)
    running = {entry[1] for entry in queue["queue_running"]}
    pending = {entry[1] for entry in queue["queue_pending"]}
    # ComfyUI records our client_id even if its /prompt response was lost.
    for entry in queue["queue_running"] + queue["queue_pending"]:
        if len(entry) > 3 and entry[3].get("client_id") in recorded_ids:
            own_ids.add(entry[1])
    if (running | pending) - own_ids:
        # Unknown work can occur if /prompt was accepted just before a crash.
        # Preserve it and require inspection rather than cancelling by guesswork.
        UNSAFE = True
        return
    try:
        if pending:
            engine("/queue", {"delete": sorted(pending)})
        if running:
            interrupt_running(running)
            for _ in range(12):
                if not engine_queue()["queue_running"]:
                    break
                time.sleep(5)
            else:
                UNSAFE = True
                return
        for job in interrupted:
            job.update(state="failed", error="Worker restarted; job was not replayed", finished_at=time.time())
            save(job)
    except Exception:
        UNSAFE = True


class PromptRejected(RuntimeError):
    """The engine replied to the submission with an explicit client error."""


def submit_product_prompt(job, graph):
    # The installed engine accepts canonical UUIDs on /prompt. Record the
    # UUID and graph before the one submission, so history can settle a
    # lost acknowledgment even if the queue has already drained.
    job.update(prompt_id=str(uuid.uuid4()), graph_sha256=product.digest(graph))
    save(job)
    body = {"prompt": graph, "client_id": job["id"], "prompt_id": job["prompt_id"]}
    try:
        response = engine("/prompt", body)
    except HTTPError as exc:
        if 400 <= exc.code < 500:
            raise PromptRejected(f"ComfyUI rejected the prompt (HTTP {exc.code})") from exc
        return False
    except (OSError, ValueError, HTTPException):
        return False
    if not isinstance(response, dict) or response.get("prompt_id") != job["prompt_id"]:
        raise RuntimeError("ComfyUI did not acknowledge the requested prompt UUID")
    return True


def observe_product_queue(job):
    global UNSAFE
    try:
        queue = engine_queue()
    except ValueError:
        UNSAFE = True
        raise
    ids = {entry[1] for key in ("queue_running", "queue_pending") for entry in queue[key]}
    foreign = ids - {job["prompt_id"]}
    if foreign:
        UNSAFE = True
        job["unexpected_work_seen"] = sorted(foreign | set(job.get("unexpected_work_seen", [])))
    return job["prompt_id"] in ids


def store_product_video(job, history):
    node = "12" if job["type"] == "i2v" else "15"
    images = history.get("outputs", {}).get(node, {}).get("images")
    if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
        raise RuntimeError("ComfyUI completed without the expected MP4")
    item = images[0]
    name = item.get("filename")
    if (item.get("subfolder") != "video" or item.get("type") != "output" or not isinstance(name, str)
            or not re.fullmatch(job["id"] + r"_\d+_\.mp4", name)):
        raise RuntimeError("ComfyUI returned an unexpected output name")
    job["output"] = "video/" + name
    result = product.result(output_file(job), "video/mp4")
    job.update(result=result, bytes=result["bytes"], state="succeeded", finished_at=time.time())
    save(job)


def generate(job, prompt, lease=None, *, negative=None):
    global ACTIVE, UNSAFE
    kind = job.get("type") if job.get("api_version") == product.VERSION else None
    acknowledged = False
    submission_attempted = False
    try:
        if lease is None:
            lease = acquire_compute()
        if lease is None:
            raise RuntimeError("Compute is reserved by another video/audio job")
        sample = gpu_sample()
        if any(s["utilization_percent"] > 5 or s["memory_mib"] > 22000 for s in sample["gpus"]):
            raise RuntimeError("Rack GPUs are busy; refusing to start a competing job")
        if kind and any(s["temperature_c"] >= 62 for s in sample["gpus"]):
            raise RuntimeError("Rack GPUs must be below 62 C for a product job")
        queue = engine_queue()
        if queue["queue_running"] or queue["queue_pending"]:
            raise RuntimeError("ComfyUI already has a job; refusing a competing request")
        timeout = product.PROFILES[kind]["timeout_seconds"] if kind else 1200
        if kind == "i2v":
            graph = image_graph(CONFIG / "workflow.json", job, prompt, negative)
        elif kind == "s2v":
            graph = speech_graph(job, prompt, negative)
        else:
            graph = make_graph(prompt, job["preset"], job["seed"], job["id"])
        submission_attempted = True
        if kind:
            acknowledged = submit_product_prompt(job, graph)
            prompt_id = job["prompt_id"]
        else:
            reply = engine("/prompt", {"prompt": graph, "client_id": job["id"]})
            prompt_id = job["prompt_id"] = reply["prompt_id"]
            acknowledged = True
        if acknowledged:
            job.update(state="running", started_at=time.time())
            save(job)
        deadline = time.monotonic() + timeout
        misses, first_miss = 0, None
        while time.monotonic() < deadline:
            job["telemetry"].append(gpu_sample())
            try:
                if kind and observe_product_queue(job):
                    acknowledged = True
                history = engine("/history/" + prompt_id).get(prompt_id)
                acknowledged = acknowledged or bool(history)
                misses, first_miss = 0, None
            except (OSError, HTTPException):
                # ComfyUI can stall a read past the 20 s HTTP timeout while it decodes a long
                # S2V clip. That is not a failed job: keep polling (the deadline still applies).
                # Give up after 12 misses in a row or 90 s since the first of them, whichever
                # comes first, so a dead ComfyUI does not hold the compute lease for long.
                misses += 1
                if first_miss is None:
                    first_miss = time.monotonic()
                job["poll_misses"] = job.get("poll_misses", 0) + 1
                if kind:
                    save(job)
                if misses >= 12 or time.monotonic() - first_miss >= 90:
                    raise
                time.sleep(5)
                continue
            if acknowledged and job["state"] != "running":
                job.update(state="running", started_at=time.time())
            if history:
                if history.get("status", {}).get("status_str") == "error":
                    raise RuntimeError("ComfyUI generation failed; inspect the worker log")
                if kind:
                    store_product_video(job, history)
                    return
                for output in history.get("outputs", {}).values():
                    for value in output.values():
                        if not isinstance(value, list):
                            continue
                        for item in value:
                            if isinstance(item, dict) and item.get("filename", "").endswith(".mp4"):
                                job["output"] = str(Path(item.get("subfolder", "")) / item["filename"])
                                path = output_file(job)
                                if not path.is_file() or path.stat().st_size == 0:
                                    raise RuntimeError("Generated file is missing or empty")
                                job.update(state="succeeded", finished_at=time.time(), bytes=path.stat().st_size)
                                save(job)
                                return
                raise RuntimeError("ComfyUI completed without an MP4")
            save(job)
            time.sleep(5)
        raise TimeoutError(f"Video exceeded the {timeout // 60}-minute job limit")
    except Exception as exc:
        definite_rejection = isinstance(exc, PromptRejected)
        if kind and submission_attempted and not acknowledged and not definite_rejection:
            UNSAFE = True
        if submission_attempted and not definite_rejection:
            try:
                queue = engine_queue()
                owned = lambda e: (len(e) > 3 and e[3].get("client_id") == job["id"]) or e[1] == job.get("prompt_id")
                running = queue["queue_running"]
                pending = [e[1] for e in queue["queue_pending"] if owned(e)]
                if kind and len(pending) != len(queue["queue_pending"]):
                    UNSAFE = True
                if pending:
                    engine("/queue", {"delete": pending})
                if running and not all(owned(e) for e in running):
                    UNSAFE = True
                    raise RuntimeError("Unknown backend work requires inspection")
                if running:
                    interrupt_running(entry[1] for entry in running)
                for _ in range(12):
                    queue = engine_queue()
                    if not queue["queue_running"] and (not kind or not queue["queue_pending"]):
                        break
                    if kind and any(not owned(entry) for key in ("queue_running", "queue_pending")
                                    for entry in queue[key]):
                        UNSAFE = True
                        break
                    time.sleep(5)
                else:
                    UNSAFE = True
            except Exception:
                UNSAFE = True
        # Do not persist upstream response bodies or prompt content in error messages.
        job.update(state="failed", error=type(exc).__name__ + ": " + str(exc)[:200], finished_at=time.time())
        save(job)
    finally:
        if lease is not None:
            lease.close()
        with LOCK:
            if ACTIVE == job["id"]:
                ACTIVE = None


def submit_product_video(kind, job_id, request):
    global ACTIVE
    path = job_path(job_id)
    request_hash = product.digest(request)
    with LOCK:
        existing = product.read_job(path)
        if product.replay(existing, kind, request_hash):
            return 200, product.job_view(existing)
        if ACTIVE or UNSAFE:
            raise product.RequestError("worker_busy", "Video worker is busy or requires inspection", 409)
        product.check_video_inputs(ROOT, kind, request)
        lease = acquire_compute()
        if lease is None:
            raise product.RequestError("worker_busy", "Compute is reserved by another video/audio job", 409)
        job = {key: value for key, value in request.items() if key not in ("prompt", "negative_prompt")}
        job.update(id=job_id, type=kind, api_version=product.VERSION, request_sha256=request_hash,
                   state="queued", created_at=time.time(), telemetry=[])
        if kind == "s2v":
            job["frames"] = product.s2v_length(request["board_frames"])
        started = False
        try:
            product.create_job(path, job)
            ACTIVE = job_id
            accepted = product.job_view(job)
            try:
                threading.Thread(target=generate, args=(job, request["prompt"], lease),
                                 kwargs={"negative": request["negative_prompt"]}, daemon=True).start()
            except RuntimeError:
                job.update(state="failed", error="Could not start worker", finished_at=time.time())
                save(job)
                return 503, product.job_view(job)
            started = True
            return 202, accepted
        finally:
            if not started:
                lease.close()
                if ACTIVE == job_id:
                    ACTIVE = None


def foley_job_path(job_id):
    return ROOT / "foley-jobs" / (job_id + ".json")


def submit_product_foley(path, job_id, request):
    existing = product.read_job(foley_job_path(job_id))
    if product.replay(existing, "foley", product.digest(request)):
        return 200, product.job_view(existing)
    return foley(path, json.dumps(request).encode())


def product_job(kind, job_id):
    path = foley_job_path(job_id) if kind == "foley" else job_path(job_id)
    job = product.read_job(path)
    if not job or job.get("type") != kind or job.get("api_version") != product.VERSION:
        raise product.RequestError("job_not_found", "Job not found", 404)
    return job


def foley_output(job):
    base = (ROOT / "output" / "foley").resolve()
    expected = base / job["id"] / "output.wav"
    path = (ROOT / "output" / job["output"]).resolve()
    if path != expected:
        raise ValueError("Invalid Foley output file")
    return path


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, fmt, *args):
        # No request bodies, prompts, query strings, or credentials in access logs.
        return

    def respond(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, source, job_id, media_type="video/mp4", suffix=".mp4"):
        """Answer with the open media file, whole or the one byte range asked for."""
        size = os.fstat(source.fileno()).st_size
        # If-Range asks for the range only while a validator still matches. This server
        # sends no validator, so none can match and the whole file is the answer.
        asked = None if self.headers.get("If-Range") else self.headers.get("Range")
        try:
            span = byte_range(asked, size)
        except LookupError:
            self.send_response(416)
            self.send_header("Content-Range", "bytes */" + str(size))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        first, last = span or (0, size - 1)
        self.send_response(206 if span else 200)
        self.send_header("Content-Type", media_type)
        self.send_header("Accept-Ranges", "bytes")
        if span:
            self.send_header("Content-Range", "bytes " + str(first) + "-" + str(last) + "/" + str(size))
        self.send_header("Content-Length", str(last - first + 1))
        self.send_header("Content-Disposition", 'inline; filename="' + job_id + suffix + '"')
        self.end_headers()
        # The answer has started: from here a fault ends the stream short, do_GET sends
        # no second answer into it, and the connection is not used again.
        self.streaming = True
        self.close_connection = True
        source.seek(first)
        count = last - first + 1
        while count > 0 and (chunk := source.read(min(1024 * 1024, count))):
            try:
                self.wfile.write(chunk)
            except (ConnectionError, TimeoutError):
                return  # The player hung up or stopped reading: how it seeks and how it stops.
            count -= len(chunk)

    def get_product(self):
        if self.path == "/v1/profiles":
            self.respond(200, {"api_version": product.VERSION, "profiles": product.PROFILES})
            return
        if self.path == "/v1/ready":
            if UNSAFE:
                raise product.RequestError("worker_held", "Render hold requires inspection", 503)
            engine("/system_stats")
            gpu_sample()
            status, audio = foley("/v1/ready")
            if status != 200 or audio.get("api_version") != product.VERSION:
                raise product.RequestError("worker_unavailable", "Foley product worker is unavailable", 503)
            self.respond(200, {"api_version": product.VERSION, "ready": True, "active_video_job": ACTIVE,
                               "active_foley_job": audio["active_job"]})
            return
        match = product.JOB_ROUTE.fullmatch(self.path)
        if not match:
            raise product.RequestError("not_found", "Not found", 404)
        kind, job_id, file_route = match.groups()
        job = product_job(kind, job_id)
        if not file_route:
            self.respond(200, product.job_view(job))
            return
        if job["state"] != "succeeded":
            raise product.RequestError("result_unavailable", "Job has no completed result", 409)
        if kind == "foley":
            with foley_output(job).open("rb") as source:
                self.send_file(source, job_id, "audio/wav", ".wav")
        else:
            with output_file(job).open("rb") as source:
                self.send_file(source, job_id)

    def put_input(self, sha256, suffix):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if not 0 < size <= product.MAX_UPLOAD or self.headers.get("Transfer-Encoding"):
            raise product.RequestError("invalid_input", "Provide 1–19,999,999 bytes with a Content-Length")
        if not UPLOAD.acquire(blocking=False):
            raise product.RequestError("upload_busy", "Another input upload is active", 409)
        try:
            created = product.stage_input(ROOT, sha256, suffix, self.rfile, size)
        finally:
            UPLOAD.release()
        return (201 if created else 200), {"sha256": sha256, "bytes": size}

    def do_PUT(self):
        try:
            match = product.INPUT_ROUTE.fullmatch(self.path)
            if match:
                status, body = self.put_input(*match.groups())
            else:
                match = product.JOB_ROUTE.fullmatch(self.path)
                if not match or match[3]:
                    raise product.RequestError("not_found", "Not found", 404)
                kind, job_id, _ = match.groups()
                request = product.validate(kind, product.read_json(self))
                if kind == "foley":
                    status, body = submit_product_foley(self.path, job_id, request)
                else:
                    status, body = submit_product_video(kind, job_id, request)
        except product.RequestError as exc:
            status, body = exc.status, exc.body()
        except Exception:
            status, body = 503, {"code": "worker_unavailable", "error": "Worker unavailable"}
        self.respond(status, body)

    def do_GET(self):
        self.streaming = False
        try:
            if self.path.startswith("/v1/"):
                self.get_product()
                return
            if self.path == "/health":
                # Status and completed downloads remain available during a render
                # hold. New submissions are independently rejected when unsafe.
                self.respond(200, {"api": "ok", "accepting_jobs": not UNSAFE, "active_job": ACTIVE})
                return
            if self.path == "/ready":
                if UNSAFE:
                    self.respond(503, {"error": "Render hold requires inspection"})
                    return
                engine("/system_stats")
                gpu_sample()
                self.respond(200, {"ready": True})
                return
            if self.path == "/openapi.json":
                self.respond(200, json.loads((CONFIG / "openapi.json").read_text()))
                return
            parts = self.path.strip("/").split("/")
            if len(parts) not in (2, 3) or parts[0] != "videos":
                self.respond(404, {"error": "Not found"})
                return
            job = json.loads(job_path(parts[1]).read_text())
            if job.get("api_version") == product.VERSION:
                self.respond(404, {"error": "Not found"})
                return
            if len(parts) == 2:
                self.respond(200, job)
            elif parts[2] == "file" and job["state"] == "succeeded":
                with output_file(job).open("rb") as source:
                    self.send_file(source, job["id"])
            else:
                self.respond(409, {"error": "Video is not ready"})
        except product.RequestError as exc:
            self.respond(exc.status, exc.body())
        except (FileNotFoundError, ValueError):
            if self.streaming:
                raise
            body = {"error": "Not found"}
            if self.path.startswith("/v1/"):
                body["code"] = "not_found"
            self.respond(404, body)
        except Exception:
            if self.streaming:
                raise
            body = {"error": "Worker unavailable"}
            if self.path.startswith("/v1/"):
                body["code"] = "worker_unavailable"
            self.respond(503, body)

    def do_POST(self):
        global ACTIVE
        if self.path != "/videos":
            self.respond(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                raise ValueError("Body must be 1–8192 bytes")
            data = json.loads(self.rfile.read(length))
            prompt = data.get("prompt")
            preset = data.get("preset", "preview")
            seed = data.get("seed", 43)
            if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 4096:
                raise ValueError("Provide a prompt of 1–4096 characters")
            if preset not in PRESETS or type(seed) is not int or not 0 <= seed <= 2**53 - 1:
                raise ValueError("Invalid preset or seed")
            with LOCK:
                if ACTIVE or UNSAFE:
                    self.respond(409, {"error": "Video worker is busy or requires inspection", "active_job": ACTIVE})
                    return
                lease = acquire_compute()
                if lease is None:
                    self.respond(409, {"error": "Compute reserved by another video/audio job"})
                    return
                try:
                    job = {"id": uuid.uuid4().hex, "state": "queued", "preset": preset, "seed": seed,
                           "created_at": time.time(), "telemetry": []}
                    save(job)
                    ACTIVE = job["id"]
                    threading.Thread(target=generate, args=(job, prompt.strip(), lease), daemon=True).start()
                except Exception:
                    lease.close()
                    ACTIVE = None
                    raise
            self.respond(202, {"id": job["id"], "state": "queued", "status_url": "/videos/" + job["id"]})
        except (ValueError, TypeError, AttributeError):
            self.respond(400, {"error": "Invalid prompt, preset, seed, or JSON body"})
        except Exception:
            self.respond(503, {"error": "Worker unavailable"})


def main():
    recover()
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    server.timeout = 10
    server.serve_forever()


if __name__ == "__main__":
    main()
