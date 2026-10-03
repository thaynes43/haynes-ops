# ---------------------------------------------------------------------------
# haynes-ops override (downloads/lazylibrarian, ConfigMap lazylibrarian-searchbook)
#
# This is LazyLibrarian's upstream lazylibrarian/searchbook.py plus this
# comment block and four marked changes (search for "haynes-ops fix").
# Everything else is upstream, byte for byte.
#
# PINNED TO UPSTREAM: image docker.io/linuxserver/lazylibrarian:version-40a389ea
#   (LazyLibrarian commit 40a389ea, pyproject version 2026.05.25).
#   sha256 of the unmodified upstream file in that image:
#   c4871db843d91fa12d7c35345266e5f1bd209f61e44b2f0c6eb40100f35df55f
#
# Why: LazyLibrarian was ~80% of all Prowlarr queries (50,431 of ~63,000 in
# the week to 2026-10-03). Each wanted format costs 3 queries per indexer per
# run (5 when the title has a parenthesis), the daily backlog search re-ran
# all 271 wanted formats every day whatever their history, and about 10% of
# the queries in a run repeated one already sent for the same book.
#
#   fix 1: the scheduled backlog search (thread SEARCHALLBOOKS) honours
#          DELAYSEARCH ("Increase delay for previously failed searches").
#          Upstream forces that thread, so the setting never applied to the
#          one search that re-runs every wanted book. Explicit asks
#          (API-SEARCH*, FORCE-SEARCH*) stay forced, as upstream.
#   fix 2: the DELAYSEARCH back-off stops growing at 7 skipped runs, so a
#          book that keeps missing is still searched about once a week
#          instead of ever more rarely.
#   fix 3: within one book's search, a mode whose queries are identical,
#          provider by provider, to a mode already sent (the eBook "title"
#          mode repeats the "book" mode whenever the author has no initials;
#          eBook and AudioBook "general" modes are the same no-category
#          query) reuses the earlier results instead of asking the indexers
#          again. Results carry no format, so reuse across formats is exact.
#   fix 4: CATEGORY SEARCH ONLY (owner ruling 2026-10-03). Each wanted format
#          is searched once per indexer, in its book category (eBook 7020,
#          audiobook 3030). Upstream follows a miss with up to four more
#          queries per indexer (short, no-category "general", short-general,
#          and "title"); those are dropped. For a title with a parenthesis,
#          the one query is the short form (the part before the
#          parenthesis), which upstream only tried second, because the full
#          form ("Dune (Movie Tie-In)") rarely matches a release name. Set
#          CATEGORY_SEARCH_ONLY = False to restore upstream's fallbacks.
#
# Needs config.ini [SearchScan] delaysearch = True for fixes 1 and 2 to do
# anything (set live 2026-10-03; old value: unset, i.e. False).
#
# BEFORE BUMPING THE IMAGE TAG in helmrelease.yaml: take the new image's
# searchbook.py and re-apply the four marked changes to it (or drop this
# override if upstream has fixed them). Never carry this file onto a
# different image unchanged: it would silently revert every other upstream
# change to the book search.
# ---------------------------------------------------------------------------
#  This file is part of Lazylibrarian.
#  Lazylibrarian is free software':'you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#  Lazylibrarian is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#  You should have received a copy of the GNU General Public License
#  along with Lazylibrarian.  If not, see <http://www.gnu.org/licenses/>.


import logging
import threading
import time
import traceback
from copy import deepcopy

import lazylibrarian
from lazylibrarian import database
from lazylibrarian.blockhandler import BLOCKHANDLER
from lazylibrarian.config2 import CONFIG
from lazylibrarian.formatter import check_int, plural, thread_name
from lazylibrarian.providers import (
    iterate_over_direct_sites,
    iterate_over_irc_sites,
    iterate_over_rss_sites,
    iterate_over_torrent_sites,
    iterate_over_znab_sites,
    return_search_structure,
)
from lazylibrarian.resultlist import download_result, find_best_result
from lazylibrarian.telemetry import TELEMETRY


# haynes-ops fix 4: one category search per indexer per wanted format, no fallback modes.
CATEGORY_SEARCH_ONLY = True


