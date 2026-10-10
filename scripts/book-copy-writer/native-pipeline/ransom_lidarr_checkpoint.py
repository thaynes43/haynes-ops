"""Pinned generic checkpoint plus only the existing read-only Lidarr profile."""
import hashlib
import os
from pathlib import Path
import stat

CORE_SHA256 = "1ceacf5d487c32b3107ac7db6c166e2384739474505d68640ea7b4921402b622"
_path = Path(__file__).with_name("checkpoint-owned-job.core.py")
_fd = os.open(_path, os.O_RDONLY | os.O_NOFOLLOW)
with os.fdopen(_fd, "rb") as _handle:
    _info = os.fstat(_handle.fileno())
    if (not stat.S_ISREG(_info.st_mode) or _info.st_uid != os.getuid()
            or _info.st_nlink != 1 or _info.st_mode & 0o777 != 0o600
            or _info.st_size > 1024 * 1024):
        raise ValueError("private original checkpoint identity differs")
    _raw = _handle.read()
    _after = os.fstat(_handle.fileno())
    _stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_uid", "st_nlink")
    if any(getattr(_info, key) != getattr(_after, key) for key in _stable) or hashlib.sha256(_raw).hexdigest() != CORE_SHA256:
        raise ValueError("original generic checkpoint source changed")
_core = {"__name__": "ransom_original_checkpoint", "__file__": str(_path)}
exec(compile(_raw, str(_path), "exec"), _core)
_core["PROFILES"]["LIDARR_CAPTURE_PHASE_READY"] = (
    "0", {"LIDARR_CAPTURE_PHASE_READY", "LIDARR_CAPTURE_DEADLINE_EPOCH"})
globals().update({name: value for name, value in _core.items() if not name.startswith("_")})

if __name__ == "__main__":
    main()
