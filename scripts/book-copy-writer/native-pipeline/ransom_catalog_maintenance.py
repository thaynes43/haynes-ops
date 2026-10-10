"""Seven-cell component for an admitted existing maintenance caller; no CLI."""
import copy
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import time
import re
from zoneinfo import ZoneInfo

import epub_metadata as metadata


class Refused(ValueError):
    pass


TARGET = {"Library": 1, "Series": 1650, "Volume": 1800, "Chapter": 3358, "File": 3570}
FILE = "/data/cephfs-hdd/data/media/books/EBooks/Daniel Silva/Ransom/Ransom - Daniel Silva.epub"
DELTA = {
    "Series": (1650, {"Name": ("Gabriel Allon", "Ransom"),
                      "NormalizedName": ("gabrielallon", "ransom"),
                      "OriginalName": ("Gabriel Allon", "Ransom")}),
    "Volume": (1800, {"LookupName": ("26", "-100000"), "Name": ("26", "-100000"),
                     "MinNumber": (26.0, -100000.0), "MaxNumber": (26.0, -100000.0)}),
}


def require(value, message):
    if not value:
        raise Refused(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def cell(kind, value):
    require(kind in ("null", "integer", "real", "text", "blob"), "unknown SQLite storage type")
    if kind == "real":
        require(math.isfinite(value), "nonfinite SQLite real")
        value = value.hex()
    elif kind == "blob":
        value = value.hex()
    return {"type": kind, "value": value}


def decode(value):
    return (float.fromhex(value["value"]) if value["type"] == "real" else
            bytes.fromhex(value["value"]) if value["type"] == "blob" else value["value"])


def snapshot(db):
    """Complete schema and all rows, preserving actual nullable storage types."""
    require(db.execute("PRAGMA quick_check").fetchall() == [("ok",)], "SQLite integrity differs")
    require(not db.execute("PRAGMA foreign_key_check").fetchall(), "SQLite foreign key violation")
    schema = [list(r) for r in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
    require(not any(row[0] == "trigger" for row in schema), "unreviewed SQLite trigger")
    tables = {}
    for kind, name, _, _ in schema:
        if kind != "table":
            continue
        columns = [row[1] for row in db.execute("PRAGMA table_info(" + quote(name) + ")")]
        require(columns and len(columns) == len(set(columns)), "SQLite column shape differs")
        query = ",".join(quote(c) + ",typeof(" + quote(c) + ")" for c in columns)
        rows = [{c: cell(values[2 * i + 1], values[2 * i]) for i, c in enumerate(columns)}
                for values in db.execute("SELECT " + query + " FROM " + quote(name))]
        tables[name] = sorted(rows, key=canonical)
    return {"schema": schema, "tables": tables}


def row(state, table, identity):
    matches = [r for r in state["tables"].get(table, [])
               if r.get("Id") == cell("integer", identity)]
    require(len(matches) == 1, "target row identity/cardinality differs")
    return matches[0]


def utc(value):
    require(isinstance(value, str) and value, "target activity clock missing")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    # These are native *Utc columns; an omitted suffix remains UTC.
    return parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed.astimezone(dt.timezone.utc)


def preconditions(state, now):
    s, v, c, f = (row(state, t, TARGET[k]) for t, k in
                  (("Series", "Series"), ("Volume", "Volume"), ("Chapter", "Chapter"), ("MangaFile", "File")))
    require(decode(s["LibraryId"]) == 1 and decode(s["Format"]) == 3, "target library/format differs")
    require(decode(v["SeriesId"]) == 1650 and decode(c["VolumeId"]) == 1800
            and decode(f["ChapterId"]) == 3358 and decode(f["FilePath"]) == FILE, "target relationships/path differ")
    for table, field, identity in (("Volume", "SeriesId", 1650), ("Chapter", "VolumeId", 1800),
                                   ("MangaFile", "ChapterId", 3358)):
        require(sum(decode(r[field]) == identity for r in state["tables"][table]) == 1,
                "target has extra volumes/chapters/files")
    for table, identity in (("Series", 1650), ("SeriesMetadata", 1650), ("Volume", 1800),
                            ("Chapter", 3358), ("MangaFile", 3570)):
        require(all(decode(value) == 0 for key, value in row(state, table, identity).items()
                    if key.endswith("Locked")), "target metadata lock changed")
    for other in state["tables"]["Series"]:
        if decode(other["Id"]) == 1650 or decode(other["LibraryId"]) != 1 or decode(other["Format"]) != 3:
            continue
        require(not any(metadata.kavita_normalized(decode(other.get(k, cell("null", None))) or "")
                        in ("ransom", "gabrielallon") for k in
                        ("Name", "NormalizedName", "OriginalName", "LocalizedName", "NormalizedLocalizedName", "SortName")),
                "same-library target alias conflict")
    times, sessions = [], set()
    for table, fields in (("AppUserProgresses", ("LastModifiedUtc",)),
                          ("AppUserReadingSessionActivityData", ("StartTimeUtc", "EndTimeUtc"))):
        for r in state["tables"][table]:
            if decode(r["SeriesId"]) == 1650:
                require(decode(r["LibraryId"]) == 1 and decode(r["ChapterId"]) == 3358
                        and decode(r["VolumeId"]) == 1800, "target activity association differs")
                times.extend(utc(decode(r[key])) for key in fields)
                if table == "AppUserReadingSessionActivityData":
                    sessions.add(decode(r["AppUserReadingSessionId"]))
    for identity in sessions:
        require(decode(row(state, "AppUserReadingSession", identity)["IsActive"]) == 0,
                "target reading session is active")
    for r in state["tables"]["AppUserReadingHistory"]:
        history = json.loads(decode(r["Data"]))
        require(isinstance(history, dict) and isinstance(history.get("Activities"), list),
                "reading history shape differs")
        if 1650 in history.get("SeriesIds", []) or any(a.get("SeriesId") == 1650 for a in history["Activities"]):
            times.append(utc(decode(r["DateUtc"])))
            times.extend(utc(a[k]) for a in history["Activities"] if a.get("SeriesId") == 1650
                         for k in ("StartTimeUtc", "EndTimeUtc"))
    require(times and now - max(times).timestamp() >= 30 * 86400, "target all-user reading is not idle30")


def expected_after(before):
    after = copy.deepcopy(before)
    for table, (identity, fields) in DELTA.items():
        r = row(after, table, identity)
        for name, (old, new) in fields.items():
            kind = "real" if isinstance(old, float) else "text"
            require(r[name] == cell(kind, old), "seven-cell before value/type differs")
            r[name] = cell(kind, new)
        after["tables"][table].sort(key=canonical)
    return after


def retain(db, before, directory, library_root):
    """Retain full DB, never overwrite a backup or replace a production DB."""
    metadata.validate_paths(library_root, str(directory))
    with metadata.safe_directory(str(directory)) as fd:
        name = "catalog-before.sqlite"
        output_fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_RDWR, 0o600, dir_fd=fd)
        initial = os.fstat(output_fd)
        try:
            path = f"/proc/self/fd/{fd}/{name}"
            with contextlib.closing(sqlite3.connect(path, isolation_level=None)) as backup:
                db.backup(backup)
                require(snapshot(backup) == before, "retained SQLite logical state differs")
            os.fsync(output_fd)
            current = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require((current.st_dev, current.st_ino, current.st_nlink, current.st_uid, current.st_mode & 0o777)
                    == (initial.st_dev, initial.st_ino, 1, os.getuid(), 0o600), "retained SQLite identity differs")
            os.lseek(output_fd, 0, os.SEEK_SET)
            h = hashlib.sha256()
            while chunk := os.read(output_fd, 1024 * 1024):
                h.update(chunk)
            finished = os.fstat(output_fd)
            require((finished.st_size, finished.st_mtime_ns, finished.st_ctime_ns)
                    == (current.st_size, current.st_mtime_ns, current.st_ctime_ns),
                    "retained SQLite changed during checksum")
            receipt = {"schema": 1, "file": name, "sha256": h.hexdigest(), "logical_before_sha256": digest(before)}
            metadata._write_file(fd, "catalog-before.json", canonical(receipt) + b"\n")
            os.fsync(fd)
            return receipt
        finally:
            os.close(output_fd)


def admitted_guard(db, before, contract, maintenance_guard, retention_directory, clock):
    """Shared physical/authority checks for forward and independently admitted inverse."""
    require(contract.get("owner_approved") is True and contract.get("root_runtime_go") is True
            and contract.get("prepared_only") is False, "catalog authority not admitted")
    require(contract.get("target_ids") == TARGET and contract.get("before_sha256") == digest(before)
            and contract.get("schema_sha256") == digest(before["schema"]), "catalog contract binding differs")
    require(not db.in_transaction and db.row_factory is None
            and db.execute("PRAGMA foreign_keys").fetchone() == (1,), "SQLite caller connection differs")
    databases = db.execute("PRAGMA database_list").fetchall()
    main = [r for r in databases if r[1] == "main"]
    # foreign_key_check can open SQLite's empty in-memory temp database.
    require(len(main) == 1 and main[0][2] == contract["database_path"]
            and all(r[1] == "main" or r[1] == "temp" and r[2] == "" for r in databases)
            and not db.execute("SELECT name FROM sqlite_temp_master").fetchall(),
            "SQLite database identity/path differs")
    with metadata.safe_directory(os.path.dirname(main[0][2])) as fd:
        info = os.stat(os.path.basename(main[0][2]), dir_fd=fd, follow_symlinks=False)
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
            and [info.st_dev, info.st_ino] == contract["database_device_inode"], "SQLite physical identity differs")
    require(str(Path(retention_directory)) == contract["retention_directory"], "retention directory differs")
    deadline = contract["original_abort_epoch"]
    require(type(deadline) in (int, float) and math.isfinite(deadline), "original abort clock invalid")
    def guard():
        require(clock() < deadline, "original catalog abort clock expired")
        maintenance_guard()  # Actual existing host custody/service/lease checks, never a cached boolean.
    return guard


