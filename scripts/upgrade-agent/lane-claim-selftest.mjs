#!/usr/bin/env node
// Self-test for "who handles a regression" (story 554): the night lane's claim
// (night-claim.mjs), triage's side (triage.sh lane_hold, state_claim, state_upsert) and the
// health gate's reading of a claim (gate.sh lane_phase), against a fake API server with real
// preconditions (test-fake-cluster.mjs: a replace at a stale resourceVersion is a Conflict, a
// create of an existing object AlreadyExists). No cluster, no network.
//
// WHY THIS EXISTS
// ---------------
// Exactly one of the lane and triage may act on a regression: two would mean two reverts (or a
// revert and an LLM remediation) of one change, two escalations and two pages. And a claim must
// never silence anyone for ever. Pinned below:
//   1. the signature: one fixture (real objects, fixtures/claim-signature-20261113.json) run
//      through coordination-lib.sh as triage and the gate run it, and through the lane's
//      coordinationSignature(), which runs the same library: the same signature; which ids
//      belong to the lane's target;
//   2. the claim format: what the lane writes is what triage and the gate read;
//   3. triage claims first: the lane stops, does not revert and records that triage owns it;
//   4. the lane claims first: triage does not summon, writes nothing over it, and says so;
//   5. both at once, in either order: exactly one holder; the loser reads the winner;
//   6. an unreadable claim: the lane does not revert; the gate pages as today;
//   7. the gate: silent on a working lane claim, pages on "revert did not verify" and on a
//      claim past its own bound; a claim it cannot understand is read as today;
//   8. dry-run and practice write nothing to upgrade-remediation-state;
//   9. the one write site refuses anything else; run-night.mjs wires the step after the
//      verifier and only writes in a live tick for the lane's own merge.
//
// Usage: node scripts/upgrade-agent/lane-claim-selftest.mjs
// Requires: node 24, bash, jq, sha256sum. No network, no cluster access.
import { readFileSync, writeFileSync, mkdtempSync, mkdirSync, rmSync, existsSync, chmodSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../..');
const RES = join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/resources');
const GATE_RES = join(ROOT, 'kubernetes/main/apps/upgrade-agent/health-gate/app/resources');
const LIB = join(GATE_RES, 'coordination-lib.sh');
const C = await import(join(RES, 'night-claim.mjs'));
const L = await import(join(RES, 'lane-classify.mjs'));
const M = await import(join(RES, 'night-merge.mjs'));
const FX_PATH = join(ROOT, 'scripts/upgrade-agent/fixtures/claim-signature-20261113.json');
const fx = JSON.parse(readFileSync(FX_PATH, 'utf8'));
const NOW = Date.parse(fx.now);
const NOW_S = Math.floor(NOW / 1000);

let passes = 0, fails = 0;
const ok = (cond, name, detail = '') => { if (cond) { passes++; console.log(`ok    ${name}`); } else { fails++; console.log(`FAIL  ${name}${detail ? `: ${detail}` : ''}`); } };
const section = s => console.log(`\n== ${s}`);

// ── the fake cluster on PATH ─────────────────────────────────────────────────────────
const TMP = mkdtempSync(join(tmpdir(), 'lane-claim-'));
const BIN = join(TMP, 'bin');
mkdirSync(BIN);
for (const tool of ['kubectl', 'curl']) {
  writeFileSync(join(BIN, tool), `#!/usr/bin/env bash\nexec node ${JSON.stringify(join(ROOT, 'scripts/upgrade-agent/test-fake-cluster.mjs'))} ${tool} "$@"\n`);
  chmodSync(join(BIN, tool), 0o755);
}
let caseN = 0;
function world({ cms = {}, flux = fx.flux, prom = fx.prom, unreadable = false } = {}) {
  const dir = join(TMP, `case${++caseN}`);
  mkdirSync(dir);
  const state = join(dir, 'state.json'), fixture = join(dir, 'fixture.json'), promf = join(dir, 'prom.json'), log = join(dir, 'kube.log');
  let rv = 100;
  const configmaps = {};
  for (const [name, data] of Object.entries(cms)) configmaps[`upgrade-agent/${name}`] = { apiVersion: 'v1', kind: 'ConfigMap', metadata: { name, namespace: 'upgrade-agent', resourceVersion: String(++rv) }, data };
  writeFileSync(state, JSON.stringify({ rv, configmaps }));
  writeFileSync(fixture, JSON.stringify({ flux, pods: fx.pods, jobs: {} }));
  writeFileSync(promf, JSON.stringify(prom));
  writeFileSync(log, '');
  const env = { ...process.env, PATH: `${BIN}:${process.env.PATH}`, FAKE_KUBE_STATE: state, FAKE_KUBE_FIXTURE: fixture, FAKE_PROM: promf, FAKE_KUBE_LOG: log, FAKE_KUBE_UNREADABLE: unreadable ? '1' : '', TZ: 'UTC' };
  const cm = name => JSON.parse(readFileSync(state, 'utf8')).configmaps[`upgrade-agent/${name}`] || null;
  const entry = sig => { const c = cm('upgrade-remediation-state'); try { return JSON.parse(c?.data?.[sig] ?? 'null'); } catch { return 'UNPARSEABLE'; } };
  const writes = () => readFileSync(log, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l)).filter(a => a.some(x => ['replace', 'create', 'apply', 'patch'].includes(x)) && !a.includes('--dry-run=client'));
  return { dir, env, state, cm, entry, writes };
}
const run = env => (cmd, argv, o = {}) => L.runReadOnly(cmd, argv, { env, ...o });
const laneDeps = (w, sig) => ({
  signature: () => sig,
  read: () => M.readNamedConfigMap(C.STATE.name, run(w.env)),
  write: (verb, manifest) => C.writeState(verb, manifest, w.env),
});
const harness = (w, args, extraEnv = {}) => spawnSync('bash', [join(ROOT, 'scripts/upgrade-agent/test-triage-harness.sh'), ...args], { env: { ...w.env, NOW: String(NOW_S), ...extraEnv }, encoding: 'utf8' });
const WORK = { ok: true, max_work_seconds: 10800 };
const REC = fx.record;

