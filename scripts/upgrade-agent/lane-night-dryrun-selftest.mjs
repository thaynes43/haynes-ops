#!/usr/bin/env node
// Self-test for the upgrade-night tick (OP512 step 1): the DRY-RUN GUARANTEE and the
// gates that must FAIL CLOSED.
//
// WHY THIS EXISTS
// ---------------
// Step 1 ships the nightly lane as a CronJob that must not be able to change anything
// except its own state ConfigMap, and whose gates must never pass on a fact it could
// not read. This test fails if a mutating gh, git, kubectl or flux verb can appear on
// the dry-run path, or if a gate opens on a failed read:
//   1. statically: the two shipped files start processes at exactly one call site
//      each (runReadOnly in lane-classify.mjs, writeLaneState in run-night.mjs),
//      fetch() is GET-only, and no mutating verb is spelled anywhere in them;
//   2. the read-only allowlist (isReadOnlyCommand) refuses every mutating shape;
//   3. the gate helpers against live-captured backup sources (per-source freshness
//      with the real schedules), the scoped Flux baseline and the state evidence;
//   4. dynamically: the real tick runs against stub gh/kubectl/git/flux/curl binaries
//      and a stub Prometheus. The only non-read is one server-side apply of ConfigMap
//      upgrade-agent/upgrade-lane-state; every failed read is a named blocker; pause
//      stops the tick; any mode but dryrun is refused before a single command runs.
//
// Usage: node scripts/upgrade-agent/lane-night-dryrun-selftest.mjs
// Requires: node 24. No network (one loopback listener), no cluster access.
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../..');
const RES = join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/resources');
const L = await import(join(RES, 'lane-classify.mjs'));
const N = await import(join(RES, 'run-night.mjs'));
const fx = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/lane-queue-20261109.json'), 'utf8'));
const bk = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/lane-backup-sources-20261110.json'), 'utf8'));
const rebase = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/lane-rebase-pairs-20261110.json'), 'utf8'));
const SSA = ['apply', '--server-side', '--field-manager=upgrade-night', '--force-conflicts', '-f', '-'];

let fails = 0, passes = 0;
const ok = (cond, name, detail = '') => { if (cond) passes++; else { fails++; console.log(`FAIL ${name}${detail ? `\n     ${detail}` : ''}`); } };
const T = s => Date.parse(s);
const H = 3600e3;

