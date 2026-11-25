#!/usr/bin/env bash
# Run from the reviewed or merged Ops checkout. Flux deploys from Git.
set -uo pipefail

root=kubernetes/main/apps/ai/studio
script_dir=scripts/studio-release
old_revision=80975a9e958d0bed78ab530c2b20f1279bf7ebe6
old_digest=sha256:08ac310f246cdc43225478e9496e480af6430b53751f4ae93f3e34206714c3e7
old_web=sha256:497e020b8846f09cdeab6d822aa1536dfeb325edd26205cd6e28f0061bb6d9ca
revision=$(yq -er '.revision' "$root/release.yaml") || exit 1
api=$(yq -er '.apiDigest' "$root/release.yaml") || exit 1
web=$(yq -er '.webDigest' "$root/release.yaml") || exit 1
phase=${1:-}
failed=0
results=()
receipt=${STUDIO_OP967_RECEIPT:-}
baseline=${STUDIO_OP967_BASELINE:-}
job_archive=
admission_window_seconds=3600
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
  'LAN root available'
  'LAN API anonymous access refused'
  'empty queues, archived data and store baseline'
)
retry_checks=(
  'original baseline and archived predecessor Job logs verified'
  'replacement migration Job and Flux health check match the terminal failure'
  'catalogue is complete 0012 or verified 0013'
)
rollback_checks=(
  'new private receipt and baseline paths supplied'
  'reviewed Ops checkout has a clean tracked tree'
  'selected fourth-pin Ops revision on main'
  'three postgres16 instances Ready'
  'offline render app idle and admitted venv complete'
  'LAN root available'
  'LAN API anonymous access refused'
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
        {schema: "studio-op967/v3", mode:$mode, ops_revision:$head, ops_tree:$tree,
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

verified_archive() {
  local evidence=$1 archived_hash
  job_archive=$(jq -er '.jobs_path | select(startswith("/"))' "$evidence") || return 1
  archived_hash=$(jq -er '.jobs_sha256 | select(test("^[a-f0-9]{64}$"))' "$evidence") || return 1
  if [[ $(archive_digest "$job_archive") != "$archived_hash" ]]; then
    printf 'FAIL predecessor Job archive missing or changed: %s\n' "$job_archive" >&2
    return 1
  fi
}

fetch_main() {
  git fetch --quiet origin main:refs/remotes/origin/main || {
    printf 'FAIL could not fetch Ops main before checking revisions\n' >&2
    return 1
  }
}

on_main() {
  [[ $1 =~ ^[0-9a-f]{40}$ ]] && git merge-base --is-ancestor "$1" refs/remotes/origin/main
}

selected_checkout() {
  fetch_main && checkout && [[ $head == "${MERGED_OPS_REVISION:-}" ]] && on_main "$head"
}

merge_admitted() {
  local mode=${1:-release} now labels
  [[ -f $receipt && -n $baseline ]] || return 1
  baseline=$(realpath -m -- "$baseline") || return 1
  fetch_main && checkout && git merge-base --is-ancestor refs/remotes/origin/main "$head" || return 1
  now=$(date -u +%s) || return 1
  local required_checks=("${preflight_checks[@]}")
  case $mode in
    release) required_checks+=('predecessor Job logs saved') ;;
    retry) required_checks+=("${retry_checks[@]}") ;;
    rollback) required_checks=("${rollback_checks[@]}") ;;
    *) return 1 ;;
  esac
  if [[ $mode != rollback ]]; then verified_archive "$receipt" || return 1; fi
  labels=$(printf '%s\n' "${required_checks[@]}" | jq -Rn '[inputs]') || return 1
  jq -e --arg mode "$mode" --arg head "$head" --arg tree "$tree" --arg baseline "$baseline" \
    --arg hash "$(file_digest "$baseline")" --argjson now "$now" \
    --argjson window "$admission_window_seconds" --argjson labels "$labels" '
    .schema == "studio-op967/v3" and .mode == $mode and .ops_revision == $head and .ops_tree == $tree and
    .passed == true and .baseline_path == $baseline and
    ($hash | test("^[a-f0-9]{64}$")) and .baseline_sha256 == $hash and
    all(.started_epoch, .finished_epoch; type == "number" and . == floor and . >= 0) and
    .started_epoch <= .finished_epoch and .finished_epoch <= $now and
    ($now - .started_epoch) < $window and
    ([.checks[].label] | sort) == ($labels | sort) and all(.checks[]; .result == "PASS")
  ' "$receipt" >/dev/null
}

