#!/usr/bin/env python3
"""Bounded stdin receiver: private proof files only; no library or PG calls."""
import ctypes
import datetime as dt
import hashlib
import json
import os
import re
import signal
import stat
import sys
import time
from contextlib import contextmanager

MAX_BYTES = 65536
MAX_HEADER = 8192
FILES = ('selected-stage-scope.json', 'native-source-binding.json')
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
SHA = re.compile(r'[0-9a-f]{64}')
PHASE = re.compile(r'[0-9a-f]{32}')
MODULES = {'epub_copies.py':'b79389c89bd5a57b4737ad693d2c209bc513e818343064ec04c8f0fcfb24a7da',
           'epub_metadata.py':'ce3c5a271cb4c94d91f3154240b4cfbc5e0cc57981969fc5c975a0c0f50eb773'}


class Refused(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise Refused(code)


def unique(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'duplicate_json_key')
        value[key] = item
    return value


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def utc_epoch(value):
    require(isinstance(value, str) and len(value) <= 40, 'utc_timestamp_required')
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(parsed.tzinfo is not None and parsed.utcoffset() == dt.timedelta(0), 'utc_timestamp_required')
    return parsed.timestamp()


@contextmanager
def alarm(epoch):
    require(0 < epoch - time.time() <= 12.01, 'private_delivery_deadline')
    def stop(*_):
        raise Refused('private_delivery_deadline_or_signal')
    handlers = {s: signal.signal(s, stop) for s in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT)}
    started = time.monotonic()
    previous = signal.getitimer(signal.ITIMER_REAL)
    remaining = max(.000001, epoch - time.time())
    if previous[0]:
        remaining = min(remaining, previous[0])
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        if previous[0]:
            signal.setitimer(signal.ITIMER_REAL, max(.000001, previous[0]-(time.monotonic()-started)), previous[1])


def read_exact(stream, count):
    result = bytearray()
    while len(result) < count:
        chunk = stream.read(count - len(result))
        require(bool(chunk), 'private_input_truncated')
        result.extend(chunk)
    return bytes(result)


def shorten_alarm(epoch):
    remaining, interval = signal.getitimer(signal.ITIMER_REAL)
    require(remaining > 0 and interval == 0 and epoch > time.time(), 'private_delivery_alarm_missing_or_expired')
    signal.setitimer(signal.ITIMER_REAL, min(remaining, max(.000001,epoch-time.time())))


def native_contract(raw, environ, mode):
    value = decode(raw)
    require(isinstance(value, dict) and set(value) == {'schema', 'phase_token', 'observed_at', 'source_binding', 'module_sha256'}
            and type(value['schema']) is int and value['schema'] == 1, 'native_contract_schema')
    binding = value['source_binding']
    require(isinstance(binding, dict) and set(binding) == {'namespace','pod_name','pod_uid','job_uid','node','image','image_id',
            'pod_spec_sha256','restarts','mount_root','nfs_server','nfs_export'}, 'native_binding_schema')
    expected = {'namespace': 'COPY_NAMESPACE', 'pod_name': 'COPY_POD_NAME', 'pod_uid': 'COPY_POD_UID',
                'job_uid': 'COPY_JOB_UID', 'node': 'COPY_NODE'}
    require(all(binding[k] == environ.get(v) for k, v in expected.items())
            and value['phase_token'] == environ.get('COPY_PHASE_TOKEN'), 'native_identity_differs')
    require(PHASE.fullmatch(value['phase_token']) and UUID.fullmatch(binding['pod_uid']) and UUID.fullmatch(binding['job_uid']),
            'native_identity_invalid')
    age = time.time() - utc_epoch(value['observed_at'])
    require(0 <= age <= 15, 'native_observation_expired')
    require(value['module_sha256'] == MODULES, 'native_modules_differs')
    if mode == 'LIVE':
        require(decode(environ['COPY_BASELINE_MODULE_SHA256_JSON']) == MODULES, 'live_module_env_differs')
    return value


def rename_noreplace(directory, source, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    require(rename is not None, 'atomic_noreplace_unsupported')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(directory, source.encode(), directory, destination.encode(), 1) != 0:
        raise Refused('atomic_noreplace_refused')


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_uid, info.st_gid, info.st_nlink)


def validate_scope(raw):
    scope = decode(raw)
    require(isinstance(scope,dict) and bool(scope) and len(scope) <= 64
            and all(isinstance(k,str) and 0 < len(k.encode()) <= 4096 and '\\' not in k and not any(ord(c)<32 for c in k)
                    and not any(p in ('','.','..') for p in k.split('/')) and isinstance(v,str) and SHA.fullmatch(v)
                    for k,v in scope.items()), 'selected_scope_schema')
    return scope


