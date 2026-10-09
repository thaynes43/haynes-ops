#!/usr/bin/env python3
"""Verify only the reviewed public cached image metadata before CI image pulls."""
import argparse
import hashlib
import json
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
IMAGES = {
    'python': ('library/python', 'sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2'),
    'postgres': ('library/postgres', 'sha256:ca0bd484cb98bf4b24eb1010e73fb3fcbd6714d240fbc1a10eea5b7dbecb641d'),
    'kavita': ('jvmilazz0/kavita', 'sha256:ca6af7a18d7124d014702983c2364e485294f808c1552e9555f2595b7cda7982'),
}
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
    repository, pinned = IMAGES[name]
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=PROFILES, required=True)
    args = parser.parse_args()

    def stop(*_):
        raise Refused('original_metadata_deadline')

    signal.signal(signal.SIGALRM, stop)
    signal.setitimer(signal.ITIMER_REAL, 60)
    try:
        result = [verify(name) for name in PROFILES[args.profile]]
        print(json.dumps({'schema': 1, 'cache_verified': True, 'images': result}, sort_keys=True))
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
