# WO067: PLAN007 private playtest release preparation

Date: 2026-09-12 UTC  
Status: The exact published PLAN007 image and private ephemeral flag are verified and ready for the checked operations PR. No PLAN007 cluster mutation has occurred.

## Scope and source state

This record prepares the next release of only the isolated `haynes-quest-playtest` workload. The worktree is `/home/dev/work/quest-private-release-prep`, branch `agent/quest-private-release-prep`, from fresh `origin/main` at `eed48853ae6d96c30f3f75515d7e81d20a0005f2`. Required reading included the repository `CLAUDE.md`, the lead PLAN007 handoff and plan, application WO063, and the preceding PLAN006 operations audit.

Root owns final approval scope, the reviewed image input, final image edit, commit, PR, merge, activity declaration, reconciliation and hosted verification. This preparation lane owns this work-order record and the one explicit `QUEST_EPHEMERAL_PLAYTEST: "true"` entry in the private HelmRelease. It does not mutate the cluster or edit the normal-app or dev-env manifests.

After root supplies and independently verifies the application image, the operations PR should change exactly two files:

1. `kubernetes/main/apps/frontend/haynes-quest/app/playtest-helmrelease.yaml`
2. this work-order record, updated with the exact image publication, PR, render, reconciliation and rollout evidence

The flag portion of the intended manifest hunk is now staged. The old reviewed image remains unchanged until its replacement passes the image gates below:

```diff
-              # Haynes Quest PR33; checked main publication 34659017677.
+              # Haynes Quest PR<PLAN007>; checked main publication <run-id>.
               # Isolated candidate review: exact artwork approval remains pending.
-              tag: sha-d932b484a32d80e4cf3ad2ece1e99d3620d4c55c@sha256:582737215e73ab34dc22445feac16f1773c20b91c27b4f82f3fdcec3ec077a27
+              tag: sha-<application-squash-merge-sha>@sha256:<verified-published-digest>
...
               QUEST_FIXTURE_MODE: "true"
+              QUEST_EPHEMERAL_PLAYTEST: "true"
               QUEST_APP_ORIGIN: https://haynes-quest-playtest.haynesops.com
```

Do not put placeholders into the real manifest. Do not alter the normal `haynes-quest` image or environment, any Secret/ExternalSecret, Service, selector, route, Cilium policy, Kustomization membership or dev-env resource.

After root supplies the exact image, use this read-only staging command to reject malformed input, require the expected old pin, and show the one-line image patch before applying that patch with `apply_patch`:

```bash
WO067_IMAGE='ghcr.io/thaynes43/haynes-quest:sha-<40-hex-merge>@sha256:<64-hex-digest>'
[[ "$WO067_IMAGE" =~ ^ghcr\.io/thaynes43/haynes-quest:sha-[0-9a-f]{40}@sha256:[0-9a-f]{64}$ ]]
wo067_current_image=$(yq -r '.spec.values.controllers.main.containers.app.image.tag' kubernetes/main/apps/frontend/haynes-quest/app/playtest-helmrelease.yaml)
test "$wo067_current_image" = 'sha-d932b484a32d80e4cf3ad2ece1e99d3620d4c55c@sha256:582737215e73ab34dc22445feac16f1773c20b91c27b4f82f3fdcec3ec077a27'
export WO067_IMAGE
wo067_candidate=$(mktemp /tmp/wo067-playtest-helmrelease.XXXXXX.yaml)
yq '.spec.values.controllers.main.containers.app.image.tag = (strenv(WO067_IMAGE) | sub("^ghcr.io/thaynes43/haynes-quest:"; ""))' kubernetes/main/apps/frontend/haynes-quest/app/playtest-helmrelease.yaml > "$wo067_candidate"
diff -u kubernetes/main/apps/frontend/haynes-quest/app/playtest-helmrelease.yaml "$wo067_candidate"
```

The comment's PR/run identifiers must be updated in the same reviewed patch. The staging command deliberately does not edit the repository.

## Why the flag disables database use

The reviewed PLAN007 server contract makes `QUEST_EPHEMERAL_PLAYTEST=true` sufficient to disable application PostgreSQL connection and migration:

- `loadConfig()` first requires fixture development mode, then sets `databaseUrl` to `null` whenever ephemeral playtest mode is true. It does not parse or require `DATABASE_URL` on that path.
- `createConfiguredStore()` returns the bounded `InMemoryQuestStore.ephemeral()` before its PostgreSQL branch. No `PostgresQuestStore` or `pg.Pool` is constructed.
- Startup calls `migrate()` only when the selected store is a `PostgresQuestStore`; the ephemeral store therefore cannot run database migrations.
- `createApp()` rejects ephemeral mode without fixture mode and rejects any ephemeral configuration backed by a non-memory store.
- Application WO063 records focused configuration/server coverage and a built-server probe that started, became ready and created a chapter-two route with `DATABASE_URL` absent.

The existing `haynes-quest-secret` is a combined Secret supplying both `BETTER_AUTH_SECRET` and `DATABASE_URL`, so the playtest pod will still receive a database URL through its unchanged `envFrom`. The unchanged playtest Cilium policy will also continue to permit DNS and TCP 5432 to `postgres16-rw`. The flag prevents the reviewed application from using those values; it does not remove the credential or network capability from the pod. Removing those capabilities would require a separate session-only Secret reference and policy change, which is outside this two-change release and must not be implied by its evidence.

Existing persistent records remain untouched. Ephemeral process memory disappears on restart, and save discovery returns empty in ephemeral mode. No Secret deletion, database cleanup, schema change or normal database action belongs to this release.

### Probe, init-container and reconciliation dependency audit

The private application Deployment has no rendered init containers. Its liveness and startup probes call only the app's `/healthz` endpoint on port 3000; that handler returns the static `ok` response. Its readiness probe calls `/readyz`, which delegates to the selected store. `InMemoryQuestStore.ready()` returns `true` without I/O, so all three probes are independent of PostgreSQL in ephemeral mode.

The live pod has one cluster-injected `k8tz` init container that runs `k8tz bootstrap`, carries no environment or Secret reference, and completed successfully. It is absent from the Helm-rendered Deployment and has no PostgreSQL dependency.

Two broader GitOps dependencies remain but do not create a private-pod database connection:

- The parent Flux Kustomization still has `dependsOn: database/cloudnative-pg-cluster`. A future database Kustomization outage could delay reconciliation before Helm sees this application change, even though the resulting ephemeral pod does not use PostgreSQL. That dependency is shared with the normal app and is outside this private-only patch.
- The app Kustomization inventory contains the separate provisioning Job `haynes-quest-db-init`. It completed once at `2026-09-11T04:04:15Z`, UID `0f22375b-2cb1-49b5-9d53-da21b2b35d13`, with one success, no failures and no active pod. Its manifest is unchanged, so this HelmRelease-only change does not recreate it. It is not an application init container.

At the dependency audit, `database/cloudnative-pg-cluster` was Ready at the same `eed4885` source revision. No private Deployment probe or init container requires PostgreSQL.

## Read-only live baseline

Captured at `2026-09-12T01:22:12Z` before any release change:

