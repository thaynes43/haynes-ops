#!/usr/bin/env bash
# Tier-4 breaking-change shepherd — main-container entrypoint (phase 4b.1, summoned).
# Mounted from a ConfigMap so the tool allowlist + prompt are auditable in every PR
# diff (not hidden in an image layer).
#
# CONTAINMENT (defense-in-depth; the LLM is prompt-injectable — it reads release notes):
#   - Read-only k8s SA (get/list/watch; no secrets/exec/write) — verify only.
#   - Egress CiliumNetworkPolicy: GitHub + Anthropic + cluster-read ONLY.
#   - The bot PEM is NEVER here — an initContainer minted a short-lived ghs_ token to
#     /creds/gh_token; this container only sees that token (contents+PRs:write, 1h TTL).
#   - --permission-mode dontAsk + an explicit --allowedTools allowlist (auto-denies
#     everything else). `gh pr merge` only in auto/remediate (MERGE_TOOLS), NO
#     WebFetch/WebSearch, NO kubectl write.
#   - DRY-RUN by default: makes NO changes. Set UPGRADE_AGENT_MODE=shepherd to enable
#     edits + PR authorship.
#
# KNOWN 4b.1 GAP (closed by the 4b.2 guardrails): the token has contents:write, so a
# rogue/injected agent could in principle `git push` straight to main. 4b.1 is
# SUMMONED + SUPERVISED (a human triggers this Job and watches). Do NOT run it
# unattended until the Kyverno admission baseline + diff-scope check + push protection
# land. `gh pr merge` is blocked in the manual modes regardless.
set -uo pipefail

MODE="${UPGRADE_AGENT_MODE:-dryrun}"
# MODEL POLICY (2026-08-23, Tom): the AUTOMATED agents (shepherd, responder) always
# run the LATEST OPUS on the plan path. Pinned to an explicit id, NOT the `opus`
# alias: alias repoints LAG a launch by days (2026-08-06: `opus` still served 4.8
# while claude-opus-5 was live), and "latest" is the whole point here. Bump this on
# an Opus launch — probe first: `claude --model <id> -p 'reply with your model id'`.
# 2026-09-23: claude-opus-5-5 (Opus 5.5; floor claude-code 2.1.280 — an older CLI
# rejects the id outright, so the image bump (#3119) has to land before this does).
MODEL="${UPGRADE_AGENT_MODEL:-claude-opus-5-5}"
# METERED-PATH MODEL — the API key is pay-per-token, so it NEVER runs Opus or Fable
# (Tom's rule 2026-08-23: no Fable on API pricing; use Sonnet). Sonnet 5.5 is
# near-Opus quality at a fraction of the cost and can dispatch a pod claude-code
# agent for heavy lifting if a fallback run needs more muscle. 2026-09-28: Sonnet 5.5
# (was Sonnet 5), which needs claude-code 2.1.284 in the image (#3171).
FALLBACK_MODEL="${UPGRADE_AGENT_FALLBACK_MODEL:-claude-sonnet-5-5}"
# MAX_TURNS is a METERED-path (api) spend control only — a plan-served run costs $0,
# so capping its turns just strands finished work (2026-07-30: the cilium one-way vet
# authored PR #2310 then died at turn 40 doing post-PR diligence → Error pod, triage
# noise, no page). On the plan path the run is bounded by RUN_TIMEOUT instead; the
# api fallback re-applies the turn cap automatically (see run_claude).
MAX_TURNS="${UPGRADE_AGENT_MAX_TURNS:-40}"
MAX_BUDGET="${UPGRADE_AGENT_MAX_BUDGET_USD:-5.00}"
RUN_TIMEOUT="${UPGRADE_AGENT_TIMEOUT:-20m}"
REPO="thaynes43/haynes-ops"
WORKDIR="${HOME}/repo"

# ── Monthly spend guard (defense-in-depth; the Anthropic account balance is the HARD
# backstop) ── Tracks bot spend in a ConfigMap and REFUSES an UNATTENDED run (auto/
# remediate) once month-to-date + this run's cap would exceed MONTHLY_CAP. Manual modes
# (dryrun/shepherd) still record spend but are never blocked (a human chose to run them).
MONTHLY_CAP="${UPGRADE_AGENT_MONTHLY_CAP_USD:-50}"
SPEND_NS="${UPGRADE_AGENT_NAMESPACE:-upgrade-agent}"
SPEND_CM="${UPGRADE_AGENT_SPEND_CM:-upgrade-shepherd-spend}"
SPEND_MONTH=""; SPEND_PRIOR="0"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }
epoch_now() { date -u +%s; }
# Run start, in GitHub's timestamp format: the auto-merge stall fallback only touches
# auto-merges this bot enabled after this instant (i.e. during this run).
RUN_START_ISO="$(date -u +%FT%TZ)"

# Returns 0 = proceed, 1 = skip (cap reached). Fails OPEN on any kubectl/RBAC error
# (the account balance + per-run --max-budget-usd are the real ceilings).
spend_guard() {
  SPEND_MONTH="$(date -u +%Y-%m)"
  local cm; cm="$(kubectl -n "$SPEND_NS" get configmap "$SPEND_CM" -o json 2>/dev/null)" || {
    log "spend-guard: cannot read $SPEND_CM (proceeding; account balance is the backstop)"; return 0; }
  local m; m="$(printf '%s' "$cm" | jq -r '.data.month // ""')"
  SPEND_PRIOR="$(printf '%s' "$cm" | jq -r '.data.spent_usd // "0"')"
  [ "$m" = "$SPEND_MONTH" ] || SPEND_PRIOR="0"   # new month => reset
  if awk -v s="$SPEND_PRIOR" -v b="$MAX_BUDGET" -v c="$MONTHLY_CAP" 'BEGIN{exit !(s + b > c)}'; then
    log "spend-guard: month-to-date \$$SPEND_PRIOR + run cap \$$MAX_BUDGET would exceed \$$MONTHLY_CAP — SKIPPING."
    return 1
  fi
  log "spend-guard: month-to-date \$$SPEND_PRIOR / \$$MONTHLY_CAP — OK (run cap \$$MAX_BUDGET)."
  return 0
}

# Best-effort: add this run's actual cost (from claude's JSON) to the ConfigMap. Uses
# create-or-apply (the CM is runtime state, deliberately NOT in git — Flux would revert
# the counter to its seed value every reconcile).
record_spend() {
  local cost; cost="$(jq -r '.total_cost_usd // .cost_usd // 0' "$1" 2>/dev/null)"
  [ -n "$cost" ] && [ "$cost" != "null" ] || cost=0
  [ -n "$SPEND_MONTH" ] || SPEND_MONTH="$(date -u +%Y-%m)"
  local new; new="$(awk -v a="${SPEND_PRIOR:-0}" -v b="$cost" 'BEGIN{printf "%.4f", a + b}')"
  if kubectl -n "$SPEND_NS" create configmap "$SPEND_CM" \
       --from-literal=month="$SPEND_MONTH" --from-literal=spent_usd="$new" \
       --dry-run=client -o yaml | kubectl -n "$SPEND_NS" apply -f - >/dev/null 2>&1; then
    log "spend-guard: recorded run cost \$$cost; month-to-date now \$$new / \$$MONTHLY_CAP."
  else
    log "spend-guard: WARN could not record cost \$$cost (RBAC?)."
  fi
}

