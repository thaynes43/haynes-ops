import { test } from 'node:test';
import assert from 'node:assert/strict';
import { plan } from './plan.mjs';
const now = '2026-11-18T14:00:00Z';
const device = { node: 55, name: 'sensor', model: 'ZSE44', hardware_version: 2, from_version: '2.40.0', to_version: '2.50.0', safetyRelated: false, endDevice: true };
function fixture() {
 return { now, manifest: { batchId: 'test-only', approvalRef: 'fixture-not-live-approval', approvalVerified: true, devices: [structuredClone(device)] },
 state: { batchId: 'test-only', index: 0, receipts: [] }, evidence: { at: now, device: structuredClone(device), battery: 80, batteryAt: now, scheduledWakeAt: now, firstWakeAfterPowerCycle: false,
 driver: { authoritative: true, bootId: 'boot1', status: 'idle' }, app: { updatePRsKnown: true, openUpdate: false, mergedUnapplied: false, mainVersion: 'v1', runningVersion: 'v1', settled: true } } };
}
function request(f, phase = 'waiting') { f.state.request = { device: structuredClone(device), startedAt: '2026-11-18T13:00:00Z', driverBootId: 'boot1', replacements: 0, phase, requestKey: 'request1' }; }
function check(name, change, decision, reason) { test(name, () => { const f=fixture();change(f);const before=structuredClone(f);const r=plan(f);assert.equal(r.decision,decision);if(reason)assert.equal(r.reason,reason);assert.equal(r.executable,false);assert.deepEqual(f,before); }); }
check('valid initial is only a proposal',()=>{},'propose-initial');
check('approval missing',f=>f.manifest.approvalVerified=false,'stop');
check('batch identity mismatch',f=>f.state.batchId='other','stop');
check('missing hardware blocks',f=>delete f.manifest.devices[0].hardware_version,'stop');
check('never-touch held node',f=>f.manifest.devices[0].node=8,'stop');
check('safety classification unknown',f=>delete f.manifest.devices[0].safetyRelated,'stop');
check('duplicate nodes',f=>f.manifest.devices.push(structuredClone(device)),'stop');
check('prior receipt cannot be skipped',f=>f.state.index=1,'stop');
check('done requires every receipt',f=>{f.state.index=1;f.state.receipts=[{device,verified:true}];},'done');
check('old current evidence',f=>f.evidence.at='2026-11-18T13:58:59Z','stop');
check('future current evidence',f=>f.evidence.at='2026-11-18T14:00:01Z','stop');
check('HA flag cannot establish idle driver',f=>{delete f.evidence.driver;f.evidence.in_progress=false;},'stop');
check('other waiting request blocks',f=>f.evidence.driver.status='waiting','stop');
check('from version mismatch',f=>f.evidence.device.from_version='2.30.0','stop');
check('battery under floor',f=>f.evidence.battery=49,'stop');
check('battery stale',f=>f.evidence.batteryAt='2026-11-17T13:59:59Z','stop');
check('scheduled wake stale',f=>f.evidence.scheduledWakeAt='2026-11-17T13:59:59Z','stop');
check('first wake after battery change',f=>f.evidence.firstWakeAfterPowerCycle=true,'stop');
for(const key of ['openUpdate','mergedUnapplied'])check(`app ${key} defers without hold`,f=>f.evidence.app[key]=true,'wait');
check('unknown app PRs defer',f=>delete f.evidence.app.updatePRsKnown,'wait');
check('app main mismatch defers',f=>f.evidence.app.runningVersion='old','wait');
for(const date of ['2026-11-19T04:30:00Z','2026-07-19T03:30:00Z'])check(`midnight DST-aware ${date}`,f=>{f.now=date;f.evidence.at=date;f.evidence.batteryAt=date;f.evidence.scheduledWakeAt=date;},'wait','midnight-start-margin');
check('retained waiting request does not restart',f=>{request(f);Object.assign(f.evidence.driver,{status:'waiting',node:55,requestKey:'request1'});},'wait','request-retained');
check('active transfer left alone despite app update',f=>{request(f,'transferring');Object.assign(f.evidence.driver,{status:'transferring',node:55,requestKey:'request1'});f.evidence.app.openUpdate=true;},'wait','leave-active-transfer-alone');
check('active transfer restart stops',f=>{request(f,'transferring');f.evidence.driver.bootId='boot2';},'stop','active-transfer-interrupted');
check('unmatched request never accepted',f=>{request(f);Object.assign(f.evidence.driver,{status:'transferring',node:55,requestKey:'other'});},'stop','unmatched-driver-request');
check('unknown disappearance stops',f=>request(f),'stop','unexplained-missing-request');
check('restart alone not drop proof',f=>{request(f);f.evidence.driver.bootId='boot2';},'stop');
check('confirmed waiting-only drop allows proposal',f=>{request(f);Object.assign(f.evidence.driver,{bootId:'boot2',waitingDropConfirmed:true});},'propose-replacement');
check('replacement repeats battery gate',f=>{request(f);Object.assign(f.evidence.driver,{bootId:'boot2',waitingDropConfirmed:true});f.evidence.battery=10;},'stop','battery-check');
check('fourth drop stops',f=>{request(f);f.state.request.replacements=3;Object.assign(f.evidence.driver,{bootId:'boot2',waitingDropConfirmed:true});},'stop','fourth-drop');
check('wake timeout not reset by app restart',f=>{request(f);f.state.request.startedAt='2026-11-18T01:00:00Z';Object.assign(f.evidence.driver,{bootId:'boot2',waitingDropConfirmed:true});},'stop','wake-timeout-leave-request');
check('aborted transfer stops',f=>{request(f);Object.assign(f.evidence.driver,{status:'aborted',requestKey:'request1'});},'stop','transfer-failed');
check('completed requires health receipt',f=>{request(f);Object.assign(f.evidence.driver,{status:'completed',node:55,requestKey:'request1'});},'stop');
check('complete receipt permits recording, not next start',f=>{request(f);Object.assign(f.evidence.driver,{status:'completed',node:55,requestKey:'request1'});f.evidence.receipt={device,versionCC:'2.50.0',haVersion:'2.50.0',updateOff:true,normalState:true,zwaveChecker:0,batteryChecker:0,sensorAt:now,battery:80,batteryAt:now};},'record-receipt');
check('invalid clock fails closed',f=>f.now='bad','stop');

check('malformed prior receipt fails closed',f=>{f.state.index=1;f.state.receipts=[null];},'stop');
check('cannot finish with residual request',f=>{request(f);f.state.index=1;f.state.receipts=[{device,verified:true}];},'stop','request-after-batch-end');
check('ZSE70 uses five-hour wake bound',f=>{f.manifest.devices[0].model='ZSE70';f.evidence.device.model='ZSE70';request(f);f.state.request.device.model='ZSE70';f.state.request.startedAt='2026-11-18T09:00:00Z';},'stop','wake-timeout-leave-request');
