#!/usr/bin/env bash
# publish-report.sh <order-key> <target-repo> <target-issue> <report-file>...
#
# GETS A WORK ORDER'S REPORTS PUBLISHED — by the target route if the credential
# allows it, by a haynes-ops mirror if it does not. Reporting therefore has no
# failure mode that needs a human, and a lane must NEVER end an order in `failed`
# — never the words "BLOCKER NEEDING A HUMAN" — over a comment box.
#
# Why it exists (haynes-ops#2709, surfaced by wo-cigar-wave3-batch1-20260831):
# the curate lane landed 60/60 verified catalog mutations and then parked the
# whole order as a human blocker because it could not post two finished report
# files to cigar-journal#196. Verified work sat behind a comment box.
#
# ROUTE 1 — the target repo, directly. Tried first, always. When the OPS bot's
# App grant covers Issues on the target repo, this is the whole story and the
# report lands where the order said to put it.
#
# ROUTE 2 — the haynes-ops mirror, on a permission refusal only. Defense in
# depth for any repo the grant does not (yet) cover.
#
# CREDENTIAL FACTS, probed live from this pod on 2026-08-31 — do not re-litigate
# from assumptions:
#   * The App installation covers BOTH thaynes43/haynes-ops and
#     thaynes43/cigar-journal (the owner widened it on 2026-08-31).
#   * It still grants NO `issues` permission, on either repo. So route 1 returns
#     "Resource not accessible by integration (addComment)" TODAY, and the
#     fallback is what actually carries the report. Repo access and permission
#     grants are separate things; adding the repo did not add Issues.
#   * The down-scope in the gh-refresher (GITHUB_BOT_TOKEN_PERMISSIONS =
#     contents:write, pull_requests:write, checks:read) cannot simply gain
#     issues:write either: a token request for a permission the installation does
#     not grant fails 422 and mints NOTHING, which would take out git push and PR
#     merge for every lane. Verified by minting with issues:write — 422, no token.
#     Route 1 starts working the moment an operator grants the App Issues:write
#     AND that permission is added to the down-scope; this script needs no edit.
#   * Pull requests are fully available: branch push, PR create, comments on a PR
#     conversation, label create + apply. Hence the mirror is a DRAFT PULL
#     REQUEST, not an issue — the bot cannot open an issue anywhere, haynes-ops
#     included. If you were told to "make the mirror an issue", that is why it is
#     not one.
#
# WHAT IT DOES
#   1. Route 1: posts every report body IN FULL as comments on
#      <target-repo>#<issue>, chunked to GitHub's comment limit, each stamped
#      with an idempotency marker so a re-run never double-posts.
#   2. On a permission refusal, switches to route 2: opens (or reuses) a DRAFT PR
#      on haynes-ops from branch `relay/<order-key>`, titled
#      `[relay] <order-key> reports for <target-repo>#<issue>`, labelled
#      `relay:pending`, carrying the files under `.agents/relay/<order-key>/`,
#      and posts the same bodies in full as comments there.
#   3. Prints the URL it published to. Put that URL in your order-status note —
#      the note is what reaches the quiet digest, and `relay:pending` is what a
#      later sweep finds.
#
# The reports on the PVC are written by the caller and are the source of truth;
# this script only publishes them. Write them first, always, both routes.
#
# The mirror PR is a CARRIER, never a merge: its diff exists only so a PR can
# exist, and it touches `.agents/relay/**` only, which no CI path filter matches.
# Whoever relays it to the target repo comments the target URL on it, swaps the
# label to `relay:done`, and closes it with --delete-branch.
#
# Usage:
#   bash /opt/dev-env-ops/publish-report.sh wo-cigar-wave3-batch1-20260831 \
#     thaynes43/cigar-journal 196 \
#     ~/.local/state/cigar-curation/reports/wave3-batch1-interim.md \
#     ~/.local/state/cigar-curation/reports/wave3-batch1-closeout.md
set -uo pipefail

key="${1:?usage: publish-report.sh <order-key> <target-repo> <target-issue> <report-file>...}"
target_repo="${2:?usage: publish-report.sh <order-key> <target-repo> <target-issue> <report-file>...}"
target_issue="${3:?usage: publish-report.sh <order-key> <target-repo> <target-issue> <report-file>...}"
shift 3
[ "$#" -gt 0 ] || { echo "publish-report: give at least one report file" >&2; exit 2; }

