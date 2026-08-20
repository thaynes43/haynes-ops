#!/usr/bin/env bash
# work-order.sh <pr-number> <class> <reason...> — HAND OFF a consequential upgrade
# to the dev-env-ops EXECUTOR (saga dev-env backlog 13) instead of one-shot
# vetting it here. Writes a pending entry to the `upgrade-work-orders` ConfigMap;
# the executor's watcher turns it into a long-lived claude session that
# researches, authors supporting edits, merges behind green checks, watches the
# roll, and heals — quiet on success, pages on failure.
#
# Born from the 2026-08-20 rook storm: the one-shot shepherd correctly vetted a
# "patch" chart bump that in fact rolled every Ceph daemon and shipped new
# ERR-severity auth checks — a class of upgrade that needs a session, not a vet.
#
# CONTAINED like silence.sh: the LLM passes only a PR number, a fixed-enum class,
# and free-text reason; the CM name/namespace are hardcoded. An existing
# pending/claimed order for the PR is never overwritten (idempotent re-runs).
# IMPORTANT (LLM contract): if this script FAILS, the hand-off did NOT happen —
# treat the PR as HOLD and say so in the summary; do not assume delivery.
set -uo pipefail
pr="${1:?usage: work-order.sh <pr-number> <class> <reason...>}"
class="${2:?usage: work-order.sh <pr-number> <class> <reason...>}"
shift 2
reason="${*:-no reason given}"

case "$pr" in *[!0-9]*|"") echo "work-order: pr must be numeric" >&2; exit 2 ;; esac
case "$class" in
  embedded-image-move|major|one-way-support|supporting-edits|other) ;;
  *) echo "work-order: class must be embedded-image-move|major|one-way-support|supporting-edits|other" >&2; exit 2 ;;
esac

CM=upgrade-work-orders
NS=upgrade-agent
REPO="${UPGRADE_AGENT_REPO:-thaynes43/haynes-ops}"
key="wo-${pr}"
now="$(date -u +%s)"

title="$(gh pr view "$pr" -R "$REPO" --json title -q .title 2>/dev/null | head -c 140)"
entry="$(jq -nc --arg pr "$pr" --arg repo "$REPO" --arg title "${title:-unknown}" \
    --arg class "$class" --arg reason "$reason" --arg now "$now" \
    '{pr:$pr, repo:$repo, title:$title, class:$class, reason:$reason,
      status:"pending", created:$now, updated:$now} | tojson')"

if kubectl -n "$NS" get cm "$CM" >/dev/null 2>&1; then
  existing="$(kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null \
    | jq -r --arg k "$key" '(.data[$k] // "{}") | (fromjson? // {}) | .status // empty')"
  case "$existing" in
    pending|claimed)
      echo "work-order: $key already ${existing} — not overwriting (hand-off already in flight)."
      exit 0 ;;
  esac
  kubectl -n "$NS" get cm "$CM" -o json 2>/dev/null \
    | jq --arg k "$key" --arg v "$entry" '.data[$k] = $v' \
    | kubectl -n "$NS" replace -f - >/dev/null \
    || { echo "work-order: FAILED to write $CM/$key — hand-off did NOT happen" >&2; exit 1; }
else
  kubectl -n "$NS" create configmap "$CM" --from-literal="$key=$entry" >/dev/null \
    || { echo "work-order: FAILED to create $CM — hand-off did NOT happen" >&2; exit 1; }
fi
echo "work-order: $key filed (class=$class) — dev-env-ops will open a session; it stays quiet unless it fails."
