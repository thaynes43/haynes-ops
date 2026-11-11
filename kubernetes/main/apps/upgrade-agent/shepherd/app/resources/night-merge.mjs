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
// unless: it is open, not a draft, based on main and opened by Renovate; every commit is
// Renovate's; the head is the chosen head; GitHub reads it MERGEABLE; no check on the
// head is running or failing and no commit status is anything but SUCCESS (a PENDING or
// EXPECTED status is refused: the handoff preflight let it through); the repository's
// required checks and every check the check map expects concluded SUCCESS; main has not
// changed one of its files after its checks ran (story 550's R7, the same functions);
// and the head contains the newest main commit that touches each of its files (a head
// behind main by such a commit is refused: "require branches to be up to date" is off
// for main, so GitHub would merge it). The merge is `gh pr merge <n> --squash
// --match-head-commit <head>`: never --admin, --auto or a force flag.
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
// identity when the verdict is clean. A hand run reads the lock and the pause and
// refuses when either is set, but does not take the lock (story 551).
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
const SHA = /^[0-9a-f]{40}$/;
const VIEW_FIELDS = 'number,state,isDraft,headRefOid,baseRefName,author,mergeable,mergeStateStatus,files,changedFiles,commits,statusCheckRollup';
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
  if (!rules.renovate_pr_authors.includes(view?.author?.login)) add(`opened by ${view?.author?.login ?? 'unknown'}, not Renovate`);
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

// The whole PR-side preflight: one fresh view, story 550's R7 (the same three exported
// functions the tick uses) and the behind-main rule.
export function evaluate({ repo, pr, head, rules, now, run }) {
  const view = readMergeView(repo, pr, run);
  if (!view) return { refusals: ['unreadable: the pull request (gh pr view)'], facts: null };
  const p = prRefusals(view, head, rules, now);
  const refusals = [...p.refusals];
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
  const cfg = { lease_seconds: n('lease_seconds'), post_merge_lease_seconds: n('post_merge_lease_seconds'), min_lease_left_seconds: n('min_lease_left_seconds') };
  const ok = c.configmap === LOCK.name && cfg.lease_seconds >= 60 && cfg.lease_seconds < 900 && cfg.post_merge_lease_seconds >= cfg.lease_seconds && cfg.min_lease_left_seconds > 0 && cfg.min_lease_left_seconds < cfg.lease_seconds;
  return { ok, ...cfg, why: ok ? null : `night.change_lock in upgrade-rules.json is invalid (configmap must be ${LOCK.name}; 60 <= lease_seconds < 900; post_merge_lease_seconds >= lease_seconds; 0 < min_lease_left_seconds < lease_seconds)` };
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
    ...(before.state === 'expired' ? { took_over_from: before.holder, took_over_expired_at: before.expires_at } : {}),
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
  const data = { schema: LOCK_SCHEMA, holder: '', phase: 'released', last_holder: held.holder, last_purpose: held.data.purpose || '', last_taken_at: held.data.taken_at, released_at: iso(now), ...(note ? { last_note: note } : {}) };
  const w = writeLock('replace', lockManifest(data, held.rv), env || held.env);
  if (w.ok) { held.released = true; held.rv = w.rv; if (HELD === held) HELD = null; }
  return { released: w.ok, released_at: w.ok ? data.released_at : null, why: w.ok ? null : `the release was not written (${w.detail}); the lease frees the lock at ${held.data.expires_at}` };
}