- Flux source `flux-system/haynes-ops` UID `a67b6f6b-743c-497f-9e3b-aae6bfc342ab` was Ready at `main@sha1:eed48853ae6d96c30f3f75515d7e81d20a0005f2`. Kustomization `frontend/haynes-quest` UID `8d6a4df9-aff7-4e3c-beae-d08a450b074f`, generation/observed generation `3/3`, was Ready at that exact revision.
- Both HelmReleases were Ready after successful upgrades on app-template `5.1.0+0d039f7760db`. Normal HelmRelease UID is `a0b71566-535e-4c48-991c-38fc6bbe4eaf`; playtest UID is `117b9801-8cf3-49b2-8f9b-7799fd4c770c`; both were generation/observed generation `3/3`.
- Private Deployment UID `d069cbd2-3d92-45f5-8ba6-b93f6cd9c12b`, generation/observed generation `3/3`, was `1/1` updated, Ready and available. Pod `haynes-quest-playtest-547b7f95b7-cdpbr`, UID `9292abea-d1f2-4ba5-8610-b7b88eb321ec`, was Ready with zero restarts. Desired image was `ghcr.io/thaynes43/haynes-quest:sha-d932b484a32d80e4cf3ad2ece1e99d3620d4c55c@sha256:582737215e73ab34dc22445feac16f1773c20b91c27b4f82f3fdcec3ec077a27`; runtime `imageID` matched digest `582737215e73ab34dc22445feac16f1773c20b91c27b4f82f3fdcec3ec077a27`.
- Normal Deployment UID `c17ee749-129e-4059-8091-ba167639604e`, generation/observed generation `3/3`, was `1/1` updated, Ready and available. Pod `haynes-quest-7d9b698574-nds6g`, UID `d3e0117d-9dc5-4716-9343-2a740147171b`, was Ready with zero restarts. It remained on `sha-3502ac7120f6d7741a1a209415f4c9eeb33f826b@sha256:743bca475c2e19d2534ecd3be7f952e52d0d5adb5724599f1285218f91e87e37`, with matching runtime digest.
- Dev-env pod `dev-env-dfdd8c894-l724p`, UID `c3a94756-af35-405e-93bc-eb05c2979d3a`, remained Running and Ready. Its `app`, `auth-watch` and `gh-refresher` containers were all Ready with zero restarts on digest `80a2c0b8acc53e63a4174ca0f4b81fd1c40aaabff9eed2bffa587883237b9af1`.
- Normal and playtest health and readiness endpoints each returned HTTP 200 with `ok` / `ready` bodies over their existing HTTPS hosts.

### Routing, selectors and policy identity

The two workloads remain distinct:

| Resource                             | Normal                                                               | Private playtest                                                    |
| ------------------------------------ | -------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Deployment selector                  | controller `main`, instance/name `haynes-quest`                      | controller `main`, instance/name `haynes-quest-playtest`            |
| Service UID / ClusterIP              | `822590b9-e17a-4666-8780-f975553d27ce` / `10.43.214.26`              | `f1c93ed4-75ae-45fa-9e1d-efe270e07c37` / `10.43.174.201`            |
| Ready EndpointSlice target           | pod UID `d3e0117d-9dc5-4716-9343-2a740147171b` at `10.42.4.222:3000` | pod UID `9292abea-d1f2-4ba5-8610-b7b88eb321ec` at `10.42.3.97:3000` |
| IngressRoute UID / generation        | `89efd0c9-ba58-4f31-b306-5b6fbc9d23a0` / `1`                         | `6bc371bf-4425-4d3b-a818-5a071625be97` / `1`                        |
| CiliumNetworkPolicy UID / generation | `5499b732-b729-4266-993d-f22611dc4904` / `1`                         | `d8c0e74a-f505-4908-931d-15edf8fbd333` / `1`                        |
| Cilium endpoint state / identity     | ready / `114782`                                                     | ready / `93149`                                                     |

Both Cilium policies reported `Valid=True`. The playtest policy selects only `app.kubernetes.io/name=haynes-quest-playtest` plus controller `main`; the normal policy selects only name `haynes-quest` plus controller `main`. Both accept port 3000 only from internal Traefik and currently allow only bounded PostgreSQL DNS/5432 egress. Both IngressRoutes remain generation 1, target their name-matched Service on port 3000 and use the internal `haynesops.com` path.

Source hashes to preserve, except for the planned playtest HelmRelease edit:

| Source                 | SHA-256 at `eed4885`                                               |
| ---------------------- | ------------------------------------------------------------------ |
| playtest HelmRelease   | `11d0c7b13154e399e10778e0eeb9ab20dd37c6b2ea5ca79ac3f021893ea0bbae` |
| normal HelmRelease     | `6c144bef5739a219cafbf1d299cda5897499baab4c7bad037b4a911481f5d6eb` |
| playtest Cilium policy | `3241620b999bf0ba39c90acd97affa492396350daa4373904f191c51d5226326` |
| normal Cilium policies | `36d23462fa463bfa0147adde8d32b310037dbc8c94263e82bf6e9db80b7129a9` |
| playtest IngressRoute  | `4f10acfd2d4ffa5325f0064eb594ccfdcff0d536a25cd954b37f845f9221fe80` |
| normal IngressRoute    | `60b22698b1ab9b6e7ca09e0f7848b2b8a7b2f2088d393ec7c4ea885dd64a9e6b` |
| app Kustomization      | `6ae837cc161eaa992a23e532c216e5c2e73f0705bb18d684f3c5385bd0e7b98f` |
| Flux Kustomization     | `f9eb5c01b0dbd762e1b5ca6b99ab4d7e079228ed902014849c206b0b79e5ca56` |

