// upgrade-history.mjs — the history of every attempt: ConfigMap upgrade-agent/upgrade-ledger (story 593).
//
// Story: https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/593
// Runbook: .agents/runbooks/upgrade-night-lane.md, "The history (story 593)".
// Format: .agents/reference/upgrade-records.md. Node 24 only, no dependencies.
//
// A THIN LAYER OVER THE RECORD FORMAT. Every event is a haynes.upgrade-event/v1 record
// built here and checked by record-format.mjs: its id is eventId(), each one is applied
// with applyEvent() (the state machine refuses an illegal edge, a wrong actor or a broken
// chain), items come from fromLaneRecord(), and a live merge-step verdict becomes events
// through eventsFromMergeStep(). record-format.mjs and its four JSON files in this
// directory are byte-identical copies of scripts/upgrade-agent/schema/ (the record-format
// selftest fails on any drift), so a change to the format is made there and copied.
//
// WHAT A TICK WRITES. Events the format gives the lane in dry-run, each once:
//   observed  an open Renovate PR's item seen for the first time, or its state (needed,
//             held, blocked), lane or class changed; an unchanged item writes nothing
//   closed    the PR closed without a merge, or it now carries a newer version (the old
//             item closes; actor renovate)
//   merged    someone other than the lane merged it (Renovate's auto-merge, the shepherd,
//             a session, a person): an open item whose PR left the open list, or a PR the
//             lane never saw open (recorded_by backfill)
//   revert-merged  a merged PR titled 'Revert "..."' whose body names a recorded item
// and in live mode the merge step's own events (eventsFromMergeStep; none in dry-run). A
// live refusal of the same head with the same refusals as the item's last refusal is
// collapsed: it writes nothing again.
//
// THE CONFIGMAP. Runtime state like upgrade-lane-state (not Flux-managed). data:
//   events  one compact event per line, oldest first: the format's event without its
//           constant "schema" key and without a null actor.login (unpack() restores it)
//   items   per item: name, from, to, update type and PR (what the events cannot say), and
//           "base", the item's state when its earlier events rotated out
//   meta    schema haynes.upgrade-ledger/v1; the backfill marker and its count comparison;
//           the last rotation
// ROTATION keeps the object under night.history.max_bytes: the oldest events go first. An
// item whose events rotate out keeps its state in items[id].base, so a reverted version
// stays reverted (the machine's one sticky state) and an open or owed item keeps its
// place. Only an item that is closed, merged_unverified or done, with no event left, is
// forgotten.
//
// THE WRITE. One spawn site: kubectl apply --server-side under field manager
// upgrade-night-history, always with metadata.resourceVersion of the read (the API server
// answers 409 Conflict when another writer changed the object since; registry Store.Update
// compares the applied object's resourceVersion with the stored one). A missing object is
// first created empty under upgrade-night-history-init (create-via-apply ignores a
// resourceVersion), so two first writers cannot both write events. A conflict or failure
// writes nothing; the next tick derives the same events again. Ids are deterministic
// (eventId over item, type, time and attempt), so a retried write is a duplicate the
// history drops.
import { spawnSync } from 'node:child_process';
import * as RF from './record-format.mjs';
import * as L from './lane-classify.mjs';
import * as M from './night-merge.mjs';

export const HISTORY = Object.freeze({ name: 'upgrade-ledger', namespace: 'upgrade-agent' });
export const HISTORY_SCHEMA = 'haynes.upgrade-ledger/v1';
export const MANAGER = 'upgrade-night-history';
export const INIT_MANAGER = 'upgrade-night-history-init';
export const KEYS = Object.freeze(['events', 'items', 'meta']);
const LABELS = Object.freeze({ 'app.kubernetes.io/name': 'upgrade-shepherd', 'app.kubernetes.io/component': 'upgrade-history' });
// open states: the lane observes these items and closes them
export const OPEN = Object.freeze(['needed', 'held', 'blocked', 'unknown']);
// the from-states of observed (a closed item is observed again when its PR reopens)
export const OBSERVABLE = Object.freeze([...OPEN, 'closed']);
// forgotten once every event rotated out: nothing is owed and nothing must be remembered
export const FORGETTABLE = Object.freeze(['closed', 'merged_unverified', 'done']);
// the states revert-merged leaves for a revert nobody recorded as opened
const REVERTABLE = ['failed', 'merged_unverified', 'awaiting_pin', 'done'];
// the item has been merged (the count comparison)
const AFTER_MERGE = ['merged_unverified', 'awaiting_pin', 'done', 'failed', 'reverting', 'reverted_unverified', 'reverted', 'escalated'];
const TERMINAL = ['merged', 'closed', 'merge-unknown'];
const SHA = /^[0-9a-f]{40}$/;
const SCAN_FIELDS = 'number,title,author,mergedAt,mergedBy,labels,mergeCommit,headRefOid';
const FATE_FIELDS = 'number,state,mergedAt,mergedBy,mergeCommit,closedAt,headRefOid';
const KUBECTL_TIMEOUT_MS = 30e3;
const iso = v => new Date(typeof v === 'number' ? v : Date.parse(v)).toISOString();
const later = (a, b) => (!b || Date.parse(a) >= Date.parse(b) ? a : b);
const parse = s => { try { return JSON.parse(s); } catch { return null; } };
const tryRun = (run, cmd, argv) => { try { return run(cmd, argv, { allowFail: true }); } catch { return null; } };

