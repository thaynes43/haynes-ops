# 10 — node-triage access (talosctl + omnictl for the pod)

**Status:** in progress — split across **PR A** (mergeable now, zero pod-rollout) and
**PR B** (DRAFT, deploys the creds; rolls the pod, so a human/outside-agent merges it
at a chosen moment). See "Split & handoff" below.
**Correction (2026-10-31):** the Omni service account `dev-env-node-triage` holds the
Omni **Admin** role by design. This doc's `Reader` and read-only-by-role wording records
the 2026-07-25 plan, not the account. See the 2026-10-31 decision below.
**Depends on:** 04 (ExternalSecret/1Password auth pattern), 05 (RBAC read-tier precedent)
**Parallel with:** nothing

## Decision log

### 2026-10-31 — the triage account holds Omni Admin by design

Tom (2026-10-31, OpenProject #233): the account is intended to be Admin; the description
agents were given was wrong. So:

- The Omni service account `dev-env-node-triage` (1Password item `dev-env`, field
  `OMNI_SERVICE_ACCOUNT_KEY`) holds the Omni **Admin** role. The `Reader` statements
  below describe the 2026-07-25 plan, not the account.
- Omni does not keep this credential read-only. The talosconfig that
  `omnictl talosconfig` fetches goes through Omni and gets Talos access from the
  account's Omni role. Nothing on the server side stops a write.
- Reads need nothing more. An Omni or Talos write changes the cluster: make one only
  inside an authorized story, one resource at a time, with health gates and a saved
  copy for exact rollback. Never write as a probe, and never run a full `task omni:sync`.
  The v2 session rules say the same
  (`kubernetes/main/apps/dev-env-system/session-config/app/config/claude/CLAUDE.md`,
  #4478).
- A separate Omni Reader account for routine reads is proposed in OpenProject #282,
  not yet provisioned.

### 2026-07-25 — proxy-through-SaaS-Omni, LAN-direct deferred

The original design centered on a **LAN-direct `os:reader` talosconfig** (talosctl
straight to each node's apid on `192.168.40.0/24:50000`, Omni-independent). That route
is **unavailable on the user's SaaS Omni tier**: break-glass / raw talosconfig download
returns **PermissionDenied even when freshly authed as the Admin user**, so no
node-trusted client cert can be minted. LAN-direct is dead for now.

**Adopted instead:** triage runs **through SaaS Omni**.
- An Omni **service account `dev-env-node-triage` (role `Reader`)** — key in 1Password
  item **`dev-env`**, field **`OMNI_SERVICE_ACCOUNT_KEY`**. *(Corrected 2026-10-31: the
  account holds Omni Admin, not `Reader`. See the decision log.)*
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
agent. *(Corrected 2026-10-31: the Omni credential is Admin, and the Omni-proxied
talosconfig takes its Talos access from it. Neither is limited to reads by its role, so
the pod does hold a mutating node/Omni credential. See the decision log.)*

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
  browser). The SA key comes from 1Password via an ExternalSecret. *(Corrected
  2026-10-31: the account holds Omni Admin, not `Reader`. See the decision log.)*
- **Topology caveat (resolve before wiring omnictl):** the **main cluster's nodes are
  managed by SaaS Omni `https://haynes.omni.siderolabs.io`** (confirmed by
  `kubernetes/main/bootstrap/omni/haynes-ops-omniconfig.yaml`, the main kubeconfig
  server `haynes.kubernetes.na-west-1.omni.siderolabs.io`, and the omni-service-account
  runbook). The **self-hosted Omni at `omni.haynesops.com`** (app
  `kubernetes/main/apps/frontend/omni`, image `ghcr.io/siderolabs/omni:v1.9.3`) was a
  **separate instance** and was *not* what registers `talosm0x`. **(UPDATE 2026-09-22:
  that instance was decommissioned — SaaS Omni is now the only Omni. See
  `.agents/reference/repo-overview.md`.)** So:
  - `OMNI_ENDPOINT` for main-cluster triage is `https://haynes.omni.siderolabs.io`,
    which requires CNP egress to `*.omni.siderolabs.io` — **not** the in-cluster svc.
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
- **In-cluster Omni svc** (**REMOVED 2026-09-22 with the self-hosted Omni**):
  `toEndpoints` (ns `frontend`, `app.kubernetes.io/name: omni`) tcp/8080, plus a DNS
  `matchName: omni.frontend.svc.cluster.local`. The 3-label-FQDN lesson still stands
  (Cilium `*` is single-label, so `*.svc` / `*.cluster.local` don't cover
  `name.ns.svc.cluster.local` — the same trap the MCP svc names hit).

### Image (PR A — build ≠ rollout)

`scripts/dev-env/Dockerfile` gains `talosctl` and `omnictl`, Renovate-pinned to match
the cluster:

- `talosctl` **v1.13.3** (`siderolabs/talos`) — matches nodes' Talos v1.13.3.
- `omnictl` **v1.9.3** (`siderolabs/omni`) — then matched the self-hosted Omni image;
  since that instance was removed (2026-09-22) the pin only needs to stay API-compatible
  with SaaS Omni, which tolerates nearby omnictl versions.

The build workflow's image tag bumps `0.4.0 → 0.5.0`; the running pod stays pinned to
`0.4.0@sha256:655a2dac…` so **the rebuild does not roll it** (build ≠ rollout — the tag
is pinned by digest). PR B references the new `0.5.0` image.

## Security rationale

- **Read-only tier ≙ the existing read-only kubectl SA.** The pod already holds a
  scoped, read-biased in-cluster kubectl identity (`rbac.yaml`); this adds a
  *read-only* node/Omni surface in the same spirit. Both node creds are gated to reads
  **server-side** (Talos apid by cert role; Omni by the `Reader` SA role) — the pod is
  not trusted to self-limit. *(Corrected 2026-10-31: neither credential is gated to
  reads. The Omni account is Admin, and the Omni-proxied talosconfig, which has no client
  cert, takes its Talos access from that account. See the decision log.)*
- **Admin node creds + the age key stay out.** No `os:admin`/`os:operator` talosconfig,
  no Omni Operator/admin SA, no SOPS age key — consistent with the pod's deliberate
  posture (`.agents/sagas/dev-env/`): mutating node/Omni ops happen elsewhere
  (`omni-service-account` runbook, workstation admin config). *(Corrected 2026-10-31:
  the Omni account the pod holds is Admin, so this bullet no longer holds for Omni or for
  the Omni-proxied talosconfig. See the decision log.)*
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
   reloader → belongs here, not PR A. *(Corrected 2026-10-31: the account is Admin.)*

**Why draft:** every item in PR B changes something the pod mounts (a new Secret
consumed via `valueFrom`, the CLAUDE.md ConfigMap) → reloader rolls the pod. A roll
kills every in-flight tmux/agent session. So PR B is merged **deliberately, from
outside the pod**, after warning/draining sessions.

### User prerequisites (run from a workstation with admin creds)

The Omni Reader service account `dev-env-node-triage` and its key already exist (the
user created them 2026-07-25). *(Corrected 2026-10-31: the account holds Omni Admin,
not `Reader`. See the decision log.)* The only standing prerequisite:

```
1Password item `dev-env` (same vault as the other dev-env items), field:
  OMNI_SERVICE_ACCOUNT_KEY = the base64 key `omnictl serviceaccount create` printed
```

(For reference, how the SA was made — see `.agents/runbooks/omni-service-account.md`:
`omnictl serviceaccount create --use-user-role=false --role Reader --ttl 8760h
dev-env-node-triage`, which prints `OMNI_ENDPOINT` +
`OMNI_SERVICE_ACCOUNT_KEY=<base64 key>`.) *(This records the 2026-07-25 plan; the live
account reports role Admin. Rotate its key with `omnictl serviceaccount renew
dev-env-node-triage`, which keeps the role. `create` makes a new account, and recreating
it with this command would make it Reader.)*

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
     SA + endpoint + egress work.
   - `omnictl talosconfig /tmp/tc && talosctl --talosconfig /tmp/tc -n 192.168.40.93
     dmesg | tail -3` — proves proxied talosctl reaches a master.
   - `kubectl get nodes` still works. Do **not** probe by attempting a mutation.

## Acceptance

- From a fresh pod shell: `omnictl get clusters` succeeds, and
  `omnictl talosconfig /tmp/tc && talosctl --talosconfig /tmp/tc -n <node> dmesg`
  returns data for the masters.
- The Omni SA is provably `Reader` (mutations denied by Omni/Talos server-side — not
  probed by attempting one). *(Not met, by design: the account holds Omni Admin. See
  the 2026-10-31 decision.)*
- `kubectl`/`flux`/existing MCP access is unaffected; no new mutating capability
  reached the pod. *(The Omni key is a mutating capability since the account is Admin;
  see the 2026-10-31 decision.)*
- WAN/Omni dependency understood: triage is unavailable if Omni or the internet is
  down (the tradeoff recorded in the 2026-07-25 decision).
