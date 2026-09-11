#!/usr/bin/env python3
"""Deterministic test-only subprocess; this is not an audio generation model."""
from __future__ import annotations

import argparse
from array import array
import math
import os
from pathlib import Path
import time
import wave


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dit", required=True)
    parser.add_argument("--decoder", required=True)
    parser.add_argument("--dit-precision", required=True)
    parser.add_argument("--decoder-precision", required=True)
    parser.add_argument("--threads", type=int, required=True)
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--steps", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    assert (args.dit, args.decoder, args.dit_precision, args.decoder_precision, args.threads) == (
        "sm-sfx", "same-s", "fp32", "w8a8", 4
    )
    if args.prompt == "fixture slow generation":
        time.sleep(120)
    frames = int(round(args.seconds * 44100))
    frequency = 220 + args.seed % 440
    samples = array("h")
    for index in range(frames):
        value = round(math.sin(2 * math.pi * frequency * index / 44100) * 4000)
        samples.extend((value, value))
    output = Path(args.out)
    temporary = output.with_suffix(".wav.tmp")
    with wave.open(str(temporary), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(samples.tobytes())
    os.replace(temporary, output)


if __name__ == "__main__":
    main()
