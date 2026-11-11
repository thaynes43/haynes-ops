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

// R8 (OP550): who includes each shared component, read from the repository. Every
// kustomization.yaml under kubernetes/ is read; an entry of its `components:` list that
// resolves to a directory under kubernetes/shared/components/ with a kustomization.yaml
// makes that file's directory a consumer. Inside kubernetes/shared/components a
// component that lists another (in components: or resources:) passes its consumers on.
// `node scripts/upgrade-agent/lane-classify-selftest.mjs --print-shared-components`
// prints the entries for upgrade-rules.json.
function yamlList(text, key) {
  const lines = text.split('\n');
  const i = lines.findIndex(l => new RegExp(`^${key}:\\s*(#.*)?$`).test(l));
  if (i < 0) return [];
  const out = [];
  for (let j = i + 1; j < lines.length; j++) {
    const l = lines[j];
    if (!l.trim() || /^\s*#/.test(l)) continue;
    const m = /^\s*-\s*['"]?([^'"#\s]+)['"]?\s*(#.*)?$/.exec(l);
    if (!m || !/^\s/.test(l) && !/^-/.test(l)) break;
    out.push(m[1]);
  }
  return out;
}
function scanSharedComponents(root) {
  const walk = d => readdirSync(join(root, d), { withFileTypes: true }).flatMap(e => e.isDirectory() ? walk(`${d}/${e.name}`) : (e.name === 'kustomization.yaml' ? [`${d}/${e.name}`] : []));
  const files = walk('kubernetes');
  const SC = 'kubernetes/shared/components/';
  const comps = new Set(files.filter(f => f.startsWith(SC)).map(f => dirname(f)));
  const direct = new Map([...comps].map(c => [c, new Set()]));
  const includes = new Map([...comps].map(c => [c, new Set()]));
  const norm = p => { const out = []; for (const s of p.split('/')) { if (s === '..') out.pop(); else if (s && s !== '.') out.push(s); } return out.join('/'); };
  for (const f of files) {
    const dir = dirname(f), text = readFileSync(join(root, f), 'utf8');
    const inShared = dir.startsWith(SC);
    for (const key of inShared ? ['components', 'resources'] : ['components']) {
      for (const entry of yamlList(text, key)) {
        const to = norm(`${dir}/${entry}`);
        if (!comps.has(to)) continue;
        if (inShared) includes.get(dir).add(to); else direct.get(to).add(dir);
      }
    }
  }
  // a consumer of component A also consumes everything A includes, transitively
  const consumers = new Map([...comps].map(c => [c, new Set(direct.get(c))]));
  let changed = true;
  while (changed) {
    changed = false;
    for (const [a, inc] of includes) for (const b of inc) for (const c of consumers.get(a)) if (!consumers.get(b).has(c)) { consumers.get(b).add(c); changed = true; }
  }
  return [...comps].sort().map(path => ({ path, consumers: [...consumers.get(path)].sort() }));
}
if (process.argv.includes('--print-shared-components')) {
  console.log(JSON.stringify(scanSharedComponents(ROOT), null, 2));
  process.exit(0);
}
const rules = L.loadRules(join(RES, 'upgrade-rules.json'));
const holdRules = L.parseJson5(readFileSync(join(ROOT, '.renovate/holds.json5'), 'utf8')).packageRules;
const fx = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/lane-queue-20261109.json'), 'utf8'));
const NOW = Date.parse(fx.now);

let fails = 0, passes = 0;
const ok = (cond, name, detail = '') => { if (cond) passes++; else { fails++; console.log(`FAIL ${name}${detail ? `\n     ${detail}` : ''}`); } };
const clone = o => JSON.parse(JSON.stringify(o));
const fact = n => { const p = fx.prs.find(x => x.facts.number === n); if (!p) throw new Error(`no fixture #${n}`); return clone(p.facts); };
const cls = (facts) => L.classify(facts, rules, { now: NOW, holdRules });
const expectLane = (name, facts, lane, klass, rulesX = rules) => {
  const r = L.classify(facts, rulesX, { now: NOW, holdRules });
  ok(r.lane === lane && (!klass || r.class === klass), name, `got ${r.lane}/${r.class}: ${r.reason}`);
  return r;
};
// Story 551: a PR on the shepherd's ramp is EXCLUDED (class shepherd-ramp), the strictest
// lane. The package rules for ramp components (cilium, rook-ceph, Dragonfly) are pinned
// against the rules without that class, so each still fires on its own if a component
// leaves the ramp; section 7 pins what the shipped rules make of them.
const rulesNoRamp = { ...rules, path_classes: rules.path_classes.filter(p => p.id !== 'shepherd-ramp') };

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
  expectLane('Dragonfly 2.1 licence change is for Tom (rules without the ramp class)', fact(4658), 'LANE-B-OWNER', 'rule-owner', rulesNoRamp);
  const cil = fact(4477);
  cil.files = ['kubernetes/main/apps/kube-system/cilium/app/helmrelease.yaml'];
  cil.packages = [{ name: 'cilium', update: 'minor', from: '1.18.4', to: '1.19.0' }]; cil.labels = ['type/minor'];
  expectLane('cilium minor is one-way (owner; rules without the ramp class)', cil, 'LANE-B-OWNER', 'rule-owner', rulesNoRamp);
  cil.packages[0].update = 'patch'; cil.packages[0].to = '1.18.5'; cil.labels = ['type/patch'];
  expectLane('cilium patch is LANE-A by the package rules (rules without the ramp class)', cil, 'LANE-A', 'cluster-change', rulesNoRamp);
  const rk = fact(4477);
  rk.files = ['kubernetes/main/apps/storage/rook-ceph/rook-ceph/app/helmrelease.yaml'];
  expectLane('any rook-ceph bump needs the embedded-Ceph check (agent; rules without the ramp class)', rk, 'LANE-B-AGENT', 'rule-agent', rulesNoRamp);
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

// ── 3b. step 2: version operators, patch fingerprint, own-image pin wait ─────────
{
  // pip tables print `==0.36.2`; every night tick on 2026-11-10 recorded #4757 as held
  // ("cannot compare ==0.36.2 with <2.0.0") although 0.36.2 is inside the hold.
  ok(L.bareVersion('==0.36.2') === '0.36.2' && L.bareVersion('===0.36.2') === '0.36.2' && L.bareVersion('=1.2.3') === '1.2.3' && L.bareVersion('v1.37.2') === '1.37.2', 'bareVersion strips an exact-pin operator and a leading v');
  for (const r of ['>=1.9.0', '^1.9.0', '~1.9.0', '~=1.9', '<3', '!=2.0.0']) ok(L.bareVersion(r) === r, `bareVersion leaves the range ${r} as written`);
  for (const r of ['>=1.9.0', '^1.9.0', '~=1.9']) ok(L.versionAllowed('<2.0.0', r) === null, `a range target ${r} cannot be compared with <2.0.0 (counts as held)`);
  ok(L.versionAllowed('/^1\\./', '^1.2.3') === false, 'a range target does not match a version regex hold');
  const rg = fact(4662); rg.packages = [{ name: 'huggingface-hub', update: null, from: '==0.36.1', to: '>=1.9.0' }];
  expectLane('a range target inside a held package stays held (fail closed)', rg, 'LANE-B-OWNER', 'hold');
  ok(L.bareVersion('latest') === 'latest' && L.bareVersion('vendor-1') === 'vendor-1', 'bareVersion leaves non-versions as written');
  ok(L.versionAllowed('<2.0.0', '==0.36.2') === true && L.versionAllowed('<2.0.0', '==2.0.1') === false, 'allowedVersions compares a pip pin by its version');
  ok(L.versionAllowed('/^3\\.12(?:\\.\\d+)?$/', '==3.12.4') === true, 'allowedVersions regex sees the bare pip version');
  ok(L.versionAllowed('<2.0.0', '==latest') === null, 'a pip pin with no version still cannot be compared (counts as held)');
  const hf = fact(4662);
  hf.packages = [{ name: 'huggingface-hub', update: null, from: '==0.36.1', to: '==0.36.2' }];
  const hfr = cls(hf);
  ok(!hfr.findings.some(f => /hold/.test(f)), 'huggingface-hub ==0.36.2 is not reported as held (<2.0.0)', hfr.findings.join(' | '));
  hf.packages[0].to = '==2.0.1';
  expectLane('huggingface-hub ==2.0.1 is held (<2.0.0)', hf, 'LANE-B-OWNER', 'hold');
  const cmp = fact(4662);
  cmp.packages = [{ name: 'some-lib', update: 'minor', from: '==1.2.0', to: '==1.3.0' }];
  ok(L.updateTypes(cmp).includes('minor') && L.compareVersions('==1.3.0', '1.2.9') === 1, 'compareVersions reads pip pins');
  ok(L.compareVersions('^1.3.0', '1.2.9') === -1, 'compareVersions finds no version in a range (as on main)');

  // patch fingerprint against the night's real rebases (fixtures/lane-rebase-pairs-20261110.json)
  const rp = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/lane-rebase-pairs-20261110.json'), 'utf8'));
  for (const p of rp.pairs) {
    const a = L.patchFingerprint(p.before.diff), b = L.patchFingerprint(p.after.diff);
    ok(a.basis === 'commit-diff' && /^sha256:[0-9a-f]{64}$/.test(a.fingerprint || ''), `fingerprint of #${p.pr} ${p.before.sha.slice(0, 8)} is a sha256`, JSON.stringify(a));
    ok((a.fingerprint === b.fingerprint) === p.same_change, `#${p.pr} ${p.before.sha.slice(0, 8)} -> ${p.after.sha.slice(0, 8)}: fingerprint ${p.same_change ? 'equal (byte-identical rebase)' : 'differs (Renovate changed a line)'}`, `${a.fingerprint} ${b.fingerprint}`);
  }
  const base = rp.pairs[0].before.diff;
  const fp0 = L.patchFingerprint(base).fingerprint;
  ok(L.patchFingerprint(base.replace(/^@@ -(\d+)/gm, (m, n) => `@@ -${Number(n) + 40}`)).fingerprint === fp0, 'fingerprint ignores hunk line numbers (a base that moved)');
  ok(L.patchFingerprint(base.replace(/^index [0-9a-f]+\.\.[0-9a-f]+/gm, 'index 0000000..1111111')).fingerprint === fp0, 'fingerprint ignores index (blob id) lines');
  ok(L.patchFingerprint(base.replace(/^ (.*)$/m, ' changed context $1')).fingerprint === fp0, 'fingerprint ignores context lines');
  ok(L.patchFingerprint(base.replace(/\n/g, '\r\n')).fingerprint === fp0, 'fingerprint is the same with CRLF line ends');
  ok(L.patchFingerprint(base.replace(/^\+(.*)1\.37\.2/m, '+$11.37.3')).fingerprint !== fp0, 'an edited added line changes the fingerprint');
  ok(L.patchFingerprint(base.replace(/^-(?!--)(.*)$/m, '-$1 ')).fingerprint !== fp0, 'trailing whitespace on a removed line changes the fingerprint (no whitespace folding)');
  ok(L.patchFingerprint(base.replace(/^(diff --git .*\n)/m, '$1old mode 100644\nnew mode 100755\n')).fingerprint !== fp0, 'a file mode change changes the fingerprint');
  ok(L.patchFingerprint(base.replace(/^(diff --git .*\n)/m, '$1new file mode 100644\n')).fingerprint !== fp0, 'a new file differs from a modified one');
  const asBody = base.replace(/^--- (a\/\S+)$/m, '--- $1').replace(/^(diff --git .*\n(?:.*\n)*?)--- (a\/\S+)\n\+\+\+ (b\/\S+)\n/m, '$1');
  ok(L.patchFingerprint(asBody).fingerprint !== fp0, 'the ---/+++ header lines are hashed as headers, so dropping them changes the value');
  ok(L.patchFingerprint(base.replace(/^(diff --git .*\n)/m, '$1similarity index 97%\n')).fingerprint === fp0, 'a similarity score line is ignored like the index line');
  const secs = base.split(/(?=^diff --git )/m).filter(Boolean);
  ok(secs.length >= 2 && L.patchFingerprint(secs.slice().reverse().join('')).fingerprint === fp0, 'file order does not matter', `${secs.length} sections`);
  ok(L.patchFingerprint(secs[0]).fingerprint !== fp0, 'dropping a file changes the fingerprint');
  ok(L.patchFingerprint('').fingerprint === null && L.patchFingerprint('').basis === 'unavailable' && L.patchFingerprint(null).basis === 'unavailable', 'an empty or missing diff has no fingerprint');
  ok(L.patchFingerprint('not a diff\n').basis === 'unavailable', 'text with no diff section has no fingerprint');
  const bin = 'diff --git a/x.png b/x.png\nindex 1..2 100644\nBinary files a/x.png and b/x.png differ\n';
  ok(L.patchFingerprint(bin).fingerprint === null && L.patchFingerprint(bin).basis === 'binary' && L.patchFingerprint(base + bin).basis === 'binary', 'a binary section makes the fingerprint null (basis binary)');

  // the record carries it; gatherFacts reads `gh pr diff`; a failed diff read is null
  const withFp = fact(4477); withFp.patch_fingerprint = fp0; withFp.patch_basis = 'commit-diff';
  const rf = cls(withFp);
  ok(rf.fingerprint === fp0 && rf.fingerprint_basis === 'commit-diff' && rf.commit_count === 1 && /^[0-9a-f]{16}$/.test(rf.change_key), 'the record carries fingerprint, basis, commit count and change_key', JSON.stringify({ f: rf.fingerprint, b: rf.fingerprint_basis, c: rf.commit_count, k: rf.change_key }));
  ok(rf.lane === cls(fact(4477)).lane, 'the fingerprint does not change the lane in step 2');
  const noFp = cls(fact(4477));
  ok(noFp.fingerprint === null && noFp.fingerprint_basis === 'unavailable', 'facts without a diff record a null fingerprint');
  const f77 = fact(4477);
  const stubView = { number: 4477, headRefOid: f77.head_sha, headRefName: f77.branch, title: f77.title, author: { login: f77.author }, isDraft: false, labels: [], files: f77.files.map(path => ({ path })), changedFiles: f77.files.length, commits: [{ oid: f77.head_sha, authors: [{ login: 'renovate[bot]' }] }], statusCheckRollup: [], body: '' };
  let diffCalls = [];
  const stubRun = (diffOut, view = stubView) => (cmd, argv) => {
    if (!L.isReadOnlyCommand(cmd, argv)) throw new Error('stub refused a non-read');
    if (argv[0] === 'pr' && argv[1] === 'view') return JSON.stringify(view);
    if (argv[0] === 'api' && argv.some(a => /\/commits\//.test(a))) { diffCalls.push(argv); if (diffOut instanceof Error) throw diffOut; return diffOut; }
    if (argv[0] === 'api') return '[[]]';
    throw new Error(`unexpected ${argv.join(' ')}`);
  };
  const g1 = L.gatherFacts('x/y', 4477, stubRun(rp.pairs[0].after.diff));
  ok(g1.patch_fingerprint === fp0 && g1.patch_basis === 'commit-diff', 'gatherFacts fingerprints the head commit\'s diff (the rebased #4477 equals the reviewed one)');
  ok(diffCalls.length === 1 && diffCalls[0].includes(`repos/x/y/commits/${f77.head_sha}`) && diffCalls[0].includes('Accept: application/vnd.github.diff'), 'the diff is fetched by the head SHA read in the same pass (no race with a push)', JSON.stringify(diffCalls));
  diffCalls = [];
  const two = { ...stubView, commits: [{ oid: 'c'.repeat(40), authors: [] }, { oid: f77.head_sha, authors: [] }] };
  const g4 = L.gatherFacts('x/y', 4477, stubRun(rp.pairs[0].after.diff, two));
  ok(g4.patch_fingerprint === null && g4.patch_basis === 'multi-commit' && diffCalls.length === 0, 'more than one commit: no fingerprint and no diff read');
  const moved = { ...stubView, commits: [{ oid: 'd'.repeat(40), authors: [] }] };
  ok(L.gatherFacts('x/y', 4477, stubRun(rp.pairs[0].after.diff, moved)).patch_fingerprint === null, 'a commit list that does not end at the head gives no fingerprint');
  const g2 = L.gatherFacts('x/y', 4477, stubRun(null));
  ok(g2.patch_fingerprint === null && g2.patch_basis === 'unavailable', 'a failed diff read (allowFail null) leaves the fingerprint null');
  const g3 = L.gatherFacts('x/y', 4477, stubRun(new Error('boom')));
  ok(g3.patch_fingerprint === null && g3.patch_basis === 'unavailable', 'a throwing diff read leaves the fingerprint null');

  // own images whose digest pin Renovate auto-merges wait for Renovate's next run
  const aw = cls(fact(4662));
  ok(aw.cluster_effect === 'delayed' && aw.cluster_effect_wait?.waits_for === 'renovate-pin-after-next-push-to-main' && aw.cluster_effect_wait.images.includes('audio-authoring'), 'audio-authoring source PR records the wait for the Renovate pin', JSON.stringify(aw.cluster_effect_wait));
  ok(/pin PR .* has merged/.test(aw.cluster_effect_wait?.verified_when || ''), 'the wait says when the unit counts as verified');
  const sw = cls(fact(4336));
  ok(sw.lane === 'EXCLUDED' && ['upgrade-shepherd', 'upgrade-agent'].every(i => sw.cluster_effect_wait?.images.includes(i)), 'the shepherd toolchain PR records the wait for both images', JSON.stringify(sw.cluster_effect_wait));
  ok(cls(fact(4372)).cluster_effect_wait === null, 'comfyui source (pin moves by hand) records no wait');
  ok(cls(fact(4335)).cluster_effect_wait === null, 'dev-env v1 source (pin never auto-merges) records no wait');
  ok(cls(fact(4477)).cluster_effect_wait === null, 'a kubernetes/** change records no wait');
  const wh = fact(4662);
  wh.files = ['scripts/wyoming-whisper-gpu/requirements.txt'];
  wh.checks = wh.checks.filter(c => !c.workflow.startsWith('Build and Push Audio'))
    .concat([{ workflow: 'Build and Push Wyoming Whisper GPU Image', name: 'build-and-push', status: 'COMPLETED', conclusion: 'SUCCESS', completed_at: fx.now }]);
  const whr = cls(wh);
  ok(whr.cluster_effect === 'immediate' && whr.cluster_effect_wait === null, 'the digest-less whisper pin (immediate) records no wait', JSON.stringify(whr.cluster_effect_wait));
  const pw = rules.own_image_pin_wait;
  ok(pw && rules.own_images.filter(o => o.cluster_effect === 'delayed' && o.pin_automerge === true).length >= 4 && Array.isArray(pw.observed_20261110) && pw.observed_20261110.length === 3, 'upgrade-rules.json documents the pin wait with the night\'s evidence');
}

// ── 3c. OP550: R7 stale checks, R8 shared components, R10 ceph-csi, cluster-wide ──
{
  const gx = JSON.parse(readFileSync(join(ROOT, 'scripts/upgrade-agent/fixtures/lane-gates-20261110.json'), 'utf8'));
  const gfact = n => clone(gx.prs[n]);
  const AT = Date.parse('2026-11-10T06:45:00Z'); // the tick that saw #4770

  // R10: #4607 (ceph-csi-rbd 3.18.2 on gasha01) is lane B by rule, not by the shepherd's LLM verdict
  const c7 = fact(4607); c7.vet_markers = [];
  const c7r = expectLane('#4607 without any vet verdict is LANE-B-AGENT by the ceph-csi rule', c7, 'LANE-B-AGENT', 'rule-agent');
  ok(c7r.rules.includes('ceph-csi-chart') && c7r.rules.includes('ceph-csi-apps') && /no session volume create, resume or attach is in flight/.test(c7r.reason), '#4607 names the ceph-csi rule and the in-flight check it needs', `${c7r.rules} | ${c7r.reason}`);
  const c7v = cls(fact(4607));
  ok(c7v.lane === 'LANE-B-AGENT' && c7v.rules.includes('ceph-csi-chart'), "#4607 with the shepherd's LEFT is still LANE-B-AGENT and records the rule", JSON.stringify(c7v.rules));
  const c7a = fact(4607); c7a.vet_markers = [{ author: 'haynes-ops-bot[bot]', sha: c7a.head_sha, verdict: 'AUTO-MERGE' }];
  expectLane('a vet AUTO-MERGE cannot move ceph-csi to lane A', c7a, 'LANE-B-AGENT', 'rule-agent');
  const cf = fact(4607); cf.files = ['kubernetes/main/apps/storage/ceph-csi/gasha01-cephfs/app/helmrelease.yaml']; cf.packages = [{ name: 'ceph-csi-cephfs', update: 'patch', from: '3.18.1', to: '3.18.2' }]; cf.vet_markers = [];
  expectLane('ceph-csi-cephfs is LANE-B-AGENT too', cf, 'LANE-B-AGENT', 'rule-agent');
  const cp = fact(4477); cp.packages = [{ name: 'ghcr.io/example/other', update: 'patch', from: '1.0.0', to: '1.0.1' }]; cp.files = ['kubernetes/main/apps/storage/ceph-csi/gasha01-rbd/app/helmrelease.yaml'];
  ok(cls(cp).rules.includes('ceph-csi-apps') && cls(cp).lane === 'LANE-B-AGENT', 'any package under the ceph-csi apps is LANE-B-AGENT (path rule)');

  // R8: #4770, the actions-runner pin in kubernetes/shared/components/arc/dind
  const p70 = L.classify(gfact(4770), rules, { now: AT, holdRules });
  const pools = ['demo-console', 'haynes-swarm', 'sigmaphiomicron-com', 'sigoalumni-org'].map(p => `kubernetes/main/apps/arc-runners/${p}/app`);
  ok(p70.lane === 'LANE-A' && p70.class === 'cluster-change', '#4770 is LANE-A cluster-change', `${p70.lane}/${p70.class}: ${p70.reason}`);
  ok(pools.every(t => p70.targets.includes(t)) && p70.targets.includes('kubernetes/main/apps/arc-runners/sigo-alumni') && !p70.targets.some(t => t.startsWith('kubernetes/shared/')), '#4770 targets the four runner pools that include arc/dind, plus sigo-alumni', JSON.stringify(p70.targets));
  ok(p70.via_components.length === 4 && p70.via_components.every(v => v.startsWith('kubernetes/shared/components/arc/dind -> ')), 'the record lists the component and each consumer', JSON.stringify(p70.via_components));
  const um = gfact(4770);
  const r2 = structuredClone(rules); r2.shared_components.entries = r2.shared_components.entries.filter(e => e.path !== 'kubernetes/shared/components/arc/dind');
  ok(L.classify(um, r2, { now: AT, holdRules }).targets.includes('kubernetes/shared/components/arc'), 'a component with no map entry stays the target (the tick reports target-unmapped)');
  const r3 = structuredClone(rules); r3.shared_components.entries.find(e => e.path === 'kubernetes/shared/components/arc/dind').consumers = [];
  ok(L.classify(um, r3, { now: AT, holdRules }).targets.includes('kubernetes/shared/components/arc/dind'), 'a component with no consumer stays the target (fail closed)');
  ok(L.componentFor('kubernetes/shared/components/common/repos/app-template/ocirepository.yaml', rules)?.path === 'kubernetes/shared/components/common/repos/app-template', 'a file maps to the deepest component that contains it');
  // a shared component reaches the consumers' path classes: the app-template chart source
  // reaches dev-env-system, so the v2 coordinator owns it
  const at = fact(4477); at.files = ['kubernetes/shared/components/common/repos/app-template/ocirepository.yaml']; at.packages = [{ name: 'ghcr.io/bjw-s-labs/helm/app-template', update: 'patch', from: '4.6.0', to: '4.6.1' }];
  const atr = expectLane('an app-template bump reaches dev-env-system through the shared component and is EXCLUDED', at, 'EXCLUDED', 'v2-coordinator');
  ok(/via shared component kubernetes\/shared\/components\/common\/repos\/app-template in kubernetes\/main\/apps\/dev-env-system/.test(atr.reason), 'the reason names the component and the consumer', atr.reason);
  ok(atr.cluster_wide.includes('shared-common') && atr.targets.length >= 40, 'the app-template source is cluster-wide and targets every consumer', `${atr.cluster_wide} ${atr.targets.length}`);
  const gz = fact(4477); gz.files = ['kubernetes/shared/components/volsync/aws-extra/replicationsource.yaml'];
  const gzr = cls(gz);
  ok(gzr.targets.includes('kubernetes/main/apps/ai/wyoming-protocol/speech-to-phrase') && gzr.findings.every(f => !/EXCLUDED|LANE-B-OWNER/.test(f)), 'a component with one ordinary consumer maps to it and adds no path class', JSON.stringify(gzr.targets));
  // the map in upgrade-rules.json is the repository's (drift)
  const scanned = scanSharedComponents(ROOT);
  ok(JSON.stringify(scanned) === JSON.stringify(rules.shared_components.entries), 'drift: shared_components in upgrade-rules.json matches the kustomization.yaml files (regenerate with --print-shared-components)',
    JSON.stringify(scanned.filter(e => JSON.stringify(e) !== JSON.stringify(rules.shared_components.entries.find(x => x.path === e.path))).map(e => e.path)));
  ok(scanned.find(e => e.path === 'kubernetes/shared/components/arc/dind').consumers.length === 4, 'drift: arc/dind has the four runner pools');

  // R5: which targets are cluster-wide
  ok(JSON.stringify(cls(fact(4607)).cluster_wide) === '["csi"]', 'ceph-csi is cluster-wide (csi)');
  ok(cls(fact(4477)).cluster_wide.length === 0, 'a leaf CronJob is not cluster-wide');
  ok(L.clusterWide(['kubernetes/main/apps/kube-system/cilium/app/helmrelease.yaml'], rules)[0] === 'cilium' && L.clusterWide(['kubernetes/main/flux/apps.yaml'], rules)[0] === 'flux' && L.clusterWide(['kubernetes/main/apps/storage/rook-ceph/rook-ceph/app/helmrelease.yaml'], rules)[0] === 'rook-ceph', 'flux, cilium and rook-ceph are cluster-wide');

  // R7: checks that predate a main commit to one of the PR's files
  const MC = gx.main_commits_20261110T0010Z;
  ok(L.checksReferenceTime('2026-11-08T04:27:45Z', '2026-11-08T04:28:00Z') === '2026-11-08T04:27:45.000Z' && L.checksReferenceTime(null, '2026-11-08T04:28:00Z') === '2026-11-08T04:28:00.000Z' && L.checksReferenceTime(null, null) === null, 'reference time: the earlier of the head commit and the first check');
  const old = fact(4334); old.head_committed_at = gx.pr4334_old_head.committed_at;
  const oldr = cls(old);
  ok(oldr.checks.reference_time === '2026-11-08T04:27:45.000Z' && JSON.stringify(oldr.files) === JSON.stringify(old.files) && oldr.head_committed_at === old.head_committed_at, "#4334's old head: the record carries files, head_committed_at and checks.reference_time", JSON.stringify({ t: oldr.checks.reference_time, f: oldr.files }));
  const st = L.checksPredateMain(oldr.checks.reference_time, oldr.files, { byPath: MC, unreadable: [] });
  ok(st.newer.length === 1 && st.newer[0].sha.startsWith('a5ccd7c2') && st.newer[0].path === 'scripts/comfyui/Dockerfile' && !st.unreadable.length, "#4334's old head reads stale: #4372 changed scripts/comfyui/Dockerfile on main at 00:00:50Z", JSON.stringify(st));
  const reb = cls(gfact(4334));
  ok(reb.checks.reference_time === '2026-11-10T00:06:49.000Z' && L.checksPredateMain(reb.checks.reference_time, reb.files, { byPath: MC, unreadable: [] }).newer.length === 0, "#4334's rebased head (2417f4fa) is not stale", reb.checks.reference_time);
  ok(L.checksPredateMain('2026-11-10T00:00:50Z', ['a'], { byPath: { a: [{ sha: 'x', at: '2026-11-10T00:00:50Z' }] } }).newer.length === 0, 'a commit at exactly the reference time is not newer (since is inclusive)');
  ok(L.checksPredateMain('2026-11-10T00:00:50Z', ['a'], { byPath: { a: [{ sha: 'x', at: null }] } }).newer.length === 1, 'fail closed: a main commit with no readable date counts as newer');
  ok(L.checksPredateMain(null, ['a'], { byPath: {} }).unreadable.length === 1, 'fail closed: no reference time is unreadable');
  const rerun = fact(4334); rerun.head_committed_at = gx.pr4334_old_head.committed_at;
  for (const c of rerun.checks) { c.started_at = '2026-11-10T00:05:00Z'; c.completed_at = '2026-11-10T00:09:00Z'; }
  ok(cls(rerun).checks.reference_time === '2026-11-08T04:27:45.000Z', 'a re-run after the main commit does not refresh the reference (it reuses the old merge commit; the head date wins)');
  const calls = [];
  const stub = out => (cmd, argv) => { calls.push(argv); if (!L.isReadOnlyCommand(cmd, argv)) throw new Error('not a read'); if (out instanceof Error) throw out; return typeof out === 'function' ? out(argv) : out; };
  const mc = L.mainCommitsTouching('x/y', ['scripts/comfyui/Dockerfile'], '2026-11-08T04:27:45.000Z', stub(JSON.stringify([{ sha: 'a5ccd7c24fc2c8f33164b346df1c8b37cdd6b8e7', commit: { committer: { date: '2026-11-10T00:00:50Z' } } }])));
  ok(mc.byPath['scripts/comfyui/Dockerfile'][0].at === '2026-11-10T00:00:50Z' && !mc.unreadable.length, 'mainCommitsTouching reads the committer date of each commit');
  ok(calls[0][0] === 'api' && calls[0][1] === 'repos/x/y/commits?sha=main&path=scripts%2Fcomfyui%2FDockerfile&since=2026-11-08T04%3A27%3A45.000Z&per_page=10' && calls.length === 1, 'it is one gh api GET per file, on main, since the reference time', JSON.stringify(calls));
  ok(L.mainCommitsTouching('x/y', ['a'], 't', stub(null)).unreadable[0] === 'a' && L.mainCommitsTouching('x/y', ['a'], 't', stub(new Error('403'))).unreadable[0] === 'a' && L.mainCommitsTouching('x/y', ['a'], 't', stub('{"message":"x"}')).unreadable[0] === 'a', 'fail closed: a failed, thrown or non-list read is unreadable');
  ok(L.mainCommitsTouching('x/y', ['<file list truncated>'], 't', stub('[]')).unreadable.length === 1 && L.mainCommitsTouching('x/y', ['a'], null, stub('[]')).unreadable.length === 1, 'fail closed: a truncated file list or no reference time is unreadable');
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
    4477: 'LANE-A', 4693: 'LANE-A', 4333: 'LANE-A', 4340: 'LANE-A', 4521: 'LANE-B-AGENT', 4670: 'LANE-A', 4607: 'LANE-B-AGENT', 4658: 'EXCLUDED' /* story 551: dragonfly is on the shepherd's ramp; LANE-B-OWNER without it */,
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

// ── 7. one owner: a PR on the shepherd's ramp is the shepherd's (story 551) ─────────
// The rule: the shepherd owns every Renovate PR that touches a path on its ramp, and the
// lane never merges one (#4780 traefik was both LANE-A and on the ramp on 2026-11-10).
// The path class shepherd-ramp must name exactly the prefixes the shepherd's pre-filter
// matches: UPGRADE_AGENT_RAMP (the upgrade-shepherd controller's env in helmrelease.yaml)
// through ramp_globs_for (run-shepherd.sh), each prefix as <prefix>**.
{
  const hrText = readFileSync(join(ROOT, 'kubernetes/main/apps/upgrade-agent/shepherd/app/helmrelease.yaml'), 'utf8');
  const sh = readFileSync(join(RES, 'run-shepherd.sh'), 'utf8');
  const rampLines = [...hrText.matchAll(/^\s+UPGRADE_AGENT_RAMP:\s*(.+?)\s*$/gm)].map(m => m[1]);
  ok(rampLines.length === 1, 'helmrelease.yaml sets UPGRADE_AGENT_RAMP once', JSON.stringify(rampLines));
  const ramp = (rampLines[0] || '').split(/\s+/).filter(Boolean);
  const fn = sh.slice(sh.indexOf('ramp_globs_for() {'), sh.indexOf('\n}\n', sh.indexOf('ramp_globs_for() {')));
  const arms = Object.fromEntries([...fn.matchAll(/^\s+([a-z-]+)\)\s+printf '%s' "([^"]*)" ;;/gm)].map(m => [m[1], m[2].split(/\s+/).filter(Boolean)]));
  ok(ramp.length >= 10 && ramp.every(c => arms[c]?.length), 'every ramp component has a path mapping in ramp_globs_for', ramp.filter(c => !arms[c]?.length).join(', '));
  const want = [...new Set(ramp.flatMap(c => arms[c] || []).map(p => `${p.replace(/\/?$/, '/')}**`))].sort();
  const pc = rules.path_classes.find(p => p.id === 'shepherd-ramp');
  ok(pc && pc.lane === 'EXCLUDED' && pc.class === 'shepherd-ramp', 'the shepherd-ramp path class is EXCLUDED, class shepherd-ramp', JSON.stringify(pc && { lane: pc.lane, class: pc.class }));
  const have = [...(pc?.globs || [])].sort();
  ok(JSON.stringify(have) === JSON.stringify(want), "drift: shepherd-ramp names exactly the shepherd's ramp prefixes", `missing: ${want.filter(g => !have.includes(g)).join(', ') || 'none'}; extra: ${have.filter(g => !want.includes(g)).join(', ') || 'none'}`);
  // #4780 as merged on 2026-11-10: one file, traefik-external's ocirepository.yaml
  const tr = fact(4477); tr.number = 4780; tr.files = ['kubernetes/main/apps/network/traefik/traefik-external/app/ocirepository.yaml'];
  tr.packages = [{ name: 'ghcr.io/traefik/helm/traefik', update: 'patch', from: '42.3.1', to: '42.3.2' }]; tr.labels = ['type/patch'];
  expectLane('#4780 (traefik, on the ramp) is LANE-A without the rule', clone(tr), 'LANE-A', 'cluster-change', rulesNoRamp);
  const t2 = expectLane("#4780 is the shepherd's: EXCLUDED/shepherd-ramp", clone(tr), 'EXCLUDED', 'shepherd-ramp');
  ok(t2.rules.includes('shepherd-ramp') && /the shepherd owns it/.test(t2.reason), 'the record names the rule and the owner', `${t2.rules} | ${t2.reason}`);
  for (const [n, f] of [['multus (#4760)', 'kubernetes/main/apps/network/multus/app/helmrelease.yaml'], ['dragonfly-operator (#4761)', 'kubernetes/main/apps/database/dragonfly/app/helmrelease.yaml'], ['plex', 'kubernetes/main/apps/media/plex/app/helmrelease.yaml'], ['flux', 'kubernetes/main/flux/config/cluster.yaml']]) {
    const x = fact(4477); x.files = [f];
    expectLane(`${n} is the shepherd's`, x, 'EXCLUDED', 'shepherd-ramp');
  }
  expectLane('a leaf off the ramp (#4477) stays LANE-A', fact(4477), 'LANE-A', 'cluster-change');
  const nr = fact(4477); nr.files = ['kubernetes/main/apps/network/traefik-forward-auth/app/helmrelease.yaml'];
  ok(L.classify(nr, rules, { now: NOW, holdRules }).class !== 'shepherd-ramp', 'a sibling directory that only shares a name prefix (traefik-forward-auth) is not on the ramp');
  // what the shipped rules make of the ramp package-rule cases above: the shepherd's, with
  // the package rule still in the record's findings
  const cil = fact(4477); cil.files = ['kubernetes/main/apps/kube-system/cilium/app/helmrelease.yaml'];
  cil.packages = [{ name: 'cilium', update: 'minor', from: '1.18.4', to: '1.19.0' }]; cil.labels = ['type/minor'];
  const cr = expectLane('cilium minor with the shipped rules: the shepherd owns it', cil, 'EXCLUDED', 'shepherd-ramp');
  ok(cr.findings.some(f => f.startsWith('LANE-B-OWNER/rule-owner')), '  ... and the one-way rule is still in its findings', cr.findings.join(' | '));
  const dr = expectLane('Dragonfly 2.1 with the shipped rules: the shepherd owns it', fact(4658), 'EXCLUDED', 'shepherd-ramp');
  ok(dr.findings.some(f => f.startsWith('LANE-B-OWNER/rule-owner')), '  ... and the licence rule is still in its findings', dr.findings.join(' | '));
}

console.log(`\n${passes} passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
