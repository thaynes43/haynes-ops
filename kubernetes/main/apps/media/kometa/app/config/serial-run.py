"""Serialize Kometa jobs behind one shared lock.

Since 2026-11-10 every job runs upstream Kometa unmodified (HNET_IMDB_RATINGS_SOURCE
unset or "upstream"). The operations-only IMDb dataset patch below is retired and
unused; see the note at APPROVED_IMDB_SHA256.
"""

import fcntl
import hashlib
import importlib.abc
import importlib.util
import os
from pathlib import Path
import runpy
import sys
import time


LOCK_PATH = Path("/config/.run.lock")
KOMETA_PATH = Path("/kometa.py")
IMDB_PATH = Path("/modules/imdb.py")
# RETIRED 2026-11-10 (owner ruling: follow upstream; story OP513). Nothing in git sets
# HNET_IMDB_RATINGS_SOURCE=dataset now, so this patch is dead code. The hash is the
# reviewed v2.5.2 modules/imdb.py, byte-identical in v2.5.3. v2.5.4 rewrote get_rating
# (batched, cached ratings) and the anchor below no longer exists, so the patch cannot
# be re-approved for v2.5.4 or later. Setting "dataset" on such an image fails closed
# here, before the lock, any Kometa import or any Plex write, and the Job fails. Do
# not update this hash. Restore the patch only by reverting the retirement PR together
# with its v2.5.3 image. Remove this dead path once v2.5.4 runs are verified.
APPROVED_IMDB_SHA256 = "f7061af37477953e5edc24836018d715e5cf038cf83319b7e61ab23726e179fe"
UPSTREAM_GET_RATING = '''    def get_rating(self, imdb_id):
        if not imdb_id:
            return None
        if self._service_available:
            try:
                data = self._service_title(imdb_id)
                return data.get("averageRating") if isinstance(data, dict) else None
            except Failed as e:
                self._service_unavailable(e)
        return self.ratings.get(imdb_id) if self.ratings else None
'''
DATASET_GET_RATING = '''    def get_rating(self, imdb_id):
        if not imdb_id:
            return None
        return self.ratings.get(imdb_id) if self.ratings else None
'''


def patched_imdb_source(source):
    if hashlib.sha256(source).hexdigest() != APPROVED_IMDB_SHA256:
        raise RuntimeError("Unsupported Kometa IMDb source: the retired dataset patch fits only the reviewed "
                           "v2.5.2/v2.5.3 imdb.py; leave HNET_IMDB_RATINGS_SOURCE unset or upstream")
    text = source.decode("utf-8")
    if text.count(UPSTREAM_GET_RATING) != 1:
        raise RuntimeError("Kometa IMDb get_rating anchor does not match the reviewed implementation")
    return text.replace(UPSTREAM_GET_RATING, DATASET_GET_RATING)


class DatasetIMDbLoader(importlib.abc.Loader):
    def create_module(self, spec):
        return None

    def exec_module(self, module):
        source = patched_imdb_source(IMDB_PATH.read_bytes())
        exec(compile(source, str(IMDB_PATH), "exec"), module.__dict__)


class DatasetIMDbFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "modules.imdb":
            return importlib.util.spec_from_file_location(fullname, IMDB_PATH, loader=DatasetIMDbLoader())
        return None


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    source = os.environ.get("HNET_IMDB_RATINGS_SOURCE", "upstream")
    if source not in ("upstream", "dataset"):
        raise RuntimeError("HNET_IMDB_RATINGS_SOURCE must be upstream or dataset")
    if source == "dataset":
        if "--operations-only" not in arguments:
            raise RuntimeError("The IMDb dataset compatibility patch is restricted to operations-only runs")
        # Validate before any Kometa imports or writes. The import hook preserves
        # Kometa's normal logger initialization and all unrelated IMDb methods.
        patched_imdb_source(IMDB_PATH.read_bytes())
        sys.meta_path.insert(0, DatasetIMDbFinder())

    with LOCK_PATH.open("a") as lock:
        print("Waiting for shared Kometa run lock /config/.run.lock", flush=True)
        started = time.monotonic()
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        os.set_inheritable(lock.fileno(), True)
        print(f"Acquired shared Kometa run lock after {time.monotonic() - started:.3f}s", flush=True)
        argv = [str(KOMETA_PATH), *arguments]
        if source == "upstream":
            # Keep the descriptor across exec: the real process holds the lock.
            os.execv(sys.executable, [sys.executable, *argv])
        print("IMDb ratings source: daily official dataset (reviewed Kometa v2.5.2 compatibility patch)", flush=True)
        sys.argv = argv
        sys.path.insert(0, str(KOMETA_PATH.parent))
        # Linux Kometa uses fork, retaining the reviewed module and shared lock
        # in its operations worker. Tini -g forwards termination to that group.
        runpy.run_path(str(KOMETA_PATH), run_name="__main__")


if __name__ == "__main__":
    main()
