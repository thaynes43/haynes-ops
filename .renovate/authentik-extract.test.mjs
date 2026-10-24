// Real Renovate 44 extraction-worker checks for the authentik lookup fix.
// getMatchingFiles unions each pattern. A "!" pattern does not subtract.
// Run: RENOVATE_PACKAGE_DIR=/path/to/renovate RENOVATE_X_IGNORE_RE2=true \
//   node --test --test-concurrency=1 .renovate/authentik-extract.test.mjs
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { test } from 'node:test';
import { pathToFileURL } from 'node:url';

const renovateDir = process.env.RENOVATE_PACKAGE_DIR;
if (!renovateDir) {
  throw new Error('RENOVATE_PACKAGE_DIR is required; list-helper checks are not this test');
}

const json5 = (await import(pathToFileURL(`${renovateDir}/node_modules/json5/dist/index.mjs`))).default;
const { getConfig } = await import(pathToFileURL(`${renovateDir}/dist/config/defaults.js`));
const { getManagerConfig, mergeChildConfig } = await import(pathToFileURL(`${renovateDir}/dist/config/index.js`));
const { getMatchingFiles } = await import(pathToFileURL(`${renovateDir}/dist/workers/repository/extract/file-match.js`));
const { applyPackageRules } = await import(pathToFileURL(`${renovateDir}/dist/util/package-rules/index.js`));
const { normalizeDepNames } = await import(pathToFileURL(`${renovateDir}/dist/workers/repository/extract/manager-files.js`));
const { GlobalConfig } = await import(pathToFileURL(`${renovateDir}/dist/config/global.js`));
const { doAutoReplace } = await import(pathToFileURL(`${renovateDir}/dist/workers/repository/update/branch/auto-replace.js`));
const dockerfile = await import(pathToFileURL(`${renovateDir}/dist/modules/manager/dockerfile/extract.js`));
const customRegex = await import(pathToFileURL(`${renovateDir}/dist/modules/manager/custom/regex/index.js`));
const kubernetes = await import(pathToFileURL(`${renovateDir}/dist/modules/manager/kubernetes/extract.js`));
const helmValues = await import(pathToFileURL(`${renovateDir}/dist/modules/manager/helm-values/extract.js`));
const flux = await import(pathToFileURL(`${renovateDir}/dist/modules/manager/flux/extract.js`));

const root = new URL('../', import.meta.url);
const digest = 'sha256:602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5';
const redaction = 'sha256:18fc99f13617a4b83dfc470e4f8cda7ed76cf513c5147c90e1de888838325663';
const authFile = 'scripts/authentik-userinfo-redaction/Dockerfile';
const badNegative = '!/(^|/)scripts/authentik-userinfo-redaction/Dockerfile$/';

function readRepo(path) {
  return readFileSync(new URL(path, root), 'utf8');
}

function depsOf(manager, content, file, extractConfig = {}) {
  return manager.extractPackageFile(content, file, extractConfig)?.deps ?? [];
}

function named(deps, depName) {
  return deps.filter((dep) => dep.depName === depName);
}

const repo = json5.parse(readRepo('.github/renovate.json5'));
const holds = json5.parse(readRepo('.renovate/holds.json5'));
const customFile = json5.parse(readRepo('.renovate/customManagers.json5'));
const autoMerge = json5.parse(readRepo('.renovate/autoMerge.json5'));
const authentikManager = customFile.customManagers.find((manager) =>
  (manager.description || []).some((line) => line.includes('Authentik userinfo redaction base image')),
);
const defaults = getConfig();
const resolved = mergeChildConfig(defaults, repo);
const tracked = execFileSync('git', ['ls-files'], { cwd: new URL(root).pathname, encoding: 'utf8' })
  .split('\n')
  .filter(Boolean);

function dockerfileFiles(config) {
  return getMatchingFiles(getManagerConfig(config, 'dockerfile'), tracked);
}

