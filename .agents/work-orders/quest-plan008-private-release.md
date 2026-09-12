# PLAN008 Haynes Quest private release audit

Date: 2026-09-12 UTC  
Status: Read-only release baseline ready; application candidate is not ready, and no image pin, PR, activity declaration, reconciliation, rollout, or other live mutation has occurred.

## Scope

This lane prepares a later image-only update to the isolated `haynes-quest-playtest` workload. The worktree is `/home/dev/work/quest-plan008-private-release`, branch `agent/quest-plan008-private-release`, created from freshly fetched `origin/main` at `6b39ea6cb74880be0e274a5501ad5c067bab8d93`.

The exact future GitOps pin is:

`kubernetes/main/apps/frontend/haynes-quest/app/playtest-helmrelease.yaml`

The checked operations PR may change only that manifest's application publication comment and image `tag`, plus this work-order record with final evidence. `QUEST_EPHEMERAL_PLAYTEST: "true"` is already deployed and must remain. The normal Quest manifest, Services, selectors, IngressRoutes, policies, Kustomizations, Secrets, database resources, and every dev-env path remain byte-identical.

The ignored structured baseline is `.private/test-results/plan008-private-release/baseline.json`. It contains only named resource metadata, non-secret environment values, image identities, readiness, routing/policy summaries and hashes. No Secret value or unrestricted pod environment was read.

## Read-only live baseline

Captured at `2026-09-12T15:33:26Z`:

- Flux source `flux-system/haynes-ops` UID `a67b6f6b-743c-497f-9e3b-aae6bfc342ab` is Ready at `main@sha1:6b39ea6cb74880be0e274a5501ad5c067bab8d93`. Kustomization `frontend/haynes-quest` UID `8d6a4df9-aff7-4e3c-beae-d08a450b074f`, generation/observed generation `3/3`, is Ready at the same revision. Its `database/cloudnative-pg-cluster` dependency is also Ready there.
- Normal HelmRelease UID `a0b71566-535e-4c48-991c-38fc6bbe4eaf` is generation `3/3`, Ready after `UpgradeSucceeded`, on app-template `5.1.0+0d039f7760db`. Normal Deployment UID `c17ee749-129e-4059-8091-ba167639604e` is generation `3/3`, revision 3, `1/1` updated/Ready/available. Pod `haynes-quest-7d9b698574-nds6g`, UID `d3e0117d-9dc5-4716-9343-2a740147171b`, is Ready with zero restarts on `sha-3502ac7120f6d7741a1a209415f4c9eeb33f826b@sha256:743bca475c2e19d2534ecd3be7f952e52d0d5adb5724599f1285218f91e87e37`.
- Private HelmRelease UID `117b9801-8cf3-49b2-8f9b-7799fd4c770c` is generation `4/4`, Ready after `UpgradeSucceeded`, on the same chart. Private Deployment UID `d069cbd2-3d92-45f5-8ba6-b93f6cd9c12b` is generation `4/4`, revision 4, `1/1` updated/Ready/available. Pod `haynes-quest-playtest-5fd6bf5558-4mtxj`, UID `0f42455e-4dc1-4354-a605-923446132889`, is Ready with zero restarts on `sha-b66b8ee2723480c6c1e0226af9018109489a1817@sha256:eb685f46f8682e2b73505e02e0a52e738879d4c142b42c6af83fe7d2ec858cdc`.
- The private Deployment has `NODE_ENV=development`, `QUEST_FIXTURE_MODE=true`, `QUEST_EPHEMERAL_PLAYTEST=true`, the private HTTPS origin and port 3000. It references only the existing `haynes-quest-secret` through `envFrom`; the name was checked without reading the Secret. Ephemeral process memory remains the reviewed application boundary even though the unchanged combined Secret and network policy retain database capability.
- Dev-env HelmRelease is generation `30/30` and Ready. Deployment UID `6959868c-24ba-476a-9a6c-5ab72e6545f9` is generation `100/100`, revision 51. Pod `dev-env-dfdd8c894-l724p`, UID `c3a94756-af35-405e-93bc-eb05c2979d3a`, and its `app`, `auth-watch`, and `gh-refresher` containers are Ready with zero restarts on digest `80a2c0b8acc53e63a4174ca0f4b81fd1c40aaabff9eed2bffa587883237b9af1`. This identity and all restart counts must remain unchanged.
- Normal and private HTTPS `/healthz` and `/readyz` each returned 200 through `192.168.40.203`.
- The separate `frontend/haynes-quest-db-init` Job UID `0f22375b-2cb1-49b5-9d53-da21b2b35d13` remains completed with one success, no active or failed pod. An image-only HelmRelease edit does not recreate it.

