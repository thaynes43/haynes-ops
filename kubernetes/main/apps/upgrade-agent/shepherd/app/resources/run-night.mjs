#!/usr/bin/env node
// run-night.mjs — the upgrade-night tick (OP512 rollout step 1: DRY-RUN ONLY).
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
// DRY-RUN GUARANTEE. This build has no live mode. Any UPGRADE_NIGHT_MODE other than
// "dryrun" is refused before any read. Every command goes through runReadOnly()
// (gh pr list/view, gh api GET, kubectl get); the only write is writeLaneState(),
// which server-side-applies one ConfigMap named upgrade-lane-state in upgrade-agent
// and only when --write-state is passed (the CronJob passes it; a local run does
// not). The minted GitHub token is read-only as well (see the CronJob's mint-token
// env). scripts/upgrade-agent/lane-night-dryrun-selftest.mjs fails if any mutating
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
const STATE = { name: 'upgrade-lane-state', namespace: 'upgrade-agent' };
const MAX_STATE_BYTES = 768 * 1024; // the API server caps an object at ~1 MiB; keep headroom
const log = (...a) => console.error(new Date().toISOString(), ...a);

// gh reads the installation token minted by the initContainer. Never logged.
const childEnv = { ...process.env };
const tokenFile = process.env.GH_TOKEN_FILE || '/creds/gh_token';
if (existsSync(tokenFile)) childEnv.GH_TOKEN = readFileSync(tokenFile, 'utf8').trim();
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
const ksId = k => `${k.metadata.namespace}/${k.metadata.name}`;
export function kustomizationsFor(target, ks) {
  return (ks?.items || []).filter(k => {
    const p = String(k.spec?.path || '').replace(/^\.\//, '').replace(/\/$/, '');
    return p === target || p.startsWith(target + '/');
  });
}
const isNotReady = o => !o.spec?.suspend && !(o.status?.conditions || []).some(c => c.type === 'Ready' && c.status === 'True');
// Blocking = NotReady and (the target, in its dependsOn chain, or in its namespace).
export function fluxScope(mine, ks, hr) {
  const byId = new Map((ks?.items || []).map(k => [ksId(k), k]));
  const chain = new Set(), queue = mine.map(ksId);
  while (queue.length) {
    const id = queue.shift();
    if (chain.has(id)) continue;
    chain.add(id);
    for (const d of byId.get(id)?.spec?.dependsOn || []) queue.push(`${d.namespace || id.split('/')[0]}/${d.name}`);
  }
  const nss = new Set(mine.map(k => k.spec?.targetNamespace || k.metadata.namespace));
  const blocking = [];
  for (const k of ks?.items || []) if (isNotReady(k) && (chain.has(ksId(k)) || nss.has(k.spec?.targetNamespace || k.metadata.namespace))) blocking.push(`Kustomization ${ksId(k)}`);
  for (const h of hr?.items || []) if (isNotReady(h) && nss.has(h.metadata.namespace)) blocking.push(`HelmRelease ${h.metadata.namespace}/${h.metadata.name}`);
  return { namespaces: [...nss], chain: [...chain], blocking };
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
  // UPGRADE_NIGHT_NOW (ISO time) pins the clock for the selftest; the CronJob never sets it.
  const now = Date.parse(process.env.UPGRADE_NIGHT_NOW || '') || Date.now();
  const night = rules.night;
  const maxAge = night.backup_rule.max_age_hours * 3600e3;
  const cms = kget(['configmaps', '-n', night.namespace]);
  const cm = name => (cms?.items || []).find(c => c.metadata?.name === name) || null;
  const pause = cm(night.state_configmap)?.data?.pause;
  if (pause) { log(`upgrade-night: PAUSED by ${night.state_configmap} data.pause=${JSON.stringify(String(pause).slice(0, 80))}; nothing read or written`); return 0; }

  const holdRules = L.fetchHoldRules(REPO, run);
  const nums = L.listRenovatePrs(REPO, rules, run);
  const records = nums.map(n => {
    try { return L.classify(L.gatherFacts(REPO, n, run), rules, { now, holdRules }); }
    catch (e) { return { schema: 'haynes.upgrade-lane/v1', pr: n, lane: 'BLOCKED', class: 'facts-incomplete', reason: `read failed: ${e.message}`, update_type: 'unknown', rules: [], checks: {}, targets: [] }; }
  });

  const lanes = records.filter(r => r.lane === 'LANE-A');
  const cluster = lanes.some(r => r.cluster_effect === 'immediate');
  const cf = cluster ? {
    ks: kget(['kustomizations.kustomize.toolkit.fluxcd.io', '-A']), hr: kget(['helmreleases.helm.toolkit.fluxcd.io', '-A']),
    rs: kget(['replicationsources.volsync.backube', '-A']), backups: kget(['backups.postgresql.cnpg.io', '-A']),
    schedules: kget(['scheduledbackups.postgresql.cnpg.io', '-A']), ceph: kget(['cephclusters.ceph.rook.io', '-A']),
  } : {};
  const prom = cluster ? await httpGetJson(`${PROM}/api/v1/query?query=${encodeURIComponent('ALERTS{alertstate="firing",severity="critical"}')}`) : null;
  const alertNs = Array.isArray(prom?.data?.result) ? [...new Set(prom.data.result.map(r => r.metric?.namespace || '(cluster)'))] : null;
  const ceph = (cf.ceph?.items || []).map(c => c.status?.ceph?.health || 'unknown').join(',') || 'unknown';
  const nsCache = new Map();
  const nsFacts = ns => { if (!nsCache.has(ns)) nsCache.set(ns, { pods: kget(['pods', '-n', ns]), cronjobs: kget(['cronjobs', '-n', ns]) }); return nsCache.get(ns); };
  const win = inWindow(now, night.timezone, night.merge_window_local.first, night.merge_window_local.last);
  const orders = cm(night.work_orders_configmap);
  const orderBad = [], claimed = [];
  for (const [k, v] of Object.entries(orders?.data || {})) { try { if (JSON.parse(v).status === 'claimed') claimed.push(k); } catch { orderBad.push(k); } }
  const flagged = new Set();

  for (const r of lanes) {
    const blockers = [];
    const backup = [];
    let scope = { namespaces: [], chain: [], blocking: [] };
    if (!cms) blockers.push('unreadable: configmaps in upgrade-agent (work orders, pause)');
    if (orderBad.length) blockers.push(`unreadable: work order ${orderBad.join(', ')}`);
    if (claimed.length) blockers.push(`work order claimed: ${claimed.join(', ')}`);
    if ((r.checks?.stale || []).length) blockers.push(`checks older than ${rules.check_max_age_days}d need a Renovate retry first`);
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
        if (!r.targets.some(t => c.paths.some(g => L.matchGlob(`${t}/x`, g)))) continue;
        backup.push({ target: c.cluster, ...cnpgFresh(c.cluster, cf.schedules.items, cf.backups.items, now, maxAge) });
      }
      for (const b of backup.filter(x => !x.pass)) blockers.push(`backup not fresh: ${b.name} (${b.why})`);
      if (/HEALTH_ERR|unknown/.test(ceph)) blockers.push(cf.ceph ? `Ceph ${ceph}` : 'unreadable: cephclusters');
      if (!alertNs) blockers.push('unreadable: critical alerts (Prometheus)');
      else { const hot = alertNs.filter(n => scope.namespaces.includes(n)); if (hot.length) blockers.push(`critical alert firing in ${hot.join(', ')}`); }
    }
    if (!win.open) blockers.push(`outside the merge window (${night.merge_window_local.first}-${night.merge_window_local.last} ${night.timezone}, now ${win.local})`);
    r.backup = { rule: night.backup_rule.text, targets: backup, stateful: backup.length > 0 };
    r.gates = { namespaces: scope.namespaces, blockers, would_be_eligible: blockers.length === 0 };
  }

  const fluxWarnings = cluster ? [...(cf.ks?.items || []).filter(isNotReady).map(k => `Kustomization ${ksId(k)}`), ...(cf.hr?.items || []).filter(isNotReady).map(h => `HelmRelease ${h.metadata.namespace}/${h.metadata.name}`)].filter(x => !flagged.has(x)) : [];
  const effOrder = { none: 0, delayed: 1, immediate: 2 };
  const eligible = lanes.filter(r => r.gates.would_be_eligible)
    .sort((a, b) => (effOrder[a.cluster_effect] - effOrder[b.cluster_effect]) || (a.backup.stateful - b.backup.stateful) || (a.pr - b.pr));
  const pick = eligible[0] || null;
  const counts = Object.fromEntries(L.LANES.map(l => [l, records.filter(r => r.lane === l).length]));
  const summary = {
    mode: MODE, at: new Date(now).toISOString(), repo: REPO, window: { ...win, tz: night.timezone },
    backup_rule: night.backup_rule.text,
    baseline: { ceph: cluster ? ceph : 'not needed', alerts_read: cluster ? alertNs !== null : 'not needed', critical_alert_namespaces: alertNs ?? [], flux_warnings: fluxWarnings },
    change_lock: 'not-implemented', work_orders_claimed: claimed, counts, lane_a_candidates: lanes.map(r => r.pr),
    would_merge: pick ? { pr: pick.pr, head: pick.head, class: pick.class, reason: 'DRY-RUN: nothing was merged' } : null,
    why_none: pick ? null : (lanes.length ? 'every lane-A candidate has a blocker (see records[].gates.blockers)' : 'no lane-A candidate'),
  };
  for (const r of records) { r.updated = summary.at; r.preapproval = {}; r.schedule = {}; r.last_attempt = {}; }

  const table = L.formatTable(records);
  log(`upgrade-night ${MODE}: ${records.length} open Renovate PRs; ${JSON.stringify(counts)}`);
  process.stdout.write(table + '\n');
  for (const r of lanes) process.stdout.write(`#${r.pr} gates: ${r.gates.blockers.length ? r.gates.blockers.join(' | ') : 'all pass'}\n`);
  if (fluxWarnings.length) process.stdout.write(`warning: Flux NotReady outside every candidate's scope: ${fluxWarnings.join(', ')}\n`);
  process.stdout.write(`change lock: not-implemented (planned for the live step)\n`);
  process.stdout.write(`would merge this tick: ${pick ? `#${pick.pr} (${pick.class})` : `none (${summary.why_none})`}\n`);

  if (!WRITE_STATE) { log('state not written (no --write-state); this was a local run'); return 0; }
  const ok = writeLaneState(stateManifest({ mode: MODE, updated: summary.at, summary: JSON.stringify(summary), table, records: JSON.stringify(records) }));
  return ok ? 0 : 2;
}

