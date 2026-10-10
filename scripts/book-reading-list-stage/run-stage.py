#!/usr/bin/env python3
"""Execute one explicit reviewed phase; never creates Jobs or changes a workload."""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import select
import selectors
import stat
import subprocess
import time

ROOT = Path(__file__).resolve().parent
MAX_INPUT = 8 * 1024 * 1024
MAX_EVENT = 8 * 1024 * 1024
MAX_JOURNAL = 32 * 1024 * 1024


def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON key')
        value[key] = item
    return value


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def instant(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('original clock needs timezone')
    return parsed.timestamp()


def read_private(path, limit=MAX_INPUT):
    path = Path(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_mode & 0o077 or before.st_size > limit:
            raise ValueError('private input must be a bounded single-link 0600 regular file')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if len(raw) > limit or identity(before) != identity(after):
            raise ValueError('private input changed or exceeded bound')
    return raw, identity(before)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink, info.st_mode, info.st_uid, info.st_gid)


def native_get(path, deadline):
    remaining = min(1.25, deadline - time.time())
    if remaining <= 0:
        raise TimeoutError('native admission deadline expired')
    result = subprocess.run(['kubectl', '--request-timeout=1s', 'get', f'--raw={path}'],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=remaining, check=True)
    if len(result.stdout) > MAX_INPUT:
        raise ValueError('native object exceeded bound')
    return json.loads(result.stdout, object_pairs_hook=unique)


def name(value):
    if not isinstance(value, str) or len(value) > 253 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', value):
        raise ValueError('native resource name is invalid')
    return value


def admit_native(binding, deadline):
    namespace, pod_name = name(binding['namespace']), name(binding['podName'])
    rs_name, deploy_name = name(binding['replicaSetName']), name(binding['deploymentName'])
    pod = native_get(f'/api/v1/namespaces/{namespace}/pods/{pod_name}', deadline)
    rs = native_get(f'/apis/apps/v1/namespaces/{namespace}/replicasets/{rs_name}', deadline)
    deploy = native_get(f'/apis/apps/v1/namespaces/{namespace}/deployments/{deploy_name}', deadline)
    for obj, kind, uid in [(pod, 'Pod', binding['podUid']), (rs, 'ReplicaSet', binding['replicaSetUid']), (deploy, 'Deployment', binding['deploymentUid'])]:
        if obj.get('kind') != kind or obj['metadata']['uid'] != uid or obj['metadata'].get('deletionTimestamp'):
            raise ValueError('actual native object identity changed')
    if sha(canonical(pod['spec'])) != binding['podSpecSha256'] or sha(canonical(deploy['spec'])) != binding['deploymentSpecSha256']:
        raise ValueError('actual native workload spec changed')
    def owner(obj, kind, uid):
        refs = [r for r in obj['metadata'].get('ownerReferences', []) if r.get('controller')]
        return len(refs) == 1 and refs[0].get('kind') == kind and refs[0].get('uid') == uid
    if not owner(pod, 'ReplicaSet', binding['replicaSetUid']) or not owner(rs, 'Deployment', binding['deploymentUid']):
        raise ValueError('native controller chain changed')
    statuses = [s for s in pod['status'].get('containerStatuses', []) if s['name'] == binding['container']]
    containers = [c for c in pod['spec']['containers'] if c['name'] == binding['container']]
    if (pod['status'].get('phase') != 'Running' or len(statuses) != 1 or len(containers) != 1
            or not statuses[0].get('ready') or not statuses[0].get('started')
            or statuses[0].get('restartCount') != binding['restartCount']
            or statuses[0].get('imageID') != binding['imageID'] or containers[0].get('image') != binding['image']):
        raise ValueError('actual container readiness/restart/image changed')


def write_event(directory, sequence, raw):
    return write_named(directory, f'{sequence:06d}.json', raw)


def write_named(directory, filename, raw):
    path = directory / filename
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def bundle_worker():
    protocol = base64.b64encode((ROOT / 'protocol.mjs').read_bytes()).decode()
    source = (ROOT / 'worker.mjs').read_text()
    marker = "from './protocol.mjs';"
    if source.count(marker) != 1:
        raise ValueError('worker import boundary changed')
    source = source.replace(marker, f"from 'data:text/javascript;base64,{protocol}';")
    if len(source.encode()) > 120 * 1024:
        raise ValueError('worker argv exceeded native bound')
    return source


def helper_bindings(source=None):
    """Bind the exact reviewed host, protocol, child and transported bundle."""
    source = bundle_worker() if source is None else source
    return {'launcherSha256': sha(Path(__file__).read_bytes()),
            'protocolSha256': sha((ROOT / 'protocol.mjs').read_bytes()),
            'workerSha256': sha((ROOT / 'worker.mjs').read_bytes()),
            'bundleSha256': sha(source.encode())}


def check_helpers(approval, source=None):
    if helper_bindings(source) != approval['helpers']:
        raise ValueError('reviewed stage helper bytes changed')


def execute(approval, directory, artifact_identities):
    start = time.time()
    expiry = min(instant(approval['capturedAt']) + 300, start + 180)
    source = bundle_worker()
    check_helpers(approval, source)
    admit_native(approval['native'], min(expiry, time.time() + 4))
    worker_sha = sha(source.encode())
    namespace, pod = approval['native']['namespace'], approval['native']['podName']
    stderr_path = directory / 'stderr.private'
    with open(stderr_path, 'xb') as stderr:
        os.chmod(stderr_path, 0o600)
        child = subprocess.Popen(['kubectl', 'exec', '-i', '-n', namespace, pod, '-c', approval['native']['container'],
                                  '--', 'nice', '-n', '19', 'node', '--input-type=module', '-e', source],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr)
        try:
            os.set_blocking(child.stdin.fileno(), False)
            os.set_blocking(child.stdout.fileno(), False)
            selector = selectors.DefaultSelector()
            selector.register(child.stdout, selectors.EVENT_READ)
            def send(raw):
                sent = 0
                while sent < len(raw):
                    if time.time() >= expiry:
                        raise TimeoutError('original stage expiry reached during input delivery')
                    _, writable, _ = select.select([], [child.stdin], [], min(1, expiry - time.time()))
                    if writable:
                        sent += os.write(child.stdin.fileno(), raw[sent:])
            send(json.dumps({'approval': approval, 'workerSha256': worker_sha,
                             'until': dt.datetime.fromtimestamp(expiry, dt.timezone.utc).isoformat()}, ensure_ascii=False, separators=(',', ':')).encode() + b'\n')
            pending = b''
            previous, sequence, total, complete, eof = '0' * 64, 0, 0, False, False
            while not eof:
                remaining = expiry - time.time()
                if remaining <= 0:
                    raise TimeoutError('original stage expiry reached; outcome unknown')
                for key, _ in selector.select(min(1, remaining)):
                    raw = os.read(key.fd, 65_536)
                    if not raw:
                        eof = True
                        break
                    pending += raw
                    if len(pending) > MAX_EVENT:
                        raise ValueError('journal event exceeded bound')
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        total += len(line)
                        if total > MAX_JOURNAL:
                            raise ValueError('journal exceeded total bound')
                        frame = json.loads(line, object_pairs_hook=unique)
                        if frame.get('type') == 'fatal':
                            write_event(directory, sequence + 1, line)
                            raise ValueError('remote child refused; retained journal requires review')
                        raw_event = frame['event'].encode()
                        event = json.loads(raw_event, object_pairs_hook=unique)
                        digest = sha(raw_event)
                        if (frame.get('needsAck') is not True or digest != frame['sha256'] or event['phase'] != approval['phase']
                                or event['seq'] != sequence + 1 or event['previous'] != previous
                                or event['type'] not in ['before', 'intent', 'response', 'readback', 'complete']):
                            raise ValueError('journal phase/hash/sequence differs')
                        sequence += 1
                        write_event(directory, sequence, line)
                        if event['type'] in ['before', 'intent']:
                            check_helpers(approval)
                            admission_end = min(expiry, time.time() + 4)
                            for path, expected in artifact_identities.items():
                                if identity(os.stat(path, follow_symlinks=False)) != expected:
                                    raise ValueError('private source artifact changed before ACK')
                            admit_native(approval['native'], admission_end)
                        if event['type'] == 'before' and event['data']['workerSha256'] != worker_sha:
                            raise ValueError('actual child source identity differs')
                        send(json.dumps({'phase': approval['phase'], 'seq': sequence, 'sha256': digest}, separators=(',', ':')).encode() + b'\n')
                        previous = digest
                        complete = event['type'] == 'complete'
            child.stdin.close()
            status = child.wait(timeout=max(0.1, min(5, expiry - time.time())))
            if pending or status != 0 or not complete:
                raise ValueError('child completion unknown; no automatic retry')
            result = {'schema': 1, 'phase': approval['phase'], 'stage': approval['stage'], 'completed': True,
                      'recipeCount': len(approval['scopes']), 'childExit': status, 'journalEvents': sequence,
                      'finalEventSha256': previous, 'workerSha256': worker_sha}
            write_named(directory, 'completion.json', canonical(result))
            return result
        finally:
            if child.stdin and not child.stdin.closed:
                child.stdin.close()
            if child.poll() is None:
                # Terminate only the local transport. Remote child owns its absolute timer;
                # EOF/ACK refusal prevents another request. Never kill the service.
                child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approval', type=Path)
    parser.add_argument('--approval-sha256')
    parser.add_argument('--journal-dir', type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--print-helper-bindings', action='store_true')
    args = parser.parse_args()
    if args.print_helper_bindings:
        if args.approval or args.approval_sha256 or args.journal_dir or args.execute:
            raise ValueError('helper binding output cannot execute or admit a phase')
        print(json.dumps(helper_bindings(), sort_keys=True))
        return
    if args.approval is None or args.approval_sha256 is None:
        raise ValueError('exact reviewed approval and SHA are required')
    raw, _ = read_private(args.approval)
    if sha(raw) != args.approval_sha256:
        raise ValueError('exact reviewed approval SHA differs')
    approval = json.loads(raw, object_pairs_hook=unique)
    check_helpers(approval)
    # Use the same validator/canonical recipe semantics as the actual child.
    validator = "import {validateApproval} from './protocol.mjs'; let raw=''; for await (const c of process.stdin) raw+=c; validateApproval(JSON.parse(raw));"
    subprocess.run(['node', '--input-type=module', '-e', validator], cwd=ROOT, input=raw,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=5)
    artifact_identities = {}
    physical_proofs = {}
    proof_shas = {scope.get('physicalProofArtifactSha256') for scope in approval['scopes']
                  if scope.get('canonicalPolicy') is not None}
    for artifact in approval['artifacts'] + ([approval['initialReceipt']] if approval.get('initialReceipt') else []):
        value, info = read_private(artifact['path'], 32 * 1024 * 1024)
        if sha(value) != artifact['sha256']:
            raise ValueError('reviewed artifact bytes differ')
        if artifact is approval.get('initialReceipt'):
            receipt = json.loads(value, object_pairs_hook=unique)
            if (receipt.get('schema') != 1 or receipt.get('stage') != 'initial'
                    or receipt.get('completed') is not True or receipt.get('recipeCount') != 1 or receipt.get('childExit') != 0):
                raise ValueError('initial receipt is not an actually completed one-recipe stage')
        artifact_identities[artifact['path']] = info
        if artifact['sha256'] in proof_shas:
            physical_proofs[artifact['sha256']] = json.loads(value, object_pairs_hook=unique)
    if proof_shas:
        validator = "import {validatePhysicalProofArtifacts} from './protocol.mjs'; let raw=''; for await (const c of process.stdin) raw+=c; const v=JSON.parse(raw); validatePhysicalProofArtifacts(v.approval,v.artifacts);"
        subprocess.run(['node', '--input-type=module', '-e', validator], cwd=ROOT,
                       input=canonical({'approval': approval, 'artifacts': physical_proofs}),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=5)
    if not args.execute:
        print(json.dumps({'validated': True, 'clusterCalls': 0, 'runtimeWrites': 0, 'phase': approval['phase']}))
        return
    if args.journal_dir is None or not args.journal_dir.is_absolute():
        raise ValueError('a new absolute private journal directory is required')
    os.mkdir(args.journal_dir, 0o700)
    print(json.dumps(execute(approval, args.journal_dir, artifact_identities)))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # No raw subprocess/vendor error or private payload reaches terminal output.
        print(json.dumps({'completed': False, 'outcome': 'unknown', 'automaticRetry': False, 'reason': 'stage refused; inspect retained private evidence'}))
        raise SystemExit(2)
