#!/usr/bin/env python3
"""Bounded stdlib safety tests. No calibre or production files needed.

Run once at low priority: nice -n 19 python3 test_epub_metadata.py
Never use CPU stress, wide parallelism or looped tests in the dev-env pod.
"""

import contextlib
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


def fixture(opf=OPF, mimetype_first=True, mimetype_stored=True, extra_members=None, zero_attributes=()):
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
            if name in zero_attributes:
                info.external_attr = 0  # Actual publisher/iTunes archives contain this unset value.
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

    def test_dedicated_grouping_escapes_xml_preserves_other_bytes_and_is_idempotent(self):
        grouping = 'Mockingjay & more (A <Writer> "Jr")'
        after, inventory = metadata.strip_opf(OPF, grouping)
        self.assertEqual(len(inventory), 7)
        nodes = metadata.xml_nodes(after)[0]
        tags = [node for node in nodes if node["attrs"].get("name") == "calibre:series"]
        self.assertEqual([node["attrs"]["content"] for node in tags], [grouping])
        self.assertIn(b'<dc:title id="title">Mockingjay &amp; more</dc:title>', after)
        self.assertIn(b'<dc:identifier id="isbn">9780439023511</dc:identifier>', after)
        self.assertIn(b'<![CDATA[Cover <and> description]]>', after)
        self.assertEqual(metadata.strip_opf(after, grouping), (after, []))
        prefixed = OPF.replace(b'<package xmlns=', b'<opf:package xmlns:opf=').replace(b'</package>', b'</opf:package>')
        for name in (b'metadata', b'meta', b'manifest', b'item', b'spine', b'itemref'):
            prefixed = prefixed.replace(b'<' + name, b'<opf:' + name).replace(b'</' + name, b'</opf:' + name)
        for raw in (prefixed, b'\xff\xfe' + OPF.decode().replace('encoding="UTF-8"', 'encoding="UTF-16"').encode("utf-16-le")):
            changed, _inventory = metadata.strip_opf(raw, grouping)
            self.assertEqual(metadata.strip_opf(changed, grouping), (changed, []))
            self.assertEqual([node["attrs"]["content"] for node in metadata.xml_nodes(changed)[0]
                              if node["attrs"].get("name") == "calibre:series"], [grouping])

    def test_lone_series_index_removed_but_unrelated_group_position_preserved(self):
        clean = metadata.strip_opf(OPF)[0]
        unrelated = b'<meta property="group-position" refines="#isbn">7</meta>'
        index = b'<meta name="calibre:series_index" content="3"/>'
        raw = clean.replace(b'</metadata>', unrelated + index + b'</metadata>')
        after, inventory = metadata.strip_opf(raw)
        self.assertEqual(after, raw.replace(index, b''))
        self.assertEqual(len(inventory), 1)
        self.assertIn(unrelated, after)

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
        inputs = (b'not a zip',
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

    def test_unset_publisher_and_itunes_member_attributes_are_preserved(self):
        raw = fixture(extra_members=[("iTunesMetadata.plist", b"unchanged store metadata")],
                      zero_attributes=("mimetype", "OEBPS/book.opf", "iTunesMetadata.plist"))
        after, inventory = metadata.sanitized_epub(raw)
        self.assertTrue(inventory)
        with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(io.BytesIO(after)) as candidate:
            for name in ("mimetype", "OEBPS/book.opf", "iTunesMetadata.plist"):
                self.assertEqual(source.getinfo(name).external_attr, 0)
                self.assertEqual(candidate.getinfo(name).external_attr, 0)
            self.assertEqual(candidate.read("iTunesMetadata.plist"), source.read("iTunesMetadata.plist"))

    def test_tagged_input_mimetype_normalized_and_other_members_preserved(self):
        for first, stored in ((False, True), (True, False), (False, False)):
            raw = fixture(mimetype_first=first, mimetype_stored=stored, zero_attributes=("mimetype",))
            with self.assertRaises(metadata.Refused):
                metadata.inspect_epub(raw)
            after, inventory = metadata.sanitized_epub(raw)
            self.assertTrue(inventory)
            metadata.inspect_epub(after)  # Full CRC/canonical candidate proof remains strict.
            with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(io.BytesIO(after)) as candidate:
                self.assertEqual(candidate.namelist(), ["mimetype"] + [n for n in source.namelist() if n != "mimetype"])
                self.assertEqual(candidate.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
                self.assertEqual(candidate.getinfo("mimetype").external_attr, 0)
                for name in source.namelist():
                    if name == "mimetype":
                        continue
                    for field in metadata.ZIP_METADATA:
                        self.assertEqual(getattr(source.getinfo(name), field), getattr(candidate.getinfo(name), field))
                    if name != "OEBPS/book.opf":
                        self.assertEqual(source.read(name), candidate.read(name))
            self.assertEqual(metadata.sanitized_epub(after), (after, []))
        with zipfile.ZipFile(io.BytesIO(fixture())) as source:
            malformed = io.BytesIO()
            with zipfile.ZipFile(malformed, "w") as destination:
                for info in source.infolist():
                    destination.writestr(info, b"wrong MIME value" if info.filename == "mimetype" else source.read(info))
        with self.assertRaisesRegex(metadata.Refused, "mimetype"):
            metadata.sanitized_epub(malformed.getvalue())

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


class ConversionSourceTests(unittest.TestCase):
    def test_large_hardlinked_source_is_checked_without_reading_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "large.azw3")
            write(source, b"AZW3")
            os.link(source, os.path.join(tmp, "retained-download.azw3"))
            # A sparse logical size exercises the bound without allocating/reading a 256 MiB fixture.
            os.truncate(source, metadata.MAX_ARCHIVE + 1)
            with metadata.safe_directory(tmp) as directory:
                with mock.patch.object(metadata.os, "read", side_effect=AssertionError("source content read")), \
                        mock.patch.object(metadata.os, "fdopen", side_effect=AssertionError("source buffered")), \
                        mock.patch("builtins.open", side_effect=AssertionError("source buffered")):
                    info = metadata.stat_conversion_source(directory, "large.azw3")
                self.assertEqual(info.st_size, metadata.MAX_ARCHIVE + 1)
                self.assertEqual(info.st_nlink, 2)
                self.assertTrue(stat.S_ISREG(info.st_mode))
                with self.assertRaises(metadata.Refused):
                    metadata.read_regular(directory, "large.azw3")

    def test_conversion_source_symlink_and_fifo_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = os.path.join(tmp, "original.mobi")
            write(original, b"MOBI")
            os.symlink(original, os.path.join(tmp, "linked.mobi"))
            os.mkfifo(os.path.join(tmp, "pipe.mobi"))
            with metadata.safe_directory(tmp) as directory:
                with self.assertRaisesRegex(metadata.Refused, "symlink"):
                    metadata.stat_conversion_source(directory, "linked.mobi")
                with self.assertRaisesRegex(metadata.Refused, "regular file"):
                    metadata.stat_conversion_source(directory, "pipe.mobi")
            self.assertEqual(read(original), b"MOBI")

    def test_plain_conversion_copy_exceeds_rewrite_bound_in_fixed_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = b"candidate EPUB bytes" * 100
            write(os.path.join(tmp, "book.epub"), payload)
            real_read, requests = os.read, []
            def bounded_read(descriptor, size):
                requests.append(size)
                self.assertLessEqual(size, 1024 * 1024)
                return real_read(descriptor, size)
            with metadata.safe_directory(tmp) as directory, \
                    mock.patch.object(metadata, "MAX_ARCHIVE", 16), \
                    mock.patch.object(metadata.os, "read", side_effect=bounded_read), \
                    mock.patch.object(metadata.os, "fdopen", side_effect=AssertionError("whole output buffered")):
                self.assertEqual(metadata.copy_conversion_output(directory, "book.epub", directory, ".book.epub.partial"), len(payload))
            self.assertEqual(read(os.path.join(tmp, ".book.epub.partial")), payload)
            self.assertGreater(len(requests), 2)  # copy, EOF and verified read-back


class MetadataIdentityTests(unittest.TestCase):
    def file_identity(self, raw, relative="Suzanne Collins/Mockingjay/Book.epub"):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "Book.epub"), raw)
            with metadata.safe_directory(tmp) as directory:
                return metadata.grouping_identity_file(directory, "Book.epub", relative)

    def test_unrelated_crc_and_rewrite_size_are_not_identity_requirements(self):
        clean = metadata.strip_opf(OPF)[0]
        raw = fixture(opf=clean, mimetype_first=False, mimetype_stored=False)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            body = archive.getinfo("OEBPS/chapter.xhtml")
        offset = body.header_offset
        start = offset + 30 + int.from_bytes(raw[offset + 26:offset + 28], "little") + int.from_bytes(raw[offset + 28:offset + 30], "little")
        raw = raw[:start] + bytes([raw[start] ^ 255]) + raw[start + 1:]
        with mock.patch.object(metadata, "MAX_ARCHIVE", 16), \
                mock.patch.object(metadata, "read_regular", side_effect=AssertionError("whole EPUB read")), \
                mock.patch.object(zipfile.ZipFile, "testzip", side_effect=AssertionError("all member CRCs read")):
            identity = self.file_identity(raw)
        self.assertFalse(identity["has_series_metadata"])
        self.assertEqual(identity["projected_aliases"], {"mockingjaymore"})
        self.assertIn("source_identity", identity)
        with self.assertRaises(metadata.Refused):
            metadata.sanitized_epub(raw)

    def test_harmless_dtd_identity_is_read_without_entity_resolution(self):
        clean = metadata.strip_opf(OPF)[0]
        for declaration in (b'<!DOCTYPE package>', b'<!DOCTYPE package SYSTEM "https://invalid.example/unused.dtd">'):
            raw = fixture(opf=clean.replace(b'<package', declaration + b'<package', 1))
            self.assertEqual(self.file_identity(raw)["projected_aliases"], {"mockingjaymore"})
            with self.assertRaises(metadata.Refused):
                metadata.sanitized_epub(raw)
        for declaration in (b'<!DOCTYPE package [<!ENTITY local "Mockingjay">]>',
                            b'<!DOCTYPE package [<!ENTITY remote SYSTEM "file:///etc/passwd">]>'):
            with self.assertRaises(metadata.Refused):
                self.file_identity(fixture(opf=clean.replace(b'<package', declaration + b'<package', 1)))
        with self.assertRaises(metadata.Refused):
            self.file_identity(fixture(opf=clean.replace(b'Mockingjay &amp; more', b'&undefined;')))

    def test_two_level_placement_uses_creator_and_empty_untagged_is_unindexed(self):
        clean = metadata.strip_opf(OPF)[0]
        identity = self.file_identity(fixture(opf=clean), "Penny Dreadfuls/Book.epub")
        self.assertEqual(identity["authors"], {"collins suzanne"})
        empty = clean.replace(b'<dc:title id="title">Mockingjay &amp; more</dc:title>', b'').replace(b'<dc:creator id="author">Suzanne Collins</dc:creator>', b'')
        identity = self.file_identity(fixture(opf=empty), "Buffalo Gals/Book.epub")
        self.assertEqual(identity["projected_aliases"], set())
        self.assertEqual(identity["current_aliases"], set())
        self.assertEqual(identity["authors"], set())
        self.assertFalse(identity["has_series_metadata"])

    def test_missing_title_active_series_is_known_but_cannot_be_stripped(self):
        raw = OPF.replace(b'<dc:title id="title">Mockingjay &amp; more</dc:title>', b'')
        identity = self.file_identity(fixture(opf=raw))
        self.assertTrue(identity["has_series_metadata"])
        self.assertEqual(identity["current_aliases"], {"anothercollection"})
        self.assertIn("no first dc:title", identity["strip_refusal"])

    def test_unrelated_group_position_is_untagged_and_xml_reads_remain_bounded(self):
        clean = metadata.strip_opf(OPF)[0].replace(b'</metadata>', b'<meta property="group-position">2</meta></metadata>')
        self.assertFalse(self.file_identity(fixture(opf=clean))["has_series_metadata"])
        with mock.patch.object(metadata, "MAX_XML", 8), self.assertRaisesRegex(metadata.Refused, "XML"):
            self.file_identity(fixture(opf=clean))
        source = metadata._MetadataReader(io.BytesIO(b"small"), 40 * 1024 * 1024, float("inf"))
        with self.assertRaisesRegex(metadata.Refused, "32 MiB"):
            source.read()


