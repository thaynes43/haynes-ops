const timeZone = 'America/New_York';
const localHour = new Intl.DateTimeFormat('en-GB', { timeZone, hour: 'numeric', hourCycle: 'h23' });
export const inventoryLimit = 1000;
const versionPattern = /^\d+(?:\.\d+){1,5}$/;
const label = (value) => typeof value === 'string' && value === value.trim() &&
  value.length > 0 && value.length <= 120 && !/[\p{C}\p{Zl}\p{Zp}]/u.test(value);
const timestamp = (value) => typeof value === 'string' &&
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/.test(value) &&
  Number.isFinite(Date.parse(value)) &&
  new Date(value).toISOString() === value.replace(/(\d{2}:\d{2}:\d{2})Z$/, '$1.000Z');

const radios = 'radios: HA warned';
const attended = 'attended: needs Tom\'s approval naming the device';
const tiers = [
  { family: 'Indoor AP', models: ['U7 Pro', 'U7-Pro-Wall'], deadline: 15 },
  { family: 'Outdoor AP', models: ['U7 Outdoor', 'U7 Pro Outdoor'], deadline: 15 },
  { family: 'Leaf switch', models: ['USW Flex', 'USW Flex 2.5G 5', 'USW Flex 2.5G 8 PoE'], deadline: 30 },
  { family: 'Pro Max 16/24', models: ['USW Pro Max 16 PoE', 'USW Pro Max 24 PoE'], deadline: 30 },
  { family: 'Pro Max 48', models: ['USW Pro Max 48 PoE'], deadline: 30, note: radios },
  { family: 'Aggregation', models: ['USW Aggregation'], note: attended },
  { family: 'Pro Aggregation', models: ['USW Pro Aggregation'], note: attended },
  { family: 'Gateway', models: ['UniFi Dream Machine PRO SE'], note: attended },
  { family: 'Cable modem', models: ['Cable Internet'], note: attended },
];
const models = new Map(tiers.flatMap((tier, order) => tier.models.map((model) => [model, { ...tier, order }])));
const inventoryOnly = new Set(['USP RPS', 'USP PDU HD', 'USP PDU Pro', 'USW WAN RJ45']);

export function parseApprovals(text) {
  const entries = JSON.parse(text);
  if (!Array.isArray(entries)) throw new Error('Approval manifest must be a list');
  return entries.map((entry, index) => {
    const { device, from_version, approved_by_ref, not_before, not_after } = entry ?? {};
    if (Object.keys(entry ?? {}).length !== 5 || !label(device) ||
        typeof from_version !== 'string' || !versionPattern.test(from_version) ||
        !/^[1-9]\d*$/.test(String(approved_by_ref)) ||
        !['number', 'string'].includes(typeof approved_by_ref) ||
        (typeof approved_by_ref === 'number' && !Number.isSafeInteger(approved_by_ref)) ||
        !timestamp(not_before) || !timestamp(not_after) || Date.parse(not_after) <= Date.parse(not_before)) {
      throw new Error(`Invalid approval at entry ${index + 1}`);
    }
    return { device, from_version, approved_by_ref: String(approved_by_ref), not_before, not_after };
  });
}

export function readDevices(payload) {
  const { data, count, totalCount, offset } = payload ?? {};
  if (!Array.isArray(data) || count !== data.length || totalCount !== count || offset !== 0 ||
      count === 0 || count >= inventoryLimit) {
    throw new Error('UniFi integration inventory is empty or incomplete');
  }
  return data.map((row) => {
    if (!label(row?.name) || !label(row.model) || !label(row.state) ||
        typeof row.firmwareUpdatable !== 'boolean') {
      throw new Error('UniFi integration inventory contains an incomplete device');
    }
    return {
      name: row.name,
      model: row.model,
      version: typeof row.firmwareVersion === 'string' && versionPattern.test(row.firmwareVersion)
        ? row.firmwareVersion : null,
      online: row.state === 'ONLINE',
      firmwareUpdatable: row.firmwareUpdatable,
      supported: row.supported === true,
      hardwareAddress: row.macAddress,
    };
  });
}

