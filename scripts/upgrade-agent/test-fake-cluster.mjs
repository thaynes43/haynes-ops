#!/usr/bin/env node
// test-fake-cluster.mjs — a fake kubectl and a fake curl for the story 554 selftests (no cluster,
// no network). The tests put two wrappers on PATH: `kubectl` -> `node test-fake-cluster.mjs
// kubectl "$@"` and `curl` -> `node test-fake-cluster.mjs curl "$@"`.
//
// kubectl: ConfigMaps live in FAKE_KUBE_STATE (a JSON file: { rv, configmaps: { "ns/name": obj } })
// with the API server's preconditions: a replace carrying a resourceVersion that is not the
// current one fails with Conflict, a create of an existing object with AlreadyExists, and every
// write bumps the version. Reads of other kinds come from FAKE_KUBE_FIXTURE (JSON: { flux,
// pods: { "ns/name": pod }, jobs: { ns: list }, gitrepository }). FAKE_KUBE_LOG gets one JSON line
// per call. FAKE_KUBE_HOOK (a JSON argv) runs once, before the first write, with FAKE_KUBE_HOOK
// unset for it: another actor writing "at the same moment". FAKE_KUBE_UNREADABLE=1 fails every
// ConfigMap read with a connection error.
//
// curl: a Prometheus query (--data-urlencode query=...) answers from FAKE_PROM (JSON: { pods:
// <query response>, default: <response> }); `vector(1)` answers one series; Pushover posts are
// appended to FAKE_CURL_LOG (one JSON array of the arguments per line) and answer status 1, or
// fail when FAKE_PUSHOVER_FAIL=1.
import { readFileSync, writeFileSync, appendFileSync, existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

const [tool, ...args] = process.argv.slice(2);
const env = process.env;
const readJson = (p, dflt) => { try { return JSON.parse(readFileSync(p, 'utf8')); } catch { return dflt; } };
const out = s => process.stdout.write(typeof s === 'string' ? s : JSON.stringify(s));
const fail = (msg, code = 1) => { process.stderr.write(`${msg}\n`); process.exit(code); };
const stdin = () => { try { return readFileSync(0, 'utf8'); } catch { return ''; } };

if (tool === 'curl') {
  const url = args.find(a => /^https?:\/\//.test(a)) || '';
  if (/pushover/.test(url)) {
    if (env.FAKE_CURL_LOG) appendFileSync(env.FAKE_CURL_LOG, `${JSON.stringify(args)}\n`);
    if (env.FAKE_PUSHOVER_FAIL === '1') fail('curl: (22) The requested URL returned error: 500', 22);
    out('{"status":1}');
    process.exit(0);
  }
  const qi = args.findIndex(a => a === '--data-urlencode');
  const q = qi >= 0 ? String(args[qi + 1]).replace(/^query=/, '') : '';
  const prom = readJson(env.FAKE_PROM || '', {});
  if (q === 'vector(1)') { out({ status: 'success', data: { resultType: 'vector', result: [{ metric: {}, value: [0, '1'] }] } }); process.exit(0); }
  if (/kube_pod_container_status_waiting_reason/.test(q)) { if (prom.pods === null) process.exit(22); out(prom.pods ?? { status: 'success', data: { result: [] } }); process.exit(0); }
  if (/ceph_health_status/.test(q)) { out({ status: 'success', data: { result: [{ metric: {}, value: [0, '0'] }] } }); process.exit(0); }
  out(prom.default ?? { status: 'success', data: { result: [] } });
  process.exit(0);
}

if (tool !== 'kubectl') fail(`fake: unknown tool ${tool}`, 2);
if (env.FAKE_KUBE_LOG) appendFileSync(env.FAKE_KUBE_LOG, `${JSON.stringify(args)}\n`);
const valued = new Set(['-n', '--namespace', '-o', '--output', '-l', '--selector', '--field-selector', '-f', '--filename', '--field-manager', '--request-timeout']);
let ns = 'default', o = null, fsel = null, file = null, all = false, dry = false;
const pos = [];
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === '-A' || a === '--all-namespaces') { all = true; continue; }
  if (a.startsWith('--dry-run')) { dry = true; continue; }
  if (/^--[a-z-]+=/.test(a)) { const [k, v] = [a.slice(0, a.indexOf('=')), a.slice(a.indexOf('=') + 1)]; if (k === '--field-selector') fsel = v; if (k === '--namespace') ns = v; if (k === '--output') o = v; continue; }
  if (valued.has(a)) { const v = args[++i]; if (a === '-n' || a === '--namespace') ns = v; else if (a === '-o' || a === '--output') o = v; else if (a === '--field-selector') fsel = v; else if (a === '-f' || a === '--filename') file = v; continue; }
  if (a.startsWith('-')) continue;
  pos.push(a);
}
const [verb, kind, name] = pos;
const stateFile = env.FAKE_KUBE_STATE;
const load = () => readJson(stateFile, { rv: 100, configmaps: {} });
const save = st => writeFileSync(stateFile, JSON.stringify(st));
const fx = readJson(env.FAKE_KUBE_FIXTURE || '', {});
const isCm = k => ['configmap', 'configmaps', 'cm'].includes(k);

