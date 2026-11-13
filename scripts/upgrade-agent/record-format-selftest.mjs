#!/usr/bin/env node
// Self-test for the update item and event records (OP592): haynes.upgrade-item/v1,
// haynes.upgrade-event/v1, their state machine and the mapping from the lane's
// haynes.upgrade-lane/v1 record.
//
// WHY THIS EXISTS
// ---------------
// The lane's merge step (story 752), the verifier (553), the revert and its drill (753)
// and the history of every attempt (593) all write these records. A record the format
// does not allow, or a transition the state machine does not have, must be refused
// before any of them lands in the history. This test pins:
//   1. the hand-written JSON Schema subset in schema/record-format.mjs, keyword by
//      keyword, and that both schemas use no keyword it does not implement;
//   2. the state machine: every state reachable, no trap, the events the later stories
//      need, and the safety properties (the lane merges only from needed and only in
//      lane A-night; a reverted version stays remembered unless its attempt was a drill);
//   3. every fixture in fixtures/upgrade-records/: valid ones pass, invalid ones are
//      refused for the stated reason, one valid item per source the lane writes;
//   4. the mapping from haynes.upgrade-lane/v1: every field the lane writes today (the
//      classifier's output on the 2026-11-09 queue, the records the real tick wrote in
//      fixtures/lane-tick-records-20261111.json, and the fields run-night.mjs sets, read
//      from its source) has a row in schema/lane-v1-to-item-v1.json, every checks field
//      is in the item schema, every copy row holds, and every converted record is a valid
//      item. A new lane field fails here until its row says where it goes;
//   5. the merge step's verdicts (story 752) to events: which verdicts write events, that
//      those events replay legally, and that every verdict and lock phase night-merge.mjs
//      can produce has a row.
//
// Usage: node scripts/upgrade-agent/record-format-selftest.mjs
// Requires: node 24. No network, no cluster access, no package install.
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../..');
const RES = join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/resources');
const FIX = join(ROOT, 'scripts/upgrade-agent/fixtures/upgrade-records');
const F = await import(join(ROOT, 'scripts/upgrade-agent/schema/record-format.mjs'));
const tickFx = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/lane-tick-records-20261111.json'), 'utf8'));
const L = await import(join(RES, 'lane-classify.mjs'));
const rules = L.loadRules(join(RES, 'upgrade-rules.json'));
const holdRules = L.parseJson5(readFileSync(join(ROOT, '.renovate/holds.json5'), 'utf8')).packageRules;
const queue = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/lane-queue-20261109.json'), 'utf8'));
const NOW = Date.parse(queue.now);
const REPO = 'thaynes43/haynes-ops';

let fails = 0, passes = 0;
const ok = (cond, name, detail = '') => { if (cond) passes++; else { fails++; console.log(`FAIL ${name}${detail ? `\n     ${detail}` : ''}`); } };
const clone = o => structuredClone(o);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const sameSet = (a, b) => same([...new Set(a)].sort(), [...new Set(b)].sort());
const get = (o, path) => path.split('.').reduce((a, k) => (a && typeof a === 'object' ? a[k] : undefined), o);
const fact = n => { const p = queue.prs.find(x => x.facts.number === n); if (!p) throw new Error(`no queue fixture #${n}`); return clone(p.facts); };
const M = F.machine;
const NON_RESERVED_STATES = Object.keys(M.states).filter(s => !M.reserved.states.includes(s));
const NON_RESERVED_EVENTS = Object.keys(M.events).filter(e => !M.reserved.events.includes(e));

// ── 1. the JSON Schema subset ────────────────────────────────────────────────
{
  for (const [k, s] of Object.entries(F.schemas)) ok(F.schemaProblems(s).length === 0, `schema ${s.$id}: only implemented keywords, every $ref resolves`, F.schemaProblems(s).join('; '));
  const s = {
    type: 'object', additionalProperties: false, required: ['a'],
    properties: {
      a: { type: 'integer', minimum: 1, maximum: 9 }, b: { type: ['string', 'null'], pattern: '^x', minLength: 2 }, c: { enum: ['p', 'q'] },
      d: { $ref: '#/$defs/t' }, e: { anyOf: [{ type: 'null' }, { const: 3 }] }, f: { type: 'array', items: { type: 'string' }, minItems: 1 },
      g: { type: 'object', maxProperties: 0 }, h: { type: 'number' }, constructor: { type: 'string' },
    },
    $defs: { t: { type: 'string', format: 'date-time', pattern: 'Z$' } },
    if: { required: ['c'], properties: { c: { const: 'p' } } }, then: { required: ['b'] },
  };
  const cases = [
    [{ a: 1 }, null], [{}, 'missing required a'], [{ a: 0 }, 'below 1'], [{ a: 10 }, 'above 9'], [{ a: 1.5 }, 'is number, expected integer'],
    [{ a: '1' }, 'is string, expected integer'], [{ a: 1, z: 1 }, '/z: not allowed'], [{ a: 1, b: 'yy' }, 'does not match ^x'],
    [{ a: 1, b: 'x' }, 'shorter than 2'], [{ a: 1, b: null }, null], [{ a: 1, c: 'r' }, 'is not one of'], [{ a: 1, c: 'p' }, 'missing required b'],
    [{ a: 1, c: 'q' }, null], [{ a: 1, d: '2026-11-11T00:00:00Z' }, null], [{ a: 1, d: '2026-11-11T00:00:00+01:00' }, 'does not match Z$'],
    [{ a: 1, d: 'yesterday' }, 'not an RFC 3339 date-time'], [{ a: 1, d: '2026-13-45T00:00:00Z' }, 'not an RFC 3339 date-time'],
    [{ a: 1, e: null }, null], [{ a: 1, e: 3 }, null], [{ a: 1, e: 4 }, 'matches none of anyOf'], [{ a: 1, f: [] }, 'fewer than 1 items'],
    [{ a: 1, f: ['x', 2] }, '/f/1: is integer, expected string'], [{ a: 1, g: {} }, null], [{ a: 1, g: { x: 1 } }, 'more than 0 properties'],
    [{ a: 1, h: 2 }, null], [{ a: 1, h: 2.5 }, null], [{ a: 1, constructor: 'x' }, null], [[1], 'is array, expected object'], [null, 'is null, expected object'],
  ];
  for (const [v, want] of cases) {
    const errs = F.validate(s, v);
    ok(want === null ? errs.length === 0 : errs.some(e => e.includes(want)), `validator: ${JSON.stringify(v)} -> ${want ?? 'valid'}`, errs.join('; '));
  }
  ok(F.validate({ type: 'object', required: ['constructor'] }, {}).some(e => e.includes('missing required constructor')), 'validator: required reads own properties only');
  ok(F.schemaProblems({ type: 'array', uniqueItems: true }).some(e => e.includes('keyword uniqueItems is not implemented')), 'validator: an unimplemented keyword is refused, not skipped');
  ok(F.schemaProblems({ type: 'string', format: 'email' }).some(e => e.includes('format email is not implemented')), 'validator: an unimplemented format is refused');
  ok(F.schemaProblems({ $ref: '#/$defs/nope' }).some(e => e.includes('does not resolve')), 'validator: an unresolved $ref is refused');
  ok(F.validate({ $ref: 'haynes.upgrade-item/v1#/$defs/sha' }, 'f'.repeat(40)).length === 0 && F.validate({ $ref: 'haynes.upgrade-item/v1#/$defs/sha' }, 'xyz').length === 1, 'validator: a $ref into another loaded schema by $id');
}

