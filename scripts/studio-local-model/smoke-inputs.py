#!/usr/bin/env python3
"""Create private, synthetic CR-20 smoke inputs without a media dependency."""
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import wave
import zlib

GLYPHS = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("10010", "10010", "10010", "11111", "00010", "00010", "00010"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
}


def png_chunk(name, data):
    return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))


def panel(width, height, face, code, color):
    pixels = bytearray(b"\xf3\xf0\xda" * (width * height))

    def rectangle(left, top, right, bottom, fill):
        x0, x1 = left * width // 100, right * width // 100
        row = fill * (x1 - x0)
        for y in range(top * height // 100, bottom * height // 100):
            start = (y * width + x0) * 3
            pixels[start:start + len(row)] = row

    ink, skin, cover, white = b"\x19\x18\x17", b"\xff\xc4\x89", b"\x75\x75\x75", b"\xff" * 3
    rectangle(25, 48, 75, 86, color)
    rectangle(32, 20, 68, 48, skin)
    rectangle(38, 29, 43, 33, ink)
    rectangle(57, 29, 62, 33, ink)
    rectangle(48, 32, 52, 38, b"\xd2\x90\x62")
    rectangle(42, 40, 58, 43, ink)
    if not face:
        rectangle(27, 17, 73, 48, cover)
    if code:
        rectangle(28, 55, 72, 73, white)
        for index, char in enumerate(code):
            for y, row in enumerate(GLYPHS[char]):
                for x, bit in enumerate(row):
                    if bit == "1":
                        left, top = 33 + 12 * index + 2 * x, 57 + 2 * y
                        rectangle(left, top, left + 2, top + 2, ink)
    else:
        rectangle(22, 48, 78, 88, cover)

    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        scanlines.extend(pixels[y * width * 3:(y + 1) * width * 3])
    return (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + png_chunk(b"IDAT", zlib.compress(scanlines)) + png_chunk(b"IEND", b""))


def main(directory):
    directory.mkdir(mode=0o700)
    green, red = b"\x18\x86\x43", b"\xc7\x24\x38"
    frames = [(True, "A17", green), (False, "B24", green), (True, "C38", red), (True, None, green)]
    paths = []
    for index, frame in enumerate(frames, 1):
        path = directory / f"frame-{index}.png"
        path.write_bytes(panel(1080, 1920, *frame))
        paths.append(path)
    still = directory / "s2v.png"
    still.write_bytes(panel(576, 1024, *frames[0]))
    audio = directory / "s2v.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        pcm = b"".join(struct.pack("<h", round(1200 * math.sin(2 * math.pi * 210 * i / 16000)))
                       for i in range(32000))
        wav.writeframes(pcm)
    paths.extend((still, audio))
    evidence = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    (directory / "hashes.json").write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
