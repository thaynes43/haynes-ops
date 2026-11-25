#!/usr/bin/env bash
# Run from the pinned Ops checkout. Reconciliation is gated by a preflight receipt.
set -uo pipefail

root=kubernetes/main/apps/ai/studio
render_root=kubernetes/main/apps/ai/studio-render
script_dir=scripts/studio-release
old_revision=80975a9e958d0bed78ab530c2b20f1279bf7ebe6
old_digest=sha256:08ac310f246cdc43225478e9496e480af6430b53751f4ae93f3e34206714c3e7
old_web=sha256:497e020b8846f09cdeab6d822aa1536dfeb325edd26205cd6e28f0061bb6d9ca
revision=$(yq -er '.revision' "$root/release.yaml") || exit 1
api=$(yq -er '.apiDigest' "$root/release.yaml") || exit 1
web=$(yq -er '.webDigest' "$root/release.yaml") || exit 1
matting="$root/matting-pending/release/release.yaml"
sidecar=$(yq -er '.data.image' "$matting") || exit 1
profile=$(yq -er '.data.profileSha256' "$matting") || exit 1
allocation=$(yq -er '.data.allocation' "$matting") || exit 1
base=https://studio.haynesops.com
phase=${1:-}
failed=0
results=()
receipt=${STUDIO_OP967_RECEIPT:-}
baseline=${STUDIO_OP967_BASELINE:-}
job_archive=
admission_window_seconds=3600
studio_stages=(studio-render studio-migration studio studio-worker-registration studio-worker
  studio-media-worker-registration studio-media-workers)
matte_stages=(studio-matte-worker studio-matte-worker-registration)
predecessor_jobs=(migrate register-worker register-voice-worker register-image-worker)
preflight_checks=(
  'new private receipt and baseline paths supplied'
  'reviewed Ops checkout has a clean tracked tree'
  'three postgres16 instances Ready'
  'installed dependencies and predecessor credential projections Ready'
  'studio-api pod image, placement and restart counts'
  'studio-mcp pod image, placement and restart counts'
  'studio-web pod image, placement and restart counts'
  'studio-worker pod image, placement and restart counts'
  'studio-voice-worker pod image, placement and restart counts'
  'studio-image-worker pod image, placement and restart counts'
  'third-pin workload set'
  'old API endpoints'
  'talosw01 capacity'
  'talosw01 CPU <=10%, memory <=25%'
  'offline render app idle and admitted venv complete'
  'Wan ready and both active jobs null'
  'shared ComfyUI queue empty'
  'retained matting allocation exact profile'
  'LAN root available'
  'LAN API anonymous access refused'
  'empty queues, archived data and store baseline'
)
retry_checks=(
  'previous admission and archived predecessor Job logs verified'
  'replacement migration Job and Flux health check match the terminal failure'
  'matte workload absent'
)
rollback_checks=(
  'new private receipt and baseline paths supplied'
  'passing preflight receipt for the selected merged Ops revision'
  'Flux stages and workload images on the selected release sequence'
  'snapshots before a workload rollback'
)

check() {
  local label=$1
  shift
  if "$@"; then
    results+=("PASS $label")
    printf 'PASS %s\n' "$label" >&2
  else
    results+=("FAIL $label")
    printf 'FAIL %s\n' "$label" >&2
    failed=1
  fi
}

finish() {
  if ((failed)); then
    printf 'FAIL OP967 %s\n' "$phase" >&2
    exit 1
  fi
  printf 'PASS OP967 %s\n' "$phase" >&2
}

checkout() {
  local changed
  head=$(git rev-parse --verify HEAD) || return 1
  tree=$(git rev-parse --verify 'HEAD^{tree}') || return 1
  changed=$(git status --porcelain --untracked-files=no) || return 1
  [[ $head =~ ^[0-9a-f]{40}$ && $tree =~ ^[0-9a-f]{40}$ && -z $changed ]]
}

receipt_paths() {
  [[ -n $receipt && -n $baseline ]] || return 1
  receipt=$(realpath -m -- "$receipt") && baseline=$(realpath -m -- "$baseline") || return 1
  [[ $receipt != "$baseline" && ! -e $receipt && ! -e $baseline ]]
}

write_receipt() {
  local mode=$1 finished utc snapshot= archived=
  finished=$(date -u +%s) || return 1
  utc=$(date -u -d "@$finished" '+%Y-%m-%dT%H:%M:%SZ') || return 1
  if (( ! failed )); then
    snapshot=$(file_digest "$baseline") || return 1
    if [[ $mode != rollback ]]; then
      archived=$(archive_digest "$job_archive") || return 1
    fi
  fi
  (umask 077; set -o noclobber
    printf '%s\n' "${results[@]}" |
      jq -Rn --arg mode "$mode" --arg head "$head" --arg tree "$tree" --argjson started "$started" \
        --argjson finished "$finished" --arg utc "$utc" --argjson failed "$failed" \
        --arg baseline "$baseline" --arg snapshot "$snapshot" --arg archive "$job_archive" --arg archived "$archived" '
        {schema: "studio-op967/v2", mode:$mode, ops_revision:$head, ops_tree:$tree,
         started_epoch:$started, finished_epoch:$finished, finished_utc:$utc, passed:($failed == 0),
         baseline_path:$baseline, baseline_sha256:$snapshot, jobs_path:$archive, jobs_sha256:$archived,
         checks:[inputs | {label:.[5:], result:.[0:4]}]}' > "$receipt"
  )
}

file_digest() {
  local checksum
  [[ -f $1 ]] || return 1
  checksum=$(sha256sum < "$1") || return 1
  printf '%s\n' "${checksum%% *}"
}

archive_digest() (
  local name checksum
  local files=()
  [[ -n $1 ]] && cd -- "$1" || return 1
  for name in "${predecessor_jobs[@]}"; do
    [[ -f $name.log && -s $name.log ]] || return 1
    files+=("$name.log")
  done
  checksum=$(sha256sum -- "${files[@]}" | sha256sum) || return 1
  printf '%s\n' "${checksum%% *}"
)

