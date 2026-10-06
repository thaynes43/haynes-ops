"""resultlist.py: a later volume of a series named after the wanted book loses VOLUME_PENALTY (haynes-ops #3368,
#3369; thaynes43/haynesnetwork#688, #694); a release that names another language loses LANGUAGE_PENALTY for an
English book, and a Failed row blocks its release under any spelling (thaynes43/haynesnetwork#755)."""
from hops_tests.hops_base import OverlayTestCase, load_fixture, score, set_config
from lazylibrarian import resultlist

ATV = ('Assistant to the Villain', 'A Cozy Fantasy Romantic Comedy from a TikTok Sensation', 'Hannah Nicole Maehrer')
ATV3 = ('Accomplice to the Villain', 'A Cozy Fantasy Romantic Comedy', 'Hannah Nicole Maehrer')
TPD = ("Terry Pratchett's Discworld", '', 'Terry Pratchett')
INH = ('Inheritance', 'Book IV', 'Christopher Paolini')
MIST = ('Mistborn', 'The Final Empire', 'Brandon Sanderson')

# (library, (title, subtitle, author), release, expected volume or None, (upstream score, patched score) or None)
CASES = [
    # haynesnetwork#688 / #686: book 3's release was grabbed and imported as book 1
    ('eBook', ATV, 'Hannah Nicole Maehrer-Assistant to the Villain 03-Accomplice to the Villain-azw3 epub mobi',
     3, (106.0, 56.0)),
    ('eBook', ATV, 'Hannah Nicole Maehrer-Assistant to the Villain 02-Apprentice to the Villain-azw3 epub mobi',
     2, (106.0, 56.0)),
    ('eBook', ATV, 'Hannah Nicole Maehrer - Assistant to the Villain', None, (100.0, 100.0)),
    ('eBook', ATV, 'Hannah Nicole Maehrer - [Assistant to the Villain 01] - Assistant to the Villain (retail) (epub)',
     None, (103.0, 103.0)),
    ('eBook', ATV3, 'Hannah Nicole Maehrer-Assistant to the Villain 03-Accomplice to the Villain-azw3 epub mobi',
     None, (106.0, 106.0)),
    # haynesnetwork#694: "<Author>'s <Series>" is also looked for as the series alone
    ('AudioBook', TPD, 'Terry Pratchett - Discworld 21 - Jingo MP3', 21, (91.0, 41.0)),
    ('eBook', TPD, 'Terry Pratchett - [Discworld 35] - Wintersmith (epub)', 35, (92.0, 42.0)),
    ('AudioBook', TPD, 'Terry Pratchett - 12. Discworld - Witches Abroad (1991) MP3', 12, (89.0, 39.0)),
    ('AudioBook', TPD, 'Terry Pratchett - Unseen Academicals_ Discworld, Book 37 (2009) MP3', 37, (88.0, 38.0)),
    ('AudioBook', TPD, 'Terry Pratchett - The Science of Discworld (1999) MP3', None, (89.0, 89.0)),
    ('eBook', TPD, 'Terry Pratchett - 41 Discworld Novels (epub)', None, None),  # a count, not a list number
    # the genuine Discworld books keep their scores
    ('AudioBook', ('Jingo', '', 'Terry Pratchett'), 'Terry Pratchett - Discworld 21 - Jingo MP3', None, None),
    ('eBook', ('Wintersmith', '', 'Terry Pratchett'), 'Terry Pratchett - [Discworld 35] - Wintersmith (epub)',
     None, None),
    ('AudioBook', ('Unseen Academicals', '', 'Terry Pratchett'),
     'Terry Pratchett - Unseen Academicals_ Discworld, Book 37 (2009) MP3', None, None),
    # false-positive guards
    ('eBook', INH, 'Christopher Paolini - Inheritance 04 - Inheritance v5.epub', None, (102.0, 102.0)),
    ('eBook', INH, 'Paolini, Christopher - Inheritance 03 - Brisingr', 3, (98.0, 48.0)),
    ('eBook', ('Fahrenheit 451', 'A Novel', 'Ray Bradbury'),
     'Ray Bradbury - Fahrenheit 451 (60th Anniversary) (epub) (epub)', None, (106.0, 106.0)),
    ('eBook', ('Fahrenheit 451', 'A Novel', 'Ray Bradbury'), 'Ray Bradbury - Fahrenheit 451 02 - Something Else',
     None, None),  # a title ending in a number is left alone
    ('AudioBook', ('Grave Secret', '', 'Charlaine Harris'), 'Grave secret -book 4-Harper Connelly series',
     None, None),  # the series name after the volume
    ('eBook', ('Chroniken der Unterwelt', '', 'Cassandra Clare'),
     'Clare Cassandra - Chroniken der Unterwelt 01 - City of Bones epub', None, (100.0, 100.0)),
    ('eBook', ('Chroniken der Unterwelt', '', 'Cassandra Clare'),
     'Clare Cassandra - Chroniken der Unterwelt 03 - City of Glass epub', 3, (100.0, 50.0)),
    ('eBook', MIST, 'Brandon Sanderson - Mistborn 01 - The Final Empire epub', None, None),
    ('eBook', MIST, 'Brandon Sanderson - [Mistborn 01-03] - The Mistborn Trilogy Omnibus (retail) (epub)', None, None),
    ('eBook', ('Shatter Me', '', 'Tahereh Mafi'), 'Tahereh.Mafi-[Shatter.Me.01.5-06.5].azw3.epub.mobi', None, None),
    # haynesnetwork#738: only a volume number, no title, still names another volume ...
    ('eBook', ATV, 'Hannah Nicole Maehrer - Assistant to the Villain 03 [epub]', 3, None),
    ('eBook', ATV, 'Assistant to the Villain Book 3 (retail) (epub)', 3, None),
    ('eBook', ATV, 'Assistant to the Villain #3', 3, None),
    ('eBook', ATV, 'Hannah Nicole Maehrer - Assistant to the Villain - 03', 3, None),
    ('eBook', ATV, 'Hannah Nicole Maehrer - [Assistant to the Villain 01] (epub)', None, None),
    ('AudioBook', MIST, 'Brandon Sanderson - Mistborn 6 (2016) MP3', 6, (101.0, 51.0)),
    ('AudioBook', TPD, 'Terry Pratchett - Discworld 21 (2001) MP3', 21, None),
    # ... but not the book's own series index after its own title, a part file, or the volume its subtitle names
    ('eBook', ('Blonde Faith', '', 'Walter Mosley'), 'Blonde Faith - 11', None, None),
    ('eBook', ATV, 'Assistant to the Villain - 03', None, None),
    ('AudioBook', ('Dark Rivers of the Heart', '', 'Dean Koontz'),
     'NMR_Dean Koontz - Dark Rivers of the Heart 02.mp3', None, None),
    ('AudioBook', ('Winter of the World', 'Book Two of the Century Trilogy', 'Ken Follett'),
     'nmr Ken Follett - Winter of the World 0.jpg 01_16', None, None),
    ('eBook', INH, 'Christopher Paolini - Inheritance 04', None, None),
    ('AudioBook', ('White Sand', 'Volume 2', 'Brandon Sanderson'), 'White Sand 02', None, None),
    ('eBook', ('Fahrenheit 451', 'A Novel', 'Ray Bradbury'), 'Ray Bradbury - Fahrenheit 451 2', None, None),
    # haynesnetwork#738: the wanted title repeated after another volume's own title is not this book again ...
    ('AudioBook', MIST, 'Brandon Sanderson - Mistborn Bk 4 - The Alloy of Law NMR 56 kbps - Brandon Sanderson - Mistborn',
     4, None),
    ('eBook', MIST, 'Brandon Sanderson - [Mistborn 03.5] - Mistborn-Secret History (retail) (epub)', 3, (100.0, 50.0)),
    ('eBook', ('Once Upon a Broken Heart', '', 'Stephanie Garber'),
     'Stephanie Garber - [Once Upon a Broken Heart 03] - A Curse for True Love (mobi) - Once Upon a Broken Heart',
     3, None),
    ('eBook', ('Shift', '', 'Hugh Howey'), 'Hugh Howey - [Silo 02-Shift 02] - Second Shift-Order (retail) (mobi)',
     2, (98.0, 48.0)),
    # ... unless the subtitle says the book is that volume
    ('eBook', INH, 'Paolini, Christopher - Inheritance 04 - Inheritance or the Vault of Souls (v5) [epub]', None, None),
    ('eBook', ("Dean Koontz's Frankenstein", 'City of night. Book two', 'Dean Koontz'),
     'Dean Koontz - [Frankenstein 02] - City of Night - Ed Gorman (azw3)', None, None),
    # ... and a narrator credit or edition words after the repeated title are not another volume's title
    ('AudioBook', ('Inheritance', '', 'Christopher Paolini'),
     'Christopher Paolini - Inheritance 04 - Inheritance (Read by Gerard Doyle) MP3', None, None),
    ('eBook', ('Inheritance', '', 'Christopher Paolini'),
     'Christopher Paolini - Inheritance 04 - Inheritance Deluxe Edition (epub)', None, None),
]


