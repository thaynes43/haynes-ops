#!/usr/bin/env python3
"""Prepare an unused private diagnostic packet; does not call cluster APIs."""
import argparse
import base64
import copy
import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / 'live-baseline-host'
LABEL = 'issue825.haynesnetwork/phase'
NAME = 'issue831-live-diagnostic-1009-01'
IMAGE = 'ghcr.io/thaynes43/book-copy-writer:sha-04c611e7ccf6d057f5b6b466dc4d52f5bb5672c8@sha256:fe12dd95f77cbdf33f2bd57cbe8ecb752e9d730a7de6b26b329beca474954d5f'
KUBECONFIG = '/home/dev/work/hn-825b-incluster-kubeconfig.json'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def private(path, value):
    raw = value if isinstance(value, bytes) else canonical(value) + b'\n'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def sample_file(path):
    if not path.is_absolute():
        raise ValueError('private sample input must be absolute')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= 4097:
            raise ValueError('private sample input must be one bounded regular file')
        raw = source.read(4098)
        after = os.fstat(source.fileno())
    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('private sample input changed')
    value = raw.decode().removesuffix('\n')
    if (not value or len(value.encode()) > 4096 or '\\' in value or any(ord(c) < 32 for c in value)
            or any(p in ('', '.', '..') or p.startswith('.') for p in value.split('/'))
            or not value.lower().endswith('.epub')):
        raise ValueError('private sample must be one visible relative EPUB')
    return value


def sources():
    return {'prepare': HERE / 'prepare.py', 'probe': HERE / 'probe.py', 'runner': HERE / 'run.py',
            'collector': BASE / 'dependencies/bound_census_collectors.py',
            'host': BASE / 'run-live-byte-baseline.py',
            'helper': BASE / 'dependencies/checkpoint-copy-job.py',
            'receiver': BASE / 'dependencies/receive-private-inputs.py',
            'verifier': BASE / 'dependencies/prepare-native-source-contract.py',
            'template': BASE / 'live-baseline-closed-manifest.json'}


def closed_manifest(sample, phase):
    job = json.loads((BASE / 'live-baseline-closed-manifest.json').read_bytes())
    for metadata in (job['metadata'], job['spec']['template']['metadata']):
        metadata['labels'][LABEL] = phase
        metadata['labels']['app'] = 'issue831-native-readonly-diagnostic'
        metadata.setdefault('annotations', {})['k8tz.io/inject'] = 'false'
    job['metadata']['name'] = NAME
    job['spec']['activeDeadlineSeconds'] = 90
    job['spec']['template']['spec']['terminationGracePeriodSeconds'] = 0
    pod = job['spec']['template']['spec']
    container = pod['containers'][0]
    container['resources']['limits'] = {'cpu': '500m', 'memory': '256Mi'}
    raw = (HERE / 'probe.py').read_bytes()
    collector = (BASE / 'dependencies/bound_census_collectors.py').read_bytes()
    bootstrap = ('import base64,hashlib;'
                 'p=base64.b64decode(' + repr(base64.b64encode(raw).decode()) + ');'
                 'c=base64.b64decode(' + repr(base64.b64encode(collector).decode()) + ');'
                 'assert hashlib.sha256(p).hexdigest()==' + repr(sha(raw)) + ';'
                 'g={"__name__":"diagnostic_probe"};exec(compile(p,"<pinned-probe>","exec"),g);'
                 'raise SystemExit(g["main"](c))')
    container['command'] = ['nice', '-n', '19', 'python', '-B', '-c', bootstrap]
    identity = [row for row in container['env'] if 'valueFrom' in row]
    container['env'] = identity + [{'name': key, 'value': value} for key, value in {
        'PYTHONDONTWRITEBYTECODE': '1', 'COPY_DIAGNOSTIC_PHASE_READY': '0',
        'COPY_SOURCE_PHASE_READY': '0', 'COPY_SOURCE_DEADLINE_EPOCH': '0',
        'COPY_DIAGNOSTIC_SAMPLE': sample}.items()]
    return job


def prepare(sample_path, output):
    if not output.is_absolute() or output.exists() or HERE.parents[2] in output.parents:
        raise ValueError('packet needs an unused absolute directory outside git')
    sample = sample_file(sample_path)
    phase = uuid.uuid4().hex
    manifest = closed_manifest(sample, phase)
    output.mkdir(mode=0o700)
    private(output / 'closed-manifest.json', manifest)
    source_pins = {key: {'path': str(path), 'sha256': sha(path.read_bytes())} for key, path in sources().items()}
    packet = {'schema': 1, 'prepared_only': True, 'copy_eligible': False,
              'name': NAME, 'phase': phase, 'sources': source_pins,
              'kubeconfig_path': KUBECONFIG,
              'manifest': {'path': str(output / 'closed-manifest.json'),
                           'sha256': sha((output / 'closed-manifest.json').read_bytes())},
              'bounds': {'diagnostic_seconds': 90, 'cleanup_seconds': 200,
                         'sample_bytes': 64 * 1024 * 1024, 'cpu': '500m', 'memory': '256Mi'},
              'library_writes': 0, 'PG_operations': 0, 'runtime_authorized': False}
    private(output / 'packet.json', packet)
    return {'packet': str(output / 'packet.json'), 'packet_sha256': sha((output / 'packet.json').read_bytes()),
            'manifest_sha256': packet['manifest']['sha256'], 'phase': phase,
            'command': ['env', 'KUBECONFIG=' + KUBECONFIG, 'nice', '-n', '19', 'python3', '-B', str(HERE / 'run.py'),
                        '--packet', str(output / 'packet.json'), '--go', sha((output / 'packet.json').read_bytes())],
            'prepared_only': True, 'runtime_authorized': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--sample-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(prepare(arguments.sample_file, arguments.output), sort_keys=True))
