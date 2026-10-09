# Full LIVE book census diagnostic — 2026-10-09

The one authorized full-corpus read-only diagnostic passed. It captured complete
bytes and OPF metadata, delivered and ACKed the sealed baseline, proved native
exit zero and Complete, and removed the actual Job and all owned Pods within the
original 200-second total lifecycle. The host exited zero after 165.603 seconds.
This is one successful observation of the unchanged 180/200-second profile; it
does not guarantee future timing or authorize COPY. The diagnostic artifact is
permanently excluded from COPY and its original byte freshness has expired.

The earlier [V3 failure and sampled timing diagnostic](book-census-native-diagnostic-2026-10-09.md)
remain historical failures/measurements. This later attempt used the separately
reviewed [two-reader source](book-census-native-performance-preparation-2026-10-09.md),
current three-path selection, signed health runtime, and corrected owning host.
It did not rerun or modify a consumed or refused packet.

## Exact authorization and code

Root granted one invocation of private packet
`/home/dev/work/hn-831-live-diagnostic-fdc-prepared-1009-v2-hardcap/diagnostic-review-packet.json`,
SHA `d72a6d3917ab67ebd750cbdb8160aca15e83361e23c5b58a3ca030aa59553a33`.
The independent preparation receipt was
`/home/dev/work/hn-831-live-diagnostic-d72-independent-review-1009.json`, SHA
`2835f9c85f2e6e7656384d256278cf04f0aaca125fe0f66a63e32fb79fc4dff4`.
Its 38 closed pins bind source, templates, scope, runtime modules and receipts.

