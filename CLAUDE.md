# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

A GitOps-managed home lab running two Kubernetes clusters (`main` and `edge`) on Talos Linux, orchestrated by Flux CD. Follows the "Home Operations" community patterns (see [onedr0p/home-ops](https://github.com/onedr0p/home-ops), [bjw-s-labs/home-ops](https://github.com/bjw-s-labs/home-ops)).

## Core Principles

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

For config changes that don't trigger a pod restart (e.g., EMQX CR config updates), verify the running config matches expectations by exec-ing into the pod or checking the operator's behavior.

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

## Safety Guardrails

- **PVC pruning risk**: Renaming/moving Flux `Kustomization` objects can trigger inventory pruning that deletes PVCs. Add `kustomize.toolkit.fluxcd.io/prune: disabled` annotation to protect stateful PVCs. See `.cursor/rules/flux-pvc-prune-safety.mdc`.
- **VolSync runbooks**: Restore and unlock procedures are documented in `.cursor/rules/volsync-restore-runbook.mdc` and `.cursor/rules/volsync-unlock-runbook.mdc`.

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
