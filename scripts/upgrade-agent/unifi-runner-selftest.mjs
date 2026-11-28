#!/usr/bin/env node
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { copyFile, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { promisify } from 'node:util';
import { plexIdle, unifiClient } from '../../kubernetes/main/apps/upgrade-agent/unifi/app/resources/unifi-job.mjs';
import { executeOne, inWindow, makeReport, parseApprovals, readDevices } from '../../kubernetes/main/apps/upgrade-agent/unifi/app/resources/unifi-runner.mjs';

const app = new URL('../../kubernetes/main/apps/upgrade-agent/unifi/app/', import.meta.url);
const exec = promisify(execFile);
const now = Date.parse('2026-11-27T08:15:00Z');
const iso = (time) => new Date(time).toISOString();
const row = (name, model, overrides = {}) => ({
  name, model, online: true, version: '1.2.3', supported: true, firmwareUpdatable: true,
  hardwareAddress: 'fixture-device-id', ...overrides,
});
const devices = [
  row('Test modem', 'Cable Internet'),
  row('Test gateway', 'UniFi Dream Machine PRO SE'),
  row('Test core pro', 'USW Pro Aggregation'),
  row('Test core', 'USW Aggregation'),
  row('Test radio switch', 'USW Pro Max 48 PoE'),
  row('Test medium switch', 'USW Pro Max 24 PoE'),
  row('Test leaf switch', 'USW Flex 2.5G 5'),
  row('Test outside AP', 'U7 Pro Outdoor'),
  row('Test room AP', 'U7 Pro'),
  row('Test offline AP', 'U7 Pro', { online: false }),
  row('Test unknown model', 'Old AP'),
  row('Test unsupported AP', 'U7 Pro', { supported: false }),
  row('Test power unit', 'USP RPS'),
];
const approval = (device, overrides = {}) => ({
  device, from_version: '1.2.3', approved_by_ref: '135',
  not_before: '2026-11-27T08:00:00Z', not_after: '2026-11-27T09:00:00Z', ...overrides,
});
const ap = approval('Test room AP');
const leaf = approval('Test leaf switch');
const integration = { name: 'Test room AP', model: 'U7 Pro', state: 'ONLINE',
  firmwareVersion: '1.2.3', supported: true, firmwareUpdatable: true, macAddress: 'fixture-device-id' };

test('the mounted manifest parses and the mounted gate matches OP662', async () => {
  const read = (file) => readFile(new URL(file, app), 'utf8');
  assert.deepEqual(parseApprovals(await read('unifi-approvals.yaml')), []);
  assert.equal(await read('resources/unifi-gate.mjs'), await readFile(new URL('unifi-gate.mjs', import.meta.url), 'utf8'));
});

async function withJobFixture(t, stage = '', kubectlStderr = 'private kubectl diagnostic') {
  const directory = await mkdtemp(join(tmpdir(), 'unifi-selftest-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const mounted = join(directory, 'mount');
  const snapshot = join(mounted, '..snapshot');
  await mkdir(snapshot, { recursive: true });
  await symlink('..snapshot', join(mounted, '..data'));
  for (const source of ['resources/unifi-job.mjs', 'resources/unifi-runner.mjs', 'unifi-approvals.yaml']) {
    const name = source.split('/').at(-1);
    await copyFile(new URL(source, app), join(snapshot, name));
    await symlink(join('..data', name), join(mounted, name));
  }
  const script = join(mounted, 'unifi-job.mjs');
  const preload = join(directory, 'fetch-fixture.mjs');
  const configmap = join(directory, 'configmap.json');
  const metadata = { name: 'unifi-inventory', namespace: 'upgrade-agent', resourceVersion: '1',
    labels: { owner: 'fixture' }, annotations: { policy: 'fixture' } };
  const seed = { apiVersion: 'v1', kind: 'ConfigMap', metadata };
  if (stage === 'report') seed.data = { 'inventory.json': '{}' };
  if (stage === 'journal') seed.data = { 'live-attempts.json': 'malformed fixture content' };
  await writeFile(configmap, JSON.stringify(seed));
  await writeFile(join(directory, 'kubectl'), `#!${process.execPath}
const assert = require('node:assert/strict');
const fs = require('node:fs');
const [timeout, command, raw, uri, ...args] = process.argv.slice(2);
assert.equal(timeout, '--request-timeout=8s');
assert.equal(raw, '--raw');
const reportPath = '/api/v1/namespaces/upgrade-agent/configmaps/unifi-inventory';
const { FIXTURE_STAGE, FIXTURE_CONFIGMAP } = process.env;
const previous = JSON.parse(fs.readFileSync(FIXTURE_CONFIGMAP, 'utf8'));
function fail() {
  console.error(process.env.FIXTURE_KUBECTL_STDERR);
  process.exit(7);
}
if (command === 'get') {
  assert.equal(uri, reportPath);
  assert.deepEqual(args, []);
  if (FIXTURE_STAGE === 'configmap-read') fail();
  console.log(JSON.stringify(previous));
} else {
  assert.equal(command, 'replace');
  assert.equal(uri, reportPath + '?fieldManager=kubectl-replace&fieldValidation=Strict');
  assert.deepEqual(args, ['-f', '-']);
  const next = JSON.parse(fs.readFileSync(0, 'utf8'));
  assert.deepEqual(next.metadata, previous.metadata);
  const secondWrite = Object.hasOwn(next.data, 'last-run.json');
  if (secondWrite) assert.equal(next.data['inventory.json'], previous.data['inventory.json']);
  if (FIXTURE_STAGE === (secondWrite ? 'last-run-write' : 'configmap-write')) fail();
  next.metadata.resourceVersion = String(Number(previous.metadata.resourceVersion) + 1);
  fs.writeFileSync(FIXTURE_CONFIGMAP, JSON.stringify(next));
  console.log(JSON.stringify(next));
}
`, { mode: 0o700 });
  await writeFile(preload, `
import assert from 'node:assert/strict';
Date.now = () => ${now};
globalThis.fetch = async (url, { method, body }) => {
  assert.equal(new URL(url).pathname, '/mcp');
  assert.equal(method, 'POST');
  const request = JSON.parse(body);
  const reply = (result) => Response.json({ jsonrpc: '2.0', id: request.id, result });
  if (request.method === 'initialize') {
    if (process.env.FIXTURE_STAGE === 'mcp-connect') {
      throw new TypeError('private network diagnostic', { cause: { code: 'EAI_AGAIN' } });
    }
    return reply({ protocolVersion: '2025-11-25' });
  }
  if (request.method === 'notifications/initialized') return new Response(null, { status: 202 });
  assert.equal(request.method, 'tools/call');
  assert.equal(request.params.name, 'list_integration_devices');
  if (process.env.FIXTURE_STAGE === 'mcp-list') {
    return reply({ isError: true, content: [{ type: 'text', text: 'private MCP diagnostic' }] });
  }
  return reply({ structuredContent: { count: 1, totalCount: 1, offset: 0, data: [${JSON.stringify(integration)}] } });
};
`);
  return {
    run: () => exec(process.execPath, ['--import', preload, script], {
      env: { PATH: directory, MCP_AUTH_TOKEN: 'fixture', UNIFI_LIVE_MODE: 'false',
        FIXTURE_STAGE: stage, FIXTURE_CONFIGMAP: configmap, FIXTURE_KUBECTL_STDERR: kubectlStderr },
    }),
    read: () => readFile(configmap, 'utf8').then(JSON.parse),
  };
}

test('the mounted CLI writes inventory and last run starting from an empty ConfigMap', async (t) => {
  const fixture = await withJobFixture(t);
  const { stdout, stderr } = await fixture.run();
  assert.equal(stderr, '');
  assert.match(stdout, /SKIP: No current approval for an unattended device/);
  const { data, metadata } = await fixture.read();
  assert.equal(metadata.resourceVersion, '3');
  assert.equal(data.observed_at, iso(now));
  assert.equal(JSON.parse(data['inventory.json']).length, 1);
  const lastRun = JSON.parse(data['last-run.json']);
  assert.equal(lastRun.outcome, 'SKIP');
  assert.equal(lastRun.live, false);
  assert.ok(Number.isFinite(Date.parse(lastRun.at)));
});

for (const [stage, detail, expectedVersion] of [
  ['configmap-read', ' (kubectl exit 7)', '1'],
  ['mcp-connect', ' (EAI_AGAIN)', '1'],
  ['mcp-list', '', '1'],
  ['report', '', '1'],
  ['configmap-write', ' (kubectl exit 7)', '1'],
  ['journal', '', '2'],
  ['last-run-write', ' (kubectl exit 7)', '2'],
]) {
  test(`the mounted CLI reports only the safe ${stage} diagnostic on failure`, async (t) => {
    const fixture = await withJobFixture(t, stage);
    await assert.rejects(fixture.run(), { code: 1, stderr: `STOP: ${stage}${detail}\n` });
    const { data, metadata } = await fixture.read();
    assert.equal(metadata.resourceVersion, expectedVersion);
    assert.equal(data?.observed_at, expectedVersion === '2' ? iso(now) : undefined);
    assert.equal(data?.['last-run.json'], undefined);
  });
}

for (const [diagnostic, reason, scenario = reason ?? 'unrecognized errors'] of [
  ['error: You must be logged in to the server (private detail)', 'unauthorized'],
  ['Error from server (Forbidden): private detail', 'forbidden'],
  ['Error from server (NotFound): private detail', 'not found'],
  ['The connection to the server localhost:8080 was refused - private detail', 'localhost fallback'],
  ['The connection to the server fixture-service was refused - private detail', 'connection refused'],
  ['error: unable to load in-cluster configuration, private detail', 'no in-cluster config'],
  ['Unable to connect to the server: tls: failed to verify certificate: x509: private detail', 'TLS certificate error'],
  ['error: Get "fixture-service": context deadline exceeded (Client.Timeout exceeded while awaiting headers)', 'timeout', 'client timeout'],
  ['Error from server (ServerTimeout): private detail', 'timeout', 'server timeout'],
  ['W1128 private client warning\nError from server (Forbidden): private detail', 'forbidden', 'an error after a client warning'],
  ['unrecognized error\nprivate second line', null],
]) {
  test(`the mounted CLI sanitizes kubectl stderr for ${scenario}`, async (t) => {
    const fixture = await withJobFixture(t, 'configmap-read', `${diagnostic}\nprivate response body`);
    const suffix = reason ? `, ${reason}` : '';
    await assert.rejects(fixture.run(), {
      code: 1, stdout: '', stderr: `STOP: configmap-read (kubectl exit 7${suffix})\n`,
    });
  });
}

test('ordered queue skips offline and EOL-unknown devices and requires special approvals', () => {
  const regular = ['Test room AP', 'Test outside AP', 'Test leaf switch', 'Test medium switch'];
  const special = ['Test radio switch', 'Test core', 'Test core pro', 'Test gateway', 'Test modem'];
  const before = makeReport(devices, [], [], now);
  assert.deepEqual(before.queue.map(({ name }) => name), regular);
  assert.equal(before.inventory.find(({ name }) => name === 'Test offline AP').status, 'Offline');
  assert.equal(before.inventory.find(({ name }) => name === 'Test unknown model').status, 'EOL status unknown');
  assert.equal(before.inventory.find(({ name }) => name === 'Test unsupported AP').status, 'EOL status unknown');
  assert.equal(before.inventory.find(({ name }) => name === 'Test power unit').status, 'Inventory only');
  const after = makeReport(devices, special.map((name) => approval(name)), [], now);
  assert.deepEqual(after.queue.map(({ name }) => name), [...regular, ...special]);
  assert.equal(after.queue[4].note, 'radios: HA warned');
  assert.match(after.queue.at(-1).note, /attended.*Tom/);
  assert.doesNotMatch(JSON.stringify({ inventory: after.inventory, queue: after.queue, table: after.table }), /fixture-device-id/);
});

test('first seen persists for the same offer and resets after it disappears or the version changes', () => {
  const first = makeReport(devices, [], [], now);
  const next = devices.map((device) => {
    if (device.name === 'Test room AP') return { ...device, firmwareUpdatable: false };
    if (device.name === 'Test outside AP') return { ...device, version: '1.2.4' };
    return device;
  });
  const second = makeReport(next, [], first.inventory, now + 60000);
  const seen = (name) => second.inventory.find((device) => device.name === name).firstSeen;
  assert.equal(seen('Test room AP'), null);
  assert.equal(seen('Test outside AP'), iso(now + 60000));
  assert.equal(seen('Test leaf switch'), iso(now));
  assert.match(second.table, /UniFi inventory.*2026-11-27/);
});

test('strict approval parsing, exclusive expiry, exact version, and unique target names', () => {
  assert.deepEqual(parseApprovals(JSON.stringify([ap])), [ap]);
  assert.throws(() => parseApprovals('{}'), /must be a list/);
  assert.throws(() => parseApprovals(JSON.stringify([{ ...ap, target_version: '1.2.4' }])), /Invalid approval/);
  assert.throws(() => parseApprovals(JSON.stringify([approval('Test AP', { not_after: '2026-11-31T09:00:00Z' })])), /Invalid approval/);
  const approvals = [
    { ...ap, not_after: iso(now) },
    { ...ap, not_before: iso(now + 1000) },
    { ...leaf, from_version: '1.2.2' },
    approval('Test modem', { not_after: iso(now) }),
  ];
  const report = makeReport(devices, approvals, [], now);
  assert.equal(report.queue.some(({ approvedBy }) => approvedBy !== null), false);
  assert.equal(report.queue.some(({ name }) => name === 'Test modem'), false);
  const duplicated = makeReport([...devices, row('Test room AP', 'U7 Pro')], [ap], [], now);
  assert.equal(duplicated.queue.some(({ name }) => name === 'Test room AP'), false);
  const overlap = makeReport(devices, [ap, { ...ap, approved_by_ref: '136' }], [], now);
  assert.equal(overlap.queue.some(({ name }) => name === 'Test room AP'), false);
  assert.equal(inWindow(now), true);
  assert.equal(inWindow(Date.parse('2026-07-01T07:15:00Z')), true);
  assert.equal(inWindow(Date.parse('2026-07-01T10:00:00Z')), false);
});

function runFixture({
  approvals = [leaf, ap], live = true, results = ['WAIT', 'GREEN'], journal = [],
  streams = [true, true], alerts = [], time = now, dryRunDelay = 0, afterReservation = 0, jobUID = 'fixture-job',
} = {}) {
  const calls = [];
  const saved = [];
  const { candidates } = makeReport(devices, approvals, [], time);
  const io = {
    now: () => time,
    async plexIdle() { calls.push('plex'); return streams.shift(); },
    async listDevices() { calls.push('inventory'); return devices; },
    async snapshot(name) {
      calls.push(`snapshot ${name}`);
      return { capturedAt: iso(time), firingAlerts: alerts,
        target: { name, version: '1.2.3.456', type: name.endsWith('AP') ? 'uap' : 'usw', upgradable: 1 } };
    },
    async dryRun({ name }) { calls.push(`dry run ${name}`); time += dryRunDelay; return true; },
    async confirm({ name }, baselineVersion) {
      calls.push(`confirm ${name}`);
      assert.equal(baselineVersion, '1.2.3.456');
      return true;
    },
    async check(name, baseline, minutes) {
      calls.push(`check ${name} ${minutes}`);
      assert.equal(baseline.target.version, '1.2.3.456');
      return { status: results.shift(), reason: 'fixture health gate' };
    },
    async wait(ms) { time += ms; },
    async saveAttempt(value) {
      saved.push(value);
      if (value?.outcome === 'PENDING') time += afterReservation;
    },
  };
  return { calls, saved, run: () => executeOne({ candidates, live, jobUID, journal, io }) };
}

test('mode off runs all pre-checks and dry run, then logs would upgrade without confirm', async () => {
  const fixture = runFixture({ live: false });
  assert.equal((await fixture.run()).reason, 'would upgrade Test room AP');
  assert.deepEqual(fixture.calls, ['plex', 'inventory', 'snapshot Test room AP', 'dry run Test room AP']);
  assert.deepEqual(fixture.saved, []);
});

test('only the first approved target runs even after GREEN', async () => {
  const fixture = runFixture();
  assert.equal((await fixture.run()).outcome, 'GREEN');
  assert.deepEqual(fixture.calls, ['plex', 'inventory', 'snapshot Test room AP', 'dry run Test room AP',
    'inventory', 'plex', 'confirm Test room AP', 'check Test room AP 15', 'check Test room AP 15']);
  assert.deepEqual(fixture.saved.map(({ outcome }) => outcome), ['PENDING', 'GREEN']);
  const duplicatePod = runFixture({ journal: fixture.saved.slice(-1) });
  assert.equal((await duplicatePod.run()).reason, 'This Job already ran a live attempt');
  assert.deepEqual(duplicatePod.calls, []);
  const switchRun = runFixture({ approvals: [leaf] });
  assert.equal((await switchRun.run()).outcome, 'GREEN');
  assert.equal(switchRun.calls.includes('check Test leaf switch 30'), true);
});

test('first STOP ends the run and a pending or stopped attempt blocks later live runs', async () => {
  const fixture = runFixture({ results: ['STOP', 'GREEN'] });
  assert.equal((await fixture.run()).outcome, 'STOP');
  assert.deepEqual(fixture.calls.filter((call) => /^(confirm|check)/.test(call)),
    ['confirm Test room AP', 'check Test room AP 15']);
  assert.deepEqual(fixture.saved.map(({ outcome }) => outcome), ['PENDING', 'STOP']);
  for (const attempt of fixture.saved) {
    const next = runFixture({ journal: [attempt] });
    assert.equal((await next.run()).outcome, 'STOP');
    assert.deepEqual(next.calls, []);
  }
  const subsequent = runFixture({ journal: [{ ...ap, outcome: 'GREEN' }] });
  assert.equal((await subsequent.run()).device, 'Test leaf switch');
});

test('attended approvals stay visible but never start in the CronJob', async () => {
  const fixture = runFixture({ approvals: [approval('Test modem'), approval('Test gateway'), approval('Test core')] });
  assert.equal((await fixture.run()).outcome, 'SKIP');
  assert.deepEqual(fixture.calls, []);
});

test('baseline alert, unreadable Plex, expired windows and expired reservation prevent confirmation', async () => {
  const fixtures = [
    [runFixture({ alerts: ['NodeNotReady'] }), 'STOP'],
    [runFixture({ streams: [false] }), 'SKIP'],
    [runFixture({ streams: [true, false] }), 'SKIP'],
    [runFixture({ approvals: [{ ...ap, not_after: iso(now) }] }), 'SKIP'],
    [runFixture({ approvals: [{ ...ap, not_after: iso(now + 1000) }], dryRunDelay: 1000 }), 'SKIP'],
    [runFixture({ approvals: [{ ...ap, not_after: iso(now + 1000) }], afterReservation: 1000 }), 'SKIP'],
  ];
  for (const [fixture, expected] of fixtures) {
    assert.equal((await fixture.run()).outcome, expected);
    assert.equal(fixture.calls.some((call) => call.startsWith('confirm')), false);
  }
  assert.equal(fixtures.at(-1)[0].saved.at(-1), null);
});

test('deadline cannot return GREEN after time is up', async () => {
  const fixture = runFixture({ results: Array(35).fill('WAIT') });
  assert.match((await fixture.run()).reason, /deadline reached/);
  assert.equal(fixture.calls.filter((call) => call.startsWith('check')).length, 30);
});

test('Plex reads all three session counts and treats errors and invalid counts as busy', async () => {
  const env = { PLEX_TOKEN: 'fixture', PLEXOPS_TOKEN: 'fixture', HAYNESTOWER_TOKEN: 'fixture' };
  let calls = 0;
  const log = () => {};
  const fakeFetch = async (url, { headers, redirect }) => {
    calls += 1;
    assert.equal(new URL(url).pathname, '/status/sessions');
    assert.equal(headers['X-Plex-Token'], 'fixture');
    assert.equal(redirect, 'error');
    return Response.json({ MediaContainer: { size: 0 } });
  };
  assert.equal(await plexIdle(env, fakeFetch, log), true);
  assert.equal(calls, 3);
  for (const response of [new Response('', { status: 401 }), Response.json({ MediaContainer: { size: 1 } }),
    Response.json({ MediaContainer: { size: '' } }), new Response('<invalid/>')]) {
    assert.equal(await plexIdle(env, async (url, options) =>
      url.includes('haynestower') ? response : fakeFetch(url, options), log), false);
  }
});

test('MCP transport negotiates session, handles SSE, and requests integration inventory only', async () => {
  const methods = [];
  const client = unifiClient('fixture', async (url, { method, body, headers }) => {
    assert.equal(new URL(url).pathname, '/mcp');
    if (method === 'DELETE') {
      methods.push('DELETE');
      return new Response(null, { status: 200 });
    }
    const request = JSON.parse(body);
    methods.push(request.method);
    const reply = (result) => ({ jsonrpc: '2.0', id: request.id, result });
    if (request.method === 'initialize') {
      return Response.json(reply({ protocolVersion: '2025-11-25' }), { headers: { 'Mcp-Session-Id': 'fixture-session' } });
    }
    assert.equal(headers['Mcp-Session-Id'], 'fixture-session');
    assert.equal(headers['MCP-Protocol-Version'], '2025-11-25');
    if (request.method === 'notifications/initialized') return new Response(null, { status: 202 });
    assert.deepEqual(request.params, { name: 'list_integration_devices', arguments: { site_id: 'default', limit: 1000, offset: 0 } });
    const data = { count: 1, totalCount: 1, offset: 0, data: [integration] };
    const result = reply({ content: [{ type: 'text', text: JSON.stringify(data) }] });
    return new Response(`event: message\r\ndata: ${JSON.stringify(result)}\r\n\r\n`, {
      headers: { 'Content-Type': 'text/event-stream' },
    });
  });
  await client.connect();
  assert.deepEqual(await client.listDevices(), [row('Test room AP', 'U7 Pro')]);
  await client.close();
  assert.deepEqual(methods, ['initialize', 'notifications/initialized', 'tools/call', 'DELETE']);
  assert.throws(() => readDevices({ offset: 0, count: 1, totalCount: 2, data: [integration] }), /incomplete/);
});

test('fixture transport sends distinct dry-run and confirm requests for the same device', async () => {
  const device = devices.find(({ name }) => name === ap.device);
  const toolCalls = [];
  const client = unifiClient('fixture', async (_url, { body }) => {
    const request = JSON.parse(body);
    const reply = (result) => Response.json({ jsonrpc: '2.0', id: request.id, result });
    if (request.method === 'initialize') return reply({ protocolVersion: '2025-11-25' });
    if (request.method === 'notifications/initialized') return new Response(null, { status: 202 });
    assert.equal(request.params.name, 'upgrade_device');
    const args = request.params.arguments;
    toolCalls.push(args);
    return reply({ structuredContent: args.dry_run ? { dry_run: true, would_upgrade: device.hardwareAddress }
      : { success: true, device_mac: device.hardwareAddress, current_version: '1.2.3.456' } });
  });
  await client.connect();
  assert.equal(await client.dryRun(device), true);
  assert.equal(await client.confirm(device, '1.2.3.456'), true);
  assert.deepEqual(toolCalls, [
    { site_id: 'default', device_mac: device.hardwareAddress, dry_run: true },
    { site_id: 'default', device_mac: device.hardwareAddress, confirm: true },
  ]);
});
