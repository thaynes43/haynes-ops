"""Append keeper trust as existing dev-env; stdout contains metadata only."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import signal
import stat
import sys
import tempfile

from public_key import LIMIT, MARKER, authorized_line, normalize_public

RECEIPT = ".keeper-node-trust.receipt.json"
BACKUP_PREFIX = ".authorized_keys.before-keeper-ca."
MAX_AUTHORIZED = 65536
REFUSAL_REASONS = frozenset({
    "DirectoryMetadata", "FileMetadata", "FileSize", "ConcurrentEdit",
    "MissingExpectedFile", "StageOwner", "ReceiptFormat", "ReceiptIdentity",
    "ReceiptDigest", "LockMetadata", "ConcurrentInstaller", "ReceiptConflict",
    "BackupIdentity", "LaterEdit", "ExistingKeyConflict", "PreservationCheck",
    "MissingReceipt", "RollbackCheck", "Operation", "AccountIdentity",
    "PublicKeySize", "PublicKeyLines", "PublicKeyType", "PublicKeyWireType",
    "PublicKeyWireSize", "PublicKeyEncoding", "PublicKeyFormat", "UnexpectedFailure",
})


def refuse(reason):
    raise ValueError(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def guard_directory(path, uid, gid, mode):
    value = path.lstat()
    if (not stat.S_ISDIR(value.st_mode) or value.st_uid != uid
            or value.st_gid != gid or stat.S_IMODE(value.st_mode) != mode):
        refuse("DirectoryMetadata")


def read_guarded(path, uid, gid, limit=MAX_AUTHORIZED):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        value = os.fstat(fd)
        if (not stat.S_ISREG(value.st_mode) or value.st_uid != uid
                or value.st_gid != gid or stat.S_IMODE(value.st_mode) != 0o600):
            refuse("FileMetadata")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            refuse("FileSize")
        return raw
    finally:
        os.close(fd)


def sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, raw, uid, gid, expected=None):
    if path.exists() or path.is_symlink():
        current = read_guarded(path, uid, gid)
        if expected is not None and current != expected:
            refuse("ConcurrentEdit")
    elif expected is not None:
        refuse("MissingExpectedFile")
    fd, temporary = tempfile.mkstemp(prefix=".keeper-trust-stage.", dir=path.parent)
    try:
        value = os.fstat(fd)
        if value.st_uid != uid or value.st_gid != gid:
            refuse("StageOwner")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
        if expected is not None and read_guarded(path, uid, gid) != expected:
            refuse("ConcurrentEdit")
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        os.close(fd)
        if os.path.lexists(temporary):
            os.unlink(temporary)


def save_receipt(directory, receipt, uid, gid):
    raw = (json.dumps(receipt, sort_keys=True) + "\n").encode("ascii")
    atomic_write(directory / RECEIPT, raw, uid, gid)


def load_receipt(directory, uid, gid):
    path = directory / RECEIPT
    if not path.exists() and not path.is_symlink():
        return None
    try:
        receipt = json.loads(read_guarded(path, uid, gid, 4096))
    except (json.JSONDecodeError, UnicodeError):
        refuse("ReceiptFormat")
    if (set(receipt) != {"version", "phase", "backup", "baselineSHA256",
                         "installedSHA256", "entrySHA256"}
            or receipt["version"] != 1 or receipt["phase"] not in {"Prepared", "Installed", "RolledBack"}
            or not isinstance(receipt["backup"], str)
            or not receipt["backup"].startswith(BACKUP_PREFIX)
            or Path(receipt["backup"]).name != receipt["backup"]):
        refuse("ReceiptIdentity")
    for key in ("baselineSHA256", "installedSHA256", "entrySHA256"):
        value = receipt[key]
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            refuse("ReceiptDigest")
    return receipt


def lock_directory(directory, uid, gid):
    path = directory / ".keeper-node-trust.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    value = os.fstat(fd)
    if (not stat.S_ISREG(value.st_mode) or value.st_uid != uid
            or value.st_gid != gid or stat.S_IMODE(value.st_mode) != 0o600):
        os.close(fd)
        refuse("LockMetadata")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        refuse("ConcurrentInstaller")
    return fd


def install(directory, uid, gid, public):
    guard_directory(directory, uid, gid, 0o700)
    authorized = directory / "authorized_keys"
    line = authorized_line(public)
    fd = lock_directory(directory, uid, gid)
    try:
        current = read_guarded(authorized, uid, gid)
        receipt = load_receipt(directory, uid, gid)
        if receipt is not None:
            if receipt["phase"] == "RolledBack" or receipt["entrySHA256"] != digest(line):
                refuse("ReceiptConflict")
            original = read_guarded(directory / receipt["backup"], uid, gid)
            expected = original + b"\n" + line + b"\n"
            if digest(original) != receipt["baselineSHA256"] or digest(expected) != receipt["installedSHA256"]:
                refuse("BackupIdentity")
            if current == expected:
                receipt["phase"] = "Installed"
                save_receipt(directory, receipt, uid, gid)
                return {"result": "already-installed", "originalBytesPreserved": True,
                        "backup": str(directory / receipt["backup"])}
            if receipt["phase"] != "Prepared" or current != original:
                refuse("LaterEdit")
        else:
            if public in current or MARKER.encode("ascii") in current:
                refuse("ExistingKeyConflict")
            original = current
            expected = original + b"\n" + line + b"\n"
            backup_fd, backup = tempfile.mkstemp(prefix=BACKUP_PREFIX, dir=directory)
            os.close(backup_fd)
            atomic_write(Path(backup), original, uid, gid)
            receipt = {"version": 1, "phase": "Prepared", "backup": Path(backup).name,
                       "baselineSHA256": digest(original), "installedSHA256": digest(expected),
                       "entrySHA256": digest(line)}
            save_receipt(directory, receipt, uid, gid)
        atomic_write(authorized, expected, uid, gid, expected=original)
        installed = read_guarded(authorized, uid, gid)
        if installed != expected or installed[:len(original)] != original:
            refuse("PreservationCheck")
        receipt["phase"] = "Installed"
        save_receipt(directory, receipt, uid, gid)
        return {"result": "installed", "originalBytesPreserved": True,
                "backup": str(directory / receipt["backup"])}
    finally:
        os.close(fd)


def rollback(directory, uid, gid):
    guard_directory(directory, uid, gid, 0o700)
    fd = lock_directory(directory, uid, gid)
    try:
        receipt = load_receipt(directory, uid, gid)
        if receipt is None:
            refuse("MissingReceipt")
        original = read_guarded(directory / receipt["backup"], uid, gid)
        if digest(original) != receipt["baselineSHA256"]:
            refuse("BackupIdentity")
        authorized = directory / "authorized_keys"
        current = read_guarded(authorized, uid, gid)
        if receipt["phase"] == "RolledBack" and current == original:
            return {"result": "already-rolled-back"}
        if receipt["phase"] == "Prepared" and current == original:
            pass
        elif digest(current) == receipt["installedSHA256"]:
            atomic_write(authorized, original, uid, gid, expected=current)
        else:
            refuse("LaterEdit")
        if read_guarded(authorized, uid, gid) != original:
            refuse("RollbackCheck")
        receipt["phase"] = "RolledBack"
        save_receipt(directory, receipt, uid, gid)
        return {"result": "rolled-back", "originalBytesPreserved": True}
    finally:
        os.close(fd)


def main():
    signal.alarm(20)
    if len(sys.argv) != 2 or sys.argv[1] not in {"install", "rollback"}:
        refuse("Operation")
    account = pwd.getpwnam("dev-env")
    if (os.getuid() == 0 or os.getuid() != account.pw_uid
            or os.getgid() != account.pw_gid or account.pw_dir != "/home/dev-env"):
        refuse("AccountIdentity")
    home = Path(account.pw_dir)
    guard_directory(home, account.pw_uid, account.pw_gid, 0o755)
    directory = home / ".ssh"
    if sys.argv[1] == "install":
        public = normalize_public(sys.stdin.buffer.read(LIMIT + 1))
        result = install(directory, account.pw_uid, account.pw_gid, public)
    else:
        result = rollback(directory, account.pw_uid, account.pw_gid)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        reason = str(exc) if str(exc) in REFUSAL_REASONS else "UnexpectedFailure"
        print(json.dumps({"result": "refused", "reason": reason}))
        sys.exit(1)
    except Exception:
        print(json.dumps({"result": "refused", "reason": "UnexpectedFailure"}))
        sys.exit(1)