// ── config: night.history in upgrade-rules.json ───────────────────────────────────
export function historyConfig(rules) {
  const c = rules?.night?.history || {};
  const int = (v, lo, hi) => Number.isInteger(v) && v >= lo && v <= hi;
  const logins = v => Array.isArray(v) && v.length > 0 && v.every(x => typeof x === 'string' && /^[A-Za-z0-9-]+(\[bot\])?$/.test(x));
  const a = c.actors || {};
  const ok = c.configmap === HISTORY.name && int(c.max_bytes, 65536, 917504) && int(c.backfill_days, 1, 60) && int(c.catch_up_margin_hours, 1, 168)
    && int(c.max_search_results, 100, 1000) && int(c.max_reason_chars, 40, 1000) && logins(a.renovate) && logins(a.shepherd) && logins(a.hand);
  return { ok, ...c, actors: a, why: ok ? null : 'night.history in upgrade-rules.json is invalid (configmap upgrade-ledger; 65536 <= max_bytes <= 917504; backfill_days 1 to 60; catch_up_margin_hours 1 to 168; max_search_results 100 to 1000; max_reason_chars 40 to 1000; actors renovate, shepherd and hand as login lists)' };
}

// ── actors ──────────────────────────────────────────────────────────────────────
// gh's GraphQL reads an app as app/<name>; REST, and the format, as <name>[bot].
export const normLogin = l => (typeof l === 'string' && l ? (l.startsWith('app/') ? `${l.slice(4)}[bot]` : l) : null);
export function actorOf(login, cfg) {
  const l = normLogin(login);
  if (!l) return null;
  for (const kind of ['renovate', 'shepherd', 'hand']) if ((cfg.actors?.[kind] || []).includes(l)) return { kind, login: l };
  return { kind: /\[bot\]$/.test(l) ? 'hand' : 'person', login: l };
}
const reasonOf = (s, cfg) => { const t = String(s ?? '').replace(/\s+/g, ' ').trim() || 'no reason recorded'; return t.length > cfg.max_reason_chars ? `${t.slice(0, cfg.max_reason_chars - 1)}…` : t; };

// ── events: built here, checked by the format ─────────────────────────────────────
export function makeEvent(f) {
  const e = { schema: RF.EVENT_SCHEMA, id: '', ...f };
  for (const k of Object.keys(e)) if (e[k] === undefined) delete e[k];
  e.id = RF.eventId(e);
  return e;
}
// storage: the constant schema key, the id (eventId() of item_id, type, at and attempt)
// and a null actor.login are left out; unpack() restores schema and id
export function pack(ev) {
  const { schema, id, ...x } = ev;
  if (x.actor && (x.actor.login === null || x.actor.login === undefined)) x.actor = { kind: x.actor.kind };
  return x;
}
export function unpack(x) {
  const e = { schema: RF.EVENT_SCHEMA, id: '', ...x };
  e.id = RF.eventId(e);
  return e;
}

// ── the document ────────────────────────────────────────────────────────────────
export const emptyDoc = () => ({ meta: { schema: HISTORY_SCHEMA, backfill: null, rotation: null }, items: {}, events: [] });
// cm = the ConfigMap as read, or null when absent. A history this tick cannot read is
// never overwritten: { ok: false } stops the phase (fail closed).
export function parseDoc(cm) {
  if (!cm) return { ok: true, absent: true, doc: emptyDoc(), rv: null };
  const rv = cm.metadata?.resourceVersion ?? null;
  const d = cm.data || {};
  if (KEYS.every(k => d[k] === undefined)) return { ok: true, absent: false, doc: emptyDoc(), rv };
  const bad = why => ({ ok: false, why: `unreadable: ConfigMap upgrade-agent/upgrade-ledger: ${why}` });
  const meta = parse(d.meta ?? ''), items = parse(d.items ?? '');
  if (!meta || meta.schema !== HISTORY_SCHEMA) return bad(`data.meta is not a ${HISTORY_SCHEMA} header`);
  if (!items || typeof items !== 'object' || Array.isArray(items)) return bad('data.items is not an object');
  const events = [];
  const lines = String(d.events ?? '').split('\n').filter(l => l.trim());
  for (const [i, line] of lines.entries()) {
    const x = parse(line);
    if (!x || typeof x !== 'object' || Array.isArray(x)) return bad(`data.events line ${i + 1} is not a JSON object`);
    const ev = unpack(x);
    const errs = RF.checkEvent(ev);
    if (errs.length) return bad(`data.events line ${i + 1}: ${errs[0]}`);
    events.push(ev);
  }
  return { ok: true, absent: false, doc: { meta: { rotation: null, ...meta }, items, events }, rv };
}
export function renderData(doc) {
  return { events: doc.events.map(e => JSON.stringify(pack(e))).join('\n') + (doc.events.length ? '\n' : ''), items: JSON.stringify(doc.items), meta: JSON.stringify(doc.meta) };
}
export const historyManifest = (doc, rv) => ({ apiVersion: 'v1', kind: 'ConfigMap', metadata: { ...HISTORY, ...(rv ? { resourceVersion: String(rv) } : {}), labels: { ...LABELS } }, data: renderData(doc) });
export const initManifest = () => ({ apiVersion: 'v1', kind: 'ConfigMap', metadata: { ...HISTORY, labels: { ...LABELS } } });
// What the API server limits (1 MiB): the bytes of the data values. The request body is
// about a tenth larger (JSON escapes), well under etcd's 1.5 MiB request limit.
export const sizeOf = m => Object.values(m.data || {}).reduce((n, v) => n + Buffer.byteLength(String(v)), 0);

