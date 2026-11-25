#!/usr/bin/env bash
# Run from the pinned Ops checkout. Reconciliation is gated by a preflight receipt.
set -uo pipefail

root=kubernetes/main/apps/ai/studio
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
  [[ -n $receipt && -n $baseline && $receipt != "$baseline" ]]
}

write_receipt() {
  local finished utc snapshot=
  finished=$(date -u +%s) || return 1
  utc=$(date -u -d "@$finished" '+%Y-%m-%dT%H:%M:%SZ') || return 1
  if (( ! failed )); then
    snapshot=$(baseline_digest) || return 1
  fi
  (umask 077; set -o noclobber
    printf '%s\n' "${results[@]}" |
      jq -Rn --arg head "$head" --arg tree "$tree" --argjson started "$started" \
        --argjson finished "$finished" --arg utc "$utc" --argjson failed "$failed" --arg snapshot "$snapshot" '
        {schema: "studio-op967/v1", ops_revision:$head, ops_tree:$tree,
         started_epoch:$started, finished_epoch:$finished, finished_utc:$utc, passed:($failed == 0),
         baseline_sha256:$snapshot,
         checks:[inputs | {label:.[5:], result:.[0:4]}]}' > "$receipt"
  )
}

baseline_digest() {
  local checksum
  [[ -f $baseline ]] || return 1
  checksum=$(sha256sum < "$baseline") || return 1
  printf '%s\n' "${checksum%% *}"
}

release_admitted() {
  local merged_at now recorded
  [[ -f $receipt && $MERGED_OPS_REVISION =~ ^[0-9a-f]{40}$ ]] || return 1
  checkout || return 1
  [[ $head == "$MERGED_OPS_REVISION" ]] || return 1
  git merge-base --is-ancestor "$head" refs/remotes/origin/main || return 1
  merged_at=$(git show -s --format=%ct "$head") || return 1
  now=$(date -u +%s) || return 1
  jq -e --arg tree "$tree" --argjson merge "$merged_at" --argjson now "$now" '
    .schema == "studio-op967/v1" and .ops_tree == $tree and
    (.ops_revision | test("^[a-f0-9]{40}$")) and .passed == true and
    (.started_epoch | type == "number") and (.finished_epoch | type == "number") and
    .started_epoch <= .finished_epoch and .finished_epoch <= $merge and $merge <= $now and
    ($merge - .started_epoch) < 3600 and
    (.baseline_sha256 | test("^[a-f0-9]{64}$")) and
    ([.checks[].label] | index("predecessor Job logs saved") != null and
                        index("empty queues, archived data and store baseline") != null) and
    all(.checks[]; .result == "PASS")
  ' "$receipt" >/dev/null || return 1
  if [[ $phase == postflight ]]; then
    recorded=$(jq -er '.baseline_sha256' "$receipt") || return 1
    [[ $(baseline_digest) == "$recorded" ]] || return 1
  fi
}

selected_source() {
  kubectl -n flux-system get gitrepository haynes-ops -o json |
    jq -er --arg revision "$MERGED_OPS_REVISION" '
      .status.artifact.revision | select(. == ("main@sha1:" + $revision))'
}

reconcile() {
  local stage=$1 namespace=$2 timeout=$3
  selected_source || return 1
  flux reconcile kustomization "$stage" -n "$namespace" --timeout="$timeout"
}

save_predecessor_logs() {
  local name container dir="${receipt}.jobs"
  (umask 077; mkdir "$dir") || return 1
  for name in migrate register-worker register-voice-worker register-image-worker; do
    container=register
    if [[ $name == migrate ]]; then container=migrate; fi
    kubectl -n ai logs "job/studio-$name-${old_digest:7:12}" --all-pods=true -c "$container" \
      > "$dir/$name.log" || return 1
    [[ -s $dir/$name.log ]] || return 1
  done
}

