# Stage Studio still matting

**Expected** for [OP459](https://work.haynesnetwork.com/work_packages/459).
CR-33 selects BiRefNet Lite FP32 on ONNX Runtime CPU. The published Studio
image, one pinned weight file and three staged workloads are the release.
The Ops merge registers the targeted network policies and provisioner
ConfigMap. The provisioning and smoke Jobs are launched individually after
the preceding checks pass. The worker sidecar and the render CPU change
remain outside Flux's applied paths. Existing worker and render pods do
not roll during this install.
[Matting profile](https://github.com/thaynes43/haynes-studio/blob/main/docs/reference/matting-backend-profile.md)
· [Studio deployment](studio-deployment.md)

## Pin and admit the release

1. Merge the Studio source PR. In its successful **Studio images** run on
   `main`, use **Publish tested Studio images** to read
   `ghcr.io/thaynes43/haynes-studio-matting@sha256:<digest>` for that full
   commit. The synthetic build smoke proves the runtime and artifact path.
2. Set that full reference as `data.image` in
   [release.yaml](../../kubernetes/main/apps/ai/studio/matting-pending/release/release.yaml).
   It feeds the image and profile identity of both Jobs and the staged
   sidecar. The Studio release check refuses a tag or an unset digest.
   Merge the Ops PR after its check passes. Run the following blocks from
   that checked-out Ops revision.
3. Reconcile and check the policy stage and the two existing workloads.
   The download pod can reach only DNS and HTTPS for `huggingface.co`
   and `us.aws.cdn.hf.co`. The smoke pod has no permitted traffic.

```bash
(
  set -euo pipefail
  flux reconcile kustomization cluster-apps -n flux-system --with-source
  flux reconcile kustomization studio-matting-policy -n ai
  kubectl -n ai wait --for=condition=ready kustomization/studio-matting-policy --timeout=2m
  kubectl -n ai get ciliumnetworkpolicy studio-matting-provision studio-matting-smoke
  kubectl -n ai get deploy studio-worker studio-render -o wide
)
```

## Provision and read back the weight

Check the jobs allocation exists and has at least 33 GiB free. The provisioner
checks the 1 GiB budget and 32 GiB free reserve before and during writes. CR-33
accepts a 32-inode allocation here without an NFS free-inode reading. The
Job writes as 1000:1000, with only `data/studio/jobs` mounted read-write.
One atomic `mkdir` claims a new private allocation. The Job hashes the file
read back from its staged bytes, syncs its profile, checksum and licence,
then renames within the exclusively allocated directory. The published
subpath is read-only in consumers. A failed allocation is retained and
never reused.

```bash
(
  set -euo pipefail
  stage=kubernetes/main/apps/ai/studio/matting-pending
  provision=$(kustomize build "$stage/provision")
  job=$(yq 'select(.kind == "Job") | .metadata.name' <<< "$provision")
  config=$(yq 'select(.kind == "Job") | .spec.template.spec.volumes[] |
    select(.name == "config") | .configMap.name' <<< "$provision")
  kubectl -n ai get "configmap/$config" -o name
  kubectl -n ai exec deploy/studio-render -c app -- sh -ec '
    test -d /studio/jobs
    df -Pk /studio/jobs | awk "NR == 2 { print; if (\$4 >= 34603008) good = 1 } END { exit !good }"
  '
  yq 'select(.kind == "Job")' <<< "$provision" | kubectl -n ai create -f -
  if ! kubectl -n ai wait --for=condition=complete "job/$job" --timeout=6m; then
    kubectl -n ai get "job/$job" -o wide
    kubectl -n ai logs "job/$job"
    exit 1
  fi
  kubectl -n ai logs "job/$job" | jq -e '
    .graph_bytes == 224005088 and
    .graph_sha256 == "5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333" and
    (.profile_sha256 | test("^[0-9a-f]{64}$"))
  '
  allocation=$(yq '.data.allocation' "$stage/release/release.yaml")
  profile=$(kubectl -n ai logs "job/$job" | jq -er '.profile_sha256')
  kubectl -n ai exec -i deploy/studio-render -c app -- python -B - "$allocation" "$profile" <<'PY'
from hashlib import file_digest
from pathlib import Path
import json
import sys

weights = Path("/studio/jobs") / sys.argv[1] / "weights"
assert (weights / "model.onnx").stat().st_size == 224005088
with (weights / "model.onnx").open("rb") as graph:
    graph_hash = file_digest(graph, "sha256").hexdigest()
with (weights / "profile.json").open("rb") as manifest:
    profile_hash = file_digest(manifest, "sha256").hexdigest()
assert graph_hash == "5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333"
assert profile_hash == sys.argv[2] == (weights / "profile.sha256").read_text().strip()
print(json.dumps({"graph_sha256": graph_hash, "profile_sha256": profile_hash}))
PY
)
```

## Run the install smoke

Use a window with no overlapping heavy offline render on `talosw01`. Keep
the queue worker's four-CPU allocation available. Run once after the above
read-back. The smoke has a 300 s deadline, no retries, no network,
read-only root and weights, and a 256 MiB private scratch. It runs as
10001:10001. It requests 2 GiB of memory and limits the job to 2 CPUs and
6 GiB. The reported metrics include fixture regions, per-request timing,
cold load and peak RSS. Capture the report from a failed run too.

```bash
(
  set -euo pipefail
  stage=kubernetes/main/apps/ai/studio/matting-pending
  allocation=$(yq '.data.allocation' "$stage/release/release.yaml")
  provision="studio-matting-provision-${allocation##*-}"
  kubectl -n ai wait --for=condition=complete "job/$provision" --timeout=5s
  profile=$(kubectl -n ai logs "job/$provision" | jq -er '.profile_sha256')
  pin=$(yq '.data.image' "$stage/release/release.yaml")
  kubectl -n ai top pod --containers -l app.kubernetes.io/name=studio-render
  smoke=$(kustomize build "$stage/smoke")
  job=$(yq '.metadata.name' <<< "$smoke")
  kubectl -n ai create -f - <<< "$smoke"
  if ! kubectl -n ai wait --for=condition=complete "job/$job" --timeout=6m; then
    kubectl -n ai get "job/$job" -o wide
    kubectl -n ai logs "job/$job"
    exit 1
  fi
  kubectl -n ai get "job/$job" -o wide
  kubectl -n ai logs "job/$job" |
    jq -ce --arg profile "$profile" --arg digest "${pin##*@}" '
      select(.passed and .profile_sha256 == $profile and .image_digest == $digest and
        .provider == "CPUExecutionProvider")
    '
)
```

Record the revision, published digest, verified graph and profile hashes,
Job status, smoke JSON and deployed health on OP459. Record the private
allocation in the story handoff. The synthetic result establishes installed
smoke behavior. [OP451](https://work.haynesnetwork.com/work_packages/451)
owns the private cast quality and serving integration. Tom owns acceptance.

## Activation, retries and rollback

Story 451 can promote the two dormant overlays after the smoke. First
move the offline worker's Flux path to `studio-render/matting-pending` and
verify the 18-CPU, 32 GiB limit. Cap offline render commands at
`--workers 16`. Then stage the queue consumer with
`studio/matting-pending/activation`: the queue keeps its four-CPU limit
and matting adds two, so the two pods total 24 CPU. The sidecar uses a
shared, group-writable `/matting` emptyDir and the read-only `/weights`
mount. Only the worker keeps its API credential and media mounts. Stage
per-attempt directories with shared GID 10001, permissions 2770, and an
input readable by that group. Check `studio-matting ready` in the
activated pod before sending any dispatch.

If provisioning fails, preserve the logs and failed allocation. Investigate
the code before deciding whether a fresh run is appropriate. A reviewed
change to `data.allocation` selects the next two-digit suffix for the two
Job names and their corresponding weights mount. Keep the original
allocation intact. If only the smoke fails, retain the exact model and scores
while diagnosing. Alternate weights require a new ruling.

After both Jobs finish, capture their evidence before the 24-hour Job TTL
removes their objects and pods. The coordinator can remove the
`studio-matting-policy` Flux object and the provision/smoke templates in a
follow-up GitOps PR. Update their release guard with the removal. Pruning
removes the ConfigMap and targeted policies. Preserve the immutable
allocation, selected pin and staged serving overlay.

To roll back before activation, withdraw the one-shot stage in Git and stop
launching Jobs. Let an existing Job reach its bound and preserve any data
it wrote. After activation, disable dispatch first and revert the Studio
worker's Flux path before restoring the offline 20-CPU path. Preserve the
weights and artifacts regardless of the rollback. Reconcile and verify the
previously authorized workloads after the Git changes.

```bash
(
  set -euo pipefail
  flux reconcile kustomization cluster-apps -n flux-system --with-source
  kubectl -n ai get kustomization studio-worker studio-render
  kubectl -n ai get deploy studio-worker studio-render -o wide
  kubectl -n ai get deploy studio-worker studio-render -o json | jq -e '
    .items | length == 2 and all(.[]; .status.availableReplicas >= 1)
  '
)
```
