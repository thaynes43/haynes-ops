# Stage the Studio upscaling comparison

CR-35 keeps Pillow Lanczos in delivery and permits a private CPU comparison
with FSRCNN ×2 followed by Lanczos. Provisional CR-49 adds stored proxy frames
and an optional master reference. The Ops stage and both Jobs that run the
graph are **Expected**. Model measurement, installed health, comparison and
Tom's verdict are pending.
[Story 461](https://work.haynesnetwork.com/work_packages/461) ·
[Studio profile](https://github.com/thaynes43/haynes-studio/blob/main/docs/reference/upscaling-backend-profile.md)

## Pin and reconcile the policy

1. Merge the Studio source PR. Read the full revision and upscaling digest in
   the successful **Publish tested Studio images** summary on Studio `main`.
2. Set only `data.image` in the Ops [release file](../../kubernetes/main/apps/ai/studio/upscaling-pending/release/release.yaml).
   The release guard fetches the immutable OCI manifest and config, checks
   their digests and verifies the resource label and `stored-proxy-frame-v2`
   comparison capability. Provision the new profile under the fresh `-02`
   allocation. The first smoke requests
   2 GiB and caps at 8 GiB for measurement on talosw01. The 2 GiB quality
   budget from CR-35 is checked against the observed peak. Serving memory
   stays unset until measured, and the sidecar and offline CPU split stay
   outside Flux. Merge after the release guard passes with the published pin.
3. Run the following in Bash from that Ops revision. Flux owns just the
   ConfigMap with the provisioner and the three targeted policies. The
   download pod can use CoreDNS and HTTPS for the immutable raw GitHub URL.
   Smoke, probe and comparison have ingress and egress denied. Each Job is created
   once by the coordinator. Provision and smoke are retained for 24 hours.
   The probe and comparison expire one hour after finishing.

Keep these helpers in the shell for the other blocks. Each check reports its
own result. An idle gate failure prevents the Job creation.

```bash
stage=kubernetes/main/apps/ai/studio/upscaling-pending

check() {
  local label=$1
  shift
  if "$@"; then printf 'PASS %s\n' "$label" >&2
  else printf 'FAIL %s\n' "$label" >&2; return 1; fi
}

check_json() {
  local label=$1 predicate=$2
  shift 2
  check "$label" jq -es "$@" "length == 1 and (.[0] | ($predicate))" >/dev/null
}

read_endpoint() {
  kubectl -n ai exec "$1" -c "$2" -- python -B -c '
import sys
from urllib.request import urlopen
with urlopen(sys.argv[1], timeout=5) as response:
    sys.stdout.buffer.write(response.read())
' "$3"
}

idle_gate() {
  local node render status=0
  node=$(kubectl top node talosw01 --no-headers) || node=
  render=$(kubectl -n ai top pod --containers --no-headers -l app.kubernetes.io/name=studio-render) || render=
  printf '%s\n%s\n' "$node" "$render" >&2
  check 'talosw01 CPU at most 10% and memory at most 25%' awk '
    $1 == "talosw01" && NF == 5 && $3 ~ /^[0-9]+%$/ && $5 ~ /^[0-9]+%$/ &&
    $3+0 <= 10 && $5+0 <= 25 {pass=1}
    END {exit !pass}
  ' <<< "$node" || status=1
  check 'render app CPU at most 250m' awk '
    $2 == "app" {n++; if ($3 ~ /^[0-9]+m$/ && $3+0 <= 250) pass++}
    END {exit !(n == 1 && pass == 1)}
  ' <<< "$render" || status=1
  read_endpoint deploy/wan-video api http://127.0.0.1:8080/health |
    check_json 'Wan idle' 'has("active_job") and .active_job == null' || status=1
  read_endpoint comfyui-0 app http://127.0.0.1:8188/queue |
    check_json 'shared ComfyUI queue empty' '.queue_running == [] and .queue_pending == []' || status=1
  return "$status"
}

wait_for_job() {
  local name=$1 condition stop=$((SECONDS + 330))
  while (( SECONDS < stop )); do
    condition=$(kubectl -n ai get "job/$name" -o json | jq -r '
      [.status.conditions[]? | select(.status == "True" and (.type == "Failed" or .type == "FailureTarget" or .type == "Complete")) | .type][0] // ""
    ') || return 1
    case "$condition" in
      Complete) return 0 ;;
      Failed|FailureTarget) return 1 ;;
    esac
    sleep 10
  done
  return 1
}

final_report() {
  kubectl -n ai logs "job/$1" -c "${2:-upscaling}" |
    awk '/^\{/ {line=$0} END {if (line != "") print line}'
}

(
  set -euo pipefail
  python3 scripts/tests/studio-release/check.py
  flux reconcile kustomization cluster-apps -n flux-system --with-source
  flux reconcile kustomization studio-upscaling-policy -n ai
  check 'upscaling policy revision ready' kubectl -n ai wait --for=condition=ready     kustomization/studio-upscaling-policy --timeout=2m
  check 'all three targeted policies present' kubectl -n ai get ciliumnetworkpolicy     studio-upscaling-provision studio-upscaling-smoke studio-upscaling-compare -o name
)
```

## Provision and verify

The Job streams the exact 38,973-byte graph to a new allocation. The writer
syncs and drops cached pages every 64 MiB and at EOF under a 1 GiB container
limit, then verifies the Git blob ID and file SHA-256 from read-back. It writes
the image, tool, host CPU and resource manifest and the pinned image's
`FSRCNN-NOTICE` before publishing the weights directory. The following block also verifies the published files independently.
If NFS omits free inode counts, set `OP461_INODE_RESERVE_CONFIRMED=yes` only
after the existing storage owner confirms room for 1,024 planned entries and at
least 1,024 further free entries. Keep failed allocations in the file budget.

```bash
(
  set -euo pipefail
  provision=$(kustomize build "$stage/provision")
  job=$(yq 'select(.kind == "Job") | .metadata.name' <<< "$provision")
  config=$(yq 'select(.kind == "Job") | .spec.template.spec.volumes[] |
    select(.name == "config") | .configMap.name' <<< "$provision")
  allocation=$(yq '.data.allocation' "$stage/release/release.yaml")
  check 'Flux provisioner ConfigMap present' kubectl -n ai get "configmap/$config" -o name
  check '40 GiB free on the Studio jobs filesystem' kubectl -n ai exec deploy/studio-render -c app -- sh -ec '
    df -Pk /studio/jobs | awk "NR == 2 { print; if (\$4 >= 41943040) good = 1 } END { exit !good }"
  '
  entries=$(kubectl -n ai exec deploy/studio-render -c app -- sh -ec 'df -Pi /studio/jobs' | awk 'NR == 2 {print $4}')
  check 'jobs inode budget and reserve confirmed' bash -ec '
    [[ $1 =~ ^[0-9]+$ ]] && (( $1 >= 2048 )) || [[ $1 == "-" && $2 == "yes" ]]
  ' -- "$entries" "${OP461_INODE_RESERVE_CONFIRMED:-}"
  yq 'select(.kind == "Job")' <<< "$provision" | kubectl -n ai create -f -
  status=0
  check 'provision Job complete' wait_for_job "$job" || status=1
  report=$(final_report "$job") || report=
  check 'provision JSON report present' test -n "$report" || status=1
  printf '%s\n' "$report"
  check_json 'provisioned graph and profile pins' '
    .graph_bytes == 38973 and
    .graph_sha256 == "366b33f0084c7b3f2bf6724f0a2c77bca94fcec9d7b6d72389d330073b380d5c" and
    .graph_git_blob == "2509b0e19b8a45ebce2ba50128e2fcb43ce8589c" and
    (.profile_sha256 | test("^[0-9a-f]{64}$")) and
    (.notice_sha256 | test("^[0-9a-f]{64}$"))
  ' <<< "$report" || status=1
  (( status == 0 )) || exit "$status"
  profile=$(jq -er '.profile_sha256' <<< "$report")
  notice=$(jq -er '.notice_sha256' <<< "$report")
  kubectl -n ai exec -i deploy/studio-render -c app -- python -B - "$allocation" "$profile" "$notice" <<'PY'
from hashlib import sha1, sha256
from pathlib import Path
import sys

weights = Path("/studio/jobs") / sys.argv[1] / "weights"
graph = (weights / "FSRCNN_x2.pb").read_bytes()
manifest = (weights / "profile.json").read_bytes()
notice = (weights / "FSRCNN-NOTICE").read_bytes()
checks = {
    "graph bytes": len(graph) == 38973,
    "graph SHA-256": sha256(graph).hexdigest() == "366b33f0084c7b3f2bf6724f0a2c77bca94fcec9d7b6d72389d330073b380d5c",
    "Git blob": sha1(b"blob 38973\0" + graph, usedforsecurity=False).hexdigest() == "2509b0e19b8a45ebce2ba50128e2fcb43ce8589c",
    "profile": sha256(manifest).hexdigest() == sys.argv[2] == (weights / "profile.sha256").read_text().strip(),
    "licence notice": bool(notice) and sha256(notice).hexdigest() == sys.argv[3],
}
for label, passed in checks.items():
    print(f"{'PASS' if passed else 'FAIL'} {label}")
raise SystemExit(not all(checks.values()))
PY
)
```

## Run the measuring smoke

Choose a window with no heavy render, Wan or Foley work. The idle gate reads
node and render load, checks Wan health and checks the queue from
`comfyui-0` itself. Keep this window exclusive for the smoke. The Job
compares the default and compact glibc allocator across the same frames and
runs the deliberate timeout and fresh-worker replay. Capture the last JSON
line even if Kubernetes recorded a failed Job. Record the measuring and
quality verdicts separately. Use the observed worker RSS and cgroup peaks
by phase to propose serving resources in a second change. The allowance check
reads the running container’s cgroup limit from `/sys/fs/cgroup/memory.max`.

```bash
(
  set -euo pipefail
  allocation=$(yq '.data.allocation' "$stage/release/release.yaml")
  provision="studio-upscaling-provision-${allocation##*-}"
  profile=$(final_report "$provision" | jq -er '.profile_sha256 | select(test("^[0-9a-f]{64}$"))')
  pin=$(yq '.data.image' "$stage/release/release.yaml")
  allowance=$(yq '.data.memoryLimit' "$stage/release/release.yaml")
  [[ $allowance =~ ^[1-9][0-9]*Gi$ ]]
  allowance_bytes=$(( ${allowance%Gi} * 1024**3 ))
  idle_gate
  smoke=$(kustomize build "$stage/smoke")
  job=$(yq '.metadata.name' <<< "$smoke")
  kubectl -n ai create -f - <<< "$smoke"
  status=0
  check 'smoke Job complete' wait_for_job "$job" || status=1
  report=$(final_report "$job") || report=
  check 'smoke JSON report present' test -n "$report" || status=1
  printf '%s\n' "$report"
  check_json 'installed profile and image match' '
    .profile_sha256 == $profile and .image_digest == $digest and .provider == "opencv-dnn-cpu-fp32"
  ' --arg profile "$profile" --arg digest "${pin##*@}" <<< "$report" || status=1
  check_json 'both allocator runs measured by phase' '
    .verdicts.allocator_measured and .verdicts.phase_memory_measured and
    (.whole_job_cgroup_peak_bytes > 0) and
    ([.settings[].frame_count] == [19, 19])
  ' <<< "$report" || status=1
  check_json 'actual cgroup memory limit matches the measuring allowance' '
    .measuring_limit_bytes == $bytes
  ' --argjson bytes "$allowance_bytes" <<< "$report" || status=1
  check_json 'allocator and fresh-worker bytes are stable' '
    .verdicts.allocator_byte_match and .verdicts.fresh_worker_repeat
  ' <<< "$report" || status=1
  check_json 'all smoke verdicts' '.passed == true' <<< "$report" || status=1
  exit "$status"
)
```

## Run the private comparison

After the synthetic smoke passes, set `OP461_INPUT` to an authorized delivery
proxy's canonical locator. For ground truth, also set `OP461_MASTER` to the
approved master locator and `OP461_DELIVERY_ID` to the ID in Studio's `titles/`
evidence. Use the approved proxy and master SHA-256 values from that evidence
in the locators. The API pod reads in a read-only transaction and requires
exactly one delivery with the requested hashes in their respective proxy and
master roles. The ID and both locators must agree with the approved inputs.
The runbook captures the selected record privately and uses its fields to
render both Jobs. The delivery lookup prints only check verdicts to the
terminal.

Set `OP461_FRAME_INDEX` to a zero-based index or `OP461_TIMESTAMP` to decimal
seconds from the first frame, with up to nine decimal places. Choose one
selector. The timestamp selects the active frame on the 24 fps delivery
clock. The probe and comparison check the first presentation timestamp
from the MP4 sample and edit tables. They refuse a nonzero origin with
`adapter.nonzero_start_timestamp` and use the zero-based delivery clock for
selection. Each file is limited to 256 MiB.

For a still, set `OP461_INPUT` to the authorized opaque PNG or JPEG locator.
Stills are limited to 4 MiB. Use an exact-aspect frame or set
`OP461_PLATE=yes` for delivery's centered cover fit. Both still and proxy
inputs have a 1280 px long-edge limit and a 921,600 pixel limit. Set
`OP461_ORIENTATION=landscape` for a 1920 × 1080 target. The default is
1080 × 1920. A master frame must match the target dimensions exactly.

A locator is `library/sha256/<first-two-hex>/<sha256>.<ext>` or
`productions/<prd_id>/sha256/<first-two-hex>/<sha256>.<ext>`, relative to
`/studio`. The hash comes from the filename. If set, `OP461_INPUT_SHA256`
must match it. Set `OP461_RUN` to a fresh 12-hex suffix. For optional ROIs,
add `--lettering-roi`, `--face-roi` or `--hair-roi` and output coordinates
`X0 Y0 X1 Y1` to `options` before rendering. The lower-right bounds are exclusive.

The gate checks the store shape and verifies the bytes. For an MP4 input,
the probe runs before the idle gate and heavy window. It reads the clock
origin from the MP4 tables and uses the bundled OpenCV FFmpeg backend to
decode the selected frame from the verified descriptors. Its deadline is
90 seconds, with one CPU and 256 MiB of memory. It reports frame count, dimensions, first timestamp
in seconds and whether the requested frame resolves. It mounts only the
read-only input shards and runs without weights or either scaler. A refusal
stops the block before the heavy comparison. Both Jobs use the same
deny-all comparison network policy. The comparison's UID/GID 1000
init container reserves one new group-writable job directory exclusively.
The comparison runs as UID/GID 10001 with supplemental group 1000. It
mounts the pinned weights and the selected hash shards read-only. Its only
writable share mount is the new output directory. One local `emptyDir`
provides bounded worker scratch. Keep the rendered Job private.

```bash
(
  set -euo pipefail
  : "${OP461_INPUT:?set the approved Studio-relative store path}"
  : "${OP461_RUN:?set a fresh 12-hex suffix}"

  input_matches_store_hash() {
    local locator=$1 name=${1##*/} limit=4194304
    [[ $name != *.mp4 ]] || limit=268435456
    kubectl -n ai exec deploy/studio-render -c app -- sh -ec '
      path=$1
      test "$(realpath -e -- "$path")" = "$path"
      test -f "$path"
      test "$(stat -c %h -- "$path")" = 1
      bytes=$(stat -c %s -- "$path")
      test "$bytes" -gt 0 && test "$bytes" -le "$3" || exit 1
      sum=$(sha256sum -- "$path")
      test "${sum%% *}" = "$2"
    ' sh "/studio/$locator" "${name%.*}" "$limit" >/dev/null 2>&1
  }

  source=$OP461_INPUT
  master=${OP461_MASTER:-}
  delivery_id=${OP461_DELIVERY_ID:-}
  if [[ -n $master ]]; then
    check 'approved delivery ID supplied for the master' test -n "$delivery_id"
    if ! delivery=$(kubectl -n ai exec -i deploy/studio-api -c api -- python -I -B - \
        "$source" "$master" "$delivery_id" < "$stage/compare/verify_delivery.py"); then
      printf 'FAIL verified delivery required for a master\n' >&2
      exit 1
    fi
    source=$(jq -er '.proxy' <<< "$delivery")
    master=$(jq -er '.master' <<< "$delivery")
    delivery_id=$(jq -er '.id' <<< "$delivery")
  else
    check 'delivery ID is used only with a master' test -z "$delivery_id"
  fi
  options=(--input "$source" --run "$OP461_RUN" --orientation "${OP461_ORIENTATION:-portrait}")
  [[ -z ${OP461_FRAME_INDEX:-} ]] || options+=(--frame-index "$OP461_FRAME_INDEX")
  [[ -z ${OP461_TIMESTAMP:-} ]] || options+=(--timestamp "$OP461_TIMESTAMP")
  [[ -z $master ]] || options+=(--master "$master" --delivery-id "$delivery_id")
  [[ ${OP461_PLATE:-} != yes ]] || options+=(--plate)
  template=$(kustomize build "$stage/compare" | yq -o=json 'select(.kind == "Job")')
  job_spec=$(python3 -B "$stage/compare/render.py" "${options[@]}" <<< "$template")
  job=$(jq -er '.metadata.name' <<< "$job_spec")
  source_name=${source##*/}
  source_hash=${source_name%.*}
  check 'supplied input digest agrees with the locator' test "${OP461_INPUT_SHA256:-$source_hash}" = "$source_hash"
  check 'content-addressed regular single-link input matches SHA-256' input_matches_store_hash "$source"
  master_hash=
  if [[ -n $master ]]; then
    check 'content-addressed regular single-link master matches SHA-256' input_matches_store_hash "$master"
    master_name=${master##*/}
    master_hash=${master_name%.*}
  fi
  allocation=$(yq '.data.allocation' "$stage/release/release.yaml")
  final_report "studio-upscaling-smoke-${allocation##*-}" |
    check_json 'synthetic install smoke passed' '.passed == true'
  kubectl -n ai get ciliumnetworkpolicy studio-upscaling-compare -o json |
    check_json 'dedicated deny-all comparison policy is installed' '.spec == {
      "endpointSelector":{"matchLabels":{"app.kubernetes.io/name":"studio-upscaling-compare"}},
      "ingressDeny":[{"fromEntities":["all"]}], "egressDeny":[{"toEntities":["all"]}]}'
  if [[ $source == *.mp4 ]]; then
    probe_spec=$(python3 -B "$stage/compare/render.py" "${options[@]}" --probe <<< "$template")
    probe=$(jq -er '.metadata.name' <<< "$probe_spec")
    kubectl -n ai create -f - <<< "$probe_spec"
    status=0
    check 'probe Job complete' wait_for_job "$probe" || status=1
    report=$(final_report "$probe") || report=
    check 'probe JSON report present' test -n "$report" || status=1
    printf '%s\n' "$report"
    check_json 'proxy frame resolves on the zero-based delivery clock' '
      .passed == true and .source.sha256 == $input and .source.resolved == true and
      .source.first_timestamp_seconds == 0 and .source.fps == 24 and
      (.source.frame_count > .source.frame_index) and (.source.size | length) == 2
    ' --arg input "$source_hash" <<< "$report" || status=1
    if [[ -n $master_hash ]]; then
      check_json 'verified master resolves at the same frame' '
        .delivery_id == $delivery and .reference.sha256 == $master and .reference.resolved == true and
        .reference.first_timestamp_seconds == 0 and .reference.fps == 24 and
        .reference.frame_index == .source.frame_index and .reference.frame_count == .source.frame_count and
        (.reference.size | length) == 2
      ' --arg delivery "$delivery_id" --arg master "$master_hash" <<< "$report" || status=1
    fi
    (( status == 0 )) || exit "$status"
  fi
  idle_gate
  kubectl -n ai create -f - <<< "$job_spec"
  status=0
  check 'comparison Job complete' wait_for_job "$job" || status=1
  report=$(final_report "$job" 2>/dev/null) || report=
  if [[ -z $report ]]; then
    report=$(final_report "$job" reserve-output 2>/dev/null) || report=
  fi
  check 'comparison JSON report present' test -n "$report" || status=1
  printf '%s\n' "$report"
  check_json 'source and both outputs recorded with measurements' '
    .source.sha256 == $input and
    ([.outputs[].sha256 | test("^[0-9a-f]{64}$")] == [true, true]) and
    (.memory_by_phase | length) >= 4 and (.objective_measures | length) == 3
  ' --arg input "$source_hash" <<< "$report" || status=1
  if [[ -n $master_hash ]]; then
    check_json 'verified delivery, master frame, reference measurements and caveat recorded' '
      .delivery_id == $delivery and
      (.pairing_verification | contains("The caller verified the pairing")) and
      (.ground_truth_scope | contains("encoded downscale of the master")) and
      .reference.sha256 == $master and .reference.frame_index == .source.frame_index and
      (.normalized_reference.sha256 | test("^[0-9a-f]{64}$")) and
      ([.objective_measures.lanczos.ground_truth, .objective_measures.fsrcnn.ground_truth] |
        all(has("luma_psnr_db") and has("luma_ssim") and has("luma_mae")))
    ' --arg delivery "$delivery_id" --arg master "$master_hash" <<< "$report" || status=1
  fi
  exit "$status"
)
```

After success, read the two result PNGs and `report.json` from the new
private jobs allocation. A video comparison also saves `proxy.png` and,
with a master, `master.png`. Verify each PNG against its report hash. Tom
judges lettering, hair and face fidelity, ringing and changed geometry at
matching 1:1 crops. The Job reports source-scale luma error, edge energy
and pairwise luma differences, including selected ROIs. With a master, it
adds luma MAE, PSNR and SSIM for both arms. The printed JSON above shows
`ground_truth_scope` alongside those numbers. The proxy is an encoded downscale
of its master, so these measures reward undoing that specific encode. The
synthetic smoke covers edge-spread, text, flatness and cadence gates. The
fuller authorized excerpt and Tom's delivery review are tracked by
[story 453](https://work.haynesnetwork.com/work_packages/453).

## Handoff, retries and rollback

Record the Git revisions, published digest, Jobs and final reports in OP461.
Installation evidence belongs to the coordinator. The comparison status
stays pending until the private review. The offline 20-vCPU split remains in
effect. A later serving change can budget two of those CPUs across the chosen
sidecars and update the serving memory contract using measurements.

Keep a failed allocation and its report. A deliberate fresh retry gets a new
Job name and allocation. Before activation, rollback withdraws the policy
stage in Git and stops new Job launches. Allow existing Jobs to reach their
bounds and preserve files and evidence. After a later activation, withdraw
profile selection, return the worker's Flux path to its previous overlay,
and restore the associated offline CPU split through Git before reconciling.