selected_checkout() {
  [[ ${MERGED_OPS_REVISION:-} =~ ^[0-9a-f]{40}$ ]] || return 1
  checkout || return 1
  [[ $head == "$MERGED_OPS_REVISION" ]] || return 1
  git merge-base --is-ancestor "$head" refs/remotes/origin/main
}

validate_receipt() {
  local file=$1 expected_tree=$2 admitted_at=$3 purpose=$4 expected_baseline=$5
  local mode reviewed labels recorded_path recorded_hash actual_hash
  [[ -f $file ]] || return 1
  mode=$(jq -er '.mode' "$file") || return 1
  local required_checks=()
  case "$purpose:$mode" in
    rollout:release) required_checks=("${preflight_checks[@]}" 'predecessor Job logs saved') ;;
    rollout:retry) required_checks=("${preflight_checks[@]}" "${retry_checks[@]}") ;;
    rollback:rollback) required_checks=("${rollback_checks[@]}") ;;
    *) return 1 ;;
  esac
  labels=$(printf '%s\n' "${required_checks[@]}" | jq -Rn '[inputs]') || return 1
  jq -e --arg tree "$expected_tree" --argjson admitted_at "$admitted_at" --arg purpose "$purpose" \
    --argjson window "$admission_window_seconds" --argjson labels "$labels" '
    def epoch: type == "number" and . == floor and . >= 0;
    .schema == "studio-op967/v2" and .ops_tree == $tree and
    (.ops_revision | test("^[a-f0-9]{40}$")) and .passed == true and
    (.started_epoch | epoch) and (.finished_epoch | epoch) and
    .started_epoch <= .finished_epoch and .finished_epoch <= $admitted_at and
    ($purpose == "rollback" or ($admitted_at - .started_epoch) < $window) and
    (.baseline_path | type == "string" and startswith("/")) and
    (.baseline_sha256 | test("^[a-f0-9]{64}$")) and
    ([.checks[].label] | sort) == ($labels | sort) and
    all(.checks[]; .result == "PASS")
  ' "$file" >/dev/null || return 1
  reviewed=$(jq -er '.ops_revision' "$file") || return 1
  [[ $(git rev-parse --verify "$reviewed^{tree}") == "$expected_tree" ]] || return 1
  recorded_path=$(jq -er '.baseline_path' "$file") || return 1
  [[ -z $expected_baseline || $recorded_path == "$expected_baseline" ]] || return 1
  recorded_hash=$(jq -er '.baseline_sha256' "$file") || return 1
  actual_hash=$(file_digest "$recorded_path") || return 1
  [[ $actual_hash == "$recorded_hash" ]]
}

merge_admitted() {
  local now
  [[ -n $baseline && -n $receipt ]] || return 1
  baseline=$(realpath -m -- "$baseline") || return 1
  checkout || return 1
  git merge-base --is-ancestor refs/remotes/origin/main "$head" || return 1
  now=$(date -u +%s) || return 1
  validate_receipt "$receipt" "$tree" "$now" rollout "$baseline"
}

release_admitted() {
  local merged_at
  [[ -n $baseline && -n $receipt ]] || return 1
  baseline=$(realpath -m -- "$baseline") || return 1
  selected_checkout || return 1
  merged_at=$(git show -s --format=%ct "$head") || return 1
  validate_receipt "$receipt" "$tree" "$merged_at" rollout "$baseline"
}

rollback_admitted() {
  local rollback_merge=$1 merged_at
  [[ -n $baseline && -n $receipt ]] || return 1
  baseline=$(realpath -m -- "$baseline") || return 1
  revision_at_or_after "$rollback_merge" "$head" || return 1
  [[ $rollback_merge != "$head" ]] || return 1
  git merge-base --is-ancestor "$rollback_merge" refs/remotes/origin/main || return 1
  merged_at=$(git show -s --format=%ct "$rollback_merge") || return 1
  validate_receipt "$receipt" "$tree" "$merged_at" rollback "$baseline"
}

retry_evidence() {
  local previous=${STUDIO_OP967_FAILED_OPS_REVISION:-} previous_receipt=${STUDIO_OP967_PREVIOUS_RECEIPT:-}
  local previous_tree previous_merge archived_hash
  [[ $previous =~ ^[0-9a-f]{40}$ && $previous != "$head" ]] || return 1
  git merge-base --is-ancestor "$previous" refs/remotes/origin/main || return 1
  git merge-base --is-ancestor "$previous" "$head" || return 1
  previous_tree=$(git rev-parse --verify "$previous^{tree}") || return 1
  previous_merge=$(git show -s --format=%ct "$previous") || return 1
  (( previous_merge <= started )) || return 1
  # The retry merge needs its own admission for the new tree.
  validate_receipt "$previous_receipt" "$previous_tree" "$previous_merge" rollout '' || return 1
  prior_baseline=$(jq -er '.baseline_path' "$previous_receipt") || return 1
  job_archive=$(jq -er '.jobs_path | select(startswith("/"))' "$previous_receipt") || return 1
  archived_hash=$(jq -er '.jobs_sha256 | select(test("^[a-f0-9]{64}$"))' "$previous_receipt") || return 1
  [[ $(archive_digest "$job_archive") == "$archived_hash" ]] || return 1
  previous_ops_revision=$previous
}

