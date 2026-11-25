# Stage Studio still matting

CR-33 selects BiRefNet Lite FP32 on ONNX Runtime CPU. Provisioning and
the first two smoke attempts ran on 2026-11-25. CR-38 stages the new image,
a fresh immutable profile allocation and the CPU-arena retest. Flux owns
the previously registered network policies and provisioner ConfigMap.
The Jobs, worker sidecar and render resource split stay staged. Launch each
Job individually after its checks pass. [OP459](https://work.haynesnetwork.com/work_packages/459)

OP459 authorizes a one-shot exception to Flux ownership for these two
unprivileged Job objects. The saga coordinator runs the reviewed Git
templates and waits at each health gate. Flux owns the targeted policies and provisioner
ConfigMap. The provisioner leaves a verified, create-only allocation on
the private Studio share, and both Job objects expire after 24 hours.
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
   sidecar. The release check refuses the previous installed digest and
   reads the resource profile from the pinned image's OCI config. Both
   the rendered smoke and sidecar must match its request and limit.
   Merge the Ops PR after its check passes. Run the following blocks
   from that checked-out Ops revision.
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

The image digest and serving settings are part of the immutable profile.
Provision the same pinned graph into allocation
`matting-op459-birefnet-lite-v1-02` for the new image. Preserve the first
allocation as the installed baseline. Check that the jobs root has at
least 33 GiB free. The provisioner
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

Use an exclusive heavy-job window on `talosw01`. Check render and Wan
activity and node pressure before dispatch. Run `studio-matting-smoke-02-r3`
once after the new pin's read-back. The Job requests 14 GiB and limits the
container to 2 CPUs and 16 GiB. It has a 300 s deadline, no retries, no
network, read-only root and weights, and a 256 MiB private scratch. It runs
as 10001:10001. This run changes only the CPU arena. Capture the report
including phase peaks, post-request RSS and fixture hash matches. Capture
logs and pod termination state if the Job is killed before it reports.

```bash
(
  set -euo pipefail
  stage=kubernetes/main/apps/ai/studio/matting-pending
  allocation=$(yq '.data.allocation' "$stage/release/release.yaml")
  provision="studio-matting-provision-${allocation##*-}"
  kubectl -n ai wait --for=condition=complete "job/$provision" --timeout=5s
  profile=$(kubectl -n ai logs "job/$provision" | jq -er '.profile_sha256')
  pin=$(yq '.data.image' "$stage/release/release.yaml")
  memory_limit=$(yq '.data.memoryLimit' "$stage/release/release.yaml")
  limit_bytes=$(( ${memory_limit%Gi} * 1024 * 1024 * 1024 ))
  kubectl top node talosw01
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
    jq -ce --arg profile "$profile" --arg digest "${pin##*@}" --argjson limit "$limit_bytes" '
      select(.passed and .pinned_profile_sha256 == $profile and .image_digest == $digest and
        .provider == "CPUExecutionProvider" and .memory_limit_bytes == $limit and
        .session_settings.cpu_arena == false and .verdicts.memory_phases_measured and
        .verdicts.installed_artifacts_match)
    '
)
```

**Measured** on 2026-11-25. The first Job was OOMKilled after 14 s
under the unmeasured 6 GiB budget. Retry `studio-matting-smoke-01-r2`
peaked at 12,997,890,048 bytes in the worker and 13,150,916,608 bytes
for the sum of process peaks. Fixture quality and replay passed. The
14 GiB request and 16 GiB limit use that measured baseline. The new
runtime peak and byte parity are pending the retest. Reject a candidate on
any cutout or mask hash difference. If the retest fails the 16 GiB memory
gate, hand back to the coordinator for a new ruling. [OP459, comment 9969](https://work.haynesnetwork.com/work_packages/459)

Record the Ops Git revision, both Job names and statuses, published image
digest, verified graph and profile hashes, smoke JSON and deployed health
on OP459. Record the private
allocation in the story handoff. The synthetic result establishes installed
smoke behavior. [OP451](https://work.haynesnetwork.com/work_packages/451)
owns the private cast quality and serving integration. Tom owns acceptance.

## Activation, retries and rollback

Read-only accounting on 2026-11-25 showed 105.98 GiB allocatable and
54.29 GiB requested, including Wan (24 GiB requested, 64 GiB limit),
Foley (12 GiB, 32 GiB) and their API (128 MiB, 512 MiB). Adding the
dormant local-model sidecar (8 GiB, 16 GiB) and one matting instance
(14 GiB, 16 GiB) leaves 29.69 GiB unrequested. Existing limits sum
to 239,904 MiB. At their limits, Wan, local model, the Studio queue
worker and matting alone consume 104 GiB before offline render, Foley
or other workloads. The node cannot carry those peaks simultaneously.
Keep heavy jobs serialized unless measured headroom shows the second
fits. Follow the Wan, Foley and local-model lease rules.
[Matting profile](https://github.com/thaynes43/haynes-studio/blob/main/docs/reference/matting-backend-profile.md)

Story 451 can promote the dormant overlays after the smoke and proof
that commands fit the reduced renderer limit. First move the offline
worker's Flux path to `studio-render/matting-pending` and verify its
1 GiB request and 18-CPU, 16 GiB limit. Cap offline render commands at
`--workers 16`. Then stage the queue consumer with
`studio/matting-pending/activation`. The worker has 4 CPUs, 2 GiB
requested and an 8 GiB limit. The matting sidecar has 2 CPUs,
14 GiB requested and a 16 GiB limit. The two pods total 24 CPUs. The
serving command retains the established ORT settings. Adopting the
measured candidate requires the passing byte comparison. The sidecar uses a
shared, group-writable `/matting` emptyDir and the read-only `/weights`
mount. Only the worker keeps its API credential and media mounts. Stage
per-attempt directories with shared GID 10001, permissions 2770, and an
input readable by that group. Check `studio-matting ready` in the
activated pod before sending any dispatch.

If provisioning fails, preserve the logs and failed allocation. Investigate
the code before deciding whether a fresh run is appropriate. A reviewed
change to `data.allocation` selects the next two-digit suffix for the two
Job names and their corresponding weights mount. Keep the original
allocation intact. After a smoke failure, retain the exact model and any
reported scores and record the coordinator's ruling before a new attempt.

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
