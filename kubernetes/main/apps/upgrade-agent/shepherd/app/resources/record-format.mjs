// record-format.mjs — the update item and event records of the upgrade lane (OP592).
//
// Story: https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/592
// Reference: .agents/reference/upgrade-records.md. No dependencies: Node 24 only.
//
//   checkItem(item)             -> [error]   haynes.upgrade-item/v1: schema, then the rules below
//   checkEvent(event)           -> [error]   haynes.upgrade-event/v1: schema, then the state machine
//   applyEvent(item, event)     -> { item, errors }   one step; refuses a broken chain
//   replay(template, events)    -> { item, errors }   all events of one item, in order
//   itemId(item), eventId(event)                      the derived ids the validator insists on
//   fromLaneRecord(record, ctx) -> item               the mapping from haynes.upgrade-lane/v1
//   eventsFromMergeStep(item, verdict, ctx) -> [event] what one merge-step verdict writes
//   fit(schema, value)          -> { value, cuts }  a record cut to the schemas' bounds, visibly
//   rememberedReverted(items, name, version) -> [{ item, why }]  every item for a package at
//                                a version that a revert touches or touched, whatever its
//                                place, with why it matched (stories 753, 556 use it)
//   validate(schema, value)     -> [error]   the JSON Schema subset below
//   schemaProblems(schema)      -> [string]  keywords the subset lacks, $refs that do not resolve
//
// THE VALIDATOR IS HAND-WRITTEN for the part of JSON Schema 2020-12 these schemas use,
// so the selftest runs offline in CI with no package install. It implements: type,
// const, enum, properties, required, additionalProperties (false or a schema), items,
// minItems, maxItems, minLength, maxLength, pattern, format (date-time only), minimum,
// maximum, minProperties, maxProperties, $ref (in-document, or '<$id>#/...' to a schema
// loaded here), allOf, anyOf, if/then/else; and ignores the annotations $schema, $id,
// $comment, title, description, $defs. schemaProblems() refuses any other keyword, so a
// schema edit cannot use a keyword this file would silently skip.
//
// Rules a schema cannot say (checked after the schema passes):
//   item:  id derived by itemId(); state not reserved; attempts numbered 1..n; a state
//          past an attempt has one; first_seen <= observed_at.
//   event: id derived by eventId(); type and states not reserved; (type, from, to) is an
//          edge of upgrade-states.v1.json with an actor (and lane) that edge allows; the
//          fields the type requires are present; recorded_at >= at.
//   chain: from equals the item's state; attempt numbers follow starts_attempt; a drill
//          attempt's events carry drill true; drill-cleared only ends a drill.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const load = f => JSON.parse(readFileSync(join(HERE, f), 'utf8'));

export const ITEM_SCHEMA = 'haynes.upgrade-item/v1';
export const EVENT_SCHEMA = 'haynes.upgrade-event/v1';
export const LANE_SCHEMA = 'haynes.upgrade-lane/v1';
export const schemas = { item: load('upgrade-item.v1.schema.json'), event: load('upgrade-event.v1.schema.json') };
export const machine = load('upgrade-states.v1.json');
export const laneMapping = load('lane-v1-to-item-v1.json');

// ── the JSON Schema subset ───────────────────────────────────────────────────
const ANNOTATIONS = new Set(['$schema', '$id', '$comment', 'title', 'description', '$defs']);
const KEYWORDS = new Set(['type', 'const', 'enum', 'properties', 'required', 'additionalProperties', 'items',
  'minItems', 'maxItems', 'minLength', 'maxLength', 'pattern', 'format', 'minimum', 'maximum', 'minProperties',
  'maxProperties', '$ref', 'allOf', 'anyOf', 'if', 'then', 'else']);
const TYPES = new Set(['null', 'boolean', 'object', 'array', 'number', 'integer', 'string']);
const REGISTRY = new Map(Object.values(schemas).map(s => [s.$id, s]));
const DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;
const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);

