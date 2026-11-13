#!/usr/bin/env node
// Self-test for the history of every attempt (story 593): upgrade-history.mjs and
// ConfigMaps upgrade-agent/upgrade-ledger and upgrade-ledger-marker.
//
// WHY THIS EXISTS
// ---------------
// The history is what piece 1 remembers: every attempt, and that a reverted version is
// never merged again. Every item must be keyed as the lane keys it, it must never lose an
// event to an overlapping writer, never record what it did not read, never forget a sticky
// state when it rotates, and never start over silently. This test pins, against fakes (no
// cluster, no network):
//   1. statically: one spawn site, kubectl apply --server-side of the two ConfigMaps only,
//      behind its refusals; every read through the read-only runner;
//   2. what a tick writes: first sight, a new state, lane or class (once), a close, a newer
//      version on the same PR, a merge by someone else on the item that was merged (H1),
//      nothing for an unchanged or unreadable item or an unread closer (M3);
//   3. live merge-step events and a repeated refusal collapsed;
//   4. every item by the lane's route (H2), the backfill, idempotence, reverts (L6), the
//      count comparison against GitHub's own count (M2), the split list (M1), the budget (L4);
//   5. rotation: 5,000 events under the cap, reverted kept, a lane merge kept (M4);
//   6. the writes: conflicts, failures, the marker and a lost history (M5), stuck states (L1);
//   7. terminal records: owed until mirrored (L2), never a new comment;
//   8. the readers, directly; 9. the size numbers.
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
const queue = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/lane-queue-20261109.json'), 'utf8'));
const holdRules = L.parseJson5(readFileSync(join(ROOT, '.renovate/holds.json5'), 'utf8')).packageRules;

let fails = 0, passes = 0;
const ok = (cond, name, detail = '') => { if (cond) passes++; else { fails++; console.log(`FAIL ${name}${detail ? `\n     ${detail}` : ''}`); } };
const T0 = Date.parse('2026-11-13T06:00:00.000Z');
const at = (min = 0) => new Date(T0 + min * 60e3).toISOString();
const sha = c => c.repeat(40);
// Renovate's package table, as gatherFacts reads it (parsePackageTable)
const table = pkgs => '| Package | Update | Change |\n|---|---|---|\n' + pkgs.map(p => `| ${p.name} | ${p.update || ''} | \`${p.from}\` → \`${p.to}\` |`).join('\n') + '\n';
// a lane record as run-night.mjs writes it (haynes.upgrade-lane/v1), small
// the place: app<n> under kubernetes/main/apps/x (o.app moves it); the record's target and the
// merged PR's files agree, as the classifier makes them agree
const placeFiles = n => [`kubernetes/main/apps/x/app${n}/app/helmrelease.yaml`];
const rec = (pr, o = {}) => ({ schema: 'haynes.upgrade-lane/v1', pr, head: sha((pr % 10).toString(16)), fingerprint: null, package: o.pkg ?? `ghcr.io/example/app${pr}`, from: '1.0.0', to: o.to ?? '1.1.0', packages: [{ name: o.pkg ?? `ghcr.io/example/app${pr}`, update: 'minor', from: '1.0.0', to: o.to ?? '1.1.0' }],
  update_type: 'minor', lane: 'LANE-A', class: 'cluster-change', reason: `checks pass on #${pr}`, findings: [], rules: [], checks: {}, targets: [`kubernetes/main/apps/x/app${o.app ?? pr}`], files: placeFiles(o.app ?? pr), own_images: [], ...o });
// a merged PR as gh lists it, with the facts the lane's route names its item by
const mergedPr = (n, min, o = {}) => { const to = o.to ?? '1.1.0', name = o.pkg ?? `ghcr.io/example/app${n}`; const { to: _t, pkg: _p, app: _a, ...rest } = o;
  return { number: n, title: `fix(container): update image ${name} to v${to}`, author: { login: 'app/renovate' }, mergedAt: at(min), mergedBy: { login: 'app/renovate' }, labels: [{ name: 'renovate/container' }, { name: 'type/minor' }], mergeCommit: { oid: sha('a') }, headRefOid: sha('b'), body: table([{ name, update: 'minor', from: '1.0.0', to }]), files: placeFiles(o.app ?? n).map(path => ({ path })), ...rest }; };
const scanOf = (list, since = at(-30 * 1440), until = at(0), extra = {}) => ({ ok: true, list: list.filter(e => !H.isRevertTitle(e.title)), reverts: list.filter(e => H.isRevertTitle(e.title)), github_count: list.filter(e => AUTHORS.includes(e.author?.login) && !H.isRevertTitle(e.title)).length, window: { since, until }, query: 'gh pr list (test)', repo: REPO, ...extra });
const plan = (doc, o = {}) => H.planHistory({ doc, records: [], openPrs: [], verdict: null, at: at(0), repo: REPO, cfg, rules, scan: scanOf([]), backfilling: false, renovateAuthors: AUTHORS, ...o });
const withMarker = doc => ({ ...doc, meta: { ...doc.meta, backfill: { at: at(-1440), window: { since: at(-31 * 1440), until: at(-1440) }, query: 'q', events: 0, compare: null } } });
const types = p => p.events.map(e => `${e.type}:${e.from ?? 'new'}>${e.to}`);
const stateOf = (doc, id) => H.replay(doc).items.get(id)?.item.state ?? null;
const itemOf = r => RF.fromLaneRecord(r, { repo: REPO, observed_at: at(0) }).id;
const closedFate = (pr, o = {}) => ({ ...mergedPr(pr, 5), state: 'CLOSED', mergedAt: null, mergedBy: null, mergeCommit: null, closedAt: at(5), closedBy: 'renovate[bot]', closed_by_read: true, ...o });
const mergedFate = (pr, o = {}) => ({ ...mergedPr(pr, 5, o), state: 'MERGED' });

ok(cfg.ok, 'config: night.history in upgrade-rules.json is valid', cfg.why);
ok(cfg.max_bytes <= 786432 && cfg.max_bytes < 1024 * 1024, 'config: the cap is at most 768 KiB of data, under the API server\'s 1 MiB');
ok(rules.night.pr_record.fields.includes('terminal'), 'config: the PR record carries terminal (story 593 (d))');
ok(cfg.max_pr_reads_per_tick > 0 && cfg.time_budget_seconds < 600 && cfg.max_mirrors_per_tick > 0, 'config: the per-PR reads, the mirrors and the time are bounded per tick, under the Job\'s 600s deadline (L4)');

