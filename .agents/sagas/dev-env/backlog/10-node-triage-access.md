# 10 — node-triage access (read-only talosctl + omnictl for the pod)

**Status:** in progress — split across **PR A** (mergeable now, zero pod-rollout) and
**PR B** (DRAFT, deploys the creds; rolls the pod, so a human/outside-agent merges it
at a chosen moment). See "Split & handoff" below.
**Depends on:** 04 (ExternalSecret/1Password auth pattern), 05 (RBAC read-tier precedent)
**Parallel with:** nothing

## Goal

Give the dev-env pod a **read-only node-triage surface** so an agent can investigate
hardware/kernel failures across the Talos nodes **from inside the pod**, without
`kubectl exec` on a workstation and without holding any mutating node/Omni credential.

Concretely: `talosctl` (dmesg, kernel/service logs, machine resources, version,
health) against each node, plus `omnictl` (machine/cluster status + events) against
Omni — both gated to **read-only** by the credential's own role, not by trust in the
agent.

## Motivation

The masters `talosm01-03` are **bare metal**. `talosm02` hard-reset twice (2026-07-18,
2026-07-24) and the pod had **no way to read `dmesg` / machine events** to triage why —
that data lives behind the Talos API (apid, tcp/50000 on each node) and behind Omni,
neither of which the pod could reach. Post-mortem of a bare-metal reset needs the
node's ring buffer and Talos service logs at the time, which are gone once you're only
looking at Kubernetes-level symptoms.

Talos apid enforces **RBAC by client-cert role** (`os:reader` / `os:operator` /
`os:admin`): an `os:reader` talosconfig can `dmesg`, `logs`, `get`, `version`, `health`
but **cannot** `reboot`, `reset`, `apply-config`, `upgrade`, or read secrets. That role
boundary is enforced server-side by every node, so a read-only talosconfig on the pod
is safe even under a fully compromised agent.

## Design

### talosctl — the workhorse (Omni-independent)

- A **`os:reader` talosconfig** minted by the admin (`talosctl config new … --roles
  os:reader`) from their existing admin config. The reader cert is signed by the
  cluster's Talos machine CA, so nodes accept it directly.
- talosctl connects **directly to node IPs** on the LAN (`192.168.40.0/24`, apid
  tcp/50000). The pod is on the same L2, so this needs no Omni round-trip — it works
  even when Omni is unreachable, which is exactly the failure mode we care about.
- Mounted read-only at `/creds/triage/talosconfig`; `TALOSCONFIG` points at it.

### omnictl — secondary (machine/cluster status from Omni's view)

- An **Omni service account with the `Reader` role** (see
  `.agents/runbooks/omni-service-account.md`), consumed as
  `OMNI_ENDPOINT` + `OMNI_SERVICE_ACCOUNT_KEY` env (omnictl's headless auth — no
  browser). The SA key comes from 1Password via an ExternalSecret.
- **Topology caveat (resolve before wiring omnictl):** the **main cluster's nodes are
  managed by SaaS Omni `https://haynes.omni.siderolabs.io`** (confirmed by
  `kubernetes/main/bootstrap/omni/haynes-ops-omniconfig.yaml`, the main kubeconfig
  server `haynes.kubernetes.na-west-1.omni.siderolabs.io`, and the omni-service-account
  runbook). The **self-hosted Omni at `omni.haynesops.com`** (app
  `kubernetes/main/apps/frontend/omni`, image `ghcr.io/siderolabs/omni:v1.9.3`) is a
  **separate instance** and is *not* what registers `talosm0x` today. So:
  - `OMNI_ENDPOINT` for main-cluster triage is `https://haynes.omni.siderolabs.io`,
    which requires CNP egress to `*.omni.siderolabs.io` — **not** the in-cluster svc.
  - The in-cluster Omni svc egress rule added in PR A (below) supports the
    self-hosted Omni (edge/lab/migration) and is harmless, but does **not** by itself
    make `omnictl` see the main nodes.
  - **Because talosctl fully covers the stated motivation, omnictl is optional.** If the
    SaaS-Omni exfil surface (`*.omni.siderolabs.io`) is unwanted, ship talosctl only and
    leave the Omni SA / omni egress for later. This decision is called out in the
    handoff so the human picks deliberately.

