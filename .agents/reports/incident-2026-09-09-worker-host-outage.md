# Incident 2026-09-09 — rolling switch reboots → Proxmox worker host down → 3.5 h partial outage

**Status:** recovered by 07:45 EDT (11:45Z). Mitigation PR: see "Mitigations" below.
**Author:** Claude (local session, driven by Tom from his phone). Times are UTC with EDT in
parentheses. Evidence: Prometheus `node_boot_time_seconds`, Loki `{source="talos"}`, HA UniFi
uptime sensors and TubesZB ESPHome sensors, zwave-js-ui store logs, pod `lastState` timestamps.

## What Tom saw

Pushover flood from Gatus, the healthchecks.io dead-man (Alertmanager Watchdog heartbeat) firing,
`talosw02` powered off in Proxmox, AppDaemon health checks blank, Primary Closet door automation
not firing. Tom powered `talosw02` back on manually at 11:13Z (07:13 EDT).

## What actually happened (it was not "one node down")

| UTC (EDT) | Event | Source |
|---|---|---|
| 07:18–07:48 (03:18–03:48) | **Every UniFi switch rebooted, one at a time, 3–7 min apart**: Pro Max 24 PoE 07:19, Shed Flex 07:21, Pro Max 48 PoE 07:25, Cloffice Flex 07:28, USW Aggregation 07:33, **Switch Pro Aggregation 07:37**, USW Flex 07:41, Pro Max 16 PoE 07:48. All now on `7.5.15.17146`. This is the signature of a UniFi scheduled device-firmware rollout. | HA `sensor.*_uptime`, `update.*` |
| 07:18 (03:18) | First control-plane blip: kube-controller-manager on talosm03 lost leader election. HA's UniFi integration lost the UDM for 83 s. | pod lastState, HA history |
| 07:24 (03:24) | **Z-Wave down.** zwave-js-ui lost `tcp://tubeszb-zwave01.haynesnetwork:6638` ("Serial port closed unexpectedly") and then failed to reopen it 574 times until 11:45Z. The ESP32 stayed pingable and its ESPHome `serial_connected` sensor stayed **on** the whole time → it was holding the dead TCP client from the old session and refusing the new one. | zwave-js-ui log, HA `binary_sensor.tubeszb_zw_serial_connected_2` |
| 07:37–07:38 (03:37) | Switch Pro Aggregation rebooted. Bare-metal masters lost network for ~70 s: kube-scheduler (m01), kube-controller-manager (m02), cilium-operator, node-exporters restarted. etcd stayed healthy. | pod lastState, Loki |
| 07:37–07:41 (03:37–03:41) | **All three Proxmox worker VMs went down.** `node_boot_time_seconds`: talosw03 07:39:09, talosw01 07:41:16 (fresh Talos boot in kernel log), **talosw02 did not come back**. Loki has no Talos shutdown-sequence logs for any worker → hard reset, not a graceful `qm shutdown`. Most likely the PVE host rebooted/self-fenced when it lost the aggregation switch; needs `journalctl -b -1` on the host to confirm. talosw02 evidently is not set to start on boot. | Prometheus, Loki, Tom |
| 07:40 → 11:20 (03:40 → 07:20) | **Prometheus and Loki dead** (both single-replica, RWO Ceph RBD PVCs attached to talosw02). Alertmanager Watchdog stopped → healthchecks.io dead-man fired. Alertmanager-1 was also on talosw02 and alertmanager-0 on talosw03 (both on the same Proxmox host). | `count(up)` gap |
| 07:40 → 11:17 | **EMQX dead** (`emqx-core-0`, single replica, PVC on talosw02) → Zigbee2MQTT crash-looped 51× (needs MQTT), every Zigbee automation dead, HA MQTT integration disconnected. | pod restarts, EMQX log |
| 07:41 → 11:43 (03:41 → 07:43) | **AppDaemon blank.** The pod restarted with talosw01; its MQTT plugin could not reach EMQX and AppDaemon blocks *all* app initialisation on "Waiting for plugins to be ready" — health checks, health card, every AppDaemon automation — for 4 h. | AppDaemon log: `CRITICAL MQTT: Could not complete MQTT Plugin initialization` every 10 s |
| 07:41 → 11:13 | plex, sabnzbd, sabnzbd-fast (PVCs on talosw02) stuck: Kubernetes never force-deletes pods or detaches volumes from a node that dies without a graceful shutdown, so nothing rescheduled. | VolumeAttachments still pointing at talosw02 |
| 11:13 (07:13) | Tom powered talosw02 on. Node Ready 11:20, PVC pods restarted in place, Flux health checks cleared by ~11:40. | events |
| 11:44:53 (07:44) | Tom power-cycled the TubesZB Z-Wave dongle; zwave-js reconnected 11:45:05, driver ready 11:45:07. Nodes 21/42/43 (outdoor ZEN14 plugs) reported dead — those were already dead before today. | zwave-js-ui log, HA |
| 11:43 (07:43) | AppDaemon apps finally initialised; health sensor repopulated. Remaining criticals: Living Room Wi-Fi fan unreachable (auto-repair running), Spa (muted). | `sensor.health_check_status` |
| ~11:35 | sigoalumni.org confirmed still on the homelab tunnel (Cloudflare proxy IPs, `cf-ray` present, healthz 200); the GCP watchdog never steered to Cloud Run (its confirm-probe saw the tunnel back within minutes). Postgres primary (postgres16-1) was on a master; only a replica was on talosw02. | curl/dig, CNPG status |

