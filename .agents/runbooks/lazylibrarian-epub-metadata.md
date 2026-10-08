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

The pass removes calibre:series, calibre:series_index, all belongs-to-collection
metadata and the collection-type/group-position refinements of those collections.
It preserves every other OPF byte and every ZIP member's uncompressed content.
Other members retain their relative order and metadata. A changed archive's
mimetype is placed first and stored, including when the original used a different
order or compression. ZIP compression streams can change. Unknown refinements to
a removed ID cause refusal.

Before any edit, a bounded read-only preflight reads every EPUB's title, creator,
series and main-title sort aliases using Kavita 0.9.0.2 name normalization. A tagged
file that would create a cross-author group is held, with conflicting paths logged.
The guard leaves existing shared title groups alone. An unreadable identity, unsafe
path or exhausted preflight budget stops the entire mutation pass, including
conversion. The census reads container and OPF metadata only, with a 4 MiB limit per
XML document, 10,000 ZIP entries and bounded central-directory reads. It accepts
harmless DTD declarations and refuses entity definitions or resolution. In Kavita's
metadata-enabled Books library, an untagged EPUB with no title is known to be
unindexed and contributes no grouping aliases. Untagged EPUBs are never rewritten.

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
absence of removed tags and dangling refinements, and unchanged unrelated bytes
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
After successful backfill, pairing and reading-list verification, restore all
five schedules and Libretto's URL in git together with the scheduled strip gate.
If the migration stops early, restore the schedules and URL through git while
keeping the strip gate off; never leave the temporary pause as an implicit handoff.

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
paths. epub_series_strip_census counts stripped, would-strip, untagged, settling,
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
