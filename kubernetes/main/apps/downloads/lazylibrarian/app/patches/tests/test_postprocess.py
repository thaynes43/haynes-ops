"""postprocess.py: a SABnzbd job that failed (e.g. "Duplicate NZB") is never post-processed, so the fuzzy pass
cannot import another book's same-named download (thaynes43/haynesnetwork#688)."""
from unittest import mock

from hops_tests.hops_base import OverlayTestCase
from lazylibrarian import postprocess


class FailedSabJobTest(OverlayTestCase):

    def ready(self, source, status, progress):
        download_id = f'{source}-{status}-{progress}'
        self.db().action("INSERT INTO wanted (BookID, NZBurl, NZBtitle, NZBprov, Status, AuxInfo, Source, DownloadID, "
                         "DLResult) VALUES ('B1', ?, 'Some Release', 'fixture', ?, 'eBook', ?, ?, 'Duplicate NZB')",
                         (f'http://fixture.invalid/{download_id}', status, source, download_id))
        row = self.db().match('SELECT * FROM wanted WHERE DownloadID=?', (download_id,))
        with mock.patch.object(postprocess, 'get_download_name', return_value='Some Release'), \
                mock.patch.object(postprocess, 'check_contents', return_value=''), \
                mock.patch.object(postprocess, 'get_download_progress', return_value=(progress, False)):
            return postprocess._get_ready_from_snatched(self.db(), [dict(row)])

    def test_aborted_sab_job_is_not_processed(self):
        self.assertEqual(self.ready('SABNZBD', 'Aborted', -1), [])

    def test_other_minus_one_rows_still_process(self):
        # a torrent removed after seeding reports -1 and must still be processed (upstream behaviour)
        self.assertEqual(len(self.ready('QBITTORRENT', 'Snatched', -1)), 1)
        # a SABnzbd row that the status pass has not aborted is left to upstream logic
        self.assertEqual(len(self.ready('SABNZBD', 'Snatched', -1)), 1)
        # a completed SABnzbd job is processed
        self.assertEqual(len(self.ready('SABNZBD', 'Snatched', 100)), 1)