function resolve(ref, root) {
  const [base, frag = ''] = String(ref).split('#');
  let node = base ? REGISTRY.get(base) : root;
  const doc = node;
  for (const part of frag.split('/').slice(1)) {
    const k = part.replace(/~1/g, '/').replace(/~0/g, '~');
    node = node && typeof node === 'object' && own(node, k) ? node[k] : undefined;
  }
  return node && typeof node === 'object' ? { node, root: doc } : null;
}
const kindOf = v => (v === null ? 'null' : Array.isArray(v) ? 'array' : typeof v === 'number' ? (Number.isInteger(v) ? 'integer' : 'number') : typeof v);
const isType = (t, v) => t === kindOf(v) || (t === 'number' && typeof v === 'number' && Number.isFinite(v));
const canon = v => (Array.isArray(v) ? v.map(canon) : v && typeof v === 'object' ? Object.fromEntries(Object.keys(v).sort().map(k => [k, canon(v[k])])) : v);
const same = (a, b) => JSON.stringify(canon(a)) === JSON.stringify(canon(b));
const at = p => p || '(root)';

function walk(s, v, path, root, errs) {
  if (s.$ref !== undefined) {
    const r = resolve(s.$ref, root);
    if (!r) errs.push(`${at(path)}: $ref ${s.$ref} does not resolve`);
    else walk(r.node, v, path, r.root, errs);
  }
  if (s.type !== undefined) {
    const ts = [].concat(s.type);
    if (!ts.some(t => isType(t, v))) { errs.push(`${at(path)}: is ${kindOf(v)}, expected ${ts.join(' or ')}`); return; }
  }
  if (own(s, 'const') && !same(s.const, v)) errs.push(`${at(path)}: must be ${JSON.stringify(s.const)}, is ${JSON.stringify(v)}`);
  if (s.enum && !s.enum.some(e => same(e, v))) errs.push(`${at(path)}: ${JSON.stringify(v)} is not one of ${s.enum.map(e => JSON.stringify(e)).join(', ')}`);
  if (typeof v === 'string') {
    const n = [...v].length;
    if (s.minLength !== undefined && n < s.minLength) errs.push(`${at(path)}: shorter than ${s.minLength}`);
    if (s.maxLength !== undefined && n > s.maxLength) errs.push(`${at(path)}: longer than ${s.maxLength}`);
    if (s.pattern !== undefined && !new RegExp(s.pattern, 'u').test(v)) errs.push(`${at(path)}: ${JSON.stringify(v)} does not match ${s.pattern}`);
    if (s.format === 'date-time' && !(DATE_TIME.test(v) && Number.isFinite(Date.parse(v)))) errs.push(`${at(path)}: ${JSON.stringify(v)} is not an RFC 3339 date-time`);
  }
  if (typeof v === 'number') {
    if (s.minimum !== undefined && v < s.minimum) errs.push(`${at(path)}: below ${s.minimum}`);
    if (s.maximum !== undefined && v > s.maximum) errs.push(`${at(path)}: above ${s.maximum}`);
  }
  if (Array.isArray(v)) {
    if (s.minItems !== undefined && v.length < s.minItems) errs.push(`${at(path)}: fewer than ${s.minItems} items`);
    if (s.maxItems !== undefined && v.length > s.maxItems) errs.push(`${at(path)}: more than ${s.maxItems} items`);
    if (s.items !== undefined) v.forEach((x, i) => walk(s.items, x, `${path}/${i}`, root, errs));
  }
  if (kindOf(v) === 'object') {
    const keys = Object.keys(v);
    if (s.minProperties !== undefined && keys.length < s.minProperties) errs.push(`${at(path)}: fewer than ${s.minProperties} properties`);
    if (s.maxProperties !== undefined && keys.length > s.maxProperties) errs.push(`${at(path)}: more than ${s.maxProperties} properties (${keys.join(', ')})`);
    for (const k of s.required || []) if (!own(v, k)) errs.push(`${at(path)}: missing required ${k}`);
    for (const k of keys) {
      if (s.properties && own(s.properties, k)) walk(s.properties[k], v[k], `${path}/${k}`, root, errs);
      else if (s.additionalProperties === false) errs.push(`${path}/${k}: not allowed here`);
      else if (s.additionalProperties && typeof s.additionalProperties === 'object') walk(s.additionalProperties, v[k], `${path}/${k}`, root, errs);
    }
  }
  for (const sub of s.allOf || []) walk(sub, v, path, root, errs);
  if (s.anyOf) {
    const tries = s.anyOf.map(sub => { const e = []; walk(sub, v, path, root, e); return e; });
    if (!tries.some(e => !e.length)) errs.push(`${at(path)}: matches none of anyOf (${tries.map(e => e[0]).join(' | ')})`);
  }
  if (s.if !== undefined) {
    const e = [];
    walk(s.if, v, path, root, e);
    const branch = e.length ? s.else : s.then;
    if (branch !== undefined) walk(branch, v, path, root, errs);
  }
}

