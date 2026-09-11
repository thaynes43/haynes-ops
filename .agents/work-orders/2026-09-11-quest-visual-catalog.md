# Haynes Quest visual catalog publication

Status: Application catalog merged, published and verified. Review-only image pin prepared; checked operations PR and rollout next.

Tom requested a subagent to organize all existing assets in the MkDocs catalog with inspiration images, 3D models and thumbnail navigation. Application WO047 records inventory, layout and verification. This is a static catalog update served by the existing isolated private playtest, within the authorized end-to-end release. No new artwork generation or exact-version gameplay approval is inferred.

Worktree `/home/dev/work/quest-catalog-promotion`, branch `agent/quest-catalog-promotion`, base `4d06789bb1b3ec854ff67d67cb4450e5098768b6`. Update only `kubernetes/main/apps/frontend/haynes-quest/app/playtest-helmrelease.yaml` after the application PR passes all checks, squash-merges, publishes and signs. Retrieve the exact GHCR tag anonymously and match its immutable digest to the successful build evidence. Keep normal demo3502 and dev-env unchanged. No new DNS, ingress, Secret, OAuth, real photo access or public hosting.

Current review image is app main `9ccc7a8d89210f6da9ba12e031e124eade52ba6b`, digest `sha256:787262684dc67dbbea0f1cb3af36765f04691f24f65bd164ca345244d7216f8d`, deployed through opsPR2857. Its actual game bundle is `index-BPeATrrp.js`, 1,001,106 bytes, SHA256 `15487f825f0f7d8125144d3a1eb49c37e5212bf6c7d0bb8de8f368f141bc6051`. Catalog changes must preserve that gameplay bundle and exact source models.

Require all operations PR checks and inspect rendered diff: only review image and consequent pod template. Declare scoped activity for `frontend,haynes-quest,haynes-quest-playtest` before checked merge/reconcile. Reconcile `frontend/haynes-quest` with source, verify exact running image, DNS/TLS, health and catalog thumbnail/model delivery, and compare normal app/dev-env UIDs/restarts. End the declaration promptly. The prior first-review rollout declaration is managed separately by the root coordinator; do not leave either active.

Live endpoints after verification: `https://haynes-quest-playtest.haynesops.com/`, `/studio/assets/catalog.html`, `/studio/assets/playtest.html`. Physical Safari, children, family setup/photos, Besties and lifetime journeys remain open.

## Initial private playtest verified

The preceding rollout is complete. At21:29UTC, September11, the new host resolved through all three CoreDNS replicas; the earlier pre-record NXDOMAIN cache expired without configuration changes or restarts. TLS was valid. On app9ccc@787262, live health/readiness and exact BPe bundle passed; all15 gameplay GLBs matched hashes, and four review pages plus the guide were delivered.

The live held-joystick menu regression passed with zero page errors and exactly one retained save. Two targeted browser probes passed: home and actual-controls boss victory, visible fictional memory images, reload/resume, absorption to age4 with jump unlocked, entry into the2024 chapter, and saved-resume of that chapter. Full local two-chapter keyboard and saved/resumed touch evidence remains application WO046; this live check is deliberately narrower. No game-state API mutation or real photos were used.

At the final check, playtest UID`8f40b5f5-f7a4-4cd1-af83-b37004ee42b8`, normal UID`d3e0117d-9dc5-4716-9343-2a740147171b`, and dev-env UID`c3a94756-af35-405e-93bc-eb05c2979d3a` retained Ready/zero-restart state. Root ended initial activity`act-205400-187494` at21:29:39UTC. Full live evidence remains `/home/dev/work/quest-parody-obby/test-results/live-review/live/`.

Catalog application PR30 merged as `b15bc98f1a6bec1108815bdea75794f86e1323db` after all final e8277bc checks passed. Main image publication/digest verification is in progress; do not change the deployment pin until it completes.

## Verified catalog image

PR30 exact head `e8277bc70b6503fe1fa9ce733aa0525077a7a9b2` merged at21:29:59UTC. Main Application34649668374 and Documentation34649668390 passed; all236 tests included nine real PostgreSQL tests. Image publication, provenance attestation and cosign signing succeeded. Anonymous exact-tag GHCR lookup returned HTTP200 OCI index, matching the CI and attested digest.

ghcr.io/thaynes43/haynes-quest:sha-b15bc98f1a6bec1108815bdea75794f86e1323db@sha256:d7eddba8db5ccf60ea03bdcb7ba921e7486106b1ab34e7380c53929a355d4599

Publication evidence: `/home/dev/work/quest-visual-catalog/test-results/catalog-publication.json`. No independent attestation-verification claim is made; successful CI signing/attestation plus the exact registry digest are the verified evidence. The original GLBs and gameplay code remain unchanged. Root has reviewed the complete catalog and its browser evidence; only the existing review workload's image pin and comment change.
