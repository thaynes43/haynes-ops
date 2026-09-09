# Proxmox access from dev-env (`pve`)

Design + decisions: `.agents/sagas/dev-env/backlog/14-proxmox-access.md`. This is the
operating manual.

## What you have

| Tier | Credential | Can | Cannot |
|---|---|---|---|
| READ | `$PVE_TOKEN_ID` / `$PVE_TOKEN_SECRET` (`prometheus@pve!exporter`, PVEAuditor) | every `GET`: cluster/HA status, quorum, node uptimes, guest placement, `onboot` per guest | any write |
| OPERATOR | `$PVE_OPERATOR_TOKEN_ID` / `$PVE_OPERATOR_TOKEN_SECRET` (`dev-env@pve!operator`), **present only after Tom fills the 1Password fields and the pod has bounced** | `onboot`, start/stop/reset on **talosw01 (103), talosw02 (108), talosw03 (113)** | anything on any other guest; HA config; node shells; node reboots — PVE answers `403` |

`pve` uses the operator token when it is set, else the read token. `pve --ro …` forces
the read token. Env is absent until PR B of backlog 14 has deployed; before that, the
API is reachable from the pod (CNP) but you hold no token — do not go looking for one in
other pods.

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

## Remedy: the fence itself (one-time, needs `Sys.Console`)

The standing operator token **cannot** do this on purpose. Per backlog 14 Q-1, HA
resources come off either by Tom (`ha-manager remove vm:104 ct:105 ct:106 ct:107
vm:109`) or by a one-shot Job holding a temporary `Sys.Console` token:

```
DELETE /cluster/ha/resources/vm:104      # gasha01
DELETE /cluster/ha/resources/ct:105      # nut2700
DELETE /cluster/ha/resources/ct:106      # cephdash
DELETE /cluster/ha/resources/ct:107      # pvedash
DELETE /cluster/ha/resources/vm:109      # ubuntu01 (already `ignored`)
```

Guests keep running; only HA management stops. Verify with `pve ha`: no `service` rows,
LRMs drift to `idle` within minutes, and from then on a network partition leaves nodes
running with a read-only cluster config instead of rebooting them. After that, delete
the temporary token (`pveum user token remove …`).

## Do not

- Do not probe write permissions by attempting real writes on real ids. To learn what a
  call needs, call it against a **non-existent** id (`vm:99999`) — PVE checks the
  privilege before the object and the `403` names it.
- Do not reboot nodes from here even if a token ever allows it — a PVE node reboot is a
  worker outage plus, on the twins, a Ceph mon down. That is Tom's call.
- Do not copy tokens out of other pods. The exporter pod holds the read token too; the
  sanctioned path is the pod env this runbook describes.
