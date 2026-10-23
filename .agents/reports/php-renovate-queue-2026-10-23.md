# Renovate queue snapshot — 2026-10-23 UTC

Canonical [Story #172](https://work.haynesnetwork.com/work_packages/172). Read-only GitHub metadata collected approximately 03:17–03:29Z; inspect current PR head/checks again before any merge. No conflicts across these 45 PRs. All lack a recorded formal review and no Claude issue-comment review was found. Independent review is mandatory for this work order. No direct PHP runtime update identified. No original PR changed or closed.

## haynes-ops — 28 Renovate PRs

`F/D` = live branch-required `Flux Local - Success` and `Diff Scope - Success`; all pass in this snapshot. Other relevant failed jobs below remain blockers for this work order despite green aggregates. All merges require scoped authorization. V1 mounted-resource PRs must remain held and be handled as held drafts by their authorized owner.

| PR / update | Required | Other failing validation | Remaining disposition |
|---|---|---|---|
| [#3561](https://github.com/thaynes43/haynes-ops/pull/3561) feat(container): update image ghcr.io/linuxserver/calibre to v9.16.0-ls428 | F/D pass | LazyLibrarian EPUB Convert - Success | Keep manual: EPUB converter validation must pass; media repair excluded. |
| [#3626](https://github.com/thaynes43/haynes-ops/pull/3626) chore(deps): update typing-extensions to 4.16.0 | F/D pass | None | Routine dependency review/test; merge approval required. |
| [#3696](https://github.com/thaynes43/haynes-ops/pull/3696) chore(container): update image python to 0514bc7 | F/D pass | None | Routine build review; coordinate Python base with #3870. |
| [#3707](https://github.com/thaynes43/haynes-ops/pull/3707) feat(github-action)!: Update ubuntu to 26.04 | F/D pass | COPY window source - Success | Fix COPY window source validation before merge; no gate waiver. |
| [#3710](https://github.com/thaynes43/haynes-ops/pull/3710) feat(container): update image ghcr.io/thaynes43/dev-env to v2.41.0 | F/D pass | H7 Metadata Reader Fixtures | Leave runtime/H7 validation to existing owner; no #109/PR517 work. |
| [#3800](https://github.com/thaynes43/haynes-ops/pull/3800) chore(deps): update numpy to 2.5.4 | F/D pass | None | Routine audio dependency review/test. |
| [#3864](https://github.com/thaynes43/haynes-ops/pull/3864) fix(container): update image docker.io/kometateam/kometa to v2.5.3 | F/D pass | None | Manual Kometa update; verify job/Plex timing before any deployment. |
| [#3870](https://github.com/thaynes43/haynes-ops/pull/3870) feat(container): update image python to v3.15 | F/D pass | Book Copy Writer - Success, build-and-push, build-and-push-comfyui | Resolve Book Copy Writer/build-and-push/build-and-push-comfyui failures; no broad Python rollout. |
| [#3871](https://github.com/thaynes43/haynes-ops/pull/3871) feat(container)!: Update image ghcr.io/home-operations/sonarr to v5 | F/D pass | None | Stranded major: Sonarr v5 compatibility/migration review required. |
| [#3911](https://github.com/thaynes43/haynes-ops/pull/3911) fix(deps): update module github.com/kyverno/api to v0.0.1-alpha.5 | F/D pass | None | Review Kyverno test dependency coherently with #3915/#4146; no admission changes. |
| [#3914](https://github.com/thaynes43/haynes-ops/pull/3914) feat(github-release): update comfy-org/comfyui to v0.42.1 | F/D pass | None | ComfyUI source update: supervised build and real render check required. |
| [#3915](https://github.com/thaynes43/haynes-ops/pull/3915) fix(deps): update kubernetes monorepo to v0.37.1 | F/D pass | Archive Guard Source - Success | Archive guard compile/test failure; coherent dependency/API update needed, separate owner. |
| [#3916](https://github.com/thaynes43/haynes-ops/pull/3916) fix(deps): update module github.com/google/go-containerregistry to v0.22.1 | F/D pass | None | Routine test-library dependency review, no production effect claimed. |
| [#3938](https://github.com/thaynes43/haynes-ops/pull/3938) feat(deps): update upgrade-agent image toolchains group | F/D pass | None | Manual paired toolchain/dashboard gates; build validation/review required. |
| [#3942](https://github.com/thaynes43/haynes-ops/pull/3942) feat(deps): update dev-env image toolchain group | F/D pass | None | Manual paired toolchain/dashboard gates; do not roll/restart V1. |
| [#3955](https://github.com/thaynes43/haynes-ops/pull/3955) chore(deps): update github.com/kyverno/pod-security-admission digest to 40bb0fb | F/D pass | None | Review with Kyverno test dependency set; preserve admission assertions. |
| [#3960](https://github.com/thaynes43/haynes-ops/pull/3960) feat(container): update image docker.io/library/python to v3.15 | F/D pass | None | Manual credential-helper Python major/minor transition; review/build, no V1 work. |
| [#4063](https://github.com/thaynes43/haynes-ops/pull/4063) chore(deps): update httpcore2 to 2.16.0 | F/D pass | Cursor Offline Fixtures | HELD V1 mount: paired httpcore2/httpx2 resolution needed; Tom controls V1 merge. |
| [#4064](https://github.com/thaynes43/haynes-ops/pull/4064) chore(deps): update httpx2 to 2.16.0 | F/D pass | Cursor Offline Fixtures | HELD V1 mount: paired httpx2/httpcore2 resolution needed; Tom controls V1 merge. |
| [#4065](https://github.com/thaynes43/haynes-ops/pull/4065) chore(deps): update starlette to 1.9.0 | F/D pass | None | HELD V1 mount: Tom controls V1 merge even though CI green. |
| [#4146](https://github.com/thaynes43/haynes-ops/pull/4146) fix(deps): update module github.com/kyverno/kyverno to v1.19.2 | F/D pass | Archive Guard Source - Success | Kyverno Evaluate API signature mismatch in archive test compile; resolve coherently with dependency set. |
| [#4150](https://github.com/thaynes43/haynes-ops/pull/4150) feat(container): update image ghcr.io/home-operations/qbittorrent to v5.3.0 | F/D pass | None | Manual qBittorrent 5.3: verify MAM approved-client line; no production media action. |
| [#4159](https://github.com/thaynes43/haynes-ops/pull/4159) fix(container): update image ghcr.io/kyverno/charts/kyverno to v3.9.2 | F/D pass | None | Stranded infra update: Kyverno CRD/admission compatibility and operator review required. |
| [#4164](https://github.com/thaynes43/haynes-ops/pull/4164) chore(container): update image docker.io/library/python to bf12ecc | F/D pass | None | Manual credential-helper base digest; coordinate with #3960, no inference of deployment. |
| [#4189](https://github.com/thaynes43/haynes-ops/pull/4189) fix(container): update image ghcr.io/actions/actions-runner to v2.338.1 | F/D pass | build-and-push | Prepared replacement uses exact same upstream pin plus custom VERSION bump; build/smoke/review/merge approval pending. |
| [#4194](https://github.com/thaynes43/haynes-ops/pull/4194) fix(container): update image ghcr.io/traefik/helm/traefik to v42.1.3 | F/D pass | None | Manual shared ingress chart patch; review both controllers; production merge approval required. |
| [#4196](https://github.com/thaynes43/haynes-ops/pull/4196) chore(deps): update pycparser to 3.12 | F/D pass | None | HELD V1 mount: pycparser update requires Tom-controlled merge. |
| [#4198](https://github.com/thaynes43/haynes-ops/pull/4198) chore(container): update image docker.io/library/eclipse-mosquitto to 847b859 | F/D pass | None | Manual broker digest: restart impacts MQTT/HA; explicit scoped production go needed. |

## dev-env — 17 Renovate PRs

`CI` = live branch-required `CI - Success`. This is PR triage only: no edits, dispatches or merges, and no work on #109/PR517. Three failed PRs need their existing infrastructure owner; routine green toolchain PRs still need review and explicit scoped merge authorization.

| PR / update | Required | Failed jobs / next step |
|---|---|---|
| [#100](https://github.com/thaynes43/dev-env/pull/100) chore(deps): update dependency @openai/codex to v0.163.0 | CI pass | Green toolchain/base update; independent review and scoped merge approval remain. |
| [#252](https://github.com/thaynes43/dev-env/pull/252) chore(deps): update ghcr.io/thaynes43/dev-env docker tag to v2.40.0 | CI FAIL | Required image fixture fails: expected old 2.25.0 base vs proposed 2.40.0. Validate intended pin/update fixture under existing dev-env owner; no weakening. |
| [#255](https://github.com/thaynes43/dev-env/pull/255) chore(deps): update golang:1.27.2 docker digest to 8201530 | CI pass | Green toolchain/base update; independent review and scoped merge approval remain. |
| [#277](https://github.com/thaynes43/dev-env/pull/277) chore(deps): update dependency @anthropic-ai/claude-code to v2.1.309 | CI pass | Green toolchain/base update; independent review and scoped merge approval remain. |
| [#278](https://github.com/thaynes43/dev-env/pull/278) chore(deps): update go toolchain directive to v1.27.3 | CI pass | Green toolchain/base update; independent review and scoped merge approval remain. |
| [#280](https://github.com/thaynes43/dev-env/pull/280) chore(deps): update dependency ubuntu to v26 | CI pass | Green toolchain/base update; independent review and scoped merge approval remain. |
| [#284](https://github.com/thaynes43/dev-env/pull/284) chore(deps): update dependency astral-sh/uv to v0.13.5 | CI pass | Green toolchain/base update; independent review and scoped merge approval remain. |
| [#293](https://github.com/thaynes43/dev-env/pull/293) chore(deps): update node.js to 55fb5f5 | CI pass | Green toolchain/base update; independent review and scoped merge approval remain. |
| [#295](https://github.com/thaynes43/dev-env/pull/295) chore(deps): update dependency helm/helm to v4.3.1 | CI pass | Green toolchain/base update; independent review and scoped merge approval remain. |
| [#343](https://github.com/thaynes43/dev-env/pull/343) chore(deps): update dependency siderolabs/talos to v1.14.4 | CI pass | Green toolchain/base update; independent review and scoped merge approval remain. |
| [#344](https://github.com/thaynes43/dev-env/pull/344) chore(deps): update module sigs.k8s.io/controller-tools to v0.23.0 | CI FAIL | Required generated-code job detects CRD drift after controller-tools bump; regenerate/review output under existing owner. |
| [#364](https://github.com/thaynes43/dev-env/pull/364) chore(deps): update dependency pnpm to v12.15.0 | CI pass | Green toolchain/base update; independent review and scoped merge approval remain. |
| [#365](https://github.com/thaynes43/dev-env/pull/365) chore(deps): update module github.com/golangci/golangci-lint/v2 to v2.15.0 | CI FAIL | Required lint fails errcheck at helper_linux.go lines 81/115/335; fix error handling under existing owner. |
| [#479](https://github.com/thaynes43/dev-env/pull/479) chore(deps): update golang docker tag to v1.27.3 | CI pass | Green toolchain/base update; independent review and scoped merge approval remain. |
| [#487](https://github.com/thaynes43/dev-env/pull/487) chore(deps): update gcr.io/distroless/static:nonroot docker digest to d620678 | CI pass | Green toolchain/base update; independent review and scoped merge approval remain. |
| [#501](https://github.com/thaynes43/dev-env/pull/501) chore(deps): update dependency cli/cli to v2.103.0 | CI pass | Green toolchain/base update; independent review and scoped merge approval remain. |
| [#509](https://github.com/thaynes43/dev-env/pull/509) chore(deps): update dependency opentofu/opentofu to v1.13.2 | CI pass | Green toolchain/base update; independent review and scoped merge approval remain. |

## Stale heads, grouping and review gates

All 45 heads are mergeable. A base commit older than current main is not by itself a broken pin or reason to rebase. Recheck exact current head SHA, required checks and compatible dependency groups before delivery. Python base digest/3.15 PRs and Kyverno/API/Kubernetes test dependencies overlap and must be selected coherently rather than merged blindly in parallel. Cursor httpx2/httpcore2 failures are a resolver conflict and require a paired compatible set; their V1 hold is independent of that fix.

Deployed leaf examples: Kometa 2.5.2 awaits #3864 (2.5.3), Sonarr 4.0.20.3012 awaits the reviewed v5 decision (#3871), qBittorrent 5.2.4 awaits approved-client verification before 5.3.0 (#4150). Live Kyverno containers are 1.19.1 while #4159 proposes chart 3.9.2; do not equate chart and app versions without chart evidence. Shared ingress and broker digest updates are not PHP remediations and may disrupt production. No claim that an unmerged pin is vulnerable solely because a newer release exists.

The source repair for #4189 will be linked from #172. Original #4189 stays open until a reviewed replacement is accepted for merge; closure is not used to claim the queue advanced. No retries of known deterministic failures, reviews or CI gate waivers were performed.
