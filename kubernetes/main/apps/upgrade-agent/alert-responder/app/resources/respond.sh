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
# STORM DISCIPLINE (2026-08-20, the rook-ceph page storm — 16 near-identical pages
# in 2.5h while a wedged rook-ceph-cluster HR fanned into ~30 FluxReconciliationFailure
# alerts):
#   - STORM COLLAPSE: >= RESPONDER_STORM_THRESHOLD unhandled incidents of one
#     alertname = ONE diagnosis of the shared root cause, ONE page, all claimed.
#   - COOLDOWN: same alertname@ns (alertname-wide after a storm) is not re-diagnosed
#     within RESPONDER_COOLDOWN_HOURS even when a resolve→refire mints a new
#     startsAt — flapping is the root cause's problem, not a new incident.
#   - PAGE CAP: at most RESPONDER_MAX_PAGES_PER_HOUR responder pages; excess
#     diagnoses land in the logs only.
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
# Namespace-SCOPED ignores (space-separated `alertname@namespace` pairs) — a surgical
# skip for a KNOWN-benign recurring alert without a blanket alertname denylist that would
# also hide the same alert elsewhere. 2026-07-12: OOMKilled@rook-ceph — the ceph-mgr has a
# documented ~8wk memory-leak self-heal OOM cycle; the responder re-diagnosed it 7x this
# month ($). A REAL OOMKilled on any OTHER namespace is still diagnosed; a genuine Ceph OOM
# disaster still surfaces via the gate's Ceph HEALTH_ERR page + Alertmanager. Grow this
# list as we identify other noisy recurrers (the state CM + Loki logs record every diagnosis
# → alertname/cost, so we can see what else is triggering spend before a broader scope call).
IGNORE_PAIRS="${RESPONDER_IGNORE_PAIRS:-OOMKilled@rook-ceph}"
MAX_PER_RUN="${RESPONDER_MAX_PER_RUN:-1}"
MAX_AGE_HOURS="${RESPONDER_MAX_AGE_HOURS:-24}"
STATE_TTL_HOURS="${RESPONDER_STATE_TTL_HOURS:-168}"
# ── Storm/loop guards (2026-08-20, the rook-ceph page storm) ──
# A single root cause (a wedged rook-ceph-cluster HR) fanned out into ~30
# FluxReconciliationFailure alerts; each was its own fingerprint, so every 10-min
# run claimed ONE new incident, re-derived the SAME root cause, and paged — 16
# near-identical pages in 2.5h. Worse, each helm retry flapped alerts
# resolve→refire, minting NEW startsAt values that re-armed at-most-once forever.
#   STORM_THRESHOLD  — >= this many unhandled incidents of one alertname collapse
#                      into ONE diagnosis + ONE page (all fingerprints claimed).
#   COOLDOWN_HOURS   — after diagnosing alertname@ns (or a storm of alertname),
#                      re-fires of the same pair are skipped for this long even
#                      though a new startsAt makes them "new" incidents.
#   MAX_PAGES_PER_HOUR — hard ceiling on responder pages; excess diagnoses are
#                      logged only (Alertmanager's own page still stands).
STORM_THRESHOLD="${RESPONDER_STORM_THRESHOLD:-3}"
COOLDOWN_HOURS="${RESPONDER_COOLDOWN_HOURS:-6}"
MAX_PAGES_PER_HOUR="${RESPONDER_MAX_PAGES_PER_HOUR:-3}"
MODEL="${RESPONDER_MODEL:-claude-opus-5}"      # plan path: always latest Opus (2026-08-23)
FALLBACK_MODEL="${RESPONDER_FALLBACK_MODEL:-claude-sonnet-5}"  # metered: never Fable/Opus (2026-08-23)
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

# Quiet claude-code's phone-home (the egress CNP would block it anyway).
export DISABLE_TELEMETRY=1 CLAUDE_CODE_ENABLE_TELEMETRY=0 \
       DISABLE_ERROR_REPORTING=1 DISABLE_AUTOUPDATER=1 DISABLE_NON_ESSENTIAL_MODEL_CALLS=1

