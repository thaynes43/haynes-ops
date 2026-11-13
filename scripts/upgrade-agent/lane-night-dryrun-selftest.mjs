#!/usr/bin/env node
// Self-test for the upgrade-night tick (OP512 step 1): the DRY-RUN GUARANTEE and the
// gates that must FAIL CLOSED.
//
// WHY THIS EXISTS
// ---------------
// The nightly lane is a CronJob whose dry-run must not be able to change anything but
// its own two ConfigMaps, and whose gates must never pass on a fact it could not read.
// Since story 752 its token can merge (ruling Q-548-02), so the guarantee is the code's:
// dry-run never reaches a GitHub write call. This test fails if a mutating gh, git,
// kubectl or flux verb can appear on the dry-run path (other than the lane's own PR record
// and retry box, story 549), or if a gate opens on a failed read:
//   1. statically: lane-classify.mjs and run-night.mjs start processes at exactly one
//      call site each (runReadOnly, writeLaneState); night-merge.mjs at three (writeLock,
//      callMerge, ghWrite), callMerge refuses every mode but live and hand before it
//      spawns, and ghWrite (story 549) sends only the lane's record and the retry box;
//      fetch() is GET-only; no mutating verb is spelled in the first two; git ships
//      dryrun, an empty class allowlist and the shepherd's token rights;
//   2. the read-only allowlist (isReadOnlyCommand) refuses every mutating shape;
//   3. the gate helpers against live-captured backup sources (per-source freshness
//      with the real schedules), the scoped Flux baseline and the state evidence;
//   4. dynamically: the real tick runs against stub gh/kubectl/git/flux/curl binaries,
//      a stub Prometheus and a fake of the lock ConfigMap. In dry-run the non-reads are
//      the lock's take and release and one server-side apply of upgrade-lane-state, and
//      no gh write is made even with a write token and the class allowlisted; every
//      failed read is a named blocker; pause stops the tick; an unknown mode or class is
//      refused before a single command runs. Live mode merges once, pinned to the head,
//      only when every condition holds, and refuses each one that does not;
//   6. story 549 through the tick against a stubbed GitHub: one record per PR, no write
//      when nothing changed, the per-tick retry cap, the ledger before the box, a second
//      failure that blocks, a refused write, a changed body or head, a forged record, the
//      token, a local run, the halt and a pause; and, after the review of 2aa270e4, F1 a
//      PR refused at the write hands the slot on (and the reads are bounded), F2 a workflow
//      run queued for the head, F4 a denial halts the next tick, F5 a pause before a record
//      write, F6 an intent left by an interrupted Job and an unwritable ledger, and F8 an
//      overlapping writer (the ledger's resourceVersion) makes no tick;
//   5. the settle gate (story 551) through the tick: a merge to kubernetes/** 10 minutes
//      earlier is the one blocker on an otherwise clean PR inside the window, the
//      2026-11-10 shepherd merges (fixtures/settle-20261110.json) make every candidate
//      wait until Flux settled, each read fails closed, and live mode does not merge.
//
// Usage: node scripts/upgrade-agent/lane-night-dryrun-selftest.mjs
// Requires: node 24. No network (one loopback listener), no cluster access.
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../..');
const RES = join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/resources');
const L = await import(join(RES, 'lane-classify.mjs'));
const N = await import(join(RES, 'run-night.mjs'));
const M = await import(join(RES, 'night-merge.mjs'));
const fx = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/lane-queue-20261109.json'), 'utf8'));
const bk = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/lane-backup-sources-20261110.json'), 'utf8'));
const rebase = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/lane-rebase-pairs-20261110.json'), 'utf8'));
const gx = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/lane-gates-20261110.json'), 'utf8'));
const rules = L.loadRules(join(RES, 'upgrade-rules.json'));
const SSA = ['apply', '--server-side', '--field-manager=upgrade-night', '--force-conflicts', '-o', 'json', '-f', '-'];

let fails = 0, passes = 0;
const ok = (cond, name, detail = '') => { if (cond) passes++; else { fails++; console.log(`FAIL ${name}${detail ? `\n     ${detail}` : ''}`); } };
const T = s => Date.parse(s);
const H = 3600e3;

