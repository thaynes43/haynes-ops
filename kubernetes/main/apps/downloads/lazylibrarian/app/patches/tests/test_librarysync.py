"""librarysync.py: the library scan resets the source ids for every file, so a file with no id of its own is never
linked to the book of the last file that had one (fix 1, thaynes43/haynesnetwork#631); and a scan with `remove`
never deletes a Paused author that still owns a book row (fix 2, thaynes43/haynesnetwork#665)."""
import os
import shutil
import tempfile
import zipfile
from unittest import mock

from hops_tests.hops_base import OverlayTestCase, set_config
from lazylibrarian import librarysync

TEST_EPUB = os.path.join('unittests', 'testdata', 'Test Title - Bob Builder.epub')  # carries GOOGLE id 9876542


def epub_without_ids(src, dest, title):
    """A copy of the upstream test epub with another title and no identifiers at all."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, 'w') as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == 'metadata.opf':
                text = data.decode('utf-8').replace('<dc:title>Test Title</dc:title>', f'<dc:title>{title}</dc:title>')
                text = '\n'.join(line for line in text.splitlines() if 'dc:identifier' not in line)
                data = text.encode('utf-8')
            zout.writestr(item, data)


def sorted_walk(real_walk):
    def walk(top, *args, **kwargs):
        for root, dirs, files in real_walk(top, *args, **kwargs):
            dirs.sort()
            files.sort()
            yield root, dirs, files
    return walk


class LibraryScanTest(OverlayTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        set_config({'BOOK_API': 'GoogleBooks', 'CONTRIBUTING_AUTHORS': False})

    def setUp(self):
        self.lib = tempfile.mkdtemp(prefix='ll-scan-')
        self.addCleanup(shutil.rmtree, self.lib, ignore_errors=True)

    def test_no_id_carry_over_between_files(self):
        self.add_author('A-bob', 'Bob Builder')
        self.add_book('9876542', 'A-bob', 'Test Title', status='Wanted', gb_id='9876542')
        self.add_book('B-second', 'A-bob', 'Second Book', status='Wanted')
        first = os.path.join(self.lib, 'a-first')
        second = os.path.join(self.lib, 'b-second')
        os.makedirs(first)
        os.makedirs(second)
        shutil.copyfile(TEST_EPUB, os.path.join(first, 'Test Title - Bob Builder.epub'))
        epub_without_ids(TEST_EPUB, os.path.join(second, 'Second Book - Bob Builder.epub'), 'Second Book')

        # the files are scanned in this order: the one with an id first, then the one without
        with mock.patch.object(librarysync.os, 'walk', sorted_walk(os.walk)):
            librarysync.library_scan(self.lib, 'eBook', remove=False)

        bookfile = self.db().match("SELECT BookFile FROM books WHERE BookID='9876542'")['BookFile'] or ''
        self.assertNotIn('b-second', bookfile, 'the file without an id was linked to the last id seen')

    def test_remove_spares_authors_that_own_books(self):
        self.add_author('A-owner', 'Owner Author', status='Paused')
        self.add_book('B-owned', 'A-owner', 'Owned Book', link=False)
        self.add_author('A-series', 'Series Contributor', status='Paused')

        librarysync.library_scan(self.lib, 'eBook', remove=True)

        self.assertEqual(self.count('authors WHERE AuthorID=?', ('A-owner',)), 1)
        self.assertEqual(self.count('books WHERE BookID=?', ('B-owned',)), 1)
        self.assertEqual(self.count('authors WHERE AuthorID=?', ('A-series',)), 0)