if (verb === 'version') { out('{"serverVersion":{"major":"1","minor":"34"}}'); process.exit(0); }

if (verb === 'get') {
  if (isCm(kind)) {
    if (env.FAKE_KUBE_UNREADABLE === '1') fail('Unable to connect to the server: dial tcp 10.96.0.1:443: i/o timeout');
    const st = load();
    if (fsel) {
      const n = /metadata\.name=(.+)/.exec(fsel)?.[1];
      out({ apiVersion: 'v1', kind: 'List', items: Object.values(st.configmaps).filter(c => c.metadata.namespace === ns && (!n || c.metadata.name === n)) });
      process.exit(0);
    }
    const cm = st.configmaps[`${ns}/${name}`];
    if (!cm) fail(`Error from server (NotFound): configmaps "${name}" not found`);
    if (o === 'json') out(cm);
    else if (o && o.startsWith('jsonpath=')) { const k = /\.data\.([A-Za-z0-9_]+)/.exec(o)?.[1]; out(String(cm.data?.[k] ?? '')); }
    else out(`NAME DATA AGE\n${name} ${Object.keys(cm.data || {}).length} 1d\n`);
    process.exit(0);
  }
  if (/kustomizations|helmreleases/.test(kind || '')) { out(fx.flux ?? { items: [] }); process.exit(0); }
  if (kind === 'pod' || kind === 'pods') {
    if (name) { const p = fx.pods?.[`${ns}/${name}`]; if (!p) fail(`Error from server (NotFound): pods "${name}" not found`); if (o === 'json') out(p); else if (o?.startsWith('jsonpath=')) out(''); process.exit(0); }
    out({ items: Object.entries(fx.pods || {}).filter(([k]) => all || k.startsWith(`${ns}/`)).map(([, v]) => v) }); process.exit(0);
  }
  if (kind === 'jobs' || kind === 'job') { out(fx.jobs?.[ns] ?? { items: [] }); process.exit(0); }
  if (kind === 'gitrepository') { out(fx.gitrepository ?? { status: { conditions: [{ type: 'Ready', status: 'True' }] } }); process.exit(0); }
  out({ items: [] });
  process.exit(0);
}

// create configmap NAME --dry-run=client -o json: the skeleton
if (verb === 'create' && isCm(kind) && dry) {
  out({ apiVersion: 'v1', kind: 'ConfigMap', metadata: { name, namespace: ns } });
  process.exit(0);
}

if (['create', 'replace', 'apply'].includes(verb) && file === '-') {
  const body = JSON.parse(stdin() || 'null');
  if (!body || body.kind !== 'ConfigMap') fail('error: the fake handles ConfigMaps only', 2);
  // another actor writes first, once (the race), then this write meets the state it left
  if (env.FAKE_KUBE_HOOK && !(env.FAKE_KUBE_HOOK_ONCE && existsSync(env.FAKE_KUBE_HOOK_ONCE))) {
    if (env.FAKE_KUBE_HOOK_ONCE) writeFileSync(env.FAKE_KUBE_HOOK_ONCE, '1');
    const argv = JSON.parse(env.FAKE_KUBE_HOOK);
    const r = spawnSync(argv[0], argv.slice(1), { env: { ...env, FAKE_KUBE_HOOK: '' }, encoding: 'utf8' });
    if (env.FAKE_HOOK_OUT) writeFileSync(env.FAKE_HOOK_OUT, `${r.stdout || ''}${r.stderr || ''}`);
  }
  const st = load();
  const bns = body.metadata?.namespace || ns;
  const key = `${bns}/${body.metadata.name}`;
  const cur = st.configmaps[key];
  if (verb === 'create' && cur) fail(`Error from server (AlreadyExists): configmaps "${body.metadata.name}" already exists`);
  if (verb === 'replace') {
    if (!cur) fail(`Error from server (NotFound): configmaps "${body.metadata.name}" not found`);
    const rv = body.metadata?.resourceVersion;
    if (rv && rv !== cur.metadata.resourceVersion) fail(`Error from server (Conflict): Operation cannot be fulfilled on configmaps "${body.metadata.name}": the object has been modified; please apply your changes to the latest version and try again`);
  }
  st.rv += 1;
  let next;
  if (verb === 'apply') next = { ...(cur || { apiVersion: 'v1', kind: 'ConfigMap' }), metadata: { ...(cur?.metadata || {}), name: body.metadata.name, namespace: bns }, data: { ...(cur?.data || {}), ...(body.data || {}) } };
  else next = { apiVersion: 'v1', kind: 'ConfigMap', metadata: { ...(body.metadata || {}), namespace: bns }, data: body.data || {} };
  next.metadata.resourceVersion = String(st.rv);
  st.configmaps[key] = next;
  save(st);
  if (o === 'json') out(next);
  process.exit(0);
}
fail(`fake kubectl: unsupported: ${args.join(' ')}`, 2);
