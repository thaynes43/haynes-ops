#!/usr/bin/env python3
"""Prepared exact MAIN stat bridge and proof transfer. No Job creation or PG socket."""
import argparse
import contextlib
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

BASE = Path(__file__).parent
BUNDLE = BASE / 'runtime-modules'
PACKET = BASE
HELPER = Path(__file__).parent / 'checkpoint-copy-job.py'
HELPER_SHA = 'c0894d655c677a5f3ba41911d0efb6f43310dc63f451530bba9f402d328046e0'
SENDER_SHA = '30ca6db3c0b82257d7196706794f6e107945fb17d36cb7f44cfd7d9ece9cadf4'
BRIDGE_SHA = 'e5ba6985ab4e68d8418b160f092c00429090831a869036afc6b8d649e4ba45ec'
TRANSPORT_SHA = '096ba710805623638b87d8c31f6be2653fe57ae0377bf35a27dabf014755e8a5'
MAIN = ('frontend', 'issue831-copy-selected-1009-03')
SHA = re.compile(r'[0-9a-f]{64}')
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
KEYS = {'schema', 'phase_state', 'restore_pr', 'expected_phase_token', 'main_pod_name',
        'main_pod_uid', 'source_library', 'snapshot', 'selection', 'app_capture',
        'publisher_proof', 'first_service_stop_observed_at'}
CAPS = {'source_library': 16*1024*1024, 'snapshot': 16*1024*1024,
        'selection': 1024*1024, 'app_capture': 32*1024*1024, 'publisher_proof': 16*1024*1024}
FILE_KEYS = {'snapshot.json': 'snapshot', 'selection.json': 'selection', 'app-capture.json': 'app_capture'}


class Refused(RuntimeError):
    pass


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def epoch(value):
    if not isinstance(value, str):
        raise Refused('UTC source timestamp required')
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() != dt.timedelta(0):
        raise Refused('UTC source timestamp required')
    return result.timestamp()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise Refused('duplicate JSON key')
        result[key] = value
    return result


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def read_private(path, cap, expected=None):
    if not isinstance(path, str) or not path.startswith('/') or any(p in ('.', '..') for p in path.split('/')):
        raise Refused('absolute confined artifact path required')
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    descriptor = None
    try:
        parts = path.split('/')[1:]
        if not parts or any(not p for p in parts):
            raise Refused('normalized absolute artifact path required')
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, 'rb') as handle:
            descriptor = None
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > cap:
                raise Refused('proof artifact is nonregular, linked or too large')
            raw = handle.read(cap + 1)
            after = os.fstat(handle.fileno())
            current = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
            def identity(info):
                return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                        info.st_ctime_ns, info.st_nlink, info.st_mode, info.st_uid, info.st_gid)
            if len(raw) > cap or identity(before) != identity(after) or identity(before) != identity(current):
                raise Refused('proof artifact changed during bounded read')
        sha = digest(raw)
        if expected is not None and (not isinstance(expected, str) or not SHA.fullmatch(expected) or sha != expected):
            raise Refused('immutable proof artifact SHA differs')
        return json.loads(raw, object_pairs_hook=unique), sha, raw
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def load_module(name, path, expected):
    raw = Path(path).read_bytes()
    if len(raw) > 1024*1024 or digest(raw) != expected:
        raise Refused('reviewed component pin changed: ' + name)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def components():
    load_module('proof_transport', BUNDLE/'proof_transport.py', TRANSPORT_SHA)
    # The imported module must be exactly the pinned receiver/send implementation.
    sys.path.insert(0, str(BUNDLE))
    import proof_transport
    if digest(Path(proof_transport.__file__).read_bytes()) != TRANSPORT_SHA:
        raise Refused('proof transport import pin changed')
    helper = load_module('main_delivery_checkpoint', HELPER, HELPER_SHA)
    sender = load_module('main_delivery_sender', PACKET/'proof_sender.py', SENDER_SHA)
    bridge = load_module('main_delivery_stat', Path(__file__).parent/'check-main-source-identity.py', BRIDGE_SHA)
    return helper, sender, bridge


def frozen_deadline(contract, publisher, now):
    if publisher.get('schema') != 1 or publisher.get('read_only') is not True or publisher.get('complete') is not True:
        raise Refused('complete publisher source proof required')
    first = epoch(contract['first_service_stop_observed_at'])
    start, end = epoch(publisher.get('capture_started_at')), epoch(publisher.get('captured_at'))
    if not first <= now or not start <= end <= now:
        raise Refused('actual stop/publisher capture ordering differs')
    deadline = math.floor(min(first + 250, start + 65, end + 35))
    if deadline <= now:
        raise Refused('frozen publisher/maintenance lease expired')
    return deadline