retry_evidence() {
  local previous=${STUDIO_OP967_FAILED_OPS_REVISION:-} previous_receipt=${STUDIO_OP967_PREVIOUS_RECEIPT:-}
  fetch_main || return 1
  [[ $previous != "$head" && -f $previous_receipt ]] && on_main "$previous" || return 1
  git merge-base --is-ancestor "$previous" "$head" || return 1
  jq -e --arg tree "$(git rev-parse --verify "$previous^{tree}")" '
    .ops_tree == $tree and .passed and (.mode == "release" or .mode == "retry")
  ' "$previous_receipt" >/dev/null || return 1
  prior_baseline=$(jq -er '.baseline_path' "$previous_receipt") || return 1
  [[ $(file_digest "$prior_baseline") == "$(jq -er '.baseline_sha256' "$previous_receipt")" ]] || return 1
  verified_archive "$previous_receipt" || return 1
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
  changed=$(git diff --name-only refs/remotes/origin/main "$head") || return 1
  [[ $changed == "$root/ks.yaml"$'\n'"$root/migration/job.yaml" ]] || return 1
  prior_gate=$(git show "$previous_ops_revision:$root/ks.yaml") || return 1
  [[ $(< "$root/migration/job.yaml") == "${source//name: $old_job/name: $new_job}" &&
     $(< "$root/ks.yaml") == "${prior_gate//name: $old_job/name: $new_job}" ]] || return 1
  kubectl -n ai get "job/$old_job" -o json | is_terminal_migration
}

is_terminal_migration() {
  jq -e --arg api "ghcr.io/thaynes43/haynes-studio@$api" '
    any(.spec.template.spec.containers[]?; .name == "migrate" and .image == $api) and
    any(.status.conditions[]?; .type == "Failed" and .status == "True") and
    all(.status.conditions[]?; .type != "Complete" or .status != "True") and
    (.status.active // 0) == 0' >/dev/null
}

migration_catalogue() {
  local primary
  primary=$(kubectl -n database get cluster postgres16 -o json | jq -er '
    .status.currentPrimary | select(type == "string" and startswith("postgres16-"))') || return 1
  kubectl -n database exec -i "$primary" -c postgres -- \
    env PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=5000 -c search_path=pg_catalog' \
    psql -X -w -h /controller/run -U postgres -d studio -qAt -v ON_ERROR_STOP=1 \
    < "$script_dir/op967-catalogue.sql"
}

retry_catalogue() {
  local state
  state=$(migration_catalogue) || return 1
  printf 'Migration catalogue: %s\n' "$state"
  case $state in
    0012_release_constraints) ;;
    0013_matte_jobs) snapshot_phase=retry-preflight-0013 ;;
    *) return 1 ;;
  esac
}

flux_state() {
  local expected=$1 source inventory stages
  source=$(kubectl -n flux-system get gitrepository haynes-ops -o json) || return 1
  inventory=$(kubectl -n flux-system get kustomization cluster-apps -o json) || return 1
  stages=$(kubectl -n ai get kustomizations -o json) || return 1
  fetch_main && on_main "$expected" || return 1
  printf '%s\n' "$source" "$inventory" "$stages" | jq -s . |
    python3 -B scripts/tests/studio-release/check.py --audit-flux "$expected"
}

release_workloads() {
  # Deployment templates may advance while predecessor pods still serve.
  # Only the two admitted image pairs are valid while Flux applies the pin.
  kubectl -n ai get deployments -o json | jq -er \
    --arg old "ghcr.io/thaynes43/haynes-studio@$old_digest" \
    --arg old_web "ghcr.io/thaynes43/haynes-studio-web@$old_web" \
    --arg api "ghcr.io/thaynes43/haynes-studio@$api" \
    --arg web "ghcr.io/thaynes43/haynes-studio-web@$web" '
    def containers: .spec.template.spec.containers | map({key: .name, value: .image}) | from_entries;
    def pair($a; $w): {
      "studio-api": {api:$a, "media-worker-routes":$w}, "studio-mcp": {mcp:$a}, "studio-web": {nginx:$w},
      "studio-worker": {worker:$a}, "studio-voice-worker": {worker:$a}, "studio-image-worker": {worker:$a}
    };
    pair($old; $old_web) as $predecessor |
    pair($api; $web) as $target |
    [.items[] | {key: .metadata.name, value: containers}] | from_entries | . as $actual |
    select(all($predecessor | keys[]; . as $name |
      ($actual[$name] == $predecessor[$name] or $actual[$name] == $target[$name]))) |
    select($actual["studio-api"] == $target["studio-api"] or
      all(["studio-worker", "studio-voice-worker", "studio-image-worker"][];
        . as $name | $actual[$name] == $predecessor[$name])) |
    any($target | keys[]; . as $name | $actual[$name] == $target[$name]) | tostring'
}

