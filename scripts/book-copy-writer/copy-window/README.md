# Fresh COPY2 window preparation

This tracked successor prepares a Stop/inverse/watch pair for the current
haynesnetwork v0.110.5 and signed writer source `7c99b2af`. It grants no runtime
authorization. Do not arm, suspend Flux, merge Stop, create producers or run MAIN
from this README. Root/coordinator must independently review a fresh exact packet
within the owner's authorized scope and grant each live stage separately.

`manifest-contract.json` pins six entire normal and Stop blobs from immutable
ops commit `d782455e8175374f779c64b1e10a53221c9c0928`, the current app/writer image
identities, all five writer modules and current three-byte-path COPY2 scope.
`window_contract.py` reconstructs Stop and checks the entire semantic tree,
unrelated byte preservation, exact changed paths and patch lines. Rebase/image or
manifest drift refuses; a fresh reviewed contract is required. This snapshot
changes no production manifest on the source package branch.

Historical references are immutable: supervisor SHA `647373d36e594cabadf5b82cb8daf143d1fce6455b830b1b9a035c3e9b883c2a`,
v5 watcher SHA `80231a299560c58a3af5b19456f6723b4d19b898ed76af469da9bd0e2f1089fc`,
and copy-aware replay SHA `6095e34a94895b329d2d698ee1e3559c3aab16e360afde36c6517ff1499ca6c4`.
The old default core helper is different. Closed unused PRs #3622/#3623 cannot be
reused as a green inverse or new runtime approval.

The supported order requires an independent watcher armed against exact normal
Git **and actual normal workloads before holds**. A separately authorized stage
then holds exactly frontend/haynesnetwork, media/libretto, downloads/lazylibrarian
and media/kavita. A fresh private `hold_receipt` records before/after full native
Kustomization metadata/spec/status, actual UID/resourceVersion and a completed
`flux-system/cluster-apps` parent reconcile against verified normal main. All four
same-UID holds must remain true afterward. A manual CronJob flag is insufficient.
[Flux suspension documentation](https://fluxcd.io/flux/components/kustomize/kustomizations/#suspending-and-resuming)
describes the API; it does not replace this actual parent-persistence proof.

Desired Stop can be staged on Git main only while those verified holds preserve
normal actual workloads. The copy-aware `retarget-restore.sh` replays the stacked
inverse onto the actual squash main commit, checks all six full blob pairs and
pushes a validation commit to request current-main checks/advisory. Prearm requires
an **open, exact-head, base-main** inverse with every required check and a clean
current normal advisory. Pending checks cannot start Stop clocks or authorize
application of desired Stop. Staging must fit the original 600s live arm ceiling;
otherwise the watcher requests recovery and retains holds until normal is proved.

Prestage failure: restore exact normal main plus actual still-normal workloads
before releasing any hold. Post-Stop: first prove exact restored normal main and
owned writer-first Job/Pod UID plus PG-lease absence; then controlled KS resume
applies restoration. Each resume repeats exact source/absence proof. Terminal
success requires every normal controller and all four resumed KS Ready on the
exact restored SHA. Incomplete recovery stays armed and incomplete. No fallback
releases a hold against paused source, ignores a reused UID, or invents success.

The source supervisor's external caller closure is coordinated with the fresh
`native-pipeline` package. All helper paths/hashes, new phase and role names,
module/image/source pins, initial templates, LIVE artifact, current scope and
immutable before/after proofs must be assembled into a fresh private packet.
The SOURCE-owner outcome caller now sends a canonical immutable request binding
the native completed MAIN receipt, phase/Job/Pod, four runtime modules and exact
three-path scope. Its private response must match the unique SOURCE-owner ready
log event raw SHA and all envelope/core bindings. SOURCE performs the actual SQL
on its owner thread before/after each outcome traversal and retained byte read.
Fresh closure hashes and independent review remain required before execution. The frozen archive and consumed
packets remain untouched. Private data, native capture artifacts, credentials,
journals and signed runtime approval stay outside public git.

Concurrent Git activity is checked from freshly fetched main. An unrelated
commit is retained by inverse replay and recovery uses the latest descendant of
the inverse merge after checking its exact normal six blobs. An unexpected change
inside the six phase manifests permanently revokes that COPY phase, touches Stop,
performs writer-first UID/PG cleanup and records the original refusal durably.
The watcher retains holds and refuses to replay the frozen normal image/settings.
A cold restart preserves revocation; it cannot authorize more writes. Recovery of
such a conflict requires an independently reviewed current-main inverse/contract
that preserves the unrelated change. This unresolved conflict path cannot promise
the 300s service ceiling; the preparation is **not ready for actual Stop** until a
concrete bounded recovery decision is ratified. Activity declarations do not lock
Git or waive this check.

Original clocks remain independent. LIVE collection is 180s / host total 200s.
Consumer byte expiry is earliest original byte capture start +300s. Actual first
LL/Kavita Stop starts the watcher +170s restoration trigger, supervisor +250s
abort, +300s service ceiling and 50s restoration reserve. Final eight-publisher
capture adds start+65s and finish+35s. MAIN's deadline is the minimum of those
applicable clocks, not a new duration after receipt delivery. SOURCE, assembly,
native binding and complete PG-owner/LL/Kavita protection must finish with enough
remaining margin; otherwise refuse and restore. Full-corpus performance remains
unproved until a separately ratified fresh read-only LIVE attempt completes.

Run finite verification serially under `nice -n 19`:

```sh
nice -n 19 python3 -B scripts/book-copy-writer/copy-window/test_window.py
bash -n scripts/book-copy-writer/copy-window/retarget-restore.sh
```

Fixtures use fake clocks, fake gh responses and a private local bare Git remote,
with a public dummy credential; they read no real token and issue no API/runtime
writes. They cover squash replay/idempotence, whole-blob drift, strict typed raw
inventory, completed parent hold proof, pre-/post-Stop cleanup ordering, pending
inverse gates, source/cleanup refusal and original restoration/publisher clocks.
Never run CPU stress, busy loops, wide parallel or repeated load tests here.
