#!/usr/bin/env python3
"""Single aggregate-only timing diagnostic; never emits a usable census."""
import datetime as dt
import hashlib
import importlib
import json
import math
import os
import signal
import stat
import sys
import time
import types
from pathlib import Path

ROOT = '/data/cephfs-hdd/data/media/books/EBooks'
MAX_SAMPLE_BYTES = 64 * 1024 * 1024
COLLECTOR_SHA = 'c22d220cafebf8c18e9b3f2bfe46156be9e3cc13fd8521532dddd12f97877b6d'
MODULES = {'epub_copies.py': '31134ebfc895e4b8cd5f4f1e4a495572d31c165a42d1ded207da58f3469ee7b6',
           'epub_metadata.py': 'ce3c5a271cb4c94d91f3154240b4cfbc5e0cc57981969fc5c975a0c0f50eb773'}


class Stop(BaseException):
    pass


def configure(collector):
    for name in ('guard', 'fingerprint', 'decimal', 'identity6', 'canonical', 'MAX_PROOF'):
        globals()[name] = getattr(collector, name)


def permission_pass(root, deadline, metadata, health, before, dirs):
    # Keep the following loop identical to the frozen stat_census loop. A
    # finite AST regression checks this; no extra walk or proof is produced.
    permissions, protected, permission_bytes = {}, [], 0
    for path, value in before.items():
        guard(deadline, metadata, health)
        absolute = os.path.join(root, path)
        with metadata.safe_directory(os.path.dirname(absolute)) as directory:
            info = os.stat(os.path.basename(absolute), dir_fd=directory, follow_symlinks=False)
            parent = os.fstat(directory)
            if (fingerprint(info) != value
                    or fingerprint(parent) != dirs[os.path.relpath(os.path.dirname(absolute), root)]):
                raise metadata.Changed("source path changed during stat census")
            permissions[path] = {"uid": value[2], "gid": value[3], "mode": value[1], "nlink": value[0][5],
                                 "parent": identity6(parent) + [decimal(parent.st_mode), decimal(parent.st_uid), decimal(parent.st_gid)],
                                 "parent_path": os.path.dirname(absolute), "mount_read_only": True}
            permission_bytes += len(canonical({path: permissions[path]}))
            if permission_bytes > MAX_PROOF // 2:
                raise metadata.Refused("complete permission proof byte cap exceeded")
            metadata._same_directory(directory, os.path.dirname(absolute))
        if not stat.S_ISREG(info.st_mode):
            protected.append({"path": path, "reason": "nonregular current library entry requires review"})
        elif info.st_uid != 1000 or info.st_nlink != 1 or parent.st_uid != 1000 or parent.st_mode & 0o300 != 0o300:
            protected.append({"path": path, "reason": "source owner/nlink or immediate parent ownership/access requires review"})
    return {'files_checked': len(permissions), 'protected_entry_count': len(protected)}


