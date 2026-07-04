#!/usr/bin/env bash
# upgrade-shepherd-triage — the Phase-4b.3 auto-summon BRAIN. DETERMINISTIC, NO LLM on the
# common path: runs on a schedule and summons the LLM (MODE=remediate) ONLY for a regression
# that a RECENT UPGRADE (a merge to main touching that workload's app path) plausibly caused,
# and only ONCE per distinct regression, ever. On a healthy cluster it costs nothing (a
# couple of kubectl/curl calls, then exit 0).
#
# WHY it lives here and not in the gate: the health-gate is deliberately read-only +
# GitHub-less + Pushover-only (an independent tripwire) and CANNOT create work. The summon
# therefore runs under the SHEPHERD's identity (bot token + LLM key + the spend-guarded
# run-shepherd.sh). triage and the gate coordinate through ONE runtime ConfigMap
# (`upgrade-remediation-state`, NOT git-managed — Flux would revert it): triage OWNS the
# class/attempted/result fields; the gate reads them to decide whether to page and only
# stamps `last_paged`. This decoupling is deliberate — the gate keeps paging independently.
#
# THE 2026-07 REDESIGN (why this file changed). A cilium-operator crash-loop (pure Talos/
# KubePrism infra, NOT an upgrade) tripped the old `(any recent merge) AND (any regression)`
# logic: it summoned remediate ($1.43, ran to max-turns, produced NO PR) AND re-paged every
# 30m. The rules now, in priority order (COST first):
#   1. AT MOST ONCE, CRASH-PROOF. `attempted=1` is written BEFORE remediate is invoked, so a
#      crash/timeout/pod-death mid-run still counts as attempted and is never retried.
#   2. SUMMON FAILS CLOSED. Remediate fires ONLY when a recent merge touched the REGRESSING
#      workload's own app path (deterministic path attribution). If the state ConfigMap is
#      UNREADABLE (can't confirm we haven't already tried), or attribution can't run, we do
#      NOT summon — protect the money; the gate still pages so a human handles it.
#   3. GATE PAGES, NOT triage. triage has no Pushover access. A human is paged ONLY when
#      auto-intervention has FAILED (or for a Flux deploy failure the gate alone catches).
#
# CONTAINMENT: same as the shepherd — read-only cluster SA (+ the in-namespace configmaps
# Role for the two runtime ConfigMaps), egress CNP (GitHub/Anthropic/cluster-read), bot token
# minted by the initContainer to /creds (PEM never here), the monthly spend guard inside
# run-shepherd.sh.
set -uo pipefail

PROM="${PROMETHEUS_URL:-http://kube-prometheus-stack-prometheus.observability.svc.cluster.local:9090}"
LOOKBACK_HOURS="${TRIAGE_MERGE_LOOKBACK_HOURS:-3}"
REPO="${REPO:-thaynes43/haynes-ops}"
REPAGE_SUPPRESS_HOURS="${REPAGE_SUPPRESS_HOURS:-6}"
STATE_TTL_HOURS="${STATE_TTL_HOURS:-24}"
NOW="$(date -u +%s)"
SINCE="$(date -u -d "-${LOOKBACK_HOURS} hours" +%FT%TZ 2>/dev/null || date -u +%FT%TZ)"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }

GH_TOKEN="$(cat /creds/gh_token 2>/dev/null || true)"
[ -n "$GH_TOKEN" ] || { log "FATAL: /creds/gh_token missing (mint failed) — cannot attribute merges."; exit 1; }
export GH_TOKEN   # so `gh` and `git` (via the clone URL) both see it

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

# ── state READ for the summon decision — MUST distinguish "readable" from "unreadable" so
#    the summon can FAIL CLOSED (rule 2): sets STATE_READ_STATUS = ok | absent | error and
#    STATE_JSON (the whole CM when ok). A clean NotFound is `absent` (verifiably no prior
#    state => a first attempt is safe to make); any other kubectl failure is `error`
#    (UNVERIFIABLE => do NOT summon, protect spend). ──
STATE_READ_STATUS=""
STATE_JSON=""
state_read() {
  local errf rc err
  errf="$(mktemp 2>/dev/null || echo "/tmp/staterr.$$")"
  STATE_JSON="$(kubectl -n "$STATE_NS" get configmap "$STATE_CM" -o json 2>"$errf")"
  rc=$?
  err="$(cat "$errf" 2>/dev/null)"
  rm -f "$errf" 2>/dev/null
  if [ "$rc" -eq 0 ]; then STATE_READ_STATUS=ok; return 0; fi
  STATE_JSON=""
  case "$err" in
    *NotFound*|*"not found"*) STATE_READ_STATUS=absent ;;
    *) STATE_READ_STATUS=error ;;
  esac
}
entry_of() {  # $1=whole-CM JSON $2=sig -> compact-JSON entry or ""
  printf '%s' "$1" | jq -c --arg k "$2" '(.data[$k] // "") | if . == "" then empty else (fromjson? // {}) end' 2>/dev/null
}

