#!/usr/bin/env bash
# alert-responder — Track-B1 of the 2026-07-06 agent-ops plan: ENRICH, DON'T ACT.
#
# Every RESPONDER_INTERVAL the CronJob polls Alertmanager for active critical alerts.
# For a NEW alert incident (fingerprint+startsAt never handled) it summons a READ-ONLY
# Claude Code diagnosis — kubectl get/describe, flux get, PromQL/LogQL via the two
# deterministic wrappers, the repo's committed runbooks — and pages the result to
# Pushover as a FOLLOW-UP to the page Alertmanager already sent. The human stays the
# actuator; the 2am page just arrives pre-investigated.
#
# COST DISCIPLINE (the shepherd/triage pattern):
#   - Healthy path = $0: one curl to Alertmanager, exit.
#   - AT MOST ONCE per alert INCIDENT (fingerprint+startsAt; a resolve+refire is a new
#     incident), claim-before-summon (crash-proof), FAIL CLOSED if the claim can't be
#     durably recorded.
#   - At most RESPONDER_MAX_PER_RUN (1) diagnosis per run; its own monthly spend CM
#     (separate envelope from the shepherd — an alert storm can't eat the upgrade
#     budget) + per-run --max-budget-usd.
# CONTAINMENT: read-only cluster SA (the shared upgrade-health-gate ClusterRole), NO
# git/gh write credential AT ALL (the repo clone is anonymous read-only), egress CNP =
# DNS/apiserver/observability/Anthropic/Pushover/GitHub only, dontAsk + allowlist.
set -uo pipefail

AM="${ALERTMANAGER_URL:-http://kube-prometheus-stack-alertmanager.observability.svc.cluster.local:9093}"
SEVERITY="${RESPONDER_SEVERITY:-critical}"
ALLOWLIST="${RESPONDER_ALERT_ALLOWLIST:-.*}"
# Skip classes the responder can't ADD VALUE to (keep in sync with the HR env):
# Watchdog-family + Protect.* (appdaemon watchdog owns it) + host-HARDWARE alerts
# (Smart.*/Hardware.*/NodeRAIDDiskFailure — self-evident, unactionable-by-cluster, and
# they RECUR so at-most-once doesn't help; were burning LLM $ on NAS drive wear-out).
DENYLIST="${RESPONDER_ALERT_DENYLIST:-^(Watchdog|InfoInhibitor|AlertmanagerReceiversNotConfigured|Protect.*|Smart.*|Hardware.*|NodeRAIDDiskFailure)$}"
MAX_PER_RUN="${RESPONDER_MAX_PER_RUN:-1}"
MAX_AGE_HOURS="${RESPONDER_MAX_AGE_HOURS:-24}"
STATE_TTL_HOURS="${RESPONDER_STATE_TTL_HOURS:-168}"
MODEL="${RESPONDER_MODEL:-sonnet}"
MAX_TURNS="${RESPONDER_MAX_TURNS:-25}"
MAX_BUDGET="${RESPONDER_MAX_BUDGET_USD:-2.00}"
MONTHLY_CAP="${RESPONDER_MONTHLY_CAP_USD:-15}"
RUN_TIMEOUT="${RESPONDER_TIMEOUT:-12m}"
DRY="${RESPONDER_DRY:-0}"
NS="${RESPONDER_NAMESPACE:-upgrade-agent}"
STATE_CM="alert-responder-state"
SPEND_CM="alert-responder-spend"
REPO_URL="${RESPONDER_REPO_URL:-https://github.com/thaynes43/haynes-ops.git}"
WORKDIR="${HOME}/repo"
NOW="$(date -u +%s)"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }

page() {  # $1=title-suffix $2=message ; priority 0 (the ORIGINAL critical already
          # paged at prio 1 via Alertmanager — this is the follow-up diagnosis).
  if [ "$DRY" = "1" ]; then log "DRY: would page '[responder] $1' :: $2"; return 0; fi
  : "${PUSHOVER_TOKEN:?}" ; : "${PUSHOVER_USER_KEY:?}"
  curl -sf --max-time 10 https://api.pushover.net/1/messages.json \
    --form-string "token=${PUSHOVER_TOKEN}" \
    --form-string "user=${PUSHOVER_USER_KEY}" \
    --form-string "title=[responder] $1" \
    --form-string "message=$2" \
    --form-string "priority=0" >/dev/null \
    && log "paged: [responder] $1" \
    || log "PAGE FAILED: [responder] $1"
}

