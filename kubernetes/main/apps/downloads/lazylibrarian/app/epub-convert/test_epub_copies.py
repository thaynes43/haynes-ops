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
        writer = {"capture_started_at": now, "quiescence_established_at": now, "quiescence_checked_at": now,
                  "quiescence_proof": "explicit complete writer fence held through application"}
        return {"schema": 1, "created_at": now, "library_capture_started_at": now,
                "ebook_root": self.root, "files": files,
                "lazylibrarian": {"complete": True, "quiesced": True, "checked_at": now,
                                  **writer, "pointers": [{"book_id": "ll-book-1", "path": self.keeper}]},
                **{source: {"complete": True, "quiesced": True, "checked_at": now, **writer, "protected_paths": []}
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

    def run_selected(self, paths, data=None, changes=None, dry_run=False, logger=None):
        data = data or self.evidence_data()
        write(self.evidence, json.dumps(data).encode())
        hashes = {row["path"]: row["sha256"] for row in data["files"]}
        selection = {"schema": 1, "kind": "copy_selection", "approved_for_retention": True,
                     "snapshot_sha256": metadata.sha256(read(self.evidence)),
                     "entries": [{"path": path, "sha256": hashes[path], "keeper": self.keeper,
                                  "keeper_sha256": hashes[self.keeper]} for path in paths]}
        selection.update(changes or {})
        target = os.path.join(self.tmp.name, "selection.json")
        write(target, json.dumps(selection).encode())
        self.lines = []
        return copies.consolidate(self.evidence, self.root, self.state, 0, frozenset(),
                                  logger or (lambda msg, **fields: self.lines.append({"msg": msg, **fields})),
                                  dry_run, selection_path=target)

    def test_ordered_selection_verifies_first_and_keeps_unselected_copy_without_rehash(self):
        second, unselected = "Suzanne Collins/Second/copy.epub", "Suzanne Collins/Third/copy.epub"
        for path in (second, unselected):
            write(os.path.join(self.root, path), self.raw)
        original_hash = copies.hash_file
        hashes = []
        def observed(directory, name, deadline):
            hashes.append((os.readlink(f"/proc/self/fd/{directory}"), name))
            return original_hash(directory, name, deadline)
        with mock.patch.object(copies, "hash_file", side_effect=observed):
            counts = self.run_selected([second, self.extra])
        self.assertEqual((counts["moved"], counts["refused"]), (2, 0))
        self.assertEqual(read(os.path.join(self.root, unselected)), self.raw)
        moves = [row for row in self.lines if row.get("result") == "moved"]
        self.assertEqual([row["path"] for row in moves], [second, self.extra])
        proof_index = next(i for i, row in enumerate(self.lines) if row["msg"] == "epub_copy_stage_proof")
        last_move = next(i for i, row in enumerate(self.lines) if row.get("result") == "moved" and row["path"] == self.extra)
        self.assertLess(proof_index, last_move)
        proof = self.lines[proof_index]
        self.assertTrue(proof["protected_bytes_verified"])
        self.assertEqual(proof["unchanged_epubs"], 3)
        self.assertEqual(sum(folder == os.path.dirname(os.path.join(self.root, unselected)) for folder, _name in hashes), 1)

    def test_selection_binding_protection_and_duplicates_refuse_before_any_move(self):
        data = self.evidence_data()
        variants = ({"snapshot_sha256": "0" * 64}, {"approved_for_retention": False}, {"entries": []}, {"schema": True},
                    *({"entries": [{"path": path, "sha256": digest, "keeper": keeper, "keeper_sha256": keeper_digest}]}
                      for path, digest, keeper, keeper_digest in (
                          (self.extra, "0" * 64, self.keeper, metadata.sha256(self.raw)),
                          ("Suzanne Collins/Unknown/missing.epub", metadata.sha256(self.raw), self.keeper, metadata.sha256(self.raw)),
                          (self.keeper, metadata.sha256(self.raw), self.keeper, metadata.sha256(self.raw)),
                          (self.extra, metadata.sha256(self.raw), self.keeper, "0" * 64))))
        before = snapshot(self.root)
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises(metadata.Refused):
                self.run_selected([self.extra], data=data, changes=changes)
            self.assertEqual(snapshot(self.root), before)
        with self.assertRaises(metadata.Refused):
            self.run_selected([self.extra, self.extra], data=data)
        data["kavita"]["protected_paths"] = [{"path": self.extra, "reason": "saved reading-list chapter"}]
        with self.assertRaises(metadata.Refused):
            self.run_selected([self.extra], data=data)
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(os.path.exists(self.state))

    def test_first_stage_detects_changed_protected_file_and_stops_remaining_moves(self):
        second, protected = "Suzanne Collins/Second/copy.epub", "Suzanne Collins/Protected/copy.epub"
        for path in (second, protected):
            write(os.path.join(self.root, path), self.raw)
        data = self.evidence_data()
        data["kavita"]["protected_paths"] = [{"path": protected, "reason": "saved chapter"}]
        def logger(msg, **fields):
            self.lines.append({"msg": msg, **fields})
            if fields.get("result") == "moved" and fields["path"] == self.extra:
                write(os.path.join(self.root, protected), self.raw + b"racing writer")
        counts = self.run_selected([self.extra, second], data=data, logger=logger)
        self.assertEqual((counts["moved"], counts["refused"]), (1, 1))
        self.assertEqual(read(os.path.join(self.root, second)), self.raw)
        self.assertEqual(read(os.path.join(self.root, self.keeper)), self.raw)
        self.assertFalse(any(row["msg"] == "epub_copy_stage_phase" for row in self.lines))
        manifest = next(row["backup_manifest"] for row in self.lines if row.get("result") == "moved")
        self.assertEqual(read(os.path.join(os.path.dirname(manifest), json.loads(read(manifest))["backup_file"])), self.raw)

    def test_first_stage_detects_unapproved_sidecar_change_and_stops_remaining(self):
        second = "Suzanne Collins/Second/copy.epub"
        sidecar = os.path.join(self.root, "Suzanne Collins/Mockingjay/book.opf")
        write(os.path.join(self.root, second), self.raw)
        write(sidecar, b"unchanged library sidecar")
        def logger(msg, **fields):
            self.lines.append({"msg": msg, **fields})
            if fields.get("result") == "moved":
                write(sidecar, b"changed by an unfenced publisher")
        counts = self.run_selected([self.extra, second], logger=logger)
        self.assertEqual((counts["moved"], counts["refused"]), (1, 1))
        self.assertEqual(read(os.path.join(self.root, second)), self.raw)
        self.assertFalse(any(row["msg"] == "epub_copy_stage_phase" for row in self.lines))

    def test_new_epub_during_initial_hash_refuses_before_any_selected_move(self):
        before = snapshot(self.root)
        original_hash = copies.hash_file
        added = False
        def racing_hash(directory, name, deadline):
            nonlocal added
            result = original_hash(directory, name, deadline)
            if not added:
                write(os.path.join(self.root, "Suzanne Collins/New/unobserved.epub"), self.raw)
                added = True
            return result
        with mock.patch.object(copies, "hash_file", side_effect=racing_hash), self.assertRaises(metadata.Changed):
            self.run_selected([self.extra])
        after = snapshot(self.root)
        self.assertTrue(all(after[path] == value for path, value in before.items() if value[2] is not None))
        self.assertFalse(os.path.exists(self.state))

    def test_selection_expiry_after_first_move_keeps_rest_and_verified_retained_bytes(self):
        second = "Suzanne Collins/Second/copy.epub"
        write(os.path.join(self.root, second), self.raw)
        clock = time.monotonic()
        def logger(msg, **fields):
            nonlocal clock
            self.lines.append({"msg": msg, **fields})
            if fields.get("result") == "moved":
                clock += copies.SNAPSHOT_MAX_AGE + 1
        with mock.patch.object(copies.time, "monotonic", side_effect=lambda: clock):
            counts = self.run_selected([self.extra, second], logger=logger)
        self.assertEqual((counts["moved"], counts["refused"]), (1, 1))
        self.assertEqual(read(os.path.join(self.root, second)), self.raw)
        result = next(row for row in self.lines if row.get("result") == "moved")
        manifest = json.loads(read(result["backup_manifest"]))
        self.assertEqual(read(os.path.join(self.state, "copies", manifest["backup_file"])), self.raw)
        self.assertFalse(any(row["msg"] == "epub_copy_stage_phase" for row in self.lines))

    def test_cli_selection_requires_consolidation_and_existing_manual_gates(self):
        self.run_selected([self.extra], dry_run=True)
        selection = os.path.join(self.tmp.name, "selection.json")
        script = os.path.join(os.path.dirname(__file__), "epub_convert.py")
        env = dict(os.environ, EBOOK_ROOT=self.root, STATE_DIR=self.state, SETTLE_SECONDS="0",
                   STRIP_SERIES_METADATA="0", STRIP_ONLY="0", DRY_RUN="1", KAVITA_URL="", KAVITA_API_KEY="")
        env.pop("STRIP_FOLDERS_JSON", None)
        for arguments in (["--copy-selection", selection],
                          ["--restore-retained-copy", self.evidence, "--copy-selection", selection]):
            result = subprocess.run([sys.executable, script, *arguments], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("usage:", result.stdout)
        arguments = ["--consolidate-copies", self.evidence, "--copy-selection", selection]
        result = subprocess.run([sys.executable, script, *arguments], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        env["STRIP_SERIES_METADATA"] = "1"
        result = subprocess.run([sys.executable, script, *arguments], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("modes off", result.stdout)
        self.assertFalse(os.path.exists(self.state))

    def moved_manifest(self):
        self.assertEqual(self.run_copies()["moved"], 1)
        manifest_path = next(line["backup_manifest"] for line in self.lines if line.get("result") == "moved")
        manifest = json.loads(read(manifest_path))
        retained = os.path.join(self.state, "copies", manifest["backup_file"])
        return manifest_path, retained

    def test_verified_copy_return_has_fresh_single_link_inode_and_retains_backup(self):
        path, retained = self.moved_manifest()
        before = snapshot(self.state)
        saved_inode = os.stat(retained).st_ino
        self.assertEqual(copies.restore_retained_copy(path, self.root, self.state, True)["result"], "would_restore")
        self.assertFalse(os.path.exists(os.path.join(self.root, self.extra)))
        self.assertEqual(copies.restore_retained_copy(path, self.root, self.state)["result"], "restored")
        restored = os.stat(os.path.join(self.root, self.extra))
        saved = os.stat(retained)
        self.assertNotEqual(restored.st_ino, saved_inode)
        self.assertEqual((restored.st_nlink, saved.st_nlink), (1, 1))
        self.assertEqual((restored.st_uid, restored.st_gid, restored.st_mode), (saved.st_uid, saved.st_gid, saved.st_mode))
        self.assertEqual(read(os.path.join(self.root, self.extra)), self.raw)
        self.assertEqual(snapshot(self.state), before)
        self.assertTrue(metadata.inspect_epub(read(os.path.join(self.root, self.extra))))

    def test_copy_return_refuses_existing_or_racing_entry_without_overwrite(self):
        path, retained = self.moved_manifest()
        original = os.path.join(self.root, self.extra)
        write(original, b"current library copy")
        with self.assertRaisesRegex(metadata.Refused, "must be absent"):
            copies.restore_retained_copy(path, self.root, self.state)
        self.assertEqual(read(original), b"current library copy")
        os.unlink(original)
        link = os.link

        def raced(*args, **kwargs):
            write(original, b"new writer copy")
            return link(*args, **kwargs)

        with mock.patch.object(os, "link", side_effect=raced), self.assertRaises(FileExistsError):
            copies.restore_retained_copy(path, self.root, self.state)
        self.assertEqual(read(original), b"new writer copy")
        self.assertEqual(read(retained), self.raw)
        self.assertFalse(any(name.startswith(".copy-restore-") for name in os.listdir(os.path.dirname(original))))

    def test_copy_return_refuses_symlink_checksum_owner_mode_and_unsafe_manifest(self):
        path, retained = self.moved_manifest()
        saved_manifest = read(path)
        write(retained, self.raw + b"changed")
        with self.assertRaisesRegex(metadata.Refused, "bytes or owner/mode"):
            copies.restore_retained_copy(path, self.root, self.state)
        write(retained, self.raw)
        real_backup = retained + ".real"
        os.rename(retained, real_backup)
        os.symlink(real_backup, retained)
        with self.assertRaises(OSError):
            copies.restore_retained_copy(path, self.root, self.state)
        os.unlink(retained)
        os.rename(real_backup, retained)
        for changes in ({"original_mode": 0}, {"relative_path": "../outside.epub"}, {"backup_file": "../outside.epub"}):
            data = json.loads(saved_manifest)
            write(path, json.dumps({**data, **changes}).encode())
            with self.assertRaises(metadata.Refused):
                copies.restore_retained_copy(path, self.root, self.state)
        write(path, saved_manifest)
        os.rename(path, path + ".real")
        os.symlink(path + ".real", path)
        with self.assertRaises(OSError):
            copies.restore_retained_copy(path, self.root, self.state)
        self.assertFalse(os.path.exists(os.path.join(self.root, self.extra)))

    def test_copy_return_interruption_preserves_verified_bytes_and_hold_blocks_return(self):
        path, retained = self.moved_manifest()
        original = os.path.join(self.root, self.extra)
        with mock.patch.dict(os.environ, {"LIBRARY_HOLD_FOLDERS_JSON": '["Suzanne Collins/Boxed Set"]'}):
            with self.assertRaisesRegex(metadata.Refused, "hold"):
                copies.restore_retained_copy(path, self.root, self.state)
        link = os.link

        def interrupted(*args, **kwargs):
            link(*args, **kwargs)
            raise OSError("interrupted after publication")

        with mock.patch.object(os, "link", side_effect=interrupted), self.assertRaisesRegex(OSError, "interrupted"):
            copies.restore_retained_copy(path, self.root, self.state)
        self.assertEqual(read(original), self.raw)
        self.assertEqual(read(retained), self.raw)
        self.assertEqual(os.stat(original).st_nlink, 1)
        with self.assertRaisesRegex(metadata.Refused, "must be absent"):
            copies.restore_retained_copy(path, self.root, self.state)

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
                for attestation in (False, None):
                    data = self.evidence_data()
                    if attestation is None:
                        data[source].pop("quiesced")
                    else:
                        data[source]["quiesced"] = attestation
                    with self.assertRaisesRegex(metadata.Refused, "quiescence"):
                        self.run_copies(data)
        data = self.evidence_data()
        data["kavita"]["checked_at"] = "2020-01-01T00:00:00Z"
        data["kavita"]["capture_started_at"] = data["kavita"]["quiescence_established_at"] = "2020-01-01T00:00:00Z"
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

    def test_complete_snapshot_consumer_refuses_missing_or_reversed_source_start_evidence(self):
        for source in copies.SOURCES:
            with self.subTest(source=source):
                data = self.evidence_data()
                data[source].pop("capture_started_at")
                with self.assertRaises(metadata.Refused):
                    self.run_copies(data)
                data = self.evidence_data()
                data[source]["quiescence_established_at"] = (
                    datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=1)).isoformat()
                with self.assertRaisesRegex(metadata.Refused, "ordering"):
                    self.run_copies(data)
                data = self.evidence_data()
                data[source]["checked_at"] = (
                    datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)).isoformat()
                with self.assertRaisesRegex(metadata.Refused, "ordering"):
                    self.run_copies(data)
        self.assertFalse(os.path.exists(self.state))
        self.assertEqual(read(os.path.join(self.root, self.extra)), self.raw)

    def test_multiauthor_metadata_is_reviewed_without_moving(self):
        ambiguous = OPF.replace(b'<dc:creator id="author">', b'<dc:creator>Another Author</dc:creator><dc:creator id="author">')
        write(os.path.join(self.root, self.extra), fixture(opf=ambiguous))
        counts = self.run_copies()
        self.assertEqual(counts["moved"], 0)
        self.assertTrue(any(line.get("result") == "review" and line.get("path") == self.extra for line in self.lines))

    def test_conflicting_creator_spelling_shared_alias_retains_all_possible_copies(self):
        third = "Suzanne Collins/Alternate/third.epub"
        write(os.path.join(self.root, third), fixture(opf=OPF.replace(b'Suzanne Collins', b'Suzanne C. Collins')))
        before = snapshot(self.root)
        counts = self.run_copies()
        self.assertEqual(counts["moved"], 0)
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(os.path.exists(self.state))
        reviews = {line.get("path") for line in self.lines if line.get("result") == "review"}
        self.assertTrue({self.keeper, self.extra, third}.issubset(reviews))

    def test_editor_translator_and_combined_credit_extras_are_never_copy_candidates(self):
        for raw in (
            OPF.replace(b'<dc:creator id="author">', b'<dc:creator id="author" xmlns:opf="http://www.idpf.org/2007/opf" opf:role="edt">'),
            OPF.replace(b'</metadata>', b'<meta property="role" refines="#author">trl</meta></metadata>'),
            OPF.replace(b'Suzanne Collins', b'Quinn, Enoch, Hawkins, Ryan'),
        ):
            with self.subTest(raw=raw[-100:]):
                write(os.path.join(self.root, self.extra), fixture(opf=raw))
                before = snapshot(self.root)
                counts = self.run_copies()
                self.assertEqual(counts["moved"], 0)
                self.assertEqual(snapshot(self.root), before)
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

    def test_eligibility_refusal_logs_final_census_without_any_move(self):
        marker = os.path.join(os.path.dirname(os.path.join(self.root, self.extra)), ".ll_ignore")
        os.symlink(os.path.join(self.tmp.name, "missing"), marker)
        before = snapshot(self.root)
        with mock.patch.object(copies, "move_copy") as move:
            counts = self.run_copies()
        move.assert_not_called()
        self.assertEqual((counts["moved"], counts["refused"]), (0, 1))
        census = [row for row in self.lines if row["msg"] == "epub_copy_consolidate_census"]
        self.assertEqual(len(census), 1)
        self.assertEqual({key: census[0][key] for key in counts}, counts)
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(os.path.exists(self.state))

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

    def test_snapshot_expiry_during_keeper_hash_is_checked_after_hash(self):
        clock = [time.time()]
        data = self.evidence_data()
        old = datetime.datetime.fromtimestamp(clock[0] - 299, datetime.timezone.utc).isoformat()
        data["created_at"] = old
        for source in copies.SOURCES:
            data[source]["checked_at"] = old
            for field in ("capture_started_at", "quiescence_established_at", "quiescence_checked_at"):
                data[source][field] = old
        original_hash = copies.hash_file
        keeper_reads = [0]

        def slow_keeper(directory, name, deadline):
            result = original_hash(directory, name, deadline)
            if name == os.path.basename(self.keeper):
                keeper_reads[0] += 1
                if keeper_reads[0] == 2:
                    clock[0] += 2
            return result

        with mock.patch.object(copies.time, "time", side_effect=lambda: clock[0]), \
                mock.patch.object(copies, "hash_file", side_effect=slow_keeper):
            counts = self.run_copies(data)
        self.assertEqual(counts["refused"], 1)
        self.assertEqual(read(os.path.join(self.root, self.extra)), self.raw)
        self.assertFalse(os.path.exists(os.path.join(self.state, "copies")))

    def test_expiry_immediately_before_unlink_keeps_both_names(self):
        clock = [time.time()]
        data = self.evidence_data()
        old = datetime.datetime.fromtimestamp(clock[0] - 299, datetime.timezone.utc).isoformat()
        data["created_at"] = old
        for source in copies.SOURCES:
            data[source]["checked_at"] = old
            for field in ("capture_started_at", "quiescence_established_at", "quiescence_checked_at"):
                data[source][field] = old
        original_stat = os.stat

        def expires_after_path_check(path, *args, **kwargs):
            info = original_stat(path, *args, **kwargs)
            if path == os.path.basename(self.extra) and info.st_nlink == 2:
                clock[0] += 2
            return info

        with mock.patch.object(copies.time, "time", side_effect=lambda: clock[0]), \
                mock.patch.object(os, "stat", side_effect=expires_after_path_check):
            counts = self.run_copies(data)
        self.assertEqual(counts["refused"], 1)
        self.assertEqual(read(os.path.join(self.root, self.extra)), self.raw)
        self.assertEqual(os.stat(os.path.join(self.root, self.extra)).st_nlink, 2)

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
        env["STRIP_SERIES_METADATA"] = "0"
        env["DRY_RUN"] = "0"
        proc = subprocess.run([sys.executable, script, "--consolidate-copies", ""],
                              env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("usage:", proc.stdout)
        self.assertFalse(os.path.exists(self.state))

    def test_cli_copy_return_dry_run_and_real_publication_use_only_return_mode(self):
        manifest, retained = self.moved_manifest()
        script = os.path.join(os.path.dirname(__file__), "epub_convert.py")
        env = dict(os.environ, EBOOK_ROOT=self.root, STATE_DIR=self.state, SETTLE_SECONDS="0",
                   STRIP_SERIES_METADATA="0", STRIP_ONLY="0", DRY_RUN="1", KAVITA_URL="", KAVITA_API_KEY="")
        env.pop("STRIP_FOLDERS_JSON", None)
        proc = subprocess.run([sys.executable, script, "--restore-retained-copy", manifest],
                              env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["result"], "would_restore")
        self.assertFalse(os.path.exists(os.path.join(self.root, self.extra)))
        env["DRY_RUN"] = "0"
        proc = subprocess.run([sys.executable, script, "--restore-retained-copy", manifest],
                              env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(read(os.path.join(self.root, self.extra)), read(retained))
        self.assertEqual(os.stat(os.path.join(self.root, self.extra)).st_nlink, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
