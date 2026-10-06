"""sabnzbd.py: the NZB is fetched and uploaded to SABnzbd (mode=addfile), never handed over as the indexer URL,
so the indexer's API key does not land in SABnzbd's history or LazyLibrarian's log (2026-10-03)."""
from unittest import mock

from hops_tests.hops_base import OverlayTestCase, set_config
from lazylibrarian import sabnzbd

INDEXER_URL = 'https://indexer.invalid/api?t=get&id=abc&apikey=SECRETKEY'
NZB = b'<?xml version="1.0" encoding="utf-8"?>\n<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"></nzb>'


def response(status=200, content=b'', payload=None):
    r = mock.Mock()
    r.status_code = status
    r.content = content
    r.json.return_value = payload or {}
    return r


class UploadNzbTest(OverlayTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        set_config({'SAB_HOST': 'sab.invalid', 'SAB_PORT': 8080, 'SAB_API': 'sabkey'})

    def send(self, fetched):
        with mock.patch.object(sabnzbd.requests, 'get', return_value=fetched) as get, \
                mock.patch.object(sabnzbd.requests, 'post',
                                  return_value=response(payload={'status': True, 'nzo_ids': ['SABnzbd_nzo_1']})) as post, \
                self.assertLogs(level='DEBUG') as logs:
            result = sabnzbd.sab_nzbd(title='Some Release', nzburl=INDEXER_URL, library='eBook')
        return result, get, post, '\n'.join(logs.output)

    def test_nzb_is_uploaded_not_linked(self):
        result, get, post, logs = self.send(response(content=NZB))
        self.assertEqual(result, ('SABnzbd_nzo_1', ''))
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0], INDEXER_URL)
        post.assert_called_once()
        sab_url = post.call_args.args[0]
        self.assertIn('mode=addfile', sab_url)
        self.assertNotIn('indexer.invalid', sab_url)
        self.assertNotIn('SECRETKEY', sab_url)
        self.assertEqual(post.call_args.kwargs['files']['name'][1], NZB)
        self.assertNotIn('SECRETKEY', logs)

    def test_a_failed_fetch_never_reaches_sab(self):
        result, get, post, logs = self.send(response(status=404, content=b'not found'))
        self.assertEqual(get.call_args.args[0], INDEXER_URL)  # the overlay fetched the NZB itself
        self.assertIs(result[0], False)
        post.assert_not_called()
        self.assertNotIn('SECRETKEY', logs)
