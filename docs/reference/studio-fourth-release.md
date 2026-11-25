# OP967 fourth Studio release

**Expected.** This source pins matte jobs, the matte lane, the matting
companion and PyAV 18.1.1. The coordinator owns installation and postflight.
The installed starting point is the [third
pin](studio-deployment.md#op964-third-release), Studio `80975a9`, with
Alembic `0012_release_constraints`. [Story 967](https://work.haynesnetwork.com/work_packages/967)

The [release record](../../kubernetes/main/apps/ai/studio/release.yaml) pins
the amd64 API and web/listener images from Studio `470a90af`. The guard checks
their OCI revisions, the reviewed 0013 migration, listener authentication,
Job names and health gates, and the offline lock export.
[Studio images run 45692717257](https://github.com/thaynes43/haynes-studio/actions/runs/45692717257)
· [Studio migration fix](https://github.com/thaynes43/haynes-studio/pull/146)

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

Preflight checks the old workloads, three Ready PostgreSQL instances, the
matting profile, installed policy, LAN and worker dependencies. It requires
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
| AI inventory | Adds matte policy, worker and registration stages. Updates render, migration and the existing registration gates. | |
| `studio-render` | Adds the fourth-pin render ConfigMap and updates the HelmRelease. The offline render pod restarts once at 17 CPUs. The installed and composed paths render the same target. | |
| `studio-migration` | Creates the digest-named 0013 migration Job. | Third-pin migration Job and pod logs. |
| `studio` | Rolls API replicas, their worker listeners, web and MCP. Updates API ingress and adds the new listener ConfigMap. The old listener ConfigMap stays available for predecessor pod replacements during a stalled surge. | |
| `studio-worker-registration` | Creates the new run registration Job. | Third-pin run registration Job and pod logs. |
| `studio-worker` | Replaces the run worker pod. | |
| `studio-media-worker-registration` | Creates the new voice and image registration Jobs and the independent matte token projection. | Third-pin voice and image registration Jobs and pod logs. |
| `studio-media-workers` | Replaces the voice and image worker pods. | |
| `studio-matte-policy` | Applies the matte NetworkPolicy before the worker stage can run. | |
| `studio-matte-worker` | Adds the Deployment, ConfigMap and ServiceAccount after the policy stage is Ready. | |
| `studio-matte-worker-registration` | Creates the matte registration Job after the exact-profile sidecar is Ready. | |

Offline render and migration can start in parallel. API, web and MCP wait
for the successful migration at the selected revision. The existing workers
wait for their API and registration dependencies. Matte also waits for the
offline render split and its NetworkPolicy stage at the selected revision.
Keep the predecessor listener ConfigMap and its
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
fresh hashes. It requires Ready third-pin workloads and endpoints and
the absence of the matte Deployment and pods. Idle render may already
be on PyAV 18.1.1. All other preflight checks apply.

- **Complete 0012.** The primary database reports exactly
  `0012_release_constraints`, with both validated 0012 foreign keys and
  without the 0013 release column, matte FIFO or widened checks. Use the
  replacement Job above to apply 0013 and read it back.
- **Committed 0013.** The primary reports exactly `0013_matte_jobs`, with
  the release column, matte FIFO, widened checks and both validated 0012
  foreign keys.
  Use the same replacement Job. Its upgrade is a no-op and its read-back
  supplies the successful Job audit. A workload revert is also available
  through the procedure below.

The catalogue probe reads the CNPG primary over its local peer socket
in a read-only transaction. Any other catalogue state fails preflight.
After the retry PR merges, select its exact merge and use `wait` and
`postflight` with the retry baseline.

## Workload turnover

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

The matte pod waits for the selected API, the Helm-ready offline CPU split,
its NetworkPolicy and token projection resources. External Secrets projects a stable,
independent generated token. The sidecar's readiness probe requires its
exact profile hash. The matte container keeps its progress marker fresh
while probing for its grant with an extra-field claim body that can't take
work. Flux observes the healthy Deployment before applying the
digest-named registration Job, which mounts the same generated token and
uses the migration identity to register `studio-matte-worker` for `matte`.
After it gets the expected validation refusal, the wrapper replaces
itself with `studio-worker --lane matte`. The worker also verifies the
sidecar before claims. Its progress liveness allowance is 300 seconds.
Under provisional coordinator ruling **CR-59**, NetworkPolicy covers both
containers in the matte pod. The offline guard checks repository-rendered
policies and cannot see every policy an installed Helm release generates.
The full-render policy job is advisory across cluster paths. The Studio
release guard is still required for Studio paths.

The authoritative checks list every installed NetworkPolicy,
CiliumNetworkPolicy and CiliumClusterwideNetworkPolicy. Preflight uses
the rendered matte and API pod labels, live namespace and Cilium identity
inputs, and substitutes the reviewed matte policy and API ingress for
their installed versions.
Postflight repeats the check with the real pod labels and the UID-bound
Cilium identities of the matte and API pods. The policy's creation
timestamp must precede the matte pod's start time. The matte pod must allow
only DNS and the API's TCP 8002 listener for egress and no pod ingress.
The audit refuses selecting Cilium allow or deny rules on the checked
directions and checks the reviewed API ingress after rollout.
[Cilium namespace selectors](https://docs.cilium.io/en/stable/security/policy/kubernetes/)

The worker token is mounted read-only only in the worker container. The
sidecar has its own PID and mount namespaces, runs as UID 10001 with a
read-only root filesystem and has no service-account token. The listener
clears forwarded headers. All six POST routes require a worker bearer and
postflight checks that anonymous requests are refused.

The three existing registrations must return audit status 200. The matte
grant's first registration returns 201. Status 200 is the matching-token
no-op on a retry. An unexpected conflict stops the rollout for review.

## Postflight

Run from the selected merge with `STUDIO_OP967_BASELINE` set. The script
reads every Flux stage, the migration and four registration Job audits,
workloads, endpoints, installed policies, sidecar, engines, encoders,
LAN and the data snapshot. It requires four grants, idle queues and
unchanged archives. It rechecks Flux before the listener and lane POST
probes, which use bodies that fail validation or authorization. After
the four route groups pass, it rechecks the matte queue and sends a
matte claim with an extra field that must return 422 before any claim.
Keep submissions closed through verification.

```bash
if ! bash scripts/studio-release/op967.sh postflight; then
  printf 'FAIL release verification, keep submissions closed\n' >&2
  exit 1
fi
```

Use `verify` for the same audits and status probes with an optional baseline
and without the final queue and claim-validation pair.

```bash
if ! bash scripts/studio-release/op967.sh verify; then
  printf 'FAIL release verification\n' >&2
  exit 1
fi
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
| Own-lane claim with an extra field after the queue read | — | — | — | 422 |

Media traffic on port 8002 gets a listener refusal on excluded run routes.
The remaining cross-lane refusals require the API's `work.lane_forbidden`
error. Malformed release for the matte token reaches API validation.
A GET and a trailing slash for release are refused at the listener.
The API validation order was independently exercised on Studio PR 139.
[Source review](https://github.com/thaynes43/haynes-studio/pull/139#issuecomment-7165445269)

**Expected.** A disposable cutout and fenced release proof need an existing
permitted still and a separate supervised test. The store held no suitable
still at pickup.

Record the script's engine reachability results and the Gatus result before
opening submissions. A real cutout and a person's review are separate
evidence. [Gatus check](studio-deployment.md#gatus-and-backup-coverage)

## Roll back workloads through a reviewed PR

Keep submissions closed until active and held work settles. The snapshot
allows existing queued matte jobs only with a recorded matte pause. The
rollback preserves matte records, grants, the migration Job and
schema 0013. Old migration images cannot resolve that revision.

Base a workloads-only revert on current Ops `main`. Under
`kubernetes/main/apps/ai/studio/`, make these edits using the verified
third-pin manifests as the workload source.

- Restore the third-pin `app/api.yaml`, `app/mcp.yaml`, `app/web.yaml`,
  `app/networkpolicy.yaml`, `worker/deployment.yaml` and
  `media-workers/{voice,image}.yaml`. Point the restored API listener
  volume at the explicit `studio-media-worker-routes-cbf48cht27` ConfigMap
  in `app/media-worker-routes-op964.yaml`. Keep its prune exemption and
  the fourth-pin listener generator while both generations turn over.
- Restore `worker-registration/job.yaml` and
  `media-worker-registration/{voice,image}-job.yaml`. Restore the three
  Job health-check names in `ks.yaml`, retaining the render dependency
  and Helm health check. Remove the `studio-matte-worker` and
  `studio-matte-worker-registration` Kustomizations from `ks.yaml`.
  Keep the `studio-matte-policy` Kustomization and its NetworkPolicy.
- Retain `migration/job.yaml`, its `studio-migration` Kustomization,
  `release.yaml` as the schema pin, and the matte
  token projection and weights allocation. Keep offline render at 17
  CPUs through matte removal. Update the release guard in the revert to
  assert the third-pin workload pair with the fourth-pin migration.
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
engine reachability, matte removal and retained schema and data.

```bash
if ! bash scripts/studio-release/op967.sh compare; then
  printf 'FAIL rollback read-back, keep submissions closed\n' >&2
  exit 1
fi
```

Record the Gatus result before reopening the original lanes. A forward
fix is needed to resume matte submissions and job detail.

A later reviewed PR removes the `studio-matte-policy` stage. Before
opening that PR, read the pods in namespace `ai` with selector
`app.kubernetes.io/name=studio-matte-worker` and confirm there are none,
including terminating pods. The rollback comparison checks the same
condition.

A separate reviewed change can restore the offline 20-CPU limit after
a passing rollback comparison and read-back that the matte pod has
left. Preflight that change in a quiet window by verifying the idle
17-CPU app, the completed old venv or exact wheel availability, and
the absence of any remaining matte pod. In `studio-render/app/`, set
20 CPUs in `helmrelease.yaml` and copy `requirements-op964.txt` over
`requirements.txt`. Align both live paths and the release guard.
Retain the forwarding init and keyed venv startup. Require PyAV
18.1.0 after automatic turnover.

On 2026-11-25 the exact yanked Python 3.12-compatible wheel downloaded
with SHA-256
`8a032e8d8ebc73dec079364b9b4a6837638a2d106e8472314e685ffbf163e700`.
If the completed venv is missing and the wheel is unavailable, retain
the 17-CPU offline pin. [PyPI wheel](https://pypi.org/project/av/18.1.0/#av-18.1.0-cp311-abi3-manylinux_2_28_x86_64.whl)
