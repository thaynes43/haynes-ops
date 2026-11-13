#!/usr/bin/env node
// Self-test for the night lane's morning summary (story 554): night-summary.mjs, its wiring in
// run-night.mjs, and its sample on the real night of 2026-11-13.
//
// WHY THIS EXISTS
// ---------------
// Tom gets one short message a day. It must say what merged and by whom, what was verified and
// what is still being checked, what was reverted and what waits for him; fit Pushover's limit
// by whole lines; carry text from outside (PR titles, package names, check names) as data only;
// and never be silent about a night the lane did not finish. Pinned below:
//   1. a quiet night;
//   2. a night with merges by several actors and practice verdicts (the real merges of
//      2026-11-13, fixtures/verify-20261113.json, through night-verify.mjs docOf);
//   3. items that need a person: the verifier's mark, a pin it awaits, blocked PRs with the
//      reason, a history that needs a person, the owner's lane; failed verdicts with their claim;
//   4. ticks: counted per night, a night the lane did not finish says how far it got;
//   5. too long: whole lines cut, "and N more", at most max_bytes; the facts keep everything;
//   6. hostile text: control and format characters, newlines, shell metacharacters, a token;
//   7. the window: from the last night's record, so nothing merged by day is lost;
//   8. the writes: data.morning under its own field manager, the delivery copy morning_* only;
//   9. the sample for 2026-11-13 (fixtures/morning-20261113.json), printed.
//
// Usage: node scripts/upgrade-agent/lane-summary-selftest.mjs [--sample]
// Requires: node 24. No network, no cluster access.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../..');
const RES = join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/resources');
const S = await import(join(RES, 'night-summary.mjs'));
const V = await import(join(RES, 'night-verify.mjs'));
const rules = JSON.parse(readFileSync(join(RES, 'upgrade-rules.json'), 'utf8'));
const vfx = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/verify-20261113.json'), 'utf8'));
const mfx = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/morning-20261113.json'), 'utf8'));
const REPO = 'thaynes43/haynes-ops';
const bytes = s => Buffer.byteLength(s, 'utf8');

let passes = 0, fails = 0;
const ok = (cond, name, detail = '') => { if (cond) { passes++; console.log(`ok    ${name}`); } else { fails++; console.log(`FAIL  ${name}${detail ? `: ${detail}` : ''}`); } };
const section = s => console.log(`\n== ${s}`);
const at = s => Date.parse(s);
const LAST = at('2026-11-13T10:45:05.419Z'); // 05:45 New York, the night's last tick
const PREV = { schema: S.SUMMARY_SCHEMA, night: '2026-11-12', composed_at: '2026-11-12T10:45:00.000Z', window_from: '2026-11-11T10:45:00.000Z', ticks_completed: 20 };
const doc13 = V.docOf(vfx.prs, { repo: REPO, rules, at: '2026-11-13T12:00:00Z' });
const vrec = (pr, name, extra = {}) => ({ pr, name, practice: true, verdict: 'verified', verdict_at: '2026-11-13T10:30:00.000Z', final: true, ...extra });
const verified13 = Object.fromEntries([[4869, 'ghcr.io/seerr-team/seerr'], [4870, 'rhasspy/wyoming-piper'], [4871, 'anthropics/claude-code-action'], [4873, 'ghcr.io/traefik/helm/traefik'], [4874, 'mirror.gcr.io/cloudflare/cloudflared']].map(([pr, n]) => [`i${pr}`, vrec(pr, n)]));
const compose = (o = {}) => S.composeMorning({ now: LAST, mode: 'dryrun', rules, prevText: JSON.stringify(PREV), records: [], verify: {}, doc: { events: [], items: {} }, historyNote: { needs_person: false, line: 'ok' }, claims: [], ...o });
const lines = t => t.split('\n');

