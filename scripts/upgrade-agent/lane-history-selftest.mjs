#!/usr/bin/env node
// Self-test for the history of every attempt (story 593): upgrade-history.mjs and
// ConfigMap upgrade-agent/upgrade-ledger.
//
// WHY THIS EXISTS
// ---------------
// The history is what piece 1 remembers: every attempt, and that a reverted version is
// never merged again. It must never lose an event to an overlapping writer, never write the
// same event twice, never forget a sticky state when it rotates, and stay under its cap.
// This test pins, against fakes (no cluster, no network):
//   1. statically: one spawn site, kubectl apply --server-side of upgrade-ledger only,
//      behind its refusals; every read through the read-only runner;
//   2. what a dry-run tick writes: first sight, a new state, lane or class (once), a close,
//      a newer version on the same PR, a merge by someone else, nothing for an unchanged
//      item, nothing for an item the lane cannot name, one record for an item two PRs carry;
//   3. live merge-step events through the format, and a repeated refusal collapsed;
//   4. the backfill: the format's fixture (PR 4817) derived exactly, idempotent, and a
//      count comparison that names a missing PR; reverts; the catch-up window;
//   5. rotation: a 5,000-event history stays under its cap, a reverted version stays
//      reverted, an open item keeps its place, a closed one is forgotten;
//   6. the write: an overlapping writer loses no event, a failure is reported and the next
//      tick writes the same events, a corrupt history is never overwritten;
//   7. terminal events on the PR record: through the publish check, an edit only when it
//      changes, never a new comment;
//   8. the size numbers.
//
// Usage: node scripts/upgrade-agent/lane-history-selftest.mjs
// Requires: node 24. No network, no cluster access.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../..');
const RES = join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/resources');
const H = await import(join(RES, 'upgrade-history.mjs'));
const RF = await import(join(RES, 'record-format.mjs'));
const L = await import(join(RES, 'lane-classify.mjs'));
const M = await import(join(RES, 'night-merge.mjs'));
const rules = L.loadRules(join(RES, 'upgrade-rules.json'));
const cfg = H.historyConfig(rules);
const REPO = 'thaynes43/haynes-ops';
const AUTHORS = rules.renovate_pr_authors;
const fixture = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/upgrade-records/seq-backfill-renovate-automerge.json'), 'utf8'));

let fails = 0, passes = 0;
const ok = (cond, name, detail = '') => { if (cond) passes++; else { fails++; console.log(`FAIL ${name}${detail ? `\n     ${detail}` : ''}`); } };
const T0 = Date.parse('2026-11-13T06:00:00.000Z');
const at = (min = 0) => new Date(T0 + min * 60e3).toISOString();
const sha = c => c.repeat(40);
// a lane record as run-night.mjs writes it (haynes.upgrade-lane/v1), small
const rec = (pr, o = {}) => ({ schema: 'haynes.upgrade-lane/v1', pr, head: sha((pr % 10).toString(16)), fingerprint: null, package: `ghcr.io/example/app${pr}`, from: '1.0.0', to: o.to ?? '1.1.0', packages: [{ name: `ghcr.io/example/app${pr}`, update: 'minor', from: '1.0.0', to: o.to ?? '1.1.0' }],
  update_type: 'minor', lane: 'LANE-A', class: 'cluster-change', reason: `checks pass on #${pr}`, findings: [], rules: [], checks: {}, targets: [`kubernetes/main/apps/x/app${pr}`], own_images: [], ...o });
const scanOf = (list, since = at(-30 * 1440), until = at(0)) => ({ ok: true, list, window: { since, until }, query: 'gh pr list (test)', repo: REPO });
const mergedPr = (n, min, o = {}) => ({ number: n, title: `fix(container): update image ghcr.io/example/app${n} to v2.${n}.0`, author: { login: 'app/renovate' }, mergedAt: at(min), mergedBy: { login: 'app/renovate' }, labels: [{ name: 'renovate/container' }, { name: 'type/minor' }], mergeCommit: { oid: sha('a') }, headRefOid: sha('b'), ...o });
const plan = (doc, o = {}) => H.planHistory({ doc, records: [], openPrs: [], verdict: null, at: at(0), repo: REPO, cfg, scan: scanOf([]), backfilling: false, renovateAuthors: AUTHORS, ...o });
const withMarker = doc => ({ ...doc, meta: { ...doc.meta, backfill: { at: at(-1440), window: { since: at(-31 * 1440), until: at(-1440) }, query: 'q', events: 0, compare: null } } });
const types = p => p.events.map(e => `${e.type}:${e.from ?? 'new'}>${e.to}`);
const stateOf = (doc, id) => H.replay(doc).items.get(id)?.item.state ?? null;
const itemOf = r => RF.fromLaneRecord(r, { repo: REPO, observed_at: at(0) }).id;

ok(cfg.ok, 'config: night.history in upgrade-rules.json is valid', cfg.why);
ok(cfg.max_bytes <= 786432 && cfg.max_bytes < 1024 * 1024, 'config: the cap is at most 768 KiB of data, under the API server\'s 1 MiB');
ok(rules.night.pr_record.fields.includes('terminal'), 'config: the PR record carries terminal (story 593 (d))');

