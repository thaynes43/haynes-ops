# Late cert-manager and GitHub CLI upgrades — 2026-10-23

**Progress receipt:** cert-manager is installed and bounded invariants accepted; both CLI source/image PR checks passed and source merged. Dev-env main publication, immutable consumer activation and final health are pending. This report will be finalized with their actual outcomes before Story183 closes.

Canonical [Story183](https://work.haynesnetwork.com/work_packages/183), [Epic173](https://work.haynesnetwork.com/work_packages/173), follows accepted [Story182](https://work.haynesnetwork.com/work_packages/182) and [report4254](https://github.com/thaynes43/haynes-ops/pull/4254). This is a new bounded pass, 16:55–18:25 EDT, by the same sole executor. It does not reopen or extend the preceding pass. Scope is the three proposals remaining at admission: cert-manager1.21.3 and GitHub CLI2.103.2 in the dev-env and upgrade-shepherd source images. Five held Renovate PRs and other owners' work remain excluded.

The referenced NEXT-ROUTINE-PASS.md was absent from this pod's volume. Parent confirmed it belonged to its assessment host; the complete plan is in Epic173 journal1158 and Story182 journal1159 and the verified supported parent message. No filesystem or access workaround was attempted. Search-before-create found no competing late-queue story. Fresh owned worktree agent/renovate-latequeue-1023 started clean from4b3d9d01; Story183 claimed normally, with no forced owner/status transition. Maximum concurrency was the driver plus two read-only source reviewers; runtime changes stayed serial.

## Automation inventory and proposal disposition

All three proposals were awaiting the normal 22:00–06:00 America/New_York Renovate schedule, not failed jobs. Only their exact dashboard checkboxes were requested at20:57:47Z. The five excluded held PRs were untouched.

| Proposal | Why automatic delivery stopped | Delivery state |
| --- | --- | --- |
|4258 cert-manager1.21.3 | Component/chart outside automatic merge; chart/CRDs/issuance-facing security changes need component review. | Merged21:02:47Z, exact3689667267f76cd30436a300314882533100ab32, squash2b293287367eed31db3e3260162323f43ede415b; installed and bounded runtime invariants accepted. |
|4259 dev-env CLI2.103.2 | Explicit manual source/toolchain group; source publication is separate from pinned V1 activation and requires genuine-work/custody checks. | Grouped replacement4261; image/publication/activation gates recorded below. |
|4260 upgrade-agent CLI2.103.2 | Explicit manual source group and previously main-only image build; source publication must advance immutable future-job consumers. | Grouped replacement4261 adds its missing PR image validation; publication/consumer/natural-job gates recorded below. |

## cert-manager review, rollout and limits

The official1.21.3 release published20:47:13Z, after the preceding pass's publication-time observations. Both old/new Helm archives passed chart hash and official maintainer GPG verification. All five new component image indexes and linux/amd64 manifest/config bytes were verified; their legacy Cosign payload signatures passed OpenSSL SHA512 against the official cert-manager public key, with repository identity and image digest assertions. This is actual signature verification, without transparency-log/build-attestation/reproducibility/runtime-binary claims.

Independent and driver source/render review found the same50 resource identities. After exact version-string normalization the only semantic delta is the optional Certificate.status.acme.ari.certID string. Served/storage v1, RBAC, admission rules, security contexts, selectors, resources and existing DNS settings remain unchanged. No values adaptation was needed. ACMEUseARI is Alpha/defaultfalse and not enabled in current values; the private-key Secret ownership fix still affects future issuance. Exact-head required Flux Local - Success and Diff Scope - Success and relevant matrix checks passed; advisory skipped for Renovate, independent review had no findings.

Scoped activityact-210245-894224 preceded merge and targeted Flux reconcile. At21:04:03Z all three controllers, cainjector and webhook ran the matching verified v1.21.3 indexes, Ready with zero restarts. All11 Certificates,2 ClusterIssuers and5 Issuers retained Ready, UID, expiry, renewal time and revision. Certificate CRD is Established, served/stored v1, with optionalcertID present; cert-manager.io/v1 discovery works. Both fail-closed webhook configurations and their CA bundles remain identical to baseline; CA valid through2027-03-02. These configuration/readiness checks do not claim an independently exercised admission round trip.

Bounded219-line startup logs had20 cainjector errors for five Certificate names already absent from the pre-upgrade Certificate inventory, four indexers each; six controller requeues reported ACME client not initialized during startup. The settled last60-second log window had no lines/errors. No orphan cleanup or Secret read was attempted. Preserve this observation for future maintenance; do not call the entire startup log error-free or attribute the absent objects to this upgrade.

LIST Orders/Challenges at cluster scope returned Forbidden once for the unchanged dev-agents/dev-env-workbench principal. Neither object type was retried, fetched through another namespace/principal/route, or treated as an empty ACME queue. This pass accepts installed component/API/CRD/webhook configuration and preservation of existing certificate/issuer invariants; it does not exercise fresh issuance/renewal, ARI rollover, next-key recovery or direct Order/Challenge progression. No forced production renewal or canary was used. The chart startupapicheck is post-install only, so no new upgrade Job was expected or forced. Activity ended21:05Z.

Rollback plan before merge was GitOps re-pin1.21.2 and targeted reconcile if an actual regression occurred, retaining all CRDs, Certificates and Secrets. Both charts template CRDs; older schema can prune optionalcertID. Unchanged v1 storage and optionalstatus-only delta suggest no storage migration barrier, but downgrade was not tested and crds.keep is deletion retention only. Additive correction to the previous source triage: do not promise an older1.21.2 rollback itself re-fetches ARI; it lacks the new certID tracking and restores prior renewal/ownership bugs. ARI is inactive locally. No rollback was needed.

## GitHub CLI source and image validation

Official2.103.2 commit4f2cfe92e3499f8581bf39db77e0a1431fa7b55c fixes attachment token handling. No local call site uses --attach; ordinary API, PR, check/merge and release paths are unchanged by the source delta. Public linux/amd64 archive and checksum file matched release API hashes; credential-free executable/version/help/flag checks passed. Those checks are not authenticated integration or complete-image tests.

Grouped PR4261 exact2ec493aa4f4a48b83153afaf2fd27f60b504c8fd changes only both GH_VERSION arguments and the narrow upgrade-shepherd PR-only image validation job. It builds the real Dockerfile without publishing and smoke-tests as UID1000 with read-only root, networknone, writable /tmp and2CPU/2GiB limits. The job has contents-read permission only and no login/sign/push/secrets. Existing main publisher/signing semantics and mutable tags remain. Independent native and advisory review found no issues; required aggregates passed. Record actual separate image-CI and main publication outcomes below before installed claims.

This necessary CLI-validation subset overlaps the prepared Story180 packet. Canonical180 was checked: grooming/readiness owner only, no implementation executor dispatched. Parent acknowledged the subset; the overlap and permission review are recorded on183. No claim of180 acceptance or implementation of its separate same-run publisher-smoke remainder. Existing manual workflow dispatch may publish from non-main refs as before; this pass accepts only automatic main merge publication. No tag policy or security/network baseline change.

## Runtime custody and remaining acceptance

Original22 retained worktrees/361 WIP hashes matched at20:58:42Z, on original storage. The normal new standby task163143 was additionally clean at164b87ed with zero untracked files, prompt/no busy or working tool-child metadata. Its clean tree was separately locked and cold-start metadata preserved privately on original storage; no transcript/evaluator payload copied. Operations queue20done/1failed/1meta,0active. These are preflight observations, not an indefinite reservation or substitute for fresh premerge checks. Final activation must recheck all23 preserved trees and actual current work, including new panes/operations. No duplicate worker or production Job was launched for proof.

CLI publication, coherent consumer activation, post-start custody/runtime and final five-minute Gatus outcomes pending at this draft checkpoint. Do not infer delivery from source review or mutable tags alone.


## 17:12 EDT source publication checkpoint

Grouped source4261 merged21:09:37Z as1057e9b858f3a14485ad7c9e9231b1889354d895 after both real PR image checks passed: dev-env40023585141 and shepherd40023585206. Source/native/advisory reviews had no findings and both required aggregates passed. Shepherd automatic main push run40024697879 succeeded, publishing/signing6e5ff891dbb83939fc7cb56d790a1e10b10fe53d154af3e851318b0c497d6513. Dev-env main40024697901 is still building. Neither runtime consumer batch nor source-only review stands in for its pending publication. Signing-step success is not independent cryptographic verification by this driver; the cert-manager public-key proof above has a different, explicitly stated scope.

Fresh23-tree/361-WIP verification at21:10:30Z passed; all private payload custody remains on original storage. Both new mutable-tag images require exact main run SHA and immutable registry bytes before coherent consumer activation. Original excluded/frozen pins and other owners remain untouched.


## 17:18 EDT immutable publication and activation candidate

Dev-env automatic main40024697901 completed successfully, including offline runtime-user GH2.103.2 smoke, publication and signing. Registrybytes/header/config proof identifies6d606d399c1cda4ca888aa92d17aea2d44bb44016c93aa8a2214d7aa51dfdd57; the same digest appears in the main workflow log. Both images are linux/amd64 single manifests, with GH_VERSION2.103.2 in build history. Adjacent image-publication and main-builds receipts preserve both exact main runs at1057e9b8, immutable digests/config identities and the independent-signature-verification limit.

Consumer4263 exact8ee3ad21937559c5280285dc77edc710c38af148 changes only12digestlines/5files, sevenDev andfiveShepherd. Independent source/native and advisory review found no pin-scope issue. Advisory could not reach public GHCR in its sandbox; primary anonymous proof was already collected before that review and matches main publisher logs. Required aggregate Flux and final current-work/custody checks remain gates; no installed-consumer claim yet.


## 17:24 EDT installed runtime and preserved custody

Consumer4263 merged21:19:53Z atbc368cdd44483a25f4b535be75cc644ee4ef4a52 after both required aggregates/relevant matrix jobs, native consumer/driver review and advisory no-findings. The final21:19:49Z gate matched all23 retained trees/361 WIP files, original refs/locks/private cold-start hashes; targetstandby163143 was at a prompt, no busy/working tool child, clean tree/zero untracked. Both Cursor roots were clean at original refs. Operations had20done/1failed/1meta and0active. No cloud-claim enumeration or indefinite idle reservation is claimed.

Activityact-211949-521123 was declared before merge. Targeted reconcile requests ran serially; normal source fanout also applies resources. Actual dev-env-ops dependency on dev/dev-env held the old healthy operations pod until V1 became Ready, then replaced operations. Each parent was replaced once for this batch. Source fetch was healthy; affected targets reachedbc368 or newer mainad1c86b8 containing the pin change. No source-controller restart/cache/config/identity workaround was needed.

At21:24:09Z new V1 UID55b4a7df-e648-4db4-93bb-88075297c1ca is3/3Ready, new operations UID747d626b-7f8a-468d-9c2d-05e59759d2e7 is2/2Ready, all five running containers use6d606d39 with zero restarts. Both actual /usr/local/bin/gh binaries report2.103.2. V1 code-server remains4.141.1/279b570 with Code1.141.0 and bundled Node24.22.1. At21:23:15Z all23 retained trees/361 WIP/cold-start/locks/refs still matched on original storage; both Cursor roots remain clean at original refs. Existing boot generated the new normal standby172232, not a manually launched duplicate worker. This23-tree receipt covers preserved trees, not every future standby; a later activation must inventory current work again.

Actual curation CronJob and Vexa future-template images use6d606d39. All five main/triage/responder future-container pins use6e5ff891; schedules and suspension flags unchanged. Stable-name Vexa template ConfigMap data updates future spawned Jobs, without replacing Vexa components or existing Jobs. No V1/operations mounted script or requirements changed and no Reloader workaround was needed. Old completed curation/agent Jobs remain historical and were not deleted, replaced, or miscounted as current failures.

Rollback target for an actual new regression is the previous immutable a2e5fe04 dev-env and0c734d13 Shepherd pair through a reviewed GitOps pin revert and targeted reconcile, preserving PVCs/original WIP and checking current work before any replacement. No rollback was needed. Final five-minute Gatus and normal21:30 responder/triage acceptance remain pending; no extra Job is launched to manufacture evidence. The main four-hour Shepherd schedule next00:00Z is outside this pass, and curation/Vexa future execution is not forced or claimed as exercised.
