"""Prove pod isolation, wait for registration, then supervise the matte worker."""

import errno
import json
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep

LISTENER = ("studio-api.ai.svc.cluster.local.", 8002)
FORBIDDEN = (
    ("kubernetes.default.svc.cluster.local.", 443),
    ("comfyui.ai.svc.cluster.local.", 8188),
)
BLOCKED = {errno.EACCES, errno.EPERM, errno.ETIMEDOUT, errno.ECONNREFUSED,
           errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ECONNRESET}
RECHECK_SECONDS = 60
TOKEN = Path("/run/secrets/studio-worker/token")


def connect(target):
    # Installed services are single-stack with one ClusterIP. Refuse an
    # ambiguous answer rather than silently testing only one of several IPs.
    addresses = {(family, address) for family, _, _, _, address in
                 socket.getaddrinfo(*target, type=socket.SOCK_STREAM)}
    if len(addresses) != 1:
        raise ValueError(f"expected one DNS address for {target[0]}")
    family, address = addresses.pop()
    with socket.socket(family, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        try:
            client.connect(address)
        except TimeoutError:
            return False
        except OSError as error:
            if error.errno in BLOCKED:
                return False
            raise
    return True


def prove_isolation(phase, *, connect_to=connect):
    reason = ""
    try:
        if not connect_to(LISTENER):
            reason = "DNS resolved but the Studio listener did not accept TCP 8002"
        else:
            for target in FORBIDDEN:
                if connect_to(target):
                    reason = f"forbidden TCP connection succeeded: {target[0]}:{target[1]}"
                    break
    except (OSError, ValueError) as error:
        reason = f"could not check isolation: {type(error).__name__}"
    print(json.dumps({"event": "matte_isolation", "phase": phase,
                      "result": "fail" if reason else "pass", "reason": reason}), flush=True)
    return not reason


def wait_for_grant(secret, stopping, recheck_at):
    import httpx

    from haynes_studio.queue.progress import ProgressFile
    from haynes_studio.queue.worker_tokens import worker_bearer

    progress = ProgressFile(os.environ["STUDIO_WORKER_PROGRESS_FILE"])
    headers = {"Authorization": f"Bearer {worker_bearer(secret)}"}
    with httpx.Client(base_url=os.environ["STUDIO_API_URL"], headers=headers,
                      timeout=5, trust_env=False) as client:
        while not stopping():
            if monotonic() >= recheck_at:
                if not prove_isolation("recheck"):
                    return None
                recheck_at = monotonic() + RECHECK_SECONDS
            progress.write_status("Waiting for the matte worker lane grant")
            try:
                # An extra field is rejected before claim SQL, even with a grant.
                response = client.post("/internal/v1/work/claim",
                                       json={"lane": "matte", "registration_probe": True})
                if response.status_code == 422:
                    payload = response.json()
                    error = payload.get("error") if isinstance(payload, dict) else None
                    if (isinstance(error, dict) and error.get("code") == "schema.unknown_field"
                            and error.get("path") == "/registration_probe"):
                        return recheck_at
            except (httpx.RequestError, ValueError):
                pass
            sleep(5)
    return None


def signal_group(worker, signum):
    try:
        os.killpg(worker.pid, signum)
    except ProcessLookupError:
        pass


def supervise(worker, stopping, recheck_at):
    while True:
        if stopping():
            signal_group(worker, signal.SIGTERM)
            return worker.wait()
        remaining = max(0, recheck_at - monotonic())
        try:
            return worker.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            if not prove_isolation("recheck"):
                signal_group(worker, signal.SIGKILL)
                worker.wait()
                return 1
            recheck_at = monotonic() + RECHECK_SECONDS


def main():
    worker = None
    stop_requested = False

    def stopping():
        return stop_requested

    def stop(signum, _frame):
        nonlocal stop_requested
        stop_requested = True
        if worker is not None:
            signal_group(worker, signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # The progress volume outlives a container restart. A fresh marker must
    # come from this process after its isolation proof.
    Path(os.environ["STUDIO_WORKER_PROGRESS_FILE"]).unlink(missing_ok=True)
    if not prove_isolation("startup"):
        return 1
    recheck_at = monotonic() + RECHECK_SECONDS
    if stopping():
        return 0
    secret = TOKEN.read_text().strip()
    recheck_at = wait_for_grant(secret, stopping, recheck_at)
    if stopping():
        return 0
    if recheck_at is None:
        return 1
    # Recheck on schedule even if registration took more than one interval.
    worker = subprocess.Popen(sys.argv[1:], start_new_session=True,
                              env={**os.environ, "STUDIO_WORKER_TOKEN": secret})
    result = supervise(worker, stopping, recheck_at)
    return 128 - result if result < 0 else result


if __name__ == "__main__":
    raise SystemExit(main())