def apply(db, before, contract, maintenance_guard, retention_directory, *, inverse=False, clock=time.time):
    """Caller must hold/maintain the separately reviewed real service fence."""
    guard = admitted_guard(db, before, contract, maintenance_guard, retention_directory, clock)
    guard()
    preconditions(before, clock())
    after = expected_after(before)
    current, desired = (after, before) if inverse else (before, after)
    require(snapshot(db) == current, "complete current schema/rows differ")
    if not inverse:
        retained = retain(db, before, retention_directory, contract["library_root"])
    else:
        retained = None  # Immutable original already retained; never create an overwrite inverse.
    guard()
    db.execute("BEGIN IMMEDIATE")
    try:
        require(snapshot(db) == current, "transaction current schema/rows differ")
        for table, (identity, fields) in DELTA.items():
            guard()
            old, new = row(current, table, identity), row(desired, table, identity)
            columns = list(old)
            sets = ",".join(quote(k) + "=?" for k in fields)
            where = " AND ".join("typeof(" + quote(k) + ")=? AND " + quote(k) + " COLLATE BINARY IS ?" for k in columns)
            params = [decode(new[k]) for k in fields]
            params += [v for k in columns for v in (old[k]["type"], decode(old[k]))]
            require(db.execute("UPDATE " + quote(table) + " SET " + sets + " WHERE " + where, params).rowcount == 1,
                    "full-row typed catalog CAS differs")
        require(snapshot(db) == desired, "catalog write exceeded seven cells")
        guard()
        require(snapshot(db) == desired, "catalog state changed before commit")
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return {"schema": 1, "changed_cells": 7, "row_updates": 2, "inverse": inverse,
            "before_sha256": digest(current), "after_sha256": digest(desired), "retained": retained}