// ── 1. static ───────────────────────────────────────────────────────────────
{
  const strip = s => s.replace(/^\s*\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '');
  const src = strip(readFileSync(join(RES, 'upgrade-history.mjs'), 'utf8'));
  ok((src.match(/\bspawnSync\s*\(/g) || []).length === 1, 'static: upgrade-history.mjs has exactly one spawnSync call site');
  ok(!/(?<![.\w])(execSync|execFileSync|execFile|exec|spawn|fork)\s*\(/.test(src.replace(/spawnSync\s*\(/g, '')) && /import \{ spawnSync \} from 'node:child_process'/.test(src), 'static: no other process API');
  const fn = src.slice(src.indexOf('export function applyHistory('), src.indexOf('\n}\n', src.indexOf('export function applyHistory(')));
  ok(/m\.name !== HISTORY\.name/.test(fn) && fn.indexOf('throw new Error') < fn.indexOf('spawnSync(') && /spawnSync\('kubectl', \['apply', '--server-side', `--field-manager=\$\{manager\}`, '--force-conflicts', '-o', 'json', '-f', '-'\]/.test(fn), 'static: the spawn is a server-side apply of upgrade-ledger, after its refusals');
  ok(/const HISTORY = Object\.freeze\(\{ name: 'upgrade-ledger', namespace: 'upgrade-agent' \}\)/.test(src), 'static: the object name is a constant');
  ok(!/['"](gh|git|flux|curl|helm)['"]\s*,\s*\[[^\]]*['"](merge|comment|edit|close|POST|PATCH|PUT|DELETE)['"]/.test(src) && !/ghWrite|callMerge|writePrRecords/.test(src) && (src.match(/tryRun\(run, 'gh'/g) || []).length === 4, 'static: GitHub is only read, through the runner (4 reads), and nothing here writes to GitHub or merges');
  ok((src.match(/\bfetch\s*\(/g) || []).length === 0, 'static: no fetch');
  const night = strip(readFileSync(join(RES, 'run-night.mjs'), 'utf8'));
  ok((night.match(/H\.historyPhase\(/g) || []).length === 1 && night.indexOf('H.historyPhase(') > night.indexOf('M.laneMergeStep(') && night.indexOf('H.historyPhase(') < night.indexOf('M.writePrRecords('), 'static: run-night.mjs runs the history once, after the merge step and before the PR writes');
  ok(/try \{ hist = H\.historyPhase\([\s\S]*?\} *\n? *catch \(e\) \{ hist = /.test(night), 'static: a thrown history phase is caught; the tick goes on to its state write');
  // a refused manifest never reaches kubectl
  const bad = [[{ ...H.historyManifest(H.emptyDoc(), '5'), metadata: { name: 'upgrade-lane-state', namespace: 'upgrade-agent', resourceVersion: '5' } }, H.MANAGER, 'another ConfigMap'],
    [H.historyManifest(H.emptyDoc(), null), H.MANAGER, 'data without a resourceVersion'], [H.historyManifest(H.emptyDoc(), '5'), 'upgrade-night', 'the tick\'s field manager'],
    [{ ...H.initManifest(), data: { events: '' } }, H.INIT_MANAGER, 'data under the init manager'], [{ ...H.historyManifest(H.emptyDoc(), '5'), data: { events: '' } }, H.MANAGER, 'a partial data write (SSA would drop the other keys)']];
  for (const [m, mgr, what] of bad) { let threw = false; try { H.applyHistory(m, mgr, {}); } catch { threw = true; } ok(threw, `static: applyHistory refuses ${what} before any spawn`); }
}

// ── 2. what a dry-run tick writes ───────────────────────────────────────────────
{
  const a = rec(101), b = rec(102, { lane: 'LANE-B-AGENT', class: 'rule-agent' }), c = rec(103, { lane: 'BLOCKED', class: 'check-failing' });
  const p1 = plan(withMarker(H.emptyDoc()), { records: [a, b, c], openPrs: [101, 102, 103] });
  ok(p1.ok && JSON.stringify(types(p1)) === JSON.stringify(['observed:new>needed', 'observed:new>needed', 'observed:new>blocked']), 'tick: first sight of three open PRs is one observed event each', JSON.stringify(types(p1)));
  ok(p1.events.every(e => RF.checkEvent(e).length === 0 && e.recorded_by === 'lane' && e.actor.kind === 'lane'), 'tick: every event passes the format (checkEvent)');
  ok(p1.events[1].lane === 'B-agent' && p1.events[1].class === 'rule-agent' && p1.events[2].lane === 'A-night', 'tick: observed carries the lane verdict as the format maps it');
  const p2 = plan(p1.doc, { records: [a, b, c], openPrs: [101, 102, 103], at: at(15) });
  ok(p2.ok && p2.events.length === 0 && !p2.changed, 'tick: the same items read again write nothing (and no write is needed)');
  const p2b = plan(p1.doc, { records: [a, b, c], openPrs: [101, 102, 103] });
  ok(p2b.events.length === 0 && !p2b.changed, 'tick: the same tick run twice writes nothing the second time');
  const rebased = rec(101, { head: sha('e'), reason: 'checks pass on a new head' });
  ok(plan(p1.doc, { records: [rebased, b, c], openPrs: [101, 102, 103], at: at(15) }).events.length === 0, 'tick: a rebase (new head, new reason) is the same item and writes nothing');
  const p3 = plan(p1.doc, { records: [a, b, rec(103)], openPrs: [101, 102, 103], at: at(15) });
  ok(JSON.stringify(types(p3)) === JSON.stringify(['observed:blocked>needed']), 'tick: blocked to needed is one observed event', JSON.stringify(types(p3)));
  const p4 = plan(p1.doc, { records: [rec(101, { class: 'immediate-cluster-change' }), b, c], openPrs: [101, 102, 103], at: at(15) });
  ok(JSON.stringify(types(p4)) === JSON.stringify(['observed:needed>needed']) && p4.events[0].class === 'immediate-cluster-change', 'tick: a new class in the same state is one observed event (the format: state, lane or class)');
  const held = rec(102, { lane: 'LANE-B-OWNER', class: 'hold', findings: ['LANE-B-OWNER/hold: .renovate/holds.json5'] });
  ok(JSON.stringify(types(plan(p1.doc, { records: [a, held, c], openPrs: [101, 102, 103], at: at(15) }))) === JSON.stringify(['observed:needed>held']), 'tick: a hold is needed to held');
  // a newer version on the same PR: the old item closes (actor renovate), the new one is observed
  const p5 = plan(p1.doc, { records: [rec(101, { to: '1.2.0' }), b, c], openPrs: [101, 102, 103], at: at(15) });
  ok(JSON.stringify(types(p5)) === JSON.stringify(['closed:needed>closed', 'observed:new>needed']) && p5.events[0].actor.kind === 'renovate' && p5.events[0].item_id === itemOf(a) && p5.terminals.length === 0,
    'tick: a newer version on the same PR closes the old item (Renovate) and observes the new one; the open PR gets no terminal', JSON.stringify(types(p5)));
  // the PR left the open list: merged by Renovate (from the merged list), or closed
  const p6 = plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), scan: scanOf([mergedPr(101, 10, { title: 'fix(container): update image ghcr.io/example/app101 to 1.1.0' })]) });
  ok(JSON.stringify(types(p6)) === JSON.stringify(['merged:needed>merged_unverified']) && p6.events[0].actor.kind === 'renovate' && p6.events[0].actor.login === 'renovate[bot]' && p6.events[0].recorded_by === 'lane' && p6.events[0].attempt === 1 && p6.events[0].evidence.merge_commit === sha('a'),
    'tick: a PR that left the open list and is in the merged list: merged by Renovate, from the state the lane saw, attempt 1', JSON.stringify(p6.events));
  ok(p6.terminals.length === 1 && p6.terminals[0].pr === 101 && p6.terminals[0].terminal.type === 'merged' && p6.terminals[0].terminal.state === 'merged_unverified', 'tick: the merge is a terminal event for PR 101', JSON.stringify(p6.terminals));
  ok(plan(p6.doc, { records: [b, c], openPrs: [102, 103], at: at(30), scan: scanOf([mergedPr(101, 10)]) }).events.length === 0, 'tick: the merge is recorded once (the next tick, same merged list)');
  for (const [who, kind] of [['app/haynes-ops-bot', 'shepherd'], ['app/haynes-dev-bot', 'hand'], ['thaynes43', 'person'], ['app/some-other-app', 'hand']]) {
    const p = plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), scan: scanOf([mergedPr(101, 10, { mergedBy: { login: who } })]) });
    ok(p.events[0]?.actor.kind === kind && RF.checkEvent(p.events[0]).length === 0, `tick: merged by ${who} is actor ${kind}`, JSON.stringify(p.events[0]?.actor));
  }
  const fate = st => new Map([[101, { number: 101, state: st, closedAt: at(5), closedBy: 'renovate[bot]', mergedAt: st === 'MERGED' ? at(5) : null, mergedBy: { login: 'app/renovate' }, mergeCommit: { oid: sha('c') } }]]);
  const p7 = plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), fates: fate('CLOSED') });
  ok(JSON.stringify(types(p7)) === JSON.stringify(['closed:needed>closed']) && p7.events[0].actor.kind === 'renovate' && /closed without a merge by renovate\[bot\]/.test(p7.events[0].reason) && p7.terminals[0]?.terminal.type === 'closed', 'tick: a PR closed without a merge: closed, by who closed it, a terminal event', JSON.stringify(p7.events));
  ok(JSON.stringify(types(plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), fates: fate('MERGED') }))) === JSON.stringify(['merged:needed>merged_unverified']), 'tick: the PR read by itself as merged (not in the merged list) is merged');
  const p8 = plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), fates: new Map([[101, null]]) });
  ok(p8.events.length === 0 && p8.notes.unread.some(x => x.pr === 101), 'tick: a PR that left the open list and cannot be read: no event, named as unread; the next tick reads it again');
  ok(plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), fates: new Map([[101, { number: 101, state: 'OPEN' }]]) }).notes.still_open.some(x => x.pr === 101), 'tick: open but not listed: no event, named');
  // a closed item is observed again when its PR reopens
  ok(JSON.stringify(types(plan(p7.doc, { records: [a, b, c], openPrs: [101, 102, 103], at: at(30) }))) === JSON.stringify(['observed:closed>needed']), 'tick: a reopened PR observes its closed item again');
  // facts the lane could not read name no item: nothing is written
  const fi = { schema: 'haynes.upgrade-lane/v1', pr: 104, lane: 'BLOCKED', class: 'facts-incomplete', reason: 'read failed', update_type: 'unknown', rules: [], checks: {}, targets: [] };
  const p9 = plan(p1.doc, { records: [a, b, c, fi], openPrs: [101, 102, 103, 104], at: at(15) });
  ok(p9.events.length === 0 && p9.notes.unread.some(x => x.pr === 104), 'tick: a record whose facts could not be read writes nothing (the item cannot be named)');
  // two open PRs carrying the same item: the lowest PR speaks for it, no flip-flop
  const twin = rec(105, { package: a.package, packages: a.packages, lane: 'LANE-B-AGENT', class: 'rule-agent' });
  const p10 = plan(p1.doc, { records: [twin, a, b, c], openPrs: [101, 102, 103, 105], at: at(15) });
  const p11 = plan(p10.doc, { records: [a, twin, b, c], openPrs: [101, 102, 103, 105], at: at(30) });
  ok(p10.events.length === 0 && p11.events.length === 0 && p10.notes.shared_item.some(x => x.pr === 105 && x.with === 101), 'tick: two open PRs that carry the same item write one record and do not flip-flop between their verdicts', JSON.stringify(p10.notes.shared_item));
  // an item past an attempt (here merged) is not observed again on a new carrier
  const again = plan(p6.doc, { records: [rec(106, { package: a.package, packages: a.packages })], openPrs: [106], at: at(30) });
  ok(again.events.length === 0 && again.notes.not_observed.some(x => x.pr === 106 && x.state === 'merged_unverified'), 'tick: a new PR for an item already merged writes no observed event (the machine has no such edge)');
}

