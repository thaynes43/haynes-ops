# SNMP Monitoring

I am skipping [snmp_exporter](https://github.com/prometheus/snmp_exporter) for now as I do not have any devices setup to produce data it can scrape. However, I have things I can setup later:

- For Unraid we need to follow a [setup guide](https://forums.unraid.net/topic/96570-guide-monitoring-network-devices-with-snmp/) we have some extra setup required.
- Unifi also needs a [custom config](https://github.com/zygiss/snmp-exporter-unifi?tab=readme-ov-file) to get it ready.
- APC Ups is configured [here](https://github.com/onedr0p/home-ops/tree/main/kubernetes/main/apps/observability/snmp-exporter) and we can do this once we get the rack one at the new place.

## Unifi

We may want to skip snmp for unifi and go straight to [unpoller](https://github.com/unpoller/unpoller) which I'll also put on the back burner for now.

