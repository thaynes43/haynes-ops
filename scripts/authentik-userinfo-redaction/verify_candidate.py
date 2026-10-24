#!/usr/bin/env python3
"""Prove the candidate image is the redaction plus the pinned certifi overlay.

Docker inspect input contains image environment values. This prints match
booleans, digests, and layer paths only. The four reviewed digest keys stay
the acceptance gate. A mismatch still writes the computed report first.
"""
import argparse
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

import install_certifi

SOURCE_SHA = "131cfcb81374f9beccb7cad6830fbcc9917f030ea5d3e4cfb26b470db6d6ebcb"
PATCHED_SHA = "ae1494c6c145d094fd1ad473517f4c0e5fbb8fdfc7b145c651235f29f1871b97"
UTILS = "authentik/providers/oauth2/utils.py"
SITE = "ak-root/.venv/lib/python3.14/site-packages"
ACCEPTED_IMAGE = "sha256:18fc99f13617a4b83dfc470e4f8cda7ed76cf513c5147c90e1de888838325663"
EXPECT_KEYS = (
    "archiveSha256",
    "candidateConfigSha256",
    "candidateImageId",
    "buildxManifestDigest",
)
ALLOWED_FILES = {UTILS: PATCHED_SHA}
for _rel in install_certifi.OVERLAY_RELS:
    ALLOWED_FILES[SITE + "/" + _rel] = install_certifi.WHEEL_FILES[_rel]
WHITEOUTS = frozenset((
    SITE + "/.wh." + install_certifi.OLD_DIST,
    SITE + "/certifi/.wh.__pycache__",
))
ALLOWED_DIRS = set()
for _path in list(ALLOWED_FILES) + list(WHITEOUTS):
    _parts = _path.split("/")
    for _index in range(1, len(_parts)):
        ALLOWED_DIRS.add("/".join(_parts[:_index]))


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


def assert_runtime_certifi(payload):
    if payload.get("importlibMetadataVersion") != install_certifi.NEW_VERSION:
        raise SystemExit("importlib.metadata version is not 2026.10.23")
    if payload.get("moduleVersion") != install_certifi.NEW_VERSION:
        raise SystemExit("imported certifi version is not 2026.10.23")
    if payload.get("distributionVersions") != [install_certifi.NEW_VERSION]:
        raise SystemExit("certifi distribution set is not exactly 2026.10.23")
    where = payload.get("where") or ""
    if not where.endswith("/site-packages/certifi/cacert.pem"):
        raise SystemExit("certifi.where does not point at the site-packages bundle")
    return {"whereMatch": True}


def _member_name(name):
    if name.startswith("./"):
        name = name[2:]
    name = name.rstrip("/")
    if not name or name.startswith("/") or ".." in name.split("/"):
        raise SystemExit("layer path escapes the image")
    return name


def read_layer(archive_path):
    with tarfile.open(archive_path) as archive:
        manifest_file = archive.extractfile("manifest.json")
        if manifest_file is None:
            raise SystemExit("archive has no manifest")
        manifest = json.load(manifest_file)
        if len(manifest) != 1:
            raise SystemExit("archive does not contain one image")
        layer_name = manifest[0]["Layers"][-1]
        config_name = manifest[0]["Config"]
        config_bytes = archive.extractfile(config_name).read()
        layer_bytes = archive.extractfile(layer_name).read()
    members = []
    with tarfile.open(fileobj=io.BytesIO(layer_bytes), mode="r:*") as layer_tar:
        for member in layer_tar:
            name = _member_name(member.name)
            if member.issym() or member.islnk():
                kind = "symlink"
                payload = b""
            elif member.isdir():
                kind = "dir"
                payload = b""
            elif member.isfile():
                kind = "file"
                extracted = layer_tar.extractfile(member)
                payload = extracted.read() if extracted is not None else b""
                if len(payload) != member.size:
                    raise SystemExit("layer member size mismatch")
            else:
                kind = "other"
                payload = b""
            members.append({
                "path": name,
                "kind": kind,
                "mode": member.mode & 0o777,
                "uid": member.uid,
                "gid": member.gid,
                "size": member.size,
                "sha256": sha256(payload) if kind == "file" else None,
            })
    return {
        "configSha256": sha256(config_bytes),
        "configName": config_name,
        "members": members,
    }


