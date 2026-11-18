#!/usr/bin/env node
// night-summary.mjs — the morning summary of the night lane (story 554).
//
// Story: https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/554
// Design: OP512 comment 6193 ("A 06:30 New York digest at low priority says what merged, what
// was reverted and what waits for him"). Runbook: .agents/runbooks/upgrade-night-lane.md,
// "The morning summary (story 554)". Node 24 only, no dependencies.
//
// WHAT. Every tick composes, from what the tick already holds (no read of its own, and no
// GitHub read): the open-PR records (the table), the verifier's records (data.verify), the
// history document (upgrade-ledger, as the history phase left it), the history's own verdict
// and the claims step. The text is in plain words, at most night.summary.max_bytes (1000, under
// Pushover's 1,024-character cap and page.sh's 1,000 clamp); when it does not fit, whole lines
// go from UPGRADED first and the last line says "+N more". The full lists stay in the facts.
//
// WHERE. run-night.mjs writes the record (text, facts, when it was composed and how many of
// the night's ticks completed) to upgrade-lane-state data.morning under the field manager
// upgrade-night-summary, and a delivery copy (morning_* keys only) to ConfigMap
// upgrade-orphan-report, the report the health gate already reads and may update (its Role is
// name-scoped get and update on that ConfigMap; it cannot read upgrade-lane-state). The gate's
// 06:30 New York CronJob (gate.sh morning-summary) reads the copy at that moment and sends it
// at Pushover priority -1; when the copy is missing or for an older night it sends its own
// "the lane wrote no summary" line, and it says when the lane did not finish the night.
//
// OUTSIDE TEXT. PR titles, package names, versions, check names and reasons come from GitHub
// and Renovate: clean() drops control and format characters, folds whitespace, redacts
// anything shaped like a GitHub token and caps the length. The text never reaches a shell
// command line: the sender passes it to curl as one quoted --form-string argument.

export const SUMMARY_SCHEMA = 'haynes.upgrade-morning/v1';
export const DELIVERY_KEYS = Object.freeze(['morning_text', 'morning_night', 'morning_composed_at', 'morning_ticks', 'morning_last_tick', 'morning_last_tick_expected', 'morning_mode']);
const parse = s => { try { return JSON.parse(s); } catch { return null; } };
const iso = ms => new Date(ms).toISOString();

export function summaryConfig(rules) {
  const c = rules?.night?.summary || {};
  const int = (k, lo, hi) => Number.isInteger(c[k]) && c[k] >= lo && c[k] <= hi;
  const ok = int('max_bytes', 200, 1000) && /^([01]\d|2[0-3]):[0-5]\d$/.test(c.last_tick_local || '') && int('max_items_per_line', 1, 20) && int('max_name_chars', 8, 80) && int('max_reason_chars', 16, 200);
  return { ok, ...c, why: ok ? null : 'night.summary in upgrade-rules.json is invalid (max_bytes 200..1000, last_tick_local HH:MM, max_items_per_line 1..20, max_name_chars 8..80, max_reason_chars 16..200)' };
}

// ── outside text ────────────────────────────────────────────────────────────────────
const TOKEN = /\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9_-]{20,})\b/g;
export function clean(s, cap) {
  let t = String(s ?? '').replace(/[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]+/gu, ' ').replace(/\s+/g, ' ').trim().replace(TOKEN, '[redacted]');
  if (cap && [...t].length > cap) t = `${[...t].slice(0, Math.max(1, cap - 3)).join('').trimEnd()}...`;
  return t;
}
const bytes = s => Buffer.byteLength(s, 'utf8');

// ── time in the lane's zone ──────────────────────────────────────────────────────────
export const nightOf = (ms, tz) => { try { return new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(ms)); } catch { return iso(ms).slice(0, 10); } };
export const hhmm = (ms, tz) => { try { return new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(ms)); } catch { return iso(ms).slice(11, 16); } };
const dayLabel = (ms, tz) => { try { return new Intl.DateTimeFormat('en-GB', { timeZone: tz, day: 'numeric', month: 'short' }).format(new Date(ms)); } catch { return iso(ms).slice(5, 10); } };

// The previous record (data.morning) or null; anything unreadable is null (a new night).
export function parseMorning(text) {
  const m = parse(text);
  return m && m.schema === SUMMARY_SCHEMA && typeof m.night === 'string' ? m : null;
}

// Shorten before capping: a long registry path must not eat the package's name. Keep the
// existing name in the facts too, for older readers; short_name is additive, not a schema change.
const shortName = (name, cap = 40) => clean(clean(name).split('/').filter(Boolean).at(-1), cap);
const nameFields = (name, cap) => ({ name: clean(name, cap), short_name: shortName(name, cap) });