// ── 3. live merge-step events through the format ───────────────────────────────
{
  const a = rec(201);
  const base = plan(withMarker(H.emptyDoc()), { records: [a], openPrs: [201] });
  const v = (verdict, extra = {}) => ({ pr: 201, head: a.head, class: 'cluster-change', chosen: true, mode: 'live', verdict, pr_refusals: [], lane_refusals: [], merge_call: 'gh pr merge 201 (exit 0)', ...extra });
  const pm = plan(base.doc, { records: [a], openPrs: [201], at: at(15), verdict: v('merged', { merged: { merge_commit: sha('d'), merged_at: at(15) } }) });
  ok(JSON.stringify(types(pm)) === JSON.stringify(['merge-attempted:needed>in_progress', 'merged:in_progress>merged_unverified']) && pm.terminals[0]?.terminal.type === 'merged', 'live: a merge verdict writes merge-attempted and merged through eventsFromMergeStep, and is terminal for the PR', JSON.stringify(types(pm)));
  ok(plan(base.doc, { records: [a], openPrs: [201], at: at(15), verdict: { ...v('refused'), mode: 'dryrun' } }).events.length === 0, 'live: a dry-run verdict writes nothing');
  ok(plan(base.doc, { records: [a], openPrs: [201], at: at(15), verdict: v('refused', { lane_refusals: ['class not allowlisted'] }) }).events.length === 0, 'live: a lane refusal writes nothing');
  const r1 = plan(base.doc, { records: [a], openPrs: [201], at: at(15), verdict: v('refused', { pr_refusals: ['head behind main'] }) });
  const r2 = plan(r1.doc, { records: [a], openPrs: [201], at: at(30), verdict: v('refused', { pr_refusals: ['head behind main'] }) });
  const r3 = plan(r2.doc, { records: [a], openPrs: [201], at: at(45), verdict: v('refused', { pr_refusals: ['a check is running'] }) });
  ok(r1.events.length === 2 && r2.events.length === 0 && r2.notes.collapsed.length === 1 && r3.events.length === 2 && r3.events[0].attempt === 2, 'live: a refusal repeated at the same head for the same reasons is recorded once; a new reason opens the next attempt', `${r1.events.length} ${r2.events.length} ${r3.events.length}`);
}

