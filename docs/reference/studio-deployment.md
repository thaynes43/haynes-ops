# Studio LAN deployment

[OP434](https://work.haynesnetwork.com/work_packages/434) installed Studio in
`ai`. [OP956](https://work.haynesnetwork.com/work_packages/956) adds the queue worker,
its credential registration and a new release pin. The queue rollout and checks
below are expected until the coordinator records results.

`https://studio.haynesops.com` uses the internal Traefik tier, the
`internal.haynesops` DNS target and the namespace's wildcard certificate.
Traefik sends `/v1/` and `/auth/` to the API and UI paths to nginx. The
probes, worker paths and MCP paths have no ingress route. A NetworkPolicy
allows internal Traefik pods to reach the API and web ClusterIP Services.
The auth route allows a burst of 30 requests and refills at 120 per minute per
observed peer address on each Traefik replica. The internal LoadBalancer
may combine clients behind a shared peer address.

MCP listens at `http://studio-mcp.ai.svc.cluster.local:8001/mcp`. The
Service accepts callers from pods in `dev-agents` and the Studio API pods in
`ai`, subject to their existing egress policies. MCP sends authenticated requests to
`http://studio-api.ai.svc.cluster.local:8000` using the caller's agent key.
Traefik has no route or network access to MCP. Use an approved protected
transport for bearer-key use outside the pod network.
[Studio MCP transport contract](https://github.com/thaynes43/haynes-studio/blob/fa5296870ccf11be99b56d9e5a2ee34b37a68a75/docs/reference/studio-mcp.md)

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
read `/index.html`. MCP probes check its TCP listener. Gatus uses the shared internal component and checks
`/v1/me` on the LAN origin, where a healthy anonymous API returns 401.

The worker runs one `studio-worker` replica on `talosw01` with `Recreate`
and the `dev-env-agent` priority class. It shares the API's UID and GID.
Both processes mount `data/studio/build-stage-v1` from the same export at
`/build-stage`, read-write, as `STUDIO_BUILD_STAGE_ROOT`. The worker builds
there and the API validates and publishes the resulting delivery. Both use the
same `STUDIO_BUILD_REVISION=git:<commit>`. The worker records the API image
digest as `STUDIO_BUILD_IMAGE_DIGEST` and mounts the matching source release's
model licence register from a Kustomize ConfigMap.

Worker egress allows cluster DNS, the Studio API on TCP 8000, the Wan product
Service on TCP 8080 and Wyoming Whisper on TCP 10300. The API admits the
worker pod to port 8000. On that port the application enforces the worker-only
`/internal/v1/work/*` boundary. The pod has no inbound listener, service account
token or database credential. ESO generates the 64-character alphanumeric token
with `CreatedOnce`. The registration Job mounts it as a file and uses the
migration identity to register worker ID `studio-worker`. The worker uses the
same Secret key as an environment reference. Re-running registration with the
same value is idempotent.
[Studio queue configuration](https://github.com/thaynes43/haynes-studio/blob/c75086b85a3278ead1d42b7a26da3b70da8b5793/docs/reference/run-queue.md#worker-configuration)
[Token administration](https://github.com/thaynes43/haynes-studio/blob/c75086b85a3278ead1d42b7a26da3b70da8b5793/docs/reference/studio-api.md#worker-token-administration)

D47 excludes NFS media from backups. The NFS server is outside the
cluster and remains a shared dependency of all API replicas. PostgreSQL
uses the existing [CNPG backup coverage](studio-database.md#backup-and-rollback).

## Initial resource settings

OP434 measured the API, MCP, web and migration images in a disposable smoke
using the packaged executables and PostgreSQL 16. The smoke sampled process
RSS and CPU with a small number of serial probes. The queue worker and
registration Job use provisional settings.

| Workload | Measured RSS | Mean CPU in smoke | CPU request | Memory request / limit |
| --- | ---: | ---: | ---: | ---: |
| API | 127.5 MiB | 1.5m | 100m | 160 MiB / 320 MiB |
| MCP | 63.1 MiB | 1.5m | 50m | 96 MiB / 192 MiB |
| Web | 9.4 MiB | below sampling resolution | 25m | 16 MiB / 32 MiB |
| Migration Job | 74.3 MiB | 1.14 CPU-seconds in 9.99 s | 100m | 96 MiB / 192 MiB |
| Registration Job | Pending | Pending | 50m | 64 MiB / 192 MiB |
| Queue worker | Pending | Pending | 250m | 2 GiB / 8 GiB |

CR-22 divides D18's 24 vCPU limit into 20 for `studio-render` and 4 for
`studio-worker`. Both use the same low priority. The worker runs one build
at a time with two media threads. Its CPU request matches the render pod's
250m scheduling share. At preflight the idle render pod used 3m CPU and
16 MiB. The existing serial mix reported 195 MB peak memory. The worker
request and limit are provisional allowances for the Python process,
decoded frame buffers and media subprocesses. Sample worker CPU, memory
and restarts during the first authorized build.
[Studio mix measurements](https://github.com/thaynes43/haynes-studio/blob/c75086b85a3278ead1d42b7a26da3b70da8b5793/docs/reference/studio-mix.md)

## Before merging the installation PR

The coordinator runs the rollout. Confirm `database/cloudnative-pg-cluster`
has all three Postgres instances ready, `ai/studio-database` and
`ai/studio-oidc` are ready, and their projections report Ready. Confirm
the schema and ACL owner bootstrap in [Studio database](studio-database.md#reconciliation-and-owner-bootstrap).
Check the Authentik application advertises the per-provider HTTPS issuer and
the redirect URI `https://studio.haynesops.com/auth/callback`. Check
at least three eligible nodes and the existing LAN TLS and DNS path.

Confirm the existing `/studio/api-store-v1` directory in the Studio render
pod. On a fresh install, create that empty directory in the same way as
below. For OP956, wait for active offline renders to finish before Helm
replaces the pod. Then create the empty sibling `/studio/build-stage-v1`
once as `1000:1000`, mode `0700`. Investigate a name collision before rollout.

```sh
kubectl -n ai exec deploy/studio-render -c app -- sh -eu -c '
  test "$(id -u):$(id -g)" = 1000:1000
  umask 077
  test -d /studio/api-store-v1
  mkdir /studio/build-stage-v1
  stat -c "%u:%g %a %n" /studio/api-store-v1 /studio/build-stage-v1
'
kubectl -n ai wait --for=condition=Ready --timeout=2m \
  externalsecret/studio-runtime-db externalsecret/studio-migrator-db \
  externalsecret/studio-oidc-client
```

## Reconcile and verify

After merge, track the same `main@sha1:<merge SHA>` through the Flux
Kustomizations below. The reconciliations can also run automatically
once Flux sees the commit. Record Job success before recording the
application rollout. The API, MCP and web wait on migration. Registration
also waits on the migration and the render Kustomization, and its health
checks require both the registration Job and the updated render HelmRelease.
The worker waits on both the app rollout and registration at the same Git
artifact revision. From the LAN, run the anonymous `/v1/me` and app root checks
below while the migration Job runs. Expect 401 and 200 respectively.

```sh
flux reconcile kustomization cluster-apps -n flux-system --with-source
flux get kustomization cloudnative-pg-cluster -n database
flux get kustomizations -n ai
flux reconcile kustomization studio-migration -n ai
kubectl -n ai wait --for=condition=complete --timeout=5m job/studio-migrate-b8460c3da822
kubectl -n ai logs job/studio-migrate-b8460c3da822 -c migrate
flux reconcile kustomization studio-render -n ai
kubectl -n ai wait --for=condition=Ready --timeout=20m helmrelease/studio-render
kubectl -n ai rollout status deploy/studio-render --timeout=5m
test "$(kubectl -n ai get deploy studio-render -o jsonpath='{.spec.template.spec.containers[0].resources.limits.cpu}')" = 20
flux reconcile kustomization studio-worker-registration -n ai
kubectl -n ai wait --for=condition=Ready --timeout=2m externalsecret/studio-worker-token
kubectl -n ai wait --for=condition=complete --timeout=5m job/studio-register-worker-b8460c3da822
kubectl -n ai logs job/studio-register-worker-b8460c3da822 -c register
flux reconcile kustomization studio -n ai
kubectl -n ai rollout status deploy/studio-api --timeout=5m
kubectl -n ai rollout status deploy/studio-web --timeout=5m
kubectl -n ai rollout status deploy/studio-mcp --timeout=5m
kubectl -n ai get pods -l 'app.kubernetes.io/name in (studio-api,studio-web,studio-mcp)' -o wide
kubectl -n ai get pods -l 'app.kubernetes.io/name in (studio-api,studio-web,studio-mcp)' \
  -o 'custom-columns=NAME:.metadata.name,NODE:.spec.nodeName,READY:.status.containerStatuses[0].ready,IMAGE:.status.containerStatuses[0].imageID'
kubectl -n ai get pdb studio-api studio-web studio-mcp
kubectl -n ai get ingressroute studio
flux reconcile kustomization studio-worker -n ai
kubectl -n ai rollout status deploy/studio-worker --timeout=5m
kubectl -n ai get pods -l app.kubernetes.io/name=studio-worker \
  -o 'custom-columns=NAME:.metadata.name,NODE:.spec.nodeName,READY:.status.containerStatuses[0].ready,IMAGE:.status.containerStatuses[0].imageID'
flux get kustomizations -n ai
```

Expect six ready app pods, with the two pods of each app Deployment on
different nodes, and one ready queue worker on `talosw01`. Each app budget
should allow one disruption when both replicas are ready. Registration
logs report the worker ID and result status only.
The migration Job logs must report a TLS login as `studio_migrator`, both HBA rejects
and Alembic revision `0003_studio_run_queue`. Run the TLS, HBA and privilege
checks in [Studio database](studio-database.md#credential-free-access-evidence)
with `verification_pod` set to a Studio API pod, `verification_container=api`
and the role argument `studio_app`.

Verify the effective NFS export and access mode on the API and worker.
For NFSv4 these mounts report `/` as the mount root and the exported
subdirectory in the source field after the separator.

```sh
for role in api worker; do
  for pod in $(kubectl -n ai get pod -l "app.kubernetes.io/name=studio-$role" -o name); do
    kubectl -n ai exec -i "$pod" -c "$role" -- python - "$role" <<'PYCODE'
import os
import sys
from pathlib import Path
from urllib.request import urlopen

role = sys.argv[1]
assert (os.getuid(), os.getgid()) == (1000, 1000)
mounts = [line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines()]
for target, subdirectory, access in (
    ("/studio", "api-store-v1", "ro" if role == "worker" else "rw"),
    ("/build-stage", "build-stage-v1", "rw"),
):
    mount, = [row for row in mounts if row[4] == target]
    separator = mount.index("-")
    assert mount[3] == "/"
    assert mount[separator + 1] in ("nfs", "nfs4")
    assert mount[separator + 2] == "gasha01.haynesnetwork:/hdd-nfs-repl/data/studio/" + subdirectory
    assert access in mount[5].split(",")
    print(role, target, subdirectory, access)
if role == "api":
    for path in ("/healthz", "/readyz"):
        with urlopen("http://127.0.0.1:8000" + path, timeout=6) as response:
            assert response.status == 204
    print("healthz=204 readyz=204")
PYCODE
  done
done
```

Prove shared writes by creating one temporary file from the first API
pod, reading it through the second, and removing only that file.

```bash
(
  set -eu
  mapfile -t pods < <(kubectl -n ai get pod -l app.kubernetes.io/name=studio-api -o name)
  test "${#pods[@]}" -eq 2
  marker=$(kubectl -n ai exec "${pods[0]}" -c api -- python -c '
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

assert (os.getuid(), os.getgid()) == (1000, 1000)
with NamedTemporaryFile(dir="/studio/.probe", prefix="op434-", delete=False) as handle:
    handle.write(b"op434")
    handle.flush()
    os.fsync(handle.fileno())
print(Path(handle.name).name)
')
  case "$marker" in op434-*) ;; *) exit 1 ;; esac
  trap 'kubectl -n ai exec "${pods[0]}" -c api -- rm -- "/studio/.probe/$marker"' EXIT
  kubectl -n ai exec "${pods[1]}" -c api -- python -c '
import os, sys
from pathlib import Path

assert (os.getuid(), os.getgid()) == (1000, 1000)
assert (Path("/studio/.probe") / sys.argv[1]).read_bytes() == b"op434"
print("cross-pod NFS write and read-back passed")
' "$marker"
)
```

From the LAN, verify the anonymous protected families, static UI and
route exclusions. A zero hash is a synthetic locator.

```sh
(
set -eu
base=https://studio.haynesops.com
status() { curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "$@"; }
zero_hash=$(printf '%064d' 0)
for path in /v1/me /v1/me/selections /v1/productions /v1/productions/sample \
  /v1/productions/sample/media-types/short/stories /v1/stories/sample \
  /v1/stories/sample/timing /v1/boards/sample /v1/assets /v1/assets/sample \
  /v1/runs/sample/delivery "/v1/media/$zero_hash"; do
  test "$(status "$base$path")" = 401
done
test "$(status -X POST "$base/auth/logout")" = 401
test "$(status "$base/")" = 200
test "$(status "$base/productions/example/shorts")" = 200
for path in /healthz /readyz /mcp /internal/v1/work/claim; do
  test "$(status "$base$path")" = 404
done
test "$(status https://video.haynesops.com/v1)" = 404
test "$(status https://video.haynesops.com/v1/me)" = 404
echo "LAN route checks passed"
)
```

Confirm that the initial browser redirect points to the configured Authentik
issuer and is reachable. Record the fields below and the result, with
state, challenge, nonce and cookies omitted.

```sh
python3 - <<'PY'
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener, urlopen

class StopRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

origin = "https://studio.haynesops.com"
auth = "authentik.haynesnetwork.com"
try:
    build_opener(StopRedirect).open(origin + "/auth/login", timeout=15)
except HTTPError as error:
    assert error.code == 303
    redirect = error.headers["Location"]
else:
    raise AssertionError("Expected an OIDC redirect")

endpoint = urlsplit(redirect)
assert (endpoint.scheme, endpoint.netloc) == ("https", auth)
params = parse_qs(endpoint.query)
assert params["client_id"] == ["haynes-studio"]
assert params["redirect_uri"] == [origin + "/auth/callback"]
assert params["code_challenge_method"] == ["S256"]
with urlopen(redirect, timeout=15) as response:
    assert urlsplit(response.geturl()).netloc == auth
    print("Authentik reachable, client_id=haynes-studio, redirect_uri=" + origin + "/auth/callback")
PY
```

Check that an anonymous call from an allowed `ai` pod reaches MCP at
the configured host and receives 401.

```sh
kubectl -n ai exec deploy/studio-api -c api -- python -c '
from urllib.error import HTTPError
from urllib.request import urlopen

try:
    urlopen("http://studio-mcp.ai.svc.cluster.local:8001/mcp", timeout=5)
except HTTPError as error:
    assert error.code == 401
    print("cluster MCP GET /mcp -> 401")
else:
    raise AssertionError("expected MCP to require an agent key")
'
```

Finish with a browser round trip using an existing allowed human account.
Verify `/v1/me` identifies the person after sign-in and returns 401 after
sign-out. For scoped playback, use an authorized private fixture and its
existing scoped caller. Verify a byte Range returns 206 for an in-scope
hash and the same request returns 404 for an out-of-scope hash. Record
statuses, scope and the fixture's hash. Verify OP956's worker below. The
broader concurrency, recovery and private delivery checks stay with
[OP437](https://work.haynesnetwork.com/work_packages/437).

## Verify the queue worker

From the worker pod, send a malformed query to the internal claim route.
Its authentication happens before query validation. The malformed query
prevents this probe from taking a claim. Record statuses only.

```sh
kubectl -n ai exec -i deploy/studio-worker -c worker -- python - <<'PY'
import os
import socket

import httpx

from haynes_studio.queue.worker_tokens import worker_bearer

with httpx.Client(base_url=os.environ["STUDIO_API_URL"], timeout=10, trust_env=False) as client:
    path = "/internal/v1/work/claim?probe=%00"
    assert client.post(path).status_code == 401
    token = worker_bearer(os.environ["STUDIO_WORKER_TOKEN"])
    headers = {"Authorization": "Bearer " + token}
    assert client.post(path, headers=headers).status_code == 422
    assert client.get("/v1/me", headers=headers).status_code == 401
    print("anonymous claim=401, authenticated claim passed auth and failed query=422, worker public access=401")

with httpx.Client(base_url=os.environ["STUDIO_WAN_URL"], timeout=10, trust_env=False) as client:
    assert client.get("/v1/ready").status_code == 200
with socket.create_connection((os.environ["STUDIO_ASR_HOST"], int(os.environ["STUDIO_ASR_PORT"])), timeout=10):
    print("Wan readiness route and Wyoming ASR listener reachable")
PY
```

Use existing authorized test clients for the agent and browser cases. From an
authorized workstation, forward the API Service to loopback for the direct API
check. The LAN ingress must continue to return 404 for the same internal path.

```sh
kubectl -n ai port-forward --address 127.0.0.1 service/studio-api 18000:8000
```

Run the following in each existing credential holder, with its agent bearer
already supplied as `STUDIO_AGENT_KEY` or its browser session already supplied
as `STUDIO_SESSION_COOKIE` (cookie value only). Use the existing client's
protected credential path. Record only HTTP status codes. If a client cannot
provide that proof safely, leave that case pending.

```sh
python3 - <<'PY'
import os
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

origin = "http://127.0.0.1:18000"
client = build_opener(ProxyHandler({}))
if "STUDIO_AGENT_KEY" in os.environ:
    caller = "agent"
    headers = {"Authorization": "Bearer " + os.environ["STUDIO_AGENT_KEY"]}
else:
    caller = "browser"
    headers = {"Cookie": "__Host-studio-session=" + os.environ["STUDIO_SESSION_COOKIE"]}

def status(path, method):
    try:
        with client.open(Request(origin + path, method=method, headers=headers), timeout=10) as response:
            return response.status
    except HTTPError as error:
        return error.code

assert status("/v1/me", "GET") == 200
assert status("/internal/v1/work/claim?probe=%00", "POST") == 401
print(caller, "/v1/me=200 internal claim=401")
PY
```

Check the shared stage from the worker and both API replicas. Keep the
temporary file present while reading and remove only that file after the check.

```bash
(
  set -eu
  marker=$(kubectl -n ai exec deploy/studio-worker -c worker -- python -c '
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

with NamedTemporaryFile(dir="/build-stage", prefix="op956-", delete=False) as handle:
    handle.write(b"op956")
    handle.flush()
    os.fsync(handle.fileno())
print(Path(handle.name).name)
')
  case "$marker" in op956-*) ;; *) exit 1 ;; esac
  trap 'kubectl -n ai exec deploy/studio-worker -c worker -- rm -- "/build-stage/$marker"' EXIT
  for pod in $(kubectl -n ai get pod -l app.kubernetes.io/name=studio-api -o name); do
    kubectl -n ai exec "$pod" -c api -- python -c '
import sys
from pathlib import Path

assert (Path("/build-stage") / sys.argv[1]).read_bytes() == b"op956"
print("API read back the worker stage marker")
' "$marker"
  done
)
```

While the queue is empty, look for a natural 204 claim in the worker log.
Once an authorized fixture is queued, look for a claim returning 200 and a
200 heartbeat. Use the run view to confirm the lease and any refusal
backoff. Capture timestamps, codes and the run ID. Let Wan's normal
lease and unknown-work checks control admission.

```sh
kubectl -n ai logs deploy/studio-worker -c worker --since=10m   | grep -E '/internal/v1/work/(claim|heartbeat|fail).*HTTP/1.1 (200|204)'
kubectl -n ai top pod -l app.kubernetes.io/name=studio-worker --containers
```

The pinned release has no supported CLI, HTTP or MCP command that installs a
production and its assets into both the API's store and catalog. The
`studio legacy import` command prepares the canonical story exchange.
`python -m haynes_studio.importer` can verify and publish source bytes into a
filesystem store and write library records. The server-side
`db.drafts.import_draft` helper registers the production, draft and asset
version metadata in one transaction. A private import harness still needs
to join those steps: publish the verified canonical blobs create-only into
the installed store, register the matching metadata and validate the
draft's pinned photo, voice and other required assets. Before enqueue,
`GET /v1/stories/<story-id>/estimate` should report a buildable reference set.
With an authorized fixture and existing credentials, use
`POST /v1/stories/<story-id>/runs` with the current `If-Match` and a fresh
`Idempotency-Key`. Read `GET /v1/runs/<run-id>` until the claim and any
natural Wan refusal are visible. For a refusal, capture the return to
`queued`, its retry time and the same run's subsequent claim after Wan clears.
Record the import prerequisite if it has not been completed.
[Private input importer](https://github.com/thaynes43/haynes-studio/blob/c75086b85a3278ead1d42b7a26da3b70da8b5793/docs/reference/private-input-import.md)
[Catalog import boundary](https://github.com/thaynes43/haynes-studio/blob/c75086b85a3278ead1d42b7a26da3b70da8b5793/docs/reference/studio-api.md#draft-metadata-and-catalog)

After the fixture settles, use a temporary GitOps change to set the worker
Deployment's `spec.replicas` to zero. Reconcile and verify the pods are gone.
Repeat the LAN HTTP and OIDC redirect checks above. In the existing signed-in
session, create a disposable draft through the Editor, reopen it, edit it
and read it back. Record the draft response codes and changed revision.
Workers stop polling while the app continues to serve. Revert the temporary
replica change through Git and verify the worker returns to Ready and resumes
polling.

```sh
flux reconcile kustomization studio-worker -n ai --with-source
kubectl -n ai rollout status deploy/studio-worker --timeout=5m
test -z "$(kubectl -n ai get pods -l app.kubernetes.io/name=studio-worker -o name)"
kubectl -n ai rollout status deploy/studio-api --timeout=5m
```

## Upgrade and rollback

For the next version, use one successful Studio image build. Check the revision
and both digests anonymously. Update the API, MCP, worker and both Jobs to the
same API digest. Update the web digest, both build revision settings, the
worker's provenance digest and its model licence register from that revision.
Update each Job name and the corresponding Flux health check together.
For a changed Job template or an exhausted retry at the same image digest,
use the digest-based name with a new suffix such as `-r2`.

Check that old API pods can serve while the schema upgrade runs. Quiesce the
queue before changing build revisions and drain the current worker. API
replicas roll independently of a registration failure. The replacement worker
requires both successful Job gates and the healthy app at the new Git
artifact revision. A failed migration leaves the existing app serving.
Investigate the failure and use a new digest-named Job for a forward fix.
Preserve the credential during routine image rollbacks. The token's
ExternalSecret uses `creationPolicy: Orphan` and, like its Password generator, is
exempt from Flux pruning, so removing either from Git or deleting the
ExternalSecret leaves the Secret in place. Re-creating the ExternalSecret mints a
new value, which is a rotation: follow the steps below. The registration Job passes
`--replace`, so re-running it with the same token changes nothing.

To rotate the worker token on purpose: scale the worker to zero in GitOps; delete
the Secret `ai/studio-worker-token` (External Secrets then mints a new value);
give the registration Job a new name with the next `-rN` suffix and update the
Flux health check that names it, in one PR; reconcile. The new Job revokes the
old token and registers the new one; restore the worker's replica once the Job is
Complete. Until that Job has run, a worker started with the new value is refused
with 401, which is the safe failure.

To pause builds, use the zero-replica GitOps change above and keep the queue,
registration and storage. To restore the pre-OP956 app before its migration
has committed, the coordinator can revert the OP956 squash commit in Git and
reconcile `cluster-apps`. Verify the OP434 pods and probes and preserve the
stage directory. After Alembic has reached `0003_studio_run_queue`, keep that
schema and the healthy migration gate. The OP434 image cannot migrate a
database at `0003`. Roll back the app behavior using a reviewed compatible
Studio build that carries the applied Alembic revision. Promote its full
image set using the normal ordering and re-run the probes. Preserve the
NFS store, build stage, database and credential.
