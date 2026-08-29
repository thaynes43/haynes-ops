#!/usr/bin/env bash
# agent-run — worktree-per-task agent dispatcher (saga plan 06). Isolation by
# construction: every task gets its own git worktree + branch + tmux session + log;
# concurrent agents in the same repo never collide. GitOps-managed — edit in
# kubernetes/main/apps/dev/dev-env/app/resources/agent-run.sh.
#
#   agent-run [<repo>] [--agent claude|codex] [--base <ref>] [-p "<task>"]
#             [--interactive] [--local] [--safe] [--model <m>] [--effort <l>]
#   agent-run                                   # bare → guided walkthrough, TOOL-FIRST:
#                                               # repo → agent → mode → model → effort.
#                                               # (`agent-run run …` still works, hidden alias.)
#   agent-run list                              # live tasks + attach cmds
#   agent-run attach [<task-id>]                # jump into a session (no id → picker)
#   agent-run detach [<task-id>]                # kick attached clients off (no id → picker)
#   agent-run reap   [<task-id>] [--force]      # kill + cleanup (no id → picker)
#   agent-run prune  [--yes] [--force]          # bulk-clean ALL stranded worktrees (dry-run w/o --yes)
#   agent-run codex-remote [stop]               # pod-level codex phone/web control (see NB)
#
# Bare `agent-run` fills every omitted choice interactively, TOOL-FIRST: repo picker
# (owner's GitHub repos newest-pushed first; offline → local clones), agent (claude|
# codex), then a MODE picker, a MODEL picker, and an EFFORT picker (codex effort is
# filtered to the model — only gpt-5.6 has max, only sol/terra add ultra). Flags win —
# scripted callers pass them and see no prompts (defaults: effort xhigh, model = each
# tool's own default).
#
# MODE (how you drive it):
#   both   claude only — a terminal TUI you type to HERE *and* drive from phone/
#          claude.ai/code (claude --remote-control). The recommended default.
#   local  a terminal TUI here only (no remote session).
#   task   fire-and-forget (-p): runs headless, logs to ~/work/<id>.log.
# Flag pins: -p → task; --local → local; --interactive → both (claude) / local (codex).
#
# model/effort plumbing: claude reads --model/--effort as top-level flags (they compose
# with --remote-control); codex reads -m <model> + -c model_reasoning_effort=<level>.
# Effort DEFAULTS TO xhigh; unset model → each tool's own default. --safe restores
# permission prompts (--permission-mode default).
# The claude model picker offers PINNED IDS, not aliases: alias→model resolution is
# client-side in a version-pinned CLI, so an alias silently serves an older tier
# whenever the image lags a launch (see the picker comment).
#
# NB codex phone/web: codex has no per-session remote like claude's --remote-control —
# its remote is a single per-machine app-server daemon. So codex phone/web is a
# POD-LEVEL `agent-run codex-remote` (starts the daemon + prints a pairing code; the
# phone picks the dir, bypassing per-worktree isolation), not a per-task mode.
#
# attach/detach/reap without an id open an arrow-key picker (↑/↓ + Enter, q
# cancels) showing each task's start time; reap's picker also lists STRANDED
# worktrees (pod restart killed tmux, PVC kept the worktree) so they don't
# balloon the PVC. `prune` clears the whole backlog at once. Both are safe by
# construction: a worktree is removed only when it has no uncommitted TRACKED
# changes (untracked scratch like `.claude/` is discarded) — agents commit +
# PR before abandoning a worktree, so tracked-clean means the work is saved.
# attach is nested-tmux aware: from inside tmux it switches clients instead of
# tripping the "sessions should be nested with care" guard.
#
# Agents run YOLO by default (claude --dangerously-skip-permissions / codex
# --dangerously-bypass-approvals-and-sandbox): the pod IS the sandbox.
set -uo pipefail

