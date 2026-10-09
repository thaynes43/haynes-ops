"""Phase-owned cached Git source proof. No hold/merge/Job operations."""
import gzip
from contextlib import contextmanager
import ctypes
import datetime as dt
import hashlib
import io
import json
import os
import re
import selectors
import signal
import stat
import tarfile
import time
import uuid
import urllib.parse
import subprocess

import window_contract as wc

SOURCE = ('flux-system', 'haynes-ops')
PARENTS = ('cluster', 'cluster-apps')
REQUEST = 'reconcile.fluxcd.io/requestedAt'
OWNER = 'issue825.haynesnetwork/cached-source-phase'
MAX_ARTIFACT = 8 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024


@contextmanager
def wall_guard(seconds):
    """Owning main-thread wall cap; nested guards never extend an outer cap."""
    wc.require(type(seconds) in (int, float) and 0 < seconds <= 30, 'wall guard cap')
    previous = signal.getsignal(signal.SIGALRM)
    timer = signal.getitimer(signal.ITIMER_REAL)
    start = time.monotonic()
    def expired(*_):
        raise TimeoutError('cached source wall budget expired')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, min(seconds, timer[0]) if timer[0] else seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if timer[0]:signal.setitimer(signal.ITIMER_REAL, max(.000001, timer[0] - (time.monotonic() - start)), timer[1])


def read_private(path, cap=1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        wc.require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                   and info.st_mode & 0o077 == 0 and info.st_nlink == 1
                   and info.st_size <= cap, 'private cached artifact mode/type/cap')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            raw = stream.read(cap + 1)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink')
        after, current = os.fstat(fd), os.stat(path, follow_symlinks=False)
        wc.require(len(raw) == info.st_size and all(getattr(info, k) == getattr(after, k) == getattr(current, k) for k in fields),
                   'private cached artifact changed')
        return raw
    finally:
        os.close(fd)


def write_private(path, raw):
    from pathlib import Path
    path = Path(path)
    wc.require(isinstance(raw, bytes) and 0 < len(raw) <= 1024 * 1024
               and path.parent.is_dir() and not path.parent.is_symlink()
               and path.parent.stat().st_mode & 0o077 == 0, 'private publication directory/cap')
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    stage = path.name + '.' + uuid.uuid4().hex + '.tmp'
    try:
        fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(parent, os.fsencode(stage), parent, os.fsencode(path.name), 1) != 0:
            raise OSError(ctypes.get_errno(), 'private no-replace publication refused')
        os.fsync(parent)
        wc.require(read_private(path) == raw, 'private publication identity changed')
    finally:
        try: os.unlink(stage, dir_fd=parent)
        except FileNotFoundError: pass
        os.close(parent)


def identity(row, kind, name, namespace='flux-system'):
    api = {'Pod': 'v1', 'GitRepository': 'source.toolkit.fluxcd.io/v1',
           'Kustomization': 'kustomize.toolkit.fluxcd.io/v1'}[kind]
    wc.require(row.get('kind') == kind and row.get('apiVersion') == api and row.get('metadata', {}).get('name') == name
               and row['metadata'].get('namespace') == namespace, 'native object identity changed')
    m = row['metadata']
    wc.require(isinstance(m.get('uid'), str) and bool(m['uid'])
               and isinstance(m.get('resourceVersion'), str) and bool(m['resourceVersion'])
               and not m.get('deletionTimestamp'), 'native object UID/RV missing or deleting')


def handled(before, after, token, kind, name, namespace='flux-system'):
    identity(before, kind, name, namespace); identity(after, kind, name, namespace)
    wc.require(before['metadata']['uid'] == after['metadata']['uid']
               and before['metadata']['resourceVersion'] != after['metadata']['resourceVersion'],
               'native object replaced or unchanged observation')
    wc.require(isinstance(token, str) and bool(token)
               and before['metadata'].get('annotations', {}).get(REQUEST) != token
               and before.get('status', {}).get('lastHandledReconcileAt') != token,
               'reconcile token must be fresh')
    wc.require(after['metadata'].get('annotations', {}).get(REQUEST) == token
               and after.get('status', {}).get('lastHandledReconcileAt') == token,
               'new handler did not acknowledge token')