# haynes-ops fix 3: never send one book's identical query twice in one search.
def _znab_query_key(book, search_type):
    """What iterate_over_znab_sites(book, search_type) would send, provider by provider, as a
    hashable key. Mirrors its enabled / blocked / DLTYPES filtering."""
    if 'book' in search_type:
        dltype = 'E'
    elif 'audio' in search_type:
        dltype = 'A'
    elif 'mag' in search_type:
        dltype = 'M'
    elif 'comic' in search_type:
        dltype = 'C'
    else:
        dltype = ''
    key = []
    for kind in ('NEWZNAB', 'TORZNAB'):
        for provider in CONFIG.providers(kind):
            if not provider['ENABLED']:
                continue
            host = provider['HOST']
            if BLOCKHANDLER.is_blocked(host):
                key.append((kind, host, 'blocked'))
                continue
            if dltype and dltype not in provider['DLTYPES']:
                continue
            params = return_search_structure(provider, provider['API'], book, search_type, 'nzb') or {}
            key.append((kind, host, tuple(sorted((str(k), repr(v)) for k, v in params.items()))))
    return tuple(key)


def _znab_once(book, search_type, sent):
    """iterate_over_znab_sites, unless this book already sent exactly these queries in this search:
    then reuse those results (copied) rather than ask the same indexers the same thing again."""
    logger = logging.getLogger(__name__)
    try:
        key = _znab_query_key(book, search_type)
    except Exception as e:
        logger.debug(f"haynes-ops: no query key for {search_type}: {type(e).__name__} {e}")
        key = None
    if key is not None and key in sent:
        logger.debug(f"haynes-ops: {search_type} search for {book['library']} {book['searchterm']} "
                     f"repeats an earlier query, reusing its results")
        resultlist, nprov = sent[key]
        return deepcopy(resultlist), nprov
    resultlist, nprov = iterate_over_znab_sites(book, search_type)
    if key is not None:
        sent[key] = (deepcopy(resultlist), nprov)
    return resultlist, nprov


def cron_search_book():
    logger = logging.getLogger(__name__)
    if 'SEARCHALLBOOKS' not in [n.name for n in list(threading.enumerate())]:
        search_book()
    else:
        logger.debug("SEARCHALLBOOKS is already running")


def good_enough(match):
    return bool(match and int(match[0]) >= CONFIG.get_int('MATCH_RATIO'))


def warn_mode(mode):
    # don't nag. Show warning messages no more than every 20 mins
    logger = logging.getLogger(__name__)
    timenow = int(time.time())
    if mode == 'rss':
        if check_int(lazylibrarian.TIMERS['NO_RSS_MSG'], 0) + 1200 < timenow:
            lazylibrarian.TIMERS['NO_RSS_MSG'] = timenow
        else:
            return
    elif mode == 'nzb':
        if check_int(lazylibrarian.TIMERS['NO_NZB_MSG'], 0) + 1200 < timenow:
            lazylibrarian.TIMERS['NO_NZB_MSG'] = timenow
        else:
            return
    elif mode == 'tor':
        if check_int(lazylibrarian.TIMERS['NO_TOR_MSG'], 0) + 1200 < timenow:
            lazylibrarian.TIMERS['NO_TOR_MSG'] = timenow
        else:
            return
    elif mode == 'irc':
        if check_int(lazylibrarian.TIMERS['NO_IRC_MSG'], 0) + 1200 < timenow:
            lazylibrarian.TIMERS['NO_IRC_MSG'] = timenow
        else:
            return
    elif mode == 'direct':
        if check_int(lazylibrarian.TIMERS['NO_DIRECT_MSG'], 0) + 1200 < timenow:
            lazylibrarian.TIMERS['NO_DIRECT_MSG'] = timenow
        else:
            return
    else:
        return
    logger.warning(f'No {mode} providers are available. Check config and blocklist')


