#!/usr/bin/env node
// Self-test for the read-only verifier of the night lane (story 553): night-verify.mjs.
//
// WHY THIS EXISTS
// ---------------
// After a merge the lane must prove the rollout healthy from facts it reads on its own ticks,
// never pass on a fact it could not read, never charge a failure to its update without
// positive evidence that the update did it (the principle of the review of PR 4880), and,
// while it only practises, never write the history. This test pins, against fakes (no
// cluster, no network), on the real facts of the five Renovate merges of 2026-11-13
// (fixtures/verify-20261113.json, captured read-only):
//   1. statically: no process API, one GET fetch, every read through the runner it is given;
//      git ships practice on and history events off; the scripts ConfigMap carries the file;
//      run-night.mjs runs it once, before the history, and hands only its events on;
//   2. the real night: two ticks at least min_stable_seconds apart verify #4869, #4870, #4873
//      and #4874; #4871 has no cluster effect; one tick is not yet;
//   3. each failure mode as a failed verdict and, on the lane's own merge, a revert decision
//      (verification-failed), each on positive evidence;
//   4. not yet and cannot tell (an unreadable fact never passes), the story-755 read named;
//   5. other changes after the merge and before it (L4);
//   6. own images and their pin (H2): identified by what it pins, never failed on absence;
//   7. practice never reaches the history; the lane's own events only with the setting on;
//   8. the practice bound, the memory (L3), the per-tick cap, the fair order, the time budget
//      before every shared read (L1), needs a person after the lane's nights;
//   9. the review of PR 4880: H1 (digests of digits), M1 (CronJobs exercised), M2 (600 s),
//      M3 (attribution by container, nodes, Prometheus restarts), M4 (start errors, Pending,
//      failing sources and HelmReleases on two reads, churn), L5 (init containers and native
//      sidecars), and a test for each mutation the review found surviving;
//  10. the image and version readers.
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
const DAY = 864e5;
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
    if (/ get nodes /.test(` ${a} `)) return w.nodes ?? null;
    if (/ get pods /.test(` ${a} `)) return w.fail?.pods ? null : (w.pods[ns]?.pods || { items: [] });
    if (/ get cronjobs /.test(` ${a} `)) return w.pods[ns]?.cronjobs || { items: [] };
    if (/ get jobs /.test(` ${a} `)) return w.fail?.jobs ? null : (w.pods[ns]?.jobs || { items: [] });
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
  if (argv[0] === 'pr' && argv[1] === 'view' && argv.includes('commits')) { const d = w.prHead?.[argv[2]]; return d ? { commits: [{ committedDate: '2026-01-01T00:00:00Z' }, { committedDate: d }] } : null; }
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
const READY_NODE = (n, boot = 'boot-1', since = '2026-11-01T00:00:00Z') => ({ metadata: { name: n }, status: { nodeInfo: { bootID: boot }, conditions: [{ type: 'Ready', status: 'True', lastTransitionTime: since }] } });
const world = () => { const pods = structuredClone(fx.pods); for (const ns of Object.keys(pods)) for (const p of pods[ns].pods.items) p.spec.nodeName = p.spec.nodeName || 'talosw01'; return { ks: structuredClone(fx.ks), hr: structuredClone(fx.hr), source: structuredClone(fx.source), pods, alerts: structuredClone(fx.alerts), promStart: '2026-11-01T00:00:00Z', nodes: { items: [READY_NODE('talosw01'), READY_NODE('talosw02')] } }; };
const views = list => list.map(n => fx.prs.find(p => p.number === n));
const vrules = over => ({ ...rules, night: { ...rules.night, verify: { ...rules.night.verify, ...over } } });
const gjOf = w => async url => (/runtimeinfo$/.test(url) ? (w.promStart ? { data: { startTime: w.promStart } } : null) : (w.alertsUnreadable ? null : w.alerts));
async function tick(w, { prs = PRS, now = T1, prev = null, own = false, cfg = {}, doc = null, onlyPrs = prs, deps = {}, startedAt = now, views: vs = null } = {}) {
  const d = doc || V.docOf(vs || views(prs), { repo: REPO, rules, at: iso(now), actor: own ? { kind: 'lane', login: null } : null });
  const f = fake(w);
  const r = await V.verifyPhase({ rules: vrules(cfg), run: f.run, repo: REPO, now, startedAt, clock: () => now, prevText: prev, onlyPrs,
    deps: { doc: d, alertSubject: N.alertSubject, getJson: gjOf(w), ...deps } });
  return { ...r, calls: f.calls, doc: d, rec: n => Object.values(r.records || {}).find(x => x.pr === n) };
}
const factOf = (rec, name) => (rec?.facts || []).find(f => f.fact === name) || {};
const twice = async (w, o = {}, w2 = w) => { const a = await tick(w, o); const b = await tick(w2, { ...o, now: T2, prev: a.memory, doc: a.doc }); return { a, b }; };
const ksOf = (w, id) => w.ks.items.find(k => L.ksId(k) === id);
const hrOf = (w, id) => w.hr.items.find(h => `${h.metadata.namespace}/${h.metadata.name}` === id);
const podsIn = (w, ns, prefix) => w.pods[ns].pods.items.filter(p => p.metadata.name.startsWith(prefix));
const planWith = (doc, verifyEvents, at) => H.planHistory({ doc: { ...doc, meta: { ...doc.meta, backfill: { at, window: { since: at, until: at }, query: 'q', events: 0, compare: null } } }, records: [], openPrs: [], verdict: null, at, repo: REPO, cfg: H.historyConfig(rules), rules,
  scan: { ok: true, list: [], reverts: [], github_count: 0, window: { since: at, until: at }, query: 'q', repo: REPO }, backfilling: false, renovateAuthors: rules.renovate_pr_authors, verifyEvents });
const stateOf = (doc, id) => H.replay(doc).items.get(id)?.item.state ?? null;
const placePods = (w, ns, prefix, node = 'talosw01') => { for (const p of podsIn(w, ns, prefix)) p.spec.nodeName = node; };