// ── replay: each item's state, from its base and its events, through applyEvent ────
const blank = id => ({ id, state: null, observed_at: null, first_seen: null, lane: null, class: null, reason: null, attempts: [], carrier: { pr: null } });
export function skeleton(id, base) {
  if (!base) return blank(id);
  const attempts = Array.from({ length: base.attempts || 0 }, (_, i) => ({ n: i + 1, drill: false, result: null, at: base.at, started_at: base.at }));
  if (attempts.length) Object.assign(attempts[attempts.length - 1], { drill: !!base.drill, result: base.result ?? null });
  return { ...blank(id), state: base.state, observed_at: base.at, first_seen: base.seen ?? base.at, lane: base.lane ?? null, class: base.class ?? null, attempts, carrier: { pr: base.pr ?? null } };
}
export function baseOf(s) {
  const it = s.item, last = it.attempts.at(-1);
  return { state: it.state, at: it.observed_at, seen: it.first_seen, lane: it.lane, class: it.class, pr: it.carrier?.pr ?? null, attempts: it.attempts.length, drill: !!last?.drill, result: last?.result ?? null, ...(s.refusal ? { refusal: s.refusal } : {}) };
}
const refusalKey = list => JSON.stringify([...(list || [])].sort());
export function replay(doc) {
  const items = new Map(), ids = new Set(), errors = [];
  for (const [id, d] of Object.entries(doc.items || {})) items.set(id, { item: skeleton(id, d.base), desc: d, refusal: d.base?.refusal ?? null });
  let dups = 0;
  for (const ev of doc.events) {
    if (ids.has(ev.id)) { dups++; continue; } // a retried write: the history keeps one
    ids.add(ev.id);
    let s = items.get(ev.item_id);
    if (!s) { s = { item: blank(ev.item_id), desc: null, refusal: null }; items.set(ev.item_id, s); }
    const r = RF.applyEvent(s.item, ev);
    if (r.errors.length) { errors.push(`${ev.id} (${ev.type} on ${ev.item_id}): ${r.errors[0]}`); continue; }
    s.item = r.item;
    if (ev.type === 'merge-refused') s.refusal = { head: ev.carrier?.head ?? null, key: refusalKey(ev.evidence?.pr_refusals) };
    else if (ev.type !== 'merge-attempted') s.refusal = null;
  }
  return { items, ids, errors, dups };
}
export function prIndex(st) {
  const by = new Map();
  for (const [id, s] of st.items) for (const pr of new Set([s.item.carrier?.pr, s.desc?.pr].filter(Number.isInteger))) by.set(pr, [...(by.get(pr) || []), id]);
  return by;
}

// ── a merged PR the lane never saw open: the backfill item (format fixture seq-backfill-*) ─
// The item is named from the title Renovate wrote: "<type>(<scope>): update [image|chart|
// dependency|...] <name> to <version>". A title without a version (a group) is named
// '<repo>#<pr>', as fromLaneRecord names a PR with no package table. Labels give the update
// type (type/<x>) and so the source: digest only is image-pin.
const TITLE = /^[a-z]+(?:\([^)]*\))?!?:\s+update\s+(?:(?:image|chart|dependency|helm release|module|docker tag)\s+)?(.+?)(?:\s+(?:action|image|digest))?\s+to\s+(\S+)$/i;
const TYPE_ORDER = ['patch', 'minor', 'major', 'digest', 'pin', 'pinDigest'];
export function backfillItem(e, repo) {
  const m = TITLE.exec(String(e.title || '').trim());
  const name = m ? m[1].trim() : `${repo}#${e.number}`;
  const to = m ? m[2] : null;
  const labels = (e.labels || []).map(l => (typeof l === 'string' ? l : l?.name)).filter(Boolean);
  const types = TYPE_ORDER.filter(t => labels.includes(`type/${t}`));
  const update = types.length ? types.join('+') : 'unknown';
  const source = types.length && types.every(t => t === 'digest' || t === 'pinDigest') ? 'image-pin' : 'renovate-pr';
  const id = RF.itemId({ source, item: { name, repo, packages: [] }, available: { version: to } });
  return { id, desc: { name, from: null, to, type: update, pr: e.number }, labels };
}
export const isRevertTitle = t => /^Revert "(.+)"$/.test(String(t || '').trim());