// ── 2. the state machine ────────────────────────────────────────────────────
{
  const itemS = F.schemas.item.$defs, evS = F.schemas.event.properties;
  ok(sameSet(Object.keys(M.states), itemS.state.enum), 'machine: its states are exactly the item schema state enum');
  ok(sameSet(Object.keys(M.events), evS.type.enum), 'machine: its events are exactly the event schema type enum');
  for (const s of M.reserved.states) ok(M.states[s]?.phase === 'reserved', `machine: reserved state ${s} has phase reserved`);
  for (const e of M.reserved.events) ok(Boolean(M.events[e]), `machine: reserved event ${e} exists`);
  ok(sameSet(Object.keys(M.states).filter(s => M.states[s].phase === 'reserved'), M.reserved.states), 'machine: phase reserved and reserved.states agree');
  const actors = itemS.actor.properties.kind.enum, lanes = itemS.lane.enum;
  const results = itemS.attempt.properties.result.enum, recov = itemS.attempt.properties.recovery.enum;
  const evFields = new Set([...Object.keys(evS), ...Object.keys(evS.evidence.properties).map(k => `evidence.${k}`)]);
  for (const [name, def] of Object.entries(M.events)) {
    const reservedEv = M.reserved.events.includes(name);
    ok(def.edges.length > 0, `machine: ${name} has at least one edge`);
    for (const r of def.requires) ok(r === 'drill' || evFields.has(r), `machine: ${name} requires ${r}, a field of the event schema`);
    for (const [i, e] of def.edges.entries()) {
      const tag = `machine: ${name} edge ${i}`;
      ok(e.from.every(s => s === null || M.states[s]) && e.to.every(s => M.states[s]), `${tag}: states exist`);
      ok(e.actors.length > 0 && e.actors.every(a => actors.includes(a)), `${tag}: actors are actor kinds`);
      ok(!e.lanes || e.lanes.every(l => lanes.includes(l)), `${tag}: lanes are lanes`);
      ok(e.attempt_result === undefined || results.includes(e.attempt_result), `${tag}: attempt_result is an attempt result`);
      ok(e.recovery === undefined || recov.includes(e.recovery), `${tag}: recovery is a recovery value`);
      if (!reservedEv) ok([...e.from, ...e.to].every(s => !M.reserved.states.includes(s)), `${tag}: touches no reserved state`);
      if (e.starts_attempt) ok(Boolean(e.attempt_result) && def.requires.includes('attempt'), `${tag}: an edge that opens an attempt sets its result and requires attempt`);
    }
  }
  // reachability from a new item, over the edges piece 1 may use
  const edges = NON_RESERVED_EVENTS.flatMap(n => M.events[n].edges.map(e => ({ ...e, name: n })));
  const seen = new Set();
  let frontier = [null];
  while (frontier.length) {
    const next = [];
    for (const s of frontier) for (const e of edges) if (e.from.includes(s)) for (const t of e.to) if (!seen.has(t)) { seen.add(t); next.push(t); }
    frontier = next;
  }
  ok(sameSet([...seen], NON_RESERVED_STATES), 'machine: every non-reserved state is reachable from a new item', `reached ${[...seen].join(', ')}`);
  for (const s of NON_RESERVED_STATES) ok(edges.some(e => e.from.includes(s)), `machine: state ${s} has a way out`);
  for (const e of ['observed', 'merge-attempted', 'merge-refused', 'merge-failed', 'merge-unknown', 'merged', 'awaiting-pin', 'verified', 'verification-failed', 'revert-opened', 'revert-merged', 'recovery-verified', 'drill-requested', 'drill-cleared'])
    ok(NON_RESERVED_EVENTS.includes(e), `machine: event ${e} exists for the later stories`);
  for (const story of ['752', '553', '753', '593']) ok(NON_RESERVED_EVENTS.some(e => M.events[e].used_by.includes(story)), `machine: story ${story} has events to write`);
  // safety properties
  const into = st => edges.filter(e => e.to.includes(st));
  ok(into('in_progress').every(e => same(e.from, ['needed'])), 'safety: an attempt starts only from needed');
  ok(into('in_progress').filter(e => e.actors.includes('lane')).every(e => same(e.lanes, ['A-night'])), 'safety: the lane starts a merge only for lane A-night');
  const laneMerged = edges.filter(e => e.name === 'merged' && e.actors.includes('lane'));
  ok(laneMerged.length === 1 && sameSet(laneMerged[0].from, ['in_progress', 'merge_unknown']), 'safety: the lane records a merge only after its merge step started (in_progress, or merge_unknown read later)', JSON.stringify(laneMerged.map(e => e.from)));
  ok(sameSet(into('merge_unknown').map(e => e.name), ['merge-unknown']) && into('merge_unknown').every(e => same(e.from, ['in_progress'])), 'safety: merge_unknown is reached only from a merge call in progress');
  ok(sameSet(edges.filter(e => e.from.includes('merge_unknown')).map(e => e.name), ['merged', 'merge-failed', 'escalated']), 'safety: merge_unknown ends only when the PR is read (merged, merge-failed) or a person takes it (escalated)');
  // lock phases (story 752): every phase night-merge.mjs writes goes with item states
  const lp = M.lock_phases;
  const phases = Object.keys(lp).filter(k => k !== 'doc');
  for (const ph of phases) ok(Array.isArray(lp[ph]) && lp[ph].length > 0 && lp[ph].every(x => NON_RESERVED_STATES.includes(x)), `machine: lock phase ${ph} goes with item states`);
  ok(sameSet(phases.flatMap(ph => lp[ph]), NON_RESERVED_STATES), 'machine: every item state goes with a lock phase');
  ok(['in_progress', 'merge_unknown'].every(x => !lp.released.includes(x)), 'machine: a merge in flight is never under a released lock');
  const mergeSrc = readFileSync(join(RES, 'night-merge.mjs'), 'utf8');
  const written = new Set([...mergeSrc.matchAll(/\bphase(?:\s*:\s*|\s*=\s*)'([a-z-]+)'/g)].map(m => m[1]));
  for (const m of mergeSrc.matchAll(/const phase = [^;]*\?\s*'([a-z-]+)'\s*:\s*'([a-z-]+)'/g)) { written.add(m[1]); written.add(m[2]); }
  ok(written.size >= 5, 'machine: lock phases found in night-merge.mjs', [...written].join(', '));
  for (const ph of written) ok(phases.includes(ph), `machine: lock phase ${ph}, written by night-merge.mjs, is in lock_phases`);
  const outOfReverted = edges.filter(e => e.from.includes('reverted')).map(e => `${e.name}:${[...e.actors].sort().join('+')}`).sort();
  ok(same(outOfReverted, ['drill-cleared:hand+lane', 'merged:hand+person+renovate+shepherd+unknown', 'resolved:person']), 'safety: three edges leave reverted: drill-cleared (a drill), resolved by a person, and a merge by anyone but the lane (it records that the version runs again)', outOfReverted.join(', '));
  ok(edges.filter(e => e.from.includes('reverted') && e.actors.includes('lane')).map(e => e.name).join() === 'drill-cleared', 'safety: the lane leaves reverted only by drill-cleared, so it never merges a remembered version');
  ok(M.events.closed.edges[0].actors.includes('shepherd') && M.events.closed.edges[0].actors.includes('unknown'), 'machine: closed takes the shepherd, and unknown for a closer that cannot be established');
  ok(edges.filter(e => e.actors.includes('unknown')).every(e => ['merged', 'closed'].includes(e.name)), 'machine: the actor unknown appears only on merged by others and closed, never where the lane acts');
  ok(M.events['drill-cleared'].requires.includes('drill'), 'safety: drill-cleared requires drill true');
  ok(edges.filter(e => e.from.includes('held')).every(e => e.actors.every(a => a !== 'lane') || e.name === 'observed' || e.name === 'closed'), 'safety: the lane takes no action on a held item');
}

