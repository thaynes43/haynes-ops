#!/usr/bin/env python3
"""Finite SOURCE PG16/temp fixtures; nice19 serial, no live corpus or load."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
CORE = HERE.parent
EPUB = Path(os.environ.get('COPY_TEST_MODULES') or HERE.parents[2] / 'kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert')
sys.path[:0] = [str(HERE), '/copy-writer', str(CORE), str(EPUB), str(CORE / 'live-performance')]
import source_health
import bound_census_collectors as collectors
from test_book_copy_writer import PG16, writer, copies, metadata
from test_epub_metadata import fixture, write

spec = importlib.util.spec_from_file_location('source_adapter', HERE / 'capture-source-stat-census.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
from outcome_test_cases import OutcomeCases


def binding(root):
    return {'namespace': 'fixture', 'pod_name': 'fixture',
            'pod_uid': '11111111-1111-4111-8111-111111111111',
            'job_uid': '22222222-2222-4222-8222-222222222222', 'node': 'fixture-node',
            'image': 'fixture', 'image_id': 'fixture@sha256:' + 'a' * 64,
            'pod_spec_sha256': 'b' * 64, 'restarts': 0, 'mount_root': root,
            'nfs_server': 'fixture', 'nfs_export': '/fixture'}


class SourceTests(OutcomeCases, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = PG16()
        try: cls.pg.__enter__()
        except BaseException:
            cls.pg.__exit__(); raise

    @classmethod
    def tearDownClass(cls):
        cls.pg.__exit__()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = str(Path(self.tmp.name) / 'EBooks')
        write(str(Path(self.root) / 'Author/Book.epub'), fixture())
        write(str(Path(self.root) / '.ll_ignore'), b'')
        write(str(Path(self.root) / 'notes.txt'), b'complete')
        self.events = []

    def fence(self):
        return writer.PrimaryShareFence(self.pg.dsn, time.time() + 10, phase_token='a' * 32)

    def controller(self, fence):
        return source_health.SourceHealth(fence, collectors, self.events.append,
                                         {'phase_token': 'a' * 32})

    def test_exact_stat_schema_matches_without_controller(self):
        fixed = '2026-10-09T12:00:00+00:00'
        with mock.patch.object(collectors, 'utc', return_value=fixed):
            expected = collectors.stat_census(self.root, time.monotonic() + 10,
                                             metadata, copies, lambda: None, binding(self.root))
            with self.fence() as fence, self.controller(fence) as controller:
                actual = controller.collect(self.root, fence.deadline, metadata, copies, binding(self.root))
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual['all_file_fingerprints']), 3)
        self.assertEqual(actual['module_sha256'], collectors.module_hashes(metadata, copies))

    def test_each_of_both_walks_has_actual_sql_before_and_after(self):
        timeline = []
        original = collectors._walk
        def walked(*args, **kwargs):
            timeline.append('walk-start')
            value = original(*args, **kwargs)
            timeline.append('walk-end')
            return value
        with self.fence() as fence:
            now = time.monotonic()
            with mock.patch.object(writer.time, 'monotonic', return_value=now), \
                 mock.patch.object(collectors, '_walk', side_effect=walked), \
                 source_health.SourceHealth(fence, collectors, lambda event: timeline.append('actual-query'), {}) as controller:
                controller.collect(self.root, fence.deadline, metadata, copies, binding(self.root))
        self.assertEqual(timeline, ['actual-query', 'walk-start', 'walk-end', 'actual-query'] * 2)
        self.assertIs(collectors._walk, original)

    def test_scan_guard_query_and_lease_events_are_same_owner_at_one_second(self):
        with self.fence() as fence, self.controller(fence) as controller:
            now = time.monotonic()
            with mock.patch.object(writer.time, 'monotonic', return_value=now): controller.health()
            self.events.clear()
            with mock.patch.object(fence.db, 'execute', wraps=fence.db.execute) as execute:
                with mock.patch.object(writer.time, 'monotonic', return_value=now + .999):
                    controller.scan_guard(); controller.scan_guard()
                self.assertEqual((execute.call_count, len(self.events)), (0, 0))
                with mock.patch.object(writer.time, 'monotonic', return_value=now + 1): controller.scan_guard()
                self.assertEqual((execute.call_count, len(self.events)), (1, 1))
                controller.health(); controller.health()
                self.assertEqual((execute.call_count, len(self.events)), (3, 3))
                self.assertTrue(all(e['backend_pid'] == fence.pid and e['share_tables'] == list(writer.TABLES)
                                    for e in self.events))

    def test_foreign_thread_cannot_query_or_emit_a_healthy_lease(self):
        with self.fence() as fence, self.controller(fence) as controller:
            refused = []
            def foreign():
                try: controller.scan_guard()
                except metadata.Refused: refused.append(True)
            with mock.patch.object(fence.db, 'execute', side_effect=AssertionError('foreign SQL')):
                child = threading.Thread(target=foreign)
                child.start(); child.join(timeout=1)
            self.assertFalse(child.is_alive())
            self.assertEqual(refused, [True])
            self.assertEqual(self.events, [])

    def test_backend_loss_after_walk_refuses_before_return_and_restores_callable(self):
        original = collectors._walk
        def killed(*args, **kwargs):
            value = original(*args, **kwargs)
            self.pg.admin.execute('SELECT pg_terminate_backend(%s)', (fence.pid,))
            return value
        with self.fence() as fence, self.controller(fence) as controller:
            with mock.patch.object(collectors, '_walk', side_effect=killed) as configured, \
                 self.assertRaises(metadata.Refused):
                controller.collect(self.root, fence.deadline, metadata, copies, binding(self.root))
            self.assertEqual(configured.call_count, 1)
        self.assertIs(collectors._walk, original)
        self.assertEqual(len(self.events), 1)

    def test_file_change_between_walks_refuses_complete_stat_proof(self):
        original = collectors._walk
        calls = []
        def changed(*args, **kwargs):
            value = original(*args, **kwargs)
            calls.append(True)
            if len(calls) == 1: write(str(Path(self.root) / 'notes.txt'), b'changed')
            return value
        with self.fence() as fence, self.controller(fence) as controller, \
             mock.patch.object(collectors, '_walk', side_effect=changed), \
             self.assertRaises(metadata.Changed):
            controller.collect(self.root, fence.deadline, metadata, copies, binding(self.root))
        self.assertEqual(len(calls), 2)

    def test_validated_injected_phase_is_passed_to_original_fence(self):
        environment = {'COPY_SOURCE_PHASE_READY': '1', 'COPY_PHASE_TOKEN': 'b' * 32,
                       'COPY_JOB_UID': binding(self.root)['job_uid'], 'COPY_POD_UID': binding(self.root)['pod_uid'],
                       'COPY_SOURCE_DEADLINE_EPOCH': str(time.time() + 10), 'DATABASE_URL': self.pg.dsn,
                       'COPY_SOURCE_FENCE_JSON': json.dumps({'schema': 1, 'phase_token': 'b' * 32,
                           'first_service_stop_observed_at': adapter.utc(), 'service_fence_checked_at': adapter.utc(),
                           'publisher_scope_sha256': 'f' * 64})}
        class StopBeforeConnect(BaseException): pass
        def entered(*args, **kwargs):
            self.assertEqual(kwargs['phase_token'], 'b' * 32)
            raise StopBeforeConnect()
        with mock.patch.dict(os.environ, {'COPY_PHASE_TOKEN': 'c' * 32}), \
             mock.patch.object(writer, 'PrimaryShareFence', side_effect=entered), \
             self.assertRaises(StopBeforeConnect): adapter.run(environment)


class ClosureTests(unittest.TestCase):
    def test_pin_closure_and_stat_only_source_without_background_pg_thread(self):
        self.assertEqual(adapter.COLLECTOR_SHA256, hashlib.sha256((CORE / 'live-performance/bound_census_collectors.py').read_bytes()).hexdigest())
        self.assertEqual(adapter.SOURCE_HEALTH_SHA256, hashlib.sha256((HERE / 'source_health.py').read_bytes()).hexdigest())
        source = (HERE / 'capture-source-stat-census.py').read_text()
        self.assertNotIn('threading', source)
        self.assertNotIn('read_epubs', source)
        self.assertNotIn('live_baseline', source)
        self.assertIn('deadline<=first+250', source)
        self.assertIn('time.time()-checked>65', source)
        self.assertEqual(copies.SNAPSHOT_MAX_AGE, 300)
        ast.parse(source)

    def test_closed_gate_refuses_before_modules_or_production_access(self):
        environment = {k: v for k, v in os.environ.items() if not k.startswith('COPY_')}
        result = subprocess.run([sys.executable, '-B', str(HERE / 'capture-source-stat-census.py')],
                                env=environment, capture_output=True, timeout=2)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b'')
        self.assertEqual(json.loads(result.stdout)['code'], 'phase_not_authorized')

    def test_lease_telemetry_cap_and_stdout_mode_and_deadline_propagation(self):
        event = {'type': 'fence_healthy', 'backend_pid': 12345, 'application_name': 'issue825-duplicate-share-fence-' + 'a' * 32,
                 'deadline_epoch_ms': 1791565200000, 'share_tables': list(writer.TABLES), 'read_only': 'on',
                 'phase_token': 'a' * 32, 'job_uid': binding('unused')['job_uid'], 'pod_uid': binding('unused')['pod_uid']}
        with mock.patch.object(adapter.os, 'write', return_value=1) as written:
            before = os.get_blocking(sys.stdout.fileno())
            adapter.emit(event)
            self.assertEqual(os.get_blocking(sys.stdout.fileno()), before)
            self.assertLessEqual(len(written.call_args[0][1]), 512)
        with mock.patch.object(adapter.os, 'write', side_effect=adapter.Stop()), \
             self.assertRaises(adapter.Stop): adapter.emit(event)


if __name__ == '__main__': unittest.main()