// ── the plan: derive this tick's events, apply them, compare, rotate (PURE) ─────────
// inputs: doc (parsed), records (the tick's lane records of open Renovate PRs), openPrs
// (every open Renovate PR this tick listed), verdict (summary.merge_step), at (the tick's
// time), scan ({ ok, list, window, why }: merged PRs from GitHub), fates (Map pr -> the PR
// as read when it left the open list, or null when unreadable), reverts (Map pr -> the PR
// number its body reverts, or null), backfilling (no marker yet).
export function planHistory({ doc, records, openPrs, verdict, at, repo, cfg, scan, fates = new Map(), reverts = new Map(), backfilling, renovateAuthors }) {
  const st = replay(doc);
  if (st.errors.length) return { ok: false, why: `the history does not replay (${st.errors.length} event(s) refused): ${st.errors[0]}` };
  const out = [];
  const notes = { unread: [], not_observed: [], shared_item: [], unrecorded: [], still_open: [], collapsed: [], duplicate: 0, revert_unmatched: [] };
  const descs = new Map();
  const state = id => { let s = st.items.get(id); if (!s) { s = { item: blank(id), desc: null, refusal: null }; st.items.set(id, s); } return s; };
  const add = (s, ev, pr) => {
    if (st.ids.has(ev.id)) { notes.duplicate++; return false; }
    const r = RF.applyEvent(s.item, ev);
    if (r.errors.length) { notes.unrecorded.push({ pr: pr ?? null, item: ev.item_id, type: ev.type, why: r.errors[0] }); return false; }
    s.item = r.item; st.ids.add(ev.id); out.push(ev);
    if (ev.type === 'merge-refused') s.refusal = { head: ev.carrier?.head ?? null, key: refusalKey(ev.evidence?.pr_refusals) };
    else if (ev.type !== 'merge-attempted') s.refusal = null;
    return true;
  };
  const renovate = cfg.actors.renovate[0];
  const openSet = new Set(openPrs);

  // 1. open PRs: first sight, or a new state, lane or class (one record per item: when
  // two open PRs carry the same item, the lowest PR speaks for it)
  const seen = new Map();
  for (const r of [...records].sort((a, b) => a.pr - b.pr)) {
    if (r.class === 'facts-incomplete') { notes.unread.push({ pr: r.pr, why: 'facts incomplete this tick: the item cannot be named; nothing written' }); continue; }
    let item;
    try { item = RF.fromLaneRecord(r, { repo, observed_at: at }); } catch (e) { notes.unrecorded.push({ pr: r.pr, why: e.message }); continue; }
    if (seen.has(item.id)) { notes.shared_item.push({ pr: r.pr, item: item.id, with: seen.get(item.id) }); continue; }
    seen.set(item.id, r.pr);
    const desc = { name: item.item.name, from: item.installed.version, to: item.available.version, type: item.available.update_type, pr: r.pr };
    // the PR now carries another version: its earlier item closes
    for (const [id, s] of st.items) {
      if (id === item.id || s.item.carrier?.pr !== r.pr || !OPEN.includes(s.item.state)) continue;
      add(s, makeEvent({ item_id: id, type: 'closed', from: s.item.state, to: 'closed', at: later(at, s.item.observed_at), actor: { kind: 'renovate', login: renovate }, recorded_by: 'lane',
        reason: reasonOf(`PR #${r.pr} now carries ${desc.name} ${desc.to ?? ''}: a newer version replaced this one`, cfg), carrier: { repo, pr: r.pr } }), r.pr);
    }
    const s = state(item.id), cur = s.item;
    if (cur.state === null || (OBSERVABLE.includes(cur.state) && (cur.state !== item.state || cur.lane !== item.lane || cur.class !== item.class))) {
      if (add(s, makeEvent({ item_id: item.id, type: 'observed', from: cur.state, to: item.state, at: cur.state === null ? at : later(at, cur.observed_at), actor: { kind: 'lane', login: null }, recorded_by: 'lane',
        lane: item.lane, class: item.class, reason: reasonOf(item.reason, cfg), carrier: { repo, pr: r.pr, head: r.head ?? null } }), r.pr)) descs.set(item.id, desc);
    } else if (!OBSERVABLE.includes(cur.state)) notes.not_observed.push({ pr: r.pr, item: item.id, state: cur.state });
    if (!s.desc) descs.set(item.id, desc);
  }

  // 2. the merge step's own events (live only; eventsFromMergeStep writes none in dry-run)
  if (verdict?.mode === 'live' && Number.isInteger(verdict.pr)) {
    const r = records.find(x => x.pr === verdict.pr);
    let item = null;
    try { item = r ? RF.fromLaneRecord(r, { repo, observed_at: at }) : null; } catch { item = null; }
    const s = item ? st.items.get(item.id) : null;
    if (s) {
      const evs = RF.eventsFromMergeStep({ id: item.id, lane: s.item.lane, attempts: s.item.attempts, carrier: { pr: verdict.pr } }, verdict, { repo, at });
      if (evs[1]?.type === 'merge-refused' && s.refusal && s.refusal.head === (verdict.head ?? null) && s.refusal.key === refusalKey(verdict.pr_refusals)) notes.collapsed.push({ pr: verdict.pr, head: verdict.head, why: 'refused again at the same head for the same reasons; recorded once' });
      else for (const ev of evs) add(s, ev, verdict.pr);
    } else if (['refused', 'merged', 'merge-failed', 'merge-unknown'].includes(verdict.verdict)) notes.unrecorded.push({ pr: verdict.pr, why: 'the merge step ran on a PR whose item the history does not hold' });
  }

  // 3. open items whose PR left the open list: merged or closed
  const scanList = scan?.ok ? scan.list : [];
  const scanByPr = new Map(scanList.map(e => [e.number, e]));
  const merged = (s, id, e, recordedBy) => {
    const a = actorOf(e.mergedBy?.login ?? e.mergedBy, cfg);
    const mc = e.mergeCommit?.oid ?? e.mergeCommit;
    if (!a) { notes.unrecorded.push({ pr: e.number, item: id, type: 'merged', why: 'who merged it could not be read' }); return false; }
    if (!SHA.test(mc || '')) { notes.unrecorded.push({ pr: e.number, item: id, type: 'merged', why: 'no merge commit was read' }); return false; }
    const when = s.item.observed_at ? later(iso(e.mergedAt), s.item.observed_at) : iso(e.mergedAt);
    const labels = (e.labels || []).map(l => (typeof l === 'string' ? l : l?.name)).filter(Boolean);
    // compact: the actor says who merged it, repo and pr say where; the labels are the reason
    return add(s, makeEvent({ item_id: id, type: 'merged', from: s.item.state, to: 'merged_unverified', at: when, actor: a, recorded_by: recordedBy,
      recorded_at: Date.parse(at) > Date.parse(when) ? at : undefined, attempt: s.item.attempts.length + 1,
      reason: labels.length ? reasonOf(`labels: ${labels.join(', ')}`, cfg) : undefined, carrier: { repo, pr: e.number }, evidence: { merge_commit: mc } }), e.number);
  };
  const terminals = [];
  for (const [id, s] of [...st.items]) {
    const pr = s.item.carrier?.pr;
    if (!OPEN.includes(s.item.state) || !Number.isInteger(pr) || openSet.has(pr)) continue;
    const f = scanByPr.has(pr) ? { state: 'MERGED', ...scanByPr.get(pr) } : fates.get(pr);
    if (!f) { notes.unread.push({ pr, item: id, why: 'the PR left the open list and its state could not be read; nothing written' }); continue; }
    if (f.state === 'MERGED') { if (merged(s, id, f, 'lane')) terminals.push(pr); }
    else if (f.state === 'CLOSED') {
      const a = actorOf(f.closedBy, cfg) || { kind: 'lane', login: null };
      const when = later(iso(f.closedAt || at), s.item.observed_at);
      if (add(s, makeEvent({ item_id: id, type: 'closed', from: s.item.state, to: 'closed', at: when, actor: a.kind === 'shepherd' ? { kind: 'hand', login: a.login } : a, recorded_by: 'lane',
        recorded_at: Date.parse(at) > Date.parse(when) ? at : undefined, reason: reasonOf(`PR #${pr} was closed without a merge${a.login ? ` by ${a.login}` : ''}`, cfg), carrier: { repo, pr } }), pr)) terminals.push(pr);
    } else notes.still_open.push({ pr, item: id, why: `GitHub reads it ${f.state || 'unknown'}, but the open list did not name it (a draft, or no longer Renovate's)` });
  }

  // 4. merged Renovate PRs the history does not hold yet (the backfill, and each tick's
  // catch-up): the first sight of the item is its merge
  const byPr = prIndex(st);
  const isRenovate = e => (renovateAuthors || []).includes(e.author?.login);
  for (const e of [...scanList].sort((a, b) => Date.parse(a.mergedAt) - Date.parse(b.mergedAt) || a.number - b.number)) {
    if (!isRenovate(e) || isRevertTitle(e.title) || byPr.has(e.number)) continue;
    const b = backfillItem(e, repo);
    const s = state(b.id);
    if (s.item.state !== null && !OPEN.includes(s.item.state)) { notes.shared_item.push({ pr: e.number, item: b.id, with: s.item.carrier?.pr ?? null, state: s.item.state }); continue; }
    if (merged(s, b.id, e, 'backfill')) { if (!s.desc) descs.set(b.id, b.desc); byPr.set(e.number, [b.id]); }
  }
  // 5. reverts: 'Revert "<title>"' whose body names the reverted PR (read per revert)
  for (const e of scanList.filter(x => isRevertTitle(x.title)).sort((a, b) => Date.parse(a.mergedAt) - Date.parse(b.mergedAt))) {
    const target = reverts.get(e.number);
    if (!Number.isInteger(target)) { notes.revert_unmatched.push({ pr: e.number, why: target === null ? 'its body could not be read' : 'its body names no reverted PR of this repository' }); continue; }
    const id = (prIndex(st).get(target) || []).find(x => REVERTABLE.includes(st.items.get(x).item.state));
    if (!id) { notes.revert_unmatched.push({ pr: e.number, reverts: target, why: 'the reverted PR carries no merged item in the history' }); continue; }
    const a = actorOf(e.mergedBy?.login ?? e.mergedBy, cfg), s = st.items.get(id), mc = e.mergeCommit?.oid ?? e.mergeCommit;
    if (!a || !['hand', 'person'].includes(a.kind)) { notes.unrecorded.push({ pr: e.number, item: id, type: 'revert-merged', why: `merged by ${a?.login ?? 'unknown'}: the format records a revert nobody opened as the lane's only for a hand run or a person` }); continue; }
    if (!SHA.test(mc || '')) { notes.unrecorded.push({ pr: e.number, item: id, type: 'revert-merged', why: 'no merge commit was read' }); continue; }
    const when = later(iso(e.mergedAt), s.item.observed_at);
    add(s, makeEvent({ item_id: id, type: 'revert-merged', from: s.item.state, to: 'reverted_unverified', at: when, actor: a, recorded_by: 'backfill', recorded_at: Date.parse(at) > Date.parse(when) ? at : undefined,
      attempt: s.item.attempts.length, reason: reasonOf(`reverted by #${e.number}: ${e.title}`, cfg), carrier: { repo, pr: target }, evidence: { revert_pr: e.number, revert_commit: mc } }), e.number);
  }

  // the new document: the events appended, each new item's description, the PR moved
  const next = { meta: structuredClone(doc.meta), items: structuredClone(doc.items), events: [...doc.events, ...out] };
  // descriptions leave out what is unknown (a backfilled item's from version, say)
  for (const [id, d] of descs) next.items[id] = { ...Object.fromEntries(Object.entries(d).filter(([, v]) => v !== null && v !== undefined)), ...(next.items[id]?.base ? { base: next.items[id].base } : {}) };
  for (const ev of out) if (next.items[ev.item_id] && Number.isInteger(ev.carrier?.pr)) next.items[ev.item_id].pr = ev.carrier.pr;
  const compare = scan?.ok ? compareCounts({ scan, st, events: next.events, renovateAuthors }) : null;
  if (backfilling && scan?.ok) next.meta.backfill = { at, window: scan.window, query: scan.query, events: out.filter(e => e.recorded_by === 'backfill').length, compare };
  const rot = rotate(next, cfg.max_bytes);
  if (!rot.ok) return { ok: false, why: rot.why };
  if (rot.dropped) rot.doc.meta.rotation = { at, dropped: rot.dropped, forgotten: rot.forgotten, oldest_kept: rot.doc.events[0]?.at ?? null };
  const changed = out.length > 0 || rot.dropped > 0 || JSON.stringify(doc.meta) !== JSON.stringify(rot.doc.meta) || JSON.stringify(doc.items) !== JSON.stringify(rot.doc.items);
  const terminalOf = new Map();
  for (const ev of out) if (TERMINAL.includes(ev.type) && Number.isInteger(ev.carrier?.pr) && (terminals.includes(ev.carrier.pr) || ev.recorded_by === 'lane')) {
    if (ev.type === 'closed' && openSet.has(ev.carrier.pr)) continue; // a newer version replaced it: the PR's record describes the new item
    terminalOf.set(ev.carrier.pr, { type: ev.type, at: ev.at, state: ev.to, by: ev.actor.login ?? ev.actor.kind, item: ev.item_id, event: ev.id, ...(ev.evidence?.merge_commit ? { merge_commit: ev.evidence.merge_commit } : {}) });
  }
  return { ok: true, doc: rot.doc, events: out, notes, compare, rotation: rot, changed, terminals: [...terminalOf].map(([pr, terminal]) => ({ pr, terminal })), bytes: rot.bytes };
}