retry_job() {
  local source old_job new_job prefix="studio-migrate-${api:7:12}" old_attempt new_attempt changed health prior_gate
  source=$(git show "$previous_ops_revision:$root/migration/job.yaml") || return 1
  old_job=$(yq -er 'select(.kind == "Job") | .metadata.name' - <<< "$source") || return 1
  new_job=$(yq -er 'select(.kind == "Job") | .metadata.name' "$root/migration/job.yaml") || return 1
  [[ $old_job =~ ^$prefix(-r[1-9][0-9]*)?$ && $new_job =~ ^$prefix-r[1-9][0-9]*$ ]] || return 1
  old_attempt=0
  if [[ $old_job != "$prefix" ]]; then old_attempt=${old_job##*-r}; fi
  new_attempt=${new_job##*-r}
  [[ $new_attempt == "$((old_attempt + 1))" ]] || return 1
  health=$(yq -er 'select(.kind == "Kustomization" and .metadata.name == "studio-migration") |
    .spec.healthChecks[] | select(.kind == "Job" and .namespace == "ai") | .name' "$root/ks.yaml") || return 1
  [[ $health == "$new_job" ]] || return 1
  changed=$(git diff --name-only "$previous_ops_revision" "$head" -- "$root" "$render_root") || return 1
  [[ $changed == "$root/ks.yaml"$'\n'"$root/migration/job.yaml" ]] || return 1
  prior_gate=$(git show "$previous_ops_revision:$root/ks.yaml") || return 1
  [[ $(< "$root/migration/job.yaml") == "${source//name: $old_job/name: $new_job}" &&
     $(< "$root/ks.yaml") == "${prior_gate//name: $old_job/name: $new_job}" ]] || return 1
  kubectl -n ai get "job/$old_job" -o json | jq -e --arg api "ghcr.io/thaynes43/haynes-studio@$api" '
    any(.spec.template.spec.containers[]?; .name == "migrate" and .image == $api) and
    any(.status.conditions[]?; .type == "Failed" and .status == "True") and
    all(.status.conditions[]?; .type != "Complete" or .status != "True") and
    (.status.active // 0) == 0' >/dev/null
}

revision_at_or_after() {
  local observed=$1 expected=$2
  [[ $observed =~ ^[0-9a-f]{40}$ && $expected =~ ^[0-9a-f]{40}$ ]] || return 1
  [[ $observed == "$expected" ]] || git merge-base --is-ancestor "$expected" "$observed"
}

source_artifact() {
  kubectl -n flux-system get gitrepository haynes-ops -o json |
    jq -er '.status.artifact.revision | select(test("^main@sha1:[0-9a-f]{40}$")) | sub("^main@sha1:"; "")'
}

same_pin() {
  local observed=$1 expected=$2
  revision_at_or_after "$observed" "$expected" || return 1
  # A descendant may include unrelated changes, but must still carry this pin.
  git diff --quiet "$expected" "$observed" -- "$root" "$render_root"
}

flux_stage() {
  local namespace=$1 row=$2 expected=$3 artifact=$4 applied attempted observed fields
  fields=$(jq -er --arg namespace "$namespace" '
    select(.metadata.deletionTimestamp == null and .spec.suspend != true and
      .spec.sourceRef.kind == "GitRepository" and .spec.sourceRef.name == "haynes-ops" and
      (.spec.sourceRef.namespace // $namespace) == "flux-system") |
    [(.status.lastAppliedRevision // "-"), (.status.lastAttemptedRevision // "-"),
     ((.status.observedGeneration == .metadata.generation and
       any(.status.conditions[]?; .type == "Ready" and .status == "True")) | tostring)] | @tsv
  ' <<< "$row") || return 1
  read -r applied attempted stage_ready <<< "$fields"
  for observed in "$applied" "$attempted"; do
    if [[ $observed == - ]]; then continue; fi
    [[ $observed == main@sha1:* ]] || return 1
    revision_at_or_after "$artifact" "${observed#main@sha1:}" || return 1
  done
  stage_applied=${applied#main@sha1:}
  stage_attempted=${attempted#main@sha1:}
  if revision_at_or_after "$stage_applied" "$expected"; then
    same_pin "$stage_applied" "$expected" || return 1
  else
    # With the selected artifact available, an active stage on the same source
    # can still be waiting for its dependencies to reach that artifact.
    stage_ready=false
  fi
}

flux_state() {
  local mode=$1 expected=${2:-$head} artifact inventory stages row stage stage_applied stage_attempted stage_ready
  local inventory_applied failed_stage=0 failed_read=0
  local expected_stages=("${studio_stages[@]}")
  if [[ $mode != rollback ]]; then expected_stages+=("${matte_stages[@]}"); fi
  artifact=$(source_artifact) || failed_read=1
  inventory=$(kubectl -n flux-system get kustomization cluster-apps -o json) || failed_read=1
  stages=$(kubectl -n ai get kustomizations -o json) || failed_read=1
  if ((failed_read)); then return 1; fi
  same_pin "$artifact" "$expected" || return 1
  flux_stage flux-system "$inventory" "$expected" "$artifact" || return 1
  inventory_applied=$stage_applied
  printf 'Flux cluster-apps applied %s, selected source %s
' "$stage_applied" "$artifact"
  if [[ $mode != pending && $stage_ready != true ]]; then failed_stage=1; fi
  for stage in "${expected_stages[@]}"; do
    if ! row=$(jq -cer --arg name "$stage" 'first(.items[] | select(.metadata.name == $name))' <<< "$stages"); then
      if [[ $mode == pending && ( $stage == "${matte_stages[0]}" || $stage == "${matte_stages[1]}" ) ]] &&
         ! revision_at_or_after "$inventory_applied" "$expected"; then
        printf 'Flux %s pending inventory from %s
' "$stage" "$artifact"
        continue
      fi
      printf 'FAIL missing Flux stage %s
' "$stage" >&2
      failed_stage=1
      continue
    fi
    if flux_stage ai "$row" "$expected" "$artifact"; then
      printf 'Flux %s applied %s, ready at selected revision %s
' "$stage" "$stage_applied" "$stage_ready"
      if [[ $mode != pending && $stage_ready != true ]]; then failed_stage=1; fi
    else
      printf 'FAIL unexpected Flux state for %s
' "$stage" >&2
      failed_stage=1
    fi
  done
  if [[ $mode == rollback ]]; then
    jq -e 'all(.items[]; .metadata.name != "studio-matte-worker" and
                         .metadata.name != "studio-matte-worker-registration")' <<< "$stages" >/dev/null || return 1
  fi
  (( ! failed_stage ))
}

reconcile() {
  local stage=$1 namespace=$2 timeout=$3 artifact
  release_admitted || return 1
  artifact=$(source_artifact) || return 1
  same_pin "$artifact" "$head" || return 1
  flux reconcile kustomization "$stage" -n "$namespace" --timeout="$timeout"
}

release_workloads() {
  # Deployment templates may advance while predecessor pods still serve.
  # Only the two admitted image pairs are valid while Flux applies the pin.
  kubectl -n ai get deployments -o json | jq -er     --arg old "ghcr.io/thaynes43/haynes-studio@$old_digest"     --arg old_web "ghcr.io/thaynes43/haynes-studio-web@$old_web"     --arg api "ghcr.io/thaynes43/haynes-studio@$api"     --arg web "ghcr.io/thaynes43/haynes-studio-web@$web" --arg sidecar "$sidecar" '
    def containers: .spec.template.spec.containers | map({key: .name, value: .image}) | from_entries;
    def pair($a; $w): {
      "studio-api": {api:$a, "media-worker-routes":$w}, "studio-mcp": {mcp:$a}, "studio-web": {nginx:$w},
      "studio-worker": {worker:$a}, "studio-voice-worker": {worker:$a}, "studio-image-worker": {worker:$a}
    };
    pair($old; $old_web) as $predecessor |
    (pair($api; $web) + {"studio-matte-worker": {worker:$api, matting:$sidecar}}) as $target |
    [.items[] | {key: .metadata.name, value: containers}] | from_entries | . as $actual |
    select(all($predecessor | keys[]; . as $name |
      ($actual[$name] == $predecessor[$name] or $actual[$name] == $target[$name]))) |
    select($actual["studio-matte-worker"] == null or $actual["studio-matte-worker"] == $target["studio-matte-worker"]) |
    select($actual["studio-api"] == $target["studio-api"] or
      all(["studio-worker", "studio-voice-worker", "studio-image-worker", "studio-matte-worker"][];
        . as $name | $actual[$name] == $predecessor[$name])) |
    any($target | keys[]; . as $name | $actual[$name] == $target[$name]) | tostring'
}

release_sequence() {
  local advanced
  flux_state pending || return 1
  advanced=$(release_workloads) || return 1
  if [[ $advanced == true ]]; then migration; fi
}

save_predecessor_logs() {
  local name container dir="${receipt}.jobs"
  (umask 077; mkdir "$dir") || return 1
  for name in "${predecessor_jobs[@]}"; do
    container=register
    if [[ $name == migrate ]]; then container=migrate; fi
    successful_job_logs "studio-$name-${old_digest:7:12}" "$container" \
      > "$dir/$name.log" || return 1
    if [[ $container == migrate ]]; then
      # 0012's migration reports the revision as text.
      awk '$0 == "Alembic revision: 0012_release_constraints" { found=1 } END { exit !found }' \
        "$dir/$name.log" || return 1
    else
      registration_status "studio-${name#register-}" '[200,201]' < "$dir/$name.log" >/dev/null || return 1
    fi
  done
  job_archive=$dir
}

last_report() {
  awk '/^\{/ { report=$0 } END { if (report == "") exit 1; print report }'
}

registration_status() {
  local worker=$1 expected=$2 logs report
  logs=$(cat) || return 1
  report=$(last_report <<< "$logs") || return 1
  jq -e --arg worker "$worker" '.worker_id == $worker' <<< "$report" >/dev/null || return 1
  # studio-api-admin writes its status audit before the final worker ID result.
  jq -nRe --arg worker "$worker" --argjson expected "$expected" '
    [inputs | fromjson? | objects | select(.action == "worker_tokens.register" and .worker_id == $worker)] |
    select(length == 1) | .[0].status as $actual | select($expected | index($actual) != null) | $actual
  ' <<< "$logs"
}

successful_job_logs() {
  local job=$1 container=$2 uid pods count pod
  kubectl -n ai wait --for=condition=Complete --timeout=5s "job/$job" || return 1
  uid=$(kubectl -n ai get "job/$job" -o json | jq -er '
    select(any(.status.conditions[]?; .type == "Complete" and .status == "True")) |
    .metadata.uid | select(type == "string" and length > 0)') || return 1
  pods=$(kubectl -n ai get pods -l "batch.kubernetes.io/controller-uid=$uid" -o json |
    jq -ce --arg uid "$uid" --arg container "$container" '
      .items | map(select(.status.phase == "Succeeded" and
        any(.metadata.ownerReferences[]?; .kind == "Job" and .controller == true and .uid == $uid) and
        any(.status.containerStatuses[]?; .name == $container and .state.terminated.exitCode == 0)) |
        .metadata.name)') || return 1
  count=$(jq -er length <<< "$pods") || return 1
  case $count in
    0) printf 'FAIL completed Job %s has no succeeded pod for UID %s\n' "$job" "$uid" >&2; return 1 ;;
    1) pod=$(jq -er '.[0] | select(type == "string" and length > 0)' <<< "$pods") || return 1 ;;
    *) printf 'FAIL completed Job %s has more than one succeeded pod for UID %s\n' "$job" "$uid" >&2; return 1 ;;
  esac
  kubectl -n ai logs "$pod" -c "$container"
}

ready() {
  kubectl -n ai wait --for=condition=Ready --timeout=30s "$@"
}

read_endpoint() {
  kubectl -n ai exec "$1" -c "$2" -- python -B -c '
import sys
from urllib.request import urlopen
with urlopen(sys.argv[1], timeout=5) as response:
    sys.stdout.buffer.write(response.read())
' "$3"
}

wan_idle() {
  read_endpoint deploy/wan-video api http://127.0.0.1:8080/v1/ready |
    jq -es 'length == 1 and (.[0] | .ready == true and
      has("active_video_job") and .active_video_job == null and
      has("active_foley_job") and .active_foley_job == null)' >/dev/null
}

images_idle() {
  read_endpoint comfyui-0 app http://127.0.0.1:8188/queue |
    jq -es 'length == 1 and (.[0] | .queue_running == [] and .queue_pending == [])' >/dev/null
}

node_idle() {
  local load
  load=$(kubectl top node talosw01 --no-headers) || return 1
  printf '%s\n' "$load"
  awk 'NF == 5 && $3 ~ /^[0-9]+%$/ && $5 ~ /^[0-9]+%$/ && $3+0 <= 10 && $5+0 <= 25 {
      passed=1 } END { exit !passed }' <<< "$load"
}

node_capacity() {
  kubectl get node talosw01 -o json |
    jq -er '.status.allocatable |
      select((.cpu | tonumber) >= 24 and (.memory | test("^[0-9]+Ki$")) and
        (.memory | sub("Ki$"; "") | tonumber) >= (51 * 1024 * 1024))'
}

render_idle() {
  local mode=$1 load
  load=$(kubectl -n ai top pod --containers -l app.kubernetes.io/name=studio-render --no-headers) || return 1
  printf '%s\n' "$load"
  awk '$2 == "app" { seen++; if ($3 ~ /^[0-9]+m$/ && $3+0 <= 250) good++ }
    END { exit !(seen == 1 && good == 1) }' <<< "$load" || return 1
  kubectl -n ai exec deploy/studio-render -c app -- /tmp/venv/bin/python -I -B -c '
import os
import sys
from pathlib import Path
import av

idle = b"sleep\0infinity\0"
init = b"/usr/local/bin/python\0-B\0/config/render-init.py\0"
pid_one = Path("/proc/1/cmdline").read_bytes()
other = [path for path in Path("/proc").iterdir()
         if path.name.isdecimal() and int(path.name) not in (1, os.getpid())]
assert Path("/tmp/venv/.complete").is_file()
if av.__version__ == "18.1.0":
    assert pid_one == idle and not other
else:
    assert sys.argv[1] == "retry" and av.__version__ == "18.1.1"
    assert pid_one.startswith(init) and len(other) == 1
    assert (other[0] / "cmdline").read_bytes() == idle
    assert "PPid:\t1" in (other[0] / "status").read_text().splitlines()
print("Idle render venv complete (PyAV", av.__version__ + ")")
' "$mode"
}

weights_pin() {
  kubectl -n ai exec deploy/studio-render -c app -- sh -c '
    weights=/studio/jobs/$1/weights
    read -r pinned < "$weights/profile.sha256" || exit 1
    found=$(sha256sum "$weights/profile.json") || exit 1
    test "$pinned" = "$2" && test "${found%% *}" = "$2"
  ' sh "$allocation" "$profile"
}

lan() {
  local path=$1 expected=$2 result
  result=$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' "$base/$path") || return 1
  printf 'HTTP %s /%s\n' "$result" "$path"
  [[ $result == "$expected" ]]
}

postgres_ready() {
  kubectl -n database get cluster postgres16 -o json |
    jq -e '.spec.instances == 3 and .status.readyInstances == 3' >/dev/null
}

ready_api_pod() {
  local build=$1
  kubectl -n ai get pods -l app.kubernetes.io/name=studio-api -o json |
    jq -er --arg image "ghcr.io/thaynes43/haynes-studio@$build" --arg digest "$build" '
      first(.items | sort_by(.metadata.name) | .[] |
        select(.metadata.deletionTimestamp == null and
          any(.status.conditions[]?; .type == "Ready" and .status == "True") and
          any(.spec.containers[]?; .name == "api" and .image == $image) and
          any(.status.containerStatuses[]?; .name == "api" and .ready and
            (.imageID | endswith($digest)))) | .metadata.name)'
}

data_probe() {
  local mode=$1 build=$2 digest=$3 pod report output
  if pod=$(ready_api_pod "$digest"); then
    :
  elif [[ $mode == rollback-baseline ]] && pod=$(ready_api_pod "$api"); then
    build=$revision
  elif [[ $phase == verify ]] && pod=$(ready_api_pod "$old_digest"); then
    build=$old_revision
  else
    printf 'FAIL no Ready API pod on an admitted build\n' >&2
    return 1
  fi
  printf 'API snapshot pod %s at Studio %s\n' "$pod" "$build" >&2
  output=$(kubectl -n ai exec -i "$pod" -c api -- python -I -B - "$mode" "$build" \
    < "$script_dir/op967-data.py") || return 1
  report=$(last_report <<< "$output") || return 1
  jq -e 'has("store") and has("runs") and has("deliveries") and has("delivery media") and has("media jobs")' <<< "$report" \
    >/dev/null || return 1
  printf '%s\n' "$report"
  case $mode in
    retry-preflight)
      jq -es 'length == 2 and .[0] == .[1]' "$prior_baseline" - <<< "$report" >/dev/null || return 1
      (umask 077; set -o noclobber; printf '%s\n' "$report" > "$baseline") ;;
    preflight|rollback-baseline)
      (umask 077; set -o noclobber; printf '%s\n' "$report" > "$baseline") ;;
    postflight|compare)
      if [[ $phase != verify || -n $baseline ]]; then
        jq -es 'length == 2 and .[0] == .[1]' "$baseline" - <<< "$report" >/dev/null
      fi ;;
  esac
}

pins() {
  local release_api=$1 release_web=$2 include_matte=$3 layout problems=0
  layout=$(jq -cn --arg api "ghcr.io/thaynes43/haynes-studio@$release_api" \
    --arg web "ghcr.io/thaynes43/haynes-studio-web@$release_web" --arg matting "$sidecar" '
    {
      api: {api:$api, "media-worker-routes":$web}, mcp: {mcp:$api}, web: {nginx:$web},
      worker: {worker:$api}, "voice-worker": {worker:$api}, "image-worker": {worker:$api},
      "matte-worker": {worker:$api, matting:$matting}
    }') || return 1
  local workload expected replicas placement
  local workers=(api mcp web worker voice-worker image-worker)
  if [[ $include_matte == yes ]]; then workers+=(matte-worker); fi
  for workload in "${workers[@]}"; do
    expected=$(jq -cer --arg name "$workload" '.[$name]' <<< "$layout") || return 1
    replicas=1
    placement=any
    case $workload in
      api|mcp|web) replicas=2 ;;
      worker|matte-worker) placement=talosw01 ;;
      *) placement=off-talosw01 ;;
    esac
    check "studio-$workload pod image, placement and restart counts" pods "$workload" "$expected" "$replicas" "$placement"
    if [[ ${results[-1]} == 'FAIL '* ]]; then problems=1; fi
  done
  (( ! problems ))
}

pods() {
  local workload=$1 containers=$2 replicas=$3 placement=$4
  kubectl -n ai get pods -l "app.kubernetes.io/name=studio-$workload" -o json |
    jq -er --argjson containers "$containers" --argjson replicas "$replicas" --arg placement "$placement" '
      .items | select(length == $replicas and
        ([.[].spec.nodeName] | unique | length) == $replicas and
        all(.[]; .metadata.deletionTimestamp == null and .spec.nodeName != null and
          ($placement == "any" or ($placement == "talosw01" and .spec.nodeName == "talosw01") or
           ($placement == "off-talosw01" and .spec.nodeName != "talosw01")) and
          any(.status.conditions[]?; .type == "Ready" and .status == "True") and
          ([.spec.containers[] | {key:.name,value:.image}] | from_entries) == $containers and
          (.status.containerStatuses | length) == ($containers | length) and
          all(.status.containerStatuses[]; . as $c |
            ($containers[$c.name] // "" | split("@") | .[1]) as $digest |
            $digest != null and .ready and .restartCount == 0 and (.imageID | endswith($digest))))) |
      .[] | [.metadata.name, .spec.nodeName, (.status.containerStatuses[].imageID)] | @tsv'
}

api_endpoints() {
  local pods
  pods=$(kubectl -n ai get pods -l app.kubernetes.io/name=studio-api -o json) || return 1
  kubectl -n ai get endpointslices -l kubernetes.io/service-name=studio-api -o json |
    jq -e --arg digest "${1:-$api}" --argjson pods "$pods" '
      $pods.items as $p |
      ($p | length) == 2 and
      all($p[]; .metadata.deletionTimestamp == null and
        any(.status.containerStatuses[]?; .name == "api" and .ready and (.imageID | endswith($digest)))) and
      ([.items[].endpoints[] | select(.conditions.ready == true) | .targetRef.uid] | unique)
        == ([$p[].metadata.uid] | unique)' >/dev/null
}

registration() {
  local lane=$1 expected=$2 stem=studio-register-worker
  if [[ $lane != worker ]]; then stem=studio-register-$lane; fi
  local job manifest
  case $lane in
    worker) manifest=worker-registration/job.yaml ;;
    matte-worker) manifest=matte-worker-registration/job.yaml ;;
    *) manifest="media-worker-registration/${lane%-worker}-job.yaml" ;;
  esac
  job=$(yq -er 'select(.kind == "Job") | .metadata.name' "$root/$manifest") || return 1
  [[ $job =~ ^$stem-${api:7:12}(-r[1-9][0-9]*)?$ ]] || return 1
  local logs
  logs=$(successful_job_logs "$job" register) || return 1
  registration_status "studio-$lane" "$expected" <<< "$logs"
}

