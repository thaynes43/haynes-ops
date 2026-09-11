# Haynes Quest private MVP operations

- **Status:** In progress
- **Owner:** Codex ops lane under PLAN-004
- **Branch:** `agent/quest-mvp-ops`
- **Application repository:** `/home/dev/work/haynes-quest-overnight-mvp`
- **Infrastructure repository:** `/home/dev/work/quest-mvp-ops`

## Objective

Provision the dedicated `haynes_quest` role and database through GitOps, then
deploy the synthetic fixture preview as a private, LAN-only application after
the application image is published. Keep database administrator and Immich
credentials out of the fixture workload.

## Fixed contracts

- PostgreSQL endpoint: `postgres16-rw.database.svc.cluster.local:5432`.
- Provisioning uses only `haynes-quest-db-init-secret`; the application uses
  only `haynes-quest-secret`.
- The compiled Node server listens on `0.0.0.0:3000`, serves the application and
  `/studio/` from the same origin, and exposes `/healthz` and `/readyz`.
- Fixture deployment requires `NODE_ENV=development` and
  `QUEST_FIXTURE_MODE=true`. It must fail if Immich environment variables are
  present and must never mount `haynes-quest-immich-secret`.
- The release image is `ghcr.io/thaynes43/haynes-quest:sha-<commit>`, pinned by
  immutable digest in GitOps after the application build succeeds.
- The preview route uses `traefik-internal` on `*.haynesops.com`. That controller
  is a LAN LoadBalancer at an RFC1918 address; the zone is published through
  UniFi external-dns and excluded from the Cloudflare external-dns instance.

## Delivery checkpoints

1. Add an idempotent, one-shot `postgres-init` Job and DB-only Cilium egress.
2. Validate rendered manifests, review the diff, and open a checked PR.
3. Squash-merge, reconcile `frontend/haynes-quest`, and verify Job completion.
4. Prove application-credential login from an ephemeral validation Job without
   reading or printing Secret values; record only a boolean/result.
5. After the application image exists, add the HelmRelease, ClusterIP Service,
   LAN-only IngressRoute, health probes, runtime network policy, and immutable
   image digest. Verify the Secret boundary and `/studio/` on the live route.

## Evidence

- 2026-09-11 preflight: all three Haynes Quest ExternalSecrets report
  `SecretSynced`; the Haynes Quest Flux Kustomization reports Ready at the
  fetched main revision; `postgres16-rw` is a ClusterIP service with a ready
  endpoint on port 5432.
- 2026-09-11 private-path audit: `traefik-internal` is a LoadBalancer on
  `192.168.40.203`; the repo documents `*.haynesops.com` as UniFi-published and
  LAN-only, and Cloudflare external-dns excludes `haynesops.com`.

Do not record Secret values, personal fields, private media, or credentialed
Immich response data in this work order.
