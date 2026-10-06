# ---------------------------------------------------------------------------
# haynes-ops override (downloads/lazylibrarian, ConfigMap lazylibrarian-dbupgrade)
#
# This is LazyLibrarian's upstream lazylibrarian/dbupgrade.py, byte for byte,
# plus this comment block and two marked changes (search for "haynes-ops fix").
#
# PINNED TO UPSTREAM: image docker.io/linuxserver/lazylibrarian:version-40a389ea
#   (LazyLibrarian commit 40a389ea, pyproject version 2026.05.25).
#   sha256 of the unmodified upstream file in that image:
#   584d33016b00721bfb2ee8021da757d13c5059fa0dc7766860f90052457a7756
#
# The fix: check_db() runs at every start and deletes each author whose
# update_totals() recount is zero ("Removing N authors with no listed books").
# update_totals() counts through bookauthors, which the API's addBook never
# wrote (gb.py carries that fix), so authors that DO own book rows were
# deleted, and books.AuthorID ON DELETE CASCADE deleted their books: 25
# authors at the 2026-10-03 restart, about 900 haynesnetwork requests' books
# since July (thaynes43/haynesnetwork#665). The delete now spares any author
# that still owns a row in books.
#
# Fix 2, a census (thaynes43/haynesnetwork#736): every start counts the books
# with no bookauthors row, which count for no author, and logs
# "LL_UNLINKED_BOOKS <n>" as a WARNING when there are any (the Loki rule in
# lokirule.yaml alerts on it). On 2026-10-06 the 211 left from before the gb.py
# fix were given their primary row (Role 1, as gb.py and LazyLibrarian's own
# v88 upgrade write it), so the census reads 0 and no book depends on fix 1.
#
# BEFORE BUMPING THE IMAGE TAG in helmrelease.yaml: take the new image's
# dbupgrade.py, and either re-apply the marked change to it (if upstream has
# not fixed the bug) or drop this override (if it has). Never carry this file
# onto a different image unchanged: it would silently revert every other
# upstream change to the database upgrade and check.
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


import datetime
import logging
import os
import string
import time
import traceback
import uuid
from shutil import copyfile

import lazylibrarian
from lazylibrarian import database
from lazylibrarian.bookwork import set_genres
from lazylibrarian.common import path_exists, pwd_generator
from lazylibrarian.config2 import CONFIG
from lazylibrarian.filesystem import DIRS, setperm, splitext, syspath
from lazylibrarian.formatter import check_int, get_list, md5_utf8, plural
from lazylibrarian.importer import update_totals
from lazylibrarian.scheduling import SchedulerCommand, restart_jobs

# database version history:
# 0 original version or new empty database
# 1 changes up to June 2016
# 2 removed " MB" from nzbsize field in wanted table
# 3 removed SeriesOrder column from books table as redundant
# 4 added duplicates column to stats table
# 5 issue numbers padded to 4 digits with leading zeros
# 6 added Manual field to books table for user editing
# 7 added Source and DownloadID to wanted table for download monitoring
# 8 move image cache from data/images/cache into datadir
# 9 add regex to magazine table
# 10 check for missing columns in pastissues table
# 11 Keep most recent book image in author table
# 12 Keep latest issue cover in magazine table
# 13 add Manual column to author table for user editing
# 14 separate book and author images in case id numbers collide
# 15 move series and seriesnum into separate tables so book can appear in multiple series
# 16 remove series, authorlink, authorname columns from book table, only in series/author tables now
# 17 remove authorid from series table, new seriesauthor table to allow multiple authors per series
# 18 Added unique constraint to seriesauthors table
# 19 add seriesdisplay to book table
# 20 add booklibrary date to book table
# 21 add audiofile audiolibrary date and audiostatus to books table
# 22 add goodreads "follow" to author table
# 23 add user accounts
# 24 add HaveRead and ToRead to user accounts
# 25 add index for magazine issues (title) for new dbchanges
# 26 create Sync table
# 27 add indexes for book/author/wanted status
# 28 add CalibreRead and CalibreToRead columns to user table
# 29 add goodreads workid to books table
# 30 add BookType to users table
# 31 add DateType to magazines table
# 32 add counters to series table
# 33 add DLResult to wanted table
# 34 add ScanResult to books table, and new isbn table
# 35 add OriginalPubDate to books table
# 36 create failedsearch table
# 37 add delete cascade to tables
# 38 change series count and total to integers
# 39 add LastBookID to author table
# 40 add CoverPage to magazines table
# 41 add Requester and AudioRequester to books table
# 42 add SendTo to users table
# 43 remove foreign key constraint on wanted table
# 44 move hosting to gitlab
# ---------- Removed support for upgrades earlier than this ----------------
# ----------   v45+ lets us migrate last dobytang version   ------------
# 45 update local git repo to new origin
# 46 remove pastissues table and rebuild to ensure no foreign key
# 47 genres and genrebooks tables
# 48 ensure magazine table schema is current
# 49 ensure author table schema is current
# 50 add comics and comicissues tables
# 51 add aka to comics table
# 52 add updated to series table
# 53 add jobs table
# 54 separated author added date from updated timestamp
# 55 Add Reason to author table
# 56 Add Cover column to comicissues and issues tables
# 57 Add Description to comics and Description/Contributors to comicissues
# 58 Ensure Link was added to comicissues (was missing for new installs in v57)
# 59 Added per provider seeders instead of global
# 60 Moved preprocessor into main program and disabled old preprocessor
# --------- Database upgrades from this point are checked at every startup ------------
# 61 Add reason to series table
# 62 Add About to author table
# 63 Add Start to jobs table, rename LastRun to Finish
# 64 Add Added time to pastissues table
# 65 Add Reading and Abandoned to users table
# 66 Add subscribers table
# 67 Add prefs to user table
# 68 Add completed time to wanted table
# 69 Add LT_WorkID to books table and AKA to authors
# 70 Add gr_id to books and authors tables
# 71 Add narrator to books table
# 72 Add separate HaveEBooks and HaveAudioBooks to authors table
# 73 Add gr_id to series table
# 74 Add Theme to users table
# 75 Add ol_id to author table
# 76 Add Label to wanted table
# 77 Add Genres to magazines and comics
# 78 Add last_login and login_count to users, sent_file table
# 79 Add Source to series table
# 80 Add Unauthorised table
# 81 Add ol_id and gb_id to books table
# 82 Add reading list tables, remove from users table
# 83 Add hc_id to author and book tables
# 84 merge into readinglists table, remove individual tables
# 85 change seriesid to include source, to avoid collisions
# 86 add language to magazine table
# 87 add hc_token to users table
# 88 add bookauthors table
# 89 add dnb_id to book table

db_current_version = 89


def upgrade_needed():
    """
    Check if database needs upgrading
    Return zero if up-to-date
    Return current version if LazyLibrarian needs upgrade
    """

    db = database.DBConnection()
    # Had a report of "index out of range", can't replicate it.
    # Maybe on some versions of sqlite an unset user_version
    # or unsupported pragma gives an empty result?

    try:
        if get_db_version(db) < db_current_version:
            return db_current_version
        return 0
    finally:
        db.close()


def get_db_version(db):
    """
    Returns the current DB version as a number
    """
    db_version = 0
    result = db.match('PRAGMA user_version')
    if result and result[0]:
        value = str(result[0])
        if value.isdigit():
            db_version = int(value)
    return db_version


def has_column(db, table, column):
    columns = db.select(f'PRAGMA table_info({table})')
    if not columns:  # no such table
        return False
    return any(item[1] == column for item in columns)


