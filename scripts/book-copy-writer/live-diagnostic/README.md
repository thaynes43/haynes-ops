# Single read-only native census diagnostic

This is a timing diagnostic for the 2026-10-09 refused LIVE capture, not a
baseline producer or COPY prerequisite. The failed attempt logged only `Stop`,
so retained evidence cannot identify its deadline phase. There is no permission
to retry the full producer, change its 180-second budget, optimize its collector,
pause a service, change reading state, or copy a library file.

The parent ratified preparation only: one unchanged complete collector walk,
one unchanged complete permission pass, then at most 64 MiB from one fixed
regular EPUB. The permission loop is checked against the frozen collector AST.
Aggregate phase start/stop events identify the deadline phase without printing
filenames, XML, reader data, or library rows. No full-file digest, baseline,
delivery ACK, PG access, service API, or library write is performed.

The isolated Job uses the existing signed `fe12dd95…` image on `talosw01`, the
same NFS export mounted read-only, UID/GID 1000, a read-only root filesystem,
all capabilities dropped, no service account token, 500m CPU and 256 MiB memory.
Its absolute host/program clock and Job deadline are at most 90 seconds.
Only its private native identity input is written in a bounded `/tmp` emptyDir,
after the host verifies the actual Running Job/Pod/image with the unchanged
native verifier and helper against this separately reviewed diagnostic manifest.
Their ownership, actual-image, security and original NFS/temporary-mount
invariants are reused unchanged. A finite regression invokes the real binder
with the diagnostic manifest. The collector also verifies actual Downward API
IDs, module pins, and source root identity. Input delivery cannot extend the
diagnostic deadline; the diagnostic never produces a LIVE or COPY prerequisite.

Preparation writes a fresh private packet outside git. Its fixed sample path
is private. The source and closed manifest hashes, fresh Job name/phase,
bounded launch command and cleanup procedure are reviewed before runtime GO.
There is one create, no restart or retry. The host retains the original
200-second total lifecycle ceiling (90 seconds plus at most 110 seconds for
cleanup): strict complete native typed JobList/PodList, full phase /
name / UID / owner union, Foreground UID-precondition deletion, and final full
union absence. A failed create may discover only its exact approved phase and
manifest; a reused name or contradictory owner refuses deletion.

Run the finite local tests once under `nice -n 19`. Never run stress, busy loops,
wide or repeated test suites. The diagnostic is not approved to run merely
because preparation or tests pass. Root must ratify the exact packet and command;
its sample rate can explain a bottleneck but cannot certify a full-corpus
180-second completion or authorize COPY.

Retained V3 evidence records 179.843 seconds, `Stop`, no baseline or ACK, then
independent actual-UID cleanup and full native union absence. Input delivery took
about two seconds. Retrospective container CPU was 12.358 seconds with zero
throttled periods, so increasing CPU is not supported by the evidence. There
were no stage counters; the exact cutoff phase and bytes completed are unknown.

A single 20-second-alarmed stat-only LL/CephFS diagnostic at 15:37:25 UTC found
4,816 files, 2,077 directories, and 1,963 visible EPUBs totaling 8,532,610,673
bytes (largest 465,274,633). Its 2.187 seconds is not native worker NFS throughput
proof. The old selected41 scope added about 296.5 MB, but the current ratified
future COPY profile is only three byte paths / about 2.16 MB; any future baseline
must use its fresh exact approved scope while retaining complete corpus proof.
Neither scope reduction nor a proposed fused permission walk is evidence of
meeting the unchanged 180-second full producer clock. Diagnose before optimizing.
