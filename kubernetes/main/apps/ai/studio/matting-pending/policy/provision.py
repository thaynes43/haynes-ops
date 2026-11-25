"""Provision only CR-33's pinned graph in a new Studio jobs allocation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

HUB = "huggingface.co"
CDN = "us.aws.cdn.hf.co"
REVISION = "de15b22ba131738a16dff04aab8bdf8dc32e3ac1"
URL = f"https://{HUB}/onnx-community/BiRefNet_lite-ONNX/resolve/{REVISION}/onnx/model.onnx"
GRAPH_BYTES = 224_005_088
GRAPH_SHA256 = "5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333"
BUDGET = 1024**3
RESERVE = 32 * 1024**3
CHUNK = 1024**2
FLUSH_EVERY = 64 * CHUNK
READ_SECONDS = 15
DOWNLOAD_SECONDS = 240
ALLOCATION = re.compile(r"matting-op459-birefnet-lite-v1-[0-9]{2}")


class ProvisionError(ValueError):
    pass


class PinnedRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        destination = urlsplit(new_url)
        allowed = destination.scheme == "https" and destination.netloc == CDN
        # The host is the evidence an operator needs when a redirect is refused.
        print(json.dumps({"redirect_host": destination.hostname, "allowed": allowed}), file=sys.stderr)
        if not allowed:
            raise ProvisionError("unexpected_download_redirect")
        return super().redirect_request(request, response, code, message, headers, new_url)


def capacity(path: Path, written: int) -> None:
    if written > BUDGET:
        raise ProvisionError("byte_budget_exceeded")
    space = os.statvfs(path)
    if space.f_bavail * space.f_frsize < RESERVE + BUDGET - written:
        raise ProvisionError("free_reserve_too_small")


def sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def settle(fd: int) -> None:
    """Commit what has been written and let its cached pages go.

    A file written with one fsync at the end keeps its pages in the container's
    memory account until then: pages not yet committed to the server cannot be
    reclaimed. The first GGUF attempt of story 442 was killed for memory that way
    (haynes-ops PR 5053). Settling every FLUSH_EVERY bytes bounds it to one
    interval, and dropping the cache before hashing makes the read-back come
    from the server and not from this client's cache.
    """
    os.fsync(fd)
    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as source:
        os.posix_fadvise(source.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        while block := source.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def download(opener, target: Path, deadline: float) -> None:
    request = Request(URL, headers={"Accept-Encoding": "identity", "User-Agent": "haynes-studio-matting/1"})
    with opener.open(request, timeout=READ_SECONDS) as source:
        length = source.headers.get("Content-Length")
        if (source.status != 200 or source.headers.get("Content-Encoding", "identity") != "identity"
                or (length is not None and length != str(GRAPH_BYTES))):
            raise ProvisionError("unexpected_download_length")
        count = settled = 0
        with target.open("xb", buffering=0) as output:
            while True:
                if monotonic() >= deadline:
                    raise ProvisionError("download_deadline")
                block = source.read(min(CHUNK, GRAPH_BYTES - count + 1))
                if not block:
                    break
                if count + len(block) > GRAPH_BYTES:
                    raise ProvisionError("unexpected_download_length")
                capacity(target.parent, count)
                if output.write(block) != len(block):
                    raise ProvisionError("short_write")
                count += len(block)
                capacity(target.parent, count)
                if count - settled >= FLUSH_EVERY:
                    settle(output.fileno())
                    settled = count
            settle(output.fileno())
        if count != GRAPH_BYTES:
            raise ProvisionError("unexpected_download_length")
    if digest_file(target) != GRAPH_SHA256:
        raise ProvisionError("download_hash_mismatch")


def provision(root: Path, allocation: str, opener, manifest: bytes, notice: bytes) -> dict:
    if not ALLOCATION.fullmatch(allocation):
        raise ProvisionError("invalid_allocation")
    profile_hash = hashlib.sha256(manifest).hexdigest()
    documents = {
        "profile.json": manifest,
        "profile.sha256": (profile_hash + "\n").encode("ascii"),
        "BiRefNet-LICENSE": notice,
    }
    if GRAPH_BYTES + sum(map(len, documents.values())) > BUDGET:
        raise ProvisionError("byte_budget_exceeded")
    capacity(root, 0)
    run = root / allocation
    try:
        # This is the no-clobber reservation on NFS. Every provisioner must
        # own a new allocation before it can enter it or use its final name.
        run.mkdir(mode=0o700)
    except FileExistsError as error:
        raise ProvisionError("allocation_already_exists") from error
    sync_dir(root)
    staged = run / ".quarantine"
    staged.mkdir(mode=0o700)
    download(opener, staged / "model.onnx", monotonic() + DOWNLOAD_SECONDS)
    written = GRAPH_BYTES
    for name, data in documents.items():
        capacity(staged, written)
        with (staged / name).open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        written += len(data)
        capacity(staged, written)
    for file in staged.iterdir():
        file.chmod(0o444)
    staged.chmod(0o555)
    sync_dir(staged)
    published = run / "weights"
    if os.path.lexists(published):
        raise ProvisionError("weights_already_exist")
    # Linux NFS rename lacks RENAME_NOREPLACE. Rename inside the private,
    # exclusively allocated directory, where this run is the sole writer.
    staged.rename(published)
    try:
        sync_dir(run)
        run.chmod(0o555)
        sync_dir(run)
        sync_dir(root)
    except OSError:
        # Restore the quarantine boundary on a failed publication sync.
        run.chmod(0o700)
        raise
    return {"allocation": allocation, "graph_bytes": GRAPH_BYTES, "graph_sha256": GRAPH_SHA256,
            "profile_sha256": profile_hash}


def main() -> int:
    from haynes_studio.matting.profile import MatteError, Profile

    try:
        if os.getuid() != 1000 or os.getgid() != 1000:
            raise ProvisionError("wrong_writer_identity")
        profile = Profile.installed()
        if (profile.graph_bytes, profile.graph_sha256) != (GRAPH_BYTES, GRAPH_SHA256):
            raise ProvisionError("image_graph_pin_mismatch")
        opener = build_opener(ProxyHandler({}), PinnedRedirect())
        result = provision(
            Path("/jobs"), os.environ.get("STUDIO_MATTING_ALLOCATION", ""), opener,
            profile.manifest(), Path("/opt/studio/BiRefNet-LICENSE").read_bytes(),
        )
    except ProvisionError as error:
        print(json.dumps({"code": str(error)}), file=sys.stderr)
        return 1
    except (OSError, ValueError, MatteError):
        # Some transport errors contain a signed CDN URL.
        print(json.dumps({"code": "download_or_filesystem_error"}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
