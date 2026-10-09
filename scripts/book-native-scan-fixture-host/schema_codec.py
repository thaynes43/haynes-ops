"""Exact ASCII schema serialization for the frozen .NET 10.0.1 fixture.

Only sqlite_master's schema strings are transported this way. Native full-state
serialization and all scanner/state decisions remain in the actual C# fixture.
Non-ASCII native schemas refuse until a reviewed codec successor exists.
"""
import hashlib


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


GOLDEN = ["simple", 'quote"and\'single', "<&+>`", "\\\t\n\r\b\f", "\x00\x0b\x7f", "/plain path", "text:CREATE TABLE \"Synthetic\" (\"Value\" TEXT DEFAULT 'x');"]


if __name__ == "__main__":
    print(serialized(GOLDEN))
    print(serialized([serialized(GOLDEN), serialized(["text:index", "null:"])]))