last_report() {
  awk '/^\{/ { report=$0 } END { if (report == "") exit 1; print report }'
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
  local load
  load=$(kubectl -n ai top pod --containers -l app.kubernetes.io/name=studio-render --no-headers) || return 1
  printf '%s\n' "$load"
  awk '$2 == "app" { seen++; if ($3 ~ /^[0-9]+m$/ && $3+0 <= 250) good++ }
    END { exit !(seen == 1 && good == 1) }' <<< "$load" || return 1
  kubectl -n ai exec deploy/studio-render -c app -- /tmp/venv/bin/python -I -B -c '
import os
from pathlib import Path
import av

assert Path("/proc/1/cmdline").read_bytes() == b"sleep\0infinity\0"
other = [path.name for path in Path("/proc").iterdir()
         if path.name.isdecimal() and int(path.name) not in (1, os.getpid())]
print(len(other), "other render-container processes")
assert not other
assert Path("/tmp/venv/.complete").is_file() and av.__version__ == "18.1.0"
print("Predecessor render venv complete (PyAV 18.1.0)")
'
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
  [[ -n $baseline ]] || return 1
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
    preflight|rollback-baseline)
      (umask 077; set -o noclobber; printf '%s\n' "$report" > "$baseline") ;;
    postflight|compare)
      jq -es 'length == 2 and .[0] == .[1]' "$baseline" - <<< "$report" >/dev/null ;;
  esac
}

pins() {
  local release_api=$1 release_web=$2 include_matte=$3 layout
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
  done
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
  kubectl -n ai wait --for=condition=Complete --timeout=5s "job/$job" || return 1
  local logs report
  logs=$(kubectl -n ai logs "job/$job" -c register) || return 1
  report=$(last_report <<< "$logs") || return 1
  jq -er --arg worker "studio-$lane" --argjson expected "$expected" '
    select(.worker_id == $worker and (.status as $actual | $expected | index($actual) != null)) | .status
  ' <<< "$report"

}

migration() {
  local job
  job=$(yq -er 'select(.kind == "Job") | .metadata.name' "$root/migration/job.yaml") || return 1
  kubectl -n ai wait --for=condition=Complete --timeout=5s "job/$job" || return 1
  kubectl -n ai logs "job/$job" -c migrate |
    awk '/^Alembic revision: 0013_matte_jobs$/ { version++; print }
         /^studio_migrator: connected to studio over TLS$/ { tls++; print }
         /^studio_migrator: HBA rejected / { hba++; print }
         END { exit !(version == 1 && tls == 1 && hba == 2) }'
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

worker_routes() {
  local detail
  for detail in voice-worker:voice image-worker:image worker:run matte-worker:matte; do
    check "${detail#*:} worker token and listener" kubectl -n ai exec -i "deploy/studio-${detail%:*}" \
      -c worker -- python -I -B - "${detail#*:}" < "$script_dir/op967-routes.py"
  done
}

flux_current() {
  local expected=$1
  shift
  kubectl -n ai get kustomization "$@" -o json |
    jq -er --arg revision "$expected" --argjson count "$#" '
      .items | select(length == $count and all(.[];
        .status.lastAppliedRevision == ("main@sha1:" + $revision) and
        any(.status.conditions[]?; .type == "Ready" and .status == "True"))) |
      .[] | [.metadata.name, .status.lastAppliedRevision] | @tsv'
}

matte_removed() {
  local result
  result=$(kubectl -n ai get deploy/studio-matte-worker --ignore-not-found -o name) || return 1
  [[ -z $result ]]
}

case $phase in
  migration|roll|postflight|rollback-baseline|compare)
    MERGED_OPS_REVISION=${MERGED_OPS_REVISION:-}
    check 'passing preflight receipt for the selected merged Ops revision' release_admitted
    if ((failed)); then finish; fi
    ;;
esac

