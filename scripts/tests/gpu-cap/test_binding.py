"""Verify reviewed bytes and exercise real admission semantics, without a cluster/GPU.

PyYAML 6.0.3 is required. Set GPU_CAP_CHART and GPU_CAP_KYVERNO to the pinned
app-template chart archive and Kyverno CLI to run the policy-engine cases too.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / 'kubernetes/main/apps/ai/llama-server/app'
POLICIES = ROOT / 'kubernetes/main/apps/kyverno/policies/app'
EXCEPTION = POLICIES / 'exceptions/llama-card-b-power-cap.yaml'


class SourceBindingTests(unittest.TestCase):
    def test_inline_program_is_exactly_the_reviewed_file(self):
        values = yaml.safe_load((APP / 'helmrelease.yaml').read_text())['spec']['values']
        cap = values['controllers']['llama-server']['initContainers']['power-cap']
        self.assertEqual(cap['command'], ['/bin/sh', '-c'])
        self.assertEqual(cap['args'], [(APP / 'card-b-power-cap.sh').read_text()])

    def test_exception_pins_reviewed_program_bytes(self):
        conditions = yaml.safe_load(EXCEPTION.read_text())['spec']['conditions']['all']
        pins = [c for c in conditions if 'sha256(' in c['key']]
        self.assertEqual(len(pins), 1)
        self.assertEqual(pins[0]['operator'], 'Equals')
        self.assertEqual(pins[0]['value'], hashlib.sha256((APP / 'card-b-power-cap.sh').read_bytes()).hexdigest())

    def test_no_runtime_script_configmap(self):
        k = yaml.safe_load((APP / 'kustomization.yaml').read_text())
        self.assertFalse(k.get('configMapGenerator'))
        values = yaml.safe_load((APP / 'helmrelease.yaml').read_text())['spec']['values']
        for volume in values['persistence'].values():
            self.assertFalse(volume.get('globalMounts'), 'a global mount reaches the privileged setter')
            self.assertNotIn('power-cap', volume.get('advancedMounts', {}).get('llama-server', {}))


@unittest.skipUnless(os.environ.get('GPU_CAP_CHART') and os.environ.get('GPU_CAP_KYVERNO'),
                     'set GPU_CAP_CHART and GPU_CAP_KYVERNO for real offline policy evaluation')
class AdmissionBindingTests(unittest.TestCase):
    def test_rendered_policy_positive_and_negative_cases(self):
        values = yaml.safe_load((APP / 'helmrelease.yaml').read_text())['spec']['values']
        records = []
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source = directory / 'values.yaml'
            source.write_text(yaml.safe_dump(values))
            render = subprocess.run(['helm', 'template', 'llama-server', os.environ['GPU_CAP_CHART'],
                                     '--namespace', 'ai', '--values', str(source)],
                                    text=True, capture_output=True, check=True, timeout=60)
            dep = next(x for x in yaml.safe_load_all(render.stdout) if x and x['kind'] == 'Deployment')
            spec = dep['spec']['template']['spec']
            self.assertEqual([c['name'] for c in spec['initContainers']], ['power-cap', 'power-gate'])
            self.assertFalse(spec['initContainers'][0].get('volumeMounts'))
            self.assertFalse(spec['automountServiceAccountToken'])
            pod = {'apiVersion': 'v1', 'kind': 'Pod',
                   'metadata': {'name': 'llama-server-review', 'namespace': 'ai',
                                'labels': dep['spec']['template']['metadata']['labels']},
                   'spec': copy.deepcopy(spec)}
            rs = copy.deepcopy(dep)
            rs['kind'] = 'ReplicaSet'
            rs['metadata']['name'] = 'llama-server-review'
            rs['spec'].pop('strategy', None)
            cases = [('positive-pod', pod, True), ('positive-deployment', dep, True), ('positive-replicaset', rs, True)]
            for kind, resource in [('pod', pod), ('deployment', dep), ('replicaset', rs)]:
                changed = copy.deepcopy(resource)
                s = changed['spec'] if kind == 'pod' else changed['spec']['template']['spec']
                s['initContainers'][0]['args'][0] = s['initContainers'][0]['args'][0].replace('-pl 250', '-pl 350')
                cases.append(('unreviewed-program-' + kind, changed, False))
            for name in ('wrong-node', 'wrong-uuid', 'server-capability', 'second-init-capability',
                         'sidecar', 'wrong-image', 'host-network', 'privileged', 'extra-script-byte',
                         'extra-argument', 'missing-argument', 'different-command', 'mutable-mount',
                         'envfrom', 'path-override', 'wrong-node-field', 'wrong-driver-capabilities'):
                x = copy.deepcopy(pod)
                s = x['spec']
                cap = s['initContainers'][0]
                env = {e['name']: e for e in cap['env']}
                if name == 'wrong-node': s['nodeSelector']['kubernetes.io/hostname'] = 'talosw01'
                elif name == 'wrong-uuid': env['NVIDIA_VISIBLE_DEVICES']['value'] = 'GPU-other'
                elif name == 'server-capability': s['containers'][0]['securityContext']['capabilities']['add'] = ['SYS_ADMIN']
                elif name == 'second-init-capability': s['initContainers'][1]['securityContext']['capabilities']['add'] = ['SYS_ADMIN']
                elif name == 'sidecar': cap['restartPolicy'] = 'Always'
                elif name == 'wrong-image': cap['image'] = 'ghcr.io/ggml-org/llama.cpp:other'
                elif name == 'host-network': s['hostNetwork'] = True
                elif name == 'privileged': cap['securityContext']['privileged'] = True
                elif name == 'extra-script-byte': cap['args'][0] += '\n'
                elif name == 'extra-argument': cap['args'].append('unreviewed')
                elif name == 'missing-argument': cap.pop('args')
                elif name == 'different-command': cap['command'] = ['/bin/bash', '-c']
                elif name == 'mutable-mount':
                    s['volumes'].append({'name': 'unreviewed', 'configMap': {'name': 'unreviewed'}})
                    cap['volumeMounts'] = [{'name': 'unreviewed', 'mountPath': '/usr/bin', 'readOnly': True}]
                elif name == 'envfrom': cap['envFrom'] = [{'configMapRef': {'name': 'unreviewed'}}]
                elif name == 'path-override': cap['env'].append({'name': 'PATH', 'value': '/tmp'})
                elif name == 'wrong-node-field': env['POD_NODE_NAME']['valueFrom']['fieldRef']['fieldPath'] = 'metadata.name'
                elif name == 'wrong-driver-capabilities': env['NVIDIA_DRIVER_CAPABILITIES']['value'] = 'all'
                cases.append((name, x, False))
            for name, resource, allowed in cases:
                with self.subTest(case=name):
                    path = directory / (name + '.yaml')
                    path.write_text(yaml.safe_dump(resource))
                    result = subprocess.run([os.environ['GPU_CAP_KYVERNO'], 'apply',
                                             str(POLICIES / 'pod-security-baseline.yaml'), '--resource', str(path),
                                             '--exception', str(EXCEPTION), '--detailed-results'],
                                            env=dict(os.environ, GOMAXPROCS='2'), text=True,
                                            capture_output=True, timeout=30)
                    records.append({'case': name, 'expected_allowed': allowed, 'returncode': result.returncode,
                                    'stdout': result.stdout, 'stderr': result.stderr})
                    self.assertEqual(result.returncode, 0 if allowed else 1, result.stdout + result.stderr)
                    self.assertIn('error: 0', result.stdout, 'a policy engine error is not an expected rejection')
            if os.environ.get('GPU_CAP_EVIDENCE'):
                Path(os.environ['GPU_CAP_EVIDENCE']).write_text(json.dumps(records, indent=2) + '\n')
            print(f'Actual Kyverno engine: {len(records)} cases evaluated without cluster access')


if __name__ == '__main__':
    unittest.main()
