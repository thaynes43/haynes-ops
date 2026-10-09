# Private native Kavita scanner fixture (preparation only)

This fixture prepares the actual scanner proof for haynesnetwork #825 / #840. It is
not a production catalog writer, scan, strip or runtime approval. The same-ID candidate
and completed offline forward/inverse evidence are in haynesnetwork PR #871. All raw
database, EPUB, user state and authentication fields remain private. Never send those
payloads to public git, GitHub Actions, build contexts or CI artifacts.

The reviewed candidate changes three Series keys and four Volume keys while retaining
the existing series/volume/chapter/file IDs. Offline SQL shape proves only those fields
changed and the inverse restored all logical rows. Actual scanner execution remains
required before any owner ruling authorizes that new production writer.

## Exact native execution architecture

Use the deployed native tag and image digest
`docker.io/jvmilazz0/kavita:0.9.0.2@sha256:ca6af7a18d7124d014702983c2364e485294f808c1552e9555f2595b7cda7982`.
Its published Kavita.Server.dll uses self-contained Core/ASP.NET 10.0.1. Tagged source
commit is `6bcd5689385d0e96824982d843c54f15ce784ddc`. A generic source-only .NET harness
loads the actual native assembly, reflects its private CreateHostBuilder and builds
its dependency-injection graph. Never invoke Program.Main, host.Start/Run, startup
migrations or any hosted/Hangfire worker. Build only the generic harness in CI; no
private fixture input is part of any build or image.

The future isolated worker contains only emptyDirs: the private original and candidate
native database copies, exact original/stripped one-EPUB pair and immutable proof input.
The candidate EPUB is mounted at its actual scanner path and candidate DB at the
native config path. No production mount/PVC/hostPath, ingress, external service token,
service-account token or provider/acquisition path is allowed. The clone itself has
authentication fields and therefore remains entirely private. Require an independently
verified network-deny barrier; an empty list of mounted credentials does not establish
network isolation. Keep the production service and schedules running.

`prepared-runtime.json` is deliberately a non-applicable preparation wrapper, with
runtime approval false and unresolved exact phase/image fields. Its worker is fixed to
talosw01, one attempt, original 180-second deadline, 500m maximum CPU, nonroot/read-only
root filesystem and four memory emptyDirs. The separate proposed Cilium policy uses
explicit all-entity ingress/egress **deny**, which takes precedence over additive allow
rules. Before a future Job, adopt the exact phase policy through GitOps, verify actual
policy/endpoint realization and bind the independent proof to its exact Job/Pod UIDs.
The source template alone does not establish that live isolation. The child checks
exact tmpfs mounts and refuses a service-account token; those are additional guards.

Before loading native services, verify exact input byte hashes, schema, triggers,
full original/candidate row delta, retained target IDs, full private saved/curation/lock
rows, original EPUB hash and stripped-member preservation. Snapshot all native rows.
Then execute the actual native ScannerService.ScanSeries(targetSeriesId, true), never
a full library scan: only the one target EPUB is mounted. Resolve and exercise actual
SeriesRepository cleanup using the **complete retained parsed-name set** from approved
full source evidence, not just the target or an inferred partial corpus. No unrelated
work may be treated as missing. Never execute a Python/C# rewritten scanner algorithm
as a substitute for the actual native methods.

Compare every saved-state/history/curation/lock row verbatim and all retained IDs after
the scanner. Any undeclared metadata difference refuses. Only explicitly reviewed
catalog-derived scan fields may differ. The exact successor admits 25 changed cells
on Series1650, Volume1800, Chapter3358, MangaFile3570 and SeriesMetadata1650. Before
ScanSeries, the actual native book/parser/count/chapter/publication/Koreader helpers
derive eleven deterministic scalar values. A separate private cover directory uses
native GetCoverImage and cloned EncodeMediaAs/CoverImageSize. The actual volume cover
used after scan must have identical encoded bytes. Native CalculateColorScape uses
Random-initialized k-means; the former deterministic color premise is withdrawn.
Only Volume1800 PrimaryColor/SecondaryColor admit native-generated null or exact
uppercase #RRGGBB, with pinned CalculateColorScape/UpdateColorScape method provenance;
every other cell retains its strict predicate. There is no reseeding, repeated draw
or rescan, and different encoded bytes refuse. The actual helper UnitOfWork context
inherits the sealed launch gate's umask077, checked from /proc/self/status before
Build/helpers and after scan. Native cover files must already be600; no post-write
chmod may conceal their original custody.
The actual helper UnitOfWork context
must be the scoped DataContext, with no Added/Modified/Deleted entries after
DetectChanges and no persisted row changes. No tracker clearing or saving is allowed.
The actual EF10.0.6 cancellation overload must decode to virtual bool-overload dispatch;
both native async hooks and the native uint increment hook are checked. RowVersion is
original+2 without overflow. The twelve scan clocks are bounded by ScanSeries itself,
with the existing one-second allowance and New York/UTC interpretation. File upload
mtime is custody evidence and cannot fill these scan values.

