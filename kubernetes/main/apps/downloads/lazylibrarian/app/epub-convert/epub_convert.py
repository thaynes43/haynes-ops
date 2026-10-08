#!/usr/bin/env python3
"""Convert LazyLibrarian's .mobi / .azw3-only books to EPUB, beside the original.

Why: LazyLibrarian accepts epub, mobi, pdf and azw3, but Kavita's Books library
opens epub and pdf only, so a book LazyLibrarian holds only as .mobi or .azw3
never reaches the library. Owner ruling on
https://github.com/thaynes43/haynesnetwork/issues/770: keep accepting those
formats and convert them to EPUB after import; keep the original.

What one run does (CronJob lazylibrarian-epub-convert, hourly):
  1. Takes a lock (a directory in STATE_DIR), so a manual run and the scheduled
     one never convert at the same time. A lock older than LOCK_STALE_SECONDS is
     from a killed run and is taken over.
  2. When STRIP_SERIES_METADATA=1, visits existing EPUBs independently, removes
     approved series metadata (same-title/different-author books receive the
     owner-ruled Title (Author) tag and index 1), verifies a backup outside EBOOK_ROOT and
     replaces each unchanged source atomically. The gate defaults OFF. Dry runs
     log extracted series/index metadata for the reading-list inventory.
     Conversion then walks EBOOK_ROOT. A folder holding .epub/.pdf is ineligible
     for conversion (its EPUBs can still be eligible for metadata removal).
     A folder that holds a .mobi or .azw3 and neither of those is a candidate,
     unless a sibling folder of the same author holds the same book (same title
     words) as an epub or pdf: the library already shows it, and a second copy
     only makes Libretto's match ambiguous. Such a folder counts as `duplicate`.
  3. For each candidate, one source file (azw3 before mobi; then the name
     LazyLibrarian gives a new import, "<Title> - <Author>"; then the newest), one
     conversion at a time with `ebook-convert` into the pod's /tmp. Skipped for now
     when a source changed in the last SETTLE_SECONDS (an import in progress), or
     when the run's time budget is spent (the next run takes it).
  4. Checks the EPUB: `ebook-meta` must read a title and an author, and the title
     must match the book's folder name (LazyLibrarian's $Title). A book that
     carries no author gets LazyLibrarian's (its author folder) written into the
     new EPUB; the original is never edited. Only then is the EPUB
     copied into the folder under a hidden name and renamed to
     "<source basename>.epub": the name LazyLibrarian gave the original, so its
     library scan links the book to the epub (EBOOK_TYPE lists epub first).
  5. Touches the book folder and the author folder after conversion or stripping (on this NFS share adding a
     file does not reliably bump the folder mtime Kavita's scan compares), then,
     when anything was converted or stripped and a key is set, queues one Kavita library scan.
  6. A source that fails (DRM, a conversion error, an unreadable or mismatched
     EPUB) is left exactly as it is and recorded in STATE_DIR/held.tsv, so it is
     reported once and not retried every hour. Delete its line to retry it.
  7. Logs a census: the folders still .mobi/.azw3-only that this run should have
     converted (`unconverted`, 0 after every run), plus the held, settling and
     duplicate ones.

Log lines are JSON, one per line, on stdout:
  epub_convert         one per conversion attempt: result converted | drm |
                       convert_error | timeout | unreadable | title_mismatch |
                       io_error (not held: retried next run)
  epub_convert_census  once per completed run
  epub_convert_locked  another run holds the lock; nothing was done
The Loki rules in ../lokirule.yaml alert on them.

Environment: EBOOK_ROOT, STATE_DIR, DRY_RUN=1 (log what would happen, write
nothing), RUN_BUDGET_SECONDS, SETTLE_SECONDS, CONVERT_TIMEOUT_SECONDS,
LOCK_STALE_SECONDS, KAVITA_URL, KAVITA_API_KEY, EBOOK_CONVERT, EBOOK_META.
STRIP_SERIES_METADATA=1 enables metadata removal, STRIP_ONLY=1 disables conversion,
STRIP_FOLDERS_JSON selects exact relative book folders (unset means all EPUBs).
--restore-backup <manifest> restores one original, under the same lock and scan contract.
--consolidate-copies <snapshot> manually retains unprotected extras outside EBooks;
it requires a fresh complete dependency census and never runs from the strip gate.
Backup retention and rollout: .agents/runbooks/lazylibrarian-epub-metadata.md.
"""

import datetime
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

import epub_metadata
import epub_copies

