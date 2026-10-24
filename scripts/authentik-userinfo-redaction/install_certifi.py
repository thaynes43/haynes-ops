#!/usr/bin/env python3
"""Overlay the pinned certifi 2026.10.23 wheel onto the image virtualenv.

The wheel bytes are the PyPI file
certifi-2026.10.23-py3-none-any.whl, sha256
e9b54de76e530bb6cf0ccd036787103acb58fd42ba930ea8bf948ddbaa2f61d1,
from https://files.pythonhosted.org/packages/32/d1/a3652fe2c19134d0d15922177821d863cbd7e842d53a21bbe7e8e3a1b5ac/certifi-2026.10.23-py3-none-any.whl.

Upstream commit b60466c9f2882d9cf088943c650b0faac06c080c changes the version
and removes the emSign C1 and C3 roots. Certifi 2026.10.19 already fixes
GHSA-6q48-6c7w-9gw2. The 2026.10.23 removal is root retirement, not a new CVE.
Files that are identical in the two wheels are left untouched. The main
certifi bytecode cache is removed. The unchanged tests bytecode cache stays.
"""
import base64
import hashlib
import io
import os
import re
import shutil
import stat
import zipfile
from pathlib import Path

WHEEL_SHA = "e9b54de76e530bb6cf0ccd036787103acb58fd42ba930ea8bf948ddbaa2f61d1"
WHEEL_URL = "https://files.pythonhosted.org/packages/32/d1/a3652fe2c19134d0d15922177821d863cbd7e842d53a21bbe7e8e3a1b5ac/certifi-2026.10.23-py3-none-any.whl"
OLD_VERSION = "2026.10.19"
NEW_VERSION = "2026.10.23"
OLD_BUNDLE_SHA = "50f79f98dac192b20dbf73f35c56f0b0c557ab267ff5199d1117cc1180b70785"
NEW_BUNDLE_SHA = "2ef52ad56b4803e264045c915e55c236cbfa045b84b81efefc218a19eb44bd2b"
OLD_INIT_SHA = "f8ccfc10235998c7edd340f2f305b5f3d71aed978aea82a02af30d7877bee902"
OLD_CERT_COUNT = 120
NEW_CERT_COUNT = 118
REMOVED_FINGERPRINTS = frozenset((
    "125609aa301da0a249b97a8239cb6a34216f44dcac9f3954b14292f2e8c8608f",
    "bc4d809b15189d78db3e1d8cf4f9726a795da1643ca5f1358e1ddb0edc0d7eb3",
))
SITE = Path("/ak-root/.venv/lib/python3.14/site-packages")
WHEEL = Path("/wheel/certifi-2026.10.23-py3-none-any.whl")
OLD_DIST = "certifi-2026.10.19.dist-info"
NEW_DIST = "certifi-2026.10.23.dist-info"
WHEEL_FILES = {
    "certifi-2026.10.23.dist-info/METADATA": "e463e38f7fe7da36a11e6d3fecc5dfa5b97be8c8022620400750d79ae7d842bc",
    "certifi-2026.10.23.dist-info/RECORD": "cda8e9d40f94e53686674f4848813b12e6a85261f306960fdbee1c6b8587a938",
    "certifi-2026.10.23.dist-info/WHEEL": "98e75747d0b7c0c568bfb71263c3d8f970b3a374a6996087bc642be5cb09a1aa",
    "certifi-2026.10.23.dist-info/licenses/LICENSE": "e93716da6b9c0d5a4a1df60fe695b370f0695603d21f6f83f053e42cfc10caf7",
    "certifi-2026.10.23.dist-info/top_level.txt": "28cbb8bd409fb232eb90f6d235d81d7a44bea552730402453bffe723c345ebe5",
    "certifi/__init__.py": "6c069810be5831ee48e99eb0fbd79c06b51803771f48d7ccd28f9c0c32270e86",
    "certifi/__main__.py": "c410688fdd394d45812d118034e71fee88ba7beddd30fe1c1281bd3b232cd758",
    "certifi/cacert.pem": "2ef52ad56b4803e264045c915e55c236cbfa045b84b81efefc218a19eb44bd2b",
    "certifi/core.py": "5c55f2727746e697f7edac9e17c377d8752e0da7ecca191531b3b80403d61dad",
    "certifi/py.typed": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "certifi/tests/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "certifi/tests/test_certify.py": "7c179d1edf37f7e912b133663735fb46c6338d5d0e5f8915d11c3bc287d227b8",
}
OVERLAY_RELS = (
    "certifi/__init__.py",
    "certifi/cacert.pem",
    "certifi-2026.10.23.dist-info/METADATA",
    "certifi-2026.10.23.dist-info/RECORD",
    "certifi-2026.10.23.dist-info/WHEEL",
    "certifi-2026.10.23.dist-info/licenses/LICENSE",
    "certifi-2026.10.23.dist-info/top_level.txt",
)
MUST_MATCH = {
    "certifi/__main__.py": WHEEL_FILES["certifi/__main__.py"],
    "certifi/core.py": WHEEL_FILES["certifi/core.py"],
    "certifi/py.typed": WHEEL_FILES["certifi/py.typed"],
}
MAY_MATCH = {
    "certifi/tests/__init__.py": WHEEL_FILES["certifi/tests/__init__.py"],
    "certifi/tests/test_certify.py": WHEEL_FILES["certifi/tests/test_certify.py"],
}
_CERT_RE = re.compile(
    br"-----BEGIN CERTIFICATE-----\r?\n(.*?)\r?\n-----END CERTIFICATE-----",
    re.S,
)
_VERSION_RE = re.compile(r'(?m)^__version__ = "([^"]*)"$')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def metadata_version(data):
    fields = {}
    for line in data.decode("utf-8").splitlines():
        if line == "":
            break
        if line[:1] in " \t":
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise SystemExit("metadata header is malformed")
        fields.setdefault(key, value.strip())
    if fields.get("Name") != "certifi":
        raise SystemExit("metadata name is not certifi")
    version = fields.get("Version")
    if not version:
        raise SystemExit("metadata version is missing")
    return version


