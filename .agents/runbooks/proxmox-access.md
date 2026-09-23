# Proxmox access from dev-env (`pve`)

Design + decisions: `.agents/sagas/dev-env/backlog/14-proxmox-access.md`. This is the
operating manual.

## What you have

| Tier | Credential | Can | Cannot |
|---|---|---|---|
| READ | `$PVE_TOKEN_ID` / `$PVE_TOKEN_SECRET` (`prometheus@pve!exporter`, PVEAuditor) | every `GET`: cluster/HA status, quorum, node uptimes, guest placement, `onboot` per guest | any write |
| OPERATOR | `$PVE_OPERATOR_TOKEN_ID` / `$PVE_OPERATOR_TOKEN_SECRET` (`dev-env@pve!operator`), **live since 2026-09-18** (ExternalSecret `dev-env-proxmox-operator` → Secret `dev-env-proxmox-operator-secret`; NB the 1Password `dev-env` fields are `PROXMOX_OPERATOR_TOKEN_*` while the env vars are `PVE_OPERATOR_TOKEN_*` — the ES remaps them, so `PROXMOX_*` never appears in the pod and finding none there is expected) | HA resources (remove/adjust), `onboot`, start/stop/reset on guests; PVE-side it is root-equivalent (`Sys.Console`, Tom's ruling 2026-09-09) | by **rule**, not ACL: node reboots, node shells, anything on guests other than the three Talos workers without `--any` (the helper refuses ids ≠ 103/108/113 as a typo guard) |

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
pve --yes vm 108 onboot 1  # operator tier
pve --yes vm 108 start     # operator tier
pve --yes vm 108 reset     # operator tier — a HARD reset; declare-activity first
pve get /cluster/ha/status/current            # raw API, any path
pve --ro get /nodes/twin-top/status
```

**Flag order (2026-09-22):** the global flags — `--ro --yes --raw --any --node <name>` —
are parsed by a loop that runs **before** the verb and stops at the first non-flag word.
A trailing `--yes` is therefore passed through as a path/parameter and the write is refused
with *"add --yes to perform this write"*. Put every global flag **before** the verb:
`pve --yes delete /cluster/ha/resources/vm:104`, not `pve delete … --yes`. (A dev-env PR
making them position-independent is held as a draft — it bounces the pod.)

Writes print the resolved URL and refuse without `--yes`. Anything disruptive (start,
stop, reset) — `declare-activity start … --scope <worker>` first, the same as a pod
delete.

## Remedy: a worker is down after a PVE fence

Signature: `PVENodeRebooted` for several nodes within a minute, workers NotReady, and
`pve guests` shows a Talos worker `stopped`. (`node-out-of-service` handles the
Kubernetes side after 8 min; this is the Proxmox side.)

1. `pve guests` — which worker is stopped, and is `onboot` 1? (All three are 1 since
   2026-09-09; if one is 0, `pve --yes vm <id> onboot 1`.)
2. `pve nodes` — is its host up and quorate? If the host is down, nothing here helps;
   that is a physical/UPS problem — page.
3. `pve --yes vm <id> start`, then `kubectl get node -w` until Ready. Log it.

## Remedy: the fence itself (DONE 2026-09-22; operator tier)

**Executed 2026-09-22 12:04Z** from the dev-env pod, declared `act-120438-1098241`: all
five HA resources removed, `pve ha` read back empty, LRMs `active`/`idle` on all five
nodes, quorum OK, no guest restarted. The fence is disarmed — a partition now leaves the
PVE nodes running with a read-only cluster config. See the 2026-09-22 addendum in
`.agents/reports/incident-2026-09-09-worker-host-outage.md`. The procedure below stays for
a **re-arm** scenario (if HA resources are ever added back and need removing again).

The standing operator token can do this (Q-1 ruling). It is a planned change, not an
emergency: `declare-activity start "removing PVE HA resources" --scope proxmox --ttl 30m`,
then:

```bash
pve ha                                                # before
for sid in vm:104 ct:105 ct:106 ct:107 vm:109; do     # gasha01 nut2700 cephdash pvedash ubuntu01
  pve --yes delete /cluster/ha/resources/$sid         # --yes BEFORE the verb (see "Flag order")
done
pve ha                                                # after: no `service` rows
```

Guests keep running; only HA management stops. LRMs drift to `idle` within minutes (the
transient `deleting` service rows cleared in ~30 s on 2026-09-22), and from then on a
network partition leaves nodes running with a read-only cluster config instead of
rebooting them. Log it in the incident report and `declare-activity end`.

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

## SSH tier (`hw-ssh`) — the hardware itself, PVE nodes + HaynesTower (2026-09-17)

Why it exists: the 2026-09-17 GPU swap on HaynesIntelligence needed `dmidecode -t slot`,
`lspci -vv` and the previous-boot `journalctl -b -1` on the **host** — no PVE API call runs
host commands — and PVE 8 lets only `root@pam` set a raw `hostpci` id (a non-root token
needs a resource mapping root creates first). Tom's ruling the same day: *"all my non-talos
hardware can be managed by dev-env models"*, so the same key covers the Unraid NAS.

| Host | Login | Tier |
|---|---|---|
| haynesintelligence, twin-top, twin-bottom, pve04, pve-filet02 (`.haynesnetwork`) | `dev-env`, key-only | sudo allowlist below; `sudo qm` makes it root-equivalent in practice — the tier of the operator token (Q-1) |
| haynestower (`.haynesnetwork`, Unraid) | `root`, key-only | Unraid has no other SSH user |

One ed25519 key, `~/.ssh/dev-env-hw`, written by dev-init from `HW_SSH_PRIVATE_KEY_B64`
(1Password `dev-env`, top-level field = base64 of the private-key file on one line).

```bash
hw-ssh list
hw-ssh haynesintelligence sudo dmidecode -t slot        # slot ↔ bus address
hw-ssh haynesintelligence 'sudo journalctl -b -1 -p err' # previous boot's errors
hw-ssh pve-all 'sudo qm list'                            # all five nodes
hw-ssh haynestower 'tail -50 /var/log/syslog'
hw-ssh haynesintelligence                                # interactive shell as dev-env (never `sudo -i`)
```

Rules: read first; `declare-activity` before `qm stop|start|set`, `pct`, `ha-manager`, or
anything touching the Unraid array; never reboot a node or stop the array from here; never
open an interactive root shell. Sudo allowlist (resolved per node at install time — a tool
that is not installed is simply absent): `dmidecode lspci journalctl dmesg sensors smartctl
nvme zpool zfs qm pct pvesh pvecm pvesm ha-manager ipmitool`. Extending it is a PR here plus
Tom re-running the node script.

### Provisioning (Tom, once)

1. **Key** (laptop):
   ```bash
   ssh-keygen -t ed25519 -a 64 -N '' -C dev-env-hw -f ~/.ssh/dev-env-hw
   base64 < ~/.ssh/dev-env-hw | tr -d '\n'; echo      # → 1Password field value (one line)
   cat ~/.ssh/dev-env-hw.pub                           # → PUBKEY for the two scripts below
   ```
2. **1Password** item `dev-env`: add a TOP-LEVEL text field labelled exactly
   `HW_SSH_PRIVATE_KEY_B64` = that base64 line. (Nested/section fields do not resolve.)
3. **PVE — on any ONE node as root** (fans out to the whole cluster over the standard
   root-to-root node SSH; idempotent, safe to re-run):
   ```bash
   PUBKEY='ssh-ed25519 AAAA…  dev-env-hw'        # ← paste the .pub line
   setup() {
     set -euo pipefail
     id dev-env >/dev/null 2>&1 || useradd -m -s /bin/bash dev-env
     install -d -m 700 -o dev-env -g dev-env /home/dev-env/.ssh
     printf '%s\n' "$PUBKEY" > /home/dev-env/.ssh/authorized_keys
     chown dev-env:dev-env /home/dev-env/.ssh/authorized_keys && chmod 600 /home/dev-env/.ssh/authorized_keys
     passwd -l dev-env >/dev/null 2>&1 || true      # key-only
     cmds=""
     for c in dmidecode lspci journalctl dmesg sensors smartctl nvme zpool zfs qm pct pvesh pvecm pvesm ha-manager ipmitool; do
       p="$(command -v "$c" 2>/dev/null || true)"; [ -n "$p" ] && cmds="${cmds:+$cmds, }$p"
     done
     printf 'dev-env ALL=(root) NOPASSWD: %s\n' "$cmds" > /etc/sudoers.d/dev-env
     chmod 440 /etc/sudoers.d/dev-env && visudo -cf /etc/sudoers.d/dev-env >/dev/null
     echo "$(hostname): dev-env ok — sudo: $cmds"
   }
   ( setup )      # subshell: the function's set -e must not leak into your interactive shell
   # Fan-out over the cluster's own root SSH. /etc/pve/.members lists the LIVE members
   # with IPs (/etc/pve/nodes/ keeps stale dirs of long-removed nodes — pve01..03 here —
   # and node NAMES are not in DNS). `ssh -n` so ssh does not eat the loop's stdin;
   # StrictHostKeyChecking=no because root's known_hosts on a PVE node does not carry
   # the peers under these names/IPs (PVE's own tooling uses HostKeyAlias) — one-time,
   # key-auth-only, on the LAN.
   python3 -c 'import json;d=json.load(open("/etc/pve/.members"));[print(n,v["ip"]) for n,v in d["nodelist"].items() if v.get("online") and n!=d["nodename"]]' \
   | while read -r n ip; do
       ssh -n -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no "root@$ip" \
         "PUBKEY='$PUBKEY'; $(declare -f setup); setup" \
         || echo "## $n ($ip) failed — paste the setup block on it directly"
     done
   ```
   (Do not write `!!` inside double quotes in that shell — history expansion rewrites it,
   which is what the first version of this block did.)
4. **Operator API token, same shell** (backlog 14 PR B step 1 — 3 minutes, prints the secret ONCE):
   ```bash
   pveum role add DevEnvOperator -privs "Sys.Audit Sys.Console VM.Audit VM.Config.Options VM.PowerMgmt"
   pveum user add dev-env@pve
   pveum acl modify / -user dev-env@pve -role DevEnvOperator
   pveum acl modify / -user dev-env@pve -role PVEAuditor
   pveum user token add dev-env@pve operator -privsep 0
   ```
   then 1Password `dev-env`, two more TOP-LEVEL fields: `PROXMOX_OPERATOR_TOKEN_ID` =
   `dev-env@pve!operator`, `PROXMOX_OPERATOR_TOKEN_SECRET` = the uuid it printed.
5. **HaynesTower** (Unraid terminal — web UI `>_` or `ssh root@haynestower`; Settings →
   Management Access → SSH must be *Yes*). `/boot/config/ssh/root.pubkeys` is what Unraid
   re-installs to `/root/.ssh/authorized_keys` on every boot:
   ```bash
   mkdir -p /boot/config/ssh /root/.ssh && chmod 700 /root/.ssh
   echo 'ssh-ed25519 AAAA…  dev-env-hw' >> /boot/config/ssh/root.pubkeys
   cat /boot/config/ssh/root.pubkeys > /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys
   ```
6. Merge the held-draft dev-env PR (it bounces the pod). **Not before steps 2 and 4's
   fields exist** — an ExternalSecret against a missing field fails the Kustomization and
   pages (2026-09-09 ×3).

### Verify (first session after the bounce)

```bash
hw-ssh list
hw-ssh pve-all 'hostname; sudo -n dmidecode -t slot | grep -c Designation'
hw-ssh haynestower 'uname -a; uptime'
pve get /access/permissions --raw | grep -o 'Sys.Console'   # operator token live
```
`hw-ssh: no key at ~/.ssh/dev-env-hw` = field missing or pod not bounced; `Permission
denied (publickey)` on one PVE node = the fan-out skipped it, re-run the block on that node;
`sudo: a password is required` = sudoers file missing on that node.

The GPU swap itself, once this is live: `hw-ssh haynesintelligence sudo dmidecode -t slot`
gives slot ↔ `0000:41:00.0`; after the physical swap, `sudo qm set 103 -hostpci0
0000:<new>:00,pcie=1` (VM stopped, `declare-activity` first), `sudo qm start 103`, then
`kubectl get node talosw01` and `nvidia-smi -L` from a GPU pod should show two boards.