EBOOK_ROOT = os.environ.get("EBOOK_ROOT", "/data/cephfs-hdd/data/media/books/EBooks")
STATE_DIR = os.environ.get("STATE_DIR", "/data/cephfs-hdd/data/media/books/.epub-convert")
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"
RUN_BUDGET_SECONDS = int(os.environ.get("RUN_BUDGET_SECONDS", "2400"))
SETTLE_SECONDS = int(os.environ.get("SETTLE_SECONDS", "900"))
CONVERT_TIMEOUT_SECONDS = int(os.environ.get("CONVERT_TIMEOUT_SECONDS", "600"))
# A run is killed at the Job's activeDeadlineSeconds (3300s); SIGTERM releases the lock, and a lock older than
# this is from a run that got SIGKILL.
LOCK_STALE_SECONDS = int(os.environ.get("LOCK_STALE_SECONDS", "3600"))
KAVITA_URL = os.environ.get("KAVITA_URL", "").rstrip("/")
KAVITA_API_KEY = os.environ.get("KAVITA_API_KEY", "")
EBOOK_CONVERT = os.environ.get("EBOOK_CONVERT", "ebook-convert")
EBOOK_META = os.environ.get("EBOOK_META", "ebook-meta")
STRIP_SERIES_METADATA = os.environ.get("STRIP_SERIES_METADATA", "") == "1"
STRIP_ONLY = os.environ.get("STRIP_ONLY", "") == "1"
SERIES_IDENTITIES = []
LIBRARY_HOLDS = None  # startup validates and freezes the configured holds; direct helpers validate on demand

SOURCES = (".azw3", ".mobi")  # preference order
BLOCKERS = (".epub", ".pdf")  # a folder holding either is never converted
HELD_FILE = "held.tsv"
LOCK_DIR = "lock"
PARTIAL_RE = re.compile(r"^\..+\.epub\.partial$")
STOPWORDS = {"the", "a", "an", "and", "of"}


def log(msg, **fields):
    line = {"ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "msg": msg}
    line.update(fields)
    print(json.dumps(line, ensure_ascii=False, separators=(",", ":")), flush=True)


# --- title check -------------------------------------------------------------


def _norm(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = text.replace("'", "")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _tokens(text):
    return set(_norm(text).split()) - STOPWORDS


def _main_title(text):
    """The title before a subtitle or decoration: "Carrier (1999)" -> "Carrier"."""
    return re.split(r":|\(|\[| - ", text or "", maxsplit=1)[0]


def title_matches(epub_title, book_title):
    """True when the EPUB's title names the book LazyLibrarian filed it under.

    LazyLibrarian's folder name is the book title with ':' written ' - ' and
    apostrophes dropped. A match is either main title contained (as words) in
    the other title, or at least half the words shared. "Carrier (1999)" matches
    "Carrier - A Guided Tour of an Aircraft Carrier"; "The Best American Science
    Fiction and Fantasy 2024" does not match "Sand".
    """
    epub, book = _tokens(epub_title), _tokens(book_title)
    if not epub or not book:
        return False
    epub_main, book_main = _tokens(_main_title(epub_title)), _tokens(_main_title(book_title))
    if epub_main and epub_main <= book:
        return True
    if book_main and book_main <= epub:
        return True
    return len(epub & book) / len(epub | book) >= 0.5


def author_matches(epub_authors, author_folder):
    """Informational only (anthologies credit an editor): the folder author's longest name word is in the EPUB's."""
    folder = _tokens(author_folder)
    if not folder:
        return False
    return max(folder, key=len) in _tokens(epub_authors)


# --- the library walk ----------------------------------------------------------


def find_candidates(root):
    """Folders that hold a .mobi/.azw3 and no .epub/.pdf: [(folder, [source names])]. Hidden entries are skipped."""
    found = []
    for folder, dirs, files in os.walk(root):
        if epub_metadata.held_library_folder(folder, root, LIBRARY_HOLDS):
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(folder, d)))
        exts = {os.path.splitext(f)[1].lower() for f in files if not f.startswith(".")}
        if exts & set(BLOCKERS):
            continue
        sources = sorted(f for f in files if not f.startswith(".") and os.path.splitext(f)[1].lower() in SOURCES)
        if sources:
            found.append((folder, sources))
    return found


def choose_source(folder, sources):
    """One source per folder: azw3 before mobi, then LazyLibrarian's own naming, then the newest, then by name."""
    title = os.path.basename(folder)
    author = os.path.basename(os.path.dirname(folder))
    ll_name = f"{title} - {author}"

    def rank(name):
        base, ext = os.path.splitext(name)
        try:
            mtime = os.stat(os.path.join(folder, name)).st_mtime
        except OSError:
            mtime = 0
        return (SOURCES.index(ext.lower()), 0 if base == ll_name else 1, -mtime, name)

    return sorted(sources, key=rank)[0]


