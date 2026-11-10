#!/usr/bin/env node
// lane-classify.mjs — deterministic lane classifier for open Renovate PRs (OP512 step 1).
//
// Design: OP512 comment 6193, "Classification" and "The merge guard"
// (https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/512).
// Runbook: .agents/runbooks/upgrade-night-lane.md. No dependencies: Node 24 only
// (the upgrade-shepherd image is node:24-slim).
//
// classify(facts, rules, opts) is a PURE function. Given one PR's facts and the
// committed registry (upgrade-rules.json) it returns
//   { lane, class, reason, rules[], checks{expected,missing,failing,pending,stale}, ... }
// with lane one of EXCLUDED, LANE-B-OWNER, LANE-B-AGENT, BLOCKED, LANE-A. Every rule
// that fires adds a finding; the PR takes the STRICTEST finding's lane, so a mixed-path
// PR lands in the strictest lane any of its paths or packages asks for.
//
// facts = {
//   number, head_sha, title, branch, author, is_draft,
//   labels: [name],                       // type/major|minor|patch|digest, ...
//   files: [path],
//   commits: [{ sha, authors: [login|null] }],
//   packages: [{ name, update, from, to }],   // the Renovate body table
//   checks: [{ workflow, name, status, conclusion, completed_at }],   // head commit
//   vet_markers: [{ author, sha, verdict }],  // '<!-- shepherd-vet sha=… -->' comments
// }
// opts = { now: epoch ms, holdRules: [packageRules from .renovate/holds.json5] }
//
// The CLI at the bottom gathers those facts for every open Renovate PR with READ-ONLY
// gh calls (gh pr list, gh pr view, gh api GET) through runReadOnly(), which refuses any
// other command shape. It never merges, comments, labels, edits or pushes.
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync, realpathSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const LANES = ['EXCLUDED', 'LANE-B-OWNER', 'LANE-B-AGENT', 'BLOCKED', 'LANE-A'];
const RANK = Object.fromEntries(LANES.map((l, i) => [l, i]));

// ── globs (GitHub path-filter / minimatch subset: **, *, ?, {a,b}) ─────────────────
const globCache = new Map();
export function globToRegExp(glob) {
  if (globCache.has(glob)) return globCache.get(glob);
  let re = '';
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === '*') {
      if (glob[i + 1] === '*') {
        i++;
        if (glob[i + 1] === '/') { i++; re += '(?:.*/)?'; } else re += '.*';
      } else re += '[^/]*';
    } else if (c === '?') re += '[^/]';
    else if (c === '{') {
      const end = glob.indexOf('}', i);
      if (end < 0) { re += '\\{'; continue; }
      re += '(?:' + glob.slice(i + 1, end).split(',').map(s => s.replace(/[.+^${}()|[\]\\]/g, '\\$&')).join('|') + ')';
      i = end;
    } else re += c.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  }
  const out = new RegExp('^' + re + '$');
  globCache.set(glob, out);
  return out;
}
export const matchGlob = (path, glob) => globToRegExp(glob).test(path);
const anyGlob = (path, globs) => (globs || []).some(g => matchGlob(path, g));

// ── JSON5 (the .renovate/*.json5 subset: comments, quoted/unquoted keys, single
//    quotes, trailing commas) — enough to read holds.json5 without a dependency. ──
export function parseJson5(text) {
  let out = '', i = 0;
  const n = text.length;
  const lastSig = () => { let j = out.length - 1; while (j >= 0 && /\s/.test(out[j])) j--; return j; };
  while (i < n) {
    const c = text[i];
    if (c === '/' && text[i + 1] === '/') { while (i < n && text[i] !== '\n') i++; continue; }
    if (c === '/' && text[i + 1] === '*') { const e = text.indexOf('*/', i + 2); i = e < 0 ? n : e + 2; continue; }
    if (c === '"' || c === "'") {
      let s = '', j = i + 1;
      for (; j < n && text[j] !== c; j++) {
        if (text[j] === '\\') {
          const e = text[++j];
          if (e === 'n') s += '\n'; else if (e === 't') s += '\t'; else if (e === 'r') s += '\r';
          else if (e === 'u') { s += String.fromCharCode(parseInt(text.slice(j + 1, j + 5), 16)); j += 4; }
          else if (e === '\n') { /* line continuation */ } else s += e;
        } else s += text[j];
      }
      if (j >= n) throw new Error('json5: unterminated string');
      out += JSON.stringify(s); i = j + 1; continue;
    }
    if (c === ']' || c === '}') { const j = lastSig(); if (j >= 0 && out[j] === ',') out = out.slice(0, j) + out.slice(j + 1); out += c; i++; continue; }
    if (/[A-Za-z_$]/.test(c)) {
      let j = i; while (j < n && /[\w$]/.test(text[j])) j++;
      const word = text.slice(i, j);
      let k = j; while (k < n && /\s/.test(text[k])) k++;
      if (text[k] === ':') out += JSON.stringify(word);
      else if (['true', 'false', 'null'].includes(word)) out += word;
      else throw new Error(`json5: unsupported bare word ${word}`);
      i = j; continue;
    }
    out += c; i++;
  }
  return JSON.parse(out);
}

