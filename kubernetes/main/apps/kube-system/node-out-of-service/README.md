# node-out-of-service

A once-a-minute CronJob that applies — and later removes — Kubernetes'
**non-graceful node shutdown** taint:

```
node.kubernetes.io/out-of-service=nodeshutdown:NoExecute
```

## Why (incident 2026-09-09)

A hard power-off of the `talosw02` Proxmox VM left every RWO-PVC pod scheduled there —
`prometheus-0`, `loki-0`, `emqx-core-0`, `alertmanager-1`, `plex`, `sabnzbd` — stuck
`Terminating` for **3.5 hours**.

That is by design, not a bug. When a node dies *without* a graceful shutdown, the
control plane cannot prove the kubelet has stopped writing to the disk. Force-deleting
the pods would break a StatefulSet's "at most one pod per ordinal" guarantee and risk
two writers on one Ceph RBD image, so Kubernetes refuses: pods stay `Terminating`, the
`VolumeAttachment` objects stay bound to the dead node, and the StatefulSet never
creates a replacement. It stays that way until the node comes back or an operator
supplies the missing proof.

The taint above *is* that proof. Applying it tells the GC controller to force-delete the
pods on that node and the attach/detach controller to detach their volumes, so the
StatefulSets reschedule within about a minute. It must come **off** when the node
returns, or `NoExecute` keeps evicting everything that lands there.

This app automates both halves so a dead worker costs ~8 minutes, not 3.5 hours.

## What it does

Every minute, `app/resources/node-out-of-service.sh` runs:

1. Lists all nodes, plus label-selector queries for the control-plane names and for
   any node carrying `EXCLUDE_LABEL`. An **empty control-plane result is fatal** —
   see the guard table.
2. For **every** node, reads the `Ready` condition status, its `lastTransitionTime`,
   and whether the taint is already present.
3. **Guard** — if more than `MAX_UNREADY` nodes have an *unadjudicated* failure
   (not `Ready` **and** not already carrying our taint), it logs `guard: N nodes not
   Ready (> MAX_UNREADY) — refusing to taint (possible partition/API issue)` and
   taints nothing.
4. **Removes** the taint from any tainted node that has been `Ready` again for at
   least `READY_SETTLE_SECONDS`. This happens **first**, and runs even when the guard
   tripped — putting a recovered node back into service is never the risky half.
5. Taints any node whose `Ready` condition has been **`Unknown`** for longer than its
   class threshold: `THRESHOLD_SECONDS` for workers, `CP_THRESHOLD_SECONDS` for
   control-plane nodes. A failed mutation is a `WARN`, not an abort.
6. Prints a one-line summary and exits 0 (non-zero only if `kubectl` itself fails, or
   on the fatal empty-selector case).

## Control-plane nodes are IN SCOPE (2026-09-12)

They were skipped until 2026-09-12, on the stated grounds that *"tainting a
control-plane node would `NoExecute` the apiserver off it"*. **That was wrong**, and
the skip was leaving real workloads stranded: 15 pods on the masters sit on RWO PVCs,
including `home-assistant`, `esphome`, `music-assistant`, `emqx-core` and `gatus-0`.
Many of them *must* live there — their macvlan attachments need the IoT VLAN on
`eth1`, which only the bare-metal masters have.

Why the old justification does not hold:

- `kube-apiserver`, `kube-scheduler` and `kube-controller-manager` are Talos-supervised
  **static pods**. Their API objects are *mirror* pods. Deleting a mirror pod is a pure
  API operation — kubelet's `HandlePodRemoves` takes the `wasMirror` branch and never
  reaches `deletePod`, then recreates the object from the on-disk manifest.
- The eviction the old note feared **already happens 180s earlier, every time**. The
  default `unreachable:NoExecute` toleration is 300s, so those mirror pods are already
  deleted before this job's window even opens. They hold no PVCs, so the detach half is
  a no-op for them.
- **etcd is not a Kubernetes object on Talos** (machined-manages it; `kubectl get pods
  -A | grep etcd` is empty). Taints are a Kubernetes concept and cannot reach it, so
  quorum, membership and leader election are untouched.

## Guard rails