def db_upgrade(current_version: int, restartjobs: bool = False):
    logger = logging.getLogger(__name__)
    with open(syspath(DIRS.get_logfile('dbupgrade.log')), 'a') as upgradelog:
        db_changes = 0
        db = database.DBConnection()
        # noinspection PyBroadException
        try:
            db_version = get_db_version(db)
            check = db.match('PRAGMA integrity_check')
            if check and check[0]:
                result = check[0]
                if result == 'ok':
                    logger.debug(f'Database integrity check: {result}')
                else:
                    logger.error(f'Database integrity check: {result}')
                    # should probably abort now if result is not "ok"

            if db_version < current_version:
                if db_version:
                    lazylibrarian.UPDATE_MSG = (f'Updating database to version {current_version}, '
                                                f'current version is {db_version}')
                    logger.info(lazylibrarian.UPDATE_MSG)
                    upgradelog.write(f"{time.ctime()} v0: {lazylibrarian.UPDATE_MSG}\n")
                else:
                    # it's a new database. Create v60 tables and then upgrade as required
                    db_version = 60
                    lazylibrarian.UPDATE_MSG = f'Creating new database, version {db_current_version}'
                    upgradelog.write(f"{time.ctime()} v{db_version}: {lazylibrarian.UPDATE_MSG}\n")
                    logger.info(lazylibrarian.UPDATE_MSG)
                    # sanity check for incomplete initialisations
                    res = db.select("select name from sqlite_master where type is 'table'")
                    for item in res:
                        if not item['name'].startswith('sqlite_'):
                            db.action(f"DROP TABLE IF EXISTS {item['name']}")

                    # new v60 set of database tables
                    db.action('CREATE TABLE authors (AuthorID TEXT UNIQUE, AuthorName TEXT UNIQUE, ' +
                              'AuthorImg TEXT, AuthorLink TEXT, DateAdded TEXT, Status TEXT, LastBook TEXT, ' +
                              'LastBookImg TEXT, LastLink TEXT, LastDate TEXT, HaveBooks INTEGER DEFAULT 0, ' +
                              'TotalBooks INTEGER DEFAULT 0, AuthorBorn TEXT, AuthorDeath TEXT, ' +
                              'UnignoredBooks INTEGER DEFAULT 0, Manual TEXT, GRfollow TEXT, ' +
                              'LastBookID TEXT, Updated INTEGER DEFAULT 0, Reason TEXT, About TEXT, AKA TEXT)')
                    db.action(
                        "CREATE TABLE wanted (BookID TEXT, NZBurl TEXT, NZBtitle TEXT, NZBdate TEXT, NZBprov TEXT, "
                        "Status TEXT, NZBsize TEXT, AuxInfo TEXT, NZBmode TEXT, Source TEXT, DownloadID TEXT, "
                        "DLResult TEXT)")
                    db.action(
                        "CREATE TABLE magazines (Title TEXT UNIQUE, Regex TEXT, Status TEXT, MagazineAdded TEXT, "
                        "LastAcquired TEXT, IssueDate TEXT, IssueStatus TEXT, Reject TEXT, LatestCover TEXT, "
                        "DateType TEXT, CoverPage INTEGER DEFAULT 1)")
                    db.action('CREATE TABLE languages (isbn TEXT, lang TEXT)')
                    db.action(
                        "CREATE TABLE stats (authorname text, GR_book_hits int, GR_lang_hits int, LT_lang_hits int, "
                        "GB_lang_change, cache_hits int, bad_lang int, bad_char int, uncached int, duplicates int)")
                    db.action(
                        "CREATE TABLE series (SeriesID INTEGER UNIQUE, SeriesName TEXT, Status TEXT,"
                        " Have INTEGER DEFAULT 0, Total INTEGER DEFAULT 0, Updated INTEGER DEFAULT 0, Reason TEXT)")
                    db.action('CREATE TABLE downloads (Count INTEGER DEFAULT 0, Provider TEXT)')
                    db.action(
                        "CREATE TABLE users (UserID TEXT UNIQUE, UserName TEXT UNIQUE, Password TEXT, Email TEXT, "
                        "Name TEXT, Perms INTEGER DEFAULT 0, HaveRead TEXT, ToRead TEXT, CalibreRead TEXT, "
                        "CalibreToRead TEXT, BookType TEXT, SendTo TEXT, Last_Login TEXT, "
                        "Login_Count INTEGER DEFAULT 0)")
                    db.action('CREATE TABLE isbn (Words TEXT, ISBN TEXT)')
                    db.action("CREATE TABLE genres (GenreID INTEGER PRIMARY KEY AUTOINCREMENT, GenreName TEXT UNIQUE)")
                    db.action('CREATE TABLE comics (ComicID TEXT UNIQUE, Title TEXT, Status TEXT, ' +
                              'Added TEXT, LastAcquired TEXT, Updated TEXT, LatestIssue TEXT, IssueStatus TEXT, ' +
                              'LatestCover TEXT, SearchTerm TEXT, Start TEXT, First INTEGER, Last INTEGER, ' +
                              'Publisher TEXT, Link TEXT, aka TEXT, Description TEXT)')
                    db.action('CREATE TABLE jobs (Name TEXT, Finish INTEGER DEFAULT 0, Start INTEGER DEFAULT 0)')

                    db.action('CREATE TABLE books (AuthorID TEXT REFERENCES authors (AuthorID) ' +
                              'ON DELETE CASCADE, BookName TEXT, BookSub TEXT, BookDesc TEXT, ' +
                              'BookGenre TEXT, BookIsbn TEXT, BookPub TEXT, BookRate INTEGER DEFAULT 0, ' +
                              'BookImg TEXT, BookPages INTEGER DEFAULT 0, BookLink TEXT, BookID TEXT UNIQUE, ' +
                              'BookFile TEXT, BookDate TEXT, BookLang TEXT, BookAdded TEXT, Status TEXT, ' +
                              'WorkPage TEXT, Manual TEXT, SeriesDisplay TEXT, BookLibrary TEXT, ' +
                              'AudioFile TEXT, AudioLibrary TEXT, AudioStatus TEXT, WorkID TEXT, ' +
                              'ScanResult TEXT, OriginalPubDate TEXT, Requester TEXT, AudioRequester TEXT, ' +
                              'LT_WorkID TEXT, Narrator TEXT)')
                    db.action(
                        "CREATE TABLE issues (Title TEXT REFERENCES magazines (Title) ON DELETE CASCADE, "
                        "IssueID TEXT UNIQUE, IssueAcquired TEXT, IssueDate TEXT, IssueFile TEXT, Cover TEXT)")
                    db.action(
                        "CREATE TABLE member (SeriesID INTEGER REFERENCES series (SeriesID) ON DELETE CASCADE, "
                        "BookID TEXT REFERENCES books (BookID) ON DELETE CASCADE, WorkID TEXT, SeriesNum TEXT)")
                    db.action(
                        "CREATE TABLE seriesauthors (SeriesID INTEGER, AuthorID TEXT REFERENCES authors (AuthorID) "
                        "ON DELETE CASCADE, UNIQUE (SeriesID,AuthorID))")
                    db.action(
                        "CREATE TABLE sync (UserID TEXT REFERENCES users (UserID) ON DELETE CASCADE, Label TEXT, "
                        "Date TEXT, SyncList TEXT)")
                    db.action(
                        "CREATE TABLE failedsearch (BookID TEXT REFERENCES books (BookID) ON DELETE CASCADE, "
                        "Library TEXT, Time TEXT, Interval INTEGER DEFAULT 0, Count INTEGER DEFAULT 0)")
                    db.action(
                        "CREATE TABLE genrebooks (GenreID INTEGER REFERENCES genres (GenreID) ON DELETE CASCADE, "
                        "BookID TEXT REFERENCES books (BookID) ON DELETE CASCADE, UNIQUE (GenreID,BookID))")
                    db.action(
                        "CREATE TABLE comicissues (ComicID TEXT REFERENCES comics (ComicID) ON DELETE CASCADE,"
                        " IssueID TEXT, IssueAcquired TEXT, IssueFile TEXT, Cover TEXT, Description TEXT, "
                        "Link TEXT, Contributors TEXT, UNIQUE (ComicID, IssueID))")
                    db.action(
                        "CREATE TABLE sent_file (WhenSent TEXT, UserID TEXT REFERENCES users (UserID) ON "
                        "DELETE CASCADE, Addr TEXT, FileName TEXT)")

                    # pastissues table has same layout as wanted table, code below is to save typos if columns change
                    res = db.match("SELECT sql FROM sqlite_master WHERE type='table' AND name='wanted'")
                    db.action(res['sql'].replace('wanted', 'pastissues'))
                    db.action('ALTER TABLE pastissues ADD COLUMN Added INTEGER DEFAULT 0')

                    cmd = "INSERT into users (UserID, UserName, Name, Password, Perms) VALUES (?, ?, ?, ?, ?)"
                    db.action(cmd, (pwd_generator(), 'admin', 'admin', md5_utf8('admin'), 65535))
                    logger.debug('Added admin user')

                    db.action('CREATE INDEX issues_Title_index ON issues (Title)')
                    db.action('CREATE INDEX books_index_authorid ON books(AuthorID)')
                    db.action('CREATE INDEX books_index_status ON books(Status)')
                    db.action('CREATE INDEX authors_index_status ON authors(Status)')
                    db.action('CREATE INDEX wanted_index_status ON wanted(Status)')

                if db_version < 45:
                    msg = f'Your database is too old. Unable to upgrade database from v{db_version}.'
                    upgradelog.write(f"{time.ctime()}: {msg}\n")
                    logger.error(msg)
                    lazylibrarian.UPDATE_MSG = msg

                index = db_version + 1
                while f'db_v{index}' in globals():
                    db_changes += 1
                    upgrade_function = getattr(lazylibrarian.dbupgrade, f'db_v{index}')
                    upgrade_function(db, upgradelog)
                    index += 1

            # a few quick sanity checks and a schema update if needed...
            lazylibrarian.UPDATE_MSG = 'Checking Database'
            db_changes += check_db(upgradelog=upgradelog)

            if db_changes:
                db.action(f'PRAGMA user_version={current_version}')
                lazylibrarian.UPDATE_MSG = 'Cleaning Database'
                upgradelog.write(f"{time.ctime()}: {lazylibrarian.UPDATE_MSG}\n")
                db.action('vacuum')
                lazylibrarian.UPDATE_MSG = f'Database updated to version {current_version}'
                logger.info(lazylibrarian.UPDATE_MSG)
                upgradelog.write(f"{time.ctime()}: {lazylibrarian.UPDATE_MSG}\n")

            db.close()

            if restartjobs:
                restart_jobs(command=SchedulerCommand.START)
            lazylibrarian.UPDATE_MSG = ''

        except Exception:
            msg = f'Unhandled exception in database upgrade: {traceback.format_exc()}'
            upgradelog.write(f"{time.ctime()}: {msg}\n")
            logger.error(msg)
            lazylibrarian.UPDATE_MSG = ''
        finally:
            db.close()


