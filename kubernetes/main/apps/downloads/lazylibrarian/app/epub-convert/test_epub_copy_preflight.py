#!/usr/bin/env python3
"""Bounded read-only adapter tests; run once under nice -n 19, never stress."""

import datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import epub_copy_preflight as preflight
import epub_metadata as metadata
from test_epub_metadata import OPF, fixture, snapshot, write


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "EBooks")
        self.keeper, self.extra = "Author/Book/primary.epub", "Author/Box/copy.epub"
        for path in (self.keeper, self.extra):
            write(os.path.join(self.root, path), fixture())
        self.timestamp = preflight.now()
        self.ll = {"captureStartedAt": self.timestamp, "capturedAt": self.timestamp, "llBooks": [
            {"BookID": "1", "BookFile": self.root + "/" + self.keeper}]}
        self.app = {"capture_started_at": self.timestamp, "captured_at": self.timestamp,
                    "completed_at": self.timestamp, "read_only": "on", "scope": "full",
                    "app": {"items": [], "wants": []}}
        self.proof = {"captureStartedAt": self.timestamp, "capturedAt": self.timestamp,
                      "readOnlySource": True, "before": ["stable"], "after": ["stable"]}
        self.census = {"capture_started_at": self.timestamp, "checked_at": self.timestamp, "complete": True, "protected_paths": []}
        self.attestations = {source: {"checked_at": self.timestamp, "quiesced": True,
                                     "established_at": self.timestamp,
                                     "proof": "reviewed runtime writer hold remains active"}
                             for source in preflight.copies.SOURCES}

    def prepare(self, library=None, series=None, protected=None, errors=None, attestations=None):
        return preflight.prepare(library or preflight.collect_library(self.root, 5), self.ll, self.app,
                                 series or {}, protected or [], errors or [], self.proof, self.census,
                                 self.attestations if attestations is None else attestations)

    def test_capture_is_complete_read_only_and_records_ancestor_and_configured_holds(self):
        write(os.path.join(self.root, "Author", ".ll_ignore"), b"repair")
        before = snapshot(self.root)
        with mock.patch.dict(os.environ, {"LIBRARY_HOLD_FOLDERS_JSON": '["Author/Box"]'}):
            library = preflight.collect_library(self.root, 5)
        self.assertTrue(library["complete"])
        self.assertFalse(library["quiesced"])
        self.assertEqual(library["production_writes"], 0)
        self.assertEqual(len(library["files"]), 2)
        extra = next(row for row in library["files"] if row["path"] == self.extra)
        self.assertTrue(any("configured library hold" in reason for reason in extra["ignore_reasons"]))
        self.assertTrue(any(".ll_ignore" in reason for reason in extra["ignore_reasons"]))
        self.assertEqual(snapshot(self.root), before)

    def test_missing_false_stale_or_unproved_quiescence_never_enables_moves(self):
        for attestations in ({}, {source: {"quiesced": False} for source in self.attestations},
                             {source: {"quiesced": True, "checked_at": self.timestamp} for source in self.attestations},
                             {source: {"quiesced": True, "checked_at": "2020-01-01T00:00:00Z", "proof": "old"}
                              for source in self.attestations}):
            with self.subTest(attestations=attestations):
                evidence, report = self.prepare(attestations=attestations)
                self.assertFalse(report["apply_ready"])
                self.assertTrue(all(not evidence[source]["quiesced"] for source in self.attestations))
                self.assertTrue(all(row["protected_reasons"] for row in report["groups"][0]["copies"]))

    def test_older_quiescence_evidence_caps_source_expiry(self):
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=250)).isoformat()
        self.attestations["kavita"]["checked_at"] = old
        self.attestations["kavita"]["established_at"] = old
        evidence, report = self.prepare()
        self.assertTrue(report["apply_ready"])
        self.assertEqual(evidence["kavita"]["checked_at"], self.timestamp)
        self.assertEqual(evidence["kavita"]["quiescence_checked_at"], old)
        self.assertLess(preflight.copies.expiry_deadline(evidence) - preflight.time.monotonic(), 51)

    def test_stop_after_capture_or_census_start_never_backdates_quiescence(self):
        library = preflight.collect_library(self.root, 5)
        later = preflight.now()
        self.attestations["kavita"].update(established_at=later, checked_at=later)
        evidence, report = self.prepare(library=library)
        self.assertFalse(report["apply_ready"])
        self.assertFalse(evidence["kavita"]["quiesced"])
        self.assertTrue(any("after a capture" in reason for reason in report["blockers"]))
        # Fresh source captures after the stop cannot legitimize a library census
        # begun before the fence was established.
        self.proof["captureStartedAt"] = self.proof["capturedAt"] = later
        evidence, report = self.prepare(library=library)
        self.assertFalse(report["apply_ready"])
        self.assertFalse(evidence["kavita"]["quiesced"])
        self.attestations["kavita"].pop("established_at")
        evidence, report = self.prepare(library=library)
        self.assertFalse(evidence["kavita"]["quiesced"])

    def test_fence_after_source_start_before_completion_and_missing_start_refuse(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        at = lambda delta: (now + datetime.timedelta(seconds=delta)).isoformat()
        for data, start, end in ((self.ll, "captureStartedAt", "capturedAt"),
                                 (self.proof, "captureStartedAt", "capturedAt"),
                                 (self.app, "capture_started_at", "completed_at"),
                                 (self.census, "capture_started_at", "checked_at")):
            data[start], data[end] = at(-20), at(-1)
        for value in self.attestations.values():
            value.update(established_at=at(-5), checked_at=at(-1))
        evidence, report = self.prepare()
        self.assertFalse(report["apply_ready"])
        self.assertTrue(all(not evidence[source]["quiesced"] for source in self.attestations))
        for data, start in ((self.ll, "captureStartedAt"), (self.proof, "captureStartedAt"),
                            (self.app, "capture_started_at"), (self.census, "capture_started_at")):
            data.pop(start)
        for value in self.attestations.values():
            value.update(established_at=at(-25))
        evidence, report = self.prepare()
        self.assertFalse(report["apply_ready"])
        self.assertTrue(all(not evidence[source]["complete"] for source in self.attestations))
        self.assertTrue(all(not evidence[source]["quiesced"] for source in self.attestations))

    def test_source_completion_before_start_refuses_even_under_earlier_fence(self):
        self.ll["captureStartedAt"] = preflight.now()
        evidence, report = self.prepare()
        self.assertFalse(report["apply_ready"])
        self.assertFalse(evidence["lazylibrarian"]["complete"])
        self.assertTrue(any("completion precedes" in reason for reason in report["blockers"]))

    def test_parked_landed_request_anchor_and_any_saved_state_protect_extra(self):
        self.app["app"]["items"] = [{"id": "item", "source": "kavita", "external_id": 7, "deleted_at": None}]
        self.app["app"]["wants"] = [{"id": "want", "ebook_status": "landed", "unroutable_reason": "parked",
                                      "pairing_books_item_id": "item"}]
        evidence, report = self.prepare(series={"7": [self.extra]},
                                       protected=[{"path": self.extra, "reason": "zero-counter XPath"}])
        self.assertTrue(report["apply_ready"])
        reasons = next(row for row in report["groups"][0]["copies"] if row["path"] == self.extra)["protected_reasons"]
        self.assertTrue(any("zero-counter XPath" in reason for reason in reasons))
        self.assertTrue(any("app want want" in reason for reason in reasons))
        self.assertEqual(evidence["app_wants"]["protected_paths"][0]["path"], self.extra)
        _evidence, incomplete = self.prepare(series={})
        self.assertFalse(incomplete["apply_ready"])
        self.assertTrue(any("no file map" in reason for reason in incomplete["blockers"]))

    def test_ambiguous_role_or_combined_credit_is_reported_without_grouping(self):
        for opf in (OPF.replace(b'Suzanne Collins', b'Quinn, Enoch, Hawkins, Ryan'),
                    OPF.replace(b'</metadata>', b'<meta property="role" refines="#author">trl</meta></metadata>')):
            with self.subTest(opf=opf[-100:]):
                write(os.path.join(self.root, self.extra), fixture(opf=opf))
                _evidence, report = self.prepare()
                self.assertEqual(report["same_author_groups"], 0)
                self.assertIn(self.extra, {row["path"] for row in report["ambiguous_files"]})

    def test_copied_kavita_db_resolves_full_file_map_and_zero_counter_state(self):
        path = os.path.join(self.tmp.name, "kavita.db")
        con = sqlite3.connect(path)
        con.executescript('CREATE TABLE Series(Id INTEGER); CREATE TABLE Volume(Id INTEGER,SeriesId INTEGER); '
                          'CREATE TABLE Chapter(Id INTEGER,VolumeId INTEGER); '
                          'CREATE TABLE MangaFile(FilePath TEXT,ChapterId INTEGER);')
        for table in preflight.STATE_TABLES:
            con.execute(f'CREATE TABLE "{table}"(Id INTEGER,SeriesId INTEGER,ChapterId INTEGER,PagesRead INTEGER,LastXPath TEXT,Data TEXT,AppUserReadingSessionId INTEGER,CollectionsId INTEGER,ItemsId INTEGER,TargetSeriesId INTEGER,ScrobbleEventId1 INTEGER,SeriesMetadatasId INTEGER,SeriesIds TEXT,LibraryIds TEXT)')
        con.execute('INSERT INTO Series VALUES(7)')
        con.execute('INSERT INTO Volume VALUES(8,7)')
        con.execute('INSERT INTO Chapter VALUES(9,8)')
        con.execute('INSERT INTO MangaFile VALUES(?,9)', (self.root + "/" + self.extra,))
        con.execute('INSERT INTO AppUserProgresses(Id,SeriesId,ChapterId,PagesRead,LastXPath) VALUES(1,7,9,0,?)', ("/html/body/p[2]",))
        con.execute('INSERT INTO AppUserReadingSession(Id) VALUES(11)')
        con.execute('INSERT INTO AppUserReadingSessionActivityData(Id,SeriesId,ChapterId,AppUserReadingSessionId) VALUES(12,7,9,11)')
        con.execute('INSERT INTO AppUserReadingHistory(Id,Data) VALUES(13,?)',
                    (json.dumps({"SeriesIds": [7], "ChapterIds": [9], "Activities": []}),))
        con.execute('INSERT INTO ReadingListItem(Id,SeriesId,ChapterId) VALUES(14,7,9)')
        con.execute('INSERT INTO AppUserCollectionSeries(CollectionsId,ItemsId) VALUES(15,7)')
        con.execute('INSERT INTO AppUserReadingProfiles(Id,SeriesIds,LibraryIds) VALUES(16,?,?)', ('[7]', '[]'))
        con.execute('INSERT INTO ScrobbleHold(Id,SeriesId) VALUES(17,7)')
        con.execute('INSERT INTO ScrobbleEvent(Id,SeriesId) VALUES(18,7)')
        con.execute('INSERT INTO ScrobbleError(Id,ScrobbleEventId1) VALUES(19,18)')
        con.execute('INSERT INTO SeriesRelation(Id,SeriesId,TargetSeriesId) VALUES(20,7,7)')
        con.execute('CREATE TABLE SeriesMetadata(Id INTEGER,SeriesId INTEGER)')
        con.execute('INSERT INTO SeriesMetadata VALUES(21,7)')
        con.execute('INSERT INTO CollectionTagSeriesMetadata(Id,SeriesMetadatasId) VALUES(22,21)')
        con.commit()
        con.close()
        before = Path(path).read_bytes()
        series, protected, errors, counts, disconnected = preflight.kavita_dependencies(path, self.proof, self.root)
        self.assertEqual(series["7"], [self.extra])
        self.assertEqual(protected[0]["path"], self.extra)
        self.assertEqual(errors, [])
        self.assertEqual(counts["AppUserProgresses"], 1)
        self.assertTrue(any("AppUserReadingSession/11" in row["reason"] for row in protected))
        self.assertTrue(any("AppUserReadingHistory/13" in row["reason"] for row in protected))
        self.assertEqual(disconnected, [])
        self.assertTrue(any("ReadingListItem/14" in row["reason"] for row in protected))
        self.assertTrue(any("AppUserCollectionSeries/" in row["reason"] for row in protected))
        for name in ("AppUserReadingProfiles/16", "ScrobbleHold/17", "ScrobbleError/19", "SeriesRelation/20", "CollectionTagSeriesMetadata/22"):
            self.assertTrue(any(name in row["reason"] for row in protected), name)
        self.assertEqual(Path(path).read_bytes(), before)
        self.assertFalse(Path(path + "-journal").exists())
        with self.assertRaises(metadata.Refused):
            preflight.kavita_dependencies(path, {**self.proof, "after": ["changed"]}, self.root)

    def test_history_removed_chapter_uses_current_series_and_disconnected_ids_are_explicit(self):
        path = os.path.join(self.tmp.name, "legacy.db")
        con = sqlite3.connect(path)
        con.executescript('CREATE TABLE Series(Id INTEGER); CREATE TABLE Volume(Id INTEGER,SeriesId INTEGER); '
                          'CREATE TABLE Chapter(Id INTEGER,VolumeId INTEGER); '
                          'CREATE TABLE MangaFile(FilePath TEXT,ChapterId INTEGER);')
        for table in preflight.STATE_TABLES:
            con.execute(f'CREATE TABLE "{table}"(Id INTEGER,SeriesId INTEGER,ChapterId INTEGER,VolumeId INTEGER,Data TEXT,ItemsId INTEGER,CollectionsId INTEGER)')
        con.execute('INSERT INTO Series VALUES(7)')
        con.execute('INSERT INTO Volume VALUES(8,7)')
        con.execute('INSERT INTO Chapter VALUES(9,8)')
        con.execute('INSERT INTO MangaFile VALUES(?,9)', (self.root + "/" + self.extra,))
        history = {"SeriesIds": [7, 70], "ChapterIds": [90, 900],
                   "Activities": [{"SeriesId": 7, "VolumeId": 8, "ChapterId": 90},
                                  {"SeriesId": 70, "VolumeId": 80, "ChapterId": 900}]}
        con.execute('INSERT INTO AppUserReadingHistory(Id,Data) VALUES(13,?)', (json.dumps(history),))
        con.commit()
        con.close()
        before = Path(path).read_bytes()
        _series, protected, errors, _counts, disconnected = preflight.kavita_dependencies(path, self.proof, self.root)
        self.assertEqual(errors, [])
        self.assertTrue(any(row["path"] == self.extra for row in protected))
        self.assertEqual(len(disconnected), 1)
        self.assertEqual(disconnected[0]["ids"], {"Series": 70, "Volume": 80, "Chapter": 900})
        self.assertEqual(len(disconnected[0]["whole_row_sha256"]), 64)
        self.assertEqual(Path(path).read_bytes(), before)
        # Any existing id or orphan MangaFile forbids claiming disconnection.
        con = sqlite3.connect(path)
        con.execute('INSERT INTO Volume VALUES(80,70)')
        con.execute('INSERT INTO MangaFile VALUES(?,900)', (self.root + "/" + self.keeper,))
        con.commit()
        con.close()
        _series, _protected, errors, _counts, _disconnected = preflight.kavita_dependencies(path, self.proof, self.root)
        self.assertTrue(any("file relationship" in error for error in errors))
        con = sqlite3.connect(path)
        con.execute('CREATE TABLE AppUserFutureSavedLocation(Id INTEGER,ChapterId INTEGER)')
        con.commit()
        con.close()
        with self.assertRaisesRegex(metadata.Refused, "unknown saved-book dependency table"):
            preflight.kavita_dependencies(path, self.proof, self.root)

    def test_arbitrary_indirect_work_and_unknown_populated_user_tables_refuse(self):
        # Table naming cannot bypass the dependency boundary.
        for ddl in ('CREATE TABLE NovelFutureBinding(Id INTEGER,JoinId INTEGER REFERENCES ReadingListItem(Id)); INSERT INTO NovelFutureBinding VALUES(1,1);',
                    'CREATE TABLE NovelUserBinding(Id INTEGER,AppUserId INTEGER); INSERT INTO NovelUserBinding VALUES(1,2);'):
            with self.subTest(ddl=ddl):
                path = os.path.join(self.tmp.name, "future.db")
                if os.path.exists(path):
                    os.unlink(path)
                con = sqlite3.connect(path)
                con.executescript('CREATE TABLE Series(Id INTEGER); CREATE TABLE Volume(Id INTEGER,SeriesId INTEGER); '
                                  'CREATE TABLE Chapter(Id INTEGER,VolumeId INTEGER); CREATE TABLE MangaFile(FilePath TEXT,ChapterId INTEGER);')
                for table in preflight.STATE_TABLES:
                    con.execute(f'CREATE TABLE "{table}"(Id INTEGER,SeriesId INTEGER REFERENCES Series(Id),ChapterId INTEGER,Data TEXT)')
                con.executescript(ddl)
                con.commit()
                con.close()
                with self.assertRaisesRegex(metadata.Refused, "unknown .*dependency table"):
                    preflight.kavita_dependencies(path, self.proof, self.root)

    def test_ll_source_copy_must_be_read_only_and_stable(self):
        path = os.path.join(self.tmp.name, "ll.jsonl")
        data = {"type": "ll-books-sql-capture", "readOnly": True, "sourceWrites": 0,
                "sourceFingerprintBefore": ["stable"], "sourceFingerprintAfter": ["stable"], **self.ll}
        Path(path).write_text('(node:24) ExperimentalWarning\n(Use `node --trace-warnings` to show where)\n' + json.dumps(data))
        self.assertEqual(preflight.ll_capture(path)["llBooks"], self.ll["llBooks"])
        Path(path).write_text(json.dumps({**data, "sourceFingerprintAfter": ["changed"]}))
        with self.assertRaises(metadata.Refused):
            preflight.ll_capture(path)


if __name__ == "__main__":
    unittest.main()
