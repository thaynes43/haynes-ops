"""gb.py: the API's addBook (GoogleBooks.add_bookid_to_db) writes the bookauthors row, so the new book's author
counts it and the start-up check keeps both (thaynes43/haynesnetwork#665)."""
from unittest import mock

from hops_tests.hops_base import OverlayTestCase, set_config
from lazylibrarian import gb

VOLUME = {
    'id': 'gbFixture01',
    'volumeInfo': {
        'title': 'Fixture Book',
        'authors': ['Fixture Author'],
        'language': 'en',
        'publishedDate': '2020-01-01',
        'publisher': 'Fixture Press',
        'industryIdentifiers': [{'type': 'ISBN_13', 'identifier': '9780000000002'}],
    },
}
AUTHOR = {'authorid': 'OL-fixture-A', 'authorname': 'Fixture Author', 'authorimg': 'images/nophoto.png',
          'authorlink': '', 'authorborn': '', 'authordeath': ''}


class AddBookTest(OverlayTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        set_config({'GB_API': 'fixture-key', 'BOOK_API': 'GoogleBooks', 'ADD_SERIES': False})

    def test_add_bookid_writes_bookauthors(self):
        ol = mock.Mock()
        ol.return_value.find_author_id.return_value = AUTHOR
        with mock.patch.object(gb, 'json_request', return_value=(VOLUME, False)), \
                mock.patch.object(gb, 'OpenLibrary', ol), \
                mock.patch.object(gb, 'get_book_cover', return_value=(None, None)):
            self.assertTrue(gb.GoogleBooks().add_bookid_to_db('gbFixture01', reason='Added by API'))

        self.assertEqual(self.count('books WHERE BookID=?', ('gbFixture01',)), 1)
        row = self.db().match('SELECT AuthorID, Role FROM bookauthors WHERE BookID=?', ('gbFixture01',))
        self.assertTrue(row, 'addBook wrote no bookauthors row')
        self.assertEqual((row['AuthorID'], row['Role']), ('OL-fixture-A', 1))
