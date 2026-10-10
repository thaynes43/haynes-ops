# Book recovery and staging evidence — 2026-10-10

The generic services-running Normal recovery prerequisite passed in 28.306327s
against its original 50s budget. No production COPY or cold stopped-service
recovery result is claimed. Cached staging v7 was withdrawn without LIVE, cache
activation, Stop application or book writes after inverse CI exceeded the
original full LIVE200s plus bind30s cutoff. Current Normal was freshly restored.

## Current recovery prerequisite

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
cache seal → fresh LIVE → bind → separate runtime GO. A parent apply over held
children is forbidden; fresh held-parent containment replaces that failed action.
The inverse must have the reviewed Stop head as its direct parent before arming.

## Bounded future staging only

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
