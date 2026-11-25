# OP967 fourth Studio release

**Expected.** This source pins matte jobs, the matte lane, the matting
companion and PyAV 18.1.1. The coordinator owns installation and postflight.
The installed starting point is the [third
pin](studio-deployment.md#op964-third-release), Studio `80975a9`, with
Alembic `0012_release_constraints`. [Story 967](https://work.haynesnetwork.com/work_packages/967)

The [release record](../../kubernetes/main/apps/ai/studio/release.yaml) pins
both amd64 images, including the web image for the API's worker listener.
The [successful Studio images
run](https://github.com/thaynes43/haynes-studio/actions/runs/45653635223)
published the pair for `0f6ca21e48f8a8e4d6745cba91267c4726733b47`.
To refresh a pin from a later qualifying successful `main` run, use
`python3 scripts/tests/studio-release/check.py --pin <full-studio-revision> <run-id>`
from the Ops checkout with PyYAML, Kustomize, uv and Python 3.12 available.
It verifies both tags and their manifests and OCI revision labels, updates
the digest-named Job references and exports the offline runtime requirements
from the selected lock. Verify that the later revision still admits the
provisioned matting profile before using it.

## Preflight

Arrange a quiet window with Studio submissions closed and no offline renders.
Keep the window through postflight. Flux can start reconciling immediately
after merge. Check for a free eligible node for each API surge replica, as
in [OP964](studio-deployment.md#op964-third-release). Use the reviewed Ops
checkout from an authorized terminal with LAN access and `XDG_RUNTIME_DIR`
set. The script prints each check and returns nonzero on a failure.

```bash
if ! bash scripts/studio-release/op967.sh preflight; then
  printf 'FAIL release admission, hold the merge and Flux rollout\n' >&2
  exit 1
fi
printf 'PASS release admission\n'
```

This reads the Wan API's `/v1/ready` and requires readiness plus both
`active_video_job` and `active_foley_job` present and null. It reads
`comfyui-0`'s shared queue and requires both lists empty. Node measurements
must show at most 10% CPU and 25% memory on `talosw01` and at most 250m CPU
in the offline render app. The read-only data probe requires zero queued,
running or held jobs and verifies both archived runs, deliveries and media
indices. It saves file count, byte count, content hash and a modification
time hash for the canonical store, excluding API readiness scratch. It also
saves counts and canonical hashes of run, delivery and job rows. Use
`STUDIO_OP967_BASELINE` to select a new file for another admission window.
Keep the original file through postflight. An existing baseline path
causes a refusal.

**Proven at pickup.** OP459's successful provision and smoke used matting
image `sha256:209031a328957e3fd25e12a4acb5518abd08e67adbc5efe1c89b721964cac0a5`
and allocation `matting-op459-birefnet-lite-v1-03`. The installed manifest,
checksum file and smoke report agree on
`7093e0b535eb7b6ab8a5f679ceb235bde6e40e4708393c54e0fcd92665f91c94`.
The OCI revision and image environment name
`f217cd21ed7899ab7d2b633d19813c536b1c2a14`. That revision and the selected API
revision have the same `src/haynes_studio/matting` Git tree
`20ab3415338c761ed8b8b9e9acfcfbd9408bbe9a`. The new worker's locked
`Profile.installed().manifest()` with the sidecar image and revision computes
the installed hash. Its NumPy, Pillow and SciPy versions are 2.4.6, 13.0.0
and 1.16.3. Reuse the existing weights allocation. A profile difference
blocks activation. [OP459](https://work.haynesnetwork.com/work_packages/459)

## Migrate while the third pin serves

`0013_matte_jobs` widens the three lane checks on `media_job_lanes`,
`queue_lane_events` and `worker_lane_grants`, and the kind, lane and
board/story checks on `media_jobs`. It seeds the matte FIFO and adds
`matte_releases SMALLINT NOT NULL DEFAULT 0` with column-scoped runtime
UPDATE permission. The constraint changes and writes commit together.
DDL can wait for or briefly block old requests. Keep sampling the old pods
while the Job runs. Record when the active window is too short to sample.

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
[Migration](https://github.com/thaynes43/haynes-studio/blob/0f6ca21e48f8a8e4d6745cba91267c4726733b47/src/haynes_studio/db/migrations/versions/0013_matte_jobs.py)
· [Old readiness](https://github.com/thaynes43/haynes-studio/blob/80975a9e958d0bed78ab530c2b20f1279bf7ebe6/src/haynes_studio/api/app.py#L226)
· [Old queue SQL](https://github.com/thaynes43/haynes-studio/blob/80975a9e958d0bed78ab530c2b20f1279bf7ebe6/src/haynes_studio/db/jobs.py#L36)
· [Old job parser](https://github.com/thaynes43/haynes-studio/blob/80975a9e958d0bed78ab530c2b20f1279bf7ebe6/src/haynes_studio/queue/media_inputs.py#L51)

Flux owns the migration and registration Jobs. The `studio` apply stage
depends on the migration Kustomization and its successful digest-named Job.
The rollout keeps `maxUnavailable: 0` on the API. Use the preflight exit
gate before merging or reconciling. After merge, sample the API and schema
gate from the Ops checkout.

```bash
root=kubernetes/main/apps/ai/studio
job=$(yq -er 'select(.kind == "Job") | .metadata.name' "$root/migration/job.yaml") || exit 1
if ! flux reconcile kustomization cluster-apps -n flux-system --with-source; then
  printf 'FAIL Flux source and inventory\n' >&2; exit 1
fi
printf 'PASS Flux source and inventory\n'
if ! kubectl -n ai get pods -l app.kubernetes.io/name=studio-api \
    -o 'custom-columns=NAME:.metadata.name,IMAGE:.status.containerStatuses[0].imageID'; then
  printf 'FAIL API pod sample\n' >&2; exit 1
fi
printf 'PASS API pod sample\n'
if ! flux reconcile kustomization studio-migration -n ai ||
   ! kubectl -n ai wait --for=condition=Complete --timeout=5m "job/$job" ||
   ! bash scripts/studio-release/op967.sh migration; then
  printf 'FAIL migration gate, hold rollout follow-up\n' >&2; exit 1
fi
printf 'PASS migration gate\n'
```

The migration Job verifies the TLS login before running the upgrade, then
reports its actual head. If the upgrade transaction fails, the prior schema and serving pods stay in place. Use a reviewed forward fix. At the same image digest
a retry needs a new `-rN` Job name and matching Flux health check.

## Roll the workloads

The run worker keeps its own pod and 4 CPUs. The new `studio-matte-worker`
Deployment on `talosw01` pairs a single `studio-worker --lane matte` with
the smoked sidecar. They share a 256 MiB `/matting` volume with supplementary
GID 10001 for the mode-0660 Unix socket and staged attempt files. The worker
uses the canonical store read/write as UID 1000. The sidecar uses UID/GID
10001 and mounts only read-only weights plus scratch. Each container has
a five-second preStop sleep and a 90-second pod grace period.

| Workload on talosw01 | CPUs | Memory request | Memory limit |
| --- | ---: | ---: | ---: |
| Offline render app | 17 | 1 GiB | 32 GiB |
| Run worker | 4 | 2 GiB | 8 GiB |
| Matte worker | 1 | 192 MiB | 1 GiB |
| Matting sidecar | 2 | 8 GiB | 10 GiB |
| Total | 24 | 11 GiB + 192 MiB | 51 GiB |

At pickup, `talosw01` reported 39 CPUs and 111,129,616 KiB (105.98 GiB)
allocatable. Limits shown here consume 51 GiB of that before other workloads.
With the dormant segmentation overlay composed on top, offline render would
drop to 15 CPUs and the additional container would add 2 CPUs and 5 GiB.
The combined ceiling would be 24 CPUs and 56 GiB. Keep heavy work serialized
and cap manual offline render at `--workers 15` in the fourth pin to leave
two cores in its 17-CPU allocation. The new offline app uses a forwarding
PID 1 that reaps children and sends termination signals to the render
startup process group. Its venv uses the exact exported lock dependencies.

The matte pod waits for the selected API, the Helm-ready offline CPU split
and token projection resources. External Secrets projects a stable,
independent generated token. The sidecar's readiness probe requires its
exact profile hash. The matte container keeps its progress marker fresh
while probing for its grant with an extra-field claim body that can't take
work. Flux observes the healthy Deployment before applying the
digest-named registration Job, which mounts the same generated token and
uses the migration identity to register `studio-matte-worker` for `matte`.
After it gets the expected validation refusal, the wrapper replaces
itself with `studio-worker --lane matte`. The worker also verifies the
sidecar before claims. Its progress liveness allowance is 300 seconds.
The API admits matte, voice and image at port 8002. The matte pod's egress
allows DNS and that port only. The listener strips forwarded headers and
admits the exact `POST /internal/v1/work/release-matte` alongside its five
existing POST routes.

```bash
: "${MERGED_OPS_REVISION:?Set to the full merged Ops release revision}"
if ! bash scripts/studio-release/op967.sh migration; then
  printf 'FAIL schema gate, hold rollout follow-up\n' >&2; exit 1
fi
failed=0
for stage in studio-render studio studio-worker-registration studio-worker \
    studio-media-worker-registration studio-media-workers studio-matte-worker studio-matte-worker-registration; do
  timeout=7m
  if [[ $stage == studio-render ]]; then timeout=23m; fi
  if flux reconcile kustomization "$stage" -n ai --timeout="$timeout"; then
    printf 'PASS reconcile %s\n' "$stage"
  else
    printf 'FAIL reconcile %s\n' "$stage" >&2
    failed=1; break
  fi
done
if ((failed)) || ! bash scripts/studio-release/op967.sh roll "$MERGED_OPS_REVISION"; then
  printf 'FAIL rollout and grant gates\n' >&2; exit 1
fi
printf 'PASS rollout and grant gates\n'
```

The three existing registrations must return audit status 200. The matte
grant's first registration returns 201. Status 200 is the matching-token
no-op on a retry. An unexpected conflict stops the rollout for review.

## Postflight

Run from the same revision and with the same baseline as preflight. The
script only permits the final idle matte claim after the old API pods have
left, both selected endpoints are Ready, the four grants and idle queues
are proven, the other route probes pass and the postflight data is equal
to the preflight snapshot.

```bash
if ! bash scripts/studio-release/op967.sh postflight; then
  printf 'FAIL release verification, keep submissions closed\n' >&2
  exit 1
fi
printf 'PASS release verification\n'
```

The probe reports `av.__version__` and `av.library_versions` from both API
pods, the run, voice, image and matte workers and the offline render venv.
It requires 18.1.1 and identical bundled library versions. Record the
actual values and the data comparisons on both
[966](https://work.haynesnetwork.com/work_packages/966) and
[967](https://work.haynesnetwork.com/work_packages/967) from the Codex
postflight session. This proof reads the installed encoders and archived
data without rebuilding deliveries or runs.

| Probe | Voice | Image | Run | Matte |
| --- | ---: | ---: | ---: | ---: |
| Malformed `/resume` | 403 | 403 | 422 | 403 |
| A known other lane and invalid fields | 403 | 403 | 403 | 403 |
| Extra field with lane omitted | 403 | 403 | 422 | 403 |
| Malformed JSON or unknown lane on claim | 422 | 422 | 422 | 422 |
| Own-lane claim with `settle_only: true` | 403 | 403 | 403 | — |
| Malformed `/release-matte` | 403 | 403 | 403 | 422 |
| Malformed heartbeat, publish, complete or fail without a job | 403 | 403 | — | 403 |
| Matte idle claim through port 8002 | — | — | — | 204 |

Media traffic on port 8002 gets a listener refusal on excluded run routes.
The remaining cross-lane refusals require the API's `work.lane_forbidden`
error. Malformed release for the matte token reaches API validation.
A GET and a trailing slash for release are refused at the listener.
The API validation order was independently exercised on Studio PR 139.
[Source review](https://github.com/thaynes43/haynes-studio/pull/139#issuecomment-7165445269)

**Expected.** Fenced release against a disposable cutout needs an existing
permitted still. At pickup the store held the two imports and no still
image for this check. When one exists, use a disposable private job with
normal consumption controlled at an idle boundary, claim it through
8002, then send its current `job_id` and `fence` with
`POST /internal/v1/work/release-matte` before dispatch. Require 204 and
confirm the same job is queued in its scoped status read, or show the
higher fence if it has already been reclaimed. Do not create a source for
this deployment pin.

Read the existing [Gatus check](studio-deployment.md#gatus-and-backup-coverage)
and the [read-only engine checks](studio-deployment.md#reconcile-and-check-health).
Record each check before opening submissions. The fourth pin checks
runtime reachability and compares archived data hashes. A real cutout and a
person's review are separate evidence.

## Roll back with schema 0013 retained

Hold submissions and wait for running or dispatched work to settle. If
queued matte jobs must be preserved through rollback, pause the lane
using the existing Studio lane control and wait for current work to settle.
The rollback probe requires a recorded matte pause before admitting queued
matte jobs. Keep the reviewed fourth-pin checkout for the data probes. Use a new private
baseline path and make this check before issuing a workload rollback.

```bash
: "${STUDIO_OP967_BASELINE:?Choose a new private rollback baseline path}"
if ! bash scripts/studio-release/op967.sh rollback-baseline; then
  printf 'FAIL rollback admission\n' >&2; exit 1
fi
printf 'PASS rollback admission\n'
```

The downgrade refuses with any matte job, pause event or lane grant.
A successful worker registration alone is sufficient. Preserve the
records and keep schema `0013_matte_jobs`. Use a reviewed workloads-only
GitOps rollback PR. Keep the completed fourth migration Job, its
`studio-migration` Flux path and health check intact. An old migration
image can't resolve revision 0013. Do not repin the migration Job or
revert the full release. Revert API, listener, web, MCP and the three
existing workers to the verified third image pair, with matching
workload and registration health gates. Remove the matte Deployment
and registration stage. Preserve its generated projection and the
provisioned allocation. Keep offline render at 17 CPUs until the matte
pod has left, then restore its third-pin CPU split and requirements if
doing a full workload rollback. Record the two gates separately in the
rollback PR and adjust the release guard there to verify the split between
the retained schema Job and the third workload pin.

```bash
if ! bash scripts/studio-release/op967.sh compare; then
  printf 'FAIL rollback read-back, keep submissions closed\n' >&2
  exit 1
fi
printf 'PASS rollback read-back\n'
```

Verify the existing engines and Gatus again. Matte records remain intact,
and cutout submission, job detail and processing need a forward fix before
they are available again. A schema reversal on an empty test database
and production workload recovery are different operations.
