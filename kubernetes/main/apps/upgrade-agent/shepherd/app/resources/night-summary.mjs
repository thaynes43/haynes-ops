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
// go from the end and the last line says "and N more". The full lists stay in the facts.
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
export const DELIVERY_KEYS = Object.freeze(['morning_text', 'morning_night', 'morning_composed_at', 'morning_ticks', 'morning_ticks_expected', 'morning_last_tick', 'morning_mode']);
const parse = s => { try { return JSON.parse(s); } catch { return null; } };
const iso = ms => new Date(ms).toISOString();

export function summaryConfig(rules) {
  const c = rules?.night?.summary || {};
  const int = (k, lo, hi) => Number.isInteger(c[k]) && c[k] >= lo && c[k] <= hi;
  const ok = int('max_bytes', 200, 1000) && int('ticks_per_night', 1, 96) && /^\d\d:\d\d$/.test(c.last_tick_local || '') && int('max_items_per_line', 1, 20) && int('max_name_chars', 8, 80) && int('max_reason_chars', 16, 200);
  return { ok, ...c, why: ok ? null : 'night.summary in upgrade-rules.json is invalid (max_bytes 200..1000, ticks_per_night 1..96, last_tick_local HH:MM, max_items_per_line 1..20, max_name_chars 8..80, max_reason_chars 16..200)' };
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

const ACTOR = { lane: 'the lane', renovate: 'Renovate', shepherd: 'the shepherd', hand: 'a session', person: 'a person', unknown: 'someone unknown' };
const actorOf = a => {
  const k = a?.kind || 'unknown';
  const login = clean(a?.login || '', 40);
  return k === 'person' || k === 'unknown' ? (login ? `${k === 'person' ? '' : 'unknown '}${login}`.trim() : ACTOR[k]) : ACTOR[k] || clean(k, 20);
};

// A blocked record's reason, in fewer words where its form is known: a check that failed again
// after the one retry names the check (story 549's "failed again on <sha> after one retry on
// <sha> at <time>: <checks>").
const BLOCKED_WORDS = { 'check-failing-after-retry': 'a check still fails after one retry' };
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
      const base = { pr: ev.carrier?.pr ?? null, name: clean(d.name, N), to: clean(d.to, 24), at: ev.at };
      if (ev.type === 'merged') facts.merged.push({ ...base, by: ev.actor?.kind || 'unknown', by_login: clean(ev.actor?.login || '', 40) || null });
      else if (['revert-merged', 'recovery-verified', 'recovery-failed'].includes(ev.type)) facts.reverted.push({ ...base, type: ev.type, reason: clean(ev.reason, R) });
    }
  }
  // the verifier's records: verified in the window; open; failed; and the marks for a person
  if (!verify) facts.unreadable.push('the verifier\'s records (data.verify)');
  for (const r of Object.values(verify || {})) {
    const base = { pr: r.pr, name: clean(r.name, N), practice: !!r.practice };
    if (r.verdict === 'verified' && inWindow(r.verdict_at)) facts.verified.push(base);
    else if (r.verdict === 'failed' && (inWindow(r.verdict_at) || !r.final)) facts.failed.push({ ...base, attribution: r.attribution || null, why: clean((r.failing || []).map(f => `${f.fact} ${f.class}`).join(', '), R) });
    else if (!r.final) facts.checking.push({ ...base, verdict: r.verdict, why: clean((r.waiting_for || [])[0] || (r.unreadable || []).join(', ') || r.why, R) });
    if (r.needs_person) facts.waits.needs_person.push({ ...base, nights: (r.nights || []).length, given_up: !!r.gave_up, why: clean(String(r.why || '').replace(/^NEEDS A PERSON: [^;]*; /, ''), R) });
    if (String(r.pin?.state || '').startsWith('awaiting') && !r.final) facts.waits.awaiting_pin.push({ ...base, why: clean(String((r.facts || []).find(f => f.fact === 'pin')?.detail || r.pin.state).replace(/^awaiting the digest pin of [^:]+: /, ''), R) });
  }
  // the open PRs: blocked with the reason; the owner's lane
  for (const r of records || []) {
    if (r.lane === 'BLOCKED') facts.waits.blocked.push({ pr: r.pr, name: clean(r.package || r.title, N), class: clean(r.class, 40), why: clean(shortReason(r.class, r.reason), R) });
    else if (r.lane === 'LANE-B-OWNER') facts.waits.owner.push({ pr: r.pr, name: clean(r.package || r.title, N), class: clean(r.class, 40) });
  }
  if (historyNote?.needs_person) facts.waits.history = clean(historyNote.line, R * 2);
  facts.claims = (claims || []).map(c => ({ pr: c.pr, outcome: c.outcome, sig: c.sig, practice: c.practice }));
  return facts;
}