class Budget:
    def __init__(self, end):
        self.end = end
        self.previous = {}

    def remaining(self):
        remaining = self.end - time.time()
        if remaining <= 0:
            raise Refused('delivery absolute deadline or 12-second host cap expired')
        return remaining

    def arm(self):
        signal.setitimer(signal.ITIMER_REAL, self.remaining())

    def __enter__(self):
        def stopped(*_):
            raise Refused('delivery deadline/signal stopped the component')
        self.previous = {s: signal.signal(s, stopped) for s in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT)}
        self.arm()
        return self

    def __exit__(self, *_):
        signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, handler in self.previous.items():
            signal.signal(sig, handler)


class Journal:
    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)

    def emit(self, event, **fields):
        raw = encoded({'schema': 1, 'event': event, 'at': stamp(), **fields}) + b'\n'
        with os.fdopen(os.dup(self.fd), 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())

    def close(self):
        os.close(self.fd)


def publish_bytes(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


class BoundedChild:
    """Keep the frozen sender's final child wait inside the same host cap."""
    def __init__(self, budget, *args, **kwargs):
        self.budget = budget
        self.child = subprocess.Popen(*args, **kwargs)

    @property
    def stdin(self):
        return self.child.stdin

    @stdin.setter
    def stdin(self, value):
        self.child.stdin = value

    def poll(self):
        return self.child.poll()

    def kill(self):
        self.child.kill()

    def wait(self, timeout):
        if self.child.poll() is not None:
            return self.child.returncode
        return self.child.wait(timeout=min(timeout, self.budget.remaining()))


def validate_inputs(contract, now):
    if (not isinstance(contract, dict) or set(contract) != KEYS or type(contract.get('schema')) is not int
            or contract['schema'] != 1 or not isinstance(contract['restore_pr'], str)
            or not re.fullmatch(r'[0-9]+', contract['restore_pr'])
            or not re.fullmatch(r'[0-9a-f]{32}', contract['expected_phase_token'])
            or not isinstance(contract['main_pod_name'], str)
            or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', contract['main_pod_name'])
            or not UUID.fullmatch(contract['main_pod_uid'])):
        raise Refused('delivery contract fields or exact phase/MAIN identity invalid')
    records, artifacts = {}, {}
    for key, cap in CAPS.items():
        record = contract[key]
        if not isinstance(record, dict) or set(record) != {'path', 'sha256'}:
            raise Refused('artifact path/SHA contract differs')
        value, sha, raw = read_private(record['path'], cap, record['sha256'])
        if not isinstance(value, dict):
            raise Refused('proof artifact must be an object')
        records[key], artifacts[key] = value, {'path': record['path'], 'sha256': sha, 'bytes': len(raw)}
    deadline = frozen_deadline(contract, records['publisher_proof'], now)
    selection = records['selection']
    if selection.get('snapshot_sha256') != artifacts['snapshot']['sha256']:
        raise Refused('selection is not bound to the exact transported snapshot')
    files = records['source_library'].get('files')
    if not isinstance(files, list):
        raise Refused('complete source EPUB records missing')
    hashes = {}
    for row in files:
        if not isinstance(row, dict) or row.get('path') in hashes or not SHA.fullmatch(row.get('sha256', '')):
            raise Refused('source census path/hash malformed or duplicated')
        hashes[row['path']] = row['sha256']
    entries = selection.get('entries')
    if not isinstance(entries, list) or not entries:
        raise Refused('exact nonempty published selection required')
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {'path', 'sha256', 'keeper', 'keeper_sha256'}
                or hashes.get(entry['path']) != entry['sha256']
                or hashes.get(entry['keeper']) != entry['keeper_sha256']):
            raise Refused('selected source/keeper differs from complete source hash census')
    # Published preflight and MAIN bind canonical decoded app JSON, while the
    # receiver separately binds the exact raw transport bytes, including LF.
    app_digest = digest(json.dumps(records['app_capture'], sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())
    if records['snapshot'].get('app_wants', {}).get('source_sha256') != app_digest:
        raise Refused('snapshot differs from the transported complete app capture')
    return records, artifacts, deadline


def state_inputs(helper, contract):
    # The frozen helper returns (object, raw SHA), never an object alone.
    state, state_sha = helper.read_json(contract['phase_state'])
    helper.validate_state(state, contract['restore_pr'])
    if (state['complete'] or not state.get('window_started_at')
            or state['phase_token'] != contract['expected_phase_token']
            or epoch(state['window_started_at']) != epoch(contract['first_service_stop_observed_at'])
            or not 0 <= time.time() - epoch(state['heartbeat']) <= 15):
        raise Refused('phase is foreign, closed, unstaged or heartbeat stale')
    rows = [r for r in state['owned_jobs'] if (r['namespace'], r['name']) == MAIN]
    if len(rows) != 1 or rows[0]['uid'] is None or rows[0]['ready_manifest'] is None:
        raise Refused('exact MAIN actual UID/ready intent missing')
    return state, state_sha, rows[0]


def source_lease(helper, state, minimum_deadline):
    lease = next(l for l in state['pg_leases'] if (l['job_namespace'], l['job_name']) == helper.SOURCE)
    if any(lease[k] is None for k in ('job_uid', 'pod_uid', 'backend_pid')):
        raise Refused('source actual Job/Pod/backend PID must be recorded before delivery')
    row = next(r for r in state['owned_jobs'] if (r['namespace'], r['name']) == helper.SOURCE)
    pod = helper.lookup_lease_pod(row, lease['pod_uid'])
    events = helper.owned_log_events(row['namespace'], pod['metadata']['name'],
                                    row['ready_manifest']['spec']['template']['spec']['containers'][0]['name'])
    if any(e.get('type') == 'fence_failed' for e in events):
        raise Refused('actual source reported its owning PG fence failure')
    health = [e for e in events if e.get('type') == 'fence_healthy']
    if not health:
        raise Refused('actual source fence health log missing')
    latest = max(health, key=lambda e: epoch(e.get('at')))
    values = {e['name']: e.get('value') for e in row['ready_manifest']['spec']['template']['spec']['containers'][0]['env']}
    source_deadline = float(values['COPY_SOURCE_DEADLINE_EPOCH'])
    if (not math.isfinite(source_deadline) or source_deadline < minimum_deadline
            or latest.get('deadline_epoch_ms') != int(source_deadline*1000)):
        raise Refused('actual source own-PG lifetime does not cover frozen MAIN deadline')
    helper.require_live_source_lease(state, {'pg_backend_pid': lease['backend_pid'], 'pg_health_at': latest['at']})
    return {'namespace': row['namespace'], 'job_name': row['name'], 'job_uid': lease['job_uid'],
            'pod_name': pod['metadata']['name'], 'pod_uid': lease['pod_uid'],
            'backend_pid': lease['backend_pid'], 'application_name': lease['application_name'],
            'pg_health_at': latest['at']}


def main_target(helper, sender, state, row, contract, hashes, deadline, after=False):
    job = sender.get('job', MAIN[1], MAIN[0])
    pod = sender.get('pod', contract['main_pod_name'], MAIN[0])
    helper.verify_owned_pod(row, job, pod, contract['main_pod_uid'], allow_completed=after)
    values = {e['name']: e.get('value') for e in row['ready_manifest']['spec']['template']['spec']['containers'][0]['env']}
    if (values.get('COPY_DEADLINE_EPOCH') != str(deadline)
            or json.loads(values['COPY_PROOF_HASHES_JSON'], object_pairs_hook=unique) != hashes):
        raise Refused('frozen deadline or complete proof hashes differ from exact ready MAIN')
    if not after:
        environment, command = sender.verify_target(helper, row, state['phase_token'], job, pod, contract['main_pod_uid'])
    else:
        environment, command = None, None
    return environment, command, {'job_uid': row['uid'], 'pod_uid': contract['main_pod_uid'],
                                'pod_name': contract['main_pod_name'], 'pod_spec_sha256': digest(encoded(pod['spec'])),
                                'job_spec_sha256': digest(encoded(job['spec'])), 'pod_phase': pod['status']['phase']}


def bounded_capture(argv, program, budget, stdout, stderr,
                    stdout_cap=16*1024*1024, stderr_cap=64*1024):
    """Read both child pipes with finite buffers before allocating excess output."""
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, bufsize=0)
    position = 0
    try:
        with selectors.DefaultSelector() as selector:
            for pipe, events, name in ((child.stdin, selectors.EVENT_WRITE, 'stdin'),
                                       (child.stdout, selectors.EVENT_READ, 'stdout'),
                                       (child.stderr, selectors.EVENT_READ, 'stderr')):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, events, name)
            while selector.get_map():
                for key, _ in selector.select(timeout=budget.remaining()):
                    pipe, name = key.fileobj, key.data
                    if name == 'stdin':
                        try:
                            position += os.write(pipe.fileno(), program[position:position+65536])
                        except BrokenPipeError:
                            position = len(program)
                        if position == len(program):
                            selector.unregister(pipe)
                            pipe.close()
                        continue
                    target, cap = (stdout, stdout_cap) if name == 'stdout' else (stderr, stderr_cap)
                    chunk = os.read(pipe.fileno(), min(65536, cap - len(target) + 1))
                    if not chunk:
                        selector.unregister(pipe)
                        pipe.close()
                        continue
                    room = cap - len(target)
                    target.extend(chunk[:room])
                    if len(chunk) > room:
                        raise Refused('exact owned MAIN stat ' + name + ' exceeded output cap')
            return child.wait(timeout=budget.remaining())
    finally:
        if child.poll() is None:
            child.kill()
            remaining = budget.end - time.time()
            if remaining > 0:
                try:
                    child.wait(timeout=min(0.1, remaining))
                except subprocess.TimeoutExpired:
                    pass
        for pipe in (child.stdin, child.stdout, child.stderr):
            if pipe is not None:
                pipe.close()


