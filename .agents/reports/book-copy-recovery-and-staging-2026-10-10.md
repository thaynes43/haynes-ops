# Book recovery and staging evidence — 2026-10-10

The earlier metadata backfill completed **290 verified EPUB metadata-edit
operations**. The October 8 verification matched 289 current paths to their
verified candidates; LazyLibrarian had replaced one historical edition. Retained
originals and ZIP members were verified. This records completed historical edits,
not a claim that every later scan passed. Three Pathfinder EPUBs subsequently
became unmapped at the normal nightly scan; that regression remains #864 until
the repair and real scheduled-scan proof pass. Do not replay the completed backfill.

The completed reading-list work added **116 missing book entries across 39 lists**:
19 entries in eight existing lists and 97 entries in 31 new lists. Existing
entries were preserved. Libretto now has 106 recipes; the original 75 recipes and
their acquisition settings are unchanged, and the 31 new lists do not acquire
books automatically. Production book acquisition remains enabled.

This work targets EPUB metadata and ebook organization. It does not rewrite MP3
or M4B audiobook tags. Clean book identities improve ownership and request
matching; reading lists provide series order without relying on the imported
EPUB series fields.

Remaining work is the two selected Pathfinder duplicate copies, fresh accounting
for the other duplicate groups, Ransom’s seven-field catalog ruling and guarded
repair, the real nightly Kavita scan, and subsequent hourly strip validation.
A separate 15:52Z read-only check now confirms Ransom has been idle for 75 days;
the required catalog ruling was asked after verification and remains pending.
Its whole-folder hold and the hourly strip remain in place. The recovery evidence
below records what actually ran; safe restoration does not establish COPY success.

The latest closed attempt, V20, corrected the private receipt publication defect
from V19. The existing private reader and independent review passed before the
launch. Its fresh read-only capture completed in 86.566465s, including delivery
acknowledgement and removal of its temporary Job and Pod. It then stopped after
the publisher capture because the phase package omitted the publisher guard's
`approved-normal-write-profiles.json` input. The file writer never started:
**zero duplicate copies moved**, and neither the LazyLibrarian nor Kavita Native
exporter ran. This was another cleanup-runner defect, not an additional metadata
fix. The earlier receipt defect and this omitted input explain attempts that
consumed time without advancing the book cleanup.

Cold service restoration completed in **85.934463s**, within the existing 130s
reserve. Final audit `edbeb69c` at 15:47:29Z proves Normal `98722be5`, all seven
controllers healthy and unheld without owners, all four original Deployment
UIDs and exact Normal specs restored, and six healthy service Pods. LazyLibrarian,
Kavita and Libretto Pods were replaced by the Stop/restore cycle. No temporary
phase Jobs, Pods or either primary process group remain, and the watcher retired.
Archived activation and cache receipts remain evidence, not active holds. No
further attempt has run under this phase; its original clocks are unchanged.

The Slskd page is resolved. Ops #3762 expanded its existing claim from 2 GiB to
4 GiB without restarting the application. Metrics show about 52% free space, and
the exact AlertManager alert cleared. The owner confirmed the page named
`downloads/slskd`. This was application database/history storage, not book scratch
space; the book source already uses `gasha01`.

The omitted policy input was fixed by ops #3768, merged at 16:03:33Z as
`d71fd3688e959ea650c021787beb3153c0225f56`. A focused regression and distinct
review passed. The offline check reproduced the missing-file failure, then
accepted the original policy and all eight retained publisher rows. It could
not prove the full inventory guard because contemporaneous Pod/PVC/PV arguments
were not retained. The actual Claude advisory failed after 410 ms without
substantive findings or an underlying reason; Root read and recorded it before
merging. This preparation fix preserves the qualified image, original runtime
programs, policy bytes and timing limits. It is not a successful duplicate cleanup.

The historical V18 attempt passed its fresh read-only capture in 91.698357s but refused
the publisher before MAIN or the book Native exporters started: 66 subprocesses
succeeded in 34.9337s (84 trace events including telemetry), then one new Sonarr
series/path differed from the reviewed stable scope. That path is outside EBooks;
all prior items/paths and the other seven publisher rows remain exact. The
prospective baseline update was subsequently ratified for only its two named
scope leaves, before V19. Zero copies moved. Actual cold restoration completed in 69.567367s under the 130s
reserve. Final audit `3bcea491` proves current Normal `55f3cac8`, all seven healthy
unheld controllers without owners, full temporary Job/Pod and both primary process
group absence, restored Normal service specs and a retired watcher.

The preceding V17 stale Lidarr bridge image guard was corrected by ops #3759 as
`1a78ce61`; focused validation, relevant CI and independent review passed. V16's
approved-inverse read race was corrected by ops #3756 as `76f22cce`. The unavailable
Claude advisory results were read and recorded. Neither fix is a COPY success.

The earlier V15 attempt, completed LIVE in 101.763380s with delivery ACK and full
foreground Job/Pod cleanup, then actuated Stop and refused a reviewed publisher
command before the NativeDB exporters or MAIN writer started. Cold restoration
completed **64.873779s** after its request, within the prospective 130s allowance.
Its final audit proved seven current-generation Ready/unheld controllers without
phase owners, complete typed phase Job/Pod and both primary PG absence, the same
four Deployment identities with exact Normal specs and six healthy current Pods,
and retirement of the watcher/group. No production COPY gain is claimed.

The earlier services-running generic Normal rehearsal passed in 28.306327s, but
its source is historical after #3732 changed cold recovery ordering. Actual v11
cold restoration took **127.249103/50s FAIL** before that fix. V12's still-Normal
staging withdrawal took **50.427959s**; it is neither a cold result nor a 50s PASS.
V15 supplies one actual cold restoration below 130s; earlier failed clocks remain
failed, and it does not guarantee the duration of a later restoration.

## Historical services-running recovery prerequisite

Ops #3694 removed the earlier Normal-only callback shortcuts; #3695 (`0bad9bd5`)
added a strict fresh current-generation Ready/exact-Normal revision skip while
preserving the generic callbacks. Full typed phase Job/Pod union and both primary
PG leases are proved before every controller release and at final proof. Source
reconciles once, then exact owned UID/full spec/phase/Ready Normal is checked
freshly before each release. Missing/stale generation or Ready evidence retains
the real reconcile; unknown inventory, PG, ownership or Git refuses release.

The immutable current-source packet `9803f674` kept source provenance `0bad9bd5`
while actual Normal was `e9c0789c`; scoped source/manifests were byte-identical.
Recovery ran 02:32:32.953765–02:33:01.260092Z, **28.306327/50s PASS**. All six
post-release Kustomizations actually reconciled (2.063–2.103s each); Source
reconciled once in 2.084735s. This proves the generic callback reserve while
services keep running, not cold service startup after actual Stop.

Private evidence directory: `/home/dev/work/hn-825-normal-ready-recovery-1010-01/`.

| Receipt | SHA-256 |
| --- | --- |
| `actual-original-budget-result.json` | `1ed52b11e4a00db1c6d2ae5fc7497033f3f9cef9eb26d6d7de02cdad4ff88c30` |
| `actual-final-audit.json` | `032c817b8a3c48c006ab73b0a1f08655e28dddfc26b8eec86387bd021f328cb6` |

Final proof includes all seven exact Normal/current-generation Ready controllers,
unsuspended with phase owner absent; complete typed phase Job/Pod union and both
primary PG leases absent; four Deployment UID/generation/full specs and six
service Pod UID/full specs unchanged; watcher and original group retired.

Retain the earlier generic `f7e4f9e6` **50s miss**: recovery began
02:01:33.160521Z, missed at 02:02:23.168273Z, and safety recovery completed
02:03:00.649231Z. A forced `cluster` CLI took 26.980978s despite retained short
controller reconciles; the CLI request-wait cause remains unproved. The earlier
28.363524s result used different Normal-only callbacks and is historical evidence.
Neither result is relabelled as a cold COPY recovery proof.

## Withdrawn staging attempts

