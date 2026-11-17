"""Story 848: execute the shipped handoff branch, then the whole responder offline.

No inherited environment, login, network command or real wait reaches a child.
The command stubs assert the environment at every call and reject unknown writes.
"""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / 'kubernetes/main/apps/upgrade-agent'
SOURCE = Path(os.environ.get('RESP', RES / 'alert-responder/app/resources/respond.sh')).read_text()
GATE = (RES / 'health-gate/app/resources/gate.sh').read_text()
FIXTURES = ROOT / 'scripts/upgrade-agent/fixtures/responder-claim'
STUB = ROOT / 'scripts/upgrade-agent/responder-claim-stub.py'
NOW = 1794938400
LANE = json.loads((FIXTURES / 'lane.json').read_text())
TRIAGE = json.loads((FIXTURES / 'triage.json').read_text())
ALERT = json.loads((FIXTURES / 'alert.json').read_text())
POD = copy.deepcopy(ALERT)
POD['labels'] = dict(alertname='KubePodCrashLooping', severity='critical', namespace='media', pod='plex-abcde')
KEY = 'fixture-claim'
passed = failed = 0


def cm(name, data):
    return dict(apiVersion='v1', kind='ConfigMap', metadata=dict(name=name, namespace='upgrade-agent'), data=data)


def snapshot(entry):
    return cm('upgrade-remediation-state', {KEY: json.dumps(entry)})


def change(entry, **fields):
    return dict(copy.deepcopy(entry), **fields)


def branch(source):
    start = source.index('      handed_off=0\n')
    end = source.index('      [ "$handed_off" = "1" ] || page ', start)
    return source[start:source.index('\n', end)]


def child_env(bin_dir, world):
    # Allowlist, not a copy of os.environ: KUBECONFIG, ALL KUBERNETES_* and real
    # credentials are absent even when this suite runs in a logged-in session pod.
    return dict(PATH=f'{bin_dir}:/usr/bin:/bin', TEST_BIN=str(bin_dir), TEST_WORLD=str(world),
                TEST_NOW=str(NOW), TEST_DATE=shutil.which('date'), TEST_JQ=shutil.which('jq'),
                HOME=str(world / 'home'), TMPDIR=str(world), LC_ALL='C',
                CLAUDE_CODE_OAUTH_TOKEN='offline-fixture', PUSHOVER_TOKEN='offline-fixture',
                PUSHOVER_USER_KEY='offline-fixture', DEV_ENV_API_REACHED='1',
                RESPONDER_STORM_THRESHOLD='2')


def run(state, alerts=None, *, source=SOURCE, full=False, extra=None, orders=None, late=None):
    alerts = copy.deepcopy(alerts if alerts is not None else [ALERT])
    with tempfile.TemporaryDirectory(prefix='responder-claim-') as directory:
        world = Path(directory)
        bin_dir = world / 'bin'
        bin_dir.mkdir()
        # Stub every external effect the responder could reach, including waits.
        for tool in ['kubectl', 'curl', 'claude', 'git', 'sleep', 'jq', 'date', 'flux']:
            target = bin_dir / tool
            target.write_text(STUB.read_text())
            target.chmod(0o755)
        (world / 'home').mkdir()
        (world / 'events').touch()
        (world / 'claim').write_text(state if isinstance(state, str) else json.dumps(state))
        (world / 'alerts').write_text(json.dumps(alerts))
        (world / 'responder-state').write_text(json.dumps(cm('alert-responder-state', {})))
        (world / 'orders').write_text(json.dumps(orders or cm('upgrade-work-orders', {})))
        if late is not None:
            (world / 'after-diagnosis').write_text(json.dumps(late))
        remediate = world / 'remediate.sh'
        remediate.write_text((RES / 'health-gate/app/resources/remediate.sh').read_text())
        remediate.chmod(0o755)
        env = child_env(bin_dir, world)
        env['RESPONDER_REMEDIATE_SH'] = str(remediate)
        if extra:
            env.update(extra)
        if full:
            script = source
        else:
            unit = dict(members=[dict(alert=a) for a in alerts])
            labels = alerts[0]['labels']
            env.update(unit=json.dumps(unit), alert=json.dumps(alerts[0]),
                       aname=labels['alertname'], ans=labels.get('namespace', labels.get('exported_namespace', '')),
                       n=str(len(alerts)), utype='storm' if len(alerts) >= 2 else 'single',
                       REPORT='ACTION: urgent | CAUSE: fixture', ACTION=env.get('TEST_ACTION', 'urgent'),
                       REMEDIATION_ENABLED=env.get('TEST_ENABLED', '1'), REMEDIATE_SH=str(remediate), title='fixture')
            script = '''set -uo pipefail
log() { printf '%s\\n' "$*" >&2; }
page() { curl -sf https://api.pushover.net/1/messages.json >/dev/null; }
''' + branch(source)
        path = world / 'respond.sh'
        path.write_text(script)
        result = subprocess.run(['bash', str(path)], env=env, text=True, capture_output=True, timeout=15)
        events = [json.loads(line) for line in (world / 'events').read_text().splitlines()]
        assert result.returncode == 0, f'exit {result.returncode}: {result.stderr}'
        assert 'Traceback' not in result.stderr, result.stderr
        assert not any(e['kind'] == 'unexpected' for e in events), events
        reads = [e for e in events if e['kind'] == 'kubectl' and 'upgrade-remediation-state' in e['args']]
        return dict(log=result.stderr, events=events, reads=reads,
                    orders=json.loads((world / 'orders').read_text()),
                    filed=sum(e['kind'] == 'order' for e in events),
                    pages=sum(e['kind'] == 'page' for e in events))


