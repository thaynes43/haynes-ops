#!/usr/bin/env node
// test-lane-claim-hook.mjs — the night lane's claim step, live, against whatever kubectl is on
// PATH (the story 554 selftests put test-fake-cluster.mjs there): the "other actor" of a race
// test, or the lane side of a sequence. The signature is given (SIG, IDS as JSON), so the hook
// runs no library; the record comes from the fixture. Prints the outcome record as JSON.
// Env: FIXTURE (claim-signature fixture), SIG, IDS, NOW_MS, LIVE (1 = live, own merge).
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const RES = join(HERE, '../../kubernetes/main/apps/upgrade-agent/shepherd/app/resources');
const C = await import(join(RES, 'night-claim.mjs'));
const L = await import(join(RES, 'lane-classify.mjs'));
const M = await import(join(RES, 'night-merge.mjs'));
const fx = JSON.parse(readFileSync(process.env.FIXTURE, 'utf8'));
const now = Number(process.env.NOW_MS || Date.parse(fx.now));
const run = (cmd, argv, o = {}) => L.runReadOnly(cmd, argv, { env: process.env, ...o });
const deps = {
  signature: () => ({ ok: true, sig: process.env.SIG, ids: JSON.parse(process.env.IDS || '[]'), pods: 'ok' }),
  read: () => M.readNamedConfigMap(C.STATE.name, run),
  write: (verb, manifest) => C.writeState(verb, manifest, process.env),
};
const rec = { ...fx.record, practice: process.env.LIVE !== '1' };
const out = C.claimStep({ rec, live: process.env.LIVE === '1', now, work: { ok: true, max_work_seconds: 10800 }, deps });
process.stdout.write(`${JSON.stringify(out)}\n`);
