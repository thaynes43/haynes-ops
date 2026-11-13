#!/usr/bin/env node
// night-claim.mjs — who handles a regression: the night lane's claim on a regression
// signature in ConfigMap upgrade-agent/upgrade-remediation-state (story 554).
//
// Story: https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/554
// Design: OP512 comment 6193, "Regression" and rollout step 4. Runbook:
// .agents/runbooks/upgrade-night-lane.md, "Who handles a regression (story 554)".
// Node 24 only, no dependencies.
//
// THE KEY. Triage (shepherd/app/resources/triage.sh) and the health gate (gate.sh) key a
// regression by the signature coordination-lib.sh computes: collect_regressions (Flux
// Kustomizations and HelmReleases NotReady past 600 s, plus persisted-unhealthy pods after
// the library's corpse filters), then sig_of(sig_key_of(REG_IDS)). The lane does not
// re-implement that: coordinationSignature() runs the library itself, the same file mounted
// at /opt/coordination in the lane's pod, so the lane and the library compute the same
// signature for the same regression set by construction. The library's contract is
// read-only (kubectl get, one Prometheus query, sha256).
//
// THE CLAIM. An entry under the signature in the claim format triage and the gate already
// read: class=upgrade, attempted=1, result, note, first_seen, last_paged; plus the lane's own
// fields (holder=lane, pr, lane_state, claimed_at, until, ids). attempted=1 is triage's
// at-most-once marker, so even a triage that knows nothing of the lane does not summon
// remediation for a signature the lane holds. The lane's states and the result each maps to:
//   working       -> inprogress  (the gate is silent until `until`, the lane's bound on its own
//                                 action; after it the gate pages as for a failed fix)
//   reverted      -> fixed       (story 753: the revert merged and recovery verified)
//   revert-failed -> failed      (story 753: the revert did not verify; the gate pages)
//
// ONE HOLDER. A claim is a create (the ConfigMap is absent) or a replace at the
// resourceVersion of the read the decision was made on, so two actors that read the same
// version cannot both write: the API server refuses the second with Conflict (or
// AlreadyExists). The loser reads again, finds the winner, and stands down. Triage's claim
// (triage.sh state_claim) does the same. No actor reads another's schedule.
//
// HOLDS. The lane stops for an update when the signature's entry has attempted=1 and is not
// the lane's (triage holds it), when another entry triage is still working on (attempted=1,
// result none or pending) names one of the target's ids, when another lane claim holds it, or
// when the state or the entry cannot be read or understood: the lane does not revert then.
//
// DRY-RUN AND PRACTICE. Only a live tick, for a merge the lane made itself, writes. In dry-run
// and on practice verdicts (merges the lane did not make) the step reads only: it records the
// signature it would claim and whether triage holds it. writeState() is the one write site; it
// refuses any ConfigMap but upgrade-agent/upgrade-remediation-state, and a replace without a
// resourceVersion.
import { spawnSync } from 'node:child_process';
import * as M from './night-merge.mjs';

export const STATE = Object.freeze({ namespace: 'upgrade-agent', name: 'upgrade-remediation-state' });
export const CLAIM_SCHEMA = 'haynes.upgrade-claim/v1';
export const LANE_RESULT = Object.freeze({ working: 'inprogress', reverted: 'fixed', 'revert-failed': 'failed' });
export const DEFAULT_LIB = '/opt/coordination/coordination-lib.sh';
const KUBECTL_TIMEOUT_MS = 30e3;
const SIG_TIMEOUT_MS = 90e3;
const SIG_RE = /^[0-9a-f]{12}$/;
const parse = s => { try { return JSON.parse(s); } catch { return null; } };
const sec = ms => Math.floor(ms / 1000);

// rules.night.claim, checked. max_work_seconds: the lane's bound on its own action (claim to
// verified revert); gate.sh refuses an `until` more than LANE_CLAIM_MAX_SECONDS (21600) after
// claimed_at, so the bound stays under that.
export function claimConfig(rules) {
  const c = rules?.night?.claim || {};
  const ok = Number.isInteger(c.max_work_seconds) && c.max_work_seconds >= 1800 && c.max_work_seconds <= 21600;
  return { ok, max_work_seconds: ok ? c.max_work_seconds : null, why: ok ? null : 'night.claim.max_work_seconds in upgrade-rules.json must be an integer from 1800 to 21600' };
}

