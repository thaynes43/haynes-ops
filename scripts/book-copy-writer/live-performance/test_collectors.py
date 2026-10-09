#!/usr/bin/env python3
"""Finite local fixtures: run once at nice19, serial; never live/load/stress."""
import ast
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
EPUB = Path(os.environ.get('COPY_TEST_MODULES') or HERE.parents[2] / 'kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert')
sys.path.insert(0, str(EPUB))
import epub_copies as copies
import epub_metadata as metadata
from test_epub_metadata import fixture, write


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


original = load('original_collectors', HERE.parent / 'live-baseline-host/dependencies/bound_census_collectors.py')
candidate = load('candidate_collectors', HERE / 'bound_census_collectors.py')
producer = load('candidate_producer', HERE / 'capture-live-byte-baseline.py')
FIXED = '2026-10-09T12:00:00+00:00'


def binding(root):
    return {'namespace': 'fixture', 'pod_name': 'fixture', 'pod_uid': '11111111-1111-4111-8111-111111111111',
            'job_uid': '22222222-2222-4222-8222-222222222222', 'node': 'fixture-node', 'image': 'fixture',
            'image_id': 'fixture@sha256:' + 'a' * 64, 'pod_spec_sha256': 'b' * 64, 'restarts': 0,
            'mount_root': root, 'nfs_server': 'fixture', 'nfs_export': '/fixture'}


class FilesystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = str(Path(self.tmp.name) / 'EBooks')
        self.raw = fixture()
        for path in ('Author/a.epub', 'Author/b.epub', '.hidden/c.epub'):
            write(str(Path(self.root) / path), self.raw)
        write(str(Path(self.root) / 'notes.txt'), b'include all non-EPUB files')
        write(str(Path(self.root) / 'Author/.ll_ignore'), b'preserve actual ignore scope')

    def census(self, module):
        with mock.patch.object(module, 'utc', return_value=FIXED):
            return module.stat_census(self.root, time.monotonic() + 5, metadata, copies, lambda: None, binding(self.root))

    def test_exact_complete_stat_permissions_and_protections_match_original(self):
        os.link(str(Path(self.root) / 'notes.txt'), str(Path(self.root) / 'hardlink.txt'))
        os.symlink('notes.txt', str(Path(self.root) / 'symlink.txt'))
        os.mkfifo(str(Path(self.root) / 'pipe'))
        os.chmod(str(Path(self.root) / 'Author'), 0o500)
        with mock.patch.dict(os.environ, {'LIBRARY_HOLD_FOLDERS_JSON': '["Author"]'}):
            before, after = self.census(original), self.census(candidate)
        self.assertEqual(before, after)
        self.assertEqual(after['configured_hold_folders'], ['Author'])
        self.assertEqual(len(after['all_file_fingerprints']), 8)
        self.assertEqual(after['derived_ignore_markers'], ['Author/.ll_ignore'])
        reasons = {row['path']: row['reason'] for row in after['additional_protected_paths']}
        self.assertIn('Author', reasons)
        self.assertIn('pipe', reasons)
        self.assertIn('symlink.txt', reasons)
        self.assertIn('hardlink.txt', reasons)

    def test_byte_and_raw_opf_rows_and_order_match_original(self):
        fingerprints, _ = candidate.walk(self.root, time.monotonic() + 5, metadata, lambda: None)
        paths = candidate.epub_paths(fingerprints)
        self.assertEqual(paths, ['Author/a.epub', 'Author/b.epub'])
        expected = [original.read_epub(self.root, p, fingerprints[p], time.monotonic() + 5, metadata, lambda: None) for p in paths]
        actual = candidate.read_epubs(self.root, paths, fingerprints, time.monotonic() + 5, metadata, lambda: None)
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0]['sha256'], hashlib.sha256(self.raw).hexdigest())
        self.assertTrue(actual[0]['packages'][0]['raw_opf_sha256'])

    def test_complete_baseline_schema_and_original_clocks_match(self):
        fp, _ = candidate.walk(self.root, time.monotonic() + 5, metadata, lambda: None)
        selected = {p: hashlib.sha256(self.raw).hexdigest() for p in candidate.epub_paths(fp)}
        with mock.patch.object(original, 'utc', return_value=FIXED), mock.patch.object(candidate, 'utc', return_value=FIXED), mock.patch.object(time, 'monotonic', return_value=0):
            expected = original.live_baseline(self.root, 5, metadata, copies, lambda: None, binding(self.root), selected)
            actual = candidate.live_baseline(self.root, 5, metadata, copies, lambda: None, binding(self.root), selected)
        self.assertEqual(actual, expected)
        self.assertEqual(actual['capture_started_at'], FIXED)
        self.assertEqual(actual['completed_at'], FIXED)
        events = []
        with mock.patch.object(candidate, 'utc', return_value=FIXED), mock.patch.object(time, 'monotonic', return_value=0):
            measured = candidate.live_baseline(self.root, 5, metadata, copies, lambda: None, binding(self.root), selected, observer=events.append)
        self.assertEqual(measured, actual)
        starts = [e['stage'] for e in events if e['type'] == 'live-baseline-stage-start']
        stops = [e['stage'] for e in events if e['type'] == 'live-baseline-stage-stop']
        self.assertEqual(starts, stops)
        self.assertEqual(len(starts), 6)
        self.assertTrue(all(len(candidate.canonical(e)) + 1 <= 512 for e in events))
        public = json.dumps(events)
        for private in ('Author', 'a.epub', 'Mockingjay', 'Suzanne', 'packages', 'source_identity'):
            self.assertNotIn(private, public)

    def test_file_and_proof_caps_are_preserved(self):
        for module in (original, candidate):
            with self.subTest(module=module.__name__), mock.patch.object(module, 'MAX_FILES', 1):
                with self.assertRaises(metadata.Refused):
                    self.census(module)
            with self.subTest(module=module.__name__, cap='fingerprint'), mock.patch.object(module, 'MAX_PROOF', 10):
                with self.assertRaises(metadata.Refused):
                    self.census(module)

    def test_permission_cap_remains_independent_of_fingerprint_cap(self):
        fp, _ = original.walk(self.root, time.monotonic() + 5, metadata, lambda: None)
        fingerprint_bytes = sum(len(original.canonical({p: v})) for p, v in fp.items())
        for module in (original, candidate):
            with self.subTest(module=module.__name__), mock.patch.object(module, 'MAX_PROOF', 2 * (fingerprint_bytes + 1)):
                with self.assertRaisesRegex(metadata.Refused, 'permission proof byte cap'):
                    self.census(module)

    def test_parent_mode_change_during_fused_batch_refuses(self):
        real = metadata._same_directory
        seen = 0
        author = str(Path(self.root) / 'Author')
        def changed(fd, path):
            nonlocal seen
            if path == author:
                seen += 1
                if seen == 2:
                    os.chmod(author, 0o750)
            real(fd, path)
        with mock.patch.object(metadata, '_same_directory', side_effect=changed):
            with self.assertRaisesRegex(metadata.Changed, 'parent changed'):
                self.census(candidate)

    def test_missing_file_after_fused_first_walk_refuses(self):
        real = candidate.walk
        def changed(*args):
            (Path(self.root) / 'notes.txt').unlink()
            return real(*args)
        with mock.patch.object(candidate, 'walk', side_effect=changed):
            with self.assertRaises(metadata.Changed):
                self.census(candidate)

    def test_absolute_parent_replacement_during_fused_batch_refuses(self):
        real = metadata._same_directory
        seen = 0
        author = str(Path(self.root) / 'Author')
        def changed(fd, path):
            nonlocal seen
            if path == author:
                seen += 1
                if seen == 2:
                    Path(author).rename(Path(self.root) / 'replaced-author')
                    Path(author).mkdir()
            real(fd, path)
        with mock.patch.object(metadata, '_same_directory', side_effect=changed):
            with self.assertRaises(metadata.Changed):
                self.census(candidate)

    def test_ignore_symlink_and_directory_symlink_refuse(self):
        marker = Path(self.root) / 'Author/.ll_ignore'
        marker.unlink()
        marker.symlink_to('a.epub')
        with self.assertRaises(metadata.Refused):
            self.census(candidate)
        marker.unlink()
        marker.write_bytes(b'')
        (Path(self.root) / 'directory-link').symlink_to('Author', target_is_directory=True)
        with self.assertRaises(metadata.Refused):
            self.census(candidate)

    def test_byte_descriptor_and_absolute_parent_races_refuse(self):
        for module in (original, candidate):
            for action in ('replace', 'symlink', 'rename-parent', 'rewrite'):
                with self.subTest(module=module.__name__, action=action), tempfile.TemporaryDirectory() as tmp:
                    root = str(Path(tmp) / 'EBooks')
                    path = Path(root) / 'Author/a.epub'
                    write(str(path), self.raw)
                    fp, _ = module.walk(root, time.monotonic() + 5, metadata, lambda: None)
                    real = metadata._grouping_from_archive
                    def changed(archive, relative):
                        row = real(archive, relative)
                        if action == 'replace':
                            replacement = Path(tmp) / 'replacement'
                            replacement.write_bytes(self.raw)
                            os.replace(replacement, path)
                        elif action == 'symlink':
                            path.unlink()
                            path.symlink_to(Path(tmp) / 'outside')
                        elif action == 'rename-parent':
                            parent = path.parent
                            parent.rename(Path(root) / 'old-author')
                            write(str(path), self.raw)
                        else:
                            path.write_bytes(self.raw + b'changed')
                        return row
                    with mock.patch.object(metadata, '_grouping_from_archive', side_effect=changed):
                        with self.assertRaises((metadata.Changed, metadata.Refused, OSError)):
                            module.read_epub(root, 'Author/a.epub', fp['Author/a.epub'], time.monotonic() + 5, metadata, lambda: None)

    def test_complete_directory_fingerprint_change_between_walks_refuses(self):
        real = candidate.walk
        def changed(*args):
            os.chmod(str(Path(self.root) / '.hidden'), 0o750)
            return real(*args)
        with mock.patch.object(candidate, 'walk', side_effect=changed):
            with self.assertRaises(metadata.Changed):
                self.census(candidate)


