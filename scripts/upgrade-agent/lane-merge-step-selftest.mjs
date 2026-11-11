#!/usr/bin/env node
// Self-test for the night lane's merge step and change lock (story 752,
// https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/752).
//
// WHY THIS EXISTS
// ---------------
// night-merge.mjs is the code that can merge a pull request and that holds the change
// lock. This test pins, without network or cluster:
//   1. the PR preflight: every refusal the work order names (a pending commit status,
//      a running or failing check, a required or expected check not SUCCESS, a draft, a
//      non-Renovate commit, a moved head, a conflicting PR, a truncated read), and that
//      an advisory check and a superseded attempt do not count;
//   2. the reads around it: story 550's R7 through the same exported functions, and the
//      head-behind-main rule, each failing closed on a read it cannot make; PR 3915 as
//      read live on 2026-11-11 (fixtures/merge-step-pr3915-20261111.json);
//   3. the change lock: free, held, expired and unreadable; an atomic take (AlreadyExists,
//      Conflict); release on return, on an exception, on process exit and on SIGTERM;
//      kept after a merge; a stale lock taken over by its lease;
//   4. the token and the class allowlist; the merge argv (squash, pinned to the head, no
//      bypass flag); callMerge refusing every mode but live and hand before it spawns;
//   5. the hand-run CLI (--check-only and --merge) against stub gh and kubectl;
//   6. the settle gate (story 551): each Flux state, each read failing closed, the
//      2026-11-10 shepherd merges (fixtures/settle-20261110.json: the lane waits through
//      multus's roll and opens once it settled), and the re-read at a live merge call and
//      before a hand --merge, where another actor's merge in between refuses the call.
// The tick-level cases (dry-run never reaching the merge call, live refusals through the
// real tick) are in lane-night-dryrun-selftest.mjs.
//
// Usage: node scripts/upgrade-agent/lane-merge-step-selftest.mjs
// Requires: node 24. No network, no cluster access; gh and kubectl are stubs on PATH.
import { spawn, spawnSync } from 'node:child_process';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../..');
const RES = join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/resources');
const L = await import(join(RES, 'lane-classify.mjs'));
const M = await import(join(RES, 'night-merge.mjs'));
const rules = L.loadRules(join(RES, 'upgrade-rules.json'));
const fx = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/merge-step-pr3915-20261111.json'), 'utf8'));
const HEAD = fx.head;
const NOW = Date.parse('2026-11-11T16:00:00Z');

let fails = 0, passes = 0;
const ok = (cond, name, detail = '') => { if (cond) passes++; else { fails++; console.log(`FAIL ${name}${detail ? `\n     ${detail}` : ''}`); } };
const has = (list, re) => (list || []).some(x => re.test(x));