# ── spend guard (same shape as the shepherd's, SEPARATE envelope) ──
SPEND_MONTH="$(date -u +%Y-%m)"; SPEND_PRIOR="0"
spend_guard() {
  local cm m
  cm="$(kubectl -n "$NS" get configmap "$SPEND_CM" -o json 2>/dev/null)" || {
    log "spend-guard: cannot read $SPEND_CM (proceeding; account balance is the backstop)"; return 0; }
  m="$(printf '%s' "$cm" | jq -r '.data.month // ""')"
  SPEND_PRIOR="$(printf '%s' "$cm" | jq -r '.data.spent_usd // "0"')"
  [ "$m" = "$SPEND_MONTH" ] || SPEND_PRIOR="0"
  if awk -v s="$SPEND_PRIOR" -v b="$MAX_BUDGET" -v c="$MONTHLY_CAP" 'BEGIN{exit !(s + b > c)}'; then
    log "spend-guard: MTD \$$SPEND_PRIOR + run cap \$$MAX_BUDGET would exceed \$$MONTHLY_CAP — SKIPPING (responder is always unattended)."
    return 1
  fi
  return 0
}
record_spend() {
  local cost new
  cost="$(jq -r '.total_cost_usd // .cost_usd // 0' "$1" 2>/dev/null)"; [ -n "$cost" ] && [ "$cost" != "null" ] || cost=0
  new="$(awk -v a="${SPEND_PRIOR:-0}" -v b="$cost" 'BEGIN{printf "%.4f", a + b}')"
  kubectl -n "$NS" create configmap "$SPEND_CM" \
    --from-literal=month="$SPEND_MONTH" --from-literal=spent_usd="$new" \
    --dry-run=client -o yaml | kubectl -n "$NS" apply -f - >/dev/null 2>&1 \
    && log "spend: +\$$cost => MTD \$$new / \$$MONTHLY_CAP" \
    || log "spend: WARN could not record \$$cost"
}

