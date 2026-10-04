# ---------------------------------------------------------------------------
# haynes-ops override (downloads/lazylibrarian, ConfigMap lazylibrarian-gb)
#
# This is LazyLibrarian's upstream lazylibrarian/gb.py, byte for byte, plus
# this comment block and ONE marked change (search for "haynes-ops fix").
#
# PINNED TO UPSTREAM: image docker.io/linuxserver/lazylibrarian:version-40a389ea
#   (LazyLibrarian commit 40a389ea, pyproject version 2026.05.25).
#   sha256 of the unmodified upstream file in that image:
#   9ab71fbd28b9600c6fa679ca9662674b82421be20c06ab23e9f024b5d635971f
#
# The fix: GoogleBooks.add_bookid_to_db() (what the API's addBook runs, and
# so every book haynesnetwork adds) never inserted the bookauthors row that
# get_author_books() writes. update_totals() counts an author's books through
# bookauthors, so an author LazyLibrarian only knew through added books
# counted zero, and dbupgrade.check_db() deleted it at the next start, taking
# every one of those books with it (books.AuthorID cascades). About 900
# haynesnetwork requests pointed at books deleted this way
# (thaynes43/haynesnetwork#665). dbupgrade.py carries the matching guard.
#
# BEFORE BUMPING THE IMAGE TAG in helmrelease.yaml: take the new image's
# gb.py, and either re-apply the marked change to it (if upstream has not
# fixed the bug) or drop this override (if it has). Never carry this file onto
# a different image unchanged: it would silently revert every other upstream
# change to the Google Books source.
# ---------------------------------------------------------------------------
#  This file is part of Lazylibrarian.
#  Lazylibrarian is free software, you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#  Lazylibrarian is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#  You should have received a copy of the GNU General Public License
#  along with Lazylibrarian.  If not, see <http://www.gnu.org/licenses/>.


# example
# https://www.googleapis.com/books/v1/volumes?q=+inauthor:george+martin+intitle:song+ice+fire

import logging
import time
import traceback
from urllib.parse import quote, quote_plus, urlencode

from rapidfuzz import fuzz

import lazylibrarian
from lazylibrarian import ROLE, database
from lazylibrarian.bookwork import (
    delete_empty_series,
    get_status,
    get_work_series,
    google_book_dict,
    is_set_or_part,
    isbnlang,
    set_series,
)
from lazylibrarian.cache import json_request
from lazylibrarian.config2 import CONFIG
from lazylibrarian.formatter import (
    clean_name,
    get_list,
    is_valid_isbn,
    make_utf8bytes,
    plural,
    replace_all,
    strip_quotes,
    thread_name,
    today,
    unaccented,
)
from lazylibrarian.hc import HardCover
from lazylibrarian.images import cache_bookimg, get_book_cover
from lazylibrarian.ol import OpenLibrary


