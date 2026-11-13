#!/usr/bin/env node
// Self-test for the night lane's morning summary (story 554): night-summary.mjs, its wiring in
// run-night.mjs, and its sample on the real night of 2026-11-13.
//
// WHY THIS EXISTS
// ---------------
// Tom gets one short message a day. It must say what merged and by whom, what was verified and
// what is still being checked, what was reverted and whether it recovered, and what waits for
// him; fit Pushover's limit by whole lines without losing a count or an unrecovered revert (the
// review's M4); read plainly to someone who knows nothing of the lane (L8); carry text from
// outside (PR titles, package names, check names) as data only; and never be silent about a
// night the lane did not finish, decided by the night's last tick, not a count (L6). Pinned:
//   1. a quiet night;
//   2. a night with merges by several actors and practice verdicts (the real merges of
//      2026-11-13, fixtures/verify-20261113.json, through night-verify.mjs docOf);
//   3. what waits for Tom, counted in items; failed verdicts and who handles them, in words;
//   4. reverts: one that recovered, one that did not, one not checked yet;
//   5. ticks: finished by the 05:45 tick, a spring-forward night (16) and a fall-back night (24);
//   6. too long: the reviewer's busy night (t/many.mjs): every count and the unrecovered revert
//      survive; whole lines cut, "and N more", at most max_bytes; the facts keep everything;
//   7. hostile text: control and format characters, newlines, shell metacharacters, a token;
//   8. the window: from the last night's record, boundaries included once;
//   9. the writes: data.morning under its own field manager, the delivery copy morning_* only;
//  10. the samples: 2026-11-13 and a made-up busy night (--sample prints them).
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
const SAMPLE = process.argv.includes('--sample');

let passes = 0, fails = 0;
const ok = (cond, name, detail = '') => { if (cond) { passes++; console.log(`ok    ${name}`); } else { fails++; console.log(`FAIL  ${name}${detail ? `: ${detail}` : ''}`); } };
const section = s => console.log(`\n== ${s}`);
const at = s => Date.parse(s);
const LAST = at('2026-11-13T10:45:05.419Z'); // 05:45 New York, the night's last tick
const PREV = { schema: S.SUMMARY_SCHEMA, night: '2026-11-12', composed_at: '2026-11-12T10:45:00.000Z', window_from: '2026-11-11T10:45:00.000Z', ticks_completed: 19 };
const doc13 = V.docOf(vfx.prs, { repo: REPO, rules, at: '2026-11-13T12:00:00Z' });
const vrec = (pr, name, extra = {}) => ({ pr, name, practice: true, verdict: 'verified', verdict_at: '2026-11-13T10:30:00.000Z', final: true, ...extra });
const verified13 = Object.fromEntries([[4869, 'ghcr.io/seerr-team/seerr'], [4870, 'rhasspy/wyoming-piper'], [4871, 'anthropics/claude-code-action'], [4873, 'ghcr.io/traefik/helm/traefik'], [4874, 'mirror.gcr.io/cloudflare/cloudflared']].map(([pr, n]) => [`i${pr}`, vrec(pr, n)]));
// the 20th tick of the night: the last record held 19
const SAME = { schema: S.SUMMARY_SCHEMA, night: '2026-11-13', composed_at: '2026-11-13T10:30:05.000Z', window_from: PREV.composed_at, ticks_completed: 19 };
const compose = (o = {}) => S.composeMorning({ now: LAST, mode: 'dryrun', rules, prevText: JSON.stringify(SAME), records: [], verify: {}, doc: { events: [], items: {} }, historyNote: { needs_person: false, line: 'ok' }, claims: [], ...o });
const lines = t => t.split('\n');
const ev = (type, atS, item, pr, extra = {}) => ({ type, at: atS, item_id: item, carrier: { pr }, ...extra });

