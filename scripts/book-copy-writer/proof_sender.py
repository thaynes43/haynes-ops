#!/usr/bin/env python3
"""Operator-side exec delivery; exact durable Job and Pod ownership before stdin."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from proof_transport import FILES, Refused, gate, send


def load_helper(path, expected_sha):
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected_sha:
        raise Refused("checkpoint helper differs from the reviewed hash")
    spec = importlib.util.spec_from_file_location("copy_checkpoint", path)
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    return helper


def verify_target(helper, row, phase, job, pod, pod_uid):
    ready = row['ready_manifest']
    ns, name, uid = row['namespace'], row['name'], row['uid']
    meta = job.get('metadata', {})
    if (not row['writer'] or not uid or row['phase_token'] != phase
            or helper.digest(helper.encoded(ready)) != row['ready_manifest_sha256']
            or (meta.get('namespace'), meta.get('name'), meta.get('uid')) != (ns, name, uid)
            or meta.get('deletionTimestamp') or meta.get('labels', {}).get(helper.LABEL) != phase
            or not helper.declared_matches(ready, job)):
        raise Refused("live Job differs from the durable ready intent or UID")
    pm = pod.get('metadata', {})
    owners = pm.get('ownerReferences', [])
    labels = pm.get('labels', {})
    if (pm.get('namespace') != ns or pm.get('uid') != pod_uid or pm.get('deletionTimestamp')
            or labels.get(helper.LABEL) != phase or labels.get('batch.kubernetes.io/controller-uid') != uid
            or len(owners) != 1 or owners[0].get('apiVersion') != 'batch/v1'
            or owners[0].get('kind') != 'Job' or owners[0].get('name') != name
            or owners[0].get('uid') != uid or owners[0].get('controller') is not True):
        raise Refused("live Pod UID, phase or controller Job owner differs")
    source = ready['spec']['template']['spec']
    containers = source.get('containers', [])
    if len(containers) != 1 or source.get('restartPolicy') != 'Never' or source.get('automountServiceAccountToken') is not False:
        raise Refused("copy receiver requires the single reviewed non-restarting writer")
    container = containers[0]
    env = container.get('env', [])
    if len({v['name'] for v in env}) != len(env):
        raise Refused("copy writer environment repeats a key")
    values = {v['name']: v.get('value') for v in env}
    for key, field in (('COPY_PHASE_TOKEN', "metadata.labels['" + helper.LABEL + "']"),
                       ('COPY_JOB_UID', "metadata.labels['batch.kubernetes.io/controller-uid']"),
                       ('COPY_POD_UID', 'metadata.uid')):
        variable = next((v for v in env if v['name'] == key), {})
        if variable != {'name': key, 'valueFrom': {'fieldRef': {'apiVersion': 'v1', 'fieldPath': field}}}:
            raise Refused("copy identity must come from the exact Downward API field")
    deadline = values.get('COPY_DEADLINE_EPOCH')
    if (container.get('command') != ['nice', '-n', '19', 'python', '/copy-writer/book_copy_writer.py']
            or container.get('args') != ['--wait-proofs', '--deadline-epoch', deadline]
            or not helper.image_identity(container['image']).startswith('ghcr.io/thaynes43/book-copy-writer@sha256:')):
        raise Refused("copy receiver requires the pinned image and actual waiting main writer")
    actual_spec = copy.deepcopy(pod['spec'])
    # Only known Kubernetes Pod defaults absent from the reviewed template.
    defaults = {'dnsPolicy': 'ClusterFirst', 'schedulerName': 'default-scheduler', 'enableServiceLinks': True,
                'terminationGracePeriodSeconds': 30, 'securityContext': {}, 'priority': 0,
                'preemptionPolicy': 'PreemptLowerPriority', 'serviceAccountName': 'default', 'serviceAccount': 'default'}
    for key, value in defaults.items():
        if key not in source and key in actual_spec:
            if not helper.exact_json(actual_spec[key], value):
                raise Refused("Pod admission default changed " + key)
            del actual_spec[key]
    # Copy Jobs must pin their reviewed worker. Never silently discard nodeName.
    if not source.get('nodeName') or actual_spec.get('nodeName') != source['nodeName']:
        raise Refused("copy Pod does not use its reviewed pinned node")
    defaults = [{'key': 'node.kubernetes.io/not-ready', 'operator': 'Exists', 'effect': 'NoExecute', 'tolerationSeconds': 300},
                {'key': 'node.kubernetes.io/unreachable', 'operator': 'Exists', 'effect': 'NoExecute', 'tolerationSeconds': 300}]
    if 'tolerations' not in source and 'tolerations' in actual_spec:
        if not helper.exact_json(actual_spec['tolerations'], defaults):
            raise Refused("Pod tolerations differ from the known admission defaults")
        del actual_spec['tolerations']
    comparison = copy.deepcopy(job)
    comparison['spec']['template']['spec'] = actual_spec
    if not helper.declared_matches(ready, comparison):
        raise Refused("live Pod executable workload differs from the exact ready intent")
    statuses = pod.get('status', {}).get('containerStatuses', [])
    if (pod.get('status', {}).get('phase') != 'Running' or len(statuses) != 1
            or statuses[0].get('name') != container['name'] or statuses[0].get('restartCount') != 0
            or set(statuses[0].get('state', {})) != {'running'}):
        raise Refused("main writer is not currently running and waiting in its original container")
    image_id = statuses[0].get('imageID', '').removeprefix('docker-pullable://')
    if helper.image_identity(image_id) != helper.image_identity(container['image']):
        raise Refused("actual writer container image digest differs")
    environment = {'COPY_PHASE_TOKEN': phase, 'COPY_JOB_UID': uid, 'COPY_POD_UID': pod_uid,
                   'COPY_PROOF_HASHES_JSON': values.get('COPY_PROOF_HASHES_JSON', ''), 'COPY_DEADLINE_EPOCH': deadline or ''}
    gate(environment)
    return environment, ['kubectl', 'exec', '-i', '-n', ns, pm['name'], '-c', container['name'],
                         '--', 'nice', '-n', '19', 'python', '/copy-writer/proof_transport.py', 'receive']


def get(kind, name, namespace):
    result = subprocess.run(['kubectl', 'get', kind, name, '-n', namespace, '-o', 'json'],
                            capture_output=True, timeout=5, check=True)
    if len(result.stdout) > 2 * 1024 * 1024:
        raise Refused("live ownership response exceeds its bounded scope")
    return json.loads(result.stdout)


def stream_to_receiver(command, paths, phase, job_uid, pod_uid, deadline, popen=subprocess.Popen):
    if not 0 < deadline - time.time() <= 300:
        raise Refused("host proof delivery needs its current bounded absolute deadline")
    def expired(_signum, _frame):
        raise Refused("host proof sender deadline/signal stopped delivery")
    previous = {s: signal.signal(s, expired) for s in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT)}
    process = None
    try:
        signal.setitimer(signal.ITIMER_REAL, max(.001, deadline - time.time()))
        process = popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        send(process.stdin, paths, phase, job_uid, pod_uid)
        process.stdin.close(); process.stdin = None
        code = process.wait(timeout=max(.001, deadline - time.time()))
        if code:
            raise Refused("owned exec receiver refused the exact proof bundle")
    finally:
        # End the delivery alarm while performing bounded local-child cleanup.
        signal.setitimer(signal.ITIMER_REAL, 0)
        try:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=3)
                if process.stdin is not None:
                    process.stdin.close()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('checkpoint-helper', 'checkpoint-sha256', 'phase-state', 'restore-pr', 'namespace', 'job', 'pod', 'pod-uid',
                 'snapshot', 'selection', 'app-capture'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    helper = load_helper(args.checkpoint_helper, args.checkpoint_sha256)
    state = helper.read_json(args.phase_state); helper.validate_state(state, args.restore_pr)
    if state['complete'] or not state.get('window_started_at'):
        raise Refused("copy delivery requires the active bounded phase")
    rows = [r for r in state['owned_jobs'] if (r['namespace'], r['name']) == (args.namespace, args.job)]
    if len(rows) != 1:
        raise Refused("copy Job is not the single exact registered intent")
    row = rows[0]
    paths = {'snapshot.json': args.snapshot, 'selection.json': args.selection, 'app-capture.json': args.app_capture}
    # Validate all immutable local inputs against the ready manifest before exec.
    with open(os.devnull, 'wb') as sink:
        header = send(sink, paths, state['phase_token'], row['uid'], args.pod_uid)
    actual_job = get('job', args.job, args.namespace)
    actual_pod = get('pod', args.pod, args.namespace)
    environment, command = verify_target(helper, row, state['phase_token'], actual_job, actual_pod, args.pod_uid)
    expected = json.loads(environment['COPY_PROOF_HASHES_JSON'])
    if {r['name']: r['sha256'] for r in header['files']} != expected:
        raise Refused("local proof files differ from the durable ready manifest hashes")
    # Re-read the exact ledger after GETs; never transfer on a changed checkpoint.
    if helper.read_json(args.phase_state) != state:
        raise Refused("copy phase ledger changed during delivery preparation")
    gate(environment)
    stream_to_receiver(command, paths, state['phase_token'], row['uid'], args.pod_uid,
                       float(environment['COPY_DEADLINE_EPOCH']))
    print(json.dumps({'proof_receiver_complete': True, 'job_uid': row['uid'], 'pod_uid': args.pod_uid,
                      'phase_token': state['phase_token'], 'bytes': sum(r['bytes'] for r in header['files'])}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'proof_delivery_refused': type(error).__name__}), file=sys.stderr)
        raise SystemExit(1)
