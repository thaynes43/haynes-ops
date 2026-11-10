#!/usr/bin/env node
// run-night.mjs — the upgrade-night tick (OP512 rollout step 1: DRY-RUN ONLY).
//
// Design: OP512 comment 6193, "Lane A" and "Rollout order" step 1
// (https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/512).
// Runbook: .agents/runbooks/upgrade-night-lane.md.
//
// Each tick: classify every open Renovate PR (lane-classify.mjs), evaluate for each
// lane-A candidate the gates it WOULD need tonight (expected checks, Renovate-only
// commits, the backup gate, the baseline health, the change lock, open work orders,
// the merge window), pick the ONE PR it would merge, and record the result in the
// ConfigMap upgrade-lane-state (namespace upgrade-agent) and in the log.
//
// DRY-RUN GUARANTEE. This build has no live mode. Any UPGRADE_NIGHT_MODE other than
// "dryrun" is refused before any read. Every command goes through runReadOnly()
// (gh pr list/view, gh api GET, kubectl get); the only write is writeLaneState(),
// which applies one ConfigMap named upgrade-lane-state in upgrade-agent and only
// when --write-state is passed (the CronJob passes it; a local run does not). The
// minted GitHub token is read-only as well (see the CronJob's mint-token env).
// scripts/upgrade-agent/lane-night-dryrun-selftest.mjs fails if any mutating
// gh/git/kubectl verb appears on this path.
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as L from './lane-classify.mjs';

const MODE = process.env.UPGRADE_NIGHT_MODE || 'dryrun';
if (MODE !== 'dryrun') {
  // REFUSAL — step 1 implements no live mode. Merging, reverting, commenting and
  // labelling arrive in later rollout steps, each in its own reviewed PR.
  console.error(`upgrade-night: REFUSED: UPGRADE_NIGHT_MODE=${MODE}. This build implements dry-run only (OP512 step 1); nothing was read or changed.`);
  process.exit(3);
}

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = process.env.UPGRADE_AGENT_REPO || 'thaynes43/haynes-ops';
const PROM = process.env.PROMETHEUS_URL || 'http://kube-prometheus-stack-prometheus.observability.svc.cluster.local:9090';
const WRITE_STATE = process.argv.includes('--write-state');
const log = (...a) => console.error(new Date().toISOString(), ...a);

// gh reads the installation token minted by the initContainer. Never logged.
const childEnv = { ...process.env };
const tokenFile = process.env.GH_TOKEN_FILE || '/creds/gh_token';
if (existsSync(tokenFile)) childEnv.GH_TOKEN = readFileSync(tokenFile, 'utf8').trim();
else log(`no ${tokenFile}; using the ambient gh login (local run)`);
const run = (cmd, argv, o = {}) => L.runReadOnly(cmd, argv, { env: childEnv, ...o });
const kget = (args) => { const out = run('kubectl', ['get', ...args, '-o', 'json'], { allowFail: true }); try { return out ? JSON.parse(out) : null; } catch { return null; } };

async function httpGetJson(url) {
  try {
    const r = await fetch(url, { method: 'GET', signal: AbortSignal.timeout(15000) });
    return r.ok ? await r.json() : null;
  } catch { return null; }
}

// ── time in New York ─────────────────────────────────────────────────────────
function zoned(ms, tz) {
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-US', { timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
    .formatToParts(new Date(ms)).filter(x => x.type !== 'literal').map(x => [x.type, Number(x.value)]));
  return p;
}
function zonedToUtc(y, mo, d, h, mi, tz) {
  let guess = Date.UTC(y, mo - 1, d, h, mi);
  for (let i = 0; i < 3; i++) {
    const z = zoned(guess, tz);
    const asUtc = Date.UTC(z.year, z.month - 1, z.day, z.hour, z.minute);
    guess -= asUtc - Date.UTC(y, mo - 1, d, h, mi);
  }
  return guess;
}
// The backup cutoff for the night that contains `now`: 23:00 local on the evening the
// night began (the previous calendar day before noon, else today, which is in the future).
export function backupCutoff(now, tz, hhmm) {
  const [h, m] = hhmm.split(':').map(Number);
  const z = zoned(now, tz);
  const base = Date.UTC(z.year, z.month - 1, z.day) - (z.hour < 12 ? 86400e3 : 0);
  const b = new Date(base);
  return zonedToUtc(b.getUTCFullYear(), b.getUTCMonth() + 1, b.getUTCDate(), h, m, tz);
}
export function inWindow(now, tz, first, last) {
  const z = zoned(now, tz);
  const mins = z.hour * 60 + z.minute;
  const toM = s => { const [h, m] = s.split(':').map(Number); return h * 60 + m; };
  return { local: `${String(z.hour).padStart(2, '0')}:${String(z.minute).padStart(2, '0')}`, open: mins >= toM(first) && mins <= toM(last) };
}

