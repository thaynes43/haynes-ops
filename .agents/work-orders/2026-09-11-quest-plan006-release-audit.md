# PLAN006 haynes-quest playtest release audit

Date: 2026-09-11 UTC  
Status: Ready for an image-only playtest pin after the application image is published. No image pin or cluster state was changed during this audit.

## Scope

This audit covers the existing `haynes-quest` / `haynes-quest-playtest` release isolation, the additive friendly-sidecar migration, the application and GitOps release gates, and the evidence required for the later reviewed rollout. It excludes dev-env changes or restarts, OAuth, real photos, secret reads, and all cluster mutations.

The later GitOps patch should contain only:

- `kubernetes/main/apps/frontend/haynes-quest/app/playtest-helmrelease.yaml`: update the source-commit comment and `tag:` to the exact published `sha-<application-squash-merge-sha>@sha256:<digest>`.
- This release work-order record, updated with publication, CI, reconciliation, and rollout evidence.

The normal deployment, ingress routes, Services, NetworkPolicies, Secret references, Kustomization membership, and dev-env resources must remain unchanged.

## Audited baseline

At the read-only audit point:

- Flux Kustomization `frontend/haynes-quest` was Ready and applied `main@sha1:f1e5133a`.
- The playtest workload ran application commit `b15bc98` at digest prefix `d7eddba`, pod UID `96a8569c-5397-41df-b85e-36f3e4cc6ecc`, Ready with zero restarts.
- The normal workload ran application commit `3502ac7` at digest prefix `743bca`, pod UID `d3e0117d-9dc5-4716-9343-2a740147171b`, Ready with zero restarts.
- The dev-env pod UID was `c3a94756-af35-405e-93bc-eb05c2979d3a`; all three containers were Ready with zero restarts.
- Both HelmReleases were Ready with successful upgrades and both Deployments were 1/1 Ready.
- Playtest and normal use distinct Deployments, Services, selectors, EndpointSlices, host routes, and name-scoped NetworkPolicies. Their service cluster IPs were respectively `10.43.174.201` and `10.43.214.26`.
- Both workloads use only `haynes-quest-secret` through `envFrom`. No secret value was read.

The two deployments deliberately share the same PostgreSQL database and Secret. Host isolation remains sound for ordinary browser use because the application cookie has no `Domain` attribute and is therefore host-only; it is also `HttpOnly`, `SameSite=Lax`, `Secure`, and scoped to `/`. The deployments use different random fixture owners. Manually transplanting a cookie between hosts is outside that isolation boundary; this is not cryptographic tenant separation.

## Schema compatibility

The candidate application adds `migrations/0004_friendly_state.sql` with SHA-256 `4a0804f89342af6d66aac02442f97ff76afa93fcaeaf4c2f31d5978826407dd9`. It adds nullable `quest_saves.friendly_state jsonb` plus a constraint permitting only `NULL` or a JSON object.

The already-deployed migration files remain byte-identical:

| Migration | SHA-256 | Known by normal | Known by current playtest |
| --- | --- | --- | --- |
| `0001_initial.sql` | `6a9404f...` | yes | yes |
| `0002_*.sql` | `6f18f85...` | yes | yes |
| `0003_*.sql` | `746b9e9...` | no | yes |
| `0004_friendly_state.sql` | `4a0804f89342af6d66aac02442f97ff76afa93fcaeaf4c2f31d5978826407dd9` | no | no |

Compatibility findings:

- Old inserts omit the new column and therefore store `NULL`.
- Old reads enumerate the old Drizzle columns rather than using `SELECT *`, so the new column is ignored.
- Old updates set named legacy fields and preserve `friendly_state`.
- Old migration runners check only their local migration filenames, so the newer ledger row does not invalidate them.
- New code accepts a `NULL` sidecar, presents a pristine synthesized friendly state without writing, and persists it atomically on the first successful gameplay action. A rejected action leaves revision zero and the sidecar null.
- New saves initialize the sidecar. Application parsing validates the exact six friendly identities and progress rules; the database constraint intentionally enforces only object-or-null shape.
- The migrator serializes startup migrations under PostgreSQL advisory lock `730204004`, validates checksums, and applies each migration in its own transaction. The server does not listen until migration completes, so a Ready new pod is evidence that `0004` succeeded.

