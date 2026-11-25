# Stage the Studio upscaling comparison

CR-35 keeps Pillow Lanczos in delivery and permits a private CPU comparison
with FSRCNN ×2 followed by Lanczos. The Ops stage and both Jobs that run the
graph are **Expected**. Model measurement, installed health, comparison and
Tom's verdict are pending.
[Story 461](https://work.haynesnetwork.com/work_packages/461) ·
[Studio profile](https://github.com/thaynes43/haynes-studio/blob/main/docs/reference/upscaling-backend-profile.md)

## Pin and reconcile the policy

1. Merge the Studio source PR. Read the full revision and upscaling digest in
   the successful **Publish tested Studio images** summary on Studio `main`.
2. Set only `data.image` in the Ops [release file](../../kubernetes/main/apps/ai/studio/upscaling-pending/release/release.yaml).
   The release guard fetches the immutable OCI manifest and config, checks
   their digests and verifies the resource label. The first smoke requests
   2 GiB and caps at 8 GiB for measurement on talosw01. The 2 GiB quality
   budget from CR-35 is checked against the observed peak. Serving memory
   stays unset until measured, and the sidecar and offline CPU split stay
   outside Flux. Merge after the release guard passes with the published pin.
3. Run the following in Bash from that Ops revision. Flux owns just the
   ConfigMap with the provisioner and the three targeted policies. The
   download pod can use CoreDNS and HTTPS for the immutable raw GitHub URL.
   Smoke and comparison have ingress and egress denied. Each Job is created
   once by the coordinator and retained for 24 hours.

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
  check 'node CPU and RAM at or below 70%' awk '
    NF == 5 && $3 ~ /^[0-9]+%$/ && $5 ~ /^[0-9]+%$/ && $3+0 <= 70 && $5+0 <= 70 {pass=1}
    END {exit !pass}
  ' <<< "$node" || status=1
  check 'render app below one CPU' awk '
    $2 == "app" {n++; if ($3 ~ /^[0-9]+m$/ && $3+0 < 1000) pass++}
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
      [.status.conditions[]? | select(.status == "True" and (.type == "Failed" or .type == "Complete")) | .type][0] // ""
    ') || return 1
    case "$condition" in
      Complete) return 0 ;;
      Failed) return 1 ;;
    esac
    sleep 10
  done
  return 1
}

final_report() { kubectl -n ai logs "job/$1" | tail -n 1; }

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
the image, tool, host CPU and resource manifest before publishing the weights
directory. The following block also verifies the published files independently.
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
  check 'provision final log line present' test -n "$report" || status=1
  printf '%s\n' "$report"
  check_json 'provisioned graph and profile pins' '
    .graph_bytes == 38973 and
    .graph_sha256 == "366b33f0084c7b3f2bf6724f0a2c77bca94fcec9d7b6d72389d330073b380d5c" and
    .graph_git_blob == "2509b0e19b8a45ebce2ba50128e2fcb43ce8589c" and
    (.profile_sha256 | test("^[0-9a-f]{64}$"))
  ' <<< "$report" || status=1
  (( status == 0 )) || exit "$status"
  profile=$(jq -er '.profile_sha256' <<< "$report")
  kubectl -n ai exec -i deploy/studio-render -c app -- python -B - "$allocation" "$profile" <<'PY'
from hashlib import sha1, sha256
from pathlib import Path
import sys

