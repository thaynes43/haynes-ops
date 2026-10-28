"""Finite source checks for the portal's Alertmanager-only egress delta."""
import copy
import json
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
POLICY = 'kubernetes/main/apps/dev-env-system/portal/policy/app/networkpolicy.yaml'
BASE = 'bb23299082e045631dddc8a886baead4425dd9a6'
DNS = 'alertmanager-operated.observability.svc.cluster.local'
LABELS = {'io.kubernetes.pod.namespace': 'observability',
          'app.kubernetes.io/name': 'alertmanager',
          'app.kubernetes.io/instance': 'kube-prometheus-stack'}
RULE = {'toEndpoints': [{'matchLabels': LABELS}],
        'toPorts': [{'ports': [{'port': '9093', 'protocol': 'TCP'}],
                     'rules': {'http': [{'method': '^GET$', 'path': '^/api/v2/alerts$'}]}}]}


def parse(text):
    result = subprocess.run(['yq', '-o=json', '.', '-'], input=text, text=True,
                            capture_output=True, check=True)
    return json.loads(result.stdout)


class PortalPagesPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        original = subprocess.run(['git', 'show', BASE + ':' + POLICY], cwd=ROOT,
                                  text=True, capture_output=True, check=True).stdout
        cls.base = parse(original)
        cls.current = parse((ROOT / POLICY).read_text())

    def test_exact_delta_and_preservation(self):
        current = copy.deepcopy(self.current)
        self.assertEqual(current['kind'], 'CiliumNetworkPolicy')
        self.assertEqual(current['metadata']['namespace'], 'dev-agents')
        self.assertEqual(current['metadata']['name'], 'dev-env-v2-portal')
        self.assertEqual(current['spec']['endpointSelector']['matchLabels'],
                         {'app.kubernetes.io/name': 'dev-env-v2-portal'})
        self.assertEqual(current['spec']['enableDefaultDeny'], {'ingress': True, 'egress': True})
        self.assertEqual(current['spec']['ingress'], self.base['spec']['ingress'])
        egress = current['spec']['egress']
        self.assertEqual(egress.count(RULE), 1)
        egress.remove(RULE)
        dns_rules = egress[0]['toPorts'][0]['rules']['dns']
        self.assertEqual(dns_rules.count({'matchName': DNS}), 1)
        dns_rules.remove({'matchName': DNS})
        self.assertEqual(current, self.base, 'non-Alertmanager policy changed')

    def test_alertmanager_scope_and_anchored_request(self):
        rules = [rule for rule in self.current['spec']['egress'] if rule == RULE]
        self.assertEqual(len(rules), 1)
        selected = rules[0]['toEndpoints']
        self.assertEqual(selected, [{'matchLabels': LABELS}])
        port = rules[0]['toPorts'][0]
        self.assertEqual(port['ports'], [{'port': '9093', 'protocol': 'TCP'}])
        allowed = port['rules']['http']
        self.assertEqual(allowed, [{'method': '^GET$', 'path': '^/api/v2/alerts$'}])
        method, path = allowed[0]['method'], allowed[0]['path']
        for verb, target, want in [('GET', '/api/v2/alerts', True),
                                   ('HEAD', '/api/v2/alerts', False),
                                   ('GET', '/api/v2/status', False),
                                   ('GET', '/api/v2/alerts?x=1', False)]:
            self.assertEqual(bool(re.fullmatch(method, verb) and re.fullmatch(path, target)), want)


if __name__ == '__main__':
    unittest.main()
