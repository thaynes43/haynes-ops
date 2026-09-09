# 14 — Proxmox access (read + VM-scoped operator tier for the pod)

**Status:** in progress — split across **PR A** (mergeable now, zero pod-rollout) and
**PR B** (DRAFT, wires the creds + `pve` helper into the pod; rolls the pod, so Tom merges
it at a natural break). See "Split & handoff".
**Depends on:** 04 (ExternalSecret/1Password pattern), 05 (RBAC tier precedent),
10 (Reader→Operator credential split precedent)
**Origin:** incident 2026-09-09 (`.agents/reports/incident-2026-09-09-worker-host-outage.md`)

## Decision log

### 2026-09-09 — why this exists

The 03:00 EDT UniFi auto-firmware rollout rebooted the aggregation switch; the **5-node
Proxmox cluster** (twin-top, twin-bottom, pve04, HaynesIntelligence, pve-filet02) lost
corosync quorum and **every node fenced itself** (`pve_uptime_seconds` reset within
seconds of each other), hard-resetting all three Talos workers — which live on **three
different hosts**, not one. talosw02 did not autostart; Prometheus, Loki, EMQX, AppDaemon
went with it; 3.5 h outage.

The fence is armed by **Proxmox HA**, which manages exactly five guests and **not one of
them is a Talos node**: `vm:104` gasha01, `ct:105` nut2700, `ct:106` cephdash, `ct:107`
pvedash, `vm:109` ubuntu01 (state `ignored`). LRM is `active` on four nodes, `idle` on
HaynesIntelligence (read 2026-09-09 14:30Z via `/cluster/ha/status/current`).

Nothing in dev-env could see or touch any of this: the pod cannot resolve
`*.haynesnetwork`, has no egress to the PVE API, and holds no PVE credential. Tom's
ruling (2026-09-09): **he is not going to run the fixes by hand — bolster dev-env so
agents can.** This item is that.

The UniFi side is already done: `mgmt.auto_upgrade` set `true → false` on Tom's go
(controller read-back confirmed; logged in `~/work/unifi-findings-2026-09-09.md` on the
dev-env PVC). That removes the *trigger*; this item is about the *fault*.

### 2026-09-09 — two tiers, and why the operator tier is VM-scoped

PVE's API tells you exactly which privilege each write wants (probed with the read-only
token against non-existent ids — the permission check runs before the existence check,
so nothing can change):

| Action | Endpoint | Privilege (PVE 8.4) |
|---|---|---|
| remove / add / edit an HA resource | `DELETE/POST/PUT /cluster/ha/resources/{sid}` | **`Sys.Console` on `/`** |
| set `onboot` on a VM | `PUT /nodes/{n}/qemu/{id}/config` | `VM.Config.Options` on `/vms/{id}` |
| start / stop / reset a VM | `POST /nodes/{n}/qemu/{id}/status/*` | `VM.PowerMgmt` on `/vms/{id}` |
| everything the exporter reads | `GET …` | `*.Audit` (PVEAuditor) |

