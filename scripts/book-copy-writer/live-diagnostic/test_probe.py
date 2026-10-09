#!/usr/bin/env python3
"""Finite fixtures only; no cluster calls, corpus reads or stress."""
import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import sys
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / 'live-baseline-host'
REPO = HERE.parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = load('probe_fixture', HERE / 'probe.py')
prepare = load('prepare_fixture', HERE / 'prepare.py')
runner = load('runner_fixture', HERE / 'run.py')
collector = load('bound_census_collectors', BASE / 'dependencies/bound_census_collectors.py')
sys.modules['bound_census_collectors'] = collector
host = load('host_fixture', BASE / 'run-live-byte-baseline.py')
helper = load('helper_fixture', BASE / 'dependencies/checkpoint-copy-job.py')
verifier = load('verifier_fixture', BASE / 'dependencies/prepare-native-source-contract.py')
metadata = load('metadata_fixture', REPO / 'kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert/epub_metadata.py')
probe.configure(collector)
PHASE = 'a' * 32
JOB_UID = '11111111-1111-4111-8111-111111111111'
POD_UID = '22222222-2222-4222-8222-222222222222'


def admitted(ready):
    job = copy.deepcopy(ready)
    job['metadata']['uid'] = JOB_UID
    job['metadata']['resourceVersion'] = '123'
    defaults = json.loads((BASE / 'synthetic-admitted-job-defaults.json').read_bytes())['spec_defaults']
    job['spec'].update(defaults)
    job['spec']['selector'] = {'matchLabels': {'batch.kubernetes.io/controller-uid': JOB_UID}}
    labels = job['spec']['template']['metadata']['labels']
    labels.update({'batch.kubernetes.io/controller-uid': JOB_UID, 'controller-uid': JOB_UID,
                   'batch.kubernetes.io/job-name': prepare.NAME, 'job-name': prepare.NAME})
    pod = {'apiVersion': 'v1', 'kind': 'Pod',
           'metadata': {**copy.deepcopy(job['spec']['template']['metadata']), 'namespace': 'frontend',
                        'name': prepare.NAME + '-fixture', 'uid': POD_UID,
                        'ownerReferences': [{'apiVersion': 'batch/v1', 'kind': 'Job', 'name': prepare.NAME,
                                             'uid': JOB_UID, 'controller': True}]},
           'spec': copy.deepcopy(job['spec']['template']['spec']),
           'status': {'phase': 'Running', 'containerStatuses': [{'name': 'census', 'restartCount': 0,
                       'imageID': helper.image_identity(prepare.IMAGE), 'state': {'running': {'startedAt': runner.stamp()}}}]}}
    return job, pod


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.ready = prepare.closed_manifest('Synthetic/Work/Fixed.epub', PHASE)

    def test_permission_loop_is_unchanged_frozen_ast(self):
        def loop(path, name):
            tree = ast.parse(path.read_bytes())
            function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
            return next(node for node in function.body if isinstance(node, ast.For))
        self.assertEqual(ast.dump(loop(HERE / 'probe.py', 'permission_pass')),
                         ast.dump(loop(BASE / 'dependencies/bound_census_collectors.py', 'stat_census')))
        self.assertEqual(hashlib.sha256((BASE / 'dependencies/bound_census_collectors.py').read_bytes()).hexdigest(), probe.COLLECTOR_SHA)

    def test_profile_exact_bounds_and_original_readonly_source(self):
        self.assertEqual(prepare.sources(), runner.source_paths())
        pod = self.ready['spec']['template']['spec']
        container = pod['containers'][0]
        self.assertEqual(self.ready['spec']['activeDeadlineSeconds'], 90)
        self.assertEqual(self.ready['spec']['backoffLimit'], 0)
        self.assertEqual(pod['terminationGracePeriodSeconds'], 0)
        self.assertEqual(container['resources']['limits'], {'cpu': '500m', 'memory': '256Mi'})
        self.assertEqual(container['image'], prepare.IMAGE)
        self.assertEqual(pod['nodeName'], 'talosw01')
        self.assertFalse(pod['automountServiceAccountToken'])
        self.assertEqual(pod['volumes'][0]['nfs'], {'server': 'gasha01.haynesnetwork', 'path': '/hdd-nfs-repl'})
        self.assertTrue(container['volumeMounts'][0]['readOnly'])
        self.assertEqual(container['securityContext'], {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}})
        env = {row['name']: row for row in container['env']}
        self.assertEqual(env['COPY_DIAGNOSTIC_PHASE_READY']['value'], '0')
        self.assertEqual(env['COPY_SOURCE_PHASE_READY']['value'], '0')
        self.assertEqual(env['COPY_SOURCE_DEADLINE_EPOCH']['value'], '0')
        self.assertEqual(probe.MAX_SAMPLE_BYTES, 67108864)

    def test_embedded_program_closes_before_any_corpus_work(self):
        program = self.ready['spec']['template']['spec']['containers'][0]['command'][-1]
        result = subprocess.run([sys.executable, '-B', '-c', program],
                                env={**os.environ, 'COPY_DIAGNOSTIC_PHASE_READY': '0'},
                                capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b'Synthetic', result.stdout)
        self.assertIn(b'diagnostic-refused', result.stdout)

    def test_real_generic_native_binder_admits_diagnostic_profile(self):
        job, pod = admitted(self.ready)
        value = verifier.bind(self.ready, job, pod, PHASE, probe.MODULES, runner.stamp(), helper, JOB_UID, POD_UID)
        self.assertEqual(value['source_binding']['image_id'], helper.image_identity(prepare.IMAGE))
        self.assertEqual(value['source_binding']['node'], 'talosw01')
        self.assertEqual(value['module_sha256'], probe.MODULES)

    def test_real_binder_rejects_source_and_identity_changes(self):
        for change in ('writable_mount', 'wrong_node', 'restart', 'other_owner', 'wrong_image'):
            with self.subTest(change=change):
                job, pod = admitted(self.ready)
                if change == 'writable_mount': pod['spec']['containers'][0]['volumeMounts'][0]['readOnly'] = False
                elif change == 'wrong_node': pod['spec']['nodeName'] = 'talosw02'
                elif change == 'restart': pod['status']['containerStatuses'][0]['restartCount'] = 1
                elif change == 'other_owner': pod['metadata']['ownerReferences'][0]['uid'] = POD_UID
                else: pod['status']['containerStatuses'][0]['imageID'] = 'example.invalid/other@sha256:' + 'f' * 64
                with self.assertRaises((helper.Refused, verifier.Refused)):
                    verifier.bind(self.ready, job, pod, PHASE, probe.MODULES, runner.stamp(), helper, JOB_UID, POD_UID)

    def test_one_walk_and_permission_pass_on_finite_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'Fixed.epub').write_bytes(b'fixture')
            (root / '.hidden').write_bytes(b'fixture')
            before, dirs = collector.walk(str(root), time.monotonic() + 3, metadata, lambda: None)
            result = probe.permission_pass(str(root), time.monotonic() + 3, metadata, lambda: None, before, dirs)
            self.assertEqual(result['files_checked'], 2)
            self.assertEqual(len(dirs), 1)

    def test_prefix_reads_cap_without_hashing(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'Fixed.epub'
            path.write_bytes(b'a' * 100)
            expected = collector.fingerprint(path.stat())
            with patch.object(probe, 'MAX_SAMPLE_BYTES', 32), patch.object(probe.hashlib, 'sha256', side_effect=AssertionError('no digest')):
                result = probe.read_prefix(temporary, path.name, expected, time.monotonic() + 3, metadata, lambda: None)
            self.assertEqual(result['sample_bytes_read'], 32)
            self.assertEqual(result['sample_total_bytes'], 100)

    def test_prefix_refuses_changed_name_or_descriptor(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'Fixed.epub'
            replacement = Path(temporary) / 'replacement'
            path.write_bytes(b'a' * 100)
            replacement.write_bytes(b'b' * 100)
            expected = collector.fingerprint(path.stat())
            calls = 0
            def health():
                nonlocal calls
                calls += 1
                if calls == 2: os.replace(replacement, path)
            with self.assertRaises(metadata.Changed):
                probe.read_prefix(temporary, path.name, expected, time.monotonic() + 3, metadata, health)

    def test_prefix_refuses_symlink_and_expired_clock(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'target'
            target.write_bytes(b'fixture')
            path = Path(temporary) / 'Fixed.epub'
            path.symlink_to(target)
            expected = collector.fingerprint(path.lstat())
            with self.assertRaises(OSError):
                probe.read_prefix(temporary, path.name, expected, time.monotonic() + 3, metadata, lambda: None)
            with self.assertRaises(metadata.Refused):
                probe.read_prefix(temporary, path.name, expected, time.monotonic() - 1, metadata, lambda: None)

    def test_closed_main_reads_no_library_or_private_payload(self):
        output = io.StringIO()
        with patch.object(probe, 'load_modules', side_effect=AssertionError('must not import')), contextlib.redirect_stdout(output):
            self.assertEqual(probe.main(b'', {'COPY_DIAGNOSTIC_SAMPLE': 'Personal/Secret.epub'}), 2)
        self.assertNotIn('Personal', output.getvalue())
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(all(event['copy_eligible'] is False and event['production_writes'] == 0 for event in events))

    def test_preparation_is_private_unused_and_no_api(self):
        with tempfile.TemporaryDirectory() as temporary:
            sample = Path(temporary) / 'sample.txt'
            sample.write_text('Synthetic/Work/Fixed.epub\n')
            output = Path(temporary) / 'packet'
            with patch.object(runner.subprocess, 'run', side_effect=AssertionError('no API')):
                value = prepare.prepare(sample, output)
            self.assertFalse(value['runtime_authorized'])
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(output / 'packet.json').st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError): prepare.prepare(sample, output)

    def cleanup_fixture(self, changed_phase=False):
        run = runner.Run.__new__(runner.Run)
        run.ready, run.phase, run.name = self.ready, PHASE, prepare.NAME
        run.uid, run.pod_uid, run.result = None, None, {}
        run.total = time.time() + 200
        run.host, run.helper, run.verifier, run.prepare = host, helper, verifier, prepare
        job, _ = admitted(self.ready)
        job.pop('kind'); job.pop('apiVersion')
        if changed_phase: job['metadata']['labels'][prepare.LABEL] = 'b' * 32
        deleted, calls = False, []
        def command(argv, payload=None, cleanup=False):
            nonlocal deleted
            calls.append(argv)
            if argv[1] == 'delete':
                body = json.loads(payload)
                self.assertEqual(body['preconditions'], {'uid': JOB_UID})
                self.assertEqual(body['propagationPolicy'], 'Foreground')
                deleted = True
                return b'{}'
            is_jobs = argv[-1].endswith('/jobs')
            return json.dumps({'apiVersion': 'batch/v1' if is_jobs else 'v1', 'kind': 'JobList' if is_jobs else 'PodList',
                               'metadata': {'resourceVersion': '123'}, 'items': [] if deleted or not is_jobs else [job]}).encode()
        run.command = command
        return run, calls

    def test_real_normalization_helper_and_uid_cleanup(self):
        run, calls = self.cleanup_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            run.out = Path(temporary)
            run.cleanup()
            self.assertTrue(run.result['cleanup_complete'])
            self.assertEqual(run.result['job_uid'], JOB_UID)
            self.assertEqual(sum(argv[1] == 'delete' for argv in calls), 1)

    def test_cleanup_refuses_reused_name_without_delete(self):
        run, calls = self.cleanup_fixture(changed_phase=True)
        with tempfile.TemporaryDirectory() as temporary:
            run.out = Path(temporary)
            with self.assertRaises(runner.Refused): run.cleanup()
        self.assertFalse(any(argv[1] == 'delete' for argv in calls))

    def test_host_alarm_harvests_partial_stage_log_and_kills_child(self):
        class Process:
            returncode = None
            killed = False
            calls = 0
            def communicate(self, timeout):
                self.calls += 1
                if self.calls == 1: raise runner.Refused('diagnostic_host_alarm_or_signal')
                return b'{"type":"diagnostic-stage-start","stage":"permission_pass"}\n', b''
            def kill(self):
                self.killed, self.returncode = True, -9
            def poll(self): return self.returncode
            def wait(self, timeout): return self.returncode
        process = Process()
        run = runner.Run.__new__(runner.Run)
        run.end, run.prepare, run.environ = time.time() + 90, prepare, {}
        with tempfile.TemporaryDirectory() as temporary:
            run.out = Path(temporary)
            with patch.object(runner.subprocess, 'Popen', return_value=process), self.assertRaises(runner.Refused):
                run.harvest_logs({'metadata': {'name': 'synthetic-pod'}})
            self.assertIn(b'permission_pass', (run.out / 'actual-log.jsonl').read_bytes())
        self.assertTrue(process.killed)
        self.assertEqual(process.returncode, -9)
        self.assertEqual(process.calls, 2)

    def test_completed_event_requires_real_zero_exit_and_current_identity(self):
        job, pod = admitted(self.ready)
        native = verifier.bind(self.ready, job, pod, PHASE, probe.MODULES, runner.stamp(), helper, JOB_UID, POD_UID)
        run = runner.Run.__new__(runner.Run)
        run.ready, run.phase, run.name, run.uid, run.helper = self.ready, PHASE, prepare.NAME, JOB_UID, helper
        job['status'] = {'active': 0, 'succeeded': 1, 'conditions': [{'type': 'Complete', 'status': 'True'}]}
        pod['status']['phase'] = 'Succeeded'
        status = pod['status']['containerStatuses'][0]
        status['state'] = {'terminated': {'exitCode': 0, 'reason': 'Completed'}}
        run.verify_completed(job, pod, native)
        status['state']['terminated'] = {'exitCode': 137, 'reason': 'Error'}
        with self.assertRaises(runner.Refused): run.verify_completed(job, pod, native)


if __name__ == '__main__':
    unittest.main(verbosity=2)
