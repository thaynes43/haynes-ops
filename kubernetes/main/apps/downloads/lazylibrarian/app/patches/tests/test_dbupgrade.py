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