The staged flag-only playtest HelmRelease is SHA-256 `5e7f00253d2e9add6ec7ac7193c056294e6cf3eb242f1c36e41bf41b6cae5868`. Deleting only its new environment key reproduces the complete base values document byte-for-byte after YAML normalization. The normal HelmRelease remains at its recorded `6c144b...` hash.

## Render and PR gates

Local preparation used the cached exact app-template `5.1.0` chart. Rendering fresh `origin/main` playtest values and the staged flag-only worktree values produced the same three resource identities: `ServiceAccount/haynes-quest-playtest`, `Service/haynes-quest-playtest` and `Deployment/haynes-quest-playtest`. The complete render diff was one Deployment environment entry with value `"true"`; names, labels, selectors, Service and ServiceAccount were byte-equivalent. The rendered Deployment contains no init containers. The old immutable application image is byte-identical in both renders.

Flag-only evidence:

- Base Helm render SHA-256: `a59cec629be021d19518dabf4261f3d928d15c97bf496db857b315991f321e64`
- Flag-only Helm render SHA-256: `4d720b18bb52b3c12372308799c361f1716927b73cf0856df0b809a27d1b3a25`
- Current complete app Kustomize render SHA-256: `fb251a351b42f79ba2518f903dbc9bf17a222075ddc463966488c066845453f0`
- `helm lint` against app-template 5.1.0: one chart passed, zero failed; only the chart's informational missing-icon note
- `kustomize build kubernetes/main/apps/frontend/haynes-quest/app`: passed
- The guarded image-staging command was exercised with a syntactically valid dummy tag/digest and produced exactly one temporary tag-line diff without editing the worktree
- Source diff: exactly the private HelmRelease environment line; the untracked work-order record is the only other worktree path

Once the digest is known, repeat the comparison with both the exact flag and immutable image. Normalize generated Helm metadata, then require:

- exactly those same three kind/name identities;
- no ServiceAccount or Service diff;
- only the playtest Deployment container image and one environment entry differ;
- the normal HelmRelease rendering is byte-equivalent before and after;
- Deployment, pod and Service selectors remain the exact playtest label triple recorded above;
- the Kustomize source diff contains only the planned HelmRelease and this record.

The operations PR must pass and be reviewed across all nine established jobs:

1. `Diff Scope - Success`
2. `Flux Local - Filter`
3. `Flux Local - Test (main)`
4. `Flux Local - Test (edge)`
5. `Flux Local - Diff (main/helmrelease)`
6. `Flux Local - Diff (main/kustomization)`
7. `Flux Local - Diff (edge/helmrelease)`
8. `Flux Local - Diff (edge/kustomization)`
9. `Flux Local - Success`

The two aggregate branch-protection contexts are `Diff Scope - Success` and `Flux Local - Success`, but every individual result and rendered diff still needs inspection. The main HelmRelease and Kustomization diffs must show only the private Deployment image/environment change; edge must be empty. No local `flux-local` limitation should be presented as equivalent to these CI results.

## Image and rollout gates for root

Before touching the operations manifest, require the exact reviewed application PR to be squash-merged and its main-branch Application verify/image and Documentation workflows to pass. Preserve the main merge SHA, run IDs, published `sha-<merge-sha>` tag, immutable digest, Buildx provenance/SBOM, GitHub attestation and cosign-signing results. Fetch the exact tag anonymously from GHCR with OCI index/image and Docker list/image Accept types; require HTTP 200 and a `Docker-Content-Digest` equal to the supplied digest without logging the bearer token.

Immediately before the authorized rollout, recapture the live baseline because another GitOps change may have landed. Root should declare scoped activity for `frontend,haynes-quest,haynes-quest-playtest`, merge the checked operations PR, and reconcile only Kustomization `frontend/haynes-quest` with source. Then require:

- Flux source and Kustomization Ready at the exact operations merge revision;
- playtest HelmRelease observed generation caught up and Ready after a successful upgrade;
- playtest Deployment `1/1` updated/Ready/available with no unavailable replica;
- the new playtest pod's desired image and runtime `imageID` equal the verified PLAN007 digest, its environment includes all three `NODE_ENV=development`, `QUEST_FIXTURE_MODE=true` and `QUEST_EPHEMERAL_PLAYTEST=true`, and it has zero restarts;
- HTTPS `/healthz` and `/readyz` return 200; `/api/session` reports fixture mode with `progressMode: "ephemeral"`; save discovery is empty and two explicit playtest starts return distinct fresh journey IDs;
- fresh chapter-one and chapter-two entry, the no-save/reload behavior, six fixture pictures and the reviewed control/audio route work on the hosted image under the application evidence scope;
- the normal Deployment/pod image, UID, restart count, environment and health remain at their immediate pre-rollout baseline;
- the dev-env pod UID, images, readiness and restart counts remain unchanged;
- Services, selectors, IngressRoutes and Cilium policy UIDs/generations/specs remain unchanged. The playtest pod and EndpointSlice target are expected to rotate. Compare Cilium identity labels and ready state; do not require a recycled numeric identity ID.

End the activity declaration promptly after verification. Do not delete old saves or database rows. The clean manifest rollback is atomic: restore the prior reviewed image and remove `QUEST_EPHEMERAL_PLAYTEST`; the normal app and database remain untouched. Prefer a forward fix if the new image itself is wrong.

## Outstanding release inputs

- Application PR number and reviewed squash-merge SHA
- Successful main Application/Documentation run IDs
- Exact `sha-<merge>` image tag and independently matched immutable digest
- Final operations render, PR/check, merge, reconciliation and hosted proof

Until root provides those inputs, changing the image pin would weaken the immutable release gate. This lane deliberately leaves the old image in place with a concrete, reviewable two-path worktree: the private flag and this record.

## Resumed release checkpoint

On September 12 at 03:27 UTC, the source application PR35 merged as `b66b8ee2723480c6c1e0226af9018109489a1817` after all final checks passed, including 350 tests. While the conversation was paused, operations main advanced through unrelated PR2861 to `f5f6e5d0125df83cad77f95c07ecc40d31101776`, adding a read-only Plex evidence collector. The release worktree fast-forwarded to that base; the Quest manifests and all fixed baseline identities remained unchanged. The first resumed comparison passed 21/23: only the two expected Flux revision checks differed. The next comparison uses the observed current source revision while retaining the original UID/image/restart baseline. This does not replace or erase the initial baseline.

Main application publication runs: Application 34670404335 and Documentation 34670404348, exact b66b8ee. The image remains pending publication. No PLAN007 cluster mutation has occurred.

## Verified release candidate

Application PR35 merged as `b66b8ee2723480c6c1e0226af9018109489a1817`. Main Application 34670404335 and Documentation 34670404348 passed, including 350 tests, Buildx provenance/SBOM, GitHub provenance attestation and cosign signing. Anonymous GHCR GET returned HTTP 200; both its Docker-Content-Digest header and SHA256 of the 857 manifest bytes independently matched `sha256:eb685f46f8682e2b73505e02e0a52e738879d4c142b42c6af83fe7d2ec858cdc`. Independent signature verification is not claimed.

The private image is now pinned to `ghcr.io/thaynes43/haynes-quest:sha-b66b8ee2723480c6c1e0226af9018109489a1817@sha256:eb685f46f8682e2b73505e02e0a52e738879d4c142b42c6af83fe7d2ec858cdc`, with `QUEST_EPHEMERAL_PLAYTEST: "true"`. The complete pre-PR verifier passed artifact identity, exact two-path source scope, invariant hashes, Helm lint/render and Kustomize render. Evidence: `/tmp/wo067-final-render.YZDDH7`; the root inspected its complete two-change Deployment diff. Service and ServiceAccount render identically. The verifier's registry header parser was corrected to portable case-insensitive matching before this pass; independent GET evidence matched throughout.

Current pre-release fixed-baseline reread passes 23/23 at f5f6e5d, in `/tmp/quest-plan007-resumed-baseline-current.json`. Normal Quest and dev-env retain their original UIDs/images/zero restart counts. Operations CI, merge and live verification remain next.
