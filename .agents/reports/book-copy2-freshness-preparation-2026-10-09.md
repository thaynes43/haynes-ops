# Fresh COPY2 preparation and expiration gates — 2026-10-09

This is a source audit and preparation map, not a launch approval. No fresh
SOURCE/MAIN packet is bound. The original full LIVE capture failed; the successful
[diagnostic](book-census-native-diagnostic-2026-10-09.md) measured different read-only
operations and does not establish a downstream COPY duration. Production services,
acquisition and schedules remain normal.

The conditional scope is the app's
[two-extra Pathfinder profile](https://github.com/thaynes43/haynesnetwork/blob/8ed34646/.agents/plans/074-pathfinder-two-extra-profile.json):
two moves, three distinct selected byte paths, one unchanged LL keeper, first
retention proof before the remainder, and restoration before follow-on scans. The
old 21-move/41-path selection and consumed packets cannot be reused.

## Serial obligations after a successful LIVE lifecycle

The reviewed private supervisor source is
`/home/dev/work/hn-825c-fence-prepared/copy-bound-supervisor-prepared-v1/copy-phase-supervisor.py`
(SHA `647373d36e594cabadf5b82cb8daf143d1fce6455b830b1b9a035c3e9b883c2a`).
Its prepared operational contract is SHA
`9efc9a7f7c73a0800c46e25e93a311438a1302eac11213a650d039e020092f40`.
These are reviewed generic preparation references, not a currently executable
fresh stage. Lines 776–784 fix the following order:

| Obligation | Required evidence and ordering | Existing cap, not measured duration |
| --- | --- | --- |
| Real maintenance fence | Reviewed GitOps inverse and armed independent watcher; actual stopped LL/Kavita, six schedules suspended, Libretto acquisition off, unknown writer refusal | Restoration trigger 170s; supervisor abort 250s/service ceiling 300s |
| Native Lidarr and SOURCE | Exact native ownership; SOURCE's own primary PG16 read-only SHARE locks; full app rows/schema/counts captured after stops | Native Lidarr at most 115s; SOURCE readiness 10s, then shared abort clock |
| SOURCE native input | Actual Running Job/Pod/image/spec/module/root proof, current phase and backend health; immutable private input delivery | Delivery at most 12s |
| Initial eight-publisher gate | All current configurations, paths, physical backing, installed source and before/after identities; SOURCE's complete stat census can proceed concurrently | Whole publisher capture at most 60s |
| Stopped LL, then Kavita | Full stable copied DB/WAL/SHM and complete LL SQL; full Kavita reading/curation/lock/dependency exports; native identity before/after retrieval | Each reader readiness at most 25s; retrieval has bounded caps |
| SOURCE finish and reader retirement | Complete raw path/fingerprint/permission census; seal both vendor payloads; exact UID Foreground cleanup and full absence before MAIN | No partial census or live SQLite reader adoption |
| Final eight-publisher gate | Fresh independent complete capture; no publisher refresh after MAIN creation | At most 60s capture; writer lease is independently shorter |
| Trusted assembly | Every SOURCE fingerprint equals sealed LIVE; preserve original byte clocks; full app/LL/Kavita protections; exact eligible two-entry selection and snapshot hash | Assembly at most 25s |
| MAIN delivery and ownership | Exact waiting MAIN; complete independent mount/state/selected access identity bridge; three raw proof files with hash-bound ACK | Bridge at most 10s inside host delivery at most 12s |
| MAIN validation and retention | Own independent PG16 SHARE transaction and full app-row comparison; selected whole SHA/OPF; first retained proof; remainder; complete final fingerprints | Earliest immutable writer/evidence deadline |
| Restore first | Actual writer-first/all Job/Pod absence, SOURCE release, restore request and independent restoration proof | No post-stage scan can delay restoration |

Authoritative app/vendor dependencies and SOURCE fingerprints cannot be precomputed
while production remains live. Their original start must follow the real fences.
Only generic code, exact manifest/inverse preparation, selection previews and
read-only source/module reviews can be staged beforehand.

## Expirations are independent and never restamped

The frozen LIVE profile remains host start plus **180s** for collection and **200s
total** including cleanup. The signed
[consumer contract](../../scripts/book-copy-writer/bound-census-contract.json)
instead expires byte evidence at its **original capture start plus 300s**.
[Bound assembly](../../scripts/book-copy-writer/bound_census.py) preserves both
original start and completion; fresh stat validation does not renew them.

The writer takes the earliest supplied deadline, every dependency's original
start/check/quiescence plus 300s, and original byte expiry
([writer](../../scripts/book-copy-writer/book_copy_writer.py), lines 282–287;
[dependency expiry](../../kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert/epub_copies.py),
lines 50–62). The final publisher lease also clamps MAIN to the minimum of
first observed stop plus 250s, publisher capture start plus 65s, and publisher
finish plus 35s (supervisor lines 88–94, 509–526). Delivery, validation and both
moves consume that same immutable lease. None of these deadlines is a promise
that the workload will fit. A completed LIVE artifact with insufficient residual
budget must refuse COPY and remain an expired historical artifact.

## Representative publisher cost is complete work, not a polling delay

The retained historical complete eight-publisher observation at
`/home/dev/work/hn-825c-fence-prepared/lidarr-native-full8-live-04/`
took **32.4162s** host capture, with a **32.114668s** proof start-to-finish span.
The scope SHA is `3e657288ffde507acf0fbc4b4afbd7f7be7521244b65315da9918460f3660ac8`;
trace SHA is `1bd9b28056983095cf895d686a87cd60571f7a163955a9c8b951cfde81524f26`.
It is stale evidence of cost, not a fresh writer permit.

The trace contains 66 native command events and 16 API timing events. Command
and native-file suboperation timings total about 30.017s: inventory 4.030s,
Radarr 7.708s, Sonarr 2.165s, Lidarr 9.669s, and the other five routes 6.445s.
The native file suboperations are nested, so this sum is an approximate breakdown,
not an exact nonoverlapping wall-clock decomposition. Radarr's full movie GET
read 43,990,663 response bytes in about 2.1s before and again after; its physical
resolver handled 9,435 paths. Lidarr's physical resolver handled 7,682 paths and
took 6.997s. These were complete inventories, not selected book lookups.

The pinned collector uses serial publisher routes, each ordered before capture,
two complete physical resolutions, mount proof, then after capture. Independent
API GETs inside Radarr/Sonarr and other routes are already concurrent. There is
no fixed 32-second polling sleep to remove. Parallel routes or shared descriptor
resolution could be separately reviewed, but require exact before/after and
physical identity preservation, bounded queues, cancellation and full final
inventory checks; no such change or repeat measurement is authorized here.

## MAIN has additional unmeasured gates

The delivery identity bridge performs **two complete file walks** under its 10s
component cap. The writer then performs **five further complete file walks** for
this two-extra stage: before/after selected validation, before the first move,
after first retention, and after the remainder. The diagnostic's 7.043s collector
walk has different checks and cannot be multiplied into a measured MAIN runtime.
Neither the bridge cap nor publisher lease should be widened from that inference.

Every folder/file in those five writer walks currently calls
`PrimaryShareFence.health()`, an actual PostgreSQL query. Using the observed
4,816 files and 2,077 folders gives about **34,465 health queries** across five
walks, before extra selected-read and syscall guards; this is a structural count,
not a latency measurement. The fence creates one connection once, begins one
RR READ ONLY transaction, takes both SHARE locks NOWAIT, and never reconnects,
commits or rolls back until final cleanup. PostgreSQL locks normally remain until
transaction end. [PostgreSQL16 locking documentation](https://www.postgresql.org/docs/16/explicit-locking.html).

Uncached real health checks immediately before retained-link, original-unlink and
manifest publication, and before/after first-retained verification, must remain.
The design explicitly says PostgreSQL health at every file boundary
([DESIGN-028 lines 1869–1876](https://github.com/thaynes43/haynesnetwork/blob/b48e62eb4/docs/designs/028-integrations-tab-goodreads-requests.md)).
No owner per-query ruling was found; changing stat-only entry callbacks therefore
requires explicit design clarification and root architecture review, not a silent
cached health result.

Root ratified preparation of a distinct nonmutating scan guard with absolute
deadline, original connection identity and local closed/broken/INTRANS checks,
plus actual SQL at most one elapsed second apart while scan guards advance, and
mandatory uncached actual health before/after each full walk and every
selected/retained read. Actual write-boundary health remains uncached. A blocked
NFS call can still delay a synchronous probe; original process deadlines and
refusal/cleanup gates continue to apply. A broken psycopg connection is closed;
[Psycopg connection documentation](https://www.psycopg.org/psycopg3/docs/api/connections.html#psycopg.Connection.broken).
Undetected loss during a read must never allow proof adoption or mutation: the
post-read probe must fail on that same connection, with no reconnect or retry.
Required finite negatives include backend termination, same-PID rollback, replaced
connection, missing SHARE, standby, expiry during reads and source replacement
during the final health/network gap. The concrete plan and normative clarification
still need independent review and root's final ratification before implementation.
No code, deadline widening or runtime is authorized by this note.

Root's concrete transaction-identity preparation adds one cryptographic phase-bound
nonce, `SET LOCAL` once after `BEGIN`, privately tied to the original connection and
PID. Every uncached health query must validate that exact nonce as well as both SHARE
locks, read-only and primary status. It must never be reset after loss: rollback plus
`BEGIN` on the same PID is a required refusal fixture. A timestamp alone is insufficient
because collision or clock changes can obscure transaction replacement. The nonce
affects only the owned read-only transaction and private process state; it persists
no book data/configuration and appears in no public logs. This is preparation only.

The concrete implementation plan is limited to the manual bound mode:

1. Preserve `PrimaryShareFence.health()` as an uncached actual query. Bind original
   connection/PID/phase/transaction nonce once; add a distinct `scan_guard()` with
   local state/deadline checks and time-bounded periodic actual health. Do not
   reconnect, retry, reset nonce or re-BEGIN.
2. Use that exact owned scan guard only inside complete nonmutating file-stat
   walks. Actual `health()` must bracket each walk. Preserve all five walks,
   complete path/decimal fingerprint comparison, caps and first-retention sequencing.
3. Keep actual health on every selected/retained whole-file read before and after
   byte IO, with final descriptor/path checks following the last network query.
   Keep the existing actual link/unlink/manifest checks and final local syscall
   identities. A broad `health()` cache is prohibited.
4. Keep SOURCE/MAIN SQL and mutation on the owning thread. Never share a psycopg
   connection with LIVE readers or parallelize selected COPY reads through a
   worker callback. LIVE's two readers have no PG client; three selected paths
   are small and stay sequential.
5. Add finite PG16/local fixtures for normal read availability and blocked writers,
   bounded actual-query counts under a fake clock, same-PID rollback plus BEGIN,
   connection/thread substitution, lost locks/primary/backend/nonce, expiry, and
   a replacement during the last query/local identity gap. Prove zero destructive
   syscalls before admission and truthful halt after a verified first retention.

This plan does not remove or widen the independent two-walk MAIN delivery bridge.
Its 10s cap still needs relevant completed validation before a disruptive stage;
the unrelated diagnostic does not establish a bug or permission to extend it.

## Fresh preparation artifacts still required

- Reviewed collector/entrypoint, finite proof and signed-image compatibility;
  unique unused LIVE phase/output, complete expanded corpus, exact three-path scope,
  immutable packet, real server admission, ACK and native full-union cleanup proof.
- Current v0.110.5 SOURCE/app and MAIN module/payload pins, durable generic supervisor,
  assembly, bridge, delivery, dependency exporters and UID cleanup sources; no
  private input, credential or progress data in git.
- Fresh current eight-publisher route PodUID/image/mount/installed-code bindings,
  complete normal-write scope and physical backing; old frontend PodUIDs cannot
  survive the app roll.
- Reviewed exact GitOps pause and inverse, current schedules/acquisition and native
  writer census, independent armed watcher, fresh phase/owner ledger, five closed
  helper intents with at most four concurrent Pods, and exact immutable source clocks.
- Fresh post-stop complete app/LL/Kavita/SOURCE protection receipts, reader payload
  seals and retirement absence, two publisher gates, assembly receipt, exact selection
  bound to the new snapshot, and explicit root one-shot phase ratification.

None exists merely because an old packet reviewed cleanly. A first-stage refusal
must preserve actual receipts and restore production, without refresh, restamping,
scope expansion or automatic retry.
