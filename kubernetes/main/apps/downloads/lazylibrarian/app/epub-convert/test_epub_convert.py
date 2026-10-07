#!/usr/bin/env python3
"""Tests for epub_convert.py, run INSIDE the pinned calibre image (real ebook-convert / ebook-meta).

    python3 test_epub_convert.py        # exit 0 = all passed

CI: .github/workflows/lazylibrarian-epub-convert.yaml runs this in the image named in
../epub-convert-cronjob.yaml, offline. It builds a throwaway library under a temp dir
(fixtures made with ebook-convert from text), runs the converter as the CronJob does,
and checks what it wrote and logged. Nothing outside the temp dir is touched.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "epub_convert.py")
sys.path.insert(0, HERE)
import epub_convert  # noqa: E402
import epub_metadata  # noqa: E402

FAILURES = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        FAILURES.append(what)


def make_book(path, title, author, html=None):
    """A real .mobi / .azw3 made by calibre from a short text (or the given HTML)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        txt = os.path.join(tmp, "book.html" if html else "book.txt")
        with open(txt, "w", encoding="utf-8") as fh:
            fh.write(html or f"{title}\n\nChapter One\n\nIt was a fine day for a test.\n" * 5)
        subprocess.run(
            ["ebook-convert", txt, path, "--title", title, "--authors", author],
            check=True,
            capture_output=True,
        )


def backdate(root):
    """Move every mtime an hour back (ctime cannot be set; SETTLE_SECONDS covers it in the runs below)."""
    old = time.time() - 3600
    for folder, dirs, files in os.walk(root):
        for name in files + dirs:
            os.utime(os.path.join(folder, name), (old, old))


def run(env_extra):
    env = dict(os.environ, STRIP_SERIES_METADATA="0", STRIP_ONLY="0", KAVITA_URL="", KAVITA_API_KEY="")
    env.pop("STRIP_FOLDERS_JSON", None)
    env.update(env_extra)
    res = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, env=env, timeout=900)
    lines = []
    for line in res.stdout.splitlines():
        try:
            lines.append(json.loads(line))
        except ValueError:
            pass
    return res.returncode, lines, res.stderr


def by_msg(lines, msg):
    return [line for line in lines if line.get("msg") == msg]


