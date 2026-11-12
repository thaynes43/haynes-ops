#!/usr/bin/env node
// run-night.mjs — the upgrade-night tick (OP512; git ships dry-run, live mode is story 752's switch).
//
// Design: OP512 comment 6193, "Lane A" and "Rollout order" step 1
// (https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/512).
// Runbook: .agents/runbooks/upgrade-night-lane.md.
//
// Each tick: classify every open Renovate PR (lane-classify.mjs), evaluate for each
// lane-A candidate the gates it WOULD need tonight (expected checks, Renovate-only
// commits, per-source backup freshness, the scoped baseline health, open work orders,
// the merge window), pick the ONE PR it would merge, and record the result in the
// ConfigMap upgrade-lane-state (namespace upgrade-agent) and in the log.
//
// Every gate FAILS CLOSED: a read that errors or returns nothing parseable is a
// blocker named "unreadable: <what>", never a pass. A target is stateless only on
// positive evidence. data.pause on upgrade-lane-state stops the tick (kill switch).
//
// STORY 551 (https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/551),
// facts, not clocks (Tom's ruling Q-548-04): the settle gate (night-merge.mjs
// settleVerdict) blocks a candidate with an immediate cluster effect while main has a change
// Flux has not applied that reaches its target, its dependsOn chain or a cluster-wide part
// (F1), while its target has not applied the source revision or is not settled (F2), or
// while a cluster-wide part is mid-change (F3). The merge step and a live merge call re-read
// it, and add F4 (no shepherd Job that can merge running).
//
// OP550 (https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/550):
// a critical alert blocks only when it belongs to the target (R5, alertGate); checks are
// stale when main changed one of the PR's files after them (R7, gh api GET of main's
// commits per file); a shared component maps to the Kustomizations that include it (R8,
// in lane-classify.mjs); ceph-csi is lane B by rule (R10, upgrade-rules.json).
//
// MODES (story 752, https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/752).
// UPGRADE_NIGHT_MODE is "dryrun" (what git ships) or "live"; anything else, or an
// UPGRADE_NIGHT_LIVE_CLASSES naming a class that does not exist, is refused before any
// read. Live mode merges only a PR whose class is in UPGRADE_NIGHT_LIVE_CLASSES, which is
// empty by default, so live mode with no class merges nothing. The merge step and the
// change lock live in night-merge.mjs.
//
// DRY-RUN GUARANTEE. In dry-run nothing reaches the merge call: night-merge.mjs's
// callMerge() throws unless its mode is live, and laneMergeStep() calls it only in live
// mode. Every read goes through runReadOnly() (gh pr list/view, gh api GET, kubectl get).
// The cluster writes are writeLaneState() here (server-side apply of ConfigMap
// upgrade-agent/upgrade-lane-state; data.retry_ledger under its own field manager) and,
// in night-merge.mjs, the take and release of ConfigMap upgrade-agent/upgrade-change-lock
// (create, or replace at a resourceVersion). Since story 549 the GitHub writes, in both
// modes, are the lane's own record comment on each open Renovate PR and Renovate's retry
// box on at most night.retry.per_tick PRs, all through night-merge.mjs ghWrite() and never
// a merge. Every write runs only with --write-state (the CronJob passes it; a local run
// does not). The minted GitHub token can merge (contents and pull_requests write, checks
// read: Tom's ruling Q-548-02), so the guarantee rests on dry-run never reaching the merge
// call, not on the token; each tick logs the token's permission names, never the token.
// scripts/upgrade-agent/lane-night-dryrun-selftest.mjs, lane-merge-step-selftest.mjs and
// lane-pr-records-selftest.mjs check this.
//
// STORY 549 (https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/549):
// after the merge step, the PR-write phase ticks Renovate's retry box on a stalled PR (the
// facts read at the write, once per head, 2 per PR in 7 days, 1 per tick) and writes or
// edits one record comment per open Renovate PR, mirrored from the records this tick
// writes to upgrade-lane-state. summary.pr_records, summary.retry and summary.github_writes
// say what it did.
//
// STORY 593 (https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/593):
// after the merge step (the lock is released by then), upgrade-history.mjs appends this
// tick's events to the history, ConfigMap upgrade-agent/upgrade-ledger: an item first seen,
// a new state, lane or class, a close, a merge by someone else, and once, with no marker,
// the backfill of the last 30 days of merges with its count compared against GitHub. Its
// own field manager, at the resourceVersion it read; a failure is logged and lands in
// summary.history, and changes no verdict and no other write. A PR that left the open list
// with a terminal event gets its record's terminal field through the PR-write phase.
import { spawnSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as L from './lane-classify.mjs';
import * as M from './night-merge.mjs';
import * as H from './upgrade-history.mjs';

const MODES = ['dryrun', 'live'];
const MODE = process.env.UPGRADE_NIGHT_MODE || 'dryrun';
const LIVE = M.parseLiveClasses(process.env.UPGRADE_NIGHT_LIVE_CLASSES);
if (!MODES.includes(MODE) || LIVE.unknown.length) {
  // REFUSAL before any read: a mistyped switch must not run as something else.
  const what = !MODES.includes(MODE) ? `UPGRADE_NIGHT_MODE=${MODE} (only dryrun or live)` : `UPGRADE_NIGHT_LIVE_CLASSES names unknown classes ${LIVE.unknown.join(', ')} (known: ${M.LANE_A_CLASSES.join(', ')})`;
  console.error(`upgrade-night: REFUSED: ${what}; nothing was read or changed.`);
  process.exit(3);
}
// UPGRADE_NIGHT_NOW pins the clock for a local read-only run. Live mode never runs on it,
// and neither does a run that writes (--write-state): a lock taken at a pinned future time
// would carry that time's expiry and could block live ticks and hand runs until then.
if (process.env.UPGRADE_NIGHT_NOW && (MODE === 'live' || process.argv.includes('--write-state'))) {
  console.error(`upgrade-night: REFUSED: UPGRADE_NIGHT_NOW is set (a test clock) ${MODE === 'live' ? 'in live mode' : 'with --write-state'}; nothing was read or changed.`);
  process.exit(3);
}

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = process.env.UPGRADE_AGENT_REPO || 'thaynes43/haynes-ops';
const PROM = process.env.PROMETHEUS_URL || 'http://kube-prometheus-stack-prometheus.observability.svc.cluster.local:9090';
const WRITE_STATE = process.argv.includes('--write-state');
const STATE = { name: 'upgrade-lane-state', namespace: 'upgrade-agent' };
const MAX_STATE_BYTES = 768 * 1024; // the API server caps an object at ~1 MiB; keep headroom
const log = (...a) => console.error(new Date().toISOString(), ...a);

// gh reads the installation token minted by the initContainer. Never logged.
const childEnv = { ...process.env };
const tokenFile = process.env.GH_TOKEN_FILE || '/creds/gh_token';
if (existsSync(tokenFile)) childEnv.GH_TOKEN = readFileSync(tokenFile, 'utf8').trim();
// The permission JSON the initContainer sent to GitHub with the mint (names and levels
// only; GitHub mints exactly those or refuses). Unreadable = cannot merge.
const permFile = process.env.GH_TOKEN_PERMISSIONS_FILE || '/creds/gh_token_permissions';
const tokenPerms = () => M.tokenPermissions(existsSync(permFile) ? readFileSync(permFile, 'utf8') : null);
const run = (cmd, argv, o = {}) => L.runReadOnly(cmd, argv, { env: childEnv, ...o });
// null = unreadable (the command failed, or the output is not a list). Never [] on error.
const kget = (args) => {
  const out = run('kubectl', ['get', ...args, '-o', 'json'], { allowFail: true });
  try { const j = out ? JSON.parse(out) : null; return Array.isArray(j?.items) ? j : null; } catch { return null; }
};

async function httpGetJson(url) {
  try {
    const r = await fetch(url, { method: 'GET', signal: AbortSignal.timeout(15000) });
    return r.ok ? await r.json() : null;
  } catch { return null; }
}

// ── time ─────────────────────────────────────────────────────────────────────
function zoned(ms, tz) {
  return Object.fromEntries(new Intl.DateTimeFormat('en-US', { timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    .formatToParts(new Date(ms)).filter(x => x.type !== 'literal').map(x => [x.type, Number(x.value)]));
}
export function inWindow(now, tz, first, last) {
  const z = zoned(now, tz);
  const mins = z.hour * 60 + z.minute;
  const toM = s => { const [h, m] = s.split(':').map(Number); return h * 60 + m; };
  return { local: `${String(z.hour).padStart(2, '0')}:${String(z.minute).padStart(2, '0')}`, open: mins >= toM(first) && mins <= toM(last) };
}

// The shortest gap between two runs of a cron schedule (5 fields, or 6 with seconds
// first as CNPG writes them; @hourly/@daily/@weekly/@monthly). The gap is the same in
// any fixed-offset zone, which is all the freshness rule needs. null = cannot parse.
export function cronPeriodMs(expr) {
  const alias = { '@hourly': '0 * * * *', '@daily': '0 0 * * *', '@midnight': '0 0 * * *', '@weekly': '0 0 * * 0', '@monthly': '0 0 1 * *' };
  let f = String(alias[String(expr).trim()] ?? expr ?? '').trim().split(/\s+/);
  if (f.length === 6) f = f.slice(1);
  if (f.length !== 5) return null;
  const field = (s, lo, hi) => {
    const set = new Set();
    for (const part of s.split(',')) {
      const m = /^(\*|\d+)(?:-(\d+))?(?:\/(\d+))?$/.exec(part);
      if (!m) return null;
      const a = m[1] === '*' ? lo : Number(m[1]);
      const b = m[1] === '*' ? hi : m[2] !== undefined ? Number(m[2]) : m[3] ? hi : a;
      for (let v = a; v <= b; v += Number(m[3] || 1)) set.add(v === 7 && hi === 7 ? 0 : v);
    }
    return set;
  };
  const [mi, ho, dom, mo, dow] = f.map((s, i) => field(s, ...[[0, 59], [0, 23], [1, 31], [1, 12], [0, 7]][i]));
  if (![mi, ho, dom, mo, dow].every(Boolean)) return null;
  const domAny = f[2] === '*', dowAny = f[4] === '*';
  const fires = [];
  for (let t = Date.UTC(2026, 0, 4), end = t + 35 * 86400e3; t < end && fires.length < 400; t += 60e3) {
    const d = new Date(t);
    const dayOk = domAny || dowAny ? (dom.has(d.getUTCDate()) && dow.has(d.getUTCDay())) : (dom.has(d.getUTCDate()) || dow.has(d.getUTCDay()));
    if (mi.has(d.getUTCMinutes()) && ho.has(d.getUTCHours()) && mo.has(d.getUTCMonth() + 1) && dayOk) fires.push(t);
  }
  if (fires.length < 2) return null;
  return Math.min(...fires.slice(1).map((t, i) => t - fires[i]));
}

// Per-source freshness: the newest successful backup or sync must be (a) at or after
// that source's most recent scheduled run and (b) no older than maxAge at merge time.
export function volsyncFresh(rs, now, maxAgeMs) {
  const name = `${rs.metadata.namespace}/${rs.metadata.name}`;
  const next = Date.parse(rs.status?.nextSyncTime || ''), last = Date.parse(rs.status?.lastSyncTime || '');
  const period = cronPeriodMs(rs.spec?.trigger?.schedule);
  const result = rs.status?.latestMoverStatus?.result || null;
  if (!Number.isFinite(next) || !period) return { kind: 'volsync', name, pass: false, why: `schedule unknown (${rs.spec?.trigger?.schedule ?? 'no schedule'}, next=${rs.status?.nextSyncTime ?? 'none'})` };
  const due = next <= now ? next : next - period;
  const pass = result === 'Successful' && Number.isFinite(last) && last >= due && now - last <= maxAgeMs;
  return { kind: 'volsync', name, due: new Date(due).toISOString(), last: rs.status?.lastSyncTime || null, result, pass,
    why: pass ? 'fresh' : (result !== 'Successful' ? `last result ${result}` : last < due ? `no sync since the run due ${new Date(due).toISOString()}` : `last sync older than ${maxAgeMs / 3600e3}h`) };
}
// CNPG: Backup.status.startedAt/stoppedAt come from the instance and can carry local
// time stamped Z (postgres16 and ytdrivarr show 00:00Z for a 05:00Z run), so the
// server-set creationTimestamp is the time used, with phase=completed.
export function cnpgFresh(cluster, schedules, backups, now, maxAgeMs) {
  const [ns, cname] = cluster.split('/');
  const sb = schedules.filter(s => s.metadata.namespace === ns && s.spec?.cluster?.name === cname && !s.spec?.suspend);
  const due = Math.max(...sb.map(s => Date.parse(s.status?.lastScheduleTime || '')).filter(Number.isFinite));
  if (!Number.isFinite(due)) return { kind: 'cnpg', name: cluster, pass: false, why: 'no ScheduledBackup with a lastScheduleTime' };
  const done = backups.filter(b => b.metadata.namespace === ns && b.spec?.cluster?.name === cname && b.status?.phase === 'completed')
    .map(b => Date.parse(b.metadata.creationTimestamp || '')).filter(Number.isFinite);
  const last = done.length ? Math.max(...done) : NaN;
  const pass = Number.isFinite(last) && last >= due && now - last <= maxAgeMs;
  return { kind: 'cnpg', name: cluster, due: new Date(due).toISOString(), last: Number.isFinite(last) ? new Date(last).toISOString() : null, result: Number.isFinite(last) ? 'completed' : null, pass,
    why: pass ? 'fresh' : !Number.isFinite(last) ? 'no completed Backup' : last < due ? `no completed Backup since the run due ${new Date(due).toISOString()}` : `newest Backup older than ${maxAgeMs / 3600e3}h` };
}

// ── Flux objects for a target ───────────────────────────────────────────────
// The target's Flux scope lives in lane-classify.mjs (story 551: the settle gate in
// night-merge.mjs reuses it); re-exported here for the tick and its selftest.
const ksId = L.ksId;
export const kustomizationsFor = L.kustomizationsFor;
const isNotReady = L.isNotReady;
export const fluxScope = L.fluxScope;

// ── critical alerts scoped to the target's workloads (R5, OP550) ─────────────────
// upgrade-rules.json alert_scope says what blocks. These helpers resolve the subject an
// alert names to the Kustomization that applied it, from objects the tick already reads
// (pods, Jobs and CronJobs of the namespace, every Kustomization and HelmRelease). null
// means "cannot tell", which blocks.
const lbls = o => o?.metadata?.labels || {};
// An object's Kustomization: its own Flux labels, else the HelmRelease that rendered it
// (helm.toolkit.fluxcd.io/name, else app.kubernetes.io/instance) and that HelmRelease's
// Flux labels.
export function ownerKs(o, hr) {
  if (!o) return null;
  const l = lbls(o);
  if (l['kustomize.toolkit.fluxcd.io/name']) return `${l['kustomize.toolkit.fluxcd.io/namespace'] || o.metadata.namespace}/${l['kustomize.toolkit.fluxcd.io/name']}`;
  const name = l['helm.toolkit.fluxcd.io/name'] || l['app.kubernetes.io/instance'];
  const ns = l['helm.toolkit.fluxcd.io/namespace'] || o.metadata?.namespace;
  const h = name ? (hr?.items || []).find(x => x.metadata?.name === name && x.metadata?.namespace === ns) : null;
  const hl = lbls(h);
  return hl['kustomize.toolkit.fluxcd.io/name'] ? `${hl['kustomize.toolkit.fluxcd.io/namespace'] || ns}/${hl['kustomize.toolkit.fluxcd.io/name']}` : null;
}
const byName = (list, n) => (list?.items || []).find(x => x.metadata?.name === n) || null;
// A pod's Kustomization: its labels, else up its owner chain (Job, then CronJob).
function podOwner(p, f, hr) {
  const own = ownerKs(p, hr);
  if (own) return own;
  const ref = (p.metadata?.ownerReferences || []).find(r => r.kind === 'Job');
  return ref ? jobOwner(ref.name, f, hr) : null;
}
function jobOwner(name, f, hr) {
  if (!f.jobs) return null; // Jobs unreadable: cannot tell (fail closed)
  const j = byName(f.jobs, name);
  if (j) {
    const own = ownerKs(j, hr);
    if (own) return own;
    const ref = (j.metadata?.ownerReferences || []).find(r => r.kind === 'CronJob');
    return ref ? ownerKs(byName(f.cronjobs, ref.name), hr) : null;
  }
  const pods = (f.pods?.items || []).filter(p => [lbls(p)['batch.kubernetes.io/job-name'], lbls(p)['job-name']].includes(name));
  if (pods.length) return ownersOf(pods.map(p => ownerKs(p, hr)));
  // a Job the history limit already removed: CronJob Jobs are named <cronjob>-<minutes>
  const m = /^(.+)-\d{6,}$/.exec(name);
  return m ? ownerKs(byName(f.cronjobs, m[1]), hr) : null;
}
// One owner, or null when the candidates disagree or any is unknown.
const ownersOf = list => (list.length && list.every(x => x && x === list[0]) ? list[0] : null);
const isScraper = (p, scope) => (scope.scraper_pod_app_names || []).includes(lbls(p)['app.kubernetes.io/name']);
// The first workload label present names the subject. Returns
// { by: 'label=value' | null, owner: '<ns>/<ks>' | null }; by null = names no workload.
export function alertSubject(metric, f, ks, hr, scope) {
  const fo = scope.flux_object || {};
  const kind = metric[fo.kind_label], fname = metric[fo.name_label];
  const ns = metric.namespace || metric[fo.namespace_label];
  if (!metric.namespace && kind && fname && (fo.kinds || []).includes(kind)) {
    if (kind === 'Kustomization') return { by: `Kustomization ${ns}/${fname}`, owner: (ks?.items || []).some(k => k.metadata.namespace === ns && k.metadata.name === fname) ? `${ns}/${fname}` : null };
    return { by: `HelmRelease ${ns}/${fname}`, owner: ownerKs((hr?.items || []).find(h => h.metadata.namespace === ns && h.metadata.name === fname) || null, hr) };
  }
  for (const key of scope.subject_labels || []) {
    const v = metric[key];
    if (!v) continue;
    const by = `${key}=${v}`;
    if (key === 'job_name') return { by, owner: jobOwner(v, f, hr) };
    if (key === 'cronjob') return { by, owner: ownerKs(byName(f.cronjobs, v), hr) };
    if (key === 'pod') {
      const p = byName(f.pods, v);
      if (p && isScraper(p, scope)) continue; // the exporter's own pod: not the subject
      return { by, owner: p ? podOwner(p, f, hr) : null };
    }
    if (['deployment', 'statefulset', 'daemonset'].includes(key)) {
      const kindOf = { deployment: 'ReplicaSet', statefulset: 'StatefulSet', daemonset: 'DaemonSet' }[key];
      const pods = (f.pods?.items || []).filter(p => (p.metadata?.ownerReferences || []).some(r => r.kind === kindOf && (key === 'deployment' ? r.name === `${v}-${lbls(p)['pod-template-hash']}` : r.name === v)));
      return { by, owner: pods.length ? ownersOf(pods.map(p => podOwner(p, f, hr))) : null };
    }
    if (key === 'persistentvolumeclaim') {
      const pods = (f.pods?.items || []).filter(p => (p.spec?.volumes || []).some(x => x.persistentVolumeClaim?.claimName === v));
      return { by, owner: pods.length ? ownersOf(pods.map(p => podOwner(p, f, hr))) : null };
    }
    // app-style labels: pods carrying that name or instance
    const pods = (f.pods?.items || []).filter(p => [lbls(p)['app.kubernetes.io/name'], lbls(p)['app.kubernetes.io/instance'], lbls(p).app].includes(v));
    return { by, owner: pods.length ? ownersOf(pods.map(p => podOwner(p, f, hr))) : null };
  }
  return { by: null, owner: null };
}
// The alert gate for one target. alerts = ALERTS metrics; scope = fluxScope(); facts(ns)
// = { pods, cronjobs, jobs } of a namespace. Returns { blockers[], unrelated[], cluster[] }.
export function alertGate(alerts, scope, clusterWideIds, facts, ks, hr, alertScope) {
  const out = { blockers: [], unrelated: [], cluster: [] };
  const chain = new Set(scope.chain);
  // the namespaces of the dependsOn chain (each Kustomization's own and its targetNamespace):
  // an alert there blocks when it traces to a chain Kustomization (a Flux failure of volsync,
  // say); anything else there is listed, never a namespace fallback (as before R5)
  const chainNs = new Set();
  for (const id of scope.chain) {
    const [n, nm] = id.split('/');
    chainNs.add(n);
    const k = (ks?.items || []).find(x => x.metadata?.namespace === n && x.metadata?.name === nm);
    if (k?.spec?.targetNamespace) chainNs.add(k.spec.targetNamespace);
  }
  // a Flux alert names a chain Kustomization by its id even when the tick lists no such object
  const fo = alertScope.flux_object || {};
  const withChainFlux = (s, m) => {
    const id = !m.namespace && m[fo.kind_label] === 'Kustomization' && m[fo.name_label] ? `${m[fo.namespace_label]}/${m[fo.name_label]}` : null;
    return !s.owner && id && chain.has(id) ? { ...s, owner: id } : s;
  };
  for (const m of alerts) {
    const name = m.alertname || '(unnamed)';
    const nss = [m.namespace, m[fo.namespace_label]].filter(Boolean);
    if (!nss.length) {
      if (clusterWideIds.length) out.blockers.push(`critical alert ${name} has no namespace label and the target is cluster-wide (${clusterWideIds.join(', ')})`);
      else out.cluster.push(`${name}: no namespace label; the target is not cluster-wide`);
      continue;
    }
    const ns = nss.find(n => scope.namespaces.includes(n));
    if (!ns) {
      const dep = nss.find(n => chainNs.has(n));
      if (!dep) continue;
      const f = facts(dep);
      const d = withChainFlux(alertSubject(m, f, ks, hr, alertScope), m);
      if (d.owner && chain.has(d.owner)) out.blockers.push(`critical alert firing in ${dep}: ${name} (${d.by}) belongs to ${d.owner}, in the target's dependsOn chain`);
      // a workload the tick could not look up because a read failed: fail closed
      else if (!d.owner && d.by && (!f.pods || !f.cronjobs || !f.jobs)) out.blockers.push(`unreadable: pods, Jobs or CronJobs in ${dep} (a dependency's namespace), so ${name} (${d.by}) cannot be traced`);
      else out.unrelated.push(`${name} (${d.by || 'no workload'}) in ${dep}, a dependency's namespace: ${d.owner ? `belongs to ${d.owner}` : 'not traced'}, outside the dependsOn chain`);
      continue;
    }
    const s = withChainFlux(alertSubject(m, facts(ns), ks, hr, alertScope), m);
    if (!s.by) out.blockers.push(`critical alert firing in ${ns}: ${name} names no workload (namespace fallback)`);
    else if (!s.owner) out.blockers.push(`critical alert firing in ${ns}: ${name} (${s.by}) cannot be traced to a Kustomization (fail closed)`);
    else if (chain.has(s.owner)) out.blockers.push(`critical alert firing in ${ns}: ${name} (${s.by}) belongs to ${s.owner}, the target or its dependsOn chain`);
    else out.unrelated.push(`${name} (${s.by}) belongs to ${s.owner}, not to the target`);
  }
  return out;
}

// ── durable state for a target: stateless only on positive evidence ─────────────
const mountsPvc = spec => (spec?.volumes || []).filter(v => v.persistentVolumeClaim || v.ephemeral).map(v => v.persistentVolumeClaim?.claimName || `${v.name} (ephemeral)`);
export function stateEvidence(k, hr, rs, pods, cronjobs) {
  const ns = k.spec?.targetNamespace || k.metadata.namespace;
  const lbl = o => o.metadata?.labels || {};
  const fluxOwned = o => lbl(o)['kustomize.toolkit.fluxcd.io/name'] === k.metadata.name && lbl(o)['kustomize.toolkit.fluxcd.io/namespace'] === k.metadata.namespace;
  const app = k.spec?.postBuild?.substitute?.APP || k.metadata.name;
  const hrNames = new Set((hr?.items || []).filter(fluxOwned).map(h => h.metadata.name));
  const owned = o => o.metadata.namespace === ns && (fluxOwned(o) || hrNames.has(lbl(o)['app.kubernetes.io/instance']) || [app, k.metadata.name].includes(lbl(o)['app.kubernetes.io/name']));
  const evidence = [];
  if (Object.keys(k.spec?.postBuild?.substitute || {}).some(x => x.startsWith('VOLSYNC_'))) evidence.push('VOLSYNC_* substitutions');
  if ((k.spec?.dependsOn || []).some(d => d.name === 'volsync')) evidence.push('dependsOn volsync');
  if ((k.spec?.components || []).some(c => /volsync/.test(c))) evidence.push('volsync component');
  for (const p of (pods?.items || []).filter(owned)) for (const c of mountsPvc(p.spec)) evidence.push(`pod ${p.metadata.name} mounts ${c}`);
  for (const j of (cronjobs?.items || []).filter(owned)) for (const c of mountsPvc(j.spec?.jobTemplate?.spec?.template?.spec)) evidence.push(`cronjob ${j.metadata.name} mounts ${c}`);
  const sources = (rs?.items || []).filter(r => fluxOwned(r));
  return { ks: ksId(k), namespace: ns, evidence: [...new Set(evidence)].slice(0, 6), sources };
}

// ── the tick ─────────────────────────────────────────────────────────────────
async function tick() {
  const rules = L.loadRules(process.env.UPGRADE_RULES || join(HERE, 'upgrade-rules.json'));
  // UPGRADE_NIGHT_NOW (ISO time) pins the clock for a local read-only run (refused above with
  // --write-state or in live mode); the CronJob never sets it.
  const pinned = Date.parse(process.env.UPGRADE_NIGHT_NOW || '');
  const now = Number.isFinite(pinned) ? pinned : Date.now();
  const clock = () => (Number.isFinite(pinned) ? pinned : Date.now());
  const night = rules.night;
  const maxAge = night.backup_rule.max_age_hours * 3600e3;
  const token = tokenPerms();
  log(`upgrade-night ${MODE}: token permissions: ${token.readable ? token.names.join(', ') : 'unreadable'} (as requested by mint-token, ${permFile}); ${token.can_merge ? 'can merge' : `cannot merge: ${token.why}`}`);
  if (MODE === 'live') log(`upgrade-night live: class allowlist: ${LIVE.classes.length ? LIVE.classes.join(', ') : 'empty, so this tick merges nothing'}`);
  const cms = kget(['configmaps', '-n', night.namespace]);
  const cm = name => (cms?.items || []).find(c => c.metadata?.name === name) || null;
  const pause = cm(night.state_configmap)?.data?.pause;
  if (pause) {
    // Pause stops new merges and new lock takes. Work already owed for a merge the lane
    // made (verify, revert) carries on through this hook; it is not built yet.
    const owed = M.owedWork({ mode: MODE, paused: true });
    log(`upgrade-night: PAUSED by ${night.state_configmap} data.pause=${JSON.stringify(String(pause).slice(0, 80))}; no merge, no lock take, nothing read from GitHub or written; owed work: ${owed.note}`);
    return 0;
  }

  const holdRules = L.fetchHoldRules(REPO, run);
  const nums = L.listRenovatePrs(REPO, rules, run);
  const factsByPr = new Map(); // story 549: the record comments and the retry box of each PR
  const records = nums.map(n => {
    try { const f = L.gatherFacts(REPO, n, run); factsByPr.set(n, f); return L.classify(f, rules, { now, holdRules }); }
    catch (e) { return { schema: 'haynes.upgrade-lane/v1', pr: n, lane: 'BLOCKED', class: 'facts-incomplete', reason: `read failed: ${e.message}`, update_type: 'unknown', rules: [], checks: {}, targets: [] }; }
  });

  // STORY 549: the retry ledger (data.retry_ledger) and each record's retry state, before
  // the gates: a failure on the head that follows a retry changes the record's class.
  const rcfg = M.retryConfig(rules);
  const stateCm = cm(night.state_configmap);
  const lg = !cms ? { ok: false, ledger: null, why: 'unreadable: configmaps in upgrade-agent (the retry ledger)' } : M.parseLedger(stateCm?.data?.retry_ledger);
  const ledger = lg.ok ? M.pruneLedger(lg.ledger, nums, now) : null;
  const ledgerBefore = JSON.stringify(lg.ledger);
  const eligibleSet = new Set();
  for (const r of records) {
    const f = factsByPr.get(r.pr);
    const a = !rcfg.ok ? { field: { state: 'unreadable', why: rcfg.why, count_7d: null, limits: {}, history: [] }, eligible: false, block: null, update: null }
      : M.retryAssess({ rec: r, box: f?.retry_box ?? null, entries: ledger ? (ledger.retries[String(r.pr)] || []) : null, cfg: rcfg, now });
    if (ledger && a.update) M.applyRetryUpdate(ledger.retries[String(r.pr)], a.update);
    if (a.block) { r.class = a.block.class; r.reason = a.block.reason; r.findings = [...(r.findings || []), `BLOCKED/${a.block.class}: ${a.block.reason}`]; }
    r.retry = a.field;
    if (a.eligible) eligibleSet.add(r.pr);
  }
  // the PR-write phase below tries them in PR order; the cap counts box writes, not PRs tried
  const retryEligible = records.filter(r => eligibleSet.has(r.pr)).sort((a, b) => a.pr - b.pr);

  const lanes = records.filter(r => r.lane === 'LANE-A');
  const cluster = lanes.some(r => r.cluster_effect === 'immediate');
  const cf = cluster ? {
    ks: kget(['kustomizations.kustomize.toolkit.fluxcd.io', '-A']), hr: kget(['helmreleases.helm.toolkit.fluxcd.io', '-A']),
    rs: kget(['replicationsources.volsync.backube', '-A']), backups: kget(['backups.postgresql.cnpg.io', '-A']),
    schedules: kget(['scheduledbackups.postgresql.cnpg.io', '-A']), ceph: kget(['cephclusters.ceph.rook.io', '-A']),
  } : {};
  const prom = cluster ? await httpGetJson(`${PROM}/api/v1/query?query=${encodeURIComponent('ALERTS{alertstate="firing",severity="critical"}')}`) : null;
  const alerts = Array.isArray(prom?.data?.result) ? prom.data.result.map(r => r.metric || {}) : null;
  const alertScope = rules.alert_scope || {};
  const nsOf = m => m.namespace || m[alertScope.flux_object?.namespace_label] || '(cluster)';
  const alertNs = alerts ? [...new Set(alerts.map(nsOf))] : null;
  const ceph = (cf.ceph?.items || []).map(c => c.status?.ceph?.health || 'unknown').join(',') || 'unknown';
  const nsCache = new Map();
  const nsFacts = ns => { if (!nsCache.has(ns)) nsCache.set(ns, { pods: kget(['pods', '-n', ns]), cronjobs: kget(['cronjobs', '-n', ns]), jobs: kget(['jobs', '-n', ns]) }); return nsCache.get(ns); };
  const win = inWindow(now, night.timezone, night.merge_window_local.first, night.merge_window_local.last);
  // THE SETTLE GATE (story 551): facts about each candidate's target, read once here (the
  // source, every Kustomization and HelmRelease, main's commits Flux has not fetched) and
  // judged per candidate; the merge step reads it again, and live mode once more at the call.
  const settleFacts = cluster ? M.readSettleFacts(REPO, rules, run) : null;
  const scopes = new Map();
  const orders = cm(night.work_orders_configmap);
  const orderBad = [], claimed = [];
  for (const [k, v] of Object.entries(orders?.data || {})) { try { if (JSON.parse(v).status === 'claimed') claimed.push(k); } catch { orderBad.push(k); } }
  const flagged = new Set();

  for (const r of lanes) {
    const blockers = [];
    const backup = [];
    let scope = { namespaces: [], chain: [], blocking: [] };
    let alertView = { unrelated: [], cluster: [] };
    if (!cms) blockers.push('unreadable: configmaps in upgrade-agent (work orders, pause)');
    if (orderBad.length) blockers.push(`unreadable: work order ${orderBad.join(', ')}`);
    if (claimed.length) blockers.push(`work order claimed: ${claimed.join(', ')}`);
    if ((r.checks?.stale || []).length) blockers.push(`checks older than ${rules.check_max_age_days}d need a Renovate retry first`);
    // R7: a main commit to one of the PR's files that is newer than the head's checks
    {
      const files = r.files || [], ref = r.checks?.reference_time;
      if (!files.length) blockers.push('unreadable: the PR files to compare with main');
      else if (!ref) blockers.push("unreadable: when the head's checks ran (needed to compare them with main)");
      else {
        const st = L.checksPredateMain(ref, files, L.mainCommitsTouching(REPO, files, ref, run));
        r.checks.main_newer = st.newer;
        if (st.unreadable.length) blockers.push(`unreadable: main commits since the head's checks for ${st.unreadable.join(', ')}`);
        if (st.newer.length) blockers.push(`checks predate main: ${st.newer.slice(0, 3).map(c => `${c.path} changed by ${String(c.sha).slice(0, 8)} at ${c.at}`).join('; ')}${st.newer.length > 3 ? ` (+${st.newer.length - 3})` : ''}, after the head's checks (${ref}); needs a Renovate rebase first`);
      }
    }
    if (r.cluster_effect === 'immediate') {
      for (const [what, v] of [['kustomizations', cf.ks], ['helmreleases', cf.hr], ['replicationsources', cf.rs], ['CNPG backups', cf.backups], ['CNPG scheduledbackups', cf.schedules]]) if (!v) blockers.push(`unreadable: ${what}`);
      const mine = cf.ks ? r.targets.flatMap(t => { const k = kustomizationsFor(t, cf.ks); if (!k.length) blockers.push(`target-unmapped: no Kustomization applies ${t}`); return k; }) : [];
      if (cf.ks && cf.hr) {
        scope = fluxScope(mine, cf.ks, cf.hr);
        if (scope.blocking.length) blockers.push(`Flux NotReady in scope: ${scope.blocking.slice(0, 5).join(', ')}`);
        scope.blocking.forEach(b => flagged.add(b));
      }
      for (const k of mine) {
        const f = nsFacts(k.spec?.targetNamespace || k.metadata.namespace);
        if (!f.pods || !f.cronjobs) { blockers.push(`unreadable: pods/cronjobs in ${k.spec?.targetNamespace || k.metadata.namespace}`); continue; }
        if (!cf.rs) continue;
        const ev = stateEvidence(k, cf.hr, cf.rs, f.pods, f.cronjobs);
        for (const s of ev.sources) backup.push({ target: ev.ks, ...volsyncFresh(s, now, maxAge) });
        if (!ev.sources.length && ev.evidence.length) blockers.push(`backup-unmatched: ${ev.ks} has durable state (${ev.evidence.join('; ')}) and no ReplicationSource applied by it`);
      }
      if (cf.backups && cf.schedules) for (const c of rules.cnpg_clients) {
        // a namespace-level target (a shared component's consumer) covers the clients below it
        if (!r.targets.some(t => c.paths.some(g => L.matchGlob(`${t}/x`, g) || g.startsWith(t + '/')))) continue;
        backup.push({ target: c.cluster, ...cnpgFresh(c.cluster, cf.schedules.items, cf.backups.items, now, maxAge) });
      }
      for (const b of backup.filter(x => !x.pass)) blockers.push(`backup not fresh: ${b.name} (${b.why})`);
      if (/HEALTH_ERR|unknown/.test(ceph)) blockers.push(cf.ceph ? `Ceph ${ceph}` : 'unreadable: cephclusters');
      if (!alerts) blockers.push('unreadable: critical alerts (Prometheus)');
      else if (cf.ks && cf.hr) {
        // R5: only alerts that belong to the target block (alert_scope in upgrade-rules.json)
        const g = alertGate(alerts, scope, r.cluster_wide || [], nsFacts, cf.ks, cf.hr, alertScope);
        blockers.push(...g.blockers);
        alertView = { unrelated: g.unrelated, cluster: g.cluster };
      }
    }
    let settleRec = { applicable: false, why: 'no immediate cluster effect: nothing in the cluster changes now' };
    if (r.cluster_effect === 'immediate') {
      const sc = M.settleScope(r, cf.ks, cf.hr);
      scopes.set(r.pr, sc);
      const sv = M.settleVerdict(settleFacts, sc, rules);
      blockers.push(...sv.blockers.map(b => `settle gate: ${b}`));
      settleRec = M.settleBrief(sv);
    }
    if (!win.open) blockers.push(`outside the merge window (${night.merge_window_local.first}-${night.merge_window_local.last} ${night.timezone}, now ${win.local})`);
    r.backup = { rule: night.backup_rule.text, targets: backup, stateful: backup.length > 0 };
    r.gates = { namespaces: scope.namespaces, blockers, alerts_not_blocking: alertView, settle: settleRec, would_be_eligible: blockers.length === 0 };
  }

  const fluxWarnings = cluster ? [...(cf.ks?.items || []).filter(isNotReady).map(k => `Kustomization ${ksId(k)}`), ...(cf.hr?.items || []).filter(isNotReady).map(h => `HelmRelease ${h.metadata.namespace}/${h.metadata.name}`)].filter(x => !flagged.has(x)) : [];
  const effOrder = { none: 0, delayed: 1, immediate: 2 };
  const order = (a, b) => (effOrder[a.cluster_effect] - effOrder[b.cluster_effect]) || (a.backup.stateful - b.backup.stateful) || (a.pr - b.pr);
  const eligible = lanes.filter(r => r.gates.would_be_eligible).sort(order);
  // Live mode prefers a PR of an allowlisted class; with none, the first eligible PR goes
  // to the merge step, which refuses it for its class (so the refusal is recorded).
  const pick = (MODE === 'live' ? eligible.find(r => LIVE.classes.includes(r.class)) : null) || eligible[0] || null;
  // Dry-run exercises the merge step on the nearest lane-A candidate when the tick chose
  // none, so every night records a verdict. Live mode runs it on the chosen PR only.
  const nearest = !pick && MODE === 'dryrun' ? [...lanes].sort(order)[0] || null : null;
  const subjectRec = pick || nearest;
  const subject = subjectRec ? { pr: subjectRec.pr, head: subjectRec.head, class: subjectRec.class, chosen: !!pick, scope: subjectRec.cluster_effect === 'immediate' ? (scopes.get(subjectRec.pr) ?? { unreadable: 'the target scope (not computed)' }) : null } : null;

  // THE MERGE STEP AND THE CHANGE LOCK (night-merge.mjs). With --write-state the lock is
  // taken before the step's reads and released before this returns, except after a live
  // merge, when it stays held through verification (story 553: not built; the post-merge
  // lease is the hold). Dry-run never reaches the merge call.
  const holder = `upgrade-night/${process.env.HOSTNAME || 'local'}/${randomBytes(4).toString('hex')}`;
  const windowOpen = t => inWindow(t, night.timezone, night.merge_window_local.first, night.merge_window_local.last);
  const step = M.laneMergeStep({ mode: MODE, subject, rules, run, env: childEnv, clock, liveClasses: LIVE.classes, token, holder, takeAllowed: WRITE_STATE, windowOpen, tickStartedAt: now });
  const verdict = step.verdict || { verdict: 'not-run' };

  const counts = Object.fromEntries(L.LANES.map(l => [l, records.filter(r => r.lane === l).length]));
  const summary = {
    mode: MODE, at: new Date(now).toISOString(), repo: REPO, window: { ...win, tz: night.timezone },
    backup_rule: night.backup_rule.text,
    baseline: { ceph: cluster ? ceph : 'not needed', alerts_read: cluster ? alertNs !== null : 'not needed', critical_alert_namespaces: alertNs ?? [],
      critical_alerts: (alerts || []).map(m => `${m.alertname || '(unnamed)'} @ ${nsOf(m)}${(alertScope.subject_labels || []).filter(k => m[k]).slice(0, 1).map(k => ` ${k}=${m[k]}`).join('')}`).slice(0, 40),
      flux_warnings: fluxWarnings },
    token_permissions: { names: token.names, can_merge: token.can_merge, why: token.why },
    live_classes: LIVE.classes,
    settle: settleFacts ? { read: true, source_revision: settleFacts.source?.status?.artifact?.revision ?? null, unapplied_complete: !!settleFacts.range?.complete, unapplied_why: settleFacts.range?.why ?? null,
      unapplied_commits: (settleFacts.range?.commits || []).map(c => ({ sha: c.sha, pr: c.pr, subject: c.subject })).slice(0, 20) } : { read: false, why: 'no lane-A candidate with an immediate cluster effect' },
    change_lock: step.lock, merge_step: verdict, merge_step_settle: step.settle || null, merge_step_shepherd_jobs: step.shepherd_jobs || null, owed_work: step.owed || M.owedWork({ mode: MODE, paused: false }),
    work_orders_claimed: claimed, counts, lane_a_candidates: lanes.map(r => r.pr),
    would_merge: pick ? { pr: pick.pr, head: pick.head, class: pick.class, reason: MODE === 'live' ? `LIVE: merge step ${verdict.verdict}` : 'DRY-RUN: nothing was merged' } : null,
    why_none: pick ? null : (lanes.length ? 'every lane-A candidate has a blocker (see records[].gates.blockers)' : 'no lane-A candidate'),
  };
  for (const r of records) { r.updated = summary.at; r.preapproval = {}; r.schedule = {}; r.last_attempt = {}; }
  if (subjectRec) subjectRec.last_attempt = { at: summary.at, mode: MODE, chosen: subject.chosen, verdict: verdict.verdict, pr_refusals: verdict.pr_refusals || [], lane_refusals: verdict.lane_refusals || [], merge_call: verdict.merge_call || null };

  // THE HISTORY (story 593): after the merge step, whose lock is released by now. Its
  // failure is logged and recorded; it changes no verdict and stops no other write.
  let hist;
  try { hist = H.historyPhase({ rules, run, env: childEnv, repo: REPO, at: summary.at, records, openPrs: nums, verdict, write: WRITE_STATE }); }
  catch (e) { hist = { summary: { error: String(e.message).slice(0, 300), written: false }, terminals: [] }; }
  summary.history = hist.summary;
  const terminalOf = new Map((hist.terminals || []).map(t => [t.pr, t.terminal]));
  for (const r of records) r.terminal = terminalOf.get(r.pr) ?? null;
  const hLine = H.historyLine(summary.history);
  if (summary.history.error || ['failed', 'conflict'].includes(summary.history.write)) log(`ERROR upgrade-night ${MODE}: HISTORY NOT WRITTEN: ${hLine}${summary.history.raw ? ` (${summary.history.raw})` : ''}`);
  else log(`upgrade-night ${MODE}: history: ${hLine}`);
  delete summary.history.raw;

  // THE PR-WRITE PHASE (story 549): the retry box for at most night.retry.per_tick PRs, then
  // one record per open Renovate PR, mirrored from the records this tick writes to
  // upgrade-lane-state. Only with --write-state, a token with pull_requests:write, a readable
  // ledger with no halt, and no pause (read again here). Never a merge. A local run plans.
  const rcd = M.recordConfig(rules);
  const prWrite = M.prWriteAllowed(token);
  let phaseWhy = !WRITE_STATE ? 'a local run (no --write-state): planned, nothing written'
    : !rcd.ok ? rcd.why : !prWrite.ok ? prWrite.why : !ledger ? `${lg.why}; it also holds the PR-write halt` : ledger.halt ? `halted: ${ledger.halt.why}` : null;
  if (!phaseWhy) phaseWhy = M.pauseRefusal(M.readPause(rules, run), 'before the PR writes');
  // F6/F8 (story 549 review): every ledger write carries the resourceVersion of the last
  // read or write of upgrade-lane-state, and before any GitHub write the tick proves the
  // ledger writable with one such write. A Job that overlaps this one and wrote the state
  // since makes that write fail, and this tick then writes nothing to GitHub; so does a
  // ledger that cannot be written (the halt it may need to hold would be lost).
  let ledgerRv = stateCm?.metadata?.resourceVersion ?? null;
  const persistLedger = l => {
    if (!WRITE_STATE) return false;
    const w = writeLaneState(ledgerManifest(l, ledgerRv), LEDGER_MANAGER);
    if (w.ok && w.rv) ledgerRv = w.rv;
    return w.ok;
  };
  if (!phaseWhy && !persistLedger(ledger)) phaseWhy = 'the retry ledger could not be written at the version this tick read (another writer since, or the write was refused); no GitHub write this tick';
  const phaseLive = !phaseWhy;
  const planOnly = !WRITE_STATE; // a local run reads the facts at the write and says what it would tick
  const attempts = [];
  let stopped = null, retryWrites = 0, boxWrites = 0, preflights = 0, retryHalt = null;
  for (const r of retryEligible) {
    // F1: a PR the facts at the write refuse hands its slot to the next eligible PR; the
    // write-time reads are bounded too, so a long queue cannot spend the tick
    const deferWhy = stopped ? `not tried: ${stopped}`
      : boxWrites >= rcfg.per_tick ? `eligible, but the per-tick cap (${rcfg.per_tick} box write${rcfg.per_tick === 1 ? '' : 's'}) is used this tick`
        : preflights >= rcfg.max_preflights_per_tick ? `eligible, but the ${rcfg.max_preflights_per_tick} write-time reads allowed per tick are used` : null;
    if (deferWhy) { r.retry = { ...r.retry, state: 'deferred', why: deferWhy }; continue; }
    if (!phaseLive && !planOnly) {
      boxWrites++;
      attempts.push({ pr: r.pr, head: r.head, outcome: 'planned', why: phaseWhy });
      r.retry = { ...r.retry, state: 'selected', why: `would read the facts and tick the box: ${r.retry.why}` };
      continue;
    }
    preflights++;
    const res = M.runRetry({ cand: { pr: r.pr, head: r.head, files: r.files || [], failing: r.checks?.failing || [] }, rules, run, env: childEnv, clock, ledger, persistLedger, plan: planOnly });
    if (res.raw_detail) log(`upgrade-night ${MODE}: retry write on #${r.pr} failed: ${res.raw_detail}`);
    delete res.raw_detail;
    attempts.push(res);
    retryWrites += res.writes;
    if (res.writes > 0 || res.outcome === 'would-tick') boxWrites++;
    if (res.outcome === 'write-failed') stopped = `the retry write on #${r.pr} failed (${res.failure})`;
    if (res.halt) retryHalt = res.halt;
    const after = M.retryAssess({ rec: r, box: res.outcome === 'ticked' ? 'ticked' : factsByPr.get(r.pr)?.retry_box ?? null, entries: ledger.retries[String(r.pr)] || [], cfg: rcfg, now });
    r.retry = { ...after.field, state: res.outcome, why: res.outcome === 'ticked' ? `ticked Renovate's retry box on ${String(r.head).slice(0, 8)}${res.why ? `; ${res.why}` : ''}` : res.why };
  }
  const items = records.map(r => ({ rec: r, comments: factsByPr.get(r.pr)?.record_comments ?? null }));
  // story 593 (d): a PR that left the open list with a terminal event: its own record, edited
  // to carry terminal (never a new comment on a closed PR)
  if (rcd.ok && (hist.terminals || []).length) {
    try {
      const t = H.terminalRecordItems(hist.terminals, nums, rcd, summary.at, run, REPO);
      items.push(...t.items);
      if (t.skipped.length) summary.history.terminal_skipped = t.skipped;
    } catch (e) { summary.history.terminal_skipped = [{ why: `the terminal records were not prepared: ${String(e.message).slice(0, 200)}` }]; }
  }
  // F5: the pause is read again just before each record write
  const pauseCheck = () => M.pauseRefusal(M.readPause(rules, run), 'just before a record write');
  const prRecords = rcd.ok ? M.writePrRecords({ items, cfg: rcd, run, env: childEnv, live: phaseLive, stopped, pauseCheck })
    : { github_writes: 0, refused: records.map(r => ({ pr: r.pr, why: rcd.why })) };
  for (const line of prRecords.raw_details || []) log(`upgrade-night ${MODE}: record write failed: ${line}`);
  delete prRecords.raw_details;
  // F4: a denial (or a record written under another identity) is a stop: a durable halt
  const halt = retryHalt || prRecords.halt;
  if (halt && ledger) ledger.halt = { at: new Date(clock()).toISOString(), ...halt };
  summary.github_writes = retryWrites + (prRecords.github_writes || 0);
  summary.pr_records = { written: phaseLive, why_not: phaseWhy, identity: rcd.author?.login ?? null, ...prRecords };
  summary.retry = { ledger: !ledger ? lg.why : lg.absent ? 'absent (a new ledger)' : 'read', limits: rcfg.ok ? { per_head: rcfg.per_head, per_pr: rcfg.per_pr, window_days: rcfg.window_days, per_tick: rcfg.per_tick } : rcfg.why,
    eligible: retryEligible.map(r => r.pr), tried: attempts.map(a => a.pr), attempts, github_writes: retryWrites, max_preflights_per_tick: rcfg.max_preflights_per_tick ?? null,
    by_state: Object.fromEntries([...new Set(records.map(r => r.retry?.state))].map(s => [s, records.filter(r => r.retry?.state === s).map(r => r.pr)])) };

  const table = L.formatTable(records);
  log(`upgrade-night ${MODE}: ${records.length} open Renovate PRs; ${JSON.stringify(counts)}`);
  const sLine = settleFacts ? `Flux at ${String(M.revisionSha(settleFacts.source?.status?.artifact?.revision) || 'unreadable').slice(0, 8)}, ${settleFacts.range?.complete ? `${settleFacts.range.commits.length} commit(s) on main not fetched yet` : `unapplied commits unreadable (${settleFacts.range?.why || 'no source revision'})`}; ${lanes.filter(r => r.cluster_effect === 'immediate').map(r => `#${r.pr} ${r.gates.settle.settled ? 'settled' : `NOT settled (${r.gates.settle.blockers.join(' | ')})`}`).join('; ')}` : 'not read (no lane-A candidate with an immediate cluster effect)';
  log(`upgrade-night ${MODE}: settle gate: ${sLine}`);
  if (step.shepherd_jobs) log(`upgrade-night ${MODE}: F4 shepherd Jobs: ${step.shepherd_jobs.readable ? (step.shepherd_jobs.active.length ? step.shepherd_jobs.why : 'none that can merge is running') : step.shepherd_jobs.why}`);
  const lk = step.lock;
  log(`upgrade-night ${MODE}: change lock ${lk.configmap}: ${lk.outcome} (before: ${lk.before?.state ?? 'not read'}${lk.before?.holder ? ` by ${lk.before.holder}` : ''}${lk.took_over_from ? `; took over ${lk.took_over_from}, phase ${lk.took_over_phase || 'unknown'}` : ''})${lk.taken_at ? ` taken_at=${lk.taken_at} expires_at=${lk.expires_at}` : ''}${lk.released_at ? ` released_at=${lk.released_at}` : ''}${lk.why ? `; ${lk.why}` : ''}`);
  const refusals = [...(verdict.pr_refusals || []), ...(verdict.lane_refusals || [])];
  // story 773: how many reads GitHub took to answer the PR's mergeability (summary.merge_step.facts.mergeable_reads)
  const reads = Number.isInteger(verdict.facts?.mergeable_reads) ? `; mergeability ${verdict.facts.mergeable}/${verdict.facts.merge_state} after ${verdict.facts.mergeable_reads} read${verdict.facts.mergeable_reads === 1 ? '' : 's'}` : '';
  const stepLine = subject ? `#${subject.pr} ${String(subject.head).slice(0, 8)} (${subject.class}${subject.chosen ? '' : ', not chosen'}): ${String(verdict.verdict).toUpperCase()}${refusals.length ? `: ${refusals.join(' | ')}` : ''}; merge call: ${verdict.merge_call || 'none'}${reads}` : `not run (${verdict.why})`;
  log(`upgrade-night ${MODE}: merge step ${stepLine}`);
  process.stdout.write(table + '\n');
  for (const r of lanes) process.stdout.write(`#${r.pr} gates: ${r.gates.blockers.length ? r.gates.blockers.join(' | ') : 'all pass'}\n`);
  if (fluxWarnings.length) process.stdout.write(`warning: Flux NotReady outside every candidate's scope: ${fluxWarnings.join(', ')}\n`);
  process.stdout.write(`settle gate: ${settleFacts ? lanes.filter(r => r.cluster_effect === 'immediate').map(r => `#${r.pr} ${r.gates.settle.settled ? 'settled' : `NOT settled (${r.gates.settle.blockers.length} blocker${r.gates.settle.blockers.length === 1 ? '' : 's'})`}`).join(', ') : 'not read'}\n`);
  process.stdout.write(`change lock: ${lk.outcome}\n`);
  process.stdout.write(`merge step: ${stepLine}\n`);
  process.stdout.write(`would merge this tick: ${pick ? `#${pick.pr} (${pick.class})` : `none (${summary.why_none})`}\n`);
  // story 549: what the PR-write phase did (summary.pr_records, summary.retry)
  const pr = summary.pr_records, list = a => (a || []).map(x => `#${x.pr ?? x}`).join(', ');
  const recLine = pr.written
    ? `${pr.github_writes || 0} written (created ${list(pr.created) || 'none'}; updated ${list(pr.updated) || 'none'}; demoted ${list(pr.demoted) || 'none'}), ${(pr.current || []).length} current${(pr.refused || []).length ? `, refused ${pr.refused.map(x => `#${x.pr} (${x.why})`).join('; ')}` : ''}${(pr.failed || []).length ? `, FAILED ${pr.failed.map(x => `#${x.pr} (${x.why})`).join('; ')}` : ''}${(pr.not_attempted || []).length ? `, not attempted ${list(pr.not_attempted)}` : ''}${(pr.foreign_ignored || []).length ? `; ignored marker comments by other authors on ${list(pr.foreign_ignored)}` : ''}`
    : `none written (${pr.why_not}); would create ${list(pr.would_create) || 'none'}, would update ${list(pr.would_update) || 'none'}, ${(pr.current || []).length} current${(pr.refused || []).length ? `, refused ${pr.refused.map(x => `#${x.pr} (${x.why})`).join('; ')}` : ''}`;
  log(`upgrade-night ${MODE}: PR records: ${recLine}`);
  const rt = summary.retry;
  const rtLine = `${rt.attempts.length ? rt.attempts.map(a => `#${a.pr} ${String(a.head).slice(0, 8)} ${String(a.outcome).toUpperCase()}${a.why ? ` (${a.why})` : ''}`).join('; ') : 'none attempted'}; eligible ${list(rt.eligible) || 'none'}; blocked after a retry ${list([...(rt.by_state['failed-again'] || []), ...(rt.by_state['no-new-head'] || [])]) || 'none'}; waiting for Renovate ${list(rt.by_state['waiting-for-renovate']) || 'none'}; ledger ${rt.ledger}`;
  log(`upgrade-night ${MODE}: retry: ${rtLine}`);
  log(`upgrade-night ${MODE}: GitHub writes this tick: ${summary.github_writes} (no merge in dry-run)`);
  process.stdout.write(`PR records: ${recLine}\nretry: ${rtLine}\n`);
  process.stdout.write(`history: ${hLine}\n`);

  if (!WRITE_STATE) { log('state not written (no --write-state); this was a local run'); return 0; }
  // the ledger first (its own field manager), then the tick's state
  if (ledger && JSON.stringify(ledger) !== ledgerBefore) summary.retry.ledger_written = persistLedger(ledger);
  const ok = writeLaneState(stateManifest({ mode: MODE, updated: summary.at, summary: JSON.stringify(summary), table, records: JSON.stringify(records) })).ok;
  return ok ? 0 : 2;
}

const stateManifest = data => ({ apiVersion: 'v1', kind: 'ConfigMap', metadata: { ...STATE, labels: { 'app.kubernetes.io/name': 'upgrade-shepherd', 'app.kubernetes.io/component': 'upgrade-night' } }, data });
// story 549: the retry ledger, data.retry_ledger, under its own field manager, so that the
// tick's apply (which owns every other key it writes, and drops what it leaves out, as the
// error record does) never removes it.
const LEDGER_MANAGER = 'upgrade-night-ledger';
// rv = the resourceVersion of the last read or write of the ConfigMap: a precondition, so a
// writer that overlapped since makes this write fail (F8).
const ledgerManifest = (l, rv) => ({ apiVersion: 'v1', kind: 'ConfigMap', metadata: { ...STATE, ...(rv ? { resourceVersion: String(rv) } : {}) }, data: { retry_ledger: JSON.stringify(l) } });

// THE ONLY CLUSTER WRITE: one ConfigMap, fixed name and namespace, runtime state like
// upgrade-orphan-report (not Flux-managed). Server-side apply: no last-applied
// annotation (256 KiB cap), and keys other managers set (data.pause) are kept. Field
// manager upgrade-night for the tick's keys; upgrade-night-ledger for data.retry_ledger only.
function writeLaneState(manifest, manager = 'upgrade-night') {
  const m = manifest.metadata || {};
  if (manifest.kind !== 'ConfigMap' || m.name !== STATE.name || m.namespace !== STATE.namespace || Object.keys(manifest).some(k => !['apiVersion', 'kind', 'metadata', 'data'].includes(k))
    || !['upgrade-night', LEDGER_MANAGER].includes(manager) || ((manager === LEDGER_MANAGER) !== (Object.keys(manifest.data || {}).join() === 'retry_ledger'))) {
    throw new Error('writeLaneState: refused (only ConfigMap upgrade-agent/upgrade-lane-state may be written, and data.retry_ledger only by its own field manager)');
  }
  const body = JSON.stringify(manifest);
  if (Buffer.byteLength(body) > MAX_STATE_BYTES) throw new Error(`state is ${Buffer.byteLength(body)} bytes, over the ${MAX_STATE_BYTES}-byte budget for one ConfigMap`);
  const r = spawnSync('kubectl', ['apply', '--server-side', `--field-manager=${manager}`, '--force-conflicts', '-o', 'json', '-f', '-'], { input: body, encoding: 'utf8', env: childEnv, maxBuffer: 64 * 1024 * 1024 });
  if (r.status !== 0) { log(`ERROR upgrade-night: STATE WRITE FAILED for ${STATE.namespace}/${STATE.name}${manager === LEDGER_MANAGER ? ' (data.retry_ledger)' : ''} (kubectl exit ${r.status}): ${String(r.stderr).slice(0, 300)}`); return { ok: false, rv: null }; }
  let rv = null;
  try { rv = JSON.parse(r.stdout)?.metadata?.resourceVersion ?? null; } catch { rv = null; }
  log(`wrote ConfigMap ${STATE.namespace}/${STATE.name}${manager === LEDGER_MANAGER ? ' (data.retry_ledger)' : ''}`);
  return { ok: true, rv };
}

// Exit codes: 0 = tick done (or paused); 2 = the state write failed, the one failure
// the ConfigMap cannot record; 1 = the tick failed and its error record could not be
// written either. A tick that fails otherwise writes data.error and exits 0, because
// a Failed pod in upgrade-agent is what the gate and triage read as a regression (see
// the runbook, "When a tick fails").
if (L.isMain(import.meta.url)) {
  tick().then(code => process.exit(code)).catch(e => {
    const at = new Date().toISOString();
    log(`upgrade-night: FAILED: ${e.message}`);
    const written = WRITE_STATE && writeLaneState(stateManifest({ mode: MODE, updated: at, error: String(e.message).slice(0, 2000), summary: JSON.stringify({ mode: MODE, at, error: String(e.message).slice(0, 500), would_merge: null }) })).ok;
    process.exit(written ? 0 : 1);
  });
}