// ── 1. the PR preflight (pure) ─────────────────────────────────────────────────
const view = (f = v => v) => f(structuredClone(fx.view));
const pre = (v, head = HEAD, now = NOW) => M.prRefusals(v, head, rules, now).refusals;
{
  ok(pre(view()).length === 0, 'PR 3915 as read on 2026-11-11 passes the PR preflight', pre(view()).join(' | '));
  const status = (state, context = 'ci/external') => view(v => { v.statusCheckRollup.push({ __typename: 'StatusContext', context, state }); return v; });
  ok(has(pre(status('PENDING')), /^commit status ci\/external is pending \(PENDING\)$/), 'a PENDING commit status is refused (the handoff preflight let it through)');
  ok(has(pre(status('EXPECTED')), /commit status ci\/external is pending \(EXPECTED\)/), 'an EXPECTED commit status is refused');
  ok(has(pre(status('FAILURE')), /commit status ci\/external is FAILURE/) && has(pre(status('ERROR')), /is ERROR/), 'a failing commit status is refused');
  ok(has(pre(status('')), /commit status ci\/external is unknown/), 'a commit status with no state is refused');
  ok(pre(status('SUCCESS')).length === 0, 'a SUCCESS commit status passes');
  const run = (name, patch) => view(v => { const c = v.statusCheckRollup.find(x => x.name === name); Object.assign(c, patch); return v; });
  ok(has(pre(run('Flux Local - Filter', { status: 'IN_PROGRESS', conclusion: null })), /^check still running: Flux Local \/ Flux Local - Filter \(IN_PROGRESS\)$/), 'a running check is refused');
  ok(has(pre(run('Flux Local - Filter', { conclusion: 'FAILURE' })), /^check failed: Flux Local \/ Flux Local - Filter \(FAILURE\)$/), 'a failing check is refused');
  ok(has(pre(run('Flux Local - Filter', { conclusion: 'STALE' })), /check failed: .*\(STALE\)/), 'a STALE check is refused');
  ok(pre(run('Claude Review (advisory)', { status: 'IN_PROGRESS', conclusion: null })).length === 0 && pre(run('Claude Review (advisory)', { conclusion: 'FAILURE' })).length === 0, 'the advisory review, running or failed, is not a gate');
  ok(has(pre(view(v => { v.statusCheckRollup = v.statusCheckRollup.filter(c => c.name !== 'Flux Local - Success'); return v; })), /^required check not SUCCESS: Flux Local - Success \(absent\)$/), 'a missing required check is refused');
  ok(has(pre(run('Diff Scope - Success', { conclusion: 'SKIPPED' })), /required check not SUCCESS: Diff Scope - Success \(SKIPPED\)/), 'a skipped required check is refused');
  ok(has(pre(view(v => { v.statusCheckRollup = v.statusCheckRollup.filter(c => c.name !== 'Archive Guard Source - Success'); return v; })), /expected check not SUCCESS: dev-env archive guard source tests \/ Archive Guard Source - Success/), 'a check the check map expects for the files, missing, is refused');
  const rerun = view(v => { const c = v.statusCheckRollup.find(x => x.name === 'Flux Local - Success'); v.statusCheckRollup.push({ ...c, conclusion: 'FAILURE', startedAt: '2026-11-10T00:10:00Z', completedAt: '2026-11-10T00:10:30Z' }); return v; });
  ok(pre(rerun).length === 0, 'an older failed attempt superseded by a newer SUCCESS does not count');
  ok(has(pre(view(), HEAD, Date.parse('2026-11-18T00:30:00Z')), /expected check older than 7d/), 'checks older than check_max_age_days are refused');
  ok(has(pre(view(v => { v.isDraft = true; return v; })), /^a draft$/), 'a draft is refused');
  ok(has(pre(view(v => { delete v.isDraft; return v; })), /unreadable: whether the PR is a draft/), 'an unreadable draft flag is refused');
  ok(has(pre(view(v => { v.state = 'CLOSED'; return v; })), /not open \(state CLOSED\)/) && has(pre(view(v => { v.state = 'MERGED'; return v; })), /not open \(state MERGED\)/), 'a closed or merged PR is refused');
  ok(has(pre(view(v => { v.baseRefName = 'release'; return v; })), /base is release, not main/), 'a PR not based on main is refused');
  ok(has(pre(view(v => { v.author = { login: 'thaynes43' }; return v; })), /opened by thaynes43 \(is_bot unknown\), not the Renovate app/), 'a PR not opened by Renovate is refused');
  ok(has(pre(view(v => { v.commits[0].authors.push({ login: 'haynes-dev-bot' }); return v; })), /commit a7174f4e is not Renovate's alone \(renovate\[bot\], haynes-dev-bot\)/), 'a commit with a non-Renovate author is refused');
  ok(has(pre(view(v => { v.commits[0].authors = []; return v; })), /is not Renovate's alone \(no author\)/), 'a commit with no linked author is refused');
  ok(has(pre(view(v => { v.commits = []; return v; })), /unreadable: the PR commits/), 'no commits is refused');
  ok(has(pre(view(), 'b'.repeat(40)), /^head moved: a7174f4e is not the chosen head bbbbbbbb$/), 'a head other than the chosen head is refused');
  ok(has(pre(view(), 'a7174f4e'), /the chosen head is not a 40-character SHA/), 'a short chosen head is refused');
  ok(has(pre(view(v => { v.mergeable = 'UNKNOWN'; return v; })), /GitHub reads it UNKNOWN, not MERGEABLE/) && has(pre(view(v => { v.mergeable = 'CONFLICTING'; return v; })), /CONFLICTING/), 'a PR GitHub does not read MERGEABLE is refused');
  ok(has(pre(view(v => { v.mergeStateStatus = 'BLOCKED'; return v; })), /merge state BLOCKED/) && has(pre(view(v => { v.mergeStateStatus = 'BEHIND'; return v; })), /merge state BEHIND/), 'a merge state other than CLEAN, HAS_HOOKS or UNSTABLE is refused');
  ok(has(pre(view(v => { v.changedFiles = 3; return v; })), /unreadable: the changed files \(2 listed of 3\)/), 'a truncated file list is refused');
  ok(has(pre(view(v => { while (v.statusCheckRollup.length < 100) v.statusCheckRollup.push({ ...v.statusCheckRollup[1] }); return v; })), /the check list may be truncated \(100 entries\)/), 'a check list of 100 entries is refused as possibly truncated');
  ok(has(pre(view(v => { v.statusCheckRollup.push({ __typename: 'Mystery' }); return v; })), /a check entry of type Mystery/), 'an unknown check entry is refused');
  ok(has(pre(view(v => { v.statusCheckRollup = null; return v; })), /unreadable: the checks on the head/), 'an unreadable check list is refused');
  ok(has(pre(view(v => { v.files.push({ path: 'docs/notes.md' }); v.changedFiles = 3; return v; })), /no PR validation for docs\/notes\.md/), 'a file no workflow validates is refused');
  // finding 5 of the independent review: the author is the Renovate app, from this repository, on a renovate/ branch
  ok(fx.view.author.login === 'app/renovate' && fx.view.author.is_bot === true && fx.view.isCrossRepository === false && fx.view.headRefName === 'renovate/kubernetes-monorepo', 'fixture: gh reported PR 3915 as app/renovate, is_bot, same repository, renovate/ branch (live 2026-11-11)');
  ok(JSON.stringify(rules.renovate_pr_authors) === '["app/renovate"]', 'renovate_pr_authors is the Renovate app login alone');
  for (const [name, f, re] of [
    ['a user named renovate', v => { v.author = { login: 'renovate', is_bot: false }; }, /opened by renovate \(is_bot false\), not the Renovate app/],
    ['renovate[bot] as a PR author login', v => { v.author = { login: 'renovate[bot]', is_bot: true }; }, /opened by renovate\[bot\] \(is_bot true\), not the Renovate app/],
    ['app/renovate that is not a bot', v => { v.author = { login: 'app/renovate', is_bot: false }; }, /\(is_bot false\), not the Renovate app/],
    ['app/renovate with is_bot unreadable', v => { v.author = { login: 'app/renovate' }; }, /\(is_bot unknown\), not the Renovate app/],
    ['a cross-repository PR', v => { v.isCrossRepository = true; }, /a pull request from another repository/],
    ['an unreadable cross-repository flag', v => { delete v.isCrossRepository; }, /unreadable: whether the PR comes from another repository/],
    ['a head branch outside renovate/', v => { v.headRefName = 'feature/renovate-ish'; }, /head branch feature\/renovate-ish is not under renovate\//],
  ]) ok(has(pre(view(v => { f(v); return v; })), re), `finding 5: ${name} is refused`, pre(view(v => { f(v); return v; })).join(' | '));
  const cr = (f = c => c) => M.commitRefusals(fx.pr_commits.map(c => f(structuredClone(c))), fx.view);
  ok(fx.pr_commits.length === 1 && cr().length === 0, "PR 3915's REST commit (renovate[bot], Bot, 29139614, committed by web-flow, signature valid) passes");
  for (const [name, f, re] of [
    ['an author of type User', c => { c.author.type = 'User'; return c; }, /author renovate\[bot\]\/User\/29139614/],
    ['another account id', c => { c.author.id = 1; return c; }, /author renovate\[bot\]\/Bot\/1,/],
    ['another author login', c => { c.author.login = 'haynes-dev-bot[bot]'; return c; }, /author haynes-dev-bot\[bot\]/],
    ['no linked author', c => { c.author = null; return c; }, /author none\/\?\/\?/],
    ['a committer other than web-flow', c => { c.committer.login = 'thaynes43'; return c; }, /committer thaynes43/],
    ['an unsigned commit', c => { c.commit.verification = { verified: false, reason: 'unsigned' }; return c; }, /signature unsigned/],
  ]) ok(has(cr(f), re), `finding 5: a commit with ${name} is refused`, cr(f).join(' | '));
  ok(has(M.commitRefusals(null, fx.view), /^unreadable: the PR commits \(REST\)$/), 'finding 5: an unreadable REST commit list is refused');
  ok(has(M.commitRefusals([...fx.pr_commits, ...fx.pr_commits], fx.view), /the REST and GraphQL commit lists disagree \(2 and 1\)/), 'finding 5: REST and GraphQL commit lists that disagree are refused');
  ok(has(M.commitRefusals(Array(100).fill(fx.pr_commits[0]), fx.view), /may be truncated \(100 commits\)/), 'finding 5: 100 commits is refused as possibly truncated');
  ok(has(M.commitRefusals([{ ...fx.pr_commits[0], sha: 'e'.repeat(40) }], fx.view), /does not end at the head/), 'finding 5: a REST list that does not end at the head is refused');
}

// ── 2. R7 and the head behind main, through evaluate() with a stub reader ─────────────
// The stub answers the read-only gh calls evaluate() makes; w overrides the fixture.
const reader = (w = {}) => (cmd, argv) => {
  if (cmd === 'gh' && argv[0] === 'pr' && argv[1] === 'view') return w.view === null ? null : JSON.stringify(w.view || fx.view);
  const api = argv.find(a => a.startsWith('repos/')) || '';
  if (/\/pulls\/\d+\/commits/.test(api)) return w.commits === 'FAIL' ? null : JSON.stringify(w.commits || fx.pr_commits);
  if (/\/compare\//.test(api)) { const s = api.split('...')[1].split('?')[0]; const c = (w.compare || fx.compare)[s]; return c === 'FAIL' || !c ? null : JSON.stringify(c); }
  if (/\/commits\?sha=main&/.test(api)) {
    const q = new URLSearchParams(api.split('?')[1]);
    const v = q.get('since') ? (w.since || {})[q.get('path')] ?? [] : (w.newest || fx.main_newest)[q.get('path')];
    return v === 'FAIL' || v === undefined ? null : JSON.stringify(v.map(c => ({ sha: c.sha, commit: { committer: { date: c.at } } })));
  }
  return null;
};
const evalRef = w => M.evaluate({ repo: M.REPO, pr: 3915, head: HEAD, rules, now: NOW, run: reader(w) });
const GO_MOD = 'scripts/tests/dev-env-archive-guard/go.mod';
{
  const e = evalRef();
  ok(e.refusals.length === 0 && e.facts.reference_time === '2026-11-10T00:10:58.000Z' && fx.main_ahead_of_head === 94, 'PR 3915 (main 94 commits ahead, no main commit to its files since its base): ready, reference time = its head commit', e.refusals.join(' | '));
  const st = evalRef({ since: { [GO_MOD]: [{ sha: '1'.repeat(40), at: '2026-11-11T09:00:00Z' }] } });
  ok(has(st.refusals, /^checks predate main: scripts\/tests\/dev-env-archive-guard\/go\.mod changed by 11111111 at 2026-11-11T09:00:00Z, after the head's checks \(2026-11-10T00:10:58\.000Z\); needs a Renovate rebase first$/), "checks older than a main commit that touches the PR's files are refused (R7)", st.refusals.join(' | '));
  ok(evalRef({ since: { [GO_MOD]: [{ sha: '1'.repeat(40), at: '2026-11-10T00:10:58Z' }] } }).refusals.length === 0, 'R7: a main commit at the reference time itself is not newer (strict comparison)');
  ok(has(evalRef({ since: { [GO_MOD]: 'FAIL' } }).refusals, /unreadable: main commits since the head's checks for scripts\/tests\/dev-env-archive-guard\/go\.mod/), 'R7: a failed read of main fails closed');
  const nb = { [GO_MOD]: [{ sha: '2'.repeat(40), at: '2026-11-11T08:00:00Z' }], 'scripts/tests/dev-env-archive-guard/go.sum': fx.main_newest['scripts/tests/dev-env-archive-guard/go.sum'] };
  const bh = evalRef({ newest: nb, compare: { ...fx.compare, ['2'.repeat(40)]: { status: 'diverged', ahead_by: 1, behind_by: 94 } } });
  ok(has(bh.refusals, /^head is behind main: scripts\/tests\/dev-env-archive-guard\/go\.mod changed by 22222222 at 2026-11-11T08:00:00Z, which the head a7174f4e does not contain; needs a Renovate rebase first$/) && bh.facts.behind_main.length === 1, "a head behind main by a commit that touches the PR's files is refused", bh.refusals.join(' | '));
  ok(evalRef({ newest: nb, compare: { ...fx.compare, ['2'.repeat(40)]: { status: 'behind', ahead_by: 0, behind_by: 4 } } }).refusals.length === 0, "the head containing main's newest commit to each file is not behind");
  ok(has(evalRef({ newest: nb, compare: { ...fx.compare, ['2'.repeat(40)]: 'FAIL' } }).refusals, /unreadable: whether the head contains main's newest commit to scripts\/tests\/dev-env-archive-guard\/go\.mod/), 'behind main: a failed compare fails closed');
  ok(has(evalRef({ newest: { ...nb, [GO_MOD]: 'FAIL' } }).refusals, /unreadable: whether the head contains main's newest commit to scripts\/tests\/dev-env-archive-guard\/go\.mod/), "behind main: a failed read of main's commits fails closed");
  ok(evalRef({ newest: { [GO_MOD]: [], 'scripts/tests/dev-env-archive-guard/go.sum': [] } }).refusals.length === 0, 'behind main: a file main has no commit for (one the PR adds) is not behind');
  ok(has(evalRef({ view: null }).refusals, /^unreadable: the pull request \(gh pr view\)$/), 'an unreadable PR fails closed');
  ok(has(evalRef({ commits: 'FAIL' }).refusals, /^unreadable: the PR commits \(REST\)$/), 'evaluate() reads the REST commit list and fails closed without it');
  ok(has(evalRef({ view: view(v => { v.commits[0].committedDate = null; v.statusCheckRollup.forEach(c => { c.startedAt = null; c.completedAt = null; }); return v; }) }).refusals, /unreadable: when the head's checks ran/), 'R7: no readable reference time fails closed');
  const b = M.behindMain(['a', 'b', 'c'], { byPath: { a: { sha: 'x', at: 't' }, b: null }, unreadable: [] }, { contains: { x: false }, unreadable: [] });
  ok(b.behind.length === 1 && b.behind[0].path === 'a' && JSON.stringify(b.unreadable) === '["c"]', 'behindMain (pure): behind, a new file, and a path with no answer');
  // R7 is story 550's rule, reused: no second staleness rule in night-merge.mjs
  const src = readFileSync(join(RES, 'night-merge.mjs'), 'utf8').replace(/^\s*\/\/.*$/gm, '');
  ok(/L\.checksReferenceTime\(/.test(src) && /L\.checksPredateMain\(/.test(src) && /L\.mainCommitsTouching\(/.test(src) && !/since=/.test(src), "night-merge.mjs reuses R7's three exported functions and has no staleness query of its own");
}

// ── 3. token, classes, argv (pure) ────────────────────────────────────────────────
{
  const t = s => M.tokenPermissions(s);
  const ro = t('{"contents":"read","pull_requests":"read","checks":"read"}');
  ok(ro.readable && !ro.can_merge && JSON.stringify(ro.names) === '["checks:read","contents:read","pull_requests:read"]' && /the token cannot merge: minted with checks:read, contents:read, pull_requests:read/.test(ro.why), 'a read-only token: names listed, cannot merge');
  ok(t('{"contents":"write","pull_requests":"write","checks":"read"}').can_merge === true, 'contents:write, pull_requests:write, checks:read can merge');
  ok(t('{"contents":"write","pull_requests":"write"}').can_merge === true, 'a write token without checks can merge (checks are read through the PR)');
  ok(!t('{"contents":"write","pull_requests":"read","checks":"read"}').can_merge && !t('{"contents":"read","pull_requests":"write"}').can_merge, 'a token missing either write cannot merge');
  ok(/wider than the lane needs: administration:write, checks:write/.test(t('{"contents":"write","pull_requests":"write","checks":"write","administration":"write"}').why || ''), 'a token wider than a merge needs is refused');
  ok(/wider than the lane needs: contents:admin/.test(t('{"contents":"admin","pull_requests":"write"}').why || ''), 'an admin level is wider than the lane needs');
  for (const bad of [null, '', 'not json', '[]', '{}', '{"contents":"all"}', '"write"']) ok(!t(bad).readable && !t(bad).can_merge && /^unreadable/.test(t(bad).why), `unreadable permissions (${JSON.stringify(bad)}) cannot merge`);
  ok(!JSON.stringify(M.tokenPermissions('{"contents":"read"}')).includes('ghs_'), 'the permission record carries names and levels only');

  ok(JSON.stringify(M.parseLiveClasses('')) === '{"classes":[],"unknown":[]}' && JSON.stringify(M.parseLiveClasses(undefined).classes) === '[]', 'the class allowlist is empty by default');
  ok(JSON.stringify(M.parseLiveClasses(' no-cluster-effect, delayed-cluster-change no-cluster-effect').classes) === '["no-cluster-effect","delayed-cluster-change"]', 'classes split on commas and spaces, de-duplicated');
  ok(JSON.stringify(M.parseLiveClasses('no-cluster-effect,stateless').unknown) === '["stateless"]', 'an unknown class is reported (run-night refuses it before any read)');
  const lr = (k, c, tok) => M.laneRefusals({ klass: k, liveClasses: c, token: tok });
  const wr = t('{"contents":"write","pull_requests":"write","checks":"read"}');
  ok(has(lr('no-cluster-effect', [], wr), /^class no-cluster-effect is not in the live allowlist \(UPGRADE_NIGHT_LIVE_CLASSES: empty\)$/), 'live mode with an empty class allowlist is refused');
  ok(has(lr('cluster-change', ['no-cluster-effect'], wr), /class cluster-change is not in the live allowlist \(UPGRADE_NIGHT_LIVE_CLASSES: no-cluster-effect\)/), 'a class outside the allowlist is refused');
  ok(lr('no-cluster-effect', ['no-cluster-effect'], wr).length === 0, 'an allowlisted class with a write token: no lane refusal');
  ok(has(lr('no-cluster-effect', ['no-cluster-effect'], ro), /the token cannot merge/), 'a read-only token is refused');
  ok(has(lr('note', ['note'], wr), /class note is not a lane-A class/), 'a non-lane-A class is refused even if listed');
  // the classes classify() can give a lane-A PR are exactly LANE_A_CLASSES
  const cls = readFileSync(join(RES, 'lane-classify.mjs'), 'utf8').match(/const aClass = [\s\S]*?;\n/)?.[0] || '';
  ok(JSON.stringify([...new Set(cls.match(/'[a-z-]+-(?:change|effect)'/g).map(s => s.slice(1, -1)))].sort()) === JSON.stringify([...M.LANE_A_CLASSES].sort()), "LANE_A_CLASSES matches lane-classify.mjs's lane-A classes", cls);

  ok(JSON.stringify(M.mergeArgv(M.REPO, 3915, HEAD)) === JSON.stringify(['pr', 'merge', '3915', '--repo', 'thaynes43/haynes-ops', '--squash', '--match-head-commit', HEAD]), 'the merge argv: squash, pinned to the head SHA');
  const bad = a => { try { M.checkMergeArgv(a, HEAD); return false; } catch { return true; } };
  ok(!bad(M.mergeArgv(M.REPO, 3915, HEAD)), 'the shipped argv passes its own check');
  for (const flag of ['--admin', '--auto', '--force', '--auto-merge', '--admin=true']) ok(bad([...M.mergeArgv(M.REPO, 3915, HEAD), flag]), `checkMergeArgv refuses ${flag}`);
  ok(bad(['pr', 'merge', '3915', '--squash']) && bad(['pr', 'merge', '3915', '--squash', '--match-head-commit', 'b'.repeat(40)]) && bad(['pr', 'merge', '3915', '--merge', '--match-head-commit', HEAD]), 'checkMergeArgv refuses an unpinned, mis-pinned or non-squash merge');
  const cfg = M.lockConfig(rules);
  ok(cfg.ok && cfg.lease_seconds === 600 && cfg.lease_seconds < 900 && cfg.post_merge_lease_seconds === 14400 && cfg.min_lease_left_seconds === 240, 'the shipped lock config: 600s lease (under the 15-minute tick), 4h post-merge hold, 240s minimum left', JSON.stringify(cfg));
  for (const [k, v] of [['lease_seconds', 900], ['lease_seconds', 30], ['post_merge_lease_seconds', 60], ['min_lease_left_seconds', 600], ['configmap', 'other'], ['lease_seconds', '600']]) {
    const r2 = structuredClone(rules); r2.night.change_lock[k] = v;
    ok(!M.lockConfig(r2).ok, `an invalid lock config is refused (${k}=${JSON.stringify(v)})`);
  }
}

// ── 3b. the settle gate (story 551): pure, and the 2026-11-10 fixture ───────────────────
// fixtures/settle-20261110.json: main as GitHub listed it after the shepherd's #4760 and
// #4761 (05:01:56Z, 05:02:06Z) and Renovate's #4767 (05:03:25Z), and the Flux objects
// rebuilt from Prometheus around multus's roll (unsettled until 05:36:43Z).
const sx = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/settle-20261110.json'), 'utf8'));
{
  const sc = M.settleConfig(rules);
  ok(sc.ok && sc.minutes === 30 && sc.watched_path === 'kubernetes' && sc.source.namespace === 'flux-system' && sc.source.name === 'haynes-ops' && sx.settle_minutes === sc.minutes, 'git ships the settle gate at 30 minutes on kubernetes/**, Flux source flux-system/haynes-ops', JSON.stringify(sc));
  for (const [k, v] of [['minutes', 29], ['minutes', '30'], ['minutes', 0], ['watched_path', 'kubernetes/main'], ['history_per_page', 4], ['flux_source', { kind: 'OCIRepository', namespace: 'flux-system', name: 'haynes-ops' }], ['flux_source', { kind: 'GitRepository', namespace: 'flux-system' }]]) {
    const r2 = structuredClone(rules); r2.night.settle_gate[k] = v;
    const sv = M.settleVerdict({ cfg: M.settleConfig(r2) }, NOW);
    ok(!M.settleConfig(r2).ok && !sv.settled && /night\.settle_gate in upgrade-rules\.json is invalid/.test(sv.blockers[0] || ''), `an invalid settle config refuses (${k}=${JSON.stringify(v)})`);
  }
  ok(!M.settleVerdict(null, NOW).settled && !M.settleVerdict({ cfg: sc }, NaN).settled, 'no facts, or no time, is not settled');
  // one Flux object
  const W = 30 * 60e3, at = '2026-11-11T15:00:00Z';
  const o = (conds, extra = {}) => ({ metadata: { generation: 2, ...(extra.meta || {}) }, spec: extra.spec || {}, status: { observedGeneration: extra.og ?? 2, conditions: conds } });
  const R = (status, reason = 'X', t = at) => ({ type: 'Ready', status, reason, lastTransitionTime: t });
  const Rc = reason => ({ type: 'Reconciling', status: 'True', reason, lastTransitionTime: at });
  const st = x => M.fluxSettleState(x, NOW, W);
  ok(st(o([R('True')])).state === 'settled', 'Flux: Ready True at its generation is settled');
  ok(st(o([R('Unknown', 'Progressing')])).state === 'progressing' && /Ready Unknown \(Progressing\)/.test(st(o([R('Unknown', 'Progressing')])).why), 'Flux: Ready Unknown is progressing');
  ok(st(o([R('True'), Rc('Progressing')])).state === 'progressing', 'Flux: Ready True but Reconciling is progressing');
  ok(st(o([R('True')], { og: 1 })).state === 'progressing' && /generation 2 not observed yet \(observed 1\)/.test(st(o([R('True')], { og: 1 })).why), 'Flux: a generation not observed yet is progressing');
  ok(st({ metadata: {}, status: { conditions: [R('True')] } }).state === 'progressing', 'Flux: no generation or observedGeneration fails closed (progressing)');
  ok(st(o([])).state === 'progressing' && st({ metadata: {} }).state === 'progressing', 'Flux: no Ready condition, or no conditions, is progressing');
  ok(st(o([R('False', 'DependencyNotReady', new Date(NOW - 5 * 60e3).toISOString())])).state === 'progressing', 'Flux: NotReady for 5 min (inside the window) is progressing');
  ok(st(o([R('False', 'HealthCheckFailed', '2026-11-09T20:00:00Z'), Rc('ProgressingWithRetry')], { og: -1 })).state === 'failing', 'Flux: NotReady for days and retrying (dev-env-h7-metadata-audit) is failing, not settling');
  ok(st(o([R('False', 'HealthCheckFailed', new Date(NOW - 5 * 60e3).toISOString()), { ...Rc('ProgressingWithRetry'), lastTransitionTime: new Date(NOW - 5 * 60e3).toISOString() }], { og: -1 })).state === 'failing', "Flux: the retry loop rewrites the transition time (h7 read live on 2026-11-11, 5 min old): still failing, not settling");
  ok(st(o([R('False', 'HealthCheckFailed', '2026-11-09T20:00:00Z'), Rc('Progressing')])).state === 'progressing', 'Flux: an old NotReady reconciling anew is progressing');
  ok(st(o([R('False', 'X', null)])).state === 'progressing', 'Flux: NotReady since an unreadable time fails closed');
  ok(st(o([R('Maybe')])).state === 'progressing', 'Flux: an unknown Ready status fails closed');
  ok(st(o([R('Unknown')], { spec: { suspend: true } })).state === 'settled', 'Flux: a suspended object is settled (nothing will move it)');
  ok(M.commitTime({ commit: { committer: { date: '2026-11-10T05:00:00Z' }, author: { date: '2026-11-10T05:10:00Z' } } }) === Date.parse('2026-11-10T05:10:00Z') && Number.isNaN(M.commitTime({ commit: { author: { date: '2026-11-10T05:10:00Z' } } })), 'commitTime: the later of committer and author; no committer date is unreadable');
  ok(M.revisionSha(`main@sha1:${'a'.repeat(40)}`) === 'a'.repeat(40) && M.revisionSha('main@sha256:abc') === null && M.revisionSha(null) === null, 'revisionSha reads main@sha1:<40 hex> only');

  // the 2026-11-10 instants, read through readSettleFacts with a reader serving the fixture
  const fxRun = inst => (cmd, argv) => {
    const api = argv.find(a => String(a).startsWith('repos/')) || '';
    const m = sx.main[inst.main];
    if (cmd === 'gh' && /\/commits\?sha=main&path=kubernetes&per_page=20$/.test(api)) return JSON.stringify(m.kubernetes);
    if (cmd === 'gh' && /\/commits\?sha=main&per_page=20$/.test(api)) return JSON.stringify(m.all);
    if (cmd === 'gh' && /\/compare\//.test(api)) {
      const [base, tip] = api.split('/compare/')[1].split('?')[0].split('...');
      const order = m.all.map(c => c.sha); // newest first
      const bi = order.indexOf(base), ti = order.indexOf(tip);
      return bi < 0 && ti < 0 ? null : JSON.stringify({ ahead_by: ti >= 0 && (bi < 0 || ti < bi) ? Math.max(1, (bi < 0 ? order.length : bi) - ti) : 0 });
    }
    if (cmd === 'kubectl' && argv[1] === 'kustomizations.kustomize.toolkit.fluxcd.io') return JSON.stringify({ items: inst.flux.kustomizations });
    if (cmd === 'kubectl' && argv[1] === 'helmreleases.helm.toolkit.fluxcd.io') return JSON.stringify({ items: inst.flux.helmreleases });
    if (cmd === 'kubectl' && argv[1] === 'gitrepositories.source.toolkit.fluxcd.io') return JSON.stringify({ items: [inst.flux.gitrepository] });
    return null;
  };
  const judge = (inst, now = Date.parse(inst.at)) => M.settleVerdict(M.readSettleFacts(M.REPO, rules, fxRun(inst)), now);
  ok(sx.instants.length === 5 && sx.instants.filter(i => i.want === 'wait').length === 4, 'the fixture has four waiting instants and one settled');
  for (const inst of sx.instants) {
    const v = judge(inst);
    const text = [...v.blockers, ...v.unsettled].join(' | ');
    ok(v.settled === (inst.want === 'settled') && inst.because.every(b => text.includes(b)), `2026-11-10 ${inst.at} (${inst.id}): the lane ${inst.want === 'settled' ? 'may merge' : 'waits'}${inst.because.length ? ` (${inst.because.join('; ')})` : ''}`, text || JSON.stringify(v.newest_change));
  }
  const i0 = sx.instants.find(i => i.id === 'after-4767');
  const v0 = judge(i0);
  ok(v0.newest_change?.sha === 'acaca28d9ed92317ad9fccb889a8336b5747758a' && v0.newest_change.at === '2026-11-10T05:03:25.000Z' && /no lane merge before 2026-11-10T05:33:25\.000Z/.test(v0.blockers.join(' ')), "the newest change is #4767 (Renovate's auto-merge at 05:03:25Z), whoever merged it; the wait runs to 05:33:25Z", JSON.stringify(v0.newest_change));
  const race = sx.instants.find(i => i.id === 'counterfactual-race');
  const vr = judge(race);
  ok(!vr.settled && !vr.blockers.some(b => /^main changed/.test(b)) && vr.blockers.some(b => /^Flux has not fetched main's newest change to kubernetes\/\*\* yet: GitRepository flux-system\/haynes-ops is at 08f367f6, which does not contain 3dadf34a$/.test(b)), 'counterfactual: 30 min after #4761 but before multus rolls, only the Flux-fetched rule holds the lane', vr.blockers.join(' | '));
  const roll = sx.instants.find(i => i.id === 'multus-rolling');
  const vroll = judge(roll);
  ok(vroll.unsettled.some(x => /^Kustomization home-automation\/home-assistant: has not attempted acaca28d yet/.test(x)) && vroll.failing_not_settling.some(x => /dev-env-h7-metadata-audit/.test(x)) && !vroll.blockers.some(b => /h7-metadata-audit/.test(b)), "multus rolling: dependents that have not attempted the revision are unsettled; the long-broken h7 Kustomization is listed, not blocking", JSON.stringify({ u: vroll.unsettled, f: vroll.failing_not_settling }));
  const done = sx.instants.find(i => i.id === 'settled-counterfactual');
  const before = Date.parse('2026-11-10T05:33:24.999Z'), after30 = Date.parse('2026-11-10T05:33:25Z');
  ok(!judge(done, before).settled && judge(done, after30).settled, 'the 30 minutes end exactly at 05:33:25Z (strict): 1 ms before still waits');
  // each settle read fails closed
  const failing = (inst, which) => (cmd, argv) => {
    const api = argv.find(a => String(a).startsWith('repos/')) || '';
    if (which === 'k8s' && /path=kubernetes/.test(api)) return null;
    if (which === 'k8s-empty' && /path=kubernetes/.test(api)) return '[]';
    if (which === 'all' && /\/commits\?sha=main&per_page=/.test(api)) return 'not json';
    if (which === 'compare' && /\/compare\//.test(api)) return null;
    if (which === argv[1]) return null;
    if (which === 'source-absent' && argv[1] === 'gitrepositories.source.toolkit.fluxcd.io') return JSON.stringify({ items: [] });
    if (which === 'source-garbled' && argv[1] === 'gitrepositories.source.toolkit.fluxcd.io') return JSON.stringify({ items: [{ ...inst.flux.gitrepository, status: { ...inst.flux.gitrepository.status, artifact: { revision: 'main@sha1:nothex' } } }] });
    return fxRun(inst)(cmd, argv);
  };
  for (const [which, re] of [['k8s', /^unreadable: main's newest commits to kubernetes\/\*\*$/], ['k8s-empty', /^unreadable: main lists no commit to kubernetes\/\*\* at all$/], ['all', /^unreadable: main's newest commits \(to find merge commits/],
    ['kustomizations.kustomize.toolkit.fluxcd.io', /^unreadable: kustomizations \(settle gate\)$/], ['helmreleases.helm.toolkit.fluxcd.io', /^unreadable: helmreleases \(settle gate\)$/], ['gitrepositories.source.toolkit.fluxcd.io', /^unreadable: GitRepository flux-system\/haynes-ops$/],
    ['source-absent', /^GitRepository flux-system\/haynes-ops does not exist$/], ['source-garbled', /^unreadable: the artifact revision of GitRepository flux-system\/haynes-ops/]]) {
    const v = M.settleVerdict(M.readSettleFacts(M.REPO, rules, failing(done, which)), Date.parse(done.at));
    ok(!v.settled && v.blockers.some(b => re.test(b)), `settle gate: ${which} unreadable fails closed`, v.blockers.join(' | '));
  }
  const cmpFail = structuredClone(done); cmpFail.flux.gitrepository.status.artifact.revision = 'main@sha1:08f367f6b8d5f87f61a718fc59c0e2dfe4069178';
  const vc = M.settleVerdict(M.readSettleFacts(M.REPO, rules, failing(cmpFail, 'compare')), Date.parse(done.at));
  ok(!vc.settled && vc.blockers.some(b => /^unreadable: whether GitRepository flux-system\/haynes-ops \(08f367f6\) contains acaca28d$/.test(b)), 'settle gate: a failed compare of the source revision fails closed', vc.blockers.join(' | '));
  // merge commits and a crowded window
  const base = { cfg: sc, k8s: sx.main.acaca28d.kubernetes, all: sx.main.acaca28d.all, ks: { items: done.flux.kustomizations }, hr: { items: done.flux.helmreleases }, source: done.flux.gitrepository, contains: true };
  const t40 = Date.parse(done.at);
  ok(M.settleVerdict(base, t40).settled, 'the settled counterfactual, as plain facts: settled');
  const mc = structuredClone(base); mc.all[0].parents.push({ sha: 'b'.repeat(40) });
  ok(!M.settleVerdict(mc, Date.parse('2026-11-10T05:20:00Z')).settled && M.settleVerdict(mc, Date.parse('2026-11-10T05:20:00Z')).blockers.some(b => /^main commit acaca28d at 2026-11-10T05:03:25\.000Z has 2 parents, inside the settle window/.test(b)), 'a merge commit inside the window fails closed');
  ok(!M.settleVerdict(mc, t40).blockers.some(b => /parents/.test(b)), 'a merge commit older than the window does not count');
  const np = structuredClone(base); delete np.all[0].parents;
  ok(M.settleVerdict(np, Date.parse('2026-11-10T05:20:00Z')).blockers.some(b => /has unreadable parents/.test(b)), 'unreadable parents inside the window fail closed');
  const crowded = structuredClone(base); crowded.all = Array.from({ length: 20 }, (_, i) => ({ sha: String(i % 10).repeat(40), parents: [{ sha: 'c'.repeat(40) }], commit: { committer: { date: new Date(t40 - (i + 1) * 60e3).toISOString() } } }));
  ok(M.settleVerdict(crowded, t40).blockers.some(b => /^unreadable: main has 20 or more commits inside the settle window/.test(b)), 'twenty commits inside the window: more than one read lists (fail closed)');
  // the revision every Kustomization of the source must have attempted
  const na = structuredClone(base); na.ks.items[0].status.lastAttemptedRevision = 'main@sha1:08f367f6b8d5f87f61a718fc59c0e2dfe4069178';
  ok(M.settleVerdict(na, t40).blockers.some(b => /Kustomization network\/multus: has not attempted acaca28d yet \(last attempted 08f367f6\)/.test(b)), 'a Kustomization of the source that has not attempted its revision is unsettled');
  const ns = structuredClone(na); ns.ks.items[0].spec.suspend = true;
  ok(M.settleVerdict(ns, t40).settled, 'a suspended Kustomization is not waited for');
  const oc = structuredClone(na); oc.ks.items[0].spec.sourceRef = { kind: 'OCIRepository', name: 'flux-manifests', namespace: 'flux-system' };
  ok(M.settleVerdict(oc, t40).settled, 'a Kustomization of another source is not compared with the GitRepository revision');
  const gf = structuredClone(base); gf.source.status.conditions = [{ type: 'Ready', status: 'False', reason: 'GitOperationFailed', lastTransitionTime: '2026-11-09T00:00:00Z' }];
  ok(M.settleVerdict(gf, t40).blockers.some(b => /^GitRepository flux-system\/haynes-ops is NotReady \(GitOperationFailed\)/.test(b)), 'a source that has been failing blocks: nothing it serves can be judged settled');
  ok(!M.settleVerdict({ ...base, contains: false }, t40).settled && !M.settleVerdict({ ...base, contains: null }, t40).settled && !M.settleVerdict({ ...base, contains: undefined }, t40).settled, 'the source must be known to contain the newest change: false, null and undefined all wait');
  const fut = structuredClone(base); fut.k8s[0].commit.committer.date = '2026-11-10T06:00:00Z';
  ok(M.settleVerdict(fut, t40).blockers.some(b => /^main changed kubernetes\/\*\* 0 min ago/.test(b)), 'a commit dated after now (clock skew) waits');
  const late = structuredClone(base); late.k8s[0].commit.author.date = '2026-11-10T05:20:00Z';
  ok(!M.settleVerdict(late, t40).settled, 'the later of committer and author date counts');
}

// ── 4. the lock and the merge call against stub kubectl and gh ─────────────────────────
const tmp = mkdtempSync(join(tmpdir(), 'merge-step-'));
try {
  const bin = join(tmp, 'bin'); mkdirSync(bin);
  const logFile = join(tmp, 'calls.jsonl'), lockFile = join(tmp, 'lock.json'), mergedFile = join(tmp, 'merged.json'), worldFile = join(tmp, 'world.json');
  const stub = `#!/usr/bin/env node
const fs = require('fs'); const path = require('path');
const cmd = path.basename(process.argv[1]); const argv = process.argv.slice(2);
let stdin = ''; try { if (cmd === 'kubectl' && ['create', 'replace'].includes(argv[0])) stdin = fs.readFileSync(0, 'utf8'); } catch {}
fs.appendFileSync(process.env.STUB_LOG, JSON.stringify({ cmd, argv, stdin }) + '\\n');
const w = fs.existsSync(process.env.STUB_WORLD) ? JSON.parse(fs.readFileSync(process.env.STUB_WORLD, 'utf8')) : {};
const out = o => { process.stdout.write(typeof o === 'string' ? o : JSON.stringify(o)); process.exit(0); };
const fail = (m = 'Error from server (Forbidden)') => { process.stderr.write(m); process.exit(1); };
const sleep = ms => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
const LOCKF = process.env.STUB_LOCK, MERGED = process.env.STUB_MERGED;
const lock = () => (fs.existsSync(LOCKF) ? JSON.parse(fs.readFileSync(LOCKF, 'utf8')) : null);
const merged = () => (fs.existsSync(MERGED) ? JSON.parse(fs.readFileSync(MERGED, 'utf8')) : {});
const count = f => { const n = Number(fs.existsSync(LOCKF + f) ? fs.readFileSync(LOCKF + f, 'utf8') : 0) + 1; fs.writeFileSync(LOCKF + f, String(n)); return n; };
if (cmd === 'gh' && argv[0] === 'pr' && argv[1] === 'list') { if (w.slowMs) sleep(w.slowMs); out([]); }
if (cmd === 'gh' && argv[0] === 'pr' && argv[1] === 'merge') {
  fs.writeFileSync(LOCKF + '.at-merge', JSON.stringify(lock()));
  if (w.mergeSleepMs) { sleep(w.mergeSleepMs); process.exit(1); }
  if (w.mergeKilled) process.kill(process.pid, 'SIGKILL');
  if (w.mergeExit) fail('merge refused'); const m = merged(); m[argv[2]] = true; fs.writeFileSync(MERGED, JSON.stringify(m)); out('');
}
if (cmd === 'gh' && argv[0] === 'pr' && argv[1] === 'view') {
  const f = argv[argv.indexOf('--json') + 1] || '';
  if (f.includes('mergedAt')) out(merged()[argv[2]] ? { state: 'MERGED', mergedAt: '2026-11-11T16:00:20Z', headRefOid: w.view.headRefOid, mergeCommit: { oid: 'f'.repeat(40) } } : { state: 'OPEN', mergedAt: null, headRefOid: w.view.headRefOid, mergeCommit: null });
  out(w.view);
}
const api = argv.find(a => a.startsWith('repos/')) || '';
if (cmd === 'gh' && /\\/pulls\\/\\d+\\/commits/.test(api)) out(w.prCommits);
if (cmd === 'gh' && api.includes('contents/.renovate/holds.json5')) { if (w.holdsFail) fail(); out({ content: w.holds }); }
if (cmd === 'gh' && /\\/issues\\/\\d+\\/comments/.test(api)) out([w.comments || []]);
if (cmd === 'gh' && /\\/commits\\/[0-9a-f]{40}$/.test(api)) out('');
if (cmd === 'gh' && /\\/compare\\//.test(api)) { const c = (w.compare || {})[api.split('...')[1].split('?')[0]]; if (!c) fail(); out(c); }
if (cmd === 'gh' && /\\/commits\\?sha=main&/.test(api)) { const q = new URLSearchParams(api.split('?')[1]); const v = q.get('since') ? (w.since || {})[q.get('path')] ?? [] : (w.newest || {})[q.get('path') ?? '(all)']; if (!v || v === 'FAIL') fail(); out(v.map(c => (c.commit ? c : { sha: c.sha, parents: c.parents ?? [{ sha: 'e'.repeat(40) }], commit: { committer: { date: c.at }, author: { date: c.author_at ?? c.at }, message: c.message ?? '' } }))); }
// story 551: the settle gate's Flux reads (any kubectl get that is not a ConfigMap)
if (cmd === 'kubectl' && argv[0] === 'get' && argv[1] !== 'configmaps') { const v = (w.flux || {})[argv[1]]; if (!v || v === 'FAIL') fail(); out(v); }
if (cmd === 'kubectl' && argv[0] === 'get') {
  const name = argv[argv.indexOf('--field-selector') + 1].replace('metadata.name=', '');
  if (w.cmFail) fail();
  let cms = w.cms || [];
  if (name === 'upgrade-lane-state') { const n = count('.state-reads'); if (w.pauseFromRead && n >= w.pauseFromRead) cms = [...cms, { metadata: { name, resourceVersion: '3' }, data: { pause: 'set mid-run' } }]; }
  out({ items: [...cms, ...(lock() ? [lock()] : [])].filter(c => c.metadata.name === name) });
}
if (cmd === 'kubectl' && (argv[0] === 'create' || argv[0] === 'replace')) {
  const n = count('.writes');
  if (w.lockWrite || w.lockWriteFailAt === n) fail();
  const m = JSON.parse(stdin), cur = lock();
  if (argv[0] === 'create' && cur) fail('Error from server (AlreadyExists): configmaps "upgrade-change-lock" already exists');
  if (argv[0] === 'replace' && !cur) fail('Error from server (NotFound)');
  if (argv[0] === 'replace' && cur.metadata.resourceVersion !== m.metadata.resourceVersion) fail('Error from server (Conflict): the object has been modified');
  m.metadata.resourceVersion = String(cur ? Number(cur.metadata.resourceVersion) + 1 : 100);
  fs.writeFileSync(LOCKF, JSON.stringify(m)); out(m);
}
fail('stub: unexpected call ' + cmd + ' ' + argv.join(' '));
`;
  for (const c of ['gh', 'kubectl']) { writeFileSync(join(bin, c), stub); chmodSync(join(bin, c), 0o755); }
  const env = { PATH: `${bin}:${process.env.PATH}`, HOME: tmp, STUB_LOG: logFile, STUB_WORLD: worldFile, STUB_LOCK: lockFile, STUB_MERGED: mergedFile };
  const calls = () => (existsSync(logFile) ? readFileSync(logFile, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse) : []);
  const lockNow = () => (existsSync(lockFile) ? JSON.parse(readFileSync(lockFile, 'utf8')) : null);
  const HOLDS = Buffer.from(readFileSync(join(ROOT, '.renovate/holds.json5'))).toString('base64');
  const atMerge = () => (existsSync(`${lockFile}.at-merge`) ? JSON.parse(readFileSync(`${lockFile}.at-merge`, 'utf8')) : null);
  // story 551: by default main's newest change to kubernetes/** is a day old and Flux is
  // settled on it, so the settle gate passes and every older case keeps its meaning
  const K8S0 = { sha: '9'.repeat(40), at: '2026-11-10T16:00:00Z', message: 'fix(container): an older change to kubernetes/** (#4700)' };
  const fluxReady = t => [{ type: 'Ready', status: 'True', reason: 'ReconciliationSucceeded', lastTransitionTime: t }];
  const SRC = { kind: 'GitRepository', name: 'haynes-ops', namespace: 'flux-system' };
  const settledFlux = (rev = K8S0.sha) => ({
    'gitrepositories.source.toolkit.fluxcd.io': { items: [{ metadata: { namespace: 'flux-system', name: 'haynes-ops', generation: 1 }, status: { observedGeneration: 1, artifact: { revision: `main@sha1:${rev}` }, conditions: fluxReady('2026-11-01T00:00:00Z') } }] },
    'kustomizations.kustomize.toolkit.fluxcd.io': { items: [{ metadata: { namespace: 'network', name: 'multus', generation: 2 }, spec: { sourceRef: SRC }, status: { observedGeneration: 2, lastAttemptedRevision: `main@sha1:${rev}`, conditions: fluxReady('2026-11-01T00:00:00Z') } }] },
    'helmreleases.helm.toolkit.fluxcd.io': { items: [{ metadata: { namespace: 'network', name: 'multus', generation: 4 }, spec: {}, status: { observedGeneration: 4, conditions: fluxReady('2026-11-01T00:00:00Z') } }] },
  });
  const world = (w = {}) => {
    for (const f of [logFile, lockFile, mergedFile, `${lockFile}.at-merge`, `${lockFile}.state-reads`, `${lockFile}.writes`]) rmSync(f, { force: true });
    if (w.lock) writeFileSync(lockFile, JSON.stringify(w.lock));
    writeFileSync(worldFile, JSON.stringify({ view: fx.view, newest: { ...fx.main_newest, kubernetes: [K8S0], '(all)': [K8S0] }, compare: fx.compare, prCommits: fx.pr_commits, comments: fx.comments, holds: HOLDS, cms: [], flux: settledFlux(), ...w }));
  };
  // the read-only runner, as the tick uses it, with the stub binaries on PATH
  const run = (cmd, argv, o = {}) => L.runReadOnly(cmd, argv, { env, ...o });
  const lockOf = (data, rv = '41') => ({ apiVersion: 'v1', kind: 'ConfigMap', metadata: { name: 'upgrade-change-lock', namespace: 'upgrade-agent', resourceVersion: rv }, data });
  const take = (now = NOW, holder = 'upgrade-night/test/1') => M.takeLock({ holder, purpose: 'test', mode: 'dryrun', pr: 1, head: HEAD, now, leaseSeconds: 600, run, env });

  // lockStatus (pure)
  const ls = (cm, now = NOW) => M.lockStatus(cm, now);
  ok(ls(null).state === 'unreadable' && ls({ found: false }).state === 'absent', 'lockStatus: a failed read is unreadable; no object is absent');
  ok(ls({ found: true, cm: lockOf({ holder: '' }) }).state === 'free' && ls({ found: true, cm: lockOf({}) }).state === 'free', 'lockStatus: an empty holder is free');
  ok(ls({ found: true, cm: lockOf({ holder: 'x', expires_at: '2026-11-11T16:05:00Z' }) }).state === 'held', 'lockStatus: a holder before its expiry holds it');
  ok(ls({ found: true, cm: lockOf({ holder: 'x', expires_at: '2026-11-11T15:59:59Z' }) }).state === 'expired' && ls({ found: true, cm: lockOf({ holder: 'x', expires_at: '2026-11-11T16:00:00Z' }) }).state === 'expired', 'lockStatus: a stale lock expires by its lease');
  ok(ls({ found: true, cm: lockOf({ holder: 'x', expires_at: 'tomorrow' }) }).state === 'unreadable' && ls({ found: true, cm: lockOf({ holder: 'x' }) }).state === 'unreadable', 'lockStatus: a holder with no readable expiry fails closed');
  ok(ls({ found: true, cm: { metadata: { name: 'upgrade-change-lock' }, data: {} } }).state === 'unreadable', 'lockStatus: no resourceVersion fails closed');

  // take and release
  world();
  const t1 = take();
  ok(t1.taken && t1.before.state === 'absent' && lockNow()?.data.holder === 'upgrade-night/test/1' && lockNow().data.expires_at === '2026-11-11T16:10:00.000Z' && lockNow().data.phase === 'taken', 'an absent lock is taken by create, with a 600s lease', JSON.stringify(lockNow()?.data));
  const t2 = take(NOW + 60e3, 'upgrade-night/test/2');
  ok(!t2.taken && t2.before.state === 'held' && /held by upgrade-night\/test\/1 \(taken: test\) until 2026-11-11T16:10:00\.000Z/.test(t2.why) && lockNow().data.holder === 'upgrade-night/test/1', 'a second take while it is held is refused and changes nothing', t2.why);
  const r1 = M.releaseLock(t1.held, { now: NOW + 5e3, env });
  ok(r1.released && lockNow().data.holder === '' && lockNow().data.last_holder === 'upgrade-night/test/1' && lockNow().data.phase === 'released', 'release frees it, keeping the last holder');
  const t3 = take(NOW + 10e3, 'upgrade-night/test/3');
  ok(t3.taken && t3.before.state === 'free' && calls().filter(c => c.argv[0] === 'replace').length === 2, 'a free lock is taken by replace at the resourceVersion it read');
  ok(M.releaseLock(t3.held, { now: NOW, env }).released, 'and released again');
  // concurrent takes: the API decides
  world({ lock: lockOf({ holder: 'other', expires_at: '2026-11-11T16:30:00Z' }) });
  const absentRun = (cmd, argv, o) => (cmd === 'kubectl' && argv[0] === 'get' ? JSON.stringify({ items: [] }) : run(cmd, argv, o));
  const tc = M.takeLock({ holder: 'h', purpose: 'p', mode: 'live', now: NOW, leaseSeconds: 600, run: absentRun, env });
  ok(!tc.taken && /create refused: Error from server \(AlreadyExists\)/.test(tc.why) && lockNow().data.holder === 'other', 'a take that raced another create fails with AlreadyExists and holds nothing', tc.why);
  const staleRun = (cmd, argv, o) => (cmd === 'kubectl' && argv[0] === 'get' ? JSON.stringify({ items: [lockOf({ holder: '' }, '7')] }) : run(cmd, argv, o));
  const tr = M.takeLock({ holder: 'h', purpose: 'p', mode: 'live', now: NOW, leaseSeconds: 600, run: staleRun, env });
  ok(!tr.taken && /replace refused: Error from server \(Conflict\)/.test(tr.why) && lockNow().data.holder === 'other', 'a take at an old resourceVersion fails with Conflict and holds nothing', tr.why);
  // a stale lock is taken over by its lease
  world({ lock: lockOf({ holder: 'upgrade-night/crashed/9', phase: 'taken', expires_at: '2026-11-11T15:55:00Z' }) });
  const tx = take();
  ok(tx.taken && tx.before.state === 'expired' && lockNow().data.took_over_from === 'upgrade-night/crashed/9' && lockNow().data.took_over_phase === 'taken' && lockNow().data.holder === 'upgrade-night/test/1', 'a lock past its expiry is taken over, naming the crashed holder and its phase');
  M.releaseLock(tx.held, { now: NOW, env });
  world({ lock: lockOf({ holder: 'upgrade-night/merged/4', phase: 'merging', expires_at: '2026-11-11T15:00:00Z' }) });
  const tm = take();
  ok(tm.taken && lockNow().data.took_over_phase === 'merging', 'round 2, finding 1: taking over an expired merging lease records took_over_phase merging');
  M.releaseLock(tm.held, { now: NOW, env });
  ok(lockNow().data.phase === 'released' && lockNow().data.last_took_over_phase === 'merging' && lockNow().data.last_took_over_from === 'upgrade-night/merged/4', 'round 2, finding 1: the release keeps last_took_over_phase, so a merge left unverified stays visible', JSON.stringify(lockNow().data));
  world({ cmFail: true });
  const tu = take();
  ok(!tu.taken && tu.before.state === 'unreadable' && !calls().some(c => ['create', 'replace'].includes(c.argv[0])), 'an unreadable lock is never written');
  world({ lockWrite: true });
  const tf = take();
  ok(!tf.taken && /create refused: Error from server \(Forbidden\)/.test(tf.why), 'a write the API refuses: not taken, the reason recorded');
  // a release that lost the lock does not overwrite the new holder
  world();
  const tl = take();
  const cur = lockNow(); cur.data.holder = 'someone-else'; cur.metadata.resourceVersion = '500'; writeFileSync(lockFile, JSON.stringify(cur));
  const rl = M.releaseLock(tl.held, { now: NOW, env });
  ok(!rl.released && /release was not written \(Error from server \(Conflict\)/.test(rl.why) && lockNow().data.holder === 'someone-else', "a release after the lock changed hands does not overwrite the new holder", rl.why);
  // writeLock refuses anything but the lock
  const src = readFileSync(join(RES, 'night-merge.mjs'), 'utf8');
  ok(/if \(!\['create', 'replace'\]\.includes\(verb\) \|\| manifest\?\.kind !== 'ConfigMap' \|\| m\.name !== LOCK\.name \|\| m\.namespace !== LOCK\.namespace/.test(src) && /\(verb === 'replace' && !m\.resourceVersion\)\) \{\s*throw/.test(src), 'writeLock refuses any verb, kind, name or namespace but the lock, and a replace without a resourceVersion');

  // the lane's merge step releases the lock on every exit path
  const OPEN = () => ({ open: true, local: '02:00' });
  const base = { rules, env, clock: () => NOW, liveClasses: [], token: M.tokenPermissions('{"contents":"read"}'), holder: 'upgrade-night/test/lane', takeAllowed: true, windowOpen: OPEN, tickStartedAt: NOW };
  const subject = { pr: 3915, head: HEAD, class: 'no-cluster-effect', chosen: true };
  world();
  const s1 = M.laneMergeStep({ ...base, mode: 'dryrun', subject, run });
  ok(s1.lock.outcome === 'taken-and-released' && s1.verdict.verdict === 'refused' && s1.verdict.pr_ready === true && lockNow().data.holder === '' && !calls().some(c => c.argv[1] === 'merge'), 'dry-run step on PR 3915: PR ready, refused for the lane (class, token), lock taken and released, no merge', JSON.stringify(s1.verdict.lane_refusals));
  world();
  // a crash after the take and the PR reads (here: inside the class check)
  const boom = { includes() { throw new Error('simulated crash in the merge step'); } };
  let threw = null;
  try { M.laneMergeStep({ ...base, mode: 'live', liveClasses: boom, subject, run }); } catch (e) { threw = e.message; }
  ok(threw === 'simulated crash in the merge step' && lockNow()?.data.holder === '' && lockNow().data.phase === 'released', 'an exception inside the merge step still releases the lock (finally)', `${threw} ${JSON.stringify(lockNow()?.data)}`);
  world();
  const s2 = M.laneMergeStep({ ...base, mode: 'live', liveClasses: ['no-cluster-effect'], subject, run });
  ok(s2.verdict.verdict === 'refused' && has(s2.verdict.lane_refusals, /token cannot merge/) && lockNow().data.holder === '' && !calls().some(c => c.argv[1] === 'merge'), 'live with a read-only token: refused, released, no merge call');
  world();
  const tokW = M.tokenPermissions('{"contents":"write","pull_requests":"write","checks":"read"}');
  const s3 = M.laneMergeStep({ ...base, mode: 'live', liveClasses: ['no-cluster-effect'], token: tokW, subject, run });
  const mc = calls().filter(c => c.cmd === 'gh' && c.argv[1] === 'merge');
  ok(s3.verdict.verdict === 'merged' && mc.length === 1 && JSON.stringify(mc[0].argv) === JSON.stringify(M.mergeArgv(M.REPO, 3915, HEAD)) && lockNow().data.phase === 'merged-awaiting-verification' && lockNow().data.holder === 'upgrade-night/test/lane' && lockNow().data.expires_at === '2026-11-11T20:00:00.000Z', 'live, clean: one pinned merge call; the lock stays held for the 4h post-merge lease', JSON.stringify(lockNow()?.data));
  world();
  const s4 = M.laneMergeStep({ ...base, mode: 'live', liveClasses: ['no-cluster-effect'], token: tokW, subject, run, clock: (() => { let n = 0; return () => NOW + (n++ > 1 ? 400e3 : 0); })() });
  ok(s4.verdict.verdict === 'refused' && has(s4.verdict.lane_refusals, /has 200s of lease left, under 240s/) && lockNow().data.holder === '' && !calls().some(c => c.argv[1] === 'merge'), 'live: not merging on a lease about to lapse', JSON.stringify(s4.verdict.lane_refusals));
  world({ cms: [{ metadata: { name: 'upgrade-lane-state', resourceVersion: '3' }, data: { pause: 'tom testing' } }] });
  const s5 = M.laneMergeStep({ ...base, mode: 'live', liveClasses: ['no-cluster-effect'], token: tokW, subject, run });
  ok(s5.verdict.verdict === 'refused' && has(s5.verdict.pr_refusals, /^paused before the lock take: data\.pause="tom testing"$/) && !calls().some(c => ['create', 'replace'].includes(c.argv[0]) || c.argv[1] === 'merge'), 'a set pause: no lock take and no merge');
  world({ cmFail: true });
  const s6 = M.laneMergeStep({ ...base, mode: 'live', liveClasses: ['no-cluster-effect'], token: tokW, subject, run });
  ok(s6.verdict.verdict === 'refused' && has(s6.verdict.pr_refusals, /unreadable: data\.pause/) && !calls().some(c => ['create', 'replace'].includes(c.argv[0]) || c.argv[1] === 'merge'), 'an unreadable pause fails closed: no lock take and no merge');
  world();
  const s7 = M.laneMergeStep({ ...base, mode: 'live', liveClasses: ['no-cluster-effect'], token: tokW, subject, run, takeAllowed: false });
  ok(s7.verdict.verdict === 'refused' && has(s7.verdict.pr_refusals, /no change lock held: a local run \(no --write-state\) never takes it/) && !calls().some(c => ['create', 'replace'].includes(c.argv[0]) || c.argv[1] === 'merge'), 'a local live run never takes the lock and so never merges');

  // the independent review of PR 4827, in process: each guard refuses without help from the others
  const live = { ...base, mode: 'live', liveClasses: ['no-cluster-effect'], token: tokW, subject };
  const noMerge = () => !calls().some(c => c.cmd === 'gh' && c.argv[1] === 'merge');
  // finding 1: pause set while the step reads the PR (the reviewer's probe)
  world();
  let pausedNow = false;
  const probe = (cmd, argv, o) => {
    if (cmd === 'kubectl' && argv.includes('metadata.name=upgrade-lane-state')) return JSON.stringify({ items: pausedNow ? [{ metadata: { name: 'upgrade-lane-state', resourceVersion: '3' }, data: { pause: 'tom' } }] : [] });
    if (cmd === 'gh' && argv[1] === 'view' && !argv.join(' ').includes('mergedAt')) pausedNow = true;
    return run(cmd, argv, o);
  };
  const f1 = M.laneMergeStep({ ...live, run: probe });
  ok(f1.verdict.verdict === 'refused' && has(f1.verdict.pr_refusals, /^paused just before the merge call: data\.pause="tom"$/) && noMerge() && lockNow().data.holder === '', "finding 1: a pause set during the PR read stops the merge (the reviewer's probe); lock released", JSON.stringify(f1.verdict.pr_refusals));
  // finding 2: the window and the age of the gates, at the merge call
  world();
  const f2a = M.laneMergeStep({ ...live, run, windowOpen: () => ({ open: false, local: '05:46' }) });
  ok(f2a.verdict.verdict === 'refused' && has(f2a.verdict.lane_refusals, /^the merge window is closed at the merge call \(now 05:46\)$/) && noMerge() && lockNow().data.holder === '', 'finding 2: a merge window closed at the call refuses (the stalled 04:45 tick)');
  world();
  const f2b = M.laneMergeStep({ ...live, run, windowOpen: undefined });
  ok(has(f2b.verdict.lane_refusals, /unreadable: whether the merge window is open at the merge call/) && noMerge(), 'finding 2: no window check given refuses (fail closed)');
  world();
  const f2c = M.laneMergeStep({ ...live, run, tickStartedAt: NOW - 301e3 });
  ok(has(f2c.verdict.lane_refusals, /^the tick's gates are 301s old at the merge call, over 300s$/) && noMerge(), "finding 2: gates older than 300s at the call refuse", JSON.stringify(f2c.verdict.lane_refusals));
  world();
  const f2d = M.laneMergeStep({ ...live, run, tickStartedAt: undefined });
  ok(has(f2d.verdict.lane_refusals, /unreadable: when the tick began/) && noMerge(), 'finding 2: an unknown tick start refuses');
  world();
  const f2e = M.laneMergeStep({ ...live, run, tickStartedAt: NOW - 299e3 });
  ok(f2e.verdict.verdict === 'merged', 'finding 2: gates 299s old still merge (the limit is 300s)', JSON.stringify(f2e.verdict.lane_refusals));
  // finding 3: the post-merge lease is in place before the call; a call with no exit status keeps it
  world();
  const f3 = M.laneMergeStep({ ...live, run });
  ok(f3.verdict.verdict === 'merged' && atMerge()?.data.phase === 'merging' && atMerge().data.expires_at === '2026-11-11T20:00:00.000Z' && atMerge().data.holder === 'upgrade-night/test/lane', 'finding 3: the lock reads phase merging with the 4h lease at the moment of the merge call', JSON.stringify(atMerge()?.data));
  world({ mergeKilled: true });
  const f3k = M.laneMergeStep({ ...live, run });
  ok(f3k.verdict.verdict === 'merge-unknown' && f3k.verdict.merged?.state === 'OPEN' && lockNow().data.holder === 'upgrade-night/test/lane' && lockNow().data.phase === 'merge-unknown' && lockNow().data.expires_at === '2026-11-11T20:00:00.000Z', 'finding 3: a merge call that ends with no exit status is merge-unknown and keeps the 4h hold, even when the PR reads OPEN', `${f3k.verdict.verdict} ${f3k.verdict.merge_call} ${JSON.stringify(lockNow()?.data)}`);
  ok(M.mergeOutcome({ exit: null }, { state: 'OPEN' }) === 'merge-unknown' && M.mergeOutcome({ exit: 1 }, { state: 'OPEN' }) === 'merge-failed' && M.mergeOutcome({ exit: 1 }, null) === 'merge-unknown' && M.mergeOutcome({ exit: 0 }, { merged: true }) === 'merged' && M.mergeOutcome({ exit: 0 }, { state: 'OPEN' }) === 'merge-unknown', 'mergeOutcome: only a real exit code with the PR open is a failure');
  world({ lockWriteFailAt: 2 });
  const f3w = M.laneMergeStep({ ...live, run });
  ok(f3w.verdict.verdict === 'refused' && has(f3w.verdict.lane_refusals, /could not be moved to phase merging/) && noMerge(), 'finding 3: when the merging phase cannot be written the lane does not merge');

  // finding 2: every read has a deadline
  world({ slowMs: 5000 });
  const t0 = Date.now();
  const slow = L.runReadOnly('gh', ['pr', 'list', '-R', 'x/y'], { env, allowFail: true, timeoutMs: 400 });
  ok(slow === null && Date.now() - t0 < 3000, 'finding 2: a read that stalls is killed at its deadline and counts as failed', `${slow} after ${Date.now() - t0}ms`);
  let tmsg = '';
  try { L.runReadOnly('gh', ['pr', 'list', '-R', 'x/y'], { env, timeoutMs: 400 }); } catch (e) { tmsg = e.message; }
  ok(/timed out or killed after 400ms \(SIGKILL\)/.test(tmsg), 'finding 2: a stalled read without allowFail throws, naming the timeout', tmsg);
  ok(L.READ_TIMEOUT_MS === 120e3 && /timeout: timeoutMs, killSignal: 'SIGKILL'/.test(readFileSync(join(RES, 'lane-classify.mjs'), 'utf8')), 'runReadOnly passes a 120s default deadline to spawnSync');

  // callMerge refuses before it spawns
  world();
  const refuse = args => { try { M.callMerge({ repo: M.REPO, pr: 3915, head: HEAD, verdict: { ok: true, pr: 3915, head: HEAD }, lockHeld: true, env, ...args }); return null; } catch (e) { return e.message; } };
  ok(/mode dryrun never merges/.test(refuse({ mode: 'dryrun' })) && /mode undefined never merges/.test(refuse({ mode: undefined })) && /mode check-only never merges/.test(refuse({ mode: 'check-only' })), 'callMerge refuses dryrun, a missing mode and check-only');
  ok(/no clean verdict/.test(refuse({ mode: 'live', verdict: { ok: false, pr: 3915, head: HEAD } })) && /no clean verdict/.test(refuse({ mode: 'live', verdict: { ok: true, pr: 3915, head: 'b'.repeat(40) } })), 'callMerge refuses without a clean verdict for this PR and head');
  ok(/does not hold the change lock/.test(refuse({ mode: 'live', lockHeld: false })), 'callMerge refuses a live merge without the lock');
  ok(/unknown repository/.test(refuse({ mode: 'live', repo: 'thaynes43/dev-env' })), 'callMerge refuses another repository');
  ok(calls().length === 0, 'none of those refusals started a process');

  // the exit and SIGTERM paths, in a child process that takes the lock
  const child = (script, w = {}) => { world(w); return spawnSync(process.execPath, ['--input-type=module', '-e', script], { env: { ...env, M_PATH: join(RES, 'night-merge.mjs'), L_PATH: join(RES, 'lane-classify.mjs') }, encoding: 'utf8', timeout: 30000 }); };
  const prelude = `const M = await import(process.env.M_PATH); const L = await import(process.env.L_PATH);
const run = (c, a, o = {}) => L.runReadOnly(c, a, { env: process.env, ...o });
const t = M.takeLock({ holder: 'child', purpose: 'exit test', mode: 'live', now: Date.now(), leaseSeconds: 600, run, env: process.env });
if (!t.taken) { console.log('NOT TAKEN ' + t.why); process.exit(9); }`;
  const ex = child(`${prelude}\nprocess.exit(5);`);
  ok(ex.status === 5 && lockNow()?.data.holder === '' && /released on process exit/.test(lockNow()?.data.last_note || ''), 'a process that exits while holding the lock releases it', `${ex.status} ${ex.stdout}${ex.stderr} ${JSON.stringify(lockNow()?.data)}`);
  const th = child(`${prelude}\nthrow new Error('uncaught');`);
  ok(th.status !== 0 && lockNow()?.data.holder === '', 'an uncaught exception after a take releases the lock', `${th.status} ${JSON.stringify(lockNow()?.data)}`);
  const hm = child(`${prelude}\nM.holdAfterMerge(t.held, { now: Date.now(), env: process.env, leaseSeconds: 14400, merged: { merge_commit: 'f'.repeat(40) } });\nprocess.exit(0);`);
  ok(hm.status === 0 && lockNow()?.data.holder === 'child' && lockNow().data.phase === 'merged-awaiting-verification' && lockNow().data.merge_commit === 'f'.repeat(40), 'after a merge the exit handler keeps the lock held', JSON.stringify(lockNow()?.data));
  world();
  const sig = await new Promise(resolve => {
    const p = spawn(process.execPath, ['--input-type=module', '-e', `${prelude}\nconsole.log('TAKEN'); setInterval(() => {}, 1000);`], { env: { ...env, M_PATH: join(RES, 'night-merge.mjs'), L_PATH: join(RES, 'lane-classify.mjs') } });
    let outp = '';
    const timer = setTimeout(() => p.kill('SIGKILL'), 30000);
    p.stdout.on('data', d => { outp += d; if (/TAKEN/.test(outp)) p.kill('SIGTERM'); });
    p.on('close', (code, signal) => { clearTimeout(timer); resolve({ code, signal, outp }); });
  });
  ok(sig.code === 143 && lockNow()?.data.holder === '' && /released on process SIGTERM/.test(lockNow()?.data.last_note || ''), 'SIGTERM while holding the lock releases it and exits 143', `${JSON.stringify(sig)} ${JSON.stringify(lockNow()?.data)}`);

  // finding 3: a kill while the merge call runs leaves the 4h hold (the post-merge lease was written first)
  world({ mergeSleepMs: 8000 });
  const killed = await new Promise(resolve => {
    const script = `const M = await import(process.env.M_PATH); const L = await import(process.env.L_PATH);
const run = (c, a, o = {}) => L.runReadOnly(c, a, { env: process.env, ...o });
const rules = L.loadRules(process.env.RULES_PATH);
M.laneMergeStep({ mode: 'live', subject: { pr: 3915, head: '${HEAD}', class: 'no-cluster-effect', chosen: true }, rules, run, env: process.env, clock: () => ${NOW},
  liveClasses: ['no-cluster-effect'], token: M.tokenPermissions('{"contents":"write","pull_requests":"write","checks":"read"}'), holder: 'child-merge', takeAllowed: true,
  windowOpen: () => ({ open: true, local: '02:00' }), tickStartedAt: ${NOW} });`;
    const p = spawn(process.execPath, ['--input-type=module', '-e', script], { env: { ...env, M_PATH: join(RES, 'night-merge.mjs'), L_PATH: join(RES, 'lane-classify.mjs'), RULES_PATH: join(RES, 'upgrade-rules.json') }, detached: true });
    const stop = setTimeout(() => { try { process.kill(-p.pid, 'SIGKILL'); } catch {} }, 30000);
    const poll = setInterval(() => { if (existsSync(`${lockFile}.at-merge`)) { clearInterval(poll); try { process.kill(-p.pid, 'SIGKILL'); } catch {} } }, 100);
    p.on('close', (code, signal) => { clearTimeout(stop); clearInterval(poll); resolve({ code, signal }); });
  });
  ok(killed.signal === 'SIGKILL' && lockNow()?.data.holder === 'child-merge' && lockNow().data.phase === 'merging' && lockNow().data.expires_at === '2026-11-11T20:00:00.000Z', 'finding 3: SIGKILL during the merge call leaves the lock in phase merging with the 4h lease', `${JSON.stringify(killed)} ${JSON.stringify(lockNow()?.data)}`);

  // ── 5. the hand-run CLI ─────────────────────────────────────────────────────────
  // The CLI refuses --merge on any test clock (UPGRADE_NIGHT_NOW, FAKE_NOW, a replaced
  // Date.now), so a real merge through the CLI is tested only for those refusals; the merge
  // path runs in process through handRun() with an explicit clock (hand() below).
  const FAKE_CLOCK = join(ROOT, 'scripts/upgrade-agent/test-fake-clock.mjs');
  const cli = (args, w = {}, { pre = [], extra = {} } = {}) => {
    world(w);
    const r = spawnSync(process.execPath, [...pre, join(RES, 'night-merge.mjs'), ...args], { env: { ...env, UPGRADE_NIGHT_NOW: new Date(NOW).toISOString(), ...extra }, encoding: 'utf8', timeout: 30000 });
    return { ...r, calls: calls(), writes: calls().filter(c => !L.isReadOnlyCommand(c.cmd, c.argv)) };
  };
  const hand = (w = {}) => { world(w); const r = M.handRun({ pr: 3915, head: HEAD, merge: true }, { rules, run, env, now: NOW }); return { r, calls: calls() }; };
  const CHECK = ['--pr', '3915', '--head', HEAD, '--check-only'], MERGE = ['--pr', '3915', '--head', HEAD, '--merge'];
  const c1 = cli(CHECK);
  ok(c1.status === 0 && /^merge step \(hand --check-only\) #3915 head a7174f4e: WOULD-MERGE$/m.test(c1.stdout) && /merge call: not made \(--check-only\)/.test(c1.stdout) && c1.writes.length === 0, '--check-only on PR 3915 as read 2026-11-11: WOULD-MERGE, exit 0, nothing written', c1.stdout + c1.stderr);
  ok(c1.calls.every(c => L.isReadOnlyCommand(c.cmd, c.argv)), '--check-only makes reads only');
  const behind = { newest: { ...fx.main_newest, [GO_MOD]: [{ sha: '2'.repeat(40), at: '2026-11-11T08:00:00Z' }] }, compare: { ...fx.compare, ['2'.repeat(40)]: { ahead_by: 1 } }, since: { [GO_MOD]: [{ sha: '2'.repeat(40), at: '2026-11-11T08:00:00Z' }] } };
  const c2 = cli(CHECK, behind);
  ok(c2.status === 1 && /: REFUSED$/m.test(c2.stdout) && /refused: checks predate main: scripts\/tests\/dev-env-archive-guard\/go\.mod changed by 22222222/.test(c2.stdout) && /refused: head is behind main: scripts\/tests\/dev-env-archive-guard\/go\.mod changed by 22222222/.test(c2.stdout) && c2.writes.length === 0, '--check-only on a stale, behind head: REFUSED with both reasons, exit 1', c2.stdout);
  const c3 = cli(CHECK, { lock: lockOf({ holder: 'upgrade-night/x/1', phase: 'taken', expires_at: '2026-11-11T16:08:00Z' }) });
  ok(c3.status === 1 && /refused: the change lock is held by upgrade-night\/x\/1/.test(c3.stdout) && c3.writes.length === 0, '--check-only refuses while the lock is held', c3.stdout);
  const c4 = cli(CHECK, { cms: [{ metadata: { name: 'upgrade-lane-state', resourceVersion: '3' }, data: { pause: 'tom' } }] });
  ok(c4.status === 1 && /refused: paused at the start: data\.pause="tom"/.test(c4.stdout), '--check-only refuses while the lane is paused', c4.stdout);
  const c5 = hand();
  const m5 = c5.calls.filter(c => c.cmd === 'gh' && c.argv[1] === 'merge');
  ok(c5.r.verdict === 'merged' && m5.length === 1 && JSON.stringify(m5[0].argv) === JSON.stringify(M.mergeArgv(M.REPO, 3915, HEAD)) && !c5.calls.some(c => c.cmd === 'kubectl' && c.argv[0] !== 'get'), '--merge on a clean verdict: one pinned squash merge, verified; the lock is not written', JSON.stringify(c5.r.refusals));
  const c6 = hand(behind);
  ok(c6.r.verdict === 'refused' && c6.r.merge_call === 'not made (refused)' && !c6.calls.some(c => c.argv[1] === 'merge'), '--merge on a refused verdict: no merge call', JSON.stringify(c6.r.refusals));
  const c7 = hand({ mergeExit: true });
  ok(c7.r.verdict === 'merge-failed', '--merge whose call fails with the PR still open: merge-failed', c7.r.verdict);
  // finding 4: the hand run classifies the PR as the lane does and refuses anything but LANE-A
  ok(/^  lane: LANE-A \(no-cluster-effect\): /m.test(c1.stdout) && c1.calls.some(c => c.cmd === 'gh' && c.argv.some(a => a.includes('contents/.renovate/holds.json5'))) && c1.calls.some(c => c.cmd === 'gh' && c.argv.some(a => /issues\/3915\/comments/.test(a))), 'finding 4: --check-only prints the lane, from the classifier with holds and comments read', c1.stdout);
  const withView = f => ({ view: (() => { const v = structuredClone(fx.view); f(v); return v; })() });
  const major = withView(v => { v.labels = [{ name: 'type/major' }]; });
  const k1 = cli(CHECK, major);
  ok(k1.status === 1 && /refused: lane LANE-B-OWNER \(major\): update type major/.test(k1.stdout), 'finding 4: a major update is refused by its lane', k1.stdout);
  const k2 = cli(CHECK, { comments: [{ user: { login: 'haynes-ops-bot[bot]' }, body: `<!-- shepherd-vet sha=${HEAD} -->\n\nVERDICT: HOLD - needs a person` }] });
  ok(k2.status === 1 && /refused: lane LANE-B-OWNER \(vet-hold\)/.test(k2.stdout), 'finding 4: a current-head vet HOLD by the trusted bot is refused', k2.stdout);
  const k3 = cli(CHECK, withView(v => { v.files.push({ path: 'kubernetes/main/apps/dev-env-system/keeper/app/helmrelease.yaml' }); v.changedFiles = 3; }));
  ok(k3.status === 1 && /refused: lane EXCLUDED/.test(k3.stdout), 'finding 4: a PR touching an excluded path is refused', k3.stdout);
  const k4 = cli(CHECK, { holdsFail: true });
  ok(k4.status === 1 && /refused: unreadable: \.renovate\/holds\.json5 on main/.test(k4.stdout), 'finding 4: unreadable holds refuse (fail closed)', k4.stdout);
  const k5 = hand(major);
  ok(k5.r.verdict === 'refused' && !k5.calls.some(c => c.argv[1] === 'merge') && has(k5.r.refusals, /^lane LANE-B-OWNER/), 'finding 4: --merge on a major update makes no merge call', JSON.stringify(k5.r.refusals));
  // finding 1, hand path: a pause that appears between the first read and the merge call
  const k6 = hand({ pauseFromRead: 2 });
  ok(k6.r.verdict === 'refused' && has(k6.r.refusals, /^paused just before the merge call: data\.pause="set mid-run"$/) && !k6.calls.some(c => c.argv[1] === 'merge'), 'finding 1: --merge re-reads the pause just before the call and refuses', JSON.stringify(k6.r.refusals));
  // finding 6 and round 2, finding 3: --merge never runs on a test clock
  const k7 = cli(MERGE);
  ok(k7.status === 2 && /--merge refuses a pinned clock \(UPGRADE_NIGHT_NOW is set\)/.test(k7.stderr) && k7.calls.length === 0, 'finding 6: --merge with UPGRADE_NIGHT_NOW set exits 2 before any call', `${k7.status} ${k7.stderr}`);
  const k8 = cli(MERGE, {}, { pre: ['--import', FAKE_CLOCK], extra: { UPGRADE_NIGHT_NOW: '', FAKE_NOW: new Date(NOW).toISOString() } });
  ok(k8.status === 2 && /--merge refuses a pinned clock \(FAKE_NOW is set\)/.test(k8.stderr) && k8.calls.length === 0, 'round 2, finding 3: --merge with the selftest clock preload (FAKE_NOW) exits 2 before any call', `${k8.status} ${k8.stderr}`);
  const k9 = cli(MERGE, {}, { extra: { UPGRADE_NIGHT_NOW: '', NODE_OPTIONS: '--import=data:text/javascript,Date.now=()=>1794400000000' } });
  ok(k9.status === 2 && /--merge refuses a pinned clock \(Date\.now is not the native clock\)/.test(k9.stderr) && k9.calls.length === 0, 'round 2, finding 3: --merge with any replaced Date.now (here through NODE_OPTIONS) exits 2 before any call', `${k9.status} ${k9.stderr}`);
  const k10 = cli([...CHECK], {}, { pre: ['--import', FAKE_CLOCK], extra: { UPGRADE_NIGHT_NOW: '', FAKE_NOW: new Date(NOW).toISOString() } });
  ok(k10.status === 0 && /WOULD-MERGE/.test(k10.stdout), '--check-only still runs on the preload (it changes nothing)', k10.stdout + k10.stderr);
  const c8 = cli([...CHECK, '--json']);
  let j8 = null; try { j8 = JSON.parse(c8.stdout); } catch {}
  ok(c8.status === 0 && j8?.verdict === 'would-merge' && j8.ok === true && j8.facts?.reference_time === '2026-11-10T00:10:58.000Z', '--json prints the verdict as JSON', c8.stdout.slice(0, 300));
  for (const [args, why] of [[['--pr', '3915', '--check-only'], 'no --head'], [['--pr', '3915', '--head', HEAD], 'neither mode'], [['--pr', '3915', '--head', HEAD, '--check-only', '--merge'], 'both modes'],
    [['--pr', 'x', '--head', HEAD, '--check-only'], 'a bad PR number'], [['--pr', '3915', '--head', HEAD.toUpperCase(), '--check-only'], 'an uppercase SHA'], [['--pr', '3915', '--head', HEAD, '--check-only', '--admin'], 'an unknown flag']]) {
    const r = cli(args);
    ok(r.status === 2 && /usage: night-merge\.mjs/.test(r.stderr) && r.calls.length === 0, `usage error (${why}): exit 2 before any call`, `${r.status} ${r.stderr}`);
  }

  // ── 6. the settle gate through the merge step and the hand run (story 551) ──────────
  const recent = (min, sha = '7'.repeat(40)) => ({ sha, at: new Date(NOW - min * 60e3).toISOString(), message: `fix(container): another actor's merge ${min} min ago` });
  const unsettled = { newest: { ...fx.main_newest, kubernetes: [recent(10)], '(all)': [recent(10), K8S0] }, flux: settledFlux('7'.repeat(40)) };
  // dry-run: the step records the settle verdict; the PR itself stays ready
  world(unsettled);
  const g1 = M.laneMergeStep({ ...base, mode: 'dryrun', subject, run });
  ok(g1.verdict.verdict === 'refused' && g1.verdict.pr_ready === true && has(g1.verdict.lane_refusals, /^settle gate: main changed kubernetes\/\*\* 10 min ago \(77777777 at 2026-11-11T15:50:00\.000Z: fix\(container\): another actor's merge 10 min ago\); no lane merge before 2026-11-11T16:20:00\.000Z, 30 min after it$/) && g1.settle?.settled === false && lockNow().data.holder === '', 'dry-run step: a merge to kubernetes/** 10 min ago is a lane refusal; the PR stays ready; the lock is released', JSON.stringify(g1.verdict.lane_refusals));
  world();
  const g2 = M.laneMergeStep({ ...base, mode: 'dryrun', subject, run });
  ok(g2.settle?.settled === true && !g2.verdict.lane_refusals.some(x => /settle gate/.test(x)), 'dry-run step on a settled world: no settle refusal, recorded settled', JSON.stringify(g2.settle));
  // live: unsettled at the step read
  world(unsettled);
  const g3 = M.laneMergeStep({ ...live, run });
  ok(g3.verdict.verdict === 'refused' && has(g3.verdict.lane_refusals, /^settle gate: main changed kubernetes/) && noMerge() && lockNow().data.holder === '', 'live: unsettled at the step: refused, no merge call, lock released', JSON.stringify(g3.verdict.lane_refusals));
  // live: settled at the step, then another actor merges before the call (the re-read)
  world();
  let k8sReads = 0;
  const lateMerge = (cmd, argv, o) => {
    const api = argv.find(a => String(a).startsWith('repos/')) || '';
    if (cmd === 'gh' && /\/commits\?sha=main&path=kubernetes&/.test(api) && ++k8sReads >= 2) return JSON.stringify([{ sha: '6'.repeat(40), parents: [{ sha: '9'.repeat(40) }], commit: { committer: { date: new Date(NOW - 30e3).toISOString() }, author: { date: new Date(NOW - 30e3).toISOString() }, message: 'feat(container): the shepherd merged meanwhile' } }]);
    return run(cmd, argv, o);
  };
  const g4 = M.laneMergeStep({ ...live, run: lateMerge });
  ok(g4.verdict.verdict === 'refused' && k8sReads === 2 && has(g4.verdict.lane_refusals, /^settle gate at the merge call: main changed kubernetes\/\*\* 0 min ago \(66666666 /) && g4.verdict.settle_at_merge_call?.settled === false && noMerge() && lockNow().data.holder === '', 'live: a merge by another actor between the step read and the call is caught at the call; no merge, lock released', JSON.stringify(g4.verdict.lane_refusals));
  // live: Flux starts progressing between the step read and the call
  world();
  let ksReads = 0;
  const lateRoll = (cmd, argv, o) => {
    if (cmd === 'kubectl' && argv[1] === 'helmreleases.helm.toolkit.fluxcd.io' && ++ksReads >= 2) return JSON.stringify({ items: [{ metadata: { namespace: 'network', name: 'multus', generation: 5 }, status: { observedGeneration: 4, conditions: fluxReady('2026-11-01T00:00:00Z') } }] });
    return run(cmd, argv, o);
  };
  const g5 = M.laneMergeStep({ ...live, run: lateRoll });
  ok(g5.verdict.verdict === 'refused' && has(g5.verdict.lane_refusals, /^settle gate at the merge call: Flux not settled: HelmRelease network\/multus: generation 5 not observed yet \(observed 4\)$/) && noMerge(), 'live: a HelmRelease that starts progressing before the call refuses it', JSON.stringify(g5.verdict.lane_refusals));
  // the re-check sits before beginMerge and the call, after the window and age checks
  {
    const lm = readFileSync(join(RES, 'night-merge.mjs'), 'utf8').replace(/^\s*\/\/.*$/gm, '');
    const body = lm.slice(lm.indexOf('export function laneMergeStep('), lm.indexOf('export function parseCli('));
    const i = k => body.indexOf(k);
    ok(i('settleCheck({ rules, run, now: clock() });\n    laneRef.push') > 0 && i('settleCheck({ rules, run, now: clock() });\n    laneRef.push') < i("if (mode !== 'live') {"), 'night-merge.mjs: the step reads the settle gate before its dry-run return');
    const second = body.indexOf('settleCheck(', body.indexOf("if (mode !== 'live') {"));
    ok(second > 0 && second > i('the merge window is closed at the merge call') && second < i('const begin = beginMerge(') && second < i('callMerge({'), 'night-merge.mjs: live mode re-reads the settle gate after the window and age checks, before beginMerge and the call');
  }
  // the hand run: at its start and again just before a --merge call
  const h1 = cli(CHECK, unsettled);
  ok(h1.status === 1 && /: REFUSED$/m.test(h1.stdout) && /refused: settle gate: main changed kubernetes\/\*\* 10 min ago/.test(h1.stdout) && /settle gate: NOT settled: main changed kubernetes/.test(h1.stdout) && h1.writes.length === 0, '--check-only within 30 min of another merge: REFUSED with the settle reason, exit 1', h1.stdout);
  const h2 = cli(CHECK);
  ok(h2.status === 0 && /settle gate: settled \(main's newest change to kubernetes\/\*\* 99999999 at 2026-11-10T16:00:00\.000Z, 1440 min ago; Flux at 99999999; nothing progressing\)/.test(h2.stdout), '--check-only on a settled world prints the settle line', h2.stdout);
  world();
  let hReads = 0;
  const handLate = (cmd, argv, o) => {
    const api = argv.find(a => String(a).startsWith('repos/')) || '';
    if (cmd === 'gh' && /\/commits\?sha=main&path=kubernetes&/.test(api) && ++hReads >= 2) return JSON.stringify([{ sha: '5'.repeat(40), parents: [{ sha: '9'.repeat(40) }], commit: { committer: { date: new Date(NOW - 60e3).toISOString() }, author: { date: new Date(NOW - 60e3).toISOString() }, message: 'renovate merged meanwhile' } }]);
    return run(cmd, argv, o);
  };
  const h3 = M.handRun({ pr: 3915, head: HEAD, merge: true }, { rules, run: handLate, env, now: NOW, clock: () => NOW });
  ok(h3.verdict === 'refused' && hReads === 2 && has(h3.refusals, /^settle gate just before the merge call: main changed kubernetes\/\*\* 1 min ago \(55555555 /) && !calls().some(c => c.cmd === 'gh' && c.argv[1] === 'merge'), 'hand --merge: a merge by another actor after the start is caught just before the call; no merge', JSON.stringify(h3.refusals));
  world(unsettled);
  const h4 = M.handRun({ pr: 3915, head: HEAD, merge: true }, { rules, run, env, now: NOW, clock: () => NOW });
  ok(h4.verdict === 'refused' && has(h4.refusals, /^settle gate: main changed kubernetes/) && !calls().some(c => c.cmd === 'gh' && c.argv[1] === 'merge'), 'hand --merge on an unsettled world: refused at the start, no merge call');
  world({ lock: lockOf({ holder: 'upgrade-night/x/1', phase: 'merged-awaiting-verification', expires_at: '2026-11-11T19:00:00Z' }) });
  const h5 = M.handRun({ pr: 3915, head: HEAD, merge: true }, { rules, run, env, now: NOW, clock: () => NOW });
  ok(h5.verdict === 'refused' && has(h5.refusals, /^the change lock is held by upgrade-night\/x\/1 \(merged-awaiting-verification\) until 2026-11-11T19:00:00Z$/) && !calls().some(c => c.cmd === 'gh' && c.argv[1] === 'merge') && !calls().some(c => ['create', 'replace'].includes(c.argv[0])), 'hand --merge while the lane holds the lock (post-merge hold): refused, no merge, the lock untouched (story 551 item 6)', JSON.stringify(h5.refusals));
} finally {
  rmSync(tmp, { recursive: true, force: true });
}

console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