class GoogleBooks:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.url = '/'.join([CONFIG['GB_URL'], 'books/v1/volumes?q='])
        self.params = {
            'maxResults': 40,
            'printType': 'books',
        }

        if CONFIG['GB_API']:
            self.params['key'] = CONFIG['GB_API']

    # noinspection PyBroadException
    def find_results(self, searchterm=None, queue=None):
        """ GoogleBooks performs much better if we search for author OR title
            not both at once, so if searchterm is not isbn, two searches needed.
            Lazylibrarian searches use <ll> to separate title from author in searchterm
            If this token isn't present, it's an isbn or searchterm as supplied by user
        """
        try:
            resultlist = []
            # See if we should check ISBN field, otherwise ignore it
            api_strings = ['inauthor:', 'intitle:']
            if is_valid_isbn(searchterm):
                api_strings = ['isbn:']

            api_hits = 0

            ignored = 0
            total_count = 0
            no_author_count = 0
            title = ''
            authorname = ''

            if '<ll>' in searchterm:  # special token separates title from author
                title, authorname = searchterm.split('<ll>')

            fullterm = searchterm.replace('<ll>', ' ')
            self.logger.debug(f'Now searching Google Books API with searchterm: {fullterm}')

            for api_value in api_strings:
                set_url = self.url
                if api_value == "isbn:":
                    set_url += quote(api_value + searchterm)
                elif api_value == 'intitle:':
                    searchterm = fullterm
                    if title and ' (' in title:  # just search for title
                        title = title.split(' (')[0]  # without any series info
                        searchterm = title
                    # strip all ascii and non-ascii quotes/apostrophes
                    searchterm = strip_quotes(searchterm)
                    set_url += quote(make_utf8bytes(f"{api_value}\"{searchterm}\"")[0])
                elif api_value == 'inauthor:':
                    searchterm = fullterm
                    if authorname:
                        searchterm = authorname  # just search for author
                    searchterm = searchterm.strip()
                    set_url += quote_plus(make_utf8bytes(f"{api_value}\"{searchterm}\"")[0])

                startindex = 0
                resultcount = 0
                ignored = 0
                number_results = 1
                total_count = 0
                no_author_count = 0
                try:
                    while startindex < number_results:

                        self.params['startIndex'] = startindex
                        url = f"{set_url}&{urlencode(self.params)}"

                        try:
                            jsonresults, in_cache = json_request(url)
                            if not jsonresults:
                                number_results = 0
                            else:
                                if not in_cache:
                                    api_hits += 1
                                number_results = jsonresults['totalItems']
                                self.logger.debug(f"Searching url: {url}")
                            if number_results == 0:
                                self.logger.warning(f'Found no results for {api_value} with value: {searchterm}')
                                break
                        except Exception as err:
                            errmsg = str(err)
                            self.logger.warning(
                                f'Google Books API Error [{errmsg}]: Check your API key or wait a while')
                            break

                        startindex += 40

                        for item in jsonresults['items']:
                            total_count += 1

                            book = google_book_dict(item)
                            if not book['author']:
                                self.logger.debug('Skipped a result without authorfield.')
                                no_author_count += 1
                                continue

                            if not book['name']:
                                self.logger.debug('Skipped a result without title.')
                                continue

                            valid_langs = get_list(CONFIG['IMP_PREFLANG'])
                            if "All" not in valid_langs:  # don't care about languages, accept all
                                try:
                                    # skip if language is not in valid list -
                                    booklang = book['lang']
                                    if not booklang:
                                        booklang = 'Unknown'
                                    if booklang not in valid_langs:
                                        self.logger.debug(
                                            f"Skipped {book['name']} with language {booklang}")
                                        ignored += 1
                                        continue
                                except KeyError:
                                    ignored += 1
                                    self.logger.debug(f"Skipped {book['name']} where no language is found")
                                    continue

                            if authorname:
                                author_fuzz = fuzz.token_sort_ratio(book['author'], authorname)
                            else:
                                author_fuzz = fuzz.token_sort_ratio(book['author'], fullterm)

                            if title:
                                if title.endswith(')'):
                                    title = title.rsplit('(', 1)[0]
                                book_fuzz = fuzz.token_set_ratio(book['name'].lower(), title.lower())
                                # lose a point for each extra word in the fuzzy matches so we get the closest match
                                words = len(get_list(book['name']))
                                words -= len(get_list(title))
                                book_fuzz -= abs(words)
                            else:
                                book_fuzz = fuzz.token_set_ratio(book['name'].lower(), fullterm.lower())

                            isbn_fuzz = 0
                            if is_valid_isbn(fullterm):
                                isbn_fuzz = 100

                            highest_fuzz = max((author_fuzz + book_fuzz) / 2, isbn_fuzz)

                            dic = {':': '.', '"': '', '\'': ''}
                            bookname = replace_all(book['name'], dic)

                            bookname = unaccented(bookname, only_ascii=False)

                            author_id = ''
                            if book['author']:
                                db = database.DBConnection()
                                match = db.match('SELECT AuthorID FROM authors WHERE AuthorName=?', (book['author'],))
                                if match:
                                    author_id = match['AuthorID']
                                db.close()

                            resultlist.append({
                                'authorname': book['author'],
                                'authorid': author_id,
                                'bookid': item['id'],
                                'bookname': bookname,
                                'booksub': book['sub'],
                                'bookisbn': book['isbn'],
                                'bookpub': book['pub'],
                                'bookdate': book['date'],
                                'booklang': book['lang'],
                                'booklink': book['link'],
                                'bookrate': float(book['rate']),
                                'bookrate_count': book['rate_count'],
                                'bookimg': book['img'],
                                'bookpages': book['pages'],
                                'bookgenre': book['genre'],
                                'bookdesc': book['desc'],
                                'author_fuzz': author_fuzz,
                                'book_fuzz': book_fuzz,
                                'isbn_fuzz': isbn_fuzz,
                                'highest_fuzz': highest_fuzz,
                                'source': 'GoogleBooks'
                            })

                            resultcount += 1

                except KeyError:
                    break

                self.logger.debug(
                    f"Returning {resultcount} {plural(resultcount, 'result')} for ({api_value}) "
                    f"with keyword: {searchterm}")

            self.logger.debug(f"Found {total_count} {plural(total_count, 'result')}")
            self.logger.debug(f"Removed {ignored} unwanted language {plural(ignored, 'result')}")
            self.logger.debug(f"Removed {no_author_count} {plural(no_author_count, 'book')} with no author")
            self.logger.debug(
                f"The Google Books API was hit {api_hits} {plural(api_hits, 'time')} for searchterm: {fullterm}")
            queue.put(resultlist)

        except Exception:
            self.logger.error(f'Unhandled exception in GB.find_results: {traceback.format_exc()}')

    def get_author_books(self, authorid=None, authorname=None, bookstatus="Skipped",
                         audiostatus="Skipped", entrystatus='Active', refresh=False, reason='gb.get_author_books'):
        # noinspection PyBroadException
        self.logger.debug(f'[{authorname}] Now processing books with Google Books API')
        db = database.DBConnection()
        try:
            # google doesn't like accents in author names
            set_url = self.url + quote(f'inauthor:"{unaccented(authorname, only_ascii=False)}"')
            entryreason = reason
            api_hits = 0
            gr_lang_hits = 0
            lt_lang_hits = 0
            gb_lang_change = 0
            cache_hits = 0
            not_cached = 0
            startindex = 0
            removed_results = 0
            duplicates = 0
            bad_lang = 0
            added_count = 0
            updated_count = 0
            locked_count = 0
            book_ignore_count = 0
            total_count = 0
            number_results = 1

            valid_langs = get_list(CONFIG['IMP_PREFLANG'])
            # Artist is loading
            db.action("UPDATE authors SET Status='Loading' WHERE AuthorID=?", (authorid,))

            try:
                threadname = thread_name()
                while startindex < number_results:
                    if lazylibrarian.STOPTHREADS and threadname == "AUTHORUPDATE":
                        self.logger.debug(f"Aborting {threadname}")
                        break
                    self.params['startIndex'] = startindex
                    url = f"{set_url}&{urlencode(self.params)}"

                    try:
                        jsonresults, in_cache = json_request(url, use_cache=not refresh)
                        if not jsonresults:
                            number_results = 0
                        else:
                            if not in_cache:
                                api_hits += 1
                            number_results = jsonresults['totalItems']
                    except Exception as err:
                        errmsg = str(err)
                        self.logger.warning(f'Google Books API Error [{errmsg}]: Check your API key or wait a while')
                        break

                    if number_results == 0:
                        self.logger.warning(f'Found no results for {authorname}')
                        break
                    self.logger.debug(f"Found {number_results} {plural(number_results, 'result')} for {authorname}")

                    startindex += 40

                    for item in jsonresults['items']:
                        if lazylibrarian.STOPTHREADS and threadname == "AUTHORUPDATE":
                            self.logger.debug(f"Aborting {threadname}")
                            break
                        total_count += 1
                        book = google_book_dict(item)
                        # skip if no author, no author is no book.
                        if not book['author']:
                            self.logger.debug('Skipped a result without authorfield.')
                            continue

                        booklang = book['lang']
                        # do we care about language?
                        if "All" not in valid_langs and book['isbn'] and booklang == "Unknown" or booklang == "en":
                            # it seems google lies to us, sometimes tells us books are in english when they are not
                            googlelang = booklang
                            match = False
                            if book['isbn']:
                                booklang, cache_hit, thing_hit = isbnlang(book['isbn'])
                                if thing_hit:
                                    lt_lang_hits += 1
                                if booklang:
                                    match = True
                            if match:
                                # We found a better language match
                                if googlelang == "en" and booklang not in ["en-US", "en-GB", "eng"]:
                                    # these are all english, may need to expand this list
                                    self.logger.debug(
                                        f"{book['name']} Google thinks [{googlelang}], we think [{booklang}]")
                                    gb_lang_change += 1
                            else:  # No match anywhere, accept google language
                                booklang = googlelang

                        ignorable = ['future', 'date', 'isbn', 'set', 'word', 'publisher']
                        if CONFIG.get_bool('NO_LANG'):
                            ignorable.append('lang')
                        rejected = []
                        bookname = book['name']
                        bookid = item['id']
                        if not bookname:
                            self.logger.debug(f'Rejecting bookid {bookid} for {authorname}, no bookname')
                            rejected.append(['name', 'No bookname'])
                        else:
                            bookname = replace_all(bookname, {':': ' ', '"': '', '\'': ''}).strip()

                        if not booklang:
                            booklang = 'Unknown'
                        if booklang not in valid_langs and 'All' not in valid_langs:
                            rejected.append(['lang', f'Invalid language [{booklang}]'])
                            bad_lang += 1

                        if CONFIG.get_bool('NO_FUTURE') and book['date'] > today()[:len(book['date'])]:
                            # googlebooks sometimes gives yyyy, sometimes yyyy-mm, sometimes yyyy-mm-dd
                            rejected.append(['future', f"Future publication date [{book['date']}]"])

                        if CONFIG.get_bool('NO_PUBDATE') and not book['date']:
                            rejected.append(['date', 'No publication date'])

                        if CONFIG.get_bool('NO_ISBN') and not book['isbn']:
                            rejected.append(['isbn', 'No ISBN'])

                        dic = {'.': ' ', '-': ' ', '/': ' ', '+': ' ', '_': ' ', '(': '', ')': '',
                               '[': ' ', ']': ' ', '#': '# ', ':': ' ', ';': ' '}
                        name = replace_all(bookname, dic).strip()
                        name = name.lower()
                        # remove extra spaces if they're in a row
                        name = " ".join(name.split())
                        namewords = name.split(' ')
                        badwords = get_list(CONFIG['REJECT_WORDS'], ',')

                        for word in badwords:
                            if (' ' in word and word in name) or word in namewords:
                                rejected.append(['word', f'Name contains [{word}]'])
                                break

                        cmd = ("SELECT BookID,gb_id FROM books,authors WHERE books.AuthorID = authors.AuthorID and "
                               "BookName=? COLLATE NOCASE and AuthorName=? COLLATE NOCASE and "
                               "books.Status != 'Ignored' and AudioStatus != 'Ignored'")
                        match = db.match(cmd, (bookname, authorname))
                        if not match:
                            in_db = lazylibrarian.librarysync.find_book_in_db(authorname, bookname, source='gb_id',
                                                                              ignored=False, library='eBook',
                                                                              reason='gb_get_author_books')
                            if in_db and in_db[0]:
                                cmd = "SELECT BookID,gb_id FROM books WHERE BookID=?"
                                match = db.match(cmd, (in_db[0],))
                        if match and match['BookID'] != bookid:
                            # we have a different book with this author/title already
                            self.logger.debug(f'Rejecting bookid {bookid} for [{authorname}][{bookname}]'
                                              f' already got {match["BookID"]}')
                            rejected.append(['dupe', f'Got under different bookid {bookid}'])
                            if not match['gb_id']:
                                db.action("UPDATE books SET gb_id=? WHERE BookID=?", (bookid, match['BookID']))

                        cmd = ("SELECT AuthorName,BookName,AudioStatus,books.Status,ScanResult,gb_id,BookID "
                               "FROM books,authors WHERE authors.AuthorID = books.AuthorID AND BookID=?")
                        match = db.match(cmd, (bookid,))
                        if match:  # we have a book with this bookid already
                            if bookname != match['BookName'] or authorname != match['AuthorName']:
                                self.logger.debug(
                                    f"Rejecting bookid {bookid} for [{authorname}][{bookname}] already got "
                                    f"bookid for [{match['AuthorName']}][{match['BookName']}]")
                                if not match['gb_id']:
                                    db.action("UPDATE books SET gb_id=? WHERE BookID=?",
                                              (bookid, match['BookID']))
                                rejected.append(['dupe', 'Already got this bookid in database'])
                            else:
                                msg = (f"Bookid {bookid} for [{authorname}][{bookname}] is in database "
                                       f"marked {match['Status']}")
                                if CONFIG.get_bool('AUDIO_TAB'):
                                    msg += f",{match['AudioStatus']}"
                                msg += f" {match['ScanResult']}"
                                self.logger.debug(msg)

                            # Make sure we don't reject books we have got
                            if match['Status'] in ['Open', 'Have'] or match['AudioStatus'] in ['Open', 'Have']:
                                rejected = []

                        fatal = False
                        reason = ''
                        if rejected:
                            for reject in rejected:
                                if reject[0] not in ignorable:
                                    if reject[0] == 'lang':
                                        bad_lang += 1
                                    if reject[0] == 'dupe':
                                        duplicates += 1
                                    if reject[0] == 'name':
                                        removed_results += 1
                                    fatal = True
                                    reason = reject[1]
                                    break

                            if not CONFIG['IMP_IGNORE']:
                                reason = str(rejected)
                                fatal = True

                            if not fatal:
                                for reject in rejected:
                                    if reject[0] in ignorable:
                                        book_ignore_count += 1
                                        reason = f"Ignored: {reject[1]}"
                                        break

                        elif 'author_update' in entryreason:
                            reason += f" Author: {authorname}"
                        else:
                            reason = entryreason

                        if fatal:
                            self.logger.debug(f"Rejected {bookid} {reason}")
                        else:
                            cmd = ("SELECT Status,AudioStatus,BookFile,AudioFile,Manual,BookAdded,BookName,ScanResult "
                                   "FROM books WHERE BookID=?")
                            existing = db.match(cmd, (bookid,))
                            if existing:
                                if reason.startswith('Ignored'):
                                    book_status = 'Ignored'
                                    audio_status = 'Ignored'
                                else:
                                    book_status = existing['Status']
                                    audio_status = existing['AudioStatus']
                                if CONFIG['FOUND_STATUS'] == 'Open':
                                    if book_status == 'Have' and existing['BookFile']:
                                        book_status = 'Open'
                                    if audio_status == 'Have' and existing['AudioFile']:
                                        audio_status = 'Open'
                                locked = existing['Manual']
                                added = existing['BookAdded']
                                if locked is None:
                                    locked = False
                                elif locked.isdigit():
                                    locked = bool(int(locked))
                            else:
                                book_status = bookstatus  # new_book status, or new_author status
                                audio_status = audiostatus
                                added = today()
                                locked = False

                            fatal = False
                            reason = ''
                            ignore_book = False
                            ignore_audio = False
                            if rejected:
                                for reject in rejected:
                                    if reject[0] not in ignorable:
                                        if reject[0] == 'lang':
                                            bad_lang += 1
                                        if reject[0] == 'dupe':
                                            duplicates += 1
                                        if reject[0] == 'name':
                                            removed_results += 1
                                        fatal = True
                                        reason = f"Ignored: {reject[1]}"
                                        break

                                if not CONFIG['IMP_IGNORE']:
                                    fatal = True

                            if not fatal:
                                for reject in rejected:
                                    if reject[0] in ignorable:
                                        ignore_book = True
                                        ignore_audio = True
                                        book_ignore_count += 1
                                        reason = f"Ignored: {reject[1]}"
                                        break
                            if not reason:
                                if 'author_update' in entryreason:
                                    reason = f'Author: {authorname}'
                                else:
                                    reason = entryreason

                            if locked:
                                locked_count += 1
                            else:
                                if ignore_book:
                                    book_status = 'Ignored'
                                if ignore_audio:
                                    audio_status = 'Ignored'
                                reason = f"[{thread_name()}] {reason}"
                                control_value_dict = {"BookID": bookid}
                                new_value_dict = {
                                    "AuthorID": authorid,
                                    "BookName": bookname,
                                    "BookSub": book['sub'],
                                    "BookDesc": book['desc'],
                                    "BookIsbn": book['isbn'],
                                    "BookPub": book['pub'],
                                    "BookGenre": book['genre'],
                                    "BookImg": book['img'],
                                    "BookLink": book['link'],
                                    "BookRate": float(book['rate']),
                                    "BookPages": book['pages'],
                                    "BookDate": book['date'],
                                    "BookLang": booklang,
                                    "Status": book_status,
                                    "AudioStatus": audio_status,
                                    "BookAdded": added,
                                    "WorkID": '',
                                    "ScanResult": reason,
                                    "gb_id": bookid
                                }

                                if 'nocover' in book['img'] or 'nophoto' in book['img']:
                                    # try to get a cover from another source
                                    link, _ = get_book_cover(bookid, ignore='googleapis')
                                    if link:
                                        new_value_dict["BookImg"] = link
                                    elif book['img'] and book['img'].startswith('http'):
                                        link = cache_bookimg(book['img'], bookid, 'gb')
                                        new_value_dict["BookImg"] = link

                                db.upsert("books", new_value_dict, control_value_dict)
                                self.logger.debug(f"Book found: {bookname} {book['date']}")

                                db.action('INSERT into bookauthors (AuthorID, BookID, Role) VALUES (?, ?, ?)',
                                          (authorid, bookid, ROLE['PRIMARY']), suppress='UNIQUE')

                                if CONFIG.get_bool('CONTRIBUTING_AUTHORS') and 'contributors' in book:
                                    for entry in book['contributors']:
                                        reason = f"Contributor to {bookname}"
                                        auth_id = lazylibrarian.importer.add_author_to_db(authorname=entry,
                                                                                          refresh=False,
                                                                                          authorid='',
                                                                                          addbooks=False,
                                                                                          reason=reason)
                                        if auth_id:
                                            db.action('INSERT into bookauthors (AuthorID, BookID, Role)'
                                                      ' VALUES (?, ?, ?)',
                                                      (auth_id, bookid, ROLE['CONTRIBUTING']), suppress='UNIQUE')

                                serieslist = []
                                if book['series']:
                                    serieslist = [('', book['seriesNum'], clean_name(book['series'], '&/'))]
                                if CONFIG.get_bool('ADD_SERIES') and "Ignored:" not in reason:
                                    newserieslist = get_work_series(bookid, 'LT', reason=reason)
                                    if newserieslist:
                                        serieslist = newserieslist
                                        self.logger.debug(f'Updated series: {bookid} [{serieslist}]')
                                    set_series(serieslist, bookid, reason=reason)

                                update_value_dict = {}
                                control_value_dict = {"BookID": bookid}
                                if existing and (existing['ScanResult'] and
                                                 'publication date' in existing['ScanResult'] and
                                                 book['date'] and book['date'] != '0000' and
                                                 book['date'] <= today()[:len(book['date'])]):
                                    # was rejected on previous scan but bookdate is now valid
                                    book_stat, audio_stat = get_status(bookid, serieslist, bookstatus, audiostatus,
                                                                       entrystatus)

                                    if existing['Status'] not in ['Wanted', 'Open', 'Have'] and not ignore_book:
                                        update_value_dict["Status"] = book_stat
                                    if existing['AudioStatus'] not in ['Wanted', 'Open', 'Have'] and not ignore_audio:
                                        update_value_dict["AudioStatus"] = audio_stat
                                    self.logger.debug(
                                        f"valid bookdate [{book['date']}] previous scanresult "
                                        f"[{existing['ScanResult']}]")
                                    update_value_dict["ScanResult"] = f"bookdate {book['date']} is now valid"

                                if update_value_dict:
                                    db.upsert("books", update_value_dict, control_value_dict)

                                if not existing:
                                    typ = 'Added'
                                    added_count += 1
                                else:
                                    typ = 'Updated'
                                    updated_count += 1
                                msg = f"[{authorname}] {typ} book: {bookname} [{booklang}] status {book_status}"
                                if CONFIG.get_bool('AUDIO_TAB'):
                                    msg += f" audio {audio_status}"
                                self.logger.debug(msg)
            except KeyError:
                pass

            delete_empty_series()
            lazylibrarian.importer.update_totals(authorid)
            self.logger.debug(
                f"[{authorname}] The Google Books API was hit {api_hits} {plural(api_hits, 'time')}"
                f" to populate book list")
            cmd = ("SELECT BookName, BookLink, BookDate, BookImg, BookID from books WHERE AuthorID=? AND "
                   "Status != 'Ignored' order by BookDate DESC")
            lastbook = db.match(cmd, (authorid,))

            if lastbook:  # maybe there are no books [remaining] for this author
                lastbookname = lastbook['BookName']
                lastbooklink = lastbook['BookLink']
                lastbookdate = lastbook['BookDate']
                lastbookid = lastbook['BookID']
                lastbookimg = lastbook['BookImg']
            else:
                lastbookname = ""
                lastbooklink = ""
                lastbookdate = ""
                lastbookid = ""
                lastbookimg = ""

            control_value_dict = {"AuthorID": authorid}
            new_value_dict = {
                "Status": entrystatus,
                "LastBook": lastbookname,
                "LastLink": lastbooklink,
                "LastDate": lastbookdate,
                "LastBookID": lastbookid,
                "LastBookImg": lastbookimg
            }

            db.upsert("authors", new_value_dict, control_value_dict)
            resultcount = added_count + updated_count
            self.logger.debug(f"Found {total_count} total {plural(total_count, 'book')} for author")
            self.logger.debug(f"Found {locked_count} locked {plural(locked_count, 'book')}")
            self.logger.debug(f"Removed {bad_lang} unwanted language {plural(bad_lang, 'result')}")
            self.logger.debug(f"Removed {removed_results} incorrect/incomplete {plural(removed_results, 'result')}")
            self.logger.debug(f"Removed {duplicates} duplicate {plural(duplicates, 'result')}")
            self.logger.debug(f"Ignored {book_ignore_count} {plural(book_ignore_count, 'book')}")
            self.logger.debug(f"Imported/Updated {resultcount} {plural(resultcount, 'book')} for {authorname}")

            db.action('insert into stats values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                      (authorname, api_hits, gr_lang_hits, lt_lang_hits, gb_lang_change,
                       cache_hits, bad_lang, removed_results, not_cached, duplicates))

            if refresh:
                self.logger.info(
                    f"[{authorname}] Book processing complete: Added {added_count} "
                    f"{plural(added_count, 'book')} / Updated {updated_count} "
                    f"{plural(updated_count, 'book')}")
            else:
                self.logger.info(
                    f"[{authorname}] Book processing complete: Added {added_count} "
                    f"{plural(added_count, 'book')} to the database")

        except Exception:
            self.logger.error(f'Unhandled exception in GB.get_author_books: {traceback.format_exc()}')
        finally:
            db.close()

    def add_bookid_to_db(self, bookid=None, bookstatus=None, audiostatus=None, reason='gb.add_bookid'):
        if not CONFIG['GB_API']:
            self.logger.warning('No GoogleBooks API key, check config')
            return False
        url = '/'.join([CONFIG['GB_URL'], f"books/v1/volumes/{str(bookid)}?key={CONFIG['GB_API']}"])
        jsonresults, _ = json_request(url)

        if not jsonresults:
            self.logger.debug(f'No results found for {bookid}')
            return False

        if not bookstatus:
            bookstatus = CONFIG['NEWBOOK_STATUS']
        if not audiostatus:
            audiostatus = CONFIG['NEWAUDIO_STATUS']

        book = google_book_dict(jsonresults)
        # TODO multiauth details here
        dic = {':': '.', '"': ''}
        bookname = replace_all(book['name'], dic).strip()

        if not book['author']:
            self.logger.debug(f'Book {bookname} does not contain author field, skipping')
            return False
        # warn if language is in ignore list, but user said they wanted this book
        valid_langs = get_list(CONFIG['IMP_PREFLANG'])
        if book['lang'] not in valid_langs and 'All' not in valid_langs:
            msg = f"Book {bookname} googlebooks language does not match preference, {book['lang']}"
            self.logger.warning(msg)
            if reason.startswith("Series:"):
                return False

        if CONFIG.get_bool('NO_PUBDATE') and (not book['date'] or book['date'] == '0000'):
            msg = f"Book {bookname} Publication date does not match preference, {book['date']}"
            self.logger.warning(msg)
            if reason.startswith("Series:"):
                return False

        if CONFIG.get_bool('NO_FUTURE') and book['date'] > today()[:4]:
            msg = f"Book {bookname} Future publication date does not match preference, {book['date']}"
            self.logger.warning(msg)
            if reason.startswith("Series:"):
                return False

        if CONFIG.get_bool('NO_SETS'):
            is_set, set_msg = is_set_or_part(bookname)
            if is_set:
                msg = f"Book {bookname} {set_msg}"
                self.logger.warning(msg)
                if reason.startswith("Series:"):
                    return False

        db = database.DBConnection()
        try:
            authorname = book['author']
            if CONFIG['BOOK_API'] == "HardCover":
                hc = HardCover()
                author = hc.find_author_id(authorname=authorname, title=bookname)
            else:
                ol = OpenLibrary()
                author = ol.find_author_id(authorname=authorname, title=bookname)
            if author:
                author_id = author['authorid']
                match = db.match('SELECT AuthorID from authors WHERE AuthorID=?', (author_id,))
                if not match:
                    match = db.match('SELECT AuthorID from authors WHERE AuthorName=?', (author['authorname'],))
                    if match:
                        self.logger.debug(
                            f"{author['authorname']}: Changing authorid from {author_id} to {match['AuthorID']}")
                        author_id = match['AuthorID']  # we have a different authorid for that authorname
                    else:  # no author but request to add book, add author with newauthor status
                        # User hit "add book" button from a search or a wishlist import
                        newauthor_status = 'Active'
                        if CONFIG['NEWAUTHOR_STATUS'] in ['Skipped', 'Ignored']:
                            newauthor_status = 'Paused'
                        # also set paused if adding author as a series contributor
                        if reason.startswith('Series:'):
                            newauthor_status = 'Paused'
                        control_value_dict = {"AuthorID": author_id}
                        new_value_dict = {
                            "AuthorName": author['authorname'],
                            "AuthorImg": author['authorimg'],
                            "AuthorLink": author['authorlink'],
                            "AuthorBorn": author['authorborn'],
                            "AuthorDeath": author['authordeath'],
                            "DateAdded": today(),
                            "Updated": int(time.time()),
                            "Status": newauthor_status,
                            "Reason": reason
                        }
                        if CONFIG['BOOK_API'] == "HardCover":
                            new_value_dict['hc_id'] = author_id
                        else:
                            new_value_dict['ol_id'] = author_id
                        authorname = author['authorname']
                        db.upsert("authors", new_value_dict, control_value_dict)
                        # if CONFIG.get_bool('NEWAUTHOR_BOOKS') and newauthor_status != 'Paused':
                        #    self.get_author_books(author_id, entrystatus=CONFIG['NEWAUTHOR_STATUS'],
                        #                          reason=reason)
            else:
                self.logger.warning(f"No AuthorID for {book['author']}, unable to add book {bookname}")
                return False

            reason = f"[{thread_name()}] {reason}"
            control_value_dict = {"BookID": bookid}
            new_value_dict = {
                "AuthorID": author_id,
                "BookName": bookname,
                "BookSub": book['sub'],
                "BookDesc": book['desc'],
                "BookIsbn": book['isbn'],
                "BookPub": book['pub'],
                "BookGenre": book['genre'],
                "BookImg": book['img'],
                "BookLink": book['link'],
                "BookRate": float(book['rate']),
                "BookPages": book['pages'],
                "BookDate": book['date'],
                "BookLang": book['lang'],
                "Status": bookstatus,
                "AudioStatus": audiostatus,
                "ScanResult": reason,
                "BookAdded": today(),
                "gb_id": bookid
            }
            db.upsert("books", new_value_dict, control_value_dict)
            # haynes-ops fix: link the book to its author, as get_author_books does. Without this row
            # update_totals counts the author's books as zero and check_db deletes the author (and, by
            # ON DELETE CASCADE, every book added this way) at the next start (thaynes43/haynesnetwork#665).
            db.action('INSERT into bookauthors (AuthorID, BookID, Role) VALUES (?, ?, ?)',
                      (author_id, bookid, ROLE['PRIMARY']), suppress='UNIQUE')

            if 'nocover' in book['img'] or 'nophoto' in book['img']:
                # try to get a cover from another source
                link, _ = get_book_cover(bookid, ignore='googleapis')
                if link:
                    new_value_dict = {"BookImg": link}
                    db.upsert("books", new_value_dict, control_value_dict)
                elif book['img'] and book['img'].startswith('http'):
                    link = cache_bookimg(book['img'], bookid, 'gb')
                    new_value_dict = {"BookImg": link}
                    db.upsert("books", new_value_dict, control_value_dict)

            self.logger.info(f"{bookname} by {authorname} added to the books database, {bookstatus}/{audiostatus}")
            serieslist = []
            if book['series']:
                serieslist = [('', book['seriesNum'], clean_name(book['series'], '&/'))]
            if CONFIG.get_bool('ADD_SERIES') and "Ignored:" not in reason:
                newserieslist = get_work_series(bookid, 'LT', reason=reason)
                if newserieslist:
                    serieslist = newserieslist
                    self.logger.debug(f'Updated series: {bookid} [{serieslist}]')
                set_series(serieslist, bookid, reason=reason)
            return True
        finally:
            db.close()