## Why the blast radius was so large

1. **Half the cluster shares one physical failure domain.** talosw01/02/03 are all VMs on one Proxmox host, and that host also holds the only i915 iGPUs and the 3090. A host reboot is a 3-node outage every time.
2. **Critical singletons happened to live on that host.** EMQX core, Prometheus, Loki, both Alertmanagers, Gatus, AppDaemon, ha-mcp were all on workers. Home Assistant, zwave, zigbee2mqtt were on bare-metal masters and survived (until their dependencies died).
3. **No non-graceful-shutdown handling.** A hard-off node keeps its VolumeAttachments and Terminating pods forever; StatefulSets never reschedule. Recovery required a human to power the VM on.
4. **talosw02 does not autostart** in Proxmox (the other two did).
5. **Dependency chains amplify one broker outage**: EMQX → Zigbee2MQTT → every Zigbee automation; EMQX → AppDaemon MQTT plugin → *all* AppDaemon apps including the health checks that would have told Tom what was wrong.
6. **Z-Wave over TCP has no stale-client recovery.** One switch reboot wedged the TubesZB for 4 h 20 min while the radio was fine; zwave-js-ui cannot reclaim the port and nothing restarted the ESP32.
7. **Monitoring dies with the workload.** Prometheus, Loki and Gatus are in-cluster on the same host; only the external dead-man and Gatus→Pushover (Gatus was on talosw01, back after 4 min) reached the phone. Loki has a 3.5 h hole exactly where the forensics would be.

## Mitigations

### In this repo (PR opened by this session)
- `kube-system/node-out-of-service` — CronJob that applies the Kubernetes non-graceful-shutdown taint (`node.kubernetes.io/out-of-service=nodeshutdown:NoExecute`) to a **worker** that has been NotReady > 8 min (guard: refuses if more than one node is unready — a partition, not a dead node), and removes it once the node is Ready again. Effect: PVC-backed StatefulSets/Deployments reschedule to a live node in minutes instead of waiting for a human.
- **Critical tier prefers bare-metal masters**: soft nodeAffinity to `node-role.kubernetes.io/control-plane` for Prometheus, Loki, Gatus, AppDaemon, ha-mcp; Alertmanager pair and both cloudflared tunnels spread across `topology.kubernetes.io/zone` (m = bare metal, w = the Proxmox host). **EMQX core is deferred**: affinity on the EMQX CR is a pod-template change that blue-greens the broker (MQTT + Zigbee bounce), so do it in a planned window together with the `cluster.hocon` config fix and a `replicas: 3` evaluation.