### Routing and policy identities

| Resource                              | Normal Quest                                                              | Private playtest                                                           |
| ------------------------------------- | ------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Deployment selector                   | controller `main`, instance/name `haynes-quest`                           | controller `main`, instance/name `haynes-quest-playtest`                   |
| Service UID / IP                      | `822590b9-e17a-4666-8780-f975553d27ce` / `10.43.214.26`                   | `f1c93ed4-75ae-45fa-9e1d-efe270e07c37` / `10.43.174.201`                   |
| EndpointSlice UID / ready target      | `d6e80168-d5e0-49a1-9d15-698f2fdde3fe` / normal pod at `10.42.4.222:3000` | `140b275f-3eec-4be2-9c77-3614662c3dbc` / private pod at `10.42.4.210:3000` |
| IngressRoute UID / generation         | `89efd0c9-ba58-4f31-b306-5b6fbc9d23a0` / 1                                | `6bc371bf-4425-4d3b-a818-5a071625be97` / 1                                 |
| Host / backend                        | `haynes-quest.haynesops.com` / `haynes-quest:3000`                        | `haynes-quest-playtest.haynesops.com` / `haynes-quest-playtest:3000`       |
| CiliumNetworkPolicy UID / generation  | `5499b732-b729-4266-993d-f22611dc4904` / 1                                | `d8c0e74a-f505-4908-931d-15edf8fbd333` / 1                                 |
| CiliumEndpoint UID / state / identity | `c4c4e1a9-2950-4bd3-9406-3391a449376c` / ready / `114782`                 | `b053a6e8-787c-4ffa-81b6-c9a7e2692d7c` / ready / `93149`                   |

Both policies validate successfully. Each selects only its named workload, accepts TCP 3000 only from internal Traefik, and permits the existing bounded PostgreSQL DNS/5432 egress. Both routes use `websecure` and the existing `certificate-haynesops` reference. A release should rotate the private pod, ReplicaSet, EndpointSlice target and CiliumEndpoint. It must preserve the Services, route/policy UIDs and specs, normal endpoints, and selectors. Compare the new private Cilium identity labels and ready state; its numeric identity is not stable.

### Source and live-spec hashes

Preserve these source bytes except for the private HelmRelease:

| Source                             | SHA-256 at `6b39ea6`                                               |
| ---------------------------------- | ------------------------------------------------------------------ |
| Normal HelmRelease                 | `6c144bef5739a219cafbf1d299cda5897499baab4c7bad037b4a911481f5d6eb` |
| Private HelmRelease before PLAN008 | `f2b08c463834bdf6782b792ae4df0fc09404a85021efc6f5751ae3a6f24cbb78` |
| Normal policy                      | `36d23462fa463bfa0147adde8d32b310037dbc8c94263e82bf6e9db80b7129a9` |
| Private policy                     | `3241620b999bf0ba39c90acd97affa492396350daa4373904f191c51d5226326` |
| Normal IngressRoute                | `60b22698b1ab9b6e7ca09e0f7848b2b8a7b2f2088d393ec7c4ea885dd64a9e6b` |
| Private IngressRoute               | `4f10acfd2d4ffa5325f0064eb594ccfdcff0d536a25cd954b37f845f9221fe80` |
| App Kustomization                  | `6ae837cc161eaa992a23e532c216e5c2e73f0705bb18d684f3c5385bd0e7b98f` |
| Flux Kustomization                 | `f9eb5c01b0dbd762e1b5ca6b99ab4d7e079228ed902014849c206b0b79e5ca56` |

