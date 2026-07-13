# 10 — Taskfile overhaul

**Status:** backlog
**Depends on:** nothing — parallelizable with everything; land before/alongside 02 so
dev-env pod agents inherit a trustworthy task surface
**Origin:** Tom, 2026-07-13 — "Some work, we need an overhaul though." Most targets
are stale scaffolding from the original cluster-template bootstrap; CLAUDE.md's Key
Commands section still recommends them to every agent session.

## Problem

The task runner is the advertised entrypoint for repo operations, but it drifted from
the repo layout and can't be trusted:

- Confirmed broken: `kubernetes:kubeconform` (its script scans a nonexistent
  `kubernetes/flux` dir).
- Suspected stale: the template-era one-shots — `configure`, `init`,
  `repository:clean/reset/force-reset`, `workstation:*` — predate the current
  main/edge layout and Omni workflow.
- Agents (including future dev-env pod agents) follow CLAUDE.md into these targets
  and burn time debugging the *task* instead of their change.

## Scope

1. **Inventory** every target across `Taskfile.yaml` + `.taskfiles/{Edge,Flux,
   Kubernetes,Omni,Repository,Rook,Sops,Talos,Workstation,_scripts}`: classify
   WORKS / BROKEN / OBSOLETE (bootstrap-era, superseded by Omni or runbooks).
   Dry-read every script a target invokes — do not judge by `--list` descriptions.
2. **Delete** the obsolete tier (git history preserves the bootstrap scaffolding;
   the repopulation-capable principle is served by `bootstrap/` + runbooks, not dead
   tasks). Anything destructive-but-real (rook wipes, omni nuke) stays but gets a
   loud description prefix.
3. **Fix** the keep tier against the current layout (e.g. kubeconform script paths),
   pinning each target to the same command the runbooks/CLAUDE.md document.
4. **Align docs**: CLAUDE.md Key Commands + `.agents/` references list only
   still-real targets; note the overhaul in the section so agents stop assuming
   completeness.
5. **Guard against re-drift**: a CI smoke job (or flux-local sidecar step) that runs
   `task --list` + `task <target> --dry` for the keep tier where dry-run is
   meaningful, so a layout change that orphans a task fails visibly.

## Non-goals

- Rewriting working flows for style; this is a trust/correctness pass, not a rework.
- Migrating tasks into the dev-env dispatch API (that's saga plans 06/09 territory).

## Acceptance

- Every surviving `task --list` entry runs (or dry-runs) green against the current
  repo layout.
- CLAUDE.md/`.agents` reference no removed target.
- The agent-memory note about untrustworthy tasks can be retired.