registration_audits() {
  check 'run worker registration reused its token' registration worker '[200]'
  check 'voice worker registration reused its token' registration voice-worker '[200]'
  check 'image worker registration reused its token' registration image-worker '[200]'
  check 'matte grant registered or reused the same token on retry' registration matte-worker '[201,200]'
}

migration() {
  local job logs report
  job=$(yq -er 'select(.kind == "Job") | .metadata.name' "$root/migration/job.yaml") || return 1
  logs=$(successful_job_logs "$job" migrate) || return 1
  report=$(last_report <<< "$logs") || return 1
  jq -e 'select(.revision == "0013_matte_jobs" and .user == "studio_migrator" and
    .database == "studio" and .tls == true and .hba_rejected == ["other database", "plaintext"])' \
    <<< "$report"
}

sidecar_ready() {
  kubectl -n ai exec deploy/studio-matte-worker -c matting -- studio-matting ready || return 1
  kubectl -n ai exec deploy/studio-matte-worker -c worker -- python -I -B -c '
import os
from pathlib import Path
from haynes_studio.matting.service import local_call
code, ready = local_call(Path("/matting/matte.sock"), "GET", "/v1/ready", timeout=2)
expected = os.environ["STUDIO_MATTING_PROFILE_SHA256"]
assert code == 200 and ready.get("profile_sha256") == expected
print("Matting profile SHA-256", expected)
'
}

