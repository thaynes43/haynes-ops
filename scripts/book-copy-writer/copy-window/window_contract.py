#!/usr/bin/env python3
"""Pure manifest and typed inventory checks; no API writes or runtime authority."""
import argparse
import collections
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess

import yaml

PREFIX = 'kubernetes/main/apps/'
PATHS = (
    PREFIX + 'downloads/lazylibrarian/app/epub-convert-cronjob.yaml',
    PREFIX + 'downloads/lazylibrarian/app/library-scan-cronjob.yaml',
    PREFIX + 'downloads/lazylibrarian/app/helmrelease.yaml',
    PREFIX + 'frontend/haynesnetwork/app/helmrelease.yaml',
    PREFIX + 'media/kavita/app/helmrelease.yaml',
    PREFIX + 'media/libretto/app/helmrelease.yaml',
)
URL = 'http://lazylibrarian.downloads.svc.cluster.local:5299'
SCOPES = (('frontend', 'haynesnetwork'), ('media', 'libretto'), ('downloads', 'lazylibrarian'), ('media', 'kavita'))
BOOK_CONTROLLERS = ('sync-books', 'sync-books-collections', 'sync-format-pairing', 'sync-goodreads')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def git(repo, *argv):
    return subprocess.check_output(['git', '-C', str(repo), *argv], timeout=10)


def blobs(repo, ref):
    return {path: git(repo, 'show', ref + ':' + path) for path in PATHS}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def expected_stop(normal):
    """The entire parsed tree must differ only at these exact nine fields."""
    docs = {p: yaml.safe_load(raw) for p, raw in normal.items()}
    conv = docs[PATHS[0]]
    require(conv['spec']['suspend'] is False, 'converter must be normal')
    env = {e['name']: e.get('value') for e in conv['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]['env']}
    require(env.get('STRIP_SERIES_METADATA') == '0', 'strip must remain off')
    require('Daniel Silva/Ransom' in json.loads(env['LIBRARY_HOLD_FOLDERS_JSON']), 'Ransom hold missing')
    require(docs[PATHS[1]]['spec']['suspend'] is False, 'library scan must be normal')
    for p, name in ((PATHS[2], 'lazylibrarian'), (PATHS[4], 'kavita')):
        require('replicas' not in docs[p]['spec']['values']['controllers'][name], 'normal replica default changed')
    controllers = docs[PATHS[3]]['spec']['values']['controllers']
    for name in BOOK_CONTROLLERS:
        require(controllers[name]['cronjob']['suspend'] is False, 'book schedule must be normal')
        require(len(controllers[name]['containers']) == 1, 'book container scope changed')
        image = next(iter(controllers[name]['containers'].values()))['image']
        require(image['tag'] == 'v0.110.5' and image['repository'] == 'ghcr.io/thaynes43/haynesnetwork', 'book capture image changed')
    main = controllers['main']['containers']['app']['image']
    require(main['tag'] == 'v0.110.5' and main['repository'] == 'ghcr.io/thaynes43/haynesnetwork', 'app image changed')
    libretto = docs[PATHS[5]]['spec']['values']['controllers']
    acquisition = [c['env'] for ctrl in libretto.values() for c in ctrl.get('containers', {}).values() if 'LAZYLIBRARIAN_URL' in c.get('env', {})]
    require(len(acquisition) == 1 and acquisition[0]['LAZYLIBRARIAN_URL'] == URL, 'normal acquisition changed')
    stopped = copy.deepcopy(docs)
    stopped[PATHS[0]]['spec']['suspend'] = True
    stopped[PATHS[1]]['spec']['suspend'] = True
    for p, name in ((PATHS[2], 'lazylibrarian'), (PATHS[4], 'kavita')):
        stopped[p]['spec']['values']['controllers'][name]['replicas'] = 0
    for name in BOOK_CONTROLLERS:
        stopped[PATHS[3]]['spec']['values']['controllers'][name]['cronjob']['suspend'] = True
    for ctrl in stopped[PATHS[5]]['spec']['values']['controllers'].values():
        for container in ctrl.get('containers', {}).values():
            if 'LAZYLIBRARIAN_URL' in container.get('env', {}):
                container['env']['LAZYLIBRARIAN_URL'] = ''
    return stopped


def stop_blobs(normal):
    out = dict(normal)
    for p in PATHS[:2]:
        raw = normal[p]
        require(raw.count(b'  suspend: false\n') == 1, 'ambiguous schedule field')
        out[p] = raw.replace(b'  suspend: false\n', b'  suspend: true\n')
    for p, name in ((PATHS[2], 'lazylibrarian'), (PATHS[4], 'kavita')):
        anchor = ('    controllers:\n      ' + name + ':\n').encode()
        require(normal[p].count(anchor) == 1, 'ambiguous controller')
        out[p] = normal[p].replace(anchor, anchor + b'        replicas: 0\n')
    lines = normal[PATHS[3]].splitlines(keepends=True)
    for name in BOOK_CONTROLLERS:
        starts = [i for i, line in enumerate(lines) if line == ('      ' + name + ':\n').encode()]
        require(len(starts) == 1, 'ambiguous book controller')
        start = starts[0]
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith(b'      ') and not lines[i].startswith(b'       ') and lines[i].strip()), len(lines))
        flags = [i for i in range(start, end) if lines[i].strip() == b'suspend: false']
        require(len(flags) == 1, 'ambiguous book schedule')
        lines[flags[0]] = lines[flags[0]].replace(b'false', b'true')
    out[PATHS[3]] = b''.join(lines)
    old = ('LAZYLIBRARIAN_URL: ' + URL).encode()
    require(normal[PATHS[5]].count(old) == 1, 'ambiguous acquisition URL')
    out[PATHS[5]] = normal[PATHS[5]].replace(old, b'LAZYLIBRARIAN_URL: ""')
    require({p: yaml.safe_load(raw) for p, raw in out.items()} == expected_stop(normal), 'unexpected Stop semantics')
    return out


