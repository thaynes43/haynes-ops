# Talos host/service log shipping to Loki

Why this exists: `talosm02` (bare-metal master) hard-reset on 2026-07-18 and
2026-07-24 with **no host-level trace** — promtail only ships *pod* logs, so
nothing from the Talos host (machined, kubelet, etcd, kernel) reached Loki. This
wires Talos' native log forwarding into a cluster-local receiver so the next
unexplained reset leaves something to read.

## What is shipping now (service logs — live, no reboot)

- Each node's `machine.logging.destinations` (in
  [`kubernetes/main/bootstrap/omni/cluster-template.yaml`](../../kubernetes/main/bootstrap/omni/cluster-template.yaml),
  per-machine `400-cm-<id>` patch) forwards **service logs** as
  **JSON-lines over UDP** to `udp://192.168.40.212:6051/`, tagged
  `extraTags: {hostname: talos<node>, cluster: main}`.
- The receiver is a **vector aggregator**
  ([`kubernetes/main/apps/observability/vector`](../../kubernetes/main/apps/observability/vector)),
  a bjw-s `app-template` deployment. A `socket` source (UDP :6051, JSON decoding)
  → `remap` (relabel `talos-service`/`talos-level`, prefer `talos-time`) →
  `loki` sink at `http://loki-headless.observability.svc.cluster.local:3100`
  (same push endpoint promtail uses; Loki `auth_enabled: false`).
- **LB VIP `192.168.40.212`** is pinned via `lbipam.cilium.io/ips` and L2-announced
  on the `eth*` LAN by the existing `CiliumL2AnnouncementPolicy`, so the Talos
  **host network** can reach it. It is the next free IP in the Cilium LB-IPAM
  pool (`192.168.40.203-254`; `.202` is the control-plane VIP).
- `machine.logging` is **machine-config only** → applies **live via
  `task omni:sync`, no reboot** (per the Talos/Omni gotchas: only `install.*` and
  kernel-arg changes are disruptive).

Labels in Loki: `source="talos"`, `log_type="service"`, `cluster`, `node`
(from the `hostname` extraTag — DHCP-independent), `talos_service`, `level`.

Query after `task omni:sync`:

```logql
{source="talos", node="talosm02"}
```

## Follow-up (OPTIONAL): kernel / kmsg logs — reboot-gated

Service logs catch the userspace side. A **hard reset** (kernel panic, watchdog,
thermal, MCE, power) most likely shows in the **kernel ring buffer (kmsg)**,
which the service-log path does **not** carry. Capturing it needs the
`talos.logging.kernel=udp://192.168.40.212:6050/` **kernel argument** — and that
is where the cost is:

- **Under UKI (`bootedWithUKI: true` on these nodes) `machine.install.extraKernelArgs`
  is a NO-OP** (Talos #10339). Kernel args must go through the **Omni-native
  per-machine `kernelArgs`** field, which **rebuilds the boot image and REBOOTS
  the node** (non-destructive; not a wipe). See
  [`talos-omni-gotchas.md`](talos-omni-gotchas.md) §2.
- **omni#2382 reboot-loop trap:** only add a kernel arg where it is NOT already
  in the running cmdline. Check `talosctl -n <ip> read /proc/cmdline` first; a
  duplicate can wedge the node in an endless reboot loop.
- **Vector side:** kernel logs land on **UDP :6050**. When enabling this, add a
  second `socket` source (`address: 0.0.0.0:6050`) + a `talos-kernel` port on the
  vector LB service + a `loki` sink with `log_type="kernel"`. The port convention
  is fixed: **6050 = kernel, 6051 = service** (community/onedr0p standard).

Because it reboots every node, do the kernel-log rollout deliberately (one node
at a time, verify `/proc/cmdline` + node health between each) rather than as a
fleet-wide `task omni:sync`.

## Verify / operate

```bash
# k8s half (after Flux reconciles this PR)
kubectl -n observability get svc vector -o wide          # EXTERNAL-IP == 192.168.40.212
kubectl -n observability get pods -l app.kubernetes.io/name=vector
# Talos half (after `task omni:sync` from a workstation with omnictl)
kubectl -n observability logs deploy/vector | grep -i talos
# Loki (Grafana Explore or MCP): {source="talos"} | line_format "{{.node}} {{.talos_service}} {{.msg}}"
```
