# haynesnetwork v0.125.2 deployment — 2026-10-14

The signed release is deployed and the bounded live checks passed. The separately approved Pokémon media worker remains on its signed v0.125.1 image while its serial repairs continue. The release ships the paired audio guidance correction in [#974](https://github.com/thaynes43/haynesnetwork/pull/974); release [#975](https://github.com/thaynes43/haynesnetwork/pull/975) includes that change and passed all four required checks.

## Prepared scope

Fresh haynes-ops base: `ba481434d1f0101df92c7b8c7db80f4bd507351f`. Exactly three image pins move from `v0.125.1` to `v0.125.2`:

- `kubernetes/main/apps/frontend/haynesnetwork/app/helmrelease.yaml`: shared `mainImage` for the three web replicas, migrators and all 24 frontend CronJob templates.
- `kubernetes/main/apps/downloads/owed-checks/app/cronjob.yaml`.
- `kubernetes/main/apps/downloads/books-census/app/cronjob.yaml`.

The existing signed `v0.125.1` Pokémon continuation Job is intentionally preserved. It is a separately approved, bounded media worker with immutable import claims and a verified checkpoint; rollout must not restart it or replay its completed slots. Job `pokemon-owned-cont-v1251-20261014-0415`, UID `ba9e6537-1546-47e5-bcfc-3488270913b7`, started at `04:14:16Z`, has a five-hour deadline at `09:14:16Z`, and runs on `talosw01` with CPU limit 500m, backoff zero and the signed image digest `sha256:5d0a28c69b7ff6ed745a09032d17b7ef72aacd3a3c988d6ab710a946252e165f`. The final active inventory below records this preserved worker. Retained finished pods remain historical evidence.

## Artifact gate — verified

Direct registry checks at `2026-10-14T04:26:22Z` returned HTTP 200 for the image index and exact digest `.sig` manifest. Both response-body hashes match their Docker-Content-Digest headers; the signature manifest has one signature layer.

```text
image: ghcr.io/thaynes43/haynesnetwork:v0.125.2
image digest: sha256:b57e1a6a3a5f37151b9f193c01356169ca94b4cc4eddf68bc3589bd7f4654981
signature manifest digest: sha256:7d0a5fd709e9a8562c06c5a4310eb231ee2d8c2b5b38a9d18fdac9f27c5f5158
issuer: https://token.actions.githubusercontent.com
subject: https://github.com/thaynes43/haynesnetwork/.github/workflows/release-please.yml@refs/heads/main
source/workflow SHA: 531df0efed0fba9695341cb46b1f4e861d4b7aa0
```

[Publisher run 38510333350](https://github.com/thaynes43/haynesnetwork/actions/runs/38510333350) completed successfully at `2026-10-14T04:17:44Z`. Its explicit cosign verification step 11 verified the exact image digest on attempt 1/5, with the issuer, workflow subject, source SHA and matching tag claims above. Root independently checked the registry artifacts and verification step before authorizing this change.

Required GitOps checks: `Flux Local - Success` and `Diff Scope - Success`; read advisory findings before merge. Local verification stays within the affected manifests under `nice -n 19`.

## Live verification — passed with preserved worker

Root-owned activity `act-034019-464174` expires at `2026-10-14T09:40:19Z`, scoped to `frontend,media,downloads,plex,plexops,sonarr,sabnzbd,haynestower`. Root owns its end. [GitOps PR #3919](https://github.com/thaynes43/haynes-ops/pull/3919) passed `Flux Local - Success`, `Diff Scope - Success` and advisory review with no findings, then root squash-merged it at `2026-10-14T04:34:44Z` as `9d2556a46a09e50468a0526b07a47ac02ef26f47`. The `haynes-ops` source fetched this revision; the three Kustomizations applied it and reported Ready=True.

At `2026-10-14T04:38:06Z`, all three new web pods (`haynesnetwork-main-b4cb4f559-64cww`, `-kqstm`, `-q4sqk`) had Ready app containers. All three migrators exited zero. Every app and migrator image ID is exactly `ghcr.io/thaynes43/haynesnetwork@sha256:b57e1a6a3a5f37151b9f193c01356169ca94b4cc4eddf68bc3589bd7f4654981`. Root independently verified all six image IDs and migrator exits.

All 24 frontend and both downloads CronJob templates use `v0.125.2`. The final Running/Pending frontend inventory at `2026-10-14T04:45:35Z` contained six serving/migrator containers on v0.125.2 and only the explicitly preserved, Ready Pokémon worker on signed v0.125.1. There were no Pending containers; 43 retained finished frontend containers remain historical evidence. No pod was removed for tag uniformity. After media workers finish, the final active inventory must still be recorded separately from finished history.

Both [internal health](https://haynesnetwork.haynesops.com/api/health) and [public health](https://haynesnetwork.com/api/health) returned HTTP 200 with status `ok`. A read-only database check found 107 migration-journal rows, the expected issue-write/Fix-link/Watch-Carry/smoke-setting schema, and all three baked migration hashes present in the journal:

| Migration | SHA-256 |
| --- | --- |
| 0105 | `e561d6935b91988c4994c4a5eed23abe7a3a062be0b086f2079023c860576585` |
| 0106 | `0f9cfba1b38c9253d01a9375ab4af4399e9157cfd45b855cd7b1e60ee853abf7` |
| 0107 | `fd08da24d175c95f95a81d419656514957f649cca65fb876578ca2b1ca39844e` |

The baked parent-owned import helper SHA-256 remains `db2788aa315faf53e8962b641e42fc563b82f7fc53feadfde9f3762fa3e61714`, matching the reviewed implementation.

Root's GET-only paired-audio verifier passed on the actual deployed release for Mickey Mouse: Mixed-Up Adventures S01E01/S01E02. Both servers confirmed the unique exact paired file and 24 actual audio tracks. Episode previews agreed, returned `already_set` / `present`, suppressed generic guidance, and matched live track names, ordering, language codes and default flags exactly. The check used 21 reads and zero media writes; household preference scope was unchanged.

## Bounded audit executions — completed

Each manual Job came from its freshly reconciled live CronJob after checking for active execution. Both retained CPU limit 500m, backoff zero, the root activity annotation and safe logs. Their pods ran on `talosw03`, used the exact signed v0.125.2 image ID above, exited zero and reached native Complete=True with succeeded=1.

| Job / namespace | UID | Native start → Complete=True | Deadline |
| --- | --- | --- | --- |
| `books-census-v1252-20261014-0441` / `downloads` | `1977f6a6-fcd8-4d30-82ee-e027367f7fb1` | `04:41:19Z → 04:42:32Z` | 600s |
| `owed-checks-v1252-20261014-0443` / `downloads` | `ad4a66a9-f243-4bd9-bf89-7f79c16848ab` | `04:43:21Z → 04:43:30Z` | 180s |

Books Census observed 1,606 LazyLibrarian books, 1,258 ebook files and 753 audio files, with zero unreadable files. Its projection recorded 16 wrong-file, zero missing-file, two foreign-held and three foreign-item findings; nine existing holds and application database read were OK. These are observations, not completed book repairs.

Owed checks read all 56 rows, evaluated 22, and reported 11 pass, seven wait, four fail, four not-yet, two manual and 28 closed. Successful execution does not convert unresolved check verdicts to pass. OC-052 still requires a real report, OC-053/054/055 real member use, and OC-056 cannot run before `2026-10-15T20:10Z` plus closed Fix preflight, including Paw Patrol ordering inside Watch Carry. The removed smoke fixture never counts as a real report or member use.

The shared activity remains open for the separately authorized media work. Pokémon slots 1–7, 9 and 10 had complete physical English/story/import/Plex/carry/protected-file proof before this report; remaining approved slots continue serially, and 8/31 remain held. This deployment report does not claim future media operations completed.
