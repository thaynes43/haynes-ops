#!/usr/bin/env python3
"""Offline chart and pinned-binary checks; no production logs or cluster access."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import copy

ROOT = Path(__file__).resolve().parents[2]
LOKI = Path(os.environ['LOKI_BIN'])
PROMTAIL = Path(os.environ['PROMTAIL_BIN'])
LOKI_CHART = Path(os.environ['LOKI_CHART'])
PROMTAIL_CHART = Path(os.environ['PROMTAIL_CHART'])
FIXTURES = ROOT / 'scripts/log-retention-policy/fixtures'


def run(args, data=None):
    p = subprocess.run([str(x) for x in args], input=data, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT)
    if p.returncode:
        raise AssertionError(f'{Path(str(args[0])).name} failed (exit {p.returncode})')
    return p.stdout


def values(path):
    return json.loads(run(['yq', '-o=json', '.spec.values', path]))


def render(chart, name, v, directory):
    f = directory / (name + '-values.json')
    f.write_text(json.dumps(v))
    out = run(['helm', 'template', name, chart, '-n', 'observability', '-f', f])
    f = directory / (name + '-render.yaml')
    f.write_text(out)
    return json.loads(run(['yq', 'eval-all', '-o=json', '[.]', f]))


def keyed(resources):
    return {(r['kind'], r['metadata']['name']): r for r in resources if r}


def main():
    assert '3.6.11' in run([LOKI, '-version'])
    assert '3.5.1' in run([PROMTAIL, '-version'])
    loki_values = values('kubernetes/main/apps/observability/loki/app/helmrelease.yaml')
    prom_values = values('kubernetes/main/apps/observability/promtail/app/helmrelease.yaml')
    assert loki_values['loki']['limits_config']['retention_period'] == '90d'
    assert not loki_values['loki'].get('runtimeConfig')
    stages = prom_values['config']['snippets']['pipelineStages']
    assert stages == [{'cri': {}}, {'labeldrop': ['retention_policy', 'retention_class']}]
    candidate = json.loads((FIXTURES / 'runtime-candidate.json').read_text())
    override = candidate['overrides']['fake']
    assert override['retention_period'] == '90d'
    rules = override['retention_stream']
    expected = {'protected_90': ('90d', 100), 'routine_30': ('30d', 10),
                'noisy_14': ('14d', 10), 'debug_3': ('3d', 10)}
    pattern = re.compile(r'^\{retention_policy="classified-v1", retention_class="([a-z0-9_]+)"\}$')
    assert len(rules) == len(expected)
    for rule in rules:
        m = pattern.fullmatch(rule['selector'])
        assert m and (rule['period'], rule['priority']) == expected.pop(m.group(1))
    assert not expected
    # Absence, unknown versions/classes and shadow labels cannot satisfy any exact selector.
    for policy, klass in [('', ''), ('classified-v1-shadow', 'debug_3'),
                          ('classified-v1', ''), ('classified-v1', 'unknown'),
                          ('other', 'routine_30')]:
        assert not any(r['selector'] == f'{{retention_policy="{policy}", retention_class="{klass}"}}' for r in rules)

    with tempfile.TemporaryDirectory(prefix='retention-test-') as temp:
        d = Path(temp)
        base = keyed(render(LOKI_CHART, 'loki', loki_values, d))
        loki_values['loki']['runtimeConfig'] = candidate
        staged = keyed(render(LOKI_CHART, 'loki', loki_values, d))
        assert base.keys() == staged.keys()
        changed = [key for key in base if base[key] != staged[key]]
        assert changed == [('ConfigMap', 'loki-runtime')], changed
        sts = base[('StatefulSet', 'loki')]
        assert any(c['image'].endswith('/loki:3.6.11') for c in sts['spec']['template']['spec']['containers'])
        base_config = base[('ConfigMap', 'loki')]['data']['config.yaml']
        parsed_base = json.loads(run(['yq', '-o=json', '.'], base_config))
        assert parsed_base['auth_enabled'] is False
        assert parsed_base['limits_config']['retention_period'] == '90d'
        assert not parsed_base['limits_config'].get('retention_stream')
        assert parsed_base['compactor']['retention_enabled'] is True
        assert json.loads(run(['yq', '-o=json', '.'], staged[('ConfigMap', 'loki-runtime')]['data']['runtime-config.yaml'])) == candidate
        config = d / 'loki.yaml'
        config.write_text(base_config)
        run([LOKI, '-verify-config', '-config.file=' + str(config)])
        # -verify-config validates base config only, not the runtime loader.
        old_values = copy.deepcopy(prom_values)
        del old_values['config']['snippets']
        old_prom = keyed(render(PROMTAIL_CHART, 'promtail', old_values, d))
        prom = keyed(render(PROMTAIL_CHART, 'promtail', prom_values, d))
        assert old_prom.keys() == prom.keys()
        assert {key for key in prom if old_prom[key] != prom[key]} == {
            ('DaemonSet', 'promtail'), ('Secret', 'promtail')}
        old_ds = copy.deepcopy(old_prom[('DaemonSet', 'promtail')])
        new_ds = copy.deepcopy(prom[('DaemonSet', 'promtail')])
        for obj in (old_ds, new_ds):
            del obj['spec']['template']['metadata']['annotations']['checksum/config']
        assert old_ds == new_ds
        ds = prom[('DaemonSet', 'promtail')]
        assert any(c['image'].endswith('/promtail:3.5.1') for c in ds['spec']['template']['spec']['containers'])
        config.write_text(prom[('Secret', 'promtail')]['stringData']['promtail.yaml'])
        run([PROMTAIL, '-check-syntax', '-config.file=' + str(config)])
        # Use the actual configured pipeline against hostile target labels and synthetic bodies.
        test_config = {'server': {'disable': True}, 'positions': {'filename': str(d / 'positions')},
                       'clients': [{'url': 'http://localhost:3100/loki/api/v1/push'}],
                       'scrape_configs': [{'job_name': 'test', 'static_configs': [{'targets': ['localhost'],
                           'labels': {'source': 'synthetic', 'retention_policy': 'classified-v1',
                                      'retention_class': 'debug_3'}}], 'pipeline_stages': stages}]}
        config.write_text(json.dumps(test_config))
        output = run([PROMTAIL, '-config.file=' + str(config), '-stdin', '-dry-run'],
                     (FIXTURES / 'records.txt').read_text())
        lines = [line for line in output.splitlines() if line.startswith('2026-01-01')]
        records = (FIXTURES / 'records.txt').read_text().splitlines()
        assert len(lines) == len(records) == 6
        for actual, original in zip(lines, records):
            timestamp, stream, flag, body = original.split(' ', 3)
            assert flag == 'F'
            prefix, actual_body = actual.split('\t', 1)
            assert prefix == timestamp.replace('Z', '+0000') + '{source="synthetic", stream="' + stream + '"}'
            assert actual_body == body
        # Prove that the hostile target labels are really present without the guard.
        test_config['scrape_configs'][0]['pipeline_stages'] = [{'cri': {}}]
        config.write_text(json.dumps(test_config))
        negative = run([PROMTAIL, '-config.file=' + str(config), '-stdin', '-dry-run'],
                       records[0] + '\n')
        emitted = [line for line in negative.splitlines() if line.startswith('2026-01-01')]
        assert len(emitted) == 1
        prefix = emitted[0].split('\t', 1)[0]
        assert 'retention_policy="classified-v1"' in prefix
        assert 'retention_class="debug_3"' in prefix
        # A broken CRI line must remain unclassified and retain its body.
        test_config['scrape_configs'][0]['pipeline_stages'] = stages
        config.write_text(json.dumps(test_config))
        malformed = 'malformed synthetic CRI record'
        output = run([PROMTAIL, '-config.file=' + str(config), '-stdin', '-dry-run'], malformed + '\n')
        entries = [line for line in output.splitlines() if '\t' in line]
        assert len(entries) == 1
        prefix, body = entries[0].rsplit('\t', 1)
        assert '{source="synthetic"}' in prefix and 'retention_' not in prefix
        assert body == malformed
    print('PASS: pinned base configs, reserved-label guard and inactive runtime render; runtime loading and classifier semantics remain unverified')


if __name__ == '__main__':
    main()