def duplicate_of(folder):
    """A sibling folder that already holds this book as an epub or pdf, or None.

    The same book is sometimes filed twice under one author, in folders whose
    names differ only in punctuation ("Dirk Gently's ..." / "Dirk Gentlys ...",
    "The Taggerung" / "Taggerung"). Kavita already shows that book from the
    sibling; a second copy would only add a duplicate series, and Libretto
    refuses a member it holds twice as ambiguous (2026-10-06: "Dirk Gently's
    Holistic Detective Agency" went from held to missing). Same words, any
    punctuation, "the" / "a" / "an" / "and" / "of" ignored.
    """
    author_dir = os.path.dirname(folder)
    mine = _tokens(os.path.basename(folder))
    if not mine:
        return None
    try:
        siblings = sorted(os.listdir(author_dir))
    except OSError:
        return None
    for name in siblings:
        path = os.path.join(author_dir, name)
        if name.startswith(".") or path == folder or os.path.islink(path) or not os.path.isdir(path) or _tokens(name) != mine:
            continue
        try:
            files = os.listdir(path)
        except OSError:
            continue
        if any(not f.startswith(".") and os.path.splitext(f)[1].lower() in BLOCKERS for f in files):
            return os.path.relpath(path, EBOOK_ROOT)
    return None


def recently_changed(folder, sources, now):
    for name in sources:
        try:
            st = os.stat(os.path.join(folder, name))
        except OSError:
            return True
        if now - max(st.st_mtime, st.st_ctime) < SETTLE_SECONDS:
            return True
    return False


def clean_partials(root):
    """Remove hidden .<name>.epub.partial files a killed run left behind (only ever written by this job)."""
    removed = 0
    for folder, dirs, files in os.walk(root):
        if epub_metadata.held_library_folder(folder, root, LIBRARY_HOLDS):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(folder, d))]
        for name in files:
            if PARTIAL_RE.match(name):
                path = os.path.join(folder, name)
                if not DRY_RUN:
                    with epub_metadata.safe_directory(folder) as directory:
                        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                        owns_publication_pair = False
                        if stat.S_ISREG(info.st_mode) and info.st_nlink == 2:
                            # A SIGKILL between atomic link publication and partial unlink leaves this pair.
                            try:
                                published = os.stat(name[1:-len(".partial")], dir_fd=directory, follow_symlinks=False)
                                owns_publication_pair = (published.st_dev, published.st_ino) == (info.st_dev, info.st_ino)
                            except FileNotFoundError:
                                pass
                        if not stat.S_ISREG(info.st_mode) or (info.st_nlink != 1 and not owns_publication_pair):
                            log("epub_convert_partial_refused", path=os.path.relpath(path, root))
                            continue
                        os.unlink(name, dir_fd=directory)
                removed += 1
                log("epub_convert_partial_removed", path=os.path.relpath(path, root), dry_run=DRY_RUN)
    return removed


# --- held sources (failures, never retried until their line is deleted) -----------


def held_key(rel_folder, source, size):
    return f"{rel_folder}\t{source}\t{size}"


def load_held(state_dir):
    held = {}
    try:
        with epub_metadata.safe_directory(state_dir) as directory:
            contents, _info = epub_metadata.read_regular(directory, HELD_FILE, 16 * 1024 * 1024)
            for line in contents.decode("utf-8").splitlines():
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 4 and not line.startswith("#"):
                    held["\t".join(parts[:3])] = parts[3]
    except FileNotFoundError:
        pass
    return held


def record_held(state_dir, key, reason, detail):
    if DRY_RUN:
        return
    detail = re.sub(r"\s+", " ", detail or "")[:300]
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with epub_metadata.safe_directory(state_dir) as directory:
        fd = os.open(HELD_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o644, dir_fd=directory)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            info = os.fstat(fh.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise epub_metadata.Refused("held.tsv must be a regular file with one hardlink")
            if info.st_size == 0:
                fh.write("# folder\tsource\tsize\treason\twhen\tdetail  (delete a line to retry that book)\n")
            fh.write(f"{key}\t{reason}\t{stamp}\t{detail}\n")
            fh.flush()
            os.fsync(fh.fileno())


# --- calibre ------------------------------------------------------------------


class BookTimeout(Exception):
    """The book's CONVERT_TIMEOUT_SECONDS ran out (shared by every calibre call for one book)."""


def _remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise BookTimeout()
    return left


def _calibre(argv, deadline):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=_remaining(deadline))
    except subprocess.TimeoutExpired as err:
        raise BookTimeout() from err