export function validate(schema, value) {
  const errs = [];
  walk(schema, value, '', schema, errs);
  return errs;
}

export function schemaProblems(schema) {
  const out = [];
  const visit = (s, where) => {
    if (!s || typeof s !== 'object' || Array.isArray(s)) { out.push(`${where}: a schema must be an object`); return; }
    for (const k of Object.keys(s)) if (!KEYWORDS.has(k) && !ANNOTATIONS.has(k)) out.push(`${where}: keyword ${k} is not implemented by record-format.mjs`);
    for (const t of s.type === undefined ? [] : [].concat(s.type)) if (!TYPES.has(t)) out.push(`${where}: unknown type ${t}`);
    if (s.$ref !== undefined && !resolve(s.$ref, schema)) out.push(`${where}: $ref ${s.$ref} does not resolve`);
    if (s.format !== undefined && s.format !== 'date-time') out.push(`${where}: format ${s.format} is not implemented`);
    if (s.pattern !== undefined) { try { new RegExp(s.pattern, 'u'); } catch (e) { out.push(`${where}: pattern ${s.pattern}: ${e.message}`); } }
    if (s.enum !== undefined && !Array.isArray(s.enum)) out.push(`${where}: enum must be a list`);
    for (const [k, sub] of Object.entries(s.properties || {})) visit(sub, `${where}/properties/${k}`);
    for (const [k, sub] of Object.entries(s.$defs || {})) visit(sub, `${where}/$defs/${k}`);
    if (s.items !== undefined) visit(s.items, `${where}/items`);
    if (s.additionalProperties !== undefined && typeof s.additionalProperties !== 'boolean') visit(s.additionalProperties, `${where}/additionalProperties`);
    for (const key of ['allOf', 'anyOf']) (s[key] || []).forEach((sub, i) => visit(sub, `${where}/${key}/${i}`));
    for (const key of ['if', 'then', 'else']) if (s[key] !== undefined) visit(s[key], `${where}/${key}`);
  };
  visit(schema, '#');
  return out;
}

// ── ids ──────────────────────────────────────────────────────────────────────
const sha = s => createHash('sha256').update(s).digest('hex');
// One update in one place, not its carrier: the source, the repository, the sorted
// [package, version] pairs, and the place: the sorted paths the update changes
// (carrier.files), a fact of the pull request alone. Not the lane's targets: those come
// through the lane's rules, which keep being tuned, and a tuning would re-key open items.
// Equal across a rebase (Renovate replays the same change on the same files) and across a
// reopened PR for the same version in the same files, which is what remembers a reverted
// version (story 753). A file list GitHub cut off ends in '<file list truncated>' (the
// lane's gatherFacts and the history's reads add it alike), so such an id says it was built
// on a cut list. Only the writer derives it: a reader treats it as opaque and never
// recomputes it.
export const CUT_FILES = '<file list truncated>';
export const placeOf = it => [...(it.carrier?.files || [])].sort();
export function itemId(it) {
  const pk = it.item?.packages?.length ? it.item.packages.map(p => [p.name, p.to ?? null]) : [[it.item?.name ?? null, it.available?.version ?? null]];
  const key = JSON.stringify([it.source, it.item?.repo ?? null, pk.map(x => JSON.stringify(x)).sort(), placeOf(it)]);
  return `${it.source}:${sha(key).slice(0, 24)}`;
}
export const eventId = ev => `ev:${sha(JSON.stringify([ev.item_id, ev.type, ev.at, ev.attempt ?? null])).slice(0, 24)}`;

