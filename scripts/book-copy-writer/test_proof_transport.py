"""Small deterministic stdin fixtures; no API, database or library operations."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from proof_transport import receive, receive_to_ready, wait_ready, send, Refused, FILES
import proof_sender


class ProofTransportTests(unittest.TestCase):
    def setUp(self):
        self.phase, self.uid = "a" * 32, "11111111-2222-3333-4444-555555555555"
        self.pod_uid = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa"
        self.raw = {"snapshot.json": b'{"complete":"snapshot"}', "selection.json": b'{"ordered":"selection"}',
                    "app-capture.json": b'{"full":"rows"}'}
        self.hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in self.raw.items()}
        self.env = {"COPY_PHASE_TOKEN": self.phase, "COPY_JOB_UID": self.uid, "COPY_POD_UID": self.pod_uid,
                    "COPY_PROOF_HASHES_JSON": json.dumps(self.hashes), "COPY_DEADLINE_EPOCH": str(time.time() + 120)}
        self.header = {"schema": 1, "phase_token": self.phase, "job_uid": self.uid, "pod_uid": self.pod_uid,
                       "files": [{"name": name, "bytes": len(raw), "sha256": self.hashes[name]} for name, raw in self.raw.items()]}

    def bundle(self, header=None, tail=b""):
        return io.BytesIO(json.dumps(header or self.header).encode() + b"\n" + b"".join(self.raw.values()) + tail)

    def test_exact_bytes_in_private_files_and_small_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = receive(self.bundle(), directory, self.env)
            self.assertEqual(set(paths), set(FILES))
            for name, path in paths.items():
                self.assertEqual(Path(path).read_bytes(), self.raw[name])
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertLess(len(self.env["COPY_PROOF_HASHES_JSON"]), 1024)

    def test_unknown_repeated_oversized_and_noninteger_scope_refuse_before_files(self):
        variants = []
        for name, length in (("../escape", 2), ("selection.json", FILES["selection.json"] + 1), ("selection.json", True)):
            header = copy.deepcopy(self.header); header["files"][1].update(name=name, bytes=length); variants.append(header)
        header = copy.deepcopy(self.header); header["files"][1] = dict(header["files"][0]); variants.append(header)
        for header in variants:
            with self.subTest(header=header), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(Refused):
                    receive(self.bundle(header), directory, self.env)
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_wrong_phase_job_or_manifest_hash_refuses_before_files(self):
        for key, value in (("phase_token", "b" * 32), ("job_uid", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"), ("pod_uid", "bbbbbbbb-cccc-dddd-eeee-ffffffffffff")):
            header = copy.deepcopy(self.header); header[key] = value
            with tempfile.TemporaryDirectory() as directory, self.assertRaises(Refused):
                receive(self.bundle(header), directory, self.env)
        header = copy.deepcopy(self.header); header["files"][0]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(Refused):
            receive(self.bundle(header), directory, self.env)

    def test_truncated_corrupt_or_trailing_bytes_never_returns_ready_paths(self):
        good = self.bundle().getvalue()
        for raw in (good[:-1], good.replace(b'"full":"rows"', b'"full":"fake"'), good + b"unapproved"):
            with tempfile.TemporaryDirectory() as directory, self.assertRaises(Refused):
                try:
                    receive_to_ready(io.BytesIO(raw), directory, self.env)
                finally:
                    self.assertFalse(Path(directory, "ready.json").exists())

    def test_closed_gate_oversized_header_duplicate_keys_and_reused_directory_refuse(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(Refused):
            receive(self.bundle(), directory, {})
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(Refused):
            receive(io.BytesIO(b"x" * 4097), directory, self.env)
        duplicate = json.dumps(self.header).replace('"schema": 1', '"schema": 1, "schema": 1').encode() + b"\n"
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(Refused):
            receive(io.BytesIO(duplicate), directory, self.env)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "existing").write_bytes(b"preserve")
            with self.assertRaises(Refused):
                receive(self.bundle(), directory, self.env)
            self.assertEqual(Path(directory, "existing").read_bytes(), b"preserve")

    def test_real_sender_roundtrip_and_changed_or_symlink_source_refusal(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            paths = {}
            for name, raw in self.raw.items():
                path = Path(source, name); path.write_bytes(raw); paths[name] = str(path)
            stream = io.BytesIO()
            send(stream, paths, self.phase, self.uid, self.pod_uid)
            stream.seek(0)
            received = receive(stream, target, self.env)
            self.assertEqual({name: Path(path).read_bytes() for name, path in received.items()}, self.raw)
            Path(paths["snapshot.json"]).unlink()
            Path(paths["snapshot.json"]).symlink_to(paths["selection.json"])
            with self.assertRaises(OSError):
                send(io.BytesIO(), paths, self.phase, self.uid, self.pod_uid)

    def await_delivery(self, directory, callback=None):
        def delivered(_delay):
            receive_to_ready(self.bundle(), directory, self.env)
            if callback:
                callback(directory)
        with mock.patch("proof_transport.time.sleep", side_effect=delivered):
            return wait_ready(directory, self.env, float(self.env["COPY_DEADLINE_EPOCH"]))

    def test_exec_receiver_publishes_exact_immutable_marker_consumed_by_main(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root, "proofs")
            paths = self.await_delivery(directory)
            self.assertEqual({name: Path(path).read_bytes() for name, path in paths.items()}, self.raw)
            marker = json.loads(Path(directory, "ready.json").read_bytes())
            self.assertEqual(marker["phase_token"], self.phase)
            self.assertEqual(marker["job_uid"], self.uid)
            for path in directory.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)
                self.assertEqual(path.stat().st_nlink, 1)
            with self.assertRaises(Refused):
                receive_to_ready(self.bundle(), directory, self.env)
            self.assertEqual(json.loads(Path(directory, "ready.json").read_bytes()), marker)

    def test_main_refuses_missing_gate_or_changed_deadline_before_creating_directory(self):
        variants = ({}, {**self.env, "COPY_DEADLINE_EPOCH": "nan"},
                    {**self.env, "COPY_DEADLINE_EPOCH": str(time.time() - 1)},
                    {**self.env, "COPY_DEADLINE_EPOCH": str(time.time() + 301)},
                    {**self.env, "COPY_PROOF_HASHES_JSON": "x" * 1025})
        for env in variants:
            with tempfile.TemporaryDirectory() as root:
                directory = Path(root, "proofs")
                with self.assertRaises(Refused):
                    wait_ready(directory, env, float(self.env["COPY_DEADLINE_EPOCH"]))
                self.assertFalse(directory.exists())
        with tempfile.TemporaryDirectory() as root, self.assertRaises(Refused):
            wait_ready(Path(root, "proofs"), self.env, float(self.env["COPY_DEADLINE_EPOCH"]) - 1)

    def test_main_rejects_wrong_phase_corrupt_file_extra_file_and_symlink(self):
        def wrong_phase(directory):
            path = Path(directory, "ready.json"); path.chmod(0o600)
            marker = json.loads(path.read_bytes()); marker["phase_token"] = "b" * 32
            path.write_text(json.dumps(marker)); path.chmod(0o400)
        def corrupt(directory):
            path = Path(directory, "snapshot.json"); path.chmod(0o600)
            path.write_bytes(b"x" * len(self.raw["snapshot.json"])); path.chmod(0o400)
        def extra(directory):
            Path(directory, "extra.json").write_bytes(b"unapproved")
        def symlink(directory):
            path = Path(directory, "snapshot.json"); path.unlink()
            path.symlink_to("selection.json")
        for callback in (wrong_phase, corrupt, extra, symlink):
            with self.subTest(callback=callback.__name__), tempfile.TemporaryDirectory() as root:
                with self.assertRaises((Refused, OSError)):
                    self.await_delivery(Path(root, "proofs"), callback)

    def test_main_wait_expiry_does_not_publish_ready_or_reuse_a_directory(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root, "proofs")
            now = time.time()
            env = {**self.env, "COPY_DEADLINE_EPOCH": str(now + 1)}
            with mock.patch("proof_transport.time.time", side_effect=[now, now + 2]), self.assertRaises(Refused):
                wait_ready(directory, env, now + 1)
            self.assertFalse(Path(directory, "ready.json").exists())
            with self.assertRaises(FileExistsError):
                wait_ready(directory, self.env, float(self.env["COPY_DEADLINE_EPOCH"]))

    def test_sender_ignores_access_time_but_rejects_source_identity_change(self):
        with tempfile.TemporaryDirectory() as root:
            paths = {}
            for name, raw in self.raw.items():
                path = Path(root, name); path.write_bytes(raw); paths[name] = str(path)
                os.utime(path, (1, 1))
            send(io.BytesIO(), paths, self.phase, self.uid, self.pod_uid)
            class TamperingStream(io.BytesIO):
                def write(inner, raw):
                    if raw.startswith(b'{"schema":'):
                        path = Path(paths["snapshot.json"])
                        path.write_bytes(b"x" * len(self.raw["snapshot.json"]))
                    return super(TamperingStream, inner).write(raw)
            with self.assertRaises(Refused):
                send(TamperingStream(), paths, self.phase, self.uid, self.pod_uid)

    def test_actual_receiver_cli_stops_an_open_stdin_at_its_deadline(self):
        environment = {**os.environ, **self.env,
                       'PYTHONPATH': str(Path(__file__).resolve().parent) + os.pathsep + os.environ.get('PYTHONPATH', '')}
        # Warm imports before setting this deliberately tiny fixture deadline;
        # cold interpreter scheduling is unrelated to blocked-stdin expiry.
        program = ("import os,runpy,sys,time,proof_transport; "
                   "os.environ['COPY_DEADLINE_EPOCH']=str(time.time()+.15); "
                   "sys.argv=[sys.argv[1],'receive']; runpy.run_path(sys.argv[0],run_name='__main__')")
        receiver = subprocess.Popen([sys.executable, '-c', program, str(Path(__file__).with_name('proof_transport.py'))],
                                    env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(receiver.wait(timeout=2), 1)
            self.assertIn(b'proof receiver deadline expired', receiver.stderr.read())
            self.assertEqual(receiver.stdout.read(), b'')
        finally:
            if receiver.poll() is None:
                receiver.kill(); receiver.wait(timeout=2)
            receiver.stdin.close(); receiver.stdout.close(); receiver.stderr.close()

    def target(self):
        # The operator imports the separately hash-pinned checkpoint validator.
        # This pure transport fixture supplies its exact-comparison interface;
        # admission normalization is already covered by that helper's own tests.
        label = 'issue825.haynesnetwork/phase'
        class Helper:
            LABEL = label
            encoded = staticmethod(lambda row: (json.dumps(row, indent=2) + '\n').encode())
            digest = staticmethod(lambda raw: hashlib.sha256(raw).hexdigest())
            exact_json = staticmethod(lambda left, right: type(left) is type(right) and left == right)
            image_identity = staticmethod(lambda value: value)
            @staticmethod
            def declared_matches(ready, job):
                normalized = copy.deepcopy(job); normalized['metadata'].pop('uid', None)
                return normalized == ready
        container = {'name': 'writer', 'image': 'ghcr.io/thaynes43/book-copy-writer@sha256:' + 'a' * 64,
                     'command': ['nice', '-n', '19', 'python', '/copy-writer/book_copy_writer.py'],
                     'args': ['--wait-proofs', '--deadline-epoch', self.env['COPY_DEADLINE_EPOCH']],
                     'env': [{'name': key, 'valueFrom': {'fieldRef': {'apiVersion': 'v1', 'fieldPath': field}}}
                             for key, field in (('COPY_PHASE_TOKEN', "metadata.labels['" + label + "']"),
                                                ('COPY_JOB_UID', "metadata.labels['batch.kubernetes.io/controller-uid']"),
                                                ('COPY_POD_UID', 'metadata.uid'))] +
                            [{'name': key, 'value': self.env[key]} for key in ('COPY_PROOF_HASHES_JSON', 'COPY_DEADLINE_EPOCH')]}
        ready = {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'namespace': 'frontend', 'name': 'copy-fixture', 'labels': {label: self.phase}},
                 'spec': {'template': {'metadata': {'labels': {label: self.phase}},
                                      'spec': {'containers': [container], 'nodeName': 'fixture-worker',
                                               'restartPolicy': 'Never', 'automountServiceAccountToken': False}}}}
        job = copy.deepcopy(ready); job['metadata']['uid'] = self.uid
        pod = {'metadata': {'namespace': 'frontend', 'name': 'copy-fixture-pod', 'uid': self.pod_uid,
                            'labels': {label: self.phase, 'batch.kubernetes.io/controller-uid': self.uid},
                            'ownerReferences': [{'apiVersion': 'batch/v1', 'kind': 'Job', 'name': 'copy-fixture', 'uid': self.uid, 'controller': True}]},
               'spec': copy.deepcopy(ready['spec']['template']['spec']),
               'status': {'phase': 'Running', 'containerStatuses': [{'name': 'writer', 'restartCount': 0,
                                                                    'state': {'running': {'startedAt': 'fixture'}}, 'imageID': container['image']}]}}
        row = {'writer': True, 'namespace': 'frontend', 'name': 'copy-fixture', 'uid': self.uid, 'phase_token': self.phase,
               'ready_manifest': ready, 'ready_manifest_sha256': Helper.digest(Helper.encoded(ready))}
        return Helper, row, job, pod

    def test_sender_validates_live_owner_and_main_then_uses_exec_only(self):
        helper, row, job, pod = self.target()
        env, command = proof_sender.verify_target(helper, row, self.phase, job, pod, self.pod_uid)
        self.assertEqual(env, self.env)
        self.assertEqual(command, ['kubectl', 'exec', '-i', '-n', 'frontend', 'copy-fixture-pod', '-c', 'writer',
                                   '--', 'nice', '-n', '19', 'python', '/copy-writer/proof_transport.py', 'receive'])
        self.assertNotIn('attach', command)

    def run_sender_cli(self, reads):
        helper, row, job, pod = self.target()
        state = {'complete': False, 'window_started_at': 'fixture',
                 'phase_token': self.phase, 'owned_jobs': [row]}
        with tempfile.TemporaryDirectory() as root:
            paths = {}
            for name, raw in self.raw.items():
                path = Path(root, name); path.write_bytes(raw); paths[name] = str(path)
            args = ['proof_sender.py', '--checkpoint-helper', 'fixture-helper', '--checkpoint-sha256', '0' * 64,
                    '--phase-state', 'fixture-ledger', '--restore-pr', '3609', '--namespace', row['namespace'],
                    '--job', row['name'], '--pod', pod['metadata']['name'], '--pod-uid', self.pod_uid,
                    '--snapshot', paths['snapshot.json'], '--selection', paths['selection.json'],
                    '--app-capture', paths['app-capture.json']]
            helper.read_json = mock.Mock(side_effect=reads(state))
            helper.validate_state = mock.Mock()
            with mock.patch.object(sys, 'argv', args), mock.patch.object(proof_sender, 'load_helper', return_value=helper), \
                    mock.patch.object(proof_sender, 'get', side_effect=[job, pod]) as get, \
                    mock.patch.object(proof_sender, 'stream_to_receiver') as deliver, \
                    mock.patch('builtins.print'):
                try:
                    proof_sender.main()
                finally:
                    self.assertEqual(helper.read_json.call_args_list,
                                     [mock.call('fixture-ledger'), mock.call('fixture-ledger')])
                    helper.validate_state.assert_called_once_with(state, '3609')
                    self.assertEqual(get.call_count, 2)
                    self.delivery_calls = deliver.call_count
        return self.delivery_calls

    def test_sender_cli_unpacks_checkpoint_read_and_delivers_unchanged_ledger(self):
        # The real core/COPY checkpoint API returns (parsed object, byte SHA).
        self.assertEqual(self.run_sender_cli(lambda state: [(state, '1' * 64), (state, '1' * 64)]), 1)

    def test_sender_cli_refuses_changed_ledger_rows_or_only_changed_bytes(self):
        # Semantically equal JSON with different bytes must also stop delivery.
        variants = [lambda state: [(state, '1' * 64), (state, '2' * 64)],
                    lambda state: [(state, '1' * 64), ({**state, 'complete': True}, '1' * 64)]]
        for reads in variants:
            with self.subTest(reads=reads), self.assertRaisesRegex(Refused, 'ledger changed'):
                self.run_sender_cli(reads)
            self.assertEqual(self.delivery_calls, 0)

    def test_sender_rejects_reused_names_owner_changes_and_workload_injections(self):
        mutations = [lambda row, job, pod: job['metadata'].update(uid='foreign'),
                     lambda row, job, pod: pod['metadata'].update(uid='foreign'),
                     lambda row, job, pod: pod['metadata']['ownerReferences'][0].update(uid='foreign'),
                     lambda row, job, pod: pod['metadata']['labels'].update({'issue825.haynesnetwork/phase': 'b' * 32}),
                     lambda row, job, pod: pod['spec']['containers'].append({'name': 'foreign'}),
                     lambda row, job, pod: pod['spec']['containers'][0].update(volumeMounts=[{'name': 'write', 'mountPath': '/unapproved'}]),
                     lambda row, job, pod: pod['spec']['containers'][0].update(command=['unapproved']),
                     lambda row, job, pod: pod['status']['containerStatuses'][0].update(restartCount=1),
                     lambda row, job, pod: pod['status']['containerStatuses'][0].update(state={'terminated': {}}),
                     lambda row, job, pod: pod['status']['containerStatuses'][0].update(imageID='foreign'),
                     lambda row, job, pod: row.update(ready_manifest_sha256='0' * 64)]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                helper, row, job, pod = self.target(); mutate(row, job, pod)
                with self.assertRaises(Refused):
                    proof_sender.verify_target(helper, row, self.phase, job, pod, self.pod_uid)

    def test_sender_deadline_interrupts_blocked_pipe_and_reaps_only_its_child(self):
        with tempfile.TemporaryDirectory() as root:
            paths = {}
            for name in FILES:
                path = Path(root, name); path.write_bytes(b'bounded fixture' * 20000); paths[name] = str(path)
            children = []
            def create(*args, **kwargs):
                child = subprocess.Popen(*args, **kwargs); children.append(child); return child
            start = time.monotonic()
            with self.assertRaisesRegex(Refused, 'host proof sender deadline'):
                proof_sender.stream_to_receiver([sys.executable, '-c', 'import time; time.sleep(3)'], paths,
                                                self.phase, self.uid, self.pod_uid, time.time() + .15, popen=create)
            self.assertLess(time.monotonic() - start, 1)
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll())


if __name__ == "__main__":
    unittest.main(verbosity=2)