weights = Path("/studio/jobs") / sys.argv[1] / "weights"
graph = (weights / "FSRCNN_x2.pb").read_bytes()
manifest = (weights / "profile.json").read_bytes()
checks = {
    "graph bytes": len(graph) == 38973,
    "graph SHA-256": sha256(graph).hexdigest() == "366b33f0084c7b3f2bf6724f0a2c77bca94fcec9d7b6d72389d330073b380d5c",
    "Git blob": sha1(b"blob 38973\0" + graph, usedforsecurity=False).hexdigest() == "2509b0e19b8a45ebce2ba50128e2fcb43ce8589c",
    "profile": sha256(manifest).hexdigest() == sys.argv[2] == (weights / "profile.sha256").read_text().strip(),
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
runs the deliberate timeout and fresh-worker replay. Capture the final log
line even if Kubernetes recorded a failed Job. Record the measuring and
quality verdicts separately. Use the observed worker RSS and cgroup peaks
by phase to propose serving resources in a second change.

```bash
(
  set -euo pipefail
  allocation=$(yq '.data.allocation' "$stage/release/release.yaml")
  provision="studio-upscaling-provision-${allocation##*-}"
  profile=$(final_report "$provision" | jq -er '.profile_sha256 | select(test("^[0-9a-f]{64}$"))')
  pin=$(yq '.data.image' "$stage/release/release.yaml")
  idle_gate
  smoke=$(kustomize build "$stage/smoke")
  job=$(yq '.metadata.name' <<< "$smoke")
  kubectl -n ai create -f - <<< "$smoke"
  status=0
  check 'smoke Job complete' wait_for_job "$job" || status=1
  report=$(final_report "$job") || report=
  check 'smoke final log line present' test -n "$report" || status=1
  printf '%s\n' "$report"
  check_json 'installed profile and image match' '
    .profile_sha256 == $profile and .image_digest == $digest and .provider == "opencv-dnn-cpu-fp32"
  ' --arg profile "$profile" --arg digest "${pin##*@}" <<< "$report" || status=1
  check_json 'both allocator runs measured by phase' '
    .verdicts.allocator_measured and .verdicts.phase_memory_measured and
    .measuring_limit_bytes == 8589934592 and (.whole_job_cgroup_peak_bytes > 0) and
    ([.settings[].frame_count] == [19, 19])
  ' <<< "$report" || status=1
  check_json 'allocator and fresh-worker bytes are stable' '
    .verdicts.allocator_byte_match and .verdicts.fresh_worker_repeat
  ' <<< "$report" || status=1
  check_json 'all smoke verdicts' '.passed == true' <<< "$report" || status=1
  exit "$status"
)
```

## Run the private still comparison

After the synthetic smoke passes, choose one opaque frame or still by a
verified SHA-256, using a relative path under the Studio share. It must be a
single PNG or JPEG, at most 4 MiB with a 1280 px long edge and 921,600 pixels.
Use an exact-aspect frame, or pass `--plate` to center-crop an off-aspect
plate. Default orientation is portrait. Optional `--lettering-roi`,
`--face-roi` and `--hair-roi` take four output pixel bounds `X0 Y0 X1 Y1`,
with the lower-right bounds exclusive. Select ROIs from the approved source.
Keep the input path in the operator's shell and Job argument only. Do not use
shell tracing or print the rendered Job. Use a new 12-hex suffix for every
attempt.

```bash
(
  set -euo pipefail
  : "${OP461_INPUT:?set the approved Studio-relative path}"
  : "${OP461_INPUT_SHA256:?set its verified bare SHA-256}"
  : "${OP461_RUN:?set a fresh 12-hex suffix}"
  [[ $OP461_RUN =~ ^[a-f0-9]{12}$ && $OP461_INPUT_SHA256 =~ ^[a-f0-9]{64}$ ]]
  export OP461_INPUT OP461_INPUT_SHA256
  export OP461_JOB="studio-upscaling-compare-$OP461_RUN"
  export OP461_ALLOCATION="upscaling-op461-compare-$OP461_RUN"
  allocation=$(yq '.data.allocation' "$stage/release/release.yaml")
  final_report "studio-upscaling-smoke-${allocation##*-}" |
    check_json 'synthetic install smoke passed' '.passed == true'
  idle_gate
  kustomize build "$stage/compare" | yq '
    .metadata.name = strenv(OP461_JOB) |
    .spec.template.spec.containers[0].args[1] = strenv(OP461_INPUT) |
    .spec.template.spec.containers[0].args[3] = strenv(OP461_INPUT_SHA256) |
    .spec.template.spec.containers[0].args[5] = strenv(OP461_ALLOCATION)
  ' | kubectl -n ai create -f -
  status=0
  check 'comparison Job complete' wait_for_job "$OP461_JOB" || status=1
  report=$(final_report "$OP461_JOB") || report=
  check 'comparison final log line present' test -n "$report" || status=1
  printf '%s\n' "$report"
  check_json 'source and both outputs recorded with measurements' '
    .source.sha256 == $input and
    ([.outputs[].sha256 | test("^[0-9a-f]{64}$")] == [true, true]) and
    (.memory_by_phase | length) >= 4 and (.objective_measures | length) == 3
  ' --arg input "$OP461_INPUT_SHA256" <<< "$report" || status=1
  exit "$status"
)
```

To choose a landscape, plate or ROI in this command, append the relevant
arguments to the rendered container's `args` array in the same `yq` expression
before the pipe to `kubectl create`. After success, read the two PNGs and
`report.json` from the new private jobs allocation. Use the report hashes to
verify both files before judging them. Report visible lettering, hair and
face fidelity, ringing and changed geometry at matching 1:1 crops. The Job
reports source-scale luma error, edge energy and pairwise luma differences,
including selected ROIs. A still has no ground-truth high-resolution image or
motion sequence. Use the synthetic smoke for its objective edge-spread, text,
flatness and cadence gates. The fuller authorized excerpt and Tom's
delivery review are tracked by [story 453](https://work.haynesnetwork.com/work_packages/453).

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