// ── the signature: the library itself ───────────────────────────────────────────────
// A fixed script: no text from outside reaches it; NOW, PROM and COORD_LIB ride the
// environment. Output lines: PODS <ok|blind>, SIG <12 hex> (when the set is not empty), ID <id>.
export const SIG_SCRIPT = [
  'set -u',
  '. "$COORD_LIB" || { echo "LIB unreadable"; exit 9; }',
  'collect_regressions',
  'printf \'PODS %s\\n\' "$SIG_PODS_STATUS"',
  '[ -n "$REG_IDS" ] && printf \'SIG %s\\n\' "$(sig_of "$(sig_key_of "$REG_IDS")")"',
  'printf \'%s\\n\' "$REG_IDS" | sed -e \'/^$/d\' -e \'s/^/ID /\'',
  'exit 0',
].join('\n');

export function parseSignatureOutput(stdout) {
  const ids = [];
  let sig = null, pods = null;
  for (const line of String(stdout || '').split('\n')) {
    if (line.startsWith('ID ')) ids.push(line.slice(3));
    else if (line.startsWith('SIG ')) sig = line.slice(4).trim();
    else if (line.startsWith('PODS ')) pods = line.slice(5).trim();
  }
  if (!pods) return { ok: false, why: 'the library printed no result' };
  if (ids.length && !SIG_RE.test(sig || '')) return { ok: false, why: `the library printed no signature for ${ids.length} id(s)` };
  return { ok: true, ids, sig: ids.length ? sig : null, pods };
}

export function coordinationSignature({ now, prom, env = process.env, lib = DEFAULT_LIB, spawn = spawnSync, timeoutMs = SIG_TIMEOUT_MS }) {
  const r = spawn('bash', ['-c', SIG_SCRIPT], { input: '', encoding: 'utf8', timeout: timeoutMs, killSignal: 'SIGKILL', maxBuffer: 8 * 1024 * 1024,
    env: { ...env, NOW: String(sec(now)), PROM: String(prom || ''), COORD_LIB: lib } });
  if (r.status !== 0) return { ok: false, why: r.status === 9 ? `the coordination library ${lib} could not be read` : `the coordination library did not finish (${r.status === null ? `killed: ${r.signal || r.error?.code || 'timeout'}` : `exit ${r.status}`})` };
  return parseSignatureOutput(r.stdout);
}

// ── which ids of the set belong to the update's target ─────────────────────────────────
// rec: the verifier's record (night-verify.mjs): scope.own (Kustomizations ns/name), scope.hr
// (HelmReleases ns/name), workloads ("Kind ns/name"). A pod belongs when its name is one its
// workload's controller makes.
const esc = s => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const POD_NAME = {
  Deployment: n => `^${n}-[a-z0-9]{5,10}-[a-z0-9]{5}$`,
  StatefulSet: n => `^${n}-[0-9]+$`,
  DaemonSet: n => `^${n}-[a-z0-9]{5}$`,
  CronJob: n => `^${n}-[0-9]{8,}-[a-z0-9]{5}$`,
};
export function targetIds(ids, rec) {
  const ks = new Set(rec?.scope?.own || []), hr = new Set(rec?.scope?.hr || []);
  const wl = (rec?.workloads || []).map(s => { const [kind, nn = ''] = String(s).split(' '); const i = nn.indexOf('/'); return { kind, ns: nn.slice(0, i), name: nn.slice(i + 1) }; })
    .filter(w => POD_NAME[w.kind] && w.ns && w.name);
  return (ids || []).filter(id => {
    const p = String(id).split('/');
    if (p[0] === 'flux' && p.length >= 4) {
      const key = `${p[2]}/${p.slice(3).join('/')}`;
      return (p[1] === 'Kustomization' && ks.has(key)) || (p[1] === 'HelmRelease' && hr.has(key));
    }
    if (p[0] === 'pod' && p.length >= 3) {
      const ns = p[1], pod = p.slice(2).join('/');
      return wl.some(w => w.ns === ns && new RegExp(POD_NAME[w.kind](esc(w.name))).test(pod));
    }
    return false;
  });
}

