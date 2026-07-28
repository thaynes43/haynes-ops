# 03 — Uptime SLI: Gatus apex check + alerting

**Status:** planned
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

Optional secondary (decide in-session, cheap): a blackbox `Probe` (module `http_2xx`) on the apex,
which the existing `BlackboxProbeFailed` critical rule covers automatically, giving
`probe_success` in Prometheus alongside Gatus's `gatus_results_*` series.

## Acceptance

- Gatus status page shows the apex endpoint with history; `gatus_results_endpoint_success` visible
  in Prometheus; a forced failure (scale app to 0 in a test window — owner-visible action, do it
  inside plan 07's drill instead if timing allows) pushes a Pushover alert and a resolve.
- Uptime percentage for 24h/7d/30d retrievable from the Gatus API (the badge contract for 04).
