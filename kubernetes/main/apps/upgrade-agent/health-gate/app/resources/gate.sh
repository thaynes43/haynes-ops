#!/usr/bin/env bash
# Deterministic Tier-4 upgrade health gate (phase 4a). READ + PAGE only, no LLM.
# An UPGRADE/DEPLOY tripwire — NOT a general-purpose alert relay. Pages ONLY when a human is
# needed for a deploy/upgrade problem; it deliberately does NOT duplicate Alertmanager (which
# already pages every critical alert with full context). Pages DIRECTLY to Pushover, so an
# Alertmanager outage can't mute it. Always exits 0 (a Job failure is not the signal).
#
# THE 2026-07 REDESIGN — remediation-aware, scoped paging. The gate and triage share a
# regression SIGNATURE (computed IDENTICALLY, see the shared block) and coordinate through the
# `upgrade-remediation-state` ConfigMap: triage writes class/attempted/result; the gate reads
# them and only stamps last_paged. Per-dimension paging policy:
#   * Flux Kustomization/HelmRelease NotReady  -> the gate is the ONLY catcher (gotk_* are not
#       scraped into Alertmanager). PAGE it with full context whenever persisted, EXCEPT while
#       remediate is actively landing a fix (suppress within the grace window).
#   * Persisted-unhealthy pods                 -> Alertmanager's KubePodCrashLooping already
#       covers general pod health. PAGE ONLY when the regression is upgrade-attributable AND
#       auto-remediation was attempted and FAILED. A non-upgrade pod flap does NOT page here.
#   * Firing critical alerts                   -> NOT checked at all (removed 2026-07): pure
#       Alertmanager duplication, and the source of context-poor "upgrade-gate: OOMKilled"
#       pages for unrelated self-healed apps.
#   * GitRepository / ExternalSecret / Ceph / Home-Assistant -> deploy/infra health the gate
#       reads directly; UNCHANGED, contextual, always-on.
# Every page names the specific resource(s) and states the remediation outcome. Coordination
# pages de-dupe to once per REPAGE_SUPPRESS_HOURS per signature. If state is unreadable, the
# Flux dimension FAILS SAFE (pages); the pod dimension fails silent (Alertmanager is the net).
set -uo pipefail

GATE_DIR="$(cd "$(dirname "$0")" && pwd)"
PROM="${PROMETHEUS_URL:-http://kube-prometheus-stack-prometheus.observability.svc.cluster.local:9090}"
HA="${HA_URL:-http://home-assistant.home-automation.svc.cluster.local:8123}"
OFFSET="${PERSIST_OFFSET:-10m}"
REPAGE_SUPPRESS_HOURS="${REPAGE_SUPPRESS_HOURS:-6}"
REMEDIATE_GRACE_MINUTES="${REMEDIATE_GRACE_MINUTES:-40}"
REGRESSIONS=0
NOW="$(date -u +%s)"

log()  { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }
page() { "$GATE_DIR/page.sh" "$@"; REGRESSIONS=$((REGRESSIONS+1)); }

# Prometheus instant query -> series count / first value / joined label values.
prom_count() { curl -sf --max-time 15 "$PROM/api/v1/query" --data-urlencode "query=$1" 2>/dev/null | jq -r '.data.result | length' 2>/dev/null; }
prom_val()   { curl -sf --max-time 15 "$PROM/api/v1/query" --data-urlencode "query=$1" 2>/dev/null | jq -r '.data.result[0].value[1] // "NaN"' 2>/dev/null; }
prom_names() { curl -sf --max-time 15 "$PROM/api/v1/query" --data-urlencode "query=$1" 2>/dev/null | jq -r "[.data.result[].metric.$2] | unique | join(\",\")" 2>/dev/null; }
gt0() { [ -n "$1" ] && [ "$1" -gt 0 ] 2>/dev/null; }

