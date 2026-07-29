# 03 — Uptime SLI: Gatus apex check + blackbox Probe + alerting

**Status:** done — [PR #2280](https://github.com/thaynes43/haynes-ops/pull/2280), live-verified:
gatus endpoint `external_haynesnetwork` UP (apex via public resolver, 1m interval, pushover 3/3),
blackbox Probe `haynesnetwork` feeding `probe_success=1` under `BlackboxProbeFailed`. Bespoke
`gatus.io/enabled` ConfigMap co-located with the app (the shared component cannot express the apex
or a strict `== 200`). Badge contract for plan 04: in-cluster base
`http://gatus.observability.svc.cluster.local:80`, uptime ratio (plain-text float 0..1) at
`/api/v1/endpoints/external_haynesnetwork/uptimes/{1h|24h|7d|30d}`; the `/statuses` JSON's
top-level `uptime` is null — use the dedicated uptimes endpoints.
**Repo:** haynes-ops (`kubernetes/main/apps/observability/gatus/` + frontend app kustomization)
**Depends on:** nothing
**Parallel with:** 01, 05, 06

## Goal

`haynesnetwork.com` uptime becomes a continuously measured, alerting, queryable metric — the
saga's success metric and the badge's data source.

## Why

Nothing measures the front page today: Gatus has only DNS checks, blackbox only icmp device
probes. An outage is currently discovered by a human noticing.

## Approach (high level)

Instantiate the shared external-check component
(`kubernetes/shared/components/gatus/external/configmap.yaml`) for the app — note it templates a
subdomain URL, and the front page is the **apex**, so either parameterize it apex-aware or write a
bespoke `gatus.io/enabled` ConfigMap: external HTTP GET on `https://haynesnetwork.com/api/health`
via public resolver, condition `[STATUS] == 200`, sensible interval, pushover alert with
failure/resolve thresholds. This exercises the full WAN path (Cloudflare edge → tunnel → traefik →
pod) — exactly what "the front page is up" means.

Also REQUIRED (owner ruling, decision 3 — two independent alarm paths): a blackbox `Probe`
(module `http_2xx`) on the apex, which the existing `BlackboxProbeFailed` critical rule covers
automatically, giving `probe_success` in Prometheus alongside Gatus's `gatus_results_*` series.
Gatus and Prometheus live on different nodes; either one alone can page.

## Acceptance

- Gatus status page shows the apex endpoint with history; `gatus_results_endpoint_success` visible
  in Prometheus; a forced failure (scale app to 0 in a test window — owner-visible action, do it
  inside plan 07's drill instead if timing allows) pushes a Pushover alert and a resolve.
- Uptime percentage for 24h/7d/30d retrievable from the Gatus API (the badge contract for 04).
- `probe_success{...haynesnetwork...}` visible in Prometheus and covered by `BlackboxProbeFailed`.
