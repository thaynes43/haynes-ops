# Haynes Quest private MVP operations

- **Status:** Final catalog image verified; GitOps rollout prepared
- **Owner:** Codex ops lane under PLAN-004
- **Branch:** database PRs from `agent/quest-mvp-ops`; deployment from
  `agent/quest-private-deploy`
- **Application repository:** `/home/dev/work/haynes-quest-overnight-mvp`
- **Infrastructure repository:** `/home/dev/work/quest-private-deploy`

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

## Deployed fixture manifest

Operations PR #2851, merged as `6622a9b0988c69de2c9dedd7e51626ed439d2aea`,
defines:

- one app-template replica listening on port 3000 behind a ClusterIP Service;
- `NODE_ENV=development`, `QUEST_FIXTURE_MODE=true`, the exact private
  `QUEST_APP_ORIGIN`, and `PORT=3000`;
- only `haynes-quest-secret` in `envFrom`, with no Immich or provisioning Secret;
- startup and liveness probes on `/healthz` and readiness on `/readyz`;
- a non-root, read-only container without a service-account token or Linux
  capabilities;
- a `traefik-internal` IngressRoute for
  `https://haynes-quest.haynesops.com`, using the existing wildcard certificate;
- runtime ingress from only the internal Traefik pods and egress to only DNS and
  the PostgreSQL writer service; and
- application commit `71c45b5fdba69cb38c59ccd336935218b322cd5c`, pinned as
  its exact commit tag plus immutable registry digest.

## Deployment procedure

1. Record the application squash-merge commit and successful image-workflow
   digest. Verify the `sha-<merge-commit>` manifest can be pulled from GHCR
   without exposing a registry credential, then replace both placeholder parts.
2. Rebase on current `haynes-ops` main, render and inspect the complete
   Kustomization, open a deployment PR, require all checks to pass, and
   squash-merge it.
3. Declare scoped activity for `frontend,haynes-quest`, reconcile the Flux
   Kustomization, and verify the HelmRelease, Deployment, Service, IngressRoute,
   NetworkPolicy, exact image digest, probes, and allowed Secret reference.
4. Verify `/healthz`, `/readyz`, `/studio/`, and the synthetic fixture journey.
   Through the API, create a synthetic save, retain its signed cookie privately,
   delete the running app pod, and prove the same session and save survive in the
   replacement pod. Delete all private cookie and response files afterward.
5. End the activity declaration immediately after validation. There are no open
   activity declarations at this checkpoint.

## Evidence

- 2026-09-11 preflight: all three Haynes Quest ExternalSecrets report
  `SecretSynced`; the Haynes Quest Flux Kustomization reports Ready at the
  fetched main revision; `postgres16-rw` is a ClusterIP service with a ready
  endpoint on port 5432.
- 2026-09-11 private-path audit: `traefik-internal` is a LoadBalancer on
  `192.168.40.203`; the repo documents `*.haynesops.com` as UniFi-published and
  LAN-only, and Cloudflare external-dns excludes `haynesops.com`.
- 2026-09-11 database bootstrap: PR #2849 merged as `705d49b`. The first Job
  stayed in its retry loop before database mutation because the pod's default
  `ndots:5` issued search-suffixed DNS names outside the exact allowlist. A safe
  probe confirmed the absolute service FQDN resolves. The follow-up pins
  `ndots:1` on only this Job and keeps the DNS boundary exact.
- 2026-09-11 database result: follow-up PR #2850 merged as `fb7bc56`;
  `frontend/haynes-quest` reconciled Ready at that revision and the init Job
  completed once with exit 0 and no restarts. An ephemeral check mounting only
  `DATABASE_URL` authenticated as the expected non-superuser role, confirmed it
  cannot create roles or databases, and confirmed it owns the dedicated
  database. The validation Job was deleted.
- 2026-09-11 integration database: a restricted disposable pod used fresh
  PostgreSQL 16 emptyDir data and a random in-memory password. The server's real
  Postgres integration suite passed 2/2 tests, including reconnect persistence,
  concurrent recovery, and owner scoping. The Job and its data were deleted.
- 2026-09-11 Immich schema smoke: an isolated one-shot Job mounted only
  `haynes-quest-immich-secret` and used the existing Immich-only network policy.
  Immich 3.1.0 returned the expected people pagination types and an eligible
  person on the single bounded page. The person-filtered metadata search returned
  an asset and the expected pagination type; `id`, `type`, `fileCreatedAt`,
  `isArchived`, `isTrashed`, `isOffline`, `visibility`, and `people` were all
  present with the expected JSON types. The Job was deleted without downloading
  media or recording response bodies, identifiers, names, dates, or private fields.
