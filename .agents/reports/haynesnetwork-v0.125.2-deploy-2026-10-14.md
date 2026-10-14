# haynesnetwork v0.125.2 deployment — 2026-10-14

The release artifact is verified and this GitOps change is prepared. Live rollout and post-deploy checks remain pending. The release ships the paired audio guidance correction in [#974](https://github.com/thaynes43/haynesnetwork/pull/974); release [#975](https://github.com/thaynes43/haynesnetwork/pull/975) includes that change and passed all four required checks.

## Prepared scope

Fresh haynes-ops base: `ba481434d1f0101df92c7b8c7db80f4bd507351f`. Exactly three image pins move from `v0.125.1` to `v0.125.2`:

- `kubernetes/main/apps/frontend/haynesnetwork/app/helmrelease.yaml`: shared `mainImage` for the three web replicas, migrators and all 24 frontend CronJob templates.
- `kubernetes/main/apps/downloads/owed-checks/app/cronjob.yaml`.
- `kubernetes/main/apps/downloads/books-census/app/cronjob.yaml`.

The existing signed `v0.125.1` Pokémon continuation Job is intentionally preserved. It is a separately approved, bounded media worker with immutable import claims and a verified checkpoint; rollout must not restart it or replay its completed slots. Job `pokemon-owned-cont-v1251-20261014-0415`, UID `ba9e6537-1546-47e5-bcfc-3488270913b7`, started at `04:14:16Z`, has a five-hour deadline at `09:14:16Z`, and runs on `talosw01` with CPU limit 500m, backoff zero and the signed image digest `sha256:5d0a28c69b7ff6ed745a09032d17b7ef72aacd3a3c988d6ab710a946252e165f`. Other active executions will be read back during deployment. Retained finished pods remain historical evidence.

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

## Live verification — pending

Root-owned activity `act-034019-464174` expires at `2026-10-14T09:40:19Z`, scoped to `frontend,media,downloads,plex,plexops,sonarr,sabnzbd,haynestower`. Root owns merge and the activity end. After merge, reconcile the source and the three Kustomizations, then verify all three serving app/migrator pairs against the signed release, all 24 frontend plus both downloads templates, health on both ingresses, and migration 107 with baked SQL hashes. Run only the bounded Books Census and owed-check executions from freshly reconciled templates, retain their safe logs and report native Job/image/exit results.

Report the preserved Pokémon worker as an explicit active `v0.125.1` exception while it runs. Other old active jobs finish naturally; do not remove retained historical pods for tag uniformity. A final active-pod inventory after media workers finish must distinguish running workloads from retained finished Job history. No future Job, pod, migration or health outcome is marked passed here.

Owed-check observations remain distinct from successful execution. OC-052 requires a real report, OC-053/054/055 real member use, and OC-056 cannot run before `2026-10-15T20:10Z` plus closed Fix preflight, including Paw Patrol ordering inside Watch Carry. The removed smoke fixture never counts as a real report or member use.