def check_db(upgradelog=None):
    logger = logging.getLogger(__name__)
    matchinglogger = logging.getLogger('special.matching')
    cnt = 0
    closefile = False
    db = database.DBConnection()
    db_changes = 0
    try:
        if not upgradelog:
            upgradelog = open(DIRS.get_logfile('dbupgrade.log'), 'a')
            closefile = True
        db_changes = update_schema(db, upgradelog)

        lazylibrarian.UPDATE_MSG = 'Checking unique authors'
        unique = False
        indexes = db.select("PRAGMA index_list('authors')")
        for item in indexes:
            data = list(item)
            if data[2] == 1:  # unique index
                res = db.match(f"PRAGMA index_info('{data[1]}')")
                data = list(res)
                if data[2] == 'AuthorID':
                    unique = True
                    break
        if not unique:
            res = db.match('select count(distinct authorid) as d,count(authorid) as c from authors')
            if res['d'] == res['c']:
                logger.warning("Adding unique index to AuthorID")
                db.action("CREATE UNIQUE INDEX unique_authorid ON authors('AuthorID')")
            else:
                msg = f"Unable to create unique index on AuthorID: {res['d']} vs {res['c']}"
                logger.error(msg)
            cnt = 1

        try:
            # check author information provider matches database
            info = CONFIG.get_str('BOOK_API')
            if info == 'OpenLibrary':
                source = 'ol_id'
            elif info == 'GoodReads':
                source = 'gr_id'
            elif info == 'HardCover':
                source = 'hc_id'
            else:
                source = ''
            if source:
                tot = db.match('SELECT count(*) from authors')
                miss = db.match(
                    f"SELECT count(*) from authors WHERE ({source} is null or {source} = '') "
                    f"and Status in ('Wanted', 'Active')")

                if miss[0]:
                    logger.warning(
                        f"Information source is {info} but {miss[0]} active authors "
                        f"(from {tot[0]}) do not have {info} ID")

            # correct any invalid/unpadded dates, longest we expect is yyyy-mm-dd but could just be yyyy
            lazylibrarian.UPDATE_MSG = 'Checking dates'
            cmd = ("SELECT BookID,BookDate from books WHERE BookDate LIKE '%-_-%' or BookDate LIKE '%-_'"
                   " or length(BookDate) > 10")
            res = db.select(cmd)
            tot = len(res)
            if tot:
                cnt += tot
                msg = f"Updating {tot} {plural(tot, 'book')} with invalid/unpadded bookdate"
                logger.warning(msg)
                for item in res:
                    parts = item['BookDate'].split('-')
                    if len(parts) == 3:
                        mn = check_int(parts[1], 0)
                        dy = check_int(parts[2], 0)
                        if mn and dy:
                            bookdate = f"{parts[0]}-{mn:02d}-{dy:02d}"
                            db.action("UPDATE books SET BookDate=? WHERE BookID=?", (bookdate, item['BookID']))
                        else:
                            logger.warning(f"Invalid Month/Day ({item['BookDate']}) for {item['BookID']}")
                            db.action("UPDATE books SET BookDate='0000' WHERE BookID=?", (item['BookID'], ))
                    else:
                        logger.warning(f"Invalid BookDate ({item['BookDate']}) for {item['BookID']}")
                        db.action("UPDATE books SET BookDate='0000' WHERE BookID=?", (item['BookID'], ))

            cmd = ("SELECT AuthorID,LastDate from authors WHERE LastDate LIKE '%-_-%' or LastDate LIKE '%-_'"
                   " or length(LastDate) > 10")
            res = db.select(cmd)
            tot = len(res)
            if tot:
                cnt += tot
                msg = f"Updating {tot} {plural(tot, 'author')} with invalid/unpadded lastdate"
                logger.warning(msg)
                for item in res:
                    parts = item['LastDate'].split('-')
                    if len(parts) == 3:
                        mn = check_int(parts[1], 0)
                        dy = check_int(parts[2], 0)
                        if mn and dy:
                            lastdate = f"{parts[0]}-{mn:02d}-{dy:02d}"
                            db.action("UPDATE authors SET LastDate=? WHERE AuthorID=?", (lastdate, item['AuthorID']))
                        else:
                            logger.warning(f"Invalid Month/Day ({item['LastDate']}) for {item['AuthorID']}")
                            db.action("UPDATE authors SET LastDate='0000' WHERE AuthorID=?", (item['AuthorID'], ))
                    else:
                        logger.warning(f"Invalid LastDate ({item['LastDate']}) for {item['AuthorID']}")
                        db.action("UPDATE authors SET LastDate='0000' WHERE AuthorID=?", (item['AuthorID'], ))

            # update any null author reason entries
            res = db.match("SELECT count(*) as counter from authors WHERE reason is null")
            tot = res['counter']
            if tot:
                cnt += tot
                logger.warning(f"Found {tot} authors with null reason")
                db.action("UPDATE authors SET reason='Unknown' WHERE reason is null")

            # delete any null wanted entries
            res = db.match("SELECT count(*) as counter from wanted WHERE BookID is null")
            tot = res['counter']
            if tot:
                cnt += tot
                logger.warning(f"Found {tot} wanted items marked null")
                db.action("DELETE from wanted WHERE BookID is null or AuxInfo is null")

            # update any series "Skipped" to series "Paused"
            res = db.match("SELECT count(*) as counter from series WHERE Status='Skipped'")
            tot = res['counter']
            if tot:
                cnt += tot
                logger.warning(f"Found {tot} series marked Skipped, updating to Paused")
                db.action("UPDATE series SET Status='Paused' WHERE Status='Skipped'")

            if CONFIG['NO_SINGLE_BOOK_SERIES']:
                cmd = 'SELECT * from series where total=1'
                res = db.select(cmd)
                if len(res):
                    logger.debug(f"Deleting {len(res)} single-book series from database")
                    for item in res:
                        logger.warning(f"Removing single book series {item['SeriesID']}:{item['SeriesName']}")
                    db.action("DELETE from series where total=1")

            # Extract any librarything workids from workpage url
            cmd = ("SELECT WorkPage,BookID from books WHERE instr(WorkPage, 'librarything.com/work/') > 0 "
                   "and LT_WorkID is NULL")
            res = db.select(cmd)
            tot = len(res)
            if tot:
                cnt += tot
                logger.warning(f"Found {tot} workpage links with no workid")
                for bk in res:
                    workid = bk[0]
                    workid = workid.split('librarything.com/work/')[1]
                    db.action("UPDATE books SET LT_WorkID=? WHERE BookID=?", (workid, bk[1]))

            # replace faulty/html language results with Unknown
            lazylibrarian.UPDATE_MSG = 'Checking languages'
            filt = "BookLang is NULL or BookLang='' or BookLang LIKE '%<%' or BookLang LIKE '%invalid%'"
            cmd = f"SELECT count(*) as counter from books WHERE {filt}"
            res = db.match(cmd)
            tot = res['counter']
            if tot:
                cnt += tot
                msg = f"Updating {tot} {plural(tot, 'book')} with no language to \"Unknown\""
                logger.warning(msg)
                db.action(f"UPDATE books SET BookLang='Unknown' WHERE {filt}")

            cmd = "SELECT BookID,BookLang from books WHERE instr(BookLang, ',') > 0"
            res = db.select(cmd)
            tot = len(res)
            if tot:
                cnt += tot
                msg = f"Updating {tot} {plural(tot, 'book')} with multiple language"
                logger.warning(msg)
                wantedlanguages = get_list(CONFIG['IMP_PREFLANG'])
                for bk in res:
                    lang = 'Unknown'
                    languages = get_list(bk[1])
                    for item in languages:
                        if item in wantedlanguages:
                            lang = item
                            break
                    db.action("UPDATE books SET BookLang=? WHERE BookID=?", (lang, bk[0]))

            # delete html error pages
            filt = 'length(lang) > 30'
            cmd = f"SELECT count(*) as counter from languages WHERE {filt}"
            res = db.match(cmd)
            tot = res['counter']
            if tot:
                cnt += tot
                msg = f"Updating {tot} {plural(tot, 'language')} with bad data"
                logger.warning(msg)
                cmd = f"DELETE from languages WHERE {filt}"
                db.action(cmd)

            # suppress duplicate entries in language table
            lazylibrarian.UPDATE_MSG = 'Checking unique languages'
            filt = 'rowid not in (select max(rowid) from languages group by isbn)'
            cmd = f"SELECT count(*) as counter from languages WHERE {filt}"
            res = db.match(cmd)
            tot = res['counter']
            if tot:
                cnt += tot
                msg = f"Deleting {tot} duplicate {plural(tot, 'language')}"
                logger.warning(msg)
                cmd = f"DELETE from languages WHERE {filt}"
                db.action(cmd)

            #  remove books with no bookid
            lazylibrarian.UPDATE_MSG = 'Removing books with no bookid'
            books = db.select("SELECT * FROM books WHERE BookID is NULL or BookID=''")
            if books:
                cnt += len(books)
                msg = f"Removing {len(books)} {plural(len(books), 'book')} with no bookid"
                logger.warning(msg)
                db.action("DELETE from books WHERE BookID is NULL or BookID=''")

            #  remove books with no authorid
            lazylibrarian.UPDATE_MSG = 'Removing books with no authorid'
            books = db.select("SELECT BookID FROM books WHERE AuthorID is NULL or AuthorID=''")
            if books:
                cnt += len(books)
                msg = f"Removing {len(books)} {plural(len(books), 'book')} with no authorid"
                logger.warning(msg)
                for book in books:
                    for table in ['books', 'wanted', 'readinglists']:
                        db.action(f"DELETE from {table} WHERE BookID=?", (book['BookID'],))

            # remove authors with no authorid
            lazylibrarian.UPDATE_MSG = 'Removing authors with no authorid'
            authors = db.select("SELECT * FROM authors WHERE AuthorID IS NULL or AuthorID=''")
            if authors:
                cnt += len(authors)
                msg = f"Removing {len(authors)} {plural(len(authors), 'author')} with no authorid"
                logger.warning(msg)
                db.action("DELETE from authors WHERE AuthorID is NULL or AuthorID=''")

            # remove authors with no name
            lazylibrarian.UPDATE_MSG = 'Removing authors with no name'
            authors = db.select("SELECT AuthorID FROM authors WHERE AuthorName IS NULL or AuthorName = ''")
            if authors:
                cnt += len(authors)
                msg = f"Removing {len(authors)} {plural(len(authors), 'author')} with no name"
                logger.warning(msg)
                for author in authors:
                    db.action("DELETE from authors WHERE AuthorID=?", (author['AuthorID'],))

            # remove authors that started initializing, but failed to get added fully
            lazylibrarian.UPDATE_MSG = 'Removing partially initialized authors'
            authors = db.select("SELECT AuthorID FROM authors WHERE instr(AuthorName, 'unknown author ') > 0")
            if authors:
                cnt += len(authors)
                msg = f"Removing {len(authors)} {plural(len(authors), 'author')} partially initialized authors"
                logger.warning(msg)
                for author in authors:
                    db.action("DELETE from authors WHERE AuthorID=?", (author['AuthorID'],))

            # remove magazines with no name
            lazylibrarian.UPDATE_MSG = 'Removing magazines with no name'
            mags = db.select("SELECT Title FROM magazines WHERE Title IS NULL or Title = ''")
            if mags:
                cnt += len(mags)
                msg = f"Removing {len(mags)} {plural(len(mags), 'magazine')} with no name"
                logger.warning(msg)
                db.action("DELETE from magazines WHERE Title IS NULL or Title = ''")

            # remove books with no title
            lazylibrarian.UPDATE_MSG = 'Removing books with no title'
            mags = db.select("SELECT BookName FROM books WHERE BookName IS NULL or BookName = ''")
            if mags:
                cnt += len(mags)
                msg = f"Removing {len(mags)} {plural(len(mags), 'book')} with no title"
                logger.warning(msg)
                db.action("DELETE from books WHERE BookName IS NULL or BookName = ''")

            # remove authors with no books
            lazylibrarian.UPDATE_MSG = 'Removing authors with no listed books'
            authors = db.select('SELECT AuthorID FROM authors WHERE TotalBooks=0')
            if authors:
                for author in authors:  # check we haven't mis-counted
                    update_totals(author['authorid'])
                # haynes-ops fix: never an author that still owns a book row (its books would cascade).
                authors = db.select('SELECT AuthorID FROM authors WHERE TotalBooks=0 AND AuthorID NOT IN '
                                    '(SELECT AuthorID FROM books WHERE AuthorID IS NOT NULL)')
                if authors:
                    cnt += len(authors)
                    msg = f"Removing {len(authors)} {plural(len(authors), 'author')} with no listed books"
                    logger.warning(msg)
                    for author in authors:
                        db.action("DELETE from authors WHERE AuthorID=?", (author['AuthorID'],))

            # haynes-ops fix 2: census of books no author counts (thaynes43/haynesnetwork#736). Fix 1 keeps them.
            unlinked = db.match('SELECT count(*) AS n FROM books b WHERE NOT EXISTS '
                                '(SELECT 1 FROM bookauthors ba WHERE ba.BookID = b.BookID)')
            unlinked = check_int(unlinked['n'], 0) if unlinked else 0
            if unlinked:
                cnt += unlinked
                logger.warning(f"LL_UNLINKED_BOOKS {unlinked} {plural(unlinked, 'book')} with no bookauthors row: "
                               f"no author counts them, only the haynes-ops spare path keeps them")
            else:
                logger.info('haynes-ops census: every book has a bookauthors row')

            # update author images if exist and nophoto in database
            authors = db.select("SELECT AuthorID FROM authors WHERE authorimg = 'images/nophoto.png'")
            if authors:
                msg = f'Checking {len(authors)} author images'
                lazylibrarian.UPDATE_MSG = msg
                logger.warning(msg)
                imgs = 0
                for author in authors:
                    filename = os.path.join(DIRS.CACHEDIR, 'author', f"{author['AuthorID']}.jpg")
                    if os.path.isfile(filename):
                        cachefile = f"cache/author/{author['AuthorID']}.jpg"
                        imgs += 1
                        db.action("UPDATE authors SET AuthorImg=? WHERE AuthorID=?", (cachefile, author['AuthorID'],))
                if imgs:
                    cnt += imgs
                    logger.warning(f"Updated {imgs} author images")

            # remove series with no members
            lazylibrarian.UPDATE_MSG = 'Removing series with no members'
            series = db.select('SELECT SeriesID,SeriesName FROM series WHERE Total=0')
            if series:
                for ser in series:  # check we haven't mis-counted
                    res = db.match('select count(*) as counter from member where seriesid=?', (ser['SeriesID'],))
                    if res:
                        counter = check_int(res['counter'], 0)
                        if counter:
                            db.action("UPDATE series SET Total=? WHERE SeriesID=?", (counter, ser['SeriesID']))
                series = db.select('SELECT SeriesID,SeriesName FROM series WHERE Total=0')
                if series:
                    cnt += len(series)
                    msg = f'Removing {len(series)} series with no members'
                    logger.warning(msg)
                    for item in series:
                        logger.warning(f"Removing series {item['SeriesID']}:{item['SeriesName']}")
                        db.action("DELETE from series WHERE SeriesID=?", (item['SeriesID'],))

            # check if genre exclusions/translations have altered
            lazylibrarian.UPDATE_MSG = 'Checking for invalid genres'
            if lazylibrarian.GRGENRES:
                for item in lazylibrarian.GRGENRES.get('genreExclude', []):
                    match = db.match('SELECT GenreID from genres where GenreName=? COLLATE NOCASE', (item,))
                    if match:
                        cnt += 1
                        msg = f'Removing excluded genre [{item}]'
                        logger.warning(msg)
                        for table in ['genrebooks', 'genres']:
                            db.action(f"DELETE from {table} WHERE GenreID=?", (match['GenreID'],))

                for item in lazylibrarian.GRGENRES.get('genreExcludeParts', []):
                    cmd = f"SELECT GenreID,GenreName from genres where instr(GenreName, '{item}') > 0 COLLATE NOCASE"
                    matches = db.select(cmd)
                    if matches:
                        cnt += len(matches)
                        for itm in matches:
                            msg = f"Removing excluded genre [{itm['GenreName']}]"
                            logger.warning(msg)
                            for table in ['genrebooks', 'genres']:
                                db.action(f"DELETE from {table} WHERE GenreID=?", (itm['GenreID'],))

                for item in lazylibrarian.GRGENRES.get('genreReplace', {}):
                    match = db.match('SELECT GenreID from genres where GenreName=? COLLATE NOCASE', (item,))
                    if match:
                        newitem = lazylibrarian.GRGENRES['genreReplace'][item]
                        newmatch = db.match('SELECT GenreID from genres where GenreName=? COLLATE NOCASE', (newitem,))
                        cnt += 1
                        msg = f'Replacing genre [{item}] with [{newitem}]'
                        logger.warning(msg)
                        if not newmatch:
                            db.action('INSERT into genres (GenreName) VALUES (?)', (newitem,))
                        res = db.select('SELECT bookid from genrebooks where genreid=?', (match['GenreID'],))
                        for bk in res:
                            cmd = ('select genrename from genres,genrebooks,books where '
                                   'genres.genreid=genrebooks.genreid  and books.bookid=genrebooks.bookid '
                                   'and books.bookid=?')
                            bkgenres = db.select(cmd, (bk['bookid'],))
                            lst = []
                            for gnr in bkgenres:
                                lst.append(gnr['genrename'])
                            if item in lst:
                                lst.remove(item)
                            if newitem not in lst:
                                lst.append(newitem)
                            set_genres(lst, bk['bookid'])
            # remove genres with no books
            lazylibrarian.UPDATE_MSG = 'Removing genres with no books'
            cmd = ('select GenreID, (select count(*) as counter from genrebooks where '
                   'genres.genreid = genrebooks.genreid) as cnt from genres where cnt = 0')
            genres = db.select(cmd)
            if genres:
                cnt += len(genres)
                msg = f"Removing {len(genres)} empty {plural(len(genres), 'genre')}"
                logger.warning(msg)
                for item in genres:
                    db.action("DELETE from genres WHERE GenreID=?", (item['GenreID'],))

            # remove any orphan entries (shouldn't happen with foreign key active)
            lazylibrarian.UPDATE_MSG = 'Removing orphans'
            for entry in [
                ['authorid', 'books', 'authors'],
                ['seriesid', 'member', 'series'],
                ['seriesid', 'seriesauthors', 'series'],
                ['seriesid', 'series', 'seriesauthors'],
                ['authorid', 'seriesauthors', 'authors'],
                ['title', 'issues', 'magazines'],
                ['genreid', 'genrebooks', 'genres'],
                ['comicid', 'comicissues', 'comics'],
                ['userid', 'subscribers', 'users'],
            ]:
                orphans = db.select(f'select {entry[0]} from {entry[1]} except select {entry[0]} from {entry[2]}')
                if orphans:
                    cnt += len(orphans)
                    msg = f'Found {len(orphans)} orphan {entry[0]} in {entry[1]}'
                    logger.warning(msg)
                    for orphan in orphans:
                        db.action(f"DELETE from {entry[1]} WHERE {entry[0]}='{orphan[0]}'")

            # reset any snatched entries in books table that don't match history/wanted
            lazylibrarian.UPDATE_MSG = 'Syncing Snatched entries'
            cmd = ("select bookid from books where status='Snatched' except select bookid from wanted "
                   "where status='Snatched' and auxinfo='eBook'")
            snatches = db.select(cmd)
            if snatches:
                cnt += len(snatches)
                msg = f'Found {len(snatches)} snatched ebook not snatched in wanted'
                logger.warning(msg)
                for orphan in snatches:
                    db.action("UPDATE books SET status='Skipped' WHERE bookid=?", (orphan[0],))

            cmd = ("select bookid from books where audiostatus='Snatched' except select bookid from wanted "
                   "where status='Snatched' and auxinfo='AudioBook'")
            snatches = db.select(cmd)
            if snatches:
                cnt += len(snatches)
                msg = f'Found {len(snatches)} snatched audiobook not snatched in wanted'
                logger.warning(msg)
                for orphan in snatches:
                    db.action("UPDATE books SET audiostatus='Skipped' WHERE bookid=?", (orphan[0],))

            # all authors with no books in the library and no books marked wanted unless series contributor
            cmd = ("select authorid from authors where havebooks=0 and totalbooks>0 and instr(Reason, 'Series') = 0 "
                   "except select authorid from books where (books.status='Wanted' or books.audiostatus='Wanted');")
            authors = db.select(cmd)
            if authors:
                msg = (f"Found {len(authors)} {plural(len(authors), 'author')} "
                       f"with no existing or wanted books")
                logger.warning(msg)
                # Don't auto delete them, may be in a reading list?
                for author in authors:
                    name = db.match("SELECT authorname,status,reason from authors where authorid=?",
                                    (author[0],))
                    matchinglogger.warning(f"{name[0]} ({name[1]}) has no active books ({name[2]})")
                # db.action('DELETE from authors where authorid=?', (author[0],))

            # update empty bookdate to "0000"
            lazylibrarian.UPDATE_MSG = 'Updating books with no bookdate'
            books = db.select("SELECT * FROM books WHERE BookDate is NULL or BookDate=''")
            if books:
                cnt += len(books)
                msg = f"Found {len(books)} {plural(len(books), 'book')} with no bookdate"
                logger.warning(msg)
                db.action("UPDATE books SET BookDate='0000' WHERE BookDate is NULL or BookDate=''")

            # update Requester and AudioRequester from NULL to ''
            lazylibrarian.UPDATE_MSG = 'Updating Requester/AudioRequester'
            books = db.select("SELECT * FROM books WHERE Requester is NULL")
            if books:
                cnt += len(books)
                msg = f"Found {len(books)} {plural(len(books), 'book')} with Requester as NULL"
                logger.warning(msg)
                db.action("UPDATE books SET Requester='' WHERE Requester is NULL")
            books = db.select("SELECT * FROM books WHERE AudioRequester is NULL")
            if books:
                cnt += len(books)
                msg = f"Found {len(books)} {plural(len(books), 'book')} with AudioRequester as NULL"
                logger.warning(msg)
                db.action("UPDATE books SET AudioRequester='' WHERE AudioRequester is NULL")

            # delete any duplicate entries in member table and add a unique constraint if not already done
            cmd = "SELECT * from sqlite_master WHERE type= 'index' and tbl_name = 'member' and name = 'unq'"
            match = db.match(cmd)
            if not match:
                logger.debug("Removing any duplicates from member table and adding unique constraint")
                cmd = ("delete from member where rowid not in (select min(rowid) from member "
                       "group by seriesid,bookid)")
                db.action(cmd)
                db.action("CREATE UNIQUE INDEX unq ON member(seriesid,bookid)")

            # check magazine latest cover is correct:
            cmd = ("select magazines.title,magazines.issuedate,latestcover,cover from magazines,issues where "
                   "magazines.title=issues.title and magazines.issuedate=issues.issuedate and "
                   "latestcover != cover and cover != '' and cover is not NULL")
            latest = db.select(cmd)
            if latest:
                cnt += len(latest)
                msg = f"Found {len(latest)} {plural(len(latest), 'magazine')} with incorrect latest cover"
                logger.warning(msg)
                for item in latest:
                    db.action('UPDATE magazines SET LatestCover=? WHERE Title=?', (item['cover'], item['title']))

            lazylibrarian.UPDATE_MSG = 'Checking goodreads lists'
            res = db.select("SELECT Label,SyncList from sync WHERE UserID='goodreads'")
            cmd = "SELECT gr_id from books WHERE BookID=?"
            for synclist in res:
                old_list = get_list(synclist['SyncList'])
                new_list = []
                for item in old_list:
                    item = str(item).strip('"')
                    if item.isnumeric():
                        new_list.append(item)
                    else:
                        # not a goodreads ID
                        match = db.match(cmd, (item,))
                        if match and match[0]:
                            new_list.append(match[0])
                            logger.debug(f"Bookid {item} is goodreads {match[0]}")
                        else:
                            logger.debug(f"Bookid {item} in {synclist['Label']} not matched at GoodReads, removed")
                new_set = set(new_list)
                new_list = ','.join(new_set)
                db.action("UPDATE sync SET SyncList=? WHERE Label=? AND UserID='goodreads'",
                          (new_list, synclist['Label']))

            lazylibrarian.UPDATE_MSG = 'Checking reading lists'
            bookids = []
            res = db.select('SELECT BookID from books')
            for bookid in res:
                bookids.append(bookid[0])
            bookids = set(bookids)
            read_ids = []
            reading_lists = ['readinglists']
            for table in reading_lists:
                exists = db.select(f'PRAGMA table_info({table})')
                if exists:
                    res = db.select(f'SELECT BookID from {table}')
                    for bookid in res:
                        read_ids.append(bookid[0])

            read_ids = set(read_ids)
            no_bookid = read_ids.difference(bookids)
            if len(no_bookid):
                logger.warning(f"Found {len(no_bookid)} unknown bookids in reading lists")
            for item in no_bookid:
                cmd = ('SELECT BookID from books WHERE ol_id=? OR gr_id=? OR lt_workid=? OR gb_id=?'
                       ' or hc_id=? or dnb_id=?')
                res = db.match(cmd, (item, item, item, item, item, item))
                if res:
                    logger.debug(f"Bookid {item} is now {res[0]}")
                    for table in reading_lists:
                        cmd = f"UPDATE {table} SET BookID=? WHERE BookID=?"
                        db.action(cmd, (res[0], item), suppress='UNIQUE')
                else:
                    logger.debug(f"Bookid {item} is unknown, deleting it")
                    for table in reading_lists:
                        cmd = f'DELETE FROM {table} WHERE BookID=?'
                        db.action(cmd, (item,))
        except Exception as e:
            msg = f'Error: {type(e).__name__} {str(e)}'
            logger.error(msg)
    except Exception as e:
        msg = f'Error: {type(e).__name__} {str(e)}'
        logger.error(msg)
    db.close()
    if closefile:
        upgradelog.close()
    logger.info(f"Database check found {cnt} {plural(cnt, 'error')}")
    lazylibrarian.UPDATE_MSG = ''
    return db_changes


