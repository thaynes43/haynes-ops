# haynesnetwork-ha — replicas, node-failure resilience, and measured uptime for the front page

**Status:** ready (2026-07-28) — exploration done, decisions ratified by the owner, backlog executable
**Operating mode:** autonomous per the dev-env precedent — agents author, review, merge, and verify
saga PRs end-to-end; pause only for genuinely new decisions outside the recorded ones or
destructive/irreversible actions. App-side legs follow the haynesnetwork repo's own docs-first
process (they mint numbered plans in `haynesnetwork/.agents/plans/`).

## Vision

`haynesnetwork.com` is the front page of the lab, and today it dies with a single pod: the app runs
**one replica** (currently on talosw01) behind an edge that is otherwise already redundant. The
goal of this saga is that **no single node failure takes the front page down**, that uptime is
**continuously measured** as a first-class metric with alerting, and that the front page **wears
its own uptime** as a badge. When this saga closes, we should be able to drain or lose any one of
the six nodes and watch the dashboards shrug.

Out of scope, by owner directive (2026-07-28): filling in the rest of the sparse homepage. That is
a **future separate saga** to be brainstormed after replicas land — this saga only ships the
uptime badge as the first new front-page element.

## Current state (verified 2026-07-28, live cluster + both repos)

The serving path, Internet → pod, with replica counts:

| Hop | What | Redundancy today |
|---|---|---|
| Cloudflare edge + DNS | proxied CNAME → `ingress-ext.haynesnetwork.com` → tunnel (external-dns-cloudflare, 1 replica) | Cloudflare-side HA; external-dns only affects record *changes* |
| Cloudflare Tunnel | `cloudflare-tunnel` Deployment, **2 replicas**, origin = `traefik-external` ClusterIP | connector HA; one logical tunnel (accepted, see Hard news) |
| traefik-external | Deployment, **3 replicas** across talosm03/talosw01/talosw02, LB IP via Cilium LB-IPAM + L2 announce (auto failover), `externalTrafficPolicy: Cluster` | good, but **no PDB / no spread constraint** — the 3-node spread is luck, not policy |
| **haynesnetwork-main** | Deployment, **1 replica** (talosw01), RollingUpdate, health probe process-only | **THE SPOF — this saga's reason to exist** |
| PostgreSQL | cnpg `postgres16`, **3 instances** on distinct nodes (primary talosw03), automated failover, PDBs present, `-rw` service abstracts promotion | healthy; storage is local `openebs-hostpath`, HA via streaming replication |

Replica-readiness audit of the app (haynesnetwork@main): **no correctness blockers.** Sessions are
DB-backed (Better Auth drizzle adapter — no in-memory session store), OAuth state is DB/cookie,
zero runtime filesystem writes (standalone Next.js, no uploads), all background jobs live in the
~18 `concurrencyPolicy: Forbid` CronJobs (none in the web pod), pg pool is 10 conns/pod against a
shared `max_connections=400` (2–3 replicas are nothing). Known multi-replica soft spots, all
pre-flagged in the app repo's own docs:

1. **Migrate init-container per pod, no advisory lock, non-idempotent migrations** — a cold
   multi-pod start or simultaneous reschedule races the migrator into a transient, self-healing
   `Init:Error` loop (rolling updates and a plain 1→2 bump serialize via maxSurge=1 and are safe).
2. **Better Auth rate limiter is in-memory per replica** (fail-open ×N) — app design doc 002 and
   backlog-recon item O-5 already call this out.
3. **Per-pod memo caches** (e.g. the play-scoreboard, ADR-068 C-05) — accepted skew at household
   scale; no action.

Uptime tooling already in the cluster, none of it pointed at haynesnetwork yet: **Gatus** v5.36
(sqlite on ceph-block, native uptime/badge API, pushover alerts, `gatus.io/enabled` ConfigMap
auto-loader + a shared external-check component at
`kubernetes/shared/components/gatus/external/configmap.yaml`), **blackbox-exporter** (`http_2xx`
module, Probe CRD, an existing `BlackboxProbeFailed` critical rule), **kromgo** (Prometheus→SVG
badges), kube-prometheus-stack + Alertmanager→Pushover. All status tooling is **LAN-only**
(traefik-internal) — nothing needs to become public for this saga (see Decision 4).

