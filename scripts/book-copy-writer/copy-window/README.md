# Fresh COPY2 window preparation

This tracked successor prepares a Stop/inverse/watch pair for the current
haynesnetwork v0.110.5. The retained image/source `7c99b2af` is historical;
the prospective byte lifetime requires a newly published signed runtime and
exact registry module closure before a packet can qualify it. This source
grants no runtime authorization. Do not arm, suspend Flux, merge Stop, create producers or run MAIN
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

For **cached-source COPY**, the supported order requires an independent watcher armed against exact normal
Git **and actual normal workloads before holds**. A separately authorized stage
first holds and fresh-handler-drains `flux-system/cluster`, then
`flux-system/cluster-apps`, each against fresh Ready Normal proof. The higher
parent must be drained before holding its child. Only then hold exactly
frontend/haynesnetwork, media/libretto, downloads/lazylibrarian and media/kavita.
A fresh private `hold_receipt` records before/after full native Kustomization
metadata/spec/status, actual UID/resourceVersion and same-phase parent containment.
Do not reconcile a live parent over held children: its Git Normal apply removes
runtime suspend and ownership fields. All six same-UID holds must remain true
through fresh final checks. A manual CronJob flag is insufficient.
[Flux suspension documentation](https://fluxcd.io/flux/components/kustomize/kustomizations/#suspending-and-resuming)
describes the API; it does not replace this actual parent containment proof.

The retained legacy non-cached verifier is separate: it requires an unsuspended
`cluster-apps` parent to finish a fresh reconcile while four app holds persist,
with a `hold_receipt` younger than 600s. Its staging cap remains 600s. That action
failed actual GitOps persistence in v4, so this legacy route is not approved for
new COPY staging; use the cached-source sequence above. Its predicates remain
unchanged, and cached parent containment cannot substitute for its receipt.

Desired Stop can be staged on Git main only while those verified holds preserve
normal actual workloads. The copy-aware `retarget-restore.sh` replays the stacked
inverse onto the actual squash main commit, checks all six full blob pairs and
pushes a validation commit to request current-main checks/advisory. Before Stop
application the same GitRepository is held and drained with a fresh requested
token, its actual Stop artifact is byte-verified, and both parent Kustomizations
are actually held and fresh-handler-drained. Their pre-hold proof must be Normal
and Ready; their held proof binds same UID/spec/phase/token, without claiming
observedGeneration advanced or Stop was applied by a held parent. The inverse then receives current
required checks and a disposed advisory and is **merged
to Normal main while actual services remain Normal**. A favorable verdict with
an unresolved severity finding refuses. Pending checks cannot start Stop clocks.
The default live-workload staging ceiling remains 600s. A fresh, explicitly
reviewed **cached-source** attempt may select `--arm-deadline` up to 1800s for this still-Normal
staging only: inverse retarget, required checks/advisory, sealing and fresh LIVE.
Allow the complete 260s LIVE host bound plus 30s bind reserve before its original
arm expiry. This does not reset or extend an already armed attempt, authorize
Stop/COPY, or guarantee CI will finish within the selected ceiling. A separate
runtime GO is required. After the conservative pre-release origin or actual Stop,
the 170s restore trigger and 300s service ceiling remain fixed. The prospective
restoration reserve is 130s (170 + 130 = 300); its actual cold timing remains
unproved. The staging ceiling cannot replace these clocks. Manual byte evidence
expires 600s after its original earliest capture, with activation admitted only
while that original capture is at most 300s old. The supervisor rechecks that
age after arming immediately before publishing the conservative activation
origin. Generic metadata freshness stays 300s. Normal rehearsal retains its
separate historical 120s arm and 50s recovery budget.
Above 600s, both cached receipt and activation paths must be configured; a
non-cached watcher or supervisor cannot adopt the larger staging allowance.

The standing owner policy makes Claude advisory, never a required check. The
normal path still reads a successful current-head Claude review explicitly
reporting no findings. A completed current-head Claude startup failure can instead
use an immutable private `review_disposition` descriptor, explicitly ratified by
root after an independent Codex review. This records the failed review truthfully;
it does not invent a successful Claude result or accept pending/unknown reviews.
All other check, exact main/inverse, source, phase, UID, lease and clock guards
remain unchanged. This optional disposition supplies no runtime GO itself.

The schema-1 disposition has `prepared_only: false`, `explicitRootApproval: true`,
`bindings` (exact `source_commit`, `stop_pr`, `stop_head`, `inverse_pr`,
`inverse_head`, `phase_token`), the exact failed `advisory` check fields, and
`independent_review: {path, sha256}`. Its separate immutable review receipt has
the same bindings, `provider: codex`, distinct nonempty `prepared_by` and
`reviewed_by`, `decision: PASS`, `unresolved_findings: []`, and all five
`source_files` hashes (supervisor, watcher, contract, cache and manifest contract).
Root reviews the actual startup evidence and independent findings before setting
approval. Those hashes must match both the running files and the exact source
commit. Failed non-advisory checks, stale heads, findings, source drift, missing
approval or receipt drift refuse. Prepared examples retain approval false.

The supervisor's optional `review_operation` binds `source_commit`, `stop_pr`
and `stop_head`; phase and inverse identities come from its existing state/config.
The watcher receives the same descriptor SHA with `--review-disposition` and
`--review-disposition-sha256`, plus `--review-source-commit`; its existing Stop
arguments and live inverse head complete the bindings. A reviewer must freeze a
new exact-head receipt after inverse retargeting. Active cached recovery never
queries or reopens advisory review, preserving the original restoration budget.

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
origin, not an invented actual Stop observation. The sole cached watcher then
applies Stop once: for each of the four app Kustomizations it proves the complete
phase Job/Pod union and both primary PG owners absent, verifies the held Source
artifact and both parents, resumes the exact owned app, reconciles Stop, and
reholds/drains that app. Source and both parents remain held. The first observed
service Stop is recorded while later scopes are still applying; producers wait
for all four exact Stop revisions and rehold receipts bound to the same activation.
A partial actuation, changed custody, or original deadline triggers existing
writer-first Normal recovery. This fixes activation previously waiting for Stop
while every app controller remained held; it changes no byte or service budget.
Missing origin with actual or
partial Stop revokes immediately; restoration requires the accepted Normal
inverse described below. Cold recovery retains the original
origin, even if the supervisor dies between release and its first Stop checkpoint.
If a staging hold unexpectedly drops before the inverse is merged, native Stop
detection revokes and retires writers before retarget/CI/advisory. The watcher
reads only bounded merge metadata afterward. An open inverse retains remaining
holds and truthfully reports Normal restoration unproved; that unclocked failure
cannot promise automatic recovery within 300s. It never waits for staging expiry to revoke.
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

Generic COPY recovery keeps a fresh complete typed Job/Pod phase union and both
primary PG lease checks before every controller release and at final proof. STOP
does not prove a submitted CREATE cannot complete late. When that complete union
is empty, recovery skips the fifteen duplicate per-intent inventory reads; any
observed resource still takes the existing exact UID/phase cleanup and final
absence proof. Malformed inventory and PG errors refuse release. The Source is
explicitly reconciled once per recovery attempt, then freshly checked for its
owned UID/spec/phase, unsuspended state and exact Ready Normal artifact before
each resume/reconcile. All six needed Kustomization reconciles remain. The
original 50s restoration reserve, 60s safety attempt and 300s service ceiling do
not change. The current generic services-running Normal rehearsal completed in
28.306327s against its original 50s reserve. The earlier 28.363524s result used
Normal-only callbacks. Neither proves an actual COPY Stop can restore cold
services within 50s; exact results and withdrawn stage receipts are recorded in
`.agents/reports/book-copy-recovery-and-staging-2026-10-10.md`.
Cached recovery requests Normal for all four owned app Kustomizations before
waiting for any app to become Ready. Every release/request still requires the
complete phase Job/Pod union and both primary PG owners absent, a fresh exact
Normal Source, and the owned UID/resourceVersion/full spec/phase guard. The
request is an atomic reconcile annotation patch, not a blocking Flux CLI wait.
Fresh waits require current integer generation, matching status and Ready
condition observed generations, exact Normal revision and unchanged ownership.
Both parents remain held until all four app checks pass; their existing restore
order, final complete absence/runtime proof and all original clocks remain.

The actual v11 cold restoration took 127.249103s against the original 50s
reserve. Its serial path did not request Kavita until about 94s after recovery
began; LazyLibrarian's successful controller health check took 30.122032081s,
beyond the forced CLI's 30s timeout. This change removes that serial delay; it
does not claim a new cold timing PASS. The historical 28.306327s services-running
Normal result remains historical because this recovery control flow changed.

The current Normal harness inherits these generic recovery callbacks without
the earlier Normal-only inventory/source/reconcile shortcuts. Finite source
controls do not replace timing proof: the next separately authorized actual
stopped-service operation must prove the original recovery reserve on this changed
path. No prior Normal result is a current cold-restoration certificate.

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
leader birth proves retirement. A missing leader with members requires prior
owner-bound custody captured while the original leader birth was proved on both
sides of the snapshot; an already exited replacement leader is otherwise
indistinguishable. Only captured identities are signaled. The immutable receipt
records which proof established retirement. A fresh snapshot bracketed by that
same original leader birth may add immutable per-member custody supplements
before any signal. The original custody is never overwritten, and at most 64
identities may be captured. With a missing leader, only previously captured
identities are admitted; an unknown survivor still refuses restoration.
The proved original leader is stopped through its captured pidfd after custody
is durable, with actual birth/group and T/t state required. Captured nonleaders
are likewise stopped and retired within the same two-second retirement budget.
The stopped leader retains the group number during fresh custody rounds and is
killed last only after no nonleaders remain. Empty-group proof still follows;
unknown survivors or an external identity/state change refuse. These signals
apply only to the dedicated exercise processes, never a service or native Job.

Rehearsal operational caps are exercise 90s, staging arm 120s and recovery 50s
from the first recovery attempt. The watcher uses its existing 10s poll interval;
actual cancellation detection and total restoration timing must also be recorded.
Forced Kustomization reconciliation is unnecessary only after a fresh owned
UID/spec/phase check proves it unsuspended, with positive integer generation,
matching integer status and Ready-condition observed generations, Ready=True,
and the exact current Normal `lastAppliedRevision`. Missing or stale evidence
keeps the actual reconcile. Fresh Source verification, each release's complete
phase union/PG proof and final runtime proof remain required. Rehearsal Flux
timings retain only the fixed owned scope, never command arguments or output.
The first generic services-running rehearsal on 2026-10-10 missed its original
50s budget and completed bounded safety cleanup; its final `cluster` forced
reconcile waited 26.981s despite two retained controller successes at 02:01:56Z.
The CLI request-wait cause is unproved. This strict Ready guard needs a fresh
actual rehearsal; services-running timing does not prove cold Stop restoration.
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
hold/drain/parent containment and restoration-budget rehearsal remains required;
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

The prospective LIVE collector/Job is bounded at 240s and its host at 260s,
including cleanup. These limits respond to the measured V12 153.754472s byte
stage and V11 39.645623s post-byte tail (209.380831s projected total, not an
actual completion). They do not change any prior attempt: V11 cold restoration
127.249103s failed its original 50s reserve; V12 staging restoration
50.427959s also exceeded 50s and did not exercise stopped services. The new
130s cold reserve is prospective, with no timing PASS or guarantee. Publisher
65s/35s and health 12s caps remain fixed. SOURCE and MAIN abort by the original
conservative activation + 170s, reserving 130s inside the service 300s ceiling.

Changing byte reuse modifies the signed `bound_census.py` runtime module. A
fresh packet must bind the actually published signed image and registry module
closure before using the prospective contract; the prior signed image cannot
be represented as containing the new byte lifetime. No source change or test
authorizes another phase, service Stop, collection or writer.
