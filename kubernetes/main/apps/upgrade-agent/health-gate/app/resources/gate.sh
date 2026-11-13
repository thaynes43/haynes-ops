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

# >>> dev-env-api reach (dev-env DESIGN-064 6.5, OpenProject #739) >>>
# One best-effort, read-only GET /v1/sessions against the dev-env v2 operator API per
# run, as the reach proof: one line on stderr with the HTTP status code (000 when no
# answer came). It never changes this run's exit code or stdout, is bounded at 10
# seconds, and runs once per run (a nested script skips it). It is skipped without a
# word when the client or the token is not mounted; when only the CA is missing, the
# client refuses before sending and the line logs HTTP 000. The same block is in
# respond.sh, run-shepherd.sh, triage.sh and gate.sh;
# scripts/upgrade-agent/dev-env-api-selftest.sh extracts and checks each copy.
dev_env_api_reach() {
  local client="${DEV_ENV_API_CLIENT:-/opt/dev-env-api/dev-env-api.sh}"
  local token="${DEV_ENV_API_TOKEN_FILE:-/var/run/secrets/dev-env/token}"
  local code="" bound=()
  [ "${DEV_ENV_API_REACHED:-}" = 1 ] && return 0
  { [ -x "$client" ] && [ -r "$token" ]; } || return 0
  export DEV_ENV_API_REACHED=1
  if command -v timeout >/dev/null 2>&1; then bound=(timeout -k 1 9); fi
  code="$(DEV_ENV_API_CONNECT_TIMEOUT=3 DEV_ENV_API_MAX_TIME=8 ${bound[@]+"${bound[@]}"} "$client" list 2>&1 >/dev/null \
    | sed -n 's|^dev-env-api: GET /v1/sessions HTTP \([0-9][0-9][0-9]\)$|\1|p' | head -n 1)" || true
  printf '%s dev-env-api reach: GET /v1/sessions HTTP %s\n' "$(date -u +%FT%TZ)" "${code:-000}" >&2
  return 0
}
dev_env_api_reach || true
# <<< dev-env-api reach <<<

# Prometheus instant query -> series count / first value / joined label values.
prom_count() { curl -sf --max-time 15 "$PROM/api/v1/query" --data-urlencode "query=$1" 2>/dev/null | jq -r '.data.result | length' 2>/dev/null; }
prom_val()   { curl -sf --max-time 15 "$PROM/api/v1/query" --data-urlencode "query=$1" 2>/dev/null | jq -r '.data.result[0].value[1] // "NaN"' 2>/dev/null; }
prom_names() { curl -sf --max-time 15 "$PROM/api/v1/query" --data-urlencode "query=$1" 2>/dev/null | jq -r "[.data.result[].metric.$2] | unique | join(\",\")" 2>/dev/null; }
gt0() { [ -n "$1" ] && [ "$1" -gt 0 ] 2>/dev/null; }

