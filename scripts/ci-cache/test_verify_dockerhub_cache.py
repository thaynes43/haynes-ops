#!/usr/bin/env python3
"""Finite fake HTTP custody; no registry, Docker, database or library calls."""
import hashlib
import importlib.util
import contextlib
import io
import json
from pathlib import Path
import unittest
import urllib.error
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('cache_verifier', HERE / 'verify_dockerhub_cache.py')
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


class CacheCases(unittest.TestCase):
    def fixture(self, mutate_index=None, mutate_manifest=None):
        manifest = {'schemaVersion': 2, 'mediaType': sorted(cache.MANIFEST_TYPES)[0],
                    'config': {'digest': 'sha256:' + 'c' * 64, 'size': 100},
                    'layers': [{'digest': 'sha256:' + 'd' * 64, 'size': 200}]}
        if mutate_manifest:
            mutate_manifest(manifest)
        child = encode(manifest)
        entry = {'mediaType': manifest['mediaType'], 'digest': digest(child), 'size': len(child),
                 'platform': {'os': 'linux', 'architecture': 'amd64'}}
        index = {'schemaVersion': 2, 'mediaType': sorted(cache.INDEX_TYPES)[0], 'manifests': [entry]}
        if mutate_index:
            mutate_index(index)
        raw = encode(index)
        return raw, child, digest(raw)

    def verify_fixture(self, fixture, read=None):
        raw, child, pin = fixture
        with mock.patch.dict(cache.IMAGES, {'synthetic': ('library/synthetic', pin)}):
            return cache.verify('synthetic', read=read or mock.Mock(side_effect=[raw, child]))

    def test_exact_pinned_index_unique_platform_and_child_are_proved(self):
        fixture = self.fixture()
        read = mock.Mock(side_effect=fixture[:2])
        result = self.verify_fixture(fixture, read)
        self.assertEqual(result['index_digest'], fixture[2])
        self.assertEqual(result['manifest_digest'], digest(fixture[1]))
        self.assertEqual(result['index_platform'], 'linux/amd64')
        self.assertEqual([call.args[1] for call in read.call_args_list], ['manifests', 'manifests'])

    def test_wrong_index_bytes_cannot_choose_another_image(self):
        fixture = self.fixture()
        with self.assertRaisesRegex(cache.Refused, 'index_sha256'):
            self.verify_fixture(fixture, mock.Mock(return_value=b'{}'))

    def test_missing_duplicate_or_other_platform_refuses(self):
        mutations = [lambda row: row['manifests'].clear(),
                     lambda row: row['manifests'].append(row['manifests'][0].copy()),
                     lambda row: row['manifests'][0]['platform'].update(architecture='arm64')]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(cache.Refused):
                self.verify_fixture(self.fixture(mutate_index=mutation))

    def test_changed_child_bytes_or_size_refuse(self):
        fixture = self.fixture()
        with self.assertRaisesRegex(cache.Refused, 'platform_manifest_sha256'):
            self.verify_fixture(fixture, mock.Mock(side_effect=[fixture[0], b'{}']))
        wrong_size = self.fixture(mutate_index=lambda row: row['manifests'][0].update(size=1))
        with self.assertRaisesRegex(cache.Refused, 'platform_manifest_sha256'):
            self.verify_fixture(wrong_size)

    def test_non_index_bad_child_schema_or_configuration_refuse(self):
        with self.assertRaisesRegex(cache.Refused, 'cached_index_schema'):
            self.verify_fixture(self.fixture(mutate_index=lambda row: row.update(mediaType='wrong')))
        for mutation in (lambda row: row.update(schemaVersion=1),
                         lambda row: row.update(config={'digest': 'bad', 'size': 1}),
                         lambda row: row.update(layers=[])):
            with self.subTest(mutation=mutation), self.assertRaises(cache.Refused):
                self.verify_fixture(self.fixture(mutate_manifest=mutation))

    def test_duplicate_json_keys_refuse(self):
        with self.assertRaisesRegex(cache.Refused, 'duplicate_json_key'):
            cache.decode(b'{"schemaVersion":2,"schemaVersion":1}')

    def test_fetch_is_exact_bounded_no_proxy_and_no_redirect(self):
        raw = b'{}'; pin = digest(raw)
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.geturl.return_value = f'https://mirror.gcr.io/v2/library/python/manifests/{pin}'
        response.read.return_value = raw
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(cache.urllib.request, 'build_opener', return_value=opener) as build:
            self.assertEqual(cache.fetch('library/python', 'manifests', pin), raw)
            self.assertEqual(opener.open.call_args.kwargs['timeout'], 8)
            response.read.assert_called_once_with(cache.CAP + 1)
            self.assertEqual(build.call_args.args[0].proxies, {})
        with self.assertRaisesRegex(cache.Refused, 'cache_redirect_refused'):
            cache.NoRedirect().redirect_request(None)

    def test_changed_response_host_oversize_and_wrong_digest_refuse(self):
        for url, raw, reason in (('https://unapproved.invalid/x', b'{}', 'cache_response'),
                                  (None, b'x' * (cache.CAP + 1), 'metadata_cap'),
                                  (None, b'changed', 'metadata_sha256')):
            response = mock.MagicMock(); response.__enter__.return_value = response; response.status = 200
            pin = digest(b'{}')
            response.geturl.return_value = url or f'https://mirror.gcr.io/v2/library/python/manifests/{pin}'
            response.read.return_value = raw
            with mock.patch.object(cache.urllib.request, 'build_opener') as build:
                build.return_value.open.return_value = response
                with self.assertRaisesRegex(cache.Refused, reason):
                    cache.fetch('library/python', 'manifests', pin)

    def test_reviewed_refs_remain_the_dockerfile_and_pg_fixture_pins(self):
        root = HERE.parent.parent
        copyfile = (root / 'scripts/book-copy-writer/Dockerfile').read_text()
        nativefile = (root / 'scripts/book-native-scan-fixture/Dockerfile').read_text()
        workflow = (root / '.github/workflows/book-copy-writer-build.yml').read_text()
        self.assertIn(cache.IMAGES['python'][1], copyfile)
        self.assertIn(cache.IMAGES['kavita'][1], nativefile)
        self.assertIn('mirror.gcr.io/library/postgres:16@' + cache.IMAGES['postgres'][1], workflow)

    def test_runner_failure_keeps_exact_http_category_without_remote_payload(self):
        output = io.StringIO()
        error = urllib.error.HTTPError('https://private.invalid/never-output', 404,
                                       'never-output-body', {}, None)
        with mock.patch.object(cache, 'verify', side_effect=error), \
             mock.patch('sys.argv', ['verify', '--profile', 'native-scanner']), \
             mock.patch.object(cache.signal, 'signal'), mock.patch.object(cache.signal, 'setitimer') as timer, \
             contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit:
            cache.main()
        self.assertEqual(exit.exception.code, 2)
        value = json.loads(output.getvalue())
        self.assertFalse(value['cache_verified'])
        self.assertEqual(value['http_status'], 404)
        self.assertNotIn('never-output', output.getvalue())
        self.assertEqual([call.args[1] for call in timer.call_args_list], [60, 0])


if __name__ == '__main__':
    unittest.main()