// ── 1. static ───────────────────────────────────────────────────────────────
{
  const strip = s => s.replace(/^\s*\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '');
  const src = strip(readFileSync(join(RES, 'upgrade-history.mjs'), 'utf8'));
  ok((src.match(/\bspawnSync\s*\(/g) || []).length === 1, 'static: upgrade-history.mjs has exactly one spawnSync call site');
  ok(!/(?<![.\w])(execSync|execFileSync|execFile|exec|spawn|fork)\s*\(/.test(src.replace(/spawnSync\s*\(/g, '')) && /import \{ spawnSync \} from 'node:child_process'/.test(src), 'static: no other process API');
  const fn = src.slice(src.indexOf('export function applyHistory('), src.indexOf('\n}\n', src.indexOf('export function applyHistory(')));
  ok(fn.indexOf('throw new Error') < fn.indexOf('spawnSync(') && /spawnSync\('kubectl', \['apply', '--server-side', `--field-manager=\$\{manager\}`, '--force-conflicts', '-o', 'json', '-f', '-'\]/.test(fn), 'static: the spawn is a server-side apply, after its refusals');
  ok(/const HISTORY = Object\.freeze\(\{ name: 'upgrade-ledger', namespace: 'upgrade-agent' \}\)/.test(src) && /const MARKER = Object\.freeze\(\{ name: 'upgrade-ledger-marker', namespace: 'upgrade-agent' \}\)/.test(src), 'static: the two object names are constants');
  ok(!/['"](gh|git|flux|curl|helm)['"]\s*,\s*\[[^\]]*['"](merge|comment|edit|close|POST|PATCH|PUT|DELETE)['"]/.test(src) && !/ghWrite|callMerge|writePrRecords/.test(src) && /tryRun\(run, 'gh'/.test(src), 'static: GitHub is only read, through the runner; nothing here writes to GitHub or merges');
  ok((src.match(/\bfetch\s*\(/g) || []).length === 0, 'static: no fetch');
  const night = strip(readFileSync(join(RES, 'run-night.mjs'), 'utf8'));
  ok(!/import \* as H from/.test(night) && /try \{\s*H = await import\('\.\/upgrade-history\.mjs'\);\s*hist = H\.historyPhase\(/.test(night), 'static (L5): run-night.mjs loads the history module inside the guarded step, so a module that does not load stops only the history');
  ok(night.indexOf('H.historyPhase(') > night.indexOf('M.laneMergeStep(') && night.indexOf('H.historyPhase(') < night.indexOf('M.writePrRecords(') && night.lastIndexOf('M.writePrRecords(') > night.indexOf('summary.pr_records = '), 'static (L3): the history runs after the merge step; the terminal mirrors are written after the open-PR records');
  const bad = [[{ ...H.historyManifest(H.emptyDoc(), '5'), metadata: { name: 'upgrade-lane-state', namespace: 'upgrade-agent', resourceVersion: '5' } }, H.MANAGER, 'another ConfigMap'],
    [H.historyManifest(H.emptyDoc(), null), H.MANAGER, 'data without a resourceVersion'], [H.historyManifest(H.emptyDoc(), '5'), 'upgrade-night', 'the tick\'s field manager'],
    [{ ...H.initManifest(), data: { events: '' } }, H.INIT_MANAGER, 'data under the init manager'], [{ ...H.historyManifest(H.emptyDoc(), '5'), data: { events: '' } }, H.MANAGER, 'a partial data write'],
    [H.markerManifest({ schema: H.MARKER_SCHEMA, created_at: at(0) }), H.MANAGER, 'the marker under the history manager'], [{ ...H.markerManifest({}), metadata: { ...H.MARKER, resourceVersion: '1' } }, H.MARKER_MANAGER, 'a marker at a version'],
    [{ ...H.historyManifest(H.emptyDoc(), '5'), metadata: { ...H.MARKER, resourceVersion: '5' } }, H.MANAGER, 'history data on the marker']];
  for (const [m, mgr, what] of bad) { let threw = false; try { H.applyHistory(m, mgr, {}); } catch { threw = true; } ok(threw, `static: applyHistory refuses ${what} before any spawn`); }
}

// ── 2. what a tick writes ───────────────────────────────────────────────────────
const a = rec(101), b = rec(102, { lane: 'LANE-B-AGENT', class: 'rule-agent' }), c = rec(103, { lane: 'BLOCKED', class: 'check-failing' });
const p1 = plan(withMarker(H.emptyDoc()), { records: [a, b, c], openPrs: [101, 102, 103] });
{
  ok(p1.ok && JSON.stringify(types(p1)) === JSON.stringify(['observed:new>needed', 'observed:new>needed', 'observed:new>blocked']), 'tick: first sight of three open PRs is one observed event each', JSON.stringify(types(p1)));
  ok(p1.events.every(e => RF.checkEvent(e).length === 0 && e.recorded_by === 'lane' && e.actor.kind === 'lane'), 'tick: every event passes the format (checkEvent)');
  ok(plan(p1.doc, { records: [a, b, c], openPrs: [101, 102, 103], at: at(15) }).changed === false, 'tick: the same items read again write nothing');
  ok(plan(p1.doc, { records: [a, b, c], openPrs: [101, 102, 103] }).events.length === 0, 'tick: the same tick run twice writes nothing the second time');
  ok(plan(p1.doc, { records: [rec(101, { head: sha('e'), reason: 'new head' }), b, c], openPrs: [101, 102, 103], at: at(15) }).events.length === 0, 'tick: a rebase is the same item and writes nothing');
  ok(JSON.stringify(types(plan(p1.doc, { records: [a, b, rec(103)], openPrs: [101, 102, 103], at: at(15) }))) === JSON.stringify(['observed:blocked>needed']), 'tick: blocked to needed is one observed event');
  ok(JSON.stringify(types(plan(p1.doc, { records: [rec(101, { class: 'immediate-cluster-change' }), b, c], openPrs: [101, 102, 103], at: at(15) }))) === JSON.stringify(['observed:needed>needed']), 'tick: a new class in the same state is one observed event (state, lane or class)');
  const held = rec(102, { lane: 'LANE-B-OWNER', class: 'hold', findings: ['LANE-B-OWNER/hold: .renovate/holds.json5'] });
  ok(JSON.stringify(types(plan(p1.doc, { records: [a, held, c], openPrs: [101, 102, 103], at: at(15) }))) === JSON.stringify(['observed:needed>held']), 'tick: a hold is needed to held');
  const p5 = plan(p1.doc, { records: [rec(101, { to: '1.2.0' }), b, c], openPrs: [101, 102, 103], at: at(15) });
  ok(JSON.stringify(types(p5)) === JSON.stringify(['closed:needed>closed', 'observed:new>needed']) && p5.events[0].actor.kind === 'renovate' && p5.terminals.length === 0, 'tick: a newer version seen open on the same PR closes the old item and observes the new one; no terminal for an open PR');
  // the PR left the open list: merged by Renovate, on the item the lane saw
  const p6 = plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), scan: scanOf([mergedPr(101, 10)]) });
  ok(JSON.stringify(types(p6)) === JSON.stringify(['merged:needed>merged_unverified']) && p6.events[0].item_id === itemOf(a) && p6.events[0].actor.login === 'renovate[bot]' && p6.events[0].recorded_by === 'lane' && p6.events[0].evidence.merge_commit === sha('a'),
    'tick: a PR that left the open list and is in the merged list: merged by Renovate, on the item the lane saw', JSON.stringify(p6.events));
  ok(p6.terminals.length === 1 && p6.terminals[0].pr === 101 && p6.terminals[0].terminal.type === 'merged', 'tick: the merge is owed to PR 101\'s record');
  ok(plan(p6.doc, { records: [b, c], openPrs: [102, 103], at: at(30), scan: scanOf([mergedPr(101, 10)]) }).events.length === 0, 'tick: the merge is recorded once');
  // H1: Renovate moved the PR to a newer version, and then it merged (or closed)
  const h1 = plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), scan: scanOf([mergedPr(101, 10, { to: '1.3.0' })]) });
  const newer = H.laneItem(mergedPr(101, 10, { to: '1.3.0' }), REPO, rules, at(15)).id;
  ok(JSON.stringify(types(h1)) === JSON.stringify(['closed:needed>closed', 'merged:new>merged_unverified']) && h1.events[0].item_id === itemOf(a) && h1.events[0].actor.kind === 'renovate' && /moved to ghcr\.io\/example\/app101 1\.3\.0 before it merged/.test(h1.events[0].reason) && h1.events[1].item_id === newer && h1.events[1].recorded_by === 'lane',
    'H1: a PR moved to a newer version and then merged: the old item closes (Renovate replaced it) and the merge goes on the item that was merged', JSON.stringify(types(h1)));
  ok(h1.terminals.length === 1 && h1.terminals[0].terminal.type === 'merged' && h1.terminals[0].terminal.item === newer && h1.doc.items[newer]?.to === '1.3.0', 'H1: the PR\'s terminal names the merged item, and its description says 1.3.0', JSON.stringify(h1.terminals));
  ok(plan(h1.doc, { records: [b, c], openPrs: [102, 103], at: at(30), scan: scanOf([mergedPr(101, 10, { to: '1.3.0' })]) }).events.length === 0, 'H1: and nothing again on the next tick');
  const h1c = plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), fates: new Map([[101, closedFate(101, { body: table([{ name: 'ghcr.io/example/app101', update: 'minor', from: '1.0.0', to: '1.3.0' }]) })]]) });
  ok(JSON.stringify(types(h1c)) === JSON.stringify(['closed:needed>closed']) && /moved to ghcr\.io\/example\/app101 1\.3\.0 before it closed/.test(h1c.events[0].reason) && h1c.notes.unrecorded.some(x => x.item === newer && /no edge from first sight to closed/.test(x.why)),
    'H1: moved to a newer version and then closed: the old item closes as replaced; the newer one, never seen open, is named (the format has no edge for it)', JSON.stringify(h1c.notes.unrecorded));
  const h1u = plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), scan: scanOf([mergedPr(101, 10, { to: '1.3.0', mergeCommit: null })]) });
  ok(h1u.events.length === 0 && h1u.notes.unread.some(x => x.pr === 101), 'H1: a newer version merged whose commit was not read: nothing yet, not even the old item\'s close');
  for (const [who, kind] of [['app/haynes-ops-bot', 'shepherd'], ['app/haynes-dev-bot', 'hand'], ['thaynes43', 'person'], ['app/github-actions', 'unknown']]) {
    const p = plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), scan: scanOf([mergedPr(101, 10, { mergedBy: { login: who } })]) });
    ok(p.events[0]?.actor.kind === kind && p.events[0].actor.login === H.normLogin(who) && RF.checkEvent(p.events[0]).length === 0, `tick: merged by ${who} is actor ${kind}, login kept`, JSON.stringify(p.events[0]?.actor));
  }
  // M3: closed, by whom it was closed; an unread closer writes nothing
  const p7 = plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), fates: new Map([[101, closedFate(101)]]) });
  ok(JSON.stringify(types(p7)) === JSON.stringify(['closed:needed>closed']) && p7.events[0].actor.kind === 'renovate' && /closed without a merge by renovate\[bot\]/.test(p7.events[0].reason) && p7.terminals[0]?.terminal.type === 'closed', 'M3: a PR closed without a merge: closed, by who closed it');
  const p7u = plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), fates: new Map([[101, closedFate(101, { closedBy: null, closed_by_read: false })]]) });
  ok(p7u.events.length === 0 && p7u.notes.unread.some(x => x.pr === 101 && /who closed it could not be read/.test(x.why)), 'M3: who closed it could not be read: no event this tick (never the lane as the closer)');
  const sh = plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), fates: new Map([[101, closedFate(101, { closedBy: 'haynes-ops-bot[bot]' })]]) });
  ok(JSON.stringify(types(sh)) === JSON.stringify(['closed:needed>closed']) && sh.events[0].actor.kind === 'shepherd' && sh.events[0].actor.login === 'haynes-ops-bot[bot]' && RF.checkEvent(sh.events[0]).length === 0,
    'format point 8: a close by the shepherd is recorded as the shepherd\'s', JSON.stringify(sh.events[0]));
  const ga = plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), fates: new Map([[101, closedFate(101, { closedBy: 'github-actions[bot]' })]]) });
  ok(ga.events[0]?.actor.kind === 'unknown' && ga.events[0].actor.login === 'github-actions[bot]', 'format point 9: closed by another bot: actor unknown (not established), its login kept');
  ok(plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), fates: new Map([[101, null]]) }).notes.unread.some(x => x.pr === 101), 'tick: a PR that left the open list and cannot be read: no event');
  ok(plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15) }).notes.deferred.some(x => x.pr === 101), 'L4: a PR whose state was not read this tick (budget) is deferred, no event');
  ok(plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), fates: new Map([[101, { ...closedFate(101), state: 'OPEN' }]]) }).notes.still_open.some(x => x.pr === 101), 'tick: open but not listed: no event');
  ok(JSON.stringify(types(plan(p7.doc, { records: [a, b, c], openPrs: [101, 102, 103], at: at(30) }))) === JSON.stringify(['observed:closed>needed']), 'tick: a reopened PR observes its closed item again');
  const fi = { schema: 'haynes.upgrade-lane/v1', pr: 104, lane: 'BLOCKED', class: 'facts-incomplete', reason: 'read failed', update_type: 'unknown', rules: [], checks: {}, targets: [] };
  ok(plan(p1.doc, { records: [a, b, c, fi], openPrs: [101, 102, 103, 104], at: at(15) }).events.length === 0, 'tick: a record whose facts could not be read writes nothing');
  // two open PRs that carry the same item: no flip-flop, and an unreadable lower PR changes nothing (L7)
  const twin = rec(105, { pkg: a.package, app: 101, lane: 'LANE-B-AGENT', class: 'rule-agent' });
  const p10 = plan(p1.doc, { records: [twin, a, b, c], openPrs: [101, 102, 103, 105], at: at(15) });
  ok(p10.events.length === 0 && plan(p10.doc, { records: [a, twin, b, c], openPrs: [101, 102, 103, 105], at: at(30) }).events.length === 0, 'tick: two open PRs that carry the same item write one record and do not flip-flop');
  const fi101 = { ...fi, pr: 101 };
  const p11 = plan(p10.doc, { records: [fi101, twin, b, c], openPrs: [101, 102, 103, 105], at: at(45) });
  ok(p11.events.length === 0 && p11.notes.shared_item.some(x => x.pr === 105 && x.with === 101) && plan(p11.doc, { records: [a, twin, b, c], openPrs: [101, 102, 103, 105], at: at(60) }).events.length === 0,
    'L7: a transient facts-incomplete on the lower PR does not hand the item to the higher one (no events either way)', JSON.stringify(p11.notes.shared_item));
  const again = plan(p6.doc, { records: [rec(106, { pkg: a.package, app: 101 })], openPrs: [106], at: at(30) });
  ok(again.events.length === 0 && again.notes.not_observed.some(x => x.pr === 106 && x.state === 'merged_unverified'), 'tick: a new PR for an item already merged writes no observed event');
}

