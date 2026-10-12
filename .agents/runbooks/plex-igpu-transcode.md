# Plex hardware transcoding on the iGPU twins

Each twin's Arrow Lake-S iGPU is the hardware transcoder for one Plex server.

| Server | Pod | Worker | VM | Host |
|---|---|---|---|---|
| HaynesKube | `media/plex` | talosw02 | `qemu/108` | twin-top |
| HaynesOps | `media/plexops` | talosw03 | `qemu/113` | twin-bottom |

## How the GPU reaches Plex

- **Host:** the iGPU (`0000:00:02.0`, `8086:7d67`, Core Ultra 7 265K) is bound to
  `vfio-pci`. The host blacklists `i915` and `xe`.
- **VM:** the whole device is passed through, and the guest sees it at `01:00.0`.
  This is the config of record for both VMs:
  `hostpci0: 0000:00:02.0,pcie=1,romfile=intelgopdriver_arrowlake.bin`, with `bios: ovmf`,
  `machine: q35`, `vga: none` and no `x-vga`.
  - **Never add `x-vga=1`.** It wedged talosw03's GPU on every boot we checked (#3833,
    proved below).
  - **No cdrom media on CephFS storage.** An ISO on `ISO-Templates` made VM 108's
    onboot start fail ([below](#vm-108-onboot-failure-2026-09-09-fixed)).
- **Guest:** Talos `siderolabs/i915` extension, with `xe` blacklisted. The firmware is
  `mtl_guc_70`, `mtl_huc_gsc`, `mtl_gsc_1` and `mtl_dmc`.
  - The Omni template's `i915 enable_guc=3` never takes effect, because udev loads i915
    before Talos applies module parameters, so the live value is `-1`.
  - That is harmless. On this platform, `-1` already means GuC submission plus HuC.
- **Pod:** `gpu.intel.com/i915: 1` (Intel device plugin), `supplementalGroups: [44]`, and
  the Plex pref `HardwareDevicePath=8086:7d67:1043:88ef@0000:01:00.0`.
- **Driver:** the Intel media driver bundled with Plex 1.43.4 supports Arrow Lake-S. On
  2026-10-12 both twins ran a full VAAPI decode and encode on it.

## Check

```bash
scripts/plex-igpu/check.sh            # both servers; plexops | plex for one
scripts/plex-igpu/check.sh --no-transcode   # node state only
```

The script reads the guest's i915 debugfs through an Omni-proxied talosconfig, which is
read-only. It then runs a ~20 s test transcode and stops it. It skips the test while
anyone is streaming.

Run it after anything that cold-starts a twin VM: a host power-on, a PVE fence, or a
`qm stop`/`start`.

Healthy output shows all of these:

- `wedged=0`
- `guc=RUNNING`
- `huc=RUNNING`
- `guc_load_err_log=0B`
- `hwDecoding=vaapi hwEncoding=vaapi`

`speed` can read `0` on a healthy run, probably because the transcoder throttles once it
is ahead of the test client. The pass condition is vaapi decode and encode.

The raw files are under `/sys/kernel/debug/dri/0/`: `i915_wedged`,
`gt0/uc/guc_info`, `gt1/uc/huc_info` and `gt0/uc/guc_load_err_log_dump`. Read them with
`talosctl --talosconfig <tc> -n <node-ip> read <file>`.

## Failure: the GPU is wedged at boot (talosw03, issue #3833)

**Symptoms:**

- The Plex log shows `iHD_drv_video.so init failed` and then
  `Failed to initialise VAAPI connection` at every transcode decision.
- Every transcode runs in software.
- Tautulli records `transcode_hw_requested=1` with no hardware decode or encode.
- `/dev/dri`, the device plugin and the display side (DMC, connectors) all look healthy,
  which is why nothing alerts.
- `check.sh` shows `wedged=1 guc=AVAILABLE huc=AVAILABLE` and a ~3 MB all-zero
  `guc_load_err_log`.
- **Alert:** `IntelGpuWedged` (warning, so it never pages; look in Alertmanager or Grafana)
  fires for 24 hours after a boot whose kernel log has one of the signature lines below, on
  any node. It matches three lines, not one, because on 2026-10-12 the `GuC initialization
  failed` and `declaring it wedged` lines never reached Loki: the kernel stream had a ~12 s
  hole while the log sink came up, and only the `ioremap on RAM` warning before it arrived.
  Rule: `kubernetes/main/apps/observability/loki/app/lokirule.yaml`. When it fires, confirm
  with `check.sh`.

**Kernel signature at boot** (`talosctl dmesg`, and in Loki as
`{talos_service="kernel", node="talosw03"}`):

```
ioremap on RAM at 0x0000000070800000 - 0x00000000783fffff   (WARN in i915_gem_stolen_lmem_setup)
i915 0000:01:00.0: [drm] GT0: GUC: firmware production part check failure
i915 0000:01:00.0: [drm] *ERROR* GT0: GuC initialization failed -ENOEXEC
i915 0000:01:00.0: [drm] *ERROR* GT0: Failed to initialize GPU, declaring it wedged!
```

A healthy boot has no `ioremap on RAM` warning and logs `GT0: GUC: submission enabled`
and `GT1: HuC: authenticated for all workloads`.
`*ERROR* GT1: GSC proxy component didn't bind` appears on healthy boots too. It only
affects protected-content playback, not transcoding.

**Cause (proved 2026-10-12): `x-vga=1` on `hostpci0`.** VM 113 had
`hostpci0: 0000:00:02.0,pcie=1,romfile=intelgopdriver_arrowlake.bin,x-vga=1`. With it,
the guest's i915 maps its stolen-memory region onto ordinary guest RAM (the
`ioremap on RAM` warning), and the GuC firmware load then fails. The likely mechanism is
QEMU's IGD quirks, which `x-vga` enables (pve-qemu-kvm 9.2.0). A wedge at init is
terminal: nothing inside the guest recovers it, and a Talos reboot does not reset the
device.

**Proof.** We changed one variable at a time on 2026-10-12 (UTC):

| Step | Change | Result of `check.sh plexops` |
|---|---|---|
| Before (16:50) | none, VM up since the 2026-09-09 onboot start | `wedged=1 guc=AVAILABLE huc=AVAILABLE guc_load_err_log=3097736B` |
| Cycle 1 (shutdown 16:50–16:52, start 16:52:20) | none (`x-vga=1` kept), manual start on a host up since 2026-09-09 | `wedged=1 guc=AVAILABLE huc=AVAILABLE guc_load_err_log=3097729B`, test transcode `hwRequested=1 hwDecoding= hwEncoding=` |
| Cycle 2 (shutdown 16:55–16:57, start 16:57:33) | `x-vga=1` removed | `wedged=0 guc=RUNNING huc=RUNNING guc_load_err_log=0B`, test transcode `hwDecoding=vaapi hwEncoding=vaapi` |

Cycle 1 shows that the other suspect, the onboot start ~20 s after host power-on, is
not needed for the wedge: the VM still wedged when it was started by hand on a host that
had been up for weeks. The fix lives in the VM config, and a host reboot proved that it
survives an early onboot start ([Host reboots](#host-reboots)). No onboot delay is set
on either twin, and none is needed.

**Still unproven:** the exact mechanism inside QEMU. Cycle 2 proves the cause, not the
mechanism.

## Host reboots

### Proof: the fix survives a host power-on (twin-top, 2026-10-12)

twin-bottom was not rebooted, because of gasha01 (see the constraints below). twin-top
stood in for it: it has the same board, BIOS and iGPU, and VM 108 has the same
`hostpci0` line as VM 113. The reboot was owner-approved and gated.

| Time (UTC) | Event |
|---|---|
| 18:14:06 | `hw-ssh twin-top sudo pvesh create /nodes/twin-top/status --command reboot` |
| 18:14:12–18:15:52 | stopall: VM 108 shut down cleanly in 100 s (Talos cordoned and drained talosw02), CT 105 in 4 s |
| 18:18:07 | new host boot (kernel) |
| 18:18:16.7 | onboot `startall`: CT 105, then the VM 108 start task at 18:18:18.7 |
| 18:18:36.3 | VM 108 running, 29 s after the host kernel |
| 18:18:49 | guest i915: `GT0: GUC: submission enabled`, `GT1: HuC: authenticated for all workloads`, no `ioremap on RAM` |
| 18:19:40 / 18:20:28 | talosw02 Ready / `media/plex` Ready (HaynesKube down about 6.5 min) |

`check.sh plex` at 18:20:37:

```
plex pod=plex-7c54656d6d-nsgzv node=talosw02 wedged=0 guc=RUNNING huc=RUNNING guc_load_err_log=0B
plex transcode title=803794 target=1280x720@2000k source=h264 hwRequested=1 hwDecoding=vaapi hwEncoding=vaapi speed=0
```

With the config of record, an early onboot start does not wedge the iGPU.

### VM 108 onboot failure (2026-09-09, fixed)

In the 2026-09-09 fence, twin-top's onboot start of VM 108 failed. `ide2` still held the
Talos install ISO on `ISO-Templates` (PVE CephFS). That mount failed at boot
(`Starting VM 108 failed: mount error`, 07:38:40Z), nothing retried the start, and
talosw02 stayed down until someone started it by hand at 11:12Z. On 2026-10-12 we
ejected the ISO live with `qm set 108 --ide2 none,media=cdrom`. In the reboot above,
VM 108 started 10 s before `ISO-Templates` was mounted. A scan of every guest found no
other onboot guest on CephFS storage. VM 113 has no cdrom.

### Before rebooting a twin host

- **The owner's explicit go-ahead comes first.** Without it, dev-env never reboots a
  node ([proxmox-access](proxmox-access.md)). Nothing below replaces it. Tom gave it in
  the session that ran the 2026-10-12 reboot.
- **twin-bottom also runs gasha01 (`qemu/104`).** It is the NFS server for about 30
  pods (home-assistant, appdaemon, immich, paperless and more), on hard mounts. A
  twin-bottom reboot takes all of them down unless gasha01 moves first. Its disks are
  on Ceph RBD (`vm-disks`) and it really uses only a few GiB. Its `memory: 131072` is a
  ceiling with a 32 GiB balloon floor, so free RAM does not block a move. `cpu: host` does:
  a live migration is safe only to twin-top, which has the same Core Ultra 7 265K. To
  move it to another host, first switch it to a portable CPU type (e.g. `x86-64-v3`),
  which takes one restart, a 2-3 min NFS pause.
- **twin-top runs `nut2700` (ct:105),** the NUT server for the big UPS. Its secondaries
  (talosm01-05, HaynesTower) log `Communications with UPS ... lost` and then
  `established`, and do not shut down while the UPS is `OL`. Check that first with
  `hw-ssh twin-top "sudo pct exec 105 -- upsc APC-2700W@localhost"`.
- **PVE Ceph HDD pool:** each twin holds 10 HDD OSDs and is one of the three HDD
  hosts. While `osd.12` (a dead disk on twin-top) is out, a reboot of either twin
  leaves the pool with zero margin. One more HDD OSD failure in the window stalls IO,
  and `hdd_repl_pool` backs gasha01's NFS export.
  - Before the reboot, set `noout` with `pvesh set /cluster/ceph/flags/noout --value 1`.
    Afterwards, set it back to `0` and wait for every PG to be `active+clean`.
  - On 2026-10-12 every PG stayed active (undersized and degraded) during the reboot,
    and all were clean again 60 s after the host returned.
- Each twin runs a PVE Ceph mon and an active MDS. With 5 mons and 2 standby MDS, the
  cluster covers the loss of one host.
- talosw02 runs the active Rook MDS (`ceph-filesystem-a`), which fails over to its
  standby-replay on talosm03. talosw02 and talosw03 run no Rook OSDs.
- Gates, after the go-ahead: `pvecm status` quorate 5/5, `ha-manager status` with no
  resources, Ceph as above, zero Plex streams, and `declare-activity`.

## Recovery

A VM power-cycle takes talosw03 offline. HaynesOps is down for about 3.5 minutes, and
every pod on talosw03 restarts, including the single-instance `postgres16-pgvecto`. Get
the owner's go-ahead first.

1. Check the VM config first: `hw-ssh twin-bottom sudo qm config 113 | grep hostpci`.
   It must match the config of record, with no `x-vga`. If it does not, fix it. A raw
   PCI id needs root, so use
   `hw-ssh twin-bottom sudo qm set 113 --hostpci0 0000:00:02.0,pcie=1,romfile=intelgopdriver_arrowlake.bin`.
   The change takes effect at the next cold start.
2. Wait until HaynesOps has no streams. `check.sh plexops --no-transcode` does not show
   streams, so check `/status/sessions` or Tautulli.
3. Declare the work:
   `declare-activity start "talosw03 VM power-cycle (iGPU reset)" --scope talosw03,media,plexops,database,postgres16-pgvecto,network --ttl 60m`.
4. Power-cycle the VM from PVE:
   `hw-ssh twin-bottom sudo qm shutdown 113 --timeout 300`, confirm with
   `sudo qm status 113` that it is `stopped`, then run `hw-ssh twin-bottom sudo qm start 113`.
   - Talos cordons and drains the node on shutdown and uncordons it on boot. plexops
     stays `Pending` until talosw03 returns, because its GPU is on that node.
   - Do not use a Talos reboot, because it does not reset the device.
5. Once the node is Ready and plexops is back, run `scripts/plex-igpu/check.sh plexops`
   and expect full vaapi. Check that `postgres16-pgvecto` is 1/1 and `postgres16` is
   3/3, with `postgres16-11` streaming (`kubectl cnpg status postgres16 -n database`).
6. Close the declaration.

**If it wedges with the config of record** (no `x-vga`), this is a new cause. Record
whether the boot log has the `ioremap on RAM` warning. An early onboot start did not
wedge it on 2026-10-12, but if the wedge followed a host power-on, testing the timing
is still the cheapest next step. Delay the onboot guests on that host with
`hw-ssh <host> sudo pvesh set /nodes/<host>/config --startall-onboot-delay 180`, then
cold-cycle the VM again.
