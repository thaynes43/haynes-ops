#!/usr/bin/env python3
"""Offline control-plane smoke using only the deterministic fixture subprocess."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import AsyncMock, patch
import wave

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


INSTALL = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8000"
MODEL_FILES = (
    "tflite/t5gemma/encoder_fp16.tflite",
    "tflite/sa3-sm-sfx/dit_fp32.tflite",
    "tflite/same-s/dec_w8a8.tflite",
)
WEIGHTS_REVISION = "da6edc54ddba10bfd79a077102ded687f80e882b"


def provision_fixture_models(root: Path) -> None:
    records = []
    for index, relative in enumerate(MODEL_FILES):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (f"TEST MODEL FIXTURE {index} — NOT A REAL MODEL\n".encode() * 8)
        path.write_bytes(content)
        records.append({
            "path": relative,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        })
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "repository": "stabilityai/stable-audio-3-optimized",
        "revision": WEIGHTS_REVISION,
        "files": records,
    }, indent=2) + "\n")


def ensure_test_ffprobe(root: Path) -> None:
    """Supply a fixture-only probe when tests run outside the built image."""
    from shutil import which
    if which("ffprobe") is not None:
        return
    directory = root / "test-bin"
    directory.mkdir()
    script = directory / "ffprobe"
    script.write_text("""#!/usr/bin/env python3
import json, sys, wave
with wave.open(sys.argv[-1], 'rb') as stream:
    duration = stream.getnframes() / stream.getframerate()
    print(json.dumps({'streams': [{'codec_type': 'audio', 'codec_name': 'pcm_s16le',
        'sample_rate': str(stream.getframerate()), 'channels': stream.getnchannels()}],
        'format': {'duration': str(duration)}}))
