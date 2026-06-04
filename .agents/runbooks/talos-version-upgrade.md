# Talos (and Kubernetes) version upgrade runbook

How to bump Talos or Kubernetes on the Omni-managed `haynes-ops` cluster, verify
it safely, and recover if a node won't upgrade and needs re-imaging.

**Read first:** [`../reference/talos-omni-gotchas.md`](../reference/talos-omni-gotchas.md)
— the silent traps (case-sensitive MACs, UKI kernel args, lost VM identity on
wipe, tiny `/boot`, VPN-NIC egress in maintenance) all show up here.

The single source of truth is
`kubernetes/main/bootstrap/omni/cluster-template.yaml`. Upgrades = edit the
version field → commit → `task omni:sync`; Omni drives the rolling upgrade
(drain + reboot, one node at a time) and **preserves `/var`** on a successful
in-place upgrade.

## Node / machine reference

| Role | Host | Omni machine ID | Node IP | Notes |
|---|---|---|---|---|
| control-plane | talosm01 | `88d0b080-43be-11ef-9fe8-3b0f229ef000` | 192.168.40.93 | bare metal, nvidia, **UKI** |
| control-plane | talosm02 | `c1f22c00-4390-11ef-a299-436f6535c900` | 192.168.40.59 | bare metal |
| control-plane | talosm03 | `98290580-1909-11ef-944c-5fe147626300` | 192.168.40.10 | bare metal, nvidia, **UKI** |
| worker | talosw01 | `83596797-0281-4927-94fb-f34bb869b1de` | 192.168.40.53 | **Proxmox VM 103**, GPU passthrough (3090), nvidia |
| worker | talosw02 | `2fe46add-9e72-401c-8ec1-b5fb6837ffa0` | 192.168.40.77 | Proxmox VM, i915 |
| worker | talosw03 | `960513a6-7a1d-4ece-949d-54a022fe85e5` | 192.168.40.21 | Proxmox VM, i915 |

Worker eth0 MACs (to find the Proxmox VM by NIC): w01 `bc:24:11:83:72:d2`,
w02 `bc:24:11:6f:7e:cd`, w03 `bc:24:11:c5:5b:92`. The `.30` VPN NIC (`net1`,
vmbr2) MACs: w01 `bc:24:11:3e:ca:00`, w02 `bc:24:11:74:58:0a`, w03 `bc:24:11:5a:b3:3a`.

## Auth / config

```bash
export OMNICONFIG="$PWD/kubernetes/main/bootstrap/omni/haynes-ops-omniconfig.yaml"
# Default talosctl config is Omni-backed; first use opens a browser CLI auth
# (SideroV1, manofoz@gmail.com). The in-repo direct-to-node TALOSCONFIG cert has
# been expired before — if needed, mint one: omnictl talosconfig <file> -c haynes-ops
```
`omnictl` may warn `version differs ... 1.5.0 vs <backend>` — harmless; the
`kernelArgs` template field still validates/syncs on 1.5.0.

---

## Phase 0 — Pre-flight research (do NOT skip)

- **Talos ↔ Kubernetes support matrix:** confirm the target Talos supports the
  current (and intended) k8s minor. docs.siderolabs.com/talos/v<ver>/.../support-matrix.
