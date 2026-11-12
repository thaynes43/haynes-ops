// upgrade-history.mjs — the history of every attempt: ConfigMap upgrade-agent/upgrade-ledger (story 593).
//
// Story: https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/593
// Runbook: .agents/runbooks/upgrade-night-lane.md, "The history of every attempt (story 593)".
// Format: .agents/reference/upgrade-records.md. Node 24 only, no dependencies.
//
// WHAT IT IS FOR. The history remembers: every attempt, and the item states that outlive
// it (reverted: never merge this version again). Nothing reads it before a merge yet;
// refusing a remembered version is stories 753 and 556.
//
// A THIN LAYER OVER THE RECORD FORMAT. Every event is a haynes.upgrade-event/v1 record
// checked by record-format.mjs (eventId, applyEvent's state machine), live merge-step
// verdicts become events through eventsFromMergeStep(), and EVERY item, open or merged, is
// named by the lane's own route: the classifier's facts (the package table, labels, title,
// files) through fromLaneRecord(). Only when a PR's body cannot be read is an item named
// from its title, and it is marked basis 'title'. record-format.mjs and its four JSON files
// here are byte-identical copies of scripts/upgrade-agent/schema/ (the record-format
// selftest fails on drift).
//
// WHAT A TICK WRITES, each once: observed (an open Renovate PR's item first seen, or a new
// state, lane or class); closed (a PR closed without a merge, or an item a newer version
// replaced on its PR); merged (by someone other than the lane, on the item that was merged:
// when the PR moved to a newer version first, the old item closes and the merge goes on the
// new one); revert-merged (a 'Revert "<title>"' PR whose body names a PR of that title); in
// live mode the merge step's events, a repeated refusal collapsed. Nothing for an unchanged
// item, a record whose facts were not read, or a fact this tick could not read (an unread
// closer included): the next tick reads it again.
//
// THE CONFIGMAPS (runtime state, not Flux-managed).
//   upgrade-ledger: data.events (one event per line without schema, id and a null login;
//     unpack() restores them), data.items (per item: name, from, to, type, pr, basis, base =
//     its state once earlier events rotated out, mirrored = the terminal event its PR
//     record carries), data.meta (schema, the backfill marker and its count comparison, the
//     last rotation, merges seen on remembered items, the mirror halt).
//   upgrade-ledger-marker: data.marker, that a history exists (created_at, backfilled_at).
//     A marker with upgrade-ledger absent or empty means the history was lost: the tick
//     writes nothing and says so on every tick until a person restores it or clears the marker.
// ROTATION keeps upgrade-ledger's data under night.history.max_bytes, oldest events first;
// an item keeps its state in items[id].base. Forgotten, once no event is left, are only:
// closed, done, and merged_unverified when someone other than the lane merged it.
//
// THE WRITES. One spawn site, applyHistory(): kubectl apply --server-side of these two
// ConfigMaps only. upgrade-ledger under upgrade-night-history, always at the
// resourceVersion read (store.go Store.Update answers 409 Conflict on a stale one); when it
// is missing, first an empty create under upgrade-night-history-init, and a create that
// finds data is a conflict. A conflict or failure writes nothing; ids are deterministic, so
// the next tick derives the same events. The marker under upgrade-night-history-marker.
//
// BUDGET. Per-PR reads per tick are capped (night.history.max_pr_reads_per_tick) and no
// read starts once the tick has run time_budget_seconds: the rest carries over (no backfill
// marker until it is done).
import { spawnSync } from 'node:child_process';
import * as RF from './record-format.mjs';
import * as L from './lane-classify.mjs';
import * as M from './night-merge.mjs';

export const HISTORY = Object.freeze({ name: 'upgrade-ledger', namespace: 'upgrade-agent' });
export const MARKER = Object.freeze({ name: 'upgrade-ledger-marker', namespace: 'upgrade-agent' });
export const HISTORY_SCHEMA = 'haynes.upgrade-ledger/v1';
export const MARKER_SCHEMA = 'haynes.upgrade-ledger-marker/v1';
export const MANAGER = 'upgrade-night-history';
export const INIT_MANAGER = 'upgrade-night-history-init';
export const MARKER_MANAGER = 'upgrade-night-history-marker';
export const KEYS = Object.freeze(['events', 'items', 'meta']);
const LABELS = Object.freeze({ 'app.kubernetes.io/name': 'upgrade-shepherd', 'app.kubernetes.io/component': 'upgrade-history' });
export const OPEN = Object.freeze(['needed', 'held', 'blocked', 'unknown']);
export const OBSERVABLE = Object.freeze([...OPEN, 'closed']);
export const STICKY = Object.freeze(['reverted']);
const REVERTABLE = ['failed', 'merged_unverified', 'awaiting_pin', 'done'];
const AFTER_MERGE = ['merged_unverified', 'awaiting_pin', 'done', 'failed', 'reverting', 'reverted_unverified', 'reverted', 'escalated'];
const TERMINAL = ['merged', 'closed'];
const SHA = /^[0-9a-f]{40}$/;
const LIST_FIELDS = 'number,title,author,mergedAt,mergedBy,labels,mergeCommit,headRefOid,body,files';
const REVERT_FIELDS = 'number,title,author,mergedAt,mergedBy,mergeCommit';
const FATE_FIELDS = 'number,state,title,author,mergedAt,mergedBy,mergeCommit,closedAt,headRefOid,labels,body,files';
const KUBECTL_TIMEOUT_MS = 30e3;
const MAX_SPLITS = 6;
const iso = v => new Date(typeof v === 'number' ? v : Date.parse(v)).toISOString();
const later = (a, b) => (!b || Date.parse(a) >= Date.parse(b) ? a : b);
const parse = s => { try { return JSON.parse(s); } catch { return null; } };
const tryRun = (run, cmd, argv) => { try { return run(cmd, argv, { allowFail: true }); } catch { return null; } };
const labelNames = e => (e?.labels || []).map(l => (typeof l === 'string' ? l : l?.name)).filter(Boolean);

// ── config: night.history in upgrade-rules.json ───────────────────────────────────
export function historyConfig(rules) {
  const c = rules?.night?.history || {};
  const int = (v, lo, hi) => Number.isInteger(v) && v >= lo && v <= hi;
  const logins = v => Array.isArray(v) && v.length > 0 && v.every(x => typeof x === 'string' && /^[A-Za-z0-9-]+(\[bot\])?$/.test(x));
  const a = c.actors || {};
  const ok = c.configmap === HISTORY.name && c.marker_configmap === MARKER.name && int(c.max_bytes, 65536, 917504) && int(c.backfill_days, 1, 60) && int(c.catch_up_margin_hours, 1, 168)
    && int(c.max_search_results, 100, 1000) && int(c.max_reason_chars, 40, 1000) && int(c.max_pr_reads_per_tick, 1, 200) && int(c.max_mirrors_per_tick, 0, 50) && int(c.time_budget_seconds, 30, 480)
    && logins(a.renovate) && logins(a.shepherd) && logins(a.hand);
  return { ok, ...c, actors: a, why: ok ? null : 'night.history in upgrade-rules.json is invalid (configmap upgrade-ledger; marker_configmap upgrade-ledger-marker; 65536 <= max_bytes <= 917504; backfill_days 1 to 60; catch_up_margin_hours 1 to 168; max_search_results 100 to 1000; max_reason_chars 40 to 1000; max_pr_reads_per_tick 1 to 200; max_mirrors_per_tick 0 to 50; time_budget_seconds 30 to 480; actors renovate, shepherd and hand as login lists)' };
}

// ── actors ──────────────────────────────────────────────────────────────────────
// gh's GraphQL reads an app as app/<name>; REST, and the format, as <name>[bot]. The format
// has five actor kinds; another bot is 'hand' (an automated session-like actor), its login kept.
export const normLogin = l => (typeof l === 'string' && l ? (l.startsWith('app/') ? `${l.slice(4)}[bot]` : l) : null);
export function actorOf(login, cfg) {
  const l = normLogin(login);
  if (!l) return null;
  for (const kind of ['renovate', 'shepherd', 'hand']) if ((cfg.actors?.[kind] || []).includes(l)) return { kind, login: l };
  return { kind: /\[bot\]$/.test(l) ? 'hand' : 'person', login: l };
}
const reasonOf = (s, cfg) => { const t = String(s ?? '').replace(/\s+/g, ' ').trim() || 'no reason recorded'; return t.length > cfg.max_reason_chars ? `${t.slice(0, cfg.max_reason_chars - 1)}…` : t; };

