# 06 — Edge hardening: traefik PDB + spread (+ metrics stretch)

**Status:** planned
**Repo:** haynes-ops (`kubernetes/main/apps/network/traefik/{traefik-external,traefik-internal}/`)
**Depends on:** nothing
**Parallel with:** 01, 03, 05

## Goal

Make the edge's current good posture (3 replicas each, spread across 3 nodes) a guarantee instead
of scheduler luck, so it stays true through drains and reschedules.

## Why

Neither traefik helmrelease has a PDB, anti-affinity, or topologySpreadConstraint. Today's 3-node
spread happened by chance; a drain or churn could co-locate replicas and quietly concentrate the
edge onto one node. Cilium L2 failover and `externalTrafficPolicy: Cluster` already handle the LB
IP side.

## Approach (high level)

For both traefik instances: `topologySpreadConstraints` on hostname (maxSkew 1) +
`PodDisruptionBudget` `minAvailable: 2`. Flux-local diff must show only the intended additions.

Stretch (separate PR, only if appetite): enable traefik Prometheus metrics + a ServiceMonitor to
unlock a request-level SLI (rate/5xx/latency) for the front page — today no traefik metrics exist
at all, so the uptime story is synthetic-only. Not required for this saga's badge.

## Acceptance

- PDBs active; spread constraints satisfied; a node drain leaves ≥2 replicas of each traefik
  serving and the LAN + WAN paths answering.
- (Stretch) `traefik_service_requests_total` visible in Prometheus for the haynesnetwork service.
