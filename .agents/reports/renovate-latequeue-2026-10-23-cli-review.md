# Story 183 / Epic 173 — GitHub CLI independent review

Review status: APPROVE SOURCE for replacement [PR #4261](https://github.com/thaynes43/haynes-ops/pull/4261) at exact head `2ec493aa4f4a48b83153afaf2fd27f60b504c8fd`; upstream/source review passes, no blocking findings. Image CI and main publication remain the driver’s acceptance gates. Reviewed base `4b3d9d010bb93648188fbad7bb707916a02f8321` in `/home/dev/work/renovate-latequeue-1023`. Driver remains sole runtime/merge/tracker owner. No repository edits, credentials, cluster actions, full image builds or extra agents used by this reviewer.

## Upstream assessment

- Official immutable [v2.103.2 release](https://github.com/cli/cli/releases/tag/v2.103.2) was published 2026-10-23 20:24:12Z. Source commit `4f2cfe92e3499f8581bf39db77e0a1431fa7b55c`; prior v2.103.1 source commit `64d175234b85aa7765c1f4cda058bd6353adb78f`.
- The [exact comparison](https://github.com/cli/cli/compare/64d175234b85aa7765c1f4cda058bd6353adb78f...4f2cfe92e3499f8581bf39db77e0a1431fa7b55c) is four commits / two fixes: [#14804](https://github.com/cli/cli/pull/14804) and [#14805](https://github.com/cli/cli/pull/14805). They remove client-side token-kind rejection for `--attach`, allow empty GraphQL viewerPermission for App installations, and defer final credential authorization to the upload endpoint. Existing host/repository and explicit insufficient-permission guards remain. No dependency manifest changes. Existing API/list/checks/merge/release operations and ordinary PR operations without attachments are unaffected by the source delta; this is a source-based compatibility inference.
- Upstream [checks on the immutable release commit](https://github.com/cli/cli/commit/4f2cfe92e3499f8581bf39db77e0a1431fa7b55c/checks) include successful Linux/macOS/Windows builds and integration tests, lint, govulncheck, CodeQL, and release jobs. Eleven changed test files cover the attachment change. The [published security advisory listing](https://github.com/cli/cli/security/advisories) shows latest advisories from September 30; this release is described as two attachment fixes, not a new security fix.

## Artifact and local checks

Downloaded public artifacts anonymously into `/tmp/renovate-183-cli-upstream` (no credential files or auth flows). Both match the immutable release API digests, and the archive also matches the published checksum file:

- `gh_2.103.2_linux_amd64.tar.gz`: `sha256:62ccf747115ddc61237efffdde66a3c8f70ba249433dc64f70801089b59c59c8`.
- `gh_2.103.2_checksums.txt`: `sha256:414d6009dc7f9f936d70831af818d1a32aa0ff07a77646861efd207356a36b2d`.

Ran extracted binary with isolated empty HOME/GH_CONFIG_DIR and a minimal credential-free environment. `--version` reported `gh version 2.103.2 (2026-10-23)`. Help invocations passed for `api`, `pr create`, `pr checks`, `pr merge`, `pr view`, `pr list`, and `release view`. Checked that existing shepherd JSON fields (`headRefOid`, `statusCheckRollup`, `autoMergeRequest`, `mergeStateStatus`, `isDraft`, `mergedAt`, `files`, `comments`) and merge flags (`--auto`, `--squash`, `--delete-branch`, `--match-head-commit`) remain supported. These are executable/flag checks, not authenticated integration or complete image tests.

## Integration and CI findings at base

- Both Dockerfiles install the Linux amd64 archive at `/usr/local/bin/gh`, unchanged path/packaging. Both use Debian/glibc amd64. Dev-env v1 generates its per-call token-refresh wrapper in `kubernetes/main/apps/dev/dev-env/app/resources/dev-init.sh:249`; it exports GH_TOKEN and forwards every argument to the real executable. Shepherd exports its initContainer token before using `gh pr list/view/merge`, `gh release view/list`, `gh api`, and PR creation/commenting. No repo call site uses `--attach`. No wrapper/auth changes are needed for this version.
- Dev-env workflow at `.github/workflows/dev-env-build.yml` already builds PRs and smoke-tests UID 1000 in a read-only, network-none container with 2 CPUs/2GiB. It runs `gh --version` alongside the toolchain. Main builds that same source, repeats smoke, publishes `0.6.8` and `latest`, records the digest and signs by digest. It creates no unique SHA tag, and the fixed tag is intentionally republished. A digest identifies immutable content, but neither tag alone uniquely proves the source revision. Driver must capture exact main workflow SHA/run, success, registry digest, and successful signing before calling publication complete. Frozen runtime digest consumers must remain unchanged.
- Material existing gap: `.github/workflows/upgrade-shepherd-build.yml` is main-only and has no PR build or smoke. It directly builds/publishes `0.1.0` + `latest`, then signs by digest. Required aggregate PR checks or a dev-env image build do not prove that the distinct complete shepherd image builds. Driver proposes a narrow PR-only validation job to close this gap in the combined source PR; reviewer endorses the technical approach.
- Minimal proposed shepherd validation: exact PR head, job-level `contents: read`, checkout without persisted credentials, build/load its own Dockerfile without publishing, bounded UID-1000/read-only/network-none smoke with writable UID-1000 `/tmp` (HOME=/tmp), testing `gh --version`, `kubectl version --client=true`, `flux --version`, `git --version`, `claude --version`. Preserve existing main publisher/signing guards; no credentials or live GitHub writes in smoke.

## Original proposal exact-head review

Also reviewed both created Renovate PR diffs through the public PR API. [#4259](https://github.com/thaynes43/haynes-ops/pull/4259), head `9f43186608ec1ad40c255efaf5585bd2f182277c`, changes only the dev-env GH_VERSION line. [#4260](https://github.com/thaynes43/haynes-ops/pull/4260), head `bdeb3c3b77b33d9b57afac950e9888726da581d4`, changes only the upgrade-shepherd GH_VERSION line. Both target base `4b3d9d010bb93648188fbad7bb707916a02f8321`. Both pure version diffs are compatible by the upstream review, while #4260 retains the image-validation gap. Their changes are contained by replacement #4261; do not describe the original shepherd proposal’s aggregate PR checks as its own image test.

## Replacement exact-head review

Reviewed PR #4261 at `2ec493aa4f4a48b83153afaf2fd27f60b504c8fd`, based on `4b3d9d010bb93648188fbad7bb707916a02f8321`, using the public PR files API and immutable local git object. Exact diff: two ARG version lines and 51 lines in the shepherd workflow. No unrelated source, runtime consumer, policy, credential, or owner changes.

The new `validate` job triggers only on pull_request; its job-level permission map specifies contents:read, making other token permissions none. Checkout persists no credentials. The build uses the real shepherd Dockerfile with push:false/load:true. The smoke has a three-minute timeout, CPU/memory limits, UID 1000, read-only root, network none, and UID-1000 `/tmp` tmpfs; it checks gh/kubectl/flux/git/claude versions. The publisher job is skipped for PRs. No repository secret, GHCR login, signing or push step exists in the PR validation job.

Preserved behavior nuance: the publisher condition is non-PR, rather than a main-ref comparison, so workflow_dispatch can still publish from another selected ref as it could before. This is not introduced by this change. Publication acceptance for this update must use the automatic main merge run. Default checkout on pull_request validates the GitHub merge candidate associated with the reviewed head; report its CI association accurately rather than claiming a literal head-ref checkout. Main publication is a separate build.

No source-review blocker. Existing missing full shepherd PR image validation is closed in source by this workflow change; it becomes tested only when the new validation actually passes.

## Remaining acceptance evidence

Driver must verify exact-head-associated dev-env + shepherd PR build/smoke checks and required aggregate checks. Then require successful main publication/signing evidence independently of PR checks. Runtime activation remains the driver’s responsibility and outside this reviewer’s actions. Existing tag conventions and unrelated automation remediation remain outside this source update.

Reviewer completed source review 2026-10-23 21:02:21Z. This artifact intentionally records image CI and publication as pending, not failed or completed. Driver owns their final evidence and the runtime-consumer decision.