// ── events ──────────────────────────────────────────────────────────────────────
export function makeEvent(f) {
  const e = { schema: RF.EVENT_SCHEMA, id: '', ...f };
  for (const k of Object.keys(e)) if (e[k] === undefined) delete e[k];
  e.id = RF.eventId(e);
  return e;
}
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

// ── the documents ───────────────────────────────────────────────────────────────
export const emptyDoc = () => ({ meta: { schema: HISTORY_SCHEMA, backfill: null, rotation: null }, items: {}, events: [] });
export function parseDoc(cm) {
  if (!cm) return { ok: true, absent: true, empty: true, doc: emptyDoc(), rv: null };
  const rv = cm.metadata?.resourceVersion ?? null;
  const d = cm.data || {};
  if (KEYS.every(k => d[k] === undefined)) return { ok: true, absent: false, empty: true, doc: emptyDoc(), rv };
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
  return { ok: true, absent: false, empty: false, doc: { meta: { rotation: null, ...meta }, items, events }, rv };
}
// The marker: null = absent; { ok: false } = present but not a marker (refused, fail closed).
export function parseMarker(cm) {
  if (!cm) return { ok: true, marker: null };
  const m = parse(cm.data?.marker ?? '');
  if (!m || m.schema !== MARKER_SCHEMA || !Number.isFinite(Date.parse(m.created_at || ''))) return { ok: false, why: 'unreadable: ConfigMap upgrade-agent/upgrade-ledger-marker is not a haynes.upgrade-ledger-marker/v1 marker' };
  return { ok: true, marker: m };
}
export function renderData(doc) {
  return { events: doc.events.map(e => JSON.stringify(pack(e))).join('\n') + (doc.events.length ? '\n' : ''), items: JSON.stringify(doc.items), meta: JSON.stringify(doc.meta) };
}
export const historyManifest = (doc, rv) => ({ apiVersion: 'v1', kind: 'ConfigMap', metadata: { ...HISTORY, ...(rv ? { resourceVersion: String(rv) } : {}), labels: { ...LABELS } }, data: renderData(doc) });
export const initManifest = () => ({ apiVersion: 'v1', kind: 'ConfigMap', metadata: { ...HISTORY, labels: { ...LABELS } } });
export const markerManifest = m => ({ apiVersion: 'v1', kind: 'ConfigMap', metadata: { ...MARKER, labels: { ...LABELS } }, data: { marker: JSON.stringify(m) } });
// What the API server limits (1 MiB): the bytes of the data values.
export const sizeOf = m => Object.values(m.data || {}).reduce((n, v) => n + Buffer.byteLength(String(v)), 0);

// ── replay ──────────────────────────────────────────────────────────────────────
const blank = id => ({ id, state: null, observed_at: null, first_seen: null, lane: null, class: null, reason: null, attempts: [], carrier: { pr: null } });
export function skeleton(id, base) {
  if (!base) return blank(id);
  const attempts = Array.from({ length: base.attempts || 0 }, (_, i) => ({ n: i + 1, actor: { kind: null }, drill: false, result: null, at: base.at, started_at: base.at }));
  if (attempts.length) Object.assign(attempts[attempts.length - 1], { drill: !!base.drill, result: base.result ?? null, actor: { kind: base.by ?? null } });
  return { ...blank(id), state: base.state, observed_at: base.at, first_seen: base.seen ?? base.at, lane: base.lane ?? null, class: base.class ?? null, attempts, carrier: { pr: base.pr ?? null } };
}
export function baseOf(s) {
  const it = s.item, last = it.attempts.at(-1);
  return { state: it.state, at: it.observed_at, seen: it.first_seen, lane: it.lane, class: it.class, pr: it.carrier?.pr ?? null, attempts: it.attempts.length, drill: !!last?.drill, result: last?.result ?? null, by: last?.actor?.kind ?? null, ...(s.refusal ? { refusal: s.refusal } : {}) };
}
// forgotten once no event is left: nothing owed, nothing to remember. merged_unverified
// owes verification (story 553) only for a merge the lane made.
export const forgettable = it => it.state === 'closed' || it.state === 'done' || (it.state === 'merged_unverified' && it.attempts.at(-1)?.actor?.kind !== 'lane');
const refusalKey = list => JSON.stringify([...(list || [])].sort());
const track = (s, ev) => {
  if (ev.type === 'merge-refused') s.refusal = { head: ev.carrier?.head ?? null, key: refusalKey(ev.evidence?.pr_refusals) };
  else if (ev.type !== 'merge-attempted') s.refusal = null;
};
export function replay(doc) {
  const items = new Map(), ids = new Set(), errors = [];
  for (const [id, d] of Object.entries(doc.items || {})) items.set(id, { item: skeleton(id, d.base), desc: d, refusal: d.base?.refusal ?? null });
  let dups = 0;
  for (const ev of doc.events) {
    if (ids.has(ev.id)) { dups++; continue; }
    ids.add(ev.id);
    let s = items.get(ev.item_id);
    if (!s) { s = { item: blank(ev.item_id), desc: null, refusal: null }; items.set(ev.item_id, s); }
    const r = RF.applyEvent(s.item, ev);
    if (r.errors.length) { errors.push(`${ev.item_id} (${ev.type} at ${ev.at}): ${r.errors[0]}`); continue; }
    s.item = r.item;
    track(s, ev);
  }
  return { items, ids, errors, dups };
}
export function prIndex(st) {
  const by = new Map();
  for (const [id, s] of st.items) for (const pr of new Set([s.item.carrier?.pr, s.desc?.pr].filter(Number.isInteger))) by.set(pr, [...(by.get(pr) || []), id]);
  return by;
}

// The fair order for what stays unread (fates, mirrors): the least recently tried first, so
// one stuck PR cannot hold a slot on every tick. tried = meta.tried.<kind>: { "<pr>": at }.
export const fairOrder = (prs, tried = {}) => [...prs].sort((a, b) => String(tried[a] ?? '').localeCompare(String(tried[b] ?? '')) || a - b);

// ── the item of a PR, by the lane's route ───────────────────────────────────────────
// e: a PR as gh reads it (title, labels, body, files). The same facts the classifier reads
// (parsePackageTable, updateTypes, clusterEffect) through fromLaneRecord, so a merged PR's
// item is the item the lane computes for the same update while it is open. With no body
// read, the item is named from the title ('<type>(<scope>): update ... <name> to <version>',
// else '<repo>#<pr>') and marked basis 'title'.
const TITLE = /^[a-z]+(?:\([^)]*\))?!?:\s+update\s+(?:(?:image|chart|dependency|helm release|module|docker tag)\s+)?(.+?)(?:\s+(?:action|image|digest))?\s+to\s+(\S+)$/i;
export function laneItem(e, repo, rules, at) {
  const labels = labelNames(e);
  // no body read, or a body that gives no package table (edited to empty, say): the title
  // route, marked basis title, and never taken as a move to a newer version
  let packages = typeof e.body === 'string' ? L.parsePackageTable(e.body) : [];
  const byTitle = !packages.length;
  let name = null, to = null;
  if (byTitle) { const m = TITLE.exec(String(e.title || '').trim()); if (m) { name = m[1].trim(); to = m[2]; } }
  else { const pk = packages[0] || {}; name = pk.name ?? null; to = pk.to ?? null; }
  const files = (e.files || []).map(f => (typeof f === 'string' ? f : f?.path)).filter(Boolean);
  const types = L.updateTypes({ labels, packages, title: e.title || '' });
  const ce = files.length ? L.clusterEffect(files, rules) : { own_images: [], targets: [] };
  const pk = packages[0] || {};
  const rec = { schema: RF.LANE_SCHEMA, pr: e.number, package: name, from: byTitle ? null : pk.from ?? null, to, packages, update_type: types.join('+') || 'unknown', own_images: ce.own_images, targets: ce.targets,
    lane: 'LANE-A', class: 'merged', reason: 'merged', rules: [], findings: [], checks: {}, updated: at };
  const it = RF.fromLaneRecord(rec, { repo, observed_at: at });
  return { id: it.id, basis: byTitle ? 'title' : 'package-table', desc: { name: it.item.name, from: it.installed.version, to: it.available.version, type: it.available.update_type, pr: e.number, ...(byTitle ? { basis: 'title' } : {}) } };
}
// Revert titles: GitHub's revert button writes 'Revert "<the reverted PR's title>"'.
const REVERT = /^Revert "(.+)"$/;
export const revertedTitle = t => REVERT.exec(String(t || '').trim())?.[1] ?? null;
export const isRevertTitle = t => revertedTitle(t) !== null;

