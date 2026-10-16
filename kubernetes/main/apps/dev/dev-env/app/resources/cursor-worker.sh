#!/usr/bin/env bash
# Optional after-ready lane. Never call from the synchronous dev-init boot path.
set -euo pipefail
umask 077
export PATH="$HOME/.local/bin:$PATH"
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VERSION=2026.10.14-0f9edde
SHA256=74fac931fe3578980d070701872b7dab54eb87a9abf9b6e2e6be19beeb9167a7
NAME=dev-env-v1-haynes-ops
SESSION=cursor-worker-v1
WORKSPACE="$HOME/cursor/haynes-ops"
STATE="$HOME/.local/state/dev-env-cursor"
DATA_DIR="$HOME/.local/share/cursor-agent"
PROFILE="${2:-haynes-ops}"
case "$PROFILE" in
  haynes-ops) ;;
  dev-env)
    NAME=dev-env
    SESSION=cursor-worker-dev-env
    WORKSPACE="$HOME/repos/dev-env"
    STATE="$HOME/.local/state/cursor-worker-dev-env"
    DATA_DIR="$HOME/.local/share/cursor-agent-dev-env"
    ;;
  *) echo 'usage: cursor-worker {up|run|status|install} [haynes-ops|dev-env]' >&2; exit 2 ;;
esac
VERSIONS="$HOME/.local/share/cursor-agent/versions"
BIN="$VERSIONS/$VERSION/cursor-agent"
# Overrides exist only for offline fixtures.
BACKOFF="${CURSOR_WORKER_BACKOFF_SECONDS:-15}"
LOG_MAX="${CURSOR_WORKER_LOG_MAX_BYTES:-10485760}"
mkdir -p "$STATE" "$HOME/.local/bin"
log() { printf 'cursor-worker: %s %s\n' "$(date -u +%FT%TZ)" "$*"; }

install_cli() {
  [ "$(uname -s)/$(uname -m)" = Linux/x86_64 ] || { log 'unsupported platform (v1 is Linux x86_64)'; return 1; }
  mkdir -p "$VERSIONS"
  if [ ! -x "$BIN" ] || [ "$("$BIN" --version 2>/dev/null)" != "$VERSION" ]; then
    # Do not overwrite a damaged or unexpected install in place.
    [ ! -e "$VERSIONS/$VERSION" ] || { log 'unexpected pinned install; preserve it for inspection'; return 1; }
    local stage
    stage="$(mktemp -d "$VERSIONS/.install.XXXXXX")"
    if ! (
      trap 'rm -rf -- "$stage"' EXIT
      curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
        --connect-timeout 15 --max-time 240 --retry 2 \
        "https://downloads.cursor.com/lab/$VERSION/linux/x64/agent-cli-package.tar.gz" -o "$stage/package.tgz" || exit 1
      printf '%s  %s\n' "$SHA256" "$stage/package.tgz" | sha256sum --check --status || exit 1
      mkdir "$stage/package" || exit 1
      tar -xzf "$stage/package.tgz" --strip-components=1 -C "$stage/package" || exit 1
      [ "$("$stage/package/cursor-agent" --version)" = "$VERSION" ] || exit 1
      mv "$stage/package" "$VERSIONS/$VERSION" || exit 1
    ); then log 'pinned installation failed; worker not started'; return 1; fi
  fi
  local command target
  for command in agent cursor-agent; do
    target="$HOME/.local/bin/$command"
    if [ -e "$target" ] || [ -L "$target" ]; then
      [ -L "$target" ] || { log "refusing to overwrite non-symlink $target"; return 1; }
      case "$(readlink "$target")" in "$VERSIONS/"*) ;; *) log "refusing unrelated symlink $target"; return 1;; esac
    fi
    ln -sfn "$BIN" "$target"
  done
}

prepare_workspace() {
  if [ ! -e "$WORKSPACE" ]; then
    [ "$PROFILE" = haynes-ops ] || { log 'dev-env checkout missing; no automatic clone'; return 1; }
    [ -d "$HOME/repos/haynes-ops/.git" ] || { log 'canonical clone missing; no automatic clone'; return 1; }
    mkdir -p "$HOME/cursor"
    # Never reset an existing checkout or fetch behind another session's back.
    git -C "$HOME/repos/haynes-ops" worktree add --detach "$WORKSPACE" origin/main
  fi
  git -C "$WORKSPACE" rev-parse --is-inside-work-tree >/dev/null
  python3 "$HERE/cursor-config.py" --workspace "$WORKSPACE"
}

