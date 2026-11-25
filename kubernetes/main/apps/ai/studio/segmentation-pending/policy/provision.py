"""Provision the exact CR-34 profile into a new private Studio jobs allocation."""

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
BUDGET = 2 * 1024**3
RESERVE = 8 * 1024**3
ENTRY_BUDGET = 64
ENTRY_RESERVE = 1024
CHUNK = 1024**2
FLUSH_EVERY = 64 * CHUNK
READ_SECONDS = 15
DOWNLOAD_SECONDS = 540
ALLOCATION = re.compile(r"segmentation-op460-dino-sam-v1-[0-9]{2}")


class ProvisionError(ValueError):
    pass


class PinnedRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        source = urlsplit(request.full_url)
        destination = urlsplit(new_url)
        repo, separator, revision_and_file = source.path.partition("/resolve/")
        cached_file = "/api/resolve-cache/models" + repo + "/" + revision_and_file
        allowed = destination.scheme == "https" and source.netloc == HUB and separator and (
            (destination.netloc == HUB and destination.path == cached_file)
            or (destination.netloc == CDN and source.path.endswith("/model.safetensors"))
        )
        # Signed CDN query strings can carry credentials scoped to this download.
        print(json.dumps({"redirect_host": destination.hostname, "allowed": bool(allowed)}), file=sys.stderr)
        if not allowed:
            raise ProvisionError("unexpected_download_redirect")
        return super().redirect_request(request, response, code, message, headers, new_url)


def capacity(path: Path, written: int, entries: int) -> None:
    if written > BUDGET or entries > ENTRY_BUDGET:
        raise ProvisionError("allocation_budget_exceeded")
    space = os.statvfs(path)
    if space.f_bavail * space.f_frsize < RESERVE + BUDGET - written:
        raise ProvisionError("free_reserve_too_small")
    # CR-34 accepts the 64-entry budget when NFS omits its free-file count.
    if space.f_files > 0 and space.f_favail < ENTRY_RESERVE + ENTRY_BUDGET - entries:
        raise ProvisionError("free_file_reserve_too_small")


def sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def settle(fd: int) -> None:
    """Flush each bounded write interval and let the NFS pages go (OP459)."""
    os.fsync(fd)
    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)


def digest_file(path: Path, deadline: float) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as source:
        fd = source.fileno()
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        offset = 0
        while block := source.read(CHUNK):
            if monotonic() >= deadline:
                raise ProvisionError("download_deadline")
            digest.update(block)
            offset += len(block)
            if offset % FLUSH_EVERY == 0:
                os.posix_fadvise(fd, offset - FLUSH_EVERY, FLUSH_EVERY, os.POSIX_FADV_DONTNEED)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    return digest.hexdigest()


def download(opener, model: dict, entry: dict, target: Path, deadline: float,
             written: int, entries: int) -> None:
    url = f"https://{HUB}/{model['repository']}/resolve/{model['revision']}/{entry['name']}"
    size = entry["size_bytes"]
    request = Request(url, headers={"Accept-Encoding": "identity", "User-Agent": "haynes-studio-segmentation/1"})
    if monotonic() >= deadline:
        raise ProvisionError("download_deadline")
    with opener.open(request, timeout=READ_SECONDS) as source:
        length = source.headers.get("Content-Length")
        if (source.status != 200 or source.headers.get("Content-Encoding", "identity") != "identity"
                or (length is not None and length != str(size))):
            raise ProvisionError("unexpected_download_length")
        count = settled = 0
        with target.open("xb", buffering=0) as output:
            while True:
                if monotonic() >= deadline:
                    raise ProvisionError("download_deadline")
                block = source.read(min(CHUNK, size - count + 1))
                if not block:
                    break
                if count + len(block) > size:
                    raise ProvisionError("unexpected_download_length")
                capacity(target.parent, written + count, entries)
                if output.write(block) != len(block):
                    raise ProvisionError("short_write")
                count += len(block)
                capacity(target.parent, written + count, entries)
                if count - settled >= FLUSH_EVERY:
                    settle(output.fileno())
                    settled = count
            settle(output.fileno())
        if count != size:
            raise ProvisionError("unexpected_download_length")
    if digest_file(target, deadline) != entry["sha256"]:
        raise ProvisionError("download_hash_mismatch")


def provision(root: Path, allocation: str, opener, profile) -> dict:
    if not ALLOCATION.fullmatch(allocation):
        raise ProvisionError("invalid_allocation")
    manifest = profile.manifest()
    profile_hash = hashlib.sha256(manifest).hexdigest()
    models = profile.model_files()
    documents = {
        "profile.sha256": (profile_hash + "\n").encode("ascii"),
        "profile.json": manifest,
    }
    entries = 2 + len(models) + len(documents) + sum(len(model["files"]) for model in models)
    payload = sum(entry["size_bytes"] for model in models for entry in model["files"])
    if (entries > ENTRY_BUDGET or payload + sum(map(len, documents.values())) > BUDGET):
        raise ProvisionError("allocation_budget_exceeded")
    capacity(root, 0, 0)
    run = root / allocation
    try:
        # The exclusive allocation is the publication lock on this NFS share.
        run.mkdir(mode=0o700)
    except FileExistsError as error:
        raise ProvisionError("allocation_already_exists") from error
    sync_dir(root)
    staged = run / ".quarantine"
    staged.mkdir(mode=0o700)
    elapsed_deadline = monotonic() + DOWNLOAD_SECONDS
    written = 0
    for model in models:
        directory = staged / model["directory"]
        directory.mkdir(mode=0o700)
        for entry in model["files"]:
            target = directory / entry["name"]
            download(opener, model, entry, target, elapsed_deadline, written, entries)
            written += entry["size_bytes"]
            target.chmod(0o444)
        directory.chmod(0o555)
        sync_dir(directory)
    for name, data in documents.items():
        capacity(staged, written, entries)
        target = staged / name
        with target.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        written += len(data)
        target.chmod(0o444)
    staged.chmod(0o555)
    sync_dir(staged)
    published = run / "weights"
    if os.path.lexists(published):
        raise ProvisionError("weights_already_exist")
    capacity(staged, written, entries)
    staged.rename(published)
    try:
        sync_dir(run)
        run.chmod(0o555)
        sync_dir(run)
        sync_dir(root)
    except OSError:
        # Restore the private quarantine boundary on a failed publication sync.
        run.chmod(0o700)
        raise
    return {
        "allocation": allocation, "download_bytes": payload, "profile_sha256": profile_hash,
        "image_digest": profile.image_digest, "models": models,
    }


def main() -> int:
    from haynes_studio.segmentation.profile import Profile, SegmentationError

    try:
        if os.getuid() != 1000 or os.getgid() != 1000:
            raise ProvisionError("wrong_writer_identity")
        opener = build_opener(ProxyHandler({}), PinnedRedirect())
        result = provision(
            Path("/jobs"), os.environ.get("STUDIO_SEGMENTATION_ALLOCATION", ""), opener, Profile.installed(),
        )
    except ProvisionError as error:
        result = {"code": str(error)}
    except (OSError, ValueError, SegmentationError):
        result = {"code": "download_profile_or_filesystem_error"}
    print(json.dumps(result, sort_keys=True), flush=True)
    return int("code" in result)


if __name__ == "__main__":
    raise SystemExit(main())