def calc_eta(start_time, start_count, done):
    percent_done = done * 100 / start_count
    if not percent_done:
        secs_left = start_count * 1.5
    else:
        time_elapsed = time.time() - start_time
        secs_per_percent = time_elapsed / percent_done
        percent_left = 100 - percent_done
        secs_left = percent_left * secs_per_percent

    eta = int(secs_left / 60) + (secs_left % 60 > 0)
    if eta < 2:
        return f"Completed {int(percent_done)}% eta {eta} minute"
    if eta < 120:
        return f"Completed {int(percent_done)}% eta {eta} minutes"
    eta = int(secs_left / 3600) + (secs_left % 3600 > 0)
    return f"Completed {int(percent_done)}% eta {eta} hours"


def db_v46(db, upgradelog):
    upgradelog.write(f"{time.ctime()} v46: Re-creating past issues table\n")
    db.action('DROP TABLE pastissues')
    res = db.match("SELECT sql FROM sqlite_master WHERE type='table' AND name='wanted'")
    db.action(res['sql'].replace('wanted', 'pastissues'))
    upgradelog.write(f"{time.ctime()} v46: complete\n")


def db_v47(db, upgradelog):
    upgradelog.write(f"{time.ctime()} v47: Creating genre tables\n")
    if not has_column(db, "genres", "GenreID"):
        db.action('CREATE TABLE genres (GenreID INTEGER PRIMARY KEY AUTOINCREMENT, GenreName TEXT UNIQUE)')
        db.action(
            "CREATE TABLE genrebooks (GenreID INTEGER REFERENCES genres (GenreID) ON DELETE CASCADE, "
            "BookID TEXT REFERENCES books (BookID) ON DELETE CASCADE, UNIQUE (GenreID,BookID))")
    res = db.select("SELECT bookid,bookgenre FROM books WHERE (Status='Open' or AudioStatus='Open')")
    tot = len(res)
    if tot:
        upgradelog.write(f"{time.ctime()} v47: Upgrading {tot} genres\n")
        for cnt, book in enumerate(res, start=1):
            db.action('DELETE from genrebooks WHERE BookID=?', (book['bookid'],))
            lazylibrarian.UPDATE_MSG = f"Updating genres {cnt} of {tot}"
            for item in get_list(book['bookgenre'], ','):
                match = db.match('SELECT GenreID from genres where GenreName=? COLLATE NOCASE', (item,))
                if not match:
                    db.action('INSERT into genres (GenreName) VALUES (?)', (item,))
                    match = db.match('SELECT GenreID from genres where GenreName=?', (item,))
                db.action('INSERT into genrebooks (GenreID, BookID) VALUES (?,?)',
                          (match['GenreID'], book['bookid']), suppress='UNIQUE')
    upgradelog.write(f"{time.ctime()} v47: complete\n")


