# Agentic remediation — the `rem-*` lane

**What it is.** A critical alert whose read-only diagnosis says `ACTION: urgent`
and which is still firing is *handed to a machine*, not paged: the
alert-responder files a `rem-responder-<sig8>` order, the `dev-env-ops` executor
runs a headless Claude Code session (Opus 5, `xhigh`, 40 min / 120 turns, no
Remote Control, nobody paged) that triages, fixes inside its containment,
verifies against the live condition, and closes silently into the quiet digest
(Pushover priority -1). Tom is paged only when the lane cannot fix or tried and
failed — and then with a joinable `esc-rem-<sig8>` session, not a summary.
North star (Tom, 2026-08-23): **page only when there is a problem he must solve.**

**Wiring** (all GitOps-managed under `kubernetes/main/apps/upgrade-agent/`):

| step | where |
|---|---|
| diagnosis → `ACTION: urgent` + still firing → handoff (fails open to a page) | `alert-responder/app/resources/respond.sh` |
| order filed in ConfigMap `upgrade-work-orders` (`rem-<source>-<sig8>`, `class: remediation`) | `health-gate/app/resources/remediate.sh` |
| watcher claims it (single-flight per lane, spawn order esc → wo → rem) and spawns the session | `dev-env-ops/app/resources/work-order-watch.sh` → `session-launch.sh` |
| the session's contract | `dev-env-ops/app/resources/ops-claude.md` → *The autonomous remediation contract* |
| close-out vocabulary (`done` silent · `working` heartbeat · `escalate` → esc-* + page · `failed` ⇒ escalate) | `dev-env-ops/app/resources/order-status.sh` |
| "is dev work causing this?" evidence | `dev-env-ops/app/resources/dev-activity-check.sh` (reads `declare-activity` records from the dev-env pod) |
| stale-lane watchdog (unclaimed 20 min / claimed 120 min → escalate) | `respond.sh` `rem_watchdog` |

## Decision table

| case | the session found | close-out |
|---|---|---|
| 1 | fixed, and the live condition re-queried clean | `done` — silent, digest line |
| 2 | nothing to fix: already self-healed, a [known-noise](known-noise-and-non-remediation.md) match, or dev-caused and harmless (a MATCHED `dev-activity-check` declaration **and** nothing actually broken) | `done` — name the entry / declaration in the note |
| 3 | needs something the lane cannot do: outside RBAC or egress, physical, a decision (bump vs hold) | `escalate` with the exact human steps |
| 4 | tried, and it did not hold | `escalate` with what was tried and the current state |

A declaration or a noise match can turn a case 4 into a case 2; it can never
suppress a case 3. A fix that cannot be verified is a failed fix — escalate it.

## Per-alert playbooks

Alert-specific procedures live beside this file in `.agents/runbooks/` — the one
directory the responder's shallow clone actually searches:

- [`ceph-daemon-crash.md`](ceph-daemon-crash.md) — `CephDaemonCrash`: verify the
  daemon is back and PGs are clean, archive the inspected crash ids, escalate
  on a repeat signature or a daemon that stays down.
- [`known-noise-and-non-remediation.md`](known-noise-and-non-remediation.md) —
  the cases where the obvious fix is wrong.

## What has actually been exercised

- 2026-08-23: a synthetic `rem-*` order end to end (#2569 wired the executor;
  #2572 added the missing responder call site — **test the deployed system,
  not the diff**). The session correctly called the synthetic order fake.
- 2026-09-11 `CephDaemonCrash`: the responder diagnosed it correctly but chose
  `investigate` (paged) because no runbook said the lane may act; the crash was
  archived by hand a day later. The `ceph-daemon-crash.md` playbook and the
  responder prompt clause that makes `urgent` the handoff verb for
  runbook-covered alerts date from that incident.
