import { execFile, spawnSync } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as wait } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { executeOne, inventoryLimit, makeReport, parseApprovals, readDevices, sameFirmware } from './unifi-runner.mjs';

const exec = promisify(execFile);
const namespace = 'upgrade-agent';
const reportName = 'unifi-inventory';
const reportPath = `/api/v1/namespaces/${namespace}/configmaps/${reportName}`;
const reportUpdatePath = `${reportPath}?fieldManager=kubectl-replace&fieldValidation=Strict`;
const site = 'default';
const gatePath = fileURLToPath(new URL('./unifi-gate.mjs', import.meta.url));
const approvalsPath = fileURLToPath(new URL('./unifi-approvals.yaml', import.meta.url));
const mcpUrl = 'http://mcp-unifi.observability.svc.cluster.local:3000/mcp';
const safeErrorCodes = new Set(['ENOENT', 'EACCES', 'ETIMEDOUT', 'ENOTFOUND', 'EAI_AGAIN', 'ECONNREFUSED', 'ECONNRESET']);
const plexServers = [
  ['plex', 'http://plex.media.svc.cluster.local:32400', 'PLEX_TOKEN'],
  ['plexops', 'http://plexops.media.svc.cluster.local:32400', 'PLEXOPS_TOKEN'],
  ['HaynesTower', 'http://haynestower.haynesnetwork:32400', 'HAYNESTOWER_TOKEN'],
];

async function readRpcReply(response, id) {
  if (!response.headers.get('content-type')?.startsWith('text/event-stream')) {
    return response.json();
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) throw new Error('MCP event stream ended without a reply');
      buffer += value;
      if (buffer.length > 2_000_000) throw new Error('MCP event stream exceeded its size limit');
      for (let event; (event = /\r?\n\r?\n/.exec(buffer));) {
        const data = buffer.slice(0, event.index).split(/\r?\n/).filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart()).join('\n');
        buffer = buffer.slice(event.index + event[0].length);
        if (!data) continue;
        const message = JSON.parse(data);
        if (message.id === id) return message;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
}