# ═══════════════════════════════════════════════════════════════════════════════════════
# SHARED coordination block — this block MUST stay byte-identical in BOTH
#   shepherd/app/resources/triage.sh  AND  health-gate/app/resources/gate.sh
# so the two independent CronJobs compute the SAME regression signature (the coordination
# key). They ship in SEPARATE ConfigMaps/images, so it is duplicated inline; edit both.
#
# The coordination set is Flux (Kustomization/HelmRelease) NotReady + persisted-unhealthy
# pods ONLY — the deploy-health signals an UPGRADE regresses through. Firing critical alerts
# are DELIBERATELY NOT here: Alertmanager already pages every critical with full context, and
# the gate must not be a second, context-poor alert relay (a self-healed soularr OOMKill once
# double-paged as a vague "upgrade-gate: OOMKilled" — the reason this was removed 2026-07).
# ═══════════════════════════════════════════════════════════════════════════════════════
STATE_NS="${UPGRADE_AGENT_NAMESPACE:-upgrade-agent}"
STATE_CM="${UPGRADE_REMEDIATION_STATE_CM:-upgrade-remediation-state}"
SIG_OFFSET="${COORD_SIG_OFFSET:-10m}"
# Canonical persisted-unhealthy-pods selectors. `offset` MUST directly follow a SELECTOR:
# `(expr) offset 10m` is a Prometheus PARSE error -> 400 -> empty body -> a false-green.
SIG_POD_WAITING='kube_pod_container_status_waiting_reason{reason=~"CrashLoopBackOff|ImagePullBackOff|ErrImagePull|CreateContainerError|CreateContainerConfigError"}'
SIG_POD_PHASE='kube_pod_status_phase{phase=~"Pending|Failed|Unknown"}'
SIG_POD_QUERY="( (${SIG_POD_WAITING} == 1) or (${SIG_POD_PHASE} == 1) ) and ( (${SIG_POD_WAITING} offset ${SIG_OFFSET} == 1) or (${SIG_POD_PHASE} offset ${SIG_OFFSET} == 1) )"

# collect_regressions — populate the globals REG_IDS (sorted-unique regression identifiers,
# one per line) and SIG_PODS_STATUS (ok|blind). Identifier forms (stable + sortable):
#   flux/<Kind>/<ns>/<name>   pod/<ns>/<pod>
# Requires $NOW (epoch) and $PROM set by the caller. Read-only; never writes anything.
collect_regressions() {
  SIG_PODS_STATUS=ok
  local flux_ids pods_json pods_ids
  flux_ids="$(kubectl get kustomizations.kustomize.toolkit.fluxcd.io,helmreleases.helm.toolkit.fluxcd.io -A -o json 2>/dev/null \
    | jq -r --argjson now "$NOW" '.items[] | . as $i
        | (.status.conditions[]? | select(.type=="Ready" and .status!="True")) as $c
        | ($c.lastTransitionTime | sub("\\.[0-9]+";"") | fromdateiso8601) as $t
        | select(($now - $t) > 600)
        | "flux/\(.kind)/\($i.metadata.namespace)/\($i.metadata.name)"' 2>/dev/null)"
  pods_json="$(curl -sf --max-time 15 "$PROM/api/v1/query" --data-urlencode "query=$SIG_POD_QUERY" 2>/dev/null)"
  if [ -z "$pods_json" ]; then
    SIG_PODS_STATUS=blind
    pods_ids=""
  else
    pods_ids="$(printf '%s' "$pods_json" | jq -r '.data.result[] | "pod/\(.metric.namespace)/\(.metric.pod)"' 2>/dev/null)"
  fi
  REG_IDS="$(printf '%s\n%s\n' "$flux_ids" "$pods_ids" | sed '/^$/d' | LC_ALL=C sort -u)"
}

# sig_of — stable short (12-hex) signature of the newline-joined identifier list ($1).
# sha256 via whichever tool exists; we slice the 64-hex out so the tool's output framing
# does not matter (identical value in the debian gate image and the node shepherd image).
sig_of() {
  printf '%s' "$1" \
    | { sha256sum 2>/dev/null || shasum -a 256 2>/dev/null || openssl dgst -sha256 2>/dev/null; } \
    | grep -oE '[0-9a-f]{64}' | head -1 | cut -c1-12
}

