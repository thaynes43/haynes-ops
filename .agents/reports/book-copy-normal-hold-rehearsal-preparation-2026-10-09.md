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