class ConversionPublicationTests(unittest.TestCase):
    def test_gate_off_large_candidate_publishes_and_permanent_refusal_is_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = os.path.join(tmp, "EBooks"), os.path.join(tmp, ".epub-convert")
            folder = os.path.join(root, "Suzanne Collins", "Mockingjay")
            source = os.path.join(folder, "Mockingjay.mobi")
            write(source, b"retained source")
            payload = fixture()
            def convert(_source, target, _deadline):
                write(target, payload)
                return None, ""
            with mock.patch.multiple(epub_convert, EBOOK_ROOT=root, STATE_DIR=state, DRY_RUN=False,
                                     STRIP_SERIES_METADATA=False, STRIP_ONLY=False, SETTLE_SECONDS=0), \
                    mock.patch.object(epub_convert, "convert", side_effect=convert) as converter, \
                    mock.patch.object(epub_convert, "read_meta", return_value=("Mockingjay", "Suzanne Collins")), \
                    mock.patch.object(epub_convert, "kavita_scan", return_value="ok"), \
                    mock.patch.object(sys, "argv", [SCRIPT]), \
                    mock.patch.object(metadata, "MAX_ARCHIVE", 16), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(epub_convert.main(), 0)
                self.assertEqual(read(os.path.join(folder, "Mockingjay.epub")), payload)
                self.assertEqual(read(source), b"retained source")
                os.unlink(os.path.join(folder, "Mockingjay.epub"))
                with mock.patch.object(metadata, "copy_conversion_output", side_effect=metadata.Refused("permanent candidate constraint")):
                    self.assertEqual(epub_convert.main(), 0)
                attempts = converter.call_count
                self.assertEqual(epub_convert.main(), 0)
                self.assertEqual(converter.call_count, attempts)
            self.assertIn("permanent candidate constraint", read(os.path.join(state, "held.tsv")).decode())
            self.assertFalse(os.path.exists(os.path.join(folder, ".Mockingjay.epub.partial")))


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

    def test_noncanonical_original_is_backed_up_and_restored_exactly(self):
        original = fixture(mimetype_first=False, mimetype_stored=False, zero_attributes=("mimetype",))
        write(self.path, original)
        result = self.strip()
        metadata.inspect_epub(read(self.path))
        manifest = json.loads(read(result["backup_manifest"]))
        self.assertEqual(read(os.path.join(self.state, "backup", manifest["backup_file"])), original)
        metadata.restore_backup(result["backup_manifest"], self.root, self.state)
        self.assertEqual(read(self.path), original)

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

    def test_new_cross_author_collision_gets_owner_grouping_and_untagged_peer_follows(self):
        harris = OPF.replace(b'Mockingjay &amp; more', b'Night Shift').replace(b'Suzanne Collins', b'Charlaine Harris')
        harris = harris.replace(b'The Hunger Games', b'Midnight, Texas')
        write(self.path, fixture(opf=harris))
        king_opf = harris.replace(b'Charlaine Harris', b'Stephen King')
        for tag in SERIES.splitlines():
            king_opf = king_opf.replace(tag.replace(b'The Hunger Games', b'Midnight, Texas'), b'')
        king = os.path.join(self.root, "Stephen King", "Night Shift", "Night Shift.epub")
        write(king, fixture(opf=king_opf))
        before = read(self.path), read(king)
        result, lines = self.run_script(STRIP_FOLDERS_JSON=json.dumps(["Suzanne Collins/Mockingjay"]))
        self.assertEqual(result.returncode, 0, result.stdout)
        changed = next(line for line in lines if line.get("result") == "stripped")
        self.assertEqual(changed["grouping"], "Night Shift (Charlaine Harris)")
        self.assertIn("Midnight, Texas", json.dumps(changed["metadata"]))
        self.assertEqual(read(king), before[1], "targeted stage does not edit the other folder")
        self.assertEqual(read(self.path), metadata.sanitized_epub(before[0], changed["grouping"])[0])
        result, lines = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(metadata.grouping_identity(read(king), "Stephen King/Night Shift/Night Shift.epub")["current_aliases"],
                         {"nightshiftstephenking"})
        before_repeat = snapshot(self.root)
        result, lines = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(snapshot(self.root), before_repeat, "correct dedicated tags retain bytes and timestamps")
        self.assertEqual(next(line for line in lines if line["msg"] == "epub_series_strip_census")["grouped"], 2)

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

    def test_untagged_nonconforming_files_are_untouched_and_tagged_fingerprint_is_checked(self):
        clean = metadata.strip_opf(OPF)[0]
        harmless = clean.replace(b'<package', b'<!DOCTYPE package><package', 1)
        other = os.path.join(self.root, "Other Author", "Other Book", "Other.epub")
        write(other, fixture(opf=harmless, mimetype_first=False, mimetype_stored=False))
        before = read(other), os.stat(other).st_mtime_ns, os.stat(other).st_ctime_ns
        result, lines = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((read(other), os.stat(other).st_mtime_ns, os.stat(other).st_ctime_ns), before)
        census = next(line for line in lines if line["msg"] == "epub_series_strip_census")
        self.assertEqual(census["stripped"], 1)
        self.assertEqual(census["untagged"], 1)
        write(self.path, self.original)
        with metadata.safe_directory(os.path.dirname(self.path)) as directory:
            identity = metadata.grouping_identity_file(directory, os.path.basename(self.path), "Suzanne Collins/Mockingjay/Mockingjay.epub")
        changed = self.original + b"changed after preflight"
        write(self.path, changed)
        with self.assertRaisesRegex(metadata.Changed, "since grouping preflight"):
            metadata.strip_existing(self.path, self.root, self.state, 0, expected_source_identity=identity["source_identity"])
        self.assertEqual(read(self.path), changed)

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

    def test_existing_untagged_cross_author_group_is_split_without_other_member_edits(self):
        clean = metadata.strip_opf(OPF)[0].replace(b'Mockingjay &amp; more', b'City of Bones')
        write(self.path, fixture(opf=clean.replace(b'Suzanne Collins', b'Cassandra Clare')))
        other = os.path.join(self.root, "Martha Wells", "City of Bones", "other.epub")
        write(other, fixture(opf=clean.replace(b'Suzanne Collins', b'Martha Wells')))
        before = {path: metadata.inspect_epub(read(path))[1] for path in (self.path, other)}
        process, lines = self.run_script()
        self.assertEqual(process.returncode, 0, process.stdout)
        self.assertEqual(next(line for line in lines if line["msg"] == "epub_series_strip_census")["stripped"], 2)
        for path, author in ((self.path, "Cassandra Clare"), (other, "Martha Wells")):
            after = metadata.inspect_epub(read(path))[1]
            self.assertEqual({name: raw for name, raw in after.items() if not name.endswith(".opf")},
                             {name: raw for name, raw in before[path].items() if not name.endswith(".opf")})
            identity = metadata.grouping_identity(read(path), os.path.relpath(path, self.root))
            self.assertEqual(identity["current_aliases"], {metadata.kavita_normalized(f"City of Bones ({author})")})

    def test_ambiguous_cross_author_metadata_is_held_without_title_edits_or_run_failure(self):
        other = os.path.join(self.root, "Other", "Book", "other.epub")
        raw = OPF.replace(b'<dc:creator id="author">', b'<dc:creator>Another Author</dc:creator><dc:creator id="author">')
        write(other, fixture(opf=raw))
        original = snapshot(self.root)
        process, lines = self.run_script()
        self.assertEqual(process.returncode, 0, process.stdout)
        self.assertEqual(snapshot(self.root), original)
        self.assertTrue(any("ambiguous" in line.get("detail", "") for line in lines))
        self.assertEqual(next(line for line in lines if line["msg"] == "epub_series_strip_census")["collision_held"], 2)

    def test_configured_hold_is_separate_and_scoped_runs_preserve_it(self):
        held_folder = os.path.join(self.root, "Daniel Silva", "Ransom")
        held = os.path.join(held_folder, "Ransom.epub")
        write(held, fixture(opf=OPF.replace(b'Mockingjay &amp; more', b'Ransom')
                            .replace(b'Suzanne Collins', b'Daniel Silva')))
        before = read(held), os.stat(held).st_mtime_ns, os.stat(held).st_ctime_ns
        holds = json.dumps(["Daniel Silva/Ransom"])
        process, lines = self.run_script(LIBRARY_HOLD_FOLDERS_JSON=holds)
        self.assertEqual(process.returncode, 0, process.stdout)
        self.assertEqual((read(held), os.stat(held).st_mtime_ns, os.stat(held).st_ctime_ns), before)
        census = next(line for line in lines if line["msg"] == "epub_series_strip_census")
        self.assertEqual((census["stripped"], census["configured_held"], census["untagged"], census["collision_held"]),
                         (1, 1, 0, 0))
        self.assertEqual(next(line for line in lines if line["msg"] == "epub_series_preflight")["epub_count"], 2)
        configured = next(line for line in lines if line.get("result") == "configured_held")
        self.assertEqual(configured["path"], "Daniel Silva/Ransom/Ransom.epub")
        self.assertEqual(configured["held_folder"], "Daniel Silva/Ransom")
        process, lines = self.run_script(LIBRARY_HOLD_FOLDERS_JSON=holds,
                                        STRIP_FOLDERS_JSON=holds)
        self.assertEqual(process.returncode, 0, process.stdout)
        self.assertEqual(next(line for line in lines if line["msg"] == "epub_series_strip_census")["configured_held"], 1)
        self.assertEqual(read(held), before[0])

    def test_configured_hold_still_contributes_collision_identity(self):
        # A held book is read by preflight, so an unheld peer cannot merge into it after stripping.
        raw = metadata.strip_opf(OPF)[0].replace(b'Suzanne Collins', b'Daniel Silva')
        held = os.path.join(self.root, "Daniel Silva", "Ransom", "Ransom.epub")
        write(held, fixture(opf=raw))
        process, lines = self.run_script(LIBRARY_HOLD_FOLDERS_JSON='["Daniel Silva/Ransom"]')
        self.assertEqual(process.returncode, 0, process.stdout)
        census = next(line for line in lines if line["msg"] == "epub_series_strip_census")
        self.assertEqual((census["stripped"], census["collision_held"], census["configured_held"], census["untagged"]),
                         (1, 0, 1, 0))
        self.assertEqual(metadata.grouping_identity(read(self.path), "Suzanne Collins/Mockingjay/Mockingjay.epub")["current_aliases"],
                         {"mockingjaymoresuzannecollins"})
        self.assertEqual(read(held), fixture(opf=raw))

    def test_configured_hold_preserves_gate_off_conversion_and_partials(self):
        held_folder = os.path.join(self.root, "Daniel Silva", "Ransom")
        source = os.path.join(held_folder, "Ransom.mobi")
        partial = os.path.join(held_folder, ".Ransom.epub.partial")
        nested = os.path.join(held_folder, "Supplement", ".notes.epub.partial")
        write(source, b"retained source")
        write(partial, b"retained partial")
        write(nested, b"retained nested partial")
        near = os.path.join(self.root, "Daniel Silva", "Ransom Again", "Other.mobi")
        near_partial = os.path.join(os.path.dirname(near), ".Other.epub.partial")
        write(near, b"unheld source")
        write(near_partial, b"ordinary stale partial")
        with mock.patch.dict(os.environ, LIBRARY_HOLD_FOLDERS_JSON='["Daniel Silva/Ransom"]'), \
                mock.patch.object(epub_convert, "LIBRARY_HOLDS", None), \
                mock.patch.object(epub_convert, "EBOOK_ROOT", self.root), \
                mock.patch.object(epub_convert, "DRY_RUN", False):
            self.assertEqual(epub_convert.find_candidates(self.root), [(os.path.dirname(near), ["Other.mobi"])])
            self.assertEqual(epub_convert.clean_partials(self.root), 1)
            with mock.patch.object(epub_convert.subprocess, "run", side_effect=AssertionError("must not convert")):
                self.assertEqual(epub_convert.convert_folder(held_folder, "Ransom.mobi"), "configured_held")
        self.assertEqual((read(source), read(partial), read(nested)),
                         (b"retained source", b"retained partial", b"retained nested partial"))
        self.assertFalse(os.path.exists(near_partial))
        os.unlink(near)  # Isolate the process-level gate-off path from calibre work.
        process, lines = self.run_script(LIBRARY_HOLD_FOLDERS_JSON='["Daniel Silva/Ransom"]',
                                        STRIP_SERIES_METADATA="0", STRIP_ONLY="0")
        self.assertEqual(process.returncode, 0, process.stdout)
        self.assertEqual((read(source), read(partial), read(nested)),
                         (b"retained source", b"retained partial", b"retained nested partial"))
        self.assertEqual(next(line for line in lines if line["msg"] == "epub_convert_census")["configured_hold_folders"],
                         ["Daniel Silva/Ransom"])

    def test_configured_hold_blocks_direct_strip_conversion_and_restore(self):
        manifest = self.strip()["backup_manifest"]
        converted = os.path.join(self.tmp.name, "conversion", "book.epub")
        write(converted, self.original)
        relative = os.path.relpath(self.path, self.root)
        before = snapshot(self.tmp.name)
        with mock.patch.dict(os.environ, LIBRARY_HOLD_FOLDERS_JSON='["Suzanne Collins/Mockingjay"]'):
            with self.assertRaisesRegex(metadata.Refused, "configured library hold"):
                self.strip()
            with self.assertRaisesRegex(metadata.Refused, "configured library hold"):
                metadata.strip_converted(converted, relative, self.root, self.state)
            with self.assertRaisesRegex(metadata.Refused, "configured library hold"):
                metadata.restore_backup(manifest, self.root, self.state)
        self.assertEqual(snapshot(self.tmp.name), before)

    def test_malformed_hold_config_fails_before_any_cleanup_or_write(self):
        partial = os.path.join(os.path.dirname(self.path), ".Mockingjay.epub.partial")
        write(partial, b"must survive invalid config")
        before = snapshot(self.tmp.name)
        for malformed in ('not-json', '{}', '["../outside"]', '["/absolute"]',
                          '["Daniel Silva//Ransom"]', '["Daniel Silva/./Ransom"]',
                          '["Daniel Silva/Ransom/"]', '[".hidden"]',
                          '["Daniel Silva/Ransom", "Daniel Silva/Ransom"]', '[1]'):
            with self.subTest(config=malformed):
                process, lines = self.run_script(LIBRARY_HOLD_FOLDERS_JSON=malformed,
                                                STRIP_SERIES_METADATA="0", STRIP_ONLY="0")
                self.assertEqual(process.returncode, 1, process.stdout)
                self.assertTrue(any(line["msg"] == "epub_convert_run_failed" for line in lines))
                self.assertEqual(snapshot(self.tmp.name), before)

    def test_hold_config_rejects_symlink_ancestors_and_accepts_missing_future_folder(self):
        alias = os.path.join(self.root, "Alias")
        os.symlink(os.path.dirname(self.path), alias)
        with mock.patch.dict(os.environ, LIBRARY_HOLD_FOLDERS_JSON='["Alias/Future"]'):
            with self.assertRaises(OSError):
                metadata.library_hold_folders(self.root)
        with mock.patch.dict(os.environ, LIBRARY_HOLD_FOLDERS_JSON='["Daniel Silva/Ransom"]'):
            self.assertEqual(metadata.library_hold_folders(self.root), frozenset(["Daniel Silva/Ransom"]))

    def test_approved_original_checksum_is_checked_before_backup_or_replacement(self):
        before = snapshot(self.tmp.name)
        with self.assertRaisesRegex(metadata.Changed, "source changed since grouping preflight"):
            self.strip(expected_sha256="0" * 64)
        self.assertEqual(snapshot(self.tmp.name), before)
        self.assertFalse(os.path.exists(self.state))


if __name__ == "__main__":
    unittest.main(verbosity=2)
