# PLAN009 private playground release

Status: Candidate image staged and verified, September 12, 2026. Application PR41 is merged and published. No operations merge, activity declaration, reconciliation or rollout has occurred.

## Scope and ownership

Root Astra owns `/home/dev/work/quest-playgrounds-release`, branch `agent/quest-playgrounds-release`, base `98ef332`. Deploy only the checked Haynes Quest image to `frontend/haynes-quest-playtest`. Normal Quest, dev-env, both Services/routes/policies, database resources and credentials remain protected. The private fixture remains ephemeral and synthetic. Identity and curated family photos are required next-stage application work, outside this image update.

The application adds two authored playgrounds, forgiving reusable geometry validation, four ordinary fights plus a boss per chapter, ferries/branches/checkpoints and full-course scenery. Existing models, sounds and fictional pictures are reused. No new authoring workload or expense is needed.

## Prepared verification

- `.private/test-results/plan009-release/tools/release-verify.mjs` is the unchanged prior 24-assertion live isolation verifier. It checks exact private image/generations/readiness and protected normal Quest/dev-env identities, images and restart counts, routing/policy identity and specs, and health/readiness. The initial read-only capture is `initial-baseline.json`; recapture immediately before merge.
- `.private/test-results/plan009-release/tools/verify-image-only.mjs` is the native verifier from `db5facb` in `/home/dev/work/quest-image-render-verify`. It uses cached app-template 5.1.0, no cluster operations. It checks exact rendered identities, byte equality after normalizing only the app image, unchanged source values after normalizing only the tag, retained ephemeral mode, seven protected source files, and the complete app Kustomize build. `render-baseline.json` passes against the unchanged PR38 image.
- Native inventory parity confirms the runtime 23 GLBs, four WAVs, six fictional pictures, 37 catalog entries, 54 thumbnails and 26 review viewers remain consistent; the application guide will include new actual gameplay captures.

## Publication and rollout gate

Wait for exact application main-commit verification, dedicated Postgres tests, image build/push, provenance/SBOM, attestation and signing. Independently retrieve the exact GHCR tag index and compare its hash with its digest header before editing the private immutable tag. Record the application PR, commit, successful main run and digest here.

The operations PR may change only the image/publication comment in the private HelmRelease and this record. Run the render verifier against the current base and expected full image. Inspect all nine existing Diff Scope/Flux Local checks and rendered comments: main must show only the private image, and edge must be empty.

Immediately before squash merge, capture the live protected baseline and declare activity scoped to `frontend,haynes-quest-playtest` for 45 minutes. Reconcile only the Quest Kustomization with source if needed. Verify the rollout against the exact merged image/revision, then run hosted API, real-control route, mobile/audio and catalog checks against the exact browser bundle. End the activity after health and hosted verification. Never restart dev-env.

## Exact application publication

Application PR41 merged as `ee468101700ed3d0a5f94ffde4226a740a3d3ac4`. Exact main Application run `34715695671` and Documentation run `34715695670` pass, including dedicated Postgres verification, Buildx publication/provenance/SBOM, attestation and cosign signing. Anonymous GHCR GET returned 200; the 857-byte OCI index hash independently matches its digest header. `.private/test-results/plan009-release/publication.json` records the checked image:

`ghcr.io/thaynes43/haynes-quest:sha-ee468101700ed3d0a5f94ffde4226a740a3d3ac4@sha256:9862c6c8f95c74b6c0c5f944125ae7aefe4729f211340a3f43d417e1f663459d`

Expected hosted client: `index-DOmWuEtB.js`, 1,171,500 bytes, SHA256 `40c5715544d0993a2e782065f06be82c29c1b0124dacf6a0ab49136b0f1f29ea`. Local application acceptance passed the full two-chapter route, mobile/audio/retry controls, authored landscape, API 59/59 and catalog. The initial artwork fault and obsolete flat-route landscape harness failures are preserved and explained in application WO078/WO081.

The exact candidate passes cached app-template5.1.0 lint, rendering and source comparison. Only the private app image differs; ServiceAccount/Service renders and the normalized Deployment are unchanged. All seven protected source files match base `98ef332`, ephemeral mode remains enabled, and the complete app Kustomize build succeeds. Machine evidence: `render-candidate.json`. Source scope is exactly this record plus the private HelmRelease's publication comment/image tag.
