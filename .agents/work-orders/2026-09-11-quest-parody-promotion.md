# Haynes Quest obby and parody promotion preparation

- Status: preparation only; no cluster changes or rollout authorization claimed.
- Operations worktree: `/home/dev/work/quest-parody-promotion`, branch `agent/quest-parody-promotion`, based on `d2acee8`.
- Application candidate: `thaynes43/haynes-quest#28`, still draft while actual full keyboard/touch verification finishes. The bounded cast is four completed v001 characters with returning Peel Patrol and Drama Dragon in chapter two; no new model production or expanded setup blocks this playtest.
- Scope: Haynes Quest image pin and this evidence record. No dev-env, OAuth, ingress, network, secret or database configuration changes are part of this promotion.

## Release boundary

The user authorized end-to-end MVP delivery and checked PR merges. Haynes Quest AGENTS/TEAM additionally require Tom's review of exact final visual/audio versions before gameplay promotion. The earlier visual question predates rejection of the generic enemies and cannot approve that cast. Prepare the completed candidate, game/catalog evidence, checked application merge, published immutable image and concrete operations PR before requesting final exact-version approval. Ordinary application code/docs PRs do not wait on asset approval.

No image pin is changed yet: the final application commit and digest do not exist. Never substitute a guessed digest, mutable tag or the older published foundation image merely to advance this work order. Do not merge a candidate-game image before the exact asset decision is recorded.

## Verified current baseline

Read-only checks on September 11, 2026 at approximately 17:46 UTC:

- App Kustomization: `frontend/haynes-quest`, source `flux-system/haynes-ops`, path `./kubernetes/main/apps/frontend/haynes-quest/app`.
- Running app pod: `haynes-quest-7d9b698574-nds6g`.
- Current tag: `sha-3502ac7120f6d7741a1a209415f4c9eeb33f826b`.
- Current image digest: `sha256:743bca475c2e19d2534ecd3be7f952e52d0d5adb5724599f1285218f91e87e37`.
- Private fixture URL: `https://haynes-quest.haynesops.com`.
- Runtime receives only its session/app database Secret; no Immich or database-init Secret mount. Existing `QUEST_FIXTURE_MODE=true` continues to select synthetic material.
- dev-env pod: `dev-env-dfdd8c894-l724p`, UID `c3a94756-af35-405e-93bc-eb05c2979d3a`; all three reported container restart counts zero. This promotion must not restart it.

## Concrete next steps

1. Finish the application's two-chapter integration with four completed candidate characters, actual keyboard/touch course journeys, image decoding and exact catalog audit. Merge the checked application PR and verify publication/signing plus anonymous retrieval of its exact immutable manifest.
2. Replace only the Haynes Quest image tag/digest and explanatory commit/run comments. Record exact candidate visual versions and their pending/approved decision. Validate the rendered app and required Flux Local checks in an operations PR.
3. Once exact asset approval is present, declare scoped activity for `frontend,haynes-quest`, squash-merge the checked operations PR and reconcile only the app's GitOps target. Do not manually delete/restart the app pod.
4. Verify source revision, Kustomization/Helm readiness, running image digest, private health/readiness, complete catalog media, actual new-game loop, decoded synthetic pictures and saved resume. Do not mistake stale existing journeys for newly created parody plans.
5. End scoped activity promptly. Record measured evidence and unperformed physical Safari/child playtests honestly. Preserve rollback image above.

## Candidate review package

Application runtime head `236d221` includes the exact 15-GLB candidate list at `docs/assets/media/playtest/v001/artwork.json` and a short owner guide at `docs/assets/playtest.md`. Those 15 files comprise two traveler stages, five environment/keepsake pieces, four equipment props and four completed enemy/boss models. No audio is mapped. The current catalog v2 selects six encounters across two chapters while preserving original catalog v1 save data. The unshipped v1 candidate cast still contains incomplete Nap and missing Diva artwork; preserved data does not establish full graphical playability of that archived candidate. New playtests must use v2.

Local typecheck/lint/build and 218 tests pass; nine additional PostgreSQL tests run in CI. All four character pages/models, 20 clips, 28 MP4s, 20 stills, four touch orbits and four editable masters passed a combined browser audit without failure counters. The complete keyboard route passed on C6hLU3Dp; current DcnSUtWQ differs only by the mobile Save & leave accessible label. Full touch remains incomplete. Tom requested Fable5.1 agentic testing and polish under application WO042; exact task `haynes-quest-0911-152226` is running from application `f9e2b49`. Wait for its findings and root integration before choosing a release image. These are preparation facts, not asset approval, physical-device acceptance or a live release.

## Read-only release audit, 19:28 UTC

Application PR checks are Application `verify` (including PostgreSQL16), `container-check`, and independent Documentation `build`. The process requires all green even though Quest currently declares no GitHub-required contexts. After squash merge, use the actual main SHA: its Application workflow runs `verify` then `image`, publishes `sha-<main SHA>` with Buildx provenance/SBOM, GitHub provenance attestation and keyless cosign signing. Require those jobs and Documentation to succeed.

Read the exact tag anonymously from GHCR with a pull token kept out of output and an Accept header supporting OCI index/manifest plus Docker list/manifest. Require200, record `Docker-Content-Digest`, and pin `sha-<main SHA>@sha256:<digest>`. This retrieval procedure was independently re-proved against the older PR27 image; it does not prove the still-unpublished candidate.

For the opsPR, check diff scope and whitespace, render the app, and run Flux Local tests for both main and edge with Helm enabled. Inspect all nine jobs: Diff Scope; Flux Local Filter; main/edge Tests; four resource Diffs; Success. Required contexts currently are `Diff Scope - Success` and `Flux Local - Success`. The rendered diff should contain the Haynes Quest image change only.

Independent `gh attestation verify` from this pod was attempted once against the older published image and failed DNS for `tmaproduction.blob.core.windows.net`, outside the egress allowlist. Do not retry through a workaround or claim independent cryptographic verification. Successful publish/attest/sign CI plus exact anonymous manifest retrieval are the established release evidence. Haynes Quest is not covered by the existing Kyverno image-verification rule; no admission-time signature enforcement is claimed.

Fixture access remains bounded to synthetic identity/media and a seven-day fixture cookie. Real private setup/admission are not delivered by this image pin. Read-only checks again confirmed the old live digest above and the same dev-env UID with zero restarts.