// ── reading a claim ─────────────────────────────────────────────────────────────────
// PURE. read = M.readNamedConfigMap() result (null = the read failed). Returns
// { state, rv, found, entry, holder, why }: state is free | triage | lane-ours | lane-other |
// unreadable. mine: the target's ids (for a hold under another signature).
const inFlight = e => ['', 'none', 'pending', 'inprogress'].includes(String(e?.result ?? ''));
export function claimStatus(read, sig, { pr = null, mine = [], now = Date.now() } = {}) {
  if (!read) return { state: 'unreadable', why: `unreadable: ConfigMap ${STATE.namespace}/${STATE.name} (the read failed)` };
  if (!read.found) return { state: 'free', found: false, rv: null, entry: null };
  const rv = read.cm?.metadata?.resourceVersion || null;
  if (!rv) return { state: 'unreadable', why: `unreadable: ConfigMap ${STATE.namespace}/${STATE.name} has no resourceVersion` };
  const data = read.cm?.data || {};
  const raw = data[sig];
  let entry = null;
  if (raw !== undefined && raw !== '') {
    entry = parse(raw);
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return { state: 'unreadable', rv, found: true, why: `the entry for signature ${sig} is not a JSON object` };
    if (entry.holder === 'lane') {
      return String(entry.pr) === String(pr) ? { state: 'lane-ours', rv, found: true, entry, holder: 'lane' }
        : { state: 'lane-other', rv, found: true, entry, holder: 'lane', why: `the night lane holds signature ${sig} for #${entry.pr} (${entry.lane_state || 'working'})` };
    }
    if (String(entry.attempted) === '1') return { state: 'triage', rv, found: true, entry, holder: 'triage', why: `triage holds signature ${sig} (attempted=1, result ${entry.result || 'none'})` };
  }
  // a hold under another signature: an entry still in flight that names one of the target's ids
  const mineSet = new Set(mine);
  for (const [k, v] of Object.entries(data)) {
    if (k === sig) continue;
    const e = parse(v);
    if (!e || !Array.isArray(e.ids) || !e.ids.some(i => mineSet.has(i))) continue;
    if (e.holder === 'lane' && String(e.pr) !== String(pr) && e.lane_state === 'working' && Number(e.until) * 1000 > now) return { state: 'lane-other', rv, found: true, entry, other_sig: k, holder: 'lane', why: `the night lane holds ${k} for #${e.pr}, which names ${e.ids.filter(i => mineSet.has(i)).join(', ')}` };
    if (e.holder !== 'lane' && String(e.attempted) === '1' && inFlight(e)) return { state: 'triage', rv, found: true, entry, other_sig: k, holder: 'triage', why: `triage holds ${k} (result ${e.result || 'none'}), which names ${e.ids.filter(i => mineSet.has(i)).join(', ')}` };
  }
  return { state: 'free', found: true, rv, entry };
}

// The lane's entry: the claim format triage and the gate read, plus the lane's fields. It keeps
// last_paged (the gate's page de-dupe) and anything else the entry held; first_seen becomes the
// claim time, so the 24-hour age-out (triage's STATE_TTL_HOURS) counts from the claim.
export function laneEntry({ prev = null, pr, item = null, mine, now, workSeconds, note }) {
  const t = sec(now);
  return { ...(prev || {}), class: 'upgrade', attempted: '1', result: LANE_RESULT.working,
    note: String(note || '').replace(/[\u0000-\u001f\u007f]/g, ' ').slice(0, 300),
    first_seen: String(t), last_paged: prev?.last_paged ?? '0',
    holder: 'lane', schema: CLAIM_SCHEMA, pr: String(pr), ...(item ? { item } : {}), lane_state: 'working',
    claimed_at: String(t), until: String(t + workSeconds), ids: [...mine] };
}

const manifestOf = (read, data) => ({ apiVersion: 'v1', kind: 'ConfigMap',
  metadata: { name: STATE.name, namespace: STATE.namespace, ...(read?.found && read.cm?.metadata?.resourceVersion ? { resourceVersion: read.cm.metadata.resourceVersion } : {}),
    ...(read?.found ? (read.cm?.metadata?.labels ? { labels: read.cm.metadata.labels } : {}) : { labels: { 'app.kubernetes.io/name': 'upgrade-shepherd', 'app.kubernetes.io/component': 'upgrade-remediation-state' } }) },
  data });

