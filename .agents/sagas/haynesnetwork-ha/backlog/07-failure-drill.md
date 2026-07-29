# 07 — Node-failure drill + runbook (prove it, measure it)

**Status:** in progress — Phase A (pod-level) PASSED 2026-07-28, both scenarios zero-downtime:
[drill record](../drills/2026-07-28-phase-a-pod-kills.md). Phase B (a real node loss) remains
open and CLOSES OPPORTUNISTICALLY (owner ruling 2026-07-28): the owner cannot schedule reboots
right now, and this estate's nodes restart organically on their own anyway — so the NEXT organic
node restart (or an eventual owner reboot via Omni) is the drill. Closure procedure for any
session: compare `kubectl get nodes` boot times against this date, pull the Gatus
`external_haynesnetwork` history covering the restart window plus kube events, write the Phase B
record in `drills/`, and mark this plan done. Expected: zero to one red 1m check. NOTE the corrected traefik selector:
`app.kubernetes.io/instance=traefik-external-network`.
**Repo:** haynes-ops (runbook doc + drill execution; read-only on the app repo)
**Depends on:** 02 (replicas), 03 (measurement — the drill should be WATCHED by the SLI)
**Parallel with:** —

## Goal

Prove, on purpose and on the record, that the front page survives a node failure — and capture the
procedure so it can be re-run after any topology change.

## Why

An HA posture that has never been exercised is a hypothesis. The saga's promise is "drain or lose
any one node and watch the dashboards shrug" — this plan is where we watch.

## Approach (high level)

Rollout-order fact (from plan 06): `traefik-internal`'s Flux Kustomization has
`dependsOn: traefik-external` (external installs the shared traefik CRDs), so any drill step that
reconciles the edge serializes external-before-internal — script the drill accordingly.

Owner-scheduled window (this one is deliberately NOT fully autonomous — it perturbs the live
estate; get an explicit go in-session). Scripted sequence, observing Gatus + kubectl throughout:

1. Drain the node hosting one app replica → expect zero failed checks (PDB holds one serving).
2. Drain the node hosting a traefik-external replica + the Cilium L2 lease holder → expect
   at-most-blip (L2 re-announce + `Cluster` policy).
3. Simulated hard loss (talosctl reboot of a worker running one app replica, owner-approved) →
   expect the surviving replica to carry, the rescheduled pod to rejoin, Postgres unaffected
   (primary on a different node) or cnpg failover to carry if it is not.
4. Record timings + any red windows in the runbook; file follow-up backlog items for anything that
   flinched.

## Acceptance

- A dated drill record in this saga dir (or `docs/` runbook per repo convention) with measured
  user-visible impact per scenario, all within agreed blip budgets.
- Any surprises converted into new numbered backlog items rather than left as lore.