# Copy-truncate: the tmux launch holds worker.log open with O_APPEND, so a rename
# would keep the live writer on the old inode. A few lines may be lost per rotation.
rotate_log() {
  local log="$STATE/worker.log"
  [ -f "$log" ] && [ "$(wc -c <"$log")" -gt "$LOG_MAX" ] || return 0
  cp -f "$log" "$log.1" && : >"$log"
}

authenticated() {
  # Read status through the vendor CLI; never parse or copy auth.json ourselves.
  "$BIN" status --format json 2>/dev/null | python3 -c \
    'import json,sys; sys.exit(0 if json.load(sys.stdin).get("isAuthenticated") is True else 1)' 2>/dev/null
}

case "${1:-status}" in
  up)
    if tmux has-session -t "=$SESSION" 2>/dev/null; then
      log "session already exists: $SESSION (inspect status; never replace a live pilot)"
      exit 0
    fi
    rotate_log
    printf -v launch 'exec bash %q run %q >>%q 2>&1' "$HERE/cursor-worker.sh" "$PROFILE" "$STATE/worker.log"
    tmux new-session -d -s "$SESSION" "$launch"
    log "launched $SESSION; log: $STATE/worker.log"
    ;;
  run)
    exec 9>"$STATE/worker.lock"
    flock -n 9 || { log 'supervisor already holds the lock'; exit 0; }
    [ ! -f "$STATE/disabled" ] || { log 'disabled by persistent operator marker'; exit 0; }
    install_cli
    prepare_workspace
    [ -s "$HOME/.config/cursor/auth.json" ] || {
      log 'browser login required: NO_OPEN_BROWSER=1 agent login (no automatic pairing)'; exit 0;
    }
    # A failed status call right after boot may be a DNS/network blip: let the
    # bounded retry budget below cover it instead of leaving the worker down.
    authenticated || log 'authentication check failed; starting within the circuit breaker'
    # Worker identity/data and browser login remain on the existing home PVC.
    # One process is not a concurrency limit: My Machines supports shared claims.
    child=''
    # Bounds the log during a long worker run; closes the lock fd so an orphaned
    # watcher can never block a later supervisor.
    ( exec 9>&-; while sleep 300; do rotate_log; done ) >/dev/null 2>&1 &
    watcher=$!
    stop() {
      [ -z "$child" ] || { kill -TERM "$child" 2>/dev/null || true; wait "$child" 2>/dev/null || true; }
    }
    trap 'kill "$watcher" 2>/dev/null || true' EXIT
    trap 'stop; exit 0' TERM INT
    failures=0
    while [ ! -f "$STATE/disabled" ]; do
      rotate_log
      started="$(date +%s)"
      "$BIN" worker --name "$NAME" --worker-dir "$WORKSPACE" \
        --data-dir "$DATA_DIR" --idle-release-timeout 0 start &
      child=$!
      rc=0; wait "$child" || rc=$?
      child=''
      elapsed=$(( $(date +%s) - started ))
      if [ "$elapsed" -ge 600 ]; then failures=0; fi
      failures=$((failures + 1))
      log "worker exited rc=$rc after ${elapsed}s; consecutive short exits=$failures"
      [ "$failures" -lt 5 ] || { log 'recovery circuit breaker open; inspect before running up again'; exit 1; }
      [ ! -f "$STATE/disabled" ] || break
      # A missing login file is a real logout. A failed status call may be a network
      # blip, so it only consumes the bounded retry budget above.
      [ -s "$HOME/.config/cursor/auth.json" ] || { log 'login file gone; human browser login required'; exit 0; }
      authenticated || log 'authentication check failed; retrying within the circuit breaker'
      # Background sleep so TERM is handled immediately, not after the delay.
      sleep "$(( failures * BACKOFF ))" & child=$!
      wait "$child" || true
      child=''
    done
    log 'disabled by persistent operator marker'
    ;;
  status)
    tmux list-panes -t "=$SESSION" -F '#{session_name} #{pane_current_command} dead=#{pane_dead}'
    [ ! -x "$BIN" ] || "$BIN" --version
    ;;
  install)
    exec 9>"$STATE/worker.lock"
    flock -n 9 || { log 'worker is running; installation refused'; exit 1; }
    install_cli
    ;;
  *) echo 'usage: cursor-worker {up|run|status|install} [haynes-ops|dev-env]' >&2; exit 2 ;;
esac