release_sequence() {
  local state=0 advanced
  flux_state "$1" || state=$?
  case $state in 0|2|3) ;; *) return 1 ;; esac
  advanced=$(release_workloads) || return 1
  if [[ $advanced == true || ${2:-} == rollback ]]; then
    if ((state == 3)); then
      printf 'FAIL workload advanced before the source reached the selected merge\n' >&2
      return 1
    fi
    if [[ ${2:-} == rollback ]]; then
      rollback_migration || return 1
    else
      migration || return 1
    fi
  fi
  return "$state"
}

rollback_source() {
  local forward=${MERGED_OPS_REVISION:-} release
  fetch_main && on_main "$forward" && [[ $forward != "$head" ]] &&
    git merge-base --is-ancestor "$forward" "$head" || return 1
  release=$(git show "$forward:$root/release.yaml") || return 1
  [[ $revision == "$(yq -er '.revision' - <<< "$release")" &&
     $api == "$(yq -er '.apiDigest' - <<< "$release")" &&
     $web == "$(yq -er '.webDigest' - <<< "$release")" ]]
}

rollback_sequence() {
  local state=0
  release_sequence "$MERGED_OPS_REVISION" rollback || state=$?
  ((state == 0 || state == 2))
}

rollback_migration() {
  local job installed state health_count
  job=$(yq -er 'select(.kind == "Job") | .metadata.name' "$root/migration/job.yaml") || return 1
  installed=$(kubectl -n ai get "job/$job" -o json) || return 1
  if ! is_terminal_migration <<< "$installed"; then
    migration
    return
  fi
  state=$(migration_catalogue) || return 1
  printf 'Terminal migration Job %s, catalogue: %s\n' "$job" "$state"
  [[ $state == 0013_matte_jobs ]] || return 1
  health_count=$(yq -er 'select(.kind == "Kustomization" and .metadata.name == "studio-migration") |
    .spec.healthChecks | length' "$root/ks.yaml") || return 1
  if [[ $health_count != 0 ]]; then
    printf 'FAIL rollback must remove the terminal migration Job health check\n' >&2
    return 1
  fi
}

wait_for_flux() {
  local expected=${ROLLBACK_OPS_REVISION:-$head} state now pickup_cutoff ready_cutoff=0
  pickup_cutoff=$(git show -s --format=%ct "$expected") || return 1
  pickup_cutoff=$((pickup_cutoff + 35 * 60))
  while true; do
    state=0
    if [[ -n ${ROLLBACK_OPS_REVISION:-} ]]; then
      flux_state "$expected" || state=$?
    else
      release_sequence "$expected" || state=$?
    fi
    now=$(date -u +%s) || return 1
    case $state in
      0) return 0 ;;
      2)
        if ((ready_cutoff == 0)); then ready_cutoff=$((now + 35 * 60)); fi
        if ((now >= ready_cutoff)); then
          printf 'FAIL Studio stages still pending 35 minutes after observing pickup\n' >&2
          return 1
        fi ;;
      3)
        if ((now >= pickup_cutoff)); then
          printf 'FAIL Flux has not picked up the selected merge within 35 minutes\n' >&2
          return 1
        fi ;;
      *) return 1 ;;
    esac
    if [[ -z ${ROLLBACK_OPS_REVISION:-} ]]; then
      kubectl -n ai get pods -l app.kubernetes.io/name=studio-api \
        -o 'custom-columns=NAME:.metadata.name,IMAGE:.status.containerStatuses[0].imageID' || return 1
      lan '' 200 && lan v1/me 401 || return 1
    fi
    sleep 15 || return 1
  done
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
        (.memory | sub("Ki$"; "") | tonumber) >= (40 * 1024 * 1024))'
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

