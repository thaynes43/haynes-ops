# ---------------------------------------------------------------------------
# haynes-ops override (downloads/lazylibrarian, ConfigMap lazylibrarian-resultlist)
#
# This is LazyLibrarian's upstream lazylibrarian/resultlist.py, byte for byte,
# plus this comment block and ONE marked change (search for "haynes-ops fix":
# an import, a helper above find_best_result, and its call in the scoring loop).
#
# PINNED TO UPSTREAM: image docker.io/linuxserver/lazylibrarian:version-40a389ea
#   (LazyLibrarian commit 40a389ea, pyproject version 2026.05.25).
#   sha256 of the unmodified upstream file in that image:
#   dd255ab9b14f52903db34fcc25d04d271cdc619742d52cced9d6972bcb40501d
#
# The fix: find_best_result() scores a release with token_set_ratio, which is
# 100 whenever the release holds every word of the wanted title. For a series
# named after its first book, every later volume's release does, so
# "...-Assistant to the Villain 03-Accomplice to the Villain-..." scored 106
# for book 1 and was grabbed and imported as it (thaynes43/haynesnetwork#688).
# LazyLibrarian holds no series data here (Google Books gives none), so the
# release itself is read: the wanted title, a volume marker with a number
# other than 1, then a different title (token_set_ratio under 90 against the
# wanted title and subtitle) loses 50 points, well under MATCH_RATIO (80).
# A title ending in a number, a file counter, scene group, format words and a
# trailing series name are not read as titles. Replayed over all 3,373
# Processed/Snatched/Seeding rows of the wanted table on 2026-10-05: 153
# changed, each a later volume, novella or single story grabbed for book 1 or
# a collection; none was a genuine grab.
# Extended for thaynes43/haynesnetwork#694: a title that starts with its author
# ("Terry Pratchett's Discworld") is also looked for as its series alone
# ("Discworld 21 - Jingo", "12. Discworld - Witches Abroad"), and when nothing
# follows the volume, the title between the author and the series counts
# ("Unseen Academicals_ Discworld, Book 37"). Replayed over 3,385 rows: 44 more
# changed, all later volumes (30 of them grabbed for that Discworld record).
# Extended for thaynes43/haynesnetwork#738: a release that gives only a volume
# number, no title ("Assistant to the Villain 03 [epub]", "Mistborn 6 (2016)
# MP3"), loses the points too for volume 2 and up, unless the number is a part
# file of a multi-file post ("... 02.mp3"), the subtitle names that volume as
# the book's own ("Inheritance", "Book IV": "Inheritance 04"), or the release
# is a bare "<title> - N" naming no author (a series index after the book's own
# title, "Blonde Faith - 11": 4 of 4 such grabs were the right book). And a
# release that repeats the wanted title after another volume's own title
# ("Mistborn Bk 4 - The Alloy of Law ... Mistborn") is no longer read as this
# book again, unless the subtitle names that volume as the book's own
# ("Inheritance 04 - Inheritance or the Vault of Souls", "Book IV"). Replayed
# over 3,989 rows on 2026-10-06: 10 more changed, every one a later volume or
# novella grabbed for book 1 (Mistborn, Once Upon a Broken Heart, Shift).
# Tests, fixtures and the replay tool: patches/tests/.
#
# BEFORE BUMPING THE IMAGE TAG in helmrelease.yaml: take the new image's
# resultlist.py, and either re-apply the marked change to it (if upstream has
# not fixed the bug) or drop this override (if it has). Never carry this file
# onto a different image unchanged: it would silently revert every other
# upstream change to result scoring.
# ---------------------------------------------------------------------------
#  This file is part of Lazylibrarian.
#
#  Lazylibrarian is free software':'you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  Lazylibrarian is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Lazylibrarian.  If not, see <http://www.gnu.org/licenses/>.

import logging
import re  # haynes-ops fix
import traceback

from rapidfuzz import fuzz

