#!/usr/bin/env node
// Offline morning-template tests (stories 554, 890). The fixtures exercise the stored facts,
// count window, phone byte limit, claims and delivery copy. No child, network, clock wait or
// real delivery. --sample prints the real 18 Nov record through the current renderer.
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
const real18 = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/morning-20261118.json'), 'utf8'));
const REPO = 'thaynes43/haynes-ops';
const bytes = s => Buffer.byteLength(s, 'utf8');
const at = s => Date.parse(s);
const lines = t => t.split('\n');
const SAMPLE = process.argv.includes('--sample');
let passes = 0, fails = 0;
const ok = (cond, name, detail = '') => { if (cond) { passes++; console.log(`ok    ${name}`); } else { fails++; console.log(`FAIL  ${name}${detail ? `: ${detail}` : ''}`); } };
const section = s => console.log(`\n== ${s}`);
const LAST = at('2026-11-13T10:45:05.419Z');
const PREV = { schema: S.SUMMARY_SCHEMA, night: '2026-11-12', composed_at: '2026-11-12T10:45:00.000Z', window_from: '2026-11-11T10:45:00.000Z', ticks_completed: 19 };
const SAME = { ...PREV, night: '2026-11-13', composed_at: '2026-11-13T10:30:05.000Z', window_from: PREV.composed_at };
const doc13 = V.docOf(vfx.prs, { repo: REPO, rules, at: '2026-11-13T12:00:00Z' });
const vrec = (pr, name, extra = {}) => ({ pr, name, practice: true, verdict: 'verified', verdict_at: '2026-11-13T10:30:00.000Z', final: true, ...extra });
const verified13 = Object.fromEntries([[4869, 'ghcr.io/seerr-team/seerr'], [4870, 'rhasspy/wyoming-piper'], [4871, 'anthropics/claude-code-action'], [4873, 'ghcr.io/traefik/helm/traefik'], [4874, 'mirror.gcr.io/cloudflare/cloudflared']].map(([pr, n]) => [`i${pr}`, vrec(pr, n)]));
const compose = (o = {}) => S.composeMorning({ now: LAST, mode: 'dryrun', rules, prevText: JSON.stringify(SAME), records: [], verify: {}, doc: { events: [], items: {} }, historyNote: { needs_person: false, line: 'ok' }, claims: [], ...o });
const ev = (type, atS, item, pr, extra = {}) => ({ type, at: atS, item_id: item, carrier: { pr }, ...extra });
const render = (facts, cfg = rules.night.summary) => S.composeText(facts, { now: at(real18.composed_at), cfg, tz: rules.night.timezone, ticks: real18.ticks_completed, finished: real18.finished });
const first = m => lines(m.morning.text)[0];

section('0. configuration');
{
  const c = S.summaryConfig(rules);
  ok(c.ok && c.max_bytes === 1000 && c.last_tick_local === '05:45' && c.ticks_per_night === undefined, '1000-byte limit, last tick 05:45, no fixed tick count');
  ok(!S.summaryConfig({ night: { summary: { ...rules.night.summary, max_bytes: 1100 } } }).ok, 'limit above the sender clamp refused');
  ok(!S.summaryConfig({ night: { summary: { ...rules.night.summary, max_bytes: 199 } } }).ok, 'limit below 200 refused');
  ok(!S.summaryConfig({ night: { summary: { ...rules.night.summary, last_tick_local: '25:45' } } }).ok, 'invalid last hour refused');
  ok(!compose({ rules: { night: { ...rules.night, summary: { ...rules.night.summary, max_bytes: 0 } } } }).ok, 'invalid config produces no morning record');
  const hr = readFileSync(join(RES, '../helmrelease.yaml'), 'utf8');
  ok(/schedule: "\*\/15 1-5 \* \* \*"/.test(hr) && /timeZone: America\/New_York/.test(hr), 'schedule still ends at 05:45 New York');
}

