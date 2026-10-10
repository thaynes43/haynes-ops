#!/usr/bin/env python3
"""Export exact saved metadata locks and their current physical mappings from a copied Kavita DB."""
import argparse
import datetime
import hashlib
import json
import pathlib
import sqlite3
import os
import stat
from pathlib import Path


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

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--db', type=pathlib.Path, required=True)
p.add_argument('--approval', type=pathlib.Path, required=True)
p.add_argument('--out', type=pathlib.Path, required=True)
a = p.parse_args()
approval = json.loads(a.approval.read_text())
targets = {x['path'] for x in approval['plan']['targets']}
copy_proof = a.db.parent / 'copy-proof.json'
copied = json.loads(copy_proof.read_text())
assert copied['readOnlySource'] and copied['before'] == copied['after'] and copied['captureStartedAt'] <= copied['capturedAt']
c = sqlite3.connect('file:' + str(a.db) + '?mode=ro', uri=True)
c.execute('PRAGMA query_only=ON')
c.row_factory = sqlite3.Row
paths = [dict(x) for x in c.execute('SELECT f.Id FileId,f.ChapterId,ch.VolumeId,v.SeriesId,f.FilePath FROM MangaFile f JOIN Chapter ch ON ch.Id=f.ChapterId JOIN Volume v ON v.Id=ch.VolumeId')]
proof = {'readOnly': True, 'capturedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
         'sourceDb': str(a.db), 'sourceCaptureStartedAt': copied['captureStartedAt'], 'sourceCapturedAt': copied['capturedAt'],
         'sourceCopyProofSha256': hashlib.sha256(copy_proof.read_bytes()).hexdigest(),
         'approvalPlanSha256': approval['approval_sha256'], 'tables': {},
         'nonScalarLockValueColumns': [], 'unresolvedLockedRows': [], 'targetOverlaps': [], 'unknownLockedTables': []}
known_tables = ['Chapter', 'Series', 'SeriesMetadata', 'Volume', 'CollectionTag', 'ReadingList', 'AppUserCollection', 'Person']
schema_tables = [row['name'] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
for table_row in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
    table_name = table_row['name']
    quoted = '"' + table_name.replace('"', '""') + '"'
    lock_columns = [x[1] for x in c.execute('PRAGMA table_info(' + quoted + ')') if x[1].endswith('Locked')]
    if lock_columns and table_name not in known_tables:
        proof['unknownLockedTables'].append({'table': table_name, 'lockColumns': lock_columns})
for table in known_tables:
    columns = [x[1] for x in c.execute('PRAGMA table_info(' + table + ')')]
    locks = [x for x in columns if x.endswith('Locked')]
    rows = []
    for raw in c.execute('SELECT * FROM ' + table):
        row = dict(raw)
        active = [key for key in locks if row[key]]
        if not active:
            continue
        if table == 'Chapter':
            mapped = [x for x in paths if x['ChapterId'] == row['Id']]
        elif table == 'Volume':
            mapped = [x for x in paths if x['VolumeId'] == row['Id']]
        elif table in ['Series', 'SeriesMetadata']:
            series_id = row['Id'] if table == 'Series' else row['SeriesId']
            mapped = [x for x in paths if x['SeriesId'] == series_id]
        elif table == 'ReadingList':
            ids = {x[0] for x in c.execute('SELECT ChapterId FROM ReadingListItem WHERE ReadingListId=?', (row['Id'],))}
            mapped = [x for x in paths if x['ChapterId'] in ids]
        elif table == 'AppUserCollection':
            ids = {x[0] for x in c.execute('SELECT ItemsId FROM AppUserCollectionSeries WHERE CollectionsId=?', (row['Id'],))}
            mapped = [x for x in paths if x['SeriesId'] in ids]
        elif table == 'CollectionTag':
            ids = {x[0] for x in c.execute('SELECT s.SeriesId FROM CollectionTagSeriesMetadata l JOIN SeriesMetadata s ON s.Id=l.SeriesMetadatasId WHERE l.CollectionTagsId=?', (row['Id'],))}
            mapped = [x for x in paths if x['SeriesId'] in ids]
        else:
            # Person's shared portrait protects every declared current book-role FK.
            # No work/author equivalence is inferred from an alias or display name.
            mapped = []
            for related in schema_tables:
                quoted = '"' + related.replace('"', '""') + '"'
                fks = [dict(x) for x in c.execute('PRAGMA foreign_key_list(' + quoted + ')')]
                person_fks = [x for x in fks if x['table'] == 'Person']
                book_fks = [x for x in fks if x['table'] in ['Chapter', 'Volume', 'Series', 'SeriesMetadata']]
                for fk in person_fks:
                    column = '"' + fk['from'].replace('"', '""') + '"'
                    for linked in c.execute('SELECT * FROM ' + quoted + ' WHERE ' + column + '=?', (row['Id'],)):
                        for book_fk in book_fks:
                            value = linked[book_fk['from']]
                            if value is None:
                                continue
                            if book_fk['table'] == 'SeriesMetadata':
                                found = c.execute('SELECT SeriesId FROM SeriesMetadata WHERE Id=?', (value,)).fetchone()
                                if found is None:
                                    proof['unresolvedLockedRows'].append({'table': table, 'id': row['Id'], 'relatedTable': related, 'reference': value})
                                    continue
                                field, value = 'SeriesId', found[0]
                            else:
                                field = {'Chapter': 'ChapterId', 'Volume': 'VolumeId', 'Series': 'SeriesId'}[book_fk['table']]
                            matched = [x for x in paths if x[field] == value]
                            if not matched:
                                proof['unresolvedLockedRows'].append({'table': table, 'id': row['Id'], 'relatedTable': related, 'reference': value})
                            mapped.extend(matched)
        values = {}
        for lock in active:
            field = lock[:-6]
            if field in row:
                values[field] = row[field]
            else:
                proof['nonScalarLockValueColumns'].append({'table': table, 'id': row['Id'], 'lock': lock, 'expectedField': field})
        rows.append({'row': row, 'activeLocks': active, 'savedLockedValues': values, 'mappedFiles': mapped})
        if not mapped:
            proof['unresolvedLockedRows'].append({'table': table, 'id': row['Id'], 'activeLocks': active})
        overlaps = [x['FilePath'] for x in mapped if x['FilePath'].removeprefix('/data/cephfs-hdd/data/media/books/EBooks/') in targets]
        if overlaps:
            proof['targetOverlaps'].append({'table': table, 'id': row['Id'], 'paths': overlaps, 'activeLocks': active})
    proof['tables'][table] = {'lockColumns': locks, 'rows': rows}
c.close()
write_private_text(a.out, json.dumps(proof, indent=2) + '\n')
print(json.dumps({'proof': str(a.out), 'lockedRowCounts': {t: len(v['rows']) for t, v in proof['tables'].items()},
                  'targetOverlaps': proof['targetOverlaps'], 'nonScalarLockValueColumns': proof['nonScalarLockValueColumns'],
                  'unresolvedLockedRows': proof['unresolvedLockedRows'], 'unknownLockedTables': proof['unknownLockedTables']}))
raise SystemExit(2 if proof['unknownLockedTables'] or proof['unresolvedLockedRows'] or proof['nonScalarLockValueColumns'] else 0)
