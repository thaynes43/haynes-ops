#!/usr/bin/env python3
"""Test harness for the LazyLibrarian overlays in ../ (haynes-ops #3402).

Runs INSIDE the pinned LazyLibrarian image (helmrelease.yaml's tag), never against a live pod:
  1. pins:  each overlay's header names the sha256 of the upstream file it was cut from; the image's copy
            must have that sha, and applying diffs/<name>.diff to it must give the overlay byte for byte.
  2. tests: a copy of the image's /app/lazylibrarian with the overlays dropped in, then the test_*.py here
            through stdlib unittest, one at a time (no parallelism), on upstream's own unit-test scaffolding.
            The tests exercise behaviour, not files: drop an overlay at a bump and its tests run against the
            new upstream code instead, and fail if upstream did not fix the bug.

Usage (README.md has the CI job and the in-cluster one-shot):
  python3 run.py                     pins + tests (what CI runs)
  python3 run.py --pins-only         pins only
  python3 run.py --tests-only --drop all
                                     the tests against the image's own files: every overlay's tests must fail
  python3 run.py --write-diffs DIR   write upstream->overlay diffs into DIR (after an intended overlay edit)
  python3 run.py --rederive DIR      at an image bump: apply diffs/ to THIS image's upstream files, write the
                                     candidate overlays into DIR, and list the hunks that no longer apply
  python3 run.py --replay DB OUT     score every Processed/Snatched/Seeding wanted row of a COPY of the LL
                                     database with and without the volume penalty (replay.py), TSV to OUT
"""
import argparse
import difflib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PATCHES = os.path.dirname(HERE)
DIFFS = os.path.join(HERE, 'diffs')
OVERLAYS = sorted(f[:-3] for f in os.listdir(PATCHES) if f.endswith('.py'))
SHA_LINE = re.compile(r'^#\s+([0-9a-f]{64})\s*$', re.M)
HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')


def read(path):
    with open(path, encoding='utf-8', newline='') as f:
        return f.read()


def sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def make_diff(name, upstream, overlay):
    return ''.join(difflib.unified_diff(upstream.splitlines(keepends=True), overlay.splitlines(keepends=True),
                                        f'a/{name}.py', f'b/{name}.py', n=3))


def parse_hunks(diff):
    hunks, cur = [], None
    for line in diff.splitlines(keepends=True):
        m = HUNK.match(line)
        if m:
            cur = {'start': int(m.group(1)), 'old': [], 'new': []}
            hunks.append(cur)
        elif cur is not None and line[:1] in (' ', '-', '+'):
            if line[0] in ' -':
                cur['old'].append(line[1:])
            if line[0] in ' +':
                cur['new'].append(line[1:])
    return hunks


def apply_diff(upstream, diff, strict=True):
    """Apply a unified diff. strict: every hunk at its recorded line. Otherwise each hunk's old lines may
    sit anywhere after the previous hunk (an image bump moved them), nearest the recorded line first.
    Returns (text, [failed hunk start lines])."""
    src = upstream.splitlines(keepends=True)
    out, pos, offset, failed = [], 0, 0, []
    for h in parse_hunks(diff):
        want = (h['start'] - 1 if h['old'] else h['start']) + offset
        n = len(h['old'])
        if strict:
            spots = [want] if src[want:want + n] == h['old'] else []
        else:
            spots = sorted((i for i in range(pos, len(src) - n + 1) if src[i:i + n] == h['old']),
                           key=lambda i: abs(i - want))
        if not spots:
            failed.append(h['start'])
            continue
        at = spots[0]
        out += src[pos:at] + h['new']
        pos = at + n
        offset = at - (h['start'] - 1 if h['old'] else h['start'])
    return ''.join(out + src[pos:]), failed


def check_pins(ll_root):
    problems = []
    for name in OVERLAYS:
        overlay = read(os.path.join(PATCHES, f'{name}.py'))
        upstream_path = os.path.join(ll_root, 'lazylibrarian', f'{name}.py')
        pinned = SHA_LINE.search(overlay)
        if not pinned:
            problems.append(f'{name}.py: no upstream sha256 line in its header')
            continue
        if not os.path.isfile(upstream_path):
            problems.append(f'{name}.py: the image has no lazylibrarian/{name}.py')
            continue
        upstream = read(upstream_path)
        if sha256(upstream) != pinned.group(1):
            problems.append(f'{name}.py: the image upstream file is sha256 {sha256(upstream)}, the header pins '
                            f'{pinned.group(1)}. A different image: re-derive (--rederive), never carry the file over.')
            continue
        diff_path = os.path.join(DIFFS, f'{name}.diff')
        if not os.path.isfile(diff_path):
            problems.append(f'{name}.py: no diffs/{name}.diff (run --write-diffs)')
            continue
        patched, failed = apply_diff(upstream, read(diff_path))
        if failed:
            problems.append(f'{name}.py: diffs/{name}.diff does not apply cleanly (hunks at {failed})')
        elif patched != overlay:
            problems.append(f'{name}.py: overlay != upstream + diffs/{name}.diff. If the overlay change is '
                            'intended, regenerate the diff with --write-diffs and commit it with the change.')
        else:
            print(f'pin ok   {name}.py  upstream sha256 {pinned.group(1)[:16]}..., diff applies cleanly')
    return problems