from lazylibrarian import database
from lazylibrarian.common import only_punctuation
from lazylibrarian.config2 import CONFIG
from lazylibrarian.downloadmethods import (
    direct_dl_method,
    irc_dl_method,
    nzb_dl_method,
    tor_dl_method,
)
from lazylibrarian.formatter import check_int, get_list, now, replace_all, unaccented
from lazylibrarian.notifiers import custom_notify_snatch, notify_snatch
from lazylibrarian.providers import get_searchterm
from lazylibrarian.scheduling import SchedulerCommand, schedule_job


def process_result_list(resultlist, book, searchtype, source):
    """ Separated this out into two functions
        1. get the "best" match
        2. if over match threshold, send it to downloader
        This lets us try several searchtypes and stop at the first successful one
        and we can combine results from tor/nzb searches in one task
        Return 0 if not found, 1 if already snatched, 2 if we found it
    """
    match = find_best_result(resultlist, book, searchtype, source)
    if match:
        score = match[0]
        # resultTitle = match[1]
        # newValueDict = match[2]
        # controlValueDict = match[3]
        # dlpriority = match[4]

        if score < CONFIG.get_int('MATCH_RATIO'):
            return 0
        return download_result(match, book)
    return 0


# haynes-ops fix: a series named after its first book ("Assistant to the Villain") makes a later volume's release
# ("...-Assistant to the Villain 03-Accomplice to the Villain-...") contain every word of book 1's title, so
# token_set_ratio scores it 100 for book 1 (thaynes43/haynesnetwork#688). LazyLibrarian holds no series data here,
# so the release itself is read: the wanted title, a volume number other than 1, then a DIFFERENT title.
VOLUME_PENALTY = 50
OTHER_TITLE_RATIO = 90
_NOT_A_TITLE = {'retail', 'ebook', 'audiobook', 'epub', 'mobi', 'azw3', 'azw', 'pdf', 'mp3', 'm4b', 'm4a', 'flac',
                'unabridged', 'abridged', 'us', 'uk', 'eng', 'en', 'nmr', 'kbps', 'yenc', 'rar', 'par2', 'pars',
                'nfo', 'sfv', 'nzb', 'zip', 'jpg', 'txt'}