// ── 0. config ───────────────────────────────────────────────────────────────────────
section('0. night.summary in upgrade-rules.json');
{
  const c = S.summaryConfig(rules);
  ok(c.ok && c.max_bytes === 1000 && c.last_tick_local === '05:45' && c.ticks_per_night === undefined, 'git ships max_bytes 1000 and the night\'s last tick, 05:45 (no tick count: L6)');
  ok(!S.summaryConfig({ night: { summary: { ...rules.night.summary, max_bytes: 1100 } } }).ok && !S.summaryConfig({ night: { summary: { ...rules.night.summary, last_tick_local: '25:45' } } }).ok, 'max_bytes over 1000 (page.sh clamps at 1000) and an hour past 23 are refused');
  const hr = readFileSync(join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/helmrelease.yaml'), 'utf8');
  ok(/schedule: "\*\/15 1-5 \* \* \*"/.test(hr) && /timeZone: America\/New_York/.test(hr), 'the upgrade-night schedule is */15 1-5 New York: its last tick is 05:45');
}

// ── 1. a quiet night ────────────────────────────────────────────────────────────────
section('1. a quiet night');
{
  const m = compose({ records: [{ pr: 4752, lane: 'LANE-A', class: 'no-cluster-effect', title: 't', package: 'p' }] });
  const t = m.morning.text;
  ok(m.ok && bytes(t) <= 1000 && m.morning.lines_cut === 0, 'composed, within 1000 bytes, nothing cut');
  ok(lines(t)[0] === 'Night of 13 Nov (lane dry-run): the lane finished the night: 20 runs, the last at 05:45.', 'first line: the night, the mode, in words whether the lane finished, its runs, the last one', lines(t)[0]);
  ok(lines(t)[1] === 'Merged 0 since 05:45 12 Nov. Reverted 0.', 'second line: merged and reverted counts', lines(t)[1]);
  ok(lines(t)[2] === 'Health checks after merging (practice: the lane watched merges it did not make): 0 passed, 0 still being checked, 0 failed. Waiting for you: 0 items.', 'third line: the checks and what waits, counted', lines(t)[2]);
  ok(lines(t).length === 3, 'and nothing else on a quiet night: no heading for an empty group (no "Waiting for you:" under 0 items)', t);
  ok(!/\btick|claim|signature/.test(t), 'no word that needs knowledge of the lane (tick, claim, signature)');
  // the practice label: dry-run says it; live with nothing judged does not (second review)
  const live0 = compose({ mode: 'live' }).morning.text;
  ok(lines(live0)[2] === 'Health checks after merging: 0 passed, 0 still being checked, 0 failed. Waiting for you: 0 items.', 'live, nothing judged: no practice label', lines(live0)[2]);
  const livePractice = compose({ mode: 'live', verify: { a: vrec(4869, 'seerr') } }).morning.text;
  ok(/^Health checks after merging \(practice: the lane watched merges it did not make\): 1 passed/.test(lines(livePractice)[2]), 'live, only practice judged: the label');
  const liveOwn = compose({ mode: 'live', verify: { a: vrec(4869, 'seerr', { practice: false }) } }).morning.text;
  ok(/^Health checks after merging: 1 passed/.test(lines(liveOwn)[2]), 'live, the lane\'s own merge judged: no label');
}

// ── 2. merges by several actors, practice verdicts ───────────────────────────────────────
section('2. merges by several actors and practice verdicts (2026-11-13)');
{
  const extra = V.docOf([{ ...vfx.prs[0], number: 4900, mergedAt: '2026-11-13T07:00:00Z', mergeCommit: { oid: 'a'.repeat(40) } }], { repo: REPO, rules, at: '2026-11-13T12:00:00Z', actor: { kind: 'lane', login: null } });
  const sess = V.docOf([{ ...vfx.prs[1], number: 4901, mergedAt: '2026-11-13T08:00:00Z', mergeCommit: { oid: 'b'.repeat(40) }, mergedBy: { login: 'app/haynes-dev-bot' } }], { repo: REPO, rules, at: '2026-11-13T12:00:00Z' });
  const doc = { events: [...doc13.events, ...extra.events, ...sess.events], items: { ...doc13.items, ...extra.items, ...sess.items } };
  const verify = { ...verified13, i4874: vrec(4874, 'mirror.gcr.io/cloudflare/cloudflared', { verdict: 'not-yet', final: false, waiting_for: ['stable: recorded 322 s ago; read again at least 600 s after that'] }) };
  const t = compose({ doc, verify }).morning.text;
  ok(lines(t)[1] === 'Merged 7 since 05:45 12 Nov (Renovate 4, the shepherd 1, the lane 1, a session 1). Reverted 0.', 'merged, by whom, in the counts', lines(t)[1]);
  ok(/: 4 passed, 1 still being checked, 0 failed\./.test(lines(t)[2]), 'checks counted', lines(t)[2]);
  ok(/\nMerged:\n- by Renovate: #4869 ghcr\.io\/seerr-team\/seerr v3\.5\.2, #4870 rhasspy\/wyoming-piper 2\.5\.3, #4871 anthropics\/claude-code-action v1\.0\.275, #4874 mirror\.gcr\.io\/cloudflare\/cloudflared 2026\.11\.0\n/.test(t), 'under its heading "Merged:", Renovate\'s four, by PR, package and version', t);
  ok(/\n- by the shepherd: #4873 ghcr\.io\/traefik\/helm\/traefik 42\.4\.0\n/.test(t) && /\n- by the lane: #4900 /.test(t) && /\n- by a session: #4901 /.test(t), 'the shepherd\'s, the lane\'s and a session\'s, each under its actor');
  ok(/\nHealth checks:\n- passed: #4869, #4870, #4871, #4873\n- still being checked: #4874 \(stable: recorded 322 s ago/.test(t), 'under its heading "Health checks:", what passed and what is still being checked, with what it waits for');
  ok(t.indexOf('\nMerged:') < t.indexOf('\nHealth checks:') && !t.includes('Waiting for you:\n'), 'the merges before the checks; nothing waits, so no "Waiting for you:" heading above them');
}

// ── 3. what waits for Tom, failed verdicts ───────────────────────────────────────────
section('3. items that wait for Tom, counted in items; failed verdicts in plain words');
{
  const verify = {
    a: vrec(4810, 'ghcr.io/thaynes43/upgrade-agent', { verdict: 'not-yet', final: true, gave_up: true, needs_person: true, nights: ['2026-11-12', '2026-11-13'], why: 'NEEDS A PERSON: open on 2 of the lane\'s nights (2026-11-12, 2026-11-13) and 9 ticks; not yet: pin: awaiting the digest pin' }),
    b: vrec(4811, 'ghcr.io/thaynes43/upgrade-shepherd', { verdict: 'not-yet', final: false, pin: { state: 'awaiting' }, facts: [{ fact: 'pin', result: 'waiting', detail: 'awaiting the digest pin of upgrade-shepherd: the build finished at 2026-11-13T04:00:00Z; pin PR #4812 is blocked: check-failing-after-retry' }] }),
  };
  const m = compose({ verify, records: mfx.records, historyNote: { needs_person: true, line: 'HISTORY NEEDS A PERSON: the backfill comparison disagrees' } });
  const t = m.morning.text;
  ok(/Waiting for you: 7 items\.$/.test(lines(t)[2]), 'seven items: one needs a person, one pin, two blocked, two of the owner\'s, the history (L8: items, not lines)', lines(t)[2]);
  ok(/\n- #4810 ghcr\.io\/thaynes43\/upgrade-agent: needs a person: its check has stayed open 2 nights and was given up: not yet: pin/.test(t), 'the verifier\'s "needs a person" mark, in words', t);
  ok(/\n- #4811 ghcr\.io\/thaynes43\/upgrade-shepherd: waiting for the update that pins its new image: the build finished at 2026-11-13T04:00:00Z/.test(t), 'an item awaiting a pin, with its reason');
  ok(/\n- #4757 antlr4-python3-runtime: blocked, a check still fails after one retry: Build and Push ControlFoley Image \/ validate \(FAILURE\)/.test(t) && /\n- #4848 .*: blocked, a check still fails after one retry: dev-env archive guard/.test(t), 'the PRs whose record says blocked, with the reason in fewer words (the real #4757 and #4848)');
  ok(bytes(t) <= 1000, `within the limit (${bytes(t)} bytes, ${m.morning.lines_cut} line(s) cut)`);
  const t2 = compose({ records: mfx.records.filter(r => r.lane === 'LANE-B-OWNER'), historyNote: { needs_person: true, line: 'HISTORY NEEDS A PERSON: the backfill comparison disagrees' } }).morning.text;
  ok(/\n- your decision on 2 updates the lane may not make \(major\): #4376 node, #4764 av\n/.test(t2) && /Waiting for you: 3 items\.$/.test(lines(t2)[2]), 'the owner\'s updates: what kind, and which; each counts as an item', t2);
  ok(/\n- the update history needs a person: HISTORY NEEDS A PERSON/.test(t2), 'a history that says it needs a person');
  // failed verdicts, each with who handles it, in words that need no knowledge of the lane
  const f = (pr, attribution, extra = {}) => vrec(pr, `pkg-${pr}`, { verdict: 'failed', attribution, final: true, verdict_at: '2026-11-13T09:00:00.000Z', failing: [{ fact: 'helmreleases', class: 'not-applied' }], ...extra });
  const verify2 = { a: f(1, 'own-regression'), b: f(2, 'own-regression'), c: f(3, 'own-regression'), d: f(4, 'own-regression'), e: f(5, 'cannot-tell-which-change'), g: f(6, 'own-regression'), h: f(7, 'own-regression') };
  const claims = [{ pr: 1, outcome: 'claimed', holder: 'lane' }, { pr: 2, outcome: 'triage-holds', holder: 'triage' }, { pr: 3, outcome: 'lost', holder: null }, { pr: 4, outcome: 'lost', holder: 'triage' },
    { pr: 5, outcome: 'not-own-regression', holder: null }, { pr: 6, outcome: 'would-claim', holder: null }, { pr: 7, outcome: 'not-in-set', holder: null }];
  const ft = compose({ verify: verify2, claims }).morning.text;
  // one failure at a time, so no line is cut
  const line = pr => lines(compose({ verify: { x: Object.values(verify2).find(v => v.pr === pr) }, claims }).morning.text).find(l => l.startsWith(`- #${pr} `)) || '';
  ok(/caused by this update \(no other change reached the app\); the night lane has taken it$/.test(line(1)), 'claimed (working): the night lane has taken it (no revert is said before one is under way)', line(1));
  ok(/; the automatic remediation \(triage\) has taken it$/.test(line(2)) && /; the automatic remediation \(triage\) has taken it$/.test(line(4)), 'triage holds it (also a lost claim that triage won): triage has taken it');
  ok(/; no automatic fix has taken it yet$/.test(line(3)) && !/lane|triage/.test(line(3)), 'a lost claim nobody holds is not worded as the lane or triage holding it', line(3));
  ok(/: helmreleases not-applied; cannot tell which change caused it; no automatic fix has taken it yet$/.test(line(5)), 'cannot tell which change (also when the other changes could not be read), in words', line(5));
  ok(/; no automatic fix has taken it yet$/.test(line(6)) && /; no automatic fix has taken it yet$/.test(line(7)) && !/only this note|does not watch/.test(line(7)), 'practice, and not seen by the gate: no claim about what else reports it (a critical alert also pages)', line(7));
  ok(S.claimWords({ outcome: 'claimed', lane_state: 'reverting' }) === 'the night lane is reverting it', '"reverting" only when the claim says a revert is under way (story 753)');
  ok(!/triage does not hold|claim|signature|lane holds/.test(ft), 'no line needs knowledge of claims or signatures');
  ok(/\nFailed health checks:\n- #1 pkg-1 \(practice\): /.test(ft), 'the failures sit under their own heading "Failed health checks:"', ft);
  ok(/: 0 passed, 0 still being checked, 7 failed\./.test(lines(ft)[2]), 'the failures are counted');
  ok(S.claimWords({ outcome: 'unreadable' }) === 'who has taken it could not be read' && S.claimWords({ outcome: 'lane-holds' }) === 'the night lane has already taken it with another update' && S.claimWords({ outcome: 'not-in-set' }) === 'no automatic fix has taken it yet' && S.claimWords(null) === '', 'the rest of the words');
  ok(/\nWaiting for you:\n- #4810 /.test(t) && lines(t).filter(l => l === 'Waiting for you:').length === 1, 'the waiting items under one "Waiting for you:" heading', t);
}

// ── 4. reverts ──────────────────────────────────────────────────────────────────────
section('4. what was reverted, and whether it recovered');
{
  const doc = { events: [
    ev('revert-merged', '2026-11-13T06:00:00Z', 'x', 4950), ev('recovery-verified', '2026-11-13T07:00:00Z', 'x', 4950),
    ev('revert-merged', '2026-11-13T06:30:00Z', 'y', 4951), ev('recovery-failed', '2026-11-13T07:30:00Z', 'y', 4951, { reason: 'traefik still crash-looping\nafter the revert' }),
    ev('revert-merged', '2026-11-13T10:00:00Z', 'z', 4952) ], items: { x: { name: 'seerr', to: 'v3.5.2' }, y: { name: 'traefik', to: '42.5.0' }, z: { name: 'piper', to: '2.5.3' } } };
  const m = compose({ doc });
  const t = m.morning.text;
  ok(lines(t)[1] === 'Merged 0 since 05:45 12 Nov. Reverted 3 (1 did NOT recover, 1 recovered, 1 not checked yet).', 'the counts: three reverts, one did not recover', lines(t)[1]);
  ok(lines(t)[3] === 'Reverted and NOT recovered:' && lines(t)[4] === '- #4951 traefik 42.5.0: traefik still crash-looping after the revert', 'the unrecovered revert is the first detail, under its own heading, with its reason', lines(t).slice(3, 5).join(' | '));
  ok(/\nReverted:\n- recovered: #4950 seerr v3\.5\.2\n- recovery not checked yet: #4952 piper 2\.5\.3/.test(t), 'the recovered one and the one not checked yet, under "Reverted:"', t);
  ok(m.morning.facts.reverted.length === 3 && m.morning.facts.reverted.find(r => r.pr === 4951).recovered === false, 'one fact per revert, with whether it recovered');
}

// ── 5. ticks ─────────────────────────────────────────────────────────────────────────
section('5. ticks of the night: finished is the 05:45 tick, not a count');
{
  const first = compose({ prevText: JSON.stringify(PREV), now: at('2026-11-13T06:00:00Z') });
  ok(first.morning.ticks_completed === 1 && first.morning.night === '2026-11-13' && !first.morning.finished && lines(first.morning.text)[0] === 'Night of 13 Nov (lane dry-run): the lane has not finished the night yet: 1 run, the last at 01:00.', 'the first run of a night (01:00): not finished yet, in words', lines(first.morning.text)[0]);
  const later = compose({ prevText: JSON.stringify(first.morning), now: at('2026-11-13T07:30:00Z') });
  ok(later.morning.ticks_completed === 2 && later.morning.window_from === first.morning.window_from && later.delivery.morning_last_tick === '02:30' && later.delivery.morning_last_tick_expected === '05:45', 'the next tick of the same night: 2, the same window; the copy carries the last tick and the night\'s last');
  const fin = compose();
  ok(fin.morning.finished && fin.delivery.morning_ticks === '20' && fin.delivery.morning_last_tick === '05:45', 'the 05:45 tick: finished');
  // 2026-03-08 New York: 02:00 does not exist; */15 1-5 gives 16 ticks, the last at 05:45 EDT (09:45Z)
  const spring = compose({ now: at('2026-03-08T09:45:04Z'), prevText: JSON.stringify({ ...SAME, night: '2026-03-08', ticks_completed: 15 }) });
  ok(spring.morning.finished && spring.morning.ticks_completed === 16 && lines(spring.morning.text)[0] === 'Night of 8 Mar (lane dry-run): the lane finished the night: 16 runs, the last at 05:45.', 'spring forward (2026-03-08): 16 runs and finished', lines(spring.morning.text)[0]);
  // 2026-11-01 New York: 01:00 to 01:45 twice; the last tick 05:45 EST (10:45Z) after 24
  const fall = compose({ now: at('2026-11-01T10:45:04Z'), prevText: JSON.stringify({ ...SAME, night: '2026-11-01', ticks_completed: 23 }) });
  ok(fall.morning.finished && fall.morning.ticks_completed === 24 && /: the lane finished the night: 24 runs, the last at 05:45\./.test(lines(fall.morning.text)[0]), 'fall back (2026-11-01): 24 runs and finished');
  ok(compose({ prevText: '{garbage' }).morning.ticks_completed === 1 && compose({ prevText: null }).ok, 'an unreadable or missing last record starts the count again');
}

// ── 6. too long ─────────────────────────────────────────────────────────────────────
section('6. too long for the limit: every count and the unrecovered revert survive');
{
  // the reviewer's busy night (t/many.mjs): 12 blocked PRs, a lane merge, a revert that did not recover
  const records = Array.from({ length: 12 }, (_, i) => ({ pr: 4800 + i, lane: 'BLOCKED', class: 'facts-incomplete', package: `ghcr.io/some/package-${i}`, reason: 'read failed: gh pr view exited 1: HTTP 502 Bad Gateway from api.github.com while reading checks' }));
  const doc = { events: [ev('merged', '2026-11-13T07:00:00Z', 'a', 4900, { actor: { kind: 'lane' } }), ev('revert-merged', '2026-11-13T08:00:00Z', 'a', 4901, { reason: 'its own regression' }), ev('recovery-failed', '2026-11-13T09:00:00Z', 'a', 4901, { reason: 'still failing' })], items: { a: { name: 'traefik', to: '42.5.0' } } };
  const m = S.composeMorning({ now: at('2026-11-13T10:45:00Z'), mode: 'live', rules, prevText: null, records, verify: {}, doc, historyNote: { needs_person: false }, claims: [] });
  const t = m.morning.text;
  ok(bytes(t) <= 1000 && m.morning.lines_cut > 0 && new RegExp(`\\nand ${m.morning.lines_cut} more lines$`).test(t), `at most 1000 bytes (${bytes(t)}), whole lines cut, "and ${m.morning.lines_cut} more lines" (lines, not items)`);
  ok(/^Merged 1 since .* \(the lane 1\)\. Reverted 1 \(1 did NOT recover\)\.$/.test(lines(t)[1]) && /Waiting for you: 12 items\.$/.test(lines(t)[2]), 'every count survives', lines(t).slice(0, 3).join(' | '));
  ok(lines(t)[3] === 'Reverted and NOT recovered:' && lines(t)[4] === '- #4901 traefik 42.5.0: still failing', 'the unrecovered revert survives, right after the counts');
  ok(lines(t).slice(5, -1).every(l => l === 'Waiting for you:' || records.some(r => l.startsWith(`- #${r.pr} `))), 'no line is cut in the middle');
  ok(m.morning.facts.waits.blocked.length === 12, 'the facts keep all 12');
  const big = Array.from({ length: 40 }, (_, i) => ({ pr: 5000 + i, lane: 'BLOCKED', class: 'check-failing-after-retry', package: `ghcr.io/example/package-with-a-long-name-${i}`, reason: `failed again on abcdef12 after one retry on 12345678 at 2026-11-13T06:00:00Z: Some Long Workflow Name / some-very-long-check-name-${i} (FAILURE)` }));
  const m2 = compose({ records: big, doc: doc13, verify: verified13 });
  ok(bytes(m2.morning.text) <= 1000 && /Merged 5 /.test(m2.morning.text) && /Waiting for you: 40 items/.test(m2.morning.text), 'forty blocked PRs: the merge count and the 40 survive the cut');
  const fx = S.fit(['x'.repeat(1200), 'y'], 1000);
  ok(bytes(fx.text) <= 1000 && /\nand 1 more line$/.test(fx.text), 'a first line too long on its own is cut by characters, still within the limit ("and 1 more line")');
  const u = S.fit(['é'.repeat(600), 'z'], 1000);
  ok(bytes(u.text) <= 1000 && !u.text.includes('\ufffd') && /and 1 more line$/.test(u.text), 'the limit is in bytes, and no character is split');
}

// ── 7. hostile text ─────────────────────────────────────────────────────────────────
section('7. hostile PR titles and package names');
{
  const evil = 'evil$(touch /tmp/pwned)`id`;rm -rf /\n- #1 fake line\u001b[31mred‮esrever​ ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 @/etc/passwd ' + 'x'.repeat(300);
  const records = [{ pr: 6000, lane: 'BLOCKED', class: 'check-failing-after-retry', package: evil, title: evil, reason: evil }];
  const t = compose({ records }).morning.text;
  const line = lines(t).find(l => l.startsWith('- #6000'));
  ok(!!line && !lines(t).some(l => l.startsWith('- #1 fake line')), 'a newline in outside text cannot make a line of its own');
  ok(!/[\u0000-\u001f\u007f‮​]/.test(t.replace(/\n/g, '')), 'no control or format characters (ESC, RLO, zero-width) reach the text');
  ok(!t.includes('ghp_ABCDEFGHIJ') && t.includes('[redacted]'), 'a token shape is redacted');
  ok(bytes(line) < 260, `the name and the reason are capped (${bytes(line)} bytes)`);
  ok(line.includes('evil$('), 'shell metacharacters stay as text: the sender passes it as data (gate-morning-selftest.sh proves nothing runs)');
  ok(S.clean('a\tb\r\nc', 10) === 'a b c', 'clean folds whitespace');
}

// ── 8. the window ───────────────────────────────────────────────────────────────────
section('8. the window: from the last night\'s record, each boundary once');
{
  const mk = (pr, mergedAt) => V.docOf([{ ...vfx.prs[0], number: pr, mergedAt, mergeCommit: { oid: String(pr % 10).repeat(40) } }], { repo: REPO, rules, at: '2026-11-13T12:00:00Z' });
  const parts = [doc13, mk(4950, '2026-11-12T16:00:00Z'), mk(4951, '2026-11-12T09:00:00Z'), mk(4952, PREV.composed_at), mk(4953, new Date(LAST).toISOString())];
  const doc = { events: parts.flatMap(d => d.events), items: Object.assign({}, ...parts.map(d => d.items)) };
  const m = S.composeMorning({ now: LAST, mode: 'dryrun', rules, prevText: JSON.stringify(PREV), records: [], verify: {}, doc, historyNote: null, claims: [] });
  const prs = m.morning.facts.merged.map(x => x.pr);
  ok(m.morning.window_from === PREV.composed_at && prs.includes(4950), 'a merge by day (16:00Z on the 12th) is in the next morning\'s summary');
  ok(!prs.includes(4951), 'a merge already in the last summary is not repeated');
  ok(!prs.includes(4952), 'a merge at exactly the last summary\'s time was in that summary: not repeated (the window opens after it)');
  ok(prs.includes(4953), 'a merge at exactly this tick\'s time is in this summary');
  const m3 = compose({ prevText: null });
  ok(Date.parse(m3.morning.window_from) === LAST - 24 * 3600e3, 'no last record: the last 24 hours');
  const m4 = compose({ doc: null });
  ok(lines(m4.morning.text)[1] === 'Merged and reverted: not known (the update history could not be read).' && m4.morning.facts.unreadable.length === 1, 'the history unreadable: said, not "nothing"');
  ok(/Health checks after merging: not known/.test(compose({ verify: null }).morning.text), 'the verifier\'s records unreadable: said');
}

// ── 9. the writes ───────────────────────────────────────────────────────────────────
section('9. the writes: data.morning and the delivery copy');
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

// ── 10. the samples ─────────────────────────────────────────────────────────────────
section('10. the samples: the night of 2026-11-13 (read-only), and a made-up busy night');
{
  const m = S.composeMorning({ now: Date.parse(mfx.last_tick), mode: 'dryrun', rules, prevText: JSON.stringify({ ...SAME, window_from: mfx.prev_composed_at }), records: mfx.records,
    verify: verified13, doc: doc13, historyNote: { needs_person: false, line: '' }, claims: [] });
  const text = m.morning.text;
  ok(bytes(text) <= 1000 && /Merged 5 since 05:45 12 Nov \(Renovate 4, the shepherd 1\)\. Reverted 0\./.test(text) && /5 passed, 0 still being checked, 0 failed\. Waiting for you: 4 items\./.test(text) && /#4757 .*blocked/.test(text) && /#4848 .*blocked/.test(text), 'the lane merged nothing; Renovate and the shepherd merged; two PRs blocked, two majors');
  if (SAMPLE) console.log(`\n----- sample 2026-11-13 (${bytes(text)} bytes) -----\n${text}\n-----`);
  const busy = S.composeMorning({ now: at('2026-11-20T10:45:03Z'), mode: 'live', rules, prevText: JSON.stringify({ ...SAME, night: '2026-11-20', composed_at: '2026-11-20T10:30:03Z', window_from: '2026-11-19T10:45:00Z', ticks_completed: 19 }),
    records: [...mfx.records, ...Array.from({ length: 6 }, (_, i) => ({ pr: 4880 + i, lane: 'BLOCKED', class: 'check-failing-after-retry', package: `ghcr.io/example/app-${i}`, reason: `failed again on abcdef12 after one retry on 12345678 at 2026-11-20T06:00:00Z: Flux Local - Test (main) (FAILURE)` }))],
    verify: { a: vrec(4890, 'ghcr.io/traefik/helm/traefik', { practice: false, verdict: 'failed', attribution: 'own-regression', verdict_at: '2026-11-20T08:15:00.000Z', failing: [{ fact: 'pods', class: 'crash-loop' }] }), b: vrec(4891, 'ghcr.io/seerr-team/seerr', { practice: false, verdict_at: '2026-11-20T09:00:00.000Z' }), c: vrec(4892, 'rhasspy/wyoming-piper', { practice: false, verdict: 'not-yet', final: false, verdict_at: '2026-11-20T10:45:03.000Z', waiting_for: ['stable: recorded 300 s ago'] }) },
    doc: { events: [ev('merged', '2026-11-20T06:40:00Z', 't', 4890, { actor: { kind: 'lane' } }), ev('merged', '2026-11-20T07:10:00Z', 's', 4891, { actor: { kind: 'lane' } }), ev('merged', '2026-11-20T03:12:00Z', 'p', 4892, { actor: { kind: 'renovate', login: 'renovate[bot]' } }),
      ev('revert-merged', '2026-11-20T08:40:00Z', 't', 4890), ev('recovery-failed', '2026-11-20T09:30:00Z', 't', 4890, { reason: 'traefik pods still crash-looping after the revert merged' })],
      items: { t: { name: 'ghcr.io/traefik/helm/traefik', to: '42.5.0' }, s: { name: 'ghcr.io/seerr-team/seerr', to: 'v3.6.0' }, p: { name: 'rhasspy/wyoming-piper', to: '2.6.0' } } },
    historyNote: { needs_person: false }, claims: [{ pr: 4890, outcome: 'claimed', holder: 'lane' }] });
  const bt = busy.morning.text;
  ok(bytes(bt) <= 1000 && lines(bt)[3] === 'Reverted and NOT recovered:' && lines(bt)[4].startsWith('- #4890 ') && /Reverted 1 \(1 did NOT recover\)/.test(bt), 'the busy night keeps the counts and the unrecovered revert first');
  ok(lines(bt)[5] === 'Failed health checks:' && /^- #4890 ghcr\.io\/traefik\/helm\/traefik: pods crash-loop; caused by this update \(no other change reached the app\); the night lane has taken it$/.test(lines(bt)[6]), 'and the failed check right after it, with its PR and who has taken it (the review: only "1 failed" survived)', lines(bt).slice(5, 7).join(' | '));
  ok(lines(bt).indexOf('Waiting for you:') > 6 && /\nand \d+ more lines$/.test(bt), 'then what waits; the cut counts lines');
  const beforeCut = t => { const l = lines(t); return l[l.length - 2] || ''; };
  ok(!/:$/.test(beforeCut(text)) && !/:$/.test(beforeCut(bt)), 'a cut never leaves a heading as the last line kept (it would head nothing)', `${beforeCut(text)} | ${beforeCut(bt)}`);
  const hd = S.fit(['a.', 'Heading:', '- x'.padEnd(990, 'x'), 'Other:', '- y'], 1000);
  ok(!/\nHeading:\nand/.test(hd.text) && lines(hd.text).at(-2) !== 'Heading:' && /and 4 more lines$/.test(hd.text), 'fit: the heading goes with its cut item', hd.text.slice(0, 80));
  if (SAMPLE) console.log(`\n----- sample, a made-up busy night (${bytes(bt)} bytes) -----\n${bt}\n-----`);
}

console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
