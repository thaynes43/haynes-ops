#!/usr/bin/env node
// Self-test for the nightly upgrade lane classifier (OP512 step 1).
//
// WHY THIS EXISTS
// ---------------
// lane-classify.mjs decides which Renovate PRs the unattended night lane may merge.
// A wrong LANE-A is an unattended merge nobody looked at, so every rule in the design
// (OP512 comment 6193, "Classification" and "The merge guard") is pinned here against
// facts captured from the real queue on 2026-11-09 (fixtures/lane-queue-20261109.json:
// gh pr view of each PR, read-only) plus small, named mutations of those facts.
//
// It also guards the registry against drift:
//   * every on.pull_request.paths filter in .github/workflows is in the check map,
//     with the same paths, the same workflow name and check names that exist there;
//   * every own-image class matches .renovate/autoMerge.json5 and its pin files;
//   * the classifier's lanes for the 27 PRs two reviewers judged on 2026-11-09
//     (OP513 comment 6182) are pinned, and no PR a reviewer held is LANE-A.
//
// Usage: node scripts/upgrade-agent/lane-classify-selftest.mjs
// Requires: node 24. No network, no cluster access.
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../..');
const RES = join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/resources');
const L = await import(join(RES, 'lane-classify.mjs'));
const rules = L.loadRules(join(RES, 'upgrade-rules.json'));
const holdRules = L.parseJson5(readFileSync(join(ROOT, '.renovate/holds.json5'), 'utf8')).packageRules;
const fx = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/lane-queue-20261109.json'), 'utf8'));
const NOW = Date.parse(fx.now);

let fails = 0, passes = 0;
const ok = (cond, name, detail = '') => { if (cond) passes++; else { fails++; console.log(`FAIL ${name}${detail ? `\n     ${detail}` : ''}`); } };
const clone = o => JSON.parse(JSON.stringify(o));
const fact = n => { const p = fx.prs.find(x => x.facts.number === n); if (!p) throw new Error(`no fixture #${n}`); return clone(p.facts); };
const cls = (facts) => L.classify(facts, rules, { now: NOW, holdRules });
const expectLane = (name, facts, lane, klass) => {
  const r = cls(facts);
  ok(r.lane === lane && (!klass || r.class === klass), name, `got ${r.lane}/${r.class}: ${r.reason}`);
  return r;
};