### Outside this repo (Tom)
- **UniFi:** confirm in Settings → System → Updates that device auto-update ran at 03:18 today, then either disable automatic device firmware updates or move the window and exclude the aggregation/core switches. Every switch rebooting in a 30-minute window at 3 AM is what kicked this off.
- **Proxmox:** on the PVE host run `uptime` and `journalctl -b -1 -e | tail -200` (look for `watchdog-mux`, `corosync`, `pve-ha-lrm`, `fence`) to confirm the reboot cause. If HA/corosync is enabled on a host that cannot keep quorum alone, disable HA or add a QDevice. Set `qm set <vmid-of-talosw02> --onboot 1` (and a startup delay) so it comes back like the other two.
- **TubesZB Z-Wave:** add auto-repair to the AppDaemon zwave health checker (hass-sandbox): when the Z-Wave integration is unavailable for > 5 min but `binary_sensor.tubeszb_zwave01_haynesnetwork` is on, press `button.restart_the_esp32_device_2`, then restart the zwave pod if still down. Also check the ESPHome stream-server config for a client idle timeout / TCP keepalive so a dead client is dropped.
- **AppDaemon:** make the MQTT plugin non-blocking or move health checks to a plugin-independent instance so a broker outage cannot blank the health dashboard.
- **EMQX config drift:** the running broker still enforces `retainer.max_payload_size=1MB` / 1 MB max packet (Z2M `bridge/devices` publishes are discarded with `frame_is_too_large`) even though `emqx-configs` now says 256MB/4MB — EMQX keeps `data/configs/cluster.hocon` overrides on the PVC. Apply via the EMQX dashboard/API or `emqx ctl conf`. Longer term: EMQX core replicas 3 so MQTT survives any single node.
- **Two stale pods** (`frontend/omni-…-fswhb`, `media/plexops-…-58rdx`, `UnexpectedAdmissionError`, 14–18 days old) predate the incident; delete them.

## Open questions
- Did the PVE host reboot (uptime), or did the VMs get reset some other way?
- Is UniFi device auto-update enabled, and on what schedule?

## Addendum 12:19–12:45Z (08:19–08:45 EDT) — Z-Wave wedged a second time

zwave-js-ui logged "Serial port closed unexpectedly" at 12:19:53Z with no switch event this time (cause unknown; the ESP32's ESPHome API also reconnected to HA at 12:20:01Z, so the ESP32 itself blipped). Tom power-cycled the dongle at 12:25:47Z; zwave-js connected at 12:26:02Z but the open failed (ZW0100) and every retry after that failed while the ESP32 kept reporting `serial connected: on`. A `kubectl rollout restart deploy/zwave` alone did NOT clear it (the new pod also failed; the pod's side of the old socket sat in FIN_WAIT2 — the ESP32 never acknowledged the close). Pressing `button.restart_the_esp32_device_2` in HA at 12:44:43Z did: driver ready ~12:44:55Z, nodes alive 12:45:00Z.

Lesson: the remedy for a wedged TubesZB Z-Wave link is **restart the ESP32 while a zwave-js pod is already in its reconnect loop**; a pod restart by itself is not enough, and a power cycle can race the driver's first open. This is the auto-repair the AppDaemon zwave checker should implement.

## Correction 13:56Z (09:56 EDT) — what the Proxmox exporter actually recorded

`observability/prometheus-pve-exporter` has existed since #2032 (61 days) and scraped the
`pvedash` LB the whole time; the "no Proxmox telemetry" premise was wrong — nobody had looked,
and nothing alerted on it. Its `pve_uptime_seconds` / `pve_guest_info` series (Prometheus was
back by 11:20Z; the values below are the boot times implied by uptime at 13:27Z) say:

| PVE node | booted (UTC) | guests that matter |
|---|---|---|
| twin-bottom | 07:38:57 | talosw03 (qemu/113, back 07:39:25), **gasha01** (qemu/104, back 07:39:25 — the external Ceph/NFS VM) |
| twin-top | 07:38:59 | **talosw02 (qemu/108) — not started until 11:13:28** |
| pve04 | 07:38:59 | pvedash (lxc/107, the API LB the exporter uses), desktop VMs |
| HaynesIntelligence | 07:41:04 | talosw01 (qemu/103, back 07:41:38, onboot=1), nas01 (lxc/101) |
| pve-filet02 | 07:48:56 | nut01 (lxc/124) |

So it was not "one Proxmox host": **all five PVE nodes rebooted within two minutes of the
Switch Pro Aggregation reboot (07:37Z)** — the corosync-quorum-loss → watchdog-fence signature
the "Mitigations → Proxmox" item guessed at. The three worker VMs are on three different
nodes (w01 HaynesIntelligence, w02 twin-top, w03 twin-bottom), which is why w01/w03 came
back at different times, and every VM with onboot set came back with its node. talosw02
is the only Talos VM without it. gasha01 (Prometheus + Loki storage, the NFS server behind
home-assistant/immich/appdaemon) also fenced and came back; its own onboot state is
unknown from this data because the exporter's config collector only read the node that
answered via the LB (HaynesIntelligence) — fixed by the per-node scrapes in the PVE alerting PR.