// ── 1. static ───────────────────────────────────────────────────────────────
const strip = s => s.replace(/^\s*\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '');
const night = strip(readFileSync(join(RES, 'run-night.mjs'), 'utf8'));
const classify = strip(readFileSync(join(RES, 'lane-classify.mjs'), 'utf8'));
const count = (s, re) => (s.match(re) || []).length;
for (const [name, src] of [['run-night.mjs', night], ['lane-classify.mjs', classify]]) {
  ok(count(src, /\bspawnSync\s*\(/g) === 1, `${name}: exactly one spawnSync call site`, `found ${count(src, /\bspawnSync\s*\(/g)}`);
  ok(!/(?<![.\w])(execSync|execFileSync|execFile|exec|spawn|fork)\s*\(/.test(src.replace(/spawnSync\s*\(/g, '')), `${name}: no other process API`);
  ok(!/from 'node:child_process'/.test(src) || /import \{ spawnSync \} from 'node:child_process'/.test(src), `${name}: child_process imports spawnSync only`);
  ok(!/['"](git|flux|curl|helm)['"]/.test(src), `${name}: never runs git, flux, curl or helm`);
}
ok(/runReadOnly[\s\S]*?if \(!isReadOnlyCommand\(cmd, argv\)\) throw[\s\S]*?spawnSync\(cmd, argv/.test(classify), 'lane-classify.mjs: the spawn sits behind isReadOnlyCommand');
ok(/function writeLaneState\(manifest, manager = 'upgrade-night'\)[\s\S]*?m\.name !== STATE\.name[\s\S]*?!\['upgrade-night', LEDGER_MANAGER, VERIFY_MANAGER, SUMMARY_MANAGER\]\.includes\(manager\)[\s\S]*?throw new Error[\s\S]*?spawnSync\('kubectl', \['apply', '--server-side', `--field-manager=\$\{manager\}`, '--force-conflicts', '-o', 'json', '-f', '-'\]/.test(night), "run-night.mjs: the spawn is writeLaneState's server-side apply, after the name and field-manager checks");
// story 554: the one write outside upgrade-lane-state is the summary's delivery copy, morning_* keys of upgrade-orphan-report only
ok(/const delivery = manager === SUMMARY_MANAGER && m\.name === DELIVERY\.name && m\.namespace === DELIVERY\.namespace[\s\S]*?every\(\(\[k, v\]\) => DELIVERY_KEYS\.includes\(k\) && typeof v === 'string'\)/.test(night)
  && /const DELIVERY = \{ name: 'upgrade-orphan-report', namespace: 'upgrade-agent' \};/.test(night) && /\(!delivery && \(m\.name !== STATE\.name \|\| m\.namespace !== STATE\.namespace\)\)/.test(night),
  "run-night.mjs (story 554): writeLaneState also writes the morning summary's delivery copy: upgrade-orphan-report, the summary's field manager, morning_* keys only");
ok(/const LEDGER_MANAGER = 'upgrade-night-ledger';/.test(night) && /\(manager === LEDGER_MANAGER\) !== \(Object\.keys\(manifest\.data \|\| \{\}\)\.join\(\) === 'retry_ledger'\)/.test(night), 'run-night.mjs (story 549): the ledger field manager writes data.retry_ledger and nothing else, and the tick\'s manager never writes it');
ok(/const STATE = \{ name: 'upgrade-lane-state', namespace: 'upgrade-agent' \}/.test(night), 'run-night.mjs: the state object name is a constant');
ok(/\(manager === VERIFY_MANAGER\) !== VERIFY_KEYS\.includes\(Object\.keys\(manifest\.data \|\| \{\}\)\.sort\(\)\.join\(\)\)/.test(night) && /manager !== VERIFY_MANAGER && Object\.keys\(manifest\.data \|\| \{\}\)\.some\(k => \/\^verify\/\.test\(k\)\)/.test(night)
  && /if \(ver\.memory && !ver\.summary\.error\) \{\s*try \{ writeLaneState\(verifyManifest\(ver\.memory, ver\.table\), VERIFY_MANAGER\); \}\s*catch \(e\) \{ log\(/.test(night) && night.indexOf('writeLaneState(verifyManifest(') > night.indexOf('const ok = writeLaneState(stateManifest('), 'run-night.mjs (story 553, L3): data.verify only under upgrade-night-verify, never under another manager, written last and only when the verifier gave a memory');
ok(count(night, /\bfetch\s*\(/g) === 1 && /fetch\(url, \{ method: 'GET'/.test(night), 'run-night.mjs: one fetch, GET only');
ok(count(classify, /\bfetch\s*\(/g) === 0, 'lane-classify.mjs: no fetch');
// 'patch' is left out: it is also a Renovate update type in lane-classify.mjs; kubectl
// patch and gh api -X PATCH are covered by the allowlist cases in section 2.
const MUTATING = /['"](merge|comment|edit|close|reopen|review|ready|lock|unlock|push|commit|checkout|switch|branch|tag|reset|rebase|annotate|label|replace|delete|create|scale|rollout|exec|cp|run|set|taint|cordon|drain|reconcile|suspend|resume|POST|PUT|DELETE)['"]/g;
ok(!MUTATING.test(classify), 'lane-classify.mjs: no mutating verb literal', String(classify.match(MUTATING)));
ok(!(night.match(MUTATING) || []).length, 'run-night.mjs: no mutating verb literal', String(night.match(MUTATING)));
ok(count(night, /['"]apply['"]/g) === 1, "run-night.mjs: 'apply' appears once (writeLaneState)");
ok(/const MODES = \['dryrun', 'live'\];/.test(night) && /if \(!MODES\.includes\(MODE\) \|\| LIVE\.unknown\.length\) \{[\s\S]*?process\.exit\(3\)/.test(night) && night.indexOf('!MODES.includes(MODE)') < night.indexOf('readFileSync(tokenFile'), 'run-night.mjs: the mode and allowlist refusal comes before any read');
ok(!/900 \* 1024/.test(night) && /MAX_STATE_BYTES = 768 \* 1024/.test(night), 'run-night.mjs: state size budget is 768 KiB under the 1 MiB object limit');

// story 752: dry-run can never reach the merge call (night-merge.mjs)
const merge = strip(readFileSync(join(RES, 'night-merge.mjs'), 'utf8'));
ok(count(merge, /\bspawnSync\s*\(/g) === 3, 'night-merge.mjs: exactly three spawnSync call sites (writeLock, callMerge, ghWrite)', `found ${count(merge, /\bspawnSync\s*\(/g)}`);
{
  // story 549: ghWrite is the one spawn for the lane's PR records and the retry box; it
  // sends gh api --method POST|PATCH <path> --input - after its refusals, and is reached
  // only from runRetry and writePrRecords, never from the merge step or the hand run
  const gw0 = merge.indexOf('export function ghWrite(');
  const gw = merge.slice(gw0, merge.indexOf('\n}\n', gw0));
  ok(gw0 > 0 && /if \(!GH_WRITE_KINDS\.includes\(kind\)\) throw/.test(gw) && /if \(a\?\.repo !== REPO\) throw/.test(gw) && gw.indexOf('throw') < gw.indexOf("spawnSync('gh', argv"), 'night-merge.mjs: ghWrite refuses an unknown kind or repository before its gh spawn');
  ok(/const argv = \['api', '--method', method, path, '--input', '-'\];/.test(gw) && /input: JSON\.stringify\(\{ body \}\)/.test(gw) && [...gw.matchAll(/method = '([A-Z]+)'/g)].map(m => m[1]).every(x => ['POST', 'PATCH'].includes(x)), 'night-merge.mjs: ghWrite sends gh api --method POST|PATCH <path> --input - with { body } only');
  ok(/export const GH_WRITE_KINDS = Object\.freeze\(\['record-create', 'record-edit', 'record-demote', 'retry-box'\]\);/.test(merge), 'night-merge.mjs: the four write kinds are a frozen constant');
  const callers = [...merge.matchAll(/\bwrite = ghWrite\b/g)].length;
  ok(callers === 2 && count(merge, /(?<!function )\bghWrite\(/g) === 0, 'night-merge.mjs: ghWrite is reached only as the default writer of runRetry and writePrRecords', `${callers} default writers, ${count(merge, /(?<!function )\bghWrite\(/g)} direct calls`);
  const lm2 = merge.slice(merge.indexOf('export function laneMergeStep('), merge.indexOf('// ── CLI'));
  const hr2 = merge.slice(merge.indexOf('export function handRun('), merge.indexOf('async function main('));
  ok(!/ghWrite|writePrRecords|runRetry/.test(lm2 + hr2), 'night-merge.mjs: the merge step and the hand run never write a record or tick a box');
  ok(!/ghWrite|callMerge/.test(night) && count(night, /M\.runRetry\(/g) === 1 && count(night, /M\.writePrRecords\(/g) === 2 && night.indexOf('M.writePrRecords({ items: t.items') > night.indexOf('summary.pr_records = '), 'run-night.mjs: one runRetry call and two writePrRecords calls (the open records, then the terminal mirrors of story 593), no direct GitHub write');
}
ok(!/(?<![.\w])(execSync|execFileSync|execFile|exec|spawn|fork)\s*\(/.test(merge.replace(/spawnSync\s*\(/g, '')) && /import \{ spawnSync \} from 'node:child_process'/.test(merge), 'night-merge.mjs: no other process API');
ok(!/['"](git|flux|curl|helm)['"]/.test(merge) && count(merge, /\bfetch\s*\(/g) === 0, 'night-merge.mjs: never runs git, flux, curl or helm, and fetches nothing');
ok(/function writeLock\(verb, manifest, env\) \{[\s\S]*?m\.name !== LOCK\.name[\s\S]*?throw new Error[\s\S]*?spawnSync\('kubectl', \[verb, '-f', '-', '-o', 'json'\]/.test(merge), "night-merge.mjs: the kubectl spawn is writeLock's create/replace, after the lock-name check");
ok(/const LOCK = Object\.freeze\(\{ name: 'upgrade-change-lock', namespace: 'upgrade-agent' \}\)/.test(merge), 'night-merge.mjs: the lock object name is a constant');
const cm0 = merge.indexOf('export function callMerge(');
const cmBody = merge.slice(cm0, merge.indexOf('\n}\n', cm0));
ok(cm0 > 0 && /^[\s\S]*?\{\s*if \(mode !== 'live' && mode !== 'hand'\) throw new Error/.test(cmBody) && cmBody.indexOf("mode !== 'live' && mode !== 'hand'") < cmBody.indexOf("spawnSync('gh', argv"), "night-merge.mjs: callMerge's first statement refuses every mode but live and hand, before its gh spawn");
ok(count(merge, /(?<!function )\bcallMerge\(\{/g) === 2, 'night-merge.mjs: callMerge is called at exactly two sites (the lane, the hand CLI)', `found ${count(merge, /(?<!function )\bcallMerge\(\{/g)}`);
{
  const lm = merge.slice(merge.indexOf('export function laneMergeStep('), merge.indexOf('// ── CLI'));
  const guard = lm.indexOf("if (mode !== 'live') { v.verdict = clean ? 'would-merge' : 'refused'; v.merge_call = 'not reached (dry-run)'; out.verdict = v; return out; }");
  ok(guard > 0 && guard < lm.indexOf('callMerge({'), 'night-merge.mjs: laneMergeStep returns before callMerge whenever mode is not live');
  const hr = merge.slice(merge.indexOf('export function handRun('), merge.indexOf('async function main('));
  ok(hr.indexOf('if (!opt.merge || !out.ok)') > 0 && hr.indexOf('if (!opt.merge || !out.ok)') < hr.indexOf('callMerge({'), 'night-merge.mjs: the hand CLI returns before callMerge on --check-only or a refusal');
}
ok(!/callMerge/.test(night) && count(night, /M\.laneMergeStep\(/g) === 1, 'run-night.mjs: never calls callMerge itself; one laneMergeStep call');
ok(!/--admin|--auto\b|'--force'/.test(merge.replace(/BYPASS_FLAGS = Object\.freeze\(\[[^\]]*\]\)/, '').replace(/\/\^--\(admin\|auto\|force\)\//, '')), 'night-merge.mjs: no bypass flag outside the refusal list');
{
  // what git ships (story 752): dryrun and an empty class allowlist (story 556 changes
  // these together with this test), and a night token with exactly the shepherd's rights
  // (Tom's ruling Q-548-02). The token can merge, so the guarantee is that dry-run never
  // reaches a GitHub write call (the checks above and the stub runs below).
  const hrl = readFileSync(join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/helmrelease.yaml'), 'utf8');
  const nightCtl = hrl.slice(hrl.indexOf('      upgrade-night:'), hrl.indexOf('    defaultPodOptions:'));
  ok(/\n\s+UPGRADE_NIGHT_MODE: dryrun\n/.test(nightCtl) && /\n\s+UPGRADE_NIGHT_LIVE_CLASSES: ""\n/.test(nightCtl), 'helmrelease: git ships UPGRADE_NIGHT_MODE dryrun and an empty UPGRADE_NIGHT_LIVE_CLASSES');
  const perms = [...nightCtl.matchAll(/GITHUB_BOT_TOKEN_PERMISSIONS: '([^']*)'/g)].map(m => m[1]);
  const shepherdCtl = hrl.slice(hrl.indexOf('      upgrade-shepherd:'), hrl.indexOf('      triage:'));
  ok(perms.length === 1 && perms[0] === '{"contents":"write","pull_requests":"write","checks":"read"}' && shepherdCtl.includes(`GITHUB_BOT_TOKEN_PERMISSIONS: '${perms[0]}'`), "helmrelease: the upgrade-night token has exactly the shepherd's rights: contents and pull_requests write, checks read (ruling Q-548-02)", JSON.stringify(perms));
  ok(M.tokenPermissions(perms[0]).can_merge === true && M.tokenPermissions(perms[0]).why === null, 'the merge step accepts exactly that token');
  const deadline = [...hrl.matchAll(/activeDeadlineSeconds: (\d+)/g)];
  ok(deadline.length === 1 && hrl.indexOf('activeDeadlineSeconds') > hrl.indexOf('      upgrade-night:') && Number(deadline[0][1]) <= rules.night.change_lock.lease_seconds && Number(deadline[0][1]) < 900, "helmrelease: only upgrade-night has activeDeadlineSeconds, at most the lock's lease and under the 15-minute period", JSON.stringify(deadline.map(m => m[1])));
  ok(!/NODE_OPTIONS|--import|FAKE_NOW|UPGRADE_NIGHT_NOW|test-fake-clock/.test(nightCtl), 'helmrelease: the upgrade-night controller sets no test clock (UPGRADE_NIGHT_NOW, FAKE_NOW, NODE_OPTIONS or --import)');
  ok(/> \/creds\/gh_token_permissions/.test(nightCtl) && !/gh_token_permissions/.test(hrl.slice(0, hrl.indexOf('      upgrade-night:'))), 'helmrelease: only the upgrade-night mint-token records its permission names');
}

// ── 2. the allowlist ──────────────────────────────────────────────────────────
const refused = [
  ['gh', ['pr', 'merge', '1', '--squash']], ['gh', ['pr', 'comment', '1', '-b', 'x']], ['gh', ['pr', 'edit', '1', '--add-label', 'x']],
  ['gh', ['pr', 'close', '1']], ['gh', ['pr', 'review', '1', '--approve']], ['gh', ['pr', 'ready', '1']], ['gh', ['pr', 'create']],
  ['gh', ['issue', 'comment', '1', '-b', 'x']], ['gh', ['issue', 'edit', '1']], ['gh', ['label', 'create', 'x']], ['gh', ['repo', 'edit']],
  ['gh', ['api', '-X', 'POST', 'repos/x/y/issues/1/comments']], ['gh', ['api', '--method', 'PATCH', 'repos/x/y/pulls/1']],
  ['gh', ['api', '--method=PUT', 'repos/x/y/pulls/1/merge']], ['gh', ['api', '-XDELETE', 'repos/x/y/git/refs/heads/z']],
  ['gh', ['api', 'repos/x/y/issues/1/comments', '-f', 'body=x']], ['gh', ['api', 'repos/x/y/issues/1/labels', '-F', 'labels[]=x']],
  ['gh', ['api', 'repos/x/y/pulls/1', '--input', 'f.json']], ['gh', ['api', 'graphql', '-f', 'query=mutation{}']], ['gh', ['api', 'graphql']],
  ['gh', ['workflow', 'run', 'x']], ['gh', ['run', 'rerun', '1']], ['gh', ['auth', 'login']],
  ['git', ['push', 'origin', 'HEAD']], ['git', ['commit', '-m', 'x']], ['git', ['checkout', '-b', 'x']], ['git', ['log']],
  ['kubectl', ['apply', '-f', '-']], ['kubectl', SSA], ['kubectl', ['-n', 'x', 'patch', 'cm', 'y']], ['kubectl', ['create', 'configmap', 'x']],
  ['kubectl', ['delete', 'pod', 'x']], ['kubectl', ['annotate', 'es', 'x', 'force-sync=1']], ['kubectl', ['replace', '-f', '-']],
  ['kubectl', ['rollout', 'restart', 'deploy/x']], ['kubectl', ['exec', 'x', '--', 'sh']], ['kubectl', ['-n', 'get', 'apply']],
  ['kubectl', ['label', 'pod', 'x', 'a=b']], ['kubectl', ['edit', 'cm', 'x']], ['kubectl', ['scale', 'deploy/x', '--replicas=0']],
  ['flux', ['reconcile', 'ks', 'x']], ['flux', ['get', 'ks']], ['curl', ['-X', 'POST', 'http://x']], ['sh', ['-c', 'gh pr merge 1']],
];
for (const [c, a] of refused) ok(!L.isReadOnlyCommand(c, a), `allowlist refuses: ${c} ${a.join(' ')}`);
const allowed = [
  ['gh', ['pr', 'list', '-R', 'x/y', '--state', 'open']], ['gh', ['pr', 'view', '1', '-R', 'x/y', '--json', 'files']],
  ['gh', ['api', '--paginate', '--slurp', 'repos/x/y/issues/1/comments?per_page=100']], ['gh', ['api', 'repos/x/y/contents/.renovate/holds.json5?ref=main']],
  ['gh', ['api', '-X', 'GET', 'repos/x/y/pulls']], ['kubectl', ['get', 'configmaps', '-n', 'upgrade-agent', '-o', 'json']],
  ['kubectl', ['-n', 'x', 'get', 'pods']], ['kubectl', ['get', 'kustomizations.kustomize.toolkit.fluxcd.io', '-A', '-o', 'json']],
];
for (const [c, a] of allowed) ok(L.isReadOnlyCommand(c, a), `allowlist allows: ${c} ${a.join(' ')}`);
let threw = false;
try { L.runReadOnly('gh', ['pr', 'merge', '1']); } catch (e) { threw = /refused non-read command/.test(e.message); }
ok(threw, 'runReadOnly throws before running a refused command');

// ── 3. gate helpers ───────────────────────────────────────────────────────────
{
  const p = (e, h) => ok(N.cronPeriodMs(e) === h * H, `cron period ${e} = ${h}h`, String(N.cronPeriodMs(e) / H));
  p('0 0 * * *', 24); p('0 0 * * 0', 168); p('30 2 * * *', 24); p('@daily', 24); p('0 0 2 * * *', 24); p('0 */4 * * *', 4);
  ok(N.cronPeriodMs('every day') === null && N.cronPeriodMs('0 0 * JAN *') === null, 'unparseable cron -> null');

  const rs = n => structuredClone(bk.replicationsources.find(r => r.metadata.name === n));
  const D = 24 * H;
  // VolSync `0 0 * * *` runs at 05:00Z here (live: lastSyncTime 05:00:52Z, nextSyncTime 05:00:00Z)
  const mq = rs('mosquitto-aws');
  ok(mq.spec.trigger.schedule === '0 0 * * *' && mq.status.nextSyncTime.endsWith('T05:00:00Z') && /T05:00:\d\dZ$/.test(mq.status.lastSyncTime), 'live: VolSync 0 0 * * * is evaluated as 05:00Z');
  ok(N.volsyncFresh(mq, T('2026-11-10T00:55:00Z'), D).pass, 'mosquitto: the evening after its 05:00Z sync is fresh');
  ok(!N.volsyncFresh(mq, T('2026-11-10T07:00:00Z'), D).pass, "mosquitto: tonight's 05:00Z run with no new sync is not fresh");
  const mq2 = structuredClone(mq); mq2.status.lastSyncTime = '2026-11-10T05:00:49Z'; mq2.status.nextSyncTime = '2026-11-11T05:00:00Z';
  ok(N.volsyncFresh(mq2, T('2026-11-10T07:00:00Z'), D).pass, "mosquitto: after tonight's sync it is fresh");
  const mq3 = structuredClone(mq2); mq3.status.latestMoverStatus.result = 'Failed';
  ok(!N.volsyncFresh(mq3, T('2026-11-10T07:00:00Z'), D).pass, 'a failed last sync is not fresh');
  const px = rs('plex-aws');
  ok(px.spec.trigger.schedule === '0 0 * * 0', 'live: plex-aws is weekly');
  ok(!N.volsyncFresh(px, T('2026-11-10T07:00:00Z'), D).pass, 'weekly plex-aws is blocked on a weeknight');
  ok(N.volsyncFresh(px, T('2026-11-08T07:00:00Z'), D).pass, 'weekly plex-aws passes on its own night');
  const oa = rs('openproject-assets-aws');
  ok(oa.spec.trigger.schedule === '30 2 * * *', 'live: openproject-assets-aws is 30 2 * * *');
  ok(N.volsyncFresh(oa, T('2026-11-10T07:00:00Z'), D).pass, 'openproject-assets: before its 07:30Z run, the last one is fresh');
  ok(!N.volsyncFresh(oa, T('2026-11-10T07:45:00Z'), D).pass, 'openproject-assets: after its 07:30Z run with no new sync is not fresh');
  const nosched = structuredClone(mq); delete nosched.spec.trigger.schedule;
  ok(!N.volsyncFresh(nosched, T('2026-11-10T00:55:00Z'), D).pass, 'a ReplicationSource with no schedule is not fresh');

  const sb = bk.scheduledbackups, b = bk.backups;
  const pg = b.find(x => x.metadata.name.startsWith('postgres-2026'));
  ok(pg.metadata.creationTimestamp === '2026-11-09T05:00:00Z' && pg.status.stoppedAt.startsWith('2026-11-09T00:0'), 'live: postgres16 Backup created 05:00Z reports stoppedAt 00:0xZ (local time stamped Z)');
  ok(sb.find(s => s.spec.cluster.name === 'postgres16').spec.schedule === '@daily', 'live: postgres16 is @daily');
  ok(N.cnpgFresh('database/postgres16', sb, b, T('2026-11-09T07:00:00Z'), D).pass, 'postgres16 (@daily, stoppedAt mislabelled 00:01Z) is fresh after its 05:00Z run');
  ok(N.cnpgFresh('database/postgres16-pgvecto', sb, b, T('2026-11-09T07:00:00Z'), D).pass, 'postgres16-pgvecto (@daily, stoppedAt 05:02Z) is fresh');
  ok(N.cnpgFresh('downloads/ytdrivarr-postgres16', sb, b, T('2026-11-09T07:00:00Z'), D).pass, 'ytdrivarr-postgres16 (@daily, stoppedAt 00:00Z) is fresh');
  ok(sb.find(s => s.spec.cluster.name === 'openproject-db').spec.schedule === '0 0 2 * * *', 'live: openproject-db is 0 0 2 * * *');
  ok(N.cnpgFresh('frontend/openproject-db', sb, b, T('2026-11-09T08:00:00Z'), D).pass, 'openproject-db is fresh after its 07:00Z run');
  const sb2 = structuredClone(sb); sb2.find(s => s.spec.cluster.name === 'postgres16').status.lastScheduleTime = '2026-11-10T05:00:00Z';
  ok(!N.cnpgFresh('database/postgres16', sb2, b, T('2026-11-10T06:00:00Z'), D).pass, "postgres16: tonight's scheduled run with no completed Backup is not fresh");
  ok(!N.cnpgFresh('database/postgres16', sb, b, T('2026-11-10T06:00:00Z'), D).pass, 'postgres16: a Backup older than 24h is not fresh');
  ok(!N.cnpgFresh('database/nope', sb, b, T('2026-11-09T07:00:00Z'), D).pass, 'a cluster with no ScheduledBackup is not fresh');

  const ready = (o, v) => ({ ...o, status: { conditions: [{ type: 'Ready', status: v ? 'True' : 'False' }] } });
  const ks = (ns, name, tns, path, dependsOn = [], v = true) => ready({ metadata: { namespace: ns, name }, spec: { targetNamespace: tns, path, dependsOn } }, v);
  const K = { items: [
    ks('database', 'mosquitto', 'database', './kubernetes/main/apps/database/mosquitto/app', [{ name: 'volsync', namespace: 'volsync-system' }]),
    ks('volsync-system', 'volsync', 'volsync-system', './kubernetes/main/apps/volsync-system/volsync/app'),
    ks('dev-env-system', 'dev-env-h7-metadata-audit', 'dev-env-system', './kubernetes/main/apps/dev-env-system/h7-metadata-audit/app', [], false),
  ] };
  const HR = { items: [ready({ metadata: { namespace: 'database', name: 'mosquitto' }, spec: {} }, true)] };
  const mine = N.kustomizationsFor('kubernetes/main/apps/database/mosquitto', K);
  ok(N.fluxScope(mine, K, HR).blocking.length === 0, 'baseline: an unrelated NotReady Kustomization (H7 audit) does not block');
  const K2 = structuredClone(K); K2.items[1].status.conditions[0].status = 'False';
  ok(N.fluxScope(mine, K2, HR).blocking.includes('Kustomization volsync-system/volsync'), 'baseline: a NotReady Kustomization in the dependsOn chain blocks');
  const HR2 = structuredClone(HR); HR2.items[0].status.conditions[0].status = 'False';
  ok(N.fluxScope(mine, K, HR2).blocking.includes('HelmRelease database/mosquitto'), 'baseline: a NotReady HelmRelease in the target namespace blocks');
  const K3 = structuredClone(K); K3.items.push(ks('database', 'other', 'database', './kubernetes/main/apps/database/other/app', [], false));
  ok(N.fluxScope(mine, K3, HR).blocking.includes('Kustomization database/other'), 'baseline: a NotReady Kustomization targeting the same namespace blocks');
  const K4 = structuredClone(K); K4.items[0].status.conditions[0].status = 'False';
  ok(N.fluxScope(mine, K4, HR).blocking.includes('Kustomization database/mosquitto'), 'baseline: the target itself NotReady blocks');

  const flux = (ks, ns = 'database') => ({ 'kustomize.toolkit.fluxcd.io/name': ks, 'kustomize.toolkit.fluxcd.io/namespace': ns });
  const pod = (name, labels, claim) => ({ metadata: { namespace: 'database', name, labels }, spec: { volumes: claim ? [{ name: 'data', persistentVolumeClaim: { claimName: claim } }] : [{ name: 'tmp', emptyDir: {} }] } });
  const HRo = { items: [{ metadata: { namespace: 'database', name: 'mosquitto', labels: flux('mosquitto') } }] };
  const rsOther = { items: [{ metadata: { namespace: 'database', name: 'mqtt-backup', labels: flux('something-else') } }] };
  const rsLabelled = { items: [{ metadata: { namespace: 'database', name: 'mqtt-backup', labels: flux('mosquitto') } }] };
  const pods = { items: [pod('mosquitto-0', { 'app.kubernetes.io/instance': 'mosquitto' }, 'data-mosquitto-0')] };
  const e1 = N.stateEvidence(mine[0], HRo, rsOther, pods, { items: [] });
  ok(e1.sources.length === 0 && e1.evidence.some(x => /mounts data-mosquitto-0/.test(x)), 'state: a PVC-mounting app whose ReplicationSource is not applied by its Kustomization has state and no source', JSON.stringify(e1));
  const e2 = N.stateEvidence(mine[0], HRo, rsLabelled, pods, { items: [] });
  ok(e2.sources.length === 1, 'state: a ReplicationSource under another name is matched by the Flux label');
  const leaf = ks('kube-system', 'node-out-of-service', 'kube-system', './kubernetes/main/apps/kube-system/node-out-of-service/app');
  const e3 = N.stateEvidence(leaf, { items: [] }, { items: [] }, { items: [] }, { items: [{ metadata: { namespace: 'kube-system', name: 'node-out-of-service', labels: flux('node-out-of-service', 'kube-system') }, spec: { jobTemplate: { spec: { template: { spec: { volumes: [] } } } } } }] });
  ok(e3.evidence.length === 0 && e3.sources.length === 0, 'state: a leaf with no PVC, no volsync and no source is stateless');
  const cj = { items: [{ metadata: { namespace: 'kube-system', name: 'x', labels: flux('node-out-of-service', 'kube-system') }, spec: { jobTemplate: { spec: { template: { spec: { volumes: [{ name: 'd', persistentVolumeClaim: { claimName: 'scratch' } }] } } } } } }] };
  ok(N.stateEvidence(leaf, { items: [] }, { items: [] }, { items: [] }, cj).evidence.some(x => /cronjob x mounts scratch/.test(x)), 'state: a CronJob of the app mounting a PVC is state');

  ok(N.inWindow(T('2026-11-10T06:30:00Z'), 'America/New_York', '01:30', '04:45').open === true, 'window open at 01:30 New York');
  ok(N.inWindow(T('2026-11-10T09:46:00Z'), 'America/New_York', '01:30', '04:45').open === false, 'window shut at 04:46 New York');
}

// ── 3b. R5 (OP550): critical alerts scoped to the target's workloads ──────────────
// The five critical alerts firing at 2026-11-10T06:30Z and the objects they name, read
// back live (fixtures/lane-gates-20261110.json). On that night the namespace rule held
// #4477 (plex-hw-canary) back all night on recyclarr's failed Job, and would have held
// #4710 (kube-prometheus-stack) back on a Proxmox VM's onboot flag.
{
  const AS = rules.alert_scope;
  const K = { items: gx.kustomizations }, HR = { items: gx.helmreleases };
  const facts = (over = {}) => ns => ({ pods: { items: [] }, cronjobs: { items: [] }, jobs: { items: [] }, ...(gx.namespaces[ns] ? Object.fromEntries(Object.entries(gx.namespaces[ns]).map(([k, v]) => [k, { items: v }])) : {}), ...(over[ns] || {}) });
  const target = t => { const mine = N.kustomizationsFor(t, K); return N.fluxScope(mine, K, HR); };
  const gate = (alerts, t, cw = [], f = facts()) => N.alertGate(alerts, target(t), cw, f, K, HR, AS);
  const A = gx.alerts_20261110T0630Z;
  const media = A.find(a => a.alertname === 'MediaAutomationJobFailed'), pve = A.find(a => a.alertname === 'PVEVMNotOnboot');

  // #4477: kubernetes/main/apps/kube-system/node-out-of-service and media/plex-hw-canary
  const s77 = N.fluxScope([...N.kustomizationsFor('kubernetes/main/apps/kube-system/node-out-of-service', K), ...N.kustomizationsFor('kubernetes/main/apps/media/plex-hw-canary', K)], K, HR);
  const g77 = N.alertGate(A, s77, [], facts(), K, HR, AS);
  ok(g77.blockers.length === 0, "#4477 is not blocked by recyclarr's MediaAutomationJobFailed (2026-11-10T06:30Z alerts)", g77.blockers.join(' | '));
  ok(g77.unrelated.some(u => /MediaAutomationJobFailed \(job_name=recyclarr-29903670\) belongs to media\/recyclarr/.test(u)), 'the recyclarr alert is traced through its Job and HelmRelease to media/recyclarr', JSON.stringify(g77.unrelated));
  ok(g77.cluster.filter(c => /DevEnvRescueFailed/.test(c)).length === 2, 'the two DevEnvRescueFailed alerts (no namespace) are listed, not blocking, for a leaf target', JSON.stringify(g77.cluster));
  // #4710: kube-prometheus-stack in observability
  const g10 = gate(A, 'kubernetes/main/apps/observability/kube-prometheus-stack');
  ok(g10.blockers.length === 0, '#4710 (kube-prometheus-stack) is not blocked by PVEVMNotOnboot', g10.blockers.join(' | '));
  ok(g10.unrelated.some(u => /PVEVMNotOnboot \(pod=prometheus-pve-exporter-[^)]+\) belongs to observability\/prometheus-pve-exporter/.test(u)), 'PVEVMNotOnboot is traced to the exporter that raised it', JSON.stringify(g10.unrelated));
  // the alert's own app is still blocked by it
  ok(gate([pve], 'kubernetes/main/apps/observability/prometheus-pve-exporter').blockers.some(b => /PVEVMNotOnboot .* belongs to observability\/prometheus-pve-exporter, the target/.test(b)), "an alert blocks the app it belongs to (the exporter's own update)");
  ok(gate([media], 'kubernetes/main/apps/media/recyclarr').blockers.length === 1, "recyclarr's failed Job blocks recyclarr's own update");
  // fail closed: every case the lane cannot tell
  const noJob = { ...media, job_name: 'gone-42' };
  ok(gate([noJob], 'kubernetes/main/apps/media/plex-hw-canary').blockers.some(b => /job_name=gone-42\) cannot be traced/.test(b)), 'fail closed: a job_name with no Job, pod or CronJob blocks');
  ok(gate([media], 'kubernetes/main/apps/media/plex-hw-canary', [], facts({ media: { jobs: null } })).blockers.some(b => /cannot be traced/.test(b)), 'fail closed: Jobs unreadable -> the job_name alert blocks');
  const gcJob = { ...media, job_name: 'recyclarr-29999999' };
  ok(gate([gcJob], 'kubernetes/main/apps/media/plex-hw-canary').blockers.length === 0, 'a Job already removed by its history limit is traced through its CronJob name');
  ok(gate([{ alertname: 'X', namespace: 'media', severity: 'critical' }], 'kubernetes/main/apps/media/plex-hw-canary').blockers.some(b => /X names no workload \(namespace fallback\)/.test(b)), 'fail closed: an alert naming no workload blocks every target in its namespace');
  const own = { alertname: 'Y', namespace: 'media', cronjob: 'plex-hw-canary', job: 'kube-state-metrics', pod: 'kube-state-metrics-6dd4fbf594-cwtvg' };
  ok(gate([own], 'kubernetes/main/apps/media/plex-hw-canary').blockers.some(b => /belongs to media\/plex-hw-canary, the target/.test(b)), "an alert on the target's own CronJob blocks it");
  ok(gate([{ alertname: 'Z', namespace: 'media', pod: 'nope-0' }], 'kubernetes/main/apps/media/plex-hw-canary').blockers.some(b => /pod=nope-0\) cannot be traced/.test(b)), 'fail closed: a pod that is not there blocks');
  const ksm = { alertname: 'KsmOnly', namespace: 'observability', job: 'kube-state-metrics', pod: 'kube-state-metrics-6dd4fbf594-cwtvg' };
  ok(gate([ksm], 'kubernetes/main/apps/observability/prometheus-pve-exporter').blockers.some(b => /KsmOnly names no workload/.test(b)), "fail closed: kube-state-metrics' own pod label is the scraper, not a workload (namespace fallback)");
  ok(gate([{ ...ksm, persistentvolumeclaim: 'storage-loki-0' }], 'kubernetes/main/apps/observability/prometheus-pve-exporter').blockers.some(b => /persistentvolumeclaim=storage-loki-0\) cannot be traced/.test(b)), 'fail closed: a claim no running pod mounts blocks');
  ok(gate([{ alertname: 'D', namespace: 'observability', deployment: 'prometheus-pve-exporter' }], 'kubernetes/main/apps/observability/kube-prometheus-stack').unrelated.some(u => /deployment=prometheus-pve-exporter\) belongs to observability\/prometheus-pve-exporter/.test(u)), 'a deployment label is resolved through its ReplicaSet-owned pods');
  // Flux alerts carry exported_namespace and the object, not namespace
  const fx1 = { alertname: 'FluxReconciliationFailure', customresource_kind: 'Kustomization', exported_namespace: 'media', name: 'plex-hw-canary' };
  ok(gate([fx1], 'kubernetes/main/apps/media/plex-hw-canary').blockers.some(b => /Kustomization media\/plex-hw-canary\) belongs to media\/plex-hw-canary/.test(b)), "a Flux failure of the target's own Kustomization blocks it");
  ok(gate([{ ...fx1, name: 'recyclarr' }], 'kubernetes/main/apps/media/plex-hw-canary').blockers.length === 0, "a Flux failure of another Kustomization in the namespace does not");
  ok(gate([{ ...fx1, customresource_kind: 'OCIRepository', name: 'app-template' }], 'kubernetes/main/apps/media/plex-hw-canary').blockers.some(b => /names no workload/.test(b)), 'fail closed: a failing Flux source in the namespace blocks every target there');
  ok(gate([{ ...fx1, name: 'no-such-ks' }], 'kubernetes/main/apps/media/plex-hw-canary').blockers.some(b => /cannot be traced/.test(b)), 'fail closed: a Flux alert for a Kustomization the tick cannot see blocks');
  // a dependency's alert blocks; the 06:30Z H7 Flux alert reaches no target outside dev-env-system
  const Kd = { items: gx.kustomizations.map(k => k.metadata.name === 'plex-hw-canary' ? { ...k, spec: { ...k.spec, dependsOn: [{ name: 'recyclarr', namespace: 'media' }] } } : k) };
  const sd = N.fluxScope(N.kustomizationsFor('kubernetes/main/apps/media/plex-hw-canary', Kd), Kd, HR);
  ok(N.alertGate([media], sd, [], facts(), Kd, HR, AS).blockers.some(b => /media\/recyclarr, the target or its dependsOn chain/.test(b)), "an alert in the target's dependsOn chain blocks");
  // a dependency in another namespace (plex-hw-canary dependsOn external-secrets/external-secrets-stores, live)
  const s77d = target('kubernetes/main/apps/media/plex-hw-canary');
  ok(s77d.chain.includes('external-secrets/external-secrets-stores') && !s77d.namespaces.includes('external-secrets'), 'fixture: plex-hw-canary depends on a Kustomization in another namespace');
  const fxDep = { alertname: 'FluxReconciliationFailure', customresource_kind: 'Kustomization', exported_namespace: 'external-secrets', name: 'external-secrets-stores' };
  ok(gate([fxDep], 'kubernetes/main/apps/media/plex-hw-canary').blockers.some(b => /critical alert firing in external-secrets: FluxReconciliationFailure \(Kustomization external-secrets\/external-secrets-stores\) belongs to external-secrets\/external-secrets-stores, in the target's dependsOn chain/.test(b)), "an alert on a dependency in another namespace blocks (review finding on PR 4825)");
  ok(gate([{ ...fxDep, name: 'external-secrets' }], 'kubernetes/main/apps/media/plex-hw-canary').blockers.some(b => /Kustomization external-secrets\/external-secrets\) belongs to external-secrets\/external-secrets, in the target's dependsOn chain/.test(b)), 'a Flux alert on a chain Kustomization blocks even when the tick lists no such object');
  const depOther = gate([{ alertname: 'W', namespace: 'external-secrets' }, { ...fxDep, name: 'not-in-the-chain' }], 'kubernetes/main/apps/media/plex-hw-canary');
  const depJob = { alertname: 'V', namespace: 'external-secrets', job_name: 'stores-sync-29900000' };
  ok(gate([depJob], 'kubernetes/main/apps/media/plex-hw-canary', [], facts({ 'external-secrets': { jobs: null } })).blockers.some(b => /unreadable: pods, Jobs or CronJobs in external-secrets \(a dependency's namespace\), so V \(job_name=stores-sync-29900000\) cannot be traced/.test(b)), "fail closed: a dependency's namespace that cannot be read blocks an alert naming a workload there (second review finding on PR 4825)");
  ok(gate([depJob], 'kubernetes/main/apps/media/plex-hw-canary').blockers.length === 0, "with that namespace readable, the same untraceable alert is listed, not blocking");
  ok(depOther.blockers.length === 0 && depOther.unrelated.length === 2 && depOther.unrelated.every(u => /a dependency's namespace/.test(u)), "in a dependency's namespace, an alert outside the chain or naming no workload is listed, not blocking (no namespace fallback there)", JSON.stringify(depOther));
  // R5 decision: an alert with no namespace label blocks a cluster-wide target only
  const g07 = gate(A, 'kubernetes/main/apps/storage/ceph-csi/gasha01-rbd', ['csi']);
  ok(g07.blockers.filter(b => /DevEnvRescueFailed has no namespace label and the target is cluster-wide \(csi\)/.test(b)).length === 2 && !g07.blockers.some(b => /FluxReconciliationFailure/.test(b)), 'cluster-wide target (ceph-csi): each alert with no namespace label blocks; the H7 Flux alert (exported_namespace dev-env-system) does not', g07.blockers.join(' | '));
}

// ── 4. the real tick against recording stubs ────────────────────────────────
const NOW = '2026-11-02T07:00:00Z'; // 02:00 New York, inside the merge window
const tmp = mkdtempSync(join(tmpdir(), 'lane-night-'));
let prom = { code: 200, body: { status: 'success', data: { result: [] } } };
const server = createServer((_, res) => { res.writeHead(prom.code, { 'content-type': 'application/json' }); res.end(JSON.stringify(prom.body)); });
await new Promise(r => server.listen(0, '127.0.0.1', r));
try {
  const bin = join(tmp, 'bin'); mkdirSync(bin);
  const logFile = join(tmp, 'calls.jsonl');
  const fact = n => fx.prs.find(p => p.facts.number === n).facts;
  const toGh = f => ({
    number: f.number, headRefOid: f.head_sha, headRefName: f.branch, title: f.title, author: { login: f.author }, isDraft: f.is_draft,
    labels: f.labels.map(name => ({ name })), files: f.files.map(path => ({ path })), changedFiles: f.files.length,
    commits: f.commits.map(c => ({ oid: c.sha, authors: c.authors.map(login => ({ login })), ...(c.sha === f.head_sha && f.head_committed_at ? { committedDate: f.head_committed_at } : {}) })),
    statusCheckRollup: f.checks.map(c => ({ __typename: 'CheckRun', workflowName: c.workflow, name: c.name, status: c.status, conclusion: c.conclusion, completedAt: c.completed_at, ...(c.started_at ? { startedAt: c.started_at } : {}) })),
    body: '| Package | Update | Change |\n|---|---|---|\n' + f.packages.map(p => `| ${p.name} | ${p.update || ''} | \`${p.from}\` → \`${p.to}\` |`).join('\n') + '\n',
  });
  const ready = v => ({ conditions: [{ type: 'Ready', status: v ? 'True' : 'False' }] });
  // story 551: Flux's source sits at main's head in every world unless a test sets
  // mainCompare (the compare <source>...main reads identical), and the Flux objects, trimmed
  // to what the older gates read, get the generations the settle gate reads, as a settled
  // cluster. Fields a test sets are kept, so a test can unsettle one.
  const K8S0 = { sha: '9'.repeat(40) };
  const fillSettled = o => {
    if (!o || typeof o !== 'object') return o;
    const x = structuredClone(o);
    x.metadata = { generation: 1, ...(x.metadata || {}) };
    x.status = { observedGeneration: x.metadata.generation, ...(x.status || {}) };
    return x;
  };
  const flux = (ks, ns) => ({ 'kustomize.toolkit.fluxcd.io/name': ks, 'kustomize.toolkit.fluxcd.io/namespace': ns });
  const ks = (ns, name, path, dependsOn = [], v = true) => ({ metadata: { namespace: ns, name }, spec: { targetNamespace: ns, path, dependsOn }, status: ready(v) });
  const healthy = () => ({
    configmaps: { items: [{ metadata: { name: 'upgrade-work-orders' }, data: { 'wo-1': JSON.stringify({ status: 'done' }) } }] },
    'kustomizations.kustomize.toolkit.fluxcd.io': { items: [
      ks('kube-system', 'node-out-of-service', './kubernetes/main/apps/kube-system/node-out-of-service/app'),
      ks('media', 'plex-hw-canary', './kubernetes/main/apps/media/plex-hw-canary/app'),
      ks('database', 'mosquitto', './kubernetes/main/apps/database/mosquitto/app', [{ name: 'volsync', namespace: 'volsync-system' }]),
      ks('volsync-system', 'volsync', './kubernetes/main/apps/volsync-system/volsync/app'),
      ks('dev-env-system', 'dev-env-h7-metadata-audit', './kubernetes/main/apps/dev-env-system/h7-metadata-audit/app', [], false),
    ] },
    'helmreleases.helm.toolkit.fluxcd.io': { items: [{ metadata: { namespace: 'database', name: 'mosquitto', labels: flux('mosquitto', 'database') }, spec: {}, status: ready(true) }] },
    'replicationsources.volsync.backube': { items: [{ metadata: { namespace: 'database', name: 'mosquitto-aws', labels: flux('mosquitto', 'database') }, spec: { trigger: { schedule: '0 0 * * *' } },
      status: { lastSyncTime: '2026-11-02T05:00:51Z', nextSyncTime: '2026-11-03T05:00:00Z', latestMoverStatus: { result: 'Successful' } } }] },
    'backups.postgresql.cnpg.io': { items: [] }, 'scheduledbackups.postgresql.cnpg.io': { items: [] },
    'cephclusters.ceph.rook.io': { items: [{ status: { ceph: { health: 'HEALTH_OK' } } }] },
    // story 551: Flux's source, at main's newest change to kubernetes/** (K8S0)
    'gitrepositories.source.toolkit.fluxcd.io': { items: [{ metadata: { namespace: 'flux-system', name: 'haynes-ops', generation: 1 }, status: { observedGeneration: 1, artifact: { revision: `main@sha1:${K8S0.sha}` }, conditions: [{ type: 'Ready', status: 'True', reason: 'Succeeded', lastTransitionTime: '2026-10-01T00:00:00Z' }] } }] },
    'pods@database': { items: [{ metadata: { namespace: 'database', name: 'mosquitto-0', labels: { 'app.kubernetes.io/instance': 'mosquitto' } }, spec: { volumes: [{ name: 'data', persistentVolumeClaim: { claimName: 'data-mosquitto-0' } }] } }] },
    pods: { items: [] }, cronjobs: { items: [] }, jobs: { items: [] },
  });
  // The stub plays gh and kubectl. Story 752 added a small fake of the API server for
  // ConfigMap upgrade-change-lock (create fails when it exists, replace checks the
  // resourceVersion), the merge step's reads and a recording gh pr merge.
  const stub = `#!/usr/bin/env node
const fs = require('fs'); const path = require('path');
const cmd = path.basename(process.argv[1]); const argv = process.argv.slice(2);
let stdin = ''; try { if ((cmd === 'kubectl' && ['apply', 'create', 'replace'].includes(argv[0])) || (cmd === 'gh' && argv[0] === 'api' && argv[1] === '--method')) stdin = fs.readFileSync(0, 'utf8'); } catch {}
fs.appendFileSync(process.env.STUB_LOG, JSON.stringify({ cmd, argv, stdin }) + '\\n');
const w = JSON.parse(fs.readFileSync(process.env.STUB_WORLD, 'utf8'));
const out = o => { process.stdout.write(typeof o === 'string' ? o : JSON.stringify(o)); process.exit(0); };
const fail = (m = 'Error from server (Forbidden)') => { process.stderr.write(m); process.exit(1); };
const LOCKF = process.env.STUB_LOCK, MERGED = process.env.STUB_MERGED;
const lock = () => (fs.existsSync(LOCKF) ? JSON.parse(fs.readFileSync(LOCKF, 'utf8')) : null);
const merged = () => (fs.existsSync(MERGED) ? JSON.parse(fs.readFileSync(MERGED, 'utf8')) : {});
const fsel = () => { const i = argv.indexOf('--field-selector'); return i < 0 ? null : argv[i + 1].replace(/^metadata\\.name=/, ''); };
// story 549: GitHub's comments and PR bodies as the lane's writes leave them (STUB_GH),
// initialised from the world; the retry ledger the lane applied (STUB_LEDGER)
const GHF = process.env.STUB_GH, LEDF = process.env.STUB_LEDGER, RVF = process.env.STUB_LEDGER + '.rv', HISTF = process.env.STUB_HISTORY, MARKF = process.env.STUB_HISTORY + '.marker';
const stateRv = () => (fs.existsSync(RVF) ? fs.readFileSync(RVF, 'utf8') : String(w.stateRv || 8));
const bumpRv = () => { const v = String(Number(stateRv()) + 1); fs.writeFileSync(RVF, v); return v; };
const ghs = () => (GHF && fs.existsSync(GHF) ? JSON.parse(fs.readFileSync(GHF, 'utf8')) : { comments: w.comments || {}, pulls: {}, next: 9000 });
const ghsSave = s => fs.writeFileSync(GHF, JSON.stringify(s));
if (cmd === 'gh' && argv[0] === 'api' && argv[1] === '--method') {
  if (w.ghWriteFail) fail(w.ghWriteFailLine || 'gh: Resource not accessible by integration (HTTP 403)');
  const s = ghs(), body = JSON.parse(stdin).body, p = argv[3]; let m;
  if (argv[2] === 'POST' && (m = /issues\\/(\\d+)\\/comments$/.exec(p))) { const c = { id: s.next++, user: { login: w.botLogin || 'haynes-ops-bot[bot]', type: 'Bot' }, body }; s.comments[m[1]] = [...(s.comments[m[1]] || []), c]; ghsSave(s); out(c); }
  if (argv[2] === 'PATCH' && (m = /issues\\/comments\\/(\\d+)$/.exec(p))) { for (const k of Object.keys(s.comments)) { const c = s.comments[k].find(x => x.id === Number(m[1])); if (c) { c.body = body; ghsSave(s); out(c); } } fail('gh: Not Found (HTTP 404)'); }
  if (argv[2] === 'PATCH' && (m = /pulls\\/(\\d+)$/.exec(p))) { s.pulls[m[1]] = body; ghsSave(s); out({ number: Number(m[1]), head: { sha: w.prs[m[1]].headRefOid }, body }); }
  fail('unexpected write');
}
if (cmd === 'gh' && argv[0] === 'api' && argv.some(a => /\\/commits\\/[0-9a-f]{40}\\/check-suites/.test(a))) { const sha = argv.find(a => /check-suites/.test(a)).split('/')[4]; const v = (w.suites || {})[sha]; if (v === 'FAIL') fail(); out(v || { total_count: 1, check_suites: [{ id: 1, app: { slug: 'github-actions' }, status: 'completed' }] }); }
if (cmd === 'gh' && argv[0] === 'api' && argv.some(a => /issues\\/comments\\/\\d+$/.test(a))) { const id = Number(argv.find(a => /issues\\/comments\\//.test(a)).split('/').pop()); for (const list of Object.values(ghs().comments)) { const c = list.find(x => x.id === id); if (c) out(c); } fail('gh: Not Found (HTTP 404)'); }
if (cmd === 'gh' && argv[0] === 'api' && argv.some(a => /\\/pulls\\/\\d+$/.test(a))) {
  const n = argv.find(a => /\\/pulls\\/\\d+$/.test(a)).split('/').pop(), p = w.prs[n], rf = GHF + '.pull-reads';
  const k = Number(fs.existsSync(rf) ? fs.readFileSync(rf, 'utf8') : 0) + 1; fs.writeFileSync(rf, String(k));
  let body = ghs().pulls[n] ?? p.body; if (w.bodyChangesAfterRead && k >= 2) body += '\\nRenovate edited this body';
  out({ number: Number(n), state: 'open', draft: false, base: { ref: 'main' }, user: { login: 'renovate[bot]', type: 'Bot', id: 29139614 }, head: { sha: (w.headAfterRead && k >= 2) ? w.headAfterRead : p.headRefOid, ref: p.headRefName, repo: { full_name: 'thaynes43/haynes-ops' } }, body });
}
// story 593: the history's merged-PR scan (gh pr list --state merged)
if (cmd === 'gh' && argv[0] === 'pr' && argv[1] === 'list' && argv.includes('merged')) { if (w.failMergedList) fail(); const q = argv[argv.indexOf('--search') + 1] || ''; out((w.mergedList || []).filter(e => (q.startsWith('Revert') ? /^Revert "/.test(e.title) : !/^Revert "/.test(e.title)))); }
if (cmd === 'gh' && argv[0] === 'api' && /^search\\/issues\\?/.test(argv[1] || '')) { if (w.failCount) fail(); out({ total_count: w.mergedCount ?? (w.mergedList || []).filter(e => !/^Revert "/.test(e.title)).length, incomplete_results: false }); }
// story 593: who closed a PR (REST issue), when a world names it
if (cmd === 'gh' && argv[0] === 'api' && /^repos\\/thaynes43\\/haynes-ops\\/issues\\/\\d+$/.test(argv[1] || '')) { const v = (w.issues || {})[argv[1].split('/').pop()]; if (!v) fail('gh: Not Found (HTTP 404)'); out(v); }
if (cmd === 'gh' && argv[0] === 'pr' && argv[1] === 'list') { if (w.failList) fail(); out(w.list); }
if (cmd === 'gh' && argv[0] === 'pr' && argv[1] === 'merge') {
  if (lock()) fs.writeFileSync(LOCKF + '.at-merge', JSON.stringify(lock()));
  if (w.mergeKilled) process.kill(process.pid, 'SIGKILL');
  if (w.mergeExit) fail('GraphQL: merge refused'); const m = merged(); m[argv[2]] = true; fs.writeFileSync(MERGED, JSON.stringify(m)); out('');
}
if (cmd === 'gh' && argv[0] === 'pr' && argv[1] === 'view') {
  const n = argv[2], f = argv[argv.indexOf('--json') + 1] || '', p = w.prs[n] && ghs().pulls[n] ? { ...w.prs[n], body: ghs().pulls[n] } : w.prs[n];
  if (f.includes('mergedAt')) out(merged()[n] ? { ...p, state: 'MERGED', mergedAt: '2026-11-02T07:00:30Z', headRefOid: p.headRefOid, mergeCommit: { oid: 'f'.repeat(40) } } : p.state === 'CLOSED' ? { ...p, state: 'CLOSED', mergedAt: null, closedAt: '2026-11-02T06:30:00Z', headRefOid: p.headRefOid, mergeCommit: null } : { ...p, state: 'OPEN', mergedAt: null, headRefOid: p.headRefOid, mergeCommit: null });
  if (f.includes('mergeStateStatus')) { const o = (w.mergeViews || {})[n]; if (o === 'FAIL') fail(); out({ ...p, author: { ...p.author, is_bot: true }, isCrossRepository: false, state: 'OPEN', baseRefName: 'main', mergeable: 'MERGEABLE', mergeStateStatus: 'CLEAN', ...(o || {}) }); }
  out(p);
}
if (cmd === 'gh' && argv[0] === 'api' && argv.some(a => /\\/pulls\\/\\d+\\/commits/.test(a))) {
  const n = argv.find(a => /\\/pulls\\//.test(a)).match(/pulls\\/(\\d+)/)[1], o = (w.restCommits || {})[n];
  if (o === 'FAIL') fail();
  out(o || w.prs[n].commits.map(c => ({ sha: c.oid, author: { login: 'renovate[bot]', type: 'Bot', id: 29139614 }, committer: { login: 'web-flow', type: 'User', id: 19864447 }, commit: { verification: { verified: true, reason: 'valid' } } })));
}
if (cmd === 'gh' && argv[0] === 'api' && argv.some(a => /\\/commits\\/[0-9a-f]{40}$/.test(a)) && !argv.includes('Accept: application/vnd.github.diff')) { const sha = argv.find(a => /\\/commits\\//.test(a)).split('/').pop(); const c = (w.commitFiles || {})[sha]; if (!c || c === 'FAIL') fail(); out(c); }
if (cmd === 'gh' && argv[0] === 'api' && argv.some(a => /\\/commits\\/[0-9a-f]{40}$/.test(a))) { const sha = argv.find(a => /\\/commits\\//.test(a)).split('/').pop(); const n = Object.keys(w.prs).find(k => w.prs[k].headRefOid === sha); if (w.diffs?.[n] === 'FAIL') fail(); out(w.diffs?.[n] ?? ''); }
if (cmd === 'gh' && argv[0] === 'api' && argv.some(a => a.includes('contents/.renovate/holds.json5'))) out({ content: w.holds });
if (cmd === 'gh' && argv[0] === 'api' && argv.some(a => /\\/compare\\/[0-9a-f]{40}\\.\\.\\.main/.test(a))) { const v = w.mainCompare ?? { status: 'identical', ahead_by: 0, behind_by: 0, total_commits: 0, commits: [] }; if (v === 'FAIL') fail(); out(v); }
if (cmd === 'gh' && argv[0] === 'api' && argv.some(a => /\\/compare\\//.test(a))) { const s = argv.find(a => /\\/compare\\//.test(a)).split('...')[1].split('?')[0]; const c = (w.contains || {})[s]; if (c === 'FAIL') fail(); out({ status: c === false ? 'diverged' : 'behind', ahead_by: c === false ? 1 : 0, behind_by: 3 }); }
if (cmd === 'gh' && argv[0] === 'api' && argv.some(a => /\\/commits\\?sha=main&/.test(a))) {
  const q = new URLSearchParams(argv.find(a => /\\/commits\\?/.test(a)).split('?')[1]);
  const v = q.get('since') ? (w.mainCommits || {})[q.get('path')] : (w.mainNewest || {})[q.get('path') ?? '(all)'];
  if (v === 'FAIL') fail(); out((v || []).map(c => (c.commit ? c : { sha: c.sha, parents: c.parents ?? [{ sha: 'e'.repeat(40) }], commit: { committer: { date: c.at }, author: { date: c.author_at ?? c.at }, message: c.message ?? '' } })));
}
if (cmd === 'gh' && argv[0] === 'api' && argv.some(a => /issues\\/\\d+\\/comments/.test(a))) { const n = argv.find(a => /issues\\/\\d+\\/comments/.test(a)).match(/issues\\/(\\d+)/)[1]; out([ghs().comments[n] || []]); }
if (cmd === 'kubectl' && argv[0] === 'get') {
  const ns = argv[argv.indexOf('-n') + 1], name = fsel();
  if (argv[1] === 'configmaps' && ns === 'upgrade-agent') {
    if (name === 'upgrade-change-lock' && w.lockRead) fail();
    const base = w.k8s['configmaps@upgrade-agent'] ?? w.k8s.configmaps;
    if (base === 'FAIL' || base === undefined) fail();
    if (base === 'GARBAGE') out('not json');
    let items = [...base.items, ...(lock() ? [lock()] : []), ...(HISTF && fs.existsSync(HISTF) && !w.historyHiddenOnRead ? [JSON.parse(fs.readFileSync(HISTF, 'utf8'))] : []), ...(fs.existsSync(MARKF) && !w.markerHiddenOnRead ? [JSON.parse(fs.readFileSync(MARKF, 'utf8'))] : [])];
    const led = LEDF && fs.existsSync(LEDF) ? fs.readFileSync(LEDF, 'utf8') : (w.ledger ?? null);
    if (led !== null || w.stateRvOnRead) items = [...items.filter(c => c.metadata.name !== 'upgrade-lane-state'), { metadata: { name: 'upgrade-lane-state', resourceVersion: w.stateRvOnRead || stateRv() }, data: led !== null ? { retry_ledger: led } : {} }];
    const reads = name === 'upgrade-lane-state' ? Number(fs.existsSync(LOCKF + '.state-reads') ? fs.readFileSync(LOCKF + '.state-reads', 'utf8') : 0) + 1 : 0;
    if (name === 'upgrade-lane-state') fs.writeFileSync(LOCKF + '.state-reads', String(reads));
    const pz = w.pauseOnReread || (w.pauseFromRead && reads >= w.pauseFromRead ? w.pauseFromReadValue : null);
    if (name === 'upgrade-lane-state' && w.pauseFailFromRead && reads >= w.pauseFailFromRead) fail();
    if (name === 'upgrade-lane-state' && pz) items = [...items.filter(c => c.metadata.name !== name), { metadata: { name, resourceVersion: '9' }, data: { pause: pz } }];
    out({ items: name ? items.filter(c => c.metadata?.name === name) : items });
  }
  const v = w.k8s[argv[1] + '@' + ns] ?? w.k8s[argv[1]]; if (v === 'FAIL' || v === undefined) fail(); out(v === 'GARBAGE' ? 'not json' : v);
}
if (cmd === 'kubectl' && (argv[0] === 'create' || argv[0] === 'replace')) {
  if (w.lockWrite) fail('Error from server (Forbidden): configmaps is forbidden');
  const m = JSON.parse(stdin), cur = lock();
  if (argv[0] === 'create' && cur) fail('Error from server (AlreadyExists): configmaps "upgrade-change-lock" already exists');
  if (argv[0] === 'replace' && !cur) fail('Error from server (NotFound)');
  if (argv[0] === 'replace' && cur.metadata.resourceVersion !== m.metadata.resourceVersion) fail('Error from server (Conflict): the object has been modified');
  m.metadata.resourceVersion = String(cur ? Number(cur.metadata.resourceVersion) + 1 : 100);
  fs.writeFileSync(LOCKF, JSON.stringify(m)); out(m);
}
if (cmd === 'kubectl' && argv[0] === 'apply' && argv.includes('--field-manager=upgrade-night-ledger')) {
  if (w.failLedger) fail();
  const m = JSON.parse(stdin), want = m.metadata.resourceVersion;
  if (want && want !== stateRv()) fail('Error from server (Conflict): Operation cannot be fulfilled on configmaps "upgrade-lane-state": the object has been modified; please apply your changes to the latest version and try again');
  fs.writeFileSync(LEDF, m.data.retry_ledger); out({ metadata: { name: 'upgrade-lane-state', resourceVersion: bumpRv() } });
}
if (cmd === 'kubectl' && argv[0] === 'apply' && w.failApply) fail();
// story 593: ConfigMap upgrade-ledger-marker (no version), then upgrade-ledger: the empty
// create, then writes at a resourceVersion
if (cmd === 'kubectl' && argv[0] === 'apply' && argv[2] === '--field-manager=upgrade-night-history-marker') {
  if (w.failHistory || w.failMarker) fail('Error from server (Forbidden): configmaps "upgrade-ledger-marker" is forbidden');
  const m = JSON.parse(stdin); const o = { metadata: { name: 'upgrade-ledger-marker', resourceVersion: '700' }, data: m.data }; fs.writeFileSync(MARKF, JSON.stringify(o)); out(o);
}
if (cmd === 'kubectl' && argv[0] === 'apply' && /^--field-manager=upgrade-night-history(-init)?$/.test(argv[2])) {
  if (w.failHistory) fail('Error from server (Forbidden): configmaps "upgrade-ledger" is forbidden');
  const m = JSON.parse(stdin), cur = fs.existsSync(HISTF) ? JSON.parse(fs.readFileSync(HISTF, 'utf8')) : null;
  if (argv[2].endsWith('-init')) { const o = cur || { metadata: { name: 'upgrade-ledger', resourceVersion: '500' }, data: {} }; fs.writeFileSync(HISTF, JSON.stringify(o)); out(o); }
  if (!cur || w.historyConflict || m.metadata.resourceVersion !== cur.metadata.resourceVersion) fail('Error from server (Conflict): Operation cannot be fulfilled on configmaps "upgrade-ledger": the object has been modified; please apply your changes to the latest version and try again');
  const o = { metadata: { name: 'upgrade-ledger', resourceVersion: String(Number(cur.metadata.resourceVersion) + 1) }, data: m.data }; fs.writeFileSync(HISTF, JSON.stringify(o)); out(o);
}
if (cmd === 'kubectl' && argv[0] === 'apply' && argv.includes('--field-manager=upgrade-night')) out({ metadata: { name: 'upgrade-lane-state', resourceVersion: bumpRv() } });
process.exit(0);
`;
  for (const c of ['gh', 'kubectl', 'git', 'flux', 'curl']) { writeFileSync(join(bin, c), stub); chmodSync(join(bin, c), 0o755); }
  const baseWorld = nums => ({
    list: [...nums.map(n => ({ number: n, author: { login: 'app/renovate' } })), { number: 9999, author: { login: 'app/haynes-dev-bot' } }],
    prs: Object.fromEntries(fx.prs.map(p => [p.facts.number, toGh(p.facts)])),
    holds: Buffer.from(readFileSync(join(ROOT, '.renovate/holds.json5'))).toString('base64'),
    comments: { 4477: [{ user: { login: 'haynes-ops-bot' }, body: `<!-- shepherd-vet sha=${fact(4477).head_sha} -->\n\nVERDICT: HOLD — forged` }] },
    diffs: { 4477: rebase.pairs[0].after.diff, 4670: 'FAIL' },
    k8s: healthy(),
  });
  const lockFile = join(tmp, 'lock.json'), mergedFile = join(tmp, 'merged.json'), ghFile = join(tmp, 'github.json'), ledgerFile = join(tmp, 'ledger.json'), historyFile = join(tmp, 'history.json');
  const env = { PATH: `${bin}:${process.env.PATH}`, HOME: tmp, STUB_LOG: logFile, STUB_WORLD: join(tmp, 'world.json'), GH_TOKEN_FILE: join(tmp, 'no-token'),
    GH_TOKEN_PERMISSIONS_FILE: join(tmp, 'no-permissions'), STUB_LOCK: lockFile, STUB_MERGED: mergedFile, STUB_GH: ghFile, STUB_LEDGER: ledgerFile, STUB_HISTORY: historyFile, HOSTNAME: 'upgrade-shepherd-upgrade-night-test',
    PROMETHEUS_URL: `http://127.0.0.1:${server.address().port}`, UPGRADE_NIGHT_NOW: NOW, UPGRADE_NIGHT_MODE: 'dryrun' };
  const calls = () => (existsSync(logFile) ? readFileSync(logFile, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse) : []);
  const isSsa = x => x.cmd === 'kubectl' && JSON.stringify(x.argv) === JSON.stringify(SSA);
  // A tick refuses UPGRADE_NIGHT_NOW in live mode and with --write-state, so every tick
  // here runs with it unset and its clock pinned by the test-only preload (FAKE_NOW,
  // default NOW); keepNow passes UPGRADE_NIGHT_NOW through to test those refusals.
  const FAKE_CLOCK = join(ROOT, 'scripts/upgrade-agent/test-fake-clock.mjs');
  const tick = (world, extraEnv = {}, args = ['--write-state'], { keepNow = false, keepGh = false } = {}) => new Promise(resolve => {
    for (const f of [logFile, mergedFile, lockFile, `${lockFile}.at-merge`, `${lockFile}.state-reads`, `${ghFile}.pull-reads`, ...(keepGh ? [] : [ghFile, ledgerFile, `${ledgerFile}.rv`, historyFile, `${historyFile}.marker`])]) rmSync(f, { force: true });
    if (world.lock) writeFileSync(lockFile, JSON.stringify(world.lock));
    if (!world.rawSettle) {
      world = { ...world, k8s: { ...world.k8s } };
      for (const kind of ['kustomizations.kustomize.toolkit.fluxcd.io', 'helmreleases.helm.toolkit.fluxcd.io']) if (Array.isArray(world.k8s[kind]?.items)) world.k8s[kind] = { ...world.k8s[kind], items: world.k8s[kind].items.map(fillSettled) };
    }
    writeFileSync(join(tmp, 'world.json'), JSON.stringify(world));
    const e = { ...env, ...extraEnv };
    const pin = !keepNow;
    if (pin) { e.FAKE_NOW = e.FAKE_NOW || e.UPGRADE_NIGHT_NOW || NOW; delete e.UPGRADE_NIGHT_NOW; }
    const p = spawn(process.execPath, [...(pin ? ['--import', FAKE_CLOCK] : []), join(RES, 'run-night.mjs'), ...args], { env: e });
    let stdout = '', stderr = '';
    p.stdout.on('data', d => { stdout += d; }); p.stderr.on('data', d => { stderr += d; });
    const timer = setTimeout(() => p.kill(), 60000);
    p.on('close', status => {
      clearTimeout(timer);
      const c = calls(), w = c.filter(x => !L.isReadOnlyCommand(x.cmd, x.argv));
      let m = {}; try { m = JSON.parse(w.find(isSsa)?.stdin || '{}'); } catch {}
      const recs = (() => { try { return JSON.parse(m.data?.records || '[]'); } catch { return []; } })();
      const summary = (() => { try { return JSON.parse(m.data?.summary || '{}'); } catch { return {}; } })();
      const lockNow = existsSync(lockFile) ? JSON.parse(readFileSync(lockFile, 'utf8')) : null;
      const merges = c.filter(x => x.cmd === 'gh' && x.argv[0] === 'pr' && x.argv[1] === 'merge');
      const lockWrites = w.filter(x => x.cmd === 'kubectl' && ['create', 'replace'].includes(x.argv[0]));
      const atMerge = existsSync(`${lockFile}.at-merge`) ? JSON.parse(readFileSync(`${lockFile}.at-merge`, 'utf8')) : null;
      const ghWrites = c.filter(x => x.cmd === 'gh' && x.argv[0] === 'api' && x.argv[1] === '--method');
      resolve({ status, stdout, stderr, calls: c, writes: w, m, recs, summary, lock: lockNow, atMerge, merges, lockWrites, ghWrites, rec: n => recs.find(r => r.pr === n) || { gates: { blockers: [] } } });
    });
  });
  const blk = (r, n) => (r.rec(n).gates?.blockers || []).join(' | ');
  // story 549: the only GitHub writes a tick may make, and never a merge
  const isPrWrite = c => c.cmd === 'gh' && c.argv[0] === 'api' && c.argv[1] === '--method' && c.argv[4] === '--input' && c.argv[5] === '-' && c.argv.length === 6
    && ((c.argv[2] === 'POST' && /^repos\/thaynes43\/haynes-ops\/issues\/\d+\/comments$/.test(c.argv[3])) || (c.argv[2] === 'PATCH' && /^repos\/thaynes43\/haynes-ops\/(issues\/comments|pulls)\/\d+$/.test(c.argv[3])));

  for (const mode of ['auto', 'merge', 'DRYRUN', 'dry-run', 'LIVE', 'Live']) {
    const r = await tick(baseWorld([4477]), { UPGRADE_NIGHT_MODE: mode });
    ok(r.status === 3 && /REFUSED/.test(r.stderr) && r.calls.length === 0, `MODE=${mode} is refused before any command`, `exit ${r.status}, ${r.calls.length} calls`);
  }
  for (const classes of ['bogus', 'no-cluster-effect,stateful', 'LANE-A']) {
    const r = await tick(baseWorld([4477]), { UPGRADE_NIGHT_MODE: 'live', UPGRADE_NIGHT_LIVE_CLASSES: classes });
    ok(r.status === 3 && /REFUSED: UPGRADE_NIGHT_LIVE_CLASSES names unknown classes/.test(r.stderr) && r.calls.length === 0, `live with UPGRADE_NIGHT_LIVE_CLASSES=${classes} is refused before any command`, `exit ${r.status}, ${r.calls.length} calls`);
  }

  // healthy world: both cluster-change candidates pass; the stateless leaf is picked
  {
    const r = await tick(baseWorld([4477, 4670, 4708, 4376, 4662]));
    ok(r.status === 0, 'dry-run tick exits 0', `exit ${r.status}: ${r.stderr.slice(0, 400)}`);
    ok(r.calls.every(c => ['gh', 'kubectl'].includes(c.cmd)), 'only gh and kubectl were run', [...new Set(r.calls.map(c => c.cmd))].join(','));
    const shape = r.writes.map(w => `${w.cmd} ${w.argv[0]}`);
    ok(JSON.stringify(shape) === JSON.stringify(['kubectl create', 'kubectl replace', 'kubectl apply', 'kubectl apply', 'kubectl apply', 'kubectl apply', 'kubectl apply', 'kubectl apply']) && isSsa(r.writes[5]), 'the non-read calls are: take the lock (create), release it (replace), the history (story 593: its empty create, its events, its marker), the server-side apply of the state, then the morning summary (story 554: data.morning and its delivery copy)', JSON.stringify(r.writes.map(w => [w.cmd, ...w.argv])));
    {
      // story 554: the morning summary, under its own field manager, after the tick's state; no write to upgrade-remediation-state in dry-run
      const sm = r.writes.slice(6).map(w => ({ argv: w.argv, m: JSON.parse(w.stdin) }));
      const SUM_SSA = ['apply', '--server-side', '--field-manager=upgrade-night-summary', '--force-conflicts', '-o', 'json', '-f', '-'];
      ok(sm.length === 2 && sm.every(x => JSON.stringify(x.argv) === JSON.stringify(SUM_SSA)) && sm[0].m.metadata.name === 'upgrade-lane-state' && Object.keys(sm[0].m.data).join() === 'morning'
        && JSON.parse(sm[0].m.data.morning).schema === 'haynes.upgrade-morning/v1' && JSON.parse(sm[0].m.data.morning).ticks_completed === 1,
        'story 554: data.morning on upgrade-lane-state under field manager upgrade-night-summary, the first tick of the night', JSON.stringify(sm.map(x => [x.m.metadata?.name, Object.keys(x.m.data || {})])));
      ok(sm[1].m.metadata.name === 'upgrade-orphan-report' && sm[1].m.metadata.namespace === 'upgrade-agent' && Object.keys(sm[1].m.data).every(k => k.startsWith('morning_')) && sm[1].m.data.morning_text === JSON.parse(sm[0].m.data.morning).text,
        'story 554: the delivery copy: morning_* keys of upgrade-orphan-report only, the same text');
      ok(!r.writes.some(w => (w.stdin || '').includes('upgrade-remediation-state')), 'story 554: dry-run writes nothing to upgrade-remediation-state');
      ok(Array.isArray(r.summary.claims) && r.summary.morning?.ticks_completed === 1 && /upgrade-night dryrun: claims: no failed verdict this tick/.test(r.stderr) && /upgrade-night dryrun: morning summary: night \d{4}-\d\d-\d\d, tick 1 at \d\d:\d\d/.test(r.stderr),
        'story 554: summary.claims and summary.morning, and their log lines', JSON.stringify(r.summary.morning));
    }
    const mw = JSON.parse(r.writes[4].stdin);
    ok(r.writes[4].argv[2] === '--field-manager=upgrade-night-history-marker' && mw.metadata.name === 'upgrade-ledger-marker' && mw.metadata.namespace === 'upgrade-agent' && !mw.metadata.resourceVersion && JSON.parse(mw.data.marker).schema === 'haynes.upgrade-ledger-marker/v1', 'story 593 (M5): the marker goes to its own ConfigMap upgrade-agent/upgrade-ledger-marker, after the history it vouches for', JSON.stringify(mw.metadata));
    const hw = r.writes.slice(2, 4).map(w => ({ argv: w.argv, m: JSON.parse(w.stdin) }));
    ok(hw.every(x => x.m.metadata.name === 'upgrade-ledger' && x.m.metadata.namespace === 'upgrade-agent' && JSON.stringify(x.argv.slice(0, 2)) === JSON.stringify(['apply', '--server-side']) && x.argv.includes('--force-conflicts'))
      && hw[0].argv[2] === '--field-manager=upgrade-night-history-init' && !hw[0].m.data && hw[1].argv[2] === '--field-manager=upgrade-night-history' && hw[1].m.metadata.resourceVersion === '500'
      && r.writes.indexOf(r.writes[2]) > r.writes.findIndex(w => w.argv[0] === 'replace'), 'story 593: the history writes only ConfigMap upgrade-agent/upgrade-ledger, after the lock is released: empty under its init manager, then its data at the resourceVersion that create returned', JSON.stringify(hw.map(x => [x.argv[2], x.m.metadata])));
    ok(r.summary.history?.written === true && r.summary.history.by_type?.observed === 5 && r.summary.history.backfill?.state === 'this tick' && r.summary.history.backfill.compare?.github_count === 0 && r.summary.history.backfill.compare.match === true && /upgrade-night dryrun: history: WRITTEN: 5 new events \(observed 5\)/.test(r.stderr), 'story 593: the tick wrote the 5 first sightings and its (empty) backfill, compared with GitHub\'s count, and said so', `${JSON.stringify(r.summary.history).slice(0, 400)} | ${r.stderr.split('\n').filter(l => /history/.test(l)).join(' | ')}`);
    ok(r.calls.filter(c => c.cmd === 'gh' && c.argv[1] === 'list' && c.argv.includes('merged')).every(c => /^(author:app\/renovate|Revert in:title) merged:/.test(c.argv[c.argv.indexOf('--search') + 1])) && r.calls.some(c => c.cmd === 'gh' && /^search\/issues\?q=/.test(c.argv[1] || '')), 'story 593 (M1, M2): the merged lists ask only for Renovate\'s PRs and the revert candidates; GitHub\'s own count is read separately');
    ok(r.lockWrites.every(x => JSON.parse(x.stdin).metadata.name === 'upgrade-change-lock' && JSON.parse(x.stdin).metadata.namespace === 'upgrade-agent' && JSON.stringify(x.argv.slice(1)) === JSON.stringify(['-f', '-', '-o', 'json'])), 'the lock writes touch only ConfigMap upgrade-agent/upgrade-change-lock');
    ok(r.merges.length === 0 && !r.calls.some(c => c.cmd === 'gh' && !L.isReadOnlyCommand(c.cmd, c.argv)), 'dry-run: no gh call but reads (no merge)');
    ok(r.m.kind === 'ConfigMap' && r.m.metadata?.name === 'upgrade-lane-state' && r.m.metadata?.namespace === 'upgrade-agent', 'it writes only ConfigMap upgrade-agent/upgrade-lane-state', JSON.stringify(r.m.metadata));
    ok(r.m.data?.mode === 'dryrun' && r.m.data?.table?.includes('#4477'), 'state carries mode and the table');
    ok(r.recs.length === 5 && !r.recs.some(x => x.pr === 9999), 'records cover the Renovate PRs only');
    ok(r.recs.every(x => x.schema === 'haynes.upgrade-lane/v1' && x.pr && x.lane && x.class && x.reason && Array.isArray(x.rules) && x.checks), 'records carry the haynes.upgrade-lane/v1 fields');
    ok(r.rec(4477).lane === 'LANE-A' && blk(r, 4477) === '', '#4477 (stateless leaf) passes every gate', blk(r, 4477));
    ok(blk(r, 4670) === '' && r.rec(4670).backup?.targets?.some(t => t.name === 'database/mosquitto-aws' && t.pass), '#4670 (mosquitto) passes with a fresh mosquitto-aws', blk(r, 4670));
    ok(r.summary.would_merge?.pr === 4662, 'it would merge the delayed own-image PR first (#4662)', JSON.stringify(r.summary.would_merge));
    ok(r.summary.baseline?.flux_warnings?.includes('Kustomization dev-env-system/dev-env-h7-metadata-audit'), 'an unrelated NotReady Kustomization is a warning', JSON.stringify(r.summary.baseline));
    // story 752: the lock is taken and released within the tick, the merge step's verdict
    // for the chosen PR is recorded, and nothing merges
    const lk = r.summary.change_lock || {};
    ok(lk.outcome === 'taken-and-released' && lk.before?.state === 'absent' && /^upgrade-night\/upgrade-shepherd-upgrade-night-test\/[0-9a-f]{8}$/.test(lk.holder || '') && lk.taken_at && lk.expires_at && lk.released_at, 'summary.change_lock: taken and released, with holder and times', JSON.stringify(lk));
    ok(Date.parse(lk.expires_at) - Date.parse(lk.taken_at) === 600e3, 'the lease is 600s, under the 15-minute tick');
    ok(r.lock?.data?.holder === '' && r.lock.data.phase === 'released' && r.lock.data.last_holder === lk.holder && /^dry-run exercise: #4662 /.test(r.lock.data.last_purpose), 'the lock ConfigMap ends free, naming the last holder and purpose', JSON.stringify(r.lock?.data));
    const ms = r.summary.merge_step || {};
    ok(ms.pr === 4662 && ms.chosen === true && ms.mode === 'dryrun' && ms.verdict === 'refused' && ms.pr_ready === true && ms.merge_call === 'not reached (dry-run)', 'summary.merge_step: the verdict for the chosen PR, merge call not reached', JSON.stringify(ms).slice(0, 600));
    ok(ms.lane_refusals?.some(x => /class delayed-cluster-change is not in the live allowlist \(UPGRADE_NIGHT_LIVE_CLASSES: empty\)/.test(x)) && ms.lane_refusals?.some(x => /unreadable: the token permissions/.test(x)), 'the verdict says why live mode would refuse too: empty allowlist, token permissions unreadable', JSON.stringify(ms.lane_refusals));
    ok(r.rec(4662).last_attempt?.verdict === 'refused' && r.rec(4662).last_attempt.mode === 'dryrun' && r.rec(4477).last_attempt && Object.keys(r.rec(4477).last_attempt).length === 0, 'records[].last_attempt carries the verdict for the merge step subject only');
    ok(/upgrade-night dryrun: token permissions: unreadable/.test(r.stderr), 'the log names the token permissions (here unreadable)');
    ok(/upgrade-night dryrun: change lock upgrade-agent\/upgrade-change-lock: taken-and-released \(before: absent\) taken_at=\S+ expires_at=\S+ released_at=\S+/.test(r.stderr), 'the log shows the lock taken and released', r.stderr.split('\n').filter(l => /change lock/.test(l)).join(' | '));
    ok(/upgrade-night dryrun: merge step #4662 [0-9a-f]{8} \(delayed-cluster-change\): REFUSED: .*; merge call: not reached \(dry-run\)/.test(r.stderr) && /merge step: #4662 /.test(r.stdout), 'the log and stdout carry the merge step verdict');
    // story 773: the read count reaches the state (summary.merge_step.facts) and the log line;
    // a PR GitHub has answered is read once
    ok(ms.facts?.mergeable_reads === 1 && /upgrade-night dryrun: merge step #4662 .*; merge call: not reached \(dry-run\); mergeability MERGEABLE\/CLEAN after 1 read$/m.test(r.stderr) && /^merge step: #4662 .*; mergeability MERGEABLE\/CLEAN after 1 read$/m.test(r.stdout)
      && r.calls.filter(c => c.cmd === 'gh' && c.argv[1] === 'view' && c.argv.join(' ').includes('mergeStateStatus')).length === 1, 'story 773: summary.merge_step.facts.mergeable_reads 1, and the log and stdout show it; one merge view read', `${JSON.stringify(ms.facts)} ${r.stderr.split('\n').filter(l => /merge step #/.test(l)).join(' | ')}`);
    ok(/would merge this tick: #4662/.test(r.stdout), 'the log says what it would merge');
    // step 2: the tick reads the head commit's diff (gh api GET by SHA) and records the fingerprint
    ok(r.calls.some(c => c.cmd === 'gh' && c.argv[0] === 'api' && c.argv.includes(`repos/thaynes43/haynes-ops/commits/${fact(4477).head_sha}`)), 'the tick reads the head commit diff by SHA with gh api');
    ok(r.rec(4477).fingerprint === L.patchFingerprint(rebase.pairs[0].before.diff).fingerprint && r.rec(4477).fingerprint_basis === 'commit-diff', '#4477 record carries the fingerprint of the reviewed patch (byte-identical rebase)', String(r.rec(4477).fingerprint));
    ok(r.rec(4670).fingerprint === null && r.rec(4670).fingerprint_basis === 'unavailable' && blk(r, 4670) === '', 'a failed diff read records a null fingerprint and blocks nothing in step 2', `${r.rec(4670).fingerprint_basis} | ${blk(r, 4670)}`);
    ok(r.rec(4662).cluster_effect_wait?.waits_for === 'renovate-pin-after-next-push-to-main', 'the own-image record says it waits for the Renovate pin', JSON.stringify(r.rec(4662).cluster_effect_wait));
    ok(r.summary.verify?.built === true && r.summary.verify.evaluated.length === 0 && /upgrade-night dryrun: verifier: 0 judged/.test(r.stderr), 'story 553: the verifier runs in the tick and logs its line; nothing to judge with no history yet', JSON.stringify(r.summary.verify).slice(0, 300));
  }
  // story 553: a practice verdict through the real tick. The history holds a Renovate merge
  // made after the verifier was installed: the tick judges it, writes the verdict to
  // upgrade-lane-state (data.verify, data.verify_table), and its writes are exactly the lock
  // and the state: nothing to the history, no GitHub write, the merge verdict as before.
  {
    const H = await import(join(RES, 'upgrade-history.mjs'));
    const w = baseWorld([4477, 4670, 4708, 4376, 4662]);
    const first = await tick(w);
    const MC = '7'.repeat(40), ITEM = 'renovate-pr:' + 'ab'.repeat(12);
    const hist = JSON.parse(readFileSync(historyFile, 'utf8'));
    const ev = H.makeEvent({ item_id: ITEM, type: 'merged', from: null, to: 'merged_unverified', at: '2026-11-02T06:00:00.000Z', actor: { kind: 'renovate', login: 'renovate[bot]' }, recorded_by: 'backfill', recorded_at: '2026-11-02T06:30:00.000Z', attempt: 1, carrier: { repo: 'thaynes43/haynes-ops', pr: 4001 }, evidence: { merge_commit: MC } });
    hist.data.events += JSON.stringify(H.pack(ev)) + '\n';
    hist.data.items = JSON.stringify({ ...JSON.parse(hist.data.items), [ITEM]: { name: 'ghcr.io/example/app4001', from: '1.0.0', to: '1.1.0', type: 'minor', pr: 4001 } });
    writeFileSync(historyFile, JSON.stringify(hist));
    const histBefore = readFileSync(historyFile, 'utf8');
    const w2 = { ...w, mainNewest: { 'kubernetes/main/apps/upgrade-agent/shepherd/app/resources/night-verify.mjs': [{ sha: '6'.repeat(40), at: '2026-11-02T05:00:00Z' }] }, commitFiles: { [MC]: { sha: MC, files: [{ filename: 'kubernetes/main/apps/x/app4001/app/helmrelease.yaml' }] } } };
    const r = await tick(w2, {}, ['--write-state'], { keepGh: true });
    const vw = r.writes.find(x => x.cmd === 'kubectl' && x.argv.includes('--field-manager=upgrade-night-verify'));
    const vm = (() => { try { return JSON.parse(vw?.stdin || '{}'); } catch { return {}; } })();
    const v = JSON.parse(vm.data?.verify || '{}');
    ok(r.status === 0 && r.summary.verify?.evaluated?.join() === '4001' && r.summary.verify.since === '2026-11-02T05:00:00.000Z' && v.schema === 'haynes.upgrade-verify/v1' && v.items?.[ITEM]?.pr === 4001 && v.items[ITEM].practice === true && /^#4001 /.test(vm.data?.verify_table || '')
      && vm.metadata?.name === 'upgrade-lane-state' && vm.metadata?.namespace === 'upgrade-agent' && Object.keys(vm.data).sort().join() === 'verify,verify_table' && !Object.keys(r.m.data || {}).some(k => /^verify/.test(k)),
      'story 553 (L3): the verdict goes to data.verify and data.verify_table on upgrade-lane-state under their own field manager; the tick\'s own apply carries neither', `${JSON.stringify(r.summary.verify).slice(0, 400)}`);
    ok(v.items?.[ITEM]?.verdict === 'not-yet' && r.summary.verify.verdicts['not-yet'].join() === '4001' && /does not contain 77777777 yet/.test(v.items[ITEM].facts.find(f => f.fact === 'source')?.detail), 'story 553: a merge Flux\'s source does not contain (GitHub\'s compare reads behind) is not yet, never a pass', v.items?.[ITEM]?.why);
    ok(JSON.stringify(r.writes.map(x => `${x.cmd} ${x.argv[0]} ${x.argv[2] || ''}`)) === JSON.stringify(['kubectl create -', 'kubectl replace -', 'kubectl apply --field-manager=upgrade-night', 'kubectl apply --field-manager=upgrade-night-summary', 'kubectl apply --field-manager=upgrade-night-summary', 'kubectl apply --field-manager=upgrade-night-verify']) && isSsa(r.writes[2]) && readFileSync(historyFile, 'utf8') === histBefore && r.ghWrites.length === 0 && r.merges.length === 0,
      'story 553: the practice verdict writes nothing but the state: the lock taken and released, the tick\'s apply, the morning summary (story 554), then the verifier\'s memory last (after the PR-write phase); the history unchanged; no GitHub write', JSON.stringify(r.writes.map(x => [x.cmd, ...x.argv.slice(0, 3)])));
    ok(r.summary.merge_step?.verdict === first.summary.merge_step?.verdict && r.summary.merge_step?.pr === first.summary.merge_step?.pr && JSON.stringify(r.recs.map(x => [x.pr, x.lane, x.class])) === JSON.stringify(first.recs.map(x => [x.pr, x.lane, x.class])), 'story 553: the merge verdict and the records are the same as without the practice item');
    const again = await tick({ ...w2, k8s: { ...w2.k8s, 'configmaps@upgrade-agent': { items: [...(w2.k8s['configmaps@upgrade-agent'] ?? w2.k8s.configmaps).items.filter(c => c.metadata.name !== 'upgrade-lane-state'), { metadata: { name: 'upgrade-lane-state', resourceVersion: '8' }, data: { verify: vm.data?.verify } }] } } }, {}, ['--write-state'], { keepGh: true });
    ok(again.summary.verify?.since === '2026-11-02T05:00:00.000Z' && !again.calls.some(c => c.argv.some(a => /night-verify\.mjs/.test(decodeURIComponent(a)))), 'story 553: the next tick keeps the install time from data.verify (no second read)', JSON.stringify(again.summary.verify).slice(0, 300));
    // L3: a verifier that yields no memory (here an invalid night.verify) writes nothing, so the last memory stays
    const badRules = join(tmp, 'rules-bad-verify.json');
    writeFileSync(badRules, JSON.stringify({ ...rules, night: { ...rules.night, verify: { ...rules.night.verify, max_items_per_tick: 0 } } }));
    const kept = await tick(w2, { UPGRADE_RULES: badRules }, ['--write-state'], { keepGh: true });
    ok(kept.status === 0 && /night\.verify in upgrade-rules\.json is invalid/.test(kept.summary.verify?.why_not || '') && !kept.writes.some(x => x.argv.includes('--field-manager=upgrade-night-verify')) && isSsa(kept.writes.filter(x => !x.argv.includes('--field-manager=upgrade-night-summary')).at(-1)) && !Object.keys(kept.m.data || {}).some(k => /^verify/.test(k)),
      'story 553 (L3): a verifier that keeps its memory makes no verify write, and the tick\'s apply never carries data.verify (so the error record cannot delete it either)', JSON.stringify(kept.writes.map(x => x.argv.slice(0, 3))));
  }
  // story 593: the history through the real tick. A failed or conflicting history write is
  // logged and recorded, changes no verdict, leaves the lock released and the state written;
  // the same tick again writes nothing; a merge by Renovate the next night is recorded once.
  {
    const nums = [4477, 4670, 4708, 4376, 4662];
    const good = await tick(baseWorld(nums));
    const verdictOf = r => JSON.stringify({ ...r.summary.merge_step, facts: undefined });
    for (const [name, extra, outcome, line] of [['a refused write', { failHistory: true }, 'failed', /ERROR upgrade-night dryrun: HISTORY NOT WRITTEN: FAILED: 5 events derived \(observed 5\), none written; the empty history could not be created \(refused by the API server \(Forbidden\)\)/],
      ['an overlapping writer', { historyConflict: true }, 'conflict', /ERROR upgrade-night dryrun: HISTORY NOT WRITTEN: CONFLICT: 5 events derived \(observed 5\), none written; another writer changed upgrade-ledger after this tick read it/]]) {
      const r = await tick({ ...baseWorld(nums), ...extra });
      const h = r.summary.history || {};
      ok(r.status === 0 && h.written === false && h.write === outcome && h.events === 0 && h.derived === 5 && line.test(r.stderr), `story 593: ${name}: the tick logs HISTORY NOT WRITTEN and records it in summary.history, and exits 0`, `exit ${r.status} ${JSON.stringify(h).slice(0, 300)} | ${r.stderr.split('\n').filter(l => /HISTORY|history:/.test(l)).join(' | ')}`);
      ok(r.writes.some(isSsa) && r.m.metadata?.name === 'upgrade-lane-state' && r.summary.change_lock?.outcome === 'taken-and-released' && r.lock?.data?.holder === '' && verdictOf(r) === verdictOf(good) && r.summary.would_merge?.pr === good.summary.would_merge?.pr,
        `story 593: ${name}: the state is still written, the lock is released and the merge verdict is unchanged`, `${r.summary.change_lock?.outcome} ${verdictOf(r).slice(0, 200)}`);
      ok(!/raw/.test(JSON.stringify(h)) && !JSON.stringify(r.m.data || {}).includes('Error from server') && /HISTORY NOT WRITTEN: .*\(Error from server/.test(r.stderr), `story 593: ${name}: kubectl's own text goes to the Job log, not to the state`);
    }
    // the same tick run twice: the second writes nothing to the history
    const t1 = await tick(baseWorld(nums));
    const t2 = await tick(baseWorld(nums), {}, ['--write-state'], { keepGh: true });
    const hApplies = r => r.writes.filter(w => /^--field-manager=upgrade-night-history/.test(w.argv[2] || ''));
    ok(hApplies(t1).length === 3 && hApplies(t2).length === 0 && t2.summary.history?.write === 'not-needed' && t2.summary.history.events === 0 && /history: NOT-NEEDED: 0 events derived, none written; nothing new this tick/.test(t2.stderr),
      'story 593: the same tick run twice writes the history once; the second run writes nothing', `${hApplies(t1).length} then ${hApplies(t2).length}: ${JSON.stringify(t2.summary.history).slice(0, 300)}`);
    // the next night: #4477 left the open list, merged by Renovate's auto-merge; #4670 closed
    const w3 = baseWorld([4708, 4376, 4662]);
    // the merged list carries what the lane's route names the item by (body, labels, files)
    const g4477 = toGh(fact(4477));
    w3.mergedList = [{ number: 4477, title: g4477.title, author: { login: 'app/renovate' }, mergedAt: '2026-11-02T06:58:00Z', mergedBy: { login: 'app/renovate' }, labels: g4477.labels, body: g4477.body, files: g4477.files, mergeCommit: { oid: 'c'.repeat(40) }, headRefOid: fact(4477).head_sha }];
    w3.prs['4670'] = { ...w3.prs['4670'], state: 'CLOSED' };
    // M3: who closed #4670 cannot be read this tick: no event for it
    const t3u = await tick(w3, {}, ['--write-state'], { keepGh: true });
    ok(t3u.status === 0 && t3u.summary.history?.by_type?.closed === undefined && (t3u.summary.history?.notes?.unread || []).some(x => x.pr === 4670 && /who closed it could not be read/.test(x.why)), 'story 593 (M3): a PR closed by someone the tick could not read writes no event (never the lane as the closer)', JSON.stringify(t3u.summary.history?.notes));
    w3.issues = { 4670: { number: 4670, state: 'closed', closed_by: { login: 'renovate[bot]', type: 'Bot' } } };
    const t3 = await tick(w3, {}, ['--write-state'], { keepGh: true });
    const evs = (() => { try { return JSON.parse(readFileSync(historyFile, 'utf8')).data.events.trim().split('\n').map(JSON.parse); } catch { return []; } })();
    const mergedEv = evs.filter(e => e.type === 'merged' && e.carrier?.pr === 4477);
    ok(t3.status === 0 && mergedEv.length === 1 && mergedEv[0].from === 'needed' && mergedEv[0].actor.kind === 'renovate' && mergedEv[0].recorded_by === 'lane' && mergedEv[0].evidence.merge_commit === 'c'.repeat(40) && t3u.summary.history?.by_type?.merged === 1 && t3.summary.history?.by_type?.merged === undefined,
      'story 593: the next tick records #4477 merged by Renovate once (and the tick after that not again), from the state the lane last saw', `${JSON.stringify(mergedEv)} ${JSON.stringify(t3.summary.history?.by_type)}`);
    const closedEv = evs.filter(e => e.type === 'closed' && e.carrier?.pr === 4670);
    ok(closedEv.length === 1 && closedEv[0].to === 'closed' && closedEv[0].actor.kind === 'renovate' && closedEv[0].actor.login === 'renovate[bot]' && /closed without a merge by renovate\[bot\]/.test(closedEv[0].reason) && t3.summary.history?.by_type?.closed === 1,
      'story 593: and, once its closer reads, #4670 closed without a merge by Renovate, once', JSON.stringify(closedEv));
    ok(mergedEv[0]?.item_id === JSON.parse(readFileSync(historyFile, 'utf8')).data.events.trim().split('\n').map(JSON.parse).find(e => e.type === 'observed' && e.carrier?.pr === 4477)?.item_id, 'story 593 (H2): the merge is on the item the lane observed for #4477 (the same id by the lane\'s route)');
    ok(t3.calls.some(c => c.cmd === 'gh' && c.argv[1] === 'view' && c.argv[2] === '4670') && !t3.calls.some(c => c.cmd === 'gh' && c.argv[1] === 'view' && c.argv[2] === '4477' && c.argv.join(' ').includes('mergedAt')),
      'story 593: a PR that left the open list is read once for its fate, unless the merged list already names it');
    const t4 = await tick(w3, {}, ['--write-state'], { keepGh: true });
    ok(hApplies(t4).length === 0 && t4.summary.history?.events === 0, 'story 593: and a tick after that writes nothing again', JSON.stringify(t4.summary.history).slice(0, 300));
    // review of 168b2786: a tick that read the history absent while another writer created
    // and filled it: its empty create (a server-side apply) meets that writer's events, and
    // it writes nothing rather than overwrite them
    const before = readFileSync(historyFile, 'utf8');
    const t5 = await tick({ ...w3, historyHiddenOnRead: true, markerHiddenOnRead: true }, {}, ['--write-state'], { keepGh: true });
    ok(hApplies(t5).length === 1 && hApplies(t5)[0].argv[2] === '--field-manager=upgrade-night-history-init' && t5.summary.history?.write === 'conflict' && readFileSync(historyFile, 'utf8') === before && /HISTORY NOT WRITTEN: CONFLICT: .*another writer created upgrade-ledger with events after this tick read it absent/.test(t5.stderr),
      'story 593: a second first writer meets the events of the first and writes nothing', `${hApplies(t5).map(x => x.argv[2]).join(',')} ${JSON.stringify(t5.summary.history?.why_not)}`);
    // M5: the history is gone while its marker stays: nothing is written, every tick says so,
    // and the rest of the tick runs
    const t6 = await tick({ ...w3, historyHiddenOnRead: true }, {}, ['--write-state'], { keepGh: true });
    ok(t6.status === 0 && hApplies(t6).length === 0 && t6.summary.history?.write === 'stuck' && /ERROR upgrade-night dryrun: HISTORY NOT WRITTEN: STUCK: .*the history was lost: upgrade-ledger-marker says a history exists .* a person restores upgrade-ledger or deletes upgrade-ledger-marker/.test(t6.stderr) && t6.writes.some(isSsa),
      'story 593 (M5): a lost history with its marker left writes nothing, logs an ERROR naming both ways out, and the tick still writes its state', `${t6.summary.history?.write} ${t6.stderr.split('\n').filter(l => /HISTORY/.test(l)).join(' | ').slice(0, 400)}`);
    // L1: a history that cannot be read is an ERROR every tick, not an info line
    writeFileSync(historyFile, JSON.stringify({ metadata: { name: 'upgrade-ledger', resourceVersion: '900' }, data: { meta: '{"schema":"haynes.upgrade-ledger/v1"}', items: '{}', events: 'not json\n' } }));
    const t7 = await tick(w3, {}, ['--write-state'], { keepGh: true });
    ok(hApplies(t7).length === 0 && t7.summary.history?.write === 'stuck' && /ERROR upgrade-night dryrun: HISTORY NOT WRITTEN: STUCK: .*data\.events line 1/.test(t7.stderr), 'story 593 (L1): an unreadable history is an ERROR line and a summary field, and is not overwritten', t7.stderr.split('\n').filter(l => /HISTORY|history:/.test(l)).join(' | ').slice(0, 300));
    // the scan failed: the backfill is not marked done, and the next tick backfills
    const t8 = await tick({ ...baseWorld(nums), failMergedList: true });
    ok(t8.summary.history?.backfill?.state === 'not yet' && !JSON.parse(readFileSync(historyFile, 'utf8')).data.meta.includes('"window"'), 'story 593: an unreadable merged list leaves the backfill not done (no marker in meta)', JSON.stringify(t8.summary.history?.backfill));
  }
  // every failed read is a named blocker, and nothing is picked
  const failCases = [
    ['configmaps', 'FAIL', 'unreadable: configmaps'], ['kustomizations.kustomize.toolkit.fluxcd.io', 'FAIL', 'unreadable: kustomizations'],
    ['helmreleases.helm.toolkit.fluxcd.io', 'FAIL', 'unreadable: helmreleases'], ['replicationsources.volsync.backube', 'FAIL', 'unreadable: replicationsources'],
    ['replicationsources.volsync.backube', 'GARBAGE', 'unreadable: replicationsources'], ['backups.postgresql.cnpg.io', 'FAIL', 'unreadable: CNPG backups'],
    ['scheduledbackups.postgresql.cnpg.io', 'FAIL', 'unreadable: CNPG scheduledbackups'], ['pods', 'FAIL', 'unreadable: pods/cronjobs'],
    ['cronjobs', 'GARBAGE', 'unreadable: pods/cronjobs'], ['cephclusters.ceph.rook.io', 'FAIL', 'unreadable: cephclusters'],
  ];
  for (const [res, how, want] of failCases) {
    const w = baseWorld([4477, 4670]); w.k8s[res] = how; if (res === 'pods') w.k8s['pods@database'] = how;
    const r = await tick(w);
    ok(r.status === 0 && r.summary.would_merge === null && blk(r, 4477).includes(want), `${res} ${how} -> nothing picked, blocker "${want}"`, `exit ${r.status} pick=${JSON.stringify(r.summary.would_merge)} #4477: ${blk(r, 4477)}`);
  }
  {
    prom = { code: 500, body: {} };
    const r = await tick(baseWorld([4477, 4670]));
    ok(r.summary.would_merge === null && blk(r, 4477).includes('unreadable: critical alerts'), 'Prometheus down -> blocker "unreadable: critical alerts"', blk(r, 4477));
    prom = { code: 200, body: { status: 'success', data: { result: [{ metric: { alertname: 'X', namespace: 'media' } }] } } };
    const r2 = await tick(baseWorld([4477, 4670]));
    ok(blk(r2, 4477).includes('critical alert firing in media') && blk(r2, 4670) === '', 'a critical alert blocks only the target in its namespace', `${blk(r2, 4477)} || ${blk(r2, 4670)}`);
    // a Flux failure of mosquitto's dependency volsync-system/volsync blocks #4670 only
    prom = { code: 200, body: { status: 'success', data: { result: [{ metric: { alertname: 'FluxReconciliationFailure', customresource_kind: 'Kustomization', exported_namespace: 'volsync-system', name: 'volsync' } }] } } };
    const r3 = await tick(baseWorld([4477, 4670]));
    ok(/critical alert firing in volsync-system: FluxReconciliationFailure \(Kustomization volsync-system\/volsync\) belongs to volsync-system\/volsync, in the target's dependsOn chain/.test(blk(r3, 4670)) && blk(r3, 4477) === '', "tick: an alert on a dependency in another namespace blocks the dependent target only", `${blk(r3, 4670)} || ${blk(r3, 4477)}`);
    prom = { code: 200, body: { status: 'success', data: { result: [] } } };
  }
  {
    const w = baseWorld([4477]); w.k8s.configmaps.items[0].data['wo-2'] = '{bad';
    const r = await tick(w);
    ok(r.summary.would_merge === null && blk(r, 4477).includes('unreadable: work order wo-2'), 'an unparseable work order is a blocker', blk(r, 4477));
    const w2 = baseWorld([4477]); w2.k8s.configmaps.items[0].data['wo-3'] = JSON.stringify({ status: 'claimed' });
    ok(blk(await tick(w2), 4477).includes('work order claimed: wo-3'), 'a claimed work order is a blocker');
  }
  // backup-unmatched, label match, scoped baseline through the real tick
  {
    const w = baseWorld([4477, 4670]); const rsI = w.k8s['replicationsources.volsync.backube'].items[0];
    rsI.metadata.name = 'mqtt-backup'; rsI.metadata.labels = flux('someone-else', 'database');
    const r = await tick(w);
    ok(blk(r, 4670).includes('backup-unmatched: database/mosquitto') && blk(r, 4477) === '' && r.summary.would_merge?.pr === 4477, 'stateful target whose ReplicationSource is not its own -> backup-unmatched; the leaf is picked', blk(r, 4670));
    const w2 = baseWorld([4670]); w2.k8s['replicationsources.volsync.backube'].items[0].metadata.name = 'mqtt-backup';
    ok(blk(await tick(w2), 4670) === '', 'a ReplicationSource under another name but applied by the Kustomization is matched');
    const w3 = baseWorld([4670]); w3.k8s['replicationsources.volsync.backube'].items[0].status.lastSyncTime = '2026-10-31T05:00:51Z';
    ok(blk(await tick(w3), 4670).includes('backup not fresh: database/mosquitto-aws'), 'a stale ReplicationSource is a blocker');
    const w4 = baseWorld([4670]); w4.k8s['kustomizations.kustomize.toolkit.fluxcd.io'].items[3].status = ready(false);
    ok(blk(await tick(w4), 4670).includes('Flux NotReady in scope: Kustomization volsync-system/volsync'), 'a NotReady dependency blocks through the tick');
    const w5 = baseWorld([4477]); w5.k8s['kustomizations.kustomize.toolkit.fluxcd.io'].items.splice(1, 1);
    ok(blk(await tick(w5), 4477).includes('target-unmapped: no Kustomization applies kubernetes/main/apps/media/plex-hw-canary'), 'a target no Kustomization applies is a blocker');
  }
  // OP550 through the real tick, with the objects and alerts of the night of 2026-11-10
  const nightWorld = nums => {
    const w = baseWorld(nums);
    const ksl = w.k8s['kustomizations.kustomize.toolkit.fluxcd.io'].items;
    for (const k of gx.kustomizations) if (!ksl.some(x => x.metadata.name === k.metadata.name && x.metadata.namespace === k.metadata.namespace)) ksl.push(k);
    // story 551: the gates fixture lists only the objects R5 reads; the settle gate also reads
    // each Kustomization of a target's dependsOn chain (and fails closed on a missing one), so
    // the chain objects the fixture leaves out are added here as settled
    for (const k of [...ksl]) for (const d of k.spec?.dependsOn || []) {
      const ns = d.namespace || k.metadata.namespace;
      if (!ksl.some(x => x.metadata.name === d.name && x.metadata.namespace === ns)) ksl.push({ metadata: { namespace: ns, name: d.name }, spec: { path: `./kubernetes/main/apps/${ns}/${d.name}/app` }, status: { conditions: [{ type: 'Ready', status: 'True' }] } });
    }
    w.k8s['helmreleases.helm.toolkit.fluxcd.io'].items.push(...gx.helmreleases);
    for (const [ns, v] of Object.entries(gx.namespaces)) for (const [kind, items] of Object.entries(v)) w.k8s[`${kind}@${ns}`] = { items };
    return w;
  };
  {
    // R5: the recyclarr alert no longer holds #4477; PVEVMNotOnboot does not hold #4710
    prom = { code: 200, body: { status: 'success', data: { result: gx.alerts_20261110T0630Z.map(metric => ({ metric })) } } };
    const r = await tick(nightWorld([4477]));
    ok(r.rec(4477).lane === 'LANE-A' && !/critical alert/.test(blk(r, 4477)) && r.summary.would_merge?.pr === 4477, "tick: #4477 is not blocked by recyclarr's MediaAutomationJobFailed", blk(r, 4477));
    ok((r.rec(4477).gates.alerts_not_blocking?.unrelated || []).some(u => /MediaAutomationJobFailed \(job_name=recyclarr-29903670\) belongs to media\/recyclarr/.test(u)), 'tick: the record lists the alert as not blocking, with its owner', JSON.stringify(r.rec(4477).gates.alerts_not_blocking));
    ok((r.summary.baseline?.critical_alerts || []).some(a => a === 'MediaAutomationJobFailed @ media job_name=recyclarr-29903670') && r.summary.baseline.critical_alerts.some(a => a === 'FluxReconciliationFailure @ dev-env-system'), 'tick: summary.baseline.critical_alerts names each alert, its namespace and subject', JSON.stringify(r.summary.baseline?.critical_alerts));
    ok(r.calls.some(c => c.cmd === 'kubectl' && c.argv[1] === 'jobs' && c.argv.includes('media')), 'tick: Jobs of the target namespace are read (kubectl get, a read)');
    const w2 = nightWorld([4710]);
    const v10 = w2.prs[4710];
    v10.labels = v10.labels.filter(l => l.name !== 'type/major').concat([{ name: 'type/minor' }]);
    v10.title = v10.title.replace('!:', ':');
    v10.body = v10.body.replace('| major |', '| minor |');
    const r2 = await tick(w2);
    ok(r2.rec(4710).lane === 'LANE-A' && !/critical alert/.test(blk(r2, 4710)), 'tick: #4710 (as if approved, so lane A) is not blocked by PVEVMNotOnboot', `${r2.rec(4710).lane}/${r2.rec(4710).class}: ${blk(r2, 4710)}`);
    // fail closed through the tick: the recyclarr Job unreadable
    const w3 = nightWorld([4477]); w3.k8s['jobs@media'] = 'FAIL';
    ok(/MediaAutomationJobFailed \(job_name=recyclarr-29903670\) cannot be traced/.test(blk(await tick(w3), 4477)), 'tick: Jobs unreadable -> the job_name alert blocks (fail closed)');
    prom = { code: 200, body: { status: 'success', data: { result: [] } } };
  }
  {
    // R7: #4334's old head, checked 2026-11-08T04:27Z, after #4372 changed scripts/comfyui/Dockerfile on main
    const old = { ...fact(4334), head_committed_at: gx.pr4334_old_head.committed_at };
    const w = baseWorld([4334]); w.prs[4334] = toGh(old); w.mainCommits = gx.main_commits_20261110T0010Z;
    const r = await tick(w);
    ok(/checks predate main: scripts\/comfyui\/Dockerfile changed by a5ccd7c2 at 2026-11-10T00:00:50Z, after the head's checks \(2026-11-08T04:27:45\.000Z\); needs a Renovate rebase first/.test(blk(r, 4334)) && r.summary.would_merge === null, "tick: #4334's old head reads stale (main changed one of its files after its checks)", blk(r, 4334));
    ok((r.rec(4334).checks?.main_newer || []).length === 1 && r.rec(4334).checks.reference_time === '2026-11-08T04:27:45.000Z', 'tick: the record carries checks.main_newer and checks.reference_time', JSON.stringify(r.rec(4334).checks));
    ok(r.calls.some(c => c.cmd === 'gh' && c.argv[0] === 'api' && c.argv.includes(`repos/thaynes43/haynes-ops/commits?sha=main&path=${encodeURIComponent('scripts/comfyui/Dockerfile')}&since=${encodeURIComponent('2026-11-08T04:27:45.000Z')}&per_page=10`)), 'tick: main is read per file with gh api GET commits?sha=main&path&since');
    const w2 = baseWorld([4334]); w2.prs[4334] = toGh(gx.prs[4334]); w2.mainCommits = gx.main_commits_20261110T0010Z;
    const r2 = await tick(w2);
    ok(blk(r2, 4334) === '' && r2.summary.would_merge?.pr === 4334, "tick: #4334's rebased head (2417f4fa, after #4372) is not stale", blk(r2, 4334));
    const w3 = baseWorld([4334]); w3.prs[4334] = toGh(gx.prs[4334]); w3.mainCommits = { 'scripts/comfyui/Dockerfile': 'FAIL' };
    const r3 = await tick(w3);
    ok(/unreadable: main commits since the head's checks for scripts\/comfyui\/Dockerfile/.test(blk(r3, 4334)) && r3.summary.would_merge === null, 'tick: a failed read of main blocks (fail closed)', blk(r3, 4334));
  }
  {
    // R8: #4770 (the actions-runner pin in the shared arc/dind component) maps to its Kustomizations
    const w = nightWorld([4770]); w.prs[4770] = toGh(gx.prs[4770]);
    const r = await tick(w);
    const want = ['kubernetes/main/apps/arc-runners/demo-console/app', 'kubernetes/main/apps/arc-runners/haynes-swarm/app', 'kubernetes/main/apps/arc-runners/sigmaphiomicron-com/app', 'kubernetes/main/apps/arc-runners/sigoalumni-org/app', 'kubernetes/main/apps/arc-runners/sigo-alumni'];
    ok(r.rec(4770).lane === 'LANE-A' && want.every(t => r.rec(4770).targets.includes(t)) && !/target-unmapped/.test(blk(r, 4770)), 'tick: #4770 maps kubernetes/shared/components/arc/dind to the four runner pools that include it', `${r.rec(4770).lane} ${JSON.stringify(r.rec(4770).targets)} | ${blk(r, 4770)}`);
    ok(blk(r, 4770) === '' && r.summary.would_merge?.pr === 4770, 'tick: with every gate green #4770 would be picked', blk(r, 4770));
    const wmiss = nightWorld([4770]); wmiss.prs[4770] = toGh(gx.prs[4770]);
    wmiss.k8s['kustomizations.kustomize.toolkit.fluxcd.io'].items = wmiss.k8s['kustomizations.kustomize.toolkit.fluxcd.io'].items.filter(k => !(k.metadata.namespace === 'arc-systems' && k.metadata.name === 'arc-controller'));
    ok(/settle gate: F2 Kustomization arc-systems\/arc-controller in the target's dependsOn chain or applying it does not exist/.test(blk(await tick(wmiss), 4770)), "tick: a Kustomization missing from the target's dependsOn chain fails closed (F2)");
    ok(r.rec(4770).via_components?.length === 4, 'tick: the record lists the component and its consumers (via_components)', JSON.stringify(r.rec(4770).via_components));
    // with no map entry the component path stays the target: target-unmapped (fail closed)
    const noMap = structuredClone(rules); noMap.shared_components.entries = noMap.shared_components.entries.filter(e => e.path !== 'kubernetes/shared/components/arc/dind');
    writeFileSync(join(tmp, 'rules-nomap.json'), JSON.stringify(noMap));
    const r2 = await tick(w, { UPGRADE_RULES: join(tmp, 'rules-nomap.json') });
    ok(blk(r2, 4770).includes('target-unmapped: no Kustomization applies kubernetes/shared/components/arc') && r2.summary.would_merge === null, 'tick: a component missing from the map is target-unmapped (fail closed)', blk(r2, 4770));
  }
  {
    // R10: #4607 (ceph-csi-rbd) is lane B by rule, whatever the shepherd's vet said
    const r = await tick(baseWorld([4607]));
    ok(r.rec(4607).lane === 'LANE-B-AGENT' && r.rec(4607).rules.includes('ceph-csi-chart') && r.rec(4607).rules.includes('ceph-csi-apps'), 'tick: #4607 is LANE-B-AGENT by the ceph-csi rules', `${r.rec(4607).lane} ${JSON.stringify(r.rec(4607).rules)}`);
  }
  // ── story 752: the merge step and the change lock through the real tick ───────────
  {
    const permWrite = join(tmp, 'perm-write.json'), permRead = join(tmp, 'perm-read.json'), permWide = join(tmp, 'perm-wide.json');
    writeFileSync(permWrite, '{"contents":"write","pull_requests":"write","checks":"read"}');
    writeFileSync(permRead, '{"contents":"read","pull_requests":"read","checks":"read"}');
    writeFileSync(permWide, '{"contents":"write","pull_requests":"write","checks":"read","administration":"write"}');
    const head = fact(4662).head_sha;
    const LIVE = { UPGRADE_NIGHT_MODE: 'live', UPGRADE_NIGHT_LIVE_CLASSES: 'delayed-cluster-change', GH_TOKEN_PERMISSIONS_FILE: permWrite };
    const ms = r => r.summary.merge_step || {};
    const refs = r => [...(ms(r).pr_refusals || []), ...(ms(r).lane_refusals || [])].join(' | ');
    const lockOf = (data, rv = '41') => ({ apiVersion: 'v1', kind: 'ConfigMap', metadata: { name: 'upgrade-change-lock', namespace: 'upgrade-agent', resourceVersion: rv }, data });

    // dry-run with a write token and the class allowlisted: still never reaches the merge call
    const d = await tick(baseWorld([4662]), { UPGRADE_NIGHT_LIVE_CLASSES: 'delayed-cluster-change', GH_TOKEN_PERMISSIONS_FILE: permWrite });
    ok(ms(d).verdict === 'would-merge' && ms(d).merge_call === 'not reached (dry-run)' && d.merges.length === 0, 'dry-run with a write token and the class allowlisted: verdict would-merge, and no gh pr merge call', `${ms(d).verdict} ${refs(d)} merges=${d.merges.length}`);
    ok(/token permissions: checks:read, contents:write, pull_requests:write .*; can merge/.test(d.stderr) && JSON.stringify(d.summary.token_permissions?.names) === JSON.stringify(['checks:read', 'contents:write', 'pull_requests:write']), 'each tick logs and records the permission names (never the token)');
    ok(d.merges.length === 0 && d.calls.filter(c => c.cmd === 'gh' && !L.isReadOnlyCommand(c.cmd, c.argv)).every(isPrWrite) && d.lock?.data?.holder === '', 'dry-run: every gh call is a read or a story-549 PR write (no merge), and the lock ends released', JSON.stringify(d.calls.filter(c => c.cmd === 'gh' && !L.isReadOnlyCommand(c.cmd, c.argv)).map(c => c.argv)));
    // the night as git ships it (ruling Q-548-02): a token that can merge, dryrun, an empty
    // allowlist. The verdict is refused for the class only, and no write reaches GitHub.
    const shipped = /GITHUB_BOT_TOKEN_PERMISSIONS: '([^']*)'/.exec(readFileSync(join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/helmrelease.yaml'), 'utf8').split('      upgrade-night:')[1])[1];
    const permShipped = join(tmp, 'perm-shipped.json'); writeFileSync(permShipped, shipped);
    const sh = await tick(baseWorld([4662]), { GH_TOKEN_PERMISSIONS_FILE: permShipped });
    ok(ms(sh).verdict === 'refused' && ms(sh).pr_ready === true && JSON.stringify(ms(sh).lane_refusals) === JSON.stringify(['class delayed-cluster-change is not in the live allowlist (UPGRADE_NIGHT_LIVE_CLASSES: empty)']) && sh.merges.length === 0 && sh.calls.filter(c => c.cmd === 'gh' && !L.isReadOnlyCommand(c.cmd, c.argv)).every(isPrWrite), "dry-run as git ships it (write token, empty allowlist): refused for the class only, every gh call a read or a story-549 PR write", refs(sh));
    ok(sh.ghWrites.length === 1 && sh.summary.pr_records?.created?.join() === '4662' && sh.summary.github_writes === 1, 'dry-run as git ships it: the tick writes the one record (story 549)', JSON.stringify(sh.summary.pr_records));
    ok(/upgrade-night dryrun: token permissions: checks:read, contents:write, pull_requests:write \(as requested by mint-token, \S+\); can merge/.test(sh.stderr) && !/ghs_|GH_TOKEN=/.test(sh.stderr + sh.stdout), 'the log names exactly checks:read, contents:write, pull_requests:write and never a token');
    ok(sh.summary.change_lock?.outcome === 'taken-and-released' && sh.rec(4662).last_attempt?.verdict === 'refused', 'and the lock is taken and released, the verdict recorded on the record');
    const dr = await tick(baseWorld([4662]), { GH_TOKEN_PERMISSIONS_FILE: permRead });
    ok(ms(dr).lane_refusals?.some(x => /the token cannot merge: minted with checks:read, contents:read, pull_requests:read/.test(x)) && /token permissions: checks:read, contents:read, pull_requests:read/.test(dr.stderr), "dry-run with a read-only token: the verdict and the log name it", refs(dr));

    // live, everything clean: one merge call pinned to the head, no bypass flag; the lock stays held
    const lv = await tick(baseWorld([4662]), LIVE);
    ok(lv.status === 0 && lv.merges.length === 1, 'live, clean: exactly one gh pr merge call', `exit ${lv.status} merges=${lv.merges.length} ${refs(lv)}`);
    ok(JSON.stringify(lv.merges[0]?.argv) === JSON.stringify(['pr', 'merge', '4662', '--repo', 'thaynes43/haynes-ops', '--squash', '--match-head-commit', head]), 'the merge call is a squash merge pinned to the head SHA', JSON.stringify(lv.merges[0]?.argv));
    ok(!lv.merges[0]?.argv.some(a => /^--(admin|auto|force|delete-branch)/.test(a)), 'the merge call carries no admin, auto, force or other flag');
    ok(ms(lv).verdict === 'merged' && ms(lv).merged?.merged === true && lv.summary.would_merge?.reason === 'LIVE: merge step merged', 'live: the verdict is merged, verified by a read', JSON.stringify(ms(lv)).slice(0, 400));
    const cmIdx = lv.calls.findIndex(c => c.cmd === 'kubectl' && c.argv[0] === 'create'), mIdx = lv.calls.findIndex(c => c.cmd === 'gh' && c.argv[1] === 'merge');
    ok(cmIdx >= 0 && cmIdx < mIdx && lv.calls.slice(cmIdx, mIdx).some(c => c.cmd === 'gh' && c.argv[1] === 'view' && c.argv.join(' ').includes('mergeStateStatus')), 'live: the lock is taken before the merge step reads the PR, and both come before the merge');
    ok(lv.lock?.data?.holder && lv.lock.data.phase === 'merged-awaiting-verification' && Date.parse(lv.lock.data.expires_at) - Date.parse(NOW) === 14400e3 && lv.summary.change_lock?.outcome === 'held after merge (merged-awaiting-verification)', 'after a live merge the lock stays held (verification not built): phase merged-awaiting-verification, 4h lease', JSON.stringify(lv.lock?.data));
    ok(lv.summary.owed_work?.built === false && /not built/.test(lv.summary.owed_work?.note || ''), 'the owed-work hook is recorded as not built');

    // live refusals: each one leaves the merge call unmade and the lock released
    const refusedLive = async (name, world, extra, re, lockWritesWanted = 2) => {
      const r = await tick(world, { ...LIVE, ...extra });
      ok(r.status === 0 && r.merges.length === 0 && re.test(refs(r)) && ms(r).verdict === 'refused' && ms(r).merge_call === 'not made (refused)', `live refuses: ${name}`, `exit ${r.status} merges=${r.merges.length} ${ms(r).verdict}: ${refs(r)}`);
      ok(r.lockWrites.length === lockWritesWanted && (lockWritesWanted !== 2 || r.lock?.data?.holder === ''), `live refuses: ${name}: lock ${lockWritesWanted === 2 ? 'taken and released' : lockWritesWanted ? 'take refused, nothing held' : 'never written'}`, `${r.lockWrites.length} lock writes, holder=${r.lock?.data?.holder}`);
      return r;
    };
    const e = await refusedLive('an empty class allowlist', baseWorld([4662]), { UPGRADE_NIGHT_LIVE_CLASSES: '' }, /class delayed-cluster-change is not in the live allowlist \(UPGRADE_NIGHT_LIVE_CLASSES: empty\)/);
    ok(/upgrade-night live: class allowlist: empty, so this tick merges nothing/.test(e.stderr), 'live with an empty allowlist logs that it merges nothing');
    await refusedLive('a token that cannot write', baseWorld([4662]), { GH_TOKEN_PERMISSIONS_FILE: permRead }, /the token cannot merge: minted with checks:read, contents:read, pull_requests:read \(a merge needs contents:write and pull_requests:write\)/);
    await refusedLive('unreadable token permissions', baseWorld([4662]), { GH_TOKEN_PERMISSIONS_FILE: join(tmp, 'absent') }, /unreadable: the token permissions/);
    await refusedLive('a token wider than a merge needs', baseWorld([4662]), { GH_TOKEN_PERMISSIONS_FILE: permWide }, /the token is wider than the lane needs: administration:write/);
    const wp = baseWorld([4662]); wp.mergeViews = { 4662: { statusCheckRollup: [...wp.prs[4662].statusCheckRollup, { __typename: 'StatusContext', context: 'ci/external', state: 'PENDING' }] } };
    await refusedLive('a pending commit status', wp, {}, /commit status ci\/external is pending \(PENDING\)/);
    const wb = baseWorld([4662]); const f0 = fact(4662).files[0]; wb.mainNewest = { [f0]: [{ sha: 'c'.repeat(40), at: '2026-11-01T12:00:00Z' }] }; wb.contains = { ['c'.repeat(40)]: false };
    await refusedLive("a head behind main by a commit that touches the PR's files", wb, {}, new RegExp(`head is behind main: ${f0.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')} changed by cccccccc at 2026-11-01T12:00:00Z, which the head ${head.slice(0, 8)} does not contain`));
    const wc = baseWorld([4662]); wc.mainNewest = { [f0]: [{ sha: 'c'.repeat(40), at: '2026-11-01T12:00:00Z' }] }; wc.contains = { ['c'.repeat(40)]: true };
    const okc = await tick(wc, LIVE);
    ok(okc.merges.length === 1 && !/behind main/.test(refs(okc)), 'a head that contains main\'s newest commit to its files is not behind');
    const wv = baseWorld([4662]); wv.mergeViews = { 4662: { mergeable: 'CONFLICTING', mergeStateStatus: 'DIRTY' } };
    await refusedLive('a conflicting PR', wv, {}, /GitHub reads it CONFLICTING, not MERGEABLE/);
    const wh = baseWorld([4662]); wh.mergeViews = { 4662: { headRefOid: 'd'.repeat(40) } };
    await refusedLive('a head that moved after the tick chose it', wh, {}, /head moved: dddddddd is not the chosen head/);
    const wf = baseWorld([4662]); wf.mergeViews = { 4662: 'FAIL' };
    await refusedLive('an unreadable PR', wf, {}, /unreadable: the pull request \(gh pr view\)/);
    const held = lockOf({ holder: 'upgrade-night/other/1', phase: 'taken', purpose: 'merge step: #1', expires_at: '2026-11-02T07:05:00Z' });
    const wl = baseWorld([4662]); wl.lock = held;
    const rh = await refusedLive('a held lock', wl, {}, /the change lock is held by upgrade-night\/other\/1 \(taken: merge step: #1\) until 2026-11-02T07:05:00Z/, 0);
    ok(rh.lock?.data?.holder === 'upgrade-night/other/1' && rh.summary.change_lock?.outcome === 'not-taken', 'a held lock is left as it was');
    const wlr = baseWorld([4662]); wlr.lockRead = true;
    await refusedLive('an unreadable lock', wlr, {}, /unreadable: ConfigMap upgrade-agent\/upgrade-change-lock \(the read failed\)/, 0);
    const wnx = baseWorld([4662]); wnx.lock = lockOf({ holder: 'someone', expires_at: 'soon' });
    await refusedLive('a held lock with no readable expiry', wnx, {}, /held by someone with no readable expires_at .*fail closed/, 0);
    const wlw = baseWorld([4662]); wlw.lockWrite = true;
    await refusedLive('a lock write the API refuses', wlw, {}, /the change lock was not taken \(create refused: Error from server \(Forbidden\)/, 1);
    const wpz = baseWorld([4662]); wpz.pauseOnReread = 'tom 02:01';
    const rp = await refusedLive('a pause set during the tick', wpz, {}, /paused before the lock take: data\.pause="tom 02:01"/, 0);
    ok(rp.summary.change_lock?.why === 'paused (data.pause); no lock taken', 'a pause set during the tick: no lock take', JSON.stringify(rp.summary.change_lock));
    // a stale lock expires by its lease: taken over, then released
    const wx = baseWorld([4662]); wx.lock = lockOf({ holder: 'upgrade-night/crashed/2', phase: 'taken', expires_at: '2026-11-02T06:50:00Z' });
    const rx = await tick(wx);
    ok(rx.summary.change_lock?.before?.state === 'expired' && rx.summary.change_lock?.took_over_from === 'upgrade-night/crashed/2' && rx.summary.change_lock?.outcome === 'taken-and-released' && JSON.stringify(rx.lockWrites.map(x => x.argv[0])) === JSON.stringify(['replace', 'replace']) && rx.lock?.data?.holder === '', 'a lock whose lease expired is taken over (replace at its resourceVersion) and released', JSON.stringify(rx.summary.change_lock));
    ok(JSON.parse(rx.lockWrites[0].stdin).metadata.resourceVersion === '41', 'the take-over replace carries the resourceVersion it read');
    // a merge call that fails and leaves the PR open: lock released, verdict merge-failed
    const wmf = baseWorld([4662]); wmf.mergeExit = true;
    const rmf = await tick(wmf, LIVE);
    ok(rmf.merges.length === 1 && ms(rmf).verdict === 'merge-failed' && rmf.lock?.data?.holder === '' && /exit 1: GraphQL: merge refused/.test(ms(rmf).merge_call), 'a failed merge call with the PR still open: merge-failed, lock released', `${ms(rmf).verdict} ${ms(rmf).merge_call} holder=${rmf.lock?.data?.holder}`);
    // no candidate: the lock is still exercised; live mode does not run the step on a blocked PR
    const wn = baseWorld([]);
    const rn = await tick(wn);
    ok(rn.summary.change_lock?.outcome === 'taken-and-released' && ms(rn).verdict === 'not-run' && rn.lock?.data?.last_purpose === 'dryrun exercise: no candidate', 'no candidate: the lock is still taken and released, the step not run', JSON.stringify(rn.summary.change_lock));
    const wo = baseWorld([4662]); wo.mainCommits = { [f0]: [{ sha: 'e'.repeat(40), at: '2026-11-06T00:00:00Z' }] };
    const ro = await tick(wo);
    ok(ro.summary.would_merge === null && ms(ro).chosen === false && ms(ro).pr === 4662 && ms(ro).lane_refusals?.[0]?.startsWith('not chosen by the tick') && /checks predate main/.test(refs(ro)), 'dry-run with no PR chosen: the step runs on the nearest lane-A candidate and records why it is refused', refs(ro));
    const rol = await tick(wo, LIVE);
    ok(ms(rol).verdict === 'not-run' && rol.merges.length === 0, 'live with no PR chosen: the merge step does not run', JSON.stringify(ms(rol)));
  }

  // ── the independent review of PR 4827: each guard has a test that fails without it ──
  {
    const permWrite = join(tmp, 'perm-write.json');
    const head = fact(4662).head_sha;
    const LIVE = { UPGRADE_NIGHT_MODE: 'live', UPGRADE_NIGHT_LIVE_CLASSES: 'delayed-cluster-change', GH_TOKEN_PERMISSIONS_FILE: permWrite };
    const ms = r => r.summary.merge_step || {};
    const refs = r => [...(ms(r).pr_refusals || []), ...(ms(r).lane_refusals || [])].join(' | ');
    // finding 1: a pause set after the lock take (here from the second read on) stops the merge
    const w1 = baseWorld([4662]); w1.pauseFromRead = 2; w1.pauseFromReadValue = 'tom 02:03';
    const r1 = await tick(w1, LIVE);
    ok(r1.merges.length === 0 && /paused just before the merge call: data\.pause="tom 02:03"/.test(refs(r1)) && r1.lock?.data?.holder === '' && r1.lockWrites.length === 2, 'finding 1: a pause set after the lock take stops the merge (re-read just before the call); lock released', `merges=${r1.merges.length} ${refs(r1)}`);
    const w1u = baseWorld([4662]); w1u.pauseFailFromRead = 2;
    const r1u = await tick(w1u, LIVE);
    ok(r1u.merges.length === 0 && /unreadable: data\.pause on upgrade-lane-state \(just before the merge call\)/.test(refs(r1u)), 'finding 1: a pause that cannot be read just before the call refuses', refs(r1u));
    // finding 8: the window, through the tick (the tick's own gate)
    const r8 = await tick(baseWorld([4662]), { ...LIVE, FAKE_NOW: '2026-11-02T10:00:00Z' });
    ok(r8.status === 0 && r8.merges.length === 0 && /outside the merge window \(01:30-04:45 America\/New_York, now 05:00\)/.test(r8.rec(4662).gates?.blockers?.join(' ') || '') && ms(r8).verdict === 'not-run', 'finding 8: live, a perfect PR outside the merge window (05:00 New York) is not merged', `${JSON.stringify(r8.rec(4662).gates?.blockers)} ${ms(r8).verdict}`);
    // finding 3: the post-merge lease is written before the call
    const r3 = await tick(baseWorld([4662]), LIVE);
    ok(r3.merges.length === 1 && r3.atMerge?.data?.phase === 'merging' && Date.parse(r3.atMerge.data.expires_at) - Date.parse(NOW) === 14400e3 && r3.atMerge.data.holder === r3.summary.change_lock?.holder, 'finding 3: at the moment of the merge call the lock already holds phase merging with the 4h lease', JSON.stringify(r3.atMerge?.data));
    const w3 = baseWorld([4662]); w3.mergeKilled = true;
    const r3k = await tick(w3, LIVE);
    ok(r3k.merges.length === 1 && ms(r3k).verdict === 'merge-unknown' && /exit null: no exit status \(SIGKILL\)/.test(ms(r3k).merge_call) && r3k.lock?.data?.holder && r3k.lock.data.phase === 'merge-unknown' && Date.parse(r3k.lock.data.expires_at) - Date.parse(NOW) === 14400e3, 'finding 3: a merge call with no exit status (killed) is merge-unknown and keeps the 4h hold, though the PR reads OPEN', `${ms(r3k).verdict} ${ms(r3k).merge_call} ${JSON.stringify(r3k.lock?.data)}`);
    // finding 5: identity, through the tick
    const w5 = baseWorld([4662]); w5.mergeViews = { 4662: { author: { login: 'app/renovate', is_bot: false } } };
    ok(/opened by app\/renovate \(is_bot false\), not the Renovate app/.test(refs(await tick(w5, LIVE))), 'finding 5: a PR author that is not a bot is refused');
    const w5b = baseWorld([4662]); w5b.mergeViews = { 4662: { isCrossRepository: true } };
    ok(/a pull request from another repository/.test(refs(await tick(w5b, LIVE))), 'finding 5: a cross-repository PR is refused');
    const w5c = baseWorld([4662]); w5c.restCommits = { 4662: [{ sha: head, author: { login: 'renovate[bot]', type: 'Bot', id: 29139614 }, committer: { login: 'thaynes43', type: 'User', id: 1 }, commit: { verification: { verified: false, reason: 'unsigned' } } }] };
    const r5c = await tick(w5c, LIVE);
    ok(r5c.merges.length === 0 && new RegExp(`commit ${head.slice(0, 8)} is not the Renovate app's \\(author renovate\\[bot\\]/Bot/29139614, committer thaynes43, signature unsigned\\)`).test(refs(r5c)), 'finding 5: a commit Renovate did not commit and sign through GitHub is refused', refs(r5c));
    // finding 6: live mode refuses a pinned clock
    const r6 = await tick(baseWorld([4662]), LIVE, ['--write-state'], { keepNow: true });
    ok(r6.status === 3 && /REFUSED: UPGRADE_NIGHT_NOW is set \(a test clock\) in live mode/.test(r6.stderr) && r6.calls.length === 0, 'finding 6: live mode with UPGRADE_NIGHT_NOW set exits 3 before any command', `exit ${r6.status} ${r6.calls.length} calls`);
  }

  // ── second round of the independent review (LOW findings) ─────────────────────
  {
    // 1: taking over an expired merging or merge-unknown lock records that phase, and the
    // release keeps it, so a merge left unknown stays visible
    const wt = baseWorld([4662]);
    wt.lock = { apiVersion: 'v1', kind: 'ConfigMap', metadata: { name: 'upgrade-change-lock', namespace: 'upgrade-agent', resourceVersion: '77' }, data: { holder: 'upgrade-night/old/3', phase: 'merge-unknown', expires_at: '2026-11-02T06:00:00Z' } };
    const rt = await tick(wt);
    ok(rt.summary.change_lock?.took_over_phase === 'merge-unknown' && rt.summary.change_lock?.took_over_from === 'upgrade-night/old/3' && JSON.parse(rt.lockWrites[0].stdin).data.took_over_phase === 'merge-unknown', 'round 2, finding 1: the take over an expired merge-unknown lock records took_over_phase, in the lock and in upgrade-lane-state', JSON.stringify(rt.summary.change_lock));
    ok(rt.lock?.data?.phase === 'released' && rt.lock.data.last_took_over_phase === 'merge-unknown' && rt.lock.data.last_took_over_from === 'upgrade-night/old/3' && /took over upgrade-night\/old\/3, phase merge-unknown/.test(rt.stderr), 'round 2, finding 1: after the release the lock still says a merge-unknown lease was taken over, and the log says so', JSON.stringify(rt.lock?.data));
    // 2: UPGRADE_NIGHT_NOW is refused with --write-state (dry-run too); a local read-only run keeps it
    const r2 = await tick(baseWorld([4477]), {}, ['--write-state'], { keepNow: true });
    ok(r2.status === 3 && /REFUSED: UPGRADE_NIGHT_NOW is set \(a test clock\) with --write-state/.test(r2.stderr) && r2.calls.length === 0, 'round 2, finding 2: dry-run with --write-state and UPGRADE_NIGHT_NOW exits 3 before any command', `exit ${r2.status} ${r2.calls.length} calls`);
    const r2l = await tick(baseWorld([4477]), {}, [], { keepNow: true });
    ok(r2l.status === 0 && r2l.writes.length === 0 && r2l.calls.length > 0, 'round 2, finding 2: a local read-only run may still pin UPGRADE_NIGHT_NOW');
  }

  // pause, write failure, a failed read of GitHub, no --write-state
  {
    const w = baseWorld([4477]); w.k8s.configmaps.items.push({ metadata: { name: 'upgrade-lane-state' }, data: { pause: 'tom 2026-11-10' } });
    const r = await tick(w);
    ok(r.status === 0 && /PAUSED/.test(r.stderr) && r.writes.length === 0 && !r.calls.some(c => c.cmd === 'gh'), 'data.pause stops the tick before any GitHub read and writes nothing', `exit ${r.status} writes ${r.writes.length} calls ${r.calls.map(c => c.cmd).join(',')}`);
    const wf = baseWorld([4477]); wf.failApply = true;
    const r2 = await tick(wf);
    ok(r2.status === 2 && /ERROR upgrade-night: STATE WRITE FAILED/.test(r2.stderr), 'a failed state write exits 2 with an ERROR line', `exit ${r2.status}`);
    const wl = baseWorld([4477]); wl.failList = true;
    const r3 = await tick(wl);
    ok(r3.status === 0 && r3.writes.length === 1 && r3.m.metadata?.name === 'upgrade-lane-state' && /failed/i.test(r3.m.data?.error || ''), 'a failed GitHub read is recorded in upgrade-lane-state and exits 0', `exit ${r3.status} error=${r3.m.data?.error}`);
    const wlf = baseWorld([4477]); wlf.failList = true; wlf.failApply = true;
    ok((await tick(wlf)).status === 1, 'a failed tick whose error record cannot be written exits 1');
    const r4 = await tick(baseWorld([4477]), {}, []);
    ok(r4.status === 0 && r4.writes.length === 0 && r4.calls.length > 0, 'without --write-state the tick makes reads only');
    ok(r4.calls.every(c => L.isReadOnlyCommand(c.cmd, c.argv)), 'every call of a local run is a read');
  }

  // ── story 551: the settle gate (facts, not clocks) and F4 through the real tick ─────
  {
    const sx = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/settle-20261110.json'), 'utf8'));
    const settleBlk = (r, n) => (r.rec(n).gates?.blockers || []).filter(b => /^settle gate: /.test(b));
    const C1 = '7'.repeat(40);
    const unapplied = (w, files, subj = "fix(container): another actor's merge (#4800)") => {
      w.mainCompare = { status: 'ahead', ahead_by: 1, behind_by: 0, total_commits: 1, commits: [{ sha: C1, parents: [{ sha: K8S0.sha }], commit: { committer: { date: '2026-11-02T06:58:00Z' }, author: { date: '2026-11-02T06:58:00Z' }, message: subj }, author: { login: 'renovate[bot]' }, committer: { login: 'web-flow' } }] };
      w.commitFiles = { [C1]: { sha: C1, files: files.map(filename => ({ filename })) } };
      return w;
    };
    // inside the window (02:00 New York) with every other gate passing: an unapplied change
    // to #4477's target is the one blocker; one to another app is none
    const r1 = await tick(unapplied(baseWorld([4477]), ['kubernetes/main/apps/media/plex-hw-canary/app/cronjob.yaml']));
    ok(r1.status === 0 && JSON.stringify(settleBlk(r1, 4477)) === JSON.stringify(["settle gate: F1 main has a change Flux has not applied that reaches target kubernetes/main/apps/media/plex-hw-canary, chain media/plex-hw-canary: 77777777 fix(container): another actor's merge (#4800)"]) && blk(r1, 4477) === settleBlk(r1, 4477)[0], "tick: an unapplied change to the candidate's target is its only blocker, named by commit, not by time", blk(r1, 4477));
    ok(r1.summary.would_merge?.pr !== 4477 && r1.rec(4477).gates.settle?.settled === false && r1.summary.settle?.read === true && r1.summary.settle.unapplied_commits?.[0]?.sha === C1, 'tick: so it is not chosen; records[].gates.settle and summary.settle say why', JSON.stringify(r1.summary.settle).slice(0, 300));
    ok(/upgrade-night dryrun: settle gate: Flux at 99999999, 1 commit\(s\) on main not fetched yet; #4477 NOT settled \(F1 main has a change/.test(r1.stderr) && /^settle gate: #4477 NOT settled \(1 blocker\)/m.test(r1.stdout), 'tick: the log and stdout carry the settle verdict per candidate', r1.stderr.split('\n').filter(l => /settle/.test(l)).join(' | '));
    const r2 = await tick(unapplied(baseWorld([4477]), ['kubernetes/main/apps/media/sonarr/app/helmrelease.yaml']));
    ok(settleBlk(r2, 4477).length === 0 && r2.rec(4477).gates.settle?.settled === true && r2.summary.would_merge?.pr === 4477, 'tick: an unapplied change to an unrelated app does not hold #4477', blk(r2, 4477));
    const r3 = await tick(unapplied(baseWorld([4477, 4670]), ['kubernetes/main/apps/network/multus/app/helmrelease.yaml'], 'feat(container): update image ghcr.io/angelnu/cni-plugins to v1.10.0 (#4760)'));
    ok([4477, 4670].every(n => settleBlk(r3, n).some(b => /^settle gate: F1 .*reaches cluster-wide multus: 77777777 feat\(container\): update image ghcr\.io\/angelnu\/cni-plugins/.test(b))), 'tick: an unapplied change to a cluster-wide part (multus) holds every immediate candidate, and names multus', JSON.stringify([settleBlk(r3, 4477), settleBlk(r3, 4670)]));
    const r0 = await tick(baseWorld([4477, 4662]));
    ok(r0.rec(4477).gates.settle?.settled === true && r0.rec(4662).gates.settle?.applicable === false && settleBlk(r0, 4662).length === 0, 'tick: settled facts pass; a delayed-effect candidate (#4662) is not judged (nothing in the cluster changes now)', JSON.stringify([r0.rec(4477).gates.settle, r0.rec(4662).gates.settle]).slice(0, 300));
    ok(!/min ago|minutes|30 min/.test(JSON.stringify(r0.summary.settle) + JSON.stringify(r1.rec(4477).gates.settle)), 'tick: no clock in what the settle gate records (ruling Q-548-04)');
    // the 2026-11-10 shepherd merges through the tick, for #4477 (a target none of them touched):
    // it waits while the multus change is unapplied and while multus rolls, then may merge
    const fxWorld = inst => {
      const w = baseWorld([4477]);
      const c = sx.compare[`${M.revisionSha(inst.flux.gitrepository.status.artifact.revision)}...${inst.main}`];
      w.mainCompare = c ?? 'FAIL';
      w.commitFiles = sx.commits;
      w.k8s['gitrepositories.source.toolkit.fluxcd.io'] = { items: [inst.flux.gitrepository] };
      const rev = inst.flux.gitrepository.status.artifact.revision;
      w.k8s['kustomizations.kustomize.toolkit.fluxcd.io'].items = [...w.k8s['kustomizations.kustomize.toolkit.fluxcd.io'].items.filter(k => !inst.flux.kustomizations.some(x => x.metadata.name === k.metadata.name && x.metadata.namespace === k.metadata.namespace)), ...inst.flux.kustomizations];
      w.k8s['helmreleases.helm.toolkit.fluxcd.io'].items.push(...inst.flux.helmreleases.filter(h => h.metadata.name !== 'plex-hw-canary'));
      void rev;
      return w;
    };
    for (const inst of sx.instants.filter(i => i.id !== 'only-4767')) {
      const r = await tick(fxWorld(inst), { FAKE_NOW: inst.at });
      const sb = settleBlk(r, 4477);
      const want = inst.want.unrelated;
      ok(want === 'settled' ? sb.length === 0 && r.rec(4477).gates.settle?.settled === true : sb.length > 0 && sb.every(b => /multus/.test(b)) && !sb.some(b => /min ago/.test(b)),
        `tick at ${inst.at} (2026-11-10, ${inst.id}): #4477 ${want === 'settled' ? 'is not held' : 'waits, and the blocker names multus'}`, sb.join(' | ') || JSON.stringify(r.rec(4477).gates.settle).slice(0, 300));
    }
    // fail closed through the tick
    const wg = baseWorld([4477]); wg.k8s['gitrepositories.source.toolkit.fluxcd.io'] = 'FAIL';
    ok(settleBlk(await tick(wg), 4477).includes('settle gate: unreadable: GitRepository flux-system/haynes-ops'), 'tick: an unreadable Flux source is a settle blocker');
    const wm = baseWorld([4477]); wm.mainCompare = 'FAIL';
    ok(settleBlk(await tick(wm), 4477).some(b => /^settle gate: F1 unreadable: what main has that Flux has not fetched \(unreadable: compare 99999999\.\.\.main\)$/.test(b)), 'tick: an unreadable compare of the source with main is a settle blocker');
    const wp = baseWorld([4670]); wp.k8s['helmreleases.helm.toolkit.fluxcd.io'].items[0].status = { observedGeneration: 1, conditions: [{ type: 'Ready', status: 'Unknown', reason: 'Progressing' }] };
    ok(settleBlk(await tick(wp), 4670).some(b => /^settle gate: F2 the target is not settled: HelmRelease database\/mosquitto: Ready Unknown \(Progressing\)$/.test(b)), "tick: a HelmRelease of the target's namespace in flight holds it (F2)");
    const wq = baseWorld([4477]); wq.k8s['helmreleases.helm.toolkit.fluxcd.io'].items[0].status = { observedGeneration: 1, conditions: [{ type: 'Ready', status: 'Unknown', reason: 'Progressing' }] };
    ok(settleBlk(await tick(wq), 4477).length === 0, 'tick: the same HelmRelease in another namespace does not hold #4477 (scope, not a cluster-wide hold)');
    // live mode: the settle gate and F4 hold the merge
    const permW = join(tmp, 'perm-write-551.json'); writeFileSync(permW, '{"contents":"write","pull_requests":"write","checks":"read"}');
    const LV = { UPGRADE_NIGHT_MODE: 'live', UPGRADE_NIGHT_LIVE_CLASSES: 'cluster-change', GH_TOKEN_PERMISSIONS_FILE: permW };
    const rl = await tick(unapplied(baseWorld([4477]), ['kubernetes/main/apps/media/plex-hw-canary/app/cronjob.yaml']), LV);
    ok(rl.status === 0 && rl.merges.length === 0 && rl.summary.would_merge === null && settleBlk(rl, 4477).length === 1 && rl.lock?.data?.holder === '', 'live: an unapplied change to the target holds the merge: no gh pr merge call, the lock released', `merges=${rl.merges.length} ${blk(rl, 4477)}`);
    const rc = await tick(baseWorld([4477]), LV);
    ok(rc.merges.length === 1, 'live, settled: the same PR merges (the facts are what held it)', JSON.stringify(rc.summary.merge_step?.lane_refusals));
    const wj = baseWorld([4477]); wj.k8s['jobs@upgrade-agent'] = { items: [{ metadata: { name: 'upgrade-shepherd-29907120', labels: { 'app.kubernetes.io/controller': 'upgrade-shepherd' }, ownerReferences: [{ kind: 'CronJob', name: 'upgrade-shepherd' }] }, status: { active: 1 } }] };
    const rj = await tick(wj, LV);
    ok(rj.merges.length === 0 && (rj.summary.merge_step?.lane_refusals || []).some(x => /^F4 a Job that can merge is running: upgrade-shepherd-29907120 \(the shepherd\)$/.test(x)) && /F4 shepherd Jobs: a Job that can merge is running/.test(rj.stderr), 'live: a running shepherd Job holds the merge (F4), logged', JSON.stringify(rj.summary.merge_step?.lane_refusals));
    const rd = await tick(wj);
    ok(rd.merges.length === 0 && rd.summary.merge_step_shepherd_jobs?.active?.length === 1, 'dry-run: F4 is recorded like the other facts (summary.merge_step_shepherd_jobs)');
    // static: one settle read per tick, judged per candidate
    ok(count(night, /M\.readSettleFacts\(/g) === 1 && count(night, /M\.settleVerdict\(/g) === 1 && /blockers\.push\(\.\.\.sv\.blockers\.map\(b => `settle gate: \$\{b\}`\)\);/.test(night), 'run-night.mjs: one settle read per tick, a verdict per immediate candidate');
  }
  // ── 6. story 549 through the tick: one record per PR, one retry per head ─────────
  {
    const permW = join(tmp, 'perm-549.json'); writeFileSync(permW, '{"contents":"write","pull_requests":"write","checks":"read"}');
    const permR = join(tmp, 'perm-549-read.json'); writeFileSync(permR, '{"contents":"write","pull_requests":"read","checks":"read"}');
    const W = { GH_TOKEN_PERMISSIONS_FILE: permW };
    const BOX = '\n\n---\n\n - [ ] <!-- rebase-check -->If you want to rebase/retry this PR, check this box\n';
    const cfg549 = M.recordConfig(rules);
    const failWorld = (nums, failing) => {
      const w = baseWorld(nums);
      for (const n of failing) { const p = w.prs[n]; p.body += BOX; p.statusCheckRollup = p.statusCheckRollup.map(c => (c.name === 'Flux Local - Success' ? { ...c, conclusion: 'FAILURE' } : c)); }
      return w;
    };
    const gh = () => JSON.parse(readFileSync(ghFile, 'utf8'));
    const ledgerNow = () => (existsSync(ledgerFile) ? JSON.parse(readFileSync(ledgerFile, 'utf8')) : null);
    const own = n => (gh().comments[n] || []).filter(c => c.user.login === 'haynes-ops-bot[bot]' && c.body.startsWith(cfg549.marker));
    const mirrors = (r, n) => { const c = own(n); const pub = c.length === 1 ? M.parseRecord(c[0].body, cfg549) : null; return !!pub && M.recordContentKey(pub) === M.recordContentKey(M.projectRecord(r.rec(n), cfg549)); };
    const pullWrites = (r, n) => r.ghWrites.filter(c => c.argv[3] === `repos/thaynes43/haynes-ops/pulls/${n}`);

    // the steady state: a first tick writes one record per PR, an identical tick writes none
    const calm = baseWorld([4477, 4708]);
    const c1 = await tick(calm, W);
    ok(c1.status === 0 && c1.ghWrites.length === 2 && c1.summary.github_writes === 2 && c1.summary.pr_records?.created?.join() === '4477,4708' && c1.ghWrites.every(isPrWrite), 'records: the first tick creates one record per open Renovate PR (2 writes)', JSON.stringify(c1.summary.pr_records));
    ok(mirrors(c1, 4477) && mirrors(c1, 4708), 'records: each comment carries the record the tick wrote to upgrade-lane-state');
    ok(/upgrade-night dryrun: PR records: 2 written \(created #4477, #4708; updated none; demoted none\), 0 current/.test(c1.stderr) && /upgrade-night dryrun: GitHub writes this tick: 2 \(no merge in dry-run\)/.test(c1.stderr), 'records: the log line counts the writes', c1.stderr.split('\n').filter(l => /PR records|GitHub writes/.test(l)).join(' | '));
    const c2 = await tick(calm, W, ['--write-state'], { keepGh: true });
    ok(c2.ghWrites.length === 0 && c2.summary.github_writes === 0 && c2.summary.pr_records?.current?.join() === '4477,4708' && own(4477).length === 1, 'records: the next identical tick makes no GitHub write (steady state 0), and still one record per PR', JSON.stringify(c2.summary.pr_records));
    ok(mirrors(c2, 4477) && mirrors(c2, 4708), 'records: the unchanged comments still match the state, apart from the time');
    // story 593 (M5): the first tick also wrote the history and its marker, and the PR-write
    // phase still wrote (the marker is its own ConfigMap, not a write to upgrade-lane-state)
    ok(c1.writes.some(w => w.argv[2] === '--field-manager=upgrade-night-history-marker') && c1.summary.history?.marker === 'written' && c1.summary.pr_records?.created?.join() === '4477,4708' && c1.summary.pr_records.why_not === null,
      'story 593 (M5): a tick that writes the history marker still writes the PR records', JSON.stringify({ marker: c1.summary.history?.marker, created: c1.summary.pr_records?.created, why: c1.summary.pr_records?.why_not }));
    // story 593 (L2, L3): #4477 left the open list, merged by Renovate: after the open records
    // (#4708, current), one edit carries terminal on #4477's record, and the history marks it
    const gone = baseWorld([4708]);
    const g7 = toGh(fact(4477));
    gone.mergedList = [{ number: 4477, title: g7.title, author: { login: 'app/renovate' }, mergedAt: '2026-11-02T06:58:00Z', mergedBy: { login: 'app/renovate' }, labels: g7.labels, body: g7.body, files: g7.files, mergeCommit: { oid: 'c'.repeat(40) }, headRefOid: fact(4477).head_sha }];
    const c3 = await tick(gone, W, ['--write-state'], { keepGh: true });
    const rec4477 = own(4477).length === 1 ? M.parseRecord(own(4477)[0].body, cfg549) : null;
    const hist = () => JSON.parse(readFileSync(historyFile, 'utf8'));
    const mergedEv = hist().data.events.trim().split('\n').map(JSON.parse).find(e => e.type === 'merged' && e.carrier?.pr === 4477);
    ok(c3.ghWrites.length === 1 && /issues\/comments\/\d+$/.test(c3.ghWrites[0].argv[3]) && rec4477?.terminal?.type === 'merged' && rec4477.terminal.merge_commit === 'c'.repeat(40) && c3.summary.pr_records?.current?.join() === '4708' && c3.summary.history?.mirrors?.updated?.join() === '4477',
      'story 593 (L2, L3): after the open records, one edit puts terminal (merged) on #4477\'s record', JSON.stringify({ writes: c3.ghWrites.map(w => w.argv[3]), mirrors: c3.summary.history?.mirrors, terminal: rec4477?.terminal }));
    ok(Object.values(JSON.parse(hist().data.items)).some(d => d.mirrored && d.pr === 4477) && c3.summary.history.mirrors.marked?.written === true, 'story 593 (L2): the history marks the terminal mirrored', JSON.stringify(c3.summary.history.mirrors.marked));
    const c4 = await tick(gone, W, ['--write-state'], { keepGh: true });
    ok(c4.ghWrites.length === 0 && !c4.summary.history?.mirrors, 'story 593 (L2): the next tick owes nothing and reads no comment for it', JSON.stringify(c4.summary.history?.mirrors));
    // L3: a denial on a terminal mirror halts only the mirrors; the open records are not halted
    const gone2 = baseWorld([4708]);
    gone2.mergedList = [{ ...gone.mergedList[0], number: 4708, title: toGh(fact(4708)).title, body: toGh(fact(4708)).body, labels: toGh(fact(4708)).labels, files: toGh(fact(4708)).files }];
    gone2.list = gone2.list.filter(x => x.number !== 4708);
    const c5 = await tick({ ...gone2, ghWriteFail: true }, W, ['--write-state'], { keepGh: true });
    ok(c5.summary.history?.mirrors?.halt && /a denial is a stop/.test(c5.summary.history.mirrors.halt.why) && /until a person removes meta\.mirror_halt in ConfigMap upgrade-ledger/.test(c5.summary.history.mirrors.halt.why) && !ledgerNow()?.halt && /ERROR upgrade-night dryrun: TERMINAL MIRRORS HALTED/.test(c5.stderr) && JSON.parse(hist().data.meta).mirror_halt,
      'story 593 (L3): a denied terminal edit halts the mirrors (meta.mirror_halt), not the open-PR records (no retry_ledger.halt)', JSON.stringify({ mirrors: c5.summary.history?.mirrors, ledgerHalt: ledgerNow()?.halt }));
    const c6 = await tick(baseWorld([4477]), W, ['--write-state'], { keepGh: true });
    ok(c6.summary.pr_records?.why_not === null && /halted/.test(c6.summary.history?.mirrors?.why_not || ''), 'story 593 (L3): on the next tick the open-PR records still write; the mirrors wait for a person', JSON.stringify({ pr: c6.summary.pr_records?.why_not, mirrors: c6.summary.history?.mirrors }));

    // two stalled PRs: the per-tick cap ticks one; the record shows it; the ledger first
    const fw = failWorld([4477, 4670, 4708], [4477, 4670]);
    const t1 = await tick(fw, W);
    ok(t1.rec(4477).lane === 'BLOCKED' && t1.rec(4477).class === 'check-failing' && t1.rec(4670).class === 'check-failing', 'retry: the stalled PRs read BLOCKED check-failing', `${t1.rec(4477).class} ${t1.rec(4670).class}`);
    ok(JSON.stringify(t1.summary.retry?.eligible) === '[4477,4670]' && JSON.stringify(t1.summary.retry?.tried) === '[4477]' && t1.summary.retry.attempts[0]?.outcome === 'ticked', 'retry: two eligible, the per-tick cap (1 box write) ticks #4477', JSON.stringify(t1.summary.retry));
    ok(t1.rec(4477).retry?.state === 'ticked' && t1.rec(4670).retry?.state === 'deferred' && /per-tick cap \(1 box write\) is used this tick/.test(t1.rec(4670).retry.why), 'retry: the records say ticked and deferred');
    const pw = pullWrites(t1, 4477);
    ok(pw.length === 1 && pullWrites(t1, 4670).length === 0 && JSON.parse(pw[0].stdin).body === M.tickRetryBox(fw.prs[4477].body).body, 'retry: one PR body edit, the box ticked and nothing else changed');
    const ledgerApplies = t1.calls.filter(c => c.cmd === 'kubectl' && c.argv.includes('--field-manager=upgrade-night-ledger'));
    const ledOf = c => JSON.parse(JSON.parse(c.stdin).data.retry_ledger);
    const iIntent = t1.calls.findIndex(c => c.cmd === 'kubectl' && c.argv.includes('--field-manager=upgrade-night-ledger') && ledOf(c).retries?.[4477]?.[0]?.status === 'intent');
    ok(iIntent >= 0 && iIntent < t1.calls.indexOf(pw[0]), 'retry: the ledger entry (intent) is written before the box is ticked');
    // F6/F8: before any GitHub write the ledger is proved writable, at the version the tick read
    const iFirstGh = t1.calls.findIndex(c => c.cmd === 'gh' && c.argv[1] === '--method');
    ok(t1.calls.indexOf(ledgerApplies[0]) < iFirstGh && JSON.parse(ledgerApplies[0].stdin).metadata.resourceVersion === undefined && ledgerApplies.slice(1).every(c => /^\d+$/.test(JSON.parse(c.stdin).metadata.resourceVersion || '')), 'retry (F6/F8): the ledger is written before any GitHub write, and every later ledger write carries the version it last saw', JSON.stringify(ledgerApplies.map(c => JSON.parse(c.stdin).metadata.resourceVersion)));
    ok(ledgerNow()?.retries?.[4477]?.[0]?.status === 'ticked' && ledgerNow().retries[4477][0].head === fact(4477).head_sha, 'retry: the ledger ends with the tick on that head (data.retry_ledger, its own field manager)');
    ok(t1.ghWrites.length === 4 && t1.summary.github_writes === 4 && t1.ghWrites.every(isPrWrite) && t1.merges.length === 0, 'first night: 3 records and 1 retry, 4 GitHub writes, no merge', JSON.stringify(t1.ghWrites.map(c => c.argv.slice(2, 4))));
    ok(own(4477).length === 1 && M.parseRecord(own(4477)[0].body, cfg549).retry.state === 'ticked' && M.parseRecord(own(4477)[0].body, cfg549).retry.history[0]?.status === 'ticked', 'retry: the retry count shows in the PR record');
    const t2 = await tick(fw, W, ['--write-state'], { keepGh: true });
    ok(t2.rec(4477).retry?.state === 'waiting-for-renovate' && pullWrites(t2, 4477).length === 0 && pullWrites(t2, 4670).length === 1 && t2.rec(4670).retry?.state === 'ticked', 'retry: next tick, #4477 waits for Renovate (no second tick on its head) and #4670 gets the cap', JSON.stringify(t2.summary.retry?.attempts));
    ok(t2.summary.pr_records?.updated?.join() === '4477,4670' && t2.summary.pr_records.current.join() === '4708' && t2.ghWrites.length === 3, 'retry: the two changed records are edited in place; the third is current', JSON.stringify(t2.summary.pr_records));
    // Renovate acted: a new head on #4477, and the check fails again
    const g = gh(); delete g.pulls[4477]; writeFileSync(ghFile, JSON.stringify(g));
    const fw3 = structuredClone(fw); const NEWH = 'b'.repeat(40);
    fw3.prs[4477].headRefOid = NEWH; fw3.prs[4477].commits = fw3.prs[4477].commits.map((c, i, a) => (i === a.length - 1 ? { ...c, oid: NEWH } : c));
    const t3 = await tick(fw3, W, ['--write-state'], { keepGh: true });
    ok(t3.rec(4477).lane === 'BLOCKED' && t3.rec(4477).class === 'check-failing-after-retry' && /^failed again on bbbbbbbb after one retry on [0-9a-f]{8} at /.test(t3.rec(4477).reason) && t3.rec(4477).retry?.state === 'failed-again', 'retry: a second failure on the new head blocks the PR with its reason', `${t3.rec(4477).class}: ${t3.rec(4477).reason}`);
    ok(pullWrites(t3, 4477).length === 0 && ledgerNow().retries[4477][0].outcome === 'failed-again' && ledgerNow().retries[4477][0].next_head === NEWH, 'retry: no retry on the new head; the ledger records the outcome');
    ok(M.parseRecord(own(4477)[0].body, cfg549).class === 'check-failing-after-retry' && own(4477).length === 1, 'retry: the record on the PR shows the block');
    const t4 = await tick(fw3, W, ['--write-state'], { keepGh: true });
    ok(t4.rec(4477).class === 'check-failing-after-retry' && pullWrites(t4, 4477).length === 0, 'retry: it stays blocked on later ticks');
    // resolved: the head after a retry passes
    const okw = baseWorld([4477]); okw.ledger = JSON.stringify({ schema: M.LEDGER_SCHEMA, retries: { 4477: [{ head: 'c'.repeat(40), at: '2026-11-02T06:30:00.000Z', status: 'ticked', checks: ['x'] }] }, halt: null });
    const t5 = await tick(okw, W);
    ok(t5.rec(4477).retry?.state === 'resolved' && ledgerNow()?.retries?.[4477]?.[0]?.outcome === 'resolved' && t5.rec(4477).lane === 'LANE-A', 'retry: the head after a retry passed: resolved, recorded in the ledger', JSON.stringify(t5.rec(4477).retry));
    // a refused write: reported with its reason, stops the rest, the state still lands
    const t6 = await tick({ ...failWorld([4477, 4708], [4477]), ghWriteFail: true }, W);
    ok(t6.status === 0 && t6.summary.retry.attempts[0]?.outcome === 'write-failed' && /HTTP 403|Resource not accessible/.test(t6.summary.retry.attempts[0].why) && JSON.stringify(t6.summary.pr_records?.not_attempted) === '[4477,4708]' && !(t6.summary.pr_records.current || []).length, 'refused write: the retry write is reported with its reason, the records are not attempted, none is called current', JSON.stringify({ r: t6.summary.retry.attempts, p: t6.summary.pr_records }));
    ok(t6.ghWrites.length === 1 && ledgerNow()?.retries?.[4477]?.[0]?.status === 'write-failed' && t6.m.data?.summary, 'refused write: one write tried, counted in the ledger, the tick state written');
    ok(/a denial is a stop/.test(ledgerNow()?.halt?.why || '') && /retry write on #4477 failed: gh api --method PATCH repos\/thaynes43\/haynes-ops\/pulls\/4477: gh: Resource not accessible by integration \(HTTP 403\)/.test(t6.stderr) && !JSON.stringify(t6.summary).includes('Resource not accessible'), 'refused write (F3/F4): a 403 halts in the ledger; the raw line is in the Job log, not in the state', JSON.stringify(ledgerNow()?.halt));
    const t6b = await tick(failWorld([4477, 4708], [4477]), W, ['--write-state'], { keepGh: true });
    ok(t6b.ghWrites.length === 0 && /^halted: the retry box write on #4477 was refused by GitHub \(HTTP 403\)/.test(t6b.summary.pr_records?.why_not || ''), 'refused write (F4): the next tick makes no GitHub write (a denial is a stop)', t6b.summary.pr_records?.why_not);
    const t6c = await tick({ ...baseWorld([4477, 4708]), ghWriteFail: true, ghWriteFailLine: 'gh: Bad Gateway (HTTP 502)' }, W);
    ok(t6c.ghWrites.length === 1 && !ledgerNow()?.halt && /GitHub server error \(HTTP 502\)/.test(t6c.summary.pr_records?.failed?.[0]?.why || ''), 'refused write (F4): a 5xx stops this tick only, no halt', JSON.stringify(t6c.summary.pr_records?.failed));
    const t7 = await tick({ ...baseWorld([4477, 4708]), ghWriteFail: true }, W);
    ok(t7.summary.pr_records?.failed?.[0]?.pr === 4477 && /refused by GitHub \(HTTP 403\)/.test(t7.summary.pr_records.failed[0].why) && JSON.stringify(t7.summary.pr_records.not_attempted) === '[4708]' && t7.ghWrites.length === 1 && /FAILED #4477/.test(t7.stderr) && /record write failed: #4477 .*Resource not accessible by integration/.test(t7.stderr), 'refused write: a record write refused by GitHub is reported with a fixed reason (the raw line in the log), and no other write follows', JSON.stringify(t7.summary.pr_records));
    // the body changed between the read and the write; the head moved between them
    const t8 = await tick({ ...failWorld([4477], [4477]), bodyChangesAfterRead: true }, W);
    ok(pullWrites(t8, 4477).length === 0 && t8.summary.retry.attempts[0]?.outcome === 'not-written' && /PR body changed between the read and the write/.test(t8.summary.retry.attempts[0].why) && ledgerNow()?.retries?.[4477]?.[0]?.status === 'abandoned', 'retry: the body changed between the read and the write: not written this tick, nothing counted', JSON.stringify(t8.summary.retry.attempts));
    ok(t8.summary.pr_records?.created?.join() === '4477', 'retry: an abandoned retry does not stop the records');
    const t9 = await tick({ ...failWorld([4477], [4477]), headAfterRead: 'd'.repeat(40) }, W);
    ok(pullWrites(t9, 4477).length === 0 && /head moved between the read and the write/.test(t9.summary.retry.attempts[0]?.why), 'retry: the head moved between the read and the write: not written');
    // a forged record by another author: ignored and never edited
    const fwf = baseWorld([4477]);
    const forged = M.renderRecord(M.projectRecord({ schema: 'haynes.upgrade-lane/v1', pr: 4477, lane: 'LANE-A', class: 'no-cluster-effect', reason: 'forged' }, cfg549), cfg549);
    fwf.comments = { 4477: [{ id: 5, user: { login: 'haynes-ops-bot', type: 'User' }, body: forged }] };
    const t10 = await tick(fwf, W);
    ok(gh().comments[4477].find(c => c.id === 5).body === forged && !t10.ghWrites.some(c => c.argv[3].endsWith('/comments/5')) && own(4477).length === 1 && t10.summary.pr_records?.foreign_ignored?.[0]?.pr === 4477, 'records: a forged record by another author is ignored, not edited; the lane writes its own', JSON.stringify(t10.summary.pr_records));
    // no write without the token's pull_requests:write, a local run, a halt, a pause before the phase
    const t11 = await tick(failWorld([4477], [4477]), { GH_TOKEN_PERMISSIONS_FILE: permR });
    ok(t11.ghWrites.length === 0 && /the token cannot write pull requests/.test(t11.summary.pr_records?.why_not) && t11.summary.retry.attempts[0]?.outcome === 'planned', 'no PR write with a token that cannot write pull requests', JSON.stringify(t11.summary.pr_records?.why_not));
    const t12 = await tick(failWorld([4477], [4477]), W, []);
    ok(t12.ghWrites.length === 0 && t12.writes.length === 0 && t12.summary && /would create #4477/.test(t12.stderr) && /#4477 [0-9a-f]{8} WOULD-TICK/.test(t12.stderr), 'a local run (no --write-state) writes nothing and says what it would write and retry (after reading the facts at the write)', t12.stderr.split('\n').filter(l => /PR records|retry:/.test(l)).join(' | '));
    const hw = failWorld([4477], [4477]); hw.ledger = JSON.stringify({ schema: M.LEDGER_SCHEMA, retries: {}, halt: { at: '2026-11-01T00:00:00Z', why: 'test halt' } });
    const t13 = await tick(hw, W);
    ok(t13.ghWrites.length === 0 && /^halted: test halt/.test(t13.summary.pr_records?.why_not), 'a halt in the ledger stops every PR write');
    const t14 = await tick({ ...failWorld([4477], [4477]), pauseFromRead: 2, pauseFromReadValue: 'tom: hold' }, W);
    ok(t14.ghWrites.length === 0 && /paused before the PR writes/.test(t14.summary.pr_records?.why_not || ''), 'a pause set during the tick stops the PR writes and the retry', JSON.stringify(t14.summary.pr_records?.why_not));
    // the token wrote as another identity: halt after one comment, and the next tick writes nothing
    const t15 = await tick({ ...baseWorld([4477, 4708]), botLogin: 'haynes-dev-bot[bot]' }, W);
    ok(t15.ghWrites.length === 1 && /written as haynes-dev-bot\[bot\]/.test(t15.summary.pr_records?.halt?.why) && /written as haynes-dev-bot/.test(ledgerNow()?.halt?.why || ''), 'identity mismatch: one comment, then a durable halt in the ledger', JSON.stringify(t15.summary.pr_records?.halt));
    const t16 = await tick({ ...baseWorld([4477, 4708]), botLogin: 'haynes-dev-bot[bot]' }, W, ['--write-state'], { keepGh: true });
    ok(t16.ghWrites.length === 0 && /^halted:/.test(t16.summary.pr_records?.why_not), 'identity mismatch: the next tick writes nothing (no comment per tick)');
    // F1: a PR the facts at the write refuse (a non-required check stuck QUEUED) hands the slot on
    const stuck = failWorld([4477, 4670, 4708], [4477, 4670]);
    stuck.prs[4477].statusCheckRollup.push({ __typename: 'CheckRun', workflowName: 'Other', name: 'lint', status: 'QUEUED', conclusion: null, startedAt: null, completedAt: null });
    const f1 = await tick(stuck, W);
    ok(JSON.stringify(f1.summary.retry?.tried) === '[4477,4670]' && f1.summary.retry.attempts[0]?.outcome === 'not-written' && /queued or running on the head: Other \/ lint/.test(f1.summary.retry.attempts[0].why) && f1.summary.retry.attempts[1]?.outcome === 'ticked' && pullWrites(f1, 4477).length === 0 && pullWrites(f1, 4670).length === 1, 'retry (F1): the stuck PR first is refused at the write, and the next eligible PR gets the tick', JSON.stringify(f1.summary.retry?.attempts));
    ok(f1.rec(4477).retry?.state === 'not-written' && !(ledgerNow()?.retries?.[4477]) && f1.summary.github_writes === 4, 'retry (F1): the refused PR leaves no ledger entry; 3 records and 1 box this tick');
    const rulesOne = join(tmp, 'rules-one-preflight.json');
    writeFileSync(rulesOne, JSON.stringify({ ...rules, night: { ...rules.night, retry: { ...rules.night.retry, max_preflights_per_tick: 1 } } }));
    const f1b = await tick(stuck, { ...W, UPGRADE_RULES: rulesOne });
    ok(JSON.stringify(f1b.summary.retry?.tried) === '[4477]' && f1b.rec(4670).retry?.state === 'deferred' && /the 1 write-time reads allowed per tick are used/.test(f1b.rec(4670).retry.why) && f1b.ghWrites.filter(c => /pulls\/\d+$/.test(c.argv[3])).length === 0, 'retry (F1): the write-time reads are bounded per tick (max_preflights_per_tick)', JSON.stringify(f1b.summary.retry));
    // F2: a workflow run for the head not started yet (its check suite queued, no check run)
    const f2w = failWorld([4477, 4670, 4708], [4477, 4670]);
    f2w.suites = { [fact(4477).head_sha]: { total_count: 2, check_suites: [{ id: 41, app: { slug: 'github-actions' }, status: 'queued' }, { id: 42, app: { slug: 'claude' }, status: 'queued' }] } };
    const f2 = await tick(f2w, W);
    ok(/a workflow run for the head is queued \(check suite 41\)/.test(f2.summary.retry?.attempts?.[0]?.why || '') && pullWrites(f2, 4477).length === 0 && pullWrites(f2, 4670).length === 1, 'retry (F2): a workflow run queued for the head stops its retry; the next PR gets the tick', JSON.stringify(f2.summary.retry?.attempts));
    ok(f2.calls.some(c => c.cmd === 'gh' && c.argv[1] === `repos/thaynes43/haynes-ops/commits/${fact(4477).head_sha}/check-suites?per_page=100`), 'retry (F2): the check suites are read by head SHA with gh api GET (checks:read)');
    const f2u = failWorld([4477], [4477]); f2u.suites = { [fact(4477).head_sha]: 'FAIL' };
    const f2b = await tick(f2u, W);
    ok(/unreadable: the check suites of the head/.test(f2b.summary.retry?.attempts?.[0]?.why || '') && pullWrites(f2b, 4477).length === 0, 'retry (F2): unreadable check suites refuse the write');
    // F5: the pause is read again just before each record write
    const f5 = await tick({ ...baseWorld([4477, 4708]), pauseFromRead: 4, pauseFromReadValue: 'tom: hold' }, W);
    ok(f5.summary.pr_records?.created?.join() === '4477' && JSON.stringify(f5.summary.pr_records?.not_attempted) === '[4708]' && /paused just before a record write/.test(f5.summary.pr_records?.stopped_by || '') && f5.ghWrites.length === 1, 'records (F5): a pause set after the first record write stops the next one', JSON.stringify(f5.summary.pr_records));
    // F6: an intent left by a Job that ended between its ledger writes
    const intentLedger = (head, at = '2026-11-02T06:45:00.000Z') => JSON.stringify({ schema: M.LEDGER_SCHEMA, retries: { 4477: [{ head, at, status: 'intent', checks: ['x'] }] }, halt: null });
    const f6w = failWorld([4477], [4477]); f6w.ledger = intentLedger(fact(4477).head_sha);
    const f6 = await tick(f6w, W);
    ok(f6.rec(4477).retry?.state === 'ticked' && pullWrites(f6, 4477).length === 1 && ledgerNow().retries[4477][0].status === 'abandoned' && ledgerNow().retries[4477][1].status === 'ticked', 'interrupted (F6): an intent with the box unticked at the same head was not made: it is abandoned and the PR ticked now', JSON.stringify(ledgerNow()?.retries));
    const f6t = failWorld([4477], [4477]); f6t.ledger = intentLedger(fact(4477).head_sha); f6t.prs[4477].body = M.tickRetryBox(f6t.prs[4477].body).body;
    const f6b = await tick(f6t, W);
    ok(f6b.rec(4477).retry?.state === 'waiting-for-renovate' && pullWrites(f6b, 4477).length === 0 && ledgerNow().retries[4477][0].status === 'ticked', 'interrupted (F6): an intent with the box ticked was made: counted, waiting for Renovate, no second tick', JSON.stringify(f6b.rec(4477).retry));
    const f6m = structuredClone(f6w); f6m.ledger = intentLedger('e'.repeat(40));
    const f6c = await tick(f6m, W);
    ok(f6c.rec(4477).class === 'check-failing-after-retry' && pullWrites(f6c, 4477).length === 0, 'interrupted (F6): an intent followed by a new head that fails again blocks the PR', `${f6c.rec(4477).class}`);
    const f6d = await tick({ ...failWorld([4477, 4708], [4477]), failLedger: true }, W);
    ok(f6d.ghWrites.length === 0 && /the retry ledger could not be written/.test(f6d.summary.pr_records?.why_not || ''), 'interrupted (F6): a ledger that cannot be written means no GitHub write this tick', f6d.summary.pr_records?.why_not);
    // F8: an overlapping Job wrote the state after this tick read it: this tick makes no tick
    const f8w = failWorld([4477, 4708], [4477]); f8w.ledger = JSON.stringify({ schema: M.LEDGER_SCHEMA, retries: {}, halt: null }); f8w.stateRv = 12; f8w.stateRvOnRead = '11';
    const f8 = await tick(f8w, W);
    ok(f8.ghWrites.length === 0 && pullWrites(f8, 4477).length === 0 && /could not be written at the version this tick read/.test(f8.summary.pr_records?.why_not || '') && /Conflict/.test(f8.stderr), 'overlap (F8): the second writer\'s ledger write fails on the resourceVersion, so it makes no tick and no record', f8.summary.pr_records?.why_not);
    const f8ok = await tick({ ...f8w, stateRvOnRead: '12' }, W);
    ok(pullWrites(f8ok, 4477).length === 1, 'overlap (F8): the same tick at the current version ticks (the conflict test is not vacuous)');
    // the state of every record the tick wrote carries retry, which the mapping covers
    ok(t1.recs.every(r => r.retry && typeof r.retry.state === 'string'), 'every record in upgrade-lane-state carries retry');
  }
} finally {
  server.close();
  rmSync(tmp, { recursive: true, force: true });
}

console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
