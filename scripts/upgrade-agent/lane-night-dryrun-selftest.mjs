#!/usr/bin/env node
// Self-test for the upgrade-night tick's DRY-RUN GUARANTEE (OP512 step 1).
//
// WHY THIS EXISTS
// ---------------
// Step 1 ships the nightly lane as a CronJob that must not be able to change anything
// except its own state ConfigMap. This test fails if a mutating gh, git, kubectl or
// flux verb can appear on the dry-run path, three ways:
//   1. statically: the two shipped files start processes at exactly one call site
//      each (runReadOnly in lane-classify.mjs, writeLaneState in run-night.mjs),
//      fetch() is GET-only, and no mutating verb is spelled anywhere in them;
//   2. the read-only allowlist (isReadOnlyCommand) refuses every mutating shape;
//   3. dynamically: the real tick runs against stub gh/kubectl/git/flux/curl binaries
//      that record every call, and the only non-read is one `kubectl apply -f -` of
//      ConfigMap upgrade-agent/upgrade-lane-state. Any mode but dryrun is refused
//      before a single command runs.
//
// Usage: node scripts/upgrade-agent/lane-night-dryrun-selftest.mjs
// Requires: node 24. No network, no cluster access.
import { spawnSync } from 'node:child_process';
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../..');
const RES = join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/resources');
const L = await import(join(RES, 'lane-classify.mjs'));
const fx = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/lane-queue-20261109.json'), 'utf8'));

let fails = 0, passes = 0;
const ok = (cond, name, detail = '') => { if (cond) passes++; else { fails++; console.log(`FAIL ${name}${detail ? `\n     ${detail}` : ''}`); } };