// A blocked record's reason, in fewer words where its form is known: a check that failed again
// after the one retry names the check (story 549's "failed again on <sha> after one retry on
// <sha> at <time>: <checks>").
function shortReason(klass, reason) {
  const t = String(reason ?? '');
  if (klass === 'check-failing-after-retry') return t.replace(/^failed again on [0-9a-f]+ after one retry on [0-9a-f]+ at \S+: /, '');
  return t;
}

// ── the facts ───────────────────────────────────────────────────────────────────────
// in: { now, mode, rules, prev (parseMorning), records (open-PR lane records), verify (the
// verifier's records, data.verify items), doc (the history document or null), historyNote
// ({ needs_person, line } from the history phase), claims (summary.claims) }.
export function composeFacts({ now, mode, rules, prev = null, records = [], verify = null, doc = null, historyNote = null, claims = [] }) {
  const cfg = summaryConfig(rules);
  const tz = rules?.night?.timezone || 'America/New_York';
  const night = nightOf(now, tz);
  const same = prev?.night === night;
  // the window: from where the last night's record ended, so nothing merged by day is lost
  const from = same ? prev.window_from : prev?.composed_at && Date.parse(prev.composed_at) < now ? prev.composed_at : iso(now - 24 * 3600e3);
  const inWindow = at => { const t = Date.parse(at); return Number.isFinite(t) && t > Date.parse(from) && t <= now; };
  const N = cfg.max_name_chars || 40, R = cfg.max_reason_chars || 90;
  const facts = { night, window_from: from, mode, merged: [], verified: [], checking: [], failed: [], reverted: [], waits: { needs_person: [], awaiting_pin: [], blocked: [], owner: [], history: null }, claims: [], unreadable: [] };

  // merged and reverted: the history's events in the window
  if (!doc || !Array.isArray(doc.events)) facts.unreadable.push('the history (upgrade-ledger): what merged and what was reverted are not known');
  else {
    for (const ev of doc.events) {
      if (!inWindow(ev.at)) continue;
      const d = doc.items?.[ev.item_id] || {};
      const base = { pr: ev.carrier?.pr ?? null, ...nameFields(d.name, N), to: clean(d.to, 24), at: ev.at };
      if (ev.type === 'merged') facts.merged.push({ ...base, by: ev.actor?.kind || 'unknown', by_login: clean(ev.actor?.login || '', 40) || null });
      else if (['revert-merged', 'recovery-verified', 'recovery-failed'].includes(ev.type)) {
        // one entry per item: the revert, then whether the target recovered (the last word wins)
        let x = facts.reverted.find(r => r.item === ev.item_id);
        if (!x) facts.reverted.push(x = { ...base, item: ev.item_id, recovered: null, reason: null });
        if (ev.type === 'revert-merged') x.reverted_at = ev.at;
        if (ev.type === 'recovery-verified') x.recovered = true;
        if (ev.type === 'recovery-failed') { x.recovered = false; x.reason = clean(ev.reason, R); }
        x.at = ev.at;
      }
    }
  }
  // the verifier's records: verified in the window; open; failed; and the marks for a person
  if (!verify) facts.unreadable.push('the verifier\'s records (data.verify)');
  for (const r of Object.values(verify || {})) {
    const base = { pr: r.pr, ...nameFields(r.name, N), practice: !!r.practice };
    if (r.verdict === 'verified' && inWindow(r.verdict_at)) facts.verified.push(base);
    else if (r.verdict === 'failed' && (inWindow(r.verdict_at) || !r.final)) facts.failed.push({ ...base, attribution: r.attribution || null, why: clean((r.failing || []).map(f => `${f.fact} ${f.class}`).join(', '), R) });
    else if (!r.final) facts.checking.push({ ...base, verdict: r.verdict, why: clean((r.waiting_for || [])[0] || (r.unreadable || []).join(', ') || r.why, R) });
    if (r.needs_person) facts.waits.needs_person.push({ ...base, nights: (r.nights || []).length, given_up: !!r.gave_up, why: clean(String(r.why || '').replace(/^NEEDS A PERSON: [^;]*; /, ''), R) });
    if (String(r.pin?.state || '').startsWith('awaiting') && !r.final) facts.waits.awaiting_pin.push({ ...base, why: clean(String((r.facts || []).find(f => f.fact === 'pin')?.detail || r.pin.state).replace(/^awaiting the digest pin of [^:]+: /, ''), R) });
  }
  // the open PRs: blocked with the reason; the owner's lane
  for (const r of records || []) {
    if (r.lane === 'BLOCKED') facts.waits.blocked.push({ pr: r.pr, ...nameFields(r.package || r.title, N), class: clean(r.class, 40), why: clean(shortReason(r.class, r.reason), R) });
    else if (r.lane === 'LANE-B-OWNER') facts.waits.owner.push({ pr: r.pr, ...nameFields(r.package || r.title, N), class: clean(r.class, 40) });
  }
  if (historyNote?.needs_person) facts.waits.history = clean(historyNote.line, R * 2);
  facts.claims = (claims || []).map(c => ({ pr: c.pr, outcome: c.outcome, holder: c.holder ?? null, lane_state: c.lane_state ?? null, sig: c.sig, practice: c.practice }));
  return facts;
}

