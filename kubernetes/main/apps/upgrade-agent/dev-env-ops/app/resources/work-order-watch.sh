#!/usr/bin/env bash
# work-order-watch.sh — dev-env-ops PID 1 (saga dev-env backlog 13 + 12's esc lane).
#
# Polls the `upgrade-work-orders` ConfigMap and turns pending entries into
# LONG-LIVED, JOINABLE claude sessions in tmux windows (`claude --remote-control`).
#
# NAMING TAXONOMY (Tom's requirement — the Remote Control list must sort cleanly;
# enforced here and in the writers; anything else is marked failed, never spawned).
# The prefix decides EVERYTHING: who runs it, and whether Tom ever hears about it.
#   haynes-ops-*  dev work in the dev-env pod (agent-run's convention; not ours)
#   wo-*          shepherd WORK ORDERS (work-order.sh) — INTERACTIVE + Remote
#                 Control, QUIET on success, model default opus (scheduled
#                 consumers stay on the cheaper tier)
#   esc-*         FAILURE ESCALATIONS (escalate.sh: esc-<source>-<sig8>) —
#                 INTERACTIVE + Remote Control, PAGE ON SPAWN (they ARE failures;
#                 the page carries the session name so Tom can join)
#   rem-*         AUTONOMOUS REMEDIATION (remediate.sh: rem-<source>-<sig8>) —
#                 HEADLESS (`claude -p`), NO Remote Control registration, NO page,
#                 NO entry in Tom's session list. It fixes and closes silently, or
#                 promotes itself into the esc-* lane. This is the "autonomy
#                 first" policy of 2026-08-23: only sessions that WANT Tom become
#                 sessions he can see.
# For wo-*/esc-* the tmux window name AND the --remote-control name are the CM
# key; for rem-* only the tmux window is named (there is no registration).
#
# LANES: each lane is SINGLE-FLIGHT (at most one active wo-*, one esc-* and one
# rem-* session; oldest pending first). "Active" is a live SESSION, not a live
# WINDOW — a finished session's window is kept on purpose (see CLEANUP
# LIFECYCLE) and must not pin its lane; lane_active() settles that from the
# order's status. wo single-flight is the shepherd's drain
# rule (cluster-infra moves one unit at a time); esc single-flight serializes
# same-root-cause escalations from different writers into one open session at a
# time — and an escalation is never stuck behind a long-running upgrade session.
# rem single-flight keeps two autonomous actuators off the cluster at once, which
# matters far more for a lane that CHANGES things than for one that talks.
#
# Order entry (JSON string, single-encoded, CM-legal key charset only):
#   {pr?, repo?, title?, source?, run_ref?, class, reason, diagnosis?, alert?,
#    sig?, model?, effort?, status, created, updated, note?, digested?}
# Status flow: pending → claimed (watcher) → done|failed|escalated (the session,
# via order-status.sh; `working` is a non-terminal heartbeat that only bumps
# `updated`). Re-queue = set status back to pending.
#
# A claimed order is closed out by its own session, or by one of three
# watchdogs — one per way a session can stop serving:
#   died, window gone   → the orphan sweep below (claimed, no window, >30min)
#   exited, never told  → session-launch.sh's lane release (#2974)
#   HUNG, never exits   → wo_watchdog below (#2976), wo-* only, OPS_WO_STALE_MINUTES
#
# CLEANUP LIFECYCLE: finished windows are reaped (done >24h — the transcript
# stays joinable for a day; failed/escalated >7d — it IS the joinable
# post-mortem), and the reap also removes the session's artifacts:
# ~/work/orders/<key>.json, <key>.log and the ~/work/<key> worktree/dir (sessions
# MUST name their worktree after their key — ops-claude.md contract). BOUND
# TOTALS: >OPS_REAP_MAX_FINISHED finished orders still holding windows reap
# oldest-first regardless of TTL, so a runaway escalation storm cannot exhaust
# tmux/PVC. ENTRY RETENTION: terminal orders that have been digested are deleted
# from the CM after OPS_ORDER_RETENTION_DAYS — the rem-* lane files an order per
# urgent alert, so unbounded entry growth is a real ceiling, not a hypothetical.
# Manual sweep: ops-reap.sh.
# Remote Control registrations die with their process, so killing the tmux
# window is the whole dereg story (verified live 2026-08-23 — see the runbook).
set -uo pipefail
CM="${WORK_ORDER_CM:-upgrade-work-orders}"
NS="${WORK_ORDER_NS:-upgrade-agent}"
POLL="${WORK_ORDER_POLL_SECONDS:-60}"
MODEL_DEFAULT="${OPS_SESSION_MODEL:-claude-opus-5}"              # wo-* lane
ESC_MODEL_DEFAULT="${OPS_ESC_MODEL:-claude-opus-5}"     # esc-* lane
REM_MODEL_DEFAULT="${OPS_REM_MODEL:-claude-opus-5}"     # rem-* lane
EFFORT_DEFAULT="${OPS_SESSION_EFFORT:-xhigh}"           # all lanes
REAP_MAX="${OPS_REAP_MAX_FINISHED:-6}"
WO_STALE_MIN="${OPS_WO_STALE_MINUTES:-180}"             # wo-* no-heartbeat deadline
RETENTION_DAYS="${OPS_ORDER_RETENTION_DAYS:-14}"
DIGEST_INTERVAL_H="${OPS_DIGEST_INTERVAL_H:-24}"
DIGEST_MAX_PENDING="${OPS_DIGEST_MAX_PENDING:-8}"
BUSY_LOG_INTERVAL="${OPS_BUSY_LOG_INTERVAL:-1800}"      # per-lane "still busy" log rate limit
ORDERS_DIR="${HOME}/work/orders"
REPO_CANON="${HOME}/repos/haynes-ops"

