"""Stat-only MAIN mount proof before MAIN transaction admission.
No PG, held-fence authority, content reads, links or permission changes.
"""
import datetime as dt
import hashlib
import json
import math
import os
import re
from pathlib import Path
import signal
import stat
import sys
import time

ROOT = '/data/cephfs-hdd/data/media/books/EBooks'
STATE = '/data/cephfs-hdd/data/media/books/.epub-convert'
PROOFS = '/tmp/copy-proofs'
MAX_CENSUS_FILES = 10000
MAX_OUTPUT_BYTES = 16 * 1024 * 1024 - 1
PINS = {
    'epub_copies.py': 'b79389c89bd5a57b4737ad693d2c209bc513e818343064ec04c8f0fcfb24a7da',
    'epub_metadata.py': 'ce3c5a271cb4c94d91f3154240b4cfbc5e0cc57981969fc5c975a0c0f50eb773',
    'book_copy_writer.py': 'fbfec65f4af933da6ec9aca90536501e4514cebfb8e378584e3085a993493880',
    'bound_census.py': 'd3df953dc7105d9d2535b0d370bb53c266ab46f018beac72652f681d91d52c38',
    'proof_transport.py': '096ba710805623638b87d8c31f6be2653fe57ae0377bf35a27dabf014755e8a5',
}


class Refused(RuntimeError):
    pass


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def identity(info):
    return [str(n) for n in (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_nlink, info.st_mode, info.st_uid, info.st_gid)]


def stat_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,19})', value) or int(value) > 2**64 - 1:
        raise Refused('main_identity: canonical decimal-string stat required')
    return int(value)


def fingerprints(root, deadline, copies):
    return {path: [[str(n) for n in value[0]], *(str(n) for n in value[1:])]
            for path, value in copies.file_fingerprints(root, deadline, max_files=MAX_CENSUS_FILES).items()}


def exact(left, right):
    return json.dumps(left, sort_keys=True, separators=(',', ':')) == json.dumps(right, sort_keys=True, separators=(',', ':'))


def bounded_json(value, cap=MAX_OUTPUT_BYTES):
    result = bytearray()
    for chunk in json.JSONEncoder(ensure_ascii=True, separators=(',', ':')).iterencode(value):
        raw = chunk.encode()
        if len(raw) > cap - len(result):
            raise Refused('main_identity: complete stat output exceeds byte cap')
        result.extend(raw)
    return bytes(result)


def bounded_census(value):
    if not isinstance(value, dict) or not value or len(value) > MAX_CENSUS_FILES:
        raise Refused('main_identity: complete census exceeds bounded file scope')
    return value


def relative(value, selected=False):
    if (not isinstance(value, str) or not value or '\\' in value or any(ord(c) < 32 for c in value)
            or any(part in ('', '.', '..') or (selected and part.startswith('.')) for part in value.split('/'))):
        raise Refused('main_identity: invalid census or selected relative path')
    return value


def directory_proof(path, metadata):
    with metadata.safe_directory(path) as descriptor:
        before = os.fstat(descriptor)
        proof = {'path': path, 'identity': identity(before), 'uid': str(before.st_uid),
                 'gid': str(before.st_gid), 'mode': str(before.st_mode),
                 'writer_wx': os.access(path, os.W_OK | os.X_OK, effective_ids=True, follow_symlinks=False),
                 'reader_rx': os.access(path, os.R_OK | os.X_OK, effective_ids=True, follow_symlinks=False)}
        metadata._same_directory(descriptor, path)
        if identity(before) != identity(os.fstat(descriptor)):
            raise Refused('main_identity: directory changed during access check')
        return proof