// ── the count comparison: merged PRs as GitHub lists them, against the history ──────
export function compareCounts({ scan, st, events = [], renovateAuthors }) {
  const { since, until } = scan.window;
  const inWin = scan.list.filter(e => Date.parse(e.mergedAt) >= Date.parse(since) && Date.parse(e.mergedAt) <= Date.parse(until));
  const byAuthor = {};
  for (const e of inWin) byAuthor[e.author?.login ?? 'unknown'] = (byAuthor[e.author?.login ?? 'unknown'] || 0) + 1;
  const byPr = prIndex(st);
  const merged = id => AFTER_MERGE.includes(st.items.get(id)?.item.state);
  const ren = inWin.filter(e => (renovateAuthors || []).includes(e.author?.login) && !isRevertTitle(e.title));
  const own = [], shared = [], missing = [];
  for (const e of ren) {
    if ((byPr.get(e.number) || []).some(merged)) { own.push(e.number); continue; }
    // the same package and version merged through another PR: the format holds one item
    const b = backfillItem(e, scan.repo);
    if (merged(b.id) && st.items.get(b.id).item.carrier?.pr !== e.number) shared.push({ pr: e.number, item: b.id, recorded_via: st.items.get(b.id).item.carrier?.pr ?? null });
    else missing.push(e.number);
  }
  const reverts = inWin.filter(e => isRevertTitle(e.title));
  const revertPrs = new Set(events.filter(e => e.type === 'revert-merged').map(e => e.evidence?.revert_pr));
  return {
    window: { since, until }, query: scan.query, gh_merged: inWin.length, by_author: byAuthor, renovate: ren.length, recorded: own.length, shared_item: shared, missing,
    reverts: { titled: reverts.length, recorded: reverts.filter(e => revertPrs.has(e.number)).length },
    other_authors: inWin.length - ren.length - reverts.filter(e => (renovateAuthors || []).includes(e.author?.login)).length,
    match: missing.length === 0,
  };
}

