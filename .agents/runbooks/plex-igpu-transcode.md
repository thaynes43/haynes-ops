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
had been up for weeks. The fix lives in the VM config, so it survives host reboots. No
onboot delay is set on either twin.

**Still unproven:**

- A host power-on with the config of record. The rules forbid host reboots from the
  dev-env pod, so the onboot path has not been tested since the fix. The next time a
  twin host powers on, run `scripts/plex-igpu/check.sh` once its VM is up.
- The exact mechanism inside QEMU. Cycle 2 proves the cause, not the mechanism.

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
whether the boot log has the `ioremap on RAM` warning. If the wedge followed a host
power-on, suspect the onboot timing next. Delay the onboot guests on that host with
`hw-ssh <host> sudo pvesh set /nodes/<host>/config --startall-onboot-delay 180`, then
cold-cycle the VM again.
