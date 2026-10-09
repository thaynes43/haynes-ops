#!/usr/bin/env python3
"""Verify only the reviewed public cached image metadata before CI image pulls."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import signal
import urllib.error
import urllib.request

HOST = 'mirror.gcr.io'
CAP = 1024 * 1024
INDEX_TYPES = {'application/vnd.oci.image.index.v1+json',
               'application/vnd.docker.distribution.manifest.list.v2+json'}
MANIFEST_TYPES = {'application/vnd.oci.image.manifest.v1+json',
                  'application/vnd.docker.distribution.manifest.v2+json'}
ACCEPT = ', '.join(sorted(INDEX_TYPES | MANIFEST_TYPES))
ROOT = Path(__file__).resolve().parents[2]
PROFILES = {'copy-writer': ('python', 'postgres'), 'native-scanner': ('kavita',)}


class Refused(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def unique(items):
    result = {}
    for key, value in items:
        require(key not in result, 'duplicate_json_key')
        result[key] = value
    return result


def decode(raw):
    value = json.loads(raw, object_pairs_hook=unique)
    require(isinstance(value, dict), 'metadata_object')
    return value


def canonical_source(name, root=ROOT):
    sources = {'python': ('scripts/book-copy-writer/Dockerfile', 'library/python', r'(?:python|library/python|docker\.io/(?:library/)?python)', 'from'),
               'kavita': ('scripts/book-native-scan-fixture/Dockerfile', 'jvmilazz0/kavita', r'(?:docker\.io/)?jvmilazz0/kavita', 'from'),
               'postgres': ('.github/workflows/book-copy-writer-build.yml', 'library/postgres', r'(?:(?:mirror\.gcr\.io|docker\.io)/library/|library/)?postgres', 'workflow')}
    require(name in sources, 'reviewed_image_source')
    path, repository, pattern, kind = sources[name]
    with (root / path).open('rb') as source:
        raw = source.read(CAP + 1)
    require(0 < len(raw) <= CAP, 'canonical_source_cap')
    text = raw.decode('utf-8')
    if kind == 'from':
        candidates = [line for line in text.splitlines()
                      if re.match(r'^[ \t]*FROM\s', line, re.IGNORECASE)
                      and re.search(r'(?:^|\s)' + pattern + r'(?=[:@\s]|$)', line)]
        require(len(candidates) == 1, 'unique_canonical_image_ref')
        match = re.fullmatch(r'[ \t]*(?i:FROM)[ \t]+(' + pattern + r':[A-Za-z0-9_][A-Za-z0-9_.-]*@(sha256:[0-9a-f]{64}))'
                             r'(?:[ \t]+(?i:AS)[ \t]+[A-Za-z0-9_-]+)?[ \t]*(?:#[^\r\n]*)?', candidates[0])
    else:
        candidates = re.findall(r'(?<![A-Za-z0-9_./-])' + pattern + r':[^\s\x27\x22\\]+', text)
        require(len(candidates) == 1, 'unique_canonical_image_ref')
        match = re.fullmatch(r'(' + pattern + r':16(?:\.[0-9]+)*(?:-[A-Za-z0-9_.-]+)?@(sha256:[0-9a-f]{64}))', candidates[0])
    require(match is not None, 'literal_digest_reference_required')
    return repository, match[2], match[1]


def canonical_image(name, root=ROOT):
    repository, pinned, _ = canonical_source(name, root)
    return repository, pinned


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        raise Refused('cache_redirect_refused')


def fetch(repository, kind, digest):
    require(kind in ('manifests', 'blobs') and re.fullmatch('sha256:[0-9a-f]{64}', digest),
            'metadata_digest')
    url = f'https://{HOST}/v2/{repository}/{kind}/{digest}'
    # No ambient HTTP proxy or alternate-registry redirect is proof authority.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(urllib.request.Request(url, headers={'Accept': ACCEPT}), timeout=8) as response:
        require(response.status == 200 and response.geturl() == url, 'cache_response')
        raw = response.read(CAP + 1)
    require(0 < len(raw) <= CAP, 'metadata_cap')
    require('sha256:' + hashlib.sha256(raw).hexdigest() == digest, 'metadata_sha256')
    return raw


def descriptor(value):
    require(isinstance(value, dict) and re.fullmatch('sha256:[0-9a-f]{64}', value.get('digest', ''))
            and type(value.get('size')) is int and 0 < value['size'] <= CAP, 'metadata_descriptor')
    return value['digest']


def verify(name, read=fetch):
    repository, pinned = canonical_image(name)
    raw = read(repository, 'manifests', pinned)
    require('sha256:' + hashlib.sha256(raw).hexdigest() == pinned, 'index_sha256')
    index = decode(raw)
    require(index.get('schemaVersion') == 2 and index.get('mediaType') in INDEX_TYPES
            and isinstance(index.get('manifests'), list)
            and 0 < len(index['manifests']) <= 64
            and all(isinstance(entry, dict) and isinstance(entry.get('platform'), dict)
                    for entry in index['manifests']), 'cached_index_schema')
    platforms = [entry for entry in index['manifests'] if isinstance(entry, dict)
                 and entry.get('platform', {}).get('os') == 'linux'
                 and entry.get('platform', {}).get('architecture') == 'amd64']
    require(len(platforms) == 1, 'unique_linux_amd64')
    entry = platforms[0]
    require(entry.get('mediaType') in MANIFEST_TYPES
            and entry['platform'].get('variant', '') in ('', 'v1'), 'amd64_descriptor')
    manifest_digest = descriptor(entry)
    raw = read(repository, 'manifests', manifest_digest)
    require(len(raw) == entry['size'] and 'sha256:' + hashlib.sha256(raw).hexdigest() == manifest_digest,
            'platform_manifest_sha256')
    manifest = decode(raw)
    require(manifest.get('schemaVersion') == 2 and manifest.get('mediaType') in MANIFEST_TYPES
            and isinstance(manifest.get('layers'), list) and manifest['layers'], 'platform_manifest_schema')
    config_digest = descriptor(manifest.get('config'))
    return {'image': name, 'repository': repository, 'index_digest': pinned,
            'manifest_digest': manifest_digest, 'config_digest': config_digest,
            'index_platform': 'linux/amd64', 'verified_cache': HOST}


def resolve_profile(profile):
    images = []
    for name in PROFILES[profile]:
        repository, pinned = canonical_image(name)
        try:
            images.append(verify(name))
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            images.append({'image': name, 'repository': repository, 'index_digest': pinned,
                           'cache_verified': False, 'http_status': 404})
    verified = all('verified_cache' in row for row in images)
    result = {'schema': 1, 'cache_verified': verified, 'images': images,
              'registry_route': 'verified_cache' if verified else 'canonical_dockerhub',
              'buildkit_config': '[registry."docker.io"]\nmirrors = ["mirror.gcr.io"]' if verified else ''}
    if 'postgres' in PROFILES[profile]:
        repository, _, reference = canonical_source('postgres')
        # The literal source provides the tag and digest. Normalize only its registry.
        tag_digest = reference.split(':', 1)[1]
        result['postgres_image'] = f'{HOST if verified else "docker.io"}/{repository}:{tag_digest}'
    return result


def action_outputs(path, result):
    # All values are fixed routing text or strictly parsed public image references.
    with path.open('a', encoding='utf-8') as output:
        output.write('cache_verified=' + str(result['cache_verified']).lower() + '\n')
        output.write('buildkit_config<<CACHE_CONFIG\n' + result['buildkit_config'] + '\nCACHE_CONFIG\n')
        if 'postgres_image' in result:
            output.write('postgres_image=' + result['postgres_image'] + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=PROFILES, required=True)
    parser.add_argument('--github-output', type=Path)
    args = parser.parse_args()

    def stop(*_):
        raise Refused('original_metadata_deadline')

    signal.signal(signal.SIGALRM, stop)
    signal.setitimer(signal.ITIMER_REAL, 60)
    try:
        result = resolve_profile(args.profile)
        if args.github_output:
            action_outputs(args.github_output, result)
        print(json.dumps(result, sort_keys=True))
    except Exception as error:
        code = str(error) if isinstance(error, Refused) else 'cache_transport_or_json_refused'
        print(json.dumps({'schema': 1, 'cache_verified': False, 'code': code,
                          'error_class': type(error).__name__,
                          'http_status': error.code if isinstance(error, urllib.error.HTTPError) else None}))
        raise SystemExit(2)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == '__main__':
    main()
