# .agents

Agent-facing operational docs for this repo — runbooks, safety rules, and reference context for AI coding agents (Claude Code) and humans. Migrated from the old `.cursor/rules/` (Cursor is no longer used; environment-specific bits were updated for Claude Code, which reaches the cluster directly).

Top-level agent instructions live in [`CLAUDE.md`](../CLAUDE.md); this folder holds the longer-form material it references.

## runbooks/ — step-by-step procedures
- [talos-version-upgrade.md](runbooks/talos-version-upgrade.md) — bump Talos/Kubernetes via Omni, verify per node, and recover (re-image) a node that won't upgrade. Pairs with the gotchas reference below.
- [renovate-upgrade-batches.md](runbooks/renovate-upgrade-batches.md) — clear a backlog of Renovate update PRs by merging in risk-tiered batches (safe → infra → breaking → storage), reconciling and verifying after each.
- [volsync-restore.md](runbooks/volsync-restore.md) — VolSync (restic) PVC restore, including PVC-resize edge cases.
- [volsync-unlock.md](runbooks/volsync-unlock.md) — clearing stale restic repository locks.
- [omni-service-account.md](runbooks/omni-service-account.md) — headless, non-interactive cluster access via an Omni service account, so `kubectl`/`flux` work from anywhere without the browser OIDC login that expires on long sessions (and can only be renewed from home).
- [upgrade-shepherd.md](runbooks/upgrade-shepherd.md) — **Tier-4 upgrade-shepherd**: the master operating manual for the agent that takes the irreducible-manual Renovate upgrades off your hands (one agent, three modes: scheduled health gate / summoned remediation / breaking-change shepherd). The `haynes-ops-bot` identity, read-only-SA guardrails, the merge-one-at-a-time loop, the holds protocol, and the merge-order/rollback-risk table.
- [upgrade-health-gate.md](runbooks/upgrade-health-gate.md) — the cross-cutting post-reconcile health checks the scheduled gate (and the shepherd's per-merge verify) runs every cycle (Flux/pods/ESO/Ceph/HA-entities/Alertmanager), with healthy / benign-warn / regression criteria and the read-only run paths.
- [tier4-component-playbooks.md](runbooks/tier4-component-playbooks.md) — per-component upgrade knowledge for the manual-tier set (rook-ceph, cnpg, emqx, dragonfly, cilium, coredns, traefik, authentik, multus, device-plugins, talos, flux): the supporting `values` edits, health checks, and **adversarially-verified rollback** (break-glass steps flagged).

## rules/ — must-follow safety guardrails
- [flux-pvc-prune-safety.md](rules/flux-pvc-prune-safety.md) — warn about Flux inventory pruning / PVC deletion when moving or renaming Kustomizations.

## reference/ — context & conventions
- [talos-omni-gotchas.md](reference/talos-omni-gotchas.md) — **big silent traps** in Talos/Omni node & network config: case-sensitive `deviceSelector` MACs, `install.extraKernelArgs` no-op under UKI, lost VM identity on wipe, tiny `/boot`, VPN-NIC egress in maintenance. Read before editing the Omni cluster template or upgrading.
- [repo-overview.md](reference/repo-overview.md) — Talos/Omni/Flux/home-ops context, repo structure, GitOps principles.
- [cluster-inspection.md](reference/cluster-inspection.md) — how to inspect the cluster safely (read-only, never dump Secrets).
