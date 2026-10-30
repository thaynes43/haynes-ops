# Morning node maintenance readiness for dev-env

OpenProject #280, Epic #103 / Saga #85. This is the morning checklist for
talosw01 and its physical host, HaynesIntelligence. A worker drain and a physical
host shutdown have different storage and guest impacts; Tom must approve the
exact operation. This document grants no runtime authority. The
AVAIL280-MORNING-READINESS-20261030-03 author performed read-only inspection only.

**Maintenance is still gated.** Portal two-replica deployment is accepted, but
keeper fresh-refresh proof, current external storage qualification and owner
checkpoints must be complete before a drain or power step. No maintenance,
relocation, session recovery, silence or Ceph flag change was performed here.

## Evidence baseline: 2026-10-30, 05:08–05:11 UTC

Source was freshly fetched and pinned to haynes-ops
`bacda7f657e3b3e8c249493e528e8b227af36b3d` (merged #4439). These are point-in-time
observations; repeat the relevant read-only preflight at the actual window.

| Component | Baseline and evidence |
| --- | --- |
| Keeper | `dev-env-system/dev-env-keeper-57f57d47dd-xqrjt`, talosm01, Running, all three containers Ready; pod started `2026-10-30T03:03:40Z`. Live Deployment: one replica, `Recreate`, no node selector or affinity. |
| Keeper Lease | `dev-env-keeper.dev-env.haynesops.com`, a holder present, renew time `2026-10-30T05:10:43.268105Z`. Lease renewal is not a credential refresh. |
| GitHub projection | `dev-agents/dev-env-gh-token`: resourceVersion, `dev-env.haynesops.com/written-at` and expiry **unavailable**. A metadata-only API request returned HTTP 403 at `2026-10-30T05:09:46Z`. No before/after mint proof obtained. |
| Claude projection | `dev-agents/dev-env-claude-live`: resourceVersion and last update time **unavailable**, same metadata-only denial/time. No fresh projection proof obtained. |
| Portal | Two Ready pods: `dev-env-v2-portal-66b4fdc475-lms6g` on talosm05 and `dev-env-v2-portal-66b4fdc475-vqndh` on talosw03. Placement-only read in this work. Accepted rollout evidence is #280 journal 3113 and parent's journal 3122: 2/2 Ready/updated/available, two endpoints, PDB minAvailable 1 / disruptionsAllowed 1, unchanged session/PVC/observed RC identities. Journal 3076 records the prerequisite single-replica image deployment. No rollout checks repeated. |
| Broker / operator | Each has two Running Ready pods split across talosm02 and talosw03. This is placement evidence, not a failover test. |
| DNS canary | `observability/dns-canary-5f4cf969d4-lmtbl`, talosw01, Running, both containers Ready. Live selector is `kubernetes.io/hostname: talosw01`. |
| Rook Ceph | CephCluster `rook-ceph` last checked `05:08:38Z`: HEALTH_OK. CR also retains Progressing/Creating and an OSD-processing message; do not hide those fields. Tools-pod `ceph status --format json` at `05:10:25Z`: quorum 5, OSDs 10/10 up/in, 169/169 PGs active+clean, zero remapped. Four auth/cipher warnings are already muted; HEALTH_OK includes those mutes. |
| External Ceph | Historical authorized read at approximately `04:06Z`, #221 journal 3057 (referenced from #280): HEALTH_OK, quorum 5/5, OSDs 31/32 up/in, one HDD OSD already down/out. No fresh external read or lease in this work. See the conditional storage gate below. |

Secret data was never requested. The API requests required
`Accept: application/json;as=PartialObjectMetadata;g=meta.k8s.io;v=v1`, without a
full-JSON fallback, using this pod's existing identity. Both were denied. No
alternative credential, identity or route was tried. The missing values above
are unknown, not zero, and readiness is not a substitute for them.

## Keeper: preserve the one credential owner

The [keeper manifest](../../kubernetes/main/apps/dev-env-system/keeper/app/helmrelease.yaml)
specifies one replica, `Recreate`, and a leader-election Lease. It has no host
pin. It currently runs off talosw01, so talosw01 maintenance needs no keeper move
on this baseline. Placement can change before morning; inspect again.

If the keeper is on the approved maintenance node at that time, use only normal
Deployment rescheduling as part of the separately approved maintenance. Wait for
the old owner to stop and the replacement to acquire the existing Lease and
become Ready. Preserve the existing durable auth journals. An unexpected host
loss uses the same controller/Lease path; an unreachable old pod is not proof
that its process stopped. If ownership is ambiguous, stop the maintenance and
have Tom restore the host or resolve that ambiguity through the supported path.

Never start a second keeper, raise replicas, switch to RollingUpdate, remove a
Lease or journal, force-delete an uncertain old owner, or run a manual Claude or
Codex login in a session pod. The keeper alone owns rotating credentials. A
second refresher can revoke the entire token family. Recovery is normal
rescheduling with the existing journals and ownership fences; it is not a fresh
login ceremony or an auth repair during maintenance.

### Required fresh-refresh evidence

An observer with existing authorized metadata access must record the following
before maintenance, then after any keeper relocation and again after host return:

1. Observation UTC time, keeper pod/node/start time and Lease holder/renew time.
2. For `dev-env-gh-token`, resourceVersion and the allowlisted
   `dev-env.haynesops.com/written-at` / `expires-at` annotations before and after
   a **natural** mint (normally about every 40 minutes; tokens last about an
   hour). Record both samples. Do not force a mint or restart the keeper.
3. For `dev-env-claude-live`, resourceVersion and the keeper's update annotation
   or the projection writer's managedFields timestamp before and after its
   next normal projection update. Record the actual update time, not Secret
   creationTimestamp, pod age, or the time of a read.
4. Require an advancing GitHub write time across the mint and a fresh Claude
   projection update after the relevant keeper start/relocation. A changed
   resourceVersion alone may reflect another metadata edit; corroborate it
   with the writer/update time. Claude's cadence is separate from GitHub's;
   do not claim one mint proves both.

Use only a PartialObjectMetadata response for the named objects and emit only
these fields. A normal Secret GET followed by client-side jsonpath still obtains
the Secret data and is unsuitable here. Do not dump annotations, auth journals,
tokens or login output. On denial, missing write timestamps, stale projection or
ambiguous ownership, record the gap and hold the disruptive step. This work's
baseline values remain unavailable; no fresh-refresh acceptance is claimed.

## DNS canary: expected loss of the probe on talosw01

The [Deployment](../../kubernetes/main/apps/observability/dns-canary/app/deployment.yaml)
hard-pins its sole replica to talosw01. Its dig and Go resolver containers log
anomalies and 15-minute heartbeats; the canary does not provide cluster DNS.
A cordon alone need not stop the existing pod, but an eviction or node outage
removes this diagnostic coverage. A replacement cannot schedule elsewhere while
the selector remains. During that interval missing heartbeats do not establish
a DNS resolver outage.

The live `kube-prometheus-stack-kubernetes-apps` rules include
`KubePodNotReady` and `KubeDeploymentReplicasMismatch`, both warning severity with
`for: 15m`. Their expressions can cover the missing/pending canary; the latter
also requires no updated-replica changes over ten minutes. No dedicated
dns-canary Prometheus rule was found in the observability rule read. Delivery
to Tom's phone depends on routing, which was not qualified here. Expected
availability alerts would describe a real missing probe, but would be
maintenance noise rather than evidence of broken cluster DNS.

Tom should expect a canary gap and possible generic warnings. Recommended for
an agreed window: explicitly choose whether to accept these notifications or
create expiring, narrow silences for `KubePodNotReady` with
`namespace=observability,pod=~dns-canary-.*`, and
`KubeDeploymentReplicasMismatch` with
`namespace=observability,deployment=dns-canary`. Record the actual alert labels,
silence IDs and expiry. Handle a separately observed rollout-stuck/node alert
only if it matches the approved outage. Do not silence cluster-wide DNS,
storage or credential failures. No silence, selector edit or Flux suspension
was made or is authorized by this checklist.

## Session custody and checkpoints

Read-only `agent-run list --state Running -o json` plus AgentSession/pod metadata
at approximately 05:10 UTC returned **15 Running sessions**. All matched Running
pods. Parent identifies custody routing, not proof that the parent is active or
that it approved interruption. Each session owns its private HOME and work.

| Node | Running session | Mode / agent state | Recorded parent |
| --- | --- | --- | --- |
| talosw01 | dev-env-1030-032005 | task / exited 0 at 04:20:33Z | dev-env-system/dev-env-human |
| talosw01 | dev-env-1030-042543 | task / busy | dev-env-system/dev-env-human |
| talosw01 | haynes-ops-1030-044221 | task / exited 0 at 04:53:08Z | dev-env-1029-132039 |
| talosw02 | dev-env-1027-010042 | remote / busy | haynes-ops-1022-015547 |
| talosw02 | dev-env-1027-010058 | remote / idle | haynes-ops-1022-015547 |
| talosw02 | dev-env-1030-041505 | task / exited 0 at 04:32:15Z | dev-env-1029-132039 |
| talosw02 | haynes-ops-1023-041051 | remote / busy | dev/dev-env |
| talosw02 | haynes-ops-1029-223503 | remote / idle | dev-env-system/dev-env-human |
| talosw02 | haynes-ops-1030-050748 | task / busy (this author) | dev-env-1029-132039 |
| talosw03 | dev-env-1027-111645 | remote / busy | dev-env-1019-205125 |
| talosw03 | dev-env-1028-031951 | local / idle | dev-env-1027-111645 |
| talosw03 | dev-env-1029-125933 | local / idle | dev-env-1019-205125 |
| talosw03 | dev-env-1029-132039 | remote / busy | dev-env-1019-205125 |
| talosw03 | hass-sandbox-1027-154126 | remote / idle | dev-env-1019-205125 |
| talosw03 | haynes-ops-1030-010017 | remote / idle | dev-env-system/dev-env-human |

Kubernetes alone showed **67 Running session pods**: talosw01 8, talosw02 28,
talosw03 31, matched by session pod name. Most of the difference is retained pods
for sessions whose phase is Suspended. Running pod counts are not active-writer
counts and do not establish a completed rescue or stop.

There are no Running **local or remote** sessions on talosw01 in this snapshot.
The three Running tasks above require their named owner/Tom or parent to confirm
a durable checkpoint and disposition before maintenance. For
dev-env-1030-042543, task work is still busy; do not infer consent from this
runbook. For either exited task, exit 0 alone is not rescue/terminal proof.

Five additional talosw01 sessions are already Suspended; leave them Suspended:

| Session | Recorded parent | Baseline agent result |
| --- | --- | --- |
| dev-env-1030-031221 | dev-env-1029-132039 | exited 0 |
| dev-env-1030-031624 | dev-env-system/dev-env-human | exited 0 |
| dev-env-1030-033642 | dev-env-1029-132039 | exited 143, native_protocol_refused |
| dev-env-1030-044406 | dev-env-system/dev-env-human | exited 0 |
| haynes-ops-1030-031418 | dev-env-1029-132039 | exited 0 |

Supported pre-step for an affected local/remote session, if one appears at the
window: its owner checkpoints branch/commit/WIP, tracker handoff and existing
conversation identity, then Tom/the authorized lifecycle owner uses
`agent-run suspend <session>`. Resume that same identity with
`agent-run resume <session>` only after the maintenance/recovery gate and owner
approval. HOME is preserved; do not reap/archive/delete, shorten retention,
mount/copy HOME elsewhere or launch a replacement session. `/tmp` and background
processes are ephemeral. Task resume does not rerun the task prompt.

**Suspend is not an immediate stop.** #273 journal 3117 records a task that
continued writing, pushed a branch and opened a PR after suspend was requested,
while rescue was still running. First obtain the owner's quiescent checkpoint;
then observe supported rescue completion and actual executor stop. Neither the
suspend request, phase alone, absence of a pod, nor exit 0 proves all of that.
If Suspended metadata and a Running retained pod disagree, preserve both facts
and ask the lifecycle owner to establish stop evidence; do not resume it or
force-delete it to make the display agree.

Reuse existing #252 (evicted session still declared Running), #102 (retention
and terminal-proof boundaries) and #222 (absent-clone rescue NotProven) paths.
These are not blanket recovery approvals. #252 still requires Tom's decision
before using recovery on Root; #273 remains stopped under Tom's “Not now”. Do
not reset a receipt, bypass finalizers or fabricate an empty clone/rescue proof.