def expect(state, *, held=False, alerts=None, source=SOURCE, extra=None, full=False, late=None):
    result = run(state, alerts, source=source, extra=extra, full=full, late=late)
    assert result['filed'] == (0 if held else 1), result['log']
    assert result['pages'] == 0, result['log']
    assert len(result['reads']) == 1, result
    assert all(e['args'][2] == 'get' for e in result['reads']), result
    if held:
        assert f'HELD (' in result['log'] and f'{KEY} holder=' in result['log'] and 'until=' in result['log'], result
    else:
        assert 'HELD (' not in result['log'], result
    return result


def test(name, fn):
    global passed, failed
    try:
        fn()
        passed += 1
        print('ok   ' + name, flush=True)
    except Exception as error:
        failed += 1
        print('FAIL ' + name + ': ' + str(error), flush=True)


cases = []


def case(name, entry=LANE, held=False, **options):
    state = snapshot(entry)
    cases.append((name, lambda source=SOURCE: expect(state, source=source, held=held, **options)))


case('working lane holds exact Flux object', held=True)
case('reverting lane holds', change(LANE, lane_state='reverting'), held=True)
case('lane at its bound still holds (now is not after until)', change(LANE, until=str(NOW)), held=True)
case('triage holds exact pod', TRIAGE, held=True, alerts=[POD])
case('triage holds exact Flux object', change(TRIAGE, ids=LANE['ids']), held=True)
case('lane holds exact pod', change(LANE, ids=TRIAGE['ids']), held=True, alerts=[POD])
for label, fields in [
    ('different object name', dict(ids=['flux/HelmRelease/media/other'])),
    ('namespace alone', dict(ids=['media'])),
    ('different namespace', dict(ids=['flux/HelmRelease/other/plex'])),
    ('different kind', dict(ids=['flux/Kustomization/media/plex'])),
    ('different holder', dict(holder='someone')),
    ('missing holder', dict(holder=None)),
    ('not upgrade', dict(**{'class': 'nonupgrade'})),
    ('not attempted', dict(attempted='0')),
    ('unknown schema', dict(schema='unknown')),
    ('unknown state', dict(lane_state='unknown')),
    ('reverted', dict(lane_state='reverted', result='fixed')),
    ('revert failed', dict(lane_state='revert-failed', result='failed')),
    ('acting but failed result', dict(result='failed')),
    ('acting but unknown result', dict(result='unknown')),
    ('expired lane', dict(until=str(NOW - 1))),
    ('future claimed_at', dict(claimed_at=str(NOW + 301))),
    ('bound before claim', dict(claimed_at=str(NOW + 100), until=str(NOW + 99))),
    ('bound too long', dict(until=str(int(LANE['claimed_at']) + 21601))),
    ('missing pr', dict(pr=None)),
    ('bad numeric bound', dict(until='1794948600junk')),
    ('leading zero bound', dict(until='01794948600')),
    ('leading zero claimed_at', dict(claimed_at='01794937800')),
    ('empty ids', dict(ids=[])),
    ('ids wrong type', dict(ids='flux/HelmRelease/media/plex')),
    ('ids has unknown value', dict(ids=[LANE['ids'][0], 3])),
]:
    case(label, change(LANE, **fields))
for label, fields in [
    ('triage failed', dict(result='failed')),
    ('triage fixed', dict(result='fixed')),
    ('triage unknown result', dict(result='unknown')),
    ('triage boolean result', dict(result=False)),
    ('triage array result', dict(result=[])),
    ('triage array subsequence result', dict(result=['none'])),
    ('triage object result', dict(result={})),
    ('triage numeric result', dict(result=1)),
    ('triage grace spent', dict(first_seen=str(NOW - 2400))),
    ('triage future time', dict(first_seen=str(NOW + 301))),
    ('triage missing time', dict(first_seen=None)),
    ('triage invalid time', dict(first_seen='001')),
    ('triage leading zero time', dict(first_seen='01794937800')),
]:
    case(label, change(TRIAGE, **fields), alerts=[POD])
