"""dbupgrade.py: the start-up check never deletes an author that still owns a book row, because books.AuthorID
cascades and the books would go with it (thaynes43/haynesnetwork#665)."""
from hops_tests.hops_base import OverlayTestCase
from lazylibrarian.dbupgrade import check_db


class CheckDbTest(OverlayTestCase):

    def test_author_owning_unlinked_books_survives(self):
        # an author that addBook created before gb.py wrote the bookauthors row: TotalBooks counts zero
        self.add_author('A-unlinked', 'Unlinked Author', status='Paused')
        self.add_book('B-unlinked', 'A-unlinked', 'Unlinked Book', status='Wanted', link=False)
        # an author with no book at all is still removed, as upstream does
        self.add_author('A-empty', 'Empty Author', status='Paused')
        # a miscounted author whose book is linked is recounted and kept, as upstream does
        self.add_author('A-linked', 'Linked Author')
        self.add_book('B-linked', 'A-linked', 'Linked Book')

        check_db()

        self.assertEqual(self.count('books WHERE BookID=?', ('B-unlinked',)), 1)
        self.assertEqual(self.count('authors WHERE AuthorID=?', ('A-unlinked',)), 1)
        self.assertEqual(self.count('authors WHERE AuthorID=?', ('A-empty',)), 0)
        self.assertEqual(self.count('books WHERE BookID=?', ('B-linked',)), 1)
        self.assertEqual(self.db().match("SELECT TotalBooks FROM authors WHERE AuthorID='A-linked'")['TotalBooks'], 1)


class CensusTest(OverlayTestCase):
    """dbupgrade.py fix 2: every start counts the books no author counts (thaynes43/haynesnetwork#736)."""

    def test_census_reports_unlinked_books(self):
        self.add_author('A-census', 'Census Author')
        self.add_book('B-census-linked', 'A-census', 'Linked Census Book')
        self.add_book('B-census-unlinked', 'A-census', 'Unlinked Census Book', link=False)
        with self.assertLogs('lazylibrarian.dbupgrade', level='INFO') as logs:
            check_db()
        self.assertTrue(any('LL_UNLINKED_BOOKS 1 book with no bookauthors row' in line for line in logs.output),
                        logs.output)

        self.db().action("INSERT INTO bookauthors (AuthorID, BookID, Role) VALUES ('A-census', 'B-census-unlinked', 1)")
        self.db().action("DELETE FROM books WHERE BookID NOT IN (SELECT BookID FROM bookauthors)")
        with self.assertLogs('lazylibrarian.dbupgrade', level='INFO') as logs:
            check_db()
        self.assertFalse(any('LL_UNLINKED_BOOKS' in line for line in logs.output))
        self.assertTrue(any('every book has a bookauthors row' in line for line in logs.output))