def read_meta(path, deadline=None):
    """(title, authors) from ebook-meta, or (None, None) when it cannot read the file."""
    res = _calibre([EBOOK_META, path], deadline if deadline is not None else time.monotonic() + 300)
    if res.returncode != 0:
        return None, None
    fields = {}
    for line in res.stdout.splitlines():
        match = re.match(r"^([A-Za-z][A-Za-z ()]*?)\s*:\s(.*)$", line)
        if match:
            fields.setdefault(match.group(1).strip(), match.group(2).strip())
    return fields.get("Title") or None, fields.get("Author(s)") or None


def convert(source_path, out_path, deadline):
    """Run ebook-convert. Returns (reason, detail): reason None on success.

    A book with one huge HTML part fails EPUB output's file splitting
    ("SplitError: Could not find reasonable point at which to split"); it is
    retried once with splitting off (--flow-size 0), which readers handle.
    """
    argv = ["nice", "-n", "10", EBOOK_CONVERT, source_path, out_path]
    res = _calibre(argv, deadline)
    output = (res.stdout or "") + (res.stderr or "")
    if res.returncode != 0 and "SplitError" in output:
        res = _calibre([*argv, "--flow-size", "0"], deadline)
        output = (res.stdout or "") + (res.stderr or "")
    if res.returncode != 0 or not os.path.isfile(out_path):
        reason = "drm" if re.search(r"DRMError|locked by DRM|\bDRM\b", output) else "convert_error"
        lines = [line for line in output.strip().splitlines() if line.strip()]
        return reason, (lines[-1] if lines else f"exit {res.returncode}")
    return None, ""


def set_authors(path, authors, deadline):
    """Write the author into our own new EPUB (never the original). True on success."""
    return _calibre([EBOOK_META, path, "--authors", authors], deadline).returncode == 0


def _kavita(method, path, token=None, timeout=30):
    req = urllib.request.Request(f"{KAVITA_URL}{path}", data=b"" if method == "POST" else None, method=method)
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw) if raw.strip() else None


def kavita_scan():
    """Queue a (non-forced) scan of the Kavita library whose folder is EBOOK_ROOT.

    Not the scan-folder API: for an author folder that already holds exactly one
    Kavita series it rescans that series only and drops a new book's files. A
    library scan compares folder mtimes, which convert_folder touched.
    """
    if DRY_RUN:
        return "dry_run"
    if not (KAVITA_URL and KAVITA_API_KEY):
        return "not_configured"
    try:
        query = urllib.parse.urlencode({"apiKey": KAVITA_API_KEY, "pluginName": "lazylibrarian-epub-convert"})
        token = (_kavita("POST", f"/api/Plugin/authenticate?{query}") or {}).get("token")
        if not token:
            return "auth_failed"
        root = EBOOK_ROOT.rstrip("/")
        library = next(
            (
                lib
                for lib in (_kavita("GET", "/api/Library/libraries", token) or [])
                if any((f or "").rstrip("/") == root for f in lib.get("folders") or [])
            ),
            None,
        )
        if not library:
            return "no_library_for_root"
        _kavita("POST", f"/api/Library/scan?libraryId={int(library['id'])}&force=false", token)
        return "ok"
    except urllib.error.HTTPError as err:
        return f"http_{err.code}"
    except Exception as err:  # noqa: BLE001 - any failure is reported, never fatal: Kavita's own scan catches up
        return f"error_{type(err).__name__}"


# --- one book -------------------------------------------------------------------


