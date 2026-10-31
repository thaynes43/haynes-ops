"""Bounded asynchronous video API for the isolated, two-GPU ComfyUI worker."""
import csv
import io
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

ENGINE = "http://127.0.0.1:8188"
ROOT = Path(os.environ.get("VIDEO_WORKSPACE", "/workspace"))
CONFIG = Path(os.environ.get("VIDEO_CONFIG", "/config/video"))
EXPECTED = {
    "GPU-d8a856f1-f955-f683-bc24-654561496774",
    "GPU-6ff9702a-1b2c-6094-5a85-00aa1924c55a",
}
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
LOCK = threading.Lock()
ACTIVE = None
UNSAFE = False
PRESETS = {"preview": (832, 480, 49, 20), "720p": (1280, 704, 81, 20)}


def engine(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = Request(ENGINE + path, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


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
            queue = engine("/queue")
            break
        except Exception:
            if time.monotonic() >= deadline:
                UNSAFE = True
                return
            time.sleep(5)
    running = {entry[1] for entry in queue.get("queue_running", [])}
    pending = {entry[1] for entry in queue.get("queue_pending", [])}
    # ComfyUI records our client_id even if its /prompt response was lost.
    for entry in queue.get("queue_running", []) + queue.get("queue_pending", []):
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
            engine("/interrupt", {})
            for _ in range(12):
                if not engine("/queue").get("queue_running"):
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


def generate(job, prompt):
    global ACTIVE, UNSAFE
    started = False
    submission_attempted = False
    try:
        sample = gpu_sample()
        if any(s["utilization_percent"] > 5 or s["memory_mib"] > 22000 for s in sample["gpus"]):
            raise RuntimeError("Rack GPUs are busy; refusing to start a competing job")
        queue = engine("/queue")
        if queue.get("queue_running") or queue.get("queue_pending"):
            raise RuntimeError("ComfyUI already has a job; refusing a competing request")
        graph = make_graph(prompt, job["preset"], job["seed"], job["id"])
        submission_attempted = True
        reply = engine("/prompt", {"prompt": graph, "client_id": job["id"]})
        prompt_id = reply["prompt_id"]
        started = True
        job.update(state="running", started_at=time.time(), prompt_id=prompt_id)
        save(job)
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            job["telemetry"].append(gpu_sample())
            history = engine("/history/" + prompt_id).get(prompt_id)
            if history:
                if history.get("status", {}).get("status_str") == "error":
                    raise RuntimeError("ComfyUI generation failed; inspect the worker log")
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
        raise TimeoutError("Video exceeded the 20-minute job limit")
    except Exception as exc:
        if started or submission_attempted:
            try:
                queue = engine("/queue")
                owned = lambda e: (len(e) > 3 and e[3].get("client_id") == job["id"]) or e[1] == job.get("prompt_id")
                running = queue.get("queue_running", [])
                pending = [e[1] for e in queue.get("queue_pending", []) if owned(e)]
                if pending:
                    engine("/queue", {"delete": pending})
                if running and not all(owned(e) for e in running):
                    UNSAFE = True
                    raise RuntimeError("Unknown backend work requires inspection")
                if running:
                    engine("/interrupt", {})
                for _ in range(12):
                    if not engine("/queue").get("queue_running"):
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
        with LOCK:
            ACTIVE = None


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

    def do_GET(self):
        try:
            if self.path == "/health":
                # Status and completed downloads remain available during a render
                # hold. New submissions are independently rejected when unsafe.
                self.respond(200, {"api": "ok", "accepting_jobs": not UNSAFE, "active_job": ACTIVE})
                return
            if self.path == "/openapi.json":
                self.respond(200, json.loads((CONFIG / "openapi.json").read_text()))
                return
            parts = self.path.strip("/").split("/")
            if len(parts) not in (2, 3) or parts[0] != "videos":
                self.respond(404, {"error": "Not found"})
                return
            job = json.loads(job_path(parts[1]).read_text())
            if len(parts) == 2:
                self.respond(200, job)
            elif parts[2] == "file" and job["state"] == "succeeded":
                path = output_file(job)
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(path.stat().st_size))
                self.send_header("Content-Disposition", 'inline; filename="' + job["id"] + '.mp4"')
                self.end_headers()
                with path.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        self.wfile.write(chunk)
            else:
                self.respond(409, {"error": "Video is not ready"})
        except (FileNotFoundError, ValueError):
            self.respond(404, {"error": "Not found"})
        except Exception:
            self.respond(503, {"error": "Worker unavailable"})

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
                job = {"id": uuid.uuid4().hex, "state": "queued", "preset": preset, "seed": seed,
                       "created_at": time.time(), "telemetry": []}
                save(job)
                ACTIVE = job["id"]
                threading.Thread(target=generate, args=(job, prompt.strip()), daemon=True).start()
            self.respond(202, {"id": job["id"], "state": "queued", "status_url": "/videos/" + job["id"]})
        except (ValueError, TypeError, AttributeError):
            self.respond(400, {"error": "Invalid prompt, preset, seed, or JSON body"})


def main():
    recover()
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    server.timeout = 10
    server.serve_forever()


if __name__ == "__main__":
    main()