// ── the text ────────────────────────────────────────────────────────────────────────
const list = (items, f, max) => `${items.slice(0, max).map(f).join(', ')}${items.length > max ? `, +${items.length - max} more` : ''}`;
const prName = x => `#${x.pr}${x.name ? ` ${x.name}` : ''}`;

export function composeText(facts, { now, cfg, tz, ticks, ticksExpected }) {
  const max = cfg.max_items_per_line || 6;
  const lines = [];
  const lane = facts.merged.filter(m => m.by === 'lane');
  lines.push(`Night of ${dayLabel(now, tz)}: lane ${facts.mode === 'live' ? 'live' : 'dry-run'}, ${ticks} of ${ticksExpected} ticks (last ${hhmm(now, tz)}). ${lane.length ? `The lane merged ${lane.length}.` : 'The lane merged nothing.'}`);
  // what waits for Tom first: it is the part he acts on
  const w = facts.waits;
  const waitLines = [];
  for (const x of w.needs_person) waitLines.push(`- ${prName(x)}: needs a person${x.given_up ? ' (practice given up)' : ''}, open ${x.nights} nights: ${x.why}`);
  for (const x of w.awaiting_pin) waitLines.push(`- ${prName(x)}: awaiting its digest pin: ${x.why}`);
  for (const x of w.blocked) waitLines.push(`- ${prName(x)}: blocked, ${BLOCKED_WORDS[x.class] || x.class}: ${x.why}`);
  if (w.owner.length) waitLines.push(`- your decision: ${list(w.owner, x => `#${x.pr} ${x.name} (${x.class})`, max)}`);
  if (w.history) waitLines.push(`- the history needs a person: ${w.history}`);
  lines.push(waitLines.length ? `Waits for you (${waitLines.length}):` : 'Waits for you: nothing.');
  lines.push(...waitLines);
  // failed verdicts next, with who holds the regression (the claims step)
  for (const x of facts.failed) {
    const c = facts.claims.find(k => k.pr === x.pr);
    const claim = !c ? '' : c.outcome === 'triage-holds' || c.outcome === 'lost' ? '; triage holds the regression' : c.outcome === 'claimed' ? '; the lane holds the regression' : c.outcome === 'would-claim' ? '; triage does not hold it' : c.outcome === 'not-in-set' ? '; triage and the gate do not see it' : c.outcome === 'unreadable' ? '; the claim could not be read' : '';
    lines.push(`FAILED ${prName(x)}${x.practice ? ' (practice)' : ''}: ${x.why}${x.attribution === 'own-regression' ? ' (its own regression)' : ' (cannot tell which change)'}${claim}`);
  }
  // merged, by whom
  if (!facts.unreadable.some(u => u.startsWith('the history'))) {
    const by = new Map();
    for (const m of facts.merged) { const k = m.by === 'person' || m.by === 'unknown' ? actorOf({ kind: m.by, login: m.by_login }) : actorOf({ kind: m.by }); by.set(k, [...(by.get(k) || []), m]); }
    const since = `since ${hhmm(Date.parse(facts.window_from), tz)} ${dayLabel(Date.parse(facts.window_from), tz)}`;
    lines.push(facts.merged.length ? `Merged ${since} (${facts.merged.length}):` : `Merged ${since}: nothing.`);
    for (const [who, ms] of by) lines.push(`- ${who} ${ms.length}: ${list(ms, m => `${prName(m)}${m.to ? ` ${m.to}` : ''}`, max)}`);
  } else lines.push('Merged: not known (the history could not be read).');
  // verified, still being checked, failed
  const practice = facts.mode !== 'live' || [...facts.verified, ...facts.checking, ...facts.failed].every(x => x.practice);
  if (facts.unreadable.some(u => u.startsWith('the verifier'))) lines.push('Checks: not known (the verifier\'s records could not be read).');
  else {
    lines.push(`Checks${practice ? ' (practice, on merges the lane did not make)' : ''}: verified ${facts.verified.length ? list(facts.verified, x => `#${x.pr}`, max * 2) : 'none'}; still checking ${facts.checking.length ? list(facts.checking, x => `#${x.pr} (${x.verdict})`, max) : 'none'}${facts.failed.length ? `; failed ${list(facts.failed, x => `#${x.pr}`, max)} (above)` : ''}.`);
  }
  lines.push(facts.reverted.length ? `Reverted: ${list(facts.reverted, x => `${prName(x)} ${x.type === 'recovery-verified' ? 'recovered' : x.type === 'recovery-failed' ? 'NOT recovered' : 'revert merged'}`, max)}.` : 'Reverted: none.');
  return fit(lines, cfg.max_bytes || 1000);
}

// Whole lines, at most maxBytes; the last line says how many were cut.
export function fit(lines, maxBytes) {
  const all = lines.map(l => String(l));
  const join = a => a.join('\n');
  if (bytes(join(all)) <= maxBytes) return { text: join(all), cut: 0 };
  for (let keep = all.length - 1; keep >= 1; keep--) {
    const t = join([...all.slice(0, keep), `and ${all.length - keep} more`]);
    if (bytes(t) <= maxBytes) return { text: t, cut: all.length - keep };
  }
  // even the first line alone is too long: cut it by characters
  let first = all[0];
  while (first && bytes(`${first}\nand ${all.length - 1} more`) > maxBytes) first = [...first].slice(0, -1).join('');
  return { text: `${first}\nand ${all.length - 1} more`, cut: all.length - 1 };
}

// ── the record, and the delivery copy ───────────────────────────────────────────────
export function composeMorning({ now, mode, rules, prevText = null, records, verify, doc, historyNote, claims }) {
  const cfg = summaryConfig(rules);
  if (!cfg.ok) return { ok: false, why: cfg.why };
  const tz = rules.night.timezone;
  const prev = parseMorning(prevText);
  const facts = composeFacts({ now, mode, rules, prev, records, verify, doc, historyNote, claims });
  const ticks = prev?.night === facts.night && Number.isInteger(prev.ticks_completed) ? prev.ticks_completed + 1 : 1;
  const { text, cut } = composeText(facts, { now, cfg, tz, ticks, ticksExpected: cfg.ticks_per_night });
  const morning = { schema: SUMMARY_SCHEMA, night: facts.night, composed_at: iso(now), window_from: facts.window_from, ticks_completed: ticks, ticks_expected: cfg.ticks_per_night,
    last_tick_local: hhmm(now, tz), expected_last_tick_local: cfg.last_tick_local, mode, bytes: bytes(text), lines_cut: cut, text, facts };
  const delivery = { morning_text: text, morning_night: facts.night, morning_composed_at: morning.composed_at, morning_ticks: String(ticks), morning_ticks_expected: String(cfg.ticks_per_night), morning_last_tick: morning.last_tick_local, morning_mode: mode };
  return { ok: true, morning, delivery };
}