def digest(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def snapshot(folder):
    return {name: os.stat(os.path.join(folder, name)).st_mtime_ns for name in sorted(os.listdir(folder))}


def test_title_rule():
    # Shapes seen in the library on 2026-10-06 (folder name = LazyLibrarian's $Title).
    tm = epub_convert.title_matches
    check(tm("Eragon", "Eragon"), "title: same title")
    check(tm("Carrier (1999)", "Carrier - A Guided Tour of an Aircraft Carrier"), "title: year decoration")
    check(tm("Notre-Dame: A Short History of the Meaning of Cathedrals",
             "Notre-Dame - A Short History of the Meaning of Cathedrals"), "title: subtitle written ' - '")
    check(tm("Dean Koontz's Frankenstein", "Dean Koontzs Frankenstein"), "title: apostrophe dropped in the folder")
    check(tm("The Expanse Origins #1 (of 4)", "The Expanse Origins - James Holden"), "title: series issue")
    check(not tm("The Best American Science Fiction and Fantasy 2024", "Sand"), "title: another book (Sand)")
    check(not tm("Beowulf: A Translation and Commentary, together with Sellic Spell",
                 "Tree and Leaf - Including Mythopoeia and The Homecoming of Beorhtnoth, Beorhthelm's Son"),
          "title: another book (Tree and Leaf)")
    check(not tm("Bobiverse 2: For We Are Many", "Potomu chto nas mnogo"), "title: other-language folder title")
    check(not tm("", "Eragon") and not tm("Eragon", ""), "title: empty never matches")


def test_runs():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "EBooks")
        state = os.path.join(tmp, ".epub-convert")
        good = os.path.join(root, "Author A", "Good Book")
        make_book(os.path.join(good, "Good Book - Author A.mobi"), "Good Book", "Author A")
        with open(os.path.join(good, "Good Book - Author A.opf"), "w") as fh:
            fh.write("<package/>")
        both = os.path.join(root, "Author A", "Two Formats")
        make_book(os.path.join(both, "Two Formats - Author A.mobi"), "Two Formats", "Author A")
        make_book(os.path.join(both, "Two Formats - Author A.azw3"), "Two Formats", "Author A")
        has_epub = os.path.join(root, "Author A", "Has Epub")
        make_book(os.path.join(has_epub, "Has Epub - Author A.mobi"), "Has Epub", "Author A")
        make_book(os.path.join(has_epub, "Has Epub - Author A.epub"), "Has Epub", "Author A")
        has_pdf = os.path.join(root, "Author A", "Has Pdf")
        make_book(os.path.join(has_pdf, "Has Pdf - Author A.azw3"), "Has Pdf", "Author A")
        with open(os.path.join(has_pdf, "Has Pdf - Author A.pdf"), "wb") as fh:
            fh.write(b"%PDF-1.4\n")
        wrong = os.path.join(root, "Author B", "Sand")
        make_book(os.path.join(wrong, "Sand - Author B.azw3"),
                  "The Best American Science Fiction and Fantasy 2024", "Author B")
        broken = os.path.join(root, "Author B", "Broken")
        os.makedirs(broken)
        with open(os.path.join(broken, "Broken - Author B.mobi"), "wb") as fh:
            fh.write(b"BOOKMOBI" + b"\0" * 4096)
        no_author = os.path.join(root, "Author D", "No Author")
        make_book(os.path.join(no_author, "No Author - Author D.mobi"), "No Author", "Unknown")
        # One paragraph far over EPUB output's split size (the Tree and Leaf shape): converts, splitting off if need be.
        big = os.path.join(root, "Author D", "Big Part")
        make_book(os.path.join(big, "Big Part - Author D.mobi"), "Big Part", "Author D",
                  html="<html><body><h1>Big Part</h1><p>" + "word " * 80000 + "</p></body></html>")
        no_author_digest = digest(os.path.join(no_author, "No Author - Author D.mobi"))
        backdate(root)
        untouched_before = {d: snapshot(d) for d in (has_epub, has_pdf)}

        # Dry run: converts in /tmp only, writes nothing to the library or the state dir.
        rc, lines, err = run({"EBOOK_ROOT": root, "STATE_DIR": state, "DRY_RUN": "1", "SETTLE_SECONDS": "0"})
        check(rc == 0, f"dry run exits 0 ({err[-300:]})")
        check(not os.path.exists(state), "dry run: no state dir")
        check(not any(f.endswith(".epub") for f in os.listdir(good)), "dry run: no epub written")
        census = by_msg(lines, "epub_convert_census")
        check(census and census[0]["converted"] == 4 and census[0]["dry_run"] is True, "dry run: census says 4 would convert")

        # First real run.
        rc, lines, err = run({"EBOOK_ROOT": root, "STATE_DIR": state, "SETTLE_SECONDS": "0"})
        check(rc == 0, f"run 1 exits 0 ({err[-300:]})")
        results = {line["folder"]: line for line in by_msg(lines, "epub_convert")}
        epub = os.path.join(good, "Good Book - Author A.epub")
        check(os.path.isfile(epub), "run 1: epub written beside the mobi, under the mobi's basename")
        check(os.path.isfile(os.path.join(good, "Good Book - Author A.mobi")), "run 1: original mobi kept")
        check(oct(os.stat(epub).st_mode & 0o777) == "0o644", "run 1: epub is 0644")
        title, authors = epub_convert.read_meta(epub)
        check(title == "Good Book" and authors and "Author A" in authors, f"run 1: ebook-meta reads the epub ({title}, {authors})")
        check(results.get("Author A/Good Book", {}).get("result") == "converted", "run 1: logs converted")
        check(results.get("Author A/Two Formats", {}).get("source") == "Two Formats - Author A.azw3",
              "run 1: azw3 preferred over mobi")
        check(sorted(f for f in os.listdir(both) if f.endswith(".epub")) == ["Two Formats - Author A.epub"],
              "run 1: one epub for a folder with two sources")
        check(results.get("Author B/Sand", {}).get("result") == "title_mismatch", "run 1: another book is held (title_mismatch)")
        check(not any(f.endswith(".epub") for f in os.listdir(wrong)), "run 1: no epub for the mismatched book")
        check(results.get("Author B/Broken", {}).get("result") in ("convert_error", "drm"), "run 1: a broken mobi is held")
        check(sorted(os.listdir(broken)) == ["Broken - Author B.mobi"], "run 1: the broken book's folder is unchanged")
        check({d: snapshot(d) for d in (has_epub, has_pdf)} == untouched_before,
              "run 1: folders with an epub or a pdf untouched")
        check(not any(n.endswith(".partial") for _, _, fs in os.walk(root) for n in fs), "run 1: no partial left")
        check(os.path.isfile(os.path.join(state, "held.tsv")), "run 1: held.tsv written")
        check(not os.path.exists(os.path.join(state, "lock")), "run 1: lock released")
        census = by_msg(lines, "epub_convert_census")
        check(census and census[0]["unconverted"] == 0 and census[0]["held"] == 2 and census[0]["converted"] == 4,
              f"run 1: census unconverted 0, held 2, converted 4 ({census})")
        title, authors = epub_convert.read_meta(os.path.join(no_author, "No Author - Author D.epub"))
        check((authors or "").startswith("Author D") and results["Author D/No Author"]["author_from_folder"] is True,
              f"run 1: a book with no author gets the folder's author in the EPUB ({authors})")
        check(digest(os.path.join(no_author, "No Author - Author D.mobi")) == no_author_digest,
              "run 1: the original is byte-for-byte unchanged")
        check(results.get("Author D/Big Part", {}).get("result") == "converted", "run 1: a book with one huge part converts")

        # Second run: idempotent.
        before = {d: snapshot(d) for d in (good, both, wrong, broken, no_author, big)}
        rc, lines, err = run({"EBOOK_ROOT": root, "STATE_DIR": state, "SETTLE_SECONDS": "0"})
        check(rc == 0, "run 2 exits 0")
        check(not by_msg(lines, "epub_convert"), "run 2: nothing attempted")
        check({d: snapshot(d) for d in (good, both, wrong, broken, no_author, big)} == before,
              "run 2: nothing changed on disk")
        census = by_msg(lines, "epub_convert_census")
        check(census and census[0]["unconverted"] == 0 and census[0]["held"] == 2, "run 2: census unchanged")

        # A new import is left to settle, and counted as settling, not unconverted.
        fresh = os.path.join(root, "Author C", "Fresh")
        make_book(os.path.join(fresh, "Fresh - Author C.mobi"), "Fresh", "Author C")
        rc, lines, err = run({"EBOOK_ROOT": root, "STATE_DIR": state, "SETTLE_SECONDS": "3600"})
        census = by_msg(lines, "epub_convert_census")
        check(not os.path.exists(os.path.join(fresh, "Fresh - Author C.epub")), "run 3: a fresh import waits")
        check(census and census[0]["settling"] == 1 and census[0]["unconverted"] == 0, "run 3: counted as settling")

        # The time budget defers the rest to the next run, and the census says so.
        rc, lines, err = run({"EBOOK_ROOT": root, "STATE_DIR": state, "SETTLE_SECONDS": "0", "RUN_BUDGET_SECONDS": "-1"})
        census = by_msg(lines, "epub_convert_census")
        check(census and census[0]["deferred"] == 1 and census[0]["unconverted"] == 1, "run 4: budget spent, deferred")

        # Another run holds the lock: nothing happens.
        os.makedirs(os.path.join(state, "lock"))
        rc, lines, err = run({"EBOOK_ROOT": root, "STATE_DIR": state, "SETTLE_SECONDS": "0"})
        check(rc == 0 and by_msg(lines, "epub_convert_locked") and not by_msg(lines, "epub_convert"),
              "run 5: a held lock stops the run")
        check(not os.path.exists(os.path.join(fresh, "Fresh - Author C.epub")), "run 5: nothing converted under the lock")
        os.rmdir(os.path.join(state, "lock"))

        # A partial from a killed run is cleaned, then the book converts.
        with open(os.path.join(fresh, ".Fresh - Author C.epub.partial"), "wb") as fh:
            fh.write(b"half")
        rc, lines, err = run({"EBOOK_ROOT": root, "STATE_DIR": state, "SETTLE_SECONDS": "0"})
        check(by_msg(lines, "epub_convert_partial_removed"), "run 6: stale partial removed")
        check(os.path.isfile(os.path.join(fresh, "Fresh - Author C.epub")), "run 6: the settled book converts")

        # Deleting a held line retries that book.
        with open(os.path.join(state, "held.tsv")) as fh:
            kept = [line for line in fh if "Broken" not in line]
        with open(os.path.join(state, "held.tsv"), "w") as fh:
            fh.writelines(kept)
        rc, lines, err = run({"EBOOK_ROOT": root, "STATE_DIR": state, "SETTLE_SECONDS": "0"})
        check([line["folder"] for line in by_msg(lines, "epub_convert")] == ["Author B/Broken"],
              "run 7: a deleted held line is retried, nothing else")