for labels in [dict(alertname='FluxReconciliationFailure', customresource_kind='HelmRelease', name='plex'),
               dict(alertname='FluxReconciliationFailure', exported_namespace='media', name='plex'),
               dict(alertname='FluxReconciliationFailure', customresource_kind='HelmRelease', exported_namespace='media'),
               dict(alertname='UnrelatedCritical', namespace='media'),
               dict(alertname='KubePodCrashLooping', pod='plex-abcde')]:
    case('incomplete labels ' + json.dumps(labels), alerts=[change(ALERT, labels=labels)])
case('all storm members held', held=True, alerts=[ALERT, ALERT])
case('one storm member unheld', alerts=[ALERT, change(ALERT, labels=change(ALERT['labels'], name='other'))])
for name, fn in cases:
    test(name, fn)
for label, state, extra in [
    ('absent state', snapshot(LANE), dict(TEST_READ='absent')),
    ('unreadable state', snapshot(LANE), dict(TEST_READ='unreadable')),
    ('failed read with valid partial output', snapshot(LANE), dict(TEST_READ='partial')),
    ('jq failure', snapshot(LANE), dict(TEST_JQ_FAIL='1')),
    ('jq failure with partial hold output', snapshot(LANE), dict(TEST_JQ_FAIL='partial')),
    ('empty state', cm('upgrade-remediation-state', {}), None),
    ('no data', {}, None),
    ('bad JSON', '{garbled', None),
    ('bad entry JSON', cm('upgrade-remediation-state', {KEY: '{garbled'}), None),
    ('wrong data type', cm('upgrade-remediation-state', []), None),
    ('wrong entry type', cm('upgrade-remediation-state', {KEY: '[]'}), None),
    ('malformed sibling with matching entry', cm('upgrade-remediation-state', {KEY: json.dumps(LANE), 'bad': '{bad'}), None),
]:
    test(label, lambda s=state, e=extra: expect(s, extra=e))


def gate_clock():
    # Compare triage decisions to the actual gate function, including both edges
    # and its environment settings. No second interpretation of its grace clock.
    fn = re.search(r'^remediation_phase\(\) \{.*?^}', GATE, re.M | re.S).group()
    with tempfile.TemporaryDirectory() as directory:
        env = dict(PATH='/usr/bin:/bin', NOW=str(NOW), REMEDIATE_GRACE_MINUTES='40', CLOCK_SKEW_SECONDS='300')
        for result in ['none', 'pending', 'inprogress', '', 'fixed', 'failed', 'unknown']:
            for at in [NOW - 2400, NOW - 2399, NOW + 300, NOW + 301]:
                out = subprocess.run(['bash', '-c', fn + '\nremediation_phase "$1" "$2" "$3"',
                                      'gate-test', 'upgrade', result, str(at)], env=env, text=True, capture_output=True, check=True)
                expect(snapshot(change(TRIAGE, result=result, first_seen=str(at))),
                       held=out.stdout == 'inflight', alerts=[POD])


test('28 triage clock/result combinations agree with gate remediation_phase', gate_clock)
test('full responder holds after a claim appears during diagnosis',
     lambda: expect(cm('upgrade-remediation-state', {}), full=True, held=True, late=snapshot(LANE)))
test('full responder fails open without a matching claim', lambda: expect({}, full=True))


def multiple_holders():
    state = snapshot(LANE)
    state['data']['triage-claim'] = json.dumps(TRIAGE)
    result = expect(state, held=True, alerts=[ALERT, POD])
    assert 'fixture-claim holder=lane' in result['log'] and 'triage-claim holder=triage' in result['log'], result['log']


test('one read can hold different storm members with different holders', multiple_holders)


def failed_order():
    result = run({}, extra=dict(TEST_ORDER_FAIL='1'), full=True)
    assert result['pages'] == 1 and 'handoff FAILED' in result['log'], result


test('failed order still pages once', failed_order)


def no_handoff():
    result = run(snapshot(LANE), extra=dict(TEST_ACTION='investigate'), full=True)
    assert result['pages'] == 1 and result['filed'] == 0 and not result['reads'], result
    result = run(snapshot(LANE), extra=dict(RESPONDER_REMEDIATION_ENABLED='0'), full=True)
    assert result['pages'] == 1 and result['filed'] == 0 and not result['reads'], result


test('investigate and disabled remediation do not read claims', no_handoff)