// ── the state machine ────────────────────────────────────────────────────────
const RESERVED_STATES = new Set(machine.reserved.states);
const RESERVED_EVENTS = new Set(machine.reserved.events);
const get = (o, path) => path.split('.').reduce((a, k) => (a && typeof a === 'object' ? a[k] : undefined), o);
const st = s => s ?? '(new)';

function findEdge(ev, errs) {
  const def = machine.events[ev.type];
  const shape = def.edges.filter(e => e.from.includes(ev.from) && e.to.includes(ev.to));
  if (!shape.length) {
    errs.push(`illegal transition: ${ev.type} from ${st(ev.from)} to ${ev.to} (legal: ${def.edges.map(e => `${e.from.map(st).join('|')} -> ${e.to.join('|')}`).join('; ')})`);
    return null;
  }
  const byActor = shape.filter(e => e.actors.includes(ev.actor.kind));
  if (!byActor.length) {
    errs.push(`/actor/kind: ${ev.actor.kind} may not record ${ev.type} from ${st(ev.from)} (allowed: ${[...new Set(shape.flatMap(e => e.actors))].join(', ')})`);
    return null;
  }
  const byLane = byActor.filter(e => !e.lanes || e.lanes.includes(ev.lane));
  if (!byLane.length) {
    errs.push(`/lane: ${ev.actor.kind} may record ${ev.type} only for lane ${byActor[0].lanes.join('|')}, not ${ev.lane ?? '(none)'}`);
    return null;
  }
  return byLane[0];
}

export function checkEvent(ev) {
  const errs = validate(schemas.event, ev);
  if (errs.length) return errs;
  const want = eventId(ev);
  if (ev.id !== want) errs.push(`/id: is ${ev.id}; the format derives ${want} from item_id, type, at and attempt`);
  if (RESERVED_EVENTS.has(ev.type)) errs.push(`/type: ${ev.type} is reserved for piece 2 and refused in piece 1`);
  for (const [k, s] of [['from', ev.from], ['to', ev.to]]) if (RESERVED_STATES.has(s)) errs.push(`/${k}: state ${s} is reserved for piece 2 and refused in piece 1`);
  if (errs.length) return errs;
  if (!findEdge(ev, errs)) return errs;
  for (const r of machine.events[ev.type].requires) {
    if (r === 'drill') { if (ev.drill !== true) errs.push(`/drill: ${ev.type} requires drill true`); continue; }
    const v = get(ev, r);
    if (v === undefined || v === null || v === '') errs.push(`/${r.replace(/\./g, '/')}: ${ev.type} requires ${r}`);
  }
  if (ev.recorded_at !== undefined && Date.parse(ev.recorded_at) < Date.parse(ev.at)) errs.push('/recorded_at: before at');
  return errs;
}