// ── 3. the fixtures ─────────────────────────────────────────────────────────
const fixtures = readdirSync(FIX).filter(f => f.endsWith('.json')).sort().map(f => ({ file: f, ...JSON.parse(readFileSync(join(FIX, f), 'utf8')) }));
const laneRecordOf = (n, mutate) => {
  const facts = fact(n);
  if (mutate) mutate(facts);
  const r = L.classify(facts, rules, { now: NOW, holdRules });
  Object.assign(r, { updated: new Date(NOW).toISOString(), preapproval: {}, schedule: {}, last_attempt: {} });
  if (r.lane === 'LANE-A') {
    Object.assign(r, { backup: { rule: rules.night.backup_rule.text, targets: [], stateful: false }, gates: { namespaces: [], blockers: [], alerts_not_blocking: { unrelated: [], cluster: [] }, would_be_eligible: true } });
    r.checks.main_newer = [];
  }
  return r;
};
{
  const eventSizes = [];
  const eventTypes = new Set();
  for (const fx of fixtures) {
    let errs;
    if (fx.kind === 'item') errs = F.checkItem(fx.record);
    else if (fx.kind === 'event') errs = F.checkEvent(fx.record);
    else if (fx.kind === 'sequence') {
      const r = F.replay(fx.record.item, fx.record.events);
      errs = r.errors;
      if (fx.expect === 'valid') ok(r.item.state === fx.final_state, `${fx.file}: ends in ${fx.final_state}`, `ends in ${r.item.state}`);
    } else { ok(false, `${fx.file}: kind ${fx.kind} is item, event or sequence`); continue; }
    if (fx.expect === 'valid') {
      ok(errs.length === 0, `${fx.file}: valid`, errs.join('\n     '));
      if (fx.kind === 'sequence') for (const e of fx.record.events) { eventTypes.add(e.type); eventSizes.push(JSON.stringify(e).length); }
      if (fx.kind === 'event') eventTypes.add(fx.record.type);
    } else {
      ok(Boolean(fx.expect?.refused) && errs.some(e => e.includes(fx.expect.refused)), `${fx.file}: refused for "${fx.expect?.refused}"`, errs.length ? errs.join('\n     ') : 'accepted');
    }
    if (fx.from_pr) {
      const it = F.fromLaneRecord(laneRecordOf(fx.from_pr), { repo: REPO, first_seen: fx.record.first_seen });
      for (const k of ['id', 'source', 'lane', 'state']) ok(it[k] === fx.record[k], `${fx.file}: ${k} matches the lane record of PR ${fx.from_pr} today`, `${it[k]} vs ${fx.record[k]}`);
    }
  }
  const valid = fixtures.filter(f => f.expect === 'valid');
  for (const src of ['renovate-pr', 'image-pin', 'own-image']) ok(valid.some(f => f.kind === 'item' && f.record.source === src), `fixtures: a valid item for source ${src}, which the lane writes`);
  for (const e of NON_RESERVED_EVENTS) ok(eventTypes.has(e), `fixtures: event ${e} appears in a valid fixture`);
  const refused = fixtures.filter(f => f.expect !== 'valid').map(f => f.expect.refused);
  for (const [what, needle] of [['unknown source', '/source:'], ['illegal transition', 'illegal transition'], ['non-empty approval', '/approval:']])
    ok(refused.some(r => r.includes(needle)), `fixtures: an invalid fixture for ${what}`);
  const avg = Math.round(eventSizes.reduce((a, b) => a + b, 0) / eventSizes.length);
  console.log(`fixtures: ${fixtures.length} files (${valid.length} valid, ${fixtures.length - valid.length} invalid); ${eventSizes.length} valid events, ${avg} bytes on average, ${Math.max(...eventSizes)} at most (compact JSON)`);
}