def test_failures():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "EBooks")
        state = os.path.join(tmp, ".epub-convert")

        # A folder the job cannot write to fails on its own; the folder after it still converts.
        locked = os.path.join(root, "Author E", "Read Only")
        make_book(os.path.join(locked, "Read Only - Author E.mobi"), "Read Only", "Author E")
        after = os.path.join(root, "Author E", "Zed Book")
        make_book(os.path.join(after, "Zed Book - Author E.mobi"), "Zed Book", "Author E")
        os.chmod(locked, 0o555)
        try:
            rc, lines, err = run({"EBOOK_ROOT": root, "STATE_DIR": state, "SETTLE_SECONDS": "0"})
        finally:
            os.chmod(locked, 0o755)
        results = {line["folder"]: line["result"] for line in by_msg(lines, "epub_convert")}
        check(rc == 0 and results.get("Author E/Read Only") == "io_error", f"io_error: logged, run continues ({results})")
        check(results.get("Author E/Zed Book") == "converted", "io_error: the next folder still converts")
        census = by_msg(lines, "epub_convert_census")
        check(census and census[0]["unconverted"] == 1 and census[0]["held"] == 0,
              "io_error: not held, counted unconverted (retried next run)")
        check(sorted(os.listdir(locked)) == ["Read Only - Author E.mobi"], "io_error: folder left as it was")

        # A book whose calibre calls run over the per-book timeout is held as timeout.
        rc, lines, err = run({"EBOOK_ROOT": root, "STATE_DIR": state, "SETTLE_SECONDS": "0",
                              "CONVERT_TIMEOUT_SECONDS": "0"})
        results = {line["folder"]: line["result"] for line in by_msg(lines, "epub_convert")}
        check(results == {"Author E/Read Only": "timeout"}, f"timeout: held as timeout ({results})")
        check(sorted(os.listdir(locked)) == ["Read Only - Author E.mobi"], "timeout: folder left as it was")

        # SIGTERM (activeDeadlineSeconds) mid-conversion releases the lock and leaves no partial.
        slow = os.path.join(root, "Author F", "Slow")
        make_book(os.path.join(slow, "Slow - Author F.mobi"), "Slow", "Author F")
        sleeper = os.path.join(tmp, "slow-convert")
        with open(sleeper, "w") as fh:
            fh.write("#!/bin/sh\nsleep 60\n")
        os.chmod(sleeper, 0o755)
        env = dict(os.environ, EBOOK_ROOT=root, STATE_DIR=state, SETTLE_SECONDS="0", EBOOK_CONVERT=sleeper)
        proc = subprocess.Popen([sys.executable, SCRIPT], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        for _ in range(100):
            if os.path.isdir(os.path.join(state, "lock")):
                break
            time.sleep(0.1)
        time.sleep(1)
        proc.terminate()
        proc.wait(timeout=30)
        check(proc.returncode != 0 and not os.path.exists(os.path.join(state, "lock")),
              f"SIGTERM: run ends and releases the lock (rc {proc.returncode})")
        check(sorted(os.listdir(slow)) == ["Slow - Author F.mobi"], "SIGTERM: the folder is left as it was")


def test_duplicates():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "EBooks")
        state = os.path.join(tmp, ".epub-convert")
        # The library already shows the book from a sibling folder (punctuation differs): no second copy.
        held = os.path.join(root, "Author G", "Dirk Gentlys Holistic Detective Agency")
        make_book(os.path.join(held, "Dirk Gentlys Holistic Detective Agency - Author G.epub"),
                  "Dirk Gently's Holistic Detective Agency", "Author G")
        dup = os.path.join(root, "Author G", "Dirk Gently's Holistic Detective Agency")
        make_book(os.path.join(dup, "Author G - Dirk Gently's Holistic Detective Agency.mobi"),
                  "Dirk Gently's Holistic Detective Agency", "Author G")
        # Two mobi-only copies of one book: the first converts, the second is its duplicate.
        first = os.path.join(root, "Author G", "Life the Universe and Everything")
        make_book(os.path.join(first, "Author G - Life the Universe and Everything.mobi"),
                  "Life, the Universe and Everything", "Author G")
        second = os.path.join(root, "Author G", "Life, the Universe, and Everything")
        make_book(os.path.join(second, "Author G - Life, the Universe, and Everything.mobi"),
                  "Life, the Universe and Everything", "Author G")
        # A longer title is another book, not a duplicate.
        other = os.path.join(root, "Author G", "Dirk Gently's Holistic Detective Agency 2")
        make_book(os.path.join(other, "Dirk Gently's Holistic Detective Agency 2 - Author G.mobi"),
                  "Dirk Gently's Holistic Detective Agency 2", "Author G")
        rc, lines, err = run({"EBOOK_ROOT": root, "STATE_DIR": state, "SETTLE_SECONDS": "0"})
        results = {line["folder"]: line["result"] for line in by_msg(lines, "epub_convert")}
        check(rc == 0, f"duplicates: run exits 0 ({err[-300:]})")
        check(sorted(os.listdir(dup)) == ["Author G - Dirk Gently's Holistic Detective Agency.mobi"],
              "duplicates: a book the library shows from a sibling folder is not copied again")
        check(results.get("Author G/Life the Universe and Everything") == "converted"
              and "Author G/Life, the Universe, and Everything" not in results,
              f"duplicates: of two mobi-only copies only the first converts ({results})")
        check(results.get("Author G/Dirk Gently's Holistic Detective Agency 2") == "converted",
              "duplicates: a longer title is another book")
        census = by_msg(lines, "epub_convert_census")
        check(census and census[0]["duplicate"] == 2 and census[0]["unconverted"] == 0 and census[0]["held"] == 0,
              f"duplicates: census counts 2 duplicates, nothing unconverted ({census})")
        # The sibling copy goes: the duplicate converts on the next run.
        os.remove(os.path.join(held, "Dirk Gentlys Holistic Detective Agency - Author G.epub"))
        rc, lines, err = run({"EBOOK_ROOT": root, "STATE_DIR": state, "SETTLE_SECONDS": "0"})
        results = {line["folder"]: line["result"] for line in by_msg(lines, "epub_convert")}
        check(results == {"Author G/Dirk Gently's Holistic Detective Agency": "converted"},
              f"duplicates: converts once the sibling copy is gone ({results})")


