"""Inspect a private serve process without calling CUDA or loading a model."""
import argparse
import ctypes
import fcntl
import json
from pathlib import Path
import selectors
import subprocess
import sys
import time


def wait_for_listener(output, event, timeout=20):
    """Wait for the serving process to report that listen() succeeded."""
    deadline = time.monotonic() + timeout
    pending = b""
    with selectors.DefaultSelector() as selector:
        selector.register(output, selectors.EVENT_READ)
        while (remaining := deadline - time.monotonic()) > 0 and selector.select(remaining):
            chunk = output.read(4096)
            if not chunk:
                break
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                try:
                    record = json.loads(line)
                except ValueError:
                    print(line.decode(errors="replace"), file=sys.stderr)
                    continue
                if isinstance(record, dict) and record.get("event") == event:
                    return record
            if len(pending) > 16 * 1024:
                break
    raise RuntimeError("Local-model listener startup record was not received")


def cuda_library_loads(lock_path):
    # Map the library while holding the original lease. Never call cuInit().
    try:
        with lock_path.open("rb") as lease:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            ctypes.CDLL("libcuda.so.1")
    except OSError:
        return False
    return True


def diagnose(workspace, driver_lock, profile_json):
    import local_model_sidecar as sidecar
    import product_local as local

    profile_dir = workspace / "diagnostic"
    profile_dir.mkdir(mode=0o700)
    profile_path = profile_dir / "local-model.json"
    profile_path.write_text(profile_json)
    # The serving function uses the original lock for the startup read only.
    # Its private workspace has no compute.lock to authorize a dispatch.
    launch = (
        "import sys; from pathlib import Path; from studio_local_backend import serve; "
        "workspace, profile, lock = map(Path, sys.argv[1:]); "
        "serve(workspace, profile, workspace / 'local-model-weights', pin_lock=lock)"
    )
    command = [sys.executable, "-m", "studio_local_backend"]
    with subprocess.Popen([sys.executable, "-c", launch, str(workspace), str(profile_path), str(driver_lock)],
                          stdout=subprocess.PIPE, bufsize=0) as server:
        try:
            startup = wait_for_listener(server.stdout, sidecar.STARTUP_LOG)
            passed = startup["reason_code"] is None
            print(json.dumps({**startup, "verdict": "ready" if passed else "refused"}), flush=True)
            probe = subprocess.run([*command, "ready", "--workspace", str(workspace), "--pid", str(server.pid)],
                                   check=False, timeout=10)
            ready = probe.returncode == 0
            # A terminal refusal is available on either action. Use status in
            # the pin-pending case to observe it without another proof attempt.
            action = "residency" if passed else "status"
            residency = local.exchange(workspace, {"action": action})
            print(json.dumps({"ready": ready, "residency": residency}), flush=True)
            library_loaded = passed and cuda_library_loads(driver_lock)
            print(json.dumps({"cuda_library_loaded": library_loaded}), flush=True)
            return 0 if (passed and ready and residency.get("resident") is False
                         and residency.get("receipt") is None and "startup_error" not in residency
                         and library_loaded) else 1
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    parser.add_argument("--driver-lock", type=Path, required=True)
    parser.add_argument("--profile-json", required=True)
    args = parser.parse_args()
    raise SystemExit(diagnose(args.workspace, args.driver_lock, args.profile_json))