# ── RAMP MAP (2026-07-06 — the ONE ramp definition) ── UPGRADE_AGENT_RAMP (HR env) is
# a space-separated component list; this map turns each component into the repo path
# prefixes the pre-filter matches. WIDEN THE RAMP by adding the component name to
# UPGRADE_AGENT_RAMP and naming it in UPGRADE_AGENT_PROMPT (a NEW component also needs
# a mapping row here). The consistency check in the main flow fails a scheduled run
# CLOSED — and the gate pages — if list/map/prompt ever drift, so the old silent
# two-place-lockstep footgun (globs vs prompt) is now a paged, refused run instead.
ramp_globs_for() {
  case "$1" in
    coredns)        printf '%s' "kubernetes/main/apps/kube-system/coredns/" ;;
    traefik)        printf '%s' "kubernetes/main/apps/network/traefik/" ;;
    multus)         printf '%s' "kubernetes/main/apps/network/multus/" ;;
    # generic-device-plugin removed 2026-09-22 with the self-hosted Omni (its only
    # devic.es/tun consumer) — see .agents/reference/repo-overview.md.
    device-plugins) printf '%s' "kubernetes/main/apps/kube-system/intel-device-plugin/ kubernetes/main/apps/kube-system/nvidia-device-plugin/" ;;
    flux)           printf '%s' "kubernetes/main/flux/" ;;
    immich-major)   printf '%s' "kubernetes/main/apps/photos/immich/" ;;
    # ── Revertible cluster-infra (2026-07-08) — the prompt's REVERTIBLE class. The
    #    shepherd auto-merges PATCH/safe-minor bumps of these (data plane untouched,
    #    pins intact) behind the backup/health gate; MAJORS that are one-way (Ceph
    #    daemon major, PG major, cilium eBPF minor/major, authentik YYYY.M major) it
    #    authors + PAGES instead of auto-merging (see the prompt). rook moves as a UNIT
    #    (operator→csi-drivers→cluster).
    rook-ceph)      printf '%s' "kubernetes/main/apps/storage/rook-ceph/rook-ceph/app/ kubernetes/main/apps/storage/rook-ceph/rook-ceph/csi-drivers/ kubernetes/main/apps/storage/rook-ceph/rook-ceph/cluster/" ;;
    cnpg)           printf '%s' "kubernetes/main/apps/database/cloudnative-pg/" ;;
    dragonfly-operator) printf '%s' "kubernetes/main/apps/database/dragonfly/" ;;
    cilium)         printf '%s' "kubernetes/main/apps/kube-system/cilium/" ;;
    authentik)      printf '%s' "kubernetes/main/apps/network/authentik/" ;;
    cert-manager)   printf '%s' "kubernetes/main/apps/cert-manager/cert-manager/" ;;
    # plex (2026-07-19) — a media leaf, but shepherd-owned so it can run the KOMETA-IDLE
    # preflight before merging (a Plex restart mid-Kometa-run corrupts Kometa's library
    # write). Renovate carves plex out of its own auto-merge; the shepherd merges it only
    # when Kometa is idle. See the prompt's PLEX clause + the plex playbook entry.
    plex)           printf '%s' "kubernetes/main/apps/media/plex/" ;;
    *)              printf '' ;;
  esac
}
ramp_prompt_token() {  # the substring that must appear in the LLM prompt for this component
  case "$1" in
    immich-major)       printf 'immich' ;;
    rook-ceph)          printf 'rook' ;;
    dragonfly-operator) printf 'dragonfly' ;;
    *)                  printf '%s' "$1" ;;
  esac
}

# ── Orphan-PR report (deterministic, ~$0 — two gh calls) ── refreshed by every
# scheduled run BEFORE the pre-filter decision: open Renovate PRs older than
# ORPHAN_AGE_DAYS that nothing has merged are aging with no automation owner (stuck
# check, missed allowlist, dashboard-approval nobody ticked). Written to the
# upgrade-orphan-report ConfigMap; the GATE (the Pushover owner) pages a weekly digest
# and stamps last_paged/last_mismatch_paged back — preserve those fields here.
# $1 = ramp-mismatch detail ("" when consistent). Best-effort: failures only log.
write_orphan_report() {
  local mm="$1" days="${ORPHAN_AGE_DAYS:-7}" cutoff prs count=0 summary=""
  cutoff="$(date -u -d "-${days} days" +%FT%TZ 2>/dev/null)" || cutoff=""
  if [ -n "$cutoff" ]; then
    prs="$(gh pr list -R "$REPO" --state open --limit 200 --json number,title,author,createdAt 2>/dev/null)" || prs=""
    if [ -n "$prs" ]; then
      count="$(printf '%s' "$prs" | jq --arg c "$cutoff" \
        '[.[] | select((.author.login|test("renovate")) and (.createdAt < $c))] | length' 2>/dev/null)" || count=0
      summary="$(printf '%s' "$prs" | jq -r --arg c "$cutoff" \
        '[.[] | select((.author.login|test("renovate")) and (.createdAt < $c))] | sort_by(.number) | .[:6] | map("#\(.number) \(.title[:55])") | join(" | ")' 2>/dev/null)" || summary=""
    fi
  fi
  local existing lp lmp
  existing="$(kubectl -n "$SPEND_NS" get configmap upgrade-orphan-report -o json 2>/dev/null)"
  lp="$(printf '%s' "$existing" | jq -r '.data.last_paged // "0"' 2>/dev/null)"; [ -n "$lp" ] || lp="0"
  lmp="$(printf '%s' "$existing" | jq -r '.data.last_mismatch_paged // "0"' 2>/dev/null)"; [ -n "$lmp" ] || lmp="0"
  if kubectl -n "$SPEND_NS" create configmap upgrade-orphan-report \
       --from-literal=count="${count:-0}" --from-literal=summary="$summary" \
       --from-literal=ramp_mismatch="$mm" --from-literal=updated="$(date -u +%s)" \
       --from-literal=last_paged="$lp" --from-literal=last_mismatch_paged="$lmp" \
       --dry-run=client -o yaml | kubectl -n "$SPEND_NS" apply -f - >/dev/null 2>&1; then
    log "orphan-report: aging(>${days}d)=${count:-0} ramp_mismatch='${mm}'"
  else
    log "orphan-report: WARN could not write the ConfigMap (RBAC?)."
  fi
}

# ── Deterministic PRE-FILTER (Phase D cost control) ── On a SCHEDULED auto run, only
# summon the (paid) LLM when an open Renovate PR actually touches a component in the
# current auto-merge ramp AND is not already vetted at its current head SHA. On a quiet
# day (no in-scope unvetted PR) this exits $0 with ZERO LLM cost. It does NOT decide
# mergeability — the LLM still fully vets every in-scope PR (holds, release notes,
# supporting edits). The filter only decides IF there is work worth looking at, keeping
# the conservative posture: deterministic layer gates the look, LLM owns the judgement.
#
# The glob list is DERIVED from UPGRADE_AGENT_RAMP via ramp_globs_for above (an explicit
# UPGRADE_AGENT_PREFILTER_GLOBS still overrides for tests). A manual/targeted summon
# clears BOTH vars and is never filtered — it runs as asked. Fails OPEN (returns 0 =
# proceed to the LLM) on any gh/jq error — never silently skips real work.
#
# Returns 0 = an in-scope unvetted PR exists (or we're unsure) -> run the LLM.
# Returns 1 = definitively nothing to look at -> caller should exit $0.
prefilter_should_run() {
  local globs="$1" prs pr meta files oid g
  # -R "$REPO" is REQUIRED: the pre-filter runs BEFORE the clone, so gh has no git
  # remote to infer the repo from — a bare `gh pr list` errors "not a git repository"
  # and the filter fails open (runs the LLM) every time (bit us live 2026-07-04).
  prs="$(gh pr list -R "$REPO" --state open --limit 200 --json number,author \
           --jq '.[] | select(.author.login|test("renovate")) | .number' 2>/dev/null)" || {
    log "pre-filter: gh pr list failed — failing OPEN (running the LLM)."; return 0; }
  [ -n "$prs" ] || { log "pre-filter: no open Renovate PRs — skipping LLM (\$0)."; return 1; }
  local checked=0
  for pr in $prs; do
    meta="$(gh pr view "$pr" -R "$REPO" --json files,headRefOid,comments 2>/dev/null)" || {
      log "pre-filter: gh pr view #$pr failed — failing OPEN (running the LLM)."; return 0; }
    files="$(printf '%s' "$meta" | jq -r '.files[].path' 2>/dev/null)"
    checked=$((checked + 1))
    for g in $globs; do
      if printf '%s\n' "$files" | grep -qF -- "$g"; then
        # VET-ONCE (2026-07-06): an in-scope PR whose CURRENT head SHA already carries
        # a 'shepherd-vet sha=<oid>' marker comment was fully vetted at exactly this
        # state — re-vetting it daily while it bakes/queues re-spends for the same
        # verdict. A Renovate rebase moves the SHA, so the marker self-invalidates
        # exactly when a re-vet is warranted.
        oid="$(printf '%s' "$meta" | jq -r '.headRefOid // ""' 2>/dev/null)"
        if [ -n "$oid" ] && printf '%s' "$meta" | jq -e --arg m "shepherd-vet sha=${oid}" \
             '[(.comments[]? | .body // "") | contains($m)] | any' >/dev/null 2>&1; then
          log "pre-filter: PR #$pr touches ramp path '$g' but is already vetted at ${oid:0:10} — not a summon reason."
        else
          log "pre-filter: Renovate PR #$pr touches ramp path '$g' — summoning the LLM."
          return 0
        fi
        break   # this PR is decided either way; move to the next PR
      fi
    done
  done
  log "pre-filter: checked $checked open Renovate PR(s); none unvetted touch the ramp — skipping LLM (\$0). Ramp globs: $globs"
  return 1
}