// ── 3. live merge-step events ───────────────────────────────────────────────────
{
  const x = rec(201);
  const base = plan(withMarker(H.emptyDoc()), { records: [x], openPrs: [201] });
  const v = (verdict, extra = {}) => ({ pr: 201, head: x.head, class: 'cluster-change', chosen: true, mode: 'live', verdict, pr_refusals: [], lane_refusals: [], merge_call: 'gh pr merge 201 (exit 0)', ...extra });
  const pm = plan(base.doc, { records: [x], openPrs: [201], at: at(15), verdict: v('merged', { merged: { merge_commit: sha('d'), merged_at: at(15) } }) });
  ok(JSON.stringify(types(pm)) === JSON.stringify(['merge-attempted:needed>in_progress', 'merged:in_progress>merged_unverified']) && pm.terminals[0]?.terminal.type === 'merged', 'live: a merge verdict writes merge-attempted and merged, and is owed to the PR record');
  ok(plan(base.doc, { records: [x], openPrs: [201], at: at(15), verdict: { ...v('refused'), mode: 'dryrun' } }).events.length === 0, 'live: a dry-run verdict writes nothing');
  ok(plan(base.doc, { records: [x], openPrs: [201], at: at(15), verdict: v('refused', { lane_refusals: ['class not allowlisted'] }) }).events.length === 0, 'live: a lane refusal writes nothing');
  const r1 = plan(base.doc, { records: [x], openPrs: [201], at: at(15), verdict: v('refused', { pr_refusals: ['head behind main'] }) });
  const r2 = plan(r1.doc, { records: [x], openPrs: [201], at: at(30), verdict: v('refused', { pr_refusals: ['head behind main'] }) });
  const r3 = plan(r2.doc, { records: [x], openPrs: [201], at: at(45), verdict: v('refused', { pr_refusals: ['a check is running'] }) });
  ok(r1.events.length === 2 && r2.events.length === 0 && r2.notes.collapsed.length === 1 && r3.events.length === 2 && r3.events[0].attempt === 2, 'live: a refusal repeated at the same head for the same reasons is recorded once');
  // M4: a merge by the lane owes verification: its merged_unverified is never forgotten
  ok(!H.forgettable(H.replay(pm.doc).items.get(itemOf(x)).item) && H.forgettable(H.replay(plan(base.doc, { records: [], openPrs: [], at: at(15), scan: scanOf([mergedPr(201, 10)]) }).doc).items.get(itemOf(x)).item),
    'M4: merged_unverified is forgettable only when someone other than the lane merged it');
}

