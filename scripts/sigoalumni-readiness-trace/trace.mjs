#!/usr/bin/env node
// sigoalumni readiness-threshold trace model (haynes-ops story H3, Sigo hardening Phase B).
//
// Deterministic, pure-computation model; NO cluster, network, Pub/Sub, DNS or watchdog
// calls. It answers: what does readiness failureThreshold 3 -> 9 change, and does the
// sustained-outage signal chain (readiness -> Service backends -> GCP uptime ->
// watchdog confirm -> steer) survive?  Rows R1-R4 of SIGO-HARDENING-PLAN.md.
//
//   nice -n 19 node scripts/sigoalumni-readiness-trace/trace.mjs [--sigo-repo DIR] [--ref REF] [--json]
//
// The one external input is the watchdog's own decision code, imported UNMODIFIED from
// sigoalumni-org (git show <ref>:watchdog/lib/{decide,steer}.mjs into a temp dir, so
// nothing is vendored here). If the repo is unavailable, the steer checks are SKIPPED
// and reported as such (the rest of the model still runs).
//
// Model, exactly (also the limitations; see the PR body):
//  * Kubelet readiness: probes start at phase+10k (worker ticker; period 10s, timeout 5s,
//    successThreshold 1). A probe fails if the app answers non-200 or takes > 5s. The
//    result is counted when the probe COMPLETES (start + response time). The pod leaves
//    the Service when consecutiveFailures reaches the threshold and rejoins at the first
//    success. Endpoint/EndpointSlice/Traefik propagation delay (seconds) is NOT modeled.
//  * App health: status is decided by the DB state when the request STARTS (primary);
//    "overlap" sensitivity makes a request fail if the DB is down at any instant in
//    [start, start+overlap]. Honest fast failure = 503 within <= 2s (the #164 budget).
//  * Liveness: tcpSocket 8080 never reads the DB; the process stays up, so it passes.
//  * GCP uptime: 6 checker series, each sampling `/` every 300s at a random phase,
//    pass = HTTP 200 within 10s. Alert model of terraform/watchdog.tf: per-series value
//    = latest sample at the evaluation time (stand-in for ALIGN_NEXT_OLDER over 1200s),
//    REDUCE_COUNT_FALSE > 1, held for 300s, evaluated every 60s. This is a SIMPLIFIED
//    model of Cloud Monitoring, not a reimplementation of it.
//  * `/` for an anonymous visitor: when at least one backend is Ready it returns 200
//    even if the DB is erroring (events degrade to null, per FACT-VALIDATION); with NO
//    Ready backend Traefik answers 503 immediately. A signed-in request needs the DB
//    for the session lookup and errors (reported, not used by the GCP check).
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

// ---------------------------------------------------------------- args
const argv = process.argv.slice(2);
const arg = (n, d) => { const i = argv.indexOf(n); return i >= 0 ? argv[i + 1] : d; };
const SIGO_REPO = arg("--sigo-repo", "/home/dev/repos/sigoalumni-org");
const SIGO_REF = arg("--ref", "origin/main");
const AS_JSON = argv.includes("--json");
// The model is tied to the manifest: the readiness block is parsed from the HelmRelease
// (default: the one in this checkout) and must equal the constants the model assumes.
const HELMRELEASE = arg("--helmrelease",
  new URL("../../kubernetes/main/apps/frontend/sigoalumni/app/helmrelease.yaml", import.meta.url).pathname);

