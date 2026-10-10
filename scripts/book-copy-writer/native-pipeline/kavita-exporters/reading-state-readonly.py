#!/usr/bin/env python3
"""Export/compare all Kavita reading-state fields from existing read-only DB copies."""
import argparse
import datetime
import hashlib
import json
import sqlite3
from pathlib import Path
import os
import stat


def write_private_text(path, text):
    """Publish one fresh private report; never repair or overwrite prior evidence."""
    path = Path(path).absolute()
    if not path.name or '..' in path.parts:
        raise ValueError('fresh output path without parent traversal required')

    def open_parent(create):
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for component in path.parts[1:-1]:
                try:
                    child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=directory)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(component, 0o700, dir_fd=directory)
                    child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=directory)
                os.close(directory)
                directory = child
            return directory
        except BaseException:
            os.close(directory)
            raise

    directory = open_parent(True)
    try:
        parent = os.fstat(directory)
        if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) not in (0o700, 0o2700):
            raise ValueError('output parent must be owned and mode 0700')
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as output:
            before = os.fstat(output.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                    or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1):
                raise ValueError('fresh output must be owned single-link mode 0600')
            raw = text.encode('utf-8')
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
            after = os.fstat(output.fileno())
            current = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink')
            if (after.st_size != len(raw) or any(getattr(before, k) != getattr(after, k)
                    or getattr(after, k) != getattr(current, k) for k in fields)
                    or any(getattr(after, k) != getattr(current, k)
                           for k in ('st_size', 'st_mtime_ns', 'st_ctime_ns'))):
                raise ValueError('private output identity changed while publishing')
        check = open_parent(False)
        try:
            current_parent = os.fstat(check)
            if any(getattr(parent, k) != getattr(current_parent, k)
                   for k in ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid')):
                raise ValueError('private output parent changed while publishing')
        finally:
            os.close(check)
        os.fsync(directory)
    finally:
        os.close(directory)

TABLES = ['AppUserProgresses', 'AppUserBookmark', 'AppUserAnnotation',
          'AppUserReadingSession', 'AppUserReadingSessionActivityData']


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def export(args):
    db_path = Path(args.db).resolve()
    con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    con.execute('BEGIN')
    tables = {table: [dict(row) for row in con.execute(f'SELECT * FROM "{table}" ORDER BY Id')]
              for table in TABLES}
    con.rollback()
    con.close()
    report = {'capturedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'sourceDb': str(db_path), 'readOnly': True, 'tables': tables,
              'tableHashes': {table: digest(rows) for table, rows in tables.items()},
              'tableCounts': {table: len(rows) for table, rows in tables.items()}}
    write_private_text(args.out, json.dumps(report, indent=2)+'\n')
    return {'out': args.out, 'tableCounts': report['tableCounts'], 'tableHashes': report['tableHashes']}, 0


def compare(args):
    before = json.loads(Path(args.before).read_text())
    after = json.loads(Path(args.after).read_text())
    comparisons = {}
    for table in TABLES:
        if table not in before['tables'] or table not in after['tables']:
            raise ValueError(f'incomplete state export: {table}')
        left = before['tables'][table]
        right = after['tables'][table]
        old = {row['Id']: row for row in left}
        new = {row['Id']: row for row in right}
        if len(old) != len(left) or len(new) != len(right):
            raise ValueError('duplicate row identities')
        changed = []
        for row_id in sorted(old.keys() & new.keys()):
            if old[row_id] != new[row_id]:
                changed.append({'Id': row_id, 'fields': {key: {'before': old[row_id].get(key),
                                                              'after': new[row_id].get(key)}
                    for key in sorted(old[row_id].keys() | new[row_id].keys())
                    if old[row_id].get(key) != new[row_id].get(key)}})
        comparisons[table] = {'beforeCount': len(left), 'afterCount': len(right),
                              'exactlyEqual': left == right, 'beforeSha256': digest(left),
                              'afterSha256': digest(right), 'changedRows': changed,
                              'removedRows': [old[key] for key in sorted(old.keys()-new.keys())],
                              'addedRows': [new[key] for key in sorted(new.keys()-old.keys())]}
    same = all(row['exactlyEqual'] for row in comparisons.values())
    report = {'readOnly': True, 'before': args.before, 'after': args.after,
              'exactlyEqual': same, 'tables': comparisons}
    if args.out:
        write_private_text(args.out, json.dumps(report, indent=2)+'\n')
    summary = {'exactlyEqual': same, 'tables': {table: {key: row[key] for key in
               ['beforeCount', 'afterCount', 'exactlyEqual']} for table, row in comparisons.items()},
               'out': args.out}
    return summary, 0 if same else 1


parser = argparse.ArgumentParser(description=__doc__)
sub = parser.add_subparsers(dest='command', required=True)
exp = sub.add_parser('export'); exp.add_argument('--db', required=True); exp.add_argument('--out', required=True); exp.set_defaults(run=export)
cmp = sub.add_parser('compare'); cmp.add_argument('before'); cmp.add_argument('after'); cmp.add_argument('--out'); cmp.set_defaults(run=compare)
args = parser.parse_args()
summary, code = args.run(args)
print(json.dumps(summary, indent=2))
raise SystemExit(code)