// ── 4. items by the lane's route, the backfill, the comparison ───────────────────────
{
  // H2: for the 30 real PRs of the 2026-11-09 queue, the item the backfill names from the PR
  // (its package table, labels, title, files) is the item the lane names from its record
  let same = 0;
  for (const p of queue.prs) {
    const f = structuredClone(p.facts);
    const lane = RF.fromLaneRecord(L.classify(f, rules, { now: Date.parse(queue.now), holdRules }), { repo: REPO, observed_at: queue.now }).id;
    const back = H.laneItem({ number: f.number, title: f.title, labels: f.labels.map(name => ({ name })), files: f.files.map(path => ({ path })), body: table(f.packages) }, REPO, rules, queue.now);
    if (back.id === lane && back.basis === 'package-table') same++; else console.log(`     #${f.number}: backfill ${back.id} (${back.basis}), lane ${lane}`);
  }
  ok(same === queue.prs.length, `H2: a backfilled item's id equals the lane's id for the same PR (${same} of ${queue.prs.length} real PRs of the 2026-11-09 queue)`);
  const fx = fixture.record, ev0 = fx.events[0];
  const e4817 = { number: 4817, title: fx.item.carrier.title, author: { login: 'app/renovate' }, mergedAt: '2026-11-11T08:49:05Z', mergedBy: { login: 'app/renovate' }, labels: [{ name: 'renovate/container' }, { name: 'type/patch' }], mergeCommit: { oid: ev0.evidence.merge_commit }, headRefOid: fx.item.carrier.head, body: table(fx.item.item.packages), files: fx.item.carrier.files.map(path => ({ path })) };
  const b4817 = H.laneItem(e4817, REPO, rules, ev0.recorded_at);
  ok(b4817.id === fx.item.id && b4817.desc.to === '2.2.20' && fx.item.available.version === '2.2.20', 'H2: PR 4817 names the format fixture\'s item (corrected to the lane\'s route: 2.2.20 from the package table, not the title\'s v2.2.20)', JSON.stringify(b4817));
  const pf = plan(H.emptyDoc(), { at: ev0.recorded_at, backfilling: true, scan: scanOf([e4817], '2026-10-12T15:00:00.000Z', ev0.recorded_at) });
  ok(pf.events.length === 1 && pf.events[0].id === ev0.id && RF.replay({ ...fx.item, state: null }, [pf.events[0]]).errors.length === 0, 'H2: and derives the fixture\'s event, which replays onto its item');
  const byTitle = H.laneItem({ ...e4817, body: undefined }, REPO, rules, at(0));
  ok(byTitle.basis === 'title' && byTitle.desc.basis === 'title' && byTitle.id !== b4817.id, 'H2: with no body read, an item is named from the title and marked basis title');
  ok(plan(H.emptyDoc(), { backfilling: true, scan: scanOf([{ ...mergedPr(301, -100), body: undefined }]) }).title_items === 1, 'H2: the plan counts the items named from a title');
  // the backfill: Renovate merges, a shared version, a group, a revert
  const list = [mergedPr(301, -3000), mergedPr(302, -2000), { ...mergedPr(303, -1000), title: 'feat(deps): update dev-env image toolchain group', body: 'no table here' },
    { ...mergedPr(304, -900, { pkg: 'ghcr.io/example/app301', app: 301 }) },
    { number: 311, title: `Revert "${mergedPr(302, 0).title}"`, author: { login: 'thaynes43' }, mergedAt: at(-500), mergedBy: { login: 'thaynes43' }, labels: [], mergeCommit: { oid: sha('8') } }];
  const titles = new Map([[302, mergedPr(302, 0).title]]);
  const p = plan(H.emptyDoc(), { backfilling: true, scan: scanOf(list), reverts: new Map([[311, { targets: [302], titles }]]) });
  const cmp = p.compare;
  ok(p.ok && JSON.stringify(types(p)) === JSON.stringify(['merged:new>merged_unverified', 'merged:new>merged_unverified', 'merged:new>merged_unverified', 'revert-merged:merged_unverified>reverted_unverified']), 'backfill: three Renovate merges and one revert by a person', JSON.stringify(types(p)));
  ok(cmp.github_count === 4 && cmp.listed === 4 && cmp.history_count === 4 && cmp.recorded === 3 && cmp.shared_item.length === 1 && cmp.shared_item[0].pr === 304 && cmp.missing.length === 0 && cmp.match === true && cmp.reverts.titled === 1 && cmp.reverts.recorded === 1 && /github_count .* against history_count/.test(cmp.compared),
    'M2: the comparison: GitHub\'s own count 4 against the 4 the history holds (3 on their own item, 1 through another PR\'s), none missing', JSON.stringify(cmp));
  ok(p.doc.items[H.laneItem(list[2], REPO, rules, at(0)).id]?.name === `${REPO}#303` && p.doc.items[H.laneItem(list[2], REPO, rules, at(0)).id]?.basis === 'title', 'backfill: a PR whose body gives no package table takes the title route (here a group title with no version: <repo>#<pr>), marked basis title');
  ok(p.doc.meta.backfill?.compare?.match === true, 'backfill: the marker is set with its comparison');
  // M2: a list shorter than GitHub's count is a mismatch, whatever the history holds
  const cut = plan(H.emptyDoc(), { backfilling: true, scan: { ...scanOf(list), github_count: 5 } });
  ok(cut.compare.match === false && cut.compare.github_count === 5 && cut.compare.listed === 4, 'M2: GitHub counts 5 and the list holds 4: the comparison fails (the list was cut)', JSON.stringify(cut.compare));
  ok(plan(H.emptyDoc(), { backfilling: true, scan: { ...scanOf(list), github_count: null } }).compare.match === null, 'M2: GitHub\'s count unread: not compared (match null), never true');
  // idempotent
  ok(plan({ ...p.doc, meta: { ...p.doc.meta, backfill: null } }, { backfilling: true, scan: scanOf(list), reverts: new Map([[311, { targets: [302], titles }]]), at: at(5) }).events.length === 0, 'backfill: run again over the same window, it writes nothing (idempotent)');
  ok(plan(p.doc, { scan: scanOf(list), at: at(5) }).events.length === 0 && plan(p.doc, { scan: scanOf(list), at: at(5) }).notes.revert_unmatched.length === 0, 'L6: a catch-up over the same window writes nothing, and a recorded revert is not looked at again');
  // a scan that failed never marks the backfill done (mutation that survived)
  const failed = plan(H.emptyDoc(), { backfilling: true, scan: { ok: false, why: 'unreadable', window: { since: at(-100), until: at(0) } } });
  ok(failed.ok && failed.doc.meta.backfill === null && failed.backfill_complete === false, 'backfill: an unreadable merged list leaves no backfill marker');
  const deferredRevert = plan(H.emptyDoc(), { backfilling: true, scan: scanOf(list) });
  ok(deferredRevert.doc.meta.backfill === null && deferredRevert.notes.deferred.some(x => x.pr === 311), 'L4: a revert left unread by the budget leaves no backfill marker; the next tick backfills again');
  const noCommit = plan(H.emptyDoc(), { backfilling: true, scan: scanOf([mergedPr(401, -100), mergedPr(402, -50, { mergeCommit: null })]) });
  ok(noCommit.compare.match === false && JSON.stringify(noCommit.compare.missing) === '[402]', 'backfill: a merged Renovate PR the history could not record is named missing');
  // L6: a revert's quoted title decides the target, not the first "Reverts #N"
  const two = plan(H.emptyDoc(), { backfilling: true, scan: scanOf(list), reverts: new Map([[311, { targets: [301, 302], titles: new Map([[301, mergedPr(301, 0).title], [302, mergedPr(302, 0).title]]) }]]) });
  ok(two.events.find(e => e.type === 'revert-merged')?.carrier.pr === 302, 'L6: a body that names two PRs: the one whose title the revert quotes');
  const wrong = plan(H.emptyDoc(), { backfilling: true, scan: scanOf(list), reverts: new Map([[311, { targets: [301], titles: new Map([[301, mergedPr(301, 0).title]]) }]]) });
  ok(!wrong.events.some(e => e.type === 'revert-merged') && wrong.notes.revert_unmatched.some(x => /has the title it quotes/.test(x.why)), 'L6: a body whose PR does not have the quoted title: not recorded, named');
  const bot = plan(H.emptyDoc(), { backfilling: true, scan: scanOf([mergedPr(301, -3000), { ...list[4], number: 312, mergedBy: { login: 'app/haynes-ops-bot' }, title: `Revert "${mergedPr(301, 0).title}"` }]), reverts: new Map([[312, { targets: [301], titles: new Map([[301, mergedPr(301, 0).title]]) }]]) });
  ok(bot.notes.unrecorded.some(x => x.type === 'revert-merged'), 'backfill: a revert merged by the shepherd is not recorded, and is named');
  // terminals never come from the backfill (mutation that survived)
  ok(p.terminals.length === 0 && H.terminalsOf(p.doc, new Set()).length === 0, 'L2: backfilled merges are never owed to a PR record (the lane did not watch those PRs)');
  // M4: a merge of a version the history remembers as reverted is visible
  const s = rec(501);
  let d = plan(withMarker(H.emptyDoc()), { records: [s], openPrs: [501], at: at(-60) }).doc;
  const id = itemOf(s);
  const ev = (type, from, to, min, extra) => H.makeEvent({ item_id: id, type, from, to, at: at(min), actor: { kind: 'lane', login: null }, recorded_by: 'lane', attempt: 1, ...extra });
  d = { ...d, events: [...d.events, ev('merge-attempted', 'needed', 'in_progress', -59, { lane: 'A-night', carrier: { repo: REPO, pr: 501 } }), ev('merged', 'in_progress', 'merged_unverified', -58, { carrier: { repo: REPO, pr: 501 }, evidence: { merge_commit: sha('5') } }),
    ev('verification-failed', 'merged_unverified', 'failed', -50, { class: 'not-ready', reason: 'x' }), ev('revert-opened', 'failed', 'reverting', -49, { evidence: { revert_pr: 9001 } }), ev('revert-merged', 'reverting', 'reverted_unverified', -40, { evidence: { revert_commit: sha('6') } }), ev('recovery-verified', 'reverted_unverified', 'reverted', -30, {})] };
  const rm = plan(d, { scan: scanOf([mergedPr(502, -5, { pkg: s.package, app: 501 })]) });
  ok(types(rm).join() === 'merged:reverted>merged_unverified' && rm.events[0].actor.kind === 'renovate' && rm.notes.remembered_merges.some(x => x.pr === 502 && x.state === 'reverted') && !rm.doc.meta.remembered_merges, 'format point 7: a merge of a version remembered as reverted is recorded as an event (merged, from reverted), no meta workaround', JSON.stringify(types(rm)));
  ok(H.historyError({ write: 'written', remembered_merges: rm.notes.remembered_merges }) && /MERGED WHILE REMEMBERED: #502 \(reverted\)/.test(H.historyLine({ write: 'written', events: 1, remembered_merges: rm.notes.remembered_merges })), 'format point 7: and the tick that sees it logs it at ERROR level');
  ok(!H.forgettable(H.replay(rm.doc).items.get(id).item), 'format point 6/7: an item that was reverted is never forgotten, even after someone merged the version again');
  // M1: a list at the search limit is split; a list still cut after the splits is refused
  const calls = [];
  let seq = 1000;
  const fake = n => (cmd, argv) => { calls.push(argv.join(' ')); if (argv[0] === 'api') return JSON.stringify({ total_count: n, incomplete_results: false }); const q = argv[argv.indexOf('--search') + 1]; if (q.startsWith('Revert')) return '[]'; const [s0, u0] = q.split('merged:')[1].split('..'); const span = Date.parse(u0) - Date.parse(s0); return JSON.stringify(Array.from({ length: span > 15 * 864e5 ? cfg.max_search_results : 3 }, () => ({ ...mergedPr(seq++, -1), mergedAt: s0 }))); };
  const big = H.readMerged(REPO, at(-30 * 1440), at(0), cfg, fake(6), H.makeBudget(cfg), AUTHORS);
  ok(big.ok && big.splits >= 1 && big.list.length === 6 && calls.some(x => x.includes('author:app/renovate merged:')) && calls.filter(x => x.includes('--search')).every(x => /author:app\/renovate|Revert in:title/.test(x)), 'M1: only Renovate\'s PRs and the revert candidates are listed; a list at the limit is split in halves', JSON.stringify({ ok: big.ok, splits: big.splits, n: big.list?.length }));
  ok(H.readMerged(REPO, at(-30 * 1440), at(0), cfg, () => JSON.stringify(Array.from({ length: cfg.max_search_results }, (_, i) => mergedPr(2000 + i, -1))), H.makeBudget(cfg), AUTHORS).ok === false, 'M1: a list still at the limit after the splits is refused, never taken as complete');
  ok(big.github_count === 6, 'M2: GitHub\'s own count is read separately (search total_count)');
  // L4: the budget stops per-PR reads, and the tick's time stops every read
  let tnow = 0;
  const bud = H.makeBudget({ ...cfg, max_pr_reads_per_tick: 2 }, () => tnow, 0);
  ok(bud.pr() && bud.pr() && !bud.pr() && /max_pr_reads_per_tick/.test(bud.stopped), 'L4: at most max_pr_reads_per_tick per-PR reads');
  const bud2 = H.makeBudget(cfg, () => tnow, 0); tnow = cfg.time_budget_seconds * 1e3;
  ok(!bud2.time() && !bud2.pr() && /time_budget_seconds/.test(bud2.stopped) && H.readMerged(REPO, at(-10), at(0), cfg, () => { throw new Error('no read expected'); }, bud2, AUTHORS).ok === false, 'L4: no read starts once the tick has run time_budget_seconds');
}

// ── 5. rotation: 5,000 events ────────────────────────────────────────────────────
{
  const s = rec(500);
  let doc = plan(withMarker(H.emptyDoc()), { records: [s], openPrs: [500], at: at(-60 * 1440) }).doc;
  const sid = itemOf(s);
  const ev = (type, from, to, min, extra, item = sid, n = 1) => H.makeEvent({ item_id: item, type, from, to, at: at(min), actor: { kind: 'lane', login: null }, recorded_by: 'lane', attempt: n, ...extra });
  doc = { ...doc, events: [...doc.events, ev('merge-attempted', 'needed', 'in_progress', -59 * 1440, { lane: 'A-night', carrier: { repo: REPO, pr: 500 } }), ev('merged', 'in_progress', 'merged_unverified', -59 * 1440 + 1, { carrier: { repo: REPO, pr: 500 }, evidence: { merge_commit: sha('5') } }),
    ev('verification-failed', 'merged_unverified', 'failed', -59 * 1440 + 30, { class: 'not-ready', reason: 'HelmRelease not Ready' }), ev('revert-opened', 'failed', 'reverting', -59 * 1440 + 31, { evidence: { revert_pr: 9001 } }),
    ev('revert-merged', 'reverting', 'reverted_unverified', -59 * 1440 + 40, { evidence: { revert_commit: sha('6') } }), ev('recovery-verified', 'reverted_unverified', 'reverted', -59 * 1440 + 60, {})] };
  // an item the lane merged whose verification is owed (merged_unverified by the lane)
  const lm = rec(504);
  doc = plan(doc, { records: [lm], openPrs: [504], at: at(-59 * 1440 + 100) }).doc;
  const lid = itemOf(lm);
  doc = { ...doc, events: [...doc.events, ev('merge-attempted', 'needed', 'in_progress', -59 * 1440 + 101, { lane: 'A-night', carrier: { repo: REPO, pr: 504 } }, lid), ev('merged', 'in_progress', 'merged_unverified', -59 * 1440 + 102, { carrier: { repo: REPO, pr: 504 }, evidence: { merge_commit: sha('7') } }, lid)] };
  const o = rec(501), cl = rec(502);
  const po = plan(doc, { records: [o, cl], openPrs: [501, 502], at: at(-58 * 1440) });
  doc = plan(po.doc, { records: [o], openPrs: [501], at: at(-58 * 1440 + 15), fates: new Map([[502, closedFate(502, { closedAt: at(-58 * 1440 + 10) })]]) }).doc;
  const list = Array.from({ length: 5000 }, (_, i) => mergedPr(10000 + i, -57 * 1440 + i * 15, { pkg: `ghcr.io/example/bulk${i}`, to: `1.${i}.0` }));
  const big = plan(doc, { records: [o], openPrs: [501], scan: scanOf(list, at(-57 * 1440), at(0)) });
  const m = H.historyManifest(big.doc, '7');
  ok(big.ok && big.events.length === 5000, 'rotation: 5,000 new events derived', big.why || big.events.length);
  ok(H.sizeOf(m) <= cfg.max_bytes && Buffer.byteLength(JSON.stringify(m)) < 1024 * 1024, `rotation: the history stays under its ${cfg.max_bytes}-byte cap (${H.sizeOf(m)} bytes of data, ${Buffer.byteLength(JSON.stringify(m))} bytes as a request)`);
  ok(H.sizeOf(m) > cfg.max_bytes * 0.9, `rotation: and does not drop much more than it must (${H.sizeOf(m)} bytes kept)`);
  const after = H.replay(big.doc);
  ok(after.errors.length === 0 && big.rotation.dropped > 0, 'rotation: what is left replays from the bases', after.errors[0]);
  ok(!big.doc.events.some(e => e.item_id === sid) && big.doc.items[sid]?.base?.state === 'reverted' && after.items.get(sid).item.state === 'reverted', 'rotation: the reverted item keeps its state after its events rotated out');
  ok(!big.doc.events.some(e => e.item_id === lid) && big.doc.items[lid]?.base?.state === 'merged_unverified' && big.doc.items[lid].base.by === 'lane', 'M4: a merge the lane made, verification owed, keeps its place after its events rotated out', JSON.stringify(big.doc.items[lid]));
  ok(after.items.get(itemOf(o))?.item.state === 'needed', 'rotation: an open item keeps its place');
  ok(!big.doc.items[itemOf(cl)] && !after.items.has(itemOf(cl)), 'rotation: a closed item with no event left is forgotten');
  const back = plan(big.doc, { records: [rec(503, { pkg: s.package, app: 500 })], openPrs: [503, 501], at: at(15) });
  ok(back.events.length === 0 && back.notes.not_observed.some(x => x.pr === 503 && x.state === 'reverted'), 'rotation: a new PR for the reverted version is not observed as needed: the version stays remembered');
  ok(H.parseDoc({ metadata: { resourceVersion: '7' }, data: m.data }).ok, 'rotation: the rotated object parses back');
  console.log(`rotation: 5,000 merges; ${big.rotation.dropped} rotated out, ${big.doc.events.length} kept, ${Object.keys(big.doc.items).length} items; ${H.sizeOf(m)} of ${cfg.max_bytes} bytes; ${Math.round(Buffer.byteLength(m.data.events) / big.doc.events.length)} bytes per stored event line`);
}

// ── 6. the writes ─────────────────────────────────────────────────────────────
// a fake API server for the two ConfigMaps: create-on-apply, a write at a resourceVersion
const api = () => {
  const objs = { 'upgrade-ledger': null, 'upgrade-ledger-marker': null };
  let rv = 100;
  const writes = [];
  const apply = (m, mgr) => {
    writes.push(mgr);
    const n = m.metadata.name, cur = objs[n];
    if (mgr === H.INIT_MANAGER) { if (!cur) objs[n] = { metadata: { name: n, resourceVersion: String(++rv) }, data: {} }; return { ok: true, rv: objs[n].metadata.resourceVersion, conflict: false, hasData: H.KEYS.some(k => objs[n].data?.[k] !== undefined) }; }
    if (mgr === H.MANAGER && (!cur || m.metadata.resourceVersion !== cur.metadata.resourceVersion)) return { ok: false, conflict: true, detail: 'conflict (HTTP 409)', raw: 'Error from server (Conflict)' };
    objs[n] = { metadata: { name: n, resourceVersion: String(++rv) }, data: structuredClone(m.data) };
    return { ok: true, rv: objs[n].metadata.resourceVersion, conflict: false };
  };
  const run = (cmd, argv) => {
    if (cmd === 'kubectl' && argv[0] === 'get') { const n = argv[argv.indexOf('--field-selector') + 1].split('=')[1]; return JSON.stringify({ items: objs[n] ? [objs[n]] : [] }); }
    if (cmd === 'gh' && argv[1] === 'list') return '[]';
    if (cmd === 'gh' && argv[0] === 'api' && argv[1].startsWith('search/')) return JSON.stringify({ total_count: 0, incomplete_results: false });
    return null;
  };
  return { apply, run, objs, writes, get: n => objs[n || 'upgrade-ledger'] };
};
const phase = (f, recs, o = {}) => H.historyPhase({ rules, run: f.run, env: {}, repo: REPO, at: at(o.min || 0), records: recs, openPrs: recs.map(r => r.pr), verdict: null, write: true, apply: f.apply, ...o });
{
  // the first tick: the empty create, the events at its version, then the marker
  const f = api();
  const t1 = phase(f, [rec(601)]);
  const mk = JSON.parse(f.get('upgrade-ledger-marker').data.marker);
  ok(t1.summary.written && JSON.stringify(f.writes) === JSON.stringify([H.INIT_MANAGER, H.MANAGER, H.MARKER_MANAGER]) && mk.schema === H.MARKER_SCHEMA && mk.created_at === at(0) && mk.backfilled_at === at(0) && t1.summary.marker === 'written',
    'M5: the first tick creates the history, writes its events and backfill, then the marker (created, backfilled)', JSON.stringify({ w: f.writes, mk }));
  ok(phase(f, [rec(601)], { min: 15 }).summary.write === 'not-needed' && f.writes.length === 3, 'M5: an unchanged tick writes neither the history nor the marker');
  // M5: the history is lost (deleted, or emptied): nothing is written, every tick says so
  for (const [what, lose] of [['deleted', () => { f.objs['upgrade-ledger'] = null; }], ['emptied', () => { f.objs['upgrade-ledger'] = { metadata: { name: 'upgrade-ledger', resourceVersion: '999' }, data: {} }; }]]) {
    lose();
    const n0 = f.writes.length;
    const t = phase(f, [rec(601), rec(602)], { min: 30 });
    ok(t.summary.write === 'stuck' && t.summary.lost?.marker && /the history was lost: upgrade-ledger-marker says a history exists \(created .*backfilled .*\), but upgrade-ledger is (absent|empty)\. Nothing is written until a person restores upgrade-ledger or deletes upgrade-ledger-marker/.test(t.summary.why_not) && f.writes.length === n0 && H.historyError(t.summary),
      `M5: a ${what} history with its marker left: nothing written, a stuck ERROR naming the two ways out`, JSON.stringify(t.summary.why_not));
    ok(phase(f, [rec(601)], { min: 45 }).summary.write === 'stuck', `M5: and the same on the next tick (${what})`);
  }
  // clearing the marker starts a new history (and a new backfill)
  f.objs['upgrade-ledger-marker'] = null; f.objs['upgrade-ledger'] = null;
  ok(phase(f, [rec(601)], { min: 60 }).summary.written && f.get('upgrade-ledger-marker'), 'M5: with the marker deleted, the next tick starts a new history and writes a new marker');
  const bad = api(); bad.objs['upgrade-ledger-marker'] = { metadata: { name: 'upgrade-ledger-marker', resourceVersion: '5' }, data: { marker: 'garbage' } };
  ok(phase(bad, [rec(601)]).summary.write === 'stuck' && bad.writes.length === 0, 'M5: a marker that is not one is refused (fail closed): nothing written');
  // two writers overlap: the second conflicts and writes nothing; its next tick writes its events
  const g = api();
  phase(g, [rec(611)]);
  const snap = structuredClone(g.get());
  const runAt = o => (cmd, argv) => (cmd === 'kubectl' && argv[0] === 'get' && argv.includes('metadata.name=upgrade-ledger') ? JSON.stringify({ items: [o] }) : g.run(cmd, argv));
  const A = H.historyPhase({ rules, run: runAt(snap), env: {}, repo: REPO, at: at(15), records: [rec(611), rec(612)], openPrs: [611, 612], verdict: null, write: true, apply: g.apply });
  const B = H.historyPhase({ rules, run: runAt(snap), env: {}, repo: REPO, at: at(16), records: [rec(611), rec(613)], openPrs: [611, 613], verdict: null, write: true, apply: g.apply });
  ok(A.summary.written && B.summary.write === 'conflict' && B.summary.events === 0, 'write: two writers read the same version; the second conflicts and writes nothing');
  phase(g, [rec(611), rec(612), rec(613)], { min: 30 });
  const ev = g.get().data.events.trim().split('\n').map(JSON.parse);
  ok([611, 612, 613].every(pr => ev.some(e => e.type === 'observed' && e.carrier.pr === pr)) && ev.length === 3, 'write: its next tick writes the lost events: every first sighting is in the history, once');
  // two first writers: the second's empty create meets the first one's events
  const h = api();
  const empty = (cmd, argv) => (cmd === 'kubectl' && argv[0] === 'get' ? JSON.stringify({ items: [] }) : h.run(cmd, argv));
  H.historyPhase({ rules, run: empty, env: {}, repo: REPO, at: at(0), records: [rec(1101)], openPrs: [1101], verdict: null, write: true, apply: h.apply });
  const second = H.historyPhase({ rules, run: empty, env: {}, repo: REPO, at: at(1), records: [rec(1102)], openPrs: [1102], verdict: null, write: true, apply: h.apply });
  ok(second.summary.write === 'conflict' && /created upgrade-ledger with events after this tick read it absent/.test(second.summary.why_not) && h.get().data.events.trim().split('\n').length === 1, 'write: a second first writer whose create meets the first one\'s events writes nothing');
  // a failed write: reported in the fixed vocabulary; the next tick writes the same events
  const k = api();
  const fail = H.historyPhase({ rules, run: k.run, env: {}, repo: REPO, at: at(0), records: [rec(701)], openPrs: [701], verdict: null, write: true, apply: (m, mgr) => (mgr === H.INIT_MANAGER ? k.apply(m, mgr) : { ok: false, conflict: false, detail: 'refused by the API server (Forbidden)', raw: 'Error from server (Forbidden): secret' }) });
  ok(fail.summary.write === 'failed' && fail.summary.derived === 1 && fail.summary.events === 0 && fail.summary.terminals.length === 0 && !/secret/.test(fail.summary.why_not) && /^FAILED: 1 event derived \(observed 1\), none written/.test(H.historyLine(fail.summary)), 'write: a failed write is reported, nothing written, no terminal offered');
  ok(phase(k, [rec(701)], { min: 15 }).summary.events === 1, 'write: the next tick writes the event the failed one derived');
  // L1: a history that cannot be read, does not replay, a bad config, over the cap: stuck, ERROR
  const corrupt = { metadata: { name: 'upgrade-ledger', resourceVersion: '9' }, data: { meta: JSON.stringify({ schema: H.HISTORY_SCHEMA }), items: '{}', events: '{"item_id":"x","type":"observed"}\n' } };
  const one = x => (cmd, argv) => (cmd === 'kubectl' ? JSON.stringify({ items: argv.includes('metadata.name=upgrade-ledger') ? [x] : [] }) : '[]');
  let wrote = 0;
  const noWrite = () => { wrote++; return { ok: true, rv: '10' }; };
  const cr = H.historyPhase({ rules, run: one(corrupt), env: {}, repo: REPO, at: at(0), records: [rec(801)], openPrs: [801], verdict: null, write: true, apply: noWrite });
  ok(wrote === 0 && cr.summary.write === 'stuck' && H.historyError(cr.summary) && /data.events line 1/.test(cr.summary.why_not), 'L1: a corrupt history is stuck (ERROR) and never overwritten');
  const broken = { ...corrupt, data: { meta: JSON.stringify({ schema: H.HISTORY_SCHEMA }), items: '{}', events: JSON.stringify(H.pack(H.makeEvent({ item_id: 'renovate-pr:' + 'a'.repeat(24), type: 'closed', from: 'needed', to: 'closed', at: at(0), actor: { kind: 'lane' }, recorded_by: 'lane', reason: 'x' }))) + '\n' } };
  const nr = H.historyPhase({ rules, run: one(broken), env: {}, repo: REPO, at: at(0), records: [], openPrs: [], verdict: null, write: true, apply: noWrite });
  ok(wrote === 0 && nr.summary.write === 'stuck' && /does not replay/.test(nr.summary.why_not), 'L1: a history that does not replay is stuck (ERROR)');
  ok(H.historyPhase({ rules: { ...rules, night: { ...rules.night, history: { ...rules.night.history, max_bytes: 1 } } }, run: api().run, env: {}, repo: REPO, at: at(0), records: [], openPrs: [], verdict: null, write: true, apply: noWrite }).summary.write === 'stuck', 'L1: a bad config is stuck (ERROR)');
  ok(H.historyError({ write: 'stuck' }) && H.historyError({ write: 'failed' }) && H.historyError({ write: 'conflict' }) && !H.historyError({ write: 'not-needed' }) && !H.historyError({ write: 'written' }), 'L1: every stuck or unwritten outcome is an ERROR line; written and not-needed are not');
  const lr = H.historyPhase({ rules, run: api().run, env: {}, repo: REPO, at: at(0), records: [rec(901)], openPrs: [901], verdict: null, write: false, apply: noWrite });
  ok(wrote === 0 && lr.summary.write === 'planned', 'write: a local run plans and writes nothing');
}

// ── 7. terminal records ────────────────────────────────────────────────────────
{
  const rcd = M.recordConfig(rules);
  const pub = M.projectRecord({ schema: 'haynes.upgrade-lane/v1', pr: 4900, head: sha('1'), lane: 'LANE-A', class: 'cluster-change', terminal: null }, rcd);
  const { terminal, ...old } = pub;
  ok(M.recordContentKey(pub) === M.recordContentKey(old), 'record: a null terminal compares equal to a record written before the field existed');
  const p6 = plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), scan: scanOf([mergedPr(101, 10)]) });
  const t = p6.terminals[0];
  ok(plan(p6.doc, { records: [b, c], openPrs: [102, 103], at: at(15 + 7 * 1440) }).terminals[0]?.terminal.event === t.terminal.event, 'L2: the terminal stays owed on later ticks, a week later too (no clock)');
  const marked = H.markMirrored(p6.doc, t.items);
  ok(marked.marked === 1 && plan(marked.doc, { records: [b, c], openPrs: [102, 103], at: at(30) }).terminals.length === 0, 'L2: once the history marks it mirrored, it is no longer owed');
  // commitMirrors: marks what the writer reports updated, current or with no record; keeps a denial as the mirrors' halt
  const f = api();
  phase(f, [b, c]);
  const ctx = { doc: p6.doc, rv: f.get().metadata.resourceVersion };
  f.objs['upgrade-ledger'] = { metadata: { name: 'upgrade-ledger', resourceVersion: ctx.rv }, data: H.renderData(p6.doc) };
  const cm = H.commitMirrors({ ctx, terminals: p6.terminals, result: { updated: [101] }, halt: null, at: at(20), env: {}, apply: f.apply });
  const after = H.parseDoc(f.get()).doc;
  ok(cm.written && after.items[t.terminal.item].mirrored === t.terminal.event, 'L2: commitMirrors marks the terminal its record now carries', JSON.stringify(cm));
  const ctx2 = { doc: after, rv: f.get().metadata.resourceVersion };
  const ch = H.commitMirrors({ ctx: ctx2, terminals: [], result: {}, halt: { why: 'denied' }, at: at(25), env: {}, apply: f.apply });
  ok(ch.written && H.parseDoc(f.get()).doc.meta.mirror_halt?.why === 'denied', 'L3: a denial on a mirror is kept as the mirrors\' own halt (meta.mirror_halt)');
  // the writer: edit only, never a new comment
  const own = [{ id: 77, user: { login: rcd.author.login, type: rcd.author.type }, body: M.renderRecord(pub, rcd) }];
  const runC = list => (cmd, argv) => (cmd === 'gh' && argv.some(x => /issues\/\d+\/comments/.test(x)) ? JSON.stringify([list]) : null);
  const term = { ...t.terminal };
  const t1 = H.terminalRecordItems([{ pr: 4900, terminal: term, items: [] }], [], rcd, at(0), runC(own), REPO, cfg, null);
  ok(t1.items.length === 1 && M.writePrRecords({ items: t1.items, cfg: rcd, run: () => null, env: {}, live: false }).would_update.includes(4900), 'record: a PR with its record: one edit planned');
  const t2 = H.terminalRecordItems([{ pr: 4901, terminal: term, items: [] }], [], rcd, at(0), runC([]), REPO, cfg, null);
  const w2 = M.writePrRecords({ items: t2.items, cfg: rcd, run: () => null, env: {}, live: true, write: () => { throw new Error('no write expected'); } });
  ok(w2.no_record.includes(4901) && w2.github_writes === 0, 'record: a PR with no lane record gets no new comment');
  const many = Array.from({ length: cfg.max_mirrors_per_tick + 3 }, (_, i) => ({ pr: 5000 + i, terminal: term, items: [] }));
  const tm = H.terminalRecordItems(many, [], rcd, at(0), runC([]), REPO, cfg, null);
  ok(tm.items.length === cfg.max_mirrors_per_tick && tm.deferred.length === 3, 'L2: at most max_mirrors_per_tick mirrors per tick; the rest wait');
}

