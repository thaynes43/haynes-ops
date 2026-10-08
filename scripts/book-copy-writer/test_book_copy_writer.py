#!/usr/bin/env python3
"""One finite PG16 fixture and temporary library; sequential nice19, never load."""
import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
MODULES = Path(os.environ.get("COPY_TEST_MODULES", HERE.parents[1] / "kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert"))
sys.path[:0] = [str(HERE), str(MODULES), "/copy-writer"]
import book_copy_writer as writer
import epub_copies as copies
import epub_metadata as metadata
from test_epub_metadata import fixture, read, write


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class PG16:
    def __enter__(self):
        self.tmp, self.server = None, None
        external = os.environ.get("COPY_TEST_DSN")
        if external:
            # The only supported external test fixture is loopback in a dedicated
            # CI container. Never accept a production hostname or inherited DSN.
            info = writer.psycopg.conninfo.conninfo_to_dict(external)
            if info.get("host") != "127.0.0.1" or info.get("dbname") != "copy_writer_fixture":
                raise RuntimeError("test DSN must name the isolated loopback copy_writer_fixture")
            self.dsn = external
        else:
            bins = os.environ.get("COPY_TEST_PG_BIN")
            if not bins:
                raise RuntimeError("COPY_TEST_PG_BIN must identify existing embedded PG16 binaries")
            self.tmp = tempfile.TemporaryDirectory(prefix="hn-copy-writer-pg16-")
            data = self.tmp.name + "/data"
            subprocess.run([bins + "/initdb", "-D", data, "-A", "trust", "-U", "postgres", "--no-locale", "--encoding=UTF8"],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
            self.server = subprocess.Popen([bins + "/postgres", "-D", data, "-h", "127.0.0.1", "-p", str(port),
                                           "-k", self.tmp.name, "-c", "shared_buffers=16MB", "-c", "max_connections=8",
                                           "-c", "max_worker_processes=0", "-c", "autovacuum=off", "-c", "jit=off"],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.dsn = f"host=127.0.0.1 port={port} user=postgres dbname=postgres"
            deadline = time.monotonic() + 5
            while True:
                try:
                    with writer.psycopg.connect(self.dsn, connect_timeout=1, autocommit=True) as db:
                        db.execute("CREATE DATABASE copy_writer_fixture")
                    break
                except writer.psycopg.OperationalError:
                    if time.monotonic() >= deadline or self.server.poll() is not None:
                        raise
                    time.sleep(.03)
            self.dsn = self.dsn.replace("dbname=postgres", "dbname=copy_writer_fixture")
        self.admin = writer.psycopg.connect(self.dsn, autocommit=True)
        version = int(self.admin.execute("SHOW server_version_num").fetchone()[0])
        assert 160000 <= version < 170000
        self.admin.execute("CREATE TABLE books_items(id integer PRIMARY KEY, source text, external_id text, media_kind text, saved text)")
        self.admin.execute("CREATE TABLE book_requests(id integer PRIMARY KEY, ll_book_id text, matched_books_item_id integer, integration_id integer, shelf_item_id integer)")
        self.admin.execute("INSERT INTO books_items VALUES(1,'kavita','s1','ebook','complete'),(2,'kavita','s2','comic','also bound')")
        self.admin.execute("INSERT INTO book_requests VALUES(1,'ll-book-1',1,21,32)")
        return self

    def __exit__(self, *_):
        if getattr(self, "admin", None):
            self.admin.close()
        if self.server:
            self.server.terminate()
            try:
                self.server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server.kill(); self.server.wait(timeout=2)
        if self.tmp:
            self.tmp.cleanup()


class WriterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = PG16()
        try:
            cls.pg.__enter__()
        except BaseException:
            cls.pg.__exit__()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.pg.__exit__()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root, self.state = self.tmp.name + "/EBooks", self.tmp.name + "/.epub-convert"
        self.keeper, self.extra = "Suzanne Collins/Mockingjay/primary.epub", "Suzanne Collins/Extra/copy.epub"
        self.raw, self.lines = fixture(), []
        for path in (self.keeper, self.extra):
            write(os.path.join(self.root, path), self.raw)

    def fence(self, ttl=10):
        return writer.PrimaryShareFence(self.pg.dsn, time.time() + ttl)

    def capture(self, fence):
        rows = writer.capture_rows(fence.db)
        return {"scope": "full", "read_only": "on", "production_writes": 0,
                "capture_started_at": stamp(), "completed_at": stamp(),
                "full_table_rows": rows, "app": writer.derive_app(rows)}

    def snapshot(self, capture):
        now = stamp()
        source = {"complete": True, "quiesced": True, "checked_at": now, "capture_started_at": now,
                  "quiescence_established_at": now, "quiescence_checked_at": now, "quiescence_proof": "fixture fence"}
        return {"schema": 1, "created_at": now, "library_capture_started_at": now, "ebook_root": self.root,
                "files": [{"path": path, "sha256": metadata.sha256(self.raw)} for path in (self.keeper, self.extra)],
                "lazylibrarian": {**source, "pointers": [{"book_id": "ll-book-1", "path": self.keeper}]},
                **{key: {**source, "protected_paths": []} for key in copies.SOURCES[1:]},
                "app_wants": {**source, "protected_paths": [], "source_sha256": metadata.sha256(writer.canonical(capture))}}

    def selection(self, capture):
        data = self.snapshot(capture)
        snapshot = self.tmp.name + "/snapshot.json"
        write(snapshot, json.dumps(data).encode())
        selection = self.tmp.name + "/selection.json"
        write(selection, json.dumps({"schema": 1, "kind": "copy_selection", "approved_for_retention": True,
                                    "snapshot_sha256": metadata.sha256(read(snapshot)),
                                    "entries": [{"path": self.extra, "sha256": metadata.sha256(self.raw),
                                                 "keeper": self.keeper, "keeper_sha256": metadata.sha256(self.raw)}]}).encode())
        return snapshot, selection

    def blocked(self, table):
        with writer.psycopg.connect(self.pg.dsn, autocommit=True) as db:
            db.execute("SET lock_timeout='100ms'")
            with self.assertRaises(writer.psycopg.errors.LockNotAvailable):
                db.execute(f"INSERT INTO {table}(id) VALUES(99)")

    def test_real_driver_binary_and_primary_readonly_share_allow_reads_block_both_writers(self):
        self.assertEqual(writer.psycopg.__version__, "3.3.6")
        self.assertEqual(writer.psycopg.pq.__impl__, "binary")
        with self.fence() as fence:
            self.assertEqual(fence.health()["share_tables"], list(writer.TABLES))
            for table in writer.TABLES:
                self.assertGreater(self.pg.admin.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
                self.blocked(table)
        for table in writer.TABLES:
            self.pg.admin.execute(f"INSERT INTO {table}(id) VALUES(99)")
            self.pg.admin.execute(f"DELETE FROM {table} WHERE id=99")

    def test_supervisor_eof_does_not_release_writer_owned_locks(self):
        with self.fence() as source, self.fence() as owning_writer:
            source_pid = source.pid
            source.db.close()  # source supervisor socket EOF, distinct backend
            self.assertNotEqual(source_pid, owning_writer.pid)
            owning_writer.health()
            for table in writer.TABLES:
                self.blocked(table)
        self.pg.admin.execute("INSERT INTO book_requests(id) VALUES(99)")
        self.pg.admin.execute("DELETE FROM book_requests WHERE id=99")

    def test_complete_rows_include_comics_all_columns_and_refuse_changed_capture(self):
        with self.fence() as fence:
            capture = self.capture(fence)
            self.assertIn("also bound", capture["full_table_rows"]["books_items"])
            self.assertIn("shelf_item_id", capture["full_table_rows"]["book_requests"])
            fence.compare_capture(capture, metadata.sha256(writer.canonical(capture)))
            corrupt = json.loads(json.dumps(capture)); corrupt["full_table_rows"]["books_items"] = "[]"
            with self.assertRaises(metadata.Refused):
                fence.compare_capture(corrupt, metadata.sha256(writer.canonical(corrupt)))
        self.pg.admin.execute("UPDATE books_items SET saved='changed' WHERE id=1")
        try:
            with self.fence() as fence, self.assertRaises(metadata.Changed):
                fence.compare_capture(capture, metadata.sha256(writer.canonical(capture)))
        finally:
            self.pg.admin.execute("UPDATE books_items SET saved='complete' WHERE id=1")

    def test_unknown_share_table_or_expired_deadline_refuses(self):
        self.pg.admin.execute("CREATE TABLE unexpected(id integer)")
        try:
            with self.fence() as fence:
                fence.db.execute("LOCK TABLE unexpected IN SHARE MODE")
                with self.assertRaises(metadata.Refused):
                    fence.health()
        finally:
            self.pg.admin.execute("DROP TABLE unexpected")
        for value in (time.time() - 1, time.time() + 301, float("nan")):
            with self.assertRaises(metadata.Refused):
                writer.PrimaryShareFence(self.pg.dsn, value)
        with self.fence() as fence:
            fence.deadline = time.monotonic() - .001
            with self.assertRaises(metadata.Refused):
                fence.health()

    def test_active_database_writer_refuses_before_any_library_action(self):
        self.pg.admin.execute("BEGIN")
        self.pg.admin.execute("UPDATE book_requests SET ll_book_id=ll_book_id WHERE id=1")
        try:
            with self.assertRaises(writer.psycopg.errors.LockNotAvailable):
                with self.fence():
                    self.fail("a concurrent writer must prevent fence acquisition")
        finally:
            self.pg.admin.execute("ROLLBACK")
        self.assertEqual(read(os.path.join(self.root, self.extra)), self.raw)
        self.assertFalse(os.path.exists(self.state))

    def test_unknown_stale_or_unbound_capture_refuses_before_consolidation(self):
        with self.fence() as fence:
            good = self.capture(fence)
            stale = json.loads(json.dumps(good)); stale["capture_started_at"] = "2000-01-01T00:00:00Z"
            unknown = json.loads(json.dumps(good)); unknown["production_writes"] = False
            missing = json.loads(json.dumps(good)); missing["full_table_rows"].pop("book_requests")
            for row, digest in ((good, "0" * 64), (stale, metadata.sha256(writer.canonical(stale))),
                                (unknown, metadata.sha256(writer.canonical(unknown))),
                                (missing, metadata.sha256(writer.canonical(missing)))):
                with self.assertRaises(metadata.Refused):
                    fence.compare_capture(row, digest)
        self.assertFalse(os.path.exists(self.state))

    def test_actual_backend_kill_refuses_move_before_any_backup_or_removal(self):
        with self.fence() as fence, writer.ScopedFileActions(fence, self.root, self.state):
            self.pg.admin.execute("SELECT pg_terminate_backend(%s)", (fence.pid,))
            with metadata.safe_directory(os.path.dirname(os.path.join(self.root, self.extra))) as directory:
                info = os.stat("copy.epub", dir_fd=directory)
            with self.assertRaises(metadata.Refused):
                copies.move_copy(self.extra, self.root, self.state, metadata.sha256(self.raw),
                                 metadata._identity(info), "0" * 64, lambda **_: None)
        self.assertEqual(read(os.path.join(self.root, self.extra)), self.raw)
        self.assertFalse(os.path.exists(self.state))

    def test_success_uses_original_ordered_move_and_first_proof_without_global_os_patch(self):
        with self.fence() as fence:
            capture = self.capture(fence)
            evidence, selection = self.selection(capture)
            fence.compare_capture(capture, self.snapshot(capture)["app_wants"]["source_sha256"])
            original_os = copies.os
            with writer.ScopedFileActions(fence, self.root, self.state) as scope:
                self.assertIs(copies.os, scope)
                self.assertIs(metadata.os, os)
                with tempfile.TemporaryFile() as unrelated:
                    unrelated.write(b"unrelated IO remains normal")
                result = copies.consolidate(evidence, self.root, self.state, 0, frozenset(),
                                            lambda msg, **fields: self.lines.append({"msg": msg, **fields}),
                                            deadline=fence.deadline, selection_path=selection)
            self.assertIs(copies.os, original_os)
        self.assertEqual((result["moved"], result["refused"]), (1, 0))
        self.assertEqual(read(os.path.join(self.root, self.keeper)), self.raw)
        self.assertFalse(os.path.exists(os.path.join(self.root, self.extra)))
        proof = next(row for row in self.lines if row["msg"] == "epub_copy_stage_proof")
        self.assertTrue(proof["protected_bytes_verified"])
        manifest = json.loads(read(proof["backup_manifest"]))
        self.assertEqual(read(os.path.join(self.state, "copies", manifest["backup_file"])), self.raw)

    def test_final_unlink_rechecks_identity_after_health_network_gap(self):
        folder = os.path.dirname(os.path.join(self.root, self.extra))
        os.makedirs(self.state + "/copies")
        os.link(os.path.join(self.root, self.extra), self.state + "/copies/retained.epub")
        with self.fence() as fence, metadata.safe_directory(folder) as directory:
            before = os.stat("copy.epub", dir_fd=directory)
            scope = writer.ScopedFileActions(fence, self.root, self.state)
            scope.active = {"folder": folder, "name": "copy.epub", "immutable": scope.immutable(before),
                            "linked_identity": metadata._identity(before)}
            health = fence.health
            def racing_health():
                health()
                os.utime(os.path.join(folder, "copy.epub"), ns=(before.st_atime_ns, before.st_mtime_ns + 10))
            with mock.patch.object(fence, "health", side_effect=racing_health), self.assertRaises(metadata.Changed):
                scope.unlink("copy.epub", dir_fd=directory)
        self.assertTrue(os.path.exists(os.path.join(folder, "copy.epub")))
        self.assertTrue(os.path.exists(self.state + "/copies/retained.epub"))

    def test_backend_loss_before_first_proof_stops_all_remaining_selected_moves(self):
        second = "Suzanne Collins/Second/copy.epub"
        write(os.path.join(self.root, second), self.raw)
        with self.fence() as fence:
            capture = self.capture(fence)
            evidence, selection = self.selection(capture)
            data = json.loads(read(evidence)); data["files"].append({"path": second, "sha256": metadata.sha256(self.raw)})
            write(evidence, json.dumps(data).encode())
            chosen = json.loads(read(selection)); chosen["snapshot_sha256"] = metadata.sha256(read(evidence))
            chosen["entries"].append({**chosen["entries"][0], "path": second})
            write(selection, json.dumps(chosen).encode())
            def after_first(msg, **fields):
                self.lines.append({"msg": msg, **fields})
                if fields.get("result") == "moved":
                    self.pg.admin.execute("SELECT pg_terminate_backend(%s)", (fence.pid,))
            with writer.ScopedFileActions(fence, self.root, self.state):
                result = copies.consolidate(evidence, self.root, self.state, 0, frozenset(), after_first,
                                            deadline=fence.deadline, selection_path=selection)
        self.assertEqual((result["moved"], result["refused"]), (1, 1))
        self.assertEqual(read(os.path.join(self.root, second)), self.raw)
        self.assertEqual(read(os.path.join(self.root, self.keeper)), self.raw)
        self.assertFalse(any(row["msg"] == "epub_copy_stage_phase" for row in self.lines))

    def test_lost_connection_blocks_exact_unlink_publication_and_first_proof(self):
        folder = os.path.dirname(os.path.join(self.root, self.extra))
        os.makedirs(self.state + "/copies")
        os.link(os.path.join(self.root, self.extra), self.state + "/copies/retained.epub")
        with self.fence() as fence, metadata.safe_directory(folder) as source, metadata.safe_directory(self.state + "/copies") as backup:
            info = os.stat("copy.epub", dir_fd=source)
            scope = writer.ScopedFileActions(fence, self.root, self.state)
            scope.active = {"folder": folder, "name": "copy.epub", "immutable": scope.immutable(info),
                            "linked_identity": metadata._identity(info), "stem": "exact"}
            fence.db.close()
            for action in (lambda: scope.unlink("copy.epub", dir_fd=source),
                           lambda: scope.write_manifest(backup, "exact.json", b"{}"),
                           lambda: scope.verify()):
                with self.assertRaises(metadata.Refused):
                    action()
        self.assertTrue(os.path.exists(os.path.join(folder, "copy.epub")))
        self.assertFalse(os.path.exists(self.state + "/copies/exact.json"))

    def test_wrong_syscall_scope_and_stale_shared_lock_refuse(self):
        with self.fence() as fence:
            scope = writer.ScopedFileActions(fence, self.root, self.state)
            with self.assertRaises(metadata.Refused):
                scope.link("copy.epub", "wrong.epub", src_dir_fd=0, dst_dir_fd=0, follow_symlinks=False)
        with writer.converter_lock(self.state), self.assertRaises(metadata.Refused):
            with writer.converter_lock(self.state):
                pass
        self.assertFalse(os.path.exists(self.state + "/lock"))

    def test_ransom_and_strip_modes_refuse_before_database_connect(self):
        args = mock.Mock(snapshot="unused", app_capture="unused", deadline_epoch=time.time() + 10)
        env = {"EBOOK_ROOT": self.root, "STATE_DIR": self.state, "STRIP_SERIES_METADATA": "1"}
        with mock.patch.object(writer, "PrimaryShareFence") as connect, self.assertRaises(metadata.Refused):
            writer.run(args, env)
        connect.assert_not_called()
        env["STRIP_SERIES_METADATA"] = "0"
        with mock.patch.object(writer, "PrimaryShareFence") as connect, self.assertRaises(metadata.Refused):
            writer.run(args, env)
        connect.assert_not_called()


if __name__ == "__main__":
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    unittest.main(verbosity=2)
