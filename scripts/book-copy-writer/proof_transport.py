"""Length/SHA-bound stdin files for one exact phase/Job; no Kubernetes calls."""
import hashlib
import argparse
import contextlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
import uuid

FILES = {"snapshot.json": 16 * 1024 * 1024, "selection.json": 1024 * 1024,
         "app-capture.json": 32 * 1024 * 1024}
HASH = re.compile(r"[0-9a-f]{64}\Z")
PHASE = re.compile(r"[0-9a-f]{32}\Z")
UID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


class Refused(ValueError):
    pass


def unique(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise Refused("proof header repeats a key")
        result[name] = value
    return result


def gate(environ, deadline=None):
    phase, uid, pod_uid = (environ.get(key, "") for key in ("COPY_PHASE_TOKEN", "COPY_JOB_UID", "COPY_POD_UID"))
    raw = environ.get("COPY_PROOF_HASHES_JSON", "{}")
    if len(raw) > 1024:
        raise Refused("proof hash environment exceeds its small fixed scope")
    hashes = json.loads(raw, object_pairs_hook=unique)
    if (not PHASE.fullmatch(phase) or not UID.fullmatch(uid) or not UID.fullmatch(pod_uid) or not isinstance(hashes, dict)
            or set(hashes) != set(FILES)
            or any(not isinstance(value, str) or not HASH.fullmatch(value) for value in hashes.values())):
        raise Refused("closed or incomplete phase/Job/proof hash gate")
    try:
        approved_deadline = float(environ.get("COPY_DEADLINE_EPOCH", "nan"))
    except ValueError as error:
        raise Refused("proof delivery needs its exact bounded Job deadline") from error
    if (not math.isfinite(approved_deadline) or not 0 < approved_deadline - time.time() <= 300
            or (deadline is not None and deadline != approved_deadline)):
        raise Refused("proof delivery deadline is missing, expired or differs from the Job")
    return phase, uid, pod_uid, hashes, approved_deadline


def remaining(deadline):
    if time.time() >= deadline:
        raise Refused("private proof delivery deadline expired")


def directory_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)


def checked_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if (stat.S_IMODE(info.st_mode) & 0o777 != 0o700 or info.st_uid != os.geteuid()
            or directory_identity(os.lstat(directory)) != directory_identity(info)):
        os.close(fd)
        raise Refused("proof directory must remain private and owned by this process")
    return fd, directory_identity(info)


