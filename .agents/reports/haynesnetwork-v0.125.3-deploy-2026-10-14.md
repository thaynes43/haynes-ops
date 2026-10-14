# haynesnetwork v0.125.3 deployment — 2026-10-14

The signed release is deployed and verified. It contains the same-parent staff Wrong-Content Replace retry and scoped failed-download janitor interlock from [haynesnetwork #978](https://github.com/thaynes43/haynesnetwork/pull/978), released by [#980](https://github.com/thaynes43/haynesnetwork/pull/980). All four required checks passed on each exact merged head. [haynes-ops #3922](https://github.com/thaynes43/haynes-ops/pull/3922) passed both required checks and its advisory review with no findings, then squash-merged at `07:30:14Z` as `9734a9463d8bd5fa03a71c94e16a4c96d0d7fe76`. The deployment and bounded execution proofs below do not authorize a Mickey recovery or close member or future owed checks.

## Deployed scope

Fresh haynes-ops base: `0190a1acf67b71ba764ee6b5faeb857334b5baca`. Exactly three image pins move from `v0.125.2` to `v0.125.3`:

- Frontend HelmRelease `mainImage`, shared by all three serving replicas, their migrators and all 24 frontend CronJob templates.
- Downloads `owed-checks` CronJob.
- Downloads `books-census` CronJob.

During rollout the independently authorized Pokémon Job `pokemon-owned-cont-v1252-20261014-0530`, UID `adcad698-99a1-4b2d-9092-50c8f6e1ba29`, remained the intentional signed v0.125.2 finite-worker exception on `talosw01`, CPU 500m. The deployment did not restart it. After its E20 verified idle boundary, the coordinator authorized its separate UID-scoped stop at `07:38:07Z` and independently confirmed its Job and Pod absent at `07:40:24Z`. Its replacement requires a separate reviewed cold handoff; do not replay completed slots. At `07:38:27Z` this deployment's inventory contained only three active haynesnetwork serving Pods and 47 retained historical completed/failed Pods. No older janitor execution was active. The original failed Mickey worker remains preserved; its successor is uncreated and held for a fresh exact recovery plan and source review.

## Signed artifact gate — verified

Direct anonymous GHCR reads at `2026-10-14T07:19:45Z` returned HTTP 200 for both the image index and its exact digest `.sig` manifest, with response-body hashes matching the Docker-Content-Digest headers:

```text
image: ghcr.io/thaynes43/haynesnetwork:v0.125.3
image digest: sha256:463eb1c92e375f2d52d22c345b42e4f05a0fdf06c19a89e7ae50681f704c0f16
signature manifest digest: sha256:33c81b2479780344cc2ae91801221e6d25156c4a7d6e74e59379fbfd22053d15
source/workflow/tag SHA: 61f83a55eca470d41f9d27941699b8abf29fd6cb
```

[Publisher run 38526806143](https://github.com/thaynes43/haynesnetwork/actions/runs/38526806143), job 115728977479, completed its explicit cosign verification step 11 successfully at `07:15:54Z`, on attempt 1/5. The verified signature JSON binds the exact image digest, `https://token.actions.githubusercontent.com` issuer, `release-please.yml@refs/heads/main` subject, this repository and the exact workflow SHA above. The coordinator independently verified the same artifact pair and claims at `07:21:29Z`.

## Actual live verification

The GitRepository and all three affected Kustomizations applied `main@sha1:9734a9463d8bd5fa03a71c94e16a4c96d0d7fe76` and reported Ready. The HelmRelease reported Ready at observed generation 282. At `07:37:46Z`, all three `haynesnetwork-main-554bd7574f` serving Pods were Ready, all three migrators had exited 0, and all six app/migrator image IDs matched signed digest `463eb1c9…`. Pod suffixes/UIDs were:

| Pod suffix | Immutable Pod UID | Node |
| --- | --- | --- |
| `526w4` | `32b79656-5b3f-4bb2-b5e9-3a9aad8b4080` | `talosw01` |
| `mv62k` | `eeb43e7f-8634-4134-936b-06a6420d4a74` | `talosm01` |
| `xf2g4` | `e2baf720-1a44-49de-845e-3e7494306aab` | `talosm05` |

All 24 frontend and two downloads CronJob templates used v0.125.3 at `07:38:27Z`. All three local health endpoints and the public ingress returned HTTP 200/status `ok` at `07:39:02Z`. The read-only database check at `07:33:02Z` found 107 migration-journal rows; migrations 0105–0107 matched both baked and applied hashes and expected schema features. Baked domain sources matched the reviewed release tree:

```text
wrong-content-retry.ts: 90a85cd8708062fe5d65cf2a4168df3ac492525968324c7c276cb207aa82a8c3
owned-sonarr-import.ts: afb82d966550d7e98233174a62a8703c31e46846d5194407a161ac12baee79e7
queue-cleanup.ts: 5c639e385da6b53fe01d1760ee26b82d621202e12c2bb655b09c96dbdf8d3eb3
```

One Books Census and one owed-check Job ran sequentially from freshly reconciled templates on the LazyLibrarian worker `talosw03`, under nice 19/CPU 500m/backoff 0/TTL 86400. Both reported native Complete=True, succeeded=1 and exit 0, with the exact signed image ID:

| Job | Job UID | Pod UID | Native completion | Deadline |
| --- | --- | --- | --- | --- |
| `verify-books-v1253-20261014-073925` | `61f9eefe-5ef1-4aef-9798-c5d41118334b` | `52887ebd-0050-4c6f-ae21-c145e3ad4abc` | `07:40:43Z` | 600s |
| `verify-owed-v1253-20261014-074110` | `ca7ecebe-8d32-4c8b-9f0a-ef251870aa9b` | `696544a9-fdc6-40e5-a172-442a3ae566b3` | `07:41:20Z` | 180s |

Census observed 1606 LazyLibrarian books, 1258 ebook files and 753 audio files; unreadable=0, missing=0, wrong-file=16 and hold-count=9. These are observations, not repairs. The owed runner retained existing failure rows for OC-015/034/035/038 and waiting checks. Successful execution does not close manual, future or unresolved checks, prove member completion, or create a readiness gate. Final PLAN-077 Step 6 snapshots and owed-note updates remain with the coordinator after the media sequence and its dated hold record; this deployment did not run that snapshot or change owed YAML.

The coordinator declared actual seven-hour activity `act-073758-527071` at `07:37:58Z`, expiring `2026-10-14T14:37:58Z`, covering `frontend,media,downloads,plex,plexops,sonarr,sabnzbd,haynestower`. These bounded Jobs recorded that activity. The coordinator ended predecessor activity `act-051827-491938` at `07:40Z` after the old Pokémon worker was gone, and owns the new activity's end. Future finite media workers must fit their full deadline plus reserve inside the actual record and remain held for separate fresh-plan authorization.
