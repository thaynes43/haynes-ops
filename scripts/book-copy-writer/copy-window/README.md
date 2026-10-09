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
pushes a validation commit to request current-main checks/advisory. Before Stop
application the same GitRepository is held and drained with a fresh requested
token, its actual Stop artifact is byte-verified, and both parent Kustomizations
are actually held and fresh-handler-drained. Their pre-hold proof must be Normal
and Ready; their held proof binds same UID/spec/phase/token, without claiming
observedGeneration advanced or Stop was applied by a held parent. The inverse then receives current
required checks and an advisory explicitly reporting no findings and is **merged
to Normal main while actual services remain Normal**. A favorable verdict with
an unresolved severity finding refuses. Pending checks cannot start Stop clocks.
Staging fits the original 600s arm ceiling; a separate runtime GO is required.

`seal-cached-source.py` performs read-only Git/native verification of the private
draft receipt and publishes one immutable 0600 seal. Its JSON binds phase, Stop
SHA, PR pair/inverse head and merge SHA, before/after source and all four
Kustomizations, fresh handled request tokens, both actual parent holds and the
source-controller Pod. It never suspends resources or creates Jobs. The whole
seal is capped at 30s. Each bracketed actual cache check has an owning five-second
wall cap, bounds curl's body to 8MiB (expanded archive 64MiB) and reaps its child on
failure. Artifact bytes are parsed in memory; nothing is extracted.

Future supervisor config must include `cached_source_receipt` as an absolute
private `{path, sha256}` descriptor and `cached_source_activation` as an initially
absent absolute path beside the private watch state. Start the watcher with both
`--cached-source-receipt` and `--cached-source-activation`. These fields augment
the original phase/helper/hold/contract closure; no existing consumed config can
be restamped. Direct source-controller HTTP requires the separately reviewed
exact DNS/backend policy in #3666. Freeze host Python/YAML plus curl/git/gh/Flux/
kubectl executable identities in the fresh packet; no unknown host closure is
approved by this source package.

Only after complete independent arming does the supervisor atomically publish
activation-ready with the accepted cache/inverse hashes and a durable conservative
`actuation_budget_started_at` before first app release. This is an earlier budget
origin, not an invented actual Stop observation. Missing origin with actual or
partial Stop revokes immediately; restoration requires the accepted Normal
inverse described below. Cold recovery retains the original
origin, even if the supervisor dies between release and its first Stop checkpoint.
If a staging hold unexpectedly drops before the inverse is merged, native Stop
detection revokes and retires writers before retarget/CI/advisory. The watcher
reads only bounded merge metadata afterward. An open inverse retains remaining
holds and truthfully reports Normal restoration unproved; that unclocked failure
cannot promise automatic recovery within 300s. It never waits 600s to revoke.
The five-second cache wall cap does not shorten the existing still-Normal inverse
retarget or Flux restoration commands.

Prestage failure: restore exact normal main plus actual still-normal workloads
before releasing any hold. Post-Stop: first prove current restored Normal inverse-descendant main and
owned writer-first Job/Pod UID plus PG-lease absence; then controlled KS resume
applies restoration. The phase-owned Git source resumes against that current
Normal first. Each app/parent release repeats source/absence proof and uses a
recorded UID/spec/phase plus RV-tested patch. App holds release first, followed by
`cluster-apps` and `cluster`. A foreign/replaced/changed hold refuses release.
Terminal success requires every normal controller and all six resumed KS Ready on the
exact restored SHA. Incomplete recovery stays armed and incomplete. No fallback
releases a hold against paused source, ignores a reused UID, or invents success.
Only after all actual Normal convergence is proved does recovery remove its own
phase annotation from the seven resumed resources, with same UID/spec/RV tests.
Other controller annotations and fresh reconcile requests remain. A foreign phase
annotation refuses retirement. This lets a later fresh phase establish ownership.

`normal-rehearsal.py` prepares and executes a separate Normal-only cancellation
rehearsal. Preparation copies the generic source closure and a reviewed exact
host Python/PyYAML dependency receipt into a fresh private directory; it performs
no API calls. Use the dedicated hash-pinned Python 3.11.2/PyYAML 6.0.3 venv and
isolated mode (`-I -B`). A replacement host receipt must be independently reviewed
and supplied with its exact SHA; never silently use globally installed packages.

The runtime interface is two separately owned processes, both requiring the
exact closed packet SHA as `--go`: first `--watch`, then `--exercise --ready` with
the watcher's private PID/start-ticks-bound readiness receipt. Recovery is armed
before any hold. Exercise holds/drains `cluster`, `cluster-apps`, the four apps,
then Source, proves real Normal archive bytes against Normal Git, and always
requests cancellation. It never invokes the Stop sealer, activation or producers.
The observer does not rewrite recovery state. No runtime is authorized by these
source instructions; the coordinator must review the fresh packet and declare
activity immediately before giving a separate exact runtime GO.

Before any hold, exercise registers its dedicated process group and PID/start
ticks under a private local registration lock. Cancellation closes registration.
Recovery retires that exact group, then applies UID/spec/phase/RV-tested metadata
barriers on all seven resources before any release. Submitted hold patches test
the earlier RV and therefore cannot re-hold a resource after its barrier. Killing
a client alone is not server-request retirement. Captured members are signaled
through stable pidfds after PID/birth/group checks, never through a numeric
group kill. Unknown identity or foreign ownership refuses release truthfully.
Once the original group
is empty, a private immutable packet/owner-SHA retirement receipt permits cold
retries without inspecting a later reused PID. An empty original group before
the first proof is also safe; another group's reused PID is never killed.
Linux retains the number while the original PGID has members, so a different
leader birth proves retirement and a missing leader leaves identifiable original
descendants. The immutable receipt records which proof established retirement.