matte_loop() {
  kubectl -n ai exec deploy/studio-matte-worker -c worker -- python -I -B -c '
from pathlib import Path
from haynes_studio.queue.progress import ProgressFile
args = Path("/proc/1/cmdline").read_bytes().strip(b"\0").split(b"\0")
assert b"studio-worker" in [item.rsplit(b"/", 1)[-1] for item in args]
assert args[-2:] == [b"--lane", b"matte"]
assert ProgressFile("/run/studio/worker-progress").fresh(300)
'
}

render_pin() {
  ready helmrelease/studio-render || return 1
  kubectl -n ai get pods -l app.kubernetes.io/name=studio-render -o json |
    jq -e '
      .items as $p | ($p | length) == 1 and
      ($p[0] | .metadata.deletionTimestamp == null and .spec.nodeName == "talosw01" and
       any(.status.conditions[]?; .type == "Ready" and .status == "True") and
       any(.spec.containers[]; .name == "app" and .resources.limits.cpu == "17" and
         .command == ["/usr/local/bin/python","-B","/config/render-init.py"]) and
       any(.status.containerStatuses[]; .name == "app" and .ready and .restartCount == 0))' >/dev/null
}

encoders() {
  local version reference= workload pod container interpreter names
  for workload in api worker voice-worker image-worker matte-worker render; do
    names=$(kubectl -n ai get pods -l "app.kubernetes.io/name=studio-$workload" -o json |
      jq -er '[.items[] | select(.metadata.deletionTimestamp == null) | .metadata.name] |
        select(length > 0) | .[]') || return 1
    while IFS= read -r pod; do
      container=worker
      interpreter=python
      case $workload in
        api) container=api ;;
        render) container=app; interpreter=/tmp/venv/bin/python ;;
      esac
      version=$(kubectl -n ai exec "$pod" -c "$container" -- "$interpreter" -I -B -c '
import av
import json
print(json.dumps({"pyav": av.__version__, "libraries": av.library_versions}, sort_keys=True))
') || return 1
      printf '%s %s\n' "$pod" "$version"
      jq -e '.pyav == "18.1.1" and (.libraries | length >= 7) and
        (.libraries.libavcodec | type == "array") and (.libraries.libavformat | type == "array")' \
        <<< "$version" >/dev/null || return 1
      if [[ -z $reference ]]; then reference=$version; fi
      [[ $version == "$reference" ]] || return 1
    done <<< "$names"
  done
}

