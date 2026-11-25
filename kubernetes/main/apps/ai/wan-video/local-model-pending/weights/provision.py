"""Provision the CR-20 GGUF pair into a new, verified directory."""
from contextlib import closing
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

HUB = "https://huggingface.co/unsloth/Qwen3.5-9B-GGUF"
DNS_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
CDN = re.compile(rf"(?:{DNS_LABEL}\.)+cdn\.hf\.co", re.IGNORECASE | re.ASCII)
FREE_BYTES = 8 * 1024**3
FREE_INODES = 1024
STAGING_INODES = 16
CHUNK = 1024 * 1024
FLUSH_EVERY = 64 * CHUNK
RENAME_NOREPLACE = 1
AT_FDCWD = -100


class ProvisionError(ValueError):
    pass


def reserve(path, *, size=0, inodes=0):
    space = os.statvfs(path)
    if space.f_bavail * space.f_frsize < FREE_BYTES + size:
        raise ProvisionError("insufficient_free_bytes")
    if space.f_favail < FREE_INODES + inodes:
        raise ProvisionError("insufficient_free_inodes")


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(staged, target):
    """A competing or existing revision must always win over this attempt."""
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(AT_FDCWD, os.fsencode(staged), AT_FDCWD, os.fsencode(target), RENAME_NOREPLACE):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


class PinnedRedirect(HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        # Record the destination before urllib follows it. Never log signed paths,
        # query strings, ports, or userinfo.
        location = headers.get("Location") or headers.get("URI")
        hostname = None
        allowed = False
        if location:
            try:
                dest = urlsplit(urljoin(req.full_url, location))
                hostname = dest.hostname
                allowed = dest.scheme == "https" and CDN.fullmatch(dest.netloc) is not None
            except ValueError:
                pass
        safe_hostname = hostname if hostname and re.fullmatch(rf"{DNS_LABEL}(?:\.{DNS_LABEL})*", hostname) else "invalid"
        print(f"Download redirect host={safe_hostname} allowed={str(allowed).lower()}", flush=True)
        if not allowed:
            raise ProvisionError("unexpected_download_redirect")
        return super().http_error_302(req, fp, code, msg, headers)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def settle(fd):
    """Write what has been written so far to the volume and let its pages go.

    A multi-gigabyte file written with one fsync at the end keeps its pages in the
    container's memory account until then; the first attempt was killed for memory
    after 570 MB. Flushing every FLUSH_EVERY bytes bounds that to one interval.
    """
    os.fsync(fd)
    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)


def download(opener, url, target, item, cap):
    expected = item["bytes"]
    if expected > cap:
        raise ProvisionError("byte_cap_exceeded")
    reserve(target.parent, size=expected + CHUNK, inodes=1)
    req = Request(url, headers={"Accept-Encoding": "identity"})
    with closing(opener.open(req, timeout=60)) as source:
        length = source.headers.get("Content-Length")
        if source.status != 200 or (length is not None and length != str(expected)):
            raise ProvisionError("unexpected_download_length")
        actual = flushed = 0
        with target.open("xb", buffering=0) as file:
            while block := source.read(min(CHUNK, expected - actual + 1)):
                actual += len(block)
                if actual > expected:
                    raise ProvisionError("byte_cap_exceeded")
                reserve(target.parent, size=len(block) + CHUNK)
                if file.write(block) != len(block):
                    raise ProvisionError("short_write")
                if actual - flushed >= FLUSH_EVERY:
                    settle(file.fileno())
                    flushed = actual
            settle(file.fileno())
        if actual != expected:
            raise ProvisionError("unexpected_download_length")
    digest = hashlib.sha256()
    with target.open("rb", buffering=0) as file:
        seen = 0
        while block := file.read(CHUNK):
            digest.update(block)
            seen += len(block)
            if seen % FLUSH_EVERY < CHUNK:
                os.posix_fadvise(file.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
    actual_hash = digest.hexdigest()
    if actual_hash != item["sha256"]:
        raise ProvisionError("download_hash_mismatch")
    target.chmod(0o444)
    return actual


def provision(root, pins, opener):
    model = pins["model"]
    revision = pins["revision"]
    if (not re.fullmatch(r"[a-z0-9_.-]+", model) or model in {".", ".."}
            or not re.fullmatch(r"[0-9a-f]{40}", revision)
            or [entry["name"] for entry in pins["files"]] != ["Qwen3.5-9B-Q5_K_M.gguf", "mmproj-BF16.gguf"]
            or sum(entry["bytes"] for entry in pins["files"]) != pins["byte_cap"]):
        raise ProvisionError("invalid_weights_pins")

    reserve(root, size=pins["byte_cap"] + CHUNK, inodes=STAGING_INODES)
    parent = root / model
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / revision
    if os.path.lexists(target):
        raise ProvisionError("revision_already_exists")
    # A hard stop also leaves this attempt quarantined. There is no cleanup or retry.
    staged = Path(tempfile.mkdtemp(prefix=".quarantine-", dir=parent))
    cap = pins["byte_cap"]
    for item in pins["files"]:
        name = item["name"]
        cap -= download(opener, f"{HUB}/resolve/{revision}/{name}", staged / name, item, cap)
    reserve(staged, size=CHUNK, inodes=1)
    with (staged / "manifest.json").open("x") as manifest:
        json.dump(pins, manifest, indent=2)
        manifest.write("\n")
        manifest.flush()
        os.fsync(manifest.fileno())
    (staged / "manifest.json").chmod(0o444)
    staged.chmod(0o555)
    reserve(staged)
    sync_directory(staged)
    publish(staged, target)
    sync_directory(parent)
    sync_directory(root)
    return target


def main():
    try:
        pins = json.loads(Path("/config/weights.json").read_text())
        opener = build_opener(ProxyHandler({}), PinnedRedirect())
        target = provision(Path("/workspace/local-model-weights"), pins, opener)
    except ProvisionError as exc:
        print(f"Provisioning stopped: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError):
        # A redirect may contain a signed URL. Report a stable code only.
        print("Provisioning stopped: download_or_filesystem_error", file=sys.stderr)
        return 1
    print(f"Verified and published {target.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
