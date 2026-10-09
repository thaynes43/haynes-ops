"""Exact ASCII schema serialization for the frozen .NET 10.0.1 fixture.

Only sqlite_master's schema strings are transported this way. Native full-state
serialization and all scanner/state decisions remain in the actual C# fixture.
Non-ASCII native schemas refuse until a reviewed codec successor exists.
"""
import hashlib
import datetime as dt
from zoneinfo import ZoneInfo


def quote(value):
    if not isinstance(value, str) or any(ord(c) > 127 for c in value):
        raise ValueError("unreviewed non-ASCII schema codec input")
    common = {"\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r", "\\": "\\\\"}
    result = ['"']
    for character in value:
        if character in common:
            result.append(common[character])
        elif ord(character) < 32 or ord(character) == 127 or character in '"&\'+<>`':
            result.append("\\u" + format(ord(character), "04X"))
        else:
            result.append(character)
    return "".join(result) + '"'


def serialized(values):
    return "[" + ",".join(quote(v) for v in values) + "]"


def schema_sha(rows):
    schema = []
    for row in rows:
        if len(row) != 4 or row[0] == "trigger":
            raise ValueError("native schema shape/trigger refused")
        schema.append(serialized(["null:" if cell is None else "text:" + cell for cell in row]))
    return hashlib.sha256(serialized(schema).encode("ascii")).hexdigest()


def file_times(mtime_ns):
    if type(mtime_ns) is not int or not 0 <= mtime_ns < 253402300799000000000:
        raise ValueError("native file timestamp refused")
    seconds, nanoseconds = divmod(mtime_ns, 1_000_000_000)
    utc = dt.datetime.fromtimestamp(seconds, dt.timezone.utc)
    fraction = format(nanoseconds // 100, "07d").rstrip("0")
    suffix = "." + fraction if fraction else ""
    return [value.strftime("%Y-%m-%d %H:%M:%S") + suffix for value in (utc.astimezone(ZoneInfo("America/New_York")), utc)]


GOLDEN = ["simple", 'quote"and\'single', "<&+>`", "\\\t\n\r\b\f", "\x00\x0b\x7f", "/plain path", "text:CREATE TABLE \"Synthetic\" (\"Value\" TEXT DEFAULT 'x');"]


if __name__ == "__main__":
    print(serialized(GOLDEN))
    print(serialized([serialized(GOLDEN), serialized(["text:index", "null:"])]))
    print(serialized(file_times(946782245123456700)))