def stable_key(source=SOURCE):
    alerts = [change(ALERT, fingerprint=f'fp{i}') for i in range(2)]
    first = run({}, alerts, source=source, full=True)
    second = run({}, alerts + [change(ALERT, fingerprint='fp3')], source=source, full=True, orders=first['orders'])
    assert len(first['orders']['data']) == len(second['orders']['data']) == 1, (first, second)
    entry = json.loads(next(iter(first['orders']['data'].values())))
    assert entry['sig'] == 'FluxReconciliationFailure@media', entry
    assert first['filed'] == 1 and second['filed'] == second['pages'] == 0, second


test('full responder: counts 2 and 3 share one actual remediate order key', stable_key)


def legacy_key():
    old = dict(status='pending', created=str(NOW), updated=str(NOW), sig='old reason with count')
    orders = cm('upgrade-work-orders', {'rem-responder-oldkey': json.dumps(old)})
    result = run({}, full=True, orders=orders)
    assert len(result['orders']['data']) == 2 and result['filed'] == 1, result['log']
    assert json.loads(result['orders']['data']['rem-responder-oldkey']) == old


test('an open old-key order is preserved when the corrected key is filed', legacy_key)

# Each mutant is valid shell and weakens one independent hold condition. The
# matching negative fixture must detect the unwanted silence. No live commands.
MUTATIONS = [
    ('object identity', 'index($id) != null', 'true', 'different object name'),
    ('holder', '.holder == "lane"', 'true', 'different holder'),
    ('class', '.class != "upgrade"', 'false', 'not upgrade'),
    ('attempted', '(.attempted | tostring) != "1"', 'false', 'not attempted'),
    ('schema', '.schema == "haynes.upgrade-claim/v1"', 'true', 'unknown schema'),
    ('acting state', '(.lane_state == "working" or .lane_state == "reverting")', 'true', 'unknown state'),
    ('result', '.result == "inprogress"', 'true', 'acting but failed result'),
    ('bound', '$now <= $deadline', 'true', 'expired lane'),
    ('clock skew', '$at <= ($now + $skew)', 'true', 'future claimed_at'),
    ('bound order', '$deadline >= $at', 'true', 'bound before claim'),
    ('bound length', '($deadline - $at) <= 21600', 'true', 'bound too long'),
    ('pr', '(.pr | epoch)', 'true', 'missing pr'),
    ('claimed_at format', '(.claimed_at | epoch)', 'true', 'leading zero claimed_at'),
    ('until format', '(.until | epoch)', 'true', 'leading zero bound'),
    ('ids element type', 'all(.[]; type == "string")', 'true', 'ids has unknown value'),
    ('triage result', 'index($r) != null', 'true', 'triage failed'),
    ('triage result type', '($r | type) == "string"', 'true', 'triage array subsequence result'),
    ('triage holder', '.holder == "triage"', 'true', 'different holder'),
    ('triage grace', '($now - $at) < $grace', 'true', 'triage grace spent'),
    ('triage clock skew', '$at <= ($now + $skew) and ($now - $at) < $grace', '($now - $at) < $grace', 'triage future time'),
    ('triage time format', '(.first_seen | epoch)', 'true', 'triage leading zero time'),
    ('whole storm', '($holds | all(.[]; . != null))', 'true', 'one storm member unheld'),
]


def killed(old, new, check):
    assert old in SOURCE
    mutant = SOURCE.replace(old, new, 1)
    syntax = subprocess.run(['bash', '-n'], input=mutant, text=True, capture_output=True)
    assert syntax.returncode == 0, syntax.stderr
    try:
        check(mutant)
    except AssertionError:
        return
    raise AssertionError('mutation survived')


for label, old, new, case_name in MUTATIONS:
    check = next(fn for name, fn in cases if name == case_name)
    test('mutation killed: ' + label, lambda o=old, n=new, c=check: killed(o, n, c))
test('mutation killed: failed read suppresses', lambda: killed(
    'held_by=""\n        if remediation_state=', 'held_by="fixture-claim holder=lane until=9999999999"\n        if remediation_state=',
    lambda s: expect(snapshot(LANE), source=s, extra=dict(TEST_READ='unreadable'))))
test('mutation killed: jq failure keeps partial hold', lambda: killed(
    ')" || held_by=""', ')" || held_by="fixture-claim holder=lane until=9999999999"',
    lambda s: expect(snapshot(LANE), source=s, extra=dict(TEST_JQ_FAIL='1'))))
test('mutation killed: old signature variable', lambda: killed('if REMEDIATE_SIG=', 'if ESCALATE_SIG=', stable_key))

print(f'responder-claim selftest: {passed} passed, {failed} failed', flush=True)
raise SystemExit(bool(failed))
