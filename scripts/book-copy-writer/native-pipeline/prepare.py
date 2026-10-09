#!/usr/bin/env python3
"""Materialize a private coherent source package. No API, PG or process launch."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

import pipeline_pins as pins

SOURCES = (
    'pipeline_pins.py', 'bootstrap_payload.py', 'source_health.py',
    'capture-source-stat-census.py', 'capture-live-byte-baseline.py',
    'bound_census_collectors.py', 'checkpoint-copy-job.py',
    'prepare-native-source-contract.py', 'deliver-private-inputs.py',
    'receive-private-inputs.py', 'receive-live-baseline-ack.py',
    'run-live-byte-baseline.py', 'assemble-copy-proofs.py',
    'check-main-source-identity.py', 'deliver-main-copy-proofs.py',
    'verify-copy-outcome-readonly.py', 'outcome_mailbox.py', 'exchange-source-outcome.py',
    'proof_sender.py', 'prepare.py',
)
TEMPLATES = (
    'live-baseline-closed-manifest.json', 'pg-nfs-readonly-source-template.prepared.json',
    'copy-writer-source-template.prepared.json', 'll-readonly-source-template.prepared.json',
    'kavita-readonly-source-template.prepared.json', 'lidarr-copy-source-template.prepared.json',
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode() + b'\n'


def read(path, cap=1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= cap:
            raise ValueError('unsafe package source')
        raw = source.read(cap + 1)
        after, current = os.fstat(source.fileno()), os.stat(path, follow_symlinks=False)
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mode', 'st_uid', 'st_gid', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink')
        if len(raw) != before.st_size or any(getattr(before, k) != getattr(after, k) or getattr(before, k) != getattr(current, k) for k in fields):
            raise ValueError('package source changed')
        return raw


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    return {'path': str(path), 'sha256': digest(raw)}


def materialize(directory, signature_receipt, registry_proof):
    """Close local dependencies; external window preparation remains separate."""
    if not directory.is_absolute() or directory.exists() or directory.is_symlink():
        raise ValueError('private package directory must be absolute and unused')
    # Authenticating this image was an independently reviewed operation. This
    # preparation verifies its exact retained receipt, not a new signature claim.
    receipt_raw, proof_raw = read(signature_receipt), read(registry_proof)
    if digest(receipt_raw) != pins.IMAGE_RECEIPT_SHA256:
        raise ValueError('reviewed image signature receipt differs')
    proof = json.loads(proof_raw)
    if (proof.get('image') != pins.IMAGE or proof.get('source_sha') != pins.IMAGE_SOURCE
            or proof.get('all_six_files_match_exact_git_source') is not True
            or proof.get('module_sha256') != pins.MODULES
            or proof.get('workdir') != '/copy-writer' or proof.get('user') != '1000:1000'
            or proof.get('entrypoint') != ['python', '/copy-writer/book_copy_writer.py']):
        raise ValueError('actual registry module closure differs')
    runtime = {}
    for name, expected in {**pins.MODULES, 'epub_copy_preflight.py': pins.PREFLIGHT_SHA256}.items():
        raw = read(pins.module_source(name))
        if digest(raw) != expected:
            raise ValueError('current runtime module source differs')
        runtime[name] = raw
    sources = {name: read(pins.HERE / name) for name in SOURCES + TEMPLATES}
    if (digest(sources['bound_census_collectors.py']) != pins.COLLECTOR_SHA256
            or digest(sources['capture-live-byte-baseline.py']) != pins.LIVE_ENTRY_SHA256
            or digest(canonical(pins.SELECTED_SCOPE)) != pins.SELECTED_SCOPE_SHA256):
        raise ValueError('reviewed bootstrap or selected scope differs')
    directory.mkdir(mode=0o700)
    module_dir = directory / 'runtime-modules'
    module_dir.mkdir(mode=0o700)
    files = {name: save(directory / name, raw) for name, raw in sources.items()}
    files.update({'runtime-modules/' + name: save(module_dir / name, raw) for name, raw in runtime.items()})
    files['image-signature-receipt.json'] = save(directory / 'image-signature-receipt.json', receipt_raw)
    files['registry-module-proof.json'] = save(directory / 'registry-module-proof.json', proof_raw)
    files['selected-stage-scope.json'] = save(directory / 'selected-stage-scope.json', canonical(pins.SELECTED_SCOPE))
    live_phase = uuid.uuid4().hex
    live = json.loads(sources['live-baseline-closed-manifest.json'])
    for meta in (live['metadata'], live['spec']['template']['metadata']):
        if meta.get('labels', {}).get(pins.LABEL) != '0' * 32:
            raise ValueError('closed source template phase differs')
        meta['labels'][pins.LABEL] = live_phase
    files['live-phase-closed-manifest.json'] = save(directory / 'live-phase-closed-manifest.json', canonical(live))
    contract = {'schema': 1, 'output_dir': str(directory / 'live-actual'), 'phase_token': live_phase}
    for key, name in {'closed_manifest': 'live-phase-closed-manifest.json', 'helper': 'checkpoint-copy-job.py',
                      'sender': 'deliver-private-inputs.py', 'receiver': 'receive-private-inputs.py',
                      'native_verifier': 'prepare-native-source-contract.py', 'collector': 'bound_census_collectors.py',
                      'ack_receiver': 'receive-live-baseline-ack.py', 'selected_scope': 'selected-stage-scope.json'}.items():
        contract[key] = files[name]
    files['live-launch-contract.json'] = save(directory / 'live-launch-contract.json', canonical(contract))
    packet = {'schema': 1, 'prepared_only': True, 'runtime_authorization': False,
              'full_pipeline_ready': False, 'production_operations': 0,
              'files': files, 'runtime_image': pins.IMAGE, 'app_image': pins.APP_IMAGE,
              'runtime_module_sha256': pins.MODULES, 'bootstrap_oci_signed': False,
              'live_phase_token': live_phase, 'job_roles': pins.JOBS,
              'selected_scope_sha256': pins.SELECTED_SCOPE_SHA256,
              'scope_profile_sha256': pins.SCOPE_PROFILE_SHA256,
              'unchanged_bounds': {'live_seconds': 180, 'host_total_seconds': 200,
                                   'artifact_bytes': 33554432, 'log_bytes': 1048576,
                                   'original_byte_start_age_seconds': 300,
                                   'restoration_trigger_seconds': 170, 'source_abort_seconds': 250},
              'unbound': ['current Stop/inverse/watch/supervisor exact heads and paused-window math',
                          'final independent source closure review and actual MAIN receipt/outcome request',
                          'server admission and current full name/phase/UID native absence',
                          'actual corpus/progress/locks/wants, capture clocks and native UID proofs',
                          'fresh exact root runtime authorization']}
    return save(directory / 'source-package-review.json', canonical(packet))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package-dir', type=Path, required=True)
    parser.add_argument('--image-signature-receipt', type=Path, required=True)
    parser.add_argument('--registry-module-proof', type=Path, required=True)
    args = parser.parse_args()
    result = materialize(args.package_dir, args.image_signature_receipt, args.registry_module_proof)
    print(json.dumps({'prepared_only': True, 'full_pipeline_ready': False, 'runtime_authorization': False, 'review': result}))


if __name__ == '__main__':
    main()