# state_get — compact-JSON entry for signature $1, or "" (FAIL-SAFE for the gate: any error
# => ""). Triage uses state_read() instead (it must distinguish unreadable from absent).
state_get() {
  kubectl -n "$STATE_NS" get configmap "$STATE_CM" -o json 2>/dev/null \
    | jq -c --arg k "$1" '(.data[$k] // "") | if . == "" then empty else (fromjson? // {}) end' 2>/dev/null
}
# jget — read field $2 from a compact-JSON entry $1; empty string if absent.
jget() { printf '%s' "$1" | jq -r --arg f "$2" '.[$f] // ""' 2>/dev/null; }
# ═══════════════════════════════════════════ end shared block ═══════════════════════════

# remediation_phase <class> <result> <first_seen> -> inflight | failed | fixed | none
#   inflight = an upgrade fix is actively landing (Flux ~30m poll) -> suppress the gate
#   failed   = upgrade fix attempted and did NOT converge (or stuck past grace) -> human needed
#   fixed    = triage confirmed the set cleared
#   none     = not upgrade-remediated (nonupgrade, no entry, or unknown class)
remediation_phase() {
  local class="$1" result="$2" fseen="$3" age grace
  case "$fseen" in ''|*[!0-9]*) fseen=0;; esac
  grace="$(( REMEDIATE_GRACE_MINUTES * 60 ))"
  age="$(( NOW - fseen ))"
  if [ "$class" = "upgrade" ]; then
    case "$result" in
      fixed)  printf 'fixed'; return ;;
      failed) printf 'failed'; return ;;
      pending|none|inprogress|"")
        if [ "$age" -lt "$grace" ]; then printf 'inflight'; else printf 'failed'; fi; return ;;
      *) printf 'failed'; return ;;
    esac
  fi
  printf 'none'
}