const stateManifest = data => ({ apiVersion: 'v1', kind: 'ConfigMap', metadata: { ...STATE, labels: { 'app.kubernetes.io/name': 'upgrade-shepherd', 'app.kubernetes.io/component': 'upgrade-night' } }, data });

// THE ONLY WRITE: one ConfigMap, fixed name and namespace, runtime state like
// upgrade-orphan-report (not Flux-managed). Server-side apply: no last-applied
// annotation (256 KiB cap), and keys other managers set (data.pause) are kept.
function writeLaneState(manifest) {
  const m = manifest.metadata || {};
  if (manifest.kind !== 'ConfigMap' || m.name !== STATE.name || m.namespace !== STATE.namespace || Object.keys(manifest).some(k => !['apiVersion', 'kind', 'metadata', 'data'].includes(k))) {
    throw new Error('writeLaneState: refused (only ConfigMap upgrade-agent/upgrade-lane-state may be written)');
  }
  const body = JSON.stringify(manifest);
  if (Buffer.byteLength(body) > MAX_STATE_BYTES) throw new Error(`state is ${Buffer.byteLength(body)} bytes, over the ${MAX_STATE_BYTES}-byte budget for one ConfigMap`);
  const r = spawnSync('kubectl', ['apply', '--server-side', '--field-manager=upgrade-night', '--force-conflicts', '-f', '-'], { input: body, encoding: 'utf8', env: childEnv });
  if (r.status !== 0) { log(`ERROR upgrade-night: STATE WRITE FAILED for ${STATE.namespace}/${STATE.name} (kubectl exit ${r.status}): ${String(r.stderr).slice(0, 300)}`); return false; }
  log(`wrote ConfigMap ${STATE.namespace}/${STATE.name}`);
  return true;
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
    const written = WRITE_STATE && writeLaneState(stateManifest({ mode: MODE, updated: at, error: String(e.message).slice(0, 2000), summary: JSON.stringify({ mode: MODE, at, error: String(e.message).slice(0, 500), would_merge: null }) }));
    process.exit(written ? 0 : 1);
  });
}