log() { printf 'wo-watch: %s %s\n' "$(date -u +%FT%TZ)" "$*"; }
oplog() { bash /opt/dev-env-ops/ops-log.sh "$@" 2>/dev/null || true; }

page() {  # $1=title $2=message — failures AND escalation spawns (both are failures).
  curl -sf --max-time 10 https://api.pushover.net/1/messages.json \
    --form-string "token=${PUSHOVER_TOKEN:-}" \
    --form-string "user=${PUSHOVER_USER_KEY:-}" \
    --form-string "title=[dev-env-ops] $1" \
    --form-string "message=$2" \
    --form-string "priority=0" >/dev/null \
    && log "paged: $1" || log "PAGE FAILED: $1"
}

update_status() {  # $1=key $2=status $3=note ; rc!=0 = did not durably land.
  kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null \
    | jq --arg k "$1" --arg s "$2" --arg n "${3:-}" --arg now "$(date -u +%s)" '
        .data[$k] = ((.data[$k] | fromjson? // {}) | .status=$s | .note=$n | .updated=$now | tojson)' 2>/dev/null \
    | kubectl -n "$NS" replace -f - >/dev/null 2>&1
}

win_exists()  { tmux list-windows -t ops -F '#W' 2>/dev/null | grep -qx "$1"; }

# lane_active <lane> [cm-data-json] — rc0 when the lane holds a window that is
# still WORKING. NOT "a window exists": windows deliberately outlive their
# sessions (session-launch.sh ends in `exec bash` so the transcript stays
# joinable, and the reap in step 2 keeps a done window 24h / a failed or
# escalated one 7d), so a window is only evidence of a live session while its
# ORDER is non-terminal. The order's status in the CM is the authority.
#
# WHY (2026-09-19): `wo-cigar-curate-20260917` finished, marked its order done,
# and sat idle at its prompt. The plain window test counted it, so the wo lane
# read busy for the whole 24h reap TTL and the next two DAILY curate orders
# (-20260918, -20260919) were skipped without a word — the lane only recovered
# when the window was killed by hand. Same stall on 2026-09-06 with a window
# whose session had DIED: nothing closes a `claimed` order whose window still
# exists, so that one would have pinned the lane until the pod restarted (the
# other half of this fix is the close-out in session-launch.sh).
#
# FAIL-SAFE: anything we cannot positively read as terminal counts as ACTIVE —
# an unreadable CM this poll ({}), a key removed by retention, pending, claimed,
# or a status we do not recognise. Never double-spawn a lane on a guess.
# Sets $LANE_HOLDER to the window that held the lane (and why), for the skip log.
lane_active() {  # $1=wo|esc|rem  $2=CM .data snapshot (defaults to this poll's $data)
  local lane="$1" snap="${2-${data-}}" w st
  LANE_HOLDER=""
  [ -n "$snap" ] || snap='{}'
  for w in $(tmux list-windows -t ops -F '#W' 2>/dev/null | grep "^${lane}-"); do
    st="$(printf '%s' "$snap" | jq -r --arg k "$w" \
      '(.[$k] // "") | (fromjson? // {}) | .status // "unknown"' 2>/dev/null)"
    case "$st" in
      done|failed|escalated) ;;   # post-mortem artifact awaiting the reap — not a session
      *) LANE_HOLDER="$w ($st)"   # pending/claimed/unknown — busy, or unverifiable
         return 0 ;;
    esac
  done
  return 1
}

