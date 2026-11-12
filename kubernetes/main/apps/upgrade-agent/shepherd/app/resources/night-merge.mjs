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
// for main, so GitHub would merge it). GitHub computes mergeability lazily and answers
// UNKNOWN on the first read after main moves, so the step reads the view again, at most
// MERGEABLE_REREAD.pauses_ms.length more times, until GitHub has answered; it judges the
// last view read, whole, and refuses if the head moved between reads (story 773). Just
// before the call, live mode re-checks the lease, the merge window, the age of the tick's
// gates (300s) and the pause, then moves the lock to phase "merging" with the post-merge
// lease. The merge is `gh pr merge <n>
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
// 'live'. The three spawn sites in this file are writeLock() (create or replace of the
// lock ConfigMap, nothing else), callMerge() and ghWrite() (story 549: the lane's own PR
// record comment and Renovate's retry box, never a merge; see "PR records and the
// stalled-check retry" below). Every read goes through the classifier's read-only runner.
// scripts/upgrade-agent/lane-merge-step-selftest.mjs, lane-night-dryrun-selftest.mjs and
// lane-pr-records-selftest.mjs check all of this.
//
// HAND RUN (the rehearsal): node night-merge.mjs --pr <n> --head <sha> --check-only
// prints the verdict and changes nothing; --merge merges once with the caller's own gh
// identity when the verdict is clean, and needs the owner's yes. It runs the classifier
// (the PR must be LANE-A) and the same preflight, reads the lock and the pause and
// refuses when either is set, but cannot take the lock (a session cannot write ConfigMaps in
// upgrade-agent). It runs the settle gate and F4 (story 551) at its start and again before
// a --merge call. It skips the merge window, the class allowlist and the tick's other
// gates. --merge refuses UPGRADE_NIGHT_NOW.
//
// THE SETTLE GATE (story 551; facts, not clocks: Tom's ruling Q-548-04). settleVerdict():
// for a candidate's target, F1 nothing on main that Flux has not applied reaches it, its
// dependsOn chain or a cluster-wide part; F2 it has applied the source revision and is
// settled; F3 no cluster-wide part is mid-change. F4: no shepherd Job that can merge is
// running. Read at the tick's start, in the merge step, just before a live merge call
// (after the merging write, for F4), and by the hand run. otherChanges() is the helper the
// verifier (stories 553 and 753) uses for "another change landed".
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

