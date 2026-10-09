#!/usr/bin/env python3
"""Refresh only successor source/template hashes after a reviewed source edit.
Never touch image/module identity maps, proof artifacts or historical archives.
"""
import hashlib
import json
from pathlib import Path
import re

from bootstrap_payload import program

HERE = Path(__file__).parent


def sha(name):
    return hashlib.sha256((HERE / name).read_bytes()).hexdigest()


def replace_constant(file, variable, value):
    path = HERE / file
    source, count = re.subn(variable + r"\s*=\s*'[0-9a-f]{64}'", variable + " = '" + value + "'", path.read_text())
    if count != 1:
        raise ValueError('successor source pin missing or ambiguous')
    path.write_text(source)


def refresh():
    for variable, name in [('OUTCOME_MAILBOX_SHA256', 'outcome_mailbox.py'), ('OUTCOME_VERIFIER_SHA256', 'verify-copy-outcome-readonly.py')]:
        replace_constant('capture-source-stat-census.py', variable, sha(name))
    files = {name: (HERE / name).read_bytes() for name in
             ('bound_census_collectors.py', 'source_health.py', 'capture-source-stat-census.py', 'outcome_mailbox.py', 'verify-copy-outcome-readonly.py')}
    path = HERE / 'pg-nfs-readonly-source-template.prepared.json'
    template = json.loads(path.read_bytes())
    template['spec']['template']['spec']['containers'][0]['command'] = ['nice', '-n', '19', 'python', '-B', '-c',
        program(files, 'capture-source-stat-census.py', 'COPY_SOURCE_PHASE_READY')]
    path.write_text(json.dumps(template, sort_keys=True, separators=(',', ':')) + '\n')
    path = HERE / 'checkpoint-copy-job.py'
    source, count = re.subn(r"('pg-nfs-readonly-source-template\.prepared\.json',')[0-9a-f]{64}(')",
                           lambda match: match.group(1) + sha('pg-nfs-readonly-source-template.prepared.json') + match.group(2), path.read_text())
    if count != 1:
        raise ValueError('successor frozen declaration pin missing')
    path.write_text(source)
    for file, variable, name in [('deliver-main-copy-proofs.py', 'HELPER_SHA', 'checkpoint-copy-job.py'),
                                ('deliver-main-copy-proofs.py', 'BRIDGE_SHA', 'check-main-source-identity.py'),
                                ('deliver-private-inputs.py', 'HELPER_SHA', 'checkpoint-copy-job.py')]:
        replace_constant(file, variable, sha(name))
    path = HERE / 'run-live-byte-baseline.py'
    source = path.read_text()
    for key, name in [('helper', 'checkpoint-copy-job.py'), ('sender', 'deliver-private-inputs.py')]:
        source, count = re.subn("'" + key + "':'[0-9a-f]{64}'", "'" + key + "':'" + sha(name) + "'", source)
        if count != 1:
            raise ValueError('successor host source pin missing')
    path.write_text(source)


if __name__ == '__main__':
    refresh()
