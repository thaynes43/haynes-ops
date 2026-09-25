# Pushover triage — 2026-09-24 18:00 → 2026-09-25 09:45 EDT

Investigation + recommendations only. Nothing in the cluster, Proxmox or GitHub was changed
while producing this report. Times are EDT (UTC−4) unless marked Z.

## TL;DR

- **~19 audible pushes + 1 silent digest.** **18 of the 19 are one fault**: `ai/ollama-prime`
  crash-looping on a dead 3090 after a Renovate bump rolled its pod. The 19th is the MAM
  governor's informational `mam_gate_resumed` (by design).
- The fault is **known** (#3052: 3090 #1 off the bus since 09-23 19:03). Its own alert,
  `GpuMissing`, is silenced to 10-01, but the silence does not cover the *downstream* alerts,
  and **five independent senders** each paged on them (Gatus hourly ×9, the upgrade health
  gate ×3 at priority 1, alert-responder ×2, dev-env-ops escalations ×3, Alertmanager ×1).
- **The fix is already written: PR #3181** (ollama-prime CPU-only). It needs **Tom's merge**
  (Diff Scope blocks bot merges by design). Until it merges the pages continue: gate
  ~10:00/~10:30, Alertmanager 12 h repeat ~13:42, responder ~13:50, Gatus every hour.
- **None of the other listed suspects paged:** thermal, `/var`, UniFi and ytdl-sub-peloton
  are all quiet (details below).

### Premise corrections (verified live 2026-09-25)

| Premise in the request | Reality |
|---|---|
| talosw01/02/03 are VMs on ONE Proxmox host with the 3090 and the i915 iGPUs | **False.** One worker per host: talosw01 = qemu/103 on **HaynesIntelligence** (both 3090s), talosw02 = qemu/108 on **twin-top** (Arrow Lake iGPU), talosw03 = qemu/113 on **twin-bottom** (Arrow Lake iGPU, plus gasha01). Pulling a 3090 takes down **talosw01 only**. The false claim comes from the first draft of the 09-09 incident report, which the same report later corrects. |
| #3052 = the replacement 3090 throttling under ComfyUI | Superseded. **Both 3090s dropped off the PCIe bus on 09-23** (#0 at 14:47, #1 at 19:03). Owner ruling: nothing runs on them. ComfyUI moved to talosm03's RTX 2000 Ada (#3144). #1 is still off the bus. |
| talosw01 `/var` + comfyui pulls | Resolved: 42–43 % free (threshold 18 %), no `NodeVarEvictionImminent` since 09-21, and comfyui no longer runs there. |
| UniFi firmware reboots 03:15–03:50 | Didn't happen last night: every device is up ≥ 299 h, no upgrade is pending, and there were no probe failures in that window. |
| ytdl-sub-peloton fails every 15 min | Not last night: **0 failed Jobs** in the window (`concurrencyPolicy: Forbid`; the 18 h run finished 05:48 with 207 subscriptions, 0 failed). Its alerts are warnings routed to `null`, so they never page. |

## Findings — what paged

Alertmanager routing (live `/api/v2/status`): the default route is `null`, and only
`severity="critical"` reaches Pushover. group_by `[alertname, job]`, group_wait 1 m,
repeat 12 h, resolved alerts are sent too. `alertmanager_notifications_total{integration="pushover"}`
went 0→1 exactly once in the window, with no failures.

| # | Time (EDT) | Sender (Pushover priority) | Message | Pushes | Cause |
|---|---|---|---|---|---|
| 1 | 09-24 20:03 | dev-env-ops digest (−1, silent) | daily digest, 1 item | 1 | — (routine) |
| 2 | 01:26 | haynesnetwork notify-outbox | `mam_gate_resumed` (queued by mam-governor 01:19) | 1 | — (informational, by design) |
| 3 | 01:35, then hourly through 09:42 | **Gatus** (1) | endpoint `internal/ollama-prime` health-check failed (initial + 8 reminders; never resolved) | **9** | A |
| 4 | ~01:43 | **Alertmanager** (1) | `[FIRING:2] FluxReconciliationFailure` HelmRelease + Kustomization `ai/ollama-prime` | 1 | A |
| 5 | 01:50, 07:50 | alert-responder (0) | `[responder] FluxReconciliationFailure (ai)`; the second one fired when the 6 h cooldown expired | 2 | A + B |
| 6 | 03:00, 03:30, 04:30 | **upgrade-health-gate** (1) | `[upgrade-gate] critical flux/<sig>`; **three different signatures** for the same HelmRelease | **3** | A + B |
| 7 | 03:01, 03:31 | dev-env-ops work-order-watch (0) | escalation sessions `esc-shepherd-30377196` and `esc-shepherd-ddb3f48c` (the second is a duplicate) | 2 | A + B |
| 8 | ~03:13 | dev-env-ops order-status (0) | `esc-shepherd-30377196 FAILED: NEEDS YOUR MERGE` (#3181) | 1 | A |

Evidence queries:
- Pushover sends: `sum by (pod)(increase(alertmanager_notifications_total{integration="pushover"}[15m]))`.
- Alerts: `ALERTS{severity="critical",alertstate="firing"}` (only 3 series: 2× ollama-prime + the silenced `GpuMissing`).
- Gatus sends: Loki `{namespace="observability",app="gatus"} |~ "(?i)sending .* alert"`.
- Lane pages: Loki `{namespace="upgrade-agent"} |~ "(?i)paged:|PAGE FAILED|ops-digest: sent"`.

Checked and silent: the dev-env auth-watch sidecar (the Max login has 26 days left), vexa
scribe-notes, the cigar-journal Loki rules and the qbittorrent Loki rules.

**Did not page (warning-level or pending only):**
- ollama-prime CrashLooping / NotReady / RolloutStuck.
- ytdl-sub `KubeJobNotCompleted` / `CronJobNotSucceeding` (cleared 05:45).
- Stale `KubeJobFailed` corpses (these never clear on their own).
- `etcdDatabaseHighFragmentationRatio`.
- `CephMonClockSkew` on mon.j/talosm01, 08:59–09:16 (43–65 ms drift against a 50 ms limit; the same thing happened on 09-21 and cleared on its own).
- One-minute probe blips: tubeszb-zigbee01/zwave01 01:27–01:54, sigoalumni 03:05, paperless 07:46.
- Critical alerts that only reached *pending*: `CNPGReplicaNotStreaming` postgres16 (19:30) and `PersistentVolumeClaimStuckPending` volsync-home-assistant-aws-src (00:01).

## Causes

### A. ollama-prime pinned to a dead GPU, restarted by a Renovate bump (NEW trigger, known fault)
1. 09-23 19:03: 3090 #1 (`GPU-d8a856f1`, host `41:00.0`, VM `02:00.0`) fell off the bus
   (device-plugin `Xid=79`). talosw01 allocatable `nvidia.com/gpu` is 1 of 2.
2. `ollama-prime` is pinned to that UUID (#3138). The running pod survived on CPU fallback
   because its nvidia CDI hook had already run.
3. 01:13: Renovate auto-merged **#3173** (ollama 0.34.4), and the pod rolled at ~01:24. Every
   new container create fails in the hook: `failed to get device handle from UUID: Unknown Error`.
   The Helm upgrade failed at 01:29 and the rollback at 01:39. `FluxReconciliationFailure`
   (critical) fired from 01:40, and CrashLoop is now past 100 restarts.
4. `GpuMissing{node="talosw01"}` has fired since 09-23 but is silenced (`7875e373`, to 10-01).
   That silence does not cover the Flux or Gatus alerts the fault produces downstream.

### B. Page amplification in the paging lanes (NEW: defects, not faults)
One fault reached five senders with no shared "known fault" state:
- **Gatus** re-sends its alert every hour and never collapses into the Alertmanager page: 9 of 19.
- **upgrade-health-gate** re-keys its signature when the pod list changes, so the 6 h dedupe
  never holds. At 03:30 the key changed because an escalation deleted a stale ytdl corpse
  (this case is tracked in **#3182**). At 04:30 it changed again because the failing
  ollama pod dropped out of the signal list. **That second variant is not in #3182.** Result:
  three priority-1 pages.
- **Triage guard wipe (untracked until this report):** from 06:00 the not-upgrade path rewrote
  the signature's state to `attempted=0 result=none` (`shepherd/app/resources/triage.sh:388`),
  so the next merge to main while the HelmRelease is still failed starts a *third* remediate
  run, escalation and page. That includes #3181 itself if triage runs before Flux applies it.
- **Triage missed the attribution:** pod-name matching can't connect `apps/ai/ollama/prime/`
  to `ollama-prime`. The remediate run was denied `git log` and `kubectl logs`, so its verdict
  named the wrong merge (#3179).
- **Duplicate escalation:** the second Fable 5.1 session (03:31) closed itself as a duplicate
  and filed #3182.
- **alert-responder** re-pages the same firing alert every 6 h (cooldown expiry, not a new event).
- The lanes made one change: they deleted the stale pod `downloads/ytdl-sub-peloton-29837715-qhsm9`
  and opened PR #3181. No rem-* order was filed and no storm guard tripped.

### C. HaynesIntelligence 3090s dropping off PCIe (ROOT, known: #3052)
- #0 (`GPU-18bf6eab`, host `01:00.0`, root port `00:01.1`) dropped 09-23 14:47 mid-render and
  came back after the host reboot at 15:45. It is present and idle since then (47–49 °C, Gen1 idle
  downshift, clean UESta/CESta).
- #1 (`GPU-d8a856f1`, host `41:00.0`, root port `40:01.1`) dropped 09-23 19:03 **at a render start
  after 18 min idle at 60 °C** (not thermal). It is still gone: `lspci` shows header type 7f, and
  `SltSta PresDet-`.
- What the two drops share: both happened on the idle → load retrain (2.5 → 16 GT/s) on Gen5 root
  ports, on board ASRock TRX50 WS with BIOS 10.01 (2024-06). The host is in APEI firmware-first
  mode, so PCIe errors never reach the OS journal (it was empty for the window). PSU and
  cabling are largely ruled out (1600 W, two dedicated 8-pins per card).
- Slot `00:01.1` has now seen two different cards drop.

### D. RTX 2000 Ada on talosm03 thermally throttling (side effect of the interim ruling; not paged)
- ComfyUI's 17 renders overnight peaked at **97 °C** (at 18:50).
- There were 29 min of thermal slowdown in total, in bursts of 5 min or less, so the 10 min
  `GpuThermalThrottling` never trips.
- The clock floor was 1575 MHz, a mild throttle compared with the 3090's collapse to 225 MHz.
  The card is at its 70 W cap with the fan at 100 %.

## Ranked next steps

### A — stop the page storm (today)
1. **Merge #3181** (**Tom's click** — Diff Scope blocks bot merges by design). Risk: low; ollama-prime
   runs CPU-only (slower answers for its consumers) until the 3090s are fixed. Fixes: the source
   of all 18 pages. Expect at most one more escalation page from the triage guard wipe (B2) when
   it merges.
2. **Decide the fate of the UUID pin (#3138) before the 3090s come back** (Tom's decision). A hard
   UUID pin turns any single-card failure into a crash-loop on the next pod restart. The 09-18
   ruling was "no per-app GPU pinning", so #3138 was an exception worth revisiting.
3. **Renovate hold on ollama while #3052 is open** (`.renovate/holds.json5`; an agent PR). Risk: nil.
   Matters only if the GPU pin returns before the hardware is proven.

### B — make one fault page once (agent PRs, Tom's go)
1. **Health gate: key the signature on the failing Flux object** (`kind/ns/name` + reason), not on
   the pod list. That covers #3182 and the 04:30 variant. Fixes: three priority-1 pages per outage.
   Risk: low.
2. **Fix the triage guard wipe** (`triage.sh:388`: the not-upgrade path resets `attempted`). Add it to
   #3182 or a new issue; nothing tracks it except this report. Fixes: a re-remediate, escalation
   and page on every merge during an ongoing failure.
3. **Gatus: no hourly Pushover reminders** for endpoints whose failure Alertmanager already pages
   (at least `internal/ollama-prime`). The alternative is to route internal endpoints through
   Alertmanager only. This is a paging-philosophy call for Tom. Fixes: 9 of the 19 pages.
4. **Triage and escalation hygiene:** map manifest paths to workload names (`apps/ai/ollama/prime`
   ↔ `ollama-prime`); allow `git log` and `kubectl logs` in the remediate run; dedupe escalations
   per Flux object in work-order-watch. Fixes: the wrong verdict and the duplicate Fable session.
5. **Cross-lane "known fault" awareness** (design decision): lanes read the active Alertmanager
   silences and treat alerts in the same node/namespace as acknowledged. This fixes the class of
   problem; the items above only fix instances of it.

### C — the 3090 hardware (all need Tom's hands)
1. **Pull #0 and soak #1 alone with idle → burst cycles.** This is Tom's plan in #3052 and uses the
   procedure below. It separates "bad cards" from "shared path". Keep the BIOS unchanged for a clean
   result. If #1 does not re-enumerate even after a cold power cycle, that is itself a strong
   answer (card or slot `40:01.1`). Cost: one ~30 min outage of talosw01 plus 10 HDD OSDs.
   The Saturday repad and CPU-cooler work can ride along in the same outage; it does not affect a
   cold-start drop.
2. **BIOS: lock the GPU slots to Gen4 (Gen3 as a test) and disable ASPM on them.** Enable
   OS-native AER if the board offers it, so the next drop leaves a log. This targets the retrain
   trigger both drops share. Risk: low and reversible (Gen3 only slows model loads). Do it in the
   next outage if #1 drops alone during the soak, or right away if Tom prefers fewer outages over
   a clean A/B test.
3. **BIOS/AGESA update** from 10.01 (2024-06). Risk: moderate. A flash resets settings, so photograph
   the IOMMU/SVM/Above-4G/ReBAR pages first and re-check vfio binding afterwards.
4. **Thermal work (Saturday: pads, CPU cooler, chassis airflow).** It fixes the throttling #3052 was
   opened for, plus the hot NIC PHY, NVMe and ConnectX-5 (#2951). It is not expected to fix the drops.

### D — Ada on talosm03
1. Nothing urgent; it is the interim home by ruling. If render volume grows, check talosm03's
   airflow. Optionally alert on cumulative throttle time (for example 15 min per hour) rather
   than a continuous 10 min, so bursty throttling becomes visible.

---

## Shutdown procedure — HaynesIntelligence (the 3090 host). NOT EXECUTED.

Scope: power off **one** PVE host to pull 3090 #0. This takes down **talosw01 only** in
Kubernetes. talosw02, talosw03, the iGPUs and gasha01 are on other hosts and are unaffected.
Nothing below runs without Tom's explicit go. The 09-18 swap followed the same shape and took
~24 min from start until the node was Ready again.

### Blast radius
- **k8s — talosw01 (17 non-DaemonSet pods):**
  - Cannot move: `ai/ollama-prime` (3090 nodeSelector; already down); `observability/dns-canary`
    (pinned to the hostname); Vexa meeting bots (the spawner pins them to talosw01, so meetings
    are not recorded).
  - Can only move to other workers (control-plane exclusion): `downloads/ytdrivarr` and
    `ytdrivarr-peloton-worker`.
  - `upgrade-agent/dev-env-ops` reschedules to a master (RWO ceph-block) but its sessions die.
  - The running `ytdl-sub-peloton` Job is killed; the next CronJob tick restarts it.
  - The other ~12 pods are stateless.
- **Proxmox:** lxc/101 nas01 (an SMB gateway; nothing in k8s uses it).
- **PVE Ceph:**
  - 1 of 5 mons (quorum stays 4/5) and the standby MDS (pve-filet02 remains standby).
  - **10 of 29 HDD OSDs**. The HDD pool has only three hosts and twin-top already runs 9/10
    (osd.12 is a dead disk), so IO continues at **zero margin** (size 3 / min_size 2).
- **Not affected:** the dev-env pod (on talosm02), both Alertmanagers, every CNPG instance, and
  gasha01 NFS.

### Window
- **Daytime, with Tom on site.** Stay clear of:
  - 00:00 VolSync (13 restic caches are pinned to talosw01);
  - 02:15, 02:45 and 04:00 vzdump (04:00 is this host);
  - Renovate's 22:00–06:00 auto-merge window;
  - scheduled meetings (Vexa).
- If possible, merge #3181 first, so ollama-prime isn't paging throughout the outage.

### 1. Pre-flight (agent; read-only, ~10 min before)
```bash
pve nodes; pve guests                                   # all 5 nodes online; 103/108/113/104 onboot=1
pve vm 103 config                                       # SAVE the output: hostpci0 0000:01:00,pcie=1 / hostpci1 0000:41:00,pcie=1
hw-ssh haynesintelligence 'sudo pvesh get /cluster/ceph/status --output-format json' | jq '.health.status, .osdmap'
hw-ssh haynesintelligence 'sudo zpool status -x'        # rpool is a 2-NVMe STRIPE: must be healthy
hw-ssh haynesintelligence 'sudo lspci -nnk | grep -A3 -i 10de; sudo lspci -vvs 40:01.1 | grep -E "LnkSta|SltSta"'
kubectl get nodes; kubectl get pdb -A | awk '$5==0'     # only postgres16-primary (on talosm03) expected
kubectl get pods -A -o wide --field-selector spec.nodeName=talosw01 | grep -v Completed
kubectl describe node talosw02 talosw03 | grep -A8 'Allocated resources'   # w03 has ~9 GiB of requests free
```
**Go/no-go:** Ceph `HEALTH_OK` (apart from known mutes), all OSDs up except osd.12, no degraded
PGs, all nodes Ready.

### 2. Declare, silence and pause automation (agent, after Tom's go)
```bash
declare-activity start "HaynesIntelligence power-off: pull 3090 #0 (#3052)" \
  --scope talosw01,HaynesIntelligence,proxmox,ai,downloads,observability,upgrade-agent,dev --ttl 2h
```
- **Alertmanager silences, 2 h**, via `amtool` in an alertmanager pod. Cover:
  - `NodeRebooted` and `KubeNode*` for talosw01;
  - `PVENodeDown` for HaynesIntelligence;
  - `PVEVMStopped` and `PVEVMNotOnboot` for vmid 103 (onboot is set to 0 on purpose in step 3).

  Check the exact label names against the rules at run time. Gatus cannot be silenced from here,
  so expect at most one push per endpoint served only from talosw01.
- **Suspend the paging lanes:**
  ```bash
  flux suspend helmrelease -n upgrade-agent <alert-responder HR> <shepherd HR> <health-gate HR>   # names from `flux get hr -n upgrade-agent`
  kubectl -n upgrade-agent patch cronjob alert-responder           -p '{"spec":{"suspend":true}}'
  kubectl -n upgrade-agent patch cronjob upgrade-health-gate       -p '{"spec":{"suspend":true}}'
  kubectl -n upgrade-agent patch cronjob upgrade-shepherd-triage   -p '{"spec":{"suspend":true}}'
  kubectl -n upgrade-agent patch cronjob upgrade-shepherd          -p '{"spec":{"suspend":true}}'
  ```
  The HelmRelease suspend keeps a mid-window Helm upgrade from resetting `suspend: false`.
- **Deliberately left running:**
  - `kube-system/node-out-of-service`. It is the safety net for anything the drain misses:
    only one node will be unready (`MAX_UNREADY=1` is satisfied), and it removes its own taint
    60 s after the node is Ready again.
  - Flux Kustomizations. Nothing in Git fights a drain.
  - The descheduler. Evicting from a cordoned node is the behaviour we want.
- **PVE Ceph noout:**
  `hw-ssh haynesintelligence 'sudo pvesh set /cluster/ceph/flags/noout --value 1'`.

### 3. Drain and power down
1. **Cordon and drain talosw01** (**Tom**). dev-env RBAC has no `nodes/patch` or `pods/eviction`.
   Tom runs this from his laptop, or explicitly orders the one-shot Job with the `headlamp` service
   account used on 09-18:
   `kubectl cordon talosw01 && kubectl drain talosw01 --ignore-daemonsets --delete-emptydir-data --timeout=15m`.
   helm-controller has a 600 s grace period, and no PDB blocks on talosw01.
2. **Stop the guests** (agent, `hw-ssh`). `qm shutdown` sends ACPI, which Talos honours; it is what
   worked on 09-18. `talosctl shutdown` would need an Omni admin talosconfig, and ours is Reader-only.
   ```bash
   hw-ssh haynesintelligence 'sudo pct shutdown 101 && sudo qm shutdown 103 --timeout 300 && sudo qm status 103'
   hw-ssh haynesintelligence 'sudo qm set 103 --onboot 0'   # so 103 cannot autostart against a stale hostpci list
   ```
   Setting onboot 0 matters: PVE refuses to start a VM whose `hostpci` device is missing. If #1
   is still off the bus after the power cycle, an onboot start would fail and leave talosw01 down
   with nobody noticing.
3. **Host off.** Either Tom at the console, or on his directive
   `hw-ssh haynesintelligence 'sudo pvesh create /nodes/HaynesIntelligence/status --command shutdown'`.
   Confirm from another node: `pve nodes` shows it offline, and Ceph shows 10 OSDs down with
   `noout` holding (HEALTH_WARN is expected).

### 4. The physical change (**Tom only**)
- **Pull #0:** host `01:00.0` behind root port `00:01.1`. It is the Zotac **without** the HDMI
  cable; #1 (`41:00.0`) is the one with HDMI attached.
- While the case is open: spin each fan by hand to check bearing play, look for cables or the
  shroud touching the blades, reseat #1 and both of its 8-pin cables, and do the Saturday
  repad/cooler work if it is being bundled.
- Leave the BIOS alone for a clean soak (see C1/C2), unless Tom chooses to bundle C2.
- Power the host on.

### 5. Power-on order (agent unless noted)
1. The host boots; lxc/101 autostarts; **103 stays off** (onboot 0).
2. Ceph: wait for all 10 OSDs on HaynesIntelligence to be up/in (~10 s on 09-18), then
   `sudo pvesh set /cluster/ceph/flags/noout --value 0`. Expect `HEALTH_OK` within a minute.
3. Check what enumerated:
   `hw-ssh haynesintelligence 'sudo lspci -nnk | grep -A3 -i 10de; sudo lspci -vvs 40:01.1 | grep -E "LnkSta|SltSta"'`.
   - **41:00.0 present and bound to vfio-pci:**
     `sudo qm set 103 --delete hostpci0`. This keeps `hostpci1 0000:41:00,pcie=1`, so the VM bus
     address and UUID are unchanged.
   - **41:00.0 absent** (#1 is dead cold):
     `sudo qm set 103 --delete hostpci0,hostpci1`. talosw01 comes back CPU-only. Record this on
     #3052; it answers the soak question without a soak.
4. `sudo qm set 103 --onboot 1 && sudo qm start 103`. **Onboot must be restored.** It is the same
   gap that kept talosw02 down on 09-09; all three workers are onboot=1 today (verified), and
   `PVEVMNotOnboot` (critical) pages if this step is forgotten once the silence expires.
5. `kubectl get node talosw01 -w` until Ready. Omni identity is safe because no disk was wiped.
6. **Uncordon** (**Tom**, or the headlamp Job on his order). The out-of-service taint, if one was
   applied, clears itself 60 s after Ready.

### 6. Verify
- `kubectl get node talosw01 -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'` returns `1`
  (or `0` in the CPU-only branch).
- `nvidia-smi -L` from a pod on talosw01 that uses the nvidia runtime shows only
  `GPU-d8a856f1…`, with `LnkSta` training up to 16 GT/s under load.
- `GpuMissing` expectations: the silence to 10-01 still covers it. Adjust the expected-card count
  if #0 stays out long-term.
- Pods rescheduled: `kubectl get pods -A -o wide | grep -v -E 'Running|Completed'`. Also check
  dns-canary, dev-env-ops and ytdrivarr.
- Ceph `HEALTH_OK`, and `probe_success{instance=~".*gasha01.*"}` = 1.
- Then run the idle → burst soak of #1 as prepared in #3052.

### 7. Resume (agent)
- Un-suspend the four CronJobs.
- `flux resume helmrelease -n upgrade-agent …`.
- Expire the silences.
- `declare-activity end <id>`.
- Post the outcome on #3052.

### Rollback
| Failure | Action |
|---|---|
| Host doesn't POST or boot | **Tom:** reseat, put #0 back in its slot, console. Once the host is up: `qm set 103 -hostpci0 0000:01:00,pcie=1` (from the saved config), onboot 1, start. |
| #1 missing after boot | CPU-only branch (step 5.3). Reinstall #0 at the next opportunity if Tom wants a GPU back sooner. |
| VM won't start | `sudo qm start 103` prints the reason (almost always a stale `hostpci`). Fix the config to match `lspci`, then retry. |
| talosw01 doesn't rejoin | `omnictl get machinestatus` and the VM console (Tom). META is intact because no disk was wiped. |
| An OSD doesn't rejoin | Keep `noout`; `hw-ssh haynesintelligence 'sudo journalctl -u ceph-osd@<id> -n 100'`. IO continues at min_size 2. |
| Drain stalls | Check for a PDB and helm-controller's 600 s grace period. `--timeout=15m` is the limit. Never force-delete pods with RWO volumes on a node that is still up. |

### Only Tom can do
- Cordon, drain and uncordon (or order the headlamp Job).
- Anything physical: pulling the card, reseating, cooling work.
- Powering the host back on.
- BIOS changes (C2/C3).
- Approving the power-off directive for the host itself.

**If a future change needs the iGPU hosts:** twin-bottom also hosts gasha01, the NFS server for
~30 pods (14 of them on masters: home-assistant, appdaemon, immich, paperless…). Powering it off
is a much larger outage and needs its own procedure.

---

## Execution log — 2026-09-25 (Tom on site, executed on his go)

| Time (Z) | Step |
|---|---|
| 14:5x | Pre-flight green: Ceph HEALTH_OK 31/32 (osd.12 dead), zpools healthy, all nodes Ready, only PDB at 0 = postgres16-primary (talosm03). `qm config 103` saved |
| 14:57 | `act-145734-70188`; silences `kubernetes_node=talosw01`, `node=talosw01`, `instance=~192.168.40.53:.*`, `id=~node/HaynesIntelligence\|qemu/103\|lxc/101` (2 h); HRs alert-responder/upgrade-health-gate/upgrade-shepherd suspended + CronJobs alert-responder/upgrade-health-gate/upgrade-shepherd/upgrade-shepherd-triage `suspend: true`; PVE Ceph `noout` |
| 14:58–14:59 | Cordon + drain via headlamp-SA Job `frontend/escape-hatch-drain-talosw01` (Tom approved); ~1 min, no unmanaged pods, nothing blocked |
| 15:00 | `pct shutdown 101`, `qm shutdown 103` (clean), `qm set 103 --onboot 0`, `pvesh create /nodes/HaynesIntelligence/status --command shutdown` |
| 15:02 | PVE offline; Ceph 21/31 up, mon quorum 4/5, all PGs active (undersized/degraded) |
| — | **Tom pulled #0** (`GPU-18bf6eab`, host `01:00.0`, root port `00:01.1`) |
| 15:08 | Host online. Only `41:00.0` enumerates; **#1 back on the bus** (root port `40:01.1` `LnkSta 16GT/s x16`, `PresDet+`) after being off since 09-23 19:03 |
| 15:08 | `noout` cleared; `qm set 103 --delete hostpci0`; onboot 1; `qm start 103` |
| ~15:12 | talosw01 Ready; out-of-service/cilium taints self-cleared; uncordon Job; `nvidia.com/gpu=1`; VM `nvidia-smi` = `GPU-d8a856f1` @ `02:00.0` Gen4 x16 |
| 15:1x | ollama-prime recovered on its own (pod Running, HR Ready `v123`) — #3181 not needed while #1 holds |
| 15:25 | Smoke soak `ai/gpu-soak-smoke-20260925`: `sw_thermal_slowdown` 100 % of a 15 s burst, 75–78 °C, SM 705–780 MHz, 214 W, fan 100 %, 40 → 32 TFLOPS |
| 15:26 | Full soak `ai/gpu-soak-3090-1-slot40-20260925` started (~58 min; `scripts/gpu-soak/`) — pointer on #3052 |

Deviations from the written procedure: none, except the procedure's `pvesh … --output-format json | jq` Ceph check must run on a
surviving node while HaynesIntelligence is down (used twin-top). Still to do after the soak: resume the four CronJobs + three
HRs, expire silences, `declare-activity end act-145734-70188`, post results on #3052.
| 15:3x | #3183 merged (GpuMissing expects 1 on talosw01 while #0 is out); blanket silence `7875e373` + the 4 outage silences expired (15 min bridge silence while Prometheus reloaded the rule, then expired) |
| 16:24 | Soak END OK — 6 Gen1→Gen4 retrains under load + 20 min sustained, **no drop**; `sw_thermal_slowdown` 98–100 % of every burst at a 75–79 °C core, sustained SM 360–435 MHz, 17 TFLOPS (vs ~53 cold). Results on #3052 |
| 16:2x | Lanes resumed (4 CronJobs + 3 HRs Ready), `act-145734-70188` ended, escape-hatch Jobs deleted; overnight airtime Job `ai/gpu-airtime-3090-1-slot40-20260925` started (66 × 10 min idle → 60 s burst, ~12 h) |
