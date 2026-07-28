# 07 — Node-failure drill + runbook (prove it, measure it)

**Status:** planned
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
