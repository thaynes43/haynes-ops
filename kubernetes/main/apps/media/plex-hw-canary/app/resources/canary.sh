#!/bin/sh
# Daily Plex hardware-transcode canary (owner decision, 2026-10-12): once a morning, check that
# every Plex server still decodes AND encodes video in hardware, and log one logfmt verdict line
# per server for the Loki ruler (../lokirule.yaml). Runbook:
# .agents/runbooks/plex-igpu-transcode.md, "Canary".
#
# probe.sh does the check: inside the Plex pod via kubectl exec for the in-cluster servers (the
# token stays in the pod), and over HTTP for HaynesTower. A server with a stream gets no test; a
# real h264/hevc video transcode that is already running counts as the check (mode=observed).
# Busy and failed servers are retried every INTERVAL_SECONDS, up to ATTEMPTS times.
#
# State lives in the ConfigMap $STATE_CONFIGMAP, one key per server:
#   streak          consecutive completed checks that were not hardware (the alert fires at 2)
#   last_completed  epoch of the last completed check (the dead-man fires at STALE_HOURS)
#   last_result     hw | not-hw of that check
# Only a completed check (hw / not-hw) moves the streak. busy and error leave it alone, so a skip
# is never a failure. A server seen for the first time starts its dead-man clock now.
#
# Lines (all logfmt):
#   event=start  servers=...
#   event=attempt server=S attempt=N <probe line>
#   event=result server=S attempts=N streak=K stale_h=H last_result=R <probe line of the last attempt>
#   event=end    state=ok|error
set -u
NS=${NAMESPACE:-media}
STATE_CM=${STATE_CONFIGMAP:-plex-hw-canary-state}
K8S_SERVERS=${K8S_SERVERS:-plexops plex}
HTTP_SERVERS=${HTTP_SERVERS:-haynestower}
ATTEMPTS=${ATTEMPTS:-6}
INTERVAL=${INTERVAL_SECONDS:-1200}
PROBE=${PROBE:-/opt/plex-hw-canary/probe.sh}
RUN=$(date +%s)
SERVERS="$K8S_SERVERS $HTTP_SERVERS"

echo "event=start run=$RUN servers=\"$SERVERS\" attempts=$ATTEMPTS interval_s=$INTERVAL"

# $1 = server, $2 = session id. Prints the probe's result= line, or an error line.
probe() {
  case " $HTTP_SERVERS " in
    *" $1 "*)
      # <SERVER>_URL and <SERVER>_TOKEN, e.g. HAYNESTOWER_URL from the CronJob env.
      u=$(echo "$1" | tr 'a-z-' 'A-Z_')
      eval "url=\${${u}_URL:-} tok=\${${u}_TOKEN:-}"
      out=$(CANARY_PLEX_URL=$url CANARY_PLEX_TOKEN=$tok timeout 150 sh "$PROBE" "$2" 2>/dev/null)
      ;;
    *)
      pod=$(kubectl get pods -n "$NS" -l "app.kubernetes.io/name=$1" --field-selector=status.phase=Running \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
      [ -n "$pod" ] || { echo "result=error mode=none reason=no-running-pod"; return; }
      out=$(timeout 150 kubectl exec -n "$NS" "$pod" -c app -- sh -c "$(cat "$PROBE")" probe "$2" 2>/dev/null)
      ;;
  esac
  out=$(echo "$out" | grep -m1 '^result=')
  echo "${out:-result=error mode=none reason=probe-failed}"
}

pending=$SERVERS
attempt=0
while [ -n "$pending" ] && [ "$attempt" -lt "$ATTEMPTS" ]; do
  attempt=$((attempt + 1))
  [ "$attempt" -gt 1 ] && sleep "$INTERVAL"
  still=""
  for s in $pending; do
    v=$(echo "$s" | tr '-' '_')
    out=$(probe "$s" "plex-hw-canary-$s-$RUN-$attempt")
    echo "event=attempt server=$s attempt=$attempt $out"
    eval "res_$v=\$out; att_$v=\$attempt"
    case "$out" in
      "result=hw "* | "result=not-hw "*) ;;
      *) still="$still $s" ;;
    esac
  done
  pending=${still# }
done

field() { echo "$2" | tr ' ' '\n' | sed -n "s/^$1=//p" | head -1; }
now=$(date +%s)
state=ok
for s in $SERVERS; do
  v=$(echo "$s" | tr '-' '_')
  eval "out=\${res_$v:-result=error mode=none reason=not-run}; att=\${att_$v:-0}"
  result=$(field result "$out")
  if ! prev=$(kubectl get configmap -n "$NS" "$STATE_CM" -o "jsonpath={.data.$s}" 2>/dev/null); then
    # Without the previous state the streak and the dead-man clock are unknown: log the verdict
    # and fail the Job rather than reset them.
    state=error
    echo "event=result server=$s attempts=$att state=unreadable $out"
    continue
  fi
  streak=$(field streak "$prev")
  last=$(field last_completed "$prev")
  last_result=$(field last_result "$prev")
  streak=${streak:-0} last=${last:-$now} last_result=${last_result:-none}
  case "$result" in
    hw) streak=0 last=$now last_result=hw ;;
    not-hw) streak=$((streak + 1)) last=$now last_result=not-hw ;;
  esac
  kubectl patch configmap -n "$NS" "$STATE_CM" --type merge \
    -p "{\"data\":{\"$s\":\"streak=$streak last_completed=$last last_result=$last_result\"}}" >/dev/null 2>&1 ||
    state=error
  echo "event=result server=$s attempts=$att streak=$streak stale_h=$(((now - last) / 3600)) last_result=$last_result $out"
done
echo "event=end run=$RUN state=$state"
[ "$state" = ok ]
