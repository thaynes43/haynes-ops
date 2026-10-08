"""Lossless OPF grouping edits and verified, reversible EPUB replacement.

XML edits delete approved meta byte spans and insert owner-ruled grouping tags. ZipFile
preserves every member's uncompressed bytes, ordering and metadata; compression
streams and central directory offsets may change. Refuse anything ambiguous.
"""

import contextlib
import copy
import datetime
import errno
import hashlib
import io
import json
import os
import re
import secrets
import stat
import time
import unicodedata
import zipfile
import zlib
from pathlib import PurePosixPath
from xml.parsers import expat
from xml.sax.saxutils import quoteattr

OPF = "http://www.idpf.org/2007/opf}"
DC = "http://purl.org/dc/elements/1.1/}"
CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container}"
MAX_ARCHIVE = 256 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_XML = 4 * 1024 * 1024
ZIP_METADATA = ("filename", "date_time", "compress_type", "comment", "extra", "create_system",
                "create_version", "extract_version", "internal_attr", "external_attr", "volume")


class Refused(ValueError):
    """The input cannot be changed while honoring the preservation contract."""


class Changed(Refused):
    """An input/directory race may clear on the next settled run; do not hold it."""


class GroupingHold(Refused):
    """A readable identity needs review before the owner-ruled tag can be derived."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _xml_text(raw):
    if len(raw) > MAX_XML:
        raise Refused("XML exceeds the 4 MiB safety limit")
    if raw.startswith(b"\xff\xfe"):
        codec, prefix = "utf-16-le", b"\xff\xfe"
    elif raw.startswith(b"\xfe\xff"):
        codec, prefix = "utf-16-be", b"\xfe\xff"
    elif raw.startswith(b"\xef\xbb\xbf"):
        codec, prefix = "utf-8", b"\xef\xbb\xbf"
    else:
        codec, prefix = "utf-8", b""
    try:
        text = raw[len(prefix):].decode(codec)
    except UnicodeError as err:
        raise Refused("XML must be UTF-8 or BOM-marked UTF-16") from err
    if prefix + text.encode(codec) != raw:
        raise Refused("XML encoding does not round trip exactly")
    return text, codec, prefix


def xml_nodes(raw, allow_harmless_dtd=False):
    """Parse safely and retain UTF-8 byte spans without serializing XML."""
    text, codec, prefix = _xml_text(raw)
    utf8 = text.encode("utf-8")
    nodes, stack = [], []
    parser = expat.ParserCreate(namespace_separator="}")

    def forbidden(*_args):
        raise Refused("DTD and XML entities are not supported")

    # Identity reads match Kavita's DtdProcessing.Ignore. Expat never loads a
    # declared external DTD here, and entity definitions/references still refuse.
    parser.StartDoctypeDeclHandler = (lambda *_args: None) if allow_harmless_dtd else forbidden
    parser.EntityDeclHandler = forbidden
    parser.ExternalEntityRefHandler = forbidden

    def tag_end(start):
        quote = None
        for i in range(start, len(utf8)):
            char = utf8[i]
            if quote:
                if char == quote:
                    quote = None
            elif char in (34, 39):
                quote = char
            elif char == 62:
                return i + 1
        raise Refused("unfinished XML tag")

    def start(name, attrs):
        begin = parser.CurrentByteIndex
        end = tag_end(begin)
        node = {"name": name, "attrs": attrs, "start": begin, "open_end": end,
                "empty": utf8[begin:end].rstrip().endswith(b"/>"),
                "parent": stack[-1] if stack else None, "text": "", "children": 0}
        if stack:
            stack[-1]["children"] += 1
        nodes.append(node)
        stack.append(node)

    def end(_name):
        node = stack.pop()
        node["end"] = node["open_end"] if node["empty"] else tag_end(parser.CurrentByteIndex)

    def chars(value):
        if stack:
            stack[-1]["text"] += value

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    try:
        parser.Parse(text, True)
    except expat.ExpatError as err:
        raise Refused(f"invalid XML: {err}") from err
    return nodes, utf8, codec, prefix


def strip_opf(raw, grouping=None):
    nodes, utf8, codec, prefix = xml_nodes(raw)
    if not nodes or nodes[0]["name"] != OPF + "package":
        raise Refused("OPF root is not an EPUB package")
    ids = {}
    for node in nodes:
        identifier = node["attrs"].get("id") or node["attrs"].get("http://www.w3.org/XML/1998/namespace}id")
        if identifier:
            if identifier in ids:
                raise Refused(f"duplicate OPF id {identifier!r}")
            ids[identifier] = node
        refines = node["attrs"].get("refines", "")
        node["refined_id"] = refines[1:] if refines.startswith("#") else None
    for node in nodes:
        if node["refined_id"] is not None and node["refined_id"] not in ids:
            raise Refused(f"dangling OPF refinement {node['attrs']['refines']!r}")

    meta = [node for node in nodes if node["name"] == OPF + "meta"
            and node["parent"] and node["parent"]["name"] == OPF + "metadata"]
    removed = [node for node in meta
               if node["attrs"].get("name") in ("calibre:series", "calibre:series_index")
               or node["attrs"].get("property") == "belongs-to-collection"]
    collections = {node["attrs"]["id"] for node in removed
                   if node["attrs"].get("property") == "belongs-to-collection" and node["attrs"].get("id")}
    removed += [node for node in meta if node not in removed and node["refined_id"] in collections
                and node["attrs"].get("property") in ("collection-type", "group-position")]
    removed_ids = {identifier for identifier, node in ids.items() if node in removed}
    for node in nodes:
        if node not in removed and node["refined_id"] in removed_ids:
            raise Refused(f"unfamiliar refinement of removed metadata: {node['attrs']!r}")
    if any(node["children"] for node in removed):
        raise Refused("series meta contains child elements")
    if grouping is not None:
        if not isinstance(grouping, str) or not grouping.strip() or any(ord(c) < 32 for c in grouping):
            raise Refused("invalid dedicated grouping tag")
        desired = {"calibre:series": grouping, "calibre:series_index": "1"}
        if (len(removed) == 2 and {node["attrs"].get("name"): node["attrs"].get("content")
                                  for node in removed} == desired
                and all(set(node["attrs"]) == {"name", "content"} for node in removed)):
            return raw, []
    inventory = [{"attributes": node["attrs"], "text": node["text"]} for node in removed]
    edits = [(node["start"], node["end"], b"") for node in removed]
    if grouping is not None:
        containers = [node for node in nodes if node["name"] == OPF + "metadata"
                      and node["parent"] is nodes[0]]
        if len(containers) != 1 or containers[0]["empty"]:
            raise Refused("dedicated grouping requires one nonempty OPF metadata element")
        container = containers[0]
        closing = utf8.rfind(b"</", container["open_end"], container["end"])
        if closing < 0:
            raise Refused("OPF metadata closing tag not found")
        inserted = ('\n<meta xmlns="http://www.idpf.org/2007/opf" name="calibre:series" content='
                    + quoteattr(grouping) + '/>\n<meta xmlns="http://www.idpf.org/2007/opf" '
                    'name="calibre:series_index" content="1"/>\n').encode("utf-8")
        edits.append((closing, closing, inserted))
    for start, end, replacement in sorted(edits, reverse=True):
        utf8 = utf8[:start] + replacement + utf8[end:]
    return prefix + utf8.decode("utf-8").encode(codec), inventory


def _member_name(name):
    if not isinstance(name, str):
        raise Refused("ZIP member name must be a string")
    parts = PurePosixPath(name).parts
    if not name or "\\" in name or "\0" in name or name.startswith("/") or ".." in parts:
        raise Refused(f"unsafe ZIP member {name!r}")


def inspect_epub(raw, require_canonical=True, grouping=None):
    """Validate all CRCs, the EPUB container and every declared package."""
    if len(raw) > MAX_ARCHIVE:
        raise Refused("EPUB exceeds the 256 MiB safety limit")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > 10000 or len({i.filename for i in infos}) != len(infos):
                raise Refused("empty, too many or duplicate ZIP members")
            first = infos[0]
            if require_canonical and (first.filename != "mimetype" or first.header_offset != 0
                                      or first.compress_type != zipfile.ZIP_STORED):
                raise Refused("mimetype must be the first, stored ZIP member")
            if sum(info.file_size for info in infos) > MAX_EXPANDED:
                raise Refused("expanded EPUB exceeds the 512 MiB safety limit")
            members = {}
            for info in infos:
                _member_name(info.filename)
                if info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise Refused("encrypted ZIP or unsupported compression")
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise Refused("ZIP contains a symlink")
                # ZIP64 extra fields would hold stale size/offset records after a rewrite.
                extra = info.extra
                while extra:
                    if len(extra) < 4:
                        raise Refused("malformed ZIP extra field")
                    field = int.from_bytes(extra[:2], "little")
                    length = int.from_bytes(extra[2:4], "little")
                    if length + 4 > len(extra) or field == 1:
                        raise Refused("malformed extra field or unsupported ZIP64")
                    extra = extra[4 + length:]
                members[info.filename] = archive.read(info)  # reads and verifies its CRC
            if members["mimetype"] != b"application/epub+zip":
                raise Refused("invalid EPUB mimetype")
            if "META-INF/signatures.xml" in members:
                raise Refused("signed EPUB cannot be edited without invalidating its signature")
            container, *_unused = xml_nodes(members.get("META-INF/container.xml", b""))
            if not container or container[0]["name"] != CONTAINER + "container":
                raise Refused("invalid EPUB container")
            packages = [node["attrs"].get("full-path") for node in container
                        if node["name"] == CONTAINER + "rootfile"
                        and node["attrs"].get("media-type") == "application/oebps-package+xml"]
            if not packages or len(set(packages)) != len(packages):
                raise Refused("container has no unique package document")
            updates, inventory = {}, []
            for name in packages:
                _member_name(name)
                if name not in members:
                    raise Refused(f"missing OPF {name!r}")
                changed, tags = strip_opf(members[name], grouping)
                if changed != members[name]:
                    updates[name] = changed
                    inventory += [{"opf": name, **tag} for tag in tags]
            return infos, members, archive.comment, updates, inventory
    except (zipfile.BadZipFile, RuntimeError, KeyError, UnicodeError, OSError, zlib.error) as err:
        raise Refused(f"invalid EPUB: {err}") from err


def sanitized_epub(raw, grouping=None):
    infos, members, comment, updates, inventory = inspect_epub(raw, require_canonical=False, grouping=grouping)
    if not updates:
        return raw, inventory
    output = io.BytesIO()
    ordered = sorted(infos, key=lambda info: info.filename != "mimetype")
    with zipfile.ZipFile(output, "w", allowZip64=False) as archive:
        archive.comment = comment
        for info in ordered:
            copied = copy.copy(info)
            if copied.filename == "mimetype":
                copied.compress_type = zipfile.ZIP_STORED
            archive.writestr(copied, updates.get(info.filename, members[info.filename]))
            # ZipFile fills a zero external_attr with default permissions. It
            # is only in the central directory, emitted on close, so restore
            # the original public ZipInfo field before that record is written.
            copied.external_attr = info.external_attr
    candidate = output.getvalue()
    after_infos, after_members, after_comment, leftover, _inventory = inspect_epub(candidate, grouping=grouping)
    if leftover or after_comment != comment or len(after_infos) != len(infos):
        raise Refused("candidate validation failed")
    for before, after in zip(ordered, after_infos):
        expected = copy.copy(before)
        if expected.filename == "mimetype":
            expected.compress_type = zipfile.ZIP_STORED
        if any(getattr(expected, key) != getattr(after, key) for key in ZIP_METADATA):
            raise Refused(f"ZIP member metadata changed: {before.filename}")
        if after_members[before.filename] != updates.get(before.filename, members[before.filename]):
            raise Refused(f"ZIP member bytes changed unexpectedly: {before.filename}")
    return candidate, inventory


def kavita_normalized(value):
    """Kavita v0.9.0.2 ToNormalized: Unicode letters, ASCII digits, +!＊！＋."""
    return "".join(char for char in value if unicodedata.category(char).startswith("L")
                   or char in "0123456789+!＊！＋").lower()


def author_normalized(value):
    words, current = [], ""
    for char in value.lower():
        if unicodedata.category(char).startswith("L") or char in "0123456789":
            current += char
        elif current:
            words.append(current)
            current = ""
    if current:
        words.append(current)
    return " ".join(sorted(words))


class _MetadataReader:
    """Bound ZipFile's central-directory reads without parsing the ZIP format.

    Only container/OPF payloads are read. The archive itself can be large, and
    unrelated member CRCs/contents are checked only when a file is rewritten.
    """

    def __init__(self, source, size, deadline):
        self.source, self.size, self.deadline = source, size, deadline

    def read(self, size=-1):
        if time.monotonic() >= self.deadline:
            raise Refused("run budget exhausted during identity preflight")
        if size < 0:
            size = max(0, self.size - self.source.tell())
        if size > 32 * 1024 * 1024:
            raise Refused("ZIP metadata read exceeds the 32 MiB safety limit")
        return self.source.read(size)

    def seek(self, *args):
        return self.source.seek(*args)

    def tell(self):
        return self.source.tell()

    def seekable(self):
        return True


def _identity_xml(archive, name):
    _member_name(name)
    if archive.getinfo(name).file_size > MAX_XML:
        raise Refused(f"identity XML exceeds the 4 MiB safety limit: {name}")
    with archive.open(name) as member:
        raw = member.read(MAX_XML + 1)  # CRC checked for the metadata actually read.
    return xml_nodes(raw, allow_harmless_dtd=True)[0]


def _qualified_authors(nodes, meta):
    """Recognize author roles without treating translators/editors as authors."""
    creators = [node for node in nodes if node["name"] == DC + "creator"
                and node["parent"] and node["parent"]["name"] == OPF + "metadata"]
    credits = []
    if any(node["attrs"].get("property") == "role" and not node["attrs"].get("refines") for node in meta):
        return [], "unassigned creator role metadata"
    for creator in creators:
        roles = []
        explicit = creator["attrs"].get(OPF + "role") or creator["attrs"].get("role")
        if explicit:
            roles.append(explicit.strip().lower())
        identifier = creator["attrs"].get("id") or creator["attrs"].get("http://www.w3.org/XML/1998/namespace}id")
        if identifier:
            roles += [node["text"].strip().lower() for node in meta
                      if node["attrs"].get("property") == "role"
                      and node["attrs"].get("refines") == "#" + identifier]
        if roles and any(role not in ("aut", "author") for role in roles):
            continue
        text = creator["text"].strip()
        if not text:
            return [], "empty author credit"
        # A single surname/forename comma is retained as written. Multi-credit
        # lists and uncertain boundaries must not become one invented author.
        if text.count(",") > 1 or re.search(r"[;&/]|\band\b", text, flags=re.IGNORECASE):
            return [], "ambiguous combined author credit"
        credits.append(text)
    if not credits:
        return [], "no unambiguous author-role creator"
    if len({author_normalized(value) for value in credits}) != 1:
        return [], "multiple author-role creators"
    return credits, None


def _grouping_from_archive(archive, relative):
    """Read aliases without imposing rewrite-only EPUB/OPF requirements.

    BookService's last calibre series / EPUB3 collection and last index win,
    and both must be present. Only guaranteed active aliases may be subtracted
    from the newly exposed aliases; uncertain/multiple titles are not subtracted.
    EPUB3 main-title file-as supplies SeriesSort; calibre:title_sort does not.
    """
    try:
        if archive:
            infos = archive.infolist()
            if len(infos) > 10000 or len({i.filename for i in infos}) != len(infos):
                raise Refused("duplicate or too many ZIP entries in identity preflight")
            container = _identity_xml(archive, "META-INF/container.xml")
            if not container or container[0]["name"] != CONTAINER + "container":
                raise Refused("identity preflight has an invalid EPUB container")
            packages = [node["attrs"].get("full-path") for node in container
                        if node["name"] == CONTAINER + "rootfile"
                        and node["attrs"].get("media-type") == "application/oebps-package+xml"]
            if not packages or len(set(packages)) != len(packages):
                raise Refused("identity preflight found no unique OPF package")
            titles, authors, qualified_authors, author_refusals = [], [], [], []
            series, sorts, inventory, active_packages = [], [], [], []
            removable = indexed_without_title = False
            for name in packages:
                nodes = _identity_xml(archive, name)
                if not nodes or nodes[0]["name"] != OPF + "package":
                    raise Refused("identity preflight OPF is not a package")
                meta = [n for n in nodes if n["name"] == OPF + "meta"
                        and n["parent"] and n["parent"]["name"] == OPF + "metadata"]
                title_nodes = [n for n in nodes if n["name"] == DC + "title"
                               and n["parent"] and n["parent"]["name"] == OPF + "metadata"]
                titles += [n["text"].strip() for n in title_nodes if n["text"].strip()]
                authors += [n["text"].strip() for n in nodes if n["name"] == DC + "creator"]
                qualified, refusal = _qualified_authors(nodes, meta)
                qualified_authors += qualified
                if refusal:
                    author_refusals.append(refusal)
                effective_series = effective_index = ""
                for n in meta:
                    attrs = n["attrs"]
                    if attrs.get("name") in ("calibre:series", "calibre:series_index") or attrs.get("property") == "belongs-to-collection":
                        removable = True
                    if attrs.get("name") in ("calibre:series", "calibre:series_index") or attrs.get("property") in (
                            "belongs-to-collection", "collection-type", "group-position"):
                        inventory.append({"opf": name, "attributes": attrs, "text": n["text"]})
                    if attrs.get("name") == "calibre:series":
                        effective_series = attrs.get("content", "")
                        series.append(effective_series.strip())
                    if attrs.get("name") == "calibre:series_index":
                        effective_index = attrs.get("content", "")
                    if attrs.get("property") == "belongs-to-collection":
                        effective_series = n["text"]
                        series.append(effective_series.strip())
                    if attrs.get("property") == "group-position":
                        effective_index = n["text"]
                    if attrs.get("property") == "title-type" and n["text"].strip() == "main":
                        refines = attrs.get("refines")
                        if refines and any("#" + t["attrs"].get("id", "") == refines for t in title_nodes):
                            sorts += [item["text"].strip() for item in meta
                                      if item["attrs"].get("property") == "file-as"
                                      and item["attrs"].get("refines") == refines and item["text"].strip()]
                if effective_series and effective_index:
                    active = {kavita_normalized(effective_series.strip())} - {""}
                    if not active:
                        raise Refused("active EPUB series normalizes to an empty name")
                    if not title_nodes or not title_nodes[0]["text"].strip():
                        indexed_without_title = True
                else:
                    possible_titles = {kavita_normalized(n["text"]) for n in title_nodes} - {""}
                    active = possible_titles if len(possible_titles) == 1 else set()
                active_packages.append(active)
            projected = {kavita_normalized(value) for value in titles + sorts} - {""}
            # Kavita rejects an empty first title after series removal. An
            # already untagged empty package is known-unindexed, with no aliases.
            if not titles:
                projected = set()
            # Only normal author/book/file placement supplies a folder alias.
            parts = relative.split("/")
            author_values = authors + ([parts[0]] if len(parts) >= 3 else [])
            author_aliases = {author_normalized(value) for value in author_values} - {""}
            current = set.intersection(*active_packages)
            comparison = {kavita_normalized(value) for value in series + sorts} - {""}
            result = {"path": relative, "authors": author_aliases, "projected_aliases": projected,
                    "current_aliases": current, "comparison_aliases": comparison, "metadata": inventory,
                    "has_series_metadata": removable,
                    "title_keys": {kavita_normalized(value) for value in titles} - {""},
                    "creator_keys": {author_normalized(value) for value in authors} - {""},
                    "author_keys": {author_normalized(value) for value in qualified_authors} - {""},
                    "author": qualified_authors[0] if qualified_authors else None,
                    "author_refusal": "; ".join(sorted(set(author_refusals))) if author_refusals else None,
                    "title": titles[0] if titles else None,
                    "creator": next((value for value in authors if value), None)}
            if indexed_without_title:
                result["strip_refusal"] = "stripping active series would leave no first dc:title for Kavita indexing"
            return result
    except (zipfile.BadZipFile, RuntimeError, KeyError, UnicodeError, OSError, zlib.error) as err:
        raise Refused(f"identity preflight cannot read EPUB: {err}") from err


def grouping_identity(raw, relative):
    """Read a bounded candidate already held in memory by rewrite validation."""
    if len(raw) > MAX_ARCHIVE:
        raise Refused("EPUB candidate exceeds the 256 MiB safety limit")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            result = _grouping_from_archive(archive, relative)
            result["original_sha256"] = sha256(raw)
            return result
    except (zipfile.BadZipFile, RuntimeError, OSError) as err:
        raise Refused(f"identity preflight cannot read EPUB: {err}") from err


def grouping_identity_file(directory, name, relative, deadline=float("inf")):
    """Read only bounded ZIP metadata through a safe, stable source descriptor."""
    if os.path.basename(name) != name or name in ("", ".", ".."):
        raise Refused("invalid EPUB identity basename")
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise Refused("EPUB identity source must be a regular file")
        try:
            with zipfile.ZipFile(_MetadataReader(source, before.st_size, deadline)) as archive:
                result = _grouping_from_archive(archive, relative)
        except (zipfile.BadZipFile, RuntimeError, OSError) as err:
            raise Refused(f"identity preflight cannot read EPUB: {err}") from err
        if _identity(before) != _identity(os.fstat(source.fileno())):
            raise Changed("EPUB changed during identity preflight")
        result["source_identity"] = _identity(before)
        return result


def collision_conflicts(identity, identities, new_file=False):
    """Hold only newly exposed cross-author aliases, leaving old groups alone."""
    exposed = identity["projected_aliases"] if new_file else (
        identity["projected_aliases"] - identity["current_aliases"])
    conflicts = []
    for other in identities:
        if other["path"] == identity["path"] or identity["authors"] & other["authors"]:
            continue
        common = exposed & (other["current_aliases"] | other["projected_aliases"] | other.get("comparison_aliases", set()))
        if common:
            conflicts.append({"path": other["path"], "aliases": sorted(common)})
    return conflicts


def dedicated_grouping(identity, identities):
    """Same-title/different-creator policy includes existing mixed-author groups."""
    collisions = [other for other in identities if other["path"] != identity["path"]
                  and identity.get("title_keys", set()) & other.get("title_keys", set())
                  and (identity.get("author_refusal") or other.get("author_refusal")
                       or identity.get("author_keys", set()) != other.get("author_keys", set()))]
    if not collisions:
        return None
    if (identity.get("author_refusal") or len(identity["title_keys"]) != 1 or len(identity["author_keys"]) != 1
            or not identity["title"] or not identity["author"]
            or any(other.get("author_refusal") or len(other.get("author_keys", set())) != 1
                   or len(other.get("title_keys", set())) != 1 for other in collisions)):
        raise GroupingHold("same-title cross-author grouping has ambiguous title/creator metadata")
    return f"{identity['title']} ({identity['author']})"


def identity_preflight(root, deadline):
    """One bounded read-only census; an unknown identity refuses the whole pass."""
    identities, errors = [], []
    def walk_error(err):
        errors.append({"path": os.path.relpath(err.filename or root, root),
                       "detail": f"cannot traverse grouping preflight directory: {err}"[:500]})

    for folder, dirs, files in os.walk(root, onerror=walk_error):
        for name in dirs:
            if not name.startswith(".") and os.path.islink(os.path.join(folder, name)):
                errors.append({"path": os.path.relpath(os.path.join(folder, name), root),
                               "detail": "symlinked directory prevents complete grouping preflight"})
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(folder, d)))
        for name in sorted(files):
            if name.startswith(".") or os.path.splitext(name)[1].lower() != ".epub":
                continue
            relative = os.path.relpath(os.path.join(folder, name), root)
            if time.monotonic() >= deadline:
                errors.append({"path": relative, "detail": "run budget exhausted during identity preflight"})
                return identities, errors
            try:
                with safe_directory(folder) as directory:
                    identities.append(grouping_identity_file(directory, name, relative, deadline))
            except Exception as err:
                errors.append({"path": relative, "detail": f"{type(err).__name__}: {err}"[:500]})
    return identities, errors


@contextlib.contextmanager
def safe_directory(path, create=False):
    """Open every absolute path component with O_NOFOLLOW, never resolve links."""
    if not os.path.isabs(path) or ".." in path.split("/"):
        raise Refused("directory must be absolute and contain no parent traversal")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.split("/"):
            if not part or part == ".":
                continue
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def validate_paths(root, state):
    root, state = os.path.abspath(root), os.path.abspath(state)
    if root == "/" or os.path.commonpath((root, state)) in (root, state):
        raise Refused("STATE_DIR must be outside and separate from EBOOK_ROOT")
    with safe_directory(root):
        pass
    # STATE_DIR may not exist yet. Its existing ancestors still must be safe.
    parent = state
    while not os.path.lexists(parent):
        parent = os.path.dirname(parent)
    with safe_directory(parent):
        pass


def library_hold_folders(root):
    """Strict normalized relative folder holds, including their descendants."""
    try:
        entries = json.loads(os.environ.get("LIBRARY_HOLD_FOLDERS_JSON", "[]"))
    except (ValueError, TypeError) as err:
        raise Refused("LIBRARY_HOLD_FOLDERS_JSON must be a JSON array of relative folders") from err
    if not isinstance(entries, list):
        raise Refused("LIBRARY_HOLD_FOLDERS_JSON must be a JSON array of relative folders")
    holds = set()
    for entry in entries:
        if (not isinstance(entry, str) or not entry or os.path.isabs(entry) or "\\" in entry
                or any(ord(c) < 32 for c in entry)
                or any(part in ("", ".", "..") or part.startswith(".") for part in entry.split("/"))
                or str(PurePosixPath(entry)) != entry or entry in holds):
            raise Refused("library hold folders must be unique normalized visible relative paths")
        target = os.path.join(os.path.abspath(root), entry)
        if os.path.commonpath((os.path.abspath(root), target)) != os.path.abspath(root):
            raise Refused("library hold folder is outside EBOOK_ROOT")
        # Missing folders are valid future holds; existing ancestors must not be links.
        existing = target
        while not os.path.lexists(existing):
            existing = os.path.dirname(existing)
        with safe_directory(existing):
            pass
        holds.add(entry)
    return frozenset(holds)


def held_library_folder(folder, root, holds=None):
    relative = os.path.relpath(folder, root)
    configured = library_hold_folders(root) if holds is None else holds
    return next((entry for entry in sorted(configured)
                 if relative == entry or relative.startswith(entry + os.sep)), None)


def require_unheld_library_file(path, root):
    folder = os.path.dirname(path)
    if os.path.commonpath((os.path.abspath(root), os.path.abspath(folder))) != os.path.abspath(root):
        raise Refused("mutation is outside EBOOK_ROOT")
    held = held_library_folder(folder, root)
    if held:
        raise Refused(f"configured library hold preserves {held}")


def _same_directory(descriptor, path):
    with safe_directory(path) as current:
        a, b = os.fstat(descriptor), os.fstat(current)
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
            raise Changed("directory changed during the operation")


def read_regular(descriptor, name, limit=MAX_ARCHIVE):
    if os.path.basename(name) != name or name in ("", ".", ".."):
        raise Refused("invalid file basename")
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise Refused("input must be a bounded regular file with one hardlink")
        raw = source.read(limit + 1)
        after = os.fstat(source.fileno())
        if len(raw) > limit or _identity(before) != _identity(after) or len(raw) != before.st_size:
            raise Changed("input changed while being read")
        return raw, before


def stat_conversion_source(descriptor, name):
    """Metadata-only check for read-only MOBI/AZW3 input, allowing size and hardlinks.

    EPUB replacement limits belong to read_regular. Conversion retains its input;
    opening with O_NOFOLLOW and checking the descriptor refuses symlinks/nonfiles
    without buffering the original or changing hardlink-based imports.
    """
    if os.path.basename(name) != name or name in ("", ".", ".."):
        raise Refused("invalid conversion source basename")
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
    except OSError as err:
        if err.errno == errno.ELOOP:
            raise Refused("conversion source must not be a symlink") from err
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise Refused("conversion source must be a regular file")
        return info
    finally:
        os.close(fd)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def copy_conversion_output(source_directory, source_name, target_directory, target_name):
    """Stream and hash an EPUB candidate, verifying the copy at its known size.

    Plain conversion can publish EPUBs larger than the metadata rewrite limit.
    Both copy and read-back use fixed 1 MiB chunks, so there is no whole-file
    allocation. The metadata pass still validates its own bounded ZIP input.
    """
    for name in (source_name, target_name):
        if os.path.basename(name) != name or name in ("", ".", ".."):
            raise Refused("invalid conversion output basename")
    try:
        source = os.open(source_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_directory)
    except OSError as err:
        if err.errno == errno.ELOOP:
            raise Refused("conversion output must not be a symlink") from err
        raise
    target = None
    try:
        before = os.fstat(source)
        if not stat.S_ISREG(before.st_mode):
            raise Refused("conversion output must be a regular file")
        target = os.open(target_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o644, dir_fd=target_directory)
        checksum, count = hashlib.sha256(), 0
        while True:
            chunk = os.read(source, 1024 * 1024)
            if not chunk:
                break
            count += len(chunk)
            if count > before.st_size:
                raise Changed("conversion output grew during copy")
            checksum.update(chunk)
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(target, remaining)
                if not written:
                    raise OSError("zero-byte write while copying conversion output")
                remaining = remaining[written:]
        if count != before.st_size or _identity(before) != _identity(os.fstat(source)):
            raise Changed("conversion output changed during copy")
        os.fchmod(target, 0o644)
        os.fsync(target)
    finally:
        os.close(source)
        if target is not None:
            os.close(target)
    copied = os.open(target_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=target_directory)
    try:
        before_copy = os.fstat(copied)
        if not stat.S_ISREG(before_copy.st_mode) or before_copy.st_nlink != 1:
            raise Refused("conversion partial must be a regular file with one hardlink")
        if before_copy.st_size != before.st_size:
            raise OSError("conversion partial size differs from the candidate")
        copied_hash, remaining = hashlib.sha256(), before.st_size
        while remaining:
            chunk = os.read(copied, min(1024 * 1024, remaining))
            if not chunk:
                raise OSError("conversion partial ended before its expected size")
            copied_hash.update(chunk)
            remaining -= len(chunk)
        if os.read(copied, 1) or _identity(before_copy) != _identity(os.fstat(copied)):
            raise Changed("conversion partial changed during verification")
        if copied_hash.digest() != checksum.digest():
            raise OSError("conversion partial checksum differs from the candidate")
    finally:
        os.close(copied)
    return before.st_size


def _write_file(descriptor, name, raw, mode=0o600):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=descriptor)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        os.unlink(name, dir_fd=descriptor)
        raise


def backup_original(raw, relative, candidate, info, state, kind):
    """Content-addressed original and path manifest, retained indefinitely."""
    original_hash, candidate_hash = sha256(raw), sha256(candidate)
    stem = sha256(relative.encode("utf-8")) + "-" + original_hash
    name = stem + ".epub"
    manifest_name = stem + ".json"
    manifest = {"schema": 1, "relative_path": relative, "original_sha256": original_hash,
                "sanitized_sha256": candidate_hash, "backup_file": name, "kind": kind,
                "original_mode": stat.S_IMODE(info.st_mode), "original_uid": info.st_uid,
                "original_gid": info.st_gid,
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    folder = os.path.join(state, "backup")
    with safe_directory(folder, create=True) as directory:
        try:
            saved, _saved_info = read_regular(directory, name)
        except FileNotFoundError:
            _write_file(directory, name, raw)
            saved, _saved_info = read_regular(directory, name)
        if saved != raw or sha256(saved) != original_hash:
            raise Refused("existing backup does not match the original")
        try:
            saved_manifest, _saved_info = read_regular(directory, manifest_name, 65536)
        except FileNotFoundError:
            _write_file(directory, manifest_name, (json.dumps(manifest, sort_keys=True) + "\n").encode())
            saved_manifest, _saved_info = read_regular(directory, manifest_name, 65536)
        saved_manifest = json.loads(saved_manifest)
        if any(saved_manifest.get(key) != manifest[key] for key in manifest if key != "created_at"):
            raise Refused("existing backup manifest does not match")
        os.fsync(directory)
        _same_directory(directory, folder)
    return os.path.join(folder, manifest_name)


def _replace(descriptor, folder, name, original, original_info, candidate):
    """Create and sync a sibling, recheck the source, then replace atomically."""
    partial = f".{name}.strip-{secrets.token_hex(8)}.partial"
    try:
        _same_directory(descriptor, folder)
        _write_file(descriptor, partial, candidate)
        with os.fdopen(os.open(partial, os.O_RDWR | os.O_NOFOLLOW, dir_fd=descriptor), "rb") as target:
            written = os.fstat(target.fileno())
            if (written.st_uid, written.st_gid) != (original_info.st_uid, original_info.st_gid):
                os.fchown(target.fileno(), original_info.st_uid, original_info.st_gid)
            os.fchmod(target.fileno(), stat.S_IMODE(original_info.st_mode))
            os.fsync(target.fileno())
        saved, current_info = read_regular(descriptor, partial)
        if saved != candidate:
            raise Changed("candidate changed after write")
        current, current_info = read_regular(descriptor, name)
        if _identity(current_info) != _identity(original_info) or current != original:
            raise Changed("original changed before replacement")
        _same_directory(descriptor, folder)
        os.replace(partial, name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        try:
            os.unlink(partial, dir_fd=descriptor)
        except FileNotFoundError:
            pass


def strip_existing(path, root, state, settle_seconds, dry_run=False, expected_sha256=None,
                   expected_source_identity=None, grouping=None):
    relative = os.path.relpath(path, root)
    if relative == ".." or relative.startswith("../"):
        raise Refused("EPUB is outside EBOOK_ROOT")
    require_unheld_library_file(path, root)
    validate_paths(root, state)
    folder, name = os.path.split(path)
    with safe_directory(folder) as directory:
        raw, info = read_regular(directory, name)
        if expected_source_identity is not None and _identity(info) != expected_source_identity:
            raise Changed("source changed since grouping preflight")
        if expected_sha256 and sha256(raw) != expected_sha256:
            raise Changed("source changed since grouping preflight")
        if time.time() - max(info.st_mtime, info.st_ctime) < settle_seconds:
            return {"result": "settling", "path": relative}
        candidate, inventory = sanitized_epub(raw, grouping)
        result = {"result": "grouped" if grouping is not None else "untagged", "path": relative}
        if grouping is not None:
            result["grouping"] = grouping
        if candidate == raw:
            return result
        result.update(result="would_strip" if dry_run else "stripped", metadata=inventory,
                      original_sha256=sha256(raw), sanitized_sha256=sha256(candidate))
        if not dry_run:
            result["backup_manifest"] = backup_original(raw, relative, candidate, info, state, "existing")
            _replace(directory, folder, name, raw, info, candidate)
            os.utime(directory)
            author = os.path.dirname(folder)
            if os.path.commonpath((os.path.abspath(root), author)) == os.path.abspath(root):
                with safe_directory(author) as author_directory:
                    os.utime(author_directory)
        return result


def strip_converted(path, relative, root, state, dry_run=False, identities=None):
    """Strip a trusted temporary conversion before it is published to the library."""
    require_unheld_library_file(os.path.join(root, relative), root)
    validate_paths(root, state)
    with safe_directory(os.path.dirname(path)) as directory:
        raw, info = read_regular(directory, os.path.basename(path))
        original_identity = grouping_identity(raw, relative)
        grouping = dedicated_grouping(original_identity, identities or [])
        candidate, inventory = sanitized_epub(raw, grouping)
        identity = grouping_identity(candidate, relative)
        if not identity["projected_aliases"]:
            raise Refused("converted EPUB has no title grouping identity after series removal")
        if grouping:
            identity["projected_aliases"] = {kavita_normalized(grouping)}
        conflicts = collision_conflicts(identity, identities or [], new_file=True)
        if conflicts:
            raise Refused(f"converted EPUB would create a cross-author grouping collision: {conflicts}")
        result = {"metadata": inventory}
        if grouping:
            result["grouping"] = grouping
        if candidate != raw:
            if not dry_run:
                result["backup_manifest"] = backup_original(raw, relative, candidate, info, state, "converted")
            # A dry conversion already writes solely into its disposable /tmp workspace.
            _replace(directory, os.path.dirname(path), os.path.basename(path), raw, info, candidate)
        return result


def restore_backup(manifest_path, root, state, dry_run=False):
    validate_paths(root, state)
    backup_dir = os.path.join(state, "backup")
    if os.path.dirname(os.path.abspath(manifest_path)) != os.path.abspath(backup_dir):
        raise Refused("restore manifest must be directly inside STATE_DIR/backup")
    with safe_directory(backup_dir) as backups:
        data, _info = read_regular(backups, os.path.basename(manifest_path), 65536)
        manifest = json.loads(data)
        if manifest.get("schema") != 1:
            raise Refused("unknown backup manifest schema")
        relative = manifest["relative_path"]
        if os.path.isabs(relative) or ".." in relative.split("/") or "\\" in relative:
            raise Refused("unsafe original relative path")
        require_unheld_library_file(os.path.join(root, relative), root)
        original, _info = read_regular(backups, manifest["backup_file"])
        if sha256(original) != manifest["original_sha256"]:
            raise Refused("backup checksum does not match its manifest")
        inspect_epub(original, require_canonical=False)
    path = os.path.join(root, relative)
    folder, name = os.path.split(path)
    with safe_directory(folder) as directory:
        current, info = read_regular(directory, name)
        inspect_epub(current)
        if sha256(current) != manifest["sanitized_sha256"]:
            raise Refused("current EPUB differs from the saved sanitized checksum")
        if not dry_run:
            _replace(directory, folder, name, current, info, original)
            os.utime(directory)
            author = os.path.dirname(folder)
            if os.path.commonpath((os.path.abspath(root), author)) == os.path.abspath(root):
                with safe_directory(author) as author_directory:
                    os.utime(author_directory)
    return {"result": "would_restore" if dry_run else "restored", "path": relative,
            "original_sha256": manifest["original_sha256"]}
