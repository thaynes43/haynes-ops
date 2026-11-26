# UniFi update runner

`upgrade-unifi` runs daily at 03:15 America/New_York. Every successful inventory
read publishes a UTC-dated Markdown table, machine-readable inventory and queue,
and a one-line summary in `upgrade-agent/unifi-inventory`. Use this command to read it.

```sh
kubectl -n upgrade-agent get configmap unifi-inventory -o 'jsonpath={.data.inventory\.md}'
```

`First seen offered` is the first daily observation of the current offer. It carries
forward while the same name, model and installed version continue to report an
offer. The clock resets if the offer disappears, the device is renamed or its
firmware changes. A failed integration read leaves the previous inventory intact.
The short status line lives in `summary.txt` and the Job log. The existing morning
digest only consumes the PR lane's summary.

The queue sorts indoor APs, outdoor APs, leaf switches, Pro Max 16/24, Pro Max 48,
USW Aggregation, Pro Aggregation, the gateway and the cable modem. Within a tier
it sorts by device name. The model allow list comes from OP658. The four power
and WAN accessory models are inventory-only. The Pro Max 48 and attended devices
require a current approval to appear in the queue. Aggregation switches, the gateway
and cable modem require an attended window under OP667 or OP664. The CronJob
reports their approvals for the operator and selects only unattended devices.

## Approvals

Edit `kubernetes/main/apps/upgrade-agent/app/unifi-approvals.yaml` through a PR
after Tom approves the named device for a specific night. The file uses the JSON
subset of YAML so the runner can parse it without a dependency. Use the exact
device name and installed version from the inventory, the ID of Tom's tracker
approval comment, and UTC timestamps. The end of the approval window is exclusive.
An approval for a Pro Max 48 must also record that Home Assistant has been warned.

```json
[
  {
    "device": "Example AP",
    "from_version": "1.2.3",
    "approved_by_ref": 12345,
    "not_before": "2026-11-27T08:00:00Z",
    "not_after": "2026-11-27T09:00:00Z"
  }
]
```

The executor selects at most one approved device between 01:00 and 06:00
America/New_York, checks that Plex, PlexOps and HaynesTower all report zero
streams, verifies the current offer through the integration API, captures the
OP662 baseline and requests an MCP dry run. An unreadable Plex count skips the
attempt. With `UNIFI_LIVE_MODE: "false"`, successful pre-checks end with
`would upgrade <device>`. The committed approvals list is empty.

Tom's separate approval on OP657 is required before setting `UNIFI_LIVE_MODE` to
`"true"` in `app/cronjob.yaml` through GitOps. Live mode checks the offer and Plex
again and records a pending attempt before confirmation. The gate polls up to
15 minutes for an AP or 30 for a switch, measured from baseline capture. The
CronJob has no Job retries. A pending or stopped live attempt blocks later live
runs until the operator resolves it. Read `last-run.json` for the latest result
and `live-attempts.json` for the blocked or already used approvals.

The Job uses the existing UniFi MCP bearer field and Plex session credentials
through the ExternalSecret. Its Role reads and updates only the inventory ConfigMap.
Egress is limited to DNS, the Kubernetes API, the UniFi MCP service, Prometheus
and the three Plex endpoints. The mounted gate is a byte-for-byte copy of
`scripts/upgrade-agent/unifi-gate.mjs`, checked for drift by the runner's selftest.

```sh
nice -n 19 node scripts/upgrade-agent/unifi-runner-selftest.mjs
nice -n 19 node scripts/upgrade-agent/unifi-gate-selftest.mjs
```
