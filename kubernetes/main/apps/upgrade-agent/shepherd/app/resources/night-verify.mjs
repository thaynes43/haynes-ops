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
//   source        Flux's GitRepository revision contains the merge commit.
//   applied       the target's own Kustomizations applied a revision that contains it and are
//                 Ready; its dependsOn chain and the Kustomizations that apply it are settled
//                 (the settle facts of story 551: night-merge.mjs settleScope, fluxStates).
//   helmreleases  the target's HelmReleases are settled and their last release is deployed.
//   pods          no pod of the target's workloads made since the merge is in CrashLoopBackOff
//                 or failing to pull its image.
//   alerts        no critical alert that belongs to the target or its chain started firing at
//                 or after the merge (R5's tracing, run-night.mjs alertSubject).
//   image         the target's workloads run the new tag or digest (pods' images and image
//                 ids), or its HelmRelease runs the new chart version.
//   rollout       each Deployment, StatefulSet and DaemonSet of the target (from the
//                 Kustomization and HelmRelease inventories) has pods of one template only,
//                 none terminating, all Ready.
//   stable        two ticks, no timer: on an earlier tick the rollout was complete and its
//                 pods were recorded (uid, restarts); on a later tick the same pods are still
//                 Ready with no higher restart count.
//   pin           an own image whose digest pin Renovate auto-merges (own_image_pin_wait):
//                 verified only through its pin, which must merge, apply and be stable.
//
// OTHER CHANGES (Tom's ruling Q-548-04). Every verdict lists the other commits on main between
// the merge and the verdict that reach the target, its chain or a cluster-wide part
// (night-merge.mjs otherChanges, story 551), or says there were none. A passing verdict stands
// whatever else merged. A failing verdict with such a change, or with that list unreadable, is
// "cannot tell which change did it"; with none it is the update's own regression.
//
// PRACTICE. The lane is in dry-run and merges nothing, so the verifier practises on merges
// the lane did not make: items the history (ConfigMap upgrade-ledger) holds in
// merged_unverified whose merge is at or after the verifier was installed, the committer date
// of the first commit on main that added this file (a fact in git, read once and kept). A
// practice verdict goes to upgrade-lane-state only (data.verify, data.verify_table): it never
// writes the history, never opens a pull request, never reverts, and never changes a merge
// verdict, the change lock, the PR records or the state write. Its record shows the events it
// WOULD write, checked against the item with record-format.mjs applyEvent.
//
// THE LANE'S OWN MERGES. A merge the lane made (the history's merged event by actor lane)
// gets its verdict as history events: verified, verification-failed (own regression),
// escalated (cannot tell which change did it) or awaiting-pin, through historyPhase. Only
// when night.verify.write_history_events is true in upgrade-rules.json, which ships false.
//
// READS ONLY. Every command goes through the read-only runner it is given (lane-classify.mjs
// runReadOnly: gh pr view, gh api GET, kubectl get); Prometheus through one GET. Nothing here
// starts a process of its own or writes anything; the CLI below prints, and with --state it
// keeps its two-tick memory in a local file.
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
// the failure modes of verification-failed: the format's six (upgrade-states.v1.json) and two
// the story names that the format's text does not (crash-loop, image-pull)
export const CLASSES = Object.freeze(['not-applied', 'not-ready', 'wrong-image', 'restart-growth', 'critical-alert', 'pin-timeout', 'crash-loop', 'image-pull']);
const SHA = /^[0-9a-f]{40}$/;
const WORKLOAD_KINDS = ['Deployment', 'StatefulSet', 'DaemonSet'];
const PULL_FAILURES = ['ErrImagePull', 'ImagePullBackOff', 'InvalidImageName', 'ErrImageNeverPull'];
const short = s => String(s || '').slice(0, 8);
const iso = v => new Date(typeof v === 'number' ? v : Date.parse(v)).toISOString();
const parse = s => { try { return JSON.parse(s); } catch { return null; } };
const tryRun = (run, cmd, argv) => { try { return run(cmd, argv, { allowFail: true }); } catch { return null; } };
const lbl = o => o?.metadata?.labels || {};
const uniq = a => [...new Set(a)];