- 2026-09-11 Immich media smoke: the same bounded lookup selected only the first
  eligible person and first person-filtered image, then streamed its preview
  thumbnail with an 8-second timeout and 5 MiB ceiling. The response was a
  289,945-byte JPEG with a matching magic-byte signature. The probe zeroed each
  streamed chunk, wrote no image to disk, emitted no identifiers or metadata,
  and its one-shot Job was deleted.
- 2026-09-11 immutable image: application PR #21 squash-merged as
  `6263dc42443eb5c33943d26f668da386df4900d4`. Main Application workflow run
  `34564020967` passed verification, published provenance, and completed the
  keyless signing step. The exact `sha-6263dc42443eb5c33943d26f668da386df4900d4`
  tag resolves anonymously to
  `sha256:36b363a9e43912691d93a65c776cc9d958101a9d3a4379b7e1f4737e0a3f23b0`;
  its Linux amd64 manifest, config blob, and first layer were also anonymously
  retrievable. Independent attestation-bundle retrieval from this pod remains
  unavailable because its Azure blob host is outside the egress allowlist; the
  publishing workflow's provenance and signing steps are the recorded evidence.
- 2026-09-11 fixture deployment: operations PR #2851 passed the Diff Scope and
  full Flux Local 8.4.0 main/edge test and diff gates, then squash-merged as
  `6622a9b0988c69de2c9dedd7e51626ed439d2aea`. Flux reconciled
  `frontend/haynes-quest` to that exact revision. The Kustomization and
  HelmRelease report Ready, with app-template 5.1.0 installed successfully.
- 2026-09-11 live boundary: one ready application pod runs the exact pinned
  digest with zero restarts. The rendered pod has only the four documented
  plain environment variables and `haynes-quest-secret` in `envFrom`; service
  account token automount is false, UID/GID are 1000, the root filesystem is
  read-only, privilege escalation is false, and all capabilities are dropped.
  The ClusterIP Service has one ready endpoint. The valid runtime Cilium policy
  selects that pod and allows only internal-Traefik ingress plus DNS and
  PostgreSQL-writer egress.
- 2026-09-11 private route: the hostname resolves to internal Traefik at
  `192.168.40.203`. `/`, `/healthz`, `/readyz`, `/studio/`, and
  `/studio/assets/catalog.html` all returned success; the health payloads
  reported `ok` and `ready`. The replacement pod emitted only its normal
  listening message, and the deployment had no warning events.
- 2026-09-11 process-replacement proof: a private signed-cookie session created
  a three-memory chronological synthetic save and recovered the first two
  memories. The state at revision 2 had age 4, the expected three abilities,
  and the child appearance stage. After deleting only that application pod, a
  different pod UID became Ready. The same cookie resolved the same player and
  an identical save view, including stable IDs, manifest, recovered order,
  revision, age, abilities, appearance, rule versions, and timestamps. The last
  memory was then recovered and the journey finished at revision 4. A distinct
  fresh session received 404 for both that save and its media.
- 2026-09-11 audit cleanup and limit: private cookie files were truncated and
  removed, no temporary Jobs were created, and the scoped activity declaration
  was ended. The first audit script attempt stopped after a successful pod
  replacement because of a local JSON-path assertion error; its cookie was also
  removed. It left one inaccessible synthetic-only save at two of three
  memories because the application has no delete-save API. The complete rerun
  left one finished synthetic save. No live database mutation was used to erase
  either record. The first deployed image remains the playable MVP checkpoint;
  the final catalog image is recorded below.
- 2026-09-11 final catalog image: application PR #23 passed all 46 fresh
  PostgreSQL 16 tests plus documentation and container checks, then
  squash-merged as `71c45b5fdba69cb38c59ccd336935218b322cd5c`. Main Application
  workflow run `34568145769` passed verification, published provenance, and
  completed keyless signing. The exact
  `sha-71c45b5fdba69cb38c59ccd336935218b322cd5c` tag resolves anonymously to
  `sha256:2129b02e1d1804ffe3a89edc7e9e2e3eca9258cf18cdaca762130680d8f78675`;
  its Linux amd64 manifest, config blob, and first layer were anonymously
  retrievable. This image contains the completed nine-model catalog and four
  candidate cue WAV auditions, including the studio WAV MIME fix; listening
  and Tom's approval remain pending.

Do not record Secret values, personal fields, private media, or credentialed
Immich response data in this work order.