export function checkItem(it) {
  const errs = validate(schemas.item, it);
  if (errs.length) return errs;
  const want = itemId(it);
  if (it.id !== want) errs.push(`/id: is ${it.id}; the format derives ${want} from source, item.repo, the packages' target versions and the place (the changed files)`);
  if (RESERVED_STATES.has(it.state)) errs.push(`/state: ${it.state} is reserved for piece 2 and refused in piece 1`);
  const dropped = it.attempts_dropped || 0;
  it.attempts.forEach((a, i) => {
    if (a.n !== dropped + i + 1) errs.push(`/attempts/${i}/n: is ${a.n}, expected ${dropped + i + 1}`);
    if (Date.parse(a.at) < Date.parse(a.started_at)) errs.push(`/attempts/${i}/at: before started_at`);
  });
  const phase = machine.states[it.state]?.phase;
  if ((phase === 'attempt' || phase === 'settled') && !it.attempts.length) errs.push(`/attempts: state ${it.state} follows an attempt, and attempts is empty`);
  if (Date.parse(it.observed_at) < Date.parse(it.first_seen)) errs.push('/observed_at: before first_seen');
  return errs;
}

// attempts[] keeps the newest MAX_ATTEMPTS (the schema's maxItems); attempts_dropped counts
// the older ones, so the next attempt's number is their sum plus one.
export const MAX_ATTEMPTS = schemas.item.properties.attempts.maxItems;
export const attemptCount = it => (it.attempts_dropped || 0) + (it.attempts || []).length;

// One event applied to the item it names. item.state null = the item has no event yet.
export function applyEvent(item, ev) {
  const fail = (...e) => ({ item, errors: e });
  const errors = checkEvent(ev);
  if (errors.length) return fail(...errors);
  if (ev.item_id !== item.id) return fail(`/item_id: the event is for ${ev.item_id}, not ${item.id}`);
  if ((item.state ?? null) !== ev.from) return fail(`chain: ${ev.type} starts from ${st(ev.from)}, but the item is ${st(item.state)}`);
  if (item.state != null && Date.parse(ev.at) < Date.parse(item.observed_at)) return fail(`chain: ${ev.type} at ${ev.at} is before the item's last event ${item.observed_at}`);
  const edge = findEdge(ev, []);
  const next = structuredClone(item);
  let cur = next.attempts.at(-1);
  if (edge.starts_attempt) {
    const n = attemptCount(next) + 1;
    if (ev.attempt !== n) return fail(`/attempt: ${ev.type} from ${st(ev.from)} opens attempt ${n}, the event says ${ev.attempt}`);
    cur = { n: ev.attempt, actor: ev.actor, started_at: ev.at, at: ev.at, result: edge.attempt_result, recovery: null, drill: ev.drill === true, evidence_url: ev.evidence?.url ?? null };
    next.attempts.push(cur);
    // the newest MAX_ATTEMPTS are kept; the dropped are counted, the kept numbered after them
    while (next.attempts.length > MAX_ATTEMPTS) { next.attempts.shift(); next.attempts_dropped = (next.attempts_dropped || 0) + 1; }
  } else if (ev.attempt != null) {
    if (!cur || ev.attempt !== cur.n) return fail(`/attempt: ${ev.type} names attempt ${ev.attempt}; the current attempt is ${cur?.n ?? 'none'}`);
    if ((ev.drill === true) !== cur.drill) return fail(`/drill: attempt ${cur.n} is ${cur.drill ? '' : 'not '}a drill, and the event says drill ${ev.drill === true}`);
    cur.at = ev.at;
    if (edge.attempt_result) cur.result = edge.attempt_result;
    if (edge.recovery) cur.recovery = edge.recovery;
    if (ev.evidence?.url) cur.evidence_url = ev.evidence.url;
  }
  if (ev.type === 'drill-cleared' && !cur?.drill) return fail(`drill-cleared: attempt ${cur?.n ?? 'none'} is not a drill, so the reverted version stays remembered`);
  if (ev.from === null) next.first_seen = ev.at;
  next.observed_at = ev.at;
  next.state = ev.to;
  if (ev.type === 'observed') Object.assign(next, { lane: ev.lane, class: ev.class, reason: ev.reason });
  if (ev.carrier && next.carrier) for (const k of ['repo', 'pr', 'head', 'fingerprint']) if (ev.carrier[k] !== undefined) next.carrier[k] = ev.carrier[k];
  return { item: next, errors: [] };
}