Each directory below is `/home/dev/work/hn-825-pathfinder-copy-packet-1010-vN/`.
All attempts retained their original 600s arm; no clock was reset or extended.
Every final audit proved fresh Normal, seven unheld controllers without phase
owner, full typed phase union/two PG absent, unchanged service identities and
retired watcher/group. No attempt applied Stop to services, ran LIVE or wrote books.

| Attempt | Actual outcome | Final audit SHA-256 |
| --- | --- | --- |
| v3 | Two app holds; local immutable partial-receipt filename collision stopped the adapter. Remaining time could not fit CI and full LIVE. Later adapters use unique per-scope receipts. | `e7c7c23cc943547630ae32ab660cda8943fbd01607eea6341774d5abd28e78f3` |
| v4 | Normal `cluster-apps` reconcile removed all four runtime suspend/owner/request fields, same UIDs; guard refused before Stop. Refusal `076e2f7807c8e5a66d56d7e9dbea75b0a27115ef52e46be59df2d7fc67038550`. | `9e42e3abea94dfc37f0b1aab424d7f2a7159d5adbc3696e0c9434bb27dd8bd0d` |
| v5 | Parents/app/source held; automatic inverse replay refused rewritten Stop ancestry. Exact owner-approved local replay preserved six-file inverse; same watcher pushed it. Normal inverse merged after the full LIVE cutoff; no activation. | `1fcde2841bb516332510270385e6b8d6fb60d4090989a0b3d2ce881705d951db` |
| v6 | Correct direct-parent pair verified before arming; coordinator did not issue hold GO promptly after readiness. Zero holds/merges/LIVE; unused watcher withdrawn. | `d2de66b2e52f0619943b74a2360ba4214b2ec663cf7773795ef20c7040b1ca9e` |
| v7 | Parent-first/app/source holds and automatic retarget succeeded. All-green inverse merged 04:14:11Z, after full LIVE cutoff 04:14:03.053845Z. Owned cancellation restored exact `81184d3f`, complete 04:17:25.327542Z before original 04:17:53.053845Z expiry. | `6374e3f6bd2e81d079eb3082783a20f4abf15386dff4155fe7a3507b3e658ab9` |

V7 final audit is `stage-cancellation-final-audit.json`; immutable withdrawal is
`stage-withdrawal.json`, SHA
`0767ee723388afeb386ae291db59418f267393cf11f28e6f4cbe87a06bd34448`.
Stop #3711 merged intent `37f3ea40`; actual Normal inverse #3712 merged
`81184d3f`. All four Deployment identities/specs and six service Pod
identities/specs remained unchanged. No cache activation or service origin exists.

The corrected cached-source operational order is fresh Normal custody → hold/drain `cluster`
first → hold/drain `cluster-apps` → four app holds → merge Stop → Source alone
fetches exact Stop → Source hold/drain → required-green reviewed inverse merge →
cache seal → three-leaf preLIVE metadata bind → fresh held-Normal proof → separate
root conditional combined LIVE/COPY GO → automatic LIVE ACK/GC verification,
baseline-leaf bind and existing supervisor exec. A parent apply over held
children is forbidden; fresh held-parent containment replaces that failed action.
The inverse must have the reviewed Stop head as its direct parent before arming.
There is no post-LIVE model handoff or additional API census. After conservative
activation the sole watcher applies each guarded app Stop once, reholds/drains
all four, and publishes completion before the supervisor admits producers.

## Historical #3715 staging checkpoint

The following bounds describe that historical checkpoint. The prospective clock
matrix adopted by #3740 below superseded these numeric bounds; this section is
not authority to execute a later phase.

Coordinator ratification permits a **fresh cached-source** reviewed staging attempt to select
`--arm-deadline` up to 1800s; default remains 600s. Actual CI timing made repeated
600s attempts unable to fit the full LIVE bound. This changes only the still-Normal
staging cap, never the clocks of v3–v7. Full LIVE200s plus bind30s must fit before
the new attempt's original expiry; missed readiness, ownership or cache proofs
still refuse. The legacy non-cached path keeps its 600s cap, unsuspended parent
reconcile and under-600s hold receipt predicates; it is not approved for new COPY
staging after the actual v4 refusal. There is no new runtime authority, helper
framework or guarantee.

The conservative pre-release/actual Stop origin still switches restoration to
the unchanged 170s trigger, 300s service ceiling and 50s recovery reserve.
Original byte-capture300s, LIVE180s/host200s, Normal120s arm/90s exercise/50s
recovery/60s safety, retry10s and all UID/spec/RV/phase/PG/source checks remain.

## Distinct completed image and native prerequisites

The signed production manual-census image deliverable is complete:
`ghcr.io/thaynes43/book-copy-writer@sha256:fdc358fcce883a198a710f9415a95e5200b39499a26ab540a9863043a8b2f86c`,
source `7c99b2afbeed3ec04af55493509169e0778d6418`, successful publish run
`37966779884`. Packet signature receipt
`9bc68e6f5c21ac436838786f10ccc93db776108b9e897a7558730e4b9bd99ddc`
verifies trusted identity/chain/SCT/Rekor; registry module receipt
`c081444ccf60cdf0bd9d0a3e48169f6e3c95fbbc42a93a2c2d8ded7a65499400`
verifies all six exact source modules. This is distinct from the signed `c5e8`
native fixture, and grants no COPY authority or bootstrap-bundle signature claim.

Historical native v13 passed original 180s/host200s with production authority
false, all 82/34433 historical projections, 32 inverse checks and encoded covers.
ACK/GC and watchdog retired; fresh complete typed fixture union was empty.
Aggregate `/home/dev/work/hn-ransom-native-fixture-actual-c5e8-v13-1010/aggregate-actual-v13-proof-final.json`
SHA `44df8c85d981c00486f86a2ac4b76bed8d7ef17ad41ad21183bda03eab16b18c`.
No additional native run, image adoption or corpus projection accompanies this cap change.

## Later actual runtime outcomes

Each private directory is `/home/dev/work/hn-825-pathfinder-copy-packet-1010-vN/`.
The new cached attempts selected the reviewed 1800s staging cap; earlier 600s
attempts retain their original clocks. Every attempt has an actual final Normal
audit and retired watcher. An exit-zero orchestration result does not imply COPY
success, MAIN creation or file writes.

| Attempt | Actual outcome | Final Normal audit SHA-256 |
| --- | --- | --- |
| v8 | LIVE succeeded in 155.26s. Post-LIVE observer/model handoff consumed original admission reserve; supervisor/activation/Stop/COPY never started. Staging cancellation 53.617356s, not cold proof. | `8f7d2f6cb74e24afb6e3dce2195ee1d8a6ec3813e7ced9338d5b1ba05c916e97` |
| v9 | LIVE refused at 180.190471s with actual full owned GC; no baseline ACK, supervisor, activation, Stop, NativeDB export or COPY. Staging cancellation 58.234635s. | `7a716c66768d7977d4ce92c95da4c1e180707c918dc07a3c700e8bb1d85c9a13` |
| v10 | LIVE succeeded in 150.499972s, but cached Stop activation had no actuator. Services never stopped, all producer UIDs stayed null, no NativeDB/MAIN/writes. Controller restoration 56.619538s exceeded 50; cold restoration remained UNTESTED. | `94024e3462a26f8a26a53f6c717677dcf2f8ba26b97e0ab4168d61b4d6bf9d9c` |
| v11 | Actual Stop occurred; Source and Lidarr producers existed, MAIN stayed null. Publisher transport omitted three already-pinned JS/JS/sh siblings and failed before API work in 0.0001s. No LL/Kavita NativeDB export or book writes; actual cold restoration 127.249103/50s FAIL. | `113e92e4b6f65abde7d713954992ba0829cb05b8da424c237c9a57f99b084843` |
| v12 | LIVE refused at 181.065640s, baseline incomplete, actual Job/Pod full foreground GC confirmed. Adapter never executed supervisor; no activation, Stop, COPY, MAIN or NativeDB export. All services/identities unchanged; withdrawal 50.427959s. | `dce47e736dbb0b7a94e99d13c408befb8fb98ab205fc00bd5cd0fcf9ed81eab4` |