worker_engine() {
  local workload=$1 service=$2
  kubectl -n ai exec "deploy/studio-$workload" -c worker -- python -I -B -c '
import os
import socket
import sys
from urllib.parse import urlsplit
import httpx
kind = sys.argv[1]
if kind == "image":
    with httpx.Client(timeout=5, trust_env=False) as client:
        response = client.get(os.environ["STUDIO_COMFYUI_URL"] + "/queue")
        response.raise_for_status()
        queue = response.json()
        assert all(isinstance(queue[key], list) for key in ("queue_running", "queue_pending"))
elif kind == "voice":
    with socket.create_connection((os.environ["STUDIO_TTS_HOST"], int(os.environ.get("STUDIO_TTS_PORT", "10210"))), 5):
        pass
elif kind == "run":
    endpoint = urlsplit(os.environ["STUDIO_WAN_URL"])
    with socket.create_connection((endpoint.hostname, endpoint.port), 5):
        pass
else:
    raise SystemExit(1)
' "$service"
}

matte_queue_idle() {
  local pod
  pod=$(ready_api_pod "$api") || return 1
  kubectl -n ai exec "$pod" -c api -- python -I -B -c '
import os
import psycopg

with psycopg.connect(os.environ["STUDIO_DATABASE_URL"], connect_timeout=5,
                     options="-c statement_timeout=5000") as conn:
    conn.read_only = True
    idle, = conn.execute("""
        SELECT NOT EXISTS (
            SELECT FROM studio.media_jobs
            WHERE lane = %s AND document->>%s = ANY(%s)
        )
    """, ("matte", "status", ["queued", "running", "held"])).fetchone()
    if not idle:
        print("Matte queue is no longer idle")
        raise SystemExit(1)
'
}