- **Pick the exact patch.** e.g. Talos **1.13.3 specifically** (1.13.2 has a
  kube-scheduler regression #13350 that CrashLoops on k8s 1.35).
- **CNI gate (the long pole for k8s):** Cilium is multi-NIC-sensitive here
  (`directRoutingDevice=eth0`, commit `ec119b90`). Do not move k8s past what the
  installed Cilium release lists as tested. As of 2026-06 we run Cilium 1.19.4,
  tested only to **k8s 1.35** → **hold k8s 1.36** until Cilium supports it.
- **Kubernetes:** can't skip minors (1.34→1.35→1.36 sequentially). k8s 1.35
  removes cgroup v1 (we're cgroup v2 ✓); 1.36 needs containerd ≥2.0 (we're 2.2.4
  ✓) and removes `gitRepo` volumes (none) / deprecates `Service.spec.externalIPs`
  (ours empty).
- **Extensions:** confirm all carry forward for the target (intel-ucode,
  nut-client, nvidia-container-toolkit-lts, nvidia-open-gpu-kernel-modules-lts,
  thunderbolt, qemu-guest-agent, i915). NVIDIA CDI is default-on in 1.13 —
  validate GPU workloads after.
- **Breaking config changes:** e.g. 1.13 `machine.network.nameservers` now
  OVERWRITES lower layers (we set a complete single-server set, fine).

## Phase 1 — Baseline capture

```bash
kubectl get nodes -o wide
# qbittorrent VPN exit IP (should stay a Mullvad IP, baseline ~149.40.50.x):
QB=$(kubectl -n downloads get pod -l app.kubernetes.io/name=qbittorrent -o name | head -1)
kubectl -n downloads exec "${QB#pod/}" -c app -- sh -c 'wget -qO- https://ipinfo.io/ip'
# Ceph baseline + set noout for the node work:
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph -s
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd set noout
```

## Phase 2 — Apply the bump

```bash
# Edit the version in cluster-template.yaml:
#   talos.version: vX.Y.Z      (Talos bump)
#   kubernetes.version: vX.Y.Z (k8s bump — do these in separate PRs/syncs)
task omni:validate
git add kubernetes/main/bootstrap/omni/cluster-template.yaml
git commit -m "feat(omni): upgrade Talos vA -> vB" && git push origin main
task omni:sync
```
- `omnictl cluster template diff -f <template>` first to see exactly what changes.
- **`task omni:sync` ending in `Error: context deadline exceeded` is NORMAL** —
  that's only the status-watch timing out; the apply itself succeeded. Confirm
  via the monitor below, not the task exit code.

## Phase 3 — Monitor the roll

Omni upgrades **control planes first (one at a time, etcd-quorum-gated), then
workers**, and **gates each control-plane reboot on Ceph health** (Rook mon/OSD
PodDisruptionBudgets). This is why it can look "paused" — it's waiting for the
previous node's Ceph daemons to rejoin. That is correct; do not force it.

```bash
# version per node (kubectl lags during reboots — trust Omni stage too):
watch -n10 "kubectl get nodes -o custom-columns=N:.metadata.name,OS:.status.nodeInfo.osImage,R:.status.conditions[-1].type"
omnictl get clustermachinestatus    # READY / STAGE / config-up-to-date
omnictl cluster template status -f kubernetes/main/bootstrap/omni/cluster-template.yaml
# Ceph must stay healthy between control-plane reboots:
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph -s
```

## Phase 4 — Per-node verification

For each node as it returns on the new version:

```bash
# /var survived (openebs-hostpath local data intact)
talosctl -n <ip> get discoveredvolumes        # EPHEMERAL present, expected size
# NICs are ethN (not enpXsY!) — else see gotchas #2
talosctl -n <ip> get links | grep -iE '58:47|bc:24'
# egress on workers via eth0, NOT eth1/VPN; eth1 is the static .10x
talosctl -n <ip> get addresses | grep 192.168.30
talosctl -n <ip> get routes -o yaml | awk '/dst: ""/{f=1} f&&/outLinkName:/{print; f=0}'
```
Then re-check `kubectl get nodes`, `scripts/checkHealth.sh`, qbittorrent VPN exit
IP, and (after k8s bumps) reconcile Flux: `task flux:reconcile`.

## Phase 5 — Cleanup

```bash
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd unset noout   # once all node work done
```
Workers may report `uptodate=false` after a live network-config change — that's
a cosmetic reboot-pending flag; it clears on their next reboot. A `git diff`
against the template being empty confirms there's nothing genuinely pending.

---

## Troubleshooting

### A node reboot-loops back to the OLD version ("stuck")
1. Get the install error (works even while it flaps, via Omni's log sink):
   ```bash
   omnictl machine-logs <machine-id> --log-format dmesg --tail 80
   ```
2. **`failed to install bootloader: ... /boot/B/initramfs.xz: no space left on
   device`** → the `/boot` partition is too small for the new initramfs (common
   on old GRUB installs with the heavy nvidia extension). **Re-image** (below).
   Resizing the VM disk will NOT fix it (only `/var` grows).
3. A genuinely wedged node ignores graceful + in-band reboots — power-cycle it
   out of band (VM: `qm reset <vmid>` / `qm stop`+`qm start`; find it by eth0 MAC).

### Re-image a node (fresh install — repartitions to the modern UKI layout)

Use when an in-place upgrade can't succeed (e.g. `/boot` too small). On a VM this
is cheap and reversible; on bare metal it's USB recovery media. **Anything on
`openebs-hostpath` is destroyed** — confirm it's backed up or self-bootstrapping
(Ceph OSDs live on separate disks and survive a system-disk wipe).

1. **Generate the install media WITH the right kernel args + extensions baked
   in.** A fresh Image Factory install honors `net.ifnames=0` (unlike an
   in-place UKI upgrade — gotcha #2), so include it:
   ```bash
   omnictl download iso --arch amd64 \
     --extensions siderolabs/qemu-guest-agent \
     --extensions siderolabs/nvidia-container-toolkit-lts \
     --extensions siderolabs/nvidia-open-gpu-kernel-modules-lts
   # (match the node's extension set; add net.ifnames=0 to the schematic kernel args)
   ```
   Or download Installation Media from the Omni UI. **It must be Omni media** so
   the node re-enrolls over SideroLink.
2. **VM only — pin the SMBIOS UUID to the existing Omni machine ID first**, so
   the wiped node reclaims its identity instead of registering as a new machine
   (gotcha #3):
   ```bash
   qm config <vmid> | grep smbios1            # preserve other fields
   qm set <vmid> --smbios1 uuid=<original-machine-id>
   ```
3. **Disconnect the `.30` VPN NIC (`net1`/vmbr2) before booting the ISO**
   (gotcha #5), so maintenance-mode egress + SideroLink go out mgmt and Omni can
   actually see the node. Capture its params to re-add later:
   `net1: virtio=<mac>,bridge=vmbr2,firewall=1`.
4. **Boot the ISO → maintenance.** It's UEFI/OVMF: set boot order to **disk
   (`scsi0`) first, CD second** and leave the ISO attached — an empty disk falls
   through to the ISO, and once installed it boots the disk (no manual timing,
   no re-loop into the installer). If it tries PXE, press ESC → Boot Manager →
   pick the CD.
5. **Re-add the machine to the cluster** (if it shows as removed/new): in Omni
   remove the orphan, then `task omni:sync`. **Adding it to the cluster triggers
   the disk install** to the target version with a fresh partition layout.
6. **After it's installed and booting from disk:** detach the ISO (or it loops),
   re-add `net1` (same MAC), delete any stray duplicate machine in Omni.
7. **Post-reinstall network:** the node likely boots **UKI** now
   (`talosctl get securitystate` → `bootedWithUKI: true`). If its NICs are
   `enpXsY`, add `kernelArgs: [net.ifnames=0]` to its `kind: Machine` block and
   sync (gotcha #2). A fresh Image-Factory install with the arg baked in comes
   up `eth*` already and needs nothing.
8. Clean up the node's stale `openebs-hostpath` PVCs so they re-provision; let
   self-bootstrapping apps (e.g. comfyui) rebuild.

### Static IP / interface config silently not applying
Almost always an UPPERCASE MAC in `deviceSelector.hardwareAddr` — see gotcha #1.
Lowercase it and `task omni:sync`; applies live, no reboot.

### IoT/macvlan pods stuck `Init:0/1` with `Link not found`
The node's NICs are `enpXsY` (UKI + `install.extraKernelArgs` no-op, gotcha #2),
so the NAD's `master: eth0/eth1` doesn't exist. Fix the naming (Omni `kernelArgs`
`net.ifnames=0`), or temporarily move the apps to a node that has `eth*`
(they're `topology.kubernetes.io/zone: m` + Ceph-backed, so they reschedule).