MIRROR_REPO="${MIRROR_REPO:-thaynes43/haynes-ops}"
CANON="${RELAY_CANON_CLONE:-$HOME/repos/haynes-ops}"
CHUNK_BYTES="${RELAY_CHUNK_BYTES:-55000}"   # GitHub's comment cap is 65536.
branch="relay/${key}"
title="[relay] ${key} reports for ${target_repo}#${target_issue}"
oplog() { bash /opt/dev-env-ops/ops-log.sh "$@" 2>/dev/null || true; }

# Always re-read the minted token: a GH_TOKEN inherited from an older shell is
# the classic 401 here, and the sidecar rewrites /creds/gh_token every 40min.
[ -r /creds/gh_token ] && export GH_TOKEN="$(cat /creds/gh_token)"

for f in "$@"; do
  [ -r "$f" ] || { echo "publish-report: cannot read report file: $f" >&2; exit 1; }
done

# Post one chunk. rc 0 = posted, 2 = REFUSED for permissions, 1 = other failure.
post_comment() { # <repo> <number> <header> <partfile>
  local repo="$1" num="$2" hdr="$3" part="$4" err rc
  err="$(jq -Rs --arg h "$hdr" '{body: ($h + "\n\n" + .)}' < "$part" \
    | gh api -X POST "/repos/${repo}/issues/${num}/comments" --input - 2>&1 >/dev/null)"; rc=$?
  [ "$rc" -eq 0 ] && return 0
  case "$err" in
    *"not accessible by integration"*|*"HTTP 403"*|*"Must have admin rights"*|*"HTTP 404"*) return 2 ;;
    *) echo "publish-report: $err" >&2; return 1 ;;
  esac
}

comment_bodies() { # <repo> <number> — existing bodies, for idempotency
  gh api --paginate "/repos/${1}/issues/${2}/comments" --jq '.[].body' 2>/dev/null
}

