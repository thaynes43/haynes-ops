"""resultlist.py: a later volume of a series named after the wanted book loses VOLUME_PENALTY (haynes-ops #3368,
#3369; thaynes43/haynesnetwork#688, #694)."""
from hops_tests.hops_base import OverlayTestCase, load_fixture, score

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
