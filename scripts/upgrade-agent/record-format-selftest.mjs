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
//      classifier's output on the 2026-11-09 queue, plus the fields run-night.mjs sets)
//      has a row in schema/lane-v1-to-item-v1.json, every copy row holds, and every
//      converted record is a valid item. A new lane field fails here until its row says
//      where it goes.
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
  for (const e of ['observed', 'merge-attempted', 'merge-refused', 'merged', 'awaiting-pin', 'verified', 'verification-failed', 'revert-opened', 'revert-merged', 'recovery-verified', 'drill-requested', 'drill-cleared'])
    ok(NON_RESERVED_EVENTS.includes(e), `machine: event ${e} exists for the later stories`);
  for (const story of ['752', '553', '753', '593']) ok(NON_RESERVED_EVENTS.some(e => M.events[e].used_by.includes(story)), `machine: story ${story} has events to write`);
  // safety properties
  const into = st => edges.filter(e => e.to.includes(st));
  ok(into('in_progress').every(e => same(e.from, ['needed'])), 'safety: an attempt starts only from needed');
  ok(into('in_progress').filter(e => e.actors.includes('lane')).every(e => same(e.lanes, ['A-night'])), 'safety: the lane starts a merge only for lane A-night');
  ok(edges.filter(e => e.name === 'merged' && e.actors.includes('lane')).every(e => same(e.from, ['in_progress'])), 'safety: the lane records a merge only after its merge step started');
  const outOfReverted = edges.filter(e => e.from.includes('reverted')).map(e => `${e.name}:${[...e.actors].sort().join('+')}`).sort();
  ok(same(outOfReverted, ['drill-cleared:hand+lane', 'resolved:person']), 'safety: exactly drill-cleared (a drill) and resolved by a person leave reverted', outOfReverted.join(', '));
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
  if (r.lane === 'LANE-A') Object.assign(r, { backup: { rule: rules.night.backup_rule.text, targets: [], stateful: false }, gates: { namespaces: [], blockers: [], would_be_eligible: true } });
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
  const nightSrc = readFileSync(join(RES, 'run-night.mjs'), 'utf8');
  const nightFields = [...nightSrc.matchAll(/\br\.([a-z_]+)\s*=(?!=)/g)].map(m => m[1]);
  ok(['backup', 'gates', 'updated', 'preapproval', 'schedule', 'last_attempt'].every(f => nightFields.includes(f)), 'mapping: run-night.mjs fields found in its source', nightFields.join(', '));
  const written = new Set([...records.flatMap(r => Object.keys(r)), ...nightFields]);
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
  for (const rec of records) {
    const it = F.fromLaneRecord(rec, { repo: REPO });
    const errs = F.checkItem(it);
    ok(errs.length === 0, `mapping: PR ${rec.pr} converts to a valid item`, errs.join('; '));
    for (const r of rows.filter(x => x.how === 'copy')) ok(same(get(it, r.item), rec[r.lane] ?? null), `mapping: PR ${rec.pr} ${r.lane} copied to ${r.item}`, `${JSON.stringify(get(it, r.item))} vs ${JSON.stringify(rec[r.lane])}`);
    const k = `${it.source}/${it.lane}/${it.state}`;
    tally[k] = (tally[k] || 0) + 1;
  }
  console.log(`mapping: ${records.length} queue PRs as source/lane/state: ` + Object.entries(tally).sort().map(([k, v]) => `${k} ${v}`).join(', '));
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
  let threw = '';
  try { F.fromLaneRecord({ ...laneRecordOf(4477), last_attempt: { result: 'merged' } }, { repo: REPO }); } catch (e) { threw = e.message; }
  ok(threw.includes('last_attempt is not empty'), 'map: a non-empty last_attempt is refused, not dropped');
  for (const [v, k] of [['1.37.2', 'semver'], ['v9.18.0-ls431', 'semver'], ['==5.0.0.93', 'semver'], ['24-slim', 'semver'], ['a0f2e26', 'digest'], ['sha256:' + 'ab'.repeat(32), 'digest'], ['latest', 'other'], [null, 'unknown']])
    ok(F.versionKind(v) === k, `map: version ${v} is ${k}`, F.versionKind(v));
}

console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
