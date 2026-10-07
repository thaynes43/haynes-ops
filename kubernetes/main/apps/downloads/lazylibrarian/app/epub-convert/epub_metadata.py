"""Lossless OPF series removal and verified, reversible EPUB replacement.

The only XML edit is deletion of approved meta element byte spans. ZipFile
preserves every member's uncompressed bytes, ordering and metadata; compression
streams and central directory offsets may change. Refuse anything ambiguous.
"""

import contextlib
import copy
import datetime
import hashlib
import io
import json
import os
import secrets
import stat
import time
import unicodedata
import zipfile
import zlib
from pathlib import PurePosixPath
from xml.parsers import expat

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


def xml_nodes(raw):
    """Parse safely and retain UTF-8 byte spans without serializing XML."""
    text, codec, prefix = _xml_text(raw)
    utf8 = text.encode("utf-8")
    nodes, stack = [], []
    parser = expat.ParserCreate(namespace_separator="}")

    def forbidden(*_args):
        raise Refused("DTD and XML entities are not supported")

    parser.StartDoctypeDeclHandler = forbidden
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


def strip_opf(raw):
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
    inventory = [{"attributes": node["attrs"], "text": node["text"]} for node in removed]
    for node in sorted(removed, key=lambda node: node["start"], reverse=True):
        utf8 = utf8[:node["start"]] + utf8[node["end"]:]
    return prefix + utf8.decode("utf-8").encode(codec), inventory


def _member_name(name):
    parts = PurePosixPath(name).parts
    if not name or "\\" in name or "\0" in name or name.startswith("/") or ".." in parts:
        raise Refused(f"unsafe ZIP member {name!r}")


def inspect_epub(raw):
    """Validate all CRCs, the EPUB container and every declared package."""
    if len(raw) > MAX_ARCHIVE:
        raise Refused("EPUB exceeds the 256 MiB safety limit")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > 10000 or len({i.filename for i in infos}) != len(infos):
                raise Refused("empty, too many or duplicate ZIP members")
            first = infos[0]
            if first.filename != "mimetype" or first.header_offset != 0 or first.compress_type != zipfile.ZIP_STORED:
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
                changed, tags = strip_opf(members[name])
                if tags:
                    updates[name] = changed
                    inventory += [{"opf": name, **tag} for tag in tags]
            return infos, members, archive.comment, updates, inventory
    except (zipfile.BadZipFile, RuntimeError, KeyError, UnicodeError, OSError, zlib.error) as err:
        raise Refused(f"invalid EPUB: {err}") from err


def sanitized_epub(raw):
    infos, members, comment, updates, inventory = inspect_epub(raw)
    if not updates:
        return raw, inventory
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=False) as archive:
        archive.comment = comment
        for info in infos:
            archive.writestr(copy.copy(info), updates.get(info.filename, members[info.filename]))
    candidate = output.getvalue()
    after_infos, after_members, after_comment, leftover, _inventory = inspect_epub(candidate)
    if leftover or after_comment != comment or len(after_infos) != len(infos):
        raise Refused("candidate validation failed")
    for before, after in zip(infos, after_infos):
        if any(getattr(before, key) != getattr(after, key) for key in ZIP_METADATA):
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