test('builtin dockerfile discovery matches the pre-override baseline', () => {
  assert.equal(repo.dockerfile, undefined);
  assert.deepEqual(repo.ignorePaths, [
    '**/*.sops.*',
    '**/bootstrap/**',
    '.archive/**',
    'archive/**',
    'scripts/book-native-scan-fixture/Dockerfile',
  ]);
  const baseline = dockerfileFiles(mergeChildConfig(defaults, { ignorePaths: repo.ignorePaths }));
  const current = dockerfileFiles(resolved);
  assert.deepEqual(current, baseline);
  assert.ok(current.length > 0 && current.length < 40, `dockerfile scan expanded to ${current.length}`);
  assert.ok(current.includes(authFile));
  assert.equal(current.some((file) => file.endsWith('.md')), false);
  assert.equal(current.some((file) => file.endsWith('.yaml') || file.endsWith('.yml')), false);
  assert.equal(current.some((file) => file.endsWith('.py')), false);
  for (const file of [
    tracked.find((file) => file.endsWith('.md')),
    tracked.find((file) => file.endsWith('.yaml')),
    tracked.find((file) => file.endsWith('.py')),
    tracked.find((file) => file.startsWith('archive/')),
    tracked.find((file) => file.includes('/bootstrap/')),
    'scripts/book-native-scan-fixture/Dockerfile',
  ]) {
    assert.equal(typeof file, 'string');
    assert.equal(current.includes(file), false);
  }

  const expanded = getManagerConfig(resolved, 'dockerfile');
  expanded.managerFilePatterns = [...expanded.managerFilePatterns, badNegative];
  const union = getMatchingFiles(expanded, tracked);
  assert.ok(union.length > current.length + 100);
  assert.ok(union.includes(authFile));
  assert.ok(union.some((file) => file.endsWith('.md')));
});

test('custom manager tracks only the server pin and the builtin duplicate is disabled', async () => {
  assert.ok(authentikManager);
  assert.equal(authentikManager.matchStrings.some((line) => line.includes('docker/dockerfile')), false);
  const regexConfig = mergeChildConfig(getManagerConfig(resolved, 'regex'), authentikManager);
  const customFiles = getMatchingFiles(regexConfig, tracked);
  assert.deepEqual(customFiles, [authFile]);

  const dockerText = readRepo(authFile);
  assert.ok(dockerText.includes(`FROM ghcr.io/goauthentik/server@${digest}`));
  assert.equal(dockerText.includes(`:2026.8.5@${digest}`), false);
  const builtin = depsOf(dockerfile, dockerText, authFile);
  const custom = depsOf(customRegex, dockerText, authFile, authentikManager).map((dep) => {
    normalizeDepNames(dep);
    return dep;
  });
  const builtinServer = named(builtin, 'ghcr.io/goauthentik/server');
  const customServer = named(custom, 'ghcr.io/goauthentik/server');
  const frontend = named(builtin, 'docker/dockerfile');
  assert.equal(builtinServer.length, 1);
  assert.equal(builtinServer[0].currentValue, undefined);
  assert.equal(builtinServer[0].currentDigest, digest);
  assert.equal(customServer.length, 1);
  assert.equal(customServer[0].currentValue, '2026.8.5');
  assert.equal(customServer[0].currentDigest, digest);
  assert.equal(customServer[0].datasource, 'docker');
  assert.equal(named(custom, 'docker/dockerfile').length, 0);
  assert.equal(frontend.length, 1);
  assert.equal(frontend[0].currentValue, '1');

  const packageRules = [
    ...(autoMerge.packageRules || []),
    ...(repo.packageRules || []),
    ...(holds.packageRules || []),
  ];
  async function ruled(dep, manager) {
    return applyPackageRules({
      ...dep,
      manager,
      packageFile: authFile,
      datasource: dep.datasource,
      packageRules,
      ignoreDeps: [],
    });
  }
  const disabled = await ruled({ ...builtinServer[0], packageName: builtinServer[0].packageName }, 'dockerfile');
  const trackedServer = await ruled(customServer[0], 'regex');
  const trackedFrontend = await ruled({ ...frontend[0], packageName: frontend[0].packageName }, 'dockerfile');
  assert.equal(disabled.enabled, false);
  assert.equal(disabled.skipReason, 'package-rules');
  assert.notEqual(trackedServer.enabled, false);
  assert.notEqual(trackedServer.skipReason, 'package-rules');
  assert.equal(trackedServer.dependencyDashboardApproval, true);
  assert.equal(trackedServer.currentValue, '2026.8.5');
  assert.equal(trackedServer.currentDigest, digest);
  assert.notEqual(trackedFrontend.enabled, false);
  assert.notEqual(trackedFrontend.skipReason, 'package-rules');

  const otherFile = 'scripts/audio-authoring/Dockerfile';
  const python = named(depsOf(dockerfile, readRepo(otherFile), otherFile), 'python')[0];
  const kept = await applyPackageRules({
    ...python,
    manager: 'dockerfile',
    packageFile: otherFile,
    packageRules,
    ignoreDeps: [],
  });
  assert.equal(kept.currentValue, '3.14-slim');
  assert.notEqual(kept.enabled, false);
});