// ── the budget: per-PR reads and the tick's own time (the Job's deadline) ────────────
export function makeBudget(cfg, clock = Date.now, startedAt = clock()) {
  let reads = 0, stopped = null;
  const left = () => cfg.time_budget_seconds * 1e3 - (clock() - startedAt);
  return {
    time() { if (left() > 0) return true; stopped = stopped || `the tick has run ${cfg.time_budget_seconds}s (time_budget_seconds); the rest is read next tick`; return false; },
    pr() { if (!this.time()) return false; if (reads >= cfg.max_pr_reads_per_tick) { stopped = stopped || `${cfg.max_pr_reads_per_tick} per-PR reads used this tick (max_pr_reads_per_tick); the rest is read next tick`; return false; } reads++; return true; },
    get reads() { return reads; }, get stopped() { return stopped; },
  };
}

// ── reads (read-only runner) ──────────────────────────────────────────────────────
const enc = s => encodeURIComponent(s);
// GitHub's own count for a search: the number the comparison checks the history against.
export function readCount(repo, q, run) {
  const j = parse(tryRun(run, 'gh', ['api', `search/issues?q=${enc(`repo:${repo} is:pr is:merged ${q}`)}&per_page=1`]));
  return Number.isInteger(j?.total_count) && j.incomplete_results !== true ? j.total_count : null;
}
// One list of merged PRs for a query; split in halves while a list comes back at the
// search limit (never take a cut list as complete).
function listSplit(repo, q, since, until, cfg, run, budget, fields, depth = 0) {
  if (!budget.time()) return { ok: false, why: budget.stopped };
  const out = parse(tryRun(run, 'gh', ['pr', 'list', '-R', repo, '--state', 'merged', '--search', `${q}merged:${since}..${until}`.trim(), '--limit', String(cfg.max_search_results), '--json', fields]));
  if (!Array.isArray(out)) return { ok: false, why: 'unreadable: merged pull requests (gh pr list --state merged)' };
  if (!out.every(e => e && Number.isInteger(e.number) && Number.isFinite(Date.parse(e.mergedAt || '')) && typeof e.author?.login === 'string')) return { ok: false, why: 'unreadable: a merged pull request without a number, author or merge time' };
  if (out.length < cfg.max_search_results) return { ok: true, list: out, splits: depth };
  if (depth >= MAX_SPLITS) return { ok: false, why: `a list of ${out.length} merged pull requests reached the search limit after ${depth} splits; the window is not taken as complete` };
  const mid = iso(Math.floor((Date.parse(since) + Date.parse(until)) / 2));
  const a = listSplit(repo, q, since, mid, cfg, run, budget, fields, depth + 1);
  if (!a.ok) return a;
  // GitHub honours sub-second bounds: the second half starts a millisecond after the middle
  const b = listSplit(repo, q, iso(Date.parse(mid) + 1), until, cfg, run, budget, fields, depth + 1);
  if (!b.ok) return b;
  return { ok: true, list: [...a.list, ...b.list], splits: Math.max(a.splits, b.splits) };
}
// The merged pull requests that become items (Renovate's, with what the lane's route needs)
// and the revert candidates (any author, 'Revert' in the title), and GitHub's own count of
// the first for the same window.
export function readMerged(repo, since, until, cfg, run, budget, authors) {
  const window = { since, until };
  const queries = authors.map(a => `author:${a} `);
  const query = queries.map(q => `${q.trim()} merged:${since}..${until}`).join(' + ');
  const list = [];
  let splits = 0;
  for (const q of queries) {
    const r = listSplit(repo, q, since, until, cfg, run, budget, LIST_FIELDS);
    if (!r.ok) return { ok: false, why: r.why, window, query };
    list.push(...r.list); splits = Math.max(splits, r.splits);
  }
  const rv = listSplit(repo, 'Revert in:title ', since, until, cfg, run, budget, REVERT_FIELDS);
  if (!rv.ok) return { ok: false, why: rv.why, window, query };
  const counts = budget.time() ? queries.map(q => readCount(repo, `${q}merged:${since}..${until}`, run)) : [null];
  const github = counts.every(Number.isInteger) ? counts.reduce((a, b) => a + b, 0) : null;
  const seen = new Set();
  const uniq = list.filter(e => !seen.has(e.number) && seen.add(e.number));
  return { ok: true, list: uniq, reverts: rv.list.filter(e => isRevertTitle(e.title)), github_count: github, window, query, splits, repo };
}
// A PR that left the open list: its state and the facts its item is named by. null =
// unreadable. closedBy null with closed_by_read false = the closer could not be read.
export function readFate(repo, pr, run) {
  const v = parse(tryRun(run, 'gh', ['pr', 'view', String(pr), '-R', repo, '--json', FATE_FIELDS]));
  if (!v || typeof v !== 'object' || !['OPEN', 'CLOSED', 'MERGED'].includes(v.state)) return null;
  let closedBy = null, read = true;
  if (v.state === 'CLOSED') { const i = parse(tryRun(run, 'gh', ['api', `repos/${repo}/issues/${pr}`])); closedBy = i?.closed_by?.login ?? null; read = !!(i && typeof i === 'object' && 'closed_by' in i && closedBy); }
  return { ...v, number: pr, closedBy, closed_by_read: v.state === 'CLOSED' ? read : null };
}
// The PRs a revert's body names ('Reverts <repo>#N' or 'Reverts #N'), this repository only.
// null = unreadable.
export function readRevertTargets(repo, pr, run) {
  const v = parse(tryRun(run, 'gh', ['pr', 'view', String(pr), '-R', repo, '--json', 'body']));
  if (!v || typeof v.body !== 'string') return null;
  const out = [];
  for (const m of v.body.matchAll(/\bReverts\s+(?:([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+))?#(\d+)\b/g)) if (!m[1] || m[1] === repo) out.push(Number(m[2]));
  return [...new Set(out)];
}
export function readTitle(repo, pr, run) {
  const v = parse(tryRun(run, 'gh', ['pr', 'view', String(pr), '-R', repo, '--json', 'title']));
  return typeof v?.title === 'string' ? v.title : null;
}
// where the catch-up starts: the newest merge the history holds (or the backfill's end),
// less a margin for GitHub's search index; never before the backfill's start
export function catchUpSince(doc, cfg) {
  const ends = doc.events.filter(e => e.type === 'merged' || e.type === 'revert-merged').map(e => Date.parse(e.at));
  const bf = doc.meta.backfill?.window;
  const newest = Math.max(...ends, Date.parse(bf?.until || ''));
  return iso(Math.max(newest - cfg.catch_up_margin_hours * 3600e3, Date.parse(bf?.since || '') || -Infinity));
}

// ── the plan (PURE) ─────────────────────────────────────────────────────────────
// fates: Map pr -> PR (readFate) | null (unreadable); a gone PR missing from it was not
// read this tick (the budget). reverts: Map revert pr -> { targets: [pr] | null, titles:
// Map pr -> title }.
export function planHistory({ doc, records, openPrs, verdict, at, repo, cfg, rules, scan, fates = new Map(), reverts = new Map(), backfilling, renovateAuthors, recount = null, triedFates = [] }) {
  const st = replay(doc);
  if (st.errors.length) return { ok: false, why: `the history does not replay (${st.errors.length} event(s) refused): ${st.errors[0]}` };
  const out = [];
  const notes = { unread: [], deferred: [], not_observed: [], shared_item: [], unrecorded: [], still_open: [], collapsed: [], duplicate: 0, revert_unmatched: [], remembered_merges: [], superseded: [] };
  const descs = new Map();
  const state = id => { let s = st.items.get(id); if (!s) { s = { item: blank(id), desc: null, refusal: null }; st.items.set(id, s); } return s; };
  const add = (s, ev, pr) => {
    if (st.ids.has(ev.id)) { notes.duplicate++; return false; }
    const r = RF.applyEvent(s.item, ev);
    if (r.errors.length) { notes.unrecorded.push({ pr: pr ?? null, item: ev.item_id, type: ev.type, why: r.errors[0] }); return false; }
    s.item = r.item; st.ids.add(ev.id); out.push(ev); track(s, ev);
    return true;
  };
  const renovate = { kind: 'renovate', login: cfg.actors.renovate[0] };
  const openSet = new Set(openPrs);
  const byPr0 = prIndex(st);
  const close = (s, id, pr, when, actor, reason, recordedAt) => add(s, makeEvent({ item_id: id, type: 'closed', from: s.item.state, to: 'closed', at: later(when, s.item.observed_at), actor, recorded_by: 'lane',
    recorded_at: recordedAt && Date.parse(recordedAt) > Date.parse(later(when, s.item.observed_at)) ? recordedAt : undefined, reason: reasonOf(reason, cfg), carrier: { repo, pr } }), pr);

  // 1. open PRs. One record per item: the lowest PR that carries it speaks for it, and a PR
  // whose facts were not read keeps the items it carries (an unreadable record changes nothing).
  const seen = new Map();
  const sorted = [...records].sort((a, b) => a.pr - b.pr);
  for (const r of sorted.filter(x => x.class === 'facts-incomplete')) {
    notes.unread.push({ pr: r.pr, why: 'facts incomplete this tick: the item cannot be named; nothing written' });
    for (const id of byPr0.get(r.pr) || []) if (OPEN.includes(st.items.get(id).item.state) && !seen.has(id)) seen.set(id, r.pr);
  }
  for (const r of sorted.filter(x => x.class !== 'facts-incomplete')) {
    let item;
    try { item = RF.fromLaneRecord(r, { repo, observed_at: at }); } catch (e) { notes.unrecorded.push({ pr: r.pr, why: e.message }); continue; }
    if (seen.has(item.id) && seen.get(item.id) !== r.pr) { notes.shared_item.push({ pr: r.pr, item: item.id, with: seen.get(item.id) }); continue; }
    seen.set(item.id, r.pr);
    const desc = { name: item.item.name, from: item.installed.version, to: item.available.version, type: item.available.update_type, pr: r.pr };
    for (const [id, s] of st.items) {
      if (id === item.id || s.item.carrier?.pr !== r.pr || !OPEN.includes(s.item.state)) continue;
      close(s, id, r.pr, at, renovate, `PR #${r.pr} now carries ${desc.name} ${desc.to ?? ''}: a newer version replaced this one`);
    }
    const s = state(item.id), cur = s.item;
    if (cur.state === null || (OBSERVABLE.includes(cur.state) && (cur.state !== item.state || cur.lane !== item.lane || cur.class !== item.class))) {
      if (add(s, makeEvent({ item_id: item.id, type: 'observed', from: cur.state, to: item.state, at: cur.state === null ? at : later(at, cur.observed_at), actor: { kind: 'lane', login: null }, recorded_by: 'lane',
        lane: item.lane, class: item.class, reason: reasonOf(item.reason, cfg), carrier: { repo, pr: r.pr, head: r.head ?? null } }), r.pr)) descs.set(item.id, desc);
    } else if (!OBSERVABLE.includes(cur.state)) notes.not_observed.push({ pr: r.pr, item: item.id, state: cur.state });
    if (!s.desc && !descs.has(item.id)) descs.set(item.id, desc);
  }

  // 2. the merge step's own events (live only)
  if (verdict?.mode === 'live' && Number.isInteger(verdict.pr)) {
    const r = records.find(x => x.pr === verdict.pr);
    let item = null;
    try { item = r && r.class !== 'facts-incomplete' ? RF.fromLaneRecord(r, { repo, observed_at: at }) : null; } catch { item = null; }
    const s = item ? st.items.get(item.id) : null;
    if (s) {
      const evs = RF.eventsFromMergeStep({ id: item.id, lane: s.item.lane, attempts: s.item.attempts, carrier: { pr: verdict.pr } }, verdict, { repo, at });
      if (evs[1]?.type === 'merge-refused' && s.refusal && s.refusal.head === (verdict.head ?? null) && s.refusal.key === refusalKey(verdict.pr_refusals)) notes.collapsed.push({ pr: verdict.pr, head: verdict.head, why: 'refused again at the same head for the same reasons; recorded once' });
      else for (const ev of evs) add(s, ev, verdict.pr);
    } else if (['refused', 'merged', 'merge-failed', 'merge-unknown'].includes(verdict.verdict)) notes.unrecorded.push({ pr: verdict.pr, why: 'the merge step ran on a PR whose item the history does not hold' });
  }

  // a merge on the item the lane's route names for what was merged
  const mergedOn = (id, e, recordedBy) => {
    const s = state(id);
    if (s.item.state !== null && !OPEN.includes(s.item.state)) {
      if (STICKY.includes(s.item.state)) notes.remembered_merges.push({ pr: e.number, item: id, state: s.item.state, at: iso(e.mergedAt), why: `PR #${e.number} merged a version the history remembers as ${s.item.state}; the format has no edge to record it` });
      else notes.shared_item.push({ pr: e.number, item: id, with: s.item.carrier?.pr ?? null, state: s.item.state });
      return false;
    }
    const a = actorOf(e.mergedBy?.login ?? e.mergedBy, cfg);
    const mc = e.mergeCommit?.oid ?? e.mergeCommit;
    if (!a) { notes.unread.push({ pr: e.number, item: id, why: 'who merged it could not be read; nothing written' }); return false; }
    if (!SHA.test(mc || '')) { notes.unread.push({ pr: e.number, item: id, why: 'no merge commit was read; nothing written' }); return false; }
    const when = s.item.observed_at ? later(iso(e.mergedAt), s.item.observed_at) : iso(e.mergedAt);
    const labels = labelNames(e);
    return add(s, makeEvent({ item_id: id, type: 'merged', from: s.item.state, to: 'merged_unverified', at: when, actor: a, recorded_by: recordedBy,
      recorded_at: Date.parse(at) > Date.parse(when) ? at : undefined, attempt: s.item.attempts.length + 1,
      reason: labels.length ? reasonOf(`labels: ${labels.join(', ')}`, cfg) : undefined, carrier: { repo, pr: e.number }, evidence: { merge_commit: mc } }), e.number);
  };

  // 3. open items whose PR left the open list: merged or closed, on the item that was
  // merged or closed (a PR Renovate moved to a newer version first closes the old item)
  const scanByPr = new Map((scan?.ok ? scan.list : []).map(e => [e.number, e]));
  for (const [id, s] of [...st.items]) {
    const pr = s.item.carrier?.pr;
    if (!OPEN.includes(s.item.state) || !Number.isInteger(pr) || openSet.has(pr)) continue;
    const f = scanByPr.has(pr) ? { state: 'MERGED', ...scanByPr.get(pr) } : fates.get(pr);
    if (f === undefined) { notes.deferred.push({ pr, item: id, why: 'the PR left the open list; its state is read on a later tick (budget)' }); continue; }
    if (f === null) { notes.unread.push({ pr, item: id, why: 'the PR left the open list and its state could not be read; nothing written' }); continue; }
    if (f.state === 'OPEN') { notes.still_open.push({ pr, item: id, why: 'GitHub reads it open, but the open list did not name it (a draft, or no longer Renovate\'s)' }); continue; }
    if (f.state === 'CLOSED' && !f.closed_by_read) { notes.unread.push({ pr, item: id, why: 'the PR was closed and who closed it could not be read; nothing written' }); continue; }
    let li;
    try { li = laneItem(f, repo, rules, at); } catch (e) { notes.unrecorded.push({ pr, item: id, why: `its item could not be named: ${e.message}` }); continue; }
    const when = iso(f.state === 'MERGED' ? f.mergedAt : f.closedAt || at);
    if (li.id !== id && li.basis !== 'title') {
      // Renovate moved the PR to a newer version before it merged or closed. A merge whose
      // merger or commit was not read waits: the old item must not close without the new one.
      if (f.state === 'MERGED' && (!actorOf(f.mergedBy?.login ?? f.mergedBy, cfg) || !SHA.test(f.mergeCommit?.oid ?? f.mergeCommit ?? ''))) { notes.unread.push({ pr, item: id, why: 'the PR merged a newer version, and who merged it or its merge commit could not be read; nothing written' }); continue; }
      close(s, id, pr, when, renovate, `PR #${pr} moved to ${li.desc.name} ${li.desc.to ?? ''} before it ${f.state === 'MERGED' ? 'merged' : 'closed'}: a newer version replaced this one`, at);
      notes.superseded.push({ pr, item: id, by: li.id });
      if (f.state === 'MERGED') { const fresh = !st.items.get(li.id)?.desc && !descs.has(li.id); if (mergedOn(li.id, f, 'lane') && fresh) descs.set(li.id, li.desc); }
      else notes.unrecorded.push({ pr, item: li.id, type: 'closed', why: 'the newer version was first seen at its close; the format has no edge from first sight to closed' });
      continue;
    }
    if (f.state === 'MERGED') mergedOn(id, f, 'lane');
    else {
      const a = actorOf(f.closedBy, cfg);
      // the format's closed edge has no shepherd actor: the nearest it allows is hand (an
      // automated session-like actor), with the shepherd's login, and the reason says so
      if (a.kind === 'shepherd') close(s, id, pr, when, { kind: 'hand', login: a.login }, `PR #${pr} was closed without a merge by ${a.login}, the shepherd's identity (recorded as hand: the format's closed edge has no shepherd actor)`, at);
      else close(s, id, pr, when, a, `PR #${pr} was closed without a merge by ${a.login}`, at);
    }
  }

  // 4. merged Renovate PRs the history does not hold (the backfill, and each tick's catch-up)
  const byPr = prIndex(st);
  for (const e of [...scanByPr.values()].sort((a, b) => Date.parse(a.mergedAt) - Date.parse(b.mergedAt) || a.number - b.number)) {
    if (!(renovateAuthors || []).includes(e.author?.login) || isRevertTitle(e.title) || byPr.has(e.number)) continue;
    let li;
    try { li = laneItem(e, repo, rules, at); } catch (err) { notes.unrecorded.push({ pr: e.number, why: `its item could not be named: ${err.message}` }); continue; }
    const fresh = state(li.id).item.state === null;
    if (mergedOn(li.id, e, 'backfill')) { if (fresh) descs.set(li.id, li.desc); byPr.set(e.number, [li.id]); }
  }

  // 5. reverts: 'Revert "<title>"' whose body names a PR of that title, merged by a session
  // or a person. One already recorded is not looked at again.
  const done = new Set([...doc.events, ...out].filter(e => e.type === 'revert-merged').map(e => e.evidence?.revert_pr));
  for (const e of [...(scan?.ok ? scan.reverts : [])].sort((a, b) => Date.parse(a.mergedAt) - Date.parse(b.mergedAt))) {
    if (done.has(e.number)) continue;
    const r = reverts.get(e.number);
    if (r === undefined) { notes.deferred.push({ pr: e.number, why: 'a revert whose body is read on a later tick (budget)' }); continue; }
    if (!r || r.targets === null) { notes.revert_unmatched.push({ pr: e.number, why: 'its body could not be read' }); continue; }
    const want = revertedTitle(e.title);
    const target = r.targets.find(t => r.titles?.get(t) === want);
    if (!Number.isInteger(target)) { notes.revert_unmatched.push({ pr: e.number, why: r.targets.length ? `no PR its body names (${r.targets.map(t => `#${t}`).join(', ')}) has the title it quotes` : 'its body names no reverted PR of this repository' }); continue; }
    const id = (prIndex(st).get(target) || []).find(x => REVERTABLE.includes(st.items.get(x).item.state));
    if (!id) { notes.revert_unmatched.push({ pr: e.number, reverts: target, why: 'the reverted PR carries no merged item in the history' }); continue; }
    const a = actorOf(e.mergedBy?.login ?? e.mergedBy, cfg), s = st.items.get(id), mc = e.mergeCommit?.oid ?? e.mergeCommit;
    if (!a || !['hand', 'person'].includes(a.kind)) { notes.unrecorded.push({ pr: e.number, item: id, type: 'revert-merged', why: `merged by ${a?.login ?? 'unknown'}: the format records a revert nobody opened only for a hand run or a person` }); continue; }
    if (!SHA.test(mc || '')) { notes.unread.push({ pr: e.number, item: id, why: 'no merge commit was read for the revert; nothing written' }); continue; }
    const when = later(iso(e.mergedAt), s.item.observed_at);
    add(s, makeEvent({ item_id: id, type: 'revert-merged', from: s.item.state, to: 'reverted_unverified', at: when, actor: a, recorded_by: 'backfill', recorded_at: Date.parse(at) > Date.parse(when) ? at : undefined,
      attempt: s.item.attempts.length, reason: reasonOf(`reverted by #${e.number}: ${e.title}`, cfg), carrier: { repo, pr: target }, evidence: { revert_pr: e.number, revert_commit: mc } }), e.number);
  }

  // the new document
  const next = { meta: structuredClone(doc.meta), items: structuredClone(doc.items), events: [...doc.events, ...out] };
  for (const [id, d] of descs) next.items[id] = { ...Object.fromEntries(Object.entries(d).filter(([, v]) => v !== null && v !== undefined)), ...(next.items[id]?.base ? { base: next.items[id].base } : {}), ...(next.items[id]?.mirrored ? { mirrored: next.items[id].mirrored } : {}) };
  for (const ev of out) if (next.items[ev.item_id] && Number.isInteger(ev.carrier?.pr)) next.items[ev.item_id].pr = ev.carrier.pr;
  // merges seen on a remembered item: kept, so every tick shows them until a person acts
  const rem = [...(next.meta.remembered_merges || [])];
  for (const x of notes.remembered_merges) if (!rem.some(y => y.pr === x.pr && y.item === x.item)) rem.push({ pr: x.pr, item: x.item, state: x.state, at: x.at, seen_at: at });
  if (rem.length) next.meta.remembered_merges = rem.slice(-50);
  const compare = scan?.ok ? compareCounts({ scan, st, events: next.events, renovateAuthors, rules, repo, at }) : null;
  const complete = scan?.ok && !notes.deferred.some(d => !Number.isInteger(d.item));
  if (backfilling && complete) next.meta.backfill = { at, window: scan.window, query: scan.query, events: out.filter(e => e.recorded_by === 'backfill').length, compare };
  // the backfill's comparison, owed until made: GitHub's count read again (recount) against
  // what the backfill listed and holds
  const bc = next.meta.backfill?.compare;
  if (!backfilling && bc && bc.match !== true && Number.isInteger(recount)) {
    next.meta.backfill = { ...next.meta.backfill, compare: { ...bc, github_count: recount, match: recount === bc.listed && bc.history_count === recount && (bc.missing || []).length === 0, compared_at: at } };
  }
  // the catch-up's comparison: one disagreement can be GitHub's search index (the list and the
  // count are read seconds apart); it is an ERROR only when the next tick disagrees again
  let catchUpRepeated = false;
  if (!backfilling && compare) {
    if (compare.match === false) { catchUpRepeated = !!doc.meta.catch_up_mismatch; next.meta.catch_up_mismatch = { since: doc.meta.catch_up_mismatch?.since ?? at, window: compare.window, github_count: compare.github_count, listed: compare.listed, history_count: compare.history_count }; }
    else if (compare.match === true) delete next.meta.catch_up_mismatch;
  }
  // the fair order: when each PR still waiting for its fate was last tried
  const pending = new Set([...st.items.values()].filter(x => OPEN.includes(x.item.state) && Number.isInteger(x.item.carrier?.pr) && !openSet.has(x.item.carrier.pr)).map(x => x.item.carrier.pr));
  const fateTried = {};
  for (const pr of pending) { const t = triedFates.includes(pr) ? at : doc.meta.tried?.fate?.[pr]; if (t) fateTried[pr] = t; }
  const tried = { ...(next.meta.tried || {}), fate: fateTried };
  if (!Object.keys(tried.fate).length) delete tried.fate;
  if (Object.keys(tried).length) next.meta.tried = tried; else delete next.meta.tried;
  const rot = rotate(next, cfg.max_bytes);
  if (!rot.ok) return { ok: false, stuck: true, why: rot.why };
  if (rot.dropped) rot.doc.meta.rotation = { at, dropped: rot.dropped, forgotten: rot.forgotten, oldest_kept: rot.doc.events[0]?.at ?? null };
  const changed = out.length > 0 || rot.dropped > 0 || JSON.stringify(doc.meta) !== JSON.stringify(rot.doc.meta) || JSON.stringify(doc.items) !== JSON.stringify(rot.doc.items);
  const titleItems = Object.values(rot.doc.items).filter(d => d.basis === 'title').length;
  return { ok: true, doc: rot.doc, events: out, notes, compare, rotation: rot, changed, backfill_complete: backfilling ? complete : null, title_items: titleItems, terminals: terminalsOf(rot.doc, openSet), bytes: rot.bytes, catch_up_repeated: catchUpRepeated };
}

// Terminal events owed to PR records (story 593 (d)), on facts: an item whose newest event
// is merged, merge-unknown or closed, recorded by the lane (it watched that PR, which has a
// record), that items[id].mirrored does not name yet. A closed item whose PR is still open
// was replaced on that PR: its record describes the new item.
export function terminalsOf(doc, openSet) {
  const last = new Map();
  for (const ev of doc.events) last.set(ev.item_id, ev);
  const byPr = new Map();
  for (const ev of last.values()) {
    if (!TERMINAL.includes(ev.type) || ev.recorded_by !== 'lane' || !Number.isInteger(ev.carrier?.pr)) continue;
    if (doc.items[ev.item_id]?.mirrored === ev.id) continue;
    if (ev.type === 'closed' && openSet.has(ev.carrier.pr)) continue;
    const prev = byPr.get(ev.carrier.pr);
    // the newest wins; at the same instant (a PR moved to a newer version, then merged) the merge
    if (prev && (Date.parse(prev.terminal.at) > Date.parse(ev.at) || (Date.parse(prev.terminal.at) === Date.parse(ev.at) && prev.terminal.type === 'merged'))) continue;
    byPr.set(ev.carrier.pr, { item: ev.item_id, terminal: { type: ev.type, at: ev.at, state: ev.to, by: ev.actor.login ?? ev.actor.kind, item: ev.item_id, event: ev.id, ...(ev.evidence?.merge_commit ? { merge_commit: ev.evidence.merge_commit } : {}) } });
  }
  // a superseded item and the merged one on the same PR: both are done once the merge is
  return [...byPr].sort((a, b) => a[0] - b[0]).map(([pr, t]) => ({ pr, terminal: t.terminal, items: [...last.values()].filter(e => e.carrier?.pr === pr && TERMINAL.includes(e.type) && e.recorded_by === 'lane' && doc.items[e.item_id]?.mirrored !== e.id).map(e => [e.item_id, e.id]) }));
}
// items[id].mirrored = the event id its PR record now carries (or that no record exists)
export function markMirrored(doc, marks) {
  const next = { ...doc, items: structuredClone(doc.items) };
  let n = 0;
  for (const [id, ev] of marks) if (next.items[id] && next.items[id].mirrored !== ev) { next.items[id].mirrored = ev; n++; }
  return { doc: next, marked: n };
}

// ── the count comparison ──────────────────────────────────────────────────────────
// The two numbers: GitHub's own search count of merged Renovate PRs in the window
// (github_count, read separately from the list) and the Renovate PRs of that window the
// history holds (history_count: recorded on their own item, or through another PR's item).
export function compareCounts({ scan, st, events = [], renovateAuthors, rules, repo, at }) {
  const ren = scan.list.filter(e => (renovateAuthors || []).includes(e.author?.login) && !isRevertTitle(e.title));
  const byPr = prIndex(st);
  const merged = id => AFTER_MERGE.includes(st.items.get(id)?.item.state);
  const own = [], shared = [], missing = [];
  let titleBased = 0;
  for (const e of ren) {
    if ((byPr.get(e.number) || []).some(merged)) { own.push(e.number); continue; }
    let li = null;
    try { li = laneItem(e, repo || scan.repo, rules, at); } catch { li = null; }
    if (li?.basis === 'title') titleBased++;
    if (li && merged(li.id) && st.items.get(li.id).item.carrier?.pr !== e.number) shared.push({ pr: e.number, item: li.id, recorded_via: st.items.get(li.id).item.carrier?.pr ?? null });
    else missing.push(e.number);
  }
  const revertPrs = new Set(events.filter(e => e.type === 'revert-merged').map(e => e.evidence?.revert_pr));
  const history = own.length + shared.length;
  const github = Number.isInteger(scan.github_count) ? scan.github_count : null;
  return {
    window: scan.window, query: scan.query, compared: 'github_count (GitHub search total_count for the query) against history_count (those PRs the history holds)',
    github_count: github, listed: ren.length, history_count: history, recorded: own.length, shared_item: shared, missing, title_based: titleBased,
    reverts: { titled: (scan.reverts || []).length, recorded: (scan.reverts || []).filter(e => revertPrs.has(e.number)).length },
    match: github === null ? null : github === ren.length && history === github && missing.length === 0,
  };
}

// ── rotation ────────────────────────────────────────────────────────────────────
export function rotate(doc, maxBytes) {
  let size = sizeOf(historyManifest(doc, '1'));
  if (size <= maxBytes) return { ok: true, doc, dropped: 0, forgotten: 0, bytes: size };
  const items = structuredClone(doc.items);
  const st = new Map(Object.entries(items).map(([id, d]) => [id, { item: skeleton(id, d.base), refusal: d.base?.refusal ?? null }]));
  const left = new Map();
  for (const ev of doc.events) left.set(ev.item_id, (left.get(ev.item_id) || 0) + 1);
  let k = 0, forgotten = 0;
  // the step: the bytes an event frees on average, its item included; re-measured each step
  const avg = Math.max(64, Math.floor(size / Math.max(1, doc.events.length)));
  const touched = new Set();
  while (size > maxBytes && k < doc.events.length) {
    const upto = Math.min(doc.events.length, k + Math.max(1, Math.ceil((size - maxBytes) / avg)));
    for (; k < upto; k++) {
      const ev = doc.events[k];
      let s = st.get(ev.item_id);
      if (!s) { s = { item: blank(ev.item_id), refusal: null }; st.set(ev.item_id, s); }
      const r = RF.applyEvent(s.item, ev);
      if (!r.errors.length) { s.item = r.item; track(s, ev); }
      left.set(ev.item_id, left.get(ev.item_id) - 1);
      touched.add(ev.item_id);
    }
    for (const id of touched) {
      const s = st.get(id);
      if (left.get(id) === 0 && forgettable(s.item)) { if (items[id]) forgotten++; delete items[id]; continue; }
      items[id] = { ...(items[id] || { pr: s.item.carrier?.pr ?? null }), base: baseOf(s) };
    }
    touched.clear();
    size = sizeOf(historyManifest({ meta: doc.meta, items, events: doc.events.slice(k) }, '1'));
  }
  if (size > maxBytes) return { ok: false, why: `the history is ${size} bytes with every event rotated out, over its ${maxBytes}-byte cap (the remembered items alone)` };
  return { ok: true, doc: { meta: doc.meta, items, events: doc.events.slice(k) }, dropped: k, forgotten, bytes: size };
}

// ── THE WRITE: one spawn, ConfigMaps upgrade-ledger and upgrade-ledger-marker only ─────
export function applyHistory(manifest, manager, env) {
  const m = manifest?.metadata || {};
  const keys = Object.keys(manifest?.data || {}).sort().join();
  const ledger = m.name === HISTORY.name, marker = m.name === MARKER.name;
  const shape = manager === MANAGER ? ledger && keys === [...KEYS].sort().join() && /^\d+$/.test(String(m.resourceVersion || ''))
    : manager === INIT_MANAGER ? ledger && !manifest.data && !m.resourceVersion
      : manager === MARKER_MANAGER ? marker && keys === 'marker' && !m.resourceVersion : false;
  if (manifest?.kind !== 'ConfigMap' || m.namespace !== HISTORY.namespace || !(ledger || marker) || Object.keys(manifest).some(k => !['apiVersion', 'kind', 'metadata', 'data'].includes(k)) || !shape) {
    throw new Error('applyHistory: refused (only upgrade-agent/upgrade-ledger: its events, items and meta at a resourceVersion under upgrade-night-history, or an empty create under upgrade-night-history-init; or upgrade-agent/upgrade-ledger-marker: its marker under upgrade-night-history-marker)');
  }
  const body = JSON.stringify(manifest);
  const r = spawnSync('kubectl', ['apply', '--server-side', `--field-manager=${manager}`, '--force-conflicts', '-o', 'json', '-f', '-'], { input: body, encoding: 'utf8', env, maxBuffer: 64 * 1024 * 1024, timeout: KUBECTL_TIMEOUT_MS, killSignal: 'SIGKILL' });
  if (r.status !== 0) {
    const raw = String(r.stderr || r.error?.message || '');
    const conflict = /\(Conflict\)|the object has been modified/i.test(raw);
    // a fixed vocabulary for the state; kubectl's own text goes to the Job log only (raw)
    const detail = r.status === null ? 'timed out or killed' : conflict ? 'conflict (HTTP 409)' : /\(Forbidden\)|forbidden/i.test(raw) ? 'refused by the API server (Forbidden)'
      : /\(NotFound\)/.test(raw) ? 'not found' : /\(Invalid\)|\(TooLong\)|too long|exceeds/i.test(raw) ? 'rejected by the API server as invalid or too large' : `kubectl exited ${r.status}`;
    return { ok: false, conflict, detail, raw: M.publicDetail(raw) };
  }
  const got = parse(r.stdout);
  const rv = got?.metadata?.resourceVersion ?? null;
  // an apply succeeds on an object that exists: a create that met another writer's data is a conflict
  const hasData = KEYS.some(k => got?.data?.[k] !== undefined);
  return { ok: !!rv, rv, conflict: false, hasData, detail: rv ? null : 'the apply returned no resourceVersion' };
}

// ── the phase ───────────────────────────────────────────────────────────────────
// write = false (a local run): plans and writes nothing. Returns { summary, terminals, ctx }:
// ctx (the document and version written) lets commitMirrors() mark mirrored terminals.
export function historyPhase({ rules, run, env, repo, at, records, openPrs, verdict, write, apply = applyHistory, clock = Date.now, startedAt = Date.parse(at) }) {
  const sum = { configmap: `${HISTORY.namespace}/${HISTORY.name}`, read: null, written: false, write: 'not-attempted', why_not: null, events: 0, derived: 0, by_type: {}, total: null, backfill: null, catch_up: null, rotation: null, notes: null, terminals: [], reads: null, elapsed_ms: null };
  const t0 = clock();
  const stuck = why => ({ summary: { ...sum, write: 'stuck', why_not: why, elapsed_ms: clock() - t0 }, terminals: [] });
  const cfg = historyConfig(rules);
  if (!cfg.ok) return stuck(cfg.why);
  const budget = makeBudget(cfg, clock, startedAt);
  // the marker first: a first writer writes the history, then the marker, so a marker read
  // before the history cannot meet a history that is not there yet
  const mread = M.readNamedConfigMap(MARKER.name, run);
  const read = M.readNamedConfigMap(HISTORY.name, run);
  if (!read || !mread) return { summary: { ...sum, read: 'unreadable', write: 'failed', why_not: `unreadable: ConfigMap upgrade-agent/${!read ? HISTORY.name : MARKER.name}; nothing derived or written`, elapsed_ms: clock() - t0 }, terminals: [] };
  const mk = parseMarker(mread.found ? mread.cm : null);
  if (!mk.ok) return stuck(`${mk.why}; nothing written`);
  const p = parseDoc(read.found ? read.cm : null);
  sum.read = !read.found ? 'absent' : p.ok ? (p.empty ? `empty at resourceVersion ${p.rv}` : `read at resourceVersion ${p.rv}`) : 'refused';
  if (!p.ok) return stuck(`${p.why}; nothing written (a history this tick cannot read is not overwritten)`);
  if (mk.marker && p.empty) return { summary: { ...sum, write: 'stuck', lost: { marker: mk.marker }, why_not: `the history was lost: upgrade-ledger-marker says a history exists (created ${mk.marker.created_at}${mk.marker.backfilled_at ? `, backfilled ${mk.marker.backfilled_at}` : ''}), but upgrade-ledger is ${read.found ? 'empty' : 'absent'}. Nothing is written until a person restores upgrade-ledger or deletes upgrade-ledger-marker (runbook: "The history was lost")`, elapsed_ms: clock() - t0 }, terminals: [] };
  const backfilling = !p.doc.meta.backfill;
  const since = backfilling ? iso(Date.parse(at) - cfg.backfill_days * 864e5) : catchUpSince(p.doc, cfg);
  const scan = readMerged(repo, since, at, cfg, run, budget, rules.renovate_pr_authors || []);
  const st0 = replay(p.doc);
  const openSet = new Set(openPrs);
  const fates = new Map(), reverts = new Map();
  const scanPrs = new Set(scan.ok ? scan.list.map(e => e.number) : []);
  const gone = [...new Set([...st0.items.values()].filter(s => OPEN.includes(s.item.state) && Number.isInteger(s.item.carrier?.pr) && !openSet.has(s.item.carrier.pr) && !scanPrs.has(s.item.carrier.pr)).map(s => s.item.carrier.pr))];
  for (const pr of fairOrder(gone, p.doc.meta.tried?.fate)) {
    if (!budget.pr()) break;
    const f = readFate(repo, pr, run);
    if (f?.state === 'CLOSED') budget.pr(); // the closer read
    fates.set(pr, f);
  }
  if (scan.ok) {
    const done = new Set(p.doc.events.filter(e => e.type === 'revert-merged').map(e => e.evidence?.revert_pr));
    const titles = new Map(scan.list.map(e => [e.number, e.title]));
    for (const e of scan.reverts.filter(x => !done.has(x.number))) {
      if (!budget.pr()) break;
      const targets = readRevertTargets(repo, e.number, run);
      const tmap = new Map();
      for (const t of targets || []) { if (titles.has(t)) tmap.set(t, titles.get(t)); else if (budget.pr()) tmap.set(t, readTitle(repo, t, run)); }
      reverts.set(e.number, { targets, titles: tmap });
    }
  }
  // the backfill's comparison is owed until GitHub's count has been read and matches: read the
  // count again for the backfill's window (one search per author; the backfill is not re-run)
  let recount = null;
  const bfc = p.doc.meta.backfill?.compare;
  if (!backfilling && bfc && bfc.match !== true && budget.time()) {
    const { since: s0, until: u0 } = p.doc.meta.backfill.window;
    const counts = (rules.renovate_pr_authors || []).map(a => readCount(repo, `author:${a} merged:${s0}..${u0}`, run));
    recount = counts.every(Number.isInteger) ? counts.reduce((x, y) => x + y, 0) : null;
  }
  const plan = planHistory({ doc: p.doc, records, openPrs, verdict, at, repo, cfg, rules, scan, fates, reverts, backfilling, renovateAuthors: rules.renovate_pr_authors || [], recount, triedFates: [...fates.keys()] });
  sum.reads = { per_pr: budget.reads, cap: cfg.max_pr_reads_per_tick, stopped: budget.stopped };
  if (!plan.ok) return stuck(`${plan.why}; nothing written`);
  for (const ev of plan.events) sum.by_type[ev.type] = (sum.by_type[ev.type] || 0) + 1;
  sum.derived = plan.events.length;
  sum.total = { events: plan.doc.events.length, items: Object.keys(plan.doc.items).length, title_items: plan.title_items, bytes: plan.bytes, cap: cfg.max_bytes, oldest: plan.doc.events[0]?.at ?? null };
  sum.rotation = plan.rotation.dropped ? { dropped: plan.rotation.dropped, forgotten: plan.rotation.forgotten } : null;
  sum.backfill = backfilling ? (plan.backfill_complete ? { state: 'this tick', window: scan.window, events: plan.doc.meta.backfill.events, compare: plan.compare } : { state: 'not yet', why: scan.ok ? 'reads left for a later tick (budget)' : scan.why, window: scan.window, ...(scan.ok ? { compare: plan.compare } : {}) })
    : { state: 'done', at: p.doc.meta.backfill.at, window: p.doc.meta.backfill.window, compare: plan.doc.meta.backfill?.compare ?? p.doc.meta.backfill.compare };
  sum.catch_up = backfilling ? null : scan.ok ? { window: scan.window, ...pick(plan.compare, ['compared', 'github_count', 'listed', 'history_count', 'shared_item', 'missing', 'reverts', 'match']), ...(plan.compare?.match === false ? { repeated: plan.catch_up_repeated } : {}) } : { window: scan.window, why: scan.why };
  sum.notes = Object.fromEntries(Object.entries(plan.notes).filter(([, v]) => (Array.isArray(v) ? v.length : v)));
  sum.remembered_merges = plan.doc.meta.remembered_merges || [];
  sum.mirror_halt = plan.doc.meta.mirror_halt ?? null;
  const owed = plan.terminals;
  const fin = extra => ({ ...sum, terminals: owed.map(t => t.pr), ...extra, elapsed_ms: clock() - t0 });
  if (!write) return { summary: fin({ write: 'planned', why_not: 'a local run (no --write-state): planned, nothing written' }), terminals: owed, plan, budget };
  // the marker vouches only for a history that exists (read with data, or written now)
  const exists = (read.found && !p.empty) || plan.changed;
  const wantMarker = exists && (!mk.marker || (plan.backfill_complete && !mk.marker.backfilled_at));
  if (!plan.changed && !wantMarker) return { summary: fin({ write: 'not-needed', why_not: 'nothing new this tick' }), terminals: owed, ctx: { doc: plan.doc, rv: p.rv }, budget };
  let rv = p.rv;
  if (plan.changed) {
    if (!read.found) {
      const w0 = apply(initManifest(), INIT_MANAGER, env);
      if (!w0.ok) return { summary: fin({ write: w0.conflict ? 'conflict' : 'failed', terminals: [], why_not: `the empty history could not be created (${w0.detail}); nothing written, the next tick derives the same events`, raw: w0.raw }), terminals: [] };
      if (w0.hasData) return { summary: fin({ write: 'conflict', terminals: [], why_not: 'another writer created upgrade-ledger with events after this tick read it absent; nothing written, the next tick derives the same events' }), terminals: [] };
      rv = w0.rv;
    }
    const w = apply(historyManifest(plan.doc, rv), MANAGER, env);
    if (!w.ok) return { summary: fin({ write: w.conflict ? 'conflict' : 'failed', terminals: [], why_not: w.conflict ? `another writer changed upgrade-ledger after this tick read it (resourceVersion ${rv}); nothing written, the next tick derives the same events` : `the write failed (${w.detail}); nothing written, the next tick derives the same events`, raw: w.raw }), terminals: [] };
    rv = w.rv;
  }
  // the marker, after the history it vouches for
  let markerOut = null;
  if (wantMarker) {
    const m = { schema: MARKER_SCHEMA, created_at: mk.marker?.created_at ?? at, backfilled_at: mk.marker?.backfilled_at ?? (plan.backfill_complete || p.doc.meta.backfill ? at : null) };
    const wm = apply(markerManifest(m), MARKER_MANAGER, env);
    markerOut = wm.ok ? 'written' : `not written (${wm.detail}); the next tick writes it`;
    if (!wm.ok) sum.marker_error = markerOut;
  }
  return { summary: fin({ written: plan.changed, write: plan.changed ? 'written' : 'not-needed', events: plan.changed ? plan.events.length : 0, rv, marker: markerOut }), terminals: owed, plan, ctx: { doc: plan.doc, rv }, budget };
}
const pick = (o, keys) => Object.fromEntries(keys.filter(k => o && k in o).map(k => [k, o[k]]));

// After the PR-write phase: mark the terminals whose record now carries them (updated,
// current) or that have no record (no_record), and keep a denial on a mirror as the
// mirrors' own halt. One more write at the version the first one returned.
// read = the PRs whose record was read this tick: those still owed keep that time in
// meta.tried.mirror (the fair order), the rest leave it.
export function commitMirrors({ ctx, terminals, result, halt, at, env, read = [], apply = applyHistory }) {
  if (!ctx?.rv) return { written: false, why: 'the history was not written this tick' };
  const ok = new Set([...(result?.updated || []), ...(result?.current || []), ...(result?.no_record || [])]);
  const marks = terminals.filter(t => ok.has(t.pr)).flatMap(t => t.items);
  let { doc, marked } = markMirrored(ctx.doc, marks);
  if (halt && !doc.meta.mirror_halt) { doc = { ...doc, meta: { ...doc.meta, mirror_halt: { at, why: halt.why } } }; marked++; }
  const owed = new Set(terminals.filter(t => !ok.has(t.pr)).map(t => t.pr));
  const before = doc.meta.tried?.mirror || {};
  const mirror = {};
  for (const pr of owed) { const t = read.includes(pr) ? at : before[pr]; if (t) mirror[pr] = t; }
  if (JSON.stringify(mirror) !== JSON.stringify(before)) {
    const tried = { ...(doc.meta.tried || {}), mirror };
    if (!Object.keys(mirror).length) delete tried.mirror;
    const meta = { ...doc.meta, tried };
    if (!Object.keys(tried).length) delete meta.tried;
    doc = { ...doc, meta };
    marked++;
  }
  if (!marked) return { written: false, why: 'nothing to mark' };
  const w = apply(historyManifest(doc, ctx.rv), MANAGER, env);
  return w.ok ? { written: true, marked: marks.length, rv: w.rv } : { written: false, why: `${w.conflict ? 'conflict' : w.detail}; the next tick reads the records again`, raw: w.raw };
}

// The tick's log line (stderr).
export function historyLine(h) {
  if (!h) return 'not run';
  if (h.error) return `FAILED: ${h.error}`;
  const types = Object.entries(h.by_type || {}).map(([k, v]) => `${k} ${v}`).join(', ');
  const bf = h.backfill?.state === 'this tick' ? `; backfill this tick: ${h.backfill.events} merged events, ${cmpLine(h.backfill.compare)}` : h.backfill?.state === 'not yet' ? `; backfill not yet: ${h.backfill.why}`
    : h.backfill?.compare && h.backfill.compare.match !== true ? `; backfill comparison still owed: ${cmpLine(h.backfill.compare)}` : '';
  const cu = h.catch_up ? (h.catch_up.why ? `; catch-up unreadable: ${h.catch_up.why}` : `; catch-up since ${h.catch_up.window.since}: ${cmpLine(h.catch_up)}${h.catch_up.match === false ? (h.catch_up.repeated ? ' on two ticks in a row' : ' (first disagreement: GitHub\'s search index may lag; an ERROR if the next tick disagrees again)') : ''}`) : '';
  const tot = h.total ? `; ${h.write === 'written' ? 'holds' : 'would hold'} ${h.total.events} events, ${h.total.items} items (${h.total.title_items} named from a title), ${h.total.bytes} of ${h.total.cap} bytes` : '';
  const n = h.write === 'written' ? `${h.events} new event${h.events === 1 ? '' : 's'}${types ? ` (${types})` : ''}` : `${h.derived || 0} event${h.derived === 1 ? '' : 's'} derived${types ? ` (${types})` : ''}, none written`;
  const rm = (h.remembered_merges || []).length ? `; MERGED WHILE REMEMBERED: ${h.remembered_merges.map(x => `#${x.pr} (${x.state})`).join(', ')}` : '';
  return `${h.write.toUpperCase()}: ${n}${h.why_not ? `; ${h.why_not}` : ''}${bf}${cu}${tot}${h.rotation ? `; rotated out ${h.rotation.dropped} events, forgot ${h.rotation.forgotten} items` : ''}${h.reads?.stopped ? `; ${h.reads.stopped}` : ''}${rm}${h.marker_error ? `; marker ${h.marker_error}` : ''}${Number.isFinite(h.elapsed_ms) ? `; ${h.elapsed_ms} ms` : ''}`;
}
const cmpLine = c => (c ? `GitHub counts ${c.github_count ?? 'unread'} merged Renovate PRs, listed ${c.listed}, the history holds ${c.history_count} (${c.shared_item?.length || 0} through another PR's item), missing ${c.missing.length ? c.missing.map(x => `#${x}`).join(', ') : 'none'}${c.match === false ? ' (MISMATCH)' : c.match === null ? ' (GitHub count unread: not compared)' : ''}` : 'no comparison');
// what the tick logs at ERROR level: anything that leaves the history unwritten or stuck,
// and a merge seen on a remembered item
export const historyError = h => !!h && !!(h.error || ['failed', 'conflict', 'stuck'].includes(h.write) || (h.remembered_merges || []).length > 0 || h.marker_error || h.backfill?.compare?.match === false || (h.catch_up?.match === false && h.catch_up.repeated));

// The PR-record items owed a terminal (story 593 (d)): the lane's own record, with
// terminal set; edit only, never a new comment on a closed PR. At most max_mirrors_per_tick.
// In the fair order (meta.tried.mirror): a record that stays refused or unparseable cannot
// hold a slot on every tick.
export function terminalRecordItems(terminals, openPrs, rcd, at, run, repo, cfg, budget, tried = {}) {
  const items = [], skipped = [], deferred = [], read = [];
  const byPr = new Map(terminals.map(t => [t.pr, t]));
  for (const pr of fairOrder(terminals.filter(x => !openPrs.includes(x.pr)).map(x => x.pr), tried)) {
    const t = byPr.get(pr);
    if (items.length + skipped.length >= cfg.max_mirrors_per_tick || (budget && !budget.pr())) { deferred.push(t.pr); continue; }
    read.push(t.pr);
    const raw = M.readIssueComments(repo, t.pr, run);
    const comments = raw ? L.recordComments(raw) : null;
    const own = comments ? M.ownRecords(comments, rcd).own : [];
    const cur = own.length ? M.parseRecord(own[0].body, rcd) : null;
    if (own.length && !(cur && cur.pr === t.pr)) { skipped.push({ pr: t.pr, why: 'the lane record on this PR cannot be parsed; not edited' }); continue; }
    items.push({ rec: cur ? { ...cur, terminal: t.terminal, updated: at } : { schema: 'haynes.upgrade-lane/v1', pr: t.pr, terminal: t.terminal, updated: at }, comments, edit_only: true });
  }
  return { items, skipped, deferred, read };
}