# Exactly the qualified native algorithm's 25 keys. No progress/curation fields.
SCAN_FIELDS = {
    "Series": (1650, ("SortName", "LastFolderScanned", "LastFolderScannedUtc", "LastModified",
                      "LastModifiedUtc", "PrimaryColor", "SecondaryColor")),
    "Volume": (1800, ("LastModified", "LastModifiedUtc")),
    "Chapter": (3358, ("Count", "IsSpecial", "Range", "Title", "TotalCount", "LastModified", "LastModifiedUtc")),
    "MangaFile": (3570, ("Bytes", "KoreaderHash", "LastModified", "LastModifiedUtc", "LastFileAnalysis", "LastFileAnalysisUtc")),
    "SeriesMetadata": (1650, ("TotalCount", "PublicationStatus", "RowVersion")),
}


def require_scan_delta(before, after, explicit, started, finished):
    """Fresh rows and real completion interval; old fixture clocks are not authority."""
    require(type(started) in (int, float) and type(finished) in (int, float)
            and math.isfinite(started) and math.isfinite(finished) and 0 < finished - started <= 300,
            "actual target scan interval differs")
    wanted = {table + ":" + field for table, (_, fields) in SCAN_FIELDS.items()
              for field in fields if not field.startswith("Last") and not field.endswith("Color")}
    require(set(explicit) == wanted, "explicit native values differ from qualified scan keys")
    fixed = {"Series:SortName": cell("text", "Ransom"), "Chapter:Count": cell("integer", 0),
             "Chapter:IsSpecial": cell("integer", 1), "Chapter:Range": cell("text", "Ransom"),
             "Chapter:Title": cell("text", "Ransom"), "Chapter:TotalCount": cell("integer", 1),
             "MangaFile:Bytes": cell("integer", 1081344), "SeriesMetadata:TotalCount": cell("integer", 1),
             "SeriesMetadata:PublicationStatus": cell("integer", 2)}
    require(all(explicit[key] == value for key, value in fixed.items()), "qualified native explicit values differ")
    version = row(before, "SeriesMetadata", 1650)["RowVersion"]
    require(version["type"] == "integer" and 0 <= decode(version) <= 2**32 - 3
            and explicit["SeriesMetadata:RowVersion"] == cell("integer", decode(version) + 2),
            "native uint RowVersion increment differs")
    koreader = explicit["MangaFile:KoreaderHash"]
    require(koreader["type"] == "text" and re.fullmatch(r"[0-9A-Fa-f]{32}", decode(koreader)),
            "pinned candidate native hash shape differs")
    expected = copy.deepcopy(before)
    for table, (identity, fields) in SCAN_FIELDS.items():
        original, actual, changed = row(before, table, identity), row(after, table, identity), row(expected, table, identity)
        for field in fields:
            value = actual[field]
            if field.startswith("Last"):
                require(value["type"] == "text" and value != original[field], "native target scan clock did not change")
                timestamp = dt.datetime.fromisoformat(decode(value).replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=dt.timezone.utc if field.endswith("Utc") else ZoneInfo("America/New_York"))
                require(started - 1 <= timestamp.timestamp() <= finished + 1, "native scan clock outside actual completion")
            elif field.endswith("Color"):
                require(value == cell("null", None) or value["type"] == "text"
                        and re.fullmatch(r"#[0-9A-Fa-f]{6}", decode(value)), "native color shape differs")
            else:
                require(value == explicit[table + ":" + field] and value != original[field],
                        "explicit native scan value differs")
            changed[field] = value
        expected["tables"][table].sort(key=canonical)
    require(expected == after, "scan changed protected schema/rows/saved-state/curation")