def module_version(data):
    matches = _VERSION_RE.findall(data.decode("utf-8"))
    if len(matches) != 1:
        raise SystemExit("certifi module version line is not unique")
    return matches[0]


def assert_distribution_versions(metadata, init_bytes):
    meta = metadata_version(metadata)
    module = module_version(init_bytes)
    if meta != NEW_VERSION or module != NEW_VERSION:
        raise SystemExit("installed certifi metadata is not 2026.10.23")
    return {"metadataVersion": meta, "moduleVersion": module}


def pem_fingerprints(data):
    found = []
    for match in _CERT_RE.finditer(data):
        body = re.sub(br"\s+", b"", match.group(1))
        der = base64.b64decode(body, validate=True)
        found.append(sha256(der))
    if not found:
        raise SystemExit("bundle has no certificates")
    return found


def assert_root_set(old_pem, new_pem):
    old_fps = pem_fingerprints(old_pem)
    new_fps = pem_fingerprints(new_pem)
    old_set = set(old_fps)
    new_set = set(new_fps)
    if len(old_fps) != OLD_CERT_COUNT or len(old_set) != OLD_CERT_COUNT:
        raise SystemExit("old bundle certificate set changed")
    if len(new_fps) != NEW_CERT_COUNT or len(new_set) != NEW_CERT_COUNT:
        raise SystemExit("new bundle certificate set changed")
    removed = old_set - new_set
    added = new_set - old_set
    if removed != REMOVED_FINGERPRINTS or added:
        raise SystemExit("root set diff is not only C1 and C3")
    return {
        "removedFingerprints": sorted(removed),
        "addedFingerprints": [],
        "oldCount": len(old_set),
        "newCount": len(new_set),
    }


def assert_bundle_delta(old_pem, new_pem):
    if sha256(old_pem) != OLD_BUNDLE_SHA:
        raise SystemExit("old certifi bundle hash mismatch")
    if sha256(new_pem) != NEW_BUNDLE_SHA:
        raise SystemExit("new certifi bundle hash mismatch")
    return assert_root_set(old_pem, new_pem)


def _safe_name(name):
    if not name or name.startswith("/") or "\\" in name:
        raise SystemExit("wheel path escapes the archive")
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise SystemExit("wheel path escapes the archive")
    return name


def _wheel_payloads(blob):
    if sha256(blob) != WHEEL_SHA:
        raise SystemExit("pinned wheel hash mismatch")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        infos = archive.infolist()
        names = [_safe_name(info.filename) for info in infos]
        if len(names) != len(set(names)) or set(names) != set(WHEEL_FILES):
            raise SystemExit("pinned wheel members mismatch")
        payloads = {}
        for info in infos:
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise SystemExit("pinned wheel contains a symlink")
            data = archive.read(info)
            if sha256(data) != WHEEL_FILES[info.filename]:
                raise SystemExit("pinned wheel file hash mismatch")
            payloads[info.filename] = data
    return payloads


def _reject_symlink(path, stop):
    current = path
    while True:
        if current.is_symlink():
            raise SystemExit("refusing to follow a symlink in the certifi overlay")
        if current == stop:
            return
        if current == current.parent:
            raise SystemExit("certifi path escaped the virtualenv")
        current = current.parent


def _write_file(dest, data, template, stop):
    _reject_symlink(dest.parent, stop)
    if dest.is_symlink():
        raise SystemExit("refusing to replace a symlink")
    missing = []
    parent = dest.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    dest.parent.mkdir(parents=True, exist_ok=True)
    for directory in missing:
        os.chmod(directory, 0o755)
        os.chown(directory, template.st_uid, template.st_gid)
    dest.write_bytes(data)
    os.chmod(dest, 0o644)
    os.chown(dest, template.st_uid, template.st_gid)