def receive(stream, environ=os.environ, directory='/tmp', publish=rename_noreplace):
    mode = 'LIVE' if environ.get('COPY_BASELINE_PHASE_READY') == '1' else 'SOURCE'
    gate = 'COPY_BASELINE_PHASE_READY' if mode == 'LIVE' else 'COPY_SOURCE_PHASE_READY'
    require(environ.get(gate) == '1' and not (mode == 'LIVE' and environ.get('COPY_SOURCE_PHASE_READY') == '1'), 'phase_not_authorized')
    require(os.getuid() == 1000 and os.getgid() == 1000, 'private_receiver_uid_gid')
    operation = float(environ['COPY_BASELINE_DEADLINE_EPOCH' if mode == 'LIVE' else 'COPY_SOURCE_DEADLINE_EPOCH'])
    receiver_end = min(operation, time.time() + 12)
    with alarm(receiver_end):
        line = stream.readline(MAX_HEADER + 1)
        require(len(line) <= MAX_HEADER and line.endswith(b'\n'), 'private_header_cap_or_truncation')
        header = decode(line)
        require(isinstance(header, dict) and set(header) == {'schema','mode','phase_token','job_uid','pod_uid','deadline_epoch','files'}
                and type(header['schema']) is int and header['schema'] == 1 and header['mode'] == mode, 'private_header_schema')
        require(all(header[k] == environ.get(v) for k, v in [('phase_token','COPY_PHASE_TOKEN'),('job_uid','COPY_JOB_UID'),('pod_uid','COPY_POD_UID')]), 'private_header_identity')
        require(isinstance(header['deadline_epoch'], str) and re.fullmatch(r'[0-9]+(?:\.[0-9]{1,6})?', header['deadline_epoch']), 'private_header_deadline')
        deadline = float(header['deadline_epoch'])
        require(time.time() < deadline <= receiver_end, 'private_header_deadline')
        shorten_alarm(deadline)
        rows = header['files']
        names = list(FILES) if mode == 'LIVE' else ['native-source-binding.json']
        require(isinstance(rows, list) and [r.get('name') for r in rows if isinstance(r,dict)] == names, 'private_file_scope')
        require(all(set(r) == {'name','bytes','sha256'} and type(r['bytes']) is int and 0 < r['bytes'] <= MAX_BYTES
                    and isinstance(r['sha256'],str) and SHA.fullmatch(r['sha256']) for r in rows)
                and sum(r['bytes'] for r in rows) <= MAX_BYTES, 'private_file_length_or_hash')
        dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        created, opened = {}, []
        try:
            for name in names:
                for path in (name, '.'+name+'.receive'):
                    try:
                        os.stat(path, dir_fd=dirfd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    raise Refused('private_path_already_exists')
            for row in rows:
                raw = read_exact(stream, row['bytes'])
                require(hashlib.sha256(raw).hexdigest() == row['sha256'], 'private_file_hash')
                if row['name'] == 'native-source-binding.json':
                    native = native_contract(raw, environ, mode)
                    require(deadline <= utc_epoch(native['observed_at']) + 15, 'private_native_deadline')
                else:
                    validate_scope(raw)
                temporary = '.'+row['name']+'.receive'
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
                created[temporary] = fd; opened.append(fd)
                with os.fdopen(os.dup(fd),'wb') as output:
                    output.write(raw); output.flush(); os.fsync(output.fileno())
                os.fchmod(fd,0o400); os.fsync(fd)
            require(stream.read(1) == b'', 'private_input_extra_bytes')
            require(time.time() < deadline, 'private_delivery_deadline')
            for row, fd in zip(rows, opened):
                temporary = '.'+row['name']+'.receive'
                before = os.fstat(fd)
                require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.getuid()
                        and stat.S_IMODE(before.st_mode) == 0o400 and before.st_size == row['bytes']
                        and fingerprint(before) == fingerprint(os.stat(temporary, dir_fd=dirfd, follow_symlinks=False)), 'private_staged_identity')
                publish(dirfd, temporary, row['name'])
                del created[temporary]
                require(fingerprint(before) == fingerprint(os.stat(row['name'],dir_fd=dirfd,follow_symlinks=False)), 'private_published_identity')
                os.fsync(dirfd)
            return {'schema':1,'type':'private_inputs_ready','mode':mode,'phase_token':header['phase_token'],
                    'job_uid':header['job_uid'],'pod_uid':header['pod_uid'],'files':rows,'production_writes':0}
        finally:
            for path, fd in created.items():
                try:
                    owned = os.fstat(fd)
                    actual = os.stat(path,dir_fd=dirfd,follow_symlinks=False)
                    if (owned.st_dev,owned.st_ino) == (actual.st_dev,actual.st_ino):
                        os.unlink(path,dir_fd=dirfd)
                except FileNotFoundError:
                    pass
            for fd in opened:
                os.close(fd)
            os.close(dirfd)


def main():
    try:
        result = receive(sys.stdin.buffer)
        print(json.dumps(result,sort_keys=True),flush=True)
        return 0
    except (Refused, ValueError, KeyError, TypeError, OSError):
        print('{"type":"private_inputs_refused","code":"private_input_contract_refused","production_writes":0}',flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