# ── incident state (at-most-once per fingerprint+startsAt, TTL-pruned) ──
sig_of() { printf '%s' "$1" | sha256sum | grep -oE '[0-9a-f]{64}' | head -1 | cut -c1-12; }
state_has() {  # $1=key ; rc0 = already handled. Any read ERROR => treat as handled
               # (FAIL CLOSED — protect spend; Alertmanager already paged the human).
  local out rc
  out="$(kubectl -n "$NS" get configmap "$STATE_CM" -o json 2>&1)"; rc=$?
  if [ $rc -ne 0 ]; then
    case "$out" in
      *NotFound*|*"not found"*) return 1 ;;  # verifiably no prior state
      *) log "state: UNREADABLE ($out) — failing CLOSED (skip)"; return 0 ;;
    esac
  fi
  printf '%s' "$out" | jq -e --arg k "$1" '.data[$k] // empty' >/dev/null 2>&1
}
state_claim() {  # $1=key $2=alertname ; claim BEFORE the summon; rc!=0 => do NOT summon.
  local ttl=$(( STATE_TTL_HOURS * 3600 ))
  if kubectl -n "$NS" get configmap "$STATE_CM" >/dev/null 2>&1; then
    kubectl -n "$NS" get configmap "$STATE_CM" -o json 2>/dev/null \
      | jq --arg k "$1" --arg a "$2" --argjson now "$NOW" --argjson ttl "$ttl" '
          .data = (.data // {})
          | .data[$k] = ({alertname:$a, attempted:"1", first_seen:($now|tostring)} | tojson)
          | .data |= with_entries(
                select(.key == $k
                       or ((((.value | (fromjson? // {}) | (.first_seen // "0") | tonumber?) // 0)) > ($now - $ttl))))
        ' 2>/dev/null | kubectl -n "$NS" replace -f - >/dev/null 2>&1
  else
    kubectl -n "$NS" create configmap "$STATE_CM" \
      --from-literal="$1"="{\"alertname\":\"$2\",\"attempted\":\"1\",\"first_seen\":\"$NOW\"}" >/dev/null 2>&1
  fi
}

# ── 1. Poll Alertmanager (the $0 path). ──
alerts="$(curl -sf --max-time 15 "$AM/api/v2/alerts?active=true&silenced=false&inhibited=false" 2>/dev/null)"
if [ -z "$alerts" ]; then
  log "Alertmanager unreachable/empty response — nothing to do (Alertmanager pages independently; the gate covers blind spots)."
  exit 0
fi
candidates="$(printf '%s' "$alerts" | jq -c --arg sev "$SEVERITY" --arg allow "$ALLOWLIST" --arg deny "$DENYLIST" --argjson now "$NOW" --argjson maxage "$(( MAX_AGE_HOURS * 3600 ))" '
  [ .[]
    | select(.labels.severity == $sev)
    | select(.labels.alertname | test($allow))
    | select(.labels.alertname | test($deny) | not)
    | select(((.startsAt | sub("\\.[0-9]+";"") | fromdateiso8601? ) // $now) > ($now - $maxage))
  ] | sort_by(.startsAt) | reverse' 2>/dev/null)"
count="$(printf '%s' "$candidates" | jq 'length' 2>/dev/null)" || count=0
if [ "${count:-0}" -eq 0 ] 2>/dev/null; then
  log "no active ${SEVERITY} alerts in scope — exit \$0."
  exit 0
fi
log "${count} in-scope ${SEVERITY} alert(s) active."

# ── 2. Pick new incidents (newest first), at most MAX_PER_RUN. ──
handled=0
i=0
while [ "$i" -lt "$count" ] && [ "$handled" -lt "$MAX_PER_RUN" ]; do
  alert="$(printf '%s' "$candidates" | jq -c ".[$i]")"
  i=$((i + 1))
  aname="$(printf '%s' "$alert" | jq -r '.labels.alertname // "unknown"')"
  ans="$(printf '%s' "$alert" | jq -r '.labels.namespace // .labels.exported_namespace // ""')"
  fp="$(printf '%s' "$alert" | jq -r '.fingerprint // ""')"
  sat="$(printf '%s' "$alert" | jq -r '.startsAt // ""')"
  key="$(sig_of "${fp}|${sat}")"
  if state_has "$key"; then
    log "incident $key ($aname) already handled — skip."
    continue
  fi
  spend_guard || exit 0
  if ! state_claim "$key" "$aname"; then
    log "incident $key ($aname): could NOT durably claim — FAIL CLOSED, not summoning (Alertmanager already paged the human)."
    continue
  fi
  log "incident $key ($aname ns=$ans): claimed — summoning read-only diagnosis."

  # ── 3. Anonymous read-only clone for the committed runbooks (repo is public; NO
  #      credential exists in this pod). Best-effort — diagnosis proceeds without it. ──
  rm -rf "$WORKDIR"
  git clone --depth 1 --quiet "$REPO_URL" "$WORKDIR" >/dev/null 2>&1 || log "clone failed — continuing without repo context."
  cd "$WORKDIR" 2>/dev/null || cd /tmp

  ALERT_JSON="$(printf '%s' "$alert" | jq -c '{labels, annotations, startsAt}')"
  PROMPT="You are the on-call ALERT RESPONDER for this Kubernetes homelab (GitOps/Flux, Talos). A critical alert is firing; Alertmanager ALREADY paged the human — your follow-up page is worth sending ONLY when it adds an action the human must take. Alert: ${ALERT_JSON}. Investigate with the allowlisted tools: kubectl get/describe, flux get, /opt/responder/prom-query.sh '<promql>' for metrics, /opt/responder/loki-query.sh '<logql>' [minutes] [limit] for logs (e.g. loki-query.sh '{namespace=\"x\",pod=~\"y.*\"}' 60). Check whether the alert is ALREADY RESOLVED (prom-query.sh 'ALERTS{alertname=\"<name>\",alertstate=\"firing\"}' — empty means cleared). If a repo checkout is present, check .agents/runbooks/ and docs/ for a matching runbook and the recent git log for a plausible culprit. Then STOP and output EXACTLY this report, under 900 characters total, no markdown headers, STARTING with ACTION: ACTION: <none|investigate|urgent> | CAUSE: <root cause, one or two sentences, state confidence> | EVIDENCE: <two or three concrete observations> | FIX: <suggested action for the human — you must NOT perform it> | RUNBOOK: <repo path or none>. Choose ACTION honestly: ACTION: none when NO human action is needed — the alert already self-healed, OR it is a KNOWN/DOCUMENTED self-healing cycle (a watchdog auto-repair, an accepted OOM-restart cycle, a component another system's watchdog owns and notifies for); ACTION: investigate when a human should look but it is not on fire; ACTION: urgent when immediate action is needed. Be decisive — if you are confident it self-healed or is a documented benign cycle, say ACTION: none (a no-action page is pure noise the human already got from Alertmanager)."
  SAFETY="SAFETY: you are STRICTLY READ-ONLY — never kubectl apply/delete/edit/exec/patch, never git push, never install anything; do not retry a denied command. Never include secret VALUES in output. You run UNATTENDED: no human answers questions; produce the report and stop. If you cannot diagnose it, output ACTION: investigate | CAUSE: inconclusive."

  export DISABLE_TELEMETRY=1 CLAUDE_CODE_ENABLE_TELEMETRY=0 \
         DISABLE_ERROR_REPORTING=1 DISABLE_AUTOUPDATER=1 DISABLE_NON_ESSENTIAL_MODEL_CALLS=1
  : "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY unset}"
  OUT_FILE="$(mktemp 2>/dev/null || echo /tmp/responder-out.json)"
  timeout "$RUN_TIMEOUT" claude -p "$PROMPT" \
    --permission-mode dontAsk \
    --allowedTools Read Grep Glob \
      "Bash(kubectl get:*)" "Bash(kubectl describe:*)" "Bash(flux get:*)" \
      "Bash(grep:*)" "Bash(cat:*)" "Bash(git log:*)" "Bash(git show:*)" \
      "Bash(/opt/responder/prom-query.sh:*)" "Bash(/opt/responder/loki-query.sh:*)" \
    --disallowedTools "WebFetch" "WebSearch" \
    --append-system-prompt "$SAFETY" \
    --max-turns "$MAX_TURNS" \
    --max-budget-usd "$MAX_BUDGET" \
    --model "$MODEL" \
    --output-format json > "$OUT_FILE" 2>/dev/null
  rc=$?
  record_spend "$OUT_FILE"
  REPORT="$(jq -r '.result // empty' "$OUT_FILE" 2>/dev/null | tr '\n\r' '  ' | cut -c1-950)"
  if [ -n "$REPORT" ]; then
    log "diagnosis ($aname): $REPORT"
    # PAGE GATE (2026-07-09): the responder page is a FOLLOW-UP on top of the page
    # Alertmanager already sent. Only send it when it ADDS an action — otherwise it is
    # pure noise (a UniFi-Protect self-heal + a ceph-mgr known-OOM cycle both paged
    # "no action required", which is what prompted this). Suppress when EITHER the LLM
    # verdict is ACTION: none, OR the alert has self-resolved since we were summoned.
    ACTION="$(printf '%s' "$REPORT" | grep -oiE 'ACTION:[[:space:]]*(none|investigate|urgent)' | head -1 | grep -oiE '(none|investigate|urgent)' | tr 'A-Z' 'a-z')"
    still_firing="$(curl -sf --max-time 10 "$AM/api/v2/alerts?active=true&silenced=false" 2>/dev/null \
      | jq -r --arg fp "$fp" 'if any(.[]?; .fingerprint==$fp) then "yes" else "no" end' 2>/dev/null)"
    if [ "$ACTION" = "none" ]; then
      log "PAGE-SUPPRESSED ($aname): ACTION=none (no human action needed; the original Alertmanager page already covers it). Diagnosis kept in logs only."
    elif [ "$still_firing" = "no" ]; then
      log "PAGE-SUPPRESSED ($aname): alert self-resolved during diagnosis — not paging (nothing to act on)."
    else
      page "${aname}${ans:+ (${ans})}" "${REPORT} [auto-diagnosis — verify before acting]"
    fi
  else
    log "diagnosis produced no report (rc=$rc) — silent (the original Alertmanager page stands)."
  fi
  handled=$((handled + 1))
done
log "responder cycle complete: handled=$handled"
exit 0