def assert_layer(members):
    files = {}
    whiteouts = {}
    for member in members:
        path = member["path"]
        if member["kind"] == "symlink":
            raise SystemExit("layer contains a symlink")
        if member["kind"] not in ("dir", "file"):
            raise SystemExit("layer contains an unexpected member type")
        if member["uid"] != 0 or member["gid"] != 0:
            raise SystemExit("layer member owner is not root")
        if member["kind"] == "dir":
            if path not in ALLOWED_DIRS:
                raise SystemExit("layer directory is not part of the overlay")
            if member["mode"] != 0o755:
                raise SystemExit("layer directory mode is not 0755")
            continue
        base = path.rsplit("/", 1)[-1]
        if base.startswith(".wh."):
            if member["size"] != 0 or member["mode"] != 0 or member["sha256"] != sha256(b""):
                raise SystemExit("whiteout is not an empty mode-0 file")
            whiteouts[path] = member
            continue
        if path not in ALLOWED_FILES:
            raise SystemExit("layer file is not part of the overlay")
        if member["mode"] != 0o644:
            raise SystemExit("layer file mode is not 0644")
        if member["sha256"] != ALLOWED_FILES[path]:
            raise SystemExit("layer file bytes were not reviewed")
        files[path] = member
    if set(files) != set(ALLOWED_FILES):
        raise SystemExit("layer is missing an overlay file")
    if set(whiteouts) != WHITEOUTS:
        raise SystemExit("layer whiteouts are not the certifi metadata and bytecode removals")
    return {
        "layerPaths": sorted(ALLOWED_FILES),
        "layerWhiteouts": sorted(WHITEOUTS),
    }


def layer_members(archive_path):
    loaded = read_layer(archive_path)
    checked = assert_layer(loaded["members"])
    checked["configSha256"] = loaded["configSha256"]
    checked["configName"] = loaded["configName"]
    return checked


def summarize_members(members):
    rows = []
    for member in members[:200]:
        rows.append({
            "path": member["path"],
            "kind": member["kind"],
            "mode": member["mode"],
            "uid": member["uid"],
            "gid": member["gid"],
            "size": member["size"],
            "sha256": member["sha256"],
        })
    return {"count": len(members), "truncated": len(members) > 200, "members": rows}


