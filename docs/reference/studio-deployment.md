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
digest as `STUDIO_BUILD_IMAGE_DIGEST`. The API and worker load the model licence register from their image's
installed Python package. The previous digest-named ConfigMap
`studio-model-licences-1aa9b4736da9` is immutable and exempt from Flux pruning.
Keep it and any older retained registers through the rollback window. Once
their images are retired as rollback candidates and no old pod mounts them,
the coordinator can remove them in a separate cleanup. The old ConfigMap's
source is available in [OP957's merged tree](https://github.com/thaynes43/haynes-ops/tree/c20a53a4925d6322895943bb2994c294357296b9/kubernetes/main/apps/ai/studio/app).

The worker records main-thread progress under `/run/studio` on a pod-local
`emptyDir`. Startup waits for the marker. Liveness checks its age every 30
seconds and restarts the container after three consecutive failures. The
1,500-second age budget gives a 20-minute Wan clip five minutes of margin.
Wan polling refreshes the marker, as do claim polls and build
steps. The probe works without network calls. On a restart, the 90-second
claim expires, and a replacement reclaims the FIFO head with a new fence or
keeps it held pending reconciliation. It reads the durable Wan ID first. An
ambiguous submission can replay the same ID and body, which Wan deduplicates.
A liveness restart sends no second engine submission for a pending attempt.
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
at a time with two media threads. OP959 installs the API's media job and lane
routes. Voice and image consumers are separate processes with their own worker
IDs, tokens, writable store mounts and Kokoro or ComfyUI settings. The
Deployment here serves the run lane. Its CPU request matches the render pod's
250m scheduling share. At preflight the idle render pod used 3m CPU and
16 MiB. The existing serial mix reported 195 MB peak memory. The worker
request and limit are provisional allowances for the Python process,
decoded frame buffers and media subprocesses. Sample worker CPU, memory
and restarts during the first authorized build.
[Studio mix measurements](https://github.com/thaynes43/haynes-studio/blob/c75086b85a3278ead1d42b7a26da3b70da8b5793/docs/reference/studio-mix.md)

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

Take the following snapshot **before and after**. Compare each film's
production, story, run, delivery and playback index counts, and the
authoritative store's file count and total bytes. Store startup probes are
transient and under `.probe`, outside the durable file count. The worker
should poll the empty queue without claiming a run. The DB transaction is
read-only and uses the runtime role's existing projection.

```bash
(
  set -euo pipefail
  kubectl -n ai exec -i deploy/studio-api -c api -- python - <<'PY'
import os

import psycopg

films = (
    ("one-more-meeting", "prd_01M7ZGYNHFET1GKR4304FVD4PE",
     "sty_01M7ZGYNHFET1GKR4304FVD4PF", "run_01M7ZHNZJ2PWA8XR8K7KKWP17V",
     "dlv_01M7ZHP1QS82QGK196ZPCM7MQS"),
    ("the-winter-menu", "prd_01M79V9FC00000000000000001",
     "sty_01M79V9FC00000000000000001", "run_01M7C5CJA7XKBCRPM0ZRP2P5RS",
     "dlv_01M7C5D3KFX3MFVWCXBCVJHMG7"),
)
with psycopg.connect(os.environ["STUDIO_DATABASE_URL"]) as conn:
    conn.read_only = True
    assert conn.execute("SELECT current_user, current_database(), ssl "
                        "FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone() == (
                            "studio_app", "studio", True)
    statuses = conn.execute(
        "SELECT document->>'status', count(*) FROM studio.runs GROUP BY 1 ORDER BY 1"
    ).fetchall()
    print("runs by status", statuses)
    assert all(status not in {"queued", "running", "held"} for status, _ in statuses)
    for film, production, story, run, delivery in films:
        counts = conn.execute("""
            SELECT (SELECT count(*) FROM studio.productions WHERE id = %s),
                   (SELECT count(*) FROM studio.stories WHERE id = %s),
                   (SELECT count(*) FROM studio.runs WHERE id = %s),
                   (SELECT count(*) FROM studio.deliveries WHERE id = %s AND run_id = %s),
                   (SELECT count(*) FROM studio.delivery_media WHERE run_id = %s)
        """, (production, story, run, delivery, run, run)).fetchone()
        assert counts[:4] == (1, 1, 1, 1) and counts[4] >= 2, (film, counts)
        print(film, "production/story/run/delivery/index counts", *counts)

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
print("store files/bytes", files, size)
PY
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
A legacy run also remains ineligible for approval.
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

Quiesce all lanes and let existing runs settle before changing the build
revision. A corrective GitOps PR can restore the API, MCP, worker and web
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
forward after the new app begins writing. The older API validates draft
writes against a board schema that rejects the new `created_by` stamp.
An old edit of a story's JSON format pin cannot update the new relational
column and can fail its consistency check. Keep affected drafts and
recipe changes paused during a rollback. Leave the database at the new
head and keep the retained Jobs Complete. Reconcile the app and worker in
the same dependency order.
Repeat the baseline counts, ready pod and digest checks for the restored
images, the pre-existing LAN routes, and signed-in imported playback. Use
the retained migration Job's head evidence and rerun the runtime privilege
check. The new routes and liveness check apply only while OP959 is installed.