// ── GitHub's mergeability, read until it has answered (story 773) ────────────────────
// Story: https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/773
// GitHub works out whether a PR can merge only when asked: the first read after main
// moves answers mergeable UNKNOWN (or mergeStateStatus UNKNOWN) while a background job
// computes it. On 2026-11-12, 5 of 20 dry-run ticks refused PR 4752 that way, each the
// first tick after a merge to main. So while an open PR reads UNKNOWN the step reads the
// view again after each pause below: at most 4 reads and 14s of pauses. This polls
// GitHub's own answer about this one PR; it waits on no other actor (ruling Q-548-04).
// The bound is a count of reads; what counts as mergeable is unchanged.
export const MERGEABLE_REREAD = Object.freeze({ pauses_ms: Object.freeze([2000, 4000, 8000]) });
// The step is synchronous (every read is a spawnSync), so the pause blocks the thread
// without spinning. Injectable: the selftests pass one that does not sleep.
export const sleepSync = ms => { if (Number.isFinite(ms) && ms > 0) Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms); };
// Pending = an open PR whose mergeable reads UNKNOWN, or reads MERGEABLE with the merge
// state still UNKNOWN. An answer that refuses (CONFLICTING) is final: it is not re-read.
export const mergeabilityPending = v => v?.state === 'OPEN' && (v.mergeable === 'UNKNOWN' || (v.mergeable === 'MERGEABLE' && v.mergeStateStatus === 'UNKNOWN'));
// { view, reads, why }. view = the LAST view read, whole, never fields mixed from two
// reads; null when a read failed (fail closed, and no earlier view is used instead).
// why = a refusal when the head moved between reads (two readable heads that differ):
// the step does not go on with the new head in the same tick. An unreadable head is the
// preflight's refusal ("unreadable: the PR head SHA"), not a move.
export function readMergeViewAnswered(repo, pr, run, { rereadPause = sleepSync } = {}) {
  const headOf = v => (SHA.test(v?.headRefOid || '') ? v.headRefOid : null);
  let view = readMergeView(repo, pr, run), reads = 1;
  const first = headOf(view);
  for (const ms of MERGEABLE_REREAD.pauses_ms) {
    if (!view || !mergeabilityPending(view)) break;
    rereadPause(ms);
    view = readMergeView(repo, pr, run); reads++;
    const cur = headOf(view);
    if (first && cur && cur !== first) return { view, reads, why: `head moved between reads of the pull request (${short(first)} on read 1, ${short(cur)} on read ${reads}); not judging the new head in this tick` };
  }
  return { view, reads, why: null };
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
export const pauseRefusal = (p, when) => (!p ? `unreadable: data.pause on upgrade-lane-state (${when})` : p.pause ? `paused ${when}: data.pause=${JSON.stringify(p.pause.slice(0, 80))}` : null);

// The whole PR-side preflight: one fresh view (read again while GitHub has not computed
// its mergeability, story 773), the REST commit identities, story 550's R7 (the same
// three exported functions the tick uses) and the behind-main rule. Every decision below
// is on the last view read. rereadPause = the wait between reads (sleepSync unless injected).
export function evaluate({ repo, pr, head, rules, now, run, rereadPause }) {
  const r = readMergeViewAnswered(repo, pr, run, { rereadPause });
  const view = r.view;
  if (!view) return { refusals: [r.reads === 1 ? 'unreadable: the pull request (gh pr view)' : `unreadable: the pull request (gh pr view, read ${r.reads} of ${MERGEABLE_REREAD.pauses_ms.length + 1}, while GitHub had not yet computed its mergeability)`], facts: null };
  const p = prRefusals(view, head, rules, now);
  const facts = { head: p.head, files: p.files, mergeable: view.mergeable, merge_state: view.mergeStateStatus, mergeable_reads: r.reads, reference_time: null, main_newer: [], behind_main: [] };
  if (r.why) return { refusals: [...new Set([r.why, ...p.refusals])], facts };
  const refusals = [...p.refusals, ...commitRefusals(readPrCommits(repo, pr, run), view)];
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
// behind. If this write fails the lane does not merge. held.merged (the exit handler then
// keeps the lock) is set by the caller immediately before the call, not here: an exit
// between this write and the call releases the lock (story 551, S4). A SIGKILL there
// leaves the merging phase to its lease: no handler runs.
export function beginMerge(held, { now, env, leaseSeconds }) {
  if (!held?.rv) return { ok: false, why: 'the change lock has no known resourceVersion; not merging on it' };
  const data = { ...held.data, phase: 'merging', expires_at: iso(now + leaseSeconds * 1000), lease_seconds: String(leaseSeconds), merge_started_at: iso(now) };
  const w = writeLock('replace', lockManifest(data, held.rv), env || held.env);
  if (!w.ok || !w.rv) return { ok: false, why: `the change lock could not be moved to phase merging (${w.ok ? 'no resourceVersion came back' : w.detail}); not merging` };
  held.rv = w.rv; held.data = data;
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

// ── the settle gate (story 551, R6; Tom's ruling Q-548-04: facts, not clocks) ──────────
// Story: https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/551
// No actor needs another actor's schedule: every check reads the state at the moment the
// lane acts. For a candidate with target T (its Flux scope as the baseline gate has it:
// L.kustomizationsFor + L.fluxScope, i.e. its Kustomizations, their dependsOn chain and
// their namespaces) and the cluster-wide parts (cluster_wide_targets in upgrade-rules.json):
//   F1  nothing unapplied on main touches T: between Flux's source revision and main's head
//       no commit changes T's paths, a path of its chain, or a cluster-wide part;
//   F2  T has applied the source's revision and is settled: each Kustomization of its chain
//       has applied (not only attempted) the revision, is Ready at its generation and not
//       reconciling; the HelmReleases of its namespaces are settled;
//   F3  no Flux object of a cluster-wide part (nor the source) is mid-change.
// An object in Flux's retry loop, or waiting on a failing dependency, is failing, not
// settling: the baseline gate judges it by scope. F4 (no shepherd Job that can merge is
// running) is merge-time only, below. Every read fails closed and names what failed.
const NAME = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/;
export function settleConfig(rules) {
  const c = rules?.night?.settle_gate || {};
  const s = c.flux_source || {};
  const ok = s.kind === 'GitRepository' && NAME.test(s.namespace || '') && NAME.test(s.name || '')
    && Number.isInteger(c.max_unapplied_commits) && c.max_unapplied_commits >= 1 && c.max_unapplied_commits <= 100;
  return { ok, source: { namespace: s.namespace, name: s.name }, max_unapplied_commits: c.max_unapplied_commits,
    why: ok ? null : 'night.settle_gate in upgrade-rules.json is invalid (flux_source a GitRepository with a namespace and a name; max_unapplied_commits an integer from 1 to 100)' };
}
// Flux writes a GitRepository revision as <branch>@sha1:<40 hex>.
export const revisionSha = rev => { const m = /^[^@\s]+@sha1:([0-9a-f]{40})$/.exec(String(rev || '')); return m ? m[1] : null; };
// A commit's time as GitHub stamps a squash merge: its committer date. NaN when unreadable.
export const commitTime = c => Date.parse(c?.commit?.committer?.date || '');
const subjectOf = c => String(c?.commit?.message || '').split('\n')[0].slice(0, 100);
const dirOf = k => String(k?.spec?.path || '').replace(/^\.\//, '').replace(/\/$/, '');
const under = (p, d) => !!d && (p === d || p.startsWith(d + '/'));

// The cluster-wide parts and their Flux objects: the Kustomizations whose spec.path falls
// under a part's globs, and the HelmReleases those Kustomizations applied (Flux labels).
export function clusterWideParts(rules, ks, hr) {
  return (rules.cluster_wide_targets || []).map(t => {
    const mine = (ks?.items || []).filter(k => dirOf(k) && t.globs.some(g => L.matchGlob(`${dirOf(k)}/x`, g)));
    const ids = new Set(mine.map(L.ksId));
    const hrs = (hr?.items || []).filter(h => { const l = h.metadata?.labels || {}; return ids.has(`${l['kustomize.toolkit.fluxcd.io/namespace'] || h.metadata?.namespace}/${l['kustomize.toolkit.fluxcd.io/name']}`); });
    return { id: t.id, globs: t.globs, ks: [...ids], hr: hrs.map(h => `${h.metadata.namespace}/${h.metadata.name}`) };
  });
}

// The settle scope of a lane-A record: null when it has no immediate cluster effect (it
// changes nothing in the cluster now), { unreadable } when the Flux objects cannot be read.
export function settleScope(rec, ks, hr) {
  if (rec?.cluster_effect !== 'immediate') return null;
  if (!ks || !hr) return { unreadable: 'kustomizations or helmreleases (the target scope)' };
  const targets = [...new Set(rec.targets || [])];
  const mine = targets.flatMap(t => L.kustomizationsFor(t, ks));
  const sc = L.fluxScope(mine, ks, hr);
  const byId = new Map((ks.items || []).map(k => [L.ksId(k), k]));
  // the Kustomizations that apply T's own (their Flux labels), up to the root: 183 of 186
  // are applied by flux-system/cluster-apps, which is in no chain (story 551, S1)
  const appliers = [], missing = sc.chain.filter(id => !byId.has(id));
  const queue = mine.map(L.ksId);
  for (let id = queue.shift(); id !== undefined; id = queue.shift()) {
    const l = byId.get(id)?.metadata?.labels || {};
    if (!l['kustomize.toolkit.fluxcd.io/name']) continue;
    const up = `${l['kustomize.toolkit.fluxcd.io/namespace'] || byId.get(id).metadata.namespace}/${l['kustomize.toolkit.fluxcd.io/name']}`;
    if (appliers.includes(up) || up === id) continue;
    appliers.push(up);
    if (!byId.has(up)) missing.push(up); else queue.push(up);
  }
  const ownIds = new Set(mine.map(L.ksId));
  const ownHr = (hr.items || []).filter(h => { const l = h.metadata?.labels || {}; return ownIds.has(`${l['kustomize.toolkit.fluxcd.io/namespace'] || h.metadata?.namespace}/${l['kustomize.toolkit.fluxcd.io/name']}`); }).map(h => `${h.metadata.namespace}/${h.metadata.name}`);
  return { targets, own: [...ownIds], own_hr: ownHr, chain: sc.chain, appliers, namespaces: sc.namespaces,
    chain_paths: sc.chain.map(id => ({ id, path: dirOf(byId.get(id)) })).filter(x => x.path), missing: [...new Set(missing)] };
}

// Which parts of a scope one changed path reaches: T's paths, its chain, a cluster-wide
// part, directly or through a shared component that includes it (R8, componentReach).
export function scopeHits(path, scope, rules) {
  const hits = [];
  // a file directly under kubernetes/main/apps/<dir>/ (namespace.yaml, the namespace's
  // kustomization.yaml listing its apps) reaches every target and chain path in <dir>
  // (story 551, S1); a file directly under kubernetes/main/apps/ is the flux part's
  // (cluster_wide_targets), as is kubernetes/main/flux/apps.yaml, which defines cluster-apps
  const nsFile = /^(kubernetes\/main\/apps\/[^/]+)\/[^/]+$/.exec(path);
  if (nsFile) {
    for (const t of scope?.targets || []) if (under(t, nsFile[1])) hits.push({ path, part: 'namespace', name: `${nsFile[1]} (for target ${t})` });
    for (const c of scope?.chain_paths || []) if (under(c.path, nsFile[1])) hits.push({ path, part: 'namespace', name: `${nsFile[1]} (for chain ${c.id})` });
  }
  const reach = [path, ...L.componentReach([path], rules).via.map(v => v.consumer)];
  for (const p of reach) {
    const via = p === path ? '' : ` (via ${p})`;
    const isDir = p !== path;
    const touches = d => under(p, d) || (isDir && under(d, p));
    for (const t of scope?.targets || []) if (touches(t)) hits.push({ path, part: 'target', name: t + via });
    for (const c of scope?.chain_paths || []) if (touches(c.path)) hits.push({ path, part: 'chain', name: c.id + via });
  }
  for (const t of rules.cluster_wide_targets || []) if (L.clusterWide([path, ...reach.slice(1).map(c => `${c}/kustomization.yaml`)], { cluster_wide_targets: [t] }).length) hits.push({ path, part: 'cluster-wide', name: t.id });
  return hits;
}

// READ-ONLY: the commits from base (exclusive) to head (inclusive), each with its files.
// One compare, then one commit read per commit. complete false (and why) when the range
// cannot be read in full: a failed read, more than max commits, a diverged base, a commit
// with 300 or more files (GitHub's first page).
export function readCommitRange(repo, base, head, run, max = 50) {
  const out = { base, head, status: null, commits: [], complete: false, why: null };
  if (!SHA.test(base || '') || !/^([0-9a-f]{40}|main)$/.test(head || '')) { out.why = `unreadable: the range ${short(base)}...${head}`; return out; }
  const cmp = parse(tryRun(run, 'gh', ['api', `repos/${repo}/compare/${base}...${head}?per_page=100`]));
  if (!cmp || typeof cmp !== 'object' || !Number.isInteger(cmp.ahead_by)) { out.why = `unreadable: compare ${short(base)}...${head}`; return out; }
  out.status = cmp.status || null;
  if (cmp.behind_by > 0 || !['identical', 'ahead'].includes(cmp.status)) { out.why = `compare ${short(base)}...${head} reads ${cmp.status} (behind by ${cmp.behind_by}): the base is not on the head's history`; return out; }
  const list = Array.isArray(cmp.commits) ? cmp.commits : null;
  if (!list) { out.why = `unreadable: the commits of compare ${short(base)}...${head}`; return out; }
  if (cmp.ahead_by > max || (Number.isInteger(cmp.total_commits) && cmp.total_commits !== list.length) || list.length !== cmp.ahead_by) { out.why = `${cmp.ahead_by} commits in ${short(base)}...${head}, more than one read can attribute (limit ${max}, listed ${list.length})`; return out; }
  for (const c of list) {
    const full = parse(tryRun(run, 'gh', ['api', `repos/${repo}/commits/${c.sha}`]));
    const files = Array.isArray(full?.files) ? full.files : null;
    if (!SHA.test(c?.sha || '') || !files) { out.why = `unreadable: the files of commit ${short(c?.sha)}`; return out; }
    if (files.length >= 300) { out.why = `commit ${short(c.sha)} lists ${files.length} files, GitHub's first page: cannot tell which it changed`; return out; }
    out.commits.push({ sha: c.sha, parents: (c.parents || []).map(p => p.sha), at: Number.isFinite(commitTime(c)) ? iso(commitTime(c)) : null, subject: subjectOf(c),
      author: c.author?.login ?? null, committer: c.committer?.login ?? null, pr: (/\(#(\d+)\)\s*$/.exec(subjectOf(c)) || [])[1] ? Number(/\(#(\d+)\)\s*$/.exec(subjectOf(c))[1]) : null,
      files: [...new Set(files.flatMap(f => [f.filename, f.previous_filename].filter(Boolean)))] });
  }
  out.complete = true;
  return out;
}

// PURE, for the verifier (stories 553 and 753): the other changes on main between a range's
// base and head that reach the scope (T's paths, its chain, a cluster-wide part). exclude =
// SHAs that are not "other" (the verifier's own merge). Fails closed: an incomplete range
// gives complete false and no claim that nothing else changed.
export function otherChanges(range, scope, rules, { exclude = [] } = {}) {
  if (!range?.complete) return { complete: false, why: range?.why || 'unreadable: the commit range', changes: [] };
  const changes = [];
  for (const c of range.commits) {
    if (exclude.includes(c.sha)) continue;
    const matched = c.files.flatMap(p => scopeHits(p, scope, rules));
    if (matched.length) changes.push({ sha: c.sha, at: c.at, subject: c.subject, pr: c.pr, author: c.author, committer: c.committer, matched });
  }
  return { complete: true, why: null, base: range.base, head: range.head, changes };
}

// PURE. One Flux object: { state: 'settled' | 'progressing' | 'failing', why }, from its
// conditions and generations only (no clock). ctx.revision = the source revision, for a
// Kustomization of that source; ctx.dep(id) = the state of a dependency. 'failing' is
// broken, not settling (the baseline gate judges it by scope): Flux's retry loop, Stalled,
// a failure at the current revision and generation, or waiting on a failing dependency.
export function fluxSettleState(o, ctx = {}) {
  if (o?.spec?.suspend === true) return { state: 'settled', suspended: true, why: 'suspended' };
  const cs = o?.status?.conditions;
  if (!Array.isArray(cs)) return { state: 'progressing', why: 'no status conditions' };
  const ready = cs.find(c => c?.type === 'Ready');
  if (!ready) return { state: 'progressing', why: 'no Ready condition yet' };
  const rec = cs.find(c => c?.type === 'Reconciling' && c.status === 'True') || null;
  const stalled = cs.some(c => c?.type === 'Stalled' && c.status === 'True');
  const gen = o?.metadata?.generation, og = o?.status?.observedGeneration;
  const gens = Number.isInteger(gen) && Number.isInteger(og);
  if (ready.status === 'Unknown') return { state: 'progressing', why: `Ready Unknown (${ready.reason || 'no reason'})` };
  if (ready.status === 'True') {
    if (rec) return { state: 'progressing', why: `Reconciling (${rec.reason || 'no reason'})` };
    if (!gens) return { state: 'progressing', why: `generation unreadable (generation ${gen ?? 'none'}, observed ${og ?? 'none'})` };
    if (og < gen) return { state: 'progressing', why: `generation ${gen} not observed yet (observed ${og})` };
    return { state: 'settled', why: null };
  }
  if (ready.status === 'False') {
    const r = ready.reason || 'no reason';
    // the retry loop rewrites lastTransitionTime on every retry (dev-env-h7-metadata-audit,
    // 2026-11-11): a failure, not a change in flight
    if (rec?.reason === 'ProgressingWithRetry') return { state: 'failing', why: `NotReady (${r}), in Flux's retry loop` };
    if (rec) return { state: 'progressing', why: `NotReady and Reconciling (${rec.reason || 'no reason'})` };
    if (stalled) return { state: 'failing', why: `NotReady (${r}), Stalled` };
    if (r === 'DependencyNotReady' && typeof ctx.dep === 'function') {
      const deps = (o?.spec?.dependsOn || []).map(d => `${d.namespace || o.metadata?.namespace}/${d.name}`);
      const st = deps.map(id => ({ id, s: ctx.dep(id) }));
      const bad = st.find(x => x.s?.state === 'failing');
      if (bad) return { state: 'failing', why: `NotReady (DependencyNotReady), waiting on ${bad.id}, which is failing` };
      // stuck, not mid-change: nothing moves until someone resumes the dependency (S2)
      const off = st.find(x => x.s?.suspended);
      if (off) return { state: 'failing', why: `NotReady (DependencyNotReady), waiting on ${off.id}, which is suspended` };
      const gone = st.find(x => !x.s);
      return { state: 'progressing', why: gone ? `NotReady (DependencyNotReady), waiting on ${gone.id}, which does not exist` : `NotReady (DependencyNotReady), waiting on ${st.filter(x => x.s.state !== 'settled').map(x => x.id).join(', ') || 'a dependency that just settled'}` };
    }
    if (!gens) return { state: 'progressing', why: `NotReady (${r}), generation unreadable` };
    if (og < gen) return { state: 'progressing', why: `NotReady (${r}), generation ${gen} not observed yet` };
    if (ctx.revision && o?.status?.lastAttemptedRevision !== ctx.revision) return { state: 'progressing', why: `NotReady (${r}), has not attempted ${short(revisionSha(ctx.revision))} yet` };
    return { state: 'failing', why: `NotReady (${r}) at its current generation${ctx.revision ? ' and revision' : ''}` };
  }
  return { state: 'progressing', why: `Ready ${ready.status ?? 'without a status'}` };
}

// The state of every Kustomization (with its dependencies resolved, cycle-safe) and
// HelmRelease, and of the source.
export function fluxStates(f) {
  const cfg = f?.cfg || {};
  const rev = f?.source?.status?.artifact?.revision || null;
  const byId = new Map((f?.ks?.items || []).map(k => [L.ksId(k), k]));
  const memo = new Map();
  const ofSource = k => { const r = k?.spec?.sourceRef || {}; return r.kind === 'GitRepository' && r.name === cfg.source?.name && (r.namespace || k.metadata?.namespace) === cfg.source?.namespace; };
  const ksState = (id, seen = new Set()) => {
    if (memo.has(id)) return memo.get(id);
    const k = byId.get(id);
    if (!k) return null;
    if (seen.has(id)) return { state: 'progressing', why: 'a dependsOn cycle' };
    seen.add(id);
    const s = fluxSettleState(k, { revision: ofSource(k) ? rev : null, dep: d => ksState(d, seen) });
    memo.set(id, s);
    return s;
  };
  for (const id of byId.keys()) ksState(id);
  const hrState = new Map((f?.hr?.items || []).map(h => [`${h.metadata.namespace}/${h.metadata.name}`, fluxSettleState(h)]));
  return { rev, byId, ofSource, ksState: id => ksState(id), hrState, source: f?.source ? fluxSettleState(f.source) : null };
}

// READ-ONLY (gh api GET and kubectl get through `run`), once per tick: the source, every
// Kustomization and HelmRelease, and main's commits the source has not fetched yet.
// undefined = the read failed; source null = the GitRepository does not exist.
export function readSettleFacts(repo, rules, run) {
  const cfg = settleConfig(rules);
  if (!cfg.ok) return { cfg };
  const kList = args => { const j = parse(tryRun(run, 'kubectl', ['get', ...args, '-o', 'json'])); return Array.isArray(j?.items) ? j : undefined; };
  const ks = kList(['kustomizations.kustomize.toolkit.fluxcd.io', '-A']);
  const hr = kList(['helmreleases.helm.toolkit.fluxcd.io', '-A']);
  const sl = kList(['gitrepositories.source.toolkit.fluxcd.io', '-n', cfg.source.namespace, '--field-selector', `metadata.name=${cfg.source.name}`]);
  const source = sl ? (sl.items.find(g => g?.metadata?.name === cfg.source.name && g?.metadata?.namespace === cfg.source.namespace) || null) : undefined;
  const srcSha = revisionSha(source?.status?.artifact?.revision);
  const range = srcSha ? readCommitRange(repo, srcSha, 'main', run, cfg.max_unapplied_commits) : null;
  return { cfg, ks, hr, source, range };
}

// PURE: the verdict for one target scope on facts from readSettleFacts.
export function settleVerdict(f, scope, rules) {
  const cfg = f?.cfg || { ok: false, why: 'unreadable: the settle gate config' };
  const out = { applicable: true, settled: false, blockers: [], source_revision: null, unapplied: [], unsettled: [], cluster_wide_unsettled: [], failing_not_settling: [] };
  const add = b => { if (!out.blockers.includes(b)) out.blockers.push(b); };
  if (scope === null) return { ...out, applicable: false, settled: true, why: 'no immediate cluster effect: nothing in the cluster changes now' };
  if (!cfg.ok) { add(cfg.why); return out; }
  if (!scope) { add('unreadable: the target scope'); return out; }
  if (scope.unreadable) { add(`unreadable: ${scope.unreadable}`); return out; }
  const src = `${cfg.source.namespace}/${cfg.source.name}`;
  if (!f.ks) add('unreadable: kustomizations (settle gate)');
  if (!f.hr) add('unreadable: helmreleases (settle gate)');
  if (f.source === undefined) add(`unreadable: GitRepository ${src}`);
  else if (f.source === null) add(`GitRepository ${src} does not exist`);
  const rev = f.source?.status?.artifact?.revision || null;
  out.source_revision = rev;
  if (f.source && !revisionSha(rev)) add(`unreadable: the artifact revision of GitRepository ${src} (${JSON.stringify(rev)})`);
  // F1: nothing unapplied on main touches T, its chain or a cluster-wide part
  if (revisionSha(rev)) {
    const oc = otherChanges(f.range, scope, rules);
    if (!oc.complete) add(`F1 unreadable: what main has that Flux has not fetched (${oc.why})`);
    else {
      out.unapplied = oc.changes.map(c => ({ sha: c.sha, pr: c.pr, subject: c.subject, parts: [...new Set(c.matched.map(m => `${m.part} ${m.name}`))] }));
      for (const c of out.unapplied) add(`F1 main has a change Flux has not applied that reaches ${c.parts.join(', ')}: ${short(c.sha)} ${c.subject}`);
    }
  }
  if (!f.ks || !f.hr || !f.source) { out.settled = false; return out; }
  const st = fluxStates(f);
  // F2: T's chain has applied the source revision and is settled; T's namespaces' HelmReleases settled
  for (const id of scope.missing || []) add(`F2 Kustomization ${id} in the target's dependsOn chain or applying it does not exist`);
  // S2: a suspended object of T's own scope, its chain or its appliers: a merge would not apply
  for (const id of [...new Set([...(scope.chain || []), ...(scope.appliers || [])])]) if (st.byId.get(id)?.spec?.suspend === true) add(`F2 Kustomization ${id} is suspended: a lane merge to the target would not apply until someone resumes it`);
  for (const id of scope.own_hr || []) if ((f.hr.items || []).find(h => `${h.metadata.namespace}/${h.metadata.name}` === id)?.spec?.suspend === true) add(`F2 HelmRelease ${id} is suspended: a lane merge to the target would not apply until someone resumes it`);
  for (const id of [...new Set([...(scope.chain || []), ...(scope.appliers || [])])]) {
    const k = st.byId.get(id); if (!k || k.spec?.suspend === true) continue;
    const s = st.ksState(id);
    const applier = (scope.appliers || []).includes(id) && !(scope.chain || []).includes(id);
    // a failing applier has not applied the revision, and no baseline gate watches it (S1)
    if (s.state === 'failing') { if (applier) out.unsettled.push(`Kustomization ${id} (applies the target): ${s.why}`); else out.failing_not_settling.push(`Kustomization ${id}: ${s.why}`); continue; }
    if (s.state === 'progressing') { out.unsettled.push(`Kustomization ${id}${applier ? ' (applies the target)' : ''}: ${s.why}`); continue; }
    if (st.ofSource(k) && k.status?.lastAppliedRevision !== rev) out.unsettled.push(`Kustomization ${id}${applier ? ' (applies the target)' : ''}: has not applied ${short(revisionSha(rev))} (applied ${short(revisionSha(k.status?.lastAppliedRevision) || k.status?.lastAppliedRevision || 'nothing')})`);
  }
  for (const [id, s] of st.hrState) {
    if (!(scope.namespaces || []).includes(id.split('/')[0])) continue;
    if (s.state === 'failing') out.failing_not_settling.push(`HelmRelease ${id}: ${s.why}`);
    else if (s.state === 'progressing') out.unsettled.push(`HelmRelease ${id}: ${s.why}`);
  }
  if (out.unsettled.length) add(`F2 the target is not settled: ${out.unsettled.slice(0, 4).join('; ')}${out.unsettled.length > 4 ? ` (+${out.unsettled.length - 4} more)` : ''}`);
  // F3: no cluster-wide part (nor the source) mid-change
  if (st.source?.state === 'progressing') out.cluster_wide_unsettled.push(`flux: GitRepository ${src}: ${st.source.why}`);
  if (st.source?.state === 'failing') add(`F3 GitRepository ${src} is ${st.source.why}; nothing it serves can be judged settled`);
  for (const part of clusterWideParts(rules, f.ks, f.hr)) {
    for (const id of part.ks) {
      const k = st.byId.get(id), s = st.ksState(id);
      if (s.state === 'progressing') out.cluster_wide_unsettled.push(`${part.id}: Kustomization ${id}: ${s.why}`);
      else if (s.state === 'settled' && k.spec?.suspend !== true && st.ofSource(k) && k.status?.lastAppliedRevision !== rev) out.cluster_wide_unsettled.push(`${part.id}: Kustomization ${id}: has not applied ${short(revisionSha(rev))}`);
      else if (s.state === 'failing') out.failing_not_settling.push(`${part.id}: Kustomization ${id}: ${s.why}`);
    }
    for (const id of part.hr) {
      const s = st.hrState.get(id);
      if (s?.state === 'progressing') out.cluster_wide_unsettled.push(`${part.id}: HelmRelease ${id}: ${s.why}`);
      else if (s?.state === 'failing') out.failing_not_settling.push(`${part.id}: HelmRelease ${id}: ${s.why}`);
    }
  }
  if (out.cluster_wide_unsettled.length) add(`F3 a cluster-wide part is mid-change: ${out.cluster_wide_unsettled.slice(0, 4).join('; ')}${out.cluster_wide_unsettled.length > 4 ? ` (+${out.cluster_wide_unsettled.length - 4} more)` : ''}`);
  out.unsettled = out.unsettled.slice(0, 20);
  out.failing_not_settling = [...new Set(out.failing_not_settling)].slice(0, 20);
  out.settled = out.blockers.length === 0;
  return out;
}
// The settle gate for one scope, read now (or on facts the caller already read).
export const settleCheck = ({ repo = REPO, rules, run, scope, facts }) => settleVerdict(facts || readSettleFacts(repo, rules, run), scope, rules);
// One log line, and what the state keeps.
export const settleLine = s => (!s.applicable ? `not applicable (${s.why})`
  : s.settled ? `settled (Flux at ${short(revisionSha(s.source_revision))}; nothing unapplied reaches the target; target and cluster-wide parts settled${s.failing_not_settling.length ? `; ${s.failing_not_settling.length} failing, not settling` : ''})`
    : `NOT settled: ${s.blockers.join(' | ')}`);
export const settleBrief = s => ({ applicable: s.applicable, settled: s.settled, source_revision: s.source_revision, blockers: s.blockers,
  unapplied: s.unapplied, unsettled: (s.unsettled || []).slice(0, 10), cluster_wide_unsettled: (s.cluster_wide_unsettled || []).slice(0, 10), failing_not_settling: (s.failing_not_settling || []).slice(0, 10), ...(s.why ? { why: s.why } : {}) });

// ── F4: no lane merge while a shepherd Job that can merge is running (story 551, R1) ─────
// The two-sided handshake on facts. The shepherd's Job exists before run-shepherd.sh reads
// the lock (and triage claims a remediation in upgrade-remediation-state before it starts
// run-shepherd.sh in remediate mode); the lane writes phase merging before it reads the
// Jobs. So at least one side sees the other. Jobs that can run `gh pr merge`, unfinished
// (no Complete or Failed condition): every Job of CronJob upgrade-shepherd (or carrying
// its controller label: a summon made from it), and a Job of upgrade-shepherd-triage while
// upgrade-remediation-state holds a claimed, unfinished remediation (attempted 1, result
// none). The lane's own Jobs and the other upgrade-agent CronJobs cannot merge: the
// alert-responder's LLM has read-only Bash (kubectl get/describe, flux get, grep, cat, git
// log/show and its query scripts, no gh), the health gate and dev-env-ops-curation-order. Any other
// unfinished Job is unknown and counts as one that can (fail closed).
export const SHEPHERD_JOBS = Object.freeze({
  namespace: 'upgrade-agent',
  merging: Object.freeze([{ cronjob: 'upgrade-shepherd', controller: 'upgrade-shepherd' }]),
  remediating: Object.freeze([{ cronjob: 'upgrade-shepherd-triage', controller: 'triage' }]),
  never: Object.freeze([{ cronjob: 'upgrade-shepherd-upgrade-night', controller: 'upgrade-night' }, { cronjob: 'alert-responder', controller: 'alert-responder' }, { cronjob: 'upgrade-health-gate', controller: 'upgrade-health-gate' }, { cronjob: 'dev-env-ops-curation-order', controller: 'curation-order' }]),
  remediation_state: 'upgrade-remediation-state',
});
const jobFinished = j => (j?.status?.conditions || []).some(c => ['Complete', 'Failed'].includes(c?.type) && c.status === 'True') || !!j?.status?.completionTime;
const jobIs = (j, list) => { const o = (j?.metadata?.ownerReferences || []).find(r => r.kind === 'CronJob')?.name; const c = j?.metadata?.labels?.['app.kubernetes.io/controller']; return list.some(x => x.cronjob === o || (o === undefined && c && x.controller === c)); };
// PURE. jobs = kubectl get jobs -n upgrade-agent (null = unreadable); remediation = the
// remediation-state read (readNamedConfigMap result; null = unreadable).
// Known limit (story 551, S5): any claim with attempted 1 and result none counts, not only
// the active triage Job's. The claim carries no time or Job identity to tell a stale one
// (a crashed remediate) from a live one, so this stays fail closed; it holds only the
// ticks that overlap a triage Job, and clears when triage records a result or the entry
// ages out (STATE_TTL_HOURS after its first_seen).
export function mergingJobs(jobs, remediation) {
  if (!Array.isArray(jobs?.items)) return { readable: false, active: [], why: 'unreadable: Jobs in upgrade-agent (F4)' };
  const claimed = !remediation ? null : Object.entries(remediation.cm?.data || {}).filter(([, v]) => { try { const e = JSON.parse(v); return String(e.attempted) === '1' && (e.result ?? 'none') === 'none'; } catch { return false; } }).map(([k]) => k);
  const active = [];
  for (const j of jobs.items.filter(x => !jobFinished(x))) {
    const name = j?.metadata?.name || '(unnamed)';
    if (jobIs(j, SHEPHERD_JOBS.merging)) active.push(`${name} (the shepherd)`);
    else if (jobIs(j, SHEPHERD_JOBS.remediating)) {
      if (claimed === null) active.push(`${name} (triage; upgrade-remediation-state unreadable, so it may be running remediate)`);
      else if (claimed.length) active.push(`${name} (triage running remediate for ${claimed.slice(0, 2).join(', ')})`);
    } else if (!jobIs(j, SHEPHERD_JOBS.never)) active.push(`${name} (an unknown Job in upgrade-agent; it may merge)`);
  }
  return { readable: true, active, why: active.length ? `a Job that can merge is running: ${active.join('; ')}` : null };
}
export function readMergingJobs(run) {
  const j = parse(tryRun(run, 'kubectl', ['get', 'jobs', '-n', SHEPHERD_JOBS.namespace, '-o', 'json']));
  return mergingJobs(Array.isArray(j?.items) ? j : null, readNamedConfigMap(SHEPHERD_JOBS.remediation_state, run));
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

// ── PR records and the stalled-check retry (story 549) ─────────────────────────────
// Story: https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/549
// Design: OP512 comment 6193 (rollout step 3; "Retry of a stalled auto-merge"). Facts, not
// clocks (Tom's ruling Q-548-04): every decision reads GitHub at the moment it writes.
//
// THE RECORD. One comment per open Renovate PR, written by the lane's own identity
// (rules.night.pr_record.author: REST user.login haynes-ops-bot[bot], type Bot) and edited
// in place. Its body starts with the marker line, then one fenced JSON block: the tick's
// record projected to rules.night.pr_record.fields, with < > & @ and ` escaped as \u
// sequences (no HTML comment, mention or fence can appear in it). A comment by any other
// author is never read as the record and never edited. The lane writes only when the
// content changed, leaving out updated and last_attempt.at. With two or more of its own it
// keeps the oldest and edits each newer one into a note without the marker.
//
// THE RETRY. Renovate's rebase/retry box is ticked once per head, at most per_pr times per
// PR in window_days and per_tick times per tick, on a PR that a failing check alone keeps
// out of lane A, after a fresh read shows: the newest run of the failing check failed and
// nothing on the head is queued or running; the box exists once and is unticked; the PR is
// Renovate's and every commit is the Renovate app's; the head is the one the tick judged.
// The ledger entry is written first (data.retry_ledger), then the pause and the PR are read
// again: a pause, a moved head or a changed body abandons the write for this tick. The edit
// changes the box's one character and nothing else (ghWrite refuses anything else).
//
// THE GITHUB WRITES. ghWrite() is their one spawn site: gh api --method POST of a new
// comment on a PR, --method PATCH of one comment by id, --method PATCH of a PR's body,
// each with { body } on stdin and nothing else. They run only from the tick's PR-write
// phase (--write-state, a token with pull_requests:write, not paused, not halted) and are
// never a merge. The first write that fails stops every other GitHub write of that tick.
export function recordConfig(rules) {
  const c = rules?.night?.pr_record || {};
  const ok = c.marker === L.RECORD_MARKER && typeof c.author?.login === 'string' && /^[a-z0-9-]+\[bot\]$/.test(c.author.login) && c.author.type === 'Bot'
    && Array.isArray(c.fields) && ['schema', 'pr', 'head', 'preapproval', 'retry', 'updated'].every(f => c.fields.includes(f))
    && Number.isInteger(c.max_body_bytes) && c.max_body_bytes >= 4096 && c.max_body_bytes <= 65000;
  return { ok, marker: c.marker, author: c.author || {}, fields: Array.isArray(c.fields) ? c.fields : [], max_body_bytes: c.max_body_bytes,
    why: ok ? null : `night.pr_record in upgrade-rules.json is invalid (marker ${L.RECORD_MARKER}; author a <app>[bot] login of type Bot; fields with schema, pr, head, preapproval, retry, updated; 4096 <= max_body_bytes <= 65000)` };
}
export function retryConfig(rules) {
  const c = rules?.night?.retry || {};
  const int = (v, lo, hi) => Number.isInteger(v) && v >= lo && v <= hi;
  const ok = c.per_head === 1 && int(c.per_pr, 1, 5) && int(c.window_days, 1, 30) && int(c.per_tick, 0, 3) && int(c.max_preflights_per_tick, 1, 10) && c.max_preflights_per_tick >= c.per_tick;
  return { ok, per_head: c.per_head, per_pr: c.per_pr, window_days: c.window_days, per_tick: c.per_tick, max_preflights_per_tick: c.max_preflights_per_tick,
    why: ok ? null : 'night.retry in upgrade-rules.json is invalid (per_head 1; per_pr 1 to 5; window_days 1 to 30; per_tick 0 to 3; max_preflights_per_tick 1 to 10 and at least per_tick)' };
}
// The PR writes need pull_requests:write (GitHub's permission tables for installation
// tokens list all three endpoints under "Pull requests", write). Unreadable = no write.
export function prWriteAllowed(token) {
  if (!token?.readable) return { ok: false, why: 'unreadable: the token permissions (/creds/gh_token_permissions); no PR write' };
  if (!(token.names || []).includes('pull_requests:write')) return { ok: false, why: `the token cannot write pull requests (${(token.names || []).join(', ') || 'no permissions'})` };
  return { ok: true, why: null };
}

// ── the record (PURE) ──
const canon = v => (Array.isArray(v) ? v.map(canon) : v && typeof v === 'object' ? Object.fromEntries(Object.keys(v).sort().map(k => [k, canon(v[k])])) : v);
export function projectRecord(rec, cfg) {
  const out = {};
  for (const f of cfg.fields) out[f] = rec?.[f] === undefined ? null : rec[f];
  return out;
}
// The content the lane compares: everything but the tick's own time. A null terminal (story
// 593: an open PR) compares equal to a record written before the field existed.
export function recordContentKey(pub) {
  const x = structuredClone(pub ?? {});
  delete x.updated;
  if (x.terminal === null || x.terminal === undefined) delete x.terminal;
  if (x.last_attempt && typeof x.last_attempt === 'object') delete x.last_attempt.at;
  return JSON.stringify(canon(x));
}
const escJson = s => s.replace(/[<>&@`]/g, c => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`);
const word = s => String(s ?? '').replace(/[^A-Za-z0-9._-]/g, '') || 'unknown';
export function renderRecord(pub, cfg) {
  return [cfg.marker,
    `The nightly upgrade lane keeps this record and edits it in place: lane \`${word(pub.lane)}\`, class \`${word(pub.class)}\`, head \`${word(String(pub.head ?? '').slice(0, 8))}\`. Runbook: \`.agents/runbooks/upgrade-night-lane.md\`.`,
    '', '```json', escJson(JSON.stringify(pub, null, 1)), '```', ''].join('\n');
}
export function parseRecord(body, cfg) {
  const b = String(body ?? '').replace(/\r\n/g, '\n');
  if (!b.startsWith(`${cfg.marker}\n`)) return null;
  const m = /\n```json\n([\s\S]*?)\n```(?:\n|$)/.exec(b);
  if (!m) return null;
  try { const j = JSON.parse(m[1]); return j && typeof j === 'object' && !Array.isArray(j) ? j : null; } catch { return null; }
}
// What must never reach a public comment. The record holds names, SHAs, times and the
// lane's own wording; any of these shapes means something else got in, and the record is
// refused (not written) with the reason.
const SECRET_SHAPES = Object.freeze([
  [/\bgh[pousr]_[A-Za-z0-9]{20,}/, 'a GitHub token'], [/\bgithub_pat_[A-Za-z0-9_]{20,}/, 'a GitHub token'],
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----/, 'a private key'], [/\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\./, 'a JSON web token'],
  [/\b[a-z][a-z0-9+.-]*:\/\/[^\s/:@]+:[^\s/@]+@/i, 'a URL with credentials'], [/\bbearer\s+[A-Za-z0-9._~+/-]{12,}/i, 'a bearer credential'],
  [/\b(?:password|passwd|secret|api[_-]?key|client[_-]?secret)\s*["']?\s*[:=]\s*\S/i, 'a credential assignment'],
  [/\bxox[abprs]-[A-Za-z0-9-]{10,}/, 'a Slack token'], [/\bAKIA[0-9A-Z]{16}\b/, 'an AWS key'],
  // F3 (story 549 review): the cluster's network is not public. Object, pod, commit and
  // backup target names may stay (they are in this public repository); addresses and
  // in-cluster DNS names may not.
  [/\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|127(?:\.\d{1,3}){3}|169\.254(?:\.\d{1,3}){2}|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])(?:\.\d{1,3}){2})\b/, 'a private network address'],
  [/\b(?:f[cd][0-9a-f]{2}|fe80):[0-9a-f]{0,4}:/i, 'a private network address'],
  [/[A-Za-z0-9-]\.svc\b|\.cluster\.local\b/i, 'an in-cluster name'],
]);
// F3/F4 (story 549 review): what a failed GitHub write publishes is a fixed vocabulary,
// never gh's or GitHub's own text (a network error's last line can name the cluster's
// resolver); the raw line goes to the Job log only. A denial (401, or a 403 that is not a
// rate limit) halts the PR writes until a person clears it; a rate limit, a 5xx, a timeout
// or a network error stops only that tick's writes.
export function writeFailure(w) {
  const raw = String(w?.detail || ''), h = Number.isInteger(w?.http) ? w.http : null;
  const kind = (w?.exit === null && h === null) || /^no exit status/.test(raw) ? 'timeout'
    : h === 429 || ((h === 403 || h === 401) && /rate limit/i.test(raw)) ? 'rate-limited'
      : h === 401 || h === 403 ? 'denied' : h === 404 ? 'not-found' : h === 409 || h === 422 ? 'rejected'
        : h !== null && h >= 500 ? 'server-error' : h !== null ? 'other-http' : 'network';
  const reason = { timeout: 'timed out or killed', 'rate-limited': `rate limited by GitHub (HTTP ${h})`, denied: `refused by GitHub (HTTP ${h})`, 'not-found': 'not found (HTTP 404)',
    rejected: `rejected by GitHub as invalid (HTTP ${h})`, 'server-error': `GitHub server error (HTTP ${h})`, 'other-http': `failed (HTTP ${h})`, network: 'network or client error (no HTTP status)' }[kind];
  return { kind, reason, halts: kind === 'denied' };
}
// The pause, as a public reason: never the pause's own text.
const pauseWhy = (p, when) => (!p ? `unreadable: data.pause on upgrade-lane-state (${when})` : p.pause ? `paused (data.pause on upgrade-lane-state) ${when}` : null);
export function publishProblems(pub, body, cfg) {
  const out = [];
  const raw = JSON.stringify(pub);
  for (const [re, what] of SECRET_SHAPES) if (re.test(raw) || re.test(body)) out.push(`the record carries text shaped like ${what}`);
  if (pub?.preapproval && Object.keys(pub.preapproval).length) out.push('preapproval is not empty (piece 1 reads no approval)');
  if (pub?.schema !== 'haynes.upgrade-lane/v1' || !Number.isInteger(pub?.pr)) out.push('not a haynes.upgrade-lane/v1 record of one PR');
  if (Buffer.byteLength(body) > cfg.max_body_bytes) out.push(`the record is ${Buffer.byteLength(body)} bytes, over ${cfg.max_body_bytes}`);
  return [...new Set(out)];
}
// comments = L.recordComments() output (REST identity). own = the lane's, oldest first.
export function ownRecords(comments, cfg) {
  const own = [], foreign = [];
  for (const c of comments || []) {
    if (!String(c?.body || '').startsWith(cfg.marker)) continue;
    if (c.author === cfg.author.login && c.author_type === cfg.author.type && Number.isInteger(c.id)) own.push(c);
    else foreign.push({ id: c.id ?? null, author: c.author ?? null });
  }
  own.sort((a, b) => a.id - b.id);
  return { own, foreign };
}
export function planRecord(pub, comments, cfg) {
  const { own, foreign } = ownRecords(comments, cfg);
  if (!own.length) return { action: 'create', id: null, demote: [], foreign };
  const cur = parseRecord(own[0].body, cfg);
  const same = !!cur && cur.pr === pub.pr && recordContentKey(cur) === recordContentKey(pub);
  return { action: same ? 'current' : 'update', id: own[0].id, demote: own.slice(1).map(c => c.id), foreign };
}
export const demoteBody = (repo, pr, keepId) => `This comment was a second night-lane record on this pull request, and the lane keeps one. The record is https://github.com/${repo}/pull/${pr}#issuecomment-${keepId}.`;

// ── the retry (PURE) ──
// The box ticked: that one character, nothing else. { ok, body } or { ok: false, why }.
export function tickRetryBox(body) {
  const b = String(body ?? '');
  const box = L.retryBox(b);
  if (box.state !== 'unticked') return { ok: false, why: `Renovate's retry box is ${box.state}` };
  const lines = b.split('\n');
  lines[box.line] = lines[box.line].replace(/^(\s*-\s+\[) \]/, '$1x]');
  const out = lines.join('\n');
  let diff = 0;
  for (let i = 0; i < b.length; i++) if (b[i] !== out[i]) diff++;
  if (out.length !== b.length || diff !== 1 || L.retryBox(out).state !== 'ticked') return { ok: false, why: 'the box could not be ticked by changing one character' };
  return { ok: true, body: out };
}
const counts = e => e && e.status !== 'abandoned';
// rec = the tick's record; box = facts.retry_box; entries = this PR's ledger entries (null
// = the ledger is unreadable); now = the tick's clock. Returns { field (the record's retry),
// eligible, block ({ class, reason } when a failure after a retry blocks the PR), update
// (fields to set on the newest ledger entry) }.
export function retryAssess({ rec, box, entries, cfg, now }) {
  const limits = { per_head: cfg.per_head, per_pr: cfg.per_pr, window_days: cfg.window_days, per_tick: cfg.per_tick };
  if (entries === null) return { field: { state: 'unreadable', why: 'unreadable: the retry ledger (data.retry_ledger on upgrade-lane-state); no retry', count_7d: null, limits, history: [] }, eligible: false, block: null, update: null };
  let counted = (entries || []).filter(counts).sort((a, b) => String(a.at).localeCompare(String(b.at)));
  // F6 (story 549 review): an intent the tick never resolved (the Job ended between the
  // ledger write and the next one) is decided from facts. The box ticked, or the head moved
  // since, means the write was made; the box unticked at the same head means it was not.
  // Anything else stays an intent, which counts (fail closed).
  let resolved = null;
  const open = counted[counted.length - 1];
  if (open && open.status === 'intent' && !open.outcome) {
    if (rec?.head !== open.head || box === 'ticked') resolved = { status: 'ticked', why: 'the tick ended before it recorded the write; the box ticked or the head moved since shows it was made' };
    else if (box === 'unticked') resolved = { status: 'abandoned', why: 'the tick ended before the write; the box unticked at the same head shows it was not made' };
    if (resolved) counted = counted.map(e => (e === open ? { ...e, ...resolved } : e)).filter(counts);
  }
  const inWin = counted.filter(e => now - Date.parse(e.at) < cfg.window_days * 864e5);
  const history = counted.map(e => ({ head: e.head, at: e.at, status: e.status, outcome: e.outcome ?? null, next_head: e.next_head ?? null }));
  const out = (state, why, extra = {}) => {
    const r = { field: { state, why, count_7d: inWin.length, limits, history }, eligible: false, block: null, update: null, ...extra };
    if (resolved) r.update = { ...resolved, ...(r.update || {}) };
    return r;
  };
  const failing = rec?.checks?.failing || [], pending = rec?.checks?.pending || [], missing = rec?.checks?.missing || [];
  const blockWith = reason => (rec?.lane === 'BLOCKED' && failing.length ? { class: 'check-failing-after-retry', reason } : null);
  const last = counted[counted.length - 1] || null;
  if (last && !last.outcome) {
    if (rec?.head === last.head) {
      if (last.status === 'ticked' && box === 'unticked') {
        const reason = `the retry on ${short(last.head)} at ${last.at} gave no new head: Renovate cleared the box${failing.length ? `; still failing: ${failing.join('; ')}` : ''}`;
        return out('no-new-head', reason, { block: blockWith(reason), update: { outcome: 'no-new-head', outcome_at: iso(now) } });
      }
      return last.status === 'ticked'
        ? out('waiting-for-renovate', `ticked on ${short(last.head)} at ${last.at}; the box reads ${box} and the head has not moved yet`)
        : out('not-retried-again', `a retry of ${short(last.head)} at ${last.at} ended ${last.status}${last.why ? ` (${last.why})` : ''}; this head is not retried again`);
    }
    if (failing.length) {
      const reason = `failed again on ${short(rec?.head)} after one retry on ${short(last.head)} at ${last.at}: ${failing.join('; ')}`;
      return out('failed-again', reason, { block: blockWith(reason), update: { outcome: 'failed-again', next_head: rec?.head ?? null, outcome_at: iso(now) } });
    }
    if (pending.length || missing.length) return out('new-head-checks-running', `the retry on ${short(last.head)} gave head ${short(rec?.head)}; waiting for its checks (${[...pending, ...missing].join('; ')})`);
    return out('resolved', `the retry on ${short(last.head)} at ${last.at} gave head ${short(rec?.head)}, and its expected checks passed`, { update: { outcome: 'resolved', next_head: rec?.head ?? null, outcome_at: iso(now) } });
  }
  if (last && failing.length && ((last.outcome === 'failed-again' && rec?.head === last.next_head) || (last.outcome === 'no-new-head' && rec?.head === last.head))) {
    const reason = last.outcome === 'failed-again' ? `failed again on ${short(rec.head)} after one retry on ${short(last.head)} at ${last.at}: ${failing.join('; ')}` : `the retry on ${short(last.head)} at ${last.at} gave no new head: Renovate cleared the box; still failing: ${failing.join('; ')}`;
    return out(last.outcome, reason, { block: blockWith(reason) });
  }
  if (!failing.length) return out('not-needed', 'no required or expected check is failing on this head');
  if (!(rec?.lane === 'BLOCKED' && rec?.class === 'check-failing')) return out('not-eligible', `lane ${rec?.lane} (${rec?.class}): only a PR that a failing check alone keeps out of lane A is retried`);
  if (pending.length) return out('not-eligible', `a check is still running on this head: ${pending.join('; ')}`);
  if (counted.some(e => e.head === rec.head)) return out('not-eligible', `already retried on this head (${short(rec.head)}); at most ${cfg.per_head} per head`);
  if (inWin.length >= cfg.per_pr) return out('budget-used', `${inWin.length} retries in the last ${cfg.window_days} days; at most ${cfg.per_pr} per PR`);
  if (box !== 'unticked') return out('not-eligible', `Renovate's retry box is ${box ?? 'unread'}`);
  return { ...out('eligible', `failing on ${short(rec.head)}: ${failing.join('; ')}; nothing running; the box unticked; ${inWin.length} of ${cfg.per_pr} retries used in ${cfg.window_days} days`), eligible: true };
}
// PURE. The facts at the moment of the write. pull = REST GET pulls/<n>; view = gh pr view
// (commits, headRefOid, statusCheckRollup); commits = REST pulls/<n>/commits.
// suites = REST GET commits/<head>/check-suites (F2): a workflow run that is queued,
// waiting, pending, requested or in progress has a github-actions check suite in that state
// before any of its check runs exist (read live 2026-11-12: head 6bd661d7 had 4 queued
// workflow runs, 4 queued github-actions suites and no check run). Other apps' suites
// (claude, cursor, renovate, cloudflare) stay queued on every head and are not judged.
export function retryPreflight({ pull, view, commits, suites, head, files, rules, now }) {
  if (!pull || typeof pull !== 'object') return ['unreadable: the pull request (REST)'];
  const out = [];
  if (pull.state !== 'open') out.push(`not open (state ${pull.state ?? 'unknown'})`);
  if (pull.draft !== false) out.push(pull.draft ? 'a draft' : 'unreadable: whether the PR is a draft');
  if (pull.base?.ref !== 'main') out.push(`base is ${pull.base?.ref ?? 'unknown'}, not main`);
  if (pull.user?.login !== RENOVATE.commit_login || pull.user?.type !== 'Bot' || pull.user?.id !== RENOVATE.commit_id) out.push(`opened by ${pull.user?.login ?? 'unknown'} (${pull.user?.type ?? '?'}/${pull.user?.id ?? '?'}), not the Renovate app`);
  if (pull.head?.repo?.full_name !== REPO) out.push(`head repository ${pull.head?.repo?.full_name ?? 'unknown'}, not ${REPO}`);
  if (!String(pull.head?.ref || '').startsWith(RENOVATE.branch_prefix)) out.push(`head branch ${pull.head?.ref ?? 'unknown'} is not under ${RENOVATE.branch_prefix}`);
  if (!SHA.test(head || '') || pull.head?.sha !== head) out.push(`head moved: ${short(pull.head?.sha)} is not the head the tick judged (${short(head)})`);
  if (!view || typeof view !== 'object') out.push('unreadable: the pull request (gh pr view)');
  else if (view.headRefOid !== pull.head?.sha) out.push('the two reads of the pull request disagree on its head');
  out.push(...commitRefusals(commits, view));
  const rollup = Array.isArray(view?.statusCheckRollup) ? view.statusCheckRollup : null;
  if (!rollup) out.push('unreadable: the checks on the head');
  else {
    if (rollup.length >= 100) out.push(`the check list may be truncated (${rollup.length} entries)`);
    const runs = [];
    for (const c of rollup) {
      if (c?.__typename === 'StatusContext') { const st = String(c.state || '').toUpperCase(); if (st === 'PENDING' || st === 'EXPECTED') out.push(`commit status ${c.context} is pending (${st})`); continue; }
      if (c?.__typename !== 'CheckRun') { out.push(`unreadable: a check entry of type ${c?.__typename ?? 'unknown'}`); continue; }
      if (c.status !== 'COMPLETED') out.push(`queued or running on the head: ${c.workflowName} / ${c.name} (${c.status || 'no status'})`);
      runs.push({ workflow: c.workflowName, name: c.name, status: c.status, conclusion: c.conclusion, completed_at: c.completedAt, started_at: c.startedAt });
    }
    if (!(files || []).length) out.push('unreadable: the PR files (for the expected checks)');
    else if (!L.evaluateChecks(L.expectedChecks(files, rules).expected, runs, now, rules.check_max_age_days).failing.length) out.push('the newest run of every required and expected check on the head did not fail');
  }
  if (!suites || !Array.isArray(suites.check_suites) || !Number.isInteger(suites.total_count)) out.push('unreadable: the check suites of the head (workflow runs that have not started a check)');
  else {
    if (suites.total_count > suites.check_suites.length) out.push(`the check suite list may be truncated (${suites.check_suites.length} of ${suites.total_count})`);
    for (const c of suites.check_suites) if (c?.app?.slug === 'github-actions' && c.status !== 'completed') out.push(`a workflow run for the head is ${c.status || 'in an unknown state'} (check suite ${c.id})`);
  }
  const box = L.retryBox(pull.body);
  if (box.state !== 'unticked') out.push(`Renovate's retry box is ${box.state}`);
  return [...new Set(out)];
}

// ── the retry ledger: data.retry_ledger on upgrade-lane-state (PURE) ──
// { schema, retries: { "<pr>": [{ head, at, status, checks, why?, outcome?, next_head?,
// outcome_at? }] }, halt: null | { at, why, comment } }. status: intent (written before the
// box is ticked; counted, since the write may have happened), ticked, write-failed
// (counted: GitHub may have applied it), abandoned (no write was made; not counted).
// Written by field manager upgrade-night-ledger, so the tick's own apply (and its error
// record) never removes it. Unparseable = unreadable: no retry and no PR write.
export const LEDGER_SCHEMA = 'haynes.upgrade-lane-retries/v1';
export const LEDGER_KEEP_DAYS = 30;
export function parseLedger(text) {
  if (text === undefined || text === null || text === '') return { ok: true, absent: true, ledger: { schema: LEDGER_SCHEMA, retries: {}, halt: null } };
  let j = null;
  try { j = JSON.parse(String(text)); } catch { j = null; }
  const okEntry = e => e && typeof e === 'object' && SHA.test(e.head || '') && Number.isFinite(Date.parse(e.at || '')) && ['intent', 'ticked', 'write-failed', 'abandoned'].includes(e.status);
  if (!j || j.schema !== LEDGER_SCHEMA || !j.retries || typeof j.retries !== 'object' || Array.isArray(j.retries)
    || !Object.entries(j.retries).every(([k, v]) => /^[1-9]\d*$/.test(k) && Array.isArray(v) && v.every(okEntry))) {
    return { ok: false, ledger: null, why: 'unreadable: data.retry_ledger on upgrade-lane-state is not a haynes.upgrade-lane-retries/v1 ledger' };
  }
  return { ok: true, absent: false, ledger: { schema: LEDGER_SCHEMA, retries: j.retries, halt: j.halt && typeof j.halt === 'object' ? j.halt : null } };
}
// The newest counted entry of a PR takes an assessment's update (an outcome seen this tick).
export function applyRetryUpdate(entries, update) {
  if (!update || !Array.isArray(entries)) return false;
  const counted = entries.filter(counts).sort((a, b) => String(a.at).localeCompare(String(b.at)));
  const last = counted[counted.length - 1];
  if (!last || last.outcome) return false;
  Object.assign(last, update);
  return true;
}
// Entries of PRs no longer open, and entries older than LEDGER_KEEP_DAYS, are dropped.
export function pruneLedger(ledger, openPrs, now) {
  const open = new Set((openPrs || []).map(String));
  const retries = {};
  for (const [k, v] of Object.entries(ledger?.retries || {})) {
    if (!open.has(k)) continue;
    const keep = v.filter(e => now - Date.parse(e.at) < LEDGER_KEEP_DAYS * 864e5);
    if (keep.length) retries[k] = keep;
  }
  return { ...ledger, retries };
}

// ── reads for the PR-write phase (read-only runner) ──
export const readPull = (repo, pr, run) => { const j = parse(tryRun(run, 'gh', ['api', `repos/${repo}/pulls/${pr}`])); return j && typeof j === 'object' && !Array.isArray(j) ? j : null; };
export const readComment = (repo, id, run) => { const j = parse(tryRun(run, 'gh', ['api', `repos/${repo}/issues/comments/${id}`])); return j && typeof j === 'object' && !Array.isArray(j) ? j : null; };
export function readIssueComments(repo, pr, run) {
  const j = parse(tryRun(run, 'gh', ['api', '--paginate', '--slurp', `repos/${repo}/issues/${pr}/comments?per_page=100`]));
  return Array.isArray(j) ? j.flat() : null;
}
const RETRY_VIEW_FIELDS = 'number,headRefOid,commits,statusCheckRollup';
export const readCheckSuites = (repo, head, run) => { const j = parse(tryRun(run, 'gh', ['api', `repos/${repo}/commits/${head}/check-suites?per_page=100`])); return j && typeof j === 'object' && !Array.isArray(j) ? j : null; };

// ── THE GITHUB WRITES (not merges) ──
export const GH_WRITE_KINDS = Object.freeze(['record-create', 'record-edit', 'record-demote', 'retry-box']);
export function ghWrite(kind, a, env) {
  if (!GH_WRITE_KINDS.includes(kind)) throw new Error(`ghWrite: refused kind ${kind}`);
  if (a?.repo !== REPO) throw new Error(`ghWrite: refused repository ${a?.repo}`);
  const body = a.body;
  if (typeof body !== 'string' || !body.length) throw new Error('ghWrite: refused an empty body');
  const posInt = v => Number.isInteger(v) && v > 0;
  let method, path;
  if (kind === 'record-create') {
    if (!posInt(a.pr) || !body.startsWith(`${L.RECORD_MARKER}\n`)) throw new Error('ghWrite: refused: a new record needs a PR number and a body that starts with the record marker');
    method = 'POST'; path = `repos/${REPO}/issues/${a.pr}/comments`;
  } else if (kind === 'record-edit' || kind === 'record-demote') {
    if (!posInt(a.commentId)) throw new Error('ghWrite: refused: a comment edit needs a comment id');
    if (kind === 'record-edit' && !body.startsWith(`${L.RECORD_MARKER}\n`)) throw new Error('ghWrite: refused: a record edit must keep the record marker first');
    if (kind === 'record-demote' && body.includes(L.RECORD_MARKER)) throw new Error('ghWrite: refused: a demoted record must not carry the marker');
    method = 'PATCH'; path = `repos/${REPO}/issues/comments/${a.commentId}`;
  } else {
    const t = tickRetryBox(a.before);
    if (!posInt(a.pr) || !t.ok || t.body !== body) throw new Error('ghWrite: refused: a PR body edit must tick the retry box and change nothing else');
    method = 'PATCH'; path = `repos/${REPO}/pulls/${a.pr}`;
  }
  const argv = ['api', '--method', method, path, '--input', '-'];
  const r = spawnSync('gh', argv, { input: JSON.stringify({ body }), encoding: 'utf8', env, timeout: GH_TIMEOUT_MS });
  const http = /\(HTTP (\d{3})\)/.exec(String(r.stderr || ''))?.[1];
  return { ok: r.status === 0, exit: r.status, http: http ? Number(http) : null, call: `gh api --method ${method} ${path}`,
    detail: r.status === 0 ? null : r.status === null ? `no exit status (${r.signal || r.error?.code || 'killed'})` : publicDetail(r.stderr || r.error?.message), json: r.status === 0 ? parse(r.stdout) : null };
}

// ── the retry write (one PR) ──
// cand = { pr, head, files, failing }; ledger = { retries: { "<pr>": [entry] } } (mutated);
// persistLedger(ledger) -> true when written. plan = true (a local run): the reads and the
// preflight only, outcome would-tick; no ledger write, no GitHub write. Returns { pr, head,
// outcome (ticked | write-failed | not-written | would-tick), why (public), raw_detail (the
// Job log only), writes, call, halt }.
export function runRetry({ cand, rules, run, env, clock, ledger, persistLedger, write = ghWrite, plan = false }) {
  const res = { pr: cand.pr, head: cand.head, outcome: 'not-written', why: null, writes: 0, call: null, halt: null, raw_detail: null };
  if (!plan) { const p0 = pauseWhy(readPause(rules, run), 'before the retry'); if (p0) return { ...res, why: p0 }; }
  const pull = readPull(REPO, cand.pr, run);
  const view = parse(tryRun(run, 'gh', ['pr', 'view', String(cand.pr), '-R', REPO, '--json', RETRY_VIEW_FIELDS]));
  const refusals = retryPreflight({ pull, view, commits: readPrCommits(REPO, cand.pr, run), suites: readCheckSuites(REPO, cand.head, run), head: cand.head, files: cand.files, rules, now: clock() });
  if (refusals.length) return { ...res, why: `the facts at the write refuse it: ${refusals.join('; ')}`, preflight: false };
  if (plan) return { ...res, outcome: 'would-tick', why: 'every fact at the write allows it (a local run writes nothing)', preflight: true };
  const key = String(cand.pr);
  const entry = { head: cand.head, at: iso(clock()), status: 'intent', checks: cand.failing || [] };
  ledger.retries[key] = [...(ledger.retries[key] || []), entry];
  if (!persistLedger(ledger)) { ledger.retries[key].pop(); return { ...res, why: 'the retry ledger could not be written before the tick; nothing was ticked' }; }
  const p1 = pauseWhy(readPause(rules, run), 'just before the retry write');
  const again = p1 ? null : readPull(REPO, cand.pr, run);
  const why = p1 || (!again ? 'unreadable: the pull request just before the write'
    : again.head?.sha !== cand.head ? `the head moved between the read and the write (${short(again.head?.sha)})`
      : again.body !== pull.body ? 'the PR body changed between the read and the write' : null);
  if (why) { entry.status = 'abandoned'; entry.why = why; return { ...res, why: `${why}; not written this tick` }; }
  const t = tickRetryBox(pull.body);
  const w = write('retry-box', { repo: REPO, pr: cand.pr, body: t.body, before: pull.body }, env);
  res.writes = 1; res.call = w.call;
  if (!w.ok) {
    const f = writeFailure(w);
    entry.status = 'write-failed'; entry.why = f.reason;
    return { ...res, outcome: 'write-failed', why: `the box write was ${f.reason}`, http: w.http, failure: f.kind, raw_detail: `${w.call}: ${w.detail}`,
      halt: f.halts ? { why: `the retry box write on #${cand.pr} was ${f.reason}; a denial is a stop: PR writes halt until a person removes retry_ledger.halt` } : null };
  }
  entry.status = 'ticked';
  const echoed = L.retryBox(w.json?.body).state;
  if (echoed !== 'ticked' || w.json?.head?.sha !== cand.head) entry.why = `GitHub's reply reads the box ${echoed} at head ${short(w.json?.head?.sha)}`;
  return { ...res, outcome: 'ticked', why: entry.why || null };
}

// ── the record writes (every open Renovate PR) ──
// items = [{ rec, comments, edit_only }] (comments = facts.record_comments, null when unread;
// edit_only: story 593's terminal record for a PR that left the open list, which edits the
// lane's own record and never creates one); live = false plans only (a local run). Returns
// the summary block.
// pauseCheck() -> a public reason when paused (or unreadable), else null: read just before
// each write (F5). The first failed write stops the rest of the tick; a denial also halts.
export function writePrRecords({ items, cfg, run, env, live, write = ghWrite, stopped = null, pauseCheck = () => null }) {
  const out = { github_writes: 0, created: [], updated: [], current: [], demoted: [], refused: [], failed: [], not_attempted: [], foreign_ignored: [], would_create: [], would_update: [], no_record: [], halt: null, stopped_by: stopped, raw_details: [] };
  let stop = stopped;
  const fail = (pr, w) => {
    const f = writeFailure(w);
    out.failed.push({ pr, why: `${w.call}: ${f.reason}`, failure: f.kind });
    out.raw_details.push(`#${pr} ${w.call}: ${w.detail}`);
    if (f.halts && !out.halt) out.halt = { why: `the record write on #${pr} was ${f.reason}; a denial is a stop: PR writes halt until a person removes retry_ledger.halt` };
    stop = `an earlier GitHub write failed this tick (#${pr}, ${f.kind})`; out.stopped_by = stop;
  };
  const paused = (pr, listIt = true) => { const p = pauseCheck(); if (!p) return false; stop = p; out.stopped_by = p; if (listIt) out.not_attempted.push(pr); return true; };
  const stillOwn = c => c && c.user?.login === cfg.author.login && c.user?.type === cfg.author.type && String(c.body || '').startsWith(cfg.marker);
  for (const { rec, comments, edit_only: editOnly } of [...items].sort((a, b) => a.rec.pr - b.rec.pr)) {
    const pr = rec.pr;
    if (stop) { out.not_attempted.push(pr); continue; }
    if (!Array.isArray(comments)) { out.refused.push({ pr, why: 'unreadable: the comments of this PR (its facts were not read this tick)' }); continue; }
    const pub = projectRecord(rec, cfg);
    const body = renderRecord(pub, cfg);
    const probs = publishProblems(pub, body, cfg);
    if (probs.length) { out.refused.push({ pr, why: probs.join('; ') }); continue; }
    let plan = planRecord(pub, comments, cfg);
    if (editOnly && plan.action === 'create') { out.no_record.push(pr); continue; }
    if (plan.foreign.length) out.foreign_ignored.push({ pr, comments: plan.foreign.map(c => `${c.id} by ${c.author}`) });
    if (!live) { if (plan.action === 'create') out.would_create.push(pr); else if (plan.action === 'update') out.would_update.push(pr); else out.current.push(pr); continue; }
    if (plan.action === 'create') {
      // listed again just before the create: never a second record
      const fresh = readIssueComments(REPO, pr, run);
      if (!fresh) { out.refused.push({ pr, why: 'unreadable: the comments of this PR just before the create; not written' }); continue; }
      plan = planRecord(pub, L.recordComments(fresh), cfg);
    }
    if (plan.action === 'update') {
      const c = readComment(REPO, plan.id, run);
      if (!stillOwn(c)) { out.refused.push({ pr, why: c ? `comment ${plan.id} is no longer the lane's record (author ${c.user?.login ?? 'unknown'}); not edited` : `unreadable: comment ${plan.id} just before the edit; not edited` }); continue; }
      const cur = parseRecord(c.body, cfg);
      if (cur && cur.pr === pub.pr && recordContentKey(cur) === recordContentKey(pub)) plan = { ...plan, action: 'current' };
    }
    if (plan.action === 'current') out.current.push(pr);
    else if (plan.action === 'update') {
      if (paused(pr)) continue;
      const w = write('record-edit', { repo: REPO, commentId: plan.id, body }, env);
      out.github_writes++;
      if (!w.ok) { fail(pr, w); continue; }
      out.updated.push(pr);
    } else {
      if (paused(pr)) continue;
      const w = write('record-create', { repo: REPO, pr, body }, env);
      out.github_writes++;
      if (!w.ok) { fail(pr, w); continue; }
      const who = { login: w.json?.user?.login ?? null, type: w.json?.user?.type ?? null };
      if (who.login !== cfg.author.login || who.type !== cfg.author.type) {
        // the token is not the identity the lane reads its records by: every later tick
        // would add another comment. Halt (data.retry_ledger.halt) until a person clears it.
        out.halt = { why: `the record on #${pr} was written as ${who.login ?? 'unknown'} (${who.type ?? '?'}), not ${cfg.author.login} (${cfg.author.type}); PR writes halt until a person removes retry_ledger.halt`, comment: w.json?.id ?? null };
        out.failed.push({ pr, why: out.halt.why });
        stop = 'the record identity check failed this tick'; out.stopped_by = stop;
        continue;
      }
      plan = { ...plan, id: w.json?.id ?? null };
      out.created.push(pr);
    }
    for (const id of plan.demote) {
      if (stop) break;
      const c = readComment(REPO, id, run);
      if (!stillOwn(c)) continue;
      if (paused(pr, false)) break;
      const w = write('record-demote', { repo: REPO, commentId: id, body: demoteBody(REPO, pr, plan.id) }, env);
      out.github_writes++;
      if (!w.ok) { fail(pr, w); break; }
      out.demoted.push({ pr, id });
    }
  }
  return out;
}

// ── the lane's merge step (one per tick) ─────────────────────────────────────────────
// subject = { pr, head, class, chosen } | null. chosen = false: the tick chose no PR and
// this is the nearest lane-A candidate, exercised in dry-run only.
// windowOpen(ms) = { open, local } for the merge window; tickStartedAt = when the tick
// began (its gates are that old). Live mode re-checks both, and the pause, just before
// the merge call. rereadPause = the wait between mergeability reads (default sleepSync); the
// re-reads sit in evaluate(), among the fact reads, so the freshness checks measure them.
export function laneMergeStep({ mode, subject, rules, run, env, clock, liveClasses, token, holder, takeAllowed, windowOpen, tickStartedAt, rereadPause }) {
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
    const ev = evaluate({ repo: REPO, pr: subject.pr, head: subject.head, rules, now: clock(), run, rereadPause });
    const prRef = [...(held ? [] : [out.lock.why || 'no change lock held']), ...ev.refusals];
    const laneRef = laneRefusals({ klass: subject.class, liveClasses, token });
    if (!subject.chosen) laneRef.unshift('not chosen by the tick: it has gate blockers (records[].gates.blockers)');
    // the settle gate (story 551), facts about the subject's target, read afresh under the
    // lock in both modes; and F4, no shepherd Job that can merge running
    const settle = settleCheck({ rules, run, scope: subject.scope ?? null });
    laneRef.push(...settle.blockers.map(b => `settle gate: ${b}`));
    out.settle = settleBrief(settle);
    const jobs0 = readMergingJobs(run);
    out.shepherd_jobs = jobs0;
    if (!jobs0.readable || jobs0.active.length) laneRef.push(`F4 ${jobs0.why}`);
    const v = { pr: subject.pr, head: subject.head, class: subject.class, chosen: subject.chosen, mode, verdict: 'refused', pr_refusals: [...new Set(prRef)], lane_refusals: laneRef, pr_ready: prRef.length === 0, facts: ev.facts };
    const clean = v.pr_refusals.length === 0 && laneRef.length === 0;
    if (mode !== 'live') { v.verdict = clean ? 'would-merge' : 'refused'; v.merge_call = 'not reached (dry-run)'; out.verdict = v; return out; }
    if (!clean) { v.merge_call = 'not made (refused)'; out.verdict = v; return out; }
    // just before the call (R5: the fact reads first, then the freshness checks, so no gate
    // is older than its limit at the call): the settle gate again (another actor may have
    // merged while the step read the PR), then the lease, the window and the age of the
    // tick's gates, then the pause, the merging write, F4, and the call
    const s1 = settleCheck({ rules, run, scope: subject.scope ?? null });
    v.lane_refusals.push(...s1.blockers.map(b => `settle gate at the merge call: ${b}`));
    v.settle_at_merge_call = settleBrief(s1);
    const t = clock();
    const left = Date.parse(held.data.expires_at) - t;
    if (!held.rv) v.lane_refusals.push('the change lock was written but its resourceVersion is unknown; not merging on it');
    else if (left < cfg.min_lease_left_seconds * 1000) v.lane_refusals.push(`the change lock has ${Math.round(left / 1000)}s of lease left, under ${cfg.min_lease_left_seconds}s; not merging on a lease that may lapse`);
    const win = typeof windowOpen === 'function' ? windowOpen(t) : null;
    if (!win || win.open !== true) v.lane_refusals.push(win ? `the merge window is closed at the merge call (now ${win.local})` : 'unreadable: whether the merge window is open at the merge call');
    const age = t - tickStartedAt;
    if (!Number.isFinite(age) || age < 0 || age > cfg.max_gate_age_seconds * 1000) v.lane_refusals.push(Number.isFinite(age) ? `the tick's gates are ${Math.round(age / 1000)}s old at the merge call, over ${cfg.max_gate_age_seconds}s` : 'unreadable: when the tick began');
    const p1 = pauseRefusal(readPause(rules, run), 'just before the merge call');
    if (p1) v.pr_refusals.push(p1);
    if (v.pr_refusals.length || v.lane_refusals.length) { v.pr_ready = v.pr_refusals.length === 0; v.merge_call = 'not made (refused)'; out.verdict = v; return out; }
    const begin = beginMerge(held, { now: clock(), env, leaseSeconds: cfg.post_merge_lease_seconds });
    if (!begin.ok) { v.lane_refusals.push(begin.why); v.merge_call = 'not made (refused)'; out.verdict = v; return out; }
    // F4 after the merging write: a shepherd Job that started after the step's read either
    // reads this phase (and stands aside) or is seen here. Seen: yield, release the lock
    // (no merge call was made) and let the next tick try again.
    const jobs1 = readMergingJobs(run);
    v.shepherd_jobs_at_merge_call = jobs1;
    if (!jobs1.readable || jobs1.active.length) {
      v.yielded = 'yielded-to-shepherd';
      v.lane_refusals.push(`F4 at the merge call: ${jobs1.why}; yielded-to-shepherd, the next tick tries again`);
      v.merge_call = 'not made (yielded)'; out.verdict = v; return out;
    }
    // from here, whatever happens next, the lock keeps the post-merge lease (S4: set only now)
    held.merged = true;
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
      const r = releaseLock(held, { now: clock(), env, note: out.verdict?.yielded || out.verdict?.verdict || 'tick ended' });
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
export function handRun(opt, { rules, run, env, now, rereadPause }) {
  const out = { mode: opt.merge ? 'hand --merge' : 'hand --check-only', pr: opt.pr, head: opt.head, refusals: [], notes: [] };
  const p0 = pauseRefusal(readPause(rules, run), 'at the start');
  if (p0) out.refusals.push(p0);
  const lock = lockStatus(readNamedConfigMap(LOCK.name, run), now);
  out.lock = { state: lock.state, holder: lock.holder || null, expires_at: lock.expires_at || null };
  if (['held', 'unreadable'].includes(lock.state)) out.refusals.push(lock.why);
  out.notes.push('a hand run reads the change lock and refuses while it is held; it cannot take it (a session cannot write ConfigMaps in upgrade-agent), so the shepherd and the lane do not see it');
  out.notes.push('a hand run runs the settle gate and F4 (story 551) at its start and, with --merge, again just before the call');
  out.notes.push('a hand run skips the merge window, the class allowlist and the tick gates (backups, Flux baseline, critical alerts, work orders); it needs the owner\'s yes');
  // the classifier, on the same facts the tick gathers
  let holdRules = null, facts = null, rec = null;
  try { holdRules = L.fetchHoldRules(REPO, run); } catch { holdRules = null; }
  try { facts = L.gatherFacts(REPO, opt.pr, run); } catch { facts = null; }
  if (!holdRules) out.refusals.push('unreadable: .renovate/holds.json5 on main');
  if (!facts) out.refusals.push('unreadable: the PR facts for the classifier');
  if (holdRules && facts) {
    rec = L.classify(facts, rules, { now, holdRules });
    out.lane = { lane: rec.lane, class: rec.class, reason: rec.reason, rules: rec.rules };
    if (rec.lane !== 'LANE-A') out.refusals.push(`lane ${rec.lane} (${rec.class}): ${rec.reason}`);
  }
  // the settle gate (story 551): facts about the PR's target (F1 to F3), and F4
  const settleOnce = when => {
    const sf = readSettleFacts(REPO, rules, run);
    const scope = rec ? settleScope(rec, sf.ks, sf.hr) : undefined;
    const st = settleVerdict(sf, scope, rules);
    const jobs = readMergingJobs(run);
    return { st, jobs, refusals: [...st.blockers.map(b => `settle gate${when}: ${b}`), ...(!jobs.readable || jobs.active.length ? [`F4${when}: ${jobs.why}`] : [])] };
  };
  const g0 = settleOnce('');
  out.settle = settleBrief(g0.st);
  out.shepherd_jobs = g0.jobs;
  out.refusals.push(...g0.refusals);
  const ev = evaluate({ repo: REPO, pr: opt.pr, head: opt.head, rules, now, run, rereadPause });
  out.refusals.push(...ev.refusals);
  out.facts = ev.facts;
  if (opt.merge) {
    const g1 = settleOnce(' just before the merge call');
    out.settle_at_merge_call = settleBrief(g1.st);
    out.shepherd_jobs_at_merge_call = g1.jobs;
    out.refusals.push(...g1.refusals);
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
    if (r.facts) lines.push(`  facts: reference_time=${r.facts.reference_time} mergeable=${r.facts.mergeable} merge_state=${r.facts.merge_state} mergeable_reads=${r.facts.mergeable_reads} files=${r.facts.files.join(',')}`);
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
