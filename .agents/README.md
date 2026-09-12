# .agents

Agent-facing operational docs for this repo — runbooks, safety rules, and reference context for AI coding agents (Claude Code) and humans. Migrated from the old `.cursor/rules/` (Cursor is no longer used; environment-specific bits were updated for Claude Code, which reaches the cluster directly).

Top-level agent instructions live in [`CLAUDE.md`](../CLAUDE.md); this folder holds the longer-form material it references.

**[`HARDENING.md`](HARDENING.md) — safety defects found while doing other work.** Checks that
can pass without having run, docs that assert a safety property the cluster does not have, and
guards whose degraded state is fail-open. Append findings there as you hit them; a false green
is worth more attention than a red.

## runbooks/ — step-by-step procedures
- [plex-tonight.md](runbooks/plex-tonight.md) — refresh private Plex watch history and library evidence for movies, new shows, and series to resume.
- [audio-authoring.md](runbooks/audio-authoring.md) — independent CPU sound generation, model provisioning, asynchronous MCP jobs, and persistent candidates.
- [blender-authoring.md](runbooks/blender-authoring.md) — dedicated Blender MCP service, persistent artifacts, independent upgrades, GPU/audio follow-up, and initial agent registration.
- [talos-version-upgrade.md](runbooks/talos-version-upgrade.md) — bump Talos/Kubernetes via Omni, verify per node, and recover (re-image) a node that won't upgrade. Pairs with the gotchas reference below.
- [renovate-upgrade-batches.md](runbooks/renovate-upgrade-batches.md) — clear a backlog of Renovate update PRs by merging in risk-tiered batches (safe → infra → breaking → storage), reconciling and verifying after each.
- [volsync-restore.md](runbooks/volsync-restore.md) — VolSync (restic) PVC restore, including PVC-resize edge cases.
- [volsync-unlock.md](runbooks/volsync-unlock.md) — clearing stale restic repository locks.
- [omni-service-account.md](runbooks/omni-service-account.md) — headless, non-interactive cluster access via an Omni service account, so `kubectl`/`flux` work from anywhere without the browser OIDC login that expires on long sessions (and can only be renewed from home).
- [upgrade-shepherd.md](runbooks/upgrade-shepherd.md) — **Tier-4 upgrade-shepherd**: the master operating manual for the agent that takes the irreducible-manual Renovate upgrades off your hands (one agent, three modes: scheduled health gate / summoned remediation / breaking-change shepherd). The `haynes-ops-bot` identity, read-only-SA guardrails, the merge-one-at-a-time loop, the holds protocol, and the merge-order/rollback-risk table.
- [upgrade-health-gate.md](runbooks/upgrade-health-gate.md) — the cross-cutting post-reconcile health checks the scheduled gate (and the shepherd's per-merge verify) runs every cycle (Flux/pods/ESO/Ceph/HA-entities/Alertmanager), with healthy / benign-warn / regression criteria and the read-only run paths.
- [alert-responder.md](runbooks/alert-responder.md) — **Track-B1 "enrich, don't act"**: a CronJob that watches Alertmanager for `severity=critical` alerts and runs **one read-only Claude Code diagnosis in-pod** per new incident, paging a `CAUSE/EVIDENCE/FIX/RUNBOOK` summary to Pushover (priority 0) as a follow-up to Alertmanager's page. At-most-once state, own $15/mo spend envelope, no cluster write and no credential in the pod; the drill recipe, env knobs, and how it differs from the gate/triage pods (LLM enrichment for ANY critical, not just upgrades — deliberately decoupled).
- [agentic-remediation.md](runbooks/agentic-remediation.md) — **the `rem-*` lane**: the critical alerts that are handed to a machine instead of to Tom. A still-firing alert whose read-only diagnosis says `ACTION: urgent` becomes a headless, silent Claude Code session that fixes, verifies, and closes into the quiet digest; a page happens only when it cannot. The wiring (responder → `remediate.sh` order → `dev-env-ops` watcher → session), the four-case decision table, the close-out vocabulary, and the index of per-alert playbooks.
- [ceph-daemon-crash.md](runbooks/ceph-daemon-crash.md) — **`CephDaemonCrash`**, the first alert the `rem-*` lane owns end to end. Ceph holds `RECENT_CRASH` until a crash record is archived, so a daemon that recovered in seconds still re-pages every 12h for two weeks. The responder answers `urgent` (the handoff verb); the lane inspects each `ceph crash ls-new` id, verifies the daemon is serving and the PGs are clean, archives **per id**, and escalates a repeat signature, a daemon that stays down, or a crash loop.
- [kyverno-enforce-verify.md](runbooks/kyverno-enforce-verify.md) — **periodic (~monthly, and after adding any operator/app/StorageClass)** read-only check that Kyverno enforce isn't silently denying a legitimate ephemeral/controller-spawned pod (the OpenEBS-helper failure class). The always-on watch is the `kyverno-guardrail.rules` alerts; this is the manual deep-check + the "did the thing I just deployed hit an exception gap?" procedure.
- [known-noise-and-non-remediation.md](runbooks/known-noise-and-non-remediation.md) — **read before "fixing" an alert.** The cases where the obvious corrective action is wrong, and several where it makes things worse: slskd's 3h share rescan (restarting restarts the wait), force-deleting `Terminating` pods on a wedged node (two writers on one RWO volume → data corruption; escalate instead), the CNPG `postgres16-primary` PDB and the Ceph `AUTH_INSECURE_*` mutes that are deliberate rather than broken, and the qbittorrent-VPN node theory that was disproven twice. Written for the `rem-*` lane and the alert-responder, which run in a different pod with no shared memory — hence `runbooks/`, which is the only path `respond.sh` actually searches.
- [tier4-component-playbooks.md](runbooks/tier4-component-playbooks.md) — per-component upgrade knowledge for the manual-tier set (rook-ceph, cnpg, emqx, dragonfly, cilium, coredns, traefik, authentik, multus, device-plugins, talos, flux): the supporting `values` edits, health checks, and **adversarially-verified rollback** (break-glass steps flagged).
- [proxmox-access.md](runbooks/proxmox-access.md) — **Proxmox from the dev-env pod** (`pve` helper): the READ tier (exporter's PVEAuditor token) and the VM-scoped OPERATOR tier (onboot/start/stop/reset on the three Talos workers only, no HA config, no node shells), the "worker down after a PVE fence" remedy, and why the fence exists at all (Proxmox HA armed by five helper guests — incident 2026-09-09). Design: saga `dev-env` backlog 14.

## rules/ — must-follow safety guardrails
- [flux-pvc-prune-safety.md](rules/flux-pvc-prune-safety.md) — warn about Flux inventory pruning / PVC deletion when moving or renaming Kustomizations.

## reference/ — context & conventions
- [talos-omni-gotchas.md](reference/talos-omni-gotchas.md) — **big silent traps** in Talos/Omni node & network config: case-sensitive `deviceSelector` MACs, `install.extraKernelArgs` no-op under UKI, lost VM identity on wipe, tiny `/boot`, VPN-NIC egress in maintenance. Read before editing the Omni cluster template or upgrading.
- [repo-overview.md](reference/repo-overview.md) — Talos/Omni/Flux/home-ops context, repo structure, GitOps principles.
- [cluster-inspection.md](reference/cluster-inspection.md) — how to inspect the cluster safely (read-only, never dump Secrets).
- [talos-log-shipping.md](reference/talos-log-shipping.md) — Talos host/**service** logs → vector aggregator (LB VIP `192.168.40.212:6051`) → Loki, so a node hard-reset leaves a trace. What ships live now (machine-config, no reboot) vs. the reboot-gated **kernel/kmsg** follow-up (UKI kernel-arg via Omni `kernelArgs`, omni#2382 caveat).

## sagas/ — multi-plan initiatives
Long-running efforts with a vision doc + an enumerated plan backlog agents execute over many sessions. Conventions in [sagas/README.md](sagas/README.md).
- [dev-env](sagas/dev-env/README.md) — 24/7 in-cluster agent development environment (Claude Code / Codex / code-server workhorse pod; the Shepherd becomes a dispatcher).
