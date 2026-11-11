#!/usr/bin/env node
// night-merge.mjs — the night lane's merge step and the change lock (story 752).
//
// Story: https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/752
// Runbook: .agents/runbooks/upgrade-night-lane.md, "Live mode, the merge step and the
// change lock". Node 24 only, no dependencies: the upgrade-shepherd image is
// node:24-slim and has no Python, so the preflight of
// scripts/upgrade-agent/deliver_reviewed_pr.py and the handoff's preflight.py is ported
// here rather than run.
//
// THE MERGE STEP. One pull request, one head SHA. It reads the PR afresh and refuses
// unless: it is open, not a draft, based on main, opened by the Renovate app (app/renovate,
// is_bot) from this repository on a renovate/ branch; every commit is the Renovate app's
// (REST: author renovate[bot], committed by web-flow, signed); the head is the chosen
// head; GitHub reads it MERGEABLE; no check on the
// head is running or failing and no commit status is anything but SUCCESS (a PENDING or
// EXPECTED status is refused: the handoff preflight let it through); the repository's
// required checks and every check the check map expects concluded SUCCESS; main has not
// changed one of its files after its checks ran (story 550's R7, the same functions);
// and the head contains the newest main commit that touches each of its files (a head
// behind main by such a commit is refused: "require branches to be up to date" is off
// for main, so GitHub would merge it). Just before the call, live mode re-checks the
// lease, the merge window, the age of the tick's gates (300s) and the pause, then moves the
// lock to phase "merging" with the post-merge lease. The merge is `gh pr merge <n>
// --squash --match-head-commit <head>`: never --admin, --auto or a force flag. A call with
// no exit status is merge-unknown and keeps the hold.
//
// THE CHANGE LOCK. ConfigMap upgrade-agent/upgrade-change-lock. data.holder empty or the
// object absent = free; a holder whose data.expires_at has passed = expired (a crashed
// tick loses it by its lease); anything else = held. A take is atomic: create when
// absent, else replace at the resourceVersion that was read, so a concurrent take fails
// with AlreadyExists or Conflict. An unreadable lock, or a holder with no readable
// expiry, fails closed. The lane takes the lock before the merge step's reads and holds
// it through the merge; it releases it on every other exit path (finally, and an exit
// and SIGTERM handler for a process that ends while holding it).
//
// WHAT NEVER HAPPENS IN DRY-RUN. callMerge() throws unless its mode is 'live' (the lane)
// or 'hand' (the CLI with --merge), and laneMergeStep() calls it only when mode is
// 'live'. The two spawn sites in this file are writeLock() (create or replace of the
// lock ConfigMap, nothing else) and callMerge(). Every read goes through the classifier's
// read-only runner. scripts/upgrade-agent/lane-merge-step-selftest.mjs and
// lane-night-dryrun-selftest.mjs check all of this.
//
// HAND RUN (the rehearsal): node night-merge.mjs --pr <n> --head <sha> --check-only
// prints the verdict and changes nothing; --merge merges once with the caller's own gh
// identity when the verdict is clean, and needs the owner's yes. It runs the classifier
// (the PR must be LANE-A) and the same preflight, reads the lock and the pause and
// refuses when either is set, but cannot take the lock (a session cannot write ConfigMaps in
// upgrade-agent). It runs the settle gate (story 551) at its start and again before a
// --merge call. It skips the merge window, the class allowlist and the tick's other
// gates. --merge refuses UPGRADE_NIGHT_NOW.
//
// THE SETTLE GATE (story 551). settleVerdict(): no merge within 30 minutes of main's newest
// commit to kubernetes/**, before Flux fetched and attempted it, or while any
// Kustomization or HelmRelease is progressing. Read at the tick's start, in the merge step,
// just before a live merge call, and by the hand run.
import { spawnSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as L from './lane-classify.mjs';

export const REPO = 'thaynes43/haynes-ops';
// The repository profile of deliver_reviewed_pr.py: literals, so neither the rules file
// nor the environment can drop a required check.
export const REQUIRED_CHECKS = Object.freeze(['Flux Local - Success', 'Diff Scope - Success']);
// Advisory, never a gate (CLAUDE.md, "CI/CD"); it skips Renovate PRs anyway.
export const ADVISORY_CHECKS = Object.freeze(['Claude Review (advisory)']);
export const BYPASS_FLAGS = Object.freeze(['--admin', '--auto', '--force', '--auto-merge']);
// The classes classify() gives a lane-A PR (lane-classify.mjs, `aClass`).
export const LANE_A_CLASSES = Object.freeze(['no-cluster-effect', 'delayed-cluster-change', 'cluster-change', 'immediate-cluster-change']);
export const LOCK = Object.freeze({ name: 'upgrade-change-lock', namespace: 'upgrade-agent' });
export const LOCK_SCHEMA = 'haynes.upgrade-change-lock/v1';
// The most a lane token may carry: what a squash merge needs plus the check reads.
const TOKEN_MAX = Object.freeze({ contents: 'write', pull_requests: 'write', checks: 'read', metadata: 'read' });
const LEVEL = { read: 1, write: 2, admin: 3 };
// The Renovate app as GitHub reports it (read live on 2026-11-11 for every open Renovate
// PR): gh gives the PR author as login app/renovate with is_bot true; the REST commit
// list gives each commit's author as renovate[bot], type Bot, id 29139614, committed by
// web-flow with a valid signature. Anything else is refused.
export const RENOVATE = Object.freeze({ pr_author: 'app/renovate', commit_login: 'renovate[bot]', commit_id: 29139614, committer: 'web-flow', branch_prefix: 'renovate/' });
const SHA = /^[0-9a-f]{40}$/;
const VIEW_FIELDS = 'number,state,isDraft,headRefOid,headRefName,baseRefName,isCrossRepository,author,mergeable,mergeStateStatus,files,changedFiles,commits,statusCheckRollup';
const FAIL = new Set(['FAILURE', 'TIMED_OUT', 'CANCELLED', 'ACTION_REQUIRED', 'STARTUP_FAILURE', 'STALE', 'ERROR']);
const MERGE_STATES_OK = new Set(['CLEAN', 'HAS_HOOKS', 'UNSTABLE']); // UNSTABLE: a non-required check; the scan below decides
const GH_TIMEOUT_MS = 120e3;
const KUBECTL_TIMEOUT_MS = 30e3;
const SECRET_MARKERS = ['token', 'bearer', 'authorization', 'password', 'secret'];
const iso = ms => new Date(ms).toISOString();
const short = s => String(s || '').slice(0, 8);

// The last non-empty line of a command's stderr, unless it may carry a credential.
export function publicDetail(text) {
  const line = String(text || '').split('\n').map(s => s.trim()).filter(Boolean).pop() || '';
  if (SECRET_MARKERS.some(m => line.toLowerCase().includes(m))) return 'command failed';
  return line.slice(0, 180) || 'command failed';
}

// ── the switch: UPGRADE_NIGHT_LIVE_CLASSES ────────────────────────────────────────
export function parseLiveClasses(text) {
  const list = [...new Set(String(text ?? '').split(/[\s,]+/).filter(Boolean))];
  return { classes: list.filter(c => LANE_A_CLASSES.includes(c)), unknown: list.filter(c => !LANE_A_CLASSES.includes(c)) };
}

// ── the token: the permission JSON the mint-token init container sent to GitHub ─────
// GitHub mints exactly the permissions requested or refuses the mint, so this file names
// the token's permissions. Unreadable = cannot merge (fail closed). Only names and levels
// are ever logged, never the token.
export function tokenPermissions(text) {
  let p = null;
  try { p = JSON.parse(String(text ?? '')); } catch { p = null; }
  if (!p || typeof p !== 'object' || Array.isArray(p) || !Object.keys(p).length || !Object.values(p).every(v => LEVEL[v])) {
    return { readable: false, names: [], can_merge: false, why: 'unreadable: the token permissions the mint-token init container recorded (/creds/gh_token_permissions)' };
  }
  const names = Object.keys(p).sort().map(k => `${k}:${p[k]}`);
  const wider = Object.keys(p).sort().filter(k => !TOKEN_MAX[k] || LEVEL[p[k]] > LEVEL[TOKEN_MAX[k]]).map(k => `${k}:${p[k]}`);
  const writes = LEVEL[p.contents] >= LEVEL.write && LEVEL[p.pull_requests] >= LEVEL.write;
  const why = !writes ? `the token cannot merge: minted with ${names.join(', ')} (a merge needs contents:write and pull_requests:write)`
    : wider.length ? `the token is wider than the lane needs: ${wider.join(', ')} (at most contents:write, pull_requests:write, checks:read)` : null;
  return { readable: true, names, can_merge: !why, why };
}

// ── reads (all through the classifier's read-only runner) ──────────────────────────
const parse = out => { try { return JSON.parse(out); } catch { return null; } };
const tryRun = (run, cmd, argv) => { try { return run(cmd, argv, { allowFail: true }); } catch { return null; } };

// One ConfigMap of upgrade-agent by name. { found, cm } or null when the read failed.
// A field-selector list tells "absent" (an empty list) from "unreadable" (a failure).
export function readNamedConfigMap(name, run) {
  const j = parse(tryRun(run, 'kubectl', ['get', 'configmaps', '-n', LOCK.namespace, '--field-selector', `metadata.name=${name}`, '-o', 'json']));
  if (!Array.isArray(j?.items)) return null;
  const cm = j.items.find(c => c?.metadata?.name === name) || null;
  return { found: !!cm, cm };
}

export function readMergeView(repo, pr, run) {
  const v = parse(tryRun(run, 'gh', ['pr', 'view', String(pr), '-R', repo, '--json', VIEW_FIELDS]));
  return v && typeof v === 'object' && !Array.isArray(v) ? v : null;
}

// The newest commit on main that touches each path (gh api GET, one per path). null for
// a path main has no commit for (a file the PR adds).
export function newestMainCommits(repo, paths, run) {
  const byPath = {}, unreadable = [];
  for (const p of paths) {
    const list = parse(tryRun(run, 'gh', ['api', `repos/${repo}/commits?sha=main&path=${encodeURIComponent(p)}&per_page=1`]));
    if (!Array.isArray(list)) { unreadable.push(p); continue; }
    byPath[p] = list.length && SHA.test(list[0]?.sha || '') ? { sha: list[0].sha, at: list[0].commit?.committer?.date || null } : null;
    if (list.length && !byPath[p]) unreadable.push(p);
  }
  return { byPath, unreadable };
}

// Whether the head contains each commit: compare <head>...<sha> lists the commits of
// <sha> missing from the head (ahead_by). 0 = an ancestor of the head (or the head).
export function headContains(repo, head, shas, run) {
  const contains = {}, unreadable = [];
  for (const s of shas) {
    const j = parse(tryRun(run, 'gh', ['api', `repos/${repo}/compare/${head}...${s}?per_page=1`]));
    if (!j || !Number.isInteger(j.ahead_by)) { unreadable.push(s); continue; }
    contains[s] = j.ahead_by === 0;
  }
  return { contains, unreadable };
}

// PURE: the paths whose newest main commit the head lacks.
export function behindMain(paths, newest, contains) {
  const behind = [], unreadable = [...(newest?.unreadable || [])];
  for (const p of paths) {
    if (unreadable.includes(p)) continue;
    const c = newest?.byPath?.[p];
    if (c === undefined) { unreadable.push(p); continue; }
    if (c === null) continue; // main has no commit to this path
    if (contains?.unreadable?.includes(c.sha) || contains?.contains?.[c.sha] === undefined) { unreadable.push(p); continue; }
    if (!contains.contains[c.sha]) behind.push({ path: p, sha: c.sha, at: c.at });
  }
  return { behind, unreadable };
}

// ── the PR preflight (PURE over one gh pr view) ────────────────────────────────────
const newest = list => list.reduce((a, b) => (String(b.completed_at || b.started_at || '') > String(a.completed_at || a.started_at || '') ? b : a));
export function prRefusals(view, expectHead, rules, now) {
  const refusals = [];
  const add = s => { if (!refusals.includes(s)) refusals.push(s); };
  const head = view?.headRefOid;
  if (view?.state !== 'OPEN') add(`not open (state ${view?.state ?? 'unknown'})`);
  if (view?.isDraft !== false) add(view?.isDraft ? 'a draft' : 'unreadable: whether the PR is a draft');
  if (view?.baseRefName !== 'main') add(`base is ${view?.baseRefName ?? 'unknown'}, not main`);
  if (view?.author?.login !== RENOVATE.pr_author || view?.author?.is_bot !== true || !rules.renovate_pr_authors.includes(view?.author?.login)) add(`opened by ${view?.author?.login ?? 'unknown'} (is_bot ${view?.author?.is_bot ?? 'unknown'}), not the Renovate app`);
  if (view?.isCrossRepository !== false) add(view?.isCrossRepository ? 'a pull request from another repository' : 'unreadable: whether the PR comes from another repository');
  if (!String(view?.headRefName || '').startsWith(RENOVATE.branch_prefix)) add(`head branch ${view?.headRefName ?? 'unknown'} is not under ${RENOVATE.branch_prefix}`);
  if (!SHA.test(expectHead || '')) add('the chosen head is not a 40-character SHA');
  if (!SHA.test(head || '')) add('unreadable: the PR head SHA');
  else if (SHA.test(expectHead || '') && head !== expectHead) add(`head moved: ${short(head)} is not the chosen head ${short(expectHead)}`);
  const commits = Array.isArray(view?.commits) ? view.commits : null;
  if (!commits || !commits.length) add('unreadable: the PR commits');
  for (const c of commits || []) {
    const who = (c.authors || []).map(a => a?.login || null);
    if (!who.length || who.some(a => !rules.renovate_commit_authors.includes(a))) add(`commit ${short(c.oid)} is not Renovate's alone (${who.map(a => a ?? 'unlinked author').join(', ') || 'no author'})`);
  }
  const headCommit = (commits || []).find(c => c.oid === head) || null;
  if (commits && commits.length && !headCommit) add('the head commit is not among the PR commits');
  if (view?.mergeable !== 'MERGEABLE') add(`GitHub reads it ${view?.mergeable ?? 'unknown'}, not MERGEABLE`);
  if (!MERGE_STATES_OK.has(view?.mergeStateStatus)) add(`merge state ${view?.mergeStateStatus ?? 'unknown'}`);
  const files = Array.isArray(view?.files) ? view.files.map(f => f?.path).filter(Boolean) : [];
  const truncated = !Array.isArray(view?.files) || files.length !== view?.changedFiles || !files.length;
  if (truncated) add(`unreadable: the changed files (${files.length} listed of ${view?.changedFiles ?? 'unknown'})`);

  // every check and commit status on the head
  const rollup = Array.isArray(view?.statusCheckRollup) ? view.statusCheckRollup : null;
  if (!rollup) add('unreadable: the checks on the head');
  else if (rollup.length >= 100) add(`the check list may be truncated (${rollup.length} entries)`);
  const runs = [];
  const groups = new Map();
  for (const c of rollup || []) {
    if (c?.__typename === 'StatusContext') {
      const st = String(c.state || '').toUpperCase();
      if (st === 'SUCCESS') continue;
      if (st === 'PENDING' || st === 'EXPECTED') add(`commit status ${c.context} is pending (${st})`);
      else add(`commit status ${c.context} is ${st || 'unknown'}`);
      continue;
    }
    if (c?.__typename !== 'CheckRun') { add(`unreadable: a check entry of type ${c?.__typename ?? 'unknown'}`); continue; }
    const r = { workflow: c.workflowName, name: c.name, status: c.status, conclusion: c.conclusion, completed_at: c.completedAt, started_at: c.startedAt };
    runs.push(r);
    if (ADVISORY_CHECKS.includes(r.name)) continue;
    const k = `${r.workflow}\u0000${r.name}`;
    groups.set(k, [...(groups.get(k) || []), r]);
  }
  for (const list of groups.values()) {
    const r = newest(list);
    const label = `${r.workflow} / ${r.name}`;
    if (r.status !== 'COMPLETED') add(`check still running: ${label} (${r.status || 'no status'})`);
    else if (FAIL.has(r.conclusion)) add(`check failed: ${label} (${r.conclusion})`);
    else if (!['SUCCESS', 'SKIPPED', 'NEUTRAL'].includes(r.conclusion)) add(`check ${label} has no known conclusion (${r.conclusion || 'none'})`);
  }
  for (const name of REQUIRED_CHECKS) {
    const mine = runs.filter(r => r.name === name);
    const r = mine.length ? newest(mine) : null;
    if (!r || r.status !== 'COMPLETED' || r.conclusion !== 'SUCCESS') add(`required check not SUCCESS: ${name} (${r ? r.conclusion || r.status : 'absent'})`);
  }
  // the check map: every expected check SUCCESS and younger than check_max_age_days
  const cm = L.expectedChecks(files, rules);
  const unval = cm.unvalidated.filter(p => !p.startsWith('kubernetes/'));
  if (unval.length) add(`no PR validation for ${unval.join(', ')}`);
  const ev = L.evaluateChecks(cm.expected, runs, now, rules.check_max_age_days);
  if (ev.failing.length) add(`expected check failing: ${ev.failing.join('; ')}`);
  if (ev.pending.length) add(`expected check still running: ${ev.pending.join('; ')}`);
  if (ev.missing.length) add(`expected check not SUCCESS: ${ev.missing.join('; ')}`);
  if (ev.stale.length) add(`expected check older than ${rules.check_max_age_days}d: ${ev.stale.join('; ')}`);
  return { refusals, head, files, truncated, head_committed_at: headCommit?.committedDate || null, ev };
}

// The REST commit list carries what gh pr view does not: each commit's author and
// committer as GitHub accounts (type and id) and its signature.
export function readPrCommits(repo, pr, run) {
  const list = parse(tryRun(run, 'gh', ['api', `repos/${repo}/pulls/${pr}/commits?per_page=100`]));
  return Array.isArray(list) ? list : null;
}
// PURE. Every commit authored by the Renovate app account, committed by web-flow and
// signed (as GitHub makes Renovate's API commits); the list ends at the head.
export function commitRefusals(list, view) {
  if (!list) return ['unreadable: the PR commits (REST)'];
  const out = [];
  if (list.length >= 100) out.push(`the commit list may be truncated (${list.length} commits)`);
  if (Array.isArray(view?.commits) && list.length !== view.commits.length) out.push(`the REST and GraphQL commit lists disagree (${list.length} and ${view.commits.length})`);
  if (!list.length || list[list.length - 1]?.sha !== view?.headRefOid) out.push('the REST commit list does not end at the head');
  for (const c of list) {
    const a = c?.author || {}, k = c?.committer || {}, sig = c?.commit?.verification || {};
    if (a.login === RENOVATE.commit_login && a.type === 'Bot' && a.id === RENOVATE.commit_id && k.login === RENOVATE.committer && sig.verified === true) continue;
    out.push(`commit ${short(c?.sha)} is not the Renovate app's (author ${a.login ?? 'none'}/${a.type ?? '?'}/${a.id ?? '?'}, committer ${k.login ?? 'none'}, signature ${sig.verified === true ? 'valid' : sig.reason || 'none'})`);
  }
  return out;
}

// data.pause on the lane state: { pause: string | null }, or null when unreadable.
export function readPause(rules, run) {
  const st = readNamedConfigMap(rules.night.state_configmap, run);
  if (!st) return null;
  return { pause: st.found && st.cm?.data?.pause ? String(st.cm.data.pause) : null };
}
const pauseRefusal = (p, when) => (!p ? `unreadable: data.pause on upgrade-lane-state (${when})` : p.pause ? `paused ${when}: data.pause=${JSON.stringify(p.pause.slice(0, 80))}` : null);

// The whole PR-side preflight: one fresh view, the REST commit identities, story 550's
// R7 (the same three exported functions the tick uses) and the behind-main rule.
export function evaluate({ repo, pr, head, rules, now, run }) {
  const view = readMergeView(repo, pr, run);
  if (!view) return { refusals: ['unreadable: the pull request (gh pr view)'], facts: null };
  const p = prRefusals(view, head, rules, now);
  const refusals = [...p.refusals, ...commitRefusals(readPrCommits(repo, pr, run), view)];
  const facts = { head: p.head, files: p.files, mergeable: view.mergeable, merge_state: view.mergeStateStatus, reference_time: null, main_newer: [], behind_main: [] };
  if (!p.truncated && SHA.test(p.head || '')) {
    const ref = L.checksReferenceTime(p.head_committed_at, p.ev.ran_at);
    facts.reference_time = ref;
    if (!ref) refusals.push("unreadable: when the head's checks ran (needed to compare them with main)");
    else {
      const st = L.checksPredateMain(ref, p.files, L.mainCommitsTouching(repo, p.files, ref, run));
      facts.main_newer = st.newer;
      if (st.unreadable.length) refusals.push(`unreadable: main commits since the head's checks for ${st.unreadable.join(', ')}`);
      if (st.newer.length) refusals.push(`checks predate main: ${st.newer.slice(0, 3).map(c => `${c.path} changed by ${short(c.sha)} at ${c.at}`).join('; ')}${st.newer.length > 3 ? ` (+${st.newer.length - 3})` : ''}, after the head's checks (${ref}); needs a Renovate rebase first`);
    }
    const nm = newestMainCommits(repo, p.files, run);
    const shas = [...new Set(Object.values(nm.byPath).filter(Boolean).map(c => c.sha))];
    const b = behindMain(p.files, nm, headContains(repo, p.head, shas, run));
    facts.behind_main = b.behind;
    if (b.unreadable.length) refusals.push(`unreadable: whether the head contains main's newest commit to ${b.unreadable.join(', ')}`);
    if (b.behind.length) refusals.push(`head is behind main: ${b.behind.slice(0, 3).map(c => `${c.path} changed by ${short(c.sha)} at ${c.at}`).join('; ')}${b.behind.length > 3 ? ` (+${b.behind.length - 3})` : ''}, which the head ${short(p.head)} does not contain; needs a Renovate rebase first`);
  }
  return { refusals: [...new Set(refusals)], facts };
}

// PURE: what the lane needs on top of a clean PR: an allowlisted class and a token that
// can merge (and no wider).
export function laneRefusals({ klass, liveClasses, token }) {
  const out = [];
  if (!LANE_A_CLASSES.includes(klass)) out.push(`class ${klass} is not a lane-A class`);
  else if (!(liveClasses || []).includes(klass)) out.push(`class ${klass} is not in the live allowlist (UPGRADE_NIGHT_LIVE_CLASSES: ${(liveClasses || []).length ? liveClasses.join(', ') : 'empty'})`);
  if (!token?.can_merge) out.push(token?.why || 'unreadable: the token permissions');
  return out;
}

// ── the change lock ──────────────────────────────────────────────────────────────
// PURE. read = readNamedConfigMap() result (null = the read failed).
export function lockStatus(read, now) {
  if (!read) return { state: 'unreadable', why: `unreadable: ConfigMap ${LOCK.namespace}/${LOCK.name} (the read failed)` };
  if (!read.found) return { state: 'absent' };
  const d = read.cm?.data || {};
  const rv = read.cm?.metadata?.resourceVersion || null;
  if (!rv) return { state: 'unreadable', why: `unreadable: ConfigMap ${LOCK.namespace}/${LOCK.name} has no resourceVersion` };
  if (!d.holder) return { state: 'free', rv, last_holder: d.last_holder || null, released_at: d.released_at || null };
  const exp = Date.parse(d.expires_at || '');
  const base = { rv, holder: d.holder, purpose: d.purpose || null, phase: d.phase || null, expires_at: d.expires_at || null };
  if (!Number.isFinite(exp)) return { state: 'unreadable', ...base, why: `the change lock is held by ${d.holder} with no readable expires_at (${JSON.stringify(d.expires_at ?? null)}); fail closed until a person clears it` };
  if (exp <= now) return { state: 'expired', ...base };
  return { state: 'held', ...base, why: `the change lock is held by ${d.holder} (${d.phase || 'taken'}${d.purpose ? `: ${d.purpose}` : ''}) until ${d.expires_at}` };
}

// rules.night.change_lock, checked: a lease shorter than the 15-minute tick, so a crashed
// tick costs at most the next one.
export function lockConfig(rules) {
  const c = rules?.night?.change_lock || {};
  const n = k => Number.isInteger(c[k]) ? c[k] : NaN;
  const cfg = { lease_seconds: n('lease_seconds'), post_merge_lease_seconds: n('post_merge_lease_seconds'), min_lease_left_seconds: n('min_lease_left_seconds'), max_gate_age_seconds: n('max_gate_age_seconds') };
  const ok = c.configmap === LOCK.name && cfg.lease_seconds >= 60 && cfg.lease_seconds < 900 && cfg.post_merge_lease_seconds >= cfg.lease_seconds && cfg.min_lease_left_seconds > 0 && cfg.min_lease_left_seconds < cfg.lease_seconds
    && cfg.max_gate_age_seconds > 0 && cfg.max_gate_age_seconds < cfg.lease_seconds;
  return { ok, ...cfg, why: ok ? null : `night.change_lock in upgrade-rules.json is invalid (configmap must be ${LOCK.name}; 60 <= lease_seconds < 900; post_merge_lease_seconds >= lease_seconds; 0 < min_lease_left_seconds < lease_seconds; 0 < max_gate_age_seconds < lease_seconds)` };
}

const lockManifest = (data, rv) => ({
  apiVersion: 'v1', kind: 'ConfigMap',
  metadata: { name: LOCK.name, namespace: LOCK.namespace, ...(rv ? { resourceVersion: rv } : {}), labels: { 'app.kubernetes.io/name': 'upgrade-shepherd', 'app.kubernetes.io/component': 'upgrade-change-lock' } },
  data,
});

// THE ONE CLUSTER WRITE OF THIS FILE: create or replace ConfigMap upgrade-change-lock.
// A replace carries the resourceVersion that was read, so it fails with Conflict when
// anyone changed the lock in between; a create fails with AlreadyExists.
function writeLock(verb, manifest, env) {
  const m = manifest?.metadata || {};
  if (!['create', 'replace'].includes(verb) || manifest?.kind !== 'ConfigMap' || m.name !== LOCK.name || m.namespace !== LOCK.namespace
    || Object.keys(manifest).some(k => !['apiVersion', 'kind', 'metadata', 'data'].includes(k)) || (verb === 'replace' && !m.resourceVersion)) {
    throw new Error('writeLock: refused (only ConfigMap upgrade-agent/upgrade-change-lock, by create or by replace at a resourceVersion)');
  }
  const r = spawnSync('kubectl', [verb, '-f', '-', '-o', 'json'], { input: JSON.stringify(manifest), encoding: 'utf8', env, timeout: KUBECTL_TIMEOUT_MS });
  if (r.status !== 0) return { ok: false, detail: publicDetail(r.stderr || r.error?.message) };
  return { ok: true, rv: parse(r.stdout)?.metadata?.resourceVersion || null };
}

let HELD = null; // the lock this process holds, for the exit and SIGTERM handlers
let exitHandlers = false;
function releaseOnExit(reason) {
  const h = HELD;
  if (!h || h.merged || h.released) return;
  releaseLock(h, { now: Date.now(), env: h.env, note: `released on process ${reason}` });
}
function installExitRelease() {
  if (exitHandlers) return;
  exitHandlers = true;
  process.once('exit', () => releaseOnExit('exit'));
  process.once('SIGTERM', () => { releaseOnExit('SIGTERM'); process.exit(143); });
}

export function takeLock({ holder, purpose, mode, pr, head, now, leaseSeconds, run, env }) {
  const before = lockStatus(readNamedConfigMap(LOCK.name, run), now);
  if (!['absent', 'free', 'expired'].includes(before.state)) return { taken: false, before, why: before.why };
  const data = {
    schema: LOCK_SCHEMA, holder, purpose, mode, phase: 'taken', pr: pr ? String(pr) : '', head: head || '',
    taken_at: iso(now), expires_at: iso(now + leaseSeconds * 1000), lease_seconds: String(leaseSeconds),
    // an expired lease taken over keeps what it was doing: a merging or merge-unknown
    // phase means a merge may have happened that nobody verified
    ...(before.state === 'expired' ? { took_over_from: before.holder, took_over_phase: before.phase || '', took_over_expired_at: before.expires_at } : {}),
  };
  const verb = before.state === 'absent' ? 'create' : 'replace';
  const w = writeLock(verb, lockManifest(data, before.state === 'absent' ? null : before.rv), env);
  if (!w.ok) return { taken: false, before, why: `the change lock was not taken (${verb} refused: ${w.detail})` };
  const held = { holder, rv: w.rv, data, env, merged: false, released: false };
  HELD = held;
  installExitRelease();
  return { taken: true, before, held };
}

export function releaseLock(held, { now, env, note }) {
  if (!held || held.released) return { released: !!held?.released };
  if (!held.rv) return { released: false, why: `no resourceVersion came back from the take; the lease frees the lock at ${held.data.expires_at}` };
  const data = { schema: LOCK_SCHEMA, holder: '', phase: 'released', last_holder: held.holder, last_purpose: held.data.purpose || '', last_taken_at: held.data.taken_at, released_at: iso(now), ...(note ? { last_note: note } : {}),
    ...(held.data.took_over_from ? { last_took_over_from: held.data.took_over_from, last_took_over_phase: held.data.took_over_phase || '' } : {}) };
  const w = writeLock('replace', lockManifest(data, held.rv), env || held.env);
  if (w.ok) { held.released = true; held.rv = w.rv; if (HELD === held) HELD = null; }
  return { released: w.ok, released_at: w.ok ? data.released_at : null, why: w.ok ? null : `the release was not written (${w.detail}); the lease frees the lock at ${held.data.expires_at}` };
}

// BEFORE the merge call: the lock moves to phase "merging" with the post-merge lease, so a
// kill, a timeout or a lost reply during the call cannot leave only the short lease
// behind. If this write fails the lane does not merge.
export function beginMerge(held, { now, env, leaseSeconds }) {
  if (!held?.rv) return { ok: false, why: 'the change lock has no known resourceVersion; not merging on it' };
  const data = { ...held.data, phase: 'merging', expires_at: iso(now + leaseSeconds * 1000), lease_seconds: String(leaseSeconds), merge_started_at: iso(now) };
  const w = writeLock('replace', lockManifest(data, held.rv), env || held.env);
  if (!w.ok || !w.rv) return { ok: false, why: `the change lock could not be moved to phase merging (${w.ok ? 'no resourceVersion came back' : w.detail}); not merging` };
  held.rv = w.rv; held.data = data; held.merged = true; // from here the exit handler keeps the lock
  return { ok: true };
}

// After a merge the lock stays held through verification. Verification is not built
// (story 553), so the lock is held for post_merge_lease_seconds: until then no later tick
// merges, and the lane makes at most one merge a night.
export function holdAfterMerge(held, { now, env, leaseSeconds, merged, phase = 'merged-awaiting-verification' }) {
  held.merged = true;
  if (!held.rv) return { held: true, why: `no resourceVersion from the take; the lock keeps its first lease (${held.data.expires_at})` };
  const data = { ...held.data, phase, expires_at: iso(now + leaseSeconds * 1000), lease_seconds: String(leaseSeconds), merged_at: iso(now), merge_commit: merged?.merge_commit || '', verification: 'not built (story 553): the lease is the hold' };
  const w = writeLock('replace', lockManifest(data, held.rv), env || held.env);
  if (w.ok) { held.rv = w.rv; held.data = data; }
  return { held: true, expires_at: w.ok ? data.expires_at : held.data.expires_at, why: w.ok ? null : `the post-merge hold was not written (${w.detail}); the lock keeps its first lease (${held.data.expires_at})` };
}

// ── the settle gate (story 551, R6) ─────────────────────────────────────────────────
// Story: https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/551
// No lane merge while the cluster may still be taking up another actor's change: within
// night.settle_gate.minutes (30) of main's newest commit to kubernetes/** (whoever merged
// it: Renovate's platform auto-merge, the shepherd, a person), while Flux has not fetched
// and attempted that commit, or while any Kustomization or HelmRelease is progressing.
// The tick reads it at its start (a blocker on every lane-A candidate), the merge step
// reads it again (both modes), live mode a third time just before the merge call, and the
// hand run at its start and before its call. Every read fails closed.
export function settleConfig(rules) {
  const c = rules?.night?.settle_gate || {};
  const s = c.flux_source || {};
  const name = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/;
  const ok = Number.isInteger(c.minutes) && c.minutes >= 30 && c.minutes <= 240 && c.watched_path === 'kubernetes'
    && Number.isInteger(c.history_per_page) && c.history_per_page >= 5 && c.history_per_page <= 100
    && s.kind === 'GitRepository' && name.test(s.namespace || '') && name.test(s.name || '');
  return { ok, minutes: c.minutes, watched_path: c.watched_path, history_per_page: c.history_per_page, source: { namespace: s.namespace, name: s.name },
    why: ok ? null : 'night.settle_gate in upgrade-rules.json is invalid (minutes an integer from 30 to 240; watched_path kubernetes; history_per_page 5 to 100; flux_source a GitRepository with a namespace and a name)' };
}
// Flux writes a GitRepository revision as <branch>@sha1:<40 hex>.
export const revisionSha = rev => { const m = /^[^@\s]+@sha1:([0-9a-f]{40})$/.exec(String(rev || '')); return m ? m[1] : null; };
// A commit's time: the later of its committer and author dates (a squash merge on GitHub
// stamps both with the merge time). NaN when the committer date is unreadable.
export function commitTime(c) {
  const k = Date.parse(c?.commit?.committer?.date || ''), a = Date.parse(c?.commit?.author?.date || '');
  return Number.isFinite(k) ? Math.max(k, Number.isFinite(a) ? a : k) : NaN;
}
const subjectOf = c => String(c?.commit?.message || '').split('\n')[0].slice(0, 100);

// PURE. One Flux object: { state: 'settled' | 'progressing' | 'failing', why }. 'failing'
// is NotReady in Flux's retry loop, or NotReady for at least the settle window and not
// reconciling: broken, not settling (the baseline gate judges it by the target's scope).
// A suspended object is settled: nothing will move it.
export function fluxSettleState(o, now, windowMs) {
  if (o?.spec?.suspend === true) return { state: 'settled', why: 'suspended' };
  const cs = o?.status?.conditions;
  if (!Array.isArray(cs)) return { state: 'progressing', why: 'no status conditions' };
  const ready = cs.find(c => c?.type === 'Ready');
  if (!ready) return { state: 'progressing', why: 'no Ready condition yet' };
  const rec = cs.find(c => c?.type === 'Reconciling' && c.status === 'True') || null;
  if (ready.status === 'Unknown') return { state: 'progressing', why: `Ready Unknown (${ready.reason || 'no reason'})` };
  if (ready.status === 'True') {
    if (rec) return { state: 'progressing', why: `Reconciling (${rec.reason || 'no reason'})` };
    const gen = o?.metadata?.generation, og = o?.status?.observedGeneration;
    if (!Number.isInteger(gen) || !Number.isInteger(og)) return { state: 'progressing', why: `generation unreadable (generation ${gen ?? 'none'}, observed ${og ?? 'none'})` };
    if (og < gen) return { state: 'progressing', why: `generation ${gen} not observed yet (observed ${og})` };
    return { state: 'settled', why: null };
  }
  if (ready.status === 'False') {
    // Flux's retry loop (Reconciling ProgressingWithRetry) is a failure, not a change in
    // flight: it rewrites lastTransitionTime on every retry (dev-env-h7-metadata-audit,
    // every 5 minutes on 2026-11-11), so a transition time cannot age it out
    if (rec?.reason === 'ProgressingWithRetry') return { state: 'failing', why: `NotReady (${ready.reason || 'no reason'}), in Flux's retry loop` };
    if (rec) return { state: 'progressing', why: `NotReady and Reconciling (${rec.reason || 'no reason'})` };
    const t = Date.parse(ready.lastTransitionTime || '');
    if (!Number.isFinite(t)) return { state: 'progressing', why: `NotReady (${ready.reason || 'no reason'}) since an unreadable time` };
    if (now - t < windowMs) return { state: 'progressing', why: `NotReady (${ready.reason || 'no reason'}) since ${ready.lastTransitionTime}, inside the settle window` };
    return { state: 'failing', why: `NotReady (${ready.reason || 'no reason'}) since ${ready.lastTransitionTime}` };
  }
  return { state: 'progressing', why: `Ready ${ready.status ?? 'without a status'}` };
}

// READ-ONLY (gh api GET and kubectl get through `run`). undefined = the read failed;
// source null = the GitRepository does not exist.
export function readSettleFacts(repo, rules, run) {
  const cfg = settleConfig(rules);
  if (!cfg.ok) return { cfg };
  const ghList = path => { const j = parse(tryRun(run, 'gh', ['api', path])); return Array.isArray(j) ? j : undefined; };
  const kList = args => { const j = parse(tryRun(run, 'kubectl', ['get', ...args, '-o', 'json'])); return Array.isArray(j?.items) ? j : undefined; };
  const k8s = ghList(`repos/${repo}/commits?sha=main&path=${cfg.watched_path}&per_page=${cfg.history_per_page}`);
  const all = ghList(`repos/${repo}/commits?sha=main&per_page=${cfg.history_per_page}`);
  const ks = kList(['kustomizations.kustomize.toolkit.fluxcd.io', '-A']);
  const hr = kList(['helmreleases.helm.toolkit.fluxcd.io', '-A']);
  const sl = kList(['gitrepositories.source.toolkit.fluxcd.io', '-n', cfg.source.namespace, '--field-selector', `metadata.name=${cfg.source.name}`]);
  const source = sl ? (sl.items.find(g => g?.metadata?.name === cfg.source.name && g?.metadata?.namespace === cfg.source.namespace) || null) : undefined;
  // does Flux's artifact contain main's newest commit to kubernetes/**? (compare, as headContains)
  const srcSha = revisionSha(source?.status?.artifact?.revision), newest = k8s?.[0]?.sha;
  let contains;
  if (srcSha && SHA.test(newest || '')) {
    if (srcSha === newest) contains = true;
    else { const h = headContains(repo, srcSha, [newest], run); contains = h.unreadable.length ? null : h.contains[newest]; }
  }
  return { cfg, k8s, all, ks, hr, source, contains };
}

// PURE: the verdict on facts from readSettleFacts at time `now`.
export function settleVerdict(f, now) {
  const cfg = f?.cfg || { ok: false, why: 'unreadable: the settle gate config' };
  const out = { settled: false, at: Number.isFinite(now) ? iso(now) : null, window_minutes: cfg.minutes ?? null, blockers: [], newest_change: null, source_revision: null, unsettled: [], failing_not_settling: [] };
  const add = b => { if (!out.blockers.includes(b)) out.blockers.push(b); };
  if (!cfg.ok) { add(cfg.why); return out; }
  if (!Number.isFinite(now)) { add('unreadable: the time the settle gate is judged at'); return out; }
  const W = cfg.minutes * 60e3, P = `${cfg.watched_path}/**`, src = `${cfg.source.namespace}/${cfg.source.name}`;
  // 1. main's newest commit to kubernetes/**, whoever merged it
  const k0 = Array.isArray(f.k8s) ? f.k8s[0] : undefined;
  if (!Array.isArray(f.k8s)) add(`unreadable: main's newest commits to ${P}`);
  else if (!k0) add(`unreadable: main lists no commit to ${P} at all`);
  else {
    const at = commitTime(k0);
    if (!SHA.test(k0.sha || '') || !Number.isFinite(at)) add(`unreadable: the SHA or time of main's newest commit to ${P}`);
    else {
      out.newest_change = { sha: k0.sha, at: iso(at), subject: subjectOf(k0), age_minutes: Math.floor((now - at) / 60e3) };
      if (now - at < W) add(`main changed ${P} ${Math.max(0, Math.floor((now - at) / 60e3))} min ago (${short(k0.sha)} at ${iso(at)}: ${subjectOf(k0) || 'no subject'}); no lane merge before ${iso(at + W)}, ${cfg.minutes} min after it`);
    }
  }
  // 2. a merge commit inside the window: the path-filtered history can hide what it changed
  if (!Array.isArray(f.all) || !f.all.length) add("unreadable: main's newest commits (to find merge commits inside the settle window)");
  else {
    for (const c of f.all) {
      const at = commitTime(c);
      if (!Number.isFinite(at)) { add(`unreadable: the time of main commit ${short(c?.sha)}`); continue; }
      if (now - at >= W) continue;
      const np = Array.isArray(c?.parents) ? c.parents.length : null;
      if (np !== 1) add(`main commit ${short(c?.sha)} at ${iso(at)} has ${np ?? 'unreadable'} parents, inside the settle window; a path-filtered history may not show what a merge commit changed (fail closed)`);
    }
    const last = commitTime(f.all[f.all.length - 1]);
    if (f.all.length >= cfg.history_per_page && (!Number.isFinite(last) || now - last < W)) add(`unreadable: main has ${cfg.history_per_page} or more commits inside the settle window, more than one read lists`);
  }
  // 3. Flux has fetched that commit, and every Kustomization of the source attempted it
  let rev = null;
  if (f.source === undefined) add(`unreadable: GitRepository ${src}`);
  else if (f.source === null) add(`GitRepository ${src} does not exist`);
  else {
    rev = f.source.status?.artifact?.revision || null;
    out.source_revision = rev;
    const sha = revisionSha(rev);
    if (!sha) add(`unreadable: the artifact revision of GitRepository ${src} (${JSON.stringify(rev)})`);
    else if (out.newest_change) {
      if (f.contains === false) add(`Flux has not fetched main's newest change to ${P} yet: GitRepository ${src} is at ${short(sha)}, which does not contain ${short(out.newest_change.sha)}`);
      else if (f.contains !== true) add(`unreadable: whether GitRepository ${src} (${short(sha)}) contains ${short(out.newest_change.sha)}`);
    }
    const s = fluxSettleState(f.source, now, W);
    if (s.state === 'progressing') out.unsettled.push(`GitRepository ${src}: ${s.why}`);
    else if (s.state === 'failing') add(`GitRepository ${src} is ${s.why}; nothing it serves can be judged settled`);
  }
  // 4. nothing progressing: every Kustomization and HelmRelease, cluster-wide
  if (!f.ks) add('unreadable: kustomizations (settle gate)');
  if (!f.hr) add('unreadable: helmreleases (settle gate)');
  const revSha = revisionSha(rev);
  for (const [kind, list] of [['Kustomization', f.ks], ['HelmRelease', f.hr]]) {
    for (const o of list?.items || []) {
      const id = `${kind} ${o?.metadata?.namespace}/${o?.metadata?.name}`;
      const s = fluxSettleState(o, now, W);
      if (s.state === 'progressing') out.unsettled.push(`${id}: ${s.why}`);
      else if (s.state === 'failing') out.failing_not_settling.push(`${id}: ${s.why}`);
      const ref = o?.spec?.sourceRef || {};
      if (kind === 'Kustomization' && revSha && o?.spec?.suspend !== true && ref.kind === 'GitRepository' && ref.name === cfg.source.name && (ref.namespace || o?.metadata?.namespace) === cfg.source.namespace
        && o?.status?.lastAttemptedRevision !== rev) out.unsettled.push(`${id}: has not attempted ${short(revSha)} yet (last attempted ${o?.status?.lastAttemptedRevision ? short(revisionSha(o.status.lastAttemptedRevision) || o.status.lastAttemptedRevision) : 'nothing'})`);
    }
  }
  if (out.unsettled.length) add(`Flux not settled: ${out.unsettled.slice(0, 5).join('; ')}${out.unsettled.length > 5 ? ` (+${out.unsettled.length - 5} more)` : ''}`);
  out.unsettled = out.unsettled.slice(0, 40);
  out.failing_not_settling = out.failing_not_settling.slice(0, 20);
  out.settled = out.blockers.length === 0;
  return out;
}
// The settle gate, read and judged now.
export const settleCheck = ({ repo = REPO, rules, run, now }) => settleVerdict(readSettleFacts(repo, rules, run), now);
// One log line for a verdict.
export const settleLine = s => (s.settled
  ? `settled (main's newest change to kubernetes/** ${s.newest_change ? `${short(s.newest_change.sha)} at ${s.newest_change.at}, ${s.newest_change.age_minutes} min ago` : 'unknown'}; Flux at ${short(revisionSha(s.source_revision))}; nothing progressing${s.failing_not_settling.length ? `; ${s.failing_not_settling.length} failing, not settling` : ''})`
  : `NOT settled: ${s.blockers.join(' | ')}`);
// What the state and the log keep of a verdict.
export const settleBrief = s => ({ settled: s.settled, at: s.at, window_minutes: s.window_minutes, newest_change: s.newest_change, source_revision: s.source_revision,
  blockers: s.blockers, unsettled: s.unsettled.slice(0, 10), failing_not_settling: s.failing_not_settling.slice(0, 10) });

// ── the merge call ─────────────────────────────────────────────────────────────────
export const mergeArgv = (repo, pr, head) => ['pr', 'merge', String(pr), '--repo', repo, '--squash', '--match-head-commit', head];
export function checkMergeArgv(argv, head) {
  if (argv.some(a => BYPASS_FLAGS.includes(a) || /^--(admin|auto|force)/.test(a))) throw new Error('merge refused: a bypass flag');
  const i = argv.indexOf('--match-head-commit');
  if (argv[0] !== 'pr' || argv[1] !== 'merge' || !argv.includes('--squash') || i < 0 || argv[i + 1] !== head || !SHA.test(head)) throw new Error('merge refused: the call must be a squash merge pinned to the 40-character head SHA');
}
// THE ONE GITHUB WRITE. Never reached in dry-run: it throws unless mode is 'live' (the
// lane, after laneMergeStep's own mode check) or 'hand' (the CLI's --merge).
export function callMerge({ repo, pr, head, mode, verdict, lockHeld, env }) {
  if (mode !== 'live' && mode !== 'hand') throw new Error(`merge refused: mode ${mode} never merges`);
  if (verdict?.ok !== true || verdict.pr !== pr || verdict.head !== head) throw new Error('merge refused: no clean verdict for this PR and head');
  if (mode === 'live' && !lockHeld) throw new Error('merge refused: the lane does not hold the change lock');
  if (repo !== REPO) throw new Error(`merge refused: unknown repository ${repo}`);
  const argv = mergeArgv(repo, pr, head);
  checkMergeArgv(argv, head);
  const r = spawnSync('gh', argv, { encoding: 'utf8', env, timeout: GH_TIMEOUT_MS, input: '' });
  // exit null = timed out or killed: GitHub may have merged; callers treat it as unknown
  return { exit: r.status, signal: r.signal || null, detail: r.status === 0 ? null : r.status === null ? `no exit status (${r.signal || r.error?.code || 'killed'})` : publicDetail(r.stderr || r.error?.message), argv: argv.join(' ') };
}
// What one merge call ended as: merged (verified), failed (a real exit code and the PR
// still open at that head) or unknown (anything else, which keeps the post-merge hold).
export function mergeOutcome(call, after) {
  if (after?.merged) return 'merged';
  if (Number.isInteger(call?.exit) && call.exit !== 0 && after?.state === 'OPEN') return 'merge-failed';
  return 'merge-unknown';
}
// After the call: is the PR merged at that head? null = cannot tell.
export function verifyMerged(repo, pr, head, run) {
  const v = parse(tryRun(run, 'gh', ['pr', 'view', String(pr), '-R', repo, '--json', 'state,mergedAt,headRefOid,mergeCommit']));
  if (!v || typeof v !== 'object') return null;
  return { merged: v.state === 'MERGED' && v.headRefOid === head && !!v.mergedAt, state: v.state ?? null, head: v.headRefOid ?? null, merged_at: v.mergedAt ?? null, merge_commit: v.mergeCommit?.oid ?? null };
}

// HOOK, stories 553 (verify a merge), 753 (revert by pull request) and 754 (a revert
// owed while paused). Pause stops new merges and new lock takes, not this work;
// suspend: true stops every tick, this work included. NOT BUILT in story 752: it reads
// nothing and runs nothing, and says so.
export function owedWork({ mode, paused }) {
  return { built: false, mode, paused: !!paused, ran: [], note: 'verification (story 553) and revert (stories 753, 754) of a merge already made are not built; nothing owed was run' };
}

// ── the lane's merge step (one per tick) ─────────────────────────────────────────────
// subject = { pr, head, class, chosen } | null. chosen = false: the tick chose no PR and
// this is the nearest lane-A candidate, exercised in dry-run only.
// windowOpen(ms) = { open, local } for the merge window; tickStartedAt = when the tick
// began (its gates are that old). Live mode re-checks both, and the pause, just before
// the merge call.
export function laneMergeStep({ mode, subject, rules, run, env, clock, liveClasses, token, holder, takeAllowed, windowOpen, tickStartedAt }) {
  const cfg = lockConfig(rules);
  const out = { mode, lock: { configmap: `${LOCK.namespace}/${LOCK.name}`, outcome: 'not-taken' }, verdict: null };
  const notRun = why => ({ verdict: 'not-run', why });
  const refusedEarly = why => (subject ? { pr: subject.pr, head: subject.head, class: subject.class, chosen: subject.chosen, mode, verdict: 'refused', pr_refusals: [why], lane_refusals: [], pr_ready: false, merge_call: mode === 'live' ? 'not made (refused)' : 'not reached (dry-run)' } : notRun(why));
  // pause, read before the take (and again before the merge call)
  const p0 = readPause(rules, run);
  const pause = pauseRefusal(p0, 'before the lock take');
  if (pause) {
    out.lock.why = !p0 ? `unreadable: ConfigMap ${rules.night.state_configmap} (pause); no lock taken` : 'paused (data.pause); no lock taken';
    out.verdict = refusedEarly(pause);
    return out;
  }
  if (!cfg.ok) { out.lock.why = cfg.why; out.verdict = refusedEarly(cfg.why); return out; }
  let held = null, take = null;
  if (takeAllowed) {
    const t0 = clock();
    take = takeLock({ holder, purpose: subject ? `${mode === 'live' ? 'merge step' : 'dry-run exercise'}: #${subject.pr} ${short(subject.head)}` : `${mode} exercise: no candidate`, mode, pr: subject?.pr, head: subject?.head, now: t0, leaseSeconds: cfg.lease_seconds, run, env });
    out.lock.before = { state: take.before.state, holder: take.before.holder || null, expires_at: take.before.expires_at || null };
    if (take.taken) { held = take.held; out.lock = { ...out.lock, outcome: 'taken', holder, taken_at: held.data.taken_at, expires_at: held.data.expires_at, ...(take.before.state === 'expired' ? { took_over_from: take.before.holder, took_over_phase: take.before.phase || null } : {}) }; }
    else out.lock.why = take.why;
  } else {
    const s = lockStatus(readNamedConfigMap(LOCK.name, run), clock());
    out.lock = { ...out.lock, outcome: 'not-taken (local run: no --write-state)', before: { state: s.state, holder: s.holder || null, expires_at: s.expires_at || null },
      why: `no change lock held: a local run (no --write-state) never takes it${s.why ? `; ${s.why}` : ''}` };
  }
  try {
    if (!subject) { out.verdict = notRun('no lane-A candidate this tick'); return out; }
    const ev = evaluate({ repo: REPO, pr: subject.pr, head: subject.head, rules, now: clock(), run });
    const prRef = [...(held ? [] : [out.lock.why || 'no change lock held']), ...ev.refusals];
    const laneRef = laneRefusals({ klass: subject.class, liveClasses, token });
    if (!subject.chosen) laneRef.unshift('not chosen by the tick: it has gate blockers (records[].gates.blockers)');
    // the settle gate (story 551), read afresh under the lock, in both modes
    const settle = settleCheck({ rules, run, now: clock() });
    laneRef.push(...settle.blockers.map(b => `settle gate: ${b}`));
    out.settle = settleBrief(settle);
    const v = { pr: subject.pr, head: subject.head, class: subject.class, chosen: subject.chosen, mode, verdict: 'refused', pr_refusals: [...new Set(prRef)], lane_refusals: laneRef, pr_ready: prRef.length === 0, facts: ev.facts };
    const clean = v.pr_refusals.length === 0 && laneRef.length === 0;
    if (mode !== 'live') { v.verdict = clean ? 'would-merge' : 'refused'; v.merge_call = 'not reached (dry-run)'; out.verdict = v; return out; }
    if (!clean) { v.merge_call = 'not made (refused)'; out.verdict = v; return out; }
    // just before the call: the lease, the window, the age of the tick's gates, the pause
    const t = clock();
    const left = Date.parse(held.data.expires_at) - t;
    if (!held.rv) v.lane_refusals.push('the change lock was written but its resourceVersion is unknown; not merging on it');
    else if (left < cfg.min_lease_left_seconds * 1000) v.lane_refusals.push(`the change lock has ${Math.round(left / 1000)}s of lease left, under ${cfg.min_lease_left_seconds}s; not merging on a lease that may lapse`);
    const win = typeof windowOpen === 'function' ? windowOpen(t) : null;
    if (!win || win.open !== true) v.lane_refusals.push(win ? `the merge window is closed at the merge call (now ${win.local})` : 'unreadable: whether the merge window is open at the merge call');
    const age = t - tickStartedAt;
    if (!Number.isFinite(age) || age < 0 || age > cfg.max_gate_age_seconds * 1000) v.lane_refusals.push(Number.isFinite(age) ? `the tick's gates are ${Math.round(age / 1000)}s old at the merge call, over ${cfg.max_gate_age_seconds}s` : 'unreadable: when the tick began');
    // the settle gate again: another actor may have merged while the step read the PR
    const s1 = settleCheck({ rules, run, now: clock() });
    v.lane_refusals.push(...s1.blockers.map(b => `settle gate at the merge call: ${b}`));
    v.settle_at_merge_call = settleBrief(s1);
    const p1 = pauseRefusal(readPause(rules, run), 'just before the merge call');
    if (p1) v.pr_refusals.push(p1);
    if (v.pr_refusals.length || v.lane_refusals.length) { v.pr_ready = v.pr_refusals.length === 0; v.merge_call = 'not made (refused)'; out.verdict = v; return out; }
    const begin = beginMerge(held, { now: clock(), env, leaseSeconds: cfg.post_merge_lease_seconds });
    if (!begin.ok) { v.lane_refusals.push(begin.why); v.merge_call = 'not made (refused)'; out.verdict = v; return out; }
    // held.merged is now true: whatever happens next, the lock keeps the post-merge lease
    const call = callMerge({ repo: REPO, pr: subject.pr, head: subject.head, mode, verdict: { ok: v.pr_refusals.length === 0 && v.lane_refusals.length === 0, pr: v.pr, head: v.head }, lockHeld: true, env });
    const after = verifyMerged(REPO, subject.pr, subject.head, run);
    v.merge_call = call.argv + (call.exit === 0 ? ' (exit 0)' : ` (exit ${call.exit}: ${call.detail})`);
    v.merged = after;
    v.verdict = mergeOutcome(call, after);
    if (v.verdict === 'merge-failed') held.merged = false; // a real failure with the PR still open: release
    out.verdict = v;
    return out;
  } finally {
    if (held && held.merged) {
      const phase = out.verdict?.verdict === 'merged' ? 'merged-awaiting-verification' : 'merge-unknown';
      const h = holdAfterMerge(held, { now: clock(), env, leaseSeconds: cfg.post_merge_lease_seconds, merged: out.verdict?.merged, phase });
      out.lock = { ...out.lock, outcome: `held after merge (${phase})`, expires_at: h.expires_at, ...(h.why ? { why: h.why } : {}) };
      out.owed = owedWork({ mode, paused: false });
    } else if (held) {
      const r = releaseLock(held, { now: clock(), env, note: out.verdict?.verdict || 'tick ended' });
      out.lock = { ...out.lock, outcome: r.released ? 'taken-and-released' : 'taken, release failed', ...(r.released_at ? { released_at: r.released_at } : {}), ...(r.why ? { why: r.why } : {}) };
    }
  }
}

// ── CLI: the hand run (rehearsal) ──────────────────────────────────────────────────
// node night-merge.mjs --pr <n> --head <40-char sha> (--check-only | --merge) [--json]
// Exit: 0 = clean verdict (and merged, with --merge); 1 = refused, nothing merged;
// 2 = usage; 3 = the merge call failed or its outcome could not be verified.
const USAGE = 'usage: night-merge.mjs --pr <number> --head <40-character sha> (--check-only | --merge) [--json]';
export function parseCli(argv) {
  const o = { pr: null, head: null, checkOnly: false, merge: false, json: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--pr') o.pr = argv[++i];
    else if (a === '--head') o.head = argv[++i];
    else if (a === '--check-only') o.checkOnly = true;
    else if (a === '--merge') o.merge = true;
    else if (a === '--json') o.json = true;
    else return { error: `unknown argument ${JSON.stringify(a)}` };
  }
  if (!/^[1-9]\d*$/.test(String(o.pr ?? ''))) return { error: '--pr must be a positive number' };
  if (!SHA.test(o.head || '')) return { error: '--head must be the 40-character lowercase head SHA' };
  if (o.checkOnly === o.merge) return { error: 'give exactly one of --check-only and --merge' };
  return { ...o, pr: Number(o.pr) };
}

// The hand run judges the PR as the lane would: the classifier (path classes, holds,
// majors and prereleases, package rules, vet markers) must put it in LANE-A, and the
// merge step's preflight must pass. It skips what only the tick knows: the merge window,
// the class allowlist and the tick's gates (backups, the Flux baseline, critical alerts,
// work orders). It needs the owner's yes for the PR; it reads no approval.
export function handRun(opt, { rules, run, env, now, clock = () => Date.now() }) {
  const out = { mode: opt.merge ? 'hand --merge' : 'hand --check-only', pr: opt.pr, head: opt.head, refusals: [], notes: [] };
  const p0 = pauseRefusal(readPause(rules, run), 'at the start');
  if (p0) out.refusals.push(p0);
  const lock = lockStatus(readNamedConfigMap(LOCK.name, run), now);
  out.lock = { state: lock.state, holder: lock.holder || null, expires_at: lock.expires_at || null };
  if (['held', 'unreadable'].includes(lock.state)) out.refusals.push(lock.why);
  out.notes.push('a hand run reads the change lock and refuses while it is held; it cannot take it (a session cannot write ConfigMaps in upgrade-agent), so the shepherd and the lane do not see it');
  out.notes.push('a hand run runs the settle gate (story 551) at its start and, with --merge, again just before the call');
  out.notes.push('a hand run skips the merge window, the class allowlist and the tick gates (backups, Flux baseline, critical alerts, work orders); it needs the owner\'s yes');
  // the settle gate (story 551): no merge within 30 minutes of another kubernetes/** merge,
  // before Flux took it up, or while Flux is progressing
  const st0 = settleCheck({ rules, run, now });
  out.settle = settleBrief(st0);
  out.refusals.push(...st0.blockers.map(b => `settle gate: ${b}`));
  // the classifier, on the same facts the tick gathers
  let holdRules = null, facts = null;
  try { holdRules = L.fetchHoldRules(REPO, run); } catch { holdRules = null; }
  try { facts = L.gatherFacts(REPO, opt.pr, run); } catch { facts = null; }
  if (!holdRules) out.refusals.push('unreadable: .renovate/holds.json5 on main');
  if (!facts) out.refusals.push('unreadable: the PR facts for the classifier');
  if (holdRules && facts) {
    const rec = L.classify(facts, rules, { now, holdRules });
    out.lane = { lane: rec.lane, class: rec.class, reason: rec.reason, rules: rec.rules };
    if (rec.lane !== 'LANE-A') out.refusals.push(`lane ${rec.lane} (${rec.class}): ${rec.reason}`);
  }
  const ev = evaluate({ repo: REPO, pr: opt.pr, head: opt.head, rules, now, run });
  out.refusals.push(...ev.refusals);
  out.facts = ev.facts;
  if (opt.merge) {
    const s1 = settleCheck({ rules, run, now: clock() });
    out.settle_at_merge_call = settleBrief(s1);
    out.refusals.push(...s1.blockers.map(b => `settle gate just before the merge call: ${b}`));
    const p1 = pauseRefusal(readPause(rules, run), 'just before the merge call'); if (p1) out.refusals.push(p1);
  }
  out.refusals = [...new Set(out.refusals)];
  out.ok = out.refusals.length === 0;
  out.verdict = out.ok ? (opt.merge ? 'merging' : 'would-merge') : 'refused';
  if (!opt.merge || !out.ok) { out.merge_call = opt.merge ? 'not made (refused)' : 'not made (--check-only)'; return out; }
  const call = callMerge({ repo: REPO, pr: opt.pr, head: opt.head, mode: 'hand', verdict: { ok: out.refusals.length === 0, pr: opt.pr, head: opt.head }, lockHeld: false, env });
  out.merge_call = call.argv + (call.exit === 0 ? ' (exit 0)' : ` (exit ${call.exit}: ${call.detail})`);
  out.merged = verifyMerged(REPO, opt.pr, opt.head, run);
  out.verdict = mergeOutcome(call, out.merged);
  return out;
}

async function main(argv) {
  const opt = parseCli(argv);
  if (opt.error) { console.error(`night-merge: ${opt.error}\n${USAGE}`); return 2; }
  // UPGRADE_NIGHT_NOW pins the clock for a read-only check; a real merge never runs on it.
  // ...nor on the selftests' preload (FAKE_NOW) or any other replaced Date.now.
  const pinned = process.env.UPGRADE_NIGHT_NOW ? 'UPGRADE_NIGHT_NOW is set' : process.env.FAKE_NOW ? 'FAKE_NOW is set' : !/\[native code\]/.test(Function.prototype.toString.call(Date.now)) ? 'Date.now is not the native clock' : null;
  if (opt.merge && pinned) { console.error(`night-merge: --merge refuses a pinned clock (${pinned})\n${USAGE}`); return 2; }
  const rules = L.loadRules(process.env.UPGRADE_RULES || join(dirname(fileURLToPath(import.meta.url)), 'upgrade-rules.json'));
  const now = Date.parse(process.env.UPGRADE_NIGHT_NOW || '') || Date.now();
  // gh uses the caller's own identity; this CLI reads no token file.
  const r = handRun(opt, { rules, run: L.runReadOnly, env: process.env, now });
  if (opt.json) process.stdout.write(JSON.stringify(r, null, 2) + '\n');
  else {
    const lines = [`merge step (${r.mode}) #${r.pr} head ${short(r.head)}: ${r.verdict.toUpperCase()}`];
    if (r.lane) lines.push(`  lane: ${r.lane.lane} (${r.lane.class}): ${r.lane.reason}`);
    for (const x of r.refusals) lines.push(`  refused: ${x}`);
    lines.push(`  lock: ${LOCK.namespace}/${LOCK.name} ${r.lock.state}${r.lock.holder ? ` (holder ${r.lock.holder} until ${r.lock.expires_at})` : ''}`);
    if (r.settle) lines.push(`  settle gate: ${settleLine(r.settle)}`);
    for (const n of r.notes) lines.push(`  note: ${n}`);
    if (r.facts) lines.push(`  facts: reference_time=${r.facts.reference_time} mergeable=${r.facts.mergeable} merge_state=${r.facts.merge_state} files=${r.facts.files.join(',')}`);
    lines.push(`  merge call: ${r.merge_call}`);
    if (r.merged) lines.push(`  after: state=${r.merged.state} head=${short(r.merged.head)} merge_commit=${short(r.merged.merge_commit)}`);
    process.stdout.write(lines.join('\n') + '\n');
  }
  if (r.verdict === 'merged' || r.verdict === 'would-merge') return 0;
  return r.verdict === 'refused' ? 1 : 3;
}

if (L.isMain(import.meta.url)) {
  main(process.argv.slice(2)).then(c => process.exit(c)).catch(e => { console.error(`night-merge: FAILED: ${e.message}`); process.exit(3); });
}