The inverse restores all 32 distinct cells from the immutable original snapshot in
one transaction: five UPDATEs compare every post-scan row column, including nullable,
unchanged and typed values, and each must affect exactly one row. Final schema, all
rows/IDs/FKs and typed-cell digest must equal the original before commit. No
INSERT/DELETE, saved-state write, rescan or database replacement is allowed. These
checks grant no production catalog writer approval. Emit aggregate identities/hashes only;
native logs and private payloads stay in the private workspace, never public CI logs.

The native method can return early, so Task completion alone is insufficient: the
target's LastFolderScanned must change inside the actual invocation's time interval.
Native ScanSeries may enqueue its normal in-memory jobs; no host or Hangfire server
is started to run those jobs. Dispose the built native host before full readback.
Check every EPUB member's identity/order, metadata and content hash against the exact
original. Only separately reviewed OPF before/after hashes from the sanctioned
series-only strip proof may differ; that private byte proof must establish the allowed
OPF deletion semantics. This harness does not infer a new stripping policy.

The full baseline is captured before native Build. Readback immediately after Build,
then after resolving scanner/job-store/DbContext dependencies but before ScanSeries,
and all native projection helpers, must be identical across every table and schema;
no constructor/helper write allowance exists. The scoped pending-state check also
prevents deferred writes from entering the scan.
The actual native DI DataContext connection must report exactly the candidate path in
PRAGMA database_list and its live open file inode must match that private input file.
ApplicationStarted must remain false before/after native invocation. Future root runtime
proof additionally verifies realized network deny and no admitted outbound traffic;
these source checks do not replace that external isolation proof.

The fixture sets the live `America/New_York` timezone and opts out of k8tz injection on
Job and Pod metadata. Native runtime admission checks actual TimeZoneInfo.Local ID and
current offset, not just the TZ environment string; CI exercises those checks in the
exact native image. A new init container or timezone mount is not implicitly admitted.
The guard always compares exact Dockerfile/protocol tag and digest internally. This
reviewed diagnostic Dockerfile alone is excluded from Renovate; production upgrades
remain independent. A production-only tag/TZ upgrade reports the frozen fixture obsolete,
passes its informational source check and never builds/publishes a fixture image. An
actual fixture-source build must match the current HelmRelease tag/TZ. A finite two-path
regression proves a production-only upgrade succeeds while an obsolete source build or
internal digest drift refuses. Source changes alone and explicit dispatch may publish.
Before runtime, freshly verify the actual production Pod imageID/native module bytes
and timezone: a matching mutable tag alone is insufficient. Do not change production
deployment pins or assume the prepared Job's admission equals the source template.

The private source proof has a required `LiveNative` object: observed-at time, native
Pod UID, exact tag/image digest, actual timezone/current offset and all eight native
module path/SHA pairs listed by FixtureProtocol.NativeModules. Capture those actual
read-only production facts after the fixture Job's original start and before private
input delivery; no previous/restamped identity is accepted. The child checks the full
tuple and compares every module to the actual fixture's native bytes before Build.
Changed live tag, imageID, module or timezone refuses until a reviewed successor exists.
This exact-version guard is independent of whether production-upgrade CI is required.
The future host must check live identity again before ratifying the resulting proof.