const identity = ({ name, model, version }) => JSON.stringify([name, model, version]);
const currentApproval = (approval, now) => now >= Date.parse(approval.not_before) && now < Date.parse(approval.not_after);
const matchesApproval = (device, approval) => approval.device === device.name && approval.from_version === device.version;

export function makeReport(devices, approvals, previous, now) {
  const at = new Date(now).toISOString();
  const oldOffers = new Map(previous.map((row) => [identity(row), row.firstSeen]));
  const nameCounts = new Map();
  for (const { name } of devices) nameCounts.set(name, (nameCounts.get(name) ?? 0) + 1);

  const candidates = [];
  const inventory = devices.map(({ name, model, version, online, firmwareUpdatable, supported }) => {
    const row = { name, model, version, online, firmwareUpdatable };
    const previousSeen = oldOffers.get(identity(row));
    const firstSeen = firmwareUpdatable
      ? (timestamp(previousSeen) && Date.parse(previousSeen) <= now ? previousSeen : at) : null;
    const tier = models.get(model);
    const active = approvals.filter((approval) => matchesApproval(row, approval) && currentApproval(approval, now));
    let status = 'Eligible';
    if (!online) status = 'Offline';
    else if (!supported || (!tier && !inventoryOnly.has(model))) status = 'EOL status unknown';
    else if (!firmwareUpdatable) status = 'No update offered';
    else if (!tier) status = 'Inventory only';
    else if (!version || nameCounts.get(name) !== 1) status = 'Device or firmware is ambiguous';
    else if (active.length > 1) status = 'Overlapping approvals';
    else if (tier.note && active.length === 0) status = tier.note === radios ? 'Needs approval and HA warning' : attended;
    else candidates.push({ ...row, ...tier, approval: active[0] });

    return { ...row, firstSeen, status };
  }).sort((a, b) => a.name.localeCompare(b.name));
  candidates.sort((a, b) => a.order - b.order || a.name.localeCompare(b.name));
  const queue = candidates.map(({ name, model, version, family, note, approval }) => ({
    name, model, version, family, note: note ?? null, approvedBy: approval?.approved_by_ref ?? null,
  }));
  const cell = (value) => String(value ?? '—').replace(/[&<>|`]/g, (c) => `&#${c.charCodeAt(0)};`);
  const line = (values) => `| ${values.map(cell).join(' | ')} |`;
  const table = [
    `# UniFi inventory — ${at}`,
    '',
    '| Device | Model | Firmware | Online | Firmware updatable | First seen offered (UTC) | Eligibility |',
    '| --- | --- | --- | --- | --- | --- | --- |',
    ...inventory.map((row) => line([row.name, row.model, row.version, row.online, row.firmwareUpdatable, row.firstSeen, row.status])),
    '',
    '## Eligibility queue',
    '',
    '| Device | Family | Firmware | Approval comment | Requirement |',
    '| --- | --- | --- | --- | --- |',
    ...queue.map((row) => line([row.name, row.family, row.version, row.approvedBy, row.note])),
    '',
  ].join('\n');
  const summary = `UniFi ${at.slice(0, 10)}: ${inventory.filter((row) => row.online).length}/${inventory.length} online, ${inventory.filter((row) => row.firmwareUpdatable).length} updates offered, ${queue.length} queued.`;
  return { at, inventory, queue, candidates, table, summary };
}

export function inWindow(now) {
  const hour = Number(localHour.format(now));
  return hour >= 1 && hour < 6;
}

export function sameFirmware(reported, baseline) {
  return typeof baseline === 'string' && versionPattern.test(baseline) &&
    (baseline === reported || baseline.startsWith(`${reported}.`));
}

export async function executeOne({ candidates, live, jobUID, journal, io }) {
  const result = (outcome, reason) => ({ outcome, reason });
  if (live && journal.some((attempt) => attempt.outcome !== 'GREEN')) {
    return result('STOP', 'Earlier live attempt needs operator review');
  }
  if (live && !jobUID) return result('STOP', 'Kubernetes Job identity is missing');
  if (live && journal.some((attempt) => attempt.jobUID === jobUID)) {
    return result('SKIP', 'This Job already ran a live attempt');
  }
  const startedAt = io.now();
  if (!inWindow(startedAt)) return result('SKIP', 'Outside the overnight window');
  const target = candidates.find(({ approval, deadline }) => approval && deadline &&
    currentApproval(approval, startedAt) &&
    !journal.some((attempt) => attempt.device === approval.device &&
      attempt.from_version === approval.from_version && attempt.approved_by_ref === approval.approved_by_ref));
  if (!target) return result('SKIP', 'No current approval for an unattended device');

  const approval = target.approval;
  const detail = (outcome, reason) => ({ ...result(outcome, reason), device: target.name,
    from_version: target.version, approved_by_ref: approval.approved_by_ref });
  if (!(await io.plexIdle())) return detail('SKIP', 'Plex has streams or session counts are unreadable');

  const verifyDevice = async () => {
    const devices = (await io.listDevices()).filter(({ name }) => name === target.name);
    const [device] = devices;
    return devices.length === 1 && device.online && device.supported && device.firmwareUpdatable &&
      device.model === target.model && device.version === target.version &&
      typeof device.hardwareAddress === 'string' && device.hardwareAddress.length > 0 ? device : null;
  };

  const device = await verifyDevice();
  if (!device) return detail('SKIP', 'Controller no longer reports the approved device and firmware');
  const baseline = await io.snapshot(device.name).catch(() => null);
  const expectedType = target.deadline === 15 ? 'uap' : 'usw';
  if (!baseline || baseline.target?.name !== device.name || baseline.target.type !== expectedType ||
      !sameFirmware(device.version, baseline.target.version) || baseline.target.upgradable !== 1 ||
      !Array.isArray(baseline.firingAlerts) || baseline.firingAlerts.length > 0 ||
      !timestamp(baseline.capturedAt) || Date.parse(baseline.capturedAt) > io.now()) {
    return detail('STOP', 'UniFi gate did not produce an eligible baseline');
  }
  const deadline = Date.parse(baseline.capturedAt) + target.deadline * 60_000;
  const mayStart = () => {
    const at = io.now();
    return at < deadline && inWindow(at) && currentApproval(approval, at);
  };
  if (!mayStart()) return detail('SKIP', 'Approval, overnight window, or baseline expired during pre-checks');
  if (!(await io.dryRun(device).catch(() => false))) return detail('STOP', 'UniFi dry run failed or was refused');
  if (!mayStart()) return detail('SKIP', 'Approval, overnight window, or baseline expired during dry run');
  if (!live) return detail('WOULD_UPGRADE', `would upgrade ${device.name}`);

  const fresh = await verifyDevice();
  if (fresh?.hardwareAddress !== device.hardwareAddress) {
    return detail('SKIP', 'Controller no longer reports the same approved device and firmware');
  }
  if (!(await io.plexIdle())) return detail('SKIP', 'Plex is no longer confirmed idle');
  if (!mayStart()) return detail('SKIP', 'Approval, overnight window, or baseline expired before confirmation');
  // Claim before sending. A restarted Job or an ambiguous response must never retry a confirm.
  await io.saveAttempt({ ...approval, jobUID, outcome: 'PENDING' });
  let outcome;
  try {
    if (!mayStart()) {
      await io.saveAttempt(null);
      return detail('SKIP', 'Approval, overnight window, or baseline expired before confirmation');
    }
    if (!(await io.confirm(device, baseline.target.version))) {
      outcome = detail('STOP', 'UniFi did not confirm an unambiguous upgrade submission');
    } else {
      outcome = detail('STOP', 'Recovery deadline reached before GREEN');
      while (io.now() < deadline) {
        const check = await io.check(device.name, baseline, target.deadline);
        if (check.status === 'STOP') {
          outcome = detail('STOP', check.reason);
          break;
        }
        if (check.status === 'GREEN' && io.now() < deadline) {
          outcome = detail('GREEN', check.reason);
          break;
        }
        await io.wait(Math.max(0, Math.min(30_000, deadline - io.now())));
      }
    }
  } catch {
    outcome = detail('STOP', 'Upgrade submission or recovery could not be verified');
  }
  await io.saveAttempt({ ...approval, jobUID, outcome: outcome.outcome, reason: outcome.reason });
  return outcome;
}