export function unifiClient(token, fetcher = fetch) {
  if (!token) throw new Error('UniFi MCP bearer is missing');
  let id = 0;
  let version;
  let session;
  const headers = () => ({
    Accept: 'application/json, text/event-stream',
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
    ...(version && { 'MCP-Protocol-Version': version }),
    ...(session && { 'Mcp-Session-Id': session }),
  });
  async function rpc(method, params, notification = false) {
    try {
      const request = { jsonrpc: '2.0', method, params, ...(notification ? {} : { id: ++id }) };
      const response = await fetcher(mcpUrl, {
        method: 'POST', headers: headers(), redirect: 'error', signal: AbortSignal.timeout(30_000),
        body: JSON.stringify(request),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      if (notification) return;
      if (method === 'initialize') session = response.headers.get('mcp-session-id');
      const reply = await readRpcReply(response, request.id);
      if (reply?.jsonrpc !== '2.0' || reply.id !== request.id || reply.error || !reply.result) {
        throw new Error('Invalid MCP reply');
      }
      return reply.result;
    } catch (error) {
      // Preserve only known network codes from fetch, with no response or error text.
      const code = error?.cause?.code;
      const failure = new Error(`UniFi MCP ${method} request failed`);
      if (safeErrorCodes.has(code)) failure.code = code;
      throw failure;
    }
  }
  async function call(name, args) {
    const result = await rpc('tools/call', { name, arguments: { site_id: site, ...args } });
    if (result.isError) throw new Error(`UniFi MCP refused ${name}`);
    if (result.structuredContent) return result.structuredContent;
    try {
      const [block] = result.content;
      if (result.content.length === 1 && block?.type === 'text') return JSON.parse(block.text);
    } catch {
      // Do not propagate tool content in an error.
    }
    throw new Error(`UniFi MCP returned invalid data for ${name}`);
  }

  return {
    async connect() {
      const result = await rpc('initialize', {
        protocolVersion: '2025-11-25', capabilities: {}, clientInfo: { name: 'unifi-runner', version: '1' },
      });
      if (!['2025-03-26', '2025-06-18', '2025-11-25'].includes(result.protocolVersion)) {
        throw new Error('UniFi MCP negotiated an unsupported protocol');
      }
      version = result.protocolVersion;
      await rpc('notifications/initialized', undefined, true);
    },
    async close() {
      if (!session) return;
      try {
        const response = await fetcher(mcpUrl, {
          method: 'DELETE', headers: headers(), redirect: 'error', signal: AbortSignal.timeout(2000),
        });
        await response.body?.cancel();
      } catch {
        // The run's verdict stands if session cleanup cannot reach the server.
      }
    },
    async listDevices() {
      return readDevices(await call('list_integration_devices', { limit: inventoryLimit, offset: 0 }));
    },
    async dryRun(device) {
      const result = await call('upgrade_device', { device_mac: device.hardwareAddress, dry_run: true });
      return result?.dry_run === true && result.would_upgrade === device.hardwareAddress.toLowerCase();
    },
    async confirm(device, baselineVersion) {
      const result = await call('upgrade_device', { device_mac: device.hardwareAddress, confirm: true });
      return result?.success === true && result.device_mac === device.hardwareAddress.toLowerCase() &&
        sameFirmware(device.version, result.current_version) && sameFirmware(result.current_version, baselineVersion);
    },
  };
}

export async function plexIdle(env, fetcher = fetch, log = console.log) {
  let allIdle = true;
  for (const [server, base, tokenKey] of plexServers) {
    const token = env[tokenKey];
    let idle = false;
    if (token) {
      try {
        const response = await fetcher(`${base}/status/sessions`, {
          headers: { Accept: 'application/json', 'X-Plex-Token': token },
          signal: AbortSignal.timeout(5000), redirect: 'error',
        });
        if (response.ok) {
          const { MediaContainer } = await response.json();
          idle = MediaContainer?.size === 0 &&
            (MediaContainer.Metadata === undefined ||
              (Array.isArray(MediaContainer.Metadata) && MediaContainer.Metadata.length === 0));
        } else {
          await response.body?.cancel();
        }
      } catch {
        // Unreadable counts are treated like a stream. Do not log the Plex response.
      }
    }
    if (!idle) {
      allIdle = false;
      log(`Plex ${server}: active streams or unreadable sessions`);
    }
  }
  return allIdle;
}

// Kubectl can include response bodies and endpoint details in stderr. Keep only
// a known diagnostic class from the first line.
const kubectlFailures = [
  [/^(?:Error from server \(Unauthorized\):|error: You must be logged in to the server\b)/, 'unauthorized'],
  [/^Error from server \(Forbidden\):/, 'forbidden'],
  [/^Error from server \(NotFound\):/, 'not found'],
  [/^The connection to the server localhost:8080 was refused\b/, 'localhost fallback'],
  [/^The connection to the server .+ was refused\b/, 'connection refused'],
  [/^(?:error: )?(?:unable to load in-cluster configuration\b|no configuration has been provided\b)/, 'no in-cluster config'],
  [/^(?:Unable to connect to the server:|error:) .*\bx509:/, 'TLS certificate error'],
  [/^Error from server \((?:Server)?Timeout\):/, 'timeout'],
  [/^(?:Unable to connect to the server:|error:) (?:.*\b)?(?:context deadline exceeded|i\/o timeout|TLS handshake timeout|Client\.Timeout exceeded)\b/, 'timeout'],
];

class KubectlError extends Error {
  constructor(status, stderr) {
    super('kubectl failed');
    this.status = status;
    const firstLine = stderr.trimStart().split(/\r?\n/, 1)[0];
    this.reason = kubectlFailures.find(([pattern]) => pattern.test(firstLine))?.[1];
  }
}

function logStageFailure(stage, error) {
  let detail = '';
  if (error instanceof KubectlError && Number.isInteger(error.status)) {
    const reason = error.reason ? `, ${error.reason}` : '';
    detail = ` (kubectl exit ${error.status}${reason})`;
  } else if (safeErrorCodes.has(error?.code)) {
    detail = ` (${error.code})`;
  }
  console.error(`STOP: ${stage}${detail}`);
}

function reportStore() {
  function kubectl(args, input) {
    const command = spawnSync('kubectl', ['--request-timeout=8s', ...args], {
      input, encoding: 'utf8', timeout: 10_000, stdio: ['pipe', 'pipe', 'pipe'],
    });
    if (command.error) throw command.error;
    if (command.status !== 0) throw new KubectlError(command.status, command.stderr);
    return JSON.parse(command.stdout);
  }
  // A fixed core API resource needs neither API discovery nor its on-disk cache.
  let cm = kubectl(['get', '--raw', reportPath]);
  return {
    read(key) { return JSON.parse(cm.data?.[key] ?? '[]'); },
    write(data) {
      // Keep resourceVersion on replace. Two pods cannot both reserve the same attempt.
      if (!cm.metadata?.resourceVersion) throw new Error('UniFi inventory ConfigMap has no resource version');
      cm = kubectl(['replace', '--raw', reportUpdatePath, '-f', '-'], JSON.stringify({
        apiVersion: 'v1',
        kind: 'ConfigMap',
        metadata: {
          name: reportName, namespace, resourceVersion: cm.metadata.resourceVersion,
          labels: cm.metadata.labels, annotations: cm.metadata.annotations,
        },
        data: { ...cm.data, ...data },
      }));
    },
  };
}

async function runGate(args) {
  try {
    const { stdout } = await exec(process.execPath, [gatePath, ...args], { timeout: 30_000 });
    return { code: 0, text: stdout.trim() };
  } catch (error) {
    return { code: error.code, text: (error.stdout || error.stderr || '').trim() };
  }
}

async function main() {
  let stage = 'configmap-read';
  let client;
  let temp;
  try {
    const store = reportStore();
    const previous = store.read('inventory.json');
    let approvals = [];
    let manifestError = false;
    try {
      approvals = parseApprovals(await readFile(approvalsPath, 'utf8'));
    } catch {
      manifestError = true;
    }
    stage = 'mcp-connect';
    client = unifiClient(process.env.MCP_AUTH_TOKEN);
    await client.connect();
    stage = 'mcp-list';
    const devices = await client.listDevices();
    stage = 'report';
    const now = Date.now();
    const report = makeReport(devices, approvals, previous, now);
    stage = 'configmap-write';
    store.write({
      'inventory.json': JSON.stringify(report.inventory),
      'inventory.md': report.table,
      'queue.json': JSON.stringify(report.queue),
      'summary.txt': report.summary,
      observed_at: report.at,
    });
    console.log(report.summary);

    stage = 'journal';
    let journal = store.read('live-attempts.json');
    if (!Array.isArray(journal) || journal.some(({ outcome }) => !['PENDING', 'GREEN', 'STOP'].includes(outcome))) {
      throw new Error('UniFi live-attempt journal is invalid');
    }
    const jobUID = process.env.JOB_UID;
    journal = journal.filter((attempt) =>
      attempt.outcome !== 'GREEN' || attempt.jobUID === jobUID || Date.parse(attempt.not_after) > now);
    const live = process.env.UNIFI_LIVE_MODE === 'true';
    let baselinePath;
    const io = {
      now: Date.now, wait, listDevices: client.listDevices,
      dryRun: client.dryRun, confirm: client.confirm, plexIdle: () => plexIdle(process.env),
      async snapshot(device) {
        const gate = await runGate(['snapshot', '--device', device]);
        if (gate.code !== 0) return null;
        const baseline = JSON.parse(gate.text);
        if (live) {
          temp = await mkdtemp(join(tmpdir(), 'unifi-'));
          baselinePath = join(temp, 'baseline.json');
          await writeFile(baselinePath, gate.text, { mode: 0o600 });
        }
        return baseline;
      },
      async check(device, baseline, minutes) {
        const gate = await runGate(['check', '--device', device, '--baseline', baselinePath,
          '--target-not', baseline.target.version, '--deadline-min', String(minutes)]);
        const status = ['GREEN', 'WAIT', 'STOP'][gate.code];
        return status && gate.text.startsWith(`${status}: `)
          ? { status, reason: gate.text.slice(status.length + 2) }
          : { status: 'STOP', reason: 'UniFi gate did not return a usable result' };
      },
      async saveAttempt(attempt) {
        const next = journal.filter(({ outcome }) => outcome !== 'PENDING');
        if (attempt) next.push({ ...attempt, recorded_at: new Date().toISOString() });
        store.write({ 'live-attempts.json': JSON.stringify(next) });
        journal = next;
      },
    };

    let result;
    try {
      result = manifestError
        ? { outcome: 'STOP', reason: 'Approval manifest is unreadable or invalid' }
        : await executeOne({ candidates: report.candidates, live, jobUID, journal, io });
    } catch {
      result = { outcome: 'STOP', reason: 'UniFi pre-check or attempt-journal operation failed' };
    }
    stage = 'last-run-write';
    store.write({ 'last-run.json': JSON.stringify({ ...result, at: new Date().toISOString(), live }) });
    console.log(`${result.outcome}: ${result.reason}`);
    if (result.outcome === 'STOP') process.exitCode = 1;
  } catch (error) {
    logStageFailure(stage, error);
    process.exitCode = 1;
  } finally {
    await client?.close();
    if (temp) await rm(temp, { recursive: true, force: true });
  }
}

if (import.meta.main) {
  main().catch(() => {
    console.error('STOP: UniFi Job cleanup failed');
    process.exitCode = 1;
  });
}