// ── versions ───────────────────────────────────────────────────────────────────
const numParts = v => (String(v || '').replace(/^v/i, '').match(/^\d+(?:\.\d+)*/) || [''])[0].split('.').filter(Boolean).map(Number);
export function compareVersions(a, b) {
  const x = numParts(a), y = numParts(b);
  for (let i = 0; i < Math.max(x.length, y.length); i++) { const d = (x[i] || 0) - (y[i] || 0); if (d) return d < 0 ? -1 : 1; }
  return 0;
}
const isVersionLike = v => numParts(v).length > 0;
const isDigestLike = v => /^[0-9a-f]{7,64}$/.test(String(v || ''));
const reFromRenovate = s => { const m = /^\/(.*)\/([a-z]*)$/.exec(s); return m ? new RegExp(m[1], m[2]) : null; };
function nameMatches(pattern, name) {
  const re = reFromRenovate(pattern);
  if (re) return re.test(name);
  return pattern === name;
}
// Renovate matches a docker package by its full name; the PR table often shows the
// short name (python, postgres). Try the usual spellings.
const nameVariants = name => {
  const v = [name];
  if (!name.includes('/')) v.push(`docker.io/library/${name}`, `docker.io/${name}`, `library/${name}`);
  else if (!/^[^/]+\.[^/]+\//.test(name)) v.push(`docker.io/${name}`);
  return v;
};

// allowedVersions: '<X.Y.Z' or '/regex/'. Returns true / false / null (unparseable).
export function versionAllowed(allowed, to) {
  if (!allowed) return true;
  const re = reFromRenovate(allowed);
  if (re) return re.test(String(to).replace(/^v/i, ''));
  const m = /^\s*(<=|<|>=|>)\s*v?([\d.]+)\s*$/.exec(allowed);
  if (!m || !isVersionLike(to)) return null;
  const c = compareVersions(to, m[2]);
  return { '<': c < 0, '<=': c <= 0, '>': c > 0, '>=': c >= 0 }[m[1]];
}

// ── holds.json5: an open PR whose package (and file, when the hold names files) is
//    held and whose new version is outside allowedVersions (or the rule disables the
//    package) matches the hold. Unparseable version => counts as held (fail closed).
export function holdMatches(facts, holdRules) {
  const out = [];
  for (const [idx, r] of (holdRules || []).entries()) {
    const holdLike = r.enabled === false || typeof r.allowedVersions === 'string';
    if (!holdLike || !Array.isArray(r.matchPackageNames)) continue;
    const title = Array.isArray(r.description) ? r.description[0] : (r.description || `holds.json5 rule ${idx}`);
    if (r.matchFileNames && !facts.files.some(p => anyGlob(p, r.matchFileNames))) continue;
    for (const pkg of facts.packages || []) {
      if (!nameVariants(pkg.name).some(nm => r.matchPackageNames.some(p => nameMatches(p, nm)))) continue;
      if (r.enabled === false) { out.push({ id: `hold:${idx}`, title, package: pkg.name, why: 'disabled for this package' }); continue; }
      if (pkg.update === 'digest' || (isDigestLike(pkg.to) && !isVersionLike(pkg.to))) continue; // the tag did not move
      const ok = versionAllowed(r.allowedVersions, pkg.to);
      if (ok === true) continue;
      out.push({ id: `hold:${idx}`, title, package: pkg.name, why: ok === null ? `cannot compare ${pkg.to} with ${r.allowedVersions}` : `${pkg.to} is outside ${r.allowedVersions}` });
    }
  }
  return out;
}

// ── the check map ─────────────────────────────────────────────────────────────
export function expectedChecks(paths, rules) {
  const cm = rules.check_map;
  const expected = new Map();
  const add = (workflow, check, why) => { const k = `${workflow}\u0000${check}`; if (!expected.has(k)) expected.set(k, { workflow, check, why }); };
  for (const a of cm.always) add(a.workflow, a.check, 'every PR');
  const unvalidated = [];
  for (const p of paths) {
    let validated = false;
    for (const e of cm.entries) {
      if (!anyGlob(p, e.paths)) continue;
      validated = true;
      for (const c of e.checks) add(e.workflow, c, `${e.id}: ${p}`);
    }
    if (!validated) unvalidated.push(p);
  }
  return { expected: [...expected.values()], unvalidated };
}

const FAIL = new Set(['FAILURE', 'TIMED_OUT', 'CANCELLED', 'ACTION_REQUIRED', 'STARTUP_FAILURE', 'ERROR']);
export function evaluateChecks(expected, runs, now, maxAgeDays) {
  const res = { expected: [], missing: [], failing: [], pending: [], stale: [] };
  for (const e of expected) {
    const label = `${e.workflow} / ${e.check}`;
    res.expected.push(label);
    const mine = (runs || []).filter(r => r.workflow === e.workflow && r.name === e.check);
    if (!mine.length) { res.missing.push(label); continue; }
    // the newest attempt counts (a re-run supersedes the failed one)
    const r = mine.reduce((a, b) => (String(b.completed_at || b.started_at || '') > String(a.completed_at || a.started_at || '') ? b : a));
    if (r.status && r.status !== 'COMPLETED') { res.pending.push(`${label} (${r.status})`); continue; }
    if (r.conclusion === 'SUCCESS') {
      const t = Date.parse(r.completed_at || '');
      if (Number.isFinite(t) && now - t > maxAgeDays * 86400e3) res.stale.push(`${label} (${r.completed_at})`);
      continue;
    }
    if (FAIL.has(r.conclusion)) res.failing.push(`${label} (${r.conclusion})`);
    else res.missing.push(`${label} (${r.conclusion || 'no conclusion'})`); // SKIPPED/NEUTRAL: not a pass
  }
  return res;
}

// ── cluster effect of the changed paths ──────────────────────────────────────
const EFFECT_RANK = { none: 0, delayed: 1, immediate: 2 };
export function clusterEffect(paths, rules) {
  let effect = 'none';
  const targets = new Set(), ownImages = new Set();
  const bump = e => { if (EFFECT_RANK[e] > EFFECT_RANK[effect]) effect = e; };
  for (const p of paths) {
    let m;
    if ((m = /^(kubernetes\/[^/]+\/apps\/[^/]+\/[^/]+)\//.exec(p))) { targets.add(m[1]); bump('immediate'); continue; }
    if ((m = /^(kubernetes\/[^/]+\/(?:flux|bootstrap))\//.exec(p)) || (m = /^(kubernetes\/shared\/[^/]+\/[^/]+)/.exec(p))) { targets.add(m[1]); bump('immediate'); continue; }
    if (p.startsWith('kubernetes/')) { targets.add(p); bump('immediate'); continue; }
    const img = rules.own_images.find(o => anyGlob(p, o.source_globs));
    if (img) {
      ownImages.add(img.id); bump(img.cluster_effect);
      if (img.cluster_effect === 'immediate' && img.target) targets.add(img.target);
    }
  }
  return { effect, targets: [...targets], own_images: [...ownImages] };
}

// ── the classifier ────────────────────────────────────────────────────────────
export function updateTypes(facts) {
  const t = new Set();
  for (const l of facts.labels || []) { const m = /^type\/(major|minor|patch|digest)$/.exec(l); if (m) t.add(m[1]); }
  for (const p of facts.packages || []) if (['major', 'minor', 'patch', 'digest', 'pin', 'pinDigest'].includes(p.update)) t.add(p.update);
  if (/^[a-z]+(\([^)]*\))?!:/.test(facts.title || '')) t.add('major');
  return [...t];
}

function packageRuleHits(facts, rule, types) {
  const m = rule.match || {};
  if (m.update_types && !types.some(t => m.update_types.includes(t))) return null;
  const pathHit = m.paths ? facts.files.filter(p => anyGlob(p, m.paths)) : null;
  if (m.paths && !pathHit.length) return null;
  if (m.unless_paths && facts.files.some(p => anyGlob(p, m.unless_paths))) return null;
  let pkgHit = null;
  if (m.packages) {
    pkgHit = (facts.packages || []).filter(pk => m.packages.some(pat => nameVariants(pk.name).some(nm => nameMatches(pat, nm))));
    if (!pkgHit.length) return null;
    if (m.min_version && !pkgHit.some(pk => isVersionLike(pk.to) && compareVersions(pk.to, m.min_version) >= 0)) return null;
  }
  return { paths: pathHit, packages: pkgHit };
}

export function classify(facts, rules, opts = {}) {
  const now = opts.now ?? Date.now();
  const findings = [];
  const ruleIds = [];
  const add = (lane, cls, reason, rule) => { findings.push({ lane, class: cls, reason, ...(rule ? { rule } : {}) }); if (rule && !ruleIds.includes(rule)) ruleIds.push(rule); };
  const files = facts.files || [];
  const head7 = String(facts.head_sha || '').slice(0, 7);
  const types = updateTypes(facts);

  if (!rules.renovate_pr_authors.includes(facts.author)) add('EXCLUDED', 'not-renovate', `PR author ${facts.author} is not Renovate`);
  if (!/^[0-9a-f]{40}$/.test(facts.head_sha || '')) add('BLOCKED', 'facts-incomplete', 'head SHA unavailable');
  if (!files.length) add('BLOCKED', 'facts-incomplete', 'no changed files reported');

  // 1. paths
  for (const pc of rules.path_classes) {
    const hit = files.filter(p => anyGlob(p, pc.globs));
    if (hit.length) add(pc.lane, pc.class, `${pc.reason} (${hit[0]}${hit.length > 1 ? ` +${hit.length - 1}` : ''})`, pc.id);
  }
  // 2. commits: every commit authored by Renovate only
  const commits = facts.commits || [];
  if (!commits.length) add('BLOCKED', 'facts-incomplete', 'no commits reported');
  const foreign = commits.filter(c => !c.authors?.length || c.authors.some(a => !rules.renovate_commit_authors.includes(a)));
  if (foreign.length) {
    const who = [...new Set(foreign.flatMap(c => (c.authors?.length ? c.authors : [null]).map(a => a ?? 'unlinked author')))].filter(a => !rules.renovate_commit_authors.includes(a));
    add('LANE-B-AGENT', 'non-renovate-commit', `${foreign.length} commit(s) not authored by Renovate (${who.join(', ')})`);
  }
  if (facts.is_draft) add('LANE-B-AGENT', 'draft', 'PR is a draft');
  // 3. update type
  if (types.some(t => rules.update_type_rules.owner_update_types.includes(t))) add('LANE-B-OWNER', 'major', `update type ${types.join('+')}`);
  const pre = new RegExp(rules.update_type_rules.prerelease_pattern, 'i');
  const preHit = (facts.packages || []).filter(p => pre.test(String(p.to || '')));
  if (preHit.length) add('LANE-B-OWNER', 'prerelease', `prerelease target ${preHit.map(p => `${p.name} ${p.to}`).join(', ')}`);
  if (!(facts.packages || []).length) add('LANE-B-AGENT', 'package-unknown', 'no package table in the PR body; the update cannot be checked against holds and rules');
  // 4. holds
  for (const h of holdMatches(facts, opts.holdRules)) add('LANE-B-OWNER', 'hold', `${h.package}: ${h.why} — ${h.title}`, h.id);
  // 5. per-package rules
  for (const r of rules.package_rules) {
    const hit = packageRuleHits(facts, r, types);
    if (!hit) continue;
    if (r.needs === 'owner') { add('LANE-B-OWNER', 'rule-owner', `${r.id}: ${r.reason}`, r.id); continue; }
    if (r.needs === 'agent') { add('LANE-B-AGENT', 'rule-agent', `${r.id}: ${r.reason}`, r.id); continue; }
    // deterministic: evaluate what step 1 can evaluate from the facts
    if (r.condition?.type === 'check-success') {
      const ev = evaluateChecks([{ workflow: r.condition.workflow, check: r.condition.check }], facts.checks, now, rules.check_max_age_days);
      if (ev.missing.length || ev.failing.length || ev.pending.length) add('LANE-B-AGENT', 'rule-unsatisfied', `${r.id}: needs ${r.condition.workflow} / ${r.condition.check} SUCCESS on ${head7}`, r.id);
      else if (!ruleIds.includes(r.id)) ruleIds.push(r.id);
    } else {
      add('LANE-B-AGENT', 'rule-not-evaluated', `${r.id}: ${r.condition?.compare || 'condition'} (${r.step1 || 'not evaluated in step 1'})`, r.id);
    }
  }
  // 6. daytime vet markers: only the trusted bot's marker on the CURRENT head counts,
  //    and it can only move a PR toward lane B (never toward lane A).
  for (const v of facts.vet_markers || []) {
    if (!rules.trusted_vet_authors.includes(v.author)) { add('LANE-A', 'note', `ignored vet marker by untrusted author ${v.author}`); continue; }
    if (v.sha !== facts.head_sha) continue;
    if (v.verdict === 'HOLD') add('LANE-B-OWNER', 'vet-hold', `current-head vet verdict HOLD by ${v.author}`);
    else if (!['AUTO-MERGE', 'NIGHTLY', 'MERGED'].includes(v.verdict)) add('LANE-B-AGENT', 'vet-declined', `current-head vet verdict ${v.verdict} by ${v.author}`);
  }
  // 7. checks (the merge guard)
  const cm = expectedChecks(files, rules);
  const unval = cm.unvalidated.filter(p => !p.startsWith('kubernetes/'));
  if (unval.length) add('LANE-B-AGENT', 'no-pr-validation', `no PR-triggered validation for ${unval.join(', ')}`);
  const ev = evaluateChecks(cm.expected, facts.checks, now, rules.check_max_age_days);
  if (ev.failing.length) add('BLOCKED', 'check-failing', `failing: ${ev.failing.join('; ')}`);
  if (ev.missing.length || ev.pending.length) add('BLOCKED', 'checks-incomplete', `waiting for: ${[...ev.pending, ...ev.missing].join('; ')}`);
  // 8. cluster effect
  const ce = clusterEffect(files, rules);

  const decisive = findings.filter(f => f.class !== 'note');
  const lane = decisive.length ? decisive.reduce((a, f) => (RANK[f.lane] < RANK[a] ? f.lane : a), 'LANE-A') : 'LANE-A';
  const top = decisive.find(f => f.lane === lane);
  const ownImmediate = ce.own_images.some(id => rules.own_images.find(o => o.id === id)?.cluster_effect === 'immediate');
  const aClass = ce.effect === 'immediate' ? (ownImmediate && !files.some(p => p.startsWith('kubernetes/')) ? 'immediate-cluster-change' : 'cluster-change')
    : ce.effect === 'delayed' ? 'delayed-cluster-change' : 'no-cluster-effect';
  const cls = top ? top.class : aClass;
  const reason = top ? top.reason
    : `${ev.expected.length} expected checks SUCCESS on ${head7}; Renovate-only commits; ${aClass}${ce.targets.length ? ` (${ce.targets.join(', ')})` : ''}${ce.own_images.length ? ` via own image ${ce.own_images.join(', ')}` : ''}`;
  const pk = (facts.packages || [])[0] || {};
  return {
    schema: 'haynes.upgrade-lane/v1',
    pr: facts.number,
    head: facts.head_sha,
    fingerprint: createHash('sha256').update(JSON.stringify([(facts.packages || []).map(p => [p.name, p.from, p.to]), [...files].sort()])).digest('hex').slice(0, 16),
    title: facts.title,
    package: pk.name ?? null, from: pk.from ?? null, to: pk.to ?? null,
    packages: facts.packages || [],
    update_type: types.join('+') || 'unknown',
    lane, class: cls, reason,
    rules: ruleIds,
    findings: findings.map(f => `${f.lane}/${f.class}: ${f.reason}`),
    checks: ev,
    cluster_effect: ce.effect, targets: ce.targets, own_images: ce.own_images,
  };
}

// ── facts from gh (READ-ONLY) ────────────────────────────────────────────────
// The one place this file starts a process. Anything that is not a read is refused
// before it runs; scripts/upgrade-agent/lane-classify-selftest.mjs proves the refusals.
export function isReadOnlyCommand(cmd, argv) {
  if (cmd === 'gh') {
    if (argv[0] === 'pr') return ['list', 'view', 'checks', 'diff'].includes(argv[1]);
    if (argv[0] === 'api') {
      for (let i = 1; i < argv.length; i++) {
        const a = argv[i];
        if (['-X', '--method'].includes(a)) { if (String(argv[i + 1]).toUpperCase() !== 'GET') return false; i++; continue; }
        if (/^--method=/.test(a) && a.slice(9).toUpperCase() !== 'GET') return false;
        if (/^-X./.test(a) && a.slice(2).toUpperCase() !== 'GET') return false;
        if (['-f', '-F', '--field', '--raw-field', '--input'].includes(a) || /^--(raw-)?field=|^--input=|^-[fF]./.test(a)) return false;
        if (a === 'graphql') return false; // mutations ride graphql; reads here use REST
      }
      return true;
    }
    return false;
  }
  if (cmd === 'kubectl') {
    const valued = new Set(['-n', '--namespace', '-o', '--output', '-l', '--selector', '--field-selector', '--request-timeout', '--context', '--kubeconfig', '--chunk-size']);
    for (let i = 0; i < argv.length; i++) {
      const a = argv[i];
      if (valued.has(a)) { i++; continue; }
      if (a.startsWith('-')) continue;
      return a === 'get'; // the first positional argument is the verb
    }
    return false;
  }
  return false;
}

export function runReadOnly(cmd, argv, { env = process.env, allowFail = false } = {}) {
  if (!isReadOnlyCommand(cmd, argv)) throw new Error(`refused non-read command: ${cmd} ${argv.slice(0, 3).join(' ')}`);
  const r = spawnSync(cmd, argv, { encoding: 'utf8', env, maxBuffer: 64 * 1024 * 1024 });
  if (r.status !== 0) {
    if (allowFail) return null;
    throw new Error(`${cmd} ${argv.slice(0, 3).join(' ')} failed (exit ${r.status}): ${String(r.stderr || r.error || '').slice(0, 300)}`);
  }
  return r.stdout;
}

// The Renovate body table: | Package | (Type |) Update | Change |
export function parsePackageTable(body) {
  const lines = String(body || '').split('\n');
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    const head = lines[i].trim();
    if (!/^\|\s*Package\s*\|/i.test(head)) continue;
    const cols = head.split('|').slice(1, -1).map(s => s.trim().toLowerCase());
    const iPkg = cols.indexOf('package'), iUpd = cols.indexOf('update'), iChg = cols.indexOf('change');
    for (let j = i + 2; j < lines.length && lines[j].trim().startsWith('|'); j++) {
      const cells = lines[j].trim().split('|').slice(1, -1).map(s => s.trim());
      const pkgCell = cells[iPkg] || '';
      const name = (/\[([^\]]+)\]/.exec(pkgCell) || [null, pkgCell.split(/\s/)[0]])[1].replace(/`/g, '');
      const chg = cells[iChg] || '';
      const vals = [...chg.matchAll(/`([^`]+)`/g)].map(m => m[1]);
      out.push({ name, update: (cells[iUpd] || '').toLowerCase() || null, from: vals[0] ?? null, to: vals[vals.length - 1] ?? null });
    }
    break;
  }
  return out;
}

const VET_RE = /^<!-- shepherd-vet sha=([0-9a-f]{40}) -->\s*\n+\s*VERDICT:\s*([A-Za-z-]+)/;
export function parseVetMarkers(comments) {
  const out = [];
  for (const c of comments || []) {
    const m = VET_RE.exec(String(c.body || ''));
    if (m) out.push({ author: c.author, sha: m[1], verdict: m[2].toUpperCase() });
  }
  return out;
}

export function gatherFacts(repo, number, run = runReadOnly) {
  const v = JSON.parse(run('gh', ['pr', 'view', String(number), '-R', repo, '--json',
    'number,headRefOid,headRefName,title,author,isDraft,labels,files,commits,statusCheckRollup,body,changedFiles']));
  // Comments over REST: user.login carries the [bot] suffix a person cannot register,
  // which GraphQL's author.login drops. The repository is public, so this matters.
  const comments = JSON.parse(run('gh', ['api', '--paginate', '--slurp', `repos/${repo}/issues/${number}/comments?per_page=100`])).flat();
  const files = (v.files || []).map(f => f.path);
  return {
    number: v.number,
    head_sha: v.headRefOid,
    title: v.title,
    branch: v.headRefName,
    author: v.author?.login,
    is_draft: !!v.isDraft,
    labels: (v.labels || []).map(l => l.name),
    files: files.length === v.changedFiles ? files : [...files, ...(v.changedFiles > files.length ? ['<file list truncated>'] : [])],
    commits: (v.commits || []).map(c => ({ sha: c.oid, authors: (c.authors || []).map(a => a.login || null) })),
    packages: parsePackageTable(v.body),
    checks: (v.statusCheckRollup || []).filter(c => c.__typename === 'CheckRun').map(c => ({
      workflow: c.workflowName, name: c.name, status: c.status, conclusion: c.conclusion, completed_at: c.completedAt, started_at: c.startedAt })),
    vet_markers: parseVetMarkers(comments.map(c => ({ author: c.user?.login, body: c.body }))),
  };
}

export function listRenovatePrs(repo, rules, run = runReadOnly) {
  const prs = JSON.parse(run('gh', ['pr', 'list', '-R', repo, '--state', 'open', '--limit', '200', '--json', 'number,author']));
  return prs.filter(p => rules.renovate_pr_authors.includes(p.author?.login)).map(p => p.number).sort((a, b) => a - b);
}

export function fetchHoldRules(repo, run = runReadOnly) {
  const meta = JSON.parse(run('gh', ['api', `repos/${repo}/contents/.renovate/holds.json5?ref=main`]));
  return parseJson5(Buffer.from(meta.content, 'base64').toString('utf8')).packageRules || [];
}

export function loadRules(path) {
  return JSON.parse(readFileSync(path || join(dirname(fileURLToPath(import.meta.url)), 'upgrade-rules.json'), 'utf8'));
}

export function formatTable(records) {
  const rows = [['PR', 'LANE', 'CLASS', 'UPDATE', 'REASON']];
  for (const r of records) rows.push([`#${r.pr}`, r.lane, r.class, r.update_type, String(r.reason).replace(/\s+/g, ' ').slice(0, 150)]);
  const w = [0, 1, 2, 3].map(i => Math.max(...rows.map(r => r[i].length)));
  return rows.map(r => r.map((c, i) => (i < 4 ? c.padEnd(w[i]) : c)).join('  ')).join('\n');
}

// ── CLI: node lane-classify.mjs [--json] [--rules FILE] [--holds FILE] [--repo R] [PR...]
async function main(argv) {
  const opt = { json: false, rules: null, holds: null, repo: process.env.UPGRADE_AGENT_REPO || 'thaynes43/haynes-ops', prs: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--json') opt.json = true;
    else if (a === '--rules') opt.rules = argv[++i];
    else if (a === '--holds') opt.holds = argv[++i];
    else if (a === '--repo') opt.repo = argv[++i];
    else if (/^\d+$/.test(a)) opt.prs.push(Number(a));
    else { console.error(`usage: lane-classify.mjs [--json] [--rules FILE] [--holds FILE] [--repo OWNER/NAME] [PR...]`); process.exit(2); }
  }
  const rules = loadRules(opt.rules);
  const holdRules = opt.holds ? parseJson5(readFileSync(opt.holds, 'utf8')).packageRules : fetchHoldRules(opt.repo);
  const nums = opt.prs.length ? opt.prs : listRenovatePrs(opt.repo, rules);
  const now = Date.now();
  const records = nums.map(n => {
    try { return classify(gatherFacts(opt.repo, n), rules, { now, holdRules }); }
    catch (e) { return { schema: 'haynes.upgrade-lane/v1', pr: n, lane: 'BLOCKED', class: 'facts-incomplete', reason: `read failed: ${e.message}`, update_type: 'unknown', rules: [], checks: {} }; }
  });
  process.stdout.write(opt.json ? JSON.stringify(records, null, 2) + '\n' : formatTable(records) + '\n');
}

// ConfigMap mounts are symlinks and Node resolves the main module's real path, so
// compare real paths (a plain string compare would never run main() in the pod).
export const isMain = (metaUrl) => { try { return !!process.argv[1] && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(metaUrl)); } catch { return false; } };
if (isMain(import.meta.url)) {
  main(process.argv.slice(2)).catch(e => { console.error(`lane-classify: ${e.message}`); process.exit(1); });
}