# wo_stale <cm-data-json> <now-epoch> — the wo-* orders that are `claimed` and
# have not been heard from for WO_STALE_MIN minutes. One key per line; pure (it
# reads nothing and changes nothing), so the selftest can drive it directly.
#
# WHY (#2976, the shape #2974 left behind): #2974 closed out a session that
# EXITS without reporting (session-launch.sh's lane release) and one whose
# window is GONE (the orphan sweep below). Neither sees a session that HANGS —
# wedged on a 529 retry loop, a login prompt, a tool call that never returns.
# Its order stays `claimed` and its window stays live, so lane_active() reads
# the wo lane busy forever and every later order sits `pending` in silence:
# the 2026-09-17 stall again, but with a session that never even finishes.
#
# SCOPE — wo-* ONLY, deliberately:
#   esc-*  EXEMPT. An escalation session idling for hours while it waits on a
#          human IS its normal state, and failing it out would discard the
#          joinable session Tom was paged with.
#   rem-*  already covered by respond.sh's rem_watchdog (claimed >
#          REM_RUNNING_DEADLINE_MIN → failed + escalate). Two watchdogs on one
#          lane would race for the same order.
#
# DEADLINE. A session refreshes `updated` with `order-status.sh <key> working`
# (ops-claude.md's heartbeat), so a healthy long job keeps itself alive. The
# curation playbook bounds a batch at ~45min and observed runs are 12-70min, so
# the 180min default is wide margin rather than a race. Tune with
# OPS_WO_STALE_MINUTES (HelmRelease env) — never below the longest legitimate
# silent stretch of a wo-* order.
#
# FAIL-SAFE, and it points one way: do nothing unless the order is positively
# readable as claimed AND its `updated` parses as a number older than the
# deadline. An unreadable CM this poll ({}), an entry whose JSON does not parse,
# a missing or non-numeric `updated`, pending, a fresh heartbeat, or any
# terminal status yields NOTHING. Holding a lane one more poll is cheap; failing
# a live session's order out from under it is not.
wo_stale() {
  local snap="${1-}" now="${2-0}"
  [ -n "$snap" ] || snap='{}'
  printf '%s' "$snap" | jq -r --argjson now "${now:-0}" \
      --argjson max "$(( ${WO_STALE_MIN:-180} * 60 ))" '
    to_entries[]
    | select(.key | startswith("wo-"))
    | select((.value | fromjson? // {}) as $o
        | ($o.status? // "") == "claimed"
          and ((($o.updated? // "") | tonumber?) // 0) > 0
          and ((($o.updated? // "") | tonumber?) // 0) < ($now - $max))
    | .key' 2>/dev/null
}

# wo_watchdog <cm-data-json> <now-epoch> — act on wo_stale's verdict.
#
# ACTS EXACTLY ONCE per order: `failed` is terminal, so the selector can never
# match that key again (the same one-shot property the rem watchdog relies on).
#
# It does NOT kill the window. Since #2974 a terminal order no longer holds its
# lane, so marking the order `failed` is the whole fix: the lane frees on this
# poll and the transcript stays joinable for the existing 7d post-mortem reap —
# which is exactly what someone debugging a wedged session wants. Killing a
# process we cannot prove is dead stays the one thing this must not do.
#
# Routed through order-status.sh so the wo-* `failed` policy lives in ONE place:
# it pages once, which is what a lane held for three hours is worth. A window
# that is GONE is not ours — that is the orphan sweep's 30min case, and skipping
# it here is what keeps one dead session from producing two pages in one poll.
wo_watchdog() {
  local key
  for key in $(wo_stale "$1" "$2"); do
    win_exists "$key" || continue
    log "wo-watchdog: $key has been claimed with no heartbeat for >${WO_STALE_MIN}min — presumed hung; marking failed so the wo lane frees (window left open for the post-mortem)."
    oplog watchdog "$key" what=wo-stale deadline_min="$WO_STALE_MIN"
    bash /opt/dev-env-ops/order-status.sh "$key" failed \
      "stale-lane watchdog: no heartbeat for ${WO_STALE_MIN}m — session presumed hung; window left open for post-mortem. It never exited, so nothing else would ever close this order and the wo lane was held. Treat the work as INCOMPLETE and re-queue the order (set it back to pending) if it still matters; join tmux '${key}' on the dev-env-ops pod to see where it wedged." \
      >/dev/null 2>&1 \
      || log "wo-watchdog: could NOT close out $key — retries next poll."
  done
}

# clean_artifacts <key> — the session's on-PVC leftovers: the order JSON, the
# headless transcript, and the ~/work/<key> worktree/dir. Idempotent and quiet
# when there is nothing to clean.
clean_artifacts() {
  local key="$1" cleaned=""
  [ -f "$ORDERS_DIR/$key.json" ] && rm -f "$ORDERS_DIR/$key.json" 2>/dev/null && cleaned="order-json"
  [ -f "$ORDERS_DIR/$key.log" ] && rm -f "$ORDERS_DIR/$key.log" 2>/dev/null && cleaned="$cleaned transcript"
  if [ -d "$HOME/work/$key" ]; then
    if [ -e "$HOME/work/$key/.git" ] \
       && git -C "$REPO_CANON" worktree remove --force "$HOME/work/$key" >/dev/null 2>&1; then
      cleaned="$cleaned worktree"
    else
      rm -rf "$HOME/work/$key" 2>/dev/null && cleaned="$cleaned dir"
    fi
    git -C "$REPO_CANON" worktree prune >/dev/null 2>&1
  fi
  [ -n "$cleaned" ] && log "cleaned artifacts for $key: $cleaned"
}

reap() {  # $1=key $2=why — kill the window (if any) + artifacts.
  win_exists "$1" && tmux kill-window -t "ops:$1" 2>/dev/null && log "reaped window $1 ($2)"
  clean_artifacts "$1"
  oplog reaped "$1" why="$2"
}

spawn_session() {  # $1=key $2=order-json-string
  local key="$1" order="$2" lane=wo model effort src reason
  case "$key" in esc-*) lane=esc ;; rem-*) lane=rem ;; esac
  # Refresh the canonical clone before every session. ops-init.sh fetches at boot,
  # but this pod runs for days — a boot-only fetch still hands day-3 sessions a
  # day-0 base. Sessions read .renovate/holds.json5 and .agents/runbooks/ to decide
  # what they are allowed to merge, so a stale base is a correctness problem, not a
  # convenience one. Non-fatal: a session that starts from a slightly older base is
  # far better than no session at all when GitHub is briefly unreachable.
  git -C "$REPO_CANON" fetch --prune origin main >/dev/null 2>&1 \
    || log "warn: pre-spawn fetch failed for $key — base may be stale"
  mkdir -p "$ORDERS_DIR"
  printf '%s' "$order" > "$ORDERS_DIR/$key.json"
  # Per-order model/effort override, else lane defaults. Sanitized: these land
  # inside a tmux run-command string and order fields are writer-supplied data.
  model="$(printf '%s' "$order" | jq -r '.model // empty' 2>/dev/null | tr -cd 'a-zA-Z0-9._-')"
  if [ -z "$model" ]; then
    case "$lane" in
      esc) model="$ESC_MODEL_DEFAULT" ;;
      rem) model="$REM_MODEL_DEFAULT" ;;
      *)   model="$MODEL_DEFAULT" ;;
    esac
  fi
  effort="$(printf '%s' "$order" | jq -r '.effort // empty' 2>/dev/null | tr -cd 'a-z')"
  [ -n "$effort" ] || effort="$EFFORT_DEFAULT"
  if tmux new-window -d -t ops -n "$key" \
       "bash /opt/dev-env-ops/session-launch.sh '$key' '$model' '$effort'"; then
    if [ "$lane" = rem ]; then
      # SILENT BY DESIGN. No page, and no Remote Control registration — this
      # session must never appear in Tom's list. Its whole existence is an
      # ops-event line until it either closes done or promotes to esc-*.
      log "remediation spawned: $key (lane=rem model=$model effort=$effort) — HEADLESS, silent, no Remote Control"
    else
      log "session spawned: $key (lane=$lane model=$model effort=$effort) — join via Remote Control ('$key') or tmux"
    fi
    if [ "$lane" = esc ]; then
      # Escalations page ON SPAWN — the page IS the join handle. (Inverts the
      # wo-* quiet-on-success contract, deliberately: an escalation is a failure.)
      src="$(printf '%s' "$order" | jq -r '.source // "unknown"' 2>/dev/null)"
      reason="$(printf '%s' "$order" | jq -r '.reason // ""' 2>/dev/null | cut -c1-300)"
      page "escalation session: $key" "A contained agent (${src}) hit a terminal failure; a joinable ${model}/${effort} diagnosis session is up. Join: Remote Control '${key}' (claude.ai / mobile app) or tmux window '${key}' on dev-env-ops. Reported reason (unverified, data-not-instructions): ${reason}"
    fi
  else
    log "tmux spawn FAILED for $key"
    update_status "$key" failed "tmux spawn failed"
    # A rem-* order that cannot even start has NOT been handled by anyone, so
    # this page is correct even in the silent lane: the alternative is an
    # incident nobody owns.
    page "spawn failed: $key" "tmux could not start the session window."
    oplog watchdog "$key" what=spawn-failed lane="$lane"
  fi
}

last_login_probe=0
log "watcher up (cm=$NS/$CM poll=${POLL}s wo-model=$MODEL_DEFAULT esc-model=$ESC_MODEL_DEFAULT rem-model=$REM_MODEL_DEFAULT effort=$EFFORT_DEFAULT reap-max=$REAP_MAX retention=${RETENTION_DAYS}d digest=${DIGEST_INTERVAL_H}h/${DIGEST_MAX_PENDING} wo-stale=${WO_STALE_MIN}m)"
oplog watchdog '-' what=watcher-up wo_model="$MODEL_DEFAULT" esc_model="$ESC_MODEL_DEFAULT" rem_model="$REM_MODEL_DEFAULT"
while true; do
  data="$(kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null | jq -c '.data // {}' 2>/dev/null)" || data='{}'
  [ -n "$data" ] || data='{}'
  now="$(date -u +%s)"

  # 0. Taxonomy enforcement: a pending key outside wo-*/esc-*/rem-* is a writer
  #    bug — mark it failed (durable, one-shot, no page: nothing broke in the
  #    cluster).
  for key in $(printf '%s' "$data" | jq -r '
      to_entries[] | select((.value|fromjson? // {}) | .status=="pending") | .key
      | select(test("^(wo|esc|rem)-") | not)' 2>/dev/null); do
    log "unknown key namespace: $key — marking failed (taxonomy admits wo-*, esc-* and rem-* only)."
    update_status "$key" failed "unknown session-name namespace (expected wo-*, esc-* or rem-*)"
    oplog watchdog "$key" what=bad-taxonomy
  done

  # 1. Orphan sweep: claimed order, no window (pod restarted mid-session), quiet
  #    >30min → failed + page. The transcript is gone; a human re-queues if wanted.
  for key in $(printf '%s' "$data" | jq -r --argjson now "$now" '
      to_entries[] | select((.value|fromjson? // {}) as $o
        | $o.status=="claimed" and ((($o.updated // "0")|tonumber? // 0) < ($now - 1800)))
      | .key' 2>/dev/null); do
    if ! win_exists "$key"; then
      case "$key" in
        rem-*)
          # A dead headless remediation leaves NOTHING to join, so paging alone
          # would point Tom at a session that does not exist. Promote it: the
          # esc-* lane spawns a real joinable session with the context.
          log "orphaned remediation $key (no window — pod restart?) — escalating."
          oplog watchdog "$key" what=orphaned-rem
          bash /opt/dev-env-ops/order-status.sh "$key" escalate \
            "The autonomous remediation session vanished before reporting (likely a dev-env-ops pod restart). The condition was NEVER verified as fixed — re-check it from scratch." \
            >/dev/null 2>&1 || update_status "$key" failed "session window lost and escalation failed"
          ;;
        *)
          log "orphaned claim $key (no window — pod restart?) — marking failed."
          update_status "$key" failed "session window lost (pod restart?)"
          oplog watchdog "$key" what=orphaned-session
          page "session lost: $key" "The session vanished (likely a pod restart) before completing. Re-queue by setting the order back to pending, or handle by hand."
          ;;
      esac
    fi
  done

  # 1b. HUNG-SESSION WATCHDOG (#2976): a wo-* session that never exits holds its
  #     lane forever — the orphan sweep needs the window GONE and the reap only
  #     touches terminal orders, so nothing above can see it. Runs before the
  #     spawn step so a freed lane is used on this same poll. wo-* only: esc-*
  #     idling on a human is normal, rem-* has respond.sh's rem_watchdog.
  wo_watchdog "$data" "$now"

  # 2. Reap finished sessions: done >24h keeps the transcript joinable for a day;
  #    failed/escalated stay 7d (they ARE the joinable post-mortem). Reaping also
  #    cleans the session's PVC artifacts (idempotent — safe for entries whose
  #    window is already gone but whose worktree/order JSON lingers).
  #
  #    The entry SURVIVES this reap — it is only removed by 2c's retention sweep,
  #    up to RETENTION_DAYS later. So this selector re-matched the same terminal
  #    key on EVERY poll for its entire zombie window. Reaping is idempotent, so
  #    nothing was corrupted, but two things were not free:
  #      - `oplog reaped` fired once per 60s per terminal order (measured
  #        2026-08-23: 152 `reaped` events vs 16 real ones in 24h — 90.5% noise),
  #        which buries genuine events in the ops log and in the digest.
  #      - clean_artifacts() re-ran every poll, so a worktree deliberately
  #        recreated under ~/work/<key> to inspect a post-mortem was silently
  #        deleted within 60s.
  #    Fixed by stamping `reaped` on the entry and skipping already-stamped ones.
  #    Consumers read named fields with // defaults, and the status-update path
  #    round-trips unknown keys, so the extra field is inert everywhere else.
  ttl_reaped=""
  for key in $(printf '%s' "$data" | jq -r --argjson now "$now" '
      to_entries[] | select((.value|fromjson? // {}) as $o
        | (($o.reaped // "") == "")
        and (($o.status=="done"   and ((($o.updated // "0")|tonumber? // 0) < ($now - 86400)))
        or (($o.status=="failed" or $o.status=="escalated")
            and ((($o.updated // "0")|tonumber? // 0) < ($now - 604800)))))
      | .key' 2>/dev/null); do
    reap "$key" "ttl"
    ttl_reaped="$ttl_reaped $key"
  done
  if [ -n "$ttl_reaped" ]; then
    reaped_marks="$(printf '%s\n' $ttl_reaped | sed '/^$/d' | jq -R . | jq -sc .)"
    kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null \
      | jq --argjson keys "$reaped_marks" --arg now "$now" '
          reduce $keys[] as $k (.;
            if (.data[$k] // "") == "" then .
            else .data[$k] = (((.data[$k] | fromjson? // {}) + {reaped: $now}) | tojson) end)' \
      | kubectl -n "$NS" replace -f - >/dev/null 2>&1 \
      || log "warn: could not stamp reaped (retries next poll):$ttl_reaped"
  fi

  # 2b. BOUND TOTALS: if more than REAP_MAX finished orders still hold windows,
  #     reap oldest-first (by updated) regardless of TTL — a runaway escalation
  #     storm must not exhaust tmux/PVC.
  finished_live=""
  for key in $(printf '%s' "$data" | jq -r '
      to_entries | map(select((.value|fromjson? // {}) as $o
        | $o.status=="done" or $o.status=="failed" or $o.status=="escalated"))
      | sort_by((.value|fromjson? // {}) | ((.updated // "0")|tonumber? // 0))
      | .[].key' 2>/dev/null); do
    win_exists "$key" && finished_live="$finished_live $key"
  done
  live_n="$(printf '%s\n' $finished_live | sed '/^$/d' | wc -l)"
  if [ "${live_n:-0}" -gt "$REAP_MAX" ] 2>/dev/null; then
    for key in $finished_live; do
      [ "$live_n" -le "$REAP_MAX" ] && break
      reap "$key" "bound: $live_n finished windows > max $REAP_MAX"
      live_n=$((live_n - 1))
    done
  fi

  # 2c. ENTRY RETENTION: delete terminal, already-digested entries older than
  #     RETENTION_DAYS. The rem-* lane files an order per urgent alert, so the CM
  #     would otherwise grow without bound (ConfigMaps cap at ~1MiB — a full CM
  #     fails EVERY subsequent write, including escalations). Digested-only, so a
  #     summary is never lost to retention.
  stale_keys="$(printf '%s' "$data" | jq -r --argjson now "$now" --argjson keep "$(( RETENTION_DAYS * 86400 ))" '
      to_entries[] | select((.value|fromjson? // {}) as $o
        | ($o.status=="done" or $o.status=="failed" or $o.status=="escalated")
          and (($o.digested // "") != "")
          and ((($o.updated // "0")|tonumber? // 0) < ($now - $keep)))
      | .key' 2>/dev/null)"
  if [ -n "$stale_keys" ]; then
    for key in $stale_keys; do reap "$key" "retention"; done
    marks="$(printf '%s\n' $stale_keys | sed '/^$/d' | jq -R . | jq -sc .)"
    if kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null \
         | jq --argjson keys "$marks" 'reduce $keys[] as $k (.; del(.data[$k]))' \
         | kubectl -n "$NS" replace -f - >/dev/null 2>&1; then
      log "retention: removed $(printf '%s\n' $stale_keys | wc -l) CM entries older than ${RETENTION_DAYS}d"
    fi
  fi

  # 3. Spawn per lane (wo-*, esc-*, rem-*): oldest pending, only when that lane
  #    is not already running one (single-flight per lane — see lane_active).
  #    esc first so a human-needed escalation is never queued behind autonomous work.
  for lane in esc wo rem; do
    key="$(printf '%s' "$data" | jq -r --arg p "^${lane}-" '
        to_entries | map(select(.key | test($p))
                         | select((.value|fromjson? // {}) | .status=="pending"))
        | sort_by((.value|fromjson? // {}) | ((.created // "0")|tonumber? // 0))
        | .[0].key // empty' 2>/dev/null)"
    [ -n "$key" ] || continue
    if lane_active "$lane"; then
      # A skipped order used to be INVISIBLE: the 2026-09-17 stall pinned the wo
      # lane for a day before anyone noticed two curate orders had gone nowhere.
      # Rate-limited per lane so a legitimately busy one does not flood stdout.
      busy_var="last_busy_log_${lane}"
      if [ $(( now - ${!busy_var:-0} )) -ge "$BUSY_LOG_INTERVAL" ]; then
        printf -v "$busy_var" '%s' "$now"
        log "lane $lane busy — $key stays pending (held by $LANE_HOLDER)"
      fi
      continue
    fi
    order="$(printf '%s' "$data" | jq -r --arg k "$key" '.[$k]' 2>/dev/null)"
    oplog filed "$key" source="$(printf '%s' "$order" | jq -r '.source // "?"' 2>/dev/null)" class="$(printf '%s' "$order" | jq -r '.class // "?"' 2>/dev/null)"
    if update_status "$key" claimed "session spawning"; then
      oplog claimed "$key" lane="$lane"
      spawn_session "$key" "$order"
    else
      log "could NOT durably claim $key — leaving pending for next tick."
      oplog watchdog "$key" what=claim-failed
    fi
  done

  # 4. QUIET DIGEST: batch the outcomes Tom was deliberately NOT paged about.
  #    Flush when enough has accumulated, or once per interval — never per event
  #    (that would just be paging with extra steps). Computed from the data we
  #    already hold, so a quiet cluster costs zero extra API calls.
  undigested="$(printf '%s' "$data" | jq -r '
      [ to_entries[] | select((.value|fromjson? // {}) as $o
          | ($o.status=="done" or $o.status=="failed" or $o.status=="escalated")
            and (($o.digested // "") == "")) ] | length' 2>/dev/null)" || undigested=0
  if [ "${undigested:-0}" -gt 0 ] 2>/dev/null; then
    last_flush="$(printf '%s' "$data" | jq -r '(."digest.last" // "{}") | (fromjson? // {}) | .last_flush // "0"' 2>/dev/null)"
    if [ "${undigested}" -ge "$DIGEST_MAX_PENDING" ] 2>/dev/null \
       || [ $(( now - ${last_flush:-0} )) -ge $(( DIGEST_INTERVAL_H * 3600 )) ] 2>/dev/null; then
      log "digest: flushing ${undigested} undigested outcome(s)"
      bash /opt/dev-env-ops/ops-digest.sh flush || log "digest flush failed (entries stay pending)"
    fi
  fi

  # 5. LOGIN WATCH (2026-08-29): interactive lanes ride ~/.claude/.credentials.json
  #    because the plan token cannot register Remote Control (see session-launch.sh
  #    LANE AUTH). Nothing else on this pod watches that login, and its failure mode
  #    is the worst one: an esc-* page pointing at a session that cannot serve.
  #    Probe daily with the SAME auth the lanes launch with (both env credentials
  #    stripped — a valid API key must not mask a dead login). Page on auth failure
  #    only; rate-limit noise is expected on a busy plan and is NOT an expiry.
  if [ $(( now - last_login_probe )) -ge 86400 ]; then
    last_login_probe="$now"
    lp_out="$(timeout 120 env -u CLAUDE_CODE_OAUTH_TOKEN -u ANTHROPIC_API_KEY \
      claude -p 'ok' --model haiku --max-turns 1 2>&1)" || true
    if printf '%s' "$lp_out" | grep -qiE 'not logged in|/login|oauth|401|invalid.*(token|api key)'; then
      page "claude login expired (interactive lanes)" "The Max login (~/.claude/.credentials.json) failed its daily probe — wo-*/esc-* Remote-Control sessions cannot serve until it is re-authed. Ceremony: dev-env saga 04-auth.md, run against the dev-env-ops pod's tmux."
      oplog watchdog '-' what=login-expired
    else
      log "login probe ok (credentials.json serves interactive lanes)"
    fi
  fi

  sleep "$POLL"
done
