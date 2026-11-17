#!/usr/bin/env python3
"""Strict offline commands for test_responder_claim.py; unexpected calls fail."""
import json
import os
from pathlib import Path
import sys

command = Path(sys.argv[0]).name
args = sys.argv[1:]
world = Path(os.environ['TEST_WORLD'])
assert not any(k == 'KUBECONFIG' or k.startswith('KUBERNETES_') for k in os.environ)
assert os.environ['PATH'].split(':')[0] == os.environ['TEST_BIN']


def event(kind, **fields):
    with (world / 'events').open('a') as f:
        f.write(json.dumps(dict(kind=kind, **fields)) + '\n')


def output(value):
    print(json.dumps(value))


def unexpected(kind, value, traceback):
    event('unexpected', command=command)
    sys.__excepthook__(kind, value, traceback)


sys.excepthook = unexpected


if command == 'date':
    if args == ['-u', '+%s']:
        print(os.environ['TEST_NOW'])
        sys.exit(0)
    os.execv(os.environ['TEST_DATE'], [os.environ['TEST_DATE'], *args])
elif command == 'jq':
    if os.environ.get('TEST_JQ_FAIL') in ['1', 'partial'] and '--argjson' in args and 'unit' in args:
        if os.environ['TEST_JQ_FAIL'] == 'partial':
            print('fixture-claim holder=lane until=9999999999')
        sys.exit(7)
    os.execv(os.environ['TEST_JQ'], [os.environ['TEST_JQ'], *args])
elif command == 'git' and args[0] == 'clone':
    Path(args[-1]).mkdir(parents=True, exist_ok=True)
elif command == 'claude':
    event('diagnose')
    # A claim appears only after diagnosis; a premature read will miss it.
    if (world / 'after-diagnosis').exists():
        (world / 'claim').write_text((world / 'after-diagnosis').read_text())
    output({'result': 'ACTION: ' + os.environ.get('TEST_ACTION', 'urgent') + ' | CAUSE: fixture | FIX: fixture'})
elif command == 'curl':
    if any('/api/v2/alerts' in a for a in args):
        print((world / 'alerts').read_text())
    elif 'https://api.pushover.net/1/messages.json' in args:
        event('page')
        output({'status': 1})
    else:
        raise AssertionError('unexpected curl')
elif command == 'kubectl':
    event('kubectl', args=args)
    assert args[:2] == ['-n', 'upgrade-agent']
    verb, rest = args[2], args[3:]
    if verb == 'get':
        assert rest[0] in ['configmap', 'cm']
        name = rest[1]
        if name == 'upgrade-remediation-state':
            mode = os.environ.get('TEST_READ', 'ok')
            if mode in ['absent', 'unreadable', 'partial']:
                if mode == 'partial':
                    print((world / 'claim').read_text())
                sys.exit(1)
            print((world / 'claim').read_text())
        elif name == 'alert-responder-state':
            output(json.loads((world / 'responder-state').read_text()))
        elif name == 'upgrade-work-orders':
            output(json.loads((world / 'orders').read_text()))
        else:
            raise AssertionError('unexpected ConfigMap')
    elif verb == 'replace' and rest == ['-f', '-']:
        obj = json.load(sys.stdin)
        name = obj['metadata']['name']
        assert name in ['alert-responder-state', 'upgrade-work-orders']
        if name == 'upgrade-work-orders':
            if os.environ.get('TEST_ORDER_FAIL') == '1':
                sys.exit(1)
            event('order', keys=sorted(obj['data']))
        (world / ('orders' if name == 'upgrade-work-orders' else 'responder-state')).write_text(json.dumps(obj))
    else:
        raise AssertionError('unexpected kubectl mutation')
else:
    raise AssertionError('unexpected command: ' + command)