# ── THE MORNING SUMMARY (story 554) ── `gate.sh morning-summary`, run by this release's 06:30
#    America/New_York CronJob. The night lane composes the summary every tick
#    (shepherd/app/resources/night-summary.mjs) and copies it into the morning_* keys of
#    ConfigMap upgrade-orphan-report, the report this ServiceAccount already reads and stamps
#    (name-scoped get and update; it cannot read upgrade-lane-state). The sender reads it now,
#    at the moment it acts: a copy that is missing or for an older night becomes a short "the
#    lane wrote no summary" message composed here, and a night whose last completed tick is
#    not the last one of the night (morning_last_tick before morning_last_tick_expected,
#    05:45) says the lane did not finish; the tick count is information only (a night the
#    clocks change has 16 or more than 20). Pushover priority -1 (page.sh severity low): no
#    sound or vibration, still shown as a notification. The text is data: it reaches curl as
#    one quoted --form-string argument, never a command line. At most MORNING_MAX_BYTES
#    (1000), whole lines cut from the end with "and N more".
#    DELIVERY: up to MORNING_ATTEMPTS (3) sends, MORNING_RETRY_SECONDS (20) apart. All failing
#    is one ERROR line, the night is left unstamped and the run still exits 0: a Failed pod of
#    this CronJob would enter the coordination set and page at priority 1 for a missed quiet
#    note. Nothing retries after the run; the next morning's message starts by naming the
#    night whose summary was not delivered (morning_sent_night older than the night before).
#    Story: https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/554
MORNING_TZ="${MORNING_TZ:-America/New_York}"
MORNING_MAX_BYTES="${MORNING_MAX_BYTES:-1000}"
MORNING_CM="${MORNING_CM:-upgrade-orphan-report}"
MORNING_ATTEMPTS="${MORNING_ATTEMPTS:-3}"
MORNING_RETRY_SECONDS="${MORNING_RETRY_SECONDS:-20}"
# cut_bytes <text> <max bytes> — at most <max> bytes, never inside a UTF-8 character
cut_bytes() {
  printf '%s' "$1" | jq -Rrsj --argjson max "$2" '
    reduce explode[] as $c ({out: [], n: 0, full: false};
      if .full then . else
        ([$c] | implode | utf8bytelength) as $b
        | if .n + $b > $max then .full = true else .out += [$c] | .n += $b end
      end) | .out | implode' 2>/dev/null
}
# fit_lines <text> <max bytes> — whole lines, the last one "and N more" when any were cut; when
# even the first line and "and N more" do not fit, the first line is cut by characters.
fit_lines() {
  local LC_ALL=C text="$1" max="$2" total kept out tail
  local -a lines
  if [ "${#text}" -le "$max" ]; then printf '%s' "$text"; return 0; fi
  mapfile -t lines <<<"$text"
  total="${#lines[@]}"
  for (( kept = total - 1; kept >= 1; kept-- )); do
    out="$(printf '%s\n' "${lines[@]:0:kept}")"$'\n'"and $(( total - kept )) more"
    if [ "${#out}" -le "$max" ]; then printf '%s' "$out"; return 0; fi
  done
  tail=$'\n'"and $(( total - 1 )) more"
  printf '%s%s' "$(cut_bytes "${lines[0]}" "$(( max - ${#tail} ))")" "$tail"
}
send_morning_summary() {
  local ns="${UPGRADE_AGENT_NAMESPACE:-upgrade-agent}" night before rep text mnight sent ticks last expected msg rc=1 try missed
  night="$(TZ="$MORNING_TZ" date +%F)"   # the night that just ended: its ticks (01:00-05:45) carry this date
  before="$(date -u -d "$night -1 day" +%F 2>/dev/null)"
  rep="$(kubectl -n "$ns" get configmap "$MORNING_CM" -o json 2>/dev/null)" || rep=""
  text="$(printf '%s' "$rep" | jq -r '.data.morning_text // ""' 2>/dev/null)"
  mnight="$(printf '%s' "$rep" | jq -r '.data.morning_night // ""' 2>/dev/null)"
  sent="$(printf '%s' "$rep" | jq -r '.data.morning_sent_night // ""' 2>/dev/null)"
  ticks="$(printf '%s' "$rep" | jq -r '.data.morning_ticks // ""' 2>/dev/null | tr -cd '0-9' | cut -c1-3)"
  last="$(printf '%s' "$rep" | jq -r '.data.morning_last_tick // ""' 2>/dev/null)"
  expected="$(printf '%s' "$rep" | jq -r '.data.morning_last_tick_expected // ""' 2>/dev/null)"
  case "$mnight" in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;; *) mnight="" ;; esac
  case "$sent" in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;; *) sent="" ;; esac
  case "$last" in [0-2][0-9]:[0-5][0-9]) ;; *) last="" ;; esac
  case "$expected" in [0-2][0-9]:[0-5][0-9]) ;; *) expected="" ;; esac
  if [ "$sent" = "$night" ]; then
    log "morning summary: already sent for the night of $night — not sending again."
    return 0
  fi
  if [ -z "$rep" ]; then
    msg="The night lane's summary for the night of ${night} could not be read (ConfigMap ${MORNING_CM} missing or unreadable). Check the upgrade-night CronJob in ${ns}."
  elif [ -z "$text" ] || [ "$mnight" != "$night" ]; then
    msg="The night lane wrote no summary for the night of ${night}: it did not run, or no tick finished. Its last summary is for ${mnight:-no night}. Check the upgrade-night CronJob in ${ns}."
  else
    msg="$text"
    # finished = the last completed tick is the night's last one (05:45), whatever the count
    if [ -z "$last" ] || [ -z "$expected" ] || [[ "$last" < "$expected" ]]; then
      msg="The night lane did not finish the night: its last tick ran at ${last:-an unknown time}, not ${expected:-the last of the night} (${ticks:-?} ticks)."$'\n'"$msg"
    fi
  fi
  # a summary a past run could not deliver: said first (nothing retries after a run)
  if [ -n "$sent" ] && [ -n "$before" ] && [[ "$sent" < "$before" ]]; then
    missed="$(date -u -d "$sent +1 day" +%F 2>/dev/null)"
    if [ "$missed" = "$before" ]; then msg="The summary for the night of ${missed} was not delivered."$'\n'"$msg"
    else msg="The summaries for the nights from ${missed:-?} to ${before} were not delivered."$'\n'"$msg"; fi
  fi
  msg="$(fit_lines "$msg" "$MORNING_MAX_BYTES")"
  for (( try = 1; try <= MORNING_ATTEMPTS; try++ )); do
    PAGE_TITLE="Night upgrades ${night}" "$GATE_DIR/page.sh" low summary morning "$msg"; rc=$?
    [ "$rc" -eq 0 ] && break
    [ "$try" -lt "$MORNING_ATTEMPTS" ] && sleep "$MORNING_RETRY_SECONDS"
  done
  if [ "$rc" -ne 0 ]; then
    log "ERROR morning summary: NOT delivered for the night of $night after $MORNING_ATTEMPTS attempts; the night stays unstamped and the next morning's message says so (this Job still succeeds: a Failed pod would page at priority 1)."
    return 0
  fi
  # Stamp the night it was sent for, at the version read (a later write wins; this only stops a
  # second run the same morning from sending it twice, and dates the last delivery).
  if [ -n "$rep" ]; then
    printf '%s' "$rep" | jq --arg n "$night" '.data.morning_sent_night = $n' 2>/dev/null \
      | kubectl -n "$ns" replace -f - >/dev/null 2>&1 \
      && log "morning summary: sent for the night of $night (attempt $try; stamped morning_sent_night)" \
      || log "morning summary: sent for the night of $night (attempt $try); WARN could not stamp morning_sent_night (conflict/RBAC)"
  else
    log "morning summary: sent the no-summary message for $night (nothing to stamp)"
  fi
}
if [ "${1:-}" = "morning-summary" ]; then
  send_morning_summary
  exit 0
