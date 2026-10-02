# eGPU test node (talosw04)

`talosw04` is a bare-metal mini PC with a Thunderbolt eGPU dock. It was `edgew01` in the
retired edge cluster (Omni machine `77d65c00-5811-11ef-b65b-a8751caa6100`). In `main` it is a
**GPU test worker**: suspect cards go on the dock, get tested, and either go back into
service or get binned. Its first card is the RTX 3090 from #3052 that dropped off the bus in
HaynesIntelligence and has one dead fan.

How it is kept apart from the rest of the cluster:

| Mechanism | Where | Effect |
|---|---|---|
| Taint `haynesops.com/gpu-test=true:NoSchedule` | kubelet `registerWithTaints` in its Omni machine block | Only pods that tolerate it schedule there. No Deployment, StatefulSet or CronJob in the cluster tolerates every taint (checked 2026-10-02). |
| Label `haynesops.com/gpu-test=true` | Talos `nodeLabels` | Selects the node for the GPU exporter and the test Jobs, and opts it out of Cilium L2 VIP announcements (`kube-system/cilium/config/cilium-l2.yaml`). |
| No NFD labels | NFD worker has no toleration | The `feature.node.kubernetes.io/nvidia-3090-gpu` selectors of ollama-prime, llama-server and immich-ml cannot match it. The taint would stop them anyway. |
| Alerts | `observability/nvidia-gpu-exporter/app/prometheusrule.yaml`, `NodeRebooted` in `kube-prometheus-stack/app/prometheusrule.yaml` | talosw04 is left out of `GpuMissing` and excluded from `GpuExporterDown` and `NodeRebooted`, so a card test never pages. `GpuTestNodeGpuMissing` (warning, "null" receiver) shows a lost card in Alertmanager and Grafana. |

What does run there: cilium, spegel, node-exporter, promtail, smartctl-exporter (all tolerate
everything), the nvidia device plugin and the GPU exporter (both tolerate the test taint), and
the test Jobs. Multus, NFD and the Ceph CSI node plugins do not run there, so a test pod there
cannot mount a Ceph PVC or attach a macvlan NIC. The test pods need neither.

Taint caveat: a registration taint is applied when the Node object is created. If someone
removes it by hand it stays gone until `kubectl delete node talosw04` makes the kubelet
re-register. Talos `machine.nodeTaints` would not work here: NodeRestriction stops a worker's
kubelet from changing taints on an existing Node, and Talos says so in its config docs.

`NodeRebooted` skips talosw04 too: a card that crashes its host is a test result, not a
page. Its reboots still show in `node_boot_time_seconds`.

## 1. Boot media and BIOS (Tom, at the box)

1. Omni → **Download Installation Media**: Talos **v1.13.9**, **amd64**, bare metal,
   **SecureBoot off** (every node in this cluster runs SecureBoot off).
2. Extensions: **siderolabs/amd-ucode** for an AMD (Ryzen) CPU or **siderolabs/intel-ucode**
   for an Intel CPU, plus **siderolabs/thunderbolt**,
   **siderolabs/nvidia-open-gpu-kernel-modules-lts** and
   **siderolabs/nvidia-container-toolkit-lts**. Nothing else.
3. Extra kernel argument: **net.ifnames=0** (the only one).
4. Format: **ISO**. Write it to a USB stick with balenaEtcher (or Rufus in DD mode).
5. The internal disk still holds the old edge install, and Omni's ISO stops when it finds an
   existing Talos ("talos.halt_if_installed"). Wipe that disk first: boot a GParted Live USB
   and delete every partition on the internal SSD.
6. BIOS: UEFI boot (CSM off), Secure Boot **disabled**. Thunderbolt/USB4 enabled, with
   security level **No Security** or **User Authorization**. The thunderbolt extension's
   udev rule authorizes devices for you; "Secure Connect" and "DisplayPort/USB only" block
   the eGPU. Turn on Thunderbolt boot or pre-boot support if the BIOS has it. **Above 4G
   decoding on**. Resizable BAR can stay at its default. Set AC power recovery to **Power On**.
   Leave the internal SSD **first** in the boot order, and boot the USB once from the
   one-time boot menu.
7. Plug the dock into the PC's Thunderbolt/USB4 port (not a plain USB-C port) with the
   dock's own short 40 Gbps cable. Power the dock **before** the PC, so the card enumerates
   at boot.
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

1. Read the hardware (read-only Reader SA):
   `omnictl get machinestatus 77d65c00-5811-11ef-b65b-a8751caa6100 -o yaml`
   - `spec.hardware.processors[].manufacturer`: the template assumes AMD (`amd-ucode`).
     Swap to `siderolabs/intel-ucode` if it says Intel.
   - `spec.network.networklinks`: one NIC needs nothing. With more than one, pin the cabled
     NIC with a lowercase `deviceSelector.hardwareAddr` and `routeMetric: 100` (gotcha #1).
     UniFi last saw `edgew01` at `58:47:ca:77:8d:d7`.
   - `spec.hardware.blockdevices`: with more than one disk, add `install.diskSelector`
     before the sync, because `install.wipe: true` wipes whichever disk Omni picks.
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
     `kubectl get node talosw04 -o jsonpath='{.spec.taints}'` shows the gpu-test taint.
   - `kubectl get pods -A -o wide --field-selector spec.nodeName=talosw04` lists only the
     DaemonSets named above.
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

**Stage 1: enumerate**
- `talosctl … dmesg | grep -iE 'thunderbolt|10de|nvrm|xid|BAR|pcieport'`. Expect the dock
  authorized, `[10de:2204]` enumerated, and NVRM loaded. `BAR 1: no space` or "This PCI I/O
  region assigned to your NVIDIA device is invalid" means the Thunderbolt bridge window is
  too small: add `pci=realloc` to the machine's `kernelArgs` and sync (a non-destructive
  reboot).
- `talosctl … ls /sys/bus/thunderbolt/devices` and `talosctl … read
  /sys/bus/thunderbolt/devices/0-1/authorized` (1 = authorized).
- `kubectl exec -n observability $E -- nvidia-smi -q`: record GPU UUID, VBIOS version and
  "GPU Link Info". Thunderbolt carries PCIe 3.0 x4, so "Max/Current Gen 3, x4" is normal on
  the dock and is not the dead-link symptom. "Replays Since Reset" should stay 0.

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
`soak.py` sets persistence mode and the 150 W limit before any load, and refuses to load the
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

Results: `kubectl logs -n ai job/<name>` while the Job exists (7 days), or Loki
`{namespace="ai", app="gpu-soak"} |= "R,"`. The line format is in the `soak.py` docstring.
A card that drops off the bus shows as `CUDA_ERROR` plus Xid 79 in dmesg, and after 10 min
as `GpuTestNodeGpuMissing`. Note the time and the dmesg lines on #3052 before anyone
power-cycles the dock.

## 4. Swapping cards, or retiring the node

- **Swap:** Tom powers the PC off, swaps the card in the dock, powers the dock, then the PC.
  Nothing in git changes for a card of the same family. Re-run stage 1 for the new UUID.
- **Retire:** remove `77d65c00-…` from the `Workers` list and its `kind: Machine` block, then
  sync. Omni wipes the machine and returns it to the pool. Remove the talosw04 terms from the
  GPU exporter, the alert rules and the Cilium L2 policy in the same PR.