### CiliumNetworkPolicy egress (PR A — applies live, no pod restart)

Added to `kubernetes/main/apps/dev/dev-env/app/networkpolicy.yaml`:

- **Talos apid on the nodes:** `toEntities: [remote-node, host]` restricted to
  tcp/50000. The repo uses `toEntities` (never `toCIDR`) for cluster infra, and
  `policy-cidr-match-mode` is unset in Cilium — so a `toCIDR: 192.168.40.0/24` rule
  would **silently fail to match node identities** (Cilium classifies node IPs as
  `host`/`remote-node`, not CIDR). Entities are the correct, working selector; the
  port scopes it to apid only. Nodes are DHCP on `192.168.40.0/24` so pinning IPs would
  drift anyway.
- **In-cluster Omni svc:** `toEndpoints` (ns `frontend`, `app.kubernetes.io/name:
  omni`) tcp/8080, plus a DNS `matchName: omni.frontend.svc.cluster.local` (Cilium `*`
  is single-label, so the existing `*.svc` / `*.cluster.local` patterns don't cover a
  3-label svc FQDN — same trap the MCP svc names hit). Supports the self-hosted Omni.

### Image (PR A — build ≠ rollout)

`scripts/dev-env/Dockerfile` gains `talosctl` and `omnictl`, Renovate-pinned to match
the cluster:

- `talosctl` **v1.13.3** (`siderolabs/talos`) — matches nodes' Talos v1.13.3.
- `omnictl` **v1.9.3** (`siderolabs/omni`) — matches the self-hosted Omni image; SaaS
  Omni is API-compatible with nearby omnictl versions.

The build workflow's image tag bumps `0.4.0 → 0.5.0`; the running pod stays pinned to
`0.4.0@sha256:655a2dac…` so **the rebuild does not roll it** (build ≠ rollout — the tag
is pinned by digest). PR B references the new `0.5.0` image.

## Security rationale

- **Read-only tier ≙ the existing read-only kubectl SA.** The pod already holds a
  scoped, read-biased in-cluster kubectl identity (`rbac.yaml`); this adds a
  *read-only* node/Omni surface in the same spirit. Both node creds are gated to reads
  **server-side** (Talos apid by cert role; Omni by the `Reader` SA role) — the pod is
  not trusted to self-limit.
- **Admin node creds + the age key stay out.** No `os:admin`/`os:operator` talosconfig,
  no Omni Operator/admin SA, no SOPS age key — consistent with the pod's deliberate
  posture (`.agents/sagas/dev-env/`): mutating node/Omni ops happen elsewhere
  (`omni-service-account` runbook, workstation admin config).
- **Exfil boundary stays enumerated.** Every CNP addition is a single named
  destination (node entities:50000, omni svc:8080). If omnictl-against-SaaS is chosen,
  `*.omni.siderolabs.io` is added deliberately, one destination, documented here.

## Split & handoff

### PR A — mergeable now (zero pod-rollout risk)

1. This design doc.
2. Dockerfile: add `talosctl` v1.13.3 + `omnictl` v1.9.3 (Renovate-pinned ARGs).
3. Workflow: bump build tag `0.4.0 → 0.5.0` (running pod pinned to `0.4.0@digest`,
   untouched).
4. CNP: node-apid + Omni-svc egress (+ DNS matchName).

**Why zero rollout:** Reloader (`reloader.stakater.com/auto: "true"` on the Deployment,
running cluster-wide) rolls the pod on changes to **ConfigMaps/Secrets the pod mounts**.
PR A touches **none** of those — a repo markdown file, the Dockerfile, the CI workflow,
and a CiliumNetworkPolicy (reloader does not watch CNPs). The Dockerfile merge fires the
image-build workflow, which is a build, not a rollout: the HelmRelease pins
`0.4.0@sha256:655a2dac…` by **digest**, so Flux keeps pulling the old image.

### PR B — DRAFT, do not merge from inside the pod (it rolls the pod)

1. ExternalSecret `dev-env-node-triage` (1Password item `dev-env-node-triage`:
   `talosconfig`, `omni-service-account-key`).