class VolumePenaltyTest(OverlayTestCase):

    def test_named_cases(self):
        for library, (title, sub, author), release, volume, scores in CASES:
            with self.subTest(title=title, release=release):
                upstream, _ = score(library, title, sub, author, release, penalty=False)
                patched, found = score(library, title, sub, author, release)
                self.assertEqual(found[0] if found else None, volume)
                if scores:
                    self.assertEqual((upstream, patched), scores)
                elif volume:
                    self.assertLess(patched, 80)
                else:
                    self.assertEqual(patched, upstream)

    def test_replay_fixture(self):
        """Rows of the 2026-10-06 replay over the live wanted table (anonymised): every release the penalty caught,
        and every release that names the wanted title and a number but must not be caught."""
        rows = load_fixture('resultlist_replay.json')
        self.assertGreater(len(rows), 250)
        for row in rows:
            with self.subTest(title=row['title'], release=row['release']):
                args = (row['library'], row['title'], row['sub'], row['author'], row['release'])
                upstream, _ = score(*args, penalty=False)
                patched, found = score(*args)
                self.assertEqual(list(found) if found else None, row['volume'])
                self.assertEqual((upstream, patched), (row['upstream'], row['patched']))


POTTER = ('Israel Potter', '', 'Herman Melville')
POTTER_DK = 'Herman.Melville.Pierre.And.Israel.Potter.2014.DANiSH.RETAiL.ePub.eBOOK-DECiPHER-mqIO'
SERPENT = ('The Serpent and the Wings of Night', '', 'Carissa Broadbent')