// ── 1. the named cases from the work order ────────────────────────────────────
{
  // leaf patch, green checks: kubectl 1.37.2 for two CronJobs (#4477)
  const r = expectLane('leaf patch with green checks is LANE-A', fact(4477), 'LANE-A', 'cluster-change');
  ok(r.checks.expected.includes('Flux Local / Flux Local - Test (main)'), 'kubernetes/** PR expects Flux Local - Test (main)');

  // kubernetes/** PR whose flux-local Test leg was skipped (renamed to the unexpanded matrix name)
  const f = fact(4693);
  for (const c of f.checks) if (c.name === 'Flux Local - Test (main)') { c.name = 'Flux Local - Test (${{ matrix.cluster }})'; c.conclusion = 'SKIPPED'; }
  const s = expectLane('kubernetes/** PR with the Test leg skipped is not LANE-A', f, 'BLOCKED', 'checks-incomplete');
  ok(s.checks.missing.some(m => m.includes('Flux Local - Test (main)')), 'skipped Test leg is reported missing');
  const g = fact(4693);
  for (const c of g.checks) if (c.name === 'Flux Local - Diff (main/helmrelease)') c.conclusion = 'SKIPPED';
  expectLane('kubernetes/** PR with a Diff leg SKIPPED is not LANE-A', g, 'BLOCKED', 'checks-incomplete');

  // scripts/** PR whose (non-required) build check fails: sentencepiece in audio-authoring (#4662)
  const b = fact(4662);
  for (const c of b.checks) if (c.workflow === 'Build and Push Audio Authoring Image') c.conclusion = 'FAILURE';
  const br = expectLane('scripts/** PR with a failing non-required build check is BLOCKED', b, 'BLOCKED', 'check-failing');
  ok(br.checks.failing.some(x => x.includes('build-and-push')), 'failing build check is named');

  // a path with no PR validation: docs/requirements.txt (#4708, publishmkdocs runs on push only)
  expectLane('docs/requirements.txt has no PR validation, never LANE-A', fact(4708), 'LANE-B-AGENT', 'no-pr-validation');

  // a major (#4376, node 26)
  expectLane('a major is LANE-B-OWNER', fact(4376), 'LANE-B-OWNER', 'major');

  // a v1-restarting path (#4065, cursor-mcp requirements in dev/dev-env)
  const v = fact(4065); v.is_draft = false;
  expectLane('a v1-restarting path is LANE-B-OWNER', v, 'LANE-B-OWNER', 'v1-restart');

  // a dev-env-system path (#3710)
  expectLane('a dev-env-system path is EXCLUDED', fact(3710), 'EXCLUDED', 'v2-coordinator');

  // own-image source PR: delayed cluster change (#4662 audio-authoring) and the
  // digest-less whisper pin: immediate
  const d = expectLane('own-image source PR is a delayed cluster change', fact(4662), 'LANE-A', 'delayed-cluster-change');
  ok(d.cluster_effect === 'delayed' && d.own_images.includes('audio-authoring'), 'audio-authoring source -> delayed', JSON.stringify(d.own_images));
  const w = fact(4662);
  w.files = ['scripts/wyoming-whisper-gpu/requirements.txt'];
  w.packages = [{ name: 'faster-whisper', update: 'patch', from: '1.2.0', to: '1.2.1' }];
  w.checks = w.checks.filter(c => !c.workflow.startsWith('Build and Push Audio'))
    .concat([{ workflow: 'Build and Push Wyoming Whisper GPU Image', name: 'build-and-push', status: 'COMPLETED', conclusion: 'SUCCESS', completed_at: fx.now }]);
  const wr = expectLane('whisper source PR (pin without digest) is an immediate cluster change', w, 'LANE-A', 'immediate-cluster-change');
  ok(wr.targets.includes('kubernetes/main/apps/ai/wyoming-protocol'), 'whisper target recorded', JSON.stringify(wr.targets));
  const cu = expectLane('comfyui source PR (pin stays manual) has no cluster effect', fact(4372), 'LANE-A', 'no-cluster-effect');
  ok(cu.cluster_effect === 'none', 'comfyui source -> none');

  // non-Renovate commit on the branch
  const n = fact(4477);
  n.commits.push({ sha: 'f'.repeat(40), authors: ['thaynes43'] });
  expectLane('a non-Renovate commit sends the PR to LANE-B-AGENT', n, 'LANE-B-AGENT', 'non-renovate-commit');
  const u = fact(4477); u.commits[0].authors = [null];
  expectLane('a commit with an unlinked author is not Renovate-only', u, 'LANE-B-AGENT', 'non-renovate-commit');

  // vet marker posted by a non-bot author is ignored; the bot's own HOLD counts
  const m = fact(4477);
  m.vet_markers = [{ author: 'haynes-ops-bot', sha: m.head_sha, verdict: 'HOLD' }, { author: 'someone', sha: m.head_sha, verdict: 'NEEDS-SESSION' }];
  const mr = expectLane('vet markers by non-bot authors are ignored', m, 'LANE-A');
  ok(mr.findings.some(x => /ignored vet marker by untrusted author haynes-ops-bot/.test(x)), 'ignored marker is reported');
  const t = fact(4477);
  t.vet_markers = [{ author: 'haynes-ops-bot[bot]', sha: t.head_sha, verdict: 'HOLD' }];
  expectLane("the bot's current-head HOLD moves the PR to LANE-B-OWNER", t, 'LANE-B-OWNER', 'vet-hold');
  const o = fact(4477);
  o.vet_markers = [{ author: 'haynes-ops-bot[bot]', sha: 'a'.repeat(40), verdict: 'HOLD' }];
  expectLane('a vet marker for an older head does not count', o, 'LANE-A');
  const a = fact(4708);
  a.vet_markers = [{ author: 'haynes-ops-bot[bot]', sha: a.head_sha, verdict: 'AUTO-MERGE' }];
  expectLane('a vet marker never moves a PR toward LANE-A', a, 'LANE-B-AGENT', 'no-pr-validation');

  // mixed paths take the strictest lane
  const x = fact(4477);
  x.files.push('docs/requirements.txt');
  expectLane('leaf + unvalidated path takes the stricter LANE-B-AGENT', x, 'LANE-B-AGENT', 'no-pr-validation');
  const y = fact(4218);
  const yr = expectLane('v1 pin + dev-env-ops pin takes EXCLUDED (strictest)', y, 'EXCLUDED', 'merger-own-path');
  ok(yr.findings.some(f => f.startsWith('LANE-B-OWNER/v1-restart')), 'the v1-restart finding is still reported');
  const z = fact(4477);
  z.files.push('kubernetes/main/apps/dev-env-system/keeper/app/helmrelease.yaml');
  expectLane('leaf + dev-env-system path is EXCLUDED', z, 'EXCLUDED', 'v2-coordinator');
}

