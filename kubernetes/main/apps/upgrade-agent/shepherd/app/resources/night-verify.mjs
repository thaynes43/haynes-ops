#!/usr/bin/env node
// night-verify.mjs — the read-only verifier of the night lane (story 553).
//
// Story: https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/553
// Runbook: .agents/runbooks/upgrade-night-lane.md, "The verifier (story 553)".
// Format: .agents/reference/upgrade-records.md. Node 24 only, no dependencies.
//
// WHAT IT DOES. On ticks after a merge it reads, read-only, whether the rollout is healthy,
// and gives one verdict per merged update: verified, failed (with the failing facts), not-yet
// (with what it waits for, as a fact) or cannot-tell (with the facts it could not read). Each
// fact has its own result: pass, fail, waiting, unreadable or not-applicable. An unreadable
// fact never yields a pass.
//
// ONE PRINCIPLE (the review of PR 4880): "failed, the update's own regression" needs positive
// evidence that the update did it. Where the evidence is only absence, or points elsewhere,
// the verdict is not-yet or cannot-tell, shown in the morning view with what it waits for and
// since when, and marked "needs a person" once it stays open on night.verify.needs_person_after_nights
// of the lane's own nights. The update's containers are the ones whose image is a package of
// the update (init containers included), or, for a chart, every container of the workloads its
// HelmRelease renders: a crash, a restart or a container error elsewhere is cannot-tell.
//
//   source        Flux's GitRepository revision contains the merge commit.
//   applied       the target's own Kustomizations applied a revision that contains it and are
//                 Ready; its dependsOn chain and the Kustomizations that apply it are settled
//                 (the settle facts of story 551: night-merge.mjs settleScope, fluxStates).
//   helmreleases  the target's HelmReleases are settled and their last release is deployed; one
//                 failing on two reads at least min_stable_seconds apart is not-applied.
//   pods          in pods made since the merge, the update's containers are not in
//                 CrashLoopBackOff or failing to pull; a CreateContainer(Config)Error or
//                 RunContainerError on two reads min_stable_seconds apart fails; a Pending pod
//                 is cannot-tell with the scheduler's reason.
//   alerts        no critical alert that belongs to the target or its chain started firing at
//                 or after the merge (R5's tracing, run-night.mjs alertSubject); one whose start
//                 cannot be told from a Prometheus restart is cannot-tell.
//   image         the update's containers run the new tag or digest (digest or tag decided by
//                 the update type the history recorded), a CronJob template names it, or its
//                 HelmRelease runs the new chart version.
//   exercised     a CronJob whose template names the update: a Job from the new template made
//                 since the merge has completed (failed: fail; none yet: not-yet).
//   rollout       each Deployment, StatefulSet and DaemonSet of the target (from the
//                 Kustomization and HelmRelease inventories) has pods of one template only,
//                 none terminating, all Ready.
//   stable        two ticks at least min_stable_seconds apart on the tick's own clock: when the
//                 rollout is complete the pods are recorded (uid, node, restarts per container);
//                 later the same pods are Ready with no more restarts of the update's
//                 containers. A workload whose pods are replaced max_replacements times fails
//                 as churn.
//   pin           an own image whose digest pin Renovate auto-merges (own_image_pin_wait):
//                 verified only through its pin, the history item that pins this image after
//                 its build finished; never failed for the pin's absence alone.
//
// OTHER CHANGES (Tom's ruling Q-548-04). Every verdict lists the other commits on main between
// the merge and the verdict that reach the target, its chain or a cluster-wide part
// (night-merge.mjs otherChanges, story 551), and the earlier merges to the same scope whose own
// verdict is not final yet; or says there were none. A passing verdict stands whatever else
// merged. A failing verdict with such a change, or with that list unreadable, is "cannot tell
// which change did it"; with none it is the update's own regression.
//
// PRACTICE. The lane is in dry-run and merges nothing, so the verifier practises on merges
// the lane did not make: items the history (ConfigMap upgrade-ledger) holds in
// merged_unverified whose merge is at or after the verifier was installed, the committer date
// of the first commit on main that added this file (a fact in git, read once and kept). A
// practice verdict goes to upgrade-lane-state only (data.verify, data.verify_table, written by
// run-night.mjs under its own field manager): it never writes the history, never opens a pull
// request, never reverts, and never changes a merge verdict, the change lock, the PR records
// or the tick's own state keys. Its record shows the events it WOULD write, checked against
// the item with record-format.mjs applyEvent.
//
// THE LANE'S OWN MERGES. A merge the lane made (the history's merged event by actor lane)
// gets its verdict as history events: verified, verification-failed (own regression),
// escalated (cannot tell which change did it) or awaiting-pin, through historyPhase. Only
// when night.verify.write_history_events is true in upgrade-rules.json, which ships false.
//
// READS ONLY. Every command goes through the read-only runner it is given (lane-classify.mjs
// runReadOnly: gh pr view, gh api GET, kubectl get); Prometheus through GETs. Nothing here
// starts a process of its own or writes anything; the CLI below prints, and with --state it
// keeps its two-tick memory in a local file. No read starts once the tick has run
// time_budget_seconds: the verifier then stops for the tick and says so.
//
// WHAT PODS ALONE CANNOT TELL. The lane's ServiceAccount (ClusterRole upgrade-health-gate)
// reads pods but nothing in apps: no Deployment, StatefulSet, DaemonSet or ReplicaSet. So a
// workload with no running pod (scaled to zero, or not created yet) is unreadable, and a
// controller that has not created all its replicas yet cannot be told from a finished one.
// The missing rule is get and list on apps deployments, statefulsets, daemonsets and
// replicasets (story 755).
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import * as L from './lane-classify.mjs';
import * as M from './night-merge.mjs';
import * as H from './upgrade-history.mjs';
import * as RF from './record-format.mjs';

export const VERIFY_SCHEMA = 'haynes.upgrade-verify/v1';
export const MODULE_PATH = 'kubernetes/main/apps/upgrade-agent/shepherd/app/resources/night-verify.mjs';
export const VERDICTS = Object.freeze(['verified', 'failed', 'not-yet', 'cannot-tell']);
export const RESULTS = Object.freeze(['pass', 'fail', 'waiting', 'unreadable', 'not-applicable']);
// the failure modes: the format's six (upgrade-states.v1.json) and four the story's facts add
// that the format's text does not list (open points in upgrade-records.md)
export const CLASSES = Object.freeze(['not-applied', 'not-ready', 'wrong-image', 'restart-growth', 'critical-alert', 'pin-timeout', 'crash-loop', 'image-pull', 'job-failed', 'churn']);
const SHA = /^[0-9a-f]{40}$/;
const WORKLOAD_KINDS = ['Deployment', 'StatefulSet', 'DaemonSet'];
const PULL_FAILURES = ['ErrImagePull', 'ImagePullBackOff', 'InvalidImageName', 'ErrImageNeverPull'];
const START_FAILURES = ['CreateContainerConfigError', 'CreateContainerError', 'RunContainerError'];
const short = s => String(s || '').slice(0, 8);
const iso = v => new Date(typeof v === 'number' ? v : Date.parse(v)).toISOString();
const parse = s => { try { return JSON.parse(s); } catch { return null; } };
const tryRun = (run, cmd, argv) => { try { return run(cmd, argv, { allowFail: true }); } catch { return null; } };
const lbl = o => o?.metadata?.labels || {};
const uniq = a => [...new Set(a)];
const wkey = w => `${w.kind}/${w.ns}/${w.name}`;
const ago = (now, at) => `${Math.round((now - Date.parse(at)) / 1000)} s`;

// ── config: night.verify in upgrade-rules.json ─────────────────────────────────────
export function verifyConfig(rules) {
  const c = rules?.night?.verify || {};
  const int = (v, lo, hi) => Number.isInteger(v) && v >= lo && v <= hi;
  const ok = typeof c.practice === 'boolean' && typeof c.write_history_events === 'boolean' && int(c.max_items_per_tick, 1, 50) && int(c.max_items_kept, 1, 300)
    && int(c.max_range_commits, 1, 250) && int(c.time_budget_seconds, 10, 400) && int(c.min_stable_seconds, 60, 3600) && int(c.needs_person_after_nights, 1, 14) && int(c.needs_person_min_ticks, 1, 100)
    && int(c.max_replacements, 2, 20) && int(c.prometheus_restart_slack_seconds, 30, 1800) && int(c.max_state_bytes, 8192, 262144);
  return { ok, ...c, why: ok ? null : 'night.verify in upgrade-rules.json is invalid (practice and write_history_events booleans; max_items_per_tick 1 to 50; max_items_kept 1 to 300; max_range_commits 1 to 250; time_budget_seconds 10 to 400; min_stable_seconds 60 to 3600; needs_person_after_nights 1 to 14; needs_person_min_ticks 1 to 100; max_replacements 2 to 20; prometheus_restart_slack_seconds 30 to 1800; max_state_bytes 8192 to 262144)' };
}

