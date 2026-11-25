# OP967 fourth Studio release

**Proven (2026-11-25).** Under provisional ruling **CR-65**, this pin updated the
API, web, MCP and the existing run, voice and image workers, applied migration
`0013_matte_jobs`, and updated the offline render venv to PyAV 18.1.1 at 20 CPUs.
Matte worker activation, including its credential and grant, registration,
network isolation, listener extension and offline CPU change, is in
[story 970](https://work.haynesnetwork.com/work_packages/970).

The rollout started from Studio `80975a9` with Alembic
`0012_release_constraints`. [Third pin](studio-deployment.md#op964-third-release)

The [release record](../../kubernetes/main/apps/ai/studio/release.yaml) pins
the amd64 API and web/listener images from Studio `470a90af`. The guard checks
their OCI revisions, the reviewed 0013 migration, listener authentication,
Job names and health gates, and the offline lock export.
[Studio images run 45692717257](https://github.com/thaynes43/haynes-studio/actions/runs/45692717257)
· [Studio migration fix](https://github.com/thaynes43/haynes-studio/pull/146)

## Installed evidence

Haynes-ops merged `59dc8678cbd7ed70648feccda53b3bdff9a86af4` at 22:42:26Z on
2026-11-25. The installed Studio revision is
`470a90af73a2201e6542b5d867853dce6facad04`. The coordinator recorded the rollout
in story 967, comment 10322. [Merge PR](https://github.com/thaynes43/haynes-ops/pull/5093)
· [Story 967](https://work.haynesnetwork.com/work_packages/967)

| Read or block | Result (UTC, 2026-11-25) |
| --- | --- |
| Migration | `studio-migrate-c13c8ad74dfb` ran from 22:56:51Z to 22:57:05Z with one succeeded pod and zero failed pods. The audit read `0013_matte_jobs` under the migrator identity over TLS. |
| Workloads | API replicas started at 22:57:11Z and 22:57:32Z, and the voice and image workers at 22:58:23Z. The run worker started at 23:01:30Z after Helm reported the offline render worker Ready. The render pod started at 22:56:42Z with 20 CPUs and forwarding init. All new pods, including two web and two MCP replicas, were Ready with zero restarts. |
| `wait` | Passed at 23:02:12Z. Flux stages were Ready at the selected pin. |
| `postflight` | **75 PASS, 0 FAIL** at 23:02:39Z. All three registration Jobs reused their tokens, both API endpoints were Ready, and the worker dependency reads passed. The three existing lane grants were present and read-only, the lanes were idle, the matte queue was empty and there was no matte grant. All 39 listener and lane probes returned the expected 401, 403 or 422. |
| Gatus `internal/studio` | All 21 samples between the merge and 23:02Z returned the expected 401. The last six took 2–7 ms. |
| Independent `verify` | **75 PASS, 0 FAIL** from 23:06:36Z to 23:06:52Z, exit 0. Ran the block as its own Bash process from a clean checkout of the exact merge, with `MERGED_OPS_REVISION` set and no baseline. |

**Changed.** Flux fetched the merge at 22:56:10Z on its 30-minute poll,
13 minutes 44 seconds after the merge. The audit accepted the later source
revision `d618d8b8` because its Studio inputs matched the merge.

**Changed.** The coordinator's `wait` and `postflight` and the independent
`verify` ran from session pods with Python 3.12 and PyYAML. Ingress reads went
through the `studio-mcp` pod and internal ingress Service. `/` returned 200
and `/v1/me` returned 401.

The coordinator's `postflight` matched both archived runs, their deliveries and
media indexes, and the store's file count, byte total, SHA-256 and modification
time fingerprint against the pre-merge baseline. The independent `verify` found
both archived runs, their linked deliveries and media indexes, and read the
current store totals and fingerprints. Before-and-after equality comes from the
coordinator's comparison with the private baseline. Tom's acceptance remains open.
[Story 967](https://work.haynesnetwork.com/work_packages/967)

The independent encoder reads confirmed PyAV 18.1.1 in the imported module and
installed distribution metadata in both API replicas and the run, voice, image
and offline render workers. All seven bundled library versions matched, including
libavcodec 62.28.103 and libavformat 62.12.103. The mounted render requirements
declared `av==18.1.1`. [Story 966](https://work.haynesnetwork.com/work_packages/966)

## Preflight

Under provisional coordinator ruling **CR-63**, merging the reviewed PR
is the only action that changes the cluster. Flux receives a GitHub push
event and polls Git every 30 minutes. The stages start automatically when
the new artifact arrives.

Use Bash with GNU coreutils, jq, yq, Python 3.12 with PyYAML and Flux CLI 2.10.
Every block can run from a session pod. Its ingress reads use
`kubectl exec -i` into `studio-mcp` and connect to
`traefik-internal.network.svc.cluster.local:443` with
`studio.haynesops.com` as TLS SNI and HTTP Host. The probe verifies the
certificate and sends anonymous GET requests for `/` and `/v1/me`.
Work from the clean reviewed PR head incorporating
current Ops `main`. Set `STUDIO_OP967_RECEIPT` and `STUDIO_OP967_BASELINE` to
new paths in a private directory outside the checkout. Keep submissions
closed and offline render idle through postflight. The render pod restarts
on merge. Check that each API surge replica has a free eligible node.
[Third pin runbook](studio-deployment.md#op964-third-release)

```bash
if ! bash scripts/studio-release/op967.sh preflight ||
   ! bash scripts/studio-release/op967.sh admit-merge; then
  printf 'FAIL release preflight, hold the merge\n' >&2
  exit 1
fi
```

The merge gate fetches Ops `main` and checks the reviewed commit and tree,
baseline hash, predecessor Job archive digest and every required PASS.
Merge within 60 minutes of starting
preflight. Take a fresh preflight if the PR head changes or the receipt
expires. Save the receipt, baseline and four predecessor Job logs in
`${STUDIO_OP967_RECEIPT}.jobs` for evidence and a possible retry. Flux prunes
those Jobs and pod logs. The baseline hashes the database rows and store
files and modification times.

Preflight checks the old workloads, three Ready PostgreSQL instances,
LAN status and worker dependencies. It requires
zero queued, running or held Studio jobs, Wan with both active jobs null,
an empty shared ComfyUI queue, and at most 10% CPU and 25% memory on
`talosw01`. The offline app must measure at most 250m CPU and run only its
idle PID 1 apart from the probe, with a complete PyAV 18.1.0 venv.

## Wait for automatic deployment

Check out the exact Ops merge and set `MERGED_OPS_REVISION` to its full
SHA. The script reads Flux and fetches `main` before each comparison. It
checks that the Git source, the rendered Studio stage definitions and the
trees and rendered manifests at their paths match the selected merge.
An unrelated descendant passes. A rewire, a changed stage input or an
observed revision missing after the fetch fails with its revision. Each
live stage at the selected pin must match every reviewed `spec` field.
The sole allowed unlisted Flux v1 API default is `force: false`.
A spec mismatch names the field. The inventory and each stage must
report Ready for the current generation
and a matching **applied** revision. An attempted revision alone is
pending. After merge the audit uses the selected SHA and live state.

```bash
if ! bash scripts/studio-release/op967.sh wait; then
  printf 'FAIL automatic Flux rollout, keep submissions closed\n' >&2
  exit 1
fi
```

Report failed pickup if the artifact still precedes the merge at 35 minutes.
After observing pickup, allow up to 35 minutes for stage readiness. While
pending, the script samples the API pods and reads LAN status. It checks
the two image pairs as workloads advance and requires a successful
migration audit before treating any advanced workload as valid.

| Flux stage | Added or changed automatically on merge | Pruned automatically |
| --- | --- | --- |
| AI inventory | Updates migration and the existing registration gates. | |
| `studio-render` | Adds the pinned render environment ConfigMap and updates the HelmRelease. Recreate rolls the offline pod once at 20 CPUs. | |
| `studio-migration` | Creates the digest-named 0013 migration Job. | Third-pin migration Job and pod logs. |
| `studio` | Rolls API replicas, web and MCP. Both API generations use the same port-8002 listener configuration. | |
| `studio-worker-registration` | Creates the new run registration Job. | Third-pin run registration Job and pod logs. |
| `studio-worker` | Replaces the run worker pod. | |
| `studio-media-worker-registration` | Creates the new voice and image registration Jobs. | Third-pin voice and image registration Jobs and pod logs. |
| `studio-media-workers` | Replaces the voice and image worker pods. | |

Offline render and migration can start in parallel. API, web and MCP wait
for the successful migration. The run worker waits for the API stage, its
registration and the Helm-ready render worker. Voice and image wait for the
API stage and their registrations. The render worker builds a new venv from
the exact lock export and keeps older completed Python environments on the
share. Cleanup remains unwritten. Its forwarding PID 1 reaps children and
forwards termination to the startup process group.

## Migrate while the third pin serves

`0013_matte_jobs` widens the three lane checks on `media_job_lanes`,
`queue_lane_events` and `worker_lane_grants`, and the kind, lane and
board/story checks on `media_jobs`. It seeds the matte FIFO and adds
`matte_releases SMALLINT NOT NULL DEFAULT 0` with column-scoped runtime
UPDATE permission. The constraint changes and writes commit together.
The migration locks grants, media lanes, pause events and media jobs in
that order. PostgreSQL's 500 ms timeout applies per lock acquisition. The
grant lock can queue and the other three prelocks use `NOWAIT`. A readiness
or worker-authentication read queued behind the migration can wait for the
grant lock budget plus DDL and commit, about 10 ms at the installed table
sizes. The independent fixture measured a 502 ms grant read when the holder
released just before timeout. The guarantee covers lock acquisition. API
readiness and worker requests can take longer. A lock timeout rolls the
upgrade back to `0012_release_constraints` and the Job retries within its
two-retry, 300-second budget. Keep sampling the old pods while the Job runs.
Record when the active window is too short to sample.

**Source verified.** The `80975a9` readiness probe selects existing columns
and tables with `LIMIT 0`. It doesn't require an exact Alembic revision.
Old claims and pause reads use explicit run, voice and image lanes. Old
media inserts and updates use named columns, and the release counter's
server default covers old inserts. The widened predicates still admit
the original job kinds and their original board/story associations.
Old catalog, release and archive queries use the same columns and rows.
The compatibility is for the original workloads. The old job provenance
parser cannot display a matte job document, and the matte lane is absent
from its queue controls. Retain those records for a forward recovery.
[Old readiness](https://github.com/thaynes43/haynes-studio/blob/80975a9e958d0bed78ab530c2b20f1279bf7ebe6/src/haynes_studio/api/app.py#L226)
· [Old queue SQL](https://github.com/thaynes43/haynes-studio/blob/80975a9e958d0bed78ab530c2b20f1279bf7ebe6/src/haynes_studio/db/jobs.py#L36)
· [Old job parser](https://github.com/thaynes43/haynes-studio/blob/80975a9e958d0bed78ab530c2b20f1279bf7ebe6/src/haynes_studio/queue/media_inputs.py#L51)

Flux owns the migration and registration Jobs. The `studio` apply stage
depends on the migration Kustomization and its successful digest-named Job.
The rollout keeps `maxUnavailable: 0` on the API.

The migration Job verifies the TLS login before upgrading and reports its
committed head. Each migration and registration read identifies the current
Job UID, requires exactly one succeeded pod owned by that Job and reads the
last line starting with `{` from its successful container. For a registration
the last JSON result identifies the worker and the earlier matching audit
line confirms its status. An older failed pod is normal after a retry. A
Complete Job with zero or multiple succeeded pods fails the audit.

### Retry a terminal migration with a new PR

**Expected.** The fourth-pin rollout completed on the first attempt.

Keep submissions closed. From current Ops `main`, change only these two
files under `kubernetes/main/apps/ai/studio/`. In `migration/job.yaml`,
increment the terminal Job name to its next `-rN` suffix, starting at
`-r1`. In `ks.yaml`, update the `studio-migration` Job health check to the
same name. Keep the image and Job spec. Code or configuration recovery
requires a separate review.

On the clean reviewed retry head, set `STUDIO_OP967_FAILED_OPS_REVISION`
and `STUDIO_OP967_PREVIOUS_RECEIPT` to the failed Ops merge and its receipt.
Use new private receipt and baseline paths.

```bash
if ! bash scripts/studio-release/op967.sh retry-preflight ||
   ! bash scripts/studio-release/op967.sh admit-merge retry; then
  printf 'FAIL retry preflight, hold the merge\n' >&2
  exit 1
fi
```

The retry preflight checks for a Failed Job with zero active pods. It
verifies the saved predecessor Job archive, the original baseline and
fresh hashes. It requires Ready third-pin workloads and endpoints. Idle render may already
be on PyAV 18.1.1. All other preflight checks apply.

- **Complete 0012.** The primary reports `0012_release_constraints`, all six
  original constraint definitions and both 0012 foreign keys, with the release
  column absent. Use the replacement Job above to apply 0013 and read it back.
- **Committed 0013.** The primary reports `0013_matte_jobs`, all six exact widened
  check definitions, both 0012 foreign keys, the `SMALLINT NOT NULL DEFAULT 0`
  release column and its `UPDATE` grant to `studio_app`, and a positive matte FIFO
  position. The replacement Job reads back the already committed head. The
  workload revert below is also available.

The catalogue uses PostgreSQL 16's `pg_get_constraintdef` and reads the CNPG
primary over its peer socket in a read-only transaction. A different catalogue
state fails preflight. After the retry PR merges, use its exact merge with `wait`
and `postflight` and the retry baseline.

## Serving while matte activation is pending

The API readiness probe requires the matte FIFO row seeded by 0013. The API
and MCP start with their existing configuration, and the run, voice and image
workers claim using their own lanes and existing bearer tokens. The matting
profile and sidecar client are loaded only by `studio-worker --lane matte`.
Port 8002 keeps the existing five POST routes for voice and image; only those
pods use that listener. The API's worker authentication looks up a single
presented token and grant per request. [API readiness and startup](https://github.com/thaynes43/haynes-studio/blob/470a90af73a2201e6542b5d867853dce6facad04/src/haynes_studio/api/app.py#L225)
· [MCP startup](https://github.com/thaynes43/haynes-studio/blob/470a90af73a2201e6542b5d867853dce6facad04/src/haynes_studio/mcp/server.py#L270)
· [Worker startup](https://github.com/thaynes43/haynes-studio/blob/470a90af73a2201e6542b5d867853dce6facad04/src/haynes_studio/queue/worker.py#L236)
· [Worker authentication](https://github.com/thaynes43/haynes-studio/blob/470a90af73a2201e6542b5d867853dce6facad04/src/haynes_studio/api/auth.py)

The pinned API has no submission gate tied to matte worker registration. A
valid matte request returns HTTP 202 and waits queued until a registered matte
worker claims it. The queue pause controls claiming and still accepts submissions.
Keep matte submissions closed operationally until story 970 activates the worker.
[Submit handler](https://github.com/thaynes43/haynes-studio/blob/470a90af73a2201e6542b5d867853dce6facad04/src/haynes_studio/api/job_routes.py#L235)
· [Lane authorization](https://github.com/thaynes43/haynes-studio/blob/470a90af73a2201e6542b5d867853dce6facad04/src/haynes_studio/api/run_routes.py#L333)

### Carried to story 970

- **P2.** A queued cutout can keep image submission disabled when following
  **Generate asset** from the same view. **Close job view** restores submission.
  A queued matte job also fails postflight and requires a recorded matte pause
  before the rollback snapshot can proceed.
- **P3.** Pin the local helper entry points permitted by the read-only command
  scanner and inspect their payloads.
- **P3.** Compare the full offline requirements list with the pinned Studio
  export in required CI.
- **P3.** Add tests that fail when each independent release-phase and schema
  guard is removed, including the all-stale encoder case.

[Story 970](https://work.haynesnetwork.com/work_packages/970) · [Independent review](https://github.com/thaynes43/haynes-ops/pull/5093#issuecomment-7176652850)

## Postflight

Run from the selected merge with `STUDIO_OP967_BASELINE` set. The script
reads every Flux stage, the migration and three registration Job audits,
workloads, endpoints, engines, encoders, LAN and the data snapshot. The
snapshot requires three existing grants, no matte grant, no queued, running
or held jobs in the matte queue, idle existing lanes and unchanged archives.
It rechecks Flux before the listener POST probes, which fail validation or
authorization before a write. Keep submissions closed through verification.

```bash
if ! bash scripts/studio-release/op967.sh postflight; then
  printf 'FAIL release verification, keep submissions closed\n' >&2
  exit 1
fi
```

Use `verify` for the same audits and status probes with an optional baseline.

```bash
if ! bash scripts/studio-release/op967.sh verify; then
  printf 'FAIL release verification\n' >&2
  exit 1
fi
```

The probe reports `av.__version__` and `av.library_versions` from both API
pods, the run, voice and image workers and the offline render venv.
It requires 18.1.1 and identical bundled library versions. Record the
actual values and the data comparisons on both
[966](https://work.haynesnetwork.com/work_packages/966) and
[967](https://work.haynesnetwork.com/work_packages/967) from the Codex
postflight session. This proof reads the installed encoders and archived
data without rebuilding deliveries or runs.

The listener probes require anonymous claim to return 401, malformed or
unknown-lane claims to return 422, and a known other lane (including matte)
or disallowed `settle_only` to return 403. The port-8002 listener refuses
`/resume`. The API refuses forwarded media requests that default to the run
lane. The run worker's malformed `/resume` reaches API validation. Record
engine reachability and Gatus before opening run, voice and image submissions. [Gatus check](studio-deployment.md#gatus-and-backup-coverage)

The static check permits `kubectl get`, `logs`, `wait`, and `top` with the
reviewed read flags. Its named `kubectl exec` probes read PostgreSQL and the
store, dependency and ingress status, the idle render process, encoder versions
and worker engines. Flux renders local inputs with `flux build kustomization
--dry-run --in-memory-build`. Direct HTTP reads use GET or HEAD. The named
listener module sends only POSTs expected to fail at the listener,
authentication, lane authorization or payload validation.

## Roll back workloads through a reviewed PR

**Expected.** The fourth-pin rollout completed without a workload rollback.

Keep submissions closed until active and held work settles. The snapshot
allows queued matte jobs with a recorded matte pause and preserves any matte
job and pause records. Keep schema 0013. Old migration images cannot resolve it.

Base a workloads-only revert on current Ops `main`. Under
`kubernetes/main/apps/ai/studio/`, use the verified third-pin manifests.

- Restore the image pair in `app/api.yaml`, `app/mcp.yaml`, `app/web.yaml`,
  `worker/deployment.yaml` and `media-workers/{voice,image}.yaml`. The generated
  port-8002 ConfigMap stays available at `studio-media-worker-routes-cbf48cht27`.
- Restore `worker-registration/job.yaml` and
  `media-worker-registration/{voice,image}-job.yaml` and their three Job health
  names in `ks.yaml`. Keep the render dependency and Helm health check.
- Retain `migration/job.yaml`, its Kustomization and `release.yaml` as the
  schema pin. Keep the offline render venv at PyAV 18.1.1 and its limit at 20 CPUs.
  Adjust the release guard to assert the third-pin workload pair alongside the
  fourth-pin migration and render lock.
- Keep the exact migration Job health check when the Job completed. For
  a terminal Job with verified 0013, remove the `healthChecks` field from
  `studio-migration` in this revert. Flux can then reconcile the restored
  workloads against the retained schema. The terminal Job stays available
  as evidence.

On the reviewed revert head, set `MERGED_OPS_REVISION` to the fourth-pin
merge and set new private `STUDIO_OP967_RECEIPT` and
`STUDIO_OP967_BASELINE` paths. The preflight checks the live Flux
sequence, the idle offline environment and ingress. It checks the
successful Job's log or, for a terminal Job, verifies the complete
0013 catalogue on the primary. It takes the data snapshot from a Ready
API pod on either verified image, so it works during a stalled turnover.

```bash
if ! bash scripts/studio-release/op967.sh rollback-preflight ||
   ! bash scripts/studio-release/op967.sh admit-merge rollback; then
  printf 'FAIL rollback preflight, hold the merge\n' >&2
  exit 1
fi
```

After the revert merges, return to the fourth-pin checkout. Set
`ROLLBACK_OPS_REVISION` to the full revert merge SHA and set
`STUDIO_OP967_ROLLBACK_BASELINE` to the revert preflight's snapshot.
Run the same `wait` block, then compare the old images, endpoints,
engine reachability and retained schema and data.

```bash
if ! bash scripts/studio-release/op967.sh compare; then
  printf 'FAIL rollback read-back, keep submissions closed\n' >&2
  exit 1
fi
```

Record the Gatus result before reopening the original lanes. A forward
fix is needed to resume matte submissions and job detail.