def convert_folder(folder, source):
    """Convert one folder's chosen source. Returns the result string."""
    rel = os.path.relpath(folder, EBOOK_ROOT)
    held_folder = epub_metadata.held_library_folder(folder, EBOOK_ROOT, LIBRARY_HOLDS)
    if held_folder:
        log("epub_convert", result="configured_held", folder=rel, source=source,
            held_folder=held_folder, detail="configured library hold preserves every source file", dry_run=DRY_RUN)
        return "configured_held"
    source_path = os.path.join(folder, source)
    base = os.path.splitext(source)[0]
    dest = os.path.join(folder, base + ".epub")
    book_title = os.path.basename(folder)
    author_folder = os.path.basename(os.path.dirname(folder))
    # Never give calibre a link outside the library, or publish through a linked directory.
    with epub_metadata.safe_directory(folder) as directory:
        source_info = epub_metadata.stat_conversion_source(directory, source)
    size = source_info.st_size
    key = held_key(rel, source, size)
    started = time.monotonic()
    deadline = started + CONVERT_TIMEOUT_SECONDS
    fields = {"folder": rel, "source": source, "epub": os.path.basename(dest), "dry_run": DRY_RUN}
    partial = os.path.join(folder, f".{base}.epub.partial")

    work = tempfile.mkdtemp(prefix="epub-convert-", dir=os.environ.get("TMPDIR", "/tmp"))
    try:
        out = os.path.join(work, "book.epub")
        title = authors = None
        author_from_folder = False
        try:
            reason, detail = convert(source_path, out, deadline)
            if reason is None:
                title, authors = read_meta(out, deadline)
                if title and (not authors or authors.strip().lower() == "unknown"):
                    # The book carries no author (calibre reads "Unknown"): take LazyLibrarian's, its author folder.
                    if set_authors(out, author_folder, deadline):
                        title, authors = read_meta(out, deadline)
                        author_from_folder = True
        except BookTimeout:
            reason, detail = "timeout", f"calibre ran over {CONVERT_TIMEOUT_SECONDS}s for this book"
        if reason is None:
            if not title or not authors or authors.strip().lower() == "unknown":
                reason, detail = "unreadable", "ebook-meta read no title or no author from the EPUB"
            elif not title_matches(title, book_title):
                reason, detail = "title_mismatch", f"EPUB title {title!r} does not name the book {book_title!r}"
        fields.update(
            title=title,
            authors=authors,
            author_match=author_matches(authors, author_folder) if authors else False,
            author_from_folder=author_from_folder,
        )
        if reason is not None:
            record_held(STATE_DIR, key, reason, detail)
            log("epub_convert", result=reason, detail=detail, seconds=round(time.monotonic() - started, 1), **fields)
            return reason

        converted_identity = None
        if STRIP_SERIES_METADATA:
            try:
                fields["series_strip"] = epub_metadata.strip_converted(
                    out, os.path.relpath(dest, EBOOK_ROOT), EBOOK_ROOT, STATE_DIR, DRY_RUN, SERIES_IDENTITIES
                )
                with epub_metadata.safe_directory(work) as converted_directory:
                    output, _output_info = epub_metadata.read_regular(converted_directory, "book.epub")
                converted_identity = epub_metadata.grouping_identity(output, os.path.relpath(dest, EBOOK_ROOT))
            except epub_metadata.Changed:
                raise
            except ValueError as err:
                reason, detail = "unreadable", f"series metadata validation refused: {err}"
                record_held(STATE_DIR, key, reason, detail)
                log("epub_convert", result=reason, detail=detail, **fields)
                return reason

        # Re-check right before writing: LazyLibrarian may have imported an epub or pdf meanwhile.
        if any(os.path.splitext(f)[1].lower() in BLOCKERS for f in os.listdir(folder)):
            log("epub_convert", result="skipped_now_has_epub_or_pdf", seconds=round(time.monotonic() - started, 1), **fields)
            return "skipped_now_has_epub_or_pdf"
        if not DRY_RUN:
            with epub_metadata.safe_directory(folder) as directory:
                current_info = epub_metadata.stat_conversion_source(directory, source)
                if epub_metadata._identity(current_info) != epub_metadata._identity(source_info):
                    raise epub_metadata.Changed("conversion source changed before publish")
                with epub_metadata.safe_directory(work) as converted_directory:
                    epub_metadata.copy_conversion_output(converted_directory, "book.epub", directory, os.path.basename(partial))
                current_info = epub_metadata.stat_conversion_source(directory, source)
                if epub_metadata._identity(current_info) != epub_metadata._identity(source_info):
                    raise epub_metadata.Changed("conversion source changed during publish preparation")
                if any(os.path.splitext(name)[1].lower() in BLOCKERS for name in os.listdir(directory)):
                    os.unlink(os.path.basename(partial), dir_fd=directory)
                    log("epub_convert", result="skipped_now_has_epub_or_pdf", seconds=round(time.monotonic() - started, 1), **fields)
                    return "skipped_now_has_epub_or_pdf"
                epub_metadata._same_directory(directory, folder)
                # Link publication is atomic and refuses an existing name, including an import racing us.
                try:
                    os.link(os.path.basename(partial), os.path.basename(dest),
                            src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                except FileExistsError:
                    log("epub_convert", result="skipped_now_has_epub_or_pdf", **fields)
                    return "skipped_now_has_epub_or_pdf"
                os.unlink(os.path.basename(partial), dir_fd=directory)
                os.fsync(directory)
                os.utime(directory)
            with epub_metadata.safe_directory(os.path.dirname(folder)) as author_directory:
                os.utime(author_directory)
            if STRIP_SERIES_METADATA:
                SERIES_IDENTITIES.append(converted_identity)
        log(
            "epub_convert",
            result="converted",
            bytes=os.path.getsize(out),
            seconds=round(time.monotonic() - started, 1),
            **fields,
        )
        return "converted"
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if not DRY_RUN:
            try:
                with epub_metadata.safe_directory(folder) as directory:
                    info = os.stat(os.path.basename(partial), dir_fd=directory, follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode):
                        os.unlink(os.path.basename(partial), dir_fd=directory)
            except FileNotFoundError:
                pass
            except OSError as err:
                log("epub_convert_partial_cleanup_refused", path=os.path.relpath(partial, EBOOK_ROOT), detail=str(err))


# --- the run --------------------------------------------------------------------


def census(held, now):
    """Count the folders still .mobi/.azw3-only, split by why."""
    counts = {"unconverted": 0, "held": 0, "settling": 0, "duplicate": 0}
    unconverted = []
    duplicates = []
    for folder, sources in find_candidates(EBOOK_ROOT):
        source = choose_source(folder, sources)
        rel = os.path.relpath(folder, EBOOK_ROOT)
        try:
            size = os.stat(os.path.join(folder, source)).st_size
        except OSError:
            continue
        if held_key(rel, source, size) in held:
            counts["held"] += 1
        elif duplicate_of(folder):
            counts["duplicate"] += 1
            duplicates.append(rel)
        elif recently_changed(folder, sources, now):
            counts["settling"] += 1
        else:
            counts["unconverted"] += 1
            unconverted.append(rel)
    return counts, unconverted[:10], duplicates[:10]


def take_lock():
    path = os.path.join(STATE_DIR, LOCK_DIR)
    with epub_metadata.safe_directory(STATE_DIR, create=True) as directory:
        try:
            os.mkdir(LOCK_DIR, dir_fd=directory)
            return path
        except FileExistsError:
            with epub_metadata.safe_directory(path) as lock_directory:
                age = time.time() - os.fstat(lock_directory).st_mtime
                if age < LOCK_STALE_SECONDS:
                    log("epub_convert_locked", lock_age_seconds=int(age))
                    return None
                log("epub_convert_lock_taken_over", lock_age_seconds=int(age))
                os.utime(lock_directory)
                return path


def strip_folders():
    raw = os.environ.get("STRIP_FOLDERS_JSON")
    if raw is None:
        return [EBOOK_ROOT]
    targets = json.loads(raw)
    if not isinstance(targets, list) or not targets or any(not isinstance(t, str) for t in targets):
        raise epub_metadata.Refused("STRIP_FOLDERS_JSON must be a nonempty JSON array of relative folders")
    folders = []
    for target in sorted(set(targets)):
        if not target or os.path.isabs(target) or any(p in ("", ".", "..") or p.startswith(".") for p in target.split("/")):
            raise epub_metadata.Refused("strip targets must be exact, visible relative folders")
        folder = os.path.join(EBOOK_ROOT, target)
        with epub_metadata.safe_directory(folder):
            pass
        folders.append(folder)
    if any(a != b and os.path.commonpath((a, b)) == a for a in folders for b in folders):
        raise epub_metadata.Refused("strip target folders must not overlap")
    return folders


def strip_series_pass(folders, run_started):
    global SERIES_IDENTITIES
    counts = {"stripped": 0, "would_strip": 0, "untagged": 0, "grouped": 0, "settling": 0,
              "refused": 0, "deferred": 0, "collision_held": 0, "configured_held": 0}
    SERIES_IDENTITIES, errors = epub_metadata.identity_preflight(EBOOK_ROOT, run_started + RUN_BUDGET_SECONDS)
    for error in errors:
        log("epub_series_preflight", result="refused", **error, dry_run=DRY_RUN)
    if errors:
        counts.update(refused=len(errors), preflight_failed=True)
        log("epub_series_strip_census", **counts, dry_run=DRY_RUN)
        return counts
    identities = {identity["path"]: identity for identity in SERIES_IDENTITIES}
    log("epub_series_preflight", result="ok", epub_count=len(SERIES_IDENTITIES), dry_run=DRY_RUN)
    for root in folders:
        for folder, dirs, files in os.walk(root):
            for name in sorted(dirs):
                path = os.path.join(folder, name)
                if not name.startswith(".") and os.path.islink(path):
                    counts["refused"] += 1
                    log("epub_series_strip", result="refused", path=os.path.relpath(path, EBOOK_ROOT),
                        detail="symlinked directory is excluded", dry_run=DRY_RUN)
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(folder, d)))
            for name in sorted(files):
                if name.startswith(".") or os.path.splitext(name)[1].lower() != ".epub":
                    continue
                path = os.path.join(folder, name)
                held_folder = epub_metadata.held_library_folder(folder, EBOOK_ROOT, LIBRARY_HOLDS)
                if held_folder:
                    counts["configured_held"] += 1
                    log("epub_series_strip", result="configured_held", path=os.path.relpath(path, EBOOK_ROOT),
                        held_folder=held_folder, detail="configured library hold preserves grouping metadata",
                        dry_run=DRY_RUN)
                    continue
                if time.monotonic() - run_started > RUN_BUDGET_SECONDS:
                    counts["deferred"] += 1
                    continue
                try:
                    relative = os.path.relpath(path, EBOOK_ROOT)
                    identity = identities.get(relative)
                    if identity is None:
                        raise epub_metadata.Refused("EPUB appeared after grouping preflight")
                    grouping = epub_metadata.dedicated_grouping(identity, SERIES_IDENTITIES)
                    if not identity["has_series_metadata"] and grouping is None:
                        counts["untagged"] += 1
                        continue
                    if identity.get("strip_refusal"):
                        raise epub_metadata.Refused(identity["strip_refusal"])
                    projected = dict(identity)
                    if grouping:
                        projected["projected_aliases"] = {epub_metadata.kavita_normalized(grouping)}
                    conflicts = epub_metadata.collision_conflicts(projected, SERIES_IDENTITIES)
                    if conflicts:
                        result = {"result": "collision_held", "path": relative, "conflicts": conflicts,
                                  "metadata": identity["metadata"],
                                  "detail": "stripping would create a cross-author Kavita grouping collision"}
                    else:
                        result = epub_metadata.strip_existing(path, EBOOK_ROOT, STATE_DIR, SETTLE_SECONDS,
                                                             DRY_RUN, expected_source_identity=identity["source_identity"],
                                                             grouping=grouping)
                        if result["result"] == "stripped":
                            identity["current_aliases"] = projected["projected_aliases"]
                            identity["comparison_aliases"] = projected["projected_aliases"]
                except Exception as err:  # one refused file never changes any other file's eligibility
                    result = {"result": "refused", "path": os.path.relpath(path, EBOOK_ROOT),
                              "detail": f"{type(err).__name__}: {err}"[:500]}
                counts[result["result"]] += 1
                if result["result"] != "untagged":
                    log("epub_series_strip", **result, dry_run=DRY_RUN)
    log("epub_series_strip_census", **counts, dry_run=DRY_RUN)
    return counts


