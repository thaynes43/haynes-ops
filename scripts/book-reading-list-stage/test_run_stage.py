"""Finite native/transport fixtures; no Kubernetes or vendor call."""
import copy
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('stage', Path(__file__).with_name('run-stage.py'))
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


def fixture():
    deploy = {'kind': 'Deployment', 'metadata': {'uid': 'deploy1'}, 'spec': {'replicas': 1}}
    rs = {'kind': 'ReplicaSet', 'metadata': {'uid': 'rs1', 'ownerReferences': [{'controller': True, 'kind': 'Deployment', 'uid': 'deploy1'}]}}
    pod = {'kind': 'Pod', 'metadata': {'uid': 'pod1', 'ownerReferences': [{'controller': True, 'kind': 'ReplicaSet', 'uid': 'rs1'}]},
           'spec': {'containers': [{'name': 'app', 'image': 'libretto:pinned'}]},
           'status': {'phase': 'Running', 'containerStatuses': [{'name': 'app', 'ready': True, 'started': True, 'restartCount': 0, 'imageID': 'sha256:pinned'}]}}
    binding = {'namespace': 'media', 'podName': 'libretto-pod', 'podUid': 'pod1', 'replicaSetName': 'libretto-rs',
               'replicaSetUid': 'rs1', 'deploymentName': 'libretto', 'deploymentUid': 'deploy1', 'container': 'app',
               'podSpecSha256': stage.sha(stage.canonical(pod['spec'])), 'deploymentSpecSha256': stage.sha(stage.canonical(deploy['spec'])),
               'image': 'libretto:pinned', 'imageID': 'sha256:pinned', 'restartCount': 0}
    return binding, [pod, rs, deploy]


class FakeChild:
    """One actor waits for every ACK, as the remote protocol does."""
    def __init__(self, directory, status=0, drift=False):
        read_input, write_input = os.pipe()
        read_output, write_output = os.pipe()
        self.stdin = os.fdopen(write_input, 'wb', buffering=0)
        self.stdout = os.fdopen(read_output, 'rb', buffering=0)
        self.returncode = None
        self.acks = []
        self.problem = None
        self.directory = directory
        def actor():
            try:
                with os.fdopen(read_input, 'rb', buffering=0) as incoming, os.fdopen(write_output, 'wb', buffering=0) as outgoing:
                    envelope = json.loads(incoming.readline())
                    previous = '0' * 64
                    for sequence, kind in enumerate(['before', 'intent', 'response', 'readback', 'complete'], 1):
                        event = {'phase': envelope['approval']['phase'], 'seq': sequence, 'previous': previous, 'type': kind,
                                 'at': dt.datetime.now(dt.timezone.utc).isoformat(),
                                 'data': {'workerSha256': envelope['workerSha256'], 'numeric': 1.0}}
                        # Preserve exact float spelling across the JS/Python protocol boundary.
                        raw = json.dumps(event, sort_keys=True, separators=(',', ':'))
                        digest = stage.sha(raw.encode())
                        frame = {'event': raw, 'sha256': 'f' * 64 if drift and sequence == 2 else digest, 'needsAck': True}
                        outgoing.write(json.dumps(frame).encode() + b'\n')
                        ack_line = incoming.readline()
                        if not ack_line:
                            break
                        ack = json.loads(ack_line)
                        assert ack == {'phase': event['phase'], 'seq': sequence, 'sha256': digest}
                        saved = directory / f'{sequence:06d}.json'
                        assert saved.exists(), 'ACK arrived before durable event creation'
                        assert json.loads(saved.read_bytes()) == frame
                        assert saved.stat().st_mode & 0o777 == 0o600
                        self.acks.append(kind)
                        previous = digest
            except Exception as error:
                self.problem = error
            finally:
                self.returncode = status
        self.thread = threading.Thread(target=actor, daemon=True)
        self.thread.start()

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise TimeoutError('fixture actor did not finish')
        if self.problem:
            raise self.problem
        self.stdout.close()
        return self.returncode

    def terminate(self):
        if not self.stdin.closed:
            self.stdin.close()

    kill = terminate