// THE ONE CLUSTER WRITE OF THIS FILE: create or replace ConfigMap upgrade-remediation-state at
// the resourceVersion that was read. Conflict or AlreadyExists when anyone wrote in between.
export function writeState(verb, manifest, env, spawn = spawnSync) {
  const m = manifest?.metadata || {};
  if (!['create', 'replace'].includes(verb) || manifest?.kind !== 'ConfigMap' || m.name !== STATE.name || m.namespace !== STATE.namespace
    || Object.keys(manifest).some(k => !['apiVersion', 'kind', 'metadata', 'data'].includes(k)) || (verb === 'replace' && !m.resourceVersion) || (verb === 'create' && m.resourceVersion)) {
    throw new Error('writeState: refused (only ConfigMap upgrade-agent/upgrade-remediation-state, by create or by replace at a resourceVersion)');
  }
  const r = spawn('kubectl', [verb, '-f', '-', '-o', 'json'], { input: JSON.stringify(manifest), encoding: 'utf8', env, timeout: KUBECTL_TIMEOUT_MS });
  if (r.status !== 0) return { ok: false, conflict: /conflict|already exists|has been modified/i.test(String(r.stderr || '')), detail: M.publicDetail(r.stderr || r.error?.message) };
  return { ok: true, rv: parse(r.stdout)?.metadata?.resourceVersion || null };
}

