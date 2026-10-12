#!/usr/bin/env bash
# Check that Plex on the iGPU twins can hardware-transcode.
#
#   scripts/plex-igpu/check.sh [plexops|plex|all] [--no-transcode] [--force]
#
# For each server it prints the guest i915 state of the node the pod runs on (read-only,
# Talos debugfs via an Omni-proxied talosconfig), then runs a short test transcode
# (~20 s, 4K HEVC -> 1080p/10 Mbps when the server has 4K, else its newest 1080p
# episode -> 720p/2 Mbps), samples /transcode/sessions and stops it. The test is skipped while
# anyone is streaming unless --force. The Plex token stays inside the pod.
# Exit 0 only when every checked server ran the test with vaapi decode AND encode.
# Runbook: .agents/runbooks/plex-igpu-transcode.md
set -euo pipefail

target=all transcode=1 force=0
for arg in "$@"; do
  case "$arg" in
    plexops | plex | all) target=$arg ;;
    --no-transcode) transcode=0 ;;
    --force) force=1 ;;
    *) echo "usage: $0 [plexops|plex|all] [--no-transcode] [--force]" >&2; exit 2 ;;
  esac
done
case "$target" in all) servers=(plexops plex) ;; *) servers=("$target") ;; esac

for cmd in kubectl talosctl; do
  command -v "$cmd" >/dev/null || { echo "plex-igpu: missing $cmd" >&2; exit 2; }
done
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
tc=${TALOSCONFIG:-}
if [[ -z $tc ]]; then
  tc=$tmp/talosconfig
  omnictl talosconfig "$tc" >/dev/null 2>&1 || { echo "plex-igpu: omnictl talosconfig failed" >&2; exit 2; }
fi

# Runs inside the Plex container (POSIX sh + curl). $1 = mode (probe|test), $2 = session id.
read -r -d '' in_pod <<'POD' || true
P="/config/Library/Application Support/Plex Media Server/Preferences.xml"
T=$(sed -n 's/.*PlexOnlineToken="\([^"]*\)".*/\1/p' "$P")
B=http://127.0.0.1:32400
get() { curl -s -H 'Accept: application/xml' "$B$1$(case "$1" in *\?*) echo '&';; *) echo '?';; esac)X-Plex-Token=$T"; }
attr() { grep -oE "$1=\"[^\"]*\"" | head -1 | cut -d'"' -f2; }
if [ "$1" = probe ]; then
  echo "streams=$(get /status/sessions | grep -oE '<MediaContainer size="[0-9]+"' | grep -oE '[0-9]+')"
  exit 0
fi
S=$2 KEY="" RES=1280x720 KBPS=2000
for sec in $(get /library/sections | grep -oE '<Directory [^>]*type="movie"[^>]*' | grep -oE ' key="[0-9]+"' | cut -d'"' -f2); do
  KEY=$(get "/library/sections/$sec/all?type=1&resolution=4k&videoCodec=hevc&X-Plex-Container-Start=0&X-Plex-Container-Size=1" | grep -oE '<Video [^>]*' | attr ratingKey)
  [ -n "$KEY" ] && { RES=1920x1080 KBPS=10000; break; }
done
if [ -z "$KEY" ]; then
  for sec in $(get /library/sections | grep -oE '<Directory [^>]*type="show"[^>]*' | grep -oE ' key="[0-9]+"' | cut -d'"' -f2); do
    KEY=$(get "/library/sections/$sec/all?type=4&resolution=1080&sort=addedAt:desc&X-Plex-Container-Start=0&X-Plex-Container-Size=1" | grep -oE '<Video [^>]*' | attr ratingKey)
    [ -n "$KEY" ] && break
  done