# (library, (title, subtitle, author), BookLang, release, the language tag it names, or None)
LANGUAGE_CASES = [
    # haynesnetwork#755: the Danish Israel Potter, and the other tag forms of the grab history
    ('eBook', POTTER, 'en', POTTER_DK, 'DANiSH'),
    ('eBook', ('The Firm', '', 'John Grisham'), 'en', 'John.Grisham.The.Firm.2021.SWEDiSH.RETAiL.ePub.eBOOK-DECiPHER-97yR',
     'SWEDiSH'),
    ('AudioBook', ('Queen Charlotte', '', 'Julia Quinn'), 'en',
     'Julia Quinn - Queen Charlotte-AUDiOBOOK-WEB-SE-2023-CRAViNGS iNT', 'SE'),
    ('AudioBook', ('Wild Cards I', '', 'George R. R. Martin'), 'en',
     'Redigeret af George R R Martin - Wild Cards 3 Jokers Wild-AUDiOBOOK-WEB-DK-2022-CRAViNGS iNT', 'DK'),
    ('AudioBook', ('Fractal Noise', '', 'Christopher Paolini'), 'en',
     'Christopher Paolini-Fractal Noise-2MP3CD-DE-2024-FKKAuDiOBooK', 'DE'),
    ('eBook', ('Sword Catcher', '', 'Cassandra Clare'), 'en', 'Clare, Cassandra - Sword Catcher 01 - Zwaardvanger-GP-EB-NL',
     'NL'),
    ('AudioBook', ('Breaking Dawn', '', 'Stephenie Meyer'), 'en', 'Stephenie Meyer-Breaking Dawn (2008) NL Audiobook(mp3) DMT',
     'NL'),
    ('eBook', ('Clockwork Prince', '', 'Cassandra Clare'), 'en', 'Clockwork Prince by Cassandra Clare [GER / EPUB]', 'GER'),
    ('AudioBook', ('Katabasis', '', 'R. F. Kuang'), 'en', 'R. F. Kuang - Katabasis [French]', 'French'),
    ('eBook', ('Eclipse', '', 'Stephenie Meyer'), 'en-GB', 'Eclipse by Stephenie Meyer [SPA / PDF]', 'SPA'),
    # the German Serpent and the Wings of Night: a format between source and language, and the mojibake edition word
    ('AudioBook', SERPENT, 'en',
     'Carissa Broadbent-Crowns of Nyaxia 01 - The Serpent and the Wings of Night-AUDiOBOOK-WEB-MP3-DE-2024-PaZ', 'DE'),
    ('AudioBook', SERPENT, 'en', 'Carissa Broadbent - Crowns of Nyaxia 01 - The Serpent and the Wings of Night '
     '(Ungek\u00c3\u00bcrzt) MP3', 'Ungekurzt'),  # the indexer's mojibake of "Ungekürzt"
    ('AudioBook', SERPENT, 'en', 'Carissa Broadbent - The Serpent and the Wings of Night (Ungek\u00fcrzt) H\u00f6rbuch',
     'Ungekurzt'),
    # a book in another language, or one LazyLibrarian holds no language for, is left alone
    ('eBook', POTTER, 'da', POTTER_DK, None),
    ('eBook', POTTER, None, POTTER_DK, None),
    ('AudioBook', ('Murtagh - Eine dunkle Bedrohung', '', 'Christopher Paolini'), 'de',
     'Murtagh: Eine dunkle Bedrohung by Christopher Paolini [GER / MP3]', None),
    # English releases, and language words that are part of a title, are not tags
    ('eBook', ("Debbie Doesn't Do It Anymore", '', 'Walter Mosley'), 'en',
     "Debbie Doesn't Do It Anymore by Walter Mosley [ENG / EPUB]", None),
    ('AudioBook', ('Queen Charlotte', '', 'Julia Quinn'), 'en', 'Julia Quinn - Queen Charlotte-AUDiOBOOK-WEB-EN-2023-GROUP',
     None),
    ('AudioBook', ('Learn French', '', 'Michel Thomas'), 'en', 'Michel Thomas - Learn French Audiobook (mp3)', None),
    ('eBook', ('The Danish Girl', '', 'David Ebershoff'), 'en', 'David.Ebershoff.The.Danish.Girl.2015.RETAiL.ePub.eBOOK-NODE',
     None),
    ('eBook', ('The American Roommate Experiment', '', 'Elena Armas'), 'en',
     'Elena Armas - [Spanish Love Deception 02] - The American Roommate Experiment (epub)', None),
    ('eBook', ('It', '', 'Stephen King'), 'en', 'Stephen.King.IT.1986.RETAIL.EPUB.eBook-BitBook', None),
    ('eBook', ('The Russian Cage', '', 'Charlaine Harris'), 'en', 'Charlaine Harris - Gunnie Rose 03 - The Russian Cage',
     None),
    ('eBook', ("Percy Jackson's Greek Gods", '', 'Rick Riordan'), 'en', 'Rick Riordan - Percy Jacksons Greek Gods (epub)',
     None),
    ('AudioBook', ('Twilight', '', 'Stephenie Meyer'), 'en', 'Stephenie Meyer - Twilight (Unabridged) MP3', None),
    ('AudioBook', SERPENT, 'en', 'Carissa Broadbent-The Serpent and the Wings of Night-AUDiOBOOK-WEB-MP3-EN-2024-GRP',
     None),
    ('eBook', ('Tienda de Sao Paulo', '', 'Ana Silva'), 'en', 'Ana Silva - Tienda de S\u00c3O Paulo (epub)', None),
]