// ── 2. other rules ────────────────────────────────────────────────────────────
{
  expectLane('a hold matches by package and file (k8s.io 0.37 in archive-guard)', fact(3915), 'LANE-B-OWNER', 'hold');
  const h = fact(3915);
  for (const p of h.packages) p.to = 'v0.36.6';
  for (const c of h.checks) if (c.conclusion === 'FAILURE') c.conclusion = 'SUCCESS';
  expectLane('the same packages inside allowedVersions are not held', h, 'LANE-A', 'no-cluster-effect');
  expectLane('python 3.15 in studio-render is held', fact(4642), 'LANE-B-OWNER', 'hold');
  expectLane('a digest update inside a held line is not held (python 3.14 base)', fact(4334), 'LANE-A', 'no-cluster-effect');
  const q = fact(4477);
  q.files = ['kubernetes/main/apps/downloads/qbittorrent/app/helmrelease.yaml'];
  q.packages = [{ name: 'ghcr.io/home-operations/qbittorrent', update: 'minor', from: '5.2.4', to: '5.3.0' }];
  q.labels = ['type/minor'];
  const qr = expectLane('qBittorrent 5.3 is held (holds.json5 <5.3.0)', q, 'LANE-B-OWNER', 'hold');
  ok(qr.rules.includes('qbittorrent-allowed-client'), 'qBittorrent rule recorded');
  q.packages[0].to = '5.2.5'; q.packages[0].update = 'minor';
  expectLane('qBittorrent minor inside the hold still needs the MAM list (not evaluated in step 1)', q, 'LANE-B-AGENT', 'rule-not-evaluated');
  const pr = fact(4477);
  pr.packages = [{ name: 'docker.io/alpine/kubectl', update: 'patch', from: '1.37.1', to: '1.38.0-rc.1' }];
  expectLane('a prerelease target is LANE-B-OWNER', pr, 'LANE-B-OWNER', 'prerelease');
  expectLane('calibre with EPUB Convert green on head is LANE-A', fact(4340), 'LANE-A', 'cluster-change');
  const ca = fact(4340);
  ca.checks = ca.checks.filter(c => c.workflow !== 'LazyLibrarian EPUB Convert');
  const car = expectLane('calibre without EPUB Convert on head is not LANE-A', ca, 'LANE-B-AGENT', 'rule-unsatisfied');
  ok(car.findings.some(f => f.startsWith('BLOCKED/checks-incomplete')), 'the missing check is also a wait');
  expectLane('kometa needs an agent probe', fact(4521), 'LANE-B-AGENT', 'rule-agent');
  expectLane('actions-runner Dockerfile without VERSION needs an agent edit', fact(4189), 'LANE-B-AGENT', 'rule-agent');
  expectLane('ComfyUI version without IMAGE_TAG needs an agent edit', fact(4349), 'LANE-B-AGENT', 'rule-agent');
  expectLane('Dragonfly 2.1 licence change is for Tom', fact(4658), 'LANE-B-OWNER', 'rule-owner');
  const cil = fact(4477);
  cil.files = ['kubernetes/main/apps/kube-system/cilium/app/helmrelease.yaml'];
  cil.packages = [{ name: 'cilium', update: 'minor', from: '1.18.4', to: '1.19.0' }]; cil.labels = ['type/minor'];
  expectLane('cilium minor is one-way (owner)', cil, 'LANE-B-OWNER', 'rule-owner');
  cil.packages[0].update = 'patch'; cil.packages[0].to = '1.18.5'; cil.labels = ['type/patch'];
  expectLane('cilium patch stays LANE-A', cil, 'LANE-A', 'cluster-change');
  const rk = fact(4477);
  rk.files = ['kubernetes/main/apps/storage/rook-ceph/rook-ceph/app/helmrelease.yaml'];
  expectLane('any rook-ceph bump needs the embedded-Ceph check (agent)', rk, 'LANE-B-AGENT', 'rule-agent');
  expectLane('a merger-own path (shepherd toolchain) is EXCLUDED', fact(4336), 'EXCLUDED', 'merger-own-path');
  expectLane('a workflow file is EXCLUDED', fact(4384), 'EXCLUDED', 'workflow-file');
  const re = fact(4477); re.files = ['.renovate/holds.json5'];
  expectLane('Renovate config is EXCLUDED', re, 'EXCLUDED', 'renovate-config');
  const np = fact(4477); np.author = 'app/haynes-dev-bot';
  expectLane('a PR not opened by Renovate is EXCLUDED', np, 'EXCLUDED', 'not-renovate');
  const dr = fact(4477); dr.is_draft = true;
  expectLane('a draft is not LANE-A', dr, 'LANE-B-AGENT', 'draft');
  const pend = fact(4477);
  for (const c of pend.checks) if (c.name === 'Flux Local - Success') { c.status = 'IN_PROGRESS'; c.conclusion = null; }
  expectLane('a pending required check is a wait', pend, 'BLOCKED', 'checks-incomplete');
  const rer = fact(4662);
  rer.checks.push({ workflow: 'Build and Push Audio Authoring Image', name: 'build-and-push', status: 'COMPLETED', conclusion: 'FAILURE', completed_at: '2026-11-01T00:00:00Z' });
  expectLane('an older failed attempt superseded by a newer success does not block', rer, 'LANE-A', 'delayed-cluster-change');
  const st = cls(fact(4477));
  ok(st.checks.stale.length > 0, '#4477 checks from 2026-10-31 are stale at 2026-11-09 (Renovate retry before a merge)');
  const nopk = fact(4477); nopk.packages = [];
  expectLane('a PR without a package table is not LANE-A', nopk, 'LANE-B-AGENT', 'package-unknown');
}

