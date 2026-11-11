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
//   5. the hand-run CLI (--check-only and --merge) against stub gh and kubectl.
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
  ok(has(pre(view(v => { v.author = { login: 'thaynes43' }; return v; })), /opened by thaynes43, not Renovate/), 'a PR not opened by Renovate is refused');
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
}

// ── 2. R7 and the head behind main, through evaluate() with a stub reader ─────────────
// The stub answers the read-only gh calls evaluate() makes; w overrides the fixture.
const reader = (w = {}) => (cmd, argv) => {
  if (cmd === 'gh' && argv[0] === 'pr' && argv[1] === 'view') return w.view === null ? null : JSON.stringify(w.view || fx.view);
  const api = argv.find(a => a.startsWith('repos/')) || '';
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
const LOCKF = process.env.STUB_LOCK, MERGED = process.env.STUB_MERGED;
const lock = () => (fs.existsSync(LOCKF) ? JSON.parse(fs.readFileSync(LOCKF, 'utf8')) : null);
const merged = () => (fs.existsSync(MERGED) ? JSON.parse(fs.readFileSync(MERGED, 'utf8')) : {});
if (cmd === 'gh' && argv[0] === 'pr' && argv[1] === 'merge') { if (w.mergeExit) fail('merge refused'); const m = merged(); m[argv[2]] = true; fs.writeFileSync(MERGED, JSON.stringify(m)); out(''); }
if (cmd === 'gh' && argv[0] === 'pr' && argv[1] === 'view') {
  const f = argv[argv.indexOf('--json') + 1] || '';
  if (f.includes('mergedAt')) out(merged()[argv[2]] ? { state: 'MERGED', mergedAt: '2026-11-11T16:00:20Z', headRefOid: w.view.headRefOid, mergeCommit: { oid: 'f'.repeat(40) } } : { state: 'OPEN', mergedAt: null, headRefOid: w.view.headRefOid, mergeCommit: null });
  out(w.view);
}
const api = argv.find(a => a.startsWith('repos/')) || '';
if (cmd === 'gh' && /\\/compare\\//.test(api)) { const c = (w.compare || {})[api.split('...')[1].split('?')[0]]; if (!c) fail(); out(c); }
if (cmd === 'gh' && /\\/commits\\?sha=main&/.test(api)) { const q = new URLSearchParams(api.split('?')[1]); const v = q.get('since') ? (w.since || {})[q.get('path')] ?? [] : (w.newest || {})[q.get('path')]; if (!v) fail(); out(v.map(c => ({ sha: c.sha, commit: { committer: { date: c.at } } }))); }
if (cmd === 'kubectl' && argv[0] === 'get') {
  const name = argv[argv.indexOf('--field-selector') + 1].replace('metadata.name=', '');
  if (w.cmFail) fail();
  out({ items: [...(w.cms || []), ...(lock() ? [lock()] : [])].filter(c => c.metadata.name === name) });
}
if (cmd === 'kubectl' && (argv[0] === 'create' || argv[0] === 'replace')) {
  if (w.lockWrite) fail();
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
  const world = (w = {}) => { for (const f of [logFile, lockFile, mergedFile]) rmSync(f, { force: true }); if (w.lock) writeFileSync(lockFile, JSON.stringify(w.lock)); writeFileSync(worldFile, JSON.stringify({ view: fx.view, newest: fx.main_newest, compare: fx.compare, cms: [], ...w })); };
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
  ok(tx.taken && tx.before.state === 'expired' && lockNow().data.took_over_from === 'upgrade-night/crashed/9' && lockNow().data.holder === 'upgrade-night/test/1', 'a lock past its expiry is taken over, naming the crashed holder');
  M.releaseLock(tx.held, { now: NOW, env });
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
  const base = { rules, env, clock: () => NOW, liveClasses: [], token: M.tokenPermissions('{"contents":"read"}'), holder: 'upgrade-night/test/lane', takeAllowed: true };
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
  ok(s5.verdict.verdict === 'refused' && has(s5.verdict.pr_refusals, /^paused: data\.pause="tom testing"$/) && !calls().some(c => ['create', 'replace'].includes(c.argv[0]) || c.argv[1] === 'merge'), 'a set pause: no lock take and no merge');
  world({ cmFail: true });
  const s6 = M.laneMergeStep({ ...base, mode: 'live', liveClasses: ['no-cluster-effect'], token: tokW, subject, run });
  ok(s6.verdict.verdict === 'refused' && has(s6.verdict.pr_refusals, /unreadable: data\.pause/) && !calls().some(c => ['create', 'replace'].includes(c.argv[0]) || c.argv[1] === 'merge'), 'an unreadable pause fails closed: no lock take and no merge');
  world();
  const s7 = M.laneMergeStep({ ...base, mode: 'live', liveClasses: ['no-cluster-effect'], token: tokW, subject, run, takeAllowed: false });
  ok(s7.verdict.verdict === 'refused' && has(s7.verdict.pr_refusals, /no change lock held: a local run \(no --write-state\) never takes it/) && !calls().some(c => ['create', 'replace'].includes(c.argv[0]) || c.argv[1] === 'merge'), 'a local live run never takes the lock and so never merges');

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

  // ── 5. the hand-run CLI ─────────────────────────────────────────────────────────
  const cli = (args, w = {}) => { world(w); const r = spawnSync(process.execPath, [join(RES, 'night-merge.mjs'), ...args], { env: { ...env, UPGRADE_NIGHT_NOW: new Date(NOW).toISOString() }, encoding: 'utf8', timeout: 30000 }); return { ...r, calls: calls(), writes: calls().filter(c => !L.isReadOnlyCommand(c.cmd, c.argv)) }; };
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
  ok(c4.status === 1 && /refused: paused: data\.pause="tom"/.test(c4.stdout), '--check-only refuses while the lane is paused', c4.stdout);
  const c5 = cli(MERGE);
  const m5 = c5.calls.filter(c => c.cmd === 'gh' && c.argv[1] === 'merge');
  ok(c5.status === 0 && /: MERGED$/m.test(c5.stdout) && m5.length === 1 && JSON.stringify(m5[0].argv) === JSON.stringify(M.mergeArgv(M.REPO, 3915, HEAD)) && !c5.calls.some(c => c.cmd === 'kubectl' && c.argv[0] !== 'get'), '--merge on a clean verdict: one pinned squash merge, verified; the lock is not written', c5.stdout + c5.stderr);
  const c6 = cli(MERGE, behind);
  ok(c6.status === 1 && /merge call: not made \(refused\)/.test(c6.stdout) && !c6.calls.some(c => c.argv[1] === 'merge'), '--merge on a refused verdict: no merge call, exit 1', c6.stdout);
  const c7 = cli(MERGE, { mergeExit: true });
  ok(c7.status === 3 && /: MERGE-FAILED$/m.test(c7.stdout), '--merge whose call fails with the PR still open: exit 3', c7.stdout);
  const c8 = cli([...CHECK, '--json']);
  let j8 = null; try { j8 = JSON.parse(c8.stdout); } catch {}
  ok(c8.status === 0 && j8?.verdict === 'would-merge' && j8.ok === true && j8.facts?.reference_time === '2026-11-10T00:10:58.000Z', '--json prints the verdict as JSON', c8.stdout.slice(0, 300));
  for (const [args, why] of [[['--pr', '3915', '--check-only'], 'no --head'], [['--pr', '3915', '--head', HEAD], 'neither mode'], [['--pr', '3915', '--head', HEAD, '--check-only', '--merge'], 'both modes'],
    [['--pr', 'x', '--head', HEAD, '--check-only'], 'a bad PR number'], [['--pr', '3915', '--head', HEAD.toUpperCase(), '--check-only'], 'an uppercase SHA'], [['--pr', '3915', '--head', HEAD, '--check-only', '--admin'], 'an unknown flag']]) {
    const r = cli(args);
    ok(r.status === 2 && /usage: night-merge\.mjs/.test(r.stderr) && r.calls.length === 0, `usage error (${why}): exit 2 before any call`, `${r.status} ${r.stderr}`);
  }
} finally {
  rmSync(tmp, { recursive: true, force: true });
}

console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
