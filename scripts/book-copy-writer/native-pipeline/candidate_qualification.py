"""Candidate image qualification separate from the frozen runtime.

Frozen pins stay the historical contract. A candidate proves itself by
comparing reviewed source bytes with measured image bytes. The signature
field on the frozen manifest is the pinned receipt; this module does not
re-verify it. Local image ID, archive SHA-256, registry digest, and
signature are different states.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
import stat
import subprocess
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
PUBLISH_IF = 'if: ' + MAIN_ONLY
KNOWN_EVENTS = ('pull_request', 'push', 'workflow_dispatch')
COPY_SOURCE_PATHS = (
    'scripts/book-copy-writer/requirements.txt',
    'kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert/epub_copies.py',
    'kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert/epub_metadata.py',
    'scripts/book-copy-writer/book_copy_writer.py',
    'scripts/book-copy-writer/bound_census.py',
    'scripts/book-copy-writer/proof_transport.py',
)


class QualificationRefused(Exception):
    """The measured bytes or the publication gate do not match the contract."""


class FrozenRuntimeDependency(QualificationRefused):
    """The immutable frozen artifact is missing or is the wrong image."""


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _is_within(path, root):
    try:
        Path(path).relative_to(root)
    except ValueError:
        return False
    return True


def _is_sha(value):
    return isinstance(value, str) and len(value) == 40 and all(char in '0123456789abcdef' for char in value)


def _git(repo, *args, check=True):
    result = subprocess.run(
        ['git', '-C', str(repo), *args], check=False, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise QualificationRefused('git %s failed: %s' % (' '.join(args), result.stderr.strip()))
    return result


def join_pip_logical_lines(requirements_text):
    """Join a physical line ending in ``\\`` with the next line and drop that backslash.

    A comment line does not continue. Callers strip ``#`` comments on the
    logical line afterwards. This is the line join pip uses for requirements,
    not a new dependency resolver.
    """
    logical = []
    pending = ''
    open_continuation = False
    for raw in requirements_text.splitlines():
        current = pending + raw if open_continuation else raw
        continues = raw.endswith('\\') and not raw.lstrip().startswith('#')
        if continues:
            pending = current[:-1]
            open_continuation = True
            continue
        logical.append(current)
        pending = ''
        open_continuation = False
    if open_continuation:
        logical.append(pending)
    return logical


def requirement_line(logical):
    return logical.split('#', 1)[0].strip()


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
    # An explicit environ is the whole mount contract. The default path is
    # visible only to the live process so a negative fixture cannot see CI's mount.
    if environ is None:
        environ = os.environ
        mounted = environ.get('FROZEN_RUNTIME_DIR') or ''
        if not mounted and Path('/frozen-runtime').is_dir():
            mounted = '/frozen-runtime'
    else:
        mounted = environ.get('FROZEN_RUNTIME_DIR') or ''
    if mounted:
        ref = environ.get('FROZEN_IMAGE_REF') or ''
        if not ref:
            raise FrozenRuntimeDependency(
                'frozen runtime mount %s has no immutable image ref; no candidate fallback' % mounted)
        return {name: Path(mounted) / name for name in MODULE_NAMES}, ref
    present = Path('/copy-writer').is_dir() if candidate_present is None else candidate_present
    if present:
        raise FrozenRuntimeDependency(
            'frozen runtime %s was not mounted; pinned signature receipt %s is not re-verified here; no candidate fallback'
            % (FROZEN_IMAGE, FROZEN_SIGNATURE))
    raise FrozenRuntimeDependency(
        'frozen runtime %s is not mounted; pinned signature receipt %s is not re-verified here; no candidate fallback'
        % (FROZEN_IMAGE, FROZEN_SIGNATURE))


def qualify_frozen_runtime(paths, image_ref):
    assert_immutable_frozen_ref(image_ref)
    measured = {}
    for name in MODULE_NAMES:
        path = Path(paths[name])
        if not path.is_file():
            raise FrozenRuntimeDependency(
                'frozen runtime %s is missing %s; no candidate fallback' % (image_ref, name))
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
        'signature_receipt_reverified': False,
        'source_sha': pins.IMAGE_SOURCE,
        'admitted_runtime': True,
        'modules': measured,
    }


def _refuse_leaf_symlink(path):
    if Path(path).is_symlink():
        raise QualificationRefused('reviewed source module is a symlink: %s' % path)


def reviewed_source_path(name):
    if name in ('epub_copies.py', 'epub_metadata.py'):
        extra = os.environ.get('COPY_TEST_MODULES')
        if extra:
            mounted = Path(extra) / name
            _refuse_leaf_symlink(mounted)
            if mounted.is_file():
                return mounted
    path = pins.module_source(name)
    _refuse_leaf_symlink(path)
    if path.is_file():
        return path
    raise QualificationRefused('reviewed source module is missing: %s' % name)


def typing_extensions_marker(requirements_text):
    found = None
    for logical in join_pip_logical_lines(requirements_text):
        line = requirement_line(logical)
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


def required_distributions(requirements_text):
    found = {}
    for logical in join_pip_logical_lines(requirements_text):
        line = requirement_line(logical)
        if not line:
            continue
        body = line.split('--hash', 1)[0].strip()
        marker = None
        if ';' in body:
            body, marker = body.split(';', 1)
            marker = marker.strip()
        if '==' not in body:
            continue
        name, version = body.split('==', 1)
        found[name.strip().lower().replace('_', '-')] = {'version': version.strip(), 'marker': marker}
    if not found:
        raise QualificationRefused('reviewed requirements did not name any distributions')
    return found


def pinned_interpreter(dockerfile_text):
    for line in dockerfile_text.splitlines():
        stripped = line.strip()
        if stripped.startswith('FROM python:'):
            tag = stripped.split(':', 1)[1].split('@', 1)[0]
            return tag.split('-', 1)[0]
    raise QualificationRefused('pinned Python interpreter is absent from the Dockerfile')


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
    requirements_path = Path('/copy-writer/requirements.txt')
    if requirements_path.is_symlink():
        raise QualificationRefused('image requirements.txt is a symlink: %s' % requirements_path)
    requirements = requirements_path.read_text() if requirements_path.is_file() else reviewed_source_path('requirements.txt').read_text()
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


def default_reviewed_roots():
    roots = []
    extra = os.environ.get('COPY_TEST_MODULES')
    if extra:
        roots.append(Path(extra))
    roots.append(pins.HERE.parent)
    epub = pins.REPO / 'kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert'
    if epub.is_dir():
        roots.append(epub)
    packaged = pins.HERE / 'runtime-modules'
    if packaged.is_dir():
        roots.append(packaged)
    return roots


def _assert_regular_leaf(path, role):
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise QualificationRefused('candidate is missing module %s' % path) from exc
    if stat.S_ISLNK(mode):
        raise QualificationRefused('%s is a symlink: %s' % (role, path))
    if not stat.S_ISREG(mode):
        raise QualificationRefused('%s is not a regular file: %s' % (role, path))


def assert_independent_module(source, image, image_root, reviewed_roots):
    source = Path(source)
    image = Path(image)
    image_root_real = Path(image_root).resolve()
    roots = [Path(root).resolve() for root in reviewed_roots]
    if not roots:
        raise QualificationRefused('no reviewed roots were declared')
    for root in roots:
        if root == image_root_real or _is_within(root, image_root_real) or _is_within(image_root_real, root):
            raise QualificationRefused('reviewed root %s is not independent of image root %s' % (root, image_root_real))
    if source.is_symlink() or not source.exists():
        if source.is_symlink():
            raise QualificationRefused('reviewed source is a symlink: %s' % source)
        raise QualificationRefused('candidate is missing module %s' % source.name)
    if image.is_symlink() or not image.exists():
        if image.is_symlink():
            raise QualificationRefused('image file is a symlink: %s' % image)
        raise QualificationRefused('candidate is missing module %s' % image.name)
    _assert_regular_leaf(source, 'reviewed source')
    _assert_regular_leaf(image, 'image file')
    source_real = source.resolve()
    image_real = image.resolve()
    if not any(_is_within(source_real, root) for root in roots):
        raise QualificationRefused('reviewed source %s is outside the declared reviewed roots' % source_real)
    if _is_within(source_real, image_root_real) or source_real == image_real:
        raise QualificationRefused('reviewed source %s resolves inside the image root' % source_real)
    if any(_is_within(image_real, root) for root in roots):
        raise QualificationRefused('image file %s resolves inside a reviewed root' % image_real)


def qualify_candidate(source_paths, image_root, source_sha, local_image_id, archive_sha256,
                      registry_digest=None, signature_sha256=None, reviewed_source_sha=None,
                      measure_interpreter=False, reviewed_roots=None):
    validate_source_sha(source_sha, reviewed_source_sha)
    assert_distinct_candidate_identity(local_image_id, archive_sha256, registry_digest, signature_sha256)
    image_root = Path(image_root)
    if reviewed_roots is None:
        reviewed_roots = default_reviewed_roots()
    modules = {}
    for name in MODULE_NAMES:
        source = Path(source_paths[name])
        image = image_root / name
        assert_independent_module(source, image, image_root, reviewed_roots)
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


def verify_reported_manifest(manifest, source_paths, image_root, reviewed_roots=None):
    if manifest.get('kind') != 'candidate-qualification':
        raise QualificationRefused('reported manifest is not a candidate qualification')
    fresh = qualify_candidate(
        source_paths, image_root, manifest['source_sha'], manifest['local_image_id'], manifest['archive_sha256'],
        manifest.get('registry_digest'), manifest.get('signature_sha256'), measure_interpreter=False,
        reviewed_roots=reviewed_roots)
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


def _workflow_triggers(header):
    if header.count('\non:\n') != 1 and not header.startswith('on:\n'):
        raise QualificationRefused('workflow trigger block is not singular')
    on_block = header.split('on:', 1)[1]
    for stopper in ('\nconcurrency:\n', '\npermissions:\n', '\njobs:\n'):
        if stopper in on_block:
            on_block = on_block.split(stopper, 1)[0]
            break
    triggers = []
    for line in on_block.splitlines():
        if line.startswith('  ') and not line.startswith('   ') and ':' in line:
            triggers.append(line.strip().split(':', 1)[0])
    return triggers


def assert_publication_gate(workflow_text):
    if 'pull_request_target' in workflow_text:
        raise QualificationRefused('workflow grants pull_request_target')
    if '\n  validate:\n' not in workflow_text or '\n  publish:\n' not in workflow_text:
        raise QualificationRefused('validate and publish jobs are required')
    header, rest = workflow_text.split('\njobs:\n', 1)
    if _workflow_triggers(header) != ['pull_request', 'push', 'workflow_dispatch']:
        raise QualificationRefused('workflow trigger is not allowed')
    if header.count('permissions:') != 1:
        raise QualificationRefused('workflow permissions changed')
    perm_lines = [line.strip() for line in header.split('permissions:', 1)[1].splitlines() if line.strip()]
    if perm_lines != ['contents: read']:
        raise QualificationRefused('workflow permissions changed')
    if not rest.startswith('  validate:\n'):
        raise QualificationRefused('validate job is required')
    validate, publish = rest[len('  validate:\n'):].split('\n  publish:\n', 1)
    if 'continue-on-error' in validate:
        raise QualificationRefused('validate continue-on-error')
    if 'packages:' in validate or 'id-token:' in validate:
        raise QualificationRefused('validate job permission increase')
    markers = (
        'Qualify the frozen runtime by immutable digest and pinned signature receipt',
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
    for token in ('FROZEN_RUNTIME_DIR', 'FROZEN_IMAGE_REF', 'FROZEN_LOCAL_IMAGE_ID',
                  'CANDIDATE_SOURCE_SHA', 'CANDIDATE_LOCAL_IMAGE_ID', 'CANDIDATE_ARCHIVE_SHA256',
                  'CANDIDATE_EVENT_SHA', 'CANDIDATE_PR_HEAD_SHA', 'CANDIDATE_PR_BASE_SHA',
                  'BOOK_COPY_WORKFLOW'):
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
    publish_if = next((line.strip() for line in publish.splitlines() if line.strip().startswith('if:')), '')
    if publish_if != PUBLISH_IF or MAIN_ONLY not in validate[positions[3]:]:
        raise QualificationRefused('publication or archive transfer is not main-only')
    if 'docker load --input' not in publish or 'sha256sum' not in publish:
        raise QualificationRefused('publish does not load the exact validated archive')
    permissions = publish.split('permissions:', 1)[1].split('steps:', 1)[0]
    lines = [line.strip() for line in permissions.splitlines() if line.strip()]
    if lines != ['contents: read', 'packages: write', 'id-token: write']:
        raise QualificationRefused('publish permissions changed: %s' % lines)


def assert_clean_copy_sources(repo, paths=COPY_SOURCE_PATHS):
    """Refuse dirty bytes for the Dockerfile COPY sources labelled by HEAD."""
    repo = Path(repo)
    for rel in paths:
        work = repo / rel
        if work.is_symlink() or not work.is_file():
            raise QualificationRefused('COPY source is not a clean regular file: %s' % rel)
        blob = _git(repo, 'rev-parse', 'HEAD:%s' % rel).stdout.strip()
        hashed = _git(repo, 'hash-object', '--', rel).stdout.strip()
        if blob != hashed:
            raise QualificationRefused('COPY source %s bytes differ from HEAD' % rel)
    diff = _git(repo, 'diff', '--quiet', 'HEAD', '--', *paths, check=False)
    if diff.returncode != 0:
        detail = (diff.stdout or diff.stderr).strip()
        raise QualificationRefused('COPY sources differ from HEAD%s' % ((': ' + detail) if detail else ''))
    status = _git(repo, 'status', '--porcelain', '--untracked-files=no', '--', *paths).stdout
    if status.strip():
        raise QualificationRefused('COPY sources are dirty: %s' % status.strip())


def commit_header_parents(repo):
    """Parent SHAs from the raw commit header, not from HEAD^N.

    actions/checkout fetches the pull-request merge with depth 1. That shallow
    commit is grafted as parentless, so HEAD^1 and HEAD^2 do not resolve even
    though the commit object still lists its parent lines. HEAD's own SHA
    covers those lines.
    """
    raw = _git(repo, 'cat-file', 'commit', 'HEAD').stdout
    header = raw.split('\n\n', 1)[0]
    parents = []
    for line in header.splitlines():
        if not line.startswith('parent '):
            continue
        sha = line[len('parent '):].strip()
        if not _is_sha(sha):
            raise QualificationRefused('commit-header parent is not a 40-character SHA')
        parents.append(sha)
    return parents


def bind_checkout_identity(repo, event_name, event_sha, pr_head_sha='', pr_base_sha=''):
    """Bind checkout HEAD to the event SHA, and a PR merge to its header parents.

    On pull_request, GITHUB_SHA is the synthetic merge commit. The pull request
    head and base are recorded separately and must equal commit-header parents
    2 and 1. The binding does not call HEAD^1 or HEAD^2.
    """
    if event_name not in KNOWN_EVENTS:
        raise QualificationRefused('unsupported checkout event %r' % (event_name,))
    if not _is_sha(event_sha):
        raise QualificationRefused('event SHA must be the exact 40-character checkout commit')
    head = _git(repo, 'rev-parse', 'HEAD').stdout.strip()
    if head != event_sha:
        raise QualificationRefused('checkout HEAD does not match the event SHA')
    pr_head_sha = pr_head_sha or ''
    pr_base_sha = pr_base_sha or ''
    if event_name == 'pull_request':
        if not _is_sha(pr_head_sha) or not _is_sha(pr_base_sha):
            raise QualificationRefused('pull request head and base SHAs must be recorded separately from the event SHA')
        parents = commit_header_parents(repo)
        if len(parents) != 2:
            raise QualificationRefused('pull request checkout commit must have exactly two commit-header parents')
        if parents[0] != pr_base_sha:
            raise QualificationRefused('commit-header parent 1 does not match the pull request base')
        if parents[1] != pr_head_sha:
            raise QualificationRefused('commit-header parent 2 does not match the pull request head')
        if pr_head_sha == event_sha:
            raise QualificationRefused('pull request head SHA collapsed into the synthetic event SHA')
    elif pr_head_sha or pr_base_sha:
        raise QualificationRefused('pull request head SHA is only meaningful for pull_request')
    return {
        'event_sha': event_sha,
        'head_sha': head,
        'pr_head_sha': pr_head_sha or None,
        'pr_base_sha': pr_base_sha or None,
    }


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
    sub.add_parser('check-copy-sources')
    sub.add_parser('check-event-binding')
    args = parser.parse_args(argv)
    if args.cmd == 'check-ref':
        assert_immutable_frozen_ref(args.ref)
    elif args.cmd == 'check-pulled':
        assert_pulled_frozen(args.ref, json.loads(args.repo_digests), args.image_id)
    elif args.cmd == 'check-identity':
        assert_distinct_candidate_identity(args.local_image_id, args.archive_sha256, None, None)
    elif args.cmd == 'check-copy-sources':
        assert_clean_copy_sources(Path.cwd())
    else:
        bound = bind_checkout_identity(
            Path.cwd(), os.environ.get('EVENT_NAME', ''), os.environ.get('GITHUB_SHA', ''),
            os.environ.get('PR_HEAD_SHA', ''), os.environ.get('PR_BASE_SHA', ''))
        sys.stdout.write('CANDIDATE_EVENT_SHA=%s\n' % bound['event_sha'])
        if bound['pr_head_sha']:
            sys.stdout.write('CANDIDATE_PR_HEAD_SHA=%s\n' % bound['pr_head_sha'])
            sys.stdout.write('CANDIDATE_PR_BASE_SHA=%s\n' % bound['pr_base_sha'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
