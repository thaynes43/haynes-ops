# syntax=docker/dockerfile:1
# Authentik server 2026.8.5, pinned by the published multi-arch index digest.
# The only filesystem change is three access_token argument removals in
# authentik/providers/oauth2/utils.py. No package install and no runtime
# USER, entrypoint, env, or cmd change. SOURCE_DATE_EPOCH is the base image
# config created time, used so a rebuild of these bytes stays identical.
FROM ghcr.io/goauthentik/server@sha256:602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5
ARG SOURCE_DATE_EPOCH=1792582296
USER root
RUN /ak-root/.venv/bin/python -I -B <<'PY'
import hashlib
import os
import stat
from pathlib import Path

path = Path("/authentik/providers/oauth2/utils.py")
source_sha = "131cfcb81374f9beccb7cad6830fbcc9917f030ea5d3e4cfb26b470db6d6ebcb"
patched_sha = "ae1494c6c145d094fd1ad473517f4c0e5fbb8fdfc7b145c651235f29f1871b97"
before = path.read_bytes()
if hashlib.sha256(before).hexdigest() != source_sha:
    raise SystemExit("base utils hash mismatch")
text = before.decode()
replacements = (
    (
        'LOGGER.debug("Token does not exist", access_token=access_token)',
        'LOGGER.debug("Token does not exist")',
    ),
    (
        'LOGGER.debug("Token has expired", access_token=access_token)',
        'LOGGER.debug("Token has expired")',
    ),
    (
        'LOGGER.warning("Revoked token was used", access_token=access_token)',
        'LOGGER.warning("Revoked token was used")',
    ),
)
for old, new in replacements:
    if text.count(old) != 1:
        raise SystemExit("expected exactly one logging call")
    text = text.replace(old, new, 1)
data = text.encode()
if hashlib.sha256(data).hexdigest() != patched_sha:
    raise SystemExit("patched utils hash mismatch")
if b"access_token=access_token" in data:
    raise SystemExit("logging argument remains")
mode = path.stat()
path.write_bytes(data)
os.chmod(path, stat.S_IMODE(mode.st_mode))
os.chown(path, mode.st_uid, mode.st_gid)
written = path.stat().st_mtime
for item in path.parent.glob("__pycache__/utils*.pyc"):
    if item.stat().st_mtime >= written:
        raise SystemExit("bytecode cache was written")
PY
USER 1000
