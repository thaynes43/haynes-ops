# Bounded LIVE byte-census preparation

This candidate preserves the complete LIVE byte/OPF proof and original bounds.
It is preparation only: no runtime packet, producer launch, image adoption,
production pause, or COPY is authorized. The consumed frozen launcher and its
dependency archive stay unchanged. The
[measured diagnostic](../../../.agents/reports/book-census-native-diagnostic-2026-10-09.md)
does not establish that this candidate fits the 180-second collection deadline.

Root ratified at most two simultaneous per-file read streams, with queue capacity
two, deterministic complete output, and the existing one-CPU / 512 MiB worker
profile. The host collection clock remains original start plus 180 seconds,
native Job deadline 180 seconds with five seconds termination grace, and total
host lifecycle original start plus 200 seconds. The 32 MiB proof cap, 300-second
consumer age from the original byte capture start, and 170-second restoration
trigger are unchanged. A future selected-byte scope must be the fresh exact
reviewed two-extra / three-path selection; no old 41-path packet is reused.

## Two per-file readers

Each reader owns its private descriptor and invokes the unchanged `read_epub`
logic: complete SHA bytes and every raw OPF plus parsed role-aware identity,
before/after descriptor and current path fingerprints, safe absolute directory
resolution, and final directory identity check. A two-slot task queue and
two-slot result queue bound scheduling; only two daemon reader threads perform
IO. At most two tasks are submitted but not yet emitted, including queued,
running, and completed-but-reordered tasks. The two-index sliding window and
two-row reorder buffer cannot grow behind a slow earlier path. Results are
assembled by sorted input path index, independently of completion order, with
the original full identity proof cap. No row, failure, or protected
entry is silently skipped.

The controlling thread waits for bounded result events against the same absolute
deadline. The first error cancels pending tasks and reaches the controlling
thread through a separate error channel, even if the result queue is full.
Reader health checks use only cancellation/deadline checks; the controlling
thread owns any caller health callback. There is no executor, worker join,
or implicit non-daemon interpreter shutdown wait. Only the owning entrypoint
hard-exits: on refusal it attempts one bounded aggregate JSON event through
nonblocking stdout, then `os._exit(2)`. No artifact is published before all readers
have returned, every per-file descriptor has closed, and the complete final
guards pass. Success still requires the host ACK and native exit-zero proof.

This prevents a blocked userspace read from making Python wait for a worker at
shutdown. Kernel hard-NFS IO can resist process exit/SIGKILL, as it can in the
original single reader. Native UID Foreground deletion and authoritative full
Job/Pod union absence remain mandatory; a cleanup refusal cannot authorize a
pause or COPY. No universal bounded-kernel-exit claim is made.

The controller emits nonblocking aggregate stage start/stop events and completed
file/byte totals every 128 emitted rows and at the end. The per-file workers
never log. Events contain no filenames, OPF, identities, reader data, or error
messages; each event is capped at 512 bytes. Even the maximum 10,000-file census
stays below the unchanged 1 MiB log cap. A full log pipe may drop telemetry but
cannot delay refusal. Signal/deadline exceptions propagate out of telemetry;
these observations neither extend clocks nor authorize incomplete proof.

## Fused permissions with complete identity checks

The complete stat census may collect each file's permission record while its
parent directory is already open in the first all-file walk. The parent
fingerprint is captured before that directory's batch. Absolute directory path
identity is checked before and after the batch, and its full fingerprint must
remain equal at batch end. Per-file stat values, parent fields, mount-read-only
flag, ownership/access/nlink tests, nonregular protection, ignore scopes, and
configured holds retain the original schema and semantics. Complete all-file
and directory fingerprints still undergo an independent second whole walk.
Any replacement, rename, symlink, missing entry, metadata change, or other
observed identity mismatch refuses the census.

The original complete byte pass also retains its independent before/after whole
file and directory walks, original start and byte-completion clocks, selected
whole-file rereads, and final whole walk. Permission fusion removes redundant
per-file absolute parent reopen work; it does not replace byte proof or weaken
the second walk. No measurement guarantees a particular speedup.

## Finite verification and remaining admission

Use one serial `nice -n 19` run of finite fixtures:

```sh
nice -n 19 python3 -B scripts/book-copy-writer/live-performance/test_collectors.py
```

The existing copy-writer CI also runs this candidate suite with no network, one
CPU, 128 MiB, and a bounded temporary fixture mount. It does not activate the
candidate: the image/Dockerfile and frozen production launcher remain unchanged.
Never run CPU busy loops,
stress, load generation, wide parallel suites, or repeated timing tests.
Fixtures must check exact original/candidate stat schema and byte/OPF identity
equivalence; two-stream bounds and out-of-order determinism; first-error refusal;
blocked fake IO in a subprocess that exits by its deadline; and real temporary
file/path/directory replacement and protection cases. They touch no live corpus.

Independent source review and root ratification must precede any concrete unused
packet. The new collector/entrypoint and all bootstrap payload pins must be
closed into that packet, using the reviewed signed image and unchanged generic
native binding/ACK/UID cleanup. No existing frozen packet is rewritten. Runtime
requires a separate fresh exact-command GO, once; no automatic full-corpus retry
or image adoption follows from this source proposal or its tests.

Python's [executor shutdown](https://docs.python.org/3.14/library/concurrent.futures.html#concurrent.futures.Executor.shutdown)
still waits for running futures at interpreter exit, including with
`wait=False`. [Threads](https://docs.python.org/3.14/library/threading.html#thread-objects)
cannot be forcibly interrupted by Python. The owning
[`os._exit`](https://docs.python.org/3.14/library/os.html#os._exit) boundary therefore
skips interpreter cleanup on refusal; descriptor closure on successful reads
and explicit flush/fsync/ACK handling must finish before success exit.
