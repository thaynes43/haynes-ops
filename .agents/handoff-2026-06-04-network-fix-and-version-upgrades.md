# Handoff — Worker Network Fix + Talos 1.13.3 / Kubernetes 1.35 Upgrades

**Written:** 2026-06-04 (end of a long session). **Audience:** future-me on a cold start.
**TL;DR:** talosw03's node egress is wrongly routed out the VPN NIC. The real fix (eth1 → static, no gateway) is **committed and synced to Omni but NOT yet applied** — it only takes effect on an **in-place Talos upgrade** (a reboot does NOT apply network config changes on these workers). The plan: **bump Talos v1.12.8 → v1.13.3 tomorrow** (which applies the staged fix and preserves data), then **bump Kubernetes v1.34.3 → latest v1.35.x** (single hop; hold 1.36 — Cilium doesn't support it yet).

---

## 1. WHERE WE LEFT OFF (the immediate state)

### The problem
- **talosw03 is stuck routing node egress through the VPN gateway:** `default via 192.168.30.1 dev eth1 metric 1024`. eth0/mgmt should own the default. This is fragile/wrong (registry-pull resets through Mullvad). w01/w02 currently route correctly via eth0 — **but only by boot-race luck**, not by config.
- Root cause: on these **VM/virtio workers**, Talos **ignores `dhcpOptions.routeMetric`** — both NIC default routes land at metric 1024, collide on route key `(default,1024,main)`, and whichever NIC's DHCP finishes first wins. On bare-metal control planes routeMetric IS honored (4 NIC defaults at distinct metrics 100/2000/3000/4000) — so the VM/virtio path is the differentiator. Talos issue #11958 (closed won't-fix) matches.
- **Two dead-ends already tried and proven not to work** (do not retry):
  1. Static `routes:` 0.0.0.0/0 metric-100 block on eth0 (commit `6c4497eb`). Removed on w03 (`59840276`) then all workers (`b121f045`). Never installed; gave no protection.
  2. `routeMetric` tuning — ignored on VM workers.

### The fix that IS staged (commit `9d436a93`, synced to Omni)
- All three workers' **eth1 switched from `dhcp: true` to `dhcp: false` + static address, NO gateway**, so eth1 can never install a default route and eth0 deterministically owns egress:
  - talosw01 eth1 `BC:24:11:3E:CA:00` → `192.168.30.101/24`
  - talosw02 eth1 `BC:24:11:74:58:0A` → `192.168.30.102/24`
  - talosw03 eth1 `BC:24:11:5A:B3:3A` → `192.168.30.103/24`
- IPs are outside the UniFi `.30` DHCP pool (`.6–.99`) and clear of qbittorrent's Multus static `.249` (the only Multus `.30` IP). VLAN's DHCP stays on for everything else.
- This was the chosen approach because UniFi (VLAN 3 / `.30`, "Auto Default Gateway" on) can't suppress the gateway per-client, and its field wouldn't accept `0.0.0.0`/blank to drop it scope-wide. Static-on-the-hosts is clean and doesn't touch the VLAN's DHCP server.

### WHY it isn't applied yet (critical mechanic)
- **A plain reboot does NOT apply network-config changes on these workers.** Verified this session: after syncing the static-eth1 config (Omni shows `configuptodate=true`, RedactedClusterMachineConfig shows `dhcp:false` + `.101`), we rebooted w01 and w03 — both came back **still on DHCP** (`AddressSpec` for eth1 still `layer=operator`, e.g. w01 eth1 still `.87`, w03 still `.99` and STILL stuck on `.30`).
- Network changes flush into the live config only on an **in-place upgrade** (the version-bump path). That is the whole reason for the Talos 1.13.3 bump below.

