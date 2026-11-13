#!/usr/bin/env python3
"""Synthetic shell integration tests; never invoke the responder or a live tool."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(sys.argv[1])
BASE = ROOT / 'kubernetes/main/apps/upgrade-agent'
RESP = (BASE / 'alert-responder/app/resources/respond.sh').read_text()
OPS = BASE / 'dev-env-ops/app/resources'
LIB = BASE / 'health-gate/app/resources'
IDENTITY = {'fingerprint': 'AbC:/"\\☃\t\n0000 ',
            'startsAt': '2026-10-31T00:00:00.' + '1234567890' * 200 + 'Z\n'}
ALERT = dict(IDENTITY, labels={'alertname': 'Fixture', 'namespace': 'fixture'},
             annotations={'description': 'context\n' + 'x' * 1000})
UNIT = {'type': 'single', 'members': [{'alert': ALERT}]}


def section(text, start, end):
    return text[text.index(start):text.index(end, text.index(start))]


def pipeline(text, anchor):
    """Extract the real get/jq/replace pipeline enclosing a unique write."""
    pos = text.index(anchor)
    start = text.rfind('\n', 0, text.rfind('kubectl ', 0, pos)) + 1
    end = text.index('\n', text.index('| kubectl ', pos))
    return text[start:end].removesuffix(' \\')


with tempfile.TemporaryDirectory(prefix='alert-identity-') as tmp:
    T = Path(tmp)
    bin_dir = T / 'bin'
    bin_dir.mkdir()
    store = T / 'cm.json'
    fake = bin_dir / 'kubectl'
    fake.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p=Path(os.environ['FIXTURE_CM']); args=sys.argv[1:]
if 'get' in args:
    if not p.exists(): sys.exit(1)
    print(p.read_text())
elif 'replace' in args:
    obj=json.load(sys.stdin); p.write_text(json.dumps(obj))
elif 'create' in args:
    arg=next(a for a in args if a.startswith('--from-literal='))
    key,value=arg[len('--from-literal='):].split('=',1)
    p.write_text(json.dumps({'data':{key:value}}))
else: sys.exit('unexpected fake kubectl operation')
''')
    fake.chmod(0o755)
    # No network side effect can escape the harness.
    for name in ('curl', 'tmux'):
        p = bin_dir / name
        p.write_text('#!/bin/sh\nexit 99\n')
        p.chmod(0o755)
    date = bin_dir / 'date'
    date.write_text('#!/bin/sh\ncase "$*" in *+%s*) echo "${FIXTURE_NOW:-2000000000}";; *) /bin/date "$@";; esac\n')
    date.chmod(0o755)
    env = dict(os.environ, PATH=str(bin_dir) + ':' + os.environ['PATH'],
               FIXTURE_CM=str(store), REMEDIATE_SIG='fixture', ESCALATE_SIG='fixture')
    # Sanitize inherited optional producer inputs so fixtures own all identity.
    for k in ('REMEDIATE_ALERT_IDENTITY', 'ESCALATE_ALERT_IDENTITY'):
        env.pop(k, None)
    escalate = T / 'escalate.sh'
    escalate.write_text((LIB / 'escalate.sh').read_text())
    status = T / 'order-status.sh'
    status.write_text((OPS / 'order-status.sh').read_text().replace(
        '/opt/coordination/escalate.sh', str(escalate)))

    def run(code, extra=None):
        subprocess.run(['bash', '-uo', 'pipefail', '-c', code], env=env | (extra or {}),
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def reset():
        store.unlink(missing_ok=True)

    def orders():
        return {k: json.loads(v) for k, v in json.loads(store.read_text())['data'].items()}

    def seed(key, entry):
        store.write_text(json.dumps({'data': {key: json.dumps(entry)}}))

    def identity(order):
        assert order.get('alertIdentity') == IDENTITY, 'exact identity missing or changed'
        # Explicit UTF-8 byte comparison includes embedded controls and trailing newline.
        for k, v in IDENTITY.items():
            assert order['alertIdentity'][k].encode() == v.encode(), k + ' bytes changed'

    def singleton(unit=UNIT, failed=False):
        # The unit setup and handoff are extracted, without executing respond.sh.
        setup = section(RESP[RESP.index('while [ "$u"'):], '  alert="$(printf', '  fps="$(printf')
        if failed:
            # rem_watchdog has an earlier ESCALATE_SIG line; select failed diagnosis.
            tail = RESP[RESP.index('    if [ "$rc" -ne 0 ]; then'):]
            call = tail.split('\n', 1)[1].split('\n        || log', 1)[0]
            call = call.replace('/opt/coordination/escalate.sh', str(escalate))
        else:
            call = section(RESP, '        if ESCALATE_SIG=', '; then\n          handed_off=1')
            call = call.replace('        if ', '        ', 1)
        run('alert_identity=null\n' + setup + '\n' + call,
            {'unit': json.dumps(unit), 'REMEDIATE_SH': str(LIB / 'remediate.sh'),
             'REPORT': 'fixture report', 'aname': 'Fixture', 'ans': 'fixture',
             'utype': unit['type'], 'n': str(len(unit['members'])), 'rc': '1'})
        return next(iter(orders().items()))

    def bound_parent():
        reset()
        key, order = singleton()
        identity(order)  # Every scenario must first prove producer binding (fails on main).
        return key, order

    failures = []

    def test(name, fn):
        try:
            fn()
            print('ok  ' + name)
        except (AssertionError, subprocess.CalledProcessError, ValueError) as exc:
            failures.append(name)
            print('FAIL ' + name + ': ' + (exc.stderr.decode() if isinstance(exc, subprocess.CalledProcessError) else str(exc)))

    def legacy_context():
        _, order = bound_parent()
        context = subprocess.check_output(['jq', '-c', '{labels, annotations, startsAt}'],
                                          input=json.dumps(ALERT).encode()).decode().rstrip('\n')
        assert isinstance(order['alert'], str)
        assert order['alert'] == context[:900], 'legacy context content/clamp changed'
        assert len(order['alert']) == 900

    test('singleton remediation exact bytes and unchanged legacy context', legacy_context)

    def failed_diagnosis():
        reset()
        key, order = singleton(failed=True)
        assert key.startswith('esc-responder-')
        identity(order)

    test('failed diagnosis esc-responder exact bytes', failed_diagnosis)

    def exclusions():
        bound_parent()
        for unit in (dict(UNIT, type='storm'), dict(UNIT, members=UNIT['members'] * 2),
                     dict(UNIT, type='aggregate'),
                     {'type': 'single', 'members': [{'alert': {'labels': ALERT['labels'],
                                                               'startsAt': IDENTITY['startsAt']}}]}):
            for failed in (False, True):
                reset()
                _, order = singleton(unit, failed)
                assert 'alertIdentity' not in order, 'aggregate/missing input was joined'

    test('storm aggregate and missing raw data omit identity in both lanes', exclusions)

    def watchdog():
        bound_parent()
        # Even an inherited identity must not attach to this producer.
        call = section(RESP, '    ESCALATE_SIG="${sig:-$key}"', '\n      || log')
        run(call.replace('/opt/coordination/escalate.sh', str(escalate)),
            {'sig': 'watchdog', 'key': 'rem-fixture', 'st': 'claimed', 'age': '200',
             'reason': 'fixture', 'ESCALATE_ALERT_IDENTITY': json.dumps(IDENTITY)})
        order = next(o for k, o in orders().items() if k.startswith('esc-rem-watchdog-'))
        assert 'alertIdentity' not in order

    test('watchdog excludes even inherited identity', watchdog)

    def malformed_input():
        # Optional identity input that is not exactly one valid object must be
        # absent and must never cost the order itself.
        for bad in (' ', '\n', '{}{}', '{"fingerprint":"a","startsAt":"b"} {"x":1}',
                    'not json', '[]', '"plain"', '{"fingerprint":1,"startsAt":"b"}'):
            reset()
            run(f'bash "{LIB / "remediate.sh"}" responder fixture reason',
                {'REMEDIATE_ALERT_IDENTITY': bad})
            order = next(o for k, o in orders().items() if k.startswith('rem-responder-'))
            assert 'alertIdentity' not in order, 'malformed remediate input was attached: ' + repr(bad)
            reset()
            run(f'bash "{escalate}" responder fixture reason', {'ESCALATE_ALERT_IDENTITY': bad})
            order = next(o for k, o in orders().items() if k.startswith('esc-responder-'))
            assert 'alertIdentity' not in order, 'malformed escalate input was attached: ' + repr(bad)

    test('malformed identity input still files the order with no identity', malformed_input)

    PARENT_CREATED = '1999999999'  # distinct from the fixture clock the child is stamped with

    def lineage(child, key, created=PARENT_CREATED):
        assert child.get('parentOrder') == {'key': key, 'created': created}, 'parentOrder missing or inexact'
        assert child['created'] != child['parentOrder']['created'], 'child own created copied'

    def promote(mode, key):
        if mode in ('failed', 'escalate'):
            code = f'bash "{status}" "$key" {mode} fixture'
        else:
            text = (OPS / ('work-order-watch.sh' if mode == 'orphan' else 'session-launch.sh')).read_text()
            start = ('          bash /opt/dev-env-ops/order-status.sh' if mode == 'orphan'
                     else '        bash /opt/dev-env-ops/order-status.sh')
            call = section(text, start, '\n            >/dev/null' if mode == 'orphan' else '\n          || oplog')
            code = call.replace('/opt/dev-env-ops/order-status.sh', str(status)).removesuffix(' \\')
        run(code, {'key': key, 'why': 'fixture', 'NS': 'upgrade-agent', 'CM': 'upgrade-work-orders'})

    def seeded_parent(**changes):
        key, parent = bound_parent()
        parent['created'] = PARENT_CREATED
        for k, v in changes.items():
            if v is None:
                parent.pop(k, None)
            else:
                parent[k] = v
        seed(key, parent)
        return key

    def child_of(key):
        return next(o for k, o in orders().items() if k.startswith('esc-rem-') and k != key)

    def promotion(mode, legacy=False):
        key = seeded_parent(alertIdentity=None) if legacy else seeded_parent()
        promote(mode, key)
        child = child_of(key)
        updated_parent = orders()[key]
        assert 'parentOrder' not in updated_parent, 'parent itself gained lineage'
        if legacy:
            assert 'alertIdentity' not in child and 'alertIdentity' not in updated_parent
        else:
            identity(child)
            identity(updated_parent)
        lineage(child, key)

    for mode in ('escalate', 'failed', 'orphan', 'closeout'):
        test(mode + ' promotion copies persisted parent identity', lambda m=mode: promotion(m))
    test('legacy promotion omits identity without backfill', lambda: promotion('escalate', True))

    def put(data):
        store.write_text(json.dumps({'data': {k: json.dumps(v) for k, v in data.items()}}))

    def legacy_parent_lineage():
        # No string `created` on the parent: no lineage, never inferred from
        # run_ref/text. Identity still copies, since that is a separate field.
        for bad in (None, '', 1999999999, ['1']):
            for mode in ('escalate', 'orphan'):
                key = seeded_parent(created=bad, run_ref=PARENT_CREATED, reason=PARENT_CREATED,
                                    note=PARENT_CREATED)
                promote(mode, key)
                child = child_of(key)
                assert 'parentOrder' not in child, 'lineage inferred for created=' + repr(bad)
                identity(child)

    test('legacy parent without a created string gives no parentOrder', legacy_parent_lineage)

    def non_promotion_orders():
        # Even a hostile inherited ESCALATE_PARENT_ORDER is ignored by every
        # non-promotion producer: rem-responder, esc-responder, esc-rem-watchdog.
        env_lineage = {'ESCALATE_PARENT_ORDER': json.dumps({'key': 'k', 'created': '1'}),
                       'REMEDIATE_PARENT_ORDER': json.dumps({'key': 'k', 'created': '1'})}
        reset()
        run(f'bash "{LIB / "remediate.sh"}" responder fixture reason', env_lineage)
        assert 'parentOrder' not in next(o for k, o in orders().items() if k.startswith('rem-responder-'))
        reset()
        run(f'bash "{escalate}" responder fixture reason', env_lineage)
        assert 'parentOrder' not in next(o for k, o in orders().items() if k.startswith('esc-responder-'))
        reset()
        call = section(RESP, '    ESCALATE_SIG="${sig:-$key}"', '\n      || log')
        run(call.replace('/opt/coordination/escalate.sh', str(escalate)),
            {'sig': 'watchdog', 'key': 'rem-fixture', 'st': 'claimed', 'age': '200',
             'reason': 'fixture', **env_lineage})
        assert 'parentOrder' not in next(o for k, o in orders().items() if k.startswith('esc-rem-watchdog-'))
        # The shipped producers' own singleton paths write none either.
        for failed in (False, True):
            reset()
            _, order = singleton(failed=failed)
            assert 'parentOrder' not in order
        # Only a well-formed key+created object attaches, and only for source rem.
        for bad in ('', 'not json', '[]', '{}', '{"key":"k"}', '{"created":"1"}', '{"key":"","created":"1"}',
                    '{"key":"k","created":""}', '{"key":1,"created":"1"}', '{"key":"k","created":1}', '{"key":"k","created":"1"}{}'):
            reset()
            run(f'bash "{escalate}" rem order:k reason', {'ESCALATE_PARENT_ORDER': bad})
            assert 'parentOrder' not in next(iter(orders().values())), 'malformed lineage attached: ' + repr(bad)

    test('non-promotion orders and malformed lineage input write no parentOrder', non_promotion_orders)

    def reused_key_lineage():
        for mode in ('escalate', 'failed', 'orphan', 'closeout'):
            key = seeded_parent()
            promote(mode, key)
            first = child_of(key)
            lineage(first, key)
            child_key = next(k for k in orders() if k.startswith('esc-rem-'))
            # A pending/claimed child dedups and keeps its original lineage.
            data = orders()
            data[key].update(status='working', created='2000000007')
            put(data)
            promote(mode, key)
            assert orders()[child_key]['parentOrder']['created'] == PARENT_CREATED, 'dedup rewrote lineage'
            # After terminal dedup the same key is a NEW order with its own lineage.
            data = orders()
            data[child_key].update(status='done', created='1', updated='1')
            put(data)
            promote(mode, key)
            new = orders()[child_key]
            assert new['created'] == '2000000000'
            assert new['parentOrder'] == {'key': key, 'created': '2000000007'}, 'reused key kept old lineage'
            # Terminal again, and the new parent is legacy: the old lineage must not survive.
            data = orders()
            data[child_key].update(status='failed')
            data[key].pop('created')
            put(data)
            promote(mode, key)
            assert 'parentOrder' not in orders()[child_key], 'legacy reuse kept stale lineage'

    test('reused esc-rem key gets its own new lineage after terminal dedup', reused_key_lineage)

    def no_backfill():
        key, parent = bound_parent()
        del parent['alertIdentity']
        seed(key, parent)  # pending dedup must never attach new identity.
        singleton()
        assert 'alertIdentity' not in orders()[key]
        parent['status'] = 'done'
        seed(key, parent)
        run(f'bash "{LIB / "remediate.sh"}" responder fixture reason',
            {'REMEDIATE_ALERT': json.dumps(ALERT)})
        assert 'alertIdentity' not in orders()[key], 'legacy context inferred identity'

    test('pending dedup and legacy context never backfill identity', no_backfill)

    def reuse(failed):
        reset()
        key, order = singleton(failed=failed)
        identity(order)
        order.update(status='done', created='1', updated='1')
        seed(key, order)
        new_identity = dict(IDENTITY, fingerprint='New/Refire "exact"', startsAt=IDENTITY['startsAt'] + 'new')
        unit = {'type': 'single', 'members': [{'alert': dict(ALERT, **new_identity)}]}
        new_key, new = singleton(unit, failed)
        assert new_key == key
        assert new['created'] == '2000000000'
        assert new['alertIdentity'] == new_identity
        # Reusing the same key without identity must discard the old one too.
        new['status'] = 'failed'
        seed(key, new)
        _, legacy = singleton({'type': 'single', 'members': [{'alert': {'labels': ALERT['labels']}}]}, failed)
        assert 'alertIdentity' not in legacy

    for failed in (False, True):
        test(('escalation' if failed else 'remediation') + ' reused key replaces identity and created',
             lambda f=failed: reuse(f))

    def bound_child():
        key = seeded_parent()
        promote('escalate', key)
        child_key = next(k for k in orders() if k.startswith('esc-rem-'))
        identity(orders()[child_key])
        lineage(orders()[child_key], key)
        return child_key

    def preserved(order):
        identity(order)
        assert order.get('parentOrder', {}).get('created') == PARENT_CREATED, 'parentOrder dropped by a rewriter'
        assert order['parentOrder']['key'].startswith('rem-responder-'), 'parentOrder key changed'

    def rewrite(source, anchor, extra=None):
        key = bound_child()
        code = pipeline(source, anchor)
        run(code, {'key': key, 'NS': 'fixture', 'CM': 'fixture', 'WORK_ORDER_CM': 'fixture',
                   'NOW': '2000000000', 'now': '2000000000', 'note': 'fixture',
                   'write_status': 'done', 'STATE_KEY': 'digest.last', 'rc': '1',
                   'marks': json.dumps([key]), 'reaped_marks': json.dumps([key])} | (extra or {}))
        updated = orders()[key]
        preserved(updated)
        if 'digested' in anchor:
            assert updated['digested'] == '2000000000'
        elif 'reaped' in anchor:
            assert updated['reaped'] == '2000000000'
        else:
            assert updated['updated'] == '2000000000'
            if 'failed' in anchor or 'stale-lane' in anchor:
                assert updated['status'] == 'failed'
            elif '.status=$s' in anchor:
                assert updated['status'] == 'done'
            else:
                assert updated['note'] == 'fixture'

    status_text = status.read_text()
    test('heartbeat preserves identity and parentOrder', lambda: rewrite(status_text, '.note=$n | .updated=$now'))
    test('terminal status preserves identity and parentOrder', lambda: rewrite(status_text, '.status=$s | .note=$n'))
    watch = (OPS / 'work-order-watch.sh').read_text()

    def update_status():
        key = bound_child()
        body = section(watch, 'update_status()', '\n}\n') + '\n}\n'
        run(body + '\nupdate_status "$key" claimed fixture', {'key': key, 'NS': 'fixture', 'CM': 'fixture'})
        preserved(orders()[key])
        assert orders()[key]['status'] == 'claimed'

    test('watcher update_status preserves identity and parentOrder', update_status)
    test('watcher reaped stamp preserves identity and parentOrder', lambda: rewrite(watch, '+ {reaped: $now}'))
    test('responder watchdog failed stamp preserves parent identity and parentOrder',
         lambda: rewrite(RESP, '.note="stale-lane watchdog:'))
    test('session-launch failed stamp preserves identity and parentOrder',
         lambda: rewrite((OPS / 'session-launch.sh').read_text(), '.status="failed" | .note=$n'))
    test('digest stamp preserves identity and parentOrder',
         lambda: rewrite((OPS / 'ops-digest.sh').read_text(), '.digested = $now'))

    if failures:
        sys.exit(f'{len(failures)} identity case(s) failed')
    print('PASS: all exact identity producer and rewriter cases (offline fixtures only)')