// ── cluster facts (read-only) ────────────────────────────────────────────────
function clusterFacts(rules) {
  const ns = rules.night.namespace;
  const ks = kget(['kustomizations.kustomize.toolkit.fluxcd.io', '-A']);
  const rs = kget(['replicationsources.volsync.backube', '-A']);
  const backups = kget(['backups.postgresql.cnpg.io', '-A']);
  const ceph = kget(['cephclusters.ceph.rook.io', '-A']);
  const cms = kget(['configmaps', '-n', ns]);
  const cm = name => (cms?.items || []).find(c => c.metadata?.name === name) || null;
  return { ks, rs, backups, ceph, lock: cm(rules.night.change_lock_configmap), orders: cm(rules.night.work_orders_configmap) };
}

function kustomizationsFor(target, ks) {
  return (ks?.items || []).filter(k => {
    const p = String(k.spec?.path || '').replace(/^\.\//, '').replace(/\/$/, '');
    return p === target || p.startsWith(target + '/');
  });
}

function backupGate(rec, rules, cf, cutoff) {
  const out = [];
  for (const t of rec.targets) {
    for (const k of kustomizationsFor(t, cf.ks)) {
      const app = k.spec?.postBuild?.substitute?.APP || k.metadata.name;
      const ns = k.spec?.targetNamespace || k.metadata.namespace;
      const r = (cf.rs?.items || []).find(x => x.metadata.namespace === ns && [`${app}-aws`, `${k.metadata.name}-aws`].includes(x.metadata.name));
      if (!r) continue;
      const last = Date.parse(r.status?.lastSyncTime || '');
      const result = r.status?.latestMoverStatus?.result || null;
      out.push({ target: t, kind: 'volsync', name: `${ns}/${r.metadata.name}`, last: r.status?.lastSyncTime || null, result,
        pass: Number.isFinite(last) && last >= cutoff && result === 'Successful' });
    }
    for (const c of rules.cnpg_clients) {
      if (!c.paths.some(g => L.matchGlob(`${t}/x`, g))) continue;
      const [cns, cname] = c.cluster.split('/');
      const mine = (cf.backups?.items || []).filter(b => b.metadata.namespace === cns && b.spec?.cluster?.name === cname && b.status?.phase === 'completed');
      const newest = mine.reduce((a, b) => (String(b.status?.stoppedAt || '') > String(a?.status?.stoppedAt || '') ? b : a), null);
      const stop = Date.parse(newest?.status?.stoppedAt || '');
      if (!out.some(o => o.name === c.cluster)) out.push({ target: t, kind: 'cnpg', name: c.cluster, last: newest?.status?.stoppedAt || null, result: newest ? 'completed' : null, pass: Number.isFinite(stop) && stop >= cutoff });
    }
  }
  return out;
}

function namespacesFor(rec, cf) {
  const set = new Set();
  for (const t of rec.targets) for (const k of kustomizationsFor(t, cf.ks)) set.add(k.spec?.targetNamespace || k.metadata.namespace);
  return [...set];
}

function baseline(cf, alerts) {
  const notReady = (cf.ks?.items || []).filter(k => !k.spec?.suspend && !(k.status?.conditions || []).some(c => c.type === 'Ready' && c.status === 'True'))
    .map(k => `${k.metadata.namespace}/${k.metadata.name}`);
  const cephHealth = (cf.ceph?.items || []).map(c => c.status?.ceph?.health || 'unknown');
  return {
    flux_read: !!cf.ks, flux_not_ready: notReady,
    ceph: cephHealth.length ? cephHealth.join(',') : 'unknown',
    alerts_read: alerts !== null,
    critical_alert_namespaces: alerts ?? [],
  };
}

// ── the tick ─────────────────────────────────────────────────────────────────
async function tick() {
  const rules = L.loadRules(process.env.UPGRADE_RULES || join(HERE, 'upgrade-rules.json'));
  const now = Date.now();
  const tz = rules.night.timezone;
  const holdRules = L.fetchHoldRules(REPO, run);
  const nums = L.listRenovatePrs(REPO, rules, run);
  const records = nums.map(n => {
    try { return L.classify(L.gatherFacts(REPO, n, run), rules, { now, holdRules }); }
    catch (e) { return { schema: 'haynes.upgrade-lane/v1', pr: n, lane: 'BLOCKED', class: 'facts-incomplete', reason: `read failed: ${e.message}`, update_type: 'unknown', rules: [], checks: {}, targets: [] }; }
  });

  const cf = clusterFacts(rules);
  const prom = await httpGetJson(`${PROM}/api/v1/query?query=${encodeURIComponent('ALERTS{alertstate="firing",severity="critical"}')}`);
  const alertNs = prom ? [...new Set((prom.data?.result || []).map(r => r.metric?.namespace || '(cluster)'))] : null;
  const base = baseline(cf, alertNs);
  const cutoff = backupCutoff(now, tz, rules.night.backup_cutoff_local);
  const win = inWindow(now, tz, rules.night.merge_window_local.first, rules.night.merge_window_local.last);
  const lockHolder = cf.lock?.data?.holder || (cf.lock ? 'present (no holder key)' : null);
  const claimed = Object.entries(cf.orders?.data || {}).filter(([, v]) => { try { return JSON.parse(v).status === 'claimed'; } catch { return false; } }).map(([k]) => k);

  for (const r of records) {
    if (r.lane !== 'LANE-A') continue;
    const backup = r.cluster_effect === 'immediate' ? backupGate(r, rules, cf, cutoff) : [];
    const nss = namespacesFor(r, cf);
    const blockers = [];
    if ((r.checks?.stale || []).length) blockers.push(`checks older than ${rules.check_max_age_days}d need a Renovate retry first`);
    if (backup.some(b => !b.pass)) blockers.push(`backup not proven after ${new Date(cutoff).toISOString()}: ${backup.filter(b => !b.pass).map(b => `${b.name} last=${b.last ?? 'none'} ${b.result ?? ''}`.trim()).join('; ')}`);
    if (r.cluster_effect === 'immediate') {
      if (!base.flux_read) blockers.push('Flux state unreadable');
      if (base.flux_not_ready.length) blockers.push(`Flux NotReady: ${base.flux_not_ready.slice(0, 5).join(', ')}${base.flux_not_ready.length > 5 ? ` +${base.flux_not_ready.length - 5}` : ''}`);
      if (/HEALTH_ERR|unknown/.test(base.ceph)) blockers.push(`Ceph ${base.ceph}`);
      if (!base.alerts_read) blockers.push('critical alerts unreadable (Prometheus)');
      const hot = base.critical_alert_namespaces.filter(n => nss.includes(n));
      if (hot.length) blockers.push(`critical alert firing in ${hot.join(', ')}`);
    }
    if (lockHolder) blockers.push(`change lock held: ${lockHolder}`);
    if (claimed.length) blockers.push(`work order claimed: ${claimed.join(', ')}`);
    if (!win.open) blockers.push(`outside the merge window (${rules.night.merge_window_local.first}-${rules.night.merge_window_local.last} ${tz}, now ${win.local})`);
    r.backup = { cutoff: new Date(cutoff).toISOString(), targets: backup, stateful: backup.length > 0 };
    r.gates = { namespaces: nss, blockers, would_be_eligible: blockers.length === 0 };
  }

  const effOrder = { none: 0, delayed: 1, immediate: 2 };
  const eligible = records.filter(r => r.gates?.would_be_eligible)
    .sort((a, b) => (effOrder[a.cluster_effect] - effOrder[b.cluster_effect]) || (a.backup.stateful - b.backup.stateful) || (a.pr - b.pr));
  const pick = eligible[0] || null;
  const counts = Object.fromEntries(L.LANES.map(l => [l, records.filter(r => r.lane === l).length]));
  const summary = {
    mode: MODE, at: new Date(now).toISOString(), repo: REPO,
    window: { ...win, tz }, backup_cutoff: new Date(cutoff).toISOString(),
    baseline: { flux_not_ready: base.flux_not_ready.length, ceph: base.ceph, alerts_read: base.alerts_read, critical_alert_namespaces: base.critical_alert_namespaces },
    change_lock: lockHolder, work_orders_claimed: claimed,
    counts, lane_a_candidates: records.filter(r => r.lane === 'LANE-A').map(r => r.pr),
    would_merge: pick ? { pr: pick.pr, head: pick.head, class: pick.class, reason: 'DRY-RUN: nothing was merged' } : null,
    why_none: pick ? null : (records.some(r => r.lane === 'LANE-A') ? 'every lane-A candidate has a blocker (see records[].gates.blockers)' : 'no lane-A candidate'),
  };
  for (const r of records) { r.updated = summary.at; r.preapproval = {}; r.schedule = {}; r.last_attempt = {}; }

  const table = L.formatTable(records);
  log(`upgrade-night ${MODE}: ${records.length} open Renovate PRs; ${JSON.stringify(counts)}`);
  process.stdout.write(table + '\n');
  for (const r of records.filter(x => x.gates)) process.stdout.write(`#${r.pr} gates: ${r.gates.blockers.length ? r.gates.blockers.join(' | ') : 'all pass'}\n`);
  process.stdout.write(`would merge this tick: ${pick ? `#${pick.pr} (${pick.class})` : `none (${summary.why_none})`}\n`);

  const manifest = {
    apiVersion: 'v1', kind: 'ConfigMap',
    metadata: { name: rules.night.state_configmap, namespace: rules.night.namespace, labels: { 'app.kubernetes.io/name': 'upgrade-shepherd', 'app.kubernetes.io/component': 'upgrade-night' } },
    data: { mode: MODE, updated: summary.at, summary: JSON.stringify(summary), table, records: JSON.stringify(records) },
  };
  if (WRITE_STATE) writeLaneState(manifest);
  else log('state not written (no --write-state); this was a local run');
}

// THE ONLY WRITE: one ConfigMap, fixed name and namespace, runtime state like
// upgrade-orphan-report (not Flux-managed). Refuses anything else.
function writeLaneState(manifest) {
  const m = manifest.metadata || {};
  if (manifest.kind !== 'ConfigMap' || m.name !== 'upgrade-lane-state' || m.namespace !== 'upgrade-agent' || Object.keys(manifest).some(k => !['apiVersion', 'kind', 'metadata', 'data'].includes(k))) {
    throw new Error('writeLaneState: refused (only ConfigMap upgrade-agent/upgrade-lane-state may be written)');
  }
  const body = JSON.stringify(manifest);
  if (Buffer.byteLength(body) > 900 * 1024) throw new Error('writeLaneState: state too large for a ConfigMap');
  const r = spawnSync('kubectl', ['apply', '-f', '-'], { input: body, encoding: 'utf8', env: childEnv });
  if (r.status !== 0) { log(`WARN could not write upgrade-lane-state (exit ${r.status}): ${String(r.stderr).slice(0, 200)}`); return false; }
  log('wrote ConfigMap upgrade-agent/upgrade-lane-state');
  return true;
}

// A failed tick (GitHub or the API unreachable, a bad rules file) is recorded in the
// state and the Job still exits 0 when that record lands: a Failed pod in
// upgrade-agent is what the health gate and triage read as a regression, and a
// dry-run that cannot read must not start a remediation. It exits 1 only when even
// the error record cannot be written.
if (L.isMain(import.meta.url)) {
  tick().catch(e => {
    const at = new Date().toISOString();
    log(`upgrade-night: FAILED: ${e.message}`);
    const written = WRITE_STATE && writeLaneState({
      apiVersion: 'v1', kind: 'ConfigMap',
      metadata: { name: 'upgrade-lane-state', namespace: 'upgrade-agent', labels: { 'app.kubernetes.io/name': 'upgrade-shepherd', 'app.kubernetes.io/component': 'upgrade-night' } },
      data: { mode: MODE, updated: at, error: String(e.message).slice(0, 2000), summary: JSON.stringify({ mode: MODE, at, error: String(e.message).slice(0, 500), would_merge: null }) },
    });
    process.exit(written ? 0 : 1);
  });
}