// template: the item's fixed fields (and, when the first event is not from null, its
// state and attempts before that event). Returns the item after the last event.
export function replay(template, events) {
  let item = structuredClone(template);
  if (!events.length) return { item, errors: ['replay: no events'] };
  if (events[0].from === null) { item.state = null; item.attempts = []; delete item.attempts_dropped; }
  for (const [i, ev] of events.entries()) {
    const r = applyEvent(item, ev);
    if (r.errors.length) return { item, errors: r.errors.map(e => `event ${i} (${ev.type}): ${e}`) };
    item = r.item;
  }
  return { item, errors: checkItem(item).map(e => `final item: ${e}`) };
}

// ── the mapping from haynes.upgrade-lane/v1 (lane-v1-to-item-v1.json) ─────────
const LANES = { 'LANE-A': 'A-night', 'LANE-B-AGENT': 'B-agent', 'LANE-B-OWNER': 'B-owner', EXCLUDED: 'excluded', BLOCKED: 'A-night' };
export function versionKind(v) {
  if (v === null || v === undefined || v === '') return 'unknown';
  const s = String(v);
  if (/^(sha256:)?[0-9a-f]{7,64}$/.test(s) && /[a-f]/.test(s)) return 'digest';
  if (/^(={1,3}|v)?\d+(\.\d+)*([-+._][0-9A-Za-z.+_-]+)?$/.test(s)) return 'semver';
  return 'other';
}

// ctx: { repo, observed_at (when the record has no 'updated'), first_seen (when known;
// age_days then counts from it, a lower bound) }. Throws on a record it cannot map
// rather than dropping a field. The item's state comes from the lane's verdict only;
// a merge-step verdict (last_attempt) is copied, and becomes events through
// eventsFromMergeStep().
export function fromLaneRecord(rec, ctx = {}) {
  if (rec?.schema !== LANE_SCHEMA) throw new Error(`fromLaneRecord: schema ${rec?.schema} is not ${LANE_SCHEMA}`);
  const repo = ctx.repo;
  if (!repo) throw new Error('fromLaneRecord: ctx.repo is required');
  const observed = rec.updated ?? ctx.observed_at;
  if (!observed) throw new Error(`fromLaneRecord #${rec.pr}: no 'updated' and no ctx.observed_at`);
  const unknownLane = rec.lane === 'BLOCKED' && rec.class === 'facts-incomplete';
  const lane = unknownLane ? 'unknown' : LANES[rec.lane];
  if (!lane) throw new Error(`fromLaneRecord #${rec.pr}: lane ${rec.lane} has no mapping`);
  const own = rec.own_images || [];
  const tokens = String(rec.update_type || 'unknown').split('+');
  const source = own.length ? 'own-image' : tokens.every(t => t === 'digest' || t === 'pinDigest') ? 'image-pin' : 'renovate-pr';
  const findings = rec.findings || [];
  const held = findings.some(f => /^[A-Z-]+\/(hold|vet-hold):/.test(f));
  const first = ctx.first_seen ?? observed;
  const age = ctx.first_seen ? Math.max(0, Math.round((Date.parse(observed) - Date.parse(first)) / 864e3) / 100) : null;
  const item = {
    schema: ITEM_SCHEMA,
    id: '',
    source,
    item: { name: rec.package ?? `${repo}#${rec.pr}`, repo, packages: (rec.packages || []).map(p => ({ name: p.name, update: p.update ?? null, from: p.from ?? null, to: p.to ?? null })), targets: rec.targets || [], own_images: own },
    installed: { version: rec.from ?? null, kind: versionKind(rec.from) },
    available: { version: rec.to ?? null, kind: versionKind(rec.to), update_type: rec.update_type || 'unknown' },
    behind: { age_days: age, versions_behind: null, days_to_expiry: null, basis: age === null ? 'unknown' : 'first-seen' },
    lane,
    class: rec.class,
    reason: rec.reason ?? '',
    state: rec.lane === 'BLOCKED' ? 'blocked' : held ? 'held' : 'needed',
    approval: rec.preapproval ?? {},
    schedule: rec.schedule ?? {},
    attempts: [],
    first_seen: first,
    observed_at: observed,
    coverage: unknownLane ? { status: 'partial', detail: rec.reason ?? 'facts incomplete' } : { status: 'read' },
    carrier: { kind: 'github-pr', repo, pr: rec.pr, head: rec.head ?? null, title: rec.title ?? null, fingerprint: rec.fingerprint ?? null, fingerprint_basis: rec.fingerprint_basis ?? null, commit_count: rec.commit_count ?? null, change_key: rec.change_key ?? null, files: rec.files || [], head_committed_at: rec.head_committed_at ?? null },
    lane_detail: { from_schema: rec.schema, rules: rec.rules || [], findings, checks: rec.checks || {}, cluster_effect: rec.cluster_effect ?? null, cluster_effect_wait: rec.cluster_effect_wait ?? null, via_components: rec.via_components || [], cluster_wide: rec.cluster_wide || [], gates: rec.gates ?? null, backup: rec.backup ?? null, last_attempt: rec.last_attempt ?? null, retry: rec.retry ?? null },
  };
  item.id = itemId(item);
  return item;
}

