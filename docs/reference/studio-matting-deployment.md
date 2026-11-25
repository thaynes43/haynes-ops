# Stage Studio still matting

CR-33 selects BiRefNet Lite FP32 on ONNX Runtime CPU. The baseline and
CPU-arena retest ran on 2026-11-25. CR-43 stages the new serving profile,
with the CPU arena off, in a fresh immutable allocation. Flux owns the
targeted policies and provisioner ConfigMap. The provision and smoke Jobs
are one-shot. OP967 owns the serving activation and CPU split. Launch each Job individually after
its checks pass. [OP459](https://work.haynesnetwork.com/work_packages/459)

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
   sidecar. The release check refuses both previously installed digests and
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
`matting-op459-birefnet-lite-v1-03` for the new image. Preserve both previous
allocations. Check that the jobs root has at least 33 GiB free. The provisioner
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
  provision=$(kustomize build "$stage/provision" | yq -e 'select(.kind == "Job")') || exit 1
  job=$(yq -e '.metadata.name' <<< "$provision") || exit 1
  config=$(yq -e '.spec.template.spec.volumes[] |
    select(.name == "config") | .configMap.name' <<< "$provision") || exit 1
  kubectl -n ai get "configmap/$config" -o name || exit 1
  kubectl -n ai exec deploy/studio-render -c app -- sh -ec '
    test -d /studio/jobs
    df -Pk /studio/jobs | awk "NR == 2 { print; if (\$4 >= 34603008) good = 1 } END { exit !good }"
  ' || exit 1
  kubectl -n ai create -f - <<< "$provision" || exit 1
  if ! kubectl -n ai wait --for=condition=complete "job/$job" --timeout=6m; then
    kubectl -n ai get "job/$job" -o wide
    kubectl -n ai logs "job/$job"
    exit 1
  fi
  report=$(kubectl -n ai logs "job/$job" | tail -n 1)
  jq -es '
    length == 1 and (.[0] |
      .graph_bytes == 224005088 and
      .graph_sha256 == "5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333" and
      (.profile_sha256 | test("^[0-9a-f]{64}$")))
  ' <<< "$report"
  allocation=$(yq '.data.allocation' "$stage/release/release.yaml")
  profile=$(jq -er '.profile_sha256' <<< "$report")
  kubectl -n ai exec -i deploy/studio-render -c app -- python -B - "$allocation" "$profile" <<'PY'
from hashlib import file_digest
from pathlib import Path
import json
import sys

weights = Path("/studio/jobs") / sys.argv[1] / "weights"
with (weights / "model.onnx").open("rb") as graph:
    graph_hash = file_digest(graph, "sha256").hexdigest()
with (weights / "profile.json").open("rb") as manifest:
    profile_hash = file_digest(manifest, "sha256").hexdigest()
checks = {
    "graph size": (weights / "model.onnx").stat().st_size == 224005088,
    "graph hash": graph_hash == "5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333",
    "profile hash": profile_hash == sys.argv[2] == (weights / "profile.sha256").read_text().strip(),
}
for name, passed in checks.items():
    print(f"{'PASS' if passed else 'FAIL'} {name}")
print(json.dumps({"graph_sha256": graph_hash, "profile_sha256": profile_hash}))
raise SystemExit(not all(checks.values()))
PY
)
```

## Run the install smoke

Use an exclusive heavy-job window on `talosw01`. Confirm the window is
clear of heavy render and Foley work and review node and render pod load
before running the block. It records fresh readings and gates Job
creation on Wan's `/v1/ready` reporting readiness and both video and Foley
idle, and the image engine's queue being empty. Before 2026-11-25 14:30Z,
the idle gates read only `/health`.
Run `studio-matting-smoke-03` once after the new pin's read-back. The Job
uses plain `studio-matting smoke` with the serving profile. It requests
8 GiB and limits the container to 2 CPUs and 10 GiB. It has a 300 s deadline,
no retries, no network, read-only root and weights, and a 256 MiB private
scratch. It runs as 10001:10001. Capture phase peaks, post-request RSS,
fixture hash matches and any failure logs.

```bash
(
  set -euo pipefail
  check_json() {
    local label=$1 predicate=$2
    shift 2
    if jq -es "$@" "length == 1 and (.[0] | ($predicate))" >/dev/null; then
      printf 'PASS %s\n' "$label"
    else
      printf 'FAIL %s\n' "$label" >&2
      return 1
    fi
  }
  read_endpoint() {
    local pod=$1 container=$2 url=$3
    kubectl -n ai exec "$pod" -c "$container" -- python -B -c '
import sys
from urllib.request import urlopen
with urlopen(sys.argv[1], timeout=5) as response:
    sys.stdout.buffer.write(response.read())
' "$url"
  }
  wan_idle() {
    read_endpoint deploy/wan-video api http://127.0.0.1:8080/v1/ready |
      check_json 'Wan idle' '.ready == true and
        has("active_video_job") and .active_video_job == null and
        has("active_foley_job") and .active_foley_job == null'
  }
  stage=kubernetes/main/apps/ai/studio/matting-pending
  allocation=$(yq -e '.data.allocation' "$stage/release/release.yaml") || exit 1
  provision="studio-matting-provision-${allocation##*-}"
  kubectl -n ai wait --for=condition=complete "job/$provision" --timeout=5s || exit 1
  profile=$(kubectl -n ai logs "job/$provision" | tail -n 1 |
    jq -ers 'select(length == 1) | .[0].profile_sha256 | select(test("^[0-9a-f]{64}$"))') || exit 1
  pin=$(yq -e '.data.image' "$stage/release/release.yaml") || exit 1
  kubectl top node talosw01 || exit 1
  kubectl -n ai top pod --containers -l app.kubernetes.io/name=studio-render || exit 1
  wan_idle || exit 1
  read_endpoint comfyui-0 app http://127.0.0.1:8188/queue |
    check_json 'Image engine queue empty' '.queue_running == [] and .queue_pending == []' || exit 1
  smoke=$(kustomize build "$stage/smoke") || exit 1
  job=$(yq -e '.metadata.name' <<< "$smoke") || exit 1
  kubectl -n ai create -f - <<< "$smoke" || exit 1
  if ! kubectl -n ai wait --for=condition=complete "job/$job" --timeout=6m; then
    kubectl -n ai get "job/$job" -o wide
    kubectl -n ai logs "job/$job"
    exit 1
  fi
  kubectl -n ai get "job/$job" -o wide
  report=$(kubectl -n ai logs "job/$job" | tail -n 1)
  printf '%s\n' "$report"
  status=0
  check_json 'Pinned serving profile and image' '
    .pinned_profile_sha256 == $profile and .profile_sha256 == $profile and .image_digest == $digest
  ' --arg profile "$profile" --arg digest "${pin##*@}" <<< "$report" || status=1
  check_json 'CPU provider with arena off' '
    .provider == "CPUExecutionProvider" and .session_settings.cpu_arena == false
  ' <<< "$report" || status=1
  check_json '10 GiB memory limit and measured phases' '
    .memory_limit_bytes == 10737418240 and .verdicts.memory_bounded == true and
    .verdicts.memory_phases_measured == true
  ' <<< "$report" || status=1
  check_json 'Installed source, cutout and mask hashes for every fixture and replay' '
    {source: true, cutout: true, mask: true} as $match |
    .verdicts.installed_artifacts_match == true and
    .installed_hash_matches == {bust: $match, green_eyes: $match, smaller_bust: $match, replay: $match}
  ' <<< "$report" || status=1
  check_json 'All smoke verdicts' '.passed == true' <<< "$report" || status=1
  exit "$status"
)
```

**Measured** on 2026-11-25. The first Job was OOMKilled after 14 s
under the unmeasured 6 GiB budget. Retry `studio-matting-smoke-01-r2`
peaked at 12,997,890,048 bytes in the worker and 13,150,916,608 bytes
for the sum of process peaks. Fixture quality and replay passed. The
retest `studio-matting-smoke-02-r3` passed all 13 verdicts with the CPU
arena disabled and byte-identical cutouts and masks. Its worker peaked at
7,538,561,024 bytes, with a 7,691,853,824-byte bound for the sum of process
peaks. CR-43 sets the new request and limit from these measurements. The
plain smoke with the pinned serving profile passed all 13 verdicts in
48 s with a 7,626,436,608-byte upper bound.
[OP459, comments 9969, 9985 and 10004](https://work.haynesnetwork.com/work_packages/459)

Record the Ops Git revision, both Job names and statuses, published image
digest, verified graph and profile hashes, smoke JSON and deployed health
on OP459. Record the private
allocation in the story handoff. The synthetic result establishes installed
smoke behavior. [OP451](https://work.haynesnetwork.com/work_packages/451)
owns the private cast quality and serving integration. Tom owns acceptance.

## Run the private cast quality trial

**Expected.** The saga coordinator chooses one existing cast photo of an
imported production under CR-46 and uses its read-only path and recorded
SHA-256. Use the content-addressed store path
`/studio/productions/<prd_id>/sha256/<first-two-hex>/<sha256>.<jpg|png>`
or `/studio/library/sha256/<first-two-hex>/<sha256>.<jpg|png>` for global
scope. Choose a representative still with eyes, lashes and hair visible,
and use the target height for its pinned cast slot. Keep the path, asset
IDs and output locator private. Mark the iris interiors and visible hair
before the trial. Run in an exclusive heavy-job window as for the smoke,
including no concurrent Foley or heavy render. The block checks fresh
node load, render load, Wan readiness and idleness for video and Foley,
and the shared image queue.

The staged template emits one Job with inline scripts. Its dedicated
`studio-matting-quality` deny-all policy must be live before admission.
The init container uses UID/GID 1000 to reserve a new group-writable
`/studio/jobs/<job>` directory exclusively. The main container uses the
smoke's UID/GID 10001, pinned image and read-only weights. Supplemental
group 1000 allows it to write `cutout.png`, `mask.png` and the completed
`report.json` under just that directory's writable subPath. The input
directory has its own read-only subPath. The gate checks the canonical
path, regular single-link file and SHA-256 match before opening the run
window. The report identifies the input by SHA-256. The Job spec contains
the store's hash-based locator and expires one hour after finishing.
As in the provisioning budget, leave 32 GiB
free after allowing 1 GiB and 32 filesystem entries. The NFS inode
reading may be unavailable under CR-33.

The final log line reports invalid input, a hash mismatch, an existing
output, unavailable readiness, a service refusal, failed verification or
a timeout with distinct `trial.*` codes. Service refusals include the
backend's `adapter.*` code. If output reservation fails, the report comes
from the init container.

Set `STUDIO_TRIAL_INPUT` to the absolute Studio-share path,
`STUDIO_TRIAL_SOURCE_SHA256` to the pinned byte digest,
`STUDIO_TRIAL_ASSET_VERSION` to the `asv_` ID,
`STUDIO_TRIAL_PRODUCTION` to the `prd_` ID (or `-` for global scope),
`STUDIO_TRIAL_JOB_ID` to the evidence's `job_` ID and
`STUDIO_TRIAL_TARGET_HEIGHT` to the slot height in pixels. Run from
the checked-out Ops revision.

```bash
(
  set -euo pipefail
  : "${STUDIO_TRIAL_INPUT:?Set the private input path}"
  : "${STUDIO_TRIAL_SOURCE_SHA256:?Set the pinned byte digest}"
  : "${STUDIO_TRIAL_ASSET_VERSION:?Set the pinned asset version}"
  : "${STUDIO_TRIAL_PRODUCTION:?Set the production or - for global scope}"
  : "${STUDIO_TRIAL_JOB_ID:?Set the canonical job ID for the receipt}"
  : "${STUDIO_TRIAL_TARGET_HEIGHT:?Set the pinned slot height}"

  check() {
    local label=$1
    shift
    if "$@"; then
      printf 'PASS %s\n' "$label"
    else
      printf 'FAIL %s\n' "$label" >&2
      return 1
    fi
  }
  assert_json() {
    jq -es "$@" >/dev/null
  }
  is_sha256() { [[ $1 =~ ^[0-9a-f]{64}$ ]]; }
  read_endpoint() {
    kubectl -n ai exec "$1" -c "$2" -- python -B -c '
import sys
from urllib.request import urlopen
with urlopen(sys.argv[1], timeout=5) as response:
    sys.stdout.buffer.write(response.read())
' "$3"
  }
  node_idle() {
    kubectl top node talosw01 --no-headers | awk '
      {print} $1 == "talosw01" && $3 ~ /^[0-9]+%$/ && $5 ~ /^[0-9]+%$/ &&
       $3+0 <= 10 && $5+0 <= 25 {ok=1} END {exit !ok}'
  }
  render_idle() {
    kubectl -n ai top pod --containers -l app.kubernetes.io/name=studio-render --no-headers |
      awk '{print} $2 == "app" && $3 ~ /^[0-9]+m$/ {
        seen=1; if ($3+0 > 250) busy=1
      } END {exit (!seen || busy)}'
  }
  room_on_share() {
    kubectl -n ai exec deploy/studio-render -c app -- sh -ec '
      df -Pk /studio/jobs | awk "NR == 2 { print; if (\$4 >= 34603008) ok=1 } END { exit !ok }"
      df -Pi /studio/jobs | awk "NR == 2 { print; if (\$4 == \"-\" || \$4 >= 32) ok=1 } END { exit !ok }"
    '
  }
  input_matches_store_hash() {
    local hash=$STUDIO_TRIAL_SOURCE_SHA256 root stem
    is_sha256 "$hash" || return 1
    if [[ $STUDIO_TRIAL_PRODUCTION == - ]]; then
      root=/studio/library
    else
      [[ $STUDIO_TRIAL_PRODUCTION =~ ^prd_[0-7][0-9a-hjkmnp-tv-z]{25}$ ]] || return 1
      root="/studio/productions/$STUDIO_TRIAL_PRODUCTION"
    fi
    stem="$root/sha256/${hash:0:2}/$hash"
    [[ $STUDIO_TRIAL_INPUT == "$stem.jpg" || $STUDIO_TRIAL_INPUT == "$stem.png" ]] || return 1
    kubectl -n ai exec deploy/studio-render -c app -- sh -ec '
      path=$1
      test "$(realpath -e -- "$path")" = "$path"
      test -f "$path"
      test "$(stat -c %h -- "$path")" = 1
      sum=$(sha256sum -- "$path")
      test "${sum%% *}" = "$2"
    ' sh "$STUDIO_TRIAL_INPUT" "$hash" >/dev/null 2>&1
  }
  read_profile() {
    profile=$(kubectl -n ai exec deploy/studio-render -c app -- cat \
      "/studio/jobs/$allocation/weights/profile.sha256" 2>/dev/null) || return 1
    is_sha256 "$profile"
  }
  deny_all() {
    kubectl -n ai get ciliumnetworkpolicy studio-matting-quality -o json |
      assert_json 'length == 1 and .[0].spec == {
        "endpointSelector":{"matchLabels":{"app.kubernetes.io/name":"studio-matting-quality"}},
        "ingressDeny":[{"fromEntities":["all"]}], "egressDeny":[{"toEntities":["all"]}]}'
  }
  wan_idle() {
    read_endpoint deploy/wan-video api http://127.0.0.1:8080/v1/ready |
      assert_json 'length == 1 and (.[0] | .ready == true and
        has("active_video_job") and .active_video_job == null and
        has("active_foley_job") and .active_foley_job == null)'
  }
  images_idle() {
    read_endpoint comfyui-0 app http://127.0.0.1:8188/queue |
      assert_json 'length == 1 and (.[0] | .queue_running == [] and .queue_pending == [])'
  }
  stage=kubernetes/main/apps/ai/studio/matting-pending
  allocation=$(yq -e '.data.allocation' "$stage/release/release.yaml") || exit 1
  pin=$(yq -e '.data.image' "$stage/release/release.yaml") || exit 1
  check 'Content-addressed regular single-link input matches its SHA-256' input_matches_store_hash || exit 1
  check 'Installed profile hash present' read_profile || exit 1
  check 'Share byte and inode budget' room_on_share || exit 1
  check 'Deny-all network policy' deny_all || exit 1
  check 'talosw01 CPU at most 10% and memory at most 25%' node_idle || exit 1
  check 'Render app CPU at most 250m' render_idle || exit 1
  check 'Wan idle' wan_idle || exit 1
  check 'Shared image queue empty' images_idle || exit 1
  printf 'PASS quality trial admission\n'

  private_path=${STUDIO_TRIAL_INPUT#/studio/}
  export TRIAL_INPUT_DIR="data/studio/${private_path%/*}" TRIAL_INPUT_NAME="${private_path##*/}"
  export TRIAL_PROFILE="$profile"
  export STUDIO_TRIAL_SOURCE_SHA256 STUDIO_TRIAL_ASSET_VERSION STUDIO_TRIAL_PRODUCTION
  export STUDIO_TRIAL_JOB_ID STUDIO_TRIAL_TARGET_HEIGHT
  suffix=$(openssl rand -hex 6) || exit 1
  export TRIAL_OUTPUT="studio-matting-quality-$suffix"
  manifest=$(kustomize build "$stage/quality" | yq -e '
    .metadata.name = strenv(TRIAL_OUTPUT) |
    .spec.template.spec.initContainers[0].args += [strenv(TRIAL_OUTPUT)] |
    (.spec.template.spec.containers[0].volumeMounts[] | select(.name == "input").subPath) =
      strenv(TRIAL_INPUT_DIR) |
    (.spec.template.spec.containers[0].volumeMounts[] | select(.name == "jobs").subPath) =
      ("data/studio/jobs/" + strenv(TRIAL_OUTPUT)) |
    .spec.template.spec.containers[0].args += [
      strenv(TRIAL_INPUT_NAME), strenv(STUDIO_TRIAL_SOURCE_SHA256),
      strenv(STUDIO_TRIAL_ASSET_VERSION), strenv(STUDIO_TRIAL_PRODUCTION), strenv(STUDIO_TRIAL_JOB_ID),
      strenv(STUDIO_TRIAL_TARGET_HEIGHT), strenv(TRIAL_PROFILE)
    ]') || exit 1
  kubectl -n ai create -f - <<< "$manifest" || exit 1

  wait_terminal() {
    local condition deadline=$((SECONDS + 330))
    while (( SECONDS < deadline )); do
      condition=$(kubectl -n ai --request-timeout=10s get "job/$TRIAL_OUTPUT" -o json |
        jq -r '[.status.conditions[]? | select(.status == "True" and
          (.type == "Complete" or .type == "Failed" or .type == "FailureTarget"))][0].type // ""') || return 1
      case $condition in
        Complete) return 0 ;;
        Failed|FailureTarget) return 1 ;;
      esac
      sleep 5
    done
    return 1
  }
  gate=0
  check 'Job completed successfully' wait_terminal || gate=1
  report=$(kubectl -n ai --request-timeout=10s logs "job/$TRIAL_OUTPUT" -c matting 2>/dev/null | tail -n 1) || report=
  if [[ -z $report ]]; then
    report=$(kubectl -n ai --request-timeout=10s logs "job/$TRIAL_OUTPUT" -c reserve-output 2>/dev/null |
      tail -n 1) || report=
  fi
  final_report() {
    assert_json 'length == 1 and (.[0] | type == "object")' <<< "$report"
  }
  report_check() {
    local predicate=$1
    shift
    assert_json "$@" "length == 1 and (.[0] | ($predicate))" <<< "$report"
  }
  check 'Final log line is a JSON report' final_report || gate=1
  check 'Expected source, image and profile hashes on CPU with arena off' report_check '
    .source_sha256 == $source and .profile_sha256 == $profile and
    .image_digest == $digest and .provider == "CPUExecutionProvider" and .cpu_arena == false
  ' --arg source "$STUDIO_TRIAL_SOURCE_SHA256" --arg profile "$profile" --arg digest "${pin##*@}" || gate=1
  check 'Cold load and request within limits with both memory phases measured' report_check '
    .startup_seconds > 0 and .startup_seconds <= 120 and .request_seconds > 0 and .request_seconds <= 35 and
    .worker_memory.session_creation.peak_rss_bytes > 0 and
    (.worker_memory.requests | length) == 1 and .worker_memory.requests[0].peak_rss_bytes > 0 and
    .peak_rss_upper_bound_bytes > 0 and .peak_rss_upper_bound_bytes <= .memory_limit_bytes and
    .memory_limit_bytes == 10737418240' || gate=1
  check 'Verified cutout, mask and size ratio' report_check '
    .passed == true and .shape.alpha_threshold == 128 and
    .shape.scale.numerator == $target and .shape.scale.denominator == .shape.bbox_height_px and
    ([.artifacts.cutout, .artifacts.mask] |
      all((.sha256 | test("^[0-9a-f]{64}$")) and .bytes > 0 and .media_type == "image/png"))
  ' --argjson target "$STUDIO_TRIAL_TARGET_HEIGHT" || gate=1
  if final_report; then
    printf '%s\n' "$report"
  fi
  if (( gate != 0 )); then
    printf 'FAIL quality trial technical checks\n' >&2
    exit 1
  fi
  printf 'PASS quality trial technical checks\n'
)
```

Within the private Studio workflow, composite the cutout over light and
dark plates and inspect both at source zoom. Record the per-eye retention,
both lash lines, at least 8 of 10 premarked visible flyaways, any halo and
the hand corrections that would be needed. Save the numerical report
and private locator on [OP451](https://work.haynesnetwork.com/work_packages/451).
Keep quality acceptance pending until Tom judges the private result.

## Activation, retries and rollback

Read-only accounting on 2026-11-25 showed 105.98 GiB allocatable and
54.29 GiB requested, including Wan (24 GiB requested, 64 GiB limit),
Foley (12 GiB, 32 GiB) and their API (128 MiB, 512 MiB). Adding the
dormant local-model sidecar (8 GiB, 16 GiB) and one matting instance
(8 GiB, 10 GiB) leaves 35.69 GiB unrequested. Existing limits sum
to 239,904 MiB. At their limits, Wan, local model, the Studio queue
worker and matting alone consume 98 GiB before offline render, Foley
or other workloads. The node cannot carry those peaks simultaneously.
Keep heavy jobs serialized unless measured headroom shows the second
fits. Follow the Wan, Foley and local-model lease rules.
[Matting profile](https://github.com/thaynes43/haynes-studio/blob/main/docs/reference/matting-backend-profile.md)

The [fourth release](studio-fourth-release.md#roll-the-workloads) activates
the sidecar beside `studio-matte-worker` after the OP459 smoke. The offline
app, run worker, matte worker and sidecar have a combined limit of 24 CPUs
and 51 GiB. Use the fourth-release checks for exact readiness and grant
registration. The Unix socket and attempt directories share GID 10001.

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