Rehearsal operational caps are exercise 90s, staging arm 120s and recovery 50s
from the first recovery attempt. The watcher uses its existing 10s poll interval;
actual cancellation detection and total restoration timing must also be recorded.
An in-budget historical proof survives cold re-verification. A miss
is retained truthfully while recovery continues in bounded 60s safety attempts
with the original 10s retry wait. These are Normal-only operational measurements,
not new Stop/COPY clocks or a proof of the actual service ceiling. SIGTERM/INT
request recovery rather than abandoning holds. Actual API/source outage or
unknown ownership remains incomplete; a killed pod still needs cold recovery.

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

The accepted cached active path performs no new GitHub advisory/CI/merge requests.
It restores from freshly fetched current Normal main descending from the accepted
merged inverse. An unrelated commit is retained. Reviewed newer images, replica
counts and settings on the same six paths are honored at restoration; no old blobs
are reset. Unsafe current intent (Stop, strip enabled, Ransom hold removed or
acquisition off) refuses terminal Normal. Native proof includes actual current
converter template flags and current desired controller images/replicas. Source/
API outage, lost artifact or unproved writer/PG cleanup remains truthfully
incomplete recovery. It never extends the 300s ceiling. Actual still-Normal
hold/drain/parent persistence and restoration-budget rehearsal remains required;
this package is **not actual Stop-ready** from finite fixtures alone.

The entire cached active/restoration tick has one owning original
`min(pre-release budget, actual Stop) +300s` deadline. It includes Git fetch and
all sequential Flux operations; individually valid command caps cannot extend
the sum. The control exception bypasses ordinary cache-proof failure fallbacks,
and nested five-second guards retain the outer remaining timer. Cold state binds
the immutable origin before commands. An expired prior origin caps recovery
before any new clock-proof read; no new duration is minted.

A missed ceiling permanently revokes COPY and records `complete=false` plus the
original miss. Cleanup-only safety restoration continues in bounded 60s attempts
with the existing 10s retry wait. Each attempt proves owned writer/PG retirement
and current Normal Git before source/app release. It cannot produce Jobs, ACKs,
leases, replay or new window approval. Actual later Normal is recorded as
`safety_recovery_complete`, never a claim that the 300s guarantee was met. Unknown
ownership or Normal intent conflict still refuses. A historical success recorded
before the original ceiling is preserved on later cold rechecks; fresh bounded
convergence verification closes authority without rewriting its completed time.
Invalid persisted clocks or failed fresh activation/clock proof enter the same
bounded safety path with `original_clock_unproved_at`, preserving the original
values and permanently revoked COPY. They do not assert a measured deadline miss
or strand owned holds solely because clock validation failed. Exact phase/UID/PG
ownership and current Normal still precede every source/app release.

Original clocks remain independent. LIVE collection is 180s / host total 200s.
Consumer byte expiry is earliest original byte capture start +300s. Actual first
LL/Kavita Stop starts the watcher +170s restoration trigger, supervisor +250s
abort, +300s service ceiling and 50s restoration reserve. Final eight-publisher
capture adds start+65s and finish+35s. MAIN's deadline is the minimum of those
applicable clocks, also clamped by the earlier immutable pre-release budget
origin. The watcher requests restore no later than that earlier origin +170s;
it never waits for new advisory calls before revocation/writer cleanup. SOURCE, assembly,
native binding and complete PG-owner/LL/Kavita protection must finish with enough
remaining margin; otherwise refuse and restore. Full-corpus performance remains
unproved until a separately ratified fresh read-only LIVE attempt completes.

The host Python/YAML dependency must be captured in a task-private venv;
do not adopt an unknown global module at execution. `requirements-ci.txt` is the
binary-only shared PyYAML6.0.3 pin for host CPython3.11 and CI3.12 (also listed
3.13/3.14 wheel hashes). The authoritative wheel digest is available from
[PyPI metadata](https://pypi.org/pypi/PyYAML/6.0.3/json). Prepare it with
`python3 -m venv /absolute/private/host-venv` and that venv's `python -m pip install
--only-binary=:all: --require-hashes -r requirements-ci.txt`. A future packet must
bind its interpreter/version/module closure and exact PATH so supervisor,
watcher, replay and assembly use the same approved dependency. This source PR
has not frozen that execution environment or granted runtime approval.

Run finite verification serially under `nice -n 19`:

```sh
nice -n 19 python3 -B scripts/book-copy-writer/copy-window/test_window.py
nice -n 19 python3 -B scripts/book-copy-writer/copy-window/test_cached_source.py
bash -n scripts/book-copy-writer/copy-window/retarget-restore.sh
```

Fixtures use fake clocks, fake gh responses and a private local bare Git remote,
with a public dummy credential; they read no real token and issue no API/runtime
writes. They cover squash replay/idempotence, whole-blob drift, strict typed raw
inventory, completed parent hold proof, pre-/post-Stop cleanup ordering, pending
inverse gates, source/cleanup refusal and original restoration/publisher clocks.
Never run CPU stress, busy loops, wide parallel or repeated load tests here.