def names_other_volume(release, title, subtitle, author):
    """ Return (volume, other title) when release reads '<title> NN', '[<title> NN]', '<title> #N', '<title> Book N'
        or '<title>, Vol N' (N not 1), with a title unlike the wanted title and subtitle after it (or, when nothing
        follows, between the author and it: "Pratchett - Unseen Academicals_ Discworld, Book 37"). A title that starts
        with its author ("Terry Pratchett's Discworld") is also looked for as its series alone ("Discworld 21 - Jingo",
        "12. Discworld - Witches Abroad"). When no title at all goes with the volume ("<title> 03 [epub]"), return
        (volume, '') for N >= 2, unless the number is a file's part counter ("<title> 02.mp3"), the book's own volume
        by its subtitle ("Inheritance", "Book IV": "Inheritance 04"), or a bare "<title> - N" that names no author
        (a series index after the book's own title: "Blonde Faith - 11"). Otherwise None. """
    def norm(text):
        return unaccented(text or '').lower().replace("'", '').replace('.', ' ')

    def words(text):
        return re.findall(r'[a-z0-9]+', norm(text))

    wanted = words(title)
    if not wanted or wanted[-1].isdigit():  # a title ending in a number ("Fahrenheit 451") is left alone
        return None
    writer = set(words(author))
    writer |= {w + 's' for w in writer}
    series = wanted
    while series and series[0] in writer:
        series = series[1:]
    # a multi-file post's [03/21] counter and a trailing scene group are not part of the title
    dotted = re.split(r'[\[(]\s*\d+\s*[_/]\s*\d+\s*[\])]', unaccented(release or '').lower().replace("'", ''))[0]
    release = dotted.replace('.', ' ')  # norm(), keeping the dotted copy aligned for "12. Discworld"
    release = re.sub(r'((?:epub|ebook|mobi|azw3|pdf|retail|mp3|m4b|audiobook|web)\s*)-[a-z0-9]+$', r'\1', release)
    names = [(wanted, False)]
    if series and series != wanted and not series[-1].isdigit():
        names.append((series, True))
    match = listed = None
    for name, series_only in names:
        name = r'(?<![a-z0-9])' + r'[\s_,:;!?&-]+'.join(name)
        match = re.search(name + r'(?P<sep>[\s_\]),:-]*(?:(?:book|bk|vol|volume)\s*|#\s*)?)(?P<vol>\d{1,2})'
                          r'(?![a-z0-9]|\s*(?:of\b|[-&_/]\s*\d))', release)
        if not match and series_only:  # a list number, "12. Discworld - Witches Abroad", not a count ("41 Discworld")
            match = listed = re.search(r'(?<![a-z0-9.])(?P<vol>\d{1,2})\.\s*' + name + r'(?![a-z0-9])', dotted)
        if match and int(match.group('vol')) != 1:
            break
    if not match or int(match.group('vol')) == 1:
        return None
    volume = int(match.group('vol'))
    skip = _NOT_A_TITLE | set(get_list(CONFIG['EBOOK_TYPE'])) | set(get_list(CONFIG['AUDIOBOOK_TYPE'])) | writer

    def title_words(text):
        return ' '.join(w for w in words(text)
                        if w not in skip and not w.isdigit() and not re.fullmatch(r'(?:v|part|vol|cd)\d+', w))
    by = '|'.join(w for w in writer if len(w) > 2)
    before = re.split(r'(?<![a-z0-9])(?:%s)(?![a-z0-9])' % by, release[:match.start()]) if by else []
    other = title_words(release[match.end():]) or (title_words(before[-1]) if len(before) > 1 and not listed else '')
    if not other:  # nothing but the volume: "Assistant to the Villain 03 [epub]" (thaynes43/haynesnetwork#738)
        if listed or volume < 2 or _PART_FILE.match(dotted, match.end('vol')) or own_volume(subtitle) == volume:
            return None
        if '-' in match.group('sep') and not (by and re.search(r'(?<![a-z0-9])(?:%s)(?![a-z0-9])' % by, release)):
            return None
        return volume, ''
    own = own_volume(subtitle)
    subtitle = ' '.join(w for w in words(subtitle) if w not in _NOT_A_TITLE and not w.isdigit() and w not in
                        ('book', 'bk', 'vol', 'volume', 'part', 'novel', 'one', 'two', 'three', 'four', 'five'))
    if other.endswith('series') or (subtitle and fuzz.token_set_ratio(subtitle, other) >= OTHER_TITLE_RATIO):
        return None  # a series name, or this book by its subtitle
    if fuzz.token_set_ratio(' '.join(wanted), other) >= OTHER_TITLE_RATIO:
        # this book again ("Inheritance 04 - Inheritance"), unless more than its title follows, and the book does
        # not call itself that volume: "Mistborn Bk 4 - The Alloy of Law - ... Mistborn" is not "Mistborn" (#738)
        extra = ' '.join(w for w in other.split() if w not in wanted)
        if not extra or own == volume or fuzz.token_set_ratio(' '.join(wanted), extra) >= OTHER_TITLE_RATIO:
            return None
    return volume, other


# a number that names a file of a multi-file post, not a volume: "<title> 02.mp3", "<title> 0.jpg"
_PART_FILE = re.compile(r'\.(?:mp3|m4a|m4b|flac|ogg|aac|wma|jpe?g|png|par2|nfo|sfv|rar|zip|7z|r\d\d)(?![a-z0-9])')
_NUMBER_WORDS = {w: n for n, w in enumerate('one two three four five six seven eight nine ten eleven twelve'.split(), 1)}
_ROMAN = {r: n for n, r in enumerate('i ii iii iv v vi vii viii ix x xi xii xiii xiv xv xvi xvii xviii xix xx'.split(), 1)}


def own_volume(subtitle):
    """ The volume a subtitle gives the book itself ("Book IV", "Book 4", "Book Four", "Volume 2", "#4"), else None. """
    found = re.search(r'(?<![a-z0-9])(?:book|bk|volume|vol|#)\s*(\d{1,2}|[a-z]+)(?![a-z0-9])',
                      unaccented(subtitle or '').lower().replace('.', ' '))
    if not found:
        return None
    n = found.group(1)
    return int(n) if n.isdigit() else _ROMAN.get(n) or _NUMBER_WORDS.get(n)


