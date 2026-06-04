# Talos + Omni gotchas (read before touching node/network config or upgrades)

Hard-won lessons from the 2026-06-04 "worker egress out the VPN NIC" + Talos
1.13.3 upgrade saga. These are silent, high-cost traps: the config looks right,
`lastconfigerror` is empty, and nothing fails loudly — it just doesn't work.
Read this before editing `kubernetes/main/bootstrap/omni/cluster-template.yaml`
or running a Talos/Kubernetes version bump.

Companion runbook: [`../runbooks/talos-version-upgrade.md`](../runbooks/talos-version-upgrade.md).

---

## 1. `deviceSelector.hardwareAddr` is CASE-SENSITIVE — MACs must be lowercase

`machine.network.interfaces[].deviceSelector.hardwareAddr` is matched
**case-sensitively** against the kernel link address, which is always
**lowercase** (`bc:24:11:74:58:0a`). An UPPERCASE MAC (`BC:24:11:74:58:0A`)
**matches nothing**, so the *entire* interface block — `dhcp`, `addresses`,
`routes`, `dhcpOptions.routeMetric`, `vip` — is **silently ignored**. No error,
`lastconfigerror: ""`, Omni may still report the machine fine.

- **Symptom we hit:** worker `eth1` (the `.30` VPN NIC) kept doing DHCP and
  installed a default route via `192.168.30.1`, stealing node egress out the
  VPN. The staged `dhcp:false` + static `192.168.30.10x` never applied. The
  earlier "virtio workers ignore `routeMetric`" theory was **wrong** — the eth0
  block (also uppercase) never matched either.
- **Why control planes worked:** their MACs were already lowercase
  (`58:47:ca:78:bf:f6`); only the workers were uppercase.
- **Fix:** lowercase every `hardwareAddr` in the template. It applies **LIVE**
  via `task omni:sync` (= `omnictl cluster template sync`) — **no upgrade, no
  reboot**. Verify with `talosctl -n <ip> get addresses` /
  `... get routes` (look for the static address + the default route on `eth0`).
- **Rule of thumb:** all MACs in this repo are lowercase. If you ever see an
  interface config "not applying," check MAC case **first**.

## 2. `install.extraKernelArgs` is a NO-OP under UKI — use Omni `kernelArgs`

Talos can boot via a **UKI** (Unified Kernel Image). Check with:
`talosctl -n <ip> get securitystate -o yaml` → `bootedWithUKI: true`.