lan() {
  local path=$1 expected=$2 result
  result=$(kubectl -n ai exec -i deploy/studio-mcp -c mcp -- python -I -B -c '
import http.client
import socket
import ssl
import sys

host = "studio.haynesops.com"
path = sys.argv[1]
with socket.create_connection(("traefik-internal.network.svc.cluster.local", 443), timeout=10) as connection:
    with ssl.create_default_context().wrap_socket(connection, server_hostname=host) as tls:
        tls.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode("ascii"))
        with http.client.HTTPResponse(tls) as response:
            response.begin()
            print(response.status)
  ' "/$path") || return 1
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
    retry-preflight|retry-preflight-0013)
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
  local release_api=$1 release_web=$2 layout problems=0
  layout=$(jq -cn --arg api "ghcr.io/thaynes43/haynes-studio@$release_api" \
    --arg web "ghcr.io/thaynes43/haynes-studio-web@$release_web" '
    {
      api: {api:$api, "media-worker-routes":$web}, mcp: {mcp:$api}, web: {nginx:$web},
      worker: {worker:$api}, "voice-worker": {worker:$api}, "image-worker": {worker:$api}
    }') || return 1
  local workload expected replicas placement
  for workload in api mcp web worker voice-worker image-worker; do
    expected=$(jq -cer --arg name "$workload" '.[$name]' <<< "$layout") || return 1
    replicas=1
    placement=any
    case $workload in
      api|mcp|web) replicas=2 ;;
      worker) placement=talosw01 ;;
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

render_pin() {
  ready helmrelease/studio-render || return 1
  kubectl -n ai get pods -l app.kubernetes.io/name=studio-render -o json |
    jq -e '
      .items as $p | ($p | length) == 1 and
      ($p[0] | .metadata.deletionTimestamp == null and .spec.nodeName == "talosw01" and
       any(.status.conditions[]?; .type == "Ready" and .status == "True") and
       any(.spec.containers[]; .name == "app" and .resources.limits.cpu == "20" and
         .command == ["/usr/local/bin/python","-B","/config/render-init.py"]) and
       any(.status.containerStatuses[]; .name == "app" and .ready and .restartCount == 0))' >/dev/null
}

encoders() {
  local version reference= workload pod container interpreter names
  for workload in api worker voice-worker image-worker render; do
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

worker_engines() {
  check 'image worker reaches the shared queue' worker_engine image-worker image
  check 'voice worker reaches Kokoro' worker_engine voice-worker voice
  check 'run worker reaches the Wan listener' worker_engine worker run
}

worker_routes() {
  local detail
  for detail in voice-worker:voice image-worker:image worker:run; do
    check "${detail#*:} worker token and listener" kubectl -n ai exec -i "deploy/studio-${detail%:*}" \
      -c worker -- python -I -B - "${detail#*:}" < "$script_dir/op967-routes.py"
  done
}

case $phase in
  wait|postflight|verify|compare)
    check 'selected fourth-pin Ops revision on main' selected_checkout
    if ((failed)); then finish; fi ;;
esac

