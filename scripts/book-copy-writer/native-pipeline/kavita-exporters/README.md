# Private Kavita evidence exports

The three scripts here are tracked successors of the retained read-only Kavita
exporters. In the October 10 V23 attempt, their ordinary `Path.write_text` calls
created the raw reading-state, dependency and saved-lock reports with mode
`0644`. The supervisor correctly refused the first report before creating the
COPY writer. File type, single-link ownership and the existing 32 MiB raw-export
cap were not the failure.

Each successor keeps the original CLI, SQLite read-only queries, JSON fields,
serialization and refusal conditions. Its only behavior change is private
publication of every report, including the reading-state and dependency compare
reports: create a fresh regular file with `O_EXCL | O_NOFOLLOW`, mode `0600`,
write and fsync it, verify its physical identity and private metadata, then fsync
the parent. Missing output parents are created with mode `0700`; an existing
output parent must already be owned by the current UID with permission bits
`0700`. The PVC inherits setgid on directories (`2700`); that inheritance is
retained without adding group/other access. Other directory mode bits refuse.
Parent traversal refuses symlinks. Existing files, links or directories at an
output path refuse without overwrite or chmod. A failed write leaves its fresh
path consumed, requiring a new output path.

The scripts remain self-contained, so a future reviewed packet can bind the same
three exporter names and their actual new source hashes. The supervisor's
private artifact predicate and byte caps remain intact. Historical V8 scripts,
V23 outputs, the frozen 38-file archive, host programs, signed image, runtime
modules and window clocks remain unchanged. These sources authorize no capture
or production window and do not repair or reuse the refused artifacts.

Focused controls use one small synthetic copied SQLite database and temporary
files. They exercise all three real export entrypoints and both compare routes
under a permissive caller umask, then seal the actual outputs with the existing
supervisor function. They also require refusal of unsafe parents, symlinks and
pre-existing outputs without changing their bytes. Run the finite suite serially
with `nice -n 19`; no load generators, wide parallel runs or test loops.