// ── 3. helpers ────────────────────────────────────────────────────────────────
{
  ok(L.matchGlob('kubernetes/main/apps/a/b.yaml', 'kubernetes/**/*'), 'glob kubernetes/**/* deep');
  ok(L.matchGlob('kubernetes/x', 'kubernetes/**/*'), 'glob kubernetes/**/* shallow');
  ok(!L.matchGlob('docs/x', 'kubernetes/**'), 'glob prefix');
  ok(L.matchGlob('kubernetes/main/apps/dev/dev-env/app/resources/cursor-mcp.py', 'kubernetes/main/apps/dev/dev-env/app/resources/cursor-*'), 'glob star in last segment');
  ok(!L.matchGlob('kubernetes/main/apps/dev/dev-env/app/resources/x/cursor-mcp.py', 'kubernetes/main/apps/dev/dev-env/app/resources/cursor-*'), 'glob star stops at /');
  ok(L.versionAllowed('<5.3.0', '5.2.9') === true && L.versionAllowed('<5.3.0', '5.3.0') === false, 'allowedVersions <');
  ok(L.versionAllowed('/^3\\.12(?:\\.\\d+)?$/', '3.12.4') === true && L.versionAllowed('/^3\\.12(?:\\.\\d+)?$/', '3.13.0') === false, 'allowedVersions regex');
  ok(L.versionAllowed('<2.0.0', 'latest') === null, 'allowedVersions unparseable -> null (counts as held)');
  ok(holdRules.length >= 15 && holdRules.every(r => typeof r === 'object'), `holds.json5 parses (${holdRules.length} rules)`);
  const ga = L.parseJson5(readFileSync(join(ROOT, '.renovate/autoMerge.json5'), 'utf8'));
  ok(Array.isArray(ga.packageRules) && ga.packageRules.length > 5, 'autoMerge.json5 parses');
  const t = L.parsePackageTable('| Package | Type | Update | Change |\n|---|---|---|---|\n| [python](https://x) ([source](https://y)) | final | digest | `35caba8` → `9aaa474` |\n');
  ok(t.length === 1 && t[0].name === 'python' && t[0].update === 'digest' && t[0].to === '9aaa474', 'package table parse', JSON.stringify(t));
  const vm = L.parseVetMarkers([{ author: 'haynes-ops-bot[bot]', body: `<!-- shepherd-vet sha=${'a'.repeat(40)} -->\n\nVERDICT: LEFT — reason` }]);
  ok(vm.length === 1 && vm[0].verdict === 'LEFT', 'vet marker parse');
}

