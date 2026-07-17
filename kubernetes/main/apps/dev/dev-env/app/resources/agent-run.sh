#!/usr/bin/env bash
# agent-run — worktree-per-task agent dispatcher (saga plan 06). Isolation by
# construction: every task gets its own git worktree + branch + tmux session + log;
# concurrent agents in the same repo never collide. GitOps-managed — edit in
# kubernetes/main/apps/dev/dev-env/app/resources/agent-run.sh.
#
#   agent-run run  --repo <name> --agent claude|codex [--base <ref>] -p "<task>"
#   agent-run run  --repo <name> --agent claude --interactive   # REMOTE-CONTROL host (phone/web drives it)
#                                                 [--local]     # classic in-terminal TUI instead
#                                                 [--safe]      # keep permission prompts
#   agent-run list                                              # live tasks + attach cmds
#   agent-run attach [<task-id>]                # jump into a session (no id → picker)
#   agent-run detach [<task-id>]                # kick attached clients off (no id → picker)
#   agent-run reap   [<task-id>] [--force]      # kill + cleanup (no id → picker)
#
# attach/detach/reap without an id open an arrow-key picker (↑/↓ + Enter, q
# cancels) showing each task's start time; reap's picker also lists STRANDED
# worktrees (pod restart killed tmux, PVC kept the worktree) so they don't
# balloon the PVC. attach is nested-tmux aware: from inside tmux it switches
# clients instead of tripping the "sessions should be nested with care" guard.
#
# Agents run YOLO by default (claude --dangerously-skip-permissions / codex
# --dangerously-bypass-approvals-and-sandbox): the pod IS the sandbox.
set -uo pipefail

REPOS="$HOME/repos" WORK="$HOME/work" OWNER="thaynes43"
log() { printf 'agent-run: %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

usage() {
  cat >&2 <<'EOF'
agent-run — worktree-per-task agent dispatcher
  agent-run run  --repo <name> --agent claude|codex [--base <ref>] -p "<task>"
  agent-run run  --repo <name> --agent claude --interactive [--local] [--safe]
                 # default: Remote Control host (drive from phone/claude.ai/code)
                 # --local: classic in-terminal TUI instead
  agent-run list
  agent-run attach [<task-id>]                # no id → arrow-key picker
  agent-run detach [<task-id>]                # no id → picker (attached sessions only)
  agent-run reap   [<task-id>] [--force]      # no id → picker (incl. stranded worktrees)
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

# Every task shell needs the fresh bot token (bashrc handles interactive shells;
# tmux run-shell strings need it inline).
token_env='export GH_TOKEN="$(cat /creds/gh_token 2>/dev/null)"'

cmd="${1:-help}"; shift || true

case "$cmd" in
  run)
    repo="" agent="" base="" prompt="" interactive=0 safe=0 local_tui=0
    while [ $# -gt 0 ]; do
      case "$1" in
        --repo) repo="$2"; shift 2 ;;
        --agent) agent="$2"; shift 2 ;;
        --base) base="$2"; shift 2 ;;
        -p|--prompt) prompt="$2"; shift 2 ;;
        --interactive) interactive=1; shift ;;
        --local) local_tui=1; shift ;;
        --safe) safe=1; shift ;;
        *) die "unknown flag $1" ;;
      esac
    done
    [ -n "$repo" ] || die "--repo required"
    case "$agent" in claude|codex) : ;; *) die "--agent must be claude|codex" ;; esac
    [ "$interactive" = 1 ] || [ -n "$prompt" ] || die "need -p or --interactive"

    # Canonical clone: fetch-only, never worked in directly.
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
open a PR with 'gh pr create' when the task is complete. If blocked, write \
BLOCKED and the reason as your final message."

    case "$agent" in
      claude) run_cmd="claude --dangerously-skip-permissions --append-system-prompt \"\$AGENT_GUARD\" -p \"\$AGENT_PROMPT\" --output-format text" ;;
      codex)  run_cmd="codex exec --dangerously-bypass-approvals-and-sandbox \"\$AGENT_GUARD \$AGENT_PROMPT\"" ;;
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
        pretrust "$wt"
        if [ "$local_tui" = 1 ]; then
          # --local: classic TUI, NO permission flag unless --safe — the pod
          # settings default to bypass, and ANY permission flag at launch
          # silently disables the in-TUI /remote-control (proven 2026-07-17).
          # Know that the in-TUI /remote-control is ALSO flaky server-side
          # (intermittent 401s, anthropics/claude-code#30093 #30102, and a
          # 3-strikes-then-disabled-until-restart hook) — which is exactly why
          # the RC-host mode below is the default, not this.
          launch="claude"
          [ "$safe" = 1 ] && launch="claude --permission-mode default"
        else
          # DEFAULT: Remote Control HOST from the get-go. The standalone
          # subcommand registers through the api.anthropic.com environments
          # API — the reliable path (the in-TUI slash command is not) — and
          # phone/claude.ai-code drives the session; local attach shows the
          # status/QR/URL screen. Failures self-document in the rc log.
          rc_mode="--permission-mode bypassPermissions"
          [ "$safe" = 1 ] && rc_mode="--permission-mode default"
          launch="claude remote-control --name $id $rc_mode --debug-file $WORK/$id.rc.log"
        fi
      else
        launch="$agent $yolo_flag"   # codex: RC is claude-only → local TUI
      fi
      tmux send-keys -t "task-$id" "$token_env; cd $wt; $launch" Enter
      if [ "$agent" = claude ] && [ "$local_tui" != 1 ]; then
        log "remote-control host starting → QR/URL: agent-run attach $id   rc log: $WORK/$id.rc.log"
      else
        log "interactive session ready →  agent-run attach $id"
      fi
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
    repo="${id%%-[0-9]*}"
    if [ -d "$WORK/$id" ]; then
      git -C "$REPOS/$repo" worktree remove $force "$WORK/$id" \
        && log "worktree removed" \
        || die "worktree dirty — commit/push first or reap --force"
    fi
    git -C "$REPOS/$repo" branch -D "agent/$id" 2>/dev/null || true
    log "reaped $id (log kept at $WORK/$id.log)"
    ;;
  *)
    usage; exit 1
    ;;
esac