def main_stat(command, source_bytes, selected_paths, budget, evidence_prefix):
    # Only a pinned program and bounded literal data travel on stdin, never large env/argv.
    program = (b"namespace={'__name__':'main_stat_component'}\nexec(" + repr(source_bytes).encode()
               + b",namespace)\nnamespace['run'](host_deadline=" + repr(budget.end).encode()
               + b',selected_paths=' + repr(selected_paths).encode() + b')\n')
    if len(program) > 128*1024:
        raise Refused('bounded stat program/selection stdin exceeds cap')
    argv = command[:command.index('--')+1] + ['nice', '-n', '19', 'python', '-']
    stdout_path, stderr_path = evidence_prefix + '.main-stat.stdout.json', evidence_prefix + '.main-stat.stderr'
    stdout, stderr = bytearray(), bytearray()
    try:
        code = bounded_capture(argv, program, budget, stdout, stderr)
    finally:
        publish_bytes(stdout_path, stdout)
        publish_bytes(stderr_path, stderr)
    if code:
        raise Refused('exact owned MAIN stat bridge refused; bounded output preserved')
    value = json.loads(stdout, object_pairs_hook=unique)
    return value, {'argv': argv, 'stdin_sha256': digest(program), 'stdout_sha256': digest(stdout),
                   'stderr_sha256': digest(stderr), 'exit_code': code,
                   'stdout_path': stdout_path, 'stderr_path': stderr_path}