class LanguagePenaltyTest(OverlayTestCase):

    def test_language_cases(self):
        for library, (title, sub, author), lang, release, tag in LANGUAGE_CASES:
            with self.subTest(title=title, lang=lang, release=release):
                upstream, _ = score(library, title, sub, author, release, penalty=False, lang=lang)
                patched, volume, found = score(library, title, sub, author, release, lang=lang, detail=True)
                self.assertEqual(found, tag)
                volume_penalty = resultlist.VOLUME_PENALTY if volume else 0
                if tag:
                    self.assertGreaterEqual(upstream, 80)
                    self.assertAlmostEqual(patched, upstream - volume_penalty - resultlist.LANGUAGE_PENALTY, places=2)
                    self.assertLess(patched, 80)
                else:
                    self.assertAlmostEqual(patched, upstream - volume_penalty, places=2)


PROVIDER = 'http://prowlarr.invalid/14/api'


class FailedReleaseTest(OverlayTestCase):
    """BLACKLIST_FAILED: a Failed wanted row blocks its release for its provider and format, whatever the spelling."""

    def setUp(self):
        set_config({'BLACKLIST_FAILED': True})
        self.db().action('DELETE FROM wanted')

    def tearDown(self):
        self.db().action('DELETE FROM wanted')
        set_config({'BLACKLIST_FAILED': False})

    def failed(self, title, file=None, provider=PROVIDER, aux='eBook', status='Failed'):
        url = 'http://prowlarr.invalid/14/download?apikey=k&link=then' + (f'&file={file}' if file else '')
        self.db().action('INSERT INTO wanted (BookID, NZBurl, NZBtitle, NZBprov, Status, AuxInfo, DLResult) '
                         'VALUES (?, ?, ?, ?, ?, ?, ?)', ('other', url, title, provider, status, aux, 'test'))

    @staticmethod
    def grab(release, book=POTTER, library='eBook', provider=PROVIDER):
        """The best match find_best_result picks from this one release (None: rejected)."""
        title, sub, author = book
        found = resultlist.find_best_result(
            [{'nzbtitle': release, 'nzburl': 'http://prowlarr.invalid/14/download?apikey=k&link=now', 'nzbprov': provider,
              'nzbsize': 0, 'nzbmode': 'nzb', 'priority': 0}],
            {'bookid': 'fixture', 'authorName': author, 'bookName': title, 'bookSub': sub, 'library': library,
             'searchterm': title}, 'book' if library == 'eBook' else 'audiobook', 'nzb')
        return found[1]['NZBtitle'] if found else None

    def test_block_with_spaces_rejects_the_dotted_release(self):
        """haynesnetwork#755: block row 9549 held the SABnzbd name, the backlog search got the indexer's."""
        self.failed('Herman Melville Pierre And Israel Potter 2014 DANiSH RETAiL ePub eBOOK-DECiPHER-mqIO')
        self.assertIsNone(self.grab(POTTER_DK))

    def test_case_and_underscores_are_one_spelling(self):
        self.failed('herman_melville__israel_potter (epub)')
        self.assertIsNone(self.grab('Herman Melville. Israel.Potter (EPUB)'))

    def test_the_link_keeps_the_indexer_title(self):
        """SABnzbd dropped ".par2." from the row (Camino Island, rows 9551 and 9585): the link's file still has it."""
        self.failed('John Grisham - Camino Island 01 - Camino Island',
                    file='John+Grisham+-+Camino+Island+01+-+Camino+Island.par2.', aux='AudioBook')
        self.assertIsNone(self.grab('John Grisham - Camino Island 01 - Camino Island.par2.',
                                    book=('Camino Island', '', 'John Grisham'), library='AudioBook'))

    def test_other_releases_are_not_blocked(self):
        self.failed('Herman.Melville.Pierre.And.Israel.Potter.2014.DANiSH.RETAiL.ePub.eBOOK-DECiPHER-0biO')
        self.failed('Herman Melville Pierre And Israel Potter 2014 DANiSH RETAiL ePub eBOOK-DECiPHER-mqIO',
                    provider='http://prowlarr.invalid/15/api')
        self.failed('Herman Melville Pierre And Israel Potter 2014 DANiSH RETAiL ePub eBOOK-DECiPHER-mqIO',
                    aux='AudioBook')
        self.failed('Herman Melville Pierre And Israel Potter 2014 DANiSH RETAiL ePub eBOOK-DECiPHER-mqIO',
                    status='Processed')
        self.assertEqual(self.grab(POTTER_DK), POTTER_DK)
