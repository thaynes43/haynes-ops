# 10 — Taskfile overhaul

**Status:** scope items 1-4 DONE (2026-09-22 audit); item 5 (re-drift CI guard) still open

## 2026-09-22 — what landed

Tom ordered the audit directly ("a lot of those TaskFiles are a mess — audit everything
and delete what's not needed, fix what is"). The task surface went from 40 targets to 22.

- **Deleted** (cluster-template scaffolding, git history keeps it): `init`, `configure`
  (+ the internal `.template` / `.validate`), the whole `repository:` namespace
  (`clean` needed a `bootstrap/` dir that no longer exists; `reset`/`force-reset` ran
  `rm -rf kubernetes/` and `git reset --hard` on a live GitOps repo), the whole `talos:`
  namespace (talhelper-based, `talconfig.yaml` absent, and raw `talosctl upgrade` fights
  Omni for the machine config), the whole `edge:` namespace (cluster decommissioned),
  `flux:apply` (pointed at the pre-multi-cluster `kubernetes/apps/`, superseded by
  `kubernetes:apply-ks`), `flux:github-deploy-key` (its sops file is gone and the
  GitRepository is public HTTPS with no secretRef), `rook:wipe-node-example`, and the
  dangling optional `user:` include.
- **Rescued**: `talos:install-helm-apps` was the only target running
  `kubernetes/main/bootstrap/helmfile.yaml` and is a real bring-up step, so it moved to
  `flux:install-helm-apps` rather than dying with the namespace.
- **Fixed**: `kubernetes:apply-ks` had three bugs (undefined `CLUSTER_DIR` → empty path;
  `flux get` exiting 1 aborting the task before the `not found` → `--dry-run` branch
  could fire; `NS` defaulting to `flux-system` when every app ks.yaml declares its own
  namespace, so the yq step rewrote it). `.envrc` pointed `TALOSCONFIG`/`OMNICONFIG` at
  `kubernetes/bootstrap/` instead of `kubernetes/main/bootstrap/`.
- **Guarded**: `omni:nuke` and the three `rook:wipe-disks-*` (plus the new generic
  `rook:wipe-node`) carry `prompt:` + a `!! DESTRUCTIVE !!` desc prefix. go-task refuses a
  prompted task outright without a TTY, so scripts and tab-completion slips cannot fire them.
- **Docs aligned**: CLAUDE.md Key Commands + Environment Setup, `docs/cluster/index.md`
  bring-up sequence, `kubernetes/edge/README.md`.

**Still open — scope item 5 (re-drift guard).** Nothing stops the next layout change from
orphaning a target again. The obstacle is that most targets cannot dry-run in CI: `--dry`
still evaluates `preconditions:` and `vars: sh:`, so `omni:*` needs omnictl creds,
`sops:*`/`flux:bootstrap` need `age.key`, `workstation:*` needs brew/paru, and
`kubernetes:apply-ks` needs a reachable cluster. Only `kubernetes:resources`,
`flux:reconcile`, `flux:install-helm-apps` and the `rook:wipe-*` targets dry-run clean with
no credentials. A useful job is therefore `task --list` (must exit 0) plus `--dry` on that
short list, plus a static check that every path a taskfile references exists — the third
part is the one that actually catches drift, and it needs deciding whether to hand-maintain
the path list or resolve the go-task templating. Trigger on `Taskfile.yaml` +
`.taskfiles/**`.

---

**Status (original):** backlog
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
