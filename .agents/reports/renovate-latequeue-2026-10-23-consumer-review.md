# Story 183 — consumer source review

APPROVE SOURCE, no blocking findings, for [PR #4263](https://github.com/thaynes43/haynes-ops/pull/4263), exact head `8ee3ad21937559c5280285dc77edc710c38af148`, based on merged source `1057e9b858f3a14485ad7c9e9231b1889354d895`. Review completed 2026-10-23 21:18:50Z. This is a source and evidence review; driver retains execution/merge ownership and the final actual-work/custody gate.

## Exact change and coherence

Read the complete immutable commit diff and relevant controller/volume/template context in `/home/dev/work/renovate-latequeue-consumers-1023`. A byte comparison confirms each complete changed file equals its parent after only the two specified digest substitutions: 12 replacements across exactly five files (7 dev-env, 5 shepherd). `git diff --check` passes. Working tree is clean.

| Consumer | Updated image references | Effect |
|---|---:|---|
| dev/dev-env HelmRelease | 3 dev-env: app, gh-refresher, auth-watch | Current Deployment pod template changes; Recreate rollout requires driver runtime gate |
| upgrade-agent/dev-env-ops HelmRelease | 3 dev-env: deployment app/refresher, digest CronJob app | Two deployment containers roll together; future digest Jobs use new image |
| Vexa notes Job JSON | 1 dev-env | Future dispatcher-created notes Jobs use new image |
| shepherd HelmRelease | 4 shepherd: main mint-token/app, triage mint-token/app | Future main/triage Jobs keep init/app image pairs consistent |
| alert-responder HelmRelease | 1 shepherd | Future responder Jobs use new image |

No executable scripts, mounted config/requirements, secrets, command arguments, schedules, service accounts, security context, volumes/PVCs, or dev-env-system/frozen consumers change. Both existing home PVC names and Recreate strategies are preserved. This review did not inspect or operate on live private WIP/custody; that remains the driver’s fresh pre-merge responsibility.

## Vexa semantics

`scribe-notes-jobtemplate` has `disableNameSuffixHash: true` and is mounted as a directory at `/templates` in the two-minute scribe-dispatcher CronJob. `create_notes_job` opens/parses that JSON for each new notes Job. Updating the stable ConfigMap supplies the new template to future dispatches without a Vexa component rollout. Existing Jobs retain their own immutable pod templates/images; no new notes Job was created as a test. This is source verification, not a claim that a natural new Job has already exercised the image.

## Publication evidence reviewed

Independently read the public Actions run/job APIs for both automatic main push runs at exact merged source `1057e9b858f3a14485ad7c9e9231b1889354d895`:

- [Dev-env run 40024697901](https://github.com/thaynes43/haynes-ops/actions/runs/40024697901): completed success, including image build, own runtime-user smoke, publication, and signing steps.
- [Shepherd run 40024697879](https://github.com/thaynes43/haynes-ops/actions/runs/40024697879): completed success, including build/publication/signing. Its new PR-only validation job was skipped on main as designed; it has no separate own-image main smoke. This does not establish acceptance of Story 180’s remaining work.

Compared all consumer targets with the driver-provided [publication proof](renovate-latequeue-2026-10-23-image-publication.json), observed 2026-10-23T21:15:28.318611+00:00. It records anonymous manifest/config byte/header checks and GH_VERSION=2.103.2 in build history; both image manifests are Linux amd64. No independent cryptographic image signature or provenance verification is claimed by this reviewer; successful signing steps are workflow evidence.

## Immutable target and rollback pairs

- Dev-env target: `ghcr.io/thaynes43/dev-env:0.6.8@sha256:6d606d399c1cda4ca888aa92d17aea2d44bb44016c93aa8a2214d7aa51dfdd57`; rollback: `ghcr.io/thaynes43/dev-env:0.6.8@sha256:a2e5fe04e34adfa073abeb04554b9c706216d0a106d8e2625a8e9f4d0fdf3e5b`.
- Shepherd target: `ghcr.io/thaynes43/upgrade-shepherd:0.1.0@sha256:6e5ff891dbb83939fc7cb56d790a1e10b10fe53d154af3e851318b0c497d6513`; rollback: `ghcr.io/thaynes43/upgrade-shepherd:0.1.0@sha256:0c734d13fbd65808066fb4d08414b37e4fa0228e48933a05a70b584e9f56ca5b`.

Use each previous digest across the same owned consumer group for a coherent GitOps rollback. Fixed version tags remain republished; digest pins identify the reviewed content.

## CI and remaining gates

At review time, checks associated with exact consumer head `8ee3ad21937559c5280285dc77edc710c38af148` have Diff Scope - Success green; Flux Local test/diff and advisory review are still running. [Flux run 40025585844](https://github.com/thaynes43/haynes-ops/actions/runs/40025585844) and [Diff Scope run 40025585805](https://github.com/thaynes43/haynes-ops/actions/runs/40025585805) are the relevant consumer validation. Driver must wait for required aggregates, read any emitted advisory findings, preserve the final fresh actual-work/custody gate, and verify rollout/template activation within authorization. No consumer runtime acceptance, new natural Job evidence, Story 180 acceptance, crypto attestation proof, cluster mutation, tracker change, or repeated image build is claimed here.