// ── 8. the readers, directly ─────────────────────────────────────────────────────
{
  const runV = map => (cmd, argv) => { const k = `${cmd} ${argv.slice(0, 3).join(' ')}`; for (const [re, v] of map) if (re.test(k)) return typeof v === 'function' ? v(argv) : v; return null; };
  const view = o => JSON.stringify({ number: 7, state: 'CLOSED', title: 't', author: { login: 'app/renovate' }, mergedAt: null, mergedBy: null, mergeCommit: null, closedAt: at(0), headRefOid: sha('1'), labels: [], body: 'b', files: [], ...o });
  const closedRead = H.readFate(REPO, 7, runV([[/^gh pr view 7/, view()], [/^gh api repos\/thaynes43\/haynes-ops\/issues\/7$/, JSON.stringify({ closed_by: { login: 'renovate[bot]' } })]]));
  ok(closedRead.state === 'CLOSED' && closedRead.closedBy === 'renovate[bot]' && closedRead.closed_by_read === true && closedRead.body === 'b', 'readFate: a closed PR with its closer and the facts its item is named by');
  const closedUnread = H.readFate(REPO, 7, runV([[/^gh pr view 7/, view()]]));
  ok(closedUnread.state === 'CLOSED' && closedUnread.closed_by_read === false, 'readFate: a closer that cannot be read is said to be unread');
  ok(H.readFate(REPO, 7, runV([[/^gh pr view 7/, view({ state: 'MERGED', mergedAt: at(0), mergedBy: { login: 'app/renovate' }, mergeCommit: { oid: sha('2') } })]])).closed_by_read === null, 'readFate: a merged PR needs no closer');
  ok(H.readFate(REPO, 7, runV([[/^gh pr view 7/, 'not json']])) === null && H.readFate(REPO, 7, runV([[/^gh pr view 7/, view({ state: 'WEIRD' })]])) === null, 'readFate: an unreadable or unknown state is null');
  const rt = body => H.readRevertTargets(REPO, 9, runV([[/^gh pr view 9/, JSON.stringify({ body })]]));
  ok(JSON.stringify(rt('Reverts thaynes43/haynes-ops#4817')) === '[4817]' && JSON.stringify(rt('Reverts #12 and Reverts #13')) === '[12,13]', 'readRevertTargets: this repository\'s PRs, every one named');
  ok(JSON.stringify(rt('Reverts someone/else#4817')) === '[]', 'readRevertTargets: a PR of another repository is not a target (mutation that survived)');
  ok(H.readRevertTargets(REPO, 9, () => null) === null, 'readRevertTargets: an unreadable body is null');
  ok(H.readCount(REPO, 'author:app/renovate merged:a..b', runV([[/^gh api search\/issues/, JSON.stringify({ total_count: 12, incomplete_results: false })]])) === 12 && H.readCount(REPO, 'q', runV([[/^gh api search\/issues/, JSON.stringify({ total_count: 12, incomplete_results: true })]])) === null, 'readCount: GitHub\'s total_count; an incomplete count is no count');
}

