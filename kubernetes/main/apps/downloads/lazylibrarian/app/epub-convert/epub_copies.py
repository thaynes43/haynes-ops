"""Manual-only, evidence-gated retention of same-title EPUB extras outside EBooks."""

import datetime
import hashlib
import json
import os
import stat
import time

import epub_metadata as metadata

SNAPSHOT_MAX_AGE = 300
SOURCES = ("lazylibrarian", "census", "kavita", "app_wants")


def relative_path(value):
    if (not isinstance(value, str) or not value or "\\" in value
            or any(ord(c) < 32 for c in value)
            or any(part in ("", ".", "..") or part.startswith(".") for part in value.split("/"))):
        raise metadata.Refused("snapshot paths must be normalized visible library-relative paths")
    return value


def fresh(timestamp):
    try:
        parsed = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.utcoffset() != datetime.timedelta(0):
            raise ValueError("timestamp must be UTC")
        age = time.time() - parsed.timestamp()
    except (AttributeError, TypeError, ValueError) as err:
        raise metadata.Refused("snapshot timestamp must be a UTC ISO timestamp") from err
    if not 0 <= age <= SNAPSHOT_MAX_AGE:
        raise metadata.Refused("dependency snapshot is stale or future-dated")


def require_fresh(snapshot):
    fresh(snapshot["created_at"])
    for source in SOURCES:
        fresh(snapshot[source]["checked_at"])


def load_snapshot(path, root):
    if os.path.commonpath((os.path.abspath(root), os.path.abspath(path))) == os.path.abspath(root):
        raise metadata.Refused("dependency snapshot must be outside EBOOK_ROOT")
    with metadata.safe_directory(os.path.dirname(os.path.abspath(path))) as directory:
        raw, _info = metadata.read_regular(directory, os.path.basename(path), 16 * 1024 * 1024)
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise metadata.Refused("dependency snapshot has duplicate JSON keys")
            result[key] = value
        return result

    snapshot = json.loads(raw, object_pairs_hook=unique_object)
    if (not isinstance(snapshot, dict) or snapshot.get("schema") != 1
            or snapshot.get("ebook_root") != os.path.abspath(root)):
        raise metadata.Refused("dependency snapshot schema/root mismatch")
    for source in SOURCES:
        section = snapshot.get(source)
        if not isinstance(section, dict) or section.get("complete") is not True:
            raise metadata.Refused(f"complete {source} dependency read is required")
        if section.get("quiesced") is not True:
            raise metadata.Refused(f"explicit {source} writer quiescence is required")
    require_fresh(snapshot)
    files = snapshot.get("files")
    if not isinstance(files, list):
        raise metadata.Refused("complete EPUB file census is required")
    hashes = {}
    for entry in files:
        path = relative_path(entry["path"])
        digest = entry["sha256"]
        if (path in hashes or not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)):
            raise metadata.Refused("duplicate census path or invalid SHA-256")
        hashes[path] = digest
    pointers = snapshot["lazylibrarian"].get("pointers")
    if not isinstance(pointers, list):
        raise metadata.Refused("complete LazyLibrarian BookFile pointer list is required")
    by_path, book_ids = {}, set()
    for entry in pointers:
        path = relative_path(entry["path"])
        book_id = entry["book_id"]
        if not isinstance(book_id, str) or not book_id.strip() or book_id in book_ids:
            raise metadata.Refused("LazyLibrarian pointers require unique nonempty book ids")
        book_ids.add(book_id)
        by_path.setdefault(path, []).append(book_id)
    protected = []
    for source in SOURCES[1:]:
        entries = snapshot[source].get("protected_paths")
        if not isinstance(entries, list):
            raise metadata.Refused(f"complete {source} path protections are required")
        for entry in entries:
            path = relative_path(entry["path"])
            reason = entry["reason"]
            if not isinstance(reason, str) or not reason.strip():
                raise metadata.Refused("path protection requires a dependency reason")
            protected.append((path, f"{source}: {reason}"))
    return snapshot, hashes, by_path, protected, metadata.sha256(raw)