| Guard | Default | Why |
| --- | --- | --- |
| Only `Ready=Unknown` is actioned, never `Ready=False` | — | **The single most important guard.** Kubernetes encodes the difference deliberately: `Unknown` means "kubelet stopped posting node status" (nobody home), `False` means the kubelet is alive and self-reporting — Cilium down on a master gives `Ready=False` with every container still running and writing. Force-detaching a Ceph RBD image from a live writer is the documented corruption path; upstream's precondition is that the node "is already in shutdown or power off state". Until 2026-09-12 this tested `!= True`, which included `False`. |
| Empty control-plane selector is **fatal** | — | Membership is resolved **server-side** by label selector, because jsonpath cannot tell an absent label from `control-plane`'s empty-string value. If that query ever returns nothing, every master would silently fall into the worker class (shorter window, `NoExecute`). The script logs `FATAL` and exits 1 instead. |
| `MAX_UNREADY` | `1` | More than one node down at once is far likelier a partition or apiserver problem than N dead hosts. Taint nothing; page a human. Counts **unadjudicated** failures only — not-`Ready` *and* not already tainted — so one long-dead node cannot permanently wedge remediation for everything that fails after it. Until 2026-09-12 the control-plane skip ran *before* this counter, so a NotReady master was invisible to it and losing two of three masters did not trip the guard at all. |
| `THRESHOLD_SECONDS` | `480` | Well above the ~5m pod-eviction timeout, so a kubelet restart, a brief network blip or a Talos upgrade reboot never trips it. |
| `CP_THRESHOLD_SECONDS` | `900` | Control-plane nodes get a longer window: a bare-metal master's POST + Talos boot is slower than a VM's, and a false positive costs more. Honestly, this adds delay rather than information — it is the weakest guard here, not the main one. |
| `CP_TAINT_EFFECT` | `NoSchedule` | Both consumers of this taint (`podgc.gcTerminating`, `attachdetach/reconciler.hasOutOfServiceTaint`) match on the **key only** and ignore value and effect, and the upstream docs accept either. `NoSchedule` therefore delivers the whole remedy while avoiding two `NoExecute`-only side effects on a master: evicting the ~11 DaemonSets that tolerate not-ready/unreachable but not this taint (multus, both Ceph CSI nodeplugins, node-exporter, promtail, …), and a mirror-pod delete/recreate loop if the taint ever sticks on a recovered master. **Flag:** it relies on the default 300s `unreachable:NoExecute` to set the `DeletionTimestamp` that podgc needs, so giving a target workload an *infinite* unreachable toleration would silently stop rescuing it. |
| `EXCLUDE_LABEL` | `node-out-of-service.haynesops.com/exclude=true` | Auditable opt-out for a single node. Its empty state is the safe default (nothing excluded), unlike the old hardcoded deny-list whose empty state was fail-open. Treat it as temporary: an excluded node still counts toward `MAX_UNREADY`. |
| Removal pass runs **first** and mutations cannot abort the run | — | `set -e` plus untaint-after-taint meant one failed `kubectl taint` aborted the script before the removal loop, so an unrelated error could leave a fully recovered node tainted indefinitely — contradicting the promise above. Each mutation is now wrapped; a failure is a `WARN` and the next tick retries. |
| `READY_SETTLE_SECONDS` | `60` | A node reports `Ready` slightly before its CSI plugin re-registers; untainting early lets pods land somewhere that cannot yet attach their volumes. |
| RBAC | `nodes: get, list, patch` | No `nodes/status`, no `update`, no access to pods or VolumeAttachments — the built-in controllers do the force-delete and detach; this job only stamps `.spec.taints`. |
| `concurrencyPolicy: Forbid`, `backoffLimit: 0`, `activeDeadlineSeconds: 50` | — | Runs never stack or retry on top of each other; each tick recomputes from scratch, and every `kubectl` call is capped at 15s. |
| No `nodeAffinity` | — | Removed 2026-09-12. Its rationale ("the job exists to repair a dead *worker*") stopped being true once control-plane nodes came into scope, and in practice it pinned every run onto `talosm02`, the node that has silently hard-reset four times. Nothing is lost: the scheduler already refuses NotReady and tainted nodes, and this pod tolerates neither our taint nor an extended not-ready window, so the next tick always lands somewhere healthy and can untaint. |

## Dry run it

The script is plain POSIX `sh` and runs unchanged from a laptop. `DRY_RUN=true` makes it
print the `kubectl taint` it *would* run instead of running it; everything else is
read-only:

```bash
export KUBECONFIG="$(git rev-parse --show-toplevel)/kubeconfig"
S=kubernetes/main/apps/kube-system/node-out-of-service/app/resources/node-out-of-service.sh

DRY_RUN=true "$S"
```