// ── merge-step verdicts to events (lane-v1-to-item-v1.json, merge_step_verdicts) ─
// verdict: summary.merge_step of one tick (night-merge.mjs laneMergeStep, story 752).
// ctx: { repo, at (the tick's summary.at) }. Returns the events to append, in order, for
// the item the verdict names; [] when the verdict writes none. The caller applies them
// with applyEvent(), which refuses them unless the item is needed in lane A-night.
export function eventsFromMergeStep(item, v, ctx = {}) {
  if (!v || v.mode !== 'live' || !['refused', 'merged', 'merge-failed', 'merge-unknown'].includes(v.verdict)) return [];
  if ((v.lane_refusals || []).length) return []; // the lane declined: no attempt on the item
  if (!ctx.repo || !ctx.at) throw new Error('eventsFromMergeStep: ctx.repo and ctx.at are required');
  if (item.carrier?.pr !== v.pr) throw new Error(`eventsFromMergeStep: the verdict is for PR ${v.pr}, the item's carrier is PR ${item.carrier?.pr}`);
  const attempt = attemptCount(item) + 1;
  const carrier = { repo: ctx.repo, pr: v.pr, head: v.head ?? null };
  const after = Date.parse(v.merged?.merged_at || '') >= Date.parse(ctx.at) ? new Date(Date.parse(v.merged.merged_at)).toISOString() : ctx.at;
  const ev = (type, from, to, extra) => {
    const e = { schema: EVENT_SCHEMA, id: '', item_id: item.id, type, from, to, at: ctx.at, actor: { kind: 'lane', login: null }, recorded_by: 'lane', attempt, ...extra };
    e.id = eventId(e);
    return e;
  };
  const out = [ev('merge-attempted', 'needed', 'in_progress', { lane: item.lane, carrier })];
  const call = v.merge_call || 'not recorded';
  if (v.verdict === 'refused') {
    const pr = v.pr_refusals || [];
    out.push(ev('merge-refused', 'in_progress', 'needed', { class: 'merge-step-refused', reason: pr[0] || 'refused by the merge step', carrier, evidence: { pr_refusals: pr, lane_refusals: [] } }));
  } else if (v.verdict === 'merged' && /^[0-9a-f]{40}$/.test(v.merged?.merge_commit || '')) {
    out.push(ev('merged', 'in_progress', 'merged_unverified', { at: after, carrier, evidence: { merge_commit: v.merged.merge_commit, merge_call: call } }));
  } else if (v.verdict === 'merge-failed') {
    out.push(ev('merge-failed', 'in_progress', 'needed', { class: 'merge-call-failed', reason: call, carrier, evidence: { merge_call: call } }));
  } else {
    // merge-unknown, or merged with no merge commit to record: fail closed
    out.push(ev('merge-unknown', 'in_progress', 'merge_unknown', { class: 'merge-outcome-unread', reason: v.verdict === 'merged' ? 'merged, but no merge commit was read' : call, carrier, evidence: { merge_call: call } }));
  }
  return out;
}