// ── the step, per failed verdict ────────────────────────────────────────────────────
// rec: the verifier's record; live: a live tick and a merge the lane made (cand.own); work:
// claimConfig(). Returns the outcome record for summary.claims (and the morning summary):
//   outcome: not-in-set | would-claim | claimed | triage-holds | lane-holds | lost |
//            unreadable | not-own-regression
//   revert_allowed: true only when the lane holds the claim (or no actor sees the regression).
// deps: { signature: () => coordinationSignature(...) result, read: () => readNamedConfigMap,
//         write: (verb, manifest) => writeState result, beforeWrite (tests only) }.
export function claimStep({ rec, live, now, work, deps }) {
  const out = { pr: rec.pr, item: rec.item ?? null, practice: !!rec.practice, attribution: rec.attribution ?? null, live: !!live, sig: null, ids: [], mine: [], pods: null, outcome: null, holder: null, revert_allowed: false, wrote: false, why: null };
  const done = (outcome, why, extra = {}) => Object.assign(out, { outcome, why, ...extra });
  if (rec.verdict !== 'failed') return done('not-own-regression', `verdict ${rec.verdict}: nothing to claim`);
  const s = deps.signature();
  if (!s?.ok) return done('unreadable', `the signature could not be computed: ${s?.why || 'no result'}; the lane does not revert`);
  out.ids = s.ids; out.pods = s.pods; out.sig = s.sig;
  out.mine = targetIds(s.ids, rec);
  if (!out.mine.length) {
    // triage and the gate do not see this regression: no signature of theirs names the target
    return done('not-in-set', `${s.ids.length ? `the coordination set (${s.ids.length} id(s), signature ${s.sig})` : 'the coordination set is empty: it'} names nothing of #${rec.pr}'s target: triage and the gate do not see this regression, so there is nothing to claim`,
      { revert_allowed: live && rec.attribution === 'own-regression' });
  }
  // the decision and the write use the same read: a replace carries its resourceVersion
  let read = deps.read();
  const st = claimStatus(read, s.sig, { pr: rec.pr, mine: out.mine, now });
  out.holder = st.holder ?? null;
  if (st.state === 'unreadable') return done('unreadable', `${st.why}; the lane does not revert`);
  if (st.state === 'triage') return done('triage-holds', `${st.why}: triage owns it${live ? `; the lane stops for #${rec.pr} and does not revert` : ''}`);
  if (st.state === 'lane-other') return done('lane-holds', `${st.why}; the lane does not revert #${rec.pr} as well`);
  if (st.state === 'lane-ours') return done('claimed', `the lane already holds signature ${s.sig} for #${rec.pr} (${st.entry.lane_state})`, { revert_allowed: live && st.entry.lane_state === 'working' });
  if (!live || rec.attribution !== 'own-regression') {
    return done(rec.attribution === 'own-regression' ? 'would-claim' : 'not-own-regression',
      `${rec.attribution === 'own-regression' ? `would claim signature ${s.sig}` : `cannot tell which change did it (a person owns it); signature ${s.sig}`}: free, triage does not hold it${live ? '' : ' (read only: practice or dry-run writes nothing)'}`);
  }
  // live, the lane's own merge, its own regression, and nobody holds it: claim
  let status = st;
  for (let attempt = 1; attempt <= 2; attempt++) {
    if (status.state !== 'free') break;
    const data = { ...(read?.found ? read.cm.data || {} : {}) };
    data[s.sig] = JSON.stringify(laneEntry({ prev: status.entry, pr: rec.pr, item: rec.item, mine: out.mine, now, workSeconds: work.max_work_seconds,
      note: `night lane: #${rec.pr} ${rec.name ?? ''} ${rec.from ?? '?'} -> ${rec.to ?? '?'} failed its verification (${(rec.failing || []).map(f => `${f.fact} ${f.class}`).join(', ')}); the lane reverts it` }));
    deps.beforeWrite?.();
    const w = deps.write(read?.found ? 'replace' : 'create', manifestOf(read, data));
    if (w.ok) return done('claimed', `claimed signature ${s.sig} for #${rec.pr} (${read?.found ? 'replace at resourceVersion ' + read.cm.metadata.resourceVersion : 'create'}); bound ${work.max_work_seconds} s`, { wrote: true, holder: 'lane', revert_allowed: true });
    // the loser reads the winner and stands down; a write that failed for another reason too
    read = deps.read(); status = claimStatus(read, s.sig, { pr: rec.pr, mine: out.mine, now });
    if (status.state === 'lane-ours') return done('claimed', `the claim of ${s.sig} landed (read back after: ${w.detail})`, { wrote: true, holder: 'lane', revert_allowed: true });
    if (status.state !== 'free' || !w.conflict) {
      return done(status.state === 'triage' ? 'lost' : status.state === 'unreadable' ? 'unreadable' : status.state === 'lane-other' ? 'lane-holds' : 'lost',
        `the claim of ${s.sig} was refused (${w.detail}); ${status.why || 'read again: still free'}; the lane stands down and does not revert`, { holder: status.holder ?? null });
    }
  }
  return done('lost', `the claim of ${s.sig} conflicted twice with other writes; the lane does not revert this tick`);
}

// For story 753: move the lane's own claim to reverted or revert-failed (the result the gate
// reads follows). Only the lane's claim for this PR, at the resourceVersion read.
export function updateClaim({ sig, pr, laneState, note = '', now, deps }) {
  if (!Object.keys(LANE_RESULT).includes(laneState)) return { ok: false, why: `unknown lane_state ${laneState}` };
  const read = deps.read();
  const st = claimStatus(read, sig, { pr, now });
  if (st.state !== 'lane-ours') return { ok: false, why: st.why || `signature ${sig} is not the lane's claim for #${pr} (${st.state})` };
  const e = { ...st.entry, lane_state: laneState, result: LANE_RESULT[laneState], updated_at: String(sec(now)), ...(note ? { note: String(note).replace(/[\u0000-\u001f\u007f]/g, ' ').slice(0, 300) } : {}) };
  const w = deps.write('replace', manifestOf(read, { ...(read.cm.data || {}), [sig]: JSON.stringify(e) }));
  return w.ok ? { ok: true, rv: w.rv } : { ok: false, why: `refused: ${w.detail}` };
}

// The tick's wiring: the deps of claimStep over the real runner and spawn sites.
export function liveDeps({ run, env, now, prom, lib = process.env.COORD_LIB || DEFAULT_LIB }) {
  let sig = null;
  return {
    signature: () => (sig ??= coordinationSignature({ now, prom, env, lib })),
    read: () => M.readNamedConfigMap(STATE.name, run),
    write: (verb, manifest) => writeState(verb, manifest, env),
  };
}

// The tick's log line.
export function claimLine(claims) {
  if (!claims?.length) return 'no failed verdict this tick';
  return claims.map(c => `#${c.pr} ${c.outcome}${c.sig ? ` sig=${c.sig}` : ''}${c.mine?.length ? ` (${c.mine.length} id(s) of its target)` : ''}${c.revert_allowed ? '; revert allowed' : ''}`).join('; ');
}
