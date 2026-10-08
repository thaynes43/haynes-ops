#!/usr/bin/env python3
"""Read-only library census and private dependency-snapshot preparation.

Run once under nice -n 19. No stress, wide parallelism or repeated load tests.
This adapter never establishes writer quiescence from an idle-looking capture.
"""

import argparse
import collections
import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

import epub_copies as copies
import epub_metadata as metadata

STATE_TABLES = ("AppUserProgresses", "AppUserBookmark", "AppUserAnnotation", "AppUserReadingSession",
                "AppUserReadingSessionActivityData", "AppUserReadingHistory", "AppUserTableOfContent")


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load(path):
    return json.loads(Path(path).read_text())


def library_path(value, root):
    if not isinstance(value, str) or not value:
        raise metadata.Refused("dependency file path is missing")
    if value.startswith(root + "/"):
        return copies.relative_path(value[len(root) + 1:])
    if value.startswith("EBooks/"):
        return copies.relative_path(value[len("EBooks/"):])
    raise metadata.Refused("dependency path is outside the exact EBooks root")


def collect_library(root, budget):
    started_at = now()
    root = os.path.abspath(root)
    with metadata.safe_directory(root):
        pass
    if budget <= 0:
        raise metadata.Refused("positive bounded capture budget is required")
    holds = metadata.library_hold_folders(root)
    deadline = time.monotonic() + min(300, budget)
    identities, errors = metadata.identity_preflight(root, deadline)
    files = []
    for identity in identities:
        path = identity["path"]
        try:
            with metadata.safe_directory(os.path.dirname(os.path.join(root, path))) as directory:
                digest, info = copies.hash_file(directory, os.path.basename(path), deadline)
            if metadata._identity(info) != identity["source_identity"]:
                raise metadata.Changed("library file changed after OPF census")
            entry = {key: sorted(value) if isinstance(value, (set, frozenset)) else value
                     for key, value in identity.items()}
            entry["sha256"] = digest
            entry["ignore_reasons"] = copies.protection_reasons(path, root, holds, [])
            files.append(entry)
        except Exception as err:
            errors.append({"path": path, "detail": f"{type(err).__name__}: {err}"[:500]})
            break
    # Re-read stable metadata so the corpus itself did not change while hashing.
    if not errors:
        after, after_errors = metadata.identity_preflight(root, deadline)
        before_map = {row["path"]: row["source_identity"] for row in identities}
        after_map = {row["path"]: row["source_identity"] for row in after}
        errors += after_errors
        if before_map != after_map:
            errors.append({"path": ".", "detail": "complete library census changed during capture"})
    return {"schema": 1, "ebook_root": root, "started_at": started_at, "checked_at": now(), "complete": not errors,
            "quiesced": False, "files": files, "errors": errors,
            "configured_hold_folders": sorted(holds),
            "bytes_hashed": sum(row["source_identity"][2] for row in files), "production_writes": 0}


def ll_capture(path):
    records = []
    for line in Path(path).read_text().splitlines():
        if line.startswith("(node:") or line.startswith("(Use `node ") or not line.strip():
            continue
        records.append(json.loads(line))
    if len(records) != 1 or records[0].get("type") != "ll-books-sql-capture":
        raise metadata.Refused("exact full LL SQL capture is required")
    row = records[0]
    if (row.get("readOnly") is not True or row.get("sourceWrites") != 0
            or not row.get("sourceFingerprintBefore")
            or row["sourceFingerprintBefore"] != row.get("sourceFingerprintAfter")):
        raise metadata.Refused("LL SQL capture was not a verified stable read-only copy")
    return row