The host came from [ops #3674](https://github.com/thaynes43/haynes-ops/pull/3674),
merged as `0555d86131b5bcb95bf270ed297465b3318ad56d`; its exact source SHA is
`43cd3830dc50067bf87aeb0a1a659d82783372b8a13e052f7fb6509b089fddc6`.
The corrected host keeps the original total deadline through request retirement,
native cleanup, receipt publication and final stdout. Lost CREATE transport with
unproved UID custody remains unknown. All required checks and the actual advisory
passed before merge; the finite host suite ran 14 cases. The final independent
source receipt is
`/home/dev/work/hn-831-live-host-independent-review-36958a97-1009.json`, SHA
`c8e81716615781568cd7af2cc25cbd13add26367fd1cd38b00710a9d46b62f8f`.

The signed runtime was
`ghcr.io/thaynes43/book-copy-writer@sha256:fdc358fcce883a198a710f9415a95e5200b39499a26ab540a9863043a8b2f86c`,
from source `7c99b2afbeed3ec04af55493509169e0778d6418`. The separately approved
bootstrap has its own exact hashes; its injected code is not claimed to have an
OCI source signature. The signed runtime's copies, metadata, writer, bound census
and transport closure remained exact. Signature receipt SHA:
`9bc68e6f5c21ac436838786f10ccc93db776108b9e897a7558730e4b9bd99ddc`.
Collector SHA:
`6e758e34db12fb82d4d6050c460d17a815d5b608c511a1d7bc9f94886342a61a`.
LIVE bootstrap SHA:
`f4724ca42ae9e85aaef949861e6ce50982c5941ccc6e74a3498d44033cbef841`.

The exact launch contract SHA was
`97beb60dfcd437fd44f18debe2b993c63cc5f6ae1ad833b886900090ac989c76`;
closed manifest SHA was
`5a94f2fbcb6d7d47424bda4f6490613e220280aeccf378d7e462c02ed8a2f264`.
Execution used the packet's frozen command with `nice -n 19 python3 -B`, phase
`8d50956684b2482cb46ce348d3c8739e`, and only
`frontend/issue831-live-byte-baseline-1009-03`. It ran exactly once.

The worker retained talosw01 placement, 1 CPU / 512 MiB, the original NFS backing
declaration with read-only container mount, read-only root filesystem, dropped
capabilities, UID/GID 1000 and no service account token. The 32 MiB proof and 1 MiB
log caps, native grace, original 180-second collection and 200-second **total**
host lifecycle were unchanged. The current scope remained two extras and three
distinct selected byte paths; complete corpus proof was retained.

## Actual result

Host start was `2026-10-09T21:42:08.982575Z`; finish was
`21:44:54.585745Z`. The actual Job UID was
`5df77a8f-b09a-4b83-82cb-bc162d9dc045`, and Pod UID was
`d0bfbe89-e3f7-4e76-975c-9544868636f3`.

| Observed stage | Seconds | Complete measured scope |
| --- | ---: | --- |
| Native binding | 0.455 | Actual Running UID/spec/image/source proof |
| Initial complete walk | 9.885 | 4,819 files and 2,077 directories |
| Complete byte/OPF read | 113.997 | 1,964 EPUBs; 8,533,467,771 bytes |
| Byte census final complete walk | 10.021 | Complete file/directory identity comparison |
| Complete stat and permissions | 16.003 | Both stat walks and every immediate parent/permission check |
| Selected whole-file read | 0.058 | Three distinct paths; 2,159,232 bytes |
| Final complete walk | 7.724 | Full corpus comparison after selected reads |
| Total owning host lifecycle | 165.603 | Delivery/ACK, native zero exit, actual-UID cleanup and receipt |

Original byte capture began at `21:42:12.083643Z` and completed at
`21:44:25.990079Z` (133.906 seconds). The baseline's later stat/selected/final
proof completed at `21:44:49.777004Z`. No stage reset the original byte clock.
The consumer's original-start-plus-300-second age expired at `21:47:12.083643Z`.
The diagnostic was never an admission artifact for COPY.

The complete baseline passed schema, full fingerprints, before/after stability,
module/source binding and selected-byte validation. Its ACK bound the exact raw
baseline SHA, phase and actual Job/Pod UIDs. The host separately required native
Pod Succeeded with original container exit zero/Completed and Job Complete, then
performed actual-UID Foreground deletion. Recorded full raw typed JobList/PodList
inventory proved absence at `21:44:54.584219Z`, resource version `819474672`.
The independent reviewer replayed those checks and obtained fresh typed complete
inventories at resource version `819481063`, with no matching Job/Pod or UID.

No service was stopped or paused. No PostgreSQL operation, library mutation,
reading-state change, reading-list write or COPY occurred. Public telemetry contains
aggregate stage times and completed file/byte counts only. Private baseline paths,
OPF payloads and all raw evidence remain outside git.

## Retained evidence and remaining boundary

Private actual directory:
`/home/dev/work/hn-831-live-diagnostic-fdc-prepared-1009-v2-hardcap/live-actual/`.

- `actual-receipt.json` SHA:
  `c732ba3b16bc38304b3aacb610d22007b0e27e0fbbe6e3ea109641b25dea2a66`.
- `live-byte-baseline.json` SHA:
  `4acf9f9c8f97fb7fef8ed4082b3c0686c0f7438bead094342796bcb9d4ab37c6`.
- `actual-log.jsonl` SHA:
  `14fc40624da616163b3db72c2071bdcba511bfa18fc6f2bf6b21437520840544`.
- Independent actual receipt:
  `/home/dev/work/hn-831-live-actual-d72-independent-1009/independent-review.json`,
  SHA `6b8041878526b7f236877b397d7ed47522e3976f91a09f770bc8d524d0068ce1`.

The result establishes that this complete read-only corpus capture finished
within the unchanged bounds on this attempt. It supplies no future timing,
availability, pause, retry or COPY authority. Kernel NFS stalls and unknown native
cleanup remain fail-closed. Future COPY needs its own fresh, coherent SOURCE/MAIN
and dependency proofs, actual Normal hold/drain/recovery evidence, exact Stop and
inverse/watch closure, unchanged lease/reserve arithmetic, and explicit root GO.
This expired diagnostic artifact may not be adopted or restamped for that work.

## Normal rehearsal start refused

The separately authorized Normal watcher launch exited 2 before producing a
Ready receipt or arming its state. The exercise was not started, and no hold,
Stop, Job creation, strip operation, library mutation or reading-list write
occurred. The private runtime residue consists of initial watcher state and its
lock. Root ended the activity at `2026-10-09T21:53:03Z`.

The known startup mismatch was the converter CronJob template's
`/spec/template/spec/volumes/0/configMap/name`: the raw Git name was compared to
the actual Kustomize-generated ConfigMap name. The read-only failure receipt
records the other deployment/image, HelmRelease generation/Ready and CronJob
checks passing. It separately records all six Kustomizations and the Git Source
unsuspended, with no owned hold annotations. This is a failed startup, not a
successful hold/drain/recovery rehearsal; no automatic retry is authorized.

Private failed-start receipt:
`/home/dev/work/hn-831-normal-only-start-refusal-1009.json`, SHA
`f6657dd08c183a553bea8d6676d66d369b3c1fc36fe46d2f1638eddeb054c519`.
Its exact hash and no-Ready/no-exercise/no-hold fields were independently checked
before this report. A reviewed correction and fresh root authorization remain
necessary before a new rehearsal. The LIVE baseline remains permanently
non-COPY and expired at its original `21:47:12.083643Z` deadline regardless of
the rehearsal's result.