class NativeTests(unittest.TestCase):
    def test_exact_native_chain_passes_and_reparented_chain_refuses(self):
        binding, objects = fixture()
        with patch.object(stage, 'native_get', side_effect=objects):
            stage.admit_native(binding, time.time() + 5)
        wrong = copy.deepcopy(objects)
        wrong[0]['metadata']['ownerReferences'][0]['uid'] = 'other-rs'
        with patch.object(stage, 'native_get', side_effect=wrong), self.assertRaisesRegex(ValueError, 'controller chain'):
            stage.admit_native(binding, time.time() + 5)

    def test_restarted_or_changed_image_refuses(self):
        binding, objects = fixture()
        wrong = copy.deepcopy(objects)
        wrong[0]['status']['containerStatuses'][0]['restartCount'] = 1
        with patch.object(stage, 'native_get', side_effect=wrong), self.assertRaisesRegex(ValueError, 'restart/image'):
            stage.admit_native(binding, time.time() + 5)
        wrong = copy.deepcopy(objects)
        wrong[0]['spec']['containers'][0]['image'] = 'other'
        with patch.object(stage, 'native_get', side_effect=wrong), self.assertRaisesRegex(ValueError, 'spec changed'):
            stage.admit_native(binding, time.time() + 5)

    def test_private_input_refuses_symlink_and_unsafe_permissions(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'input'
            path.write_bytes(b'{}')
            with self.assertRaisesRegex(ValueError, '0600'):
                stage.read_private(path)
            path.chmod(0o600)
            self.assertEqual(stage.read_private(path)[0], b'{}')
            link = Path(root) / 'link'
            link.symlink_to(path)
            with self.assertRaises(OSError):
                stage.read_private(link)


class TransportTests(unittest.TestCase):
    def run_fixture(self, directory, status=0, drift=False):
        binding, _ = fixture()
        approval = {'phase': '11111111-2222-3333-4444-555555555555', 'capturedAt': dt.datetime.now(dt.timezone.utc).isoformat(),
                    'native': binding, 'stage': 'initial', 'scopes': [{}], 'helpers': stage.helper_bindings()}
        holder = []
        def spawn(*args, **kwargs):
            child = FakeChild(directory, status, drift)
            holder.append(child)
            return child
        with patch.object(stage, 'admit_native'), patch.object(stage.subprocess, 'Popen', side_effect=spawn):
            try:
                result = stage.execute(approval, directory, {})
            finally:
                self.child = holder[0]
        return result

    def test_every_ack_follows_immutable_event_and_actual_completion_is_retained(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            result = self.run_fixture(directory)
            self.assertTrue(result['completed'])
            self.assertEqual(self.child.acks, ['before', 'intent', 'response', 'readback', 'complete'])
            self.assertEqual(json.loads((directory / 'completion.json').read_bytes())['childExit'], 0)
            with self.assertRaises(FileExistsError):
                stage.write_event(directory, 1, b'{}')

    def test_hash_drift_gets_no_intent_ack_and_no_completed_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            with self.assertRaisesRegex(ValueError, 'phase/hash/sequence'):
                self.run_fixture(directory, drift=True)
            self.assertEqual(self.child.acks, ['before'])
            self.assertTrue((directory / '000001.json').exists())
            self.assertFalse((directory / 'completion.json').exists())

    def test_nonzero_child_preserves_prefix_without_success_or_retry(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            with self.assertRaisesRegex(ValueError, 'completion unknown'):
                self.run_fixture(directory, status=2)
            self.assertEqual(len(list(directory.glob('[0-9]*.json'))), 5)
            self.assertFalse((directory / 'completion.json').exists())

    def test_changed_reviewed_helper_refuses_before_native_or_child(self):
        binding, _ = fixture()
        approval = {'capturedAt': dt.datetime.now(dt.timezone.utc).isoformat(), 'native': binding,
                    'helpers': {**stage.helper_bindings(), 'workerSha256': 'f' * 64}}
        with tempfile.TemporaryDirectory() as root, patch.object(stage, 'admit_native') as native, patch.object(stage.subprocess, 'Popen') as child:
            with self.assertRaisesRegex(ValueError, 'helper bytes changed'):
                stage.execute(approval, Path(root), {})
            native.assert_not_called()
            child.assert_not_called()


if __name__ == '__main__':
    unittest.main()