The ignored baseline also records canonical live `.spec` hashes for both Services, routes and policies. Recapture all baselines immediately before merge; do not treat these values as authority after `origin/main` or live state changes.

## Candidate input gate and exact manifest patch

Do not edit the manifest until the application PR is squash-merged and its exact main image exists. The root should fill these variables from reviewed evidence:

```bash
PLAN008_APP_SHA='<40-hex application squash merge>'
PLAN008_IMAGE_DIGEST='<64-hex digest, without sha256:>'
PLAN008_APP_PR='<number>'
PLAN008_APPLICATION_RUN='<number>'
PLAN008_DOCUMENTATION_RUN='<number>'
[[ "$PLAN008_APP_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$PLAN008_IMAGE_DIGEST" =~ ^[0-9a-f]{64}$ ]]
[[ "$PLAN008_APP_PR" =~ ^[0-9]+$ ]]
[[ "$PLAN008_APPLICATION_RUN" =~ ^[0-9]+$ ]]
[[ "$PLAN008_DOCUMENTATION_RUN" =~ ^[0-9]+$ ]]
PLAN008_TAG="sha-${PLAN008_APP_SHA}@sha256:${PLAN008_IMAGE_DIGEST}"
PLAN008_IMAGE="ghcr.io/thaynes43/haynes-quest:${PLAN008_TAG}"
```

Require the PR and both main-branch runs to match that SHA and be successful:

```bash
gh pr view "$PLAN008_APP_PR" --repo thaynes43/haynes-quest --json state,mergeCommit
gh run view "$PLAN008_APPLICATION_RUN" --repo thaynes43/haynes-quest --json status,conclusion,event,headSha,workflowName
gh run view "$PLAN008_DOCUMENTATION_RUN" --repo thaynes43/haynes-quest --json status,conclusion,event,headSha,workflowName
```

The two run records must be completed successful `push` runs named `Application` and `Documentation` at the exact merge SHA. Preserve the Application job evidence for tests, image build/push, Buildx provenance/SBOM, GitHub provenance attestation and cosign signing. Independently fetch the exact tag from GHCR using all OCI/Docker index and image manifest Accept types. Require HTTP 200 and `Docker-Content-Digest: sha256:${PLAN008_IMAGE_DIGEST}`; keep the bearer token out of output and unset it immediately.

Before patching, fetch `origin/main`, update the task branch cleanly, and establish the current source/live tag rather than relying on the audit's old pin:

```bash
git fetch origin main
PLAN008_MANIFEST=kubernetes/main/apps/frontend/haynes-quest/app/playtest-helmrelease.yaml
PLAN008_BASE=$(git rev-parse origin/main)
test "$(git merge-base HEAD origin/main)" = "$PLAN008_BASE"
plan008_source_tag=$(git show "origin/main:${PLAN008_MANIFEST}" | yq -r '.spec.values.controllers.main.containers.app.image.tag')
plan008_live_image=$(kubectl get deploy -n frontend haynes-quest-playtest -o json | jq -r '.spec.template.spec.containers[] | select(.name=="app") | .image')
test "$plan008_live_image" = "ghcr.io/thaynes43/haynes-quest:${plan008_source_tag}"
test "$(git show "origin/main:${PLAN008_MANIFEST}" | yq -r '.spec.values.controllers.main.containers.app.env.QUEST_EPHEMERAL_PLAYTEST')" = true
```

Use `apply_patch` for the actual edit. It changes only:

```diff
-              # Haynes Quest PR35; checked main publication 34670404335.
+              # Haynes Quest PR<PLAN008_APP_PR>; checked main publication <PLAN008_APPLICATION_RUN>.
               # Isolated candidate review: exact artwork approval remains pending.
-              tag: sha-b66b8ee2723480c6c1e0226af9018109489a1817@sha256:eb685f46f8682e2b73505e02e0a52e738879d4c142b42c6af83fe7d2ec858cdc
+              tag: sha-<PLAN008_APP_SHA>@sha256:<PLAN008_IMAGE_DIGEST>
```

Do not add, remove or reorder environment keys. Do not put placeholders into the manifest.

## Local render and source-scope gate

The current base passes Helm lint against cached app-template 5.1.0, renders exactly `ServiceAccount`, `Service`, and `Deployment` named `haynes-quest-playtest`, and passes the app Kustomize build (current rendered SHA-256 `62c70c4175d4becf5106eb1d9858eab3d58dd7d3693c272331cd8e4a5fdc4dc1`). Repeat after the exact patch:

```bash
PLAN008_CHART=/home/dev/.cache/helm/repository/app-template-5.1.0.tgz
PLAN008_RENDER_DIR=$(mktemp -d /tmp/plan008-render.XXXXXX)
git show "origin/main:${PLAN008_MANIFEST}" | yq '.spec.values' > "$PLAN008_RENDER_DIR/base-values.yaml"
yq '.spec.values' "$PLAN008_MANIFEST" > "$PLAN008_RENDER_DIR/candidate-values.yaml"
helm lint "$PLAN008_CHART" --namespace frontend --values "$PLAN008_RENDER_DIR/candidate-values.yaml"
helm template haynes-quest-playtest "$PLAN008_CHART" --namespace frontend --values "$PLAN008_RENDER_DIR/base-values.yaml" > "$PLAN008_RENDER_DIR/base.yaml"
helm template haynes-quest-playtest "$PLAN008_CHART" --namespace frontend --values "$PLAN008_RENDER_DIR/candidate-values.yaml" > "$PLAN008_RENDER_DIR/candidate.yaml"
kustomize build kubernetes/main/apps/frontend/haynes-quest/app > "$PLAN008_RENDER_DIR/kustomize.yaml"
```

Require the same three kind/name identities in both renders. Compare all non-Deployment resources byte-for-byte. In each Deployment render, replace only the app container image with a fixed marker, then require the normalized Deployments to be byte-identical. Require the unnormalized candidate image to equal `${PLAN008_IMAGE}` and `QUEST_EPHEMERAL_PLAYTEST` to remain true. Unlike WO067, do not normalize away an environment difference; PLAN008 changes no environment value.

Check source scope from the current base rather than fixed hashes:

```bash
plan008_actual_paths=$({ git diff --name-only origin/main; git ls-files --others --exclude-standard; } | sort -u)
plan008_expected_paths=$(printf '%s\n%s\n' \
  '.agents/work-orders/quest-plan008-private-release.md' \
  "$PLAN008_MANIFEST" | sort)
test "$plan008_actual_paths" = "$plan008_expected_paths"
```

For every protected source path listed in the hash table, compare the working copy to `git show origin/main:<path>` with `cmp`; this survives unrelated main advancement and proves the release branch did not edit them. Inspect the complete source and rendered diffs before committing.

The local `flux-local` binary is version 6.0.2. Its baseline tests fail during collection because it does not understand the repository's current HelmRelease `spec.chartRef` without an explicit namespace. This is a tooling-version limitation, not a PLAN008 manifest result. Do not present local 6.0.2 as release validation. CI pins `ghcr.io/allenporter/flux-local:v8.4.0` and is authoritative.

## PR, merge and rollout checklist

Open the operations PR only after the exact candidate, source scope and render pass. Inspect all nine expected checks:

1. `Diff Scope - Success`
2. `Flux Local - Filter`
3. `Flux Local - Test (main)`
4. `Flux Local - Test (edge)`
5. `Flux Local - Diff (main/helmrelease)`
6. `Flux Local - Diff (main/kustomization)`
7. `Flux Local - Diff (edge/helmrelease)`
8. `Flux Local - Diff (edge/kustomization)`
9. `Flux Local - Success`

