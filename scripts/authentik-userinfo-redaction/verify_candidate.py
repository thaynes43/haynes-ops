#!/usr/bin/env python3
"""Prove the candidate image differs by the three logging argument removals.

Docker inspect input contains image environment values. This prints match
booleans, digests, and layer paths only.
"""
import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path

SOURCE_SHA = "131cfcb81374f9beccb7cad6830fbcc9917f030ea5d3e4cfb26b470db6d6ebcb"
PATCHED_SHA = "ae1494c6c145d094fd1ad473517f4c0e5fbb8fdfc7b145c651235f29f1871b97"
UTILS = "authentik/providers/oauth2/utils.py"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def runtime_identity(inspect_doc):
    image = inspect_doc[0]
    config = image["Config"]
    env = config.get("Env") or []
    labels = config.get("Labels") or {}
    health = config.get("Healthcheck") or {}
    return {
        "architecture": image.get("Architecture"),
        "os": image.get("Os"),
        "user": config.get("User"),
        "entrypoint": config.get("Entrypoint"),
        "cmd": config.get("Cmd"),
        "workingDir": config.get("WorkingDir"),
        "envCount": len(env),
        "envSha256": sha256("\n".join(env).encode()),
        "healthcheck": health.get("Test"),
        "labelsSha256": sha256(json.dumps(labels, sort_keys=True).encode()),
        "version": labels.get("org.opencontainers.image.version"),
        "rootfs": image.get("RootFS", {}).get("Layers") or [],
        "imageId": image.get("Id"),
    }


def same_runtime(base, candidate):
    keys = (
        "architecture", "os", "user", "entrypoint", "cmd", "workingDir",
        "envCount", "envSha256", "healthcheck", "labelsSha256", "version",
    )
    mismatches = [key for key in keys if base[key] != candidate[key]]
    prefix = len(candidate["rootfs"]) == len(base["rootfs"]) + 1 and candidate["rootfs"][:-1] == base["rootfs"]
    return {
        "runtimeMatch": not mismatches and prefix,
        "mismatches": mismatches,
        "rootfsPrefixMatch": prefix,
        "baseLayers": len(base["rootfs"]),
        "candidateLayers": len(candidate["rootfs"]),
        "platform": [candidate["architecture"], candidate["os"]],
        "user": candidate["user"],
        "entrypoint": candidate["entrypoint"],
        "cmd": candidate["cmd"],
        "version": candidate["version"],
        "baseImageId": base["imageId"],
        "candidateImageId": candidate["imageId"],
    }


def utils_delta(base_bytes, patched_bytes, proposal):
    import difflib
    if sha256(base_bytes) != SOURCE_SHA or sha256(patched_bytes) != PATCHED_SHA:
        raise SystemExit("extracted utils hash mismatch")
    original = base_bytes.decode()
    corrected = patched_bytes.decode()
    diff = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), corrected.splitlines(keepends=True),
        fromfile="a/authentik/providers/oauth2/utils.py",
        tofile="b/authentik/providers/oauth2/utils.py"))
    if diff != proposal:
        raise SystemExit("image bytes do not match the three-call proposal")
    removed = [line[1:] for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")]
    if len(removed) != 3 or any(not line.endswith(", access_token=access_token)") for line in removed):
        raise SystemExit("proposal removes more than the logging argument")
    return {"utilsDeltaMatch": True, "baseUtilsSha256": SOURCE_SHA, "patchedUtilsSha256": PATCHED_SHA}


def layer_members(archive_path):
    with tarfile.open(archive_path) as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
        if len(manifest) != 1:
            raise SystemExit("archive does not contain one image")
        layer_name = manifest[0]["Layers"][-1]
        config_name = manifest[0]["Config"]
        config_bytes = archive.extractfile(config_name).read()
        layer_bytes = archive.extractfile(layer_name).read()
        with tarfile.open(fileobj=io.BytesIO(layer_bytes), mode="r:*") as layer_tar:
            names = []
            for member in layer_tar:
                name = member.name[2:] if member.name.startswith("./") else member.name
                names.append({"path": name, "file": member.isfile(), "dir": member.isdir()})
    allowed_dirs = {"authentik", "authentik/providers", "authentik/providers/oauth2"}
    unexpected = []
    saw_utils = False
    for member in names:
        path = member["path"].rstrip("/")
        if member["dir"] and path in allowed_dirs:
            continue
        if member["file"] and path == UTILS:
            saw_utils = True
            continue
        unexpected.append(path)
    if unexpected or not saw_utils:
        raise SystemExit("candidate layer is not only the oauth utils file")
    return {
        "layerPaths": [UTILS],
        "configSha256": sha256(config_bytes),
        "configName": config_name,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-inspect", required=True)
    parser.add_argument("--candidate-inspect", required=True)
    parser.add_argument("--base-utils", required=True)
    parser.add_argument("--candidate-utils", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--expect")
    args = parser.parse_args()
    base = runtime_identity(json.loads(Path(args.base_inspect).read_text()))
    candidate = runtime_identity(json.loads(Path(args.candidate_inspect).read_text()))
    runtime = same_runtime(base, candidate)
    if not runtime["runtimeMatch"]:
        raise SystemExit("runtime identity mismatch: " + ",".join(runtime["mismatches"] or ["rootfs"]))
    proposal = Path(args.proposal).read_text()
    delta = utils_delta(Path(args.base_utils).read_bytes(), Path(args.candidate_utils).read_bytes(), proposal)
    archive = Path(args.archive).read_bytes()
    layer = layer_members(args.archive)
    if layer["configSha256"] != candidate["imageId"].removeprefix("sha256:"):
        raise SystemExit("archive config digest does not match the loaded image id")
    raw_metadata = Path(args.metadata).read_text().strip()
    metadata = json.loads(raw_metadata) if raw_metadata else {}
    buildx_digest = metadata.get("containerimage.digest") or ""
    report = {
        "baseIndexDigest": "sha256:602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5",
        "baseAmd64ManifestDigest": "sha256:6b924afb97053a27ca3f42f006c920464fb77763558c72219019365db191827b",
        "baseConfigDigest": "sha256:32dd380d0e766ff6a8100935f957b27574bd6af349832e1f0780c99f963b0204",
        "baseCompressedLayerBytes": 377501332,
        "archiveSha256": sha256(archive),
        "candidateImageId": candidate["imageId"],
        "candidateConfigSha256": layer["configSha256"],
        "buildxManifestDigest": buildx_digest or None,
        "registryDigest": None,
        "platform": runtime["platform"],
        "user": runtime["user"],
        "entrypoint": runtime["entrypoint"],
        "cmd": runtime["cmd"],
        "version": runtime["version"],
        "runtimeMatch": True,
        "rootfsPrefixMatch": True,
        "baseLayers": runtime["baseLayers"],
        "candidateLayers": runtime["candidateLayers"],
        "utilsDeltaMatch": True,
        "baseUtilsSha256": delta["baseUtilsSha256"],
        "patchedUtilsSha256": delta["patchedUtilsSha256"],
        "layerPaths": layer["layerPaths"],
        "registryPublished": False,
    }
    if args.expect:
        expected = json.loads(Path(args.expect).read_text())
        for key in ("archiveSha256", "candidateConfigSha256", "candidateImageId", "buildxManifestDigest"):
            if report[key] != expected[key]:
                raise SystemExit("built candidate does not match reviewed " + key)
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "verified": True,
        "archiveSha256": report["archiveSha256"],
        "candidateImageId": report["candidateImageId"],
        "buildxManifestDigest": report["buildxManifestDigest"],
        "registryPublished": False,
        "layerPaths": report["layerPaths"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
