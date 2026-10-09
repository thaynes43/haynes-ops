# Cached Stop source preparation — 2026-10-09

This successor augments the reviewed COPY window package. It is preparation only:
no GitRepository or Kustomization hold, Stop merge/apply, Job or library mutation
is authorized by this report. The earlier window package cannot safely depend on
fresh Git review during its active Stop window after same-file repository drift.

The coordinator ratified the following sequence for source/code preparation:

1. Arm cold recovery while remote main and actual workloads are Normal. Establish
   and drain the four application Kustomization holds. Fresh requested reconciles
   of `cluster-apps` and `cluster` must preserve all same-UID holds.
2. Merge reviewed Stop under those holds and fetch its exact artifact. Suspend
   the same GitRepository with this phase's ownership annotation and a new
   `reconcile.fluxcd.io/requestedAt` token. Its matching handled token drains
   earlier same-object handlers; a pre-suspend handler cannot acknowledge the new
   token from a later object snapshot. Recheck actual artifact bytes/digest and
   source-controller Pod UID, both parents, and all four holds.
3. Replay, review and merge the exact inverse while services remain Normal.
   Remote main must already be a Normal inverse descendant before actual Stop.
   The sealed cache receipt binds the Stop artifact, source/controller identities,
   drained source token, both parent tokens, phase and inverse merge SHA.
4. A fresh LIVE artifact, original ACK/expiry/cleanup and a separate runtime GO are
   still required. The supervisor checks the sealed cache and merged inverse and
   publishes an immutable activation-ready record only after complete arming.
   Its durable timestamp is a conservative budget origin recorded before the
   first application release, distinct from an observed actual Stop. Both watcher
   and supervisor use the earlier applicable origin; a crash before observing
   Stop cannot fall back to the 600s staging arm clock.
   Application holds may then be released against the cached Stop artifact.
5. Failure or the original restore trigger permanently revokes COPY. Retire owned
   Jobs/Pods and PG leases before releasing the Git source. Fetch the latest
   reviewed Normal inverse descendant, then resume/reconcile the application
   Kustomizations. Prove actual desired Normal controllers and Flux convergence
   before terminal success. Later image/settings changes on the same six paths
   are honored from current main; recovery never rewrites old manifest blobs.

Pre-stage cancellation requires restored Normal remote main and actual
still-Normal workloads before releasing holds. After actual Stop, Normal Git and
owned-writer/PG absence precede controlled source/application release; actual
Normal follows application restoration. Unexpected hold loss before the bound
activation record, source/controller replacement, changed artifact or unknown
intent refuses COPY. Cold restart retains original clocks and ownership.
Missing activation budget with actual/partial Stop immediately revokes and
restores with the origin truthfully unknown. The active cached path uses the
previously accepted merged-inverse identity, without new GitHub comments/checks
queries. Due restoration revokes and retires writers before attempting Git
availability. Actual Normal includes the converter's current job template,
including `STRIP_SERIES_METADATA=0` and the Ransom hold; Git intent alone cannot
prove those flags applied.

This removes CI and advisory review from the active Stop path. It does not remove
availability dependencies: source-controller uses `emptyDir`; an eviction can
lose artifact bytes while suspended status still says Ready. The watcher must
resume the source against already-Normal main to recover. Source/API outage or
unproved writer cleanup is truthfully incomplete recovery, never grounds to
extend the service ceiling. Runtime prerequisite rehearsal must prove real
hold/drain/parent persistence and Normal restoration timing while services stay
Normal. No rehearsal has run.

All original bounds remain: LIVE 180s / host 200s, earliest original byte capture
+300s, restore request no later than the earlier of pre-release budget or actual
Stop +170s, SOURCE/host abort +250s and service
ceiling +300s (50s restoration reserve), publisher start +65s / finish +35s.
Global source hold requires declared cluster activity with TTL at most two hours.

Read-only feasibility evidence: OPERATOR can patch the GitRepository; actual
source/kustomize controllers are v1.9.6. Source and all four application
Kustomizations omit `spec.suspend` in Git. `cluster` owns the source and
`cluster-apps` owns those application Kustomizations. No holds were changed.
The actual artifact URL uses `source-controller.flux-system.svc.cluster.local.`;
Service TCP80 maps to Pod TCP9090. The exact DNS/backend policy in
[#3666](https://github.com/thaynes43/haynes-ops/pull/3666) merged at `83a29816` and
deployed Ready on that revision. One direct read of the current Normal artifact
passed with 1,838,604 bytes and SHA-256
`5da78ef85a46db1147268804fe186951b51f569ce4e88ffa0950ccc56efbb1a8`.
Native source UID/spec/artifact and controller UID/spec/image/restart observations
bracketed the read without drift; policy RV was `819262437`. The private proof
receipt SHA is `fa04dd57a30d26bcc2a0d6b56e3ba7ec0f2a929cc7c811592a04b57eb2d322e4`.
This proves direct Normal artifact reachability, not suspended hold persistence.
No alternate egress path, hold change, Job or library write occurred.

Primary sources:

- [GitRepository suspension and artifact-loss behavior](https://fluxcd.io/flux/components/source/gitrepositories/#suspend).
- [v1.9.6 source handler](https://github.com/fluxcd/source-controller/blob/v1.9.6/internal/controller/gitrepository_controller.go)
  installs deferred request recording and returns before storage/fetch when suspended.
- [Deferred request recording](https://github.com/fluxcd/source-controller/blob/v1.9.6/internal/reconcile/summarize/processor.go)
  and [summary patch ordering](https://github.com/fluxcd/source-controller/blob/v1.9.6/internal/reconcile/summarize/summary.go).
- [Exact runtime patch helper](https://github.com/fluxcd/pkg/blob/runtime/v0.110.3/runtime/patch/patch.go)
  patches copies and does not replace the handler's annotation snapshot.
- [Same-object handler serialization](https://github.com/kubernetes-sigs/controller-runtime/blob/v0.24.1/pkg/internal/controller/controller.go).
- [v1.9.6 kustomize handler](https://github.com/fluxcd/kustomize-controller/blob/v1.9.6/internal/controller/kustomization_controller.go)
  fetches an existing Git source artifact without requiring that source to be unsuspended.

Source validation: final combined 46/46 finite tests (21 new +25 legacy) passed in
6.791s under the dedicated hash-pinned host venv and `nice -n 19`; diff-check was
clean. Independent review remains required before merge.
Fixtures cover raw native UID/RV/token drain, parent hold persistence,
artifact/controller loss, full manifest/cap drift, original clocks/cold recovery,
partial Stop before checkpoint, Git outage after revocation, no active advisory
queries and actual converter drift. A finite blocked-child fixture proves the
owning cache check is capped at five seconds with kill/reap. The seal has one
30s wall cap, nested caps preserve it, and curl separately caps time/body/output.
Private receipts use exclusive, fsynced no-replace publication. No test reads
credentials, changes native holds or creates a Job.
