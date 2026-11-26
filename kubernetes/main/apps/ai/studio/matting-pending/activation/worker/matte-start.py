"""Prove pod isolation, wait for registration, then supervise the matte worker."""

import errno
import json
import os
import secrets
import signal
import socket
import struct
import subprocess
import sys
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from time import monotonic, sleep

LISTENER = ("studio-api.ai.svc.cluster.local.", 8002)
COREDNS = ("10.43.0.10", 53)
POD_NETWORK = IPv4Network("10.42.0.0/16")
METRICS_PORT = 9153
BACKEND_ATTEMPTS = 12
TCP_TABLE = Path("/proc/self/net/tcp")
INSTANCE_FD_NAME = "studio-matte-launcher"
CONNECT_SECONDS = 2
RECHECK_SECONDS = 60
TOKEN = Path("/run/secrets/studio-worker/token")
DNS_HEADER = struct.Struct("!6H")
DNS_RECORD = struct.Struct("!HHIH")
DNS_QUESTION = b"\x08kube-dns\x0bkube-system\x03svc\x07cluster\x05local\0" + struct.pack("!HH", 1, 1)


def listener_address():
    # The worker listener is a single-stack Service with one ClusterIP.
    addresses = {(family, address) for family, _, _, _, address in
                 socket.getaddrinfo(*LISTENER, type=socket.SOCK_STREAM)}
    if len(addresses) != 1:
        raise ValueError(f"expected one DNS address for {LISTENER[0]}")
    return addresses.pop()


def dns_backend(dns):
    # Cilium reverse-translates getpeername(). The TCP table reads the socket's
    # real inet_daddr. Match by inode while we still own the open socket.
    inode = str(os.fstat(dns.fileno()).st_ino)
    with TCP_TABLE.open() as table:
        next(table, None)
        rows = [fields for line in table if len(fields := line.split()) > 9 and fields[9] == inode]
    if len(rows) != 1 or rows[0][3] != "01":
        raise ValueError("expected a unique established IPv4 DNS socket in the TCP table")
    address_hex, port_hex = rows[0][2].split(":")
    # procfs formats the network-order address as a native-endian integer.
    raw_address = bytes.fromhex(address_hex)
    address = IPv4Address(raw_address[::-1] if sys.byteorder == "little" else raw_address)
    if address not in POD_NETWORK or str(address) == COREDNS[0] or int(port_hex, 16) != COREDNS[1]:
        raise ValueError("TCP table peer is not a DNS backend pod address on port 53")
    return str(address)


def query_dns(dns, reader):
    # The stable Service A record is only a liveness witness. Its value and
    # TTL never supply a destination for the forbidden connection.
    query_id = secrets.randbits(16)
    request = DNS_HEADER.pack(query_id, 0x0100, 1, 0, 0, 0) + DNS_QUESTION
    dns.sendall(struct.pack("!H", len(request)) + request)
    read_dns_reply(reader, query_id)


def read_dns_reply(reader, query_id):
    prefix = reader.read(2)
    size = int.from_bytes(prefix, "big")
    if len(prefix) != 2 or not DNS_HEADER.size <= size <= 512:
        raise ValueError("invalid DNS response size")
    response = reader.read(size)
    if len(response) != size:
        raise ValueError("incomplete DNS response")
    received_id, flags, questions, answers, authority, extra = DNS_HEADER.unpack_from(response)
    # Require a successful query response and exactly one A record owned
    # by this question. The owner may be compressed to offset 12.
    if (received_id != query_id or flags & 0xFA0F != 0x8000
            or (questions, answers, authority, extra) != (1, 1, 0, 0)
            or not response[DNS_HEADER.size:].startswith(DNS_QUESTION)):
        raise ValueError("invalid DNS response header or question")
    answer = response[DNS_HEADER.size + len(DNS_QUESTION):]
    for owner in (b"\xc0\x0c", DNS_QUESTION[:-4]):
        if answer.startswith(owner) and len(answer) == len(owner) + DNS_RECORD.size + 4:
            break
    else:
        raise ValueError("unexpected DNS answer owner or size")
    kind, dns_class, _, length = DNS_RECORD.unpack_from(answer, len(owner))
    if (kind, dns_class, length) != (1, 1, 4) or IPv4Address(answer[-4:]) != IPv4Address(COREDNS[0]):
        raise ValueError("expected the DNS Service A record")


