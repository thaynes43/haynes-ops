#!/usr/bin/env python3
"""Bounded temporary-library tests; run once with nice -n 19, never stress/loop."""

import datetime
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import epub_copies as copies
import epub_metadata as metadata
from test_epub_metadata import OPF, fixture, read, snapshot, write


class CopyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "EBooks")
        self.state = os.path.join(self.tmp.name, ".epub-convert")
        self.evidence = os.path.join(self.tmp.name, "snapshot.json")
        self.keeper = "Suzanne Collins/Mockingjay/primary.epub"
        self.extra = "Suzanne Collins/Boxed Set/copy.epub"
        self.raw = fixture()
        write(os.path.join(self.root, self.keeper), self.raw)
        write(os.path.join(self.root, self.extra), self.raw)
        self.lines = []

    def evidence_data(self):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        files = []
        for folder, _dirs, names in os.walk(self.root):
            for name in names:
                if name.endswith(".epub"):
                    path = os.path.join(folder, name)
                    files.append({"path": os.path.relpath(path, self.root), "sha256": metadata.sha256(read(path))})
        return {"schema": 1, "created_at": now, "ebook_root": self.root, "files": files,
                "lazylibrarian": {"complete": True, "checked_at": now,
                                  "pointers": [{"book_id": "ll-book-1", "path": self.keeper}]},
                **{source: {"complete": True, "checked_at": now, "protected_paths": []}
                   for source in copies.SOURCES[1:]}}

    def run_copies(self, data=None, dry_run=False, holds=frozenset()):
        write(self.evidence, json.dumps(data or self.evidence_data()).encode())
        self.lines = []
        return copies.consolidate(self.evidence, self.root, self.state, 0, holds,
                                  lambda msg, **fields: self.lines.append({"msg": msg, **fields}), dry_run)

    def test_dry_run_then_move_keeps_pointed_inode_bytes_and_manifest(self):
        before = snapshot(self.root)
        counts = self.run_copies(dry_run=True)
        self.assertEqual(counts["would_move"], 1)
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(os.path.exists(self.state))
        original = os.stat(os.path.join(self.root, self.extra))
        counts = self.run_copies()
        self.assertEqual((counts["moved"], counts["retained"], counts["refused"]), (1, 1, 0))
        self.assertEqual(read(os.path.join(self.root, self.keeper)), self.raw)
        self.assertFalse(os.path.exists(os.path.join(self.root, self.extra)))
        result = next(line for line in self.lines if line.get("result") == "moved")
        manifest = json.loads(read(result["backup_manifest"]))
        retained = os.path.join(self.state, "copies", manifest["backup_file"])
        self.assertEqual(read(retained), self.raw)
        self.assertEqual(os.stat(retained).st_ino, original.st_ino)
        self.assertEqual(os.stat(retained).st_nlink, 1)
        self.assertEqual(manifest["relative_path"], self.extra)
        self.assertEqual(manifest["evidence_sha256"], metadata.sha256(read(self.evidence)))

    def test_each_dependency_and_ignore_or_configured_hold_retains_extra(self):
        for source in copies.SOURCES[1:]:
            with self.subTest(source=source):
                data = self.evidence_data()
                data[source]["protected_paths"] = [{"path": self.extra, "reason": "saved zero-counter XPath or repair/want"}]
                counts = self.run_copies(data)
                self.assertEqual((counts["moved"], counts["protected"]), (0, 1))
                self.assertEqual(read(os.path.join(self.root, self.extra)), self.raw)
                self.assertFalse(os.path.exists(self.state))
        counts = self.run_copies(holds=frozenset(["Suzanne Collins/Boxed Set"]))
        self.assertEqual(counts["protected"], 1)
        write(os.path.join(self.root, "Suzanne Collins", ".ll_ignore"), b"census repair")
        counts = self.run_copies()
        self.assertEqual((counts["moved"], counts["protected"]), (0, 1))

    def test_no_keeper_or_competing_pointer_retains_whole_group(self):
        data = self.evidence_data()
        data["lazylibrarian"]["pointers"] = []
        self.assertEqual(self.run_copies(data)["review_groups"], 1)
        data["lazylibrarian"]["pointers"] = [{"book_id": "1", "path": self.keeper}, {"book_id": "2", "path": self.extra}]
        self.assertEqual(self.run_copies(data)["review_groups"], 1)
        self.assertFalse(os.path.exists(self.state))

    def test_unknown_stale_and_incomplete_evidence_refuse_before_any_move(self):
        for source in copies.SOURCES:
            with self.subTest(source=source):
                data = self.evidence_data()
                data[source]["complete"] = False
                with self.assertRaisesRegex(metadata.Refused, "complete"):
                    self.run_copies(data)
        data = self.evidence_data()
        data["kavita"]["checked_at"] = "2020-01-01T00:00:00Z"
        with self.assertRaisesRegex(metadata.Refused, "stale"):
            self.run_copies(data)
        data = self.evidence_data()
        data["files"].pop()
        with self.assertRaisesRegex(metadata.Refused, "exact current"):
            self.run_copies(data)
        data = self.evidence_data()
        data["files"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(metadata.Changed, "hash/identity"):
            self.run_copies(data)
        self.assertFalse(os.path.exists(self.state))
        self.assertEqual(read(os.path.join(self.root, self.extra)), self.raw)

    def test_multiauthor_metadata_is_reviewed_without_moving(self):
        ambiguous = OPF.replace(b'<dc:creator id="author">', b'<dc:creator>Another Author</dc:creator><dc:creator id="author">')
        write(os.path.join(self.root, self.extra), fixture(opf=ambiguous))
        counts = self.run_copies()
        self.assertEqual(counts["moved"], 0)
        self.assertTrue(any(line.get("result") == "review" and line.get("path") == self.extra for line in self.lines))

    def test_hardlinks_ignore_symlinks_and_existing_destinations_refuse(self):
        extra = os.path.join(self.root, self.extra)
        hardlink = os.path.join(self.tmp.name, "download.epub")
        os.link(extra, hardlink)
        self.assertEqual(self.run_copies()["refused"], 1)
        os.unlink(hardlink)
        marker = os.path.join(os.path.dirname(extra), ".ll_ignore")
        os.symlink(os.path.join(self.tmp.name, "missing"), marker)
        self.assertEqual(self.run_copies()["refused"], 1)
        os.unlink(marker)
        stem = metadata.sha256(self.extra.encode()) + "-" + metadata.sha256(self.raw)
        existing = os.path.join(self.state, "copies", stem + ".epub")
        write(existing, b"older retained artifact")
        self.assertEqual(self.run_copies()["refused"], 1)
        self.assertEqual(read(existing), b"older retained artifact")
        self.assertEqual(read(extra), self.raw)

    def test_new_ignore_marker_is_checked_again_before_move(self):
        original_move = copies.move_copy

        def protects(*args, **kwargs):
            write(os.path.join(self.root, "Suzanne Collins", "Boxed Set", ".ll_ignore"), b"new repair")
            return original_move(*args, **kwargs)

        with mock.patch.object(copies, "move_copy", side_effect=protects):
            counts = self.run_copies()
        self.assertEqual(counts["refused"], 1)
        self.assertEqual(read(os.path.join(self.root, self.extra)), self.raw)
        self.assertFalse(os.path.exists(os.path.join(self.state, "copies")))

    def test_cross_device_backup_refuses_without_removing_source(self):
        original_fstat = os.fstat

        def another_device(fd):
            info = original_fstat(fd)
            if os.readlink(f"/proc/self/fd/{fd}") == os.path.join(self.state, "copies"):
                return type("DifferentDevice", (), {"st_dev": info.st_dev + 1})()
            return info

        with mock.patch.object(os, "fstat", side_effect=another_device):
            counts = self.run_copies()
        self.assertEqual(counts["refused"], 1)
        self.assertEqual(read(os.path.join(self.root, self.extra)), self.raw)

    def test_source_change_and_keeper_loss_refuse_and_preserve_extra(self):
        initial = copies.move_copy

        def changed(*args, **kwargs):
            write(os.path.join(self.root, self.extra), self.raw + b"changed")
            return initial(*args, **kwargs)

        with mock.patch.object(copies, "move_copy", side_effect=changed):
            counts = self.run_copies()
        self.assertEqual(counts["refused"], 1)
        self.assertEqual(read(os.path.join(self.root, self.extra)), self.raw + b"changed")
        write(os.path.join(self.root, self.extra), self.raw)

        def missing_keeper(*args, **kwargs):
            os.unlink(os.path.join(self.root, self.keeper))
            return initial(*args, **kwargs)

        with mock.patch.object(copies, "move_copy", side_effect=missing_keeper):
            counts = self.run_copies()
        self.assertEqual(counts["refused"], 1)
        self.assertEqual(read(os.path.join(self.root, self.extra)), self.raw)

    def test_interrupted_move_retains_both_names_and_is_never_auto_finished(self):
        original_link = os.link

        def interrupted(*args, **kwargs):
            original_link(*args, **kwargs)
            raise OSError("crash after link")

        with mock.patch.object(os, "link", side_effect=interrupted):
            counts = self.run_copies()
        self.assertEqual(counts["refused"], 1)
        self.assertEqual(read(os.path.join(self.root, self.extra)), self.raw)
        self.assertEqual(os.stat(os.path.join(self.root, self.extra)).st_nlink, 2)
        counts = self.run_copies()
        self.assertEqual(counts["refused"], 1)
        self.assertEqual(os.stat(os.path.join(self.root, self.extra)).st_nlink, 2)

    def test_expiry_during_application_never_removes_original(self):
        original_move = copies.move_copy

        def expires(*args, **kwargs):
            with mock.patch.object(copies.time, "time", return_value=time.time() + 301):
                return original_move(*args, **kwargs)

        with mock.patch.object(copies, "move_copy", side_effect=expires):
            counts = self.run_copies()
        self.assertEqual(counts["refused"], 1)
        self.assertEqual(read(os.path.join(self.root, self.extra)), self.raw)

    def test_cli_lock_modes_and_dry_run(self):
        write(self.evidence, json.dumps(self.evidence_data()).encode())
        script = os.path.join(os.path.dirname(__file__), "epub_convert.py")
        env = dict(os.environ, EBOOK_ROOT=self.root, STATE_DIR=self.state, SETTLE_SECONDS="0",
                   STRIP_SERIES_METADATA="0", STRIP_ONLY="0", DRY_RUN="1", KAVITA_URL="", KAVITA_API_KEY="")
        env.pop("STRIP_FOLDERS_JSON", None)
        proc = subprocess.run([sys.executable, script, "--consolidate-copies", self.evidence],
                              env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(os.path.exists(self.state))
        env["STRIP_SERIES_METADATA"] = "1"
        proc = subprocess.run([sys.executable, script, "--consolidate-copies", self.evidence],
                              env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("modes off", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