// ── 1. static ───────────────────────────────────────────────────────────────
{
  const strip = s => s.replace(/^\s*\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '');
  const src = strip(readFileSync(join(RES, 'night-verify.mjs'), 'utf8'));
  ok(!/child_process/.test(src) && !/(?<![.\w])(spawn|spawnSync|exec|execSync|execFile|execFileSync|fork)\s*\(/.test(src), 'static: night-verify.mjs starts no process (every read goes through the runner it is handed)');
  ok((src.match(/\bfetch\s*\(/g) || []).length === 1 && /fetch\(url, \{ method: 'GET'/.test(src), 'static: one fetch site, GET only (Prometheus)');
  ok((src.match(/\bwriteFileSync\s*\(/g) || []).length === 1 && src.indexOf('writeFileSync(o.state') > src.indexOf('async function main('), 'static: the one file write is the CLI\'s --state, a local file');
  ok(!/applyHistory|writeLaneState|ghWrite|callMerge|takeLock|releaseLock|writePrRecords|runRetry/.test(src), 'static: it calls no writer of the lane (history, state, PR records, lock, merge)');
  // 'rollout' is left out: it is the name of a fact here, and no process can run it
  const MUTATING = /['"](merge|comment|edit|close|reopen|review|push|commit|annotate|label|replace|delete|create|scale|exec|patch|apply|POST|PUT|PATCH|DELETE)['"]/g;
  ok(!(src.match(MUTATING) || []).length, 'static: no mutating verb literal', String(src.match(MUTATING)));
  const cfg = V.verifyConfig(rules);
  ok(cfg.ok, 'config: night.verify in upgrade-rules.json is valid', cfg.why);
  ok(cfg.practice === true && cfg.write_history_events === false, 'config: git ships practice on and the lane\'s history events off');
  ok(cfg.min_stable_seconds === 600 && cfg.needs_person_after_nights >= 1 && cfg.max_replacements >= 2 && cfg.prometheus_restart_slack_seconds > 0 && cfg.pin_wait_pushes === undefined, 'config: a 600 s soak, a bound in the lane\'s nights, a churn count, a Prometheus restart slack; no push count for the pin (H2)');
  ok(cfg.time_budget_seconds < 600 && cfg.max_items_per_tick <= 50 && cfg.max_range_commits <= 250, 'config: per tick the items, the commit range and the time are bounded, under the Job\'s 600s');
  ok(!V.verifyConfig(vrules({ min_stable_seconds: 1 })).ok && !V.verifyConfig(vrules({ needs_person_after_nights: 0 })).ok, 'config: a soak under 60 s or no night bound is refused');
  const kz = readFileSync(join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/kustomization.yaml'), 'utf8');
  ok(/\n\s+- resources\/night-verify\.mjs\n/.test(kz), 'the scripts ConfigMap (upgrade-shepherd-scripts) carries night-verify.mjs');
  const night = strip(readFileSync(join(RES, 'run-night.mjs'), 'utf8'));
  ok((night.match(/V\.verifyPhase\(/g) || []).length === 1 && night.indexOf('V.verifyPhase(') > night.indexOf('M.laneMergeStep(') && night.indexOf('V.verifyPhase(') < night.indexOf('H.historyPhase('), 'run-night.mjs: one verify phase, after the merge step and before the history');
  ok(/verifyEvents: ver\.events \|\| \[\]/.test(night) && !/ver\.records/.test(night), 'run-night.mjs: hands the history only the phase\'s events (never practice records)');
  ok(/await import\('\.\/night-verify\.mjs'\)/.test(night) && /catch \(e\) \{ ver = \{ summary: \{ error:/.test(night), 'run-night.mjs: loads the verifier inside a guarded step; a failure is recorded, not thrown');
  ok(/if \(ver\.memory && !ver\.summary\.error\) \{\s*try \{ writeLaneState\(verifyManifest\([^;]*VERIFY_MANAGER\); \}\s*catch \(e\) \{ log\(/.test(night) && !/stateManifest\(\{[^)]*verify/.test(night), 'run-night.mjs (L3): the memory is written only under its own manager, never in the tick\'s apply or its error record');
  const hist = strip(readFileSync(join(RES, 'upgrade-history.mjs'), 'utf8'));
  ok(/s\.item\.attempts\.at\(-1\)\?\.actor\?\.kind !== 'lane'/.test(hist), 'upgrade-history.mjs: refuses a verifier event unless the lane made the item\'s current attempt');
}

// ── 2. the real night of 2026-11-13 ──────────────────────────────────────────────
const real = await twice(world());
{
  const { a, b } = real;
  for (const n of [4869, 4870, 4873, 4874]) {
    ok(a.rec(n)?.verdict === 'not-yet' && factOf(a.rec(n), 'stable').result === 'waiting', `real #${n}: the first tick is not yet (the rollout recorded; stability needs a later tick)`, JSON.stringify(a.rec(n)?.why));
    ok(b.rec(n)?.verdict === 'verified', `real #${n}: verified on the second tick, 900 s later`, b.rec(n)?.why);
    ok(['source', 'applied', 'helmreleases', 'pods', 'alerts', 'image', 'rollout', 'stable'].every(x => ['pass', 'not-applicable'].includes(factOf(b.rec(n), x).result)), `real #${n}: every fact has its own passing result`);
    ok(b.rec(n)?.practice === true && (b.rec(n).would_write || []).map(e => `${e.type}:${e.legal}`).join() === 'verified:true', `real #${n}: practice; would write verified (legal on someone else's merge), not written`);
  }
  ok(b.rec(4871)?.verdict === 'verified' && factOf(b.rec(4871), 'source').result === 'not-applicable' && a.rec(4871)?.verdict === 'verified', 'real #4871 (a GitHub Action): no cluster effect, nothing to verify');
  ok(/HelmRelease network\/traefik-external runs chart traefik 42\.4\.0/.test(factOf(b.rec(4873), 'image').detail), 'real #4873: the chart update is read from the HelmRelease\'s last release (traefik 42.4.0)');
  ok(/4 container\(s\).*cloudflared 2026\.11\.0/.test(factOf(b.rec(4874), 'image').detail), 'real #4874: four containers run cloudflared 2026.11.0');
  ok(/rhasspy\/wyoming-piper 2\.5\.3/.test(factOf(b.rec(4870), 'image').detail) && b.rec(4870).updated.some(x => /Deployment\/ai\/piper\/app/.test(x)), 'real #4870: piper runs 2.5.3; the update\'s container is piper/app');
  const oc = b.rec(4874).other_changes;
  ok(oc.complete && oc.changes.length === 1 && oc.changes[0].pr === 4873 && /chain network\/traefik-external/.test(oc.changes[0].parts.join()), 'real #4874: one other change, #4873 on its chain (traefik-external); the pass stands', JSON.stringify(oc));
  ok(b.rec(4869).other_changes.complete && b.rec(4869).other_changes.changes.length === 0, 'real #4869: no other change reached seerr, its chain or a cluster-wide part');
  ok(b.summary.events_for_history === 0 && b.events.length === 0 && b.summary.practice_events_not_written === 5, 'real: no event for the history; five practice events not written');
  ok(a.calls.every(c => /^(gh (api|pr view)|kubectl get) /.test(c)), 'real: every command was a read (gh api GET, kubectl get)');
  ok(b.summary.reads.commands <= 30 && b.summary.reads.github <= 12, `real: ${b.summary.reads.commands} reads (${b.summary.reads.github} GitHub) for five merges`);
  ok(/#4874 .* VERIFIED/.test(b.table) && /other changes: [0-9a-f]{8} #4873/.test(b.table) && /would write \(not written: practice\): verified merged_unverified->done/.test(b.table), 'real: the morning view names each verdict, fact and other change');
  ok(/open: not-yet since .*judged on 1 tick\(s\) over 1 night\(s\).*pods recorded at/.test(a.table), 'real: the morning view says what an open verdict waits for, since when and over how many nights');
}

// ── 3. the failure modes (practice: failed; the lane's own merge: a revert decision) ───────
async function failure(name, n, mutate, cls, { secondTick = false, mutateSecond = () => {} } = {}) {
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
await failure('not applied, on two reads 900 s apart (N2)', 4870, w => { const k = piperKs(w); k.status.lastAppliedRevision = `main@sha1:${sha(4869)}`; k.status.lastAttemptedRevision = w.source.status.artifact.revision; k.status.conditions = [{ type: 'Ready', status: 'False', reason: 'BuildFailed' }]; }, 'not-applied', { secondTick: true });
await failure('a HelmRelease failing on two reads 900 s apart (M4)', 4870, w => { hrOf(w, 'ai/piper').status.conditions = [{ type: 'Ready', status: 'False', reason: 'UpgradeFailed' }, { type: 'Stalled', status: 'True', reason: 'RetriesExceeded' }]; }, 'not-applied', { secondTick: true });
await failure('not Ready (Kustomization health), on two reads 900 s apart (N2)', 4869, w => { ksOf(w, 'media/seerr').status.conditions = [{ type: 'Ready', status: 'False', reason: 'HealthCheckFailed' }]; }, 'not-ready', { secondTick: true });
{
  const wk = world(); ksOf(wk, 'media/seerr').status.conditions = [{ type: 'Ready', status: 'False', reason: 'HealthCheckFailed' }];
  const rk = await tick(wk, { prs: [4869] });
  ok(rk.rec(4869).verdict === 'not-yet' && /media\/seerr applied .* and is failing.*since .*fails if it still holds 600 s after that/.test(factOf(rk.rec(4869), 'applied').detail), 'N2: a failing Kustomization on one read is not yet, saying since when', rk.rec(4869).why);
}
await failure('wrong image', 4869, w => { for (const p of podsIn(w, 'media', 'seerr-')) p.spec.containers[0].image = 'ghcr.io/seerr-team/seerr:v3.5.1'; }, 'wrong-image');
await failure('restart growth of the update\'s container', 4870, null, 'restart-growth', { secondTick: true, mutateSecond: w => { podsIn(w, 'ai', 'piper-')[0].status.containerStatuses[0].restartCount += 1; } });
await failure('the update\'s container not Ready on the later tick, its node Ready', 4870, w => placePods(w, 'ai', 'piper-'), 'not-ready', { secondTick: true, mutateSecond: w => { const p = podsIn(w, 'ai', 'piper-')[0]; p.status.conditions = [{ type: 'Ready', status: 'False' }]; p.status.containerStatuses[0].ready = false; } });
const seerrPod = fx.pods.media.pods.items[0].metadata.name;
await failure('a new critical alert', 4869, w => { w.alerts.data.alerts.push({ labels: { alertname: 'SeerrDown', severity: 'critical', namespace: 'media', pod: seerrPod }, state: 'firing', activeAt: '2026-11-13T04:00:00Z' }); }, 'critical-alert');
await failure('a new critical alert that starts exactly at the merge', 4869, w => { w.alerts.data.alerts.push({ labels: { alertname: 'SeerrDown', severity: 'critical', namespace: 'media', pod: seerrPod }, state: 'firing', activeAt: fx.prs.find(p => p.number === 4869).mergedAt }); }, 'critical-alert');
await failure('a crash loop of the update\'s container', 4870, w => { const c = podsIn(w, 'ai', 'piper-')[0].status.containerStatuses[0]; c.state = { waiting: { reason: 'CrashLoopBackOff' } }; c.restartCount = 4; }, 'crash-loop');
await failure('an image-pull failure of the update\'s container', 4869, w => { podsIn(w, 'media', 'seerr-')[0].status.containerStatuses[0].state = { waiting: { reason: 'ImagePullBackOff' } }; }, 'image-pull');
await failure('a container that cannot start, on two reads 900 s apart (M4)', 4869, w => { podsIn(w, 'media', 'seerr-')[0].status.containerStatuses[0].state = { waiting: { reason: 'CreateContainerConfigError', message: 'secret "seerr" not found' } }; }, 'not-ready', { secondTick: true });
await failure('a chart settled on the old version with its source settled too (no wrong verified)', 4873, w => { const h = hrOf(w, 'network/traefik-external'); h.status.history[0].chartVersion = '42.3.2+89dc66bab32a';
  w.charts = [{ metadata: { name: 'traefik-external-oci', namespace: 'network', generation: 1 }, status: { observedGeneration: 1, conditions: [{ type: 'Ready', status: 'True' }], artifact: { revision: '42.3.2@sha256:' + 'a'.repeat(64) } } }]; }, 'wrong-image');
await failure('a chart source failing on two reads 900 s apart (M4)', 4873, w => { const h = hrOf(w, 'network/traefik-external'); h.status.history[0].chartVersion = '42.3.2+89dc66bab32a';
  w.charts = [{ metadata: { name: 'traefik-external-oci', namespace: 'network', generation: 2 }, status: { observedGeneration: 2, conditions: [{ type: 'Ready', status: 'False', reason: 'OCIArtifactPullFailed' }, { type: 'Stalled', status: 'True' }] } }]; }, 'not-applied', { secondTick: true });
{
  // N3: churn counts only pods replaced after they were not Ready or restarting; a pod moved
  // while Ready (the descheduler, a drain) is recorded again and counted as a move
  const seerrW = (gen, ready = true) => { const w = world(); const p = podsIn(w, 'media', 'seerr-')[0]; if (gen) { p.metadata.uid += `-${gen}`; p.metadata.name += `-${gen}`; } if (!ready) { p.status.conditions = [{ type: 'Ready', status: 'False' }]; p.status.containerStatuses[0].ready = false; } return w; };
  // a sick pod (not Ready on a read under 600 s after the record) then replaced, three times
  const r0 = await tick(seerrW(0), { prs: [4869] });
  let prev = r0.memory, last, t = T1;
  for (let i = 1; i <= 3; i++) {
    t += 300e3; last = await tick(seerrW(i - 1, false), { prs: [4869], now: t, prev, doc: r0.doc }); prev = last.memory;
    t += 300e3; last = await tick(seerrW(i), { prs: [4869], now: t, prev, doc: r0.doc }); prev = last.memory;
  }
  ok(last.rec(4869).verdict === 'failed' && last.rec(4869).failing[0].class === 'churn' && /replaced 3 times after they were not Ready or restarting/.test(factOf(last.rec(4869), 'stable').detail), 'churn (N3): pods replaced three times after they were not Ready fail', last.rec(4869).why);
  // healthy pods moved three times: never churn, the moves are counted and shown
  const m0 = await tick(seerrW(0), { prs: [4869] });
  let pm = m0.memory, lm;
  for (let i = 1; i <= 3; i++) { lm = await tick(seerrW(i), { prs: [4869], now: T1 + i * 900e3, prev: pm, doc: m0.doc }); pm = lm.memory; }
  ok(lm.rec(4869).verdict === 'not-yet' && /moved \(Ready, no new restarts at its last read\)/.test(factOf(lm.rec(4869), 'stable').detail) && /pods Deployment\/media\/seerr moved 3x/.test(lm.table), 'N3: a Ready pod moved three times is not churn; the moves show in the morning view', lm.rec(4869).why);
  const after = await tick(seerrW(3), { prs: [4869], now: T1 + 4 * 900e3, prev: pm, doc: m0.doc });
  ok(after.rec(4869).verdict === 'verified', 'N3: after the moves the new record soaks and verifies', after.rec(4869).why);
}
{
  // an alert that fired before the merge is not new; another app's in the target's namespace is not the target's
  const w = world();
  w.alerts.data.alerts.push({ labels: { alertname: 'SeerrDown', severity: 'critical', namespace: 'media', pod: seerrPod }, state: 'firing', activeAt: '2026-11-13T02:00:00Z' });
  w.alerts.data.alerts.push({ labels: { alertname: 'PiperDown', severity: 'critical', namespace: 'ai', pod: fx.pods.ai.pods.items[0].metadata.name }, state: 'firing', activeAt: '2026-11-13T04:00:00Z' });
  const radarr = hrOf(w, 'media/radarr') ? 'radarr' : null;
  if (radarr) w.pods.media.pods.items.push({ metadata: { name: 'radarr-6f7d9c8b5-abcde', namespace: 'media', uid: 'u-radarr', creationTimestamp: '2026-11-13T04:00:00Z', labels: { 'app.kubernetes.io/instance': 'radarr', 'pod-template-hash': '6f7d9c8b5' }, ownerReferences: [{ kind: 'ReplicaSet', name: 'radarr-6f7d9c8b5' }] }, spec: { containers: [{ name: 'app', image: 'ghcr.io/home-operations/radarr:6' }] }, status: { phase: 'Running', conditions: [{ type: 'Ready', status: 'True' }], containerStatuses: [{ name: 'app', ready: true, restartCount: 0, state: { running: {} } }] } });
  if (radarr) w.alerts.data.alerts.push({ labels: { alertname: 'RadarrDown', severity: 'critical', namespace: 'media', pod: 'radarr-6f7d9c8b5-abcde' }, state: 'firing', activeAt: '2026-11-13T04:00:00Z' });
  const r = (await twice(w, { prs: [4869] })).b;
  ok(!!radarr && r.rec(4869).verdict === 'verified' && /firing since before the merge: SeerrDown/.test(factOf(r.rec(4869), 'alerts').detail), 'alerts: one that fired before the merge is listed; another app\'s in the target\'s namespace (radarr in media) is not the target\'s', r.rec(4869).why);
  const w2 = world();
  w2.alerts.data.alerts.push({ labels: { alertname: 'BlackboxProbeFailed', severity: 'critical', namespace: 'media' }, state: 'firing', activeAt: '2026-11-13T04:00:00Z' });
  const r2 = await tick(w2, { prs: [4869] });
  ok(r2.rec(4869).verdict === 'cannot-tell' && factOf(r2.rec(4869), 'alerts').result === 'unreadable', 'alerts: a new alert in the target namespace that names no workload: cannot tell (never a pass)', r2.rec(4869).why);
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
  const wn = world(); podsIn(wn, 'media', 'seerr-')[0].status.conditions = [{ type: 'Ready', status: 'False' }];
  const rn = await tick(wn, { prs: [4869] });
  ok(rn.rec(4869).verdict === 'not-yet' && /1 of 1 pod\(s\) not Ready/.test(factOf(rn.rec(4869), 'rollout').detail) && factOf(rn.rec(4869), 'stable').result === 'waiting', 'not yet: a pod not Ready on the first tick is still rolling (the rollout does not ignore readiness)', rn.rec(4869).why);
  const wp = world(); ksOf(wp, 'media/seerr').status.conditions = [{ type: 'Ready', status: 'True' }, { type: 'Reconciling', status: 'True', reason: 'Progressing' }];
  const rp = (await twice(wp, { prs: [4869] })).b;
  ok(rp.rec(4869).verdict === 'not-yet' && factOf(rp.rec(4869), 'applied').result === 'waiting' && /Reconciling/.test(factOf(rp.rec(4869), 'applied').detail), 'not yet: a Kustomization still progressing does not pass, on any tick', rp.rec(4869).why);
  const w3 = world(); hrOf(w3, 'media/seerr').status.conditions = [{ type: 'Ready', status: 'Unknown', reason: 'Progressing' }];
  ok((await tick(w3, { prs: [4869] })).rec(4869).verdict === 'not-yet', 'not yet: a HelmRelease still progressing');
  const wd = world(); hrOf(wd, 'media/seerr').status.history[0].status = 'pending-upgrade';
  const rd = (await twice(wd, { prs: [4869] })).b;
  ok(rd.rec(4869).verdict === 'not-yet' && /its last release is pending-upgrade/.test(factOf(rd.rec(4869), 'helmreleases').detail), 'not yet: a HelmRelease whose last release is not deployed does not pass', rd.rec(4869).why);
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
  const wc = world(); const chainId = `${ksOf(wc, 'media/seerr').spec.dependsOn[1].namespace}/${ksOf(wc, 'media/seerr').spec.dependsOn[1].name}`;
  ksOf(wc, chainId).status.conditions = [{ type: 'Ready', status: 'False', reason: 'HealthCheckFailed' }, { type: 'Stalled', status: 'True' }];
  const rc = await tick(wc, { prs: [4869] });
  ok(rc.rec(4869).verdict === 'cannot-tell' && new RegExp(`${chainId}.*in the dependsOn chain.*is failing`).test(factOf(rc.rec(4869), 'applied').detail), 'cannot tell: a failing Kustomization in the chain is not ignored', factOf(rc.rec(4869), 'applied').detail);
  const wa = world(); ksOf(wa, 'flux-system/cluster-apps').status.conditions = [{ type: 'Ready', status: 'False', reason: 'BuildFailed' }, { type: 'Stalled', status: 'True' }];
  const ra = await tick(wa, { prs: [4869] });
  ok(ra.rec(4869).verdict === 'cannot-tell' && /cluster-apps \(applies the target\) is failing/.test(factOf(ra.rec(4869), 'applied').detail), 'cannot tell: a failing Kustomization that applies the target is not ignored', factOf(ra.rec(4869), 'applied').detail);
  const w11 = world(); w11.fail = { range: true };
  const r11 = (await twice(w11, { prs: [4874] })).b;
  ok(r11.rec(4874).verdict === 'verified' && r11.rec(4874).other_changes.complete === false, 'a pass stands with the other changes unreadable (they are listed as unreadable)');
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
  // L4: an earlier merge to the same scope whose own verdict is not final counts as another change
  const w6 = world(); for (const p of podsIn(w6, 'media', 'seerr-')) p.spec.containers[0].image = 'ghcr.io/seerr-team/seerr:v3.5.1';
  const E = 'e9'.repeat(20);
  const earlier = { ...fx.prs.find(p => p.number === 4869), number: 9050, title: 'fix(container): update image ghcr.io/example/seerr-sidecar to v1.0.1', mergedAt: '2026-11-13T03:10:56Z', mergeCommit: { oid: E }, body: '| Package | Update | Change |\n|---|---|---|\n| ghcr.io/example/seerr-sidecar | patch | `1.0.0` → `1.0.1` |\n' };
  const d6 = V.docOf([earlier, fx.prs.find(p => p.number === 4869)], { repo: REPO, rules, at: iso(T1) });
  const id9050 = d6.events.find(e => e.carrier.pr === 9050).item_id;
  const mem9050 = JSON.stringify({ schema: 'haynes.upgrade-verify/v1', since: null, items: { [id9050]: { pr: 9050, merged_at: earlier.mergedAt, verdict: 'not-yet', final: false, files: ['kubernetes/main/apps/media/seerr/app/helmrelease.yaml'] } }, done: {} });
  const r6 = await tick(w6, { prs: [9050, 4869], onlyPrs: [9050, 4869], doc: d6, prev: mem9050 });
  ok(r6.rec(4869).attribution === 'cannot-tell-which-change' && r6.rec(4869).other_changes.complete && r6.rec(4869).other_changes.changes.some(c => c.pr === 9050 && c.before === true) && /#9050 \(merged before, not verified yet\)/.test(r6.rec(4869).why),
    'L4: an earlier merge to the same app whose own verdict is not final counts as another change', r6.rec(4869).why);
  const r6b = await tick(w6, { prs: [9050, 4869], onlyPrs: [9050, 4869], doc: d6 });
  ok(r6b.rec(4869).attribution === 'cannot-tell-which-change' && /an earlier merge not verified yet whose files were not read \(#9050\)/.test(r6b.rec(4869).other_changes.why), 'L4: an earlier unverified merge whose files are unknown makes the list incomplete (cannot tell which change did it)', JSON.stringify(r6b.rec(4869).other_changes));
  const r6c = await tick(w6, { prs: [9050, 4869], onlyPrs: [9050, 4869], doc: d6, prev: JSON.stringify({ ...JSON.parse(mem9050), done: { [id9050]: 'verified 2026-11-13T04:00:00.000Z' } }) });
  ok(r6c.rec(4869).attribution === 'own-regression', 'L4: an earlier merge whose own verdict is final does not count', r6c.rec(4869).why);
}

// ── 6. own images and their pin (H2) ─────────────────────────────────────────────────
{
  const img = rules.own_images.find(o => o.id === 'audio-authoring');
  const S = 'a1'.repeat(20), P = 'b2'.repeat(20), X = 'c3'.repeat(20), D = 'd4'.repeat(32);
  const src = { number: 9101, state: 'MERGED', title: 'fix(container): update python docker tag to v3.14.1', author: { login: 'app/renovate' }, mergedAt: '2026-11-13T05:00:00Z', mergedBy: { login: 'app/renovate' }, mergeCommit: { oid: S }, labels: [{ name: 'type/patch' }], body: '| Package | Update | Change |\n|---|---|---|\n| python | patch | `3.14.0` → `3.14.1` |\n', files: [{ path: 'scripts/audio-authoring/Dockerfile' }], changedFiles: 1 };
  const pinOf = (n, sha_, at, digest, name = 'ghcr.io/thaynes43/audio-authoring') => ({ number: n, state: 'MERGED', title: `chore(container): update ${name} docker digest`, author: { login: 'app/renovate' }, mergedAt: at, mergedBy: { login: 'app/renovate' }, mergeCommit: { oid: sha_ }, labels: [{ name: 'type/digest' }], body: `| Package | Update | Change |\n|---|---|---|\n| ${name} | digest | \`aaaaaaa\` → \`${digest.slice(0, 7)}\` |\n`, files: [{ path: img.pins[0] }], changedFiles: 1 });
  const pin = pinOf(9102, P, '2026-11-13T05:20:00Z', D);
  const base = () => {
    const w = world();
    w.ks = { items: [...w.ks.items, { metadata: { name: 'audio-authoring', namespace: 'dev', generation: 1, labels: { 'kustomize.toolkit.fluxcd.io/name': 'cluster-apps', 'kustomize.toolkit.fluxcd.io/namespace': 'flux-system' } }, spec: { path: './kubernetes/main/apps/dev/audio-authoring/app', sourceRef: { kind: 'GitRepository', name: 'haynes-ops', namespace: 'flux-system' } },
      status: { observedGeneration: 1, lastAppliedRevision: w.source.status.artifact.revision, lastAttemptedRevision: w.source.status.artifact.revision, conditions: [{ type: 'Ready', status: 'True', reason: 'ReconciliationSucceeded' }], inventory: { entries: [{ id: 'dev_audio-authoring_helm.toolkit.fluxcd.io_HelmRelease', v: 'v2' }] } } }] };
    w.hr = { items: [...w.hr.items, { metadata: { name: 'audio-authoring', namespace: 'dev', generation: 3, labels: { 'kustomize.toolkit.fluxcd.io/name': 'audio-authoring', 'kustomize.toolkit.fluxcd.io/namespace': 'dev' } }, spec: {},
      status: { observedGeneration: 3, conditions: [{ type: 'Ready', status: 'True', reason: 'UpgradeSucceeded' }], history: [{ chartName: 'app-template', chartVersion: '5.3.0', status: 'deployed', lastDeployed: '2026-11-13T05:22:00Z' }], inventory: { entries: [{ id: 'dev_audio-authoring_apps_Deployment', v: 'v1' }] } } }] };
    w.pods.dev = { pods: { items: [{ metadata: { name: 'audio-authoring-7f9c8d6b5-x1', namespace: 'dev', uid: 'u-audio-1', creationTimestamp: '2026-11-13T05:23:00Z', labels: { 'pod-template-hash': '7f9c8d6b5' }, ownerReferences: [{ kind: 'ReplicaSet', name: 'audio-authoring-7f9c8d6b5' }] },
      spec: { containers: [{ name: 'app', image: `ghcr.io/thaynes43/audio-authoring:0.2.0@sha256:${D}` }] }, status: { phase: 'Running', conditions: [{ type: 'Ready', status: 'True' }], containerStatuses: [{ name: 'app', imageID: `ghcr.io/thaynes43/audio-authoring@sha256:${D}`, ready: true, restartCount: 0, state: { running: {} } }] } }] }, cronjobs: { items: [] }, jobs: { items: [] } };
    w.extraCommits = [{ sha: S, at: src.mergedAt, subject: src.title, pr: 9101, author: 'renovate[bot]', files: ['scripts/audio-authoring/Dockerfile'] }];
    w.runs = { [S]: [{ path: img.build, status: 'completed', conclusion: 'success', created_at: '2026-11-13T05:00:10Z', updated_at: '2026-11-13T05:06:00Z' }] };
    w.prHead = { 9102: '2026-11-13T05:12:00Z' };
    return w;
  };
  const run2 = (w, views_, o = {}) => tick(w, { prs: views_.map(v => v.number), onlyPrs: [9101], views: views_, ...o });
  // 1 push, no pin: awaiting, with the build's state; would write awaiting-pin
  const w = base(); w.extraCommits.push({ sha: 'e1'.repeat(20), at: '2026-11-13T05:10:00Z', subject: 'docs: a push', pr: 9103, author: 'haynes-dev-bot[bot]', files: ['docs/x.md'] });
  const r = await run2(w, [src]);
  ok(r.rec(9101).verdict === 'not-yet' && /awaiting the digest pin of audio-authoring: the build finished at 2026-11-13T05:06:00Z; no pin PR is open/.test(factOf(r.rec(9101), 'pin').detail), 'own image: not yet, awaiting the digest pin, with the build\'s state', r.rec(9101).why);
  ok(r.rec(9101).would_write.map(e => `${e.type}:${e.from}>${e.to}:${e.legal}`).join() === 'awaiting-pin:merged_unverified>awaiting_pin:true', 'own image: would write awaiting-pin (legal)');
  const o = await run2(base(), [src], { own: true, cfg: { write_history_events: true } });
  const p = planWith(o.doc, o.events, iso(T1));
  ok(o.events[0]?.type === 'awaiting-pin' && stateOf(p.doc, o.events[0].item_id) === 'awaiting_pin', 'own image, the lane\'s merge: awaiting-pin recorded, the item awaiting_pin');
  // H2 b, L-e, L-g: no pin is only awaiting; open on two nights and enough ticks it needs a person; a practice item is then given up
  const w2 = base(); for (let i = 0; i < 6; i++) w2.extraCommits.push({ sha: `e${i}`.repeat(20), at: `2026-11-13T05:1${i}:00Z`, subject: 'a push', pr: 9200 + i, author: 'haynes-dev-bot[bot]', files: ['docs/x.md'] });
  const n1 = await run2(w2, [src], { cfg: { needs_person_min_ticks: 3 } });
  const n2 = await run2(w2, [src], { now: T1 + DAY, prev: n1.memory, doc: n1.doc, cfg: { needs_person_min_ticks: 3 } });
  const n3 = await run2(w2, [src], { now: T1 + DAY + 900e3, prev: n2.memory, doc: n1.doc, cfg: { needs_person_min_ticks: 3 } });
  ok(n1.rec(9101).verdict === 'not-yet' && !n2.rec(9101).needs_person && n3.rec(9101).verdict === 'not-yet' && n3.rec(9101).needs_person === true && /^NEEDS A PERSON: open on 2 of the lane's nights .* and 3 ticks/.test(n3.rec(9101).why) && /GIVEN UP, NEEDS A PERSON/.test(n3.table) && n3.summary.needs_person.join() === '9101',
    'own image (H2 b, L-e): no pin is never failed; on the second night it needs a person only once it has the minimum ticks (2 ticks over 2 nights: not yet marked)', n3.rec(9101).why);
  ok(n3.rec(9101).gave_up === true && n3.rec(9101).final === true && /given-up/.test(JSON.parse(n3.memory).done[n3.rec(9101).item]) && n3.summary.given_up.join() === '9101', 'L-g: a practice item that needs a person is given up: final, in done, not judged again');
  // H2 a, L-d: commits to the pins file that are not the pin; a pin made after the build started (before it finished) is the pin
  const w3 = base();
  w3.extraCommits.push({ sha: X, at: '2026-11-13T05:15:00Z', subject: 'fix(audio-authoring): a values change by the shepherd', pr: 9104, author: 'haynes-ops-bot[bot]', files: [img.pins[0]] });
  const other = pinOf(9105, 'f5'.repeat(20), '2026-11-13T05:16:00Z', 'e'.repeat(64), 'ghcr.io/thaynes43/blender-authoring');
  w3.extraCommits.push({ sha: other.mergeCommit.oid, at: other.mergedAt, subject: other.title, pr: 9105, author: 'renovate[bot]', files: [img.pins[0]] });
  const early = pinOf(9106, 'a7'.repeat(20), '2026-11-13T05:17:00Z', 'b'.repeat(64));
  w3.extraCommits.push({ sha: early.mergeCommit.oid, at: early.mergedAt, subject: early.title, pr: 9106, author: 'renovate[bot]', files: [img.pins[0]] });
  w3.prHead = { 9106: '2026-11-13T04:59:00Z' }; // made before this build started: another build's digest
  const r3 = await run2(w3, [src, other, early]);
  ok(r3.rec(9101).verdict === 'not-yet' && /not the pin: c3c3c3c3 #9104 \(not an update of audio-authoring\), f5f5f5f5 #9105 \(not an update of audio-authoring\), #9106 \(made 2026-11-13T04:59:00Z, before this build started at 2026-11-13T05:00:10Z/.test(factOf(r3.rec(9101), 'pin').detail),
    'own image (H2 a): a shepherd edit of the pins file, a pin of another image and a pin made before this build started are not the pin', factOf(r3.rec(9101), 'pin').detail);
  const w3b = base(); w3b.extraCommits.push({ sha: early.mergeCommit.oid, at: early.mergedAt, subject: early.title, pr: 9106, author: 'renovate[bot]', files: [img.pins[0]] });
  w3b.prHead = { 9106: '2026-11-13T05:03:00Z' }; // after the build started at 05:00:10, before it finished at 05:06 (it signs after pushing)
  ok(/pinned by #9106 .* made after this build started at 2026-11-13T05:00:10Z/.test(factOf((await run2(w3b, [src, early])).rec(9101), 'pin').detail), 'L-d: a pin made after the build started, before its last update, is the pin (the run\'s start ties them)');
  // the pin, made after the build: verified through it
  const pinned = () => { const w6 = base(); w6.extraCommits.push({ sha: P, at: pin.mergedAt, subject: pin.title, pr: 9102, author: 'renovate[bot]', files: [img.pins[0]] });
    w6.source.status.artifact.revision = `main@sha1:${P}`; // Flux fetched the pin, and the target applied it
    for (const k of w6.ks.items) { k.status.lastAppliedRevision = w6.source.status.artifact.revision; k.status.lastAttemptedRevision = w6.source.status.artifact.revision; }
    for (const pp of w6.pods.dev.pods.items) pp.spec.nodeName = 'talosw01';
    return w6; };
  const w6 = pinned();
  const a6 = await run2(w6, [src, pin]);
  const b6 = await run2(w6, [src, pin], { now: T2, prev: a6.memory, doc: a6.doc });
  ok(a6.rec(9101).verdict === 'not-yet' && b6.rec(9101).verdict === 'verified' && /pinned by #9102 .* made after this build started/.test(factOf(b6.rec(9101), 'pin').detail) && /1 container\(s\) in dev\/audio-authoring run ghcr\.io\/thaynes43\/audio-authoring d4d4d4d/.test(factOf(b6.rec(9101), 'image').detail), 'own image: verified through its pin #9102 (the new digest runs and is stable)', b6.rec(9101).why);
  // N1: after the pin merged, its rollout can fail; before it, nothing does
  const w6f = pinned(); for (const pp of w6f.pods.dev.pods.items) { pp.spec.containers[0].image = `ghcr.io/thaynes43/audio-authoring:0.2.0@sha256:${'1'.repeat(64)}`; pp.status.containerStatuses[0].imageID = `ghcr.io/thaynes43/audio-authoring@sha256:${'1'.repeat(64)}`; }
  ok((await run2(w6f, [src, pin])).rec(9101).failing?.[0]?.class === 'wrong-image', 'N1: after the pin merged its rollout fails (the old digest runs)');
  const w7 = base(); w7.runs[S][0].conclusion = 'cancelled';
  const r7 = await run2(w7, [src]);
  ok(r7.rec(9101).verdict === 'not-yet' && /the build .* ended cancelled \(a later build may carry the change\)/.test(factOf(r7.rec(9101), 'pin').detail) && r7.rec(9101).pin.state === 'awaiting (build failed)', 'N1: a cancelled build is not a failure: awaiting the pin, with the reason', r7.rec(9101).why);
  const w7b = base(); w7b.runs[S][0].conclusion = 'failure';
  ok((await run2(w7b, [src])).rec(9101).verdict === 'not-yet', 'N1: a failed build is not a failure either');
  const openPin = { pr: 9110, lane: 'BLOCKED', class: 'check-failing-after-retry', reason: 'failing: Flux Local - Test (main) after a retry', packages: [{ name: 'ghcr.io/thaynes43/audio-authoring', to: 'abc1234' }], files: [img.pins[0]] };
  const r8 = await run2(base(), [src], { deps: { records: [openPin] } });
  ok(r8.rec(9101).verdict === 'not-yet' && /pin PR #9110 is blocked: check-failing-after-retry: failing: Flux Local - Test \(main\) after a retry/.test(factOf(r8.rec(9101), 'pin').detail), 'N1: a blocked pin PR is not a failure: awaiting, with its class and reason', r8.rec(9101).why);
  const r8b = await run2(base(), [src], { deps: { records: [{ ...openPin, lane: 'LANE-B-AGENT', class: 'rule-agent' }] } });
  ok(r8b.rec(9101).verdict === 'not-yet' && /pin PR #9110 is open \(LANE-B-AGENT\/rule-agent\)/.test(factOf(r8b.rec(9101), 'pin').detail), 'own image: an open pin PR that is not blocked: still awaiting, named');
  const w4 = base(); w4.runs[S][0].status = 'in_progress';
  ok((await run2(w4, [src])).rec(9101).verdict === 'not-yet', 'own image: a build still running is not yet');
  const w5 = base(); w5.runs = {};
  const r5 = await run2(w5, [src]);
  ok(r5.rec(9101).verdict === 'not-yet' && /the run of .* could not be read/.test(factOf(r5.rec(9101), 'pin').detail), 'N1: the build unreadable keeps it awaiting (never failed), saying so', r5.rec(9101).why);
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

// ── 8. the practice bound, the memory, the cap, the fair order, the time budget ───────────
{
  const w = world(); w.installed = [{ sha: 'f'.repeat(40), at: '2026-11-13T09:30:00Z' }, { sha: '9'.repeat(40), at: '2026-11-13T03:12:30Z' }];
  const r = await tick(w, { onlyPrs: null });
  ok(r.summary.since === '2026-11-13T03:12:30.000Z' && /first commit on main that added night-verify\.mjs: 99999999/.test(r.summary.since_basis), 'bound: since is the oldest commit of night-verify.mjs on main (a fact in git)', JSON.stringify([r.summary.since, r.summary.since_basis]));
  ok(r.summary.evaluated.sort().join() === '4870,4873,4874', 'bound: only merges at or after it are practised (#4869 and #4871 merged before)', r.summary.evaluated.join());
  const r2 = await tick(world(), { onlyPrs: null });
  ok(r2.summary.evaluated.length === 0 && /unreadable: when the verifier was installed/.test(r2.summary.why_not) && r2.memory === null, 'bound: an unreadable install time practises nothing (never the backfill) and writes no memory');
  const r3 = await tick(world(), { onlyPrs: null, prev: r.memory });
  ok(r3.summary.since === r.summary.since && !r3.calls.some(c => /night-verify\.mjs/.test(decodeURIComponent(c))), 'bound: kept in the memory, not read again');
  // L3: a tick that cannot list the practice candidates keeps the open records and done
  const keep = await tick(world(), { onlyPrs: null, prev: JSON.stringify({ ...JSON.parse(r.memory), since: null, done: { x: 'verified 2026-11-13T00:00:00.000Z' } }) });
  const km0 = JSON.parse(keep.memory || '{"items":{},"done":{}}');
  ok(Object.values(km0.items).filter(x => !x.final).length === Object.values(JSON.parse(r.memory).items).filter(x => !x.final).length && km0.done.x && Object.values(km0.items).every(x => x.baseline !== undefined), 'L3: a tick that could not list the practice candidates (no install time) drops no open record and keeps done');
  const off = await tick(world(), { onlyPrs: null, prev: JSON.stringify({ ...JSON.parse(r.memory), done: { x: 'verified 2026-11-13T00:00:00.000Z' } }), cfg: { practice: false } });
  ok(JSON.parse(off.memory).done.x, 'L3: a tick with practice off keeps done');
  ok((await tick(world(), { onlyPrs: null, prev: r.memory, deps: {} })).memory !== null, 'memory: kept and written again on the next tick');
  const unread = await V.verifyPhase({ rules, run: fake(world()).run, repo: REPO, now: T1, clock: () => T1, prevText: r.memory, prevUnreadable: true, deps: { alertSubject: N.alertSubject, getJson: gjOf(world()) } });
  ok(unread.memory === null && /memory is unknown; nothing judged, the memory is kept/.test(unread.summary.why_not), 'L3: upgrade-lane-state unreadable: nothing judged, no memory written (the last one stays)');
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
  // the byte budget: an open record (with the pods recorded for stability) goes last
  const k = await tick(world(), { cfg: { max_state_bytes: 9000 } });
  const km = JSON.parse(k.memory), openKept = Object.values(km.items).filter(x => !x.final);
  ok(Buffer.byteLength(k.memory) <= 9000 && k.summary.trimmed === 1 && k.summary.compacted > 0 && openKept.length === 4 && openKept.every(x => x.baseline?.pods) && openKept.some(x => x.compacted),
    'memory over max_state_bytes: the final record goes first, then the open ones lose their details and keep their recorded pods', `${Buffer.byteLength(k.memory)} bytes ${JSON.stringify(k.summary)}`.slice(0, 400));
  ok(/\(facts and other changes cut to keep data\.verify within max_state_bytes/.test(k.table), 'memory: the morning view says when a record\'s details were cut');
  const k2 = await tick(world(), { cfg: { max_state_bytes: 9000 }, now: T2, prev: k.memory });
  ok([4869, 4870, 4873, 4874].every(n => k2.summary.verdicts.verified.includes(n)) && Buffer.byteLength(k2.memory) <= 9000, 'memory over budget: the next tick still verifies the open items against the kept pods', JSON.stringify(k2.summary.verdicts));
  const kc = await tick(world(), { cfg: { max_items_kept: 2 } });
  const kcm = JSON.parse(kc.memory);
  ok(Object.keys(kcm.items).length === 4 && Object.values(kcm.items).every(x => !x.final) && kc.summary.trimmed === 1, 'memory: max_items_kept drops final records only, never an open one', JSON.stringify(kc.summary));
  const big = { schema: 'haynes.upgrade-verify/v1', since: null, items: { open1: { pr: 1, merged_at: '2026-11-13T00:00:00Z', verdict: 'not-yet', why: 'x', facts: [{ fact: 'image', result: 'pass', detail: 'y'.repeat(800) }], baseline: { at: '2026-11-13T01:00:00Z', pods: { u1: { name: 'p', restarts: { app: 0 } } } }, final: false } },
    done: Object.fromEntries(Array.from({ length: 2000 }, (_, i) => [`renovate-pr:${i.toString(16).padStart(24, '0')}`, 'verified 2026-11-13T00:00:00.000Z'])) };
  const fm = V.fitMemory(big, { max_items_kept: 60, max_state_bytes: 9000 }, id => id === 'open1', id => (id === 'open1' ? '2026-11-13' : `2026-11-01-${id}`));
  ok(Buffer.byteLength(fm.text) <= 9000 && JSON.parse(fm.text).items.open1?.baseline?.pods?.u1 && fm.counts.done_trimmed > 0 && !fm.counts.trimmed_open, 'memory: done over the budget is trimmed, oldest first, before an open record is touched', JSON.stringify(fm.counts));
  ok(V.parseMemory('not json').ok === false && V.parseMemory(null).absent === true, 'memory: an unparseable data.verify starts a new one; none is absent');
  // L1: the time budget before each shared read
  const late = await tick(world(), { startedAt: T1 - 200e3 });
  ok(late.summary.evaluated.length === 0 && /time budget \(150 s of the tick\) was used before reading the Flux objects; nothing more this tick, the memory is kept/.test(late.summary.why_not) && late.memory === null && !late.calls.some(c => /kubectl get kustomizations|compare/.test(c)), 'L1: a phase that starts after time_budget_seconds reads nothing shared, judges nothing, keeps the memory', late.summary.why_not);
  const lateP = await tick(world(), { onlyPrs: null, startedAt: T1 - 200e3 });
  ok(/used before reading the install time/.test(lateP.summary.why_not) && lateP.calls.length === 0, 'L1: in practice it stops before the install-time read too');
  const f2 = fake({ ...world(), ledger: { metadata: { name: 'upgrade-ledger', resourceVersion: '5' }, data: { meta: 'not json', items: '{}', events: '' } } });
  const nh = await V.verifyPhase({ rules, run: f2.run, repo: REPO, now: T1, startedAt: T1, clock: () => T1, deps: { alertSubject: N.alertSubject, getJson: gjOf(world()) } });
  ok(nh.summary.evaluated.length === 0 && /the history could not be read/.test(nh.summary.why_not) && nh.memory === null, 'a history it cannot read: nothing judged, it says why, the memory is kept');
  ok(V.verifyLine(t2.summary).includes('verified #') && /\(\d+ GitHub\)/.test(V.verifyLine(t2.summary)) && V.verifyLine({ error: 'x' }) === 'FAILED: x', 'the log line, with the GitHub read count');
  // needs a person: a cannot-tell that stays open on the lane's nights
  const wp = world(); wp.alertsUnreadable = true;
  const c3 = { needs_person_min_ticks: 3 };
  const p1 = await tick(wp, { prs: [4869], cfg: c3 }), p2 = await tick(wp, { prs: [4869], now: T2, prev: p1.memory, doc: p1.doc, cfg: c3 }), p3 = await tick(wp, { prs: [4869], now: T1 + DAY, prev: p2.memory, doc: p1.doc, cfg: c3 });
  ok(!p2.rec(4869).needs_person && p3.rec(4869).needs_person === true && p3.rec(4869).nights.length === 2 && /given up: cannot-tell since .* over 2 night\(s\)/.test(p3.table), 'needs a person: a cannot-tell open on two of the lane\'s nights and three ticks (two ticks of one night count once)', JSON.stringify(p3.rec(4869).nights));
  // L-e: an item merged late and open at the first tick of the next night is not marked after two ticks
  const q1 = await tick(wp, { prs: [4869], now: Date.parse('2026-11-13T10:40:00Z') }), q2 = await tick(wp, { prs: [4869], now: Date.parse('2026-11-14T06:00:00Z'), prev: q1.memory, doc: q1.doc });
  ok(q2.rec(4869).nights.length === 2 && q2.rec(4869).ticks === 2 && !q2.rec(4869).needs_person, 'L-e: two local dates in two ticks are not enough (needs_person_min_ticks, 8 in git)', JSON.stringify([q2.rec(4869).nights, q2.rec(4869).ticks]));
}

// ── 9. the review of PR 4880 ─────────────────────────────────────────────────────────
{
  // H1: a short digest of digits only is still a digest (decided by the recorded update type)
  const D = '9014532' + 'a'.repeat(57);
  const v = { ...fx.prs.find(p => p.number === 4869), labels: [{ name: 'renovate/container' }, { name: 'type/digest' }], body: '| Package | Update | Change |\n|---|---|---|\n| ghcr.io/seerr-team/seerr | digest | `abcd123` → `9014532` |\n' };
  const w = world(); for (const p of podsIn(w, 'media', 'seerr-')) { p.spec.containers[0].image = `ghcr.io/seerr-team/seerr:v3.5.2@sha256:${D}`; p.status.containerStatuses[0].imageID = `ghcr.io/seerr-team/seerr@sha256:${D}`; }
  const h1 = (await twice(w, { prs: [4869], views: [v] })).b;
  ok(h1.rec(4869).type === 'digest' && h1.rec(4869).verdict === 'verified' && /run ghcr\.io\/seerr-team\/seerr 9014532/.test(factOf(h1.rec(4869), 'image').detail), 'H1: a digest update to 9014532 (digits only) is read as a digest and verified, not a wrong image', h1.rec(4869).why);
  const w2 = world(); for (const p of podsIn(w2, 'media', 'seerr-')) { p.spec.containers[0].image = `ghcr.io/seerr-team/seerr:v3.5.2@sha256:${'1'.repeat(64)}`; p.status.containerStatuses[0].imageID = `ghcr.io/seerr-team/seerr@sha256:${'1'.repeat(64)}`; }
  ok((await tick(w2, { prs: [4869], views: [v] })).rec(4869).failing?.[0]?.class === 'wrong-image', 'H1: another digest is a wrong image');
  ok(V.versionKindOf('digest') === 'digest' && V.versionKindOf('pinDigest') === 'digest' && V.versionKindOf('minor') === 'tag' && V.versionKindOf('minor+digest') === 'either' && V.versionKindOf(null) === 'either', 'H1: the version kind comes from the update type');
  // M1: a target of CronJobs only is exercised by a Job from the new template
  const cjWorld = (jobs, image = 'ghcr.io/example/backup-tool:1.1.0') => {
    const w = world();
    w.ks = { items: [...w.ks.items, { metadata: { name: 'backup-tool', namespace: 'media', generation: 1, labels: { 'kustomize.toolkit.fluxcd.io/name': 'cluster-apps', 'kustomize.toolkit.fluxcd.io/namespace': 'flux-system' } }, spec: { path: './kubernetes/main/apps/media/backup-tool/app', sourceRef: { kind: 'GitRepository', name: 'haynes-ops', namespace: 'flux-system' } },
      status: { observedGeneration: 1, lastAppliedRevision: w.source.status.artifact.revision, lastAttemptedRevision: w.source.status.artifact.revision, conditions: [{ type: 'Ready', status: 'True' }], inventory: { entries: [{ id: 'media_backup-tool_batch_CronJob', v: 'v1' }] } } }] };
    w.pods.media.cronjobs = { items: [{ metadata: { name: 'backup-tool', namespace: 'media' }, spec: { schedule: '0 3 * * *', timeZone: 'America/New_York', jobTemplate: { spec: { template: { spec: { containers: [{ name: 'tool', image }] } } } } }, status: { lastScheduleTime: '2026-11-12T08:00:00Z' } }] };
    w.pods.media.jobs = { items: jobs };
    w.extraCommits = [{ sha: 'b9'.repeat(20), at: '2026-11-13T06:00:00Z', subject: 'fix(container): update image ghcr.io/example/backup-tool to v1.1.0', pr: 9300, author: 'renovate[bot]', files: ['kubernetes/main/apps/media/backup-tool/app/cronjob.yaml'] }];
    w.source.status.artifact.revision = `main@sha1:${'b9'.repeat(20)}`; // Flux fetched it, and the targets applied it
    for (const k of w.ks.items) { k.status.lastAppliedRevision = w.source.status.artifact.revision; k.status.lastAttemptedRevision = w.source.status.artifact.revision; }
    return w;
  };
  const cjView = { number: 9300, state: 'MERGED', title: 'fix(container): update image ghcr.io/example/backup-tool to v1.1.0', author: { login: 'app/renovate' }, mergedAt: '2026-11-13T06:00:00Z', mergedBy: { login: 'app/renovate' }, mergeCommit: { oid: 'b9'.repeat(20) }, labels: [{ name: 'type/minor' }], body: '| Package | Update | Change |\n|---|---|---|\n| ghcr.io/example/backup-tool | minor | `1.0.0` → `1.1.0` |\n', files: [{ path: 'kubernetes/main/apps/media/backup-tool/app/cronjob.yaml' }], changedFiles: 1 };
  const job = (name, made, cond, image = 'ghcr.io/example/backup-tool:1.1.0') => ({ metadata: { name, namespace: 'media', creationTimestamp: made, ownerReferences: [{ kind: 'CronJob', name: 'backup-tool' }] }, spec: { template: { spec: { containers: [{ name: 'tool', image }] } } }, status: { conditions: cond ? [{ type: cond, status: 'True', reason: cond === 'Failed' ? 'BackoffLimitExceeded' : undefined }] : [] } });
  const m1a = await tick(cjWorld([job('backup-tool-old', '2026-11-12T08:00:00Z', 'Complete', 'ghcr.io/example/backup-tool:1.0.0')]), { prs: [9300], views: [cjView] });
  ok(m1a.rec(9300).verdict === 'not-yet' && /no Job from the new template since the merge; waiting for its first run \(schedule 0 3 \* \* \* America\/New_York, last scheduled 2026-11-12T08:00:00Z, next 2026-11-14T08:00:00\.000Z\)/.test(factOf(m1a.rec(9300), 'exercised').detail), 'M1: a CronJob not run since the merge is not yet, with its next run', factOf(m1a.rec(9300), 'exercised').detail);
  const m1b = await tick(cjWorld([job('backup-tool-new', '2026-11-13T08:00:00Z', 'Complete')]), { prs: [9300], views: [cjView] });
  ok(m1b.rec(9300).verdict === 'verified' && factOf(m1b.rec(9300), 'exercised').result === 'pass', 'M1: a Job from the new template completed: verified', m1b.rec(9300).why);
  const m1c = await tick(cjWorld([job('backup-tool-new', '2026-11-13T08:00:00Z', 'Failed')]), { prs: [9300], views: [cjView] });
  ok(m1c.rec(9300).verdict === 'failed' && m1c.rec(9300).failing[0].class === 'job-failed', 'M1: a Job from the new template failed: failed', m1c.rec(9300).why);
  const m1d = await tick(cjWorld([], 'ghcr.io/example/backup-tool:1.0.0'), { prs: [9300], views: [cjView] });
  ok(m1d.rec(9300).verdict === 'failed' && m1d.rec(9300).failing[0].class === 'wrong-image', 'M5: a CronJob template naming another version does not pass', m1d.rec(9300).why);
  const wj = cjWorld([]); wj.fail = { jobs: true };
  ok((await tick(wj, { prs: [9300], views: [cjView] })).rec(9300).verdict === 'cannot-tell', 'M1: Jobs unreadable is cannot tell');
  // M2: two reads must be at least min_stable_seconds apart
  const a = await tick(world(), { prs: [4869] }), b = await tick(world(), { prs: [4869], now: T1 + 1000, prev: a.memory, doc: a.doc });
  ok(b.rec(4869).verdict === 'not-yet' && /recorded at .*, 1 s ago; they are read again at least 600 s after that/.test(factOf(b.rec(4869), 'stable').detail), 'M2: two reads a second apart are not yet, saying how long ago the pods were recorded', factOf(b.rec(4869), 'stable').detail);
  const same = await tick(world(), { prs: [4869], now: T1, prev: a.memory, doc: a.doc });
  ok(same.rec(4869).verdict === 'not-yet' && factOf(same.rec(4869), 'stable').result === 'waiting', 'M5: no stability within the same tick');
  // M3: attribution by container, nodes, Prometheus restarts
  const side = w => { for (const p of podsIn(w, 'ai', 'piper-')) { p.spec.containers.push({ name: 'exporter', image: 'ghcr.io/example/exporter:1' }); p.status.containerStatuses.push({ name: 'exporter', ready: true, restartCount: 0, state: { running: {} } }); } };
  const wc = world(); side(wc); podsIn(wc, 'ai', 'piper-')[0].status.containerStatuses[1].state = { waiting: { reason: 'CrashLoopBackOff' } };
  const rc = await tick(wc, { prs: [4870] });
  ok(rc.rec(4870).verdict === 'cannot-tell' && /container exporter is CrashLoopBackOff.*not one of the update's containers/.test(factOf(rc.rec(4870), 'pods').detail), 'M3: a crash loop of another container in the pod is cannot tell, named', rc.rec(4870).why);
  const wr = world(); side(wr); const wr2 = structuredClone(wr); podsIn(wr2, 'ai', 'piper-')[0].status.containerStatuses[1].restartCount = 2;
  const rr = (await twice(wr, { prs: [4870] }, wr2)).b;
  ok(rr.rec(4870).verdict === 'cannot-tell' && /container exporter restarted: 0 at .*, 2 now \(not one of the update's containers/.test(factOf(rr.rec(4870), 'stable').detail), 'M3: a restart of another container is cannot tell, named', rr.rec(4870).why);
  const wk = world(); placePods(wk, 'ai', 'piper-', 'talosw09'); wk.nodes = { items: [{ metadata: { name: 'talosw09' }, status: { conditions: [{ type: 'Ready', status: 'Unknown' }] } }] };
  const wk2 = structuredClone(wk); { const p = podsIn(wk2, 'ai', 'piper-')[0]; p.status.conditions = [{ type: 'Ready', status: 'False' }]; p.status.containerStatuses[0].ready = false; }
  const rk = (await twice(wk, { prs: [4870] }, wk2)).b;
  ok(rk.rec(4870).verdict === 'cannot-tell' && /its node talosw09 is not Ready: cannot tell/.test(factOf(rk.rec(4870), 'stable').detail), 'M3: a recorded pod not Ready on a node that is not Ready is cannot tell', rk.rec(4870).why);
  const restartAlert = w => w.alerts.data.alerts.push({ labels: { alertname: 'OOMKilled', severity: 'critical', namespace: 'media', pod: seerrPod }, state: 'firing', activeAt: '2026-11-13T10:00:30Z' });
  const wpr = world(); wpr.promStart = '2026-11-13T10:00:00Z'; restartAlert(wpr);
  const rpr = await tick(wpr, { prs: [4869] });
  ok(rpr.rec(4869).verdict === 'cannot-tell' && /within 300 s of Prometheus's start at 2026-11-13T10:00:00\.000Z, after the merge: cannot tell it from the restart/.test(factOf(rpr.rec(4869), 'alerts').detail), 'M3: an alert that starts 30 s after a Prometheus restart is cannot tell, not new', factOf(rpr.rec(4869), 'alerts').detail);
  const wps = world(); wps.promStart = null; restartAlert(wps);
  ok((await tick(wps, { prs: [4869] })).rec(4869).verdict === 'cannot-tell', 'M3: with Prometheus\'s start unreadable a new alert is cannot tell');
  // M4: a Pending pod is cannot tell with the scheduler's reason
  const wpe = world(); { const p = podsIn(wpe, 'media', 'seerr-')[0]; p.status.phase = 'Pending'; p.status.conditions = [{ type: 'PodScheduled', status: 'False', reason: 'Unschedulable', message: '0/9 nodes are available: insufficient memory' }]; }
  const rpe = await tick(wpe, { prs: [4869] });
  ok(rpe.rec(4869).verdict === 'cannot-tell' && /Pending: Unschedulable: 0\/9 nodes are available/.test(factOf(rpe.rec(4869), 'pods').detail), 'M4: a Pending pod is cannot tell with the scheduler\'s reason', rpe.rec(4869).why);
  const wcc = world(); podsIn(wcc, 'media', 'seerr-')[0].status.containerStatuses[0].state = { waiting: { reason: 'CreateContainerConfigError' } };
  const rcc = await tick(wcc, { prs: [4869] });
  ok(rcc.rec(4869).verdict === 'not-yet' && /CreateContainerConfigError.*since .*fails if it still holds 600 s after that/.test(factOf(rcc.rec(4869), 'pods').detail), 'M4: a start error on the first read is not yet, saying since when', rcc.rec(4869).why);
  // M5: crash loops in init containers are read
  const wi = world(); { const p = podsIn(wi, 'ai', 'piper-')[0]; p.spec.initContainers = [{ name: 'fetch-voices', image: 'ghcr.io/example/fetch:1' }]; p.status.initContainerStatuses = [{ name: 'fetch-voices', ready: false, restartCount: 3, state: { waiting: { reason: 'CrashLoopBackOff' } } }]; }
  const ri = await tick(wi, { prs: [4870] });
  ok(ri.rec(4870).verdict === 'cannot-tell' && /init container fetch-voices is CrashLoopBackOff/.test(factOf(ri.rec(4870), 'pods').detail), 'M5: a crash loop in an init container is read (another container: cannot tell)', ri.rec(4870).why);
  // M5: stability is not read when the image fails
  const wf = world(); const wf2 = structuredClone(wf); for (const p of podsIn(wf2, 'media', 'seerr-')) p.spec.containers[0].image = 'ghcr.io/seerr-team/seerr:v3.5.1';
  const rf = (await twice(wf, { prs: [4869] }, wf2)).b;
  ok(rf.rec(4869).verdict === 'failed' && factOf(rf.rec(4869), 'stable').result === 'waiting' && /read once the image, the rollout and the pods pass/.test(factOf(rf.rec(4869), 'stable').detail), 'M5: stability is not read when the image fails', factOf(rf.rec(4869), 'stable').detail);
  // L5: an image used only in an init container is found; a native sidecar's restarts count
  const wl5 = world(); { const p = podsIn(wl5, 'ai', 'piper-')[0]; p.spec.initContainers = [{ name: 'piper-init', image: 'rhasspy/wyoming-piper:2.5.3', restartPolicy: 'Always' }]; p.status.initContainerStatuses = [{ name: 'piper-init', ready: true, restartCount: 0, imageID: 'x', state: { running: {} } }]; p.spec.containers[0].image = 'ghcr.io/example/other:1'; }
  const wl52 = structuredClone(wl5); podsIn(wl52, 'ai', 'piper-')[0].status.initContainerStatuses[0].restartCount = 1;
  const l5a = (await twice(wl5, { prs: [4870] })).b;
  ok(l5a.rec(4870).verdict === 'verified' && /1 container\(s\) in ai\/piper run rhasspy\/wyoming-piper 2\.5\.3/.test(factOf(l5a.rec(4870), 'image').detail), 'L5: an image used only in an init container (a native sidecar) is found', factOf(l5a.rec(4870), 'image').detail);
  const l5b = (await twice(wl5, { prs: [4870] }, wl52)).b;
  ok(l5b.rec(4870).failing?.[0]?.class === 'restart-growth' && /container piper-init restarted/.test(factOf(l5b.rec(4870), 'stable').detail), 'L5: a native sidecar of the update that restarts fails as restart growth', l5b.rec(4870).why);
  // N3: a restart on a node that rebooted, or that was not Ready since the record, is not the update's
  const reboot = (boot, since) => { const w = world(); w.nodes = { items: [READY_NODE('talosw01', boot, since), READY_NODE('talosw02')] }; podsIn(w, 'ai', 'piper-')[0].status.containerStatuses[0].restartCount = 1; return w; };
  const nb1 = await tick(world(), { prs: [4870] });
  const nb2 = await tick(reboot('boot-2'), { prs: [4870], now: T2, prev: nb1.memory, doc: nb1.doc });
  ok(nb2.rec(4870).verdict === 'not-yet' && /container app restarted: 0 at .*, 1 now, on node talosw01, which rebooted \(its boot id changed\): not the update's; the pods are recorded again/.test(factOf(nb2.rec(4870), 'stable').detail), 'N3: a restart on a node whose boot id changed is recorded again, not failed', nb2.rec(4870).why);
  const nb3 = await tick(reboot('boot-2'), { prs: [4870], now: T3, prev: nb2.memory, doc: nb1.doc });
  ok(nb3.rec(4870).verdict === 'verified', 'N3: after the node reboot the new record soaks and verifies', nb3.rec(4870).why);
  const nr2 = await tick(reboot('boot-1', iso(T1 + 300e3)), { prs: [4870], now: T2, prev: nb1.memory, doc: nb1.doc });
  ok(nr2.rec(4870).verdict === 'not-yet' && /which was not Ready since the record/.test(factOf(nr2.rec(4870), 'stable').detail), 'N3: a restart on a node that was not Ready since the record is recorded again', nr2.rec(4870).why);
  const wnn = reboot('boot-1'); wnn.nodes = null;
  ok((await tick(wnn, { prs: [4870], now: T2, prev: nb1.memory, doc: nb1.doc })).rec(4870).verdict === 'cannot-tell', 'N3: a restart whose node cannot be read is cannot tell');
  // N3: once stable has passed it stays passed for the item (an item judged again, here a named PR)
  const sp2 = (await twice(world(), { prs: [4870] })).b;
  const wl3 = world(); podsIn(wl3, 'ai', 'piper-')[0].status.containerStatuses[0].restartCount = 5;
  const sp3 = await tick(wl3, { prs: [4870], now: T3, prev: sp2.memory, doc: sp2.doc });
  ok(sp2.rec(4870).verdict === 'verified' && sp3.rec(4870).verdict === 'verified' && /passed at .*; not read again: health after the soak is the alerting's business/.test(factOf(sp3.rec(4870), 'stable').detail), 'N3: stable passed stays passed; a later restart is the alerting\'s business', sp3.rec(4870).why);
  // L-b: a CronJob template whose version cannot be read for an update of unknown type is unreadable, not a wrong image
  const lbView = { ...cjView, labels: [], body: '| Package | Update | Change |\n|---|---|---|\n| ghcr.io/example/backup-tool |  | `1234abc` → `abcd123` |\n' };
  const lb = await tick(cjWorld([], 'ghcr.io/example/backup-tool:1.1.0'), { prs: [9300], views: [lbView] });
  ok(lb.rec(9300).verdict === 'cannot-tell' && /the version CronJob media\/backup-tool names .*either update/.test(factOf(lb.rec(9300), 'image').detail), 'L-b: an unknown-type update and a tag-only template: cannot tell, not a wrong image', lb.rec(9300).why);
  // L-c: the newest finished Job decides; a running newer one does not hide it; failing before the merge; suspended
  const running = job('backup-tool-newer', '2026-11-13T09:00:00Z', null);
  const lc1 = await tick(cjWorld([job('backup-tool-new', '2026-11-13T08:00:00Z', 'Complete'), running]), { prs: [9300], views: [cjView] });
  ok(lc1.rec(9300).verdict === 'verified' && /backup-tool-new from the new template completed/.test(factOf(lc1.rec(9300), 'exercised').detail), 'L-c: a newer Job still running does not hide the finished one', lc1.rec(9300).why);
  const lc2 = await tick(cjWorld([job('backup-tool-old', '2026-11-12T08:00:00Z', 'Failed', 'ghcr.io/example/backup-tool:1.0.0'), job('backup-tool-new', '2026-11-13T08:00:00Z', 'Failed')]), { prs: [9300], views: [cjView] });
  ok(lc2.rec(9300).verdict === 'cannot-tell' && /failed too: it was failing before the merge/.test(factOf(lc2.rec(9300), 'exercised').detail), 'L-c: a new Job that fails where the old template failed too is cannot tell', lc2.rec(9300).why);
  const wsus = cjWorld([]); wsus.pods.media.cronjobs.items[0].spec.suspend = true;
  const lc3 = await tick(wsus, { prs: [9300], views: [cjView] });
  ok(lc3.rec(9300).verdict === 'cannot-tell' && /is suspended: no run comes until someone resumes it/.test(factOf(lc3.rec(9300), 'exercised').detail) && !/next /.test(factOf(lc3.rec(9300), 'exercised').detail), 'L-c: a suspended CronJob says suspended, not a next run', lc3.rec(9300).why);
  // L-h: a Job from the new template made before the merge does not count
  const lh = await tick(cjWorld([job('backup-tool-early', '2026-11-13T05:00:00Z', 'Complete')]), { prs: [9300], views: [cjView] });
  ok(lh.rec(9300).verdict === 'not-yet' && /no Job from the new template since the merge/.test(factOf(lh.rec(9300), 'exercised').detail), 'L-h: a Job made before the merge does not count', lh.rec(9300).why);
  // L-e: a record forgotten because the history no longer holds it is named in the summary
  const ghost = JSON.stringify({ schema: 'haynes.upgrade-verify/v1', since: null, items: { 'renovate-pr:000000000000000000000abc': { pr: 4000, merged_at: '2026-11-12T00:00:00Z', verdict: 'not-yet', final: false } }, done: {} });
  const fg = await tick(world(), { prs: [4869], prev: ghost });
  ok(fg.summary.forgotten?.some(x => x.pr === 4000 && /no longer holds it/.test(x.why)) && /forgotten #4000/.test(V.verifyLine(fg.summary)), 'L-e: a record the history no longer holds is named when it is forgotten', JSON.stringify(fg.summary.forgotten));
  // L-g, L-h: the time budget inside the commit-range read, and before it
  const budgetRun = async (stepMs, startLeftMs, deps0) => {
    const w = world(); let t = T1; const f = fake(w);
    const run = (cmd, argv, o) => { t += stepMs; return f.run(cmd, argv, o); };
    const r = await V.verifyPhase({ rules, run, repo: REPO, now: T1, startedAt: T1 - (150e3 - startLeftMs), clock: () => t, onlyPrs: PRS, deps: { doc: V.docOf(views(PRS), { repo: REPO, rules, at: iso(T1) }), alertSubject: N.alertSubject, getJson: gjOf(w), ...deps0 } });
    return { r, calls: f.calls };
  };
  const inRange = await budgetRun(5e3, 12e3, { ks: fx.ks, hr: fx.hr, source: fx.source });
  ok(/before the end of the commit-range read \(it stopped inside the read\)/.test(inRange.r.summary.why_not) && inRange.r.memory === null && inRange.calls.filter(c => /commits\//.test(c)).length <= 2, 'L-g: the budget runs out inside the range read: it stops there, judges nothing, keeps the memory', `${inRange.r.summary.why_not} ${inRange.calls.length}`);
  const beforeRange = await budgetRun(130e3, 120e3, { hr: fx.hr, source: fx.source });
  ok(/before reading the commits on main/.test(beforeRange.r.summary.why_not) && !beforeRange.calls.some(c => /compare/.test(c)), 'L-h: the budget used by the Flux read stops it before the range read starts', beforeRange.r.summary.why_not);
  ok(V.nextCron('*/15 1-5 * * *', 'America/New_York', Date.parse('2026-11-13T10:50:00Z')) === '2026-11-14T06:00:00.000Z' && V.nextCron('bad', 'UTC', 0) === null, 'nextCron: the lane\'s own schedule; an expression it cannot read is null');
}

// ── 10. the image and version readers ──────────────────────────────────────────────────
{
  const p = V.parseImage('docker.io/library/traefik:v3.7.18@sha256:' + 'a'.repeat(64));
  ok(p.repo === 'traefik' && p.tag === 'v3.7.18' && p.digest === `sha256:${'a'.repeat(64)}`, 'parseImage: docker.io/library dropped, tag and digest read');
  ok(V.parseImage('registry.example:5000/x/y').tag === null && V.parseImage('registry.example:5000/x/y').repo === 'registry.example:5000/x/y', 'parseImage: a registry port is not a tag');
  ok(V.normRepo('rhasspy/wyoming-piper') === V.parseImage('docker.io/rhasspy/wyoming-piper:2.5.3').repo, 'normRepo: a Docker Hub short name matches its docker.io form');
  ok(V.versionMatches({ tag: 'v3.5.2' }, 'v3.5.2', 'tag') === true && V.versionMatches({ tag: '3.5.2' }, 'v3.5.2', 'tag') === true && V.versionMatches({ tag: '3.5.1' }, '3.5.2', 'tag') === false, 'versionMatches: tags, a leading v ignored');
  ok(V.versionMatches({ tag: '0.2.0', digest: `sha256:${'d4'.repeat(32)}` }, 'd4d4d4d', 'digest') === true && V.versionMatches({ tag: '0.2.0', image_id: `x@sha256:${'e5'.repeat(32)}` }, 'd4d4d4d', 'digest') === false && V.versionMatches({ tag: '0.2.0' }, 'd4d4d4d', 'digest') === null, 'versionMatches: a digest update compares the digest; none to read is cannot tell');
  ok(V.versionMatches({ tag: '9014532', digest: null }, '9014532', 'digest') === null && V.versionMatches({ tag: '1.0.0' }, '9014532', 'either') === null && V.versionMatches({ tag: '1.0.0' }, 'v1.0.0', 'either') === true, 'versionMatches: a digest update never matches by tag; an unknown type matches by either reading');
  ok(V.versionMatches({ tag: null }, '1.0.0') === null && V.versionMatches({ tag: '1.0.0' }, null) === null, 'versionMatches: no tag or no version is cannot tell');
  ok(V.inventoryOf({ status: {} }) === null && V.inventoryOf({ status: { inventory: { entries: [{ id: 'media_seerr_apps_Deployment' }, { id: '_x_rbac.authorization.k8s.io_ClusterRole' }] } } }).map(e => `${e.ns}/${e.name}/${e.group}/${e.kind}`).join() === 'media/seerr/apps/Deployment,/x/rbac.authorization.k8s.io/ClusterRole', 'inventoryOf: Flux ids, cluster-scoped ones too');
  ok(V.buildOf(null, 'x') === null && V.buildOf([], 'x').state === 'none' && V.buildOf([{ path: 'x', status: 'completed', conclusion: 'success', created_at: '1', updated_at: '2026-01-01T00:00:00Z' }], 'x').state === 'success', 'buildOf: the build run for a commit');
}

console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