## Hard news (constraints we accept, so later sessions don't re-litigate)

- **The WAN path is one logical Cloudflare tunnel** (single tunnel ID, single account). Two
  cloudflared replicas cover connector/node loss, but Cloudflare itself and the tunnel remain a
  single logical dependency. Accepted for a home lab; a secondary ingress path is not this saga.
- **The measurement plane is not HA** (prometheus, alertmanager, gatus are 1 replica each). A node
  failure can blind monitoring while the app stays up. Accepted: Gatus alerts via pushover on its
  own, and measurement gaps ≠ serving gaps. Revisit only if it bites.
- **Postgres volumes are local-path** — durability comes from 3-way streaming replication across
  distinct nodes, which is the intended cnpg pattern here. We keep the instances spread; we do not
  move to replicated block storage in this saga.
- **The uptime badge is rendered by the app it measures.** When the app is down, the badge is down
  with it — that is fine, the badge is a trophy on the page, not the outage detector (Gatus +
  Pushover are the detector).

## Decision log

| # | Date | Decision | Status |
|---|------|----------|--------|
| 1 | 2026-07-28 | Saga lives in haynes-ops (dev-env precedent); app-side legs mint numbered plans in `haynesnetwork/.agents/plans/` and are cross-linked from the backlog table. | DECIDED (convention) |
| 2 | 2026-07-28 | Target **replicas: 3** with a `topologySpreadConstraint` across nodes + PDB `minAvailable: 2` — owner ruling 2026-07-28 (over the proposed 2): survives a double fault and keeps two serving through any drain. | DECIDED (owner) |
| 3 | 2026-07-28 | **Run BOTH probes** (owner ruling 2026-07-28, home-operations precedent): Gatus is the uptime source of record (apex check, history, badge math, direct Pushover) AND a blackbox `Probe` feeds `probe_success` into Prometheus under the existing `BlackboxProbeFailed` critical rule — two independent alarm paths across the single-replica monitoring plane. | DECIDED (owner) |
| 4 | 2026-07-28 | The front-page badge is **rendered app-side from the in-cluster Gatus API** (server-side fetch → tRPC → token-themed `@hnet/ui` component). No public exposure of gatus/kromgo/grafana, no third-party badge iframe. | DECIDED (owner 2026-07-28) |
| 5 | 2026-07-28 | Migrator serialization via **`pg_advisory_lock` in the app's migrate script**, not a Flux-ordered migration Job — keeps the GitOps surface unchanged and the init-container pattern intact. | DECIDED (owner 2026-07-28) |
| 6 | 2026-07-28 | Homepage content expansion is a **future separate saga** (owner directive) — nothing beyond the uptime badge lands on the page from this saga. | DECIDED (owner) |

## Plan backlog

| Plan | Title | Repo | Depends on | Parallel? |
|------|-------|------|------------|-----------|
| [01](backlog/01-migrator-advisory-lock.md) | Migrator advisory lock (serialize concurrent migrate init-containers) | haynesnetwork | — | yes |
| [02](backlog/02-app-replicas-and-spread.md) | App replicas: 3 + topology spread + PDB | haynes-ops | 01 (soft) | after 01 |
| [03](backlog/03-uptime-sli-gatus.md) | Uptime SLI: Gatus apex check + alerting | haynes-ops | — | yes |
| [04](backlog/04-front-page-uptime-badge.md) | Front-page uptime badge (app dashboard) | haynesnetwork | 03 | — |
| [05](backlog/05-shared-rate-limit-storage.md) | Rate-limit storage: in-memory → database | haynesnetwork | — | yes |
| [06](backlog/06-edge-hardening.md) | Edge hardening: traefik PDB + spread (+ metrics stretch) | haynes-ops | — | yes |
| [07](backlog/07-failure-drill.md) | Node-failure drill + runbook (prove it, measure it) | haynes-ops | 02, 03 | — |
