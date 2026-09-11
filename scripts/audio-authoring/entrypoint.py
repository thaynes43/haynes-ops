#!/usr/bin/env python3
"""Select the networked prefetch job or the network-free authoring service."""
from __future__ import annotations

import os
import sys


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) == 2 else "serve" if len(sys.argv) == 1 else ""
    if mode == "prefetch":
        # The downloader is deliberately anonymous and is the image's only online mode.
        os.environ["HF_HUB_OFFLINE"] = "0"
        os.environ.pop("HF_TOKEN", None)
        os.environ.pop("HUGGING_FACE_HUB_TOKEN", None)
        from prefetch import main as prefetch
        prefetch()
        return
    if mode == "serve":
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from service import main as serve
        serve()
        return
    if mode == "test" and os.path.isfile(os.path.join(os.path.dirname(__file__), "TEST_IMAGE")):
        from test_service import main as test
        test()
        return
    raise SystemExit("usage: entrypoint.py [serve|prefetch]")


if __name__ == "__main__":
    main()