On a healthy cluster that reports a no-op — there is no way to make it *say* it would
taint something while every node is `Ready`, because "not `Ready`" is the trigger, not
the threshold. Mid-incident, `THRESHOLD_SECONDS=0` makes it act the moment a node flips,
which is the useful override to reach for when you already know the host is gone:

```bash
DRY_RUN=true THRESHOLD_SECONDS=0 "$S"     # see the decision without making it
```

All knobs are environment variables with the defaults above: `THRESHOLD_SECONDS`,
`MAX_UNREADY`, `DRY_RUN`, `READY_SETTLE_SECONDS`, `REQUEST_TIMEOUT`, `TAINT_KEY`,
`TAINT_VALUE`, `TAINT_EFFECT`. In-cluster, the first three are set explicitly in
`app/cronjob.yaml` so they can be tuned there without touching the ConfigMap.

## Watch it

```bash
kubectl get cronjob node-out-of-service -n kube-system
kubectl logs -n kube-system -l app.kubernetes.io/name=node-out-of-service --tail=50
```

A healthy quiet cluster logs one line per minute:

```
2026-09-09T12:03:59Z summary: nodes=6 control_plane_skipped=3 not_ready=0 tainted=0 untainted=0 dry_run=false
```

## Disable it

Suspend the CronJob — this is a **temporary, non-GitOps** override; Flux will not undo
it (`suspend` is not in the manifest, so it is not reconciled away), but the next person
to read Git will not know:

```bash
kubectl patch cronjob node-out-of-service -n kube-system -p '{"spec":{"suspend":true}}'
kubectl patch cronjob node-out-of-service -n kube-system -p '{"spec":{"suspend":false}}'   # re-enable
```

To disable it **properly**, remove `- ./node-out-of-service/ks.yaml` from
`kubernetes/main/apps/kube-system/kustomization.yaml` and commit — Flux prunes the whole
app. To neuter it while leaving it deployed, set `DRY_RUN` to `"true"` in
`app/cronjob.yaml`: it keeps logging what it would have done without touching a node.

## Implementation notes

- **Image**: `docker.io/alpine/kubectl` — the kubectl image this repo already uses
  (`frontend/headlamp`). It is alpine-minirootfs + curl + kubectl: **BusyBox ash, no
  bash and no jq**. Hence POSIX `sh` and `kubectl -o jsonpath` rather than `jq`.
- **Timestamps** are converted to epoch seconds in `awk` (a days-from-civil
  calculation), never `date -d`: GNU date wants `-d <ts>`, BusyBox date wants
  `-D <fmt> -d <ts>`, and BSD/macOS date wants `-j -f <fmt>`. The awk version behaves
  identically under gawk, mawk, BusyBox awk and BSD awk, and was unit-tested against
  Python across leap years, century boundaries and garbage input.
- **In-cluster auth is explicit.** kubectl only falls back to the mounted service-account
  token when its merged client config equals the built-in default; `--request-timeout`
  (or any other override) makes it "a real config" pointing at `localhost:8080`, so the
  first three Jobs on 2026-09-09 silently listed zero nodes. The script now writes a
  minimal kubeconfig into `/tmp` that references the mounted `tokenFile`/`ca.crt` and
  exports `KUBECONFIG` — only when no `KUBECONFIG` was supplied, so laptop runs are
  unchanged.
- **Kyverno** runs `pod-security-baseline`, `restrict-image-registries` and
  `restrict-rbac-escalation` in enforce mode. This pod clears baseline with room to
  spare (`runAsNonRoot`, `readOnlyRootFilesystem`, all capabilities dropped,
  `seccompProfile: RuntimeDefault`), `docker.io` is on the registry allowlist, and the
  ClusterRole carries no wildcard or `escalate`/`bind`/`impersonate` verb — so no
  `PolicyException` is needed.
- The script ConfigMap is **hash-suffixed** (the kustomize default, as in
  `observability/dns-canary`), so editing the script mints a new ConfigMap name and the
  next Job picks it up rather than silently reading a swapped file.

## References

- [Non-graceful node shutdown](https://kubernetes.io/docs/concepts/cluster-administration/node-shutdown/#non-graceful-node-shutdown)
- [`node.kubernetes.io/out-of-service` taint](https://kubernetes.io/docs/reference/labels-annotations-taints/#node-kubernetes-io-out-of-service)