2. HelmRelease: mount `talosconfig` at `/creds/triage/talosconfig`, env
   `TALOSCONFIG` + `OMNI_ENDPOINT` + `OMNI_SERVICE_ACCOUNT_KEY`; image tag bump to
   `0.5.0@sha256:<PLACEHOLDER — from PR A's build>`.
3. CLAUDE.md ConfigMap: flip the `talosctl`/`omnictl` tool-auth rows to
   "✅ read-only" (ConfigMap change → reloader → belongs here, not PR A).

**Why draft:** every item in PR B changes something the pod mounts (a new Secret volume
+ envFrom, the CLAUDE.md ConfigMap) → reloader rolls the pod. A roll kills every
in-flight tmux/agent session. So PR B is merged **deliberately, from outside the pod**,
after warning/draining sessions.

### User prerequisites (run from a workstation with admin creds)

```bash
# 1) Mint a READ-ONLY talosconfig for the pod (os:reader = dmesg/logs/get/version,
#    no mutate). Run against the admin talosconfig for the main cluster.
#    NOTE: verify flag names against `talosctl config new --help` on v1.13 —
#    `--roles os:reader` is correct for v1.13, but confirm before relying on it.
talosctl config new dev-env-reader.talosconfig --roles os:reader --crt-ttl 8760h
#    Ensure the config's endpoints/nodes cover the node LAN IPs the pod will hit
#    (192.168.40.0/24) — or the agent supplies `-e/-n <ip>` per call.

# 2) (Optional — only if enabling omnictl) Create an Omni Reader service account.
#    Per .agents/runbooks/omni-service-account.md, against the Omni that manages the
#    MAIN nodes (currently SaaS: https://haynes.omni.siderolabs.io):
omnictl serviceaccount create --use-user-role=false --role Reader --ttl 8760h dev-env-node-triage
#    -> prints OMNI_ENDPOINT + OMNI_SERVICE_ACCOUNT_KEY=<base64 key>. Save the key.

# 3) Create 1Password item `dev-env-node-triage` (same vault as the other dev-env
#    items) with fields the ExternalSecret expects:
#      talosconfig               = full contents of dev-env-reader.talosconfig
#      omni-service-account-key  = the OMNI_SERVICE_ACCOUNT_KEY value (omnictl only)
```

### Outside-agent deploy steps (merging PR B)

1. Confirm PR A's build succeeded; grab the `0.5.0` digest (anon GHCR token → `GET
   https://ghcr.io/v2/thaynes43/dev-env/manifests/0.5.0` → 200; record the digest).
2. Fill the `0.5.0@sha256:<PLACEHOLDER>` in PR B's HelmRelease with that digest.
3. Confirm the 1Password item `dev-env-node-triage` exists (ExternalSecret will sync).
4. Decide `OMNI_ENDPOINT` (SaaS `haynes.omni.siderolabs.io` for main nodes → also add
   `*.omni.siderolabs.io` egress; or drop omnictl and ship talosctl-only).
5. **Warn/drain the dev-env agent sessions (tmux session `main`)** — merging rolls the
   pod and kills them.
6. Mark PR B ready + merge `--squash --delete-branch`; watch
   `kubectl rollout status deploy/dev-env -n dev`.
7. Verify from a fresh pod shell: `talosctl -n <node-ip> version` and
   `talosctl -n <node-ip> dmesg | tail` succeed; `kubectl get nodes` still works;
   inspect the reader cert's role (`talosctl config info` / decode the client cert) to
   confirm it is `os:reader` — **do NOT test by attempting a mutation** (a denied
   `reboot` would be a destructive probe).

## Acceptance

- From a fresh pod shell: `talosctl -n <node> dmesg` and `talosctl -n <node> logs
  <service>` return data for every node (masters included).
- The mounted talosconfig is provably `os:reader` (cert role inspected, not by
  attempting a mutation).
- `kubectl`/`flux`/existing MCP access is unaffected; no new mutating capability
  reached the pod.
- (If omnictl enabled) `omnictl get machinestatus` returns for the main nodes against
  the chosen `OMNI_ENDPOINT`.
</content>
</invoke>