**`Sys.Console` is the problem.** HA management needs it, but it *also* gates
`/nodes/{node}/termproxy` — a root shell on every PVE node over the API. A standing token
with it is root-equivalent on the whole cluster, in the environment of yolo-mode agents.
That is the same class of decision as the age key ("never enters this pod without an
explicit decision") and is **not** taken here by default.

### 2026-09-09 — Q-1 ruling (Tom): standing access, `Sys.Console` included

Asked in-session with the root-shell consequence spelled out. Tom: *"we will need to get it
access so don't think past that."* Ruling: **dev-env holds a standing Proxmox operator
token that includes `Sys.Console`.** It is root-equivalent on the PVE cluster and that is
accepted, the same way `kubectl exec` into secret-mounting pods was accepted 2026-08-06.
The rest of this item is written to that ruling; the earlier VM-scoped design below is
kept as the record of what was considered.

**Adopted:**

- **READ tier — zero new provisioning.** Reuse the exporter's `prometheus@pve!exporter`
  token (PVEAuditor) from the existing 1Password `proxmox` item. Agents get full
  visibility: HA state, quorum, onboot per guest, node uptimes, VM placement.
- **OPERATOR tier — standing, root-equivalent (Q-1 ruling).** PVE user `dev-env@pve`,
  custom role `DevEnvOperator` = `Sys.Audit, Sys.Console, VM.Audit, VM.Config.Options,
  VM.PowerMgmt` (+ `PVEAuditor` for the other read privileges), one ACL on `/`, token
  `dev-env@pve!operator` with `privsep=0`. Agents can remove/adjust HA resources, set
  onboot, start/stop/reset guests. Node reboots and shells are *possible* via
  `Sys.Console` but off-limits by rule (runbook "Do not"), not by ACL.
- **Accident guardrail, not a security boundary:** the `pve` helper's `vm` verbs refuse
  ids other than the three Talos workers (103/108/113) unless `--any` is passed, so a
  typo cannot stop gasha01. PVE's task log (`/cluster/tasks`, user `dev-env@pve`) is the
  audit trail; revocation is deleting the token.
- **The fence fix is a normal agent task** once PR B is deployed: `pve delete
  /cluster/ha/resources/<sid> --yes` for the five resources, declared with
  `declare-activity`, logged in the incident report.

## Goal

From inside the dev-env pod, an agent can (a) read the whole Proxmox cluster's state and
(b) bring a Talos worker VM back or set it to autostart — without a workstation and
without a root-equivalent standing credential.

## Design

### Network (PR A — applies live, no pod restart)

CiliumNetworkPolicy `dev-env`: DNS `matchName` for the six `.haynesnetwork` hosts and a
`toFQDNs` egress rule — `pvedash.haynesnetwork:443` (HAProxy LB, lxc/107) and the five
nodes' `pveproxy` on `:8006`. Enumerated names, not `*.haynesnetwork` (that would name
every device on the LAN). These hosts are not cluster nodes, so they carry `world`
identity and `toFQDNs` matches them (the Talos-node CIDR trap in rule 4 does not apply).
All six names resolve from cluster pods (verified 2026-09-09 from the exporter pod:
pvedash .49, twin-top .60, twin-bottom .18, pve04 .9, HaynesIntelligence .11,
pve-filet02 .13).

### Credentials (PR A creates the Secrets; PR B consumes them)

- `ExternalSecret dev-env-proxmox` → `PVE_TOKEN_ID` / `PVE_TOKEN_SECRET` from the
  `proxmox` item (`PROXMOX_API_TOKEN_ID` / `PROXMOX_API_TOKEN_SECRET`). Syncs today.
- `ExternalSecret dev-env-proxmox-operator` → `PVE_OPERATOR_TOKEN_ID` /
  `PVE_OPERATOR_TOKEN_SECRET` from the `dev-env` item. **SecretSyncedError until Tom adds
  the fields** — harmless (dev-env-gcp precedent); PR B consumes it `optional: true`.

### `pve` helper (PR B — ConfigMap, rolls the pod)