def write_report(report, out_path):
    Path(out_path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def enforce_expect(report, expect_path, out_path):
    report = dict(report)
    report["acceptance"] = "UNACCEPTED"
    report["expectMatch"] = False
    report["signed"] = False
    write_report(report, out_path)
    if not expect_path:
        return report
    expected = json.loads(Path(expect_path).read_text())
    mismatched = [key for key in EXPECT_KEYS if report.get(key) != expected.get(key)]
    if mismatched:
        report["expectMismatches"] = mismatched
        write_report(report, out_path)
        print("built candidate does not match reviewed " + ",".join(mismatched), file=sys.stderr)
        raise SystemExit(2)
    report["acceptance"] = "REVIEWED"
    report["expectMatch"] = True
    write_report(report, out_path)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-inspect", required=True)
    parser.add_argument("--candidate-inspect", required=True)
    parser.add_argument("--base-utils", required=True)
    parser.add_argument("--candidate-utils", required=True)
    parser.add_argument("--base-cacert", required=True)
    parser.add_argument("--candidate-cacert", required=True)
    parser.add_argument("--candidate-init", required=True)
    parser.add_argument("--candidate-metadata", required=True)
    parser.add_argument("--runtime-certifi", required=True)
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
    roots = install_certifi.assert_bundle_delta(
        Path(args.base_cacert).read_bytes(),
        Path(args.candidate_cacert).read_bytes(),
    )
    versions = install_certifi.assert_distribution_versions(
        Path(args.candidate_metadata).read_bytes(),
        Path(args.candidate_init).read_bytes(),
    )
    runtime_certifi = assert_runtime_certifi(json.loads(Path(args.runtime_certifi).read_text()))
    archive = Path(args.archive).read_bytes()
    loaded = read_layer(args.archive)
    report = {
        "acceptance": "UNACCEPTED",
        "addedFingerprints": roots["addedFingerprints"],
        "archiveSha256": sha256(archive),
        "baseAmd64ManifestDigest": "sha256:6b924afb97053a27ca3f42f006c920464fb77763558c72219019365db191827b",
        "baseCompressedLayerBytes": 377501332,
        "baseConfigDigest": "sha256:32dd380d0e766ff6a8100935f957b27574bd6af349832e1f0780c99f963b0204",
        "baseIndexDigest": "sha256:602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5",
        "baseLayers": runtime["baseLayers"],
        "baseUtilsSha256": delta["baseUtilsSha256"],
        "candidateConfigSha256": loaded["configSha256"],
        "candidateImageId": candidate["imageId"],
        "candidateLayers": runtime["candidateLayers"],
        "certifiFromVersion": install_certifi.OLD_VERSION,
        "certifiToVersion": install_certifi.NEW_VERSION,
        "cmd": runtime["cmd"],
        "distributionVersions": [install_certifi.NEW_VERSION],
        "entrypoint": runtime["entrypoint"],
        "expectMatch": False,
        "importlibMetadataVersion": versions["metadataVersion"],
        "moduleVersion": versions["moduleVersion"],
        "newBundleSha256": install_certifi.NEW_BUNDLE_SHA,
        "newCertificateCount": roots["newCount"],
        "oldBundleSha256": install_certifi.OLD_BUNDLE_SHA,
        "oldCertificateCount": roots["oldCount"],
        "patchedUtilsSha256": delta["patchedUtilsSha256"],
        "platform": runtime["platform"],
        "qualified": False,
        "registryPublished": False,
        "removedFingerprints": roots["removedFingerprints"],
        "rootfsPrefixMatch": True,
        "runtimeMatch": True,
        "signature": {
            "acceptedImageDigest": ACCEPTED_IMAGE,
            "acceptedImageSignsThisCandidate": False,
            "candidateSigned": False,
        },
        "signed": False,
        "user": runtime["user"],
        "utilsDeltaMatch": True,
        "version": runtime["version"],
        "wheelSha256": install_certifi.WHEEL_SHA,
        "wheelUrl": install_certifi.WHEEL_URL,
        "whereMatch": runtime_certifi["whereMatch"],
    }
    raw_metadata = Path(args.metadata).read_text().strip()
    metadata = json.loads(raw_metadata) if raw_metadata else {}
    report["buildxManifestDigest"] = metadata.get("containerimage.digest") or None
    if loaded["configSha256"] != candidate["imageId"].removeprefix("sha256:"):
        report["proofError"] = "archive config digest does not match the loaded image id"
        write_report(report, args.out)
        raise SystemExit(report["proofError"])
    try:
        layer = assert_layer(loaded["members"])
    except SystemExit as exc:
        message = exc.code if isinstance(exc.code, str) else "layer rejected"
        report["proofError"] = message
        report["observedLayer"] = summarize_members(loaded["members"])
        write_report(report, args.out)
        print(message, file=sys.stderr)
        raise SystemExit(1) from None
    report["qualified"] = True
    report["layerPaths"] = layer["layerPaths"]
    report["layerWhiteouts"] = layer["layerWhiteouts"]
    enforce_expect(report, args.expect, args.out)
    written = json.loads(Path(args.out).read_text())
    print(json.dumps({
        "acceptance": written["acceptance"],
        "archiveSha256": written["archiveSha256"],
        "buildxManifestDigest": written["buildxManifestDigest"],
        "candidateImageId": written["candidateImageId"],
        "expectMatch": written["expectMatch"],
        "layerPaths": written["layerPaths"],
        "qualified": written["qualified"],
        "registryPublished": False,
        "signed": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