### Data-safety insight (corrects an earlier wrong worry)
- The openebs-hostpath data loss earlier this saga (prometheus/loki/alertmanager history, ollama models on **talosm01 + talosm03**) was caused by **failed upgrades that needed USB-drive bare-metal recovery = fresh install = honors `install.wipe:true` = wiped `/var`**. It was NOT caused by normal upgrades.
- **A successful in-place upgrade preserves `/var`** — proven: m02 + all three workers upgraded 1.12.3→1.12.8 in-place and KEPT their openebs data. The "disk-space error" that caused the m01/m03 upgrade failures has since been fixed on all nodes, so future in-place upgrades should not need USB recovery.
- **Therefore the static-eth1 fix can be applied via the Talos 1.13.3 upgrade with NO data loss** — no destructive reformat, no pre-backup dance required.
- pgvecto (`postgres16-pgvecto`, single-instance, Immich vector DB ~1.4GB, on w03) **IS backed up** anyway: CNPG barman → `s3://cnpg-haynesops/`, daily `scheduledbackup`, ~minutes-fresh, 30d retention. (Earlier I wrongly called it unbacked — I'd only checked VolSync and missed the CNPG-native barman backup.)

---

## 2. TOMORROW'S PLAN (ordered)

### Phase A — Apply the worker network fix via Talos 1.12.8 → 1.13.3
1. **Target v1.13.3 EXACTLY** (not 1.13.0–1.13.2). v1.13.2 has a kube-scheduler regression (#13350) that CrashLoops scheduler on k8s 1.35; fixed in 1.13.3.
2. Generate a fresh **Image Factory schematic for 1.13.3** with the SAME 7 extensions (intel-ucode, nut-client, nvidia-container-toolkit-lts, nvidia-open-gpu-kernel-modules-lts, thunderbolt, qemu-guest-agent, i915). Confirm Omni's installer image reference includes all of them before applying.
3. Edit `kubernetes/main/bootstrap/omni/cluster-template.yaml`: `talos.version: v1.12.8` → `v1.13.3`. Commit, push, `task omni:sync`.
4. Omni drives the in-place upgrade (drain + reboot, one node at a time), preserving `/var`. **Watch the FIRST worker closely:** confirm (a) `/var/openebs/local` data survived, AND (b) eth1 flipped to the static address with **no default route on eth1** and eth0 owning the default. Use:
   - `talosctl -n <id> get addresses` (eth1 should be `.10x`, layer=configuration, not operator)
   - `talosctl -n <id> get routes` — only `default via 192.168.40.1 dev eth0`
   - Then re-verify qbittorrent VPN egress is still a Mullvad exit IP (baseline was `149.40.50.102`).
5. Roll through all 6 nodes. Control planes (m01/m02/m03) are unaffected by the network change (their routeMetric already works); workers get the fix.
6. **Have Omni/console out-of-band access ready** — the eth1 DHCP→static change is connectivity-affecting; a mistake could drop a worker's data-plane link.

### Phase B — Kubernetes 1.34.3 → latest 1.35.x (after Talos settles)
1. Cannot skip minors: 1.34 → 1.35 → 1.36 each sequentially. **Do 1.35 now; HOLD 1.36** (see gate below).
2. Edit `cluster-template.yaml`: `kubernetes.version: v1.34.3` → latest `v1.35.x`. Commit, push, `task omni:sync`. Omni rolls the k8s upgrade.
3. Prereqs already GREEN (verified 2026-06-04): all nodes **cgroup v2** (k8s 1.35 removes cgroup v1) and **containerd 2.2.4** (k8s 1.36 needs ≥2.0). No `gitRepo` volumes; `externalIPs` are empty `[]`. So no blockers for 1.35.
4. After: `scripts/checkHealth.sh`, reconcile Flux, watch for DRA-stable / in-place-pod-resize-GA surprises (both additive, low risk).

### Phase C — Kubernetes → 1.36 (LATER, gated)
- **GATE: Cilium.** Our Cilium is **1.19.4**; Cilium 1.18/1.19 are tested only up to **k8s 1.35** — **1.36 is NOT in the matrix.** Do not go to 1.36 until Cilium is on a release that lists 1.36 (likely 1.20.x). Our multi-NIC setup is Cilium-sensitive (commit `ec119b90` pinned `directRoutingDevice=eth0`), so treat the CNI as the long pole. Also confirm cert-manager/Rook list 1.36 before the hop.

---

## 3. TALOS 1.13.3 RESEARCH (key findings)
- **Supported direct hop** 1.12→1.13 (one minor). 1.12 is EOL at 1.13.0; upgrade off it.
- **Network machine-config fields are SAFE** — `deviceSelector.hardwareAddr`, `dhcp`, `dhcpOptions.routeMetric`, `addresses`, `routes`, `vip` are NOT renamed/removed. v1.13 adds NEW alternative docs (LinkConfig/RouteConfig/RoutingRuleConfig/VRFConfig) but legacy `machine.network.interfaces` still works. **Our static-eth1 config is valid as-is.**
- **⚠️ BREAKING — nameservers:** when `machine.network.nameservers` is set it now **OVERWRITES** all lower layers (no smart IPv4/IPv6 merge). We set `nameservers: [192.168.40.1]` on every node — that's a complete single-server set, so fine, but be aware.
- **k8s compat:** Talos 1.13.3 supports k8s **1.31–1.36** (default bundle 1.36.1). So it supports both 1.35 and 1.36 — Talos is NOT the k8s-1.36 blocker (Cilium is).
- **Extensions:** all 7 carry forward, no renames. NVIDIA LTS = 580.x in 1.13.
- **⚠️ NVIDIA CDI now default-on** (`enable_cdi=true`) in 1.13; recommended path shifts to gpu-operator. Our extension-based runtime-class setup still installs, but **validate GPU workloads** after the bump (test on `edge` first if possible). If ever adopting gpu-operator, set `NVIDIA_CDI_HOOK_PATH=/usr/local/bin/nvidia-cdi-hook` (Talos puts the binary in `/usr/local/bin`, issue #13021).
- **Version-bump upgrade applies staged config + preserves `/var`** — confirms Phase A is data-safe.
- Sources: docs.siderolabs.com/talos/v1.13/getting-started/support-matrix ; github.com/siderolabs/talos/releases/tag/v1.13.0 and …/v1.13.3 ; issues #13350, #13021.

## 4. KUBERNETES 1.35/1.36 RESEARCH (key findings)
- **No GVK API-version REMOVALS in 1.35 or 1.36** (last removal was flowcontrol v1beta3 in 1.32). No manifest GVK rewrites needed.
- **⚠️ 1.35 removes cgroup v1** (KEP-5573) — kubelet won't start on non-cgroup-v2. **We are cgroup v2 ✓.**
- **containerd 1.x deprecated; need 2.0+ before 1.36.** **We're on 2.2.4 ✓.**
- **kube-proxy ipvs deprecated** — N/A, we run Cilium kube-proxy replacement.
- **1.36 removes gitRepo volume** (none in repo ✓) and **deprecates `Service.spec.externalIPs`** (ours are empty `[]` ✓).
- **DRA** goes stable/default-on in 1.35; **in-place pod resize** GA in 1.35 — additive.
- **Risk verdict:** 1.35 is actually the higher-prep hop (cgroup gate), but we pass it. 1.36 is low-churn EXCEPT the **Cilium compatibility gate**. Recommendation: **1.35.x now, 1.36 later behind a Cilium upgrade.**
- Sources: kubernetes.io/releases/version-skew-policy ; kubernetes.io/blog v1.35 (2025-12-17) and v1.36 (2026-04-22) releases + sneak peeks ; deprecation-guide ; docs.cilium.io compatibility matrix.

## 5. REPO / VERSION CONTEXT (as of 2026-06-04)
- **main (haynes-ops):** k8s `v1.34.3`, Talos `v1.12.8`. **edge (haynes-edge):** k8s `v1.33.4`, Talos `v1.10.7` (use edge to test risky changes first).
- Helm charts: cilium **1.19.4** (the 1.36 gate), rook-ceph v1.20.0, traefik 40.2.0, cert-manager v1.20.2, external-secrets 2.5.0, kube-prometheus-stack 86.1.0, cloudnative-pg 0.28.2, volsync 0.14.0, bjw-s app-template 5.0.1, flux2 v2.6.4.
- apiVersion audit: the repo uses some operator CRD alpha/beta versions (traefik.io/v1alpha1, notification.toolkit.fluxcd.io/v1beta3, cilium.io/v2alpha1, nfd v1alpha1, volsync v1alpha1, externaldns v1alpha1, dragonflydb v1alpha1, emqx v2beta1, kustomize v1alpha1). **These are governed by their OPERATORS, not k8s core — k8s 1.35/1.36 do NOT remove them.** They're general-hygiene items, not upgrade blockers. (The repo-audit agent over-flagged them as "must fix before 1.35"; that's not accurate for core k8s bumps.)

## 6. COLD-START OPERATIONAL CONTEXT
- **Configs/auth:** `OMNICONFIG=kubernetes/main/bootstrap/omni/haynes-ops-omniconfig.yaml`; the in-repo `TALOSCONFIG` is direct-to-node and its cert was EXPIRED — instead generate a working one via `omnictl talosconfig /tmp/w01-talos.yaml -c haynes-ops` (used throughout this session as `TALOSCONFIG=/tmp/w01-talos.yaml`). omnictl/omni auth is SideroV1 (manofoz@gmail.com) and may need an interactive browser CLI auth on first use.
- **Omni-issued talosconfig is `os:reader`:** can read RouteStatus/RouteSpec/AddressStatus/AddressSpec/dmesg/ls/version, but NOT machineconfig or operatorspecs (PermissionDenied). Use Omni's `redactedclustermachineconfig <machine-id>` to see the rendered config instead.
- **Apply workflow:** edit `cluster-template.yaml` → commit/push → `task omni:sync` (= `omnictl cluster template sync -f …`). Network changes need an upgrade to take effect (NOT a reboot). `omnictl cluster template status -f …` shows rollout.
- **Lesson — wedged Talos node:** a wedged node ignores graceful "Shutdown" and even in-band `reboot --mode powercycle` (held sequencer lock). Use an **out-of-band Proxmox hard stop** (`qm stop <vmid>` / `qm reset`). Find the VM by eth0 MAC: w01 `BC:24:11:83:72:D2`, w02 `BC:24:11:6F:7E:CD`, w03 `BC:24:11:C5:5B:92`. Omni's `connected` flag lags minutes after a node dies.
- **Ceph:** has `noout` set (the only reason for HEALTH_WARN). `ceph osd unset noout` once all node work is done. Ceph data survived everything (OSD disks are separate from the wiped system disk).
- **Still pending (separate):** verify commit `b96dc882` (CP metrics bind-address 0.0.0.0) effective, then back out `1c782635` (kubeEtcd-monitoring-disabled workaround) and confirm TargetDown/etcd alerts clear.

## 7. FIRST COMMANDS ON RESUME
```bash
export TALOSCONFIG=$(mktemp) ; omnictl talosconfig "$TALOSCONFIG" -c haynes-ops   # fresh node creds
export OMNICONFIG="$PWD/kubernetes/main/bootstrap/omni/haynes-ops-omniconfig.yaml"
kubectl get nodes -o wide                          # current Talos/k8s/containerd versions
# Is w03 still stuck on .30?  (yes until the 1.13.3 upgrade applies the static-eth1 fix)
W03=960513a6-7a1d-4ece-949d-54a022fe85e5
talosctl -n $W03 get routes -o yaml | grep -B2 -A6 "dst: \"\""   # look for default route dev
git log --oneline -8                               # 9d436a93 = staged static-eth1 fix
```
