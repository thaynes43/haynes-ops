#!/usr/bin/env bash
# Deterministic Tier-4 upgrade health gate (phase 4a). READ + PAGE only, no LLM.
# Runs the six upgrade-health-gate.md checks; pages ONLY on a regression that
# persisted past one poll cycle (stateless: Prometheus `offset` + Flux
# lastTransitionTime are the prior-state stores). Pages DIRECTLY to Pushover, so an
# Alertmanager outage can't mute it. Always exits 0 (a Job failure is not the signal).
set -uo pipefail

GATE_DIR="$(cd "$(dirname "$0")" && pwd)"
PROM="${PROMETHEUS_URL:-http://kube-prometheus-stack-prometheus.observability.svc.cluster.local:9090}"
HA="${HA_URL:-http://home-assistant.home-automation.svc.cluster.local:8123}"
OFFSET="${PERSIST_OFFSET:-10m}"
FLUX_STALE="${FLUX_STALE_SECONDS:-2700}"
REGRESSIONS=0
NOW="$(date -u +%s)"

log()  { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }
page() { "$GATE_DIR/page.sh" "$@"; REGRESSIONS=$((REGRESSIONS+1)); }

# Prometheus instant query -> series count / first value / joined label values.
prom_count() { curl -sf --max-time 15 "$PROM/api/v1/query" --data-urlencode "query=$1" 2>/dev/null | jq -r '.data.result | length' 2>/dev/null; }
prom_val()   { curl -sf --max-time 15 "$PROM/api/v1/query" --data-urlencode "query=$1" 2>/dev/null | jq -r '.data.result[0].value[1] // "NaN"' 2>/dev/null; }
prom_names() { curl -sf --max-time 15 "$PROM/api/v1/query" --data-urlencode "query=$1" 2>/dev/null | jq -r "[.data.result[].metric.$2] | unique | join(\",\")" 2>/dev/null; }
gt0() { [ -n "$1" ] && [ "$1" -gt 0 ] 2>/dev/null; }

# ---- Gate self-health: if we can reach NEITHER the API server NOR Prometheus we are
#      blind — page a warning and bail so a silently-broken gate is never "green". ----
kubectl version -o json >/dev/null 2>&1; API_OK=$?
[ "$(prom_count 'vector(1)')" = "1" ]; PROM_OK=$?
if [ "$API_OK" -ne 0 ] && [ "$PROM_OK" -ne 0 ]; then
  page warning gate-blind self "Gate cannot reach the API server OR Prometheus — health unknown."
  exit 0
fi

# ---- Check 1: Flux (kubectl ONLY — gotk_* unscraped). ks/HR NotReady past a settle
#      window; and the flux-system GitRepository must be Ready + freshly fetched. ----
if [ "$API_OK" -eq 0 ]; then
  for kind in kustomizations.kustomize.toolkit.fluxcd.io helmreleases.helm.toolkit.fluxcd.io; do
    bad="$(kubectl get "$kind" -A -o json 2>/dev/null | jq -r --argjson now "$NOW" '
      .items[] | . as $i
      | ( .status.conditions[]? | select(.type=="Ready" and .status!="True") ) as $c
      | ( $c.lastTransitionTime | sub("\\.[0-9]+";"") | fromdateiso8601 ) as $t
      | select( ($now - $t) > 600 )
      | "\($i.metadata.namespace)/\($i.metadata.name): \($c.message // "not ready")"' 2>/dev/null)"
    [ -n "$bad" ] && page critical flux "${kind%%.*}" "NotReady >10m: $(echo "$bad" | head -3 | tr '\n' ';')"
  done
  gr="$(kubectl -n flux-system get gitrepository flux-system -o json 2>/dev/null)"
  gr_ready="$(echo "$gr" | jq -r '.status.conditions[]? | select(.type=="Ready") | .status' 2>/dev/null)"
  gr_ts="$(echo "$gr" | jq -r '.status.artifact.lastUpdateTime // empty | sub("\\.[0-9]+";"") | fromdateiso8601' 2>/dev/null)"
  if [ -z "$gr_ready" ]; then
    page warning flux-blind flux-system "Cannot read flux-system GitRepository status — Flux dimension is blind."
  elif [ "$gr_ready" != "True" ]; then
    page critical flux flux-system "flux-system GitRepository Ready=$gr_ready (fetch failing)."
  elif [ -n "$gr_ts" ] && [ "$((NOW - gr_ts))" -gt "$FLUX_STALE" ]; then
    page critical flux flux-system "flux-system GitRepository last fetch >${FLUX_STALE}s ago — Flux stopped pulling main."
  fi
