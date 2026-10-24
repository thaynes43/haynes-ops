"""Run the real setter against an inert nvidia-smi double; never touch a GPU."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'kubernetes/main/apps/ai/llama-server/app/card-b-power-cap.sh'
GPU = 'GPU-18bf6eab-c76a-26ba-74c8-76093b705b8b'
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ['CAP_TEST_STATE'])
s = json.loads(p.read_text())
args = sys.argv[1:]
with Path(os.environ['CAP_TEST_LOG']).open('a') as f:
    f.write(json.dumps(args) + '\n')
if args[:2] != ['-i', 'GPU-18bf6eab-c76a-26ba-74c8-76093b705b8b']:
    sys.exit('unexpected GPU selection')
op = args[2]
if op == s.get('fail'):
    sys.exit(2)
if op.startswith('--query-gpu='):
    print(s.get('raw', f"{s['uuid']}, {s['mode']}, {s['configured']}, {s['enforced']}"))
elif op.startswith('--query-compute-apps='):
    if s.get('processes'): print(s['processes'])
elif op == '-pm':
    assert args[3:] == ['1']
    if not s.get('ignore_mode'): s['mode'] = 'Enabled'
elif op == '-pl':
    assert args[3:] == ['250']
    if not s.get('ignore_cap'): s.update(configured='250.00', enforced='250.00')
else:
    sys.exit('unexpected command')
p.write_text(json.dumps(s))
'''


class PowerCapTests(unittest.TestCase):
    def run_cap(self, state=None, env=None):
        values = dict(uuid=GPU, mode='Disabled', configured='350.00', enforced='350.00')
        values.update(state or {})
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'nvidia-smi').write_text(FAKE)
            (d / 'nvidia-smi').chmod(0o755)
            (d / 'state').write_text(json.dumps(values))
            environment = dict(os.environ, PATH=f"{d}:{os.environ['PATH']}",
                               CAP_TEST_STATE=str(d / 'state'), CAP_TEST_LOG=str(d / 'calls'),
                               POD_NODE_NAME='talosw04', NVIDIA_VISIBLE_DEVICES=GPU)
            environment.update(env or {})
            result = subprocess.run(['/bin/sh', str(SCRIPT)], env=environment,
                                    text=True, capture_output=True, timeout=10)
            calls = [json.loads(x) for x in (d / 'calls').read_text().splitlines()] if (d / 'calls').exists() else []
            return result, calls, json.loads((d / 'state').read_text())

    def assert_no_writes(self, calls):
        self.assertFalse([c for c in calls if c[2] in ('-pm', '-pl')], calls)

    def test_boot_default_restored_and_verified(self):
        result, calls, state = self.run_cap()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c[2:] for c in calls if c[2].startswith('-p')], [['-pm', '1'], ['-pl', '250']])
        self.assertEqual(state['enforced'], '250.00')
        self.assertIn('verified 250.00 W', result.stdout)

    def test_repeated_startup_is_idempotent(self):
        result, calls, _ = self.run_cap(dict(mode='Enabled', configured='250', enforced='250'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_writes(calls)

    def test_lower_maintenance_cap_is_never_raised(self):
        result, calls, state = self.run_cap(dict(configured='150', enforced='150'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state['configured'], '150')
        self.assertFalse([c for c in calls if c[2] == '-pl'])

    def test_active_or_unreadable_compute_list_refuses_all_writes(self):
        for processes in ('12345', 'N/A', 'Not Supported'):
            with self.subTest(processes=processes):
                result, calls, _ = self.run_cap(dict(processes=processes))
                self.assertNotEqual(result.returncode, 0)
                self.assert_no_writes(calls)

    def test_runtime_void_rewrite_still_caps_the_pinned_gpu(self):
        result, calls, state = self.run_cap(env={'NVIDIA_VISIBLE_DEVICES': 'void'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c[2:] for c in calls if c[2].startswith('-p')], [['-pm', '1'], ['-pl', '250']])
        self.assertEqual(state['enforced'], '250.00')

    def test_wrong_node_or_visible_gpu_never_queries_hardware(self):
        for env in ({'POD_NODE_NAME': 'talosw01'},
                    {'NVIDIA_VISIBLE_DEVICES': 'all'},
                    {'NVIDIA_VISIBLE_DEVICES': 'none'},
                    {'NVIDIA_VISIBLE_DEVICES': '0'},
                    {'NVIDIA_VISIBLE_DEVICES': ''},
                    {'NVIDIA_VISIBLE_DEVICES': 'void '}):
            result, calls, _ = self.run_cap(env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(calls, [])

    def test_unknown_identity_or_invalid_reading_refuses_writes(self):
        states = [dict(uuid='GPU-other'), dict(mode='N/A'), dict(raw=''),
                  dict(raw=f'{GPU}, Enabled, 250, 250\n{GPU}, Enabled, 250, 250')]
        states += [dict(enforced=v) for v in ('N/A', 'NaN', 'inf', '-1', '0', '250 W')]
        states += [dict(configured='200', enforced='350')]
        for state in states:
            with self.subTest(state=state):
                result, calls, _ = self.run_cap(state)
                self.assertNotEqual(result.returncode, 0)
                self.assert_no_writes(calls)

    def test_failed_identity_or_process_query_refuses_writes(self):
        for op in ('--query-gpu=uuid,persistence_mode,power.limit,enforced.power.limit', '--query-compute-apps=pid'):
            result, calls, _ = self.run_cap(dict(fail=op))
            self.assertNotEqual(result.returncode, 0)
            self.assert_no_writes(calls)

    def test_write_failures_keep_startup_blocked(self):
        for op in ('-pm', '-pl'):
            result, calls, _ = self.run_cap(dict(fail=op))
            self.assertNotEqual(result.returncode, 0)
            if op == '-pm':
                self.assertFalse([c for c in calls if c[2] == '-pl'])

    def test_successful_command_with_wrong_readback_still_blocks(self):
        for state in (dict(ignore_cap=True), dict(ignore_mode=True)):
            result, _, _ = self.run_cap(state)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('verified', result.stdout)

    def test_decimal_over_limit_is_reduced_not_truncated(self):
        result, calls, _ = self.run_cap(dict(configured='250.9', enforced='250.9'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len([c for c in calls if c[2] == '-pl']), 1)


if __name__ == '__main__':
    unittest.main()