def deliver(contract, journal, helper, sender, bridge, budget):
    records, artifacts, deadline = validate_inputs(contract, time.time())
    budget.end = min(budget.end, deadline)
    budget.arm()
    state, state_sha, row = state_inputs(helper, contract)
    budget.end = min(budget.end, epoch(state['heartbeat']) + 15)
    budget.arm()
    capture = records['app_capture']
    source_before = source_lease(helper, state, deadline)
    if (capture.get('phase_token'), capture.get('job_uid'), capture.get('pod_uid'), capture.get('lock_backend_pid'),
            capture.get('lock_application_name')) != (state['phase_token'], source_before['job_uid'], source_before['pod_uid'],
                                                      source_before['backend_pid'], source_before['application_name']):
        raise Refused('transported app capture differs from actual source lease identity')
    hashes = {name: artifacts[key]['sha256'] for name, key in FILE_KEYS.items()}
    environment, receive_command, main_before = main_target(helper, sender, state, row, contract, hashes, deadline)
    journal.emit('delivery_bound', phase_token=state['phase_token'], state_sha256=state_sha,
                 deadline_epoch=str(deadline), host_delivery_deadline_epoch=budget.end,
                 main=main_before, source=source_before, artifacts=artifacts)
    selected = sorted({entry[key] for entry in records['selection']['entries'] for key in ('path', 'keeper')})
    source_bytes = (Path(__file__).parent/'check-main-source-identity.py').read_bytes()
    if digest(source_bytes) != BRIDGE_SHA:
        raise Refused('stat source changed after component load')
    actual, execution = main_stat(receive_command, source_bytes, selected, budget, journal.path)
    if (actual.get('phase_token'), actual.get('job_uid'), actual.get('pod_uid'), actual.get('main_deadline_epoch'),
            actual.get('host_delivery_deadline_epoch'), actual.get('runtime_module_sha256')) != (
            state['phase_token'], row['uid'], contract['main_pod_uid'], str(deadline), budget.end, bridge.PINS):
        raise Refused('actual stat response does not bind exact MAIN/runtime/deadlines')
    if not epoch(capture['capture_started_at']) <= epoch(records['source_library']['started_at']) <= epoch(actual['capture_started_at']) <= epoch(actual['captured_at']) <= time.time():
        raise Refused('actual source/stat capture start ordering differs')
    bridge.compare(records['source_library'], actual, records['selection'])
    journal.emit('main_stat_verified', complete_file_count=len(actual['all_file_fingerprints']),
                 selected_path_count=len(selected), capture_started_at=actual['capture_started_at'],
                 captured_at=actual['captured_at'], actual_stat_sha256=digest(encoded(actual)), execution=execution)
    # All identities and file hashes are checked again before the first proof byte.
    for key, cap in CAPS.items():
        read_private(artifacts[key]['path'], cap, artifacts[key]['sha256'])
    source_ready = source_lease(helper, state, deadline)
    _, command, main_ready = main_target(helper, sender, state, row, contract, hashes, deadline)
    if (main_ready['pod_spec_sha256'], main_ready['job_spec_sha256']) != (main_before['pod_spec_sha256'], main_before['job_spec_sha256']):
        raise Refused('actual MAIN spec changed between stat bridge and transport')
    if helper.read_json(contract['phase_state']) != (state, state_sha):
        raise Refused('immutable COPY phase ledger changed before transport')
    budget.remaining()
    paths = {name: artifacts[key]['path'] for name, key in FILE_KEYS.items()}
    with open(os.devnull, 'wb') as sink:
        header = sender.send(sink, paths, state['phase_token'], row['uid'], contract['main_pod_uid'])
    if {r['name']: r['sha256'] for r in header['files']} != hashes:
        raise Refused('actual transport header differs from approved proof hashes')
    journal.emit('delivery_started', main=main_ready, source=source_ready, header=header,
                 main_deadline_epoch=str(deadline), transport_deadline_epoch=budget.end)
    sender.stream_to_receiver(command, paths, state['phase_token'], row['uid'], contract['main_pod_uid'], budget.end,
                              popen=lambda *args, **kwargs: BoundedChild(budget, *args, **kwargs))
    # The sender clears its alarm during child cleanup. Re-arm the same remaining cap.
    budget.arm()
    _, _, main_after = main_target(helper, sender, state, row, contract, hashes, deadline, after=True)
    source_after = source_lease(helper, state, deadline)
    if helper.read_json(contract['phase_state']) != (state, state_sha):
        raise Refused('immutable COPY phase ledger changed during transport')
    budget.remaining()
    journal.emit('delivery_completed', proof_receiver_complete=True, main=main_after, source=source_after,
                 phase_token=state['phase_token'], main_deadline_epoch=str(deadline), header=header,
                 main_pg_fence_recorded_before_moves=False)
    return {'proof_receiver_complete': True, 'phase_token': state['phase_token'], 'main': main_after,
            'source': source_after, 'deadline_epoch': str(deadline), 'bytes': sum(r['bytes'] for r in header['files']),
            'host_delivery_deadline_epoch': budget.end, 'main_pg_fence_recorded_before_moves': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contract', required=True)
    parser.add_argument('--receipt', required=True)
    args = parser.parse_args(argv)
    began = time.time()
    journal = Journal(args.receipt)
    try:
        journal.emit('delivery_start', contract_path=args.contract, host_started_at_epoch=began,
                     production_job_creations=0, production_pg_connections=0, corpus_content_reads=0)
        with Budget(began + 12) as budget:
            contract, sha, _ = read_private(args.contract, 64*1024)
            journal.emit('contract_bound', sha256=sha)
            helper, sender, bridge = components()
            result = deliver(contract, journal, helper, sender, bridge, budget)
        print(json.dumps(result, separators=(',', ':')))
    except BaseException as error:
        journal.emit('delivery_refused', error_class=type(error).__name__, reason=str(error)[:512], retry=False,
                     receiver_may_have_completed='inspect delivery_started and actual owned MAIN; never retry automatically')
        raise
    finally:
        journal.close()


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        print(json.dumps({'proof_delivery_refused': type(error).__name__, 'automatic_retry': False}), file=sys.stderr)
        raise SystemExit(2)
