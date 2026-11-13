#!/usr/bin/env node
// Self-test for the read-only verifier of the night lane (story 553): night-verify.mjs.
//
// WHY THIS EXISTS
// ---------------
// After a merge the lane must prove the rollout healthy from facts it reads on its own ticks,
// never pass on a fact it could not read, never charge a failure to its update while another
// change reached the same target, and, while it only practises, never write the history.
// This test pins, against fakes (no cluster, no network), on the real facts of the five
// Renovate merges of 2026-11-13 (fixtures/verify-20261113.json, captured read-only):
//   1. statically: no process API, one GET fetch, every read through the runner it is given;
//      git ships practice on and history events off; the scripts ConfigMap carries the file;
//      run-night.mjs runs it once, before the history, and hands only its events on;
//   2. the real night: two ticks verify #4869, #4870, #4873 and #4874; #4871 has no cluster
//      effect; one tick is not yet (stability needs a second tick);
//   3. each failure mode as a failed verdict and, on the lane's own merge, a revert decision
//      (verification-failed): not applied, not Ready, wrong image, restart growth, a new
//      critical alert, plus a crash loop and an image-pull failure;
//   4. not yet, cannot tell (an unreadable fact never passes), the story-755 read named;
//   5. other changes: #4874 fails with #4873 on its chain (cannot tell which change did it,
//      escalated); #4869 fails with only unrelated changes (its own regression); a synthetic
//      later commit on the same app;
//   6. an own image awaiting its digest pin: awaiting-pin, the build failure, the pin timeout
//      on facts (pushes after the build), and verified through the pin;
//   7. practice never reaches the history; the lane's own events only with the setting on,
//      and the history refuses a verifier event on a merge the lane did not make;
//   8. the practice bound (installed at the first commit of night-verify.mjs on main), the
//      memory (done items, the cap, the byte budget), the per-tick cap and the fair order;
//   9. the image and version readers.
//
// Usage: node scripts/upgrade-agent/lane-verify-selftest.mjs
// Requires: node 24. No network, no cluster access.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../..');
const RES = join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/resources');
const L = await import(join(RES, 'lane-classify.mjs'));
const H = await import(join(RES, 'upgrade-history.mjs'));
const V = await import(join(RES, 'night-verify.mjs'));
const N = await import(join(RES, 'run-night.mjs'));
const rules = L.loadRules(join(RES, 'upgrade-rules.json'));
const fx = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/verify-20261113.json'), 'utf8'));
const REPO = 'thaynes43/haynes-ops';

let fails = 0, passes = 0;
const ok = (cond, name, detail = '') => { if (cond) passes++; else { fails++; console.log(`FAIL ${name}${detail ? `\n     ${detail}` : ''}`); } };
const iso = t => new Date(t).toISOString();
const T1 = Date.parse('2026-11-13T13:00:00Z'), T2 = T1 + 15 * 60e3, T3 = T2 + 15 * 60e3;
const PRS = [4869, 4870, 4871, 4873, 4874];
const sha = n => fx.prs.find(p => p.number === n).mergeCommit.oid;