def db_v48(db, upgradelog):
    upgradelog.write(f"{time.ctime()} v48: Checking magazines table\n")
    res = db.action("SELECT sql FROM sqlite_master WHERE type='table' AND name='magazines'")
    if 'Title TEXT UNIQUE' not in res:
        res = db.match('SELECT count(*) as cnt from magazines')
        upgradelog.write(f"{time.ctime()} v48: updating {res['cnt']} magazines\n")
        db.action('PRAGMA foreign_keys = OFF')
        db.action('DROP TABLE IF EXISTS temp')
        db.action('ALTER TABLE magazines RENAME to temp')
        db.action(
            "CREATE TABLE magazines (Title TEXT UNIQUE, Regex TEXT, Status TEXT, MagazineAdded TEXT, "
            "LastAcquired TEXT, IssueDate TEXT, IssueStatus TEXT, Reject TEXT, LatestCover TEXT, "
            "DateType TEXT, CoverPage INTEGER DEFAULT 1)")
        db.action(
            "INSERT INTO magazines SELECT Title,Regex,Status,MagazineAdded,LastAcquired,IssueDate,IssueStatus,"
            "Reject,LatestCover,DateType,CoverPage FROM temp")
        db.action('DROP TABLE temp')
        db.action('PRAGMA foreign_keys = ON')
    upgradelog.write(f"{time.ctime()} v48: complete\n")