// After a merge the lock stays held through verification. Verification is not built
// (story 553), so the lock is held for post_merge_lease_seconds: until then no later tick
// merges, and the lane makes at most one merge a night.
export function holdAfterMerge(held, { now, env, leaseSeconds, merged }) {
  held.merged = true;
  if (!held.rv) return { held: true, why: `no resourceVersion from the take; the lock keeps its first lease (${held.data.expires_at})` };
  const data = { ...held.data, phase: 'merged-awaiting-verification', expires_at: iso(now + leaseSeconds * 1000), lease_seconds: String(leaseSeconds), merged_at: iso(now), merge_commit: merged?.merge_commit || '', verification: 'not built (story 553): the lease is the hold' };
  const w = writeLock('replace', lockManifest(data, held.rv), env || held.env);
  if (w.ok) { held.rv = w.rv; held.data = data; }
  return { held: true, expires_at: w.ok ? data.expires_at : held.data.expires_at, why: w.ok ? null : `the post-merge hold was not written (${w.detail}); the lock keeps its first lease (${held.data.expires_at})` };
}

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
  return { exit: r.status, detail: r.status === 0 ? null : publicDetail(r.stderr || r.error?.message), argv: argv.join(' ') };
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
export function laneMergeStep({ mode, subject, rules, run, env, clock, liveClasses, token, holder, takeAllowed }) {
  const cfg = lockConfig(rules);
  const out = { mode, lock: { configmap: `${LOCK.namespace}/${LOCK.name}`, outcome: 'not-taken' }, verdict: null };
  const notRun = why => ({ verdict: 'not-run', why });
  const refusedEarly = why => (subject ? { pr: subject.pr, head: subject.head, class: subject.class, chosen: subject.chosen, mode, verdict: 'refused', pr_refusals: [why], lane_refusals: [], pr_ready: false, merge_call: mode === 'live' ? 'not made (refused)' : 'not reached (dry-run)' } : notRun(why));
  // pause, re-read just before the take: a pause set mid-tick stops the merge
  const st = readNamedConfigMap(rules.night.state_configmap, run);
  const pause = !st ? 'unreadable' : (st.found && st.cm?.data?.pause) ? String(st.cm.data.pause) : null;
  if (pause) {
    out.lock.why = pause === 'unreadable' ? `unreadable: ConfigMap ${rules.night.state_configmap} (pause); no lock taken` : 'paused (data.pause); no lock taken';
    out.verdict = refusedEarly(pause === 'unreadable' ? `unreadable: data.pause on ${rules.night.state_configmap}` : `paused: data.pause=${JSON.stringify(pause.slice(0, 80))}`);
    return out;
  }
  if (!cfg.ok) { out.lock.why = cfg.why; out.verdict = refusedEarly(cfg.why); return out; }
  let held = null, take = null;
  if (takeAllowed) {
    const t0 = clock();
    take = takeLock({ holder, purpose: subject ? `${mode === 'live' ? 'merge step' : 'dry-run exercise'}: #${subject.pr} ${short(subject.head)}` : `${mode} exercise: no candidate`, mode, pr: subject?.pr, head: subject?.head, now: t0, leaseSeconds: cfg.lease_seconds, run, env });
    out.lock.before = { state: take.before.state, holder: take.before.holder || null, expires_at: take.before.expires_at || null };
    if (take.taken) { held = take.held; out.lock = { ...out.lock, outcome: 'taken', holder, taken_at: held.data.taken_at, expires_at: held.data.expires_at, ...(take.before.state === 'expired' ? { took_over_from: take.before.holder } : {}) }; }
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
    const v = { pr: subject.pr, head: subject.head, class: subject.class, chosen: subject.chosen, mode, verdict: 'refused', pr_refusals: [...new Set(prRef)], lane_refusals: laneRef, pr_ready: prRef.length === 0, facts: ev.facts };
    const clean = v.pr_refusals.length === 0 && laneRef.length === 0;
    if (mode !== 'live') { v.verdict = clean ? 'would-merge' : 'refused'; v.merge_call = 'not reached (dry-run)'; out.verdict = v; return out; }
    if (!clean) { v.merge_call = 'not made (refused)'; out.verdict = v; return out; }
    const left = Date.parse(held.data.expires_at) - clock();
    if (!held.rv || left < cfg.min_lease_left_seconds * 1000) {
      v.lane_refusals.push(!held.rv ? 'the change lock was written but its resourceVersion is unknown; not merging on it' : `the change lock has ${Math.round(left / 1000)}s of lease left, under ${cfg.min_lease_left_seconds}s; not merging on a lease that may lapse`);
      v.merge_call = 'not made (refused)'; out.verdict = v; return out;
    }
    held.merged = true; // from here the exit handler keeps the lock: a merge may have happened
    const call = callMerge({ repo: REPO, pr: subject.pr, head: subject.head, mode, verdict: { ok: true, pr: subject.pr, head: subject.head }, lockHeld: true, env });
    const after = verifyMerged(REPO, subject.pr, subject.head, run);
    v.merge_call = call.argv + (call.exit === 0 ? ' (exit 0)' : ` (exit ${call.exit}: ${call.detail})`);
    v.merged = after;
    if (after?.merged) v.verdict = 'merged';
    else if (after && after.state === 'OPEN' && call.exit !== 0) { v.verdict = 'merge-failed'; held.merged = false; }
    else v.verdict = 'merge-unknown'; // treated as merged: the lock is held for the post-merge lease
    out.verdict = v;
    return out;
  } finally {
    if (held && held.merged) {
      const h = holdAfterMerge(held, { now: clock(), env, leaseSeconds: cfg.post_merge_lease_seconds, merged: out.verdict?.merged });
      out.lock = { ...out.lock, outcome: 'held after merge (merged-awaiting-verification)', expires_at: h.expires_at, ...(h.why ? { why: h.why } : {}) };
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

export function handRun(opt, { rules, run, env, now }) {
  const out = { mode: opt.merge ? 'hand --merge' : 'hand --check-only', pr: opt.pr, head: opt.head, refusals: [], notes: [] };
  const st = readNamedConfigMap(rules.night.state_configmap, run);
  if (!st) out.refusals.push(`unreadable: data.pause on ${rules.night.state_configmap}`);
  else if (st.found && st.cm?.data?.pause) out.refusals.push(`paused: data.pause=${JSON.stringify(String(st.cm.data.pause).slice(0, 80))}`);
  const lock = lockStatus(readNamedConfigMap(LOCK.name, run), now);
  out.lock = { state: lock.state, holder: lock.holder || null, expires_at: lock.expires_at || null };
  if (['held', 'unreadable'].includes(lock.state)) out.refusals.push(lock.why);
  out.notes.push('a hand run reads the change lock and refuses while it is held; it does not take it (story 551)');
  const ev = evaluate({ repo: REPO, pr: opt.pr, head: opt.head, rules, now, run });
  out.refusals.push(...ev.refusals);
  out.facts = ev.facts;
  out.ok = out.refusals.length === 0;
  out.verdict = out.ok ? (opt.merge ? 'merging' : 'would-merge') : 'refused';
  if (!opt.merge || !out.ok) { out.merge_call = opt.merge ? 'not made (refused)' : 'not made (--check-only)'; return out; }
  const call = callMerge({ repo: REPO, pr: opt.pr, head: opt.head, mode: 'hand', verdict: { ok: true, pr: opt.pr, head: opt.head }, lockHeld: false, env });
  out.merge_call = call.argv + (call.exit === 0 ? ' (exit 0)' : ` (exit ${call.exit}: ${call.detail})`);
  out.merged = verifyMerged(REPO, opt.pr, opt.head, run);
  out.verdict = out.merged?.merged ? 'merged' : (out.merged?.state === 'OPEN' && call.exit !== 0) ? 'merge-failed' : 'merge-unknown';
  return out;
}

async function main(argv) {
  const opt = parseCli(argv);
  if (opt.error) { console.error(`night-merge: ${opt.error}\n${USAGE}`); return 2; }
  const rules = L.loadRules(process.env.UPGRADE_RULES || join(dirname(fileURLToPath(import.meta.url)), 'upgrade-rules.json'));
  // UPGRADE_NIGHT_NOW pins the clock for the selftest only.
  const now = Date.parse(process.env.UPGRADE_NIGHT_NOW || '') || Date.now();
  // gh uses the caller's own identity; this CLI reads no token file.
  const r = handRun(opt, { rules, run: L.runReadOnly, env: process.env, now });
  if (opt.json) process.stdout.write(JSON.stringify(r, null, 2) + '\n');
  else {
    const lines = [`merge step (${r.mode}) #${r.pr} head ${short(r.head)}: ${r.verdict.toUpperCase()}`];
    for (const x of r.refusals) lines.push(`  refused: ${x}`);
    lines.push(`  lock: ${LOCK.namespace}/${LOCK.name} ${r.lock.state}${r.lock.holder ? ` (holder ${r.lock.holder} until ${r.lock.expires_at})` : ''}`);
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
