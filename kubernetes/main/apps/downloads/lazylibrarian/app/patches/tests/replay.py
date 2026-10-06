"""Historical replay of the resultlist.py penalties (volume, language) over a COPY of LazyLibrarian's database.

Not a unit test (the data is the household's download history and stays out of git). run.py --replay copies
this into the test tree and runs it there, so it scores with the effective code: the image plus the overlays.

For every wanted row in Processed, Snatched or Seeding whose book still exists, the release is scored for its
book twice through find_best_result, once with the penalties disabled (upstream scoring) and once as patched,
with the book's own BookLang and the live config values from hops_base.LIVE. The database is opened read-only (immutable). Output: one TSV
row per wanted row, then a summary on stderr. Compare two TSVs (old and new overlay) to see what a change moved.
"""
import csv
import sqlite3
import sys

from hops_tests.hops_base import OverlayTestCase, score

QUERY = """
SELECT w.rowid AS rowid, w.Status, w.AuxInfo, w.NZBtitle, b.BookID, b.BookName, b.BookSub, b.BookLang,
       a.AuthorName
FROM wanted w JOIN books b ON b.BookID = w.BookID JOIN authors a ON a.AuthorID = b.AuthorID
WHERE w.Status IN ('Processed', 'Snatched', 'Seeding') ORDER BY w.rowid
"""


def main(db_path, out_path):
    src = sqlite3.connect(f'file:{db_path}?mode=ro&immutable=1', uri=True)
    src.row_factory = sqlite3.Row
    rows = src.execute(QUERY).fetchall()
    src.close()
    OverlayTestCase.setUpClass()
    changed = below = 0
    try:
        with open(out_path, 'w', newline='', encoding='utf-8') as f:
            out = csv.writer(f, delimiter='\t')
            out.writerow(['rowid', 'status', 'aux', 'upstream', 'patched', 'volume', 'other', 'language', 'booklang',
                          'book', 'sub', 'author', 'release'])
            for row in rows:
                args = (row['AuxInfo'], row['BookName'], row['BookSub'], row['AuthorName'], row['NZBtitle'])
                before, _ = score(*args, penalty=False, lang=row['BookLang'])
                after, volume, language = score(*args, lang=row['BookLang'], detail=True)
                changed += before != after
                below += before is not None and after is not None and before >= 80 > after
                out.writerow([row['rowid'], row['Status'], row['AuxInfo'], before, after,
                              volume[0] if volume else '', volume[1] if volume else '', language or '',
                              row['BookLang'] or '', row['BookName'], row['BookSub'] or '', row['AuthorName'],
                              row['NZBtitle']])
    finally:
        OverlayTestCase.tearDownClass()
    print(f'replayed {len(rows)} rows: {changed} scores changed, {below} fall from >= 80 to under 80',
          file=sys.stderr)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
