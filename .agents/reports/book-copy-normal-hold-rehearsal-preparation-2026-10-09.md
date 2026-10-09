# Normal-only hold rehearsal preparation — 2026-10-09

Preparation only. No hold, Stop merge, activation, producer, COPY or perishable
baseline is authorized. A separately reviewed exact closed packet and coordinator
GO are required before runtime. The coordinator ratified this narrow containment
correction after #3667: both parent Kustomizations must actually be suspended and
drained, as well as the four applications. Unsuspended parent readiness does not
prove that a parent cannot overwrite a child hold.

Before any hold, an independently owned recovery process must be armed against
current Normal main, actual Normal services, and a fresh closed phase with no
Jobs, PG owners, leases or COPY authority. Preserve the same source/controller
and all six Kustomization identities. Both parents must initially be Normal and
Ready on the verified main revision. Hold/drain `cluster`, then `cluster-apps`,
then the four apps, with fresh requested tokens and same UID/spec/phase. A held
handler acknowledgement does not claim observedGeneration advanced or Stop was
applied. The Source is held and drained after containment; its current Normal
archive is fetched and compared with actual Normal Git bytes using bounded
archive helpers. It is not passed through the Stop-only sealer.

Cancellation must retire the exact phase/name/UID/owner union and PG ownership,
prove current Normal Git and fetch it through the same owned Source, then release
each owned app and parent with UID/RV-tested JSON patches. A replacement object,
foreign hold or changed Kustomization spec refuses release. The parent holds stay
until their children have been restored. Terminal proof requires all six
Kustomizations resumed, Ready and applied on current Normal, plus actual Normal
controllers, STRIP=0, the Ransom hold and acquisition on.