V10 conservative activation was **06:00:15.438926Z**, recorded at watch top level.
The final audit's null projected `actuation_budget_started_at` must not be used
to claim no conservative activation clock. Actual first Stop/window remained
null. Restore request 06:03:07.607232Z to supervisor verification
06:04:04.226770Z was 56.619538s; watch completion was a separate 56.385290s.

V11 conservative activation was 06:52:02.124344Z, first actual Stop
06:52:33.395688Z and all-four Stop/rehold completion 06:52:56.623702Z. Owned Jobs
and source reader were stopped before restore request **06:53:12.245268Z**.
Normal verification **06:55:19.494371Z** made the actual cold interval 127.249103s.
The final actual Normal was scoped current `b1a289b5`; expected Pod/generation
changes after Stop were recorded with same Deployment UIDs and restored full
Normal specs. The v11 audit became the v12 service baseline; older v3 Pod and
generation equality is historical.

The retained v11 LL controller event proves health checking took 30.122032081s,
with reconciliation 30.657794262s ending at 06:54:28Z. Serial request/wait ordering
delayed the Kavita Normal request until about +94s. The exact failed CLI output
was not retained, so there is no invented per-call duration or general cold
startup guarantee.

## Source and transport repairs after those outcomes

- #3721 removed the two-reader head-of-line submission stall with unchanged two
  active reads and bounded deterministic reorder/proof accounting. Collector
  transport changed separately; the six signed runtime modules did not.
- The already-reviewed automatic handoff `2f49c2df` verifies original-clock LIVE
  exit/ACK/full GC, binds only the baseline leaf, then directly execs the existing
  supervisor under prior root conditional GO. It removes the failed v8 message
  handoff without relaxing current custody or admission checks.
- #3727 added the missing once-only cached Stop actuator. Source/parents remain
  held; each app release/reconcile/rehold/drain is guarded by original UID/full
  spec/RV/phase/request and fresh complete union/primary PG absence. Pending
  transitions use the Source/parent guard; strict all-seven-held proof applies
  after all four reholds. Producers still wait for actual service fences.
- #3732 (`c16bb00e`) requests Normal for all four owned app controllers before
  waiting for any app Ready, then restores parents only after all apps pass.
  Each release/request retains fresh Source, complete typed union/two PG,
  UID/RV/full spec/phase and strict current integer-generation Ready/exact
  revision guards. The existing blocking Stop/legacy semantics and all original
  budgets remain. Focused controls passed serially under nice19; actual advisory
  finished with No Findings and independent exact-head source review passed.
- The local coherent publisher bundle includes all **11 siblings / nine required
  program hashes**, including the missing JS/JS/sh files at their existing pins.
  Closure `a18e2e6b687c4503f0b2fe8eadfbf8859e0a4faeac88221577718a70ebfc754f`
  repaired transport; no program rewrite, new image adoption or API authority.

The current source's cold50 result remains unproved. Historical 28.306327s Normal
evidence cannot certify changed `recover_cluster`, and no new Normal/corpus/native
rehearsal was performed to restamp it. Exact source peer for #3732 is
`7d6c048cbfacfe22b4fbd84470ddae3a8b753d57f849845efb8df81a53033f3f`.

V12 completed all **1,970 byte/OPF reads / 8,547,028,338 bytes** in 153.754472s,
compared with v11's 96.434639s for the same complete/prefix totals. The subsequent
walk reached the original 180s producer deadline; no baseline delivery/ACK began.
Applying v11's measured 39.645623s post-byte tail to v12 projects 209.380831s,
which is a feasibility comparison, not an actual completed run or permission to
extend a clock. No reader/kernel telemetry establishes an NFS or queue cause.
Retained diagnostic `v12/actual-live-deadline-diagnosis.json` SHA
`6478647f8676e781b47ff64a71b1231ad8eaf2ebbe23240a219568473940be4a`
requires no additional corpus read, API observation or unchanged retry.

## V12 timing and final safe state

Current source was `c16bb00e`; actual Normal inverse #3734 was `9f3d6ed2`.
The corrected immutable deck bound the actual v11 final service baseline,
fresh five null Job UIDs/two unowned PG leases, all 11 publisher siblings/nine
required hashes, and the unchanged signed runtime/selection/native closure.
Unused incorrect sequence-path descriptors were withdrawn before any launch.

Owned cancellation was **07:42:33.489265Z**; authority revocation +4.100705s,
Source resume +9.020305s, then all four retained app request tokens were at
+14.056542/+14.975265/+15.913779/+16.890979s. Parent request tokens followed at
+42.778628/+45.771988s; final phase cleanup was +48.287609s and Normal verification
**07:43:23.917224Z**, +50.427959s. These request tokens demonstrate actual
all-apps-first ordering. There is no per-call trace; final app Ready transition
timestamps reflect later parent applies and cannot identify each wait duration.

Timing-only receipt `actual-staging-recovery-timing-only.json` SHA
`251ef79fdaf52d63313668055f29cd1d0255c8c811369296b590a6b27187ec23`
retains those limits. Final `actual-final-audit.json` proves exact Normal
`9f3d6ed2`, full typed phase union/both primary PG absence first, all seven strict
current-generation Ready/unheld/owner-absent controllers, unchanged four
Deployment UID/generation/spec and six healthy Pod UID/spec, and retired original
watcher group. Activity `act-073008-1450828` ended after coordinator physically
verified that receipt. No new phase or runtime authority follows this report.

## Remaining Ransom and overnight gates

No attempt above produced a current Kavita NativeDB export. Historical native
v13 proves its exact historical inputs only. Current Ransom file-to-chapter
mapping, every user's saved progress/location/session/bookmark/annotation, latest
actual reading state, and 30-day idle eligibility are not certified by it.
OC046 requires actual verified series-only stripping, original/input identity,
bounded scan and hold release; 30-day idle is eligibility, not closure. Active or
unknown reading state retains the whole folder. No reading-state migration or
production catalog write is authorized by these fixture results.

The reviewed same-ID catalog boundary contains only Series
Name/NormalizedName/OriginalName and Volume LookupName/Name/MinNumber/MaxNumber;
Series/Volume/Chapter/File identities remain. Any production decision requires a
fresh verified premise and the owner's recorded ruling.

The next actual scheduled Books scan is **2026-10-11T04:00Z**, with the planned
04:32 evidence pairing still pending. Existing `downloads/owed-checks` runs
hourly at :50 with a bounded 180s read-only runner for app PG16, LL SQLite, Loki
and Prometheus; it has no Kavita NativeDB/file-mapping executor or callable
same-chat wakeup. This session lacks automation/thread-wakeup scheduling tools
and no existing local at/cron/systemd-run mechanism was available. No controller,
scheduler, polling loop, new thread, Kavita schedule change or manual Force scan
was added. **Hourly stripping remains held until the actual required proof.**

## Prospective clocks and actual signed runtime qualification

After the retained v12 feasibility result, coordinator ratification selected a
prospective matrix: BYTE-only proof lifetime 600s from the original earliest
capture; LIVE producer 240s and host 260s; activation admission at original byte
age no greater than 300s, checked again immediately after arm before activation.
The actual service ceiling stays 300s and independent watcher trigger stays 170s;
the prospective restoration reserve is 130s. Generic metadata freshness 300s,
publisher 65s/35s and health 12s remain. All prior 50s failures and untested cold
results remain historical evidence; **actual cold130 recovery was unproved at
this prospective decision**. Later V15, V17 and V18 outcomes are recorded above
and below; they do not guarantee a future restoration interval.
These numeric bounds were agent design choices, not an identified owner quote.
No original attempt's clock was extended or reset.

Source #3740 merged `5a696d43` after actual advisory findings were fixed or
concretely disposed and exact-head independent peer
`f77718de4d9dae0aa72a98b2297c7a6c10d95e58261ce2f0dd80abeba398648c`.
The existing 93-line automatic LIVE-to-COPY adapter is now tracked; canonical
preLIVE configuration is checked before starting the child and again after it.
It preserves actual ACK/full owned foreground GC, binds only the baseline SHA,
and directly executes the existing supervisor without a postLIVE model turn or
API census. The 250s Job lifetime fallback remains separate from SOURCE/MAIN
absolute abort170 and independent watcher170, which cancels owned writers and
requires the full typed Job/Pod union and both primary PG leases absent before
any Normal release. Recovery does not wait for 250s or claim a 130s guarantee.

