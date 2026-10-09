"""Validate only a plain Ed25519 public line; never read or generate private keys."""

import base64
import binascii
import struct

LIMIT = 8192
PRINCIPAL = "dev-env-keeper-proxmox-minter"
MARKER = "dev-env-keeper-proxmox-minter-ca"


def normalize_public(raw):
    if not raw or len(raw) > LIMIT:
        raise ValueError("PublicKeySize")
    try:
        lines = [line for line in raw.decode("ascii").splitlines() if line.strip()]
        if len(lines) != 1:
            raise ValueError("PublicKeyLines")
        fields = lines[0].split()
        if len(fields) < 2 or fields[0] != "ssh-ed25519":
            raise ValueError("PublicKeyType")
        wire = base64.b64decode(fields[1], validate=True)
        name_length = struct.unpack(">I", wire[:4])[0]
        name_end = 4 + name_length
        if wire[4:name_end] != b"ssh-ed25519":
            raise ValueError("PublicKeyWireType")
        key_length = struct.unpack(">I", wire[name_end:name_end + 4])[0]
        if key_length != 32 or len(wire) != name_end + 4 + key_length:
            raise ValueError("PublicKeyWireSize")
        if base64.b64encode(wire).decode("ascii") != fields[1]:
            raise ValueError("PublicKeyEncoding")
        return ("ssh-ed25519 " + fields[1]).encode("ascii")
    except (UnicodeError, binascii.Error, struct.error) as exc:
        raise ValueError("PublicKeyFormat") from exc


def authorized_line(public):
    return (b'restrict,cert-authority,principals="' + PRINCIPAL.encode("ascii")
            + b'" ' + public + b" " + MARKER.encode("ascii"))
