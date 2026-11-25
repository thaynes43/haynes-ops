"""Collect a comparable, read-only database and store snapshot inside studio-api."""

import json
import os
import stat
import sys
from hashlib import file_digest, sha256
from pathlib import Path

import psycopg


IMPORTS = {
    "run_01M7ZHNZJ2PWA8XR8K7KKWP17V": (
        "prd_01M7ZGYNHFET1GKR4304FVD4PE", "sty_01M7ZGYNHFET1GKR4304FVD4PF",
        "dlv_01M7ZHP1QS82QGK196ZPCM7MQS",
    ),
    "run_01M7C5CJA7XKBCRPM0ZRP2P5RS": (
        "prd_01M79V9FC00000000000000001", "sty_01M79V9FC00000000000000001",
        "dlv_01M7C5D3KFX3MFVWCXBCVJHMG7",
    ),
}
WORKERS = {
    "studio-worker": "run", "studio-image-worker": "image",
    "studio-voice-worker": "voice", "studio-matte-worker": "matte",
}


def digest(data):
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(payload).hexdigest()


def fail_walk(error):
    raise error


def store_snapshot(root):
    """Include every stored file and its modification time, except the API's readiness scratch."""
    if not root.is_dir():
        raise ValueError("missing store")
    contents = []
    times = []
    total = 0
    for directory, subdirs, names in os.walk(root, onerror=fail_walk):
        parent = Path(directory)
        if parent == root:
            subdirs[:] = [name for name in subdirs if name != ".probe"]
        if any((parent / name).is_symlink() for name in subdirs):
            raise ValueError("unexpected store directory symlink")
        for name in names:
            path = parent / name
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("unexpected non-regular store file")
            with path.open("rb") as source:
                checksum = file_digest(source, "sha256").hexdigest()
            after = path.lstat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                    after.st_size, after.st_mtime_ns, after.st_ino):
                raise ValueError("store changed during probe")
            relative = str(path.relative_to(root))
            total += after.st_size
            contents.append((relative, after.st_size, checksum))
            times.append((relative, after.st_mtime_ns))
    if not contents:
        raise ValueError("empty store")
    return {"files": len(contents), "bytes": total, "sha256": digest(sorted(contents)),
            "mtime_sha256": digest(sorted(times))}