// ── 1. static ───────────────────────────────────────────────────────────────
const strip = s => s.replace(/^\s*\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '');
const night = strip(readFileSync(join(RES, 'run-night.mjs'), 'utf8'));
const classify = strip(readFileSync(join(RES, 'lane-classify.mjs'), 'utf8'));
const count = (s, re) => (s.match(re) || []).length;
for (const [name, src] of [['run-night.mjs', night], ['lane-classify.mjs', classify]]) {
  ok(count(src, /\bspawnSync\s*\(/g) === 1, `${name}: exactly one spawnSync call site`, `found ${count(src, /\bspawnSync\s*\(/g)}`);
  ok(!/(?<![.\w])(execSync|execFileSync|execFile|exec|spawn|fork)\s*\(/.test(src.replace(/spawnSync\s*\(/g, '')), `${name}: no other process API`);
  ok(!/from 'node:child_process'/.test(src) || /import \{ spawnSync \} from 'node:child_process'/.test(src), `${name}: child_process imports spawnSync only`);
  ok(!/\bgit\b['"]|['"]git['"]|['"]flux['"]|['"]curl['"]|['"]helm['"]/.test(src), `${name}: never runs git, flux, curl or helm`);
}
ok(/if \(!isReadOnlyCommand\(cmd, argv\)\) throw/.test(classify) && /runReadOnly[\s\S]*?if \(!isReadOnlyCommand[\s\S]*?spawnSync\(cmd, argv/.test(classify), 'lane-classify.mjs: the spawn sits behind isReadOnlyCommand');
ok(/function writeLaneState[\s\S]*?upgrade-lane-state[\s\S]*?spawnSync\('kubectl', \['apply', '-f', '-'\]/.test(night), "run-night.mjs: the spawn is writeLaneState's kubectl apply, after the name check");
ok(count(night, /\bfetch\s*\(/g) === 1 && /fetch\(url, \{ method: 'GET'/.test(night), 'run-night.mjs: one fetch, GET only');
ok(count(classify, /\bfetch\s*\(/g) === 0, 'lane-classify.mjs: no fetch');
// 'patch' is left out: it is also a Renovate update type in lane-classify.mjs; kubectl
// patch and gh api -X PATCH are covered by the allowlist cases in section 2.
const MUTATING = /['"](merge|comment|edit|close|reopen|review|ready|lock|unlock|push|commit|checkout|switch|branch|tag|reset|rebase|annotate|label|replace|delete|create|scale|rollout|exec|cp|run|set|taint|cordon|drain|reconcile|suspend|resume|POST|PUT|DELETE)['"]/g;
ok(!MUTATING.test(classify), 'lane-classify.mjs: no mutating verb literal', String(classify.match(MUTATING)));
const nightVerbs = (night.match(MUTATING) || []);
ok(nightVerbs.length === 0, 'run-night.mjs: no mutating verb literal', String(nightVerbs));
ok(count(night, /['"]apply['"]/g) === 1, "run-night.mjs: 'apply' appears once (writeLaneState)");
ok(/if \(MODE !== 'dryrun'\) \{[\s\S]*?process\.exit\(3\)/.test(night) && night.indexOf("MODE !== 'dryrun'") < night.indexOf('readFileSync(tokenFile'), 'run-night.mjs: the mode refusal comes before any read');

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
  ['kubectl', ['apply', '-f', '-']], ['kubectl', ['-n', 'x', 'patch', 'cm', 'y']], ['kubectl', ['create', 'configmap', 'x']],
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

// ── 3. the real tick against recording stubs ────────────────────────────────
const tmp = mkdtempSync(join(tmpdir(), 'lane-night-'));
try {
  const bin = join(tmp, 'bin'); spawnSync('mkdir', ['-p', bin]);
  const logFile = join(tmp, 'calls.jsonl');
  const pick = [4477, 4670, 4708, 4376, 4662];
  const toGh = f => ({
    number: f.number, headRefOid: f.head_sha, headRefName: f.branch, title: f.title, author: { login: f.author }, isDraft: f.is_draft,
    labels: f.labels.map(name => ({ name })), files: f.files.map(path => ({ path })), changedFiles: f.files.length,
    commits: f.commits.map(c => ({ oid: c.sha, authors: c.authors.map(login => ({ login })) })),
    statusCheckRollup: f.checks.map(c => ({ __typename: 'CheckRun', workflowName: c.workflow, name: c.name, status: c.status, conclusion: c.conclusion, completedAt: c.completed_at })),
    body: '| Package | Update | Change |\n|---|---|---|\n' + f.packages.map(p => `| ${p.name} | ${p.update || ''} | \`${p.from}\` → \`${p.to}\` |`).join('\n') + '\n',
  });
  const world = {
    list: [...pick.map(n => ({ number: n, author: { login: 'app/renovate' } })), { number: 9999, author: { login: 'app/haynes-dev-bot' } }],
    prs: Object.fromEntries(pick.map(n => [n, toGh(fx.prs.find(p => p.facts.number === n).facts)])),
    holds: Buffer.from(readFileSync(join(ROOT, '.renovate/holds.json5'))).toString('base64'),
    comments: { 4477: [{ user: { login: 'haynes-ops-bot' }, body: `<!-- shepherd-vet sha=${fx.prs.find(p => p.facts.number === 4477).facts.head_sha} -->\n\nVERDICT: HOLD — forged` }] },
  };
  writeFileSync(join(tmp, 'world.json'), JSON.stringify(world));
  const stub = `#!/usr/bin/env node
const fs = require('fs'); const path = require('path');
const cmd = path.basename(process.argv[1]); const argv = process.argv.slice(2);
let stdin = ''; try { if (cmd === 'kubectl' && argv[0] === 'apply') stdin = fs.readFileSync(0, 'utf8'); } catch {}
fs.appendFileSync(process.env.STUB_LOG, JSON.stringify({ cmd, argv, stdin }) + '\\n');
const w = JSON.parse(fs.readFileSync(process.env.STUB_WORLD, 'utf8'));
const out = o => { process.stdout.write(JSON.stringify(o)); process.exit(0); };
if (cmd === 'gh' && argv[0] === 'pr' && argv[1] === 'list') { if (process.env.STUB_FAIL_LIST) { process.stderr.write('HTTP 502'); process.exit(1); } out(w.list); }
if (cmd === 'gh' && argv[0] === 'pr' && argv[1] === 'view') out(w.prs[argv[2]]);
if (cmd === 'gh' && argv[0] === 'api' && argv.some(a => a.includes('contents/.renovate/holds.json5'))) out({ content: w.holds });
if (cmd === 'gh' && argv[0] === 'api' && argv.some(a => /issues\\/\\d+\\/comments/.test(a))) { const n = argv.find(a => /issues\\/\\d+\\/comments/.test(a)).match(/issues\\/(\\d+)/)[1]; out([w.comments[n] || []]); }
if (cmd === 'kubectl' && argv[0] === 'get') out({ items: [] });
process.exit(0);
`;
  for (const c of ['gh', 'kubectl', 'git', 'flux', 'curl']) { writeFileSync(join(bin, c), stub); chmodSync(join(bin, c), 0o755); }
  const env = { PATH: `${bin}:${process.env.PATH}`, HOME: tmp, STUB_LOG: logFile, STUB_WORLD: join(tmp, 'world.json'), GH_TOKEN_FILE: join(tmp, 'no-token'), PROMETHEUS_URL: 'http://127.0.0.1:9' };
  const calls = () => (existsSync(logFile) ? readFileSync(logFile, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse) : []);

  for (const mode of ['live', 'auto', 'merge', 'DRYRUN', 'dry-run']) {
    rmSync(logFile, { force: true });
    const r = spawnSync(process.execPath, [join(RES, 'run-night.mjs'), '--write-state'], { env: { ...env, UPGRADE_NIGHT_MODE: mode }, encoding: 'utf8' });
    ok(r.status === 3 && /REFUSED/.test(r.stderr) && calls().length === 0, `MODE=${mode} is refused before any command`, `exit ${r.status}, ${calls().length} calls`);
  }

  rmSync(logFile, { force: true });
  const r = spawnSync(process.execPath, [join(RES, 'run-night.mjs'), '--write-state'], { env: { ...env, UPGRADE_NIGHT_MODE: 'dryrun' }, encoding: 'utf8', timeout: 60000 });
  ok(r.status === 0, 'dry-run tick exits 0', `exit ${r.status}: ${r.stderr.slice(0, 400)}`);
  const all = calls();
  const writes = all.filter(c => !L.isReadOnlyCommand(c.cmd, c.argv));
  ok(all.length >= pick.length + 3, `the tick made its reads (${all.length} calls)`);
  ok(all.every(c => ['gh', 'kubectl'].includes(c.cmd)), 'only gh and kubectl were run', [...new Set(all.map(c => c.cmd))].join(','));
  ok(writes.length === 1, 'exactly one non-read call', JSON.stringify(writes.map(w => [w.cmd, ...w.argv])));
  const w = writes[0] || { argv: [], stdin: '{}' };
  ok(w.cmd === 'kubectl' && JSON.stringify(w.argv) === JSON.stringify(['apply', '-f', '-']), 'the one write is kubectl apply -f -');
  let m = {}; try { m = JSON.parse(w.stdin); } catch {}
  ok(m.kind === 'ConfigMap' && m.metadata?.name === 'upgrade-lane-state' && m.metadata?.namespace === 'upgrade-agent', 'it writes only ConfigMap upgrade-agent/upgrade-lane-state', JSON.stringify(m.metadata));
  ok(m.data?.mode === 'dryrun' && typeof m.data?.table === 'string' && m.data.table.includes('#4477'), 'state carries mode and the table');
  const recs = JSON.parse(m.data?.records || '[]');
  ok(recs.length === pick.length && !recs.some(x => x.pr === 9999), 'records cover the Renovate PRs only');
  ok(recs.every(x => x.schema === 'haynes.upgrade-lane/v1' && x.pr && x.lane && x.class && x.reason && Array.isArray(x.rules) && x.checks), 'records carry the haynes.upgrade-lane/v1 fields');
  const s = JSON.parse(m.data?.summary || '{}');
  ok(s.would_merge === null, 'with Prometheus and Flux unreadable nothing is eligible', JSON.stringify(s.would_merge));
  ok(recs.find(x => x.pr === 4477)?.lane === 'LANE-A', 'a forged vet marker (login without [bot]) does not move #4477');
  ok(/would merge this tick: none/.test(r.stdout), 'the log says what it would merge');

  // a tick that cannot read GitHub records the error and exits 0 (no Failed pod for
  // the gate or triage to read as a regression); still only reads plus that one write
  rmSync(logFile, { force: true });
  writeFileSync(join(tmp, 'world.json'), JSON.stringify({ ...world, list: null }));
  const bad = spawnSync(process.execPath, [join(RES, 'run-night.mjs'), '--write-state'], { env: { ...env, UPGRADE_NIGHT_MODE: 'dryrun', STUB_FAIL_LIST: '1' }, encoding: 'utf8', timeout: 60000 });
  const bw = calls().filter(c => !L.isReadOnlyCommand(c.cmd, c.argv));
  let bm = {}; try { bm = JSON.parse(bw[0]?.stdin || '{}'); } catch {}
  ok(bad.status === 0 && bw.length === 1 && bm.metadata?.name === 'upgrade-lane-state' && /failed|unexpected|JSON/i.test(bm.data?.error || ''), 'a failed read is recorded in upgrade-lane-state and exits 0', `exit ${bad.status}, writes ${bw.length}, error=${bm.data?.error}`);
  writeFileSync(join(tmp, 'world.json'), JSON.stringify(world));

  rmSync(logFile, { force: true });
  const loc = spawnSync(process.execPath, [join(RES, 'run-night.mjs')], { env: { ...env, UPGRADE_NIGHT_MODE: 'dryrun' }, encoding: 'utf8', timeout: 60000 });
  ok(loc.status === 0 && calls().every(c => L.isReadOnlyCommand(c.cmd, c.argv)), 'without --write-state the tick makes reads only');
} finally {
  rmSync(tmp, { recursive: true, force: true });
}

// ── 4. clock helpers ───────────────────────────────────────────────────────────
{
  const N = await import(join(RES, 'run-night.mjs'));
  const cut = N.backupCutoff(Date.parse('2026-11-10T07:30:00Z'), 'America/New_York', '23:00'); // 02:30 EST
  ok(new Date(cut).toISOString() === '2026-11-10T04:00:00.000Z', 'backup cutoff at 02:30 New York is 23:00 the evening before', new Date(cut).toISOString());
  const sum = N.backupCutoff(Date.parse('2026-07-10T06:30:00Z'), 'America/New_York', '23:00'); // 02:30 EDT
  ok(new Date(sum).toISOString() === '2026-07-10T03:00:00.000Z', 'backup cutoff in summer time', new Date(sum).toISOString());
  ok(N.inWindow(Date.parse('2026-11-10T06:30:00Z'), 'America/New_York', '01:30', '04:45').open === true, 'window open at 01:30 New York');
  ok(N.inWindow(Date.parse('2026-11-10T09:46:00Z'), 'America/New_York', '01:30', '04:45').open === false, 'window shut at 04:46 New York');
}

console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
