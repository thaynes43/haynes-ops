# Studio LAN deployment

[OP434](https://work.haynesnetwork.com/work_packages/434) installed Studio in
`ai`. [OP956](https://work.haynesnetwork.com/work_packages/956) adds the queue worker
and its credential registration. [OP957](https://work.haynesnetwork.com/work_packages/957)
installed the `f30445f` app pin and imported One More Meeting and
The Winter Menu. Its scoped playback proof and key revocation are **Proven**
in OP957 comment 9914 and OP944 comment 9915. [OP959](https://work.haynesnetwork.com/work_packages/959)
pins the next app images and retires those one-shot stages. The OP959 rollout
and checks below are **Expected** until the coordinator records the results.

`https://studio.haynesops.com` uses the internal Traefik tier, the
`internal.haynesops` DNS target and the namespace's wildcard certificate.
Traefik sends `/v1/` and `/auth/` to the API and UI paths to nginx. The
probes, worker paths and MCP paths have no ingress route. A NetworkPolicy
allows internal Traefik pods to reach the API and web ClusterIP Services.
The auth route allows a burst of 30 requests and refills at 120 per minute per
observed peer address on each Traefik replica. The internal LoadBalancer
may combine clients behind a shared peer address.

MCP exposes `/mcp` and `/mcp/upload` on the same TCP listener at
`http://studio-mcp.ai.svc.cluster.local:8001`. The existing NetworkPolicy
allows port 8001 from `dev-agents` and Studio API pods in `ai`, subject to
caller egress policy. The internal Traefik route excludes the entire
`/mcp` path family and Traefik has no network access to MCP (CR-21).
MCP forwards requests to `http://studio-api.ai.svc.cluster.local:8000` using
the caller's agent key. Upload requires an active `assets:upload` grant.
[Studio MCP transport contract](https://github.com/thaynes43/haynes-studio/blob/393fe1d66070ac726739b4cdb001a9e6b2c467db/docs/reference/studio-mcp.md)

The API, MCP and static web each run two replicas, with required hostname
anti-affinity and a PodDisruptionBudget of one available replica. Updates
allow one surge pod and zero unavailable replicas. Plan for a third
eligible node during a rollout. The migration Job has its own account
and Secret reference. API pods use the runtime DB and OIDC projections.
Reloader handles changes to those two projections with the Deployment's
rolling strategy. A completed Job remains complete after password
rotation. A new Job pod reads the current migration projection.

The product store is `gasha01.haynesnetwork:/hdd-nfs-repl/data/studio/api-store-v1`,
mounted at `/studio` read-write on the API and read-only on the worker.
The API runs as `1000:1000`, the existing NFS writer's IDs. Its startup command checks
exclusive create, fsync, hard-link no-clobber and read-back under
`.probe/` before serving. Kubernetes uses `/healthz` for process
liveness. The readiness probe tolerates the first `/readyz` response
of 503 while the store check runs. It also checks PostgreSQL. Web probes
read `/index.html`. MCP probes check its TCP listener. The
[LAN route check](#gatus-and-backup-coverage) runs from Gatus.

The worker runs one `studio-worker` replica on `talosw01` with `Recreate`
and the `dev-env-agent` priority class. It shares the API's UID and GID.
Both processes mount `data/studio/build-stage-v1` from the same export at
`/build-stage`, read-write, as `STUDIO_BUILD_STAGE_ROOT`. The worker builds
there and the API validates and publishes the resulting delivery. Both use the
same `STUDIO_BUILD_REVISION=git:<commit>`. The worker records the API image
digest as `STUDIO_BUILD_IMAGE_DIGEST`. The API and worker load the model
licence register from their image's installed Python package. The API
inherits `/opt/studio` from the image's `WORKDIR`.

The immutable `studio-model-licences-1aa9b4736da9` ConfigMap is exempt from
pruning and falls out of every Flux inventory at OP959. Keep it through the
rollback window. Once `f30445f` is retired as a rollback candidate and no
pod or workload mounts the ConfigMap, remove it in a follow-up GitOps cleanup.
Render the identical immutable ConfigMap without its prune exemption in
a cleanup Kustomization with pruning enabled. Confirm Flux owns the exact
ConfigMap again, its live prune exemption is gone and the inventory contains
only that ConfigMap. Then retire the cleanup Kustomization and verify the
ConfigMap is gone. Recover the original contents from
[OP957's merged tree](https://github.com/thaynes43/haynes-ops/tree/c20a53a4925d6322895943bb2994c294357296b9/kubernetes/main/apps/ai/studio/app).

The worker records main-thread progress under `/run/studio` on a pod-local
`emptyDir`. Startup waits for the marker. Liveness checks its age every 30
seconds and restarts the container after three consecutive failures. The
1,500-second age budget gives a 20-minute Wan clip five minutes of margin.
Wan polling refreshes the marker, as do claim polls and build
steps. The probe works without network calls. After a liveness kill, the
90-second claim expires. The replacement reclaims the FIFO head with a new
fence or keeps it held pending reconciliation. It reconciles by the recorded
Wan job ID and may re-send the same ID and body at most once per claim if
Wan reports the ID absent after an ambiguous submission. Wan deduplicates
that request, so the pending attempt causes no second engine submission.
[Progress probe](https://github.com/thaynes43/haynes-studio/blob/393fe1d66070ac726739b4cdb001a9e6b2c467db/docs/reference/media-jobs.md#worker-progress-probe)
[Claim recovery](https://github.com/thaynes43/haynes-studio/blob/393fe1d66070ac726739b4cdb001a9e6b2c467db/docs/reference/run-queue.md#claims-and-reconciliation)

Worker egress allows cluster DNS, the Studio API on TCP 8000, the Wan product
Service on TCP 8080 and Wyoming Whisper on TCP 10300. The API admits the
worker pod to port 8000. On that port the application enforces the worker-only
`/internal/v1/work/*` boundary. The pod has no inbound listener, service account
token or database credential. ESO generates the 64-character alphanumeric token
with `CreatedOnce`. The registration Job mounts it as a file and uses the
migration identity to register worker ID `studio-worker`. The worker uses the
same Secret key as an environment reference. Re-running registration with the
same value is idempotent.
[Studio queue configuration](https://github.com/thaynes43/haynes-studio/blob/393fe1d66070ac726739b4cdb001a9e6b2c467db/docs/reference/run-queue.md#worker-configuration)
[Token administration](https://github.com/thaynes43/haynes-studio/blob/393fe1d66070ac726739b4cdb001a9e6b2c467db/docs/reference/studio-api.md#worker-token-administration)

D47 excludes NFS media from backups. The NFS server is outside the
cluster and remains a shared dependency of all API replicas. PostgreSQL
uses the existing [CNPG backup coverage](studio-database.md#backup-and-rollback).

For the CPU matting release sequence and dormant sidecar, see the
[Studio matting deployment](studio-matting-deployment.md).

## Gatus and backup coverage

[OP435](https://work.haynesnetwork.com/work_packages/435) verified the Gatus
endpoint installed by OP434. The `ai/studio` Flux Kustomization renders
`studio-gatus-ep` from the shared `common/gatus/guarded` component. The Gatus
ConfigMap sidecar in `observability` watches the `gatus.io/enabled` label and
loads the endpoint into the `internal` group. Gatus requests
`https://studio.haynesops.com/v1/me` through internal Traefik once a minute.
It sends no credential. The expected anonymous response is **401**, within
the installed 200–401 success range. A timeout, too-many-requests response,
missing route or server error fails. The process and database/store probes
remain on the API pod. The Studio Deployments do not mount the Gatus
ConfigMap, so its installation or update requires no Studio pod rollout.

The Gatus ConfigMap reconciles with the app. Verify its configuration and
check the `internal/studio` result in Gatus. Use a result timestamp following
the reconcile and within two probe intervals of verification. The first
history entry can take one interval.

```bash
(
  set -euo pipefail
  kubectl -n ai get configmap studio-gatus-ep -o jsonpath='{.data.config\.yaml}'
)
```

OP435 saw passing Gatus checks at 01:59, 02:00 and 02:01 UTC on 2026-11-25.
It also observed 401 at `/v1/me`, 200 at `/` and 404 at `/healthz` from
the Gatus pod. It found direct NFS mounts and no Studio claim in `ai`.
VolSync has no Studio claim to snapshot.

## Initial resource settings

OP434 measured the API, MCP, web and migration images in a disposable smoke
using the packaged executables and PostgreSQL 16. The smoke sampled process
RSS and CPU with a small number of serial probes. The queue worker and
registration Job use provisional settings.

| Workload | Measured RSS | Mean CPU in smoke | CPU request | Memory request / limit |
| --- | ---: | ---: | ---: | ---: |
| API | 127.5 MiB | 1.5m | 100m | 160 MiB / 640 MiB |
| MCP | 63.1 MiB | 1.5m | 50m | 96 MiB / 320 MiB |
| Web | 9.4 MiB | below sampling resolution | 25m | 16 MiB / 32 MiB |
| Migration Job | 74.3 MiB | 1.14 CPU-seconds in 9.99 s | 100m | 96 MiB / 192 MiB |
| Registration Job | Pending | Pending | 50m | 64 MiB / 192 MiB |
| Queue worker | Pending | Pending | 250m | 2 GiB / 8 GiB |

The API and MCP memory limits include headroom for the maximum photo
upload measured on [Studio PR 102](https://github.com/thaynes43/haynes-studio/pull/102)
at 346 MiB in the API (640 MiB limit) and 173 MiB in MCP (320 MiB limit).
The measurements are process RSS from the local test. The `ai` namespace had no ResourceQuota at OP957 preflight. Its
`default-cpu-request` LimitRange set a default CPU request of 50m.

CR-22 divides D18's 24 vCPU limit into 20 for `studio-render` and 4 for
`studio-worker`. Both use the same low priority. The worker runs one build
at a time with two media threads. The Deployment serves the run lane. Its CPU
request matches the render pod's 250m scheduling share. At preflight the idle
render pod used 3m CPU and 16 MiB. The existing serial mix reported 195 MB
peak memory. The worker request and limit are provisional allowances for the Python process,
decoded frame buffers and media subprocesses. Sample worker CPU, memory
and restarts during the first authorized build.
[Studio mix measurements](https://github.com/thaynes43/haynes-studio/blob/c75086b85a3278ead1d42b7a26da3b70da8b5793/docs/reference/studio-mix.md)

### Voice and image queues before consumers are installed

OP959 opens the media-job and lane routes. A signed-in person who presses
**Generate voice** sees **Queued in voice lane** (or **Queued · voice lane
paused**). Voice and image jobs stay `queued` indefinitely at this pin. The
run worker posts an empty claim body and can claim only the run lane. There
is no media-job cancel route, purge or automatic retry. Tom can pause the
voice and image lanes from his signed-in session to hold future claims.
Only a person can change lane pauses. Paused lanes still accept new jobs.

[OP961](https://work.haynesnetwork.com/work_packages/961) adds separate
consumers with their own worker IDs, tokens, writable store mounts and Kokoro
or ComfyUI settings. The first consumer for each lane would take any stale
jobs in queue order and submit them to the shared image engine or voice
service. Repeat the [media queue count](#queued-media-jobs) before enabling
either consumer. If either count is nonzero, stop for backlog review.

## OP959 release

Use the digest pair in [release.yaml](../../kubernetes/main/apps/ai/studio/release.yaml).
Studio main commit `393fe1d66070ac726739b4cdb001a9e6b2c467db` contains
`dccfc9b` and was the newest successful **Studio images** publish at selection.
The API, MCP, worker, migration and worker-registration Jobs share the
Python digest. Web uses its matching nginx digest. Both tags, digests and
Linux/amd64 revision labels were verified anonymously.
[Studio images run](https://github.com/thaynes43/haynes-studio/actions/runs/45552195213)

`STUDIO_GO_LIVE_APPROVER_SUBJECTS` stays `[]` and the sign-in freshness
window defaults to 900 seconds. `STUDIO_ESCALATION_AUTHORIZATION=person`
leaves authorization with a signed-in person. API and MCP use the default
90-second upload body deadline and 15-second idle deadline. The existing
database, store, OIDC, staging and service settings supply the required
startup values for these entry points. Worker registration reuses the
registered worker token. The release provisions zero agent keys.

### Before reconciliation

This upgrade assumes the existing NFS store and build-stage directories.
For a fresh installation, prepare `api-store-v1` and `build-stage-v1` as
UID/GID 1000 on the Studio export before rolling the app. Check the existing
store mount and the database and OIDC prerequisites.

Run the checks before merging the GitOps change. Flux can reconcile as soon
as it sees the merged commit. Ask Studio users to keep Studio idle until
postflight completes. The previous pin has no queue-pause routes, so check
the queue again immediately before merge. Require ready database and OIDC
projections, all three database instances ready, the worker idle and at
least three nodes eligible for the rolling app replicas. Use the normal
[database readiness checks](studio-database.md#reconciliation-and-owner-bootstrap).
Confirm OP957's successful proof and key revocations before retiring the stages.

```bash
(
  set -euo pipefail
  flux get kustomization cloudnative-pg-cluster -n database
  kubectl -n database get cluster.postgresql.cnpg.io postgres16 \
    -o 'custom-columns=NAME:.metadata.name,DESIRED:.spec.instances,READY:.status.readyInstances'
  for stage in studio-database studio-oidc studio studio-worker; do
    kubectl -n ai wait --for=condition=Ready --timeout=1m "kustomization/$stage"
  done
  kubectl -n ai wait --for=condition=Ready --timeout=1m \
    externalsecret/studio-runtime-db externalsecret/studio-migrator-db \
    externalsecret/studio-oidc-client externalsecret/studio-worker-token \
    helmrelease/studio-render
  kubectl get nodes
  kubectl -n ai get ingressroute studio
  kubectl -n ai get svc studio-api studio-web studio-mcp
  kubectl -n ai get networkpolicy studio-api-ingress studio-web-ingress studio-mcp-ingress
  kubectl -n ai get job studio-revoke-playback-1aa9b4736da9 \
    -o 'custom-columns=JOB:.metadata.name,SUCCEEDED:.status.succeeded'
  kubectl -n ai logs deploy/studio-worker -c worker --since=2m | grep -c 'work/claim "HTTP/1.1 204'
)
```

Run this block with `phase=preflight` and keep the baseline file on the
coordinator's machine. After verifying the new pod pins, run it again with
`phase=postflight` and the same file. It requires an exact match for each
film's production, story, run, delivery and playback index counts and the
durable store's file count and total bytes. The store count excludes `.probe`.
Use `phase=compare` for the counts alone on a rollback, against a fresh
`preflight` baseline taken immediately before rollback at a separate path.

Every mode uses the API's runtime database connection in a read-only
transaction. Postflight also calls the pinned application's `_check_approval`
in preview mode, `_stored_snapshot`, and `drafts.get_draft` followed by
`Draft.summary`, which loads the canonical model with `Story.from_document`.
The probe needs no browser session or agent key and outputs only IDs, codes,
counts and hashes.
It exits unsuccessfully on any difference.

```bash
(
  set -euo pipefail
  phase=preflight
  baseline="${XDG_RUNTIME_DIR:?}/studio-op959-baseline.json"
  expected=
  if [[ $phase != preflight ]]; then
    expected=$(<"$baseline")
    test -n "$expected"
  fi
  report=$(kubectl -n ai exec -i deploy/studio-api -c api -- python -I -B - "$phase" "$expected" <<'PY'
import json
import os
import sys
from hashlib import sha256

from haynes_studio.db.engine import connect_engine, snapshot

films = (
    ("prd_01M7ZGYNHFET1GKR4304FVD4PE",
     "sty_01M7ZGYNHFET1GKR4304FVD4PF", "run_01M7ZHNZJ2PWA8XR8K7KKWP17V",
     "dlv_01M7ZHP1QS82QGK196ZPCM7MQS", True, 1),
    ("prd_01M79V9FC00000000000000001",
     "sty_01M79V9FC00000000000000001", "run_01M7C5CJA7XKBCRPM0ZRP2P5RS",
     "dlv_01M7C5D3KFX3MFVWCXBCVJHMG7", False, 0),
)


def check_import(conn, production, story, run, delivery, has_snapshot, expected_boards):
    from haynes_studio.api.approval_routes import _check_approval, _delivery
    from haynes_studio.api.errors import APIError
    from haynes_studio.api.run_routes import _stored_snapshot
    from haynes_studio.db import drafts, runs
    from haynes_studio.golive import records
    from haynes_studio.store.local import LocalStore

    row = runs.get(conn, run)
    assert (row["production_id"], row["story_id"]) == (production, story), run
    delivered = _delivery(conn, run)
    assert delivered is not None and delivered["id"] == delivery, (run, delivery)
    active = conn.scalar(records.active_id(run))
    _, refusal = _check_approval(row["document"], delivered, delivered["master"]["sha256"],
                                 True, active, preview=True)
    code = refusal.details["code"] if refusal is not None else None
    assert code == "approval.legacy_run", (run, code)
    print(run, code)

    try:
        response = _stored_snapshot(LocalStore(os.environ["STUDIO_STORE_ROOT"]), row)
    except APIError as error:
        code = error.details["code"]
        assert not has_snapshot and (error.status_code, code) == (404, "run.snapshot_missing"), (
            run, error.status_code, code)
        print(run, error.status_code, code)
    else:
        assert has_snapshot and response.status_code == 200, (run, response.status_code)
        digest = sha256(response.body).hexdigest()
        recorded = row["document"]["snapshot"]["sha256"]
        assert digest == recorded, (run, digest, recorded)
        print(run, response.status_code, digest)

    draft = drafts.get_draft(conn, story)
    assert all("created_by" not in board for board in draft.boards.values()), story
    summary = draft.summary(conn)
    assert (summary["id"], summary["production_id"]) == (story, production), story
    board_count = summary["derived"]["board_count"]
    assert board_count == len(draft.boards) == expected_boards, (story, board_count, len(draft.boards))
    print(story, "boards", board_count)


phase, expected = sys.argv[1:]
assert phase in {"preflight", "postflight", "compare"}, phase
if phase == "postflight":
    assert os.environ["STUDIO_BUILD_REVISION"] == "git:393fe1d66070ac726739b4cdb001a9e6b2c467db"
engine = connect_engine(os.environ["STUDIO_DATABASE_URL"], os.getenv("STUDIO_DATABASE_SCHEMA", "studio"))
imports = {}
with snapshot(engine) as conn:
    conn.exec_driver_sql("SET TRANSACTION READ ONLY")
    assert conn.exec_driver_sql("SELECT current_user, current_database(), ssl "
                               "FROM pg_stat_ssl WHERE pid = pg_backend_pid()").one() == (
                                   "studio_app", "studio", True)
    statuses = dict(conn.exec_driver_sql(
        "SELECT document->>'status', count(*) FROM studio.runs GROUP BY 1 ORDER BY 1"
    ).tuples().all())
    assert not {"queued", "running", "held"}.intersection(statuses), statuses
    for production, story, run, delivery, has_snapshot, expected_boards in films:
        counts = list(conn.exec_driver_sql("""
            SELECT (SELECT count(*) FROM studio.productions WHERE id = %s),
                   (SELECT count(*) FROM studio.stories WHERE id = %s),
                   (SELECT count(*) FROM studio.runs WHERE id = %s),
                   (SELECT count(*) FROM studio.deliveries WHERE id = %s AND run_id = %s),
                   (SELECT count(*) FROM studio.delivery_media WHERE run_id = %s)
        """, (production, story, run, delivery, run, run)).one())
        assert counts[:4] == [1, 1, 1, 1] and counts[4] >= 2, (run, counts)
        imports[run] = counts
        if phase == "postflight":
            check_import(conn, production, story, run, delivery, has_snapshot, expected_boards)


def fail_walk(error):
    raise error


store = os.environ["STUDIO_STORE_ROOT"]
files = size = 0
for directory, subdirs, names in os.walk(store, onerror=fail_walk):
    if directory == store and ".probe" in subdirs:
        subdirs.remove(".probe")
    for name in names:
        size += os.stat(os.path.join(directory, name), follow_symlinks=False).st_size
        files += 1
report = {"production/story/run/delivery/index counts": imports, "store files/bytes": [files, size]}
if phase != "preflight":
    baseline = json.loads(expected)
    assert report == baseline, {"expected": baseline, "actual": report}
    print("runs by status", statuses)
print(json.dumps(report, sort_keys=True))
PY
  )
  printf '%s\n' "$report"
  if [[ $phase == preflight ]]; then
    (umask 077; set -o noclobber; printf '%s\n' "$report" > "$baseline")
  fi
)
```

### Migration and application rollout

The source gate chain is `cluster-apps` → `studio-migration` → `studio`
and `studio-worker-registration` → `studio-worker`. Registration also
waits on the existing `studio-render` Kustomization and its HelmRelease
health. The final worker gate requires both the healthy app and registration.
All four Studio Kustomizations must be Ready at the same Git artifact revision.
Keep render and the database components at their current configuration.
The old app can keep serving if the migration fails. Investigate the Job
and use a forward fix. For a changed or exhausted Job at the same digest,
change its name and Flux health check together using the next `-rN` suffix.

The old API `f30445f` serves while the migration Job moves from
`0004_go_live_approvals` to `0007_escalation_packages`. Revision 0005 creates
the media job tables, lane events and promotions, and grants privileges to
the runtime role on the new tables. Revision 0006 adds the formats tables
and a nullable `stories.format_version_id` with a foreign key and a check
that permits NULL. Existing story rows and old inserts therefore pass.
The old code uses its declared columns and updates the story document,
so pre-existing drafts with no relational pin remain writable. Revision 0007
creates the three escalation tables and adds their runtime grants. The three
revisions leave the existing runtime grants, including `asset_versions`,
in place. The old API checks the store and its database connection at
readiness. It does not resolve the Alembic head. PostgreSQL still needs a
schema lock for the new `stories` column and constraint. Keep writes idle
and check the LAN during the migration.
[Studio migration source](https://github.com/thaynes43/haynes-studio/tree/393fe1d66070ac726739b4cdb001a9e6b2c467db/src/haynes_studio/db/migrations/versions)

From a LAN-capable terminal, record 200 at the root and 401 at `/v1/me`
before, during and after the migration. While the Job is active, record a
timestamp, the Job's status and Ready API pod names and image digests. Sample
early because a short migration can finish before the first observation.
Record any missed active window as such.

```bash
(
  set -euo pipefail
  date -u
  kubectl -n ai get job studio-migrate-4787d14f98a4 --ignore-not-found
  kubectl -n ai get pod -l app.kubernetes.io/name=studio-api \
    -o 'custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[0].ready,IMAGE:.status.containerStatuses[0].imageID'
  base=https://studio.haynesops.com
  status() { curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "$@"; }
  test "$(status "$base/")" = 200
  test "$(status "$base/v1/me")" = 401
  echo "LAN root=200 v1/me=401"
)
```

After the PR merges, reconcile from the coordinator session. The controllers
can start dependent stages automatically once their gates pass.

```bash
(
  set -euo pipefail
  suffix=4787d14f98a4
  flux reconcile kustomization cluster-apps -n flux-system --with-source
  flux reconcile kustomization studio-migration -n ai
  kubectl -n ai wait --for=condition=complete --timeout=5m "job/studio-migrate-$suffix"
  kubectl -n ai logs "job/studio-migrate-$suffix" -c migrate
  flux reconcile kustomization studio -n ai
  for workload in api mcp web; do
    kubectl -n ai rollout status "deploy/studio-$workload" --timeout=5m
  done
  flux reconcile kustomization studio-worker-registration -n ai
  kubectl -n ai wait --for=condition=complete --timeout=5m "job/studio-register-worker-$suffix"
  kubectl -n ai logs "job/studio-register-worker-$suffix" -c register
  flux reconcile kustomization studio-worker -n ai
  kubectl -n ai rollout status deploy/studio-worker --timeout=5m
  flux get kustomizations -n ai
)
```

Expect migration logs showing TLS login as `studio_migrator`, both HBA
rejections, all three revisions in order, and `Alembic revision:
0007_escalation_packages`. The runtime role has no grant on
`alembic_version`, so use the migration Job's head check. Expect an
unchanged result for the worker registration.

### Postflight

Verify pod readiness and pins against the release record. Expect two pods
of each app on distinct nodes and one worker on `talosw01`. The status
`imageID` may be qualified by the runtime. Verify its digest and the
configured image for each pod. Repeat the [baseline snapshot](#before-reconciliation).
Use `phase=postflight` to check the pinned read paths on both imports. Both
must return `approval.legacy_run` in the approval preview. The Winter
Menu's recovered run must return 404 `run.snapshot_missing`. One More Meeting
must return snapshot bytes that hash to its recorded SHA-256. Both draft
summaries must load. One More Meeting has one board without `created_by`.
The Winter Menu's recovered draft has zero boards.

```bash
(
  set -euo pipefail
  root=kubernetes/main/apps/ai/studio
  api="ghcr.io/thaynes43/haynes-studio@$(yq -e '.apiDigest' "$root/release.yaml")"
  web="ghcr.io/thaynes43/haynes-studio-web@$(yq -e '.webDigest' "$root/release.yaml")"
  for workload in api mcp web worker; do
    expected=$api
    replicas=2
    node=""
    if [[ $workload == web ]]; then expected=$web; fi
    if [[ $workload == worker ]]; then
      replicas=1
      node=talosw01
    fi
    kubectl -n ai get pod -l "app.kubernetes.io/name=studio-$workload" -o json |
      jq -er --arg image "$expected" --arg digest "${expected##*@}" \
        --arg node "$node" --argjson replicas "$replicas" '
        [.items[] | select(.metadata.deletionTimestamp == null)] |
        select(length == $replicas and
          ([.[].spec.nodeName] | unique | length) == $replicas and
          all(.[];
            ($node == "" or .spec.nodeName == $node) and
            .spec.containers[0].image == $image and .status.containerStatuses[0].ready and
            (.status.containerStatuses[0].imageID | endswith($digest)))) |
        .[] | [.metadata.name, .spec.nodeName, .spec.containers[0].image,
               .status.containerStatuses[0].imageID] | @tsv
      '
  done
  kubectl -n ai get pdb studio-api studio-mcp studio-web
  kubectl -n ai exec deploy/studio-worker -c worker -- studio-worker --check-progress 1500
  kubectl -n ai logs deploy/studio-worker -c worker --since=2m | grep -c 'work/claim "HTTP/1.1 204'
)
```

No Studio-built run on this installation has exercised the 1,500-second
liveness budget. Watch the worker's restart count throughout the first
authorized build. If the count increases, check the pod events for a
liveness kill and follow the [recorded Wan job ID](https://github.com/thaynes43/haynes-studio/blob/393fe1d66070ac726739b4cdb001a9e6b2c467db/docs/reference/run-queue.md#claims-and-reconciliation)
through recovery.

```bash
(
  set -euo pipefail
  kubectl -n ai get pods -l app.kubernetes.io/name=studio-worker --watch \
    -o 'custom-columns=POD:.metadata.name,RESTARTS:.status.containerStatuses[0].restartCount'
)
```

From an API pod, check the effective runtime grants. A table described as
insert-only below also permits reads. The guard checks both table-level and
column-level UPDATE grants, including inherited privileges. The only job
updates are to the mutable worker columns. In particular, the requester
stays fixed at insertion.

```bash
(
  set -euo pipefail
  kubectl -n ai exec -i deploy/studio-api -c api -- python - <<'PY'
import os

import psycopg

append_only = (
    "asset_versions", "asset_promotions", "queue_lane_events", "formats",
    "format_versions", "escalation_check_failures", "escalation_packages",
    "escalation_events", "go_live_approvals", "go_live_withdrawals",
)
mutable = {
    "media_job_lanes": {"next_position"},
    "media_jobs": {"document", "worker_id", "fence", "claim_until", "dispatched"},
    "stories": {"document", "format_version_id"},
}
with psycopg.connect(os.environ["STUDIO_DATABASE_URL"]) as conn:
    conn.read_only = True
    assert conn.execute("SELECT current_user").fetchone() == ("studio_app",)
    for table in (*append_only, *mutable):
        name = f"studio.{table}"
        privileges = conn.execute("""
            SELECT has_table_privilege(%s, 'SELECT'), has_table_privilege(%s, 'INSERT'),
                   has_table_privilege(%s, 'UPDATE'), has_table_privilege(%s, 'DELETE'),
                   has_table_privilege(%s, 'TRUNCATE')
        """, (name,) * 5).fetchone()
        assert privileges == (True, table != "media_job_lanes", False, False, False), (table, privileges)
        updates = {column for column, in conn.execute("""
            SELECT attname FROM pg_attribute
            WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped
              AND has_column_privilege(attrelid, attnum, 'UPDATE')
        """, (name,))}
        assert updates == mutable.get(table, set()), (table, updates)
        print(table, "SELECT", "INSERT" if privileges[1] else "",
              "UPDATE columns", sorted(updates), "DELETE/TRUNCATE denied")
PY
)
```

#### Queued media jobs

Expect zero queued jobs in both lanes at OP959 rollout. No person has
enqueued a job, and this installation provisions no agent key that can
enqueue one. Run the same check before enabling the OP961 consumers. Stop
for backlog review if either count is nonzero.

```bash
(
  set -euo pipefail
  kubectl -n ai exec -i deploy/studio-api -c api -- python -I -B - <<'PY'
import os

import psycopg

with psycopg.connect(os.environ["STUDIO_DATABASE_URL"]) as conn:
    conn.read_only = True
    counts = conn.execute("""
        SELECT lanes.lane, count(jobs.id)
        FROM studio.media_job_lanes AS lanes
        LEFT JOIN studio.media_jobs AS jobs
          ON jobs.lane = lanes.lane AND jobs.document->>'status' = 'queued'
        GROUP BY lanes.lane
        ORDER BY lanes.lane
    """).fetchall()
    for lane, count in counts:
        print(lane, count)
    assert counts == [("image", 0), ("voice", 0)], counts
PY
)
```

#### Route and playback checks

From the LAN, verify the new and existing anonymous API boundaries, including
both imported delivery proxies. These HTTP probes use no credentials, even
for the lane PUT. The route must refuse it before reading the body. Verify
that no MCP path is present on the ingress and test the cluster permits a
connection to MCP from an API pod but refuses anonymous requests.

```bash
(
  set -euo pipefail
  base=https://studio.haynesops.com
  status() { curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "$@"; }
  meeting=run_01M7ZHNZJ2PWA8XR8K7KKWP17V
  winter=run_01M7C5CJA7XKBCRPM0ZRP2P5RS
  meeting_proxy=63dbb1e26287360ed6c4a0422dbdbfa250ad981c74e9719133d4dfe72d809d1b
  winter_proxy=0212a7de9d6b7816c4c4d3c98bfd6d8e80fc112163944152e399b3e980a6748d
  for path in /v1/me /v1/productions /v1/formats /v1/jobs/sample /v1/boards/sample/jobs \
    /v1/queue/lanes "/v1/runs/$meeting/go-live-approvals" "/v1/runs/$meeting/snapshot" \
    /v1/productions/prd_01M7ZGYNHFET1GKR4304FVD4PE/escalations \
    "/v1/runs/$meeting/delivery" "/v1/runs/$winter/delivery" \
    "/v1/media/$meeting_proxy" "/v1/media/$winter_proxy"; do
    test "$(status "$base$path")" = 401
    printf '%s = 401\n' "$path"
  done
  test "$(status -X PUT "$base/v1/queue/lanes/run")" = 401
  for path in /healthz /readyz /mcp /mcp/upload /internal/v1/work/claim; do
    test "$(status "$base$path")" = 404
  done
  kubectl -n ai get ingressroute studio -o json |
    jq -e '[.spec.routes[].services[].name] | unique == ["studio-api", "studio-web"]' >/dev/null
)
```

```bash
(
  set -euo pipefail
  kubectl -n ai exec -i deploy/studio-api -c api -- python - <<'PY'
from urllib.error import HTTPError
from urllib.request import Request, urlopen

base = "http://studio-mcp.ai.svc.cluster.local:8001"
for path in ("/mcp", "/mcp/upload"):
    request = Request(base + path, data=b"{}", headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            raise AssertionError((path, response.status))
    except HTTPError as error:
        assert error.code == 401, (path, error.code)
        print(path, error.code)
PY
)
```

Check that the worker routes require a registered bearer in the
`worker_` namespace. Send deliberately malformed JSON so even the successful
authentication case cannot claim work. The valid bearer is built and used in
the worker pod from its existing projection. The probe prints only statuses.

```bash
(
  set -euo pipefail
  kubectl -n ai exec -i deploy/studio-worker -c worker -- python - <<'PY'
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from haynes_studio.queue.worker_tokens import worker_bearer

base = os.environ["STUDIO_API_URL"]
for label, token, expected in (
    ("anonymous", None, 401),
    ("wrong prefix", "0" * 64, 401),
    ("unregistered worker bearer", "worker_" + "0" * 64, 401),
    ("registered worker bearer, malformed body", worker_bearer(os.environ["STUDIO_WORKER_TOKEN"]), 422),
):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    request = Request(base + "/internal/v1/work/claim", data=b"{", headers=headers)
    try:
        with urlopen(request, timeout=5) as response:
            actual = response.status
    except HTTPError as error:
        actual = error.code
    assert actual == expected, (label, actual)
    print(label, actual)
PY
)
```

Use an existing signed-in browser on the LAN to confirm the queue lanes
read, the go-live review read and both imported proxy players. A signed-in
person should see the queue's three lanes and a 200 review response with
`approve.allowed=false` and `approve.code=approval.approver_not_configured`.
With the empty approver list, the review read gives that code for every
signed-in person, including on imported runs. The approver refusal takes
precedence over the run-level refusal. The in-pod preview establishes
`approval.legacy_run` for the imports.
Keep playback, phone playback and Tom's acceptance separate from the
anonymous response and integrity evidence.
[Go-live contract](https://github.com/thaynes43/haynes-studio/blob/393fe1d66070ac726739b4cdb001a9e6b2c467db/docs/reference/go-live.md)

### Pruned inventory

The live OP957 inventories before this change contained the following
objects in `ai`. The parent prunes the five Flux Kustomizations, and their
inventories prune each resource listed in the middle column. Kubernetes
garbage-collects their dependent Job pods and the two Secrets owned by the
deleted ExternalSecrets.
[Flux deletion policy](https://fluxcd.io/flux/components/kustomize/kustomizations/#deletion-policy)

| Kustomization | Flux-managed inventory to prune | Other dependent objects |
| --- | --- | --- |
| `studio-archive-access` | NetworkPolicies `studio-archive-db-jobs`, `studio-playback-proof` | |
| `studio-import-one-more-meeting` | Job `studio-import-one-more-meeting-1aa9b4736da9`, ConfigMap `studio-import-one-more-meeting-receipt-794tm4hc6d` | Job pod |
| `studio-import-winter-menu` | Job `studio-import-winter-menu-1aa9b4736da9`, ConfigMap `studio-import-winter-menu-receipt-4k96t9t4hf` | Job pod |
| `studio-playback-registration` | Job `studio-register-playback-1aa9b4736da9`. Password generators and ExternalSecrets `studio-playback-one-more-meeting-1aa9b4736da9`, `studio-playback-winter-menu-1aa9b4736da9` | Job pod and Secrets named after the ExternalSecrets |
| `studio-playback-proof` | Job `studio-verify-playback-1aa9b4736da9`, ConfigMap `studio-playback-proof-4mkt8tk52g` | Job pod |

The two old completed Job objects for migration and worker registration are
also pruned when their replacements are applied. The manually created
`studio-revoke-playback-1aa9b4736da9` Job sits outside Flux's inventory.
It has no proof key mount and remains as revocation evidence. The store is
a direct NFS mount and the retired inventories contain zero PVCs. Prune
exemptions retain the prior licence register and the worker token's
ExternalSecret and generator. Its unowned Secret continues to supply the
worker's environment and the registration Job's mounted token file. The
retired jobs have already populated their database rows and store blobs.
Pruning removes the transient resources.

```bash
(
  set -euo pipefail
  old=1aa9b4736da9
  for object in \
    kustomization/studio-archive-access kustomization/studio-import-one-more-meeting \
    kustomization/studio-import-winter-menu kustomization/studio-playback-registration \
    kustomization/studio-playback-proof networkpolicy/studio-archive-db-jobs \
    networkpolicy/studio-playback-proof configmap/studio-playback-proof-4mkt8tk52g \
    configmap/studio-import-one-more-meeting-receipt-794tm4hc6d \
    configmap/studio-import-winter-menu-receipt-4k96t9t4hf \
    "job/studio-import-one-more-meeting-$old" "job/studio-import-winter-menu-$old" \
    "job/studio-register-playback-$old" "job/studio-verify-playback-$old" \
    "job/studio-migrate-$old" "job/studio-register-worker-$old" \
    "externalsecret/studio-playback-one-more-meeting-$old" "externalsecret/studio-playback-winter-menu-$old" \
    "password.generators.external-secrets.io/studio-playback-one-more-meeting-$old" \
    "password.generators.external-secrets.io/studio-playback-winter-menu-$old"; do
    found=$(kubectl -n ai get "$object" --ignore-not-found -o name)
    test -z "$found"
  done
  kubectl -n ai get "configmap/studio-model-licences-$old" \
    externalsecret/studio-worker-token password.generators.external-secrets.io/studio-worker-token -o name
  kubectl -n ai get deploy studio-api studio-worker \
    -o 'custom-columns=NAME:.metadata.name,CLAIMS:.spec.template.spec.volumes[*].persistentVolumeClaim.claimName,NFS:.spec.template.spec.volumes[*].nfs.path'
)
```

Verify the two playback Secrets' absence with authorized metadata-only
access. Session RBAC excludes Secret reads. With only that session access,
record the two ExternalSecrets' disappearance, their published
`creationPolicy: Owner` and expected garbage collection, and mark direct
Secret absence unverified. The worker's authenticated probe confirms its
credential remains usable without reading any Secret value.

### Roll back while keeping the new schema

Stop new submissions and let existing runs settle before changing the build
revision. Require empty queues across all three lanes. Use the baseline's
run-status guard and the media queue count before rollback. The old build
lacks the lane-pause table and ignores pauses. If a queued OP959 run slips
through, the old worker claims it and fails prebuild with
`work.revision_mismatch`, before any engine submission. Voice and image jobs
have no consumer at this pin. A nonempty media queue requires a backlog
review before proceeding.

Revoke any provisioned escalation responder key before rollback. None is
provisioned here. The older API lacks the route confinement for that key.

A corrective GitOps PR can restore the API, MCP, worker and web
images from `f30445f`, along with their environment, build revision,
worker provenance digest, old register generator and mounts. Recover those
from [OP957's merged tree](https://github.com/thaynes43/haynes-ops/tree/c20a53a4925d6322895943bb2994c294357296b9/kubernetes/main/apps/ai/studio).
Remove the new worker probes with the worker rollback because that CLI lacks
`--check-progress`. Keep the OP959 migration and worker-registration Jobs
and their Flux gates, the existing worker token, the store and the inputs.
The corrective PR's release check must enforce the previous app image pair
and separately enforce the retained OP959 Job digest and names. Restoring
the whole OP957 deployment would reintroduce the retired stages and the
older migration Job, whose Alembic cannot resolve the new head.

The same additive schema reasoning allows `f30445f` to serve pre-upgrade
data and imported playback at `0007_escalation_packages`. Prefer fixing
forward after the new app begins writing. The older API validates the
whole story on each write and at enqueue. A single board stamped
`created_by` by the new build blocks every old-build write and enqueue on
that story.
An old edit of a story's JSON format pin cannot update the new relational
column and can fail its consistency check. Keep affected drafts and
recipe changes paused during a rollback. Leave the database at the new
head and keep the retained Jobs Complete. Reconcile the app and worker in
the same dependency order.
Repeat the baseline counts, ready pod and digest checks for the restored
images, the pre-existing LAN routes, and signed-in imported playback. Use
the retained migration Job's head evidence and rerun the runtime privilege
check. The new routes and liveness check apply only while OP959 is installed.

## OP961 voice and image workers

**Expected.** OP961 uses the installed OP959 image digests (Studio
`393fe1d`) and schema (`0007_escalation_packages`). The licence register
comes from the Python package in the image. Each media Deployment has one
replica with `Recreate`, the existing low priority class and a 1-vCPU
limit. Both are eligible off `talosw01` (CR-37). The engines are remote, and layered
compositing is one local still. The 20+4 vCPU budget on `talosw01` is
unchanged. Voice's provisional memory request/limit is 128/384 MiB. Image's
is 192 MiB/1 GiB. Record memory and restarts during the authorized jobs.

Both lanes run as the NFS writer UID/GID. They need the writable canonical
`/studio` store to read source blobs and publish results create-only. The
voice lane reads reusable takes and writes WAV takes and the assembled
track. The image lane reads reference and layer inputs and writes the PNG.
Neither uses build staging or a separate scratch directory. The only
other mount holds the main-thread progress marker. The 120-second voice
budget covers a 45-second Wyoming exchange plus local assembly, store
I/O and 15-second API calls. The 300-second image budget covers the
60-second ComfyUI HTTP inactivity timeout and gives reference preparation,
single-still compositing and store I/O room. The worker touches progress
before each queue and history request during long engine waits.
[Media worker source and progress contract](https://github.com/thaynes43/haynes-studio/blob/393fe1d66070ac726739b4cdb001a9e6b2c467db/docs/reference/media-jobs.md)

The API rolls both replicas with an nginx sidecar from the already pinned
web image. A ConfigMap supplies its configuration. Media NetworkPolicies
allow DNS, API port 8002 and each lane's engine: Kokoro Wyoming 10210 or
ComfyUI TCP 8188 on the `comfyui` pod. There is no engine ingress policy to
extend. API port 8002 accepts traffic from the two media Deployments. The
listener forwards only the five media-work POSTs to the API over loopback,
with no request retry or access logging. The API authenticates them.
Traefik, MCP and the run worker keep using 8000. The new workers have no
port 8000 grant. The dedicated listener enforces the HTTP path boundary
without putting worker bearer headers into Cilium HTTP flow logging.
[Cilium HTTP policy](https://docs.cilium.io/en/stable/security/policy/layer7/#http)
[Cilium header redaction settings](https://docs.cilium.io/en/stable/helm-values/#hubble-redact)

Each media lane has its own worker ID and ESO-generated token. Its
registration Job uses the migration projection and `--replace`. The
generator and ExternalSecret are prune-exempt. `CreatedOnce` plus
`creationPolicy: Orphan` keep the same value across reconciles and
ExternalSecret removal. A lost or revoked token cannot be fixed by
re-registering the old value. The API associates the bearer with a worker
ID and uses that ID in claim fences. It does not bind the ID to a lane.
A registered worker token can claim any lane on the shared claim route.
A source follow-up should bind explicit lane grants to worker identities,
migrate the run identity, and reject out-of-lane claims before queue access.
[Worker administration](https://github.com/thaynes43/haynes-studio/blob/393fe1d66070ac726739b4cdb001a9e6b2c467db/docs/reference/studio-api.md#worker-token-administration)

### Before enabling either consumer

Run the [read-only queued media jobs check](#queued-media-jobs) immediately before
merging this GitOps change. Flux can enable the consumers as soon as their
dependencies are ready. Require a successful check with both counts zero.
If either count is nonzero, stop for backlog review. The first consumer
could otherwise drain stale requests in queue order against ComfyUI or
Kokoro. Keep Studio users from submitting media jobs between the count
and the start of both consumers.

### Reconcile and check health

The coordinator runs this after the GitOps PR has merged. Registration
depends on the completed OP959 migration. The media worker stage depends
on both registrations and the API sidecar being ready in the app stage.
The existing run registration and worker stages have their own gates.

```bash
(
  set -uo pipefail
  failed=0
  check() {
    local label=$1
    shift
    if "$@"; then
      printf 'PASS %s\n' "$label"
    else
      printf 'FAIL %s\n' "$label" >&2
      failed=1
    fi
  }
  stop_on_failure() {
    if (( failed )); then
      printf 'FAIL OP961 rollout verification\n' >&2
      exit 1
    fi
  }

  root=kubernetes/main/apps/ai/studio
  read_digest() {
    local value
    value=$(yq -er "$1" "$root/release.yaml") &&
      [[ $value =~ ^sha256:[0-9a-f]{64}$ ]] &&
      printf -v "$2" '%s' "$value"
  }
  pinned() {
    kubectl -n ai get deploy "$1" -o json |
      jq -e --arg name "$2" --arg image "$expected" '
        any(.spec.template.spec.containers[]; .name == $name and .image == $image)'
  }
  listener_ready() {
    kubectl -n ai get pods -l app.kubernetes.io/name=studio-api -o json |
      jq -e --arg digest "$web_digest" '
        [.items[] | select(.metadata.deletionTimestamp == null)] |
        length == 2 and all(.[]; any(.status.containerStatuses[]?;
          .name == "media-worker-routes" and .ready and (.imageID | endswith($digest))))'
  }
  same_revision() {
    kubectl -n ai get kustomization studio-migration studio studio-media-worker-registration studio-media-workers \
      -o json | jq -e '
        [.items[].status.lastAppliedRevision] | select(length == 4) |
        unique | select(length == 1 and .[0] != null)'
  }
  worker_ready() {
    kubectl -n ai get pod -l "app.kubernetes.io/name=studio-$1-worker" -o json |
      jq -er --arg image "$expected" --arg digest "$digest" --arg lane "$1" '
        [.items[] | select(.metadata.deletionTimestamp == null)] | select(length == 1) | .[0] |
        select(.spec.nodeName != null and .spec.nodeName != "talosw01" and
          .spec.containers[0].args == ["--lane", $lane] and
          .spec.containers[0].image == $image and
          .status.containerStatuses[0].ready and
          (.status.containerStatuses[0].imageID | endswith($digest))) |
        [.metadata.name, .spec.nodeName, .status.containerStatuses[0].imageID,
          .status.containerStatuses[0].restartCount] | @tsv'
  }

  check 'release API digest' read_digest .apiDigest digest
  check 'release web digest' read_digest .webDigest web_digest
  stop_on_failure
  expected="ghcr.io/thaynes43/haynes-studio@$digest"
  check 'installed API pin' pinned studio-api api
  check 'installed run worker pin' pinned studio-worker worker
  check 'engine and migration readiness' kubectl -n ai wait --for=condition=Ready --timeout=1m \
    helmrelease/kokoro helmrelease/comfyui kustomization/studio-migration
  stop_on_failure
  check 'reconcile parent' flux reconcile kustomization cluster-apps -n flux-system --with-source
  stop_on_failure
  check 'reconcile Studio API' flux reconcile kustomization studio -n ai
  check 'API rollout' kubectl -n ai rollout status deploy/studio-api --timeout=5m
  check 'two ready API listeners at the web digest' listener_ready
  stop_on_failure
  check 'reconcile media registration' flux reconcile kustomization studio-media-worker-registration -n ai
  for lane in voice image; do
    check "$lane token projection" kubectl -n ai wait --for=condition=Ready --timeout=2m \
      "externalsecret/studio-$lane-worker-token"
    if job=$(yq -er 'select(.kind == "Job") | .metadata.name' "$root/media-worker-registration/$lane-job.yaml"); then
      printf 'PASS %s registration Job name (%s)\n' "$lane" "$job"
      check "$lane registration completion" kubectl -n ai wait --for=condition=complete --timeout=5m "job/$job"
      check "$lane registration logs" kubectl -n ai logs "job/$job" -c register
    else
      printf 'FAIL %s registration Job name\n' "$lane" >&2
      failed=1
    fi
  done
  stop_on_failure
  check 'reconcile media workers' flux reconcile kustomization studio-media-workers -n ai
  check 'Studio and media stage readiness' kubectl -n ai wait --for=condition=Ready --timeout=5m \
    kustomization/studio kustomization/studio-media-worker-registration kustomization/studio-media-workers
  check 'matching applied revisions' same_revision
  for lane in voice image; do
    check "$lane rollout" kubectl -n ai rollout status "deploy/studio-$lane-worker" --timeout=5m
    check "$lane pod placement, args and pin" worker_ready "$lane"
  done
  stop_on_failure
  printf 'PASS OP961 rollout verification\n'
)
```

In a quiet window expect at least one idle 204 from each consumer. Allow
two 5-second poll intervals after rollout on an empty queue. Record
progress and log counts together with the live launch arguments. For the
run worker the command has no lane argument, so the pinned client sends
the empty run claim. The two media clients send their configured lane
in the claim body. Logs record status and path only. They do not
record the body, so retain the launch argument check and the targeted
200s during the two real jobs as lane evidence.

```bash
(
  set -uo pipefail
  failed=0
  if kubectl -n ai get deploy studio-worker -o json | jq -e '
    .spec.template.spec.containers[0] |
    .command == ["studio-worker"] and ((.args // []) == [])'; then
    printf 'PASS run worker launch arguments\n'
  else
    printf 'FAIL run worker launch arguments\n' >&2
    failed=1
  fi
  for spec in worker:1500 voice-worker:120 image-worker:300; do
    worker="studio-${spec%:*}"
    if kubectl -n ai exec "deploy/$worker" -c worker -- studio-worker --check-progress "${spec#*:}"; then
      printf 'PASS %s progress\n' "$worker"
    else
      printf 'FAIL %s progress\n' "$worker" >&2
      failed=1
    fi
    if count=$(kubectl -n ai logs "deploy/$worker" -c worker --since=2m |
      awk '/\/work\/claim "HTTP\/1.1 204/ {n++} END {print n+0}') && (( count > 0 )); then
      printf 'PASS %s idle claims (%s)\n' "$worker" "$count"
    else
      printf 'FAIL %s idle claims (need at least one 204)\n' "$worker" >&2
      failed=1
    fi
  done
  if (( failed )); then
    printf 'FAIL worker progress and idle claims\n' >&2
    exit 1
  fi
  printf 'PASS worker progress and idle claims\n'
)
```

**Changed (2026-11-25): check that each worker can reach its own engine.**
The first install passed every check above while the image worker could
not connect to ComfyUI. Its egress rule selected the engine pod by
`statefulset.kubernetes.io/pod-name`, a label Cilium leaves out of a pod's
identity, so the rule never matched and every connection timed out. No
job had been requested, so nothing failed. The rule now selects by the
three application labels, the release check refuses a policy peer that
uses an identity-excluded label, and this block proves reachability
directly. The image check is the same read-only `GET /queue` the worker
makes before it submits. The voice check opens and closes one TCP
connection and sends nothing.

```bash
(
  set -uo pipefail
  failed=0
  if kubectl -n ai exec -i deploy/studio-voice-worker -c worker -- python -B - <<'PY'
import os
import socket

socket.create_connection((os.environ["STUDIO_TTS_HOST"], int(os.environ.get("STUDIO_TTS_PORT", "10210"))), timeout=5).close()
PY
  then
    printf 'PASS voice worker reaches its engine\n'
  else
    printf 'FAIL voice worker reaches its engine\n' >&2
    failed=1
  fi
  if kubectl -n ai exec -i deploy/studio-image-worker -c worker -- python -B - <<'PY'
import os

import httpx

response = httpx.get(os.environ["STUDIO_COMFYUI_URL"].rstrip("/") + "/queue", timeout=5, trust_env=False)
queue = response.json()
raise SystemExit(not (response.status_code == 200 and {"queue_running", "queue_pending"} <= queue.keys()))
PY
  then
    printf 'PASS image worker reaches its engine\n'
  else
    printf 'FAIL image worker reaches its engine\n' >&2
    failed=1
  fi
  if (( failed )); then
    printf 'FAIL media workers reach their engines\n' >&2
    exit 1
  fi
  printf 'PASS media workers reach their engines\n'
)
```

Verify each token in its own pod. Invalid JSON proves the credential passed
the internal auth check (422 after auth versus 401 without it) without
claiming or changing any work. `/v1/me` on the worker listener is refused
with 403. The bearer remains in the pod process and only statuses are printed.

```bash
(
  set -uo pipefail
  failed=0
  for lane in voice image; do
    if kubectl -n ai exec -i "deploy/studio-$lane-worker" -c worker -- python - <<'PY'
import os

import httpx

from haynes_studio.queue.worker_tokens import worker_bearer

def check(label, request, expected):
    try:
        status = request().status_code
    except httpx.HTTPError as error:
        print("FAIL", label, type(error).__name__)
        return False
    passed = status == expected
    print("PASS" if passed else "FAIL", label, status, "expected", expected)
    return passed

auth = {"Authorization": "Bearer " + worker_bearer(os.environ["STUDIO_WORKER_TOKEN"])}
results = []
with httpx.Client(base_url=os.environ["STUDIO_API_URL"], trust_env=False, timeout=5) as client:
    for operation in ("claim", "heartbeat", "publish", "complete", "fail"):
        path = "/internal/v1/work/" + operation
        headers = {"Content-Type": "application/json"}
        results.append(check(f"{path} anonymous", lambda: client.post(path, content=b"{", headers=headers), 401))
        results.append(check(
            f"{path} registered", lambda: client.post(path, content=b"{", headers={**headers, **auth}), 422,
        ))
    results.append(check("/v1/me restricted", lambda: client.get("/v1/me", headers=auth), 403))
raise SystemExit(0 if all(results) else 1)
PY
    then
      printf 'PASS %s worker token and routes\n' "$lane"
    else
      printf 'FAIL %s worker token and routes\n' "$lane" >&2
      failed=1
    fi
  done
  if (( failed )); then
    printf 'FAIL media worker token verification\n' >&2
    exit 1
  fi
  printf 'PASS media worker token verification\n'
)
```

### Two real jobs and people-only pause proof

**Expected, with Tom.** Pause and resume require his signed-in browser and
`X-Studio-CSRF`. The coordinator can capture pod evidence while Tom makes
these requests. The coordinator has no people's session or agent key, and
the pinned `studio-api-admin` can administer credentials and import verified
finished archives. It cannot create a draft production or request a job.
The migration role is for those operator commands only. A future approved
key could use `read`, `draft:write` and `assets:generate` on the single
eligible production with 60-minute expiry and revocation after the check.
Even that key could not pause the lane, create a production, or open an
unselected result's people-only media routes. Use Tom's session for this
proof.

Both imported stories are protected and their recorded asset versions lack
media. Keep their boards and selections intact. Use a fresh Short draft in
the existing production that owns the stock Kokoro voice. A read-only
catalog check before OP961 found one promoted `Kokoro-82M v1.0` voice
definition (`af_heart`) scoped to a production, with no story or board
scope. A stock definition supplies an engine voice ID and needs no
recorded audio. Confirm the definition still appears through the API.
Use no pinned photos, cast or set stills.

Tom can issue the API requests below in his signed-in Studio browser. This
helper is for a same-origin console at `https://studio.haynesops.com`.
Run one step at a time. Keep the session cookie in the browser and record
only statuses, IDs, timings and hashes.

```js
const studio = await (async () => {
  if (location.origin !== "https://studio.haynesops.com") throw Error("Open Studio first");
  const me = await fetch("/v1/me").then((response) => response.json());
  if (me.principal?.kind !== "human" || !me.csrf_token) throw Error("Sign in to Studio");
  return async (method, path, body, extra = {}, expected = 200) => {
    const headers = new Headers(extra);
    if (method !== "GET") headers.set("X-Studio-CSRF", me.csrf_token);
    if (body !== undefined) headers.set("Content-Type", "application/json");
    const response = await fetch(path, {method, headers, body: JSON.stringify(body)});
    const result = await response.json();
    if (response.status !== expected) throw Error(`${response.status} ${result.error?.code ?? ""}`);
    return result;
  };
})();
```

1. Read `GET /v1/assets?kind=voice` and choose the promoted Kokoro definition
   with `scope.production_id` set and both `scope.story_id` and
   `scope.board_id` empty. Use its production and version IDs. The production
   already has Short enabled. Create a new story and board through the browser:

   ```js
   const voices = await studio("GET", "/v1/assets?kind=voice");
   const stock = voices.items.find((v) => v.state === "promoted" &&
     v.scope.production_id && !v.scope.story_id && !v.scope.board_id &&
     v.attributes.voice_id === "af_heart" && v.provenance.generator?.model === "Kokoro-82M v1.0");
   if (!stock) throw Error("No eligible stock voice");
   const story = await studio("POST", `/v1/productions/${stock.scope.production_id}/media-types/short/stories`,
     {title: "OP961 media proof", narrator_voice_version_id: stock.id}, {}, 201);
   const board = await studio("POST", `/v1/stories/${story.id}/boards`, {
     lines: [{speaker: "narrator", text: "The tiny kettle is ready.", gap_before_ms: null}],
     method: {layer: "whole_frame", motion: "i2v"},
     photo: {current_version_id: null, prompt: "A tiny yellow kettle on a blue table",
       input_asset_version_ids: []},
   }, {"If-Match": String(story.revision)}, 201);
   ```

2. Voice first. `GET /v1/queue/lanes` must show the voice lane open before
   starting. Send `PUT /v1/queue/lanes/voice` with `{"paused":true}`. With the
   lane paused, send `POST /v1/boards/<board.id>/voice-jobs` with `{}` and a
   unique `Idempotency-Key`. Expect 202. Observe its job still `queued` after
   at least two idle poll intervals through `GET /v1/jobs/<id>` and worker
   204s. Send `PUT /v1/queue/lanes/voice` with `{"paused":false}`. Poll
   the same job to `succeeded` and check `result_is_current: true`. This
   board has one uncached line, so it requires one Kokoro take.

3. Image next. Begin with the image lane open and verify no other Studio
   image job is active. The coordinator observes ComfyUI with the GET-only
   probe below. If household work is present, let it finish and observe
   the worker's backoff if the two overlap. Do not create household load to
   force that condition. Tom pauses image with `PUT /v1/queue/lanes/image`
   and `{"paused":true}`. Send `POST /v1/boards/<board.id>/photo-jobs` with
   `{"seed":0}` and its own unique `Idempotency-Key`. Expect 202 while
   paused and confirm that job remains queued through two poll intervals.
   It is a whole-frame text-to-image request with no reference images.
   Read `GET /v1/boards/<board.id>` and use its revision as `If-Match`
   for `PATCH /v1/boards/<board.id>`. Send only `photo`, preserving its
   fields and changing `prompt` to `"A tiny yellow kettle on a green table"`.
   Resume image with `PUT /v1/queue/lanes/image` and `{"paused":false}`.
   Expect the single original job to succeed with `result_is_current: false`.
   Confirm the board's `photo.current_version_id` is still null. Keep the
   draft and both results as private evidence. Restore any lane paused
   by the procedure before ending the window, including on a failed check.

4. For each successful job, use `GET /v1/jobs/<id>/result`. Take the WAV
   hash from `voice.track.sha256` and the PNG hash from `content.sha256`.
   Fetch the corresponding `/v1/jobs/<id>/media/<sha256>` through
   Tom's browser. Check 200, `audio/wav` or `image/png`, and SHA-256 of
   the returned bytes. The same version is readable through
   `/v1/asset-versions/<version-id>/media/<sha256>` for a signed-in person.
   The generic media route is not the proof path for the unselected PNG.
   Keep file bytes, request bodies, cookie and CSRF out of evidence.
   Record Tom's Inspector/Composer review and acceptance separately.
   The pinned web client does not expose a photo-job button, so this
   procedure uses the authenticated API for the photo.

Use a fresh key for each of the two job requests. In a transport ambiguity
reuse that request's exact body and key to find the same job. Do not
submit an additional job to compensate for a failed inference.
[API requests, results and queue controls](https://github.com/thaynes43/haynes-studio/blob/393fe1d66070ac726739b4cdb001a9e6b2c467db/docs/reference/studio-api.md#media-operations)

### Shared engine evidence

The image worker checks both queue lists before starting and checks again
immediately before marking dispatch and submitting the prompt. A busy
engine causes a ten-second backoff, up to 30 minutes. An unreadable
queue stops that job. The last idle check and POST are separate requests,
so household work can arrive in between. Studio submits one prompt and
polls its specific history before it can move to another job. With an
uncertain dispatch it does not retry the prompt, interrupt, or clear any
queue entry. Image edits can upload an input before the second idle check.
Layered stills compose locally. For this proof the photo has no input upload.
[Queue ordering and busy/unknown fixtures](https://github.com/thaynes43/haynes-studio/blob/393fe1d66070ac726739b4cdb001a9e6b2c467db/tests/queue/test_media_services.py)

Before the image lane resumes, take this read-only sample through the image
pod's permitted engine connection. Repeat it during the wait only if
needed to observe existing household work.

```bash
(
  set -uo pipefail
  if kubectl -n ai exec -i deploy/studio-image-worker -c worker -- python - <<'PY'
import os
from datetime import UTC, datetime

import httpx

with httpx.Client(base_url=os.environ["STUDIO_COMFYUI_URL"], trust_env=False, timeout=5) as client:
    try:
        response = client.get("/queue")
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise SystemExit(f"FAIL GET /queue ({type(error).__name__})") from None
    print("PASS GET /queue", response.status_code)
    try:
        queue = response.json()
    except ValueError:
        raise SystemExit("FAIL /queue JSON") from None
    results = []
    for key in ("queue_running", "queue_pending"):
        entries = queue.get(key) if isinstance(queue, dict) else None
        passed = isinstance(entries, list)
        results.append(passed)
        print(datetime.now(UTC).isoformat(), "PASS" if passed else "FAIL", key,
              len(entries) if passed else "expected list")
raise SystemExit(0 if all(results) else 1)
PY
  then
    printf 'PASS ComfyUI queue sample\n'
  else
    printf 'FAIL ComfyUI queue sample\n' >&2
    exit 1
  fi
)
```

Capture a UTC timestamp just before the first job is enqueued. After both
finish, substitute that timestamp below. For the two isolated jobs the
expected successful claim counts are zero run, one voice, one image.
For image, read the final `GET /queue`, the successful API `POST /publish`,
the single `POST /prompt` and the resulting history fetch in order. Compare
any household busy interval with the completed job's `waited_s` and the
image engine job ID in its result provenance. If the household queue was
idle throughout, mark live busy backoff unobserved and link the source
fixture instead. Preserve only aggregate counts, statuses and the IDs
for this proof.

```bash
(
  set -uo pipefail
  failed=0
  since='<UTC RFC3339 start time>'
  for entry in worker:0 voice-worker:1 image-worker:1; do
    worker="studio-${entry%:*}"
    expected="${entry#*:}"
    if ! logs=$(kubectl -n ai logs "deploy/$worker" -c worker --since-time="$since" --timestamps); then
      printf 'FAIL %s log sample\n' "$worker" >&2
      failed=1
      continue
    fi
    printf 'PASS %s log sample\n' "$worker"
    if count=$(awk '/\/work\/claim "HTTP\/1.1 200/ {n++} END {print n+0}' <<< "$logs") &&
       [[ $count == "$expected" ]]; then
      printf 'PASS %s successful claims (%s)\n' "$worker" "$count"
    else
      printf 'FAIL %s successful claims (expected %s)\n' "$worker" "$expected" >&2
      failed=1
    fi
    if [[ $worker == studio-image-worker ]]; then
      if awk '/(GET .*\/(queue|history\/)|POST .*\/(work\/publish|prompt)) /' <<< "$logs" &&
         prompts=$(awk '/POST .*\/prompt "HTTP\/1.1/ {n++} END {print n+0}' <<< "$logs") &&
         [[ $prompts == 1 ]]; then
        printf 'PASS image worker prompt count (1)\n'
      else
        printf 'FAIL image worker prompt count (expected 1)\n' >&2
        failed=1
      fi
    fi
  done
  if (( failed )); then
    printf 'FAIL worker log verification\n' >&2
    exit 1
  fi
  printf 'PASS worker log verification\n'
)
```

### Rotation and rollback

For planned rotation, drain the lane in an idle maintenance window. Add a
new digest-independent, versioned token generator and Orphan ExternalSecret.
Use the new token reference in the lane Deployment and registration Job
and keep the same worker ID. Add `--replace` to the rotation Job's arguments.
Bump that Job's digest-suffixed name and Flux
health check to the next `-rN` (start at `-r1`). Reconcile registration
before the workers. The existing pod's old bearer is revoked as soon as
`--replace` commits. Expect a brief interruption until the new pod starts.
Verify internal 422 and idle 204 with the replacement before resuming. For
a retry with the same secret, use a fresh Job suffix and leave the
credential references alone. An already revoked digest needs a fresh token.

For rollback, drain both lanes where possible. Revert just the OP961 source
through GitOps and keep OP959's app pin. Flux prunes the media worker
Deployments, their policies and Jobs. The API rolls back to its 8000-only
pod template. Keep both tokens registered and their prune-exempt
projections so reinstalling adopts the same tokens. If a token is ever
compromised, revoke that ID with the migration role and rotate before
reinstalling. Completed or queued jobs, catalog entries and result blobs
remain in place. A paused lane can be left paused while the workers are
absent.

```bash
(
  set -uo pipefail
  failed=0
  check() {
    local label=$1
    shift
    if "$@"; then
      printf 'PASS %s\n' "$label"
    else
      printf 'FAIL %s\n' "$label" >&2
      failed=1
    fi
  }
  absent() {
    local found
    found=$(kubectl -n ai get "deployment/studio-$1-worker" --ignore-not-found -o name) &&
      [[ -z $found ]]
  }
  api_port() {
    kubectl -n ai get svc studio-api -o json | jq -e '[.spec.ports[].port] == [8000]'
  }
  check 'reconcile parent after revert' flux reconcile kustomization cluster-apps -n flux-system --with-source
  if (( failed == 0 )); then
    check 'reconcile Studio API' flux reconcile kustomization studio -n ai
    check 'API rollout' kubectl -n ai rollout status deploy/studio-api --timeout=5m
    check 'run worker rollout' kubectl -n ai rollout status deploy/studio-worker --timeout=5m
    for lane in voice image; do
      check "$lane Deployment pruned" absent "$lane"
      check "$lane token projection retained" kubectl -n ai wait --for=condition=Ready --timeout=1m \
        "externalsecret/studio-$lane-worker-token"
    done
    check 'API service exposes only port 8000' api_port
  fi
  if (( failed )); then
    printf 'FAIL OP961 rollback verification\n' >&2
    exit 1
  fi
  printf 'PASS OP961 rollback verification\n'
)
```

## OP964 third release

**Proven (2026-11-25, 11:14Z to 11:18Z):** this pin is installed. Evidence:
[OP964, comment 10061](https://work.haynesnetwork.com/work_packages/964).
**Changed:** the postflight probe at first asserted that The Winter Menu's run
document carries no snapshot reference. It does; only the stored snapshot is
absent. The assertion below is corrected, and the probe passed in full with it.
The anonymous ingress block was run from inside the cluster through the
internal ingress with the certificate verified, since the coordinator's pod has
no LAN route.

[OP964](https://work.haynesnetwork.com/work_packages/964) promotes feedback and
iterations, the reference-photo composer, private release packages, the
Assets page, the staging archive and worker lane grants. The coordinator
reviews, merges and rolls out the pin. All checks below are expected until
the coordinator records installed evidence.

Use [release.yaml](../../kubernetes/main/apps/ai/studio/release.yaml) for the
revision, digest pair and successful Studio images run ID. The Python digest
covers the API, MCP, three workers, migration and three registration Jobs.
The web digest covers the web pods and the API's media-worker listener.

The selected pair (Studio `80975a9`) includes Studio PRs 116, 118, 120, 121,
123, 125, 127, 128 and 131. Its Alembic head is `0012_release_constraints`.
OP437 is outside this pin. PR 128 is the matting adapter's source (OP451):
nothing calls it yet, and the matting sidecar is not part of this pin. The
coordinator repinned from `b182c1d` with the command below after PRs 131 and
128 merged. For a repin, select a successful main publish containing all nine
PRs. Run the following from haynes-ops with the selected
full SHA and publish run ID. It verifies both Linux/amd64 tags, immutable
manifests and revision labels anonymously, then updates the release record,
workload references and digest-named Job gates together. Update the recorded
PR ancestry and migration head. [Studio images](https://github.com/thaynes43/haynes-studio/actions/workflows/studio-images.yml)

```bash
uv run --no-project --with PyYAML==6.0.3 -- python -I -B scripts/tests/studio-release/check.py \
  --pin "$studio_revision" "$studio_publish_run"
```

**Drain on termination (added with this pin).** The API container, its
media-worker listener, MCP and web each sleep 8 seconds in `preStop` before they
receive the stop signal, inside the default 30-second grace period. A
terminating pod stays in the ingress's rotation for a moment; without the drain
the next request to it is a 502, on a pod delete and on every roll. The release
check requires the sleep on all four containers. The first roll to this pin
still terminates pods of the previous pin, which have no drain, so a brief 502
is possible during this roll only. Once this pin is installed, the
[one-app-pod recovery check](https://work.haynesnetwork.com/work_packages/437)
can require zero failed requests.

### Preflight

Before merging, arrange an idle window and keep submissions stopped until
postflight passes. Flux can pick up the merge before a manual reconcile.
Require all three postgres16 instances Ready, ready Studio stages and
existing projections, ready Kokoro and ComfyUI, and room for the extra API
pod on a separate eligible node. Check [database
readiness](studio-database.md#reconciliation-and-owner-bootstrap) and
[OP961 installed evidence](https://work.haynesnetwork.com/work_packages/961)
(comments 9993, 9997 and 10002). Review the current shared ComfyUI queue
using the [image worker](#shared-engine-evidence) and submit nothing.

```bash
(
  set -euo pipefail
  kubectl -n ai wait --for=condition=Ready --timeout=1m \
    kustomization/studio-database kustomization/studio-oidc kustomization/studio-migration \
    kustomization/studio kustomization/studio-worker-registration kustomization/studio-worker \
    kustomization/studio-media-worker-registration kustomization/studio-media-workers \
    helmrelease/studio-render helmrelease/kokoro helmrelease/comfyui \
    externalsecret/studio-runtime-db externalsecret/studio-migrator-db \
    externalsecret/studio-oidc-client externalsecret/studio-worker-token \
    externalsecret/studio-voice-worker-token externalsecret/studio-image-worker-token
  kubectl get nodes
  kubectl -n ai get pods -l app.kubernetes.io/name=studio-api -o wide
)
```

#### Imported productions and queue counts

Run this block in `preflight` on the old API and retain the baseline.
All modes call the running build's publication and approval filters and
check both imported story IDs. Run `postflight` only after
the pod check on the new pair. Use `compare` after an image rollback with a
fresh `preflight` baseline taken immediately before rollback. Choose a
different path for that baseline via `STUDIO_OP964_BASELINE`. Use the same
path for each preflight and its follow-up.

Run preflight immediately before merge. The probe counts queued and running
work in each of the run, voice and image lanes. A held run also stops the
roll for review. With `maxUnavailable: 0`, the old API serves while the new
API becomes Ready. Old API pods can authenticate existing worker tokens
without enforcing the lane grants until the last old pod is gone.

Every mode uses a read-only snapshot as `studio_app` over TLS. Postflight
calls the same database and validation helpers as the new read routes.
Both imports must have empty feedback, no active iteration, no frozen
release target or package, and an empty staging archive. Each is unpublished
and unapproved. Release eligibility must report `approval.legacy_run`.
A package list represents only prepared packages, so the legacy case has
no package row to display as held. Its eligibility refusal is the held
reason that the release view would use. The catalog must expose One More
Meeting's recorded-only versions by their frozen IDs. The recovered Winter
Menu run has no frozen catalog pins. Shared catalog entries can appear
for either production and are separate from those pins. The snapshot
and draft results follow [OP959](#postflight). See the Studio references
for [feedback](https://github.com/thaynes43/haynes-studio/blob/main/docs/reference/feedback.md),
[release](https://github.com/thaynes43/haynes-studio/blob/main/docs/reference/release-package.md)
and [archive](https://github.com/thaynes43/haynes-studio/blob/main/docs/reference/staging-archive.md).

```bash
(
  set -euo pipefail
  phase=${STUDIO_OP964_PHASE:-preflight}
  baseline=${STUDIO_OP964_BASELINE:-"${XDG_RUNTIME_DIR:?}/studio-op964-baseline.json"}
  revision=$(yq -er '.revision' kubernetes/main/apps/ai/studio/release.yaml)
  expected=
  if [[ $phase != preflight ]]; then
    expected=$(<"$baseline")
    test -n "$expected"
  fi
  report=$(kubectl -n ai exec -i deploy/studio-api -c api -- python -I -B - \
      "$phase" "$expected" "$revision" <<'PY'
import json
import os
import sys
from hashlib import sha256

from haynes_studio.db import drafts, runs
from haynes_studio.db.engine import connect_engine, snapshot


films = (
    ("One More Meeting", "prd_01M7ZGYNHFET1GKR4304FVD4PE",
     "sty_01M7ZGYNHFET1GKR4304FVD4PF", "run_01M7ZHNZJ2PWA8XR8K7KKWP17V",
     "dlv_01M7ZHP1QS82QGK196ZPCM7MQS", True, 1),
    ("The Winter Menu", "prd_01M79V9FC00000000000000001",
     "sty_01M79V9FC00000000000000001", "run_01M7C5CJA7XKBCRPM0ZRP2P5RS",
     "dlv_01M7C5D3KFX3MFVWCXBCVJHMG7", False, 0),
)


def check(label, operation, *args):
    try:
        result = operation(*args)
    except Exception as error:
        print(f"FAIL {label} ({type(error).__name__})", file=sys.stderr)
        return False
    print(f"PASS {label}: {result}", file=sys.stderr)
    return True


def queue_counts(conn):
    queued, running, held = conn.exec_driver_sql("""
        SELECT count(*) FILTER (WHERE document->>'status' = 'queued'),
               count(*) FILTER (WHERE document->>'status' = 'running'),
               count(*) FILTER (WHERE document->>'status' = 'held')
        FROM studio.runs
    """).one()
    media = conn.exec_driver_sql("""
        SELECT lanes.lane,
               count(jobs.id) FILTER (WHERE jobs.document->>'status' = 'queued'),
               count(jobs.id) FILTER (WHERE jobs.document->>'status' = 'running')
        FROM studio.media_job_lanes AS lanes
        LEFT JOIN studio.media_jobs AS jobs ON jobs.lane = lanes.lane
        GROUP BY lanes.lane ORDER BY lanes.lane
    """).tuples().all()
    print("run queued/running/held", queued, running, held, file=sys.stderr)
    for lane, queued_media, running_media in media:
        print(f"{lane} queued/running", queued_media, running_media, file=sys.stderr)
    assert (queued, running, held) == (0, 0, 0)
    assert media == [("image", 0, 0), ("voice", 0, 0)]
    return "all three lanes idle"


def unpack_import(conn, production, story, run, delivery):
    row = runs.get(conn, run)
    assert (row["production_id"], row["story_id"]) == (production, story)
    assert row["document"]["delivery_id"] == delivery
    return row


def check_draft(conn, production, story, expected_boards):
    draft = drafts.get_draft(conn, story)
    assert all("created_by" not in board for board in draft.boards.values())
    summary = draft.summary(conn)
    assert (summary["id"], summary["production_id"]) == (story, production)
    assert summary["derived"]["board_count"] == len(draft.boards) == expected_boards

    # This calls the installed build's real publication and approval filters.
    def listed(published, approved):
        return {
            item["id"] for item in drafts.list_stories(
                conn, production, summary["media_kind"], None, 50, published=published, approved=approved,
            )
        }

    assert story in listed(False, False)
    assert story not in listed(True, None) and story not in listed(None, True)
    return f"{expected_boards} boards, unpublished, unapproved"


def check_approval(conn, row, delivery):
    from haynes_studio.api.approval_routes import _check_approval, _delivery
    from haynes_studio.golive import packages, records

    delivered = _delivery(conn, row["id"])
    assert delivered is not None and delivered["id"] == delivery
    active = conn.scalar(records.active_id(row["id"]))
    _, refusal, _ = _check_approval(
        row["document"], delivered, delivered["master"]["sha256"], True, active,
        lambda: packages.for_run(conn, row["id"]), preview=True,
    )
    assert active is None and refusal is not None and refusal.details["code"] == "approval.legacy_run"
    return refusal.details["code"]


def check_snapshot(conn, row, has_snapshot):
    from haynes_studio.api.errors import APIError
    from haynes_studio.api.run_routes import _stored_snapshot
    from haynes_studio.store.local import LocalStore

    try:
        pin = runs.get(conn, row["id"], snapshot_pin_only=True)
        response = _stored_snapshot(LocalStore(os.environ["STUDIO_STORE_ROOT"]), pin, None)
    except APIError as error:
        # A recovered run has no stored snapshot (the column is null). Its document still
        # names the snapshot it was built from, so that reference is not null.
        assert not has_snapshot and row["snapshot"] is None
        assert (error.status_code, error.details["code"]) == (404, "run.snapshot_missing")
        return "404 run.snapshot_missing (recovered run)"
    digest = sha256(response.body).hexdigest()
    assert has_snapshot and response.status_code == 200 and digest == row["document"]["snapshot"]["sha256"]
    return f"200 SHA-256 {digest}"


def check_feedback(conn, story, run):
    from haynes_studio.feedback import records

    assert records.list_candidates(conn, story, run, None, 51) == []
    assert records.draft_iteration(conn, story) is None
    return "zero candidates and no active iteration"


def check_packages(conn, row):
    from haynes_studio.api.errors import APIError
    from haynes_studio.api.release_routes import _require_releasable, _review_evidence
    from haynes_studio.golive import packages

    assert packages.targets(row["snapshot"]) == []
    assert packages.for_run(conn, row["id"]) == []
    current, refusal = _review_evidence(conn, row)
    assert current is None and refusal is not None
    try:
        _require_releasable(None, None, refusal)
    except APIError as error:
        assert (error.status_code, error.details["code"]) == (422, "approval.legacy_run")
        return "zero targets and packages, eligibility refused (approval.legacy_run)"
    raise AssertionError("legacy release unexpectedly allowed")


def check_catalog(conn, production, row, has_snapshot):
    from haynes_studio.db import catalog

    frozen = {version["id"] for version in row["frozen_library"]}
    assert bool(frozen) == has_snapshot
    visible = catalog.find_assets(
        conn, {production}, kind=None, labels=[], production_id=production,
        version_ids=frozen, after=None, limit=len(frozen) + 1,
    )
    assert {version["id"] for version in visible} == frozen
    assert all(version["recorded_without_media"] and version["scope"]["production_id"] == production
               for version in visible)
    # Exercise the unfiltered first page as the catalog does; shared results are permitted.
    page = catalog.find_assets(
        conn, {production}, kind=None, labels=[], production_id=production,
        version_ids=None, after=None, limit=51,
    )
    assert all(version["scope"]["production_id"] in (None, production) for version in page)
    return f"{len(visible)} recorded-only frozen versions, {min(50, len(page))} first-page entries"


def check_archive(conn, production):
    from haynes_studio.db import staging_archive

    drafts.production(conn, production)
    assert staging_archive.production_records(conn, production, None, 51) == []
    return "zero staging archive records"


def import_counts(conn, production, story, run, delivery):
    return list(conn.exec_driver_sql("""
        SELECT (SELECT count(*) FROM studio.productions WHERE id = %s),
               (SELECT count(*) FROM studio.stories WHERE id = %s AND production_id = %s),
               (SELECT count(*) FROM studio.runs WHERE id = %s AND story_id = %s),
               (SELECT count(*) FROM studio.deliveries WHERE id = %s AND run_id = %s),
               (SELECT count(*) FROM studio.delivery_media WHERE run_id = %s)
    """, (production, story, production, run, story, delivery, run, run)).one())


def fail_walk(error):
    raise error


def store_counts():
    store = os.environ["STUDIO_STORE_ROOT"]
    files = size = 0
    for directory, subdirs, names in os.walk(store, onerror=fail_walk):
        if directory == store and ".probe" in subdirs:
            subdirs.remove(".probe")
        for name in names:
            size += os.stat(os.path.join(directory, name), follow_symlinks=False).st_size
            files += 1
    return [files, size]


phase, expected, revision = sys.argv[1:]
assert phase in {"preflight", "postflight", "compare"}
installed = os.environ["STUDIO_BUILD_REVISION"]
old = "git:393fe1d66070ac726739b4cdb001a9e6b2c467db"
assert installed == (f"git:{revision}" if phase == "postflight" else old) or (
    phase == "preflight" and installed == f"git:{revision}"
), installed
print(f"PASS probe build: {installed}", file=sys.stderr)

engine = connect_engine(os.environ["STUDIO_DATABASE_URL"], os.getenv("STUDIO_DATABASE_SCHEMA", "studio"))
imports = {}
results = []
with snapshot(engine) as conn:
    conn.exec_driver_sql("SET TRANSACTION READ ONLY")
    assert conn.exec_driver_sql("SELECT current_user, current_database(), ssl "
                               "FROM pg_stat_ssl WHERE pid = pg_backend_pid()").one() == (
                                   "studio_app", "studio", True)
    print("PASS runtime login and TLS", file=sys.stderr)

    def db_check(operation, *args):
        # A failed SQL read only aborts its savepoint, so other reads still report.
        with conn.begin_nested():
            return operation(conn, *args)

    results.append(check("queue counts", db_check, queue_counts))
    for title, production, story, run, delivery, has_snapshot, expected_boards in films:
        counts = import_counts(conn, production, story, run, delivery)
        imports[run] = counts
        passed = counts[:4] == [1, 1, 1, 1] and counts[4] >= 2
        print("PASS" if passed else "FAIL", title, "production/story/run/delivery/index", counts, file=sys.stderr)
        results.append(passed)
        results.append(check(f"{title} draft and filters", db_check, check_draft, production, story, expected_boards))
        if phase == "postflight":
            row = unpack_import(conn, production, story, run, delivery)
            results.append(check(f"{title} approval preview", db_check, check_approval, row, delivery))
            results.append(check(f"{title} snapshot", db_check, check_snapshot, row, has_snapshot))
            results.append(check(f"{title} feedback", db_check, check_feedback, story, run))
            results.append(check(f"{title} package list and hold", db_check, check_packages, row))
            results.append(check(f"{title} catalog", db_check, check_catalog, production, row, has_snapshot))
            results.append(check(f"{title} archive list", db_check, check_archive, production))

files_and_bytes = store_counts()
print("PASS store file/byte totals", files_and_bytes, file=sys.stderr)
report = {"production/story/run/delivery/index counts": imports, "store files/bytes": files_and_bytes}
if phase != "preflight":
    passed = report == json.loads(expected)
    print("PASS" if passed else "FAIL", "import and store baseline", file=sys.stderr)
    results.append(passed)
print("PASS" if all(results) else "FAIL", f"OP964 {phase}", file=sys.stderr)
if not all(results):
    raise SystemExit(1)
print(json.dumps(report, sort_keys=True))
PY
  )
  printf '%s\n' "$report"
  if [[ $phase == preflight ]]; then
    (umask 077; set -o noclobber; printf '%s\n' "$report" > "$baseline")
  fi
)
```

### Migration and rollout

The installed starting point is Studio `393fe1d` at
`0007_escalation_packages`. Migrate to the head shipped in the selected
Python image. The old API reads named columns, checks database and store
readiness, and continues serving after the schema transaction commits.
Changes to existing tables can take locks. **The whole upgrade is one
transaction:** the `ALTER TABLE stories` and the index on `runs` in 0008 and
the `SHARE ROW EXCLUSIVE` lock on `stories` in 0009 are held until the single
commit after 0012, so the old API's story reads wait for the entire run
(seconds on this data, against its 30-second statement timeout), and a failure
in any revision rolls the schema back to 0007 with `393fe1d` still serving.
Sample old API availability before, during and after migration.
[Migration source](https://github.com/thaynes43/haynes-studio/tree/main/src/haynes_studio/db/migrations/versions)

| Revision | Schema change | Effect on the serving `393fe1d` build |
| --- | --- | --- |
| `0008_candidate_feedback` | Creates append-only candidates, notes and iterations, adds nullable `stories.feedback_iteration_id` and a partial expression index on run answers, and grants scoped runtime access. | Old story inserts omit the nullable reference and old edits update named columns. The partial index applies only to run documents with `answers`. |
| `0009_release_packages` | Adds append-only package, publication and iteration-clear tables. Locks `stories` in SHARE ROW EXCLUSIVE mode, installs a constraint trigger on story insert/update for a non-null feedback iteration, and validates existing references against the candidate's run story. | Old inserts and existing drafts without an active iteration pass the trigger condition. The previous API's published status, list filters and story insert default still use `stories.published`. This migration preserves the Boolean column and its default. |
| `0010_staging_archive` | Creates production-scoped archive records and immutable version chains and grants runtime SELECT and INSERT. | Old API and workers never query these tables. The production foreign key uses existing IDs. |
| `0011_worker_lane_grants` | Creates a SELECT-only runtime grant table and seeds `studio-worker → run`, `studio-voice-worker → voice` and `studio-image-worker → image`. The new API joins the binding to the active worker identity. | Old API ignores the table. The migration changes neither token rows nor token projections. Lane enforcement starts on a per-pod basis at the API rollout. |
| `0012_release_constraints` (OP963) | Backfills `publications.target_id` from the package, rejects duplicate run/target publications, constrains the package byte count and composite publication/package identity, and requires archive version parents and roots to be contiguous (the root FK is deferred). | These release and archive tables were introduced after the old build. At a first upgrade from 0007 they have no rows to backfill or validate. If an intermediate pin has already written records, resolve any data-integrity failure through a reviewed forward fix. |

The selected publish has one Alembic head, 0012, whose parents in order are
0011, 0010, 0009 and 0008. Read the migration
Job output for the actual revisions and head. Expect the migrator to use
TLS and the configured role policies to refuse the other connections.
Record a missed active window explicitly if the Job finishes before a
sample. Use the following during the change from a LAN-capable terminal.

```bash
(
  set -euo pipefail
  root=kubernetes/main/apps/ai/studio
  job=$(yq -er 'select(.kind == "Job") | .metadata.name' "$root/migration/job.yaml")
  date -u
  kubectl -n ai get "job/$job" --ignore-not-found
  kubectl -n ai get pods -l app.kubernetes.io/name=studio-api \
    -o 'custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[0].ready,IMAGE:.status.containerStatuses[0].imageID'
  base=https://studio.haynesops.com
  status() { curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "$1"; }
  test "$(status "$base/")" = 200
  test "$(status "$base/v1/me")" = 401
  printf 'PASS LAN root=200 v1/me=401\n'
)
```

Registration follows migration. Each digest-named Job mounts the existing
projection for its worker. In the token administration code a matching
active token exits successfully with audit status `200` before writing
anything. For this release the Job arguments omit `--replace` so a
different token for an existing ID fails with a conflict. An audit `201`
would mean a first registration and is unexpected on this installed
cluster. Stop and look at that worker's state before resuming submissions.

After merge, from the coordinator session, follow the existing Flux
dependencies. Controllers can start dependent stages as soon as their
gates pass. A failed migration leaves the previous app available. Use a
forward fix and change the Job name and health check together for a retry
at the same digest (`-rN`). Keep the existing projections, storage and
engine configuration.

```bash
(
  set -euo pipefail
  root=kubernetes/main/apps/ai/studio
  job_name() { yq -er 'select(.kind == "Job") | .metadata.name' "$root/$1"; }

  flux reconcile kustomization cluster-apps -n flux-system --with-source
  flux reconcile kustomization studio-migration -n ai
  job=$(job_name migration/job.yaml)
  kubectl -n ai wait --for=condition=complete --timeout=5m "job/$job"
  kubectl -n ai logs "job/$job" -c migrate

  flux reconcile kustomization studio -n ai
  for workload in api mcp web; do
    kubectl -n ai rollout status "deploy/studio-$workload" --timeout=5m
  done
  flux reconcile kustomization studio-worker-registration -n ai
  flux reconcile kustomization studio-media-worker-registration -n ai
  for worker in worker voice-worker image-worker; do
    case "$worker" in
      worker) job=$(job_name worker-registration/job.yaml) ;;
      *) job=$(job_name "media-worker-registration/${worker%-worker}-job.yaml") ;;
    esac
    kubectl -n ai wait --for=condition=complete --timeout=5m "job/$job"
    kubectl -n ai logs "job/$job" -c register | jq -ser --arg worker "studio-$worker" '
      map(select(.worker_id == $worker and .status != null) | .status)
      | select(. == [200]) | .[0]'
    printf 'PASS %s existing registration\n' "$worker"
  done
  for stage in studio-worker studio-media-workers; do
    flux reconcile kustomization "$stage" -n ai
  done
  for worker in worker voice-worker image-worker; do
    kubectl -n ai rollout status "deploy/studio-$worker" --timeout=5m
  done
  kubectl -n ai get kustomization studio-migration studio studio-worker-registration \
    studio-worker studio-media-worker-registration studio-media-workers -o json |
    jq -er '
      [.items[] | select(any(.status.conditions[]?; .type == "Ready" and .status == "True"))] |
      select(length == 6) | [.[].status.lastAppliedRevision] | unique |
      select(length == 1 and .[0] != null) | .[0]'
)
```

### Postflight

Run each block before allowing submissions. Require both API containers
on the pair, two Ready API, MCP and web replicas on distinct nodes within
each Deployment, and one Ready worker per lane. The run worker stays on
`talosw01` and both media workers stay off it. Require zero restarts on
every container. The check includes terminating pods so a terminating
old API fails the gate for worker claim probes. Expect each worker's
progress check and idle-claim log check from [OP961](#reconcile-and-check-health)
to pass again.

```bash
(
  set -uo pipefail
  root=kubernetes/main/apps/ai/studio
  api_digest=$(yq -er '.apiDigest' "$root/release.yaml") || exit 1
  web_digest=$(yq -er '.webDigest' "$root/release.yaml") || exit 1
  api="ghcr.io/thaynes43/haynes-studio@$api_digest"
  web="ghcr.io/thaynes43/haynes-studio-web@$web_digest"
  failed=0
  for workload in api mcp web worker voice-worker image-worker; do
    replicas=2
    placement=any
    case "$workload" in
      api) containers=$(jq -cn --arg api "$api" --arg web "$web" \
             '{api:$api, "media-worker-routes":$web}') ;;
      mcp) containers=$(jq -cn --arg api "$api" '{mcp:$api}') ;;
      web) containers=$(jq -cn --arg web "$web" '{nginx:$web}') ;;
      *)
        replicas=1
        placement=off-talosw01
        if [[ $workload == worker ]]; then placement=talosw01; fi
        containers=$(jq -cn --arg api "$api" '{worker:$api}') ;;
    esac
    if kubectl -n ai get pods -l "app.kubernetes.io/name=studio-$workload" -o json |
      jq -er --argjson containers "$containers" --argjson replicas "$replicas" \
        --arg placement "$placement" '
        .items | select(length == $replicas and
          ([.[].spec.nodeName] | unique | length) == $replicas and
          all(.[];
            .metadata.deletionTimestamp == null and .spec.nodeName != null and
            ($placement == "any" or
             ($placement == "talosw01" and .spec.nodeName == "talosw01") or
             ($placement == "off-talosw01" and .spec.nodeName != "talosw01")) and
            any(.status.conditions[]?; .type == "Ready" and .status == "True") and
            ([.spec.containers[] | {key:.name,value:.image}] | from_entries) == $containers and
            (.status.containerStatuses | length) == ($containers | length) and
            all(.status.containerStatuses[]; . as $c |
              ($containers[$c.name] // "" | split("@") | .[1]) as $digest |
              $digest != null and .ready and .restartCount == 0 and (.imageID | endswith($digest))))) |
        .[] | [.metadata.name, .spec.nodeName, (.status.containerStatuses[].imageID)] | @tsv'
    then
      printf 'PASS studio-%s pods, pins and restarts\n' "$workload"
    else
      printf 'FAIL studio-%s pods, pins or restarts\n' "$workload" >&2
      failed=1
    fi
  done
  if (( failed )); then
    printf 'FAIL OP964 pod verification\n' >&2
    exit 1
  fi
  printf 'PASS OP964 pod verification\n'
)
```

Run the [import and queue probe](#imported-productions-and-queue-counts)
with `STUDIO_OP964_PHASE=postflight` and the original baseline. Run the
[OP961 engine block](#reconcile-and-check-health) from the voice and image
pods. It opens a Kokoro connection and reads `GET /queue` from shared
ComfyUI. From the run pod, open and close one TCP connection to its
configured Wan service. This establishes reachability for all three
workers without requesting engine work.

```bash
(
  set -euo pipefail
  kubectl -n ai exec -i deploy/studio-worker -c worker -- python -I -B - <<'PY'
import os
import socket
from urllib.parse import urlsplit

service = urlsplit(os.environ["STUDIO_WAN_URL"])
try:
    socket.create_connection((service.hostname, service.port), timeout=5).close()
except OSError as error:
    raise SystemExit(f"FAIL run worker reaches its engine ({type(error).__name__})") from None
print("PASS run worker reaches its engine")
PY
)
```

Check `internal/studio` in Gatus using the [existing Gatus
procedure](#gatus-and-backup-coverage). Record a passing timestamp after
reconcile and within two one-minute probe intervals. Confirm the anonymous
`/v1/me` response on the LAN is 401. A passing health result alone does
not verify any signed-in feature.

#### Worker lane grants

Run this block only after the pin, registration and queue checks pass.
It repeats the API pin and endpoint gates before sending a well-formed
claim for another lane. The grants query checks the three seeded IDs, and the worker
Deployment checks bind each probe to the same existing projection used by
its successful registration Job. The probes use the workers' own API
addresses and read the token within each worker process. They never send a
well-formed claim for the caller's lane. Authenticated invalid JSON gets
422 and cannot reach the claim handler. Each well-formed cross-lane request
must return 403 `work.lane_forbidden` before the claim transaction starts.

```bash
(
  set -euo pipefail
  root=kubernetes/main/apps/ai/studio
  revision=$(yq -er '.revision' "$root/release.yaml")
  digest=$(yq -er '.apiDigest' "$root/release.yaml")
  pods=$(kubectl -n ai get pods -l app.kubernetes.io/name=studio-api -o json)
  endpoints=$(kubectl -n ai get endpointslices -l kubernetes.io/service-name=studio-api -o json)
  if jq -e --arg digest "$digest" --argjson endpoints "$endpoints" '
    .items as $pods |
    ($pods | length) == 2 and
    all($pods[];
      .metadata.deletionTimestamp == null and
      any(.status.containerStatuses[]?; .name == "api" and .ready and (.imageID | endswith($digest)))) and
    ([$endpoints.items[].endpoints[] | select(.conditions.ready == true) | .targetRef.uid] | unique)
    == ([$pods[].metadata.uid] | unique)
  ' <<< "$pods" >/dev/null; then
    printf 'PASS both API endpoints use the selected pin\n'
  else
    printf 'FAIL API pin or endpoints, skip all cross-lane claims\n' >&2
    exit 1
  fi

  kubectl -n ai exec -i deploy/studio-api -c api -- python -I -B - "$revision" <<'PY'
import os
import sys

import psycopg

# The previous build lacks this function.
from haynes_studio.api.auth import require_worker_lane

assert callable(require_worker_lane) and os.environ["STUDIO_BUILD_REVISION"] == f"git:{sys.argv[1]}"
expected = [("studio-image-worker", "image"), ("studio-voice-worker", "voice"), ("studio-worker", "run")]
with psycopg.connect(os.environ["STUDIO_DATABASE_URL"]) as conn:
    conn.read_only = True
    grants = conn.execute("""
        SELECT worker_id, lane FROM studio.worker_lane_grants
        WHERE worker_id IN ('studio-image-worker', 'studio-voice-worker', 'studio-worker')
        ORDER BY worker_id
    """).fetchall()
    permissions = conn.execute("""
        SELECT has_table_privilege('studio.worker_lane_grants', 'SELECT'),
               has_any_column_privilege('studio.worker_lane_grants', 'INSERT'),
               has_any_column_privilege('studio.worker_lane_grants', 'UPDATE'),
               has_table_privilege('studio.worker_lane_grants', 'DELETE'),
               has_table_privilege('studio.worker_lane_grants', 'TRUNCATE')
    """).fetchone()
for worker, lane in grants:
    print("PASS" if (worker, lane) in expected else "FAIL", worker, lane)
if grants != expected:
    raise SystemExit("FAIL installed worker lane grants, skip all cross-lane claims")
print("PASS installed worker lane grants")
if permissions != (True, False, False, False, False):
    raise SystemExit("FAIL worker lane grant access, skip all cross-lane claims")
print("PASS runtime worker lane grants are read-only")
PY

  for worker in worker voice-worker image-worker; do
    if kubectl -n ai get deploy "studio-$worker" -o json |
      jq -e --arg secret "studio-$worker-token" '
        [.spec.template.spec.containers[] | select(.name == "worker") | .env[] |
         select(.name == "STUDIO_WORKER_TOKEN") |
         .valueFrom.secretKeyRef] == [{name:$secret, key:"STUDIO_WORKER_TOKEN"}]
      ' >/dev/null; then
      printf 'PASS studio-%s token projection reference\n' "$worker"
    else
      printf 'FAIL studio-%s token projection, skip all cross-lane claims\n' "$worker" >&2
      exit 1
    fi
  done

  failed=0
  for entry in worker:run voice-worker:voice image-worker:image; do
    worker=studio-${entry%:*}
    own_lane=${entry#*:}
    other_lane=run
    if [[ $own_lane == run ]]; then other_lane=voice; fi
    if kubectl -n ai exec -i "deploy/$worker" -c worker -- python -I -B - "$own_lane" "$other_lane" <<'PY'
import os
import sys

import httpx
from haynes_studio.queue.worker_tokens import worker_bearer

own_lane, other_lane = sys.argv[1:]
assert (own_lane, other_lane) in {("run", "voice"), ("voice", "run"), ("image", "run")}
headers = {"Content-Type": "application/json"}
auth = {**headers, "Authorization": "Bearer " + worker_bearer(os.environ["STUDIO_WORKER_TOKEN"])}


def check(label, request, expected, code=None):
    try:
        response = request()
        status = response.status_code
        match = code is None or (
            status == expected and response.json().get("error", {}).get("code") == code
        )
    except (httpx.HTTPError, ValueError, AttributeError) as error:
        print("FAIL", label, type(error).__name__)
        return False
    passed = status == expected and match
    print("PASS" if passed else "FAIL", label, status, code if match and code else "")
    return passed


results = []
with httpx.Client(base_url=os.environ["STUDIO_API_URL"], trust_env=False, timeout=5) as client:
    for operation in ("claim", "heartbeat", "publish", "complete", "fail"):
        path = "/internal/v1/work/" + operation
        results.append(check(f"{own_lane} {operation} anonymous",
                             lambda: client.post(path, headers=headers, content=b"{"), 401))
        results.append(check(f"{own_lane} {operation} authenticated malformed",
                             lambda: client.post(path, headers=auth, content=b"{"), 422))
    # A failed authentication or projection check prevents the well-formed request.
    if all(results):
        results.append(check(f"{own_lane} token claiming {other_lane}",
                             lambda: client.post("/internal/v1/work/claim", headers=auth,
                                                 json={"lane": other_lane}),
                             403, "work.lane_forbidden"))
    else:
        print("FAIL", own_lane, "cross-lane claim skipped after failed authentication probe")
raise SystemExit(0 if all(results) else 1)
PY
    then
      printf 'PASS %s authentication and lane refusal\n' "$worker"
    else
      printf 'FAIL %s authentication or lane refusal\n' "$worker" >&2
      failed=1
    fi
  done
  if (( failed )); then
    printf 'FAIL OP964 worker lane verification\n' >&2
    exit 1
  fi
  printf 'PASS OP964 worker lane verification\n'
)
```

#### Anonymous ingress and MCP

Verify the internal ingress uses the API's public HTTP port and serves
the web root. Requests use the existing certificate validation. All
listed API reads and writes must return 401 without an identity. Worker
and MCP paths must return 404 at the ingress. Also repeat the
[existing route and playback checks](#route-and-playback-checks) for the
older routes and both delivery proxies.

```bash
(
  set -uo pipefail
  base=https://studio.haynesops.com
  production=prd_01M7ZGYNHFET1GKR4304FVD4PE
  story=sty_01M7ZGYNHFET1GKR4304FVD4PF
  run=run_01M7ZHNZJ2PWA8XR8K7KKWP17V
  printf -v absent_digest '%064d' 0
  failed=0
  check() {
    local expected=$1 method=$2 path=$3 status=transport-error
    local -a body=()
    if [[ $method == POST || $method == DELETE ]]; then
      body=(-H 'Content-Type: application/json' --data-binary '{')
    fi
    if status=$(curl -q -sS --max-time 15 -o /dev/null -w '%{http_code}' \
        -X "$method" "${body[@]}" "$base$path") && [[ $status == "$expected" ]]; then
      printf 'PASS %s %s %s\n' "$method" "$path" "$status"
    else
      printf 'FAIL %s %s got %s expected %s\n' "$method" "$path" "$status" "$expected" >&2
      failed=1
    fi
  }

  check 200 GET /
  for path in /v1/me "/v1/stories/$story/candidates?run_id=$run" \
    "/v1/runs/$run/release-packages" "/v1/runs/$run/release-packages/$absent_digest/download" \
    "/v1/assets?production=$production" \
    "/v1/productions/$production/archive-records" \
    /v1/archive-records/arc_00000000000000000000000001 \
    /v1/archive-versions/arv_00000000000000000000000001; do
    check 401 GET "$path"
  done
  for path in "/v1/runs/$run/candidates" "/v1/runs/$run/release-packages" \
    "/v1/runs/$run/go-live-approvals" \
    "/v1/runs/$run/publications" "/v1/productions/$production/archive-records" \
    /v1/archive-records/arc_00000000000000000000000001/versions \
    /v1/archive-versions/arv_00000000000000000000000001/apply \
    /v1/candidates/cnd_00000000000000000000000001/feedback \
    /v1/candidates/cnd_00000000000000000000000001/iterations \
    /v1/asset-jobs; do
    check 401 POST "$path"
  done
  check 401 DELETE "/v1/stories/$story/feedback-iteration"
  for path in /healthz /readyz /mcp /mcp/upload /internal/v1/work/claim; do
    check 404 GET "$path"
  done
  if (( failed )); then
    printf 'FAIL OP964 anonymous ingress\n' >&2
    exit 1
  fi
  printf 'PASS OP964 anonymous ingress\n'
)
```

Cluster MCP checks the bearer before reading the request body or applying
tool-specific byte limits. A small ordinary call and a bounded request just
over 256 KiB must both return 401. The latter demonstrates auth ordering.
Verifying the exceptions requires authenticated tool dispatch. The Studio
source fixture `test_only_archive_text_writers_get_larger_mcp_envelopes`
covers the 256 KiB default, the 2 MiB wire ceiling for
`studio_create_archive_record` and `studio_add_archive_version`, and refusals
for other tool names and non-call messages. `/mcp/upload` has its separate
existing limit. Record the live 401s and cite the fixture for cap evidence.
[MCP reference](https://github.com/thaynes43/haynes-studio/blob/main/docs/reference/studio-mcp.md)
and [fixture](https://github.com/thaynes43/haynes-studio/blob/main/tests/mcp/test_server.py).

```bash
(
  set -euo pipefail
  kubectl -n ai exec -i deploy/studio-api -c api -- python -I -B - <<'PY'
import httpx

base = "http://studio-mcp.ai.svc.cluster.local:8001"
initialize = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "deploy-probe", "version": "1"}},
}
probe_call = {
    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
    "params": {"name": "studio_get_board", "arguments": {"board_id": "x" * (256 * 1024)}},
}
results = []
with httpx.Client(base_url=base, trust_env=False, timeout=5,
                  headers={"Accept": "application/json, text/event-stream"}) as client:
    for label, path, body in (("MCP initialize", "/mcp", initialize),
                              ("MCP upload initialize", "/mcp/upload", initialize),
                              ("MCP over 256 KiB", "/mcp", probe_call)):
        try:
            response = client.post(path, json=body)
            status = response.status_code
            passed = status == 401 and response.json().get("error", {}).get("code") == "auth.required"
        except (httpx.HTTPError, ValueError, AttributeError) as error:
            status, passed = type(error).__name__, False
        results.append(passed)
        print("PASS" if passed else "FAIL", label, status)
print("PASS" if all(results) else "FAIL", "OP964 anonymous MCP")
raise SystemExit(0 if all(results) else 1)
PY
)
```

Signed-in feature use and phone playback have separate evidence. The
existing imports can be reviewed privately but are permanently ineligible
for go-live. A new Studio-built run and a signed-in person are required
for the first real release package. See
[OP944](https://work.haynesnetwork.com/work_packages/944) for phone playback.

### Roll back while keeping the third schema

Use a corrective GitOps PR that restores all six workload pins to the
`393fe1d` Python and web pair, including the API's listener, and restores
the old API/worker build revision and worker image provenance. Recover the
workload pins from [the OP961/Cilium-fixed tree](https://github.com/thaynes43/haynes-ops/tree/7690f436b/kubernetes/main/apps/ai/studio).
Keep the new migration and registration Jobs and their health checks
Complete. The old migration image cannot resolve the newer Alembic head.
Review the corrective PR's release check so it verifies the restored
workload pair separately from the retained Job digest. Keep the store,
database, projections and existing worker IDs. Reconcile in dependency
order and use the read-only `compare` phase against a new baseline.
Repeat the restored pod, old LAN, Gatus and imported playback checks.

Before rollback, stop submissions, drain all three lanes and review any
held run. Check for newer writes before restoring writer access. The old
build may serve the original imports and their media at the new schema.
Prefer a forward fix as soon as people have used the new features.

| Revision | Rollback with the upgraded schema retained |
| --- | --- |
| 0008 | Unused feedback tables and a null story reference are safe. Existing notes and iteration records stay stored but disappear from the old UI. An active feedback iteration won't be frozen into a run enqueued by the old app. Answering runs can carry `answers`, which the old run schema rejects. Keep affected drafts and runs out of the old editing and build flow. A full return to that flow requires no new feedback-linked work. |
| 0009 | The old app can still read `stories.published`. It ignores release packages, iteration-clear events and publications. New publication rows won't appear in the old column's status or filters, and the old approval path lacks the package checks. Restore old publishing and edit semantics only before new release or feedback state has been written. Preserve all package copies and records. |
| 0010 | Staged text and versions stay in the database but are unavailable in the old app. Once a storyboard is applied, its `draft_application.archive_version_id` stamp is outside the old board schema. Keep affected drafts out of old editing and enqueue. A full writer rollback requires no archive applies or new archive work to continue. |
| 0011 | The seeded grants and token rows survive rollback. The old API ignores lane bindings even after the new schema is in place. Keep all submissions stopped until the lane-aware app is restored. The old pin can cover private import reads in the meantime. |
| 0012 | Package, publication and archive integrity constraints remain in place. The old build uses none of those tables. Their records retain the 0009/0010 visibility and writer restrictions above. |
| No revision (Studio PR 128) | The new build's format recipe schema accepts `matte_slots`. A format version saved with it by the new build is outside the old build's recipe schema, so the old build would likely refuse any story or run pinned to that version. Count such versions below before an image rollback; if any exist, fix forward. |

Keep any data written by the new build. An image rollback never calls
`alembic downgrade` and never rewrites a legacy published flag to imitate a
new publication record.

**Count what the new build has written before deciding on an image rollback.**
`compare` covers only the import counts and the store totals. This read-only
block counts every kind of row and field that only the new build writes. All
zero means an image rollback strands nothing; anything else is listed by name,
and the table above says what the old build does with it.

```bash
(
  set -uo pipefail
  kubectl -n ai exec -i deploy/studio-api -c api -- python -I -B - <<'PY'
import os
import sys

import psycopg

QUERIES = {
    "run candidates": "SELECT count(*) FROM studio.run_candidates",
    "feedback notes": "SELECT count(*) FROM studio.candidate_feedback",
    "feedback iterations": "SELECT count(*) FROM studio.feedback_iterations",
    "iteration clears": "SELECT count(*) FROM studio.feedback_iteration_clears",
    "stories with an active iteration": "SELECT count(*) FROM studio.stories WHERE feedback_iteration_id IS NOT NULL",
    "release packages": "SELECT count(*) FROM studio.release_packages",
    "publications": "SELECT count(*) FROM studio.publications",
    "archive records": "SELECT count(*) FROM studio.archive_records",
    "archive versions": "SELECT count(*) FROM studio.archive_versions",
    "runs with feedback answers": "SELECT count(*) FROM studio.runs WHERE document ? 'answers'",
    "boards stamped from the archive":
        "SELECT count(*) FROM studio.boards WHERE document::text LIKE '%\"archive_version_id\"%'",
    "format versions with matte slots":
        "SELECT count(*) FROM studio.format_versions WHERE recipe::text LIKE '%\"matte_slots\"%'",
}
written = 0
with psycopg.connect(os.environ["STUDIO_DATABASE_URL"]) as conn:
    conn.read_only = True
    for label, query in QUERIES.items():
        try:
            count = conn.execute(query).fetchone()[0]
        except psycopg.Error as error:
            print(f"FAIL {label}: {type(error).__name__}")
            conn.rollback()
            written = -1
            continue
        print(f"{'PASS' if count == 0 else 'NOTE'} {label}: {count}")
        if count and written >= 0:
            written += count
if written < 0:
    print("FAIL some counts could not be read; do not conclude anything from this block")
    sys.exit(2)
print("PASS nothing written by the new build" if written == 0
      else "NOTE the new build has written data: see the rollback table before an image rollback")
sys.exit(0 if written == 0 else 1)
PY
)
```

The block exits 0 only when every count is zero, 1 when the new build has
written something (a result, not an error), and 2 when a count could not be
read. Column and table names are those of the schema at the pinned build; if a
count fails after a later pin, correct the query before trusting the block.
