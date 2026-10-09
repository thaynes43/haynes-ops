#!/usr/bin/env python3
"""Finite fake HTTP custody; no registry, Docker, database or library calls."""
import hashlib
import importlib.util
import contextlib
import io
import json
from pathlib import Path
import tempfile
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
        with mock.patch.object(cache, 'canonical_image', return_value=('library/synthetic', pin)):
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
        nativeworkflow = (root / '.github/workflows/book-native-scan-fixture.yml').read_text()
        builderfile = (root / 'scripts/ci-cache/buildkit/Dockerfile').read_text()
        self.assertIn(cache.canonical_image('python')[1], copyfile)
        self.assertIn(cache.canonical_image('kavita')[1], nativefile)
        self.assertIn('docker.io/library/postgres:16@' + cache.canonical_image('postgres')[1], workflow)
        self.assertIn('PG_IMAGE: ${{ steps.cache.outputs.postgres_image }}', workflow)
        self.assertIn('"$PG_IMAGE"', workflow)
        self.assertIn('buildkitd-config-inline: ${{ steps.cache.outputs.buildkit_config }}', workflow)
        self.assertIn(cache.canonical_image('buildkit')[1], builderfile)
        for text in (workflow, nativeworkflow):
            self.assertIn('driver-opts: image=${{ steps.cache.outputs.buildkit_image }}', text)

    def test_changed_canonical_pins_are_the_only_cache_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {'python': 'scripts/book-copy-writer/Dockerfile',
                     'kavita': 'scripts/book-native-scan-fixture/Dockerfile',
                     'buildkit': 'scripts/ci-cache/buildkit/Dockerfile',
                     'postgres': '.github/workflows/book-copy-writer-build.yml'}
            references = {'python': 'python:3.14-slim', 'kavita': 'docker.io/jvmilazz0/kavita:0.9.0.2',
                          'buildkit': 'docker.io/moby/buildkit:buildx-stable-1',
                          'postgres': 'mirror.gcr.io/library/postgres:16'}
            for name, path in paths.items():
                source = root / path; source.parent.mkdir(parents=True, exist_ok=True)
                for changed in ('1', '2'):
                    pin = 'sha256:' + changed * 64
                    source.write_text(('FROM ' if name != 'postgres' else 'docker run ') + references[name] + '@' + pin + '\n')
                    self.assertEqual(cache.canonical_image(name, root)[1], pin)

    def test_tag_only_interpolated_malformed_and_ambiguous_sources_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'scripts/book-copy-writer/Dockerfile'
            source.parent.mkdir(parents=True)
            valid = 'FROM python:3.14-slim@sha256:' + '1' * 64 + '\n'
            for text in ('FROM python:3.14-slim\n', 'FROM python:${TAG}@sha256:' + '1' * 64,
                         'FROM ${PYTHON_IMAGE}\n', 'FROM --platform=linux/amd64 python:3.14-slim@sha256:' + '1' * 64,
                         'FROM docker.io/library/library/python:3.14-slim@sha256:' + '1' * 64,
                         valid + valid, 'FROM python:3.14-slim@sha256:bad\n'):
                with self.subTest(text=text), self.assertRaises(cache.Refused):
                    source.write_text(text); cache.canonical_image('python', root)
            pg = root / '.github/workflows/book-copy-writer-build.yml'; pg.parent.mkdir(parents=True)
            ref = 'mirror.gcr.io/library/postgres:16@sha256:' + '1' * 64
            for text in ('docker run postgres:16', 'docker run postgres:${TAG}@sha256:' + '1' * 64,
                         'docker run postgres:17@sha256:' + '1' * 64, ref + '\n' + ref):
                with self.subTest(text=text), self.assertRaises(cache.Refused):
                    pg.write_text(text); cache.canonical_image('postgres', root)

    def test_runner_failure_keeps_exact_http_category_without_remote_payload(self):
        output = io.StringIO()
        error = urllib.error.HTTPError('https://private.invalid/never-output', 401,
                                       'never-output-body', {}, None)
        with mock.patch.object(cache, 'verify', side_effect=error), \
             mock.patch('sys.argv', ['verify', '--profile', 'native-scanner']), \
             mock.patch.object(cache.signal, 'signal'), mock.patch.object(cache.signal, 'setitimer') as timer, \
             contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit:
            cache.main()
        self.assertEqual(exit.exception.code, 2)
        value = json.loads(output.getvalue())
        self.assertFalse(value['cache_verified'])
        self.assertEqual(value['http_status'], 401)
        self.assertNotIn('never-output', output.getvalue())
        self.assertEqual([call.args[1] for call in timer.call_args_list], [60, 0])

    def test_only_exact_404_routes_whole_profile_to_unchanged_canonical_refs(self):
        miss = urllib.error.HTTPError('https://private.invalid/never-output', 404,
                                     'never-output-body', {}, None)
        for profile, names in cache.PROFILES.items():
            pins = {name: cache.canonical_image(name)[1] for name in names}
            for failed in names:
                def verify(name):
                    if name == failed:
                        raise miss
                    return {'image': name, 'index_digest': pins[name], 'verified_cache': cache.HOST}
                with self.subTest(profile=profile, failed=failed), mock.patch.object(cache, 'verify', side_effect=verify):
                    result = cache.resolve_profile(profile)
                self.assertFalse(result['cache_verified'])
                self.assertEqual(result['registry_route'], 'canonical_dockerhub')
                self.assertEqual(result['buildkit_config'], '')
                self.assertEqual(result['buildkit_image'], 'docker.io/moby/buildkit:buildx-stable-1@' + pins['buildkit'])
                if 'postgres' in names:
                    self.assertEqual(result['postgres_image'], 'docker.io/library/postgres:16@' + pins['postgres'])
                refused = next(row for row in result['images'] if row['image'] == failed)
                self.assertEqual(refused['index_digest'], pins[failed])
                self.assertEqual(refused['http_status'], 404)
                self.assertNotIn('verified_cache', refused)
                self.assertNotIn('never-output', json.dumps(result))

    def test_verified_profile_alone_selects_cache_and_other_errors_remain_fatal(self):
        with mock.patch.object(cache, 'verify', side_effect=lambda name: {'image': name, 'verified_cache': cache.HOST}):
            result = cache.resolve_profile('copy-writer')
        self.assertTrue(result['cache_verified'])
        self.assertEqual(result['registry_route'], 'verified_cache')
        self.assertIn('mirrors = ["mirror.gcr.io"]', result['buildkit_config'])
        self.assertEqual(result['postgres_image'], 'mirror.gcr.io/library/postgres:16@' + cache.canonical_image('postgres')[1])
        self.assertEqual(result['buildkit_image'], 'mirror.gcr.io/moby/buildkit:buildx-stable-1@' + cache.canonical_image('buildkit')[1])
        with mock.patch.object(cache, 'verify', side_effect=lambda name: {'image': name, 'verified_cache': cache.HOST}):
            native = cache.resolve_profile('native-scanner')
        self.assertEqual(native['buildkit_image'], result['buildkit_image'])
        errors = [urllib.error.HTTPError('private', status, 'private', {}, None)
                  for status in (401, 403, 429, 500)] + [TimeoutError(), cache.Refused('index_sha256'),
                                                      cache.Refused('unique_linux_amd64')]
        miss = urllib.error.HTTPError('private', 404, 'private', {}, None)
        for error in errors:
            with self.subTest(error=type(error).__name__), \
                 mock.patch.object(cache, 'verify', side_effect=[miss, error]), self.assertRaises(type(error)):
                cache.resolve_profile('copy-writer')

    def test_404_action_outputs_are_truthful_and_fatal_errors_publish_no_route(self):
        miss = urllib.error.HTTPError('https://private.invalid/never-output', 404,
                                     'never-output-body', {}, None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'actions-output'; path.touch()
            stdout = io.StringIO()
            argv = ['verify', '--profile', 'native-scanner', '--github-output', str(path)]
            with mock.patch.object(cache, 'verify', side_effect=miss), mock.patch('sys.argv', argv), \
                 mock.patch.object(cache.signal, 'signal'), mock.patch.object(cache.signal, 'setitimer'), \
                 contextlib.redirect_stdout(stdout):
                cache.main()
            result = json.loads(stdout.getvalue())
            self.assertFalse(result['cache_verified'])
            self.assertNotIn('never-output', stdout.getvalue())
            self.assertEqual(path.read_text(), 'cache_verified=false\nbuildkit_config<<CACHE_CONFIG\n\nCACHE_CONFIG\nbuildkit_image=docker.io/moby/buildkit:buildx-stable-1@' + cache.canonical_image('buildkit')[1] + '\n')
            path.write_text('')
            with mock.patch.object(cache, 'verify', side_effect=TimeoutError()), mock.patch('sys.argv', argv), \
                 mock.patch.object(cache.signal, 'signal'), mock.patch.object(cache.signal, 'setitimer'), \
                 contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                cache.main()
            self.assertEqual(path.read_text(), '')


if __name__ == '__main__':
    unittest.main()
