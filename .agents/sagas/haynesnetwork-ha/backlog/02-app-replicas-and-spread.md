# 02 — App replicas: 3 + topology spread + PDB

**Status:** done — [PR #2282](https://github.com/thaynes43/haynes-ops/pull/2282). replicas 3 on 3
distinct nodes (talosm01/talosm03/talosw01), native app-template 5.0.1 per-controller PDB
minAvailable 2, hostname topologySpread scoped by `controller: main` so CronJob pods are excluded.
Zero gatus red during the rollout; the three concurrent migrate init-containers serialized cleanly
on the plan-01 advisory lock.
**Repo:** haynes-ops (`kubernetes/main/apps/frontend/haynesnetwork/app/helmrelease.yaml`)
**Depends on:** 01 (soft — a plain 1→2 bump is safe without it; cold multi-pod starts are not)
**Parallel with:** —

## Goal

The front page survives the loss of any single node with zero-to-blip user-visible downtime.

## Why

`haynesnetwork-main` is 1 replica on talosw01 — the tightest SPOF on the whole serving path. The
statelessness audit (saga README, Current state) found no correctness blockers for 2+ replicas:
DB-backed sessions, no filesystem state, background work confined to CronJobs, connection budget
trivial (10/pod vs shared max 400; three replicas = 30).

## Approach (high level)

In the app-template values: `replicas: 3` (owner ruling, decision 2); a
`topologySpreadConstraints` on hostname (`maxSkew: 1`, `whenUnsatisfiable: DoNotSchedule`) so the
three replicas land on three distinct nodes; a `PodDisruptionBudget` `minAvailable: 2` so drains
keep two serving. Verify the rollout serializes
migrations (maxSurge math), then kubectl-watch a drain of the node hosting one replica while
curling the apex. CronJobs are untouched (single-writer by `concurrencyPolicy: Forbid`).

Known accepted skew: per-pod memo caches (ADR-068 C-05) may differ across replicas at household
scale — no action.

## Acceptance

- 3 pods on 3 distinct nodes; PDB active; rolling deploy stays green.
- Drain of either hosting node: apex keeps answering (Gatus shows no red once 03 lands).
- The deploy runbook's rollout-watch section still holds (no runbook drift).