def kavita_dependencies(db_path, proof, root):
    if proof.get("readOnlySource") is not True or not proof.get("before") or proof["before"] != proof.get("after"):
        raise metadata.Refused("Kavita database copy needs identical before/after source stats")
    con = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        files = [dict(row) for row in con.execute(
            "SELECT f.FilePath AS path,f.ChapterId AS chapter_id,v.Id AS volume_id,s.Id AS series_id "
            "FROM MangaFile f LEFT JOIN Chapter c ON c.Id=f.ChapterId LEFT JOIN Volume v ON v.Id=c.VolumeId "
            "LEFT JOIN Series s ON s.Id=v.SeriesId")]
        current_ids = {kind: {row[0] for row in con.execute(f'SELECT Id FROM "{kind}"')}
                       for kind in ("Series", "Chapter", "Volume")}
        states = {table: [dict(row) for row in con.execute(f'SELECT * FROM "{table}"')] for table in STATE_TABLES}
        con.rollback()
    finally:
        con.close()
    series_files, protected, errors = {str(key): [] for key in current_ids["Series"]}, [], []
    history_evidence = []
    for row in files:
        if row["path"].startswith(root + "/"):
            if any(row[key] is None for key in ("chapter_id", "volume_id", "series_id")):
                errors.append(f"unresolved Kavita file relationship: {row['path']}")
            else:
                series_files[str(row["series_id"])].append(library_path(row["path"], root))
    for table, rows in states.items():
        for state in rows:
            references = [state]
            if table == "AppUserReadingSession":
                references = [activity for activity in states["AppUserReadingSessionActivityData"]
                              if activity.get("AppUserReadingSessionId") == state.get("Id")]
            elif table == "AppUserReadingHistory":
                try:
                    history = json.loads(state["Data"])
                    if (not isinstance(history["SeriesIds"], list) or not isinstance(history["ChapterIds"], list)
                            or not isinstance(history["Activities"], list)
                            or any(not isinstance(value, dict) for value in history["Activities"])):
                        raise ValueError("incomplete reading-history references")
                    references = ([{"SeriesId": value} for value in history["SeriesIds"]]
                                  + [{"ChapterId": value} for value in history["ChapterIds"]]
                                  + history["Activities"])
                    # A removed chapter id still resolves through its own
                    # historical activity's current series/volume, never names.
                    references = [next((activity for activity in history["Activities"]
                                        if (ref.get("ChapterId") and activity.get("ChapterId") == ref["ChapterId"])
                                        or (ref.get("SeriesId") and activity.get("SeriesId") == ref["SeriesId"])), ref)
                                  for ref in references]
                except (KeyError, TypeError, ValueError):
                    errors.append(f"unresolved Kavita reading history: {state.get('Id')}")
                    references = []
            if not references:
                continue  # A session/history with no book reference cannot depend on a file.
            matches = []
            for ref in references:
                resolved = [row for row in files if
                            (ref.get("ChapterId") and ref["ChapterId"] == row["chapter_id"])
                            or (ref.get("SeriesId") and ref["SeriesId"] == row["series_id"])
                            or (ref.get("VolumeId") and ref["VolumeId"] == row["volume_id"])]
                if not resolved:
                    ids = {kind: ref.get(kind + "Id") for kind in current_ids if ref.get(kind + "Id")}
                    if (table == "AppUserReadingHistory" and ids
                            and all(value not in current_ids[kind] for kind, value in ids.items())
                            and not any(row["chapter_id"] == ref.get("ChapterId") for row in files)):
                        evidence = {"history_id": state["Id"], "ids": ids,
                                    "whole_row_sha256": metadata.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()),
                                    "classification": "disconnected legacy activity",
                                    "proof": "ids absent from full Series/Chapter/Volume; no MangaFile chapter reference"}
                        if evidence not in history_evidence:
                            history_evidence.append(evidence)
                    else:
                        errors.append(f"unresolved Kavita state dependency: {table}/{state.get('Id')}")
                matches += resolved
            for row in matches:
                if row["path"].startswith(root + "/"):
                    protected.append({"path": library_path(row["path"], root),
                                      "reason": f"{table}/{state.get('Id')} saved state"})
    return series_files, protected, errors, {table: len(rows) for table, rows in states.items()}, history_evidence