REPOS="$HOME/repos" WORK="$HOME/work" OWNER="thaynes43"
log() { printf 'agent-run: %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# did_you_mean <word> <candidate...> — echoes the closest candidate within edit
# distance 2, else nothing. awk does the Levenshtein; bash arrays are misery.
did_you_mean() {
  local w="$1"; shift
  printf '%s\n' "$@" | awk -v w="$w" '
    function min3(a,b,c,  r) { r=a; if(b<r)r=b; if(c<r)r=c; return r }
    BEGIN { best=99 }
    {
      n=length(w); m=length($0)
      for(i=0;i<=n;i++) d[i,0]=i
      for(j=0;j<=m;j++) d[0,j]=j
      for(i=1;i<=n;i++) for(j=1;j<=m;j++)
        d[i,j]=min3(d[i-1,j]+1, d[i,j-1]+1,
                    d[i-1,j-1]+(substr(w,i,1)!=substr($0,j,1)))
      if(d[n,m]<best) { best=d[n,m]; bestw=$0 }
    }
    END { if(best<=2) print bestw }'
}

usage() {
  cat >&2 <<'EOF'
agent-run — worktree-per-task agent dispatcher
  agent-run [<repo>] [flags]                  # bare = guided walkthrough (repo → agent →
                                              # mode → model → effort). `run` is a hidden alias.
      --repo <name>                           # same as the positional <repo>
      --agent claude|codex
      --base <ref>                            # branch the worktree off this (default: origin/HEAD)
      -p "<task>"                             # fire-and-forget (mode=task); log at ~/work/<id>.log
      --interactive                           # claude → mode=both (TUI here + phone/web); codex → local TUI
      --local                                 # terminal TUI here only, no remote (implies --interactive)
      --safe                                  # keep permission prompts (default: skipped — pod is the sandbox)
      --model <m>                             # claude MODEL ID (claude-opus-5, claude-fable-5[1m], …) or codex slug;
                                              # all modes. Bare aliases (opus/fable/…) still work but resolve
                                              # client-side against the PINNED CLI and can silently serve an
                                              # older tier — prefer the id. See the model picker comment.
      --effort <level>                        # claude low|medium|high|xhigh|max; codex adds ultra; default xhigh
  agent-run list
  agent-run attach [<task-id>]                # no id → arrow-key picker
  agent-run detach [<task-id>]                # no id → picker (attached sessions only)
  agent-run reap   [<task-id>] [--force]      # no id → picker (incl. stranded worktrees)
  agent-run prune  [--yes] [--force]          # bulk-clean stranded worktrees (dry-run without --yes)
  agent-run codex-remote [stop]               # pod-level codex phone/web control (daemon + pairing code)
EOF
}

# "haynesnetwork-0714-230456" → "07-14 23:04:56"; falls back to the worktree
# dir mtime, then "?". (The id embeds creation time, so no tmux query needed —
# works for stranded worktrees whose session is long gone.)
when_of() {
  local id="$1"
  if [[ "$id" =~ ([0-9]{2})([0-9]{2})-([0-9]{2})([0-9]{2})([0-9]{2})$ ]]; then
    printf '%s-%s %s:%s:%s' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" \
      "${BASH_REMATCH[3]}" "${BASH_REMATCH[4]}" "${BASH_REMATCH[5]}"
  elif [ -e "$WORK/$id" ]; then
    date -d "@$(stat -c %Y "$WORK/$id")" '+%m-%d %H:%M:%S'
  else
    printf '?'
  fi
}

# pick "title" row… — arrow-key menu drawn on /dev/tty (stdout stays clean so
# callers can command-substitute the result). Echoes the selected row's first
# field (the task id). Returns 1 on cancel or when there's nothing to show.
pick() {
  local title="$1"; shift
  local rows=("$@") n=$# cur=0 j key seq
  [ "$n" -gt 0 ] || { log "nothing to select"; return 1; }
  { exec 3</dev/tty 4>/dev/tty; } 2>/dev/null \
    || { log "no TTY for the picker — pass an explicit <task-id>"; return 1; }
  draw() {
    for ((j = 0; j < n; j++)); do
      if [ "$j" -eq "$cur" ]; then printf '\033[K \033[7m▸ %s\033[0m\n' "${rows[j]}"
      else printf '\033[K   %s\n' "${rows[j]}"; fi
    done >&4
  }
  printf '%s  (↑/↓ or j/k, Enter selects, q cancels)\n' "$title" >&4
  printf '\033[?25l' >&4
  while :; do
    draw
    IFS= read -rsn1 key <&3 || key=q
    case "$key" in
      $'\x1b')
        IFS= read -rsn2 -t 0.05 seq <&3 || seq=""
        case "$seq" in
          '[A') ((cur > 0)) && ((cur--)) ;;
          '[B') ((cur < n - 1)) && ((cur++)) ;;
          '') key=q ;;   # bare ESC = cancel
        esac ;;
      k) ((cur > 0)) && ((cur--)) ;;
      j) ((cur < n - 1)) && ((cur++)) ;;
      ''|$'\r') break ;; # Enter = select ('' when the tty maps CR→NL, \r when raw)
    esac
    [ "$key" = q ] && { printf '\033[?25h\n' >&4; return 1; }
    printf '\033[%dA' "$n" >&4
  done
  printf '\033[?25h' >&4
  printf '%s\n' "${rows[cur]%% *}"
}

# Emit "id|attached-count" for every live task session.
live_tasks() {
  tmux ls -F '#{session_name}|#{session_attached}' 2>/dev/null | sed -n 's/^task-//p'
}

