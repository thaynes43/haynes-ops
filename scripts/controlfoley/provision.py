#!/usr/bin/env python3
"""Provision only pinned TV2A inference assets; never invoked by runtime inference."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import urllib.request
import uuid

SOURCE_REVISION = "b6c902888d45d75f022e253e3db29836360a3f82"
COMFY_REFERENCE_REVISION = "c918ad747e2997dd80e7aaa86765603f1c2d136d"
MODEL_REPO = "YJX-Xiaomi/ControlFoley"
MODEL_REVISION = "cde633f5c3f5ccbedf07994ad6dc124b761df22f"
CLIP_REPO = "apple/DFN5B-CLIP-ViT-H-14-384"
CLIP_REVISION = "01b771ed0d1395ca5ffdd279897d665ebe00dfd2"
VOCODER_REPO = "nvidia/bigvgan_v2_44khz_128band_512x"
VOCODER_REVISION = "95a9d1dcb12906c03edd938d77b9333d6ded7dfb"

# SHA256 values obtained from immutable upstream Git/LFS objects, not TOFU downloads.
# local path, repository, revision, repository path, bytes, sha256
ASSETS = (
    ("weights/controlfoley.pth", MODEL_REPO, MODEL_REVISION, "weights/controlfoley.pth", 11292123018, "8e2359cdf412b07381282c07e53205ce3d778a4a8be1edbc5c00d373e5186ed6"),
    ("ext_weights/v1-44.pth", MODEL_REPO, MODEL_REVISION, "ext_weights/v1-44.pth", 1221942998, "ab6cc15dc31947675f75c950c41f4dcfd0d6d1817555ac871f809ec388e4651a"),
    ("ext_weights/synchformer_state_dict.pth", MODEL_REPO, MODEL_REVISION, "ext_weights/synchformer_state_dict.pth", 950058171, "8aff082f2df5c3bc52759db0c865c7ee772ae6400b860d1b7e90413f2defb67c"),
    ("ext_weights/cav_mae_st.pth", MODEL_REPO, MODEL_REVISION, "ext_weights/cav_mae_st.pth", 762301703, "de9f7dff5a643690ae9e98bb686abf4f259456a9f3be8ef1fa403384eeefc6d8"),
    ("clip/open_clip_pytorch_model.bin", CLIP_REPO, CLIP_REVISION, "open_clip_pytorch_model.bin", 3947081637, "c07a17b547d461c60a3cce5062b26bf8545b13de602c4c59d8490361eb716033"),
    ("clip/open_clip_config.json", CLIP_REPO, CLIP_REVISION, "open_clip_config.json", 735, "e043bd1129b97d3afc77eef2e8bfda940af588dabf2cbaacc21e956345402f67"),
    ("clip/LICENSE", CLIP_REPO, CLIP_REVISION, "LICENSE", 5820, "d8f5d9dfbdb60dc81b8f1720421699e7811070f0b46f6668f90253d5ac8c0b20"),
    ("vocoder/bigvgan_generator.pt", VOCODER_REPO, VOCODER_REVISION, "bigvgan_generator.pt", 489041291, "d9fe7ec6bd0b44ed9d66973d5012d8181c1570b01e5c72df51973e241dccd357"),
    ("vocoder/config.json", VOCODER_REPO, VOCODER_REVISION, "config.json", 1403, "65b7f487bfaf15256056a75f6667ef5f640214c981922a111927873705ea4ddb"),
    ("vocoder/LICENSE", VOCODER_REPO, VOCODER_REVISION, "LICENSE", 1076, "90459cd52fc41bd723df7c0c76fac1e4dd60e6bfd644a7e2a93f325bed4f6d95"),
)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest():
    return {"schema_version": 1, "source_revision": SOURCE_REVISION,
            "assets": [{"path": p, "repo": r, "revision": rev, "remote_path": f,
                        "bytes": n, "sha256": digest} for p, r, rev, f, n, digest in ASSETS]}


def verify(weights_dir, *, hashes=True):
    root = Path(weights_dir)
    expected = manifest()
    if json.loads((root / "manifest.json").read_text()) != expected:
        raise ValueError("Weights manifest differs from pinned release")
    for asset in expected["assets"]:
        path = root / asset["path"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size != asset["bytes"]:
            raise ValueError(f"Missing or wrong-sized asset: {asset['path']}")
        if hashes and sha256(path) != asset["sha256"]:
            raise ValueError(f"SHA256 mismatch: {asset['path']}")
    return expected


def provision(root):
    root = Path(root)
    if not root.is_absolute():
        raise ValueError("weights-dir must be absolute")
    root.mkdir(parents=True, exist_ok=True)
    remaining = sum(n for p, _, _, _, n, digest in ASSETS
                    if not (root / p).is_file() or (root / p).stat().st_size != n)
    if shutil.disk_usage(root).free < remaining + 1024 ** 3:
        raise ValueError("Insufficient free disk for pinned assets plus 1 GiB reserve")
    for relative, repo, revision, remote, size, digest in ASSETS:
        destination = root / relative
        if destination.exists():
            if destination.is_symlink() or destination.stat().st_size != size or sha256(destination) != digest:
                raise ValueError(f"Existing asset fails verification; refusing overwrite: {relative}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".partial")
        # Exclusive creation prevents concurrent provisioning from corrupting downloads.
        created = False
        try:
            with partial.open("xb") as output:
                created = True
                url = f"https://huggingface.co/{repo}/resolve/{revision}/{remote}"
                with urllib.request.urlopen(url, timeout=120) as response:
                    total = 0
                    for chunk in iter(lambda: response.read(8 * 1024 * 1024), b""):
                        total += len(chunk)
                        if total > size:
                            raise ValueError(f"Oversized download: {relative}")
                        output.write(chunk)
            if partial.stat().st_size != size or sha256(partial) != digest:
                raise ValueError(f"Download verification failed: {relative}")
            os.link(partial, destination)  # Atomic publication, no replacement.
        finally:
            # Do not remove another provisioner's pre-existing partial file.
            if created:
                partial.unlink(missing_ok=True)
        print(f"Verified {relative}", flush=True)
    expected = manifest()
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        if json.loads(manifest_path.read_text()) != expected:
            raise ValueError("Existing manifest differs; refusing overwrite")
    else:
        temp = root / ("manifest-" + uuid.uuid4().hex + ".tmp")
        try:
            with temp.open("x") as handle:
                json.dump(expected, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temp, manifest_path)
        finally:
            temp.unlink(missing_ok=True)
    return verify(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights-dir", type=Path, default=Path("/workspace/controlfoley-models"))
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = verify(args.weights_dir) if args.verify_only else provision(args.weights_dir)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
