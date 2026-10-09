#!/usr/bin/env python3
"""One explicitly ratified diagnostic Job and bounded actual-UID cleanup."""
import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
NS = 'frontend'
LABEL = 'issue825.haynesnetwork/phase'
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
MODULES = {'epub_copies.py': '31134ebfc895e4b8cd5f4f1e4a495572d31c165a42d1ded207da58f3469ee7b6',
           'epub_metadata.py': 'ce3c5a271cb4c94d91f3154240b4cfbc5e0cc57981969fc5c975a0c0f50eb773'}


class Refused(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise Refused(code)


def unique(rows):
    result = {}
    for key, value in rows:
        require(key not in result, 'duplicate_json_key')
        result[key] = value
    return result


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pinned(row, cap=2 * 1024 * 1024):
    require(isinstance(row, dict) and set(row) == {'path', 'sha256'} and Path(row['path']).is_absolute(), 'artifact_binding')
    fd = os.open(row['path'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= cap, 'artifact_type_or_cap')
        raw = source.read(cap + 1)
        after = os.fstat(source.fileno())
        current = os.stat(row['path'], follow_symlinks=False)
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mode', 'st_uid', 'st_gid', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink')
    require(len(raw) == before.st_size and all(getattr(before, field) == getattr(after, field) == getattr(current, field) for field in fields)
            and hashlib.sha256(raw).hexdigest() == row['sha256'], 'private_pin_differs')
    return raw


def source_paths():
    base = HERE.parent / 'live-baseline-host'
    return {'prepare': HERE / 'prepare.py', 'probe': HERE / 'probe.py', 'runner': HERE / 'run.py',
            'collector': base / 'dependencies/bound_census_collectors.py', 'host': base / 'run-live-byte-baseline.py',
            'helper': base / 'dependencies/checkpoint-copy-job.py', 'receiver': base / 'dependencies/receive-private-inputs.py',
            'verifier': base / 'dependencies/prepare-native-source-contract.py', 'template': base / 'live-baseline-closed-manifest.json'}


class Run:
    def __init__(self, packet_path, go):
        self.started, self.start_tick = stamp(), time.monotonic()
        epoch = time.time()
        self.end, self.total = epoch + 90, epoch + 200
        self.limit = self.end
        self.uid, self.pod_uid = None, None
        raw = pinned({'path': str(packet_path), 'sha256': go})
        self.packet = json.loads(raw, object_pairs_hook=unique)
        expected_sources = source_paths()
        require(set(self.packet['sources']) == set(expected_sources), 'source_closure_differs')
        for key, path in expected_sources.items():
            row = self.packet['sources'][key]
            require(row['path'] == str(path), 'source_path_differs')
            pinned(row)
        self.prepare = load('diagnostic_prepare', HERE / 'prepare.py')
        require(type(self.packet['schema']) is int and self.packet['schema'] == 1 and self.packet['prepared_only'] is True
                and self.packet['copy_eligible'] is False and self.packet['runtime_authorized'] is False
                and self.packet['bounds'] == {'diagnostic_seconds': 90, 'cleanup_seconds': 200,
                                             'sample_bytes': 64 * 1024 * 1024, 'cpu': '500m', 'memory': '256Mi'},
                'diagnostic_contract_differs')
        require(self.packet['kubeconfig_path'] == self.prepare.KUBECONFIG, 'cluster_configuration_differs')
        self.environ = {**os.environ, 'KUBECONFIG': self.prepare.KUBECONFIG}
        self.phase, self.name = self.packet['phase'], self.packet['name']
        require(re.fullmatch(r'[0-9a-f]{32}', self.phase) and self.name == self.prepare.NAME, 'diagnostic_identity_differs')
        self.ready = json.loads(pinned(self.packet['manifest']), object_pairs_hook=unique)
        env = {row['name']: row for row in self.ready['spec']['template']['spec']['containers'][0]['env']}
        require(self.ready == self.prepare.closed_manifest(env['COPY_DIAGNOSTIC_SAMPLE']['value'], self.phase),
                'closed_diagnostic_manifest_differs')
        for key in ('COPY_DIAGNOSTIC_PHASE_READY', 'COPY_SOURCE_PHASE_READY'):
            env[key]['value'] = '1'
        env['COPY_SOURCE_DEADLINE_EPOCH']['value'] = f'{self.end:.6f}'
        self.out = packet_path.parent / 'runtime'
        self.out.mkdir(mode=0o700)
        self.prepare.private(self.out / 'ready-manifest.json', self.ready)
        sources = self.packet['sources']
        self.host = load('diagnostic_inventory', Path(sources['host']['path']))
        self.helper = load('diagnostic_native_helper', Path(sources['helper']['path']))
        collector = load('bound_census_collectors', Path(sources['collector']['path']))
        sys.modules['bound_census_collectors'] = collector
        self.verifier = load('diagnostic_absence', Path(sources['verifier']['path']))
        self.result = {'schema': 1, 'started_at': self.started, 'job_uid': None, 'pod_uid': None,
                       'diagnostic_complete': False, 'copy_eligible': False,
                       'production_library_writes': 0, 'PG_operations': 0,
                       'cleanup_complete': False}

    def check(self):
        require(time.time() < self.limit, 'diagnostic_or_cleanup_deadline')

    def command(self, argv, payload=None, cleanup=False):
        self.check()
        try:
            result = subprocess.run(argv, input=payload, capture_output=True,
                                    env=self.environ,
                                    timeout=min(12, max(.001, self.limit - time.time())))
        except subprocess.TimeoutExpired:
            raise Refused('native_command_deadline') from None
        require(result.returncode == 0 and len(result.stdout) <= 4 * 1024 * 1024, 'native_command_refused')
        self.check()
        return result.stdout

    def inventory(self, kind):
        # Reuse the reviewed raw typed parent/item validation and normalization.
        return self.host.Run.inventory(self, kind)

    def matching_jobs(self, jobs):
        return [job for job in jobs['items'] if job['metadata']['name'] == self.name
                or job['metadata'].get('labels', {}).get(LABEL) == self.phase
                or self.uid is not None and job['metadata'].get('uid') == self.uid]

    def owned_pods(self, pods):
        result = []
        for pod in pods['items']:
            meta = pod['metadata']
            labels, owners = meta.get('labels', {}), meta.get('ownerReferences', [])
            selected = (labels.get(LABEL) == self.phase
                        or self.uid is not None and labels.get('batch.kubernetes.io/controller-uid') == self.uid
                        or any(owner.get('kind') == 'Job' and (owner.get('name') == self.name
                               or self.uid is not None and owner.get('uid') == self.uid) for owner in owners))
            if not selected:
                continue
            require(len(owners) == 1 and owners[0].get('kind') == 'Job' and owners[0].get('apiVersion') == 'batch/v1'
                    and owners[0].get('name') == self.name and owners[0].get('controller') is True
                    and isinstance(owners[0].get('uid'), str) and UUID.fullmatch(owners[0]['uid'])
                    and self.uid in (None, owners[0]['uid']) and labels.get(LABEL) == self.phase
                    and labels.get('batch.kubernetes.io/controller-uid') == owners[0]['uid']
                    and isinstance(meta.get('uid'), str) and UUID.fullmatch(meta['uid']), 'diagnostic_pod_reused')
            result.append(pod)
        require(len(result) <= 1, 'diagnostic_pod_duplicate')
        return result

    def current_job(self):
        return json.loads(self.command(['kubectl', 'get', 'job', self.name, '-n', NS, '-o', 'json']), object_pairs_hook=unique)

    def native_binding(self, job, pod):
        return self.verifier.bind(self.ready, job, pod, self.phase, MODULES,
                                  stamp(), self.helper, self.uid, pod['metadata']['uid'])

    def deliver_native(self, pod, native):
        raw = canonical(native) + b'\n'
        deadline = min(self.end, time.time() + 11)
        header = {'schema': 1, 'mode': 'SOURCE', 'phase_token': self.phase,
                  'job_uid': self.uid, 'pod_uid': self.pod_uid, 'deadline_epoch': f'{deadline:.6f}',
                  'files': [{'name': 'native-source-binding.json', 'bytes': len(raw),
                             'sha256': hashlib.sha256(raw).hexdigest()}]}
        code = pinned(self.packet['sources']['receiver']).decode()
        result = json.loads(self.command(['kubectl', 'exec', '-i', '-n', NS, pod['metadata']['name'], '-c', 'census',
                                          '--', 'nice', '-n', '19', 'python', '-B', '-c', code], canonical(header) + b'\n' + raw),
                            object_pairs_hook=unique)
        require(result.get('type') == 'private_inputs_ready' and result.get('mode') == 'SOURCE'
                and result.get('job_uid') == self.uid and result.get('pod_uid') == self.pod_uid
                and result.get('phase_token') == self.phase and result.get('production_writes') == 0,
                'native_input_receipt_differs')
        self.prepare.private(self.out / 'native-input-receipt.json', result)

    def collect(self):
        require(not self.matching_jobs(self.inventory('jobs')) and not self.owned_pods(self.inventory('pods')),
                'diagnostic_name_or_phase_exists')
        admitted = json.loads(self.command(['kubectl', 'create', '--dry-run=server', '-f', '-', '-o', 'json'], canonical(self.ready)), object_pairs_hook=unique)
        require(self.helper.declared_matches(self.ready, admitted), 'diagnostic_admission_differs')
        self.prepare.private(self.out / 'server-dry-run.json', admitted)
        created = json.loads(self.command(['kubectl', 'create', '-f', '-', '-o', 'json'], canonical(self.ready)), object_pairs_hook=unique)
        require(self.helper.declared_matches(self.ready, created), 'created_diagnostic_differs')
        self.uid = created['metadata']['uid']
        require(isinstance(self.uid, str) and UUID.fullmatch(self.uid), 'created_uid_invalid')
        self.result['job_uid'] = self.uid
        self.prepare.private(self.out / 'actual-created-job.json', created)
        while True:
            job = self.current_job()
            pods = self.owned_pods(self.inventory('pods'))
            require(not job.get('status', {}).get('failed'), 'diagnostic_job_failed')
            if pods and pods[0].get('status', {}).get('phase') == 'Running':
                break
            self.check()
            time.sleep(.25)
        pod = pods[0]
        self.pod_uid = pod['metadata']['uid']
        self.result['pod_uid'] = self.pod_uid
        native = self.native_binding(job, pod)
        self.prepare.private(self.out / 'actual-running-job.json', job)
        self.prepare.private(self.out / 'actual-running-pod.json', pod)
        self.prepare.private(self.out / 'native-source-binding.json', native)
        self.deliver_native(pod, native)
        # One log stream; no repeated reads or data-work reruns. Its process is
        # bounded by the same absolute clock; the container emits stage events.
        raw = self.harvest_logs(pod)
        require(len(raw) <= 65536, 'diagnostic_log_cap')
        events = [json.loads(line, object_pairs_hook=unique) for line in raw.splitlines()]
        require(all(event.get('copy_eligible') is False and event.get('production_writes') == 0 and event.get('PG_operations') == 0 for event in events), 'diagnostic_event_scope')
        completed = [event for event in events if event.get('type') == 'diagnostic-complete']
        require(len(completed) == 1 and not any(event.get('type') == 'diagnostic-refused' for event in events), 'diagnostic_refused')
        while True:
            job = self.current_job()
            pods = self.owned_pods(self.inventory('pods'))
            require(len(pods) == 1 and not job.get('status', {}).get('failed'), 'diagnostic_completion_owner_or_failure')
            if any(condition.get('type') == 'Complete' and condition.get('status') == 'True' for condition in job.get('status', {}).get('conditions', [])):
                break
            self.check()
            time.sleep(.25)
        self.verify_completed(job, pods[0], native)
        self.prepare.private(self.out / 'actual-complete-job.json', job)
        self.prepare.private(self.out / 'actual-complete-pod.json', pods[0])
        self.result.update(diagnostic_complete=True, events=events)

    def harvest_logs(self, pod):
        process = subprocess.Popen(['kubectl', 'logs', '-f', '-n', NS, pod['metadata']['name'], '-c', 'census',
                                    '--limit-bytes=65536'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environ)
        raw, failure = b'', None
        try:
            raw, _ = process.communicate(timeout=max(.001, self.end - time.time()))
        except BaseException as error:
            # SIGALRM raises Refused rather than TimeoutExpired. Save partial
            # phase evidence on either path; it is the purpose of this probe.
            failure = Refused('diagnostic_deadline') if isinstance(error, subprocess.TimeoutExpired) else error
            process.kill()
            raw, _ = process.communicate(timeout=2)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
        self.prepare.private(self.out / 'actual-log.jsonl', raw)
        if failure is not None:
            raise failure
        require(process.returncode == 0, 'diagnostic_log_transport')
        return raw

    def verify_completed(self, job, pod, native):
        expected = native['source_binding']
        jm, pm = job['metadata'], pod['metadata']
        require(jm.get('uid') == self.uid and jm.get('name') == self.name and jm.get('namespace') == NS
                and jm.get('labels', {}).get(LABEL) == self.phase and not jm.get('deletionTimestamp')
                and self.helper.declared_matches(self.ready, job), 'completed_diagnostic_job_differs')
        self.owned_pods({'items': [pod]})
        require(pm.get('uid') == expected['pod_uid'] and pm.get('name') == expected['pod_name']
                and not pm.get('deletionTimestamp') and hashlib.sha256(canonical(pod['spec'])).hexdigest() == expected['pod_spec_sha256'],
                'completed_diagnostic_pod_differs')
        statuses = pod.get('status', {}).get('containerStatuses', [])
        require(len(statuses) == 1, 'completed_container_count')
        status, conditions = statuses[0], job.get('status', {}).get('conditions', [])
        terminated = status.get('state', {}).get('terminated', {})
        require(status.get('name') == 'census' and status.get('restartCount') == 0
                and status.get('imageID', '').removeprefix('docker-pullable://') == expected['image_id']
                and set(status.get('state', {})) == {'terminated'} and type(terminated.get('exitCode')) is int
                and terminated['exitCode'] == 0 and terminated.get('reason') == 'Completed'
                and pod['status'].get('phase') == 'Succeeded' and job.get('status', {}).get('active', 0) == 0
                and job.get('status', {}).get('succeeded') == 1 and any(c.get('type') == 'Complete' and c.get('status') == 'True' for c in conditions)
                and not any(c.get('type') == 'Failed' and c.get('status') == 'True' for c in conditions), 'diagnostic_not_complete')

    def cleanup(self):
        self.limit = self.total
        jobs = self.matching_jobs(self.inventory('jobs'))
        require(len(jobs) <= 1, 'cleanup_diagnostic_job_duplicate')
        if jobs:
            job = jobs[0]
            meta = job['metadata']
            require(meta['name'] == self.name and meta.get('labels', {}).get(LABEL) == self.phase
                    and self.uid in (None, meta['uid']) and self.helper.declared_matches(self.ready, job), 'cleanup_diagnostic_job_reused')
            self.uid = meta['uid']
            self.result['job_uid'] = self.uid
            self.command(['kubectl', 'delete', '--raw', f'/apis/batch/v1/namespaces/{NS}/jobs/{self.name}', '-f', '-'],
                         canonical({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'propagationPolicy': 'Foreground',
                                    'preconditions': {'uid': self.uid}}))
        else:
            for pod in self.owned_pods(self.inventory('pods')):
                self.uid = pod['metadata']['ownerReferences'][0]['uid']
                self.result['job_uid'] = self.uid
                self.command(['kubectl', 'delete', '--raw', f"/api/v1/namespaces/{NS}/pods/{pod['metadata']['name']}", '-f', '-'],
                             canonical({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'propagationPolicy': 'Foreground',
                                        'preconditions': {'uid': pod['metadata']['uid']}}))
        while True:
            jobs, pods = self.inventory('jobs'), self.inventory('pods')
            if not self.matching_jobs(jobs) and not self.owned_pods(pods):
                break
            self.check()
            time.sleep(.5)
        if self.uid is not None:
            self.verifier.baseline_absent(jobs, pods, NS, self.name, self.uid, self.phase)
        self.prepare.private(self.out / 'actual-cleanup-jobs.json', jobs)
        self.prepare.private(self.out / 'actual-cleanup-pods.json', pods)
        self.result.update(cleanup_complete=True, job_and_all_owned_pods_absent=True, cleanup_verified_at=stamp())

    def execute(self):
        old = {}
        def stop(*_):
            raise Refused('diagnostic_host_alarm_or_signal')
        try:
            for sig in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT):
                old[sig] = signal.signal(sig, stop)
            signal.setitimer(signal.ITIMER_REAL, max(.001, self.end - time.time()))
            self.collect()
        except BaseException as error:
            self.result.update(error_class=type(error).__name__, error_code=str(error) if isinstance(error, Refused) else None)
        finally:
            signal.setitimer(signal.ITIMER_REAL, max(.001, self.total - time.time()))
            try:
                self.cleanup()
            except BaseException as error:
                self.result.update(cleanup_error_class=type(error).__name__, cleanup_error_code=str(error) if isinstance(error, Refused) else None)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                for sig, handler in old.items():
                    signal.signal(sig, handler)
        self.result.update(finished_at=stamp(), actual_seconds=time.monotonic() - self.start_tick)
        self.prepare.private(self.out / 'actual-receipt.json', self.result)
        print(json.dumps({key: value for key, value in self.result.items() if key != 'events'}, sort_keys=True))
        return 0 if self.result['diagnostic_complete'] and self.result['cleanup_complete'] else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--packet', type=Path, required=True)
    parser.add_argument('--go', required=True)
    arguments = parser.parse_args()
    require(arguments.packet.is_absolute() and re.fullmatch(r'[0-9a-f]{64}', arguments.go), 'explicit_packet_go_required')
    raise SystemExit(Run(arguments.packet, arguments.go).execute())