# Files an esc-shepherd-* entry (dev-env-ops spawns a joinable session and pages
# with its name). $1 = reason. Honours ESCALATE_SIG from the caller (a stable
# signature, so a re-fire of the same failure dedups instead of paging again).
shepherd_escalate() {
  bash /opt/coordination/escalate.sh shepherd "job:${HOSTNAME:-unknown} mode=${MODE}" "$1"
}

# ── AUTO-MERGE STALL FALLBACK (2026-09-30, #3287) ── `gh pr merge --auto` on a PR whose
# checks finished long ago can leave GitHub with nothing to act on. #3280 (rook v1.20.8
# operator) had 7/7 checks green since 02:21Z; the 04:07Z run enabled auto-merge while
# mergeStateStatus still read UNKNOWN (GitHub computes it lazily), so gh queued instead
# of merging, and a PR whose checks are already complete never produces the
# check-completion event that fires a queued merge. It sat CLEAN until a human merged it
# at 08:09Z, and the 08:00Z run paged on the pair's other half meanwhile.
# So after the LLM's turn (MODE=auto only) the LAUNCHER, not the LLM, watches every PR
# whose auto-merge THIS BOT enabled during THIS run and finishes the merge itself once
# GitHub has left it mergeable for AUTOMERGE_GRACE_S. The guardrails are the ones the
# queued merge had, checked here instead of assumed:
#   - only auto-merges enabled by the shepherd bot since RUN_START_ISO — never Renovate's,
#     a human's, or an earlier run's;
#   - OPEN, not draft, auto-merge STILL enabled (a human who switched it off meant it),
#     mergeStateStatus CLEAN, every check COMPLETED + SUCCESS/SKIPPED/NEUTRAL, and both
#     required contexts present;
#   - --match-head-commit pins the merge to the head those checks ran on;
#   - NEVER --admin: the bot is non-admin/non-bypass, so branch protection still refuses
#     the merge server-side unless Flux Local + Diff Scope are green.
# A PR still waiting on checks when the watch ends is left to GitHub: a check completing
# AFTER auto-merge was enabled is exactly the event that fires it. MODE=remediate never
# runs this (its fix PRs are fresh, with checks pending when auto-merge is enabled, and
# triage reads "a new PR is still open" as result=pending).
AUTOMERGE_GRACE_S="${UPGRADE_AGENT_AUTOMERGE_GRACE_S:-120}"
AUTOMERGE_WATCH_S="${UPGRADE_AGENT_AUTOMERGE_WATCH_S:-360}"
AUTOMERGE_POLL_S="${UPGRADE_AGENT_AUTOMERGE_POLL_S:-15}"
SHEPHERD_BOT_LOGIN="${UPGRADE_AGENT_BOT_LOGIN:-haynes-ops-bot}"
REQUIRED_CHECKS_JSON='["Flux Local - Success","Diff Scope - Success"]'