The first main publication failed in a one-second SOURCE health test before
publishing or signing. Test-only #3742 merged `daa542a3`; one sustained fake
clock and an exactly representable boundary made that control deterministic.
There was no production change, load reproduction or blind workflow rerun.
Actual main run 38038830436 then tested, published and signed
`ghcr.io/thaynes43/book-copy-writer@sha256:628e97b8dbcc83a4d7068484b516b21dde740c4dd130d54f3b030f1ee7a75601`.

Actual registry extraction proves all six installed module bytes equal exact
Git `daa542a3`; `bound_census.py` is
`d3df953dc7105d9d2535b0d370bb53c266ab46f018beac72652f681d91d52c38`,
and the other five module bytes remain unchanged. Normal cosign verification
passed the exact main workflow/source identity, issuer, chain, SCT and Rekor
checks. Qualification receipt
`23f773d42f5e16de57905f9070b3a5475ed96d55025313190c8d3df7952607b8`
and signature receipt
`ee882db8feb49ff1510700a415d6635b33850ab0e1f6c76c0825775652818183`
were physically read by the coordinator. An unsupported inherited-layer-equality
assertion refused in the earlier private reader; it was removed without another
image build or module reproof. No base-layer equality is claimed.

Literal active-pin adoption #3743 merged `506197a2` after the obsolete positive
image fixture was fixed, current required checks passed, actual advisory output
was read, and exact-head independent successor peer
`ee53a6239f5717895fde9d84aa838465e7b4b1618a0592f2f679ba348c87a214`
passed. Historical image receipts remain historical. Automatic later publication
does not require another image/adoption cycle for these unchanged module bytes.
No new phase, corpus read, Native fixture or Normal rehearsal qualifies this
prospective matrix; the next actual result must do so truthfully.

## Reading-list outcome context for the final combined update

Canonical completion preserves every old item and adds only missing works; an
extra legitimate copy is not a missing work. The newly created Dune item296 was
an extra Heretics copy and was removed by its distinct guarded inverse, preserving
the original five items, their non-order payloads and relative order. That actual
safe correction does not erase the retained clock-contract error in its prepared
approval; successful observed writes and descriptor correctness remain separate.
The three
existing-list repairs then added seven distinct missing canonical works across
Crescent32 (+1), Kingsbridge34 (+4), and Poppy35 (+2). Recipe hashes and normal
acquisition remained unchanged. A mode0644 host refusal made zero cluster calls;
mode0600 correction used the same bytes and original clock. The earlier contract
restamp defect is retained as a defect; actual writes occurred before the original
approved expiry.

The first four-new-list launch at 08:59:38.718607Z, phasea782, refused in under
0.4s solely because scheduled reconciliation intersected its bounded stage; no
intent or write occurred. The ordinary 09:00 reconcile ran without scheduler or
acquisition changes before its fresh successor. At 09:05:28.339Z the coordinator
physically verified that successor's four readbacks and ACK chain, bringing the
reported actual total at that point to 27 additions across 11 lists. Subsequent
fresh per-scope receipts completed **116 useful additions across 39 lists** and
31 new manual recipes with acquisition false, bringing the recipe store to 106.
The original 75 recipes and all previous list item payloads were preserved. The original
132 proposed additions must not be presented as 132 new canonical works.

The last four completed at **09:55:43.997Z**. Coordinator independently checked
the 56-event journal chain ending
`fe2dc2d2059554116712893988cef92fc4ef867a23cdd985e148857244b433cd`
and all 25 API responses were 200. Final fresh verification receipt
`/home/dev/work/hn-825-reading-policy-four-1010-03/actual-four-verification-receipt.json`
SHA `fe2d2d9d93c7ca1230814b03e52f16bee76e6e2da0034e8c44b5f5113a920154`
confirmed the final scope before all reading children retired. No scheduler or
normal acquisition setting changed.

Canonical-policy #3744 merged `be5fc3b1` after parsed actual pinned physical
artifacts were bound to inline owners and conservative ISBN/author-title ranks
before execution, while full old items/snapshots/native/acquisition and mutable
ACK artifact guards remained. Focused successor peer
`805b159c41a454904cd47efb28214ee95a44d384058cb19e59cf5913b5301e77`
carried the prior exact-head peer and reviewed the real child wrapper controls
without repeating tests or APIs. This policy is child-local and grants no COPY
or production Ransom authority.

## V13 LIVE success, preactivation refusal and safe Normal

Fresh prewatch after every reading child retired proved actual current Normal
`be5fc3b1`, all seven strict current-generation Ready/unheld controllers, the
current four Deployment/six healthy Pod identities, and complete typed phase
Job/Pod plus both primary PG absence. The exact parent-first holds, Stop #3745
(`2023b1c3`) and direct inverse #3746 (`a8d1f5ca`, final head `4639aff1`)
completed under the new phase's original 1800s staging clock. No prior attempt's
clock was extended or reset.

Actual LIVE completed at **10:06:52.542912Z**, in **106.867921829s**. Its immutable
receipt `v13/live-actual/actual-receipt.json` SHA
`976acd07f55f798e7c32840e3742f5123a3f6e0f19325e5659836a07403dcdde`
records baseline complete, delivery ACK, complete foreground GC, and zero
production library writes or PG operations. The automatic handoff bound the
baseline and directly entered supervisor validation, which refused
`exact root scope artifact changed` before activation. The manifest-contract
descriptor alone expected historical hash
`9ea1c812b31792d9193a6f98595b1a9f2b09a3088fe2d2f59fa90e4e8c951491`
while the actual closed v13 contract was
`2c4e8f43ffc8b3dacce2ce7b9eccb44e43eb5f58b2417c2223d287f3e5d278b3`.
The other five root scope artifact hashes matched. The static closure refusal
prevented Stop, producers, NativeDB export and book moves; it grants no current
Ransom eligibility premise or cold130 timing claim.

Owned cancellation was issued at **10:07:29.772168Z**. The existing watcher
restored actual Normal `a8d1f5ca` and retired. One final audit
`v13/actual-final-audit.json` SHA
`89765a59f08e610bf903f4b1a5b3afdb5bfa46d80ed87e590b2dac3ba2589e4a`
proved full typed phase Job/Pod union and both primary PG absence before checking
all seven current-generation Ready/unheld/no-owner controllers, unchanged four
Deployment full Normal specs and six healthy Pod identities, and empty original
watcher group. Coordinator physically verified that receipt and ended
`act-095726-1515793`. The consumed phase remains withdrawn; no retry, recapture
or new phase is authorized by this evidence.

## V14 LIVE success, private-input pin refusal and safe Normal

Host-only preflight #3747 merged `d4be1a92` with exact source/materializer peer
`62c3cc6d07a655155d35459051d294aaeee4296c92ad5c893aa3b3ccdd7f2ab4`.
It checks the five static root scope references before LIVE and derives the
manifest pin from the frozen actual bytes. The new closed v14 retained the
qualified image and original prospective clocks. One declared same-Normal
convergence and then one fresh prewatch proved all seven exact current `d4be1a92`
controllers and current service state before its watcher armed. Its Stop #3748
merged as `554478bf`; its exact current inverse #3749 merged as `21b9cdc3`.

Actual LIVE completed at **10:36:22.851173Z**, in **100.569740057s**, with baseline,
delivery ACK and full foreground GC complete. Receipt
`v14/live-actual/actual-receipt.json` SHA
`5d92412e5c6e191e5999c3f607127486c73b37cc1083084a68f705e301c75576`
records zero PG operations and production library writes. The automatic handoff
then reached supervisor validation, which refused
`private-input reviewed source pin differs` before activation. No Stop, producer,
NativeDB export or book move occurred, and current Ransom eligibility remains
unverified. The completed LIVE is not an actual cold130 recovery proof.
The retained local diagnosis `v14/private-input-pin-diagnosis.json` SHA
`56ca6998ec938f0135c667bc11323a40c71d7a2c0e64c1e9f34a33c9e107e938`
proved that only the sender policy pin was stale: supervisor expected historical
`8d3cb8b3…`, while the physical reviewed sender was `57137681…`. Receiver,
native verifier and collector pins matched. No packet bytes or clocks were
modified to turn this refusal into a pass.

