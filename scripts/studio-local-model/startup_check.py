"""Inspect a private serve process without calling CUDA or loading a model."""
import argparse
import csv
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


def inspect_driver(lock_path, card):
    import product_local as local

    # Take the original flock by inode. This only runs the loader and
    # the same read-only NVML query as status; it never calls cuInit().
    with lock_path.open("rb") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            ctypes.CDLL("libcuda.so.1")
            library_loaded = True
        except OSError:
            library_loaded = False
        try:
            identity = local.sample_card(card)["gpu_uuid"]
        except (OSError, ValueError, csv.Error, subprocess.SubprocessError):
            identity = None
    return {"cuda_library_loaded": library_loaded, "nvml_gpu_uuid": identity}


def diagnose(workspace, driver_lock):
    import local_model_sidecar as sidecar
    import product_local as local

    command = [sys.executable, "-m", "studio_local_backend"]
    with subprocess.Popen([*command, "serve", "--workspace", str(workspace)], stdout=subprocess.PIPE, bufsize=0) as server:
        try:
            startup = wait_for_listener(server.stdout, sidecar.STARTUP_LOG)
            print(json.dumps(startup), flush=True)
            probe = subprocess.run([*command, "ready", "--workspace", str(workspace), "--pid", str(server.pid)],
                                   check=False, timeout=10)
            ready = probe.returncode == 0
            residency = local.exchange(workspace, {"action": "residency"})
            print(json.dumps({"ready": ready, "residency": residency}), flush=True)
            card = startup.get("gpu_uuid")
            proof = inspect_driver(driver_lock, card) if card in local.APPROVED_CARDS else {}
            print(json.dumps(proof), flush=True)
            return 0 if (ready and residency.get("resident") is False and residency.get("receipt") is None
                         and "startup_error" not in residency and not startup["startup_error"]
                         and proof == {"cuda_library_loaded": True, "nvml_gpu_uuid": card}) else 1
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
    args = parser.parse_args()
    raise SystemExit(diagnose(args.workspace, args.driver_lock))