The required aggregate contexts are `Diff Scope - Success` and `Flux Local - Success`; still inspect every job and sticky rendered-diff comment. Main must show only the private Deployment image change. Edge must be empty. Service and ServiceAccount must not change.

Immediately before the already-scoped rollout, recapture the named live resources into a new ignored baseline and compare it with source. Declare the work before merge/reconcile:

```bash
declare-activity start "deploying reviewed PLAN008 Haynes Quest private playtest image" \
  --scope frontend,haynes-quest,haynes-quest-playtest --ttl 45m
```

After the checked PR is squash-merged:

```bash
flux reconcile kustomization haynes-quest -n frontend --with-source
flux get kustomization haynes-quest -n frontend
flux get helmrelease haynes-quest-playtest -n frontend
kubectl rollout status deployment/haynes-quest-playtest -n frontend --timeout=5m
```

Require source and Kustomization Ready at the exact operations merge revision, private HelmRelease observed generation caught up and Ready after `UpgradeSucceeded`, Deployment `1/1` updated/Ready/available, and the new private pod at the exact desired tag and runtime digest with zero restarts. Confirm the five non-secret environment values, HTTPS health/readiness, fixture session `progressMode: "ephemeral"`, empty save discovery, two distinct fresh-run IDs, direct chapter-one and chapter-two entry, and the focused hosted boss regression journey supplied by the application release evidence.

Normal Quest must retain its immediate pre-merge Deployment/pod UIDs, image, runtime digest, zero restarts, environment and health. Dev-env pod UID `c3a94756-af35-405e-93bc-eb05c2979d3a`, all three container digests/readiness/restart counts, its Deployment generation and its HelmRelease generation must remain unchanged. Services, selectors, IngressRoutes and Cilium policies must retain their immediate baseline UIDs, generations and exact specs. End the activity declaration promptly after verification.

The previous WO067 post-release verifier contains hard-coded pod and baseline values from before the current private rollout. Do not run it unchanged. Reuse its narrow checks only after replacing its baseline from the immediate PLAN008 JSON, retaining `QUEST_EPHEMERAL_PLAYTEST=true` as an invariant rather than an introduced difference, and adding the focused PLAN008 hosted behavior checks. No rollback, data deletion, pod restart or other remediation is pre-authorized by this audit.

## Outstanding inputs

- final reviewed Haynes Quest application PR and squash-merge SHA;
- successful main `Application` and `Documentation` run IDs;
- exact published `sha-<merge>` tag and independently matched immutable digest;
- final application test/browser evidence for the physical Besties regression;
- checked operations PR, rendered-diff review, merge revision, reconciliation and hosted proof.

Until those inputs exist, the released `b66b8ee` private image remains the correct immutable pin.

## Audit verification

- Narrow named-resource baseline reread: passed; recorded Git, Flux, Quest and dev-env identities still matched live state, both source image pins matched their Deployments, all four HTTPS health/readiness probes returned 200, and the ignored JSON parsed successfully.
- `helm lint` of current private values against cached app-template 5.1.0: one chart passed, zero failed; only the informational missing-icon note.
- `helm template`: passed with exactly `ServiceAccount/haynes-quest-playtest`, `Service/haynes-quest-playtest`, and `Deployment/haynes-quest-playtest`.
- `kustomize build kubernetes/main/apps/frontend/haynes-quest/app`: passed; SHA-256 `62c70c4175d4becf5106eb1d9858eab3d58dd7d3693c272331cd8e4a5fdc4dc1`.
- `jq empty .private/test-results/plan008-private-release/baseline.json`: passed.
- `git diff --check`: passed.
- Local `flux-local test` for both clusters: did not collect tests under installed 6.0.2 because that version rejects current `spec.chartRef` objects without a namespace. CI's pinned 8.4.0 runs remain required; no pass is claimed from the local attempts.