Owned cancellation was issued at **10:36:58.963124Z**. One existing final audit
`v14/actual-final-audit.json` SHA
`85d06a17ba3fa25d4c5e1245b520bde0945bb458ce2cbd724049029e19c0cb02`
proved full typed phase Job/Pod union and both primary PG absence first, all seven
strict current Normal `21b9cdc3` Ready/unheld/no-owner controllers, the original
four full Normal Deployment specs and six healthy Pod identities, and retirement
of the watcher and its whole original process group. Coordinator physically
verified that receipt and ended `act-102303-1539685`. The phase is consumed; no
retry, clock reset, recapture or new phase is authorized by this evidence.

Host-only #3750 merged as `709fdd2a` at **10:46:47Z** after the current actual
advisory, required checks and exact independent peer
`e00c164c27466e0051a1a011aafe11ca48d4e42315e5b52d5ce09a7e3183ff69`.
It corrects the sender pin and runs the existing complete supervisor validator
before LIVE, retaining all five physical static-reference checks. The sole
deferred input is an absolute, absent and unbound future LIVE baseline;
post-LIVE validation still requires the complete baseline. The six signed
runtime modules, image, recovery callbacks, ownership guards and clocks are
unchanged. This source correction supplies no new runtime or cold130 proof.


## V15 actual Stop, publisher refusal and cold Normal restoration

V15 retained the exact #3750 full-validator fix, qualified image and clock matrix.
The sole declared same-Normal convergence command returned a Flux CLI failure at
its first app scope. The wrapper did not retain that command's stderr. Fresh
current conditions subsequently showed all seven exact `709fdd2a` Ready/unheld;
one fresh prewatch independently proved the complete current custody, services,
Job/Pod union and both PG absence before arming. No convergence retry occurred.

The new Stop #3751 merged as `5a16a513`; the reviewed actual inverse #3752 merged
as `067abcca`. The original stage arm was **11:01:38.608512Z** and expiry
**11:31:38.608512Z**. A local launch-wrapper integer/string birth-tick comparison
refused before any adapter or API call; correcting only that comparison ran the
same authorized command. Parents were held before all four apps, with no parent
reconcile afterward; all subsequent holds/cache bindings kept the original clock.

Root launched the sole combined operation at **11:09:50.616132Z**. LIVE completed
at **11:11:31.862710Z** in **101.763380289s**, with baseline, delivery ACK and full
foreground GC. Its receipt `v15/live-actual/actual-receipt.json` SHA
`2f15b9f9c9738a04d0c7e52e6519e438a504cb8d2d1a7a4851a858ae9318556d`
records zero PG operations and production library writes. Original byte proof
began at **11:09:53.606416Z**; no new epoch was introduced after delivery.

Conservative activation began at **11:11:37.207555Z**. The owned watcher actuated
Stop at **11:11:47.900071Z**, observed the first actual service stop/window origin
at **11:12:13.173013Z**, and completed all four app actuations at
**11:12:36.345719Z**. SOURCE and Lidarr producers were created; LL/Kavita NativeDB
exporters and MAIN writer UIDs remained null. The publisher command refused with
`FileNotFoundError`: its frozen PATH omitted the existing `hw-ssh` executable.
Retained diagnosis `v15/actual-publisher-path-diagnosis.json` SHA
`27ea51f2d6c79783858245cd74d524d913e4c56dab59c9de353c4d375b1c77f0`
records six inventory calls before that missing executable;
no hardware command or native route capture started. No NativeDB export or book
move occurred. The fresh app capture is a PG books dependency snapshot and does
not provide native all-user reading state. Current Ransom eligibility remains
unverified.

Owned Jobs stopped at **11:12:54.011407Z** and restoration was requested at
**11:12:54.048555Z**. The watcher verified Normal and completed at
**11:13:58.922334Z**, **64.873779s** after the request; the supervisor finished
restored verification at **11:13:59.025435Z**, **64.976880s** after the request.
These actual cold intervals satisfy the new 130s allowance for this run, without
changing or replacing any earlier 50s result. COPY itself refused and had no
production gain.

The single independent final audit `v15/actual-final-audit.json` SHA
`110f6c3342422b2266a40bdd175086de69c20141276b9b97c9f1a96ccb6cd9e0`
proved full typed phase Job/Pod union and both primary PG absence first, all seven
strict current Normal `067abcca` Ready/unheld/no-owner controllers, the same four
Deployment UIDs with exact Normal specs, six healthy current Pods after actual
Stop, and retirement of the watcher and its whole original process group.
Coordinator physically accepted that audit, independently retained the two
restoration timings, and ended `act-105808-1567097` after **11:17:50Z**. The phase
is consumed; no retry, new phase or reuse of its byte proof is authorized.

## V16 approved Normal inverse misclassified as drift

Host-only #3753 merged as `6f302ae8` at **11:29:16Z**. It binds the eleven
publisher tools and their physical hashes before LIVE and passes the same PATH,
including the existing `/home/dev/.local/bin`, through LIVE and the supervisor.
Independent production peer `3606ab9033d079ed0fae1d338c47c855ee176c393246c8f037b657ddbfc0ed49`
and test-only successor peer `f966892226fde7acefc879896ae78ba38299ede6f426796920f614f3fc78db83`
passed; the runtime image, six modules, collector, supervisor, recovery and clocks
remained unchanged.

V16 began at **11:43:54.961228Z**, with original staging expiry
**12:13:54.961228Z**. One current Normal convergence and fresh prewatch passed;
parent-first and four app holds kept services Normal. Stop #3754 merged as
`77e2e570` at **11:45:32Z**, and the reviewed actual inverse #3755 merged as
`2253339e` at **11:48:44Z**. Before cache sealing, `guard_phase_git` classified
that approved Normal revision as unexpected Stop-manifest drift. The retained
`git_drift_first_sha` and `git_drift_current_sha` both equal the approved inverse,
and all six recorded drift hashes are the restored Normal blobs. The inverse PR
view and fresh main lookup straddled the merge. Authority was revoked at
**11:48:44.457952Z**; the stop file's recorded birth is
**11:48:44.458585239Z**, consistent with the guard's own stop-file creation.
The next cached tick relabeled the recovery reason `original_restore_due`.
Missing a sealed receipt itself is permitted by the staging branch and was not
the trigger.

The bind-inverse adapter refused at its initial live-authority guard, before
sealing or API calls. No cache seal, activation, LIVE, runtime Stop, COPY, NativeDB
export or book move occurred. The sole watcher completed owned Normal recovery
at **11:49:44.998520Z**. The one independent absent-cache audit
`v16/actual-final-audit.before-seal.json` SHA
`f8752e7df9999f5bab2d31fa2dbef858593519ced51030eada2f1011ea23f35d`
proved the full typed Job/Pod union and both primary PG absence first, all seven
strict current `2253339e` Ready/unheld/no-owner controllers, unchanged four full
Normal Deployment identities/specs/generations and six healthy Pod identities,
and the retired watcher with its whole original group empty. Its narrow context
successor binds the exact unmerged draft and actual inverse while explicitly
preserving cache absence and lack of runtime authorization. No cold timing proof
is supplied by this staging-only withdrawal, and no retry or clock reset occurred.
Root physically accepted that audit and its focused absent-cache peer
`fa6af6222ae055c6c143a0e2c4e448c0031b4962e13becb4321039f38eb1248f`,
then ended `act-114152-1601991`. The phase is consumed.

