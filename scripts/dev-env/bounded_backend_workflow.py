"""OFFLINE local intent ledger. No transport, authentication, approval or live adoption.

A qualified external adapter must verify caller/liveness, receipts, body semantics,
current reviewed PR heads/CI, grants and audit completeness. Stored claims only
constrain this local sequence; they never establish source or access authority.
"""
import argparse
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

DISCOVERY_SLOTS = ('version', 'permissions', 'ceph-status', 'ceph-pool')
ALL_SLOTS = DISCOVERY_SLOTS + ('revalidation', 'metrics')
PROOF_GUARD_S = 300
METRICS_BUDGET_S = 10
PVE_BUDGET_S = 12
MAX_CANDIDATES = 3
MAX_LEDGER_BYTES = 65536
REQUIRED_CHECKS = ('Flux Local - Success', 'Diff Scope - Success')
OUTCOMES = ('success', 'denied', 'unknown', 'uncertain', 'failure')
TOKEN_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,80}$')


class Conflict(Exception):
    pass


class Gate(Exception):
    pass


class Reject(Exception):
    pass


def _token(value, label):
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise Reject('invalid ' + label)
    return value


def _sha(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}', value):
        raise Reject('invalid full git SHA: ' + label)
    return value


def parse_endpoint(text):
    if not isinstance(text, str) or not text.endswith(':9283/metrics'):
        raise Reject('endpoint must be canonical IPv4:9283/metrics (TCP)')
    host = text[:-len(':9283/metrics')]
    try:
        if str(ipaddress.IPv4Address(host)) != host:
            raise ValueError('noncanonical')
    except ValueError as exc:
        raise Reject('endpoint must be canonical IPv4:9283/metrics (TCP)') from exc
    return text