Lesson for "why did nothing tell us": the data was there; there were no PrometheusRules on
it and no dashboard. The PVE alerting PR adds `PVEVMStopped` / `PVEVMNotOnboot` /
`PVENodeDown` / `PVENodeRebooted` / `PVEExporterNoData` and the community "Proxmox via
Prometheus" board.

## Follow-up window 14:17–14:45Z (10:17–10:45 EDT) — EMQX hardening, one self-inflicted outage

Lane A of the post-incident work: the EMQX core now prefers the bare-metal masters (#2801, running
on talosm02), the retainer limits that broke HA's retained-discovery fetch are fixed live and in
git (#2800: 8MB / unlimited delivery — the broker had been enforcing 1MB since 2026-06-05 because
of a PVC-resident override, see `.agents/runbooks/emqx-config-drift.md`), and zigbee2mqtt's MQTT 5
maximum packet size is 10 MiB (#2802 — the `frame_is_too_large` lines were z2m's own 1 MiB client
limit, not the broker's). Cost: the operator's blue-green killed the old core before the new one
could stand alone → **MQTT down 14:19–14:30:45Z (12 min)**, recovered by giving the new core a
fresh data volume; the retained store was rebuilt by the z2m restart. Details, timeline and the
procedure for next time are in the runbook's "Lessons from the 2026-09-09 window".

## Addendum 14:30Z (10:30 EDT) — root cause from the UniFi controller + Proxmox, and what changed

Evidence gathered by the dev-env UniFi lane (`~/work/unifi-findings-2026-09-09.md` on the
dev-env PVC has the full tables); this corrects two premises above.

**1. It was the UniFi auto-firmware rollout — confirmed from the controller, not inferred.**
Site setting `mgmt.auto_upgrade = true`, `auto_upgrade_hour = 3` (America/New_York). Six
switches took `7.5.10.17129 → 7.5.15.17146` (the Pro Aggregation from `7.4.1.16850`), exact
`startup_timestamp`s 07:18:37 → 07:47:03Z; the Shed Flex and Cloffice Flex got no firmware and
rebooted as PoE collateral of their parents. The UDM SE did **not** reboot (up since 08-25 —
its own console update, same 3 AM window). No AP rebooted. UniFi offers no per-device
exclusion, so on Tom's go the setting was flipped `true → false` at ~14:25Z (read back from
the controller). The event log itself is unreachable on Network 10.6.101 via API.

**2. The three workers are on three different Proxmox hosts, not one — and all five hosts
rebooted.** talosw01 = qemu/103 on HaynesIntelligence (agg. port 6), talosw02 = qemu/108 on
twin-top (port 11), talosw03 = qemu/113 on twin-bottom (port 9). `pve_uptime_seconds` puts
twin-bottom / pve04 / twin-top kernel starts at 07:38:21–22Z (within one second),
HaynesIntelligence 07:40:27Z, pve-filet02 07:48:19Z. That is not a power event (PDUs kept
26–327 d uptimes) — it is **Proxmox HA watchdog fencing on corosync quorum loss** when the
aggregation switch every node is single-homed into rebooted at 07:37:10Z. "Half the cluster
shares one physical failure domain" is true, but the domain is the *switch*, not a host.

**3. HA is armed by five helper guests, none of them Talos.** `/cluster/ha/resources`:
vm:104 gasha01, ct:105 nut2700, ct:106 cephdash, ct:107 pvedash (all `started`), vm:109
ubuntu01 (`ignored`). The Talos VMs are not HA resources; they simply die with their hosts.
Removing those five from HA disarms the fence — a network partition then leaves nodes running
with a read-only cluster config. `pve_onboot_status` now reads 1 for every guest including
talosw02.

**4. TubesZB:** both dongles are PoE-powered from Switch Pro Max 48 PoE (Z-Wave port 12,
Zigbee port 14) and power-cycled with its 07:24:55Z reboot — that is the 07:24Z wedge. The
12:19Z wedge had no switch event; port 12 shows 9 link-downs since 07:25Z versus 0 on the
Zigbee port beside it — the Z-Wave dongle's Ethernet link is flapping on its own.

**Follow-through:** Tom's ruling is that the Proxmox fixes are not done by hand — dev-env gets
Proxmox access instead (saga `dev-env` backlog 14, runbook `proxmox-access.md`). The
"Outside this repo (Tom) — Proxmox" bullet above is superseded by that item; the HA removal
itself needs `Sys.Console` and awaits Q-1 there.