test('custom digest and version replacement stays digest-only', async () => {
  const dockerText = readRepo(authFile);
  const server = depsOf(customRegex, dockerText, authFile, authentikManager)[0];
  normalizeDepNames(server);
  const nextDigest = `sha256:${'ab'.repeat(32)}`;
  const dir = mkdtempSync(`${tmpdir()}/auth-rr-`);
  GlobalConfig.set({ localDir: dir });
  const updated = await doAutoReplace({
    ...authentikManager,
    manager: 'regex',
    packageFile: 'Dockerfile',
    depName: server.depName,
    packageName: server.packageName,
    currentValue: server.currentValue,
    newValue: '2026.8.6',
    currentDigest: server.currentDigest,
    newDigest: nextDigest,
    replaceString: server.replaceString,
    datasource: 'docker',
    depIndex: 0,
  }, dockerText, false, true);
  assert.match(updated, /Authentik server 2026\.8\.6, pinned by the published multi-arch index digest/);
  assert.ok(updated.includes(`FROM ghcr.io/goauthentik/server@${nextDigest}`));
  assert.equal(updated.includes(`:2026.8.6@${nextDigest}`), false);
  assert.equal(updated.includes(digest), false);
  const again = named(depsOf(customRegex, updated, authFile, authentikManager), 'ghcr.io/goauthentik/server');
  assert.equal(again[0].currentValue, '2026.8.6');
  assert.equal(again[0].currentDigest, nextDigest);
});

test('helm image expression is a docker package until it contains $', () => {
  const expression = '{{ default .Values.global.image.repository .Values.server.image.repository }}:{{ default (include "authentik.defaultTag" .) .Values.server.image.tag }}@{{ default .Values.global.image.digest .Values.server.image.digest }}';
  const broken = `
image:
  repository: ghcr.io/thaynes43/authentik-userinfo-redaction
  tag: "2026.8.5"
initContainers:
  - name: web-dist-copy
    image: '${expression}'
  - name: init-db
    image: ghcr.io/home-operations/postgres-init:18.6
note: '{{ .Values.notAnImage }}'
`;
  const before = depsOf(helmValues, broken, 'fixture.yaml');
  const fake = before.find((dep) => (dep.depName || '').includes('default .Values.global.image.repository'));
  assert.ok(fake);
  assert.equal(fake.datasource, 'docker');
  assert.equal(fake.skipReason, undefined);
  assert.equal(named(before, 'ghcr.io/home-operations/postgres-init')[0].currentValue, '18.6');
  assert.equal(before.some((dep) => (dep.depName || '').includes('notAnImage')), false);
  const after = depsOf(helmValues, broken.replaceAll('.Values', '$.Values'), 'fixture.yaml');
  assert.equal(after.some((dep) => (dep.depName || '').includes('default')), false);
  assert.equal(after.find((dep) => dep.skipReason === 'contains-variable').depName, undefined);
  assert.equal(named(after, 'ghcr.io/home-operations/postgres-init')[0].currentValue, '18.6');
  assert.equal(named(after, 'ghcr.io/thaynes43/authentik-userinfo-redaction')[0].currentValue, '2026.8.5');
});

test('repository helmrelease keeps real images and drops the expression package', () => {
  const helmText = readRepo('kubernetes/main/apps/network/authentik/app/helmrelease.yaml');
  assert.ok(helmText.includes(redaction));
  assert.equal(helmText.includes('default .Values.global.image.repository'), false);
  const values = depsOf(helmValues, helmText, 'kubernetes/main/apps/network/authentik/app/helmrelease.yaml');
  assert.equal(values.some((dep) => (dep.depName || '').includes('{{')), false);
  assert.equal(values.some((dep) => dep.skipReason === 'contains-variable'), true);
  assert.equal(named(values, 'ghcr.io/thaynes43/authentik-userinfo-redaction')[0].currentValue, '2026.8.5');
  assert.equal(named(values, 'ghcr.io/home-operations/postgres-init')[0].currentValue, '18.6');
  assert.equal(named(values, 'docker.io/library/node')[0].currentValue, '22-bookworm-slim');
  const kube = depsOf(kubernetes, helmText, 'helmrelease.yaml');
  assert.equal(kube.some((dep) => (dep.depName || '').includes('{{')), false);
  const fluxDeps = depsOf(flux, helmText, 'helmrelease.yaml');
  assert.equal(named(fluxDeps, 'authentik')[0].currentValue, '2026.8.5');
  assert.equal(fluxDeps.some((dep) => (dep.depName || '').includes('$.Values')), false);
});