// ── the fake world: GitHub's reads and kubectl get, served from the fixture ───────────────
const commitsOf = w => [{ sha: fx.oldest_commit.sha, at: fx.prs.find(p => p.mergeCommit.oid === fx.oldest_commit.sha).mergedAt, subject: 'oldest', pr: null, author: 'renovate[bot]', files: fx.oldest_commit.files }, ...fx.range.commits, ...(w.extraCommits || [])];
function serve(w, cmd, argv) {
  const a = argv.join(' ');
  if (cmd === 'kubectl') {
    const ns = argv[argv.indexOf('-n') + 1];
    if (/kustomizations/.test(a)) return w.fail?.ks ? null : w.ks;
    if (/helmreleases/.test(a)) return w.fail?.hr ? null : w.hr;
    if (/gitrepositories/.test(a)) return { items: [w.source] };
    if (/ocirepositories|helmcharts/.test(a)) return { items: w.charts || [] };
    if (/ get pods /.test(` ${a} `)) return w.fail?.pods ? null : (w.pods[ns]?.pods || { items: [] });
    if (/ get cronjobs /.test(` ${a} `)) return w.pods[ns]?.cronjobs || { items: [] };
    if (/ get jobs /.test(` ${a} `)) return { items: [] };
    if (/configmaps/.test(a)) return { items: w.ledger ? [w.ledger] : [] };
    return null;
  }
  if (cmd !== 'gh') return null;
  const list = commitsOf(w), idx = s => list.findIndex(c => c.sha === s);
  let m;
  if ((m = /compare\/([0-9a-f]{40})\.\.\.main\?/.exec(a))) {
    if (w.fail?.range) return null;
    const after = list.slice(idx(m[1]) + 1);
    return { status: after.length ? 'ahead' : 'identical', ahead_by: after.length, behind_by: 0, total_commits: after.length,
      commits: after.map(c => ({ sha: c.sha, parents: [{ sha: '0'.repeat(40) }], commit: { committer: { date: c.at }, message: `${c.subject}${c.pr ? ` (#${c.pr})` : ''}` }, author: { login: c.author }, committer: { login: 'web-flow' } })) };
  }
  if ((m = /compare\/([0-9a-f]{40})\.\.\.([0-9a-f]{40})/.exec(a))) { const i = idx(m[1]), j = idx(m[2]); return i < 0 || j < 0 ? null : { status: j === i ? 'identical' : j > i ? 'ahead' : 'behind' }; }
  if ((m = /repos\/[^/]+\/[^/]+\/commits\/([0-9a-f]{40})$/.exec(argv.at(-1)))) { const c = list.find(x => x.sha === m[1]); return c ? { sha: c.sha, files: c.files.map(filename => ({ filename })) } : null; }
  if (/commits\?sha=main&path=/.test(a)) return w.installed === undefined ? null : [w.installed.map(x => ({ sha: x.sha, commit: { committer: { date: x.at } } }))];
  if ((m = /actions\/runs\?head_sha=([0-9a-f]{40})/.exec(a))) return w.runs?.[m[1]] === undefined ? null : { workflow_runs: w.runs[m[1]] };
  return null;
}
function fake(w) {
  const calls = [];
  const run = (cmd, argv, o = {}) => {
    calls.push(`${cmd} ${argv.join(' ')}`);
    if (!L.isReadOnlyCommand(cmd, argv)) throw new Error(`a command that is not a read: ${cmd} ${argv.slice(0, 3).join(' ')}`);
    const out = serve(w, cmd, argv);
    if (out === null) { if (o.allowFail) return null; throw new Error('read failed'); }
    return JSON.stringify(out);
  };
  return { run, calls };
}
const world = () => ({ ks: structuredClone(fx.ks), hr: structuredClone(fx.hr), source: structuredClone(fx.source), pods: structuredClone(fx.pods), alerts: structuredClone(fx.alerts) });
const views = list => list.map(n => fx.prs.find(p => p.number === n));
const vrules = over => ({ ...rules, night: { ...rules.night, verify: { ...rules.night.verify, ...over } } });
async function tick(w, { prs = PRS, now = T1, prev = null, own = false, cfg = {}, doc = null, onlyPrs = prs, deps = {} } = {}) {
  const d = doc || V.docOf(views(prs), { repo: REPO, rules, at: iso(now), actor: own ? { kind: 'lane', login: null } : null });
  const f = fake(w);
  const r = await V.verifyPhase({ rules: vrules(cfg), run: f.run, repo: REPO, now, startedAt: now, clock: () => now, prevText: prev, onlyPrs,
    deps: { doc: d, alertSubject: N.alertSubject, getJson: async () => (w.alertsUnreadable ? null : w.alerts), ...deps } });
  return { ...r, calls: f.calls, doc: d, rec: n => Object.values(r.records || {}).find(x => x.pr === n) };
}
const factOf = (rec, name) => (rec?.facts || []).find(f => f.fact === name) || {};
const twice = async (w, o = {}, w2 = w) => { const a = await tick(w, o); const b = await tick(w2, { ...o, now: T2, prev: a.memory }); return { a, b }; };
const ksOf = (w, id) => w.ks.items.find(k => L.ksId(k) === id);
const hrOf = (w, id) => w.hr.items.find(h => `${h.metadata.namespace}/${h.metadata.name}` === id);
const podsIn = (w, ns, prefix) => w.pods[ns].pods.items.filter(p => p.metadata.name.startsWith(prefix));
const planWith = (doc, verifyEvents, at) => H.planHistory({ doc: { ...doc, meta: { ...doc.meta, backfill: { at, window: { since: at, until: at }, query: 'q', events: 0, compare: null } } }, records: [], openPrs: [], verdict: null, at, repo: REPO, cfg: H.historyConfig(rules), rules,
  scan: { ok: true, list: [], reverts: [], github_count: 0, window: { since: at, until: at }, query: 'q', repo: REPO }, backfilling: false, renovateAuthors: rules.renovate_pr_authors, verifyEvents });
const stateOf = (doc, id) => H.replay(doc).items.get(id)?.item.state ?? null;

// ── 1. static ───────────────────────────────────────────────────────────────
{
  const strip = s => s.replace(/^\s*\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '');
  const src = strip(readFileSync(join(RES, 'night-verify.mjs'), 'utf8'));
  ok(!/child_process/.test(src) && !/(?<![.\w])(spawn|spawnSync|exec|execSync|execFile|execFileSync|fork)\s*\(/.test(src), 'static: night-verify.mjs starts no process (every read goes through the runner it is handed)');
  ok((src.match(/\bfetch\s*\(/g) || []).length === 1 && /fetch\(url, \{ method: 'GET'/.test(src), 'static: one fetch, GET only (Prometheus /api/v1/alerts)');
  ok((src.match(/\bwriteFileSync\s*\(/g) || []).length === 1 && src.indexOf('writeFileSync(o.state') > src.indexOf('async function main('), 'static: the one file write is the CLI\'s --state, a local file');
  ok(!/applyHistory|writeLaneState|ghWrite|callMerge|takeLock|releaseLock|writePrRecords|runRetry/.test(src), 'static: it calls no writer of the lane (history, state, PR records, lock, merge)');
  // 'rollout' is left out: it is the name of a fact here, and no process can run it
  const MUTATING = /['"](merge|comment|edit|close|reopen|review|push|commit|annotate|label|replace|delete|create|scale|exec|patch|apply|POST|PUT|PATCH|DELETE)['"]/g;
  ok(!(src.match(MUTATING) || []).length, 'static: no mutating verb literal', String(src.match(MUTATING)));
  const cfg = V.verifyConfig(rules);
  ok(cfg.ok, 'config: night.verify in upgrade-rules.json is valid', cfg.why);
  ok(cfg.practice === true && cfg.write_history_events === false, 'config: git ships practice on and the lane\'s history events off');
  ok(cfg.time_budget_seconds < 600 && cfg.max_items_per_tick <= 50 && cfg.max_range_commits <= 250, 'config: per tick the items, the commit range and the time are bounded, under the Job\'s 600s');
  const kz = readFileSync(join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/kustomization.yaml'), 'utf8');
  ok(/\n\s+- resources\/night-verify\.mjs\n/.test(kz), 'the scripts ConfigMap (upgrade-shepherd-scripts) carries night-verify.mjs');
  const night = strip(readFileSync(join(RES, 'run-night.mjs'), 'utf8'));
  ok((night.match(/V\.verifyPhase\(/g) || []).length === 1 && night.indexOf('V.verifyPhase(') > night.indexOf('M.laneMergeStep(') && night.indexOf('V.verifyPhase(') < night.indexOf('H.historyPhase('), 'run-night.mjs: one verify phase, after the merge step and before the history');
  ok(/verifyEvents: ver\.events \|\| \[\]/.test(night) && !/ver\.records/.test(night), 'run-night.mjs: hands the history only the phase\'s events (never practice records)');
  ok(/await import\('\.\/night-verify\.mjs'\)/.test(night) && /catch \(e\) \{ ver = \{ summary: \{ error:/.test(night), 'run-night.mjs: loads the verifier inside a guarded step; a failure is recorded, not thrown');
  const hist = strip(readFileSync(join(RES, 'upgrade-history.mjs'), 'utf8'));
  ok(/s\.item\.attempts\.at\(-1\)\?\.actor\?\.kind !== 'lane'/.test(hist), 'upgrade-history.mjs: refuses a verifier event unless the lane made the item\'s current attempt');
}

// ── 2. the real night of 2026-11-13 ──────────────────────────────────────────────
const real = await twice(world());
{
  const { a, b } = real;
  for (const n of [4869, 4870, 4873, 4874]) {
    ok(a.rec(n)?.verdict === 'not-yet' && factOf(a.rec(n), 'stable').result === 'waiting', `real #${n}: the first tick is not yet (the rollout recorded; stability needs a later tick)`, JSON.stringify(a.rec(n)?.why));
    ok(b.rec(n)?.verdict === 'verified', `real #${n}: verified on the second tick`, b.rec(n)?.why);
    ok(['source', 'applied', 'helmreleases', 'pods', 'alerts', 'image', 'rollout', 'stable'].every(x => ['pass', 'not-applicable'].includes(factOf(b.rec(n), x).result)), `real #${n}: every fact has its own passing result`);
    ok(b.rec(n)?.practice === true && (b.rec(n).would_write || []).map(e => `${e.type}:${e.legal}`).join() === 'verified:true', `real #${n}: practice; would write verified (legal on someone else's merge), not written`);
  }
  ok(b.rec(4871)?.verdict === 'verified' && factOf(b.rec(4871), 'source').result === 'not-applicable' && a.rec(4871)?.verdict === 'verified', 'real #4871 (a GitHub Action): no cluster effect, nothing to verify');
  ok(/HelmRelease network\/traefik-external runs chart traefik 42\.4\.0/.test(factOf(b.rec(4873), 'image').detail), 'real #4873: the chart update is read from the HelmRelease\'s last release (traefik 42.4.0)');
  ok(/4 container\(s\).*cloudflared 2026\.11\.0/.test(factOf(b.rec(4874), 'image').detail), 'real #4874: four containers run cloudflared 2026.11.0');
  ok(/rhasspy\/wyoming-piper 2\.5\.3/.test(factOf(b.rec(4870), 'image').detail), 'real #4870: piper runs 2.5.3 (a tag pinned with a digest)');
  const oc = b.rec(4874).other_changes;
  ok(oc.complete && oc.changes.length === 1 && oc.changes[0].pr === 4873 && /chain network\/traefik-external/.test(oc.changes[0].parts.join()), 'real #4874: one other change, #4873 on its chain (traefik-external); the pass stands', JSON.stringify(oc));
  ok(b.rec(4869).other_changes.complete && b.rec(4869).other_changes.changes.length === 0, 'real #4869: no other change reached seerr, its chain or a cluster-wide part');
  ok(b.summary.events_for_history === 0 && b.events.length === 0 && b.summary.practice_events_not_written === 5, 'real: no event for the history; five practice events not written');
  ok(a.calls.every(c => /^(gh (api|pr view)|kubectl get) /.test(c)), 'real: every command was a read (gh api GET, kubectl get)');
  ok(b.summary.reads.commands <= 30, `real: ${b.summary.reads.commands} reads for five merges`);
  ok(/#4874 .* VERIFIED/.test(b.table) && /other changes: [0-9a-f]{8} #4873/.test(b.table) && /would write \(not written: practice\): verified merged_unverified->done/.test(b.table), 'real: the morning view names each verdict, fact and other change');
}

// ── 3. the failure modes (practice: failed; the lane's own merge: a revert decision) ───────
async function failure(name, n, mutate, cls, { secondTick = false, mutateSecond = null } = {}) {
  const w = world(); mutate?.(w);
  let r;
  if (secondTick) { const w2 = structuredClone(w); mutateSecond(w2); r = (await twice(w, { prs: [n] }, w2)).b; }
  else r = await tick(w, { prs: [n] });
  const rec = r.rec(n);
  ok(rec?.verdict === 'failed' && rec.failing?.some(f => f.class === cls), `${name}: #${n} fails with ${cls}`, rec?.why);
  // the same facts on the lane's own merge, with the history events on: a revert decision
  const w3 = world(); mutate?.(w3);
  let o;
  if (secondTick) { const w4 = structuredClone(w3); mutateSecond(w4); const a = await tick(w3, { prs: [n], own: true, cfg: { write_history_events: true } }); o = await tick(w4, { prs: [n], own: true, cfg: { write_history_events: true }, now: T2, prev: a.memory, doc: a.doc }); }
  else o = await tick(w3, { prs: [n], own: true, cfg: { write_history_events: true } });
  const evs = o.events;
  const p = planWith(o.doc, evs, iso(secondTick ? T2 : T1));
  ok(evs.length === 1 && evs[0].type === 'verification-failed' && evs[0].class === cls && p.ok && p.events.length === 1 && stateOf(p.doc, evs[0].item_id) === 'failed',
    `${name}: on the lane's own merge it writes verification-failed (${cls}), and the item is failed (a revert is owed)`, JSON.stringify(evs.map(e => [e.type, e.class])) + ' ' + JSON.stringify(p.notes?.unrecorded));
  return rec;
}
const piperKs = w => ksOf(w, 'ai/piper');
await failure('not applied', 4870, w => { const k = piperKs(w); k.status.lastAppliedRevision = `main@sha1:${sha(4869)}`; k.status.lastAttemptedRevision = w.source.status.artifact.revision; k.status.conditions = [{ type: 'Ready', status: 'False', reason: 'BuildFailed' }]; }, 'not-applied');
await failure('not Ready (HelmRelease)', 4870, w => { hrOf(w, 'ai/piper').status.conditions = [{ type: 'Ready', status: 'False', reason: 'UpgradeFailed' }, { type: 'Stalled', status: 'True', reason: 'RetriesExceeded' }]; }, 'not-ready');
await failure('not Ready (Kustomization health)', 4869, w => { ksOf(w, 'media/seerr').status.conditions = [{ type: 'Ready', status: 'False', reason: 'HealthCheckFailed' }]; }, 'not-ready');
await failure('wrong image', 4869, w => { for (const p of podsIn(w, 'media', 'seerr-')) p.spec.containers[0].image = 'ghcr.io/seerr-team/seerr:v3.5.1'; }, 'wrong-image');
await failure('restart growth', 4870, null, 'restart-growth', { secondTick: true, mutateSecond: w => { podsIn(w, 'ai', 'piper-')[0].status.containerStatuses[0].restartCount += 1; } });
await failure('not Ready on the later tick', 4870, null, 'not-ready', { secondTick: true, mutateSecond: w => { podsIn(w, 'ai', 'piper-')[0].status.conditions = [{ type: 'Ready', status: 'False' }]; } });
const seerrPod = fx.pods.media.pods.items[0].metadata.name;
await failure('a new critical alert', 4869, w => { w.alerts.data.alerts.push({ labels: { alertname: 'SeerrDown', severity: 'critical', namespace: 'media', pod: seerrPod }, state: 'firing', activeAt: '2026-11-13T04:00:00Z' }); }, 'critical-alert');
await failure('a crash loop', 4870, w => { const c = podsIn(w, 'ai', 'piper-')[0].status.containerStatuses[0]; c.state = { waiting: { reason: 'CrashLoopBackOff' } }; c.restartCount = 4; }, 'crash-loop');
await failure('an image-pull failure', 4869, w => { podsIn(w, 'media', 'seerr-')[0].status.containerStatuses[0].state = { waiting: { reason: 'ImagePullBackOff' } }; }, 'image-pull');
{
  // an alert that fired before the merge is not new; one in another app's namespace is not the target's
  const w = world();
  w.alerts.data.alerts.push({ labels: { alertname: 'SeerrDown', severity: 'critical', namespace: 'media', pod: seerrPod }, state: 'firing', activeAt: '2026-11-13T02:00:00Z' });
  w.alerts.data.alerts.push({ labels: { alertname: 'PiperDown', severity: 'critical', namespace: 'ai', pod: fx.pods.ai.pods.items[0].metadata.name }, state: 'firing', activeAt: '2026-11-13T04:00:00Z' });
  const r = (await twice(w, { prs: [4869] })).b;
  ok(r.rec(4869).verdict === 'verified' && /firing since before the merge: SeerrDown/.test(factOf(r.rec(4869), 'alerts').detail), 'alerts: one that fired before the merge is listed, not a failure; another app\'s is not the target\'s', r.rec(4869).why);
  const w2 = world();
  w2.alerts.data.alerts.push({ labels: { alertname: 'BlackboxProbeFailed', severity: 'critical', namespace: 'media' }, state: 'firing', activeAt: '2026-11-13T04:00:00Z' });
  const r2 = await tick(w2, { prs: [4869] });
  ok(r2.rec(4869).verdict === 'cannot-tell' && factOf(r2.rec(4869), 'alerts').result === 'unreadable', 'alerts: a new alert in the target namespace that names no workload: cannot tell (never a pass)', r2.rec(4869).why);
  // a crash loop in a pod made before the merge is not the update's
  const w3 = world();
  podsIn(w3, 'ai', 'kokoro-')[0].status.containerStatuses[0].state = { waiting: { reason: 'CrashLoopBackOff' } };
  podsIn(w3, 'ai', 'kokoro-')[0].metadata.creationTimestamp = '2026-11-01T00:00:00Z';
  const r3 = await tick(w3, { prs: [4870] });
  ok(factOf(r3.rec(4870), 'pods').result === 'pass' && /made before the merge/.test(factOf(r3.rec(4870), 'pods').detail), 'pods: a crash loop in a pod made before the merge is listed, not charged to the update');
}

// ── 4. not yet and cannot tell ────────────────────────────────────────────────────
{
  const w = world(); w.source.status.artifact.revision = `main@sha1:${sha(4869)}`;
  const r = await tick(w, { prs: [4870] });
  ok(r.rec(4870).verdict === 'not-yet' && /does not contain dee1a27b yet/.test(factOf(r.rec(4870), 'source').detail) && factOf(r.rec(4870), 'image').result === 'waiting', 'not yet: Flux\'s source does not contain the merge; the later facts wait', r.rec(4870).why);
  const w2 = world(); for (const p of podsIn(w2, 'media', 'seerr-')) { const old = structuredClone(p); old.metadata.uid += '-old'; old.metadata.name += '-old'; old.metadata.labels['pod-template-hash'] = '0ldhash00'; old.metadata.ownerReferences = [{ kind: 'ReplicaSet', name: 'seerr-0ldhash00' }]; old.spec.containers[0].image = 'ghcr.io/seerr-team/seerr:v3.5.1'; w2.pods.media.pods.items.push(old); }
  const r2 = await tick(w2, { prs: [4869] });
  ok(r2.rec(4869).verdict === 'not-yet' && factOf(r2.rec(4869), 'rollout').result === 'waiting' && factOf(r2.rec(4869), 'image').result === 'waiting', 'not yet: two templates side by side (rolling); the old image is not a failure while it rolls', r2.rec(4869).why);
  const wt = world(); { const p = structuredClone(podsIn(wt, 'media', 'seerr-')[0]); p.metadata.uid += '-t'; p.metadata.name += '-t'; p.metadata.deletionTimestamp = '2026-11-13T12:59:00Z'; wt.pods.media.pods.items.push(p); }
  const rt = await tick(wt, { prs: [4869] });
  ok(rt.rec(4869).verdict === 'not-yet' && /1 pod\(s\) terminating/.test(factOf(rt.rec(4869), 'rollout').detail), 'not yet: a pod still terminating', rt.rec(4869).why);
  const w3 = world(); hrOf(w3, 'media/seerr').status.conditions = [{ type: 'Ready', status: 'Unknown', reason: 'Progressing' }];
  ok((await tick(w3, { prs: [4869] })).rec(4869).verdict === 'not-yet', 'not yet: a HelmRelease still progressing');
  const w4 = world(); w4.alertsUnreadable = true;
  const r4 = (await twice(w4, { prs: [4874] })).b;
  ok(r4.rec(4874).verdict === 'cannot-tell' && factOf(r4.rec(4874), 'alerts').result === 'unreadable' && factOf(r4.rec(4874), 'stable').result === 'pass', 'cannot tell: Prometheus unreadable; every other fact passes, and still no pass', r4.rec(4874).why);
  const w5 = world(); w5.pods.media.pods.items = [];
  const r5 = await tick(w5, { prs: [4869] });
  ok(r5.rec(4869).verdict === 'cannot-tell' && /scaled to zero, or not created yet.*story 755/.test(factOf(r5.rec(4869), 'rollout').detail), 'cannot tell: a Deployment with no pod; the missing read (apps, story 755) is named', factOf(r5.rec(4869), 'rollout').detail);
  const w6 = world(); w6.fail = { pods: true };
  ok((await tick(w6, { prs: [4869] })).rec(4869).verdict === 'cannot-tell', 'cannot tell: pods unreadable');
  const w7 = world(); delete ksOf(w7, 'media/seerr').status.inventory;
  ok(/the inventory of Kustomization media\/seerr/.test(factOf((await tick(w7, { prs: [4869] })).rec(4869), 'workloads').detail), 'cannot tell: a Kustomization with no inventory');
  const w8 = world(); w8.fail = { ks: true };
  ok((await tick(w8, { prs: [4869] })).rec(4869).verdict === 'cannot-tell', 'cannot tell: Kustomizations unreadable');
  const w9 = world(); ksOf(w9, 'media/seerr').spec.suspend = true;
  ok((await tick(w9, { prs: [4869] })).rec(4869).verdict === 'cannot-tell', 'cannot tell: the target\'s Kustomization is suspended');
  const w10 = world(); ksOf(w10, 'media/seerr').status.conditions = [{ type: 'Ready', status: 'False', reason: 'DependencyNotReady' }];
  ok((await tick(w10, { prs: [4869] })).rec(4869).verdict === 'not-yet', 'not yet: DependencyNotReady behind a settled dependency is progressing (the settle facts)');
  const dep = ksOf(w10, `${ksOf(w10, 'media/seerr').spec.dependsOn[0].namespace || 'media'}/${ksOf(w10, 'media/seerr').spec.dependsOn[0].name}`);
  dep.status.conditions = [{ type: 'Ready', status: 'False', reason: 'HealthCheckFailed' }, { type: 'Stalled', status: 'True', reason: 'x' }];
  const r10 = await tick(w10, { prs: [4869] });
  ok(r10.rec(4869).verdict === 'cannot-tell' && /which is failing/.test(factOf(r10.rec(4869), 'applied').detail), 'cannot tell: waiting on a failing dependency that is not the update\'s (never charged to it)', r10.rec(4869).why);
  const w11 = world(); w11.fail = { range: true };
  const r11 = (await twice(w11, { prs: [4874] })).b;
  ok(r11.rec(4874).verdict === 'verified' && r11.rec(4874).other_changes.complete === false, 'a pass stands with the other changes unreadable (they are listed as unreadable)');
  // a pass needs every fact: a verdict with one unreadable and the rest pass is never verified
  ok(V.verdictOf([{ fact: 'a', result: 'pass' }, { fact: 'b', result: 'unreadable', detail: 'x' }], { complete: true, changes: [] }).verdict === 'cannot-tell'
    && V.verdictOf([{ fact: 'a', result: 'pass' }, { fact: 'b', result: 'bogus' }], { complete: true, changes: [] }).verdict === 'cannot-tell'
    && V.verdictOf([{ fact: 'a', result: 'waiting' }, { fact: 'b', result: 'unreadable' }], {}).verdict === 'cannot-tell'
    && V.verdictOf([{ fact: 'a', result: 'fail', class: 'not-ready' }, { fact: 'b', result: 'unreadable' }], { complete: true, changes: [] }).verdict === 'failed', 'verdictOf: fail > unreadable > waiting > pass; an unknown result is cannot tell');
}

// ── 5. other changes: cannot tell which change did it, or the update's own ───────────────
{
  const bad = w => { for (const p of podsIn(w, 'network', 'cloudflare-tunnel-')) p.spec.containers[0].image = 'mirror.gcr.io/cloudflare/cloudflared:2026.10.7'; };
  const w = world(); bad(w);
  const r = await tick(w, { prs: [4874] });
  ok(r.rec(4874).verdict === 'failed' && r.rec(4874).attribution === 'cannot-tell-which-change' && /cannot tell which change did it: 89d9e394 #4873/.test(r.rec(4874).why), '#4874 fails while #4873 changed its chain: cannot tell which change did it, and #4873 is named', r.rec(4874).why);
  const w2 = world(); bad(w2);
  const o = await tick(w2, { prs: [4874], own: true, cfg: { write_history_events: true } });
  const p = planWith(o.doc, o.events, iso(T1));
  ok(o.events.length === 1 && o.events[0].type === 'escalated' && o.events[0].class === 'cannot-tell-which-change' && stateOf(p.doc, o.events[0].item_id) === 'escalated', 'on the lane\'s own merge it holds and reports: escalated (a person owns it), not failed (no revert owed)');
  const w3 = world(); for (const p of podsIn(w3, 'media', 'seerr-')) p.spec.containers[0].image = 'ghcr.io/seerr-team/seerr:v3.5.1';
  const r3 = await tick(w3, { prs: [4869] });
  ok(r3.rec(4869).attribution === 'own-regression' && r3.rec(4869).other_changes.changes.length === 0 && fx.range.commits.length >= 7, '#4869 fails while seven later commits changed other apps only: the update\'s own regression', r3.rec(4869).why);
  const w4 = world(); for (const p of podsIn(w4, 'media', 'seerr-')) p.spec.containers[0].image = 'ghcr.io/seerr-team/seerr:v3.5.1';
  w4.extraCommits = [{ sha: 'c'.repeat(40), at: '2026-11-13T12:50:00Z', subject: 'fix(seerr): a values change', pr: 9001, author: 'haynes-dev-bot[bot]', files: ['kubernetes/main/apps/media/seerr/app/helmrelease.yaml'] }];
  const r4 = await tick(w4, { prs: [4869] });
  ok(r4.rec(4869).attribution === 'cannot-tell-which-change' && r4.rec(4869).other_changes.changes[0]?.parts.includes('target kubernetes/main/apps/media/seerr') && r4.rec(4869).other_changes.changes[0].fetched_by_flux === false, 'a later change to the same app (not fetched by Flux yet): cannot tell which change did it', JSON.stringify(r4.rec(4869).other_changes));
  const w5 = world(); for (const p of podsIn(w5, 'media', 'seerr-')) p.spec.containers[0].image = 'ghcr.io/seerr-team/seerr:v3.5.1'; w5.fail = { range: true };
  ok((await tick(w5, { prs: [4869] })).rec(4869).attribution === 'cannot-tell-which-change', 'a failure with the other changes unreadable: cannot tell which change did it (never charged to the update)');
}

// ── 6. an own image awaiting its digest pin ───────────────────────────────────────────
{
  const img = rules.own_images.find(o => o.id === 'audio-authoring');
  const S = 'a1'.repeat(20), P = 'b2'.repeat(20), D = 'd4'.repeat(32);
  const src = { number: 9101, state: 'MERGED', title: 'fix(container): update python docker tag to v3.14.1', author: { login: 'app/renovate' }, mergedAt: '2026-11-13T05:00:00Z', mergedBy: { login: 'app/renovate' }, mergeCommit: { oid: S }, labels: [], body: '| Package | Update | Change |\n|---|---|---|\n| python | patch | `3.14.0` → `3.14.1` |\n', files: [{ path: 'scripts/audio-authoring/Dockerfile' }], changedFiles: 1 };
  const pin = { number: 9102, state: 'MERGED', title: 'chore(container): update ghcr.io/thaynes43/audio-authoring docker digest', author: { login: 'app/renovate' }, mergedAt: '2026-11-13T05:20:00Z', mergedBy: { login: 'app/renovate' }, mergeCommit: { oid: P }, labels: [], body: `| Package | Update | Change |\n|---|---|---|\n| ghcr.io/thaynes43/audio-authoring | digest | \`aaaaaaa\` → \`${D.slice(0, 7)}\` |\n`, files: [{ path: img.pins[0] }], changedFiles: 1 };
  const base = () => {
    const w = world();
    w.ks = { items: [...w.ks.items, { metadata: { name: 'audio-authoring', namespace: 'dev', generation: 1, labels: { 'kustomize.toolkit.fluxcd.io/name': 'cluster-apps', 'kustomize.toolkit.fluxcd.io/namespace': 'flux-system' } }, spec: { path: './kubernetes/main/apps/dev/audio-authoring/app', sourceRef: { kind: 'GitRepository', name: 'haynes-ops', namespace: 'flux-system' } },
      status: { observedGeneration: 1, lastAppliedRevision: w.source.status.artifact.revision, lastAttemptedRevision: w.source.status.artifact.revision, conditions: [{ type: 'Ready', status: 'True', reason: 'ReconciliationSucceeded' }], inventory: { entries: [{ id: 'dev_audio-authoring_helm.toolkit.fluxcd.io_HelmRelease', v: 'v2' }] } } }] };
    w.hr = { items: [...w.hr.items, { metadata: { name: 'audio-authoring', namespace: 'dev', generation: 3, labels: { 'kustomize.toolkit.fluxcd.io/name': 'audio-authoring', 'kustomize.toolkit.fluxcd.io/namespace': 'dev' } }, spec: {},
      status: { observedGeneration: 3, conditions: [{ type: 'Ready', status: 'True', reason: 'UpgradeSucceeded' }], history: [{ chartName: 'app-template', chartVersion: '5.3.0', status: 'deployed', lastDeployed: '2026-11-13T05:22:00Z' }], inventory: { entries: [{ id: 'dev_audio-authoring_apps_Deployment', v: 'v1' }] } } }] };
    w.pods.dev = { pods: { items: [{ metadata: { name: 'audio-authoring-7f9c8d6b5-x1', namespace: 'dev', uid: 'u-audio-1', creationTimestamp: '2026-11-13T05:23:00Z', labels: { 'pod-template-hash': '7f9c8d6b5' }, ownerReferences: [{ kind: 'ReplicaSet', name: 'audio-authoring-7f9c8d6b5' }] },
      spec: { containers: [{ name: 'app', image: `ghcr.io/thaynes43/audio-authoring:0.2.0@sha256:${D}` }] }, status: { phase: 'Running', conditions: [{ type: 'Ready', status: 'True' }], containerStatuses: [{ name: 'app', imageID: `ghcr.io/thaynes43/audio-authoring@sha256:${D}`, ready: true, restartCount: 0, state: { running: {} } }] } }] }, cronjobs: { items: [] } };
    w.extraCommits = [{ sha: S, at: src.mergedAt, subject: src.title, pr: 9101, author: 'renovate[bot]', files: ['scripts/audio-authoring/Dockerfile'] }];
    w.runs = { [S]: [{ path: img.build, status: 'completed', conclusion: 'success', created_at: '2026-11-13T05:00:10Z', updated_at: '2026-11-13T05:06:00Z' }] };
    return w;
  };
  const run2 = async (w, prs, o = {}) => { const d = V.docOf(prs.map(n => n === 9101 ? src : pin), { repo: REPO, rules, at: iso(T1), actor: o.own ? { kind: 'lane', login: null } : null }); return tick(w, { prs: [9101], doc: d, ...o }); };
  const w = base();
  w.extraCommits.push({ sha: 'e1'.repeat(20), at: '2026-11-13T05:10:00Z', subject: 'docs: a push', pr: 9103, author: 'haynes-dev-bot[bot]', files: ['docs/x.md'] });
  const r = await run2(w, [9101]);
  ok(r.rec(9101).verdict === 'not-yet' && /awaiting the digest pin of audio-authoring.*1 of 3 pushes/.test(factOf(r.rec(9101), 'pin').detail), 'own image: not yet, awaiting the digest pin (1 of 3 pushes since the build)', r.rec(9101).why);
  ok(r.rec(9101).would_write.map(e => `${e.type}:${e.from}>${e.to}:${e.legal}`).join() === 'awaiting-pin:merged_unverified>awaiting_pin:true', 'own image: would write awaiting-pin (legal)');
  const o = await run2(base(), [9101], { own: true, cfg: { write_history_events: true } });
  const p = planWith(o.doc, o.events, iso(T1));
  ok(o.events[0]?.type === 'awaiting-pin' && stateOf(p.doc, o.events[0].item_id) === 'awaiting_pin', 'own image, the lane\'s merge: awaiting-pin recorded, the item awaiting_pin');
  const w2 = base(); for (let i = 0; i < 3; i++) w2.extraCommits.push({ sha: `e${i}`.repeat(20), at: `2026-11-13T05:1${i}:00Z`, subject: 'a push', pr: 9200 + i, author: 'haynes-dev-bot[bot]', files: ['docs/x.md'] });
  const r2 = await run2(w2, [9101]);
  ok(r2.rec(9101).verdict === 'failed' && r2.rec(9101).failing[0].class === 'pin-timeout' && /3 push\(es\) after the image build finished/.test(factOf(r2.rec(9101), 'pin').detail), 'own image: the pin timeout is a fact (3 pushes to main after the build, none the pin), not a clock', r2.rec(9101).why);
  const w3 = base(); w3.runs[S][0].conclusion = 'failure';
  ok((await run2(w3, [9101])).rec(9101).failing?.[0]?.class === 'not-applied', 'own image: a failed build fails (the image was not published)');
  const w4 = base(); w4.runs[S][0].status = 'in_progress';
  ok((await run2(w4, [9101])).rec(9101).verdict === 'not-yet', 'own image: a build still running is not yet');
  const w5 = base(); w5.runs = {};
  ok((await run2(w5, [9101])).rec(9101).verdict === 'cannot-tell', 'own image: the build unreadable is cannot tell');
  const w6 = base(); w6.extraCommits.push({ sha: P, at: pin.mergedAt, subject: pin.title, pr: 9102, author: 'renovate[bot]', files: [img.pins[0]] });
  w6.source.status.artifact.revision = `main@sha1:${P}`; // Flux fetched the pin, and the target applied it
  for (const k of w6.ks.items) { k.status.lastAppliedRevision = w6.source.status.artifact.revision; k.status.lastAttemptedRevision = w6.source.status.artifact.revision; }
  const d6 = V.docOf([src, pin], { repo: REPO, rules, at: iso(T1) });
  const a6 = await tick(w6, { prs: [9101, 9102], onlyPrs: [9101], doc: d6 });
  const b6 = await tick(w6, { prs: [9101, 9102], onlyPrs: [9101], doc: d6, now: T2, prev: a6.memory });
  ok(a6.rec(9101).verdict === 'not-yet' && b6.rec(9101).verdict === 'verified' && /pinned by #9102/.test(factOf(b6.rec(9101), 'pin').detail) && /1 container\(s\) in dev\/audio-authoring run ghcr\.io\/thaynes43\/audio-authoring d4d4d4d/.test(factOf(b6.rec(9101), 'image').detail), 'own image: verified through its pin #9102 (the new digest runs and is stable)', b6.rec(9101).why);
}

// ── 7. practice never reaches the history ─────────────────────────────────────────────
{
  const r = (await twice(world(), { prs: [4874], cfg: { write_history_events: true } })).b;
  ok(r.rec(4874).verdict === 'verified' && r.events.length === 0 && r.summary.practice_events_not_written === 1, 'practice with the setting on: still no event for the history');
  const forged = V.eventsFor({ id: r.doc.events[0].item_id, pr: 4874, state: 'merged_unverified', attempt: 1, merge_commit: sha(4874) }, { verdict: 'verified', why: 'x', facts: [], other_changes: { complete: true, changes: [] } }, iso(T1), REPO);
  const p = planWith(r.doc, forged, iso(T1));
  ok(p.ok && p.events.length === 0 && p.notes.unrecorded.some(u => /did not make \(practice\)/.test(u.why)) && stateOf(p.doc, forged[0].item_id) === 'merged_unverified', 'the history refuses a verifier event on a merge the lane did not make');
  const own = (await twice(world(), { prs: [4874], own: true })).b;
  ok(own.rec(4874).verdict === 'verified' && own.events.length === 0 && own.summary.events_for_history === 0, 'the lane\'s own merge with the setting off (as git ships): verified, no event');
  const ownOn = (await twice(world(), { prs: [4874], own: true, cfg: { write_history_events: true } })).b;
  const p2 = planWith(ownOn.doc, ownOn.events, iso(T2));
  ok(ownOn.events.length === 1 && ownOn.events[0].type === 'verified' && p2.events.length === 1 && stateOf(p2.doc, ownOn.events[0].item_id) === 'done' && ownOn.events[0].evidence.checks.length >= 8, 'the lane\'s own merge with the setting on: verified recorded, the item done, the facts in evidence.checks');
  const again = planWith(p2.doc, ownOn.events, iso(T2));
  ok(again.ok && again.events.length === 0, 'the same event twice is a duplicate the history drops');
  const notYet = await tick(world(), { prs: [4874], own: true, cfg: { write_history_events: true } });
  ok(notYet.rec(4874).verdict === 'not-yet' && notYet.events.length === 0, 'not yet writes no event');
}

// ── 8. the practice bound, the memory, the cap and the fair order ─────────────────────────
{
  // installed at the first commit of night-verify.mjs on main: earlier merges are the backfill's
  const w = world(); w.installed = [{ sha: 'f'.repeat(40), at: '2026-11-13T09:30:00Z' }, { sha: '9'.repeat(40), at: '2026-11-13T03:12:30Z' }];
  const r = await tick(w, { onlyPrs: null });
  ok(r.summary.since === '2026-11-13T03:12:30.000Z' && /first commit on main that added night-verify\.mjs: 99999999/.test(r.summary.since_basis), 'bound: since is the oldest commit of night-verify.mjs on main (a fact in git)', JSON.stringify([r.summary.since, r.summary.since_basis]));
  ok(r.summary.evaluated.sort().join() === '4870,4873,4874', 'bound: only merges at or after it are practised (#4869 and #4871 merged before)', r.summary.evaluated.join());
  const w2 = world();
  const r2 = await tick(w2, { onlyPrs: null });
  ok(r2.summary.evaluated.length === 0 && /unreadable: when the verifier was installed/.test(r2.summary.why_not), 'bound: an unreadable install time practises nothing (never the backfill)');
  const r3 = await tick(world(), { onlyPrs: null, prev: r.memory });
  ok(r3.summary.since === r.summary.since && !r3.calls.some(c => /night-verify\.mjs/.test(decodeURIComponent(c))), 'bound: kept in the memory, not read again');
  // own merges are verified whatever the bound
  const r4 = await tick(world(), { onlyPrs: null, own: true });
  ok(r4.summary.evaluated.length === 5, 'the lane\'s own merges are verified whatever the practice bound');
  // final practice verdicts are not judged again; non-final ones are
  const wb = world(); wb.installed = [{ sha: '9'.repeat(40), at: '2026-11-13T03:00:00Z' }];
  const t1 = await tick(wb, { onlyPrs: null }), t2 = await tick(wb, { onlyPrs: null, now: T2, prev: t1.memory }), t3 = await tick(wb, { onlyPrs: null, now: T3, prev: t2.memory });
  ok(t1.summary.verdicts.verified.join() === '4871' && t2.summary.verdicts.verified.length === 4 && t3.summary.evaluated.length === 0 && Object.keys(JSON.parse(t3.memory).done).length === 5 && /VERIFIED/.test(t3.table), 'memory: a final practice verdict is kept and not judged again', JSON.stringify(t3.summary.evaluated));
  // the per-tick cap and the fair order
  const c1 = await tick(world(), { cfg: { max_items_per_tick: 2 } });
  const c2 = await tick(world(), { cfg: { max_items_per_tick: 2 }, now: T2, prev: c1.memory });
  ok(c1.summary.evaluated.length === 2 && c1.summary.deferred.length === 3 && c2.summary.evaluated.every(n => !c1.summary.evaluated.includes(n)), 'cap: two per tick, and the next tick takes the least recently judged', JSON.stringify([c1.summary.evaluated, c2.summary.evaluated]));
  // the byte budget and the kept cap
  const k = await tick(world(), { cfg: { max_state_bytes: 9000 } });
  ok(Buffer.byteLength(k.memory) <= 9000 && k.summary.trimmed > 0, 'memory: data.verify stays within max_state_bytes (oldest records first)', `${Buffer.byteLength(k.memory)} bytes, trimmed ${k.summary.trimmed}`);
  const kc = await tick(world(), { cfg: { max_items_kept: 2 } });
  ok(Object.keys(JSON.parse(kc.memory).items).length === 2, 'memory: at most max_items_kept records');
  ok(V.parseMemory('not json').ok === false && V.parseMemory(null).absent === true, 'memory: an unparseable data.verify starts a new one; none is absent');
  // the time budget: no new item once the tick has run time_budget_seconds
  const f = fake(world());
  const late = await V.verifyPhase({ rules, run: f.run, repo: REPO, now: T1, startedAt: T1 - 200e3, clock: () => T1, onlyPrs: PRS, deps: { doc: V.docOf(views(PRS), { repo: REPO, rules, at: iso(T1) }), alertSubject: N.alertSubject, getJson: async () => fx.alerts } });
  ok(late.summary.evaluated.length === 0 && late.summary.deferred.length === 5, 'budget: a phase that starts after time_budget_seconds judges nothing and defers all');
  // the history unreadable: nothing judged, said why
  const f2 = fake({ ...world(), ledger: { metadata: { name: 'upgrade-ledger', resourceVersion: '5' }, data: { meta: 'not json', items: '{}', events: '' } } });
  const nh = await V.verifyPhase({ rules, run: f2.run, repo: REPO, now: T1, startedAt: T1, clock: () => T1, deps: { alertSubject: N.alertSubject, getJson: async () => fx.alerts } });
  ok(nh.summary.evaluated.length === 0 && /the history could not be read/.test(nh.summary.why_not), 'a history it cannot read: nothing judged, and it says why');
  ok(V.verifyLine(t2.summary).includes('verified #') && V.verifyLine({ error: 'x' }) === 'FAILED: x', 'the log line');
}

// ── 9. the image and version readers ──────────────────────────────────────────────────
{
  const p = V.parseImage('docker.io/library/traefik:v3.7.18@sha256:' + 'a'.repeat(64));
  ok(p.repo === 'traefik' && p.tag === 'v3.7.18' && p.digest === `sha256:${'a'.repeat(64)}`, 'parseImage: docker.io/library dropped, tag and digest read');
  ok(V.parseImage('registry.example:5000/x/y').tag === null && V.parseImage('registry.example:5000/x/y').repo === 'registry.example:5000/x/y', 'parseImage: a registry port is not a tag');
  ok(V.normRepo('rhasspy/wyoming-piper') === V.parseImage('docker.io/rhasspy/wyoming-piper:2.5.3').repo, 'normRepo: a Docker Hub short name matches its docker.io form');
  ok(V.versionMatches({ tag: 'v3.5.2' }, 'v3.5.2') === true && V.versionMatches({ tag: '3.5.2' }, 'v3.5.2') === true && V.versionMatches({ tag: '3.5.1' }, '3.5.2') === false, 'versionMatches: tags, a leading v ignored');
  ok(V.versionMatches({ tag: '0.2.0', digest: `sha256:${'d4'.repeat(32)}` }, 'd4d4d4d') === true && V.versionMatches({ tag: '0.2.0', image_id: `x@sha256:${'e5'.repeat(32)}` }, 'd4d4d4d') === false && V.versionMatches({ tag: '0.2.0' }, 'd4d4d4d') === null, 'versionMatches: a digest update compares the digest; none to read is cannot tell');
  ok(V.versionMatches({ tag: null }, '1.0.0') === null && V.versionMatches({ tag: '1.0.0' }, null) === null, 'versionMatches: no tag or no version is cannot tell');
  ok(V.inventoryOf({ status: {} }) === null && V.inventoryOf({ status: { inventory: { entries: [{ id: 'media_seerr_apps_Deployment' }, { id: '_x_rbac.authorization.k8s.io_ClusterRole' }] } } }).map(e => `${e.ns}/${e.name}/${e.group}/${e.kind}`).join() === 'media/seerr/apps/Deployment,/x/rbac.authorization.k8s.io/ClusterRole', 'inventoryOf: Flux ids, cluster-scoped ones too');
  ok(V.buildOf(null, 'x') === null && V.buildOf([], 'x').state === 'none' && V.buildOf([{ path: 'x', status: 'completed', conclusion: 'success', created_at: '1', updated_at: '2026-01-01T00:00:00Z' }], 'x').state === 'success', 'buildOf: the build run for a commit');
}

console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