// ── the text ────────────────────────────────────────────────────────────────────────
// Fixed headings and a closed vocabulary (story 890). A PR can have several reasons; each
// phrase is at most four words. The raw check names, explanations and claim data stay in facts.
const WAIT_WORDS = Object.freeze({
  'check-failing': 'check failing', 'check-failing-after-retry': 'check failing',
  'checks-incomplete': 'checks pending', 'facts-incomplete': 'facts unreadable',
  major: 'major', 'v1-restart': 'restarts v1', prerelease: 'prerelease',
  hold: 'held', 'vet-hold': 'held by review', 'rule-owner': 'owner decision',
});
const FAILURE_WORDS = [
  [/job-failed/, 'job failed'], [/crash-loop/, 'pods crashing'],
  [/not-applied/, 'rollout failed'], [/critical-alert/, 'critical alert'],
  [/wrong-image/, 'wrong image'], [/churn/, 'pods replaced'],
  [/restart-growth/, 'restarts growing'], [/not-ready/, 'not ready'],
  [/image-pull/, 'image pull failed'], [/pin-timeout/, 'pin timed out'],
];
const failureWords = why => FAILURE_WORDS.filter(([re]) => re.test(why || '')).map(([, words]) => words);
const displayName = (x, cfg) => shortName(x.short_name || x.name, cfg.max_name_chars || 40) || 'unknown';
const prName = (x, cfg) => `#${clean(x.pr, 12)} ${displayName(x, cfg)}`;

// Reverting is said only when the claim's own state says a revert is under way (story 753).
export function claimWords(c) {
  if (!c) return '';
  if (c.outcome === 'claimed') return c.lane_state === 'reverting' ? 'lane reverting' : 'lane owns fix';
  if (c.outcome === 'triage-holds' || (c.outcome === 'lost' && c.holder === 'triage')) return 'triage owns fix';
  if (c.outcome === 'lane-holds') return 'lane fixing another update';
  if (c.outcome === 'budget-exhausted') return 'fix budget spent; no revert this run';
  if (c.outcome === 'unreadable') return 'owner unreadable';
  return 'fix unclaimed';
}

export function composeText(facts, { now, cfg, tz, finished }) {
  const historyKnown = !facts.unreadable.some(u => u.startsWith('the history'));
  const checksKnown = !facts.unreadable.some(u => u.startsWith('the verifier'));
  const waiting = new Map();
  const wait = (x, ...reasons) => {
    const key = String(x.pr);
    if (!waiting.has(key)) waiting.set(key, { ...x, reasons: [] });
    for (const reason of reasons) if (!waiting.get(key).reasons.includes(reason)) waiting.get(key).reasons.push(reason);
  };
  const w = facts.waits;
  for (const x of w.needs_person) wait(x, `open ${x.nights} nights`, ...(x.given_up ? ['checks abandoned'] : []));
  for (const x of w.awaiting_pin) wait(x, 'pin pending');
  for (const x of w.blocked) wait(x, WAIT_WORDS[x.class] || 'blocked');
  for (const x of w.owner) wait(x, WAIT_WORDS[x.class] || 'owner decision');
  const waitCount = waiting.size + (w.history ? 1 : 0);

  // One line per package, at its most recently merged version. Keep the first appearance's
  // position for a stable list. Compare timestamps, not input order or semver (also digests).
  const packageKey = x => `${x.name}\0${displayName(x, cfg)}`;
  const upgrades = new Map();
  for (const x of facts.merged) {
    const key = packageKey(x);
    const old = upgrades.get(key);
    if (!old || Date.parse(x.at) >= Date.parse(old.at)) upgrades.set(key, x);
  }
  const lines = [`Updates ${dayLabel(now, tz)}${facts.mode === 'live' ? '' : ' (dry-run)'}: ${historyKnown ? upgrades.size : '?'} in, ${historyKnown ? facts.reverted.length : '?'} reverted, ${checksKnown ? facts.failed.length : '?'} failed, ${waitCount} need you${checksKnown && facts.checking.length ? `, ${facts.checking.length} still being checked` : ''}`];
  const trouble = [];
  if (!finished) trouble.push('NIGHT INCOMPLETE');
  if (facts.unreadable.length) trouble.push(`UNREADABLE: ${facts.unreadable.map(u => u.startsWith('the history') ? 'history' : u.startsWith('the verifier') ? 'checks' : clean(u, cfg.max_reason_chars || 90)).join('; ')}`);
  for (const c of facts.claims.filter(c => c.outcome === 'unreadable')) trouble.push(`UNREADABLE: owner #${clean(c.pr, 12)}`);
  if (trouble.length) lines.push(trouble.join('; '));
  const group = (heading, items) => { if (items.length) lines.push(heading, ...items); };
  group('NEED YOU:', [
    ...[...waiting.values()].map(x => `- ${prName(x, cfg)}: ${x.reasons.join('; ')}`),
    ...(w.history ? ['- history: needs review'] : []),
  ]);
  group('FAILED:', !checksKnown ? [] : facts.failed.map(x => {
    const reasons = failureWords(x.why);
    if (!reasons.length) reasons.push('checks failed');
    reasons.push(x.attribution === 'own-regression' ? 'by update' : 'cause unclear');
    const who = claimWords(facts.claims.find(k => k.pr === x.pr));
    return `- ${prName(x, cfg)}: ${reasons.join(', ')}${who ? `; ${who}` : ''}`;
  }));
  group('REVERTED:', facts.reverted.map(x => {
    const reasons = x.recovered === true ? ['recovered'] : x.recovered === false ? ['not recovered', ...failureWords(x.reason)] : ['recovery unchecked'];
    return `- ${prName(x, cfg)}${x.to ? ` ${clean(x.to, 24)}` : ''}: ${reasons.join('; ')}`;
  }));
  // This is activity during the span, not a live inventory: explicitly mark a rolled-back
  // target. Recovery timestamps must not make a later re-merge look reverted. Older facts
  // have only at, which is the best timestamp available for their revert.
  const wasReverted = x => facts.reverted.some(r => packageKey(r) === packageKey(x) && (!r.to || r.to === x.to) && Date.parse(r.reverted_at || r.at) >= Date.parse(x.at));
  group('UPGRADED:', [...upgrades.values()].map(x => `- ${displayName(x, cfg)}${x.to ? ` ${clean(x.to, 24)}` : ''}${wasReverted(x) ? ': reverted' : ''}`));
  return fit(lines, cfg.max_bytes || 1000);
}