Before the first suspend, register the exercise's dedicated process group with
the immutable packet/phase and actual PID/start ticks under a local registration
lock. Recovery cancels further registration and retires that exact group before
issuing any restore. Captured members are signaled through stable pidfds after
PID/start-ticks/group validation; no numeric process-group kill is used. Unknown
identity refuses release.
After retirement, UID/spec/phase/RV-tested annotation barriers on all seven
resources invalidate each previously submitted hold patch's old RV. A delayed
patch either committed before its barrier and is restored, or refuses its stale
RV afterward. This closes the arm-expiry race where recovery could finish while
the exercise retained request authority. It does not claim that killing a client
retires a server request. No new exercise may be registered after cancellation.
Persist immutable packet/phase/owner-SHA-bound retirement proof as soon as the
original group is empty. Cold retries use that proof rather than re-adopting a
later process with a reused PID. Before the first proof, a full empty original
group permits retirement even if the old PID was reused outside that group.
[Linux 6.18 PID allocation](https://github.com/torvalds/linux/blob/v6.18/kernel/pid.c#L329-L349)
retains an ID while any PID/PGID/SID member references it; allocation releases
the ID only afterward. Thus a different leader birth proves original retirement,
including a replacement leading a new group. It is never signaled. A missing
leader with members is ambiguous: a replacement group may also have lost its
leader. Require private owner-bound custody captured while the original leader
birth was proved before/after the member snapshot, then signal only those
captured identities. While that original leader still brackets a fresh snapshot,
new members receive immutable per-member custody supplements before any signal,
with at most 64 captured identities in total. Original custody is never replaced.
A missing leader permits only prior custody; unknown survivors refuse.
The coordinator ratified a further narrow owning-process signal correction:
persist custody, pidfd-stop the original leader and prove its birth/group/T/t
state, then stop/retire captured nonleaders while that leader retains the group
number. The leader is killed last after no nonleaders remain, followed by full
empty-group proof. The original 64-identity, 2s retirement and 50s restoration
bounds remain. Non-atomic snapshots and external process loss still can refuse;
no universal fork exclusion, service signal or new runtime authority is claimed.
[Python's pidfd signal API](https://docs.python.org/3.11/library/signal.html#signal.pidfd_send_signal)
targets the captured process handle across the final numeric PID reuse gap.

The rehearsal has no Stop clocks or COPY lease. Its closed operational bounds are
exercise 90s, staging arm 120s, recovery 50s from the first recovery attempt;
the existing 10s watcher poll latency and total cancellation-to-restoration time
must be measured separately. Bounded 60s safety
attempts and 10s waits continue after an honestly recorded miss. Its independent
recovery process must be armed before exercise; it must report restoration
timing honestly rather than treating Normal-only success as the actual Stop
300-second guarantee. Failure requests cancellation; API/source outage or unknown
ownership remains unproved recovery. Bounded idempotent cleanup must continue
without producers, ACKs, new leases or resetting original clocks. Declare honest
cluster activity immediately before an independently authorized runtime attempt,
and end it after restoration.

The sibling cached recovery now needs the same owned releases and held-parent
contract. Cached Stop remains proved by exact frozen Source/controller identity
and archive bytes; held parents retain their prior Normal revision. The LIVE
180/200, byte-age 300, restore trigger 170, Source 250 plus reserve 50 and publisher
65/35 clocks remain unchanged.

After actual Normal convergence, same UID/spec/RV/phase tests retire only the
owned phase annotation on all seven resumed resources. Other annotations and
reconcile tokens remain. This fixes the finite successful-recovery→fresh-phase
boundary; no foreign phase can be erased. A cold recheck preserves historical
in-budget Normal proof and cannot manufacture a new rehearsal clock or COPY.

Finite source verification passed serially under `nice -n 19`: 25 legacy cases
in 6.294s, 38 cached cases in 0.916s and eight Normal-only cases in 0.148s. The
real recovery release method proves UID/RV-tested patch use and rejects replaced
objects with zero writes. Normal-only fixtures prove seven holds/drains and real
Normal byte comparison, cancellation after lost patch replies/PG refusal, no
observer state writes, PID-reuse refusal, prior-phase annotation retirement,
nonblocking aggregate output and original cold budget/historical proof. This is
source validation; no real hold/drain/recovery timing has been measured.

The request-retirement correction adds four focused controls. All twelve
Normal-only cases passed in 0.237s; the strengthened real descendant-group and
delayed server-RV controls passed in 0.123s, serially under `nice -n 19`.
They cover cancellation before registration, exact owner/request descendant
retirement and PID replacement refusal, retirement→barrier→restore ordering,
all seven RV barriers and stale hold/foreign-owner refusal. No production API,
hold or Job was used.

Independent review of exact `b2c3336c` passed source preparation, receipt
`275f538a8fbb7c0d6fb120e42c51a71d87b139357061d73603e60bf1d8ff3f8b`.
It pins nine source/doc/test/CI blobs and independently passed twelve Normal
cases in 0.273s plus four parent/release/retirement controls in 0.040s, serially
under `nice -n 19`. Advisory comment `6088820407`, updated
2026-10-09 20:52:55Z, explicitly reviewed that commit and reported no findings.
The writer-image validation failed before tests on the unchanged Docker Hub
base digest's HTTP 429; source/window checks passed. This report-only commit
records that review without changing its executable files. The subsequent
`d2aea652` advisory found a real cold retry gap (`4234474264`): checking a PID
again after group retirement could strand holds if that PID was later reused.
The successor persists immutable owner-bound full zero-group proof and reuses
it on retries. Two new cold-proof/binding controls plus actual group retirement
passed in 0.180s, serially under `nice -n 19`. The older source PASS is superseded
pending independent successor review. Its `737feeb4` successor then exposed
the replacement-as-group-leader/orphan cases (`4234498865`). The final correction
uses the verified Linux allocation rule and per-member pidfds, with finite
replacement-at-signal, replacement-before-validation, orphan and actual
descendant controls. Runtime containment and restoration proof remains absent.
The complete eighteen-case Normal suite passed in 0.365s under serial
`nice -n 19`; the five targeted signal/replacement/orphan/actual-descendant
controls passed in 0.154s. No runtime API or service hold was used.
The coordinator's final review added the ambiguous missing-leader case: durable
captured-member custody, prior-captured orphan retirement and an exited
replacement group's refusal. Those three controls passed in 0.126s serially
under `nice -n 19`, with no numeric group signal or native API.
Independent exact `fff72d74` review passed, receipt
`f66a1d814ff4438f9cc9165c27745b6f7a5904ff5e75ed6d223bdb5e9e5792b7`,
pinning nine files and independently passing six focused custody/pidfd controls
in 0.190s. It supersedes the earlier receipts. Advisory `6088820407` briefly
reported no findings at 2026-10-09 21:08:58Z, then was replaced at 21:11:36Z
with a real MEDIUM finding: a new member observed while the original leader
was proved could never enter immutable custody. That successor adds bounded
immutable supplements under the same original-leader proof; the prior PASS
does not cover this correction.
The four new controls prove durable custody before signaling a newly observed
live-leader child, cold orphan retirement from that supplement, missing-leader
unknown refusal, the 64-identity cap and wrong-owner proof refusal. The complete
23-case Normal suite passed serially under `nice -n 19` in 0.441s. Independent
successor review and the actual current advisory disposition remain pending;
no runtime API, native hold or Job was used.
Independent `13963a9b` source review passed, receipt
`e4947acc97c6ec9ca0a34472f5aba5f303ff5ac511ae03aa9302b69dd2a8dc30`;
four focused methods passed in 0.062s. Advisory `6088820407` was then updated
at 21:21:11Z with the additional kill-round fork availability defect: a new
child can outlive the leader killed in the same round. The stop/leader-last
successor addresses that separate finding; the earlier PASS does not cover it.
The three focused controls cover a descendant fork during retirement with
leader-last ordering, actual stopped-state acknowledgement within the original
deadline, and resumed-leader refusal before any descendant kill. The complete
26-case Normal suite passed in 0.696s serially under `nice -n 19`, including the
actual sleeping process/descendant pidfd control. Successor independent review
and actual current advisory remain pending. No native API or service hold ran.
The independent targeted reread caught a dispatch guard gap: stopped-state
acknowledgement checked the deadline after sending STOP. The final correction
checks the same original deadline before every STOP and KILL dispatch, as well
as while awaiting actual state. The expired-budget fixture now requires zero
signals. Three focused controls passed in 0.163s under serial `nice -n 19`.
Independent exact `84f73ee9` review passed, receipt
`e7635686812453acd3c7f0449b7116ed60fc87947050eb74e87729a35882d642`,
pinning nine files and independently passing those three controls in 0.153s.
It supersedes the earlier source receipts for the stop/leader-last and dispatch
corrections. Actual current advisory and required checks still gate merge;
this is preparation evidence, with no runtime packet or hold.
An uncaptured surviving process can still prevent proven retirement; its parent
may have died before its command timeout was enforced. Do not promise that such
an orphan exits within 50s. Record the miss and continue bounded safe attempts,
with coordinator intervention if ownership stays unknown. No extra stop/signal
authority is inferred from the advisory's optional suggestion.

The independently reviewed #3667 successor was `f9f654b6` (receipt
`89ffe7fc818cc66273505adcd6f4580fdeb5108ae7f280d4ad9b38133095e738`)
and its current advisory explicitly reported no findings. It merged at
`f206275d` on 2026-10-09 20:09:41Z. The source-only LIVE diagnostic packet
`17e40857` passed pin/profile/admission checks but was refused for its host
finalizer's original 200s boundary, receipt
`1ce20e35eda2c23a3fac5099428f570903ac50ad538ab7c18b6e1cea75e00bdc`.
The peer owns that separate narrow host correction; no Job was authorized or run.

Validation will use finite fake-native, fake-clock and private local Git fixtures
under `nice -n 19`, serially. No stress, busy loops, wide test runs, native holds,
Jobs or library writes are permitted during preparation. Runtime proof remains
missing until the exact closed rehearsal is separately approved and run.

The first exact Normal-only watcher launch on 2026-10-09 at 21:52:01Z refused
before readiness (exit 2). Packet `5ff97f17` and its initial state are preserved;
no exercise, hold, producer or data write occurred. Private refusal receipt
`f6657dd08c183a553bea8d6676d66d369b3c1fc36fe46d2f1638eddeb054c519`
records fresh unsuspended Source and all six Kustomizations. The runtime check
compared the raw converter ConfigMap reference `lazylibrarian-epub-convert`
against Kustomize's generated `lazylibrarian-epub-convert-6hf7f7477c`; this was
the sole JobTemplate mismatch. Four HelmRelease values/Ready/generation checks,
four Deployment/Pod/image checks and the six active CronJob checks passed.
The Source artifact was still at `0555d861` while remote main was `6f255ce4`.
The attempt remains refused and provides no hold/drain/recovery runtime proof.

The ratified source correction adds four auxiliary inputs from the same immutable
current Git SHA: the LL app Kustomization and all three converter generator files.
It preserves the canonical six service-goal paths. Before accepting the generated
reference, require the exact current-revision Ready LL Flux inventory and its
source/path/target namespace, then bind the actual downloads ConfigMap name and
UID plus every data key and byte to that complete generator. Recheck its UID/RV
after the JobTemplate comparison. Only that proved name is substituted in a copy
of the expected template; all remaining declared fields retain their checks.
Unexpected generator settings, file/key/data changes, binary data, inventory,
revision or identity drift refuse. A source-fixed attempt requires a new unused
packet, current Source convergence, peer review and separate runtime authorization.
The four auxiliary blobs are read by immutable SHA for both current Normal and
the held original Normal goal. They do not enter the six-manifest archive/Stop
contract. Actual CM UID custody survives a cold reload; UID/RV of both CM and LL
Kustomization must remain stable across each proof. Suspension does not need an
invented observed-generation advance: held ownership/drain remains the existing
separate proof, while this check requires the original applied revision and Ready
inventory. Kustomize's generated content suffix and reference rewrite are documented
by [Kubernetes](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/);
Flux's [inventory schema](https://github.com/fluxcd/kustomize-controller/blob/main/config/crd/bases/kustomize.toolkit.fluxcd.io_kustomizations.yaml)
defines the exact namespace/name/group/kind reference used here.

Eight focused finite methods passed serially under `nice -n 19` (seven in 1.358s,
then the added held-original/current-main counterexample in 0.131s). They cover
the real raw/generated reference mismatch, every remaining declared template
field, complete generator closure, inventory/revision/source drift, all script
keys/data/binary data, bracketed UID/RV replacement and cold UID custody, and
the unchanged six-file archive contract. The source-only same-SHA probe accepted
all four actual Git inputs at `6f255ce4` with three data keys and 134,768 script
bytes. No candidate runtime API write, hold, producer or rehearsal retry occurred.
Current-head CI, actual advisory disposition and independent peer review still
gate merge; runtime hold/drain/recovery assurance remains unproved.

Independent peer review of `1b640bcc` passed the CM binding and seven new finite
methods, but found that the preexisting subset comparator also accepted extra
JobTemplate fields such as an added initContainer. That source is superseded by
the strict comparator correction: strip only eight omitted fields when their
values and types equal the actual known defaults (empty JobTemplate metadata,
IfNotPresent, /dev/termination-log, File, ClusterFirst, default-scheduler, integer
30s grace and integer 420 ConfigMap mode), then require typed equality in both
directions. Explicitly declared values always remain exact. Added initContainers,
args, envFrom, unknown fields or changed/default-type values refuse. Three
necessary focused controls passed in 0.194s serially under `nice -n 19`, including
the existing actual strip/hold protection case. The only subset JobTemplate
comparison in the shared watcher was the converter; other CronJob image/suspend
checks and the six service-goal paths remain unchanged.

The actual `b82c792a` advisory (`6090032193`, `6090093642`) reported HIGH
`4234924494` and MEDIUM `4234924813`; green advisory execution was not a clean
review. An unrelated later commit applied before a hold can otherwise strand
the held-original cancellation proof. The ratified successor permits that
case only inside `runtime_still_normal`: parse the exact applied main SHA,
prove it is a descendant of the original baseline and reachable from freshly
fetched main, and read all four auxiliary blobs at that applied SHA. All four
hashes and all three data values must equal the original goal. Current/final
Normal and arming still require exact current main; deliberate arming refusal
while Flux lags remains a preflight gate, addressed by normal convergence before
the fresh attempt. Inventory, complete data, UID/RV and the remaining service
fields are still checked. The held-only flag is restored in `finally`, including
failure. Malformed converter shapes and typed proof failures return unhealthy;
the owning `ServiceCeiling` control and other `BaseException`s propagate.

Five necessary successor methods passed serially under `nice -n 19` in 1.057s.
A real finite Git fixture proves unrelated descendant acceptance only for the
held original, current/final lag refusal, changed auxiliary content refusal,
older/non-descendant and foreign-root refusal. Malformed volume/revision cases
return false, while the actual service-ceiling exception propagates. Existing
strip/hold, bracketed UID/RV/cold custody and original/current-goal controls also
pass. No cluster write, hold, producer or rehearsal retry was performed.
The successor peer review caught two additional malformed shapes: null KS status
and a null ConfigMap object raise `AttributeError`. The narrow ordinary-error
catch includes that type, with both finite counterexamples returning false and
the same `ServiceCeiling` propagation control retained. This supersedes the
`622228c1` malformed-proof result; no other authority or scope changed.
The necessary single updated method passed in 0.066s under serial `nice -n 19`.

The final source `521faef0` passed independent review (receipt
`f2fa62b0d1c4332343bb3fc138c6356b660b305ff22b79b00da69e4e429ac36f`),
all current CI and the actual no-findings advisory `6090219388`. PR
[#3682](https://github.com/thaynes43/haynes-ops/pull/3682) merged at `08a9659e`
on 2026-10-09 22:22:44Z. Source and all six relevant Kustomizations converged
to current main `489a2189` before the new attempt. All four Deployment
UID/generation/spec hashes and all six service Pod UID/spec hashes stayed
unchanged across the targeted Source reconciliation.

One new unused Normal-only packet `ff1fa31b` passed independent preparation
review `7f411f353b94519df5bf22bb2e5bbe25da2b63964607a6eb80520902ae423539`.
Its separately authorized watcher produced readiness at 22:28:16.020693Z,
bound to PID 969255/start ticks 233611013 and exact packet/phase/source closure.
The coordinator verified that live readiness before authorizing one exercise.
A later peer readiness observation at 22:29:10 was after the exercise; its
original receipt
`d0ba25c78047c66a9d836391cfb2f6bf86a846b85991089cef749621f8aebac1`
and incorrect "no exercise yet" claim are explicitly superseded by immutable
correction `29223553ec3da49a8c47f38adadcbe976c66bb61de11d1c1631968501a0896e7`.
It does not replace the coordinator's prior authorization-time proof.

The exercise exited zero and proved fresh same-UID handler drains for both
actually suspended parents, all four app Kustomizations and the Source, with
the actual Normal archive bytes and unchanged source-controller identity.
It applied no Stop contract and created no producers. Cancellation began
restoration at 22:29:08.925175Z. The original 50-second recovery budget was
missed at 22:29:58.925777Z and remained recorded. After the fixed 10-second
retry wait, bounded safety recovery restored actual Normal at
22:31:07.270425Z, **118.345250 seconds** after the original recovery origin;
the subsequent safety attempt took approximately 58 seconds.

The result is **RECOVERED_AFTER_MISSED_BUDGET**. Fresh read-only final checks
at 22:32:18–22:32:20Z confirmed Source and all six Kustomizations unsuspended
and Ready on `489a2189`, all seven phase-owner annotations retired, full
owned Job/Pod union and both PostgreSQL leases absent, and the watcher exited.
All four Deployment specs/UIDs/generations and all six service Pod UIDs/specs
remained unchanged. No COPY, producer, library/list mutation or strip enable
occurred. The actual final receipt is
`24621b9e917bd82d1c4c37727fd71499b46de1dc8f06f6a5e71faeaa35818c6e`;
private raw proof and original clocks remain preserved.

The historical raw watcher state set generic `complete=true` even while its
Normal-specific within-budget result was false. The scoped source correction
keeps `complete=false` after a Normal recovery miss and records completed safety
restoration separately, including cold verification. It does not change the
failed timing result or authorize another attempt. The unresolved 50-second
performance gate is tracked in
[#3684](https://github.com/thaynes43/haynes-ops/issues/3684), with exact cold-start
context and next finite work. No original per-call timing was retained; the
serial recovery prefix is an implementation observation, not a proven cause.
No recovery budget is widened. Cached-Stop/COPY readiness remains unproved.
The generic recovery terminal condition also treats the Normal miss marker as
safety-only before saving any result, closing the crash gap between generic
restoration and the scoped Normal result helper. A failed cold verification
cannot persist a successful in-budget result. Independent actual audit receipt
`521a89a5be91e629ba2a19594f69fe3e1baba89d6a4c9f9fa47163ee2092f158`
confirmed the disposed result, held/drained identities, seven request barriers,
retired ownership and fresh writer/PG absence without advancing any gate.
Three focused finite controls passed serially under `nice -n 19` in 0.021s:
no saved generic completion after a miss, failed cold verification preserving
the original miss, scoped cold-result correction and historical in-budget proof.
They use fake services/clocks and make no production API call.
The Normal-only generic safety branch preserves its first safety-completion
timestamp; a later successful cold proof has a separate reverified timestamp.
The finite control exercises the real generic path at both fake times so the
scoped helper cannot conceal a timestamp overwrite. Other recovery modes keep
their existing timestamp behavior.
The single extended real-generic control passed in 0.008s under serial `nice -n 19`.
The actual advisory findings `4235127776`/`4235138148` also require preserving
historical raw `completed_at` when no safety timestamp exists. That Normal-only
fallback and its real-generic cold-state counterexample are included; a new
verification time never replaces the original restoration proof.

Unused prepared Stop/inverse drafts
[#3659](https://github.com/thaynes43/haynes-ops/pull/3659) and
[#3660](https://github.com/thaynes43/haynes-ops/pull/3660) were closed without merge
or application after this failed timing gate. Their branches remain preserved at
`7bcdbb188bba43daeb00c4d3b852d71b9281bdf8` and
`27156481c649fe20acc008cfd1a4c4f1cba575fe` respectively; no branch was deleted.
Any future pair requires a fresh full packet and separate authorization.
The hourly-strip draft #3571 remains held.