# stdin = `gh pr list --json number,autoMergeRequest`; $1 = ISO-8601 instant. Prints
# the PRs whose auto-merge the shepherd bot enabled at or after $1, one per line.
automerge_candidates() {
  jq -r --arg since "$1" --arg bot "$SHEPHERD_BOT_LOGIN" '
    .[] | select(.autoMergeRequest != null)
    | select((.autoMergeRequest.enabledAt // "") >= $since)
    | select(((.autoMergeRequest.enabledBy.login // "") | sub("^app/"; "") | sub("\\[bot\\]$"; "")) == $bot)
    | .number' 2>/dev/null
}

# stdin = `gh pr view --json state,isDraft,mergedAt,mergeStateStatus,autoMergeRequest,
# headRefOid,statusCheckRollup`. Prints one of:
#   merged          merged (by GitHub or anyone) — done
#   skip <why>      no longer ours to finish (closed, draft, auto-merge switched off)
#   wait <why>      not mergeable yet (a check pending/red, a required context missing,
#                   mergeState not CLEAN) — GitHub's own trigger path still owns it
#   ready <sha>     CLEAN, every check green, both required contexts green, unmerged
automerge_decide() {
  jq -r --argjson req "$REQUIRED_CHECKS_JSON" '
    def green:
      if .__typename == "StatusContext" then (.state // "") == "SUCCESS"
      else (.status // "") == "COMPLETED"
        and ((.conclusion // "") as $c | $c == "SUCCESS" or $c == "SKIPPED" or $c == "NEUTRAL")
      end;
    def cname: .name // .context // "?";
    (.statusCheckRollup // []) as $checks
    | ([$checks[] | select(green | not) | cname]) as $red
    | ($req - [$checks[] | select(green) | cname]) as $missing
    | if (.mergedAt // null) != null or .state == "MERGED" then "merged"
      elif .state != "OPEN" then "skip state=\(.state)"
      elif .isDraft == true then "skip draft"
      elif .autoMergeRequest == null then "skip auto-merge-disabled"
      elif ($checks | length) == 0 then "wait no-checks"
      elif ($red | length) > 0 then "wait checks-not-green=\($red | join(","))"
      elif ($missing | length) > 0 then "wait required-missing=\($missing | join(","))"
      elif .mergeStateStatus != "CLEAN" then "wait mergeState=\(.mergeStateStatus)"
      elif ((.headRefOid // "") | length) == 0 then "wait no-head-sha"
      else "ready \(.headRefOid)" end' 2>/dev/null
}

# $1 = PR, $2 = head SHA the checks passed on, $3 = seconds it sat ready. Plain squash
# merge; on refusal re-reads the PR (GitHub may have won the race) and escalates only
# a PR that is still unmerged. Returns 0 = merged, 1 = not merged.
automerge_complete() {
  local pr="$1" sha="$2" waited="$3" out merged_at
  log "automerge-fallback: #$pr has been CLEAN with every check green for ${waited}s and GitHub has not merged it — merging it (plain squash, pinned to ${sha:0:10}, no --admin)."
  if out="$(gh pr merge "$pr" -R "$REPO" --squash --delete-branch --match-head-commit "$sha" 2>&1)"; then
    log "automerge-fallback: #$pr merged by the fallback."
    return 0
  fi
  merged_at="$(gh pr view "$pr" -R "$REPO" --json mergedAt 2>/dev/null | jq -r '.mergedAt // ""' 2>/dev/null)"
  if [ -n "$merged_at" ]; then
    log "automerge-fallback: #$pr merge call failed but the PR is merged (GitHub got there first) — fine."
    return 0
  fi
  out="$(printf '%s' "$out" | tr '\n\r' '  ' | cut -c1-200)"
  log "automerge-fallback: WARN #$pr stays unmerged — the fallback merge was refused: $out"
  ESCALATE_SIG="shepherd-automerge|pr=${pr}|sha=${sha}" shepherd_escalate \
    "auto-merge stalled: #${pr} is CLEAN with every check green and auto-merge enabled, GitHub has not merged it, and the launcher's plain squash merge was refused: ${out}" \
    || log "escalate.sh could not file the esc-shepherd entry (best-effort; run outcome unchanged)"
  return 1
}

# $1 = RUN_START_ISO. Best-effort: gh failures only log, and it always returns 0.
automerge_fallback() {
  local since="$1" list prs pr view verdict sha key t t0 deadline still waits
  local -A ready_since=()
  list="$(gh pr list -R "$REPO" --state open --limit 200 --json number,autoMergeRequest 2>/dev/null)" || {
    log "automerge-fallback: gh pr list failed — cannot watch for a stalled auto-merge this run."; return 0; }
  prs="$(printf '%s' "$list" | automerge_candidates "$since" | tr '\n' ' ')"
  if [ -z "${prs// /}" ]; then
    log "automerge-fallback: no auto-merge enabled by this run is still open — nothing to watch."
    return 0
  fi
  log "automerge-fallback: watching this run's auto-merge(s): ${prs}(grace ${AUTOMERGE_GRACE_S}s, watch ${AUTOMERGE_WATCH_S}s)."
  deadline=$(( $(epoch_now) + AUTOMERGE_WATCH_S ))
  while :; do
    still=""; waits=""
    t="$(epoch_now)"
    for pr in $prs; do
      view="$(gh pr view "$pr" -R "$REPO" --json state,isDraft,mergedAt,mergeStateStatus,autoMergeRequest,headRefOid,statusCheckRollup 2>/dev/null)" || {
        still="$still $pr"; waits="$waits #$pr(gh-view-failed)"; continue; }
      verdict="$(printf '%s' "$view" | automerge_decide)"
      case "$verdict" in
        merged) log "automerge-fallback: #$pr merged by GitHub auto-merge." ;;
        skip\ *) log "automerge-fallback: #$pr ${verdict#skip } — leaving it alone." ;;
        ready\ *)
          sha="${verdict#ready }"; key="${pr}@${sha}"
          # Keyed by head SHA: a new push restarts the grace for the new head.
          [ -n "${ready_since[$key]:-}" ] || ready_since[$key]="$t"
          t0="${ready_since[$key]}"
          if [ $(( t - t0 )) -ge "$AUTOMERGE_GRACE_S" ]; then
            automerge_complete "$pr" "$sha" "$(( t - t0 ))" || true
          else
            still="$still $pr"
          fi ;;
        *) still="$still $pr"; waits="$waits #$pr(${verdict:-undecidable})" ;;
      esac
    done
    prs="$still"
    [ -n "${prs// /}" ] || break
    if [ "$(epoch_now)" -ge "$deadline" ]; then
      log "automerge-fallback: watch over; still queued:${prs} —${waits:- ready but inside the grace window} — left to GitHub auto-merge (their checks completed after it was enabled, which is the event that fires it)."
      break
    fi
    sleep "$AUTOMERGE_POLL_S"
  done
  return 0
}

# ── VERDICTS + DEFER (2026-09-30, #3287) ── the LLM ends a run with at most these lines
# at the TOP of its summary (the operating rules in SAFETY_PROMPT define them):
#   BREAK-GLASS: / HOLD: / HOLD NEEDED:   a human must act — escalates (pages).
#   DEFER: #<N> waits for #<M> — <why>    the PR is fine and a LATER RUN of this job
#       clears the wait by itself (the other half of a must-move-together set has not
#       merged and rolled Ready, the drain rule's one unit is spent, Kometa is running).
#       Does NOT escalate. A deferred PR gets no vet marker, so the next run re-reads it.
# The FATAL BACKSTOP for DEFER: a wait that does not clear is a real stall (the 08:00Z
# page was one underneath: #3280's auto-merge never fired). defer_track keeps each wait's
# streak in the upgrade-shepherd-defers ConfigMap and escalates, once per streak, when the
# same wait has lasted DEFER_ESCALATE_MIN. 450 min = the third consecutive deferral on the
# 4h cadence (~8h after the first), less 30 min of run-start jitter.
DEFER_CM="${UPGRADE_AGENT_DEFER_CM:-upgrade-shepherd-defers}"
DEFER_ESCALATE_MIN="${UPGRADE_AGENT_DEFER_ESCALATE_MINUTES:-450}"

# $1 = the flattened summary, $2 = claude's rc. Prints the escalation reason, or nothing.
escalation_reason() {
  if printf '%s' "$1" | grep -qE '(BREAK-GLASS|HOLD( NEEDED)?):'; then
    printf 'terminal verdict: %s' "$1"
  elif [ "$2" -ne 0 ]; then
    printf 'auto run died rc=%s (%s)' "$2" "${1:-no summary — see Job logs}"
  fi
}

# stdin = the LLM's full final text (newlines kept). Prints "<key><TAB><line>" per DEFER
# line. The key is the wait's identity, stable however the line is phrased: the first two
# PR numbers after 'DEFER:' sorted and joined with '+' (deferred PR + blocking PR), one
# number when the blocker is not a PR (Kometa), '?' when the line names no PR at all.
defer_keys() {
  local line rest nums
  while IFS= read -r line; do
    case "$line" in *DEFER:*) ;; *) continue ;; esac
    rest="${line#*DEFER:}"
    nums="$(printf '%s' "$rest" | grep -oE '#[0-9]+' | tr -d '#' | head -2 | sort -n | paste -sd+ -)"
    printf '%s\t%s\n' "${nums:-?}" "$(printf '%s' "$line" | tr -d '\t\r' | cut -c1-200)"
  done
}

# stdin = defer_keys output; $1 = previous streak state (JSON object), $2 = now (epoch).
# Pure. A wait deferred again keeps its first_seen and escalated stamp and counts the run;
# a wait NOT deferred this run is dropped — it cleared (merged, proceeded, or closed).
defer_next_state() {
  jq -R 'select(length > 0) | split("\t") | {k: .[0], why: (.[1:] | join(" "))}' \
    | jq -sc --argjson prev "$1" --argjson now "$2" '
        reduce .[] as $d ({}; .[$d.k] = {
          first_seen: ($prev[$d.k].first_seen // $now),
          runs: (($prev[$d.k].runs // 0) + 1),
          escalated: ($prev[$d.k].escalated // 0),
          why: $d.why })'
}

# $1 = streak state, $2 = now, $3 = limit (s). Pure. Prints the waits at least $3 old
# that have not been escalated in this streak yet.
defer_overdue() {
  printf '%s' "$1" | jq -r --argjson now "$2" --argjson lim "$3" '
    to_entries[] | select(($now - .value.first_seen) >= $lim and (.value.escalated // 0) == 0) | .key'
}

# Prints the streak state ("{}" when the ConfigMap does not exist yet); rc 1 = unreadable.
defer_state_read() {
  local out err="/tmp/defer-state-read.err"
  if out="$(kubectl -n "$SPEND_NS" get configmap "$DEFER_CM" -o json 2>"$err")"; then
    printf '%s' "$out" | jq -c '(.data.state // "{}") | (fromjson? // {}) | if type == "object" then . else {} end'
  elif grep -q 'NotFound' "$err" 2>/dev/null; then
    printf '{}'
  else
    log "defer-track: WARN cannot read ${DEFER_CM}: $(tr '\n' ' ' < "$err" 2>/dev/null | cut -c1-160)"
    return 1
  fi
}

# $1 = streak state JSON. Runtime state, NOT in git (Flux would reset it), like the spend CM.
defer_state_write() {
  kubectl -n "$SPEND_NS" create configmap "$DEFER_CM" --from-literal=state="$1" \
    --dry-run=client -o yaml | kubectl -n "$SPEND_NS" apply -f - >/dev/null 2>&1
}

# $1 = the LLM's full final text, $2 = non-empty when this run already escalated (that
# session covers an overdue wait too, so it is not filed twice; the next run retries).
# Best-effort: an unreadable state leaves every streak untouched (never reset blind).
defer_track() {
  local text="$1" escalated_now="$2" prev next now keys overdue k d age runs why
  keys="$(printf '%s\n' "$text" | defer_keys)"
  prev="$(defer_state_read)" || return 1
  [ -n "$prev" ] || prev='{}'
  now="$(epoch_now)"
  next="$(printf '%s\n' "$keys" | defer_next_state "$prev" "$now")" || {
    log "defer-track: WARN could not compute the next streak state — leaving it untouched."; return 1; }
  [ -z "$keys" ] || log "defer-track: deferred this run: $(printf '%s\n' "$keys" | cut -f1 | paste -sd' ' -)"
  overdue="$(defer_overdue "$next" "$now" "$(( DEFER_ESCALATE_MIN * 60 ))")"
  while IFS= read -r k; do
    [ -n "$k" ] || continue
    if [ -n "$escalated_now" ]; then
      log "defer-track: wait $k is overdue, but this run already escalated — not filing twice; the next run retries."
      continue
    fi
    d="$(printf '%s' "$next" | jq -r --arg k "$k" '.[$k] | "\(.first_seen) \(.runs)"')"
    age=$(( now - ${d% *} )); runs="${d#* }"
    why="$(printf '%s' "$next" | jq -r --arg k "$k" '.[$k].why')"
    if ESCALATE_SIG="shepherd-defer|${k}" shepherd_escalate \
         "DEFER stalled: the same wait (PRs ${k}) has been deferred for $(( age / 3600 ))h$(( age % 3600 / 60 ))m across ${runs} runs — it is not clearing by itself. Latest: ${why}"; then
      next="$(printf '%s' "$next" | jq -c --arg k "$k" --argjson now "$now" '.[$k].escalated = $now')"
    else
      log "escalate.sh could not file the DEFER backstop for $k (best-effort; retried next run)"
    fi
  done <<< "$overdue"
  if [ "$prev" = '{}' ] && [ "$next" = '{}' ]; then return 0; fi
  defer_state_write "$next" || { log "defer-track: WARN could not write ${DEFER_CM} (RBAC?)."; return 1; }
}

# Quiet claude-code's phone-home (the egress CNP would block it anyway).
export DISABLE_TELEMETRY=1 CLAUDE_CODE_ENABLE_TELEMETRY=0 \
       DISABLE_ERROR_REPORTING=1 DISABLE_AUTOUPDATER=1 DISABLE_NON_ESSENTIAL_MODEL_CALLS=1

# ── AUTH PATH (2026-07-13, saga plan 07 Option A) ── PLAN-FIRST, API-FALLBACK.
# The LLM turn runs HERE, in the contained shepherd pod — we changed the CREDENTIAL,
# not the execution site. (Dispatching the turn into the dev-env pod would hand a
# prompt-injectable agent — the shepherd READS RELEASE NOTES — that pod's 23-repo
# write token and operator RBAC. See .agents/sagas/dev-env/backlog/07.)
#
#   plan → CLAUDE_CODE_OAUTH_TOKEN (Max subscription; $0 API spend)
#   api  → ANTHROPIC_API_KEY (metered, spend-guarded) — the fallback
#
# CRITICAL: claude-code prefers ANTHROPIC_API_KEY when BOTH are set, so the plan path
# must UNSET it — otherwise we'd silently keep billing the API while believing we're
# on the plan. The key is stashed and restored for the fallback.
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

[ -s /creds/gh_token ] || { log "FATAL: /creds/gh_token missing — initContainer token mint failed"; exit 1; }
GH_TOKEN="$(cat /creds/gh_token)"; export GH_TOKEN
# Assert the PEM did NOT leak into this (the LLM) container.
if [ -n "${GITHUB_BOT_APP_PRIVATE_KEY:-}" ]; then
  log "FATAL: bot PEM present in the LLM container env — refusing to run."; exit 3
fi

# ── RAMP derivation + consistency (2026-07-06) ── UPGRADE_AGENT_RAMP (component list,
# set ONLY on the scheduled cronjob) is the single ramp definition: the path map above
# derives the pre-filter globs, and the check below asserts every component is also
# named in the LLM prompt. Drift (a component missing from the map or the prompt) makes
# a scheduled run REFUSE to vet (fail closed) and flags the gate to page — never a
# silent pre-filter blind spot. Back-compat: an explicit UPGRADE_AGENT_PREFILTER_GLOBS
# still overrides the derivation. A manual/targeted summon clears BOTH vars.
PROMPT="${UPGRADE_AGENT_PROMPT:-}"
RAMP="${UPGRADE_AGENT_RAMP:-}"
RAMP_MISMATCH=""
if [ -n "$RAMP" ] && [ -z "${UPGRADE_AGENT_PREFILTER_GLOBS:-}" ]; then
  UPGRADE_AGENT_PREFILTER_GLOBS=""
  for c in $RAMP; do
    g="$(ramp_globs_for "$c")"
    if [ -z "$g" ]; then RAMP_MISMATCH="$RAMP_MISMATCH $c(no-path-mapping)"; continue; fi
    UPGRADE_AGENT_PREFILTER_GLOBS="${UPGRADE_AGENT_PREFILTER_GLOBS} ${g}"
    if [ -n "$PROMPT" ]; then
      t="$(ramp_prompt_token "$c")"
      case "$PROMPT" in *"$t"*) : ;; *) RAMP_MISMATCH="$RAMP_MISMATCH $c(not-in-prompt)" ;; esac
    fi
  done
fi
if [ "$MODE" = "auto" ] && [ -n "$RAMP" ] && [ -n "$RAMP_MISMATCH" ]; then
  log "RAMP MISMATCH:$RAMP_MISMATCH — failing CLOSED (no vet this run); the gate pages."
  write_orphan_report "$RAMP_MISMATCH"
  exit 2
fi

# Pre-filter BEFORE the clone/LLM: a scheduled auto run with no in-scope unvetted PR
# exits here at $0 (skips clone + LLM). Only when MODE=auto AND a ramp is configured
# (i.e. the scheduled cronjob). Manual/targeted summons clear both vars and are never
# filtered. The orphan-PR report refreshes first — deterministic, ~$0, every scheduled
# run — so the gate's weekly digest never goes stale.
if [ "$MODE" = "auto" ] && [ -n "${UPGRADE_AGENT_PREFILTER_GLOBS:-}" ]; then
  write_orphan_report ""
  if ! prefilter_should_run "${UPGRADE_AGENT_PREFILTER_GLOBS}"; then
    log "run skipped by pre-filter (no in-scope unvetted open PR) — no clone, no LLM, \$0."
    exit 0
  fi
fi

git config --global user.name  "haynes-ops-bot[bot]"
git config --global user.email "haynes-ops-bot[bot]@users.noreply.github.com"
git config --global safe.directory "${WORKDIR}"

log "cloning ${REPO} (shallow)…"
rm -rf "${WORKDIR}"
git clone --depth 1 "https://x-access-token:${GH_TOKEN}@github.com/${REPO}.git" "${WORKDIR}" >&2 || {
  log "FATAL: clone failed (token/egress?)"; exit 1; }
cd "${WORKDIR}"

# ── Tool allowlist + task, per mode. dontAsk auto-denies anything NOT listed. ──
READONLY_TOOLS=(Read Grep Glob
  "Bash(git log:*)" "Bash(git diff:*)" "Bash(git show:*)" "Bash(git status:*)" "Bash(git fetch:*)"
  "Bash(gh pr list:*)" "Bash(gh pr view:*)" "Bash(gh pr diff:*)" "Bash(gh pr checks:*)"
  # gh api repos/* (2026-07-30): release-note diligence reads UPSTREAM repos too
  # (cilium CRD listings burned 8 denials in the 07-30 run). Mutation risk is
  # contained by the installation-scoped bot token (thaynes43/haynes-ops only) +
  # the GitHub-only egress CNP.
  "Bash(gh release view:*)" "Bash(gh release list:*)" "Bash(gh api repos/*)"
  "Bash(kubectl get:*)" "Bash(kubectl describe:*)" "Bash(kubectl version:*)" "Bash(flux get:*)" "Bash(grep:*)" "Bash(cat:*)")
WRITE_TOOLS=(Edit Write
  "Bash(git switch:*)" "Bash(git checkout -b:*)" "Bash(git add:*)" "Bash(git commit:*)"
  "Bash(git push:*)" "Bash(gh pr create:*)" "Bash(gh pr comment:*)")
# gh pr merge is allowed ONLY in auto/remediate modes. Note this is NOT the safety
# boundary: the bot is non-admin + non-bypass, so `gh pr merge --auto` only QUEUES and
# GitHub merges server-side ONLY when Flux Local + Diff Scope are both green. It cannot
# merge past a red/pending check, and `--admin` (skip-checks) fails for a non-admin.
# The launcher's own plain-merge fallback for a queued merge GitHub never fired (see
# AUTO-MERGE STALL FALLBACK) runs outside the LLM and is bound by that same protection.
MERGE_TOOLS=("Bash(gh pr merge:*)")
# Upgrade-window silencing (2026-07-08): after auto-merging a cluster-infra component,
# the shepherd may set a BOUNDED Alertmanager silence for that component's expected
# rollout-transient alerts (OSD/mgr churn, backfill IO, pod restarts) so a normal roll
# doesn't page. silence.sh hardcodes the matchers + max duration; the LLM passes ONLY a
# component name (contained — it cannot silence arbitrary alerts). auto/remediate only.
SILENCE_TOOLS=("Bash(/opt/shepherd/silence.sh:*)")
# Work-order hand-off (2026-08-20, saga dev-env backlog 13): consequential upgrades
# (embedded-component-image moves, majors needing research/supporting edits) go to
# the dev-env-ops EXECUTOR as a session instead of a one-shot vet here. Contained
# like silence.sh — the LLM passes a PR number + fixed-enum class + reason only.
WORKORDER_TOOLS=("Bash(/opt/shepherd/work-order.sh:*)")

# NB: set PROMPT/SAFETY defaults on their OWN line — NOT inline via ${VAR:-default}.
# An apostrophe or brace inside a ${VAR:-default} breaks bash quote parsing.
# (PROMPT itself is read earlier, before the ramp consistency check.)
SAFETY_MERGE="NEVER gh pr merge, NEVER push to main"
case "$MODE" in
  shepherd)  # open a PR, human merges (Phase 4b.1)
    ALLOWED=("${READONLY_TOOLS[@]}" "${WRITE_TOOLS[@]}")
    [ -n "$PROMPT" ] || PROMPT="You are the Tier-4 upgrade shepherd. Follow .agents/runbooks/upgrade-shepherd.md exactly. Survey open manual-tier Renovate PRs (gh pr list); pick the NEXT one by the runbook merge-order. CONSULT .renovate/holds.json5 first (skip if held). Read the release notes (gh release view) and the component section in .agents/runbooks/tier4-component-playbooks.md. Make the required supporting helmrelease/values edits on a NEW branch shepherd/<pkg>-<version>, commit, push, and open a PR with gh pr create. Do NOT merge, do NOT push to main, do NOT touch anything outside kubernetes/**. One PR only, then stop and summarize."
    ;;
  auto)      # open a PR AND enable server-side auto-merge (Phase 4b.3)
    ALLOWED=("${READONLY_TOOLS[@]}" "${WRITE_TOOLS[@]}" "${MERGE_TOOLS[@]}" "${SILENCE_TOOLS[@]}" "${WORKORDER_TOOLS[@]}")
    SAFETY_MERGE="you MAY enable auto-merge with 'gh pr merge <N> --auto --squash --delete-branch' AFTER opening the PR; NEVER merge immediately, NEVER use --admin, NEVER push to main. If a queued auto-merge has not landed although its checks are green, do NOT merge it yourself and do not re-queue it: after your turn the launcher completes any auto-merge you queued that GitHub left CLEAN and unmerged"
    [ -n "$PROMPT" ] || PROMPT="You are the Tier-4 upgrade shepherd in AUTO mode. Follow .agents/runbooks/upgrade-shepherd.md. Survey open manual-tier Renovate PRs (gh pr list); pick the NEXT one by the runbook merge-order. CONSULT .renovate/holds.json5 first (skip if held). Read the release notes and the component playbook. If supporting edits are needed, make them on a NEW branch shepherd/<pkg>-<version>, commit, push, and open a PR (gh pr create); then enable auto-merge with gh pr merge <N> --auto --squash --delete-branch. GitHub merges only when Flux Local AND Diff Scope are both green. Do NOT merge immediately, do NOT push to main, do NOT touch anything outside kubernetes/**. One PR only, then stop and summarize."
    ;;
  remediate) # mode 2: diagnose a regression, forward-fix or rollback+hold (Phase 4b.3)
    ALLOWED=("${READONLY_TOOLS[@]}" "${WRITE_TOOLS[@]}" "${MERGE_TOOLS[@]}" "${SILENCE_TOOLS[@]}" "${WORKORDER_TOOLS[@]}")
    # COST CONTROL (2026-07): remediate runs on a LOWER turn budget and MUST bail early.
    # triage already proved the regression is upgrade-attributable, so the LLM's only job is
    # a git fix OR a fast BREAK-GLASS verdict — never a max-turns investigation. Set the
    # lower cap on its OWN line (never inline via ${VAR:-default} with braces/quotes).
    MAX_TURNS="${UPGRADE_AGENT_REMEDIATE_MAX_TURNS:-20}"
    # MODEL ESCALATION (2026-07-06): remediate is rare, budget-capped ($5/run) and
    # diagnosis-bound — a stronger model is the difference between a clean revert PR
    # and a wasted BREAK-GLASS. Overrides the HR-wide UPGRADE_AGENT_MODEL (claude-opus-5-5),
    # which still governs the daily survey/auto runs.
    MODEL="${UPGRADE_AGENT_REMEDIATE_MODEL:-claude-opus-5-5}"
    SAFETY_MERGE="you MAY open a forward-fix or rollback PR and enable auto-merge with 'gh pr merge <N> --auto'; NEVER merge immediately, NEVER use --admin, NEVER push to main. BAIL EARLY (within a few turns) with one line 'BREAK-GLASS: <reason>' if a git-only fix is not clearly available (immutable field, wedged HelmRelease, stuck finalizer, one-way major, infra/KubePrism/etcd/node, or not caused by a recent upgrade); do NOT investigate to max-turns, do NOT retry denied cluster writes"
    [ -n "$PROMPT" ] || PROMPT="You are the Tier-4 upgrade shepherd in REMEDIATE mode (Mode 2). A recent upgrade may have regressed. Follow .agents/runbooks/upgrade-shepherd.md Mode 2. Diagnose READ-ONLY (flux get, kubectl describe/get) and identify the culprit merge (git log) FAST. If there is a clean git fix — git revert the bump / re-pin the prior version, or a documented supporting forward-fix from .agents/runbooks/tier4-component-playbooks.md — make it on a NEW branch, commit, push, open a PR, and enable auto-merge (gh pr merge <N> --auto). OTHERWISE STOP EARLY, within a few turns, with a single line 'BREAK-GLASS: <reason>' — do NOT keep investigating to max-turns. Bail to BREAK-GLASS when the fix would hit an immutable field, a wedged HelmRelease, a stuck finalizer, a one-way major, an infra/KubePrism/etcd/node fault, or when NO recent merge plausibly caused this. You have a read-only cluster SA: NEVER kubectl apply/exec/delete and do NOT retry a denied command. If a Flux HelmRelease is Stalled/UpgradeFailed on a chart/image bump (the release itself is broken, e.g. a version that fails its readiness probe and rollback-loops), the fix is to re-pin its tag/version in kubernetes/** to the last-working one (a pure bump — auto-mergeable) so git stops re-applying the broken version. A durable HOLD to stop it re-auto-merging lives in .renovate/holds.json5, which is OUTSIDE kubernetes/** and the diff-scope gate blocks the bot there — so do NOT edit .renovate/; instead put a clear 'HOLD NEEDED: <pkg> <version> — <one-line reason>' line at the TOP of your summary so a human adds the hold. Do NOT push to main, stay inside kubernetes/**."
    ;;
  *)         # dryrun (default): read-only, report a plan
    ALLOWED=("${READONLY_TOOLS[@]}")
    [ -n "$PROMPT" ] || PROMPT="DRY RUN - make NO changes. You are the Tier-4 upgrade shepherd. Survey open manual-tier Renovate PRs (gh pr list) and, for the next one per .agents/runbooks/upgrade-shepherd.md, REPORT: is it held (.renovate/holds.json5)? what supporting helmrelease/values edits would it need (per tier4-component-playbooks.md)? Output a concise plan. Do NOT edit files, push, or open PRs."
    ;;
esac

# MODEL on the plan path. SUPERSEDES the 2026-07-13 "keep the cheap model" call:
# Tom's 2026-08-23 ruling is that the automated agents (shepherd + responder) always
# run the LATEST OPUS — these runs merge upgrades and touch production, and being
# wrong costs far more than the quota does. (The 07-13 fact still holds — one shared
# pool, no separate Opus bucket — it is the trade-off that changed.) Interactive
# dev-env work is the surface that stays on Fable.
# UPGRADE_AGENT_PLAN_MODEL can still override per-run.
if [ "$AUTH_PATH" = "plan" ] && [ "$MODE" != "remediate" ] && [ -n "${UPGRADE_AGENT_PLAN_MODEL:-}" ]; then
  MODEL="$UPGRADE_AGENT_PLAN_MODEL"
fi

# Spend guard governs the METERED path only — a plan-served run costs $0 in API spend,
# so gating it on the API budget would pointlessly refuse free work. The guard still
# protects the fallback (and every run before the plan token is mounted).
if [ "$AUTH_PATH" = "api" ]; then
  spend_guard; guard_rc=$?
  if [ "$guard_rc" -ne 0 ]; then
    case "$MODE" in
      auto|remediate) log "run skipped by spend guard (monthly cap reached)"; exit 0 ;;
      *) log "spend-guard: cap reached but MODE=$MODE is manual/human-summoned — proceeding." ;;
    esac
  fi
fi

# ── Cross-cutting operating rules, injected into EVERY mode's system prompt so they hold
# regardless of which task PROMPT is active (incl. the scheduled-ramp override in the HR
# env). Built up on their OWN lines — apostrophes and braces inside a ${VAR:-default}
# break bash quote parsing, and $( ) must be escaped as \$( ) so it isn't run here.
SAFETY_PROMPT="SAFETY: read-only cluster default; ALL cluster changes go via a PR to kubernetes/**; NEVER kubectl apply/exec/delete; ${SAFETY_MERGE}; stay inside kubernetes/**."
# (1) PR AUTHORING — the fix for the heredoc dontAsk denial that blocked the shepherd from
# opening its own PRs (immich #1966): author the body as a FILE, never a command-sub.
SAFETY_PROMPT="${SAFETY_PROMPT} PR AUTHORING: to open a PR, FIRST write the body to a file with the Write tool (e.g. /tmp/pr-body.md), THEN run 'gh pr create --title \"...\" --body-file /tmp/pr-body.md'. NEVER build the body with a heredoc or \$(cat ...) command substitution — that compound form is auto-denied and the PR will silently fail to open. The same trap applies to commits and gh api: write the message to a file and use 'git commit -F /tmp/commit-msg.txt' (NEVER -m with a \$(...) substitution), and pass gh api paths UNQUOTED (quoting the path defeats the allowlist matcher and the call is denied)."
# (1b) GH AUTH (2026-08-05) — the 08-05 12:00Z auto run rabbit-holed on minting a
# token: it tried only NON-allowlisted probes (scripts/github-app-token.sh, gh auth
# status, gh --version, env), concluded "gh is blocked by the sandbox" and HELDed
# asking a human to repair a sandbox that wasn't broken — the same run's pre-filter
# had just used gh fine, and the launcher exports GH_TOKEN before the LLM starts.
# State the auth facts so no run re-derives that wrong turn.
SAFETY_PROMPT="${SAFETY_PROMPT} GH AUTH: gh is ALREADY authenticated — the launcher minted a token and exported GH_TOKEN before you started; never verify, mint, or hunt for credentials. scripts/github-app-token.sh, 'gh auth', 'gh --version', 'env' and 'printenv' are NOT on the allowlist, and their denial tells you NOTHING about whether gh works — do not conclude gh or the sandbox is broken from them. Just run the allowlisted gh subcommands your task names directly (with -R ${REPO} when outside the repo checkout). Only if one of THOSE exact allowlisted commands is denied, stop with a HOLD quoting the denied command verbatim."
# (2) BACKUP GATE — before touching anything with durable state, confirm the auto-backup
# safety net is intact (read-only; the shepherd has kubectl get). A stale/failed backup
# means a human is needed (the CNPG/VolSync backup-failure alerts already page), so HOLD.
SAFETY_PROMPT="${SAFETY_PROMPT} BACKUP GATE: before you merge, enable auto-merge on, or forward-fix any component backed by a database or persistent volume (cnpg/postgres, rook-ceph, dragonfly, emqx, immich, authentik, paperless, the *arr apps, etc.), FIRST verify a recent SUCCESSFUL backup exists — for cnpg: 'kubectl get backup -n database' (newest .status.phase=completed within 24h) or the Cluster .status.lastSuccessfulBackup; for volsync-backed apps: 'kubectl get replicationsource -A' (.status.lastSyncTime within its schedule). If no healthy backup within 24h exists, do NOT proceed — stop with one line 'HOLD: backup safety net compromised for <component>'. Stateless components need no backup check."
# (3) AUTONOMY — this Job runs unattended on a schedule; there is no human to answer a
# mid-task question. Finish the allowlisted work or bail with ONE structured line.
# (3b) VERDICTS (2026-09-30, #3287) — HOLD pages Tom (MODE=auto escalation below), so a
# wait the next run clears on its own gets its own non-paging verdict, DEFER. The 08:00Z
# page was a HOLD on the second half of the rook pair while the first half's auto-merge
# was still queued: nothing a human needed to do.
SAFETY_PROMPT="${SAFETY_PROMPT} AUTONOMY: you run UNATTENDED — no human is watching this run. For reversible, in-scope actions proceed without asking. NEVER end your turn with a question, a plan, or an 'I will…' you have not executed — either complete the allowlisted work now, or put a verdict line at the TOP of your summary."
SAFETY_PROMPT="${SAFETY_PROMPT} VERDICTS — choose by WHO has to act next. 'DEFER: #<N> waits for #<blocking PR> — <why>' (the blocking PR is whatever holds it back: the previous PR of its set, the unit this run took under the drain rule, the phase-1 PR; write 'waits for <condition>' when the blocker is not a PR; one DEFER line per waiting PR) when the PR itself is fine and a LATER RUN of this scheduled job clears the wait on its own: the previous PR of a must-move-together set has not merged and rolled Ready yet, the one-stateful-unit-per-run drain rule is spent for this run, Kometa is running or about to start, a split component's first phase is not verified yet, an auto-merge you queued has not landed yet. DEFER does not page anyone; the launcher escalates it only if the same wait is still there about 8h later. 'HOLD: <why>' only when a HUMAN must act before the PR can move: backup safety net compromised, an allowlisted command denied, a failed hand-off, a state you cannot explain. HOLD pages the owner. 'BREAK-GLASS: <why>' when recovery needs a cluster write you do not have. Never HOLD a wait the next run clears by itself, and never DEFER a real problem."
# (4) VET MARKER (2026-07-06) — the write half of the pre-filter's vet-once skip: a vet
# recorded as a PR comment keyed to the head SHA is never re-paid while the PR bakes.
# Not applicable to dryrun (no comment tool there; dryrun is never the scheduled run).
if [ "$MODE" != "dryrun" ]; then
  SAFETY_PROMPT="${SAFETY_PROMPT} VET MARKER: when you FINISH vetting a Renovate PR — whatever the verdict (auto-merge enabled, declined, held, left-for-Renovate, or you authored a supporting-edit PR for it), EXCEPT a DEFER — record the vet so it is never re-done at this state: get the head SHA with 'gh pr view <N> --json headRefOid', use the Write tool to author /tmp/vet-comment.md whose FIRST line is exactly '<!-- shepherd-vet sha=<HEAD_SHA> -->' followed by 'VERDICT: <one word>' and 2-4 sentences of reasoning, then run 'gh pr comment <N> --body-file /tmp/vet-comment.md'. Never build the comment inline. Conversely, SKIP (do not re-vet) any PR whose CURRENT head SHA already appears in a shepherd-vet comment — treat its recorded verdict as done. A DEFERRED PR gets NO marker: the marker would stop the next run from re-reading it, and that next run is what clears the wait."
fi

if [ "$AUTH_PATH" = "plan" ]; then
  log "MODE=$MODE auth=$AUTH_PATH model=$MODEL max_turns=unbounded(timeout=$RUN_TIMEOUT; api fallback=$MAX_TURNS) budget=\$$MAX_BUDGET cap=\$$MONTHLY_CAP"
else
  log "MODE=$MODE auth=$AUTH_PATH model=$MODEL max_turns=$MAX_TURNS budget=\$$MAX_BUDGET cap=\$$MONTHLY_CAP"
fi

# One claude run. --max-turns and --max-budget-usd are METERED-path spend controls;
# a plan-served run is $0, so its only bound is RUN_TIMEOUT (capping turns there just
# strands finished work — see MAX_TURNS above). AUTH_PATH is read at CALL time, so the
# api fallback rerun automatically regains both caps. Writes JSON to $1.
run_claude() {
  local out="$1" args=()
  args=(-p "$PROMPT"
        --permission-mode dontAsk
        --allowedTools "${ALLOWED[@]}"
        --disallowedTools "WebFetch" "WebSearch"
        --append-system-prompt "$SAFETY_PROMPT"
        --model "$MODEL"
        --output-format json)
  [ "$AUTH_PATH" = "api" ] && args+=(--max-turns "$MAX_TURNS" --max-budget-usd "$MAX_BUDGET")
  timeout "$RUN_TIMEOUT" claude "${args[@]}" | tee "$out"
  return "${PIPESTATUS[0]}"
}

# A plan run that dies on a RATE LIMIT (5-hour window or weekly cap exhausted) must
# not strand the work — least of all a remediate run, where a stalled fix means a
# live regression stays broken (Decision #2). Fall back to the metered API key ONCE,
# under the spend guard. An AUTH failure (expired/revoked token) also falls back, and
# auth-watch pages so the token gets re-minted. Any OTHER failure is a real error and
# is NOT retried — retrying a genuinely broken run would just double-spend.
set +e
OUT_FILE="$(mktemp 2>/dev/null || echo /tmp/claude-out.json)"
run_claude "$OUT_FILE"; rc=$?

if [ "$AUTH_PATH" = "plan" ] && [ "$rc" -ne 0 ] && [ -n "$ANTHROPIC_API_KEY_STASH" ]; then
  fallback_reason=""
  if grep -qiE 'rate limit|usage limit|limit reached|too many requests|429' "$OUT_FILE" 2>/dev/null; then
    fallback_reason="plan rate-limited"
  elif grep -qiE 'unauthorized|invalid.*(token|api key)|authentication|401|/login' "$OUT_FILE" 2>/dev/null; then
    fallback_reason="plan token rejected (re-run the setup-token ceremony)"
  fi
  if [ -n "$fallback_reason" ]; then
    log "FALLBACK: $fallback_reason — retrying on the metered API key."
    AUTH_PATH="api"
    export ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY_STASH"
    # Metered path: Sonnet 5.5 for EVERY mode, remediate included (2026-08-23 rule —
    # pay-per-token never runs Opus/Fable). A remediate fallback that needs more
    # muscle should dispatch a pod claude-code agent, not bill Opus by the token.
    MODEL="$FALLBACK_MODEL"
    spend_guard; guard_rc=$?
    if [ "$guard_rc" -ne 0 ] && { [ "$MODE" = "auto" ] || [ "$MODE" = "remediate" ]; }; then
      log "fallback BLOCKED by the spend guard (monthly cap reached) — no run this cycle."
    else
      run_claude "$OUT_FILE"; rc=$?
    fi
  else
    log "run failed (rc=$rc) for a reason that is NOT rate-limit/auth — not falling back."
  fi
fi
set -e 2>/dev/null || true

# Only a metered run has a dollar cost to record; a plan-served run is $0 by
# construction, so the monthly counter stays flat (that IS the win being measured).
if [ "$AUTH_PATH" = "api" ]; then
  record_spend "$OUT_FILE"
else
  log "spend: \$0 this run (served by the Max plan)."
fi
# Durable verdict (2026-07-06): leave the final summary line at a well-known path for
# the caller — triage records it in the coordination state and the GATE puts it in the
# page body, so a human sees the BREAK-GLASS/HOLD reason without digging Job logs.
# `|| …=""` on both: `set -e` is live from here on, so a jq parse error on a truncated
# OUT_FILE must not end the script before the rc!=0 escalation below gets to run.
SUMMARY="$(jq -r '.result // empty' "$OUT_FILE" 2>/dev/null | tr '\n\r' '  ' | cut -c1-400)" || SUMMARY=""
# The full text keeps its newlines: defer_track reads one DEFER line per waiting PR, and
# those can sit past the 400 chars SUMMARY keeps.
RESULT_TEXT="$(jq -r '.result // empty' "$OUT_FILE" 2>/dev/null)" || RESULT_TEXT=""
printf '%s' "$SUMMARY" > /tmp/shepherd-summary.txt 2>/dev/null || true
[ -n "$SUMMARY" ] && log "final: $SUMMARY"
log "claude exited rc=$rc"

# ── FAILURE ESCALATION (2026-08-21, backlog 12→13; verdicts 2026-09-30, #3287) ── a
# SCHEDULED (MODE=auto, unattended) run that ends in a terminal BREAK-GLASS/HOLD verdict
# or dies rc!=0 files an esc-shepherd-* entry via the shared writer; the dev-env-ops
# executor spawns a joinable session and pages Tom WITH the session name.
# Deliberately NOT for manual modes (a human summoned and is watching those) and
# NOT for remediate (triage owns that outcome — it escalates on RESULT=failed
# with the coordination signature; hooking here too would double-file).
# A DEFER verdict is NOT terminal and does not escalate here: defer_track owns it and
# escalates only a wait that has persisted DEFER_ESCALATE_MIN, keyed on the PRs in the
# wait so every re-fire dedups. The verdict text is no key for that: a waiting PR carries
# no vet marker, so the next run re-vets it, and its wording changes from run to run.
# (Before #3287 the pair's second half was a HOLD, so it paged, under a fresh signature,
# every 4h for as long as the first half sat unmerged.) Vetted HOLDs rarely repeat: the
# vet marker stops the next run re-vetting the same head SHA.
# BEST-EFFORT: nothing here changes this run's rc.
if [ "$MODE" = "auto" ]; then
  automerge_fallback "$RUN_START_ISO" \
    || log "automerge-fallback: unexpected error (best-effort; run outcome unchanged)"
  esc_reason="$(escalation_reason "$SUMMARY" "$rc")" || esc_reason=""
  if [ -n "$esc_reason" ]; then
    shepherd_escalate "$esc_reason" \
      || log "escalate.sh could not file the esc-shepherd entry (best-effort; run outcome unchanged)"
  fi
  defer_track "$RESULT_TEXT" "$esc_reason" \
    || log "defer-track: streaks not updated this run (best-effort; run outcome unchanged)"
fi
exit "$rc"