fi

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
# CLOCK_SKEW_SECONDS (story 554, review M2): how far ahead of this pod's clock a time written
# by another pod may be and still be believed. A first_seen further in the future than that is
# not believed: its grace window counts as spent, so it pages instead of staying silent.
CLOCK_SKEW_SECONDS="${CLOCK_SKEW_SECONDS:-300}"
remediation_phase() {
  local class="$1" result="$2" fseen="$3" age grace
  case "$fseen" in ''|0*|*[!0-9]*|?????????????*) fseen=0;; esac
  grace="$(( REMEDIATE_GRACE_MINUTES * 60 ))"
  age="$(( NOW - fseen ))"
  [ "$fseen" -le "$(( NOW + CLOCK_SKEW_SECONDS ))" ] || age="$grace"
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

# lane_phase <entry> — the night lane's claim (story 554), read from its own fields; prints
# "<phase>|<page note>" or NOTHING when the entry is not the lane's or is not understood. The
# gate decides on its OWN clock: a working claim keeps it silent only while all of these hold
#   - pr, claimed_at and until are plain numbers (no sign, no leading zero, at most 12 digits);
#   - claimed_at is not ahead of NOW by more than CLOCK_SKEW_SECONDS (300: clock difference
#     between pods; a claim from the future is not believed);
#   - until >= claimed_at, and until - claimed_at <= LANE_CLAIM_MAX_SECONDS (21600);
#   - NOW < until.
# Anything else is a claim the gate does not understand: it is read as today, by
# remediation_phase on its class/result/first_seen, which applies the same tolerance. The bound
# `until` is the lane's own, on its own action (night.claim.max_work_seconds).
#   working, understood, NOW < until -> inflight (no page)
#   working, understood, NOW >= until -> failed (page: the lane did not finish)
#   revert-failed                     -> failed (page: the lane's revert did not verify)
#   reverted                          -> fixed (Flux still failing pages as today's "fixed")
LANE_CLAIM_MAX_SECONDS="${LANE_CLAIM_MAX_SECONDS:-21600}"
lane_phase() {
  local e="$1" st pr claimed until at n
  [ "$(jget "$e" holder)" = "lane" ] || return 0
  st="$(jget "$e" lane_state)"; pr="$(jget "$e" pr)"; claimed="$(jget "$e" claimed_at)"; until="$(jget "$e" until)"
  for n in "$pr" "$claimed" "$until"; do
    case "$n" in ''|0*|*[!0-9]*|?????????????*) return 0 ;; esac
  done
  [ "$claimed" -le "$(( NOW + CLOCK_SKEW_SECONDS ))" ] || return 0
  [ "$until" -ge "$claimed" ] || return 0
  [ "$(( until - claimed ))" -le "$LANE_CLAIM_MAX_SECONDS" ] || return 0
  at="$(date -u -d "@$until" +%FT%TZ 2>/dev/null || printf '%s' "$until")"
  case "$st" in
    working)
      if [ "$NOW" -lt "$until" ]; then printf 'inflight|the night lane holds it for #%s and is working on it until %s' "$pr" "$at"
      else printf 'failed|the night lane claimed it for #%s and did not finish by its own bound (%s) — human check needed' "$pr" "$at"; fi ;;
    revert-failed) printf 'failed|the night lane reverted #%s and the revert did NOT verify — human fix needed' "$pr" ;;
    reverted) printf 'fixed|the night lane reverted #%s and verified recovery, but the signal persists — human check needed' "$pr" ;;
    *) return 0 ;;
  esac
}

