# haynesnetwork v0.125.1 deployment — 2026-10-14

The artifact gate is verified; rollout and live checks remain pending. This change ships the P6 correct-story language guard ([#970](https://github.com/thaynes43/haynesnetwork/pull/970)) and parent-owned Sonarr Import Check, truthful active-request status, scoped neutral queue forget and full-slot Fix completion ([#972](https://github.com/thaynes43/haynesnetwork/pull/972), merged as `2c65d28071cd63d039786fbb0c4cd13100a7976a`). Release [#971](https://github.com/thaynes43/haynesnetwork/pull/971) passed all four required checks and merged at `2026-10-14T01:44:54Z` as `11906545fdc9e894d5d3bea77595c4a09300c0ad`.

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

## Post-deploy verification — pending

Root declared `act-015216-436898` at `2026-10-14T01:52:16Z`, before merge, with scope `frontend,media,downloads,plex,plexops,sonarr,sabnzbd,haynestower` and expiry `2026-10-14T04:52:16Z`. Root owns its end, exact-head review, artifact attachment and merge signal. Reconcile only source `haynes-ops` in `flux-system` and Kustomizations `haynesnetwork` in `frontend`, `owed-checks` and `books-census` in `downloads`; verify the actual applied merge revision and readiness.

After rollout, record all three Ready web app/migrator images and image IDs against the signed digest; every Running/Pending frontend haynesnetwork container; all 24 frontend CronJob templates plus both downloads templates; internal/external health; all migrator exits/logs; and the existing 107-row migration journal including hashes for 0105/0106/0107 against SQL baked into the deployed image. Wait for any old active Job to finish naturally; retained finished history does not count as a serving image mismatch.

Run one bounded observe-only Books Census Job and one read-only owed-check Job from newly reconciled templates, checking for active runs before creating either. Record actual Job/Pod image/digest, native completion, exit zero and retained safe logs. Owed failures and waits remain observations; OC-056 cannot run before `2026-10-15T20:10Z` and closed Fix preflight, including Paw Patrol's Watch Carry ordering switch. The P7 fixture never counts as a real report or member use.

Media activation remains separate: only after root approves full signed deployment proof may the existing Pokémon E1 download continue under its original Fix/verified Carry, followed by the previously approved remaining slots with fresh fingerprints and individual physical story/English/Plex/carry proof. No media writer or prepared runtime Job is executed by this GitOps preparation.

Actual deployment and bounded-job results will be appended after verification and persisted to main in the same session, either in this report or the final application PLAN-077 context. No expected future result above is marked passed.
