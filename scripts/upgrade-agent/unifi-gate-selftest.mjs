import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const { checks } = JSON.parse(await readFile(new URL('fixtures/unifi-gate.json', import.meta.url), 'utf8'));
const command = fileURLToPath(new URL('unifi-gate', import.meta.url));
const site = 'Example (default)';
const source = 'source-label-must-stay-private';
const ap = 'Workshop AP';
const sw = 'Bench Switch';
const gateway = 'Hallway Gateway';
const spare = 'Spare AP';
const oldVersion = '1.2.3.12345';
const newVersion = '1.2.4.12456';

function sample(metric, labels, value, at) {
  return { metric: { __name__: metric, ...labels }, value: [at, String(value)] };
}

function samplesFor(fixture, at) {
  const samples = [sample('up', { job: 'unpoller' }, fixture.scrape ?? 1, at)];
  const add = (metric, labels, value) => {
    samples.push(sample(`unpoller_${metric}`, { job: 'unpoller', site_name: site, source, ...labels }, value, at));
  };
  const target = fixture.switch ? sw : ap;
  for (const [name, type, model] of [[ap, 'uap', 'DEMO-AP'], [sw, 'usw', 'DEMO-SW'],
                                      [gateway, 'udm', 'DEMO-GW'], [spare, 'uap', 'DEMO-AP']]) {
    if ((name === target && fixture.missing) || (name === gateway && fixture.peerMissing)) continue;
    const labels = { name, type };
    const version = name === target && !fixture.snapshot && !fixture.oldVersion ? newVersion : oldVersion;
    add('device_info', { ...labels, model: name === target && fixture.wrongModel ? 'DEMO-OTHER' : model,
      version, ip: 'private-ip-label', mac: 'private-mac-label', serial: 'private-serial-label' }, 1);
    let uptime = 3600;
    if (name === spare || (name === gateway && fixture.peerOffline)) uptime = 0;
    if (name === target && !fixture.snapshot) uptime = fixture.offline ? 0 : (fixture.uptime ?? [100, 160])[1];
    add('device_uptime_seconds', labels, uptime);
    const flag = fixture.snapshot ? 1 : (fixture.upgradable === undefined ? 0 : fixture.upgradable);
    if (name === target && flag !== null) add('device_upgradable', labels, flag);
  }
  if (fixture.switch) {
    for (const port of fixture.links ?? [1, 2, 3]) {
      add('device_port_port_speed_bps', { name: sw, port_num: String(port) }, 1_000_000_000);
    }
    for (const port of fixture.power ?? [2]) {
      add('device_port_poe_watts', { name: sw, port_num: String(port) }, 2.5);
    }
  } else if (fixture.clients !== null) {
    add('device_stations', { name: ap, type: 'uap', station_type: 'user' }, fixture.clients ?? 10);
    add('device_stations', { name: ap, type: 'uap', station_type: 'guest' }, 0);
  }

  for (const [key, fallback] of [['adopted', 4], ['disconnected', 1], ['pending', 0]]) {
    if (fixture[key] !== null) add(`site_${key}`, { subsystem: 'lan' }, fixture[key] ?? fallback);
  }
  if (!fixture.wanMissing) {
    add('device_wan_status', { name: gateway, wan_networkgroup: 'Primary',
      status: fixture.wanDown ? 'offline' : 'online' }, fixture.wanDown ? 0 : 1);
  }
  if (fixture.latency !== null) {
    add('device_wan_monitor_latency_average_seconds',
      { name: gateway, wan_networkgroup: 'Primary', target: 'private-wan-monitor-label' }, fixture.latency ?? 0.030);
  }
  return samples;
}