def find_best_result(resultlist, book, searchtype, source):
    """ resultlist: collated results from search providers
        book:       the book we want to find
        searchtype: book, magazine, shortbook, audiobook etc.
        source:     nzb, tor, rss, irc, direct
        return:     highest scoring match, or None if no match
    """
    # noinspection PyBroadException
    logger = logging.getLogger(__name__)
    fuzzlogger = logging.getLogger('special.fuzz')
    db = database.DBConnection()
    highest = None
    try:
        # '0': '', '1': '', '2': '', '3': '', '4': '', '5': '', '6': '', '7': '', '8': '', '9': '',
        dictrepl = {'...': '', '.': ' ', ' & ': ' ', ' = ': ' ', '?': '', '$': 's', ' + ': ' ', '"': '',
                    ',': ' ', '*': '', '(': '', ')': '', '[': '', ']': '', '#': '', '\'': '',
                    ':': '', '!': '', '-': ' ', r'\s\s': ' '}

        dic = {'...': '', '.': ' ', ' & ': ' ', ' = ': ' ', '?': '', '$': 's', ' + ': ' ', '"': '',
               ',': '', '*': '', ':': '.', ';': '', '\'': ''}

        if source == 'rss':
            author, title = get_searchterm(book, searchtype)
        else:
            author = unaccented(replace_all(book['authorName'], dic), only_ascii=False)
            title = unaccented(replace_all(book['bookName'], dic), only_ascii=False)

        if 'short' in searchtype and '(' in title:
            title = title.split('(')[0].strip()

        if book['library'] == 'AudioBook':
            reject_list = get_list(CONFIG['REJECT_AUDIO'], ',')
            maxsize = CONFIG.get_int('REJECT_MAXAUDIO')
            minsize = CONFIG.get_int('REJECT_MINAUDIO')
            auxinfo = 'AudioBook'

        else:  # elif book['library'] == 'eBook':
            reject_list = get_list(CONFIG['REJECT_WORDS'], ',')
            maxsize = CONFIG.get_int('REJECT_MAXSIZE')
            minsize = CONFIG.get_int('REJECT_MINSIZE')
            auxinfo = 'eBook'

        if source == 'nzb':
            prefix = 'nzb'
        else:  # rss and direct providers return same names as torrents
            prefix = 'tor_'

        logger.debug(f'Searching {len(resultlist)} {source} results for best {auxinfo} match')
        matches = []
        ignored_messages = []
        for res in resultlist:
            result_title = unaccented(replace_all(res[f"{prefix}title"], dictrepl),
                                      only_ascii=False).strip()
            result_title = ' '.join(result_title.split())  # remove extra whitespace
            only_title = result_title.replace(author, '')
            if not only_title or only_punctuation(only_title):
                book_match = fuzz.token_set_ratio(title, result_title)
            else:
                book_match = fuzz.token_set_ratio(title.replace(author, ''), only_title)
            if 'booksearch' in res and res['booksearch'] == 'bibliotik':
                # bibliotik only returns book title, not author name
                fuzzlogger.debug("bibliotik, ignoring author fuzz")
                author_match = 100
            else:
                author_match = fuzz.token_set_ratio(author, result_title)

            fuzzlogger.debug(f"{source.upper()} author/book Match: {author_match}/{book_match} {result_title} "
                             f"at {res[prefix + 'prov']}")

            rejected = False

            url = res[f"{prefix}url"]
            if not url:
                rejected = True
                logger.debug(f"Rejecting {result_title}, no URL found")

            if not rejected and CONFIG.get_bool('BLACKLIST_FAILED'):
                cmd = "SELECT * from wanted WHERE NZBurl=? and Status='Failed'"
                args = (url,)
                if res.get('tor_type', '') == 'irc':
                    cmd += " and NZBTitle=?"
                    args += (res['tor_title'],)
                blacklisted = db.match(cmd, args)
                if blacklisted:
                    logger.debug(f"Rejecting {res[prefix + 'title']}, url blacklisted (Failed) at "
                                 f"{blacklisted['NZBprov']}")
                    rejected = True
                if not rejected:
                    blacklisted = db.match("SELECT * from wanted WHERE NZBprov=? and NZBtitle=? "
                                           "and Status='Failed'",
                                           (res[f"{prefix}prov"], res[f"{prefix}title"]))
                    if blacklisted:
                        logger.debug(f"Rejecting {res[prefix + 'title']}, title blacklisted (Failed) at "
                                     f"{blacklisted['NZBprov']}")
                        rejected = True

            if not rejected and CONFIG.get_bool('BLACKLIST_PROCESSED'):
                cmd = "SELECT * from wanted WHERE NZBurl=?"
                args = (url,)
                if res.get('tor_type', '') == 'irc':
                    cmd += " and NZBTitle=?"
                    args += (res['tor_title'],)
                blacklisted = db.match(cmd, args)
                if blacklisted:
                    logger.debug(f"Rejecting {res[prefix + 'title']}, url blacklisted ({blacklisted['Status']}) "
                                 f"at {blacklisted['NZBprov']}")
                    rejected = True
                if not rejected:
                    blacklisted = db.match('SELECT * from wanted WHERE NZBprov=? and NZBtitle=?',
                                           (res[f"{prefix}prov"], res[f"{prefix}title"]))
                    if blacklisted:
                        logger.debug(f"Rejecting {res[prefix + 'title']}, title blacklisted ({blacklisted['Status']}) "
                                     f"at {blacklisted['NZBprov']}")
                        rejected = True

            if not rejected and source == 'rss':
                if searchtype in ['book', 'shortbook'] and 'E' not in res['types']:
                    rejected = True
                    ignore_msg = f"Ignoring {res[prefix + 'prov']} for eBook"
                    if ignore_msg not in ignored_messages:
                        ignored_messages.append(ignore_msg)
                        logger.debug(ignore_msg)
                if 'audio' in searchtype and 'A' not in res['types']:
                    rejected = True
                    ignore_msg = f"Ignoring {res[prefix + 'prov']} for AudioBook"
                    if ignore_msg not in ignored_messages:
                        ignored_messages.append(ignore_msg)
                        logger.debug(ignore_msg)
                if 'mag' in searchtype and 'M' not in res['types']:
                    rejected = True
                    ignore_msg = f"Ignoring {res[prefix + 'prov']} for Magazine"
                    if ignore_msg not in ignored_messages:
                        ignored_messages.append(ignore_msg)
                        logger.debug(ignore_msg)

            if not rejected:
                if source == 'irc':
                    if not url.startswith('!'):
                        rejected = True
                elif res[prefix + 'prov'] in ['zlibrary', 'soulseek']:
                    if '^' not in url:
                        rejected = True
                elif res[prefix + 'prov'] == 'annas':
                    # annas gives us an id in hex, verify we got hex digits
                    try:
                        _ = int(url, 16)
                    except ValueError:
                        rejected = True
                elif not url.startswith('http') and not url.startswith('magnet'):
                    rejected = True
                if rejected:
                    logger.debug(f"Rejecting {res[prefix + 'title']}, invalid URL [{url}]")

            if not rejected:
                for word in reject_list:
                    if word in get_list(result_title.lower()) and word not in get_list(author.lower()) \
                            and word not in get_list(title.lower()):
                        rejected = True
                        logger.debug(f"Rejecting {result_title}, contains {word}")
                        break

            size_temp = check_int(res[f"{prefix}size"], 1000)  # Need to cater for when this is NONE (Issue 35)
            size = round(float(size_temp) / 1048576, 2)

            if not rejected and maxsize and size > maxsize:
                rejected = True
                logger.debug(f"Rejecting {result_title}, too large ({size}Mb)")

            if not rejected and minsize and size < minsize:
                rejected = True
                logger.debug(f"Rejecting {result_title}, too small ({size}Mb)")

            if not rejected:
                bookid = book['bookid']

                if source == 'nzb':
                    mode = res.get('nzbmode', '')  # nzb, torznab
                else:
                    mode = res.get('tor_type', '')  # torrent, magnet, nzb(from rss), direct, irc

                control_value_dict = {"NZBurl": url}
                new_value_dict = {
                    "NZBprov": res[f"{prefix}prov"],
                    "BookID": bookid,
                    "NZBdate": now(),  # when we asked for it
                    "NZBsize": size,
                    "NZBtitle": res[f"{prefix}title"],  # was resultTitle,
                    "NZBmode": mode,
                    "AuxInfo": auxinfo,
                    "Label": res.get('label', ''),
                    "Status": "Matched"
                }
                if source == 'irc':
                    new_value_dict['NZBprov'] = res['tor_feed']
                    new_value_dict['NZBtitle'] = res[f"{prefix}title"]

                if author_match >= CONFIG.get_int('MATCH_RATIO'):
                    score = book_match
                else:
                    score = (book_match + author_match) / 2  # as a percentage
                # lose a point for each unwanted word in the title so we get the closest match
                # but for rss ignore anything at the end in square braces [keywords, genres etc]
                if source == 'rss':
                    wordlist = get_list(result_title.rsplit('[', 1)[0].lower())
                else:
                    wordlist = get_list(result_title.lower())
                words = [x for x in wordlist if x not in get_list(author.lower())]
                words = [x for x in words if x not in get_list(title.lower())]
                typelist = ''

                if new_value_dict['AuxInfo'] == 'eBook':
                    words = [x for x in words if x not in get_list(CONFIG['EBOOK_TYPE'])]
                    typelist = get_list(CONFIG['EBOOK_TYPE'])
                elif new_value_dict['AuxInfo'] == 'AudioBook':
                    words = [x for x in words if x not in get_list(CONFIG['AUDIOBOOK_TYPE'])]
                    typelist = get_list(CONFIG['AUDIOBOOK_TYPE'])

                score -= len(words)
                # prioritise titles that include the ebook types we want
                # add more points for booktypes nearer the left in the list
                # eg if epub, mobi, pdf  add 3 points if epub found, 2 for mobi, 1 for pdf
                booktypes = [x for x in wordlist if x in typelist]
                if booktypes:
                    typelist = list(reversed(typelist))
                    for item in booktypes:
                        for i in [i for i, x in enumerate(typelist) if x == item]:
                            score += i + 1

                # now do the same for words in the "preferred words" list"
                preferwords = get_list(CONFIG['PREFER_WORDS'])
                if preferwords:
                    preferwords = list(reversed(preferwords))
                    for word in wordlist:
                        for i in [i for i, x in enumerate(preferwords) if x == word]:
                            score += i + 1

                # haynes-ops fix: a later volume of a series named after the wanted book
                other_volume = names_other_volume(res[f"{prefix}title"], title, book.get('bookSub'), author)
                if other_volume:
                    score -= VOLUME_PENALTY
                    logger.debug(f"{result_title} is volume {other_volume[0]} ({other_volume[1]}) of a series "
                                 f"named {title}: score {round(score, 2)}")

                matches.append([score, new_value_dict, control_value_dict, res['priority']])

        if matches:
            highest = max(matches, key=lambda s: (s[0], s[3]))
            score = highest[0]
            new_value_dict = highest[1]
            # controlValueDict = highest[2]
            dlpriority = highest[3]

            if score < CONFIG.get_int('MATCH_RATIO'):
                logger.info(
                    f"Nearest match ({round(score, 2)}%): {new_value_dict['NZBtitle']} using {searchtype} search for "
                    f"{book['authorName']} {book['bookName']}")
            else:
                logger.info(
                    f"Best match ({round(score, 2)}%): {new_value_dict['NZBtitle']} using {searchtype} search, "
                    f"{new_value_dict['NZBprov']} priority {dlpriority}")
        else:
            logger.debug(f"No {source} found for [{book['searchterm']}] using searchtype {searchtype}")
    except Exception:
        logger.error(f'Unhandled exception in find_best_result: {traceback.format_exc()}')

    db.close()
    return highest