def grouping_identity(raw, relative):
    """Read aliases without imposing rewrite-only EPUB/OPF requirements.

    BookService's last calibre series / EPUB3 collection and last index win,
    and both must be present. Only guaranteed active aliases may be subtracted
    from the newly exposed aliases; uncertain/multiple titles are not subtracted.
    EPUB3 main-title file-as supplies SeriesSort; calibre:title_sort does not.
    """
    if len(raw) > MAX_ARCHIVE:
        raise Refused("EPUB exceeds the identity preflight size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > 10000 or len({i.filename for i in infos}) != len(infos):
                raise Refused("duplicate or too many ZIP entries in identity preflight")
            if sum(i.file_size for i in infos) > MAX_EXPANDED:
                raise Refused("expanded EPUB exceeds identity preflight limit")
            bad = archive.testzip()
            if bad:
                raise Refused(f"invalid ZIP CRC in {bad}")
            if archive.getinfo("META-INF/container.xml").file_size > MAX_XML:
                raise Refused("container exceeds identity preflight XML limit")
            container, *_unused = xml_nodes(archive.read("META-INF/container.xml"))
            packages = [node["attrs"].get("full-path") for node in container
                        if node["name"] == CONTAINER + "rootfile"
                        and node["attrs"].get("media-type") == "application/oebps-package+xml"]
            if not packages:
                raise Refused("identity preflight found no OPF package")
            titles, authors, series, sorts, inventory, active_packages = [], [], [], [], [], []
            for name in packages:
                _member_name(name)
                if archive.getinfo(name).file_size > MAX_XML:
                    raise Refused("OPF exceeds identity preflight limit")
                nodes, *_unused = xml_nodes(archive.read(name))
                if not nodes or nodes[0]["name"] != OPF + "package":
                    raise Refused("identity preflight OPF is not a package")
                meta = [n for n in nodes if n["name"] == OPF + "meta"
                        and n["parent"] and n["parent"]["name"] == OPF + "metadata"]
                title_nodes = [n for n in nodes if n["name"] == DC + "title"
                               and n["parent"] and n["parent"]["name"] == OPF + "metadata"]
                titles += [n["text"].strip() for n in title_nodes if n["text"].strip()]
                authors += [n["text"].strip() for n in nodes if n["name"] == DC + "creator"]
                effective_series = effective_index = ""
                for n in meta:
                    attrs = n["attrs"]
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
                else:
                    possible_titles = {kavita_normalized(n["text"]) for n in title_nodes} - {""}
                    active = possible_titles if len(possible_titles) == 1 else set()
                active_packages.append(active)
            projected = {kavita_normalized(value) for value in titles + sorts} - {""}
            if not titles or not projected:
                raise Refused("identity preflight cannot read a nonempty dc:title")
            # The author directory is an additional alias, including books with missing creators.
            parts = relative.split("/")
            if len(parts) < 3:
                raise Refused("identity preflight requires author/book/file placement")
            author_aliases = {author_normalized(value) for value in authors + [parts[0]]} - {""}
            if not author_aliases:
                raise Refused("identity preflight cannot read an author identity")
            current = set.intersection(*active_packages)
            comparison = {kavita_normalized(value) for value in series + sorts} - {""}
            return {"path": relative, "authors": author_aliases, "projected_aliases": projected,
                    "current_aliases": current, "comparison_aliases": comparison, "metadata": inventory,
                    "original_sha256": sha256(raw)}
    except (zipfile.BadZipFile, RuntimeError, KeyError, UnicodeError, OSError, zlib.error) as err:
        raise Refused(f"identity preflight cannot read EPUB: {err}") from err


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
                    raw, _info = read_regular(directory, name)
                identities.append(grouping_identity(raw, relative))
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


def _same_directory(descriptor, path):
    with safe_directory(path) as current:
        a, b = os.fstat(descriptor), os.fstat(current)
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
            raise Refused("directory changed during the operation")


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
            raise Refused("input changed while being read")
        return raw, before


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


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
            raise Refused("candidate changed after write")
        current, current_info = read_regular(descriptor, name)
        if _identity(current_info) != _identity(original_info) or current != original:
            raise Refused("original changed before replacement")
        _same_directory(descriptor, folder)
        os.replace(partial, name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        try:
            os.unlink(partial, dir_fd=descriptor)
        except FileNotFoundError:
            pass


def strip_existing(path, root, state, settle_seconds, dry_run=False, expected_sha256=None):
    relative = os.path.relpath(path, root)
    if relative == ".." or relative.startswith("../"):
        raise Refused("EPUB is outside EBOOK_ROOT")
    validate_paths(root, state)
    folder, name = os.path.split(path)
    with safe_directory(folder) as directory:
        raw, info = read_regular(directory, name)
        if expected_sha256 and sha256(raw) != expected_sha256:
            raise Refused("source changed since grouping preflight")
        if time.time() - max(info.st_mtime, info.st_ctime) < settle_seconds:
            return {"result": "settling", "path": relative}
        candidate, inventory = sanitized_epub(raw)
        result = {"result": "untagged", "path": relative}
        if not inventory:
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
    validate_paths(root, state)
    with safe_directory(os.path.dirname(path)) as directory:
        raw, info = read_regular(directory, os.path.basename(path))
        candidate, inventory = sanitized_epub(raw)
        identity = grouping_identity(candidate, relative)
        conflicts = collision_conflicts(identity, identities or [], new_file=True)
        if conflicts:
            raise Refused(f"converted EPUB would create a cross-author grouping collision: {conflicts}")
        result = {"metadata": inventory}
        if inventory:
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
        original, _info = read_regular(backups, manifest["backup_file"])
        if sha256(original) != manifest["original_sha256"]:
            raise Refused("backup checksum does not match its manifest")
        inspect_epub(original)
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
