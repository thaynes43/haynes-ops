# Handoff (cold start): HaynesIntelligence PCIe re-layout for the two rack 3090s

Written 2026-10-29 by Remote Control session `haynes-ops-1027-150450`, for a fresh agent that
continues the GPU work. The canonical tracker is OpenProject: story
[OP#221](https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/221) under epic
[OP#124](https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/124). Origin:
[OP#217](https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/217), the
2026-10-27 power-loss triage (Accepted). This file holds the detail behind OP#221. Progress
belongs on OP#221, not here.

**Status on 2026-10-29: nothing has moved.** The swap has not started. Execution waits for Tom
to say **"go OP#221"** and to be at the rack.

## 0. Cold start

1. Read OP#221 and its newest comments (`opwork show 221 --history`). Then read this file, the
   card table in [egpu-test-node](../runbooks/egpu-test-node.md), and the sudo allowlist in
   [proxmox-access](../runbooks/proxmox-access.md).
2. Re-check the current state (section 1) before acting. Cards and slots change between
   sessions. On 09-17, `GPU-d8a856f1` sat at host `41:00.0`; today the evidence puts it at
   `01:00.0`.
3. Do nothing on the host, the node or the GPUs until Tom says "go OP#221". Shut the host down
   only on Tom's explicit go for that action.
4. Identify cards by NVML UUID, never by a letter in a chat message. On 10-28 Tom called the
   dock card "C", and the exporter showed it was B. The letters follow the runbook card table:
   A = `GPU-6ff9702a…`, B = `GPU-18bf6eab…`, C = `GPU-d8a856f1…`.

## 1. Current state (verified 2026-10-29 01:07Z unless dated otherwise)

| What | State | Source |
|---|---|---|
| talosw01 (VM 103) | Ready since 2026-10-27 22:38:56Z, uncordoned, 2 GPUs allocatable, no GPU pods | kubectl |
| HaynesIntelligence host | Up since about 2026-10-27 22:35Z (15 h 50 m at 10-28 14:25Z) | hw-ssh `uptime` |
| Power limits | A and C at 350 W on talosw01; B at 250 W on talosw04 | exporter `nvidia_smi_power_limit_watts` |
| Case fans | pwm3–5 forced to 100% (`enable=0`) since 10-27 about 22:52Z. They return to BIOS control at the next host power-off. | hw-ssh, 10-28 14:25Z |
| Grants | None active for this session; the last ones were Released 10-28 about 14:33Z | `agent-run grant list --mine` |
| Policies | `credential-hw-ssh-attested` (1h), `egress-haynesintelligence-ssh-attested` (1h), `kube-nodes-attested` (3h). All limited to `attested: {profiles: [full], repos: [haynes-ops]}`. | `kubectl get grantpolicies -n dev-env-system` |
| Test Jobs in `ai` | `gpu-test-rack-{carda,cardc}-20261027-r3…r6` and `…-20261028-probe`, finished (7-day TTL). Logs are in Loki: `{namespace="ai", app="gpu-soak"}`. | kubectl |

## 2. Verified facts

### Host and slots
- ASRock **TRX50 WS**, BIOS **10.01** (2024-06-05), Proxmox VE 8.4.16, kernel 6.8.12-18-pve
  (hw-ssh, 10-28 02:31Z). The firmware has no slot table (`dmidecode -t slot` is empty), so
  software cannot name a slot.
- Slots, from the [ASRock manual](https://download.asrock.com/Manual/TRX50%20WS.pdf) (all
  physically x16):

  | Slot | Electrical |
  |---|---|
  | PCIE1 | 5.0 x16 |
  | PCIE2 | 4.0 x4 |
  | PCIE3 | 5.0 x16 |
  | PCIE4 | 5.0 x8 |
  | PCIE5 | 4.0 x8 |

- Tom, 10-28 00:10Z: "PCIE1 and PCIE3 have GPUs now, which makes PCIE1's fans tight against
  3s heat".
- Devices (hw-ssh `lspci` and sysfs, 10-28 02:31Z):

  | Host address | Device | Root port (max speed) | Link |
  |---|---|---|---|
  | `01:00.0` | RTX 3090 (vfio-pci) | `00:01.1` (32 GT/s) | x16, 2.5 GT/s at idle |
  | `41:00.0` | RTX 3090 (vfio-pci) | `40:01.1` (32 GT/s) | x16, 2.5 GT/s at idle |
  | `84:00.0/.1` | ConnectX-5, PCIe 3.0 x8 (mlx5_core) | `80:03.3` (16 GT/s) | 8 GT/s x8 |

  The ConnectX-5's root port tops out at PCIe 4.0 and links at x8. Only PCIE5 matches that
  (PCIE2 is x4), so the card is in **PCIE5**. An older note said PCIE4; the link data rules it
  out, but Tom has not confirmed it by eye.
- VM 103: `hostpci0: 0000:01:00,pcie=1`, `hostpci1: 0000:41:00,pcie=1`, `onboot: 0`.
  CT 101 (nas01): `onboot: 1`; mount `mp2` `/mnt/cephfs-hdd` is NFS from gasha01.
- Inside talosw01 (exporter `nvidia_smi_gpu_info`, 10-29 01:07Z):

  | Guest address | UUID |
  |---|---|
  | `00000000:01:00.0` (index 0) | `GPU-d8a856f1-f955-f683-bc24-654561496774` |
  | `00000000:02:00.0` (index 1) | `GPU-6ff9702a-1b2c-6094-5a85-00aa1924c55a` |

  talosw04 (the dock) has `GPU-18bf6eab-c76a-26ba-74c8-76093b705b8b` at 250 W.

### Network and Ceph on the host
- The ConnectX-5's port 2 `enp132s0f1np1` is the only port of `vmbr1` (192.168.20.22/24, STP
  off, forward delay 0), which is the **Proxmox Ceph cluster network** at 25G. Port 1
  `enp132s0f0np0` is cabled but down. Interface names follow the PCI bus; there are no `.link`
  pins.
- Neither onboard NIC moves: AQC113 10G `enp66s0` is `vmbr0` 192.168.40.11 (also a gasha01
  Ceph mon address), and Realtek 2.5G `enp70s0` is `vmbr2` 192.168.30.11.
- HaynesIntelligence is one of 5 Proxmox Ceph mons and holds **10 HDD OSDs** (osd.18–22,
  osd.28–32). At 10-28 02:31Z Ceph was HEALTH_OK with 31 of 32 OSDs up; osd.12 on twin-top
  was already down and out. The PVE cluster has 5 nodes and is quorate.

### Thermals: 75 s bursts at 350 W, both cards (soak.py FP16 8192² matmul)

| Run (UTC) | Conditions | A peak | C peak | C heat throttle | Median TFLOPS A / C |
|---|---|---|---|---|---|
| r4, 10-27 about 22:50 | case fans on the board's curve | 80 °C | 89 °C | 21% | 67.6 / 67.4 (C ended at 62.3) |
| r5, 10-27 about 22:55 | case fans at 100% | 79 °C | 90 °C (abort at 58 s, started warm) | — | — |
| r6, 10-27 about 23:07 | fans 100% plus an AC on the intake | 77 °C | 88 °C | 5% | 68.0 / 67.7 |
| probe, 10-28 14:29:49–14:31:07 | fans 100%; AC state not recorded | 77 °C | 88 °C | 5% | 68.0 / 67.8 |

C runs about 10 °C hotter than A at the same power. Full fans and an AC bought 1–3 °C.

### Power
On 10-27, HaynesIntelligence moved to a dedicated 15 A wall circuit. The same two-card burst
then drew 1,083 W (8.7 A) and no PDU outlets were cut. The rack PDU bank overload is
[OP#220](https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/220).

### Fans and probes
- The board's fan chip is an nct6799. Reading it needs `modprobe nct6775`, which does not
  persist across reboots.
- Case headers (Linux pwm3–5) follow `SYSTIN`, the board sensor, on the curve
  20 °C→20%, 65 °C→70%, 70 °C→100%. The board idles at about 48–53 °C. In r4 the case fans went
  only from 56% to 61%.
- Linux fan3 reads 0 RPM even at full duty.
- Tom says the T_SENSOR probes are plugged in. During the probe burst (logged every 2 s, case
  fans at 100%):

  | Input | Before → peak |
  |---|---|
  | AUXTIN5 | 32 → 34 °C, fell after the load |
  | AUXTIN3 | 31 → 32 °C |
  | AUXTIN1 | 51 → 53 °C, slow |
  | AUXTIN2 | 47 → 48 °C, slow |
  | SYSTIN | 48 → 53 °C, still rising |
  | AUXTIN0 | jumps 8–19 °C (floating) |
  | AUXTIN4 | −25 °C (open) |

- `sensors` shows no ConnectX-5 temperature on this kernel. The kernel's mlx5 "High
  temperature" warnings are the only signal: 552 in 7 days in September (09-17 handoff), 0 this
  boot at 10-28 02:31Z.

### Access (verified while it was used, 10-28)
- **SSH:** user `dev-env`, key only, through the attested hw-ssh credential grant plus
  `haynesintelligence.haynesnetwork:22` egress. The sudo allowlist is dmidecode, lspci,
  journalctl, dmesg, sensors, smartctl, nvme, zpool, zfs, qm, pct, pvesh, pvecm, pvesm and
  ha-manager.
  - Not allowed: ceph, ifreload, shutdown, file edits, `/sys` writes.
  - `pvesh` covers Ceph flags, network config and node shutdown.
- **This pod's agent-run (2.45)** has no `grant attest`. Attest with a POST to
  `$AGENTD_API_URL/v1/grants/<grant>/attestation`, sending
  `{"approvedBy":"user","ref":"<ref>","scopeDigest":"<digest>"}`; the digest comes from
  `GET /v1/grants/<grant>`.
  - `ref` is at most 128 characters of letters, digits and `. _ : / # -`. Anything else gets a
    422.
  - Put the token in a mode-600 curl config under `$XDG_RUNTIME_DIR`. A `-K <(…)` inside an
    array assignment fails.
  - The key appears at `$XDG_RUNTIME_DIR/dev-env-hwssh/<grant>/key` 5–13 s after Active. Poll
    for the file.
  - Run commands as `HW_SSH_KEY=<that path> hw-ssh haynesintelligence '<cmd>'`.
  - Never print the key or the token.
- **Node drain:** an attested `--type kube --role dev-env-grant-nodes` grant (up to 3h). The
  role is cluster-wide. Cordon and drain talosw01 only. **Never add a NoExecute taint**: it
  bypasses PDBs and the guard, session pods included.
- `declare-activity` works from v2 sessions.
- **Capped GPU runs are refused.** dev-env-agent-guard denies `SOAK_POWER_LIMIT_W` (it needs
  SYS_ADMIN). Only uncapped soak Jobs (runAsUser 1000) are admitted. See section 6.
- **Kyverno:** `verify-thaynes43-images` (Audit) warned at 10-28 14:29:45Z that the comfyui
  digest fails keyless verification ("x509: certificate signed by unknown authority … TUF
  root"). Nothing was blocked.

## 3. Inferences, not yet verified

- **H1. `GPU-d8a856f1` (C) is physically in PCIE1 at host `01:00.0`.** The chain:
  - C is at guest `01:00.0`.
  - Under Proxmox's usual q35 layout, `hostpci0` lands on guest bus 01. This is a convention
    and was not checked on this VM.
  - `hostpci0` is host `01:00.0`.
  - Tom's "hot slot" is PCIE1, and C is the card that runs hot.

  Verify with `sudo -n qm showcmd 103 --pretty | grep -E 'hostpci|pcie-port'`. Tom can also
  check by eye: A is the card with the replacement fans (runbook card table).
- **H2. After the move:**
  - The ConnectX-5 shows up on bus 0x41, under PCIE3's root port `40:01.1`, as
    `enp65s0f0np0` and `enp65s0f1np1`.
  - Card A's new bus (PCIE4's root port) is unknown until boot.
  - This assumes `41:00.0` is PCIE3. The procedure reads the real addresses after boot either
    way.
- **H3. C runs hot because of where it sits.** Its fans face the PCIE3 card's backplate. A is
  cooler at the same power, and airflow changes helped little.
- **H4. The PCIe 4.0 lock might prevent drops.** Cards dropped off the bus behind root port
  `00:01.1` on 09-23 and earlier. The 09-25 triage linked those drops to the idle-to-load link
  retrain on Gen5 root ports, so locking the GPU slots to PCIe 4.0 may avoid them.
- **H5. AUXTIN5 and AUXTIN3 are the probes,** and they are not in hot air. Tom can match them
  against the T_SENSOR1/2 values the BIOS shows.
- **H6. Card A at PCIe 4.0 x8 costs little.** Expect about 1–3% on heavy compute and nothing
  measurable for inference. This is general knowledge, not measured here.

## 4. Recommendation (Tom's chosen layout, 10-28 00:19Z)

| Slot | Device | Change | Expected host address |
|---|---|---|---|
| PCIE1 | `GPU-d8a856f1-f955-f683-bc24-654561496774` (C) | stays | `01:00.0` (H1) |
| PCIE2 | empty (under C) | — | — |
| PCIE3 | ConnectX-5, cable stays on **port 2** | from PCIE5 | `41:00.0/.1` (H2) |
| PCIE4 | `GPU-6ff9702a-1b2c-6094-5a85-00aa1924c55a` (A), PCIe 4.0 x8 | from PCIE3 | new, read after boot |
| PCIE5 | empty (under A) | — | — |
| talosw04 dock | `GPU-18bf6eab-c76a-26ba-74c8-76093b705b8b` (B), 250 W | none | — |

BIOS settings during the swap (Tom, 10-28):
- Lock **PCIE1 and PCIE4 to PCIe 4.0**. Leave PCIE3 on Auto. ASPM off is optional.
- Set case fans **CHA_FAN1/WP, CHA_FAN2/WP and CHA_FAN3/WP to Full Speed**. Leave CPU_FAN1
  and CPU_FAN2/WP alone.
- Photograph the IOMMU, SVM, Above 4G, ReBAR, PCIe and fan pages first.
- Read T_SENSOR1/2 and find the header that shows 0 RPM.
- Move one probe into the air between and above the GPUs.

Tom is also fitting a small fan on a 3D-printed bracket to the ConnectX-5:
- Test-fit it with A in PCIE4. The NIC's heatsink faces A's backplate.
- Print in PETG or ASA, not PLA.
- Power it from the empty 0-RPM CHA_FAN header if possible, so its RPM is visible.

### Alternatives considered
- **ConnectX-5 in PCIE2 (4.0 x4, enough for one 25G port):** rejected. C in PCIE1 covers PCIE2.
- **GPUs in PCIE1 and PCIE5 with PCIE3 empty:** the widest gap. It depends on clearance under
  PCIE5, and Tom chose PCIE4 instead.
- **Swap B and C** (Tom's idea, 10-28 00:22Z: B into the hot slot since it runs at 250 W
  anyway, C to the dock at 350 W): not recommended.
  - B dropped off the bus at host `01:00.0` / root port `00:01.1` on 09-23.
  - B hit Xid 79 at 300–350 W.
  - Recovering it takes a host power cycle, which would also take down the other rack card.
  - Parked, not ruled on.
- **Power-cap C (for example 300 W) instead of moving it:** needs the capped-Job route in
  section 6 ([OP#233](https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/233)). Estimated −10% throughput (not measured). B measured −19% at 250 W.
- **BIOS update:** not now. 10.01 → 11.02 stays in the same AMD firmware family; 13.04 and
  later change families. No release note mentions a PCIe fix. A flash resets every setting
  (IOMMU, SVM, Above 4G, ReBAR, fans, PCIe). Reconsider only if drops recur after the Gen4 lock
  or a Threadripper 9000 is planned.

## 5. Ordered next steps (on "go OP#221")

Tom's "go OP#221" is the approval reference for every grant. Cite it truthfully, and request a
fresh grant for each window.

**Window 1, agent (about 15 min)**
1. Grants: attested hw-ssh and `:22` (1h), plus attested `dev-env-grant-nodes` (2h).
2. `declare-activity start "OP#221 HaynesIntelligence PCIe re-layout" --scope ai,talosw01,haynesintelligence,rook-ceph --ttl 3h`.
3. Save a baseline: `lspci -nn`, `qm config 103`, `pvesh get /nodes/HaynesIntelligence/network/vmbr1`, Ceph status and the OSD tree.
4. Cordon and drain talosw01.
5. Stop the guests and confirm both are stopped:
   - `sudo -n qm shutdown 103 --timeout 300`
   - `sudo -n pct shutdown 101 --timeout 120`
6. Set the Ceph flags:
   - `sudo -n pvesh set /cluster/ceph/flags/noout --value 1`
   - `sudo -n pvesh set /cluster/ceph/flags/noup --value 1`

   noup keeps the 10 OSDs down at boot until vmbr1 works again. Without it they would come up
   with no cluster network and flap.
7. **Only on Tom's explicit go:** `sudo -n pvesh create /nodes/HaynesIntelligence/status --command shutdown`, or Tom uses the UI. Then release the grants.

**Physical, Tom**
8. Unplug. ConnectX-5 from PCIE5 to PCIE3 (port 2 stays cabled). A from PCIE3 to PCIE4. C
   stays in PCIE1. Fit the NIC fan.
9. BIOS settings as in section 4.

**Window 2, agent (about 20 min)**
10. Fresh grants. Read `lspci -nn` (`10de` is NVIDIA, `15b3` is Mellanox), link speeds and
    widths, and interface names.
11. Fix vmbr1 through the API with the new port 2 name, then apply:
    - `sudo -n pvesh set /nodes/HaynesIntelligence/network/vmbr1 --type bridge --cidr 192.168.20.22/24 --autostart 1 --bridge_ports <new port 2> --bridge_stp off --bridge_fd 0`
    - `sudo -n pvesh set /nodes/HaynesIntelligence/network`
    - Delete the stale `enp132s0f*` entries.
    - Verify carrier, speed 25000 and that the Ceph peers answer.
12. Unset noup, watch all 10 OSDs come up and recover, then unset noout. Wait for HEALTH_OK.
13. Point the VM at A's new address:
    - `sudo -n qm set 103 --hostpci1 0000:<A's new bus>:00,pcie=1`
    - Check that no hostpci entry points at the Mellanox, then `sudo -n qm start 103`.
14. When talosw01 is Ready:
    - Confirm both UUIDs in the exporter.
    - Uncordon.
    - Record each UUID's new guest and host address on OP#221.
    - `declare-activity end`, release the grants.
15. **On Tom's go:** a 75 s two-card burst at 350 W, the same as r4. Render one Job per card
    and apply both together:
    - `SOAK_ABORT_C=90 scripts/gpu-soak/render-job.sh <uuid> gpu-test-rack-<card>-<yyyymmdd>-<tag> sustain:1:0:75 | kubectl apply -f -`
    - Do not set `SOAK_POWER_LIMIT_W`.
    - Log the probes every 2 s over SSH.
    - Compare with section 2, and count NIC high-temperature warnings.
    - Acceptance for OP#221: both cards at or below 83 °C with no throttle, or recorded
      numbers plus a power-cap decision.

**Rollback:**
1. Shut down and put the cards back. The PCIe 4.0 lock can stay: a 3090 can't use 5.0, and
   the lock is part of the drop mitigation (H4). Revert it only if Tom asks.
2. After boot, restore from the step 3 baseline: `hostpci1 0000:41:00,pcie=1` and vmbr1
   `bridge_ports enp132s0f1np1`. Apply the network change.
3. Finish the way steps 12–14 do. `noup` and `noout` are cluster-wide, and VM 103 has
   `onboot: 0`, so a rollback that stops early leaves OSDs down everywhere and talosw01 off.
   - Unset `noup`, wait for the 10 OSDs, then unset `noout`.
   - `qm start 103`, uncordon talosw01 once it is Ready.
   - End the activity declaration and release the grants.

## 6. Safety constraints
- GPU load runs only on Tom's go: 75 s bursts with `SOAK_ABORT_C=90`, and one run at a time.
- Card B stays at 250 W ([OP#199](https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/199)). Never infer 300 W for B.
- Host power actions only on Tom's explicit direction. No NoExecute taints. Release grants
  when done; nothing renews automatically.
- No secrets in git, chat or logs. Never `cat` the hw-ssh key; removing a lease does not
  revoke a copy.
- The hw-ssh key is broadly authorized across hardware. Use it only for this host and this
  story.
- **Capped comparisons need a new admission route.** The proposal is in
  [2026-10-29-gpu-power-cap-route-proposal.md](2026-10-29-gpu-power-cap-route-proposal.md)
  and tracked as [OP#233](https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/233).
  It has not been applied.

## 7. Waiting on Tom
1. When to do the swap ("go OP#221"), and a separate explicit go for the host shutdown.
2. ASPM off, yes or no (optional).
3. After the swap: is the 300 W comparison for C still wanted? Does the power-cap route
   include a B row (re-apply 250 W only)?
4. The B/C swap idea: parked, needs a ruling if he wants it.
5. Case-fan curve once a probe sits in hot air: [OP#234](https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/234).

## 8. Artifacts and ownership
- This file and the proposal were merged through this session's PR. Branch
  `agent/haynes-ops-1027-150450`; no other source work is open in this session.
- Working notes stay on that session's volume:
  - `~/work/orders/ups-power-triage-20261027/`: `PLAN-op221-card-move.md`, `CHECKPOINT.md`,
    `PROPOSAL-gpu-power-cap-route.md`, `MEATBALL-REPORT-op221-access.md` and
    `probe-burst-20261028.log`.
  - Everything needed from them is in this file.
- OP#221 comments 1993 (plan), 1996, 1997 (access), 2025 (BIOS checklist), 2026 (probe
  burst), 2033 (NIC fan), plus the handoff comment that links this file.
