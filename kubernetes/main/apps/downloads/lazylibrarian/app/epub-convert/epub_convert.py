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
  2. Walks EBOOK_ROOT. A folder that holds any .epub or .pdf is never touched.
     A folder that holds a .mobi or .azw3 and neither of those is a candidate.
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
  5. Touches the book folder and the author folder (on this NFS share adding a
     file does not reliably bump the folder mtime Kavita's scan compares), then,
     when anything was converted and a key is set, queues one Kavita library scan.
  6. A source that fails (DRM, a conversion error, an unreadable or mismatched
     EPUB) is left exactly as it is and recorded in STATE_DIR/held.tsv, so it is
     reported once and not retried every hour. Delete its line to retry it.
  7. Logs a census: the folders still .mobi/.azw3-only that this run should have
     converted (`unconverted`, 0 after every run), plus the held and settling ones.

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
"""

import datetime
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

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

SOURCES = (".azw3", ".mobi")  # preference order
BLOCKERS = (".epub", ".pdf")  # a folder holding either is never touched
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
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
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
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if PARTIAL_RE.match(name):
                path = os.path.join(folder, name)
                if not DRY_RUN:
                    os.remove(path)
                removed += 1
                log("epub_convert_partial_removed", path=os.path.relpath(path, root), dry_run=DRY_RUN)
    return removed


# --- held sources (failures, never retried until their line is deleted) -----------


def held_key(rel_folder, source, size):
    return f"{rel_folder}\t{source}\t{size}"


def load_held(state_dir):
    held = {}
    path = os.path.join(state_dir, HELD_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 4 and not line.startswith("#"):
                    held["\t".join(parts[:3])] = parts[3]
    except FileNotFoundError:
        pass
    return held


def record_held(state_dir, key, reason, detail):
    if DRY_RUN:
        return
    path = os.path.join(state_dir, HELD_FILE)
    new = not os.path.exists(path)
    detail = re.sub(r"\s+", " ", detail or "")[:300]
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, "a", encoding="utf-8") as fh:
        if new:
            fh.write("# folder\tsource\tsize\treason\twhen\tdetail  (delete a line to retry that book)\n")
        fh.write(f"{key}\t{reason}\t{stamp}\t{detail}\n")


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
    source_path = os.path.join(folder, source)
    base = os.path.splitext(source)[0]
    dest = os.path.join(folder, base + ".epub")
    book_title = os.path.basename(folder)
    author_folder = os.path.basename(os.path.dirname(folder))
    size = os.stat(source_path).st_size
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

        # Re-check right before writing: LazyLibrarian may have imported an epub or pdf meanwhile.
        if any(os.path.splitext(f)[1].lower() in BLOCKERS for f in os.listdir(folder)):
            log("epub_convert", result="skipped_now_has_epub_or_pdf", seconds=round(time.monotonic() - started, 1), **fields)
            return "skipped_now_has_epub_or_pdf"
        if not DRY_RUN:
            shutil.copyfile(out, partial)
            os.chmod(partial, 0o644)
            if os.path.exists(dest):
                os.remove(partial)
                log("epub_convert", result="skipped_now_has_epub_or_pdf", seconds=round(time.monotonic() - started, 1), **fields)
                return "skipped_now_has_epub_or_pdf"
            os.rename(partial, dest)
            if os.path.getsize(dest) != os.path.getsize(out):
                raise OSError(f"size mismatch after copy: {dest}")
            os.utime(folder)
            os.utime(os.path.dirname(folder))
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
        if not DRY_RUN and os.path.exists(partial):
            os.remove(partial)


# --- the run --------------------------------------------------------------------


def census(held, now):
    """Count the folders still .mobi/.azw3-only, split by why."""
    counts = {"unconverted": 0, "held": 0, "settling": 0}
    unconverted = []
    for folder, sources in find_candidates(EBOOK_ROOT):
        source = choose_source(folder, sources)
        rel = os.path.relpath(folder, EBOOK_ROOT)
        try:
            size = os.stat(os.path.join(folder, source)).st_size
        except OSError:
            continue
        if held_key(rel, source, size) in held:
            counts["held"] += 1
        elif recently_changed(folder, sources, now):
            counts["settling"] += 1
        else:
            counts["unconverted"] += 1
            unconverted.append(rel)
    return counts, unconverted[:10]


def take_lock():
    path = os.path.join(STATE_DIR, LOCK_DIR)
    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        os.mkdir(path)
        return path
    except FileExistsError:
        age = time.time() - os.stat(path).st_mtime
        if age < LOCK_STALE_SECONDS:
            log("epub_convert_locked", lock_age_seconds=int(age))
            return None
        log("epub_convert_lock_taken_over", lock_age_seconds=int(age))
        os.utime(path)
        return path


def _terminate(signum, _frame):
    # activeDeadlineSeconds or a pod delete: unwind through main()'s finally, which releases the lock
    # (subprocess.run kills the running ebook-convert on the way out).
    raise SystemExit(128 + signum)


def main():
    signal.signal(signal.SIGTERM, _terminate)
    run_started = time.monotonic()
    if not os.path.isdir(EBOOK_ROOT):
        log("epub_convert_run_failed", error=f"EBOOK_ROOT {EBOOK_ROOT} is not a directory")
        return 1
    lock = None if DRY_RUN else take_lock()
    if not DRY_RUN and lock is None:
        return 0
    try:
        if not DRY_RUN:
            clean_partials(EBOOK_ROOT)
        held = load_held(STATE_DIR)
        now = time.time()
        results = {}
        deferred = 0
        for folder, sources in find_candidates(EBOOK_ROOT):
            source = choose_source(folder, sources)
            rel = os.path.relpath(folder, EBOOK_ROOT)
            try:
                size = os.stat(os.path.join(folder, source)).st_size
            except OSError:
                continue  # moved or removed since the walk (an import); the next run sees the folder as it is
            if held_key(rel, source, size) in held or recently_changed(folder, sources, now):
                continue
            if time.monotonic() - run_started > RUN_BUDGET_SECONDS:
                deferred += 1
                continue
            try:
                result = convert_folder(folder, source)
            except Exception as err:  # noqa: BLE001 - one folder's I/O failure never stops the others
                # Not recorded as held: an NFS hiccup or an import race clears by itself, and the next run retries.
                # A failure that repeats logs every hour and keeps LazyLibrarianEpubConvertHeld up.
                result = "io_error"
                log("epub_convert", result=result, folder=rel, source=source, detail=f"{type(err).__name__}: {err}"[:300])
            results[result] = results.get(result, 0) + 1
        kavita = kavita_scan() if results.get("converted") else "nothing_converted"
        counts, sample = census(load_held(STATE_DIR), time.time())
        log(
            "epub_convert_census",
            unconverted=counts["unconverted"],
            held=counts["held"],
            settling=counts["settling"],
            deferred=deferred,
            converted=results.get("converted", 0),
            failed=sum(v for k, v in results.items() if k not in ("converted", "skipped_now_has_epub_or_pdf")),
            results=results,
            kavita_scan=kavita,
            unconverted_sample=sample,
            dry_run=DRY_RUN,
            seconds=round(time.monotonic() - run_started, 1),
        )
        return 0
    finally:
        if lock:
            shutil.rmtree(lock, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
