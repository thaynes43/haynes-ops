# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

A GitOps-managed home lab running two Kubernetes clusters (`main` and `edge`) on Talos Linux, orchestrated by Flux CD. Follows the "Home Operations" community patterns (see [onedr0p/home-ops](https://github.com/onedr0p/home-ops), [bjw-s-labs/home-ops](https://github.com/bjw-s-labs/home-ops)).

## Core Principles

- **Make changes only when asked or authorized by the user.** Default to read-only. Investigation, searching, and read-only introspection (reading files, `kubectl get/logs/describe`, MCP queries, `flux get`) are always fine and encouraged. But **any change** — editing/creating files, `git commit`/`push`, cluster mutations (`kubectl apply/delete/annotate/rollout`, `flux reconcile` that applies new state, secret writes, pod restarts), or anything outward-facing — **requires an explicit request or clear authorization.** When in doubt, propose the change and wait for the go-ahead rather than acting.
- **When authorized, carry the task through to completion — don't re-pause at each step.** The read-only default above governs whether to *start*. Once the user gives an explicit or standing go-ahead for a task (e.g. "deploy this", "you own the whole flow, I don't need to approve anything"), that authorization covers the **entire chain the task implies** — commit, push, open/merge PRs, review CI + bot comments, verify the image, bump the tag, reconcile Flux, and verify the rollout — **end-to-end**. Do **not** stop to re-confirm each outward-facing sub-step; finish the job, then report what was done. Only pause again for a genuinely new decision, a destructive/irreversible action outside the task's scope, or a blocker you cannot resolve.
- **GitOps strictly**: This repo is the source of truth. All cluster changes go through Git commits — Flux applies them. No `kubectl apply` for persistent changes.
- **Repopulation capable**: The repo must be able to bootstrap clusters from scratch.
- **Never `kubectl patch` Flux-managed resources** to make lasting changes — always edit the Git source and let Flux reconcile.

## Repository Layout

```
kubernetes/
├── main/              # Production cluster
│   ├── apps/          # App deployments organized by domain (home-automation, database, media, etc.)
│   ├── bootstrap/     # Cluster bootstrap (Flux, Talos, Omni configs)
│   └── flux/          # Flux config: cluster.yaml (entrypoint), apps.yaml, vars/
├── edge/              # Edge/test cluster (same structure)
└── shared/            # Cross-cluster reusable resources
    ├── components/    # Kustomize components (volsync, gatus, common)
    └── repositories/  # Shared OCI/Helm repositories
```

Each app follows this structure: `kubernetes/{cluster}/apps/{domain}/{app-name}/ks.yaml` (Flux Kustomization) + `app/` dir containing `helmrelease.yaml`, `kustomization.yaml`, and supporting resources.

## Navigating the Live System

Prefer querying the running system over guessing — several MCP servers are wired in for **live, mostly read-only introspection**:

- **`home-assistant`** — inspect/manage HA entities, automations, scripts, logs, devices, ZHA/Z-Wave. Consult its skill (`skill://home-assistant-best-practices`) before editing HA config.
- **`grafana`** — PromQL/LogQL queries (`query_prometheus`, `query_loki_logs`), dashboards, alerts, datasources. Grafana runs on external CNPG Postgres (durable); SA token in 1Password `grafana` item. Use this to read cluster/app metrics + logs.
- **`mcp-unifi`** — read-only UniFi (UDM SE) introspection: clients, devices, RSSI, channel utilization, topology. **Gotcha:** `list_sites` returns a UUID, but per-site tools want the **legacy site code `default`** (the `internalReference` field), not the UUID.

Repo/cluster navigation tips:
- **Find an app's reconcile target:** open its `ks.yaml` → the Kustomization `name` + `namespace` → `flux reconcile kustomization <name> -n <namespace> --with-source`.
- **Shared building blocks** live in `kubernetes/shared/components/common/` (the bjw-s `app-template` OCIRepository, `gatus` health-check configmaps, `volsync`) — pulled into an app via `components:` in its `app/kustomization.yaml`.
- **Non-obvious operational history & gotchas** are captured in agent memory (`~/.claude/.../memory/`) and the runbooks under [`.agents/`](.agents/README.md) — check there before re-debugging recurring issues (re-image aftermath, HA backups, Grafana token, UniFi API keys, etc.).

## App Deployment Pattern

- Most apps use [bjw-s-labs app-template](https://bjw-s-labs.github.io/helm-charts/docs/app-template/) as their Helm chart
- `ks.yaml` defines the Flux `Kustomization` with `postBuild.substitute` variables (APP, VOLSYNC_CAPACITY, GATUS_SUBDOMAIN, etc.)
- Shared Kustomize components (`kubernetes/shared/components/`) provide reusable VolSync backup, Gatus health check, and alert configurations
- Secrets managed via SOPS (Age encryption) and External Secrets (1Password)
- Flux entrypoint: `kubernetes/{cluster}/flux/config/cluster.yaml` → `apps.yaml` → per-domain kustomizations → per-app `ks.yaml`

## Change Workflow

All persistent cluster changes follow this GitOps workflow: edit files → commit → push → reconcile → verify.

### 1. Commit and push

```bash
git add <files>
git commit -m "description"
git push origin main
```

**SSH key note**: SSH keys are managed via 1Password. The first `git push` in a new terminal session requires the user to approve the key interactively. If push fails with "communication with agent failed" or "Permission denied (publickey)", ask the user to approve and retry — do not troubleshoot SSH config.

### 2. Reconcile Flux

**Full reconcile** (pulls latest Git and reconciles everything from the top-level `cluster` kustomization down):
```bash
task flux:reconcile
# Runs: flux reconcile --namespace flux-system kustomization cluster --with-source
```

**Targeted reconcile** (faster — only reconciles a specific app's kustomization):
```bash
# Find the kustomization name and namespace from the app's ks.yaml, then:
flux reconcile kustomization <ks-name> -n <ks-namespace> --with-source
# Example: flux reconcile kustomization emqx-cluster -n database --with-source
```

### 3. Monitor reconciliation

Watch the specific kustomization until it shows `Ready=True` with a revision matching your commit:
```bash
flux get kustomization <ks-name> -n <ks-namespace> -w
# Or watch all kustomizations:
scripts/watchflux.sh
```

### 4. Verify changes took effect

Check that the affected pods/resources updated and are healthy:
```bash
# Check pod status for the affected app
kubectl get pods -n <namespace> -l app.kubernetes.io/name=<app>
# Check logs for errors
kubectl logs -n <namespace> <pod> --tail=50
# Broad health check (all namespaces)
scripts/checkHealth.sh
```

**Cascading restarts**: Operator-managed resources (EMQX CR, Rook CephCluster, etc.) may restart pods when their config changes. Check whether dependent apps need a restart too. For example, changing EMQX config restarts the broker pod, which clears in-flight MQTT state — downstream clients like Zigbee2MQTT may need a pod restart to re-publish retained messages.

## AppDaemon Deploys (cross-repo: `../hass-sandbox` → this repo)

AppDaemon (`kubernetes/main/apps/home-automation/appdaemon`) runs a Docker image built from the **separate `hass-sandbox` repo** (`ghcr.io/thaynes43/appdaemon`). A change spans both repos. When authorized, drive the whole chain to completion without pausing between steps:

1. **hass-sandbox** — branch, edit `appdaemon/`, bump `VERSION` (semver; **compare against `main` first** — `git show main:VERSION` — don't double-bump), run tests (`source .venv/bin/activate && cd appdaemon && python -m pytest tests/ -q`). Per its `AGENTS.md`, PRs are created `--draft`.
2. **Open + review** — `gh pr create --draft`, then `gh pr ready <N>` to trigger the `Claude Code Review` bot (**it only fires on `ready_for_review` and skips drafts**). Watch CI (`gh pr checks <N> --watch`: `test`, `build-and-push`, `docs-build`), read the bot review + docs audits, address anything actionable.
3. **Merge** — `gh pr merge <N> --squash --delete-branch`. Merge to `main` runs `build-and-push`, which pushes `ghcr.io/thaynes43/appdaemon:<VERSION>`.
4. **Verify the image** — anon GHCR pull token → `GET https://ghcr.io/v2/thaynes43/appdaemon/manifests/<VERSION>` returns `200` (`gh`'s token lacks `read:packages`; use the registry API).
5. **This repo** — bump `tag:` in `appdaemon/app/helmrelease.yaml`, commit **only that file** (the working tree often carries unrelated WIP), `git push origin main` (rebase if Renovate advanced `main` while you worked).
6. **Reconcile + verify** — `flux reconcile kustomization appdaemon -n home-automation --with-source`, then `kubectl rollout status deploy/appdaemon -n home-automation` and confirm the pod runs `:<VERSION>`. For health-check changes, confirm behavior in logs: `kubectl logs -n home-automation -l app.kubernetes.io/name=appdaemon | grep -i <checker>`.

Note: prod apps in `apps-prod.yaml` carry `disable: true`; **the image build strips it**, so they are enabled in the deployed image (don't be misled into thinking a prod checker is off).

## Key Commands (Task runner)

```bash
task --list                          # List all available tasks
task flux:reconcile                  # Force Flux to pull latest from Git (full reconcile)
task flux:bootstrap                  # Bootstrap Flux into a cluster
task kubernetes:kubeconform          # Validate manifests with kubeconform
task kubernetes:apply-ks CLUSTER=main PATH=home-automation/home-assistant  # Apply a single app's Kustomization
task kubernetes:sync-secrets CLUSTER=main  # Force-sync all ExternalSecrets
task kubernetes:browse-pvc CLUSTER=main NS=media CLAIM=plex  # Browse a PVC interactively
task sops:encrypt                    # Encrypt all SOPS files
task rook:*                          # Rook/Ceph disk operations
```

## CI/CD

- **PR validation**: `flux-local` runs on PRs touching `kubernetes/` — tests and diffs both `main` and `edge` clusters
- **Renovate**: Auto-updates container images, Helm charts, and GitHub Actions on weekends; ignores SOPS files and bootstrap dirs

## Agent docs (`.agents/`)

Longer-form runbooks, safety rules, and reference context live in [`.agents/`](.agents/README.md) (migrated from the retired `.cursor/rules/`). Consult these when the task matches:

- **Runbooks** — [`.agents/runbooks/`](.agents/runbooks/): [talos-version-upgrade](.agents/runbooks/talos-version-upgrade.md) (bump Talos/k8s via Omni, verify per node, re-image a node that won't upgrade), [renovate-upgrade-batches](.agents/runbooks/renovate-upgrade-batches.md) (merge Renovate PR backlogs in risk-tiered batches), [volsync-restore](.agents/runbooks/volsync-restore.md), [volsync-unlock](.agents/runbooks/volsync-unlock.md).
- **Rules** — [`.agents/rules/`](.agents/rules/): [flux-pvc-prune-safety](.agents/rules/flux-pvc-prune-safety.md).
- **Reference** — [`.agents/reference/`](.agents/reference/): [talos-omni-gotchas](.agents/reference/talos-omni-gotchas.md) (silent Talos/Omni node & network traps — read before editing the Omni cluster template or upgrading), [repo-overview](.agents/reference/repo-overview.md), [cluster-inspection](.agents/reference/cluster-inspection.md).

## Safety Guardrails

- **PVC pruning risk**: Renaming/moving Flux `Kustomization` objects can trigger inventory pruning that deletes PVCs. Add `kustomize.toolkit.fluxcd.io/prune: disabled` annotation to protect stateful PVCs. See [`.agents/rules/flux-pvc-prune-safety.md`](.agents/rules/flux-pvc-prune-safety.md).
- **VolSync runbooks**: Restore and unlock procedures are in [`.agents/runbooks/volsync-restore.md`](.agents/runbooks/volsync-restore.md) and [`.agents/runbooks/volsync-unlock.md`](.agents/runbooks/volsync-unlock.md).
- **Upgrading Renovate PRs**: Work through update-PR backlogs in risk-tiered batches per [`.agents/runbooks/renovate-upgrade-batches.md`](.agents/runbooks/renovate-upgrade-batches.md).
- **Talos/Omni node & network config (SILENT traps)**: Before editing `kubernetes/main/bootstrap/omni/cluster-template.yaml` or running a Talos/k8s upgrade, read [`.agents/reference/talos-omni-gotchas.md`](.agents/reference/talos-omni-gotchas.md). The big ones, each of which fails *silently* (no error, `lastconfigerror` empty):
  - **`deviceSelector.hardwareAddr` is case-sensitive — MACs MUST be lowercase.** An uppercase MAC matches nothing, so the whole interface block (dhcp/addresses/routes/routeMetric/vip) is silently ignored. This was the real cause of the "worker egress out the VPN NIC" bug. Fix applies live via `task omni:sync` — no reboot.
  - **`machine.install.extraKernelArgs` is a no-op under UKI** (`talosctl get securitystate` → `bootedWithUKI: true`). Use the Omni-native per-machine `kernelArgs` field instead (rebuilds the boot image, reboots — not a wipe; don't duplicate an arg already in the cmdline → reboot loop, omni#2382).
  - **Wiping a VM loses its Omni identity** (META partition) → re-registers as a new machine. Pin `qm set <vmid> --smbios1 uuid=<original-machine-id>` before re-imaging.
  - **Re-imaging** (node that won't upgrade, e.g. `/boot` too small for the nvidia initramfs) follows [`.agents/runbooks/talos-version-upgrade.md`](.agents/runbooks/talos-version-upgrade.md) — bake `net.ifnames=0` + extensions into the image; resizing the VM disk does NOT enlarge `/boot`.

## Environment Setup

Managed via `direnv` (`.envrc`):
- `KUBECONFIG` → `./kubeconfig` (main) / `./kubeconfig-edge` (edge)
- `SOPS_AGE_KEY_FILE` → `./age.key`
- `TALOSCONFIG` / `OMNICONFIG` → bootstrap dirs
- Python venv at `.venv/` (for `makejinja` templating)

## Infrastructure Stack

- **OS**: Talos Linux (immutable), managed via Omni (not raw talosctl)
- **CNI**: Cilium
- **Ingress**: Traefik
- **Storage**: Rook/Ceph (distributed), OpenEBS (local hostpath)
- **Backups**: VolSync with restic to S3
- **SSO**: Authentik
- **DNS**: External DNS + Cloudflare DDNS/Tunnel
- **Monitoring**: Prometheus stack, Grafana, Loki, Gatus