try {
  // ── 1. the signature, through both ────────────────────────────────────────────────
  section('1. one regression, one signature: coordination-lib.sh as triage runs it, and the lane');
  {
    const w = world();
    const triageSide = spawnSync('bash', ['-c', `set -u; . ${JSON.stringify(LIB)}; NOW=${NOW_S}; PROM=http://prometheus.test:9090; collect_regressions; printf '%s\\n' "$(sig_of "$(sig_key_of "$REG_IDS")")"; printf '%s\\n' "$REG_IDS"`], { env: w.env, encoding: 'utf8' });
    const [tSig, ...tIds] = triageSide.stdout.trim().split('\n');
    const lane = C.coordinationSignature({ now: NOW, prom: 'http://prometheus.test:9090', env: w.env, lib: LIB });
    ok(triageSide.status === 0 && /^[0-9a-f]{12}$/.test(tSig), 'triage side: the library computes a 12-hex signature', `${triageSide.status} ${triageSide.stderr.slice(0, 200)}`);
    ok(lane.ok && lane.sig === tSig, `the lane computes the same signature (${lane.sig} = ${tSig})`, JSON.stringify(lane).slice(0, 300));
    ok(JSON.stringify(lane.ids) === JSON.stringify(tIds), 'and the same identifier list', `${lane.ids} vs ${tIds}`);
    ok(lane.ids.includes('flux/HelmRelease/media/seerr') && lane.ids.includes('flux/Kustomization/media/seerr') && lane.ids.includes('pod/media/seerr-bb94554fd-qd4fm') && lane.ids.includes('pod/ai/kokoro-6dcbb966bf-c8z88'),
      'the set: seerr\'s Kustomization, HelmRelease and pod, and the unrelated kokoro pod', lane.ids.join(', '));
    ok(!lane.ids.some(i => /h7-metadata-audit/.test(i)) && !lane.ids.some(i => /traefik/.test(i)), 'the library\'s own rules hold: the held H7 audit is dropped, a Ready HelmRelease is not in the set');
    // the key is the Flux lines only (sig_key_of, #3182): the same as hashing them alone
    const fluxOnly = spawnSync('bash', ['-c', `. ${JSON.stringify(LIB)}; sig_of "$(printf 'flux/HelmRelease/media/seerr\\nflux/Kustomization/media/seerr')"`], { encoding: 'utf8' }).stdout.trim();
    ok(lane.sig === fluxOnly, 'with Flux ids present the signature is over the Flux lines only (the pods churn)');
    const mine = C.targetIds(lane.ids, REC);
    ok(JSON.stringify(mine) === JSON.stringify(['flux/HelmRelease/media/seerr', 'flux/Kustomization/media/seerr', 'pod/media/seerr-bb94554fd-qd4fm']), 'the ids of #4869\'s target: its Kustomization, HelmRelease and pod, not kokoro', mine.join(', '));
    // pods only: the key is the pods' workloads
    const w2 = world({ flux: fx.flux_healthy });
    const t2 = spawnSync('bash', ['-c', `. ${JSON.stringify(LIB)}; NOW=${NOW_S}; PROM=x; collect_regressions; sig_of "$(sig_key_of "$REG_IDS")"`], { env: w2.env, encoding: 'utf8' }).stdout.trim();
    const l2 = C.coordinationSignature({ now: NOW, prom: 'x', env: w2.env, lib: LIB });
    ok(l2.ok && l2.sig === t2 && l2.sig !== lane.sig && l2.ids.length === 2, `pods only: the same signature on both sides (${l2.sig}), keyed by workload`);
    // empty set: no signature
    const w3 = world({ flux: fx.flux_healthy, prom: { pods: { status: 'success', data: { result: [] } } } });
    const l3 = C.coordinationSignature({ now: NOW, prom: 'x', env: w3.env, lib: LIB });
    ok(l3.ok && l3.sig === null && l3.ids.length === 0 && l3.pods === 'ok', 'a healthy cluster: no ids, no signature');
    // Prometheus unreadable: the pods are blind, as for triage; the Flux part still keys it
    const w4 = world({ prom: { pods: null } });
    const l4 = C.coordinationSignature({ now: NOW, prom: 'x', env: w4.env, lib: LIB });
    ok(l4.ok && l4.pods === 'blind' && l4.sig === lane.sig, 'Prometheus blind: pods dimension blind, the same Flux-keyed signature');
    // the library missing: no signature, the lane does not revert
    const l5 = C.coordinationSignature({ now: NOW, prom: 'x', env: w.env, lib: join(TMP, 'missing.sh') });
    ok(!l5.ok && /could not be read/.test(l5.why), 'the library unreadable: no signature');
    const s5 = C.claimStep({ rec: REC, live: true, now: NOW, work: WORK, deps: { signature: () => l5, read: () => { throw new Error('no read'); }, write: () => { throw new Error('no write'); } } });
    ok(s5.outcome === 'unreadable' && !s5.revert_allowed && !s5.wrote, 'and the step stops: unreadable, no revert');
    ok(!C.parseSignatureOutput('PODS ok\nID flux/x/y/z\n').ok, 'output with ids but no signature is refused');
    ok(C.SIG_SCRIPT.includes('collect_regressions') && C.SIG_SCRIPT.includes('sig_of "$(sig_key_of "$REG_IDS")"') && !/\$\{?(PR|TITLE|NAME)/.test(C.SIG_SCRIPT), 'the script is fixed: the library\'s own calls, no outside text');
  }
  const SIG = C.coordinationSignature({ now: NOW, prom: 'x', env: world().env, lib: LIB });
  const MINE = C.targetIds(SIG.ids, REC);

  // ── 2. the claim format ───────────────────────────────────────────────────────────
  section('2. the lane writes the claim format triage and the gate read');
  {
    const e = C.laneEntry({ prev: { last_paged: '1794560000', first_seen: '1794000000', note: 'old' }, pr: 4869, item: 'x', mine: MINE, now: NOW, workSeconds: 10800, note: 'night lane: #4869\nseerr\u0007' });
    ok(e.class === 'upgrade' && e.attempted === '1' && e.result === 'inprogress' && e.holder === 'lane' && e.lane_state === 'working', 'class upgrade, attempted 1, result inprogress, holder lane, working');
    ok(e.last_paged === '1794560000' && e.first_seen === String(NOW_S) && e.claimed_at === String(NOW_S) && e.until === String(NOW_S + 10800), 'keeps last_paged; first_seen and claimed_at are the claim; until is its own bound');
    ok(!/[\u0000-\u001f]/.test(e.note), 'the note carries no control characters');
    ok(C.claimConfig({ night: { claim: { max_work_seconds: 10800 } } }).ok && !C.claimConfig({ night: { claim: { max_work_seconds: 99999 } } }).ok && !C.claimConfig({}).ok, 'night.claim.max_work_seconds is checked (1800..21600)');
    const rules = JSON.parse(readFileSync(join(RES, 'upgrade-rules.json'), 'utf8'));
    ok(C.claimConfig(rules).ok && rules.night.claim.max_work_seconds === 10800, 'git ships a valid bound (10800 s)');
  }

  // ── 3. triage claims first: the lane stops ─────────────────────────────────────────
  section('3. triage claims first: the lane stops and does not revert');
  {
    const w = world({ cms: { 'upgrade-remediation-state': {} } });
    const t = harness(w, ['state_claim', SIG.sig, SIG.ids.join('\n')]);
    ok(t.status === 0 && /claim sig=.* holder=triage/.test(t.stderr), 'triage claims (state_claim rc 0)', t.stderr.slice(0, 300));
    const e = w.entry(SIG.sig);
    ok(e?.attempted === '1' && e.holder === 'triage' && e.class === 'upgrade' && Array.isArray(e.ids) && e.ids.length === SIG.ids.length, 'its entry: attempted 1, holder triage, the ids it saw');
    const before = w.writes().length;
    const s = C.claimStep({ rec: REC, live: true, now: NOW, work: WORK, deps: laneDeps(w, SIG) });
    ok(s.outcome === 'triage-holds' && !s.revert_allowed && !s.wrote && s.holder === 'triage', 'the lane: triage-holds, no revert, nothing written', JSON.stringify(s).slice(0, 300));
    ok(/triage owns it/.test(s.why) && /does not revert/.test(s.why), 'and records that triage owns it', s.why);
    ok(w.writes().length === before && w.entry(SIG.sig).holder === 'triage', 'the ConfigMap is unchanged');
    // under another signature: triage still working on an entry that names the target's ids
    const w2 = world({ cms: { 'upgrade-remediation-state': { aaaaaaaaaaaa: JSON.stringify({ class: 'upgrade', attempted: '1', result: 'pending', holder: 'triage', ids: ['flux/HelmRelease/media/seerr'], first_seen: String(NOW_S - 600) }) } } });
    const s2 = C.claimStep({ rec: REC, live: true, now: NOW, work: WORK, deps: laneDeps(w2, SIG) });
    ok(s2.outcome === 'triage-holds' && !s2.revert_allowed && /aaaaaaaaaaaa/.test(s2.why), 'triage working under another signature that names the target: the lane stops too');
    // an old triage entry (no ids) under the same signature, attempted: held
    const w3 = world({ cms: { 'upgrade-remediation-state': { [SIG.sig]: JSON.stringify({ class: 'upgrade', attempted: '1', result: 'failed', first_seen: String(NOW_S - 3600), last_paged: '0' }) } } });
    ok(C.claimStep({ rec: REC, live: true, now: NOW, work: WORK, deps: laneDeps(w3, SIG) }).outcome === 'triage-holds', 'an entry written before story 554 (attempted 1, no holder): triage holds it');
    // a finished triage entry under another signature (result failed) does not hold a new one
    const w4 = world({ cms: { 'upgrade-remediation-state': { bbbbbbbbbbbb: JSON.stringify({ class: 'upgrade', attempted: '1', result: 'failed', holder: 'triage', ids: ['flux/HelmRelease/media/seerr'], first_seen: String(NOW_S - 9000) }) } } });
    ok(C.claimStep({ rec: REC, live: true, now: NOW, work: WORK, deps: laneDeps(w4, SIG) }).outcome === 'claimed', 'a finished triage attempt under another signature does not hold this one');
  }

  // ── 4. the lane claims first: triage stops ─────────────────────────────────────────
  section('4. the lane claims first: triage does not summon and writes nothing over it');
  {
    const w = world({ cms: { 'upgrade-remediation-state': { other: JSON.stringify({ class: 'nonupgrade', attempted: '0', result: 'none', first_seen: String(NOW_S - 60), last_paged: '0' }) } } });
    const s = C.claimStep({ rec: REC, live: true, now: NOW, work: WORK, deps: laneDeps(w, SIG) });
    ok(s.outcome === 'claimed' && s.wrote && s.revert_allowed && s.holder === 'lane', 'the lane claims (replace at the version read)', JSON.stringify(s).slice(0, 300));
    const e = w.entry(SIG.sig);
    ok(e.holder === 'lane' && e.pr === '4869' && e.attempted === '1' && e.lane_state === 'working' && JSON.stringify(e.ids) === JSON.stringify(MINE), 'its entry names #4869, working, and the target\'s ids');
    ok(w.entry('other')?.class === 'nonupgrade', 'other entries are kept');
    const h = harness(w, ['lane_hold', SIG.sig, SIG.ids.join('\n')]);
    ok(h.status === 0 && /held by the night lane for #4869/.test(h.stdout), 'triage: lane_hold says the lane holds it', h.stdout + h.stderr);
    const n = w.writes().length;
    const c = harness(w, ['state_claim', SIG.sig, SIG.ids.join('\n')]);
    ok(c.status !== 0 && /standing down: not triage's/.test(c.stderr) && w.writes().length === n, 'triage: state_claim stands down and writes nothing', c.stderr.slice(0, 300));
    const u = harness(w, ['state_upsert', SIG.sig, 'nonupgrade', '0', 'none']);
    ok(u.status !== 0 && /NOT writing over its claim/.test(u.stderr) && w.entry(SIG.sig).holder === 'lane' && w.entry(SIG.sig).attempted === '1', 'triage: state_upsert refuses to write over the lane\'s claim');
    const nu = harness(w, ['nonupgrade_record', SIG.sig]);
    ok(w.entry(SIG.sig).class === 'upgrade' && w.entry(SIG.sig).lane_state === 'working', 'triage: the non-upgrade path leaves the lane\'s claim as it was', nu.stderr.slice(0, 200));
    // the shipped main flow checks lane_hold before attribution and before any write
    const tri = readFileSync(join(RES, 'triage.sh'), 'utf8');
    const iHold = tri.indexOf('LANE_HELD="$(lane_hold "$STATE_JSON" "$SIG" "$REG_IDS")"'), iAttr = tri.indexOf('CLASS="$(attribute "$SIG" "$REG_IDS")"'), iClaim = tri.indexOf('if ! state_claim "$SIG" "$REG_IDS"; then');
    ok(iHold > 0 && iAttr > iHold && iClaim > iAttr && /not triage's: NOT summoning remediation and NOT writing its entry/.test(tri), 'triage.sh: the lane check runs before attribution and the claim, and says so in its log');
    ok(!/state_upsert "\$SIG" upgrade 1 none/.test(tri), 'triage.sh: the claim is state_claim (decide and write on one read), not the old upsert');
    // a churned set: the lane's claim names seerr; the set gained another failure, so the signature moved
    const moved = 'cccccccccccc';
    const h2 = harness(w, ['lane_hold', moved, [...SIG.ids, 'flux/Kustomization/ai/comfyui'].join('\n')]);
    ok(h2.status === 0 && /the night lane holds .* which names/.test(h2.stdout), 'a set that changed since the claim (same regression, new signature): still the lane\'s', h2.stdout);
    const h3 = harness(w, ['lane_hold', moved, 'flux/Kustomization/ai/comfyui']);
    ok(h3.status !== 0, 'a regression that names nothing of the lane\'s claim is triage\'s as before');
    const h4 = harness(w, ['lane_hold', moved, SIG.ids.join('\n')], { NOW: String(NOW_S + 10801) });
    ok(h4.status !== 0, 'past its own bound, a lane claim no longer holds a moved signature (its own signature stays held: at most once)');
  }

  // ── 5. both at once ───────────────────────────────────────────────────────────────
  section('5. both claim at the same moment: exactly one holder');
  {
    // triage writes between the lane's read and the lane's write
    const w = world({ cms: { 'upgrade-remediation-state': {} } });
    const s = C.claimStep({ rec: REC, live: true, now: NOW, work: WORK, deps: { ...laneDeps(w, SIG), beforeWrite: () => { const t = harness(w, ['state_claim', SIG.sig, SIG.ids.join('\n')]); ok(t.status === 0, 'triage claims in between the lane\'s read and write'); } } });
    ok(s.outcome === 'lost' && !s.revert_allowed && s.holder === 'triage' && /refused/.test(s.why), 'the lane\'s write is refused (Conflict); it reads the winner and stands down', JSON.stringify(s).slice(0, 400));
    const e = w.entry(SIG.sig);
    ok(e.holder === 'triage' && e.attempted === '1', 'one holder: triage');
    // the lane writes between triage's read and triage's write (the fake runs the lane once, before triage's first write)
    const w2 = world({ cms: { 'upgrade-remediation-state': {} } });
    const hookOut = join(w2.dir, 'hook.out');
    const t = harness(w2, ['state_claim', SIG.sig, SIG.ids.join('\n')], {
      FAKE_KUBE_HOOK: JSON.stringify(['node', join(ROOT, 'scripts/upgrade-agent/test-lane-claim-hook.mjs')]), FAKE_KUBE_HOOK_ONCE: join(w2.dir, 'hook.once'), FAKE_HOOK_OUT: hookOut,
      FIXTURE: FX_PATH, SIG: SIG.sig, IDS: JSON.stringify(SIG.ids), NOW_MS: String(NOW), LIVE: '1' });
    const hook = JSON.parse((readFileSync(hookOut, 'utf8').split('\n').find(l => l.startsWith('{')) || 'null'));
    ok(hook?.outcome === 'claimed' && hook.wrote, 'the lane claims in between triage\'s read and write', JSON.stringify(hook).slice(0, 200));
    ok(t.status !== 0 && /claim refused/.test(t.stderr) && /standing down: not triage's/.test(t.stderr), 'triage\'s write is refused; it reads again, finds the lane and stands down', t.stderr.slice(0, 400));
    const e2 = w2.entry(SIG.sig);
    ok(e2.holder === 'lane' && e2.pr === '4869', 'one holder: the lane');
    // both find the ConfigMap absent: create is the precondition (AlreadyExists for the second)
    const w3 = world();
    const s3 = C.claimStep({ rec: REC, live: true, now: NOW, work: WORK, deps: { ...laneDeps(w3, SIG), beforeWrite: () => harness(w3, ['state_claim', SIG.sig, SIG.ids.join('\n')]) } });
    ok(s3.outcome === 'lost' && w3.entry(SIG.sig)?.holder === 'triage', 'ConfigMap absent: triage\'s create wins, the lane\'s create is AlreadyExists, the lane stands down');
    // an unrelated write (the gate stamping another signature) between read and write: the lane reads again and claims
    const w4 = world({ cms: { 'upgrade-remediation-state': { dddddddddddd: JSON.stringify({ last_paged: '1', first_seen: String(NOW_S) }) } } });
    let stamped = false;
    const s4 = C.claimStep({ rec: REC, live: true, now: NOW, work: WORK, deps: { ...laneDeps(w4, SIG), beforeWrite: () => {
      if (stamped) return; stamped = true;
      const st = JSON.parse(readFileSync(w4.state, 'utf8')); const cm = st.configmaps['upgrade-agent/upgrade-remediation-state'];
      cm.data.dddddddddddd = JSON.stringify({ last_paged: '2', first_seen: String(NOW_S) }); st.rv += 1; cm.metadata.resourceVersion = String(st.rv); writeFileSync(w4.state, JSON.stringify(st)); } } });
    ok(s4.outcome === 'claimed' && w4.entry(SIG.sig)?.holder === 'lane' && JSON.parse(w4.cm('upgrade-remediation-state').data.dddddddddddd).last_paged === '2', 'an unrelated write in between: Conflict, read again, still free, claimed at the new version; the other write kept');
  }

  // ── 6. an unreadable claim ────────────────────────────────────────────────────────
  section('6. an unreadable claim: the lane does not revert');
  {
    const w = world({ cms: { 'upgrade-remediation-state': { [SIG.sig]: '{not json' } } });
    const s = C.claimStep({ rec: REC, live: true, now: NOW, work: WORK, deps: laneDeps(w, SIG) });
    ok(s.outcome === 'unreadable' && !s.revert_allowed && !s.wrote && /not a JSON object/.test(s.why), 'an entry that is not JSON: unreadable, no revert, no write');
    const w2 = world({ cms: { 'upgrade-remediation-state': {} }, unreadable: true });
    const s2 = C.claimStep({ rec: REC, live: true, now: NOW, work: WORK, deps: laneDeps(w2, SIG) });
    ok(s2.outcome === 'unreadable' && !s2.revert_allowed && w2.writes().length === 0, 'the ConfigMap cannot be read: unreadable, no revert, no write');
    const w3 = world({ cms: { 'upgrade-remediation-state': { [SIG.sig]: '"a string"' } } });
    ok(C.claimStep({ rec: REC, live: true, now: NOW, work: WORK, deps: laneDeps(w3, SIG) }).outcome === 'unreadable', 'an entry that is JSON but not an object: unreadable');
    ok(C.claimStatus({ found: true, cm: { data: {} } }, SIG.sig).state === 'unreadable', 'no resourceVersion: unreadable (nothing to write against)');
  }

  // the shipped gate.sh and page.sh against the fake cluster, its clock pinned to nowS
  const gate = (stateData, nowS = NOW_S) => {
    const w = world({ cms: stateData === undefined ? {} : { 'upgrade-remediation-state': stateData } });
    const curlLog = join(w.dir, 'curl.log');
    writeFileSync(curlLog, '');
    // NOW is the gate's own clock (date -u +%s): pin it with a fake date that answers %s
    const fakeDate = join(w.dir, 'bin');
    mkdirSync(fakeDate);
    const realDate = spawnSync('bash', ['-c', 'command -v date'], { encoding: 'utf8' }).stdout.trim();
    writeFileSync(join(fakeDate, 'date'), `#!/usr/bin/env bash\nfor a in "$@"; do [ "$a" = "+%s" ] && { echo ${nowS}; exit 0; }; done\nexec ${realDate} "$@"\n`);
    chmodSync(join(fakeDate, 'date'), 0o755);
    // the ConfigMap mounts the scripts 0555; git does not carry the mode, so copy them
    const gd = join(w.dir, 'gate'); mkdirSync(gd);
    for (const f of ['gate.sh', 'page.sh']) { writeFileSync(join(gd, f), readFileSync(join(GATE_RES, f))); chmodSync(join(gd, f), 0o755); }
    const r = spawnSync('bash', [join(gd, 'gate.sh')], { env: { ...w.env, PATH: `${fakeDate}:${w.env.PATH}`, COORD_LIB: LIB, PUSHOVER_TOKEN: 'test-token', PUSHOVER_USER_KEY: 'test-user', FAKE_CURL_LOG: curlLog, PROMETHEUS_URL: 'http://prometheus.test:9090' }, encoding: 'utf8' });
    const pages = readFileSync(curlLog, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l));
    return { r, pages, w, flux: pages.filter(p => p.some(a => /^title=.*critical flux/.test(a))), podsP: pages.filter(p => p.some(a => /^title=.*critical pods/.test(a))) };
  };

  // ── 7. the gate ───────────────────────────────────────────────────────────────────
  section('7. the gate reads the lane\'s claim');
  {
    const working = C.laneEntry({ pr: 4869, mine: MINE, now: NOW - 600e3, workSeconds: 10800, note: 'night lane' });
    const g1 = gate({ [SIG.sig]: JSON.stringify(working) });
    ok(g1.r.status === 0 && g1.flux.length === 0 && g1.podsP.length === 0 && /coordination sig=.* phase=inflight: nothing to page/.test(g1.r.stderr), 'a working lane claim: the gate is silent (phase inflight)', g1.r.stderr.split('\n').filter(l => /coordination|paged/.test(l)).join(' | ').slice(0, 400));
    const g0 = gate({});
    ok(g0.flux.length === 1, 'the same regression with no claim: the gate pages Flux as today (the control)');
    const failedRevert = { ...working, lane_state: 'revert-failed', result: 'failed' };
    const g2 = gate({ [SIG.sig]: JSON.stringify(failedRevert) });
    ok(g2.flux.length === 1 && g2.podsP.length === 1 && g2.flux[0].some(a => /revert did NOT verify/.test(a)), 'the lane records that its revert did not verify: the gate pages (Flux and pods) and says so');
    ok(g2.flux[0].includes('priority=1'), 'at the existing critical priority');
    const past = C.laneEntry({ pr: 4869, mine: MINE, now: NOW - 11000e3, workSeconds: 10800, note: 'x' });
    const g3 = gate({ [SIG.sig]: JSON.stringify(past) });
    ok(g3.flux.length === 1 && g3.flux[0].some(a => /did not finish by its own bound/.test(a)), 'a working claim past its own bound: the gate pages (it never silences for ever)');
    const g4 = gate({ [SIG.sig]: JSON.stringify({ ...working, until: String(Number(working.claimed_at) + 999999) }) });
    ok(g4.flux.length === 0 && /phase=inflight/.test(g4.r.stderr), 'an until beyond 6 h is not understood: read as today (class upgrade, inprogress, within the 40-minute grace)');
    const g4b = gate({ [SIG.sig]: JSON.stringify({ ...working, first_seen: String(NOW_S - 3600), until: 'soon' }) });
    ok(g4b.flux.length === 1, 'not understood and past today\'s 40-minute grace: pages as today');
    const g5 = gate({ [SIG.sig]: '{not json' });
    ok(g5.flux.length === 1, 'an unreadable claim: the gate pages as today (Flux)');
    const g6 = gate(undefined);
    ok(g6.flux.length === 1, 'the ConfigMap absent: the gate pages as today');
    const g7 = gate({ [SIG.sig]: JSON.stringify({ ...working, lane_state: 'reverted', result: 'fixed' }) });
    ok(g7.flux.length === 1 && g7.flux[0].some(a => /verified recovery, but the signal persists/.test(a)), 'reverted but still failing: pages as today\'s "fixed"');
    const gsh = readFileSync(join(GATE_RES, 'gate.sh'), 'utf8');
    ok(/LANE_CLAIM_MAX_SECONDS="\$\{LANE_CLAIM_MAX_SECONDS:-21600\}"/.test(gsh), 'gate.sh caps a lane bound at 6 hours');
  }

  // ── 8. dry-run and practice write nothing ──────────────────────────────────────────
  section('8. dry-run and practice write nothing to upgrade-remediation-state');
  {
    const w = world({ cms: { 'upgrade-remediation-state': {} } });
    const s = C.claimStep({ rec: { ...REC, practice: true }, live: false, now: NOW, work: WORK, deps: laneDeps(w, SIG) });
    ok(s.outcome === 'would-claim' && !s.wrote && !s.revert_allowed && s.sig === SIG.sig && /would claim signature/.test(s.why), 'practice, free: would-claim, the signature recorded, nothing written', JSON.stringify(s).slice(0, 300));
    harness(w, ['state_claim', SIG.sig, SIG.ids.join('\n')]);
    const n = w.writes().length;
    const s2 = C.claimStep({ rec: { ...REC, practice: true }, live: false, now: NOW, work: WORK, deps: laneDeps(w, SIG) });
    ok(s2.outcome === 'triage-holds' && s2.holder === 'triage' && w.writes().length === n, 'practice, triage holds it: recorded read-only');
    const s3 = C.claimStep({ rec: REC, live: false, now: NOW, work: WORK, deps: laneDeps(world({ cms: { 'upgrade-remediation-state': {} } }), SIG) });
    ok(s3.outcome === 'would-claim' && !s3.wrote, 'dry-run on the lane\'s own merge: would-claim, nothing written');
    const s4 = C.claimStep({ rec: { ...REC, attribution: 'cannot-tell-which-change' }, live: true, now: NOW, work: WORK, deps: laneDeps(world({ cms: { 'upgrade-remediation-state': {} } }), SIG) });
    ok(s4.outcome === 'not-own-regression' && !s4.wrote && !s4.revert_allowed, 'live but cannot tell which change: a person owns it, nothing claimed');
    const s5 = C.claimStep({ rec: { ...REC, scope: { own: ['ai/other'], hr: [] }, workloads: [] }, live: true, now: NOW, work: WORK, deps: laneDeps(world({ cms: { 'upgrade-remediation-state': {} } }), SIG) });
    ok(s5.outcome === 'not-in-set' && !s5.wrote && s5.revert_allowed, 'a regression the coordination set does not name: nothing to claim (triage and the gate do not see it)');
    // the tick wires it: live only with --write-state, the lane's own merge and a valid bound
    const rn = readFileSync(join(RES, 'run-night.mjs'), 'utf8');
    ok(/C\.claimStep\(\{ rec: r, live: MODE === 'live' && WRITE_STATE && !r\.practice && work\.ok, now, work, deps \}\)/.test(rn), 'run-night.mjs: live = live mode, --write-state, the lane\'s own merge, a valid bound');
    ok(rn.indexOf('C.claimStep(') > rn.indexOf('V.verifyPhase(') && rn.indexOf('C.claimStep(') < rn.indexOf('H.historyPhase('), 'run-night.mjs: the step runs after the verifier and before the history');
  }

  // ── 9. the one write site ─────────────────────────────────────────────────────────
  section('9. the one write site refuses anything else');
  {
    const m = (o = {}) => ({ apiVersion: 'v1', kind: 'ConfigMap', metadata: { name: 'upgrade-remediation-state', namespace: 'upgrade-agent', resourceVersion: '5', ...o }, data: {} });
    const refused = (verb, man) => { try { C.writeState(verb, man, {}, () => { throw new Error('spawned'); }); return false; } catch (e) { return /refused/.test(e.message); } };
    ok(refused('replace', m({ name: 'upgrade-lane-state' })), 'another ConfigMap: refused');
    ok(refused('replace', m({ namespace: 'media' })), 'another namespace: refused');
    ok(refused('replace', { ...m(), metadata: { name: 'upgrade-remediation-state', namespace: 'upgrade-agent' } }), 'a replace without a resourceVersion: refused');
    ok(refused('create', m()), 'a create carrying a resourceVersion: refused');
    ok(refused('apply', m()) && refused('patch', m()) && refused('delete', m()), 'apply, patch, delete: refused');
    ok(refused('replace', { ...m(), spec: {} }), 'extra top-level keys: refused');
    const src = readFileSync(join(RES, 'night-claim.mjs'), 'utf8');
    ok((src.match(/spawn\(/g) || []).length === 2 && /spawn\('bash', \['-c', SIG_SCRIPT\]/.test(src) && /spawn\('kubectl', \[verb, '-f', '-', '-o', 'json'\]/.test(src), 'night-claim.mjs has two process calls: the library (read-only) and writeState');
    const kz = readFileSync(join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/kustomization.yaml'), 'utf8');
    ok(/resources\/night-claim\.mjs/.test(kz) && /resources\/night-summary\.mjs/.test(kz), 'the scripts ConfigMap carries night-claim.mjs and night-summary.mjs');
    const hr = readFileSync(join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/helmrelease.yaml'), 'utf8');
    ok(/coordination-lib:\s*\n\s*type: configMap\s*\n\s*name: upgrade-coordination-lib[\s\S]{0,120}globalMounts:\s*\n\s*- path: \/opt\/coordination/.test(hr), 'the lane\'s pod mounts the library at /opt/coordination (globalMounts)');
    // updateClaim, for story 753
    const w = world({ cms: { 'upgrade-remediation-state': {} } });
    C.claimStep({ rec: REC, live: true, now: NOW, work: WORK, deps: laneDeps(w, SIG) });
    const u = C.updateClaim({ sig: SIG.sig, pr: 4869, laneState: 'revert-failed', now: NOW + 60e3, deps: laneDeps(w, SIG) });
    ok(u.ok && w.entry(SIG.sig).lane_state === 'revert-failed' && w.entry(SIG.sig).result === 'failed', 'updateClaim (story 753): revert-failed -> result failed');
    ok(!C.updateClaim({ sig: SIG.sig, pr: 9999, laneState: 'reverted', now: NOW, deps: laneDeps(w, SIG) }).ok, 'updateClaim refuses a claim that is not this PR\'s');
    ok(!C.updateClaim({ sig: SIG.sig, pr: 4869, laneState: 'bogus', now: NOW, deps: laneDeps(w, SIG) }).ok, 'updateClaim refuses an unknown state');
  }

  // ── 10. the review of the branch (fix round) ────────────────────────────────────────
  section('10. the review: the gate on its own clock (M2), the moved signature (L10), F4 (L7), and the surviving mutations');
  {
    const e = (o = {}) => JSON.stringify({ ...C.laneEntry({ pr: 4869, mine: MINE, now: NOW - 600e3, workSeconds: 10800, note: 'night lane' }), ...o });
    // M2: a claim from the future is not believed; the cap is on the gate's silence
    const y10 = String(NOW_S + 10 * 365 * 86400);
    const gf = gate({ [SIG.sig]: e({ claimed_at: y10, until: String(Number(y10) + 3600), first_seen: y10 }) });
    ok(gf.flux.length === 1, 'claimed_at ten years ahead, until an hour after it: not believed, the gate pages (was silent for years)', gf.r.stderr.split('\n').filter(l => /coordination/.test(l)).join(' | ').slice(0, 300));
    const gd = gate({ [SIG.sig]: e({ claimed_at: String(NOW_S + 86400), until: String(NOW_S + 86400 + 3600), first_seen: String(NOW_S + 86400) }) });
    ok(gd.flux.length === 1, 'claimed_at a day ahead: the gate pages');
    const gs = gate({ [SIG.sig]: e({ claimed_at: String(NOW_S + 200), until: String(NOW_S + 3600), first_seen: String(NOW_S + 200) }) });
    ok(gs.flux.length === 0, 'claimed_at 200 s ahead (within CLOCK_SKEW_SECONDS, 300): believed, silent');
    const gu = gate({ [SIG.sig]: e({ claimed_at: String(NOW_S + 200), until: String(NOW_S + 100), first_seen: String(NOW_S - 3600) }) });
    ok(gu.flux.length === 1, 'until before claimed_at (and NOW before until): not understood, read as today, pages (mutation: no until >= claimed_at check)');
    const gc = gate({ [SIG.sig]: e({ claimed_at: String(NOW_S - 3600), until: String(NOW_S - 3600 + 25200), first_seen: String(NOW_S - 3600) }) });
    ok(gc.flux.length === 1, 'a claim 7 hours long (over the 6-hour cap), not yet past until: not understood, read as today (an hour past the 40-minute grace), pages (mutation: the cap disabled)');
    const gz = gate({ [SIG.sig]: e({ claimed_at: `0${NOW_S - 600}`, first_seen: String(NOW_S - 3600) }) });
    ok(gz.flux.length === 1, 'a leading zero (octal to bash arithmetic): not understood, read as today');
    const gr = gate({ [SIG.sig]: JSON.stringify({ class: 'upgrade', attempted: '1', result: 'none', first_seen: String(NOW_S + 86400), last_paged: '0' }) });
    ok(gr.flux.length === 1, 'today\'s reading, a first_seen a day ahead (remediation_phase): its grace counts as spent, the gate pages (was silent)');
    const gr2 = gate({ [SIG.sig]: JSON.stringify({ class: 'upgrade', attempted: '1', result: 'none', first_seen: String(NOW_S + 120), last_paged: '0' }) });
    ok(gr2.flux.length === 0, 'today\'s reading, a first_seen 120 s ahead (within the tolerance): in flight, silent as before');
    const gsh = readFileSync(join(GATE_RES, 'gate.sh'), 'utf8');
    ok(/CLOCK_SKEW_SECONDS="\$\{CLOCK_SKEW_SECONDS:-300\}"/.test(gsh), 'the tolerance is named: CLOCK_SKEW_SECONDS, 300');
    // L10: a moved signature pages now, with the true note
    const gm = gate({ cccccccccccc: e({ ids: ['flux/HelmRelease/media/seerr'] }) });
    const note = gm.flux[0]?.find(a => a.startsWith('message=')) || '';
    ok(gm.flux.length === 1 && /the night lane is reverting #4869 \(claim cccccccccccc, until [0-9T:-]+Z\) and holds flux\/HelmRelease\/media\/seerr; the rest of this set \(flux\/Kustomization\/media\/seerr, pod\/ai\/kokoro-6dcbb966bf-c8z88, pod\/media\/seerr-bb94554fd-qd4fm\) has no owner/.test(note) && !/not attributable to a recent upgrade/.test(note),
      'a set that moved since the lane claimed part of it: the gate pages, naming the lane\'s PR and ids and saying the rest has no owner', note.slice(0, 400));
    const gm2 = gate({ cccccccccccc: e({ ids: ['flux/HelmRelease/media/seerr'], until: String(NOW_S - 60), claimed_at: String(NOW_S - 3600) }) });
    ok(gm2.flux.length === 1 && gm2.flux[0].some(a => /not attributable to a recent upgrade/.test(a)), 'a lane claim past its bound under another signature: today\'s note');
    // L7: the morning summary's Jobs never merge, so F4 does not wait for them
    const job = (meta, done = false) => ({ metadata: { name: 'x-1', ...meta }, status: done ? { completionTime: 't' } : {} });
    const mj = M.mergingJobs({ items: [job({ ownerReferences: [{ kind: 'CronJob', name: 'upgrade-health-gate-morning-summary' }] }), job({ name: 'manual', labels: { 'app.kubernetes.io/controller': 'morning-summary' } })] }, { found: true, cm: { data: {} } });
    ok(mj.readable && mj.active.length === 0, 'F4: a running morning-summary Job (by its CronJob, or by its controller label) is not one that can merge', JSON.stringify(mj));
    ok(M.mergingJobs({ items: [job({ ownerReferences: [{ kind: 'CronJob', name: 'something-new' }] })] }, { found: true, cm: { data: {} } }).active.length === 1, 'an unknown Job still counts (fail closed: the control)');
    // surviving mutation: triage's create counted as claimed on AlreadyExists (losing a create race to the lane)
    const w3 = world();
    const hookOut = join(w3.dir, 'hook.out');
    const t3 = harness(w3, ['state_claim', SIG.sig, SIG.ids.join('\n')], {
      FAKE_KUBE_HOOK: JSON.stringify(['node', join(ROOT, 'scripts/upgrade-agent/test-lane-claim-hook.mjs')]), FAKE_KUBE_HOOK_ONCE: join(w3.dir, 'hook.once'), FAKE_HOOK_OUT: hookOut,
      FIXTURE: FX_PATH, SIG: SIG.sig, IDS: JSON.stringify(SIG.ids), NOW_MS: String(NOW), LIVE: '1' });
    ok(t3.status !== 0 && /claim refused/.test(t3.stderr) && /standing down: not triage's/.test(t3.stderr) && w3.entry(SIG.sig)?.holder === 'lane', 'ConfigMap absent at triage\'s read, the lane creates it first: triage\'s create is AlreadyExists, it reads again and stands down', t3.stderr.slice(0, 300));
    // surviving mutation: a lane claim under another signature holding for ever on the lane's side
    const w4 = world({ cms: { 'upgrade-remediation-state': { eeeeeeeeeeee: JSON.stringify({ holder: 'lane', pr: '1234', lane_state: 'working', attempted: '1', result: 'inprogress', claimed_at: String(NOW_S - 20000), until: String(NOW_S - 9200), ids: MINE, first_seen: String(NOW_S - 20000) }) } } });
    ok(C.claimStep({ rec: REC, live: true, now: NOW, work: WORK, deps: laneDeps(w4, SIG) }).outcome === 'claimed', 'another update\'s lane claim past its own until does not hold this regression for ever (the lane claims it)');
    const w4b = world({ cms: { 'upgrade-remediation-state': { eeeeeeeeeeee: JSON.stringify({ holder: 'lane', pr: '1234', lane_state: 'working', attempted: '1', result: 'inprogress', claimed_at: String(NOW_S - 600), until: String(NOW_S + 9000), ids: MINE, first_seen: String(NOW_S - 600) }) } } });
    ok(C.claimStep({ rec: REC, live: true, now: NOW, work: WORK, deps: laneDeps(w4b, SIG) }).outcome === 'lane-holds', 'the same claim before its until: it holds (the control)');
    // surviving mutation: after a write error that is not a conflict, the lane reads back whether its claim landed
    const w5 = world({ cms: { 'upgrade-remediation-state': {} } });
    const d5 = laneDeps(w5, SIG);
    const s5 = C.claimStep({ rec: REC, live: true, now: NOW, work: WORK, deps: { ...d5, write: (verb, m) => { d5.write(verb, m); return { ok: false, conflict: false, detail: 'timed out waiting for the server' }; } } });
    ok(s5.outcome === 'claimed' && s5.revert_allowed && /landed \(read back after: timed out/.test(s5.why), 'a write that landed but reported an error (a timeout): read back, it is the lane\'s claim', JSON.stringify(s5).slice(0, 300));
    const s5b = C.claimStep({ rec: REC, live: true, now: NOW, work: WORK, deps: { ...laneDeps(world({ cms: { 'upgrade-remediation-state': {} } }), SIG), write: () => ({ ok: false, conflict: false, detail: 'Forbidden' }) } });
    ok(s5b.outcome === 'lost' && !s5b.revert_allowed && /Forbidden/.test(s5b.why), 'a write refused (RBAC) that did not land: no revert');
    // surviving mutation: triage's early lane-hold exit (behaviour, not source order): the shipped
    // triage.sh, its fixed paths pointed at stubs, against the fake cluster
    const tri = (cms) => {
      const w = world({ cms });
      const d = join(w.dir, 'tri'); mkdirSync(d);
      writeFileSync(join(d, 'gh_token'), 'not-a-token');
      writeFileSync(join(d, 'summon.sh'), `#!/usr/bin/env bash\necho SUMMONED >> ${JSON.stringify(join(d, 'summoned'))}\n`);
      for (const b of ['git', 'gh']) { writeFileSync(join(d, b), '#!/usr/bin/env bash\nexit 128\n'); chmodSync(join(d, b), 0o755); }
      // triage's clock (date -u +%s) pinned to the fixture's, like the gate's
      const realDate = spawnSync('bash', ['-c', 'command -v date'], { encoding: 'utf8' }).stdout.trim();
      writeFileSync(join(d, 'date'), `#!/usr/bin/env bash\nfor a in "$@"; do [ "$a" = "+%s" ] && { echo ${NOW_S}; exit 0; }; done\nexec ${realDate} "$@"\n`); chmodSync(join(d, 'date'), 0o755);
      const src = readFileSync(join(RES, 'triage.sh'), 'utf8').replaceAll('/creds/gh_token', join(d, 'gh_token')).replaceAll('/opt/shepherd/run-shepherd.sh', join(d, 'summon.sh')).replaceAll('/opt/coordination/escalate.sh', join(d, 'summon.sh'));
      writeFileSync(join(d, 'triage.sh'), src);
      const urls = join(d, 'urls');
      writeFileSync(urls, '');
      const r = spawnSync('bash', [join(d, 'triage.sh')], { env: { ...w.env, PATH: `${d}:${w.env.PATH}`, COORD_LIB: LIB, PROMETHEUS_URL: 'http://prometheus.test:9090', FAKE_CURL_URLS: urls, HOME: d }, encoding: 'utf8' });
      return { r, w, github: readFileSync(urls, 'utf8').split('\n').filter(u => /api\.github\.com/.test(u)).length, summoned: existsSync(join(d, 'summoned')) };
    };
    const held = tri({ 'upgrade-remediation-state': { [SIG.sig]: e() } });
    ok(held.r.status === 0 && /is held by the night lane for #4869 .* — not triage's: NOT summoning remediation and NOT writing its entry/.test(held.r.stderr) && held.github === 0 && !held.summoned && held.w.writes().length === 0,
      'triage.sh, run: a signature the lane holds: it says so, reads nothing from GitHub, attributes nothing, summons nothing, writes nothing', held.r.stderr.split('\n').slice(-4).join(' | ').slice(0, 400));
    const moved = tri({ 'upgrade-remediation-state': { cccccccccccc: e({ ids: ['flux/HelmRelease/media/seerr'] }) } });
    ok(moved.r.status === 0 && /the night lane holds cccccccccccc for #4869/.test(moved.r.stderr) && moved.github === 0 && moved.w.writes().length === 0, 'triage.sh, run: a moved signature the lane holds part of: the same', moved.r.stderr.split('\n').slice(-4).join(' | ').slice(0, 500));
    const free = tri({ 'upgrade-remediation-state': {} });
    ok(free.r.status === 0 && free.github >= 1 && /attribution|merge to main/.test(free.r.stderr) && !/held by the night lane/.test(free.r.stderr), 'triage.sh, run, no lane claim: it reads GitHub and attributes as before (the control)', free.r.stderr.split('\n').slice(-3).join(' | ').slice(0, 300));
  }
} finally {
  rmSync(TMP, { recursive: true, force: true });
}

console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
