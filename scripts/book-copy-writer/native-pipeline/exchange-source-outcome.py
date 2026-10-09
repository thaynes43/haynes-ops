#!/usr/bin/env python3
"""SOURCE exec child: private bytes only; no PG or library proof authority."""
import ctypes
import hashlib
import json
import os
import signal
import stat
import sys
import time

REQUEST = 'copy-outcome-request.json'
RESPONSE = 'copy-outcome-response.json'
CAP_REQUEST = 32 * 1024 * 1024
CAP_RESPONSE = 16 * 1024 * 1024


class Refused(RuntimeError):
    pass


def require(condition):
    if not condition:
        raise Refused('private_outcome_exchange_refused')


def unique(rows):
    result = {}
    for key, value in rows:
        require(key not in result)
        result[key] = value
    return result


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode() + b'\n'


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_uid, info.st_gid,
            info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def read_private(directory, name, cap, mode):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.getuid()
                and before.st_gid == os.getgid() and stat.S_IMODE(before.st_mode) == mode and 0 < before.st_size <= cap)
        raw = source.read(cap + 1)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(source.fileno()))
                == identity(os.stat(name, dir_fd=directory, follow_symlinks=False)))
        return raw


def publish(directory, temporary, final):
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    require(rename(directory, temporary.encode(), directory, final.encode(), 1) == 0)


def exchange(raw, epoch, environ=os.environ, directory='/tmp', wait=time.sleep):
    require(environ.get('COPY_SOURCE_PHASE_READY') == '1' and len(raw) <= CAP_REQUEST)
    value = json.loads(raw, object_pairs_hook=unique)
    require(isinstance(value, dict) and canonical(value) == raw and value.get('type') == 'copy_outcome_request'
            and type(value.get('deadline_epoch')) in (int, float) and value['deadline_epoch'] == epoch
            and all(value.get(key) == environ.get(env) for key, env in
                    [('phase_token', 'COPY_PHASE_TOKEN'), ('job_uid', 'COPY_JOB_UID'), ('pod_uid', 'COPY_POD_UID')])
            and time.time() < epoch <= min(float(environ['COPY_SOURCE_DEADLINE_EPOCH']), time.time() + 12))
    request_sha = hashlib.sha256(raw).hexdigest()
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    before_dir = os.fstat(fd)
    temporary = '.' + REQUEST + '.receive'
    staged = None
    try:
        for name in (temporary, REQUEST, RESPONSE):
            try: os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError: continue
            raise Refused('private_outcome_exchange_already_used')
        staged = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(os.dup(staged), 'wb') as output:
            output.write(raw); output.flush(); os.fsync(output.fileno())
        os.fchmod(staged, 0o400); os.fsync(staged)
        require(time.time() < epoch and identity(os.fstat(staged)) == identity(os.stat(temporary, dir_fd=fd, follow_symlinks=False)))
        publish(fd, temporary, REQUEST); os.fsync(fd)
        while True:
            require(time.time() < epoch)
            try: response = read_private(fd, RESPONSE, CAP_RESPONSE, 0o600)
            except FileNotFoundError:
                wait(min(.05, max(0, epoch - time.time()))); continue
            decoded = json.loads(response, object_pairs_hook=unique)
            require(isinstance(decoded, dict) and decoded.get('type') == 'copy_outcome_response'
                    and decoded.get('request_sha256') == request_sha
                    and decoded.get('main_receipt_sha256') == value.get('main_receipt_sha256')
                    and all(decoded.get(k) == value[k] for k in ('phase_token', 'job_uid', 'pod_uid'))
                    and identity(os.fstat(staged)) == identity(os.stat(REQUEST, dir_fd=fd, follow_symlinks=False))
                    and (before_dir.st_dev, before_dir.st_ino) == (os.stat(directory, follow_symlinks=False).st_dev, os.stat(directory, follow_symlinks=False).st_ino)
                    and time.time() < epoch)
            return response
    finally:
        if staged is not None:
            try:
                if identity(os.fstat(staged)) == identity(os.stat(temporary, dir_fd=fd, follow_symlinks=False)):
                    os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError: pass
            os.close(staged)
        os.close(fd)


def main():
    # Arm before stdin; a truncated stream cannot consume a fresh extra budget.
    epoch = float(sys.argv[1])
    require(time.time() < epoch <= min(float(os.environ['COPY_SOURCE_DEADLINE_EPOCH']), time.time() + 12))
    def stopped(*_): raise Refused('private_outcome_exchange_deadline')
    for sig in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT): signal.signal(sig, stopped)
    signal.setitimer(signal.ITIMER_REAL, epoch - time.time())
    raw = sys.stdin.buffer.read(CAP_REQUEST + 1)
    response = exchange(raw, epoch)
    sys.stdout.buffer.write(response); sys.stdout.buffer.flush()


if __name__ == '__main__':
    try: main()
    except BaseException: os._exit(2)