class SchedulingTests(unittest.TestCase):
    def test_two_index_window_bound_and_out_of_order_determinism(self):
        release, first_started, second_done, lock = threading.Event(), threading.Event(), threading.Event(), threading.Lock()
        state = {'submitted': 0, 'emitted': 0, 'max_outstanding': 0, 'active': 0, 'max_active': 0}
        answer, errors = [], []
        queue_class, made = queue.Queue, []
        class Tasks(queue_class):
            def put_nowait(self, value):
                with lock:
                    state['submitted'] += 1
                    state['max_outstanding'] = max(state['max_outstanding'], state['submitted'] - state['emitted'])
                return super().put_nowait(value)
        def make_queue(*args, **kwargs):
            result = Tasks(*args, **kwargs) if not made else queue_class(*args, **kwargs)
            made.append(result)
            return result
        def read(_root, path, *_):
            with lock:
                state['active'] += 1
                state['max_active'] = max(state['max_active'], state['active'])
            try:
                if path == '0':
                    first_started.set()
                    release.wait(2)
                elif path == '1':
                    first_started.wait(1)
                    second_done.set()
                return {'index': path}
            finally:
                with lock:
                    state['active'] -= 1
        real_canonical = candidate.canonical
        def emitted(row):
            with lock:
                state['emitted'] += 1
            return real_canonical(row)
        def execute():
            try:
                answer.extend(candidate.read_epubs('fixture', ['0', '1', '2', '3'], dict.fromkeys(['0', '1', '2', '3']), time.monotonic() + 2, metadata, lambda: None))
            except BaseException as error:
                errors.append(error)
        with mock.patch.object(candidate, 'read_epub', side_effect=read), mock.patch.object(candidate, 'canonical', side_effect=emitted), mock.patch.object(candidate.queue, 'Queue', side_effect=make_queue):
            control = threading.Thread(target=execute, daemon=True)
            control.start()
            try:
                self.assertTrue(second_done.wait(1))
                with lock:
                    self.assertEqual(state['submitted'], 2)
            finally:
                release.set()
                control.join(2)
        self.assertFalse(control.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(answer, [{'index': p} for p in ['0', '1', '2', '3']])
        self.assertEqual(state['max_active'], 2)
        self.assertEqual(state['max_outstanding'], 2)
        self.assertEqual([q.maxsize for q in made], [2, 2])

    def test_identity_row_proof_cap_refuses_instead_of_truncating(self):
        with mock.patch.object(candidate, 'read_epub', return_value={'row': 'too large'}), mock.patch.object(candidate, 'MAX_PROOF', 10):
            with self.assertRaisesRegex(metadata.Refused, 'identity proof byte cap'):
                candidate.read_epubs('fixture', ['a'], {'a': None}, time.monotonic() + 1, metadata, lambda: None)

    def test_coarse_telemetry_has_complete_totals_and_only_controller_callbacks(self):
        events, controllers = [], []
        paths = ['private-a', 'private-b', 'private-c']
        fingerprints = {p: [['1', '2', str(size), '4', '5', '1'], '0', '0', '0'] for p, size in zip(paths, [7, 11, 13])}
        def observe(event):
            controllers.append(threading.get_ident())
            events.append(event)
        with mock.patch.object(candidate, 'PROGRESS_FILES', 2), mock.patch.object(candidate, 'read_epub', side_effect=lambda _root, path, *_: {'private_path': path}):
            rows = candidate.read_epubs('fixture', paths, fingerprints, time.monotonic() + 1, metadata, lambda: None, observer=observe)
        self.assertEqual(len(rows), 3)
        self.assertEqual([e['completed_file_count'] for e in events], [2, 3])
        self.assertEqual([e['completed_byte_count'] for e in events], [18, 31])
        self.assertEqual(controllers, [threading.get_ident()] * 2)
        self.assertNotIn('private', json.dumps(events))
        max_events = (candidate.MAX_FILES + candidate.PROGRESS_FILES - 1) // candidate.PROGRESS_FILES + 2 * 7 + 3
        self.assertLess(max_events * 512, 1024 * 1024)

    def child(self, mode):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'artifact'
            program = r'''
import importlib.util, pathlib, signal, sys, threading, time
here, marker, mode = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
import os
sys.path.insert(0, str(os.environ.get('COPY_TEST_MODULES') or here.parents[2] / 'kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert'))
def load(name, path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
c=load('candidate',here/'bound_census_collectors.py');p=load('producer',here/'capture-live-byte-baseline.py')
import epub_metadata as metadata
blocked=threading.Event();started=threading.Event()
def read(_root,path,*_):
 if path=='a':
  started.set();blocked.wait()
 if mode=='later-error':
  started.wait(1);raise ValueError('/private/epub/path <privateXML>')
 blocked.wait()
c.read_epub=read
def operation():
 if mode=='signal':
  def stopped(*_):raise p.Stop('private deadline')
  signal.signal(signal.SIGALRM,stopped);signal.setitimer(signal.ITIMER_REAL,.2)
 deadline=time.monotonic()+(.2 if mode=='deadline' else 10)
 c.read_epubs('fixture',['a','b'],{'a':None,'b':None},deadline,metadata,lambda:None)
 marker.write_text('invalid artifact');return 0
p.run=operation;p.main()
'''
            result = subprocess.run([sys.executable, '-B', '-c', program, str(HERE), str(marker), mode], capture_output=True, timeout=2)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(marker.exists())
            self.assertEqual(result.stderr, b'')
            self.assertNotIn(b'private', result.stdout)
            event = json.loads(result.stdout)
            self.assertEqual(event['type'], 'live-baseline-refused')
            self.assertEqual(event['production_writes'], 0)
            return event

    def test_deadline_exits_with_two_blocked_fake_readers(self):
        self.assertEqual(self.child('deadline')['error_class'], 'Refused')

    def test_signal_exits_with_two_blocked_fake_readers(self):
        self.assertEqual(self.child('signal')['error_class'], 'Stop')

    def test_later_index_error_does_not_wait_for_blocked_first_index(self):
        self.assertEqual(self.child('later-error')['error_class'], 'ValueError')


class ClosureTests(unittest.TestCase):
    def test_original_per_file_read_function_is_unchanged(self):
        def body(path):
            tree = ast.parse(path.read_text())
            return ast.dump(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'read_epub'), include_attributes=False)
        self.assertEqual(body(HERE / 'bound_census_collectors.py'), body(HERE.parent / 'live-baseline-host/dependencies/bound_census_collectors.py'))

    def test_collector_pin_and_unchanged_consumer_age(self):
        self.assertEqual(producer.COLLECTOR_SHA256, hashlib.sha256((HERE / 'bound_census_collectors.py').read_bytes()).hexdigest())
        self.assertEqual(copies.SNAPSHOT_MAX_AGE, 300)
        self.assertEqual(candidate.MAX_PROOF, 32 * 1024 * 1024)
        self.assertEqual(candidate.READ_STREAMS, 2)
        self.assertEqual(candidate.PROGRESS_FILES, 128)

    def test_telemetry_does_not_swallow_owning_deadline_signal(self):
        with mock.patch.object(producer.os, 'write', side_effect=producer.Stop('deadline')):
            with self.assertRaises(producer.Stop):
                producer.best_effort_event({'type': 'fixture'})

    def test_closed_command_refuses_before_core_import_or_any_source_work(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith('COPY_')}
        result = subprocess.run([sys.executable, '-B', str(HERE / 'capture-live-byte-baseline.py')], env=env, capture_output=True, timeout=2)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b'')
        self.assertEqual(json.loads(result.stdout)['code'], 'phase_not_authorized')

    def test_closed_gate_exits_with_full_undrained_stdout(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith('COPY_')}
        read_fd, write_fd = os.pipe()
        child = None
        try:
            os.set_blocking(write_fd, False)
            capacity = fcntl.fcntl(write_fd, fcntl.F_GETPIPE_SZ)
            self.assertLessEqual(capacity, 1024 * 1024)
            self.assertEqual(os.write(write_fd, b'x' * capacity), capacity)
            os.set_blocking(write_fd, True)
            child = subprocess.Popen([sys.executable, '-B', str(HERE / 'capture-live-byte-baseline.py')], env=env, stdout=write_fd, stderr=subprocess.PIPE)
            self.assertEqual(child.wait(timeout=2), 2)
            self.assertEqual(child.communicate(timeout=1)[1], b'')
        finally:
            if child is not None and child.poll() is None:
                child.kill()
                child.wait(timeout=1)
            os.close(write_fd)
            os.close(read_fd)


if __name__ == '__main__':
    unittest.main()