def prepare(library, ll, app, series_files, kavita_protected, kavita_errors, kavita_proof,
            census, attestations):
    root, blockers = library["ebook_root"], []
    pointers, ll_by_id = [], {}
    for book in ll["llBooks"]:
        if str(book["BookID"]) in ll_by_id:
            blockers.append(f"duplicate LL BookID: {book['BookID']}")
        ll_by_id[str(book["BookID"])] = book
        if book.get("BookFile"):
            try:
                pointers.append({"book_id": str(book["BookID"]), "path": library_path(book["BookFile"], root)})
            except metadata.Refused as err:
                blockers.append(f"LL {book['BookID']}: {err}")
    pointer_by_id = {entry["book_id"]: entry["path"] for entry in pointers}
    items = {str(item["id"]): item for item in app["app"]["items"]}
    app_protected, app_errors, historical_refs = [], [], []
    for want in app["app"]["wants"]:
        ll_id = str(want.get("ll_book_id") or "")
        if ll_id in pointer_by_id:
            app_protected.append({"path": pointer_by_id[ll_id], "reason": f"app want {want['id']} LL {ll_id}"})
        elif ll_id and ll_id not in ll_by_id:
            historical_refs.append(f"want {want['id']} LL book {ll_id} is absent from the full current LL table")
        for field in ("matched_books_item_id", "pairing_books_item_id"):
            if not want.get(field):
                continue
            item = items.get(str(want[field]))
            if item is None:
                app_errors.append(f"want {want['id']} has unresolved {field}")
                continue
            if item.get("source") != "kavita":
                continue
            key = str(item["external_id"])
            paths = series_files.get(key, [])
            if key not in series_files:
                if item.get("deleted_at") is not None:
                    historical_refs.append(f"want {want['id']} deleted Kavita anchor {key} is absent from the full current file map")
                else:
                    app_errors.append(f"want {want['id']} live Kavita anchor {key} has no file map")
            app_protected += [{"path": path, "reason": f"app want {want['id']} {field} {item['id']}"} for path in paths]
    snapshot = {"schema": 1, "created_at": now(), "ebook_root": root,
                "library_capture_started_at": library.get("started_at"),
                "files": [{"path": row["path"], "sha256": row["sha256"]} for row in library["files"]],
                "lazylibrarian": {"complete": not blockers, "capture_started_at": ll.get("captureStartedAt"),
                                  "checked_at": ll["capturedAt"], "pointers": pointers},
                "census": {"complete": census.get("complete") is True, "checked_at": census.get("checked_at"),
                           "capture_started_at": census.get("capture_started_at"),
                           "protected_paths": census.get("protected_paths", [])},
                "kavita": {"complete": not kavita_errors, "checked_at": kavita_proof["capturedAt"],
                           "capture_started_at": kavita_proof.get("captureStartedAt"),
                           "protected_paths": kavita_protected},
                "app_wants": {"complete": app.get("read_only") == "on" and app.get("scope") == "full" and not app_errors,
                              "capture_started_at": app.get("capture_started_at"),
                              "checked_at": app.get("completed_at", app["captured_at"]), "protected_paths": app_protected}}
    if library.get("complete") is not True:
        blockers.append("library census is incomplete")
    blockers += kavita_errors + app_errors
    for source in copies.SOURCES:
        section = snapshot[source]
        attestation = attestations.get(source, {})
        section["quiesced"] = attestation.get("quiesced") is True
        section["quiescence_proof"] = attestation.get("proof")
        section["quiescence_checked_at"] = attestation.get("checked_at")
        section["quiescence_established_at"] = attestation.get("established_at")
        try:
            start = copies.timestamp_epoch(section["capture_started_at"])
            finish = copies.timestamp_epoch(section["checked_at"])
            if start > finish:
                raise metadata.Refused("source completion precedes capture start")
            copies.fresh(section["capture_started_at"])
        except metadata.Refused as err:
            section["complete"] = False
            blockers.append(f"{source}: {err}")
        if section["quiesced"] and (not isinstance(section["quiescence_proof"], str) or not section["quiescence_proof"].strip()):
            section["quiesced"] = False
        if section["quiesced"]:
            try:
                copies.fresh(attestation.get("checked_at"))
                established = copies.timestamp_epoch(attestation.get("established_at"))
                if established > min(copies.timestamp_epoch(section["capture_started_at"]),
                                     copies.timestamp_epoch(library.get("started_at")),
                                     copies.timestamp_epoch(attestation["checked_at"])):
                    raise metadata.Refused("writer fence was established after a capture")
            except metadata.Refused as err:
                section["quiesced"] = False
                blockers.append(f"{source}: {err}")
        if not section["quiesced"]:
            blockers.append(f"{source} writer quiescence is unproven")
        if not section["complete"]:
            blockers.append(f"{source} dependency read is incomplete")
        try:
            copies.fresh(section["checked_at"])
        except metadata.Refused as err:
            blockers.append(f"{source}: {err}")
    try:
        copies.fresh(library["checked_at"])
    except metadata.Refused as err:
        blockers.append(f"library: {err}")
    by_path = collections.defaultdict(list)
    for entry in pointers:
        by_path[entry["path"]].append(entry["book_id"])
    protections = []
    for source in copies.SOURCES[1:]:
        for row in snapshot[source]["protected_paths"]:
            try:
                path = copies.relative_path(row["path"])
                if not isinstance(row["reason"], str) or not row["reason"].strip():
                    raise metadata.Refused("path protection needs a nonempty dependency reason")
                protections.append((path, f"{source}: {row['reason']}"))
            except (KeyError, metadata.Refused) as err:
                blockers.append(f"{source}: invalid protection: {err}")
    groups, ambiguous = collections.defaultdict(list), []
    identities = [{**row, "title_keys": set(row["title_keys"]), "author_keys": set(row["author_keys"])}
                  for row in library["files"]]
    for row in identities:
        conflicts = metadata.author_identity_conflicts(row, identities)
        if row.get("author_refusal") or len(row["title_keys"]) != 1 or len(row["author_keys"]) != 1 or conflicts:
            ambiguous.append({"path": row["path"], "reason": row.get("author_refusal") or "ambiguous title/author identity",
                              "shared_alias_conflicts": conflicts})
            continue
        groups[(next(iter(row["title_keys"])), next(iter(row["author_keys"])))].append(row)
    report_groups = []
    for (title, author), rows in groups.items():
        if len(rows) < 2:
            continue
        keepers = [row["path"] for row in rows if row["path"] in by_path]
        valid_keeper = len(keepers) == 1 and len(by_path[keepers[0]]) == 1
        details = []
        for row in rows:
            reasons = [reason for prefix, reason in protections if row["path"] == prefix or row["path"].startswith(prefix + "/")]
            reasons += row.get("ignore_reasons", [])
            if not valid_keeper:
                reasons.append("no unique one-owner LL BookFile keeper")
            if blockers:
                reasons.append("operational snapshot blocked; copy stays in place")
            details.append({"path": row["path"], "keeper": valid_keeper and row["path"] == keepers[0],
                            "ll_book_ids": by_path[row["path"]], "protected_reasons": sorted(set(reasons))})
        report_groups.append({"title_key": title, "author_key": author, "keeper": keepers[0] if valid_keeper else None,
                              "copies": details})
    return snapshot, {"captured_at": now(), "production_writes": 0, "apply_ready": not blockers,
                      "blockers": sorted(set(blockers)), "epub_count": len(library["files"]),
                      "configured_hold_folders": library.get("configured_hold_folders", []),
                      "historical_app_refs": historical_refs,
                      "same_author_groups": len(report_groups), "ambiguous_files": ambiguous,
                      "groups": report_groups}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect")
    collect.add_argument("root")
    collect.add_argument("--budget", type=int, default=240)
    build = sub.add_parser("prepare")
    for name in ("library", "ll_sql", "app_audit", "kavita_db", "kavita_proof", "census_protections", "output"):
        build.add_argument("--" + name.replace("_", "-"), required=True)
    build.add_argument("--writer-attestations")
    args = parser.parse_args()
    if args.command == "collect":
        result = collect_library(args.root, args.budget)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["complete"] else 1
    library, ll, app, proof, census = (load(args.library), ll_capture(args.ll_sql), load(args.app_audit),
                                     load(args.kavita_proof), load(args.census_protections))
    series, protected, errors, state_counts, history_evidence = kavita_dependencies(args.kavita_db, proof, library["ebook_root"])
    snapshot, report = prepare(library, ll, app, series, protected, errors, proof, census,
                               load(args.writer_attestations) if args.writer_attestations else {})
    snapshot["kavita"]["disconnected_history"] = history_evidence
    output = Path(args.output).resolve()
    if os.path.commonpath((str(output), library["ebook_root"])) == library["ebook_root"]:
        raise metadata.Refused("private snapshot output must be outside EBooks")
    output.mkdir(parents=True, exist_ok=False)
    (output / "snapshot.json").write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n")
    (output / "report.json").write_text(json.dumps({**report, "kavita_state_counts": state_counts,
                                                 "disconnected_history": history_evidence}, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"output": str(output), "apply_ready": report["apply_ready"],
                      "epub_count": report["epub_count"], "same_author_groups": report["same_author_groups"],
                      "ambiguous_files": len(report["ambiguous_files"]), "blockers": len(report["blockers"]),
                      "historical_app_refs": len(report["historical_app_refs"]),
                      "kavita_state_counts": state_counts, "production_writes": 0}, indent=2))
    return 0 if report["apply_ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