def connect(family, address):
    with socket.socket(family, socket.SOCK_STREAM) as client:
        client.settimeout(CONNECT_SECONDS)
        try:
            client.connect(address)
        except OSError as error:
            # Only a connect timeout witnesses a silent drop. In particular,
            # Cilium can reject a Service with no backend before egress policy.
            if error.errno == errno.ETIMEDOUT or (isinstance(error, TimeoutError) and error.errno is None):
                return False
            raise
    return True


def sample_backends():
    backends = set()
    for _ in range(BACKEND_ATTEMPTS):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as dns:
            dns.settimeout(CONNECT_SECONDS)
            dns.connect(COREDNS)
            backend = dns_backend(dns)
            if backend in backends:
                continue
            with dns.makefile("rb") as reader:
                query_dns(dns, reader)
                if connect(socket.AF_INET, (backend, METRICS_PORT)):
                    raise ValueError(f"forbidden TCP connection to {backend}:{METRICS_PORT} succeeded")
                query_dns(dns, reader)
            backends.add(backend)
            if len(backends) == 2:
                return sorted(backends)
    raise ValueError("could not witness two distinct DNS backends")


def prove_isolation(phase, instance):
    reason = ""
    backends = []
    try:
        if not connect(*listener_address()):
            raise ValueError("listener did not accept TCP 8002")
        backends = sample_backends()
    except (OSError, ValueError) as error:
        reason = f"{type(error).__name__}: {error}"
    print(json.dumps({"event": "matte_isolation", "phase": phase, "instance": instance,
                      "result": "fail" if reason else "pass", "reason": reason, "backends": backends}), flush=True)
    return not reason


def launcher_instance(proc_fds=Path("/proc/1/fd")):
    # Read the anonymous file held open by this container's PID 1. The
    # progress volume alone could still hold a previous container's ID.
    matches = []
    for fd in proc_fds.iterdir():
        try:
            target = os.readlink(fd)
        except FileNotFoundError:  # An unrelated fd closed during enumeration.
            continue
        if target == f"/memfd:{INSTANCE_FD_NAME} (deleted)":
            matches.append(fd)
    if len(matches) != 1:
        raise ValueError("expected one live matte launcher instance")
    return matches[0].read_text()


def wait_for_grant(secret, stopping, recheck_at, instance):
    import httpx

    from haynes_studio.queue.progress import ProgressFile
    from haynes_studio.queue.worker_tokens import worker_bearer

    progress = ProgressFile(os.environ["STUDIO_WORKER_PROGRESS_FILE"])
    headers = {"Authorization": f"Bearer {worker_bearer(secret)}"}
    with httpx.Client(base_url=os.environ["STUDIO_API_URL"], headers=headers,
                      timeout=5, trust_env=False) as client:
        while not stopping():
            if monotonic() >= recheck_at:
                if not prove_isolation("recheck", instance):
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


def supervise(worker, stopping, recheck_at, instance):
    while True:
        if stopping():
            signal_group(worker, signal.SIGTERM)
            return worker.wait()
        remaining = max(0, recheck_at - monotonic())
        try:
            return worker.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            if not prove_isolation("recheck", instance):
                signal_group(worker, signal.SIGKILL)
                worker.wait()
                return 1
            recheck_at = monotonic() + RECHECK_SECONDS


def main():
    instance = secrets.token_hex(16)
    with os.fdopen(os.memfd_create(INSTANCE_FD_NAME, os.MFD_CLOEXEC), "w") as identity:
        identity.write(instance)
        identity.flush()
        return run_worker(instance)


def run_worker(instance):
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
    if not prove_isolation("startup", instance):
        return 1
    recheck_at = monotonic() + RECHECK_SECONDS
    if stopping():
        return 0
    secret = TOKEN.read_text().strip()
    recheck_at = wait_for_grant(secret, stopping, recheck_at, instance)
    if stopping():
        return 0
    if recheck_at is None:
        return 1
    # Recheck on schedule even if registration took more than one interval.
    worker = subprocess.Popen(sys.argv[1:], start_new_session=True,
                              env={**os.environ, "STUDIO_WORKER_TOKEN": secret})
    result = supervise(worker, stopping, recheck_at, instance)
    return 128 - result if result < 0 else result


if __name__ == "__main__":
    raise SystemExit(main())
