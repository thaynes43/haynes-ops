# LIVE bootstrap integration preparation — 2026-10-09

Preparation only. Root approved the bounded reader source in #3648, merged at
`9b0d3f0dbbb0a6c8148116afc0d7549bc193906b`, and the method of a signed immutable
runtime image plus a separately reviewed, hash-bound bootstrap payload. An image
signature authenticates the image; it does not OCI-sign injected source. No new
image adoption, Job, full-corpus read, baseline/ACK, pause or COPY is authorized.
Historical archives and consumed packets stay unchanged.

## Required coordinated image and module identity

The original proposed runtime was image digest
`fe12dd95f77cbdf33f2bd57cbe8ecb752e9d730a7de6b26b329beca474954d5f`.
Its retained registry proof identifies Python 3.14.8, `/copy-writer` as the working
directory, and the six original image source files. Application layers contain
no `bound_census_collectors.py`; four base/runtime layers are not archived locally.
With the actual retained image module directory first and candidate payload
directory second, an offline PathFinder check resolves the collector from the
payload and copies/metadata from the image directory. The entrypoint also checks
their exact bytes before collection. This check is not a new native image probe.

That original image cannot supply useful LIVE evidence to the new health consumer.
`bound_census.py:106` requires exact equality between the baseline's copies/metadata
hash map and the consumer's current module hashes. Trusted assembly preserves the
original baseline map (`bind_live_baseline`, lines 195–201); it cannot replace the
map just because read-only behavior appears unchanged. Health #3653 changes
`epub_copies.py`, so old-image evidence would fail closed at assembly/MAIN.

Before finalizing a LIVE packet, merge and validate the health source, wait for its
normal signed-image publication, verify the actual immutable image and all source
bytes, then use that same image/module version across LIVE, SOURCE, assembly and
MAIN. Root must approve the new image explicitly. Old capture clocks or module
hashes must never be restamped. The new image digest is deliberately unbound here.

Reviewed reader payload at main #3648:

| File | SHA-256 |
| --- | --- |
| `live-performance/bound_census_collectors.py` | `6e758e34db12fb82d4d6050c460d17a815d5b608c511a1d7bc9f94886342a61a` |
| `live-performance/capture-live-byte-baseline.py` | `f4724ca42ae9e85aaef949861e6ce50982c5941ccc6e74a3498d44033cbef841` |

The reached non-stdlib LIVE dependencies are the image's exact `epub_copies.py`
and `epub_metadata.py`; Python/stdlib are image-bound. `epub_copies`' lazy
`bound_census` import belongs to the unused manual consolidation path. The new
package must record all actual image source hashes and verify the actual import
layout, including refusal of a conflicting collector shadow. It must not start
the image's default writer or import the PG writer into the LIVE path.

## SOURCE caller compatibility

The frozen private SOURCE producer has a background heartbeat calling
`PrimaryShareFence.health()` at `capture-source-stat-census.py:61–75`. Health
#3653 correctly requires that SQL remain on the original process/thread. Merely
re-pinning that old producer to the new image would refuse and stop SOURCE.
A new SOURCE adapter must serialize SQL on the owner thread. A watchdog may
signal an owning-process deadline, but cannot query or adopt its PG connection.
Full app row/schema captures and complete SOURCE permission/fingerprint proof
remain mandatory; owner-thread health and its bounded native lease events must
remain valid throughout capture and the subsequent holding interval.

Fresh pipeline preparation must also close the new image/module pins in the
SOURCE adapter, MAIN source-identity checker, trusted assembly and outcome
verifier. Independent read-only source tests must reject mixed old/new copies
hashes and a SOURCE heartbeat that attempts SQL from a foreign thread. The
existing frozen versions remain historical evidence, not editable templates for
an already-consumed approval.

## Exact future scope and lifecycle

The app's prepared two-extra profile has SHA-256
`3f3cb56027c880e4f21151b8b8d025a217a52df7e9373d13dbac8699d9dcb666`.
Its selected byte scope contains exactly both Pathfinder extras and the unique
Pathfinder LL keeper. Canonical sorted JSON plus a final newline is 394 bytes,
SHA-256 `1754edf94c3735c5c7cf6a78d30e3bea3b110e7b48a77ef1fea6e16e91c82663`.
These are preparation pins; current complete captures must still prove every
selected path and byte hash before a later COPY selection is admitted.

Use a separate successor package, new Job name, phase and unused output directory.
The proposed next LIVE name is `issue831-live-byte-baseline-1009-03`; live name
absence and native admission remain unproved. Close bootstrap source, manifest,
actual image/modules, dependencies, selected scope and host source in a new
immutable packet. Root approval must refer to its exact hash and launch argv.
No literal old deadline/context value may be copied into a new packet.

Keep original LIVE collection/Job 180 seconds, host start plus 200 seconds total
including cleanup, one CPU/512 MiB, complete corpus proof/32 MiB artifact and
1 MiB aggregate logs. The consumer's byte age remains original capture start
plus 300 seconds, and the separate restoration trigger remains 170 seconds.
No full-corpus completion or throughput is guaranteed by the finite source tests.

Preserve the unchanged generic native bind/input/ACK contracts: actual Job/Pod
UID/spec/image identity before delivery and artifact fetch, exact private artifact
SHA ACK, actual native exit-zero Complete proof, actual-UID Foreground deletion
and complete raw typed Job/Pod union absence. Omitted redundant child types may
be filled only after their typed parents and any present child values are verified.
No new Job is permitted until independent source/package/packet review and a
fresh explicit root one-shot runtime GO. A failed or expired capture cannot
authorize another capture, production stop or COPY.