def download_result(match, book):
    """ match:  best result from search providers
        book:   book we are downloading (needed for reporting author name)
        return: 0 if failed to snatch
                1 if already snatched
                2 if we snatched it
    """
    # noinspection PyBroadException
    logger = logging.getLogger(__name__)
    db = database.DBConnection()
    try:
        new_value_dict = match[1]
        control_value_dict = match[2]

        # It's possible to get book and wanted tables "Snatched" status out of sync
        # for example if a user marks a book as "Wanted" after a search task snatches it and before postprocessor runs
        # so check status in both tables here
        snatched = db.match("SELECT BookID from wanted WHERE BookID=? and AuxInfo=? and Status='Snatched'",
                            (new_value_dict["BookID"], new_value_dict["AuxInfo"]))
        if snatched:
            logger.debug(
                f"{new_value_dict['AuxInfo']} {book['authorName']} {book['bookName']} "
                f"already marked snatched in wanted table")
            return 1  # someone else already found it

        if new_value_dict["AuxInfo"] == 'eBook':
            snatched = db.match("SELECT BookID from books WHERE BookID=? and Status='Snatched'",
                                (new_value_dict["BookID"],))
        else:
            snatched = db.match("SELECT BookID from books WHERE BookID=? and AudioStatus='Snatched'",
                                (new_value_dict["BookID"],))
        if snatched:
            logger.debug(
                f"{new_value_dict['AuxInfo']} {book['authorName']} {book['bookName']} "
                f"already marked snatched in book table")
            return 1  # someone else already found it

        db.upsert("wanted", new_value_dict, control_value_dict)
        label = new_value_dict.get('Label', '')
        if new_value_dict['NZBmode'] == 'direct':
            snatch, res = direct_dl_method(new_value_dict["BookID"], new_value_dict["NZBtitle"],
                                           control_value_dict["NZBurl"], new_value_dict["AuxInfo"],
                                           new_value_dict['NZBprov'])
        elif new_value_dict['NZBmode'] == 'irc':
            snatch, res = irc_dl_method(new_value_dict["BookID"], new_value_dict["NZBtitle"],
                                        control_value_dict["NZBurl"], new_value_dict["AuxInfo"],
                                        new_value_dict['NZBprov'])
        elif new_value_dict['NZBmode'] in ["torznab", "torrent", "magnet"]:
            snatch, res = tor_dl_method(new_value_dict["BookID"], new_value_dict["NZBtitle"],
                                        control_value_dict["NZBurl"], new_value_dict["AuxInfo"], label,
                                        new_value_dict['NZBprov'])
        elif new_value_dict['NZBmode'] == 'nzb':
            snatch, res = nzb_dl_method(new_value_dict["BookID"], new_value_dict["NZBtitle"],
                                        control_value_dict["NZBurl"], new_value_dict["AuxInfo"], label)
        else:
            res = f"Unhandled NZBmode [{new_value_dict['NZBmode']}] for {control_value_dict['NZBurl']}"
            logger.error(res)
            snatch = 0

        if snatch:
            logger.info(
                f"Downloading {new_value_dict['AuxInfo']} {new_value_dict['NZBtitle']} from "
                f"{new_value_dict['NZBprov']}")
            custom_notify_snatch(f"{new_value_dict['BookID']} {new_value_dict['AuxInfo']}")
            notify_snatch(
                f"{new_value_dict['AuxInfo']} {new_value_dict['NZBtitle']} from "
                f"{CONFIG.disp_name(new_value_dict['NZBprov'])} at {now()}")
            # at this point we could add NZBprov to the blocklist with a short timeout, a second or two?
            # This would implement a round-robin search system. Blocklist with an incremental counter.
            # If number of active providers == number blocklisted, so no unblocked providers are left,
            # either sleep for a while, or unblock the one with the lowest counter.
            schedule_job(SchedulerCommand.START, target='PostProcessor')
            return 2  # we found it
        db.action("UPDATE wanted SET status='Failed',DLResult=? WHERE NZBurl=?",
                  (res, control_value_dict["NZBurl"]))
        return 0
    except Exception:
        logger.error(f'Unhandled exception in download_result: {traceback.format_exc()}')
        return 0
    finally:
        db.close()
