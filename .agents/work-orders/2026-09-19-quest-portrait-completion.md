# Private Quest portrait and completion repair

Release contract and evidence, September 19, 2026. The final hosted outcome is recorded in the application's [WO085](https://github.com/thaynes43/haynes-quest/blob/main/.agents/work-orders/085-portrait-and-completion-repair.md) and [release evidence index](https://github.com/thaynes43/haynes-quest/blob/main/.agents/evidence/portrait-completion-release.json).

Root Astra owns this release from `/home/dev/work/quest-blockers-release-20260919`, branch `agent/quest-blockers-release-20260919`, base `ebf1898`. Application work is in `/home/dev/work/quest-playtest-blockers-20260919`, WO085. The user requested repair and renewed playtesting after phone portrait input latched upper-left and the major first-chapter memory appeared uncollectable after dragon victory.

Change only the immutable image and its publication comment in `kubernetes/main/apps/frontend/haynes-quest/app/playtest-helmrelease.yaml`. Keep normal Quest, dev-env, routes, Services, policies, database and secret references unchanged. The fixture remains private, ephemeral and synthetic. There is no new Blender/audio/model delivery or personal-media access in this release.

The verified baseline is application PR44 `191f8c2420aa00e98caf026af334987aee98e593`, deployed by operations PR2898 `96c09bc0ba6c800858025593c606ae19f1edac05`, with digest `sha256:21d4e4052609299734c10aa984feb2d1d8ed650e007b9479a768fbbdf4e059c5`. The old publication comment still named PR41; replace it with the actual new publication evidence.

Application [PR46](https://github.com/thaynes43/haynes-quest/pull/46) merged as `032b996e2e9c99271bbf47fb49bee4ee947a7473` after the exact head `6576a53` passed all 562 tests, including 12 dedicated PostgreSQL cases, typecheck, lint, documentation and container checks. Local Chromium 153 completed the full missed-minor recovery and both chapters, an independent Besties shortcut, and the strengthened portrait input regression. The exact tested client is `/assets/index-B6ZOcLIe.js`, 1,193,022 bytes, SHA256 `c1ebea0d164c81561b58495dc2a992149bcb9ff1ccc34d651408b3cf319de81c`. Physical Safari acceptance remains distinct from browser emulation.

An unrelated dev-env roll interrupted the browser work before this rollout. The original audit baseline is retained; the protected-state baseline was recaptured September 19 at 19:54 UTC after recovery. Dev-env generation changed from 107 to 108 with the same image, all containers ready and no restarts. Compare the release against that post-interruption identity; no dev-env resource is changed here.

## Gates

1. Review exact application source/tests, successful local browser diagnostics and both-chapter progression. Require PR verification, dedicated disposable PostgreSQL checks, documentation and container checks; squash merge the exact checked head.
2. Wait for the exact main commit's published image, provenance/attestation/signing workflow and documentation success. Independently hash the anonymous GHCR manifest and match its digest header before pinning it.
3. Check the image-only manifest diff and all required operations checks. Capture normal Quest/dev-env baseline identities and readiness. Declare activity scoped to `frontend,haynes-quest-playtest` before merging the operations PR.
4. Reconcile only `frontend/haynes-quest` if needed. Verify exact deployed image, health/readiness, hosted JS/CSS bytes and actual touch/progression behavior. Check protected normal Quest/dev-env and end the activity early after verification.
5. Publish concise final evidence in the app handoff and this record. Browser emulation is not physical-device acceptance.

## Published artifact

The exact main [Application run 35467300966](https://github.com/thaynes43/haynes-quest/actions/runs/35467300966) and [Documentation run 35467300988](https://github.com/thaynes43/haynes-quest/actions/runs/35467300988) passed. Image build/push, provenance attestation and cosign signing each succeeded. Anonymous GHCR retrieval independently matched the raw OCI index body hash to `Docker-Content-Digest`: `sha256:a87013f9b806008dcd95caa2a487acc61c919374630bd49ccd4a62b8206f2dcc`. The GitHub attestations API records SLSA v1 provenance for that exact subject/digest. This verifies publication and provenance presence; no independent cryptographic attestation verification is claimed.

Target image: `ghcr.io/thaynes43/haynes-quest:sha-032b996e2e9c99271bbf47fb49bee4ee947a7473@sha256:a87013f9b806008dcd95caa2a487acc61c919374630bd49ccd4a62b8206f2dcc`. The manifest changes only this private playtest image and its publication comment. Root performs the scoped rollout and records live results in the application release index.

## Validation diagnostic

The first PR validation run `35467621388` passed main/edge configuration tests, scope validation and the other three rendered comparisons; main HelmRelease comparison failed before producing a diff comment. Its detailed runner log redirects to a blob domain not reachable through this pod's egress policy. Check annotations identify no manifest error. Local reproduction is inconclusive because the installed CLI is older than CI's pinned version. The app token also lacks Actions rerun permission. This record update starts fresh required PR checks through the normal branch workflow; preserve the original failure as unexplained and require all new-head checks plus the rendered private-image diff before merge. No validation requirement or network policy is changed.
