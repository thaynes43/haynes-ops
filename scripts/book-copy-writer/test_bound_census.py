"""Finite temporary-file guards; run once sequentially at nice 19, never load."""
import copy
import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
MODULES = Path(os.environ.get("COPY_TEST_MODULES") or HERE.parents[1] / "kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert")
sys.path[:0] = ["/copy-writer", str(HERE), str(MODULES)]
import bound_census as bound
import epub_copies as copies
import epub_metadata as metadata
from test_epub_metadata import OPF, SERIES, fixture, read, write


def clock(seconds=0):
    return datetime.datetime.fromtimestamp(time.time() + seconds, datetime.timezone.utc).isoformat()


def source_binding(root, role):
    return {"namespace": "fixture", "pod_name": role, "pod_uid": "11111111-2222-3333-4444-555555555555",
            "job_uid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "node": "fixture-node", "image": "fixture:only",
            "image_id": "fixture@sha256:" + "a" * 64, "pod_spec_sha256": "b" * 64, "restarts": 0,
            "mount_root": root, "nfs_server": "fixture", "nfs_export": "/fixture",
            "root_identity6": [str(n) for n in metadata._identity(os.stat(root))]}


def bound_records(root):
    fingerprints, _ = bound.stat_census(root, time.monotonic() + 10, lambda: None)
    rows = []
    for path in sorted(fingerprints):
        if not path.endswith(".epub"):
            continue
        with metadata.safe_directory(os.path.dirname(os.path.join(root, path))) as directory:
            digest, identity, packages, info = bound.selected_file(directory, os.path.basename(path), path,
                                                                  time.monotonic() + 10, lambda: None)
        rows.append({**bound.serialized(identity), "source_identity": [str(n) for n in metadata._identity(info)],
                     "sha256": digest, "packages": packages})
    return {"schema": 1, "kind": "bound_census", "byte_baseline_sha256": "c" * 64,
            "byte_capture_started_at": clock(-4), "byte_completed_at": clock(-3),
            "byte_source_binding": source_binding(root, "live-byte-reader"),
            "module_sha256": bound.current_module_hashes(),
            "current_validation": {"capture_started_at": clock(-2), "checked_at": clock(-1),
                                   "source_binding": source_binding(root, "fenced-source")},
            "files": rows, "all_file_fingerprints": fingerprints}