fi
[ -z "$KEY" ] && { echo "result=no-test-title"; exit 0; }
C="X-Plex-Client-Identifier=$S&X-Plex-Product=Plex%20Web&X-Plex-Platform=Chrome&X-Plex-Device=Linux&X-Plex-Token=$T"
Q="path=%2Flibrary%2Fmetadata%2F$KEY&mediaIndex=0&partIndex=0&protocol=hls&fastSeek=1&directPlay=0&directStream=0&directStreamAudio=1&videoQuality=100&videoResolution=$RES&maxVideoBitrate=$KBPS&session=$S&$C"
U=$B/video/:/transcode/universal
curl -s -o /dev/null "$U/decision?$Q"
# The transcoder starts once a client fetches the variant playlist and a segment.
V=$(curl -s "$U/start.m3u8?$Q" | grep -v '^#' | head -1 | tr -d '\r')
case "$V" in session/*) ;; *) echo "result=start-failed"; exit 0 ;; esac
SEG=$(curl -s "$U/$V" | grep -v '^#' | head -1 | tr -d '\r')
[ -n "$SEG" ] && curl -s -o /dev/null "$U/session/$S/base/$SEG?X-Plex-Token=$T"
LAST=""
for i in 1 2 3 4; do
  sleep 5
  LAST=$(get /transcode/sessions | grep -oE "<TranscodeSession [^>]*key=\"$S\"[^>]*|<TranscodeSession key=\"$S\"[^>]*" | head -1)
done
curl -s -o /dev/null "$U/stop?session=$S&$C"
echo "title=$KEY target=$RES@${KBPS}k source=$(echo "$LAST" | attr sourceVideoCodec)" \
  "hwRequested=$(echo "$LAST" | attr transcodeHwRequested)" \
  "hwDecoding=$(echo "$LAST" | attr transcodeHwDecoding)" \
  "hwEncoding=$(echo "$LAST" | attr transcodeHwEncoding)" \
  "speed=$(echo "$LAST" | attr speed | cut -c1-4)"
POD

fail=0
for app in "${servers[@]}"; do
  # Every probe below tolerates failure: a dead read prints "?" and fails the check
  # instead of aborting the run under set -e.
  pod=$(kubectl get pods -n media -l "app.kubernetes.io/name=$app" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [[ -z $pod ]]; then echo "$app: no pod found"; fail=1; continue; fi
  node=$(kubectl get pod -n media "$pod" -o jsonpath='{.spec.nodeName}' 2>/dev/null || true)
  ip=$(kubectl get node "$node" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || true)
  dbg() { talosctl --talosconfig "$tc" -n "$ip" read "/sys/kernel/debug/dri/0/$1" 2>/dev/null || true; }
  wedged=$(dbg i915_wedged)
  guc=$(dbg gt0/uc/guc_info | sed -n 's/^\s*status: //p' | head -1)
  huc=$(dbg gt1/uc/huc_info | sed -n 's/^\s*status: //p' | head -1)
  errlog=$(dbg gt0/uc/guc_load_err_log_dump | wc -c)
  echo "$app pod=$pod node=$node wedged=${wedged:-?} guc=${guc:-?} huc=${huc:-?} guc_load_err_log=${errlog}B"
  [[ $wedged == 0 && $guc == RUNNING ]] || fail=1
  ((transcode)) || continue
  streams=$(kubectl exec -n media "$pod" -c app -- sh -c "$in_pod" _ probe 2>/dev/null | sed -n 's/^streams=//p' || true)
  if [[ ${streams:-1} != 0 && $force == 0 ]]; then
    echo "$app transcode test skipped: ${streams:-?} active stream(s); rerun later or pass --force"
    fail=1
    continue
  fi
  result=$(kubectl exec -n media "$pod" -c app -- sh -c "$in_pod" _ test "igpu-check-$app-$(date +%s)" 2>/dev/null || echo "result=exec-failed")
  echo "$app transcode $result"
  [[ $result == *"hwDecoding=vaapi"* && $result == *"hwEncoding=vaapi"* ]] || fail=1
done
exit "$fail"