// ── 4. the mapping from haynes.upgrade-lane/v1 ────────────────────────────────
{
  const rows = F.laneMapping.fields;
  const rowOf = new Map(rows.map(r => [r.lane, r]));
  ok(rowOf.size === rows.length, 'mapping: one row per lane field');
  ok(F.laneMapping.from === F.LANE_SCHEMA && F.laneMapping.to === F.ITEM_SCHEMA, 'mapping: from lane v1 to item v1');
  // what the lane writes today: the classifier's keys, plus every r.<field> = run-night.mjs sets
  const records = queue.prs.map(p => laneRecordOf(p.facts.number));
  // run-night.mjs sets fields on a record as r.<f> = or subjectRec.<f> = (top level) and
  // r.checks.<f> = (inside the checks object, which the item schema types field by field)
  const nightSrc = readFileSync(join(RES, 'run-night.mjs'), 'utf8');
  const sets = [...nightSrc.matchAll(/\b(?:r|subjectRec)\.([a-z_]+)(?:\.([a-z_]+))?\s*=(?!=)/g)].map(m => [m[1], m[2]]);
  const nightFields = [...new Set(sets.filter(([, b]) => !b).map(([a]) => a))];
  const nightChecks = [...new Set(sets.filter(([a, b]) => a === 'checks' && b).map(([, b]) => b))];
  ok(['backup', 'gates', 'updated', 'preapproval', 'schedule', 'last_attempt'].every(f => nightFields.includes(f)), 'mapping: run-night.mjs fields found in its source', nightFields.join(', '));
  ok(nightChecks.includes('main_newer'), 'mapping: run-night.mjs checks fields found in its source', nightChecks.join(', '));
  const tickRecords = tickFx.records.map(x => x.record);
  const written = new Set([...records.flatMap(r => Object.keys(r)), ...tickRecords.flatMap(r => Object.keys(r)), ...nightFields]);
  const checkKeys = new Set([...records, ...tickRecords].flatMap(r => Object.keys(r.checks || {})).concat(nightChecks));
  const schemaChecks = Object.keys(F.schemas.item.properties.lane_detail.properties.checks.properties);
  for (const k of checkKeys) ok(schemaChecks.includes(k), `mapping: lane checks field ${k} is in the item schema (lane_detail.checks)`);
  for (const f of written) ok(rowOf.has(f), `mapping: lane field ${f} has a row`, 'add a row to scripts/upgrade-agent/schema/lane-v1-to-item-v1.json saying where it goes');
  for (const r of rows) ok(written.has(r.lane), `mapping: row ${r.lane} names a field the lane writes`);
  for (const r of rows) {
    ok(['copy', 'map', 'reserved', 'events'].includes(r.how), `mapping: row ${r.lane} how is known`);
    ok(r.how === 'copy' || Boolean(r.note), `mapping: row ${r.lane} (${r.how}) states its rule`);
    const deref = n => (n?.$ref?.startsWith('#/$defs/') ? F.schemas.item.$defs[n.$ref.slice(8)] : n);
    let node = F.schemas.item;
    for (const k of r.item.split('.')) node = deref(node)?.properties?.[k];
    ok(node !== undefined, `mapping: row ${r.lane} names item path ${r.item} in the item schema`);
  }
  // every record converts to a valid item, and every copy row holds
  const tally = {};
  for (const rec of [...records, ...tickRecords]) {
    const it = F.fromLaneRecord(rec, { repo: REPO });
    const errs = F.checkItem(it);
    ok(errs.length === 0, `mapping: PR ${rec.pr} converts to a valid item`, errs.join('; '));
    for (const r of rows.filter(x => x.how === 'copy')) ok(same(get(it, r.item), rec[r.lane] ?? null), `mapping: PR ${rec.pr} ${r.lane} copied to ${r.item}`, `${JSON.stringify(get(it, r.item))} vs ${JSON.stringify(rec[r.lane])}`);
    const k = `${it.source}/${it.lane}/${it.state}`;
    tally[k] = (tally[k] || 0) + 1;
  }
  console.log(`mapping: ${records.length} queue PRs and ${tickRecords.length} tick records as source/lane/state: ` + Object.entries(tally).sort().map(([k, v]) => `${k} ${v}`).join(', '));
  // the map rows, case by case
  const conv = (n, mutate) => F.fromLaneRecord(laneRecordOf(n, mutate), { repo: REPO });
  const expectItem = (name, it, want) => ok(Object.entries(want).every(([k, v]) => get(it, k) === v), name, JSON.stringify(Object.fromEntries(Object.keys(want).map(k => [k, get(it, k)]))));
  expectItem('map: LANE-A cluster change is A-night, needed, renovate-pr', conv(4477), { lane: 'A-night', state: 'needed', source: 'renovate-pr', 'installed.kind': 'semver' });
  expectItem('map: a digest-only update is image-pin', conv(4333), { source: 'image-pin', 'available.kind': 'digest' });
  expectItem('map: an update inside one of our own images is own-image', conv(4662), { source: 'own-image', lane: 'A-night', 'installed.kind': 'semver' });
  expectItem('map: own-image wins over a digest-only update', conv(4334), { source: 'own-image' });
  expectItem('map: a hold is held, lane B-owner', conv(3915), { state: 'held', lane: 'B-owner', class: 'hold' });
  expectItem('map: a hold finding is held even when the top class is major', conv(4376), { state: 'held', lane: 'B-owner', class: 'major' });
  expectItem('map: EXCLUDED is excluded and needed', conv(4384), { lane: 'excluded', state: 'needed' });
  expectItem('map: LANE-B-AGENT is B-agent', conv(4708), { lane: 'B-agent', state: 'needed' });
  const failing = conv(4477, f => { for (const c of f.checks) c.conclusion = 'FAILURE'; });
  expectItem('map: BLOCKED by a failing check is blocked in lane A-night', failing, { lane: 'A-night', state: 'blocked', class: 'check-failing' });
  ok(failing.id === conv(4477).id, 'map: the item id does not change with the lane verdict');
  ok(conv(4477, f => { f.head_sha = 'f'.repeat(40); }).id === conv(4477).id, 'map: a rebase (new head) keeps the item id: the item is the update, not the carrier');
  ok(conv(4477, f => { f.packages[0].to = '1.37.3'; }).id !== conv(4477).id, 'map: a newer available version is a new item');
  // run-night.mjs's fallback record when the facts could not be read
  const fallback = { schema: 'haynes.upgrade-lane/v1', pr: 4999, lane: 'BLOCKED', class: 'facts-incomplete', reason: 'read failed: gh pr view exited 1', update_type: 'unknown', rules: [], checks: {}, targets: [], updated: '2026-11-10T10:45:00.000Z', preapproval: {}, schedule: {}, last_attempt: {} };
  const fb = F.fromLaneRecord(fallback, { repo: REPO });
  ok(F.checkItem(fb).length === 0, 'map: the read-failed record converts to a valid item', F.checkItem(fb).join('; '));
  expectItem('map: a read-failed record is blocked, lane unknown, coverage partial, named after its PR', fb, { state: 'blocked', lane: 'unknown', 'coverage.status': 'partial', 'item.name': 'thaynes43/haynes-ops#4999', 'available.kind': 'unknown' });
  const pre = F.fromLaneRecord({ ...laneRecordOf(4477), preapproval: { by: 'owner' } }, { repo: REPO });
  ok(F.checkItem(pre).some(e => e.startsWith('/approval:')), 'map: a lane record with a non-empty preapproval gives an item the validator refuses');
  const subj = tickRecords.find(r => r.last_attempt?.verdict === 'merged');
  const subjItem = F.fromLaneRecord(subj, { repo: REPO });
  ok(same(subjItem.lane_detail.last_attempt, subj.last_attempt) && subjItem.attempts.length === 0 && subjItem.state === 'needed', 'map: a merge-step verdict is copied to lane_detail.last_attempt; attempts and state come from events and the lane verdict');
  for (const [v, k] of [['1.37.2', 'semver'], ['v9.18.0-ls431', 'semver'], ['==5.0.0.93', 'semver'], ['24-slim', 'semver'], ['a0f2e26', 'digest'], ['sha256:' + 'ab'.repeat(32), 'digest'], ['latest', 'other'], [null, 'unknown']])
    ok(F.versionKind(v) === k, `map: version ${v} is ${k}`, F.versionKind(v));
}

