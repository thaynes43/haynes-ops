# Repository overview & GitOps principles

This repository manages Kubernetes clusters that run on [Talos Linux](https://www.talos.dev/) by Sidero Labs. The Talos nodes are managed by Sidero Labs' [Omni](https://docs.siderolabs.com/omni/overview/what-is-omni). Use of `talosctl` is limited; `omnictl` handles cluster management and can read machine config that `talosctl` cannot (Omni gates it).

This repo follows patterns from the `Home Operations` Discord community. Most services are deployed with the [bjw-s-labs app-template](https://bjw-s-labs.github.io/helm-charts/docs/app-template/) chart rather than per-app charts.

Good reference `home-ops` repositories:
- [onedr0p/home-ops](https://github.com/onedr0p/home-ops) — single cluster
- [bjw-s-labs/home-ops](https://github.com/bjw-s-labs/home-ops) — single cluster
- [szinn/k8s-homelab](https://github.com/szinn/k8s-homelab) — multi-cluster
- [dmfrey/home-gitops](https://github.com/dmfrey/home-gitops) — useful reference for the rook-ceph v1.20 / ceph-csi-drivers layout

## Repo structure

One cluster: `main` (production, in a home server rack). The `edge` proving-ground
cluster was retired on 2026-09-22 — see *Edge cluster retirement* below.

```
haynes-ops/
└── kubernetes/                # GitOps root
    ├── main/                  # 🏠 the cluster
    │   ├── apps/              # app deployments by domain
    │   ├── bootstrap/         # Flux/Talos/Omni bootstrap
    │   └── flux/              # Flux config (cluster.yaml entrypoint)
    └── shared/                # 🔄 reusable resources
        ├── components/        # reusable Kustomize components (volsync, gatus, common)
        └── repositories/      # shared OCI/Helm repositories
```

`kubernetes/shared/` keeps its shared shape even with one cluster: apps pull the
components from there via `components:`, and a future second cluster inherits them.

## Principles

- **GitOps strictly**: This repo is the source of truth. All *persistent* cluster changes are made via Git commits; Flux applies them.
- **No manual `kubectl apply`** for persistent changes, and never `kubectl patch` a Flux-managed resource to make a lasting change — edit the Git source and let Flux reconcile.
- **Repopulation capability**: The repo must stay able to bootstrap a cluster from scratch. Avoid state that lives only on the cluster and not in Git.

## Edge cluster retirement (2026-09-22)

Tom retired the `edge` cluster outright. The nodes (`edgem01`-`03`, `edgew01`) were VMs
on the `pve01`-`03` Proxmox hosts, which are no longer in the PVE cluster; the SaaS Omni
lists only `haynes-ops`, and nothing matching the edge machines exists anywhere in the
fleet. This repo removed, in one PR:

- `kubernetes/edge/` in full (apps, `flux/`, `bootstrap/`, its README) and `kubeconfig-edge`
- the `edge` leg of the `flux-local` `test` + `diff` CI matrices, and the `edge` base
  branch from the two `diff-scope` workflow triggers
- the `kubernetes/edge/**` entries in `.github/renovate.json5` `ignorePaths` and in the
  `pr-disposition` RAMP_CLEAN prefixes
- the `edge` target in `scripts/switch-contexts.sh`

`kubernetes/shared/**` was left completely untouched: `kubernetes/edge/` referenced
nothing under it (verified by grep), so nothing there was edge-only.

**Leftovers outside this repo — Tom's to clear, none of them blocking:**

| Leftover | Where | Note |
|---|---|---|
| `haynes-edge` cluster object | self-hosted Omni (`frontend/omni` in the main cluster) | empty; logs `No control plane nodes are connected` and a failed etcd backup every 60s — 2,884 error/warn lines a day into Loki, all phantom. Destroy via the Omni UI → Clusters → `haynes-edge` → (Unlock Cluster, if shown) → Destroy Cluster. The dev-env pod's only Omni credential is the **SaaS** Reader service account, which cannot reach or write this instance (`invalid signature`). |
| `edge` git branch | GitHub `thaynes43/haynes-ops` | last commit 2025-09-18; it was the edge cluster's Flux `GitRepository` ref. Protected by a `deletion` rule, so the bot cannot delete it. |
| "Edge" repository ruleset (id 18431432) | GitHub repo rulesets | targets `refs/heads/edge` and requires `Diff Scope - Success`, which no longer runs on that branch. Delete the ruleset with the branch. Apps cannot edit rulesets. |
| DHCP reservations `edgem01/02/03` (192.168.40.6/7/8) | UniFi | free the addresses when convenient |
| VLAN 8 `RookEdgeLan` (192.168.80.0/24) | UniFi | existed only for edge's Rook replication traffic |

### Reaching the self-hosted Omni (rescued from the deleted edge bootstrap)

`kubernetes/edge/bootstrap/omni/haynes-edge-omniconfig.yaml` was the only file in the
repo that recorded how to reach the self-hosted instance, so its contents are kept here:

- URL `https://omni.haynesops.com` (UI/API). Also `api.omni.haynesops.com` (SideroLink
  machine API) and `kube.omni.haynesops.com` (Kubernetes proxy); WireGuard on the
  LoadBalancer IP `192.168.40.210:50180/UDP`.
- `omnictl` auth is **SideroV1** with identity **`admin@haynesnetwork.com`** — note this
  is *not* the SaaS identity (`manofoz@gmail.com`). Browser sign-in is SAML via
  Authentik. Enrolling the PGP key requires a browser, so there is no headless path from
  the dev-env pod.

**This instance manages nothing else.** `haynes-edge` is its only cluster object, with
zero machines; every real Talos node is on the SaaS Omni. Once `haynes-edge` is
destroyed it holds no clusters at all, which makes `frontend/omni` itself a decommission
candidate — it is the repo's most privileged workload (`privileged: true`, root,
NET_ADMIN/NET_RAW, a standing Kyverno PSS exception), holds ~20Gi Ceph + 12Gi hostpath,
backs an empty cluster's etcd to S3 nightly, and is the *only* consumer of
`generic-device-plugin`'s `devic.es/tun`. Removing it is a separate decision for Tom;
if it is done, remove `kubernetes/main/apps/frontend/omni/` **as a whole directory** so
the `omni-gatus-ep` health check goes with it — leaving that ConfigMap behind pages
Pushover ~10 minutes after the endpoint dies.

Branch protection on `main` requires exactly two aggregate status contexts —
`Flux Local - Success` and `Diff Scope - Success` — and never an individual matrix leg,
which is why dropping the edge matrix entry did not strand PRs on a missing check. Keep
it that way: a required check named after a matrix cell would block every future PR the
moment that cell is removed.

## Environment note (Claude Code)

Unlike the old Cursor/VSCode setup, **this environment can reach the cluster directly** — `kubectl`, `flux`, `task`, `talosctl`, and `omnictl` all work here (KUBECONFIG/TALOSCONFIG/OMNICONFIG are wired via `direnv`). Use them freely for **read-only inspection and verification** (get/describe/logs, `flux get`, `ceph -s`, etc.). Reserve mutations for the GitOps flow above; transient ops (cordon/uncordon, rollout restart, reconcile) are fine when they serve verification or remediation. `omnictl` auth uses Omni SideroV1 (`manofoz@gmail.com`) and may need a one-time browser CLI login.