The three-file host fix #3756 merged as `76f22cce` at **12:06:36Z**, exact source
head `3c64c232708628a466237b3edcd34ab46e350c78`. Only a fresh main matching all
six frozen Normal blobs can refresh the exact inverse. The same observed head,
PR identity, Stop/head contract, Stop-to-head and Stop-to-merge-to-main ancestry,
and merged full Normal blobs must all pass before the existing cached tick.
Every failed proof retains original drift revocation and writer-first recovery.
Three focused controls passed under nice 19 in 0.246s; all required, writer and
COPY source checks passed. Independent source peer
`16bc29fd2e6471c94b9012516e746f5e17602a7b9e7d3adafaccd70380c571fb`
passed. Actual advisory run `38050637764` failed after initialization without a
substantive review (313ms, `is_error: true`, no model usage); Root read the actual
error-only comment and empty inline findings and recorded the missing-review
disposition in comment `6097312417`. This is not a clean Claude review, and no
advisory retry occurred. Recovery, service and staging clocks, runtime image,
six installed modules and custody guards remain unchanged.

## V17 actual Stop, bounded restoration, no COPY gain

V17 used the same selected-two scope, qualified `628e97b8` image, complete eleven
publisher siblings/nine required programs and original clock matrix. Its phase
`6689b3642786403fbbc4aad39b7ff9ed` was armed at **12:28:18.184227Z**, with original
staging expiry **12:58:18.184227Z**. The single fresh prewatch passed all seven
strict current Normal `76f22cce` controllers; no same-Normal convergence was
needed. Parent-first and four app holds retained actual Normal services. Stop
#3757 merged as `29bee291` at **12:30:19Z**; inverse #3758, exact retargeted head
`e26d975ba8d7a066c6f7aad15408c2b79f185d1a`, merged as `bc4708fc` at
**12:40:13Z**, preserving the full preStop Normal tree.

Both actual current-head Claude jobs ended in startup failure without a
substantive review: Stop run `38050911544`/job `114209680105` reported 491ms and
empty model usage; inverse run `38052212963`/job `114213446757` reported 393ms
and empty model usage. Root read the actual error-only comments and terminal
metadata, required checks, exact manifest/ancestry proofs and distinct
Pathfinder reviews. Root approved only those exact bindings through the existing
disposition mechanism. The Stop record was merge-policy-only; the inverse
approved descriptor SHA
`e844e291e62b2b16e51adf4c5955c6f5305f05465908f44e363280192e036de2`
was supplied through the existing paired optional supervisor fields. These
failures are not clean Claude reviews or a future review exemption.

The first existing readonly seal invocation refused with `ValueError`; its
specific stderr was not retained. Its subprocess inherited a stale GitHub
token. Root's identical direct binary failed with the old environment and
succeeded with the freshly mounted token, and Root authorized exactly one
same-command readonly retry with that refreshed environment. The retry passed,
producing cached receipt SHA
`9f3a4b9bea863035c80fa071153486d4f4d69bede0957eccfb789cd2636262d1`.
The original seal's exact HTTP 401 is unproved. No source, safety predicate,
hold or clock changed during the retry. Full preLIVE filesystem/tool/disposition
validation and the single cache-readiness observer passed before Root's sole
combined invocation.

LIVE completed at **12:45:58.480195Z** in **98.29430150985718s**, with complete
baseline, ACK and foreground removal of its exact Job and all owned Pods. Its
receipt `v17/live-actual/actual-receipt.json` SHA
`97721d5777a52a31193eb72a781b64e5233f9637a96a3b25ea35e26b9fb7c6f2`
records zero PG operations and production library writes. Original byte capture
began at **12:44:23.275719Z**; the automatic adapter retained that epoch and
directly entered the supervisor without a postLIVE model turn or observer.

Conservative activation began at **12:46:04.234441Z**. The watcher began Stop
actuation at **12:46:11.809829Z**, observed first actual service Stop/window at
**12:46:37.393427Z**, and completed all four app actuations at
**12:47:00.431999Z**. The PG fence was established at **12:47:13.348567Z**.
SOURCE and Lidarr producers had actual UIDs; LL/Kavita Native exporters and the
MAIN writer remained uncreated. The supervisor refused a bounded reviewed
publisher command, with private output retained; COPY did not succeed and no
book move or NativeDB export occurred. Current Ransom all-user eligibility
therefore remains unverified; historical reading dates do not replace that
missing current export.

Owned Jobs stopped at **12:47:34.435474Z**, the SOURCE reader stopped at
**12:47:34.459009Z**, and restoration was requested at **12:47:34.467956Z**.
The watcher verified Normal at **12:48:51.754869Z**, an actual cold interval of
**77.286913s**, within this run's 130s reserve. The supervisor completed at
**12:48:51.955456Z**, **77.487500s** after the request, recorded separately.
Neither measurement replaces historical failed or untested 50s outcomes or
guarantees a future cold recovery.

The one final audit `v17/actual-final-audit.json` SHA
`9c81016b96329fb5fa0196c97a02cf5460670ae7467064e60de2cf0e76c41520`
proved full typed phase Job/Pod union and both primary PG absence first, all
seven strict current Normal `bc4708fc` controllers Ready/unheld/no owner, the
same four Deployment UIDs and exact restored Normal specs, six healthy current
Pods, and retirement of the watcher and its whole original group. Deployment
generations and Pod identities changed as expected after real Stop. Root
physically accepted the audit and ended `act-122418-1617618`. V17 is consumed;
no retry, new phase or reuse of its byte proof is authorized.

The retained V17 publisher diagnosis
`v17/actual-publisher-image-pin-diagnosis.json` SHA
`380452f8fe9c9009426d9a6898d162ef61aed37a4d2d8b8f9edc56839a015755`
records 25 trace events and 21 actual commands, all exit zero, over 14.7625s.
The existing Lidarr bridge still required the historical `fdc358fc` image while
the actual declared ready receipt pinned qualified `628e97b8`. Its source-backed
failure region is the native Pod/image guard before native execution. The exact
Pod response was not retained, so this record does not claim an independently
replayed Pod comparison. The publisher reported `Refused` with null refusal
text because the bridge uses a distinct exception class. Both Native exports
were absent, MAIN UID was null, and moves were zero. Root authorized only the
narrow bridge literal/current publisher closure correction before any new phase.

The three-file correction #3759 merged at **13:04:51Z** as `1a78ce61`, exact
reviewed head `8000db2d99758c748409949e5e1747dcbe8ec027`. It tracks the existing
bridge with the qualified `628e97b8` guard; the meaningful image admission/drift
control passed in 0.002s. Independent source and prospective materializer peer
`418e84254f7703922eeca04d8eb5b5c88dd2be015cdf74c5628554a8a18250ba`
passed: the next materializer copies exact Git bridge bytes, preserves the other
ten siblings/eight required programs and derives all nine actual checksums.
All relevant checks passed or intentionally skipped. Actual advisory run
`38053893403`/job `114218334359` failed at startup (340ms, empty model usage,
error-only comment `6097742356`, no inline findings); Root read it and recorded
the unavailable-review disposition in `6097796405`. This is not a clean Claude
review. Runtime modules/image, clocks and historical bundles remain unchanged.

## V18 actual outcome and safe restoration

The fresh closed V18 packet used source `1a78ce61`, exact Git bridge SHA
`827539b27a510dd55c9b9dfdf601cfb4e68e500114c7e5498bd3505ee5c829bc`,
all eleven coherent publisher siblings and nine derived program hashes. The
other ten sibling bytes and eight required program hashes were carried unchanged.
The qualified `628e97b8` runtime, selected-two scope and clock matrix were unchanged.
Root's one same-Normal convergence command returned exit 1 on its existing Flux
acknowledgment timeout. Subsequent actual controller state and the one fresh
prewatch passed on current `1a78ce61`; convergence was not repeated or reported
as a successful command. Prewatch SHA was
`14adf4b39a4ef751468e09e26516379122204b159aba70476d0f5b7d2a3f2f07`.