// ── 9. size ─────────────────────────────────────────────────────────────────────
{
  const p = plan(withMarker(H.emptyDoc()), { records: [rec(1001, { reason: 'x'.repeat(400) })], openPrs: [1001], scan: scanOf([mergedPr(1002, -10)]) });
  ok(p.events.every(e => !e.reason || e.reason.length <= cfg.max_reason_chars), 'size: event reasons are cut to max_reason_chars');
  const lines = H.renderData(p.doc).events.trim().split('\n');
  ok(lines.every(l => !/"schema"|"id"|"login":null/.test(l)) && lines.map(l => H.unpack(JSON.parse(l))).every((e, i) => e.id === p.events[i].id && RF.checkEvent(e).length === 0), 'size: a stored line leaves out schema, id and a null login; unpack restores the event');
  console.log(`size: one observed line ${Buffer.byteLength(lines[0])} bytes (a ${cfg.max_reason_chars}-character reason), one merged line ${Buffer.byteLength(lines[1])} bytes`);
}

// ── 10. the second review (LOW points on bac73879) ──────────────────────────────────
{
  // 1: a split boundary: the second half starts a millisecond after the middle
  const since = at(-30 * 1440), until = at(0);
  const mid = new Date(Math.floor((Date.parse(since) + Date.parse(until)) / 2)).toISOString();
  const atMid = { ...mergedPr(7001, 0), mergedAt: mid }, after = { ...mergedPr(7002, 0), mergedAt: new Date(Date.parse(mid) + 1).toISOString() };
  const runB = (cmd, argv) => {
    if (argv[0] === 'api') return JSON.stringify({ total_count: 2, incomplete_results: false });
    const q = argv[argv.indexOf('--search') + 1];
    if (q.startsWith('Revert')) return '[]';
    const [s0, u0] = q.split('merged:')[1].split('..');
    if (s0 === since && u0 === until) return JSON.stringify(Array.from({ length: cfg.max_search_results }, (_, i) => mergedPr(8000 + i, -1)));
    return JSON.stringify([atMid, after].filter(e => Date.parse(e.mergedAt) >= Date.parse(s0) && Date.parse(e.mergedAt) <= Date.parse(u0)));
  };
  const rb = H.readMerged(REPO, since, until, cfg, runB, H.makeBudget(cfg), AUTHORS);
  ok(rb.ok && rb.list.filter(e => e.number === 7001).length === 1 && rb.list.filter(e => e.number === 7002).length === 1, 'second review 1: at a split, a PR merged at the middle and one a millisecond later are each listed once', JSON.stringify(rb.list?.map(e => [e.number, e.mergedAt])));

  // 2: a stuck PR at the front does not stop a later one from being read on the next tick
  const r1 = { ...rules, night: { ...rules.night, history: { ...rules.night.history, max_pr_reads_per_tick: 1 } } };
  const f = api();
  const seen = [];
  const runF = (cmd, argv) => {
    if (cmd === 'gh' && argv[0] === 'pr' && argv[1] === 'view') { seen.push(Number(argv[2])); return argv[2] === '9102' ? JSON.stringify({ ...mergedPr(9102, 5), state: 'MERGED' }) : null; }
    return f.run(cmd, argv);
  };
  H.historyPhase({ rules: r1, run: runF, env: {}, repo: REPO, at: at(0), records: [rec(9101), rec(9102)], openPrs: [9101, 9102], verdict: null, write: true, apply: f.apply });
  const t1 = H.historyPhase({ rules: r1, run: runF, env: {}, repo: REPO, at: at(15), records: [], openPrs: [], verdict: null, write: true, apply: f.apply });
  const t2 = H.historyPhase({ rules: r1, run: runF, env: {}, repo: REPO, at: at(30), records: [], openPrs: [], verdict: null, write: true, apply: f.apply });
  ok(JSON.stringify(seen) === JSON.stringify([9101, 9102]) && t1.summary.events === 0 && t2.summary.by_type?.merged === 1 && JSON.parse(f.get().data.meta).tried?.fate?.[9101] === at(15),
    'second review 2: with one read a tick, the stuck #9101 is read first, then #9102 on the next tick (least recently tried first)', JSON.stringify({ seen, t2: t2.summary.by_type, tried: JSON.parse(f.get().data.meta).tried }));

  // 3: closed against merged at the same instant: merged wins, also when the merged item had earlier events
  const y = rec(9201, { to: '2.0.0', pkg: 'ghcr.io/example/app9202', app: 9202 });
  const x = rec(9202);
  const d0 = plan(withMarker(H.emptyDoc()), { records: [y, x], openPrs: [9201, 9202] }).doc;
  const d1 = plan(d0, { records: [y], openPrs: [9201], at: at(15), scan: scanOf([mergedPr(9202, 10, { to: '2.0.0' })]) });
  ok(types(d1).join() === 'closed:needed>closed,merged:needed>merged_unverified' && d1.events[0].at === d1.events[1].at && d1.terminals.find(t => t.pr === 9202)?.terminal.type === 'merged',
    'second review 3: a PR moved to a version another item already had, then merged: the close and the merge share an instant, and the PR\'s terminal says merged', JSON.stringify({ types: types(d1), t: d1.terminals }));

  // 4: the backfill's comparison is owed until GitHub's count has been read and matches
  const list = [mergedPr(9301, -100), mergedPr(9302, -50)];
  const bf = plan(H.emptyDoc(), { backfilling: true, scan: { ...scanOf(list), github_count: null } });
  ok(bf.doc.meta.backfill && bf.doc.meta.backfill.compare.match === null, 'second review 4: a backfill whose GitHub count was unread completes, its comparison pending (match null)');
  const later1 = plan(bf.doc, { at: at(15), recount: null });
  ok(later1.doc.meta.backfill.compare.match === null && !H.historyError({ write: 'written', backfill: { state: 'done', compare: later1.doc.meta.backfill.compare } }), 'second review 4: still pending while the count stays unread (not an ERROR)');
  const later2 = plan(bf.doc, { at: at(30), recount: 2 });
  ok(later2.doc.meta.backfill.compare.match === true && later2.doc.meta.backfill.compare.github_count === 2 && later2.doc.meta.backfill.compare.compared_at === at(30) && later2.events.length === 0, 'second review 4: a later tick reads the count and makes the comparison (no backfill re-run)');
  const bad = plan(bf.doc, { at: at(30), recount: 3 });
  ok(bad.doc.meta.backfill.compare.match === false && H.historyError({ write: 'not-needed', backfill: { state: 'done', compare: bad.doc.meta.backfill.compare } }), 'second review 4: a mismatch is an ERROR on every tick until it matches');
  ok(plan(bad.doc, { at: at(45), recount: 2 }).doc.meta.backfill.compare.match === true, 'second review 4: and stops when a later count matches');

  // 5: a catch-up disagreement is an ERROR only when the next tick disagrees again
  const base = plan(H.emptyDoc(), { backfilling: true, scan: scanOf(list) }).doc;
  const c1 = plan(base, { at: at(15), scan: { ...scanOf(list, at(-100), at(15)), github_count: 3 } });
  const s1 = { write: 'written', catch_up: { match: c1.compare.match, repeated: c1.catch_up_repeated } };
  ok(c1.compare.match === false && !c1.catch_up_repeated && !H.historyError(s1) && c1.doc.meta.catch_up_mismatch, 'second review 5: a first catch-up disagreement is an info line and a summary field');
  const c2 = plan(c1.doc, { at: at(30), scan: { ...scanOf(list, at(-100), at(30)), github_count: 3 } });
  ok(c2.catch_up_repeated && H.historyError({ write: 'written', catch_up: { match: false, repeated: true } }), 'second review 5: the same disagreement on the next tick is an ERROR');
  ok(!plan(c1.doc, { at: at(30), scan: scanOf(list, at(-100), at(30)) }).doc.meta.catch_up_mismatch, 'second review 5: a matching tick clears it');

  // 6: mirrors in the fair order; a PR with no record is marked mirrored
  const rcd = M.recordConfig(rules);
  const one = { ...cfg, max_mirrors_per_tick: 1 };
  const term = { type: 'merged', at: at(0), state: 'merged_unverified', by: 'renovate[bot]', item: 'i', event: 'e' };
  const bodies = { 9401: [{ id: 1, user: { login: rcd.author.login, type: rcd.author.type }, body: `${rcd.marker}\nbroken` }], 9402: [] };
  const runM = (cmd, argv) => { const m = argv.join(' ').match(/issues\/(\d+)\/comments/); return m ? JSON.stringify([bodies[m[1]] || []]) : null; };
  const owed = [{ pr: 9401, terminal: term, items: [] }, { pr: 9402, terminal: term, items: [] }];
  const m1 = H.terminalRecordItems(owed, [], rcd, at(0), runM, REPO, one, null, {});
  const m2 = H.terminalRecordItems(owed, [], rcd, at(15), runM, REPO, one, null, { 9401: at(0) });
  ok(m1.read.join() === '9401' && m1.skipped.length === 1 && m2.read.join() === '9402', 'second review 6: an unparseable record tried first does not hold the one mirror slot on the next tick', JSON.stringify({ m1: m1.read, m2: m2.read }));
  const ctxDoc = plan(withMarker(H.emptyDoc()), { records: [rec(9403)], openPrs: [9403] }).doc;
  const g = api(); g.objs['upgrade-ledger'] = { metadata: { name: 'upgrade-ledger', resourceVersion: '50' }, data: H.renderData(ctxDoc) };
  const id = itemOf(rec(9403)), evId = ctxDoc.events[0].id;
  const cm = H.commitMirrors({ ctx: { doc: ctxDoc, rv: '50' }, terminals: [{ pr: 9403, terminal: term, items: [[id, evId]] }], result: { no_record: [9403] }, halt: null, at: at(5), env: {}, apply: g.apply });
  ok(cm.written && JSON.parse(g.get().data.items)[id].mirrored === evId, 'second review 6: a PR with no lane record is marked mirrored (it owes nothing more)');
  const ctx2 = H.parseDoc(g.get());
  const cm2 = H.commitMirrors({ ctx: { doc: ctx2.doc, rv: ctx2.rv }, terminals: [{ pr: 9404, terminal: term, items: [] }], result: {}, halt: null, at: at(10), env: {}, read: [9404], apply: g.apply });
  ok(cm2.written && JSON.parse(g.get().data.meta).tried?.mirror?.[9404] === at(10), 'second review 6: a mirror tried and still owed keeps its time in meta.tried.mirror');

  // 7: the mirror halt names the key that holds it
  const w7 = M.writePrRecords({ items: [{ rec: { schema: 'haynes.upgrade-lane/v1', pr: 9501, terminal: term }, comments: [{ id: 5, author: rcd.author.login, author_type: rcd.author.type, body: M.renderRecord({ schema: 'haynes.upgrade-lane/v1', pr: 9501 }, rcd) }], edit_only: true }], cfg: rcd,
    run: (cmd, argv) => JSON.stringify({ id: 5, user: { login: rcd.author.login, type: rcd.author.type }, body: M.renderRecord({ schema: 'haynes.upgrade-lane/v1', pr: 9501 }, rcd) }), env: {}, live: true, haltKey: 'meta.mirror_halt in ConfigMap upgrade-ledger (the terminal mirrors only)',
    write: () => ({ ok: false, exit: 1, http: 403, call: 'gh api --method PATCH x', detail: 'HTTP 403' }) });
  ok(/until a person removes meta\.mirror_halt in ConfigMap upgrade-ledger/.test(w7.halt?.why || '') && !/retry_ledger\.halt/.test(w7.halt?.why || ''), 'second review 7: a mirror denial\'s halt names meta.mirror_halt, not retry_ledger.halt', w7.halt?.why);

  // 8: the marker is read before the history
  const order = [];
  const f8 = api();
  H.historyPhase({ rules, run: (cmd, argv) => { if (cmd === 'kubectl') order.push(argv[argv.indexOf('--field-selector') + 1]); return f8.run(cmd, argv); }, env: {}, repo: REPO, at: at(0), records: [], openPrs: [], verdict: null, write: true, apply: f8.apply });
  ok(order.join() === 'metadata.name=upgrade-ledger-marker,metadata.name=upgrade-ledger', 'second review 8: the marker is read before the history (a first writer writes the history, then the marker)', order.join());

  // 9: no marker without a history
  const f9 = api();
  const only = (m, mgr) => (mgr === H.MANAGER ? { ok: false, conflict: false, detail: 'refused by the API server (Forbidden)', raw: 'x' } : f9.apply(m, mgr));
  const t9 = H.historyPhase({ rules, run: f9.run, env: {}, repo: REPO, at: at(0), records: [rec(9601)], openPrs: [9601], verdict: null, write: true, apply: only });
  ok(t9.summary.write === 'failed' && !f9.objs['upgrade-ledger-marker'] && !f9.writes.includes(H.MARKER_MANAGER), 'second review 9: when only the history write fails, the marker is not written');
  const f9b = api();
  const t9b = H.historyPhase({ rules, run: (cmd, argv) => (cmd === 'gh' ? null : f9b.run(cmd, argv)), env: {}, repo: REPO, at: at(0), records: [], openPrs: [], verdict: null, write: true, apply: f9b.apply });
  ok(t9b.summary.write === 'not-needed' && f9b.writes.length === 0 && !f9b.objs['upgrade-ledger-marker'], 'second review 9: a tick that writes no history (nothing derived, the scan unread) writes no marker either', JSON.stringify({ w: t9b.summary.write, writes: f9b.writes }));

  // 10: a body edited to empty is the title route and never a move; the mapping row agrees with the code
  const e10 = plan(p1.doc, { records: [b, c], openPrs: [102, 103], at: at(15), scan: scanOf([{ ...mergedPr(101, 10), body: '', title: 'chore(deps): update something' }]) });
  ok(types(e10).join() === 'merged:needed>merged_unverified' && e10.events[0].item_id === itemOf(a) && !e10.notes.superseded.length, 'second review 10: a PR whose body was edited to empty merges on the item the lane saw (never a move)', JSON.stringify(types(e10)));
  const row = JSON.parse(readFileSync(join(RES, 'lane-v1-to-item-v1.json'), 'utf8')).fields.find(r => r.lane === 'terminal');
  const src = readFileSync(join(RES, 'upgrade-history.mjs'), 'utf8');
  ok(/type \(merged or closed\)/.test(row.note) && /const TERMINAL = \['merged', 'closed'\];/.test(src), 'second review 10: the terminal mapping row and the code agree: merged or closed');
}