# ── AUTH PATH (2026-07-28, saga plan 07 Option A extended to the responder) ──
# PLAN-FIRST, API-FALLBACK — the run-shepherd.sh pattern verbatim. The LLM turn
# runs HERE, in the contained responder pod (alert labels/annotations are a
# prompt-injection surface; dispatching into dev-env would hand that surface the
# dev pod's write powers — see .agents/sagas/dev-env/backlog/07).
#
#   plan → CLAUDE_CODE_OAUTH_TOKEN (Max subscription; $0 API spend), model=$MODEL
#   api  → ANTHROPIC_API_KEY (metered, spend-guarded), model=$FALLBACK_MODEL
#
# CRITICAL: claude-code prefers ANTHROPIC_API_KEY when BOTH are set, so the plan
# path must UNSET it — otherwise we'd silently keep billing the API while
# believing we're on the plan. The key is stashed and restored for the fallback.
AUTH_PATH="api"
ANTHROPIC_API_KEY_STASH="${ANTHROPIC_API_KEY:-}"
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  AUTH_PATH="plan"
  unset ANTHROPIC_API_KEY
  log "auth: Max plan (CLAUDE_CODE_OAUTH_TOKEN); API key held in reserve for fallback."
elif [ -n "${ANTHROPIC_API_KEY_STASH}" ]; then
  log "auth: API key (no plan token mounted)."
else
  log "FATAL: neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set."; exit 1
fi

# `%Y-%m-%d-%H` (no colon/T): the bucket lands in a ConfigMap key, see sk().
HOUR_BUCKET="$(date -u +%Y-%m-%d-%H)"
page() {  # $1=title-suffix $2=message ; priority 0 (the ORIGINAL critical already
          # paged at prio 1 via Alertmanager — this is the follow-up diagnosis).
          # Rate-capped at MAX_PAGES_PER_HOUR (2026-08-20 storm guard): an alert
          # storm must never turn the responder into a second spam source — the
          # diagnosis always lands in the logs either way.
  if [ "$DRY" = "1" ]; then log "DRY: would page '[responder] $1' :: $2"; return 0; fi
  local sent
  sent="$(printf '%s' "$STATE_JSON" | jq -r --arg k "pages.$HOUR_BUCKET" \
    '(.[$k] // "{}") | (fromjson? // {}) | (.count // "0") | tonumber? // 0' 2>/dev/null)" || sent=0
  if [ "${sent:-0}" -ge "$MAX_PAGES_PER_HOUR" ] 2>/dev/null; then
    log "PAGE-CAPPED ($1): $sent pages already this hour (cap $MAX_PAGES_PER_HOUR) — diagnosis kept in logs only."
    return 0
  fi
  : "${PUSHOVER_TOKEN:?}" ; : "${PUSHOVER_USER_KEY:?}"
  curl -sf --max-time 10 https://api.pushover.net/1/messages.json \
    --form-string "token=${PUSHOVER_TOKEN}" \
    --form-string "user=${PUSHOVER_USER_KEY}" \
    --form-string "title=[responder] $1" \
    --form-string "message=$2" \
    --form-string "priority=0" >/dev/null \
    && { log "paged: [responder] $1"
         state_write "$(jq -nc --arg k "pages.$HOUR_BUCKET" --argjson now "$NOW" --argjson n "$(( sent + 1 ))" \
           '{($k): ({count:($n|tostring), first_seen:($now|tostring)} | tojson)}')" \
           || log "WARN: could not record page count (cap may under-enforce this hour)"; } \
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
# Read ONCE per run into STATE_JSON (the cron is concurrencyPolicy: Forbid, so no
# writer races us); all membership checks are in-memory, all writes go through
# state_write (which merges + TTL-prunes + refreshes the in-memory copy).
sig_of() { printf '%s' "$1" | sha256sum | grep -oE '[0-9a-f]{64}' | head -1 | cut -c1-12; }
sk() {  # sanitize a constructed state key to the ConfigMap-legal charset.
        # CM data keys must match [-._a-zA-Z0-9]+ — the API server REJECTS the
        # whole write otherwise, which fails claims closed and silently disables
        # diagnosis (bitten 2026-08-20 with `cool:<name>@<ns>` keys).
  printf '%s' "$1" | tr -c 'a-zA-Z0-9._-' '_'
}
STATE_JSON='{}'
STATE_RAW="$(kubectl -n "$NS" get configmap "$STATE_CM" -o json 2>&1)"; STATE_RC=$?
if [ $STATE_RC -ne 0 ]; then
  case "$STATE_RAW" in
    *NotFound*|*"not found"*) STATE_JSON='{}' ;;  # verifiably no prior state
    *) log "state: UNREADABLE ($STATE_RAW) — failing CLOSED (every incident treated as handled)."
       STATE_JSON='__UNREADABLE__' ;;
  esac