def _terminate(signum, _frame):
    # activeDeadlineSeconds or a pod delete: unwind through main()'s finally, which releases the lock
    # (subprocess.run kills the running ebook-convert on the way out).
    raise SystemExit(128 + signum)


def main():
    global LIBRARY_HOLDS
    signal.signal(signal.SIGTERM, _terminate)
    run_started = time.monotonic()
    restore = None
    copies = None
    if len(sys.argv) > 1:
        if len(sys.argv) != 3 or sys.argv[1] not in ("--restore-backup", "--consolidate-copies"):
            log("epub_convert_run_failed", error="usage: epub_convert.py [--restore-backup <manifest> | --consolidate-copies <snapshot>]")
            return 1
        if sys.argv[1] == "--restore-backup":
            restore = sys.argv[2]
        else:
            copies = sys.argv[2]
    try:
        epub_metadata.validate_paths(EBOOK_ROOT, STATE_DIR)
        LIBRARY_HOLDS = epub_metadata.library_hold_folders(EBOOK_ROOT)
        if copies and (STRIP_SERIES_METADATA or STRIP_ONLY or "STRIP_FOLDERS_JSON" in os.environ):
            raise epub_metadata.Refused("copy consolidation requires stripping/conversion modes off")
        if STRIP_ONLY and not STRIP_SERIES_METADATA and not restore:
            raise epub_metadata.Refused("STRIP_ONLY requires STRIP_SERIES_METADATA=1")
        folders = strip_folders() if STRIP_SERIES_METADATA and not restore else []
        if "STRIP_FOLDERS_JSON" in os.environ and not STRIP_ONLY and not restore:
            raise epub_metadata.Refused("targeted stripping requires STRIP_ONLY=1 to isolate the stage")
    except (ValueError, OSError) as err:
        log("epub_convert_run_failed", error=str(err))
        return 1
    lock = None if DRY_RUN else take_lock()
    if not DRY_RUN and lock is None:
        return 0
    try:
        if copies:
            try:
                counts = epub_copies.consolidate(copies, EBOOK_ROOT, STATE_DIR, SETTLE_SECONDS,
                                                LIBRARY_HOLDS, log, DRY_RUN,
                                                run_started + RUN_BUDGET_SECONDS)
            except Exception as err:
                log("epub_copy_consolidate", result="refused", detail=f"{type(err).__name__}: {err}"[:500], dry_run=DRY_RUN)
                return 1
            if counts["moved"]:
                log("epub_copy_consolidate_scan", kavita_scan=kavita_scan())
            return 1 if counts["refused"] else 0
        if restore:
            try:
                result = epub_metadata.restore_backup(restore, EBOOK_ROOT, STATE_DIR, DRY_RUN)
            except Exception as err:
                log("epub_series_restore", result="refused", detail=f"{type(err).__name__}: {err}"[:500], dry_run=DRY_RUN)
                return 1
            log("epub_series_restore", **result, dry_run=DRY_RUN,
                kavita_scan=kavita_scan() if result["result"] == "restored" else "dry_run")
            return 0
        if not DRY_RUN and not STRIP_ONLY:
            clean_partials(EBOOK_ROOT)
        series = strip_series_pass(folders, run_started) if STRIP_SERIES_METADATA else {"stripped": 0, "refused": 0, "deferred": 0}
        held = load_held(STATE_DIR)
        now = time.time()
        results = {}
        deferred = 0
        for folder, sources in ([] if STRIP_ONLY or series.get("preflight_failed") else find_candidates(EBOOK_ROOT)):
            source = choose_source(folder, sources)
            rel = os.path.relpath(folder, EBOOK_ROOT)
            try:
                size = os.stat(os.path.join(folder, source)).st_size
            except OSError:
                continue  # moved or removed since the walk (an import); the next run sees the folder as it is
            if held_key(rel, source, size) in held or recently_changed(folder, sources, now):
                continue
            if duplicate_of(folder):
                continue  # counted by the census as `duplicate`; converts by itself if the sibling copy goes
            if time.monotonic() - run_started > RUN_BUDGET_SECONDS:
                deferred += 1
                continue
            try:
                result = convert_folder(folder, source)
            except epub_metadata.Changed as err:
                result = "io_error"
                log("epub_convert", result=result, folder=rel, source=source,
                    detail=f"changed input, retry after settling: {err}"[:300])
            except epub_metadata.Refused as err:
                # A stable safety/format constraint needs an operator decision, not hourly calibre work.
                result = "unreadable"
                detail = f"conversion safety refused: {err}"[:300]
                record_held(STATE_DIR, held_key(rel, source, size), result, detail)
                log("epub_convert", result=result, folder=rel, source=source, detail=detail)
            except Exception as err:  # noqa: BLE001 - one folder's I/O failure never stops the others
                # Not recorded as held: an NFS hiccup or an import race clears by itself, and the next run retries.
                # A failure that repeats logs every hour and keeps LazyLibrarianEpubConvertHeld up.
                result = "io_error"
                log("epub_convert", result=result, folder=rel, source=source, detail=f"{type(err).__name__}: {err}"[:300])
            results[result] = results.get(result, 0) + 1
        kavita = kavita_scan() if results.get("converted") or series["stripped"] else "nothing_converted"
        counts, sample, duplicate_sample = census(load_held(STATE_DIR), time.time())
        log(
            "epub_convert_census",
            unconverted=counts["unconverted"],
            held=counts["held"],
            settling=counts["settling"],
            duplicate=counts["duplicate"],
            deferred=deferred,
            converted=results.get("converted", 0),
            series_strip=series,
            failed=sum(v for k, v in results.items() if k not in ("converted", "skipped_now_has_epub_or_pdf", "configured_held")),
            configured_hold_folders=sorted(LIBRARY_HOLDS),
            results=results,
            kavita_scan=kavita,
            unconverted_sample=sample,
            duplicate_sample=duplicate_sample,
            dry_run=DRY_RUN,
            seconds=round(time.monotonic() - run_started, 1),
        )
        return 1 if series["refused"] or series["deferred"] else 0
    finally:
        if lock:
            shutil.rmtree(lock, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
