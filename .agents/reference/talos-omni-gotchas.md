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

## 6. nut-client's config is in per-machine Omni patches, not the template → new node sticks in `booting`

The control planes carry `siderolabs/nut-client` in their template `systemExtensions`,
but the extension's `ExtensionServiceConfig` (`upsmon.conf`, which holds NUT
credentials) cannot go in this public repo. It lives in machine-level Omni config
patches made in the Omni UI: IDs `500-<uuid>`, labelled only
`omni.sidero.dev/machine: <machine-id>` (no cluster label, so template syncs never
touch them). A node that gets the extension from the template but has no such patch
waits for that config forever.

- **Tell:** the node is Ready in k8s and in etcd, yet `talosctl get machinestatus`
  stays at stage `booting`, Omni counts its machine set short (e.g. 3/5 ready), and
  `talosctl service ext-nut-client` shows `Waiting for extension service config`.
  Hit by talosm04 + talosm05 when they joined on 2026-10-03 (#3339). Stage `booting`
  means *any* service is still `Waiting`, so check `talosctl services` for others.
  For example, talosm04 also waited on `ext-nvidia-persistenced` ("Waiting for file
  /sys/bus/pci/drivers/nvidia") because its GPU does not enumerate (#3348).
  **It does not stay there:** 70 minutes into the boot, Talos gives up and reboots
  (section 7). Fix it inside that window.
- **Current state (2026-10-03, #3347):** each of the five masters has its own patch:
  `500-9f9b0486-…` (talosm01), `500-60c89b5b-…` (m02), `500-6ef5e5d8-…` (m03),
  `500-nut-client-talosm04`, and `500-nut-client-talosm05`. All five have the same payload:
  `MONITOR APC-2700W@192.168.40.12:3493 1 talos <password> secondary` plus a
  `SHUTDOWNCMD` line. `talos` is a dedicated `upsmon secondary` user on `nut2700`, the
  APC Smart-UPS X 3000 (the large UPS, which is where the masters belong, Tom
  2026-10-03), served from LXC ct:105 on twin-top. Its password exists only in
  ct:105's `/etc/nut/upsd.users` and in those patches. Keep the patches per machine:
  a machine-set-level patch alongside them would be a second `nut-client`
  `ExtensionServiceConfig` on the same machine.
- **Fix for a new master:** give it its own machine-level patch, a clone of an existing
  master's. dev-env's Omni account is a Reader, so the write goes through an
  Operator-key Job (the [egpu-test-node](../runbooks/egpu-test-node.md) §2 pattern).
  That Job reads the source patch and writes the clone itself, so the credentials
  never pass through a session or a log. If a new secret has to reach the Job, pipe
  it in with `kubectl exec -i <pod> -- sh -c 'cat > /work/pw' < file`, not through Job
  env. The dev-env image's system python has no PyYAML, so use `omnictl get -o json`,
  and `omnictl apply` accepts JSON. Never print a patch's `spec.data`; to inspect one,
  print its `MONITOR` line with the password field masked. Adding or changing this
  document restarts only `ext-nut-client`; no reboot (boot ids unchanged on all
  five masters, 2026-10-03).
- **Verify the login, not the state.** On 2026-10-03 the old talosm01-03 patches
  pointed at `HaynesTowerUPS@haynestower.haynesnetwork`, which no longer serves NUT.
  Every master's `ext-nut-client` still showed `Running` while it logged
  `connect failed: Connection refused` and protected nothing. The proof is the server's
  client list, which needs no credentials:
  `hw-ssh twin-top "sudo pct exec 105 -- upsc -c APC-2700W@localhost"` lists one IP
  per logged-in client. Also check `talosctl logs ext-nut-client` for
  `ACCESS-DENIED`/`refused`. `upsc -l <host>` and `upsc <ups>@<host>` read any NUT
  server. The 900 W Back-UPS is `APC-900W-01` on `nut01` (ct:124).
- **Adding a NUT user on ct:105:** append the stanza to `/etc/nut/upsd.users` (back it up
  first), then run `systemctl reload nut-server`. A plain `upsd -c reload` fails there
  (`fopen /run/nut/upsd.pid`) because systemd runs upsd with `-F` and it writes no
  pid file.

### UPS / NUT: topology and shutdown policy

- **Topology.** The APC Smart-UPS X 3000 (`APC-2700W`, NMC at 192.168.40.71, SNMPv3)
  is read by NUT 2.8.0 `snmp-ups` on `nut2700` (ct:105 on twin-top, served at
  192.168.40.12:3493). nut2700's own upsmon is the primary and logs in as `upsmon`.
  The secondaries are HaynesTower (Unraid nut-dw plugin; it logs in as `upsmon` but its
  `MONITOR` line says `slave`) and talosm01-05 (user `talos`, see above). With all of
  them logged in, `upsc -c APC-2700W@localhost` on ct:105 lists 7 IPs.
- **Policy (Tom, 2026-10-04): shut down on runtime left, not on time on battery.** The
  Generac generator and transfer switch usually restore power within a minute, so
  everything rides the battery and shuts down cleanly only if the generator fails.
  HaynesTower goes at 15 minutes of runtime left and the masters at 10 minutes. At about
  65% load the UPS reports roughly 2,500 s of runtime, so that is about 27 and 32
  minutes on battery.
- **Masters, 10 min: `/etc/nut/ups.conf` on ct:105, section `[APC-2700W]`:**
  `ignorelb` and `override.battery.runtime.low = 600`. The driver ignores the NMC's own
  low-battery flag (which fires at 120 s) and raises LB itself when
  `battery.runtime < battery.runtime.low`. On OB+LB the primary sets FSD, and every
  secondary still logged in shuts down. Check with `upsc APC-2700W@localhost`: it should
  show `battery.runtime.low: 600` and `driver.flag.ignorelb: enabled`. The driver logs
  `dstate_setflags: base variable (battery.runtime.low) is immutable` about every 30 s.
  That is the override holding, because snmp-ups keeps trying to mark the NMC value
  writable; it is not a fault. The NMC's own threshold stays at 120 s. Never change it
  with `upsrw`, and never `upscmd` the UPS from an agent.
- **Editing `ups.conf` restarts the driver by itself.** `nut-driver-enumerator.path`
  watches the file. When a section changes, it re-creates `nut-driver@APC-2700W`, which
  leaves about 2 s of stale data, and reloads upsd with SIGHUP, so clients stay logged
  in. Edit the file only while `ups.status` is `OL`. upsmon ignores a UPS that goes
  stale after it was last seen OL, but a UPS last seen OB is promoted to OB+LB after
  DEADTIME. Back the file up first (`ups.conf.bak-<stamp>`).
- **HaynesTower, 15 min: `/boot/config/plugins/nut-dw/nut-dw.cfg`**, which is Settings →
  NUT in the Unraid UI. Shutdown Mode is "Runtime Left" (`SHUTDOWN="batt_timer"`) with
  `RTVALUE="900"`. The value is in seconds and is compared directly with
  `battery.runtime`. `RTUNIT="seconds"` only sets how the UI displays runtime. These keys
  are not written into any NUT config file. `/usr/sbin/nut-notify` reads the cfg when
  upsmon reports ONBATT, polls `battery.runtime` until it is at or below `RTVALUE`, then
  runs `upsmon -c fsd`. That shuts down HaynesTower only, because it is a secondary. A
  cfg edit takes effect at the next ONBATT and needs no restart. Before 2026-10-04 it was
  "Time on Battery" with 600 s.

## 7. A service that never comes up reboots the node every 70 minutes

Talos's boot sequence waits in `startAllServices` for every service, extension services
included, to report `up`. If one stays `Waiting`, the task hits its deadline **70 minutes
after it started**, the boot sequence fails and Talos reboots the node. The same service
waits again on the next boot, so the node reboots every ~72 minutes. Each reboot takes
down whatever the node runs. For a master that is an etcd member, its Ceph mon and its
OSDs.

- **Signature** (Loki `{node="<node>", talos_service="machined"}`; nothing in the Omni
  audit log, because nobody asked for the reboot):
  ```
  [talos] task startAllServices (1/1): failed: 2 errors occurred:
          * context deadline exceeded
          * context deadline exceeded          <- one line per service still Waiting
  [talos] phase startEverything (9/9): failed
  [talos] boot sequence: failed
  [talos] initialize sequence: 9 phase(s)      <- about 85 s later, the next boot
  ```
  The earlier `task startAllServices (1/1): service "X" to be "up", ...` lines, every
  15 s, name the services it is waiting for.
- **Between reboots the node looks healthy:** k8s Ready, etcd member healthy, Ceph OSDs
  up. Only Omni (stage `booting`, machine set short) and `talosctl services` show
  anything wrong. Do not read "Ready and in quorum" as "fine".
- **Known causes:**
  - An NVIDIA extension on a node whose card is not on the PCI bus. `ext-nvidia-persistenced`
    waits for `/sys/bus/pci/drivers/nvidia`, which never appears, and
    `KernelModuleSpecController` logs `load nvidia failed: no such device`. Seen on
    talosm04 on 2026-10-04 (A2000 fitted but not enumerating, #3348): it rebooted at
    03:14 UTC and was due again at about 04:26. #3353 took its NVIDIA extensions and
    `kernel.modules` out of the template; the restore steps are on #3348. This also
    applies to talosw04 if it boots without its card. A dock powered up after the PC, or
    a card that dropped off the bus before a reboot, puts it in the loop
    ([egpu-test-node](../runbooks/egpu-test-node.md) §1).
  - `ext-nut-client` with no machine-level config patch (section 6).
- **Fix:** make the service able to start (add its config, or fit the hardware), or
  remove the extension from the node's template block and sync. Removing an extension
  changes the schematic. Omni then cordons and drains the node, runs a same-version
  upgrade to the new installer image and reboots it once. The upgrade cancels the stuck
  boot sequence (`startAllServices (1/1): failed: context canceled`, then
  `reboot sequence`), so it does not wait for the 70-minute deadline. On talosm04
  (#3353) it took 3.5 minutes from the operator-key sync to stage `running`, and the new
  boot logged `boot sequence: done: 6.477328845s`. That line is the proof the loop is
  gone: once the boot sequence is done, there is no deadline left to hit.

## 8. `kubelet.extraConfig.systemReserved` replaces Talos's default map

Talos reserves `cpu: 50m`, `memory: 512Mi` (masters) or `384Mi` (workers), `pid: "100"`
and `ephemeral-storage: 256Mi` for the system, but only when `systemReserved` is empty
(`kubelet_spec.go`: `if len(config.SystemReserved) == 0`). Setting the map in
`machine.kubelet.extraConfig` drops every default key you leave out, with no warning. The
template therefore repeats `pid` and `ephemeral-storage` next to its own `cpu`/`memory`
(#3382). `kubeReserved` has no Talos default.

- **What the reservations do here.** `enforceNodeAllocatable` stays at the kubelet default
  `["pods"]`. Allocatable drops by the reserved amounts, the kubepods cgroup's `memory.max`
  becomes capacity minus the reservations, and its `cpu.weight` follows allocatable CPU. Talos
  sets its own `cpu.weight` and `memory.min`/`low` on `/podruntime` (kubelet, containerd, etcd)
  and `/system`. Adding `kube-reserved`/`system-reserved` to `enforceNodeAllocatable` would
  replace those with smaller weights and hard memory caps on etcd and containerd, so do not.
- **CPU is shared by weight, not capped.** Under full contention the top-level split on a
  master is about kubepods 780 : podruntime 157 : init 79 : system 59
  (`talosctl cgroups --preset cpu`). Inside kubepods, every BestEffort pod on the node shares
  one `cpu.weight` of 1, against the burstable class's weight equal to its summed requests.
  A busy pod therefore starves BestEffort pods long before it starves the kubelet.
- **Lowering allocatable below the pods' requests rejects pods.** After the change the
  kubelet restarts and admits its pods again; any that no longer fit fail with
  `OutOfcpu`/`OutOfmemory`. Compare each node's `kubectl describe node` "Allocated
  resources" with the new allocatable before you sync.
- **Applying** is a kubelet restart on each node, no reboot (Omni applies config in
  `AUTO` mode, and kubelet settings take effect live). Verify with
  `talosctl -n <ip> read /etc/kubernetes/kubelet.yaml | grep -A5 Reserved` and
  `kubectl get node <n> -o jsonpath='{.status.allocatable}'`.

---

## Quick triage map

| Symptom | Likely cause | Section |
|---|---|---|
| Static IP / `routeMetric` / interface config "not applying", no error | uppercase MAC in `deviceSelector` | 1 |
| NICs are `enpXsY` not `ethN`; Multus `Link not found`; IoT pods `Init:0/1` | UKI + `install.extraKernelArgs` no-op | 2 |
| Node shows as a new/duplicate machine after reinstall | META wiped, lost Omni identity | 3 |
| Node reboot-loops back to the OLD version after an upgrade | `/boot` too small for initramfs | 4 |
| Booted ISO, node says connected but Omni says offline / won't install | VPN NIC stealing egress in maintenance | 5 |
| Node Ready in k8s + etcd, but Omni stage stays `booting` / machine set short | an extension service still `Waiting` (`talosctl services`): nut-client with no machine-level patch, or nvidia with no GPU | 6, 7 |
| Node reboots about every 72 min with no audit-log entry; OSDs, mon and etcd member drop each time | a service still `Waiting` when the 70-min `startAllServices` deadline hits: `boot sequence: failed` | 7 |

## Reset Ceph after node work

Node maintenance sets `ceph osd set noout` to suppress rebalancing. It shows as
`HEALTH_WARN noout flag(s) set` (the *only* warn once OSDs are back). When all
node work is done and `ceph -s` shows full mon quorum + all OSDs up +
`active+clean`, clear it: `kubectl -n rook-ceph exec deploy/rook-ceph-tools --
ceph osd unset noout`.
