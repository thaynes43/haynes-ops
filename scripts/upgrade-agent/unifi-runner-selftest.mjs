#!/usr/bin/env node
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdtemp, readFile, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { plexIdle, unifiClient } from '../../kubernetes/main/apps/upgrade-agent/app/resources/unifi-job.mjs';
import { executeOne, inWindow, makeReport, parseApprovals, readDevices } from '../../kubernetes/main/apps/upgrade-agent/app/resources/unifi-runner.mjs';

const app = new URL('../../kubernetes/main/apps/upgrade-agent/app/', import.meta.url);
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

test('the mounted manifest parses and the mounted gate matches OP662', async () => {
  const read = (file) => readFile(new URL(file, app), 'utf8');
  assert.ok(Array.isArray(parseApprovals(await read('unifi-approvals.yaml'))));
  assert.equal(await read('resources/unifi-gate.mjs'), await readFile(new URL('unifi-gate.mjs', import.meta.url), 'utf8'));
});

test('the CLI starts through a ConfigMap-style symlink', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'unifi-selftest-'));
  try {
    const script = join(directory, 'unifi-job.mjs');
    await symlink(fileURLToPath(new URL('resources/unifi-job.mjs', app)), script);
    await assert.rejects(exec(process.execPath, [script], { env: {} }), { code: 1 });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

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
  const integration = { name: 'Test room AP', model: 'U7 Pro', state: 'ONLINE',
    firmwareVersion: '1.2.3', supported: true, firmwareUpdatable: true, macAddress: 'fixture-device-id' };
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