// ---------------------------------------------------------------- deterministic PRNG
function mulberry32(a) {
  return () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

// ---------------------------------------------------------------- probe constants
const PERIOD = 10, TIMEOUT = 5, HEALTH_BUDGET = 2; // s; budget = #164 app health deadline
const LIVE = { period: 30, timeout: 5, threshold: 3 };
// What the plan (decisions 4-5) and the model assume. Parsed manifest values must equal these.
const EXPECTED = {
  readiness: { path: "/api/healthz", port: 8080, periodSeconds: PERIOD, timeoutSeconds: TIMEOUT, successThreshold: 1, failureThreshold: 9 },
  liveness: { tcpSocket: { port: 8080 }, periodSeconds: LIVE.period, timeoutSeconds: LIVE.timeout, failureThreshold: LIVE.threshold },
};

function readManifestProbes(file) {
  const py = "import sys,yaml,json\n" +
    "d=[x for x in yaml.safe_load_all(open(sys.argv[1])) if x and x.get('kind')=='HelmRelease'][0]\n" +
    "pr=d['spec']['values']['controllers']['main']['containers']['app']['probes']\n" +
    "print(json.dumps({k:pr[k]['spec'] for k in ('readiness','liveness')}))";
  const raw = JSON.parse(execFileSync("python3", ["-c", py, file]).toString());
  const r = raw.readiness, l = raw.liveness;
  return {
    readiness: { path: r.httpGet?.path, port: r.httpGet?.port, periodSeconds: r.periodSeconds, timeoutSeconds: r.timeoutSeconds,
      successThreshold: r.successThreshold ?? 1 /* k8s default */, failureThreshold: r.failureThreshold },
    liveness: { tcpSocket: l.tcpSocket, periodSeconds: l.periodSeconds, timeoutSeconds: l.timeoutSeconds, failureThreshold: l.failureThreshold },
  };
}
const MANIFEST = readManifestProbes(HELMRELEASE);
const T = MANIFEST.readiness.failureThreshold; // threshold under test, taken from the manifest

// DB state: list of [from, to) down intervals. downAt(t, overlap).
const mkDb = (intervals) => (t, overlap = 0) => intervals.some(([a, b]) => t < b && t + overlap >= a);

// app responses. mode: "honest" (503 <=2s while DB down), "hang" (no answer; kubelet times out)
function healthz(db, mode = "honest", overlap = 0, failDur = HEALTH_BUDGET) {
  return (t) => {
    if (!db(t, overlap)) return { ok: true, dur: 0.05 };
    return mode === "hang" ? { ok: false, dur: TIMEOUT + 1 } : { ok: false, dur: failDur };
  };
}

/**
 * Simulate one pod's readiness probe worker over [0, horizon]. Returns the readiness
 * timeline (array of {t, ready}), the max consecutive failures, and the first drain time.
 */
function simulateReadiness({ phase, threshold, respond, horizon }) {
  let consec = 0, ready = true, maxConsec = 0, firstDrain = null;
  const timeline = [{ t: 0, ready: true }];
  for (let start = phase; start <= horizon; start += PERIOD) {
    const r = respond(start);
    const ok = r.ok && r.dur <= TIMEOUT;
    const done = start + Math.min(r.dur, TIMEOUT);
    if (ok) {
      consec = 0;
      if (!ready) { ready = true; timeline.push({ t: done, ready: true }); }
    } else {
      consec += 1; maxConsec = Math.max(maxConsec, consec);
      if (consec >= threshold && ready) { ready = false; timeline.push({ t: done, ready: false }); firstDrain ??= done; }
    }
  }
  return { timeline, maxConsec, firstDrain };
}
const readyAt = (tl, t) => { let r = tl[0].ready; for (const e of tl) if (e.t <= t) r = e.ready; return r; };

/** Liveness: tcpSocket never touches the DB -> 0 failures. Counted explicitly, not assumed. */
function simulateLiveness({ phase, horizon, processUp = () => true }) {
  let consec = 0, maxConsec = 0;
  for (let t = phase; t <= horizon; t += LIVE.period) {
    if (processUp(t)) consec = 0; else { consec += 1; maxConsec = Math.max(maxConsec, consec); }
  }
  return { maxConsec, restarted: maxConsec >= LIVE.threshold };
}

// ---------------------------------------------------------------- sweeps
const mm = (xs) => ({ min: +Math.min(...xs).toFixed(2), max: +Math.max(...xs).toFixed(2) });
const PHASES = Array.from({ length: 200 }, (_, i) => i * 0.05); // exhaustive 0..9.95 at 50ms

/** Per-pod drain-delay bound (seconds after outage onset) over the exhaustive phase grid. */
function drainBound(threshold, { mode = "honest", overlap = 0, failDur = HEALTH_BUDGET } = {}) {
  const T0 = 1000;
  const db = mkDb([[T0, T0 + 3600]]);
  const xs = PHASES.map((ph) => {
    const s = simulateReadiness({ phase: ph, threshold, respond: healthz(db, mode, overlap, failDur), horizon: T0 + 600 });
    return s.firstDrain - T0;
  });
  return mm(xs);
}

/** Three pods, random phases; returns per-pod and Service-empty stats for a DB outage [T0, T0+len). */
function threePods({ threshold, len, seed, trials, mode = "honest" }) {
  const rnd = mulberry32(seed), T0 = 1000, db = mkDb([[T0, T0 + len]]);
  const emptied = [], perPodMaxConsec = [], emptyDur = [], livenessRestarts = [];
  const emptyStart = [], recov = [];
  let everEmpty = 0;
  for (let k = 0; k < trials; k++) {
    const pods = [0, 1, 2].map(() => simulateReadiness({
      phase: rnd() * PERIOD, threshold, respond: healthz(db, mode), horizon: T0 + len + 120 }));
    const live = [0, 1, 2].map(() => simulateLiveness({ phase: rnd() * LIVE.period, horizon: T0 + len + 120 }));
    livenessRestarts.push(live.some((l) => l.restarted) ? 1 : 0);
    perPodMaxConsec.push(Math.max(...pods.map((p) => p.maxConsec)));
    // service-empty intervals: sample the union timeline on event times
    const ev = [...new Set(pods.flatMap((p) => p.timeline.map((e) => e.t)))].sort((a, b) => a - b);
    let first = null, last = null, dur = 0, emptyPrev = false, tPrev = 0;
    for (const t of ev) {
      const empty = pods.every((p) => !readyAt(p.timeline, t));
      if (emptyPrev) dur += t - tPrev;
      if (empty && first === null) first = t;
      if (empty) last = t;
      emptyPrev = empty; tPrev = t;
    }
    if (first !== null) { everEmpty++; emptyStart.push(first - T0); emptyDur.push(dur); recov.push(tPrev - (T0 + len)); }
  }
  return { trials, fractionServiceEmptied: everEmpty / trials, perPodMaxConsecFailures: mm(perPodMaxConsec),
    emptyStartAfterOnset: emptyStart.length ? mm(emptyStart) : null,
    emptyDuration: emptyDur.length ? mm(emptyDur) : null,
    livenessRestartFraction: livenessRestarts.reduce((a, b) => a + b, 0) / trials };
}

// ---------------------------------------------------------------- GCP uptime + alert model
const GCP = { checkers: 6, period: 300, timeout: 10, evalEvery: 60, hold: 300, countFalseGt: 1 };

/**
 * pathFn(t) -> {ok, dur} for GET / at time t. Returns the first alert-fire time (s) or null.
 */
function gcpAlert({ phases, pathFn, T0, horizon }) {
  const samples = phases.map((ph) => {
    const s = [];
    for (let t = ph; t <= horizon; t += GCP.period) { const r = pathFn(t); s.push({ t, pass: r.ok && r.dur <= GCP.timeout }); }
    return s;
  });
  const latest = (i, t) => { let v = true; for (const x of samples[i]) if (x.t <= t) v = x.pass; else break; return v; };
  let heldSince = null;
  for (let t = T0 - 600; t <= horizon; t += GCP.evalEvery) {
    const falses = samples.reduce((n, _, i) => n + (latest(i, t) ? 0 : 1), 0);
    if (falses > GCP.countFalseGt) { heldSince ??= t; if (t - heldSince >= GCP.hold) return { fire: t, samples }; }
    else heldSince = null;
  }
  return { fire: null, samples };
}

/** `/` as seen from outside, from the pods' readiness timelines (anonymous, DB erroring). */
const rootFromPods = (podTimelines, { slowSec = 0 } = {}) => (t) =>
  podTimelines.some((tl) => readyAt(tl, t)) ? { ok: true, dur: slowSec || 0.2 } : { ok: false, dur: 0.05 };

// ---------------------------------------------------------------- watchdog (imported unmodified)
async function loadWatchdog() {
  try {
    const dir = mkdtempSync(join(tmpdir(), "sigo-watchdog-"));
    const rev = execFileSync("git", ["-C", SIGO_REPO, "rev-parse", "--short=12", SIGO_REF]).toString().trim();
    for (const f of ["decide.mjs", "steer.mjs"])
      writeFileSync(join(dir, f), execFileSync("git", ["-C", SIGO_REPO, "show", `${SIGO_REF}:watchdog/lib/${f}`]));
    const steer = await import(pathToFileURL(join(dir, "steer.mjs")).href);
    const decide = await import(pathToFileURL(join(dir, "decide.mjs")).href);
    return { steer, decide, rev };
  } catch (e) { return { error: String(e.message).split("\n")[0] }; }
}

/** Run handleAlert with every effect mocked on a virtual clock. probeFn(t)->bool healthy. */
async function runSteer(wd, { startAt, probeFn, records, lastSteer = null, probeCostMs = 1000 }) {
  let now = startAt * 1000;
  const calls = { steer: 0, dnsRead: 0, notify: [], probes: [] };
  const deps = {
    log: { log() {}, error() {} },
    dns: { getApexState: async () => { calls.dnsRead++; return { records }; }, steer: async () => { calls.steer++; } },
    probeOnce: async () => { const up = probeFn(now / 1000); calls.probes.push({ t: +(now / 1000).toFixed(1), up }); now += probeCostMs; return up; },
    verify: async () => ({ ok: true, elapsedMs: 1000 }),
    state: { getLastSteer: async () => lastSteer, setLastSteer: async () => {} },
    notify: async (phase) => { calls.notify.push(phase); },
    ping: async () => ({ status: 200, ok: true, body: "" }),
    sleep: async (ms) => { now += ms; }, now: () => now,
    config: { rateLimitMs: wd.decide.RATE_LIMIT_MS, probeIntervalMs: 25000, projectId: "p", zoneId: "z", stateBucket: "b" },
  };
  const res = await wd.steer.handleAlert(deps);
  return { res, calls };
}

// ================================================================ main
const out = {};
out.manifest = { file: HELMRELEASE, parsed: MANIFEST, expected: EXPECTED, thresholdUnderTest: T };
const wd = await loadWatchdog();
out.watchdogSource = wd.error ? { skipped: wd.error } : { repo: "sigoalumni-org", ref: SIGO_REF, rev: wd.rev };

// -- drain bounds
const envelope = (...b) => ({ min: Math.min(...b.map((x) => x.min)), max: Math.max(...b.map((x) => x.max)) });
const fast3 = drainBound(3, { failDur: 0.05 }), fast9 = drainBound(T, { failDur: 0.05 });
const bud3 = drainBound(3), bud9 = drainBound(T);
out.drainBound = {
  note: "seconds from outage onset until the pod leaves the Service; honest 503 answered in 0.05s (fast) or at the full 2s app budget",
  threshold3_fast503: fast3, threshold3_503AtBudget2s: bud3, threshold3_envelope: envelope(fast3, bud3),
  thresholdPR_fast503: fast9, thresholdPR_503AtBudget2s: bud9, thresholdPR_envelope: envelope(fast9, bud9),
  threshold3_overlap2s: drainBound(3, { overlap: 2 }), thresholdPR_overlap2s: drainBound(T, { overlap: 2 }),
  threshold3_hung5sTimeout: drainBound(3, { mode: "hang" }), thresholdPR_hung5sTimeout: drainBound(T, { mode: "hang" }),
  phaseGrid: "200 phases at 50ms over one 10s period, exhaustive",
};

// -- R1: 30s shared DB loss, honest 503
const R1 = { len: 30, trials: 20000 };
R1.thresholdPR = threePods({ threshold: T, len: 30, seed: 11, trials: R1.trials });
R1.threshold3_baseline = threePods({ threshold: 3, len: 30, seed: 11, trials: R1.trials });
{ // sensitivity: a request fails if DB is down at any instant of its 2s budget
  const rnd = mulberry32(5), T0 = 1000, db = mkDb([[T0, T0 + 30]]); let worst = 0;
  for (let k = 0; k < 20000; k++) worst = Math.max(worst, simulateReadiness({ phase: rnd() * 10, threshold: T, respond: healthz(db, "honest", HEALTH_BUDGET), horizon: T0 + 150 }).maxConsec);
  R1.thresholdPR_overlap2s_maxConsecFailures = worst;
}
out.R1 = R1;

// -- R2: sustained fast errors
const R2 = {};
R2.pods120s_thresholdPR = threePods({ threshold: T, len: 120, seed: 21, trials: 20000 });
R2.pods120s_threshold3_baseline = threePods({ threshold: 3, len: 120, seed: 21, trials: 20000 });
// alert + steer chain for a SUSTAINED outage (long enough for the 300s hold), 500 phase draws
async function chain(threshold, len, trials, seed) {
  const rnd = mulberry32(seed), T0 = 3000, fires = [], emptyAt = [], steerAt = [], decideAt = [], conf = [], steerCalls = [];
  let fired = 0;
  for (let k = 0; k < trials; k++) {
    const db = mkDb([[T0, T0 + len]]);
    const pods = [0, 1, 2].map(() => simulateReadiness({ phase: rnd() * 10, threshold, respond: healthz(db), horizon: T0 + len + 60 }));
    const tls = pods.map((p) => p.timeline);
    const ev = pods.flatMap((p) => p.timeline.map((e) => e.t)).sort((a, b) => a - b);
    const tEmpty = ev.find((t) => tls.every((tl) => !readyAt(tl, t)) && t >= T0);
    const phases = Array.from({ length: GCP.checkers }, () => rnd() * GCP.period);
    const a = gcpAlert({ phases, pathFn: rootFromPods(tls), T0, horizon: T0 + len + 600 });
    if (tEmpty != null) emptyAt.push(tEmpty - T0);
    if (a.fire == null) continue;
    fired++; fires.push(a.fire - T0);
    if (wd.error) continue;
    const probeFn = (t) => t >= T0 && t < T0 + len ? false : true; // healthz via Traefik: 503 while no backend / DB down
    const s = await runSteer(wd, { startAt: a.fire, probeFn, records: [{ type: "CNAME", content: "x.cfargotunnel.com" }] });
    steerCalls.push(s.calls.steer); conf.push(s.calls.probes.filter((p) => !p.up).length);
    steerAt.push(s.calls.probes[0].t - T0); decideAt.push(s.calls.probes[2].t + 1 - T0);
  }
  return { threshold, outageLenS: len, trials, alertFiredFraction: fired / trials,
    serviceEmptyAfterOnset: emptyAt.length ? mm(emptyAt) : null,
    alertFireAfterOnset: fires.length ? mm(fires) : null,
    watchdogFirstConfirmAfterOnset: steerAt.length ? mm(steerAt) : "skipped",
    steerDecisionAfterOnset: decideAt.length ? mm(decideAt) : "skipped (wake->confirm delay of the Pub/Sub path is NOT modeled, 0s assumed)",
    failingConfirms: conf.length ? mm(conf) : "skipped",
    mockedSteerCallsPerAlert: steerCalls.length ? mm(steerCalls) : "skipped",
    alertsFired: fired, totalMockedSteers: steerCalls.reduce((a, b) => a + b, 0) };
}
R2.chain_thresholdPR_sustained1800s = await chain(T, 1800, 500, 31);
R2.chain_threshold3_baseline_sustained1800s = await chain(3, 1800, 500, 31);
R2.chain_thresholdPR_120sOutage = await chain(T, 120, 500, 32);
out.R2 = R2;

// -- R3: not touched by this PR; characterise the existing code once (authoritative tests: H5)
if (!wd.error) {
  const tun = [{ type: "CNAME", content: "t.cfargotunnel.com" }];
  const steered = [...wd.decide.GOOGLE_A.map((c) => ({ type: "A", content: c })), ...wd.decide.GOOGLE_AAAA.map((c) => ({ type: "AAAA", content: c }))];
  const rec = await runSteer(wd, { startAt: 0, records: tun, probeFn: (t) => t >= 26 }); // recovers at confirm #2
  const done = await runSteer(wd, { startAt: 0, records: steered, probeFn: () => false });
  const rl = await runSteer(wd, { startAt: 0, records: tun, probeFn: () => false, lastSteer: -30 * 60 * 1000 });
  out.R3 = {
    note: "Characterization only. This PR edits no watchdog code (git diff --name-only shows helmrelease.yaml and scripts/sigoalumni-readiness-trace/ only). Authoritative R3 tests belong to H5.",
    healthRecoversDuringConfirm: { action: rec.res.action, steers: rec.calls.steer },
    alreadySteered: { action: done.res.action, steers: done.calls.steer, probes: done.calls.probes.length },
    steeredLessThanHourAgo: { action: rl.res.action, steers: rl.calls.steer },
    outageAgePredicate: await (async () => {
      const src = ["decide.mjs", "steer.mjs"].map((f) => execFileSync("git", ["-C", SIGO_REPO, "show", `${SIGO_REF}:watchdog/lib/${f}`]).toString()).join("\n");
      const code = src.split("\n").filter((l) => !/^\s*\/\//.test(l)).join("\n");
      const hit = /\b(420|420000|7\s*\*\s*60|minutes?\s*\(\s*7)\b/.test(code);
      return hit ? "FOUND a possible seven-minute constant: review" : "none: no 7-minute / 420s constant in non-comment code of decide.mjs, steer.mjs";
    })(),
  };
} else out.R3 = { skipped: wd.error };

// -- R4
const R4 = {};
{ // slow `/` (12s > 10s GCP timeout) while healthz stays 200 and readiness stays ready
  const T0 = 3000, len = 1800, rnd = mulberry32(41), fires = []; let notDrained = 0, fired = 0, declined = 0, steers = 0, trials = 300;
  const slowRoot = (t) => (t >= T0 && t < T0 + len ? { ok: true, dur: 12 } : { ok: true, dur: 0.2 });
  for (let k = 0; k < trials; k++) {
    const phases = Array.from({ length: GCP.checkers }, () => rnd() * GCP.period);
    const a = gcpAlert({ phases, pathFn: slowRoot, T0, horizon: T0 + len });
    const pods = [0, 1, 2].map(() => simulateReadiness({ phase: rnd() * 10, threshold: T, respond: healthz(mkDb([])), horizon: T0 + len }));
    if (pods.every((p) => p.firstDrain === null)) notDrained++;
    if (a.fire != null) {
      fired++; fires.push(a.fire - T0);
      if (!wd.error) { const s = await runSteer(wd, { startAt: a.fire, probeFn: () => true, records: [{ type: "CNAME", content: "t.cfargotunnel.com" }] }); if (s.res.action === "declined-healthy") declined++; steers += s.calls.steer; }
    }
  }
  R4.slowRoot_readinessStaysReady = { trials, podsNeverDrainedFraction: notDrained / trials, uptimeAlertFiredFraction: fired / trials,
    alertFireAfterOnset: fires.length ? mm(fires) : null, watchdogDeclinedHealthyFraction: wd.error ? "skipped" : declined / Math.max(fired, 1), mockedSteers: wd.error ? "skipped" : steers };
}
{ // negative control: process-only readiness (always 200) + fast fallback `/`, DB fully down 1800s
  const T0 = 3000, len = 1800, rnd = mulberry32(42), trials = 300; let drained = 0, fired = 0;
  const processOnly = () => ({ ok: true, dur: 0.01 });
  for (let k = 0; k < trials; k++) {
    const pods = [0, 1, 2].map(() => simulateReadiness({ phase: rnd() * 10, threshold: T, respond: processOnly, horizon: T0 + len }));
    if (pods.some((p) => p.firstDrain !== null)) drained++;
    const phases = Array.from({ length: GCP.checkers }, () => rnd() * GCP.period);
    const a = gcpAlert({ phases, pathFn: rootFromPods(pods.map((p) => p.timeline)), T0, horizon: T0 + len });
    if (a.fire != null) fired++;
  }
  const dbSensitive = await chain(T, len, 100, 43);
  R4.negativeControl_processOnlyReadiness_dbDown1800s = { trials, anyPodDrainedFraction: drained / trials, uptimeAlertFiredFraction: fired / trials,
    comparison_dbSensitiveReadiness_alertFiredFraction: dbSensitive.alertFiredFraction,
    conclusion: "with process-only readiness a DB outage never empties the Service, the anonymous fast `/` stays 200, no alert wakes the watchdog, no steer; DB-sensitive readiness is what turns it into an alert" };
}
out.R4 = R4;

// ---------------------------------------------------------------- assertions (exit non-zero on regression)
const fails = [];
const need = (c, m) => { if (!c) fails.push(m); };
for (const probe of ["readiness", "liveness"])
  for (const [k, v] of Object.entries(EXPECTED[probe]))
    need(JSON.stringify(MANIFEST[probe][k]) === JSON.stringify(v), `manifest ${probe}.${k}=${JSON.stringify(MANIFEST[probe][k])} but the model assumes ${JSON.stringify(v)}`);
need(out.drainBound.thresholdPR_envelope.min >= 79.9 && out.drainBound.thresholdPR_envelope.max <= 92.1, "threshold 9 drain bound outside 80-92s");
need(out.drainBound.threshold3_envelope.min >= 19.9 && out.drainBound.threshold3_envelope.max <= 32.1, "threshold 3 drain bound outside 20-32s");
need(R1.thresholdPR.perPodMaxConsecFailures.max < 9 && R1.thresholdPR.fractionServiceEmptied === 0, "R1: a pod reached 9 consecutive failures at threshold 9");
need(R1.thresholdPR.livenessRestartFraction === 0, "R1: liveness affected");
need(R1.threshold3_baseline.fractionServiceEmptied > 0, "R1 baseline: threshold 3 should drain on a 30s blip");
need(R2.pods120s_thresholdPR.fractionServiceEmptied === 1, "R2: not every trial emptied the Service within 120s");
need(R2.pods120s_thresholdPR.emptyStartAfterOnset.max <= 92.1, "R2: Service-empty bound > 92s");
need(R2.chain_thresholdPR_sustained1800s.alertFiredFraction === 1, "R2: sustained outage did not always alert");
need(R1.thresholdPR_overlap2s_maxConsecFailures < T, "R1: overlap-sensitivity run reached the threshold");
if (!wd.error) {
  need(R2.chain_thresholdPR_sustained1800s.failingConfirms.min === 3 && R2.chain_thresholdPR_sustained1800s.failingConfirms.max === 3, "R2: not exactly three failing confirms");
  need(R2.chain_thresholdPR_sustained1800s.mockedSteerCallsPerAlert.min === 1 && R2.chain_thresholdPR_sustained1800s.mockedSteerCallsPerAlert.max === 1, "R2: not exactly one mocked steer");
  // A rare alert in the 120s case (stale forward-filled samples, see README) must never lead to a steer.
  need(R2.chain_thresholdPR_120sOutage.totalMockedSteers === 0, "R2: an alert in the 120s case led to a steer");
  need(out.R3.healthRecoversDuringConfirm.action === "declined-healthy" && out.R3.alreadySteered.action === "already-steered" && out.R3.steeredLessThanHourAgo.action === "rate-limited", "R3 characterization changed");
  need(R4.slowRoot_readinessStaysReady.watchdogDeclinedHealthyFraction === 1 && R4.slowRoot_readinessStaysReady.mockedSteers === 0, "R4: slow `/` should alert but be declined by the watchdog");
}
need(R4.slowRoot_readinessStaysReady.podsNeverDrainedFraction === 1 && R4.slowRoot_readinessStaysReady.uptimeAlertFiredFraction === 1, "R4: slow `/` independence not shown");
need(R4.negativeControl_processOnlyReadiness_dbDown1800s.uptimeAlertFiredFraction === 0 && R4.negativeControl_processOnlyReadiness_dbDown1800s.anyPodDrainedFraction === 0, "R4: negative control did not mask the DB outage");
out.assertions = fails.length ? { failed: fails } : "all passed";

console.log(JSON.stringify(out, null, AS_JSON ? 0 : 2));
process.exit(fails.length ? 1 : 0);
