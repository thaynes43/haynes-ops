# Bounded library-only reading-list stage

Manual preparation for haynesnetwork #825 / DESIGN-037. The retained private recipe
helpers allowed item deletion and predate current runtime admission. This successor
uses the deployed Libretto matcher/reconciler/Kavita adapter, refuses any plan that
would remove an old item, and permits only approved recipe saves and owned-list
creation, chapter additions, ordering and provenance metadata. It never invokes
normal `/api/apply`, a provider builder, acquisition or a disk cache writer.

Unused COPY pause/inverse preparations [#3622](https://github.com/thaynes43/haynes-ops/pull/3622)
and [#3623](https://github.com/thaynes43/haynes-ops/pull/3623) are closed as superseded.
Neither issued Stop nor started a Job. Their stale bindings cannot authorize a new
COPY stage; a new exact pair requires successful native baseline closure, current
v0.110.5 SOURCE/MAIN fences and independently ratified two-extra phase/inverse/watch.
Reading-list stages use this separate child-only admission and require no pause pair.

The normal service, schedules and global environment remain live. `loadConfig` is
called with a child-only empty LazyLibrarian endpoint; no service setting changes.
Preparation and a merged helper do not grant apply authority. Finish library recovery,
fresh source/physical-file/preservation proofs and independent review, then ROOT must
ratify the exact private approval SHA and phase before execution.

## Missing canonical works

DESIGN-037 permits multiple legitimate copies of a work; it does not require adding
an extra copy. The completion policy for this stage preserves every existing item
and adds only canonical works absent from the list. A new work gets one physically
proved chapter: prefer an exact ISBN proof over full title and contributor proof,
then the lowest chapter ID among equally proved copies. Ambiguous canonical owners
refuse. Existing duplicates remain, and the outbound no-deletion guard stays intact.

The worker applies this policy to both the approved preview and the native adapter's
plan before reconciliation. The complete fresh chapter snapshots, physical proof
artifact, original item payloads and exact ordered plan remain required. This policy
is child-local; the deployed Libretto modules and normal acquisition are unchanged.

## Private approval and admission

`run-stage.py --approval <private.json> --approval-sha256 <exact-sha> --journal-dir
<new-private-directory> --execute` is the only writer entrypoint. It executes one
bounded child in the existing admitted Libretto Pod. No Job, pause, restart, delete
or GitOps mutation is part of this tool. A missing `--execute` validates the local
approval only and performs no cluster call.

The schema-1 approval contains:

- `phase`: a UUID; `capturedAt`: the earliest native/store/source/preservation capture
  start, never a refreshed approval time; `explicitRootApproval: true`;
- `stage`: `initial` (exactly one existing recipe) or `batch` (one to four recipes).
  Batch admission also binds a separately reviewed completed initial receipt by SHA;
- `native`: namespace, Pod name/UID/spec SHA, ReplicaSet name/UID and Deployment
  name/UID/spec SHA, container, actual image and imageID;
- `compiledModules`: exact relative file/SHA map of all deployed `dist` JavaScript
  and JSON files; `runsSha256`: the complete raw native run-store SHA; `timezone`;
- `helpers`: reviewed `launcherSha256`, `protocolSha256`, `workerSha256` and
  `bundleSha256`, generated locally with `python3 run-stage.py --print-helper-bindings`
  before freezing the approval. The host verifies these before execution and each
  before/intent ACK; the child refuses a bundle that differs from the approved SHA;
- `recipeStore`: the full parsed current store, preserving every original payload
  plus only reviewed actual prior receipts; `recipeStoreSha256`;
- `artifacts`: absolute private paths, SHA-256 and original `capturedAt` for current
  physical/source/scan/state proofs. The host verifies bytes and clocks; ROOT reviews
  their substance. Opaque `verified` booleans alone do not establish source identity;
- `scopes`: exact recipe payload, `save` (`false`, `create`, or `update`), original
  payload for an update, canonical builder `works`, works SHA, complete fresh chapter
  snapshots for every matched series, approved ordered chapter keys, original owned
  list identity and full item payloads. Source/physical proofs must name these exact
  works, contributor rosters, chapters and files. Unknown identity remains held.

All captures expire 300 seconds after the earliest original start. The child has at
most 180 seconds, further bounded by that original expiry; each network request is
bounded. Admission re-reads native identity before execution and before each write
ACK. The child checks recipe store, raw run store, installed scheduler's next run and
current module/physical chapter identity. A running record, malformed/missing store,
scheduled run inside the remaining stage, restart, drift or expiry refuses.
The run and recipe store are rechecked after the durable intent ACK as well, so an
observable change during the receiver's admission wait stops before dispatch.

## Journal and preservation

Before any write, the child sends the complete before snapshot and then an exact
intent to the host. The host records each immutable 0600 event, fsyncs it and its
0700 directory, rechecks native identity, then sends an identity/hash-bound ACK.
No ACK means no new request. Responses and final readback also require durable ACKs.
The host keeps all received events on error, deadline or partial completion. Raw
recipe/list/source payloads and journals stay private and never enter public git.

The native sync adapter is used only after every old chapter key is proved present
exactly once in the approved desired plan. The outbound guard rejects `delete-item`
even if the adapter later attempts it. Every old item ID and its non-order payload
must survive final readback. Item-to-chapter proof is cleared at both scope boundaries
and bound to the exact list ID that was freshly read; one list's item IDs cannot
authorize another list's ordering. A lost response, partial journal, rejected readback or
unknown child result halts; do not replay, adopt a saved recipe/list or roll back
implicitly. ROOT reviews actual receipts before a separate exact recovery.

These checks bound cross-system races; they do not lock another API client. Native
Pod/spec and full raw run history do not provide an atomic queue lock. Any observed
interference refuses rather than becoming authorization for an additional write.

## Validation

Use finite protocol/fixture tests only, under `nice -n 19`, serially. No CPU stress,
busy loops, wide parallel or repeated test runs. `node --test protocol.test.mjs`
does not contact a cluster or vendor. A future actual stage requires current private
payloads and exact ROOT ratification; no production apply is part of preparation.

From the repository root, run each bounded command once after relevant changes:

```sh
nice -n 19 node --test --test-concurrency=1 scripts/book-reading-list-stage/protocol.test.mjs scripts/book-reading-list-stage/worker.test.mjs
nice -n 19 python3 -B scripts/book-reading-list-stage/test_run_stage.py
```

The finite tests cover complete-identity/preservation/recipe/endpoint admission,
native controller/image drift, approved helper drift, immutable ACK ordering and
unknown outcomes. The actual child runs against local adapter fixtures to prove
add/order preservation in one- and two-list stages, chapter drift, lost intent ACK and run/store drift during
ACK without any cluster/vendor call. The deployed read-only interface check found
Node 22.23.3, America/New_York scheduling, all five required native adapter methods,
normal acquisition configured and child acquisition absent. This is interface
evidence; fresh full inputs and exact runtime ratification remain required.