def database_snapshot(conn, phase, results):
    identity = conn.execute(
        "SELECT current_user, current_database(), ssl, current_setting('transaction_read_only') "
        "FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
    ).fetchone()
    results["runtime identity, TLS and read-only transaction"] = identity == ("studio_app", "studio", True, "on")
    active_runs = conn.execute("""
        SELECT document->>'status', count(*) FROM studio.runs
        WHERE document->>'status' IN ('queued', 'running', 'held') GROUP BY 1
    """).fetchall()
    active_media = conn.execute("""
        SELECT lane, document->>'status', count(*) FROM studio.media_jobs
        WHERE document->>'status' IN ('queued', 'running', 'held') GROUP BY 1, 2
    """).fetchall()
    media_lanes = {row[0] for row in conn.execute("SELECT lane FROM studio.media_job_lanes")}
    rollback = phase in {"rollback-baseline", "compare"}
    predecessor = phase in {"preflight", "retry-preflight"}
    expected_lanes = {"voice", "image"}
    if not predecessor:
        expected_lanes.add("matte")
    matte_paused = False
    if rollback:
        matte_paused = conn.execute("""
            SELECT paused FROM studio.queue_lane_events
            WHERE lane = 'matte' ORDER BY sequence DESC LIMIT 1
        """).fetchone() == (True,)
    blocked_media = [
        row for row in active_media
        if not (matte_paused and row[0] == "matte" and row[1] == "queued")
    ]
    results[f"required lanes idle ({', '.join(sorted(media_lanes))})"] = (
        not active_runs and not blocked_media and media_lanes == expected_lanes
    )
    print(f"Active or held runs: {active_runs}. Active or held media jobs: {active_media}.", file=sys.stderr)

    if phase == "postflight":
        grants = dict(conn.execute("""
            SELECT worker_id, lane FROM studio.worker_lane_grants WHERE worker_id = ANY(%s)
        """, (list(WORKERS),)).fetchall())
        results["all four worker lane grants"] = grants == WORKERS
        checks = conn.execute("""
            SELECT has_table_privilege('studio.worker_lane_grants', 'SELECT'),
                   has_any_column_privilege('studio.worker_lane_grants', 'INSERT'),
                   has_any_column_privilege('studio.worker_lane_grants', 'UPDATE'),
                   has_table_privilege('studio.worker_lane_grants', 'DELETE'),
                   has_table_privilege('studio.worker_lane_grants', 'TRUNCATE')
        """).fetchone()
        results["runtime lane grants are read-only"] = checks == (True, False, False, False, False)

    report = {}
    for name, query in (
        ("runs", "SELECT id, production_id, story_id, document, snapshot FROM studio.runs ORDER BY id"),
        ("deliveries", "SELECT id, run_id, document FROM studio.deliveries ORDER BY id"),
        ("delivery media", "SELECT run_id, locator, sha256, size FROM studio.delivery_media ORDER BY run_id, locator"),
        ("media jobs", "SELECT id, lane, fence, document FROM studio.media_jobs ORDER BY id"),
    ):
        rows = conn.execute(query).fetchall()
        report[name] = {"rows": len(rows), "sha256": digest(rows)}

    if rollback:
        checks = (
            ("matte jobs", "SELECT to_jsonb(j) FROM studio.media_jobs AS j WHERE lane = 'matte' ORDER BY id"),
            ("matte pause events", "SELECT to_jsonb(e) FROM studio.queue_lane_events AS e "
                                 "WHERE lane = 'matte' ORDER BY sequence"),
            ("matte grants", "SELECT worker_id FROM studio.worker_lane_grants WHERE lane = 'matte' ORDER BY worker_id"),
        )
        for name, query in checks:
            rows = conn.execute(query).fetchall()
            report[name] = {"rows": len(rows), "sha256": digest(rows)}
            print(name, len(rows), file=sys.stderr)
        results["0013 release column visible"] = conn.execute("""
            SELECT EXISTS (SELECT FROM information_schema.columns
                WHERE table_schema = 'studio' AND table_name = 'media_jobs' AND column_name = 'matte_releases')
        """).fetchone() == (True,)

    for run, (production, story, delivery) in IMPORTS.items():
        counts = conn.execute("""
            SELECT (SELECT count(*) FROM studio.productions WHERE id = %s),
                   (SELECT count(*) FROM studio.stories WHERE id = %s AND production_id = %s),
                   (SELECT count(*) FROM studio.runs WHERE id = %s AND story_id = %s AND production_id = %s),
                   (SELECT count(*) FROM studio.deliveries WHERE id = %s AND run_id = %s),
                   (SELECT count(*) FROM studio.delivery_media WHERE run_id = %s)
        """, (production, story, production, run, story, production, delivery, run, run)).fetchone()
        results[f"archived run, delivery and index {run}"] = counts[:4] == (1, 1, 1, 1) and counts[4] >= 2
    return report


def main():
    phase, expected_build = sys.argv[1:]
    results = {"installed Studio revision": os.environ["STUDIO_BUILD_REVISION"] == f"git:{expected_build}"}
    report = {}
    try:
        with psycopg.connect(os.environ["STUDIO_DATABASE_URL"], connect_timeout=5,
                             options="-c statement_timeout=30000") as conn:
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            conn.read_only = True
            report.update(database_snapshot(conn, phase, results))
    except (psycopg.Error, ValueError, KeyError) as error:
        results[f"database snapshot ({type(error).__name__})"] = False

    try:
        store = store_snapshot(Path(os.environ["STUDIO_STORE_ROOT"]))
        results["store file and byte totals and SHA-256"] = True
        report["store"] = store
    except (OSError, ValueError, KeyError) as error:
        results[f"store snapshot ({type(error).__name__})"] = False

    for check, passed in results.items():
        print("PASS" if passed else "FAIL", check, file=sys.stderr)
    if all(results.values()):
        print(json.dumps(report, sort_keys=True))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