fi

if [ "$PROM_OK" -eq 0 ]; then
  # ---- Check 2: pods bad NOW and at offset (persisted past a cycle) => regression. ----
  waiting='kube_pod_container_status_waiting_reason{reason=~"CrashLoopBackOff|ImagePullBackOff|ErrImagePull|CreateContainerError|CreateContainerConfigError"} == 1'
  phase='kube_pod_status_phase{phase=~"Pending|Failed|Unknown"} == 1'
  q_pods="( (${waiting}) or (${phase}) ) and ( (${waiting}) or (${phase}) ) offset ${OFFSET}"
  gt0 "$(prom_count "$q_pods")" && page critical pods "$(prom_names "$q_pods" pod)" "Pod(s) unhealthy now AND ${OFFSET} ago (persisted): $(prom_names "$q_pods" pod)"

  # ---- Check 3: ExternalSecret Ready=False persisted past a cycle. ----
  q_eso='(externalsecret_status_condition{condition="Ready",status="False"} == 1) and (externalsecret_status_condition{condition="Ready",status="False"} offset '"$OFFSET"' == 1)'
  gt0 "$(prom_count "$q_eso")" && page critical eso "$(prom_names "$q_eso" name)" "ExternalSecret(s) Ready=False, persisted ${OFFSET}: $(prom_names "$q_eso" name)"

  # ---- Check 4: any firing severity=critical (already 'for:'-debounced by the rules). ----
  q_crit='ALERTS{alertstate="firing",severity="critical"}'
  gt0 "$(prom_count "$q_crit")" && page critical alerts "$(prom_names "$q_crit" alertname)" "Firing severity=critical: $(prom_names "$q_crit" alertname)"

  # ---- Check 5: Ceph HEALTH_ERR (==2) pages; WARN (==1) is a benign note. ----
  ceph="$(prom_val 'ceph_health_status')"
  if [ "$ceph" = "2" ]; then
    page critical ceph rook-ceph "Ceph HEALTH_ERR (ceph_health_status==2). Ceph majors are forward-only — a git revert does NOT recover the data plane."
  elif [ "$ceph" = "1" ]; then
    log "note: ceph HEALTH_WARN (==1) — benign unless a NEW OSD/PG/mon fault (see runbook allowlist)."
  fi
fi

# ---- Check 6: Home Assistant availability (only if HASS_TOKEN is set). ----
if [ -n "${HASS_TOKEN:-}" ]; then
  hastate() { curl -sf --max-time 10 -H "Authorization: Bearer $HASS_TOKEN" "$HA/api/states/$1" 2>/dev/null | jq -r '.state // "ERR"' 2>/dev/null; }
  z2m="$(hastate binary_sensor.zigbee2mqtt_bridge_connection_state)"
  [ "$z2m" = "off" ] && page critical ha zigbee2mqtt "Zigbee2MQTT bridge connection == off (mesh down / Z2M-HA restart race)."
  for lk in front_door_lock side_door_lock bulkhead_lock mudroom_door_lock; do
    st="$(hastate "lock.$lk")"
    case "$st" in unavailable|unknown) page critical ha "lock.$lk" "Door lock lock.$lk == $st." ;; esac
  done
  # Spa (Gecko in.touch3) is chronically flaky over RF — benign, deliberately NOT paged.
fi

# ---- Dead-man's-switch: a successful cycle pings the heartbeat (if configured). ----
[ -n "${GATE_HEARTBEAT_URL:-}" ] && curl -fsS --max-time 10 "$GATE_HEARTBEAT_URL" >/dev/null 2>&1
log "gate cycle complete: regressions=$REGRESSIONS"
exit 0
