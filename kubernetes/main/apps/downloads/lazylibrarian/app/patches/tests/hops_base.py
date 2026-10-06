"""Shared scaffolding for the overlay tests: upstream LazyLibrarian's own unit-test start-up (config, logging,
a fresh test database built by db_upgrade), plus the live config values that change scoring and search."""
import json
import os
import socket

from lazylibrarian import database
from lazylibrarian.config2 import CONFIG
from lazylibrarian.configtypes import ConfigCSV
from unittests.unittesthelpers import LLTestCaseWithStartup

FIXTURES = os.path.join(os.environ.get('HOPS_TESTS_FIXTURES', os.path.dirname(__file__)), 'fixtures')

# Values from the live config.ini that the patched code reads (2026-10-06). Everything else is upstream's
# test default. REJECT_* and the size limits are cleared so a fixture release is scored, never rejected.
LIVE = {
    'EBOOK_TYPE': 'epub, mobi, pdf, azw3',
    'AUDIOBOOK_TYPE': 'mp3, m4b, m4a',
    'PREFER_WORDS': 'retail',
    'MATCH_RATIO': 80,
    'REJECT_WORDS': '',
    'REJECT_AUDIO': '',
    'REJECT_MAXSIZE': 0,
    'REJECT_MINSIZE': 0,
    'REJECT_MAXAUDIO': 0,
    'REJECT_MINAUDIO': 0,
    'BLACKLIST_FAILED': False,
    'BLACKLIST_PROCESSED': False,
}


def set_config(values):
    for key, value in values.items():
        if isinstance(value, bool):
            CONFIG.set_bool(key, value)
            got = CONFIG.get_bool(key)
        elif isinstance(value, int):
            CONFIG.set_int(key, value)
            got = CONFIG.get_int(key)
        else:
            if isinstance(CONFIG.get_item(key), ConfigCSV):
                CONFIG.set_csv(key, value)
            else:
                CONFIG.set_str(key, value)
            got, value = CONFIG[key].replace(' ', ''), value.replace(' ', '')
        if got != value:
            raise AssertionError(f'config {key}: set {value!r}, reads {got!r}')


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding='utf-8') as f:
        return json.load(f)


def score(library, title, sub, author, release, penalty=True):
    """(score, names_other_volume result) for one release against one book, through resultlist.find_best_result
    exactly as a search scores it (source nzb). penalty=False is upstream scoring (the overlay's penalty off)."""
    from lazylibrarian import resultlist
    book = {'bookid': 'fixture', 'authorName': author, 'bookName': title, 'bookSub': sub or '', 'library': library,
            'searchterm': title}
    res = {'nzbtitle': release, 'nzburl': 'http://fixture.invalid/nzb', 'nzbprov': 'fixture', 'nzbsize': 0,
           'nzbmode': 'nzb', 'priority': 0}
    original = getattr(resultlist, 'names_other_volume', None)
    seen = {}

    def spy(*args, **kwargs):
        seen['volume'] = original(*args, **kwargs) if penalty else None
        return seen['volume']

    if original:
        resultlist.names_other_volume = spy
    try:
        match = resultlist.find_best_result([res], book, 'book' if library == 'eBook' else 'audiobook', 'nzb')
    finally:
        if original:
            resultlist.names_other_volume = original
    return (round(match[0], 2) if match else None), seen.get('volume')


_real_connect = socket.socket.connect


def _no_network(sock, address):
    """The tests are hermetic: any connection off this host fails at once (CI also runs with --network none)."""
    if sock.family == socket.AF_UNIX or (isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1', 'localhost')):
        return _real_connect(sock, address)
    raise OSError(f'overlay tests: no network ({address!r})')


socket.socket.connect = _no_network


class OverlayTestCase(LLTestCaseWithStartup):
    """One LazyLibrarian start per test class, on a throwaway database."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        set_config(LIVE)

    def add_author(self, authorid, name, status='Active', total=0):
        self.db().action('INSERT INTO authors (AuthorID, AuthorName, Status, TotalBooks) VALUES (?, ?, ?, ?)',
                         (authorid, name, status, total))

    def add_book(self, bookid, authorid, name, status='Skipped', audiostatus='Skipped', link=True, **extra):
        cols = {'BookID': bookid, 'AuthorID': authorid, 'BookName': name, 'Status': status,
                'AudioStatus': audiostatus, **extra}
        self.db().action(f"INSERT INTO books ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         tuple(cols.values()))
        if link:
            self.db().action('INSERT INTO bookauthors (AuthorID, BookID, Role) VALUES (?, ?, 1)', (authorid, bookid))

    def count(self, sql, args=()):
        return self.db().match(f'SELECT count(*) AS n FROM {sql}', args)['n']

    @staticmethod
    def db():
        return database.DBConnection()