The original watcher arm was **13:16:56.643781Z**, with immutable staging expiry
**13:46:56.643781Z**. Parent cluster, parent cluster-apps and four apps were held
and drained in that order. Stop #3760 head `67560f8a` merged at **13:25:51Z** as
`e9ed7a2d`; inverse #3761 actual retargeted head `361a084c` merged at
**13:32:21Z** as `55f3cac89ba58bd139ef60d5c286cc4393b279ef`. Current relevant
checks passed or intentionally skipped. Both actual advisory jobs failed before
substantive review: Stop run `38054456889`/job `114219949575` recorded 334ms,
and inverse run `38055696071`/job `114223620428` recorded 388ms; both had
`is_error: true`, empty model usage and error-only comments, without an underlying
reported reason or inline findings. Root read the retained records, distinct exact
independent peers and graph/source proofs and recorded public dispositions
`6097963157` and `6098019531`. These were unavailable-review dispositions, not
clean Claude reviews. The approved inverse descriptor SHA
`14c410d13c15fd4fde47abbe914ff4dc14e7496b3a62166b779026ab926cf49b`
bound only that current head/job/phase through the existing optional review gate.

One original bind/seal passed with fresh mounted GitHub credentials and the
existing kubeconfig/PATH. Cached receipt SHA was
`74439265a457deb59f950ee97f31bea7ebb07c89dd0ce78a09d1dd1813446399`.
The full local preLIVE validator and Root's one readiness observer passed before
the sole combined launch. LIVE completed at **13:39:16.489425Z** in
**91.69835686683655s**; foreground removal of its exact Job and all owned Pods
was verified at **13:39:17.087832Z**. LIVE receipt SHA was
`6618d4ac88886d32ce03b6ba45914b3a08bccce0460c36f43d9b82b29841b07e`.
The original earliest byte epoch remained **13:37:48.232195Z**.

Conservative activation began at **13:39:22.356557Z**. Watcher Stop actuation
began at **13:39:23.312560Z**, first actual Stop/window was observed at
**13:39:48.192772Z**, and all four app actuations completed at
**13:40:11.654923Z**. The PG fence was established at **13:40:24.252458Z**.
The supervisor refused a bounded reviewed command and retained private output.
SOURCE and Lidarr producers had actual UIDs; LL/Kavita Native exporters and MAIN
remained uncreated. No COPY move or NativeDB export occurred. The combined
process's exit 0 records completed recovery, not COPY success. Current all-user
Ransom eligibility remains unverified, and its whole-folder hold remains in place.

Owned Jobs stopped at **13:41:06.529629Z** and the SOURCE reader stopped at
**13:41:06.548648Z**, before the restoration request at **13:41:06.553414Z**.
The watcher verified Normal at **13:42:16.120781Z**: **69.567367s actual cold
recovery**, within this operation's 130s reserve. The supervisor finished at
**13:42:16.717406Z**, **70.163992s** after the request, recorded separately.
These observations retain the original origins and do not guarantee a future
recovery interval. Supervisor receipt SHA was
`093032da4de6b932bf2bb3d0947c965be150d2bc905a6151ee0615e3c17f2584`;
final watcher SHA was
`a06b49c14aaf94e5e70e0d18cca501cb43b940c4cb02b7c5af71fc248e19da3f`.

The one independent final audit `v18/actual-final-audit.json`, SHA
`3bcea49113d279a7229fa1806f2ca28e53e68fb34cbceece375290bec3a99394`,
proved full typed phase Job/Pod union and both primary PG absence first, all seven
strict current Normal `55f3cac8` controllers Ready/unheld/no owner, the same four
Deployment UIDs with exact restored Normal specs, six healthy current Pods, and
retirement of the watcher and its full original group. Actual activation/Stop were
present; Pod identities and Deployment generations changed as expected and were
not claimed unchanged. Root physically accepted the audit and ended
`act-131345-1658454`. V18 is consumed; no automatic successor, recapture or
reuse of its clocks is authorized.

The retained V18 diagnosis `v18/actual-publisher-scope-drift-diagnosis.json`, SHA
`07b94d623a63d3e424d475228556dcab9000bd28a57708f0be928945a439c857`,
identifies the exact refusal at `publisher-scope-capture.py:361`: configuration
differed from the reviewed pre-window scope. The 60s collector completed its
retained command sequence in **34.9337s**, with 84 trace events comprising 66
actual subprocesses, all exit zero, and 18 telemetry events. Expected stable
scope digest `72f0ab043bfabbc0211e412e04c7a1313b5d4fe36f8e61d2bc4558f5b5900e56`
differed from actual digest
`34def493c2901c49161ad7f9ed14ad701208e198a7a3233e909abe0b92e22982`.
The only stable-field delta was Sonarr series ID **1082** and its normal write
path: series count **1067 → 1068**, no removed items or paths, all prior series
and resolved entries exact, root folders/custom hooks unchanged, and the other
seven publishers' stable rows exact. The retained capture's physical resolution,
alias checks and alternate NFS/client mapping checks passed; the added path was
disjoint from EBooks. The series' origin is not established by these snapshots;
there is no causal reading-list or EPUB claim. This diagnosis used retained
inputs only, with zero new captures, API calls or production writes. It is not
fresh authority for a later operation. Any prospective paired scope-digest
binding requires Root's explicit review of this exact delta and the existing
fresh capture, before/after stability, alias, ownership and original-clock gates;
arbitrary future drift remains refused.

## PVC paging investigation and Slskd capacity correction

The owner reported repeated book/PVC pages, so all prospective COPY phases were
held. The read-only event snapshot at approximately 13:55Z contained no current
attach, mount or PVC warnings in downloads/media/frontend. Event retention and
missing indexed Alertmanager logs limit historical attribution; this snapshot
does not establish that earlier pages did not occur.

Retained alert-responder Loki records contain two actual `paged:` sends in the
24-hour window ending 2026-10-10T14:10Z, at **05:30:29Z** and **11:40:26Z**. The
seven-day query returned the same two, without truncation. Both were
`KubePersistentVolumeFillingUp (downloads)` and both diagnoses identified
**downloads/slskd**, not a book claim. Cooldown polling lines were not counted as
pages. These records count responder sends, not all direct Alertmanager/Pushover
deliveries. Evidence SHA:
`203886cbf65d347f1c4ada283f873c286058352438aff8725db88072010593b8`.

A read-only inspection of Slskd's mounted `/config` measured **98% usage** and
**49,592 KiB available**. Its 2Gi Ceph claim contained application state:
`events.db` **870,096,896 bytes**, `transfers.db` **582,361,088 bytes**,
`shares.local.bak.db` **239,513,600 bytes**, plus **244,844 KiB** of migration
backups. `/app` is an emptyDir, so inspecting it would miss the full claim.
No database contents were read, no data was pruned or vacuumed, and no restart,
PVC mutation or NAS migration was performed. Occupancy/file-size evidence SHAs:
`127378fcc60bc8cbbd472efad17af59b58f1ccbde15198e8625812dc52cfdb05`
and `082785d3aede8b34ce813d17900504b3d0af08eb59a4962a3f7b18b6e3d6bb1c`.

The current `ceph-block` StorageClass enables volume expansion and uses ext4;
the Ready RBD CSI controller includes `csi-resizer:v2.2.1` and
`cephcsi:v3.18.1`. No open Slskd PR, matching live AgentSession or active matching
upgrade-work-order was found in the read-only ownership check. Existing matching
work-order records were terminal historical incidents. Support/ownership
evidence SHA:
`7880bf5360232258603c986a5632b160b50d405b74e4a29690a6859b44ea6e73`.
The prepared capacity/support record, including the actual CSI-resizer projection
and before-change PVC/PV/Pod identities, has SHA
`b59f93628c9a2b2c5b6edbb5dfdbeefa3e4e3bb3ab30d9981e12c99400fced59`.

Root authorized the single GitOps change `slskd/ks.yaml`:
`VOLSYNC_CAPACITY: 2Gi → 4Gi`. The existing shared component applies this scalar
to the same prune-protected claim and VolSync restore capacity. It changes no
claim identity, storage class, mount or app configuration. At preparation,
merge/deployment and verification of expansion, health and alert clearance were
pending. The actual deployment and clearance are recorded below.

The retained V18 volume mapping (SHA
`1372665cbee7889a4dc29a73ce964512d64e850328440e036b61e397027153de`)
shows zero scratch PVCs: LIVE/SOURCE use read-only direct gasha NFS plus an
emptyDir; the Lidarr reader uses the existing read-only Lidarr claim on its
declared node. LL/Kavita exporters and MAIN were never created. The ordinary
midnight VolSync clone claims had successful provisioning records and backup
logs reporting zero errors. None of this establishes the cause of any
unidentified phone page. The offered gasha NAS is already the book-read source;
no new scratch migration is authorized or required by the measured Slskd issue.