def read_prefix(root, path, expected, deadline, metadata, health):
    guard(deadline, metadata, health)
    absolute = os.path.join(root, path)
    with metadata.safe_directory(os.path.dirname(absolute)) as directory:
        fd = os.open(os.path.basename(absolute), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        with os.fdopen(fd, 'rb') as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or fingerprint(before) != expected:
                raise metadata.Changed('sample source changed before read')
            limit = min(MAX_SAMPLE_BYTES, before.st_size)
            size = 0
            while size < limit:
                guard(deadline, metadata, health)
                chunk = source.read(min(1024 * 1024, limit - size))
                if not chunk:
                    raise metadata.Changed('sample ended before bounded prefix')
                size += len(chunk)
            after = os.fstat(source.fileno())
            current = os.stat(os.path.basename(absolute), dir_fd=directory, follow_symlinks=False)
            metadata._same_directory(directory, os.path.dirname(absolute))
            if fingerprint(after) != expected or fingerprint(current) != expected:
                raise metadata.Changed('sample descriptor/path changed during read')
    guard(deadline, metadata, health)
    return {'sample_bytes_read': size, 'sample_total_bytes': before.st_size,
            'sample_byte_cap': MAX_SAMPLE_BYTES}


class Reporter:
    def __init__(self):
        self.stage = 'bootstrap'
        self.tick = time.monotonic()
        self.started = self.tick

    def event(self, kind, **fields):
        value = {'type': kind, 'stage': self.stage,
                 'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                 'elapsed_seconds': time.monotonic() - self.started,
                 'production_writes': 0, 'PG_operations': 0,
                 'copy_eligible': False, **fields}
        print(json.dumps(value, sort_keys=True, separators=(',', ':')), flush=True)

    def start(self, stage):
        self.stage, self.tick = stage, time.monotonic()
        self.event('diagnostic-stage-start')

    def stop(self, **fields):
        self.event('diagnostic-stage-stop', stage_seconds=time.monotonic() - self.tick, **fields)


def load_modules(raw):
    if hashlib.sha256(raw).hexdigest() != COLLECTOR_SHA:
        raise RuntimeError('collector pin differs')
    sys.path.insert(0, '/copy-writer')
    for name, sha in MODULES.items():
        if hashlib.sha256(Path('/copy-writer', name).read_bytes()).hexdigest() != sha:
            raise RuntimeError('signed image module pin differs')
    metadata, copies = importlib.import_module('epub_metadata'), importlib.import_module('epub_copies')
    collector = types.ModuleType('diagnostic_frozen_collector')
    exec(compile(raw, '<frozen-collector>', 'exec'), collector.__dict__)
    configure(collector)
    return collector, metadata, copies


def main(raw, environ=os.environ):
    report = Reporter()
    armed = False
    try:
        if environ.get('COPY_DIAGNOSTIC_PHASE_READY') != '1':
            raise RuntimeError('closed diagnostic gate')
        epoch = float(environ['COPY_SOURCE_DEADLINE_EPOCH'])
        remaining = epoch - time.time()
        if not math.isfinite(epoch) or not 0 < remaining <= 90 or os.getuid() != 1000 or os.getgid() != 1000:
            raise RuntimeError('diagnostic clock or uid differs')
        deadline = time.monotonic() + remaining
        def stop(*_):
            raise Stop()
        for sig in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, stop)
        signal.setitimer(signal.ITIMER_REAL, remaining)
        armed = True
        collector, metadata, copies = load_modules(raw)
        sample = collector.safe_relative(environ['COPY_DIAGNOSTIC_SAMPLE'], metadata)
        if not sample.lower().endswith('.epub') or any(part.startswith('.') for part in sample.split('/')):
            raise metadata.Refused('sample is not a visible EPUB')
        health = lambda: None
        report.start('native_binding')
        collector.await_native_binding(environ, ROOT, deadline, metadata, copies, health)
        report.stop(status='complete')
        report.start('complete_walk')
        before, dirs = collector.walk(ROOT, deadline, metadata, health)
        paths = collector.epub_paths(before)
        if sample not in paths:
            raise metadata.Refused('fixed sample is not in complete walk')
        report.stop(status='complete', all_file_count=len(before), directory_count=len(dirs),
                    epub_count=len(paths), epub_total_bytes=sum(int(before[path][0][2]) for path in paths))
        report.start('permission_pass')
        report.stop(status='complete', **permission_pass(ROOT, deadline, metadata, health, before, dirs))
        report.start('sample_prefix_read')
        report.stop(status='complete', **read_prefix(ROOT, sample, before[sample], deadline, metadata, health))
        report.event('diagnostic-complete', complete_proof=False)
        return 0
    except BaseException as error:
        report.stop(status='deadline' if isinstance(error, Stop) else 'refused', error_class=type(error).__name__)
        report.event('diagnostic-refused', error_class=type(error).__name__, complete_proof=False)
        return 2
    finally:
        if armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