case $phase in
  admit-merge)
    check 'passing preflight receipt within the merge window' merge_admitted "${2:-release}"
    ;;
  preflight|retry-preflight)
    check 'new private receipt and baseline paths supplied' receipt_paths
    check 'reviewed Ops checkout has a clean tracked tree' checkout
    if ((failed)); then finish; fi
    started=$(date -u +%s) || exit 1
    mode=release
    render_mode=predecessor
    snapshot_phase=$phase
    predecessor_flux_stages=(kustomization/studio-migration kustomization/studio
      kustomization/studio-worker-registration kustomization/studio-worker
      kustomization/studio-media-worker-registration kustomization/studio-media-workers)
    if [[ $phase == retry-preflight ]]; then
      mode=retry
      render_mode=retry
      predecessor_flux_stages=()
      check 'original baseline and archived predecessor Job logs verified' retry_evidence
      if (( ! failed )); then
        check 'replacement migration Job and Flux health check match the terminal failure' retry_job
      fi
      if (( ! failed )); then
        check 'catalogue is complete 0012 or verified 0013' retry_catalogue
      fi
    fi
    check 'three postgres16 instances Ready' postgres_ready
    check 'installed dependencies and predecessor credential projections Ready' ready \
      kustomization/studio-database kustomization/studio-oidc "${predecessor_flux_stages[@]}" \
      helmrelease/studio-render helmrelease/kokoro helmrelease/comfyui \
      externalsecret/studio-runtime-db externalsecret/studio-migrator-db \
      externalsecret/studio-oidc-client externalsecret/studio-worker-token \
      externalsecret/studio-image-worker-token externalsecret/studio-voice-worker-token
    check 'third-pin workload set' pins "$old_digest" "$old_web"
    check 'old API endpoints' api_endpoints "$old_digest"
    check 'talosw01 capacity' node_capacity
    check 'talosw01 CPU <=10%, memory <=25%' node_idle
    check 'offline render app idle and admitted venv complete' render_idle "$render_mode"
    check 'Wan ready and both active jobs null' wan_idle
    check 'shared ComfyUI queue empty' images_idle
    check 'LAN root available' lan '' 200
    check 'LAN API anonymous access refused' lan v1/me 401
    if (( ! failed )); then
      if [[ $mode == release ]]; then
        check 'predecessor Job logs saved' save_predecessor_logs
      fi
      check 'empty queues, archived data and store baseline' data_probe "$snapshot_phase" "$old_revision" "$old_digest"
    fi
    if ! write_receipt "$mode"; then
      printf 'FAIL cannot write a new preflight receipt\n' >&2
      exit 1
    fi
    printf 'Preflight receipt %s\n' "$receipt"
    ;;
  wait)
    check 'automatic Flux pickup and Studio stages Ready at the pin' wait_for_flux
    ;;
  postflight|verify)
    check 'Flux stages Ready at the selected release or a matching descendant' flux_state "$head"
    if ((failed)); then finish; fi
    check 'migration Job completed at 0013 with correct identity' migration
    registration_audits
    check 'fourth-pin workload set' pins "$api" "$web"
    check 'both selected API endpoints Ready' api_endpoints
    check 'offline render app at 20 CPUs with forwarding init' render_pin
    worker_engines
    check 'one PyAV 18.1.1 encoder and bundled libraries' encoders
    check 'LAN root available' lan '' 200
    check 'LAN API still refuses anonymous access' lan v1/me 401
    snapshot_check='all queues idle, grants present and archived data and store identical'
    if [[ $phase == verify && -z $baseline ]]; then
      snapshot_check='all queues idle, grants present and archived data and store read'
    fi
    check "$snapshot_check" data_probe postflight "$revision" "$api"
    if (( ! failed )); then
      check 'Flux stages still Ready before listener probes' flux_state "$head"
    fi
    if (( ! failed )); then worker_routes; fi
    ;;
  rollback-preflight)
    check 'new private receipt and baseline paths supplied' receipt_paths
    check 'reviewed Ops checkout has a clean tracked tree' checkout
    check 'selected fourth-pin Ops revision on main' rollback_source
    if ((failed)); then finish; fi
    started=$(date -u +%s) || exit 1
    check 'Flux stages and workload images on the selected release sequence' rollback_sequence
    check 'three postgres16 instances Ready' postgres_ready
    check 'offline render app idle and admitted venv complete' render_idle retry
    check 'LAN root available' lan '' 200
    check 'LAN API anonymous access refused' lan v1/me 401
    if (( ! failed )); then
      check 'snapshots before a workload rollback' data_probe rollback-baseline "$old_revision" "$old_digest"
    fi
    if ! write_receipt rollback; then
      printf 'FAIL cannot write a new rollback preflight receipt\n' >&2
      exit 1
    fi
    ;;
  compare)
    baseline=${STUDIO_OP967_ROLLBACK_BASELINE:-}
    check 'Flux stages Ready on the selected rollback sequence' flux_state "${ROLLBACK_OPS_REVISION:-}"
    if ((failed)); then finish; fi
    check 'third-pin workload set' pins "$old_digest" "$old_web"
    check 'old API endpoints' api_endpoints "$old_digest"
    worker_engines
    check 'LAN root available on previous build' lan '' 200
    check 'LAN API still refuses anonymous access' lan v1/me 401
    check '0013 retained and archived data and store identical' data_probe compare "$old_revision" "$old_digest"
    ;;
  *) printf 'Usage: bash %s {preflight|retry-preflight|rollback-preflight|admit-merge [release|retry|rollback]|wait|postflight|verify|compare}\n' "$0" >&2; exit 2 ;;
esac
finish
