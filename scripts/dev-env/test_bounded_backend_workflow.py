"""Offline synthetic requirements regressions; no endpoint or credential contact."""
import contextlib
import copy
import importlib.util
import io
import json
import multiprocessing
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock

# Explicit sibling import also works under the CI interpreter's isolated (-I) mode.
SOURCE = Path(__file__).with_name('bounded_backend_workflow.py').resolve()
SPEC = importlib.util.spec_from_file_location('bounded_backend_workflow', SOURCE)
workflow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workflow)
BASE = 1_700_000_000
FSID = 'fsid-synthetic'
MANAGER = 'mgr-synthetic'
ENDPOINT = '203.0.113.10:9283/metrics'
OTHER_ENDPOINT = '203.0.113.11:9283/metrics'
HEAD = 'a' * 40
MERGED = 'b' * 40
BINDING = {'identity': 'synthetic-owner', 'scope': 'synthetic-scope', 'attempt': 'attempt-1'}


def invoke(path, command, **flags):
    argv = ['--ledger', str(path), command]
    for key, value in flags.items():
        if value is False or value is None:
            continue
        for item in value if isinstance(value, list) else [value]:
            argv.append('--' + key.replace('_', '-'))
            if item is not True:
                argv.append(str(item))
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = workflow.main(argv)
        except SystemExit as exc:
            return exc.code, {'parse_error': True}
    return code, json.loads(out.getvalue())


