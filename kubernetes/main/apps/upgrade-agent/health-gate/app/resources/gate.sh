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
#   * Flux Kustomization/HelmRelease NotReady  -> PAGE it with full context whenever persisted,
#       EXCEPT while remediate is actively landing a fix (suppress within the grace window).
#       Since 2026-07-17 Alertmanager ALSO covers this (FluxReconciliationFailure via
#       kube-state-metrics gotk_resource_info) — the gate stays as the Alertmanager-
#       independent second catcher, not the only one.
#   * Persisted-unhealthy pods                 -> Alertmanager's KubePodCrashLooping already
#       covers general pod health. PAGE ONLY when the regression is upgrade-attributable AND
#       auto-remediation was attempted and FAILED. A non-upgrade pod flap does NOT page here.
#   * Firing critical alerts                   -> NOT checked at all (removed 2026-07): pure
#       Alertmanager duplication, and the source of context-poor "upgrade-gate: OOMKilled"
#       pages for unrelated self-healed apps.
#   * GitRepository / ExternalSecret / Ceph        -> deploy/infra health the gate reads
#       directly; UNCHANGED, contextual, always-on.
# HA/Zigbee/lock/device availability is NOT the gate's job — it is owned by the hass-sandbox
# AppDaemon health-check suite + Alertmanager bridge. The gate deliberately does NOT poll HA
# (removed 2026-07: an HA integration re-auth flipped every lock `unavailable` and the gate
# paged ~5 unrelated criticals to the operator's phone — device health is not a deploy signal).
# Every page names the specific resource(s) and states the remediation outcome. Coordination
# pages de-dupe to once per REPAGE_SUPPRESS_HOURS per signature. If state is unreadable, the
# Flux dimension FAILS SAFE (pages); the pod dimension fails silent (Alertmanager is the net).
set -uo pipefail

GATE_DIR="$(cd "$(dirname "$0")" && pwd)"
PROM="${PROMETHEUS_URL:-http://kube-prometheus-stack-prometheus.observability.svc.cluster.local:9090}"
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

# ── SHARED coordination logic — single-sourced from the upgrade-coordination-lib
#    ConfigMap (see coordination-lib.sh; replaces the old byte-identical duplicated
#    block). If the lib is missing the gate must NOT run half-configured — page blind. ──
if ! . "${COORD_LIB:-/opt/coordination/coordination-lib.sh}" 2>/dev/null; then
  "$GATE_DIR/page.sh" warning gate-blind coordination-lib \
    "coordination-lib.sh missing/unreadable — gate cannot compute regression signatures; check the upgrade-coordination-lib ConfigMap mount."
  exit 0
fi

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
# Blind is NOT green — symmetric with the Prometheus-alone warning below: API-server
# alone unreachable used to silently skip the Flux/GitRepository/orphan dimensions
# (the gate's SOLE-catcher checks) while the heartbeat kept pinging (2026-07-06).
[ "$API_OK" -ne 0 ] && page warning gate-blind apiserver "API server unreachable — Flux/GitRepository/orphan dimensions are blind this cycle (Prometheus checks continue)."

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
  snote="$(jget "$entry" note)"        # shepherd's final summary line (triage records it)
  phase="$(remediation_phase "$cls" "$res" "$fseen")"

  # Human-readable remediation context for the page body.
  case "$phase" in
    failed) rnote="auto-remediation was attempted and did NOT converge — human fix needed" ;;
    fixed)  rnote="auto-remediation reported FIXED but the signal persists — human check needed" ;;
    *)      rnote="not attributable to a recent upgrade (no auto-remediation ran)" ;;
  esac
  # Durable-verdict passthrough (2026-07-06): if the shepherd left a summary (its
  # BREAK-GLASS/HOLD line or final report), put the WHY in the page instead of making
  # the human dig Job logs. Trimmed — Pushover bodies cap at 1024 chars.
  [ -n "$snote" ] && rnote="${rnote} | shepherd: ${snote:0:220}"

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
      [ "$want_flux" = "1" ] && page critical flux "$SIG" "Flux deploy NotReady >10m (first-failure anchored; Alertmanager's FluxReconciliationFailure should also have fired): ${flux_list}[sig=$SIG] Remediation: ${rnote}."
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

  # ---- Check 1b (2026-07-06): orphan-PR digest + ramp-mismatch tripwire. The shepherd's
  #      scheduled run writes the deterministic ($0) report to the upgrade-orphan-report
  #      ConfigMap; the gate — the Pushover owner — pages it: orphans at most weekly,
  #      a ramp mismatch (prompt/glob-map drift, which silently blinds the pre-filter)
  #      at most daily. The gate stamps its own page timestamps back into the CM
  #      (name-scoped get+update, same pattern as the coordination CM). ----
  orph="$(kubectl -n "$STATE_NS" get configmap upgrade-orphan-report -o json 2>/dev/null)"
  if [ -n "$orph" ]; then
    o_count="$(echo "$orph" | jq -r '.data.count // "0"')"
    o_sum="$(echo "$orph"   | jq -r '.data.summary // ""')"
    o_mm="$(echo "$orph"    | jq -r '.data.ramp_mismatch // ""')"
    o_lp="$(echo "$orph"    | jq -r '.data.last_paged // "0"')"
    o_lmp="$(echo "$orph"   | jq -r '.data.last_mismatch_paged // "0"')"
    case "$o_lp"  in ''|*[!0-9]*) o_lp=0;;  esac
    case "$o_lmp" in ''|*[!0-9]*) o_lmp=0;; esac
    paged_mm=""; paged_orph=""
    if [ -n "$o_mm" ] && [ "$(( NOW - o_lmp ))" -ge 86400 ]; then
      page critical shepherd ramp-mismatch "Shepherd RAMP/prompt mismatch — the scheduled run REFUSED to vet (fail-closed):${o_mm}. Fix UPGRADE_AGENT_RAMP / the prompt in shepherd/app/helmrelease.yaml."
      paged_mm=1
    fi
    if [ "$o_count" -gt 0 ] 2>/dev/null && [ "$(( NOW - o_lp ))" -ge 604800 ]; then
      page warning renovate orphans "${o_count} Renovate PR(s) open >7d with no automation owner: ${o_sum:0:600}"
      paged_orph=1
    fi
    if [ -n "$paged_mm$paged_orph" ]; then
      # Stamp ONLY the timestamp(s) whose page actually fired this cycle.
      echo "$orph" | jq --arg now "$NOW" --arg pm "$paged_mm" --arg po "$paged_orph" '
          .data.last_paged          = (if $po != "" then $now else (.data.last_paged // "0") end)
          | .data.last_mismatch_paged = (if $pm != "" then $now else (.data.last_mismatch_paged // "0") end)
        ' 2>/dev/null | kubectl -n "$STATE_NS" replace -f - >/dev/null 2>&1 \
        || log "orphan-report: WARN could not stamp page timestamps (may re-page next window)"
    fi
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

# ---- Dead-man's-switch: a successful cycle pings the heartbeat (if configured). ----
[ -n "${GATE_HEARTBEAT_URL:-}" ] && curl -fsS --max-time 10 "$GATE_HEARTBEAT_URL" >/dev/null 2>&1
log "gate cycle complete: regressions=$REGRESSIONS"
exit 0
