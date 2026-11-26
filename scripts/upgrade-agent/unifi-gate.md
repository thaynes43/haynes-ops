# UniFi update health gate

Capture the baseline immediately before the update. Pass the full firmware version
from `target.version` to each check, including the build number.

```sh
scripts/upgrade-agent/unifi-gate snapshot --device "Workshop AP" > baseline.json
scripts/upgrade-agent/unifi-gate check --device "Workshop AP" --baseline baseline.json \
  --target-not "1.2.3.12345" --deadline-min 10 --client-drop-max 0.3
```

| Exit | Result | Meaning |
| --- | --- | --- |
| 0 | GREEN | Recovery and health checks passed within the deadline. |
| 1 | WAIT | Target recovery is still in progress inside the deadline. |
| 2 | STOP | A stop condition fired, required evidence was unusable, or the deadline passed with a check still short. |

**On STOP, halt and report the reasons to the update owner. Never downgrade or
retry the update automatically.** Keep the original baseline for the whole
attempt. Its capture time starts the deadline. A GREEN observation must finish
before the deadline. A pre-update snapshot includes any firing alerts on the
deny list so the operator can see them before proceeding.

Recovery shortfalls WAIT until the deadline and only then STOP: a peer that went
offline (devices behind a rebooting switch, or powered by its PoE, drop with it),
site counts, the site's wireless client total, and switch links and PoE. A peer still
offline once the target is back on new firmware and settled is a STOP at once. Site
counts are read only after the target reports its new firmware, because the
controller counts it as disconnected while it is updating. Learned on the first live
rollout, 2026-11-26 (OP657): AP clients roam to neighbouring APs during a reboot and
stay there, so an AP is judged by the site-wide total; this line's WAN latency
spikes reach 125 ms in 48 hours, so the margin is 100 ms. For APs pass
`--deadline-min 15`; for switches with PoE devices behind them, 25 to 30.

The program sends GET requests to the Prometheus query API. It defaults to
`kube-prometheus-stack-prometheus.observability.svc.cluster.local:9090` over HTTP.
Set `PROMETHEUS_URL` to use an authorized Prometheus endpoint or read-only proxy.
Snapshots serialize the fields shown below and omit raw source, address, hardware
ID, and WAN monitor target labels.

| Evidence | Prometheus metrics and policy |
| --- | --- |
| Exporter and site | `up{job="unpoller"}` must be 1. Current queries use a two-minute lookback. `unpoller_device_info` and positive `unpoller_device_uptime_seconds` identify online devices at the target site. Snapshot reconciles that inventory with `unpoller_site_adopted` and `unpoller_site_disconnected`. A baseline peer going offline triggers STOP even during the target reboot. After the target returns, adopted counts must be at least their baseline values, and disconnected and pending counts must be at most their baseline values in each subsystem. |
| Target | Firmware must change and `unpoller_device_upgradable` must read 0. The last two raw uptime samples must be fresh and increasing and place the reboot after baseline capture. The inferred boot times may differ by up to 65 seconds to account for the one-minute exporter and scrape cadence. The target settles for 120 seconds of reported uptime before the AP or switch test. |
| AP | Sum the `user` and `guest` buckets of `unpoller_device_stations` over every AP of the site. At least 70% of the baseline site total must be back by default. A baseline without the site total falls back to the target AP's own count. |
| Switch | Positive `unpoller_device_port_port_speed_bps` shows a negotiated link. Positive `unpoller_device_port_poe_watts` shows PoE draw. Check each baseline port number with either condition for the corresponding link or draw after settle. Unpoller can omit inactive port series. |
| WAN | Each WAN group online in the baseline must still be online in `unpoller_device_wan_status`. Each group must retain at least as many nonzero `unpoller_device_wan_monitor_latency_average_seconds` readings. The slowest reading may exceed the baseline maximum by up to 100 ms. |
| Alerts | Query `ALERTS{alertstate="firing"}`. STOP on Gatus alerts, `TargetDown` for Gatus, `NodeNotReady`, `KubeNodeNotReady`, `NodeRebooted`, and any alert starting with `PVE` or `Talos`. Known gap: only `NodeRebooted`, the `PVE*` rules and the kube-prometheus-stack defaults exist as rules today. Gatus reports through its own alerting, so a Gatus endpoint check (`gatus_results_endpoint_success`) is a follow-up. |

A gateway reboot can interrupt Prometheus or its UniFi telemetry. If the site or
WAN cannot be observed, the gate returns STOP.

Run the offline fixture suite with
`node scripts/upgrade-agent/unifi-gate-selftest.mjs`.
