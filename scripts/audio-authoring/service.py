#!/usr/bin/env python3
"""Bounded, offline Stable Audio 3 SFX job service with native HTTP MCP."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import unicodedata
import uuid

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse, StreamingResponse
import uvicorn


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("audio-authoring")
INSTALL = Path(__file__).resolve().parent
TEST_MODE = (INSTALL / "TEST_IMAGE").is_file() and os.environ.get("AUDIO_AUTHORING_TEST_MODE") == "1"
MODEL_ROOT = Path(os.environ["AUDIO_AUTHORING_TEST_MODELS"] if TEST_MODE else "/models")
WORKSPACE = Path(os.environ["AUDIO_AUTHORING_TEST_WORKSPACE"] if TEST_MODE else "/workspace")
UPSTREAM = INSTALL / "stable-audio-3" / "optimized" / "tflite"
UPSTREAM_SCRIPT = UPSTREAM / "scripts" / "sa3_tflite.py"
FIXTURE_SCRIPT = INSTALL / "fixture_backend.py"
WEIGHTS_REPOSITORY = "stabilityai/stable-audio-3-optimized"
WEIGHTS_REVISION = "da6edc54ddba10bfd79a077102ded687f80e882b"
SOURCE_REPOSITORY = "Stability-AI/stable-audio-3"
SOURCE_REVISION = "779434a908193105335fd8d833418603625b2859"
SERVICE_VERSION = "0.1.1"
MODEL_FILES = (
    "tflite/t5gemma/encoder_fp16.tflite",
    "tflite/sa3-sm-sfx/dit_fp32.tflite",
    "tflite/same-s/dec_w8a8.tflite",
)
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
QUEUE_CAPACITY = 4
GENERATION_TIMEOUT_SECONDS = 60 * 60
MODEL = {
    "name": "Stable Audio 3 Small SFX",
    "source_repository": SOURCE_REPOSITORY,
    "source_revision": SOURCE_REVISION,
    "weights_repository": WEIGHTS_REPOSITORY,
    "weights_revision": WEIGHTS_REVISION,
    "device": "cpu",
    "runtime": "LiteRT/XNNPACK",
    "dit": "sm-sfx",
    "decoder": "same-s",
    "dit_precision": "fp32",
    "decoder_precision": "w8a8",
    "threads": 4,
}
ARTIFACT_TYPES = {".wav": "audio/wav", ".json": "application/json"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def elapsed_seconds(job: dict[str, object]) -> float | None:
    started = job.get("started_at")
    if not isinstance(started, str):
        return None
    end = job.get("finished_at") if isinstance(job.get("finished_at"), str) else now()
    try:
        start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return round(max(0.0, (end_dt - start_dt).total_seconds()), 3)
    except ValueError:
        return None


def validate_relative(relative: str, suffixes: set[str] | None = None) -> list[str]:
    parts = relative.split("/")
    if not parts or any(
        not part or part.startswith(".") or "\\" in part or "\x00" in part
        for part in parts
    ):
        raise ValueError("Invalid path")
    if suffixes is not None and Path(parts[-1]).suffix.lower() not in suffixes:
        raise ValueError("Unsupported file type")
    return parts


def open_regular(root: Path, relative: str, suffixes: set[str] | None = None,
                 require_single_link: bool = True):
    """Open beneath root by descriptors without following any symlink."""
    parts = validate_relative(relative, suffixes)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (require_single_link and info.st_nlink != 1):
            os.close(descriptor)
            raise ValueError("File must be a regular file")
        return os.fdopen(descriptor, "rb"), info
    finally:
        os.close(directory)


def hash_stream(stream) -> str:
    digest = hashlib.sha256()
    while block := stream.read(8 * 1024 * 1024):
        digest.update(block)
    return digest.hexdigest()


def verify_model_bundle() -> dict[str, object]:
    """Verify the prefetch attestation and every byte before declaring readiness."""
    stream, info = open_regular(MODEL_ROOT, "manifest.json", {".json"})
    with stream:
        if info.st_size > 64 * 1024:
            raise RuntimeError("Model manifest is unexpectedly large")
        manifest = json.load(stream)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise RuntimeError("Model manifest schema is invalid")
    if manifest.get("repository") != WEIGHTS_REPOSITORY:
        raise RuntimeError("Model manifest repository does not match the runtime")
    if manifest.get("revision") != WEIGHTS_REVISION:
        raise RuntimeError("Model manifest revision does not match the runtime")
    records = manifest.get("files")
    if not isinstance(records, list) or len(records) != len(MODEL_FILES):
        raise RuntimeError("Model manifest file list is invalid")
    by_path = {record.get("path"): record for record in records if isinstance(record, dict)}
    if set(by_path) != set(MODEL_FILES):
        raise RuntimeError("Model manifest does not contain the exact SFX bundle")
    verified = []
    for relative in MODEL_FILES:
        record = by_path[relative]
        expected_size = record.get("size")
        expected_hash = record.get("sha256")
        if (not isinstance(expected_size, int) or expected_size <= 0 or
                not isinstance(expected_hash, str) or not SHA256.fullmatch(expected_hash)):
            raise RuntimeError(f"Model manifest record is invalid: {relative}")
        stream, model_info = open_regular(MODEL_ROOT, relative, {".tflite"}, False)
        with stream:
            if model_info.st_size != expected_size:
                raise RuntimeError(f"Model size differs from manifest: {relative}")
            if not TEST_MODE and model_info.st_size < 1024 * 1024:
                raise RuntimeError(f"Model is too small to be a TFLite graph: {relative}")
            actual_hash = hash_stream(stream)
        if not secrets.compare_digest(actual_hash, expected_hash):
            raise RuntimeError(f"Model checksum differs from manifest: {relative}")
        verified.append({"path": relative, "size": expected_size, "sha256": expected_hash})
    return {"repository": WEIGHTS_REPOSITORY, "revision": WEIGHTS_REVISION, "files": verified}


def validate_prompt(prompt: str) -> str:
    if not isinstance(prompt, str):
        raise ValueError("prompt must be text")
    prompt = prompt.strip()
    if not prompt or len(prompt) > 500 or len(prompt.encode("utf-8")) > 2000:
        raise ValueError("prompt must contain 1 to 500 characters and at most 2000 UTF-8 bytes")
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in prompt):
        raise ValueError("prompt must not contain control or surrogate characters")
    return prompt


def validate_parameters(seconds: float, steps: int, seed: int | None) -> tuple[float, int, int]:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
        raise ValueError("seconds must be a finite number")
    seconds = float(seconds)
    if not 5 <= seconds <= 30:
        raise ValueError("seconds must be between 5 and 30")
    if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= 16:
        raise ValueError("steps must be an integer between 1 and 16")
    if seed is None:
        seed = secrets.randbelow(2**31)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**31:
        raise ValueError("seed must be an integer between 0 and 2147483647")
    return seconds, steps, seed


def atomic_write_job(job: dict[str, object]) -> None:
    directory = WORKSPACE / "jobs" / str(job["id"])
    temporary = directory / "job.json.tmp"
    target = directory / "job.json"
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(job, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)


def read_job(relative: str) -> dict[str, object]:
    stream, info = open_regular(WORKSPACE, relative, {".json"})
    with stream:
        if info.st_size > 64 * 1024:
            raise ValueError("Job metadata is unexpectedly large")
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("Job metadata is invalid")
    return value


def read_peak_rss(pid: int) -> int:
    """Return Linux VmHWM for the fixed generation subprocess, in bytes."""
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as stream:
            fields = {}
            for line in stream:
                if line.startswith(("VmHWM:", "VmRSS:")):
                    name, value, unit = line.split()
                    if unit != "kB":
                        continue
                    fields[name.rstrip(":")] = int(value) * 1024
        return max(fields.values(), default=0)
    except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
        return 0


def verify_wave(path: Path, relative: str, requested_seconds: float) -> dict[str, object]:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    probe = json.loads(completed.stdout)
    streams = probe.get("streams", [])
    if len(streams) != 1 or streams[0].get("codec_type") != "audio":
        raise RuntimeError("Generated artifact does not contain one audio stream")
    stream = streams[0]
    if stream.get("codec_name") != "pcm_s16le" or stream.get("sample_rate") != "44100":
        raise RuntimeError("Generated artifact is not 44.1 kHz 16-bit PCM")
    if stream.get("channels") != 2:
        raise RuntimeError("Generated artifact is not stereo")
    duration = float(probe["format"]["duration"])
    if abs(duration - requested_seconds) > 0.05:
        raise RuntimeError("Generated artifact duration differs from the request")
    artifact, artifact_info = open_regular(WORKSPACE, relative, {".wav"})
    with artifact:
        sha256 = hash_stream(artifact)
    return {
        "codec": "pcm_s16le", "sample_rate": 44100, "channels": 2,
        "duration_seconds": round(duration, 3), "size_bytes": artifact_info.st_size,
        "sha256": sha256,
    }


def write_process_log(identifier: str, data: bytes) -> None:
    """Persist only a bounded diagnostic tail; it is never an HTTP artifact."""
    path = WORKSPACE / "jobs" / identifier / "generation.log"
    with path.open("wb") as stream:
        stream.write(data[-16 * 1024:])
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o600)


def diagnostic_text(data: bytes) -> str:
    decoded = data[-4096:].decode("utf-8", errors="replace")
    return "".join(character if character in "\n\t" or character.isprintable() else "?"
                   for character in decoded).strip()


class Runtime:
    def __init__(self):
        self.jobs: dict[str, dict[str, object]] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_CAPACITY)
        self.lock = asyncio.Lock()
        self.worker: asyncio.Task | None = None
        self.model_watcher: asyncio.Task | None = None
        self.current_process: asyncio.subprocess.Process | None = None
        self.current_job: str | None = None
        self.model_manifest: dict[str, object] | None = None
        self.model_error: str | None = None
        self.stopping = False

    async def start(self) -> None:
        os.umask(0o077)
        (WORKSPACE / "jobs").mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._recover_jobs)
        try:
            self.model_manifest = await asyncio.to_thread(verify_model_bundle)
            self.model_error = None
            logger.info("verified pinned Stable Audio model bundle (%d files)", len(MODEL_FILES))
        except Exception as error:
            self.model_manifest = None
            self.model_error = str(error)
            logger.error("model bundle is not ready: %s", error)
        self.worker = asyncio.create_task(self._worker(), name="audio-generation-worker")
        self.model_watcher = asyncio.create_task(self._watch_models(), name="audio-model-watcher")

    def _recover_jobs(self) -> None:
        for entry in os.scandir(WORKSPACE / "jobs"):
            if not entry.is_dir(follow_symlinks=False) or not JOB_ID.fullmatch(entry.name):
                continue
            try:
                job = read_job(f"jobs/{entry.name}/job.json")
                if job.get("id") != entry.name or job.get("status") not in TERMINAL | {"queued", "running"}:
                    raise ValueError("Invalid job identity or state")
                self.jobs[entry.name] = job
                if job["status"] in {"queued", "running"}:
                    job["status"] = "interrupted"
                    job["finished_at"] = now()
                    job["error"] = "service restarted before generation completed"
                    atomic_write_job(job)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                logger.warning("ignoring invalid persisted job %s: %s", entry.name, error)

    def ready(self) -> bool:
        return (
            self.model_manifest is not None and self.worker is not None and
            not self.worker.done() and not self.stopping
        )

    async def _watch_models(self) -> None:
        """Notice an atomically published prefetch manifest without restarting."""
        last_signature = None
        if self.model_manifest is not None:
            try:
                initial = (MODEL_ROOT / "manifest.json").stat()
                last_signature = (initial.st_ino, initial.st_size, initial.st_mtime_ns)
            except OSError:
                pass
        while True:
            try:
                info = (MODEL_ROOT / "manifest.json").stat()
                signature = (info.st_ino, info.st_size, info.st_mtime_ns)
            except OSError:
                signature = None
            if signature is None and last_signature is not None:
                last_signature = None
                async with self.lock:
                    self.model_manifest = None
                    self.model_error = "model manifest disappeared"
                logger.error("model manifest disappeared; readiness withdrawn")
            if signature is not None and signature != last_signature:
                last_signature = signature
                try:
                    manifest = await asyncio.to_thread(verify_model_bundle)
                    async with self.lock:
                        self.model_manifest = manifest
                        self.model_error = None
                    logger.info("new pinned Stable Audio model bundle is ready")
                except Exception as error:
                    async with self.lock:
                        self.model_manifest = None
                        self.model_error = str(error)
                    logger.error("new model manifest failed verification: %s", error)
            await asyncio.sleep(2)

    def _public(self, job: dict[str, object]) -> dict[str, object]:
        result = dict(job)
        elapsed = elapsed_seconds(job)
        if elapsed is not None:
            result["elapsed_seconds"] = elapsed
        if job.get("status") == "completed":
            result["download_url"] = f"/artifacts/{job['id']}/output.wav"
        result.setdefault("model", {"provenance": "not recorded by the originating runtime"})
        if TEST_MODE:
            result["test_backend"] = "deterministic fixture; NOT real generation proof"
        return result

    async def submit(self, prompt: str, seconds: float, steps: int,
                     seed: int | None) -> dict[str, object]:
        prompt = validate_prompt(prompt)
        seconds, steps, seed = validate_parameters(seconds, steps, seed)
        async with self.lock:
            if not self.ready():
                raise RuntimeError(f"model is not ready: {self.model_error or 'worker unavailable'}")
            if self.queue.full():
                raise RuntimeError(f"generation queue is full (maximum {QUEUE_CAPACITY} waiting jobs)")
            identifier = uuid.uuid4().hex
            directory = WORKSPACE / "jobs" / identifier
            directory.mkdir(mode=0o700)
            job = {
                "schema_version": 1,
                "service_version": SERVICE_VERSION,
                "model": dict(MODEL),
                "id": identifier,
                "status": "queued",
                "prompt": prompt,
                "seconds": seconds,
                "steps": steps,
                "seed": seed,
                "created_at": now(),
                "started_at": None,
                "finished_at": None,
                "peak_subprocess_rss_bytes": 0,
                "artifact": None,
                "audio": None,
                "error": None,
            }
            self.jobs[identifier] = job
            atomic_write_job(job)
            self.queue.put_nowait(identifier)
            return self._public(job)

    async def get(self, identifier: str) -> dict[str, object]:
        if not JOB_ID.fullmatch(identifier):
            raise ValueError("job_id must be a 32-character lowercase hexadecimal ID")
        async with self.lock:
            if identifier not in self.jobs:
                raise ValueError("generation job was not found")
            return self._public(self.jobs[identifier])

    async def list(self, limit: int) -> list[dict[str, object]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        async with self.lock:
            jobs = sorted(self.jobs.values(), key=lambda item: str(item.get("created_at", "")), reverse=True)
            return [self._public(job) for job in jobs[:limit]]

    async def cancel(self, identifier: str) -> dict[str, object]:
        if not JOB_ID.fullmatch(identifier):
            raise ValueError("job_id must be a 32-character lowercase hexadecimal ID")
        process = None
        async with self.lock:
            job = self.jobs.get(identifier)
            if job is None:
                raise ValueError("generation job was not found")
            if job["status"] in TERMINAL:
                return self._public(job)
            job["status"] = "cancelled"
            job["finished_at"] = now()
            job["error"] = None
            atomic_write_job(job)
            if self.current_job == identifier:
                process = self.current_process
        if process is not None:
            await self._terminate(process)
        return await self.get(identifier)

    def _command(self, job: dict[str, object], output: Path) -> list[str]:
        script = FIXTURE_SCRIPT if TEST_MODE else UPSTREAM_SCRIPT
        if not TEST_MODE and not script.is_file():
            raise RuntimeError("Pinned Stable Audio inference script is missing")
        return [
            os.environ.get("PYTHON", os.sys.executable), str(script),
            "--dit", "sm-sfx",
            "--decoder", "same-s",
            "--dit-precision", "fp32",
            "--decoder-precision", "w8a8",
            "--threads", "4",
            "--seconds", str(job["seconds"]),
            "--steps", str(job["steps"]),
            "--seed", str(job["seed"]),
            "--prompt=" + str(job["prompt"]),
            "--out", str(output),
        ]

    async def _worker(self) -> None:
        while True:
            identifier = await self.queue.get()
            try:
                async with self.lock:
                    job = self.jobs.get(identifier)
                    if job is None or job["status"] != "queued":
                        continue
                    job["status"] = "running"
                    job["started_at"] = now()
                    atomic_write_job(job)
                await self._run(identifier)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("generation job %s failed unexpectedly", identifier)
                async with self.lock:
                    job = self.jobs.get(identifier)
                    if job is not None and job["status"] not in TERMINAL:
                        job["status"] = "failed"
                        job["finished_at"] = now()
                        job["error"] = "internal generation worker error"
                        atomic_write_job(job)
            finally:
                self.queue.task_done()

    async def _run(self, identifier: str) -> None:
        job = self.jobs[identifier]
        output = WORKSPACE / "jobs" / identifier / "output.wav"
        command = self._command(job, output)
        environment = {
            "HOME": "/tmp/audio-home",
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "HF_HOME": "/tmp/huggingface",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(WORKSPACE if TEST_MODE else UPSTREAM),
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        output_task = asyncio.create_task(self._drain_output(process.stdout))
        async with self.lock:
            self.current_job = identifier
            self.current_process = process
            terminate_after_launch = self.stopping or self.jobs[identifier]["status"] != "running"
        rss_task = asyncio.create_task(self._monitor_rss(identifier, process))
        # Cancellation may race between queued->running and process assignment.
        # The child is now addressable, so honor the persisted terminal state.
        if terminate_after_launch:
            await self._terminate(process)
        timed_out = False
        output_tail = b""
        try:
            try:
                returncode = await asyncio.wait_for(process.wait(), GENERATION_TIMEOUT_SECONDS)
            except TimeoutError:
                timed_out = True
                await self._terminate(process)
                returncode = process.returncode
        finally:
            await rss_task
            output_tail = await output_task
            await asyncio.to_thread(write_process_log, identifier, output_tail)
            async with self.lock:
                if self.current_job == identifier:
                    self.current_job = None
                    self.current_process = None
        async with self.lock:
            job = self.jobs[identifier]
            if job["status"] in {"cancelled", "interrupted"}:
                atomic_write_job(job)
                return
        error = None
        audio = None
        if timed_out:
            error = f"generation exceeded the {GENERATION_TIMEOUT_SECONDS}-second limit"
        elif returncode != 0:
            error = f"generation process exited with code {returncode}"
            detail = diagnostic_text(output_tail)
            if detail:
                error += f": {detail}"
        else:
            try:
                relative = f"jobs/{identifier}/output.wav"
                stream, _ = open_regular(WORKSPACE, relative, {".wav"})
                stream.close()
                audio = await asyncio.to_thread(
                    verify_wave, output, relative, float(job["seconds"])
                )
            except (OSError, ValueError, KeyError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
                error = f"generated WAV failed validation: {exc}"
        async with self.lock:
            job = self.jobs[identifier]
            if job["status"] not in {"cancelled", "interrupted"}:
                job["status"] = "completed" if error is None else "failed"
                job["finished_at"] = now()
                job["artifact"] = f"jobs/{identifier}/output.wav" if error is None else None
                job["audio"] = audio
                job["error"] = error
                atomic_write_job(job)

    @staticmethod
    async def _drain_output(stream: asyncio.StreamReader | None) -> bytes:
        if stream is None:
            return b""
        tail = bytearray()
        while block := await stream.read(8192):
            tail.extend(block)
            if len(tail) > 16 * 1024:
                del tail[:-16 * 1024]
        return bytes(tail)

    async def _monitor_rss(self, identifier: str, process: asyncio.subprocess.Process) -> None:
        last_persisted = 0
        while process.returncode is None:
            peak = read_peak_rss(process.pid)
            async with self.lock:
                job = self.jobs[identifier]
                if peak > int(job.get("peak_subprocess_rss_bytes", 0)):
                    job["peak_subprocess_rss_bytes"] = peak
                    if peak - last_persisted >= 1024 * 1024:
                        atomic_write_job(job)
                        last_persisted = peak
            await asyncio.sleep(0.25)
        peak = read_peak_rss(process.pid)
        async with self.lock:
            job = self.jobs[identifier]
            job["peak_subprocess_rss_bytes"] = max(
                int(job.get("peak_subprocess_rss_bytes", 0)), peak
            )
            atomic_write_job(job)

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

    async def stop(self) -> None:
        self.stopping = True
        process = None
        async with self.lock:
            for job in self.jobs.values():
                if job["status"] in {"queued", "running"}:
                    job["status"] = "interrupted"
                    job["finished_at"] = now()
                    job["error"] = "service stopped before generation completed"
                    atomic_write_job(job)
            process = self.current_process
        if process is not None:
            await self._terminate(process)
        if self.worker is not None:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        if self.model_watcher is not None:
            self.model_watcher.cancel()
            await asyncio.gather(self.model_watcher, return_exceptions=True)


runtime = Runtime()
mcp = FastMCP(
    "Haynes Quest Audio",
    host="0.0.0.0",
    port=8000,
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "127.0.0.1:*", "localhost:*", "audio-authoring:*", "audio-authoring.dev:*",
            "audio-authoring.dev.svc:*", "audio-authoring.dev.svc.cluster.local:*",
        ],
        allowed_origins=[],
    ),
    instructions=(
        "Submit bounded Stable Audio 3 Small SFX jobs and poll them asynchronously. "
        "One generation runs at a time and up to four wait. Download completed WAV files "
        "from the returned internal artifact URL."
    ),
)


@mcp.tool()
async def generate_sound(prompt: str, seconds: float = 5, steps: int = 8,
                         seed: int | None = None) -> str:
    """Queue a 5-30 second sound effect and return its job ID immediately."""
    return json.dumps(await runtime.submit(prompt, seconds, steps, seed), indent=2)


@mcp.tool()
async def get_generation(job_id: str) -> str:
    """Get persisted status, elapsed time, peak subprocess RSS and artifact details."""
    return json.dumps(await runtime.get(job_id), indent=2)


@mcp.tool()
async def cancel_generation(job_id: str) -> str:
    """Cancel a waiting or active generation job."""
    return json.dumps(await runtime.cancel(job_id), indent=2)


@mcp.tool()
async def list_generations(limit: int = 20) -> str:
    """List the newest persisted generation jobs."""
    return json.dumps(await runtime.list(limit), indent=2)


@mcp.custom_route("/healthz", methods=["GET"])
async def health(_request):
    return JSONResponse({"healthy": True})


@mcp.custom_route("/readyz", methods=["GET"])
async def ready(_request):
    status_code = 200 if runtime.ready() else 503
    return JSONResponse({
        "ready": runtime.ready(),
        "service_version": SERVICE_VERSION,
        "model_ready": runtime.model_manifest is not None,
        "model_error": runtime.model_error,
        "busy": runtime.current_process is not None,
        "queued": runtime.queue.qsize(),
        "queue_capacity": QUEUE_CAPACITY,
        "model": MODEL,
        "test_backend": TEST_MODE,
    }, status_code=status_code)


@mcp.custom_route("/artifacts/{job_id}/output.wav", methods=["GET"])
async def artifact(request):
    identifier = request.path_params["job_id"]
    if not JOB_ID.fullmatch(identifier):
        return JSONResponse({"error": "Artifact not found"}, status_code=404)
    async with runtime.lock:
        job = runtime.jobs.get(identifier)
        completed = job is not None and job.get("status") == "completed"
    if not completed:
        return JSONResponse({"error": "Artifact not found"}, status_code=404)
    try:
        stream, info = open_regular(WORKSPACE, f"jobs/{identifier}/output.wav", {".wav"})
    except (OSError, ValueError):
        return JSONResponse({"error": "Artifact not found"}, status_code=404)

    async def chunks():
        try:
            while block := await asyncio.to_thread(stream.read, 1024 * 1024):
                yield block
        finally:
            stream.close()

    return StreamingResponse(
        chunks(), media_type="audio/wav",
        headers={
            "Content-Length": str(info.st_size),
            "Content-Disposition": f'attachment; filename="sound-{identifier}.wav"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


app = mcp.streamable_http_app()
http_lifespan = app.router.lifespan_context


@asynccontextmanager
async def lifespan(application):
    await runtime.start()
    try:
        async with http_lifespan(application):
            yield
    finally:
        await runtime.stop()


app.router.lifespan_context = lifespan


def main() -> None:
    server = uvicorn.Server(uvicorn.Config(
        app, host="0.0.0.0", port=8000, timeout_graceful_shutdown=15,
        access_log=False,
    ))
    server.run()
    raise SystemExit(0 if server.started else 1)


if __name__ == "__main__":
    main()