# ── route 2 setup: open or reuse the mirror carrier PR ────────────────────────
open_mirror() {
  mirror_num="$(gh pr list --repo "$MIRROR_REPO" --head "$branch" --state all \
    --json number --jq '.[0].number // empty' 2>/dev/null)"
  [ -n "$mirror_num" ] && return 0

  gh label create "relay:pending" --repo "$MIRROR_REPO" --color ededed \
    --description "Reports the ops bot could not post to their target repo — relay them, then close" >/dev/null 2>&1
  gh label create "relay:done" --repo "$MIRROR_REPO" --color ededed \
    --description "Relayed to the target repo; carrier PR may be closed unmerged" >/dev/null 2>&1

  [ -d "$CANON/.git" ] || git clone "https://github.com/${MIRROR_REPO}.git" "$CANON" >/dev/null 2>&1
  git -C "$CANON" fetch --prune origin main >/dev/null 2>&1 \
    || { echo "publish-report: could not fetch origin/main" >&2; return 1; }

  local wt="$HOME/work/relay-${key}" dest
  rm -rf "$wt"; git -C "$CANON" worktree prune >/dev/null 2>&1
  git -C "$CANON" worktree add -B "$branch" "$wt" origin/main >/dev/null 2>&1 \
    || { echo "publish-report: could not create the relay worktree" >&2; return 1; }

  dest="$wt/.agents/relay/${key}"
  mkdir -p "$dest"
  cp -- "$@" "$dest/" || { echo "publish-report: could not stage report files" >&2; return 1; }
  {
    printf '# Relay carrier: %s\n\n' "$key"
    printf 'Reports the OPS bot could not post to `%s#%s` (haynes-ops#2709).\n' "$target_repo" "$target_issue"
    printf 'Full bodies are also PR comments. Carrier only — do not merge.\n'
  } > "$dest/README.md"

  git -C "$wt" add -A ".agents/relay/${key}" >/dev/null 2>&1
  git -C "$wt" commit -q -m "chore(relay): ${key} reports for ${target_repo}#${target_issue}" >/dev/null 2>&1
  if ! git -C "$wt" push -q -u origin "$branch" 2>/dev/null; then
    echo "publish-report: push of $branch failed" >&2
    git -C "$CANON" worktree remove --force "$wt" >/dev/null 2>&1; return 1
  fi

  local body
  body="$(printf '%s\n' \
    "Carrier PR. **Do not merge** — close it with \`--delete-branch\` once relayed." \
    "" \
    "The OPS bot completed order \`${key}\` but could not post its reports to \`${target_repo}#${target_issue}\`: the App grant carries no \`issues\` permission, so \`addComment\` is refused (history: haynes-ops#2709). The reports are therefore published here, in full, as comments below." \
    "" \
    "**To discharge this relay:** post the comment bodies to ${target_repo}#${target_issue}, comment the target URL here, swap \`relay:pending\` for \`relay:done\`, and close." \
    "" \
    "Files on the branch: \`.agents/relay/${key}/\`")"

  if ! gh pr create --repo "$MIRROR_REPO" --draft --base main --head "$branch" \
       --title "$title" --body "$body" >/dev/null 2>&1; then
    echo "publish-report: gh pr create failed" >&2
    git -C "$CANON" worktree remove --force "$wt" >/dev/null 2>&1; return 1
  fi
  git -C "$CANON" worktree remove --force "$wt" >/dev/null 2>&1

  mirror_num="$(gh pr list --repo "$MIRROR_REPO" --head "$branch" --state all \
    --json number --jq '.[0].number // empty' 2>/dev/null)"
  [ -n "$mirror_num" ] || { echo "publish-report: PR created but could not be located" >&2; return 1; }
  gh api -X POST "/repos/${MIRROR_REPO}/issues/${mirror_num}/labels" \
    -f "labels[]=relay:pending" >/dev/null 2>&1
  return 0
}

# ── publish ───────────────────────────────────────────────────────────────────
route="target"; dest_repo="$target_repo"; dest_num="$target_issue"; mirror_num=""
existing="$(comment_bodies "$dest_repo" "$dest_num")"
posted=0; skipped=0

switch_to_mirror() {
  open_mirror "$@" || { echo "publish-report: BOTH routes failed — reports remain on the PVC only" >&2; exit 1; }
  route="mirror"; dest_repo="$MIRROR_REPO"; dest_num="$mirror_num"
  existing="$(comment_bodies "$dest_repo" "$dest_num")"
  echo "publish-report: ${target_repo}#${target_issue} refused the post — relaying to the haynes-ops mirror instead" >&2
}

for f in "$@"; do
  base="$(basename "$f")"
  tmpd="$(mktemp -d)" || { echo "publish-report: mktemp failed" >&2; exit 1; }
  split -C "$CHUNK_BYTES" -d -a 3 -- "$f" "$tmpd/part." 2>/dev/null || cp -- "$f" "$tmpd/part.000"
  parts=( "$tmpd"/part.* ); total="${#parts[@]}"; i=0
  for p in "${parts[@]}"; do
    i=$((i + 1))
    marker="<!-- relay:${key}:${base}:${i}/${total} -->"
    if printf '%s' "$existing" | grep -qF "$marker"; then skipped=$((skipped + 1)); continue; fi
    hdr="${marker}"$'\n'"**\`${base}\`**"
    [ "$total" -gt 1 ] && hdr="${hdr} (part ${i} of ${total})"
    post_comment "$dest_repo" "$dest_num" "$hdr" "$p"; rc=$?
    if [ "$rc" -eq 2 ] && [ "$route" = "target" ]; then
      switch_to_mirror "$@"
      if printf '%s' "$existing" | grep -qF "$marker"; then skipped=$((skipped + 1)); continue; fi
      post_comment "$dest_repo" "$dest_num" "$hdr" "$p"; rc=$?
    fi
    if [ "$rc" -ne 0 ]; then
      echo "publish-report: FAILED to post ${base} part ${i}/${total} to ${dest_repo}#${dest_num}" >&2
      rm -rf "$tmpd"; exit 1
    fi
    posted=$((posted + 1))
  done
  rm -rf "$tmpd"
done

if [ "$route" = "mirror" ]; then
  url="$(gh pr view "$mirror_num" --repo "$MIRROR_REPO" --json url --jq .url 2>/dev/null)"
  [ -n "$url" ] || url="https://github.com/${MIRROR_REPO}/pull/${mirror_num}"
else
  url="https://github.com/${target_repo}/issues/${target_issue}"
fi
oplog published "$key" route="$route" url="$url" target="${target_repo}#${target_issue}" posted="$posted"
echo "publish-report: route=${route}; ${posted} comment(s) posted, ${skipped} already present"
echo "$url"
