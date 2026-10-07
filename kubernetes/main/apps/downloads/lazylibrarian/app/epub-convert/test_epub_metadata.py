#!/usr/bin/env python3
"""Bounded stdlib safety tests. No calibre or production files needed.

Run once at low priority: nice -n 19 python3 test_epub_metadata.py
Never use CPU stress, wide parallelism or looped tests in the dev-env pod.
"""

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

import epub_metadata as metadata
import epub_convert

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "epub_convert.py")
SERIES = b'''<meta name='calibre:series' content='The Hunger Games' />
<meta name="calibre:series_index" content="3.0"/>
<meta property="belongs-to-collection" id="series">The Hunger Games</meta>
<meta refines="#series" property="collection-type">series</meta>
<meta property="group-position" refines="#series">3</meta>
<meta property="belongs-to-collection" id="set">Another collection</meta>
<meta property="collection-type" refines="#set">set</meta>'''
OPF = b'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" version="3.0" unique-identifier="isbn">
<metadata>
<!-- Leave spelling, entities, whitespace and quotes alone. -->
<dc:title id="title">Mockingjay &amp; more</dc:title>
<meta property="title-type" refines="#title">collection</meta>
<dc:creator id="author">Suzanne Collins</dc:creator>
<meta property="file-as" refines="#author">Collins, Suzanne</meta>
<dc:identifier id="isbn">9780439023511</dc:identifier>
<dc:language>en</dc:language><dc:description><![CDATA[Cover <and> description]]></dc:description>
<meta name="cover" content="cover"/>
''' + SERIES + b'''
</metadata>
<manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/><item id="cover" href="cover.jpg" media-type="image/jpeg"/></manifest>
<spine><itemref idref="chapter"/></spine></package>'''
CONTAINER = b'''<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'''


def fixture(opf=OPF, mimetype_first=True, mimetype_stored=True, extra_members=None):
    members = [("mimetype", b"application/epub+zip"), ("META-INF/container.xml", CONTAINER),
               ("OEBPS/book.opf", opf), ("OEBPS/chapter.xhtml", b"<html><body>A chapter.</body></html>"),
               ("OEBPS/cover.jpg", bytes(range(256)))]
    if not mimetype_first:
        members[0], members[1] = members[1], members[0]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.comment = b"Keep archive comment"
        for name, raw in members + (extra_members or []):
            info = zipfile.ZipInfo(name, (2024, 2, 3, 4, 5, 6))
            info.compress_type = zipfile.ZIP_STORED if name == "mimetype" and mimetype_stored else zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o640) << 16
            info.internal_attr = 1
            info.comment = b"Keep member comment"
            info.extra = b"\xfe\xca\x04\x00keep"
            archive.writestr(info, raw)
    return output.getvalue()


def write(path, raw):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as target:
        target.write(raw)
    os.chmod(path, 0o640)


def read(path):
    with open(path, "rb") as source:
        return source.read()


def snapshot(root):
    result = {}
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            path = os.path.join(folder, name)
            info = os.lstat(path)
            result[os.path.relpath(path, root)] = (info.st_mtime_ns, info.st_ctime_ns,
                                                 read(path) if stat.S_ISREG(info.st_mode) else None)
    return result


class OpfTests(unittest.TestCase):
    def test_only_approved_bytes_removed(self):
        after, inventory = metadata.strip_opf(OPF)
        expected = OPF
        for tag in SERIES.splitlines():
            expected = expected.replace(tag, b"")
        self.assertEqual(after, expected)
        self.assertEqual(len(inventory), 7)
        self.assertIn(b">collection</meta>", after)
        self.assertIn(b"9780439023511", after)
        self.assertEqual(metadata.strip_opf(after), (after, []))

    def test_namespace_prefix_and_utf16_round_trip(self):
        prefixed = OPF.replace(b'<package xmlns=', b'<opf:package xmlns:opf=').replace(b'</package>', b'</opf:package>')
        for name in (b'metadata', b'meta', b'manifest', b'item', b'spine', b'itemref'):
            prefixed = prefixed.replace(b'<' + name, b'<opf:' + name).replace(b'</' + name, b'</opf:' + name)
        self.assertEqual(len(metadata.strip_opf(prefixed)[1]), 7)
        for codec, bom in (("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")):
            encoded = bom + OPF.decode().replace('encoding="UTF-8"', 'encoding="UTF-16"').encode(codec)
            stripped, inventory = metadata.strip_opf(encoded)
            self.assertEqual(len(inventory), 7)
            self.assertIn("9780439023511", stripped[len(bom):].decode(codec))
            self.assertEqual(metadata.strip_opf(stripped), (stripped, []))

    def test_unfamiliar_refinement_refused(self):
        for extra in (b'<meta property="file-as" refines="#series">Keep me</meta>',
                      b'<meta property="display-seq" refines="#series">1</meta>'):
            with self.assertRaisesRegex(metadata.Refused, "unfamiliar refinement"):
                metadata.strip_opf(OPF.replace(b'</metadata>', extra + b'</metadata>'))

    def test_dangling_duplicate_nested_and_entity_xml_refused(self):
        for raw in (OPF.replace(b'refines="#author"', b'refines="#absent"'),
                    OPF.replace(b'id="author"', b'id="title"'),
                    OPF.replace(b'>The Hunger Games</meta>', b'><dc:title>Nested</dc:title></meta>'),
                    OPF.replace(b'<package', b'<!DOCTYPE package [<!ENTITY injected "external">]><package', 1),
                    b'<broken>'):
            with self.assertRaises(metadata.Refused):
                metadata.strip_opf(raw)


class ArchiveTests(unittest.TestCase):
    def test_member_bytes_and_metadata_preserved(self):
        raw = fixture()
        stripped, inventory = metadata.sanitized_epub(raw)
        self.assertEqual(len(inventory), 7)
        with zipfile.ZipFile(io.BytesIO(raw)) as before, zipfile.ZipFile(io.BytesIO(stripped)) as after:
            self.assertEqual(before.comment, after.comment)
            self.assertEqual(before.namelist(), after.namelist())
            self.assertEqual(after.infolist()[0].compress_type, zipfile.ZIP_STORED)
            self.assertIsNone(after.testzip())
            for a, b in zip(before.infolist(), after.infolist()):
                for field in metadata.ZIP_METADATA:
                    self.assertEqual(getattr(a, field), getattr(b, field), (a.filename, field))
                if a.filename != "OEBPS/book.opf":
                    self.assertEqual(before.read(a), after.read(b))
        self.assertEqual(metadata.sanitized_epub(stripped), (stripped, []))

    def test_unsafe_invalid_signed_archives_refused(self):
        inputs = (b'not a zip', fixture(mimetype_first=False), fixture(mimetype_stored=False),
                  fixture(extra_members=[("../outside", b"unsafe")]),
                  fixture(extra_members=[("META-INF/signatures.xml", b"signed")]),
                  fixture(opf=b'<broken>'))
        for raw in inputs:
            with self.subTest(raw=raw[:20]), self.assertRaises(metadata.Refused):
                metadata.sanitized_epub(raw)
        raw = fixture()
        corrupt = raw[:100] + bytes([raw[100] ^ 1]) + raw[101:]
        with self.assertRaises(metadata.Refused):
            metadata.sanitized_epub(corrupt)

    def test_multiple_opfs_stripped(self):
        raw = fixture(extra_members=[("OEBPS/second.opf", OPF)])
        with zipfile.ZipFile(io.BytesIO(raw)) as source:
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w") as destination:
                for info in source.infolist():
                    data = source.read(info)
                    if info.filename == "META-INF/container.xml":
                        data = data.replace(b'</rootfiles>', b'<rootfile full-path="OEBPS/second.opf" media-type="application/oebps-package+xml"/></rootfiles>')
                    destination.writestr(info, data)
        _after, inventory = metadata.sanitized_epub(out.getvalue())
        self.assertEqual(len(inventory), 14)


class GroupingTests(unittest.TestCase):
    def setUp(self):
        self.clean = metadata.strip_opf(OPF)[0].replace(b'Mockingjay &amp; more', b'Night Shift')
        king = self.clean.replace(b'Suzanne Collins', b'Stephen King')
        self.king = metadata.grouping_identity(fixture(opf=king), "Stephen King/Night Shift/King.epub")

    def identity_with(self, tags):
        harris = self.clean.replace(b'Suzanne Collins', b'Charlaine Harris')
        harris = harris.replace(b'</metadata>', tags + b'</metadata>')
        return metadata.grouping_identity(fixture(opf=harris), "Charlaine Harris/Night Shift/Harris.epub")

    def test_inactive_collection_or_calibre_alias_cannot_mask_new_collision(self):
        cases = (
            b'<meta property="belongs-to-collection" id="old">Night Shift</meta>'
            b'<meta property="group-position" refines="#old">1</meta>'
            b'<meta property="belongs-to-collection" id="active">Midnight, Texas</meta>'
            b'<meta property="group-position" refines="#active">3</meta>',
            b'<meta name="calibre:series" content="Night Shift"/>'
            b'<meta name="calibre:series_index" content="1"/>'
            b'<meta property="belongs-to-collection" id="active">Midnight, Texas</meta>'
            b'<meta property="group-position" refines="#active">3</meta>',
            b'<meta property="belongs-to-collection" id="old">Night Shift</meta>'
            b'<meta property="group-position" refines="#old">1</meta>'
            b'<meta name="calibre:series" content="Midnight, Texas"/>'
            b'<meta name="calibre:series_index" content="3"/>',
        )
        for tags in cases:
            identity = self.identity_with(tags)
            self.assertEqual(identity["current_aliases"], {"midnighttexas"})
            self.assertTrue(metadata.collision_conflicts(identity, [identity, self.king]))

    def test_name_or_index_without_its_partner_uses_existing_title_group(self):
        for tags in (b'<meta name="calibre:series" content="Midnight, Texas"/>',
                     b'<meta property="belongs-to-collection" id="series">Midnight, Texas</meta>',
                     b'<meta name="calibre:series_index" content="3"/>'):
            identity = self.identity_with(tags)
            self.assertEqual(identity["current_aliases"], {"nightshift"})
            self.assertFalse(metadata.collision_conflicts(identity, [identity, self.king]))


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "EBooks")
        self.state = os.path.join(self.tmp.name, ".epub-convert")
        self.path = os.path.join(self.root, "Suzanne Collins", "Mockingjay", "Mockingjay.epub")
        self.original = fixture()
        write(self.path, self.original)

    def strip(self, **kwargs):
        return metadata.strip_existing(self.path, self.root, self.state, 0, **kwargs)

    def run_script(self, *args, **extra):
        env = dict(os.environ, EBOOK_ROOT=self.root, STATE_DIR=self.state, SETTLE_SECONDS="0",
                   STRIP_SERIES_METADATA="1", STRIP_ONLY="1", DRY_RUN="0", KAVITA_URL="", KAVITA_API_KEY="")
        env.pop("STRIP_FOLDERS_JSON", None)
        env.update(extra)
        result = subprocess.run([sys.executable, SCRIPT, *args], env=env, capture_output=True, text=True, timeout=20)
        self.assertFalse(result.stderr, result.stderr)
        return result, [json.loads(line) for line in result.stdout.splitlines()]

    def test_dry_run_inventory_and_untagged_do_not_write(self):
        before = snapshot(self.tmp.name)
        result = self.strip(dry_run=True)
        self.assertEqual(result["result"], "would_strip")
        self.assertIn("The Hunger Games", json.dumps(result["metadata"]))
        self.assertIn("3.0", json.dumps(result["metadata"]))
        self.assertEqual(snapshot(self.tmp.name), before)
        write(self.path, metadata.sanitized_epub(self.original)[0])
        before = snapshot(self.tmp.name)
        self.assertEqual(self.strip()["result"], "untagged")
        self.assertEqual(snapshot(self.tmp.name), before)
        self.assertFalse(os.path.exists(self.state))

    def test_backup_replace_idempotence_and_restore(self):
        original_info = os.stat(self.path)
        result = self.strip()
        self.assertEqual(result["result"], "stripped")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o640)
        self.assertNotEqual(os.stat(self.path).st_ino, original_info.st_ino)
        manifest_path = result["backup_manifest"]
        manifest = json.loads(read(manifest_path))
        backup = os.path.join(self.state, "backup", manifest["backup_file"])
        self.assertEqual(read(backup), self.original)
        self.assertEqual(manifest["relative_path"], "Suzanne Collins/Mockingjay/Mockingjay.epub")
        before = snapshot(self.tmp.name)
        self.assertEqual(self.strip()["result"], "untagged")
        self.assertEqual(snapshot(self.tmp.name), before)
        self.assertEqual(metadata.restore_backup(manifest_path, self.root, self.state, True)["result"], "would_restore")
        self.assertEqual(snapshot(self.tmp.name), before)
        restored = metadata.restore_backup(manifest_path, self.root, self.state)
        self.assertEqual(restored["result"], "restored")
        self.assertEqual(read(self.path), self.original)
        self.assertEqual(read(backup), self.original)

    def test_failed_replace_leaves_original_and_keeps_backup(self):
        with mock.patch.object(metadata, "_replace", side_effect=OSError("simulated write failure")):
            with self.assertRaises(OSError):
                self.strip()
        self.assertEqual(read(self.path), self.original)
        self.assertEqual(len(os.listdir(os.path.join(self.state, "backup"))), 2)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["Mockingjay.epub"])

    def test_changed_source_is_not_overwritten(self):
        backup = metadata.backup_original
        changed = fixture(opf=OPF.replace(b'Mockingjay &amp; more', b'Changed source'))

        def interfere(*args):
            result = backup(*args)
            write(self.path, changed)
            return result

        with mock.patch.object(metadata, "backup_original", side_effect=interfere):
            with self.assertRaisesRegex(metadata.Refused, "original changed"):
                self.strip()
        self.assertEqual(read(self.path), changed)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["Mockingjay.epub"])

    def test_corrupt_backup_and_changed_current_refuse_restore(self):
        result = self.strip()
        manifest = json.loads(read(result["backup_manifest"]))
        write(self.path, read(self.path) + b'changed')
        with self.assertRaisesRegex(metadata.Refused, "current EPUB differs"):
            metadata.restore_backup(result["backup_manifest"], self.root, self.state)
        write(os.path.join(self.state, "backup", manifest["backup_file"]), b'corrupt backup')
        with self.assertRaisesRegex(metadata.Refused, "backup checksum"):
            metadata.restore_backup(result["backup_manifest"], self.root, self.state)

    def test_symlinks_hardlinks_nonregular_and_state_in_library_refused(self):
        outside = os.path.join(self.tmp.name, "outside.epub")
        write(outside, self.original)
        os.unlink(self.path)
        os.symlink(outside, self.path)
        with self.assertRaises((metadata.Refused, OSError)):
            self.strip()
        self.assertEqual(read(outside), self.original)
        os.unlink(self.path)
        os.link(outside, self.path)
        with self.assertRaises(metadata.Refused):
            self.strip()
        os.unlink(self.path)
        os.mkfifo(self.path)
        with self.assertRaises(metadata.Refused):
            self.strip()
        os.unlink(self.path)
        write(self.path, self.original)
        with self.assertRaises(metadata.Refused):
            metadata.strip_existing(self.path, self.root, os.path.join(self.root, ".state"), 0)
        os.symlink(os.path.dirname(self.path), os.path.join(self.root, "linked"))
        with self.assertRaises(OSError):
            metadata.strip_existing(os.path.join(self.root, "linked", "Mockingjay.epub"), self.root, self.state, 0)
        os.symlink(os.path.join(self.tmp.name, "new-state"), self.state)
        with self.assertRaises(OSError):
            self.strip()
        self.assertEqual(read(self.path), self.original)

    def test_settling_refused_input_and_gate_off(self):
        result = metadata.strip_existing(self.path, self.root, self.state, 3600)
        self.assertEqual(result["result"], "settling")
        self.assertFalse(os.path.exists(self.state))
        result, lines = self.run_script(STRIP_SERIES_METADATA="0", STRIP_ONLY="0")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(any(line["msg"] == "epub_series_strip" for line in lines))
        self.assertEqual(read(self.path), self.original)
        write(self.path, fixture(opf=OPF.replace(b'</metadata>', b'<meta property="file-as" refines="#series">Keep</meta></metadata>')))
        before = read(self.path)
        result, lines = self.run_script()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(read(self.path), before)
        self.assertTrue(any(line.get("result") == "refused" for line in lines))
        self.assertFalse(os.path.exists(os.path.join(self.state, "backup")))

    def test_targeted_dry_run_stage_and_lock(self):
        other = os.path.join(self.root, "Other Author", "Other Book", "Other.epub")
        write(other, self.original)
        targets = json.dumps(["Suzanne Collins/Mockingjay"])
        before = snapshot(self.tmp.name)
        result, lines = self.run_script(DRY_RUN="1", STRIP_FOLDERS_JSON=targets)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(snapshot(self.tmp.name), before)
        self.assertEqual([line["path"] for line in lines if line.get("result") == "would_strip"],
                         ["Suzanne Collins/Mockingjay/Mockingjay.epub"])
        os.makedirs(os.path.join(self.state, "lock"))
        result, lines = self.run_script(STRIP_FOLDERS_JSON=targets)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(any(line["msg"] == "epub_convert_locked" for line in lines))
        self.assertEqual(read(self.path), self.original)
        os.rmdir(os.path.join(self.state, "lock"))
        result, lines = self.run_script(STRIP_FOLDERS_JSON=targets)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotEqual(read(self.path), self.original)
        self.assertEqual(read(other), self.original)
        census = next(line for line in lines if line["msg"] == "epub_convert_census")
        self.assertEqual(census["series_strip"]["stripped"], 1)
        self.assertEqual(census["kavita_scan"], "not_configured")
        result, lines = self.run_script(STRIP_FOLDERS_JSON=json.dumps(["../outside"]))
        self.assertEqual(result.returncode, 1)
        self.assertTrue(any(line["msg"] == "epub_convert_run_failed" for line in lines))

    def test_restore_cli_and_conversion_helper(self):
        converted = os.path.join(self.tmp.name, "conversion", "book.epub")
        write(converted, self.original)
        relative = "Suzanne Collins/Mockingjay/Mockingjay.epub"
        result = metadata.strip_converted(converted, relative, self.root, self.state)
        self.assertEqual(len(result["metadata"]), 7)
        self.assertFalse(metadata.inspect_epub(read(converted))[3])
        self.assertEqual(read(self.path), self.original)
        write(self.path, read(converted))
        process, lines = self.run_script("--restore-backup", result["backup_manifest"], STRIP_SERIES_METADATA="0", STRIP_ONLY="0")
        self.assertEqual(process.returncode, 0, process.stdout)
        self.assertEqual(read(self.path), self.original)
        self.assertTrue(any(line.get("result") == "restored" for line in lines))
        self.assertFalse(os.path.exists(os.path.join(self.state, "lock")))

    def test_new_cross_author_collision_is_held(self):
        harris = OPF.replace(b'Mockingjay &amp; more', b'Night Shift').replace(b'Suzanne Collins', b'Charlaine Harris')
        harris = harris.replace(b'The Hunger Games', b'Midnight, Texas')
        write(self.path, fixture(opf=harris))
        king_opf = harris.replace(b'Charlaine Harris', b'Stephen King')
        for tag in SERIES.splitlines():
            king_opf = king_opf.replace(tag.replace(b'The Hunger Games', b'Midnight, Texas'), b'')
        king = os.path.join(self.root, "Stephen King", "Night Shift", "Night Shift.epub")
        write(king, fixture(opf=king_opf))
        before = read(self.path)
        result, lines = self.run_script(STRIP_FOLDERS_JSON=json.dumps(["Suzanne Collins/Mockingjay"]))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(read(self.path), before)
        self.assertFalse(os.path.exists(os.path.join(self.state, "backup")))
        held = next(line for line in lines if line.get("result") == "collision_held")
        self.assertEqual(held["conflicts"][0]["path"], "Stephen King/Night Shift/Night Shift.epub")
        self.assertEqual(held["conflicts"][0]["aliases"], ["nightshift"])
        self.assertIn("Midnight, Texas", json.dumps(held["metadata"]))

    def test_unknown_identity_blocks_targeted_pass_without_rewrite_requirements(self):
        # A readable untagged EPUB whose mimetype is compressed/second still contributes identity aliases.
        clean = metadata.strip_opf(OPF)[0].replace(b'Mockingjay &amp; more', b'Other Title')
        other = os.path.join(self.root, "Other", "Other Title", "Other.epub")
        write(other, fixture(opf=clean, mimetype_first=False, mimetype_stored=False))
        result, lines = self.run_script(DRY_RUN="1", STRIP_FOLDERS_JSON=json.dumps(["Suzanne Collins/Mockingjay"]))
        self.assertEqual(result.returncode, 0, result.stdout)
        write(other, b'broken ZIP')
        result, lines = self.run_script(STRIP_FOLDERS_JSON=json.dumps(["Suzanne Collins/Mockingjay"]))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(read(self.path), self.original)
        error = next(line for line in lines if line["msg"] == "epub_series_preflight")
        self.assertEqual(error["path"], "Other/Other Title/Other.epub")
        self.assertFalse(os.path.exists(os.path.join(self.state, "backup")))

    def test_preflight_budget_and_kavita_normalization(self):
        self.assertEqual(metadata.kavita_normalized(" Thé Night_Shift!＋ 2"), "thénightshift!＋2")
        self.assertNotEqual(metadata.kavita_normalized("The Night Shift"), metadata.kavita_normalized("Night Shift"))
        result, lines = self.run_script(RUN_BUDGET_SECONDS="0")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(read(self.path), self.original)
        self.assertTrue(any("budget" in line.get("detail", "") for line in lines))

    def test_killed_conversion_publication_pair_is_recovered_safely(self):
        folder = os.path.join(self.root, "Other Author", "Recovery")
        partial = os.path.join(folder, ".Recovery.epub.partial")
        published = os.path.join(folder, "Recovery.epub")
        write(partial, self.original)
        os.link(partial, published)
        self.assertEqual(os.stat(published).st_nlink, 2)
        with mock.patch.object(epub_convert, "DRY_RUN", False):
            self.assertEqual(epub_convert.clean_partials(self.root), 1)
        self.assertFalse(os.path.lexists(partial))
        self.assertEqual(os.stat(published).st_nlink, 1)
        self.assertEqual(read(published), self.original)

    def test_existing_cross_author_group_is_not_a_new_collision(self):
        raw = fixture(opf=OPF.replace(b'Mockingjay &amp; more', b'The Hunger Games'))
        a = metadata.grouping_identity(raw, "Suzanne Collins/The Hunger Games/a.epub")
        b = metadata.grouping_identity(raw.replace(b'Suzanne Collins', b'Suzanne Collins'), "Other/Book/b.epub")
        b["authors"] = {"other author"}
        # Remove the unrelated second collection alias to model an existing shared exact title group.
        a["current_aliases"] = a["projected_aliases"]
        self.assertFalse(metadata.collision_conflicts(a, [a, b]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