worker_routes() {
  local detail
  for detail in voice-worker:voice image-worker:image worker:run matte-worker:matte; do
    check "${detail#*:} worker token and listener" kubectl -n ai exec -i "deploy/studio-${detail%:*}" \
      -c worker -- python -I -B - "${detail#*:}" < "$script_dir/op967-routes.py"
  done
}

matte_removed() {
  local result
  result=$(kubectl -n ai get deploy/studio-matte-worker --ignore-not-found -o name) || return 1
  [[ -z $result ]]
}

installed_policies() {
  local namespaced clusterwide endpoints api_pods matte_pods
  namespaced=$(kubectl -n ai get networkpolicies.networking.k8s.io,ciliumnetworkpolicies.cilium.io -o json) || return 1
  clusterwide=$(kubectl get ciliumclusterwidenetworkpolicies.cilium.io -o json) || return 1
  endpoints=$(kubectl -n ai get ciliumendpoints.cilium.io -o json) || return 1
  api_pods=$(kubectl -n ai get pods -l app.kubernetes.io/name=studio-api -o json) || return 1
  matte_pods=$(kubectl -n ai get pods -l app.kubernetes.io/name=studio-matte-worker -o json) || return 1
  printf '%s\n' "$namespaced" "$clusterwide" "$endpoints" "$api_pods" "$matte_pods" |
    jq -s . | python3 -B scripts/tests/studio-release/check.py --installed-policies
}

case $phase in
  migration|roll|postflight|rollback-baseline|compare)
    check 'passing preflight receipt for the selected merged Ops revision' release_admitted
    if ((failed)); then finish; fi
    ;;
  verify)
    check 'selected fourth-pin Ops revision on main' selected_checkout
    if ((failed)); then finish; fi
    ;;
esac