The migration has no SQL lock or statement timeout. An `ACCESS EXCLUSIVE` lock wait or constraint scan can outlast the 300-second startup probe and cause a rollout retry. The current low-volume fixture database makes this an operational risk rather than a release blocker. If startup exceeds the probe window, inspect database lock contention; do not alter the schema manually.

The application tests specifically cover null-sidecar lazy behavior, successful persistence, retry behavior, rejection of future-level friendly progress, atomic PostgreSQL persistence, owner scoping, and preservation of a pre-`0003` v1 row through migrations `0003` and `0004`.

## Rollback boundary

The additive database migration is backward-compatible and may remain after an image rollback. Existing v1/v2 saves are forward-compatible with the candidate application.

Application data has a narrower rollback boundary: after the candidate creates a parody-catalog-v3 Besties save, the currently deployed playtest image does not know that catalog/period and may report `SAVE_DATA_INVALID` or fail to list that owner's saves after rollback. This affects the playtest host only under normal host-only session use; it does not make the normal deployment's saves unreadable. Treat an image rollback after v3 writes as recovery requiring a forward fix or removal of the synthetic v3 fixture data, not as a fully transparent rollback.

## Release prerequisites and gates

### 1. Publish and verify the application image

Current hard prerequisite: there was no open application PR and no published digest at audit time. Do not prepare or merge the GitOps pin until the application PR is reviewed, its checks pass, it is squash-merged, and the immutable image digest is published.

Application PR checks expected:

- `Application / verify`
- `Application / container-check`
- `Documentation / build`

After squash merge to `main`, record successful:

- `Application / verify`
- `Application / image`
- `Documentation / build`

`Application / image` must publish `ghcr.io/thaynes43/haynes-quest:sha-<application-squash-merge-sha>` with Buildx provenance mode `max`, SBOM, a pushed GitHub provenance attestation, and a keyless cosign signature. Preserve the job summary's `ghcr.io/...@sha256:...` value as evidence.

Verify the exact tag through the anonymous GHCR manifest endpoint using an `Accept` header that includes OCI index, OCI image manifest, Docker manifest list, and Docker image manifest media types. Record HTTP 200 and `Docker-Content-Digest`; it must equal the image-job digest and the digest referenced by provenance/signing evidence. Do not record or print the bearer token.

Suggested application publication artifact: `test-results/release-publication.json`, containing the application merge SHA, image tag, immutable digest, registry status/digest, workflow run URLs or IDs, provenance/attestation result, cosign verification result, and UTC verification time.

### 2. Review the image-only GitOps PR

The later ops PR should change only the playtest image reference and this work-order evidence. Its file list must exclude the normal HelmRelease, both ingress definitions, Services, NetworkPolicies, Kustomizations, Secrets, and every dev-env resource.

Inspect all nine expected check jobs:

1. `Diff Scope - Success`
2. `Flux Local - Filter`
3. `Flux Local - Test (main)`
4. `Flux Local - Test (edge)`
5. `Flux Local - Diff (main/helmrelease)`
6. `Flux Local - Diff (main/kustomization)`
7. `Flux Local - Diff (edge/helmrelease)`
8. `Flux Local - Diff (edge/kustomization)`
9. `Flux Local - Success`

The branch-protection aggregate contexts are `Diff Scope - Success` and `Flux Local - Success`; the individual jobs still require review.

Rendered CI evidence is posted as sticky PR comments rather than a downloadable workflow artifact. Preserve links or captures under these names:

- `<ops-pr>/main/helmrelease`: exactly one rendered workload change, Deployment `frontend/haynes-quest-playtest`, with only its container image changing from the audited old reference to the exact new tag and digest.
- `<ops-pr>/main/kustomization`: exactly one aggregate change for HelmRelease `frontend/haynes-quest-playtest`, reflecting the same image substitution.
- `<ops-pr>/edge/helmrelease`: no rendered change and normally no sticky diff comment.
- `<ops-pr>/edge/kustomization`: no rendered change and normally no sticky diff comment.

Any additional rendered resource change is a blocker to merging the ops PR.

### 3. Reconcile and verify the rollout

Immediately before the authorized rollout, record a scoped activity for `frontend,haynes-quest,haynes-quest-playtest`. After the checked ops PR is squash-merged:

1. Reconcile Kustomization `haynes-quest` in namespace `frontend` with source refresh.
2. Verify the Kustomization is Ready at the exact ops merge revision and the playtest HelmRelease is Ready at its observed generation.
3. Verify the playtest Deployment rollout completes at 1/1 Ready and the running pod reports the exact intended tag, digest, and image ID.
4. Record the new playtest pod UID, Ready state, and zero restart count.
5. Verify the normal pod UID, image ID/digest, Ready state, and restart count remain at the audited baseline.
6. Verify the dev-env pod UID and all container restart counts remain at the audited baseline.
7. Verify selectors, Service cluster IPs, ready EndpointSlices, ingress hosts, NetworkPolicies, and Secret references are unchanged.
8. Verify `/healthz` and `/readyz` through the private LAN TLS/DNS routes. New-pod readiness also proves the startup migration completed.
9. Resume an existing synthetic v2 playtest save, then create and resume a synthetic v3 journey exercising the six friendlies and the logical Bickering Besties encounter. Do not use OAuth or real photos.
10. Verify the deployed catalog resolves the approved current assets, including the expected 23 GLBs and four gameplay cues.
11. End the activity declaration promptly and add UTC evidence to this record.

Suggested rollout evidence artifact: `test-results/release-rollout.json`, containing the ops merge revision, Flux and HelmRelease generations/conditions, old and new playtest pod identity/image/restarts, unchanged normal and dev-env identities/images/restarts, endpoint/route/policy checks, health results, migration readiness inference, synthetic v2/v3 smoke results, catalog asset counts, and activity ID/timestamps.

## Decision

There is no Kubernetes isolation or additive-schema blocker to the later image-only playtest release. The release is not actionable until the application PR is merged and its exact image digest, provenance, signature, and public registry response are recorded. The non-transparent rollback behavior for newly written v3 saves must remain explicit in the review and release evidence.

## Publication and prepared pin

Application PR33 merged as `d932b484a32d80e4cf3ad2ece1e99d3620d4c55c`. Main Application run34659017677 and Documentation run34659017660 passed. All 294 tests passed, including ten real PostgreSQL cases. Buildx used maximum provenance and SBOM; GitHub provenance attestation, cosign signing and image recording steps all succeeded and named the same digest.

Exact published reference:

```text
ghcr.io/thaynes43/haynes-quest:sha-d932b484a32d80e4cf3ad2ece1e99d3620d4c55c@sha256:582737215e73ab34dc22445feac16f1773c20b91c27b4f82f3fdcec3ec077a27
```

Anonymous registry lookup returned HTTP200; both Docker-Content-Digest and the downloaded OCI index's computed SHA256 matched the CI digest. Independent `gh attestation verify` could not fetch GitHub's attestation bundle because dev-env could not resolve `tmaproduction.blob.core.windows.net`. No alternate route or network-policy change was attempted. This release records successful CI provenance/signing and an independently matching registry artifact; it does not claim independent cryptographic verification.

The operations branch was fast-forwarded to main `7bf5752` before preparing the pin. The only deployment-source change is the playtest image/comment above. The normal HelmRelease, ingress, service, policies, secret references and dev-env files remain byte-identical. A checked PR and rendered-diff review still precede rollout; no activity or cluster change is active yet.