def test_series_metadata():
    """The real calibre output is stripped before publication, and the source stays intact."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "EBooks")
        state = os.path.join(tmp, ".epub-convert")
        existing = os.path.join(root, "Author H", "Existing Tagged", "Existing Tagged.epub")
        source = os.path.join(root, "Author H", "Series Output", "Series Output - Author H.mobi")
        make_book(existing, "Existing Tagged", "Author H")
        make_book(source, "Series Output", "Author H")
        for path in (existing, source):
            subprocess.run(["ebook-meta", path, "--series", "Test Saga", "--index", "2"],
                           check=True, capture_output=True)
        source_hash = digest(source)
        existing_hash = digest(existing)
        # Calibre can discard series while converting MOBI. Add real ebook-meta tags to its output,
        # so this fixture proves stripping/backup even when a converter preserves grouping.
        wrapper = os.path.join(tmp, "tag-converted-output")
        with open(wrapper, "w") as script:
            script.write(f"#!{sys.executable}\nimport subprocess,sys\n")
            script.write("rc=subprocess.call(['ebook-convert', *sys.argv[1:]])\n")
            script.write("if rc == 0: rc=subprocess.call(['ebook-meta', sys.argv[2], '--series', 'Test Saga', '--index', '2'])\n")
            script.write("sys.exit(rc)\n")
        os.chmod(wrapper, 0o755)
        rc, lines, err = run({"EBOOK_ROOT": root, "STATE_DIR": state, "SETTLE_SECONDS": "0",
                              "STRIP_SERIES_METADATA": "1", "EBOOK_CONVERT": wrapper})
        check(rc == 0, f"series: gated run succeeds ({err[-300:]})")
        output = os.path.splitext(source)[0] + ".epub"
        check(os.path.exists(output), "series: conversion publishes its EPUB")
        for path in (existing, output):
            with open(path, "rb") as epub:
                check(not epub_metadata.inspect_epub(epub.read())[3],
                      f"series: approved metadata absent from {os.path.basename(path)}")
            title, authors = epub_convert.read_meta(path)
            check(bool(title and authors and "Author H" in authors), "series: real ebook-meta still reads title/author")
        check(digest(source) == source_hash, "series: original mobi remains byte-for-byte unchanged")
        check(digest(existing) != existing_hash, "series: existing tagged EPUB changed")
        manifests = [name for name in os.listdir(os.path.join(state, "backup")) if name.endswith(".json")]
        check(len(manifests) == 2, "series: existing and converted originals both have backups")
        before = {path: (digest(path), os.stat(path).st_mtime_ns) for path in (existing, output)}
        rc, lines, err = run({"EBOOK_ROOT": root, "STATE_DIR": state, "SETTLE_SECONDS": "0",
                              "STRIP_SERIES_METADATA": "1"})
        check(rc == 0 and not by_msg(lines, "epub_convert"), "series: next gated run converts nothing")
        check({path: (digest(path), os.stat(path).st_mtime_ns) for path in (existing, output)} == before,
              "series: untagged EPUBs keep bytes and mtimes on the next run")


if __name__ == "__main__":
    test_title_rule()
    test_runs()
    test_failures()
    test_duplicates()
    test_series_metadata()
    print(f"\n{len(FAILURES)} failed" if FAILURES else "\nall passed")
    sys.exit(1 if FAILURES else 0)
