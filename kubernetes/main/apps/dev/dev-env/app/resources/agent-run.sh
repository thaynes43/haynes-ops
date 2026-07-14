#!/usr/bin/env bash
# agent-run — worktree-per-task agent dispatcher (saga plan 06). Isolation by
# construction: every task gets its own git worktree + branch + tmux session + log;
# concurrent agents in the same repo never collide. GitOps-managed — edit in
# kubernetes/main/apps/dev/dev-env/app/resources/agent-run.sh.
#
#   agent-run run  --repo <name> --agent claude|codex [--base <ref>] -p "<task>"
#   agent-run run  --repo <name> --agent claude --interactive   # tmux session, no task
#                                                 [--safe]      # keep permission prompts
#   agent-run list                                              # live tasks + attach cmds
#   agent-run attach <task-id>                                  # tmux attach
#   agent-run reap   <task-id> [--force]                        # kill + cleanup
#
# Agents run YOLO by default (claude --dangerously-skip-permissions / codex
# --dangerously-bypass-approvals-and-sandbox): the pod IS the sandbox.
set -uo pipefail

REPOS="$HOME/repos" WORK="$HOME/work" OWNER="thaynes43"
log() { printf 'agent-run: %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# Every task shell needs the fresh bot token (bashrc handles interactive shells;
# tmux run-shell strings need it inline).
token_env='export GH_TOKEN="$(cat /creds/gh_token 2>/dev/null)"'

cmd="${1:-help}"; shift || true

case "$cmd" in
  run)
    repo="" agent="" base="" prompt="" interactive=0 safe=0
    while [ $# -gt 0 ]; do
      case "$1" in
        --repo) repo="$2"; shift 2 ;;
        --agent) agent="$2"; shift 2 ;;
        --base) base="$2"; shift 2 ;;
        -p|--prompt) prompt="$2"; shift 2 ;;
        --interactive) interactive=1; shift ;;
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
      tmux send-keys -t "task-$id" "$token_env; cd $wt; $agent $yolo_flag" Enter
      log "interactive session ready →  agent-run attach $id"
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
    ids="$(tmux ls -F '#{session_name}' 2>/dev/null | sed -n 's/^task-//p')"
    if [ -z "$ids" ]; then
      printf '  (none)\n'
    else
      for i in $ids; do printf '  %s\n      attach:  agent-run attach %s\n' "$i" "$i"; done
    fi
    printf 'WORKTREES:\n'
    for r in "$REPOS"/*/; do [ -d "$r/.git" ] && git -C "$r" worktree list | grep "$WORK" || true; done
    ;;
  attach)
    id="${1:-}"; [ -n "$id" ] || die "attach <task-id>   (see: agent-run list)"
    id="${id#task-}"   # tolerate pasting the tmux session name by mistake
    tmux has-session -t "task-$id" 2>/dev/null \
      || die "no live session '$id' — run 'agent-run list' to see what's running"
    exec tmux attach -t "task-$id"
    ;;
  reap)
    id="${1:-}"; [ -n "$id" ] || die "reap <task-id> [--force]"
    id="${id#task-}"   # same tolerance as attach
    force=""; [ "${2:-}" = "--force" ] && force="--force"
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
    sed -n '3,10p' "$0"; exit 1
    ;;
esac