def competing_writer(path, revision, holding, contender_done, pipe, first):
    try:
        if first:
            original_dump = workflow.atomic_dump

            def hold_before_commit(*args, **kwargs):
                # Deterministic overlap: writer 1 holds the CAS lock while writer 2
                # attempts exactly the same revision, BEFORE writer 1 replaces it.
                holding.set()
                if not contender_done.wait(timeout=5):
                    raise RuntimeError('contender did not finish bounded attempt')
                return original_dump(*args, **kwargs)

            workflow.atomic_dump = hold_before_commit
        elif not holding.wait(timeout=5):
            raise RuntimeError('first writer did not reach commit boundary')
        result = invoke(path, 'admit', **BINDING, now=BASE + 2500,
                        expect_revision=revision, slot='version', intent_id='race', decision='admit')
        pipe.send(result)
    finally:
        if not first:
            contender_done.set()
        pipe.close()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.json'
        self.now = BASE

    def state(self):
        return workflow.load_ledger(self.path)

    def call(self, command, **flags):
        flags = {**BINDING, 'now': self.now, **flags}
        if command == 'record-residual':
            flags.setdefault('cnp_uid', 'cnp-' + flags['uid'])
        if command != 'init' and 'expect_revision' not in flags:
            flags['expect_revision'] = self.state()['revision']
        return invoke(self.path, command, **flags)

    def good(self, command, **flags):
        code, body = self.call(command, **flags)
        self.assertEqual(code, 0, body)
        return body

    def no_dispatch(self, command, **flags):
        code, body = self.call(command, **flags)
        self.assertFalse(body.get('dispatch', False), body)
        return code, body

    def initialize(self, existing=False, **flags):
        return self.good('init', owner_kind='persistent', owner_liveness='observed',
                         owner_ready=True, owner_live=True, fsid=FSID,
                         endpoint=[ENDPOINT, OTHER_ENDPOINT],
                         covered_endpoint=[ENDPOINT] if existing else None,
                         planned_revalidation=True, **flags)

    def reserve(self, deadline=BASE + 10000):
        self.good('reserve-cleanup', seconds=180, deadline=deadline, ci_estimate_s=900)

    def prepare(self, head=HEAD, manager=MANAGER, endpoint=ENDPOINT, **flags):
        return self.call('prepare-candidate', head=head, reviewed_head=head,
                         ci_head=head, pr='pr-synthetic', manager=manager,
                         endpoint=endpoint, flux_local='success', diff_scope='success',
                         ci_started_at=BASE, ci_finished_at=self.now, **flags)

    def ready(self, existing=False):
        self.initialize(existing)
        self.reserve()
        self.now = BASE + 2500  # CI exceeds BOTH 300s and the source author's 1800s.
        if not existing:
            code, body = self.prepare()
            self.assertEqual(code, 0, body)

    def admit(self, slot, **flags):
        return self.good('admit', slot=slot, intent_id='intent-' + slot,
                         decision='admit', **flags)

    def outcome(self, slot, outcome='success', **flags):
        proof = {}
        if slot in ('ceph-status', 'revalidation'):
            proof = dict(observed_fsid=FSID, observed_manager=MANAGER,
                         observed_endpoint=ENDPOINT)
        return self.call('record-outcome', **{
            'intent_id': 'intent-' + slot, 'outcome': outcome, 'request_at': self.now,
            'observed_at': self.now, 'receipt': 'receipt-' + slot,
            'body_sha256': 'c' * 64, **proof, **flags})

    def discover(self):
        for slot in workflow.DISCOVERY_SLOTS:
            self.assertTrue(self.admit(slot)['dispatch'])
            code, body = self.outcome(slot)
            self.assertEqual(code, 0, body)

    def select(self, existing=False, **flags):
        return self.call('select-candidate', **{
            'branch': 'existing' if existing else 'new', 'verified_fsid': FSID,
            'verified_manager': MANAGER, 'verified_endpoint': ENDPOINT,
            'proof_receipt': 'receipt-ceph-status', **flags})

    def deliver(self, existing=False):
        code, body = self.select(existing)
        self.assertEqual(code, 0, body)
        if not existing:
            self.good('record-merge', pr_head=HEAD, merged_sha=MERGED,
                      flux_source=MERGED, flux_applied=MERGED)
        self.good('observe-delivery', deployment='ready', grant='ready', cnp='ready')

    def metrics_ready(self, existing=False):
        self.ready(existing)
        self.discover()
        self.deliver(existing)
        self.now += 700  # deployment time is not the proof freshness window.
        self.assertTrue(self.admit('revalidation')['dispatch'])
        code, body = self.outcome('revalidation')
        self.assertEqual(code, 0, body)

    def metrics(self, **flags):
        return self.call('admit', **{
            'slot': 'metrics', 'intent_id': 'intent-metrics', 'decision': 'admit',
            'observed_fsid': FSID, 'observed_manager': MANAGER, 'observed_endpoint': ENDPOINT,
            'proof_receipt': 'receipt-revalidation', **flags})

    def block(self, kind='recoverable'):
        self.good('record-blocker', kind=kind)
        return self.state()['blockers'][-1].get('id', 'blocker-' + str(len(self.state()['blockers'])))

    def resolve(self, identifier, **flags):
        return self.call('resolve-blocker', **{
            'blocker_id': identifier, 'reason': 'dependency-restored',
            'prerequisite_ref': 'verified-external-receipt', 'verified_at': self.now, **flags})

    def audit_fixture(self):
        self.good('record-residual', uid='own', owner_class='own', active='false',
                  expires_at=self.now - 1, cnp='absent', reason='natural-expiry', receipt='expired-own')
        return dict(binding=BINDING, receipt='audit', observed_at=self.now, complete=True,
                    policy=dict(ref='policy-synthetic', present=False),
                    grants=[dict(uid='own', owner_class='own', expires_at=self.now - 1,
                                 active=False, cnp_uid='cnp-own', cnp_present=False)],
                    cnps=[dict(uid='cnp-own', present=False)])

    def assert_structured_rejection(self, command, **flags):
        before = self.path.read_bytes()
        code, body = invoke(self.path, command, **{
            **BINDING, 'now': self.now, 'expect_revision': 1, **flags})
        self.assertNotEqual(code, 0, body)
        self.assertIs(body['ok'], False)
        self.assertIs(body['dispatch'], False)
        self.assertEqual(self.path.read_bytes(), before)

    def test_recoverable_blocker_resolution_resumes_only_unused_work_and_is_audited(self):
        self.ready()
        blocker = self.block()
        self.no_dispatch('admit', slot='version', intent_id='intent-version', decision='admit')
        before = self.state()
        self.now += 1
        code, body = self.resolve(blocker)
        self.assertEqual(code, 0, body)
        state = self.state()
        self.assertEqual(state['owner'], before['owner'])
        self.assertEqual(state['intents'], before['intents'])
        self.assertEqual(state['slot_intent'], before['slot_intent'])
        self.assertEqual(state['proof'], before['proof'])
        record = state['blockers'][0]
        self.assertEqual(record['resolution'], dict(reason='dependency-restored',
                         prerequisite_ref='verified-external-receipt', verified_at=self.now,
                         resolved_at=self.now, binding=BINDING))
        resolved = self.path.read_bytes()
        self.assertNotEqual(self.resolve(blocker)[0], 0)
        self.assertEqual(self.path.read_bytes(), resolved)
        self.assertTrue(self.admit('version')['dispatch'])
        self.assertFalse(body['operation_complete'])
        self.assertFalse(body['successor_authorized'])

    def test_resolution_cannot_refund_or_overwrite_consumed_non_success_slots(self):
        for outcome in (None, 'denied', 'unknown', 'uncertain', 'failure'):
            with self.subTest(outcome=outcome):
                self.path = Path(self.tmp.name) / ('resolution-' + str(outcome))
                self.now = BASE
                self.ready()
                self.admit('version')
                if outcome:
                    self.assertEqual(self.outcome('version', outcome)[0], 0)
                blocker = self.block()
                before = self.path.read_bytes()
                self.assertNotEqual(self.resolve(blocker)[0], 0)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertFalse(self.admit('version')['dispatch'])
                self.no_dispatch('admit', slot='permissions', intent_id='next', decision='admit')
                self.good('record-residual', uid='partial', owner_class='own', active='true',
                          expires_at=self.now + 500, cnp='present', receipt='partial')
        for decision in ('pending', 'denied', 'unknown', 'uncertain'):
            with self.subTest(decision=decision):
                self.path = Path(self.tmp.name) / ('decision-' + decision)
                self.now = BASE
                self.ready()
                self.good('admit', slot='version', intent_id='spent', decision=decision)
                blocker = self.block()
                before = self.path.read_bytes()
                self.assertNotEqual(self.resolve(blocker)[0], 0)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertNotEqual(self.call('admit', slot='version', intent_id='retry', decision='admit')[0], 0)

    def test_resolution_binding_reference_and_observation_are_required(self):
        self.ready()
        blocker = self.block()
        for flags in ([{key: 'wrong'} for key in BINDING] +
                      [{'blocker_id': 'missing'}, {'reason': ''}, {'prerequisite_ref': ''},
                       {'verified_at': self.now - 1}, {'verified_at': self.now + 1},
                       {'now': self.now - 1}, {'expect_revision': 0}]):
            with self.subTest(flags=flags):
                before = self.path.read_bytes()
                self.assertNotEqual(self.resolve(blocker, **flags)[0], 0)
                self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.resolve(blocker)[0], 0)

    def test_resolution_does_not_resurrect_lost_owner_or_turn_end(self):
        for kind in ('lost-owner', 'turn-end'):
            with self.subTest(kind=kind):
                self.path = Path(self.tmp.name) / kind
                self.now = BASE
                self.ready()
                blocker = self.block()
                terminal = self.block(kind)
                before = self.path.read_bytes()
                self.assertNotEqual(self.resolve(terminal)[0], 0)
                self.assertNotEqual(self.resolve(blocker)[0], 0)
                self.assertEqual(self.path.read_bytes(), before)
                self.no_dispatch('admit', slot='version', intent_id='v', decision='admit')
                self.now += 20000
                self.good('record-residual', uid='partial', owner_class='own', active='true',
                          expires_at=self.now + 300, cnp='present', receipt='partial')

    def test_resolution_retains_owner_reserve_and_proof_time_gates(self):
        for gate in ('fresh', 'stale', 'near-expiry', 'deadline', 'reserve', 'not-ready', 'not-live', 'lost'):
            with self.subTest(gate=gate):
                self.path = Path(self.tmp.name) / gate
                self.now = BASE
                self.metrics_ready()
                blocker = self.block()
                proof = copy.deepcopy(self.state()['proof'])
                if gate in ('not-ready', 'not-live', 'lost'):
                    state = self.state()
                    state['owner'][{'not-ready': 'ready', 'not-live': 'live', 'lost': 'lost'}[gate]] = gate == 'lost'
                    workflow.atomic_dump(self.path, state)
                if gate == 'fresh':
                    self.now += 1
                elif gate == 'stale':
                    self.now += 301
                elif gate == 'near-expiry':
                    self.now += 292
                elif gate == 'deadline':
                    self.now = BASE + 10001
                elif gate == 'reserve':
                    self.good('reserve-cleanup', seconds=180, deadline=self.now + 185)
                before = self.path.read_bytes()
                code, body = self.resolve(blocker)
                if gate in ('fresh', 'stale', 'near-expiry'):
                    self.assertEqual(code, 0, body)
                    self.assertEqual(self.metrics()[1]['dispatch'], gate == 'fresh')
                    self.assertIn('metrics', self.state()['slot_intent'])
                else:
                    self.assertNotEqual(code, 0, body)
                    self.assertEqual(self.path.read_bytes(), before)
                    self.assertFalse(self.metrics()[1].get('dispatch', False))
                self.assertEqual(self.state()['proof'], proof)

    def test_resolution_cannot_reopen_fully_exhausted_attempt(self):
        self.metrics_ready()
        self.assertTrue(self.metrics()[1]['dispatch'])
        self.assertEqual(self.outcome('metrics')[0], 0)
        blocker = self.block()
        before = self.path.read_bytes()
        self.assertNotEqual(self.resolve(blocker)[0], 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_blocker_records_are_bounded_for_entire_attempt(self):
        self.ready()
        for index in range(8):
            blocker = self.block()
            self.assertEqual(blocker, 'blocker-' + str(index + 1))
            self.assertEqual(self.resolve(blocker)[0], 0)
        before = self.path.read_bytes()
        self.assertNotEqual(self.call('record-blocker', kind='recoverable')[0], 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_legacy_blockers_allow_cleanup_without_inventing_recovery_evidence(self):
        self.ready()
        state = self.state()
        state['blockers'] = [dict(kind='recoverable', nonterminal=True)]
        workflow.atomic_dump(self.path, state)
        before = self.path.read_bytes()
        self.assertNotEqual(self.resolve('blocker-1')[0], 0)
        self.assertEqual(self.path.read_bytes(), before)
        self.no_dispatch('admit', slot='version', intent_id='v', decision='admit')
        self.now += 20000
        self.good('record-residual', uid='partial', owner_class='own', active='true',
                  expires_at=self.now + 300, cnp='present', receipt='partial')
        self.assertEqual(self.state()['blockers'], state['blockers'])

    def test_bad_ledger_structures_fail_closed_without_writes(self):
        self.metrics_ready()
        self.audit_fixture()
        good = self.state()
        invalid = [None, [], {'schema': 1, 'revision': 1}]
        for key in good:
            missing = copy.deepcopy(good)
            del missing[key]
            invalid.append(missing)
        for key, value in [('binding', []), ('owner', {}), ('cleanup', {}),
                           ('proof', []), ('intents', []), ('slot_intent', []),
                           ('blockers', [None]), ('candidates', [None]), ('revision', True)]:
            invalid.append({**good, key: value})
        for group, key in [('owner', 'lost'), ('cleanup', 'grants'), ('contract', 'fsid')]:
            item = copy.deepcopy(good)
            del item[group][key]
            invalid.append(item)
        for key, value in [('uid', []), ('cnp_uid', {}), ('expires_at', True)]:
            item = copy.deepcopy(good)
            item['cleanup']['grants'][0][key] = value
            invalid.append(item)
        for data in invalid:
            with self.subTest(data=str(data)[:100]):
                self.path.write_text(json.dumps(data))
                self.assert_structured_rejection('show')
                self.assert_structured_rejection('record-blocker', kind='recoverable')

    def test_bad_audit_structures_fail_closed_without_writes(self):
        self.ready()
        audit = self.audit_fixture()
        path = Path(self.tmp.name) / 'audit.json'
        invalid = [None, [], {}]
        for key in audit:
            item = copy.deepcopy(audit)
            del item[key]
            invalid.append(item)
        for row_key in audit['grants'][0]:
            item = copy.deepcopy(audit)
            del item['grants'][0][row_key]
            invalid.append(item)
        for key, value in [('uid', []), ('uid', {}), ('cnp_uid', []), ('cnp_uid', {}),
                           ('expires_at', True), ('active', 0), ('cnp_present', 0)]:
            item = copy.deepcopy(audit)
            item['grants'][0][key] = value
            invalid.append(item)
        invalid += [{**audit, 'grants': [None]}, {**audit, 'cnps': [None]},
                    {**audit, 'cnps': [{'uid': [], 'present': False}]}]
        for data in invalid:
            with self.subTest(data=str(data)[:100]):
                path.write_text(json.dumps(data))
                self.assert_structured_rejection('evaluate-clean', audit_file=path,
                                                 expect_revision=self.state()['revision'])

    def test_invalid_json_truncation_and_nonfinite_inputs_preserve_bytes(self):
        self.ready()
        audit = Path(self.tmp.name) / 'audit.json'
        valid = self.path.read_bytes()
        for payload in (b'', b'{', b'{"schema":1,"revision":', b'null', b'\xff',
                        b'{"schema":1,"revision":NaN}', b'[' * 1500):
            with self.subTest(payload=payload[:30]):
                self.path.write_bytes(payload)
                self.assert_structured_rejection('show')
                self.assert_structured_rejection('record-blocker', kind='recoverable')
                self.path.write_bytes(valid)
                audit.write_bytes(payload)
                self.assert_structured_rejection('evaluate-clean', audit_file=audit,
                                                 expect_revision=self.state()['revision'])

    def test_ledger_and_audit_reject_unsafe_files_promptly(self):
        self.ready()
        valid_audit = self.audit_fixture()
        target = Path(self.tmp.name) / 'target.json'
        original_ledger = self.path.read_bytes()
        for surface in ('ledger', 'audit'):
            for kind in ('symlink', 'fifo', 'oversize'):
                with self.subTest(surface=surface, kind=kind):
                    target.write_bytes(original_ledger if surface == 'ledger' else
                                       json.dumps(valid_audit).encode())
                    path = Path(self.tmp.name) / (surface + '-' + kind)
                    if kind == 'symlink':
                        path.symlink_to(target)
                    elif kind == 'fifo':
                        os.mkfifo(path)
                    else:
                        path.write_bytes(b' ' * (workflow.MAX_LEDGER_BYTES + 1))
                    command = 'show' if surface == 'ledger' else 'evaluate-clean'
                    ledger = path if surface == 'ledger' else self.path
                    argv = [sys.executable, '-I', '-B', str(SOURCE), '--ledger', str(ledger), command]
                    flags = {**BINDING, 'now': self.now, 'expect_revision': self.state()['revision']}
                    if surface == 'audit':
                        flags['audit_file'] = path
                    for key, value in flags.items():
                        argv += ['--' + key.replace('_', '-'), str(value)]
                    before = self.path.read_bytes()
                    target_before = target.read_bytes()
                    # One bounded child at a time; timeout kills the old audit FIFO open.
                    result = subprocess.run(argv, capture_output=True, text=True, timeout=2)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    body = json.loads(result.stdout)
                    self.assertIs(body['ok'], False)
                    self.assertIs(body['dispatch'], False)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertEqual(self.path.read_bytes(), before)
                    self.assertEqual(target.read_bytes(), target_before)

    def test_a_discovery_without_preselection_and_slow_ci(self):
        self.ready()
        self.assertIsNone(self.state()['selected_head'])
        self.discover()
        code, body = self.select()
        self.assertEqual(code, 0, body)
        self.assertEqual(self.state()['selected_head'], HEAD)
        self.assertNotIn('author_budget_s', self.state())
        self.assertEqual(self.state()['series'], 'UNKNOWN')
        self.assertEqual(self.state()['capacity_gate'], 'external')
        self.assertFalse(self.state()['owner']['operations_owner_accepted'])

    def test_a_no_proof_until_successful_response(self):
        self.ready()
        self.admit('version')
        self.assertEqual(self.state()['proof'], {})
        self.no_dispatch('admit', slot='permissions', intent_id='early', decision='admit')
        self.assertNotIn('permissions', self.state()['slot_intent'])
        self.assertEqual(self.outcome('version')[0], 0)
        self.assertEqual(self.state()['proof'], {})
        self.admit('permissions')
        self.assertEqual(self.outcome('permissions')[0], 0)
        self.admit('ceph-status')
        self.assertEqual(self.state()['proof'], {})
        self.now += 2
        self.assertEqual(self.outcome('ceph-status')[0], 0)
        proof = self.state()['proof']['discovery']
        self.assertEqual(proof['observed_at'], self.now)
        self.assertEqual(proof['binding'], BINDING)

    def test_a_selection_only_after_four_successes(self):
        self.ready()
        self.assertNotEqual(self.select()[0], 0)
        self.admit('version')
        self.assertEqual(self.outcome('version', 'unknown')[0], 0)
        code, _ = self.no_dispatch('admit', slot='permissions', intent_id='next', decision='admit')
        self.assertNotEqual(code, 0)
        self.assertNotEqual(self.select()[0], 0)

    def test_full_budget_and_squash_merge_head_binding(self):
        self.metrics_ready()
        state = self.state()
        self.assertEqual(state['merge']['pr_head'], HEAD)
        self.assertEqual(state['merge']['merged'], MERGED)
        self.assertNotEqual(HEAD, MERGED)
        code, body = self.metrics()
        self.assertEqual(code, 0, body)
        self.assertTrue(body['dispatch'])
        self.assertEqual(body['transport_deadline'], self.now + 300)
        self.assertEqual(body['read_budget_s'], 10)
        self.assertEqual(body['proof_deadline'], self.now + 300)
        self.assertEqual(body['counts']['discovery'], 4)
        self.assertEqual(body['counts']['revalidation'], 1)
        self.assertEqual(body['counts']['metrics'], 1)
        self.assertEqual(self.outcome('metrics')[0], 0)
        self.assertFalse(self.metrics()[1]['dispatch'])
        self.assertFalse(self.admit('revalidation')['dispatch'])

    def test_existing_route_no_candidate_no_merge_still_waits(self):
        self.ready(existing=True)
        self.discover()
        self.assertEqual(self.select(existing=True)[0], 0)
        self.assertEqual(self.state()['candidates'], [])
        self.assertIsNone(self.state()['selected_head'])
        self.assertEqual(self.state()['merge'], {})
        self.no_dispatch('admit', slot='revalidation', intent_id='rv', decision='admit')
        self.good('observe-delivery', deployment='ready', grant='pending', cnp='ready')
        self.no_dispatch('admit', slot='revalidation', intent_id='rv', decision='admit')
        self.good('observe-delivery', deployment='ready', grant='ready', cnp='pending')
        self.no_dispatch('admit', slot='revalidation', intent_id='rv', decision='admit')
        self.good('observe-delivery', deployment='ready', grant='ready', cnp='ready')
        self.assertFalse(self.metrics()[1].get('dispatch', False))
        self.admit('revalidation')
        self.assertFalse(self.metrics()[1].get('dispatch', False))
        self.assertEqual(self.outcome('revalidation')[0], 0)
        self.assertTrue(self.metrics()[1]['dispatch'])

    def test_pending_denied_unknown_uncertain_and_failed_do_not_progress_on_reload(self):
        for outcome in (None, 'denied', 'unknown', 'uncertain', 'failure'):
            with self.subTest(outcome=outcome):
                self.path = Path(self.tmp.name) / ('ledger-' + str(outcome))
                self.ready()
                self.admit('version')
                if outcome:
                    self.assertEqual(self.outcome('version', outcome)[0], 0)
                # Real on-disk reload on each invocation; no in-memory continuation.
                self.assertFalse(self.admit('version')['dispatch'])
                code, _ = self.no_dispatch('admit', slot='permissions', intent_id='next', decision='admit')
                self.assertNotEqual(code, 0)
                self.assertEqual(len(self.state()['intents']), 1)

    def test_admission_decisions_and_conflicting_replay(self):
        self.ready()
        body = self.good('admit', slot='version', intent_id='one', decision='pending')
        self.assertFalse(body['dispatch'])
        self.assertNotEqual(self.call('admit', slot='version', intent_id='one', decision='admit')[0], 0)
        self.assertNotEqual(self.call('admit', slot='permissions', intent_id='one', decision='pending')[0], 0)
        self.assertNotEqual(self.outcome('version')[0], 0)
        self.assertNotEqual(self.call('admit', slot='permissions', intent_id='two', decision='admit')[0], 0)

    def test_strict_outcome_and_immutable_receipt(self):
        self.ready()
        self.admit('version')
        self.assertNotEqual(self.outcome('version', 'observed')[0], 0)
        self.assertNotEqual(self.outcome('version', 'anything')[0], 0)
        self.assertEqual(self.outcome('version')[0], 0)
        self.assertNotEqual(self.outcome('version', receipt='changed')[0], 0)
        self.assertEqual(self.state()['intents']['intent-version']['receipt'], 'receipt-version')

    def test_metrics_fail_closed_on_time_or_identity_drift(self):
        cases = [('stale', 301, {}), ('eight-left', 292, {}), ('future', -1, {}),
                 ('wrong-fsid', 0, {'observed_fsid': 'wrong'}),
                 ('manager', 0, {'observed_manager': 'changed'}),
                 ('endpoint', 0, {'observed_endpoint': OTHER_ENDPOINT}),
                 ('unknown', 0, {'observed_endpoint': '203.0.113.99:9283/metrics'}),
                 ('receipt', 0, {'proof_receipt': 'wrong'})]
        for name, delta, flags in cases:
            with self.subTest(name=name):
                self.path = Path(self.tmp.name) / name
                self.now = BASE
                self.metrics_ready()
                self.now += delta
                code, body = self.metrics(**flags)
                self.assertFalse(body.get('dispatch', False), (code, body))
                if name != 'future':  # rollback rejects before consuming.
                    self.assertEqual(self.state()['slot_intent']['metrics'], 'intent-metrics')
                    self.assertEqual(self.state()['intents']['intent-metrics']['decision'], 'denied')
                    self.assertFalse(self.metrics()[1].get('dispatch', False))
                    self.no_dispatch('admit', slot='metrics', intent_id='corrected-retry', decision='admit',
                                     observed_fsid=FSID, observed_manager=MANAGER,
                                     observed_endpoint=ENDPOINT, proof_receipt='receipt-revalidation')
                    self.assertEqual(self.state()['slot_intent']['metrics'], 'intent-metrics')

    def test_request_interval_not_response_time_controls_full_ten_seconds(self):
        self.ready()
        self.discover()
        self.deliver()
        self.admit('revalidation')
        request = self.now
        self.now += 292
        self.assertEqual(self.outcome('revalidation', request_at=request)[0], 0)
        self.assertEqual(self.state()['proof']['revalidation']['request_at'], request)
        self.assertFalse(self.metrics()[1].get('dispatch', False))

    def test_stored_revalidation_proof_cannot_certify_wrong_cluster_or_manager(self):
        for key, value in [('observed_fsid', 'wrong'), ('observed_manager', 'changed'),
                           ('observed_endpoint', OTHER_ENDPOINT),
                           ('observed_endpoint', '203.0.113.99:9283/metrics')]:
            with self.subTest(key=key, value=value):
                self.path = Path(self.tmp.name) / (key + '-' + value.replace('/', '_'))
                self.now = BASE
                self.ready()
                self.discover()
                self.deliver()
                self.admit('revalidation')
                self.assertEqual(self.outcome('revalidation', **{key: value})[0], 0)
                self.assertFalse(self.metrics(**{key: value})[1].get('dispatch', False))

    def test_order_consumed_slot_and_exact_freshness_boundary(self):
        self.ready()
        self.no_dispatch('admit', slot='ceph-status', intent_id='early', decision='admit')
        self.assertEqual(self.state()['intents'], {})
        self.discover()
        self.deliver()
        self.admit('revalidation')
        self.assertEqual(self.outcome('revalidation')[0], 0)
        self.assertNotEqual(self.call('admit', slot='revalidation', intent_id='different', decision='admit')[0], 0)
        self.now += 290
        self.assertTrue(self.metrics()[1]['dispatch'])  # exactly ten seconds left.

    def test_future_receipt_rollback_and_immutable_origin(self):
        self.ready()
        self.discover()
        self.deliver()
        self.admit('revalidation')
        self.assertNotEqual(self.outcome('revalidation', observed_at=self.now + 1)[0], 0)
        self.assertNotIn('revalidation', self.state()['proof'])
        self.assertEqual(self.outcome('revalidation')[0], 0)
        proof = self.state()['proof']['revalidation']
        self.now += 10
        self.good('record-reversal', head='d' * 40)
        self.assertFalse(self.state()['reversal']['precertified'])
        self.assertEqual(self.state()['reversal']['removal_ci'], 'external-later')
        self.assertEqual(self.state()['proof']['revalidation'], proof)
        self.assertFalse(self.metrics(now=self.now - 1)[1].get('dispatch', False))
        self.assertEqual(self.state()['proof']['revalidation'], proof)

    def test_clock_rollback_via_exact_replay_cannot_admit_metrics(self):
        self.metrics_ready()
        self.now += 20
        self.assertFalse(self.admit('version')['dispatch'])
        self.assertFalse(self.metrics(now=self.now - 1)[1].get('dispatch', False))

    def test_selection_proof_freshness_and_exact_match(self):
        self.ready()
        self.discover()
        for flags in ({'verified_fsid': 'wrong'}, {'verified_manager': 'changed'},
                      {'verified_endpoint': OTHER_ENDPOINT}, {'now': self.now + 301},
                      {'proof_receipt': 'wrong'}):
            self.assertNotEqual(self.select(**flags)[0], 0)
        self.assertEqual(self.select()[0], 0)
        self.assertNotEqual(self.select()[0], 0)  # no second activation/selection.
        self.assertNotEqual(self.call('record-merge', pr_head='d' * 40, merged_sha=MERGED,
                                     flux_source=MERGED, flux_applied=MERGED)[0], 0)
        self.assertNotEqual(self.call('record-merge', pr_head=HEAD, merged_sha=MERGED,
                                     flux_source=HEAD, flux_applied=MERGED)[0], 0)

    def test_caller_identity_scope_attempt_required_for_every_mutation(self):
        self.ready()
        for key in BINDING:
            code, body = self.no_dispatch('admit', slot='version', intent_id='v', decision='admit',
                                          **{key: 'wrong'})
            self.assertNotEqual(code, 0, body)
            self.assertNotEqual(self.call('record-blocker', kind='turn-end', **{key: 'wrong'})[0], 0)
            self.assertNotEqual(self.call('reserve-cleanup', seconds=180, deadline=BASE + 9999,
                                         **{key: 'wrong'})[0], 0)
        self.assertEqual(self.state()['intents'], {})

    def test_actual_head_ci_and_candidate_count(self):
        self.initialize()
        self.reserve()
        self.now += 2500
        bad = [{'reviewed_head': 'd' * 40}, {'ci_head': 'e' * 40}, {'private_review': True}]
        for check in ('flux_local', 'diff_scope'):
            bad.extend({check: status} for status in ('pending', 'failure', 'missing', 'skipped'))
        # No successful preparation means no discovery lease/read advice.
        for flags in bad:
            base = dict(head=HEAD, reviewed_head=HEAD, ci_head=HEAD, pr='pr-synthetic',
                        manager=MANAGER, endpoint=ENDPOINT, flux_local='success', diff_scope='success',
                        ci_started_at=BASE, ci_finished_at=self.now)
            self.assertNotEqual(self.call('prepare-candidate', **{**base, **flags})[0], 0)
        self.no_dispatch('admit', slot='version', intent_id='v', decision='admit')
        for index in range(3):
            self.assertEqual(self.prepare(head=str(index) * 40, manager='mgr-' + str(index))[0], 0)
        self.assertNotEqual(self.prepare(head='f' * 40)[0], 0)
        self.admit('version')
        self.assertNotEqual(self.prepare(head='f' * 40)[0], 0)

    def test_multiple_prepared_alternatives_select_only_discovered_manager(self):
        self.ready()
        self.assertEqual(self.prepare(head='d' * 40, manager='other-manager', endpoint=OTHER_ENDPOINT)[0], 0)
        self.discover()
        self.assertEqual(self.select()[0], 0)
        self.assertEqual(self.state()['selected_head'], HEAD)
        self.assertEqual(self.state()['selected_manager'], MANAGER)
        self.assertEqual(self.state()['selected_endpoint'], ENDPOINT)

    def test_ambiguous_candidates_cannot_activate(self):
        self.ready()
        self.assertEqual(self.prepare(head='d' * 40)[0], 0)
        self.discover()
        self.assertNotEqual(self.select()[0], 0)
        self.assertIsNone(self.state()['selected_head'])

    def test_missing_ready_or_live_observation_cannot_initialize(self):
        for ready, live in ((True, False), (False, True)):
            code, _ = self.call('init', owner_kind='persistent', owner_liveness='observed',
                                owner_ready=ready, owner_live=live, fsid=FSID,
                                endpoint=[ENDPOINT], planned_revalidation=True)
            self.assertNotEqual(code, 0)

    def test_operation_owner_must_be_observed_persistent(self):
        for kind, liveness in [('task', 'observed'), ('persistent', 'cap-only'), ('persistent', 'unobserved')]:
            code, body = self.call('init', owner_kind=kind, owner_liveness=liveness,
                                   owner_ready=True, owner_live=True, owner_cap_s=14400,
                                   fsid=FSID, endpoint=[ENDPOINT], planned_revalidation=True)
            self.assertNotEqual(code, 0, body)
        self.assertFalse(self.path.exists())

    def test_explicit_revalidation_plan_required(self):
        code, _ = self.call('init', owner_kind='persistent', owner_liveness='observed',
                             owner_ready=True, owner_live=True, fsid=FSID, endpoint=[ENDPOINT])
        self.assertNotEqual(code, 0)

    def test_reserve_is_remaining_time_gate_cleanup_remains_writable(self):
        self.ready()
        self.good('reserve-cleanup', seconds=180, deadline=self.now + 185, ci_estimate_s=900)
        self.no_dispatch('admit', slot='version', intent_id='v', decision='admit')
        self.now += 20000
        for kind in ('recoverable', 'turn-end', 'lost-owner'):
            self.good('record-blocker', kind=kind)
        self.good('record-residual', uid='own-grant', owner_class='own', active='true',
                  expires_at=self.now + 300, cnp='present', receipt='residual-1')
        self.assertEqual(self.state()['cleanup']['grants'][0]['uid'], 'own-grant')
        self.assertFalse(self.state()['operation_complete'])
        self.assertFalse(self.state()['successor_authorized'])
        self.assertNotEqual(self.call('close-operation')[0], 0)
        self.assertNotEqual(self.call('authorize-successor')[0], 0)
        self.assertNotEqual(self.call('publish')[0], 0)

    def test_truthful_cleanup_requires_absence_full_audit_and_natural_expiry(self):
        self.ready()
        self.admit('version')  # partial attempt, still no automatic completion.
        self.good('record-blocker', kind='lost-owner')
        for owner in ('own', 'other'):
            self.good('record-residual', uid=owner, owner_class=owner, active='true',
                      expires_at=self.now + 50, cnp='present', receipt='residual-' + owner)
        self.now += 20000
        self.assertNotEqual(self.call('record-residual', uid='other', owner_class='other',
                                     active='false', expires_at=self.now - 1, cnp='absent',
                                     reason='released', receipt='bad')[0], 0)
        for owner in ('own', 'other'):
            self.good('record-residual', uid=owner, owner_class=owner, active='false',
                      expires_at=BASE + 2550, cnp='absent', reason='natural-expiry',
                      receipt='expired-' + owner)
        def audit_file(policy_present=False, extra_present_cnp=False, complete=True, uids=('own', 'other')):
            audit = dict(binding=BINDING, receipt='audit', observed_at=self.now,
                         complete=complete, policy=dict(ref='policy-synthetic', present=policy_present),
                         grants=[dict(uid=uid, owner_class=uid, expires_at=BASE + 2550,
                                      active=False, cnp_uid='cnp-' + uid, cnp_present=False) for uid in uids],
                         cnps=[dict(uid='cnp-' + uid, present=False) for uid in uids])
            if extra_present_cnp:
                audit['cnps'].append(dict(uid='extra-cnp', present=True))
            path = Path(self.tmp.name) / 'audit.json'
            path.write_text(json.dumps(audit))
            return path

        for flags in ({'policy_present': True}, {'extra_present_cnp': True}, {'complete': False}):
            body = self.good('evaluate-clean', audit_file=audit_file(**flags))
            self.assertFalse(body['evaluation']['clean'])
        self.assertNotEqual(self.call('evaluate-clean', audit_file=audit_file(uids=('own',)))[0], 0)
        body = self.good('evaluate-clean', audit_file=audit_file())
        self.assertTrue(body['evaluation']['clean'])
        self.assertFalse(body['operation_complete'])
        self.assertFalse(body['evaluation']['other_owners_released'])
        audit = json.loads(audit_file().read_text())
        audit['binding'] = {**BINDING, 'attempt': 'wrong'}
        bad = Path(self.tmp.name) / 'bad-audit.json'
        bad.write_text(json.dumps(audit))
        self.assertNotEqual(self.call('evaluate-clean', audit_file=bad)[0], 0)
        # Cleanup record invalidates a prior clean evaluation.
        self.good('record-residual', uid='own', owner_class='own', active='true',
                  expires_at=BASE + 2550, cnp='present', receipt='new-observation')
        self.assertIsNone(self.state()['cleanup']['evaluation'])

    def test_two_process_same_revision_admits_at_most_once(self):
        self.ready()
        ctx = multiprocessing.get_context('fork')
        holding, contender_done = ctx.Event(), ctx.Event()
        pipes = [ctx.Pipe(duplex=False) for _ in range(2)]
        workers = [ctx.Process(target=competing_writer,
                              args=(self.path, self.state()['revision'], holding, contender_done, pipes[i][1], i == 0))
                   for i in range(2)]
        try:
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=8)
                self.assertFalse(worker.is_alive())
                self.assertEqual(worker.exitcode, 0)
            results = [pipe[0].recv() for pipe in pipes]
            self.assertEqual(sum(bool(body.get('dispatch')) for _, body in results), 1, results)
            self.assertEqual(sorted(code for code, _ in results), [0, 2])
            self.assertEqual(len(self.state()['intents']), 1)
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=2)
            for pair in pipes:
                for pipe in pair:
                    pipe.close()

    def test_fault_after_replace_cannot_reissue(self):
        self.ready()
        real_replace = os.replace

        def replace_then_fault(*args, **kwargs):
            real_replace(*args, **kwargs)
            raise OSError('synthetic crash after replace before dispatch advice')

        with mock.patch.object(workflow.os, 'replace', side_effect=replace_then_fault):
            code, body = self.call('admit', slot='version', intent_id='v', decision='admit')
        self.assertNotEqual(code, 0, body)
        self.assertFalse(self.good('admit', slot='version', intent_id='v', decision='admit')['dispatch'])
        self.assertEqual(len(self.state()['intents']), 1)

    def test_init_exclusive_and_short_payload_writes(self):
        self.initialize()
        before = self.path.read_bytes()
        self.assertNotEqual(self.initialize_again()[0], 0)
        self.assertEqual(self.path.read_bytes(), before)
        real_write = os.write
        with mock.patch.object(workflow.os, 'write', side_effect=lambda fd, data: real_write(fd, data[:7])):
            self.reserve()
        self.assertEqual(self.state()['cleanup']['reserved_s'], 180)

    def initialize_again(self):
        return self.call('init', owner_kind='persistent', owner_liveness='observed',
                         owner_ready=True, owner_live=True, fsid=FSID,
                         endpoint=[ENDPOINT], planned_revalidation=True)

    def test_endpoint_canonical_and_isolated_import(self):
        self.assertEqual(Path(workflow.__file__).resolve(), SOURCE)
        for endpoint in ('203.0.113.010:9283/metrics', '+203.0.113.10:9283/metrics',
                         '203.0.113.10:09283/metrics', '203.0.113.10:9283/other',
                         '203.0.113.10:9283/metrics?q=1', '*.example:9283/metrics',
                         '203.0.113.10:80/metrics', '203.0.113.10:9283/metrics/'):
            with self.assertRaises(workflow.Reject, msg=endpoint):
                workflow.parse_endpoint(endpoint)


if __name__ == '__main__':
    unittest.main()