def search_book(books=None, library=None):
    """
    books is a list of new books to add, or None for backlog search
    library is "eBook" or "AudioBook" or None to search all book types
    """
    TELEMETRY.record_usage_data('Search/Book')
    logger = logging.getLogger(__name__)
    searchinglogger = logging.getLogger('special.searching')
    searchinglogger.debug(f"search_book: {books}")
    db = database.DBConnection()
    # noinspection PyBroadException
    try:
        threadname = thread_name()
        if "SEARCH" not in threadname:
            if not books:
                thread_name("SEARCHALLBOOKS")
                threadname = "SEARCHALLBOOKS"
            else:
                thread_name("SEARCHBOOKS")

        # haynes-ops fix 1: the scheduled backlog (SEARCHALLBOOKS) honours DELAYSEARCH; upstream also
        # matched 'SEARCHALL' here, which forced it and made DELAYSEARCH a no-op for the daily search.
        if 'API-SEARCH' in threadname or 'FORCE-SEARCH' in threadname:
            force = True
        else:
            force = False

        logger.debug(f"Storing start time for {thread_name()}")
        db.upsert("jobs", {"Start": time.time()}, {"Name": thread_name()})
        searchlist = []
        searchbooks = []

        if not books:
            # We are performing a backlog search
            cmd = ("SELECT BookID, AuthorName, Bookname, BookSub, BookAdded, books.Status, AudioStatus "
                   "from books,authors WHERE (books.Status='Wanted' OR AudioStatus='Wanted') and "
                   "books.AuthorID = authors.AuthorID order by BookAdded desc")
            results = db.select(cmd)
            for terms in results:
                searchbooks.append(terms)
        else:
            # The user has added new books
            if library:
                logger.debug(f"Searching for {len(books)} {plural(len(books), library)}")
                searchinglogger.debug(f"{books}")
            for book in books:
                if book['bookid'] not in ['booklang', 'library', 'ignored']:
                    cmd = ("SELECT BookID, AuthorName, BookName, BookSub, books.Status, AudioStatus "
                           "from books,authors WHERE BookID=? AND books.AuthorID = authors.AuthorID")
                    results = db.select(cmd, (book['bookid'],))
                    if results:
                        for terms in results:
                            searchbooks.append(terms)
                    else:
                        logger.debug(f"SearchBooks - BookID {book['bookid']} is not in the database")

        if len(searchbooks) == 0:
            logger.debug("No books to search for")
            db.upsert("jobs", {"Finish": time.time()}, {"Name": thread_name()})
            return

        nprov = CONFIG.total_active_providers()
        if nprov == 0:
            msg = "SearchBooks - No providers to search"
            blocked = BLOCKHANDLER.number_blocked()
            if blocked:
                msg += f" (there {plural(blocked, 'is')} {blocked} in blocklist)"
            else:
                msg += " (check you have some enabled)"
            logger.debug(msg)
            db.upsert("jobs", {"Finish": time.time()}, {"Name": thread_name()})
            return

        modelist = []
        if CONFIG.use_nzb():
            modelist.append('nzb')
        if CONFIG.use_tor():
            modelist.append('tor')
        if CONFIG.use_direct():
            modelist.append('direct')
        if CONFIG.use_rss():
            modelist.append('rss')
        if CONFIG.use_irc():
            modelist.append('irc')

        if not library:
            library = 'item'

        logger.info(
            f"Searching {nprov} {plural(nprov, 'provider')} {str(modelist)} for {len(searchbooks)} "
            f"{plural(len(searchbooks), library)}")
        logger.info(
            f"Provider Blocklist contains {BLOCKHANDLER.number_blocked()} "
            f"{plural(BLOCKHANDLER.number_blocked(), 'entry')}")

        for searchbook in searchbooks:
            if lazylibrarian.STOPTHREADS and thread_name() == "SEARCHALLBOOKS":
                logger.debug("STOPTHREADS Aborting SEARCHALLBOOKS")
                break

            # searchterm is only used for display purposes
            searchterm = ''
            if searchbook['AuthorName']:
                searchterm = searchbook['AuthorName']
            else:
                logger.warning(f"No AuthorName for {searchbook['BookID']}")

            if searchbook['BookName']:
                if len(searchterm):
                    searchterm += ' '
                searchterm += searchbook['BookName']
            else:
                logger.warning(f"No BookName for {searchbook['BookID']}")

            if searchbook['BookSub']:
                if len(searchterm):
                    searchterm += ': '
                searchterm += searchbook['BookSub']

            if searchbook['Status'] == "Wanted":
                cmd = "SELECT BookID from wanted WHERE BookID=? and AuxInfo='eBook' and Status='Snatched'"
                snatched = db.match(cmd, (searchbook["BookID"],))
                if snatched:
                    logger.warning(
                        f"eBook {searchbook['AuthorName']} {searchbook['BookName']} "
                        f"already marked snatched in wanted table")
                else:
                    searchlist.append(
                        {"bookid": searchbook['BookID'],
                         "bookName": searchbook['BookName'],
                         "bookSub": searchbook['BookSub'],
                         "authorName": searchbook['AuthorName'],
                         "library": "eBook",
                         "searchterm": searchterm})

            if searchbook['AudioStatus'] == "Wanted":
                cmd = "SELECT BookID from wanted WHERE BookID=? and AuxInfo='AudioBook' and Status='Snatched'"
                snatched = db.match(cmd, (searchbook["BookID"],))
                if snatched:
                    logger.warning(
                        f"AudioBook {searchbook['AuthorName']} {searchbook['BookName']} "
                        f"already marked snatched in wanted table")
                else:
                    searchlist.append(
                        {"bookid": searchbook['BookID'],
                         "bookName": searchbook['BookName'],
                         "bookSub": searchbook['BookSub'],
                         "authorName": searchbook['AuthorName'],
                         "library": "AudioBook",
                         "searchterm": searchterm})

        # only get rss results once per run, as they are not search specific
        rss_resultlist = None
        if CONFIG.use_rss():
            rss_resultlist, nprov, dltypes = iterate_over_rss_sites()
            if not nprov or (library == 'Audiobook' and 'A' not in dltypes) or \
                    (library == 'eBook' and 'E' not in dltypes) or \
                    (library is None and ('E' in dltypes or 'A' in dltypes)):
                warn_mode('rss')

        book_count = 0
        sent = {}  # haynes-ops fix 3: queries already sent for sent_for (one book, both formats)
        sent_for = None
        for book in searchlist:
            if book['bookid'] != sent_for:
                sent, sent_for = {}, book['bookid']
            if lazylibrarian.STOPTHREADS and thread_name() == "SEARCHALLBOOKS":
                logger.debug("STOPTHREADS Aborting SEARCHALLBOOKS")
                break
            do_search = True
            if CONFIG.get_bool('DELAYSEARCH') and not force:
                res = db.match('SELECT * FROM failedsearch WHERE BookID=? AND Library=?',
                               (book['bookid'], book['library']))
                if not res:
                    logger.debug(f"SearchDelay: {book['library']} {book['bookid']} has not failed before")
                else:
                    skipped = check_int(res['Count'], 0)
                    interval = check_int(res['Interval'], 0)
                    if skipped < interval:
                        logger.debug(f"SearchDelay: {book['library']} {book['bookid']} not due ({skipped}/{interval})")
                        db.action("UPDATE failedsearch SET Count=? WHERE BookID=? AND Library=?",
                                  (skipped + 1, book['bookid'], book['library']))
                        do_search = False
                    else:
                        logger.debug(
                            f"SearchDelay: {book['library']} {book['bookid']} due this time ({skipped}/{interval})")

            matches = []
            if do_search:
                # first attempt, try author/title in category "book"
                if book['library'] == 'AudioBook':
                    searchtype = 'audio'
                else:
                    searchtype = 'book'

                # haynes-ops fix 4: with CATEGORY_SEARCH_ONLY this is the ONLY query per provider, so a
                # title with a parenthesis is searched in its short form (upstream's second try).
                if CATEGORY_SEARCH_ONLY and '(' in book['bookName']:
                    first_type = f"short{searchtype}"
                else:
                    first_type = searchtype

                if CONFIG.use_nzb():
                    resultlist, nprov = _znab_once(book, first_type, sent)  # haynes-ops fix 3
                    if not nprov:
                        warn_mode('nzb')
                    elif resultlist:
                        match = find_best_result(resultlist, book, searchtype, 'nzb')
                        if not good_enough(match):
                            logger.info(f"NZB search for {book['library']} {book['searchterm']} returned no results.")
                        else:
                            logger.info(
                                f"Found NZB result: {searchtype} {round(match[0], 2)}%, {match[1]['NZBprov']} "
                                f"priority {match[3]}")
                            matches.append(match)

                if CONFIG.use_tor():
                    resultlist, nprov = iterate_over_torrent_sites(book, first_type)  # haynes-ops fix 4
                    if not nprov:
                        warn_mode('tor')
                    elif resultlist:
                        match = find_best_result(resultlist, book, searchtype, 'tor')
                        if not good_enough(match):
                            logger.info(
                                f"Torrent search for {book['library']} {book['searchterm']} returned no results.")
                        else:
                            logger.info(
                                f"Found Torrent result: {searchtype} {round(match[0], 2)}%, {match[1]['NZBprov']} "
                                f"priority {match[3]}")
                            matches.append(match)

                if CONFIG.use_direct():
                    resultlist, nprov = iterate_over_direct_sites(book, first_type)  # haynes-ops fix 4
                    if not nprov:
                        warn_mode('direct')
                    elif resultlist:
                        match = find_best_result(resultlist, book, searchtype, 'direct')
                        if not good_enough(match):
                            logger.info(
                                f"Direct search for {book['library']} {book['searchterm']} returned no results.")
                        else:
                            logger.info(
                                f"Found Direct result: {searchtype} {round(match[0], 2)}%, "
                                f"{match[1]['NZBprov']} priority {match[3]}")
                            matches.append(match)

                if CONFIG.use_irc():
                    resultlist, nprov = iterate_over_irc_sites(book, first_type)  # haynes-ops fix 4
                    if not nprov:
                        warn_mode('irc')
                    elif resultlist:
                        match = find_best_result(resultlist, book, searchtype, 'irc')
                        if not good_enough(match):
                            logger.info(f"IRC search for {book['library']} {book['searchterm']} returned no results.")
                        else:
                            logger.info(
                                f"Found IRC result: {searchtype} {round(match[0], 2)}%, {match[1]['NZBprov']} "
                                f"priority {match[3]}")
                            matches.append(match)

                if CONFIG.use_rss() and rss_resultlist:
                    match = find_best_result(rss_resultlist, book, searchtype, 'rss')
                    if not good_enough(match):
                        logger.info(f"RSS search for {book['library']} {book['searchterm']} returned no results.")
                    else:
                        logger.info(
                            f"Found RSS result: {searchtype} {round(match[0], 2)}%, "
                            f"{match[1]['NZBprov']} priority {match[3]}")
                        matches.append(match)

                # if you can't find the book, try author/title without any "(extended details, series etc)"
                if not CATEGORY_SEARCH_ONLY and not matches and '(' in book['bookName']:  # haynes-ops fix 4
                    if CONFIG.use_nzb():
                        resultlist, nprov = _znab_once(book, f"short{searchtype}", sent)  # haynes-ops fix 3
                        if not nprov:
                            warn_mode('nzb')
                        elif resultlist:
                            match = find_best_result(resultlist, book, searchtype, 'nzb')
                            if not good_enough(match):
                                logger.info(
                                    f"NZB short search for {book['library']} {book['searchterm']} returned no results.")
                            else:
                                logger.info(
                                    f"Found NZB result: {searchtype} {round(match[0], 2)}%, {match[1]['NZBprov']} "
                                    f"priority {match[3]}")
                                matches.append(match)

                    if CONFIG.use_tor():
                        resultlist, nprov = iterate_over_torrent_sites(book, f"short{searchtype}")
                        if not nprov:
                            warn_mode('tor')
                        elif resultlist:
                            match = find_best_result(resultlist, book, searchtype, 'tor')
                            if not good_enough(match):
                                logger.info(
                                    f"Torrent short search for {book['library']} {book['searchterm']} "
                                    f"returned no results.")
                            else:
                                logger.info(
                                    f"Found Torrent result: {searchtype} {round(match[0], 2)}%, {match[1]['NZBprov']} "
                                    f"priority {match[3]}")
                                matches.append(match)

                    if CONFIG.use_direct():
                        resultlist, nprov = iterate_over_direct_sites(book, f"short{searchtype}")
                        if not nprov:
                            warn_mode('direct')
                        elif resultlist:
                            match = find_best_result(resultlist, book, searchtype, 'direct')
                            if not good_enough(match):
                                logger.info(
                                    f"Direct short search for {book['library']} {book['searchterm']} "
                                    f"returned no results.")
                            else:
                                logger.info(
                                    f"Found Direct result: {searchtype} {round(match[0], 2)}%, {match[1]['NZBprov']} "
                                    f"priority {match[3]}")
                                matches.append(match)

                    if CONFIG.use_irc():
                        resultlist, nprov = iterate_over_irc_sites(book, f"short{searchtype}")
                        if not nprov:
                            warn_mode('irc')
                        elif resultlist:
                            match = find_best_result(resultlist, book, searchtype, 'irc')
                            if not good_enough(match):
                                logger.info(
                                    f"IRC short search for {book['library']} {book['searchterm']} returned no results.")
                            else:
                                logger.info(
                                    f"Found IRC result: {searchtype} {round(match[0], 2)}%, "
                                    f"{match[1]['NZBprov']} priority {match[3]}")
                                matches.append(match)

                    if CONFIG.use_rss() and rss_resultlist:
                        match = find_best_result(rss_resultlist, book, searchtype, 'rss')
                        if not good_enough(match):
                            logger.info(
                                f"RSS short search for {book['library']} {book['searchterm']} returned no results.")
                        else:
                            logger.info(
                                f"Found RSS result: {searchtype} {round(match[0], 2)}%, {match[1]['NZBprov']} "
                                f"priority {match[3]}")
                            matches.append(match)

                # if you can't find the book under "books", you might find under general search
                # general search is the same as booksearch for torrents, irc and rss, no need to check again
                if not CATEGORY_SEARCH_ONLY and not matches and CONFIG.use_nzb():  # haynes-ops fix 4
                    resultlist, nprov = _znab_once(book, f"general{searchtype}", sent)  # haynes-ops fix 3
                    if not nprov:
                        warn_mode('nzb')
                    elif resultlist:
                        match = find_best_result(resultlist, book, searchtype, 'nzb')
                        if not good_enough(match):
                            logger.info(
                                f"NZB general search for {book['library']} {book['searchterm']} returned no results.")
                        else:
                            logger.info(
                                f"Found NZB result: {searchtype} {round(match[0], 2)}%, {match[1]['NZBprov']} "
                                f"priority {match[3]}")
                            matches.append(match)

                # if still not found, try general search again without any "(extended details, series etc)"
                # shortgeneral is the same as shortbook for torrents, irc and rss, no need to check again
                if not CATEGORY_SEARCH_ONLY and not matches and CONFIG.use_nzb() and '(' in book['searchterm']:  # fix 4
                    resultlist, nprov = _znab_once(book, f"shortgeneral{searchtype}", sent)  # haynes-ops fix 3
                    if not nprov:
                        warn_mode('nzb')
                    elif resultlist:
                        match = find_best_result(resultlist, book, searchtype, 'nzb')
                        if not good_enough(match):
                            logger.info(
                                f"NZB shortgeneral search for {book['library']} {book['searchterm']} "
                                f"returned no results.")
                        else:
                            logger.info(
                                f"Found NZB result: {searchtype} {round(match[0], 2)}%, "
                                f"{match[1]['NZBprov']} priority {match[3]}")
                            matches.append(match)

                # if still not found, try general search again with title only
                if not CATEGORY_SEARCH_ONLY and not matches:  # haynes-ops fix 4
                    if CONFIG.use_nzb():
                        resultlist, nprov = _znab_once(book, f"title{searchtype}", sent)  # haynes-ops fix 3
                        if not nprov:
                            warn_mode('nzb')
                        elif resultlist:
                            match = find_best_result(resultlist, book, searchtype, 'nzb')
                            if not good_enough(match):
                                logger.info(
                                    f"NZB title search for {book['library']} {book['searchterm']} returned no results.")
                            else:
                                logger.info(
                                    f"Found NZB result: {searchtype} {round(match[0], 2)}%, {match[1]['NZBprov']} "
                                    f"priority {match[3]}")
                                matches.append(match)

                    if CONFIG.use_tor():
                        resultlist, nprov = iterate_over_torrent_sites(book, f"title{searchtype}")
                        if not nprov:
                            warn_mode('tor')
                        elif resultlist:
                            match = find_best_result(resultlist, book, searchtype, 'tor')
                            if not good_enough(match):
                                logger.info(
                                    f"Torrent title search for {book['library']} {book['searchterm']} "
                                    f"returned no results.")
                            else:
                                logger.info(
                                    f"Found Torrent result: {searchtype} {round(match[0], 2)}%, {match[1]['NZBprov']} "
                                    f"priority {match[3]}")
                                matches.append(match)

                    if CONFIG.use_direct():
                        resultlist, nprov = iterate_over_direct_sites(book, f"title{searchtype}")
                        if not nprov:
                            warn_mode('direct')
                        elif resultlist:
                            match = find_best_result(resultlist, book, searchtype, 'direct')
                            if not good_enough(match):
                                logger.info(
                                    f"Direct title search for {book['library']} {book['searchterm']}"
                                    f" returned no results.")
                            else:
                                logger.info(
                                    f"Found Direct result: {searchtype} {round(match[0], 2)}%, {match[1]['NZBprov']} "
                                    f"priority {match[3]}")
                                matches.append(match)

                    # irchighway says search results without both author and title will be
                    # silently rejected but that doesn't seem to be actioned...
                    if CONFIG.use_irc():
                        resultlist, nprov = iterate_over_irc_sites(book, f"title{searchtype}")
                        if not nprov:
                            warn_mode('irc')
                        elif resultlist:
                            match = find_best_result(resultlist, book, searchtype, 'irc')
                            if not good_enough(match):
                                logger.info(
                                    f"IRC title search for {book['library']} {book['searchterm']} returned no results.")
                            else:
                                logger.info(
                                    f"Found IRC result: {searchtype} {round(match[0], 2)}%, {match[1]['NZBprov']} "
                                    f"priority {match[3]}")
                                matches.append(match)

                    if CONFIG.use_rss():
                        match = find_best_result(rss_resultlist, book, searchtype, 'rss')
                        if not good_enough(match):
                            logger.info(
                                f"RSS title search for {book['library']} {book['searchterm']} returned no results.")
                        else:
                            logger.info(
                                f"Found RSS result: {searchtype} {round(match[0], 2)}%, {match[1]['NZBprov']} "
                                f"priority {match[3]}")
                            matches.append(match)

            if matches:
                try:
                    highest = max(matches, key=lambda s: (s[0], s[3]))  # sort on percentage and priority
                except TypeError:
                    highest = max(matches, key=lambda s: (str(s[0]), str(s[3])))

                logger.info(
                    f"Requesting {book['library']} download: {round(highest[0], 2)}% {highest[1]['NZBprov']}: "
                    f"{highest[1]['NZBtitle']}")
                if download_result(highest, book) > 1:
                    book_count += 1  # we found it
                db.action("DELETE from failedsearch WHERE BookID=? AND Library=?",
                          (book['bookid'], book['library']))
            elif CONFIG.get_bool('DELAYSEARCH') and not force and do_search and len(modelist):
                res = db.match('SELECT * FROM failedsearch WHERE BookID=? AND Library=?',
                               (book['bookid'], book['library']))
                if res:
                    interval = check_int(res['Interval'], 0)
                else:
                    interval = 0

                # haynes-ops fix 2: cap the back-off at 7 skipped runs (about weekly on a daily search).
                db.upsert("failedsearch",
                          {'Count': 0, 'Interval': min(interval + 1, 7), 'Time': time.time()},
                          {'BookID': book['bookid'], 'Library': book['library']})

            time.sleep(CONFIG.get_int('SEARCH_RATELIMIT'))

        logger.info(f"Search for Wanted items complete, found {book_count} {plural(book_count, 'book')}")

    except Exception:
        logger.error(f'Unhandled exception in search_book: {traceback.format_exc()}')
    finally:
        logger.debug(f"Storing finish time for {thread_name()}")
        db.upsert("jobs", {"Finish": time.time()}, {"Name": thread_name()})
        db.close()
        thread_name("WEBSERVER")