# Rows for the repo picker: "name  pushed MM-DD HH:MM", newest activity first.
# GitHub is the source of truth (it shows repos not yet cloned to this pod). Two
# lookup paths because the pod credential is a GitHub App installation token:
# `gh repo list` (GraphQL) covers PATs and app tokens that can search, and REST
# /installation/repositories is the endpoint purpose-built for app tokens. If
# both fail (offline / no token) fall back to the local canonical clones,
# ordered by last fetch — degraded but never empty on a working pod.
# Hardening: re-read /creds/gh_token EVERY call — an inherited GH_TOKEN is a
# 60-min app token that a >1h-old shell holds dead, and the sidecar re-mints the
# file every 40min. And check gh's EXIT STATUS on the REST fallback: on HTTP
# error `gh api` prints the raw JSON error body to stdout (skipping --jq) and
# exits 1, so piping it straight to sort would turn "Bad credentials" into
# picker rows — capture output only on success.
repo_rows() {
  local out name ts
  [ -s /creds/gh_token ] && export GH_TOKEN="$(cat /creds/gh_token)"
  out="$(gh repo list "$OWNER" --limit 100 --no-archived --json name,pushedAt \
           --jq 'sort_by(.pushedAt) | reverse | .[] | "\(.name)|\(.pushedAt)"' 2>/dev/null)"
  if [ -z "$out" ]; then
    if out="$(gh api --paginate /installation/repositories \
             --jq '.repositories[] | select(.archived | not) | "\(.name)|\(.pushed_at)"' 2>/dev/null)"; then
      out="$(sort -t'|' -k2,2 -r <<<"$out")"
    else
      out=""
    fi
  fi
  if [ -n "$out" ]; then
    while IFS='|' read -r name ts; do
      # Require BOTH fields: a row missing its ts (malformed / stray error text)
      # would let `date -d ''` silently emit today's midnight — never a real row.
      [ -n "$name" ] && [ -n "$ts" ] || continue
      printf '%s  pushed %s\n' "$name" "$(date -d "$ts" '+%m-%d %H:%M' 2>/dev/null || printf '%s' "${ts%%T*}")"
    done <<<"$out"
  else
    log "GitHub repo list unavailable — showing local clones only"
    for r in "$REPOS"/*/; do
      [ -d "$r/.git" ] || continue
      printf '%s|%s\n' "$(stat -c %Y "$r/.git/FETCH_HEAD" 2>/dev/null || stat -c %Y "$r/.git")" "$(basename "$r")"
    done | sort -t'|' -k1,1 -rn | while IFS='|' read -r _ name; do
      printf '%s  (local clone)\n' "$name"
    done
  fi
}

# Resolve the canonical repo working dir that OWNS a linked worktree — works for
# ANY worktree name and either repo. (reap USED to guess the repo from the id
# prefix via `${id%%-[0-9]*}`, which broke on agent-made worktrees like
# 'hnet-award-order-fix' → a nonexistent repo path → git erroring → the
# misleading "worktree dirty" die. Ask git who owns it instead.) The common dir
# is '<repo>/.git', so strip the trailing '/.git'. Nonzero if not a git worktree.
owning_repo() {  # $1 = worktree path → echoes repo dir
  local gd
  gd="$(git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 1
  [ -n "$gd" ] || return 1
  printf '%s\n' "${gd%/.git}"
}

# Count uncommitted TRACKED changes (staged/unstaged edits, adds, deletes,
# renames of files git tracks). This — NOT untracked files — is the real
# "unsaved work" signal: agents commit + open a PR before a worktree is
# abandoned, so a stranded worktree with zero tracked-dirty has everything
# safely committed (and, per the branch's merged PR, landed). Untracked files
# are runtime scratch — `.claude/` (scheduled_tasks.lock, settings.json),
# node_modules, build dirs — which legitimately blocks a plain `git worktree
# remove` yet is disposable. Prints an integer.
tracked_dirty() {  # $1 = worktree path
  git -C "$1" status --porcelain=v1 2>/dev/null | grep -vcE '^(\?\?|!!)' || true
}

# Remove ONE worktree (forcing past disposable untracked scratch), then retire the
# branch it had checked out — SAFELY, OFFLINE. Caller has cleared the tracked-dirty
# gate, but a clean tree can still hide committed-but-never-pushed commits, and once
# the worktree (+ its HEAD reflog) is gone the branch ref is those commits' only
# anchor. So delete ONLY when git can PROVE offline the branch holds nothing beyond
# HEAD: `git branch -d` (deletes plain/FF-merged or already-at-base; refuses if the
# branch has its own commits). On refusal — unpushed WIP OR squash-merged (upstream
# deleted, history rewritten) — we do NOT force: a name-only "a PR merged" signal is
# not proof THIS local tip landed (stacked commits / reused branch names both defeat
# it), so we never escalate to -D. Keep the branch (weightless) and print the exact
# drop command. Never touches a shared base branch. $1 = worktree, $2 = repo dir.
drop_worktree() {
  local wt="$1" repo="$2" br tip
  br="$(git -C "$wt" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  tip="$(git -C "$wt" rev-parse --short HEAD 2>/dev/null)"
  git -C "$repo" worktree remove --force "$wt" || return 1
  case "$br" in ""|HEAD|main|master) return 0 ;; esac
  if git -C "$repo" branch -d "$br" >/dev/null 2>&1; then
    log "branch $br deleted"
  else
    log "KEPT branch $br @ $tip — has local commit(s) not on HEAD (unpushed WIP, or squash-merged). Confirm it landed, then drop: git -C $repo branch -D $br"
  fi
  return 0
}

# Pre-trust a fresh worktree in claude's state file: `claude remote-control`
# REFUSES untrusted dirs outright, and the TUI stops at a trust dialog — both
# kill "RC from the get-go" for worktrees that are, by construction, brand new.
# Best-effort (atomic tmp+mv): on any failure the session just shows the normal
# trust prompt. State file: $CLAUDE_CONFIG_DIR/.claude.json (this pod sets
# CLAUDE_CONFIG_DIR), else ~/.claude.json.
pretrust() {  # $1 = worktree path
  local state="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.claude.json"
  [ -f "$state" ] || state="$HOME/.claude.json"
  [ -f "$state" ] || { log "WARN: no claude state file yet — accept the trust dialog on first attach"; return 0; }
  local tmp="${state}.agent-run.$$"
  if jq --arg wt "$1" \
       '.projects[$wt] = ((.projects[$wt] // {}) + {hasTrustDialogAccepted: true, hasCompletedProjectOnboarding: true})' \
       "$state" > "$tmp" 2>/dev/null && [ -s "$tmp" ]; then
    mv "$tmp" "$state"
  else
    rm -f "$tmp"; log "WARN: could not pre-trust $1 — accept the trust dialog on first attach"
  fi
}

# ---- picker row sources (pick() returns each row's first token as the value) ----
#
# Claude's effort levels, read from the INSTALLED CLI rather than hardcoded here:
# `claude --help` prints them as "(low, medium, high, xhigh, max)" under
# `--effort <level>`. A Claude Code upgrade that adds or retires a level then
# flows through to both the picker and --effort validation with no edit to this
# script. Falls back to the known-good set if the help text ever changes shape.
# Unlike codex, claude's levels are uniform across models — no per-model table.
claude_effort_levels() {
  local parsed
  parsed="$(claude --help 2>/dev/null \
    | grep -A1 -e '--effort <level>' | tr -d '\n' \
    | grep -oE '\(([a-z]+, )+[a-z]+\)' | tr -d '() ')"
  case "$parsed" in
    *[a-z]*) printf '%s' "$parsed" ;;
    *)       printf 'low,medium,high,xhigh,max' ;;
  esac
}

# Picker rows for claude, xhigh first (the dev-env default). Levels are ordered
# by the preference list below; anything the CLI reports that isn't in it is
# appended verbatim, so a newly-added level still shows up (unlabelled).
claude_effort_rows() {
  local levels lvl note
  levels=",$(claude_effort_levels),"
  for lvl in xhigh max high medium low; do
    case "$levels" in *",$lvl,"*) ;; *) continue ;; esac
    case "$lvl" in
      xhigh)  note='   (dev-env default)' ;;
      max)    note='     (hardest problems, slowest)' ;;
      high)   note="    (claude's stock default)" ;;
      *)      note='' ;;
    esac
    printf '%s%s\n' "$lvl" "$note"
  done
  for lvl in ${levels//,/ }; do
    case " xhigh max high medium low " in *" $lvl "*) ;; *) printf '%s\n' "$lvl" ;; esac
  done
}

# Codex's catalog, unlike claude's, IS machine-readable locally: codex maintains
# ~/.codex/models_cache.json (slug, display_name, description, per-model
# supported_reasoning_levels, visibility, priority), refreshes it from the server
# as it runs, and the home PVC carries it across pod bounces. Codex model AND
# effort rows generate from it, so new codex models/levels show up with no edit
# to this script. The hardcoded fallbacks below only serve a home where codex has
# never run; when the live list drifts from the fallback, the picker WARNs so the
# fallback gets refreshed (dev-env PR) instead of rotting silently.
CODEX_CACHE="${CODEX_HOME:-$HOME/.codex}/models_cache.json"

codex_model_rows_fallback() {
  printf '%s\n' \
    'gpt-5.6-sol    GPT-5.6-Sol · latest frontier agentic coding' \
    'gpt-5.6-terra  GPT-5.6-Terra · balanced everyday' \
    'gpt-5.6-luna   GPT-5.6-Luna · fast & affordable' \
    'gpt-5.5        GPT-5.5 · prior frontier' \
    'gpt-5.4        GPT-5.4 · everyday coding' \
    'gpt-5.4-mini   GPT-5.4-Mini · small & fast'
}

# Visible models in priority order: "slug  DisplayName · description".
codex_model_rows() {
  local live
  live="$(jq -r '.models | map(select(.visibility == "list")) | sort_by(.priority)[]
                 | .slug + "\t" + .display_name + " · " + (.description | rtrimstr("."))' \
          "$CODEX_CACHE" 2>/dev/null | awk -F'\t' '{ printf "%-15s%s\n", $1, $2 }')"
  if [ -z "$live" ]; then codex_model_rows_fallback; return; fi
  if [ "$(printf '%s\n' "$live" | awk '{print $1}' | sort)" \
    != "$(codex_model_rows_fallback | awk '{print $1}' | sort)" ]; then
    log "WARN: codex fallback rows are stale vs models_cache.json — refresh codex_model_rows_fallback in agent-run.sh (held-draft dev-env PR)"
  fi
  printf '%s\n' "$live"
}

# Union of effort levels across all cached models — used for --effort validation
# only; a level a given model doesn't advertise is clamped server-side.
codex_effort_levels() {
  local parsed
  parsed="$(jq -r '[.models[].supported_reasoning_levels[].effort] | unique | join(",")' \
    "$CODEX_CACHE" 2>/dev/null)"
  case "$parsed" in
    *[a-z]*) printf '%s' "$parsed" ;;
    *)       printf 'low,medium,high,xhigh,max,ultra' ;;
  esac
}

# Effort rows for the given model, xhigh first (the dev-env default), read from
# the cache; levels the preference list doesn't know are appended unlabelled —
# same contract as claude_effort_rows. Blank/unknown model or no cache → the
# hardcoded per-model fallback table.
codex_effort_rows() {
  local levels lvl note
  levels="$(jq -r --arg m "$1" \
    'first(.models[] | select(.slug == $m)) | [.supported_reasoning_levels[].effort] | join(",")' \
    "$CODEX_CACHE" 2>/dev/null)"
  if [ -z "$levels" ]; then codex_effort_rows_fallback "$1"; return; fi
  levels=",$levels,"
  for lvl in xhigh ultra max high medium low; do
    case "$levels" in *",$lvl,"*) ;; *) continue ;; esac
    case "$lvl" in
      xhigh) note='   (dev-env default)' ;;
      ultra) note='   (max reasoning + auto-delegation)' ;;
      *)     note='' ;;
    esac
    printf '%s%s\n' "$lvl" "$note"
  done
  for lvl in ${levels//,/ }; do
    case " xhigh ultra max high medium low " in *" $lvl "*) ;; *) printf '%s\n' "$lvl" ;; esac
  done
}

codex_effort_rows_fallback() {
  case "$1" in
    gpt-5.6-sol|gpt-5.6-terra)
      printf '%s\n' 'xhigh   (dev-env default)' 'ultra   (max reasoning + auto-delegation)' 'max' 'high' 'medium' 'low' ;;
    gpt-5.6-luna)
      printf '%s\n' 'xhigh   (dev-env default)' 'max' 'high' 'medium' 'low' ;;
    *)
      printf '%s\n' 'xhigh   (dev-env default)' 'high' 'medium' 'low' ;;
  esac
}

# Every task shell needs the fresh bot token (bashrc handles interactive shells;
# tmux run-shell strings need it inline).
token_env='export GH_TOKEN="$(cat /creds/gh_token 2>/dev/null)"'

# Command routing: a leading subcommand word (list/attach/…) dispatches to that
# arm; ANYTHING else — bare `agent-run`, a repo name, or a -flag — is a run, so
# `agent-run` alone starts the guided walkthrough. `run` stays a hidden alias.
case "${1:-}" in
  run)                                          shift; cmd=run ;;
  list|attach|detach|reap|prune|codex-remote)   cmd="$1"; shift ;;
  help|-h|--help)                               usage; exit 0 ;;
  *)
    # Bare word, not a flag, no such local clone: before treating it as the
    # repo shorthand, catch subcommand typos — `detatch` used to sail through
    # the whole run walkthrough and die at clone time with a misleading error.
    # A real repo whose name shadows a typo is forced with --repo <name>.
    if [ -n "${1:-}" ] && [ "${1#-}" = "${1:-}" ] && [ ! -d "$REPOS/$1/.git" ]; then
      sug="$(did_you_mean "$1" run list attach detach reap prune codex-remote help)"
      [ -n "$sug" ] && die "unknown command '$1' — did you mean '$sug'? (for a repo really named '$1', use --repo $1)"
    fi
    cmd=run ;;   # bare / repo / -flag → run (keep $@)
esac

case "$cmd" in
  run)
    repo="" agent="" base="" prompt="" interactive=0 safe=0 local_tui=0 model="" effort=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --repo) repo="$2"; shift 2 ;;
        --agent) agent="$2"; shift 2 ;;
        --base) base="$2"; shift 2 ;;
        -p|--prompt) prompt="$2"; shift 2 ;;
        --interactive) interactive=1; shift ;;
        --local) local_tui=1; shift ;;
        --model) model="$2"; shift 2 ;;
        --effort) effort="$2"; shift 2 ;;
        --safe) safe=1; shift ;;
        -*) sug="$(did_you_mean "$1" --repo --agent --base -p --prompt --interactive --local --model --effort --safe)"
            die "unknown flag $1${sug:+ — did you mean '$sug'?}" ;;
        *) [ -z "$repo" ] || die "unexpected argument '$1' (repo already '$repo')"
           repo="$1"; shift ;;   # positional shorthand: agent-run run <repo>
      esac
    done
    # Anything not pinned by a flag is asked interactively — bare `agent-run run`
    # walks repo → agent → mode → effort, so nobody has to remember exact repo
    # names. Pickers need a TTY; scripted callers pass flags and never see a prompt.
    asked=0
    if [ -z "$repo" ]; then
      asked=1
      mapfile -t rows < <(repo_rows)
      repo="$(pick 'run an agent in repo:' ${rows[@]+"${rows[@]}"})" || die "--repo required"
    fi
    if [ -z "$agent" ]; then
      asked=1
      agent="$(pick 'agent:' \
        'claude  Claude Code (Max plan)' \
        'codex   Codex CLI (ChatGPT plan)')" || die "--agent must be claude|codex"
    fi
    case "$agent" in claude|codex) : ;; *) die "--agent must be claude|codex" ;; esac
    # Validate --effort against the SELECTED tool's levels — fail fast, before any
    # clone/worktree side effects (the repo/agent pickers above are read-only).
    # claude: whatever `claude --help` advertises (uniform across models) — read
    # live so this stays right across upgrades, same source as the picker.
    # codex: the union across models_cache.json (fallback: the known set); a level
    # a given model doesn't advertise is clamped server-side, so no per-model fail.
    if [ -n "$effort" ]; then
      case "$agent" in
        claude) case ",$(claude_effort_levels)," in *",$effort,"*) : ;;
          *) die "claude --effort must be one of $(claude_effort_levels) (got '$effort'); 'ultracode' is a /effort session-mode — set it in-session" ;; esac ;;
        codex)  case ",$(codex_effort_levels)," in *",$effort,"*) : ;;
          *) die "codex --effort must be one of $(codex_effort_levels) (got '$effort')" ;; esac ;;
      esac
    fi
    # --- mode: how to drive it. Resolve from flags first (so no combination lands
    # in a picker that contradicts a flag), then a single picker if unresolved.
    #   both  = terminal TUI here + drive from phone/web (claude --remote-control) — claude only
    #   local = terminal TUI here only
    #   task  = fire-and-forget (-p; runs headless, logs to a file)
    # Flag pins: -p → task; --local → local (implies interactive); --interactive alone
    # → both (claude) / local (codex, which has no per-session remote — see codex-remote).
    # -p is mutually exclusive with the interactive flags — reject the contradiction
    # rather than silently dropping the task prompt (or ignoring the flag).
    [ -n "$prompt" ] && { [ "$interactive" = 1 ] || [ "$local_tui" = 1 ]; } \
      && die "-p (fire-and-forget) can't combine with --interactive/--local — pick one"
    mode=""
    if [ -n "$prompt" ]; then
      mode=task
    elif [ "$local_tui" = 1 ]; then
      mode=local; interactive=1
    elif [ "$interactive" = 1 ]; then
      [ "$agent" = claude ] && mode=both || mode=local
    fi
    if [ -z "$mode" ]; then
      if { : </dev/tty; } 2>/dev/null; then
        asked=1
        if [ "$agent" = claude ]; then
          mode="$(pick 'run claude — how do you want to drive it?' \
            'both    terminal here + drive from phone/claude.ai/code  (recommended)' \
            'local   terminal here only (no phone/web)' \
            'task    fire-and-forget: type a prompt, walk away — logs to ~/work/<id>.log')" \
            || die "mode required (or pass -p / --interactive / --local)"
        else
          mode="$(pick 'run codex — how to drive it?  (phone/web is pod-level → agent-run codex-remote)' \
            'local   terminal here (codex TUI)' \
            'task    fire-and-forget: type a prompt, walk away — logs to ~/work/<id>.log')" \
            || die "mode required (or pass -p / --interactive / --local)"
        fi
        case "$mode" in
          both|local) interactive=1 ;;
          task) printf 'task prompt> ' >/dev/tty
                IFS= read -r prompt </dev/tty || prompt=""
                [ -n "$prompt" ] || die "a fire-and-forget run needs a task prompt" ;;
        esac
      else
        die "need -p, --interactive, or --local"
      fi
    fi
    # --- model picker (menu mode) ---
    # Every claude mode takes --model as a top-level flag (it composes with
    # --remote-control too); codex takes -m. The effort picker below then filters to
    # the model's levels (only varies for codex).
    if [ -z "$model" ] && [ "$asked" = 1 ]; then
      if [ "$agent" = claude ]; then
        # The VALUES here are PINNED IDS, not aliases — deliberately (2026-08-29).
        # `opus`/`sonnet`/`haiku`/`fable` resolve CLIENT-SIDE, against a table baked
        # into the installed CLI, and this pod's CLI is version-pinned in the image
        # (Dockerfile ARG CLAUDE_CODE_VERSION). So an alias tracks "latest tier the
        # PINNED BINARY knows about", NOT "the latest tier that exists" — and when
        # the image lags a launch it SILENTLY SERVES THE OLDER MODEL, with no
        # warning anywhere. Proven in-pod on claude-code 2.1.217: `--model opus` →
        # claude-opus-4-8, while `--model claude-opus-5` → claude-opus-5 on the very
        # same binary. Full ids are resolved SERVER-SIDE, so they work even when
        # they are newer than the CLI's alias table — which makes them strictly
        # safer here: what you pick is what you get, and a stale image can never
        # quietly downgrade a session.
        # REFRESH both halves of a row when a new tier lands:
        #   claude --help | grep -A4 -- '--model'      # what the CLI advertises
        #   claude --model <id> -p 'model id?'          # what the API actually serves
        # ⚠ Agents: these ids may be NEWER than your training data. Before
        # "correcting" one downward, probe it live with the command above — an agent
        # reverted Opus 5→4.8 on 2026-08-06 because its cutoff predated the launch.
        model="$(pick 'model:' \
          'claude-fable-5[1m]  Fable 5 · 1M ctx, most capable (dev-env default)' \
          'claude-opus-5       Opus 5 · deep reasoning + agentic coding' \
          'claude-sonnet-5     Sonnet 5 · near-Opus quality, cheaper' \
          'claude-haiku-4-5    Haiku 4.5 · fastest, simple tasks')" || model=""
        # No "leave it unset" row any more: ~/.claude/settings.json lives on the PVC,
        # not in the ConfigMap, so ANY in-session `/model` rewrites the pod-wide
        # default for every future session (that is how the documented
        # claude-fable-5[1m] default became `opus` — i.e. Opus 4.8 — on 2026-08-29).
        # Passing the id explicitly makes the picker's choice authoritative.
      else
        # Live from models_cache.json (priority order; top row is codex's own
        # default tier). Cancel leaves $model empty → codex's default stands.
        mapfile -t mrows < <(codex_model_rows)
        model="$(pick 'model:' "${mrows[@]}")" || model=""
      fi
    fi

    # --- effort (deterministic default xhigh; menu filters to the model's set) ---
    # Historically NOTHING set effort — no flag reached RC hosts, no settings key,
    # no env var — so every session silently ran at the model default. Now every
    # run gets a level: a menu shows a picker (claude's levels are uniform across
    # models; codex's are filtered to the selected model), a flagged call takes the
    # xhigh default. xhigh is valid for every current claude AND codex model.
    if [ -z "$effort" ]; then
      if [ "$asked" = 1 ]; then
        if [ "$agent" = claude ]; then
          mapfile -t erows < <(claude_effort_rows)
          effort="$(pick 'effort:' "${erows[@]}")" || effort=xhigh
        else
          mapfile -t erows < <(codex_effort_rows "$model")
          effort="$(pick 'effort:' "${erows[@]}")" || effort=xhigh
        fi
      else
        effort=xhigh
      fi
    fi

    # Canonical clone: fetch-only, never worked in directly. Fresh token first —
    # same footgun as repo_rows(): an inherited GH_TOKEN outlives its 60-min mint,
    # and a credential-helper hijack (gh auth setup-git) makes git present it.
    [ -s /creds/gh_token ] && export GH_TOKEN="$(cat /creds/gh_token)"
    if [ ! -d "$REPOS/$repo/.git" ]; then
      log "cloning $OWNER/$repo…"
      GIT_TERMINAL_PROMPT=0 git clone "https://github.com/$OWNER/$repo" "$REPOS/$repo" \
        || die "clone failed (private repo not granted to haynes-ops-bot?)"
    fi
    git -C "$REPOS/$repo" fetch origin --prune || die "fetch failed"
    [ -n "$base" ] || base="origin/$(git -C "$REPOS/$repo" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | cut -d/ -f2 || echo main)"

    id="${repo}-$(date +%m%d-%H%M%S)"
    wt="$WORK/$id"
    git -C "$REPOS/$repo" worktree add "$wt" -b "agent/$id" "$base" >/dev/null \
      || die "worktree add failed"
    log "task $id  worktree=$wt  branch=agent/$id  base=$base"

    guard="You are working in an isolated git worktree ($wt) on branch agent/$id. \
Stay inside it. NEVER push to main — commit to agent/$id, push that branch, and \
open a PR with 'gh pr create' when the task is complete. If the task needs extra \
git worktrees, create them under $WORK and 'git worktree remove' each once its PR \
merges — never leave stranded worktrees behind. If blocked, write BLOCKED and the \
reason as your final message."

    # Model/effort per tool. CLAUDE reads them as TOP-LEVEL flags (--model/--effort);
    # they compose with `--remote-control` too, so every claude mode uses $cg uniformly.
    # CODEX reads `-m <model>` + `-c model_reasoning_effort=<level>` (codex exec AND the
    # TUI). %q-escape the values: they ride through one more shell layer (tmux send-keys
    # re-parses the launch string), and a legit value like 'claude-fable-5[1m]' carries
    # glob metacharacters that would otherwise be pathname-expanded.
    cg="" xc=""
    if [ -n "$model" ];  then cg="$cg --model $(printf '%q' "$model")";  xc="$xc -m $(printf '%q' "$model")"; fi
    if [ -n "$effort" ]; then cg="$cg --effort $(printf '%q' "$effort")"; xc="$xc -c model_reasoning_effort=$(printf '%q' "$effort")"; fi

    case "$agent" in
      claude) run_cmd="claude$cg --dangerously-skip-permissions --append-system-prompt \"\$AGENT_GUARD\" -p \"\$AGENT_PROMPT\" --output-format text" ;;
      codex)  run_cmd="codex exec$xc --dangerously-bypass-approvals-and-sandbox \"\$AGENT_GUARD \$AGENT_PROMPT\"" ;;
    esac

    # YOLO BY DEFAULT — interactive too. The whole point of this pod is that the
    # sandbox IS the environment: an isolated worktree, a scoped SA, a default-deny
    # egress policy. Approving every edit inside that is theater. `--safe` opts out
    # (normal permission prompts) when you want to babysit something.
    # NOTE: codex has no --yolo alias; the flag is the long dangerous- one.
    yolo_flag=""
    if [ "$safe" != 1 ]; then
      case "$agent" in
        claude) yolo_flag="--dangerously-skip-permissions" ;;
        codex)  yolo_flag="--dangerously-bypass-approvals-and-sandbox" ;;
      esac
    fi

    tmux new-session -d -s "task-$id" -c "$wt" || die "tmux session failed"
    if [ "$interactive" = 1 ]; then
      if [ "$agent" = claude ]; then
        pretrust "$wt"   # a fresh worktree blocks on the trust dialog in either claude mode
        # both  = `claude --remote-control <id>`: a terminal TUI you type to HERE and a
        #         session drivable from phone/claude.ai/code, registered via the reliable
        #         api.anthropic.com/v1/code/sessions path. local = the same TUI, no remote.
        # --model/--effort ($cg) compose as normal top-level flags in both. Permission
        # bypass comes from the pod settings.json default (no launch flag); --safe restores
        # prompts via --permission-mode default (verified to coexist with --remote-control).
        if [ "$mode" = both ]; then
          # The long-lived CLAUDE_CODE_OAUTH_TOKEN env (saga 04 hardening) CANNOT
          # register /v1/code/sessions — the session silently never appears on the
          # phone/web remote list (A/B-proven in-pod 2026-08-29). Strip it for
          # `both` only, falling back to ~/.claude/.credentials.json (Max login);
          # local TUI, task mode, and auth-watch stay on the env token.
          launch="env -u CLAUDE_CODE_OAUTH_TOKEN claude$cg --remote-control $id"
          [ "$safe" = 1 ] && launch="$launch --permission-mode default"
        else
          launch="claude$cg"
          [ "$safe" = 1 ] && launch="claude$cg --permission-mode default"
        fi
      else
        # codex interactive = local terminal TUI. Codex phone/web control is a per-pod
        # daemon (one socket, no per-dir binding), NOT per-task → `agent-run codex-remote`.
        launch="codex$xc $yolo_flag"
      fi
      tmux send-keys -t "task-$id" "$token_env; cd $wt; $launch" Enter
      case "$agent:$mode" in
        claude:both)  log "claude TUI + remote-control '$id' up (model=${model:-pod-default} effort=${effort:-default}) — this pane AND phone/web →  agent-run attach $id" ;;
        claude:local) log "claude local TUI ready (model=${model:-pod-default} effort=${effort:-default}; no remote) →  agent-run attach $id" ;;
        codex:*)      log "codex local TUI ready (model=${model:-default} effort=${effort:-default}; phone/web → agent-run codex-remote) →  agent-run attach $id" ;;
      esac
    else
      # Env-var indirection keeps the prompt out of shell-quoting hell; log to
      # $WORK/<id>.log for post-hoc review (reap keeps the log).
      tmux send-keys -t "task-$id" "AGENT_GUARD=$(printf '%q' "$guard") AGENT_PROMPT=$(printf '%q' "$prompt"); $token_env" Enter
      tmux send-keys -t "task-$id" "$run_cmd 2>&1 | tee $WORK/$id.log; echo TASK-EXIT:\$? >> $WORK/$id.log" Enter
      log "dispatched. follow: agent-run attach $id   log: $WORK/$id.log"
    fi
    printf '%s\n' "$id"
    ;;
  list)
    # Print the TASK ID (not the raw tmux session name) plus a copy-pasteable command.
    # The old version printed 'task-<id>', which people then passed to `attach` — and
    # attach re-prefixed it into 'task-task-<id>' and failed. Show exactly what to type.
    printf 'LIVE TASKS:\n'
    found=0
    while IFS='|' read -r sid att; do
      found=1
      printf '  %s  (started %s%s)\n      attach:  agent-run attach %s\n' \
        "$sid" "$(when_of "$sid")" "$([ "${att:-0}" -gt 0 ] && echo ', attached')" "$sid"
    done < <(live_tasks)
    [ "$found" = 1 ] || printf '  (none)\n'
    printf 'WORKTREES:\n'
    for r in "$REPOS"/*/; do [ -d "$r/.git" ] && git -C "$r" worktree list | grep "$WORK" || true; done
    strand=0
    for wt in "$WORK"/*/; do
      [ -d "$wt" ] || continue
      tmux has-session -t "task-$(basename "$wt")" 2>/dev/null && continue
      owning_repo "$wt" >/dev/null 2>&1 && strand=$((strand + 1))   # count what prune acts on
    done
    [ "$strand" -gt 0 ] && printf 'STRANDED: %d worktree(s) with no live session → agent-run prune\n' "$strand"
    tmux has-session -t codex-remote 2>/dev/null && [ -S "$HOME/.codex/app-server-control/app-server-control.sock" ] \
      && printf 'CODEX REMOTE: active (pod-level) — agent-run codex-remote (re-pair) | agent-run codex-remote stop\n'
    true   # never let the trailing test set a nonzero exit for `agent-run list`
    ;;
  attach)
    id="${1:-}"
    if [ -z "$id" ]; then
      rows=()
      while IFS='|' read -r sid att; do
        rows+=("$sid  started $(when_of "$sid")  $([ "${att:-0}" -gt 0 ] && echo '[attached]' || echo '[detached]')")
      done < <(live_tasks)
      id="$(pick 'attach to task:' ${rows[@]+"${rows[@]}"})" || exit 1
    fi
    id="${id#task-}"   # tolerate pasting the tmux session name by mistake
    tmux has-session -t "task-$id" 2>/dev/null \
      || die "no live session '$id' — run 'agent-run list' to see what's running"
    # Nested-tmux aware: inside tmux, attach would die with "sessions should be
    # nested with care" — switch this client instead.
    if [ -n "${TMUX:-}" ]; then
      [ "$(tmux display-message -p '#S')" = "task-$id" ] \
        && { log "you are already inside task-$id"; exit 0; }
      exec tmux switch-client -t "task-$id"
    fi
    exec tmux attach -t "task-$id"
    ;;
  detach)
    # Detach every client from a task's session — the session (and the agent in
    # it) keeps running. This exists because code-server swallows Ctrl+B (its
    # sidebar shortcut), so the in-tmux detach keystroke never arrives there.
    id="${1:-}"
    if [ -z "$id" ]; then
      rows=()
      while IFS='|' read -r sid att; do
        [ "${att:-0}" -gt 0 ] || continue
        rows+=("$sid  started $(when_of "$sid")  [$att attached]")
      done < <(live_tasks)
      id="$(pick 'detach clients from task:' ${rows[@]+"${rows[@]}"})" || exit 1
    fi
    id="${id#task-}"
    tmux has-session -t "task-$id" 2>/dev/null \
      || die "no live session '$id' — run 'agent-run list' to see what's running"
    tmux detach-client -s "task-$id"
    log "detached clients from task-$id (session keeps running)"
    ;;
  reap)
    id="${1:-}" force="" picked=0
    if [ "$id" = "--force" ]; then force="--force"; id=""; fi
    [ "${2:-}" = "--force" ] && force="--force"
    if [ -z "$id" ]; then
      rows=()
      while IFS='|' read -r sid _att; do
        rows+=("$sid  started $(when_of "$sid")  [live session]")
      done < <(live_tasks)
      for wt in "$WORK"/*/; do
        [ -d "$wt" ] || continue
        wid="$(basename "$wt")"
        tmux has-session -t "task-$wid" 2>/dev/null && continue
        rows+=("$wid  started $(when_of "$wid")  [stranded — worktree only]")
      done
      id="$(pick 'reap task (kills session + deletes worktree; log kept):' ${rows[@]+"${rows[@]}"})" || exit 1
      picked=1
    fi
    id="${id#task-}"   # same tolerance as attach
    if [ "$picked" = 1 ]; then
      # Picker mode reaps whatever Enter landed on — confirm before deleting.
      printf 'reap %s? [y/N] ' "$id" >/dev/tty
      IFS= read -r ans </dev/tty || ans=n
      case "$ans" in y|Y|yes) : ;; *) log "aborted"; exit 1 ;; esac
    fi
    tmux kill-session -t "task-$id" 2>/dev/null && log "session killed" || log "no session"
    wt="$WORK/$id"
    if [ -d "$wt" ]; then
      repo_dir="$(owning_repo "$wt")" \
        || die "$id is not a git worktree — if it's a stray dir, remove it by hand"
      td="$(tracked_dirty "$wt")"
      if [ "${td:-0}" -gt 0 ] && [ "$force" != "--force" ]; then
        log "WON'T reap $id — $td uncommitted TRACKED change(s) (real WIP):"
        git -C "$wt" status --porcelain=v1 2>/dev/null | grep -vE '^(\?\?|!!)' | sed 's/^/    /' >&2
        die "commit/push first, or 'agent-run reap $id --force' to discard"
      fi
      ut="$(git -C "$wt" status --porcelain=v1 2>/dev/null | grep -cE '^\?\?' || true)"
      [ "${ut:-0}" -gt 0 ] && log "discarding $ut untracked scratch file(s) (.claude/ etc.) in $id"
      drop_worktree "$wt" "$repo_dir" && log "worktree removed" \
        || die "worktree remove failed for $wt"
      git -C "$repo_dir" worktree prune 2>/dev/null || true
    else
      log "no worktree dir for $id (already gone)"
    fi
    log "reaped $id (log kept at $WORK/$id.log)"
    ;;
  prune)
    # Bulk-clean STRANDED worktrees under $WORK (dead session, PVC kept the dir) so
    # they don't balloon the PVC — the reap picker one-at-a-time doesn't scale to a
    # backlog. Default is a DRY-RUN plan; add --yes to execute. SAFE: skips any
    # worktree with uncommitted TRACKED changes (real WIP) unless --force; untracked
    # scratch (.claude/ etc.) is always discarded. Ends by pruning stale worktree
    # admin entries in every repo.
    do_it="" pforce=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --yes|-y) do_it=1; shift ;;
        --force)  pforce=1; shift ;;
        *) die "unknown flag $1 (usage: agent-run prune [--yes] [--force])" ;;
      esac
    done
    plan=0 wip=0 removed=0 forced=0 failed=0
    for wt in "$WORK"/*/; do
      [ -d "$wt" ] || continue
      wt="${wt%/}"; wid="$(basename "$wt")"
      tmux has-session -t "task-$wid" 2>/dev/null && continue   # live → leave it
      repo_dir="$(owning_repo "$wt")" || { printf '  skip  %-34s (not a git worktree)\n' "$wid"; continue; }
      br="$(git -C "$wt" rev-parse --abbrev-ref HEAD 2>/dev/null)"
      td="$(tracked_dirty "$wt")"
      note="" is_wip=""
      if [ "${td:-0}" -gt 0 ]; then
        if [ -z "$pforce" ]; then
          printf '  SKIP  %-34s %s  (%s tracked WIP — prune --yes --force to discard)\n' "$wid" "$br" "$td"
          wip=$((wip + 1)); continue
        fi
        note="  ⚠ discards $td tracked WIP"; is_wip=1   # --force: shown, never hidden
      fi
      plan=$((plan + 1))
      if [ -n "$do_it" ]; then
        if drop_worktree "$wt" "$repo_dir"; then
          printf '  REAP  %-34s %s%s\n' "$wid" "$br" "$note"
          removed=$((removed + 1)); [ -n "$is_wip" ] && forced=$((forced + 1))
        else
          printf '  FAIL  %-34s %s  (worktree remove failed)\n' "$wid" "$br"; failed=$((failed + 1))
        fi
      else
        printf '  reap  %-34s %s%s\n' "$wid" "$br" "$note"
        [ -n "$is_wip" ] && forced=$((forced + 1))
      fi
    done
    for r in "$REPOS"/*/; do [ -d "$r/.git" ] && git -C "$r" worktree prune 2>/dev/null || true; done
    if [ -n "$do_it" ]; then
      log "prune done: $removed removed ($forced force-discarded WIP), $failed failed, $wip skipped (WIP)"
    else
      log "DRY-RUN: $plan would be reaped ($forced discarding tracked WIP), $wip skipped (WIP). Execute:  agent-run prune --yes${pforce:+ --force}"
    fi
    ;;
  codex-remote)
    # Pod-level codex phone/web control. codex has NO per-session --remote-control
    # like claude; its remote is a single per-machine app-server daemon (one socket,
    # no --cwd). So this is pod-wide, not per-worktree: enable it, then drive codex
    # from the ChatGPT app / Codex web, where the PHONE picks the working dir —
    # which bypasses agent-run's per-worktree isolation. `stop` to shut it down.
    sub="${1:-start}"
    sock="$HOME/.codex/app-server-control/app-server-control.sock"
    sess="codex-remote"
    case "$sub" in
      stop)
        tmux kill-session -t "$sess" 2>/dev/null || true
        pkill -f 'app-server' 2>/dev/null || true   # `codex remote-control stop` HANGS — kill the daemon directly
        rm -f "$sock"
        log "codex remote-control stopped (pod-level)"
        ;;
      start|"")
        if tmux has-session -t "$sess" 2>/dev/null && [ -S "$sock" ]; then
          log "codex remote-control already running — re-pairing"   # idempotent: one daemon per pod
        else
          # The PVC keeps ~/.codex across pod rolls, so a stale socket can block the
          # bind — clear it when no live daemon owns it.
          [ -S "$sock" ] && ! pgrep -f 'app-server' >/dev/null 2>&1 && rm -f "$sock"
          tmux kill-session -t "$sess" 2>/dev/null || true
          tmux new-session -d -s "$sess" -c "$HOME" || die "tmux session failed"
          tmux send-keys -t "$sess" "$token_env; codex remote-control start" Enter  # token_env → phone-driven git/PRs auth
          for _ in $(seq 1 100); do [ -S "$sock" ] && break; sleep 0.2; done   # up to 20s for the daemon to bind the socket
          [ -S "$sock" ] || die "codex remote-control did not come up (no socket) — inspect: tmux attach -t $sess"
        fi
        code="$(codex remote-control pair --json 2>/dev/null | jq -r '.manualPairingCode // empty')"
        [ -n "$code" ] || die "pairing failed — inspect: tmux attach -t $sess"
        printf '\n  CODEX REMOTE-CONTROL (pod-level — drive from the ChatGPT app / Codex web)\n'
        printf '  pairing code:  %s\n\n' "$code"
        printf '  NOTE: pod-level — the phone picks the working dir, which BYPASSES\n'
        printf '        agent-run per-worktree isolation.   stop: agent-run codex-remote stop\n\n'
        ;;
      *) die "usage: agent-run codex-remote [stop]" ;;
    esac
    ;;
  *)
    usage; exit 1
    ;;
esac