// ── 0. config ───────────────────────────────────────────────────────────────────────
section('0. night.summary in upgrade-rules.json');
{
  const c = S.summaryConfig(rules);
  ok(c.ok && c.max_bytes === 1000 && c.ticks_per_night === 20 && c.last_tick_local === '05:45', 'git ships max_bytes 1000, 20 ticks a night, the last at 05:45');
  ok(!S.summaryConfig({ night: { summary: { ...rules.night.summary, max_bytes: 1100 } } }).ok, 'max_bytes over 1000 (page.sh clamps at 1000) is refused');
  const hr = readFileSync(join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/helmrelease.yaml'), 'utf8');
  ok(/schedule: "\*\/15 1-5 \* \* \*"/.test(hr), 'the upgrade-night schedule is */15 1-5 (20 ticks, 01:00 to 05:45): what ticks_per_night describes');
}

// ── 1. a quiet night ────────────────────────────────────────────────────────────────
section('1. a quiet night');
{
  const m = compose({ records: [{ pr: 4752, lane: 'LANE-A', class: 'no-cluster-effect', title: 't', package: 'p' }] });
  const t = m.morning.text;
  ok(m.ok && bytes(t) <= 1000 && m.morning.lines_cut === 0, 'composed, within 1000 bytes, nothing cut');
  ok(/^Night of 13 Nov: lane dry-run, 1 of 20 ticks \(last 05:45\)\. The lane merged nothing\./.test(t), 'first line: the night, the mode, the ticks, the lane merged nothing', lines(t)[0]);
  ok(t.includes('Waits for you: nothing.') && /Merged since 05:45 12 Nov: nothing\./.test(t) && t.includes('verified none; still checking none') && t.includes('Reverted: none.'), 'every section says nothing in plain words', t);
}

// ── 2. merges by several actors, practice verdicts ───────────────────────────────────────
section('2. merges by several actors and practice verdicts (2026-11-13)');
{
  // a lane merge and a session's merge besides the real five
  const extra = V.docOf([{ ...vfx.prs[0], number: 4900, mergedAt: '2026-11-13T07:00:00Z', mergeCommit: { oid: 'a'.repeat(40) } }], { repo: REPO, rules, at: '2026-11-13T12:00:00Z', actor: { kind: 'lane', login: null } });
  const sess = V.docOf([{ ...vfx.prs[1], number: 4901, mergedAt: '2026-11-13T08:00:00Z', mergeCommit: { oid: 'b'.repeat(40) }, mergedBy: { login: 'app/haynes-dev-bot' } }], { repo: REPO, rules, at: '2026-11-13T12:00:00Z' });
  const doc = { events: [...doc13.events, ...extra.events, ...sess.events], items: { ...doc13.items, ...extra.items, ...sess.items } };
  const verify = { ...verified13, i4874: vrec(4874, 'mirror.gcr.io/cloudflare/cloudflared', { verdict: 'not-yet', final: false, waiting_for: ['stable: recorded 322 s ago; read again at least 600 s after that'] }) };
  const m = compose({ doc, verify });
  const t = m.morning.text;
  ok(/The lane merged 1\./.test(lines(t)[0]), 'the first line counts the lane\'s own merge');
  ok(/Merged since 05:45 12 Nov \(7\):/.test(t), 'seven merges since the last summary', t);
  ok(/- Renovate 4: #4869 ghcr\.io\/seerr-team\/seerr v3\.5\.2, #4870 rhasspy\/wyoming-piper 2\.5\.3, #4871 anthropics\/claude-code-action v1\.0\.275, #4874 mirror\.gcr\.io\/cloudflare\/cloudflared 2026\.11\.0/.test(t), 'Renovate\'s four, by PR, package and version', t);
  ok(/- the shepherd 1: #4873 ghcr\.io\/traefik\/helm\/traefik 42\.4\.0/.test(t), 'the shepherd\'s one (#4873)');
  ok(/- the lane 1: #4900/.test(t) && /- a session 1: #4901/.test(t), 'the lane\'s and a session\'s, each under its actor');
  ok(/Checks \(practice, on merges the lane did not make\): verified #4869, #4870, #4871, #4873; still checking #4874 \(not-yet\)\./.test(t), 'verified and still being checked, marked practice', t);
  ok(m.morning.facts.merged.length === 7 && m.morning.facts.verified.length === 4 && m.morning.facts.checking[0].why.startsWith('stable:'), 'the facts behind it: seven merges, four verified, one open with what it waits for');
}

// ── 3. what waits for Tom ───────────────────────────────────────────────────────────
section('3. items that need a person, failed verdicts and their claim');
{
  const verify = {
    a: vrec(4810, 'ghcr.io/thaynes43/upgrade-agent', { verdict: 'not-yet', final: true, gave_up: true, needs_person: true, nights: ['2026-11-12', '2026-11-13'], why: 'NEEDS A PERSON: open on 2 of the lane\'s nights (2026-11-12, 2026-11-13) and 9 ticks; not yet: pin: awaiting the digest pin' }),
    b: vrec(4811, 'ghcr.io/thaynes43/upgrade-shepherd', { verdict: 'not-yet', final: false, pin: { state: 'awaiting' }, facts: [{ fact: 'pin', result: 'waiting', detail: 'awaiting the digest pin of upgrade-shepherd: the build finished at 2026-11-13T04:00:00Z; pin PR #4812 is blocked: check-failing-after-retry' }] }),
    c: vrec(4869, 'ghcr.io/seerr-team/seerr', { verdict: 'failed', attribution: 'own-regression', final: true, verdict_at: '2026-11-13T09:00:00.000Z', failing: [{ fact: 'helmreleases', class: 'not-applied' }] }),
    d: vrec(4870, 'rhasspy/wyoming-piper', { verdict: 'failed', attribution: 'cannot-tell-which-change', final: true, verdict_at: '2026-11-13T09:00:00.000Z', failing: [{ fact: 'pods', class: 'crash-loop' }] }),
  };
  const records = mfx.records;
  const claims = [{ pr: 4869, outcome: 'triage-holds', sig: 'ae85955b9204', practice: true }, { pr: 4870, outcome: 'not-in-set', sig: null, practice: true }];
  const m = compose({ verify, records, claims, historyNote: { needs_person: true, line: 'HISTORY NEEDS A PERSON: the backfill comparison disagrees' } });
  const t = m.morning.text;
  ok(/Waits for you \(\d+\):/.test(t) && lines(t)[1].startsWith('Waits for you'), 'what waits for Tom comes right after the first line');
  ok(/- #4810 ghcr\.io\/thaynes43\/upgrade-agent: needs a person \(practice given up\), open 2 nights: not yet: pin/.test(t), 'the verifier\'s "needs a person" mark, with its nights and why', t);
  ok(/- #4811 ghcr\.io\/thaynes43\/upgrade-shepherd: awaiting its digest pin: the build finished at 2026-11-13T04:00:00Z; pin PR #4812/.test(t), 'an item awaiting a pin, with its reason', t);
  ok(/- #4757 antlr4-python3-runtime: blocked, a check still fails after one retry: Build and Push ControlFoley Image \/ validate \(FAILURE\)/.test(t) && /- #4848 .*: blocked, a check still fails after one retry: dev-env archive guard/.test(t), 'the PRs whose record says blocked, with the reason in fewer words (the real #4757 and #4848)', t);
  ok(/- your decision: #4376 node \(major\), #4764 .* \(major\)/.test(t), 'the owner\'s lane: the majors that need his decision');
  ok(/- the history needs a person: HISTORY NEEDS A PERSON/.test(t), 'a history that says it needs a person');
  ok(bytes(t) <= 1000, `within the limit (${bytes(t)} bytes, ${m.morning.lines_cut} line(s) cut)`);
  // the failed verdicts on their own: each with who holds the regression
  const f = compose({ verify: { c: verify.c, d: verify.d }, claims });
  const ft = f.morning.text;
  ok(/\nFAILED #4869 ghcr\.io\/seerr-team\/seerr \(practice\): helmreleases not-applied \(its own regression\); triage holds the regression\n/.test(ft), 'a failed verdict with its claim: triage holds it', ft);
  ok(/\nFAILED #4870 rhasspy\/wyoming-piper \(practice\): pods crash-loop \(cannot tell which change\); triage and the gate do not see it\n/.test(ft), 'cannot tell which change, not in the coordination set');
  ok(ft.indexOf('FAILED #4869') < ft.indexOf('Merged since') && /failed #4869, #4870 \(above\)/.test(ft) && f.morning.lines_cut === 0, 'failed verdicts come before the merges; the checks line points to them');
  ok(m.morning.facts.waits.blocked.length === 2 && m.morning.facts.waits.owner.length === 2, 'the facts list both blocked PRs and both owner PRs');
}

// ── 4. ticks ─────────────────────────────────────────────────────────────────────────
section('4. ticks of the night');
{
  const first = compose({ prevText: JSON.stringify(PREV), now: at('2026-11-13T06:00:00Z') });
  ok(first.morning.ticks_completed === 1 && first.morning.night === '2026-11-13', 'the first tick of a night: 1, the night is New York\'s date');
  const later = compose({ prevText: JSON.stringify(first.morning), now: at('2026-11-13T07:30:00Z') });
  ok(later.morning.ticks_completed === 2 && later.morning.window_from === first.morning.window_from, 'the next tick of the same night: 2, the same window');
  const stopped = compose({ prevText: JSON.stringify({ ...first.morning, ticks_completed: 6 }), now: at('2026-11-13T07:30:00Z') });
  ok(/^Night of 13 Nov: lane dry-run, 7 of 20 ticks \(last 02:30\)/.test(stopped.morning.text) && stopped.delivery.morning_ticks === '7' && stopped.delivery.morning_ticks_expected === '20' && stopped.delivery.morning_last_tick === '02:30',
    'a night the lane did not finish: "7 of 20 ticks (last 02:30)", and the sender reads 7 < 20 from the copy', stopped.morning.text.split('\n')[0]);
  ok(compose({ prevText: '{garbage' }).morning.ticks_completed === 1 && compose({ prevText: null }).ok, 'an unreadable or missing last record starts the count again');
}

// ── 5. too long ─────────────────────────────────────────────────────────────────────
section('5. too long for the limit');
{
  const records = Array.from({ length: 40 }, (_, i) => ({ pr: 5000 + i, lane: 'BLOCKED', class: 'check-failing-after-retry', package: `ghcr.io/example/package-with-a-long-name-${i}`, reason: `failed again on abcdef12 after one retry: Some Long Workflow Name / some-very-long-check-name-${i} (FAILURE)` }));
  const m = compose({ records });
  const t = m.morning.text;
  ok(bytes(t) <= 1000, `at most 1000 bytes (${bytes(t)})`);
  ok(/\nand \d+ more$/.test(t) && m.morning.lines_cut > 0, `whole lines cut, the last says "and ${m.morning.lines_cut} more"`);
  ok(lines(t).slice(0, -1).every(l => records.some(r => l.includes(`#${r.pr} `)) || !l.startsWith('- #')), 'no line is cut in the middle');
  ok(m.morning.facts.waits.blocked.length === 40, 'the facts keep all 40');
  const f = S.fit(['x'.repeat(1200), 'y'], 1000);
  ok(bytes(f.text) <= 1000 && /and 1 more$/.test(f.text), 'a first line too long on its own is cut by characters, still within the limit');
  const u = S.fit(['é'.repeat(600)], 1000);
  ok(bytes(u.text) <= 1000, 'the limit is in bytes (multi-byte text)');
}

// ── 6. hostile text ─────────────────────────────────────────────────────────────────
section('6. hostile PR titles and package names');
{
  const evil = 'evil$(touch /tmp/pwned)`id`;rm -rf /\n- #1 fake line\u001b[31mred‮esrever​ ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 @/etc/passwd ' + 'x'.repeat(300);
  const records = [{ pr: 6000, lane: 'BLOCKED', class: 'check-failing-after-retry', package: evil, title: evil, reason: evil }];
  const m = compose({ records });
  const t = m.morning.text;
  const line = lines(t).find(l => l.startsWith('- #6000'));
  ok(!!line && !lines(t).some(l => l.startsWith('- #1 fake line')), 'a newline in outside text cannot make a line of its own');
  ok(!/[\u0000-\u001f\u007f‮​]/.test(t.replace(/\n/g, '')), 'no control or format characters (ESC, RLO, zero-width) reach the text');
  ok(!t.includes('ghp_ABCDEFGHIJ') && t.includes('[redacted]'), 'a token shape is redacted');
  ok(bytes(line) < 260, `the name and the reason are capped (${bytes(line)} bytes)`);
  ok(line.includes('$(touch /tmp/pwned)') || line.includes('evil$('), 'shell metacharacters stay as text: the sender passes it as data (gate-morning-selftest.sh proves nothing runs)');
  ok(S.clean('a\tb\r\nc', 10) === 'a b c', 'clean folds whitespace');
}

// ── 7. the window ───────────────────────────────────────────────────────────────────
section('7. the window: from the last night\'s record');
{
  const doc = { events: [...doc13.events], items: { ...doc13.items } };
  const day = V.docOf([{ ...vfx.prs[0], number: 4950, mergedAt: '2026-11-12T16:00:00Z', mergeCommit: { oid: 'c'.repeat(40) } }], { repo: REPO, rules, at: '2026-11-13T12:00:00Z' });
  doc.events.push(...day.events); Object.assign(doc.items, day.items);
  const m = compose({ doc });
  ok(m.morning.window_from === PREV.composed_at && m.morning.facts.merged.some(x => x.pr === 4950), 'a merge by day (16:00Z on the 12th) is in the next morning\'s summary');
  const old = V.docOf([{ ...vfx.prs[0], number: 4951, mergedAt: '2026-11-12T09:00:00Z', mergeCommit: { oid: 'd'.repeat(40) } }], { repo: REPO, rules, at: '2026-11-13T12:00:00Z' });
  const m2 = compose({ doc: { events: [...doc.events, ...old.events], items: { ...doc.items, ...old.items } } });
  ok(!m2.morning.facts.merged.some(x => x.pr === 4951), 'a merge already in the last summary is not repeated');
  const m3 = compose({ prevText: null });
  ok(Date.parse(m3.morning.window_from) === LAST - 24 * 3600e3, 'no last record: the last 24 hours');
  const m4 = compose({ doc: null });
  ok(/Merged: not known \(the history could not be read\)\./.test(m4.morning.text) && m4.morning.facts.unreadable.length === 1, 'the history unreadable: said, not "nothing"');
  const m5 = compose({ verify: null });
  ok(/Checks: not known/.test(m5.morning.text), 'the verifier\'s records unreadable: said');
}

// ── 8. the writes ───────────────────────────────────────────────────────────────────
section('8. the writes: data.morning and the delivery copy');
{
  const m = compose();
  ok(Object.keys(m.delivery).every(k => S.DELIVERY_KEYS.includes(k)) && Object.values(m.delivery).every(v => typeof v === 'string') && m.delivery.morning_text === m.morning.text, 'the delivery copy: morning_* keys only, strings, the same text');
  ok(m.morning.schema === S.SUMMARY_SCHEMA && m.morning.composed_at === new Date(LAST).toISOString() && m.morning.night === '2026-11-13', 'the record says when it was composed and for which night');
  const rn = readFileSync(join(RES, 'run-night.mjs'), 'utf8');
  const keys = /const DELIVERY_KEYS = Object\.freeze\((\[[^\]]+\])\)/.exec(rn)?.[1];
  ok(keys && JSON.stringify(JSON.parse(keys.replace(/'/g, '"'))) === JSON.stringify(S.DELIVERY_KEYS), 'run-night.mjs writes exactly the keys night-summary.mjs composes');
  ok(/\(manager === SUMMARY_MANAGER\) !== \(delivery \|\| Object\.keys\(manifest\.data \|\| \{\}\)\.join\(\) === 'morning'\)/.test(rn), 'writeLaneState: data.morning (or the delivery copy) only under upgrade-night-summary, and that manager writes nothing else');
  ok(/if \(ok && morning\?\.ok\) \{/.test(rn) && rn.indexOf('writeLaneState(morningManifest(') > rn.indexOf("writeLaneState(stateManifest({ mode: MODE, updated: summary.at"), 'the summary is written after the tick\'s own state landed (a refused tick did not complete)');
  ok(/const DELIVERY = \{ name: 'upgrade-orphan-report', namespace: 'upgrade-agent' \};/.test(rn) && /writeLaneState\(deliveryManifest\(morning\.delivery\), SUMMARY_MANAGER\)/.test(rn), 'the delivery copy: server-side apply (writeLaneState, the one spawn) to upgrade-orphan-report under the summary\'s field manager');
  ok(!S.DELIVERY_KEYS.includes('morning_sent_night') && /every\(\(\[k, v\]\) => DELIVERY_KEYS\.includes\(k\) && typeof v === 'string'\)/.test(rn), 'the lane never writes morning_sent_night (the sender\'s stamp): writeLaneState refuses any other key of upgrade-orphan-report');
  const idx = rn.indexOf('S.composeMorning(');
  ok(idx > rn.indexOf('H.historyPhase(') && idx > rn.indexOf('M.writePrRecords('), 'composed after the history and the PR records: from what the tick holds');
  const src = readFileSync(join(RES, 'night-summary.mjs'), 'utf8');
  ok(!/child_process|fetch\(|spawn|runReadOnly|run\(/.test(src.replace(/\/\/.*$/gm, '')), 'night-summary.mjs reads nothing itself: no process, no fetch, no runner (no new GitHub read)');
}

// ── 9. the sample: the night of 2026-11-13 ────────────────────────────────────────────
section('9. the sample: the night of 2026-11-13, read-only');
{
  const m = S.composeMorning({ now: Date.parse(mfx.last_tick), mode: 'dryrun', rules, prevText: JSON.stringify({ ...PREV, composed_at: mfx.prev_composed_at }), records: mfx.records,
    verify: verified13, doc: doc13, historyNote: { needs_person: false, line: '' }, claims: [] });
  // the night ran all its ticks: the copy counts them as the lane's record would after the 20th
  const text = m.morning.text.replace(/1 of 20 ticks/, '20 of 20 ticks');
  ok(bytes(text) <= 1000 && /The lane merged nothing\./.test(text) && /Renovate 4: #4869/.test(text) && /the shepherd 1: #4873/.test(text) && /#4757 .*blocked/.test(text) && /#4848 .*blocked/.test(text), 'the lane merged nothing; Renovate and the shepherd merged; two PRs blocked');
  if (process.argv.includes('--sample')) console.log(`\n----- sample (${bytes(text)} bytes) -----\n${text}\n-----`);
}

console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
