# Story181: eligible templates-only dev-env2.43 source review

> Historical2.43 source review only. Current2.44 acceptance is in the main report and distinct v2-244 registry/refresh/runtime receipts; this appendix does not establish2.44.

2026-10-23. Independent bounded read-only helper. No repo edits/fetch, runtime probe, auth/inference/credential operation, push, or tests. Existing `/home/dev/repos/dev-env` stays clean at2.42.0 source7cb1a19575a4a6ae86f56248c84cf86994c0a2d1; exact2.43 source was read through public GitHub endpoints into `/tmp/dev-env-243-review/`.

**Assessment:** no source incompatibility or required local template adaptation identified for the narrowly scoped2.42→2.43 template image line. The entire3710 branch is not the eligible change: it also modifies frozen H7 audit, shelf and keeper helper pins, which must remain excluded. Driver owns the one-file PR, required CI, controlled activation/observation and exact final diff review.

## Exact published identity

- Candidate `ghcr.io/thaynes43/dev-env:2.43.0@sha256:1e29f6ae6dbf15e906805a92c96040aa237a04e0c3e5b1d2a144f2d8d748654c` matches current [PR3710](https://github.com/thaynes43/haynes-ops/pull/3710) head1c5d071135d03acfdbbf9b664c577893d002cccd.
- [Release2.43.0](https://github.com/thaynes43/dev-env/releases/tag/v2.43.0), published2026-10-23T09:46:03Z, points to source `3dc1ad50c3b84f007ae4aef3150452daff2a751b`.
- [Publish-agent run39934249741](https://github.com/thaynes43/dev-env/actions/runs/39934249741), [job120349765631](https://github.com/thaynes43/dev-env/actions/runs/39934249741/job/120349765631), triggered by push of refs/tags/v2.43.0, succeeded. Build, exact-image smoke, push, keyless sign and signature verification all succeeded. Log prints exact candidate digest at09:51:27Z and verified signature at09:51:33Z with identity `https://github.com/thaynes43/dev-env/.github/workflows/publish-agent.yml@refs/tags/v2.43.0` and GitHub Actions OIDC issuer. Agentd/agent-run print v2.43.0 with the full release source commit; compiledGo1.27.2/linuxamd64. Safe filtered evidence: `/tmp/dev-env-243-public-proof.txt`.
- [Pinned publishing workflow](https://github.com/thaynes43/dev-env/blob/3dc1ad50c3b84f007ae4aef3150452daff2a751b/.github/workflows/publish-agent.yml) checks release tag/main ancestry, avoids overwriting existing release tags, builds and loads once, smoke-tests before pushing, then signs/verifies the pushed digest against that exact tag identity. Smoke uses uid1000, read-only root, network-none and2CPU/2GiB. No independent signature verification was run by this helper; the public publisher's verification is the evidence.

## Actual2.42→2.43 changes and compatibility

[Exact compare](https://github.com/thaynes43/dev-env/compare/v2.42.0...v2.43.0) contains two commits: featureb972bccfb48fa1c10a30ee6785a3f5ef161a56bc ([PR520](https://github.com/thaynes43/dev-env/pull/520)) and release3dc1ad5. Twenty-six changed paths are offline maintenancecontract package/CLI, tests, documentation and release record. No image Dockerfile/tool pins, Go dependency files, template parser/schema, controller, keeper or shelf production files change. Production agentd code is unchanged; its only changed path is a role test.

New `agent-run maintenance check --input <file|-> [--now RFC3339]` is explicit opt-in. Its implementation imports standard-library I/O/parsing/clock plus the pure maintenancecontract package; reads at most64KiB of a supplied file/stdin; never calls the operator API, reads provider tokens or starts a responder. Existing dispatch adds one distinct maintenance verb. Results retain unverified evidence and false authorization/operational-deferral flags. No boot path invokes it. Documentation/examples do not install or activate the role. No wider role migration or acceptance is asserted.

Required [feature CI39931040654](https://github.com/thaynes43/dev-env/actions/runs/39931040654) passed including lint/test/build/generated/image jobs and aggregate. Release-source [CI39934227838](https://github.com/thaynes43/dev-env/actions/runs/39934227838) also has successful aggregate. These are existing public CI results, not tests run here. Source contracts/tests do not establish runtime maintenance parity or authorize alert suppression.

Local template changes needed: image tag/digest and accurate release provenance comments only. Current2.42 comments should name2.43/source3dc1ad5/run39934249741 after the image line changes. Keep template schema/profiles/env/mounts/lifecycle values unchanged. This publishes no operator-image deployment and changes no keeper auth helper.

## Scope boundary and frozen consumers

The existing eligible file is `kubernetes/main/apps/dev-env-system/templates/app/templates.yaml`; its parent generator emits stable ConfigMap `dev-env-templates` without a hash suffix. This image is for managed session pods. Existing controller behavior marks old revisions Outdated and refreshes eligible idle/unattached sessions through its preserving lifecycle; source preparation is not a command to restart an active session.

PR3710 also changes these three consumers, all excluded from the narrow PR:

- `kubernetes/main/apps/dev-env-system/h7-metadata-audit/app/job.yaml`: historical2.15.1 finite audit image; do not change the Job or frozen acceptance evidence.
- `kubernetes/main/apps/dev-env-system/shelf/app/deployment.yaml`: historical2.9.1 shelf/history path; do not broaden this cohort or restart it.
- `kubernetes/main/apps/dev-env-system/keeper/app/helmrelease.yaml`:2.39.1 Claude-login-idle helper image inside keeper; do not alter its auth window/pod/template/image.

The H7 workflow filters are limited to metadata-reader/audit/tests/workflow paths; templates.yaml is absent. Its previously documented frozen Kyverno hash failure is independent; narrowing the PR does not repair, waive or refresh that evidence. No H7 expected hash/assertion changed. A template-only diff does not edit any of the three held manifests or credential inputs, but live object/pod identity and eligible-session behavior remain the driver's verification responsibility.

Installation status: source/release/publish verified only. Driver reports Flux source fetch is currently blocked by60sGitHub clone timeouts. This helper queried no cluster state, and no installed2.43 claim follows from an eventual merge or public image publication. Preserve that distinction in the handoff.
