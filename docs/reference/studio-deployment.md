# Studio LAN deployment

[OP434](https://work.haynesnetwork.com/work_packages/434) installs Studio in
`ai`. `https://studio.haynesops.com` uses the internal Traefik tier, the
`internal.haynesops` DNS target and the namespace's wildcard certificate.
Traefik sends `/v1/` and `/auth/` to the API and UI paths to nginx. The
probes, worker paths and MCP paths have no ingress route. A NetworkPolicy
allows internal Traefik pods to reach the API and web ClusterIP Services.
The auth route allows a burst of 10 requests and refills at 30 per minute per
observed peer address on each Traefik replica. The internal LoadBalancer
may combine clients behind a shared peer address.

MCP listens at `http://studio-mcp.ai.svc.cluster.local:8001/mcp`. The
Service accepts callers from pods in `ai` and `dev-agents`, subject to their
existing egress policies. MCP sends authenticated requests to
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
mounted at `/studio` in the API container only. The API runs as
`1000:1000`, the existing NFS writer's IDs. Its startup command checks
exclusive create, fsync, hard-link no-clobber and read-back under
`.probe/` before serving. Kubernetes uses `/healthz` for process
liveness. The readiness probe tolerates the first `/readyz` response
of 503 while the store check runs. It also checks PostgreSQL. Web probes
read `/index.html`. MCP probes check its TCP listener. Gatus uses the shared internal component and checks
`/v1/me` on the LAN origin, where a healthy anonymous API returns 401.

D47 excludes NFS media from backups. The NFS server is outside the
cluster and remains a shared dependency of all API replicas. PostgreSQL
uses the existing [CNPG backup coverage](studio-database.md#backup-and-rollback).

## Initial resource settings

The built images were measured in a disposable local smoke using the packaged
executables and PostgreSQL 16. The smoke sampled process RSS and CPU with a
small number of serial probes. Requests round up the observed memory with
startup and request headroom. Memory limits are two times the requests. CPU
requests give cold starts a scheduling share and leave bursts unthrottled.
Use installed metrics to revisit the sizing after representative use.

| Workload | Measured RSS | Mean CPU in smoke | CPU request | Memory request / limit |
| --- | ---: | ---: | ---: | ---: |
| API | 127.5 MiB | 1.5m | 100m | 160 MiB / 320 MiB |
| MCP | 63.1 MiB | 1.5m | 50m | 96 MiB / 192 MiB |
| Web | 9.4 MiB | below sampling resolution | 25m | 16 MiB / 32 MiB |
| Migration Job | 74.3 MiB | 1.14 CPU-seconds in 9.99 s | 100m | 96 MiB / 192 MiB |

## Before merging the installation PR

The coordinator runs the rollout. Confirm `database/cloudnative-pg-cluster`
has all three Postgres instances ready, `ai/studio-database` and
`ai/studio-oidc` are ready, and their projections report Ready. Confirm
the schema and ACL owner bootstrap in [Studio database](studio-database.md#reconciliation-and-owner-bootstrap).
Check the Authentik application advertises the per-provider HTTPS issuer and
the redirect URI `https://studio.haynesops.com/auth/callback`. Check
at least three eligible nodes and the existing LAN TLS and DNS path.

Create the empty store directory once using the Studio render pod's
existing write access. A collision needs investigation before rolling out.
The command's parent is the Studio share and the dedicated child's mode
is `0700`.

```sh
kubectl -n ai exec deploy/studio-render -c app -- sh -eu -c '
  test "$(id -u):$(id -g)" = 1000:1000
  umask 077
  mkdir /studio/api-store-v1
  stat -c "%u:%g %a %n" /studio/api-store-v1
'
kubectl -n ai wait --for=condition=Ready --timeout=2m \
  externalsecret/studio-runtime-db externalsecret/studio-migrator-db \
  externalsecret/studio-oidc-client
```

## Reconcile and verify

After merge, track the same `main@sha1:<merge SHA>` through the Flux
Kustomizations below. The reconciliations can also run automatically
once Flux sees the commit. Record Job success before recording the
application rollout.

```sh
flux reconcile kustomization cluster-apps -n flux-system --with-source
flux get kustomization cloudnative-pg-cluster -n database
flux get kustomizations -n ai
flux reconcile kustomization studio-migration -n ai
kubectl -n ai wait --for=condition=complete --timeout=5m job/studio-migrate-8de1942e806c
kubectl -n ai logs job/studio-migrate-8de1942e806c -c migrate
flux reconcile kustomization studio -n ai
kubectl -n ai rollout status deploy/studio-api --timeout=5m
kubectl -n ai rollout status deploy/studio-web --timeout=5m
kubectl -n ai rollout status deploy/studio-mcp --timeout=5m
kubectl -n ai get pods -l 'app.kubernetes.io/name in (studio-api,studio-web,studio-mcp)' -o wide
kubectl -n ai get pods -l 'app.kubernetes.io/name in (studio-api,studio-web,studio-mcp)' \
  -o 'custom-columns=NAME:.metadata.name,NODE:.spec.nodeName,READY:.status.containerStatuses[0].ready,IMAGE:.status.containerStatuses[0].imageID'
kubectl -n ai get pdb studio-api studio-web studio-mcp
kubectl -n ai get ingressroute studio
```

Expect six ready pods, with the two pods of each Deployment on different
nodes. Each budget should allow one disruption when both replicas are ready.
The Job logs must report a TLS login as `studio_migrator`, both HBA rejects
and Alembic revision `0002_active_agent_grants`. Run the TLS, HBA and privilege
checks in [Studio database](studio-database.md#credential-free-access-evidence)
with `verification_pod` set to a Studio API pod, `verification_container=api`
and the role argument `studio_app`.

Verify both API pods' live probes and their effective NFS path.

```sh
for pod in $(kubectl -n ai get pod -l app.kubernetes.io/name=studio-api -o name); do
  kubectl -n ai exec "$pod" -c api -- python -c '
import os
from pathlib import Path
from urllib.request import urlopen

assert (os.getuid(), os.getgid()) == (1000, 1000)
mount, = [line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines()
          if line.split()[4] == "/studio"]
assert mount[3].endswith("/data/studio/api-store-v1")
separator = mount.index("-")
assert mount[separator + 1] in ("nfs", "nfs4")
assert mount[separator + 2].startswith("gasha01.haynesnetwork:")
for path in ("/healthz", "/readyz"):
    with urlopen("http://127.0.0.1:8000" + path, timeout=6) as response:
        assert response.status == 204
print("1000:1000", mount[3], mount[separator + 1], "healthz=204 readyz=204")
'
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
statuses, scope and the fixture's hash. Queue and worker-loss testing
belongs to [OP437](https://work.haynesnetwork.com/work_packages/437).

## Upgrade and rollback

For the next version, use one successful Studio image build. Check the
revision and both digests anonymously, update the API, MCP and migration Job
to the same API digest, update the web digest, and update the Job name
and Flux health-check name together. Rename the Job if its pod template
changes at the same image digest. Keep the runtime grants aligned with
the source revision. Confirm old API pods can serve against the new
schema during the rollout. Flux requires a healthy migration Kustomization
at the exact Git artifact revision before updating the app. A failed Job
blocks that revision's app reconciliation and leaves the existing pods
and ingress serving. Fix a failed migration forward in Git using a fresh
Job name if the failed Job exhausted its retries.

To remove the initial install, create a rollback PR from current
haynes-ops `main` with `git revert <OP434 squash-merge SHA>`. Merge that
PR, then reconcile `cluster-apps` with source. Check that Flux prunes
both Studio Kustomizations and their inventory.

```sh
flux reconcile kustomization cluster-apps -n flux-system --with-source
kubectl -n ai get kustomizations,deployments,services,pdb,ingressroutes,jobs \
  | awk '$1 ~ /\/(studio$|studio-api$|studio-web$|studio-mcp$|studio-migration$|studio-migrate-)/'
```

Expect no output. Preserve `studio-database`, `studio-oidc`, the database
contents and the NFS store. For a future image rollback after a successful
schema upgrade, keep the healthy migration gate for the applied revision.
Use a reviewed Git change to restore only the known-compatible prior API, MCP
and web digests, then repeat the probes and auth smoke. If the prior API
is incompatible with the applied schema, recover forward. Production
schema downgrades and overwriting media are outside this procedure.
