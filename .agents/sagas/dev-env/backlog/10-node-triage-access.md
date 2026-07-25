# 10 — node-triage access (read-only talosctl + omnictl for the pod)

**Status:** in progress — split across **PR A** (mergeable now, zero pod-rollout) and
**PR B** (DRAFT, deploys the creds; rolls the pod, so a human/outside-agent merges it
at a chosen moment). See "Split & handoff" below.
**Depends on:** 04 (ExternalSecret/1Password auth pattern), 05 (RBAC read-tier precedent)
**Parallel with:** nothing

## Decision log

### 2026-07-25 — proxy-through-SaaS-Omni, LAN-direct deferred

The original design centered on a **LAN-direct `os:reader` talosconfig** (talosctl
straight to each node's apid on `192.168.40.0/24:50000`, Omni-independent). That route
is **unavailable on the user's SaaS Omni tier**: break-glass / raw talosconfig download
returns **PermissionDenied even when freshly authed as the Admin user**, so no
node-trusted client cert can be minted. LAN-direct is dead for now.

**Adopted instead:** triage runs **through SaaS Omni**.
- An Omni **service account `dev-env-node-triage` (role `Reader`)** — key in 1Password
  item **`dev-env`**, field **`OMNI_SERVICE_ACCOUNT_KEY`**.
- `omnictl` authenticates headless via `OMNI_ENDPOINT` +
  `OMNI_SERVICE_ACCOUNT_KEY`. The endpoint is
  **`https://haynes.na-west-1.omni.siderolabs.io:443`** (note `na-west-1`; explicit
  `:443` — verbatim what `omnictl serviceaccount create` printed).
- `talosctl` gets an **Omni-proxied talosconfig at runtime** (`omnictl talosconfig`),
  so talosctl traffic **also routes through SaaS Omni** (WAN), not the LAN.

**Tradeoff (accepted):** triage now **depends on WAN + Omni availability**. If Omni or
the internet is down — or during an Omni-side incident — the pod cannot reach the
nodes, which is exactly a moment you might want triage. The LAN-direct route did not
have this dependency.

**Preferred future upgrade (if Sidero support enables break-glass on this tier):**
switch back to LAN-direct — re-add the `talosconfig` field to the 1Password item + the
ExternalSecret mapping, mount it + set `TALOSCONFIG`, and the WAN dependency drops away.
The CNP already keeps the Talos-apid node egress rule
(`toEntities: [remote-node, host]` :50000) in place as pre-positioning, so that flip is
a creds-only change, not a network-policy change.

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

**As implemented under the 2026-07-25 decision (proxy-through-SaaS-Omni):**

1. ExternalSecret `dev-env-node-triage` → Secret `dev-env-node-triage-secret`, one
   field: `OMNI_SERVICE_ACCOUNT_KEY` from **1Password item `dev-env`**, property
   **`OMNI_SERVICE_ACCOUNT_KEY`**. (No `talosconfig` field — a missing 1Password
   property fails the whole ES sync, so LAN-direct's talosconfig is omitted until
   break-glass is available.)
2. HelmRelease: env `OMNI_ENDPOINT=https://haynes.na-west-1.omni.siderolabs.io:443`
   (plain) + `OMNI_SERVICE_ACCOUNT_KEY` (valueFrom the secret); **no talosconfig
   mount, no `TALOSCONFIG`** — talosctl gets an Omni-proxied config at runtime
   (`omnictl talosconfig`). Image tag stays a clearly-marked PLACEHOLDER (bump all 3
   containers to `0.5.0@sha256:<from PR A's build>` before merge).
3. CLAUDE.md ConfigMap: `omnictl` → ✅ Reader SA (proxied); `talosctl` → ✅ via
   Omni-proxied talosconfig (`omnictl talosconfig` at runtime). ConfigMap change →
   reloader → belongs here, not PR A.

**Why draft:** every item in PR B changes something the pod mounts (a new Secret
consumed via `valueFrom`, the CLAUDE.md ConfigMap) → reloader rolls the pod. A roll
kills every in-flight tmux/agent session. So PR B is merged **deliberately, from
outside the pod**, after warning/draining sessions.

### User prerequisites (run from a workstation with admin creds)

The Omni Reader service account `dev-env-node-triage` and its key already exist (the
user created them 2026-07-25). The only standing prerequisite:

```
1Password item `dev-env` (same vault as the other dev-env items), field:
  OMNI_SERVICE_ACCOUNT_KEY = the base64 key `omnictl serviceaccount create` printed
```

(For reference, how the SA was made — see `.agents/runbooks/omni-service-account.md`:
`omnictl serviceaccount create --use-user-role=false --role Reader --ttl 8760h
dev-env-node-triage`, which prints `OMNI_ENDPOINT` +
`OMNI_SERVICE_ACCOUNT_KEY=<base64 key>`.)

### Outside-agent deploy steps (merging PR B)

1. Confirm PR A's build succeeded; grab the `0.5.0` digest (anon GHCR token → `GET
   https://ghcr.io/v2/thaynes43/dev-env/manifests/0.5.0` → 200; record the digest).
2. Fill the `0.5.0@sha256:<PLACEHOLDER>` in PR B's HelmRelease (all 3 containers).
3. Confirm the 1Password item `dev-env` has field `OMNI_SERVICE_ACCOUNT_KEY` (the ES
   will sync it to `dev-env-node-triage-secret`).
4. **Warn/drain the dev-env agent sessions (tmux session `main`)** — merging rolls the
   pod and kills them.
5. Mark PR B ready + merge `--squash --delete-branch`; watch
   `kubectl rollout status deploy/dev-env -n dev`.
6. Verify from a fresh pod shell (read-only):
   - `omnictl get clusters` (or `omnictl get machinestatus`) returns — proves the
     Reader SA + endpoint + egress work.
   - `omnictl talosconfig /tmp/tc && talosctl --talosconfig /tmp/tc -n 192.168.40.93
     dmesg | tail -3` — proves proxied talosctl reaches a master.
   - `kubectl get nodes` still works. Do **not** probe by attempting a mutation.

## Acceptance

- From a fresh pod shell: `omnictl get clusters` succeeds, and
  `omnictl talosconfig /tmp/tc && talosctl --talosconfig /tmp/tc -n <node> dmesg`
  returns data for the masters.
- The Omni SA is provably `Reader` (mutations denied by Omni/Talos server-side — not
  probed by attempting one).
- `kubectl`/`flux`/existing MCP access is unaffected; no new mutating capability
  reached the pod.
- WAN/Omni dependency understood: triage is unavailable if Omni or the internet is
  down (the tradeoff recorded in the 2026-07-25 decision).
</content>
</invoke>
