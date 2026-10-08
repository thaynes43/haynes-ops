"""Manual-only, evidence-gated retention of same-title EPUB extras outside EBooks."""

import datetime
import hashlib
import json
import os
import stat
import time
import uuid

import epub_metadata as metadata

SNAPSHOT_MAX_AGE = 300
SOURCES = ("lazylibrarian", "census", "kavita", "app_wants")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise metadata.Refused("evidence/manifest has duplicate JSON keys")
        result[key] = value
    return result


def relative_path(value):
    if (not isinstance(value, str) or not value or "\\" in value
            or any(ord(c) < 32 for c in value)
            or any(part in ("", ".", "..") or part.startswith(".") for part in value.split("/"))):
        raise metadata.Refused("snapshot paths must be normalized visible library-relative paths")
    return value


def timestamp_epoch(timestamp):
    try:
        parsed = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.utcoffset() != datetime.timedelta(0):
            raise ValueError("timestamp must be UTC")
    except (AttributeError, TypeError, ValueError) as err:
        raise metadata.Refused("snapshot timestamp must be a UTC ISO timestamp") from err
    return parsed.timestamp()


def fresh(timestamp):
    age = time.time() - timestamp_epoch(timestamp)
    if not 0 <= age <= SNAPSHOT_MAX_AGE:
        raise metadata.Refused("dependency snapshot is stale or future-dated")


def require_fresh(snapshot):
    fresh(snapshot["created_at"])
    for source in SOURCES:
        fresh(snapshot[source]["checked_at"])
        fresh(snapshot[source]["capture_started_at"])
        fresh(snapshot[source]["quiescence_checked_at"])


def expiry_deadline(snapshot):
    expiry = min(timestamp_epoch(snapshot["created_at"]),
                 *(timestamp_epoch(snapshot[source][key]) for source in SOURCES
                   for key in ("checked_at", "capture_started_at", "quiescence_checked_at"))) + SNAPSHOT_MAX_AGE
    return time.monotonic() + max(0, expiry - time.time())


def load_snapshot(path, root):
    if os.path.commonpath((os.path.abspath(root), os.path.abspath(path))) == os.path.abspath(root):
        raise metadata.Refused("dependency snapshot must be outside EBOOK_ROOT")
    with metadata.safe_directory(os.path.dirname(os.path.abspath(path))) as directory:
        raw, _info = metadata.read_regular(directory, os.path.basename(path), 16 * 1024 * 1024)
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
        start, finish, established, verified = (timestamp_epoch(section.get(key)) for key in
                                                ("capture_started_at", "checked_at", "quiescence_established_at", "quiescence_checked_at"))
        library_start = timestamp_epoch(snapshot.get("library_capture_started_at"))
        if established > min(start, library_start, verified) or start > finish:
            raise metadata.Refused(f"{source} fence/capture start ordering is invalid")
        if not isinstance(section.get("quiescence_proof"), str) or not section["quiescence_proof"].strip():
            raise metadata.Refused(f"observable {source} writer quiescence proof is required")
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
            guard(recheck_keeper=False)  # no I/O between this freshness check and unlink
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