else
  STATE_JSON="$(printf '%s' "$STATE_RAW" | jq -c '.data // {}' 2>/dev/null)" || STATE_JSON='__UNREADABLE__'
fi
state_has() {  # $1=key ; rc0 = already handled. Unreadable state => handled
               # (FAIL CLOSED — protect spend; Alertmanager already paged the human).
  [ "$STATE_JSON" = "__UNREADABLE__" ] && return 0
  printf '%s' "$STATE_JSON" | jq -e --arg k "$1" '.[$k] // empty' >/dev/null 2>&1
}
cooldown_active() {  # $1=alertname $2=ns ; rc0 = this pair (or an alertname-wide
                     # storm cooldown) was diagnosed within COOLDOWN_HOURS — skip.
                     # This is the FLAP GUARD: a resolve→refire mints a new
                     # startsAt (new incident key), but the same alertname@ns
                     # re-diagnosed minutes later is noise, not signal.
  [ "$STATE_JSON" = "__UNREADABLE__" ] && return 0
  local cd=$(( COOLDOWN_HOURS * 3600 ))
  # `_all` is the storm (alertname-wide) marker — it cannot collide with a real
  # namespace because DNS-1123 names never contain `_`.
  printf '%s' "$STATE_JSON" | jq -e --arg a "$(sk "cool.$1.$2")" --arg w "$(sk "cool.$1._all")" \
      --argjson now "$NOW" --argjson cd "$cd" '
    [.[$a] // empty, .[$w] // empty] | map(fromjson? // {})
    | any(.[]; ((.first_seen // "0") | tonumber? // 0) > ($now - $cd))' >/dev/null 2>&1
}
state_write() {  # $1 = JSON object {key: value-string, ...} merged into .data with
                 # TTL pruning. rc!=0 => the write did NOT durably land (claims must
                 # then FAIL CLOSED). Refreshes STATE_JSON on success.
  local ttl=$(( STATE_TTL_HOURS * 3600 )) add="$1"
  if [ "$STATE_JSON" = "__UNREADABLE__" ]; then return 1; fi
  if kubectl -n "$NS" get configmap "$STATE_CM" >/dev/null 2>&1; then
    kubectl -n "$NS" get configmap "$STATE_CM" -o json 2>/dev/null \
      | jq --argjson add "$add" --argjson now "$NOW" --argjson ttl "$ttl" '
          .data = ((.data // {}) + $add)
          | .data |= with_entries(
                select(.key as $ek | ($add | has($ek))
                       or ((((.value | (fromjson? // {}) | (.first_seen // "0") | tonumber?) // 0)) > ($now - $ttl))))
        ' 2>/dev/null | kubectl -n "$NS" replace -f - >/dev/null 2>&1
  else
    jq -nc --argjson add "$add" --arg cm "$STATE_CM" --arg ns "$NS" \
      '{apiVersion:"v1", kind:"ConfigMap", metadata:{name:$cm, namespace:$ns}, data:$add}' \
      | kubectl -n "$NS" create -f - >/dev/null 2>&1
  fi || return 1
  STATE_JSON="$(printf '%s' "$STATE_JSON" | jq -c --argjson add "$add" '. + $add' 2>/dev/null)" \
    || STATE_JSON='__UNREADABLE__'
}
claim_entry() {  # $1=alertname ; emit the standard claim/cooldown entry VALUE (a JSON string)
  jq -nc --arg a "$1" --argjson now "$NOW" \
    '{alertname:$a, attempted:"1", first_seen:($now|tostring)} | tojson'
}

# ── 1. Poll Alertmanager (the $0 path). ──
alerts="$(curl -sf --max-time 15 "$AM/api/v2/alerts?active=true&silenced=false&inhibited=false" 2>/dev/null)"
if [ -z "$alerts" ]; then
  log "Alertmanager unreachable/empty response — nothing to do (Alertmanager pages independently; the gate covers blind spots)."
  exit 0
fi
candidates="$(printf '%s' "$alerts" | jq -c --arg sev "$SEVERITY" --arg allow "$ALLOWLIST" --arg deny "$DENYLIST" --arg ignore "$IGNORE_PAIRS" --argjson now "$NOW" --argjson maxage "$(( MAX_AGE_HOURS * 3600 ))" '
  ($ignore | split(" ") | map(select(. != ""))) as $ignorepairs
  | [ .[]
    | select(.labels.severity == $sev)
    | select(.labels.alertname | test($allow))
    | select(.labels.alertname | test($deny) | not)
    # namespace-scoped ignore (2026-07-12): drop known-benign recurrers by
    # alertname@namespace (e.g. OOMKilled@rook-ceph = the ceph-mgr self-heal cycle),
    # WITHOUT a blanket alertname denylist. Uses the same ns resolution as the loop below.
    | select((.labels.alertname + "@" + (.labels.namespace // .labels.exported_namespace // "")) as $p | ($ignorepairs | index($p)) == null)
    # scope=host opt-out (2026-07-10): the CLEAN, future-proof way to keep the responder
    # out of an alert — host/hardware/appliance alerts (SMART, RAID, fan/power, NAS) that
    # are self-evident + unactionable-by-cluster. The alert author labels the rule
    # `scope: host` and the responder skips it (no LLM, no page); Alertmanager still routes
    # it to Pushover normally. Anything without the label is unaffected.
    | select((.labels.scope // "") != "host")
    | select(((.startsAt | sub("\\.[0-9]+";"") | fromdateiso8601? ) // $now) > ($now - $maxage))
  ] | sort_by(.startsAt) | reverse' 2>/dev/null)"
count="$(printf '%s' "$candidates" | jq 'length' 2>/dev/null)" || count=0
if [ "${count:-0}" -eq 0 ] 2>/dev/null; then
  log "no active ${SEVERITY} alerts in scope — exit \$0."
  exit 0
fi
log "${count} in-scope ${SEVERITY} alert(s) active."

# ── 2. Build the eligible set (unhandled + not cooling), then collapse storms. ──
# STORM COLLAPSE (2026-08-20): >= STORM_THRESHOLD unhandled incidents sharing one
# alertname become ONE work unit — one diagnosis of the COMMON root cause, one
# page, ALL fingerprints claimed. Without this, a single wedged dependency (the
# rook-ceph-cluster HR) fans into ~30 FluxReconciliationFailure incidents and the
# responder re-diagnoses the same root once per run, forever.
elig='[]'
i=0
while [ "$i" -lt "$count" ]; do
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
  if cooldown_active "$aname" "$ans"; then
    log "incident $key ($aname@$ans) inside the ${COOLDOWN_HOURS}h cooldown — skip (flap guard: a resolve→refire is not a new problem)."
    continue
  fi
  elig="$(printf '%s' "$elig" | jq -c --argjson a "$alert" --arg key "$key" --arg aname "$aname" --arg ans "$ans" --arg fp "$fp" \
    '. + [{key:$key, aname:$aname, ans:$ans, fp:$fp, alert:$a}]')"
done
# Work units: storm groups first (largest first), then singletons (newest first —
# elig preserves the candidates sort).
units="$(printf '%s' "$elig" | jq -c --argjson t "$STORM_THRESHOLD" '
  (group_by(.aname) | map(select(length >= $t)) | sort_by(-length)
     | map({type:"storm", aname:(.[0].aname), members:.})) as $storms
  | ($storms | map(.members[].key)) as $stormkeys
  | ($storms + (map(select(.key as $k | ($stormkeys | index($k)) | not))
                  | map({type:"single", aname, ans, members:[.]})))')"
u_count="$(printf '%s' "$units" | jq 'length' 2>/dev/null)" || u_count=0

handled=0
u=0
while [ "$u" -lt "${u_count:-0}" ] && [ "$handled" -lt "$MAX_PER_RUN" ]; do
  unit="$(printf '%s' "$units" | jq -c ".[$u]")"
  u=$((u + 1))
  utype="$(printf '%s' "$unit" | jq -r '.type')"
  aname="$(printf '%s' "$unit" | jq -r '.aname')"
  ans="$(printf '%s' "$unit" | jq -r '.members[0].ans // ""')"
  n="$(printf '%s' "$unit" | jq -r '.members | length')"
  alert="$(printf '%s' "$unit" | jq -c '.members[0].alert')"
  fps="$(printf '%s' "$unit" | jq -c '[.members[].fp]')"
  # Spend guard governs the METERED path only — a plan-served diagnosis is $0, so a
  # high (stale) monthly counter must not block it. The guard still protects the
  # fallback (checked again before any fallback retry below).
  if [ "$AUTH_PATH" = "api" ]; then spend_guard || exit 0; fi
  # Claim BEFORE the summon (crash-proof): every member key, plus the cooldown
  # marker — alertname-wide (`@*`) for a storm so late-joining victims of the same
  # root cause don't each mint a fresh diagnosis; alertname@ns for a singleton.
  cool_key="$(sk "cool.${aname}._all")"; [ "$utype" = "single" ] && cool_key="$(sk "cool.${aname}.${ans}")"
  additions="$(printf '%s' "$unit" | jq -c --arg ck "$cool_key" --argjson now "$NOW" '
    (.members | map({(.key): ({alertname:.aname, attempted:"1", first_seen:($now|tostring)} | tojson)}) | add)
    + {($ck): ({alertname:.aname, attempted:"1", first_seen:($now|tostring)} | tojson)}')"
  if ! state_write "$additions"; then
    log "unit $aname (${utype} n=$n): could NOT durably claim — FAIL CLOSED, not summoning (Alertmanager already paged the human)."
    continue
  fi
  log "unit $aname (${utype} n=$n ns=$ans): claimed — summoning read-only diagnosis."

  # ── 3. Anonymous read-only clone for the committed runbooks (repo is public; NO
  #      credential exists in this pod). Best-effort — diagnosis proceeds without it. ──
  rm -rf "$WORKDIR"
  git clone --depth 1 --quiet "$REPO_URL" "$WORKDIR" >/dev/null 2>&1 || log "clone failed — continuing without repo context."
  cd "$WORKDIR" 2>/dev/null || cd /tmp

  ALERT_JSON="$(printf '%s' "$alert" | jq -c '{labels, annotations, startsAt}')"
  if [ "$utype" = "storm" ]; then
    # Compact label set for every storm member (capped) — the diagnosis must name
    # the ONE shared root cause, not re-investigate each victim.
    STORM_LABELS="$(printf '%s' "$unit" | jq -c '[.members[:25][].alert.labels | del(.severity, .prometheus, .container, .endpoint, .instance, .job, .service, .pod)]')"
    ALERT_CONTEXT="ALERT STORM: ${n} critical alerts are firing with the SAME alertname (${aname}) — they almost certainly share ONE root cause (e.g. a common dependency). Identify the single failing component; do NOT diagnose each victim separately. Representative alert: ${ALERT_JSON}. All affected instances (label sets, first 25): ${STORM_LABELS}."
  else
    ALERT_CONTEXT="Alert: ${ALERT_JSON}."
  fi
  PROMPT="You are the on-call ALERT RESPONDER for this Kubernetes homelab (GitOps/Flux, Talos). A critical alert is firing; Alertmanager ALREADY paged the human — your follow-up page is worth sending ONLY when it adds an action the human must take. ${ALERT_CONTEXT} Investigate with the allowlisted tools: kubectl get/describe, flux get, /opt/responder/prom-query.sh '<promql>' for metrics, /opt/responder/loki-query.sh '<logql>' [minutes] [limit] for logs (e.g. loki-query.sh '{namespace=\"x\",pod=~\"y.*\"}' 60). Check whether the alert is ALREADY RESOLVED (prom-query.sh 'ALERTS{alertname=\"<name>\",alertstate=\"firing\"}' — empty means cleared). If a repo checkout is present, check .agents/runbooks/ and docs/ for a matching runbook and the recent git log for a plausible culprit. Then STOP and output EXACTLY this report, under 900 characters total, no markdown headers, STARTING with ACTION: ACTION: <none|investigate|urgent> | CAUSE: <root cause, one or two sentences, state confidence> | EVIDENCE: <two or three concrete observations> | FIX: <suggested action for the human — you must NOT perform it> | RUNBOOK: <repo path or none>. Choose ACTION honestly: ACTION: none when NO human action is needed — the alert already self-healed, OR it is a KNOWN/DOCUMENTED self-healing cycle (a watchdog auto-repair, an accepted OOM-restart cycle, a component another system's watchdog owns and notifies for); ACTION: investigate when a human should look but it is not on fire; ACTION: urgent when immediate action is needed. Be decisive — if you are confident it self-healed or is a documented benign cycle, say ACTION: none (a no-action page is pure noise the human already got from Alertmanager)."
  SAFETY="SAFETY: you are STRICTLY READ-ONLY — never kubectl apply/delete/edit/exec/patch, never git push, never install anything; do not retry a denied command. Never include secret VALUES in output. You run UNATTENDED: no human answers questions; produce the report and stop. If you cannot diagnose it, output ACTION: investigate | CAUSE: inconclusive."

  OUT_FILE="$(mktemp 2>/dev/null || echo /tmp/responder-out.json)"
  ERR_FILE="${OUT_FILE}.err"
  run_claude() {  # $1=out-file $2=err-file ; model + budget flag follow AUTH_PATH
    local m="$MODEL"; local -a args
    [ "$AUTH_PATH" = "api" ] && m="$FALLBACK_MODEL"
    args=(-p "$PROMPT"
      --permission-mode dontAsk
      --allowedTools Read Grep Glob
        "Bash(kubectl get:*)" "Bash(kubectl describe:*)" "Bash(flux get:*)"
        "Bash(grep:*)" "Bash(cat:*)" "Bash(git log:*)" "Bash(git show:*)"
        "Bash(/opt/responder/prom-query.sh:*)" "Bash(/opt/responder/loki-query.sh:*)"
      --disallowedTools "WebFetch" "WebSearch"
      --append-system-prompt "$SAFETY"
      --max-turns "$MAX_TURNS"
      --model "$m"
      --output-format json)
    [ "$AUTH_PATH" = "api" ] && args+=(--max-budget-usd "$MAX_BUDGET")
    timeout "$RUN_TIMEOUT" claude "${args[@]}" > "$1" 2>"$2"
  }
  run_claude "$OUT_FILE" "$ERR_FILE"; rc=$?
  # A plan run that dies on a RATE LIMIT / AUTH failure falls back ONCE to the
  # metered key ($FALLBACK_MODEL, spend-guarded) — a 2am diagnosis must not stall
  # because the 5-hour plan window is exhausted. Any OTHER failure is a real error
  # and is NOT retried (a genuinely broken run would just double-spend).
  if [ "$AUTH_PATH" = "plan" ] && [ "$rc" -ne 0 ] && [ -n "$ANTHROPIC_API_KEY_STASH" ]; then
    fallback_reason=""
    if grep -qiE 'rate limit|usage limit|limit reached|too many requests|429' "$OUT_FILE" "$ERR_FILE" 2>/dev/null; then
      fallback_reason="plan rate-limited"
    elif grep -qiE 'unauthorized|invalid.*(token|api key)|authentication|401|/login' "$OUT_FILE" "$ERR_FILE" 2>/dev/null; then
      fallback_reason="plan token rejected (re-run the setup-token ceremony)"
    fi
    if [ -n "$fallback_reason" ]; then
      log "FALLBACK: $fallback_reason — retrying on the metered API key ($FALLBACK_MODEL)."
      AUTH_PATH="api"
      export ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY_STASH"
      if spend_guard; then
        run_claude "$OUT_FILE" "$ERR_FILE"; rc=$?
      else
        log "fallback BLOCKED by the spend guard (monthly cap reached) — no diagnosis this incident (the original Alertmanager page stands)."
      fi
    else
      log "run failed (rc=$rc) for a reason that is NOT rate-limit/auth — not falling back."
    fi
  fi
  # Only a metered run has a dollar cost to record; a plan-served run is $0 by
  # construction (that IS the win being measured).
  if [ "$AUTH_PATH" = "api" ]; then
    record_spend "$OUT_FILE"
  else
    log "spend: \$0 this run (served by the Max plan)."
  fi
  REPORT="$(jq -r '.result // empty' "$OUT_FILE" 2>/dev/null | tr '\n\r' '  ' | cut -c1-950)"
  if [ -n "$REPORT" ]; then
    log "diagnosis ($aname): $REPORT"
    # PAGE GATE (2026-07-09): the responder page is a FOLLOW-UP on top of the page
    # Alertmanager already sent. Only send it when it ADDS an action — otherwise it is
    # pure noise (a UniFi-Protect self-heal + a ceph-mgr known-OOM cycle both paged
    # "no action required", which is what prompted this). Suppress when EITHER the LLM
    # verdict is ACTION: none, OR the alert has self-resolved since we were summoned.
    ACTION="$(printf '%s' "$REPORT" | grep -oiE 'ACTION:[[:space:]]*(none|investigate|urgent)' | head -1 | grep -oiE '(none|investigate|urgent)' | tr 'A-Z' 'a-z')"
    # For a storm, "still firing" = ANY member fingerprint still active.
    still_firing="$(curl -sf --max-time 10 "$AM/api/v2/alerts?active=true&silenced=false" 2>/dev/null \
      | jq -r --argjson fps "$fps" 'if any(.[]?; .fingerprint as $f | $fps | index($f)) then "yes" else "no" end' 2>/dev/null)"
    if [ "$ACTION" = "none" ]; then
      log "PAGE-SUPPRESSED ($aname): ACTION=none (no human action needed; the original Alertmanager page already covers it). Diagnosis kept in logs only."
    elif [ "$still_firing" = "no" ]; then
      log "PAGE-SUPPRESSED ($aname): alert self-resolved during diagnosis — not paging (nothing to act on)."
    else
      title="${aname}${ans:+ (${ans})}"
      [ "$utype" = "storm" ] && title="${aname} ×${n} — storm, one root cause"
      page "$title" "${REPORT} [auto-diagnosis — verify before acting]"
    fi
  else
    log "diagnosis produced no report (rc=$rc) — silent (the original Alertmanager page stands)."
    # ── FAILURE ESCALATION (2026-08-21, backlog 12→13) ── the incident was CLAIMED
    # (at-most-once burned) but the responder itself failed: run died rc!=0 after the
    # plan path + any fallback (incl. fallback-blocked-by-spend-guard, where rc keeps
    # the first run's failure) and produced NO report — the 2am page stays
    # uninvestigated and nothing will retry. File an esc-responder-* entry keyed on
    # alertname@ns (stable across re-fires) so the dev-env-ops executor spawns a
    # joinable fable/xhigh session and pages Tom WITH the session name. Ordinary
    # completed diagnoses (ACTION: none/investigate/urgent) NEVER escalate.
    # BEST-EFFORT: a write failure only logs.
    if [ "$rc" -ne 0 ]; then
      ESCALATE_SIG="${aname}@${ans}" bash /opt/coordination/escalate.sh responder "job:${HOSTNAME:-unknown}" \
        "diagnosis FAILED rc=${rc} for ${aname}@${ans:-?} (${utype} n=${n}): incident claimed but no diagnosis produced (plan+fallback exhausted or run died) — Alertmanager's page stands uninvestigated. See the alert-responder Job logs." \
        || log "escalate.sh could not file the esc-responder entry (best-effort)"
    fi
  fi
  handled=$((handled + 1))
done
log "responder cycle complete: handled=$handled"
exit 0