let fixture;
let servedQueries = [];
let serverError;
const server = createServer((req, res) => {
  try {
    assert.equal(req.method, 'GET');
    const url = new URL(req.url, 'http://localhost');
    assert.equal(url.pathname, '/api/v1/query');
    assert.equal(url.searchParams.get('lookback_delta'), '120s');
    const query = url.searchParams.get('query');
    servedQueries.push(query);
    const at = Number(url.searchParams.get('time'));
    assert.ok(at > 0);
    if (fixture.httpStatus) {
      res.writeHead(fixture.httpStatus);
      res.end('private error body');
      return;
    }

    let result;
    let resultType = 'vector';
    if (query === 'ALERTS{alertstate="firing"}') {
      const labels = { alertname: fixture.alert, alertstate: 'firing', job: fixture.alertJob };
      result = fixture.alert ? [sample('ALERTS', labels, 1, at)] : [];
    } else if (query.startsWith('{__name__=~"up|') && query.endsWith('",job="unpoller"}')) {
      const metricPattern = new RegExp(`^(?:${query.split('"')[1]})$`);
      result = samplesFor(fixture, at).filter(({ metric }) => metricPattern.test(metric.__name__));
    } else {
      const device = fixture.switch ? sw : ap;
      assert.equal(query, `unpoller_device_uptime_seconds{job="unpoller",name="${device}",site_name="${site}"}[300s]`);
      resultType = 'matrix';
      const ages = fixture.sampleAges ?? [70, 10];
      const values = (fixture.uptime ?? [100, 160]).map((uptime, index) => [at - ages[index], String(uptime)]);
      result = [{ metric: { name: device, site_name: site, source }, values }];
      if (fixture.duplicateHistory) result.push({ ...result[0] });
    }
    const warnings = fixture.warning ? ['private warning text'] : undefined;
    res.end(JSON.stringify({ status: 'success', data: { resultType, result }, warnings }));
  } catch (error) {
    serverError = error;
    res.writeHead(500);
    res.end();
  }
});

async function run(args) {
  servedQueries = [];
  const child = spawn(command, args, {
    env: { PATH: process.env.PATH, PROMETHEUS_URL: `http://localhost:${server.address().port}` },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stdout = '';
  let stderr = '';
  child.stdout.setEncoding('utf8').on('data', (data) => { stdout += data; });
  child.stderr.setEncoding('utf8').on('data', (data) => { stderr += data; });
  const code = await new Promise((resolve, reject) => {
    child.once('close', resolve);
    child.once('error', reject);
  });
  if (serverError) throw serverError;
  assert.doesNotMatch(stdout + stderr, /private-|private |source-label/);
  return { code, stdout, stderr };
}

const directory = await mkdtemp(join(tmpdir(), 'unifi-gate-'));
try {
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, 'localhost', resolve);
  });

  const baselines = new Map();
  for (const name of [ap, sw]) {
    fixture = { snapshot: true, switch: name === sw };
    const result = await run(['snapshot', '--device', name]);
    assert.equal(result.code, 0, result.stderr);
    const snapshot = JSON.parse(result.stdout);
    assert.equal(snapshot.target.version, oldVersion);
    assert.equal(snapshot.target.upgradable, 1);
    assert.deepEqual(snapshot.onlineDevices, [sw, gateway, ap]);
    assert.deepEqual(snapshot.wan, [{ group: 'Primary', online: true, monitors: 1, maxLatencySeconds: 0.030 }]);
    if (name === ap) assert.equal(snapshot.target.clients, 10);
    if (name === sw) {
      assert.deepEqual(snapshot.target.linkPorts, [1, 2, 3]);
      assert.deepEqual(snapshot.target.poePorts, [2]);
    }
    baselines.set(name, snapshot);
  }

  const file = join(directory, 'baseline.json');
  for (const [name, test] of Object.entries(checks)) {
    fixture = test;
    const device = test.switch ? sw : ap;
    const baseline = structuredClone(baselines.get(device));
    baseline.capturedAt = new Date(Date.now() - (test.elapsed ?? 240) * 1000).toISOString();
    if (test.invalidBaseline) delete baseline.target.linkPorts;
    await writeFile(file, JSON.stringify(baseline));
    const args = ['check', '--device', device, '--baseline', file, '--target-not', test.targetNot ?? oldVersion];
    if (test.deadline !== undefined) args.push('--deadline-min', String(test.deadline));
    if (test.dropMax !== undefined) args.push('--client-drop-max', String(test.dropMax));
    const { code, stdout, stderr } = await run(args);
    const output = stdout + stderr;
    assert.equal(code, test.exit, `${name}: ${output}`);
    assert.ok(output.startsWith(['GREEN', 'WAIT', 'STOP'][test.exit] + ':'), name);
    if (test.reason) assert.ok(output.includes(test.reason), `${name}: ${output}`);
    if (test.httpStatus || test.warning) assert.deepEqual(servedQueries, ['ALERTS{alertstate="firing"}']);
    if (test.targetNot || test.invalidBaseline) assert.deepEqual(servedQueries, []);
  }
  console.log(`UniFi health gate selftest: 2 snapshots and ${Object.keys(checks).length} checks passed`);
} finally {
  if (server.listening) await new Promise((resolve) => server.close(resolve));
  await rm(directory, { recursive: true, force: true });
}