case $phase in
  admit-merge)
    check 'passing preflight receipt within the merge window' merge_admitted
    ;;
  preflight|retry-preflight)
    check 'new private receipt and baseline paths supplied' receipt_paths
    check 'reviewed Ops checkout has a clean tracked tree' checkout
    if ((failed)); then finish; fi
    started=$(date -u +%s) || exit 1
    mode=release
    render_mode=predecessor
    predecessor_flux_stages=(kustomization/studio-migration kustomization/studio
      kustomization/studio-worker-registration kustomization/studio-worker
      kustomization/studio-media-worker-registration kustomization/studio-media-workers)
    if [[ $phase == retry-preflight ]]; then
      mode=retry
      render_mode=retry
      predecessor_flux_stages=()
      check 'previous admission and archived predecessor Job logs verified' retry_evidence
      if (( ! failed )); then
        check 'replacement migration Job and Flux health check match the terminal failure' retry_job
      fi
      check 'matte workload absent' matte_removed
    fi
    check 'three postgres16 instances Ready' postgres_ready
    check 'installed dependencies and predecessor credential projections Ready' ready \
      kustomization/studio-database kustomization/studio-oidc "${predecessor_flux_stages[@]}" \
      helmrelease/studio-render helmrelease/kokoro helmrelease/comfyui \
      externalsecret/studio-runtime-db externalsecret/studio-migrator-db \
      externalsecret/studio-oidc-client externalsecret/studio-worker-token \
      externalsecret/studio-image-worker-token externalsecret/studio-voice-worker-token
    check 'third-pin workload set' pins "$old_digest" "$old_web" no
    check 'old API endpoints' api_endpoints "$old_digest"
    check 'talosw01 capacity' node_capacity
    check 'talosw01 CPU <=10%, memory <=25%' node_idle
    check 'offline render app idle and admitted venv complete' render_idle "$render_mode"
    check 'Wan ready and both active jobs null' wan_idle
    check 'shared ComfyUI queue empty' images_idle
    check 'retained matting allocation exact profile' weights_pin
    check 'LAN root available' lan '' 200
    check 'LAN API anonymous access refused' lan v1/me 401
    if (( ! failed )); then
      if [[ $mode == release ]]; then
        check 'predecessor Job logs saved' save_predecessor_logs
      fi
      check 'empty queues, archived data and store baseline' data_probe "$phase" "$old_revision" "$old_digest"
    fi
    if ! write_receipt "$mode"; then
      printf 'FAIL cannot write a new preflight receipt\n' >&2
      exit 1
    fi
    printf 'Preflight receipt %s\n' "$receipt"
    ;;
  migration)
    check 'Flux stages and workload images on the selected release sequence' release_sequence
    if ((failed)); then finish; fi
    check 'Flux source revision and inventory' reconcile cluster-apps flux-system 7m
    if ((failed)); then finish; fi
    check 'API pod sample' kubectl -n ai get pods -l app.kubernetes.io/name=studio-api \
      -o 'custom-columns=NAME:.metadata.name,IMAGE:.status.containerStatuses[0].imageID'
    if ((failed)); then finish; fi
    check 'migration stage reconciliation' reconcile studio-migration ai 7m
    if ((failed)); then finish; fi
    check 'migration Job completed at 0013 with correct identity' migration
    check 'LAN root remains available' lan '' 200
    check 'LAN API still refuses anonymous access' lan v1/me 401
    ;;
  roll)
    check 'Flux stages and workload images on the selected release sequence' release_sequence
    if ((failed)); then finish; fi
    check 'migration Job completed at 0013 with correct identity' migration
    if ((failed)); then finish; fi
    for stage in "${studio_stages[@]}" "${matte_stages[@]}"; do
      if [[ $stage == studio-migration ]]; then continue; fi
      timeout=7m
      if [[ $stage == studio-render ]]; then timeout=23m; fi
      check "reconcile $stage" reconcile "$stage" ai "$timeout"
      if ((failed)); then finish; fi
    done
    check 'Flux stages Ready at the selected release or a matching descendant' flux_state complete
    registration_audits
    ;;
  postflight|verify)
    check 'Flux stages Ready at the selected release or a matching descendant' flux_state complete
    check 'migration Job completed at 0013 with correct identity' migration
    registration_audits
    check 'fourth-pin workload set' pins "$api" "$web" yes
    check 'both selected API endpoints Ready' api_endpoints
    check 'installed effective matte policy and API ingress' installed_policies
    check 'offline render app on the 17-CPU split and forwarding init' render_pin
    check 'exact sidecar profile reached from the matte worker' sidecar_ready
    check 'matte worker entered its normal polling loop and reports progress' matte_loop
    check 'image worker reaches the shared queue' worker_engine image-worker image
    check 'voice worker reaches Kokoro' worker_engine voice-worker voice
    check 'run worker reaches the Wan listener' worker_engine worker run
    check 'one PyAV 18.1.1 encoder and bundled libraries' encoders
    check 'LAN root available' lan '' 200
    check 'LAN API still refuses anonymous access' lan v1/me 401
    snapshot_check='all queues idle, grants present and archived data and store identical'
    if [[ $phase == verify && -z $baseline ]]; then
      snapshot_check='all queues idle, grants present and archived data and store read'
    fi
    check "$snapshot_check" data_probe postflight "$revision" "$api"
    if (( ! failed )); then
      if [[ $phase == postflight ]]; then
        check 'passing preflight receipt before listener probes' release_admitted
      fi
      check 'Flux stages still Ready before listener probes' flux_state complete
    fi
    if (( ! failed )); then worker_routes; fi
    if [[ $phase == postflight ]]; then
      if (( ! failed )); then
        check 'passing preflight receipt before the idle claim' release_admitted
      fi
      if (( ! failed )); then
        check 'matte queue still idle before the claim' matte_queue_idle
      fi
      if (( ! failed )); then
        check 'matte idle claim over port 8002' kubectl -n ai exec -i deploy/studio-matte-worker \
          -c worker -- python -I -B - matte idle-claim < "$script_dir/op967-routes.py"
      else
        printf 'FAIL idle claim skipped after unsuccessful postflight checks\n' >&2
      fi
    fi
    ;;
  rollback-baseline)
    check 'Flux stages and workload images on the selected release sequence' release_sequence
    receipt=${STUDIO_OP967_ROLLBACK_RECEIPT:-}
    baseline=${STUDIO_OP967_ROLLBACK_BASELINE:-}
    check 'new private receipt and baseline paths supplied' receipt_paths
    if ((failed)); then finish; fi
    started=$(date -u +%s) || exit 1
    check 'snapshots before a workload rollback' data_probe rollback-baseline "$old_revision" "$old_digest"
    if ! write_receipt rollback; then
      printf 'FAIL cannot write a new rollback receipt\n' >&2
      exit 1
    fi
    ;;
  compare)
    receipt=${STUDIO_OP967_ROLLBACK_RECEIPT:-}
    baseline=${STUDIO_OP967_ROLLBACK_BASELINE:-}
    check 'passing rollback snapshot for the selected release' rollback_admitted "${ROLLBACK_OPS_REVISION:-}"
    if ((failed)); then finish; fi
    check 'Flux stages Ready on the selected rollback sequence' flux_state rollback "$ROLLBACK_OPS_REVISION"
    check 'third-pin workload set' pins "$old_digest" "$old_web" no
    check 'matte worker removed' matte_removed
    check 'old API endpoints' api_endpoints "$old_digest"
    check 'LAN root available on previous build' lan '' 200
    check 'LAN API still refuses anonymous access' lan v1/me 401
    check '0013 retained and archived data and store identical' data_probe compare "$old_revision" "$old_digest"
    ;;
  *) printf 'Usage: bash %s {preflight|retry-preflight|admit-merge|migration|roll|postflight|verify|rollback-baseline|compare}\n' "$0" >&2; exit 2 ;;
esac
finish