// Cut whole lines from the bottom: UPGRADED first, then punch-list entries only if necessary.
// +N more counts omitted entries, not headings; lines_cut still counts all omitted lines.
export function fit(lines, maxBytes) {
  const all = lines.map(l => String(l));
  const join = a => a.join('\n');
  const heading = l => /^(NEED YOU|FAILED|REVERTED|UPGRADED):?$/.test(l) || (/:$/.test(l) && !l.startsWith('- '));
  const more = keep => `+${all.slice(keep).filter(l => !heading(l)).length} more`;
  if (bytes(join(all)) <= maxBytes) return { text: join(all), cut: 0 };
  for (let keep = all.length - 1; keep >= 1; keep--) {
    if (heading(all[keep - 1])) continue;
    const text = join([...all.slice(0, keep), more(keep)]);
    if (bytes(text) <= maxBytes) return { text, cut: all.length - keep };
  }
  // Defensive fallback for callers of fit with an oversized first line: UTF-8 stays whole.
  let first = all[0];
  while (first && bytes(`${first}\n${more(1)}`) > maxBytes) first = [...first].slice(0, -1).join('');
  return { text: `${first}\n${more(1)}`, cut: all.length - 1 };
}

// ── the record, and the delivery copy ───────────────────────────────────────────────
export function composeMorning({ now, mode, rules, prevText = null, records, verify, doc, historyNote, claims }) {
  const cfg = summaryConfig(rules);
  if (!cfg.ok) return { ok: false, why: cfg.why };
  const tz = rules.night.timezone;
  const prev = parseMorning(prevText);
  const facts = composeFacts({ now, mode, rules, prev, records, verify, doc, historyNote, claims });
  const ticks = prev?.night === facts.night && Number.isInteger(prev.ticks_completed) ? prev.ticks_completed + 1 : 1;
  // finished: this tick is the night's last one (review L6). The count is information only: the
  // night the clocks go forward has 16 ticks, the night they go back may have more than 20.
  const last = hhmm(now, tz);
  const finished = last >= cfg.last_tick_local;
  const { text, cut } = composeText(facts, { now, cfg, tz, ticks, finished });
  const morning = { schema: SUMMARY_SCHEMA, night: facts.night, composed_at: iso(now), window_from: facts.window_from, ticks_completed: ticks, finished,
    last_tick_local: last, expected_last_tick_local: cfg.last_tick_local, mode, bytes: bytes(text), lines_cut: cut, text, facts };
  const delivery = { morning_text: text, morning_night: facts.night, morning_composed_at: morning.composed_at, morning_ticks: String(ticks), morning_last_tick: last, morning_last_tick_expected: cfg.last_tick_local, morning_mode: mode };
  return { ok: true, morning, delivery };
}
