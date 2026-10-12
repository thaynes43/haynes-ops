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
- **Guest:** Talos `siderolabs/i915` extension, with `xe` blacklisted. The firmware is
  `mtl_guc_70`, `mtl_huc_gsc`, `mtl_gsc_1` and `mtl_dmc`.
  - The Omni template's `i915 enable_guc=3` never takes effect, because udev loads i915
    before Talos applies module parameters, so the live value is `-1`.
  - That is harmless. On this platform, `-1` already means GuC submission plus HuC.
- **Pod:** `gpu.intel.com/i915: 1` (Intel device plugin), `supplementalGroups: [44]`, and
  the Plex pref `HardwareDevicePath=8086:7d67:1043:88ef@0000:01:00.0`.
- **Driver:** the Intel media driver bundled with Plex 1.43.4 supports Arrow Lake-S. On
  2026-10-12 talosw02 ran a full VAAPI decode and encode on it.

## Check

```bash
scripts/plex-igpu/check.sh            # both servers; plexops | plex for one
scripts/plex-igpu/check.sh --no-transcode   # node state only
```

The script reads the guest's i915 debugfs through an Omni-proxied talosconfig, which is
read-only. It then runs a ~20 s test transcode and stops it. It skips the test while
anyone is streaming.

Healthy output shows all of these:

- `wedged=0`
- `guc=RUNNING`
- `huc=RUNNING`
- `guc_load_err_log=0B`
- `hwDecoding=vaapi hwEncoding=vaapi`

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

**Evidence (2026-10-12, talosw03):**

- `i915_wedged=1` on both GTs.
- No engines are registered.
- GuC status is `0x00000001` with BootROM `0x0`, so the firmware never started.
- `gt0` holds a 3 MB GuC load-error log of all zeros.
- talosw02 has the same firmware, image and guest config, and shows `RUNNING` with an
  empty error log.
- The 2026-08-17 transcode on talosw03's previous boot had already fallen back to
  software, so this has happened on at least two boots.

**What it is not:**

- The Plex build or the bundled driver (the same build works on talosw02).
- Missing firmware.
- Guest config.
- Host binding. Both hosts run the same BIOS (2006) and the same vfio-pci setup, and
  log the same benign `VFIO_MAP_DMA` warnings at VM start.

A wedge at init is terminal. Nothing inside the guest recovers it. The device needs a
reset, which means a VM power-cycle.

**Trigger, not yet proven.** talosw03 differs from the working talosw02 in two ways:

1. It had `x-vga=1` on `hostpci0`.
2. Its onboot start came about 20 s after the host powered on (2026-04-08 and
   2026-09-09). talosw02's onboot start fails on the `ISO-Templates` mount, so it has
   been started by hand hours later each time.

## Recovery

This needs the owner, because it is a worker outage on talosw03.

1. Wait until HaynesOps has no streams. `check.sh plexops --no-transcode` does not show
   streams, so check `/status/sessions` or Tautulli.
2. Declare the work:
   `declare-activity start "talosw03 VM power-cycle (iGPU reset)" --scope talosw03,media,plexops --ttl 45m`.
3. Align the VM with the working twin (this needs root, because of the raw PCI id):
   `hw-ssh twin-bottom sudo qm set 113 --hostpci0 0000:00:02.0,pcie=1,romfile=intelgopdriver_arrowlake.bin`
4. Power-cycle the VM from PVE:
   `hw-ssh twin-bottom sudo qm shutdown 113 --timeout 300`, then
   `hw-ssh twin-bottom sudo qm start 113`.
   - Do not use a Talos reboot. A `hostpci` change only takes effect when the VM starts
     fresh, and a stop/start is the one path certain to reset the device.
5. Once the node is Ready and plexops is back, run `scripts/plex-igpu/check.sh plexops`
   and expect full vaapi.
6. Close the declaration.

**If it wedges again after the next host power-on,** the onboot timing is the trigger.
Delay the onboot guests on that host, for example
`pvenode config set --startall-onboot-delay 180` on twin-bottom. Then cold-cycle the VM
again.