def validate_pair(normal, stopped, contract):
    require(set(normal) == set(stopped) == set(PATHS), 'exact six manifests required')
    require(set(contract['manifests']) == set(PATHS), 'contract manifest scope changed')
    for p in PATHS:
        require(sha(normal[p]) == contract['manifests'][p]['normal_sha256'], 'normal blob changed')
        require(sha(stopped[p]) == contract['manifests'][p]['stop_sha256'], 'Stop blob changed')
    require({p: yaml.safe_load(raw) for p, raw in stopped.items()} == expected_stop(normal), 'Stop semantics changed')
    require(stopped == stop_blobs(normal), 'Stop must preserve all unrelated bytes')


def verify_git_pair(repo, base, head, contract, direction='inverse'):
    before, after = blobs(repo, base), blobs(repo, head)
    normal, stopped = (after, before) if direction == 'inverse' else (before, after)
    validate_pair(normal, stopped, contract)
    changed = git(repo, 'diff', '--name-only', base + '...' + head).decode().splitlines()
    require(set(changed) == set(PATHS) and len(changed) == 6, 'PR contains unrelated paths')
    patch = git(repo, 'diff', '--unified=0', base + '...' + head).decode().splitlines()
    plus = collections.Counter(l[1:].strip() for l in patch if l.startswith('+') and not l.startswith('+++'))
    minus = collections.Counter(l[1:].strip() for l in patch if l.startswith('-') and not l.startswith('---'))
    restore = collections.Counter(['suspend: false'] * 6 + ['LAZYLIBRARIAN_URL: ' + URL])
    stop = collections.Counter(['suspend: true'] * 6 + ['LAZYLIBRARIAN_URL: ""', 'replicas: 0', 'replicas: 0'])
    require((plus, minus) == ((restore, stop) if direction == 'inverse' else (stop, restore)), 'unexpected patch fields')


