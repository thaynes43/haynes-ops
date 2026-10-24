// Real Renovate 44 manager extraction for the authentik lookup warnings.
// Run: RENOVATE_PACKAGE_DIR=/path/to/renovate RENOVATE_X_IGNORE_RE2=true \
//   node --test --test-concurrency=1 .renovate/authentik-extract.test.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { pathToFileURL } from 'node:url';

const renovateDir = process.env.RENOVATE_PACKAGE_DIR;
if (!renovateDir) {
  throw new Error('RENOVATE_PACKAGE_DIR is required; regex-only checks are not this test');
}

const json5 = (await import(pathToFileURL(`${renovateDir}/node_modules/json5/dist/index.mjs`))).default;
const dockerfile = await import(pathToFileURL(`${renovateDir}/dist/modules/manager/dockerfile/extract.js`));
const customRegex = await import(pathToFileURL(`${renovateDir}/dist/modules/manager/custom/regex/index.js`));
const kubernetes = await import(pathToFileURL(`${renovateDir}/dist/modules/manager/kubernetes/extract.js`));
const helmValues = await import(pathToFileURL(`${renovateDir}/dist/modules/manager/helm-values/extract.js`));
const flux = await import(pathToFileURL(`${renovateDir}/dist/modules/manager/flux/extract.js`));
const { matchRegexOrGlobList } = await import(pathToFileURL(`${renovateDir}/dist/util/string-match.js`));

const root = new URL('../', import.meta.url);
const config = { registryAliases: {} };
const digest = 'sha256:602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5';
const redaction = 'sha256:18fc99f13617a4b83dfc470e4f8cda7ed76cf513c5147c90e1de888838325663';

function readRepo(path) {
  return readFileSync(new URL(path, root), 'utf8');
}

function depsOf(manager, content, file, extractConfig = config) {
  return manager.extractPackageFile(content, file, extractConfig)?.deps ?? [];
}

function named(deps, depName) {
  return deps.filter((dep) => dep.depName === depName);
}

const renovateConfig = json5.parse(readRepo('.github/renovate.json5'));
const customConfig = json5.parse(readRepo('.renovate/customManagers.json5'));
const authentikManager = customConfig.customManagers.find((manager) =>
  (manager.description || []).some((line) => line.includes('Authentik userinfo-redaction')),
);
const dockerPatterns = renovateConfig.dockerfile.managerFilePatterns;

test('built-in dockerfile manager leaves a digest-only FROM without a version', () => {
  const bare = depsOf(dockerfile, `FROM ghcr.io/goauthentik/server@${digest}\n`, 'fixture.Dockerfile');
  const server = named(bare, 'ghcr.io/goauthentik/server');
  assert.equal(server.length, 1);
  assert.equal(server[0].currentValue, undefined);
  assert.equal(server[0].currentDigest, digest);

  const tagged = depsOf(
    dockerfile,
    `FROM ghcr.io/goauthentik/server:2026.8.5@${digest}\n`,
    'fixture.Dockerfile',
  );
  assert.equal(named(tagged, 'ghcr.io/goauthentik/server')[0].currentValue, '2026.8.5');
  assert.equal(named(tagged, 'ghcr.io/goauthentik/server')[0].currentDigest, digest);

  const real = readRepo('scripts/authentik-userinfo-redaction/Dockerfile');
  assert.ok(real.includes(`FROM ghcr.io/goauthentik/server@${digest}`));
  assert.equal(real.includes(`:${digest}`), false);
  const extracted = named(depsOf(dockerfile, real, 'scripts/authentik-userinfo-redaction/Dockerfile'), 'ghcr.io/goauthentik/server');
  assert.equal(extracted[0].currentValue, undefined);
  assert.equal(extracted[0].currentDigest, digest);
});

