#!/usr/bin/env python3
"""Fetch the one immutable Stable Audio 3 SFX bundle and attest its contents."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from huggingface_hub import hf_hub_download


MODEL_ROOT = Path("/models")
REPOSITORY = "stabilityai/stable-audio-3-optimized"
REVISION = "da6edc54ddba10bfd79a077102ded687f80e882b"
MANIFEST = "manifest.json"
MODEL_FILES = (
    "tflite/t5gemma/encoder_fp16.tflite",
    "tflite/sa3-sm-sfx/dit_fp32.tflite",
    "tflite/same-s/dec_w8a8.tflite",
)


def digest_file(path: Path) -> tuple[int, str]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size < 1024 * 1024:
            raise RuntimeError(f"Downloaded model is not a non-empty regular file: {path}")
        digest = hashlib.sha256()
        while block := os.read(descriptor, 8 * 1024 * 1024):
            digest.update(block)
        return info.st_size, digest.hexdigest()
    finally:
        os.close(descriptor)


def write_manifest(files: list[dict[str, object]]) -> None:
    document = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "revision": REVISION,
        "files": files,
    }
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=MODEL_ROOT, prefix=".manifest-", delete=False
    ) as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    os.chmod(temporary, 0o600)
    os.replace(temporary, MODEL_ROOT / MANIFEST)


def main() -> None:
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    records = []
    for relative in MODEL_FILES:
        print(f"fetching {relative} from {REPOSITORY}@{REVISION}", flush=True)
        downloaded = Path(hf_hub_download(
            repo_id=REPOSITORY,
            filename=relative,
            revision=REVISION,
            local_dir=MODEL_ROOT,
            token=False,
        ))
        expected = MODEL_ROOT / relative
        if downloaded != expected or downloaded.is_symlink():
            raise RuntimeError(f"Hugging Face returned an unexpected model path: {downloaded}")
        size, sha256 = digest_file(expected)
        records.append({"path": relative, "size": size, "sha256": sha256})
        print(f"verified {relative}: {size} bytes sha256={sha256}", flush=True)
    write_manifest(records)
    print(f"model bundle ready: {MODEL_ROOT / MANIFEST}", flush=True)


if __name__ == "__main__":
    main()