// ── bounds: a record cut to the schemas, with a visible mark (never refused for length) ──
// A string over its maxLength keeps maxLength - 1 characters and ends in '…', unless the
// field has a pattern (a class, a timestamp, a repo, a SHA): a patterned field is never cut,
// so validation refuses a value that is too long with the real reason. A list of
// strings over its maxItems keeps maxItems - 1 entries and a last entry '… (+N more)'; a list
// of objects keeps its first maxItems. cuts names each path that was cut, so the writer can
// say so. The bounds are the schemas' own (one place): upgrade-item.v1.schema.json $defs
// name, text, text_or_empty, names, texts, and the inline maxLength/maxItems.
export function fit(schema, value) {
  const cuts = [];
  const nodes = (s, root) => { const out = []; let n = s, r = root; for (let i = 0; n && i < 8; i++) { out.push(n); if (!n.$ref) break; const x = resolve(n.$ref, r); if (!x) break; n = x.node; r = x.root; } return { list: out, root: r }; };
  const go = (s, v, path, root) => {
    if (!s || typeof s !== 'object') return v;
    const { list, root: r } = nodes(s, root);
    const pick = k => list.map(n => n[k]).find(x => x !== undefined);
    if (typeof v === 'string') {
      const max = pick('maxLength'), chars = [...v];
      if (pick('pattern') !== undefined) return v; // never cut a patterned field
      if (Number.isInteger(max) && chars.length > max) { cuts.push(path || '(root)'); return chars.slice(0, max - 1).join('') + '…'; }
      return v;
    }
    if (Array.isArray(v)) {
      const max = pick('maxItems'), items = pick('items');
      let out = v.map((x, i) => go(items, x, `${path}/${i}`, r));
      if (Number.isInteger(max) && out.length > max) {
        cuts.push(path || '(root)');
        out = out.every(x => typeof x === 'string') ? [...out.slice(0, max - 1), `… (+${out.length - max + 1} more)`] : out.slice(0, max);
      }
      return out;
    }
    if (v && typeof v === 'object') {
      const props = Object.assign({}, ...list.map(n => n.properties || {}));
      return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, props[k] ? go(props[k], x, `${path}/${k}`, r) : x]));
    }
    return v;
  };
  return { value: go(schema, value, '', schema), cuts };
}

// ── the memory across places (stories 753, 556; nothing here decides a merge) ─────────
// Conservative: every item that updates the package to the version, whatever its place (the
// id has the place in it, so the same version in other files is another item), and that a
// revert touches or touched. why says which: failed (a revert is owed), reverting (a revert
// is open), reverted_unverified (the revert merged; recovery not verified, where every
// revert the history records today stays), reverted (recovery verified), or ever-reverted
// (the version was merged again after its revert; ever_reverted on a history item, or an
// attempt with result reverted). A version never reverted returns nothing.
export const REVERT_MEMORY = Object.freeze(['failed', 'reverting', 'reverted_unverified', 'reverted']);
export function rememberedReverted(items, name, version) {
  const out = [];
  for (const it of items || []) {
    const same = it?.item?.packages?.length
      ? it.item.packages.some(p => p.name === name && (p.to ?? null) === (version ?? null))
      : it?.item?.name === name && (it?.available?.version ?? null) === (version ?? null);
    if (!same) continue;
    const ever = it.ever_reverted === true || (it.attempts || []).some(a => a?.result === 'reverted');
    const why = REVERT_MEMORY.includes(it.state) ? it.state : ever ? 'ever-reverted' : null;
    if (why) out.push({ item: it, why });
  }
  return out;
}
