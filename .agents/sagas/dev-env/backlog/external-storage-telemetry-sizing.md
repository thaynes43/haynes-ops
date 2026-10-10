# External storage telemetry resource sizing

Status: design/sizing follow-up; no workload change authorized by this record.

At the 2026-10-10 external-storage telemetry audit, both GitOps
`kube-prometheus-stack/app/helmrelease.yaml` and the selected live Prometheus
object had a 4000Mi memory limit and no CPU limit. Its CPU request was 500m.
The existing chart comments record an earlier no-CPU-limits sizing decision.

The external Ceph ScrapeConfig reuses the existing Prometheus and
adds no Pod, Deployment, exporter or observer workload. This unit preserves
all Prometheus resources and does not authorize a restart or tuning change.
Before a new collector/observer workload is introduced, give it explicit CPU
and memory limits. Any change to existing Prometheus limits needs a separate
bounded sizing decision using its current workload and scrape-cost evidence;
do not choose a cap or roll Prometheus as a side effect of this scrape.

External Ceph scrape freshness is separate from gasha01 NFS performance,
locking, export identity and quota. Gateway collection/access remains
unavailable and is not supplied by the separate NAS node exporter.
