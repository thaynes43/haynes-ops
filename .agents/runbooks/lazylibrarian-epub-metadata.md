# LazyLibrarian EPUB grouping metadata

The existing hourly converter implements
[the decision](https://github.com/thaynes43/haynesnetwork/blob/main/docs/adrs/105-one-kavita-series-per-book.md)
and [the converter design](https://github.com/thaynes43/haynesnetwork/blob/main/docs/designs/028-integrations-tab-goodreads-requests.md#amendment-2026-10-07-one-kavita-series-per-book-issue-825-adr-105).
The work order is [issue 825](https://github.com/thaynes43/haynesnetwork/issues/825).

The CronJob ships with STRIP_SERIES_METADATA=0. Conversion of MOBI/AZW3-only
folders continues. Metadata removal requires STRIP_SERIES_METADATA=1.
STRIP_ONLY=1 skips conversion and partial-file cleanup; use it for inventory and
backfill Jobs. STRIP_FOLDERS_JSON selects exact relative book folders and requires
STRIP_ONLY=1. Without that variable the pass visits all EPUBs under EBOOK_ROOT.

LIBRARY_HOLD_FOLDERS_JSON lists exact normalized library-relative book folders
that every mutation path must preserve. Malformed or unconfined entries stop the
job before writes. Holds apply to stripping, conversion, cleanup and restore,
including targeted one-offs; the read-only collision census still includes their
metadata. A held folder is reported separately and is never called untagged.
The configured hold is Daniel Silva/Ransom. Its EPUB has a saved Kavita reading
location and session history, so its original metadata and files remain intact
until its saved reading state has been idle for 30 days, as ruled in
[the state-preservation decision](https://github.com/thaynes43/haynesnetwork/issues/840).
No progress migration is authorized. Removing this hold requires a separate reviewed
GitOps change after the idle check; the converter never ages a hold out itself.
Compare that state before and after every migration scan. This hold stays in git
when the temporary migration pauses are removed and hourly stripping is enabled.

The pass removes calibre:series, calibre:series_index, all belongs-to-collection
metadata and the collection-type/group-position refinements of those collections.
It preserves every other OPF byte and every ZIP member's uncompressed content.
Other members retain their relative order and metadata. A changed archive's
mimetype is placed first and stored, including when the original used a different
order or compression. ZIP compression streams can change. Unknown refinements to
a removed ID cause refusal.

Before any edit, a bounded read-only preflight reads every EPUB's title, creator,
series and main-title sort aliases using Kavita 0.9.0.2 name normalization. A tagged
file with the same title as a different author's book receives the owner's
[Title (Author) grouping tag](https://github.com/thaynes43/haynesnetwork/issues/830):
calibre:series is the unchanged OPF title followed by the unchanged OPF creator in
parentheses, and calibre:series_index is 1. Only author-role credits qualify;
EPUB3 role refinements and EPUB2 opf:role exclude editor/translator credits.
Combined credits such as "Quinn, Enoch, Hawkins, Ryan" are ambiguous and held;
the converter never guesses comma-separated author boundaries. This also separates existing mixed-author
groups, including untagged EPUBs. Only unambiguous title/creator metadata qualifies;
missing or conflicting identities remain held. Different creator spellings that
share an existing author/folder alias are also held, since they may be one person.
This ambiguity excludes those copies from consolidation too. Ordinary books still lose their
grouping tags. The inserted tags are the only additional OPF bytes, and an already
correct pair is unchanged. An unreadable identity, unsafe
path or exhausted preflight budget stops the entire mutation pass, including
conversion. The census reads container and OPF metadata only, with a 4 MiB limit per
XML document, 10,000 ZIP entries and bounded central-directory reads. It accepts
harmless DTD declarations and refuses entity definitions or resolution. In Kavita's
metadata-enabled Books library, an untagged EPUB with no title is known to be
unindexed and contributes no grouping aliases. Untagged EPUBs are changed only when
the same-title, different-author policy requires the dedicated grouping tag.

A reviewed private manifest may resolve one known ambiguous creator credit for
grouping only. `grouping_author_identity` requires the exact relative file path,
whole-file SHA-256, OPF SHA-256, title, ISBN, ordered raw creator list and owned
frontmatter member SHA-256. Its selected author must already be an unchanged raw
creator, with no explicit non-author role, and the manifest records the primary
publisher/provider evidence. A different file, hash, title, ISBN or credit refuses
the exception. This supports the publisher-verified R.L. Stine edition of The Face
whose second raw creator is its cover artist. It never edits creator credits or
roles, changes copy-consolidation eligibility, or establishes a general alias.
The supported manual entrypoint is `epub_convert.py --strip-author-proof <manifest.json>`,
with `STRIP_ONLY=1`, `STRIP_SERIES_METADATA=1` and explicit `STRIP_FOLDERS_JSON`
that includes the approved file. The manifest is the schema-1 `grouping_author`
record described above, outside EBooks. It is loaded and validated against the
complete current identity census and source bytes before any metadata edit. A
missing, changed or out-of-scope proof refuses the entire pass. Ordinary scheduled
stripping and copy retention do not load these proofs. The reviewed stage writes only `The Face (R.L. Stine)` and index 1; all other metadata stays exact.

Full archive validation applies to files being changed: at most 256 MiB compressed
and 512 MiB expanded, every ZIP member's CRC checked, and a first, stored mimetype.
With stripping disabled, ordinary conversion publishes in fixed-size chunks and
verifies the known-size output hash, preserving support for large converted EPUBs.

## Backups and refusal

Originals and JSON manifests live directly in STATE_DIR/backup, normally
books/.epub-convert/backup/, outside EBooks/. Names contain hashes of the original
relative path and original bytes. Each manifest records that path, original and
sanitized SHA-256, original mode/owner and whether the original was an existing
EPUB or fresh conversion output.

Retention is indefinite. No automated pruning is implemented. Delete backups only
after an explicit owner decision, preserving inventory needed for reading-list
recipes. A matching backup can be reused; corruption or a conflicting manifest
refuses the edit.

The job verifies the backup before writing a hidden sibling temporary EPUB. It
validates every ZIP CRC, first/stored mimetype, every declared package document,
absence of removed tags (or the exact approved dedicated tag/index pair), no dangling
refinements, and unchanged unrelated bytes
and ZIP metadata. It rechecks source inode, size, timestamps and hash before atomic
replacement. Symlinked paths, hardlinked EPUBs, nonregular inputs, signed EPUBs,
unsupported ZIP encodings, changing and unsettled inputs are refused or deferred.
Limits: 256 MiB compressed, 512 MiB expanded, 10,000 ZIP entries and 4 MiB per XML.
These limits apply to EPUBs. Read-only MOBI/AZW3 originals may be hardlinked or
larger; their identity checks read descriptor metadata without buffering content.

Successful changes touch book/author folders and queue one Kavita library scan.
The existing state-directory lock serializes scheduled, backfill and restore runs.
DRY_RUN=1 writes nothing to the library or state, creates no lock and queues no scan.

## Inventory and staged Jobs

Deploy the app's pairing safeguards, complete the after-strip Held File Check and
adversarial review, and repair findings or record justified Census Holds. Keep the
scheduled gate off throughout inventory and staging.

During the controlled migration, git suspends the EPUB converter and the app's
books, Goodreads, collections and format-pairing CronJobs. Libretto's configured
LazyLibrarian URL is temporarily empty so no acquisition context can be created
while Kavita identities change. Existing recipe policies remain intact. Verify
those deployed settings before running manual Jobs with the reviewed app image.
Before a pause, checkpoint the exact schedules and acquisition setting in the
migration report. Prepare and review one GitOps PR that reverses the pause, and
arm a recovery watchdog before merging the pause. Pause only for approved metadata
edits, Kavita scans, file validation and reading-state comparison. Restore the five
schedules and Libretto URL immediately after that window succeeds or fails, with
the strip gate still off. Restore before app, pairing or reading-list verification,
long waits, or uncertain investigation. Enable hourly stripping later in a separate
GitOps PR after those checks pass. Declare the tight maintenance window before
deploying its pause and end the activity promptly after restoration.
Confirm the runtime configuration directly without printing credentials:

    kubectl exec -n media deployment/libretto -- node --input-type=module -e '
    import { loadConfig } from "/app/dist/config.js";
    import { createLogger } from "/app/dist/logger.js";
    import { createAcquireContext } from "/app/dist/acquire/acquire.js";
    const config = loadConfig();
    const disabled = process.env.LAZYLIBRARIAN_URL === "" &&
      config.lazyLibrarian === undefined &&
      createAcquireContext(config, createLogger("silent")) === undefined;
    console.log(JSON.stringify({ acquisitionDisabled: disabled }));
    if (!disabled) process.exitCode = 1;
    '

Clone the CronJob's pinned image, service account, NFS mount, worker-node affinity,
one-CPU limit and non-root user into each temporary Job. This prepares and submits
a read-only inventory Job; its preflight examines the whole EPUB library.

    kubectl create job -n downloads epub-strip-inventory \
      --from=cronjob/lazylibrarian-epub-convert --dry-run=client -o json \
      > /tmp/epub-strip-base.json
    python3 - <<'PY'
    import json
    job = json.load(open("/tmp/epub-strip-base.json"))
    container = job["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item for item in container["env"]}
    for name, value in {"STRIP_SERIES_METADATA": "1", "STRIP_ONLY": "1", "DRY_RUN": "1"}.items():
        env[name] = {"name": name, "value": value}
    container["env"] = list(env.values())
    for mount in container["volumeMounts"]:
        if mount["name"] == "books":
            mount["readOnly"] = True
    job["spec"]["ttlSecondsAfterFinished"] = 86400
    json.dump(job, open("/tmp/epub-strip-inventory.json", "w"))
    PY
    kubectl create -f /tmp/epub-strip-inventory.json
    kubectl logs -n downloads job/epub-strip-inventory \
      > /tmp/epub-strip-inventory.jsonl

Wait for the Job to finish before collecting complete logs. Preserve JSONL outside
public git, on durable storage under books/.epub-convert/inventory/.
epub_series_strip lines contain extracted names/indexes for Libretto reading order,
source/candidate hashes and collision Holds. Refusal/preflight lines identify exact
paths. epub_series_strip_census counts stripped, would-strip, untagged, unchanged grouped, settling,
refused, deferred and collision-held. A refusal or exhausted budget exits nonzero;
collision Holds allow remaining eligible files to finish.

Declare activity for downloads,lazylibrarian,media,kavita before library edits.
Prepare a fresh Job from the CronJob with STRIP_SERIES_METADATA=1, STRIP_ONLY=1,
DRY_RUN=0 and STRIP_FOLDERS_JSON set to exactly:

    ["Suzanne Collins/The Hunger Games", "Suzanne Collins/Mockingjay"]

Retain the writable books mount and use a unique Job name. Verify backups, actual
file coverage in Kavita, books-sync identities, Request Events and pairing without
unintended pushes after its scan. End activity when verification finishes. Only
then run a full STRIP_ONLY=1 Job without STRIP_FOLDERS_JSON, preserving justified
Holds.

Enable the scheduled gate in a new GitOps PR only after the full eligible run and
app state are verified. Record the next 04:00Z scan as a dated Owed Check and verify
it. Never stress this pod or run wide or looped tests. Tests run once at low
priority; calibre CI runs sequentially on one CPU.

## Restore

Disable the scheduled gate through git and verify Flux applied it. Declare activity
and clone the same CronJob into a uniquely named restore Job. Set
STRIP_SERIES_METADATA=0 and STRIP_ONLY=0, remove STRIP_FOLDERS_JSON, and append these
arguments to the existing command:

    --restore-backup
    /data/cephfs-hdd/data/media/books/.epub-convert/backup/<manifest-name>.json

First run with DRY_RUN=1 and the books mount read-only. The manifest must be directly
under the configured backup folder, its original relative path must stay inside
EBOOK_ROOT, and its original checksum and EPUB must validate. The current file must
be a safe valid EPUB matching the saved sanitized hash. Divergence refuses restore.

Repeat with DRY_RUN=0 and a writable books mount after that check. Restore uses the
shared lock, a synced sibling temporary file, source recheck and atomic replacement,
touches book/author folders and queues one Kavita scan. Verify scan and app state,
end activity and retain backups. Restore reinstates grouping metadata; resolve its
policy before re-enabling the gate.

## Same-title copy consolidation

The normative [same-title grouping and retained-copy decision](https://github.com/thaynes43/haynesnetwork/blob/main/docs/adrs/106-distinguish-and-retain-same-title-books.md)
permits the narrowly scoped grouping tags and guarded copy retention below.

The owner's [keep one copy ruling](https://github.com/thaynes43/haynesnetwork/issues/831)
uses a separate manual `--consolidate-copies <snapshot.json>` command, under the
converter lock. STRIP_SERIES_METADATA never enables moves. Do not combine this
command with conversion or stripping. First run with DRY_RUN=1 and a read-only
library mount, inspect every retained copy and intended move, then produce a fresh
reviewed snapshot and apply under the same operational pauses.

The snapshot is private operational data outside EBooks, not a committed manifest.
Schema 1 contains `created_at` (UTC ISO timestamp), `ebook_root`, and four complete
source sections: `lazylibrarian`, `census`, `kavita`, `app_wants`. Each section has
`complete: true`, `quiesced: true`, `capture_started_at` and `checked_at`, plus
`quiescence_established_at`, `quiescence_checked_at` and observable `quiescence_proof`.
Source captures must record their start before the first read/copy, and completion
must not precede that start. The fence must precede each source start and the
`library_capture_started_at`. Missing starts or reversed ordering refuse the
snapshot. Capture and verification timestamps must be within five minutes of
application. Missing, incomplete, stale or future evidence refuses the command.
The operator must obtain complete current reads, never infer an empty dependency
list from an API error or partial page. Parent migration coordination owns those
reads and all service pauses. The converter rechecks snapshot freshness for each
move; expiry ends application safely with remaining copies intact.
All hashes share the earliest source expiry deadline. Freshness is checked after
each keeper hash and immediately before removing the original directory entry.

`files` is the complete EPUB census, each entry `{path, sha256}` relative to
EBOOK_ROOT. It must exactly match the fresh read-only converter census, and every
hash must match before any moves. `lazylibrarian.pointers` contains all nonempty
BookFile paths, each `{book_id, path}` relative to EBOOK_ROOT. Include pointers to
other formats too: these do not establish an EPUB keeper. Paths outside EBooks
must be ruled out by the reviewed collector, not silently omitted from a complete
claim. The other sections contain `protected_paths` (exact relative files or
folders) with `path` and a nonempty `reason` describing the dependency. Census
repairs and Census Holds must be represented. Kavita protection includes every
saved progress/session/bookmark/annotation dependency, including a nonempty XPath
with zero numeric counters. App wants include every dependent active request and
pairing/library anchor. Resolve ids to all affected file paths before declaring a
source complete; unresolved or partially read state refuses snapshot production.
Kavita protections also include current reading-list items, list-remap rules,
user collections, wants, ratings, custom contents, on-deck preferences, per-series
reading profiles, scrobble state, curated series relations, blacklists and legacy
collection membership. JSON series references and indirect metadata/event foreign
keys must resolve too. The schema inventory follows all foreign-key paths to work
and user tables, regardless of names; unfamiliar populated user tables or
unclassified work-reference tables refuse. Saved Kavita metadata locks protect every
current file linked to the locked chapter, volume, series, series metadata, collection,
reading list or person. Unknown populated lock tables, invalid flags and unresolved
locked references block copy movement. Explicitly classified catalog metadata without saved locks
(titles, genre/person/tag joins, imported provider metadata and parser-error
diagnostic keys) is a rebuildable
read model, not saved user state. Identity/authentication, global reader settings,
library permissions, device and dashboard tables have no physical copy binding. Protect
all joined chapter/volume/series files; do not assume the BookFile keeper can replace
a referenced copy or rewrite the list to permit a move. Unknown saved-book
dependency tables or orphan file relationships keep the source incomplete.
Quiescence is a separate required attestation, never inferred from freshness or
two identical database reads. Stop dependency writers for the complete application
window and record observable proof. This includes LazyLibrarian's internal import/
postprocess work and manual/library-scan entrypoints, Kavita readers and background
jobs, app requests/pairing jobs, and census-repair writers. Pausing only the app's
five sync CronJobs and Libretto acquisition is insufficient. If idle cannot be
established and held, keep the corresponding copies protected and do not apply.

The read-only `epub_copy_preflight.py` adapter collects the current library's OPF
identity, stable source identity, SHA-256 and ancestor ignore markers without
changing files. Its prepare command combines this full census with the app audit,
all LL BookFile SQL rows, a stat-guarded Kavita database copy and explicit census
protections. It produces a private candidate/protection report and schema-1
snapshot. Captures retain their original timestamps; stale/partial inputs and
missing writer attestations are reported as blockers rather than made fresh or
complete. Each writer attestation supplies `established_at`, a fresh `checked_at`
and observable `proof` for the continuously held fence. The fence must have been
established at or before both the source capture and the library census start.
A stop performed after either capture cannot make that earlier capture safe.
LL and Kavita capture inputs use `captureStartedAt`; the app transaction audit and
census protection input use `capture_started_at`. The library collector records
`started_at` before its first filesystem read. Missing start evidence is unknown,
even when the capture completion is recent.
App request anchors resolve through the actual Kavita file map; an
unresolved request dependency blocks snapshot completeness, including parked and
landed requests that retain a library anchor. Any saved Kavita state
row protects its joined files, including zero-counter locations. The report is
exploratory while any blocker remains and is never permission to move a file.
Aggregated reading history resolves removed chapter ids through the same stored
activity's current series/volume, protecting that work's current files. A legacy
activity is classified as disconnected only when all of its series/chapter/volume
ids are absent from the complete current tables and no MangaFile references its
chapter. The report retains its ids and whole-row checksum. It never rewrites or
omits the historical row. Unknown current references or orphan file relationships
keep the source incomplete.

Live observation on 2026-10-08 found LL's `showJobs` failing HTTP 500 and
`showThreads` returning Code 501 because psutil is absent. These are unknown idle
state, not proof of inactivity. LL's internal PostProcessor defaults to a ten-minute
schedule, and API/manual imports and library scans are independent entrypoints.
Suspend `lazylibrarian-library-scan` through git as well as the converter when
consolidating. Kavita's production API does not expose its complete Hangfire queue;
scan completion also does not stop readers from writing locations and sessions.
A reviewed GitOps downtime hold of LL and Kavita, verified with no running app
containers and no active LL library-scan Job, is the comprehensive writer hold.
Without such a hold or an equally observable gate covering all those entrypoints,
their quiescence attestations stay false and consolidation stays blocked. The
parent migration session owns any downtime decision, declarations and restoration.

Groups require one unambiguous OPF title and author-role creator shared by all their copies;
they are not grouped by folder spelling or fuzzy title matching. Exactly one
existing EPUB in the group must be pointed to by BookFile, with one LL book id.
That copy stays. No keeper or competing BookFile copies retain the entire group
for review. An extra pointed to by any LL record, a configured library hold, any
source protection, or an ancestor `.ll_ignore` stays and is listed for review.
An `.ll_ignore` file is treated as folder protection regardless of its content;
its symlink or an unsafe directory refuses the operation. The Ransom hold applies
to this command and descendants as it does to every other mutation.

Only verified, settled, single-link regular EPUBs can move. The destination is
STATE_DIR/copies, outside EBooks and on the same filesystem. Each move has a
durable hash/path manifest, original owner/mode and the evidence-snapshot hash.
The converter uses a no-overwrite hard link followed by removal of the original
directory entry, verifies both bytes and source identity before removal, and never
deletes the retained destination. Cross-device moves, symlinks, hash divergence,
source/directory races and a conflicting destination refuse. This retains the
original inode and bytes; originals are kept indefinitely. A crash between link
and source-entry removal leaves both names and requires review, never automatic
cleanup. Do not manually delete the protected in-library name to finish a move.

`epub_copy_consolidate` logs every keeper, protected copy and move; its census
counts retained, protected, moved, would-move, settling and review groups. One scan
is queued only after real moves. A move failure exits nonzero. Under the same
reviewed operational pause, `--restore-retained-copy <manifest>` returns one copy
after validating its saved bytes, complete EPUB, manifest path and original
ownership/mode. Its original library path must be absent. The command publishes
a verified fresh inode with no overwrite, retains the backup indefinitely,
touches book/author directories and queues one scan. A changed or symlinked backup,
existing destination or configured library hold refuses the command. Interrupted
publication can leave a hidden restore artifact for review; never replace a current
file or remove retained bytes to force a retry. Begin with DRY_RUN=1, then verify
the returned file, scan, dependencies and coverage.

A manual selection manifest binds an ordered list of exact source and keeper paths and hashes to the complete
dependency snapshot. Validate the entire selection before any move. Retain and verify the first selected copy,
confirm its keeper and every unapproved file remain intact, then continue the remaining selection only if that
proof passes. Abort on mismatch, lost fence or deadline.

For staged retention, add `--copy-selection <selection.json>` to the manual
consolidation command. The schema-1 `copy_selection` record lives outside EBooks,
requires `approved_for_retention: true`, binds `snapshot_sha256` to the exact complete
dependency snapshot, and contains an ordered nonempty `entries` array of
`{path, sha256, keeper, keeper_sha256}` records. Each entry must independently be an
eligible unprotected extra under that complete snapshot. Missing, repeated, changed,
protected or keeper entries refuse the entire selection before any move. Narrowing
scope never removes source files, dependencies or protections from the snapshot.

The invocation hashes the complete current corpus once. It moves the first exact
selected extra, verifies the retained bytes/manifest/ownership, absent original and
unchanged keeper, then verifies the complete remaining file census against the
original fingerprints. This includes every protected EPUB and non-EPUB library
file; EPUB fingerprints remain bound to the full corpus hash. It logs a
separate `epub_copy_stage_proof` before starting the remaining selected moves. Any
failure stops continuation. Remaining moves retain per-source identity, keeper,
protection and freshness guards under the same continuously held fence and earliest
source expiry. Unselected extras stay in EBooks. These checks reuse the verified
complete census and avoid another whole-corpus hash inside the 300-second window.