// ── rotation: under max_bytes, oldest events first; bases keep what must be remembered ──
export function rotate(doc, maxBytes) {
  let size = sizeOf(historyManifest(doc, '1'));
  if (size <= maxBytes) return { ok: true, doc, dropped: 0, forgotten: 0, bytes: size };
  const items = structuredClone(doc.items);
  const st = new Map(Object.entries(items).map(([id, d]) => [id, { item: skeleton(id, d.base), refusal: d.base?.refusal ?? null }]));
  const left = new Map();
  for (const ev of doc.events) left.set(ev.item_id, (left.get(ev.item_id) || 0) + 1);
  let k = 0, forgotten = 0;
  // the step: the bytes an event frees on average, its item's description included (a
  // forgotten item goes with its last event); re-measured after each step, so it converges
  // on the cap from above without dropping much more than it must
  const avg = Math.max(64, Math.floor(size / Math.max(1, doc.events.length)));
  const touched = new Set();
  while (size > maxBytes && k < doc.events.length) {
    const upto = Math.min(doc.events.length, k + Math.max(1, Math.ceil((size - maxBytes) / avg)));
    for (; k < upto; k++) {
      const ev = doc.events[k];
      let s = st.get(ev.item_id);
      if (!s) { s = { item: blank(ev.item_id), refusal: null }; st.set(ev.item_id, s); }
      const r = RF.applyEvent(s.item, ev);
      if (!r.errors.length) {
        s.item = r.item;
        if (ev.type === 'merge-refused') s.refusal = { head: ev.carrier?.head ?? null, key: refusalKey(ev.evidence?.pr_refusals) };
        else if (ev.type !== 'merge-attempted') s.refusal = null;
      }
      left.set(ev.item_id, left.get(ev.item_id) - 1);
      touched.add(ev.item_id);
    }
    for (const id of touched) {
      const s = st.get(id);
      if (left.get(id) === 0 && FORGETTABLE.includes(s.item.state)) { if (items[id]) forgotten++; delete items[id]; continue; }
      items[id] = { ...(items[id] || { name: null, from: null, to: null, type: null, pr: s.item.carrier?.pr ?? null }), base: baseOf(s) };
    }
    touched.clear();
    size = sizeOf(historyManifest({ meta: doc.meta, items, events: doc.events.slice(k) }, '1'));
  }
  if (size > maxBytes) return { ok: false, why: `the history is ${size} bytes with every event rotated out, over its ${maxBytes}-byte cap (the remembered items alone)` };
  return { ok: true, doc: { meta: doc.meta, items, events: doc.events.slice(k) }, dropped: k, forgotten, bytes: size };
}