def restore_retained_copy(manifest_path, root, state, dry_run=False, deadline=float("inf")):
    """Publish one verified fresh inode without replacing any library entry."""
    metadata.validate_paths(root, state)
    backup_folder = os.path.join(state, "copies")
    if os.path.dirname(os.path.abspath(manifest_path)) != os.path.abspath(backup_folder):
        raise metadata.Refused("retained-copy manifest must be directly inside STATE_DIR/copies")
    with metadata.safe_directory(backup_folder) as backups:
        data, _manifest_info = metadata.read_regular(backups, os.path.basename(manifest_path), 65536)
        manifest = json.loads(data, object_pairs_hook=unique_object)
        if manifest.get("schema") != 1 or manifest.get("kind") != "retained_copy":
            raise metadata.Refused("unknown retained-copy manifest schema/kind")
        relative = relative_path(manifest["relative_path"])
        digest = manifest["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise metadata.Refused("retained-copy manifest checksum is invalid")
        stem = metadata.sha256(relative.encode()) + "-" + digest
        if manifest["backup_file"] != stem + ".epub" or os.path.basename(manifest_path) != stem + ".json":
            raise metadata.Refused("retained-copy manifest filenames do not match the original path/hash")
        mode, uid, gid = (manifest[key] for key in ("original_mode", "original_uid", "original_gid"))
        if (any(type(value) is not int or value < 0 for value in (mode, uid, gid)) or mode > 0o7777):
            raise metadata.Refused("retained-copy owner/mode are invalid")
        raw, info = metadata.read_regular(backups, manifest["backup_file"])
        if metadata.sha256(raw) != digest or (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid) != (mode, uid, gid):
            raise metadata.Refused("retained-copy bytes or owner/mode differ from the manifest")
        metadata.inspect_epub(raw, require_canonical=False)
        absolute = os.path.join(root, relative)
        metadata.require_unheld_library_file(absolute, root)
        folder, name = os.path.split(absolute)
        # Existing book directories are retained by move_copy. Do not recreate an
        # unknown library layout during a later restoration.
        with metadata.safe_directory(folder) as directory:
            try:
                os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise metadata.Refused("retained-copy original path must be absent")
            result = {"result": "would_restore" if dry_run else "restored", "path": relative, "sha256": digest}
            if dry_run:
                return result
            partial = ".copy-restore-" + uuid.uuid4().hex
            metadata._write_file(directory, partial, raw)
            created = os.stat(partial, dir_fd=directory, follow_symlinks=False)
            try:
                fd = os.open(partial, os.O_RDWR | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    os.fchown(fd, uid, gid)
                    os.fchmod(fd, mode)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                candidate, candidate_info = metadata.read_regular(directory, partial)
                if (metadata.sha256(candidate) != digest
                        or (stat.S_IMODE(candidate_info.st_mode), candidate_info.st_uid, candidate_info.st_gid) != (mode, uid, gid)
                        or (candidate_info.st_dev, candidate_info.st_ino) != (created.st_dev, created.st_ino)
                        or (candidate_info.st_dev, candidate_info.st_ino) == (info.st_dev, info.st_ino)):
                    raise metadata.Changed("retained-copy restoration candidate failed verification")
                metadata.inspect_epub(candidate, require_canonical=False)
                saved_hash, saved = hash_file(backups, manifest["backup_file"], deadline)
                if (saved_hash != digest or metadata._identity(saved) != metadata._identity(info)
                        or (stat.S_IMODE(saved.st_mode), saved.st_uid, saved.st_gid) != (mode, uid, gid)):
                    raise metadata.Changed("retained copy changed before restoration publication")
                metadata.require_unheld_library_file(absolute, root)
                metadata._same_directory(backups, backup_folder)
                metadata._same_directory(directory, folder)
                if time.monotonic() >= deadline:
                    raise metadata.Refused("run budget exhausted before retained-copy restoration")
                # Atomic link publication refuses an entry that raced the absence
                # check. The new inode is independent of the retained backup.
                os.link(partial, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                os.fsync(directory)
            finally:
                try:
                    cleanup_hash, cleanup_info = hash_file(directory, partial, deadline)
                    if cleanup_hash == digest and (cleanup_info.st_dev, cleanup_info.st_ino) == (created.st_dev, created.st_ino):
                        os.unlink(partial, dir_fd=directory)
                        os.fsync(directory)
                except FileNotFoundError:
                    pass
            published, published_info = metadata.read_regular(directory, name)
            if metadata.sha256(published) != digest or (stat.S_IMODE(published_info.st_mode), published_info.st_uid, published_info.st_gid) != (mode, uid, gid):
                raise metadata.Changed("published retained-copy restoration failed verification; review both copies")
            os.utime(directory)
            author_folder = os.path.dirname(folder)
            if os.path.commonpath((os.path.abspath(root), author_folder)) == os.path.abspath(root):
                with metadata.safe_directory(author_folder) as author:
                    os.utime(author)
            return result


def load_selection(path, root, evidence_hash, eligible, hashes):
    if os.path.commonpath((os.path.abspath(root), os.path.abspath(path))) == os.path.abspath(root):
        raise metadata.Refused("copy selection must be outside EBOOK_ROOT")
    with metadata.safe_directory(os.path.dirname(os.path.abspath(path))) as directory:
        raw, _info = metadata.read_regular(directory, os.path.basename(path), 1024 * 1024)
    selection = json.loads(raw, object_pairs_hook=unique_object)
    if (not isinstance(selection, dict) or type(selection.get("schema")) is not int or selection["schema"] != 1
            or selection.get("kind") != "copy_selection" or selection.get("approved_for_retention") is not True
            or selection.get("snapshot_sha256") != evidence_hash
            or not isinstance(selection.get("entries"), list) or not selection["entries"]):
        raise metadata.Refused("approved copy selection must bind the exact complete snapshot")
    chosen = []
    for entry in selection["entries"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "keeper", "keeper_sha256"}:
            raise metadata.Refused("copy selection requires exact source/keeper path and hash records")
        path, keeper = relative_path(entry["path"]), relative_path(entry["keeper"])
        if (path in chosen or eligible.get(path) != keeper or hashes.get(path) != entry["sha256"]
                or hashes.get(keeper) != entry["keeper_sha256"]):
            raise metadata.Refused("selected copy is repeated, protected, ineligible or changed")
        chosen.append(path)
    return chosen, metadata.sha256(raw)


def file_fingerprints(root, deadline):
    """Read every library file's identity without rehashing the complete corpus."""
    result = {}
    def walk_error(error):
        raise error
    for folder, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        if time.monotonic() >= deadline:
            raise metadata.Refused("source expiry reached during complete file census")
        for name in dirs:
            if os.path.islink(os.path.join(folder, name)):
                raise metadata.Refused("symlinked directory in complete file census")
        with metadata.safe_directory(folder) as directory:
            for name in names:
                if time.monotonic() >= deadline:
                    raise metadata.Refused("source expiry reached during complete file census")
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                result[os.path.relpath(os.path.join(folder, name), root)] = (
                    metadata._identity(info), info.st_mode, info.st_uid, info.st_gid)
    return result


def verify_first_retention(result, keeper, root, state, hashes, files, all_files,
                           snapshot, evidence_hash, deadline):
    """Prove the first retained copy and complete remaining census before continuing."""
    path = result["path"]
    manifest_path = result["backup_manifest"]
    backup_folder = os.path.join(state, "copies")
    if os.path.dirname(manifest_path) != backup_folder:
        raise metadata.Refused("first retention manifest is outside the exact backup folder")
    with metadata.safe_directory(backup_folder) as directory:
        raw, _info = metadata.read_regular(directory, os.path.basename(manifest_path), 65536)
        manifest = json.loads(raw, object_pairs_hook=unique_object)
        stem = metadata.sha256(path.encode()) + "-" + hashes[path]
        original = files[path]
        if (manifest.get("schema") != 1 or manifest.get("kind") != "retained_copy"
                or manifest.get("relative_path") != path or manifest.get("sha256") != hashes[path]
                or manifest.get("evidence_sha256") != evidence_hash
                or manifest.get("backup_file") != stem + ".epub"
                or (manifest.get("original_mode"), manifest.get("original_uid"), manifest.get("original_gid"))
                != (stat.S_IMODE(original.st_mode), original.st_uid, original.st_gid)):
            raise metadata.Refused("first retention manifest differs from approved source")
        digest, retained = hash_file(directory, stem + ".epub", deadline)
        if (digest != hashes[path] or retained.st_nlink != 1
                or (retained.st_dev, retained.st_ino, retained.st_size, retained.st_mode, retained.st_uid, retained.st_gid)
                != (original.st_dev, original.st_ino, original.st_size, original.st_mode, original.st_uid, original.st_gid)):
            raise metadata.Changed("first retention bytes/inode/ownership differ from approved source")
    with metadata.safe_directory(os.path.dirname(os.path.join(root, path))) as directory:
        try:
            os.stat(os.path.basename(path), dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise metadata.Changed("first retained copy still has a library entry")
    with metadata.safe_directory(os.path.dirname(os.path.join(root, keeper))) as directory:
        digest, current = hash_file(directory, os.path.basename(keeper), deadline)
        if digest != hashes[keeper] or metadata._identity(current) != metadata._identity(files[keeper]):
            raise metadata.Changed("first-stage keeper changed")
    remaining = file_fingerprints(root, deadline)
    if remaining != {name: info for name, info in all_files.items() if name != path}:
        raise metadata.Changed("unapproved library file changed after first retention")
    require_fresh(snapshot)
    if time.monotonic() >= deadline:
        raise metadata.Refused("source expiry reached during first-stage verification")
    return {"path": path, "keeper": keeper, "backup_manifest": manifest_path,
            "retained_sha256": hashes[path],
            "keeper_sha256": hashes[keeper], "unchanged_epubs": len(files) - 1,
            "unchanged_files": len(remaining),
            "evidence_sha256": evidence_hash, "protected_bytes_verified": True}


def consolidate(snapshot_path, root, state, settle_seconds, holds, log, dry_run=False, deadline=float("inf"),
                selection_path=None):
    snapshot, hashes, pointers, protected, evidence_hash = load_snapshot(snapshot_path, root)
    deadline = min(deadline, expiry_deadline(snapshot))
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
        if (not identity.get("author_refusal") and len(identity["title_keys"]) == 1 and len(identity["author_keys"]) == 1
                and not metadata.author_identity_conflicts(identity, identities)):
            key = (next(iter(identity["title_keys"])), next(iter(identity["author_keys"])))
            groups.setdefault(key, []).append(identity["path"])
        else:
            log("epub_copy_consolidate", result="review", path=identity["path"],
                detail="ambiguous title/creator identity is retained", dry_run=dry_run)
    eligible = {}
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

            eligible[path] = keeper
    if counts["refused"]:
        log("epub_copy_consolidate_census", **counts, dry_run=dry_run)
        return counts
    chosen, selection_hash = (load_selection(selection_path, root, evidence_hash, eligible, hashes)
                              if selection_path else (list(eligible), None))
    all_files = file_fingerprints(root, deadline) if selection_path else None
    if all_files is not None:
        visible_epubs = {path for path in all_files if path.lower().endswith(".epub")
                         and all(not part.startswith(".") for part in path.split("/"))}
        if visible_epubs != set(files):
            raise metadata.Changed("complete EPUB census changed before selection application")
        for path, original in files.items():
            if all_files.get(path) != (metadata._identity(original), original.st_mode, original.st_uid, original.st_gid):
                raise metadata.Changed("hashed EPUB changed before selection application")
    for index, path in enumerate(chosen):
        keeper, info = eligible[path], files[path]

        def guard(recheck_keeper=True):
            require_fresh(snapshot)
            if time.monotonic() >= deadline:
                raise metadata.Refused("run budget exhausted before copy move")
            if not recheck_keeper:
                return
            if protection_reasons(path, root, holds, protected):
                raise metadata.Refused("copy gained an ignore/hold protection before move")
            # Keep the pointer-selected copy present and byte-identical before each removal.
            with metadata.safe_directory(os.path.dirname(os.path.join(root, keeper))) as directory:
                digest, current = hash_file(directory, os.path.basename(keeper), deadline)
                metadata._same_directory(directory, os.path.dirname(os.path.join(root, keeper)))
            if digest != hashes[keeper] or metadata._identity(current) != metadata._identity(files[keeper]):
                raise metadata.Changed("LL BookFile keeper changed/disappeared after census")
            require_fresh(snapshot)
            if time.monotonic() >= deadline:
                raise metadata.Refused("snapshot/run budget exhausted during keeper recheck")

        try:
            result = move_copy(path, root, state, hashes[path], metadata._identity(info),
                               evidence_hash, guard, dry_run, deadline)
            counts[result["result"]] += 1
            log("epub_copy_consolidate", **result, keeper=keeper, dry_run=dry_run,
                phase="first" if selection_path and index == 0 else "remaining" if selection_path else "all")
            if selection_path and index == 0 and not dry_run:
                proof = verify_first_retention(result, keeper, root, state, hashes, files, all_files,
                                               snapshot, evidence_hash, deadline)
                log("epub_copy_stage_proof", result="verified", stage="first", selection_sha256=selection_hash, **proof)
                if len(chosen) > 1:
                    log("epub_copy_stage_phase", result="started", phase="remaining", approved=len(chosen) - 1,
                        selection_sha256=selection_hash)
        except Exception as err:
            counts["refused"] += 1
            log("epub_copy_consolidate", result="refused", path=path,
                detail=f"{type(err).__name__}: {err}"[:500], dry_run=dry_run)
            # An expired/racing snapshot must not authorize subsequent moves.
            break
    log("epub_copy_consolidate_census", **counts, dry_run=dry_run)
    return counts
