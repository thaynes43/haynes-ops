# eGPU test node (talosw04)

> **Status, 2026-10-30: the test role is retired** (Tom; OpenProject
> [#279](https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/279)).
> talosw04 is back in the rack as an ordinary worker hosting Muse on card B at 250 W. The
> gpu-test taint and label are coming off, and it pages for GPU and node outages like any
> other GPU node. The rest of this page is the history and the dock procedure, kept in case
> the dock is ever used for testing again; that would need the isolation below restored
> first (git history before #279 has it).

`talosw04` is a bare-metal mini PC with an **OCuLink** eGPU dock (a direct PCIe x4 cable). It
was `edgew01` in the
retired edge cluster (Omni machine `77d65c00-5811-11ef-b65b-a8751caa6100`). In `main` it is a
**GPU test worker**: suspect cards go on the dock, get tested, and either go back into
service or get binned. Results go on #3052, one comment per stage. During the October 2026
repair program, it also hosts Muse on card B (card C until 2026-10-17) so local voice
remains available while cards A and C run in the rack. This temporary production placement requires the precautions below.

Cards so far (Tom's identification is ground truth; NVML UUIDs are the software identity):

| Card | UUID | What it is | Status |
|---|---|---|---|
| A | `GPU-6ff9702a-1b2c-6094-5a85-00aa1924c55a` | Repaired card; Tom identified two broken original fans. All three replacement fans visibly spun under load. | 2026-10-13: replacement fans, 2 mm front/rear memory pads and PhaseSheet installed. Short stock-power tests showed no Xid/AER; rack performance was thermally limited compared with the dock. Now in talosw01/VM103 on HaynesIntelligence. Sustained rack validation is tracked in #3052; thermal throttling alone is not Tom's rejection criterion. |
| B (was "#0" in older records) | `GPU-18bf6eab-c76a-26ba-74c8-76093b705b8b` | A **later** card: in HaynesIntelligence slot 01:00.0 from 2026-09-18, dropped 09-23, pulled 09-25. Tom's latest assessment is an intact-looking fan that did not spin; replacement fans are already owned. | 2026-10-17: fans and pads repaired, then Xid 79 again in the dock at 350 W (84 s) and 300 W (34 s), cool and unthrottled; 600 s clean at 250 W. It power-limits about 25 W early at 350 W (319 W against A's 343 W), which points at one power input (8-pin socket or contact, shunt, or power stage). On the dock at 250 W; recovery from a bus drop is a power cycle (a Talos kexec reboot hangs). See #3052. |
| C | `GPU-d8a856f1-f955-f683-bc24-654561496774` | Previously the Muse card in talosw01, then in the talosw04 dock; back in talosw01/VM103 beside A by 2026-10-27. | 2026-10-27/28: in the rack it runs about 10 °C hotter than A at 350 W (88–90 °C, up to 21% heat throttling) because of its slot; the PCIe re-layout is OpenProject #221, handoff `.agents/handoffs/2026-10-29-gpu-pcie-relayout.md`. 2026-10-17: out of the dock for repadding (about a week), then joins A in the rack. Muse moved to card B (#3052). Earlier: stock tests ran much better on the dock than in the rack, with thermal slowdown flags still set (289/290 loaded samples; about 1% Muse decode cost). |
| talosm04 A2000 (#3348) | `GPU-75697ff0-783e-9a2d-6859-3d5ffef0325f` | RTX A2000 12GB (VBIOS 94.06.32.00.18, serial 1712324009904), pulled from talosm04 where it never enumerated. | 2026-10-30: **dead.** Back on its original heatsink and fan, it never enumerated in three dock boots (no device, "NVRM: No NVIDIA GPU found") though its fans spun; warranty claim is OpenProject #270, verdict on #266. 2026-10-06: **faulty.** In the dock it linked at 16 GT/s x4, but one boot dropped its link after 11 s and a clean boot hit Xid 119 (GSP RPC timeout) after 9 min at idle, with the link up and AER 0. |

How it was kept apart from the rest of the cluster until #279 (none of this applies now:
the taint and label are removed, NFD labels the node like any other GPU worker, and the alert
exclusions and `GpuTestNodeGpuMissing` are gone):

| Mechanism | Where | Effect |
|---|---|---|
| Taint `haynesops.com/gpu-test=true:NoSchedule` | kubelet `registerWithTaints` in its Omni machine block | Only pods that tolerate it schedule there. Muse has an exact `Equal` toleration for this key/value/effect and a talosw04 hostname selector during the repair program. Do not remove the taint or add a blanket toleration. |
| Label `haynesops.com/gpu-test=true` | Talos `nodeLabels` | Selects the node for the GPU exporter and the test Jobs, and opts it out of Cilium L2 VIP announcements (`kube-system/cilium/config/cilium-l2.yaml`). |
| No NFD labels | NFD worker has no toleration | The NVIDIA feature selector cannot match this node. Muse uses a talosw04 hostname selector and the exact UUID of the dock card (card B since 2026-10-17, behind a 250 W power gate). The startup-cap proposal below reapplies the limit before that gate; the earlier deployment required a manual cap Job after each boot. Ollama Prime runs on Card A in talosw01, has no dock-taint toleration, and cannot schedule on this node. |
| Alerts | `observability/nvidia-gpu-exporter/app/prometheusrule.yaml`, `NodeRebooted` in `kube-prometheus-stack/app/prometheusrule.yaml` | talosw04 is left out of `GpuMissing` and excluded from `GpuExporterDown` and `NodeRebooted`, so a card test never pages. `GpuTestNodeGpuMissing` (warning, "null" receiver) shows a lost card in Alertmanager and Grafana. |

What does run there: cilium, spegel, node-exporter and smartctl-exporter (all tolerate
everything); promtail, the nvidia device plugin and the GPU exporter (each tolerates the test
taint explicitly; promtail is what gets the test Jobs' logs into Loki); and approved test
Jobs. Muse is the temporary production exception described above. Multus, NFD and the Ceph
CSI node plugins do not run there, so a pod there cannot mount a Ceph PVC or attach a
macvlan NIC. Muse uses direct NFS and emptyDir rather than a Ceph PVC.

Before a dock benchmark, GPU replacement, cable change or node shutdown, reroute local
voice to a healthy backend and scale Muse down through GitOps. Verify that Muse has no
pod on talosw04 and no CUDA process remains before giving a test Job exclusive access to
the card. Restore and verify voice after the work. Never benchmark the dock card alongside
its resident Muse model.

Taint caveat (if the role ever returns): a registration taint is applied when the Node object
is created. Removed by hand, it stays gone until `kubectl delete node talosw04` makes the
kubelet re-register. Talos `machine.nodeTaints` would not work here: NodeRestriction stops a
worker's kubelet from changing taints on an existing Node, and Talos says so in its config docs.

### Card B startup cap — prepared, 2026-10-24 (OpenProject #199)

**Tom approved only the exact startup capability with reviewed script bytes pinned.**
Independent review, required CI and the supported PR workflow still precede deployment.
The connected GitHub integration rejected PR creation with HTTP 403; this is a separate
blocker and is not permission to use another identity or route. No new capability,
grant, policy change or reboot was applied during preparation.
The old `egpu-test-*` Kyverno exception does not
override V2's `dev-env-agent-guard`: it denies `SYS_ADMIN` on agent-created Jobs.
Do not use a different identity or route around that boundary.

The October 24 boot restored Card B's configured and enforced limit to **350 W**.
The existing read-only gate correctly stopped Muse. The source had no automatic
reapplication mechanism: `nvidia-smi -pm 1` retains state between driver clients,
but does not preserve the power limit over a machine reboot.

The proposal adds `power-cap` before `power-gate` in the **existing** llama-server
Pod. Kubernetes reruns init containers when the Pod restarts; no polling controller
or additional resident daemon is introduced. The canonical script lives at
`kubernetes/main/apps/ai/llama-server/app/card-b-power-cap.sh`. Its exact bytes are
embedded in the init container's single shell argument, and the capability exception
pins their SHA-256. There is no runtime script ConfigMap, volume mount or `envFrom`;
the three environment inputs and shell command are constrained. Editing either script
copy without the reviewed matching hash blocks the capability exception. Tests compare
both copies and the hash and exercise altered bytes/commands/mounts/environment against
the actual Kyverno engine. The script:

- Accepts only `talosw04`. The visible-device value may be Card B's UUID, or `void` after the NVIDIA runtime mounts that pinned device for `NVIDIA_DRIVER_CAPABILITIES=utility`. Every NVIDIA command still selects Card B's UUID.
- 2026-10-24: the first utility-capability startup saw `void` and exited before any power-limit write. Helm then restored the previous pod, whose read-only gate stayed at 350 W. `ERROR: init 250 result=11` on that log line is the Talos driver probe.
- Refuses active or unreadable compute-process lists before any write.
- Enables persistence mode and lowers the configured cap to 250 W; preserves any
  already lower maintenance limit. Reads back both configured and enforced limits.
- Fails closed on identity, command, numeric/read-back or process-query errors.
- Starts no CUDA workload and has no volume mounts or Kubernetes API token.

The setter needs UID 0 plus **Linux `SYS_ADMIN`**, a capability broader than power
management. Its proposed Kyverno exception is limited to llama-server Pod/Deployment/
ReplicaSet resources in `ai`, the pinned llama.cpp image, the `power-cap` init
container, exact node/UUID, no token mount and no native-sidecar restart policy.
Only that init capability is waived; the ordinary server and independent read-only
gate retain their existing privileges. The byte binding is enforced by the existing
admission policy machinery; no cluster-wide admission availability policy is changed.
No dev-env guard/RBAC/grant change is proposed.
An approved deployment would recreate only llama-server and then load its model;
HA, AppDaemon and other GPU workloads need no restart.

For an **already authorized GPU operator**, the same script can be rendered as a
no-load version of the existing Job:

```bash
scripts/gpu-soak/render-job.sh --cap-only egpu-test-card-b-cap-YYYYMMDD > /tmp/cap-job.yaml
```

Rendering is not execution authorization. Inspect ownership/active GPU work again
before an approved apply. Do not use an idle-only soak plan as a cap-only substitute:
`soak.py` still imports Torch and allocates matrices before its plan runs.

Validation before deployment: mocked command/error/identity/active-process tests,
rendered init order and mounts, narrowed policy conditions and required PR checks.
After required review/CI and PR access are resolved: deploy the exception first, then llama-server; verify setter read-back,
the independent gate, model readiness, endpoint health and AppDaemon Voice status.
Tom subsequently authorized an actual talosw04 reboot test via existing Omni/Talos
authority, only after deployment, initial Voice recovery, active-work/ownership and
safe-impact checks, rollback readiness and ops coordination. Inspect existing authority;
do not acquire access or use an alternate route if denied. Verify boot -> exact GPU
250 W -> gate -> model -> Voice with timestamped evidence. This warm reboot does not
prove BIOS power-on after AC restoration; that BIOS task remains deferred.
Offline tests/rendering do not prove reboot recovery or live GPU behavior.

References: [Kubernetes init restart behavior](https://kubernetes.io/docs/concepts/workloads/pods/init-containers/#detailed-behavior)
and [Kyverno scoped Pod Security exceptions](https://kyverno.io/docs/guides/exceptions/#pod-security-exemptions).

## 1. Boot media and BIOS (Tom, at the box)

1. Omni → **Download Installation Media**: Talos **v1.13.9**, **amd64**, bare metal,
   **SecureBoot off** (every node in this cluster runs SecureBoot off).
2. Extensions: **siderolabs/amd-ucode** for an AMD (Ryzen) CPU or **siderolabs/intel-ucode**
   for an Intel CPU, plus **siderolabs/nvidia-open-gpu-kernel-modules-lts** and
   **siderolabs/nvidia-container-toolkit-lts**. Nothing else. The media only has to boot to
   maintenance mode: once the machine is in the cluster, Omni installs the extension list
   from the template (its MachineExtensions), not the ISO's.
3. Extra kernel argument: **net.ifnames=0** (the only one).
4. Format: **ISO**. Write it to a USB stick with balenaEtcher (or Rufus in DD mode).
5. The internal disk still holds the old edge install, and Omni's ISO stops when it finds an
   existing Talos ("talos.halt_if_installed"). Wipe that disk first: boot a GParted Live USB
   and delete every partition on the internal SSD.
6. BIOS: UEFI boot (CSM off), Secure Boot **disabled**, **Above 4G decoding on**. Resizable
   BAR can stay at its default. Set AC power recovery to **Power On**. If the board has a
   PCIe link-speed setting for the OCuLink port, leave it on Auto for the first boot; forcing
   it to **Gen3** is the standard first fix for link instability (§3). Leave the internal SSD
   **first** in the boot order, and boot the USB once from the one-time boot menu.
7. OCuLink is **not hot-pluggable**. On every boot, connect the cable and power the dock
   **before** powering the PC; a dock that comes up after the PC never enumerates the card.
   Never unplug the cable or switch the dock off while the PC is on. If talosw04 does boot
   without its card, its NVIDIA extension services wait forever and Talos reboots the node
   70 minutes into each boot ([talos-omni-gotchas](../reference/talos-omni-gotchas.md)
   section 7). Power it off, power the dock on, then boot the PC again.
8. Dock power supply: a 350 W RTX 3090 spikes well past 500 W for milliseconds. Use a dock
   PSU of at least 650-750 W, feeding the card with two separate 8-pin cables, not one
   daisy-chained cable. The 150 W capped stage is safe on any dock; the full burn is not.
9. When Omni shows the machine as installed and running, pull the USB.

## 2. Join it (agent, after `omnictl get machines` lists `77d65c00-…`)

Do **not** sync the template before the machine registers. A sync does not fail on an
unregistered machine: the client does no existence check, and Omni's MachineSetNode
validation skips its Talos-version check when the MachineStatus is missing (Omni v1.12.2
source). Omni then simply waits. But the workers machine set reports `ScalingUp`, so the
cluster is not Ready. Talos upgrades still proceed, while a **Kubernetes version upgrade
stalls at "waiting for the cluster to be ready"** until the box joins or leaves the template.

1. Read the hardware (read-only Reader SA) and check it against the machine block:
   `omnictl get machinestatus 77d65c00-5811-11ef-b65b-a8751caa6100 -o yaml`. As of
   2026-10-02 the block matches this box:
   - CPU AMD Ryzen 9 8945HS, so `amd-ucode`. Swap to `intel-ucode` on an Intel board.
   - NICs: eth0 is RTL8125 `58:47:ca:77:8d:d7` (cabled, metric 100) and eth1 is RTL8125
     `58:47:ca:77:8d:d6` (metric 2000), both pinned by lowercase MAC (gotcha #1). A new
     board means new MACs here.
   - Disk: `install.diskSelector: {type: nvme}`. The USB boot stick shows up as `/dev/sda`;
     the selector keeps the wipe-and-install off it.
   - `securitystate.secureboot` should be false.
2. `omnictl cluster template validate -f kubernetes/main/bootstrap/omni/cluster-template.yaml`
   and `omnictl cluster template diff -f …`. The diff must show only creates for
   `77d65c00-…`: MachineSetNode, ConfigPatch `400-cm-77d65c00-…`, ExtensionsConfiguration and
   KernelArgs. Anything else is drift; stop and find it.
3. Merge the PR. Then sync, either with Tom's `task omni:sync` or with the operator-key
   Job below. Declare it first:
   `declare-activity start "join talosw04" --scope talosw04 --ttl 45m`.
4. Verify:
   - `kubectl get node talosw04 -o wide` shows Ready.
     `kubectl get node talosw04 -o jsonpath='{.spec.taints}'` is empty (the gpu-test taint
     was removed with the test role, #279).
   - `kubectl get pods -A -o wide --field-selector spec.nodeName=talosw04` lists the
     DaemonSets named above, approved test Jobs, and Muse only while its temporary
     production placement is configured. Muse and a GPU test must not run together.
   - `kubectl get node talosw04 -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'` is 1.
   - `count by (node) (nvidia_smi_gpu_info)` in Prometheus includes talosw04.

### Sync Job (dev-env cannot sync itself: its Omni SA is Reader)

The operator key lives in Secret `dev/dev-env-omni-operator-secret`. No pod mounts it,
because the dev-env pod would restart on any change. A one-shot Job mounts it and syncs a
template pinned by commit SHA and checksum, so the key never enters a session. Keep the Job
unlabelled: unlabelled Job pods in `dev` have open egress.

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: omni-sync-talosw04          # any name; one Job per sync
  namespace: dev
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 900
  ttlSecondsAfterFinished: 86400
  template:
    spec:
      restartPolicy: Never
      securityContext: {runAsNonRoot: true, runAsUser: 1000, runAsGroup: 1000, seccompProfile: {type: RuntimeDefault}}
      containers:
        - name: sync
          # the dev-env image ships omnictl: kubectl get deploy -n dev dev-env -o jsonpath='{.spec.template.spec.containers[0].image}'
          image: ghcr.io/thaynes43/dev-env:<tag@digest>
          securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: ["ALL"]}}
          env:
            - {name: HOME, value: /tmp}
            - {name: SHA, value: "<merge commit on main>"}
            - {name: SUM, value: "<sha256sum of cluster-template.yaml at that commit>"}
            - {name: OMNI_ENDPOINT, value: "https://haynes.na-west-1.omni.siderolabs.io:443"}
            - name: OMNI_OPERATOR_KEY
              valueFrom: {secretKeyRef: {name: dev-env-omni-operator-secret, key: OMNI_OPERATOR_KEY}}
          command: [bash, -ec]
          args:
            - |
              cd /tmp
              curl -fsSL "https://raw.githubusercontent.com/thaynes43/haynes-ops/${SHA}/kubernetes/main/bootstrap/omni/cluster-template.yaml" -o t.yaml
              echo "${SUM}  t.yaml" | sha256sum -c -
              # the 1Password field holds a whole `export …=eyJ…` line: keep the token only
              k="${OMNI_OPERATOR_KEY#*eyJ}"; export OMNI_SERVICE_ACCOUNT_KEY="eyJ${k%%[\"\' ]*}"
              omnictl cluster template validate -f t.yaml
              echo "=== diff before sync"; omnictl cluster template diff -f t.yaml || true
              echo "=== sync"; omnictl cluster template sync -f t.yaml --verbose || true
              echo "=== diff after sync"; omnictl cluster template diff -f t.yaml | grep -c '^+++' || true
```

The sync's own status wait can end in `context deadline exceeded`. That is normal (see
talos-version-upgrade.md); judge the result by the "diff after sync" count (0) and by Omni.

## 3. Test ladder (agent drives, Tom watches the card)

Talos access: `omnictl talosconfig /tmp/tc`, then
`talosctl --talosconfig /tmp/tc -n <talosw04-ip> …`. The exporter pod there has `nvidia-smi`:
`E=$(kubectl get pod -n observability -l app.kubernetes.io/name=nvidia-gpu-exporter --field-selector spec.nodeName=talosw04 -o name)`.

**Cable or card?** The same symptom, a card that drops off the bus, can come from the OCuLink
cable or port instead of the card. Take this reading at stage 1 and again before and after
every load stage. Neither the Talos host nor the test images ship `lspci`, so it reads the
same LnkSta/LnkCap and AER data from sysfs through the Omni-proxied talosctl:

```bash
TC=/tmp/tc; N=<talosw04-ip>
G=$(talosctl --talosconfig $TC -n $N get pcidevices | awk '/NVIDIA/ && /VGA/ {print $4; exit}')
pcie() {  # the card, then the port above it (the OCuLink port), then kernel Xid/AER counts
  for d in "$G" "$G/.."; do
    printf '%s  ' "$d"
    for f in current_link_speed current_link_width max_link_speed max_link_width; do
      printf '%s=%s ' "$f" "$(talosctl --talosconfig $TC -n $N read /sys/bus/pci/devices/$d/$f 2>/dev/null | tr -d '\n')"
    done
    for f in aer_dev_correctable aer_dev_nonfatal aer_dev_fatal; do
      talosctl --talosconfig $TC -n $N read /sys/bus/pci/devices/$d/$f 2>/dev/null | grep -oE 'TOTAL_ERR_[A-Z]+ [0-9]+' | tr '\n' ' '
    done
    echo
  done
  talosctl --talosconfig $TC -n $N dmesg | grep -cE 'NVRM: Xid' | sed 's/^/xid_lines=/'
  talosctl --talosconfig $TC -n $N dmesg | grep -iE 'AER:|pcieport.*error' | grep -vc 'AER: enabled' | sed 's/^/aer_lines=/'
}
pcie
```

How to read it:
- **Link (LnkSta vs LnkCap).** The card's `current_link_width` should equal the port's
  `max_link_width` (x4 on OCuLink; the card itself is x16-capable). Speed drops to 2.5 GT/s
  at idle, which is normal power saving, so judge speed **under load**: it should reach the
  port's `max_link_speed` (16 GT/s for Gen4, or 8 GT/s if Gen3 is forced). The same data is in
  `nvidia-smi -q` → "GPU Link Info" ("PCIe Generation" and "Link Width", Max vs Current), and
  `soak.py` samples gen and width every 2 s.
- **AER.** On this board the OCuLink root port (`0000:00:01.1`, AMD Phoenix GPP bridge)
  exposes no AER counters, so the port line shows none; the card's own counters and dmesg
  are what there is. The `AER: enabled with IRQ` boot lines (the USB4 bridges) are not
  errors, and `aer_lines` skips them. `TOTAL_ERR_COR` should stay 0 or close to it. Counts
  that climb under load, `NONFATAL`/`FATAL` at all, or `AER:` lines in dmesg are link
  errors. The nvidia-smi "Replays Since Reset" counter is the card's side of the same story.
- **Xid.** `xid_lines` should stay 0. Xid 79 is "fallen off the bus".
- **Verdict.** A drop or Xid **with** AER noise or a downgraded link (narrower than x4, or
  stuck below max speed under load) points at the cable or port. Reseat both cable ends
  first, then force the OCuLink port to Gen3 in the BIOS, and only then blame the card. A
  card that drops on a clean link (AER 0, full width and speed until the moment it goes) is
  the card.

**Stage 1: enumerate**
- `talosctl … dmesg | grep -iE '10de|nvrm|xid|BAR|pcieport|AER'`. Expect `[10de:2204]`
  enumerated and NVRM loaded. `BAR 1: no space` or "This PCI I/O region assigned to your
  NVIDIA device is invalid" means the firmware left the port's memory window too small: add
  `pci=realloc` to the machine's `kernelArgs` and sync (a non-destructive reboot). No card at
  all usually means the dock was powered after the PC.
- `kubectl exec -n observability $E -- nvidia-smi -q`: record GPU UUID, VBIOS version and
  "GPU Link Info".
- Run `pcie` and keep the output as the baseline.
- Fans: `nvidia-smi` shows one aggregate "Fan Speed", which hides a dead fan. Read each
  channel's duty, target and tachometer RPM with
  `scripts/gpu-soak/render-fans-job.sh egpu-test-fans-1 180 | kubectl apply -f -`
  (read-only, no privileges). A zero-RPM card shows 0 rpm on every channel at idle, so the
  fan verdict comes from running it **next to** a load stage. A channel whose RPM stays far
  below its siblings at the same duty is the broken fan. A channel the controller keeps at
  0 % duty under load while another channel runs (card A's channel 1) is not being driven at
  all: either a dead fan or a header fault. NVML's "target" read a fixed 30 % on card A, so
  ignore it. A GPU has fewer fan channels than fans, and one tachometer per channel, so map
  channels to physical fans with Tom's eyes on the card.

**Stage 2: idle temperatures (15 min, no load)**
```bash
SOAK_NODE=talosw04 scripts/gpu-soak/render-job.sh all egpu-test-idle-1 "idle:1:900:0" | kubectl apply -f -
```
A healthy 3090 idles at about 30-50 °C. A card that climbs at idle on one fan is not ready
for stage 3.

**Stage 3: short load, capped at 150 W, aborts at 83 °C**
```bash
SOAK_NODE=talosw04 SOAK_POWER_LIMIT_W=150 SOAK_ABORT_C=83 \
  scripts/gpu-soak/render-job.sh all egpu-test-capped-1 "capped:3:120:60,cool:1:300:0" | kubectl apply -f -
```
The cap outlives the Job (persistence mode keeps it) until a reboot or the next
`SOAK_POWER_LIMIT_W`; stage 4 sets 350 W explicitly. `soak.py` sets persistence mode and the
150 W limit before any load, and refuses to load the
card if the cap fails (`END,ABORTED_NO_CAP`). It stops the load the moment the core reaches
83 °C (`END,ABORTED_HOT`, exit 3). The cap needs root with CAP_SYS_ADMIN. Kyverno admits
exactly that one capability, and only on the ComfyUI image for Jobs named `egpu-test-*` in
`ai` (`kyverno/policies/app/exceptions/egpu-test-capabilities.yaml`); privileged mode and
host access stay blocked. Pass: three bursts with no `CUDA_ERROR`, no
Xid in dmesg, the card still listed afterwards, and the core under about 80 °C.

**Stage 4: full burn, only after the fan and thermal pads are replaced**
```bash
SOAK_NODE=talosw04 SOAK_POWER_LIMIT_W=350 SOAK_ABORT_C=88 \
  scripts/gpu-soak/render-job.sh all egpu-test-burn-1 | kubectl apply -f -
```
This uses the default plan: idle-to-burst retrains (the trigger seen on 2026-09-23), 20 min
sustained, a hot retrain, then cool-down. 350 W is the stock limit.

Run `render-fans-job.sh` alongside stages 3 and 4, with its seconds set to the soak's
length. Results: `kubectl logs -n ai job/<name>` while the Job exists (7 days), or Loki
`{namespace="ai", app="gpu-soak"} |= "R,"`. The line format is in the `soak.py` docstring.
Run `pcie` after every stage and compare it with the baseline.
A card that drops off the bus shows as `CUDA_ERROR` plus Xid 79 in dmesg, and after 10 min
as `GpuTestNodeGpuMissing` (gone since #279: talosw04 now pages through `GpuMissing`, so
restore the exclusion before testing here again). Before anyone power-cycles, run `pcie` and post the time, its
output and the dmesg lines on #3052; that reading is what tells cable from card. Then power
down in OCuLink order: PC off, dock off and on, PC on.

## 4. Swapping cards, or retiring the node

- **Swap:** Tom powers the PC off, swaps the card in the dock, powers the dock, then the PC.
  Nothing in git changes for a card of the same family. Re-run stage 1 for the new UUID.
- **Retire:** remove `77d65c00-…` from the `Workers` list and its `kind: Machine` block, then
  sync. Omni wipes the machine and returns it to the pool. Remove the talosw04 terms from the
  GPU exporter, the alert rules and the Cilium L2 policy in the same PR.
