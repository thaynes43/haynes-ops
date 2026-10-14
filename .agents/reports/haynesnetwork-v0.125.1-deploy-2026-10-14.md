# haynesnetwork v0.125.1 deployment — 2026-10-14

The signed release is deployed and the bounded live deployment checks passed. This change ships the P6 correct-story language guard ([#970](https://github.com/thaynes43/haynesnetwork/pull/970)) and parent-owned Sonarr Import Check, truthful active-request status, scoped neutral queue forget and full-slot Fix completion ([#972](https://github.com/thaynes43/haynesnetwork/pull/972), merged as `2c65d28071cd63d039786fbb0c4cd13100a7976a`). Release [#971](https://github.com/thaynes43/haynesnetwork/pull/971) passed all four required checks and merged at `2026-10-14T01:44:54Z` as `11906545fdc9e894d5d3bea77595c4a09300c0ad`.

## Prepared scope and verified baseline

Fresh haynes-ops base: `5c133617497fdd00cc63ff2aa4eca9dbabe9b949`. Exactly three image pins move from `v0.125.0` to `v0.125.1`:

- `kubernetes/main/apps/frontend/haynesnetwork/app/helmrelease.yaml`, the `mainImage` anchor shared by the three serving replicas, migrators and all 24 frontend CronJob templates.
- `kubernetes/main/apps/downloads/owed-checks/app/cronjob.yaml`.
- `kubernetes/main/apps/downloads/books-census/app/cronjob.yaml`.

Read-only preflight at `2026-10-14T01:25:05Z`: three web pods Running/Ready; their app and migrator images were `v0.125.0`. One active queue-cleanup Job also used `v0.125.0`. All 24 frontend templates and both downloads templates used `v0.125.0`. Retained finished history included `v0.122.1`, `v0.123.1`, `v0.125.0` and `v0.96.0`; those pods are evidence of past executions and are preserved.

## Artifact gate — verified

Direct registry checks at `2026-10-14T01:51:14Z` returned HTTP 200 for the image index and its exact digest `.sig` manifest. Both response-body hashes matched their Docker-Content-Digest headers; the signature manifest has one signature layer.

```text
image: ghcr.io/thaynes43/haynesnetwork:v0.125.1
image digest: sha256:5d0a28c69b7ff6ed745a09032d17b7ef72aacd3a3c988d6ab710a946252e165f
signature reference: sha256-5d0a28c69b7ff6ed745a09032d17b7ef72aacd3a3c988d6ab710a946252e165f.sig
signature manifest digest: sha256:5f3f8f4d725cb18f344fae3d88716a2e046aea6044ab8551e06b9f833cd85e30
issuer: https://token.actions.githubusercontent.com
subject: https://github.com/thaynes43/haynesnetwork/.github/workflows/release-please.yml@refs/heads/main
source/workflow SHA: 11906545fdc9e894d5d3bea77595c4a09300c0ad
```

[Publisher run 38497376028](https://github.com/thaynes43/haynesnetwork/actions/runs/38497376028) completed successfully. Its explicit cosign verification step 11 verified the same image digest and logged `cosign verify succeeded on attempt 1/5` at `2026-10-14T01:48:38.5250928Z`. The signed identity binds the digest to the issuer, workflow subject and source SHA above. Root reviewed the artifact proof before authorizing commit/push/PR.

Required GitOps checks: `Flux Local - Success` and `Diff Scope - Success`. Read any advisory findings; service unavailability does not cause reviewer retries. Local checks stay within the three affected manifests under `nice -n 19`.

## Post-deploy verification — passed

Root declared `act-015216-436898` at `2026-10-14T01:52:16Z`, before merge, with scope `frontend,media,downloads,plex,plexops,sonarr,sabnzbd,haynestower` and expiry `2026-10-14T04:52:16Z`. Root owns its end. [GitOps PR #3908](https://github.com/thaynes43/haynes-ops/pull/3908) passed both required checks and advisory review with no findings, then merged at `2026-10-14T01:58:25Z` as `692295c9e34ff6c9c26cc7f48995fc3e96df9b62`. The reconciled source and the `haynesnetwork`, `owed-checks` and `books-census` Kustomizations reported this revision and Ready=True.

All three serving pods (`haynesnetwork-main-64f9d9cbf-87zs7`, `-c8mm5`, `-tqwpz`) became Running/Ready with zero application restarts. Their three app containers and three successful migrators all report `v0.125.1` and the exact signed image ID `ghcr.io/thaynes43/haynesnetwork@sha256:5d0a28c69b7ff6ed745a09032d17b7ef72aacd3a3c988d6ab710a946252e165f`. Each migrator exited zero and logged `Migrations applied.` Root independently verified all six image IDs and migrator exits.

At `2026-10-14T02:11:07Z`, every Running/Pending frontend haynesnetwork container used `v0.125.1`: six app/migrator containers, with no old active Job remaining. All 24 frontend CronJob templates and both downloads templates use `v0.125.1`. The old queue-cleanup execution finished naturally. Finished pods from older releases remain historical evidence; none were removed for tag uniformity.

Both [internal ingress health](https://haynesnetwork.haynesops.com/api/health) and [public health](https://haynesnetwork.com/api/health) returned HTTP 200 with `{"status":"ok"}`. A read-only database check at `2026-10-14T02:01:56Z` found the existing 107 migration-journal rows, required issue-write/Fix-link/Watch-Carry/smoke-setting schema, and these exact baked-SQL hashes present in the journal:

| Migration | SHA-256 |
| --- | --- |
| 0105 | `e561d6935b91988c4994c4a5eed23abe7a3a062be0b086f2079023c860576585` |
| 0106 | `0f9cfba1b38c9253d01a9375ab4af4399e9157cfd45b855cd7b1e60ee853abf7` |
| 0107 | `fd08da24d175c95f95a81d419656514957f649cca65fb876578ca2b1ca39844e` |

The deployed parent-owned import helper SHA-256 is `db2788aa315faf53e8962b641e42fc563b82f7fc53feadfde9f3762fa3e61714`, matching the reviewed implementation shipped in #972.

## Bounded audit executions — completed

Each manual Job came from the freshly reconciled live template after checking for an active execution, retained its CPU limit of 500m and backoff limit zero, and carries the root activity annotation. Both pods used the exact signed image ID above, exited zero and reached native Complete=True. Safe logs were retained; these executions observe existing state and do not repair media.

| Job / namespace | UID | Native start → Complete=True | Deadline |
| --- | --- | --- | --- |
| `books-census-v1251-20261014-0204` / `downloads` | `38e544ed-964b-4b56-88f0-2aed7194b531` | `02:04:42Z → 02:05:44Z` | 600s |
| `owed-checks-v1251-20261014-0207` / `downloads` | `b1df1dcc-b089-4184-8bf9-0dff1e0844b9` | `02:10:27Z → 02:10:36Z` | 180s |

Books Census observed 1,606 LazyLibrarian books, 1,258 ebook files and 753 audio files, with zero unreadable files. Its projection reported 16 wrong-file findings, zero missing-file findings, two foreign-held and three foreign-item findings; nine existing holds and the application database read were OK. These are census findings, not completed repairs.

Owed checks read all 56 rows, evaluated 22 and reported 11 pass, seven wait, four fail, four not-yet, two manual and 28 closed. Existing OC-015, OC-034, OC-035 and OC-038 failures remain recorded observations; Job success does not mark those checks passed. OC-048/050/051 automated subchecks passed; OC-052 still awaits a real report, and OC-053/054/055 await real member use. OC-056 cannot run before `2026-10-15T20:10Z` and closed Fix preflight, including Paw Patrol's Watch Carry ordering switch. The removed P7 smoke fixture never counts as a real report or member use.

Media activation remains a separate root-reviewed action after this full signed deployment proof. The existing Pokémon E1 download retains its original Fix and verified Carry; subsequent approved slots require fresh fingerprints and individual physical story, English, Plex and carried-account proof. This deployment report does not claim those future media operations completed. Root keeps the shared activity open for that separately authorized work.
