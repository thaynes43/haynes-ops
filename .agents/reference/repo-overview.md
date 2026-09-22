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
| `haynes-edge` cluster object | ~~self-hosted Omni~~ | **GONE** — it lived only in the self-hosted Omni's etcd, which was deleted with that app on 2026-09-22 (below). No UI step needed. |
| `edge` git branch | GitHub `thaynes43/haynes-ops` | last commit 2025-09-18; it was the edge cluster's Flux `GitRepository` ref. Protected by a `deletion` rule, so the bot cannot delete it. |
| "Edge" repository ruleset (id 18431432) | GitHub repo rulesets | targets `refs/heads/edge` and requires `Diff Scope - Success`, which no longer runs on that branch. Delete the ruleset with the branch. Apps cannot edit rulesets. |
| DHCP reservations `edgem01/02/03` (192.168.40.6/7/8) | UniFi | free the addresses when convenient |
| VLAN 8 `RookEdgeLan` (192.168.80.0/24) | UniFi | existed only for edge's Rook replication traffic |

Branch protection on `main` requires exactly two aggregate status contexts —
`Flux Local - Success` and `Diff Scope - Success` — and never an individual matrix leg,
which is why dropping the edge matrix entry did not strand PRs on a missing check. Keep
it that way: a required check named after a matrix cell would block every future PR the
moment that cell is removed.

## Self-hosted Omni decommission (2026-09-22)

Same day, immediately after the edge retirement above: **the self-hosted Omni
(`kubernetes/main/apps/frontend/omni/`, `https://omni.haynesops.com`, ns `frontend`) is
gone.** With edge retired it managed zero clusters and zero machines — its only tenant
was the empty `haynes-edge` object, which logged ~2,900 phantom error/warn lines a day
into Loki. It was also the repo's most privileged workload (`privileged: true`, root,
NET_ADMIN/NET_RAW, a standing Kyverno PSS exception), held ~20Gi Ceph + 12Gi hostpath,
and backed an empty cluster's etcd to S3 nightly. Verified before removal: zero
connected machines (`kubectl logs -n frontend deploy/omni` showed only
`ControlPlaneStatusController ... No control plane nodes are connected`).

**Omni is now SaaS-only.** `haynes.omni.siderolabs.io` /
`haynes.na-west-1.omni.siderolabs.io` (identity `manofoz@gmail.com`) manages every real
Talos node, and `kubernetes/main/bootstrap/omni/` + `.taskfiles/Omni/` are untouched and
still correct. The old self-hosted endpoints (`omni.haynesops.com`,
`api.omni.haynesops.com`, `kube.omni.haynesops.com`, WireGuard on
`192.168.40.210:50180/UDP`) and its separate SideroV1 identity
(`admin@haynesnetwork.com`, SAML via Authentik) no longer exist — do not send anyone
there.

Removed in one PR:

- `kubernetes/main/apps/frontend/omni/` **as a whole directory** and its entry in
  `kubernetes/main/apps/frontend/kustomization.yaml`. Whole-directory is the rule here:
  the `gatus/gaurded` component renders `omni-gatus-ep` from that same path, and leaving
  that ConfigMap behind pages Pushover ~10 minutes after the endpoint dies.
- `kubernetes/main/apps/kube-system/generic-device-plugin/` (+ its `kustomization.yaml`
  entry, its `pss-baseline` exception name, and its path in `pr-disposition.yml`'s
  `RAMP_CLEAN` and the shepherd's `device-plugins` ramp glob). Omni was the **only**
  consumer of its `devic.es/tun`; nothing else in `kubernetes/` referenced it.
- The `frontend`/`omni*` block in the Kyverno `pss-baseline-privileged-infra`
  PolicyException.
- `certificate-omni-prod.yaml` (four certs: `api`/`kube.omni.haynesnetwork.com` +
  `api`/`kube.omni.haynesops.com`) and its `kustomization.yaml` entry.
- `https://omni.haynesops.com` from the Authentik CSP `form-action` allowlist
  (`*.haynesops.com` already covered it, so no other app changed).
- The dev-env CNP's `omni.frontend.svc.cluster.local` DNS entry and its in-cluster
  egress rule to `frontend`/`omni:8080`.

**Left behind on purpose / outside git:**

| Leftover | Where | Note |
|---|---|---|
| PVC `omni` (10Gi `ceph-block`, ns `frontend`) + its PV | main cluster | The shared `volsync/aws` component stamps `kustomize.toolkit.fluxcd.io/prune: disabled` on every `${APP}` claim, so Flux does **not** prune it — by design, and the reason no other app's data is ever lost to a Kustomization removal. Delete by hand when the S3 backup is no longer wanted: `kubectl delete pvc omni -n frontend`. dev-env cannot do it (its PVC-delete RBAC is scoped to `database`). |
| restic repo `<REPOSITORY_TEMPLATE>/omni` in S3 bucket `volsync-hayesops` | AWS | Nightly VolSync backups of the (now deleted) Omni etcd; `REPOSITORY_TEMPLATE` is the `volsync-hayesops` 1Password item. Intentionally NOT purged — purge from the AWS console/`restic forget` when the data is definitively unwanted. |
| Authentik SAML app + provider **"Omni"** (slug `omni`, provider pk 74) | Authentik UI/DB | UI-created, never a blueprint — nothing in `app/blueprints/` references it, so git cannot remove it. Delete in Authentik → Applications → `Omni` (and Providers → `Provider for Omni`). `exports/applications.json` is a dated 2026-07-10 read-only snapshot and is deliberately left as-is. |
| UniFi local DNS record for `omni.haynesops.com`, if one exists | UniFi | The IngressRoute's external-dns annotation is gone with it, so the automated record is removed automatically; only a hand-made UniFi entry would linger. |
| LoadBalancer IP `192.168.40.210` | Cilium LB-IPAM pool | Released back to the pool when the `omni-wireguard` Service was pruned. No pool edit was needed (the IP was requested via `lbipam.cilium.io/ips`, not reserved in the pool spec). |

The three `volsync-*-omni-*` PVCs (8Gi + 10Gi + 4Gi) carry owner references to the
`ReplicationSource`/`ReplicationDestination`, so they cascade-deleted with those objects
and needed no manual step.

## Environment note (Claude Code)

Unlike the old Cursor/VSCode setup, **this environment can reach the cluster directly** — `kubectl`, `flux`, `task`, `talosctl`, and `omnictl` all work here (KUBECONFIG/TALOSCONFIG/OMNICONFIG are wired via `direnv`). Use them freely for **read-only inspection and verification** (get/describe/logs, `flux get`, `ceph -s`, etc.). Reserve mutations for the GitOps flow above; transient ops (cordon/uncordon, rollout restart, reconcile) are fine when they serve verification or remediation. `omnictl` auth uses Omni SideroV1 (`manofoz@gmail.com`) and may need a one-time browser CLI login.