// ── 11. the format round (the reader's points, decided in story 593) ──────────────────
{
  // point 6: the place in the id; a PR whose place changes while it is open (PR 4873 on
  // 2026-11-12: two traefik targets, then one after the other half merged as PR 4876)
  const two = rec(9701, { targets: ['kubernetes/main/apps/network/traefik', 'kubernetes/main/apps/network/traefik-internal'], files: ['kubernetes/main/apps/network/traefik/app/helmrelease.yaml', 'kubernetes/main/apps/network/traefik-internal/app/helmrelease.yaml'] });
  const oneT = rec(9701, { targets: ['kubernetes/main/apps/network/traefik'], files: ['kubernetes/main/apps/network/traefik/app/helmrelease.yaml'] });
  ok(itemOf(two) !== itemOf(oneT) && itemOf(rec(9701, { ...two, head: sha('9') })) === itemOf(two), 'point 6: the same update in other targets is another item; a rebase (same files) keeps it');
  const m0 = plan(withMarker(H.emptyDoc()), { records: [two], openPrs: [9701] });
  const m1 = plan(m0.doc, { records: [oneT], openPrs: [9701], at: at(15) });
  ok(types(m1).join() === 'closed:needed>closed,observed:new>needed' && m1.events[0].item_id === itemOf(two) && m1.events[0].actor.kind === 'renovate' && /the same update in another place/.test(m1.events[0].reason) && m1.events[1].item_id === itemOf(oneT),
    'point 6: a PR whose place changes while open closes the old item (replaced) and observes the new one (PR 4873)', JSON.stringify(types(m1)));
  const m2 = plan(m0.doc, { records: [], openPrs: [], at: at(15), scan: scanOf([{ ...mergedPr(9701, 10, { pkg: two.package }), files: oneT.files.map(path => ({ path })) }]) });
  ok(types(m2).join() === 'closed:needed>closed,merged:new>merged_unverified' && m2.events[1].item_id === itemOf(oneT), 'point 6: and when it merged in its new place before the lane saw it, the merge goes on the new place (the H1 path)');
  // the place is the changed paths: a rules change that remaps the targets (the same files)
  // is the same item, and writes nothing on the next tick (no 'replaced' that never happened)
  const remapped = rec(9701, { ...two, targets: ['kubernetes/main/apps/network/traefik-remapped'] });
  ok(itemOf(remapped) === itemOf(two) && plan(m0.doc, { records: [remapped], openPrs: [9701], at: at(15) }).events.length === 0, 'paths rule: a rules change that remaps the targets changes no id and writes no event');
  // a file list GitHub cut off: the lane (gatherFacts) and the backfill build the same id from
  // the same cut list, and the item and the summary say it was cut
  const hundred = Array.from({ length: 100 }, (_, i) => `kubernetes/main/apps/x/big/app/f${String(i).padStart(3, '0')}.yaml`);
  const laneCut = rec(9702, { files: [...hundred, RF.CUT_FILES], targets: ['kubernetes/main/apps/x/big'] });
  const backCut = { ...mergedPr(9702, 10, { pkg: laneCut.package }), files: hundred.map(path => ({ path })), changedFiles: 120 };
  const backFull = { ...backCut, changedFiles: 100 };
  ok(H.laneItem(backCut, REPO, rules, at(0)).id === itemOf(laneCut) && H.laneItem(backCut, REPO, rules, at(0)).desc.files_cut === true && H.laneItem(backFull, REPO, rules, at(0)).id !== itemOf(laneCut) && !H.laneItem(backFull, REPO, rules, at(0)).desc.files_cut,
    'cut list: 100 of 120 files: the backfill appends the lane\'s marker, gets the lane\'s id, and marks the item files_cut; a complete list of 100 does not');
  // the marker the lane's gatherFacts writes is the format's CUT_FILES
  const view = { number: 9702, headRefOid: sha('1'), headRefName: 'renovate/x', title: 't', author: { login: 'app/renovate' }, isDraft: false, labels: [], files: hundred.map(path => ({ path })), commits: [], statusCheckRollup: [], body: '', changedFiles: 120 };
  const gf = L.gatherFacts(REPO, 9702, (cmd, argv) => (argv[0] === 'pr' ? JSON.stringify(view) : '[[]]'));
  ok(gf.files.length === 101 && gf.files.at(-1) === RF.CUT_FILES, 'cut list: the lane\'s gatherFacts ends a cut list with the format\'s CUT_FILES marker, the same one the backfill adds');
  const pc = plan(H.emptyDoc(), { backfilling: true, scan: scanOf([backCut]) });
  const po = plan(withMarker(H.emptyDoc()), { records: [laneCut], openPrs: [9702] });
  ok(pc.cut_file_items === 1 && po.cut_file_items === 1 && /1 with a file list GitHub cut off/.test(H.historyLine({ write: 'written', events: 1, total: { events: 1, items: 1, title_items: 0, cut_file_items: 1, bytes: 1, cap: 2 } })),
    'cut list: the summary counts the items built on a cut list (backfilled or seen open), and the log line says so');
  // point 6: the memory across places, from the history's own items
  const s = rec(9800, { app: 1 });
  let d = plan(withMarker(H.emptyDoc()), { records: [s], openPrs: [9800], at: at(-60) }).doc;
  const id = itemOf(s);
  const ev = (type, from, to, min, extra) => H.makeEvent({ item_id: id, type, from, to, at: at(min), actor: { kind: 'lane', login: null }, recorded_by: 'lane', attempt: 1, ...extra });
  d = { ...d, events: [...d.events, ev('merge-attempted', 'needed', 'in_progress', -59, { lane: 'A-night', carrier: { repo: REPO, pr: 9800 } }), ev('merged', 'in_progress', 'merged_unverified', -58, { carrier: { repo: REPO, pr: 9800 }, evidence: { merge_commit: sha('5') } }),
    ev('verification-failed', 'merged_unverified', 'failed', -50, { class: 'not-ready', reason: 'x' }), ev('revert-opened', 'failed', 'reverting', -49, { evidence: { revert_pr: 9001 } }), ev('revert-merged', 'reverting', 'reverted_unverified', -40, { evidence: { revert_commit: sha('6') } }), ev('recovery-verified', 'reverted_unverified', 'reverted', -30, {})] };
  const elsewhere = rec(9801, { pkg: s.package, app: 2 });
  ok(itemOf(elsewhere) !== id && RF.rememberedReverted(H.historyItems(d), s.package, s.to).map(x => x.id).join() === id && RF.rememberedReverted(H.historyItems(d), s.package, '9.9.9').length === 0,
    'point 6: the history\'s items answer rememberedReverted: the version reverted in app1 is found when asked from app2 (another item)');
  // point 5: free text shaped like a secret is replaced before it is stored, and named
  const fake = ['gh', 'p_', 'A'.repeat(36)].join('');
  const x = rec(9901);
  const xb = plan(withMarker(H.emptyDoc()), { records: [x], openPrs: [9901] });
  const live = { pr: 9901, head: x.head, class: 'cluster-change', chosen: true, mode: 'live', verdict: 'merge-failed', pr_refusals: [], lane_refusals: [], merge_call: `gh pr merge 9901 failed: token ${fake}` };
  const xr = plan(xb.doc, { records: [{ ...x, class: 'immediate-cluster-change', reason: 'reached upgrade-svc.default.svc.cluster.local' }], openPrs: [9901], at: at(15), verdict: live });
  const stored = H.renderData(xr.doc).events;
  ok(!stored.includes(fake) && !stored.includes('svc.cluster.local') && stored.includes(H.REDACTED) && xr.notes.redacted.some(r => r.field === 'evidence.merge_call' && r.shape === 'a GitHub token') && xr.notes.redacted.some(r => r.field === 'reason' && r.shape === 'an in-cluster name'),
    'point 5: a token in a merge call and an in-cluster name in a reason are replaced by the fixed placeholder before they are stored, and named by field', JSON.stringify(xr.notes.redacted));
  ok(H.historyError({ write: 'written', redacted: xr.notes.redacted }) && /REDACTED .*evidence\.merge_call of .* \(shaped like a GitHub token\)/.test(H.historyLine({ write: 'written', events: 1, redacted: xr.notes.redacted })) && !H.historyLine({ write: 'written', events: 1, redacted: xr.notes.redacted }).includes(fake), 'point 5: the tick logs an ERROR naming the field, never the text');
  // point 3: a merge step's long refusals are cut to the bounds with a visible mark, not refused
  const many = { ...live, verdict: 'refused', merge_call: 'not made', pr_refusals: Array.from({ length: 130 }, (_, i) => `refusal ${i} ${'r'.repeat(1100)}`) };
  const xc = plan(xb.doc, { records: [x], openPrs: [9901], at: at(30), verdict: many });
  const refused = xc.events.find(e => e.type === 'merge-refused');
  ok(refused && refused.evidence.pr_refusals.length === 100 && refused.evidence.pr_refusals[99] === '… (+31 more)' && refused.evidence.pr_refusals[0].length === 1000 && refused.reason.length <= 1000 && RF.checkEvent(refused).length === 0 && xc.notes.cut.length > 0,
    'point 3: a list of 130 long refusals is stored as 100, each cut to 1000 characters with a mark, and the cut is named');
  // point 3: the attempt count survives rotation, so numbering carries on after attempts were dropped
  const sk = H.skeleton('renovate-pr:' + 'c'.repeat(24), { state: 'needed', at: at(0), attempts: 25, by: 'lane', result: 'merge-refused' });
  ok(RF.attemptCount(sk) === 25 && sk.attempts.length === 1 && sk.attempts_dropped === 24, 'point 3: an item\'s base keeps its attempt count (25) while holding one attempt, so the next is 26');
}

console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