// ── 1. static ───────────────────────────────────────────────────────────────
const strip = s => s.replace(/^\s*\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '');
const night = strip(readFileSync(join(RES, 'run-night.mjs'), 'utf8'));
const classify = strip(readFileSync(join(RES, 'lane-classify.mjs'), 'utf8'));
const count = (s, re) => (s.match(re) || []).length;
for (const [name, src] of [['run-night.mjs', night], ['lane-classify.mjs', classify]]) {
  ok(count(src, /\bspawnSync\s*\(/g) === 1, `${name}: exactly one spawnSync call site`, `found ${count(src, /\bspawnSync\s*\(/g)}`);
  ok(!/(?<![.\w])(execSync|execFileSync|execFile|exec|spawn|fork)\s*\(/.test(src.replace(/spawnSync\s*\(/g, '')), `${name}: no other process API`);
  ok(!/from 'node:child_process'/.test(src) || /import \{ spawnSync \} from 'node:child_process'/.test(src), `${name}: child_process imports spawnSync only`);
  ok(!/['"](git|flux|curl|helm)['"]/.test(src), `${name}: never runs git, flux, curl or helm`);
}
ok(/runReadOnly[\s\S]*?if \(!isReadOnlyCommand\(cmd, argv\)\) throw[\s\S]*?spawnSync\(cmd, argv/.test(classify), 'lane-classify.mjs: the spawn sits behind isReadOnlyCommand');
ok(/function writeLaneState[\s\S]*?m\.name !== STATE\.name[\s\S]*?spawnSync\('kubectl', \['apply', '--server-side', '--field-manager=upgrade-night', '--force-conflicts', '-f', '-'\]/.test(night), "run-night.mjs: the spawn is writeLaneState's server-side apply, after the name check");
ok(/const STATE = \{ name: 'upgrade-lane-state', namespace: 'upgrade-agent' \}/.test(night), 'run-night.mjs: the state object name is a constant');
ok(count(night, /\bfetch\s*\(/g) === 1 && /fetch\(url, \{ method: 'GET'/.test(night), 'run-night.mjs: one fetch, GET only');
ok(count(classify, /\bfetch\s*\(/g) === 0, 'lane-classify.mjs: no fetch');
// 'patch' is left out: it is also a Renovate update type in lane-classify.mjs; kubectl
// patch and gh api -X PATCH are covered by the allowlist cases in section 2.
const MUTATING = /['"](merge|comment|edit|close|reopen|review|ready|lock|unlock|push|commit|checkout|switch|branch|tag|reset|rebase|annotate|label|replace|delete|create|scale|rollout|exec|cp|run|set|taint|cordon|drain|reconcile|suspend|resume|POST|PUT|DELETE)['"]/g;
ok(!MUTATING.test(classify), 'lane-classify.mjs: no mutating verb literal', String(classify.match(MUTATING)));
ok(!(night.match(MUTATING) || []).length, 'run-night.mjs: no mutating verb literal', String(night.match(MUTATING)));
ok(count(night, /['"]apply['"]/g) === 1, "run-night.mjs: 'apply' appears once (writeLaneState)");
ok(/if \(MODE !== 'dryrun'\) \{[\s\S]*?process\.exit\(3\)/.test(night) && night.indexOf("MODE !== 'dryrun'") < night.indexOf('readFileSync(tokenFile'), 'run-night.mjs: the mode refusal comes before any read');
ok(!/900 \* 1024/.test(night) && /MAX_STATE_BYTES = 768 \* 1024/.test(night), 'run-night.mjs: state size budget is 768 KiB under the 1 MiB object limit');

// ── 2. the allowlist ──────────────────────────────────────────────────────────
const refused = [
  ['gh', ['pr', 'merge', '1', '--squash']], ['gh', ['pr', 'comment', '1', '-b', 'x']], ['gh', ['pr', 'edit', '1', '--add-label', 'x']],
  ['gh', ['pr', 'close', '1']], ['gh', ['pr', 'review', '1', '--approve']], ['gh', ['pr', 'ready', '1']], ['gh', ['pr', 'create']],
  ['gh', ['issue', 'comment', '1', '-b', 'x']], ['gh', ['issue', 'edit', '1']], ['gh', ['label', 'create', 'x']], ['gh', ['repo', 'edit']],
  ['gh', ['api', '-X', 'POST', 'repos/x/y/issues/1/comments']], ['gh', ['api', '--method', 'PATCH', 'repos/x/y/pulls/1']],
  ['gh', ['api', '--method=PUT', 'repos/x/y/pulls/1/merge']], ['gh', ['api', '-XDELETE', 'repos/x/y/git/refs/heads/z']],
  ['gh', ['api', 'repos/x/y/issues/1/comments', '-f', 'body=x']], ['gh', ['api', 'repos/x/y/issues/1/labels', '-F', 'labels[]=x']],
  ['gh', ['api', 'repos/x/y/pulls/1', '--input', 'f.json']], ['gh', ['api', 'graphql', '-f', 'query=mutation{}']], ['gh', ['api', 'graphql']],
  ['gh', ['workflow', 'run', 'x']], ['gh', ['run', 'rerun', '1']], ['gh', ['auth', 'login']],
  ['git', ['push', 'origin', 'HEAD']], ['git', ['commit', '-m', 'x']], ['git', ['checkout', '-b', 'x']], ['git', ['log']],
  ['kubectl', ['apply', '-f', '-']], ['kubectl', SSA], ['kubectl', ['-n', 'x', 'patch', 'cm', 'y']], ['kubectl', ['create', 'configmap', 'x']],
  ['kubectl', ['delete', 'pod', 'x']], ['kubectl', ['annotate', 'es', 'x', 'force-sync=1']], ['kubectl', ['replace', '-f', '-']],
  ['kubectl', ['rollout', 'restart', 'deploy/x']], ['kubectl', ['exec', 'x', '--', 'sh']], ['kubectl', ['-n', 'get', 'apply']],
  ['kubectl', ['label', 'pod', 'x', 'a=b']], ['kubectl', ['edit', 'cm', 'x']], ['kubectl', ['scale', 'deploy/x', '--replicas=0']],
  ['flux', ['reconcile', 'ks', 'x']], ['flux', ['get', 'ks']], ['curl', ['-X', 'POST', 'http://x']], ['sh', ['-c', 'gh pr merge 1']],
];
for (const [c, a] of refused) ok(!L.isReadOnlyCommand(c, a), `allowlist refuses: ${c} ${a.join(' ')}`);
const allowed = [
  ['gh', ['pr', 'list', '-R', 'x/y', '--state', 'open']], ['gh', ['pr', 'view', '1', '-R', 'x/y', '--json', 'files']],
  ['gh', ['api', '--paginate', '--slurp', 'repos/x/y/issues/1/comments?per_page=100']], ['gh', ['api', 'repos/x/y/contents/.renovate/holds.json5?ref=main']],
  ['gh', ['api', '-X', 'GET', 'repos/x/y/pulls']], ['kubectl', ['get', 'configmaps', '-n', 'upgrade-agent', '-o', 'json']],
  ['kubectl', ['-n', 'x', 'get', 'pods']], ['kubectl', ['get', 'kustomizations.kustomize.toolkit.fluxcd.io', '-A', '-o', 'json']],
];
for (const [c, a] of allowed) ok(L.isReadOnlyCommand(c, a), `allowlist allows: ${c} ${a.join(' ')}`);
let threw = false;
try { L.runReadOnly('gh', ['pr', 'merge', '1']); } catch (e) { threw = /refused non-read command/.test(e.message); }
ok(threw, 'runReadOnly throws before running a refused command');

// ── 3. gate helpers ───────────────────────────────────────────────────────────
{
  const p = (e, h) => ok(N.cronPeriodMs(e) === h * H, `cron period ${e} = ${h}h`, String(N.cronPeriodMs(e) / H));
  p('0 0 * * *', 24); p('0 0 * * 0', 168); p('30 2 * * *', 24); p('@daily', 24); p('0 0 2 * * *', 24); p('0 */4 * * *', 4);
  ok(N.cronPeriodMs('every day') === null && N.cronPeriodMs('0 0 * JAN *') === null, 'unparseable cron -> null');

  const rs = n => structuredClone(bk.replicationsources.find(r => r.metadata.name === n));
  const D = 24 * H;
  // VolSync `0 0 * * *` runs at 05:00Z here (live: lastSyncTime 05:00:52Z, nextSyncTime 05:00:00Z)
  const mq = rs('mosquitto-aws');
  ok(mq.spec.trigger.schedule === '0 0 * * *' && mq.status.nextSyncTime.endsWith('T05:00:00Z') && /T05:00:\d\dZ$/.test(mq.status.lastSyncTime), 'live: VolSync 0 0 * * * is evaluated as 05:00Z');
  ok(N.volsyncFresh(mq, T('2026-11-10T00:55:00Z'), D).pass, 'mosquitto: the evening after its 05:00Z sync is fresh');
  ok(!N.volsyncFresh(mq, T('2026-11-10T07:00:00Z'), D).pass, "mosquitto: tonight's 05:00Z run with no new sync is not fresh");
  const mq2 = structuredClone(mq); mq2.status.lastSyncTime = '2026-11-10T05:00:49Z'; mq2.status.nextSyncTime = '2026-11-11T05:00:00Z';
  ok(N.volsyncFresh(mq2, T('2026-11-10T07:00:00Z'), D).pass, "mosquitto: after tonight's sync it is fresh");
  const mq3 = structuredClone(mq2); mq3.status.latestMoverStatus.result = 'Failed';
  ok(!N.volsyncFresh(mq3, T('2026-11-10T07:00:00Z'), D).pass, 'a failed last sync is not fresh');
  const px = rs('plex-aws');
  ok(px.spec.trigger.schedule === '0 0 * * 0', 'live: plex-aws is weekly');
  ok(!N.volsyncFresh(px, T('2026-11-10T07:00:00Z'), D).pass, 'weekly plex-aws is blocked on a weeknight');
  ok(N.volsyncFresh(px, T('2026-11-08T07:00:00Z'), D).pass, 'weekly plex-aws passes on its own night');
  const oa = rs('openproject-assets-aws');
  ok(oa.spec.trigger.schedule === '30 2 * * *', 'live: openproject-assets-aws is 30 2 * * *');
  ok(N.volsyncFresh(oa, T('2026-11-10T07:00:00Z'), D).pass, 'openproject-assets: before its 07:30Z run, the last one is fresh');
  ok(!N.volsyncFresh(oa, T('2026-11-10T07:45:00Z'), D).pass, 'openproject-assets: after its 07:30Z run with no new sync is not fresh');
  const nosched = structuredClone(mq); delete nosched.spec.trigger.schedule;
  ok(!N.volsyncFresh(nosched, T('2026-11-10T00:55:00Z'), D).pass, 'a ReplicationSource with no schedule is not fresh');

  const sb = bk.scheduledbackups, b = bk.backups;
  const pg = b.find(x => x.metadata.name.startsWith('postgres-2026'));
  ok(pg.metadata.creationTimestamp === '2026-11-09T05:00:00Z' && pg.status.stoppedAt.startsWith('2026-11-09T00:0'), 'live: postgres16 Backup created 05:00Z reports stoppedAt 00:0xZ (local time stamped Z)');
  ok(sb.find(s => s.spec.cluster.name === 'postgres16').spec.schedule === '@daily', 'live: postgres16 is @daily');
  ok(N.cnpgFresh('database/postgres16', sb, b, T('2026-11-09T07:00:00Z'), D).pass, 'postgres16 (@daily, stoppedAt mislabelled 00:01Z) is fresh after its 05:00Z run');
  ok(N.cnpgFresh('database/postgres16-pgvecto', sb, b, T('2026-11-09T07:00:00Z'), D).pass, 'postgres16-pgvecto (@daily, stoppedAt 05:02Z) is fresh');
  ok(N.cnpgFresh('downloads/ytdrivarr-postgres16', sb, b, T('2026-11-09T07:00:00Z'), D).pass, 'ytdrivarr-postgres16 (@daily, stoppedAt 00:00Z) is fresh');
  ok(sb.find(s => s.spec.cluster.name === 'openproject-db').spec.schedule === '0 0 2 * * *', 'live: openproject-db is 0 0 2 * * *');
  ok(N.cnpgFresh('frontend/openproject-db', sb, b, T('2026-11-09T08:00:00Z'), D).pass, 'openproject-db is fresh after its 07:00Z run');
  const sb2 = structuredClone(sb); sb2.find(s => s.spec.cluster.name === 'postgres16').status.lastScheduleTime = '2026-11-10T05:00:00Z';
  ok(!N.cnpgFresh('database/postgres16', sb2, b, T('2026-11-10T06:00:00Z'), D).pass, "postgres16: tonight's scheduled run with no completed Backup is not fresh");
  ok(!N.cnpgFresh('database/postgres16', sb, b, T('2026-11-10T06:00:00Z'), D).pass, 'postgres16: a Backup older than 24h is not fresh');
  ok(!N.cnpgFresh('database/nope', sb, b, T('2026-11-09T07:00:00Z'), D).pass, 'a cluster with no ScheduledBackup is not fresh');

  const ready = (o, v) => ({ ...o, status: { conditions: [{ type: 'Ready', status: v ? 'True' : 'False' }] } });
  const ks = (ns, name, tns, path, dependsOn = [], v = true) => ready({ metadata: { namespace: ns, name }, spec: { targetNamespace: tns, path, dependsOn } }, v);
  const K = { items: [
    ks('database', 'mosquitto', 'database', './kubernetes/main/apps/database/mosquitto/app', [{ name: 'volsync', namespace: 'volsync-system' }]),
    ks('volsync-system', 'volsync', 'volsync-system', './kubernetes/main/apps/volsync-system/volsync/app'),
    ks('dev-env-system', 'dev-env-h7-metadata-audit', 'dev-env-system', './kubernetes/main/apps/dev-env-system/h7-metadata-audit/app', [], false),
  ] };
  const HR = { items: [ready({ metadata: { namespace: 'database', name: 'mosquitto' }, spec: {} }, true)] };
  const mine = N.kustomizationsFor('kubernetes/main/apps/database/mosquitto', K);
  ok(N.fluxScope(mine, K, HR).blocking.length === 0, 'baseline: an unrelated NotReady Kustomization (H7 audit) does not block');
  const K2 = structuredClone(K); K2.items[1].status.conditions[0].status = 'False';
  ok(N.fluxScope(mine, K2, HR).blocking.includes('Kustomization volsync-system/volsync'), 'baseline: a NotReady Kustomization in the dependsOn chain blocks');
  const HR2 = structuredClone(HR); HR2.items[0].status.conditions[0].status = 'False';
  ok(N.fluxScope(mine, K, HR2).blocking.includes('HelmRelease database/mosquitto'), 'baseline: a NotReady HelmRelease in the target namespace blocks');
  const K3 = structuredClone(K); K3.items.push(ks('database', 'other', 'database', './kubernetes/main/apps/database/other/app', [], false));
  ok(N.fluxScope(mine, K3, HR).blocking.includes('Kustomization database/other'), 'baseline: a NotReady Kustomization targeting the same namespace blocks');
  const K4 = structuredClone(K); K4.items[0].status.conditions[0].status = 'False';
  ok(N.fluxScope(mine, K4, HR).blocking.includes('Kustomization database/mosquitto'), 'baseline: the target itself NotReady blocks');

  const flux = (ks, ns = 'database') => ({ 'kustomize.toolkit.fluxcd.io/name': ks, 'kustomize.toolkit.fluxcd.io/namespace': ns });
  const pod = (name, labels, claim) => ({ metadata: { namespace: 'database', name, labels }, spec: { volumes: claim ? [{ name: 'data', persistentVolumeClaim: { claimName: claim } }] : [{ name: 'tmp', emptyDir: {} }] } });
  const HRo = { items: [{ metadata: { namespace: 'database', name: 'mosquitto', labels: flux('mosquitto') } }] };
  const rsOther = { items: [{ metadata: { namespace: 'database', name: 'mqtt-backup', labels: flux('something-else') } }] };
  const rsLabelled = { items: [{ metadata: { namespace: 'database', name: 'mqtt-backup', labels: flux('mosquitto') } }] };
  const pods = { items: [pod('mosquitto-0', { 'app.kubernetes.io/instance': 'mosquitto' }, 'data-mosquitto-0')] };
  const e1 = N.stateEvidence(mine[0], HRo, rsOther, pods, { items: [] });
  ok(e1.sources.length === 0 && e1.evidence.some(x => /mounts data-mosquitto-0/.test(x)), 'state: a PVC-mounting app whose ReplicationSource is not applied by its Kustomization has state and no source', JSON.stringify(e1));
  const e2 = N.stateEvidence(mine[0], HRo, rsLabelled, pods, { items: [] });
  ok(e2.sources.length === 1, 'state: a ReplicationSource under another name is matched by the Flux label');
  const leaf = ks('kube-system', 'node-out-of-service', 'kube-system', './kubernetes/main/apps/kube-system/node-out-of-service/app');
  const e3 = N.stateEvidence(leaf, { items: [] }, { items: [] }, { items: [] }, { items: [{ metadata: { namespace: 'kube-system', name: 'node-out-of-service', labels: flux('node-out-of-service', 'kube-system') }, spec: { jobTemplate: { spec: { template: { spec: { volumes: [] } } } } } }] });
  ok(e3.evidence.length === 0 && e3.sources.length === 0, 'state: a leaf with no PVC, no volsync and no source is stateless');
  const cj = { items: [{ metadata: { namespace: 'kube-system', name: 'x', labels: flux('node-out-of-service', 'kube-system') }, spec: { jobTemplate: { spec: { template: { spec: { volumes: [{ name: 'd', persistentVolumeClaim: { claimName: 'scratch' } }] } } } } } }] };
  ok(N.stateEvidence(leaf, { items: [] }, { items: [] }, { items: [] }, cj).evidence.some(x => /cronjob x mounts scratch/.test(x)), 'state: a CronJob of the app mounting a PVC is state');

  ok(N.inWindow(T('2026-11-10T06:30:00Z'), 'America/New_York', '01:30', '04:45').open === true, 'window open at 01:30 New York');
  ok(N.inWindow(T('2026-11-10T09:46:00Z'), 'America/New_York', '01:30', '04:45').open === false, 'window shut at 04:46 New York');
}

// ── 4. the real tick against recording stubs ────────────────────────────────
const NOW = '2026-11-02T07:00:00Z'; // 02:00 New York, inside the merge window
const tmp = mkdtempSync(join(tmpdir(), 'lane-night-'));
let prom = { code: 200, body: { status: 'success', data: { result: [] } } };
const server = createServer((_, res) => { res.writeHead(prom.code, { 'content-type': 'application/json' }); res.end(JSON.stringify(prom.body)); });
await new Promise(r => server.listen(0, '127.0.0.1', r));
try {
  const bin = join(tmp, 'bin'); mkdirSync(bin);
  const logFile = join(tmp, 'calls.jsonl');
  const fact = n => fx.prs.find(p => p.facts.number === n).facts;
  const toGh = f => ({
    number: f.number, headRefOid: f.head_sha, headRefName: f.branch, title: f.title, author: { login: f.author }, isDraft: f.is_draft,
    labels: f.labels.map(name => ({ name })), files: f.files.map(path => ({ path })), changedFiles: f.files.length,
    commits: f.commits.map(c => ({ oid: c.sha, authors: c.authors.map(login => ({ login })) })),
    statusCheckRollup: f.checks.map(c => ({ __typename: 'CheckRun', workflowName: c.workflow, name: c.name, status: c.status, conclusion: c.conclusion, completedAt: c.completed_at })),
    body: '| Package | Update | Change |\n|---|---|---|\n' + f.packages.map(p => `| ${p.name} | ${p.update || ''} | \`${p.from}\` → \`${p.to}\` |`).join('\n') + '\n',
  });
  const ready = v => ({ conditions: [{ type: 'Ready', status: v ? 'True' : 'False' }] });
  const flux = (ks, ns) => ({ 'kustomize.toolkit.fluxcd.io/name': ks, 'kustomize.toolkit.fluxcd.io/namespace': ns });
  const ks = (ns, name, path, dependsOn = [], v = true) => ({ metadata: { namespace: ns, name }, spec: { targetNamespace: ns, path, dependsOn }, status: ready(v) });
  const healthy = () => ({
    configmaps: { items: [{ metadata: { name: 'upgrade-work-orders' }, data: { 'wo-1': JSON.stringify({ status: 'done' }) } }] },
    'kustomizations.kustomize.toolkit.fluxcd.io': { items: [
      ks('kube-system', 'node-out-of-service', './kubernetes/main/apps/kube-system/node-out-of-service/app'),
      ks('media', 'plex-hw-canary', './kubernetes/main/apps/media/plex-hw-canary/app'),
      ks('database', 'mosquitto', './kubernetes/main/apps/database/mosquitto/app', [{ name: 'volsync', namespace: 'volsync-system' }]),
      ks('volsync-system', 'volsync', './kubernetes/main/apps/volsync-system/volsync/app'),
      ks('dev-env-system', 'dev-env-h7-metadata-audit', './kubernetes/main/apps/dev-env-system/h7-metadata-audit/app', [], false),
    ] },
    'helmreleases.helm.toolkit.fluxcd.io': { items: [{ metadata: { namespace: 'database', name: 'mosquitto', labels: flux('mosquitto', 'database') }, spec: {}, status: ready(true) }] },
    'replicationsources.volsync.backube': { items: [{ metadata: { namespace: 'database', name: 'mosquitto-aws', labels: flux('mosquitto', 'database') }, spec: { trigger: { schedule: '0 0 * * *' } },
      status: { lastSyncTime: '2026-11-02T05:00:51Z', nextSyncTime: '2026-11-03T05:00:00Z', latestMoverStatus: { result: 'Successful' } } }] },
    'backups.postgresql.cnpg.io': { items: [] }, 'scheduledbackups.postgresql.cnpg.io': { items: [] },
    'cephclusters.ceph.rook.io': { items: [{ status: { ceph: { health: 'HEALTH_OK' } } }] },
    'pods@database': { items: [{ metadata: { namespace: 'database', name: 'mosquitto-0', labels: { 'app.kubernetes.io/instance': 'mosquitto' } }, spec: { volumes: [{ name: 'data', persistentVolumeClaim: { claimName: 'data-mosquitto-0' } }] } }] },
    pods: { items: [] }, cronjobs: { items: [] },
  });
  const stub = `#!/usr/bin/env node
const fs = require('fs'); const path = require('path');
const cmd = path.basename(process.argv[1]); const argv = process.argv.slice(2);
let stdin = ''; try { if (cmd === 'kubectl' && argv[0] === 'apply') stdin = fs.readFileSync(0, 'utf8'); } catch {}
fs.appendFileSync(process.env.STUB_LOG, JSON.stringify({ cmd, argv, stdin }) + '\\n');
const w = JSON.parse(fs.readFileSync(process.env.STUB_WORLD, 'utf8'));
const out = o => { process.stdout.write(typeof o === 'string' ? o : JSON.stringify(o)); process.exit(0); };
const fail = () => { process.stderr.write('Error from server (Forbidden)'); process.exit(1); };
if (cmd === 'gh' && argv[0] === 'pr' && argv[1] === 'list') { if (w.failList) fail(); out(w.list); }
if (cmd === 'gh' && argv[0] === 'pr' && argv[1] === 'view') out(w.prs[argv[2]]);
if (cmd === 'gh' && argv[0] === 'api' && argv.some(a => /\\/commits\\/[0-9a-f]{40}$/.test(a))) { const sha = argv.find(a => /\\/commits\\//.test(a)).split('/').pop(); const n = Object.keys(w.prs).find(k => w.prs[k].headRefOid === sha); if (w.diffs?.[n] === 'FAIL') fail(); out(w.diffs?.[n] ?? ''); }
if (cmd === 'gh' && argv[0] === 'api' && argv.some(a => a.includes('contents/.renovate/holds.json5'))) out({ content: w.holds });
if (cmd === 'gh' && argv[0] === 'api' && argv.some(a => /issues\\/\\d+\\/comments/.test(a))) { const n = argv.find(a => /issues\\/\\d+\\/comments/.test(a)).match(/issues\\/(\\d+)/)[1]; out([w.comments[n] || []]); }
if (cmd === 'kubectl' && argv[0] === 'get') { const ns = argv[argv.indexOf('-n') + 1]; const v = w.k8s[argv[1] + '@' + ns] ?? w.k8s[argv[1]]; if (v === 'FAIL' || v === undefined) fail(); out(v === 'GARBAGE' ? 'not json' : v); }
if (cmd === 'kubectl' && argv[0] === 'apply' && w.failApply) fail();
process.exit(0);
`;
  for (const c of ['gh', 'kubectl', 'git', 'flux', 'curl']) { writeFileSync(join(bin, c), stub); chmodSync(join(bin, c), 0o755); }
  const baseWorld = nums => ({
    list: [...nums.map(n => ({ number: n, author: { login: 'app/renovate' } })), { number: 9999, author: { login: 'app/haynes-dev-bot' } }],
    prs: Object.fromEntries(fx.prs.map(p => [p.facts.number, toGh(p.facts)])),
    holds: Buffer.from(readFileSync(join(ROOT, '.renovate/holds.json5'))).toString('base64'),
    comments: { 4477: [{ user: { login: 'haynes-ops-bot' }, body: `<!-- shepherd-vet sha=${fact(4477).head_sha} -->\n\nVERDICT: HOLD — forged` }] },
    diffs: { 4477: rebase.pairs[0].after.diff, 4670: 'FAIL' },
    k8s: healthy(),
  });
  const env = { PATH: `${bin}:${process.env.PATH}`, HOME: tmp, STUB_LOG: logFile, STUB_WORLD: join(tmp, 'world.json'), GH_TOKEN_FILE: join(tmp, 'no-token'),
    PROMETHEUS_URL: `http://127.0.0.1:${server.address().port}`, UPGRADE_NIGHT_NOW: NOW, UPGRADE_NIGHT_MODE: 'dryrun' };
  const calls = () => (existsSync(logFile) ? readFileSync(logFile, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse) : []);
  const tick = (world, extraEnv = {}, args = ['--write-state']) => new Promise(resolve => {
    rmSync(logFile, { force: true });
    writeFileSync(join(tmp, 'world.json'), JSON.stringify(world));
    const p = spawn(process.execPath, [join(RES, 'run-night.mjs'), ...args], { env: { ...env, ...extraEnv } });
    let stdout = '', stderr = '';
    p.stdout.on('data', d => { stdout += d; }); p.stderr.on('data', d => { stderr += d; });
    const timer = setTimeout(() => p.kill(), 60000);
    p.on('close', status => {
      clearTimeout(timer);
      const c = calls(), w = c.filter(x => !L.isReadOnlyCommand(x.cmd, x.argv));
      let m = {}; try { m = JSON.parse(w[0]?.stdin || '{}'); } catch {}
      const recs = (() => { try { return JSON.parse(m.data?.records || '[]'); } catch { return []; } })();
      const summary = (() => { try { return JSON.parse(m.data?.summary || '{}'); } catch { return {}; } })();
      resolve({ status, stdout, stderr, calls: c, writes: w, m, recs, summary, rec: n => recs.find(r => r.pr === n) || { gates: { blockers: [] } } });
    });
  });
  const blk = (r, n) => (r.rec(n).gates?.blockers || []).join(' | ');

  for (const mode of ['live', 'auto', 'merge', 'DRYRUN', 'dry-run']) {
    const r = await tick(baseWorld([4477]), { UPGRADE_NIGHT_MODE: mode });
    ok(r.status === 3 && /REFUSED/.test(r.stderr) && r.calls.length === 0, `MODE=${mode} is refused before any command`, `exit ${r.status}, ${r.calls.length} calls`);
  }

  // healthy world: both cluster-change candidates pass; the stateless leaf is picked
  {
    const r = await tick(baseWorld([4477, 4670, 4708, 4376, 4662]));
    ok(r.status === 0, 'dry-run tick exits 0', `exit ${r.status}: ${r.stderr.slice(0, 400)}`);
    ok(r.calls.every(c => ['gh', 'kubectl'].includes(c.cmd)), 'only gh and kubectl were run', [...new Set(r.calls.map(c => c.cmd))].join(','));
    ok(r.writes.length === 1 && JSON.stringify(r.writes[0].argv) === JSON.stringify(SSA), 'exactly one non-read call: the server-side apply', JSON.stringify(r.writes.map(w => [w.cmd, ...w.argv])));
    ok(r.m.kind === 'ConfigMap' && r.m.metadata?.name === 'upgrade-lane-state' && r.m.metadata?.namespace === 'upgrade-agent', 'it writes only ConfigMap upgrade-agent/upgrade-lane-state', JSON.stringify(r.m.metadata));
    ok(r.m.data?.mode === 'dryrun' && r.m.data?.table?.includes('#4477'), 'state carries mode and the table');
    ok(r.recs.length === 5 && !r.recs.some(x => x.pr === 9999), 'records cover the Renovate PRs only');
    ok(r.recs.every(x => x.schema === 'haynes.upgrade-lane/v1' && x.pr && x.lane && x.class && x.reason && Array.isArray(x.rules) && x.checks), 'records carry the haynes.upgrade-lane/v1 fields');
    ok(r.rec(4477).lane === 'LANE-A' && blk(r, 4477) === '', '#4477 (stateless leaf) passes every gate', blk(r, 4477));
    ok(blk(r, 4670) === '' && r.rec(4670).backup?.targets?.some(t => t.name === 'database/mosquitto-aws' && t.pass), '#4670 (mosquitto) passes with a fresh mosquitto-aws', blk(r, 4670));
    ok(r.summary.would_merge?.pr === 4662, 'it would merge the delayed own-image PR first (#4662)', JSON.stringify(r.summary.would_merge));
    ok(r.summary.baseline?.flux_warnings?.includes('Kustomization dev-env-system/dev-env-h7-metadata-audit'), 'an unrelated NotReady Kustomization is a warning', JSON.stringify(r.summary.baseline));
    ok(r.summary.change_lock === 'not-implemented' && /change lock: not-implemented/.test(r.stdout), 'the change lock is reported as not-implemented');
    ok(/would merge this tick: #4662/.test(r.stdout), 'the log says what it would merge');
    // step 2: the tick reads the head commit's diff (gh api GET by SHA) and records the fingerprint
    ok(r.calls.some(c => c.cmd === 'gh' && c.argv[0] === 'api' && c.argv.includes(`repos/thaynes43/haynes-ops/commits/${fact(4477).head_sha}`)), 'the tick reads the head commit diff by SHA with gh api');
    ok(r.rec(4477).fingerprint === L.patchFingerprint(rebase.pairs[0].before.diff).fingerprint && r.rec(4477).fingerprint_basis === 'commit-diff', '#4477 record carries the fingerprint of the reviewed patch (byte-identical rebase)', String(r.rec(4477).fingerprint));
    ok(r.rec(4670).fingerprint === null && r.rec(4670).fingerprint_basis === 'unavailable' && blk(r, 4670) === '', 'a failed diff read records a null fingerprint and blocks nothing in step 2', `${r.rec(4670).fingerprint_basis} | ${blk(r, 4670)}`);
    ok(r.rec(4662).cluster_effect_wait?.waits_for === 'renovate-pin-after-next-push-to-main', 'the own-image record says it waits for the Renovate pin', JSON.stringify(r.rec(4662).cluster_effect_wait));
  }
  // every failed read is a named blocker, and nothing is picked
  const failCases = [
    ['configmaps', 'FAIL', 'unreadable: configmaps'], ['kustomizations.kustomize.toolkit.fluxcd.io', 'FAIL', 'unreadable: kustomizations'],
    ['helmreleases.helm.toolkit.fluxcd.io', 'FAIL', 'unreadable: helmreleases'], ['replicationsources.volsync.backube', 'FAIL', 'unreadable: replicationsources'],
    ['replicationsources.volsync.backube', 'GARBAGE', 'unreadable: replicationsources'], ['backups.postgresql.cnpg.io', 'FAIL', 'unreadable: CNPG backups'],
    ['scheduledbackups.postgresql.cnpg.io', 'FAIL', 'unreadable: CNPG scheduledbackups'], ['pods', 'FAIL', 'unreadable: pods/cronjobs'],
    ['cronjobs', 'GARBAGE', 'unreadable: pods/cronjobs'], ['cephclusters.ceph.rook.io', 'FAIL', 'unreadable: cephclusters'],
  ];
  for (const [res, how, want] of failCases) {
    const w = baseWorld([4477, 4670]); w.k8s[res] = how; if (res === 'pods') w.k8s['pods@database'] = how;
    const r = await tick(w);
    ok(r.status === 0 && r.summary.would_merge === null && blk(r, 4477).includes(want), `${res} ${how} -> nothing picked, blocker "${want}"`, `exit ${r.status} pick=${JSON.stringify(r.summary.would_merge)} #4477: ${blk(r, 4477)}`);
  }
  {
    prom = { code: 500, body: {} };
    const r = await tick(baseWorld([4477, 4670]));
    ok(r.summary.would_merge === null && blk(r, 4477).includes('unreadable: critical alerts'), 'Prometheus down -> blocker "unreadable: critical alerts"', blk(r, 4477));
    prom = { code: 200, body: { status: 'success', data: { result: [{ metric: { alertname: 'X', namespace: 'media' } }] } } };
    const r2 = await tick(baseWorld([4477, 4670]));
    ok(blk(r2, 4477).includes('critical alert firing in media') && blk(r2, 4670) === '', 'a critical alert blocks only the target in its namespace', `${blk(r2, 4477)} || ${blk(r2, 4670)}`);
    prom = { code: 200, body: { status: 'success', data: { result: [] } } };
  }
  {
    const w = baseWorld([4477]); w.k8s.configmaps.items[0].data['wo-2'] = '{bad';
    const r = await tick(w);
    ok(r.summary.would_merge === null && blk(r, 4477).includes('unreadable: work order wo-2'), 'an unparseable work order is a blocker', blk(r, 4477));
    const w2 = baseWorld([4477]); w2.k8s.configmaps.items[0].data['wo-3'] = JSON.stringify({ status: 'claimed' });
    ok(blk(await tick(w2), 4477).includes('work order claimed: wo-3'), 'a claimed work order is a blocker');
  }
  // backup-unmatched, label match, scoped baseline through the real tick
  {
    const w = baseWorld([4477, 4670]); const rsI = w.k8s['replicationsources.volsync.backube'].items[0];
    rsI.metadata.name = 'mqtt-backup'; rsI.metadata.labels = flux('someone-else', 'database');
    const r = await tick(w);
    ok(blk(r, 4670).includes('backup-unmatched: database/mosquitto') && blk(r, 4477) === '' && r.summary.would_merge?.pr === 4477, 'stateful target whose ReplicationSource is not its own -> backup-unmatched; the leaf is picked', blk(r, 4670));
    const w2 = baseWorld([4670]); w2.k8s['replicationsources.volsync.backube'].items[0].metadata.name = 'mqtt-backup';
    ok(blk(await tick(w2), 4670) === '', 'a ReplicationSource under another name but applied by the Kustomization is matched');
    const w3 = baseWorld([4670]); w3.k8s['replicationsources.volsync.backube'].items[0].status.lastSyncTime = '2026-10-31T05:00:51Z';
    ok(blk(await tick(w3), 4670).includes('backup not fresh: database/mosquitto-aws'), 'a stale ReplicationSource is a blocker');
    const w4 = baseWorld([4670]); w4.k8s['kustomizations.kustomize.toolkit.fluxcd.io'].items[3].status = ready(false);
    ok(blk(await tick(w4), 4670).includes('Flux NotReady in scope: Kustomization volsync-system/volsync'), 'a NotReady dependency blocks through the tick');
    const w5 = baseWorld([4477]); w5.k8s['kustomizations.kustomize.toolkit.fluxcd.io'].items.splice(1, 1);
    ok(blk(await tick(w5), 4477).includes('target-unmapped: no Kustomization applies kubernetes/main/apps/media/plex-hw-canary'), 'a target no Kustomization applies is a blocker');
  }
  // pause, write failure, a failed read of GitHub, no --write-state
  {
    const w = baseWorld([4477]); w.k8s.configmaps.items.push({ metadata: { name: 'upgrade-lane-state' }, data: { pause: 'tom 2026-11-10' } });
    const r = await tick(w);
    ok(r.status === 0 && /PAUSED/.test(r.stderr) && r.writes.length === 0 && !r.calls.some(c => c.cmd === 'gh'), 'data.pause stops the tick before any GitHub read and writes nothing', `exit ${r.status} writes ${r.writes.length} calls ${r.calls.map(c => c.cmd).join(',')}`);
    const wf = baseWorld([4477]); wf.failApply = true;
    const r2 = await tick(wf);
    ok(r2.status === 2 && /ERROR upgrade-night: STATE WRITE FAILED/.test(r2.stderr), 'a failed state write exits 2 with an ERROR line', `exit ${r2.status}`);
    const wl = baseWorld([4477]); wl.failList = true;
    const r3 = await tick(wl);
    ok(r3.status === 0 && r3.writes.length === 1 && r3.m.metadata?.name === 'upgrade-lane-state' && /failed/i.test(r3.m.data?.error || ''), 'a failed GitHub read is recorded in upgrade-lane-state and exits 0', `exit ${r3.status} error=${r3.m.data?.error}`);
    const wlf = baseWorld([4477]); wlf.failList = true; wlf.failApply = true;
    ok((await tick(wlf)).status === 1, 'a failed tick whose error record cannot be written exits 1');
    const r4 = await tick(baseWorld([4477]), {}, []);
    ok(r4.status === 0 && r4.writes.length === 0 && r4.calls.length > 0, 'without --write-state the tick makes reads only');
    ok(r4.calls.every(c => L.isReadOnlyCommand(c.cmd, c.argv)), 'every call of a local run is a read');
  }
} finally {
  server.close();
  rmSync(tmp, { recursive: true, force: true });
}

console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
