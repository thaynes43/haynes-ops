# Planned node maintenance for dev-env services and sessions

Planning record: [OpenProject #280](https://work.haynesnetwork.com/work_packages/280),
Epic #103. This runbook is documentation only. It does not authorize a drain,
restart, credential operation or power action. No live maintenance was performed
to validate it. Obtain Tom's approval for the exact node, host, interruption and
maintenance window before executing the disruptive steps below.

## 1. Preflight: identify everything the outage would affect

Choose the Kubernetes node from the approved plan; do not infer it from an old
placement report. Run these read-only checks from an authorized control point
that will remain available throughout maintenance:

```bash
NODE='<approved-node>'
kubectl get nodes -L topology.kubernetes.io/zone
kubectl get pods -A --field-selector "spec.nodeName=$NODE" \
  -o custom-columns='NAMESPACE:.metadata.namespace,NAME:.metadata.name,PHASE:.status.phase,OWNER:.metadata.ownerReferences[*].kind'
kubectl -n dev-env-system get deployments,pdb
kubectl -n dev-agents get deployments,pdb
kubectl -n dev-env-system get pods \
  -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName,READY:.status.containerStatuses[*].ready'
kubectl -n dev-agents get pods \
  -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName,READY:.status.containerStatuses[*].ready'
agent-run list
agent-run fleet
kubectl -n dev-agents get agentsessions \
  -o custom-columns='NAME:.metadata.name,MODE:.spec.mode,OPERATING:.spec.operatingMode,PHASE:.status.phase'
```

The checked-in service configuration is:

| Component | Deployment namespace/name | Replicas and disruption protection |
|---|---|---|
| Keeper | `dev-env-system/dev-env-keeper` | 1, `Recreate`, no configured PDB; sole rotating credential owner, with a Lease |
| Portal | `dev-agents/dev-env-v2-portal` | 1, `RollingUpdate`, no configured PDB; expect interruption on eviction |
| Operator | `dev-env-system/dev-env-operator` | 2, PDB `minAvailable: 1`, leader election |
| Broker | `dev-env-system/dev-env-broker` | 2, PDB `minAvailable: 1`, leader election |
| Session | `dev-agents/<session-name>` | Owned by an `AgentSession`; inspect each session's actual state |

Operator and broker spread over `topology.kubernetes.io/zone` with
`DoNotSchedule`; this is not per-host anti-affinity. Verify that a Ready survivor
is outside the outage and replacement pods can satisfy placement. Neither keeper
nor portal is pinned by a node selector or affinity in these manifests. That does
not prove another node has capacity: check requests including init containers,
pod overhead, taints, available resources and scheduling events before proceeding.
Inventory other node workloads too. In particular, #280 records a hard-pinned
`dns-canary` blocking the earlier maintenance; do not delete or repin it to make a
drain pass. Its owner must resolve the approved plan first.

Read storage metadata without mounting another session's HOME:

```bash
kubectl -n dev-agents get pvc \
  -o custom-columns='NAME:.metadata.name,UID:.metadata.uid,STATUS:.status.phase,CLASS:.spec.storageClassName,ACCESS:.spec.accessModes,VOLUME:.spec.volumeName'
kubectl get storageclass gasha01-rbd ceph-filesystem ceph-block
kubectl get pv \
  -o custom-columns='NAME:.metadata.name,CLAIM:.spec.claimRef.name,DRIVER:.spec.csi.driver,RECLAIM:.spec.persistentVolumeReclaimPolicy,NODEAFFINITY:.spec.nodeAffinity'
kubectl -n dev-agents get pod '<session-name>' \
  -o jsonpath='{range .spec.volumes[*]}{.name}{"\t"}{.persistentVolumeClaim.claimName}{"\n"}{end}'
```

The templates default each `home-<session-name>` to RWO `gasha01-rbd` (external
Ceph RBD). `dev-env-shared` is RWX `ceph-filesystem` (in-cluster Rook CephFS),
mounted at `~/.shared` for ordinary sessions. Some private/canary pods deliberately
omit that shared mount. `dev-env-history-archive` is a separate RWO `ceph-block`
claim. Check actual claims and UID identities; `Bound` does not prove recoverability.
RWO relocation must wait for the old writer to stop and attachment to clear.
Never force-delete a stuck pod or volume attachment to create a second writer.

A Kubernetes drain does not evacuate a physical host's Ceph services or guests.
For a host with a monitor or OSDs, obtain a storage-owner assessment of **both**
Ceph clusters: remaining monitor quorum, OSD failure domains, pool size/min_size,
PG availability, degraded/recovering state and any existing failed OSDs. A green
health summary alone does not qualify loss of the host. Use the storage owner's
existing authorized tooling; external Ceph tolerance is **unverified here**.

For HaynesIntelligence, the [existing hardware handoff](../handoffs/2026-10-29-gpu-pcie-relayout.md)
records talosw01 as VM103, nas01 as CT101, one of five external Ceph monitors and
ten HDD OSDs. It also records CT101's NFS mount from gasha01. The dev-env manifests
use CSI RBD/CephFS, not a direct nas01 NFS mount; this does not establish the full
host/storage dependency chain. Have the host owner verify current guest placement,
NAS clients and storage tolerance before any host shutdown. CT101 and Ceph power
sequencing require their own approved plan; they are not covered by this runbook.

Record the baseline on #280: timestamp, affected services and session owners,
pod/PVC UIDs, storage qualification, expected interruption, blockers and rollback
owner. Keep private conversations and credential material out of the record.

## 2. Move the keeper before a long outage

The keeper is the **sole credential refresher**. It mints the haynes-dev-bot token
every 40 minutes; each token normally lasts 60 minutes. The usual margin at the
next scheduled refresh is about 20 minutes, not 20 minutes after the last mint.
The expiry timestamp is the deadline. Git pushes and `gh` calls fail after expiry
if no fresh token reaches sessions. A newly leading keeper mints immediately.
It also owns Claude/Codex refresh and their durable auth journals; a fresh GitHub
mint does not by itself prove those provider projections are healthy.

1. Before moving it, have Tom or an already authorized Secret reader record only
   the fields below. The ordinary session identity cannot read Secrets; a denied
   request is a stop, not a reason to use another identity or copy credentials.
   Do not request YAML/JSON dumps, `.data`, token contents or login material.

   ```bash
   kubectl -n dev-agents get secret dev-env-gh-token \
     -o jsonpath='{.metadata.resourceVersion}{"\t"}{.metadata.annotations.dev-env\.haynesops\.com/written-at}{"\t"}{.metadata.annotations.dev-env\.haynesops\.com/expires-at}{"\n"}'
   ```

2. With Tom's node/drain approval, a working activity declaration (section 8),
   and sufficient destination capacity, cordon the target. If the keeper is on
   it, an approved workbench operator can restart the existing singleton while
   the cordon makes its replacement schedule elsewhere:

   ```bash
   kubectl cordon "$NODE"
   kubectl -n dev-env-system rollout restart deployment/dev-env-keeper
   kubectl -n dev-env-system rollout status deployment/dev-env-keeper --timeout=300s
   kubectl -n dev-env-system get pods -l app.kubernetes.io/name=dev-env-keeper \
     -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName,READY:.status.containerStatuses[*].ready'
   kubectl -n dev-env-system get lease dev-env-keeper.dev-env.haynesops.com
   ```

   `Recreate` and the existing leader Lease serialize the move. Wait for the old
   keeper to terminate and one replacement to be Ready on another node. If it
   cannot move, stop the outage plan. Never scale keeper, change its strategy,
   start a second keeper, run a manual login, or bypass its durable refresh
   ambiguity safeguards. A lost/ambiguous refresh requires the keeper owner's
   supported recovery procedure.

3. Repeat the metadata command. Require a newer `written-at`, changed
   `resourceVersion` and future `expires-at`, recorded after relocation. A
   resourceVersion change alone can be an unrelated metadata write; it does not
   prove a mint. Keeper readiness is also required. Allow kubelet projection
   propagation (source documents about two minutes), then confirm an existing
   session's ordinary `gh` operation works without displaying its token. Record
   actual observation; do not call metadata alone proof of session projection.
   If expiry is near, mint evidence is missing or auth is ambiguous, postpone
   maintenance. Do not power down the old host while keeper recovery is unresolved.

Skip the restart if keeper already runs safely outside the outage, but still
confirm current mint/expiry evidence before a long window.

## 3. Allow for the portal interruption

The portal has one replica. A drain eviction leaves it unavailable until a
replacement schedules, starts and passes readiness. No maximum downtime is proven
here; allow time for image pulls and dependencies. Its rolling-update surge does
not guarantee availability during node eviction. Existing session HOME/history
remain on their volumes, but portal controls and new browser requests can fail.
Keep the independent control point available.

After its approved eviction/reschedule, verify:

```bash
kubectl -n dev-agents rollout status deployment/dev-env-v2-portal --timeout=300s
kubectl -n dev-agents get pods -l app.kubernetes.io/name=dev-env-v2-portal \
  -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName,READY:.status.containerStatuses[*].ready'
```

Require a Ready portal outside the outage and an authenticated browser check at
the existing portal showing the expected sessions. Rollout completion does not
prove browser authentication or history continuity. Do not increase replicas
without separately qualifying database and session consistency.

## 4. Let operator and broker PDBs govern eviction

With two Ready replicas of each service and an unaffected survivor, normal
eviction should preserve one available replica. Leader handoff can briefly delay
reconciliation or grant handling. A PDB is not a guarantee for an involuntary
host failure, nor proof that a second replica can schedule.

```bash
kubectl -n dev-env-system get pdb
kubectl -n dev-env-system rollout status deployment/dev-env-operator --timeout=300s
kubectl -n dev-env-system rollout status deployment/dev-env-broker --timeout=300s
```

Inspect `ALLOWED DISRUPTIONS` before each eviction. Drain one node at a time and
wait for replacement readiness before another disruption. If eviction blocks,
stop and diagnose. Never bypass a PDB with `--disable-eviction`, a NoExecute taint
or direct pod deletion. Operator/broker replacement does not itself require
restarting session pods.

## 5. Preserve sessions before the node drain

Suspended sessions need no new lifecycle action. Still include any retained pods
in the inventory: [#102](https://work.haynesnetwork.com/work_packages/102), journal
2243, documents a Suspended private task with a Running pod. A successful model
exit or `Suspended` phase is not whole-pod terminal proof. Do not reap it, fake
proof, shorten retention or remove finalizers to clear the node.

For each running local or remote session, arrange a quiet checkpoint with its
owner. Record session identity, branch/commit/WIP, conversation or pairing
continuity evidence and HOME PVC UID privately where appropriate. Commit/push
authorized work; move needed `/tmp` files into HOME. `/tmp` is ephemeral and is
not preserved by rescue. Use the supported operator API through `agent-run`:

```bash
SESSION='<approved-session-name>'
agent-run show "$SESSION"
agent-run suspend "$SESSION"
agent-run show "$SESSION"
agent-run rescue list
kubectl -n dev-agents get pod "$SESSION" --ignore-not-found
kubectl -n dev-agents get pvc "home-$SESSION" \
  -o custom-columns='NAME:.metadata.name,UID:.metadata.uid,STATUS:.status.phase,VOLUME:.spec.volumeName'
```

Suspend requests rescue first, then stopping through the supported lifecycle.
Wait for its actual rescue result and pod state, not merely the CLI's accepted
request. Keep HOME and history; never delete a PVC, session or rescue bundle for
maintenance. Never use `reap` as a shortcut. Task/private-history protections can
retain the executor; stop and consult the owner if it remains on the target.

Automatic **drain-at-idle** is a different mechanism for eligible outdated
sessions. It requires a template revision change, resumable conversation proof,
an idle/waiting agent and no attached client. Current source excludes remote,
summoned, workspace and private-history protected sessions. It does not respond
to a node cordon as a general evacuation mechanism. Do not change templates to
trigger it. Only rely on an already eligible, authorized move after observing
completion; otherwise explicitly suspend at the owner's approved boundary.

**A node drain kills unsuspended work.** Do not start the final drain while any
session lacks an agreed preservation outcome. After all gates pass, the approved
node operator can perform a normal eviction-based drain:

```bash
kubectl drain "$NODE" --ignore-daemonsets --timeout=15m
```

This conservative command may refuse pods with `emptyDir` or unmanaged owners.
Do not automatically add `--force` or `--delete-emptydir-data`; inventory the exact
blockers and obtain approval for any data discard. A timeout is a stop, not proof
of evacuation. Re-inventory the node and confirm only approved remaining system
workloads before Tom performs any separately approved power operation.

Unplanned loss is not this procedure. [#252](https://work.haynesnetwork.com/work_packages/252)
records an evicted session still specified Running, where `resume` was a no-op;
it has no approved generic readback/reset route. Preserve HOME and escalate to
its owner. [#222](https://work.haynesnetwork.com/work_packages/222) records
`BootstrapInterrupted`/`NoCloneProof`: truthful diagnosis still leaves
`RescueFailed` and does not authorize archive/delete. Never create a replacement
clone to manufacture successful rescue.

## 6. Keep Flux changes out of the drain window

Agree a change freeze before section 2. Avoid merges, template/image changes and
manual reconciles during the drain. Flux still reconciles on its own interval.
If the approved window requires a hold, record each currently unsuspended target
and its revision, then suspend only those targets. For example, these hold the
service and session-template changes:

```bash
flux get kustomizations -n dev-env-system
flux suspend kustomization dev-env-keeper -n dev-env-system
flux suspend kustomization dev-env-v2-portal -n dev-env-system
flux suspend kustomization dev-env-operator -n dev-env-system
flux suspend kustomization dev-env-broker -n dev-env-system
flux suspend kustomization dev-env-templates -n dev-env-system
flux suspend kustomization dev-env-session-config -n dev-env-system
```

Set up any hold **before** relocation/drain, despite its explanation appearing
here. A Kustomization hold stops its application of source; it does not suspend
the Helm controller, existing Deployments, operator lifecycle or already pending
rollouts. Check those are settled first. Parent reconciles or git edits can alter
the hold; monitor the recorded suspend state and coordinate the freeze. Do not
broaden it to the whole cluster casually. Git remains the source of truth.

After recovery, explicitly resume every hold introduced for this window; leave
pre-existing holds intact. For the example above, if all six were newly held:

```bash
flux resume kustomization dev-env-keeper -n dev-env-system
flux resume kustomization dev-env-v2-portal -n dev-env-system
flux resume kustomization dev-env-operator -n dev-env-system
flux resume kustomization dev-env-broker -n dev-env-system
flux resume kustomization dev-env-templates -n dev-env-system
flux resume kustomization dev-env-session-config -n dev-env-system
flux get kustomizations -n dev-env-system
```

Review queued revisions before resume, especially session-template changes. If
they conflict with a runtime hold, stop for the owner and hand off the still-held
targets explicitly. Do not silently apply a held deployment.

## 7. Roll back or verify after uncordon

If relocation or preservation fails, abort the outage while the host is still
available. Keep the original volumes and healthy service replicas. Restore node
schedulability when safe; a failed restart may then schedule back on it. Do not
force a second keeper or RBD writer. If the host has already stopped, Tom and the
host/storage owners restore it under their approved rollback plan before session
recovery. A node outage alone needs no image rollback; source/config rollback,
if necessary, goes through a separately reviewed GitOps change.

After the node and storage owners confirm recovery:

```bash
kubectl wait --for=condition=Ready "node/$NODE" --timeout=300s
kubectl uncordon "$NODE"
kubectl -n dev-env-system rollout status deployment/dev-env-keeper --timeout=300s
kubectl -n dev-env-system rollout status deployment/dev-env-operator --timeout=300s
kubectl -n dev-env-system rollout status deployment/dev-env-broker --timeout=300s
kubectl -n dev-agents rollout status deployment/dev-env-v2-portal --timeout=300s
kubectl -n dev-env-system get pdb
```

Repeat the placement/PVC inventory and storage-owner checks, including CT101 and
any host-level Ceph flags handled by the separate plan. Services can stay on
their replacement nodes; uncordon does not move them back. Repeat the keeper
metadata check from section 2 and observe its **next scheduled mint**, normally
within 40 minutes of the last successful mint, with a later expiry. Record a
pending follow-up owner/time if the window closes before that observation;
do not claim ongoing refresh acceptance yet. Repeat the portal browser check.

For sessions explicitly suspended in this window, resume one at a time with the
owner's go-ahead:

```bash
agent-run resume "$SESSION"
agent-run show "$SESSION"
```

Confirm the same HOME PVC UID, worktree/WIP, conversation/history and existing
remote pairing from the user's normal client. Background processes need starting
again. A task's original prompt does not run again on resume. Failure to reconnect
is a stop for supported owner-led recovery, not permission to delete HOME or
re-pair blindly. Resume Flux as described in section 6, verify Ready at the
reviewed revisions, end the activity and release window-specific grants.

On #280 record source, checks, installed state and actual runtime acceptance
separately. This docs delivery proves none of relocation, storage-loss tolerance,
successful rescue or remote continuity; those need the later approved window's
evidence.

## 8. Approval and activity declaration

Tom must approve the exact drain/cordon window and power actions (guest or host
shutdown, restart, reset or physical work). Session owners must agree their
checkpoint/interruption and recovery plan. Use existing supported permissions
and grants; this document grants no credentials or access. Baseline task sessions
cannot mutate dev-env namespaces; the keeper restart above is an existing
workbench operation. Metadata-only Secret output still requires an authorized
Secret reader. Stop on RBAC/admission denial.

Declare disruptive work **before section 2**, including relocation/restart, Flux
holds or drain, using actual approved scopes and a TTL covering the window:

```bash
declare-activity start "OP280 planned node maintenance" \
  --scope "$NODE,dev-env-system,dev-agents" --ttl 1h
# Record the returned activity ID. End it after verification or an aborted window:
declare-activity end '<activity-id>'
```

Include the physical host and affected storage namespaces in scope when the
approved plan affects them. A declaration is attribution, not alert suppression
or authorization. If the installed command/API is unavailable, do no disruptive
work from that session; hand off to Tom's supported control point. Command syntax
is checked against repo/source documentation; runtime execution and current
permission availability are **unverified by this docs-only story**.

## Sources checked

Source snapshot: haynes-ops `0a48e8b5f1966efd6a93649425dc777a679991a2` and
dev-env `3cb19139fad2b0e3e2d5c09c6f6ebcdaa831b615` (2026-10-30). Recheck live
placement, images and behavior before use; published source may be newer than
the installed component.

- [Keeper manifest](../../kubernetes/main/apps/dev-env-system/keeper/app/helmrelease.yaml),
  [operator](../../kubernetes/main/apps/dev-env-system/operator/app/helmrelease.yaml),
  [broker](../../kubernetes/main/apps/dev-env-system/broker/app/helmrelease.yaml),
  [portal](../../kubernetes/main/apps/dev-env-system/portal/app/deployment.yaml),
  [templates](../../kubernetes/main/apps/dev-env-system/templates/app/templates.yaml),
  [storage](../../kubernetes/main/apps/dev-env-system/storage/app/pvc.yaml),
  [workbench restart RBAC](../../kubernetes/main/apps/dev-env-system/rbac/app/workbench-admin.yaml).
- dev-env [quickstart](https://github.com/thaynes43/dev-env/blob/3cb19139fad2b0e3e2d5c09c6f6ebcdaa831b615/docs/v2-quickstart.md),
  [remote upgrade limitations](https://github.com/thaynes43/dev-env/blob/3cb19139fad2b0e3e2d5c09c6f6ebcdaa831b615/docs/remote-session-upgrades.md),
  [CLI lifecycle help](https://github.com/thaynes43/dev-env/blob/3cb19139fad2b0e3e2d5c09c6f6ebcdaa831b615/internal/agentrun/help.go),
  [drain eligibility](https://github.com/thaynes43/dev-env/blob/3cb19139fad2b0e3e2d5c09c6f6ebcdaa831b615/internal/controller/drain.go),
  [keeper metadata writes](https://github.com/thaynes43/dev-env/blob/3cb19139fad2b0e3e2d5c09c6f6ebcdaa831b615/internal/keeper/secret.go).
- [#239 journal 2964](https://work.haynesnetwork.com/work_packages/239/activity):
  historical preflight and keeper/portal sequencing; not current placement proof.
  Preservation dependencies: #252, #102 and #222, linked in section 5.