case $phase in
  preflight)
    check 'new private receipt and baseline paths supplied' receipt_paths
    check 'reviewed Ops checkout has a clean tracked tree' checkout
    if ((failed)); then finish; fi
    started=$(date -u +%s) || exit 1
    check 'three postgres16 instances Ready' postgres_ready
    check 'currently installed Flux stages and credential projections Ready' ready \
      kustomization/studio-database kustomization/studio-oidc kustomization/studio-migration \
      kustomization/studio kustomization/studio-worker-registration kustomization/studio-worker \
      kustomization/studio-media-worker-registration kustomization/studio-media-workers \
      helmrelease/studio-render helmrelease/kokoro helmrelease/comfyui \
      externalsecret/studio-runtime-db externalsecret/studio-migrator-db \
      externalsecret/studio-oidc-client externalsecret/studio-worker-token \
      externalsecret/studio-image-worker-token externalsecret/studio-voice-worker-token
    check 'third-pin workload set' pins "$old_digest" "$old_web" no
    check 'old API endpoints' api_endpoints "$old_digest"
    check 'talosw01 capacity' node_capacity
    check 'talosw01 CPU <=10%, memory <=25%' node_idle
    check 'offline render app idle and predecessor venv complete' render_idle
    check 'Wan ready and both active jobs null' wan_idle
    check 'shared ComfyUI queue empty' images_idle
    check 'retained matting allocation exact profile' weights_pin
    check 'LAN root available' lan '' 200
    check 'LAN API anonymous access refused' lan v1/me 401
    if (( ! failed )); then
      check 'predecessor Job logs saved' save_predecessor_logs
      check 'empty queues, archived data and store baseline' data_probe preflight "$old_revision" "$old_digest"
    else
      results+=("SKIP predecessor Job logs saved" "SKIP empty queues, archived data and store baseline")
    fi
    if ! write_receipt; then
      printf 'FAIL cannot write a new preflight receipt\n' >&2
      exit 1
    fi
    printf 'Preflight receipt %s\n' "$receipt"
    ;;
  migration)
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
    check 'migration Job completed at 0013 with correct identity' migration
    if ((failed)); then finish; fi
    for stage in studio-render studio studio-worker-registration studio-worker \
      studio-media-worker-registration studio-media-workers studio-matte-worker studio-matte-worker-registration; do
      timeout=7m
      if [[ $stage == studio-render ]]; then timeout=23m; fi
      check "reconcile $stage" reconcile "$stage" ai "$timeout"
      if ((failed)); then finish; fi
    done
    check 'Flux installed this revision and all apply stages are Ready' flux_current "$MERGED_OPS_REVISION" \
      studio-render studio-migration studio studio-worker-registration studio-worker \
      studio-media-worker-registration studio-media-workers studio-matte-worker studio-matte-worker-registration
    check 'run worker registration reused its token' registration worker '[200]'
    check 'voice worker registration reused its token' registration voice-worker '[200]'
    check 'image worker registration reused its token' registration image-worker '[200]'
    check 'matte grant registered or reused the same token on retry' registration matte-worker '[201,200]'
    ;;
  postflight)
    check 'fourth-pin workload set' pins "$api" "$web" yes
    check 'both selected API endpoints Ready' api_endpoints
    check 'offline render app on the 17-CPU split and forwarding init' render_pin
    check 'exact sidecar profile reached from the matte worker' sidecar_ready
    check 'matte worker entered its normal polling loop and reports progress' matte_loop
    check 'image worker reaches the shared queue' worker_engine image-worker image
    check 'voice worker reaches Kokoro' worker_engine voice-worker voice
    check 'run worker reaches the Wan listener' worker_engine worker run
    check 'one PyAV 18.1.1 encoder and bundled libraries' encoders
    check 'LAN root available' lan '' 200
    check 'LAN API still refuses anonymous access' lan v1/me 401
    check 'all queues idle, grants present and archived data and store identical' data_probe postflight "$revision" "$api"
    if (( ! failed )); then worker_routes; fi
    if (( ! failed )); then
      check 'matte idle claim over port 8002' kubectl -n ai exec -i deploy/studio-matte-worker \
        -c worker -- python -I -B - matte idle-claim < "$script_dir/op967-routes.py"
    else
      printf 'FAIL idle claim skipped after unsuccessful postflight checks\n' >&2
    fi
    ;;
  rollback-baseline)
    check 'snapshots before a workload rollback' data_probe rollback-baseline "$old_revision" "$old_digest"
    ;;
  compare)
    check 'third-pin workload set' pins "$old_digest" "$old_web" no
    check 'matte worker removed' matte_removed
    check 'old API endpoints' api_endpoints "$old_digest"
    check 'LAN root available on previous build' lan '' 200
    check 'LAN API still refuses anonymous access' lan v1/me 401
    check '0013 retained and archived data and store identical' data_probe compare "$old_revision" "$old_digest"
    ;;
  *) printf 'Usage: bash %s {preflight|migration|roll|postflight|rollback-baseline|compare}\n' "$0" >&2; exit 2 ;;
esac
finish