Under UKI the kernel cmdline is baked into the (signed) image, so
`machine.install.extraKernelArgs` is **silently ignored** (Talos issue #10339).
This is independent of SecureBoot — we run `secureBoot: false` but still boot UKI.

- **Symptom we hit:** `net.ifnames=0` was in `install.extraKernelArgs` for every
  node, but the USB-recovered control planes (m01, m03) boot UKI, so it never
  took → their NICs came up as `enp3s0f0np0`/`enp91s0`… instead of `eth0/eth1`.
  Every macvlan `NetworkAttachmentDefinition` hardcodes `master: eth1` (IoT) /
  `eth0` (Sonos), so Multus failed with **`Link not found`** and
  home-assistant / zigbee2mqtt / esphome / zwave were stuck `Init:0/1`.
- **Fix:** set the **Omni-native `kernelArgs`** field on the `kind: Machine`
  block (sibling of `name`/`systemExtensions`/`patches`). Omni rebuilds the boot
  image with the arg and applies it via a **non-destructive reboot** (no wipe).
  Applies on the next Omni-driven reconcile.
- **Reboot-loop caveat (omni#2382):** only add `kernelArgs` where the arg is NOT
  already in the running cmdline. On a GRUB-booted node that already has
  `net.ifnames=0` baked in, a duplicate can cause an endless reboot loop. Check
  `talosctl -n <ip> read /proc/cmdline` before adding.
- **Fresh Image Factory installs DO bake it:** a clean install from the Omni ISO
  applied `net.ifnames=0` correctly (w01 came back UKI **with** `eth*`). It was
  only the older USB-recovery images that didn't. So: **bake `net.ifnames=0`
  into the schematic/image kernel args when generating re-image media.**

## 3. Wiping a VM loses its Omni identity (META partition) → re-registers as NEW

Omni's machine ID is the SMBIOS UUID when usable, otherwise a Talos-generated ID
stored in the **META partition**. A disk wipe (reinstall) erases META, so the
node **re-registers as a brand-new machine with a new UUID**, orphaning the
machine the cluster template references.

- Bare metal keeps its SMBIOS UUID (burned in) → reconnects with the same ID
  (this is why m01/m03 USB recoveries kept `88d0b080…` / `98290580…`).
- A **VM** may not — if the original ID came from META, a wipe yields a new one.
- **Fix (VM):** pin the SMBIOS UUID to the original machine ID **before**
  reinstall so it reclaims its identity:
  `qm set <vmid> --smbios1 uuid=<original-omni-machine-id>` (preserve any other
  `smbios1` fields). Then the unchanged cluster template matches it.
- **Re-provision flow:** in Omni, remove the (orphan) machine from the cluster →
  `task omni:sync` re-adds it. **Adding a machine to a cluster is what triggers
  the disk install** — a machine that's "already a member but disconnected" just
  sits there and Omni won't reprovision it.

## 4. Old install = tiny `/boot` → nvidia initramfs won't fit → upgrade reboot-loop

Old Talos installs use a GRUB layout with a **1.0 GB `BOOT` partition**. A
1.13.x initramfs bloated by `nvidia-open-gpu-kernel-modules` can't fit alongside
the existing slot → the installer fails writing `/boot/B/initramfs.xz`
(`no space left on device`) → the upgrade **reverts to the old version** →
Omni retries → **endless reboot loop on the OLD version** (looks "stuck").

- **Diagnose:** `omnictl machine-logs <machine-id> --log-format dmesg --tail 80`
  — look for `failed to install bootloader: ... no space left on device`.
- **Resizing the VM disk does NOT help:** `BOOT` is a fixed mid-table partition;
  only the last partition (`EPHEMERAL`/`/var`) auto-grows into new space.
- **Fix:** reinstall (fresh install repartitions to the UKI / ~2.2 GB EFI layout
  with room for the initramfs). See the upgrade runbook's re-image section.
- Fleet split that bit us: control planes + w01 (nvidia) need the big layout;
  w02/w03 (`i915`, small initramfs) fit the old 1.0 GB `BOOT` fine.

## 5. In maintenance mode, the eth1 VPN default-route bug breaks SideroLink

When a node boots the Omni ISO into **maintenance**, no cluster config is applied
yet, so every NIC does DHCP — including the `.30` VPN NIC (`eth1`), which can win
the default route. The node's egress (and the SideroLink/WireGuard tunnel to
Omni) then goes out the VPN with an unstable exit IP, so **Omni shows the machine
`connected: false` / `POWERED_OFF` and never starts the install.**

- **Tell:** node's own dashboard shows SideroLink ✓ / CONNECTIVITY ✓, but
  `omnictl get machinestatus <id>` says `connected: false`; logs spam
  `RouteSpecController ... Src:192.168.30.x Gateway:192.168.30.1 ... file exists`.
- **Fix:** disconnect the VPN NIC in Proxmox for the install
  (VM → Hardware → Network Device `net1` (vmbr2) → Disconnect/Remove), so egress
  goes out mgmt (`eth0`). Re-add it (same MAC) after the node is installed and on
  disk — the now-static `eth1` config means the bug won't recur.

---

## Quick triage map

| Symptom | Likely cause | Section |
|---|---|---|
| Static IP / `routeMetric` / interface config "not applying", no error | uppercase MAC in `deviceSelector` | 1 |
| NICs are `enpXsY` not `ethN`; Multus `Link not found`; IoT pods `Init:0/1` | UKI + `install.extraKernelArgs` no-op | 2 |
| Node shows as a new/duplicate machine after reinstall | META wiped, lost Omni identity | 3 |
| Node reboot-loops back to the OLD version after an upgrade | `/boot` too small for initramfs | 4 |
| Booted ISO, node says connected but Omni says offline / won't install | VPN NIC stealing egress in maintenance | 5 |

## Reset Ceph after node work

Node maintenance sets `ceph osd set noout` to suppress rebalancing. It shows as
`HEALTH_WARN noout flag(s) set` (the *only* warn once OSDs are back). When all
node work is done and `ceph -s` shows full mon quorum + all OSDs up +
`active+clean`, clear it: `kubectl -n rook-ceph exec deploy/rook-ceph-tools --
ceph osd unset noout`.
