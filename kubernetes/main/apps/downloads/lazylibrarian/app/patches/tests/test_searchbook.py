"""searchbook.py, indexer query volume (2026-10-03): the daily backlog search honours DELAYSEARCH (fix 1) with the
back-off capped at 7 skipped runs (fix 2), never sends one book's identical query twice (fix 3), and makes one
category search per indexer per wanted format with no fallback modes (fix 4)."""
from unittest import mock

from hops_tests.hops_base import OverlayTestCase, set_config
from lazylibrarian import searchbook
from lazylibrarian.config2 import CONFIG

PROVIDER = {'ENABLED': True, 'HOST': 'https://indexer.invalid', 'API': 'key', 'DLTYPES': 'E,A'}


def query(provider, api, book, search_type, mode):
    """Stand-in for providers.return_search_structure: 'book' and 'generalbook' ask the same thing."""
    kind = search_type.replace('general', '').replace('short', '').replace('title', '')
    return {'t': kind, 'q': f"{book['authorName']} {book['bookName'].split('(')[0].strip()}"}


class SearchVolumeTest(OverlayTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        set_config({'DELAYSEARCH': True, 'SEARCH_RATELIMIT': 0})

    def setUp(self):
        for table in ('failedsearch', 'wanted', 'bookauthors', 'books', 'authors'):
            self.db().action(f'DELETE FROM {table}')
        self.add_author('A1', 'Fixture Author')

    def backlog(self):
        """Run the scheduled backlog search with one NZB indexer that finds nothing; return the queries sent."""
        sent = []

        def znab(book, search_type):
            sent.append((book['library'], search_type))
            return [], 1

        with mock.patch.object(searchbook, 'iterate_over_znab_sites', side_effect=znab), \
                mock.patch.object(searchbook, 'return_search_structure', side_effect=query, create=True), \
                mock.patch.object(CONFIG, 'providers', side_effect=lambda kind: [PROVIDER] if kind == 'NEWZNAB' else []), \
                mock.patch.object(CONFIG, 'total_active_providers', return_value=1), \
                mock.patch.object(CONFIG, 'use_nzb', return_value=1), \
                mock.patch.object(CONFIG, 'use_tor', return_value=0), \
                mock.patch.object(CONFIG, 'use_direct', return_value=0), \
                mock.patch.object(CONFIG, 'use_rss', return_value=0), \
                mock.patch.object(CONFIG, 'use_irc', return_value=0), \
                mock.patch.object(searchbook, 'thread_name', side_effect=lambda name=None: 'SEARCHALLBOOKS'):
            searchbook.search_book()
        return sent

    def failed(self, bookid, library):
        return self.db().match('SELECT Count, Interval FROM failedsearch WHERE BookID=? AND Library=?',
                               (bookid, library))

    def test_one_category_search_per_format(self):
        self.add_book('B1', 'A1', 'Fixture Book', status='Wanted', audiostatus='Wanted')
        self.assertEqual(sorted(self.backlog()), [('AudioBook', 'audio'), ('eBook', 'book')])

    def test_a_parenthesis_title_is_searched_short(self):
        self.add_book('B2', 'A1', 'Fixture Book (Fixture Series 2)', status='Wanted')
        self.assertEqual(self.backlog(), [('eBook', 'shortbook')])

    def test_backlog_honours_delaysearch(self):
        self.add_book('B3', 'A1', 'Fixture Book', status='Wanted')
        self.db().action("INSERT INTO failedsearch (BookID, Library, Count, Interval, Time) "
                         "VALUES ('B3', 'eBook', 0, 2, 0)")
        self.assertEqual(self.backlog(), [])
        self.assertEqual(self.failed('B3', 'eBook')['Count'], 1)

    def test_backoff_is_capped_at_seven(self):
        self.add_book('B4', 'A1', 'Fixture Book', status='Wanted')
        self.db().action("INSERT INTO failedsearch (BookID, Library, Count, Interval, Time) "
                         "VALUES ('B4', 'eBook', 7, 7, 0)")
        self.assertEqual(self.backlog(), [('eBook', 'book')])
        self.assertEqual((self.failed('B4', 'eBook')['Count'], self.failed('B4', 'eBook')['Interval']), (0, 7))

    def test_identical_query_is_sent_once(self):
        book = {'bookid': 'B5', 'authorName': 'Fixture Author', 'bookName': 'Fixture Book', 'library': 'eBook',
                'searchterm': 'Fixture Author Fixture Book'}
        calls = []

        def znab(book, search_type):
            calls.append(search_type)
            return [{'nzbtitle': 'x'}], 1

        sent = {}
        with mock.patch.object(searchbook, 'iterate_over_znab_sites', side_effect=znab), \
                mock.patch.object(searchbook, 'return_search_structure', side_effect=query, create=True), \
                mock.patch.object(CONFIG, 'providers', side_effect=lambda kind: [PROVIDER] if kind == 'NEWZNAB' else []):
            first, _ = searchbook._znab_once(book, 'book', sent)
            second, _ = searchbook._znab_once(book, 'generalbook', sent)
            searchbook._znab_once(book, 'audio', sent)
        self.assertEqual(calls, ['book', 'audio'])
        self.assertEqual(first, second)
        self.assertIsNot(first, second)  # a copy: one search's scoring cannot change the other's results