// ── 4. the backfill ─────────────────────────────────────────────────────────────
{
  // the format's own backfill fixture, PR 4817, derived exactly
  const fx = fixture.record, ev0 = fx.events[0];
  const e4817 = { number: 4817, title: fx.item.carrier.title, author: { login: 'app/renovate' }, mergedAt: '2026-11-11T08:49:05Z', mergedBy: { login: 'app/renovate' }, labels: [{ name: 'renovate/container' }, { name: 'type/patch' }], mergeCommit: { oid: ev0.evidence.merge_commit }, headRefOid: fx.item.carrier.head };
  const b = H.backfillItem(e4817, REPO);
  ok(b.id === fx.item.id && b.desc.name === fx.item.item.name && b.desc.to === fx.item.available.version, 'backfill: PR 4817 names the fixture\'s item (same id, name and version)', JSON.stringify(b));
  const pf = plan(H.emptyDoc(), { at: ev0.recorded_at, backfilling: true, scan: scanOf([e4817], '2026-10-12T15:00:00.000Z', ev0.recorded_at) });
  const e = pf.events[0];
  ok(pf.events.length === 1 && e.id === ev0.id && e.type === 'merged' && e.from === null && e.to === 'merged_unverified' && e.recorded_by === 'backfill' && e.recorded_at === ev0.recorded_at && e.actor.login === 'renovate[bot]' && e.evidence.merge_commit === ev0.evidence.merge_commit,
    'backfill: PR 4817 derives the fixture\'s event (same id, type, states, actor, merge commit)', JSON.stringify(e));
  ok(RF.replay({ ...fx.item, state: null }, [e]).errors.length === 0, 'backfill: the event replays onto the fixture\'s item and leaves a valid item (record-format replay)');
  ok(pf.doc.meta.backfill?.compare?.match === true && pf.doc.meta.backfill.compare.renovate === 1 && pf.doc.meta.backfill.compare.recorded === 1, 'backfill: the marker is set with its count comparison');
  // 30 days, two authors, a shared version, a revert
  const list = [mergedPr(301, -3000), mergedPr(302, -2000), mergedPr(303, -1000, { title: 'feat(deps): update dev-env image toolchain group' }),
    { ...mergedPr(304, -900), title: 'fix(container): update image ghcr.io/example/app301 to v2.301.0' }, // the same version through another PR
    { number: 310, title: 'feat(opwork): something', author: { login: 'app/haynes-dev-bot' }, mergedAt: at(-800), mergedBy: { login: 'app/haynes-dev-bot' }, labels: [], mergeCommit: { oid: sha('9') } },
    { number: 311, title: 'Revert "fix(container): update image ghcr.io/example/app302 to v2.302.0"', author: { login: 'thaynes43' }, mergedAt: at(-500), mergedBy: { login: 'thaynes43' }, labels: [], mergeCommit: { oid: sha('8') } }];
  const p = plan(H.emptyDoc(), { backfilling: true, scan: scanOf(list), reverts: new Map([[311, 302]]) });
  const c = p.compare;
  ok(p.ok && JSON.stringify(types(p)) === JSON.stringify(['merged:new>merged_unverified', 'merged:new>merged_unverified', 'merged:new>merged_unverified', 'revert-merged:merged_unverified>reverted_unverified']), 'backfill: three Renovate merges and one revert by a person', JSON.stringify(types(p)));
  ok(c.gh_merged === 6 && c.renovate === 4 && c.recorded === 3 && c.shared_item.length === 1 && c.shared_item[0].pr === 304 && c.missing.length === 0 && c.match && c.reverts.titled === 1 && c.reverts.recorded === 1 && c.other_authors === 2,
    'backfill: the comparison: 6 merged, 4 Renovate, 3 recorded, 1 through another PR\'s item, none missing, 1 revert recorded', JSON.stringify(c));
  const rv = p.events.find(x => x.type === 'revert-merged');
  ok(rv.actor.kind === 'person' && rv.evidence.revert_pr === 311 && rv.carrier.pr === 302 && rv.attempt === 1, 'backfill: the revert is recorded on the item of the PR its body names');
  ok(p.doc.items[H.backfillItem(list[2], REPO).id]?.name === `${REPO}#303`, 'backfill: a group title with no version is named <repo>#<pr>, as fromLaneRecord names a PR with no package table');
  // idempotent: run again with the marker removed, or as a catch-up over the same window
  const again = plan({ ...p.doc, meta: { ...p.doc.meta, backfill: null } }, { backfilling: true, scan: scanOf(list), reverts: new Map([[311, 302]]), at: at(5) });
  ok(again.events.length === 0 && again.compare.match, 'backfill: run again over the same window, it writes nothing (idempotent)', JSON.stringify(types(again)));
  ok(plan(p.doc, { scan: scanOf(list), reverts: new Map([[311, 302]]), at: at(5) }).events.length === 0, 'backfill: a catch-up over the same window writes nothing');
  // the comparison names a PR the history does not hold
  const noCommit = plan(H.emptyDoc(), { backfilling: true, scan: scanOf([mergedPr(401, -100), mergedPr(402, -50, { mergeCommit: null })]) });
  ok(noCommit.compare.match === false && JSON.stringify(noCommit.compare.missing) === '[402]' && noCommit.notes.unrecorded.some(x => x.pr === 402), 'backfill: a merged Renovate PR the history could not record is named missing, and the comparison fails', JSON.stringify(noCommit.compare));
  const dropped = { ...p.doc, events: p.doc.events.filter(x => x.carrier?.pr !== 302 && x.type !== 'revert-merged') };
  ok(H.compareCounts({ scan: scanOf(list), st: H.replay(dropped), events: dropped.events, renovateAuthors: AUTHORS }).missing.includes(302), 'backfill: a merge missing from the history is named by the comparison');
  // a revert made by the shepherd is not a revert the format lets anyone but a hand run or a person record
  const bot = plan(H.emptyDoc(), { backfilling: true, scan: scanOf([mergedPr(301, -3000), { ...list[5], number: 312, mergedBy: { login: 'app/haynes-ops-bot' }, title: 'Revert "fix(container): update image ghcr.io/example/app301 to v2.301.0"' }]), reverts: new Map([[312, 301]]) });
  ok(bot.events.length === 1 && bot.notes.unrecorded.some(x => x.type === 'revert-merged'), 'backfill: a revert merged by the shepherd is not recorded, and is named');
  ok(plan(H.emptyDoc(), { backfilling: true, scan: scanOf([list[5]]), reverts: new Map([[311, 999]]) }).notes.revert_unmatched.length === 1, 'backfill: a revert of a PR the history does not hold is named, not guessed');
  // the catch-up window: the newest merge recorded, less the margin; never before the backfill
  const since = H.catchUpSince(p.doc, cfg);
  ok(Date.parse(since) === Date.parse(at(-900)) - cfg.catch_up_margin_hours * 3600e3 || Date.parse(since) === Date.parse(p.doc.meta.backfill.window.until) - cfg.catch_up_margin_hours * 3600e3, 'backfill: the catch-up starts at the newest recorded merge (or the backfill\'s end) less the margin', since);
  // the merged list read: refused when it may be cut off by the search limit, or malformed
  const fakeRun = out => () => JSON.stringify(out);
  ok(H.readMerged(REPO, at(-100), at(0), cfg, fakeRun(Array.from({ length: cfg.max_search_results }, (_, i) => mergedPr(i + 1, -1)))).ok === false, 'backfill: a merged list that reached the search limit is refused as possibly incomplete');
  ok(H.readMerged(REPO, at(-100), at(0), cfg, fakeRun([{ number: 1 }])).ok === false, 'backfill: a merged list without merge times is refused');
  ok(H.readMerged(REPO, at(-100), at(0), cfg, fakeRun([mergedPr(1, -10), mergedPr(2, 10)])).list.length === 1, 'backfill: merges after the window\'s end are left for the next tick');
}

