// OP644: pure policy planner, NOT a firmware executor. No I/O or credentials.
// Evidence is supplied by a trusted adapter; JSON itself cannot confer approval.
const HOUR = 3600000;
const fields = ['node', 'name', 'model', 'hardware_version', 'from_version', 'to_version'];
const heldNodes = new Set([8, 9, 21, 42, 43, 63]);
const allowedModels = new Map([['ZSE44', 13], ['ZSE70', 5]]);
const time = x => typeof x === 'string' ? Date.parse(x) : NaN;
const fresh = (x, now, age) => Number.isFinite(time(x)) && time(x) <= now && now - time(x) <= age;
const same = (a, b) => fields.every(k => a?.[k] !== undefined && a[k] !== null && a[k] !== '' && a[k] === b?.[k]);
const result = (decision, reason) => ({ decision, reason, executable: false });

/** Pure recommendation only. The future executor must atomically persist intent BEFORE
 * dispatch, reconcile ambiguous starts rather than replay, and serialize the network.
 * evidence.at bounds all current driver/app/entity observations to 60 seconds.
 * approvalVerified is supplied by a trusted approval resolver, never a manifest author.
 */
export function plan({ manifest, state, evidence, now: nowText }) {
  const now = time(nowText);
  const stop = reason => result('stop', reason);
  const wait = reason => result('wait', reason);
  if (!Number.isFinite(now)) return stop('invalid-clock');
  if (!manifest || manifest.approvalVerified !== true || typeof manifest.approvalRef !== 'string' || !manifest.approvalRef.trim() ||
      typeof manifest.batchId !== 'string' || !manifest.batchId.trim() || !Array.isArray(manifest.devices) || !manifest.devices.length) return stop('unverified-batch');
  if (!state || state.batchId !== manifest.batchId || !Number.isInteger(state.index) || state.index < 0 || state.index > manifest.devices.length) return stop('invalid-state');
  const ids = manifest.devices.map(d => d?.node);
  if (new Set(ids).size !== ids.length || ids.some(n => !Number.isInteger(n) || n <= 0)) return stop('invalid-device-list');
  for (const d of manifest.devices) {
    if (!same(d, d) || !allowedModels.has(d.model) || heldNodes.has(d.node) || d.safetyRelated !== false || d.endDevice !== true) return stop('excluded-or-unknown-device');
  }
  if (!Array.isArray(state.receipts) || state.receipts.length !== state.index ||
      state.receipts.some((r, i) => !same(r.device, manifest.devices[i]) || r.verified !== true)) return stop('missing-prior-receipt');
  if (state.index === manifest.devices.length) return result('done', 'all-receipts-verified');
  const device = manifest.devices[state.index];
  if (!evidence || !fresh(evidence.at, now, 60000)) return stop('stale-or-missing-evidence');
  const driver = evidence.driver;
  if (!driver || typeof driver.bootId !== 'string' || !driver.bootId || driver.authoritative !== true ||
      !['idle', 'waiting', 'transferring', 'completed', 'failed', 'aborted'].includes(driver.status)) return stop('unknown-driver-state');
  const req = state.request;
  if (req) {
    if (!same(req.device, device) || !fresh(req.startedAt, now, Infinity) || typeof req.driverBootId !== 'string' || !req.driverBootId ||
        !Number.isInteger(req.replacements) || req.replacements < 0 || req.replacements > 3 ||
        typeof req.requestKey !== 'string' || !req.requestKey || !['waiting', 'transferring'].includes(req.phase)) return stop('invalid-request-record');
    if (driver.status !== 'idle' && driver.requestKey !== req.requestKey) return stop('unmatched-driver-request');
    if (driver.status === 'failed' || driver.status === 'aborted') return stop('transfer-failed');
    if (req.phase === 'transferring' && driver.bootId !== req.driverBootId) return stop('active-transfer-interrupted');
    if (driver.status === 'transferring' && driver.node === device.node) return wait('leave-active-transfer-alone');
    if (driver.status === 'completed' && driver.node === device.node) {
      const r = evidence.receipt;
      if (!r || !same(r.device, device) || r.versionCC !== device.to_version || r.haVersion !== device.to_version ||
          r.updateOff !== true || r.normalState !== true || !fresh(r.sensorAt, now, 24 * HOUR) ||
          !Number.isFinite(r.battery) || r.battery < 50 || !fresh(r.batteryAt, now, 24 * HOUR)) return stop('incomplete-post-update-receipt');
      return result('record-receipt', 'verified-target-and-health');
    }
    if (req.phase === 'transferring') return stop('lost-active-transfer');
    // Bound never resets on a replacement: startedAt remains the initial start.
    if (now - time(req.startedAt) >= allowedModels.get(device.model) * HOUR) return stop('wake-timeout-leave-request');
    if (driver.status === 'waiting' && driver.node === device.node) return wait('request-retained');
    if (driver.status !== 'idle') return stop('other-or-ambiguous-request');
    if (driver.bootId === req.driverBootId || driver.waitingDropConfirmed !== true) return stop('unexplained-missing-request');
    if (req.replacements >= 3) return stop('fourth-drop');
  } else if (driver.status !== 'idle') return stop('network-not-free');
  // These gates apply to every initial/replacement start, never to cancelling a transfer.
  if (!same(evidence.device, device)) return stop('compatibility-mismatch');
  if (!Number.isFinite(evidence.battery) || evidence.battery < 50 || evidence.battery > 100 || !fresh(evidence.batteryAt, now, 24 * HOUR)) return stop('battery-check');
  if (!fresh(evidence.scheduledWakeAt, now, 24 * HOUR) || evidence.firstWakeAfterPowerCycle !== false) return stop('wake-check');
  const app = evidence.app;
  if (!app || app.updatePRsKnown !== true || app.openUpdate !== false || app.mergedUnapplied !== false ||
      typeof app.mainVersion !== 'string' || !app.mainVersion || app.runningVersion !== app.mainVersion || app.settled !== true) return wait('app-update-not-settled');
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(now));
  const value = k => Number(parts.find(p => p.type === k).value);
  if (value('hour') === 23 && value('minute') >= 30) return wait('midnight-start-margin');
  return result(req ? 'propose-replacement' : 'propose-initial', 'prechecks-pass-no-execution');
}