# state_set_last_paged — stamp last_paged on this signature's entry (page de-dupe memory).
# The gate's ONE write: name-scoped `update` on the coordination CM (get+update Role). Never
# creates the CM (triage owns creation) and never touches class/attempted/result. Best-effort:
# a failure just means we may re-page next cycle (fail-safe).
state_set_last_paged() {  # $1=sig $2=epoch
  local sig="$1" ts="$2" out
  out="$(kubectl -n "$STATE_NS" get configmap "$STATE_CM" -o json 2>/dev/null \
    | jq --arg k "$sig" --arg ts "$ts" '
        .data = (.data // {})
        | ((.data[$k] // "{}") | (fromjson? // {})) as $e
        | .data[$k] = (($e + {last_paged:$ts, first_seen:($e.first_seen // $ts)}) | tojson)
      ' 2>/dev/null)"
  if [ -z "$out" ]; then
    log "coordination: cannot read $STATE_CM to stamp last_paged (paged anyway; will re-evaluate next cycle)"
    return 0
  fi
  printf '%s' "$out" | kubectl -n "$STATE_NS" replace -f - >/dev/null 2>&1 \
    && log "coordination: stamped last_paged=$ts for sig=$sig" \
    || log "coordination: WARN could not stamp last_paged for sig=$sig (CM absent/RBAC/conflict) — may re-page next cycle"
}

# ---- Gate self-health: if we can reach NEITHER the API server NOR Prometheus we are
#      blind — page a warning and bail so a silently-broken gate is never "green". ----
kubectl version -o json >/dev/null 2>&1; API_OK=$?
[ "$(prom_count 'vector(1)')" = "1" ]; PROM_OK=$?
if [ "$API_OK" -ne 0 ] && [ "$PROM_OK" -ne 0 ]; then
  page warning gate-blind self "Gate cannot reach the API server OR Prometheus — health unknown."
  exit 0
fi

# ---- Coordination decision (Flux NotReady + persisted pods, remediation-aware). ONE
#      signature, per-dimension paging: Flux is the sole catcher (page unless a fix is in
#      flight); pods page ONLY for an upgrade regression remediate attempted+failed. ----
collect_regressions
if [ -n "$REG_IDS" ]; then
  SIG="$(sig_of "$REG_IDS")"
  flux_list="$(printf '%s\n' "$REG_IDS" | sed -n 's#^flux/##p' | tr '\n' ' ')"
  pod_list="$(printf '%s\n'  "$REG_IDS" | sed -n 's#^pod/##p'  | tr '\n' ' ')"
  entry="$(state_get "$SIG")"          # "" on no-entry OR unreadable CM
  cls="$(jget "$entry" class)"
  res="$(jget "$entry" result)"
  fseen="$(jget "$entry" first_seen)"
  lpaged="$(jget "$entry" last_paged)"
  phase="$(remediation_phase "$cls" "$res" "$fseen")"

  # Human-readable remediation context for the page body.
  case "$phase" in
    failed) rnote="auto-remediation was attempted and did NOT converge — human fix needed" ;;
    fixed)  rnote="auto-remediation reported FIXED but the signal persists — human check needed" ;;
    *)      rnote="not attributable to a recent upgrade (no auto-remediation ran)" ;;
  esac

  want_flux=0; want_pod=0
  # Flux = sole catcher: page unless a fix is actively in flight.
  [ -n "$flux_list" ] && [ "$phase" != "inflight" ] && want_flux=1
  # Pods = Alertmanager-covered: page ONLY when an upgrade fix was attempted and failed.
  [ -n "$pod_list" ]  && [ "$phase" = "failed" ]    && want_pod=1

  if [ "$want_flux" = "1" ] || [ "$want_pod" = "1" ]; then
    case "$lpaged" in ''|*[!0-9]*) lpaged=0;; esac
    if [ "$lpaged" -gt 0 ] && [ "$(( NOW - lpaged ))" -lt "$(( REPAGE_SUPPRESS_HOURS * 3600 ))" ]; then
      log "coordination sig=$SIG phase=$phase: page(s) due but last_paged=$lpaged within ${REPAGE_SUPPRESS_HOURS}h — DEDUPED (flux='$flux_list' pod='$pod_list')"
    else
      [ "$want_flux" = "1" ] && page critical flux "$SIG" "Flux deploy NotReady >10m (the gate is the ONLY catcher — gotk_* are not in Alertmanager): ${flux_list}[sig=$SIG] Remediation: ${rnote}."
      [ "$want_pod" = "1" ]  && page critical pods "$SIG" "Pod(s) unhealthy >10m after an UPGRADE and auto-remediation FAILED (not the general crashloop Alertmanager covers): ${pod_list}[sig=$SIG] Remediation: ${rnote}."
      state_set_last_paged "$SIG" "$NOW"
    fi
  else
    log "coordination sig=$SIG phase=$phase: nothing to page (in-flight fix, or non-upgrade pod flap handled by Alertmanager). flux='$flux_list' pod='$pod_list'"
  fi
fi

# ---- Check 1 (Flux source): the flux-system GitRepository must be Ready + reachable.
#      (NotReady kustomizations/helmreleases are handled by the coordination block above.) ----
if [ "$API_OK" -eq 0 ]; then
  # The GitRepository is named 'haynes-ops' (in ns flux-system). Ready=False is the
  # reliable "Flux can't fetch main" signal (auth/repo failure). We deliberately do
  # NOT staleness-check artifact.lastUpdateTime: it only advances on a CONTENT change,
  # so a quiet period with no commits would false-page. A wedged/down source-controller
  # surfaces via the pod sweep + NotReady kustomizations instead.
  gr="$(kubectl -n flux-system get gitrepository haynes-ops -o json 2>/dev/null)"
  gr_ready="$(echo "$gr" | jq -r '.status.conditions[]? | select(.type=="Ready") | .status' 2>/dev/null)"
  if [ -z "$gr_ready" ]; then
    page warning flux-blind haynes-ops "Cannot read the haynes-ops GitRepository status — Flux source dimension is blind."
  elif [ "$gr_ready" != "True" ]; then
    page critical flux haynes-ops "haynes-ops GitRepository Ready=$gr_ready — Flux can't fetch main (auth/repo failure)."
  fi
fi

if [ "$PROM_OK" -eq 0 ]; then
  # ---- Blind warning for the coordination pods dimension (detection lives in
  #      collect_regressions; an empty/failed query is BLIND, never a false-green). ----
  [ "$SIG_PODS_STATUS" = "blind" ] && page warning gate-blind pods "Pods persisted-unhealthy query failed (parse/timeout) — pods dimension is blind."

  # ---- Check 3: ExternalSecret Ready=False persisted past a cycle. ----
  q_eso='(externalsecret_status_condition{condition="Ready",status="False"} == 1) and (externalsecret_status_condition{condition="Ready",status="False"} offset '"$OFFSET"' == 1)'
  c_eso="$(prom_count "$q_eso")"
  if [ -z "$c_eso" ]; then
    page warning gate-blind eso "ExternalSecret persisted query failed (parse/timeout) — ESO dimension is blind."
  elif gt0 "$c_eso"; then
    page critical eso "$(prom_names "$q_eso" name)" "ExternalSecret(s) Ready=False, persisted ${OFFSET}: $(prom_names "$q_eso" name)"
  fi

  # ---- Check 5: Ceph HEALTH_ERR (==2) pages; WARN (==1) is a benign note. ----
  ceph="$(prom_val 'ceph_health_status')"
  if [ "$ceph" = "2" ]; then
    page critical ceph rook-ceph "Ceph HEALTH_ERR (ceph_health_status==2). Ceph majors are forward-only — a git revert does NOT recover the data plane."
  elif [ "$ceph" = "1" ]; then
    log "note: ceph HEALTH_WARN (==1) — benign unless a NEW OSD/PG/mon fault (see runbook allowlist)."
  fi
else
  # Prometheus alone unreachable => the pods/ESO/Ceph dimensions are dark. Blind is NOT
  # green — warn. (Bit us 2026-07-04: a CNP DNS gap made Prometheus unresolvable since
  # deploy and the gate quietly ran as a reduced gate.)
  page warning gate-blind prometheus "Prometheus unreachable — pods/ESO/Ceph dimensions are blind this cycle."
fi

# ---- Check 6: Home Assistant availability (only if HASS_TOKEN is set). ----
if [ -n "${HASS_TOKEN:-}" ]; then
  hastate() { curl -sf --max-time 10 -H "Authorization: Bearer $HASS_TOKEN" "$HA/api/states/$1" 2>/dev/null | jq -r '.state // "ERR"' 2>/dev/null; }
  # Blind is NOT green: if the HA API itself is unreachable, warn instead of silently
  # skipping (hastate returns ""/ERR on curl failure, which pages nothing).
  if ! curl -sf --max-time 10 -H "Authorization: Bearer $HASS_TOKEN" "$HA/api/" >/dev/null 2>&1; then
    page warning gate-blind home-assistant "HA API unreachable — Zigbee/lock dimensions are blind this cycle."
  else
  z2m="$(hastate binary_sensor.zigbee2mqtt_bridge_connection_state)"
  [ "$z2m" = "off" ] && page critical ha zigbee2mqtt "Zigbee2MQTT bridge connection == off (mesh down / Z2M-HA restart race)."
  for lk in front_door_lock side_door_lock bulkhead_lock mudroom_door_lock; do
    st="$(hastate "lock.$lk")"
    case "$st" in unavailable|unknown) page critical ha "lock.$lk" "Door lock lock.$lk == $st." ;; esac
  done
  # Spa (Gecko in.touch3) is chronically flaky over RF — benign, deliberately NOT paged.
  fi
fi

# ---- Dead-man's-switch: a successful cycle pings the heartbeat (if configured). ----
[ -n "${GATE_HEARTBEAT_URL:-}" ] && curl -fsS --max-time 10 "$GATE_HEARTBEAT_URL" >/dev/null 2>&1
log "gate cycle complete: regressions=$REGRESSIONS"
exit 0
