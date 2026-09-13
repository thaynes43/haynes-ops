# Proxmox access from dev-env (`pve`)

Design + decisions: `.agents/sagas/dev-env/backlog/14-proxmox-access.md`. This is the
operating manual.

## What you have

| Tier | Credential | Can | Cannot |
|---|---|---|---|
| READ | `$PVE_TOKEN_ID` / `$PVE_TOKEN_SECRET` (`prometheus@pve!exporter`, PVEAuditor) | every `GET`: cluster/HA status, quorum, node uptimes, guest placement, `onboot` per guest | any write |
| OPERATOR | `$PVE_OPERATOR_TOKEN_ID` / `$PVE_OPERATOR_TOKEN_SECRET` (`dev-env@pve!operator`), **not wired yet — see "Enabling OPERATOR" below; filling the 1Password fields alone is not enough** | HA resources (remove/adjust), `onboot`, start/stop/reset on guests; PVE-side it is root-equivalent (`Sys.Console`, Tom's ruling 2026-09-09) | by **rule**, not ACL: node reboots, node shells, anything on guests other than the three Talos workers without `--any` (the helper refuses ids ≠ 103/108/113 as a typo guard) |

`pve` uses the operator token when it is set, else the read token. `pve --ro …` forces
the read token. Env is absent until PR B of backlog 14 has deployed; before that, the
API is reachable from the pod (CNP) but you hold no token — do not go looking for one in
other pods.

### Enabling OPERATOR — three steps, not two

The operator env vars are populated by an ExternalSecret that **does not exist in the
repo yet**. It is commented out on purpose in
`kubernetes/main/apps/dev/dev-env/app/externalsecret.yaml`, because an ExternalSecret
pointing at 1Password fields that do not exist goes `SecretSyncedError`, and the
dev-env Kustomization is `wait: true` — which fails the whole app and pages. That is
exactly what happened on 2026-09-09 and had to be reverted in #2807.

So enabling operator tier takes **three** steps, in this order (the authoritative copy,
with the exact `pveum` commands and the ExternalSecret body, is the comment block in
`externalsecret.yaml`):

1. On a PVE node, create the `DevEnvOperator` role, the `dev-env@pve` user and its
   token — the secret prints **once**.
2. Add `PROXMOX_OPERATOR_TOKEN_ID` and `PROXMOX_OPERATOR_TOKEN_SECRET` as **top-level
   fields on the 1Password `dev-env` item**.
3. **Only then** uncomment and commit the `dev-env-proxmox-operator` ExternalSecret,
   and let the pod bounce once (reloader fires on Secret *updates*, not creation).

Skipping step 3 is the trap this section exists for: filling in 1Password and bouncing
the pod changes nothing, because nothing is reading those fields yet.

**Naming gotcha:** the 1Password *field* names are `PROXMOX_OPERATOR_TOKEN_ID` /
`PROXMOX_OPERATOR_TOKEN_SECRET`, but the *env vars* `pve.sh` reads are
`PVE_OPERATOR_TOKEN_ID` / `PVE_OPERATOR_TOKEN_SECRET`. The ExternalSecret remaps between
them, so `PROXMOX_*` will never appear in the pod environment — checking for it and
finding nothing is expected, not a fault.

## The cluster

Five PVE 8.4 nodes, all single-homed into Switch Pro Aggregation (that is the fence
trigger): twin-top `.60`, twin-bottom `.18`, pve04 `.9`, HaynesIntelligence `.11`,
pve-filet02 `.13` (all `192.168.40.x`, `<name>.haynesnetwork`). API entry:
`https://pvedash.haynesnetwork` (HAProxy on lxc/107, itself an HA resource — if pvedash
is down, hit a node directly on `:8006`; `pve` takes `PVE_API_URL`).

Worker placement (2026-09-09): talosw01 = `qemu/103` on HaynesIntelligence, talosw02 =
`qemu/108` on twin-top, talosw03 = `qemu/113` on twin-bottom. gasha01 = `qemu/104` on
twin-bottom (external Ceph for Prometheus/Loki + NFS).

## Commands

```bash
pve ha                     # HA resources + quorum + LRM/CRM state (the fence picture)
pve nodes                  # node uptime / status
pve guests                 # every guest: id, name, node, status, onboot
pve vm 108 config          # one guest's config
pve vm 108 onboot 1 --yes  # operator tier
pve vm 108 start --yes     # operator tier
pve vm 108 reset --yes     # operator tier — a HARD reset; declare-activity first
pve get /cluster/ha/status/current            # raw API, any path
pve --ro get /nodes/twin-top/status
```

Writes print the resolved URL and refuse without `--yes`. Anything disruptive (start,
stop, reset) — `declare-activity start … --scope <worker>` first, the same as a pod
delete.

## Remedy: a worker is down after a PVE fence

Signature: `PVENodeRebooted` for several nodes within a minute, workers NotReady, and
`pve guests` shows a Talos worker `stopped`. (`node-out-of-service` handles the
Kubernetes side after 8 min; this is the Proxmox side.)

1. `pve guests` — which worker is stopped, and is `onboot` 1? (All three are 1 since
   2026-09-09; if one is 0, `pve vm <id> onboot 1 --yes`.)
2. `pve nodes` — is its host up and quorate? If the host is down, nothing here helps;
   that is a physical/UPS problem — page.
3. `pve vm <id> start --yes`, then `kubectl get node -w` until Ready. Log it.

## Remedy: the fence itself (one-time; operator tier)

The standing operator token can do this (Q-1 ruling). It is a planned change, not an
emergency: `declare-activity start "removing PVE HA resources" --scope proxmox --ttl 30m`,
then:

```bash
pve ha                                                # before
for sid in vm:104 ct:105 ct:106 ct:107 vm:109; do     # gasha01 nut2700 cephdash pvedash ubuntu01
  pve delete /cluster/ha/resources/$sid --yes
done
pve ha                                                # after: no `service` rows
```

Guests keep running; only HA management stops. LRMs drift to `idle` within minutes, and
from then on a network partition leaves nodes running with a read-only cluster config
instead of rebooting them. Log it in the incident report and `declare-activity end`.

## Do not

- Do not probe write permissions by attempting real writes on real ids. To learn what a
  call needs, call it against a **non-existent** id (`vm:99999`) — PVE checks the
  privilege before the object and the `403` names it.
- Do not reboot nodes or open node shells (`termproxy`) from here even though the
  operator token allows it — a PVE node reboot is a worker outage plus, on the twins, a
  Ceph mon down. That is Tom's call, every time.
- Do not act on guests other than the three Talos workers without a reason you would
  write in a PR; `--any` exists for reads and for the HA removal above.
- Do not copy tokens out of other pods. The exporter pod holds the read token too; the
  sanctioned path is the pod env this runbook describes.