# ── state WRITE (triage owns class/attempted/result; create OR replace via the in-namespace
#    configmaps Role). last_paged + first_seen are PRESERVED so the gate's page de-dupe holds. ──
state_upsert() {  # <sig> <class> <attempted> <result> ; RETURNS the write rc (0=persisted).
  # The caller MUST check the rc when it is CLAIMING an attempt (cost safety): if the
  # attempted=1 write did not land, the attempt is not durably recorded, so summoning
  # would risk a re-spend next cycle — the summon path fails CLOSED on a non-zero rc.
  local sig="$1" class="$2" att="$3" res="${4:-none}"
  [ -n "$res" ] || res=none
  local ttl rc
  ttl="$(( STATE_TTL_HOURS * 3600 ))"
  if kubectl -n "$STATE_NS" get configmap "$STATE_CM" >/dev/null 2>&1; then
    kubectl -n "$STATE_NS" get configmap "$STATE_CM" -o json 2>/dev/null \
      | jq --arg k "$sig" --arg class "$class" --arg att "$att" --arg res "$res" \
           --argjson now "$NOW" --argjson ttl "$ttl" '
          .data = (.data // {})
          | ((.data[$k] // "{}") | (fromjson? // {})) as $e
          | .data[$k] = (($e + {class:$class, attempted:$att, result:$res,
                first_seen:($e.first_seen // ($now|tostring)),
                last_paged:($e.last_paged // "0")}) | tojson)
          | .data |= with_entries(
                select(.key == $k
                       or ((((.value | (fromjson? // {}) | (.first_seen // "0") | tonumber?) // 0)) > ($now - $ttl))))
        ' 2>/dev/null \
      | kubectl -n "$STATE_NS" replace -f - >/dev/null 2>&1
    rc=${PIPESTATUS[2]:-1}
  else
    kubectl -n "$STATE_NS" create configmap "$STATE_CM" --dry-run=client -o json 2>/dev/null \
      | jq --arg k "$sig" --arg class "$class" --arg att "$att" --arg res "$res" --argjson now "$NOW" '
          .data = { ($k): (({class:$class, attempted:$att, result:$res,
                first_seen:($now|tostring), last_paged:"0"}) | tojson) }
        ' 2>/dev/null \
      | kubectl -n "$STATE_NS" create -f - >/dev/null 2>&1
    rc=${PIPESTATUS[2]:-1}
  fi
  if [ "${rc:-1}" -eq 0 ]; then
    log "state: upsert sig=$sig class=$class attempted=$att result=$res (+pruned >${STATE_TTL_HOURS}h)"
  else
    log "state: WARN upsert sig=$sig failed rc=$rc (RBAC/conflict?) — a CLAIM caller fails CLOSED; the gate then FAILS SAFE to paging"
  fi
  return "${rc:-1}"
}

# state_prune — drop entries older than the TTL (approximates "regression no longer present"
# race-safely: a just-cleared signature lingers <= TTL, never pruned out from under a gate
# cycle mid-read). Cheap; run every cycle.
state_prune() {
  kubectl -n "$STATE_NS" get configmap "$STATE_CM" >/dev/null 2>&1 || return 0
  local ttl out
  ttl="$(( STATE_TTL_HOURS * 3600 ))"
  out="$(kubectl -n "$STATE_NS" get configmap "$STATE_CM" -o json 2>/dev/null \
    | jq --argjson now "$NOW" --argjson ttl "$ttl" '
        .data = (.data // {})
        | .data |= with_entries(
              select((((.value | (fromjson? // {}) | (.first_seen // "0") | tonumber?) // 0) > ($now - $ttl))))
      ' 2>/dev/null)"
  [ -n "$out" ] || return 0
  printf '%s' "$out" | kubectl -n "$STATE_NS" replace -f - >/dev/null 2>&1 \
    && log "state: pruned entries older than ${STATE_TTL_HOURS}h" \
    || true
}

# ── ATTRIBUTION (deterministic, no LLM). class=upgrade iff a merge to main in the lookback
#    window touched a REGRESSING workload's own app path under kubernetes/**. High-confidence
#    tokens only (Flux release name; a pod's app.kubernetes.io/name + owner-derived name) to
#    bias toward the SAFE outcome (a false `nonupgrade` merely means the gate pages a Flux
#    failure with less context and never auto-remediates a pod; a false `upgrade` would waste
#    an LLM summon AND could wrongly suppress a page). FAIL-SAFE: any git error => nonupgrade.
#    ANY regressing id matching a merge path => upgrade (the Flux release name is the reliable
#    signal; a real upgrade regression almost always fails the HR too). ──
seg_match() {  # $1=token (a whole path segment) $2=slash-wrapped path list ; rc0 if present
  local tok="$1"
  [ -n "$tok" ] || return 1
  case "$tok" in *[!a-zA-Z0-9._-]*) return 1;; esac
  printf '%s' "$2" | grep -qF "/$tok/"
}

pod_tokens() {  # $1=ns $2=pod -> newline app tokens (label + owner-derived)
  local ns="$1" pod="$2" lbl ownerkind owner
  lbl="$(kubectl get pod -n "$ns" "$pod" -o jsonpath='{.metadata.labels.app\.kubernetes\.io/name}' 2>/dev/null)"
  [ -n "$lbl" ] && printf '%s\n' "$lbl"
  ownerkind="$(kubectl get pod -n "$ns" "$pod" -o jsonpath='{.metadata.ownerReferences[0].kind}' 2>/dev/null)"
  owner="$(kubectl get pod -n "$ns" "$pod" -o jsonpath='{.metadata.ownerReferences[0].name}' 2>/dev/null)"
  if [ "$ownerkind" = "ReplicaSet" ] && [ -n "$owner" ]; then
    printf '%s\n' "${owner%-*}"   # strip the RS template hash -> Deployment name
  elif [ -n "$owner" ]; then
    printf '%s\n' "$owner"
  fi
}

id_attributable() {  # $1=identifier $2=slash-wrapped path list ; rc0 if a merge touched it
  local id="$1" wrapped="$2" name ns pod tok
  case "${id%%/*}" in
    flux)  name="${id##*/}"; seg_match "$name" "$wrapped" && return 0; return 1 ;;
    pod)
      ns="$(printf '%s' "$id" | cut -d/ -f2)"
      pod="$(printf '%s' "$id" | cut -d/ -f3-)"
      for tok in $(pod_tokens "$ns" "$pod"); do
        seg_match "$tok" "$wrapped" && return 0
      done
      return 1 ;;
    *)     return 1 ;;
  esac
}

attribute() {  # $1=sig (log only) $2=REG_IDS -> "upgrade" | "nonupgrade"
  local ids="$2" dir paths wrapped id
  dir="$(mktemp -d 2>/dev/null || echo "/tmp/triage-attrib.$$")"
  # Shallow, bounded (depth=50) clone JUST for path attribution. FAIL-SAFE: any failure
  # => nonupgrade (never summon on a guess; the gate still catches a Flux failure).
  if ! git clone --depth 50 --quiet "https://x-access-token:${GH_TOKEN}@github.com/${REPO}.git" "$dir" >/dev/null 2>&1; then
    log "attribution: clone failed — FAIL-SAFE to nonupgrade."
    rm -rf "$dir" 2>/dev/null
    printf 'nonupgrade'
    return 0
  fi
  paths="$(git -C "$dir" log --since="$SINCE" --name-only --pretty=format: 2>/dev/null | sed '/^$/d' | LC_ALL=C sort -u)"
  rm -rf "$dir" 2>/dev/null
  if [ -z "$paths" ]; then
    log "attribution: no changed paths in the lookback window — nonupgrade."
    printf 'nonupgrade'
    return 0
  fi
  wrapped="$(printf '%s\n' "$paths" | sed 's#^#/#; s#$#/#')"
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    if id_attributable "$id" "$wrapped"; then
      log "attribution: '$id' <- a recent merge touched its app path => class=upgrade."
      printf 'upgrade'
      return 0
    fi
    log "attribution: '$id' has NO matching recent merge path."
  done <<EOF
$ids
EOF
  log "attribution: no regressing workload matches a recent merge path => class=nonupgrade."
  printf 'nonupgrade'
}

# ── 1. Detect the current regression set (deterministic; SAME logic the gate runs). ──
collect_regressions
[ "$SIG_PODS_STATUS" = "ok" ] || log "WARN: persisted-pods query failed (parse/timeout) — pods dimension blind this run."
if [ -z "$REG_IDS" ]; then
  log "no active regression — pruning stale coordination state. exit."
  state_prune
  exit 0
fi
SIG="$(sig_of "$REG_IDS")"
REG_SUMMARY="$(printf '%s' "$REG_IDS" | tr '\n' ' ')"
log "active regression sig=$SIG: ${REG_SUMMARY}"

# ── 2. Read state (fail-closed aware) + attribute the regression. ──
state_read
entry=""
[ "$STATE_READ_STATUS" = "ok" ] && entry="$(entry_of "$STATE_JSON" "$SIG")"
prev_attempted="$(jget "$entry" attempted)"

recent="$(curl -sf -H "Authorization: Bearer ${GH_TOKEN}" -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/${REPO}/commits?sha=main&since=${SINCE}&per_page=10" 2>/dev/null | jq 'length' 2>/dev/null)"
if [ "${recent:-0}" -gt 0 ] 2>/dev/null; then
  log "detected ${recent} commit(s) on main since ${SINCE} — attributing."
  CLASS="$(attribute "$SIG" "$REG_IDS")"
else
  log "no merge to main in the last ${LOOKBACK_HOURS}h — regression is NOT upgrade-attributable."
  CLASS="nonupgrade"
fi

# ── 3. Act on the classification (COST first). ──
if [ "$CLASS" = "upgrade" ]; then
  if [ "$prev_attempted" = "1" ]; then
    log "sig=$SIG upgrade-attributable but remediate was ALREADY attempted — NOT re-summoning (at most once, ever); the gate escalates."
    state_upsert "$SIG" upgrade 1 "$(jget "$entry" result)"
    exit 0
  fi
  if [ "$STATE_READ_STATUS" = "error" ]; then
    # UNVERIFIABLE prior state => cannot rule out a prior attempt => FAIL CLOSED (rule 2).
    # Do NOT summon (protect spend) and do NOT write (the write would fail too). The gate
    # fails SAFE and pages this regression, so a human handles it.
    log "sig=$SIG upgrade-attributable but the state CM is UNREADABLE — cannot confirm no prior attempt; FAIL CLOSED, NOT summoning (protect spend). The gate pages."
    exit 0
  fi
  log "sig=$SIG upgrade-attributable, FIRST attempt — claiming the attempt before summoning."
  # Rule 1 (crash-proof at-most-once): claim the attempt BEFORE invoking. A crash/timeout/
  # pod-death mid-run then still counts as attempted and is NEVER retried. result=none keeps
  # the gate suppressing (within the grace window) while remediate is in flight.
  # COST FAIL-CLOSED: if the claim write does NOT land (RBAC glitch, or a resourceVersion
  # race with the gate's last_paged write), we cannot guarantee at-most-once — so do NOT
  # summon (protect spend). The gate fails safe and pages, so a human still handles it.
  if ! state_upsert "$SIG" upgrade 1 none; then
    log "sig=$SIG: could NOT durably claim the attempt — FAIL CLOSED, NOT summoning (protect spend). The gate pages."
    exit 0
  fi
  log "sig=$SIG: attempt claimed — summoning MODE=remediate."
  prs_before="$(gh pr list -R "$REPO" --state open --json number --jq '[.[].number]|sort' 2>/dev/null || echo '[]')"
  export UPGRADE_AGENT_MODE=remediate
  export UPGRADE_AGENT_PROMPT="A regression appeared after a merge to main within the last ${LOOKBACK_HOURS}h. Detected signals: ${REG_SUMMARY}. Diagnose (read-only) and remediate per .agents/runbooks/upgrade-shepherd.md Mode 2 — forward-fix or git revert/re-pin the culprit bump and enable auto-merge. BAIL EARLY (within a few turns) with a single line 'BREAK-GLASS: <reason>' if the regression is NOT clearly caused by a recent upgrade you can fix via git — immutable field, wedged HelmRelease, stuck finalizer, one-way major, or an infra/KubePrism/etcd/node fault. Do NOT investigate to max-turns, do NOT retry denied cluster writes, do NOT push to main; stay inside kubernetes/**."
  # NOT `exec`: we must regain control to record the outcome.
  /bin/bash /opt/shepherd/run-shepherd.sh
  rc=$?
  log "run-shepherd.sh (remediate) exited rc=$rc — re-checking the regression."
  prs_after="$(gh pr list -R "$REPO" --state open --json number --jq '[.[].number]|sort' 2>/dev/null || echo '[]')"
  new_pr="$(jq -n --argjson a "${prs_before:-[]}" --argjson b "${prs_after:-[]}" '(($b - $a) | length)' 2>/dev/null || echo 0)"
  collect_regressions
  if [ -z "$REG_IDS" ]; then
    RESULT=fixed
    log "post-remediate: cluster healthy — result=fixed (silent; no page)."
  elif [ "${new_pr:-0}" -gt 0 ] 2>/dev/null; then
    RESULT=pending
    log "post-remediate: still present but remediate opened ${new_pr} new PR(s) — result=pending (Flux applies within ~30m; the gate suppresses through the grace window, then pages if it did not converge)."
  else
    RESULT=failed
    log "post-remediate: still present and NO new PR (break-glass / no-op) — result=failed (the gate pages)."
  fi
  state_upsert "$SIG" upgrade 1 "$RESULT"
  exit 0
fi

log "sig=$SIG is NOT upgrade-attributable (class=nonupgrade) — NOT summoning. The gate pages a Flux deploy failure (sole catcher) but stays silent for a non-upgrade pod crashloop (Alertmanager covers it)."
state_upsert "$SIG" nonupgrade 0 none
exit 0