// ── 5. rotation: 5,000 events ────────────────────────────────────────────────────
{
  // an item that was merged, reverted and recovered: reverted, the sticky state
  const s = rec(500);
  let doc = plan(withMarker(H.emptyDoc()), { records: [s], openPrs: [500], at: at(-60 * 1440) }).doc;
  const sid = itemOf(s);
  const ev = (type, from, to, min, extra) => H.makeEvent({ item_id: sid, type, from, to, at: at(min), actor: { kind: 'lane', login: null }, recorded_by: 'lane', attempt: 1, ...extra });
  const chain = [ev('merge-attempted', 'needed', 'in_progress', -59 * 1440, { lane: 'A-night', carrier: { repo: REPO, pr: 500 } }), ev('merged', 'in_progress', 'merged_unverified', -59 * 1440 + 1, { carrier: { repo: REPO, pr: 500 }, evidence: { merge_commit: sha('5') } }),
    ev('verification-failed', 'merged_unverified', 'failed', -59 * 1440 + 30, { class: 'not-ready', reason: 'HelmRelease not Ready' }), ev('revert-opened', 'failed', 'reverting', -59 * 1440 + 31, { evidence: { revert_pr: 9001 } }),
    ev('revert-merged', 'reverting', 'reverted_unverified', -59 * 1440 + 40, { evidence: { revert_commit: sha('6') } }), ev('recovery-verified', 'reverted_unverified', 'reverted', -59 * 1440 + 60, {})];
  doc = { ...doc, events: [...doc.events, ...chain] };
  ok(stateOf(doc, sid) === 'reverted', 'rotation: the sticky item is reverted before rotation');
  // an open item first seen long ago, and a closed one
  const o = rec(501), cl = rec(502);
  const po = plan(doc, { records: [o, cl], openPrs: [501, 502], at: at(-58 * 1440) });
  const pc = plan(po.doc, { records: [o], openPrs: [501], at: at(-58 * 1440 + 15), fates: new Map([[502, { number: 502, state: 'CLOSED', closedAt: at(-58 * 1440 + 10) }]]) });
  doc = pc.doc;
  // then 5,000 events: merges by Renovate of distinct items, one every 15 minutes
  const list = Array.from({ length: 5000 }, (_, i) => mergedPr(10000 + i, -57 * 1440 + i * 15, { title: `fix(container): update image ghcr.io/example/bulk${i} to v1.${i}.0` }));
  const big = H.planHistory({ doc, records: [o], openPrs: [501], verdict: null, at: at(0), repo: REPO, cfg, scan: scanOf(list, at(-57 * 1440), at(0)), backfilling: false, renovateAuthors: AUTHORS });
  const m = H.historyManifest(big.doc, '7');
  ok(big.ok && big.events.length === 5000, 'rotation: 5,000 new events derived', big.why || big.events.length);
  ok(H.sizeOf(m) <= cfg.max_bytes && Buffer.byteLength(JSON.stringify(m)) < 1024 * 1024, `rotation: the history stays under its ${cfg.max_bytes}-byte cap (${H.sizeOf(m)} bytes of data, ${Buffer.byteLength(JSON.stringify(m))} bytes as a request)`);
  ok(H.sizeOf(m) > cfg.max_bytes * 0.9, `rotation: and does not drop much more than it must (${H.sizeOf(m)} bytes kept of ${cfg.max_bytes})`);
  ok(big.rotation.dropped > 0 && big.doc.events.length < 5000 + doc.events.length && big.doc.meta.rotation?.dropped === big.rotation.dropped, 'rotation: the oldest events rotated out, and the meta says how many', JSON.stringify(big.doc.meta.rotation));
  const after = H.replay(big.doc);
  ok(after.errors.length === 0, 'rotation: what is left still replays, from the bases', after.errors[0]);
  ok(!big.doc.events.some(e => e.item_id === sid) && big.doc.items[sid]?.base?.state === 'reverted' && after.items.get(sid).item.state === 'reverted' && after.items.get(sid).item.attempts.length === 1,
    'rotation: the reverted item keeps its state after every event of it rotated out (items[id].base)', JSON.stringify(big.doc.items[sid]));
  ok(after.items.get(itemOf(o))?.item.state === 'needed' && big.doc.items[itemOf(o)]?.base, 'rotation: an open item whose events rotated out keeps its place');
  ok(!big.doc.items[itemOf(cl)] && !after.items.has(itemOf(cl)), 'rotation: a closed item with no event left is forgotten');
  // the memory holds: a new PR for the reverted version observes nothing, and no merge starts
  const back = plan(big.doc, { records: [rec(503, { package: s.package, packages: s.packages })], openPrs: [503, 501], at: at(15) });
  ok(back.events.length === 0 && back.notes.not_observed.some(x => x.pr === 503 && x.state === 'reverted'), 'rotation: a new PR for the reverted version is not observed as needed: the version stays remembered', JSON.stringify(back.notes.not_observed));
  const mergeAgain = H.makeEvent({ item_id: sid, type: 'merge-attempted', from: 'needed', to: 'in_progress', at: at(15), actor: { kind: 'lane', login: null }, recorded_by: 'lane', attempt: 2, lane: 'A-night', carrier: { repo: REPO, pr: 503 } });
  ok(RF.applyEvent(after.items.get(sid).item, mergeAgain).errors.length > 0, 'rotation: and the format refuses a merge attempt on it');
  ok(H.parseDoc({ metadata: { resourceVersion: '7' }, data: m.data }).ok, 'rotation: the rotated object parses back (every line passes checkEvent)');
  const perEvent = Math.round(Buffer.byteLength(m.data.events) / big.doc.events.length);
  console.log(`rotation: 5,000 merges plus ${doc.events.length} earlier events; ${big.rotation.dropped} rotated out, ${big.doc.events.length} kept, ${Object.keys(big.doc.items).length} items; ${H.sizeOf(m)} of ${cfg.max_bytes} bytes; ${perEvent} bytes per stored event line`);
}