## Storage: conditional host-loss tolerance

Keep **Rook** and **external Ceph behind gasha01-rbd** separate. The current
Rook read above covers the in-cluster filesystem/storage only. It cannot prove
that session HOME on external RBD, NAS dependencies or physical host loss are
safe. A Bound PVC proves a binding, not availability or recovered contents.

The earlier authorized external read (#221 journal 3057, cross-referenced by
#280 journal 3122) found all 15 pools replicated size 3 / min_size 2 and
host failure domains. HaynesIntelligence held one of five monitors and ten HDD
OSDs. `hdd_repl_pool` had exactly three eligible HDD hosts; `k8s-rbd` used an
any-class rule over four hosts and had one PG. One unrelated HDD OSD was
already down/out. Those are historical facts, not a new current-health sample.

Conditional on that topology, healthy remaining replicas and monitor placement:
losing HaynesIntelligence leaves monitor quorum 4/5. Affected PGs lose at most
one host replica and can serve at 2/3, degraded/undersized at min_size 2, with
**zero extra replica-loss margin**. Another failure holding a remaining replica
of an affected PG can block its I/O. The HDD pool cannot restore three replicas
while one of its only three HDD hosts is absent. Exact host placement of
`k8s-rbd`'s PG remains unknown; do not claim it is unaffected. NAS01/CT101 on the
physical host also needs a separate owner-approved interruption/return plan.

Before power-off, Tom/the existing storage owner must supply fresh external
health, OSD up/in distribution, quorum, pool min_size/rules and PG-state evidence,
explicitly accounting for the already down/out OSD. Confirm remaining replica
availability, NAS dependencies and the maximum window. Prior access leases were
released. This work opens no lease, credential, SSH or new monitoring path and
sets no Ceph flags. Historical discussion of `noout`/`noup` is not authorization
or an instruction to set them. If fresh facts or a safe window are unavailable,
defer power-off. HEALTH_OK alone does not pass this gate.

## Ordered morning sequence and rollback

1. **Tom selects and approves the operation.** Name the node/physical host,
   guest impact, maintenance owner, window and rollback deadline. Approval for
   portal replicas did not approve a drain, session interruption or power-off.
   A worker-only drain does not qualify the physical host/storage step.
2. **Refresh read-only gates.** Re-list session custody/placement, keeper/Lease,
   canary and current storage facts. Reuse accepted portal evidence unless a
   new fault appears; two replicas are not proof of host-loss or OIDC recovery.
   Pass the keeper metadata fresh-refresh gate above. Stop on an unknown owner,
   active writer without checkpoint, credential ambiguity or missing storage
   qualification. Assign a monitor outside the maintenance node.
3. **Tom authorizes the affected session pre-steps.** Owners checkpoint and
   quiesce work; the authorized lifecycle owner suspends only the approved
   affected sessions. Wait for rescue and actual stop evidence. Leave already
   Suspended sessions alone. Any unresolved stop/rescue proof stops the drain.
4. **Tom declares activity and chooses silences before disruption.** Use the
   supported `declare-activity start` with required scope `talosw01` and the
   actually affected apps/namespaces/host, a reason and bounded TTL. Record its
   ID in #280. It is attribution, not a mute. Tom chooses the narrow canary
   silences described above, records IDs/expiry, or explicitly accepts alerts.
   If the supported declaration path is unavailable, leave disruption to Tom
   through the established path; do not invent one.
5. **The approved operator performs the drain, then stops at its gate.** Honor
   PDBs and storage detach/attach safety; do not bypass a blocked eviction,
   force-delete an uncertain writer or alter Flux to force progress. Verify
   surviving services, session stop/rescue evidence and keeper single-owner
   status. If normal keeper rescheduling occurred, require the post-relocation
   natural mint/projection evidence before proceeding. Tom must separately
   approve physical power work only after current external Ceph/NAS gates pass.
6. **Tom/the approved hardware owner performs the power step.** Keep the window
   short, observe quorum/PG availability and surviving services from outside
   the host. A new degraded-PG replica loss, I/O stall, quorum loss, keeper
   ownership/refresh failure or lost surviving endpoint ends the forward plan:
   restore the original host/guest first under that owner's control. Do not
   compensate by changing replication, flags, credentials or admission.
7. **Return and recovery gate.** After host/guest return, the approved operator
   confirms node readiness, storage/quorum and PG recovery, then restores normal
   scheduling through the approved path. Require fresh keeper mint/projection
   metadata again. Owners resume only the same approved local/remote session
   identities; leave stopped/Suspended work and held tasks alone unless their
   owners explicitly resume them. Check retained HOME/history/RC continuity
   through supported reads and owner acceptance; pod Ready alone is insufficient.
8. **Tom closes the window.** Remove only this window's silences, end the activity
   with `declare-activity end <id>`, and record actual source, tested, deployed,
   failover/recovered and accepted states separately in #280. If an unplanned
   loss leaves recovery NotProven, retain HOME and checkpoint the exact blocker
   under the existing recovery story; do not treat absence as success.

Rollback before power-off is to stop the plan, restore normal scheduling only
through the approved owner, and resume only sessions whose owners permit it
after preservation is proven. After power-off, restore the original host and
guests, then pass storage/keeper/session recovery gates. No maintenance rollback
needs to undo the accepted portal two-replica deployment. A separate portal
regression uses the reviewed Git revert procedure in
[portal availability](dev-env-portal-availability.md), not a direct scale.

This docs-only PR stops at open/checks-started plus the #280 evidence handoff.
It is not a maintenance GO, merge/deployment instruction or completed #280
failover acceptance.