""")
    script.chmod(0o700)
    os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")


def service_environment(models: Path, workspace: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({
        "AUDIO_AUTHORING_TEST_MODE": "1",
        "AUDIO_AUTHORING_TEST_MODELS": str(models),
        "AUDIO_AUTHORING_TEST_WORKSPACE": str(workspace),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    })
    return environment


def test_xnnpack_cache_paths(root: Path) -> None:
    scripts = INSTALL / "stable-audio-3" / "optimized" / "tflite" / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        from xnnpack_cache import default_weight_cache_path
    finally:
        sys.path.remove(str(scripts))

    first_model = root / "first" / "codec.tflite"
    second_model = root / "second" / "codec.tflite"
    first_model.parent.mkdir()
    second_model.parent.mkdir()
    first_model.touch()
    second_model.touch()

    first = default_weight_cache_path(first_model)
    second = default_weight_cache_path(second_model)
    assert first.parent == Path("/tmp/audio-xnnpack")
    assert first == default_weight_cache_path(first_model)
    assert first != second


def start_service(models: Path, workspace: Path, log_path: Path, ready: bool = True):
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, str(INSTALL / "service.py")],
            env=service_environment(models, workspace),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"service exited during startup ({process.returncode})")
        try:
            health = httpx.get(BASE + "/healthz", timeout=1)
            readiness = httpx.get(BASE + "/readyz", timeout=1)
            if health.status_code == 200 and readiness.status_code == (200 if ready else 503):
                return process
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    process.terminate()
    process.wait(timeout=10)
    raise RuntimeError("service startup timed out")


def stop_service(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=15)


@asynccontextmanager
async def connected():
    async with streamablehttp_client(BASE + "/mcp") as (read, write, _):
        async with ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=15)
        ) as session:
            await session.initialize()
            yield session


async def call(session: ClientSession, name: str, arguments: dict) -> dict:
    result = await session.call_tool(name, arguments)
    text = "\n".join(block.text for block in result.content if block.type == "text")
    assert not result.isError, text
    return json.loads(text)


async def wait_status(session: ClientSession, identifier: str, states: set[str], timeout: float = 15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = await call(session, "get_generation", {"job_id": identifier})
        if job["status"] in states:
            return job
        if job["status"] in {"failed", "cancelled", "interrupted"}:
            raise AssertionError(f"job {identifier} reached unexpected terminal state: {job}")
        await asyncio.sleep(0.1)
    raise AssertionError(f"job {identifier} did not reach {states}")


async def test_launch_cancel_race(workspace: Path) -> None:
    # Import only after the test-only sentinel and path environment are active.
    import service

    runtime = service.Runtime()
    identifier = "a" * 32
    directory = workspace / "jobs" / identifier
    directory.mkdir(parents=True)
    job = {
        "schema_version": 1, "id": identifier, "status": "running",
        "prompt": "launch race fixture", "seconds": 5.0, "steps": 8, "seed": 1,
        "created_at": service.now(), "started_at": service.now(), "finished_at": None,
        "peak_subprocess_rss_bytes": 0, "artifact": None, "audio": None, "error": None,
    }
    runtime.jobs[identifier] = job
    assert "service_version" not in runtime._public(job)
    service.atomic_write_job(job)
    spawning = asyncio.Event()
    release = asyncio.Event()

    class FakeProcess:
        pid = 99999999
        returncode = -15
        stdout = asyncio.StreamReader()

        def __init__(self):
            self.stdout.feed_eof()

        async def wait(self):
            return self.returncode

    async def delayed_spawn(*_args, **_kwargs):
        spawning.set()
        await release.wait()
        return FakeProcess()

    terminator = AsyncMock()
    with patch.object(service.asyncio, "create_subprocess_exec", delayed_spawn), \
            patch.object(runtime, "_terminate", terminator):
        running = asyncio.create_task(runtime._run(identifier))
        await spawning.wait()
        cancelled = await runtime.cancel(identifier)
        assert cancelled["status"] == "cancelled"
        release.set()
        await running
    terminator.assert_awaited_once()
    assert runtime.jobs[identifier]["status"] == "cancelled"


async def exercise(workspace: Path) -> str:
    async with connected() as session, httpx.AsyncClient(base_url=BASE) as client:
        names = {tool.name for tool in (await session.list_tools()).tools}
        assert names == {
            "generate_sound", "get_generation", "cancel_generation", "list_generations"
        }, names

        bad = await session.call_tool("generate_sound", {"prompt": "bad\nprompt"})
        assert bad.isError
        bad = await session.call_tool("generate_sound", {"prompt": "okay", "seconds": 31})
        assert bad.isError
        bad = await session.call_tool("generate_sound", {"prompt": "okay", "steps": 17})
        assert bad.isError

        started = time.monotonic()
        first = await call(session, "generate_sound", {
            "prompt": "deterministic fixture tone", "seconds": 5, "steps": 8, "seed": 7,
        })
        assert time.monotonic() - started < 1.0
        assert first["status"] in {"queued", "running"}
        assert first["service_version"] == "0.1.1"
        assert first["test_backend"].endswith("NOT real generation proof")
        completed = await wait_status(session, first["id"], {"completed"})
        assert completed["audio"]["duration_seconds"] == 5.0
        assert completed["audio"]["size_bytes"] > 1000
        assert len(completed["audio"]["sha256"]) == 64
        assert completed["peak_subprocess_rss_bytes"] > 0
        response = await client.get(completed["download_url"])
        assert response.status_code == 200 and response.content[:4] == b"RIFF"
        assert response.headers["content-disposition"].startswith("attachment;")
        with wave.open(io.BytesIO(response.content), "rb") as probe:
            assert probe.getnchannels() == 2 and probe.getframerate() == 44100
            assert abs(probe.getnframes() / probe.getframerate() - 5) < 0.05

        output = workspace / completed["artifact"]
        outside = workspace.parent / "outside.wav"
        outside.write_bytes(response.content)
        output.unlink()
        os.link(outside, output)
        assert (await client.get(completed["download_url"])).status_code == 404
        output.unlink()
        output.symlink_to(outside)
        assert (await client.get(completed["download_url"])).status_code == 404
        for path in ("/artifacts/../outside.wav", "/artifacts/%2fetc/passwd/output.wav",
                     "/artifacts/not-a-job/output.wav"):
            assert (await client.get(path)).status_code == 404

        slow = await call(session, "generate_sound", {
            "prompt": "fixture slow generation", "seconds": 5, "seed": 8,
        })
        live = await wait_status(session, slow["id"], {"running"})
        await asyncio.sleep(0.4)
        live = await call(session, "get_generation", {"job_id": slow["id"]})
        assert live["elapsed_seconds"] > 0 and live["peak_subprocess_rss_bytes"] > 0
        waiting = []
        for seed in range(20, 24):
            waiting.append(await call(session, "generate_sound", {
                "prompt": f"queued fixture {seed}", "seconds": 5, "seed": seed,
            }))
        overflow = await session.call_tool("generate_sound", {
            "prompt": "queue overflow fixture", "seconds": 5, "seed": 30,
        })
        assert overflow.isError
        queued_cancel = await call(session, "cancel_generation", {"job_id": waiting[0]["id"]})
        assert queued_cancel["status"] == "cancelled"
        active_cancel = await call(session, "cancel_generation", {"job_id": slow["id"]})
        assert active_cancel["status"] == "cancelled"
        for item in waiting[1:]:
            assert (await wait_status(session, item["id"], {"completed"}))["status"] == "completed"
        listed = await call(session, "list_generations", {"limit": 100})
        assert any(item["id"] == first["id"] for item in listed)

        restart = await call(session, "generate_sound", {
            "prompt": "fixture slow generation", "seconds": 5, "seed": 40,
        })
        await wait_status(session, restart["id"], {"running"})
        return restart["id"]


async def verify_restart(identifier: str) -> None:
    async with connected() as session:
        job = await call(session, "get_generation", {"job_id": identifier})
        assert job["status"] == "interrupted", job
        assert job["model"]["source_revision"] == "prior-fixture-runtime", job


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sentinel = INSTALL / "TEST_IMAGE"
    made_sentinel = not sentinel.exists()
    if made_sentinel:
        sentinel.touch()
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="audio-authoring-smoke-") as directory:
            root = Path(directory)
            models = root / "models"
            workspace = root / "workspace"
            models.mkdir()
            workspace.mkdir()
            ensure_test_ffprobe(root)
            provision_fixture_models(models)
            os.environ.update(service_environment(models, workspace))
            test_xnnpack_cache_paths(root)
            asyncio.run(test_launch_cancel_race(workspace))

            log_path = root / "service.log"
            process = start_service(models, workspace, log_path)
            with httpx.Client(base_url=BASE) as client:
                body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-03-26", "capabilities": {},
                    "clientInfo": {"name": "smoke", "version": "1"},
                }}
                headers = {"accept": "application/json, text/event-stream"}
                response = client.post("/mcp", json=body, headers={**headers, "host": "evil.invalid:8000"})
                assert response.status_code == 421
                response = client.post("/mcp", json=body, headers={**headers,
                    "host": "audio-authoring.dev.svc.cluster.local:8000"})
                assert response.status_code == 200
            restart_id = asyncio.run(exercise(workspace))
            stop_service(process)
            record_path = workspace / "jobs" / restart_id / "job.json"
            record = json.loads(record_path.read_text())
            assert record["service_version"] == "0.1.1"
            assert record["model"]["weights_revision"] == WEIGHTS_REVISION
            # Older jobs must retain their originating model identity after upgrades.
            record["model"]["source_revision"] = "prior-fixture-runtime"
            record_path.write_text(json.dumps(record))
            process = start_service(models, workspace, log_path)
            asyncio.run(verify_restart(restart_id))
            stop_service(process)

            saved_manifest = root / "manifest.saved"
            (models / "manifest.json").replace(saved_manifest)
            process = start_service(models, workspace, log_path, ready=False)
            assert httpx.get(BASE + "/healthz").status_code == 200
            unavailable = httpx.get(BASE + "/readyz")
            assert unavailable.status_code == 503 and not unavailable.json()["model_ready"]
            saved_manifest.replace(models / "manifest.json")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if httpx.get(BASE + "/readyz").status_code == 200:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("published model manifest did not make the service ready")
            stop_service(process)
            process = None
            print("PASS: native MCP transport, async queue/cancel/launch race, persisted restart state, "
                  "secure WAV download, live elapsed/RSS, offline model readiness transition")
            print("TEST BACKEND: deterministic subprocess fixture only — NOT real generation proof")
    except BaseException:
        if "log_path" in locals() and log_path.exists():
            print(log_path.read_text(errors="replace")[-20000:], file=sys.stderr)
        raise
    finally:
        if process is not None:
            stop_service(process)
        if made_sentinel:
            sentinel.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