def invert_after_scan(db, original, after, contract, maintenance_guard, retention_directory, clock=time.time):
    """Production 32-key CAS algorithm; a NEW stopped-service admission is required."""
    guard = admitted_guard(db, original, contract, maintenance_guard, retention_directory, clock)
    require(contract.get("post_scan_after_sha256") == digest(after), "fresh post-scan rows not bound")
    require_scan_delta(expected_after(original), after, contract["native_explicit_values"],
                       contract["scan_started_epoch"], contract["scan_finished_epoch"])
    fields = {table: (identity, set(names)) for table, (identity, names) in SCAN_FIELDS.items()}
    for table, (identity, names) in DELTA.items():
        require(fields[table][0] == identity, "inverse identity differs")
        fields[table][1].update(names)
    require(sum(len(names) for _, names in fields.values()) == 32 and len(fields) == 5,
            "inverse is not exact 32-key/five-row union")
    guard()
    require(snapshot(db) == after, "current post-scan state drifted; inverse refused")
    db.execute("BEGIN IMMEDIATE")
    try:
        require(snapshot(db) == after, "post-scan state changed before transaction")
        for table, (identity, names) in fields.items():
            guard()
            current, desired = row(after, table, identity), row(original, table, identity)
            names = sorted(names)
            columns = list(current)
            sets = ",".join(quote(name) + "=?" for name in names)
            where = " AND ".join("typeof(" + quote(name) + ")=? AND " + quote(name) + " COLLATE BINARY IS ?" for name in columns)
            values = [decode(desired[name]) for name in names]
            values += [value for name in columns for value in (current[name]["type"], decode(current[name]))]
            require(db.execute("UPDATE " + quote(table) + " SET " + sets + " WHERE " + where, values).rowcount == 1,
                    "inverse full nullable typed after-row CAS differs")
        require(snapshot(db) == original, "inverse exceeded exact 32 keys")
        guard()
        require(snapshot(db) == original, "protected state changed before inverse commit")
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return {"schema": 1, "changed_cells": 32, "row_updates": 5, "inverse": True,
            "before_sha256": digest(after), "after_sha256": digest(original)}
