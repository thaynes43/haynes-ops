"""Candidate image qualification separate from the frozen signed runtime.

Frozen pins stay the historical contract. A candidate proves itself by
comparing reviewed source bytes with measured image bytes. Local image ID,
archive SHA-256, registry digest, and signature are different states.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys

import pipeline_pins as pins

MANIFEST_VERSION = 1
MODULE_NAMES = (
    'requirements.txt',
    'epub_copies.py',
    'epub_metadata.py',
    'book_copy_writer.py',
    'bound_census.py',
    'proof_transport.py',
)
MARKER_VERSIONS = ('3.12', '3.13', '3.14', '3.14.2')
FROZEN_IMAGE = 'ghcr.io/thaynes43/book-copy-writer@sha256:628e97b8dbcc83a4d7068484b516b21dde740c4dd130d54f3b030f1ee7a75601'
FROZEN_SOURCE = 'daa542a395eb508de6bbeb2a7430e63aa3454b6e'
FROZEN_SIGNATURE = 'ee882db8feb49ff1510700a415d6635b33850ab0e1f6c76c0825775652818183'
FROZEN_MODULES = {
    'requirements.txt': '830451570988c97907965090ff7d83b327681336527f6f06dc92d0c74411becc',
    'epub_copies.py': 'b79389c89bd5a57b4737ad693d2c209bc513e818343064ec04c8f0fcfb24a7da',
    'epub_metadata.py': 'ce3c5a271cb4c94d91f3154240b4cfbc5e0cc57981969fc5c975a0c0f50eb773',
    'book_copy_writer.py': 'fbfec65f4af933da6ec9aca90536501e4514cebfb8e378584e3085a993493880',
    'bound_census.py': 'd3df953dc7105d9d2535b0d370bb53c266ab46f018beac72652f681d91d52c38',
    'proof_transport.py': '096ba710805623638b87d8c31f6be2653fe57ae0377bf35a27dabf014755e8a5',
}
MAIN_ONLY = "github.ref == 'refs/heads/main' && github.event_name != 'pull_request'"


class QualificationRefused(Exception):
    """The measured bytes or the publication gate do not match the contract."""


class FrozenRuntimeDependency(QualificationRefused):
    """The immutable frozen artifact is missing or is the wrong image."""


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def refuse_rewritten_frozen_contract():
    if pins.IMAGE != FROZEN_IMAGE or pins.IMAGE_SOURCE != FROZEN_SOURCE or pins.IMAGE_RECEIPT_SHA256 != FROZEN_SIGNATURE:
        raise QualificationRefused('frozen image, source, or signature pin was rewritten')
    if dict(pins.MODULES) != FROZEN_MODULES or tuple(pins.MODULES) != MODULE_NAMES:
        raise QualificationRefused('frozen module pins were rewritten')


def assert_immutable_frozen_ref(image_ref):
    refuse_rewritten_frozen_contract()
    if not isinstance(image_ref, str) or '@sha256:' not in image_ref:
        raise FrozenRuntimeDependency(
            'frozen runtime must be selected by immutable digest, not a mutable tag (%r); no candidate fallback' % (image_ref,))
    digest = image_ref.split('@', 1)[1]
    if len(digest) != 71 or not digest.startswith('sha256:') or any(char not in '0123456789abcdef' for char in digest[7:]):
        raise FrozenRuntimeDependency(
            'frozen runtime digest %r is not a sha256 digest; no candidate fallback' % (image_ref,))
    if image_ref != FROZEN_IMAGE:
        raise FrozenRuntimeDependency(
            'wrong immutable frozen image %s; required %s; no candidate or mutable-tag fallback' % (image_ref, FROZEN_IMAGE))


def assert_pulled_frozen(image_ref, repo_digests, image_id):
    assert_immutable_frozen_ref(image_ref)
    if not isinstance(repo_digests, list) or image_ref not in repo_digests:
        raise FrozenRuntimeDependency(
            'pulled artifact did not report immutable digest %s (repo digests %s); no candidate fallback' % (image_ref, repo_digests))
    if not isinstance(image_id, str) or len(image_id) != 71 or not image_id.startswith('sha256:'):
        raise FrozenRuntimeDependency('pulled frozen image %s has no distinct local image id' % image_ref)
    if image_id == image_ref.split('@', 1)[1]:
        raise QualificationRefused('local image id collapsed into the frozen registry digest')


def resolve_frozen_paths(environ=None, candidate_present=None):
    environ = os.environ if environ is None else environ
    mounted = environ.get('FROZEN_RUNTIME_DIR') or ''
    if not mounted and Path('/frozen-runtime').is_dir():
        mounted = '/frozen-runtime'
    if mounted:
        ref = environ.get('FROZEN_IMAGE_REF') or ''
        if not ref:
            raise FrozenRuntimeDependency(
                'frozen runtime mount %s has no immutable image ref; no candidate fallback' % mounted)
        return {name: Path(mounted) / name for name in MODULE_NAMES}, ref
    present = Path('/copy-writer').is_dir() if candidate_present is None else candidate_present
    if present:
        raise FrozenRuntimeDependency(
            'frozen signed runtime %s was not mounted; no candidate fallback' % FROZEN_IMAGE)
    return {name: pins.module_source(name) for name in MODULE_NAMES}, pins.IMAGE


def qualify_frozen_runtime(paths, image_ref):
    assert_immutable_frozen_ref(image_ref)
    measured = {}
    for name in MODULE_NAMES:
        path = Path(paths[name])
        if not path.is_file():
            raise FrozenRuntimeDependency(
                'frozen signed runtime %s is missing %s; no candidate fallback' % (image_ref, name))
        measured[name] = sha256_file(path)
        if measured[name] != pins.MODULES[name]:
            raise QualificationRefused(
                'frozen artifact %s hash %s does not match contract %s' % (name, measured[name], pins.MODULES[name]))
    return {
        'manifest_version': MANIFEST_VERSION,
        'kind': 'frozen-runtime',
        'image_ref': image_ref,
        'local_image_id': None,
        'archive_sha256': None,
        'registry_digest': image_ref.split('@', 1)[1],
        'signature_sha256': pins.IMAGE_RECEIPT_SHA256,
        'source_sha': pins.IMAGE_SOURCE,
        'admitted_runtime': True,
        'modules': measured,
    }


def reviewed_source_path(name):
    if name in ('epub_copies.py', 'epub_metadata.py'):
        extra = os.environ.get('COPY_TEST_MODULES')
        if extra:
            mounted = Path(extra) / name
            if mounted.is_file():
                return mounted
    path = pins.module_source(name)
    if path.is_file():
        return path
    raise QualificationRefused('reviewed source module is missing: %s' % name)


def typing_extensions_marker(requirements_text):
    found = None
    for raw in requirements_text.splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line.startswith('typing-extensions==') and not line.startswith('typing_extensions=='):
            continue
        if ';' not in line:
            raise QualificationRefused('typing-extensions requirement has no environment marker')
        found = line.split(';', 1)[1].split('--hash', 1)[0].strip()
    if not found:
        raise QualificationRefused('typing-extensions requirement is absent')
    return found


def _version_pair(value):
    parts = str(value).split('.')
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise QualificationRefused('unsupported Python version %r' % (value,))
    return int(parts[0]), int(parts[1])


def evaluate_python_version_marker(marker, python_version):
    prefix = 'python_version < "'
    if not marker.startswith(prefix) or not marker.endswith('"') or marker.count('"') != 2:
        raise QualificationRefused('unsupported requirement marker %r' % (marker,))
    ceiling = marker[len(prefix):-1]
    return _version_pair(python_version) < _version_pair(ceiling)


def marker_table(requirements_text):
    marker = typing_extensions_marker(requirements_text)
    return {
        'marker': marker,
        'selects': {version: evaluate_python_version_marker(marker, version) for version in MARKER_VERSIONS},
        'marker_is_binary_proof': False,
    }


def distribution_report(measured_in_image):
    if not measured_in_image:
        return {
            'measured_in_image': False,
            'interpreter': None,
            'installed': None,
            'marker_selects_typing_extensions': None,
            'marker_is_binary_proof': False,
        }
    installed = []
    for dist in importlib.metadata.distributions():
        name = dist.metadata.get('Name')
        if name:
            installed.append({'name': name, 'version': dist.version})
    installed.sort(key=lambda item: item['name'].lower())
    version = sys.version.split()[0]
    requirements = Path('/copy-writer/requirements.txt').read_text() if Path('/copy-writer/requirements.txt').is_file() else reviewed_source_path('requirements.txt').read_text()
    marker = typing_extensions_marker(requirements)
    return {
        'measured_in_image': True,
        'interpreter': version,
        'installed': installed,
        'marker_selects_typing_extensions': evaluate_python_version_marker(marker, version),
        'marker_is_binary_proof': False,
    }


def validate_source_sha(source_sha, reviewed_source_sha=None):
    if not isinstance(source_sha, str) or len(source_sha) != 40 or any(char not in '0123456789abcdef' for char in source_sha):
        raise QualificationRefused('candidate source SHA must be the exact 40-character reviewed commit')
    if reviewed_source_sha is not None and source_sha != reviewed_source_sha:
        raise QualificationRefused('candidate source SHA does not match the reviewed checkout')


def assert_distinct_candidate_identity(local_image_id, archive_sha256, registry_digest, signature_sha256):
    if not isinstance(local_image_id, str) or len(local_image_id) != 71 or not local_image_id.startswith('sha256:'):
        raise QualificationRefused('candidate local image/config ID must be a sha256 image id')
    if any(char not in '0123456789abcdef' for char in local_image_id[7:]):
        raise QualificationRefused('candidate local image/config ID must be lowercase hex')
    if not isinstance(archive_sha256, str) or len(archive_sha256) != 64 or any(char not in '0123456789abcdef' for char in archive_sha256):
        raise QualificationRefused('candidate archive SHA256 must be 64 lowercase hex characters')
    if archive_sha256 == local_image_id[7:]:
        raise QualificationRefused('archive SHA256 must stay distinct from the local image ID')
    if registry_digest:
        raise QualificationRefused('fake promotion proof: registry digest is absent until main publication')
    if signature_sha256:
        raise QualificationRefused('fake promotion proof: signature is absent until the existing publisher')
    if local_image_id == FROZEN_IMAGE.split('@', 1)[1]:
        raise QualificationRefused('mixed candidate/frozen manifest: local image ID is the frozen registry digest')


def qualify_candidate(source_paths, image_root, source_sha, local_image_id, archive_sha256,
                      registry_digest=None, signature_sha256=None, reviewed_source_sha=None, measure_interpreter=False):
    validate_source_sha(source_sha, reviewed_source_sha)
    assert_distinct_candidate_identity(local_image_id, archive_sha256, registry_digest, signature_sha256)
    image_root = Path(image_root)
    modules = {}
    for name in MODULE_NAMES:
        source = Path(source_paths[name])
        image = image_root / name
        if not source.is_file() or not image.is_file():
            raise QualificationRefused('candidate is missing module %s' % name)
        source_sha256 = sha256_file(source)
        image_sha256 = sha256_file(image)
        if source_sha256 != image_sha256:
            raise QualificationRefused(
                'candidate %s source %s does not match image %s' % (name, source_sha256, image_sha256))
        modules[name] = {'source_sha256': source_sha256, 'image_sha256': image_sha256}
    requirements = Path(source_paths['requirements.txt']).read_text()
    manifest = {
        'manifest_version': MANIFEST_VERSION,
        'kind': 'candidate-qualification',
        'source_sha': source_sha,
        'local_image_id': local_image_id,
        'archive_sha256': archive_sha256,
        'registry_digest': None,
        'signature_sha256': None,
        'admitted_runtime': False,
        'modules': modules,
        'marker': marker_table(requirements),
        'distribution_measurement': distribution_report(measure_interpreter),
    }
    refuse_mixed_manifest(manifest)
    return manifest


def verify_reported_manifest(manifest, source_paths, image_root):
    if manifest.get('kind') != 'candidate-qualification':
        raise QualificationRefused('reported manifest is not a candidate qualification')
    fresh = qualify_candidate(
        source_paths, image_root, manifest['source_sha'], manifest['local_image_id'], manifest['archive_sha256'],
        manifest.get('registry_digest'), manifest.get('signature_sha256'), measure_interpreter=False)
    for name in MODULE_NAMES:
        reported = manifest['modules'][name]
        if reported != fresh['modules'][name]:
            raise QualificationRefused('candidate manifest hashes do not match independently measured bytes for %s' % name)
    return fresh


def refuse_mixed_manifest(manifest):
    if manifest.get('kind') != 'candidate-qualification':
        return
    if manifest.get('admitted_runtime'):
        raise QualificationRefused('mixed candidate/frozen manifest: candidate is not an admitted runtime')
    if manifest.get('registry_digest') or manifest.get('signature_sha256'):
        raise QualificationRefused('mixed candidate/frozen manifest: promotion proof is not part of candidate qualification')
    frozen_digest = FROZEN_IMAGE.split('@', 1)[1]
    if manifest.get('local_image_id') == frozen_digest:
        raise QualificationRefused('mixed candidate/frozen manifest: local image ID is the frozen registry digest')


def workflow_path():
    override = os.environ.get('BOOK_COPY_WORKFLOW')
    path = Path(override) if override else pins.REPO / '.github/workflows/book-copy-writer-build.yml'
    if not path.is_file():
        raise FrozenRuntimeDependency('book-copy-writer workflow is unavailable at %s; publication gate was not skipped' % path)
    return path


def assert_publication_gate(workflow_text):
    if 'pull_request_target' in workflow_text:
        raise QualificationRefused('workflow grants pull_request_target')
    if '\n  validate:\n' not in workflow_text or '\n  publish:\n' not in workflow_text:
        raise QualificationRefused('validate and publish jobs are required')
    header, rest = workflow_text.split('\njobs:\n', 1)
    if 'contents: read\n' not in header:
        raise QualificationRefused('workflow permissions changed')
    if not rest.startswith('  validate:\n'):
        raise QualificationRefused('validate job is required')
    validate, publish = rest[len('  validate:\n'):].split('\n  publish:\n', 1)
    if 'packages:' in validate or 'id-token:' in validate:
        raise QualificationRefused('validate job permission increase')
    markers = (
        'Qualify the frozen signed runtime by immutable digest',
        'Record the candidate archive SHA without publishing',
        'Verify coherent closed native pipeline source dependencies without a network',
        'Upload the validated image artifact',
    )
    positions = []
    for marker in markers:
        index = validate.find(marker)
        if index < 0:
            raise QualificationRefused('validate job is missing %s' % marker)
        positions.append(index)
    if positions != sorted(positions):
        raise QualificationRefused('frozen and candidate qualification do not gate publication')
    closure = validate[positions[2]:positions[3]]
    for token in ('FROZEN_RUNTIME_DIR', 'FROZEN_IMAGE_REF', 'CANDIDATE_SOURCE_SHA',
                  'CANDIDATE_LOCAL_IMAGE_ID', 'CANDIDATE_ARCHIVE_SHA256', 'BOOK_COPY_WORKFLOW'):
        if token not in closure:
            raise QualificationRefused('closure step does not receive %s' % token)
    if 'CANDIDATE_REGISTRY_DIGEST' in workflow_text or 'CANDIDATE_SIGNATURE' in workflow_text:
        raise QualificationRefused('workflow pre-fills a registry digest or signature')
    if validate.count('docker save') != 1:
        raise QualificationRefused('candidate archive must be saved once and then transferred')
    if 'docker build' in publish or 'build-push-action' in publish or 'docker save' in publish:
        raise QualificationRefused('publish rebuilds or re-saves the image')
    if 'needs: validate' not in publish:
        raise QualificationRefused('publish does not need validate')
    if MAIN_ONLY not in publish or MAIN_ONLY not in validate[positions[3]:]:
        raise QualificationRefused('publication or archive transfer is not main-only')
    if 'docker load --input' not in publish or 'sha256sum' not in publish:
        raise QualificationRefused('publish does not load the exact validated archive')
    permissions = publish.split('permissions:', 1)[1].split('steps:', 1)[0]
    lines = [line.strip() for line in permissions.splitlines() if line.strip()]
    if lines != ['contents: read', 'packages: write', 'id-token: write']:
        raise QualificationRefused('publish permissions changed: %s' % lines)


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd', required=True)
    check_ref = sub.add_parser('check-ref')
    check_ref.add_argument('--ref', required=True)
    pulled = sub.add_parser('check-pulled')
    pulled.add_argument('--ref', required=True)
    pulled.add_argument('--repo-digests', required=True)
    pulled.add_argument('--image-id', required=True)
    identity = sub.add_parser('check-identity')
    identity.add_argument('--local-image-id', required=True)
    identity.add_argument('--archive-sha256', required=True)
    args = parser.parse_args(argv)
    if args.cmd == 'check-ref':
        assert_immutable_frozen_ref(args.ref)
    elif args.cmd == 'check-pulled':
        assert_pulled_frozen(args.ref, json.loads(args.repo_digests), args.image_id)
    else:
        assert_distinct_candidate_identity(args.local_image_id, args.archive_sha256, None, None)
    return 0


if __name__ == '__main__':
    sys.exit(main())