def db_v49(db, upgradelog):
    upgradelog.write(f"{time.ctime()} v49: Checking authors table\n")
    res = db.action("SELECT sql FROM sqlite_master WHERE type='table' AND name='authors'")
    if 'AuthorID TEXT UNIQUE' not in res or 'AuthorName TEXT UNIQUE' not in res:
        res = db.match('SELECT count(*) as cnt from authors')
        upgradelog.write(f"{time.ctime()} v49: updating {res['cnt']} authors\n")
        db.action('PRAGMA foreign_keys = OFF')
        db.action('DROP TABLE IF EXISTS temp')
        db.action('ALTER TABLE authors RENAME to temp')
        db.action('CREATE TABLE authors (AuthorID TEXT UNIQUE, AuthorName TEXT UNIQUE, ' +
                  'AuthorImg TEXT, AuthorLink TEXT, DateAdded TEXT, Status TEXT, LastBook TEXT, ' +
                  'LastBookImg TEXT, LastLink TEXT, LastDate TEXT, HaveBooks INTEGER DEFAULT 0, ' +
                  'TotalBooks INTEGER DEFAULT 0, AuthorBorn TEXT, AuthorDeath TEXT, ' +
                  'UnignoredBooks INTEGER DEFAULT 0, Manual TEXT, GRfollow TEXT, LastBookID TEXT)')
        db.action(
            "INSERT INTO authors SELECT AuthorID,AuthorName,AuthorImg,AuthorLink,DateAdded,Status,LastBook,"
            "LastBookImg,LastLink,LastDate,HaveBooks,TotalBooks,AuthorBorn,AuthorDeath,UnignoredBooks,Manual,"
            "GRfollow,LastBookID FROM temp")
        db.action('DROP TABLE temp')
        db.action('PRAGMA foreign_keys = ON')
    upgradelog.write(f"{time.ctime()} v49: complete\n")


def db_v50(db, upgradelog):
    upgradelog.write(f"{time.ctime()} v50: Creating comics tables\n")
    if not has_column(db, "comics", "ComicID"):
        db.action(
            "CREATE TABLE comics (ComicID TEXT UNIQUE, Title TEXT, Status TEXT, Added TEXT, LastAcquired TEXT, "
            "Updated TEXT, LatestIssue TEXT, IssueStatus TEXT, LatestCover TEXT, SearchTerm TEXT, Start TEXT, "
            "First INTEGER, Last INTEGER, Publisher TEXT, Link TEXT)")
        db.action(
            "CREATE TABLE comicissues (ComicID TEXT REFERENCES comics (ComicID) ON DELETE CASCADE, IssueID TEXT, "
            "IssueAcquired TEXT, IssueFile TEXT, UNIQUE (ComicID, IssueID))")