def artifact_bytes(source, raw, contract, stop_sha):
    artifact = source.get('status', {}).get('artifact', {})
    wc.require(artifact.get('revision') == 'main@sha1:' + stop_sha, 'cached source is not exact Stop')
    wc.require(type(artifact.get('size')) is int and 0 < artifact['size'] <= MAX_ARTIFACT
               and len(raw) == artifact['size'], 'artifact byte size changed')
    wc.require(artifact.get('digest') == 'sha256:' + hashlib.sha256(raw).hexdigest(), 'artifact digest changed')
    # No filesystem extraction. Bound decompression before parsing any members.
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as zipped:
        expanded = zipped.read(MAX_EXPANDED + 1)
    wc.require(len(expanded) <= MAX_EXPANDED, 'artifact expanded cap')
    found = {}
    with tarfile.open(fileobj=io.BytesIO(expanded), mode='r:') as archive:
        for count, member in enumerate(archive):
            wc.require(count < 20000, 'artifact member cap')
            name = member.name.removeprefix('./')
            if name not in wc.PATHS:
                continue
            wc.require(member.isfile() and name not in found and 0 <= member.size <= 1024 * 1024,
                       'artifact manifest must be one bounded regular file')
            with archive.extractfile(member) as stream:
                found[name] = stream.read(member.size + 1)
            wc.require(len(found[name]) == member.size
                       and wc.sha(found[name]) == contract['manifests'][name]['stop_sha256'],
                       'artifact Stop manifest bytes changed')
    wc.require(set(found) == set(wc.PATHS), 'artifact six manifests missing')


def controller(receipt, actual):
    old = receipt['controller_pod']
    identity(old, 'Pod', old['metadata']['name'])
    identity(actual, 'Pod', old['metadata']['name'])
    wc.require(actual['metadata']['uid'] == old['metadata']['uid'] and actual['spec'] == old['spec'],
               'source-controller Pod changed')
    states = actual.get('status', {}).get('containerStatuses', [])
    old_states = old.get('status', {}).get('containerStatuses', [])
    wc.require(actual.get('status', {}).get('phase') == 'Running' and states and len(states) == len(old_states)
               and all(s.get('ready') is True and 'running' in s.get('state', {}) for s in states)
               and [(s['name'], s.get('imageID'), s.get('restartCount')) for s in states]
               == [(s['name'], s.get('imageID'), s.get('restartCount')) for s in old_states],
               'source-controller readiness/image/restart changed')


def validate(receipt, source, controller_pod, parents, holds, raw, contract, phase, require_holds=True):
    wc.require(receipt.get('schema') == 1 and receipt.get('phase_token') == phase,
               'cached receipt phase/schema changed')
    before, frozen = receipt['source_before'], receipt['source_after']
    handled(before, frozen, receipt['source_token'], 'GitRepository', SOURCE[1])
    wc.require(before['spec'].get('suspend', False) is False, 'source initially held by another phase')
    expected = dict(before['spec'], suspend=True)
    identity(source, 'GitRepository', SOURCE[1])
    wc.require(frozen['spec'] == expected and source['spec'] == expected
               and source['metadata']['uid'] == frozen['metadata']['uid']
               and source['metadata'].get('annotations', {}).get(OWNER) == phase
               and frozen['metadata'].get('annotations', {}).get(OWNER) == phase
               and source['metadata'].get('annotations', {}).get(REQUEST) == receipt['source_token']
               and source.get('status', {}).get('lastHandledReconcileAt') == receipt['source_token'],
               'source hold ownership/drain/spec changed')
    wc.require(source.get('status', {}).get('artifact') == frozen.get('status', {}).get('artifact'),
               'frozen artifact metadata changed')
    controller(receipt, controller_pod)
    wc.require(set(parents) == set(PARENTS) and set(receipt['parents']) == set(PARENTS), 'both parents required')
    for name in PARENTS:
        proof = receipt['parents'][name]
        handled(proof['before'], proof['after'], proof['token'], 'Kustomization', name)
        current = parents[name]
        identity(current, 'Kustomization', name)
        wc.require(current['metadata']['uid'] == proof['after']['metadata']['uid']
                   and current['spec'] == proof['after']['spec']
                   and current['spec'].get('suspend', False) is False,
                   'parent replaced or unexpectedly held')
        for row in (proof['after'], current):
            s = row.get('status', {})
            wc.require(s.get('lastHandledReconcileAt') == proof['token']
                       and s.get('observedGeneration') == row['metadata']['generation']
                       and s.get('lastAppliedRevision') == 'main@sha1:' + receipt['stop_main_sha']
                       and any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in s.get('conditions', [])),
                       'parent did not preserve exact frozen source reconcile')
    wc.require(set(receipt['holds']) == {ns + '/' + name for ns, name in wc.SCOPES}, 'four held proofs required')
    if require_holds:wc.require(set(holds) == set(wc.SCOPES), 'four actual holds required')
    for ns, name in wc.SCOPES:
        proof = receipt['holds'][ns + '/' + name]
        handled(proof['before'], proof['after'], proof['token'], 'Kustomization', name, ns)
        wc.require(proof['before']['spec'].get('suspend', False) is False
                   and proof['after']['spec'] == dict(proof['before']['spec'], suspend=True),
                   'historical application hold spec changed')
        if require_holds:
            current = holds[(ns, name)]
            identity(current, 'Kustomization', name, ns)
            wc.require(current['metadata']['uid'] == proof['after']['metadata']['uid']
                       and current['spec'] == proof['after']['spec'] and current['spec'].get('suspend') is True
                       and current['metadata'].get('annotations', {}).get(REQUEST) == proof['token']
                       and current.get('status', {}).get('lastHandledReconcileAt') == proof['token'],
                       'application hold did not drain/persist parent reconcile')
    artifact_bytes(source, raw, contract, receipt['stop_main_sha'])


