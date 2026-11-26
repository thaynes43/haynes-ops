#!/usr/bin/env node
/** Read-only UniFi health gate. On STOP, halt and report. Never downgrade or retry automatically. */
import { readFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';

const PROMETHEUS = 'http://kube-prometheus-stack-prometheus.observability.svc.cluster.local:9090';
const MAX_SAMPLE_AGE = 120;
const SETTLE_SECONDS = 120;
const WAN_LATENCY_MARGIN = 0.100; // 48h max-latency spikes reach 125 ms on this line (2026-11-26)
const UPTIME_HISTORY = 300;
const UPTIME_JITTER = 65; // The exporter cache and Prometheus each refresh about once a minute.

const metric = {
  info: 'unpoller_device_info',
  uptime: 'unpoller_device_uptime_seconds',
  upgradable: 'unpoller_device_upgradable',
  stations: 'unpoller_device_stations',
  link: 'unpoller_device_port_port_speed_bps',
  poe: 'unpoller_device_port_poe_watts',
  wan: 'unpoller_device_wan_status',
  latency: 'unpoller_device_wan_monitor_latency_average_seconds',
  adopted: 'unpoller_site_adopted',
  disconnected: 'unpoller_site_disconnected',
  pending: 'unpoller_site_pending',
};
const currentQuery = `{__name__=~"up|${Object.values(metric).join('|')}",job="unpoller"}`;
const alertsQuery = 'ALERTS{alertstate="firing"}';
const denyAlert = /^(?:Gatus.*|KubeNodeNotReady|NodeNotReady|NodeRebooted|PVE.*|Talos.*)$/i;
const siteMetrics = ['adopted', 'disconnected', 'pending'];
const usage = `Usage:
  unifi-gate snapshot --device <name>
  unifi-gate check --device <name> --baseline <file> --target-not <old-version>
                   [--deadline-min 10] [--client-drop-max 0.3]

On STOP, halt and report the reasons. Never downgrade or retry the update automatically.`;

function requireEvidence(condition, message) {
  if (!condition) throw new Error(message);
}

function label(value) {
  return typeof value === 'string' && value.trim().length > 0 && !/[\r\n\x00-\x1f]/u.test(value);
}

function nonnegative(value) {
  return Number.isFinite(value) && value >= 0;
}

function sampleValue(pair) {
  if (!Array.isArray(pair) || typeof pair[1] !== 'string' || !pair[1].trim()) return NaN;
  return Number(pair[1]);
}

async function query(expression, at, resultType, description) {
  let response;
  let payload;
  try {
    const base = process.env.PROMETHEUS_URL || PROMETHEUS;
    const url = new URL(`${base.replace(/\/+$/, '')}/api/v1/query`);
    url.searchParams.set('query', expression);
    url.searchParams.set('time', String(at));
    url.searchParams.set('lookback_delta', `${MAX_SAMPLE_AGE}s`);
    response = await fetch(url, { signal: AbortSignal.timeout(8000), redirect: 'error' });
    if (response.ok) payload = await response.json();
  } catch {
    throw new Error(`Prometheus ${description} request failed`);
  }
  requireEvidence(response.ok, `Prometheus ${description} returned HTTP ${response.status}`);
  requireEvidence(payload?.status === 'success' && payload.data?.resultType === resultType &&
    Array.isArray(payload.data.result) && !payload.warnings?.length,
  `Prometheus ${description} returned incomplete or invalid data`);
  return payload.data.result;
}

function readSamples(rows) {
  const byMetric = new Map();
  for (const row of rows) {
    const labels = row?.metric;
    const value = sampleValue(row?.value);
    requireEvidence(labels && label(labels.__name__) && nonnegative(value),
      'Invalid unpoller sample');
    if (!byMetric.has(labels.__name__)) byMetric.set(labels.__name__, []);
    byMetric.get(labels.__name__).push({ labels, value });
  }
  const scrape = byMetric.get('up') ?? [];
  requireEvidence(scrape.length === 1 && scrape[0].value === 1,
    'Unpoller scrape is down, missing, or ambiguous');
  return byMetric;
}

function byLabel(samples, key, description) {
  const indexed = new Map();
  for (const sample of samples) {
    const id = sample.labels[key];
    requireEvidence(label(id) && !indexed.has(id), `Invalid or duplicate ${description}`);
    indexed.set(id, sample);
  }
  return indexed;
}

function targetSamples(read, target, metricName) {
  return read(metricName).filter(({ labels }) =>
    labels.name === target.name && (labels.type === undefined || labels.type === target.type));
}

function readTarget(read, info, uptimeSeconds) {
  const { name, version, model, type } = info.labels;
  requireEvidence(info.value === 1 && [name, version, model, type].every(label),
    'Invalid device identity or firmware labels');
  const target = { name, type, model, version, uptimeSeconds };
  const upgrades = targetSamples(read, target, metric.upgradable);
  requireEvidence(upgrades.length <= 1 && upgrades.every(({ value }) => value === 0 || value === 1),
    'Invalid or duplicate upgrade flag');
  target.upgradable = upgrades[0]?.value ?? null;
  if (type === 'uap') {
    const counts = byLabel(targetSamples(read, target, metric.stations), 'station_type', 'AP station type');
    const users = counts.get('user')?.value;
    const guests = counts.get('guest')?.value;
    target.clients = Number.isSafeInteger(users) && Number.isSafeInteger(guests) ? users + guests : null;
  } else if (type === 'usw') {
    const positivePorts = (metricName) => {
      const ports = byLabel(targetSamples(read, target, metricName), 'port_num', 'switch port number');
      requireEvidence([...ports.keys()].every((port) => /^\d+$/.test(port)), 'Invalid switch port number');
      return [...ports].filter(([, sample]) => sample.value > 0).map(([port]) => Number(port)).sort((a, b) => a - b);
    };
    target.linkPorts = positivePorts(metric.link);
    target.poePorts = positivePorts(metric.poe);
  }
  return target;
}

function readWan(read) {
  const wans = byLabel(read(metric.wan), 'wan_networkgroup', 'WAN group');
  return Array.from(wans, ([group, { labels, value }]) => {
    const monitors = read(metric.latency).filter((sample) => sample.labels.wan_networkgroup === group &&
      sample.labels.name === labels.name && sample.value > 0);
    const latencies = monitors.map((sample) => sample.value);
    return {
      group,
      online: labels.status === 'online' && value === 1,
      monitors: latencies.length,
      maxLatencySeconds: latencies.length ? Math.max(...latencies) : null,
    };
  }).sort((a, b) => a.group.localeCompare(b.group));
}

function readFiringAlerts(alerts) {
  const firing = new Set();
  for (const row of alerts) {
    const name = row?.metric?.alertname;
    requireEvidence(label(name) && row.metric.alertstate === 'firing' && sampleValue(row.value) === 1,
      'Invalid Prometheus firing alert sample');
    if (denyAlert.test(name) || (name === 'TargetDown' &&
      [row.metric.job, row.metric.service].includes('gatus'))) firing.add(name);
  }
  return [...firing].sort();
}

function readSite(metrics, device, baseline) {
  const find = (name) => metrics.get(name) ?? [];
  const matches = find(metric.info).filter(({ labels }) => labels.name === device);
  const site = baseline?.site ?? matches[0]?.labels.site_name;
  requireEvidence(matches.length <= 1 && label(site), 'Device name or site is missing or ambiguous');
  requireEvidence(!matches.length || matches[0].labels.site_name === site, 'Device moved to a different site');
  const read = (name) => find(name).filter(({ labels }) => labels.site_name === site);
  const sources = new Set(Object.values(metric).flatMap((name) => read(name).map(({ labels }) => labels.source)));
  requireEvidence(sources.size === 1 && [...sources].every(label), 'Site telemetry is missing or ambiguous');

  const inventory = byLabel(read(metric.info), 'name', 'device name');
  const uptime = byLabel(read(metric.uptime), 'name', 'device uptime');
  if (!baseline) {
    requireEvidence(inventory.size === uptime.size && [...inventory].every(([name, info]) =>
      info.value === 1 && uptime.get(name)?.labels.type === info.labels.type),
    'Site device inventory or uptime samples are incomplete');
  }
  const onlineDevices = [...inventory].filter(([name, info]) => {
    const current = uptime.get(name);
    return info.value === 1 && current?.value > 0 && current.labels.type === info.labels.type;
  }).map(([name]) => name).sort();
  const target = matches.length ? readTarget(read, matches[0], onlineDevices.includes(device) ?
    uptime.get(device).value : 0) : null;

  const siteCounts = {};
  for (const name of siteMetrics) {
    const counts = byLabel(read(metric[name]), 'subsystem', `site ${name} subsystem`);
    requireEvidence(counts.size > 0 && [...counts.values()].every(({ value }) => Number.isSafeInteger(value)),
      `Site ${name} counts are missing or invalid`);
    siteCounts[name] = Object.fromEntries(Array.from(counts, ([subsystem, sample]) => [subsystem, sample.value])
      .sort(([a], [b]) => a.localeCompare(b)));
  }
  if (!baseline) {
    const total = (kind) => Object.values(siteCounts[kind]).reduce((sum, value) => sum + value, 0);
    requireEvidence(inventory.size === total('adopted') &&
      onlineDevices.length === total('adopted') - total('disconnected'),
    'Site totals do not account for the device inventory');
  }
  // Clients roam to neighbouring APs while one reboots and often stay there, so an AP is judged by
  // the site's wireless total, not by its own client count.
  const stations = read(metric.stations).filter(({ labels }) =>
    labels.type === 'uap' && ['user', 'guest'].includes(labels.station_type));
  const wifiClients = stations.length ? stations.reduce((sum, { value }) => sum + value, 0) : null;
  return { site, target, onlineDevices, siteCounts, wifiClients, wan: readWan(read) };
}

function assertBaseline(baseline) {
  const target = baseline?.target;
  const ports = (values) => Array.isArray(values) && new Set(values).size === values.length &&
    values.every((port) => Number.isSafeInteger(port) && port >= 0);
  const validCounts = siteMetrics.every((kind) => {
    const counts = baseline?.siteCounts?.[kind];
    return counts && typeof counts === 'object' && !Array.isArray(counts) && Object.keys(counts).length > 0 &&
      Object.entries(counts).every(([subsystem, value]) => label(subsystem) && Number.isSafeInteger(value) && value >= 0);
  });
  requireEvidence(baseline?.schema === 1 && typeof baseline.capturedAt === 'string' &&
    Number.isFinite(Date.parse(baseline.capturedAt)) && label(baseline.site) &&
    [target?.name, target?.version, target?.type, target?.model].every(label) &&
    nonnegative(target.uptimeSeconds) && target.uptimeSeconds > 0 && [0, 1].includes(target.upgradable) &&
    Array.isArray(baseline.onlineDevices) && baseline.onlineDevices.every(label) &&
    new Set(baseline.onlineDevices).size === baseline.onlineDevices.length &&
    baseline.onlineDevices.includes(target.name) && validCounts &&
    Array.isArray(baseline.wan) && baseline.wan.length > 0 &&
    baseline.wan.every((wan) => label(wan?.group) && wan.online === true &&
      Number.isSafeInteger(wan.monitors) && wan.monitors > 0 && nonnegative(wan.maxLatencySeconds) &&
      wan.maxLatencySeconds > 0) && new Set(baseline.wan.map((wan) => wan.group)).size === baseline.wan.length &&
    (target.type !== 'uap' || (Number.isSafeInteger(target.clients) && target.clients >= 0)) &&
    (target.type !== 'usw' || (ports(target.linkPorts) && target.linkPorts.length > 0 && ports(target.poePorts))),
  'Invalid baseline or missing required pre-update evidence');
}

function growingAfterReboot(history, capturedAt, at, currentUptime) {
  requireEvidence(history.length <= 1, 'Device uptime history is ambiguous');
  const samples = history[0]?.values ?? [];
  requireEvidence(Array.isArray(samples), 'Invalid device uptime history');
  const [previous, current] = samples.slice(-2);
  const previousValue = sampleValue(previous);
  const currentValue = sampleValue(current);
  if (!Number.isFinite(previous?.[0]) || !Number.isFinite(current?.[0]) ||
      previous[0] < capturedAt || current[0] <= previous[0] || at - previous[0] >= MAX_SAMPLE_AGE ||
      current[0] > at || previousValue <= 0 || currentValue <= previousValue || currentValue !== currentUptime) return false;

  const previousBoot = previous[0] - previousValue;
  const currentBoot = current[0] - currentValue;
  return previousBoot >= capturedAt && currentBoot >= capturedAt &&
    Math.abs(currentBoot - previousBoot) <= UPTIME_JITTER;
}

function assess(baseline, current, history, capturedAt, at, deadline, clientDropMax) {
  const stops = [];
  const waiting = [];
  const { target } = current;
  const lostPeers = baseline.onlineDevices.filter((name) =>
    name !== baseline.target.name && !current.onlineDevices.includes(name));
  // Devices downstream of a rebooting switch drop with it. Count a lost peer as a stop only once the
  // target is back on new firmware and settled; until then it waits, bounded by the deadline.
  const targetBack = target?.uptimeSeconds >= SETTLE_SECONDS && target.version !== baseline.target.version;
  if (lostPeers.length) {
    (targetBack ? stops : waiting).push(`Previously online devices unavailable: ${lostPeers.join(', ')}`);
  }

  // Site counts include the target while it downloads, flashes and reboots, and its old uptime
  // series can stay positive throughout. Check them only once it reports the new firmware.
  if (target?.uptimeSeconds > 0 && target.version !== baseline.target.version) {
    for (const kind of siteMetrics) {
      for (const [subsystem, before] of Object.entries(baseline.siteCounts[kind])) {
        const after = current.siteCounts[kind][subsystem];
        if (!Number.isSafeInteger(after) || (kind === 'adopted' ? after < before : after > before)) {
          waiting.push(`Site ${kind} count regressed for ${subsystem}`);
        }
      }
    }
  }

  for (const before of baseline.wan) {
    const after = current.wan.find((wan) => wan.group === before.group);
    if (!after?.online) {
      stops.push(`WAN ${before.group} is down or missing`);
    } else if (after.monitors < before.monitors || after.maxLatencySeconds === null) {
      stops.push(`WAN ${before.group} latency evidence is missing`);
    } else if (after.maxLatencySeconds > before.maxLatencySeconds + WAN_LATENCY_MARGIN) {
      const maxMs = ((before.maxLatencySeconds + WAN_LATENCY_MARGIN) * 1000).toFixed(1);
      stops.push(`WAN ${before.group} latency exceeds ${maxMs} ms`);
    }
  }

  if (target && (target.type !== baseline.target.type || target.model !== baseline.target.model)) {
    stops.push('Target device type or model changed');
  } else if (!target?.uptimeSeconds) {
    waiting.push('Target device has not returned online');
  } else {
    if (target.version === baseline.target.version) waiting.push('Target still reports the old firmware version');
    if (target.upgradable !== 0) waiting.push('Target upgrade flag is nonzero or missing');
    if (!growingAfterReboot(history, capturedAt, at, target.uptimeSeconds)) {
      waiting.push('No fresh, growing uptime from a reboot after the baseline');
    } else if (target.uptimeSeconds < SETTLE_SECONDS) {
      waiting.push(`Target has not settled for ${SETTLE_SECONDS} seconds since reboot`);
    } else if (target.type === 'uap') {
      // Shortfalls wait until the deadline: clients and links can take minutes to return.
      const siteWide = Number.isSafeInteger(baseline.wifiClients);
      const before = siteWide ? baseline.wifiClients : baseline.target.clients;
      const after = siteWide ? current.wifiClients : target.clients;
      const threshold = Math.ceil(before * (1 - clientDropMax) - Number.EPSILON * before);
      if (after === null || after === undefined || after < threshold) {
        waiting.push(`${siteWide ? 'Site wireless' : 'AP'} client count ${after ?? 'missing'} is below the required ${threshold}`);
      }
    } else if (target.type === 'usw') {
      const lostLinks = baseline.target.linkPorts.filter((port) => !target.linkPorts.includes(port));
      const lostPower = baseline.target.poePorts.filter((port) => !target.poePorts.includes(port));
      if (lostLinks.length) waiting.push(`Switch port links missing: ${lostLinks.join(', ')}`);
      if (lostPower.length) waiting.push(`Switch PoE power missing: ${lostPower.join(', ')}`);
    }
  }
  if (Date.now() / 1000 >= deadline) stops.push('Recovery deadline reached before GREEN');
  if (stops.length) return ['STOP', 2, [...stops, ...waiting]];
  if (waiting.length) return ['WAIT', 1, waiting];
  return ['GREEN', 0, [`Recovery checks passed on firmware ${target.version}`]];
}

async function main() {
  const [command, ...args] = process.argv.slice(2);
  if (command === '--help' || (['snapshot', 'check'].includes(command) && args.join() === '--help')) {
    console.log(usage);
    return;
  }
  requireEvidence(['snapshot', 'check'].includes(command), usage);
  const options = { device: { type: 'string' } };
  if (command === 'check') {
    options.baseline = { type: 'string' };
    options['target-not'] = { type: 'string' };
    options['deadline-min'] = { type: 'string', default: '10' };
    options['client-drop-max'] = { type: 'string', default: '0.3' };
  }
  let values;
  try {
    ({ values } = parseArgs({ args, options }));
  } catch {
    throw new Error(usage);
  }
  const { device } = values;
  requireEvidence(label(device), usage);
  const at = Date.now() / 1000;

  let baseline;
  let capturedAt;
  let deadline;
  let clientDropMax;
  if (command === 'check') {
    requireEvidence(label(values.baseline) && label(values['target-not']), usage);
    const minutes = Number(values['deadline-min']);
    clientDropMax = Number(values['client-drop-max']);
    requireEvidence(values['deadline-min'].trim() && Number.isFinite(minutes) && minutes > 0 &&
      values['client-drop-max'].trim() && nonnegative(clientDropMax) && clientDropMax <= 1,
    'Deadline must be positive and client-drop-max must be between 0 and 1');
    try {
      baseline = JSON.parse(await readFile(values.baseline, 'utf8'));
    } catch {
      throw new Error('Baseline file cannot be read as JSON');
    }
    assertBaseline(baseline);
    capturedAt = Date.parse(baseline.capturedAt) / 1000;
    deadline = capturedAt + minutes * 60;
    requireEvidence(capturedAt <= at && Number.isFinite(deadline), 'Invalid baseline time or deadline');
    requireEvidence(baseline.target.name === device && baseline.target.version === values['target-not'],
      'Device or --target-not does not match the baseline');
  }

  const firingAlerts = readFiringAlerts(await query(alertsQuery, at, 'vector', 'firing alerts'));
  if (baseline && firingAlerts.length) {
    report('STOP', 2, firingAlerts.map((alert) => `Firing alert: ${alert}`));
    return;
  }
  const samples = readSamples(await query(currentQuery, at, 'vector', 'unpoller samples'));
  const current = readSite(samples, device, baseline);
  if (command === 'snapshot') {
    const snapshot = {
      schema: 1,
      capturedAt: new Date(at * 1000).toISOString(),
      ...current,
      wan: current.wan.filter((wan) => wan.online),
      firingAlerts,
    };
    assertBaseline(snapshot);
    console.log(JSON.stringify(snapshot, null, 2));
    return;
  }

  let history = [];
  if (current.target?.uptimeSeconds > 0) {
    const uptimeQuery = `${metric.uptime}{job="unpoller",name=${JSON.stringify(device)},site_name=` +
      `${JSON.stringify(baseline.site)}}[${UPTIME_HISTORY}s]`;
    history = await query(uptimeQuery, at, 'matrix', 'target uptime history');
  }
  const [status, code, reasons] = assess(baseline, current, history, capturedAt, at, deadline, clientDropMax);
  report(status, code, reasons);
}

function report(status, code, reasons) {
  console.log(`${status}: ${reasons.join('; ')}`);
  process.exitCode = code;
}

main().catch((error) => {
  console.error(`STOP: ${error.message}`);
  process.exitCode = 2;
});