### Actual expansion and alert clearance

Ops PR #3762 merged as `b8b7fd5299dad6b15fa4b6d86566400e48806699` at
14:21:43Z. The successful current-head rendered CI diff changed only Slskd's
capacity substitution, existing claim request and restore capacity from 2Gi to
4Gi. The Source → cluster-apps → slskd reconcile chain completed at 14:22:46Z.

The 14:23:55Z read-only verification proved the same PVC
`a08e09bf-86cd-4b69-acdc-7b07815e5dee`, bound to the same PV
`pvc-f491a7b9-0b92-4424-ad92-20caf50f2de4`, had request/capacity **4Gi**,
remained Bound and had no resize conditions. `/config` had **4,054,752 KiB**
filesystem capacity and **2,111,416 KiB available**. The same Pod UID
`16e11775-0140-4406-9e35-a30bf27f18b7` remained Ready with its unchanged
**35** prior restarts; `/health` succeeded. No restart, pruning or direct storage
mutation was needed. Actual verification SHA:
`128b714884f31f7f1d092024410f0e152370f3aac25f58867c3b0d4df4ede206`.

At 14:23:55Z the monitor still sampled the prior filesystem size and both
volume alerts remained active. The single later check at **14:27:31Z** showed
**4,152,066,048 bytes** capacity and **2,161,086,464 bytes available**
(approximately **52.05% free**); the exact Slskd PVC volume-alert query returned
no series, and Alertmanager returned HTTP 200 with no matching active,
silenced or inhibited alerts. Clearance receipt SHA:
`582b9c1346924b07766dd69de8cc69b733ad6d76121bc775e35cea331e79bebb`.
This closes the measured Slskd capacity incident and does not authorize a new
book runtime phase or establish any COPY gain.

### V19 actual outcome: private review artifact refused before activation

V19 used the qualified image and unchanged clocks, with only the two explicitly
approved publisher-scope digest leaves rebound. Stop #3763 merged as
`a86abab6ec1c6e4dff22ca587ce9be91418bef2f`; its exact inverse #3764 merged as
`3f46cd0cedd47f785c65be772b6223a3a7fdeda0`. The original staging clock remained
14:43:38.048338Z → 15:13:38.048338Z.

The sole combined invocation completed LIVE in **86.581288 seconds**, with a
complete baseline, ACK and full foreground Job/Pod cleanup (LIVE receipt SHA
`7ea33c3603c1812c8885a67b5b1fe318b56f1cdeadbdde1511a5a33c484d13e6`).
The supervisor then refused before activation: the approved exact inverse
review artifact was mode **0644**, while the existing `cached_source.read_private`
requires private permissions. The independent peer receipt was also 0644 and
would fail the same guard. Their bytes, owner and single-link identities matched
their pins; the local byte-reader validation had missed this physical permission
requirement. No consumed artifact or clock was changed after refusal. Diagnosis
SHA: `22c70274b5c21925f41070f85e368b041763a7a6288acfd9fada252ae7d07393`.

All five producer UIDs and MAIN remained null: **zero book moves**, no Stop
actuation, and no LL/Kavita Native exports. Recovery request
15:05:45.560381Z → watcher Normal verification 15:06:39.402743Z took
**53.842362 seconds** in staging; the supervisor interval was **54.554692 seconds**.
This is not a cold recovery test. Current all-user Ransom eligibility and broader
#831 native mapping/accounting remain unverified because no fresh Native inputs
were produced.

Root's corrected final audit passed at 15:08:06Z, SHA
`f8e36aa4c55ccf393144132c977bd9224ed7345140c219c14d299208216a1480`:
full typed phase Job/Pod union and both primary PG leases absent first, all seven
controllers strictly current Normal/unheld/owner-free, unchanged four deployment
UIDs/generations/full specs and six healthy Pod identities, and the original
watcher group retired. The initial argument-free audit invocation failed before
producing a receipt; its partial output directory was preserved before the
corrected invocation. Combined exit 0 denotes completed recovery, not COPY success.

### V20 actual outcome: required publisher profile absent after Stop

V20 preserved the image, programs and clock matrix. Its newly consumed review
artifacts were created exclusively with mode 0600, and the actual private reader
and review loader passed before LIVE. Stop #3765 merged as
`b2064b099befbf857bf8166ac774ad272f9c0fa5`; inverse #3766 merged as
`98722be545d32e0c40625167539f4447caceff48`. The original staging clock remained
15:23:04.998272Z → 15:53:04.998272Z.

LIVE completed in **86.566465 seconds**, with a complete baseline, ACK and full
owned Job/Pod cleanup (receipt SHA
`6e940889e3e92d37bff7712ce9e5a3f4c8d0cf3b2c8006725bf477b7dbdcedae`).
Actual conservative activation began at 15:43:49.495802Z; the first service Stop
was observed at 15:44:23.601009Z. SOURCE and the Lidarr reader were created. The
publisher capture completed and passed its reviewed scope digest, then
`publisher-scope-guard.normal_profiles()` raised `FileNotFoundError`: the adjacent
`publisher/approved-normal-write-profiles.json` was absent. The guard requires
its existing exact SHA
`a5f076a6131d94820791ee040510d6544a2fccd4aa0c5e5e5449555b01e490e0`.
Retained diagnosis SHA:
`61835aaaefb394fe3c9663229bd299a7455c66561fa84d9017ce183c4064a378`.

MAIN and the LL/Kavita Native reader UIDs remained null: **zero COPY book moves**,
no MAIN ACK, and no fresh all-user Kavita export. The source counts of 1,970 EPUBs
and 4,845 files are scope observations, not verified canonical-work or reading
eligibility accounting. Current Ransom eligibility and broader #831 native
mapping/accounting were not established by this runtime.

Restore request 15:45:41.430026Z → watcher completion 15:47:07.364470Z took
**85.934444 seconds**, within the unchanged 130-second reserve for this actual
cold operation. The following Normal re-verification timestamp was 19 microseconds
later, giving **85.934463 seconds**; the supervisor interval was separately
**86.757161 seconds**. These measurements do not guarantee a future recovery time.

Root's sole final audit passed at 15:47:29Z, SHA
`edbeb69c501e48982b8ebb47f198d4b8b5f0de84653eebbece155b010ad659a8`:
full typed phase Job/Pod union and both primary PG leases absent first, all seven
controllers strictly current Normal/unheld/owner-free, the same four deployment
UIDs with exact restored Normal specs, six current healthy Pods, and the original
watcher group retired and empty. Deployment generations and Pod identities
changed as expected after actual Stop. Archived cache and activation files
remain; they do not represent active authority. Root ended the scoped activity.
Combined exit 0 denotes completed restoration, not COPY success.

### Fresh Ransom eligibility premise after safe restoration

After V20's safe closure, a separately authorized use of the existing read-only
Kavita export route passed stable Pod, whole-folder and database-sidecar checks.
The fresh all-user target evidence establishes the 30-day reading and file idle
premise. Current Library/Series/Volume/Chapter/File IDs remain
`1 / 1650 / 1800 / 3358 / 3570`, the seven proposed catalog cells still match
their reviewed before-values, and no competing same-library alias was found.
The private aggregate seal is
`e6e88737ddf50f93c8e233e2acb9dd6ac86843dd6636576d7c3a6ac4764f8937`;
raw user identities and reading locations are excluded from this report.

The target's five reading-state tables match the prior production capture,
the immutable Kavita image identity matches, and all six folder stat rows match.
Applicability addendum SHA:
`572f884f14426ae94fa780803f232726ca40073688e558fe463610171a9cc2a2`.
The earlier actual Native v13 scanner result remains historical; no new scanner,
catalog writer or strip was run, and no equality claim covers all current global
database tables. The seven-cell catalog writer still requires the existing
PLAN-074 owner ruling and its guarded preservation procedure. The whole-folder
hold and global hourly gate remain in place.