// ── images ──────────────────────────────────────────────────────────────────────
// A registry path as one name: docker.io and library/ dropped, lower case.
export const normRepo = r => { let x = String(r || '').trim().toLowerCase().replace(/^(index\.|registry-1\.)?docker\.io\//, ''); if (x.startsWith('library/')) x = x.slice(8); return x; };
// <repo>[:<tag>][@<digest>] -> { repo, tag, digest }; null for an empty reference.
export function parseImage(ref) {
  const s = String(ref || '').trim();
  if (!s) return null;
  const at = s.indexOf('@');
  const rest = at >= 0 ? s.slice(0, at) : s;
  const digest = at >= 0 ? s.slice(at + 1) : null;
  const slash = rest.lastIndexOf('/'), colon = rest.lastIndexOf(':');
  const tag = colon > slash ? rest.slice(colon + 1) : null;
  return { repo: normRepo(colon > slash ? rest.slice(0, colon) : rest), tag: tag || null, digest: digest || null };
}
const digestOf = id => { const m = /sha256:([0-9a-f]{64})/.exec(String(id || '')); return m ? m[1] : null; };
// Digest or tag, from the update type the history recorded (H1: never from the characters; a
// short digest of digits only is still a digest). 'either' for a mixed or unknown type: a
// match by either reading is a match, a mismatch needs both.
export function versionKindOf(type) {
  const t = String(type || '').split('+').filter(Boolean);
  if (!t.length || t.includes('unknown')) return 'either';
  const d = t.filter(x => x === 'digest' || x === 'pinDigest').length;
  return d === t.length ? 'digest' : d === 0 ? 'tag' : 'either';
}
// Does a container run version `to` of its image? true | false | null (cannot tell).
export function versionMatches(c, to, kind = 'either') {
  if (to === null || to === undefined || to === '') return null;
  const want = String(to).replace(/^sha256:/, '');
  const hex = /^[0-9a-f]{7,64}$/.test(want);
  const byDigest = () => { const have = [digestOf(c.digest), digestOf(c.image_id)].filter(Boolean); return have.length ? have.some(h => h.startsWith(want)) : null; };
  const byTag = () => (c.tag ? c.tag === to || L.bareVersion(c.tag) === L.bareVersion(to) : null);
  if (kind === 'digest') return hex ? byDigest() : null;
  if (kind === 'tag' || !hex) return byTag();
  const a = byDigest(), b = byTag();
  return a === true || b === true ? true : a === false && b === false ? false : null;
}

// ── inventories and workloads ─────────────────────────────────────────────────────
// Flux writes an inventory entry id as <namespace>_<name>_<group>_<kind>; Kubernetes names
// carry no underscore. null = no inventory to read.
export function inventoryOf(o) {
  const e = o?.status?.inventory?.entries;
  if (!Array.isArray(e)) return null;
  return e.map(x => String(x?.id || '').split('_')).filter(p => p.length === 4).map(([ns, name, group, kind]) => ({ ns, name, group, kind }));
}
// The target's workloads: Deployments, StatefulSets, DaemonSets and CronJobs in the inventory
// of its own Kustomizations and of the HelmReleases those apply (or that carry their Flux labels).
export function workloadsOf(sc, ks, hr) {
  const byKs = new Map((ks?.items || []).map(k => [L.ksId(k), k]));
  const byHr = new Map((hr?.items || []).map(h => [`${h.metadata.namespace}/${h.metadata.name}`, h]));
  const workloads = [], unread = [], hrIds = new Set(sc.own_hr || []);
  const take = (inv, via) => {
    for (const e of inv) {
      if (e.group === 'apps' && WORKLOAD_KINDS.includes(e.kind)) workloads.push({ kind: e.kind, ns: e.ns, name: e.name, via });
      else if (e.group === 'batch' && e.kind === 'CronJob') workloads.push({ kind: 'CronJob', ns: e.ns, name: e.name, via });
      else if (e.group === 'helm.toolkit.fluxcd.io' && e.kind === 'HelmRelease') hrIds.add(`${e.ns}/${e.name}`);
    }
  };
  for (const id of sc.own || []) {
    const inv = inventoryOf(byKs.get(id));
    if (!inv) unread.push(`the inventory of Kustomization ${id}`); else take(inv, `Kustomization ${id}`);
  }
  for (const id of hrIds) {
    const h = byHr.get(id);
    if (!h) { unread.push(`HelmRelease ${id} (named by the inventory, not listed)`); continue; }
    const inv = inventoryOf(h);
    if (!inv) unread.push(`the inventory of HelmRelease ${id}`); else take(inv, `HelmRelease ${id}`);
  }
  const seen = new Set();
  return { workloads: workloads.filter(w => { const k = wkey(w); return !seen.has(k) && seen.add(k); }), helmreleases: [...hrIds], unread };
}
// A pod's workload: a Deployment's pods are owned by its ReplicaSet <name>-<pod-template-hash>.
const revisionOf = (p, kind) => (kind === 'Deployment' ? lbl(p)['pod-template-hash'] : lbl(p)['controller-revision-hash']) ?? null;
export function podsOf(w, pods) {
  return (pods || []).filter(p => p?.metadata?.namespace === w.ns && (p.metadata.ownerReferences || []).some(r => (w.kind === 'Deployment'
    ? r.kind === 'ReplicaSet' && (lbl(p)['pod-template-hash'] ? r.name === `${w.name}-${lbl(p)['pod-template-hash']}` : new RegExp(`^${w.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}-[a-z0-9]{5,12}$`).test(r.name))
    : r.kind === w.kind && r.name === w.name)));
}
const terminal = p => ['Succeeded', 'Failed'].includes(p?.status?.phase);
const current = pods => pods.filter(p => !p.metadata?.deletionTimestamp && !terminal(p));
const podReady = p => (p?.status?.conditions || []).some(c => c.type === 'Ready' && c.status === 'True');
// Every container of a pod with its status, init containers included (L5); sidecar = a native
// sidecar (an init container with restartPolicy Always, which keeps running and can restart).
export function containersOf(p) {
  const st = (list, name) => (list || []).find(s => s.name === name) || {};
  const one = (c, init) => {
    const s = st(init ? p.status?.initContainerStatuses : p.status?.containerStatuses, c.name);
    return { pod: p.metadata.name, uid: p.metadata.uid, container: c.name, init, sidecar: init && c.restartPolicy === 'Always', ...parseImage(c.image), image: c.image, image_id: s.imageID || null,
      restarts: Number.isInteger(s.restartCount) ? s.restartCount : 0, ready: s.ready === true, waiting: s.state?.waiting?.reason || null, waiting_message: s.state?.waiting?.message || null };
  };
  return [...(p?.spec?.initContainers || []).map(c => one(c, true)), ...(p?.spec?.containers || []).map(c => one(c, false))];
}
// the containers whose restarts count: the main ones and native sidecars
const restartable = c => !c.init || c.sidecar;
const templateContainers = spec => [...(spec?.initContainers || []), ...(spec?.containers || [])];

// The update's containers (M3): those whose image is a package of the update, in the target's
// workloads' pods (init containers included); for a chart, every container of the workloads
// its HelmRelease renders; and the CronJobs whose template names a package.
export function updatedOf(packages, wl, hrIds, ctx) {
  const containers = new Set(), chartWorkloads = new Set(), crons = [], charts = [], hits = [], found = new Map();
  for (const pkg of packages) {
    const want = normRepo(pkg.name);
    let hit = false;
    for (const w of wl) {
      const f = ctx.pods(w.ns);
      if (w.kind === 'CronJob') {
        const cj = (f?.cronjobs?.items || []).find(x => x.metadata?.name === w.name);
        for (const c of templateContainers(cj?.spec?.jobTemplate?.spec?.template?.spec)) { const im = parseImage(c.image); if (im?.repo === want) { crons.push({ w, cj, pkg, ...im, image: c.image, container: c.name }); hit = true; } }
        continue;
      }
      for (const p of current(podsOf(w, f?.pods?.items || []))) for (const c of containersOf(p)) if (c.repo === want) { containers.add(`${wkey(w)}/${c.container}`); hits.push({ w, pkg, ...c }); hit = true; }
    }
    if (!hit) {
      const chartName = want.split('/').pop();
      for (const id of hrIds) {
        const h = (ctx.hr?.items || []).find(x => `${x.metadata.namespace}/${x.metadata.name}` === id);
        if (String(h?.status?.history?.[0]?.chartName || '').toLowerCase() !== chartName) continue;
        charts.push({ id, h, pkg }); hit = true;
        for (const w of wl.filter(x => x.via === `HelmRelease ${id}`)) chartWorkloads.add(wkey(w));
      }
    }
    found.set(pkg.name, hit);
  }
  return { containers, chartWorkloads, crons, charts, hits, found, has: (w, cname) => containers.has(`${wkey(w)}/${cname}`) || chartWorkloads.has(wkey(w)) };
}

// ── fact results ─────────────────────────────────────────────────────────────────
const fact = (name, result, detail, extra = {}) => ({ fact: name, result, detail: String(detail ?? '').slice(0, 900), ...extra });
const NA = (name, why) => fact(name, 'not-applicable', why);
// fold several findings of one fact into one result: fail > unreadable > waiting > pass
function fold(name, parts, passDetail) {
  const f = parts.filter(p => p.r === 'fail'), u = parts.filter(p => p.r === 'unreadable'), w = parts.filter(p => p.r === 'waiting');
  if (f.length) return fact(name, 'fail', f.map(x => x.d).join('; '), { class: f[0].class });
  if (u.length) return fact(name, 'unreadable', u.map(x => x.d).join('; '));
  if (w.length) return fact(name, 'waiting', w.map(x => x.d).join('; '));
  return fact(name, 'pass', passDetail ?? parts.map(x => x.d).join('; '));
}
// A condition that must hold on two reads at least min_stable_seconds apart before it fails:
// seen = { key: first seen at }; returns { r, d } and records the key in next.
function twice(key, d, cls, seen, next, now, minMs) {
  const at = seen?.[key];
  next[key] = at ?? iso(now);
  if (at && now - Date.parse(at) >= minMs) return { r: 'fail', class: cls, d: `${d}, on reads ${ago(now, at)} apart (since ${at})` };
  return { r: 'waiting', d: `${d}, since ${next[key]} (fails if it still holds ${minMs / 1000} s after that)` };
}

// ── the facts of one target (PURE over the reads it is handed) ───────────────────────────
// ctx: { st (M.fluxStates), contains(m, x) -> true|false|null, pods(ns) -> { pods, cronjobs, jobs }
// (null lists = unreadable), nodes() -> list|null, alerts (Prometheus /api/v1/alerts
// data.alerts, or null), promStart (ISO|null), alertSubject, alertScope, sourceRev,
// chartSource(h) -> { id, version, state } | null, now, cfg }
export function factSource(m, ctx) {
  const s = M.revisionSha(ctx.sourceRev);
  if (!s) return fact('source', 'unreadable', `the artifact revision of Flux's GitRepository (${JSON.stringify(ctx.sourceRev ?? null)})`);
  const has = ctx.contains(m, s);
  if (has === null) return fact('source', 'unreadable', `whether Flux's revision ${short(s)} contains ${short(m)} (a compare on GitHub)`);
  return has ? fact('source', 'pass', `Flux's source is at ${short(s)}, which contains ${short(m)}`) : fact('source', 'waiting', `Flux's source is at ${short(s)}, which does not contain ${short(m)} yet`);
}

// A failing Kustomization of the target fails only on two reads min_stable_seconds apart (N2):
// Flux's retry loop can hold one for an interval after a webhook blip or a CRD not established yet.
export function factApplied(m, sc, ctx, seen = {}, next = {}) {
  const minMs = (ctx.cfg?.min_stable_seconds ?? 600) * 1e3;
  const parts = [];
  if (!(sc.own || []).length) return fact('applied', 'unreadable', `no Kustomization applies ${(sc.targets || []).join(', ') || 'the target'}`);
  for (const id of sc.missing || []) parts.push({ r: 'unreadable', d: `Kustomization ${id} (the target's chain or one that applies it) is not listed` });
  for (const id of sc.own) {
    const k = ctx.st.byId.get(id);
    if (!k) { parts.push({ r: 'unreadable', d: `Kustomization ${id} is not listed` }); continue; }
    if (k.spec?.suspend === true) { parts.push({ r: 'unreadable', d: `Kustomization ${id} is suspended: cannot tell until someone resumes it` }); continue; }
    const s = ctx.st.ksState(id);
    const applied = M.revisionSha(k.status?.lastAppliedRevision), attempted = M.revisionSha(k.status?.lastAttemptedRevision);
    const has = applied ? ctx.contains(m, applied) : false;
    if (s.state === 'failing') {
      const dep = /DependencyNotReady/.test(s.why);
      if (dep) parts.push({ r: 'unreadable', d: `Kustomization ${id}: ${s.why}: cannot tell whether the update applied` });
      else if (has === true) parts.push(twice(`ks:${id}`, `Kustomization ${id} applied ${short(applied)} and is failing: ${s.why}`, 'not-ready', seen, next, ctx.now, minMs));
      else if (attempted && ctx.contains(m, attempted) === true) parts.push(twice(`ks:${id}`, `Kustomization ${id} attempted ${short(attempted)} and did not apply it: ${s.why}`, 'not-applied', seen, next, ctx.now, minMs));
      else parts.push({ r: 'unreadable', d: `Kustomization ${id} is failing (${s.why}) before it attempted the merge` });
      continue;
    }
    if (s.state === 'progressing') { parts.push({ r: 'waiting', d: `Kustomization ${id}: ${s.why}` }); continue; }
    if (has === null) { parts.push({ r: 'unreadable', d: `whether ${short(applied)}, applied by ${id}, contains ${short(m)}` }); continue; }
    if (!has) { parts.push({ r: 'waiting', d: `Kustomization ${id} has applied ${short(applied) || 'nothing'}, which does not contain ${short(m)} yet` }); continue; }
    parts.push({ r: 'pass', d: `${id} applied ${short(applied)} and is Ready` });
  }
  // the chain and the Kustomizations that apply the target: settled. A failing one cannot be
  // charged to this update (it may be broken for its own reasons), so it is unreadable.
  for (const id of uniq([...(sc.chain || []), ...(sc.appliers || [])]).filter(x => !sc.own.includes(x))) {
    const k = ctx.st.byId.get(id);
    if (!k || k.spec?.suspend === true) continue; // missing ones are listed above; a suspended dependency is not this update's
    const s = ctx.st.ksState(id);
    const role = (sc.appliers || []).includes(id) && !(sc.chain || []).includes(id) ? 'applies the target' : 'in the dependsOn chain';
    if (s.state === 'failing') parts.push({ r: 'unreadable', d: `Kustomization ${id} (${role}) is failing: ${s.why}` });
    else if (s.state === 'progressing') parts.push({ r: 'waiting', d: `Kustomization ${id} (${role}): ${s.why}` });
  }
  return fold('applied', parts, parts.filter(p => p.r === 'pass').map(p => p.d).join('; ') + `; chain and appliers settled (${uniq([...(sc.chain || []), ...(sc.appliers || [])]).length - sc.own.length})`);
}

// HelmReleases: settled and deployed. A failing one is not-applied when it is still failing on
// a read at least min_stable_seconds after it was first seen failing (M4); seen/next: memory.
export function factHelmReleases(hrIds, hrList, ctx, seen = {}, next = {}) {
  if (!hrIds.length) return NA('helmreleases', 'the target has no HelmRelease');
  const parts = [];
  for (const id of hrIds) {
    const h = (hrList?.items || []).find(x => `${x.metadata.namespace}/${x.metadata.name}` === id);
    if (!h) { parts.push({ r: 'unreadable', d: `HelmRelease ${id} is not listed` }); continue; }
    if (h.spec?.suspend === true) { parts.push({ r: 'unreadable', d: `HelmRelease ${id} is suspended: cannot tell until someone resumes it` }); continue; }
    const s = ctx.st.hrState.get(id) || M.fluxSettleState(h);
    const last = (h.status?.history || [])[0] || null;
    if (s.state === 'failing') { parts.push(twice(`hr:${id}`, `HelmRelease ${id} is failing: ${s.why}${last ? ` (last release ${last.chartVersion ?? '?'} ${last.status ?? '?'})` : ''}`, 'not-applied', seen, next, ctx.now, ctx.cfg.min_stable_seconds * 1e3)); continue; }
    if (s.state === 'progressing') { parts.push({ r: 'waiting', d: `HelmRelease ${id}: ${s.why}` }); continue; }
    if (!last) { parts.push({ r: 'unreadable', d: `HelmRelease ${id} has no release history` }); continue; }
    if (last.status === 'failed') { parts.push(twice(`hr:${id}`, `HelmRelease ${id}: its last release (${last.chartVersion}) failed`, 'not-applied', seen, next, ctx.now, ctx.cfg.min_stable_seconds * 1e3)); continue; }
    if (last.status !== 'deployed') { parts.push({ r: 'waiting', d: `HelmRelease ${id}: its last release is ${last.status}` }); continue; }
    parts.push({ r: 'pass', d: `${id} Ready, ${last.chartName ?? 'chart'} ${String(last.chartVersion || '').split('+')[0]} deployed ${last.lastDeployed ?? ''}`.trim() });
  }
  return fold('helmreleases', parts);
}

// The pods made since the merge (M3, M4): a crash loop or an image-pull failure of one of the
// update's containers fails; of another container it is cannot-tell, named. A container that
// cannot start (CreateContainerConfigError, CreateContainerError, RunContainerError) fails on
// two reads min_stable_seconds apart. A Pending pod is cannot-tell with the scheduler's reason.
// The same in pods made before the merge is listed (it was there before the update).
export function factPods(mergedAt, wl, u, ctx, seen = {}, next = {}) {
  const parts = [], older = [];
  const t = Date.parse(mergedAt), minMs = ctx.cfg.min_stable_seconds * 1e3;
  for (const w of wl.filter(x => x.kind !== 'CronJob')) {
    const f = ctx.pods(w.ns);
    if (!f?.pods) { parts.push({ r: 'unreadable', d: `pods in ${w.ns}` }); continue; }
    for (const p of current(podsOf(w, f.pods.items))) {
      const made = Date.parse(p.metadata?.creationTimestamp || '');
      if (!Number.isFinite(made)) { parts.push({ r: 'unreadable', d: `when pod ${p.metadata.name} was made` }); continue; }
      const isNew = made >= t;
      if (isNew && p.status?.phase === 'Pending') {
        const sch = (p.status?.conditions || []).find(c => c.type === 'PodScheduled' && c.status === 'False');
        if (sch) { parts.push({ r: 'unreadable', d: `pod ${p.metadata.name} is Pending: ${sch.reason || 'not scheduled'}${sch.message ? `: ${String(sch.message).slice(0, 160)}` : ''}` }); continue; }
      }
      for (const c of containersOf(p)) {
        const why = c.waiting;
        const cls = why === 'CrashLoopBackOff' ? 'crash-loop' : PULL_FAILURES.includes(why) ? 'image-pull' : START_FAILURES.includes(why) ? 'not-ready' : null;
        if (!cls) continue;
        const d = `${w.kind} ${w.ns}/${w.name}: pod ${p.metadata.name} ${c.init ? 'init ' : ''}container ${c.container} is ${why}${c.waiting_message ? ` (${String(c.waiting_message).slice(0, 120)})` : ''} (restarts ${c.restarts})`;
        if (!isNew) { older.push(`${d}, made before the merge`); continue; }
        if (!u.has(w, c.container)) { parts.push({ r: 'unreadable', d: `${d}: not one of the update's containers, so cannot tell` }); continue; }
        parts.push(cls === 'not-ready' ? twice(`ctr:${c.uid}/${c.container}/${why}`, d, cls, seen, next, ctx.now, minMs) : { r: 'fail', class: cls, d });
      }
    }
  }
  const pods = wl.filter(x => x.kind !== 'CronJob').length;
  return fold('pods', parts, pods ? `no crash loop, image-pull failure or start failure in the update's containers of pods made since the merge${older.length ? `; before the merge: ${older.join('; ')}` : ''}` : 'the target has no Deployment, StatefulSet or DaemonSet');
}

// Critical alerts by R5's tracing: an alert belongs to the target when its subject traces to
// the target's Kustomization or its dependsOn chain. New = it started firing at or after the
// merge (Prometheus activeAt). Prometheus gives a rule with no `for:` its restart time as the
// start, so one that started within prometheus_restart_slack_seconds of a restart after the
// merge cannot be told from the restart (M3). A new alert the tracing cannot place in a target
// namespace is unreadable; an older one is listed.
export function factAlerts(mergedAt, sc, clusterWide, ctx) {
  if (!Array.isArray(ctx.alerts)) return fact('alerts', 'unreadable', 'critical alerts (Prometheus /api/v1/alerts)');
  if (typeof ctx.alertSubject !== 'function') return fact('alerts', 'unreadable', 'the alert tracing (run-night.mjs alertSubject) was not handed in');
  const fo = ctx.alertScope?.flux_object || {};
  const chain = new Set(sc.chain || []);
  const chainNs = new Set();
  for (const id of sc.chain || []) { chainNs.add(id.split('/')[0]); const k = ctx.st.byId.get(id); if (k?.spec?.targetNamespace) chainNs.add(k.spec.targetNamespace); }
  const t = Date.parse(mergedAt);
  const ps = Date.parse(ctx.promStart || '');
  const slack = (ctx.cfg?.prometheus_restart_slack_seconds ?? 300) * 1e3;
  const parts = [], older = [];
  for (const a of ctx.alerts) {
    if (a?.state !== 'firing' || a?.labels?.severity !== 'critical') continue;
    const m = a.labels || {}, name = m.alertname || '(unnamed)';
    const since = Date.parse(a.activeAt || '');
    const isNew = !Number.isFinite(since) ? null : since >= t;
    const at = Number.isFinite(since) ? iso(since) : 'an unreadable time';
    const nss = [m.namespace, m[fo.namespace_label]].filter(Boolean);
    const flux = !m.namespace && m[fo.kind_label] === 'Kustomization' && m[fo.name_label] ? `${m[fo.namespace_label]}/${m[fo.name_label]}` : null;
    let res = null, by = null;
    if (!nss.length) {
      if (clusterWide.length) res = 'unplaced', by = `no namespace label, and the target is cluster-wide (${clusterWide.join(', ')})`;
    } else {
      const ns = nss.find(n => (sc.namespaces || []).includes(n)) || nss.find(n => chainNs.has(n));
      if (!ns) continue;
      const f = ctx.pods(ns) || {};
      const s0 = ctx.alertSubject(m, f, ctx.ks, ctx.hr, ctx.alertScope);
      const s = !s0.owner && flux && chain.has(flux) ? { ...s0, owner: flux } : s0;
      by = s.by || 'no workload';
      if (s.owner && chain.has(s.owner)) res = 'belongs', by = `${by}, belongs to ${s.owner}`;
      else if (!(sc.namespaces || []).includes(ns)) continue; // a dependency's namespace: only what traces to the chain counts
      else if (!s.owner && s.by && (!f.pods || !f.cronjobs || !f.jobs)) res = 'unplaced', by = `${by}: pods, Jobs or CronJobs in ${ns} unreadable`;
      else if (!s.owner) res = 'unplaced', by = s.by ? `${by}, not traced to a Kustomization` : 'names no workload';
      else continue; // another Kustomization's alert
    }
    if (!res) continue;
    const d = `${name} (${by}) firing since ${at}`;
    if (isNew === false) { older.push(d); continue; }
    if (res === 'belongs' && isNew === true) {
      if (!Number.isFinite(ps)) { parts.push({ r: 'unreadable', d: `${d}: when Prometheus started is unreadable, so a start at its restart cannot be ruled out` }); continue; }
      if (ps >= t && since - ps < slack) { parts.push({ r: 'unreadable', d: `${d}: within ${slack / 1000} s of Prometheus's start at ${iso(ps)}, after the merge: cannot tell it from the restart` }); continue; }
      parts.push({ r: 'fail', class: 'critical-alert', d });
    } else if (res === 'belongs') parts.push({ r: 'unreadable', d: `whether ${d} is new` });
    else parts.push({ r: 'unreadable', d: `cannot tell whether ${d} belongs to the target` });
  }
  return fold('alerts', parts, `no new critical alert on the target or its chain${older.length ? `; firing since before the merge: ${older.slice(0, 5).join('; ')}` : ''}`);
}

// The packages of the update: the update's containers run the new version (digest or tag by
// the recorded update type); else a CronJob template names it; else the HelmRelease whose chart
// it is runs it. A package found nowhere is unreadable. A chart source failing on two reads is
// not-applied (M4).
export function factImage(cand, u, rollout, ctx, seen = {}, next = {}) {
  const parts = [];
  const kind = versionKindOf(cand.type);
  if (!cand.packages.length || cand.packages.some(p => !p.name)) return fact('image', 'unreadable', 'the update names no package (no package table in the PR)');
  for (const pkg of cand.packages) {
    const label = `${pkg.name} ${pkg.to ?? '(no version)'}`;
    const conts = u.hits.filter(h => h.pkg === pkg), crons = u.crons.filter(c => c.pkg === pkg), charts = u.charts.filter(c => c.pkg === pkg);
    if (conts.length) {
      const unk = conts.filter(c => versionMatches(c, pkg.to, kind) === null), bad = conts.filter(c => versionMatches(c, pkg.to, kind) === false);
      if (unk.length) parts.push({ r: 'unreadable', d: `the version ${unk[0].pod}/${unk[0].container} runs (${unk[0].image}; the update is a ${kind} update)` });
      else if (bad.length) {
        const rolling = bad.some(c => rollout.get(wkey(c.w)) === 'waiting');
        parts.push(rolling ? { r: 'waiting', d: `${bad.length} container(s) still run ${bad[0].image} while ${bad[0].w.kind} ${bad[0].w.ns}/${bad[0].w.name} rolls` }
          : { r: 'fail', class: 'wrong-image', d: `${bad.length} of ${conts.length} container(s) run ${bad[0].image}, not ${label} (${bad[0].pod}/${bad[0].container}), and nothing is rolling` });
      } else parts.push({ r: 'pass', d: `${conts.length} container(s) in ${uniq(conts.map(c => `${c.w.ns}/${c.w.name}`)).join(', ')} run ${label}` });
      continue;
    }
    if (crons.length) {
      const unk = crons.filter(c => versionMatches(c, pkg.to, kind) === null), bad = crons.filter(c => versionMatches(c, pkg.to, kind) === false);
      parts.push(bad.length ? { r: 'fail', class: 'wrong-image', d: `CronJob ${bad[0].w.ns}/${bad[0].w.name} names ${bad[0].image}, not ${label}` }
        : unk.length ? { r: 'unreadable', d: `the version CronJob ${unk[0].w.ns}/${unk[0].w.name} names (${unk[0].image}; the update is a ${kind} update)` }
          : { r: 'pass', d: `CronJob ${crons.map(c => `${c.w.ns}/${c.w.name}`).join(', ')} names ${label}` });
      continue;
    }
    if (charts.length) {
      for (const { id, h } of charts) {
        const last = h.status.history[0];
        const ver = String(last.chartVersion || '').split('+')[0];
        if (L.bareVersion(ver) === L.bareVersion(pkg.to) && last.status === 'deployed') { parts.push({ r: 'pass', d: `HelmRelease ${id} runs chart ${last.chartName} ${ver}` }); continue; }
        const s = ctx.st.hrState.get(id) || M.fluxSettleState(h);
        if (s.state !== 'settled') { parts.push({ r: 'waiting', d: `HelmRelease ${id} runs chart ${ver} and is ${s.state} (${s.why})` }); continue; }
        // settled on another chart version: did its chart source move? (OCIRepository or HelmChart)
        const src = ctx.chartSource ? ctx.chartSource(h) : null;
        if (!src) parts.push({ r: 'unreadable', d: `HelmRelease ${id} is settled on chart ${ver}; its chart source could not be read` });
        else if (src.version && L.bareVersion(src.version) === L.bareVersion(pkg.to)) parts.push({ r: 'waiting', d: `HelmRelease ${id} is settled on chart ${ver}; its source ${src.id} has ${src.version}` });
        else if (src.state === 'settled') parts.push({ r: 'fail', class: 'wrong-image', d: `HelmRelease ${id} runs chart ${ver}, and its source ${src.id} is settled on ${src.version ?? 'an unreadable version'}, not ${pkg.to}` });
        else if (src.state === 'failing') parts.push(twice(`src:${src.id}`, `HelmRelease ${id} runs chart ${ver}; its source ${src.id} is failing (${src.why || 'no reason'})`, 'not-applied', seen, next, ctx.now, ctx.cfg.min_stable_seconds * 1e3));
        else parts.push({ r: 'waiting', d: `HelmRelease ${id} runs chart ${ver}; its source ${src.id} is ${src.state}` });
      }
      continue;
    }
    parts.push({ r: 'unreadable', d: `no running container, CronJob template or HelmRelease chart of the target is ${pkg.name}` });
  }
  return fold('image', parts);
}

// The next time a cron schedule fires, in its time zone, scanning minutes for up to 8 days.
// null = cannot tell (an expression or zone it cannot read).
export function nextCron(expr, tz, fromMs) {
  const f = String(expr || '').trim().split(/\s+/);
  if (f.length !== 5) return null;
  const field = (s, lo, hi) => { const set = new Set(); for (const part of s.split(',')) { const m = /^(\*|\d+)(?:-(\d+))?(?:\/(\d+))?$/.exec(part); if (!m) return null; const a = m[1] === '*' ? lo : Number(m[1]); const b = m[1] === '*' ? hi : m[2] !== undefined ? Number(m[2]) : m[3] ? hi : a; for (let v = a; v <= b; v += Number(m[3] || 1)) set.add(v === 7 && hi === 7 ? 0 : v); } return set; };
  const [mi, ho, dom, mo, dow] = f.map((s, i) => field(s, ...[[0, 59], [0, 23], [1, 31], [1, 12], [0, 7]][i]));
  if (![mi, ho, dom, mo, dow].every(Boolean)) return null;
  let fmt;
  try { fmt = new Intl.DateTimeFormat('en-US', { timeZone: tz || 'UTC', hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', weekday: 'short' }); } catch { return null; }
  const DOW = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  const domAny = f[2] === '*', dowAny = f[4] === '*';
  for (let t = Math.ceil((fromMs + 1) / 60e3) * 60e3, end = fromMs + 8 * 864e5; t < end; t += 60e3) {
    const p = Object.fromEntries(fmt.formatToParts(new Date(t)).filter(x => x.type !== 'literal').map(x => [x.type, x.value]));
    const day = domAny || dowAny ? dom.has(Number(p.day)) && dow.has(DOW[p.weekday]) : dom.has(Number(p.day)) || dow.has(DOW[p.weekday]);
    if (mi.has(Number(p.minute)) && ho.has(Number(p.hour)) && mo.has(Number(p.month)) && day) return iso(t);
  }
  return null;
}
// M1, L-c: a CronJob whose template names the update has run from the new template since the
// merge. The newest finished Job from the new template made since the merge decides (a newer
// one still running does not hide it). A failure is the update's only when the CronJob's last
// finished Job from the old template did not fail too. A suspended CronJob is cannot-tell.
export function factExercised(u, mergedAt, kind, ctx) {
  if (!u.crons.length) return null;
  const parts = [], t = Date.parse(mergedAt);
  const finishedAs = j => { const c = type => (j.status?.conditions || []).some(x => x.type === type && x.status === 'True'); return c('Failed') ? 'failed' : c('Complete') || j.status?.succeeded > 0 ? 'complete' : null; };
  const newest = list => [...list].sort((a, b) => String(b.metadata.creationTimestamp).localeCompare(String(a.metadata.creationTimestamp)));
  for (const c of u.crons) {
    const id = `CronJob ${c.w.ns}/${c.w.name}`;
    const f = ctx.pods(c.w.ns);
    if (!f?.jobs) { parts.push({ r: 'unreadable', d: `Jobs in ${c.w.ns}` }); continue; }
    if (c.cj?.spec?.suspend === true) { parts.push({ r: 'unreadable', d: `${id} is suspended: no run comes until someone resumes it` }); continue; }
    const mine = (f.jobs.items || []).filter(j => (j.metadata?.ownerReferences || []).some(r => r.kind === 'CronJob' && r.name === c.w.name));
    const fromNew = j => templateContainers(j.spec?.template?.spec).some(x => parseImage(x.image)?.repo === c.repo && versionMatches({ ...parseImage(x.image) }, c.pkg.to, kind) === true);
    const since = mine.filter(j => Date.parse(j.metadata?.creationTimestamp || '') >= t && fromNew(j));
    const done = newest(since.filter(finishedAs));
    if (!done.length) {
      if (since.length) { parts.push({ r: 'waiting', d: `${id}: Job ${newest(since)[0].metadata.name} from the new template is running` }); continue; }
      const next = nextCron(c.cj?.spec?.schedule, c.cj?.spec?.timeZone, ctx.now);
      parts.push({ r: 'waiting', d: `${id}: no Job from the new template since the merge; waiting for its first run (schedule ${c.cj?.spec?.schedule ?? '?'}${c.cj?.spec?.timeZone ? ` ${c.cj.spec.timeZone}` : ''}, last scheduled ${c.cj?.status?.lastScheduleTime ?? 'never'}, next ${next ?? 'unreadable'})` });
      continue;
    }
    const j = done[0];
    if (finishedAs(j) === 'complete') { parts.push({ r: 'pass', d: `${id}: Job ${j.metadata.name} from the new template completed` }); continue; }
    const why = (j.status.conditions.find(x => x.type === 'Failed') || {}).reason || 'no reason';
    const old = newest(mine.filter(x => !fromNew(x) && finishedAs(x)))[0];
    if (old && finishedAs(old) === 'failed') parts.push({ r: 'unreadable', d: `${id}: Job ${j.metadata.name} from the new template failed (${why}), and its last Job from the old template (${old.metadata.name}) failed too: it was failing before the merge, so cannot tell` });
    else parts.push({ r: 'fail', class: 'job-failed', d: `${id}: Job ${j.metadata.name} from the new template failed (${why})${old ? `; its last Job from the old template (${old.metadata.name}) completed` : ''}` });
  }
  return fold('exercised', parts);
}

// Per workload: pods of one template, none terminating, all Ready. Returns the fact and a
// map workload -> its own result (factImage reads whether a workload is still rolling).
export function factRollout(wl, ctx) {
  const parts = [], per = new Map();
  const ws = wl.filter(w => w.kind !== 'CronJob');
  if (!ws.length) return { fact: NA('rollout', 'the target has no Deployment, StatefulSet or DaemonSet'), per };
  for (const w of ws) {
    const key = wkey(w), f = ctx.pods(w.ns);
    if (!f?.pods) { parts.push({ r: 'unreadable', d: `pods in ${w.ns}` }); per.set(key, 'unreadable'); continue; }
    const all = podsOf(w, f.pods.items), cur = current(all), term = all.filter(p => p.metadata?.deletionTimestamp);
    const revs = uniq(cur.map(p => revisionOf(p, w.kind)));
    const notReady = cur.filter(p => !podReady(p));
    let r, d;
    if (!cur.length) { r = 'unreadable'; d = `${key} has no running pod (scaled to zero, or not created yet): pods alone cannot tell its desired replicas; needs get on apps ${w.kind.toLowerCase()}s (story 755)`; }
    else if (revs.some(x => x === null)) { r = 'unreadable'; d = `${key}: a pod carries no template revision label`; }
    else if (revs.length > 1) { r = 'waiting'; d = `${key} has pods of ${revs.length} templates (${revs.join(', ')})`; }
    else if (term.length) { r = 'waiting'; d = `${key}: ${term.length} pod(s) terminating`; }
    else if (notReady.length) { r = 'waiting'; d = `${key}: ${notReady.length} of ${cur.length} pod(s) not Ready (${notReady.slice(0, 2).map(p => p.metadata.name).join(', ')})`; }
    else { r = 'pass'; d = `${key}: ${cur.length} pod(s) of template ${revs[0]}, all Ready`; }
    parts.push({ r, d }); per.set(key, r);
  }
  return { fact: fold('rollout', parts, parts.map(p => p.d).join('; ') + '; pods alone cannot tell a controller that has not created all its replicas yet (story 755)'), per };
}

// Two ticks at least min_stable_seconds apart, on the tick's own clock (M2). prior (the record):
// { at, pods: { uid: { name, node, wkey, restarts: { container: n } } }, nodes: { node: bootID } },
// taken when the rollout was complete. m0: the stability memory of the last tick: last (each
// pod's state at its last read), replaced and moves (per workload), passed. Returns { fact,
// baseline, mem }.
//   - once stable has passed it stays passed for the item (N3): health after the soak is the
//     alerting's business;
//   - a recorded pod that is gone was moved when it was Ready with no new restarts at its last
//     read (the descheduler, a drain): the pods are recorded again and the move is counted;
//     otherwise it was replaced, and max_replacements replacements of the update's workload
//     fail as churn (N3);
//   - a restart of an update's container fails as restart-growth, unless its node rebooted
//     (its boot id changed) or was not Ready since the record: then the pods are recorded again
//     (N3); a restart of another container is cannot-tell, named (M3);
//   - a recorded pod not Ready now fails as not-ready when an update's container is not ready
//     and its node is Ready; on a node not Ready it is cannot-tell.
export function factStable(wl, u, prior, now, ctx, rolloutDone = true, m0 = {}) {
  const ws = wl.filter(w => w.kind !== 'CronJob');
  const minMs = ctx.cfg.min_stable_seconds * 1e3;
  const mem = { last: {}, replaced: { ...(m0.replaced || {}) }, moves: { ...(m0.moves || {}) }, passed: m0.passed ?? null };
  if (!ws.length) return { fact: NA('stable', 'the target has no Deployment, StatefulSet or DaemonSet'), baseline: null, mem };
  if (mem.passed) return { fact: fact('stable', 'pass', `passed at ${mem.passed.at} (${mem.passed.detail}); not read again: health after the soak is the alerting's business`), baseline: prior, mem };
  const pods = [];
  for (const w of ws) { const f = ctx.pods(w.ns); if (!f?.pods) return { fact: fact('stable', 'unreadable', `pods in ${w.ns}`), baseline: prior, mem: { ...mem, last: m0.last || {} } }; for (const p of current(podsOf(w, f.pods.items))) pods.push({ p, w }); }
  for (const { p } of pods) mem.last[p.metadata.uid] = { ready: podReady(p), restarts: Object.fromEntries(containersOf(p).filter(restartable).map(c => [c.container, c.restarts])) };
  let nodeMap;
  const nodes = () => {
    if (nodeMap !== undefined) return nodeMap;
    const l = ctx.nodes ? ctx.nodes() : null;
    nodeMap = l ? Object.fromEntries((l.items || []).map(n => { const r = (n.status?.conditions || []).find(c => c.type === 'Ready'); return [n.metadata.name, { boot: n.status?.nodeInfo?.bootID ?? null, ready: r?.status === 'True', since: r?.lastTransitionTime ?? null }]; })) : null;
    return nodeMap;
  };
  const record = () => {
    const nm = nodes();
    return { at: iso(now), pods: Object.fromEntries(pods.map(({ p, w }) => [p.metadata.uid, { name: p.metadata.name, node: p.spec?.nodeName ?? null, wkey: wkey(w), restarts: mem.last[p.metadata.uid].restarts }])),
      nodes: nm ? Object.fromEntries(uniq(pods.map(x => x.p.spec?.nodeName).filter(Boolean)).map(n => [n, nm[n]?.boot ?? null])) : null };
  };
  if (!prior?.pods || !prior.at) return rolloutDone ? { fact: fact('stable', 'waiting', `the rollout is complete at ${iso(now)} (${pods.length} pod(s) recorded); they are read again at least ${minMs / 1000} s later`), baseline: record(), mem }
    : { fact: fact('stable', 'waiting', 'read once the rollout is complete'), baseline: null, mem };
  if (Date.parse(prior.at) >= now) return { fact: fact('stable', 'waiting', `recorded at ${prior.at}; a later tick reads the pods again`), baseline: prior, mem };
  const age = now - Date.parse(prior.at);
  const byUid = new Map(pods.map(x => [x.p.metadata.uid, x]));
  const gone = Object.entries(prior.pods).filter(([uid]) => !byUid.has(uid));
  if (gone.length) {
    const what = [];
    for (const [uid, b] of gone) {
      const l = m0.last?.[uid] ?? { ready: true, restarts: b.restarts };
      const moved = l.ready === true && Object.entries(b.restarts || {}).every(([c, n]) => (l.restarts?.[c] ?? n) <= n);
      if (moved) { mem.moves[b.wkey] = (mem.moves[b.wkey] || 0) + 1; what.push(`${b.name} moved (Ready, no new restarts at its last read)`); }
      else { mem.replaced[b.wkey] = (mem.replaced[b.wkey] || 0) + 1; what.push(`${b.name} replaced after it was ${l.ready ? 'restarting' : 'not Ready'}`); }
    }
    const churn = Object.entries(mem.replaced).filter(([, n]) => n >= ctx.cfg.max_replacements);
    if (churn.length) {
      const [k, n] = churn[0];
      const mine = u.chartWorkloads.has(k) || [...u.containers].some(x => x.startsWith(`${k}/`));
      return { fact: fact('stable', mine ? 'fail' : 'unreadable', `${k}: pods replaced ${n} times after they were not Ready or restarting${mine ? '' : ' (not a workload the update changed: cannot tell)'}`, mine ? { class: 'churn' } : {}), baseline: rolloutDone ? record() : null, mem };
    }
    const counts = [...Object.entries(mem.moves).map(([k, n]) => `${k} moved ${n}x`), ...Object.entries(mem.replaced).map(([k, n]) => `${k} replaced ${n}x`)].join(', ');
    return { fact: fact('stable', 'waiting', `${what.slice(0, 2).join('; ')}${what.length > 2 ? ` (+${what.length - 2})` : ''} since ${prior.at} (${counts}); ${rolloutDone ? `the pods are recorded again at ${iso(now)}` : 'they are recorded again once the rollout is complete'}`), baseline: rolloutDone ? record() : null, mem, rebaselined: true };
  }
  if (age < minMs) return { fact: fact('stable', 'waiting', `the pods were recorded at ${prior.at}, ${Math.round(age / 1000)} s ago; they are read again at least ${minMs / 1000} s after that`), baseline: prior, mem };
  const parts = [], maint = [];
  for (const [uid, b] of Object.entries(prior.pods)) {
    const { p, w } = byUid.get(uid);
    const cs = containersOf(p);
    for (const c of cs.filter(restartable)) {
      const was = b.restarts?.[c.container];
      if (!Number.isInteger(was) || c.restarts <= was) continue;
      const d = `pod ${b.name} container ${c.container} restarted: ${was} at ${prior.at}, ${c.restarts} now`;
      if (!u.has(w, c.container)) { parts.push({ r: 'unreadable', d: `${d} (not one of the update's containers: cannot tell)` }); continue; }
      const nm = nodes(), node = p.spec?.nodeName, cur = nm?.[node], rec0 = prior.nodes?.[node];
      if (!node || !cur || !rec0) { parts.push({ r: 'unreadable', d: `${d}; whether its node ${node ?? '(none)'} rebooted cannot be read` }); continue; }
      if (cur.boot !== rec0 || !cur.ready || Date.parse(cur.since || '') > Date.parse(prior.at)) { maint.push(`${d}, on node ${node}, which ${cur.boot !== rec0 ? 'rebooted (its boot id changed)' : 'was not Ready since the record'}`); continue; }
      parts.push({ r: 'fail', class: 'restart-growth', d });
    }
    if (podReady(p)) continue;
    const nm = nodes();
    const node = (nm || {})[p.spec?.nodeName];
    const down = cs.filter(c => !c.init && !c.ready).map(c => c.container);
    const d = `pod ${b.name} was Ready at ${prior.at} and is not Ready now (${down.join(', ') || 'no container named'})`;
    if (!p.spec?.nodeName || !node) parts.push({ r: 'unreadable', d: `${d}; its node ${p.spec?.nodeName ?? '(none)'} could not be read` });
    else if (!node.ready) parts.push({ r: 'unreadable', d: `${d}; its node ${p.spec.nodeName} is not Ready: cannot tell` });
    else if (down.some(x => u.has(w, x))) parts.push({ r: 'fail', class: 'not-ready', d });
    else parts.push({ r: 'unreadable', d: `${d} (not one of the update's containers: cannot tell)` });
  }
  if (parts.length) return { fact: fold('stable', parts), baseline: prior, mem };
  if (maint.length) return { fact: fact('stable', 'waiting', `${maint.join('; ')}: not the update's; the pods are recorded again at ${iso(now)}`), baseline: rolloutDone ? record() : null, mem, rebaselined: true };
  if (!rolloutDone) return { fact: fact('stable', 'waiting', `the ${Object.keys(prior.pods).length} pod(s) recorded at ${prior.at} are Ready with no new restarts, and the rollout is not complete now`), baseline: prior, mem };
  const detail = `the same ${Object.keys(prior.pods).length} pod(s) recorded at ${prior.at} (${Math.round(age / 1000)} s ago) are Ready now with no new restarts`;
  mem.passed = { at: iso(now), detail };
  return { fact: fact('stable', 'pass', detail), baseline: prior, mem };
}

// ── the verdict ──────────────────────────────────────────────────────────────────
// fail > unreadable > waiting > pass. A fail with other changes (or with them unreadable)
// cannot be charged to the update: "cannot tell which change did it".
export function verdictOf(facts, other) {
  const fails = facts.filter(f => f.result === 'fail'), unread = facts.filter(f => f.result === 'unreadable'), waits = facts.filter(f => f.result === 'waiting');
  const bad = facts.filter(f => !RESULTS.includes(f.result));
  if (bad.length) return { verdict: 'cannot-tell', why: `a fact has no known result: ${bad.map(f => f.fact).join(', ')}`, unreadable: bad.map(f => f.fact) };
  if (fails.length) {
    const attributed = other?.complete === true && other.changes.length === 0;
    return { verdict: 'failed', attribution: attributed ? 'own-regression' : 'cannot-tell-which-change', failing: fails.map(f => ({ fact: f.fact, class: f.class, detail: f.detail })),
      why: attributed ? `failed: ${fails.map(f => `${f.fact} (${f.class})`).join(', ')}; no other change reached the target, its chain or a cluster-wide part: the update's own regression`
        : `failed: ${fails.map(f => `${f.fact} (${f.class})`).join(', ')}; cannot tell which change did it: ${other?.complete ? other.changes.map(c => `${short(c.sha)}${c.pr ? ` #${c.pr}` : ''}${c.before ? ' (merged before, not verified yet)' : ''}`).join(', ') : `other changes unreadable (${other?.why ?? 'not read'})`}` };
  }
  if (unread.length) return { verdict: 'cannot-tell', unreadable: unread.map(f => f.fact), why: `cannot tell: ${unread.map(f => `${f.fact}: ${f.detail}`).join(' | ')}` };
  if (waits.length) return { verdict: 'not-yet', waiting_for: waits.map(f => `${f.fact}: ${f.detail}`), why: `not yet: ${waits.map(f => `${f.fact}: ${f.detail}`).join(' | ')}` };
  return { verdict: 'verified', why: 'every fact passed (or does not apply)' };
}

// ── other changes (story 551's helper over one range read) ─────────────────────────────
// After the merge: the commits on main that reach the scope. Before it (L4): the earlier
// merges whose own verdict is not final yet that reach the scope; one whose files are
// unknown makes the list incomplete.
export function otherFor(cand, range, sc, rules, sourceSha, earlier = []) {
  if (!sc) return { complete: true, applicable: false, why: 'no immediate cluster effect: no target to compare', changes: [] };
  if (!range?.complete) return { complete: false, why: range?.why || 'unreadable: the commits on main since the merge', changes: [] };
  const i = range.commits.findIndex(c => c.sha === cand.merge_commit);
  if (i < 0 && range.base !== cand.merge_commit) return { complete: false, why: `${short(cand.merge_commit)} is older than the commits read this tick (more than the range limit since it)`, changes: [] };
  const sub = { complete: true, base: cand.merge_commit, head: range.head, commits: range.commits.slice(i + 1) };
  const oc = M.otherChanges(sub, sc, rules);
  const srcI = range.commits.findIndex(c => c.sha === sourceSha);
  const changes = oc.changes.map(c => ({ sha: c.sha, pr: c.pr, at: c.at, subject: c.subject, author: c.author,
    parts: uniq(c.matched.map(x => `${x.part} ${x.name}`)).slice(0, 6), fetched_by_flux: srcI >= 0 ? range.commits.findIndex(x => x.sha === c.sha) <= srcI : sourceSha === range.base ? false : null }));
  const unknown = earlier.filter(e => !Array.isArray(e.files));
  const ec = M.otherChanges({ complete: true, base: null, head: null, commits: earlier.filter(e => Array.isArray(e.files)).map(e => ({ sha: e.sha, at: e.at, subject: `#${e.pr} merged before, not verified yet (${e.verdict ?? 'not judged'})`, pr: e.pr, files: e.files })) }, sc, rules);
  for (const c of ec.changes) changes.push({ sha: c.sha, pr: c.pr, at: c.at, subject: c.subject, before: true, parts: uniq(c.matched.map(x => `${x.part} ${x.name}`)).slice(0, 6), fetched_by_flux: null });
  if (unknown.length) return { complete: false, why: `an earlier merge not verified yet whose files were not read (${unknown.map(e => `#${e.pr}`).join(', ')})`, changes };
  return { complete: true, why: null, through: range.commits.at(-1)?.sha ?? range.base, changes };
}

// ── the events a verdict writes (the lane's own merge) or would write (practice) ─────────
export function eventsFor(cand, rec, at, repo) {
  const base = { item_id: cand.id, at, actor: { kind: 'lane', login: null }, recorded_by: 'lane', attempt: cand.attempt, carrier: { repo, pr: cand.pr } };
  const checks = rec.facts.map(f => `${f.fact}: ${f.result}${f.detail ? ` (${f.detail})` : ''}`);
  const others = rec.other_changes?.complete ? (rec.other_changes.changes.length ? `other changes: ${rec.other_changes.changes.map(c => `${short(c.sha)}${c.pr ? ` #${c.pr}` : ''}`).join(', ')}` : 'no other change') : `other changes unreadable (${rec.other_changes?.why ?? 'not read'})`;
  const evidence = { merge_commit: cand.merge_commit, checks: [...checks, others], ...(rec.flux_revision ? { flux_revision: rec.flux_revision } : {}) };
  const reason = `${rec.why}; ${others}`.slice(0, 1000);
  if (rec.verdict === 'verified') return [H.makeEvent({ ...base, type: 'verified', from: cand.state, to: 'done', reason, evidence })];
  if (rec.verdict === 'failed' && rec.attribution === 'own-regression') return [H.makeEvent({ ...base, type: 'verification-failed', from: cand.state, to: 'failed', class: rec.failing[0].class, reason, evidence })];
  if (rec.verdict === 'failed') return [H.makeEvent({ ...base, type: 'escalated', from: cand.state, to: 'escalated', class: 'cannot-tell-which-change', reason, evidence })];
  if (rec.verdict === 'not-yet' && rec.pin?.state === 'awaiting' && cand.state === 'merged_unverified') return [H.makeEvent({ ...base, type: 'awaiting-pin', from: cand.state, to: 'awaiting_pin', reason, evidence })];
  return [];
}
// Each event checked against the item as the history replays it (record-format.mjs).
export function checkEvents(item, events) {
  let it = item;
  const out = [];
  for (const ev of events) {
    const r = RF.applyEvent(it, ev);
    out.push({ type: ev.type, from: ev.from, to: ev.to, ...(ev.class ? { class: ev.class } : {}), legal: !r.errors.length, ...(r.errors.length ? { why: r.errors[0] } : {}) });
    if (!r.errors.length) it = r.item;
  }
  return out;
}

// ── the candidates: what the history holds in merged_unverified or awaiting_pin ─────────────
// own: the merge was the lane's (the merged event's actor). Practice: anyone else's, merged at
// or after `since`. A merge whose merged event rotated out (no merge commit) is skipped. type:
// the update type the history recorded (H1: digest or tag).
const packagesOf = d => (Array.isArray(d.pk) ? d.pk.map(([name, to]) => ({ name, to })) : d.name ? [{ name: d.name, to: d.to ?? null }] : []);
export function candidatesOf(doc, { since, practice, onlyPrs = null }) {
  const st = H.replay(doc);
  const merged = new Map();
  for (const ev of doc.events) if (ev.type === 'merged') merged.set(ev.item_id, ev);
  const out = [], skipped = [];
  for (const [id, s] of st.items) {
    if (!['merged_unverified', 'awaiting_pin'].includes(s.item.state)) continue;
    const ev = merged.get(id);
    if (!ev || !SHA.test(ev.evidence?.merge_commit || '')) { skipped.push({ item: id, why: 'its merged event rotated out of the history (no merge commit)' }); continue; }
    const own = ev.actor?.kind === 'lane';
    if (onlyPrs && !onlyPrs.includes(ev.carrier?.pr)) continue;
    if (!onlyPrs && !own && (!practice || !Number.isFinite(Date.parse(since)) || Date.parse(ev.at) < Date.parse(since))) continue;
    const d = doc.items[id] || {};
    out.push({ id, pr: ev.carrier?.pr ?? null, name: d.name ?? null, from: d.from ?? null, to: d.to ?? null, type: d.type ?? null, basis: d.basis ?? 'package-table', packages: packagesOf(d), merged_at: ev.at, merge_commit: ev.evidence.merge_commit,
      merged_by: { kind: ev.actor.kind, login: ev.actor.login ?? null }, own, state: s.item.state, attempt: RF.attemptCount(s.item), item: s.item });
  }
  return { list: out.sort((a, b) => Date.parse(a.merged_at) - Date.parse(b.merged_at) || a.pr - b.pr), skipped };
}

// ── the memory: data.verify on upgrade-lane-state ───────────────────────────────────────
export function parseMemory(text) {
  if (text === null || text === undefined || text === '') return { ok: true, absent: true, mem: { schema: VERIFY_SCHEMA, since: null, since_basis: null, items: {}, done: {} } };
  const m = parse(text);
  if (!m || m.schema !== VERIFY_SCHEMA || typeof m.items !== 'object' || Array.isArray(m.items)) return { ok: false, why: 'data.verify on upgrade-lane-state is not a haynes.upgrade-verify/v1 record', mem: { schema: VERIFY_SCHEMA, since: null, since_basis: null, items: {}, done: {} } };
  return { ok: true, absent: false, mem: { done: {}, ...m } };
}
// When the verifier was installed: the committer date of the oldest commit on main that
// touches this file. A fact in git, so a lost memory reads it again. null = unreadable.
export function readInstalledAt(repo, run) {
  const j = parse(tryRun(run, 'gh', ['api', '--paginate', '--slurp', `repos/${repo}/commits?sha=main&path=${encodeURIComponent(MODULE_PATH)}&per_page=100`]));
  const list = Array.isArray(j) ? j.flat() : null;
  if (!list?.length) return null;
  const c = list.at(-1);
  const at = Date.parse(c?.commit?.committer?.date || '');
  return SHA.test(c?.sha || '') && Number.isFinite(at) ? { since: iso(at), basis: `the first commit on main that added ${MODULE_PATH.split('/').pop()}: ${short(c.sha)} at ${iso(at)}` } : null;
}
// The lane's night of a tick: its local date in night.timezone (ticks run 01:00 to 05:45).
export const nightOf = (now, tz) => { try { return new Intl.DateTimeFormat('en-CA', { timeZone: tz || 'UTC', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(now)); } catch { return iso(now).slice(0, 10); } };

// ── the phase ───────────────────────────────────────────────────────────────────
const kList = (run, args) => { const j = parse(tryRun(run, 'kubectl', ['get', ...args, '-o', 'json'])); return Array.isArray(j?.items) ? j : null; };
async function getJson(url) {
  try { const r = await fetch(url, { method: 'GET', signal: AbortSignal.timeout(15000) }); return r.ok ? await r.json() : null; } catch { return null; }
}
// The image build of an own image for a commit: { state: success|failure|running|none, started,
// at } | null. started (the run's start) ties a pin to the build (L-d); at is its last update.
export function buildOf(runs, workflow) {
  if (!Array.isArray(runs)) return null;
  const mine = runs.filter(r => r?.path === workflow || String(r?.path || '').startsWith(`${workflow}@`));
  if (!mine.length) return { state: 'none' };
  const r = mine.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))[0];
  const started = r.run_started_at || r.created_at || null;
  return r.status !== 'completed' ? { state: 'running', started } : { state: r.conclusion === 'success' ? 'success' : 'failure', conclusion: r.conclusion, started, at: r.updated_at || null };
}

// One candidate, with every read it needs handed in (tests pass fakes). Returns its record.
// ctx.prior: its record from the last tick (the memory).
export function judge(cand, ctx) {
  const { rules, range } = ctx;
  const prior = ctx.prior || {};
  const rec = { pr: cand.pr, item: cand.id, name: cand.name, from: cand.from, to: cand.to, type: cand.type ?? null, merged_at: cand.merged_at, merge_commit: cand.merge_commit, merged_by: cand.merged_by, practice: !cand.own,
    flux_revision: ctx.sourceRev ?? null, facts: [], other_changes: null, baseline: null, mem: { failing: {}, errors: {}, stable: prior.mem?.stable ?? {} } };
  const files = cand.files;
  if (!files) {
    rec.facts.push(fact('source', 'unreadable', `the files of ${short(cand.merge_commit)} (the merge)`));
    return finish(rec, cand, ctx);
  }
  const ce = L.clusterEffect(files, rules);
  rec.cluster_effect = ce.effect; rec.targets = ce.targets; rec.files = files.slice(0, 100);
  // an own image whose digest pin Renovate auto-merges: verified through its pin
  const waits = ce.own_images.map(id => rules.own_images.find(o => o.id === id)).filter(o => o?.cluster_effect === 'delayed' && o.pin_automerge === true);
  if (ce.effect === 'delayed' && waits.length) return pinPath(rec, cand, waits, ctx);
  if (ce.effect !== 'immediate') {
    rec.facts.push(NA('source', `no cluster effect (${ce.effect}${ce.own_images.length ? `, own image ${ce.own_images.join(', ')}` : ''}): nothing in the cluster changes`));
    rec.other_changes = otherFor(cand, range, null, rules, null);
    return finish(rec, cand, ctx);
  }
  if (!ctx.ks || !ctx.hr) { rec.facts.push(fact('applied', 'unreadable', 'kustomizations or helmreleases')); rec.other_changes = { complete: false, why: 'no scope (Flux objects unreadable)', changes: [] }; return finish(rec, cand, ctx); }
  const sc = M.settleScope({ cluster_effect: 'immediate', targets: ce.targets }, ctx.ks, ctx.hr);
  rec.scope = { own: sc.own, chain: sc.chain.filter(x => !sc.own.includes(x)), namespaces: sc.namespaces };
  rec.other_changes = otherFor(cand, range, sc, rules, M.revisionSha(ctx.sourceRev), ctx.earlier || []);
  const clusterWide = L.clusterWide([...files, ...L.componentReach(files, rules).via.map(v => v.path)], rules);
  return judgeTarget(rec, cand, { merge_commit: cand.merge_commit, merged_at: cand.merged_at }, sc, clusterWide, { packages: cand.packages, type: cand.type }, ctx);
}

// The facts of one target. change: the commit whose rollout is judged (the merge, or an own
// image's pin) and when it merged; cand: the history's item (its events name its own merge);
// upd: { packages, type } of the change.
function judgeTarget(rec, cand, change, sc, clusterWide, upd, ctx) {
  const { now } = ctx;
  const m = change.merge_commit;
  const prior = ctx.prior || {};
  const seen = prior.mem || {};
  const src = factSource(m, ctx);
  rec.facts.push(src);
  const after = (name, why) => fact(name, 'waiting', why);
  if (src.result !== 'pass') {
    for (const n of ['applied', 'helmreleases', 'pods', 'alerts', 'image', 'rollout', 'stable']) rec.facts.push(after(n, 'read once Flux\'s source contains the merge'));
    return finish(rec, cand, ctx);
  }
  const ap = factApplied(m, sc, ctx, seen.failing, rec.mem.failing);
  rec.facts.push(ap);
  const w = workloadsOf(sc, ctx.ks, ctx.hr);
  rec.workloads = w.workloads.map(x => `${x.kind} ${x.ns}/${x.name}`).slice(0, 20);
  const hrf = factHelmReleases(w.helmreleases, ctx.hr, ctx, seen.failing, rec.mem.failing);
  rec.facts.push(hrf);
  const wl = w.workloads;
  if (w.unread.length) rec.facts.push(fact('workloads', 'unreadable', w.unread.join('; ')));
  const u = updatedOf(upd.packages, wl, w.helmreleases, ctx);
  rec.updated = [...u.containers, ...[...u.chartWorkloads].map(k => `${k}/*`), ...u.crons.map(c => `${wkey(c.w)}/${c.container}`)].slice(0, 20);
  rec.facts.push(factPods(change.merged_at, wl, u, ctx, seen.errors, rec.mem.errors));
  rec.facts.push(factAlerts(change.merged_at, sc, clusterWide, ctx));
  if (ap.result !== 'pass' || !['pass', 'not-applicable'].includes(hrf.result)) {
    for (const n of ['image', 'rollout', 'stable']) rec.facts.push(after(n, 'read once the target applied the merge and its HelmReleases are settled'));
    return finish(rec, cand, ctx);
  }
  const ro = factRollout(wl, ctx);
  const im = factImage(upd, u, ro.per, ctx, seen.failing, rec.mem.failing);
  rec.facts.push(im, ro.fact);
  const ex = im.result === 'pass' ? factExercised(u, change.merged_at, versionKindOf(upd.type), ctx) : (u.crons.length ? after('exercised', 'read once the image passes') : null);
  if (ex) rec.facts.push(ex);
  const podsOk = rec.facts.find(f => f.fact === 'pods')?.result === 'pass';
  const rolloutDone = ['pass', 'not-applicable'].includes(ro.fact.result);
  // stability is read once the rollout was complete on some tick: on this one, or on an
  // earlier one that recorded the pods (then a recorded pod gone unready fails); never when
  // the image fails
  const base = prior.baseline ?? null;
  if (rec.mem.stable.passed) {
    // N3: once stable has passed it stays passed for the item
    const s = factStable(wl, u, base, now, ctx, rolloutDone, rec.mem.stable);
    rec.facts.push(s.fact); rec.baseline = base; rec.mem.stable = s.mem;
  } else if (podsOk && !w.unread.length && (rolloutDone ? im.result === 'pass' : !!base?.pods && im.result !== 'fail')) {
    const s = factStable(wl, u, base, now, ctx, rolloutDone && im.result === 'pass', rec.mem.stable);
    rec.facts.push(s.fact);
    rec.baseline = s.baseline;
    rec.mem.stable = s.mem;
  } else rec.facts.push(after('stable', 'read once the image, the rollout and the pods pass'));
  return finish(rec, cand, ctx);
}

// An own-image source: verified only through its digest pin (N1, L-d). The pin is a merged
// history item that updates this image (its package is the image), whose PR's last commit was
// made after this source's build run started (or after the merge when no run is read: an image
// cannot carry the change before it), and whose commit on main changes one of the image's pins
// files. Before the pin has merged nothing of the update runs in the cluster, so nothing is
// failed: a failed or cancelled build, a blocked pin PR or no pin keep the item awaiting its pin
// with the reason, shown every morning and marked "needs a person" by the common rule. Only the
// facts read after the pin merged (its rollout) can fail.
function pinPath(rec, cand, images, ctx) {
  const { range, rules } = ctx;
  rec.other_changes = otherFor(cand, range, null, rules, null);
  const pinFiles = new Set(images.flatMap(o => o.pins || []));
  const names = new Set(images.map(o => normRepo(o.image)));
  const ids = images.map(o => o.id).join(', ');
  const wf = images[0].build;
  const b = ctx.builds ? ctx.builds(cand.merge_commit) : null;
  const st = b ? buildOf(b, wf) : null;
  const anchor = st?.started ?? cand.merged_at;
  rec.pin = { state: 'awaiting', images: images.map(o => o.id), build: st, anchor, since: st?.at ?? st?.started ?? cand.merged_at };
  if (!range?.complete) { rec.facts.push(fact('pin', 'unreadable', `the commits on main since the merge (${range?.why || 'not read'})`)); rec.pin.state = 'unreadable'; return finish(rec, cand, ctx); }
  const i = range.commits.findIndex(c => c.sha === cand.merge_commit);
  if (i < 0 && range.base !== cand.merge_commit) { rec.facts.push(fact('pin', 'unreadable', `${short(cand.merge_commit)} is older than the commits read this tick (more than the range limit since it)`)); rec.pin.state = 'unreadable'; return finish(rec, cand, ctx); }
  const skipped = [];
  let pin = null, pinItem = null;
  for (const c of range.commits.slice(i + 1).filter(x => x.files.some(f => pinFiles.has(f)))) {
    const item = c.pr ? ctx.itemByPr?.(c.pr) : null;
    if (!item || !item.packages.some(p => names.has(normRepo(p.name)))) { skipped.push(`${short(c.sha)}${c.pr ? ` #${c.pr}` : ''} (not an update of ${ids})`); continue; }
    const head = ctx.prHeadDate ? ctx.prHeadDate(c.pr) : null;
    if (!head) { rec.facts.push(fact('pin', 'unreadable', `when #${c.pr}, an update of ${ids}, was made (its last commit)`)); rec.pin.state = 'unreadable'; return finish(rec, cand, ctx); }
    if (Date.parse(head) < Date.parse(anchor)) { skipped.push(`#${c.pr} (made ${head}, before this build started at ${anchor})`); continue; }
    pin = c; pinItem = item; break;
  }
  if (skipped.length) rec.pin.not_the_pin = skipped.slice(0, 5);
  if (!pin) {
    const build = !st ? `the run of ${wf} for ${short(cand.merge_commit)} could not be read` : st.state === 'none' ? `no run of ${wf} for ${short(cand.merge_commit)}`
      : st.state === 'running' ? `the build ${wf} is running` : st.state === 'failure' ? `the build ${wf} for ${short(cand.merge_commit)} ended ${st.conclusion} (a later build may carry the change)` : `the build finished at ${st.at}`;
    const open = (ctx.openRecords || []).find(r => (r.packages || []).some(p => names.has(normRepo(p.name))) && (r.files || []).some(f => pinFiles.has(f)));
    const pr = !open ? 'no pin PR is open' : open.lane === 'BLOCKED' ? `pin PR #${open.pr} is blocked: ${open.class}: ${String(open.reason || '').slice(0, 200)}` : `pin PR #${open.pr} is open (${open.lane}/${open.class})`;
    if (open) { rec.pin.pr = open.pr; rec.pin.pr_state = open.lane === 'BLOCKED' ? `blocked: ${open.class}` : 'open'; }
    if (st?.state === 'failure') rec.pin.state = 'awaiting (build failed)';
    rec.facts.push(fact('pin', 'waiting', `awaiting the digest pin of ${ids}: ${build}; ${pr}${skipped.length ? `; not the pin: ${skipped.slice(0, 3).join(', ')}` : ''}`));
    return finish(rec, cand, ctx);
  }
  rec.pin = { ...rec.pin, state: 'merged', commit: pin.sha, pr: pin.pr, at: pin.at, item: pinItem.id };
  rec.facts.push(fact('pin', 'pass', `pinned by #${pin.pr} (${short(pin.sha)} at ${pin.at}), made after this build started at ${anchor}`));
  const ce = L.clusterEffect(pin.files, rules);
  if (!ctx.ks || !ctx.hr) { rec.facts.push(fact('applied', 'unreadable', 'kustomizations or helmreleases')); return finish(rec, cand, ctx); }
  const sc = M.settleScope({ cluster_effect: 'immediate', targets: ce.targets }, ctx.ks, ctx.hr);
  rec.scope = { own: sc.own, chain: sc.chain.filter(x => !sc.own.includes(x)), namespaces: sc.namespaces, via_pin: pin.pr };
  rec.other_changes = otherFor({ ...cand, merge_commit: pin.sha }, range, sc, rules, M.revisionSha(ctx.sourceRev), ctx.earlier || []);
  return judgeTarget(rec, cand, { merge_commit: pin.sha, merged_at: pin.at || cand.merged_at }, sc, L.clusterWide(pin.files, rules), { packages: pinItem.packages, type: pinItem.type }, ctx);
}

function finish(rec, cand, ctx) {
  const prior = ctx.prior || {};
  const v = verdictOf(rec.facts, rec.other_changes);
  Object.assign(rec, v);
  rec.verdict_at = iso(ctx.now);
  rec.verdict_since = prior.verdict === v.verdict && prior.verdict_since ? prior.verdict_since : rec.verdict_at;
  rec.ticks = (prior.ticks || 0) + 1;
  // final: not judged again (its record stays); the lane's own merge is judged on every tick
  // until the history moves it out of merged_unverified or awaiting_pin
  rec.final = ['verified', 'failed'].includes(v.verdict);
  // "needs a person" (H2, M4, L-e): open on needs_person_after_nights of the lane's nights AND
  // judged on at least needs_person_min_ticks of its ticks
  const night = nightOf(ctx.now, ctx.rules?.night?.timezone);
  const nights = ctx.cfg?.needs_person_after_nights || 2, minTicks = ctx.cfg?.needs_person_min_ticks || 8;
  rec.nights = rec.final ? (prior.nights || []) : uniq([...(prior.nights || []), night]).slice(-(nights + 1));
  rec.needs_person = !rec.final && rec.nights.length >= nights && rec.ticks >= minTicks;
  if (rec.needs_person) rec.why = `NEEDS A PERSON: open on ${rec.nights.length} of the lane's nights (${rec.nights.join(', ')}) and ${rec.ticks} ticks; ${rec.why}`;
  // a practice item that needs a person is given up (L-g): kept with its last facts, not judged
  // again, so it no longer holds the commit range open; the lane's own merge is never given up
  if (rec.needs_person && !cand.own) { rec.gave_up = true; rec.final = true; }
  const evs = eventsFor(cand, rec, iso(ctx.now), ctx.repo);
  rec.would_write = checkEvents(cand.item, evs);
  rec.events = evs.filter((_, i) => rec.would_write[i].legal);
  return rec;
}

// THE PHASE, once per tick: read the history's candidates, the facts they need, judge each.
// deps: { alertSubject (run-night.mjs), nsFacts(ns) (the tick's cache), ks, hr, source (when the
// tick already read them), records (the tick's open-PR lane records: open pin PRs), prom
// (Prometheus base URL), getJson (tests), doc (a history document: the CLI) }. Returns
// { summary, memory (the next data.verify, or null to keep the last), table, events (the lane's
// own merges' events, only when night.verify.write_history_events is true) }.
export async function verifyPhase({ rules, run, repo, now, startedAt = now, clock = () => Date.now(), prevText = null, prevUnreadable = false, deps = {}, onlyPrs = null }) {
  const t0 = clock();
  const cfg = verifyConfig(rules);
  const sum = { built: true, practice: cfg.practice ?? null, write_history_events: cfg.write_history_events ?? null, since: null, since_basis: null, history: null, candidates: 0, evaluated: [], deferred: [], kept_final: 0,
    verdicts: { verified: [], failed: [], 'not-yet': [], 'cannot-tell': [] }, needs_person: [], events_for_history: 0, practice_events_not_written: 0, reads: null, elapsed_ms: null, why_not: null };
  let reads = 0, gh = 0;
  const counted = (cmd, argv, o) => { reads++; if (cmd === 'gh') gh++; return run(cmd, argv, o); };
  // L-g: every read after the shared start checks the tick's elapsed time, inside the range read
  // and the per-merge reads too: once the budget is used a read fails and budgetHit says why
  let budgetHit = false;
  const timed = (cmd, argv, o) => { if (cfg.time_budget_seconds * 1e3 - (clock() - startedAt) <= 0) { budgetHit = true; if (o?.allowFail) return null; throw new Error('the verifier\'s time budget is used'); } return counted(cmd, argv, o); };
  const out = (extra = {}) => ({ summary: { ...sum, ...extra, reads: { commands: reads, github: gh }, elapsed_ms: clock() - t0 }, memory: null, table: null, events: [] });
  if (!cfg.ok) return out({ why_not: cfg.why });
  // L1: no read starts once the tick has run time_budget_seconds; the verifier stops, the tick goes on
  const left = () => cfg.time_budget_seconds * 1e3 - (clock() - startedAt);
  const stop = what => out({ why_not: `the time budget (${cfg.time_budget_seconds} s of the tick) was used before ${what}; nothing more this tick, the memory is kept` });
  // L3: a memory this tick cannot read is not overwritten
  if (prevUnreadable) return out({ why_not: 'upgrade-lane-state could not be read: the verifier\'s memory is unknown; nothing judged, the memory is kept' });
  const pm = parseMemory(prevText);
  const mem = structuredClone(pm.mem);
  if (!pm.ok) sum.memory = `${pm.why}; starting a new memory`;
  // since: kept, else read from git (a fact), else no practice this tick
  if (!onlyPrs && (!mem.since || !Number.isFinite(Date.parse(mem.since)))) {
    if (left() <= 0) return stop('reading the install time');
    const i = readInstalledAt(repo, counted); if (i) { mem.since = i.since; mem.since_basis = i.basis; }
  }
  sum.since = onlyPrs ? 'not used (named pull requests)' : mem.since; sum.since_basis = mem.since_basis;
  // the history
  let doc = deps.doc ?? null;
  if (!doc) {
    if (left() <= 0) return stop('reading the history');
    const r = M.readNamedConfigMap(H.HISTORY.name, counted);
    if (!r) return out({ history: 'unreadable: ConfigMap upgrade-agent/upgrade-ledger', why_not: 'the history could not be read; nothing verified this tick, the memory is kept' });
    const p = H.parseDoc(r.found ? r.cm : null);
    if (!p.ok) return out({ history: p.why, why_not: 'the history could not be read; nothing verified this tick, the memory is kept' });
    sum.history = r.found ? `read at resourceVersion ${p.rv}` : 'absent (no history yet)';
    doc = p.doc;
  } else sum.history = 'handed in';
  if (!onlyPrs && cfg.practice && !mem.since) sum.why_not = 'unreadable: when the verifier was installed (the first commit of night-verify.mjs on main); no practice verdict this tick';
  // listed: the candidate list is complete for practice too (L3: otherwise no open record is dropped)
  const listed = !!onlyPrs || (cfg.practice && !!mem.since);
  const cands = candidatesOf(doc, { since: mem.since, practice: cfg.practice && !!mem.since, onlyPrs });
  const isFinal = c => !!mem.done?.[c.id] || mem.items?.[c.id]?.final === true;
  const all = cands.list.filter(c => onlyPrs || c.own || !mem.done?.[c.id]);
  sum.candidates = all.length;
  if (cands.skipped.length) sum.skipped = cands.skipped.slice(0, 10);
  // the fair order: the least recently judged first, then the oldest merge
  const lastAt = c => mem.items?.[c.id]?.verdict_at || '';
  const order = [...all].sort((a, b) => lastAt(a).localeCompare(lastAt(b)) || Date.parse(a.merged_at) - Date.parse(b.merged_at));
  const pick = order.slice(0, cfg.max_items_per_tick);
  sum.deferred = order.slice(cfg.max_items_per_tick).map(c => c.pr);
  const docItems = H.replay(doc);
  if (pick.length) {
    // the reads every candidate shares, each behind the time budget (L1)
    const need = what => { if (left() > 0) return true; sum.stopped = `the time budget (${cfg.time_budget_seconds} s of the tick) was used before ${what}`; return false; };
    if (!need('reading the Flux objects')) return stop('reading the Flux objects');
    const ks = deps.ks ?? kList(counted, ['kustomizations.kustomize.toolkit.fluxcd.io', '-A']);
    const hr = deps.hr ?? (need('reading the HelmReleases') ? kList(counted, ['helmreleases.helm.toolkit.fluxcd.io', '-A']) : null);
    const scfg = M.settleConfig(rules);
    let source = deps.source;
    if (source === undefined && need('reading the GitRepository')) { const sl = kList(counted, ['gitrepositories.source.toolkit.fluxcd.io', '-n', scfg.source.namespace, '--field-selector', `metadata.name=${scfg.source.name}`]); source = sl ? (sl.items.find(g => g?.metadata?.name === scfg.source.name) || null) : undefined; }
    if (sum.stopped) return stop(sum.stopped.replace(/^.*used before /, ''));
    const sourceRev = source?.status?.artifact?.revision ?? null;
    const st = M.fluxStates({ cfg: scfg, ks, hr, source });
    // one range read from the oldest merge that fits the limit; older merges fall back to reads of their own
    let range = null;
    for (const c of [...pick].sort((a, b) => Date.parse(a.merged_at) - Date.parse(b.merged_at))) {
      if (!need('reading the commits on main')) return stop('reading the commits on main');
      range = M.readCommitRange(repo, c.merge_commit, 'main', timed, cfg.max_range_commits);
      if (budgetHit) return stop('the end of the commit-range read (it stopped inside the read)');
      if (range.complete || !/more than one read can attribute/.test(range.why || '')) break;
    }
    const pos = new Map((range?.complete ? range.commits : []).map((c, i) => [c.sha, i]));
    if (range?.complete) pos.set(range.base, -1);
    const memo = new Map();
    const contains = (m, x) => {
      if (!SHA.test(m || '') || !SHA.test(x || '')) return null;
      if (m === x) return true;
      if (pos.has(m) && pos.has(x)) return pos.get(x) >= pos.get(m);
      const k = `${m}...${x}`;
      if (!memo.has(k)) { const c = parse(tryRun(timed, 'gh', ['api', `repos/${repo}/compare/${m}...${x}?per_page=1`])); memo.set(k, !c?.status ? null : ['identical', 'ahead'].includes(c.status) ? true : ['behind', 'diverged'].includes(c.status) ? false : null); }
      return memo.get(k);
    };
    const nsCache = new Map();
    const own = ns => { if (!nsCache.has(ns)) nsCache.set(ns, { pods: kList(timed, ['pods', '-n', ns]), cronjobs: kList(timed, ['cronjobs', '-n', ns]), jobs: kList(timed, ['jobs', '-n', ns]) }); return nsCache.get(ns); };
    const pods = ns => { if (left() <= 0) { budgetHit = true; return { pods: null, cronjobs: null, jobs: null }; } return (deps.nsFacts || own)(ns); };
    let nodeList;
    const nodes = () => { if (nodeList === undefined) nodeList = kList(timed, ['nodes']); return nodeList; };
    const prom = deps.prom || 'http://kube-prometheus-stack-prometheus.observability.svc.cluster.local:9090';
    const gj = deps.getJson || getJson;
    if (!need('reading the alerts')) return stop('reading the alerts');
    const aj = await gj(`${prom}/api/v1/alerts`);
    const alerts = Array.isArray(aj?.data?.alerts) ? aj.data.alerts : null;
    const rt = alerts ? await gj(`${prom}/api/v1/status/runtimeinfo`) : null;
    const promStart = Number.isFinite(Date.parse(rt?.data?.startTime || '')) ? rt.data.startTime : null;
    const chartSource = h => {
      const ref = h.spec?.chartRef;
      const kind = ref?.kind === 'OCIRepository' ? 'ocirepositories.source.toolkit.fluxcd.io' : !ref && h.spec?.chart ? 'helmcharts.source.toolkit.fluxcd.io' : null;
      if (!kind) return null;
      const ns = ref?.namespace || h.metadata.namespace;
      const name = ref ? ref.name : `${h.metadata.namespace}-${h.metadata.name}`;
      const l = kList(timed, [kind, '-n', ns, '--field-selector', `metadata.name=${name}`]);
      const o = l?.items?.find(x => x.metadata?.name === name);
      if (!o) return null;
      const s = M.fluxSettleState(o);
      return { id: `${kind.split('.')[0]} ${ns}/${name}`, version: String(o.status?.artifact?.revision || '').split('@')[0] || null, state: s.state, why: s.why };
    };
    const builds = sha => { const j = parse(tryRun(timed, 'gh', ['api', `repos/${repo}/actions/runs?head_sha=${sha}&per_page=50`])); return Array.isArray(j?.workflow_runs) ? j.workflow_runs : null; };
    const prHeadDate = pr => { const j = parse(tryRun(timed, 'gh', ['pr', 'view', String(pr), '-R', repo, '--json', 'commits'])); const c = Array.isArray(j?.commits) ? j.commits.at(-1) : null; return Number.isFinite(Date.parse(c?.committedDate || '')) ? c.committedDate : null; };
    const itemByPr = pr => { const ids = H.prIndex(docItems).get(pr) || []; const id = ids.find(x => ['merged_unverified', 'awaiting_pin', 'done', 'failed'].includes(docItems.items.get(x)?.item.state)); if (!id) return null; const d = doc.items[id] || {}; return { id, type: d.type ?? null, packages: packagesOf(d) }; };
    const filesBySha = new Map((range?.complete ? range.commits : []).map(c => [c.sha, c.files]));
    const filesOf = c => { if (filesBySha.has(c.merge_commit)) return filesBySha.get(c.merge_commit); const j = parse(tryRun(timed, 'gh', ['api', `repos/${repo}/commits/${c.merge_commit}`])); const f = Array.isArray(j?.files) && j.files.length < 300 ? uniq(j.files.flatMap(x => [x.filename, x.previous_filename].filter(Boolean))) : null; if (f) filesBySha.set(c.merge_commit, f); return f; };
    // L4: the earlier merges whose own verdict is not final yet (their files, when known)
    const prevFiles = new Map(Object.entries(mem.items || {}).map(([id, r]) => [id, Array.isArray(r.files) ? r.files : null]));
    const earlierOf = c => cands.list.filter(x => x.id !== c.id && Date.parse(x.merged_at) < Date.parse(c.merged_at) && !isFinal(x))
      .map(x => ({ sha: x.merge_commit, pr: x.pr, at: x.merged_at, verdict: mem.items?.[x.id]?.verdict ?? null, files: filesBySha.get(x.merge_commit) ?? prevFiles.get(x.id) ?? null }));
    for (const c of pick) {
      if (left() <= 0) { sum.deferred.push(c.pr); sum.stopped = sum.stopped || `the time budget (${cfg.time_budget_seconds} s of the tick) was used; the rest is judged next tick`; continue; }
      const prev = mem.items?.[c.id] || null;
      budgetHit = false;
      const files = filesOf(c);
      const rec = judge({ ...c, files }, { rules, cfg, now, repo, range, ks, hr, st, sourceRev, contains, pods, nodes, alerts, promStart, alertSubject: deps.alertSubject, alertScope: rules.alert_scope || {}, chartSource, builds, prHeadDate, itemByPr,
        openRecords: deps.records || [], earlier: earlierOf(c), prior: prev });
      // a record judged while the budget ran out is not kept: its reads failed for want of time
      if (budgetHit) { sum.deferred.push(c.pr); sum.stopped = `the time budget (${cfg.time_budget_seconds} s of the tick) ran out while #${c.pr} was judged; its last record is kept, the rest is judged next tick`; break; }
      if (!rec.files && Array.isArray(prev?.files)) rec.files = prev.files;
      rec.first_at = prev?.first_at ?? rec.verdict_at;
      mem.items[c.id] = rec;
      sum.evaluated.push(c.pr);
    }
    for (const c of pick) if (!sum.evaluated.includes(c.pr) && !sum.deferred.includes(c.pr)) sum.deferred.push(c.pr);
    sum.range = range ? (range.complete ? { base: short(range.base), commits: range.commits.length } : { why: range.why }) : null;
  }
  // the verdicts, the events, the memory kept
  const ev = [];
  for (const c of pick) {
    const r = mem.items[c.id];
    if (!r || r.verdict_at !== iso(now)) continue;
    sum.verdicts[r.verdict]?.push(c.pr);
    if (r.needs_person) sum.needs_person.push(c.pr);
    if (r.gave_up) (sum.given_up = sum.given_up || []).push(c.pr);
    if (c.own && cfg.write_history_events) ev.push(...(r.events || []));
    else if (!c.own) sum.practice_events_not_written += (r.events || []).length;
  }
  for (const r of Object.values(mem.items)) delete r.events; // never kept: rebuilt from the facts
  const live = new Set(cands.list.map(c => c.id));
  for (const [id, r] of Object.entries(mem.items)) if (r.final) mem.done[id] = `${r.gave_up ? 'given-up' : r.verdict} ${r.verdict_at}`;
  // forget only what a complete candidate list no longer holds (L3): done is there to skip a
  // candidate, and an open record holds the pods recorded for stability
  if (listed) {
    const rotated = new Set(cands.skipped.map(x => x.item));
    for (const id of Object.keys(mem.done)) if (!live.has(id)) delete mem.done[id];
    for (const id of Object.keys(mem.items)) if (!live.has(id) && !mem.items[id].final) {
      // L-e: said, not silent
      (sum.forgotten = sum.forgotten || []).push({ pr: mem.items[id].pr, item: id, verdict: mem.items[id].verdict, why: rotated.has(id) ? 'its merged event rotated out of the history' : 'the history no longer holds it in merged_unverified or awaiting_pin' });
      delete mem.items[id];
    }
  }
  mem.at = iso(now);
  const mergedAt = new Map(cands.list.map(c => [c.id, c.merged_at]));
  const fit = fitMemory(mem, cfg, id => (mem.items[id] ? !mem.items[id].final : false), id => mergedAt.get(id) ?? mem.items[id]?.merged_at ?? '');
  Object.assign(sum, fit.counts);
  sum.kept_final = Object.keys(mem.done).length;
  sum.events_for_history = ev.length;
  sum.reads = { commands: reads, github: gh };
  const result = out();
  result.summary = { ...sum, elapsed_ms: clock() - t0 };
  // nothing to keep yet (no install time, no record): no memory to write
  result.memory = !mem.since && !Object.keys(mem.items).length && !Object.keys(mem.done).length ? null : fit.text;
  const table = tableOf(mem, now);
  result.table = result.memory === null ? null : Buffer.byteLength(table) <= cfg.max_state_bytes ? table : `${Buffer.from(table).subarray(0, cfg.max_state_bytes - 80).toString('utf8').replace(/\n[^\n]*$/, '')}\n… (cut at ${cfg.max_state_bytes} bytes; data.verify has every record)`;
  result.events = ev;
  result.records = mem.items;
  return result;
}

// The memory within its bounds (review of PR 4880). An open record (not final) holds the pods
// recorded for stability, so it goes last. In order:
//   1. final records beyond max_items_kept, oldest merge first (display only: done keeps them
//      from being judged again);
//   2. over max_state_bytes: final records, oldest first; then open records lose their
//      details (facts, other changes, the events they would write), keeping the verdict, the
//      recorded pods and the counts; then the oldest done entries (such an item may be judged
//      again); last, the oldest open records, counted in trimmed_open.
export function fitMemory(mem, cfg, isOpen, mergedAtOf) {
  const counts = { trimmed: 0, compacted: 0, done_trimmed: 0, trimmed_open: 0 };
  const byAge = ids => [...ids].sort((a, b) => String(mergedAtOf(a)).localeCompare(String(mergedAtOf(b))));
  const finals = () => byAge(Object.keys(mem.items).filter(id => !isOpen(id)));
  const opens = () => byAge(Object.keys(mem.items).filter(id => isOpen(id)));
  const extra = Object.keys(mem.items).length - cfg.max_items_kept;
  for (const id of finals().slice(0, Math.max(0, extra))) { delete mem.items[id]; counts.trimmed++; }
  let text = JSON.stringify(mem);
  const over = () => Buffer.byteLength(text) > cfg.max_state_bytes;
  for (const id of finals()) { if (!over()) break; delete mem.items[id]; counts.trimmed++; text = JSON.stringify(mem); }
  for (const id of opens()) {
    if (!over()) break;
    const r = mem.items[id];
    mem.items[id] = { pr: r.pr, item: r.item, name: r.name, from: r.from, to: r.to, type: r.type ?? null, merged_at: r.merged_at, merged_by: r.merged_by, practice: r.practice, verdict: r.verdict, why: String(r.why || '').slice(0, 300),
      verdict_at: r.verdict_at, verdict_since: r.verdict_since, first_at: r.first_at, ticks: r.ticks, final: r.final, nights: r.nights, needs_person: r.needs_person, gave_up: r.gave_up, baseline: r.baseline ?? null, mem: r.mem, files: r.files, compacted: true };
    counts.compacted++; text = JSON.stringify(mem);
  }
  for (const id of byAge(Object.keys(mem.done))) { if (!over()) break; delete mem.done[id]; counts.done_trimmed++; text = JSON.stringify(mem); }
  for (const id of opens()) { if (!over()) break; delete mem.items[id]; counts.trimmed_open++; text = JSON.stringify(mem); }
  return { text, counts: Object.fromEntries(Object.entries(counts).filter(([, v]) => v)) };
}

// The morning view: one block per update, newest merge first. An open verdict says what it
// waits for, since when, the lane's nights it has been open and the replacement counts.
export function tableOf(mem, now = Date.now()) {
  const lines = [];
  for (const r of Object.values(mem.items || {}).sort((a, b) => String(b.merged_at).localeCompare(String(a.merged_at)))) {
    lines.push(`#${r.pr} ${r.name ?? '?'} ${r.from ?? '?'} -> ${r.to ?? '?'}, merged ${r.merged_at} by ${r.merged_by?.login ?? r.merged_by?.kind ?? '?'}${r.practice ? ' (practice)' : ''}: ${r.gave_up ? 'GIVEN UP, NEEDS A PERSON: ' : r.needs_person ? 'NEEDS A PERSON: ' : ''}${String(r.verdict).toUpperCase()}${r.attribution ? ` (${r.attribution})` : ''} at ${r.verdict_at}`);
    const sm = r.mem?.stable || {};
    const counts = [...Object.entries(sm.moves || {}).map(([k, n]) => `${k} moved ${n}x`), ...Object.entries(sm.replaced || {}).map(([k, n]) => `${k} replaced ${n}x`)].join(', ');
    if (!r.final || r.gave_up) lines.push(`  ${r.gave_up ? 'given up' : 'open'}: ${r.verdict} since ${r.verdict_since ?? r.first_at ?? '?'}; judged on ${r.ticks ?? 1} tick(s) over ${(r.nights || []).length} night(s) (${(r.nights || []).join(', ')})${counts ? `; pods ${counts}` : ''}${r.baseline?.at ? `; pods recorded at ${r.baseline.at} (${ago(now, r.baseline.at)} ago)` : ''}`);
    if (r.compacted) { lines.push('  (facts and other changes cut to keep data.verify within max_state_bytes; the verdict and the recorded pods are kept)'); continue; }
    for (const f of r.facts || []) lines.push(`  ${f.fact}: ${f.result}${f.class ? ` [${f.class}]` : ''}${f.detail ? `: ${f.detail}` : ''}`);
    const oc = r.other_changes;
    lines.push(`  other changes: ${!oc ? 'not read' : oc.applicable === false ? 'not applicable (no target)' : !oc.complete ? `unreadable (${oc.why})${oc.changes?.length ? `; known: ${oc.changes.map(c => `${short(c.sha)}${c.pr ? ` #${c.pr}` : ''}`).join(', ')}` : ''}` : oc.changes.length ? oc.changes.map(c => `${short(c.sha)}${c.pr ? ` #${c.pr}` : ''} ${c.subject} [${c.parts.join('; ')}]${c.fetched_by_flux === false ? ' (not fetched by Flux yet)' : ''}`).join(' | ') : 'none on the target, its chain or a cluster-wide part'}`);
    if ((r.would_write || []).length) lines.push(`  ${r.practice ? 'would write (not written: practice)' : 'events'}: ${r.would_write.map(e => `${e.type} ${e.from}->${e.to}${e.class ? ` [${e.class}]` : ''}${e.legal ? '' : ` REFUSED (${e.why})`}`).join(', ')}`);
  }
  return lines.join('\n');
}
// The tick's log line.
export function verifyLine(s) {
  if (!s) return 'not run';
  if (s.error) return `FAILED: ${s.error}`;
  const v = Object.entries(s.verdicts || {}).filter(([, l]) => l.length).map(([k, l]) => `${k} ${l.map(p => `#${p}`).join(', ')}`).join('; ');
  return `${s.evaluated?.length || 0} judged${v ? ` (${v})` : ''}${s.needs_person?.length ? `; NEEDS A PERSON ${s.needs_person.map(p => `#${p}`).join(', ')}` : ''}${s.given_up?.length ? ` (practice given up: ${s.given_up.map(p => `#${p}`).join(', ')})` : ''}${s.forgotten?.length ? `; forgotten ${s.forgotten.map(x => `#${x.pr} (${x.why})`).join(', ')}` : ''}${s.deferred?.length ? `, deferred ${s.deferred.map(p => `#${p}`).join(', ')}` : ''}; since ${s.since ?? 'unreadable'}; history ${s.history ?? 'not read'}${s.why_not ? `; ${s.why_not}` : ''}${s.stopped ? `; ${s.stopped}` : ''}; ${s.events_for_history} event(s) for the history, ${s.practice_events_not_written} practice event(s) not written; ${s.reads?.commands ?? 0} reads (${s.reads?.github ?? 0} GitHub); ${s.elapsed_ms ?? '?'} ms`;
}

// A history document of merged pull requests (gh pr view), each item named by the lane's
// route (upgrade-history.mjs laneItem) with its merged event, as a backfill records it. actor
// overrides who merged (tests: the lane's own merge).
export function docOf(views, { repo, rules, at, actor = null }) {
  const hcfg = H.historyConfig(rules);
  const doc = H.emptyDoc();
  for (const e of views) {
    const li = H.laneItem(e, repo, rules, at);
    const a = actor || H.actorOf(e.mergedBy?.login, hcfg) || { kind: 'unknown', login: null };
    doc.items[li.id] = li.desc;
    const t = Date.parse(e.mergedAt), carrier = { repo, pr: e.number }, mc = e.mergeCommit?.oid ?? e.mergeCommit;
    if (a.kind === 'lane') {
      // the lane's own merge, as its merge step records it: observed, merge-attempted, merged
      const lane = { kind: 'lane', login: null };
      doc.events.push(H.makeEvent({ item_id: li.id, type: 'observed', from: null, to: 'needed', at: iso(t - 120e3), actor: lane, recorded_by: 'lane', lane: 'A-night', class: 'cluster-change', reason: 'lane A', carrier }));
      doc.events.push(H.makeEvent({ item_id: li.id, type: 'merge-attempted', from: 'needed', to: 'in_progress', at: iso(t - 60e3), actor: lane, recorded_by: 'lane', attempt: 1, lane: 'A-night', carrier }));
      doc.events.push(H.makeEvent({ item_id: li.id, type: 'merged', from: 'in_progress', to: 'merged_unverified', at: iso(t), actor: lane, recorded_by: 'lane', attempt: 1, carrier, evidence: { merge_commit: mc, merge_call: 'gh pr merge (exit 0)' } }));
    } else doc.events.push(H.makeEvent({ item_id: li.id, type: 'merged', from: null, to: 'merged_unverified', at: iso(t), actor: a, recorded_by: 'backfill', recorded_at: at, attempt: 1, carrier, evidence: { merge_commit: mc } }));
  }
  return doc;
}

// ── CLI (read-only): verify named merged pull requests now ─────────────────────────────
// node night-verify.mjs --pr <n> [--pr <n> ...] [--state <local file>] [--alerts-file <file>] [--json]
// Builds each PR's item as the history would (laneItem, a merged event), reads the facts and
// prints the verdicts. --state keeps the two-tick memory in a LOCAL file between runs;
// --alerts-file reads the critical alerts from a local file in the shape of Prometheus'
// /api/v1/alerts, with prometheus_start (a session pod cannot reach Prometheus; read them
// through Grafana). Nothing in the cluster or on GitHub is written.
const USAGE = 'usage: night-verify.mjs --pr <number> [--pr <number> ...] [--state <local file>] [--alerts-file <file>] [--json]';
async function main(argv) {
  const prs = [], o = { json: false, state: null, alerts: null };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--pr' && /^\d+$/.test(argv[i + 1] || '')) prs.push(Number(argv[++i]));
    else if (argv[i] === '--state' && argv[i + 1]) o.state = argv[++i];
    else if (argv[i] === '--alerts-file' && argv[i + 1]) o.alerts = argv[++i];
    else if (argv[i] === '--json') o.json = true;
    else { console.error(USAGE); return 2; }
  }
  if (!prs.length) { console.error(USAGE); return 2; }
  const rules = L.loadRules(process.env.UPGRADE_RULES || undefined);
  const repo = process.env.UPGRADE_AGENT_REPO || 'thaynes43/haynes-ops';
  const run = (cmd, a, x = {}) => L.runReadOnly(cmd, a, { env: process.env, ...x });
  const now = Date.now();
  const views = [];
  for (const n of prs) {
    const e = parse(tryRun(run, 'gh', ['pr', 'view', String(n), '-R', repo, '--json', 'number,state,title,author,mergedAt,mergedBy,mergeCommit,labels,body,files,changedFiles']));
    if (e?.state !== 'MERGED' || !SHA.test(e.mergeCommit?.oid || '')) { console.error(`#${n}: not merged, or unreadable; skipped`); continue; }
    views.push(e);
  }
  const doc = docOf(views, { repo, rules, at: iso(now) });
  const N = await import('./run-night.mjs');
  const prevText = o.state && existsSync(o.state) ? readFileSync(o.state, 'utf8') : null;
  const file = o.alerts ? parse(readFileSync(o.alerts, 'utf8')) : null;
  const fromFile = o.alerts ? async url => (/runtimeinfo$/.test(url) ? (file?.prometheus_start ? { data: { startTime: file.prometheus_start } } : null) : file) : undefined;
  const r = await verifyPhase({ rules, run, repo, now, prevText, deps: { doc, alertSubject: N.alertSubject, prom: process.env.PROMETHEUS_URL, getJson: fromFile }, onlyPrs: prs });
  if (o.state && r.memory) writeFileSync(o.state, r.memory);
  if (o.json) process.stdout.write(JSON.stringify({ summary: r.summary, records: r.records }, null, 2) + '\n');
  else process.stdout.write(`${r.table || '(no verdict)'}\n\nverifier: ${verifyLine(r.summary)}\n`);
  return 0;
}
if (L.isMain(import.meta.url)) main(process.argv.slice(2)).then(c => process.exit(c)).catch(e => { console.error(`night-verify: FAILED: ${e.message}`); process.exit(1); });
