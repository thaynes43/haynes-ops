# .agents

Agent-facing operational docs for this repo — runbooks, safety rules, and reference context for AI coding agents (Claude Code) and humans. Migrated from the old `.cursor/rules/` (Cursor is no longer used; environment-specific bits were updated for Claude Code, which reaches the cluster directly).

Top-level agent instructions live in [`CLAUDE.md`](../CLAUDE.md); this folder holds the longer-form material it references.

## runbooks/ — step-by-step procedures
- [renovate-upgrade-batches.md](runbooks/renovate-upgrade-batches.md) — clear a backlog of Renovate update PRs by merging in risk-tiered batches (safe → infra → breaking → storage), reconciling and verifying after each.
- [volsync-restore.md](runbooks/volsync-restore.md) — VolSync (restic) PVC restore, including PVC-resize edge cases.
- [volsync-unlock.md](runbooks/volsync-unlock.md) — clearing stale restic repository locks.

## rules/ — must-follow safety guardrails
- [flux-pvc-prune-safety.md](rules/flux-pvc-prune-safety.md) — warn about Flux inventory pruning / PVC deletion when moving or renaming Kustomizations.

## reference/ — context & conventions
- [repo-overview.md](reference/repo-overview.md) — Talos/Omni/Flux/home-ops context, repo structure, GitOps principles.
- [cluster-inspection.md](reference/cluster-inspection.md) — how to inspect the cluster safely (read-only, never dump Secrets).