def hash_file(directory, name, deadline):
    """Stream the complete census without buffering unrelated large EPUBs."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise metadata.Refused("census input must be a regular file")
        digest, size = hashlib.sha256(), 0
        while True:
            if time.monotonic() >= deadline:
                raise metadata.Refused("run budget exhausted while hashing copy census")
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        if size != before.st_size or metadata._identity(before) != metadata._identity(os.fstat(source.fileno())):
            raise metadata.Changed("EPUB changed during copy census")
    return digest.hexdigest(), before


def protection_reasons(path, root, holds, protected):
    reasons = [reason for prefix, reason in protected if path == prefix or path.startswith(prefix + "/")]
    folder = os.path.dirname(os.path.join(root, path))
    held = metadata.held_library_folder(folder, root, holds)
    if held:
        reasons.append(f"configured library hold: {held}")
    while True:
        with metadata.safe_directory(folder) as directory:
            try:
                marker = os.stat(".ll_ignore", dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(marker.st_mode):
                    raise metadata.Refused(".ll_ignore protection marker must be a regular file")
                reasons.append(f".ll_ignore protection: {os.path.relpath(folder, root)}")
            except FileNotFoundError:
                pass
        if folder == os.path.abspath(root):
            break
        folder = os.path.dirname(folder)
    return reasons


def move_copy(path, root, state, expected_hash, expected_identity, evidence_hash,
              guard, dry_run=False, deadline=float("inf")):
    """Retain the same inode at a no-overwrite backup name, then remove its old name."""
    metadata.validate_paths(root, state)
    absolute = os.path.join(root, relative_path(path))
    metadata.require_unheld_library_file(absolute, root)
    folder, name = os.path.split(absolute)
    with metadata.safe_directory(folder) as source:
        raw, info = metadata.read_regular(source, name)
        if metadata.sha256(raw) != expected_hash or metadata._identity(info) != expected_identity:
            raise metadata.Changed("copy changed since dependency census")
        metadata.inspect_epub(raw, require_canonical=False)
        guard()
        metadata._same_directory(source, folder)
        result = {"path": path, "sha256": expected_hash, "result": "would_move" if dry_run else "moved"}
        if dry_run:
            return result
        backup_folder = os.path.join(state, "copies")
        stem = metadata.sha256(path.encode()) + "-" + expected_hash
        backup_name, manifest_name = stem + ".epub", stem + ".json"
        manifest = {"schema": 1, "kind": "retained_copy", "relative_path": path,
                    "sha256": expected_hash, "backup_file": backup_name,
                    "original_mode": stat.S_IMODE(info.st_mode), "original_uid": info.st_uid,
                    "original_gid": info.st_gid, "evidence_sha256": evidence_hash,
                    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        with metadata.safe_directory(backup_folder, create=True) as backups:
            if os.fstat(backups).st_dev != info.st_dev:
                raise metadata.Refused("copy backup must be on the same filesystem")
            # Existing artifacts, including an interrupted move, always need review.
            for candidate in (backup_name, manifest_name):
                try:
                    os.stat(candidate, dir_fd=backups, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise metadata.Refused("copy backup already exists; inspect the interrupted/prior move")
            metadata._write_file(backups, manifest_name, (json.dumps(manifest, sort_keys=True) + "\n").encode())
            os.fsync(backups)
            guard()
            metadata._same_directory(source, folder)
            metadata._same_directory(backups, backup_folder)
            if metadata._identity(os.stat(name, dir_fd=source, follow_symlinks=False)) != metadata._identity(info):
                raise metadata.Changed("copy changed before backup link; original remains in place")
            # link refuses an existing destination and retains the original's inode/bytes/mode.
            os.link(name, backup_name, src_dir_fd=source, dst_dir_fd=backups, follow_symlinks=False)
            os.fsync(backups)
            saved_hash, saved = hash_file(backups, backup_name, deadline)
            current_hash, current = hash_file(source, name, deadline)
            if (saved_hash != expected_hash or current_hash != expected_hash
                    or saved.st_nlink != 2 or current.st_nlink != 2
                    or metadata._identity(saved) != metadata._identity(current)
                    or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode, info.st_uid, info.st_gid)
                    != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns,
                        current.st_mode, current.st_uid, current.st_gid)):
                raise metadata.Changed("copy changed while retaining the backup; both names remain for review")
            guard()
            metadata._same_directory(source, folder)
            metadata._same_directory(backups, backup_folder)
            if metadata._identity(os.stat(name, dir_fd=source, follow_symlinks=False)) != metadata._identity(current):
                raise metadata.Changed("copy path changed before removal; both names remain for review")
            os.unlink(name, dir_fd=source)  # destination keeps the inode and all bytes
            os.fsync(source)
            os.fsync(backups)
            result["backup_manifest"] = os.path.join(backup_folder, manifest_name)
        os.utime(source)
        author_folder = os.path.dirname(folder)
        if os.path.commonpath((os.path.abspath(root), author_folder)) == os.path.abspath(root):
            with metadata.safe_directory(author_folder) as author:
                os.utime(author)
        return result


def consolidate(snapshot_path, root, state, settle_seconds, holds, log, dry_run=False, deadline=float("inf")):
    snapshot, hashes, pointers, protected, evidence_hash = load_snapshot(snapshot_path, root)
    identities, errors = metadata.identity_preflight(root, deadline)
    if errors:
        raise metadata.Refused(f"copy identity census is incomplete: {errors}")
    if {identity["path"] for identity in identities} != set(hashes):
        raise metadata.Refused("dependency snapshot does not cover the exact current EPUB census")
    files = {}
    for identity in identities:
        path = identity["path"]
        with metadata.safe_directory(os.path.dirname(os.path.join(root, path))) as directory:
            digest, info = hash_file(directory, os.path.basename(path), deadline)
        if digest != hashes[path] or metadata._identity(info) != identity["source_identity"]:
            raise metadata.Changed(f"copy census hash/identity changed: {path}")
        files[path] = info
    require_fresh(snapshot)
    groups = {}
    counts = {"moved": 0, "would_move": 0, "retained": 0, "protected": 0, "settling": 0,
              "review_groups": 0, "refused": 0}
    for identity in identities:
        if len(identity["title_keys"]) == 1 and len(identity["creator_keys"]) == 1:
            key = (next(iter(identity["title_keys"])), next(iter(identity["creator_keys"])))
            groups.setdefault(key, []).append(identity["path"])
        else:
            log("epub_copy_consolidate", result="review", path=identity["path"],
                detail="ambiguous title/creator identity is retained", dry_run=dry_run)
    for paths in groups.values():
        if len(paths) < 2:
            continue
        keepers = [path for path in paths if path in pointers]
        if len(keepers) != 1 or len(pointers[keepers[0]]) != 1:
            counts["review_groups"] += 1
            log("epub_copy_consolidate", result="review", paths=paths,
                detail="exactly one EPUB and one LL BookFile owner are required", dry_run=dry_run)
            continue
        keeper = keepers[0]
        counts["retained"] += 1
        log("epub_copy_consolidate", result="keeper", path=keeper, book_id=pointers[keeper][0], dry_run=dry_run)
        for path in paths:
            if path == keeper:
                continue
            try:
                reasons = protection_reasons(path, root, holds, protected)
            except Exception as err:
                counts["refused"] += 1
                log("epub_copy_consolidate", result="refused", path=path,
                    detail=f"{type(err).__name__}: {err}"[:500], dry_run=dry_run)
                break
            if reasons:
                counts["protected"] += 1
                log("epub_copy_consolidate", result="protected", path=path, reasons=reasons, dry_run=dry_run)
                continue
            info = files[path]
            if time.time() - max(info.st_mtime, info.st_ctime) < settle_seconds:
                counts["settling"] += 1
                log("epub_copy_consolidate", result="settling", path=path, dry_run=dry_run)
                continue

            def guard():
                require_fresh(snapshot)
                if time.monotonic() >= deadline:
                    raise metadata.Refused("run budget exhausted before copy move")
                if protection_reasons(path, root, holds, protected):
                    raise metadata.Refused("copy gained an ignore/hold protection before move")
                # Keep the pointer-selected copy present and byte-identical before each removal.
                with metadata.safe_directory(os.path.dirname(os.path.join(root, keeper))) as directory:
                    digest, current = hash_file(directory, os.path.basename(keeper), deadline)
                    metadata._same_directory(directory, os.path.dirname(os.path.join(root, keeper)))
                if digest != hashes[keeper] or metadata._identity(current) != metadata._identity(files[keeper]):
                    raise metadata.Changed("LL BookFile keeper changed/disappeared after census")

            try:
                result = move_copy(path, root, state, hashes[path], metadata._identity(info),
                                   evidence_hash, guard, dry_run, deadline)
                counts[result["result"]] += 1
                log("epub_copy_consolidate", **result, keeper=keeper, dry_run=dry_run)
            except Exception as err:
                counts["refused"] += 1
                log("epub_copy_consolidate", result="refused", path=path,
                    detail=f"{type(err).__name__}: {err}"[:500], dry_run=dry_run)
                # An expired/racing snapshot must not authorize subsequent moves.
                break
        if counts["refused"]:
            break
    log("epub_copy_consolidate_census", **counts, dry_run=dry_run)
    return counts
