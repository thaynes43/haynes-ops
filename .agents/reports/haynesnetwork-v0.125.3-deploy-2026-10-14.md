# haynesnetwork v0.125.3 deployment — 2026-10-14

The signed release is ready for GitOps deployment. It contains the same-parent staff Wrong-Content Replace retry and scoped failed-download janitor interlock from [haynesnetwork #978](https://github.com/thaynes43/haynesnetwork/pull/978), released by [#980](https://github.com/thaynes43/haynesnetwork/pull/980). All four required checks passed on each exact merged head. Live rollout and bounded audit execution remain pending in this prepared record; no Mickey recovery is authorized by this deployment PR.

## Prepared scope

Fresh haynes-ops base: `0190a1acf67b71ba764ee6b5faeb857334b5baca`. Exactly three image pins move from `v0.125.2` to `v0.125.3`:

- Frontend HelmRelease `mainImage`, shared by all three serving replicas, their migrators and all 24 frontend CronJob templates.
- Downloads `owed-checks` CronJob.
- Downloads `books-census` CronJob.

The independently authorized Pokémon worker stays running without restart or replay. Current Job `pokemon-owned-cont-v1252-20261014-0530`, UID `adcad698-99a1-4b2d-9092-50c8f6e1ba29`, runs on `talosw01` with CPU limit 500m and signed `v0.125.2` digest `sha256:b57e1a6a3a5f37151b9f193c01356169ca94b4cc4eddf68bc3589bd7f4654981`. Its current Pod is Ready with zero restarts. Retained completed or failed Pods are historical evidence and are not removed for image uniformity. The terminated original Mickey worker remains preserved; its successor stays held for full v0.125.3 verification, fresh exact plan and renewed activity coverage.

## Signed artifact gate — verified

Direct anonymous GHCR reads at `2026-10-14T07:19:45Z` returned HTTP 200 for both the image index and its exact digest `.sig` manifest, with response-body hashes matching the Docker-Content-Digest headers:

```text
image: ghcr.io/thaynes43/haynesnetwork:v0.125.3
image digest: sha256:463eb1c92e375f2d52d22c345b42e4f05a0fdf06c19a89e7ae50681f704c0f16
signature manifest digest: sha256:33c81b2479780344cc2ae91801221e6d25156c4a7d6e74e59379fbfd22053d15
source/workflow/tag SHA: 61f83a55eca470d41f9d27941699b8abf29fd6cb
```

[Publisher run 38526806143](https://github.com/thaynes43/haynesnetwork/actions/runs/38526806143), job 115728977479, completed its explicit cosign verification step 11 successfully at `07:15:54Z`, on attempt 1/5. The verified signature JSON binds the exact image digest, `https://token.actions.githubusercontent.com` issuer, `release-please.yml@refs/heads/main` subject, this repository and the exact workflow SHA above. The coordinator independently verified the same artifact pair and claims at `07:21:29Z`.

## Verification plan

Required GitOps checks are `Flux Local - Success` and `Diff Scope - Success`; read any actual advisory findings. After merge, reconcile the three affected Kustomizations and verify all serving/migrator image IDs against the signed digest, all 26 CronJob templates, health, 107 migration-journal rows and baked retry/owned-import/janitor source hashes. Confirm no older active janitor execution can race the new recovery. Keep the running Pokémon v0.125.2 exception distinct from retained history.

Run one bounded Books Census and one bounded owed-check Job from freshly reconciled templates, on the library worker node under nice 19/CPU 500m/backoff 0 with their native deadlines. Report actual execution and verdicts separately: an execution success does not close manual, future or unresolved checks. No fabricated member activity, report or readiness metric is required. Final PLAN-077 owed-note updates remain with the coordinator after the media sequence and its dated hold record.

Current root activity `act-051827-491938` expires at `2026-10-14T11:18:27Z` and covers `frontend,media,downloads,plex,plexops,sonarr,sabnzbd,haynestower`; the coordinator owns its end. The six-hour Mickey successor needs new actual coverage after full release verification and remains uncreated.