// ── 5. merge-step verdicts to events (story 752) ─────────────────────────────
{
  const N = await import(join(RES, 'night-merge.mjs'));
  const table = Object.keys(F.laneMapping.merge_step_verdicts.verdicts);
  const outcomes = [N.mergeOutcome({ exit: 0 }, { merged: true }), N.mergeOutcome({ exit: 1 }, { state: 'OPEN' }), N.mergeOutcome({ exit: null }, null)];
  for (const v of new Set([...outcomes, ...tickFx.merge_steps.map(x => x.merge_step.verdict)])) ok(table.some(k => k === v || k.startsWith(`${v} (`)), `verdicts: ${v} is in merge_step_verdicts`);
  const tickRecords = tickFx.records.map(x => x.record);
  const subject = tickRecords.find(r => r.last_attempt?.verdict === 'merged');
  const base ={ ...F.fromLaneRecord(subject, { repo: REPO }) };
  const want = { 'dryrun/refused': [], 'dryrun/would-merge': [], 'none/not-run': [], 'live/merged': ['merged_unverified', 'merged'], 'live/merge-failed': ['needed', 'merge-failed'], 'live/merge-unknown': ['merge_unknown', 'merge-unknown'] };
  for (const { at, merge_step: v } of tickFx.merge_steps) {
    const key = `${v.mode || 'none'}/${v.verdict}`;
    const lane = (v.lane_refusals || []).length > 0, prOnly = !lane && (v.pr_refusals || []).length > 0;
    const evs = F.eventsFromMergeStep({ ...base, carrier: { ...base.carrier, pr: v.pr ?? base.carrier.pr } }, v, { repo: REPO, at });
    const tag = `events: ${key}${v.verdict === 'refused' ? (lane ? ' with lane refusals' : ' with PR refusals only') : ''}`;
    if (v.mode !== 'live' || lane) { ok(evs.length === 0, `${tag} writes no event`, evs.map(e => e.type).join(', ')); continue; }
    const r = F.replay({ ...base, carrier: { ...base.carrier, pr: v.pr } }, evs);
    ok(r.errors.length === 0, `${tag} replays legally from needed`, r.errors.join('; '));
    ok(evs[0].type === 'merge-attempted' && evs.every(e => e.attempt === 1 && e.actor.kind === 'lane'), `${tag} opens attempt 1 with merge-attempted`);
    if (prOnly) ok(evs.length === 2 && evs[1].type === 'merge-refused' && r.item.state === 'needed' && same(evs[1].evidence.pr_refusals, v.pr_refusals), `${tag}: merge-refused with the refusals in evidence`, JSON.stringify(evs[1]));
    else ok(r.item.state === want[key][0] && evs[1].type === want[key][1] && evs[1].evidence.merge_call === v.merge_call, `${tag}: ${want[key][1]}, item ${want[key][0]}`, `${evs[1]?.type} ${r.item.state}`);
    if (v.verdict === 'merged') ok(evs[1].evidence.merge_commit === v.merged.merge_commit && evs[1].at === v.merged.merged_at.replace('Z', '.000Z'), `${tag}: the merge commit and time from the verdict`);
    ok(same(evs.map(e => e.id), F.eventsFromMergeStep({ ...base, carrier: { ...base.carrier, pr: v.pr } }, v, { repo: REPO, at }).map(e => e.id)), `${tag}: the same verdict gives the same event ids (a retried write is a duplicate)`);
  }
  // story 551 (F4): a live tick that yields to a shepherd Job after the merging write made no
  // merge call. The lane reports it as refused with a lane refusal, so it writes no event.
  const yielded = { pr: base.carrier.pr, mode: 'live', verdict: 'refused', pr_refusals: [], lane_refusals: ['F4 at the merge call: 1 shepherd Job that can merge is active; yielded-to-shepherd, the next tick tries again'], yielded: 'yielded-to-shepherd', merge_call: 'not made (yielded)' };
  ok(F.eventsFromMergeStep(base, yielded, { repo: REPO, at: tickFx.merge_steps[0].at }).length === 0, 'events: live/refused by a yield to a shepherd Job (story 551) writes no event');
  ok(F.eventsFromMergeStep(base, { ...yielded, lane_refusals: [] }, { repo: REPO, at: tickFx.merge_steps[0].at }).some(e => e.type === 'merge-refused'), 'events: the same verdict without its lane refusal would be an attempt (the yield test is not vacuous)');
  const merged = tickFx.merge_steps.find(x => x.merge_step.verdict === 'merged');
  const noCommit = structuredClone(merged.merge_step); noCommit.merged.merge_commit = null;
  ok(F.eventsFromMergeStep(base, noCommit, { repo: REPO, at: merged.at })[1]?.type === 'merge-unknown', 'events: merged with no merge commit is recorded as merge-unknown (fail closed)');
  const heldItem = { ...base, state: 'held' };
  ok(F.replay(heldItem, F.eventsFromMergeStep(heldItem, merged.merge_step, { repo: REPO, at: merged.at })).errors.some(e => e.includes('chain: merge-attempted starts from needed')), 'events: a verdict on an item that is not needed is refused by the chain');
  const ownerItem = { ...base, lane: 'B-owner' };
  ok(F.replay(ownerItem, F.eventsFromMergeStep(ownerItem, merged.merge_step, { repo: REPO, at: merged.at })).errors.some(e => e.includes('only for lane A-night')), 'events: a lane merge of an item outside lane A-night is refused');
  let threw = '';
  try { F.eventsFromMergeStep({ ...base, carrier: { ...base.carrier, pr: 1 } }, merged.merge_step, { repo: REPO, at: merged.at }); } catch (e) { threw = e.message; }
  ok(threw.includes('the verdict is for PR'), 'events: a verdict for another PR is refused');
  // a later tick reads the PR left merge_unknown: merged, or not merged
  const unk = tickFx.merge_steps.find(x => x.merge_step.verdict === 'merge-unknown');
  const r0 = F.replay(base, F.eventsFromMergeStep(base, unk.merge_step, { repo: REPO, at: unk.at }));
  const later = (type, to, extra) => { const e = { schema: F.EVENT_SCHEMA, id: '', item_id: base.id, type, from: 'merge_unknown', to, at: '2026-11-02T07:15:00.000Z', actor: { kind: 'lane', login: null }, recorded_by: 'lane', attempt: 1, carrier: { repo: REPO, pr: base.carrier.pr, head: base.carrier.head }, ...extra }; e.id = F.eventId(e); return e; };
  ok(F.applyEvent(r0.item, later('merged', 'merged_unverified', { evidence: { merge_commit: 'f'.repeat(40) } })).errors.length === 0, 'events: merge_unknown read later as merged');
  ok(F.applyEvent(r0.item, later('merge-failed', 'needed', { class: 'not-merged', reason: 'open at that head on the next read', evidence: { merge_call: unk.merge_step.merge_call } })).errors.length === 0, 'events: merge_unknown read later as not merged');
}