def collect(root, state, proofs, deadline, copies, metadata, selected_paths):
    started = stamp()
    empty_identity = None

    def waiting():
        nonlocal empty_identity
        if time.monotonic() >= deadline:
            raise Refused('main_identity: deadline expired')
        with metadata.safe_directory(proofs) as descriptor:
            info = os.fstat(descriptor)
            current = identity(info)
            if (os.listdir(descriptor) or stat.S_IMODE(info.st_mode) not in (0o700, 0o2700)
                    or (info.st_uid, info.st_gid) != (1000, 1000)
                    or (empty_identity is not None and empty_identity != current)):
                raise Refused('main_identity: MAIN is not still waiting in the same empty private proof directory')
            metadata._same_directory(descriptor, proofs)
            empty_identity = current

    waiting()
    before = bounded_census(fingerprints(root, deadline, copies))
    if (not isinstance(selected_paths, list) or not selected_paths
            or len(selected_paths) != len(set(selected_paths)) or len(selected_paths) > 2048):
        raise Refused('main_identity: bounded exact selected source/keeper paths required')
    for value in selected_paths:
        relative(value, selected=True)
        if value not in before:
            raise Refused('main_identity: selected source or keeper missing')
    parents, access = {}, {}
    for value in before:
        relative(value)
    for value in selected_paths:
        fingerprint = before[value]
        parent, name = os.path.split(os.path.join(root, value))
        if parent not in parents:
            waiting()
            parents[parent] = directory_proof(parent, metadata)
        if stat.S_ISREG(stat_value(fingerprint[1])):
            with metadata.safe_directory(parent) as descriptor:
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if not exact([[str(n) for n in metadata._identity(info)], str(info.st_mode), str(info.st_uid), str(info.st_gid)], fingerprint):
                    raise Refused('main_identity: file changed before kernel access check')
                access[value] = {'reader_r': os.access(name, os.R_OK, dir_fd=descriptor,
                                                     effective_ids=True, follow_symlinks=False)}
                after = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                metadata._same_directory(descriptor, parent)
                if identity(info) != identity(after):
                    raise Refused('main_identity: file changed during kernel access check')
    state_proof = directory_proof(state, metadata)
    backup = os.path.join(state, 'copies')
    try:
        backup_proof = directory_proof(backup, metadata)
        backup_proof['present'] = True
    except FileNotFoundError:
        backup_proof = {'path': backup, 'present': False, 'creation_parent': state_proof}
    after = bounded_census(fingerprints(root, deadline, copies))
    for path, previous in parents.items():
        if not exact(previous, directory_proof(path, metadata)):
            raise Refused('main_identity: library parent changed during complete census')
    if not exact(state_proof, directory_proof(state, metadata)):
        raise Refused('main_identity: state parent changed')
    if backup_proof['present']:
        current = directory_proof(backup, metadata)
        current['present'] = True
        if not exact(current, backup_proof):
            raise Refused('main_identity: backup parent changed')
    elif os.path.lexists(backup):
        raise Refused('main_identity: absent backup directory appeared')
    waiting()
    if not exact(before, after):
        raise Refused('main_identity: complete stat census changed')
    return {'schema': 2, 'complete': True, 'read_only': True, 'production_writes': 0,
            'capture_started_at': started, 'captured_at': stamp(), 'ebook_root': root,
            'actual_uid': os.getuid(), 'actual_gid': os.getgid(), 'actual_euid': os.geteuid(),
            'actual_egid': os.getegid(), 'groups': os.getgroups(),
            'all_file_fingerprints': before, 'parents': parents, 'file_access': access,
            'state': state_proof, 'backup_parent': backup_proof,
            'proof_directory_identity': empty_identity,
            'protected_hardlinks': Path('/proc/sys/fs/protected_hardlinks').read_text().strip()}


