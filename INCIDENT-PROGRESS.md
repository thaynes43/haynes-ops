# Incident haynes-ops-1019-111525 — hot NVMe alerts

Incident/story record: https://github.com/thaynes43/haynes-ops/issues/4101
Branch: agent/haynes-ops-1019-111525 · Owner: this session · Investigator: 1x Sonnet 5.5 · Checkpoint: ~11:41 EDT (25 min from issue creation 11:16:39 EDT / 15:16:39Z)

## Status log (EDT, verified with `date`)
- 11:16:39 story saved as #4101 (createdAt 15:16:39Z).
- ~11:17 one sonnet-worker investigator dispatched (read-only). Host/drive/temperature/risk: not yet known.
- 2026-10-19 11:18 EDT [investigator] CHECKPOINT: NOT a thermal problem (no urgent physical action). Host haynestower (Unraid NAS), ALL FOUR NVMe nvme0-3 alert (8 alerts, SmartDeviceHighTemperature, severity critical -> pushover). Real current temps nvme0-3 = 34/35/38/34 C. The alerts fire on smartctl_device_temperature series with temperature_type=op_limit_max(85)/critical_limit_max(95) -- the drive's rated LIMITS, not temperatures -- because the rule expr 'smartctl_device_temperature > 65' has no temperature_type="current" filter. Model/serial still pending (hw-ssh).
- 2026-10-19 11:19 EDT [investigator] FINDINGS: false alarm. Exporter :latest recreated on NAS 2026-10-12 03:31 EDT -> new op_limit_max(85)/critical_limit_max(95) series trip unfiltered rule (>65). Real temps 34-38C (7d max 44C). Flap-resets from single-scrape dropouts cause the repeated Pushovers. Drives CT2000P3PSSD8 serials 2331E865B710/B76D/B74C/B742 (nvme0-3). Silence proposed, not applied. Report: .agents/reports/hot-nvme-2026-10-19.md
- 2026-10-19 11:21 EDT [coordinator] VERIFIED independently: rule `smartctl_device_temperature > 65` (for 15m, critical) has no temperature_type filter (prometheusrule.yaml:15); live Prometheus shows nvme0-3 temperature_type=current = 35/35/37/35 C, plus op_limit_max=85 and critical_limit_max=95 series that trip the rule. VERDICT: false alarm, monitoring change (exporter recreated 2026-10-12 03:31 EDT added limit series). No urgent physical action. Investigation ACCEPTANCE MET except nothing applied: silence NOT applied; rule-fix PR NOT opened (outside read-only scope) -- awaiting Tom's go. Full report: .agents/reports/hot-nvme-2026-10-19.md