def db_v51(db, upgradelog):
    if not has_column(db, "comics", "aka"):
        lazylibrarian.UPDATE_MSG = 'Adding aka to comics table'
        upgradelog.write(f"{time.ctime()} v51: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE comics ADD COLUMN aka TEXT')
    upgradelog.write(f"{time.ctime()} v51: complete\n")


def db_v52(db, upgradelog):
    if not has_column(db, "series", "Updated"):
        lazylibrarian.UPDATE_MSG = 'Adding Updated column to series table'
        upgradelog.write(f"{time.ctime()} v52: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE series ADD COLUMN Updated INTEGER DEFAULT 0')
    upgradelog.write(f"{time.ctime()} v52: complete\n")


def db_v53(db, upgradelog):
    if not has_column(db, "jobs", "Name"):
        lazylibrarian.UPDATE_MSG = 'Creating jobs table'
        upgradelog.write(f"{time.ctime()} v53: {lazylibrarian.UPDATE_MSG}\n")
        db.action('CREATE TABLE jobs (Name TEXT, LastRun INTEGER DEFAULT 0)')
    upgradelog.write(f"{time.ctime()} v53: complete\n")


def db_v54(db, upgradelog):
    if not has_column(db, "authors", "Updated"):
        lazylibrarian.UPDATE_MSG = 'Separating dates in authors table'
        upgradelog.write(f"{time.ctime()} v54: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE authors ADD COLUMN Updated INTEGER DEFAULT 0')
        lazylibrarian.UPDATE_MSG = 'Updating author dates'
        upgradelog.write(f"{time.ctime()} v54: {lazylibrarian.UPDATE_MSG}\n")
        authors = db.select('SELECT AuthorID,AuthorImg,DateAdded from authors')
        cnt = 0
        if authors:
            tot = len(authors)
            for author in authors:
                cnt += 1
                lazylibrarian.UPDATE_MSG = f"Updating Author dates: {cnt} of {tot}"
                updated = 0
                # noinspection PyBroadException
                try:
                    updated = int(time.mktime(datetime.datetime.strptime(author['DateAdded'],
                                                                         "%Y-%m-%d").timetuple()))
                except Exception:
                    upgradelog.write(
                        f"{time.ctime()} v54: Error getting date from [{author['DateAdded']}] {author['AuthorID']}\n")
                finally:
                    db.action('UPDATE authors SET Updated=? WHERE AuthorID=?',
                              (updated, author['AuthorID']))
            upgradelog.write(f"{time.ctime()} v54: {lazylibrarian.UPDATE_MSG}\n")
    upgradelog.write(f"{time.ctime()} v54: complete\n")


def db_v55(db, upgradelog):
    if not has_column(db, "authors", "Reason"):
        lazylibrarian.UPDATE_MSG = 'Adding Reason column to authors table'
        upgradelog.write(f"{time.ctime()} v55: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE authors ADD COLUMN Reason TEXT')
    upgradelog.write(f"{time.ctime()} v55: complete\n")


def db_v56(db, upgradelog):
    if not has_column(db, "issues", "Cover"):
        lazylibrarian.UPDATE_MSG = 'Adding Cover column to issues table'
        upgradelog.write(f"{time.ctime()} v56: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE issues ADD COLUMN Cover TEXT')

        issues = db.select('SELECT IssueFile from issues')
        tot = len(issues)
        start_time = time.time()
        for cnt, issue in enumerate(issues, start=1):
            lazylibrarian.UPDATE_MSG = (f"Updating issue cover for {issue['IssueFile']}: "
                                        f"{calc_eta(start_time, tot, cnt)}")
            coverfile = f"{splitext(issue['IssueFile'])[0]}.jpg"
            if not path_exists(coverfile):
                coverfile = os.path.join(DIRS.PROG_DIR, 'data', 'images', 'nocover.jpg')
            myhash = uuid.uuid4().hex
            hashname = os.path.join(DIRS.CACHEDIR, 'magazine', f'{myhash}.jpg')
            cachefile = f'cache/magazine/{myhash}.jpg'
            copyfile(coverfile, hashname)
            setperm(hashname)
            db.action('UPDATE issues SET Cover=? WHERE IssueFile=?', (cachefile, issue['IssueFile']))

    if not has_column(db, "comicissues", "Cover"):
        lazylibrarian.UPDATE_MSG = 'Adding Cover column to comicissues table'
        upgradelog.write(f"{time.ctime()} v56: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE comicissues ADD COLUMN Cover TEXT')

        issues = db.select('SELECT * from comicissues')
        tot = len(issues)
        start_time = time.time()
        for cnt, issue in enumerate(issues, start=1):
            lazylibrarian.UPDATE_MSG = (f"Updating comicissue cover for {issue['IssueFile']}: "
                                        f"{calc_eta(start_time, tot, cnt)}")
            coverfile = f"{splitext(issue['IssueFile'])[0]}.jpg"
            if not path_exists(coverfile):
                coverfile = os.path.join(DIRS.PROG_DIR, 'data', 'images', 'nocover.jpg')
            myhash = uuid.uuid4().hex
            hashname = os.path.join(DIRS.CACHEDIR, 'comic', f'{myhash}.jpg')
            cachefile = f'cache/comic/{myhash}.jpg'
            copyfile(coverfile, hashname)
            setperm(hashname)
            db.action('UPDATE comicissues SET Cover=? WHERE IssueFile=?', (cachefile, issue['IssueFile']))

    upgradelog.write(f"{time.ctime()} v56: complete\n")


def db_v57(db, upgradelog):
    if not has_column(db, "comicissues", "Description"):
        lazylibrarian.UPDATE_MSG = 'Adding Description, Link and Contributors columns to comicissues table'
        upgradelog.write(f"{time.ctime()} v57: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE comicissues ADD COLUMN Description TEXT')
        db.action('ALTER TABLE comicissues ADD COLUMN Link TEXT')
        db.action('ALTER TABLE comicissues ADD COLUMN Contributors TEXT')
    if not has_column(db, "comics", "Description"):
        lazylibrarian.UPDATE_MSG = 'Adding Description column to comics table'
        upgradelog.write(f"{time.ctime()} v57: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE comics ADD COLUMN Description TEXT')
    upgradelog.write(f"{time.ctime()} v57: complete\n")


def db_v58(db, upgradelog):
    if not has_column(db, "comicissues", "Link"):
        lazylibrarian.UPDATE_MSG = 'Adding Link column to comicissues table'
        upgradelog.write(f"{time.ctime()} v58: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE comicissues ADD COLUMN Link TEXT')
    upgradelog.write(f"{time.ctime()} v58: complete\n")


# noinspection PyUnusedLocal
def db_v59(db, upgradelog):
    seeders = CONFIG.get_int('NUMBEROFSEEDERS')
    if seeders:
        lazylibrarian.UPDATE_MSG = 'Setting up SEEDERS'
        upgradelog.write(f"{time.ctime()} v58: {lazylibrarian.UPDATE_MSG}\n")
        for entry in CONFIG.providers('TORZNAB'):
            entry['SEEDERS'].set_int(seeders)
        for item in ['KAT_SEEDERS', 'TPB_SEEDERS', 'TDL_SEEDERS', 'LIME_SEEDERS']:
            CONFIG.set_int(item, seeders)
    CONFIG.set_int('NUMBEROFSEEDERS', 0)
    CONFIG.save_config_and_backup_old()
    upgradelog.write(f"{time.ctime()} v59: complete\n")


# noinspection PyUnusedLocal
def db_v60(db, upgradelog):
    lazylibrarian.UPDATE_MSG = '<b>The old example_preprocessor is deprecated</b>'
    lazylibrarian.UPDATE_MSG += '<br>it\'s functions are now included in the main program'
    lazylibrarian.UPDATE_MSG += '<br>See new config options in "processing" tab'
    time.sleep(30)
    upgradelog.write(f"{time.ctime()} v60: complete\n")


def update_schema(db, upgradelog):
    logger = logging.getLogger(__name__)
    db_version = get_db_version(db)
    changes = 0

    # logger.debug("Schema check v%s, database is v%s" % (db_current_version, db_version))
    if db_current_version != db_version:
        changes += 1

    if not has_column(db, "series", "Reason"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding Reason column to series table'
        upgradelog.write(f"{time.ctime()} v61: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE series ADD COLUMN Reason TEXT')
        db.action("UPDATE series SET Reason='Historic'")

    if not has_column(db, "authors", "About"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding About column to authors table'
        upgradelog.write(f"{time.ctime()} v62: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE authors ADD COLUMN About TEXT')

    if not has_column(db, "jobs", "Start"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Replacing jobs table'
        upgradelog.write(f"{time.ctime()} v63: {lazylibrarian.UPDATE_MSG}\n")
        db.action('DROP TABLE IF EXISTS temp')
        db.action('ALTER TABLE jobs RENAME to temp')
        db.action('CREATE TABLE jobs (Name TEXT, Finish INTEGER DEFAULT 0, Start INTEGER DEFAULT 0)')
        db.action('INSERT INTO jobs SELECT Name,LastRun as Start,LastRun as Finish FROM temp')
        db.action('DROP TABLE temp')

    if not has_column(db, "pastissues", "Added"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding Added column to pastissues table'
        upgradelog.write(f"{time.ctime()} v64: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE pastissues ADD COLUMN Added INTEGER DEFAULT 0')
        db.action('UPDATE pastissues SET Added=? WHERE Added=0', (int(time.time()),))

    if not has_column(db, "users", "Abandoned"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding Reading,Abandoned columns to users table'
        upgradelog.write(f"{time.ctime()} v65: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE users ADD COLUMN Reading TEXT')
        db.action('ALTER TABLE users ADD COLUMN Abandoned TEXT')

    if not has_column(db, "subscribers", "UserID"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Creating subscribers table'
        upgradelog.write(f"{time.ctime()} v66: {lazylibrarian.UPDATE_MSG}\n")
        act = 'CREATE TABLE subscribers (UserID TEXT REFERENCES users (UserID) ON DELETE CASCADE,'
        act += ' Type TEXT, WantID Text)'
        db.action(act)

    if not has_column(db, "users", "Prefs"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding Prefs to users table'
        upgradelog.write(f"{time.ctime()} v67: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE users ADD COLUMN Prefs INTEGER DEFAULT 0')

    if not has_column(db, "wanted", "Completed"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding Completed to wanted table'
        upgradelog.write(f"{time.ctime()} v68: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE wanted ADD COLUMN Completed INTEGER DEFAULT 0')

    if not has_column(db, "authors", "AKA"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding AKA to authors table'
        upgradelog.write(f"{time.ctime()} v69: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE authors ADD COLUMN AKA TEXT')

    if not has_column(db, "books", "LT_WorkID"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding LT_WorkID to books table'
        upgradelog.write(f"{time.ctime()} v69: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE books ADD COLUMN LT_WorkID TEXT')

    if not has_column(db, 'authors', 'gr_id'):
        changes += 1
        db.action('ALTER TABLE authors ADD COLUMN gr_id TEXT')
        res = db.select('SELECT authorid,authorname from authors')
        tot = len(res)
        if tot:
            lazylibrarian.UPDATE_MSG = f"Copying authorid for {tot} authors"
            logger.debug(lazylibrarian.UPDATE_MSG)
            cnt = 0
            for auth in res:
                gr_id = auth[0]
                # name = auth[1]
                if gr_id.isdigit():
                    cnt += 1
                    db.action("UPDATE authors SET gr_id=? WHERE authorid=?", (gr_id, gr_id))
            lazylibrarian.UPDATE_MSG = f"Copied authorid for {cnt} authors (from {tot})"
            upgradelog.write(f"{time.ctime()} v70: {lazylibrarian.UPDATE_MSG}\n")
    if not has_column(db, 'books', 'gr_id'):
        changes += 1
        db.action('ALTER TABLE books ADD COLUMN gr_id TEXT')
        res = db.select('SELECT bookid from books')
        tot = len(res)
        if tot:
            lazylibrarian.UPDATE_MSG = f"Copying bookid for {tot} books"
            logger.debug(lazylibrarian.UPDATE_MSG)
            cnt = 0
            for book in res:
                gr_id = book[0]
                if gr_id.isdigit():
                    cnt += 1
                    db.action("UPDATE books SET gr_id=? WHERE bookid=?", (gr_id, gr_id))
            lazylibrarian.UPDATE_MSG = f"Copied bookid for {cnt} books (from {tot})"
            logger.debug(lazylibrarian.UPDATE_MSG)
            upgradelog.write(f"{time.ctime()} v70: {lazylibrarian.UPDATE_MSG}\n")
    if not has_column(db, "books", "Narrator"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding Narrator to books table'
        upgradelog.write(f"{time.ctime()} v71: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE books ADD COLUMN Narrator TEXT')

    if not has_column(db, "authors", "HaveAudioBooks"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding HaveEBooks and HaveAudioBooks to authors table'
        upgradelog.write(f"{time.ctime()} v72: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE authors ADD COLUMN HaveEBooks INTEGER DEFAULT 0')
        db.action('ALTER TABLE authors ADD COLUMN HaveAudioBooks INTEGER DEFAULT 0')
        authors = db.select('SELECT AuthorID FROM authors WHERE TotalBooks>0')
        if authors:
            for author in authors:
                update_totals(author['AuthorID'])

    if not has_column(db, "series", "gr_id"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding gr_id to series table'
        upgradelog.write(f"{time.ctime()} v73: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE series ADD COLUMN gr_id TEXT')

    if not has_column(db, "users", "Theme"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding Theme to users table'
        upgradelog.write(f"{time.ctime()} v74: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE users ADD COLUMN Theme TEXT')

    if not has_column(db, "authors", "ol_id"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding ol_id to authors table'
        upgradelog.write(f"{time.ctime()} v75: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE authors ADD COLUMN ol_id TEXT')
        res = db.select("SELECT authorid from authors WHERE authorid like 'OL%A'")
        if len(res):
            lazylibrarian.UPDATE_MSG = f"Copying authorid for {len(res)} authors"
            logger.debug(lazylibrarian.UPDATE_MSG)
            for author in res:
                db.action("UPDATE authors SET ol_id=? WHERE authorid=?", (author['authorid'], author['authorid']))
            lazylibrarian.UPDATE_MSG = f"Copied authorid for {len(res)} authors"
            logger.debug(lazylibrarian.UPDATE_MSG)

    if not has_column(db, "wanted", "Label"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding Label to wanted table'
        upgradelog.write(f"{time.ctime()} v76: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE wanted ADD COLUMN Label TEXT')

    if not has_column(db, "magazines", "Genre"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding Genre to magazine/comic tables'
        upgradelog.write(f"{time.ctime()} v77: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE magazines ADD COLUMN Genre TEXT')
        db.action('ALTER TABLE comics ADD COLUMN Genre TEXT')

    if not has_column(db, "users", "Login_Count"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding Last_Login and Login_Count to users table'
        upgradelog.write(f"{time.ctime()} v78: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE users ADD COLUMN Last_Login TEXT')
        db.action('ALTER TABLE users ADD COLUMN Login_Count INTEGER DEFAULT 0')
        if not has_column(db, "sent_file", "UserID"):
            changes += 1
            lazylibrarian.UPDATE_MSG = 'Creating sent_file table'
            upgradelog.write(f"{time.ctime()} v78: {lazylibrarian.UPDATE_MSG}\n")
            db.action('CREATE TABLE sent_file (WhenSent TEXT, UserID TEXT REFERENCES '
                      'users (UserID) ON DELETE CASCADE, Addr TEXT, FileName TEXT)')

    if not has_column(db, "series", "Source"):
        res = db.match("SELECT sql FROM sqlite_master WHERE type='table' AND name='series'")
        if 'SeriesID INTEGER' in res[0]:
            changes += 1
            lazylibrarian.UPDATE_MSG = 'Adding Source to series table'
            upgradelog.write(f"{time.ctime()} v79: {lazylibrarian.UPDATE_MSG}\n")
            db.action("ALTER TABLE series ADD COLUMN Source TEXT DEFAULT ''")

    if not has_column(db, "unauthorised", "UserID"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Creating unauthorised table'
        upgradelog.write(f"{time.ctime()} v80: {lazylibrarian.UPDATE_MSG}\n")
        db.action('CREATE TABLE unauthorised (AccessTime TEXT, UserID TEXT REFERENCES '
                  'users (UserID) ON DELETE CASCADE, Attempt TEXT)')

    if not has_column(db, "books", "ol_id"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding ol_id and gb_id to books table'
        upgradelog.write(f"{time.ctime()} v81: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE books ADD COLUMN ol_id TEXT')
        db.action('ALTER TABLE books ADD COLUMN gb_id TEXT')

        allowed = set(f"{string.ascii_letters + string.digits}-_")
        res = db.select("SELECT bookid from books")
        if len(res):
            lazylibrarian.UPDATE_MSG = f"Populating new fields in books table for {len(res)} books"
            logger.debug(lazylibrarian.UPDATE_MSG)
            for book in res:
                if book['bookid'] and book['bookid'].startswith('OL') and book['bookid'].endswith('W'):
                    db.action("UPDATE books SET ol_id=? WHERE bookid=?", (book['bookid'], book['bookid']))
                elif book['bookid'] and book['bookid'].isnumeric():
                    db.action("UPDATE books SET gr_id=? WHERE bookid=?", (book['bookid'], book['bookid']))
                elif book['bookid']:
                    if set(book['bookid']) <= allowed:
                        db.action("UPDATE books SET gb_id=? WHERE bookid=?", (book['bookid'], book['bookid']))
                else:
                    logger.warning(f"Unable to determine bookid type for {book['bookid']}")
            lazylibrarian.UPDATE_MSG = f"Processed {len(res)} books"
            logger.debug(lazylibrarian.UPDATE_MSG)

    if has_column(db, "users", "HaveRead"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding reading tables'
        upgradelog.write(f"{time.ctime()} v82: {lazylibrarian.UPDATE_MSG}\n")
        db.action('CREATE TABLE haveread (UserID TEXT REFERENCES users (UserID) ON DELETE CASCADE, BookID TEXT)')
        db.action('CREATE TABLE toread (UserID TEXT REFERENCES users (UserID) ON DELETE CASCADE, BookID TEXT)')
        db.action('CREATE TABLE reading (UserID TEXT REFERENCES users (UserID) ON DELETE CASCADE, BookID TEXT)')
        db.action('CREATE TABLE abandoned (UserID TEXT REFERENCES users (UserID) ON DELETE CASCADE, BookID TEXT)')
        res = db.select("SELECT UserID,HaveRead,ToRead,Reading,Abandoned from users")
        lazylibrarian.UPDATE_MSG = f"Populating new fields in reading tables for {len(res)} users"
        logger.debug(lazylibrarian.UPDATE_MSG)
        cnt = 0
        for user in res:
            userid = user['UserID']
            for reading_list in ['HaveRead', 'ToRead', 'Reading', 'Abandoned']:
                id_list = get_list(user[reading_list])
                if id_list:
                    cnt += 1
                    new_list = []
                    for item in id_list:
                        item = str(item).strip('"')
                        new_list.append(item)
                    new_set = set(new_list)
                    for item in new_set:
                        cmd = f'INSERT into {reading_list} (UserID, BookID) VALUES (?,?)'
                        db.action(cmd, (userid, item))
        if cnt:
            lazylibrarian.UPDATE_MSG = f"Processed {cnt} reading lists"
            logger.debug(lazylibrarian.UPDATE_MSG)

        db.action('DROP TABLE IF EXISTS temp')
        db.action(
            "CREATE TABLE temp (UserID TEXT UNIQUE, UserName TEXT UNIQUE, Password TEXT, Email TEXT, Name TEXT, "
            "Perms INTEGER DEFAULT 0, CalibreRead TEXT, CalibreToRead TEXT, BookType TEXT, SendTo TEXT, "
            "Last_Login TEXT, Login_Count INTEGER DEFAULT 0, Prefs INTEGER DEFAULT 0, Theme TEXT)")
        db.action(
            "INSERT INTO temp SELECT UserID,UserName,Password,Email,Name,Perms,CalibreRead,CalibreToRead,"
            "BookType,SendTo,Last_Login,Login_Count,Prefs,Theme FROM users")
        db.action('PRAGMA foreign_keys = OFF')
        db.action('DROP TABLE users')
        db.action('ALTER TABLE temp RENAME TO users')
        db.action('PRAGMA foreign_keys = ON')
        db.action('vacuum')

    if not has_column(db, "books", "hc_id"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding hc_id to author, books and users tables'
        upgradelog.write(f"{time.ctime()} v83: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE books ADD COLUMN hc_id TEXT')
        db.action('ALTER TABLE authors ADD COLUMN hc_id TEXT')
        db.action('ALTER TABLE users ADD COLUMN hc_id TEXT')
        wantedlanguages = get_list(CONFIG['IMP_PREFLANG'])
        for lang in ['en', 'eng', 'en-US', 'en-GB']:
            if lang in wantedlanguages:
                wantedlanguages.append('English')
                wantedlanguages = ', '.join(list(set(wantedlanguages)))
                CONFIG.set_str('IMP_PREFLANG', wantedlanguages)
                CONFIG.save_config_and_backup_old()
                break

    if not has_column(db, "readinglists", "Status"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Updating reading tables'
        upgradelog.write(f"{time.ctime()} v84: {lazylibrarian.UPDATE_MSG}\n")
        db.action(
            "CREATE TABLE readinglists (UserID TEXT REFERENCES users (UserID) ON DELETE CASCADE, BookID TEXT, "
            "Status INTEGER DEFAULT 0, Percent INTEGER DEFAULT 0, Msg Text, UNIQUE (UserID,BookID))")
        # status_id = 1 want-to-read, 2 currently_reading, 3 read, 4 owned, 5 dnf
        for tbl in [['toread', 1], ['reading', 2], ['haveread', 3], ['abandoned', 5]]:
            res = db.select(f"SELECT * from {tbl[0]}")
            for item in res:
                db.action("INSERT into readinglists (UserID, BookID, Status) VALUES (?, ?, ?)",
                          (item['UserID'], item['BookID'], tbl[1]), suppress='UNIQUE')
            db.action(f"DROP table {tbl[0]}")

    res = db.match("SELECT sql FROM sqlite_master WHERE type='table' AND name='series'")
    if 'SeriesID INTEGER' in res[0]:
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Updating series,member,seriesauthors tables'
        upgradelog.write(f"{time.ctime()} v85: {lazylibrarian.UPDATE_MSG}\n")
        db.action('PRAGMA foreign_keys = OFF')
        db.action('DROP TABLE IF EXISTS temp')
        db.action(
            "CREATE TABLE temp (SeriesID TEXT UNIQUE, SeriesName TEXT, Status TEXT, Have INTEGER DEFAULT 0, "
            "Total INTEGER DEFAULT 0, Updated INTEGER DEFAULT 0, Reason TEXT)")
        res = db.select("SELECT * from series")
        for item in res:
            db.action("INSERT into temp (SeriesID, SeriesName, Status, Have, Total, Updated, Reason) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (item['Source'] + str(item['SeriesID']), item['SeriesName'], item['Status'],
                       item['Have'], item['Total'], item['Updated'], item['Reason']))
        db.action("DROP TABLE series")
        db.action("ALTER TABLE temp RENAME TO series")
        for table in ['member', 'seriesauthors']:
            res = db.match(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'")
            create_new_table = res[0].replace('SeriesID INTEGER', 'SeriesID TEXT').replace(table, 'temp')
            db.action(create_new_table)
            db.action(f"INSERT into temp SELECT * from {table}")
            db.action(f"DROP TABLE {table}")
            db.action(f"ALTER TABLE temp RENAME TO {table}")

        res = db.select("SELECT SeriesID from series")
        for item in res:
            seriesid = item['SeriesID']
            for table in ['member', 'seriesauthors']:
                cmd = f"UPDATE {table} SET SeriesID=? WHERE SeriesID=?"
                db.action(cmd, (seriesid, seriesid[2:]))
        db.action('PRAGMA foreign_keys = ON')
        db.action('vacuum')

    if not has_column(db, "magazines", "Language"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding language to magazines tables'
        upgradelog.write(f"{time.ctime()} v86: {lazylibrarian.UPDATE_MSG}\n")
        db.action("ALTER TABLE magazines ADD COLUMN Language TEXT default 'en'")

    if not has_column(db, "users", "hc_token"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding hc_token column to users table'
        upgradelog.write(f"{time.ctime()} v87: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE users ADD COLUMN hc_token TEXT')
        if CONFIG.get_str('HC_KEY'):
            db.action("UPDATE users SET hc_token=? WHERE perms=65535 and username='admin'",
                      (CONFIG.get_str('HC_KEY'), ))

    if not has_column(db, "bookauthors", "Role"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding bookauthors table'
        upgradelog.write(f"{time.ctime()} v88: {lazylibrarian.UPDATE_MSG}\n")
        db.action("CREATE TABLE bookauthors (BookID TEXT REFERENCES books (BookID) ON DELETE CASCADE, "
                  "AuthorID TEXT REFERENCES authors (AuthorID) ON DELETE CASCADE, Role INTEGER DEFAULT 0, "
                  "UNIQUE (BookID,AuthorID))")

        selection = db.select("SELECT authors.AuthorID,BookID from books,authors WHERE books.authorid=authors.authorid")
        upgradelog.write(f"{time.ctime()} v88: updating bookauthors from {len(selection)} book entries\n")
        lazylibrarian.UPDATE_MSG = f"Updating bookauthors from {len(selection)} book entries"
        for entry in selection:
            # Add what we currently have as primary author
            db.action('INSERT into bookauthors (AuthorID, BookID, Role) VALUES (?, ?, ?)',
                      (entry['AuthorID'], entry['BookID'], 1), suppress='UNIQUE')

    if not has_column(db, "books", "dnb_id"):
        changes += 1
        lazylibrarian.UPDATE_MSG = 'Adding dnb_id column to books table'
        upgradelog.write(f"{time.ctime()} v89: {lazylibrarian.UPDATE_MSG}\n")
        db.action('ALTER TABLE books ADD COLUMN dnb_id TEXT')

    if changes:
        upgradelog.write(f"{time.ctime()} Changed: {changes}\n")
    logger.debug(f"Schema changes: {changes}")
    return changes
