# Native book census timing diagnostic — 2026-10-09

The one ratified diagnostic completed in 25.154 seconds, with native exit zero
and independent proof that its Job and all matching Pods were absent. It measured
a complete filesystem walk, the unchanged permission pass, and one 64 MiB file
prefix. It produced no complete byte baseline, ACK, or COPY eligibility. The
original V3 full-corpus capture remains refused and unproved.

## Authorization and source

[Ops #3643](https://github.com/thaynes43/haynes-ops/pull/3643) merged as
`7ddcb830e20052bd1f857f756d495015de2eda3d`. The source is
[`scripts/book-copy-writer/live-diagnostic/`](../../scripts/book-copy-writer/live-diagnostic/README.md).
The 15 finite tests passed under `nice -n 19`; an independent run also passed all
15. There were no wide, repeated, stress, or load tests.

Root then authorized exactly one invocation of the unused private packet:

- Packet: `/home/dev/work/hn-831-native-diagnostic-prepared-1009-v2-final15/packet.json`,
  SHA `cf2d2cb39a6556ad964b0690d92fa2e160fd286da760e34ec509e88a395d96ee`.
- Closed manifest SHA:
  `787aeff87d4a814a411bb28b54bcf60e8b2787a546769558ff5ed9e2a990b3b4`.
- Server-admitted dry-run SHA:
  `fbf18140bb4a2ab19ad6bf9495c5c2caf557753043adc2be021de8fa35b58694`.
- Independent preparation receipt:
  `/home/dev/work/hn-831-native-diagnostic-independent-review-final15.json`,
  SHA `e4aca364d5fb645b63c490f8c16eb4912583ad9fc0ac3a3bf687631906b979eb`.

The exact invocation used `nice -n 19 python3 -B` with the merged `run.py`, that
packet, and its SHA as `--go`. It ran once. The packet's exclusive runtime output
prevents reuse; no new producer or repeat is authorized by this report.

The Job used the signed `fe12dd95…` image on `talosw01`, with a 90-second hard
deadline, 500m CPU, 256 MiB memory, backoff zero, UID/GID 1000, read-only root
filesystem, all capabilities dropped, and no service account token. The original
NFS backing volume declaration was preserved; the container mount was read-only.
The unchanged generic native verifier bound the actual Running Job, Pod, image,
spec, and source mount before private input delivery. The host kept its original
start plus 200-second total lifecycle ceiling, including cleanup.

## Actual observations

The host started at `2026-10-09T16:19:38.689737Z` and finished at
`16:20:03.843886Z`. The actual Job UID was
`21cd4de5-e5ff-4795-98b4-e7898687ffdb`; its Pod UID was
`a027cf8d-d850-4b76-9ea6-d3d7fe1b70fc`.

| Stage | Seconds | Complete observed scope |
| --- | ---: | --- |
| Native binding/input | 0.455 | Actual source identity and pinned modules |
| Complete walk | 7.043 | 4,816 files, 2,077 directories, 1,963 visible EPUBs |
| Unchanged permission pass | 10.605 | All 4,816 files |
| One fixed prefix read | 1.616 | 67,108,864 bytes of one 465,274,633-byte regular EPUB |
| Host lifecycle including native cleanup | 25.154 | Exit-zero Complete proof and final full union absence |

The visible EPUB sizes totalled 8,532,610,673 bytes. The prefix read used before
and after descriptor/path checks and performed no digest, ZIP, or OPF parsing.
Its rate was about 41.52 MB/s (39.60 MiB/s). If that single prefix rate represented
the entire corpus, byte reading alone would take about 205.5 seconds. That is an
inference, not a full-corpus throughput measurement: caching, per-file latency,
hashing, ZIP parsing, and the original collector's other walks were not measured
by the sample.

The permission pass reported zero protected filesystem entries. This is not a
complete LazyLibrarian, Kavita reading state, or app-wants dependency census, and
does not establish that a copy is eligible. Logs contain only aggregate counts
and timings, with no sample filename, XML, reading state, or credentials.

The probe's completion event explicitly reported `complete_proof: false`,
`copy_eligible: false`, zero production writes, and zero PG operations. The host
separately verified native container exit zero, Succeeded/Complete, unchanged
UID/spec/image, then performed actual-UID Foreground deletion. Its full raw typed
Job/Pod union was empty at `16:20:03.842371Z`. The independent reviewer repeated
the native binding/completion checks and obtained a fresh empty union at resource
version `818964033`.

Private evidence remains outside git:

- Runtime directory:
  `/home/dev/work/hn-831-native-diagnostic-prepared-1009-v2-final15/runtime/`.
- `actual-receipt.json` SHA:
  `cc23b36ff7092212628d65adfa60906dcfb28b7030a46b26a62c7902334b7409`.
- `actual-log.jsonl` SHA:
  `90149f46a12e93bbe963b0bee8727aebd6198b497b500718f9fa5da7ab94b6c5`.
- Independent actual receipt:
  `/home/dev/work/hn-831-native-diagnostic-actual-independent-review-1009.json`,
  SHA `301117eced67f3d0f88362e07858d82800186b6c83414bc7c72892db82acf27b`.

No service was stopped or paused, no full hash/census baseline or delivery ACK
was produced, and no library, PostgreSQL, reading-list, or COPY mutation occurred.

## Clock origins and admission constraints

The clocks serve different purposes. The frozen
[`LIVE launcher`](../../scripts/book-copy-writer/live-baseline-host/run-live-byte-baseline.py)
sets collection to host start plus 180 seconds and total lifecycle to start plus
200 (line 63). These are the separately ratified implementation profile's bounds;
the diagnostic neither changed nor satisfied the full producer profile.

The signed consumer contract's `byte_age_max_seconds` is **300**, not 180
([contract](../../scripts/book-copy-writer/bound-census-contract.json), line 79;
[`epub_copies.py`](../../kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert/epub_copies.py),
line 13). [`bound_census.baseline_expiry`](../../scripts/book-copy-writer/bound_census.py)
(lines 87–94) expires bytes at their **original capture start plus 300**. It checks
both original start and completion for freshness, without resetting age when the
capture completes or when SOURCE is assembled.

The collector's start precedes its first complete walk. Its original byte
completion is recorded after the whole byte/OPF pass and second walk; complete
permission/stat and selected-byte measurements follow before the artifact can
return (frozen collector lines 260–315). Those later stages and transport consume
the same original age. The 32 MiB proof cap and complete corpus identity remain
unchanged.

Trusted assembly first compares every path and decimal fingerprint in the fresh
SOURCE census with the sealed LIVE baseline. It carries `capture_started_at` and
`completed_at` unchanged into the bound census and keeps current permissions,
holds, ignore markers, and protections (bound census lines 187–205).
[`book_copy_writer.py`](../../scripts/book-copy-writer/book_copy_writer.py)
lines 282–287 takes the earliest supplied writer deadline, dependency expiry,
and original byte expiry. MAIN independently repeats the complete stat census,
every selected whole-file hash/OPF/identity check, and a second complete stat
census, then rechecks freshness (bound census lines 282–326). Fresh stat proof
does not turn an old byte read into a new one.

Current haynesnetwork
[PLAN-074](https://github.com/thaynes43/haynesnetwork/blob/8ed34646/.agents/plans/074-one-kavita-series-per-book.md)
(lines 207–229) and
[DESIGN-028](https://github.com/thaynes43/haynesnetwork/blob/8ed34646/docs/designs/028-integrations-tab-goodreads-requests.md)
(lines 1835–1878) preserve original capture clocks, fresh independent SOURCE/MAIN
evidence, and restore-first behavior. Authoritative dependency captures begin
after the real fences and stops; staging manifests or selection previews while
live does not replace them. The
[two-extra Pathfinder profile](https://github.com/thaynes43/haynesnetwork/blob/8ed34646/.agents/plans/074-pathfinder-two-extra-profile.json)
(line 38) explicitly preserves the 170-second restoration trigger. The prior
private prepared supervisor at
`/home/dev/work/hn-825c-fence-prepared/copy-bound-supervisor-prepared-v1/`
separately fixes a 300-second
maximum service absence, abort at 250 with a 50-second restoration reserve, and
a publisher writer lease ending no later than the minimum of phase deadline
minus reserve, publisher start plus 65, and publisher finish plus 35. Its first
observed service stop starts that maintenance clock. These private prepared
bounds do not authorize a future pause; the current stage still needs a fresh
exact packet and root ratification.

The [owner copy ruling](https://github.com/thaynes43/haynesnetwork/issues/831#issuecomment-6048523309)
required preservation of the LL keeper, backup of eligible extras, and refusal
for any dependency. The reviewed normative text and
owner ruling do not specify numerical LIVE 180/200 bounds. That distinction does
not revoke the current frozen profile or permit changing it without review.

## Next decision remains separate

A separately reviewed longer **read-only** capture could keep production live
and preserve the original 300-second consumer age and unchanged restoration
trigger. Capture success would only establish a baseline lifecycle, not COPY
readiness. At a 280-second capture finish, at most 20 seconds remain on the
original byte clock, before native transport, fresh stopped SOURCE/vendor/app
proofs, publisher checks, assembly, MAIN validation, or moves. Every earlier
publisher/dependency deadline also continues to apply. The next profile must
explicitly refuse COPY when that residual budget cannot cover its complete
reviewed stage and restoration reserve.

The current approved copy direction has two extra files and three distinct
selected byte paths (about 2.16 MB), replacing the old 21-move / 41-path scope
(296.5 MB selected bytes). A fresh LIVE selection may use exactly that reviewed
scope while retaining complete whole-corpus proof. This reduction and the
measured 10.605-second permission pass do not prove a 180-second full lifecycle.
No collector optimization, full producer retry, clock extension/reset, service
pause, or COPY is authorized by the diagnostic or this report.
