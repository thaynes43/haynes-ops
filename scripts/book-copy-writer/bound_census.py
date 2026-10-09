"""Manual-only reuse of sealed byte evidence after complete current validation.

This module is bundled only in the copy writer. It is not an hourly entrypoint.
The trusted assembly preserves the original live byte clocks and source digest.
MAIN checks all current fingerprints and rereads every selected file before a move.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import zipfile

import epub_copies as copies
import epub_metadata as metadata

SHA = re.compile(r"[0-9a-f]{64}")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,19})")
SET_FIELDS = ("authors", "projected_aliases", "current_aliases", "comparison_aliases",
              "title_keys", "creator_keys", "author_keys")
IDENTITY_FIELDS = ("path", *SET_FIELDS, "metadata", "has_series_metadata", "author",
                   "author_refusal", "title", "creator", "strip_refusal")
MAX_FILES = 10000
SOURCE_KEYS = {"namespace", "pod_name", "pod_uid", "job_uid", "node", "image", "image_id",
               "pod_spec_sha256", "restarts", "mount_root", "nfs_server", "nfs_export", "root_identity6"}


def serialized(value):
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, dict):
        return {key: serialized(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialized(item) for item in value]
    return value


def current_module_hashes():
    return {Path(module.__file__).name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (copies, metadata)}


def fingerprint(info):
    return [[str(n) for n in metadata._identity(info)], str(info.st_mode), str(info.st_uid), str(info.st_gid)]


def stat_value(value):
    if not isinstance(value, str) or not DECIMAL.fullmatch(value) or int(value) > 2**64 - 1:
        raise metadata.Refused("canonical decimal-string stat value required")
    return int(value)


def validate_fingerprints(values):
    if not isinstance(values, dict) or not values or len(values) > MAX_FILES:
        raise metadata.Refused("complete bounded file fingerprint map required")
    for path, value in values.items():
        # The complete walk includes hidden ignore markers and non-EPUB files.
        if (not isinstance(path, str) or not path or "\\" in path or any(ord(c) < 32 for c in path)
                or any(part in ("", ".", "..") for part in path.split("/"))):
            raise metadata.Refused("complete fingerprint path is unsafe")
        if (not isinstance(value, list) or len(value) != 4 or not isinstance(value[0], list)
                or len(value[0]) != 6):
            raise metadata.Refused("full device/inode/time/link/mode/owner fingerprint required")
        if any(stat_value(n) < 0 for n in (*value[0], *value[1:])) or stat_value(value[0][5]) < 1:
            raise metadata.Refused("full device/inode/time/link/mode/owner fingerprint required")


def validate_source(source, root):
    if (not isinstance(source, dict) or set(source) != SOURCE_KEYS
            or any(not isinstance(source[key], str) or not source[key] for key in SOURCE_KEYS
                   - {"restarts", "root_identity6"})
            or not UUID.fullmatch(source["pod_uid"]) or not UUID.fullmatch(source["job_uid"])
            or not SHA.fullmatch(source["pod_spec_sha256"])
            or not re.search(r"sha256:[0-9a-f]{64}$", source["image_id"])
            or type(source["restarts"]) is not int or source["restarts"] != 0
            or not isinstance(source["root_identity6"], list) or len(source["root_identity6"]) != 6
            or not os.path.isabs(source["mount_root"]) or not source["nfs_export"].startswith("/")
            or os.path.commonpath((root, source["mount_root"])) != source["mount_root"]):
        raise metadata.Refused("actual native source/node/mount/module binding required")
    for value in source["root_identity6"]:
        stat_value(value)


def baseline_expiry(bound):
    start = copies.timestamp_epoch(bound.get("byte_capture_started_at"))
    finish = copies.timestamp_epoch(bound.get("byte_completed_at"))
    if start > finish:
        raise metadata.Refused("live byte clocks are inverted")
    copies.fresh(bound["byte_capture_started_at"])
    copies.fresh(bound["byte_completed_at"])
    return time.monotonic() + max(0, start + copies.SNAPSHOT_MAX_AGE - time.time())


def validate_bound(bound, root, hashes):
    keys = {"schema", "kind", "byte_baseline_sha256", "byte_capture_started_at", "byte_completed_at",
            "byte_source_binding", "module_sha256", "current_validation", "files", "all_file_fingerprints"}
    if (not isinstance(bound, dict) or set(bound) != keys or type(bound.get("schema")) is not int
            or bound["schema"] != 1 or bound.get("kind") != "bound_census"
            or not isinstance(bound.get("byte_baseline_sha256"), str)
            or not SHA.fullmatch(bound["byte_baseline_sha256"])):
        raise metadata.Refused("sealed manual bound census required")
    baseline_expiry(bound)
    if bound["module_sha256"] != current_module_hashes():
        raise metadata.Refused("live byte evidence parser/module identity differs")
    validate_source(bound["byte_source_binding"], root)
    validation = bound["current_validation"]
    if (not isinstance(validation, dict) or set(validation) != {"capture_started_at", "checked_at", "source_binding"}
            or copies.timestamp_epoch(validation["capture_started_at"]) > copies.timestamp_epoch(validation["checked_at"])
            or copies.timestamp_epoch(bound["byte_completed_at"]) > copies.timestamp_epoch(validation["capture_started_at"])):
        raise metadata.Refused("current stat validation clocks differ from live byte clocks")
    for key in ("capture_started_at", "checked_at"):
        copies.fresh(validation[key])
    validate_source(validation["source_binding"], root)
    for key in ("node", "mount_root", "nfs_server", "nfs_export"):
        if bound["byte_source_binding"][key] != validation["source_binding"][key]:
            raise metadata.Refused("byte evidence and current SOURCE backing differ")
    if bound["byte_source_binding"]["root_identity6"][:2] != validation["source_binding"]["root_identity6"][:2]:
        raise metadata.Refused("byte evidence root device/inode differs")
    values = bound["all_file_fingerprints"]
    validate_fingerprints(values)
    visible = {path for path in values if path.lower().endswith(".epub")
               and all(not part.startswith(".") for part in path.split("/"))}
    rows, identities = bound["files"], []
    if not isinstance(rows, list) or len(rows) != len(visible) or len(rows) > MAX_FILES:
        raise metadata.Refused("complete bound EPUB identity coverage required")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise metadata.Refused("bound EPUB record is malformed")
        path = copies.relative_path(row.get("path"))
        if (path in seen or path not in visible or not isinstance(row.get("sha256"), str)
                or not SHA.fullmatch(row["sha256"]) or row["sha256"] != hashes.get(path)
                or not isinstance(row.get("source_identity"), list) or row["source_identity"] != values[path][0]
                or not stat.S_ISREG(stat_value(values[path][1]))):
            raise metadata.Refused("bound EPUB bytes/identity/path differ from complete snapshot")
        packages = row.get("packages")
        if (not isinstance(packages, list) or not packages
                or any(not isinstance(p, dict) or set(p) != {"path", "raw_opf_sha256"}
                       or not isinstance(p["path"], str) or not isinstance(p["raw_opf_sha256"], str)
                       or not SHA.fullmatch(p["raw_opf_sha256"]) for p in packages)
                or len({p["path"] for p in packages}) != len(packages)):
            raise metadata.Refused("complete original raw OPF proof required")
        for package in packages:
            metadata._member_name(package["path"])
        identity = {key: row[key] for key in IDENTITY_FIELDS if key in row}
        if any(key not in identity for key in IDENTITY_FIELDS if key != "strip_refusal"):
            raise metadata.Refused("complete role-aware identity record required")
        for key in SET_FIELDS:
            values_set = identity[key]
            if (not isinstance(values_set, list) or any(not isinstance(v, str) for v in values_set)
                    or len(values_set) != len(set(values_set))):
                raise metadata.Refused("complete identity keys are malformed")
            identity[key] = set(values_set)
        identity["source_identity"] = tuple(stat_value(v) for v in row["source_identity"])
        identities.append(identity)
        seen.add(path)
    if seen != visible or seen != set(hashes):
        raise metadata.Refused("bound census omits, adds or repeats an EPUB")
    return identities, {row["path"]: row for row in rows}


def bind_live_baseline(baseline, baseline_sha256, current, root):
    """Pure trusted-assembly boundary: compare complete current stat proof first.

    The caller verifies the sealed artifact SHA and actual native SOURCE lease.
    Neither this function nor its result assigns a new clock to old byte reads.
    """
    if (not isinstance(baseline, dict) or type(baseline.get("schema")) is not int or baseline["schema"] != 1
            or baseline.get("kind") != "live_byte_baseline" or baseline.get("ebook_root") != root
            or baseline.get("complete") is not True or baseline.get("stable_before_after") is not True
            or baseline.get("read_only") is not True or type(baseline.get("production_writes")) is not int
            or baseline["production_writes"] != 0 or not isinstance(baseline_sha256, str)
            or not SHA.fullmatch(baseline_sha256)):
        raise metadata.Refused("complete sealed live byte baseline required")
    if (not isinstance(current, dict) or type(current.get("schema")) is not int or current["schema"] != 2
            or current.get("kind") != "stat_census" or current.get("ebook_root") != root
            or current.get("complete") is not True or current.get("quiesced") is not False
            or type(current.get("production_writes")) is not int or current["production_writes"] != 0
            or current.get("errors") not in (None, [])
            or not {"started_at", "checked_at", "source_binding", "permissions", "derived_ignore_markers",
                    "additional_protected_paths", "configured_hold_folders"} <= set(current)
            or {"files", "sha256", "packages"} & set(current)):
        raise metadata.Refused("complete actual SOURCE stat census required")
    # Compare every path and every decimal-string field before borrowing hashes
    # or author/OPF identity from the old byte reads.
    validate_fingerprints(baseline.get("all_file_fingerprints"))
    validate_fingerprints(current.get("all_file_fingerprints"))
    if current["all_file_fingerprints"] != baseline["all_file_fingerprints"]:
        raise metadata.Changed("current complete SOURCE path/fingerprint set differs from sealed live bytes")
    bound = {"schema": 1, "kind": "bound_census", "byte_baseline_sha256": baseline_sha256,
             "byte_capture_started_at": baseline["capture_started_at"], "byte_completed_at": baseline["completed_at"],
             "byte_source_binding": baseline["source_binding"], "module_sha256": baseline["module_sha256"],
             "current_validation": {"capture_started_at": current["started_at"], "checked_at": current["checked_at"],
                                    "source_binding": current["source_binding"]},
             "files": baseline["files"], "all_file_fingerprints": current["all_file_fingerprints"]}
    hashes = {row["path"]: row["sha256"] for row in baseline["files"]}
    validate_bound(bound, root, hashes)
    # Keep all current source permissions, holds, ignore markers and protections.
    # Only byte/OPF/grouping rows are derived from the exactly matched baseline.
    library = {**current, "kind": "validated_byte_census", "files": baseline["files"],
               "bound_census": bound, "byte_baseline_sha256": baseline_sha256,
               "byte_capture_started_at": baseline["capture_started_at"], "byte_completed_at": baseline["completed_at"]}
    return library, bound


def stat_census(root, deadline, health):
    """One exhaustive local walk; retain real stat results and every device ID."""
    values, infos = {}, {}
    def failed(error):
        raise error
    for folder, dirs, names in os.walk(root, followlinks=False, onerror=failed):
        health()
        if time.monotonic() >= deadline:
            raise metadata.Refused("bound stat census deadline expired")
        if any(os.path.islink(os.path.join(folder, name)) for name in dirs):
            raise metadata.Refused("symlinked directory prevents complete bound census")
        with metadata.safe_directory(folder) as directory:
            for name in names:
                health()
                if time.monotonic() >= deadline or len(values) >= MAX_FILES:
                    raise metadata.Refused("bound stat census deadline/count cap exceeded")
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                path = os.path.relpath(os.path.join(folder, name), root)
                values[path], infos[path] = fingerprint(info), info
    validate_fingerprints(values)
    health()
    return values, infos


def selected_file(directory, name, relative, deadline, health):
    """Hash and parse the selected file through one stable, bounded descriptor."""
    health()
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > metadata.MAX_ARCHIVE:
            raise metadata.Refused("selected copy must be regular and singly linked")
        digest, size = hashlib.sha256(), 0
        while True:
            if time.monotonic() >= deadline:
                raise metadata.Refused("selected byte proof deadline expired")
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        source.seek(0)
        with zipfile.ZipFile(metadata._MetadataReader(source, before.st_size, deadline)) as archive:
            identity = metadata._grouping_from_archive(archive, relative)
            container = metadata._identity_xml(archive, "META-INF/container.xml")
            names = [n["attrs"].get("full-path") for n in container
                     if n["name"] == metadata.CONTAINER + "rootfile"
                     and n["attrs"].get("media-type") == "application/oebps-package+xml"]
            packages = []
            for package in names:
                metadata._member_name(package)
                if archive.getinfo(package).file_size > metadata.MAX_XML:
                    raise metadata.Refused("selected OPF proof exceeds metadata cap")
                with archive.open(package) as member:
                    raw = member.read(metadata.MAX_XML + 1)
                if len(raw) > metadata.MAX_XML:
                    raise metadata.Refused("selected OPF proof exceeds metadata cap")
                packages.append({"path": package, "raw_opf_sha256": hashlib.sha256(raw).hexdigest()})
        health()
        after = os.fstat(source.fileno())
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (size != before.st_size or fingerprint(before) != fingerprint(after)
                or fingerprint(before) != fingerprint(current)):
            raise metadata.Changed("selected descriptor/path changed during byte proof")
    return digest.hexdigest(), identity, packages, before


def prepare(snapshot, hashes, root, selection_path, evidence_hash, deadline, health):
    if not selection_path:
        raise metadata.Refused("bound census requires explicit root-reviewed selection")
    if os.path.commonpath((os.path.abspath(root), os.path.abspath(selection_path))) == os.path.abspath(root):
        raise metadata.Refused("copy selection must be outside EBOOK_ROOT")
    bound = snapshot.get("bound_census")
    identities, rows = validate_bound(bound, root, hashes)
    deadline = min(deadline, baseline_expiry(bound))
    with metadata.safe_directory(os.path.dirname(os.path.abspath(selection_path))) as directory:
        raw, _ = metadata.read_regular(directory, os.path.basename(selection_path), 1024 * 1024)
    selection = json.loads(raw, object_pairs_hook=copies.unique_object)
    if (not isinstance(selection, dict) or type(selection.get("schema")) is not int or selection["schema"] != 1
            or selection.get("kind") != "copy_selection" or selection.get("approved_for_retention") is not True
            or selection.get("bound_census_baseline_sha256") != bound["byte_baseline_sha256"]
            or selection.get("snapshot_sha256") != evidence_hash
            or not isinstance(selection.get("entries"), list) or not selection["entries"]):
        raise metadata.Refused("ordered selection must bind the exact live byte baseline")
    selected = set()
    chosen = set()
    for entry in selection["entries"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "keeper", "keeper_sha256"}:
            raise metadata.Refused("bound selection record is malformed")
        if entry["path"] in chosen or entry["path"] == entry["keeper"]:
            raise metadata.Refused("bound selection repeats a source or selects its own keeper")
        chosen.add(entry["path"])
        for key, hash_key in (("path", "sha256"), ("keeper", "keeper_sha256")):
            path = copies.relative_path(entry[key])
            if hashes.get(path) != entry[hash_key]:
                raise metadata.Refused("selected live byte hash differs")
            selected.add(path)
    health()
    root_info = os.stat(root, follow_symlinks=False)
    if (not stat.S_ISDIR(root_info.st_mode) or fingerprint(root_info)[0][:2]
            != bound["current_validation"]["source_binding"]["root_identity6"][:2]):
        raise metadata.Changed("MAIN root device/inode differs from current SOURCE")
    before, infos = stat_census(root, deadline, health)
    if before != bound["all_file_fingerprints"]:
        raise metadata.Changed("complete current path/fingerprint set differs from live evidence")
    for path in sorted(selected):
        health()
        with metadata.safe_directory(os.path.dirname(os.path.join(root, path))) as directory:
            digest, identity, packages, info = selected_file(directory, os.path.basename(path), path, deadline, health)
        if (digest != hashes[path] or fingerprint(info) != before[path]
                or packages != rows[path]["packages"]
                or any(serialized(identity.get(key)) != serialized(rows[path].get(key)) for key in IDENTITY_FIELDS)):
            raise metadata.Changed("selected whole bytes, OPF or complete identity differ")
    after, _ = stat_census(root, deadline, health)
    if after != before:
        raise metadata.Changed("complete library changed while selected cohort was verified")
    copies.require_fresh(snapshot)
    baseline_expiry(bound)
    health()
    return identities, {path: infos[path] for path in hashes}, deadline
