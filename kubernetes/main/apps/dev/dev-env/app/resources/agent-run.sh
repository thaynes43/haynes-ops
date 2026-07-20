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
#                                     [--model <m>] [--effort <l>]  # override the pod default (Fable)
#   agent-run list                                              # live tasks + attach cmds
#   agent-run attach [<task-id>]                # jump into a session (no id → picker)
#   agent-run detach [<task-id>]                # kick attached clients off (no id → picker)
#   agent-run reap   [<task-id>] [--force]      # kill + cleanup (no id → picker)
#   agent-run prune  [--yes] [--force]          # bulk-clean ALL stranded worktrees (dry-run w/o --yes)
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

usage() {
  cat >&2 <<'EOF'
agent-run — worktree-per-task agent dispatcher
  agent-run run  --repo <name> --agent claude|codex [--base <ref>] -p "<task>"
  agent-run run  --repo <name> --agent claude --interactive [--local] [--safe]
                 [--model <m>] [--effort low|medium|high|xhigh|max]
                 # default: Remote Control host (drive from phone/claude.ai/code)
                 # --local: classic in-terminal TUI instead
                 # --model/--effort override the pod default (Fable, session effort)
  agent-run list
  agent-run attach [<task-id>]                # no id → arrow-key picker
  agent-run detach [<task-id>]                # no id → picker (attached sessions only)
  agent-run reap   [<task-id>] [--force]      # no id → picker (incl. stranded worktrees)
  agent-run prune  [--yes] [--force]          # bulk-clean stranded worktrees (dry-run without --yes)
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

# Every task shell needs the fresh bot token (bashrc handles interactive shells;
# tmux run-shell strings need it inline).
token_env='export GH_TOKEN="$(cat /creds/gh_token 2>/dev/null)"'

cmd="${1:-help}"; shift || true

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
        *) die "unknown flag $1" ;;
      esac
    done
    [ -n "$repo" ] || die "--repo required"
    case "$agent" in claude|codex) : ;; *) die "--agent must be claude|codex" ;; esac
    [ "$interactive" = 1 ] || [ -n "$prompt" ] || die "need -p or --interactive"
    if [ -n "$effort" ]; then   # fail fast, before any clone/worktree side effects
      case "$effort" in low|medium|high|xhigh|max) : ;;
        *) die "--effort must be low|medium|high|xhigh|max (got '$effort'); 'ultracode' is a /effort session-mode — set it in-session" ;;
      esac
    fi

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
open a PR with 'gh pr create' when the task is complete. If the task needs extra \
git worktrees, create them under $WORK and 'git worktree remove' each once its PR \
merges — never leave stranded worktrees behind. If blocked, write BLOCKED and the \
reason as your final message."

    # Optional top-level claude flags — model/effort — apply to -p, --local, and RC
    # launches alike. Model otherwise defaults to the pod settings.json (Fable);
    # these MUST sit in top-level position (before the remote-control subcommand),
    # which is where claude reads them. No-op for codex.
    # %q-escape the values: they ride through one more shell layer (tmux send-keys
    # re-parses the launch string), and a legit value like 'claude-fable-5[1m]'
    # carries glob metacharacters that would otherwise be pathname-expanded.
    cg=""
    [ -n "$model" ]  && cg="$cg --model $(printf '%q' "$model")"
    [ -n "$effort" ] && cg="$cg --effort $(printf '%q' "$effort")"

    case "$agent" in
      claude) run_cmd="claude$cg --dangerously-skip-permissions --append-system-prompt \"\$AGENT_GUARD\" -p \"\$AGENT_PROMPT\" --output-format text" ;;
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
          launch="claude$cg"
          [ "$safe" = 1 ] && launch="claude$cg --permission-mode default"
        else
          # DEFAULT: Remote Control HOST from the get-go. The standalone
          # subcommand registers through the api.anthropic.com environments
          # API — the reliable path (the in-TUI slash command is not) — and
          # phone/claude.ai-code drives the session; local attach shows the
          # status/QR/URL screen. Failures self-document in the rc log.
          #
          # NB: the subcommand takes NO --model/--effort, and top-level flags
          # placed BEFORE it make it reject its own --name (`claude $cg
          # remote-control --name …` → "unknown option --name", verified live).
          # So an RC host takes its model from the pod settings (Fable) and runs
          # at the default effort — set a different effort with /effort in the
          # session, or use --local for a flag-controlled TUI. NEVER splice $cg
          # here.
          [ -n "$cg" ] && log "note: --model/--effort don't apply to an RC host (model = pod default; use --local, or set /effort in-session)"
          rc_mode="--permission-mode bypassPermissions"
          [ "$safe" = 1 ] && rc_mode="--permission-mode default"
          # --spawn=same-dir: newer claude prompts "[1] same-dir / [2] worktree"
          # on startup and BLOCKS there un-registered (not phone-reachable) until
          # answered. Pin same-dir so the host registers immediately AND so phone/
          # web sessions reuse this worktree instead of spawning fresh ones (which
          # would re-grow the very worktree sprawl reap/prune exist to control).
          launch="claude remote-control --name $id --spawn same-dir $rc_mode --debug-file $WORK/$id.rc.log"
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
    strand=0
    for wt in "$WORK"/*/; do
      [ -d "$wt" ] || continue
      tmux has-session -t "task-$(basename "$wt")" 2>/dev/null && continue
      owning_repo "$wt" >/dev/null 2>&1 && strand=$((strand + 1))   # count what prune acts on
    done
    [ "$strand" -gt 0 ] && printf 'STRANDED: %d worktree(s) with no live session → agent-run prune\n' "$strand"
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
  *)
    usage; exit 1
    ;;
esac