def _remove_tree(path):
    if path.is_symlink():
        raise SystemExit("refusing to remove a certifi symlink")
    if not path.is_dir():
        raise SystemExit("certifi removal target is not a directory")
    shutil.rmtree(path)


def install(site, wheel, require_root=True):
    site = Path(site)
    payloads = _wheel_payloads(Path(wheel).read_bytes())
    cacert = site / "certifi" / "cacert.pem"
    init = site / "certifi" / "__init__.py"
    old_dist = site / OLD_DIST
    for path in (cacert, init, old_dist):
        if path.is_symlink():
            raise SystemExit("refusing to follow a certifi symlink")
    old_pem = cacert.read_bytes()
    if sha256(old_pem) != OLD_BUNDLE_SHA:
        raise SystemExit("installed bundle is not certifi 2026.10.19")
    if sha256(init.read_bytes()) != OLD_INIT_SHA:
        raise SystemExit("installed certifi module is not 2026.10.19")
    meta_path = old_dist / "METADATA"
    if not old_dist.is_dir() or meta_path.is_symlink():
        raise SystemExit("installed certifi distribution is not 2026.10.19")
    if metadata_version(meta_path.read_bytes()) != OLD_VERSION:
        raise SystemExit("installed distribution version is not 2026.10.19")
    found = sorted(path.name for path in site.glob("certifi-*.dist-info") if path.is_dir())
    if found != [OLD_DIST]:
        raise SystemExit("unexpected certifi distribution directories")
    for rel, digest in MUST_MATCH.items():
        path = site / rel
        if path.is_symlink() or not path.is_file() or sha256(path.read_bytes()) != digest:
            raise SystemExit("unchanged certifi file does not match the pinned wheel")
    for rel, digest in MAY_MATCH.items():
        path = site / rel
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file() or sha256(path.read_bytes()) != digest:
            raise SystemExit("unchanged certifi test file does not match the pinned wheel")
    delta = assert_bundle_delta(old_pem, payloads["certifi/cacert.pem"])
    template = cacert.stat()
    if require_root and (template.st_uid != 0 or template.st_gid != 0):
        raise SystemExit("certifi files are not root-owned")
    for rel in OVERLAY_RELS:
        _write_file(site / rel, payloads[rel], template, site)
    for name in (
        "INSTALLER", "METADATA", "RECORD", "REQUESTED", "WHEEL", "top_level.txt",
        "licenses/LICENSE",
    ):
        path = old_dist / name
        if path.is_symlink() or not path.is_file():
            raise SystemExit("installed certifi metadata file is missing")
    main_cache = site / "certifi" / "__pycache__"
    if main_cache.is_symlink() or not main_cache.is_dir():
        raise SystemExit("main certifi bytecode cache is missing")
    main_names = sorted(path.name for path in main_cache.iterdir() if path.is_file())
    if main_names != ["__init__.cpython-314.pyc", "__main__.cpython-314.pyc", "core.cpython-314.pyc"]:
        raise SystemExit("main certifi bytecode cache is not the measured set")
    tests_cache = site / "certifi" / "tests" / "__pycache__"
    if tests_cache.is_symlink() or not tests_cache.is_dir():
        raise SystemExit("unchanged certifi tests bytecode cache is missing")
    test_names = sorted(path.name for path in tests_cache.iterdir() if path.is_file())
    if test_names != ["__init__.cpython-314.pyc", "test_certify.cpython-314.pyc"]:
        raise SystemExit("unchanged certifi tests bytecode cache changed")
    preserved_tests = {
        path.name: path.read_bytes() for path in tests_cache.iterdir() if path.is_file()
    }
    _remove_tree(old_dist)
    _remove_tree(main_cache)
    for pyc in (site / "certifi").rglob("*.pyc"):
        if "tests" in pyc.relative_to(site / "certifi").parts:
            continue
        raise SystemExit("non-test certifi bytecode remains")
    if main_cache.exists():
        raise SystemExit("main certifi bytecode cache remains")
    for name, payload in preserved_tests.items():
        current = tests_cache / name
        if current.is_symlink() or not current.is_file() or current.read_bytes() != payload:
            raise SystemExit("unchanged certifi tests bytecode cache was rewritten")
    for rel in OVERLAY_RELS:
        if sha256((site / rel).read_bytes()) != WHEEL_FILES[rel]:
            raise SystemExit("overlay write mismatch")
    versions = assert_distribution_versions(
        (site / NEW_DIST / "METADATA").read_bytes(),
        (site / "certifi" / "__init__.py").read_bytes(),
    )
    found = sorted(path.name for path in site.glob("certifi-*.dist-info") if path.is_dir())
    if found != [NEW_DIST]:
        raise SystemExit("certifi distribution was not replaced")
    return {"bundle": delta, "versions": versions}


def main():
    install(SITE, WHEEL)


if __name__ == "__main__":
    main()
