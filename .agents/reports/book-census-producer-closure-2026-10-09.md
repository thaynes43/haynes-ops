# Book census producer closure — 2026-10-09

The signed image and read-only mount are verified. V2 refused before creating a
Job; the separately ratified V3 reached the original 180-second capture deadline
and refused without an artifact or ACK. Independent foreground cleanup then
proved its Job and all matching Pods absent. The complete live producer lifecycle
is still unproved; no COPY or production pause is authorized by this evidence.

## Image and preparation

[Ops #3620](https://github.com/thaynes43/haynes-ops/pull/3620) merged at
`04c611e7ccf6d057f5b6b466dc4d52f5bb5672c8`.
[Publication run 37868326665](https://github.com/thaynes43/haynes-ops/actions/runs/37868326665)
validated the image, transferred the exact validated image without rebuilding,
published it on main, and signed its immutable digest:
`ghcr.io/thaynes43/book-copy-writer@sha256:fe12dd95f77cbdf33f2bd57cbe8ecb752e9d730a7de6b26b329beca474954d5f`.

Full cosign v3.1.3 verification passed for the exact main workflow identity,
GitHub OIDC issuer, repository, and source SHA. It validated claims, the trusted
certificate chain, and offline transparency-log inclusion. The verifier binary
SHA `4629c757b7618056f8ddd7e2625ae9fdd94c0372a65049520bc7d9df9efc7f71`
matches the official release asset. The safe verification stdout SHA is
`45fb3bccaca20edead2ce8f230154c4732f7a311f9d70a25d08931b19a90acc9`.
This closes the prior registry proof's explicit certificate-chain gap. The
workflow signs images; it does not generate a separate build provenance
attestation. A bounded `cosign download attestation` query returned no records.
These are signature and exact validated-image publication claims, not a SLSA
provenance claim. See the official [verification documentation](https://docs.sigstore.dev/cosign/verifying/verify/).

The immutable V2 packet
`/home/dev/work/hn-825c-fence-prepared/live-byte-baseline-lifecycle-prepared-v2/immutable-review-packet.json`
has SHA `c9501cdf207add2242176a63da81f573abb74d23799788a51018f69878f559fe`;
all 14 file pins matched. Its later independent review at 02:02:39Z passed the
prepared host lifecycle, superseding the stale report's 01:59 blocker. Prepared
reviews did not prove actual corpus capture, ACK delivery, or foreground cleanup.

## Worker image and mount smoke

The bounded read-only smoke Job ran on `talosw01` from 15:08:49Z to 15:08:55Z
(six seconds; container exit zero, no restart). Its CPU limit was 500m, memory
256 MiB, deadline 120 seconds, and backoff zero. It confirmed the exact image
digest, all six module hashes, psycopg 3.3.6 and Python 3.14.8 imports, and the
reachable EBooks root under UID/GID 1000. `/data/cephfs-hdd` was the required
container read-only NFS mount from `gasha01.haynesnetwork:/hdd-nfs-repl`, backed by
the normal read-write export. Corpus files read: zero. Production writes: zero.
Foreground deletion completed, followed by empty Job and Pod inventories for
that smoke Job. The frozen actual LIVE manifest already pins `nodeName:
talosw01`; no placement revision was needed.

## Consumed V2 attempt and correction

The single ratified attempt ran from 15:13:31.944301Z to 15:13:32.553967Z and
refused with `complete_pod_inventory`. Job and Pod UIDs stayed null; only the
private ready manifest and failure receipt were written. The host's cleanup
also refused the typed inventory guard and correctly made no absence claim.
A separate complete native Jobs/Pods inventory confirmed no exact-name,
phase-labelled, or Job-owned object existed. No Job was created, no corpus bytes
were read, and no PostgreSQL or production library write occurred. The preserved
private failure receipt is
`/home/dev/work/hn-831-copy05-live-byte-baseline-actual/actual-receipt.json`.

The cause was a host/API mismatch: `kubectl get pods/jobs -o json` synthesized
`kind: List` with an empty resource version. The raw namespace API returned the
expected `PodList` and `JobList`, with native resource versions. V3 requests the
raw typed endpoint and refuses wrong schema, missing resource version, pagination,
or truncated inventory. It preserves the original 180-second collection clock,
200-second total cleanup budget, 32 MiB artifact cap, UID-precondition foreground
deletion, and phase/controller/owner union. The durable source and finite tests
are under `scripts/book-copy-writer/live-baseline-host/`; external dependency
pins and V2 evidence remain unchanged. A fresh V3 packet, name, phase, output,
independent review, and explicit one-attempt ratification are required.

The durable package retains all five generic dependencies byte-for-byte, the
unchanged ACK receiver, a synthetic `Author/Book.epub` fixture, the exact closed
profile manifest, dependency pins, and a non-runtime packet preparer. It contains
no private selected-scope data, credentials, or progress receipts. The preparer
reads no kubeconfig and makes no API call. Twenty-six finite tests passed in
0.528 seconds under `nice -n 19`, including the actual inventory failure,
pagination/schema failures, ownership union, UID-precondition foreground delete,
and the frozen manifest's pin and valid phase Downward API. Current real raw
inventories passed the new guard. The final V3 manifest passed server dry-run and
the existing helper's exact declared-template comparison on `talosw01`.

The final V3 packet is
`/home/dev/work/hn-825c-fence-prepared/live-byte-baseline-lifecycle-prepared-v3-native-inventory-final26/immutable-review-packet.json`,
SHA `ac5f3c55bf53e11e8ad81c1491cea59997bce7c2834ac09c0e28fc17305ece6e`.
It binds Job `issue831-live-byte-baseline-1009-02`, a fresh phase and unused private
output. The only frozen manifest changes are its Job name and two phase labels.
The phase environment remains the original Downward API entry. A preparation
mistake that briefly supplied both `value` and `valueFrom` was caught by peer
review, removed before any launch, and covered by the new regression. Provisional
packets remain unexecuted; the original failed V2 packet and receipt are preserved.

Independent final V3 review passed all 14 packet/source pins, the private scope
and contract bindings, raw native inventories, the exact manifest delta, and
26 finite tests in 0.531 seconds. It made no runtime or COPY claim. The private
review receipt is
`/home/dev/work/hn-831-live-native-inventory-independent-review-final26.json`,
SHA `ca9ee853fa38a2a4fd0ed120c70add5b1d9638b21944f1f8ea6aff7eb9177fea`.

Production services, CronJobs, Flux, library data, and app settings remained in
their normal state. This Python NFS-only lifecycle is independent of the pending
haynesnetwork v0.110.5 release. It supplies no app capture and no COPY approval;
future mutation must use the fresh expanded corpus and deployed app version,
with original expiry/restoration bounds and without reusing the old 1956 approval.

## Actual V3 attempt and remaining runtime gate

[Ops #3638](https://github.com/thaynes43/haynes-ops/pull/3638) merged the durable
host package at `b3f4a689`. Required checks and the advisory review passed, with
no findings. Root separately ratified one exact V3 attempt. It ran from
15:28:22.291056Z to 15:31:22.133909Z (179.843 seconds). Native worker, image,
read-only mount, Job/Pod ownership, server admission and private input delivery
passed. The producer emitted `live-baseline-refused` with error class `Stop` and
zero production writes. The Job reached `DeadlineExceeded`. No complete baseline,
original capture/measurement clocks, fresh corpus count, retrieved artifact,
delivery ACK, or successful completion can be claimed.

Automatic cleanup refused because native typed list items omit their redundant
`kind` / `apiVersion`, while `helper.declared_matches` expects those fields on
the individual Job. The list envelope itself passed validation. Only the exact
owned Job was then deleted with a UID precondition and foreground propagation.
At 15:33:33.915754Z, independent complete raw `JobList` / `PodList` inventories
(resource version `818882625`, no continuation) proved the full union of name,
UID, phase label, controller UID, and Job owner name/UID absent. The host refusal
receipt remains unchanged; its absence claim remains false. The separate private
cleanup receipt records the independently verified result.

The narrow correction restores omitted item type/version fields from the already
verified typed envelope, after rejecting any explicit mismatch or wrong namespace.
A fixture derived from actual server admission, with synthetic identities and no
progress data, exercises the real helper comparison and foreground cleanup path.
It does not change the manifest, image, capture clocks, expiry, cap, or retry count.
Twenty-seven finite tests pass under `nice -n 19` (0.540 seconds); the new cleanup
case proves the unnormalized actual-admission shape fails the real helper, then
passes the strict typed inventory, real helper and native absence verifier after
normalization. This cleanup correction does not prove that full capture fits
180 seconds. The last
sampled cumulative CPU counter was only 12.358 seconds; increasing the CPU limit
is not supported by that evidence. Stage timing and NFS work require diagnosis
before another capture can be ratified. No retry or new phase was attempted.

Private runtime evidence is under
`/home/dev/work/hn-831-copy05-live-byte-baseline-actual-v3/`, including the original
host receipt, admitted/created native objects, input transport receipt, refusal
log, and independent cleanup receipt. No private input, credential, or progress
receipt is committed. Production services, CronJobs, Flux and library data
remained in their normal state throughout.

## Read-only deployment verification after v0.110.5

[App #866](https://github.com/thaynes43/haynesnetwork/pull/866) merged at
`68a3ccd9ce59c69983b7f2d3962a0d00fce0ab41`.
The [v0.110.5 tag](https://github.com/thaynes43/haynesnetwork/tree/v0.110.5)
resolves to released source `8374aeca2456ec7aba035530be4755fd89216cf1`.
[Ops #3639](https://github.com/thaynes43/haynes-ops/pull/3639) updated the app and
its download CronJobs. Independent native checks after reconciliation confirmed:

- Deployment generation 216 was observed; all three replicas were updated,
  available and Ready on v0.110.5, with zero restarts and exact image ID
  `sha256:e264e8a63b7a6b8865bfb51ecb38c398534d492ba64c6956960dedd8e6e64c6a`.
- All three Pods' `init-db`, `migrate` and `k8tz` init containers exited zero.
  Bounded startup and migrator log inspection found zero error/fatal/panic lines;
  no raw logs or private data were copied into this report.
- All 22 frontend haynesnetwork CronJob templates, plus downloads `books-census`
  and `owed-checks`, used v0.110.5 and were unsuspended.
- Each of the three Pods matched released git blob bytes for the eight modules
  below. The API router is compiled into the Next.js bundle; it has no raw TS
  module in the runtime image, so no raw router SHA claim is made.
- The live converter retained `STRIP_SERIES_METADATA=0` and was unsuspended.
  Libretto had one Ready replica, a configured acquisition endpoint, and a
  selective runtime environment check confirmed the endpoint remained enabled.
  No environment credentials were read or printed.

| Released source module | SHA256, identical in all three Pods |
| --- | --- |
| `packages/domain/src/book-requests.ts` | `4c1a27ab355e25deec322747d420b8da634c0b4adcbb5d0917dc02c32a0b838f` |
| `packages/domain/src/collection-wants-sync.ts` | `04953c98d084948ae844feadeb8b046458337e9cfb739274cc1aad7850bbed45` |
| `packages/domain/src/format-pairing.ts` | `a0476659be99875d13e2b9b4ea1ab9cd5285e9c63d4f3742c2f025d5871b2d19` |
| `packages/domain/src/goodreads-sync.ts` | `9178f70f339a14468daad33008a31609d772600b3c3e34e83602846883e3e6d2` |
| `packages/domain/src/ll-book-check.ts` | `15ed9e458abefb0871b3821d18a7ba00bdbbba4ecd5dea77f79c9cd236b64119` |
| `packages/domain/src/ll-release.ts` | `2012538c4d6757668eb08b06698308f21a8a4b1d1d71f9aae0b197d8f9a75134` |
| `packages/domain/src/pairing-work-identity.ts` | `11fa398e5278731f33fdab2d359c73e5ab09fbf014b381789bd3ce55fed5714c` |
| `packages/sync/src/goodreads.ts` | `b678c95a5fd28ec5ea862c47c160ae8a1a35d90cbbb51e45aff3813db8dfd5d5` |

The verification used read-only native queries, selective source hashes and
bounded logs. It created no Job, restarted no workload, and made no app, library
or list write. Root reconciled the three release scopes and ended deployment
activity at 15:51:44Z. This deployment proof does not supply the outstanding
native producer artifact, app capture, or COPY authorization.

## Qualified performance diagnosis and next probe

A separate stat-only LazyLibrarian/CephFS snapshot at 15:37:25Z counted 4,816
files, 2,077 directories and 1,963 visible EPUBs totalling 8,532,610,673 bytes.
The retained selected-41 sizes totalled 296,546,588 bytes. This snapshot is not
native NFS identity/byte proof and cannot be adopted as the required fresh
baseline. Input delivery consumed about 2.26 seconds of the original capture
window, leaving about 177.449 seconds. Reading whole plus selected EPUB bytes
alone would require about 49.76 MB/s, before ZIP/OPF, path and metadata work.

Retrospective metrics showed 12.358097 cumulative container CPU seconds and zero
throttled periods out of 1,688. The collector performs five full walks, complete
EPUB SHA and ZIP identity reads, 4,816 repeated parent permission checks, and the
selected-41 byte rereads. The final generic `Stop` contained no counters; the
actual cutoff stage and bytes completed are unrecoverable. No performance
guarantee or CPU-limit remedy follows from these observations.

Root ratified preparation, but not execution, of one diagnostic worker probe:
one unchanged walk, one unchanged per-file permission pass, and one fixed EPUB
read of at most 64 MiB with before/after descriptor/path checks. Its proposed
hard bounds are 90 seconds, 500m CPU and 256 MiB, on `talosw01` with the same
read-only mount, signed image and security profile. It emits only aggregate
stage telemetry, with no baseline artifact, ACK, COPY claim, full hashing or
retry. Independent review of the concrete script, manifest, hashes and cleanup,
followed by fresh root GO, is required before execution. Any algorithm change
waits for those timings. The original 180/200-second lifecycle and 180-second
artifact expiry / 170-second restoration trigger remain unchanged.