test('custom manager reads the existing comment version and keeps the digest-only FROM', () => {
  assert.ok(authentikManager);
  const real = readRepo('scripts/authentik-userinfo-redaction/Dockerfile');
  const deps = depsOf(
    customRegex,
    real,
    'scripts/authentik-userinfo-redaction/Dockerfile',
    authentikManager,
  );
  const server = named(deps, 'ghcr.io/goauthentik/server');
  assert.equal(server.length, 1);
  assert.equal(server[0].currentValue, '2026.8.5');
  assert.equal(server[0].currentDigest, digest);
  assert.equal(server[0].datasource, 'docker');
  assert.match(server[0].replaceString, new RegExp(`FROM ghcr\\.io/goauthentik/server@${digest}`));
  assert.equal(server[0].replaceString.includes(`:2026.8.5@${digest}`), false);
  assert.equal(named(deps, 'docker/dockerfile')[0].currentValue, '1');

  const other = 'FROM ghcr.io/goauthentik/server@' + digest + '\n';
  const missed = depsOf(customRegex, other, 'other.Dockerfile', authentikManager);
  assert.equal(named(missed, 'ghcr.io/goauthentik/server').length, 0);

  const unrelated = 'FROM python:3.14-slim\n';
  assert.equal(depsOf(customRegex, unrelated, 'scripts/audio-authoring/Dockerfile', authentikManager).length, 0);
});

test('dockerfile manager scans other Dockerfiles and skips only the redaction file', () => {
  const redactionFile = 'scripts/authentik-userinfo-redaction/Dockerfile';
  const otherFile = 'scripts/audio-authoring/Dockerfile';
  assert.equal(matchRegexOrGlobList(redactionFile, dockerPatterns), false);
  assert.equal(matchRegexOrGlobList(otherFile, dockerPatterns), true);
  assert.equal(matchRegexOrGlobList('scripts/dev-env/Dockerfile', dockerPatterns), true);
  const other = readRepo(otherFile);
  const python = named(depsOf(dockerfile, other, otherFile), 'python');
  assert.equal(python.length, 1);
  assert.equal(python[0].currentValue, '3.14-slim');
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
  assert.ok(fake.currentDigest);
  assert.equal(named(before, 'ghcr.io/home-operations/postgres-init')[0].currentValue, '18.6');
  assert.equal(named(before, 'ghcr.io/thaynes43/authentik-userinfo-redaction')[0].currentValue, '2026.8.5');
  assert.equal(before.some((dep) => (dep.depName || '').includes('notAnImage')), false);

  const after = depsOf(helmValues, broken.replaceAll('.Values', '$.Values'), 'fixture.yaml');
  assert.equal(after.some((dep) => (dep.depName || '').includes('default')), false);
  const variable = after.find((dep) => dep.skipReason === 'contains-variable');
  assert.ok(variable);
  assert.equal(variable.depName, undefined);
  assert.equal(named(after, 'ghcr.io/home-operations/postgres-init')[0].currentValue, '18.6');
  assert.equal(named(after, 'ghcr.io/thaynes43/authentik-userinfo-redaction')[0].currentValue, '2026.8.5');
});

test('repository helmrelease keeps real images and drops the expression package', () => {
  const helmText = readRepo('kubernetes/main/apps/network/authentik/app/helmrelease.yaml');
  assert.ok(helmText.includes(redaction));
  assert.equal(helmText.includes('default .Values.global.image.repository'), false);
  assert.ok(helmText.includes('ghcr.io/home-operations/postgres-init:18.6'));
  assert.ok(helmText.includes('docker.io/library/node:22-bookworm-slim'));

  const values = depsOf(helmValues, helmText, 'kubernetes/main/apps/network/authentik/app/helmrelease.yaml');
  assert.equal(values.some((dep) => (dep.depName || '').includes('{{')), false);
  assert.equal(values.some((dep) => dep.skipReason === 'contains-variable'), true);
  assert.equal(named(values, 'ghcr.io/thaynes43/authentik-userinfo-redaction')[0].currentValue, '2026.8.5');
  assert.equal(named(values, 'ghcr.io/home-operations/postgres-init')[0].currentValue, '18.6');
  assert.equal(named(values, 'docker.io/library/node')[0].currentValue, '22-bookworm-slim');

  const kube = depsOf(kubernetes, helmText, 'helmrelease.yaml');
  assert.equal(kube.some((dep) => (dep.depName || '').includes('{{')), false);
  assert.equal(named(kube, 'ghcr.io/home-operations/postgres-init')[0].currentValue, '18.6');
  assert.equal(named(kube, 'docker.io/library/node')[0].currentValue, '22-bookworm-slim');

  const fluxDeps = depsOf(flux, helmText, 'helmrelease.yaml');
  assert.equal(named(fluxDeps, 'authentik')[0].currentValue, '2026.8.5');
  assert.equal(named(fluxDeps, 'ghcr.io/thaynes43/authentik-userinfo-redaction')[0].currentValue, '2026.8.5');
  assert.equal(fluxDeps.some((dep) => (dep.depName || '').includes('$.Values')), false);
});