section('1. quiet night and modes');
{
  const m = compose({ records: [{ pr: 4752, lane: 'LANE-A', class: 'no-cluster-effect', package: 'p' }] });
  const t = m.morning.text;
  ok(m.ok && bytes(t) <= 1000 && m.morning.lines_cut === 0, 'quiet night fits without cuts');
  ok(t === 'Updates 13 Nov (dry-run): 0 in, 0 reverted, 0 failed, 0 need you', 'one line carries the date, mode and all zero counts', t);
  ok(lines(t).length === 1 && !/NEED YOU|FAILED|REVERTED|UPGRADED/.test(t), 'no empty sections');
  ok(!/passed|practice|still being checked|\btick|claim|signature/.test(t), 'no passed count, zero checking count or internal descriptions');
  ok(first(compose({ mode: 'live' })) === 'Updates 13 Nov: 0 in, 0 reverted, 0 failed, 0 need you', 'live omits the dry-run label');
  ok(!compose({ mode: 'live', verify: { a: vrec(4869, 'seerr') } }).morning.text.includes('practice'), 'practice verdicts add no wording in live mode');
  ok(first(compose({ mode: 'live', verify: { a: vrec(4869, 'seerr', { practice: false }) } })) === 'Updates 13 Nov: 0 in, 0 reverted, 0 failed, 0 need you', 'a passed live check adds no text');
  const pending = compose({ verify: { a: vrec(1, 'p', { verdict: 'not-yet', final: false, waiting_for: ['stable'] }) } });
  ok(first(pending).endsWith(', 1 still being checked'), 'nonzero checking count appears on line 1');
  ok(pending.morning.facts.checking[0].why === 'stable', 'checking detail survives in facts');
}