// ── 6. the lane's copy (story 593) ──────────────────────────────────────────────
// The night lane runs from the scripts ConfigMap, built from the shepherd app's resources/
// directory only, so it carries a copy of the format. The copy must be the format: a change
// made here and not copied fails until it is.
{
  for (const f of ['record-format.mjs', 'upgrade-item.v1.schema.json', 'upgrade-event.v1.schema.json', 'upgrade-states.v1.json', 'lane-v1-to-item-v1.json']) {
    const want = readFileSync(join(ROOT, 'scripts/upgrade-agent/schema', f));
    let got = null;
    try { got = readFileSync(join(RES, f)); } catch { got = null; }
    ok(got !== null && Buffer.compare(want, got) === 0, `copy: resources/${f} is byte-identical to scripts/upgrade-agent/schema/${f}`, `cp scripts/upgrade-agent/schema/${f} kubernetes/main/apps/upgrade-agent/shepherd/app/resources/${f}`);
  }
  const kz = readFileSync(join(RES, '..', 'kustomization.yaml'), 'utf8');
  for (const f of ['record-format.mjs', 'upgrade-item.v1.schema.json', 'upgrade-event.v1.schema.json', 'upgrade-states.v1.json', 'lane-v1-to-item-v1.json', 'upgrade-history.mjs'])
    ok(kz.includes(`      - resources/${f}\n`), `copy: the scripts ConfigMap carries resources/${f}`);
}