`resources/pve.sh`, installed as `pve` like `declare-activity`. Thin bash over
`curl`+`jq` against `$PVE_API_URL` (`https://pvedash.haynesnetwork`), header
`Authorization: PVEAPIToken=<id>=<secret>`, `-k` (PVE's own CA; same stance as the
exporter's `PVE_VERIFY_SSL=false`). Picks the operator token when present, else the
read token; `--ro` forces read. Verbs: `pve get|post|put|delete <path> [k=v…]` plus
sugar: `pve ha`, `pve nodes`, `pve guests`, `pve vm <id> config|start|stop|reset|onboot`.
Writes print the resolved URL and ask for `--yes` (agents pass it deliberately, like
`confirm: true` on mcp-unifi).

### Docs

`.agents/runbooks/proxmox-access.md` — usage, the fence remedy, and the trust boundary.
CLAUDE.md tool-auth row (PR B, it's a ConfigMap).

## Security rationale

- Read tier is the exporter's own token — no new blast radius; it already sits in
  `observability`.
- Operator tier is **root-equivalent on the PVE cluster** (`Sys.Console` → node shell
  via `termproxy`). Accepted by Tom 2026-09-09 (Q-1). Worst case for a compromised agent
  is the same as the pod already carries for the Kubernetes side at OPERATOR RBAC plus
  exec-into-secret-pods: the CNP egress allowlist is the exfil boundary, the token has no
  password login, and it is one `pveum user token remove` from gone.
- The `pve` helper's worker-only guard on `vm` verbs exists to catch mistakes, not
  attackers.

## Split & handoff

### PR A — mergeable now (zero pod-rollout)

1. This design doc + `.agents/runbooks/proxmox-access.md` + incident report addendum.
2. CNP: six DNS names + the two `toFQDNs` egress rules.
3. Two ExternalSecrets (unreferenced by the Deployment → reloader is not involved).

**Why zero rollout:** nothing the pod mounts changes. CNPs apply live; new Secrets that
no container references do not trigger reloader.

### PR B — DRAFT, do not merge from inside the pod (it rolls the pod)

1. HelmRelease app container: `PVE_API_URL` env + `envFrom` both secrets with
   `optional: true` (app-template 5.1.0 passes `optional` through — checked in the chart's
   `_envFrom.tpl`).
2. `resources/pve.sh` in the `dev-env-scripts` ConfigMap; dev-init installs it.
3. CLAUDE.md: tool-auth row `pve` (✅ READ via PVEAuditor; ✅ OPERATOR VM-scoped once
   the 1Password fields exist).

### User prerequisites (Tom, once, ~3 min — a shell on any PVE node, or the web UI)

Only needed for the OPERATOR tier; the READ tier works with nothing from Tom.

```bash
pveum role add DevEnvOperator -privs "Sys.Audit Sys.Console VM.Audit VM.Config.Options VM.PowerMgmt"
pveum user add dev-env@pve
pveum acl modify / -user dev-env@pve -role DevEnvOperator
pveum acl modify / -user dev-env@pve -role PVEAuditor
pveum user token add dev-env@pve operator -privsep 0     # prints the secret ONCE — copy it
```

Web UI equivalent: Datacenter → Permissions → Roles (create `DevEnvOperator` with those
five privileges) → Users (add `dev-env`, realm `pve`) → Add User Permission on `/` twice
(`DevEnvOperator`, `PVEAuditor`) → API Tokens (user `dev-env@pve`, id `operator`,
**untick Privilege Separation**, no expiry).

Then 1Password item `dev-env`, two TOP-LEVEL fields, exact labels:

```
PROXMOX_OPERATOR_TOKEN_ID     = dev-env@pve!operator
PROXMOX_OPERATOR_TOKEN_SECRET = <the uuid pveum printed>
```

### Q-1 — the HA fix itself (needs `Sys.Console`)

Recorded and asked 2026-09-09. Options were (a) temporary `Sys.Console` + one-shot Job,
(b) standing `Sys.Console`, (c) Tom runs `ha-manager remove`. **Ruling (Tom): (b)** —
*"we will need to get it access so don't think past that."*

## Acceptance

- PR A merged: from the dev-env pod, `getent hosts pvedash.haynesnetwork` resolves and
  `curl -k https://pvedash.haynesnetwork/api2/json/version` answers `401` (reachable,
  unauthenticated). `kubectl get es -n dev dev-env-proxmox` → `SecretSynced`.
- PR B merged (post-bounce): `pve ha` lists the HA resources and quorum; `pve vm 108
  config` shows `onboot: 1`; `pve vm 104 stop --yes` is refused by the helper's
  worker-only guard (no request made); `pve --any vm 104 config` reads.
- The fence remedy in the runbook has been executed once and `pve ha` shows no
  `started` resources; LRMs report `idle`.