// ── 6. the write ──────────────────────────────────────────────────────────────
{
  // a fake API server for upgrade-ledger: create-on-apply, and a write at a resourceVersion
  const api = () => {
    let obj = null, rv = 100;
    const apply = (m, mgr) => {
      if (mgr === H.INIT_MANAGER) { if (!obj) obj = { metadata: { name: m.metadata.name, resourceVersion: String(++rv) }, data: {} }; return { ok: true, rv: obj.metadata.resourceVersion, conflict: false }; }
      if (!obj || m.metadata.resourceVersion !== obj.metadata.resourceVersion) return { ok: false, conflict: true, detail: 'conflict (HTTP 409)', raw: 'Error from server (Conflict)' };
      obj = { metadata: { name: m.metadata.name, resourceVersion: String(++rv) }, data: structuredClone(m.data) };
      return { ok: true, rv: obj.metadata.resourceVersion, conflict: false };
    };
    const run = (cmd, argv) => {
      if (cmd === 'kubectl' && argv[0] === 'get') return JSON.stringify({ items: obj ? [obj] : [] });
      if (cmd === 'gh' && argv[1] === 'list') return '[]';
      return null;
    };
    return { apply, run, get: () => obj };
  };
  const phase = (fake, recs, extra = {}) => H.historyPhase({ rules, run: fake.run, env: {}, repo: REPO, at: at(extra.min || 0), records: recs, openPrs: recs.map(r => r.pr), verdict: null, write: true, apply: fake.apply, ...extra });
  // two writers overlap: both read the same version; the second's write conflicts and writes
  // nothing; its next tick derives its events again and writes them: no event is lost
  const f = api();
  phase(f, [rec(601)]);
  const v1 = f.get().metadata.resourceVersion;
  const snap = structuredClone(f.get());
  const runAt = (o) => (cmd, argv) => (cmd === 'kubectl' && argv[0] === 'get' ? JSON.stringify({ items: [o] }) : f.run(cmd, argv));
  const A = H.historyPhase({ rules, run: runAt(snap), env: {}, repo: REPO, at: at(15), records: [rec(601), rec(602)], openPrs: [601, 602], verdict: null, write: true, apply: f.apply });
  const B = H.historyPhase({ rules, run: runAt(snap), env: {}, repo: REPO, at: at(16), records: [rec(601), rec(603)], openPrs: [601, 603], verdict: null, write: true, apply: f.apply });
  ok(A.summary.written && !B.summary.written && B.summary.write === 'conflict' && B.summary.events === 0 && /another writer changed upgrade-ledger/.test(B.summary.why_not), 'write: two writers read the same version; the second conflicts and writes nothing', `${v1} ${JSON.stringify(B.summary).slice(0, 200)}`);
  const B2 = phase(f, [rec(601), rec(602), rec(603)], { min: 30 });
  const ev = f.get().data.events.trim().split('\n').map(JSON.parse);
  ok(B2.summary.written && [601, 602, 603].every(pr => ev.some(e => e.type === 'observed' && e.carrier.pr === pr)) && ev.length === 3, 'write: its next tick derives the lost events again: every first sighting is in the history, once', JSON.stringify(ev.map(e => e.carrier.pr)));
  // a retried write of the same events: a duplicate, not a second event
  const doc = H.parseDoc(f.get()).doc;
  const dup = { ...doc, events: [...doc.events, doc.events[0]] };
  ok(H.replay(dup).dups === 1 && H.replay(dup).errors.length === 0 && plan(dup, { records: [rec(601), rec(602), rec(603)], openPrs: [601, 602, 603], at: at(45) }).events.length === 0, 'write: a duplicate line (a retried write) is dropped by id on replay');
  // a failed write: reported, nothing written, and the next tick writes the same events
  const g = api();
  const fail = H.historyPhase({ rules, run: g.run, env: {}, repo: REPO, at: at(0), records: [rec(701)], openPrs: [701], verdict: null, write: true, apply: (m, mgr) => (mgr === H.INIT_MANAGER ? g.apply(m, mgr) : { ok: false, conflict: false, detail: 'refused by the API server (Forbidden)', raw: 'Error from server (Forbidden): secret' }) });
  ok(!fail.summary.written && fail.summary.write === 'failed' && fail.summary.derived === 1 && fail.summary.events === 0 && /refused by the API server \(Forbidden\)/.test(fail.summary.why_not) && !/secret/.test(JSON.stringify(fail.summary.why_not)), 'write: a failed write is reported in the fixed vocabulary, nothing written', JSON.stringify(fail.summary).slice(0, 300));
  ok(/^FAILED: 1 event derived \(observed 1\), none written; the write failed/.test(H.historyLine(fail.summary)), 'write: the log line says so', H.historyLine(fail.summary));
  const retry = phase(g, [rec(701)], { min: 15 });
  ok(retry.summary.written && retry.summary.events === 1, 'write: the next tick writes the event the failed one derived');
  // a history this tick cannot read is never overwritten
  const corrupt = { metadata: { name: 'upgrade-ledger', resourceVersion: '9' }, data: { meta: JSON.stringify({ schema: H.HISTORY_SCHEMA }), items: '{}', events: '{"item_id":"x","type":"observed"}\n' } };
  let wrote = 0;
  const cr = H.historyPhase({ rules, run: (cmd, argv) => (cmd === 'kubectl' ? JSON.stringify({ items: [corrupt] }) : '[]'), env: {}, repo: REPO, at: at(0), records: [rec(801)], openPrs: [801], verdict: null, write: true, apply: () => { wrote++; return { ok: true, rv: '10' }; } });
  ok(wrote === 0 && !cr.summary.written && /unreadable: ConfigMap upgrade-agent\/upgrade-ledger: data.events line 1/.test(cr.summary.why_not), 'write: a corrupt history is refused and never overwritten', JSON.stringify(cr.summary.why_not));
  ok(/unreadable/.test(H.historyPhase({ rules, run: () => null, env: {}, repo: REPO, at: at(0), records: [], openPrs: [], verdict: null, write: true, apply: () => { wrote++; return { ok: true }; } }).summary.why_not) && wrote === 0, 'write: an unreadable ConfigMap list writes nothing');
  // a local run plans and writes nothing
  const lr = H.historyPhase({ rules, run: api().run, env: {}, repo: REPO, at: at(0), records: [rec(901)], openPrs: [901], verdict: null, write: false, apply: () => { wrote++; return { ok: true }; } });
  ok(wrote === 0 && lr.summary.write === 'planned' && lr.summary.derived === 1, 'write: a local run (no --write-state) plans and writes nothing');
}

