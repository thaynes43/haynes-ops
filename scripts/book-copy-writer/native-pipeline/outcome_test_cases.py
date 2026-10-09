"""Finite temporary outcome cases sharing the SOURCE suite's one PG16 fixture."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import threading
import time
import types
from unittest import mock

import outcome_mailbox as mailbox
import source_health
from test_book_copy_writer import copies, metadata, writer
from test_epub_metadata import fixture, write

HERE = Path(__file__).parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


core = load('outcome_core_fixture', HERE / 'verify-copy-outcome-readonly.py')
transport = load('outcome_transport_fixture', HERE / 'exchange-source-outcome.py')


class OutcomeCases:
    def outcome_fixture(self):
        keeper, extras = 'Fixture/keeper.epub', ['Fixture/extra1.epub', 'Fixture/extra2.epub']
        state = str(Path(self.tmp.name) / '.epub-convert')
        Path(state).mkdir()
        (Path(self.root) / '.ll_ignore').unlink()
        raw = fixture()
        digest = hashlib.sha256(raw).hexdigest()
        for path in [keeper, *extras]: write(str(Path(self.root) / path), raw)
        original = {p: [[str(n) for n in value[0]], *(str(n) for n in value[1:])]
                    for p, value in copies.file_fingerprints(self.root, time.monotonic() + 5).items()}
        events, entries = [], []
        for path in extras:
            identity = tuple(int(n) for n in original[path][0])
            moved = copies.move_copy(path, self.root, state, digest, identity, 'e' * 64,
                                     lambda **_: None, deadline=time.monotonic() + 5)
            events.append({'msg': 'epub_copy_consolidate', **moved, 'keeper': keeper})
            entries.append({'path': path, 'sha256': digest, 'keeper': keeper, 'keeper_sha256': digest})
        env = {'COPY_SOURCE_PHASE_READY': '1', 'COPY_PHASE_TOKEN': 'a' * 32,
               'COPY_JOB_UID': '11111111-1111-4111-8111-111111111111',
               'COPY_POD_UID': '22222222-2222-4222-8222-222222222222',
               'COPY_SOURCE_DEADLINE_EPOCH': str(time.time() + 10)}
        payload = {'phase_token': env['COPY_PHASE_TOKEN'], 'job_uid': env['COPY_JOB_UID'], 'pod_uid': env['COPY_POD_UID'],
                   'library': {'schema': 2, 'kind': 'validated_byte_census', 'all_file_fingerprints': original},
                   'selection': {'entries': entries}, 'events': events, 'snapshot_sha256': 'e' * 64}
        scope = {p: digest for p in [keeper, *extras]}
        scope_sha = mailbox.sha(mailbox.canonical(scope))
        request = {'schema': 1, 'type': 'copy_outcome_request', 'phase_token': payload['phase_token'],
                   'job_uid': payload['job_uid'], 'pod_uid': payload['pod_uid'], 'runtime_module_sha256': mailbox.MODULES,
                   'selected_scope_sha256': scope_sha, 'main_receipt_sha256': 'f' * 64,
                   'deadline_epoch': time.time() + 3, 'payload': payload}
        directory = Path(self.tmp.name) / 'outcome'
        directory.mkdir()
        modules = Path(self.tmp.name) / 'runtime'
        modules.mkdir()
        for name, module in [('epub_copies.py', copies), ('epub_metadata.py', metadata), ('bound_census.py', core.bound)]:
            (modules / name).write_bytes(Path(module.__file__).read_bytes())
        # Only fixture roots/scope and supplied test-module location differ.
        # The production SOURCE always uses /copy-writer and the pinned scope.
        self.enterContext(mock.patch.object(core, 'ROOT', self.root))
        self.enterContext(mock.patch.object(core, 'STATE', state))
        self.enterContext(mock.patch.object(mailbox, 'KEEPER', keeper))
        self.enterContext(mock.patch.object(mailbox, 'SCOPE_SHA256', scope_sha))
        verifier = types.SimpleNamespace(collect=lambda *args: core.collect(*args, module_dir=str(modules)))
        return request, env, directory, verifier

    def publish_request(self, directory, request):
        path = directory / mailbox.REQUEST
        path.write_bytes(mailbox.canonical(request)); path.chmod(0o400)

    def owner(self, verifier, controller, env, directory):
        return mailbox.SourceOutcome(verifier, controller, metadata, copies, env,
                                     float(env['COPY_SOURCE_DEADLINE_EPOCH']), str(directory), self.events.append)

    def test_owner_outcome_real_retained_receipts_and_both_walk_sql_brackets(self):
        request, env, directory, verifier = self.outcome_fixture()
        self.publish_request(directory, request)
        timeline = []
        original = copies.file_fingerprints
        def walk(*args, **kwargs):
            timeline.append('walk-start'); result = original(*args, **kwargs); timeline.append('walk-end'); return result
        with self.fence() as fence, source_health.SourceHealth(fence, None, lambda _: timeline.append('query'), {}) as controller, \
             mock.patch.object(copies, 'file_fingerprints', side_effect=walk):
            owner = self.owner(verifier, controller, env, directory)
            self.assertTrue(owner.process_pending())
            self.assertFalse(owner.process_pending())
        walks = [i for i, value in enumerate(timeline) if value == 'walk-start']
        self.assertEqual(len(walks), 2)
        for index in walks:
            self.assertEqual(timeline[index - 1:index + 3], ['query', 'walk-start', 'walk-end', 'query'])
        response = json.loads((directory / mailbox.RESPONSE).read_bytes())
        self.assertEqual(response['outcome']['actual_moved_count'], 2)
        self.assertTrue(response['outcome']['every_unapproved_file_unchanged'])
        self.assertEqual(response['request_sha256'], mailbox.sha(mailbox.canonical(request)))
        self.assertEqual(response['main_receipt_sha256'], request['main_receipt_sha256'])

    def test_owner_outcome_lost_backend_after_first_walk_publishes_no_response(self):
        request, env, directory, verifier = self.outcome_fixture(); self.publish_request(directory, request)
        original = copies.file_fingerprints
        with self.fence() as fence, self.controller(fence) as controller:
            def walked(*args, **kwargs):
                result = original(*args, **kwargs)
                with writer.psycopg.connect(self.pg.dsn, autocommit=True) as observer:
                    observer.execute('SELECT pg_terminate_backend(%s)', (fence.pid,))
                return result
            with mock.patch.object(copies, 'file_fingerprints', side_effect=walked), self.assertRaises(Exception):
                self.owner(verifier, controller, env, directory).process_pending()
        self.assertFalse((directory / mailbox.RESPONSE).exists())

    def test_owner_outcome_same_backend_new_transaction_refuses_before_proof(self):
        request, env, directory, verifier = self.outcome_fixture(); self.publish_request(directory, request)
        with self.fence() as fence, self.controller(fence) as controller:
            pid = fence.pid
            fence.db.rollback(); fence.db.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            with mock.patch.object(verifier, 'collect', wraps=verifier.collect) as collect, self.assertRaises(metadata.Refused):
                self.owner(verifier, controller, env, directory).process_pending()
            self.assertEqual(fence.db.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid'], pid)
            collect.assert_not_called()
        self.assertFalse((directory / mailbox.RESPONSE).exists())

    def test_owner_outcome_foreign_thread_refuses_before_query_or_proof(self):
        request, env, directory, verifier = self.outcome_fixture(); self.publish_request(directory, request)
        errors = []
        with self.fence() as fence, self.controller(fence) as controller, \
             mock.patch.object(fence.db, 'execute', wraps=fence.db.execute) as execute:
            owner = self.owner(verifier, controller, env, directory)
            def worker():
                try: owner.process_pending()
                except BaseException as error: errors.append(error)
            thread = threading.Thread(target=worker); thread.start(); thread.join(1)
            self.assertFalse(thread.is_alive()); self.assertIsInstance(errors[0], metadata.Refused)
            execute.assert_not_called()
        self.assertFalse((directory / mailbox.RESPONSE).exists())

    def test_owner_outcome_immutable_request_inode_replacement_refuses(self):
        request, env, directory, verifier = self.outcome_fixture(); self.publish_request(directory, request)
        original = verifier.collect
        def changed(*args):
            result = original(*args)
            (directory / mailbox.REQUEST).unlink(); self.publish_request(directory, request)
            return result
        verifier.collect = changed
        with self.fence() as fence, self.controller(fence) as controller, self.assertRaises(metadata.Refused):
            self.owner(verifier, controller, env, directory).process_pending()
        self.assertFalse((directory / mailbox.RESPONSE).exists())

    def test_owner_outcome_semantic_identity_scope_module_deadline_refusals(self):
        request, env, directory, verifier = self.outcome_fixture()
        with self.fence() as fence, self.controller(fence) as controller:
            owner = self.owner(verifier, controller, env, directory)
            owner.validate(request)
            changes = [('phase_token', 'b' * 32), ('job_uid', '33333333-3333-4333-8333-333333333333'),
                       ('runtime_module_sha256', {}), ('selected_scope_sha256', '0' * 64),
                       ('main_receipt_sha256', 'invalid'), ('deadline_epoch', time.time() - 1)]
            for key, value in changes:
                altered = copy.deepcopy(request); altered[key] = value
                with self.subTest(key=key), self.assertRaises(metadata.Refused): owner.validate(altered)
            altered = copy.deepcopy(request); altered['payload']['selection']['entries'][0]['keeper'] = 'Fixture/fabricated.epub'
            with self.assertRaises(metadata.Refused): owner.validate(altered)

    def test_private_exchange_child_has_no_pg_and_owner_returns_bound_proof(self):
        request, env, directory, verifier = self.outcome_fixture()
        ready, allow, results, errors = threading.Event(), threading.Event(), [], []
        def waiting(_): ready.set(); allow.wait(1)
        def child():
            try: results.append(transport.exchange(mailbox.canonical(request), request['deadline_epoch'], env, str(directory), waiting))
            except BaseException as error: errors.append(error)
        with self.fence() as fence, self.controller(fence) as controller:
            worker = threading.Thread(target=child, daemon=True); worker.start()
            self.assertTrue(ready.wait(1))
            try: self.assertTrue(self.owner(verifier, controller, env, directory).process_pending())
            finally: allow.set(); worker.join(1)
            self.assertFalse(worker.is_alive()); self.assertEqual(errors, [])
        self.assertEqual(json.loads(results[0])['outcome']['actual_moved_count'], 2)
        with self.assertRaises(transport.Refused):
            transport.exchange(mailbox.canonical(request), request['deadline_epoch'], env, str(directory))

    def test_owner_outcome_actual_alarm_interrupts_blocked_fake_io_and_restores_original_clock(self):
        request, env, directory, verifier = self.outcome_fixture()
        request['deadline_epoch'] = time.time() + .08; self.publish_request(directory, request)
        verifier.collect = lambda *_: threading.Event().wait(2)
        class AlarmStop(BaseException): pass
        def stopped(*_): raise AlarmStop()
        previous = signal.signal(signal.SIGALRM, stopped)
        try:
            signal.setitimer(signal.ITIMER_REAL, 2)
            with self.fence() as fence, self.controller(fence) as controller, self.assertRaises(AlarmStop):
                self.owner(verifier, controller, env, directory).process_pending()
            self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0)
            self.assertLess(signal.getitimer(signal.ITIMER_REAL)[0], 2)
            self.assertFalse((directory / mailbox.RESPONSE).exists())
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0); signal.signal(signal.SIGALRM, previous)