def typed_inventory(value, kind, api, namespace):
    """Raw List envelopes are strict before omitted child fields are normalized."""
    require(isinstance(value, dict) and value.get('kind') == kind + 'List' and value.get('apiVersion') == api, 'typed inventory required')
    meta = value.get('metadata')
    require(isinstance(meta, dict) and isinstance(meta.get('resourceVersion'), str) and bool(meta['resourceVersion']), 'inventory resourceVersion missing')
    require(meta.get('continue', '') == '' and meta.get('remainingItemCount', 0) == 0, 'inventory is continued/truncated')
    items = value.get('items')
    require(isinstance(items, list), 'inventory items missing')
    rows = []
    for child in items:
        require(isinstance(child, dict) and child.get('kind', kind) == kind and child.get('apiVersion', api) == api, 'wrong inventory child type')
        m = child.get('metadata', {})
        require((namespace is None or m.get('namespace') == namespace) and isinstance(m.get('name'), str) and bool(m['name']) and isinstance(m.get('uid'), str) and bool(m['uid']), 'inventory child identity missing')
        if kind in ('Pod', 'Job', 'PersistentVolumeClaim'):
            require(isinstance(m.get('namespace'), str) and bool(m['namespace']), 'namespaced child identity missing')
        row = copy.deepcopy(child)
        row.setdefault('kind', kind); row.setdefault('apiVersion', api)
        rows.append(row)
    require(len({(r['metadata'].get('namespace'), r['metadata']['name']) for r in rows}) == len(rows), 'duplicate inventory name')
    return rows


def validate_holds(proof, actual, now):
    """Prove a completed parent reconcile preserved all four same-UID holds."""
    require(proof.get('schema') == 1 and proof.get('workloads_normal') is True, 'normal held preflight missing')
    checked = dt.datetime.fromisoformat(proof['checked_at'].replace('Z', '+00:00')).timestamp()
    require(0 <= now - checked < 600, 'held parent preflight expired/future')
    before, after = proof['parent_before'], proof['parent_after']
    require(before['metadata']['uid'] == after['metadata']['uid'], 'parent Kustomization replaced')
    require(isinstance(after['metadata'].get('uid'), str) and bool(after['metadata']['uid']), 'actual parent UID missing')
    require(bool(before['metadata'].get('resourceVersion')) and bool(after['metadata'].get('resourceVersion')) and before['metadata']['resourceVersion'] != after['metadata']['resourceVersion'], 'fresh parent resourceVersion missing')
    require(after['metadata']['namespace'] == 'flux-system' and after['metadata']['name'] == 'cluster-apps', 'wrong parent')
    require(after['spec'].get('suspend', False) is False, 'parent itself must reconcile')
    request = after['metadata'].get('annotations', {}).get('reconcile.fluxcd.io/requestedAt')
    require(bool(request) and request != before['metadata'].get('annotations', {}).get('reconcile.fluxcd.io/requestedAt'), 'fresh parent reconcile request missing')
    status = after['status']
    require(status.get('lastHandledReconcileAt') == request and status.get('observedGeneration') == after['metadata']['generation'], 'parent did not complete requested reconcile')
    require(any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in status.get('conditions', [])), 'parent reconcile not Ready')
    require(status.get('lastAppliedRevision', '').rsplit(':', 1)[-1] == proof['normal_main_sha'], 'parent did not apply verified normal main')
    expected = set(SCOPES)
    require(set(actual) == expected, 'four exact actual holds required')
    for key in ('holds_before', 'holds_after'):
        rows = proof[key]
        require(isinstance(rows, list) and len(rows) == 4, 'incomplete held snapshots')
        by_key = {(r['metadata']['namespace'], r['metadata']['name']): r for r in rows}
        require(set(by_key) == expected, 'held snapshot scope changed')
        for identity, row in by_key.items():
            live = actual[identity]
            require(row['spec'].get('suspend') is True and live['spec'].get('suspend') is True, 'hold did not persist parent reconcile')
            require(row['metadata']['uid'] == live['metadata']['uid'], 'held Kustomization replaced')
            require(bool(row['metadata'].get('resourceVersion')) and bool(live['metadata'].get('resourceVersion')), 'held native resourceVersion missing')
            require(isinstance(row.get('status'), dict), 'actual held status missing')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo'); parser.add_argument('base'); parser.add_argument('head')
    parser.add_argument('--direction', choices=('stop', 'inverse'), default='inverse')
    parser.add_argument('--contract', default=str(Path(__file__).with_name('manifest-contract.json')))
    args = parser.parse_args()
    verify_git_pair(args.repo, args.base, args.head, json.loads(Path(args.contract).read_bytes()), args.direction)
    print('PASS exact six-file bytes, semantic fields and patch scope; no runtime action')


if __name__ == '__main__':
    main()