// ── 7. terminal events on the PR record ──────────────────────────────────────────
{
  const rcd = M.recordConfig(rules);
  ok(rcd.ok && rcd.fields.includes('terminal'), 'record: terminal is one of the published fields');
  const pub = M.projectRecord({ schema: 'haynes.upgrade-lane/v1', pr: 4900, head: sha('1'), lane: 'LANE-A', class: 'cluster-change', terminal: null }, rcd);
  const { terminal, ...old } = pub;
  ok(M.recordContentKey(pub) === M.recordContentKey(old), 'record: a null terminal compares equal to a record written before the field existed (no edit storm)');
  const term = { type: 'merged', at: at(0), state: 'merged_unverified', by: 'renovate[bot]', item: 'renovate-pr:' + '1'.repeat(24), event: 'ev:' + '2'.repeat(24), merge_commit: sha('3') };
  ok(M.recordContentKey({ ...pub, terminal: term }) !== M.recordContentKey(pub), 'record: a terminal event changes the content (one edit)');
  const body = M.renderRecord({ ...pub, terminal: term }, rcd);
  ok(M.publishProblems({ ...pub, terminal: term }, body, rcd).length === 0 && M.parseRecord(body, rcd).terminal.merge_commit === sha('3'), 'record: the terminal record passes the publish check and parses back');
  const own = [{ id: 77, user: { login: rcd.author.login, type: rcd.author.type }, body: M.renderRecord(pub, rcd) }];
  const runC = list => (cmd, argv) => (cmd === 'gh' && argv.some(a => /issues\/\d+\/comments/.test(a)) ? JSON.stringify([list]) : null);
  const t1 = H.terminalRecordItems([{ pr: 4900, terminal: term }], [], rcd, at(0), runC(own), REPO);
  const w1 = M.writePrRecords({ items: t1.items, cfg: rcd, run: () => null, env: {}, live: false });
  ok(t1.items.length === 1 && t1.items[0].rec.terminal === term && t1.items[0].edit_only && w1.would_update.includes(4900), 'record: a PR that left the open list with its record: one edit planned, through the existing writer');
  const t2 = H.terminalRecordItems([{ pr: 4901, terminal: term }], [], rcd, at(0), runC([]), REPO);
  const w2 = M.writePrRecords({ items: t2.items, cfg: rcd, run: () => null, env: {}, live: true, write: () => { throw new Error('no write expected'); } });
  ok(w2.no_record.includes(4901) && w2.github_writes === 0 && !w2.created.length, 'record: a PR with no lane record gets no new comment (edit only)');
  const already = [{ id: 78, user: { login: rcd.author.login, type: rcd.author.type }, body: M.renderRecord({ ...pub, pr: 4902, terminal: term }, rcd) }];
  const t3 = H.terminalRecordItems([{ pr: 4902, terminal: term }], [], rcd, at(5), runC(already), REPO);
  ok(M.writePrRecords({ items: t3.items, cfg: rcd, run: () => null, env: {}, live: false }).current.includes(4902), 'record: a record that already carries the terminal event is current (no edit)');
  ok(H.terminalRecordItems([{ pr: 4903, terminal: term }], [4903], rcd, at(0), runC(own), REPO).items.length === 0, 'record: an open PR is not a terminal item (its record comes from the tick)');
}

// ── 8. size ─────────────────────────────────────────────────────────────────────
{
  const p = plan(withMarker(H.emptyDoc()), { records: [rec(1001, { reason: 'x'.repeat(400) })], openPrs: [1001], scan: scanOf([mergedPr(1002, -10)]) });
  ok(p.events.every(e => !e.reason || e.reason.length <= cfg.max_reason_chars), 'size: event reasons are cut to max_reason_chars');
  const lines = H.renderData(p.doc).events.trim().split('\n');
  ok(lines.every(l => !/"schema"|"id"|"login":null/.test(l)), 'size: a stored line leaves out schema, id and a null login');
  ok(lines.map(l => H.unpack(JSON.parse(l))).every((e, i) => e.id === p.events[i].id && RF.checkEvent(e).length === 0), 'size: unpack restores the format\'s event, id included');
  console.log(`size: one observed line ${Buffer.byteLength(lines[0])} bytes (a ${cfg.max_reason_chars}-character reason), one merged line ${Buffer.byteLength(lines[1])} bytes`);
}

console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