Each before/after-Build/after-binding/after/inverse snapshot is created mode 0600 and
fsynced. A refusal after
native execution still offers its available before/after evidence. The child emits only
the receipt's aggregate hashes/UIDs, then waits at most 30 seconds **within the original
deadline** for an exact phase/Job/Pod/receipt-SHA durable-copy ACK. The host must copy all
receipt-listed snapshots to an exclusive private directory, verify their exact bytes,
fsync files/directories, and atomically deliver the ACK. Missing/mismatched/late ACK is
unknown outcome, not success. Terminal private handback never authorizes retry.

Private input files are delivered with mode 0600 and no symlink ancestry; the approved
packet is delivered last by atomic rename after all pinned input bytes are durable.
Future host preparation must bind full native inventories, input hashes, signed image,
manifest/admission/defaults and source receipt before delivery. Foreground deletion by
the exact recorded Job UID and independent complete raw Job/Pod union absence are
required on success, refusal, expired capture and host interruption. No host launcher
or runtime execution authority is supplied by this preparation commit; that exact
delivery/cleanup packet must receive independent review and root GO before any Job.

## Gates before any test Job

This source is preparation only. Root must first review the completed candidate plan
and independent receipt, then the exact generic assembly/build/runtime hashes, native
source/image/module pins, private input SHA, schema and precise allowlisted metadata,
prepared manifest and closed native Job/Pod UID lifecycle. No Job is created here.
Use one worker, `nice -n 19`, CPU limit at most 500m, bounded memory and no wide/looped
tests. A proposed fixture's original Job deadline is at most 180s; complete exact-UID
foreground cleanup and independent all-owned Job/Pod union absence remain mandatory.
Unknown outcome preserves private evidence and halts without retry or broader scan.

The current dev pod has no SDK, and `mcr.microsoft.com` is not resolvable through its
allowlist. Do not use a proxy or mirror workaround. Generic compiler setup may run on
CI using the official version/checksum; its exact assembly and runtime source must be
reviewed before a worker can run. No actual scanner proof is claimed until that exact
isolated native invocation completes with preserved state and cleanup evidence.

## Generic build and finite validation

The path-scoped workflow resolves the official SDK 10.0.101 image to an immutable
digest once, checks that SDK version, and publishes a self-contained linux-x64 apphost
with Core/ASP.NET runtime 10.0.1. The final image derives from the exact deployed Kavita
digest above; it does not rely on a dotnet muxer/shared-framework installation in that
image. A Dockerfile-specific deny-all build context includes only the four generic
source/build files. No private database, EPUB, approval packet or user payload reaches
the build context, CI image or Actions artifact.

For fixture source changes CI launches that actual apphost in the exact native base with network none/read-only,
500m CPU and `nice -n 19`. The finite self-test checks 11 malformed packet refusals,
three durable-ACK refusals, six fresh live-native drift refusals, storage-type boundaries, saved-state mutation/removal
refusals, exact inverse and actual published reflection signatures, without building
the host or scanning any fixture. A separate finite child blocks on a full undrained
stdout pipe; the original deadline must still exit it with code 124. The hard-exit
callback performs no telemetry or other blocking output before termination. Seven Python
checks cover the prepared manifest, current GitOps version/TZ and
build-context barriers. Initial public CI run 37968299781 passed compilation and native
apphost launch at f657b5d8, without private inputs or host/scan. Subsequent source changes
require their own current-head pass; neither launch establishes scanner behavior.

CI records only official SDK and generic/native module hashes. On main fixture-source
changes it publishes
the same tested image without rebuilding and signs its digest. Fresh root review must
bind that image, apphost/runtime/assembly/source receipt and the reviewed private input
packet before runtime. This source package is preparation, not catalog-writer approval.

Native SQLite scan timestamps have no offset suffix. The tagged native code writes
local fields with DateTime.Now and `*Utc` fields with DateTime.UtcNow. Interpret
`*Utc` cells explicitly as UTC and other scan clocks in the verified New York local
zone, using invariant culture; do not apply the local offset to a UTC cell. The
actual-runtime self-test checks matching local/UTC instants, explicit offsets and
malformed, stale and future refusals through the same bounded-clock helper. This
changes no allowance or state preservation rule. The earlier published diagnostic
image is obsolete for private execution until the reviewed successor is signed
and hash-bound by the host.
