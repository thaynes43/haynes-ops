# Repository overview & GitOps principles

This repository manages Kubernetes clusters that run on [Talos Linux](https://www.talos.dev/) by Sidero Labs. The Talos nodes are managed by Sidero Labs' [Omni](https://docs.siderolabs.com/omni/overview/what-is-omni). Use of `talosctl` is limited; `omnictl` handles cluster management and can read machine config that `talosctl` cannot (Omni gates it).

This repo follows patterns from the `Home Operations` Discord community. Most services are deployed with the [bjw-s-labs app-template](https://bjw-s-labs.github.io/helm-charts/docs/app-template/) chart rather than per-app charts.

Good reference `home-ops` repositories:
- [onedr0p/home-ops](https://github.com/onedr0p/home-ops) — single cluster
- [bjw-s-labs/home-ops](https://github.com/bjw-s-labs/home-ops) — single cluster
- [szinn/k8s-homelab](https://github.com/szinn/k8s-homelab) — multi-cluster
- [dmfrey/home-gitops](https://github.com/dmfrey/home-gitops) — useful reference for the rook-ceph v1.20 / ceph-csi-drivers layout

## Repo structure

Two clusters: `main` (production, in a home server rack) and `edge` (used to test re-architecting before it lands on `main`).

```
haynes-ops/
└── kubernetes/                # GitOps root
    ├── main/                  # 🏠 primary cluster
    │   ├── apps/              # app deployments by domain
    │   ├── bootstrap/         # Flux/Talos/Omni bootstrap
    │   └── flux/              # Flux config (cluster.yaml entrypoint)
    ├── edge/                  # 🌐 edge/test cluster (same shape)
    └── shared/                # 🔄 cross-cluster resources
        ├── components/        # reusable Kustomize components (volsync, gatus, common)
        └── repositories/      # shared OCI/Helm repositories
```

## Principles

- **GitOps strictly**: This repo is the source of truth. All *persistent* cluster changes are made via Git commits; Flux applies them.
- **No manual `kubectl apply`** for persistent changes, and never `kubectl patch` a Flux-managed resource to make a lasting change — edit the Git source and let Flux reconcile.
- **Repopulation capability**: The repo must stay able to bootstrap a cluster from scratch. Avoid state that lives only on the cluster and not in Git.

## Environment note (Claude Code)

Unlike the old Cursor/VSCode setup, **this environment can reach the cluster directly** — `kubectl`, `flux`, `task`, `talosctl`, and `omnictl` all work here (KUBECONFIG/TALOSCONFIG/OMNICONFIG are wired via `direnv`). Use them freely for **read-only inspection and verification** (get/describe/logs, `flux get`, `ceph -s`, etc.). Reserve mutations for the GitOps flow above; transient ops (cordon/uncordon, rollout restart, reconcile) are fine when they serve verification or remediation. `omnictl` auth uses Omni SideroV1 (`manofoz@gmail.com`) and may need a one-time browser CLI login.
