"""Provision the CR-35 graph in a new private Studio jobs allocation.

The bounded download, fsync/cache-drop and create-only publication follow the
installed matting provisioner. Git blob verification binds the publisher pin.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from time import monotonic
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

REVISION = "6a4812c4ef1c4f5947d79beafa32a05a6eb4a94d"
SOURCE = f"https://raw.githubusercontent.com/Saafke/FSRCNN_Tensorflow/{REVISION}"
URL = f"{SOURCE}/models/FSRCNN_x2.pb"
GRAPH_BYTES = 38_973
GRAPH_SHA256 = "366b33f0084c7b3f2bf6724f0a2c77bca94fcec9d7b6d72389d330073b380d5c"
GRAPH_BLOB = "2509b0e19b8a45ebce2ba50128e2fcb43ce8589c"
BUDGET = 8 * 1024**3
RESERVE = 32 * 1024**3
INODE_BUDGET_AND_RESERVE = 2048
CHUNK = 1024**2
FLUSH_EVERY = 64 * CHUNK
READ_SECONDS = 15
DOWNLOAD_SECONDS = 240
ALLOCATION = re.compile(r"upscaling-op461-fsrcnn-x2-v1-[0-9]{2}")


class ProvisionError(ValueError):
    pass


class PinnedRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise ProvisionError("unexpected_download_redirect")


def capacity(path: Path, written: int, *, check_inodes=False) -> None:
    if written > BUDGET:
        raise ProvisionError("byte_budget_exceeded")
    space = os.statvfs(path)
    if check_inodes and space.f_files and space.f_favail != (1 << 64) - 1:
        if space.f_favail < INODE_BUDGET_AND_RESERVE:
            raise ProvisionError("free_inode_reserve_too_small")
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


def digests_file(path: Path) -> tuple[str, str]:
    digest = hashlib.sha256()
    blob = hashlib.sha1(b"blob " + str(GRAPH_BYTES).encode() + b"\0", usedforsecurity=False)
    with path.open("rb", buffering=0) as source:
        os.posix_fadvise(source.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        while block := source.read(CHUNK):
            digest.update(block)
            blob.update(block)
    return digest.hexdigest(), blob.hexdigest()


def download(opener, target: Path, deadline: float) -> None:
    request = Request(URL, headers={"Accept-Encoding": "identity", "User-Agent": "haynes-studio-upscaling/1"})
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
    if digests_file(target) != (GRAPH_SHA256, GRAPH_BLOB):
        raise ProvisionError("download_hash_mismatch")


def provision(root: Path, allocation: str, opener, manifest: bytes, notice: bytes) -> dict:
    if not ALLOCATION.fullmatch(allocation):
        raise ProvisionError("invalid_allocation")
    profile_hash = hashlib.sha256(manifest).hexdigest()
    documents = {
        "FSRCNN-NOTICE": notice,
        "profile.json": manifest,
        "profile.sha256": (profile_hash + "\n").encode("ascii"),
    }
    if GRAPH_BYTES + sum(map(len, documents.values())) > BUDGET:
        raise ProvisionError("byte_budget_exceeded")
    capacity(root, 0, check_inodes=True)
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
    download(opener, staged / "FSRCNN_x2.pb", monotonic() + DOWNLOAD_SECONDS)
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
            "graph_git_blob": GRAPH_BLOB,
            "profile_sha256": profile_hash, "notice_sha256": hashlib.sha256(notice).hexdigest()}


def main() -> int:
    from haynes_studio.upscaling.profile import Profile, UpscaleError

    try:
        if os.getuid() != 1000 or os.getgid() != 1000:
            raise ProvisionError("wrong_writer_identity")
        profile = Profile.installed()
        if (profile.graph_bytes, profile.graph_sha256, profile.graph_blob) != (GRAPH_BYTES, GRAPH_SHA256, GRAPH_BLOB):
            raise ProvisionError("image_graph_pin_mismatch")
        opener = build_opener(ProxyHandler({}), PinnedRedirect())
        result = provision(
            Path("/jobs"), os.environ.get("STUDIO_UPSCALING_ALLOCATION", ""), opener,
            profile.manifest(), Path("/opt/studio/FSRCNN-NOTICE").read_bytes(),
        )
    except ProvisionError as error:
        print(json.dumps({"code": str(error)}), file=sys.stderr)
        return 1
    except (OSError, ValueError, UpscaleError):
        # Transport and file exceptions can contain private paths.
        print(json.dumps({"code": "download_or_filesystem_error"}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
