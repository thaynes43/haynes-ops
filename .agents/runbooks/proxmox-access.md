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
twin-bottom (external Ceph for Prometheus/Loki + NFS). The twins pass their Arrow Lake
iGPUs into talosw02/03 for Plex. Never add `x-vga=1` to their `hostpci0`, because it
wedges the GPU at boot (#3833). The config of record and the recovery are in
[plex-igpu-transcode](plex-igpu-transcode.md). If the owner approves a twin host reboot,
read its *Before rebooting a twin host* first: twin-bottom also runs gasha01.

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

**Flag order (2026-09-22):** global flags `--ro --yes --raw --any --node <name>`
may appear before or after the verb. Both `pve --yes delete <path>` and
`pve delete <path> --yes` set write confirmation. The position-independent parser
is deployed; a trailing flag is no longer treated as an API parameter.

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

## SSH tier (`hw-ssh`) — the hardware itself, PVE nodes + HaynesTower + PiKVM (2026-09-17)

Why it exists: the 2026-09-17 GPU swap on HaynesIntelligence needed physical PCI
inventory from `lspci -vv` and the previous-boot `journalctl -b -1` on the **host** — no PVE API call runs
host commands — and PVE 8 lets only `root@pam` set a raw `hostpci` id (a non-root token
needs a resource mapping root creates first). Tom's ruling the same day: *"all my non-talos
hardware can be managed by dev-env models"*, so the same key covers the Unraid NAS.

| Host | Login | Tier |
|---|---|---|
| haynesintelligence, twin-top, twin-bottom, pve04, pve-filet02 (`.haynesnetwork`) | `dev-env`, key-only | sudo allowlist below; `sudo qm` makes it root-equivalent in practice — the tier of the operator token (Q-1) |
| haynestower (`.haynesnetwork`, Unraid) | `root`, key-only | Unraid has no other SSH user |
| pikvm (`.haynesnetwork`, fixed IP 192.168.40.33; alias `kvm`), added 2026-10-03 | `root`, key-only | console + ATX on every bare-metal master through the TESmart KVM, so **root-equivalent on the masters**, the same tier as the operator token. Rules in [PiKVM](#pikvm) below |

One ed25519 key, `~/.ssh/dev-env-hw`, written by dev-init from `HW_SSH_PRIVATE_KEY_B64`
(1Password `dev-env`, top-level field = base64 of the private-key file on one line).

```bash
hw-ssh list
hw-ssh haynesintelligence 'sudo -n lspci -nnk'          # PCI devices and driver bindings
hw-ssh haynesintelligence 'sudo -n qm config 103'       # existing passthrough entries
hw-ssh haynesintelligence 'sudo -n dmidecode -t slot'    # SMBIOS slot records; empty on this host
hw-ssh haynesintelligence 'sudo journalctl -b -1 -p err' # previous boot's errors
hw-ssh pve-all 'sudo qm list'                            # all five nodes
hw-ssh haynestower 'tail -50 /var/log/syslog'
hw-ssh pikvm 'systemctl status kvmd --no-pager'          # kvmd health
hw-ssh haynesintelligence                                # interactive shell as dev-env (never `sudo -i`)
```

Rules: read first; `declare-activity` before `qm stop|start|set`, `pct`, `ha-manager`, or
anything touching the Unraid array; never reboot a node or stop the array from here; never
open an interactive root shell. Sudo allowlist (resolved per node at install time — a tool
that is not installed is simply absent): `dmidecode lspci journalctl dmesg sensors smartctl
nvme zpool zfs qm pct pvesh pvecm pvesm ha-manager ipmitool`. Extending it is a PR here plus
Tom re-running the node script.

### Guest VM reads: gasha01

The PVE host login is `dev-env`, using the existing hardware key and the sudo
allowlist above. `sudo qm` provides root-equivalent VM administration. This SSH
authority is separate from the API token: an API `VM.Monitor` denial does not
establish that the authorized host SSH path is unavailable.

For gasha01 (VM104), resolve its current PVE host, then check the guest agent:

```bash
hw-ssh <current-pve-host> sudo -n qm guest cmd 104 ping
```

Guest-agent commands require a responding agent inside the VM. On 2026-10-10,
host SSH worked and VM104's config had `agent: 1`, but this ping reported
`QEMU guest agent is not running`. No guest SSH username, key acceptance or
privilege tier was verified. Confirm those facts before using
`hw-ssh --raw <verified-user>@gasha01.haynesnetwork <read-only-command>`; the PVE
host account does not establish the guest account or its privileges.

Guest SSH also requires the exact gateway name in both the pod's DNS allowlist
and its TCP22 `toFQDNs` rule. Change those through GitOps. The purpose is to
inspect the NFS export, permissions and directory quotas for the external
storage comparison. This does not provision a guest login or start its agent.

### PiKVM

The PiKVM drives the TESmart KVM wired to every bare-metal master: video and keyboard on
each master's console, plus ATX power and Wake-on-LAN through kvmd's GPIO. Root over SSH
reaches all of that, so treat a session on it like the operator token. Its config source
is `pikvm.yaml` at the repo root; `docs/cluster/pikvm-readme.md` has the background. PiKVM
OS keeps its root filesystem read-only: `rw` remounts it writable, `ro` puts it back.

Rules:
- **Read first.** `systemctl status kvmd`, `journalctl -u kvmd`, `cat
  /etc/kvmd/override.yaml` and `kvmd -m` (the merged config, all overrides applied) are all
  read-only and need no `rw`. The box runs kvmd 4.20 (`pacman -Q kvmd`, 2026-10-03), which
  has `-m` but not the newer `-M`.
- **`rw` only to deploy the config**, and `ro` straight afterwards, every time. Never leave
  the box writable. If `ro` fails (a process holds a file open), retry `hw-ssh pikvm ro`.
- **`declare-activity` before** switching a KVM channel or restarting kvmd.
- **Never use ATX power, ATX reset or Wake-on-LAN on a node, or send keystrokes to a node
  console, without Tom's explicit go-ahead in that session.** Those are a master reboot or
  a typed command on a master, not a PiKVM change.

Deploy `pikvm.yaml` (it goes to `/etc/kvmd/override.yaml`, then `systemctl restart kvmd`,
as the file's own header and the readme say). Run it from a worktree at the repo root:

```bash
# 1. Read first: the box against the repo. Tom keeps the repo copy in sync by hand, so a
#    line on the box that the repo lacks is drift: stop and ask him, never overwrite it.
hw-ssh pikvm cat /etc/kvmd/override.yaml | diff -u - pikvm.yaml
# 2. Declare, back up to /tmp (tmpfs, writable while ro), then rw → write → ro.
#    `ro` runs even if the write fails.
declare-activity start "pikvm: deploy pikvm.yaml + restart kvmd" --scope pikvm --ttl 20m
hw-ssh pikvm 'cp /etc/kvmd/override.yaml /tmp/override.yaml.bak && rw && { cat > /etc/kvmd/override.yaml; rc=$?; ro; exit $rc; }' < pikvm.yaml
# 3. Check that kvmd parses the merged config, then restart it and confirm it is up.
hw-ssh pikvm 'kvmd -m >/dev/null && systemctl restart kvmd && sleep 5 && systemctl is-active kvmd'
declare-activity end <id>
```

If step 3 fails at `kvmd -m`, kvmd is still running the old config. Restore the backup
before anything restarts it:
`hw-ssh pikvm 'rw && { cp /tmp/override.yaml.bak /etc/kvmd/override.yaml; rc=$?; ro; exit $rc; }'`.

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
6. **PiKVM** (done 2026-10-03). PiKVM OS lets root log in over SSH; its root filesystem
   is read-only, so wrap the write in `rw`/`ro`:
   ```bash
   rw
   mkdir -p /root/.ssh && chmod 700 /root/.ssh
   echo 'ssh-ed25519 AAAA…  dev-env-hw' >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys
   ro
   ```
7. Merge the held-draft dev-env PR (it bounces the pod). **Not before steps 2 and 4's
   fields exist** — an ExternalSecret against a missing field fails the Kustomization and
   pages (2026-09-09 ×3).

### Verify (first session after the bounce)

```bash
hw-ssh list
hw-ssh pve-all 'sudo -n qm list'                       # allowed sudo read on every PVE node
hw-ssh haynestower 'uname -a; uptime'
hw-ssh pikvm 'hostname; pacman -Q kvmd; ls -l /etc/kvmd/override.yaml'
pve get /access/permissions --raw | grep -o 'Sys.Console'   # operator token live
```
`hw-ssh: no key at ~/.ssh/dev-env-hw` = field missing or pod not bounced; `Permission
denied (publickey)` on one PVE node = the fan-out skipped it, re-run the block on that node;
`sudo: a password is required` = sudoers file missing on that node.
The guest-list probe checks an allowed command without depending on optional SMBIOS
records. An empty slot inventory is not evidence that SSH or sudo failed.

Before a GPU swap, record the live PCI devices, VM configuration and network interface
names. A motherboard slot label does not establish a PCI address; this host's SMBIOS
currently supplies no slot records.

1. Read the GPU and audio functions with `hw-ssh haynesintelligence 'sudo -n lspci -nnk'`
   and the existing mapping with `hw-ssh haynesintelligence 'sudo -n qm config 103'`.
   Verify that the intended functions form an isolated IOMMU group. Use the observed
   GPU address in these paths; for the verified `0000:41:00.0` GPU:

   ```bash
   hw-ssh haynesintelligence 'readlink /sys/bus/pci/devices/0000:41:00.0/iommu_group'
   hw-ssh haynesintelligence 'ls /sys/bus/pci/devices/0000:41:00.0/iommu_group/devices'
   ```

   The group link ends in `42` and its members are only the GPU `.0` and audio `.1`.
   After the October 2026 A/C swap, both functions remained at `0000:41:00.0` and
   `0000:41:00.1`, used
   `vfio-pci`, and were the only members of group 42. The existing VM entry was
   `hostpci1: 0000:41:00,pcie=1`, so no mapping change was needed.
2. If the intended address has changed, declare the disruptive work and stop the VM
   normally before editing its existing `hostpci` entry. Preserve every other PCI and
   USB mapping; do not assume the GPU uses `hostpci0`. Start the VM normally after
   checking the resulting configuration and storage health.
3. Verify `talosw01` is Ready and check `nvidia-smi -L` from its GPU pod. The expected
   sole rack GPU after this swap is Card A,
   `GPU-6ff9702a-1b2c-6094-5a85-00aa1924c55a`. Check fresh boot logs for GPU and PCIe
   errors before applying load.

GPU slot changes can also alter PCI-derived NIC names. During the failed boot in this
maintenance window, the NICs appeared as `enp65s0` and `enp69s0`, while
`/etc/network/interfaces` still expected `enp66s0` for `vmbr0` and `enp70s0` for `vmbr2`.
Both bridges failed to start. Moving the GPU restored the expected names. Compare
actual interfaces, PCI addresses and MACs with the saved network configuration and
boot journal before changing the configuration. A static KVM startup image alone does
not establish a kernel hang or a defective GPU slot.

## Source-only bounded read ledger

`scripts/dev-env/bounded_backend_workflow.py` is a mandatory **OFFLINE** local
phase/intent/budget/freshness/owner-reference guard for a future qualified workflow.
It implements no transport, credential access, grant request, policy publication or
runtime activation. No current operations requester or accepted persistent operations
owner exists. Source tests do not establish live workflow adoption, provider/agentd
process persistence, access approval, or storage completion. Runtime hardening belongs
to the separate 153323/OP355 lane. Developing this source in an engineering Task is
allowed; running operations as a Task owner is refused.

The old **160852 four-GET approval is exhausted and nontransferable**. Any future
4 discovery + 1 separately planned revalidation + 1 metrics scope needs new explicit
approval bound to the real future identity, scope and attempt. Every local mutation
and admission compares those three references. These comparisons are not
authentication or consent. A trusted external adapter must verify the actual caller,
its ready/live persistent owner observation, and every receipt/body/source claim.
Owner caps and ledger flags prove neither liveness nor acceptance. There is no
source-author 1800s limit or arbitrary 4h operational stop. Turn-end, recoverable
blockers and lost owners remain nonterminal, without successor permission.

Use a private local directory on a filesystem supporting POSIX flock, fsync and
atomic same-directory replacement. Init and CAS take the same nonblocking exclusive
lock. A competing writer returns conflict and must reload; it must never retry a
backend read. Complete payload writes, file fsync, atomic replacement and directory
fsync finish **before** dispatch advice. Revision mismatch fails without writing.
A crash after durable intent loses that slot conservatively, even if no read happened.
Do not delete lock/ledger files, reset revisions, copy a live ledger to create another
attempt, or bypass the mandatory guard. This is a cooperative local durability guard,
not protection against a caller rewriting its own files or a hostile filesystem.

Sequence and external prerequisites:

1. Initialize expected FSID and 1–4 exact canonical IPv4 TCP `9283/metrics`
   destinations, with **no known active manager**. Externally verified already
   covered endpoints can be declared as a subset. There is no wildcard, alternate
   port/path, fallback or endpoint discovery permission.
2. Under a future explicitly approved publication bundle, prepare up to three
   alternative actual candidate PR heads **before any discovery lease/read**.
   Publication, current-head source review and actual required CI remain external.
   Record the same full SHA for candidate, reviewed head and CI head, with both
   `Flux Local - Success` and `Diff Scope - Success` successful. Private review,
   missing/pending/failed/skipped CI or another reviewed head cannot qualify.
   An already covered route requires no fabricated candidate, PR, head or merge.
3. Admit and complete four discovery intents in order: `version`, `permissions`,
   `ceph-status`, `ceph-pool`. Run the guard **before each existing bounded read**;
   only `ok=true, dispatch=true` permits the separately authorized adapter to proceed.
   Record the actual outcome **after** that exact read, before another admission.
   Pre-dispatch admission records no response timestamps or proof. Only the strict
   `success` outcome advances normal progression; `denied`, `unknown`, `uncertain`,
   `failure` and a missing outcome block it. Admission decisions `pending`, `denied`,
   `unknown` and `uncertain` consume the slot without dispatch. Exact replay never
   dispatches again; a conflicting replay fails. No retries or implicit new lease.
4. After all four successful outcomes, select the existing covered route or exactly
   one matching prepared candidate using the fresh externally verified discovery
   FSID, active manager, exact advertised endpoint and receipt. At most one selection
   and new activation is possible. The helper compares these against stored proof;
   it does not interpret the raw body or prove the manager is available.
5. For a new route, record selected reviewed PR head separately from actual squash
   merge SHA. Flux source and applied proof must equal the **merge SHA**. Existing
   coverage skips that merge gate. Both routes wait for externally observed
   deployment, metrics grant and CNP readiness. Unknown future reversal heads are
   never pre-certified; actual removal CI is a later external gate.
6. Admit the separately explicit, at most one post-delivery status revalidation.
   Successful post-read outcome records the immutable original request/response
   interval, body SHA256, receipt, FSID, manager, exact endpoint and caller references.
   CI can exceed 300s or 1800s before discovery; deployment can exceed 300s before
   this revalidation. Neither must fit the manager proof guard.
7. Admit at most one metrics intent using that revalidation receipt and the same
   FSID, selected manager and exact endpoint. Freshness conservatively starts at the
   original request, not a later response or caller-supplied reset. Require the full
   **10 seconds** inside the **300 second** guard. Stale/future intervals, clock
   rollback, eight seconds remaining, wrong FSID, manager drift or an unknown/different
   endpoint give zero metrics dispatch. The response includes `read_budget_s=10`,
   `proof_deadline` and `transport_deadline` (the earlier of proof expiry and the
   caller deadline minus cleanup reserve). Immediately before the real read, the
   future adapter must recheck real current time, clock continuity, caller/liveness,
   readiness and approval, and require current time + 10 <= transport deadline.
   It must enforce a 10s total read bound and never reset the proof origin.

Reserve cleanup against an **explicit caller deadline** before new stages. The
remaining time must cover the requested stage budget plus the reserve. PVE admissions
allow at least the original 12s read budget; metrics admissions require 10s. Use
`--stage-budget-s` for a larger actual stage/publication estimate. CI estimates are
not guaranteed deadlines; an external publication adapter must check the remaining
reserve again at its real action edge. `publish` always refuses here. The plan deadline
blocks new stages but never prevents recording outcomes, blockers or exact residuals.
Cleanup can remain outstanding beyond it; extending a local estimate grants no access.

Recoverable technical dependencies have one explicit local recovery path. Record
`record-blocker --kind recoverable`; the ledger assigns `blocker-1` through
`blocker-8` and preserves each record for the entire attempt. After the qualified
external adapter verifies the prerequisite, the same bound identity/scope/attempt
may record:

```bash
step resolve-blocker --blocker-id blocker-1 --reason dependency-restored \
  --prerequisite-ref verified-external-receipt --verified-at "$NOW"
```

The `step` fixture below supplies the ledger's expected revision and caller binding.
Reason and reference are nonsecret tokens of at most 80 characters. The external
observation time must be between the blocker's recorded time and current time.
Resolution stores that time, current resolution time and caller binding once; it
never deletes a blocker or resets the eight-record limit. Every other unresolved
blocker still gates normal work. A blocker from the earlier offline format without
an ID/event time stays unresolved and cleanupable; this command does not invent
missing evidence or migrate it implicitly.

Resolution requires the existing observed ready/live persistent owner, monotonic
clock and caller deadline with cleanup reserve plus at least the next read budget
(12s, or 10s after revalidation), or a larger `--stage-budget-s`. It refuses a
`turn-end`/`lost-owner`, any such record elsewhere in the attempt, a consumed intent
without successful outcome, or an already consumed metrics slot. A delayed real
successful outcome may still be recorded for a dispatched pending intent; no
non-success outcome can be overwritten. Exhausted or uncertain attempts remain
blocked and cleanupable. No resolution changes owner readiness/liveness/loss,
intents, slot counts, admission decisions, outcomes, receipts, proof origins,
access, approval or successor authority. A fresh external caller/liveness/readiness
observation remains the adapter's separate obligation at every action edge.
Resolution only removes that dependency gate: stale proof or less than a full 10s
still denies and consumes metrics; clock/reserve gates continue to refuse admission.
It grants no retry, backend read, new attempt or human approval.

Both ledger and audit inputs use a bounded regular-file read with no final symlink
following and nonblocking open. Inputs over 64KiB, FIFOs, symlinks, malformed or
truncated JSON, nonfinite constants and missing/incorrect required record types
return structured `ok=false, dispatch=false` without changing ledger bytes or
revision. File type/shape checks establish neither authenticity nor completeness.

Synthetic local CLI example (values are fixtures, not qualified operational evidence):

```bash
LEDGER=./bounded-read-ledger.json
NOW=1700000000
wf() {
  python3 -I -B scripts/dev-env/bounded_backend_workflow.py --ledger "$LEDGER" "$@" \
    --identity synthetic-owner --scope synthetic-scope --attempt attempt-1 --now "$NOW"
}
step() {
  local revision
  revision=$(python3 -I -B -c 'import json,sys; print(json.load(open(sys.argv[1]))["revision"])' "$LEDGER")
  wf "$@" --expect-revision "$revision"
}
wf init --owner-kind persistent --owner-liveness observed --owner-ready --owner-live \
  --fsid fsid-synthetic --endpoint 203.0.113.10:9283/metrics --planned-revalidation
step reserve-cleanup --seconds 180 --deadline 1700010000 --ci-estimate-s 900
NOW=1700002500
HEAD=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
step prepare-candidate --head "$HEAD" --reviewed-head "$HEAD" --ci-head "$HEAD" \
  --pr pr-synthetic --manager mgr-synthetic --endpoint 203.0.113.10:9283/metrics \
  --flux-local success --diff-scope success --ci-started-at 1700000000 --ci-finished-at "$NOW"
step admit --slot version --intent-id intent-version --decision admit
# The future approved adapter performs only that read here, after durable advice.
# Then record its externally verified actual receipt, interval and body digest:
BODY_SHA=cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
step record-outcome --intent-id intent-version --outcome success \
  --request-at "$NOW" --observed-at "$NOW" --receipt receipt-version --body-sha256 "$BODY_SHA"
```

Repeat separate guard/outcome pairs for `permissions`, `ceph-status`, `ceph-pool`.
Successful `ceph-status` and `revalidation` outcomes also require
`--observed-fsid fsid-synthetic --observed-manager mgr-synthetic
--observed-endpoint 203.0.113.10:9283/metrics`. Failure outcomes must describe what
actually happened; never synthesize success to advance. Only after discovery:

```bash
step select-candidate --branch new --verified-fsid fsid-synthetic \
  --verified-manager mgr-synthetic --verified-endpoint 203.0.113.10:9283/metrics \
  --proof-receipt receipt-ceph-status
MERGED=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
step record-merge --pr-head "$HEAD" --merged-sha "$MERGED" \
  --flux-source "$MERGED" --flux-applied "$MERGED"
step observe-delivery --deployment ready --grant ready --cnp ready
step admit --slot revalidation --intent-id intent-revalidation --decision admit
# Perform exactly the qualified planned status read; record successful outcome
# with its original interval, receipt-revalidation, digest and observed fields.
step admit --slot metrics --intent-id intent-metrics --decision admit \
  --observed-fsid fsid-synthetic --observed-manager mgr-synthetic \
  --observed-endpoint 203.0.113.10:9283/metrics --proof-receipt receipt-revalidation
# Edge recheck, bounded metrics read, then record-outcome for intent-metrics.
```

For existing coverage, initialize with `--covered-endpoint` matching an allowlisted
endpoint; use `select-candidate --branch existing` and skip prepare/merge if no new
route is needed. The other gates remain mandatory. All commands expose `--help`.

Cleanup remains writable after partial execution, blockers, owner loss and deadline
expiry. `record-residual --uid <grant-uid> --owner-class own|other --active true|false
--expires-at <original-expiry> --cnp-uid <exact-uid> --cnp present|absent --receipt <ref>`
records an observation, never a release. Inactive grants require exact CNP absence
and `--reason natural-expiry|released`. Only own grants may be recorded released;
other grants can be recorded naturally expired after their immutable expiry. Removing
standing policy does **not** revoke active ordinary standing grants.

`evaluate-clean --audit-file <qualified-local-audit.json>` requires a bounded complete
external audit, not a positive status string. Its schema is:

```json
{
  "binding": {"identity": "synthetic-owner", "scope": "synthetic-scope", "attempt": "attempt-1"},
  "receipt": "audit-synthetic", "observed_at": 1700003000, "complete": true,
  "policy": {"ref": "policy-synthetic", "present": false},
  "grants": [{"uid": "other-grant", "owner_class": "other", "expires_at": 1700002900,
              "active": false, "cnp_uid": "cnp-synthetic", "cnp_present": false}],
  "cnps": [{"uid": "cnp-synthetic", "present": false}]
}
```

The audit must be at least as recent as the last recorded event and no later than
current time. Record every newly found own/other class grant first. The exact UID set,
classes, original expiries, active states and CNP UIDs/presence must match residuals.
Missing/incomplete audit, present policy, any present CNP or active grant cannot be
clean. The helper records the audit and digest, but its authenticity/completeness and
actual resource absence are still qualified external audit gates. `clean` is only a
conditional local cleanup report; it never completes operations or releases another
owner. A new residual observation or normal stage invalidates the prior evaluation.

The existing private reader exposes `identity_guard()`, `read('pve',
'/nodes/pve04/ceph/status')`, `save(body, receipt)` and `url_for()`. Its `pve` CLI
bundles **all four GETs**; there is no single-status flag. Before any future access
ask, a NEW-IDENTITY/SAFE_END adapter must be independently reviewed and equivalence
checked. It must call identity_guard first, then exactly one approved read/save after
this durable unique intent. Preserve the original immutable source/constants and its
12s total/3s connect/8MiB/no retry/redirect/proxy/baseline-reader limits. The qualification
handoff is preserved externally at
`/home/dev/.shared/handoffs/storage-workflow-correction-20261102-1808/FUTURE-REVALIDATION-ADAPTER-PREREQUISITE.md`.
No existing reader function was executed for this source work.

Raw PVE/metrics FSID, active-manager availability, advertised endpoint and body/receipt
semantics stay externally verified. There is no fake parser, capacity oracle or admin
oracle here. Any future raw parser must retain 8MiB and allow legitimate additional
manager services. Capacity, physical/byte/object quotas, usable versus raw replication,
concurrent-write budgets and admin approval need external evidence/review. Missing
series are `UNKNOWN`, never zero/unlimited. Live versions and samples are unverified.

Continuous regression CI uses the current `ubuntu-26.04` runner pattern, pinned
checkout without persisted credentials, `contents: read`, five-minute timeout and
bounded concurrency. `Storage Workflow Fixtures` is a **delivery gate** alongside
normal `Flux Local - Success` and `Diff Scope - Success`; branch protection is unchanged.
It does not activate this guard in any live reader. Run its exact isolated command:

```bash
nice -n 19 python3 -I -B -m unittest discover -s scripts/dev-env -p test_bounded_backend_workflow.py -v
```

Tests use only synthetic local files, at most two competing workers and one focused
suite. No image builds, cluster tests, backend contact or credentials are involved.