// ── config: night.verify in upgrade-rules.json ─────────────────────────────────────
export function verifyConfig(rules) {
  const c = rules?.night?.verify || {};
  const int = (v, lo, hi) => Number.isInteger(v) && v >= lo && v <= hi;
  const ok = typeof c.practice === 'boolean' && typeof c.write_history_events === 'boolean' && int(c.max_items_per_tick, 1, 50) && int(c.max_items_kept, 1, 300)
    && int(c.max_range_commits, 1, 250) && int(c.time_budget_seconds, 10, 400) && int(c.pin_wait_pushes, 1, 50) && int(c.max_state_bytes, 8192, 262144);
  return { ok, ...c, why: ok ? null : 'night.verify in upgrade-rules.json is invalid (practice and write_history_events booleans; max_items_per_tick 1 to 50; max_items_kept 1 to 300; max_range_commits 1 to 250; time_budget_seconds 10 to 400; pin_wait_pushes 1 to 50; max_state_bytes 8192 to 262144)' };
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
export const isDigestVersion = v => /^(sha256:)?[0-9a-f]{7,64}$/.test(String(v ?? '')) && /[a-f]/.test(String(v));
// Does a container run version `to` of its image? true | false | null (cannot tell).
// A digest update compares the digest in the spec or the image id the kubelet reports; a
// tag update compares the tag, with a leading v or a pin operator ignored (bareVersion).
export function versionMatches(c, to) {
  if (to === null || to === undefined || to === '') return null;
  if (isDigestVersion(to)) {
    const want = String(to).replace(/^sha256:/, '');
    const have = [digestOf(c.digest), digestOf(c.image_id)].filter(Boolean);
    return have.length ? have.some(h => h.startsWith(want)) : null;
  }
  if (!c.tag) return null;
  return c.tag === to || L.bareVersion(c.tag) === L.bareVersion(to);
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
  return { workloads: workloads.filter(w => { const k = `${w.kind}/${w.ns}/${w.name}`; return !seen.has(k) && seen.add(k); }), helmreleases: [...hrIds], unread };
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
const restartsOf = p => (p?.status?.containerStatuses || []).reduce((n, c) => n + (Number.isInteger(c.restartCount) ? c.restartCount : 0), 0);
const containersOf = p => (p?.spec?.containers || []).map(c => {
  const st = (p.status?.containerStatuses || []).find(s => s.name === c.name) || {};
  return { pod: p.metadata.name, container: c.name, ...parseImage(c.image), image: c.image, image_id: st.imageID || null };
});

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

// ── the facts of one target (PURE over the reads it is handed) ───────────────────────────
// ctx: { st (M.fluxStates), contains(m, x) -> true|false|null, pods(ns) -> { pods, cronjobs, jobs }
// (null lists = unreadable), alerts (Prometheus /api/v1/alerts data.alerts, or null), alertSubject,
// alertScope, sourceRev, chartSource(h) -> { revision, state } | null, now }
export function factSource(m, ctx) {
  const s = M.revisionSha(ctx.sourceRev);
  if (!s) return fact('source', 'unreadable', `the artifact revision of Flux's GitRepository (${JSON.stringify(ctx.sourceRev ?? null)})`);
  const has = ctx.contains(m, s);
  if (has === null) return fact('source', 'unreadable', `whether Flux's revision ${short(s)} contains ${short(m)} (a compare on GitHub)`);
  return has ? fact('source', 'pass', `Flux's source is at ${short(s)}, which contains ${short(m)}`) : fact('source', 'waiting', `Flux's source is at ${short(s)}, which does not contain ${short(m)} yet`);
}

export function factApplied(m, sc, ctx) {
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
      else if (has === true) parts.push({ r: 'fail', class: 'not-ready', d: `Kustomization ${id} applied ${short(applied)} and is failing: ${s.why}` });
      else if (attempted && ctx.contains(m, attempted) === true) parts.push({ r: 'fail', class: 'not-applied', d: `Kustomization ${id} attempted ${short(attempted)} and did not apply it: ${s.why}` });
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

export function factHelmReleases(m, hrIds, hrList, ctx) {
  if (!hrIds.length) return NA('helmreleases', 'the target has no HelmRelease');
  const parts = [];
  for (const id of hrIds) {
    const h = (hrList?.items || []).find(x => `${x.metadata.namespace}/${x.metadata.name}` === id);
    if (!h) { parts.push({ r: 'unreadable', d: `HelmRelease ${id} is not listed` }); continue; }
    if (h.spec?.suspend === true) { parts.push({ r: 'unreadable', d: `HelmRelease ${id} is suspended: cannot tell until someone resumes it` }); continue; }
    const s = ctx.st.hrState.get(id) || M.fluxSettleState(h);
    const last = (h.status?.history || [])[0] || null;
    if (s.state === 'failing') { parts.push({ r: 'fail', class: 'not-ready', d: `HelmRelease ${id}: ${s.why}${last ? ` (last release ${last.chartVersion ?? '?'} ${last.status ?? '?'})` : ''}` }); continue; }
    if (s.state === 'progressing') { parts.push({ r: 'waiting', d: `HelmRelease ${id}: ${s.why}` }); continue; }
    if (!last) { parts.push({ r: 'unreadable', d: `HelmRelease ${id} has no release history` }); continue; }
    if (last.status === 'failed') { parts.push({ r: 'fail', class: 'not-ready', d: `HelmRelease ${id}: its last release (${last.chartVersion}) failed` }); continue; }
    if (last.status !== 'deployed') { parts.push({ r: 'waiting', d: `HelmRelease ${id}: its last release is ${last.status}` }); continue; }
    parts.push({ r: 'pass', d: `${id} Ready, ${last.chartName ?? 'chart'} ${String(last.chartVersion || '').split('+')[0]} deployed ${last.lastDeployed ?? ''}`.trim() });
  }
  return fold('helmreleases', parts);
}

// The workloads' pods: crash loops and image-pull failures in pods made at or after the merge
// fail; the same in older pods is listed (it was there before the update).
export function factPods(mergedAt, wl, ctx) {
  const parts = [], older = [];
  const t = Date.parse(mergedAt);
  for (const w of wl.filter(x => x.kind !== 'CronJob')) {
    const f = ctx.pods(w.ns);
    if (!f?.pods) { parts.push({ r: 'unreadable', d: `pods in ${w.ns}` }); continue; }
    for (const p of current(podsOf(w, f.pods.items))) {
      const made = Date.parse(p.metadata?.creationTimestamp || '');
      for (const c of [...(p.status?.initContainerStatuses || []), ...(p.status?.containerStatuses || [])]) {
        const why = c.state?.waiting?.reason;
        const cls = why === 'CrashLoopBackOff' ? 'crash-loop' : PULL_FAILURES.includes(why) ? 'image-pull' : null;
        if (!cls) continue;
        const d = `${w.kind} ${w.ns}/${w.name}: pod ${p.metadata.name} container ${c.name} is ${why} (restarts ${c.restartCount ?? '?'})`;
        if (!Number.isFinite(made)) parts.push({ r: 'unreadable', d: `when pod ${p.metadata.name} was made (it is ${why})` });
        else if (made >= t) parts.push({ r: 'fail', class: cls, d });
        else older.push(`${d}, made before the merge`);
      }
    }
  }
  const pods = wl.filter(x => x.kind !== 'CronJob').length;
  return fold('pods', parts, pods ? `no crash loop or image-pull failure in the pods made since the merge${older.length ? `; before the merge: ${older.join('; ')}` : ''}` : 'the target has no Deployment, StatefulSet or DaemonSet');
}

// Critical alerts by R5's tracing: an alert belongs to the target when its subject traces to
// the target's Kustomization or its dependsOn chain. New = it started firing at or after the
// merge (Prometheus activeAt). A new alert the tracing cannot place in a target namespace is
// unreadable; an older one is listed.
export function factAlerts(mergedAt, sc, clusterWide, ctx) {
  if (!Array.isArray(ctx.alerts)) return fact('alerts', 'unreadable', 'critical alerts (Prometheus /api/v1/alerts)');
  if (typeof ctx.alertSubject !== 'function') return fact('alerts', 'unreadable', 'the alert tracing (run-night.mjs alertSubject) was not handed in');
  const fo = ctx.alertScope?.flux_object || {};
  const chain = new Set(sc.chain || []);
  const chainNs = new Set();
  for (const id of sc.chain || []) { chainNs.add(id.split('/')[0]); const k = ctx.st.byId.get(id); if (k?.spec?.targetNamespace) chainNs.add(k.spec.targetNamespace); }
  const t = Date.parse(mergedAt);
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
    if (res === 'belongs') parts.push(isNew === null ? { r: 'unreadable', d: `whether ${d} is new` } : { r: 'fail', class: 'critical-alert', d });
    else parts.push({ r: 'unreadable', d: `cannot tell whether ${d} belongs to the target` });
  }
  return fold('alerts', parts, `no new critical alert on the target or its chain${older.length ? `; firing since before the merge: ${older.slice(0, 5).join('; ')}` : ''}`);
}

// The packages of the update in the target's workloads: containers whose image is the
// package (pods; CronJob templates when no pod runs it), else the HelmRelease whose chart it
// is. A package found nowhere is unreadable.
export function factImage(cand, wl, hrIds, rollout, ctx) {
  const parts = [];
  if (!cand.packages.length || cand.packages.some(p => !p.name)) return fact('image', 'unreadable', 'the update names no package (no package table in the PR)');
  for (const pkg of cand.packages) {
    const want = normRepo(pkg.name);
    const conts = [], crons = [];
    let podsUnread = false;
    for (const w of wl) {
      const f = ctx.pods(w.ns);
      if (w.kind === 'CronJob') {
        const cj = (f?.cronjobs?.items || []).find(x => x.metadata?.name === w.name);
        if (!f?.cronjobs) podsUnread = true;
        for (const c of cj?.spec?.jobTemplate?.spec?.template?.spec?.containers || []) { const im = parseImage(c.image); if (im?.repo === want) crons.push({ w, ...im, image: c.image }); }
        continue;
      }
      if (!f?.pods) { podsUnread = true; continue; }
      for (const p of current(podsOf(w, f.pods.items))) for (const c of containersOf(p)) if (c.repo === want) conts.push({ w, ...c });
    }
    const label = `${pkg.name} ${pkg.to ?? '(no version)'}`;
    if (conts.length) {
      const unk = conts.filter(c => versionMatches(c, pkg.to) === null), bad = conts.filter(c => versionMatches(c, pkg.to) === false);
      if (unk.length) parts.push({ r: 'unreadable', d: `the version ${unk[0].pod}/${unk[0].container} runs (${unk[0].image})` });
      else if (bad.length) {
        const rolling = bad.some(c => rollout.get(`${c.w.kind}/${c.w.ns}/${c.w.name}`) === 'waiting');
        parts.push(rolling ? { r: 'waiting', d: `${bad.length} container(s) still run ${bad[0].image} while ${bad[0].w.kind} ${bad[0].w.ns}/${bad[0].w.name} rolls` }
          : { r: 'fail', class: 'wrong-image', d: `${bad.length} of ${conts.length} container(s) run ${bad[0].image}, not ${label} (${bad[0].pod}/${bad[0].container}), and nothing is rolling` });
      } else parts.push({ r: 'pass', d: `${conts.length} container(s) in ${uniq(conts.map(c => `${c.w.ns}/${c.w.name}`)).join(', ')} run ${label}` });
      continue;
    }
    if (crons.length) {
      const bad = crons.filter(c => versionMatches(c, pkg.to) !== true);
      parts.push(bad.length ? { r: 'fail', class: 'wrong-image', d: `CronJob ${bad[0].w.ns}/${bad[0].w.name} names ${bad[0].image}, not ${label}` }
        : { r: 'pass', d: `CronJob ${crons.map(c => `${c.w.ns}/${c.w.name}`).join(', ')} names ${label} (no pod runs it now)` });
      continue;
    }
    // a chart: the HelmRelease's last release is that chart at that version
    const chartName = want.split('/').pop();
    const hrs = hrIds.map(id => (ctx.hr?.items || []).find(x => `${x.metadata.namespace}/${x.metadata.name}` === id)).filter(Boolean)
      .filter(h => String((h.status?.history || [])[0]?.chartName || '').toLowerCase() === chartName);
    if (hrs.length) {
      for (const h of hrs) {
        const id = `${h.metadata.namespace}/${h.metadata.name}`, last = h.status.history[0];
        const ver = String(last.chartVersion || '').split('+')[0];
        if (L.bareVersion(ver) === L.bareVersion(pkg.to) && last.status === 'deployed') { parts.push({ r: 'pass', d: `HelmRelease ${id} runs chart ${last.chartName} ${ver}` }); continue; }
        const s = ctx.st.hrState.get(id) || M.fluxSettleState(h);
        if (s.state !== 'settled') { parts.push({ r: 'waiting', d: `HelmRelease ${id} runs chart ${ver} and is ${s.state} (${s.why})` }); continue; }
        // settled on another chart version: did its chart source move? (OCIRepository or HelmChart)
        const src = ctx.chartSource ? ctx.chartSource(h) : null;
        if (!src) parts.push({ r: 'unreadable', d: `HelmRelease ${id} is settled on chart ${ver}; its chart source could not be read` });
        else if (src.version && L.bareVersion(src.version) === L.bareVersion(pkg.to)) parts.push({ r: 'waiting', d: `HelmRelease ${id} is settled on chart ${ver}; its source ${src.id} has ${src.version}` });
        else if (src.state === 'settled') parts.push({ r: 'fail', class: 'wrong-image', d: `HelmRelease ${id} runs chart ${ver}, and its source ${src.id} is settled on ${src.version ?? 'an unreadable version'}, not ${pkg.to}` });
        else parts.push({ r: 'waiting', d: `HelmRelease ${id} runs chart ${ver}; its source ${src.id} is ${src.state}` });
      }
      continue;
    }
    parts.push({ r: 'unreadable', d: podsUnread ? `pods or CronJobs of the target (to find ${pkg.name})` : `no running container, CronJob template or HelmRelease chart of the target is ${pkg.name}` });
  }
  return fold('image', parts);
}

// Per workload: pods of one template, none terminating, all Ready. Returns the fact and a
// map workload -> its own result (factImage reads whether a workload is still rolling).
export function factRollout(wl, ctx) {
  const parts = [], per = new Map();
  const ws = wl.filter(w => w.kind !== 'CronJob');
  if (!ws.length) return { fact: NA('rollout', 'the target has no Deployment, StatefulSet or DaemonSet'), per };
  for (const w of ws) {
    const key = `${w.kind}/${w.ns}/${w.name}`, f = ctx.pods(w.ns);
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

// Two ticks, no timer. prior: { at, pods: { uid: { name, restarts } } }, recorded on an earlier
// tick when the rollout was complete. rolloutDone: the rollout fact passes on this tick. The
// pods are recorded only when the rollout is complete; a recorded pod that is not Ready now, or
// restarted, fails even while something else rolls.
export function factStable(wl, prior, now, ctx, rolloutDone = true) {
  const ws = wl.filter(w => w.kind !== 'CronJob');
  if (!ws.length) return { fact: NA('stable', 'the target has no Deployment, StatefulSet or DaemonSet'), baseline: null };
  const pods = [];
  for (const w of ws) { const f = ctx.pods(w.ns); if (!f?.pods) return { fact: fact('stable', 'unreadable', `pods in ${w.ns}`), baseline: prior }; pods.push(...current(podsOf(w, f.pods.items))); }
  const snap = { at: iso(now), pods: Object.fromEntries(pods.map(p => [p.metadata.uid, { name: p.metadata.name, restarts: restartsOf(p) }])) };
  if (!prior?.pods || !prior.at) return rolloutDone ? { fact: fact('stable', 'waiting', `the rollout is complete at ${snap.at} (${pods.length} pod(s) recorded); a later tick reads them again`), baseline: snap }
    : { fact: fact('stable', 'waiting', 'read once the rollout is complete'), baseline: null };
  if (Date.parse(prior.at) >= now) return { fact: fact('stable', 'waiting', `recorded at ${prior.at}; a later tick reads the pods again`), baseline: prior };
  const byUid = new Map(pods.map(p => [p.metadata.uid, p]));
  const gone = Object.entries(prior.pods).filter(([uid]) => !byUid.has(uid));
  if (gone.length) return { fact: fact('stable', 'waiting', `pod ${gone[0][1].name}${gone.length > 1 ? ` (+${gone.length - 1})` : ''} recorded at ${prior.at} was replaced; ${rolloutDone ? `the pods are recorded again at ${snap.at}` : 'they are recorded again once the rollout is complete'}`), baseline: rolloutDone ? snap : null, rebaselined: true };
  const grew = Object.entries(prior.pods).filter(([uid, b]) => restartsOf(byUid.get(uid)) > b.restarts);
  if (grew.length) { const [uid, b] = grew[0]; return { fact: fact('stable', 'fail', `pod ${b.name} restarted: ${b.restarts} restarts at ${prior.at}, ${restartsOf(byUid.get(uid))} now${grew.length > 1 ? ` (+${grew.length - 1} more pods)` : ''}`, { class: 'restart-growth' }), baseline: prior }; }
  const down = Object.entries(prior.pods).filter(([uid]) => !podReady(byUid.get(uid)));
  if (down.length) return { fact: fact('stable', 'fail', `pod ${down[0][1].name} was Ready at ${prior.at} and is not Ready now`, { class: 'not-ready' }), baseline: prior };
  if (!rolloutDone) return { fact: fact('stable', 'waiting', `the ${Object.keys(prior.pods).length} pod(s) recorded at ${prior.at} are Ready with no new restarts, and the rollout is not complete now`), baseline: prior };
  return { fact: fact('stable', 'pass', `the same ${Object.keys(prior.pods).length} pod(s) recorded at ${prior.at} are Ready now with no new restarts`), baseline: prior };
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
        : `failed: ${fails.map(f => `${f.fact} (${f.class})`).join(', ')}; cannot tell which change did it: ${other?.complete ? other.changes.map(c => `${short(c.sha)}${c.pr ? ` #${c.pr}` : ''}`).join(', ') : `other changes unreadable (${other?.why ?? 'not read'})`}` };
  }
  if (unread.length) return { verdict: 'cannot-tell', unreadable: unread.map(f => f.fact), why: `cannot tell: ${unread.map(f => `${f.fact}: ${f.detail}`).join(' | ')}` };
  if (waits.length) return { verdict: 'not-yet', waiting_for: waits.map(f => `${f.fact}: ${f.detail}`), why: `not yet: ${waits.map(f => `${f.fact}: ${f.detail}`).join(' | ')}` };
  return { verdict: 'verified', why: 'every fact passed (or does not apply)' };
}

// ── other changes (story 551's helper over one range read) ─────────────────────────────
export function otherFor(cand, range, sc, rules, sourceSha) {
  if (!sc) return { complete: true, applicable: false, why: 'no immediate cluster effect: no target to compare', changes: [] };
  if (!range?.complete) return { complete: false, why: range?.why || 'unreadable: the commits on main since the merge', changes: [] };
  const i = range.commits.findIndex(c => c.sha === cand.merge_commit);
  if (i < 0 && range.base !== cand.merge_commit) return { complete: false, why: `${short(cand.merge_commit)} is older than the commits read this tick (more than the range limit since it)`, changes: [] };
  const sub = { complete: true, base: cand.merge_commit, head: range.head, commits: range.commits.slice(i + 1) };
  const oc = M.otherChanges(sub, sc, rules);
  const srcI = range.commits.findIndex(c => c.sha === sourceSha);
  return { complete: true, why: null, through: range.commits.at(-1)?.sha ?? range.base, changes: oc.changes.map(c => ({ sha: c.sha, pr: c.pr, at: c.at, subject: c.subject, author: c.author,
    parts: uniq(c.matched.map(x => `${x.part} ${x.name}`)).slice(0, 6), fetched_by_flux: srcI >= 0 ? range.commits.findIndex(x => x.sha === c.sha) <= srcI : sourceSha === range.base ? false : null })) };
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
// or after `since`. A merge whose merged event rotated out (no merge commit) is skipped.
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
    const packages = Array.isArray(d.pk) ? d.pk.map(([name, to]) => ({ name, to })) : d.name ? [{ name: d.name, to: d.to ?? null }] : [];
    out.push({ id, pr: ev.carrier?.pr ?? null, name: d.name ?? null, from: d.from ?? null, to: d.to ?? null, basis: d.basis ?? 'package-table', packages, merged_at: ev.at, merge_commit: ev.evidence.merge_commit,
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

// ── the phase ───────────────────────────────────────────────────────────────────
const kList = (run, args) => { const j = parse(tryRun(run, 'kubectl', ['get', ...args, '-o', 'json'])); return Array.isArray(j?.items) ? j : null; };
async function getJson(url) {
  try { const r = await fetch(url, { method: 'GET', signal: AbortSignal.timeout(15000) }); return r.ok ? await r.json() : null; } catch { return null; }
}
// The image build of an own image for a commit: { state: success|failure|running|none, at } | null.
export function buildOf(runs, workflow) {
  if (!Array.isArray(runs)) return null;
  const mine = runs.filter(r => r?.path === workflow || String(r?.path || '').startsWith(`${workflow}@`));
  if (!mine.length) return { state: 'none' };
  const r = mine.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))[0];
  return r.status !== 'completed' ? { state: 'running' } : { state: r.conclusion === 'success' ? 'success' : 'failure', conclusion: r.conclusion, at: r.updated_at || null };
}

// One candidate, with every read it needs handed in (tests pass fakes). Returns its record.
export function judge(cand, ctx) {
  const { rules, cfg, now, range } = ctx;
  const rec = { pr: cand.pr, item: cand.id, name: cand.name, from: cand.from, to: cand.to, merged_at: cand.merged_at, merge_commit: cand.merge_commit, merged_by: cand.merged_by, practice: !cand.own,
    flux_revision: ctx.sourceRev ?? null, facts: [], other_changes: null, baseline: null };
  const files = cand.files;
  if (!files) {
    rec.facts.push(fact('source', 'unreadable', `the files of ${short(cand.merge_commit)} (the merge)`));
    return finish(rec, cand, ctx);
  }
  const ce = L.clusterEffect(files, rules);
  rec.cluster_effect = ce.effect; rec.targets = ce.targets; rec.files = files.slice(0, 20);
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
  rec.other_changes = otherFor(cand, range, sc, rules, M.revisionSha(ctx.sourceRev));
  const clusterWide = L.clusterWide([...files, ...L.componentReach(files, rules).via.map(v => v.path)], rules);
  return judgeTarget(rec, cand, { merge_commit: cand.merge_commit, merged_at: cand.merged_at }, sc, clusterWide, cand.packages, ctx);
}

// The facts of one target. change: the commit whose rollout is judged (the merge, or an own
// image's pin) and when it merged; cand: the history's item (its events name its own merge).
function judgeTarget(rec, cand, change, sc, clusterWide, packages, ctx) {
  const { now } = ctx;
  const m = change.merge_commit;
  const prior = ctx.prior;
  const src = factSource(m, ctx);
  rec.facts.push(src);
  const after = (name, why) => fact(name, 'waiting', why);
  if (src.result !== 'pass') {
    for (const n of ['applied', 'helmreleases', 'pods', 'alerts', 'image', 'rollout', 'stable']) rec.facts.push(after(n, 'read once Flux\'s source contains the merge'));
    return finish(rec, cand, ctx);
  }
  const ap = factApplied(m, sc, ctx);
  rec.facts.push(ap);
  const w = workloadsOf(sc, ctx.ks, ctx.hr);
  rec.workloads = w.workloads.map(x => `${x.kind} ${x.ns}/${x.name}`).slice(0, 20);
  const hrf = factHelmReleases(m, w.helmreleases, ctx.hr, ctx);
  rec.facts.push(hrf);
  const wl = w.workloads;
  if (w.unread.length) rec.facts.push(fact('workloads', 'unreadable', w.unread.join('; ')));
  rec.facts.push(factPods(change.merged_at, wl, ctx));
  rec.facts.push(factAlerts(change.merged_at, sc, clusterWide, ctx));
  if (ap.result !== 'pass' || !['pass', 'not-applicable'].includes(hrf.result)) {
    for (const n of ['image', 'rollout', 'stable']) rec.facts.push(after(n, 'read once the target applied the merge and its HelmReleases are settled'));
    return finish(rec, cand, ctx);
  }
  const ro = factRollout(wl, ctx);
  const im = factImage({ packages }, wl, w.helmreleases, ro.per, ctx);
  rec.facts.push(im, ro.fact);
  const podsOk = rec.facts.find(f => f.fact === 'pods')?.result === 'pass';
  const rolloutDone = ['pass', 'not-applicable'].includes(ro.fact.result);
  // stability is read once the rollout was complete on some tick: on this one, or on an
  // earlier one that recorded the pods (then a recorded pod gone unready fails)
  if (podsOk && !w.unread.length && (rolloutDone ? im.result === 'pass' : !!prior?.pods && im.result !== 'fail')) {
    const s = factStable(wl, prior, now, ctx, rolloutDone && im.result === 'pass');
    rec.facts.push(s.fact);
    rec.baseline = s.baseline;
    if (s.rebaselined) rec.rebaselined = (prior?.rebaselined || 0) + 1;
  } else rec.facts.push(after('stable', 'read once the image, the rollout and the pods pass'));
  return finish(rec, cand, ctx);
}

// An own-image source: verified only through its digest pin (the first later commit on main
// that changes one of the image's pin files), which is itself verified on its own target.
function pinPath(rec, cand, images, ctx) {
  const { range, rules, cfg } = ctx;
  rec.other_changes = otherFor(cand, range, null, rules, null);
  const pinFiles = new Set(images.flatMap(o => o.pins || []));
  if (!range?.complete) { rec.facts.push(fact('pin', 'unreadable', `the commits on main since the merge (${range?.why || 'not read'})`)); rec.pin = { state: 'unreadable' }; return finish(rec, cand, ctx); }
  const i = range.commits.findIndex(c => c.sha === cand.merge_commit);
  if (i < 0 && range.base !== cand.merge_commit) { rec.facts.push(fact('pin', 'unreadable', `${short(cand.merge_commit)} is older than the commits read this tick (more than the range limit since it)`)); rec.pin = { state: 'unreadable' }; return finish(rec, cand, ctx); }
  const later = range.commits.slice(i + 1);
  const pin = later.find(c => c.files.some(f => pinFiles.has(f)));
  if (!pin) {
    const b = ctx.builds ? ctx.builds(cand.merge_commit) : null;
    const wf = images[0].build;
    const st = b ? buildOf(b, wf) : null;
    rec.pin = { state: 'awaiting', images: images.map(o => o.id), build: st };
    if (!st) rec.facts.push(fact('pin', 'unreadable', `the run of ${wf} for ${short(cand.merge_commit)}`));
    else if (st.state === 'none') rec.facts.push(fact('pin', 'unreadable', `no run of ${wf} for ${short(cand.merge_commit)}`));
    else if (st.state === 'running') rec.facts.push(fact('pin', 'waiting', `the image build ${wf} for ${short(cand.merge_commit)} is running`));
    else if (st.state === 'failure') rec.facts.push(fact('pin', 'fail', `the image build ${wf} for ${short(cand.merge_commit)} ended ${st.conclusion}: the image was not published`, { class: 'not-applied' }));
    else {
      const pushes = later.filter(c => Date.parse(c.at || '') > Date.parse(st.at || '')).length;
      rec.pin.pushes_since_build = pushes;
      rec.facts.push(pushes >= cfg.pin_wait_pushes
        ? fact('pin', 'fail', `main had ${pushes} push(es) after the image build finished at ${st.at}, and none changed a pin of ${images.map(o => o.id).join(', ')} (${[...pinFiles].join(', ')}): the digest pin did not come`, { class: 'pin-timeout' })
        : fact('pin', 'waiting', `awaiting the digest pin of ${images.map(o => o.id).join(', ')}: the build finished at ${st.at}; ${pushes} of ${cfg.pin_wait_pushes} pushes to main since then (Renovate's runs follow pushes)`));
    }
    return finish(rec, cand, ctx);
  }
  // the pin merged: its item in the history names the digest; verify it on its own target
  const pinItem = ctx.itemByPr ? ctx.itemByPr(pin.pr) : null;
  rec.pin = { state: 'merged', commit: pin.sha, pr: pin.pr, at: pin.at, item: pinItem?.id ?? null };
  if (!pinItem) { rec.facts.push(fact('pin', 'unreadable', `the pin ${short(pin.sha)}${pin.pr ? ` (#${pin.pr})` : ''} is not an update the history holds, so its digest is unknown`)); return finish(rec, cand, ctx); }
  rec.facts.push(fact('pin', 'pass', `pinned by #${pin.pr} (${short(pin.sha)} at ${pin.at})`));
  const ce = L.clusterEffect(pin.files, rules);
  if (!ctx.ks || !ctx.hr) { rec.facts.push(fact('applied', 'unreadable', 'kustomizations or helmreleases')); return finish(rec, cand, ctx); }
  const sc = M.settleScope({ cluster_effect: 'immediate', targets: ce.targets }, ctx.ks, ctx.hr);
  rec.scope = { own: sc.own, chain: sc.chain.filter(x => !sc.own.includes(x)), namespaces: sc.namespaces, via_pin: pin.pr };
  rec.other_changes = otherFor({ ...cand, merge_commit: pin.sha }, range, sc, rules, M.revisionSha(ctx.sourceRev));
  const clusterWide = L.clusterWide(pin.files, rules);
  return judgeTarget(rec, cand, { merge_commit: pin.sha, merged_at: pin.at || cand.merged_at }, sc, clusterWide, pinItem.packages, ctx);
}

function finish(rec, cand, ctx) {
  const v = verdictOf(rec.facts, rec.other_changes);
  Object.assign(rec, v);
  rec.verdict_at = iso(ctx.now);
  // final: a practice item is not judged again (its record stays); the lane's own merge is
  // judged on every tick until the history moves it out of merged_unverified or awaiting_pin
  rec.final = ['verified', 'failed'].includes(v.verdict);
  const evs = eventsFor(cand, rec, iso(ctx.now), ctx.repo);
  rec.would_write = checkEvents(cand.item, evs);
  rec.events = evs.filter((_, i) => rec.would_write[i].legal);
  return rec;
}

// THE PHASE, once per tick: read the history's candidates, the facts they need, judge each.
// deps: { alertSubject (run-night.mjs), nsFacts(ns) (the tick's cache), ks, hr, source (when the
// tick already read them), prom (Prometheus base URL), getJson (tests), doc (a history document:
// the CLI) }. Returns { summary, memory (the next data.verify), table, events (the lane's own
// merges' events, only when night.verify.write_history_events is true) }.
export async function verifyPhase({ rules, run, repo, now, startedAt = now, clock = () => Date.now(), prevText = null, prevUnreadable = false, deps = {}, onlyPrs = null }) {
  const t0 = clock();
  const cfg = verifyConfig(rules);
  const sum = { built: true, practice: cfg.practice ?? null, write_history_events: cfg.write_history_events ?? null, since: null, since_basis: null, history: null, candidates: 0, evaluated: [], deferred: [], kept_final: 0,
    verdicts: { verified: [], failed: [], 'not-yet': [], 'cannot-tell': [] }, events_for_history: 0, practice_events_not_written: 0, reads: null, elapsed_ms: null, why_not: null };
  const out = (extra = {}) => ({ summary: { ...sum, ...extra, elapsed_ms: clock() - t0 }, memory: null, table: null, events: [] });
  if (!cfg.ok) return out({ why_not: cfg.why });
  let reads = 0;
  const counted = (cmd, argv, o) => { reads++; return run(cmd, argv, o); };
  const pm = prevUnreadable ? { ok: false, why: 'upgrade-lane-state could not be read this tick', mem: parseMemory(null).mem } : parseMemory(prevText);
  const mem = structuredClone(pm.mem);
  if (!pm.ok) sum.memory = `${pm.why}; starting a new memory`;
  // since: kept, else read from git (a fact), else no practice this tick
  if (!onlyPrs && (!mem.since || !Number.isFinite(Date.parse(mem.since)))) { const i = readInstalledAt(repo, counted); if (i) { mem.since = i.since; mem.since_basis = i.basis; } }
  sum.since = onlyPrs ? 'not used (named pull requests)' : mem.since; sum.since_basis = mem.since_basis;
  // the history
  let doc = deps.doc ?? null;
  if (!doc) {
    const r = M.readNamedConfigMap(H.HISTORY.name, counted);
    if (!r) return out({ history: 'unreadable: ConfigMap upgrade-agent/upgrade-ledger', why_not: 'the history could not be read; nothing verified this tick', reads: { commands: reads } });
    const p = H.parseDoc(r.found ? r.cm : null);
    if (!p.ok) return out({ history: p.why, why_not: 'the history could not be read; nothing verified this tick', reads: { commands: reads } });
    sum.history = r.found ? `read at resourceVersion ${p.rv}` : 'absent (no history yet)';
    doc = p.doc;
  } else sum.history = 'handed in';
  if (!onlyPrs && cfg.practice && !mem.since) sum.why_not = 'unreadable: when the verifier was installed (the first commit of night-verify.mjs on main); no practice verdict this tick';
  const cands = candidatesOf(doc, { since: mem.since, practice: cfg.practice && !!mem.since, onlyPrs });
  const all = cands.list.filter(c => onlyPrs || c.own || !mem.done?.[c.id]);
  sum.candidates = all.length;
  if (cands.skipped.length) sum.skipped = cands.skipped.slice(0, 10);
  // the fair order: the least recently judged first, then the oldest merge
  const lastAt = c => mem.items?.[c.id]?.verdict_at || '';
  const order = [...all].sort((a, b) => lastAt(a).localeCompare(lastAt(b)) || Date.parse(a.merged_at) - Date.parse(b.merged_at));
  const pick = order.slice(0, cfg.max_items_per_tick);
  sum.deferred = order.slice(cfg.max_items_per_tick).map(c => c.pr);
  const result = { ...out(), memory: null };
  if (pick.length) {
    // the reads every candidate shares: Flux, the commits on main since the oldest merge
    const ks = deps.ks ?? kList(counted, ['kustomizations.kustomize.toolkit.fluxcd.io', '-A']);
    const hr = deps.hr ?? kList(counted, ['helmreleases.helm.toolkit.fluxcd.io', '-A']);
    const scfg = M.settleConfig(rules);
    let source = deps.source;
    if (source === undefined) { const sl = kList(counted, ['gitrepositories.source.toolkit.fluxcd.io', '-n', scfg.source.namespace, '--field-selector', `metadata.name=${scfg.source.name}`]); source = sl ? (sl.items.find(g => g?.metadata?.name === scfg.source.name) || null) : undefined; }
    const sourceRev = source?.status?.artifact?.revision ?? null;
    const st = M.fluxStates({ cfg: scfg, ks, hr, source });
    // one range read from the oldest merge that fits the limit; older merges fall back to reads of their own
    let range = null;
    for (const c of [...pick].sort((a, b) => Date.parse(a.merged_at) - Date.parse(b.merged_at))) {
      range = M.readCommitRange(repo, c.merge_commit, 'main', counted, cfg.max_range_commits);
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
      if (!memo.has(k)) { const c = parse(tryRun(counted, 'gh', ['api', `repos/${repo}/compare/${m}...${x}?per_page=1`])); memo.set(k, !c?.status ? null : ['identical', 'ahead'].includes(c.status) ? true : ['behind', 'diverged'].includes(c.status) ? false : null); }
      return memo.get(k);
    };
    const nsCache = new Map();
    const pods = deps.nsFacts || (ns => { if (!nsCache.has(ns)) nsCache.set(ns, { pods: kList(counted, ['pods', '-n', ns]), cronjobs: kList(counted, ['cronjobs', '-n', ns]), jobs: kList(counted, ['jobs', '-n', ns]) }); return nsCache.get(ns); });
    const prom = deps.prom || 'http://kube-prometheus-stack-prometheus.observability.svc.cluster.local:9090';
    const aj = await (deps.getJson || getJson)(`${prom}/api/v1/alerts`);
    const alerts = Array.isArray(aj?.data?.alerts) ? aj.data.alerts : null;
    const chartSource = h => {
      const ref = h.spec?.chartRef;
      const kind = ref?.kind === 'OCIRepository' ? 'ocirepositories.source.toolkit.fluxcd.io' : !ref && h.spec?.chart ? 'helmcharts.source.toolkit.fluxcd.io' : null;
      if (!kind) return null;
      const ns = ref?.namespace || h.metadata.namespace;
      const name = ref ? ref.name : `${h.metadata.namespace}-${h.metadata.name}`;
      const l = kList(counted, [kind, '-n', ns, '--field-selector', `metadata.name=${name}`]);
      const o = l?.items?.find(x => x.metadata?.name === name);
      if (!o) return null;
      return { id: `${kind.split('.')[0]} ${ns}/${name}`, version: String(o.status?.artifact?.revision || '').split('@')[0] || null, state: M.fluxSettleState(o).state };
    };
    const builds = sha => { const j = parse(tryRun(counted, 'gh', ['api', `repos/${repo}/actions/runs?head_sha=${sha}&per_page=50`])); return Array.isArray(j?.workflow_runs) ? j.workflow_runs : null; };
    const docItems = H.replay(doc);
    const itemByPr = pr => { const ids = H.prIndex(docItems).get(pr) || []; const id = ids.find(x => ['merged_unverified', 'awaiting_pin', 'done', 'failed'].includes(docItems.items.get(x)?.item.state)); if (!id) return null; const d = doc.items[id] || {}; return { id, packages: Array.isArray(d.pk) ? d.pk.map(([name, to]) => ({ name, to })) : d.name ? [{ name: d.name, to: d.to ?? null }] : [] }; };
    const filesOf = c => { const x = range?.complete ? range.commits.find(y => y.sha === c.merge_commit) : null; if (x) return x.files; const j = parse(tryRun(counted, 'gh', ['api', `repos/${repo}/commits/${c.merge_commit}`])); return Array.isArray(j?.files) && j.files.length < 300 ? uniq(j.files.flatMap(f => [f.filename, f.previous_filename].filter(Boolean))) : null; };
    for (const c of pick) {
      if (clock() - startedAt > cfg.time_budget_seconds * 1e3) { sum.deferred.push(c.pr); continue; }
      const prev = mem.items?.[c.id] || null;
      const rec = judge({ ...c, files: filesOf(c) }, { rules, cfg, now, repo, range, ks, hr, st, sourceRev, contains, pods, alerts, alertSubject: deps.alertSubject, alertScope: rules.alert_scope || {}, chartSource, builds, itemByPr, prior: prev?.baseline ?? null });
      rec.first_at = prev?.first_at ?? rec.verdict_at;
      rec.ticks = (prev?.ticks || 0) + 1;
      if (prev?.rebaselined && !rec.rebaselined) rec.rebaselined = prev.rebaselined;
      mem.items[c.id] = rec;
      sum.evaluated.push(c.pr);
    }
    sum.range =range ? (range.complete ? { base: short(range.base), commits: range.commits.length } : { why: range.why }) : null;
  }
  // the verdicts, the events, the memory kept
  const ev = [];
  for (const c of pick) {
    const r = mem.items[c.id];
    if (!r || r.verdict_at !== iso(now)) continue;
    sum.verdicts[r.verdict]?.push(c.pr);
    if (c.own && cfg.write_history_events) ev.push(...(r.events || []));
    else if (!c.own) sum.practice_events_not_written += (r.events || []).length;
  }
  for (const r of Object.values(mem.items)) delete r.events; // never kept: rebuilt from the facts
  const live = new Set(cands.list.map(c => c.id));
  for (const [id, r] of Object.entries(mem.items)) if (r.final) mem.done[id] = `${r.verdict} ${r.verdict_at}`;
  // done is only there to skip a candidate: an id that is no longer one is forgotten; a record
  // that is neither a candidate nor final is too
  for (const id of Object.keys(mem.done)) if (!live.has(id)) delete mem.done[id];
  for (const id of Object.keys(mem.items)) if (!live.has(id) && !mem.items[id].final) delete mem.items[id];
  mem.at = iso(now);
  const mergedAt = new Map(cands.list.map(c => [c.id, c.merged_at]));
  const fit = fitMemory(mem, cfg, id => mem.items[id] ? !mem.items[id].final && live.has(id) : false, id => mergedAt.get(id) ?? mem.items[id]?.merged_at ?? '');
  Object.assign(sum, fit.counts);
  sum.kept_final = Object.keys(mem.done).length;
  const text = fit.text;
  sum.events_for_history = ev.length;
  sum.reads = { commands: reads };
  result.summary = { ...sum, elapsed_ms: clock() - t0 };
  result.memory = text;
  const table = tableOf(mem);
  result.table = Buffer.byteLength(table) <= cfg.max_state_bytes ? table : `${Buffer.from(table).subarray(0, cfg.max_state_bytes - 80).toString('utf8').replace(/\n[^\n]*$/, '')}\n… (cut at ${cfg.max_state_bytes} bytes; data.verify has every record)`;
  result.events = ev;
  result.records = mem.items;
  return result;
}

// The memory within its bounds (review of PR 4880). An open record (still a candidate, not
// final) holds the pods recorded for stability, so it goes last. In order:
//   1. final records beyond max_items_kept, oldest merge first (display only: done keeps them
//      from being judged again);
//   2. over max_state_bytes: final records, oldest first; then open records lose their
//      details (facts, other changes, the events they would write), keeping the verdict and
//      the recorded pods; then the oldest done entries (such an item may be judged again);
//      last, the oldest open records, counted in trimmed_open.
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
    mem.items[id] = { pr: r.pr, item: r.item, name: r.name, from: r.from, to: r.to, merged_at: r.merged_at, merged_by: r.merged_by, practice: r.practice, verdict: r.verdict, why: String(r.why || '').slice(0, 300),
      verdict_at: r.verdict_at, first_at: r.first_at, ticks: r.ticks, final: r.final, baseline: r.baseline ?? null, ...(r.rebaselined ? { rebaselined: r.rebaselined } : {}), compacted: true };
    counts.compacted++; text = JSON.stringify(mem);
  }
  for (const id of byAge(Object.keys(mem.done))) { if (!over()) break; delete mem.done[id]; counts.done_trimmed++; text = JSON.stringify(mem); }
  for (const id of opens()) { if (!over()) break; delete mem.items[id]; counts.trimmed_open++; text = JSON.stringify(mem); }
  return { text, counts: Object.fromEntries(Object.entries(counts).filter(([, v]) => v)) };
}

// The morning view: one block per update, newest merge first.
export function tableOf(mem) {
  const lines = [];
  for (const r of Object.values(mem.items || {}).sort((a, b) => String(b.merged_at).localeCompare(String(a.merged_at)))) {
    lines.push(`#${r.pr} ${r.name ?? '?'} ${r.from ?? '?'} -> ${r.to ?? '?'}, merged ${r.merged_at} by ${r.merged_by?.login ?? r.merged_by?.kind ?? '?'}${r.practice ? ' (practice)' : ''}: ${String(r.verdict).toUpperCase()}${r.attribution ? ` (${r.attribution})` : ''} at ${r.verdict_at}`);
    for (const f of r.facts || []) lines.push(`  ${f.fact}: ${f.result}${f.class ? ` [${f.class}]` : ''}${f.detail ? `: ${f.detail}` : ''}`);
    const oc = r.other_changes;
    lines.push(`  other changes: ${!oc ? 'not read' : oc.applicable === false ? 'not applicable (no target)' : !oc.complete ? `unreadable (${oc.why})` : oc.changes.length ? oc.changes.map(c => `${short(c.sha)}${c.pr ? ` #${c.pr}` : ''} ${c.subject} [${c.parts.join('; ')}]${c.fetched_by_flux === false ? ' (not fetched by Flux yet)' : ''}`).join(' | ') : 'none on the target, its chain or a cluster-wide part'}`);
    if ((r.would_write || []).length) lines.push(`  ${r.practice ? 'would write (not written: practice)' : 'events'}: ${r.would_write.map(e => `${e.type} ${e.from}->${e.to}${e.class ? ` [${e.class}]` : ''}${e.legal ? '' : ` REFUSED (${e.why})`}`).join(', ')}`);
  }
  return lines.join('\n');
}
// The tick's log line.
export function verifyLine(s) {
  if (!s) return 'not run';
  if (s.error) return `FAILED: ${s.error}`;
  const v = Object.entries(s.verdicts || {}).filter(([, l]) => l.length).map(([k, l]) => `${k} ${l.map(p => `#${p}`).join(', ')}`).join('; ');
  return `${s.evaluated?.length || 0} judged${v ? ` (${v})` : ''}${s.deferred?.length ? `, deferred ${s.deferred.map(p => `#${p}`).join(', ')}` : ''}; since ${s.since ?? 'unreadable'}; history ${s.history ?? 'not read'}${s.why_not ? `; ${s.why_not}` : ''}; ${s.events_for_history} event(s) for the history, ${s.practice_events_not_written} practice event(s) not written; ${s.reads?.commands ?? 0} reads; ${s.elapsed_ms ?? '?'} ms`;
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
// /api/v1/alerts (a session pod cannot reach Prometheus; read them through Grafana).
// Nothing in the cluster or on GitHub is written.
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
  const fromFile = o.alerts ? async () => parse(readFileSync(o.alerts, 'utf8')) : undefined;
  const r = await verifyPhase({ rules, run, repo, now, prevText, deps: { doc, alertSubject: N.alertSubject, prom: process.env.PROMETHEUS_URL, getJson: fromFile }, onlyPrs: prs });
  if (o.state && r.memory) writeFileSync(o.state, r.memory);
  if (o.json) process.stdout.write(JSON.stringify({ summary: r.summary, records: r.records }, null, 2) + '\n');
  else process.stdout.write(`${r.table || '(no verdict)'}\n\nverifier: ${verifyLine(r.summary)}\n`);
  return 0;
}
if (L.isMain(import.meta.url)) main(process.argv.slice(2)).then(c => process.exit(c)).catch(e => { console.error(`night-verify: FAILED: ${e.message}`); process.exit(1); });
