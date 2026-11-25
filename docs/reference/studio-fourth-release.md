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
published the previous candidate, `0f6ca21e48f8a8e4d6745cba91267c4726733b47`.
The release guard refuses that build's migration. After the Studio fix
merges, select a successful Studio images run on `main` whose commit carries
the reviewed fix. Repin with
`python3 scripts/tests/studio-release/check.py --pin <full-studio-revision> <run-id>`
from the Ops checkout with PyYAML, Flux CLI 2.10, Kustomize, uv and Python
3.12 available.
It verifies both tags and their manifests and OCI revision labels, updates
the digest-named Job references and exports the offline runtime requirements
from the selected lock. It also checks the published revision's worker-route
authentication and the bytes of the reviewed migration. Verify that the
selected revision still admits the provisioned matting profile.
[Studio migration fix](https://github.com/thaynes43/haynes-studio/pull/146)

## Preflight

**The merge is the apply gate under provisional coordinator ruling CR-62.** Arrange a quiet window with Studio
submissions closed and the offline render container idle. The offline
render pod restarts on merge. Finish any offline render or exec session
before preflight, and keep the quiet window through postflight. Check for
a free eligible node for each API surge replica as in the [third pin
runbook](studio-deployment.md#op964-third-release). Use a LAN-capable Bash
shell with GNU coreutils and PyYAML in the clean, reviewed PR head
incorporating current Ops `main`. Use new receipt and baseline paths in a private directory outside
the checkout. Keep the same paths through postflight.

```bash
if ! bash scripts/studio-release/op967.sh preflight; then
  printf 'FAIL release admission, hold the merge and Flux rollout\n' >&2
  exit 1
fi
printf 'PASS release admission\n'
```

The preflight writes `STUDIO_OP967_RECEIPT` with the reviewed Ops revision
and Git tree, UTC time, every required check, and the baseline path and
hash. Immediately before merging, fetch Ops `main` and run the gate below
from the reviewed tree. Merge within 60 minutes of starting the passing
preflight. If the reviewed tree changes or the window closes, take a new
preflight with new evidence paths.

```bash
if ! bash scripts/studio-release/op967.sh admit-merge; then
  printf 'FAIL release merge gate\n' >&2
  exit 1
fi
printf 'PASS release merge gate\n'
```

The baseline at `STUDIO_OP967_BASELINE` records archived deliveries, run
and job rows, and store file totals, content and modification time hashes. Before the merge, the script also saves the four predecessor
Job logs from their succeeded pods under `${STUDIO_OP967_RECEIPT}.jobs`.
The receipt records the archive's path and hash. Keep the receipt, baseline
and logs together for a possible retry. Flux prunes those Jobs and their pod logs.

Preflight requires Wan `/v1/ready` with both active-job fields present and
null, the shared `comfyui-0` queues empty, and at most 10% CPU and 25%
memory on `talosw01`. The offline app must measure at most 250m CPU, have
only its idle PID 1 apart from the probe, and have the completed PyAV
18.1.0 venv. Studio must have zero queued, running or held jobs.

After merge, fetch Ops `main`, set `MERGED_OPS_REVISION` to the exact
full merge SHA, and check out that SHA. Keep the release receipt and
baseline at their recorded paths. The script checks that the reviewed
tree matches the selected merge on `origin/main`, all required checks
passed, the baseline hash matches and the receipt was valid at the merge.
The receipt remains valid for completion and recovery after the window
closes.

Flux starts the rollout automatically. Before manually accelerating it,
the script reads the GitRepository artifact, `cluster-apps` and the Studio
stages. It accepts the selected merge or descendants that carry the same
Studio and Studio-render source. A stage can be pending on that artifact
while its dependencies apply. It checks that the Deployment templates
use the predecessor or selected image pair and that migration has
completed if any selected workload has already advanced. If the artifact
has yet to reach the selected merge, wait for the ordinary source sync.

| Flux stage | Added or changed automatically on merge | Pruned automatically |
| --- | --- | --- |
| AI inventory | Adds matte worker and registration stages. Updates render, migration and the existing registration gates. | |
| `studio-render` | Adds the fourth-pin render ConfigMap and updates the HelmRelease. The offline render pod restarts once at 17 CPUs. The installed and composed paths render the same target. | |
| `studio-migration` | Creates the digest-named 0013 migration Job. | Third-pin migration Job and pod logs. |
| `studio` | Rolls API replicas, their worker listeners, web and MCP. Updates API ingress and adds the new listener ConfigMap. The old listener ConfigMap stays available for predecessor pod replacements during a stalled surge. | |
| `studio-worker-registration` | Creates the new run registration Job. | Third-pin run registration Job and pod logs. |
| `studio-worker` | Replaces the run worker pod. | |
| `studio-media-worker-registration` | Creates the new voice and image registration Jobs and the independent matte token projection. | Third-pin voice and image registration Jobs and pod logs. |
| `studio-media-workers` | Replaces the voice and image worker pods. | |
| `studio-matte-worker` | Adds the Deployment, ConfigMap, ServiceAccount and NetworkPolicy. | |
| `studio-matte-worker-registration` | Creates the matte registration Job after the exact-profile sidecar is Ready. | |

Offline render and migration can start in parallel. API, web and MCP wait
for the successful migration at the selected revision. The existing workers
wait for their API and registration dependencies. Matte also waits for the
offline render split. Keep the predecessor listener ConfigMap and its
explicit Flux prune exemption through the next pin. After verified turnover
on that pin, remove the exemption while Flux still owns the object, then
remove its declaration in the pin after next.

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

`release.yaml` still names the pre-fix candidate until the coordinator
repins. The lock order and timings below apply to the corrected 0013 in
the next published Studio image. The current candidate fails the release
guard and is held from deployment. [Studio migration fix](https://github.com/thaynes43/haynes-studio/pull/146)

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
The rollout keeps `maxUnavailable: 0` on the API. The reconciliation below
accelerates the automatic inventory and migration reconciliation.

```bash
if ! bash scripts/studio-release/op967.sh migration; then
  printf 'FAIL migration gate, hold rollout follow-up\n' >&2
  exit 1
fi
printf 'PASS migration gate\n'
```

The migration Job verifies the TLS login before upgrading and reports its
committed head. Each migration and registration read identifies the current
Job UID, requires exactly one succeeded pod owned by that Job and reads the
last line starting with `{` from its successful container. For a registration
the last JSON result identifies the worker and the earlier matching audit
line confirms its status. An older failed pod is normal after a retry. A
Complete Job with zero or multiple succeeded pods fails the audit.

### Admit a terminal migration retry

After retries are exhausted, keep submissions closed. The old API and queue
workloads can still be serving while the offline render worker has already
turned over to PyAV 18.1.1 and Flux has pruned the predecessor migration Job.
Preserve the first admission's receipt, baseline and archives. On an Ops
branch based on `main`, increment the migration Job suffix to `-r1` or the
next `-rN` and change the matching `studio-migration` Flux health check to
that exact name. This path retries the same reviewed image and Job spec. A
code or config change needs a separately reviewed recovery plan.

Run the retry preflight on the clean, reviewed retry PR head before merging.
Set `STUDIO_OP967_FAILED_OPS_REVISION` to the exact merged revision that
created the terminal Job and `STUDIO_OP967_PREVIOUS_RECEIPT` to the admission
receipt for that merge. Set `STUDIO_OP967_RECEIPT` and `STUDIO_OP967_BASELINE`
to new private paths for the retry.

```bash
if ! bash scripts/studio-release/op967.sh retry-preflight; then
  printf 'FAIL retry admission, hold the merge\n' >&2
  exit 1
fi
printf 'PASS retry admission\n'
```

The check requires the original admission and hashes for the predecessor
Job logs and baseline. It reads the terminal fourth-pin Job and requires
Failed, no Complete condition and no active pod. It refuses changes to
the admitted Studio workload or Job spec apart from the Job name and matching
health check. It checks the predecessor workload images and both old API
endpoints, no matte Deployment, the 0012 catalog constraints and no 0013
release column or matte FIFO. It compares the live database and store
snapshot with the saved baseline. Either the completed idle 18.1.0 render
environment or the completed idle 18.1.1 environment under its forwarding
init is accepted. The ordinary dependency, capacity, queue and LAN checks
still apply. A missing or changed archive, a different schema, a workload
already on the new image, active render or unsupported environment refuses
the retry. The original Job logs are read from the archives and the retry
does no live lookup of the pruned predecessor Jobs.

Use `admit-merge` with the passing `retry` receipt before merging within
60 minutes of its start. After merge, select the retry's merge SHA and use
its receipt and baseline through migration, roll and postflight. The
first receipt remains the evidence for the retry preflight.

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
Under provisional coordinator ruling CR-59, the matting sidecar shares the
matte worker's pod network namespace. NetworkPolicy applies to both
containers. The release guard unions matching policies from the
recursively rendered Flux root, including siblings of `cluster-apps`,
and checks clusterwide policy definitions in the repository. An omitted
or empty Kubernetes `policyTypes` list defaults to ingress and also egress
when egress rules exist. CI runs the guard for changes anywhere under
`kubernetes/`, including the Flux roots, app paths and shared bases. It requires pod egress limited to DNS and the API's port-8002
listener, no allowed pod ingress, and the reviewed API ingress. It refuses
matching Cilium allow or deny rules on the checked directions. The worker
token is mounted read-only in the worker container only. The worker's
environment is local to its process, with separate PID and mount namespaces
and no token on shared storage. The
sidecar runs as UID 10001 with a read-only root filesystem and no
service-account token. All six exact POST routes on 8002 require a worker
token. The listener clears forwarded headers. The release guard checks the
router and token requirements in the pinned source, and postflight checks
that every allowed route refuses an anonymous request.

```bash
if ! bash scripts/studio-release/op967.sh roll; then
  printf 'FAIL rollout and grant gates\n' >&2
  exit 1
fi
printf 'PASS rollout and grant gates\n'
```

The three existing registrations must return audit status 200. The matte
grant's first registration returns 201. Status 200 is the matching-token
no-op on a retry. An unexpected conflict stops the rollout for review.

## Postflight

Run from the selected merge with its receipt and baseline. Postflight
reads the Flux stages, the migration and all four registration Jobs, and
checks the workload images, endpoints, installed policies, encoder
versions, LAN status, queue and data snapshot. It requires both selected
API endpoints, four grants, idle queues and unchanged archived data.
Installed policy selection uses the actual pod labels and each pod's
UID-bound Cilium endpoint identity. After these gates pass, the script
runs the listener and lane probes with deliberately invalid requests.
The idle matte claim runs last after all four route groups pass.

```bash
if ! bash scripts/studio-release/op967.sh postflight; then
  printf 'FAIL release verification, keep submissions closed\n' >&2
  exit 1
fi
printf 'PASS release verification\n'
```

Use `verify` at any time for the selected merge. It reads every Flux
stage, all five Job audits, the workload, endpoint, policy, encoder, LAN
and data status. If the original baseline is available, set
`STUDIO_OP967_BASELINE` to compare snapshots. Once the live state passes
the checks, verification also runs the read-only listener and lane POST
probes. The idle claim remains part of postflight.

```bash
if ! bash scripts/studio-release/op967.sh verify; then
  printf 'FAIL read-only release verification\n' >&2
  exit 1
fi
printf 'PASS read-only release verification\n'
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
matte jobs. Keep the reviewed fourth-pin checkout for the data probes and
its merge SHA in `MERGED_OPS_REVISION` with the original release receipt
and baseline. Set new private `STUDIO_OP967_ROLLBACK_RECEIPT` and
`STUDIO_OP967_ROLLBACK_BASELINE` paths. Capture the rollback snapshot
before merging the reviewed workload rollback PR. The script checks the
original admission and the live release sequence. The baseline selects
a Ready non-terminating API pod on either verified image.
It prefers the predecessor when the new pods have yet to become Ready and
checks the committed schema separately using the matte lane and release
column.

```bash
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

For a full offline rollback, keep the forwarding init and the new startup
logic, restore 20 CPUs and supply the byte-identical
`requirements-op964.txt` contents as the active `requirements.txt` alongside
the init. The pin keeps completed venvs on NFS by Python version and
requirements hash. The old venv is reused and its PyAV import verified
before readiness. A missing or incomplete environment is rebuilt at that
same key while the fourth-pin venv stays available.

On 2026-11-25 the exact yanked wheel for the pinned Python 3.12,
Debian Bookworm amd64 image downloaded successfully from PyPI. It is
`av-18.1.0-cp311-abi3-manylinux_2_28_x86_64.whl`, compatible with 3.12
through the stable ABI, with SHA-256
`8a032e8d8ebc73dec079364b9b4a6837638a2d106e8472314e685ffbf163e700`.
If the old venv is absent and the wheel becomes unavailable, retain the
fourth-pin offline workload and close the full offline rollback gate.
The API and queue workload rollback can still proceed. [PyPI wheel
details](https://pypi.org/project/av/18.1.0/#av-18.1.0-cp311-abi3-manylinux_2_28_x86_64.whl)

After merging, fetch Ops `main` and set `ROLLBACK_OPS_REVISION` to the
exact rollback merge SHA while keeping the fourth-pin checkout. Compare
uses both receipts, checks the Flux stages are Ready at the rollback
revision or a matching descendant, and compares the same snapshots.
Completion has no wall-clock deadline.

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