def write_diffs(ll_root, out):
    os.makedirs(out, exist_ok=True)
    for name in OVERLAYS:
        upstream = read(os.path.join(ll_root, 'lazylibrarian', f'{name}.py'))
        overlay = read(os.path.join(PATCHES, f'{name}.py'))
        with open(os.path.join(out, f'{name}.diff'), 'w', encoding='utf-8', newline='') as f:
            f.write(make_diff(name, upstream, overlay))
        print(f'wrote {out}/{name}.diff')


def rederive(ll_root, out):
    os.makedirs(out, exist_ok=True)
    bad = 0
    for name in OVERLAYS:
        upstream = read(os.path.join(ll_root, 'lazylibrarian', f'{name}.py'))
        patched, failed = apply_diff(upstream, read(os.path.join(DIFFS, f'{name}.diff')), strict=False)
        with open(os.path.join(out, f'{name}.py'), 'w', encoding='utf-8', newline='') as f:
            f.write(patched)
        state = f'FAILED hunks at old lines {failed}: re-apply those by hand' if failed else 'all hunks applied'
        bad += bool(failed)
        print(f'{name}.py  new upstream sha256 {sha256(upstream)}  {state}')
    print('Then: put each new sha256 in its header, review, copy into patches/, --write-diffs, and run the tests.')
    return 1 if bad else 0


def run_tests(ll_root, pattern, replay=None, drop=()):
    work = tempfile.mkdtemp(prefix='ll-overlay-tests-')
    tree = os.path.join(work, 'll')
    shutil.copytree(ll_root, tree, ignore=shutil.ignore_patterns('__pycache__', '.git'))
    for name in OVERLAYS:
        if name in drop or 'all' in drop:
            print(f'--drop: testing the image upstream {name}.py, not the overlay')
            continue
        shutil.copyfile(os.path.join(PATCHES, f'{name}.py'), os.path.join(tree, 'lazylibrarian', f'{name}.py'))
    shutil.copytree(HERE, os.path.join(tree, 'hops_tests'), ignore=shutil.ignore_patterns('__pycache__', 'diffs'))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', HOPS_TESTS_FIXTURES=os.path.join(tree, 'hops_tests'))
    cmd = [sys.executable, '-m', 'unittest', 'discover', '-s', 'hops_tests', '-t', '.', '-p', pattern, '-v']
    if replay:
        cmd = [sys.executable, '-m', 'hops_tests.replay'] + [os.path.abspath(p) for p in replay]
    try:
        return subprocess.call(cmd, cwd=tree, env=env)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ll-root', default='/app/lazylibrarian', help="the image's LazyLibrarian checkout")
    ap.add_argument('--pins-only', action='store_true')
    ap.add_argument('--tests-only', action='store_true')
    ap.add_argument('--pattern', default='test_*.py', help='unittest discovery pattern')
    ap.add_argument('--write-diffs', metavar='DIR')
    ap.add_argument('--rederive', metavar='DIR')
    ap.add_argument('--replay', nargs=2, metavar=('DB', 'OUT'))
    ap.add_argument('--drop', default='', metavar='NAME[,NAME]',
                    help="run the tests against the image's own copy of these files (or 'all'): at a bump, the "
                         'way to see whether upstream fixed a bug, and the proof that each test can fail')
    args = ap.parse_args()
    if args.write_diffs:
        write_diffs(args.ll_root, args.write_diffs)
        return 0
    if args.rederive:
        return rederive(args.ll_root, args.rederive)
    drop = tuple(n for n in args.drop.split(',') if n)
    if args.replay:
        return run_tests(args.ll_root, args.pattern, replay=args.replay, drop=drop)
    if not args.tests_only:
        problems = check_pins(args.ll_root)
        for p in problems:
            print(f'PIN FAIL {p}')
        if problems:
            return 1
    if args.pins_only:
        return 0
    return run_tests(args.ll_root, args.pattern, drop=drop)


if __name__ == '__main__':
    sys.exit(main())