// ── 4. drift: every PR path filter in .github/workflows is in the check map ──────
// A small reader for the `on:` block (the workflows use plain block YAML there).
function prTrigger(text) {
  const lines = text.split('\n');
  const onIdx = lines.findIndex(l => /^(on|"on"|'on'|true):\s*(#.*)?$/.test(l));
  if (onIdx < 0) { const inline = lines.find(l => /^on:\s*\S/.test(l)); return inline && /pull_request/.test(inline) ? { paths: null } : null; }
  let pr = null;
  for (let i = onIdx + 1; i < lines.length && !/^\S/.test(lines[i]); i++) {
    const m = /^  (pull_request|pull_request_target):\s*(.*)$/.exec(lines[i]);
    if (!m) continue;
    pr = { paths: null };
    for (let j = i + 1; j < lines.length && (/^\s{4,}/.test(lines[j]) || !lines[j].trim()); j++) {
      const pm = /^    (paths|paths-ignore):\s*(.*)$/.exec(lines[j]);
      if (!pm) continue;
      if (pm[1] === 'paths-ignore') { pr.ignore = true; continue; }
      if (pm[2].startsWith('[')) { pr.paths = pm[2].replace(/^\[|\]\s*$/g, '').split(',').map(s => s.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean); continue; }
      pr.paths = [];
      for (let k = j + 1; k < lines.length; k++) {
        const im = /^\s{6,}-\s*(.+?)\s*(#.*)?$/.exec(lines[k]);
        if (im) pr.paths.push(im[1].replace(/^['"]|['"]$/g, ''));
        else if (lines[k].trim() && !/^\s*#/.test(lines[k])) break;
      }
    }
  }
  return pr;
}
{
  const dir = join(ROOT, '.github/workflows');
  const files = readdirSync(dir).filter(f => /\.ya?ml$/.test(f));
  const cm = rules.check_map;
  const listed = new Set([...cm.always, ...cm.advisory, ...cm.entries].map(e => e.source));
  for (const f of files) {
    const src = `.github/workflows/${f}`;
    const text = readFileSync(join(dir, f), 'utf8');
    const name = (/^name:\s*(.+?)\s*$/m.exec(text) || [])[1]?.replace(/^['"]|['"]$/g, '');
    const trig = prTrigger(text);
    if (!trig) { ok(cm.no_pr_trigger.includes(src), `drift: ${src} has no PR trigger and is listed as such`); continue; }
    ok(!trig.ignore, `drift: ${src} uses paths-ignore, which the check map cannot express`);
    if (trig.paths === null) {
      ok([...cm.always, ...cm.advisory].some(e => e.source === src), `drift: ${src} runs on every PR and is in always/advisory`);
      continue;
    }
    const e = cm.entries.find(x => x.source === src && x.filter === 'on.pull_request.paths');
    if (!e) { ok(false, `drift: ${src} has on.pull_request.paths but no check-map entry`); continue; }
    ok(JSON.stringify([...e.paths].sort()) === JSON.stringify([...trig.paths].sort()), `drift: ${src} paths match the workflow`, `map=${JSON.stringify(e.paths)} workflow=${JSON.stringify(trig.paths)}`);
    ok(e.workflow === name, `drift: ${src} workflow name`, `map=${e.workflow} workflow=${name}`);
  }
  for (const e of [...cm.always, ...cm.advisory, ...cm.entries]) {
    let text = '';
    try { text = readFileSync(join(ROOT, e.source), 'utf8'); } catch { ok(false, `drift: ${e.source} named by the check map exists`); continue; }
    const name = (/^name:\s*(.+?)\s*$/m.exec(text) || [])[1]?.replace(/^['"]|['"]$/g, '');
    ok(e.workflow === name, `drift: ${e.source} workflow name ${e.workflow}`, `file says ${name}`);
    const jobNames = [...text.matchAll(/^ {4}name:\s*(.+?)\s*$/gm)].map(m => m[1].replace(/^['"]|['"]$/g, ''));
    const jobIds = [...text.matchAll(/^ {2}([A-Za-z0-9_-]+):\s*$/gm)].map(m => m[1]);
    for (const c of e.checks || [e.check]) {
      const hit = jobIds.includes(c) || jobNames.some(n => n === c || new RegExp('^' + n.replace(/[.*+?^()|[\]\\]/g, '\\$&').replace(/\$\\?\{\\?\{[^}]*\}\}/g, '.+').replace(/\$\{\{[^}]*\}\}/g, '.+') + '$').test(c));
      ok(hit, `drift: check "${c}" exists in ${e.source}`);
    }
    if (e.filter === 'job') for (const p of e.paths) ok(text.includes(p), `drift: job-level path ${p} appears in ${e.source}`);
  }
  for (const src of listed) ok(files.includes(src.split('/').pop()), `drift: ${src} still exists`);
  const prCount = files.filter(f => prTrigger(readFileSync(join(dir, f), 'utf8'))).length;
  console.log(`drift: ${files.length} workflows, ${prCount} with a PR trigger, ${cm.entries.length} check-map entries`);
}

// ── 5. own-image classes against autoMerge.json5 and the pins ──────────────────
{
  const am = L.parseJson5(readFileSync(join(ROOT, '.renovate/autoMerge.json5'), 'utf8')).packageRules;
  const renovateAutomerges = image => {
    let v = null; // last matching rule wins, as in Renovate
    for (const r of am) {
      if (!(r.matchPackageNames || []).some(p => { const m = /^\/(.*)\/([a-z]*)$/.exec(p); return m ? new RegExp(m[1], m[2]).test(image) : p === image; })) continue;
      if (r.matchUpdateTypes && !r.matchUpdateTypes.includes('digest')) continue;
      v = r.automerge;
    }
    return v;
  };
  for (const o of rules.own_images) {
    if (!o.image) continue;
    ok(renovateAutomerges(o.image) === o.pin_automerge, `own image ${o.id}: pin_automerge matches autoMerge.json5`, `rules=${o.pin_automerge} renovate=${renovateAutomerges(o.image)}`);
    for (const p of o.pins) {
      let t = '';
      try { t = readFileSync(join(ROOT, p), 'utf8'); } catch { ok(false, `own image ${o.id}: pin file ${p} exists`); continue; }
      ok(t.includes(o.image), `own image ${o.id}: ${p} pins ${o.image}`);
      const lines = t.split('\n').filter(l => l.includes(o.image.split('/').pop()) || /^\s*(tag|digest):/.test(l));
      const digest = /@sha256:[0-9a-f]{64}|^\s*digest:\s*sha256:/m.test(t);
      if (o.pin_follows === 'tag-without-digest') {
        const tagLine = t.split('\n').findIndex(l => l.includes(`repository: ${o.image}`));
        ok(tagLine >= 0 && !/@sha256:/.test(t.split('\n').slice(tagLine, tagLine + 3).join('\n')), `own image ${o.id}: ${p} pins a tag with no digest`);
      } else if (o.pin_follows === 'digest') ok(digest && lines.length > 0, `own image ${o.id}: ${p} pins by digest`);
    }
    if (o.build) ok(rules.check_map.entries.some(e => e.source === o.build) || !o.source_globs.length, `own image ${o.id}: build ${o.build} is in the check map`);
  }
}

// ── 6. the 27 reviewed PRs: pinned lanes and the safety property ──────────────
{
  const pinned = {
    4384: 'EXCLUDED', 4708: 'LANE-B-AGENT', 4636: 'LANE-A', 4372: 'LANE-A', 4334: 'LANE-A', 4335: 'LANE-A', 4662: 'LANE-A', 4336: 'EXCLUDED',
    4189: 'LANE-B-AGENT', 4349: 'LANE-B-AGENT', 4376: 'LANE-B-OWNER', 4377: 'EXCLUDED', 4378: 'EXCLUDED', 4610: 'LANE-B-OWNER', 3915: 'LANE-B-OWNER',
    4477: 'LANE-A', 4693: 'LANE-A', 4333: 'LANE-A', 4340: 'LANE-A', 4521: 'LANE-B-AGENT', 4670: 'LANE-A', 4607: 'LANE-B-AGENT', 4658: 'LANE-B-OWNER',
    4660: 'LANE-B-OWNER', 4710: 'LANE-B-OWNER', 4642: 'LANE-B-OWNER', 4644: 'LANE-B-OWNER',
  };
  const reviewed = fx.prs.filter(p => p.review);
  ok(reviewed.length === 27, `27 reviewed PRs in the fixture (${reviewed.length})`);
  const rows = [];
  for (const { facts, review } of reviewed) {
    const r = cls(clone(facts));
    ok(r.lane === pinned[facts.number], `reviewed #${facts.number}: lane pinned`, `got ${r.lane}/${r.class}, pinned ${pinned[facts.number]}`);
    if (r.lane === 'LANE-A') ok(['MERGE', 'MERGE-WITH-PRECONDITION'].includes(review.verdict), `safety: #${facts.number} is LANE-A only if a reviewer would merge it`, review.verdict);
    rows.push(`#${facts.number}  ${review.verdict.padEnd(23)} ${r.lane.padEnd(12)} ${r.class}`);
  }
  console.log('reviewed PRs (reviewer verdict -> classifier lane/class):\n  ' + rows.join('\n  '));
}

console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
