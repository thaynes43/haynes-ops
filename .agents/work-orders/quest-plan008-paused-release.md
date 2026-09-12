# PLAN008 paused-status follow-up private release

Date: 2026-09-12 UTC
Status: Read-only baseline and release path prepared; application PR38 is not yet published, and no image pin, operations PR, cluster mutation, reconciliation or rollout has occurred.

## Scope

This follow-up will deploy the reviewed paused-status clock repair only to the isolated `frontend/haynes-quest-playtest` workload. The future source change is limited to the publication comment and immutable image tag in `kubernetes/main/apps/frontend/haynes-quest/app/playtest-helmrelease.yaml`, plus this record. Normal Quest, Services, IngressRoutes, Cilium policies, Kustomizations, Secrets, database resources and every dev-env path must remain unchanged.

The task worktree is `/home/dev/work/quest-plan008-paused-release` on `agent/quest-plan008-paused-release`. It was created at the initial PLAN008 operations merge `882ef5a847f0b7461234a2642a446480de248273`, then fast-forwarded before editing to current `origin/main` `024066546951c4cbd165b10db33a82798fac22ae`. The intervening Ceph-remediation and external-Traefik logging changes are unrelated and remain intact.

Do not edit the image pin or open the operations PR until all of these exact inputs exist:

- application PR38 is squash-merged and its 40-hex main commit is recorded;
- main-branch `Application` and `Documentation` push runs both complete successfully at that exact commit;
- the Application run's tests, image build/push, provenance/SBOM, GitHub attestation and signing steps pass;
- an anonymous GHCR read returns HTTP 200 for `sha-<merge>` and its header digest matches an independent hash of the exact manifest bytes.

## Current read-only baseline

The sanitized baseline at `.private/test-results/plan008-paused-release/baseline.json` was captured at `2026-09-12T17:07:14Z`. The copied verifier is `.private/test-results/plan008-paused-release/tools/plan008-release-verify.mjs`, SHA-256 `3056b4f4fb67af9526454741ec2e465a4141fb154b3735d173f66e41ecd36a0d`. A current-state run at `.private/test-results/plan008-paused-release/baseline-verify.json` passes all 24 assertions.

- Flux source `flux-system/haynes-ops` and Kustomization `frontend/haynes-quest` are Ready at `main@sha1:024066546951c4cbd165b10db33a82798fac22ae`.
- Private HelmRelease and Deployment retain UID `117b9801-8cf3-49b2-8f9b-7799fd4c770c` and `d069cbd2-3d92-45f5-8ba6-b93f6cd9c12b`; both are observed at generation 5. Pod `haynes-quest-playtest-549c4d7b76-nn4tf`, UID `7ae5b272-2a6d-4784-9422-4c5fc21d9545`, is Ready with zero restarts on `sha-c5696e3b884059040bcf67d225697139979a3551@sha256:b5e6aee84795299699bc8f02304a5ddd1cb644f81bf458718bdaa9540e988fd5`.
- Normal Quest remains at Deployment generation 3, pod UID `d3e0117d-9dc5-4716-9343-2a740147171b`, zero restarts and `sha-3502ac7120f6d7741a1a209415f4c9eeb33f826b@sha256:743bca475c2e19d2534ecd3be7f952e52d0d5adb5724599f1285218f91e87e37`.
- Dev-env remains at HelmRelease generation 30 and Deployment generation 100. Pod UID `c3a94756-af35-405e-93bc-eb05c2979d3a`; `app`, `auth-watch` and `gh-refresher` are Ready with zero restarts on digest `80a2c0b8acc53e63a4174ca0f4b81fd1c40aaabff9eed2bffa587883237b9af1`.
- Normal/private Service UIDs remain `822590b9-e17a-4666-8780-f975553d27ce` / `f1c93ed4-75ae-45fa-9e1d-efe270e07c37`; IngressRoute UIDs remain `89efd0c9-ba58-4f31-b306-5b6fbc9d23a0` / `6bc371bf-4425-4d3b-a818-5a071625be97`; policy UIDs remain `5499b732-b729-4266-993d-f22611dc4904` / `d8c0e74a-f505-4908-931d-15edf8fbd333`. Their canonical live-spec hashes exactly match the prior release baseline. All four Quest health/readiness probes return HTTP 200 with the expected bodies.

## Future validation and handoff

After the exact application publication is available, fetch current `origin/main` again and confirm the branch has no unrelated diff. Require the current source tag to equal the live private Deployment image and `QUEST_EPHEMERAL_PLAYTEST` to remain `"true"` before patching. Use `apply_patch` to replace only the PR/publication comment and image tag; do not add, remove or reorder environment entries.

Repeat the prior release gate against cached app-template 5.1.0: Helm lint; exact `ServiceAccount`, `Service` and `Deployment` identities; byte-identical non-Deployment renders; byte-identical Deployment renders after normalizing only the app image; exact candidate image; retained ephemeral flag; and a successful complete app Kustomize build. Source scope must contain only this work order and the private playtest HelmRelease. Compare the normal manifest, both routes, both policies, app Kustomization and Flux Kustomization byte-for-byte to the current base.

Open a standard operations PR only after those gates pass. Inspect the established nine checks: `Diff Scope - Success`, `Flux Local - Filter`, both main/edge tests, all four main/edge HelmRelease/Kustomization diffs, and `Flux Local - Success`. The main rendered comments may show only the private image change; edge must be empty, and Service/ServiceAccount must be unchanged.

The PLAN008 lead owns the immediate pre-merge baseline, activity lifecycle, merge, scoped Flux reconciliation, rollout and hosted proof. Post-release, run the copied verifier with the new exact image and operations merge SHA. It must preserve normal Quest and dev-env identities, images and restart counts; stable routing/policy UIDs and specs; and require the new private controller generations, exact image/digest, Ready pod and zero restarts. Private pod, EndpointSlice and CiliumEndpoint identities may rotate.

Activity `act-164947-373500` belongs to the lead's preceding rollout/test and was still active when this preparation began; this lane neither changes nor relies on it.