// ── reads (read-only runner) ──────────────────────────────────────────────────────
export function readMerged(repo, since, until, cfg, run) {
  const query = `merged:>=${since}`;
  const out = parse(tryRun(run, 'gh', ['pr', 'list', '-R', repo, '--state', 'merged', '--search', query, '--limit', String(cfg.max_search_results), '--json', SCAN_FIELDS]));
  const window = { since, until };
  const q = `gh pr list -R ${repo} --state merged --search "${query}" --limit ${cfg.max_search_results} --json number,author,mergedAt (then mergedAt <= ${until})`;
  if (!Array.isArray(out)) return { ok: false, why: 'unreadable: merged pull requests (gh pr list --state merged)', window, query: q };
  const okEntry = e => e && Number.isInteger(e.number) && Number.isFinite(Date.parse(e.mergedAt || '')) && typeof e.author?.login === 'string';
  if (!out.every(okEntry)) return { ok: false, why: 'unreadable: a merged pull request without a number, author or merge time', window, query: q };
  if (out.length >= cfg.max_search_results) return { ok: false, why: `the merged list reached GitHub's search limit (${out.length} of at most ${cfg.max_search_results}): the window may be incomplete`, window, query: q };
  return { ok: true, list: out.filter(e => Date.parse(e.mergedAt) <= Date.parse(until)), window, query: q, repo };
}
export function readFate(repo, pr, run) {
  const v = parse(tryRun(run, 'gh', ['pr', 'view', String(pr), '-R', repo, '--json', FATE_FIELDS]));
  if (!v || typeof v !== 'object' || !['OPEN', 'CLOSED', 'MERGED'].includes(v.state)) return null;
  let closedBy = null;
  if (v.state === 'CLOSED') closedBy = parse(tryRun(run, 'gh', ['api', `repos/${repo}/issues/${pr}`]))?.closed_by?.login ?? null;
  return { number: pr, state: v.state, mergedAt: v.mergedAt ?? null, mergedBy: v.mergedBy ?? null, mergeCommit: v.mergeCommit ?? null, closedAt: v.closedAt ?? null, closedBy, headRefOid: v.headRefOid ?? null, labels: [] };
}
export function readRevertTarget(repo, pr, run) {
  const v = parse(tryRun(run, 'gh', ['pr', 'view', String(pr), '-R', repo, '--json', 'body']));
  if (!v || typeof v.body !== 'string') return null;
  const m = /\bReverts\s+(?:([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+))?#(\d+)\b/.exec(v.body);
  return m && (!m[1] || m[1] === repo) ? Number(m[2]) : undefined;
}
// where the catch-up starts: the newest merge the history holds (or the backfill's end),
// less a margin for GitHub's search index; never before the backfill's start
export function catchUpSince(doc, cfg) {
  const ends = doc.events.filter(e => e.type === 'merged' || e.type === 'revert-merged').map(e => Date.parse(e.at));
  const bf = doc.meta.backfill?.window;
  const newest = Math.max(...ends, Date.parse(bf?.until || ''));
  const since = newest - cfg.catch_up_margin_hours * 3600e3;
  return iso(Math.max(since, Date.parse(bf?.since || '') || -Infinity));
}

// ── THE WRITE: one spawn, ConfigMap upgrade-agent/upgrade-ledger only ─────────────────
export function applyHistory(manifest, manager, env) {
  const m = manifest?.metadata || {};
  const keys = Object.keys(manifest?.data || {}).sort().join();
  if (manifest?.kind !== 'ConfigMap' || m.name !== HISTORY.name || m.namespace !== HISTORY.namespace || Object.keys(manifest).some(k => !['apiVersion', 'kind', 'metadata', 'data'].includes(k))
    || !(manager === MANAGER ? keys === [...KEYS].sort().join() && /^\d+$/.test(String(m.resourceVersion || '')) : manager === INIT_MANAGER ? !manifest.data && !m.resourceVersion : false)) {
    throw new Error('applyHistory: refused (only ConfigMap upgrade-agent/upgrade-ledger: its events, items and meta at a resourceVersion under upgrade-night-history, or an empty create under upgrade-night-history-init)');
  }
  const body = JSON.stringify(manifest);
  const r = spawnSync('kubectl', ['apply', '--server-side', `--field-manager=${manager}`, '--force-conflicts', '-o', 'json', '-f', '-'], { input: body, encoding: 'utf8', env, maxBuffer: 64 * 1024 * 1024, timeout: KUBECTL_TIMEOUT_MS, killSignal: 'SIGKILL' });
  if (r.status !== 0) {
    const raw = String(r.stderr || r.error?.message || '');
    const conflict = /\(Conflict\)|the object has been modified/i.test(raw);
    // a fixed vocabulary for the state; kubectl's own text goes to the Job log only (raw)
    const detail = r.status === null ? 'timed out or killed' : conflict ? 'conflict (HTTP 409)' : /\(Forbidden\)|forbidden/i.test(raw) ? 'refused by the API server (Forbidden)'
      : /\(NotFound\)/.test(raw) ? 'not found' : /\(Invalid\)|\(TooLong\)|too long|exceeds/i.test(raw) ? 'rejected by the API server as invalid or too large' : `kubectl exited ${r.status}`;
    return { ok: false, conflict, detail, raw: `${M.publicDetail(raw)}` };
  }
  const rv = parse(r.stdout)?.metadata?.resourceVersion ?? null;
  return { ok: !!rv, rv, conflict: false, detail: rv ? null : 'the apply returned no resourceVersion' };
}

// ── the phase: read, derive, write (run-night.mjs calls it once per tick) ─────────────
// Never throws for a read or write failure: the summary says what happened. write =
// false (a local run): plans and writes nothing.
export function historyPhase({ rules, run, env, repo, at, records, openPrs, verdict, write, apply = applyHistory }) {
  const sum = { configmap: `${HISTORY.namespace}/${HISTORY.name}`, read: null, written: false, write: 'not-attempted', why_not: null, events: 0, derived: 0, by_type: {}, total: null, backfill: null, catch_up: null, rotation: null, notes: null, terminals: [] };
  const cfg = historyConfig(rules);
  if (!cfg.ok) return { summary: { ...sum, why_not: cfg.why }, terminals: [] };
  const read = M.readNamedConfigMap(HISTORY.name, run);
  if (!read) return { summary: { ...sum, read: 'unreadable', why_not: 'unreadable: ConfigMap upgrade-agent/upgrade-ledger; nothing derived or written' }, terminals: [] };
  const p = parseDoc(read.found ? read.cm : null);
  sum.read = !read.found ? 'absent (a new history)' : p.ok ? `read at resourceVersion ${p.rv}` : 'refused';
  if (!p.ok) return { summary: { ...sum, why_not: `${p.why}; nothing written (a history this tick cannot read is not overwritten)` }, terminals: [] };
  const backfilling = !p.doc.meta.backfill;
  const since = backfilling ? iso(Date.parse(at) - cfg.backfill_days * 864e5) : catchUpSince(p.doc, cfg);
  const scan = readMerged(repo, since, at, cfg, run);
  const st0 = replay(p.doc);
  const openSet = new Set(openPrs);
  const fates = new Map(), reverts = new Map();
  const scanPrs = new Set(scan.ok ? scan.list.map(e => e.number) : []);
  for (const [, s] of st0.items) {
    const pr = s.item.carrier?.pr;
    if (OPEN.includes(s.item.state) && Number.isInteger(pr) && !openSet.has(pr) && !scanPrs.has(pr) && !fates.has(pr)) fates.set(pr, readFate(repo, pr, run));
  }
  if (scan.ok) for (const e of scan.list.filter(x => isRevertTitle(x.title))) reverts.set(e.number, readRevertTarget(repo, e.number, run));
  const plan = planHistory({ doc: p.doc, records, openPrs, verdict, at, repo, cfg, scan, fates, reverts, backfilling, renovateAuthors: rules.renovate_pr_authors || [] });
  if (!plan.ok) return { summary: { ...sum, why_not: `${plan.why}; nothing written` }, terminals: [] };
  for (const ev of plan.events) sum.by_type[ev.type] = (sum.by_type[ev.type] || 0) + 1;
  sum.derived = plan.events.length; // events counts what was written
  sum.total = { events: plan.doc.events.length, items: Object.keys(plan.doc.items).length, bytes: plan.bytes, cap: cfg.max_bytes, oldest: plan.doc.events[0]?.at ?? null };
  sum.rotation = plan.rotation.dropped ? { dropped: plan.rotation.dropped, forgotten: plan.rotation.forgotten } : null;
  sum.backfill = backfilling ? (scan.ok ? { state: 'this tick', window: scan.window, events: plan.doc.meta.backfill.events, compare: plan.compare } : { state: 'not yet', why: scan.why, window: scan.window })
    : { state: 'done', at: p.doc.meta.backfill.at, window: p.doc.meta.backfill.window, compare: p.doc.meta.backfill.compare };
  sum.catch_up = backfilling ? null : scan.ok ? { window: scan.window, ...pick(plan.compare, ['gh_merged', 'renovate', 'recorded', 'shared_item', 'missing', 'reverts', 'match']) } : { window: scan.window, why: scan.why };
  const n = plan.notes;
  sum.notes = Object.fromEntries(Object.entries(n).filter(([, v]) => (Array.isArray(v) ? v.length : v)));
  const planned = plan.terminals;
  if (!write) return { summary: { ...sum, write: 'planned', why_not: 'a local run (no --write-state): planned, nothing written', terminals: planned.map(t => t.pr) }, terminals: planned, plan };
  sum.total.written = false;
  if (!plan.changed) return { summary: { ...sum, write: 'not-needed', why_not: 'nothing new this tick' }, terminals: [] };
  let rv = p.rv;
  if (!read.found) {
    // create the object empty first (create-via-apply ignores a resourceVersion), then
    // write at the version that create returned: a second first writer meets a conflict
    const w0 = apply(initManifest(), INIT_MANAGER, env);
    if (!w0.ok) return { summary: { ...sum, write: w0.conflict ? 'conflict' : 'failed', why_not: `the empty history could not be created (${w0.detail}); nothing written, the next tick derives the same events`, raw: w0.raw }, terminals: [] };
    rv = w0.rv;
  }
  const w = apply(historyManifest(plan.doc, rv), MANAGER, env);
  if (!w.ok) return { summary: { ...sum, write: w.conflict ? 'conflict' : 'failed', why_not: w.conflict ? `another writer changed upgrade-ledger after this tick read it (resourceVersion ${rv}); nothing written, the next tick derives the same events` : `the write failed (${w.detail}); nothing written, the next tick derives the same events`, raw: w.raw }, terminals: [] };
  return { summary: { ...sum, written: true, write: 'written', events: plan.events.length, rv: w.rv, total: { ...sum.total, written: true }, terminals: planned.map(t => t.pr) }, terminals: planned, plan };
}
const pick = (o, keys) => Object.fromEntries(keys.filter(k => o && k in o).map(k => [k, o[k]]));

// The tick's log line (stderr).
export function historyLine(h) {
  if (!h) return 'not run';
  if (h.error) return `FAILED: ${h.error}`;
  const types = Object.entries(h.by_type || {}).map(([k, v]) => `${k} ${v}`).join(', ');
  const bf = h.backfill?.state === 'this tick' ? `; backfill this tick: ${h.backfill.events} merged events, ${cmpLine(h.backfill.compare)}` : h.backfill?.state === 'not yet' ? `; backfill not yet: ${h.backfill.why}` : '';
  const cu = h.catch_up ? (h.catch_up.why ? `; catch-up unreadable: ${h.catch_up.why}` : `; catch-up since ${h.catch_up.window.since}: ${cmpLine(h.catch_up)}`) : '';
  const tot = h.total ? `; ${h.write === 'written' ? 'holds' : 'would hold'} ${h.total.events} events, ${h.total.items} items, ${h.total.bytes} of ${h.total.cap} bytes` : '';
  const n = h.write === 'written' ? `${h.events} new event${h.events === 1 ? '' : 's'}${types ? ` (${types})` : ''}` : `${h.derived || 0} event${h.derived === 1 ? '' : 's'} derived${types ? ` (${types})` : ''}, none written`;
  return `${h.write.toUpperCase()}: ${n}${h.why_not ? `; ${h.why_not}` : ''}${bf}${cu}${tot}${h.rotation ? `; rotated out ${h.rotation.dropped} events, forgot ${h.rotation.forgotten} items` : ''}`;
}
const cmpLine = c => (c ? `GitHub lists ${c.gh_merged} merged (${c.renovate} Renovate), the history holds ${c.recorded}${c.shared_item?.length ? ` + ${c.shared_item.length} through another PR` : ''}, missing ${c.missing.length ? c.missing.map(x => `#${x}`).join(', ') : 'none'}${c.match ? '' : ' (MISMATCH)'}` : 'no comparison');

// The PR-record items for PRs that left the open list this tick with a terminal event
// (story 593 (d)): the lane's own record, with terminal set; edit only, never a new
// comment on a closed PR. Comments unreadable: the writer refuses it with the reason.
export function terminalRecordItems(terminals, openPrs, rcd, at, run, repo) {
  const items = [], skipped = [];
  for (const t of terminals.filter(x => !openPrs.includes(x.pr))) {
    const raw = M.readIssueComments(repo, t.pr, run);
    const comments = raw ? L.recordComments(raw) : null;
    const own = comments ? M.ownRecords(comments, rcd).own : [];
    const cur = own.length ? M.parseRecord(own[0].body, rcd) : null;
    if (own.length && !(cur && cur.pr === t.pr)) { skipped.push({ pr: t.pr, why: 'the lane record on this PR cannot be parsed; not edited' }); continue; }
    // no own record (or comments unreadable): the writer never creates one for an edit-only item
    items.push({ rec: cur ? { ...cur, terminal: t.terminal, updated: at } : { schema: 'haynes.upgrade-lane/v1', pr: t.pr, terminal: t.terminal, updated: at }, comments, edit_only: true });
  }
  return { items, skipped };
}
