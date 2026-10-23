# Code-server and Audiobookshelf delivery — Story182

**Delivered and monitored at 16:51 EDT:** code-server4.141.1 on ESPHome, Home Assistant and the rebuilt V1/operations image; Audiobookshelf2.37.2. Five runtime/source PRs4251/4252/4253/4255/4256 merged. Gatus61/61 endpoints passed the full five-minute window; newest sample45.3seconds old.134/134 HelmReleases and157/157 ExternalSecrets Ready;177/178 Kustomizations Ready, only the pre-existing frozen H7 audit unready. Ceph HEALTH_OK. No production rollback was required. All declared activities ended and temporary HA suspensions cleared.

Three newly arriving proposals remain **unrequested for the next bounded pass**, not compatibility failures or additional old held PRs: cert-manager1.21.3 and two CLI2.103.2 source groups. The five original Book/dev-env/compiler/Sonarr/MAM holds remain. This closes this bounded pass under its original17:02 EDT deadline, not Tom's whole changing Renovate queue. Historical checkpoints below retain their original pending wording; final evidence and remaining work follow them.

[Story182](https://work.haynesnetwork.com/work_packages/182), [Epic173](https://work.haynesnetwork.com/work_packages/173), related accepted [Story181](https://work.haynesnetwork.com/work_packages/181) / [historical report4230](https://github.com/thaynes43/haynes-ops/pull/4230).

Additive follow-up for three new dashboard candidates posted20:00Z /16:00 EDT, after Story181 accepted19:52Z snapshot. Same sole executor/worktree/branch, original18:02–21:02Z deadline, max driver+two bounded helpers; no new executor or budget reset. Supported Accepted→In progress transition on181 returnedHTTP422; not forced, retried through another status/identity or bypassed. Normal search-first creation/claim established182 under existingEpic173 and related181; historical acceptance remains intact.

## Release and proposal review

Requests submitted on dashboard1 at20:03:33Z:
-4251 ESPHome code-server sidecar, exact62618fa8e73c2df24cc53c600ebd2861727c6b5d.
-4252 HA code-server sidecar, exact87e5b8dc318b6951be2da7ec6e1fb488e074b292.
-4253 V1 image source ARG, exactff52ab6f655463362f3c372729ead1c39362a17e.

Independent native and driver review found no required local adaptation. [Official4.141.1 release](https://github.com/coder/code-server/releases/tag/v4.141.1) advances bundled Node24.21.0→24.22.1 for Brotli decoder security and rejects compressed JSON/urlencoded request bodies. VS Code gitlink unchanged. No local compressed-body caller established; existing absolute paths/auth/port/disable flags remain compatible. A current global Node does not repair the separate code-server bundle.

Upstream exact-release proxy E2E had a Save As quick-input timeout; not called all-green or harmless. An earlier successful run has the identical complete Git tree and passed that same test. Nondeterministic UI timing supported, exact cause unproven. Failed disable-file-downloads mode is absent from local flags. See [independent exact-head/source proof](renovate-code-server-2026-10-23-review.md).

Driver anonymously verified registry index and amd64 child bytes/header:4.141.1 indexbfe06d7f36273d84a61d6bf2f9656b9f612f9c5c6fbb45ba5e19a29b42ded6f4, child8f7089d81c3f7deba60d98c9aa5c78fd893d03f8b68055deaaf6bac7d2cc4752. No independent cryptographic attestation verification claimed.

## Fresh preservation and backup gates

At20:04:17Z all21 original retained worktrees/361 WIP files matched custody hashes. Operations metadata:20done/1failed/1meta, no active work orders. Specific remaining V1 task haynes-ops-1023-150749 had prompt indicators, no busy indicator or working tool child in bounded process metadata; no transcript payload printed. Its tree was not in original21. Now separately locked/preserved on original storage at104f47ed3206cb5988ee77ba8f2aaa35d9f9c4b0, branchagent/haynes-ops-1023-150749, tracked clean, zero untracked files. Private cold-start/manifest remain at original /home/dev/.local/state/renovate-181-cutover/codepatch-150749-*; original context/owner governs recovery. No protected/evaluator payload transferred or semantically inspected.22 retained trees, original361 WIP files remain original custody.

Fresh metadata qualification: HA archiveac968f2d from15:13 EDT completed/includes HA settings but database_included:false. Earlier “full native backup” described the API archive operation, not verified recorder inclusion. Historical automaticb9ab6cbe includes database. No restore tested/archive/key exported. One existing-settings backup.create_automatic request dispatched20:05Z to establish a fresh database-inclusive recovery point before another HA parent rollout. Response timed out while manager remained create_backup; completion/inclusion pending, not asserted. This corrects scope without rewriting accepted historical evidence.

## Delivery checkpoint at16:10 EDT

4251 auto-merged20:08:56Z; targeted ESPHome reconcile underway underact-200810-674874. Before rollout ESPHome/HA parents Ready zero restarts; ESPHome process metadata showed no compiler/flasher. Existing esphome-device worker is not firmware upload proof.

4252 awaits completed backup/final exact-head checks; only sidecar changes, Core remains2026.10.3.4253 PR image build/smoke running; source merge/publish is not installed V1. After immutable publication proof, align seven existing V1/ops/futureVexa consumers, fresh genuine-work checks, one further necessary replacement per parent. No mounted scripts/requirements change, so no repeat Reloader ignore workaround planned.

Five existing Book/MAM/broaddev-env/compiler/Sonarr gates and CUDA/Comfy/frozen/keeper holds remain. No repeated compile/forced MAM access, extra production worker, credential operation, security-policy change, decommission/storage deletion. Natural20:00 responder/shepherd/triage Jobs used0c734d13 and succeeded20:00:21/36/38Z per independent coordinator metadata; no duplicate Jobs launched.


## 16:18 EDT installed-sidecar checkpoint

4251 ESPHome is installed: new parentUIDeae9b5ab-bf66-4ebe-8986-f491becb9011, both containers Ready/zero restarts; code-server4.141.1/279b570 with Code1.141.0, bundled Node24.22.1. Local health endpointHTTP200; status expired is its activity heartbeat state, not represented as server failure or a real browser session test. ESPHome app version stays2026.10.0; no firmware flash/compile was launched.

Backup49d64c13 completed20:12:26Z (manageridle/eventcompleted), includes settings and recorder DB,3,395,645,440bytes, currentCore2026.10.3, configured local backup agent. Metadata protected:false is retained truthfully; no archive/key was read/exported, encryption setting changed or restore tested. Driver initially merged4252 upon seeing archive metadata while manager still creating; immediately suspended HA Kustomization+HelmRelease before deployment. Old parentUID78537f0c stayed alive with old sidecar. Only after backup completed were both suspensions resumed. This sequencing error and correction are disclosed; no backup was interrupted.

4252 merged20:12:03Z/90333e7a; actual rollout waited for source recovery. New HA parentUIDd1a0f802-f1ec-438e-b967-e94fd2f7f7c8 has both containers Ready/zero restarts, code-server4.141.1 and bundledNode24.22.1. Core/image remains2026.10.3/index407b89cd; config valid, recorder activeSQLite3.53.4 starting20:16:29Z. MQTT6590/12unavailable, ZWave583/0 and total811unavailable match fresh baseline; unknown2410→2431 is transient/volatile, not claimed identical. Separate MCP remains same8.7.0 API. Both HA suspend fields false; no temporary hold remains.

Source GitRepository repeatedly hit60sGitHub clone deadlines20:13/14/15 while retaining b5af artifact. One declared ordinary source-controller restart recovered Ready/beebb168; activityact-201534-536761 ended. No controller config, identity, permission, egress or credential change. This recurrence after the earlier Story181 incident is an evidence-backed Meatball intake item; root cause remains unproven, not mislabeled policy/auth failure.

4253 exact PR image build/runtime-user offline smoke passed; source merged20:13:35Z/beebb168. Main40017564194 publication is building, not claimed published/installed. Prepared consumer scope is exactly7pins across V1x3/opsx3/futureVexax1. No new production agent Job, notes/inference/publication job or duplicate executor was forced.

Current Gatus five-minute sample at20:18Z is60/61 due the recent HA restart; final green-window acceptance is pending. Historical Story181 acceptance remains valid for its snapshot and does not stand in for this follow-up.


## 16:22 EDT source publication and preserved activation candidate

Main40017564194/job120633458801 completedSUCCESS: exact-image runtime-user/offline smoke reports code-server4.141.1/279b570 with Code1.141.0; publish and keyless sign passed. Registry manifest/config bytes verify a2e5fe04e34adfa073abeb04554b9c706216d0a106d8e2625a8e9f4d0fdf3e5b, and public build history specifies CODE_SERVER_VERSION4.141.1. Raw workflow log remains local. No independent cryptographic verification claim.

PR4255 exact1c9d5c500efddc3064f69339fe7aa4f73177ad86 changes seven image pins across exactly three files (V1x3/opsx3/futureVexax1). Required exact-head Flux checks running; advisory emitted no findings, native final consumer review pending. No mounted data/Reloader workaround, credentials, egress/policy or storage changes. At20:22:13Z all22tree refs/locks/private cold-start hashes and361WIP hashes still match; targettask remains prompt/no busy indicator, both Cursor roots clean at original refs. Final operations/current-work recheck remains required immediately before merge.

Gatus61/61 min-success1 over five minutes at20:22:44Z, after HA/ESPHome recovery. Own permitted metadata now directly verifies natural20:00 responder/shepherd/triage Job UIDs, exact0c734d13 images and succeeded outcomes; earlier independent-only relay has primary metadata in adjacent receipt. No new agent Job was launched.


## Final installed acceptance and recovery truth — 16:51 EDT

4255 merged20:25:11Z at164b87edc36a306dc0ce3484460dc9f61db85e16 after required exact-head checks and independent/driver review. Exactly seven consumer pins advanced to published a2e5fe04e34adfa073abeb04554b9c706216d0a106d8e2625a8e9f4d0fdf3e5b: five running containers across V1/operations, one future curation CronJob template and one future Vexa scribe template. Both new parent pods Ready/zero restarts; code-server4.141.1/279b570, Code1.141.0, bundled Node24.22.1 verified. V1 Cursor CLI remains2026.10.14-0f9edde and both normal Cursor supervisors/children alive. This is process/version acceptance, not an authenticated remote session or production inference test.

Correction to the prepared-scope shorthand: V1/operations mounted scripts and requirements did not change; no Reloader workaround. The separate stable Vexa scribe-notes-jobtemplate ConfigMap data DOES change its future image. The dispatcher consumes it for future Jobs; no existing Job was replaced/forced. Final premerge20:25:08Z and poststartup20:32:52Z receipts preserve all22 retained trees and361 WIP hashes, original refs/locks and cold-start metadata on original storage. Existing target pane was at a prompt without busy/tool-child evidence immediately before merge. Neither a cloud claim inventory nor indefinite idle reservation was established. Normal boot created a new standby pane; future activation must inventory current work afresh rather than reuse this receipt. No private transcript/evaluator payload transferred.

The20:15 source-controller restart was NOT a durable fix. At20:18:16Z GitOps removed the temporary restart annotation and restored its baseline ReplicaSet, replacing the pod and losing its emptyDir artifact cache. Further ordinary Git fetch60second deadlines followed. No second driver restart, source timeout patch, identity/credential/policy change or controller suspension. Ordinary retry recovered20:28:45Z at164b, then fetched Audiobookshelf91ee normally20:43:04Z. The current source is Ready at91ee5cc03b0e1de46200f095ed0f8a90c9b1a10d. Recurring clone latency plus managed restart/cache loss is a Meatball investigation item; root cause remains unproven.

Audiobookshelf4256 exact3b42db966e2361f2e6bb0f3b84ecdee018f9ef14 auto-merged20:41:40Z at91ee5cc03b0e1de46200f095ed0f8a90c9b1a10d after requested outside-schedule approval, independent source/driver review and required checks. Official patch adds transaction/basepath/copy/RSS fixes and rebuilds Node24.21.0→24.22.1; no schema migration/dependency/API-format/mount adaptation found. New pod75c6d669-8990-4079-909b-8f75225ab71a runs2.37.2, actual indexb897c33f05972fb1bf484f7487bc878fa91ec8691335c1db6e2929c3a7658efa, Node24.22.1, localhost healthcheckHTTP200, Ready/zero restarts. Existing20Gi PVC28bed580-0cbb-46f7-a34e-ad4b6a71fdd8 remains Bound. Bounded startup logs: one No migrations to run; zero SQLite/migration/permission error markers. No scan/upload/download/playback/account test was forced or library logs published.

Audiobookshelf recovery gate was the normal daily full-PVC VolSync backup completed04:01:19Z today, duration1m19.9s, approximately16h40old at rollout, healthy WaitingForSchedule. Config/metadata SQLite/WAL remain on that same claim, library read-only. This was a routine patch without schema migration; no extra manual backup was triggered. Do not call that backup fresh app-quiesced or restore-tested, or claim startup created another backup. Image rollback is not data recovery; no restore/downgrade tested. No actual new production failure required rollback.

## Next bounded pass: unrequested late queue and ownership

Dashboard refreshed16:52 EDT: exactly five existing open held Renovate PRs and three awaiting-schedule proposals. No new checkboxes requested for these three and no new PR/build/pin/restart started:

| Branch | Proposal/current state | Required next acceptance |
|---|---|---|
| renovate/quay.io-jetstack-charts-cert-manager-1.x | v1.21.2→v1.21.3, UNREQUESTED | Finish artifact publication/component image proof, rendered CRD/images/RBAC/webhook review, exact required CI, targeted rollout and controller/webhook/CRD/issuer/certificate/renewal acceptance. |
| renovate/dev-env-image-toolchain | cli/cli2.103.1→2.103.2, UNREQUESTED | Source/image PR checks, main publication/signing, coherent seven-consumer pin PR, fresh custody and genuine-work checks, one coordinated future V1/operations replacement and health. |
| renovate/upgrade-agent-image-toolchains | cli/cli2.103.1→2.103.2, UNREQUESTED | Source/image checks, main publication/signing, future-job consumer pin alignment and natural scheduled-job/health acceptance; batch with preceding CLI source changes. |

Cert-manager source triage completed within its bounded gate: official v1.21.3 tag925c22283de556172d48d96a165dc2fa27f7a6ff and chart OCIe482675aec32862d323a9218293ff7de81192337998615e4378c56498473f240 available; official chart GPG provenance independently verified, chart SHA matches. This is chart-only cryptographic proof, unlike unclaimed independent image-attestation verification above. Release/static-manifest publication was still incomplete as of20:45Z; component image availability/signatures not checked. Initial Quay404 without proper OCI Accept was corrected200, not misreported missing chart.

No local values adaptation established, but this is not a trivial no-schema patch: optional Certificate status.acme.ari.certID plus private-key Secret ownership hardening need issuance/rotation review. Chart-managed CRDs enabled/keep true; keep does not guarantee schema-downgrade safety. Do not uninstall/delete CRDs/Certificates, rotate production keys merely for testing, or read Secret payloads. No compatibility failure is asserted; complete publication/CRD/issuance/rollback/health work did not fit the remaining original window. See adjacent source review.

Estimated next-pass wall time45–75minutes for both CLI pipelines together,35–60minutes for cert-manager, uncertain CI/publication/source latency; serial runtime stages and fresh isolated ownership required. Current V1a2e5 and shepherd0c734 working images retained with CLI2.103.1. Root/coordinator selects the next bounded story and verifies no active writer before the same authorized owner resumes; do not silently extend182, overlap execution, manufacture frozen/held successors or reset the original deadline.

Meatball intake: recurring source fetch/cache-loss evidence above; grouped/manual source paths correctly require image publication plus consumer activation;4251/4256 demonstrate eligible auto-merge works after outside-nightly-window request. Existing frozen Book/keeper/H7, Sonarr migration, compiler incompatibility and MAM approval blockers retain their detailed accepted181 evidence. Comfy GPU access grant remains terminal Denied, CUDA compatibility hold unchanged. Parent owns173/100 held packets; no protected payload fetched.

All production source/pin work is merged. Final report PR4254 supplies this durable handoff; private custody/WIP remain on original storage and are not transferred. No active deployment/activity/Flux suspension remains. No extra executor or agent Job was launched. Canonical182 records the same bounded ending state and next safe step.
