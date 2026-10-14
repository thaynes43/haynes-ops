# haynesnetwork v0.125.4 deployment — 2026-10-14

The signed release is deployed and its serving replicas, templates, migrations and bounded verification runners
are verified. [haynes-ops #3925](https://github.com/thaynes43/haynes-ops/pull/3925) passed both required checks and
its advisory review with no findings, then squash-merged at `11:38:18Z` as
`ef7936cfe41024f05aed8c145b66ad51a0947346`. Two independently authorized finite media workers intentionally
remain on signed v0.125.3; their post-terminal inventory is still owed. Final Mickey E13 to E16 execution requires
its separate reviewed fresh plan and authorization and is not authorized by this deployment.

## Deployed scope

Fresh haynes-ops base: `d7948aecd113096411d0b20959a0452ed81da916`. Exactly three image pins moved from
`v0.125.3` to `v0.125.4`: the frontend HelmRelease `mainImage` anchor (app, migrators and frontend CronJob
templates), downloads `owed-checks`, and downloads `books-census`.

The release contains same-parent selected multipart recovery from
[haynesnetwork #987](https://github.com/thaynes43/haynesnetwork/pull/987), squash-merged at `11:08:27Z` as
`6f431991d1e2dc167166a253203236b5e1255d8e`, with all four required checks successful and renewed advisory review
reporting no findings. [Release #988](https://github.com/thaynes43/haynesnetwork/pull/988) squash-merged at
`11:20:44Z` as `ab8181c36b00d389dbdb05db534b0a10845b6178`, with all four required checks successful.

## Signed artifact gate — verified

[Publisher run 38555912857](https://github.com/thaynes43/haynesnetwork/actions/runs/38555912857),
job `115824866506`, succeeded. Its actual cosign step 11 verified the expected repository workflow identity
regular expression, GitHub OIDC issuer and exact image digest on attempt 1 at `11:25:00.6443625Z`.

The coordinator independently fetched both actual GHCR manifests at `2026-10-14T11:26:10.253469Z`.
Both returned HTTP 200, and each response-body SHA matched its Docker-Content-Digest header:

```text
image: ghcr.io/thaynes43/haynesnetwork:v0.125.4
image digest: sha256:5522d758426a317b42fec609fbe02f8f27cdc042f03b133833002f8ccb2f881f
signature manifest digest: sha256:f4faf47c28acaecc3a19be71e0a847678c46a2e19c7faf62e1b8ad953d91cba2
source/workflow/tag SHA: ab8181c36b00d389dbdb05db534b0a10845b6178
```

The image response is an OCI image index; the `.sig` response is an OCI image manifest. The exact
[credential-free artifact proof](../evidence/haynesnetwork-v0.125.4-artifact-pair-20261014.json) is attached in
this change. Workflow success alone was not used as the deployment artifact gate.

## Actual live verification

At `11:46:00Z`, the GitRepository and all three affected Kustomizations reported Ready at
`main@sha1:ef7936cfe41024f05aed8c145b66ad51a0947346`. The HelmRelease reported Ready with generation and observed
generation 283. All three serving replicas were Ready; each app and migrate image ID matched the signed
`5522d758…` digest and every migrator exited 0:

| Serving Pod | Immutable Pod UID | Node |
| --- | --- | --- |
| `haynesnetwork-main-7dc9896b47-d6brf` | `46b16cba-2b3a-4ea6-bc15-5013182e7af1` | `talosw01` |
| `haynesnetwork-main-7dc9896b47-dpfts` | `2a6f4d08-d7b6-4d59-a829-ad4fb7c3e711` | `talosm02` |
| `haynesnetwork-main-7dc9896b47-dthkz` | `b08d3d7d-5730-4ba5-8d5f-ba62a25c0607` | `talosm05` |

All 24 frontend and both downloads CronJob templates used v0.125.4. Every replica's local health check and
public ingress returned HTTP 200/status `ok` at `11:46:02Z`. Read-only database verification at `11:46:03Z`
found 107 migration-journal rows. Baked migrations 0105–0107 on every replica matched the release tree and
applied journal hashes; all four expected schema features were present. Four deployed domain sources and the
private CLI matched release commit `ab8181c36b00d389dbdb05db534b0a10845b6178` on every replica:

```text
wrong-content-parts.ts: 6edb2b9ded0ef44ad4339163c3f323207eb175ac235fd04927dbdb8b762d751a
owned-sonarr-import.ts: 98fff1fc757d20e120fcee12edd6bc512aa87c8cc386524ccef8ecd55d241c0f
wrong-content-retry.ts: a044c89f43b18f1ebf33422e1db7aee29cc386871fd5fe777366e55190bb20ed
queue-cleanup.ts: acce8e62dd3ea21af7b8aa2c6feaa9962400b7436c63eea216dfcea8d8540d88
repair-guide.ts: 5ff0d6f51f6ae56b50b18e59f3cce1f051705d1957302d567f1ff696df4eaa4c
```

The credential-free [actual deployment proof](../evidence/haynesnetwork-v0.125.4-deployment-20261014.json)
attaches full native identities, image IDs, source and migration hashes, templates and runner outcomes. The
initial inventory recorded 52 historical terminal Pods; none was deleted or altered by this verification.

## Sequential bounded verification Jobs

The coordinator authorized exactly two new downloads Jobs from the reconciled templates. Both ran on
`talosw03` under nice 19, CPU limit 500m, deadline 300s, backoff 0, restartPolicy Never, TTL 86400 and no service
account token. The existing actual scoped activity `act-073758-527071` covered their full bounds, with expiry
`2026-10-14T14:37:58Z`; its immutable record SHA was
`7e4a5046caa6fb82d67ea6c3c755773d2b4124c69e4d2b55e45f42dd2e4a6dfb`.

| Job | Job UID | Pod UID | Created / native Complete | Exit |
| --- | --- | --- | --- | --- |
| `verify-books-v1254-20261014-114755` | `9e96ed9b-763d-47b7-8b43-2865fd355415` | `ec8d9918-0e73-4450-8774-8deb2cd60109` | `11:47:55Z` / `11:49:08Z` | 0 |
| `verify-owed-v1254-20261014-115020` | `d155e9c8-bdc1-43be-bf36-4be11390157a` | `cfdf450c-8825-439b-bfd9-d83d462221a5` | `11:50:20Z` / `11:50:29Z` | 0 |

Both reported native Complete=True, succeeded=1, restartCount=0 and the exact signed v0.125.4 image ID.
Books Census completed before the owed Job was created. It observed 1606 LazyLibrarian books, 1258 ebook
files and 753 audio files; unreadable=0, missing=0, wrong-file=16 and hold-count=9. These are observations;
no library repair was attempted.

The owed runner observed 56 tracker rows: 28 pending, 9 overdue and 22 evaluated. Verdicts were 11 pass,
7 wait, 28 closed, 2 manual, 4 fail and 4 not_yet. The existing failed rows remain OC-015/034/035/038; manual
rows remain OC-009/046, with all dated, member-use and other waits retained. The runner did not update owed
YAML, close any row, prove member completion, run PLAN-077 Step 6 or satisfy the owner's readiness judgment.

## Intentional old-image executions and pending final inventory

A fresh all-namespace running inventory after both verification Jobs still found exactly two older running
haynesnetwork executions. Both retained signed v0.125.3 digest
`463eb1c92e375f2d52d22c345b42e4f05a0fdf06c19a89e7ae50681f704c0f16`, their original identities and finite bounds:

| Independent media Job | Immutable Job / Pod UIDs | Original write cutoff / expiry |
| --- | --- | --- |
| `p077-mickey-resume-v1253-r2` | `5c04bbf3-135e-4330-a52a-cb6fce15a56e` / `610f5a36-6f21-4a3a-a4cd-8e3137db7b2c` | `13:55:35.213261Z` / `14:10:35.213261Z` |
| `pokemon-owned-cont-v1253-20261014-0830` | `998d39b1-cf59-42bb-bec1-9f32126635a3` / `c3b0b685-8ee9-4d95-86bc-292935d6c654` | `13:25:21Z` / `13:40:21Z` |

Those original bounds come from their previously reviewed sealed envelopes and merged PLAN-077 runtime
record; no capture, cutoff or expiry was refreshed. The deployment and verification neither replaced nor
stopped either worker. All other running haynesnetwork Pods in that inventory were the three verified v0.125.4
serving replicas. Historical terminal Pods remain preserved.

**Required follow-through:** after both independent media Jobs become natively terminal, repeat the full
all-image inventory, record actual terminal identities/outcomes and any remaining old running image, and
merge that proof before the overall task ends. This interim deployment report does not claim that milestone.
Final Mickey remains separately held for all 23 initial finishes, actual predecessor termination, a fresh
physical mapping and a reviewed sealed execution envelope. The final proposal is still disabled.