def receive(stream, directory, environ):
    phase, uid, pod_uid, hashes, deadline = gate(environ)
    line = stream.readline(4097)
    remaining(deadline)
    if len(line) > 4096 or not line.endswith(b"\n"):
        raise Refused("bounded proof header is missing or too large")
    header = json.loads(line, object_pairs_hook=unique)
    if (not isinstance(header, dict) or set(header) != {"schema", "phase_token", "job_uid", "pod_uid", "files"}
            or type(header["schema"]) is not int or header["schema"] != 1
            or header["phase_token"] != phase or header["job_uid"] != uid or header["pod_uid"] != pod_uid
            or not isinstance(header["files"], list) or len(header["files"]) != len(FILES)):
        raise Refused("proof header does not bind this exact phase and Job")
    seen = set()
    for row in header["files"]:
        if (not isinstance(row, dict) or set(row) != {"name", "bytes", "sha256"}
                or not isinstance(row["name"], str) or row["name"] not in FILES or row["name"] in seen
                or type(row["bytes"]) is not int or not 0 < row["bytes"] <= FILES[row["name"]]
                or row["sha256"] != hashes[row["name"]]):
            raise Refused("proof file scope, length or manifest hash changed")
        seen.add(row["name"])
    # The caller creates a new mode-0700 directory under /tmp. Do not accept a
    # reused tree or arbitrary file paths from the streamed header.
    directory = Path(directory)
    fd, initial = checked_directory(directory)
    try:
        if os.listdir(fd):
            raise Refused("proof directory must be new and empty")
        paths = {}
        for row in header["files"]:
            digest, left = hashlib.sha256(), row["bytes"]
            created = os.open(row["name"], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            with os.fdopen(created, "wb") as output:
                while left:
                    remaining(deadline)
                    chunk = stream.read(min(65536, left))
                    if not chunk:
                        raise Refused("proof file is truncated")
                    output.write(chunk); digest.update(chunk); left -= len(chunk)
                output.flush(); os.fsync(output.fileno())
            if digest.hexdigest() != row["sha256"]:
                raise Refused("proof file bytes differ from the reviewed manifest hash")
            paths[row["name"]] = str(directory / row["name"])
        if stream.read(1):
            raise Refused("proof bundle has unapproved trailing bytes")
        remaining(deadline)
        if directory_identity(os.lstat(directory)) != initial or set(os.listdir(fd)) != set(FILES):
            raise Refused("proof directory identity or exact contents changed")
        os.fsync(fd)
    finally:
        os.close(fd)
    return paths


def send(stream, paths, phase, uid, pod_uid):
    """Host-side bounded encoder; caller verifies exact live Pod ownership first."""
    if set(paths) != set(FILES) or not PHASE.fullmatch(phase) or not UID.fullmatch(uid) or not UID.fullmatch(pod_uid):
        raise Refused("sender needs the exact file scope, phase and Job UID")
    with contextlib.ExitStack() as stack:
        records = []
        for name, limit in FILES.items():
            fd = os.open(paths[name], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            source = stack.enter_context(os.fdopen(fd, "rb"))
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= limit:
                raise Refused("sender proof is not a bounded regular file")
            digest = hashlib.sha256()
            while chunk := source.read(65536):
                digest.update(chunk)
            if identity(os.fstat(source.fileno())) != identity(before):
                raise Refused("sender proof changed during hash")
            source.seek(0)
            records.append((source, before, {"name": name, "bytes": before.st_size, "sha256": digest.hexdigest()}))
        header = {"schema": 1, "phase_token": phase, "job_uid": uid, "pod_uid": pod_uid, "files": [row for _, _, row in records]}
        stream.write(json.dumps(header, separators=(",", ":")).encode() + b"\n")
        for source, before, record in records:
            digest, left = hashlib.sha256(), record["bytes"]
            while left:
                chunk = source.read(min(65536, left))
                if not chunk:
                    raise Refused("sender proof changed or was truncated while streaming")
                stream.write(chunk); digest.update(chunk); left -= len(chunk)
            if digest.hexdigest() != record["sha256"] or identity(os.fstat(source.fileno())) != identity(before) or source.read(1):
                raise Refused("sender proof changed while streaming")
        stream.flush()
        return header


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_mode, info.st_uid, info.st_gid, info.st_nlink)


def receive_to_ready(stream, directory, environ):
    paths = receive(stream, directory, environ)
    hashes = json.loads(environ["COPY_PROOF_HASHES_JSON"], object_pairs_hook=unique)
    marker = {"schema": 1, "phase_token": environ["COPY_PHASE_TOKEN"], "job_uid": environ["COPY_JOB_UID"], "pod_uid": environ["COPY_POD_UID"],
              "files": [{"name": name, "bytes": os.stat(path, follow_symlinks=False).st_size, "sha256": hashes[name]}
                        for name, path in paths.items()]}
    fd, initial = checked_directory(directory)
    partial = ".ready-" + uuid.uuid4().hex
    try:
        for name in paths:
            proof_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                if not stat.S_ISREG(os.fstat(proof_fd).st_mode) or os.fstat(proof_fd).st_nlink != 1:
                    raise Refused("received proof was replaced before readiness")
                os.fchmod(proof_fd, 0o400); os.fsync(proof_fd)
            finally:
                os.close(proof_fd)
        if directory_identity(os.lstat(directory)) != initial:
            raise Refused("proof directory changed before readiness")
        created = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=fd)
        with os.fdopen(created, "wb") as output:
            output.write(json.dumps(marker, separators=(",", ":")).encode()); output.flush(); os.fsync(output.fileno())
        remaining(float(environ["COPY_DEADLINE_EPOCH"]))
        os.link(partial, "ready.json", src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.unlink(partial, dir_fd=fd); os.fsync(fd)
    finally:
        os.close(fd)
    return marker


def wait_ready(directory, environ, deadline):
    phase, uid, pod_uid, hashes, _ = gate(environ, deadline)
    # Only the main writer creates the directory. An interrupted receiver cannot
    # silently reuse a prior tree; the caller must start a fresh owned Job.
    os.mkdir(directory, 0o700)
    directory_fd, initial = checked_directory(directory)
    try:
        while True:
            remaining(deadline)
            try:
                marker_info = os.stat("ready.json", dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                marker_info = None
            # The no-overwrite hard-link publication has a short nlink=2 phase
            # until its private staging name is removed. Never consume it early.
            if marker_info is not None and marker_info.st_nlink != 2:
                break
            time.sleep(min(.2, max(.001, deadline - time.time())))
        if directory_identity(os.lstat(directory)) != initial or set(os.listdir(directory_fd)) != set(FILES) | {"ready.json"}:
            raise Refused("ready directory identity or exact contents changed")
        paths = _read_ready(directory, directory_fd, deadline, phase, uid, pod_uid, hashes)
        if directory_identity(os.lstat(directory)) != initial or set(os.listdir(directory_fd)) != set(FILES) | {"ready.json"}:
            raise Refused("proof directory changed during main verification")
        return paths
    finally:
        os.close(directory_fd)


def _read_ready(directory, directory_fd, deadline, phase, uid, pod_uid, hashes):
    fd = os.open("ready.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o400 or not 0 < info.st_size <= 4096):
            raise Refused("ready marker is not a bounded private regular file")
        marker = json.loads(source.read(4097), object_pairs_hook=unique)
        if identity(os.fstat(source.fileno())) != identity(info):
            raise Refused("ready marker changed during read")
    if (not isinstance(marker, dict) or set(marker) != {"schema", "phase_token", "job_uid", "pod_uid", "files"}
            or type(marker["schema"]) is not int or marker["schema"] != 1
            or marker["phase_token"] != phase or marker["job_uid"] != uid or marker["pod_uid"] != pod_uid
            or not isinstance(marker["files"], list) or len(marker["files"]) != len(FILES)):
        raise Refused("ready marker belongs to another phase or Job")
    paths = {}
    for row in marker["files"]:
        if (not isinstance(row, dict) or set(row) != {"name", "bytes", "sha256"}
                or not isinstance(row["name"], str) or row["name"] not in FILES
                or row["name"] in paths or type(row["bytes"]) is not int or not 0 < row["bytes"] <= FILES[row["name"]]
                or row["sha256"] != hashes.get(row["name"])):
            raise Refused("ready proof scope/length/hash differs from the reviewed manifest")
        path = Path(directory) / row["name"]
        fd = os.open(row["name"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.geteuid()
                    or before.st_size != row["bytes"] or stat.S_IMODE(before.st_mode) != 0o400):
                raise Refused("ready proof file is not exact and immutable")
            digest = hashlib.sha256()
            while chunk := source.read(65536):
                remaining(deadline)
                digest.update(chunk)
            if digest.hexdigest() != row["sha256"] or identity(os.fstat(source.fileno())) != identity(before):
                raise Refused("ready proof bytes/identity changed")
        paths[row["name"]] = str(path)
    remaining(deadline)
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bounded private proofs; receiver performs no book or database actions")
    parser.add_argument("mode", choices=("send", "receive"))
    parser.add_argument("--directory", default="/tmp/copy-proofs")
    parser.add_argument("--phase")
    parser.add_argument("--job-uid")
    parser.add_argument("--pod-uid")
    parser.add_argument("--snapshot")
    parser.add_argument("--selection")
    parser.add_argument("--app-capture")
    args = parser.parse_args()
    if args.mode == "receive":
        if args.directory != "/tmp/copy-proofs":
            raise Refused("exec receiver can only publish the fixed private proof directory")
        *_, deadline = gate(os.environ)
        def expired(_signum, _frame):
            raise Refused("exec proof receiver deadline expired")
        for signum in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, expired)
        signal.setitimer(signal.ITIMER_REAL, deadline - time.time())
        try:
            receive_to_ready(sys.stdin.buffer, args.directory, os.environ)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    else:
        send(sys.stdout.buffer, {"snapshot.json": args.snapshot, "selection.json": args.selection,
                                 "app-capture.json": args.app_capture}, args.phase, args.job_uid, args.pod_uid)
