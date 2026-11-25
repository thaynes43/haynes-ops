# Studio LAN deployment

[OP434](https://work.haynesnetwork.com/work_packages/434) installed Studio in
`ai`. [OP956](https://work.haynesnetwork.com/work_packages/956) adds the queue worker
and its credential registration. [OP957](https://work.haynesnetwork.com/work_packages/957)
pins the release with the finished-production importer and adds two imports and a
scoped playback proof. The OP957 rollout and proof below are **Expected** until
the coordinator records their results.

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
read `/index.html`. MCP probes check its TCP listener. The
[LAN route check](#gatus-and-backup-coverage) runs from Gatus.

The worker runs one `studio-worker` replica on `talosw01` with `Recreate`
and the `dev-env-agent` priority class. It shares the API's UID and GID.
Both processes mount `data/studio/build-stage-v1` from the same export at
`/build-stage`, read-write, as `STUDIO_BUILD_STAGE_ROOT`. The worker builds
there and the API validates and publishes the resulting delivery. Both use the
same `STUDIO_BUILD_REVISION=git:<commit>`. The worker records the API image
digest as `STUDIO_BUILD_IMAGE_DIGEST`. The API and worker mount the same
Kustomize ConfigMap, `studio-model-licences`, copied from the pinned release.
The API reads `docs/reference/model-licences.json` relative to its
`/opt/studio` working directory. The worker reads
`STUDIO_MODEL_REGISTER=/etc/studio/model-licences.json`.

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

On a fresh install, the main [rollout](#reconcile-and-verify) applies the
ConfigMap with the app. For an existing deployment, once the Studio migration
dependency is Ready at the chosen Git revision, reconcile just `ai/studio`
if the ConfigMap is missing or out of date. The existing OP435 deployment
requires only the verification steps.

```sh
# On initial installation or to reconcile a missing/outdated ConfigMap
flux reconcile kustomization studio -n ai --with-source

# Verify the rendered config and a fresh probe result
kubectl -n ai get configmap studio-gatus-ep -o jsonpath='{.data.config\.yaml}'
kubectl -n observability exec -i gatus-0 -c configmap-sidecar -- python - <<'PY'
import json
from urllib.error import HTTPError
from urllib.request import urlopen

with urlopen("http://127.0.0.1/api/v1/endpoints/statuses", timeout=10) as response:
    studio = next(entry for entry in json.load(response)
                  if entry["group"] == "internal" and entry["name"] == "studio")
    result = max(studio["results"], key=lambda entry: entry["timestamp"])
    print("internal/studio", result["timestamp"], result["success"])
    assert result["success"]
try:
    with urlopen("https://studio.haynesops.com/v1/me", timeout=10) as response:
        raise SystemExit(f"Expected anonymous HTTP 401, got {response.status}")
except HTTPError as error:
    print("/v1/me", error.code)
    assert error.code == 401
PY
```

Check that the result timestamp follows the reconcile on a fresh install
and is within two probe intervals of verification. An empty history on
first load needs one check interval. OP435 saw passing checks at 01:59,
02:00 and 02:01 UTC on 2026-11-25 and observed 401 at `/v1/me`, 200 at `/`
and 404 at `/healthz` from the Gatus pod.

OP435 found no Studio PVC in `ai` and no claim mounts on `studio-api`,
`studio-web`, `studio-mcp`, `studio-worker` or `studio-render`. API, worker
and render use raw NFS volumes. VolSync has no Studio claim to snapshot.
To recheck after a storage change, list the namespace claims and workload
volume references:

```sh
kubectl -n ai get pvc
kubectl -n ai get deploy studio-api studio-web studio-mcp studio-worker studio-render \
  -o 'custom-columns=NAME:.metadata.name,CLAIMS:.spec.template.spec.volumes[*].persistentVolumeClaim.claimName,NFS:.spec.template.spec.volumes[*].nfs.path'
```

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
at 346 MiB and 173 MiB respectively. That upload implementation is outside this
pin. The `ai` namespace had no ResourceQuota at OP957 preflight. Its
`default-cpu-request` LimitRange set a default CPU request of 50m.

CR-22 divides D18's 24 vCPU limit into 20 for `studio-render` and 4 for
`studio-worker`. Both use the same low priority. The worker runs one build
at a time with two media threads. Its CPU request matches the render pod's
250m scheduling share. At preflight the idle render pod used 3m CPU and
16 MiB. The existing serial mix reported 195 MB peak memory. The worker
request and limit are provisional allowances for the Python process,
decoded frame buffers and media subprocesses. Sample worker CPU, memory
and restarts during the first authorized build.
[Studio mix measurements](https://github.com/thaynes43/haynes-studio/blob/c75086b85a3278ead1d42b7a26da3b70da8b5793/docs/reference/studio-mix.md)

## Before the initial install

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

For the OP957 pin and imports, follow the [archive rollout](#roll-out-the-archive-imports).

After the initial installation merge, track the same `main@sha1:<merge SHA>` through the Flux
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
sign-out. The [archive playback Job](#roll-out-the-archive-imports) captures scoped
HTTP evidence. Record the signed-in phone playback and creative verdict
separately. Verify OP956's worker below. The
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

The OP957 imports publish finished deliveries for playback. Their input pins have
metadata only, and the recovered Winter Menu run has no board snapshot. For a
separately authorized build fixture, supply its inputs through the existing asset
paths and pin the resulting versions in a buildable draft. Use
`GET /v1/stories/<story-id>/estimate` to check it before queueing. With an
authorized caller, send `POST /v1/stories/<story-id>/runs` with the current
`If-Match` and a fresh `Idempotency-Key`. Read the run until the claim and
any normal Wan refusal are visible. For a refusal, record the return to
`queued`, its retry time and the same run's subsequent claim after Wan clears.
[Private input importer](https://github.com/thaynes43/haynes-studio/blob/f30445ff312bcdb04519765e9000a5fe1295c223/docs/reference/private-input-import.md)

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
same API digest. Update the published pair in `studio/release.yaml`, both build
revision settings, the worker's provenance digest and the shared model licence
register. Update each changed Job name and its Flux health check together.
Retire the OP957 one-shot Job graph and its release-guard entries together
after recording proof and revoking the two keys.
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

## Roll out the archive imports

**Expected** for OP957. Use the published [Studio images run](https://github.com/thaynes43/haynes-studio/actions/runs/45534812883)
for `f30445ff312bcdb04519765e9000a5fe1295c223`. The API, MCP, worker and every
Job use the Python image digest in [the release record](../../kubernetes/main/apps/ai/studio/release.yaml).
The web uses the matching web digest. An anonymous GHCR pull verified both
digests and both image revision labels.

Keep `STUDIO_GO_LIVE_APPROVER_SUBJECTS='[]'`. An authorized signed-in person
with a valid CSRF header receives 403 `approval.approver_not_configured`.
A Studio agent key receives 403 `approval.approver_not_permitted` before the
run checks. An imported run has a legacy marker and an otherwise eligible
approver receives 422 `approval.legacy_run`. The API uses the shared, read-only
licence register described above. No additional go-live environment value is
needed.

Quiesce the queue and let the worker finish its current run before changing
the build revision. Check the existing database and OIDC projections and
the LAN route before reconciliation. While the migration runs, check from
the LAN that the old app still serves 200 at `/` and 401 at `/v1/me`.

The cluster reconciles `ai/studio-migration` after the database and OIDC
dependencies. The application rollout and worker registration follow the
migration. The worker also waits for the existing `studio-render` HelmRelease
health check and the healthy app. The old API replicas serve during migration.

The independent `studio-archive-access` Kustomization installs the policies
for both imports, key registration and verification before the Jobs start.
The migration and network policy stage gate One More Meeting. Its completed
Job and the migration gate The Winter Menu. Both completed imports and the
healthy app gate key registration. The proof Job waits for key registration.
Use the same applied Git revision for every Flux gate. A failed import stops
the dependent steps while the app's own migration-based rollout can continue.

From the LAN, run these commands before and during the migration.

```sh
test "$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' https://studio.haynesops.com/)" = 200
test "$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' https://studio.haynesops.com/v1/me)" = 401
```

From the coordinator session, use the following commands to reconcile and
record evidence. Flux may begin dependent stages automatically. Once proof
and registration begin, keep them together within the keys' expiry window.

```sh
set -eu
suffix=1aa9b4736da9
flux reconcile kustomization cluster-apps -n flux-system --with-source
kubectl -n ai wait --for=condition=Ready --timeout=2m \
  externalsecret/studio-migrator-db externalsecret/studio-runtime-db externalsecret/studio-oidc-client
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

flux reconcile kustomization studio-archive-access -n ai
for archive in one-more-meeting winter-menu; do
  flux reconcile kustomization "studio-import-$archive" -n ai
  kubectl -n ai wait --for=condition=complete --timeout=5m "job/studio-import-$archive-$suffix"
  kubectl -n ai logs "job/studio-import-$archive-$suffix" -c import
done
flux reconcile kustomization studio-playback-registration -n ai
flux reconcile kustomization studio-playback-proof -n ai
kubectl -n ai wait --for=condition=complete --timeout=2m "job/studio-verify-playback-$suffix"
kubectl -n ai logs "job/studio-verify-playback-$suffix" -c verify
flux get kustomizations -n ai
kubectl -n ai get pod -l 'app.kubernetes.io/name in (studio-api,studio-web,studio-mcp,studio-worker)' \
  -o 'custom-columns=NAME:.metadata.name,NODE:.spec.nodeName,READY:.status.containerStatuses[0].ready,IMAGE:.status.containerStatuses[0].imageID'
```

Expect Alembic `0004_go_live_approvals`, migration TLS and HBA checks, and
the unchanged worker token registration result. Record the import JSON
lines with run and delivery IDs, counts, sizes and hashes. Expect 10 files
and 10,102,482 bytes for One More Meeting and five files and 73,638,915 bytes
for The Winter Menu. Their ConfigMaps contain the exact two `files` JSON
objects from [the OP942 receipt](https://github.com/thaynes43/haynes-studio/blob/f33073c4a29cd0ea27efe6e452198ff753e8ac62/titles/one-more-meeting/run-evidence.json).
The release guard and the import Jobs check each receipt's text SHA-256.
The importer verifies every archive member independently.

The proof dials the internal Traefik HTTPS Service with the Studio host in
TLS and HTTP. It checks the certificate and uses the pinned media identities.
Expect a scoped production list, imported run marker and delivery for each
key, a 200 HEAD and a 206 open-ended whole proxy, first, past-midpoint and suffix ranges. The
partial bytes must equal slices of the verified whole proxy. One More Meeting
must match 1,789,344 bytes and
`63dbb1e26287360ed6c4a0422dbdbfa250ad981c74e9719133d4dfe72d809d1b`.
Both masters and the Winter Menu proxy must match their delivery records and
archive receipts. Expect 404 for the other production's key and for an
unknown hash, 401 without a key, and 403 for approval, draft edit and run
queue with each key. Record the Job's JSON of statuses, ranges, lengths and
hashes separately from [phone playback](https://work.haynesnetwork.com/work_packages/944)
and Tom's verdict.

### Retry an import or proof

Each import mounts the archive read-only and publishes create-only into
`api-store-v1`. A receipt, identity or pre-publication check failure leaves the
database and store intact. A store or commit failure can leave complete,
unreferenced blobs and staging files. The final database transaction rolls
back the affected rows on failure. Keep the original archive and receipts,
leave the store's retention in place and investigate any conflict.
After correcting the cause, give the failed Job a new `-rN` name and update
its Flux health check in Git. Preserve the digest, receipt and source
directory basename. A repeat of a committed import verifies the published
blobs and reports `already_imported` with exit code 0.

The admin command accepts an absolute future expiry and has no minimum TTL.
The registration Job uses five minutes to cover the dependent proof's
two-minute deadline and Flux scheduling. If registration partially succeeds
or the proof expires, revoke any registered IDs first. For a repeat, change
both registration and proof Job names and gates to the next `-rN` suffix and
give each Password generator, ExternalSecret and target Secret a fresh name.
Update the two Jobs' projections in the same Git change. Reusing a revoked key
is refused. New proof keys still grant only `read` on their respective
production.

### Revoke the playback keys

After proof succeeds or stops, revoke the key IDs reported by the registration
Job using the migrator projection. The following one-off coordinator Job uses
the same pinned image and restricted DB policy and mounts only its scratch
directory. Leave the archived films, registered worker token and media intact.

```sh
set -eu
suffix=1aa9b4736da9
registration="studio-register-playback-$suffix"
revoke="studio-revoke-playback-$suffix"
ids=$(kubectl -n ai logs "job/$registration" -c register | jq -Rsc '
  split("\n") | map(fromjson? | select(keys == ["key_id"]) | .key_id) | unique
')
jq -en --argjson ids "$ids" '
  $ids | length >= 1 and length <= 2 and
  all(.[]; type == "string" and test("^key_[0-7][0-9A-HJKMNP-TV-Z]{25}$"))
' >/dev/null
kubectl -n ai get job "$registration" -o json | jq --arg name "$revoke" --argjson ids "$ids" '
  {
    apiVersion: "batch/v1", kind: "Job",
    metadata: {name: $name, namespace: "ai"},
    spec: {
      activeDeadlineSeconds: 120, backoffLimit: 0,
      template: {
        metadata: {labels: {"app.kubernetes.io/name": "studio-playback-registration"}},
        spec: .spec.template.spec
      }
    }
  }
  | .spec.template.spec.volumes |= map(select(.name != "keys"))
  | .spec.template.spec.containers[0] |= (
      .name = "revoke"
      | .args = ["for id in \"$@\"; do studio-api-admin revoke \"$id\"; done", "revoke"] + $ids
      | .volumeMounts |= map(select(.name != "keys"))
    )
' | kubectl create -f -
kubectl -n ai wait --for=condition=complete --timeout=2m "job/$revoke"
kubectl -n ai logs "job/$revoke" -c revoke
```

For a retried registration, set `registration` to its actual Job name and use
a fresh `revoke` Job name. Each result reports the key ID and `revoked_at`.
A failed registration with zero issued IDs needs no revocation Job. The old
keys also expire automatically. The ESO generators and ExternalSecrets are
exempt from Flux pruning and their target Secrets are orphaned. Revocation
removes access even while those resources remain.

### Roll back the OP957 pin

Before `0004_go_live_approvals` commits, the coordinator can revert the OP957
squash commit and reconcile `cluster-apps`. After it commits, retain the schema
and use a corrective GitOps PR. Stop any pending proof stages and revoke issued
keys while the registration metadata and DB policy are available. Remove the
one-shot OP957 Kustomizations, their manifests and their release-guard entries
together. Keep the empty go-live approver list and matching register.

A compatible pre-import image pair is the successful
[Studio images run for `df6929a`](https://github.com/thaynes43/haynes-studio/actions/runs/45533416204).
Use revision `df6929affb2c4dd4849a28d9b962a84fd66c14ae`, the API digest
`sha256:ef46ff3d9e49c89c0f0bd1eee0490a596556a2a4f56b61fada8462b230b1cf21`
and web digest
`sha256:67c22c46b2d656e28ac7a1c45e546307534cfc34f5ffdd6e29622439778beff5`.
Both resolved anonymously under that revision. Update the Job names and
health checks with the pin, reconcile migration, app, registration and worker
in the same order, and verify `0004_go_live_approvals`, app readiness and LAN
responses. The earlier OP956 migration image cannot resolve revision `0004`.

This rolls back the running code and reconciles its workload configuration.
The imported rows, hashes and content-addressed blobs stay in PostgreSQL and
the store. The read-only originals on NFS remain intact. A pin revert also
leaves database key grants in place until expiry or explicit revocation.