def fetch(source, deadline=None):
    artifact = source.get('status', {}).get('artifact', {})
    url = urllib.parse.urlparse(artifact.get('url', ''))
    wc.require(url.scheme == 'http' and (url.hostname or '').removesuffix('.') in (
        'source-controller.flux-system.svc', 'source-controller.flux-system.svc.cluster.local')
        and url.port in (None, 80) and not url.username and not url.password
        and not url.query and not url.fragment
        and url.path.startswith('/gitrepository/flux-system/haynes-ops/'), 'artifact endpoint changed')
    budget = 4 if deadline is None else min(4, deadline - time.time())
    wc.require(budget > 0, 'artifact original deadline')
    # Curl's wall deadline bounds a trickling body too; no redirects or proxy.
    # No command response enters diagnostics. A fixed internal URL has no auth.
    until = time.monotonic() + budget
    process = subprocess.Popen(['curl', '--noproxy', '*', '--fail', '--silent',
                                '--max-time', str(budget), '--max-filesize', str(MAX_ARTIFACT),
                                '--url', artifact['url']], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    raw, errors = bytearray(), 0
    try:
        with selectors.DefaultSelector() as select:
            select.register(process.stdout, selectors.EVENT_READ, True)
            select.register(process.stderr, selectors.EVENT_READ, False)
            while select.get_map():
                left = until - time.monotonic()
                wc.require(left > 0, 'artifact wall deadline')
                for key, _ in select.select(min(.05, left)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        select.unregister(key.fileobj); continue
                    if key.data:
                        raw.extend(chunk); wc.require(len(raw) <= MAX_ARTIFACT, 'artifact byte cap')
                    else:
                        errors += len(chunk); wc.require(errors <= 1024 * 1024, 'artifact diagnostic cap')
            wc.require(process.wait(timeout=max(.001, until - time.monotonic())) == 0, 'artifact unavailable')
        raw = bytes(raw)
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=1)
        process.stdout.close(); process.stderr.close()
    wc.require(len(raw) <= MAX_ARTIFACT and (deadline is None or time.time() < deadline), 'artifact byte/deadline cap')
    return raw


def _check_live(receipt, get, contract, phase, holds=True, deadline=None):
    def snapshot():
        source = get('gitrepository', SOURCE[1], SOURCE[0])
        pod = get('pod', receipt['controller_pod']['metadata']['name'], SOURCE[0])
        parents = {n: get('kustomization', n, SOURCE[0]) for n in PARENTS}
        actual_holds = {(ns, n): get('kustomization', n, ns) for ns, n in wc.SCOPES} if holds else {}
        return source, pod, parents, actual_holds
    before = snapshot()
    raw = fetch(before[0], deadline)
    validate(receipt, *before, raw, contract, phase, holds)
    validate(receipt, *snapshot(), raw, contract, phase, holds)


def check_live(receipt, get, contract, phase, holds=True, deadline=None):
    budget = 5 if deadline is None else min(5, deadline - time.time())
    wc.require(budget > 0, 'cached source original deadline')
    with wall_guard(budget):
        _check_live(receipt, get, contract, phase, holds, deadline)


def activation(value, phase, receipt_sha, restore_merge_sha):
    wc.require(value.get('schema') == 1 and value.get('phase_token') == phase
               and value.get('cached_source_receipt_sha256') == receipt_sha
               and value.get('normal_inverse_merge_sha') == restore_merge_sha
               and value.get('armed_ready') is True, 'cached activation identity changed')
    origin = value.get('actuation_budget_started_at')
    wc.require(isinstance(origin, str), 'durable pre-release budget origin missing')
    parsed = dt.datetime.fromisoformat(origin.replace('Z', '+00:00'))
    wc.require(parsed.tzinfo is not None, 'budget origin timezone missing')
    return parsed.timestamp()


def normal_goal(normal):
    """Recovery follows reviewed current Normal intent, including newer images."""
    docs = {p: wc.yaml.safe_load(raw) for p, raw in normal.items()}
    wc.require(set(docs) == set(wc.PATHS), 'Normal requires six manifests')
    converter = docs[wc.PATHS[0]]
    wc.require(converter['spec'].get('suspend') is False
               and docs[wc.PATHS[1]]['spec'].get('suspend') is False, 'Normal schedules still held')
    env = {e['name']: e.get('value') for e in converter['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]['env']}
    wc.require(env.get('STRIP_SERIES_METADATA') == '0'
               and 'Daniel Silva/Ransom' in json.loads(env.get('LIBRARY_HOLD_FOLDERS_JSON', '[]')),
               'Normal strip or Ransom hold changed')
    deployments, helm_values, cron_images = {}, {}, {}
    def image(container):
        value = container['image']
        wc.require(isinstance(value.get('repository'), str) and bool(value['repository'])
                   and isinstance(value.get('tag'), str) and bool(value['tag']), 'Normal image missing')
        result = value['repository'] + ':' + value['tag']
        if value.get('digest'):
            wc.require(re.fullmatch('sha256:[0-9a-f]{64}', value['digest']), 'Normal image digest invalid')
            result += '@' + value['digest']
        return result
    for path, ns, release, ctrl_name, deploy in (
        (wc.PATHS[2], 'downloads', 'lazylibrarian', 'lazylibrarian', 'lazylibrarian'),
        (wc.PATHS[3], 'frontend', 'haynesnetwork', 'main', 'haynesnetwork-main'),
        (wc.PATHS[4], 'media', 'kavita', 'kavita', 'kavita'),
        (wc.PATHS[5], 'media', 'libretto', 'main', 'libretto')):
        values = docs[path]['spec']['values']; ctrls = values['controllers']
        # Libretto's controller has historically been named libretto, not main.
        if release == 'libretto':
            matches = [name for name, c in ctrls.items() if any('LAZYLIBRARIAN_URL' in x.get('env', {}) for x in c.get('containers', {}).values())]
            wc.require(len(matches) == 1, 'Normal acquisition controller ambiguous')
            ctrl_name = matches[0]
        ctrl = ctrls[ctrl_name]; replicas = ctrl.get('replicas', 1)
        wc.require(type(replicas) is int and replicas > 0, 'Normal replicas still stopped')
        deployments[(ns, deploy)] = dict(replicas=replicas,
            images=[(name, image(c)) for name, c in ctrl['containers'].items()])
        helm_values[(ns, release)] = values
        if release == 'libretto':
            urls = [c['env']['LAZYLIBRARIAN_URL'] for c in ctrl['containers'].values() if 'LAZYLIBRARIAN_URL' in c.get('env', {})]
            wc.require(urls == [wc.URL], 'Normal acquisition not restored')
        if release == 'haynesnetwork':
            for name in wc.BOOK_CONTROLLERS:
                book = ctrls[name]
                wc.require(book['cronjob'].get('suspend') is False, 'Normal book schedule still held')
                cron_images['haynesnetwork-' + name] = [image(c) for c in book['containers'].values()]
    return dict(deployments=deployments, helm_values=helm_values, cron_images=cron_images,
                converter_job_template=converter['spec']['jobTemplate'])


def includes(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and includes(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(includes(a, e) for a, e in zip(actual, expected))
    return type(actual) is type(expected) and actual == expected


def release_patch(source, uid, phase):
    identity(source, 'GitRepository', SOURCE[1])
    wc.require(source['metadata']['uid'] == uid, 'owned source replaced before resume')
    if source['spec'].get('suspend', False) is False:
        return None
    wc.require(source['metadata'].get('annotations', {}).get(OWNER) == phase, 'foreign source hold cannot be released')
    return [dict(op='test', path='/metadata/uid', value=uid),
            dict(op='test', path='/metadata/resourceVersion', value=source['metadata']['resourceVersion']),
            dict(op='test', path='/metadata/annotations/' + OWNER.replace('/', '~1'), value=phase),
            dict(op='test', path='/spec/suspend', value=True),
            dict(op='replace', path='/spec/suspend', value=False)]