section('2. the stored night of 18 Nov, rendered without changing its facts');
{
  const before = JSON.stringify(real18);
  const r = render(real18.facts);
  const expected = `Updates 18 Nov (dry-run): 10 in, 0 reverted, 1 failed, 5 need you
NEED YOU:
- #4757 antlr4-python3-runtime: check failing
- #4848 kyverno: check failing
- #4376 node: major
- #4758 httpcore2: restarts v1
- #4764 av: major
FAILED:
- #4918 ytdl-sub: job failed, by update
UPGRADED:
- claude-code-action v1.0.281
- cloudflared 2026.11.1
- wyoming-piper 2.5.4
- audiobookshelf 2.38.0
- k... 93.4.0
- nvidia-device-plugin 0.20.2
- cilium 1.20.5
- paperless-ngx 3.5.1
- cloudna... 0.29.2
- traefik 42.4.1`;
  // The legacy record already truncated two chart names. Do not guess or special-case them.
  ok(r.text === expected, 'real sample matches the fixed template exactly', r.text);
  ok(r.cut === 0 && bytes(r.text) <= 1000, `all punch and upgrade lines fit (${bytes(r.text)} bytes)`);
  ok(real18.facts.merged.length === 11 && r.text.split('UPGRADED:\n')[1].split('\n').length === 10, '11 merges become ten name/version lines');
  ok(!r.text.includes('v1.0.280') && r.text.includes('v1.0.281'), 'newest claude-code-action target shown once');
  ok(r.text.includes('#4918 ytdl-sub: job failed, by update'), 'stored attribution preserved, with no story 879 special case');
  ok(!/practice|passed|Renovate|since |\[/.test(r.text), 'sentences, actor split, passed count and practice wording removed');
  ok(JSON.stringify(real18) === before, 'rendering does not change the stored sample or facts');
  const reversed = { ...real18.facts, merged: [...real18.facts.merged].reverse() };
  ok(render(reversed).text.includes('- claude-code-action v1.0.281') && !render(reversed).text.includes('v1.0.280'), 'newest target is chosen by time even with reverse input');
  if (SAMPLE) console.log(`\n----- real 2026-11-18 (${bytes(r.text)} bytes) -----\n${r.text}\n-----`);
}

section('2b. fresh facts shorten full names before the old name cap');
{
  const name = 'ghcr.io/prometheus-community/charts/kube-prometheus-stack';
  const doc = { events: [ev('merged', '2026-11-13T08:00:00Z', 'k', 9)], items: { k: { name, to: '93.4.0' } } };
  const m = compose({ doc });
  ok(m.morning.text.includes('- kube-prometheus-stack 93.4.0'), 'full chart name survives a registry path longer than max_name_chars');
  ok(m.morning.facts.merged[0].name === S.clean(name, 40), 'legacy name field keeps its existing cap');
  ok(m.morning.facts.merged[0].short_name === 'kube-prometheus-stack', 'additive short_name carries the intact final component');
  const verify = { a: vrec(8, name, { verdict: 'failed', attribution: 'own-regression', failing: [{ fact: 'pods', class: 'not-ready' }] }) };
  ok(compose({ verify }).morning.text.includes('#8 kube-prometheus-stack: not ready, by update'), 'failure names shorten before capping too');
  const rec = [{ pr: 10, lane: 'BLOCKED', class: 'facts-incomplete', package: name }];
  ok(compose({ records: rec }).morning.text.includes('#10 kube-prometheus-stack: facts unreadable'), 'waiting names shorten before capping too');
  const long = compose({ records: [{ ...rec[0], package: `registry/${'x'.repeat(80)}` }] }).morning;
  ok(long.facts.waits.blocked[0].short_name.length === 40 && long.text.includes('...: facts unreadable'), 'long final components remain capped');
}

section('3. waiting items, combined reasons and fixed vocabulary');
{
  const verify = {
    a: vrec(4810, 'ghcr.io/thaynes43/upgrade-agent', { verdict: 'not-yet', final: true, gave_up: true, needs_person: true, nights: ['2026-11-12', '2026-11-13'], why: 'NEEDS A PERSON: open on 2 nights; pin not built' }),
    b: vrec(4811, 'ghcr.io/thaynes43/upgrade-shepherd', { verdict: 'not-yet', final: false, pin: { state: 'awaiting' }, facts: [{ fact: 'pin', detail: 'awaiting the digest pin of upgrade-shepherd: pin PR is blocked' }] }),
  };
  const m = compose({ verify, records: mfx.records, historyNote: { needs_person: true, line: 'HISTORY NEEDS A PERSON: the backfill comparison disagrees' } });
  const t = m.morning.text;
  ok(first(m).includes('7 need you'), 'seven distinct waiting items including history');
  ok(t.includes('- #4810 upgrade-agent: open 2 nights; checks abandoned'), 'overdue nights and abandoned checks survive in short form');
  ok(t.includes('- #4811 upgrade-shepherd: pin pending'), 'digest pin pending');
  ok(t.includes('- #4757 antlr4-python3-runtime: check failing') && t.includes('- #4848 kyverno: check failing'), 'both failing-check classes share the same reason');
  ok(t.includes('- #4376 node: major') && t.includes('- #4764 av: major'), 'each owner decision gets a line');
  ok(t.includes('- history: needs review'), 'history without a PR gets its own waiting entry');
  ok(lines(t).filter(l => l === 'NEED YOU:').length === 1, 'one waiting heading');
  ok(m.morning.facts.waits.needs_person[0].why === 'pin not built' && m.morning.facts.waits.awaiting_pin[0].why === 'pin PR is blocked', 'raw waiting explanations stay in facts');
  ok(m.morning.facts.waits.blocked.find(x => x.pr === 4757).why.includes('Build and Push ControlFoley'), 'failed workflow name stays in facts');
  const own = vrec(4811, 'upgrade-shepherd', { verdict: 'not-yet', final: false, needs_person: true, nights: ['a', 'b'], pin: { state: 'awaiting' }, facts: [{ fact: 'pin', detail: 'pin PR blocked' }] });
  const both = compose({ verify: { own }, records: [{ pr: 4811, package: 'upgrade-shepherd', lane: 'LANE-B-OWNER', class: 'major' }] });
  ok(first(both).includes('1 need you'), 'overdue, pin and owner reasons count as one PR');
  ok(lines(both.morning.text).filter(l => l.startsWith('- #4811 ')).length === 1 && both.morning.text.includes('open 2 nights; pin pending; major'), 'all reasons retained on one line');
  ok(first(compose({ verify: { own }, historyNote: { needs_person: true, line: 'review' } })).includes('2 need you'), 'history remains a separate item');
  for (const [klass, reason] of Object.entries({ 'checks-incomplete': 'checks pending', 'facts-incomplete': 'facts unreadable', prerelease: 'prerelease', hold: 'held', 'vet-hold': 'held by review', 'rule-owner': 'owner decision', 'v1-restart': 'restarts v1' })) {
    ok(compose({ records: [{ pr: 1, package: 'p', lane: klass.startsWith('checks') || klass.startsWith('facts') ? 'BLOCKED' : 'LANE-B-OWNER', class: klass }] }).morning.text.includes(`- #1 p: ${reason}`), `fixed waiting reason: ${klass}`);
  }
  ok(compose({ records: [{ pr: 1, package: 'p', lane: 'BLOCKED', class: 'future-class', reason: 'long explanation' }] }).morning.text.includes('- #1 p: blocked'), 'unknown blocked class has a bounded fallback');
}

section('3b. failed checks, attribution and every ownership outcome');
{
  const failed = (pr, extra = {}) => vrec(pr, `registry/pkg-${pr}`, { verdict: 'failed', attribution: 'own-regression', failing: [{ fact: 'helmreleases', class: 'not-applied' }], ...extra });
  const t = (rec, claim) => compose({ verify: { a: rec }, claims: claim ? [{ pr: rec.pr, ...claim }] : [] }).morning.text;
  ok(t(failed(1)).includes('- #1 pkg-1: rollout failed, by update'), 'attributed rollout failure');
  ok(t(failed(1, { attribution: 'cannot-tell-which-change' })).includes('rollout failed, cause unclear'), 'unattributed failure does not blame the update');
  ok(t(failed(1, { attribution: null })).includes('cause unclear'), 'missing attribution is explicitly unclear');
  ok(t(failed(1), { outcome: 'claimed' }).includes('; lane owns fix'), 'claimed, working');
  ok(t(failed(1), { outcome: 'claimed', lane_state: 'reverting' }).includes('; lane reverting'), 'reverting only when the claim says so');
  ok(t(failed(1), { outcome: 'triage-holds' }).includes('; triage owns fix'), 'triage holder');
  ok(t(failed(1), { outcome: 'lost', holder: 'triage' }).includes('; triage owns fix'), 'lost to triage');
  ok(t(failed(1), { outcome: 'lost' }).includes('; fix unclaimed'), 'lost without a holder is unclaimed');
  ok(t(failed(1), { outcome: 'lane-holds' }).includes('; lane fixing another update'), 'lane holder with another update');
  ok(t(failed(1), { outcome: 'budget-exhausted' }).includes('; fix budget spent; no revert this run'), 'spent budget and no revert both stated');
  const unreadable = t(failed(1), { outcome: 'unreadable' });
  ok(lines(unreadable)[1] === 'UNREADABLE: owner #1' && unreadable.includes('; owner unreadable'), 'unreadable ownership is trouble immediately after line 1 too');
  for (const outcome of ['not-own-regression', 'would-claim', 'not-in-set']) ok(t(failed(1), { outcome }).includes('; fix unclaimed'), `${outcome}: no automatic fix claimed`);
  ok(S.claimWords(null) === '', 'no claim adds no invented owner');
  ok(!/practice|\[|signature|claim/.test(t(failed(1), { outcome: 'claimed' })), 'failure line has no practice or internal prose');
  ok(t(failed(1)).includes('\nFAILED:\n') && first(compose({ verify: { a: failed(1) } })).includes('1 failed'), 'failure heading and count agree');
  for (const [klass, words] of Object.entries({ 'job-failed': 'job failed', 'crash-loop': 'pods crashing', 'critical-alert': 'critical alert', 'wrong-image': 'wrong image', churn: 'pods replaced', 'restart-growth': 'restarts growing', 'not-ready': 'not ready', unknown: 'checks failed' })) {
    ok(t(failed(1, { failing: [{ fact: 'pods', class: klass }] })).includes(`: ${words}, by update`), `fixed failure reason: ${klass}`);
  }
  ok(t(failed(1, { failing: [{ fact: 'pods', class: 'crash-loop' }, { fact: 'jobs', class: 'job-failed' }] })).includes('job failed, pods crashing, by update'), 'multiple failure categories stay visible');
}

section('4. reverts and fixed section order');
{
  const doc = { events: [ev('revert-merged', '2026-11-13T06:00:00Z', 'x', 4950), ev('recovery-verified', '2026-11-13T07:00:00Z', 'x', 4950), ev('revert-merged', '2026-11-13T06:30:00Z', 'y', 4951), ev('recovery-failed', '2026-11-13T07:30:00Z', 'y', 4951, { reason: 'traefik still crash-looping\nafter the revert' }), ev('revert-merged', '2026-11-13T10:00:00Z', 'z', 4952)], items: { x: { name: 'registry/seerr', to: 'v3.5.2' }, y: { name: 'registry/traefik', to: '42.5.0' }, z: { name: 'piper', to: '2.5.3' } } };
  const m = compose({ doc });
  const t = m.morning.text;
  ok(first(m).includes('3 reverted'), 'one revert per item, not one per recovery event');
  ok(t.includes('- #4950 seerr v3.5.2: recovered'), 'recovered revert with version');
  ok(t.includes('- #4951 traefik 42.5.0: not recovered; pods crashing'), 'unrecovered revert and failure category');
  ok(t.includes('- #4952 piper 2.5.3: recovery unchecked'), 'unchecked recovery remains distinct');
  ok(m.morning.facts.reverted.length === 3 && m.morning.facts.reverted.find(r => r.pr === 4951).reason === 'traefik still crash-looping after the revert', 'full recovery reason remains in facts');
  const facts = structuredClone(real18.facts);
  facts.reverted = m.morning.facts.reverted;
  const all = render(facts).text;
  ok(all.indexOf('\nNEED YOU:\n') < all.indexOf('\nFAILED:\n') && all.indexOf('\nFAILED:\n') < all.indexOf('\nREVERTED:\n') && all.indexOf('\nREVERTED:\n') < all.indexOf('\nUPGRADED:\n'), 'fixed order: NEED YOU, FAILED, REVERTED, UPGRADED');
}

section('5. finish by the last local tick, including DST');
{
  const start = compose({ prevText: JSON.stringify(PREV), now: at('2026-11-13T06:00:00Z') });
  ok(start.morning.ticks_completed === 1 && !start.morning.finished, 'first tick resets the counter and is incomplete');
  ok(lines(start.morning.text)[1] === 'NIGHT INCOMPLETE', 'incomplete night directly below summary');
  const later = compose({ prevText: JSON.stringify(start.morning), now: at('2026-11-13T07:30:00Z') });
  ok(later.morning.ticks_completed === 2 && later.morning.window_from === start.morning.window_from, 'same night increments ticks and keeps the window');
  ok(later.delivery.morning_last_tick === '02:30' && later.delivery.morning_last_tick_expected === '05:45', 'delivery retains the last and expected tick');
  ok(compose().morning.finished && !compose().morning.text.includes('INCOMPLETE'), '05:45 finishes without a trouble line');
  const spring = compose({ now: at('2026-03-08T09:45:04Z'), prevText: JSON.stringify({ ...SAME, night: '2026-03-08', ticks_completed: 15 }) });
  ok(spring.morning.finished && spring.morning.ticks_completed === 16 && first(spring).startsWith('Updates 8 Mar'), 'spring forward: 16 ticks, finished');
  const fall = compose({ now: at('2026-11-01T10:45:04Z'), prevText: JSON.stringify({ ...SAME, night: '2026-11-01', ticks_completed: 23 }) });
  ok(fall.morning.finished && fall.morning.ticks_completed === 24 && !fall.morning.text.includes('INCOMPLETE'), 'fall back: 24 ticks, finished');
  ok(compose({ prevText: '{garbage' }).morning.ticks_completed === 1 && compose({ prevText: null }).ok, 'missing or unreadable previous record starts anew');
}

section('6. byte limit: upgrades go before any punch-list item');
{
  const facts = structuredClone(real18.facts);
  facts.merged = Array.from({ length: 30 }, (_, i) => ({ pr: 6000 + i, name: `registry/package-${i}`, to: '123.456.789', at: real18.composed_at }));
  const r = render(facts, { ...rules.night.summary, max_bytes: 450 });
  const t = r.text;
  ok(bytes(t) <= 450 && r.cut > 0 && /\n\+\d+ more$/.test(t), 'overflow fits with a short cut marker');
  ok(facts.waits.blocked.every(x => t.includes(`#${x.pr} `)) && facts.waits.owner.every(x => t.includes(`#${x.pr} `)) && t.includes('#4918 ytdl-sub'), 'the entire punch list survives upgrade truncation');
  const keptUpgrades = (t.split('UPGRADED:\n')[1] || '').split('\n').filter(l => l.startsWith('- ')).length;
  ok(Number(/\+(\d+) more$/.exec(t)[1]) === 30 - keptUpgrades, 'marker counts omitted upgrades, excluding the heading');
  ok(lines(t)[0].includes('30 in'), 'summary keeps the total, including upgrades represented by the cut marker');
  ok(!/UPGRADED:\n\+/.test(t), 'no dangling upgraded heading');
  ok(facts.merged.length === 30, 'full list retained in facts');
  const big = compose({ records: Array.from({ length: 40 }, (_, i) => ({ pr: 5000 + i, lane: 'BLOCKED', class: 'facts-incomplete', package: `registry/package-${i}` })), doc: doc13, verify: verified13 }).morning;
  ok(bytes(big.text) <= 1000 && big.text.includes('5 in') && big.text.includes('40 need you'), 'even an oversized punch list keeps all counts');
  ok(!big.text.includes('UPGRADED:') && big.lines_cut > 0, 'no upgrade survives once a punch-list entry must be cut');
  ok(big.facts.waits.blocked.length === 40 && big.facts.merged.length === 5, 'facts keep all cut entries');
  const whole = S.fit(['summary', 'NEED YOU:', '- #1 p: major', 'UPGRADED:', ...Array.from({ length: 12 }, (_, i) => `- p${i} ${'x'.repeat(60)}`)], 90);
  ok(whole.text.includes('- #1 p: major') && !whole.text.includes('UPGRADED:\n+'), 'heading goes with its last omitted entry');
  const fx = S.fit(['x'.repeat(1200), 'y'], 1000);
  ok(bytes(fx.text) <= 1000 && fx.text.endsWith('\n+1 more'), 'oversized first line has a bounded fallback');
  const u = S.fit(['é'.repeat(600), 'z'], 1000);
  ok(bytes(u.text) <= 1000 && !u.text.includes('\ufffd') && u.text.endsWith('\n+1 more'), 'UTF-8 byte limit never splits a character');
  const under = S.fit(['summary', 'UPGRADED:', '- p 1'], 1000);
  ok(under.cut === 0 && under.text === 'summary\nUPGRADED:\n- p 1', 'fitting text is unchanged');
}

section('7. hostile outside text stays data');
{
  const evil = 'evil$(touch pwned)`id`;x\n- #1 fake line\u001b\u202e\u200b ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ' + 'x'.repeat(100);
  const m = compose({ records: [{ pr: 6000, lane: 'BLOCKED', class: 'check-failing', package: evil, reason: evil }] });
  const t = m.morning.text;
  ok(!lines(t).some(l => l.startsWith('- #1 fake line')), 'outside newline cannot inject an item');
  ok(!/[\p{Cc}\p{Cf}]/u.test(t.replace(/\n/g, '')), 'no control or format characters reach the message');
  ok(!t.includes('ghp_ABCDEFGHIJ') && !JSON.stringify(m.morning.facts).includes('ghp_ABCDEFGHIJ'), 'tokens redacted in text and facts');
  ok(lines(t).find(l => l.startsWith('- #6000')).length < 100, 'outside name and fixed reason bounded');
  ok(t.includes('evil$('), 'shell syntax remains plain text');
  ok(S.clean('a\tb\r\nc', 10) === 'a b c', 'whitespace folded');
  ok(S.clean('ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') === '[redacted]', 'token shape redacted before capping');
}

section('8. one shared window and old/new record compatibility');
{
  const mk = (pr, date) => V.docOf([{ ...vfx.prs[0], number: pr, mergedAt: date, mergeCommit: { oid: String(pr % 10).repeat(40) } }], { repo: REPO, rules, at: '2026-11-13T12:00:00Z' });
  const parts = [doc13, mk(4950, '2026-11-12T16:00:00Z'), mk(4951, '2026-11-12T09:00:00Z'), mk(4952, PREV.composed_at), mk(4953, new Date(LAST).toISOString())];
  const doc = { events: parts.flatMap(d => d.events), items: Object.assign({}, ...parts.map(d => d.items)) };
  const m = compose({ prevText: JSON.stringify(PREV), doc });
  const prs = m.morning.facts.merged.map(x => x.pr);
  ok(m.morning.window_from === PREV.composed_at && prs.includes(4950), 'daytime merge belongs to the next morning');
  ok(!prs.includes(4951), 'merge before previous summary excluded');
  ok(!prs.includes(4952), 'open lower boundary excludes an already reported merge');
  ok(prs.includes(4953), 'closed upper boundary includes the current tick');
  ok(Date.parse(compose({ prevText: null }).morning.window_from) === LAST - 24 * 3600e3, 'no previous record: 24-hour fallback');
  const noHistory = compose({ doc: null }).morning;
  ok(lines(noHistory.text)[1] === 'UNREADABLE: history' && noHistory.facts.unreadable.length === 1, 'unreadable history directly below summary');
  ok(lines(noHistory.text)[0].includes('? in, ? reverted'), 'unknown history counts never claim zero');
  const noChecks = compose({ verify: null }).morning.text;
  ok(lines(noChecks)[1] === 'UNREADABLE: checks' && lines(noChecks)[0].includes('? failed'), 'unreadable verifier directly below summary, unknown count');
  const both = compose({ doc: null, verify: null, now: at('2026-11-13T06:00:00Z') }).morning.text;
  ok(lines(both)[1] === 'NIGHT INCOMPLETE; UNREADABLE: history; checks', 'all trouble on one line immediately below summary');
  const windowVerify = { old: vrec(1, 'old', { verdict_at: PREV.composed_at }), future: vrec(2, 'future', { verdict_at: '2026-11-14T07:00:00Z' }), yes: vrec(3, 'in'), oldFail: vrec(4, 'old-fail', { verdict: 'failed', attribution: 'own-regression', verdict_at: '2026-11-11T10:00:00Z' }) };
  const wv = compose({ verify: windowVerify }).morning;
  ok(wv.facts.verified.length === 1 && wv.facts.verified[0].pr === 3 && wv.facts.failed.length === 0, 'final check verdicts use the same open/closed time window');
  ok(wv.facts.checking.length === 0 && !wv.text.includes('still being checked'), 'old final verdict does not become a pending check');
  for (const prior of [real18, { ...real18, text: render(real18.facts).text }]) {
    const next = compose({ now: at('2026-11-19T10:45:00Z'), prevText: JSON.stringify(prior) }).morning;
    ok(next.window_from === real18.composed_at && next.ticks_completed === 1, `next night follows ${prior.text.startsWith('Night of') ? 'old' : 'new'} text using JSON metadata`);
  }
  ok(S.parseMorning(JSON.stringify(real18)).night === '2026-11-18', 'legacy v1 record parses');
  ok(S.parseMorning(JSON.stringify(compose().morning)).schema === S.SUMMARY_SCHEMA, 'new text keeps the same record schema');
  ok(S.parseMorning('not json') === null && S.parseMorning('{"schema":"wrong","night":"2026-11-18"}') === null, 'invalid and wrong-schema records rejected');
  const future = compose({ doc: { events: [ev('merged', '2026-11-14T07:00:00Z', 'future', 9999)], items: { future: { name: 'future' } } } }).morning;
  ok(future.facts.merged.length === 0 && !future.text.includes('future'), 'future-dated event excluded');
  const login = 'actor\n\u001b\u202eghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  const actor = compose({ doc: { events: [ev('merged', '2026-11-13T07:00:00Z', 'actor', 9997, { actor: { kind: 'person', login } })], items: { actor: { name: 'p' } } } }).morning;
  ok(actor.facts.merged[0].by_login === S.clean(login, 40) && !/[\p{Cc}\p{Cf}]/u.test(actor.facts.merged[0].by_login), 'actor login still sanitized in stored facts');
}

section('9. delivery copy and write boundaries unchanged');
{
  const m = compose();
  ok(Object.keys(m.delivery).every(k => S.DELIVERY_KEYS.includes(k)) && Object.values(m.delivery).every(v => typeof v === 'string') && m.delivery.morning_text === m.morning.text, 'delivery copy has only morning_* string keys and identical text');
  ok(m.morning.schema === S.SUMMARY_SCHEMA && m.morning.composed_at === new Date(LAST).toISOString() && m.morning.night === '2026-11-13', 'record date and schema unchanged');
  ok(m.morning.bytes === bytes(m.morning.text), 'record byte count matches actual UTF-8 text');
  const rn = readFileSync(join(RES, 'run-night.mjs'), 'utf8');
  const keys = /const DELIVERY_KEYS = Object\.freeze\((\[[^\]]+\])\)/.exec(rn)?.[1];
  ok(keys && JSON.stringify(JSON.parse(keys.replace(/'/g, '"'))) === JSON.stringify(S.DELIVERY_KEYS), 'runner writes exactly the composed delivery keys');
  ok(/\(manager === SUMMARY_MANAGER\) !== \(delivery \|\| Object\.keys\(manifest\.data \|\| \{\}\)\.join\(\) === 'morning'\)/.test(rn), 'field manager only owns morning record or delivery copy');
  ok(/if \(ok && morning\?\.ok\) \{/.test(rn) && rn.indexOf('writeLaneState(morningManifest(') > rn.indexOf('writeLaneState(stateManifest({ mode: MODE, updated: summary.at'), 'summary written only after tick state lands');
  ok(/const DELIVERY = \{ name: 'upgrade-orphan-report', namespace: 'upgrade-agent' \};/.test(rn) && /writeLaneState\(deliveryManifest\(morning\.delivery\), SUMMARY_MANAGER\)/.test(rn), 'same server-side delivery apply target');
  ok(!S.DELIVERY_KEYS.includes('morning_sent_night') && /every\(\(\[k, v\]\) => DELIVERY_KEYS\.includes\(k\) && typeof v === 'string'\)/.test(rn), 'lane never owns the sent stamp');
  ok(!S.DELIVERY_KEYS.includes('morning_first_attempt_night'), 'lane never owns the first-attempt stamp');
  const idx = rn.indexOf('S.composeMorning(');
  ok(idx > rn.indexOf('H.historyPhase(') && idx > rn.indexOf('M.writePrRecords('), 'composition after history and PR records');
  const src = readFileSync(join(RES, 'night-summary.mjs'), 'utf8');
  ok(!/child_process|fetch\(|spawn|runReadOnly|run\(/.test(src.replace(/\/\/.*$/gm, '')), 'renderer starts no process and performs no reads');
}

section('10. original 13 Nov fixture retains coverage');
{
  const m = compose({ now: at(mfx.last_tick), prevText: JSON.stringify({ ...SAME, window_from: mfx.prev_composed_at }), records: mfx.records, verify: verified13, doc: doc13 });
  const t = m.morning.text;
  ok(first(m) === 'Updates 13 Nov (dry-run): 5 in, 0 reverted, 0 failed, 4 need you', 'five upgraded names, two blocked PRs and two owner decisions');
  ok(t.includes('- seerr v3.5.2') && t.includes('- traefik 42.4.0'), 'name/version bottom section retained from original fixture');
  ok(m.morning.facts.merged.filter(x => x.by === 'renovate').length === 4 && m.morning.facts.merged.filter(x => x.by === 'shepherd').length === 1, 'actor split retained in facts');
  ok(m.morning.facts.verified.length === 5 && !t.includes('passed'), 'passed verdict list retained in facts only');
  ok(bytes(t) <= 1000 && m.morning.lines_cut === 0, 'whole original sample now fits');
  if (SAMPLE) console.log(`\n----- sample 2026-11-13 (${bytes(t)} bytes) -----\n${t}\n-----`);
}
console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
