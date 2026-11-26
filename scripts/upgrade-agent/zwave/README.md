# OP644 policy core

`plan.mjs` is a pure decision function, not an updater. It performs no I/O, imports
no adapter, starts no process and always returns `executable: false`. It is not
mounted by a CronJob. Run its offline tests with:

```
node --test scripts/upgrade-agent/zwave/plan.test.mjs
```

The source policy is `.agents/runbooks/zwave-firmware-updates.md` (OP643).
The existing Opus session retains the approved active OP642 batch. This module
must not be used to replay that historical manifest or take over its queue.

## Contract

A future trusted adapter supplies the manifest, state and current evidence.
An approval resolver must set `approvalVerified` from the approved named batch,
not from user-supplied JSON. Exact model/hardware/from/to must be established;
normalize device Version CC `2.50` to the manifest's `2.50.0` only with a documented,
model-specific equivalence. Missing facts fail closed.

Current driver/app/entity evidence is at most 60 seconds old. Historical battery
and scheduled-wake observations retain their own timestamps (24-hour maximum).
`driver.authoritative` cannot be inferred from Home Assistant `in_progress`.
`driver.requestKey` must identify the exact request recorded in state, not just
its node. A driver restart alone is not confirmation of a dropped waiting request.
A waiting-only drop must exclude any intervening transfer start, otherwise stop.

The pure core returns proposals, waiting, stop, receipt-recording or done. A stop
means report and leave the device/request untouched, never cancel or reset it.
Active transfers are left alone. App-upgrade uncertainty defers new starts;
there is no app hold. The timeout is conservatively anchored to the first start,
not reset by replacement requests. Three replacement starts are allowed, never
a fourth. No live manifest or approval record is shipped with these tests.

## Remaining integration gates (not implemented here)

1. Prove a driver observation contract capable of distinguishing idle, waiting,
   transferring, failed and completed, including restart gaps and request identity.
   Current human receipts do not constitute this contract.
2. Use the existing approval framework for named manifest authorization. Never
   accept an `approvalVerified` boolean from an untrusted caller as authorization.
3. Persist network-wide exclusive custody and a start intent atomically *before*
   `update.install`. If delivery is ambiguous, reconcile with the driver, never
   replay automatically. Persist counts/receipts across executor restarts and
   reject an older state revision; test concurrent callers and crash boundaries.
4. Read app-update PR/main/applied state through existing authorized access. No new
   access grant, secrets or broad RBAC is part of this slice.
5. Add a fake-server adapter integration suite, immutable receipt storage and
   one-day post-update checker verification before the supervised first live run.
6. Install only after review/CI and coordinator handoff, with no collision with
   the Opus-owned batch. A deployment rollback cannot undo device firmware.

OP644 remains incomplete until these gates and its live acceptance are met.