class BoundTests(unittest.TestCase):
    def test_distinct_stat_guard_keeps_actual_queries_before_and_after_complete_walk(self):
        with tempfile.TemporaryDirectory() as root:
            write(os.path.join(root, 'folder/a.txt'), b'a')
            write(os.path.join(root, 'folder/b.txt'), b'b')
            events = []
            values, _ = bound.stat_census(root, time.monotonic() + 10,
                                         lambda: events.append('query'), lambda: events.append('local'))
            self.assertEqual(set(values), {'folder/a.txt', 'folder/b.txt'})
            self.assertEqual(events[0], 'query')
            self.assertEqual(events[-1], 'query')
            self.assertEqual(events.count('query'), 2)
            self.assertEqual(events.count('local'), 4)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root, self.state = self.tmp.name + "/EBooks", self.tmp.name + "/.epub-convert"
        self.keeper = "Suzanne Collins/Mockingjay/keeper.epub"
        self.extra = "Suzanne Collins/Mockingjay/extra.epub"
        self.untouched = "Other Writer/Unrelated/untouched.epub"
        self.raw = fixture()
        for path in (self.keeper, self.extra):
            write(os.path.join(self.root, path), self.raw)
        write(os.path.join(self.root, self.untouched), fixture(OPF.replace(b"Mockingjay &amp; more", b"Unrelated").replace(b"Suzanne Collins", b"Other Writer").replace(SERIES, b"")))
        write(os.path.join(self.root, "notes.txt"), b"complete non-EPUB coverage")
        self.proof = bound_records(self.root)
        self.hashes = {row["path"]: row["sha256"] for row in self.proof["files"]}
        now = clock()
        fence = {"complete": True, "quiesced": True, "checked_at": now, "capture_started_at": now,
                 "quiescence_established_at": now, "quiescence_checked_at": now,
                 "quiescence_proof": "TEMPORARY FIXTURE ONLY"}
        self.snapshot = {"schema": 1, "created_at": now, "ebook_root": self.root,
                         "library_capture_started_at": now,
                         "files": [{"path": p, "sha256": sha} for p, sha in self.hashes.items()],
                         "lazylibrarian": {**fence, "pointers": [{"book_id": "fixture-book", "path": self.keeper}]},
                         **{key: {**fence, "protected_paths": []} for key in copies.SOURCES[1:]},
                         "bound_census": self.proof}
        self.lines, self.health_calls = [], 0

    def health(self):
        self.health_calls += 1

    def inputs(self):
        snapshot = self.tmp.name + "/snapshot.json"
        write(snapshot, json.dumps(self.snapshot).encode())
        selection = self.tmp.name + "/selection.json"
        value = {"schema": 1, "kind": "copy_selection", "approved_for_retention": True,
                 "snapshot_sha256": metadata.sha256(read(snapshot)),
                 "bound_census_baseline_sha256": self.proof["byte_baseline_sha256"],
                 "entries": [{"path": self.extra, "sha256": self.hashes[self.extra],
                              "keeper": self.keeper, "keeper_sha256": self.hashes[self.keeper]}]}
        write(selection, json.dumps(value).encode())
        return snapshot, selection

    def apply(self, dry_run=False):
        snapshot, selection = self.inputs()
        return copies.consolidate_bound(snapshot, self.root, self.state, 0, [],
                                         lambda msg, **fields: self.lines.append((msg, fields)), dry_run,
                                         time.monotonic() + 10, selection_path=selection, health=self.health)

    def assert_refuses_before_any_move(self, error=metadata.Refused):
        with mock.patch.object(copies, "move_copy", side_effect=AssertionError("a move must not start")) as move:
            with self.assertRaises(error):
                self.apply()
            move.assert_not_called()
        self.assertTrue(Path(self.root, self.extra).exists())

    def test_manual_all_selected_reverified_before_first_move_without_reading_unselected_bytes(self):
        verified, moved = [], []
        original_selected, original_move = bound.selected_file, copies.move_copy
        def select(*args, **kwargs):
            verified.append(args[2])
            return original_selected(*args, **kwargs)
        def move(*args, **kwargs):
            self.assertEqual(set(verified), {self.keeper, self.extra})
            moved.append(args[0])
            return original_move(*args, **kwargs)
        with mock.patch.object(bound, "selected_file", side_effect=select), mock.patch.object(copies, "move_copy", side_effect=move), \
             mock.patch.object(metadata, "identity_preflight", side_effect=AssertionError("no whole OPF reread")):
            result = self.apply()
        self.assertEqual(result["moved"], 1)
        self.assertEqual(moved, [self.extra])
        self.assertGreater(self.health_calls, 4)
        self.assertEqual(read(os.path.join(self.root, self.keeper)), self.raw)
        self.assertTrue(Path(self.root, self.untouched).exists())

    def test_default_consolidation_still_parses_and_hashes_unselected_epubs(self):
        snapshot, selection = self.inputs()
        names = []
        original = copies.hash_file
        def hashed(directory, name, deadline):
            names.append(name)
            return original(directory, name, deadline)
        with mock.patch.object(bound, "prepare", side_effect=AssertionError("default must not reuse byte evidence")), \
             mock.patch.object(copies, "hash_file", side_effect=hashed):
            result = copies.consolidate(snapshot, self.root, self.state, 0, [], lambda *a, **k: None,
                                         True, time.monotonic() + 10, selection_path=selection)
        self.assertEqual(result["would_move"], 1)
        self.assertIn("untouched.epub", names)

    def test_every_fingerprint_field_and_exact_path_set_are_load_bearing(self):
        original = copy.deepcopy(self.proof["all_file_fingerprints"])
        for field in range(9):
            with self.subTest(field=field):
                value = self.proof["all_file_fingerprints"]["notes.txt"]
                if field < 6:
                    value[0][field] = str(int(value[0][field]) + 1)
                else:
                    value[field - 5] = str(int(value[field - 5]) + 1)
                self.assert_refuses_before_any_move(metadata.Changed)
                self.proof["all_file_fingerprints"] = copy.deepcopy(original)
        write(os.path.join(self.root, "new.txt"), b"new arrival")
        self.assert_refuses_before_any_move(metadata.Changed)
        Path(self.root, "new.txt").unlink()
        Path(self.root, "notes.txt").unlink()
        self.assert_refuses_before_any_move(metadata.Changed)

    def test_unsafe_numeric_or_noncanonical_stat_values_refuse(self):
        for value in (1791500000000000001, "01", "+1", "1.0", "18446744073709551616"):
            with self.subTest(value=value):
                self.proof["all_file_fingerprints"]["notes.txt"][0][3] = value
                with mock.patch.object(bound, "stat_census", side_effect=AssertionError("invalid proof must not traverse")):
                    self.assert_refuses_before_any_move()

    def test_byte_clocks_are_not_restamped_by_current_validation(self):
        self.proof["byte_capture_started_at"] = clock(-301)
        self.assert_refuses_before_any_move()
        self.proof["byte_capture_started_at"] = clock(-4)
        self.proof["byte_completed_at"] = clock(10)
        self.assert_refuses_before_any_move()
        self.proof["byte_completed_at"] = clock(-5)
        self.assert_refuses_before_any_move()

    def test_wrong_parser_or_backing_source_cannot_derive_identity(self):
        expected = self.proof["module_sha256"]
        self.proof["module_sha256"] = {"epub_metadata.py": "a" * 64, "epub_copies.py": "b" * 64}
        self.assert_refuses_before_any_move()
        self.proof["module_sha256"] = expected
        for key in ("node", "nfs_server", "nfs_export"):
            with self.subTest(key=key):
                self.proof["current_validation"]["source_binding"][key] += "-different"
                self.assert_refuses_before_any_move()
                self.proof["current_validation"]["source_binding"][key] = self.proof["byte_source_binding"][key]

    def test_selected_false_sha_raw_opf_or_complete_credit_refuses_before_first_move(self):
        row = next(row for row in self.proof["files"] if row["path"] == self.extra)
        row["packages"][0]["raw_opf_sha256"] = "d" * 64
        self.assert_refuses_before_any_move(metadata.Changed)
        self.proof = bound_records(self.root)
        self.snapshot["bound_census"] = self.proof
        row = next(row for row in self.proof["files"] if row["path"] == self.extra)
        row["author_keys"] = ["someoneelse"]
        self.assert_refuses_before_any_move(metadata.Changed)
        self.proof = bound_records(self.root)
        self.snapshot["bound_census"] = self.proof
        row = next(row for row in self.proof["files"] if row["path"] == self.extra)
        row["sha256"] = self.hashes[self.extra] = "e" * 64
        self.snapshot["files"] = [{"path": p, "sha256": sha} for p, sha in self.hashes.items()]
        self.assert_refuses_before_any_move(metadata.Changed)

    def test_unselected_change_between_walks_and_immediately_before_first_move_refuses(self):
        original = bound.stat_census
        count = 0
        def raced(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                write(os.path.join(self.root, "notes.txt"), b"changed after first walk")
            return original(*args, **kwargs)
        with mock.patch.object(bound, "stat_census", side_effect=raced):
            self.assert_refuses_before_any_move(metadata.Changed)
        self.proof = bound_records(self.root)
        self.snapshot["bound_census"] = self.proof
        original_common = copies.file_fingerprints
        def late(*args, **kwargs):
            write(os.path.join(self.root, "late.txt"), b"new non-EPUB at last guard")
            return original_common(*args, **kwargs)
        with mock.patch.object(copies, "file_fingerprints", side_effect=late):
            self.assert_refuses_before_any_move(metadata.Changed)

    def test_owning_health_loss_before_selected_read_prevents_movement(self):
        self.health = mock.Mock(side_effect=metadata.Refused("fixture own fence lost"))
        self.assert_refuses_before_any_move()

    def test_selected_source_symlink_and_changed_descriptor_path_refuse(self):
        destination = self.tmp.name + "/replacement.epub"
        write(destination, self.raw)
        Path(self.root, self.extra).unlink()
        Path(self.root, self.extra).symlink_to(destination)
        self.assert_refuses_before_any_move(metadata.Changed)

    def test_final_wholewalk_refuses_unapproved_change_during_later_move_and_retains_partial_receipts(self):
        second = "Suzanne Collins/Mockingjay/second.epub"
        write(os.path.join(self.root, second), self.raw)
        self.proof = bound_records(self.root)
        self.snapshot["bound_census"] = self.proof
        self.hashes[second] = metadata.sha256(self.raw)
        self.snapshot["files"].append({"path": second, "sha256": self.hashes[second]})
        snapshot, selection = self.inputs()
        chosen = json.loads(read(selection))
        chosen["entries"].append({**chosen["entries"][0], "path": second})
        write(selection, json.dumps(chosen).encode())
        def logged(msg, **fields):
            self.lines.append((msg, fields))
            if fields.get("result") == "moved" and fields.get("path") == second:
                write(os.path.join(self.root, "notes.txt"), b"unexpected other writer")
        result = copies.consolidate_bound(snapshot, self.root, self.state, 0, [], logged,
                                          deadline=time.monotonic() + 10, selection_path=selection, health=self.health)
        self.assertEqual((result["moved"], result["refused"]), (2, 1))
        self.assertEqual(len([f for m, f in self.lines if m == "epub_copy_consolidate" and f.get("result") == "moved"]), 2)
        final = next(f for m, f in self.lines if m == "epub_copy_bound_final")
        self.assertEqual((final["result"], set(final["moved_paths"])), ("refused", {self.extra, second}))

    def test_bound_selection_refuses_missing_baseline_or_approval_before_byte_reads(self):
        snapshot, selection = self.inputs()
        original = json.loads(read(selection))
        for key, value in (("bound_census_baseline_sha256", "d" * 64), ("approved_for_retention", False)):
            with self.subTest(key=key), mock.patch.object(bound, "selected_file", side_effect=AssertionError("invalid approval must not read selected bytes")):
                changed = {**original, key: value}
                write(selection, json.dumps(changed).encode())
                with self.assertRaises(metadata.Refused):
                    copies.consolidate_bound(snapshot, self.root, self.state, 0, [], lambda *a, **k: None,
                                              deadline=time.monotonic() + 10, selection_path=selection, health=self.health)

    def producer_records(self):
        baseline = {"schema": 1, "kind": "live_byte_baseline", "ebook_root": self.root,
                    "capture_started_at": self.proof["byte_capture_started_at"], "completed_at": self.proof["byte_completed_at"],
                    "complete": True, "stable_before_after": True, "read_only": True, "production_writes": 0,
                    "module_sha256": self.proof["module_sha256"], "source_binding": self.proof["byte_source_binding"],
                    "files": self.proof["files"], "all_file_fingerprints": self.proof["all_file_fingerprints"],
                    "read_only_stage_measurement": {"synthetic_fixture": True}}
        current = {"schema": 2, "kind": "stat_census", "ebook_root": self.root,
                   "started_at": self.proof["current_validation"]["capture_started_at"],
                   "checked_at": self.proof["current_validation"]["checked_at"], "complete": True,
                   "quiesced": False, "production_writes": 0, "source_binding": self.proof["current_validation"]["source_binding"],
                   "all_file_fingerprints": copy.deepcopy(self.proof["all_file_fingerprints"]), "permissions": {},
                   "derived_ignore_markers": [], "additional_protected_paths": [], "configured_hold_folders": []}
        return baseline, current

    def test_pure_assembly_preserves_original_byte_clocks_and_exact_current_protections(self):
        baseline, current = self.producer_records()
        current["additional_protected_paths"] = [{"path": self.untouched, "reason": "current source protection"}]
        library, result = bound.bind_live_baseline(baseline, "c" * 64, current, self.root)
        self.assertEqual(result, self.proof)
        self.assertEqual(library["files"], baseline["files"])
        self.assertEqual(library["additional_protected_paths"], current["additional_protected_paths"])
        self.assertEqual(library["started_at"], current["started_at"])
        self.assertEqual(result["byte_capture_started_at"], baseline["capture_started_at"])
        self.assertNotEqual(result["byte_capture_started_at"], current["started_at"])

    def test_pure_assembly_refuses_one_nanosecond_or_numeric_loss_before_identity_derivation(self):
        baseline, current = self.producer_records()
        value = current["all_file_fingerprints"]["notes.txt"][0][3]
        current["all_file_fingerprints"]["notes.txt"][0][3] = str(int(value) + 1)
        with mock.patch.object(bound, "validate_bound", side_effect=AssertionError("changed source must not derive identity")):
            with self.assertRaises(metadata.Changed):
                bound.bind_live_baseline(baseline, "c" * 64, current, self.root)
        current["all_file_fingerprints"]["notes.txt"][0][3] = int(value)
        with self.assertRaises(metadata.Refused):
            bound.bind_live_baseline(baseline, "c" * 64, current, self.root)

    def test_pure_assembly_rejects_borrowed_source_rows_and_stale_original_byte_read(self):
        baseline, current = self.producer_records()
        current["files"] = baseline["files"]
        with self.assertRaises(metadata.Refused):
            bound.bind_live_baseline(baseline, "c" * 64, current, self.root)
        del current["files"]
        baseline["capture_started_at"] = clock(-301)
        with self.assertRaises(metadata.Refused):
            bound.bind_live_baseline(baseline, "c" * 64, current, self.root)

    def test_manual_complete_walk_count_cap_refuses_before_unbounded_allocation(self):
        with self.assertRaises(metadata.Refused):
            copies.file_fingerprints(self.root, time.monotonic() + 10, health=self.health, max_files=2)


if __name__ == "__main__":
    unittest.main()
