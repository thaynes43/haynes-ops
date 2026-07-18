#!/usr/bin/env python3
"""Validate the app-owned Kometa managed include files (ADR-072 / DESIGN-042 D-09).

This is the CI gate the haynesnetwork /collections auto-merge waits on
(docs/ops/014-haynesops-collection-writes.md). It runs on PRs that touch the two
app-owned managed includes and asserts they are structurally safe to load into a
Kometa run — a malformed managed file would fail the WHOLE Kometa collections run,
so it must never reach main.

Scope (deliberately OFFLINE, secret-free, deterministic — so it is green-capable in
a public runner): YAML well-formedness + the app-managed collection-file shape and
safety contract. The deeper `--validate-level full` Kometa pass connects to Plex /
TMDb / TVDb and mutates nothing, but has no offline dry-run (DESIGN-042 D-09) and
needs cluster credentials — it is deferred to the post-bootstrap canary run
(ADR-069 C-06), not attempted here.

Exit 0 = valid; exit 1 = a validation error (fails the gate).
"""

from __future__ import annotations

import sys

try:
    import yaml
except ImportError:  # pragma: no cover - the workflow pip-installs PyYAML
    print("::error::PyYAML is not installed (pip install pyyaml).", file=sys.stderr)
    sys.exit(2)

# The EXACT Kometa builder allowlist the app compiler emits
# (packages/db/src/schema/enums.ts KOMETA_BUILDER_TYPES). A managed collection MUST
# carry exactly one of these — a raw-YAML passthrough or an owner-only engine
# (tmdb_discover / imdb_chart / …) has no business in an app-owned file.
ALLOWED_BUILDERS = {
    "imdb_list",
    "tmdb_collection_details",
    "tvdb_list_details",
    "tmdb_movie",
    "tmdb_show",
    "tvdb_show",
}

# The app tags every managed collection with this label (the Q-05 namespace marker,
# packages/domain/src/collection-provenance.ts HNET_MANAGED_LABEL). Its presence is
# the safety contract that the file contains ONLY app-managed collections.
MANAGED_LABEL = "HNet Managed"


def validate_file(path: str) -> list[str]:
    errors: list[str] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        return [f"{path}: cannot read file ({exc})."]

    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return [f"{path}: not valid YAML ({exc})."]

    if not isinstance(doc, dict):
        return [f"{path}: top-level must be a mapping with a `collections:` key."]
    if "collections" not in doc:
        return [f"{path}: missing the top-level `collections:` key."]

    collections = doc["collections"]
    # An empty managed file is `collections: {}` (what the compiler emits for zero
    # recipes) — YAML loads that as an empty dict, which is valid and expected.
    if collections is None:
        collections = {}
    if not isinstance(collections, dict):
        return [f"{path}: `collections:` must be a mapping (got {type(collections).__name__})."]

    for name, body in collections.items():
        where = f"{path}: collection {name!r}"
        if not isinstance(body, dict):
            errors.append(f"{where}: must be a mapping.")
            continue
        builders = [k for k in body if k in ALLOWED_BUILDERS]
        if len(builders) == 0:
            errors.append(
                f"{where}: no allowlisted builder key "
                f"(one of {sorted(ALLOWED_BUILDERS)} required)."
            )
        elif len(builders) > 1:
            errors.append(f"{where}: exactly one builder key allowed, found {builders}.")
        if body.get("label") != MANAGED_LABEL:
            errors.append(
                f"{where}: missing `label: {MANAGED_LABEL}` — an app-owned managed "
                f"file must contain only app-managed collections."
            )

    return errors


def main(argv: list[str]) -> int:
    paths = argv[1:]
    if not paths:
        print("usage: kometa-validate-managed.py <file.yml> [<file.yml> ...]", file=sys.stderr)
        return 2

    all_errors: list[str] = []
    for path in paths:
        file_errors = validate_file(path)
        if file_errors:
            all_errors.extend(file_errors)
        else:
            print(f"OK: {path}")

    if all_errors:
        for err in all_errors:
            print(f"::error::{err}")
        print(f"\n{len(all_errors)} validation error(s).", file=sys.stderr)
        return 1

    print(f"\nAll {len(paths)} managed file(s) valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