def read_json_file(path, label):
    """Bound both local inputs before parsing; never follow a final symlink or FIFO."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise Reject(label + ' must be a regular file')
        if info.st_size > MAX_LEDGER_BYTES:
            raise Reject(label + ' exceeds bounded size')
        payload = handle.read(MAX_LEDGER_BYTES + 1)
    if len(payload) > MAX_LEDGER_BYTES:
        raise Reject(label + ' exceeds bounded size')

    def invalid_constant(value):
        raise Reject('nonfinite JSON value')

    return json.loads(payload, parse_constant=invalid_constant), payload


def fields(record, specification, label):
    """Validate required local record shapes before indexing or hashing values."""
    if not isinstance(record, dict):
        raise Reject('invalid ' + label + ' record')
    for key, types in specification.items():
        types = types if isinstance(types, tuple) else (types,)
        if key not in record or type(record[key]) not in types:
            raise Reject('invalid or missing ' + label + '.' + key)


def binding_fields(record):
    fields(record, dict(identity=str, scope=str, attempt=str), 'binding')
    for key in ('identity', 'scope', 'attempt'):
        _token(record[key], key)


def proof_fields(record):
    fields(record, dict(binding=dict, request_at=int, observed_at=int, receipt=str,
                        body_sha256=str, fsid=str, manager=str, endpoint=str), 'proof')
    binding_fields(record['binding'])
    for key in ('receipt', 'fsid', 'manager'):
        _token(record[key], key)
    parse_endpoint(record['endpoint'])


def load_ledger(path):
    state, _ = read_json_file(path, 'ledger')
    nullable = (str, type(None))
    fields(state, dict(schema=int, revision=int, last_now=int, binding=dict, owner=dict,
           contract=dict, cleanup=dict, candidates=list, selected_head=nullable,
           selected_manager=nullable, selected_endpoint=nullable, branch=nullable,
           merge=dict, delivery=dict, reversal=dict, proof=dict, intents=dict,
           slot_intent=dict, blockers=list, operation_complete=bool,
           successor_authorized=bool, capacity_gate=str, admin_gate=str, series=str,
           quotas=str, prior_pve_bundle=str), 'ledger')
    if state['schema'] != 1 or state['revision'] < 1 or state['last_now'] < 0:
        raise Reject('unknown ledger format; no implicit migration')
    binding_fields(state['binding'])
    fields(state['owner'], dict(kind=str, ready=bool, live=bool, lost=bool,
                               operations_owner_accepted=bool), 'owner')
    contract = state['contract']
    fields(contract, dict(fsid=str, endpoints=list, covered_endpoints=list,
                          planned_revalidation=bool), 'contract')
    _token(contract['fsid'], 'FSID')
    if not 1 <= len(contract['endpoints']) <= 4 or len(contract['covered_endpoints']) > 4:
        raise Reject('invalid endpoint inventory')
    for endpoint in contract['endpoints'] + contract['covered_endpoints']:
        parse_endpoint(endpoint)
    cleanup = state['cleanup']
    fields(cleanup, dict(reserved_s=int, deadline=(int, type(None)),
                        ci_estimate_s=(int, type(None)), grants=list,
                        evaluation=(dict, type(None))), 'cleanup')
    if len(cleanup['grants']) > 16:
        raise Reject('bounded residual list full')
    for row in cleanup['grants']:
        fields(row, dict(uid=str, owner_class=str, expires_at=int, active=bool,
                         cnp=str, cnp_uid=str, reason=str, receipt=str, observed_at=int), 'residual')
        for key in ('uid', 'cnp_uid', 'receipt'):
            _token(row[key], key)
    if len(state['candidates']) > MAX_CANDIDATES or len(state['blockers']) > 8:
        raise Reject('bounded candidate or blocker list exceeded')
    for row in state['candidates']:
        fields(row, dict(head=str, reviewed_head=str, ci_head=str, pr=str, manager=str,
                         endpoint=str, checks=dict, ci_started_at=int, ci_finished_at=int), 'candidate')
        fields(row['checks'], dict.fromkeys(REQUIRED_CHECKS, str), 'candidate checks')
    for row in state['blockers']:
        fields(row, dict(kind=str, nonterminal=bool), 'blocker')
        # Earlier offline records have no recovery metadata. Keep cleanup writable,
        # but never invent their event time or silently qualify them for resolution.
        if any(key in row for key in ('id', 'recorded_at', 'resolution')):
            fields(row, dict(id=str, recorded_at=int, resolution=(dict, type(None))), 'blocker')
            _token(row['id'], 'blocker ID')
        if row['kind'] not in ('recoverable', 'turn-end', 'lost-owner'):
            raise Reject('invalid blocker kind')
        if row.get('resolution') is not None:
            fields(row['resolution'], dict(reason=str, prerequisite_ref=str, verified_at=int,
                                           resolved_at=int, binding=dict), 'resolution')
            binding_fields(row['resolution']['binding'])
            if row['kind'] != 'recoverable' or row['resolution']['binding'] != state['binding']:
                raise Reject('invalid blocker resolution')
    if state['merge']:
        fields(state['merge'], dict(pr_head=str, merged=str, flux_source=str, flux_applied=str), 'merge')
    if state['delivery']:
        fields(state['delivery'], dict(deployment=str, grant=str, cnp=str), 'delivery')
    if state['reversal']:
        fields(state['reversal'], dict(head=str, precertified=bool, removal_ci=str), 'reversal')
    if not set(state['proof']) <= {'discovery', 'revalidation'}:
        raise Reject('unknown proof key')
    for proof in state['proof'].values():
        proof_fields(proof)
    if (not set(state['slot_intent']) <= set(ALL_SLOTS)
            or len(state['intents']) > len(ALL_SLOTS)):
        raise Reject('invalid intent inventory')
    for key, row in state['intents'].items():
        _token(key, 'intent ID')
        fields(row, dict(slot=str, fingerprint=dict, admitted_at=int, dispatch=int,
                         decision=str, reason=(str, type(None)), outcome=(str, type(None))), 'intent')
        fields(row['fingerprint'], dict(slot=str, decision=str, observed_fsid=str,
               observed_manager=str, observed_endpoint=str, proof_receipt=str), 'fingerprint')
        if row['slot'] not in ALL_SLOTS or row['dispatch'] not in (0, 1):
            raise Reject('invalid intent slot or dispatch')
        if row['outcome'] is not None:
            if row['outcome'] not in OUTCOMES:
                raise Reject('invalid outcome')
            fields(row, dict(result=dict, receipt=str), 'completed intent')
            fields(row['result'], dict(outcome=str, request_at=(int, type(None)),
                   observed_at=(int, type(None)), receipt=str, body_sha256=(str, type(None)),
                   fsid=str, manager=str, endpoint=str), 'outcome')
        if state['slot_intent'].get(row['slot']) != key:
            raise Reject('intent missing exact slot binding')
    for slot, intent_id in state['slot_intent'].items():
        _token(intent_id, 'slot intent ID')
        if intent_id not in state['intents'] or state['intents'][intent_id]['slot'] != slot:
            raise Reject('slot missing exact intent binding')
    return state


@contextmanager
def ledger_lock(path):
    """Nonblocking exclusive lock: a contender must reload, never queue a read."""
    path = Path(path)
    fd = os.open(str(path) + '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Reject('lock must be a regular file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Conflict('ledger busy; reload revision, never retry a read') from exc
        yield
    finally:
        os.close(fd)


def atomic_dump(path, state):
    payload = json.dumps(state, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(payload) > MAX_LEDGER_BYTES:
        raise Reject('ledger exceeds bounded size')
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise OSError('short ledger write')
            remaining = remaining[written:]
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if fd is not None:
            os.close(fd)
        if os.path.lexists(tmp):
            os.unlink(tmp)


def caller(args):
    return {key: _token(getattr(args, key), key) for key in ('identity', 'scope', 'attempt')}


def check_binding(state, args):
    if caller(args) != state['binding']:
        raise Reject('caller identity/scope/attempt does not match stored references')


def cas(path, expected, mutate, args):
    with ledger_lock(path):
        state = load_ledger(path)
        check_binding(state, args)
        if state['revision'] != expected:
            raise Conflict('stale revision')
        draft = copy.deepcopy(state)
        result = mutate(draft) or {}
        for key, proof in state['proof'].items():
            if draft['proof'].get(key) != proof:
                raise Reject('proof origin is immutable')
        draft['revision'] += 1
        atomic_dump(path, draft)
        return result_from(draft, **result)


def counts(state):
    used = state['slot_intent']
    return {'discovery': sum(slot in used for slot in DISCOVERY_SLOTS),
            'discovery_budget': 4, 'revalidation': int('revalidation' in used),
            'revalidation_budget': 1, 'metrics': int('metrics' in used), 'metrics_budget': 1}


def result_from(state, **extra):
    return {'ok': True, 'revision': state['revision'], 'dispatch': False,
            'operation_complete': False, 'successor_authorized': False,
            'counts': counts(state), **extra}


def advance_clock(state, now):
    if now < state['last_now']:
        raise Gate('clock rollback')
    state['last_now'] = now


def require_owner_observation(state):
    owner = state['owner']
    if owner['kind'] != 'persistent' or not owner['ready'] or not owner['live'] or owner['lost']:
        raise Gate('observed ready/live persistent owner required')


def require_owner(state):
    require_owner_observation(state)
    if any(row.get('resolution') is None for row in state['blockers']):
        raise Gate('blocker is nonterminal; cleanup remains recordable')


def require_reserve(state, now, stage_s=1):
    cleanup = state['cleanup']
    if cleanup['deadline'] is None or now + cleanup['reserved_s'] + stage_s > cleanup['deadline']:
        raise Gate('insufficient time before explicit caller deadline including cleanup reserve')


def normal_stage(state, args, stage_s=1):
    require_owner(state)
    advance_clock(state, args.now)
    require_reserve(state, args.now, max(stage_s, args.stage_budget_s))
    state['cleanup']['evaluation'] = None


def successful(state, slots):
    for slot in slots:
        intent = state['slot_intent'].get(slot)
        if not intent or state['intents'][intent]['outcome'] != 'success':
            raise Gate('successful outcome required for ' + slot)


def classify_proof(proof, now, budget=0):
    start, end = proof['request_at'], proof['observed_at']
    if start > end or end > now:
        raise Gate('future or invalid proof interval')
    deadline = min(start, end) + PROOF_GUARD_S
    if now + budget > deadline:
        raise Gate('proof stale or insufficient full read budget')
    return deadline


def match_proof(state, proof, fsid, manager, endpoint, receipt):
    if proof['binding'] != state['binding'] or proof['fsid'] != state['contract']['fsid']:
        raise Gate('proof binding or FSID mismatch')
    if (fsid, manager, endpoint, receipt) != (proof['fsid'], proof['manager'], proof['endpoint'], proof['receipt']):
        raise Gate('proof FSID/manager/exact endpoint/receipt mismatch')
    if endpoint not in state['contract']['endpoints']:
        raise Gate('unknown endpoint')
    if state['selected_manager'] and (manager, endpoint) != (state['selected_manager'], state['selected_endpoint']):
        raise Gate('selected manager or endpoint changed')


def require_delivery(state):
    if state['branch'] not in ('existing', 'new'):
        raise Gate('select after all discovery outcomes')
    if state['branch'] == 'new':
        merge = state['merge']
        if not merge or merge['pr_head'] != state['selected_head']:
            raise Gate('selected reviewed head must be merged')
        if merge['flux_source'] != merge['merged'] or merge['flux_applied'] != merge['merged']:
            raise Gate('Flux must apply actual merge SHA')
    if state['delivery'] != {'deployment': 'ready', 'grant': 'ready', 'cnp': 'ready'}:
        raise Gate('observed deployment, metrics grant and CNP readiness required')


def cmd_init(args):
    if (args.owner_kind != 'persistent' or args.owner_liveness != 'observed'
            or not args.owner_ready or not args.owner_live):
        raise Reject('OPERATIONS needs an observed ready/live persistent owner; a Task or cap is insufficient')
    if not args.planned_revalidation:
        raise Reject('separately explicit planned revalidation required for this future scope')
    endpoints = [parse_endpoint(value) for value in args.endpoint]
    covered = [parse_endpoint(value) for value in args.covered_endpoint]
    if not 1 <= len(endpoints) <= 4 or len(set(endpoints)) != len(endpoints):
        raise Reject('allowlist requires 1 to 4 distinct exact endpoints')
    if len(set(covered)) != len(covered) or not set(covered) <= set(endpoints):
        raise Reject('existing covered endpoints must be exact allowlisted routes')
    state = {'schema': 1, 'revision': 1, 'last_now': args.now, 'binding': caller(args),
             'owner': {'kind': 'persistent', 'ready': True, 'live': True, 'lost': False,
                       'operations_owner_accepted': False},
             'contract': {'fsid': _token(args.fsid, 'FSID'), 'endpoints': endpoints,
                          'covered_endpoints': covered, 'planned_revalidation': True},
             'cleanup': {'reserved_s': 0, 'deadline': None, 'ci_estimate_s': None,
                         'grants': [], 'evaluation': None},
             'candidates': [], 'selected_head': None, 'selected_manager': None,
             'selected_endpoint': None, 'branch': None, 'merge': {}, 'delivery': {},
             'reversal': {}, 'proof': {}, 'intents': {}, 'slot_intent': {}, 'blockers': [],
             'operation_complete': False, 'successor_authorized': False,
             'capacity_gate': 'external', 'admin_gate': 'external', 'series': 'UNKNOWN',
             'quotas': 'UNKNOWN', 'prior_pve_bundle': 'exhausted'}
    with ledger_lock(args.ledger):
        if os.path.lexists(args.ledger):
            raise Conflict('ledger exists; no reset')
        atomic_dump(args.ledger, state)
    return result_from(state)


def mutate_command(state, args):
    command = args.command
    # Cleanup, blocker and outcomes are writable after owner loss or plan expiry.
    if command in ('reserve-cleanup', 'record-outcome', 'record-blocker', 'record-residual'):
        advance_clock(state, args.now)
    elif command not in ('admit', 'evaluate-clean', 'resolve-blocker'):
        normal_stage(state, args)

    if command == 'reserve-cleanup':
        if args.seconds <= 0 or args.deadline <= args.now + args.seconds:
            raise Reject('reserve must leave real time before explicit caller deadline')
        if args.ci_estimate_s is not None and args.ci_estimate_s < 0:
            raise Reject('CI estimate must be nonnegative; it is not a guarantee')
        state['cleanup'].update(reserved_s=args.seconds, deadline=args.deadline,
                                ci_estimate_s=args.ci_estimate_s)
    elif command == 'prepare-candidate':
        if state['slot_intent'] or state['branch']:
            raise Gate('prepare actual PR head/CI before any discovery lease or read')
        head = _sha(args.head, 'candidate')
        if (args.reviewed_head != head or args.ci_head != head or args.private_review
                or args.flux_local != 'success' or args.diff_scope != 'success'):
            raise Gate('actual reviewed current PR head and both required aggregates must match and succeed')
        if not 0 <= args.ci_started_at < args.ci_finished_at <= args.now:
            raise Gate('actual CI must finish before discovery')
        if len(state['candidates']) >= MAX_CANDIDATES:
            raise Gate('at most three prepared alternatives')
        if any(item['head'] == head for item in state['candidates']):
            raise Conflict('duplicate candidate')
        endpoint = parse_endpoint(args.endpoint)
        if endpoint not in state['contract']['endpoints']:
            raise Gate('candidate endpoint outside allowlist')
        state['candidates'].append({'head': head, 'reviewed_head': head, 'ci_head': head,
                                    'pr': _token(args.pr, 'PR'), 'manager': _token(args.manager, 'manager'),
                                    'endpoint': endpoint, 'checks': dict.fromkeys(REQUIRED_CHECKS, 'success'),
                                    'ci_started_at': args.ci_started_at, 'ci_finished_at': args.ci_finished_at})
    elif command == 'select-candidate':
        successful(state, DISCOVERY_SLOTS)
        if state['branch']:
            raise Conflict('one selection/activation only')
        proof = state['proof']['discovery']
        classify_proof(proof, args.now)
        match_proof(state, proof, args.verified_fsid, args.verified_manager,
                    args.verified_endpoint, args.proof_receipt)
        if args.branch == 'existing':
            if proof['endpoint'] not in state['contract']['covered_endpoints']:
                raise Gate('no externally observed existing covered route')
        else:
            eligible = [item for item in state['candidates']
                        if (item['manager'], item['endpoint']) == (proof['manager'], proof['endpoint'])]
            if len(eligible) != 1:
                raise Gate('exactly one prepared reviewed candidate must match discovered route')
            state['selected_head'] = eligible[0]['head']
        state.update(branch=args.branch, selected_manager=proof['manager'], selected_endpoint=proof['endpoint'])
    elif command == 'record-merge':
        if state['branch'] != 'new' or args.pr_head != state['selected_head']:
            raise Gate('PR head must be selected prepared/reviewed head; existing route needs no merge')
        merged = _sha(args.merged_sha, 'actual squash merge')
        if args.flux_source != merged or args.flux_applied != merged:
            raise Gate('Flux source and applied proof must match actual merge SHA')
        if state['merge']:
            raise Conflict('merge already recorded')
        state['merge'] = dict(pr_head=args.pr_head, merged=merged,
                              flux_source=args.flux_source, flux_applied=args.flux_applied)
    elif command == 'record-reversal':
        if state['reversal']:
            raise Conflict('reversal already recorded')
        state['reversal'] = dict(head=_sha(args.head, 'reversal'), precertified=False, removal_ci='external-later')
    elif command == 'observe-delivery':
        if state['branch'] is None or (state['branch'] == 'new' and not state['merge']):
            raise Gate('delivery observation requires selected route and actual merge if new')
        if 'revalidation' in state['slot_intent']:
            raise Gate('delivery changes after revalidation need a new external plan, not another read')
        state['delivery'] = dict(deployment=args.deployment, grant=args.grant, cnp=args.cnp)
    elif command == 'admit':
        return admit(state, args)
    elif command == 'record-outcome':
        return record_outcome(state, args)
    elif command == 'record-blocker':
        if len(state['blockers']) >= 8:
            raise Reject('bounded blocker list full')
        state['blockers'].append(dict(id='blocker-' + str(len(state['blockers']) + 1),
                                     kind=args.kind, nonterminal=True, recorded_at=args.now,
                                     resolution=None))
        if args.kind == 'lost-owner':
            state['owner']['lost'] = True
    elif command == 'resolve-blocker':
        return resolve_blocker(state, args)
    elif command == 'record-residual':
        return residual(state, args)
    elif command == 'evaluate-clean':
        result = evaluate(state, args)
        advance_clock(state, args.now)
        return result
    else:
        raise Reject('no publication, operation completion or successor authority in this offline helper')
    return {}


def resolve_blocker(state, args):
    # A local recovery record is neither owner liveness nor approval evidence.
    require_owner_observation(state)
    advance_clock(state, args.now)
    next_budget = METRICS_BUDGET_S if 'revalidation' in state['slot_intent'] else PVE_BUDGET_S
    require_reserve(state, args.now, max(next_budget, args.stage_budget_s))
    blocker_id = _token(args.blocker_id, 'blocker ID')
    record = next((row for row in state['blockers'] if row.get('id') == blocker_id), None)
    if not record or record['kind'] != 'recoverable' or record['resolution'] is not None:
        raise Gate('only an unresolved recoverable blocker can be resolved once')
    if any(row['kind'] != 'recoverable' for row in state['blockers']):
        raise Gate('turn-end or lost-owner cannot be locally resurrected')
    if ('metrics' in state['slot_intent'] or any(
            row['outcome'] != 'success' or row['decision'] != 'admit' or not row['dispatch']
            for row in state['intents'].values())):
        raise Gate('consumed non-success or exhausted attempt remains blocked; no retry or refund')
    if not record['recorded_at'] <= args.verified_at <= args.now:
        raise Gate('external prerequisite observation must follow blocker and not be future')
    record['resolution'] = dict(reason=_token(args.reason, 'resolution reason'),
                                prerequisite_ref=_token(args.prerequisite_ref, 'external prerequisite reference'),
                                verified_at=args.verified_at, resolved_at=args.now,
                                binding=copy.deepcopy(state['binding']))
    state['cleanup']['evaluation'] = None
    return dict(resolved_blocker=blocker_id)


def admit(state, args):
    advance_clock(state, args.now)
    intent_id = _token(args.intent_id, 'intent')
    fingerprint = {key: getattr(args, key) for key in ('slot', 'decision', 'observed_fsid',
                   'observed_manager', 'observed_endpoint', 'proof_receipt')}
    prior = state['intents'].get(intent_id)
    if prior:
        if prior['fingerprint'] != fingerprint:
            raise Conflict('conflicting intent replay')
        return dict(replay=True, consumed=True, dispatch=False, next_metrics_reads=0)
    if args.slot in state['slot_intent']:
        raise Conflict('slot already consumed; pending after crash is not retryable')
    normal_stage(state, args, METRICS_BUDGET_S if args.slot == 'metrics' else PVE_BUDGET_S)
    if args.slot in DISCOVERY_SLOTS:
        if not state['candidates'] and not state['contract']['covered_endpoints']:
            raise Gate('prepare actual candidate CI or externally covered route before discovery')
        successful(state, DISCOVERY_SLOTS[:DISCOVERY_SLOTS.index(args.slot)])
    else:
        successful(state, DISCOVERY_SLOTS)
        require_delivery(state)
        if args.slot == 'metrics':
            successful(state, ('revalidation',))
    decision, reason, dispatch = args.decision, None, args.decision == 'admit'
    deadlines = {}
    if dispatch and args.slot == 'metrics':
        proof = state['proof']['revalidation']
        try:
            deadline = classify_proof(proof, args.now, METRICS_BUDGET_S)
            match_proof(state, proof, args.observed_fsid, args.observed_manager,
                        args.observed_endpoint, args.proof_receipt)
            deadlines = dict(proof_deadline=deadline,
                             transport_deadline=min(deadline, state['cleanup']['deadline'] - state['cleanup']['reserved_s']),
                             read_budget_s=METRICS_BUDGET_S)
        except Gate as exc:
            decision, reason, dispatch = 'denied', str(exc), False
    state['slot_intent'][args.slot] = intent_id
    state['intents'][intent_id] = dict(slot=args.slot, fingerprint=fingerprint,
                                     admitted_at=args.now, dispatch=int(dispatch),
                                     decision=decision, reason=reason, outcome=None)
    # This entire record is fsynced under the exclusive CAS lock BEFORE advice.
    return dict(dispatch=dispatch, consumed=True, decision=decision,
                next_metrics_reads=0, **deadlines)


def record_outcome(state, args):
    if args.outcome not in OUTCOMES:
        raise Reject('strict outcome enum: success/denied/unknown/uncertain/failure')
    record = state['intents'].get(args.intent_id)
    if not record:
        raise Gate('unknown intent')
    if not record['dispatch'] or record['decision'] != 'admit':
        raise Gate('no successful outcome for an intent without dispatch advice')
    receipt = _token(args.receipt, 'externally verified receipt')
    details = dict(outcome=args.outcome, request_at=args.request_at,
                   observed_at=args.observed_at, receipt=receipt, body_sha256=args.body_sha256,
                   fsid=args.observed_fsid, manager=args.observed_manager, endpoint=args.observed_endpoint)
    if record['outcome'] is not None:
        if record['result'] != details:
            raise Conflict('immutable outcome/receipt; no timestamp reset')
        return dict(replay=True)
    if args.outcome == 'success':
        if (args.request_at is None or args.observed_at is None
                or not record['admitted_at'] <= args.request_at <= args.observed_at <= args.now):
            raise Gate('success needs actual post-admission request/response interval')
        if not re.fullmatch(r'[0-9a-f]{64}', args.body_sha256 or ''):
            raise Reject('success needs externally verified body SHA256')
        if record['slot'] in ('ceph-status', 'revalidation'):
            key = 'discovery' if record['slot'] == 'ceph-status' else 'revalidation'
            proof = dict(binding=copy.deepcopy(state['binding']), **{k: details[k] for k in
                         ('request_at', 'observed_at', 'receipt', 'body_sha256', 'fsid', 'manager', 'endpoint')})
            _token(proof['fsid'], 'FSID')
            _token(proof['manager'], 'manager')
            parse_endpoint(proof['endpoint'])
            state['proof'][key] = proof
    record.update(outcome=args.outcome, result=details, receipt=receipt)
    return {}


def residual(state, args):
    uid = _token(args.uid, 'grant UID')
    receipt = _token(args.receipt, 'residual receipt')
    cnp_uid = _token(args.cnp_uid, 'exact CNP UID')
    active = args.active == 'true'
    grants = state['cleanup']['grants']
    old = next((item for item in grants if item['uid'] == uid), None)
    if old and (old['owner_class'] != args.owner_class or old['expires_at'] != args.expires_at or old['cnp_uid'] != cnp_uid):
        raise Conflict('grant UID class/expiry cannot change')
    if not active:
        if args.reason not in ('natural-expiry', 'released') or args.cnp != 'absent':
            raise Gate('inactive residual requires exact observed CNP absence and reason')
        if args.owner_class == 'other' and args.reason != 'natural-expiry':
            raise Reject('cannot release another owner; observed natural expiry may be recorded')
        if args.reason == 'natural-expiry' and args.expires_at > args.now:
            raise Gate('grant has not naturally expired')
    record = dict(uid=uid, owner_class=args.owner_class, expires_at=args.expires_at,
                  active=active, cnp=args.cnp, cnp_uid=cnp_uid, reason=args.reason, receipt=receipt, observed_at=args.now)
    if old:
        old.update(record)
    elif len(grants) < 16:
        grants.append(record)
    else:
        raise Reject('bounded residual list full')
    state['cleanup']['evaluation'] = None
    return dict(grants=grants)


def evaluate(state, args):
    # A qualified external auditor supplies this complete observation; the helper
    # compares its references, not truth/authenticity. Status flags alone cannot clean.
    audit, payload = read_json_file(args.audit_file, 'audit')
    if not isinstance(audit, dict) or audit.get('binding') != state['binding']:
        raise Gate('audit identity/scope/attempt mismatch')
    _token(audit.get('receipt'), 'external audit receipt')
    observed = audit.get('observed_at')
    if type(observed) is not int or not state['last_now'] <= observed <= args.now:
        raise Gate('audit must be a current externally observed snapshot')
    if type(audit.get('complete')) is not bool:
        raise Reject('explicit external audit completeness required')
    policy = audit.get('policy')
    if not isinstance(policy, dict) or type(policy.get('present')) is not bool:
        raise Reject('audit needs exact policy reference and observed presence')
    _token(policy.get('ref'), 'audited policy reference')
    grants = state['cleanup']['grants']
    rows = audit.get('grants')
    if not isinstance(rows, list) or len(rows) > 16 or any(not isinstance(row, dict) for row in rows):
        raise Reject('audit needs bounded exact class-grant observations')
    for row in rows:
        fields(row, dict(uid=str, owner_class=str, expires_at=int, cnp_uid=str,
                         active=bool, cnp_present=bool), 'audited grant')
        _token(row['uid'], 'audited grant UID')
        _token(row['cnp_uid'], 'audited grant CNP UID')
    if (len({row.get('uid') for row in rows}) != len(rows)
            or {row.get('uid') for row in rows} != {row['uid'] for row in grants}):
        raise Gate('audit must enumerate exact own AND other residual UIDs; record newly found residuals first')
    by_uid = {row['uid']: row for row in rows}
    for grant in grants:
        row = by_uid[grant['uid']]
        for key in ('uid', 'owner_class', 'expires_at', 'cnp_uid'):
            if row.get(key) != grant[key]:
                raise Gate('audit grant/expiry/CNP reference mismatch')
        if type(row.get('active')) is not bool or type(row.get('cnp_present')) is not bool:
            raise Reject('audit requires observed active state and exact CNP presence')
        if row['active'] != grant['active'] or row['cnp_present'] != (grant['cnp'] == 'present'):
            raise Gate('audit differs from residual ledger; record current observation first')
    cnps = audit.get('cnps')
    if (not isinstance(cnps, list) or len(cnps) > 16
            or any(not isinstance(row, dict) or type(row.get('present')) is not bool for row in cnps)):
        raise Reject('audit needs complete exact CNP inventory')
    for row in cnps:
        _token(row.get('uid'), 'audited CNP UID')
    if len({row['uid'] for row in cnps}) != len(cnps):
        raise Reject('duplicate CNP in audit')
    cnp_by_uid = {row['uid']: row['present'] for row in cnps}
    if any(cnp_by_uid.get(row['cnp_uid']) is not row['cnp_present'] for row in rows):
        raise Gate('exact CNP absence observation missing or inconsistent')
    clean = (audit['complete'] and not policy['present']
             and not any(row['present'] for row in cnps)
             and not any(row['active'] or row['cnp_present'] for row in rows))
    evaluation = dict(clean=clean, audit=audit,
                      audit_sha256=hashlib.sha256(payload).hexdigest(),
                      other_owners_released=False)
    state['cleanup']['evaluation'] = evaluation
    return dict(evaluation=evaluation)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', required=True, type=Path)
    sub = parser.add_subparsers(dest='command', required=True)

    def command(name, init=False):
        item = sub.add_parser(name)
        for key in ('identity', 'scope', 'attempt'):
            item.add_argument('--' + key, required=True)
        item.add_argument('--now', required=True, type=int)
        if not init:
            item.add_argument('--expect-revision', required=True, type=int)
        item.add_argument('--stage-budget-s', type=int, default=1)
        return item

    init = command('init', init=True)
    init.add_argument('--owner-kind', choices=('persistent', 'task'), required=True)
    init.add_argument('--owner-liveness', choices=('observed', 'cap-only', 'unobserved'), required=True)
    init.add_argument('--owner-ready', action='store_true')
    init.add_argument('--owner-live', action='store_true')
    init.add_argument('--owner-cap-s', type=int, help='informational only; never liveness or operation TTL')
    init.add_argument('--fsid', required=True)
    init.add_argument('--endpoint', action='append', required=True)
    init.add_argument('--covered-endpoint', action='append', default=[])
    init.add_argument('--planned-revalidation', action='store_true')
    reserve = command('reserve-cleanup')
    reserve.add_argument('--seconds', type=int, required=True)
    reserve.add_argument('--deadline', type=int, required=True)
    reserve.add_argument('--ci-estimate-s', type=int)
    prepare = command('prepare-candidate')
    for key in ('head', 'reviewed-head', 'ci-head', 'pr', 'manager', 'endpoint', 'flux-local', 'diff-scope'):
        prepare.add_argument('--' + key, required=True)
    for key in ('ci-started-at', 'ci-finished-at'):
        prepare.add_argument('--' + key, type=int, required=True)
    prepare.add_argument('--private-review', action='store_true')
    select = command('select-candidate')
    select.add_argument('--branch', choices=('existing', 'new'), required=True)
    for key in ('verified-fsid', 'verified-manager', 'verified-endpoint', 'proof-receipt'):
        select.add_argument('--' + key, required=True)
    merge = command('record-merge')
    for key in ('pr-head', 'merged-sha', 'flux-source', 'flux-applied'):
        merge.add_argument('--' + key, required=True)
    command('record-reversal').add_argument('--head', required=True)
    delivery = command('observe-delivery')
    for key in ('deployment', 'grant', 'cnp'):
        delivery.add_argument('--' + key, choices=('pending', 'ready'), required=True)
    admit_parser = command('admit')
    admit_parser.add_argument('--slot', choices=ALL_SLOTS, required=True)
    admit_parser.add_argument('--intent-id', required=True)
    admit_parser.add_argument('--decision', choices=('admit', 'pending', 'denied', 'unknown', 'uncertain'), required=True)
    outcome = command('record-outcome')
    outcome.add_argument('--intent-id', required=True)
    outcome.add_argument('--outcome', required=True)
    outcome.add_argument('--receipt', required=True)
    outcome.add_argument('--body-sha256')
    for key in ('request-at', 'observed-at'):
        outcome.add_argument('--' + key, type=int)
    for item in (admit_parser, outcome):
        for key in ('observed-fsid', 'observed-manager', 'observed-endpoint'):
            item.add_argument('--' + key, default='')
    admit_parser.add_argument('--proof-receipt', default='')
    command('record-blocker').add_argument('--kind', choices=('recoverable', 'turn-end', 'lost-owner'), required=True)
    resolution = command('resolve-blocker')
    for key in ('blocker-id', 'reason', 'prerequisite-ref'):
        resolution.add_argument('--' + key, required=True)
    resolution.add_argument('--verified-at', type=int, required=True)
    residual_parser = command('record-residual')
    for key in ('uid', 'receipt', 'cnp-uid'):
        residual_parser.add_argument('--' + key, required=True)
    residual_parser.add_argument('--owner-class', choices=('own', 'other'), required=True)
    residual_parser.add_argument('--active', choices=('true', 'false'), required=True)
    residual_parser.add_argument('--expires-at', type=int, required=True)
    residual_parser.add_argument('--cnp', choices=('present', 'absent'), required=True)
    residual_parser.add_argument('--reason', default='observed')
    evaluate_parser = command('evaluate-clean')
    evaluate_parser.add_argument('--audit-file', type=Path, required=True)
    for name in ('close-operation', 'authorize-successor', 'publish', 'show'):
        command(name)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.now < 0 or args.stage_budget_s <= 0:
            raise Reject('nonnegative wall time and positive stage budget required')
        if args.command == 'init':
            result = cmd_init(args)
        elif args.command == 'show':
            state = load_ledger(args.ledger)
            check_binding(state, args)
            if state['revision'] != args.expect_revision:
                raise Conflict('stale revision')
            result = dict(ok=True, ledger=state, dispatch=False)
        else:
            result = cas(args.ledger, args.expect_revision, lambda state: mutate_command(state, args), args)
    except (Conflict, Gate, Reject, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        code = 2 if isinstance(exc, Conflict) else 3 if isinstance(exc, Gate) else 4
        print(json.dumps(dict(ok=False, dispatch=False, next_metrics_reads=0,
                              error=type(exc).__name__, detail=str(exc)), sort_keys=True))
        return code
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