// ── 7. the reader's points (format review of 2026-11-12, decided in story 593) ─────────
{
  // bounds: every string and every list in both schemas has one
  const unbounded = [];
  for (const [name, root] of Object.entries(F.schemas)) {
    const walk = (n, p) => {
      if (!n || typeof n !== 'object') return;
      const t = [].concat(n.type || []);
      if (t.includes('string') && n.maxLength === undefined && !n.enum && n.const === undefined) unbounded.push(`${name} ${p}`);
      if (t.includes('array') && n.maxItems === undefined) unbounded.push(`${name} ${p}`);
      for (const [k, v] of Object.entries(n.properties || {})) walk(v, `${p}/${k}`);
      for (const [k, v] of Object.entries(n.$defs || {})) walk(v, `#/$defs/${k}`);
      if (n.items) walk(n.items, `${p}[]`);
      for (const k of ['anyOf', 'allOf']) (n[k] || []).forEach((x, i) => walk(x, `${p}|${k}${i}`));
    };
    walk(root, '');
  }
  ok(unbounded.length === 0, 'bounds: every string and every list of both schemas has a maxLength or maxItems', unbounded.join('; '));
  const d = F.schemas.item.$defs;
  ok(d.name.maxLength === 300 && d.text.maxLength === 1000 && d.names.maxItems === 100 && d.texts.maxItems === 100 && F.MAX_ATTEMPTS === 20, 'bounds: the numbers in one place (the item schema\'s $defs name 300, text 1000, names and texts 100; attempts 20)');
  // fit: cut with a visible mark, never refused
  const long = { schema: F.EVENT_SCHEMA, id: '', item_id: 'renovate-pr:' + 'a'.repeat(24), type: 'merge-refused', from: 'in_progress', to: 'needed', at: '2026-11-13T06:00:00.000Z', actor: { kind: 'lane', login: null }, recorded_by: 'lane', attempt: 1, class: 'merge-step-refused',
    reason: 'r'.repeat(1500), evidence: { pr_refusals: Array.from({ length: 130 }, (_, i) => `refusal ${i}`), lane_refusals: [], merge_call: 'm'.repeat(1200) } };
  long.id = F.eventId(long);
  ok(F.checkEvent(long).some(e => /longer than 1000/.test(e)), 'bounds: the validator refuses a record over its bounds');
  const cut = F.fit(F.schemas.event, long);
  ok(F.checkEvent(cut.value).length === 0 && cut.value.reason.length === 1000 && cut.value.reason.endsWith('…') && cut.value.evidence.pr_refusals.length === 100 && cut.value.evidence.pr_refusals[99] === '… (+31 more)' && cut.value.id === long.id && cut.cuts.sort().join() === '/evidence/merge_call,/evidence/pr_refusals,/reason',
    'bounds: fit() cuts a string and a list to its bound with a visible mark, keeps the id, and names the cuts', JSON.stringify(cut.cuts));
  // attempts: the newest 20, the dropped counted, the numbering carried on
  const fx = fixtures.find(f => f.file === 'seq-merge-step-refused.json').record;
  let it = { ...clone(fx.item), state: null, attempts: [] };
  const mk = (type, from, to, n, min, extra) => { const e = { schema: F.EVENT_SCHEMA, id: '', item_id: it.id, type, from, to, at: new Date(Date.parse('2026-11-13T00:00:00.000Z') + min * 60e3).toISOString(), actor: { kind: 'lane', login: null }, recorded_by: 'lane', attempt: n, ...extra }; e.id = F.eventId(e); return e; };
  const first = mk('observed', null, 'needed', null, 0, { lane: 'A-night', class: 'no-cluster-effect', reason: 'x' });
  delete first.attempt; first.id = F.eventId(first);
  let r = F.applyEvent(it, first);
  it = r.item;
  for (let n = 1; n <= 25; n++) {
    for (const ev of [mk('merge-attempted', 'needed', 'in_progress', n, n, { lane: 'A-night', carrier: { repo: REPO, pr: it.carrier.pr } }), mk('merge-refused', 'in_progress', 'needed', n, n, { class: 'merge-step-refused', reason: 'refused' })]) {
      r = F.applyEvent(it, ev); if (r.errors.length) break; it = r.item;
    }
  }
  ok(r.errors.length === 0 && it.attempts.length === 20 && it.attempts_dropped === 5 && it.attempts[0].n === 6 && it.attempts.at(-1).n === 25 && F.checkItem(it).length === 0 && F.attemptCount(it) === 25, 'bounds: 25 attempts keep the newest 20, count 5 dropped, and stay a valid item', `${r.errors[0] || ''} ${it.attempts.length} ${it.attempts_dropped}`);
  const v26 = { pr: it.carrier.pr, head: it.carrier.head, mode: 'live', verdict: 'refused', pr_refusals: ['x'], lane_refusals: [], merge_call: 'not made' };
  ok(F.eventsFromMergeStep(it, v26, { repo: REPO, at: '2026-11-13T01:00:00.000Z' })[0].attempt === 26, 'bounds: the next merge-step attempt is numbered 26, after the dropped ones');
  // the place in the id
  const one = laneRecordOf(4477), item = F.fromLaneRecord(one, { repo: REPO });
  ok(F.placeOf(item).join() === [...one.files].sort().join() && F.placeOf(item).length > 0, 'id: the place is the sorted changed paths (carrier.files), a fact of the pull request alone');
  const otherFiles = F.fromLaneRecord({ ...one, files: ['kubernetes/main/apps/somewhere/else/app/helmrelease.yaml'] }, { repo: REPO });
  ok(item.id !== otherFiles.id, 'id: the same package and version changed in other files is another item');
  ok(F.fromLaneRecord({ ...one, head: 'f'.repeat(40), files: [...one.files].reverse() }, { repo: REPO }).id === item.id, 'id: a rebase (the same files, in any order, a new head) keeps the item');
  // a rules change that remaps a target (R8 consumers, say) changes no id
  ok(F.fromLaneRecord({ ...one, targets: ['kubernetes/main/apps/remapped/consumer', ...one.targets] }, { repo: REPO }).id === item.id && F.fromLaneRecord({ ...one, targets: [] }, { repo: REPO }).id === item.id, 'id: a rules change that remaps the targets changes no id (targets stay on the item for readers)');
  const cutList = F.fromLaneRecord({ ...one, files: [...one.files, F.CUT_FILES] }, { repo: REPO });
  ok(cutList.id !== item.id && F.placeOf(cutList).includes(F.CUT_FILES), 'id: an id built on a file list GitHub cut off carries the marker, so it says so');
  // the memory across places: a reverted version in one target is found from another target
  const rev = fixtures.find(f => f.file === 'seq-verify-failed-reverted.json').record;
  const done = F.replay(rev.item, rev.events).item;
  const pk = done.item.packages[0] || { name: done.item.name, to: done.available.version };
  const elsewhere = F.fromLaneRecord({ ...laneRecordOf(4477), packages: done.item.packages, package: pk.name, to: pk.to, files: ['kubernetes/main/apps/another/app/helmrelease.yaml'] }, { repo: REPO });
  ok(done.state === 'reverted' && elsewhere.id !== done.id && F.rememberedReverted([done, elsewhere], pk.name, pk.to).map(x => x.id).join() === done.id, 'memory: a version reverted at one path is found when asked about the same package and version at another path', `${done.state} ${pk.name}@${pk.to}`);
  ok(F.rememberedReverted([done], pk.name, '0.0.0-other').length === 0 && F.rememberedReverted([{ ...done, state: 'done' }], pk.name, pk.to).length === 0, 'memory: another version, or the same version not reverted, is not remembered');
  // a merge of a remembered version can be recorded, by anyone but the lane
  const again = (kind) => { const e = { schema: F.EVENT_SCHEMA, id: '', item_id: done.id, type: 'merged', from: 'reverted', to: 'merged_unverified', at: '2026-11-14T00:00:00.000Z', actor: { kind, login: null }, recorded_by: 'lane', attempt: F.attemptCount(done) + 1, carrier: { repo: REPO, pr: 1 }, evidence: { merge_commit: 'c'.repeat(40) } }; e.id = F.eventId(e); return F.applyEvent(done, e); };
  ok(again('renovate').errors.length === 0 && again('unknown').errors.length === 0 && again('lane').errors.length > 0, 'safety: a merge of a remembered version is recorded for renovate or unknown, and refused for the lane');
}

console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
