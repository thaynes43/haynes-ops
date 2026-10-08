#!/usr/bin/env python3
"""Manual-only copy writer: one process owns its PG fence and every file action."""
import argparse
import contextlib
import json
import os
import signal
import stat
import sys
import time

import psycopg
from psycopg.rows import dict_row

import epub_copies as copies
import epub_metadata as metadata

TABLES = ("book_requests", "books_items")
HEALTH_SQL = """SELECT pg_backend_pid() AS pid, pg_is_in_recovery() AS standby,
 current_setting('transaction_read_only') AS read_only,
 ARRAY(SELECT c.relname::text FROM pg_locks l JOIN pg_class c ON c.oid=l.relation
 JOIN pg_namespace n ON n.oid=c.relnamespace WHERE l.pid=pg_backend_pid()
 AND l.locktype='relation' AND l.mode='ShareLock' AND l.granted AND n.nspname='public'
 ORDER BY c.relname) AS share_tables"""
ROWS_SQL = {table: "SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.id), '[]'::jsonb)::text AS rows "
                   "FROM public." + table + " t" for table in TABLES}


class DeadlineExpired(BaseException):
    """Unwind through finally rather than a per-copy Exception handler."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def log(message, **fields):
    print(json.dumps({"msg": message, **fields}, sort_keys=True), flush=True)


def read_private(path, root):
    absolute = os.path.abspath(path)
    if os.path.commonpath((absolute, os.path.abspath(root))) == os.path.abspath(root):
        raise metadata.Refused("private proof must be outside EBOOK_ROOT")
    with metadata.safe_directory(os.path.dirname(absolute)) as directory:
        raw, _ = metadata.read_regular(directory, os.path.basename(absolute), 32 * 1024 * 1024)
    return json.loads(raw, object_pairs_hook=copies.unique_object)


def capture_rows(db):
    return {table: db.execute(ROWS_SQL[table]).fetchone()["rows"] for table in TABLES}


def derive_app(rows):
    if not isinstance(rows, dict) or set(rows) != set(TABLES):
        raise metadata.Refused("full canonical rows of both application tables are required")
    parsed = {}
    for table in TABLES:
        if not isinstance(rows[table], str):
            raise metadata.Refused("canonical table rows must retain PostgreSQL JSONB text")
        values = json.loads(rows[table], object_pairs_hook=copies.unique_object)
        if not isinstance(values, list) or any(not isinstance(row, dict) or "id" not in row for row in values):
            raise metadata.Refused("application table rows are incomplete")
        if len({str(row["id"]) for row in values}) != len(values):
            raise metadata.Refused("application table rows repeat an id")
        parsed[table] = values
    items = [row for row in parsed["books_items"] if row.get("media_kind") is not None and row["media_kind"] != "comic"]
    # Keep every column in full_table_rows. These two omitted presentation columns
    # are the same exclusion used by the existing complete app-audit adapter.
    wants = [{key: value for key, value in row.items() if key not in ("integration_id", "shelf_item_id")}
             for row in parsed["book_requests"]]
    return {"items": items, "wants": wants}


class PrimaryShareFence:
    def __init__(self, dsn, deadline_epoch, connect=psycopg.connect):
        if not 0 < deadline_epoch - time.time() <= 300:
            raise metadata.Refused("absolute writer deadline must be within 300 seconds")
        self.deadline = time.monotonic() + deadline_epoch - time.time()
        self.dsn, self.connect, self.db, self.pid = dsn, connect, None, None

    def remaining(self):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise metadata.Refused("writer fence deadline expired")
        return left

    def __enter__(self):
        self.db = self.connect(self.dsn, autocommit=True, row_factory=dict_row,
                               connect_timeout=max(1, min(3, int(self.remaining()))),
                               application_name="issue831-manual-copy-writer")
        try:
            row = self.db.execute("SELECT pg_is_in_recovery() AS standby, "
                                  "current_setting('server_version_num')::int AS version").fetchone()
            if row["standby"] or not 160000 <= row["version"] < 170000:
                raise metadata.Refused("copy fence requires a PostgreSQL 16 primary")
            self.db.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            self.db.execute("SET LOCAL statement_timeout='2s'")
            self.db.execute("SET LOCAL idle_in_transaction_session_timeout='300s'")
            # No SELECT establishes the RR snapshot until both locks are held.
            self.db.execute("LOCK TABLE public.book_requests, public.books_items IN SHARE MODE NOWAIT")
            self.health()
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def health(self):
        self.remaining()
        try:
            row = self.db.execute(HEALTH_SQL).fetchone()
        except psycopg.Error as err:
            raise metadata.Refused("writer database connection/query failed") from err
        if (row["standby"] or row["read_only"] != "on" or row["share_tables"] != list(TABLES)
                or (self.pid is not None and row["pid"] != self.pid)):
            raise metadata.Refused("writer backend or complete SHARE fence changed")
        self.pid = row["pid"]
        self.remaining()
        return row

    def compare_capture(self, capture, source_sha256):
        self.health()
        if (capture.get("scope") != "full" or capture.get("read_only") != "on"
                or type(capture.get("production_writes")) is not int or capture["production_writes"] != 0
                or metadata.sha256(canonical(capture)) != source_sha256):
            raise metadata.Refused("application capture does not match the complete dependency snapshot")
        copies.fresh(capture.get("capture_started_at"))
        copies.fresh(capture.get("completed_at"))
        if copies.timestamp_epoch(capture["completed_at"]) < copies.timestamp_epoch(capture["capture_started_at"]):
            raise metadata.Refused("application capture completion precedes start")
        rows = capture.get("full_table_rows")
        if derive_app(rows) != capture.get("app"):
            raise metadata.Refused("application dependency arrays differ from their complete full-table rows")
        if capture_rows(self.db) != rows:
            raise metadata.Changed("complete application rows changed before the writer acquired its locks")
        self.health()

    def __exit__(self, *_):
        if self.db is not None:
            try:
                self.db.execute("ROLLBACK")
            except Exception:
                pass
            finally:
                self.db.close()
                self.db = None


class ScopedFileActions:
    """Wrap only copies.os and its manifest writer, never the shared os module."""
    def __init__(self, fence, root, state):
        self.fence, self.root, self.state = fence, os.path.abspath(root), os.path.abspath(state)
        self.active = None
        self.original_os, self.original_move = copies.os, copies.move_copy
        self.original_verify, self.original_write = copies.verify_first_retention, metadata._write_file

    def __getattr__(self, name):
        return getattr(self.original_os, name)

    def immutable(self, info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode, info.st_uid, info.st_gid)

    def check_source(self, directory, name, nlink, expected):
        row = self.active
        if row is None or name != row["name"]:
            raise metadata.Refused("copy syscall is outside the exact active source")
        metadata._same_directory(directory, row["folder"])
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != nlink
                or metadata._identity(info) != expected or self.immutable(info) != row["immutable"]):
            raise metadata.Changed("source changed during database health check; original name remains")

    def link(self, source_name, backup_name, *, src_dir_fd, dst_dir_fd, follow_symlinks):
        row = self.active
        if row is None or backup_name != row["stem"] + ".epub" or follow_symlinks is not False:
            raise metadata.Refused("unapproved retained-link syscall")
        self.fence.health()
        metadata._same_directory(dst_dir_fd, os.path.join(self.state, "copies"))
        self.check_source(src_dir_fd, source_name, 1, row["initial_identity"])
        return self.original_os.link(source_name, backup_name, src_dir_fd=src_dir_fd,
                                     dst_dir_fd=dst_dir_fd, follow_symlinks=False)

    def unlink(self, name, *, dir_fd):
        row = self.active
        if row is None or row.get("linked_identity") is None:
            raise metadata.Refused("unapproved original-name removal")
        self.fence.health()
        self.check_source(dir_fd, name, 2, row["linked_identity"])
        # No network/query/keeper I/O after this last local identity check.
        return self.original_os.unlink(name, dir_fd=dir_fd)

    def write_manifest(self, directory, name, raw, mode=0o600):
        row = self.active
        if row is None or name != row["stem"] + ".json":
            raise metadata.Refused("unapproved copy manifest publication")
        self.fence.health()
        metadata._same_directory(directory, os.path.join(self.state, "copies"))
        return self.original_write(directory, name, raw, mode)

    def move(self, path, root, state, expected_hash, expected_identity, evidence_hash,
             guard, dry_run=False, deadline=float("inf")):
        if root != self.root or state != self.state or self.active is not None:
            raise metadata.Refused("copy scope/reentrant movement changed")
        self.fence.health()
        absolute = os.path.join(root, copies.relative_path(path))
        folder, name = os.path.split(absolute)
        with metadata.safe_directory(folder) as directory:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        self.active = {"folder": folder, "name": name, "initial_identity": expected_identity,
                       "immutable": self.immutable(info), "linked_identity": None,
                       "stem": metadata.sha256(path.encode()) + "-" + expected_hash}
        def guarded(recheck_keeper=True):
            if recheck_keeper:
                self.fence.health()
            else:
                self.fence.remaining()
            guard(recheck_keeper=recheck_keeper)
            if recheck_keeper:
                with metadata.safe_directory(folder) as directory:
                    current = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if current.st_nlink == 2:
                    self.active["linked_identity"] = metadata._identity(current)
        try:
            return self.original_move(path, root, state, expected_hash, expected_identity, evidence_hash,
                                      guarded, dry_run, min(deadline, self.fence.deadline))
        finally:
            self.active = None

    def verify(self, *args, **kwargs):
        self.fence.health()
        proof = self.original_verify(*args, **kwargs)
        self.fence.health()
        return proof

    def __enter__(self):
        copies.os, copies.move_copy, copies.verify_first_retention = self, self.move, self.verify
        metadata._write_file = self.write_manifest
        return self

    def __exit__(self, *_):
        copies.os, copies.move_copy, copies.verify_first_retention = self.original_os, self.original_move, self.original_verify
        metadata._write_file = self.original_write


@contextlib.contextmanager
def converter_lock(state):
    with metadata.safe_directory(state, create=True) as directory:
        try:
            os.mkdir("lock", dir_fd=directory)
        except FileExistsError as err:
            raise metadata.Refused("existing converter lock requires review; no stale takeover") from err
        try:
            yield
        finally:
            os.rmdir("lock", dir_fd=directory)


def run(args, environ=os.environ):
    root, state = os.path.abspath(environ["EBOOK_ROOT"]), os.path.abspath(environ["STATE_DIR"])
    if environ.get("STRIP_SERIES_METADATA") != "0" or any(k in environ for k in ("STRIP_ONLY", "STRIP_FOLDERS_JSON")):
        raise metadata.Refused("manual copy writer requires metadata/conversion modes off")
    metadata.validate_paths(root, state)
    holds = metadata.library_hold_folders(root)
    if "Daniel Silva/Ransom" not in json.loads(environ.get("LIBRARY_HOLD_FOLDERS_JSON", "[]")):
        raise metadata.Refused("manual copy writer requires the existing Ransom hold")
    snapshot, *_ = copies.load_snapshot(args.snapshot, root)
    capture = read_private(args.app_capture, root)
    source_sha256 = snapshot["app_wants"].get("source_sha256")
    if not isinstance(source_sha256, str) or len(source_sha256) != 64:
        raise metadata.Refused("snapshot must bind its complete app capture SHA-256")
    deadline = min(args.deadline_epoch, time.time() + max(0, copies.expiry_deadline(snapshot) - time.monotonic()))
    def terminated(signum, _frame):
        raise DeadlineExpired("signal/deadline stopped the owning copy process")
    previous = {s: signal.signal(s, terminated) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM)}
    try:
        signal.setitimer(signal.ITIMER_REAL, max(0.001, deadline - time.time()))
        with PrimaryShareFence(environ["DATABASE_URL"], deadline) as fence:
            fence.compare_capture(capture, source_sha256)
            log("epub_copy_writer_fence", backend_pid=fence.pid, share_tables=list(TABLES), read_only="on")
            dry_run = environ.get("DRY_RUN", "1") != "0"
            with contextlib.nullcontext() if dry_run else converter_lock(state), ScopedFileActions(fence, root, state):
                counts = copies.consolidate(args.snapshot, root, state, 900, holds, log,
                                            dry_run, fence.deadline,
                                            selection_path=args.selection)
            fence.health()
            return 1 if counts["refused"] else 0
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot")
    parser.add_argument("--selection")
    parser.add_argument("--app-capture")
    parser.add_argument("--wait-proofs", action="store_true")
    parser.add_argument("--deadline-epoch", required=True, type=float)
    args = parser.parse_args()
    try:
        if args.wait_proofs:
            if any((args.snapshot, args.selection, args.app_capture)) or not 0 < args.deadline_epoch - time.time() <= 300:
                raise metadata.Refused("proof wait requires its own bounded absolute deadline and no proof paths")
            from proof_transport import wait_ready
            def timed_out(_signum, _frame):
                raise DeadlineExpired("proof delivery deadline expired")
            previous = {s: signal.signal(s, timed_out) for s in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT)}
            try:
                signal.setitimer(signal.ITIMER_REAL, args.deadline_epoch - time.time())
                paths = wait_ready("/tmp/copy-proofs", os.environ, args.deadline_epoch)
                args.snapshot, args.selection, args.app_capture = (paths[name] for name in
                                                                 ("snapshot.json", "selection.json", "app-capture.json"))
                return run(args)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
        if not all((args.snapshot, args.selection, args.app_capture)):
            raise metadata.Refused("three exact proof paths or --wait-proofs are required")
        return run(args)
    except (Exception, DeadlineExpired) as error:
        # Do not log DSN, raw driver errors, application rows or credentials.
        log("epub_copy_writer_refused", error_class=type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