# lane_overlap_note <REG_IDS> — story 554 (review L10). When the set has no entry of its own but
# a working night-lane claim under ANOTHER signature names some of its ids (the set changed
# since the lane claimed it), triage stands down for the whole set and the gate pages it now.
# This is the true note for that page: which PR the lane is working on, the ids it holds, and
# that the rest of the set has no owner. Prints nothing otherwise (or when unreadable).
lane_overlap_note() {
  kubectl -n "$STATE_NS" get configmap "$STATE_CM" -o json 2>/dev/null | jq -r --arg ids "$1" --argjson now "$NOW" '
    ($ids | split("\n") | map(select(. != ""))) as $set
    | [ (.data // {}) | to_entries[] | .key as $sk | (.value | fromjson? // {}) as $o
        | select(($o | type) == "object" and ($o.holder // "") == "lane" and ($o.lane_state // "") == "working"
                 and ((($o.until // "0") | tostring | tonumber? // 0) > $now))
        | [ ($o.ids // [])[] | select(. as $i | $set | index($i) != null) ] as $hit
        | select(($hit | length) > 0)
        | "the night lane is reverting #\($o.pr // "?") (claim \($sk), until \($o.until | tonumber? // 0 | todate)) and holds \($hit | join(", ")); the rest of this set (\([ $set[] | select(. as $i | $hit | index($i) == null) ] | join(", ") | if . == "" then "nothing else" else . end)) has no owner: triage stands down while the lane works" ]
    | first // empty' 2>/dev/null
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
# Diagnostic snapshot (log-only): every Flux resource currently NotReady with the age of
# EACH failure condition. Exists because the cilium 1.19.6 wedge (2026-07-17) was live
# across two gate cycles yet flux='' both times — either every anchor condition's
# lastTransitionTime churns while helm-controller actively retries, or something else;
# the conditions were rewritten before anyone could look. Next time this line IS the look.
if [ "$API_OK" -eq 0 ] 2>/dev/null || kubectl version -o json >/dev/null 2>&1; then
  # TZ=UTC for jq: jq 1.6 (Debian bookworm) parses fromdateiso8601 in local time, and under the pod's TZ=America/New_York it lands an hour late during DST.
  watch_dbg="$(kubectl get kustomizations.kustomize.toolkit.fluxcd.io,helmreleases.helm.toolkit.fluxcd.io -A -o json 2>/dev/null \
    | TZ=UTC jq -r --argjson now "$NOW" '.items[] | . as $i
        | select(.status.conditions[]? | select(.type=="Ready" and .status!="True"))
        | "\(.kind)/\($i.metadata.namespace)/\($i.metadata.name)[" + ([.status.conditions[]?
            | "\(.type)=\(.status)@\(($now - ((.lastTransitionTime|sub("\\.[0-9]+";"")|fromdateiso8601?) // $now)))s"] | join(",")) + "]"' 2>/dev/null \
    | tr '\n' ' ')"
  [ -n "$watch_dbg" ] && log "flux NotReady snapshot (condition ages, pre-persistence): $watch_dbg"
fi
if [ -n "$REG_IDS" ]; then
  SIG="$(sig_of "$(sig_key_of "$REG_IDS")")"
  flux_list="$(printf '%s\n' "$REG_IDS" | sed -n 's#^flux/##p' | tr '\n' ' ')"
  pod_list="$(printf '%s\n'  "$REG_IDS" | sed -n 's#^pod/##p'  | tr '\n' ' ')"
  entry="$(state_get "$SIG")"          # "" on no-entry OR unreadable CM
  cls="$(jget "$entry" class)"
  res="$(jget "$entry" result)"
  fseen="$(jget "$entry" first_seen)"
  lpaged="$(jget "$entry" last_paged)"
  snote="$(jget "$entry" note)"        # shepherd's final summary line (triage records it)
  lphase="$(lane_phase "$entry")"     # story 554: the night lane's claim, when it holds this one
  if [ -n "$lphase" ]; then phase="${lphase%%|*}"; else phase="$(remediation_phase "$cls" "$res" "$fseen")"; fi

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
  [ -n "$lphase" ] && rnote="${lphase#*|}"
  # review L10: a set that moved since the lane claimed part of it pages now, with the true note
  if [ -z "$lphase" ] && [ "$phase" = "none" ]; then
    onote="$(lane_overlap_note "$REG_IDS")"
    [ -n "$onote" ] && rnote="$onote"
  fi

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
      # ── REPEAT-PAGE ESCALATION (2026-08-21, backlog 12→13) ── lpaged>0 means this
      # exact signature already paged >= REPAGE_SUPPRESS_HOURS ago and NOTHING fixed
      # it (auto-remediation failed or never applied) — file an esc-gate-* entry so
      # the dev-env-ops executor spawns a joinable session (esc-* lane model) and pages Tom WITH
      # the session name. Dedup lives in escalate.sh (an open entry for this sig is
      # not refiled). BEST-EFFORT: a write failure only logs — the page above is the
      # primary signal and already went out.
      if [ "$lpaged" -gt 0 ]; then
        ESCALATE_SIG="$SIG" bash /opt/coordination/escalate.sh gate "gate sig=$SIG" \
          "repeat page (unresolved >${REPAGE_SUPPRESS_HOURS}h): flux='${flux_list}' pods='${pod_list}' phase=${phase} — ${rnote}" \
          || log "coordination: escalate.sh could not file esc-gate for sig=$SIG (best-effort; page already sent)"
      fi
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
      # story 554: the count is of PRs with no lane (not on the night lane's last record);
      # every open PR classified reads 0 and does not page. count_basis says how it was made.
      page warning renovate orphans "${o_count} Renovate PR(s) open >7d with no lane ($(echo "$orph" | jq -r '.data.count_basis // "basis not recorded"' | cut -c1-160)): ${o_sum:0:600}"
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
