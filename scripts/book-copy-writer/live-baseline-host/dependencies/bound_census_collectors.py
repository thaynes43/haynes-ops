"""Read-only producers for the manual bound-census API; no filesystem mutations.

All stat integers cross the proof boundary as canonical decimal strings. Native
kernel values are never reconstructed from a JavaScript-parsed numeric proof.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import zipfile

MAX_FILES = 10000
MAX_PROOF = 32 * 1024 * 1024
DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,19})")
SHA = re.compile(r"[0-9a-f]{64}")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
BINDING_KEYS = {"namespace", "pod_name", "pod_uid", "job_uid", "node", "image", "image_id",
                "pod_spec_sha256", "restarts", "mount_root", "nfs_server", "nfs_export"}


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def decimal(value):
    if type(value) is not int or not 0 <= value <= 2**64 - 1:
        raise ValueError("native stat value is outside unsigned 64-bit range")
    return str(value)


def require_decimal(value):
    if not isinstance(value, str) or not DECIMAL.fullmatch(value) or int(value) > 2**64 - 1:
        raise ValueError("canonical decimal-string stat required")
    return int(value)


def identity6(info):
    return [decimal(n) for n in (info.st_dev, info.st_ino, info.st_size,
                               info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)]


def fingerprint(info):
    return [identity6(info), decimal(info.st_mode), decimal(info.st_uid), decimal(info.st_gid)]


def serialized(value):
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, dict):
        return {key: serialized(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialized(item) for item in value]
    return value


def guard(deadline, metadata, health):
    health()
    if time.monotonic() >= deadline:
        raise metadata.Refused("collector absolute deadline expired")


def safe_relative(path, metadata):
    if (not isinstance(path, str) or not path or len(path.encode()) > 4096 or "\\" in path
            or any(ord(c) < 32 for c in path)
            or any(part in ("", ".", "..") for part in path.split("/"))):
        raise metadata.Refused("complete census path is unsafe")
    return path


def walk(root, deadline, metadata, health):
    """Complete all-file and directory walk, including hidden/non-EPUB entries."""
    files, directories, proof_bytes = {}, {}, 0
    def failed(error):
        raise error
    for folder, dirs, names in os.walk(root, followlinks=False, onerror=failed):
        guard(deadline, metadata, health)
        with metadata.safe_directory(folder) as directory:
            relative = os.path.relpath(folder, root)
            directories[relative] = fingerprint(os.fstat(directory))
            for name in dirs:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode):
                    raise metadata.Refused("symlink or changed directory in complete census")
            for name in sorted(names):
                guard(deadline, metadata, health)
                path = safe_relative(os.path.relpath(os.path.join(folder, name), root), metadata)
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                files[path] = fingerprint(info)
                proof_bytes += len(canonical({path: files[path]}))
                if proof_bytes > MAX_PROOF // 2:
                    raise metadata.Refused("complete fingerprint proof byte cap exceeded")
                if len(files) > MAX_FILES:
                    raise metadata.Refused("complete census file cap exceeded")
            metadata._same_directory(directory, folder)
    return files, directories


def epub_paths(files):
    return sorted(path for path in files if path.lower().endswith(".epub")
                  and all(not part.startswith(".") for part in path.split("/")))


def module_hashes(metadata, copies):
    return {Path(module.__file__).name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (copies, metadata)}


def source_binding(binding, root, root_info, metadata):
    if (not isinstance(binding, dict) or set(binding) != BINDING_KEYS
            or any(not isinstance(binding[key], str) or not binding[key] for key in BINDING_KEYS - {"restarts"})
            or not UUID.fullmatch(binding["pod_uid"]) or not UUID.fullmatch(binding["job_uid"])
            or not SHA.fullmatch(binding["pod_spec_sha256"]) or type(binding["restarts"]) is not int
            or binding["restarts"] != 0 or not re.search(r"sha256:[0-9a-f]{64}$", binding["image_id"])
            or not os.path.isabs(binding["mount_root"]) or not binding["nfs_export"].startswith("/")
            or os.path.commonpath((root, binding["mount_root"])) != binding["mount_root"]):
        raise metadata.Refused("actual native source binding differs")
    return {**binding, "root_identity6": identity6(root_info)}


def read_private(path, maximum, metadata, copies):
    with metadata.safe_directory(os.path.dirname(path)) as directory:
        raw, _ = metadata.read_regular(directory, os.path.basename(path), maximum)
    return json.loads(raw, object_pairs_hook=copies.unique_object)


def await_native_binding(environ, root, deadline, metadata, copies, health):
    """Host supplies native evidence only after actual UID-owned Running proof.

    This file is an input contract, not a claim that DAPI exposes imageID/spec.
    The reviewed host verifier fixes those values from the actual Job and Pod.
    """
    path = environ.get("COPY_NATIVE_BINDING_PATH", "/tmp/native-source-binding.json")
    if path != "/tmp/native-source-binding.json":
        raise metadata.Refused("native binding path differs")
    while True:
        guard(deadline, metadata, health)
        try:
            value = read_private(path, 65536, metadata, copies)
            break
        except FileNotFoundError:
            time.sleep(.05)
    keys = {"schema", "phase_token", "observed_at", "source_binding", "module_sha256"}
    if (not isinstance(value, dict) or set(value) != keys or type(value["schema"]) is not int
            or value["schema"] != 1 or value["phase_token"] != environ.get("COPY_PHASE_TOKEN")
            or not re.fullmatch(r"[0-9a-f]{32}", value["phase_token"])
            or value["module_sha256"] != module_hashes(metadata, copies)):
        raise metadata.Refused("native source/module contract differs")
    observed = copies.timestamp_epoch(value["observed_at"])
    if not 0 <= time.time() - observed <= 15:
        raise metadata.Refused("native source observation is stale or future")
    binding = value["source_binding"]
    actual = {"namespace": "COPY_NAMESPACE", "pod_name": "COPY_POD_NAME", "pod_uid": "COPY_POD_UID",
              "job_uid": "COPY_JOB_UID", "node": "COPY_NODE"}
    if not isinstance(binding, dict) or any(not environ.get(env) or binding.get(key) != environ[env]
                                            for key, env in actual.items()):
        raise metadata.Refused("native source disagrees with actual Downward API identity")
    with metadata.safe_directory(root) as directory:
        source_binding(binding, root, os.fstat(directory), metadata)
    return binding


def read_epub(root, path, expected, deadline, metadata, health):
    """One descriptor supplies full bytes and every OPF's raw/parsed identity."""
    guard(deadline, metadata, health)
    absolute = os.path.join(root, safe_relative(path, metadata))
    with metadata.safe_directory(os.path.dirname(absolute)) as directory:
        fd = os.open(os.path.basename(absolute), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or fingerprint(before) != expected:
                raise metadata.Changed("EPUB source differs before byte capture")
            digest, size = hashlib.sha256(), 0
            while True:
                guard(deadline, metadata, health)
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
            source.seek(0)
            with zipfile.ZipFile(metadata._MetadataReader(source, before.st_size, deadline)) as archive:
                row = metadata._grouping_from_archive(archive, path)
                container = metadata._identity_xml(archive, "META-INF/container.xml")
                names = [node["attrs"].get("full-path") for node in container
                         if node["name"] == metadata.CONTAINER + "rootfile"
                         and node["attrs"].get("media-type") == "application/oebps-package+xml"]
                packages = []
                for name in names:
                    guard(deadline, metadata, health)
                    metadata._member_name(name)
                    member = archive.getinfo(name)
                    if member.file_size > metadata.MAX_XML:
                        raise metadata.Refused("complete raw OPF exceeds XML cap")
                    raw = archive.read(member)
                    if len(raw) != member.file_size:
                        raise metadata.Refused("complete raw OPF differs from declared size")
                    packages.append({"path": name, "raw_opf_sha256": hashlib.sha256(raw).hexdigest()})
            after = os.fstat(source.fileno())
            current = os.stat(os.path.basename(absolute), dir_fd=directory, follow_symlinks=False)
            metadata._same_directory(directory, os.path.dirname(absolute))
            if size != before.st_size or fingerprint(after) != expected or fingerprint(current) != expected:
                raise metadata.Changed("EPUB descriptor/path changed during byte capture")
    return {**serialized(row), "source_identity": expected[0], "sha256": digest.hexdigest(), "packages": packages}


def stat_census(root, deadline, metadata, copies, health, binding):
    started = utc()
    before, dirs = walk(root, deadline, metadata, health)
    holds = sorted(metadata.library_hold_folders(root))
    permissions, protected, permission_bytes = {}, [], 0
    for path, value in before.items():
        guard(deadline, metadata, health)
        absolute = os.path.join(root, path)
        with metadata.safe_directory(os.path.dirname(absolute)) as directory:
            info = os.stat(os.path.basename(absolute), dir_fd=directory, follow_symlinks=False)
            parent = os.fstat(directory)
            if (fingerprint(info) != value
                    or fingerprint(parent) != dirs[os.path.relpath(os.path.dirname(absolute), root)]):
                raise metadata.Changed("source path changed during stat census")
            permissions[path] = {"uid": value[2], "gid": value[3], "mode": value[1], "nlink": value[0][5],
                                 "parent": identity6(parent) + [decimal(parent.st_mode), decimal(parent.st_uid), decimal(parent.st_gid)],
                                 "parent_path": os.path.dirname(absolute), "mount_read_only": True}
            permission_bytes += len(canonical({path: permissions[path]}))
            if permission_bytes > MAX_PROOF // 2:
                raise metadata.Refused("complete permission proof byte cap exceeded")
            metadata._same_directory(directory, os.path.dirname(absolute))
        if not stat.S_ISREG(info.st_mode):
            protected.append({"path": path, "reason": "nonregular current library entry requires review"})
        elif info.st_uid != 1000 or info.st_nlink != 1 or parent.st_uid != 1000 or parent.st_mode & 0o300 != 0o300:
            protected.append({"path": path, "reason": "source owner/nlink or immediate parent ownership/access requires review"})
    after, after_dirs = walk(root, deadline, metadata, health)
    if before != after or dirs != after_dirs:
        raise metadata.Changed("complete library changed during stat census")
    ignores = sorted(path for path in before if os.path.basename(path) == ".ll_ignore")
    for path in ignores:
        if not stat.S_ISREG(require_decimal(before[path][1])):
            raise metadata.Refused("ignore marker is not a regular file")
        protected.append({"path": os.path.dirname(path) or ".", "reason": "actual .ll_ignore scope " + path})
    with metadata.safe_directory(root) as directory:
        root_info = os.fstat(directory)
    if fingerprint(root_info) != dirs["."]:
        raise metadata.Changed("root changed during stat census")
    guard(deadline, metadata, health)
    return {"schema": 2, "kind": "stat_census", "ebook_root": root, "started_at": started, "checked_at": utc(),
            "complete": True, "quiesced": False, "production_writes": 0, "all_file_fingerprints": before,
            "permissions": permissions, "configured_hold_folders": holds, "derived_ignore_markers": ignores,
            "additional_protected_paths": protected, "module_sha256": module_hashes(metadata, copies),
            "source_binding": source_binding(binding, root, root_info, metadata)}


def live_baseline(root, deadline, metadata, copies, health, binding, selected):
    """Complete LIVE byte capture plus one measured stat/selected-byte stage."""
    started = utc()
    before, dirs = walk(root, deadline, metadata, health)
    paths = epub_paths(before)
    if not paths:
        raise metadata.Refused("complete LIVE EPUB census is empty")
    files, row_bytes = [], 0
    for path in paths:
        row = read_epub(root, path, before[path], deadline, metadata, health)
        row_bytes += len(canonical(row))
        if row_bytes > MAX_PROOF // 2:
            raise metadata.Refused("complete LIVE identity proof byte cap exceeded")
        files.append(row)
    after, after_dirs = walk(root, deadline, metadata, health)
    if before != after or dirs != after_dirs:
        raise metadata.Changed("complete library changed during LIVE byte capture")
    completed = utc()  # Preserve the original full-byte completion clock.
    if (not isinstance(selected, dict) or not selected or len(selected) > MAX_FILES
            or any(path not in paths or not isinstance(sha, str) or not SHA.fullmatch(sha)
                   for path, sha in selected.items())):
        raise metadata.Refused("exact selected byte-stage scope is missing or invalid")
    stage_started, tick = utc(), time.monotonic()
    # Measure the actual complete SOURCE stat producer, including immediate
    # parent/permission guards and its second complete walk; no PG/byte reads.
    source_stage = stat_census(root, deadline, metadata, copies, health, binding)
    stats = source_stage['all_file_fingerprints']
    stat_seconds = time.monotonic() - tick
    if stats != before:
        raise metadata.Changed("library changed before measured selected stage")
    selected_started, tick = utc(), time.monotonic()
    original = {row["path"]: row for row in files}
    for path, sha in sorted(selected.items()):
        row = read_epub(root, path, before[path], deadline, metadata, health)
        if row != original[path] or row["sha256"] != sha:
            raise metadata.Changed("selected full bytes/OPF identity differ")
    selected_seconds = time.monotonic() - tick
    final, final_dirs = walk(root, deadline, metadata, health)
    if final != before or final_dirs != dirs:
        raise metadata.Changed("library changed during measured selected stage")
    with metadata.safe_directory(root) as directory:
        root_info = os.fstat(directory)
    if fingerprint(root_info) != dirs["."]:
        raise metadata.Changed("root changed during LIVE capture")
    guard(deadline, metadata, health)
    return {"schema": 1, "kind": "live_byte_baseline", "ebook_root": root,
            "capture_started_at": started, "completed_at": completed, "complete": True,
            "stable_before_after": True, "read_only": True, "production_writes": 0,
            "module_sha256": module_hashes(metadata, copies), "all_file_fingerprints": before, "files": files,
            "source_binding": source_binding(binding, root, root_info, metadata),
            "read_only_stage_measurement": {"started_at": stage_started, "selected_started_at": selected_started,
                "completed_at": utc(), "stat_seconds": stat_seconds, "selected_byte_seconds": selected_seconds,
                "stat_scope": "actual complete stat_census: two walks plus every immediate parent/permission check",
                "all_file_count": len(before), "epub_count": len(files), "selected_distinct_count": len(selected),
                "selected_bytes": str(sum(require_decimal(before[path][0][2]) for path in selected)),
                "production_writes": 0, "qualification": "One sequential read-only observation; no availability guarantee."}}
