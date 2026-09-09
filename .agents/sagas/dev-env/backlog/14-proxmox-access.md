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

**Adopted:**

- **READ tier — zero new provisioning.** Reuse the exporter's `prometheus@pve!exporter`
  token (PVEAuditor) from the existing 1Password `proxmox` item. Agents get full
  visibility: HA state, quorum, onboot per guest, node uptimes, VM placement.
- **OPERATOR tier — VM-scoped, no shell.** A new PVE user `dev-env@pve` with a custom
  role `DevEnvVMOperator` = `VM.Audit, VM.Config.Options, VM.PowerMgmt`, ACL'd on
  **`/vms/103`, `/vms/108`, `/vms/113` only** (talosw01/02/03), plus `PVEAuditor` on `/`
  for reads. Token `dev-env@pve!operator`, `privsep=0` (inherits the user's ACL). An agent
  can fix onboot, start a dead worker, or hard-reset a wedged one — and cannot touch
  gasha01, the dashboards, HA config, or a node shell.
- **HA changes (the one-time fence fix) are a separate, non-standing act.** Either Tom
  runs `ha-manager remove` ×4 (declined 2026-09-09), or a *temporary* `Sys.Console` token
  is minted for a one-shot Job and deleted afterwards (the operator-key Job pattern from
  the 2026-08-21 Talos roll). **Open question Q-1 to Tom:** temporary token + one-shot
  Job, or accept a standing root-equivalent token? Recorded here; asked in-session.

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
- Operator tier can act on three VM ids and nothing else. Worst case for a compromised
  agent: stop/reset the Talos workers — the same thing `kubectl delete pod` at the
  OPERATOR RBAC tier can already do to their workloads, and Kubernetes recovers.
- `Sys.Console` (HA management, node shells) stays out of the pod's standing environment.
  If Tom later rules otherwise, that is a one-line ACL change on the PVE side and a
  decision entry here — the wiring does not change.

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

### User prerequisites (Tom, ~5 min, PVE web UI on pvedash — phone works)

Only needed for the OPERATOR tier; the READ tier works with nothing from Tom.

1. Datacenter → Permissions → **Roles** → Create: `DevEnvVMOperator`, privileges
   `VM.Audit`, `VM.Config.Options`, `VM.PowerMgmt`.
2. Datacenter → Permissions → **Users** → Add: `dev-env`, realm `pve` (any password;
   it is never used — token auth only).
3. Datacenter → Permissions → **Add → User Permission**, three times: path `/vms/103`,
   `/vms/108`, `/vms/113`; user `dev-env@pve`; role `DevEnvVMOperator`. And once more:
   path `/`, role `PVEAuditor`.
4. Datacenter → Permissions → **API Tokens** → Add: user `dev-env@pve`, token id
   `operator`, **untick Privilege Separation**, no expiry. Copy the secret (shown once).
5. 1Password item `dev-env` (same vault as the other dev-env fields), two TOP-LEVEL
   fields, exact labels:
   `PROXMOX_OPERATOR_TOKEN_ID = dev-env@pve!operator`
   `PROXMOX_OPERATOR_TOKEN_SECRET = <the uuid>`

Equivalent CLI on any node, if preferred:

```bash
pveum role add DevEnvVMOperator -privs "VM.Audit VM.Config.Options VM.PowerMgmt"
pveum user add dev-env@pve
for v in 103 108 113; do pveum acl modify /vms/$v -user dev-env@pve -role DevEnvVMOperator; done
pveum acl modify / -user dev-env@pve -role PVEAuditor
pveum user token add dev-env@pve operator -privsep 0     # prints the secret once
```

### Q-1 — the HA fix itself (needs `Sys.Console`)

Recorded 2026-09-09; asked in-session. Options: **(a)** temporary token with
`Sys.Console` on `/`, used by a one-shot Job to `DELETE /cluster/ha/resources/{ct:105,
ct:106, ct:107, vm:104}` (and `vm:109`), then deleted; **(b)** grant `dev-env@pve`
`Sys.Console` standing and accept root-equivalence; **(c)** Tom runs `ha-manager remove`
himself. Ruling: _pending_.

## Acceptance

- PR A merged: from the dev-env pod, `getent hosts pvedash.haynesnetwork` resolves and
  `curl -k https://pvedash.haynesnetwork/api2/json/version` answers `401` (reachable,
  unauthenticated). `kubectl get es -n dev dev-env-proxmox` → `SecretSynced`.
- PR B merged (post-bounce): `pve ha` lists the HA resources and quorum; `pve vm 108
  config` shows `onboot: 1`; with the operator token present, `pve vm 108 config` via
  `--ro` and non-`--ro` both work and a `pve vm 104 config` write is refused `403` by PVE
  (out-of-scope guest).
- The fence remedy in the runbook has been exercised once (Q-1 ruling applied) and
  `pve ha` shows no `started` resources.