def compare(source, actual, selection, root=ROOT):
    if (source.get('schema') != 2 or source.get('kind') != 'validated_byte_census' or source.get('complete') is not True
            or source.get('ebook_root') != root or source.get('errors') not in (None, [])
            or source.get('production_writes') != 0 or actual.get('schema') != 2
            or actual.get('complete') is not True or actual.get('read_only') is not True
            or actual.get('production_writes') != 0 or actual.get('ebook_root') != root
            or any(actual.get(k) != 1000 for k in ('actual_uid', 'actual_gid', 'actual_euid', 'actual_egid'))
            or actual.get('protected_hardlinks') not in ('0', '1')):
        raise Refused('main_identity: complete source, actual UID/GID or kernel policy unknown')
    fingerprints = source.get('all_file_fingerprints')
    if not isinstance(fingerprints, dict) or not fingerprints:
        raise Refused('main_identity: complete source fingerprints missing')
    for path, record in fingerprints.items():
        relative(path)
        if (not isinstance(record, list) or len(record) != 4 or not isinstance(record[0], list)
                or len(record[0]) != 6
                or any(stat_value(v) < 0 for v in record[0] + record[1:])
                or stat_value(record[0][5]) < 1):
            raise Refused('main_identity: malformed complete fingerprint')
    # No device/inode, unrelated file, timestamp, mode or owner normalization.
    if not exact(fingerprints, actual.get('all_file_fingerprints')):
        raise Refused('main_identity: source and MAIN complete device/inode fingerprints differ')
    if (selection.get('schema') != 1 or selection.get('kind') != 'copy_selection'
            or selection.get('approved_for_retention') is not True
            or not isinstance(selection.get('entries'), list) or not selection['entries']):
        raise Refused('main_identity: real approved selection schema required')
    state, backup = actual['state'], actual['backup_parent']
    for parent in (state, backup if backup['present'] else backup['creation_parent']):
        if parent['uid'] != '1000' or parent['writer_wx'] is not True or parent['reader_rx'] is not True:
            raise Refused('main_identity: actual backup parent ownership/access inadequate')
    if backup['present'] and backup['identity'][0] != state['identity'][0]:
        raise Refused('main_identity: backup and state filesystem differ')
    selected = set()
    for item in selection['entries']:
        if not isinstance(item, dict) or set(item) != {'path', 'sha256', 'keeper', 'keeper_sha256'}:
            raise Refused('main_identity: selected path/keeper/hash fields differ')
        if item['path'] == item['keeper'] or item['path'] in selected:
            raise Refused('main_identity: selected copy repeats or is its own keeper')
        selected.add(item['path'])
        for value in (item['path'], item['keeper']):
            relative(value, selected=True)
            fingerprint = fingerprints.get(value)
            permission = source.get('permissions', {}).get(value)
            parent_path = os.path.dirname(os.path.join(root, value))
            parent = actual['parents'].get(parent_path)
            if (fingerprint is None or not stat.S_ISREG(stat_value(fingerprint[1])) or fingerprint[0][5] != '1'
                    or permission is None or permission.get('parent_path') != parent_path
                    or permission.get('mount_read_only') is not True or parent is None
                    or not exact(permission.get('parent'), parent['identity'])
                    or (permission.get('uid'), permission.get('gid'), permission.get('mode'), permission.get('nlink'))
                    != (fingerprint[2], fingerprint[3], fingerprint[1], fingerprint[0][5])
                    or actual['file_access'].get(value, {}).get('reader_r') is not True
                    or parent['reader_rx'] is not True):
                raise Refused('main_identity: selected source/keeper or exact source parent proof inadequate')
        source_info = fingerprints[item['path']]
        parent = actual['parents'][os.path.dirname(os.path.join(root, item['path']))]
        if (source_info[2] != '1000' or source_info[0][0] != state['identity'][0]
                or parent['uid'] != '1000' or parent['writer_wx'] is not True):
            raise Refused('main_identity: selected source/parent/backup filesystem is ineligible')
    if selected.intersection(item['keeper'] for item in selection['entries']):
        raise Refused('main_identity: a selected keeper would also be removed')
    return True


def run(env=os.environ, host_deadline=None, selected_paths=None):
    if (env.get('COPY_PROOF_HASHES_JSON') in (None, '{}')
            or not all(env.get(k) for k in ('COPY_PHASE_TOKEN', 'COPY_JOB_UID', 'COPY_POD_UID'))):
        raise Refused('main_identity: actual closed MAIN binding absent')
    end = float(env['COPY_DEADLINE_EPOCH'])
    if host_deadline is None or not math.isfinite(host_deadline) or host_deadline > end:
        raise Refused('main_identity: bounded host delivery deadline required')
    remaining = min(10, host_deadline - time.time())
    if remaining <= 0:
        raise Refused('main_identity: MAIN absolute lease expired')

    def stopped(*_):
        raise Refused('main_identity: own read-only alarm/signal')

    for sig in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stopped)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    cmd = Path('/proc/1/cmdline').read_bytes()
    if (len(cmd) > 4096 or cmd.rstrip(b'\0').split(b'\0')[1:]
            != [b'/copy-writer/book_copy_writer.py', b'--bound-census', b'--wait-proofs', b'--deadline-epoch', env['COPY_DEADLINE_EPOCH'].encode()]):
        raise Refused('main_identity: PID1 is not the unchanged waiting MAIN')
    sys.path.insert(0, '/copy-writer')
    for name, expected in PINS.items():
        raw = Path('/copy-writer', name).read_bytes()
        if len(raw) > 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected:
            raise Refused('main_identity: published runtime module pin changed')
    import epub_copies as copies
    import epub_metadata as metadata
    result = collect(ROOT, STATE, PROOFS, time.monotonic() + remaining, copies, metadata, selected_paths)
    result.update(phase_token=env['COPY_PHASE_TOKEN'], job_uid=env['COPY_JOB_UID'],
                  pod_uid=env['COPY_POD_UID'], main_deadline_epoch=env['COPY_DEADLINE_EPOCH'],
                  host_delivery_deadline_epoch=host_deadline, runtime_module_sha256=PINS)
    sys.stdout.buffer.write(bounded_json(result) + b'\n')
    sys.stdout.buffer.flush()


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise Refused('main_identity: one host deadline argument required')
        run(host_deadline=float(sys.argv[1]))
    except Exception as error:
        print(json.dumps({'complete': False, 'read_only': True, 'production_writes': 0,
                          'code': 'main_identity_refused', 'error_class': type(error).__name__}))
        raise SystemExit(2)
