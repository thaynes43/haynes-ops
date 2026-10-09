# Bounded LIVE census source preparation — 2026-10-09

Root ratified this source preparation after the measured diagnostic. The
candidate is in `scripts/book-copy-writer/live-performance/`; it is not an
admitted native package or runtime packet. No live corpus was read, no Job was
created, and no image, service state, database, reading list, or library changed.

The candidate retains the frozen per-file byte/OPF reader unchanged, with at most
two submitted-but-not-emitted tasks and deterministic ordered rows. It fuses
permissions into the complete first stat walk while retaining full parent
fingerprint and absolute path checks before/end of each directory batch and the
independent second complete file/directory walk. The owning entrypoint uses
bounded nonblocking refusal telemetry and hard exit; blocked userspace reader
threads are never joined. Hard-NFS kernel IO remains a possible native cleanup
refusal, requiring actual UID deletion and full-union absence before any pause.

Controller-only stage/progress events omit all private paths and metadata, use
at most 512 bytes per event, and remain below the existing 1 MiB log cap at the
maximum file count. The complete artifact schema and 32 MiB cap are unchanged.
No byte clock is reset: future adoption must preserve LIVE 180-second collection,
200-second total host lifecycle, consumer original byte start plus 300 seconds,
and the 170-second restoration trigger. Future selection must use the fresh
exact reviewed three-path scope; the consumed old archive and packets are intact.

One serial `nice -n 19` final run passed all 23 finite fixtures in 0.781 seconds.
The independent reviewer previously passed all 22 in 0.765 seconds and reviewed
the five candidate source/CI files, then passed the four focused stdout/signal/pin
fixtures in 0.060 seconds after the advisory correction. Coverage includes complete legacy/candidate schema
and byte/OPF equality; original reader AST equality; two-stream/reorder bounds;
first error behind a blocked earlier index; deadline and signal hard exit;
full undrained stdout at the closed gate and intact ready/delivered messages after
telemetry restores stdout's original blocking flag; directory/file replacement and other
mutation refusals; protection/cap preservation; and telemetry limits with
deadline propagation. CI runs the same suite in a network-isolated one-CPU,
128 MiB container. These are correctness fixtures, not throughput measurements.

Independent final source receipt:
`/home/dev/work/hn-831-live-performance-independent-source-review-stdout23.json`,
SHA `1614a837dc4f21c5d36d48c1b9390c152494c5e67228f20f6027a158a1351027`.
It retains the prior 22-fixture source-review receipt
`9354305a370f25ec44c412a741130fc84e4cad6c273fd2e68edbe7a75926caf9`.
Collector SHA:
`6e758e34db12fb82d4d6050c460d17a815d5b608c511a1d7bc9f94886342a61a`.
Entrypoint SHA:
`f4724ca42ae9e85aaef949861e6ce50982c5941ccc6e74a3498d44033cbef841`.

Required next boundary: source PR/checks/advisory and root merge, then a reviewed
image/native bootstrap integration with actual immutable image/module/code pins,
fresh closed Job/Pod declaration, exact three-path selection and original bounds.
A new full-corpus attempt requires fresh root ratification of that exact unused
packet and command. This preparation supplies no runtime, performance-probe,
repeat, baseline, ACK, pause, or COPY authorization, and proves no 180-second
full-corpus completion.
