#!/usr/bin/env python3
"""Finite source/import/closed-manifest fixtures. No network, PG or live corpus."""
import ast
import base64
import copy
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import bootstrap_payload
import pipeline_pins as pins
import prepare


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


helper = load('successor_checkpoint', 'checkpoint-copy-job.py')
host = load('successor_live_host', 'run-live-byte-baseline.py')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assignments(path):
    result = {}
    for node in ast.parse(path.read_bytes()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try: result[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError): pass
    return result


class PipelineCases(unittest.TestCase):
    def test_complete_current_runtime_and_scope_source_pins(self):
        modules = Path('/copy-writer')
        for name, expected in pins.MODULES.items():
            path = modules / name if modules.is_dir() else pins.module_source(name)
            self.assertEqual(digest(path), expected)
        self.assertEqual(digest(HERE / 'bound_census_collectors.py'), pins.COLLECTOR_SHA256)
        self.assertEqual(digest(HERE / 'capture-live-byte-baseline.py'), pins.LIVE_ENTRY_SHA256)
        self.assertEqual(hashlib.sha256(prepare.canonical(pins.SELECTED_SCOPE)).hexdigest(), pins.SELECTED_SCOPE_SHA256)
        self.assertEqual(len(pins.SELECTED_SCOPE), 3)

    def test_exact_bootstrap_payload_closure_and_runtime_shadow_refusal(self):
        for name, entry, gate, expected_files in [
                ('live-baseline-closed-manifest.json', 'capture-live-byte-baseline.py', 'COPY_BASELINE_PHASE_READY',
                 ('bound_census_collectors.py', 'capture-live-byte-baseline.py')),
                ('pg-nfs-readonly-source-template.prepared.json', 'capture-source-stat-census.py', 'COPY_SOURCE_PHASE_READY',
                 ('bound_census_collectors.py', 'source_health.py', 'capture-source-stat-census.py', 'outcome_mailbox.py', 'verify-copy-outcome-readonly.py'))]:
            command = json.loads((HERE / name).read_bytes())['spec']['template']['spec']['containers'][0]['command'][-1]
            encoded = assignments_from_source(command)
            self.assertEqual(set(encoded['files']), set(expected_files))
            files = {key: base64.b64decode(value, validate=True) for key, value in encoded['files'].items()}
            for key, raw in files.items():
                self.assertEqual(raw, (HERE / key).read_bytes())
                self.assertEqual(hashlib.sha256(raw).hexdigest(), encoded['hashes'][key])
            self.assertEqual(command, bootstrap_payload.program(files, entry, gate))
        with tempfile.TemporaryDirectory() as directory:
            runtime, payload = Path(directory) / 'runtime', Path(directory) / 'payload'
            runtime.mkdir(); payload.mkdir()
            (runtime / 'epub_copies.py').write_text('runtime=True')
            (payload / 'epub_copies.py').write_text('runtime=False')
            (payload / 'bound_census_collectors.py').write_text('candidate=True')
            roots = [str(runtime), str(payload)]
            self.assertEqual(importlib.machinery.PathFinder.find_spec('epub_copies', roots).origin, str(runtime / 'epub_copies.py'))
            self.assertEqual(importlib.machinery.PathFinder.find_spec('bound_census_collectors', roots).origin, str(payload / 'bound_census_collectors.py'))
            (runtime / 'bound_census_collectors.py').write_text('candidate=False')
            self.assertNotEqual(importlib.machinery.PathFinder.find_spec('bound_census_collectors', roots).origin, str(payload / 'bound_census_collectors.py'))

    def test_all_closed_role_templates_and_frozen_whole_template_pins(self):
        for name in prepare.TEMPLATES:
            value = json.loads((HERE / name).read_bytes())
            container = value['spec']['template']['spec']['containers'][0]
            env = {row['name']: row for row in container['env']}
            self.assertFalse(value['spec']['template']['spec']['automountServiceAccountToken'])
            self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
            if name == 'live-baseline-closed-manifest.json':
                self.assertEqual(value['spec']['activeDeadlineSeconds'], 180)
                self.assertEqual(container['resources']['limits'], {'cpu': '1', 'memory': '512Mi'})
                self.assertEqual(env['COPY_BASELINE_PHASE_READY']['value'], '0')
            else:
                gate, _, _ = helper.profile(value)
                self.assertEqual(env[gate]['value'], helper.PROFILES[gate][0])
        for _, (name, expected) in helper.FROZEN_TEMPLATES.items():
            self.assertEqual(digest(HERE / name), expected)
        value = json.loads((HERE / 'pg-nfs-readonly-source-template.prepared.json').read_bytes())
        value['spec']['template']['spec']['containers'][0]['securityContext']['readOnlyRootFilesystem'] = False
        with self.assertRaises(helper.Refused): helper.profile(value)

    def test_all_caller_pins_match_new_siblings_without_old_module_restamp(self):
        sender = assignments(HERE / 'deliver-private-inputs.py')
        delivery = assignments(HERE / 'deliver-main-copy-proofs.py')
        receiver = assignments(HERE / 'receive-private-inputs.py')
        self.assertEqual(sender['IMAGE'], helper.COPY_IMAGE)
        self.assertEqual(sender['HELPER_SHA'], digest(HERE / 'checkpoint-copy-job.py'))
        self.assertEqual(sender['COLLECTOR_SHA'], pins.COLLECTOR_SHA256)
        self.assertEqual(delivery['HELPER_SHA'], sender['HELPER_SHA'])
        self.assertEqual(delivery['BRIDGE_SHA'], digest(HERE / 'check-main-source-identity.py'))
        self.assertEqual(receiver['MODULES'], {k: pins.MODULES[k] for k in ('epub_copies.py', 'epub_metadata.py')})
        self.assertEqual(host.MODULES, receiver['MODULES'])
        self.assertEqual(host.PINS['sender'], digest(HERE / 'deliver-private-inputs.py'))
        self.assertEqual(host.PINS['receiver'], digest(HERE / 'receive-private-inputs.py'))
        self.assertEqual(helper.CAPTURE_IMAGE, pins.APP_IMAGE)
        self.assertEqual(helper.SOURCE, pins.JOBS['source']); self.assertEqual(helper.MAIN, pins.JOBS['main'])
        self.assertEqual(assignments(HERE / 'assemble-copy-proofs.py')['BOUND_MODULE_PINS'],
                         {k: pins.MODULES[k] for k in ('bound_census.py', 'epub_copies.py', 'epub_metadata.py')})
        self.assertEqual(assignments(HERE / 'verify-copy-outcome-readonly.py')['PINS'],
                         {k: pins.MODULES[k] for k in ('epub_copies.py', 'epub_metadata.py', 'bound_census.py')})

    def test_unchanged_native_verifier_ack_and_proof_sender(self):
        for name, sha in [('prepare-native-source-contract.py', '67f40c064babee41cc7faba1b7b9541a0ffe65d4d0f137507248fdf5c9e8108f'),
                          ('receive-live-baseline-ack.py', 'de30ca5463b6564146f4fe71c79a6502487941ccfc09d7967054abeceff421f3'),
                          ('proof_sender.py', '30ca6db3c0b82257d7196706794f6e107945fb17d36cb7f44cfd7d9ece9cadf4')]:
            self.assertEqual(digest(HERE / name), sha)

    def test_complete_job_requires_actual_original_main_pod_zero_exit(self):
        ready = json.loads((HERE / 'copy-writer-source-template.prepared.json').read_bytes())
        ns, name = helper.MAIN
        job_uid, pod_uid = '11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'
        phase = 'a' * 32
        for meta in (ready['metadata'], ready['spec']['template']['metadata']):
            meta.setdefault('labels', {})[helper.LABEL] = phase
        row = {'ready_manifest': ready, 'uid': job_uid, 'namespace': ns, 'name': name, 'phase_token': phase}
        job = copy.deepcopy(ready)
        job['metadata']['uid'] = job_uid
        generated = {'batch.kubernetes.io/controller-uid': job_uid, 'controller-uid': job_uid,
                     'batch.kubernetes.io/job-name': name, 'job-name': name}
        job['spec']['template']['metadata']['labels'].update(generated)
        job['spec']['selector'] = {'matchLabels': {'batch.kubernetes.io/controller-uid': job_uid}}
        job['status'] = {'active': 0, 'conditions': [{'type': 'Complete', 'status': 'True'}]}
        pod = {'metadata': {'namespace': ns, 'name': 'synthetic-main-pod', 'uid': pod_uid,
                            'labels': {helper.LABEL: phase, **generated}, 'annotations': {'k8tz.io/inject': 'false'},
                            'ownerReferences': [{'apiVersion': 'batch/v1', 'kind': 'Job', 'name': name,
                                                 'uid': job_uid, 'controller': True}]},
               'spec': copy.deepcopy(job['spec']['template']['spec']),
               'status': {'phase': 'Running', 'containerStatuses': [{
                   'name': ready['spec']['template']['spec']['containers'][0]['name'], 'restartCount': 0,
                   'imageID': pins.IMAGE, 'state': {'running': {}}}]}}
        pod['spec']['nodeName'] = 'talosw01'
        self.assertTrue(helper.declared_matches(ready, job))
        helper.verify_owned_pod(row, job, pod, pod_uid)
        with self.assertRaises(helper.Refused): helper.verify_owned_pod(row, job, pod, pod_uid, True)
        pod['status']['phase'] = 'Succeeded'
        pod['status']['containerStatuses'][0]['state'] = {'terminated': {'exitCode': 0, 'reason': 'Completed'}}
        helper.verify_owned_pod(row, job, pod, pod_uid, True)
        for field, value in [('exitCode', 1), ('reason', 'Error')]:
            altered = copy.deepcopy(pod); altered['status']['containerStatuses'][0]['state']['terminated'][field] = value
            with self.subTest(field=field), self.assertRaises(helper.Refused):
                helper.verify_owned_pod(row, job, altered, pod_uid, True)
        for status in ({'active': 1, 'conditions': [{'type': 'Complete', 'status': 'True'}]},
                       {'active': 0, 'conditions': []},
                       {'active': 0, 'conditions': [{'type': 'Complete', 'status': 'True'}, {'type': 'Failed', 'status': 'True'}]}):
            altered = copy.deepcopy(job); altered['status'] = status
            with self.subTest(status=status), self.assertRaises(helper.Refused):
                helper.verify_owned_pod(row, altered, pod, pod_uid, True)

    def test_bootstrap_closed_guard_no_payload_import_or_full_pipe_wait(self):
        command = bootstrap_payload.program({'fixture.py': b'raise RuntimeError("must not execute")'}, 'fixture.py', 'COPY_SOURCE_PHASE_READY')
        # The child fills its own pipe before executing the exact closed guard.
        prefix = 'import os,sys\nfd=sys.stdout.fileno();os.set_blocking(fd,False)\nfor _ in range(257):\n try:os.write(fd,b"x"*4096)\n except BlockingIOError:break\nelse:os._exit(3)\nos.set_blocking(fd,True)\n'
        # This is a finite bounded pipe fill, never a CPU/load loop.
        child = subprocess.Popen([sys.executable, '-B', '-c', prefix + command], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env={k: v for k, v in os.environ.items() if k != 'COPY_SOURCE_PHASE_READY'})
        try:
            self.assertEqual(child.wait(timeout=2), 2)
            self.assertEqual(child.stderr.read(), b'')
        finally:
            if child.poll() is None: child.kill(); child.wait(timeout=1)
            child.stdout.close(); child.stderr.close()

    def test_preparation_signature_mismatch_refuses_before_package_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory); receipt = directory / 'receipt'; proof = directory / 'proof'
            receipt.write_text('synthetic fixture'); proof.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'signature receipt'):
                prepare.materialize(directory / 'package', receipt, proof)
            self.assertFalse((directory / 'package').exists())

    def test_children_claim_no_pg_or_held_fence_authority_and_outcome_is_owner_only(self):
        tree = ast.parse((HERE / 'exchange-source-outcome.py').read_bytes())
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        self.assertNotIn('psycopg', imports); self.assertNotIn('book_copy_writer', imports); self.assertNotIn('epub_copies', imports)
        self.assertIn('before MAIN transaction admission', (HERE / 'check-main-source-identity.py').read_text())
        result = subprocess.run([sys.executable, '-B', str(HERE / 'verify-copy-outcome-readonly.py')],
                                env={**os.environ, 'PYTHONPATH': os.environ.get('COPY_TEST_MODULES', str(pins.module_source('epub_copies.py').parent)) + os.pathsep + str(HERE.parent)},
                                capture_output=True, timeout=2)
        self.assertEqual(result.returncode, 2); self.assertEqual(result.stdout, b'')


def assignments_from_source(source):
    result = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            result[node.targets[0].id] = ast.literal_eval(node.value)
    return result


if __name__ == '__main__': unittest.main()
