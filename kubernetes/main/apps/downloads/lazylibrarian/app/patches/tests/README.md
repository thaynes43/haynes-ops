# LazyLibrarian overlay harness

Tests for the patched upstream files in `../` (haynes-ops #3402). Everything runs inside the LazyLibrarian
image pinned in `../../helmrelease.yaml`, never against the live pod or its database.

## What it checks

1. **Pins.** Each overlay's header names the sha256 of the upstream file it was cut from. The image's copy must
   have that sha256, and applying `diffs/<name>.diff` to it must give the overlay byte for byte. So the overlay is
   the pinned upstream file plus exactly the reviewed diff, nothing else.
2. **Behaviour.** `test_*.py` run on upstream's own unit-test scaffolding (`unittests/unittesthelpers.py`: config,
   logging, a fresh throwaway database) against a copy of the image's code with the overlays dropped in. Network
   is blocked; indexer, SABnzbd and Google Books calls are mocked.

| Overlay | Test |
|---|---|
| `resultlist.py` | the volume penalty: the four Assistant to the Villain releases, the Discworld record, the guards (Inheritance, Fahrenheit 451, Harper Connelly, Chroniken 01, a "41 Discworld novels" count), plus `fixtures/resultlist_replay.json`: 296 anonymised rows of the 2026-10-06 replay over the live `wanted` table, each with its upstream and patched score; the language penalty: each tag form of the grab history (`DANiSH`, `WEB-SE`, `2MP3CD-DE`, `WEB-MP3-DE`, `[GER / EPUB]`, `[French]`, `EB-NL`, `(Ungekürzt)` and its mojibake) for an English book, and the guards (another `BookLang`, `[ENG / EPUB]`, `WEB-EN`, a language word in the title: Learn French, The Danish Girl, IT); the blacklist: a `Failed` row blocks its release under any spelling (spaces for dots, case, underscores, the title in its link's `file`), only for its provider and format |
| `dbupgrade.py` | the start-up check keeps an author that owns a book with no `bookauthors` row, and its census logs `LL_UNLINKED_BOOKS` for such books |
| `gb.py` | the API's `addBook` writes the `bookauthors` row |
| `librarysync.py` | no source-id carry-over between files; a `remove` scan keeps authors that own books |
| `postprocess.py` | an aborted SABnzbd job is never post-processed; a torrent's -1 still is |
| `sabnzbd.py` | the NZB is uploaded (`mode=addfile`), the indexer URL and its key never reach SABnzbd or the log |
| `searchbook.py` | one category search per format, the backlog honours DELAYSEARCH, the back-off caps at 7, an identical query is sent once |

The tests target behaviour, not files: run with `--drop all` (or `--drop <name>`) and they run against the image's
own copies, where every test of a fix fails. That is also how to find out at a bump whether upstream fixed a bug.

## Running it

- **CI:** `.github/workflows/lazylibrarian-overlays.yaml` runs on every PR that touches
  `apps/downloads/lazylibrarian/app/`: `docker run --network none --entrypoint python3 <pinned image>
  /patches/tests/run.py` with `patches/` mounted read-only. The check is advisory (not a required context), so
  Renovate never auto-merges a LazyLibrarian bump (`.renovate/autoMerge.json5`): read the check before merging.
- **With docker:** the same command from the repo root:
  `docker run --rm --network none --entrypoint python3 -v "$PWD/kubernetes/main/apps/downloads/lazylibrarian/app/patches:/patches:ro" docker.io/linuxserver/lazylibrarian:<tag> /patches/tests/run.py`
- **In the cluster (no docker, e.g. the dev-env pod):** `./oneshot.sh [run.py args]` starts a throwaway Job on the
  pinned image (1 CPU cap), runs the harness once and deletes the Job.

Everything is sequential: one process, one test at a time. Never run it in a loop or in parallel.

## Changing an overlay

1. Edit the overlay and its header comment.
2. Regenerate its diff inside the pinned image: `./oneshot.sh write-diffs` (or `run.py --write-diffs DIR` in a
   container), and commit it next to the change.
3. Add the case to the test, and run `run.py` (and `run.py --tests-only --drop <name>` to see the new test fail on
   upstream).
4. A scoring change (`resultlist.py`): replay the history first, `./oneshot.sh replay /tmp/new.tsv`, once with the
   old overlay and once with the new, and hand-check every row whose score moved (the TSV's `volume` and
   `language` columns say which penalty moved it). The TSV is the whole download
   history with providers, dates and requester tags: keep it out of git. `fixtures/resultlist_replay.json` is a
   deliberate, curated subset of it (the rows that test the penalty, deduplicated), kept to title, author and
   release name: no provider, URL, size, date, requester tag or scene group. Fold changed rows in the same way.
5. After the deploy: `./verify-deployed.sh` (the pod serves exactly these files, each module imports, no traceback).

## Bumping the image tag

1. Point a scratch run at the new image: `run.py --rederive /tmp/new` applies every `diffs/*.diff` to the new
   image's files (hunks may have moved) and lists the hunks that no longer apply.
2. For each overlay: if `--drop <name>` passes on the new image, upstream fixed it; delete the overlay, its diff,
   its ConfigMap and its mount. Otherwise take `/tmp/new/<name>.py`, re-apply any failed hunk by hand, put the new
   upstream sha256 in its header, copy it into `patches/` and `--write-diffs`.
3. Bump the tag in `helmrelease.yaml`. CI runs the whole harness against the new image before anything deploys.
