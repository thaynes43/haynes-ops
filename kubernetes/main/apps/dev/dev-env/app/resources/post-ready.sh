#!/usr/bin/env bash
# post-ready — the pod's AFTER-BOOT lane: the slow, optional session launches that
# must NEVER run inside dev-init. Started detached from the container args, in tmux
# window `main:post-ready`, immediately after the tmux server comes up and BEFORE
# code-server execs, so nothing in the boot path ever waits on it:
#
#   dev-init.sh                        (fast, synchronous: link GitOps config into $HOME)
#     → tmux new-session -d -s main    (the agent session host)
#     → tmux new-window -d -t main -n post-ready  ← THIS SCRIPT (detached, non-blocking)
#     → exec code-server               (binds :8443 — what the probes watch)
#
# WHY IT EXISTS (incident 2026-09-10; PR #2824, reverted wholesale in #2827):
# #2824 ran `agent-run codex-remote up` and the claude standby session SYNCHRONOUSLY
# at the end of dev-init. dev-init finishes BEFORE code-server binds :8443, the app
# container's liveness probe was tcpSocket 8443 / periodSeconds 10 / failureThreshold 3
# with NO startup probe — so a dev-init taking >30s was a kubelet kill (exit 137).
# The pod restarted every minute and every restart minted ANOTHER remote-control
# session + worktree: 30+ orphans. Two independent fixes, both needed:
#   1. helmrelease.yaml now has a startup probe (10 min ceiling) so a slow boot can
#      never be read as a liveness failure; and
#   2. the slow work moved here, off the boot path, and only starts once the KUBELET
#      says the pod is ready — plus a circuit breaker that refuses to launch anything
#      when this pod has restarted repeatedly (step 4 below).
#
# RULES FOR ANYTHING ADDED HERE: never fatal, never on the boot path, ALWAYS exit 0,
# and never assume this is the only copy that has ever run against this PVC.
#
# Inspect a boot:  cat /tmp/post-ready.log   |   tmux capture-pane -pt main:post-ready
# Source of truth: kubernetes/main/apps/dev/dev-env/app/resources/post-ready.sh
set -uo pipefail

NS="$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace 2>/dev/null || echo dev)"
POD="${HOSTNAME:-$(hostname 2>/dev/null || echo '')}"
AGENT_RUN="$HOME/.local/bin/agent-run"
# PVC-backed on purpose: the circuit breaker has to see the PREVIOUS containers'
# launches, and /tmp is an emptyDir that a restart wipes.
LAUNCH_LOG="$HOME/.cache/dev-env/post-ready-launches.log"
LOOP_WINDOW=1800   # 30 min
LOOP_MAX=3         # launches inside that window → stop launching, something is wrong
# Same default as dev-init (the pod-wide interactive model; Model policy in CLAUDE.md).
DEV_ENV_CLAUDE_MODEL="${DEV_ENV_CLAUDE_MODEL:-claude-fable-5-1}"

log() { printf 'post-ready: %s %s\n' "$(date -u '+%H:%M:%SZ')" "$*"; }

codex_state="not reached"
standby_state="not reached"
finish() {
  log "SUMMARY  codex-remote: ${codex_state}  |  claude standby: ${standby_state}"
  exit 0   # ALWAYS: a non-zero exit here must never colour the pod's health
}

log "start (pod=${POD:-?} ns=$NS model=$DEV_ENV_CLAUDE_MODEL)"

# ── 1. wait for code-server to answer locally ───────────────────────────────────
# /healthz is code-server's own health route. curl is in the image (dev-init installs
# codex/kubectl-cnpg with it), but keep a dependency-free fallback: a bash /dev/tcp
# connect proves the listener without needing any binary at all.
if command -v curl >/dev/null 2>&1; then
  http_up() { curl -fsS -o /dev/null --max-time 5 http://127.0.0.1:8443/healthz; }
  probe_kind="curl http://127.0.0.1:8443/healthz"
else
  http_up() { (exec 3<>/dev/tcp/127.0.0.1/8443) 2>/dev/null; }
  probe_kind="bash /dev/tcp 127.0.0.1:8443 (no curl in the image)"
fi
log "waiting for code-server — $probe_kind, every 5s, up to 15 min"
served=0
for _ in $(seq 1 180); do
  if http_up; then served=1; break; fi
  sleep 5
done
if [ "$served" != 1 ]; then
  codex_state="skipped (code-server never answered on :8443 within 15 min)"
  standby_state="$codex_state"
  log "WARN code-server did not answer within 15 min — launching nothing"
  finish
fi
log "code-server is answering on :8443"

# ── 2. wait for the KUBELET's own verdict ──────────────────────────────────────
# The point of the whole file: launch only once the pod is Ready AND the app
# container's startup probe has passed (`.started`), i.e. the kubelet has stopped
# treating this boot as a startup and liveness is now in charge.
# In-cluster kubectl takes NO flags that touch the client config: kubectl only
# auto-detects the mounted service account when the merged client config is exactly
# the built-in default, and any config override (--request-timeout is one) makes it
# "a real config" so every call silently dies against localhost:8080 — see the header
# of kubernetes/main/apps/kube-system/node-out-of-service/app/resources/node-out-of-service.sh.
# `-n` and `-o` are namespace/printer flags, not config overrides, so they are safe.
# RBAC: the dev-env SA already has core pods get/list/watch cluster-wide via the
# dev-env-operator ClusterRole (app/rbac.yaml) — no extra Role needed for this.
SETTLE=60
kube_ready=0
if command -v kubectl >/dev/null 2>&1 && [ -n "$POD" ] \
   && kubectl get pod "$POD" -n "$NS" -o name >/dev/null 2>&1; then
  log "waiting for kubelet Ready=True + app.started=true (pod $POD, ns $NS), every 5s, up to 10 min"
  for _ in $(seq 1 120); do
    ready=""; started=""
    read -r ready started <<<"$(kubectl get pod "$POD" -n "$NS" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status} {.status.containerStatuses[?(@.name=="app")].started}' 2>/dev/null)"
    if [ "${ready:-}" = "True" ] && [ "${started:-}" = "true" ]; then kube_ready=1; break; fi
    sleep 5
  done
  if [ "$kube_ready" = 1 ]; then
    log "kubelet reports Ready=True and the app container passed its startup probe"
  else
    log "WARN kubelet never reported Ready+started within 10 min — code-server IS up, so continuing"
  fi
else
  # No kubectl / no pod name / no RBAC: healthz already proved the server is serving.
  SETTLE=90
  log "WARN cannot read this pod with kubectl (no binary, no \$HOSTNAME, or no RBAC) — falling back to healthz + a 90s settle"
fi

# ── 3. settle ──────────────────────────────────────────────────────────────────
# Liveness (tcpSocket 8443 / 10s) gets several clean periods before anything heavy
# starts, so a session launch can never be what tips a marginal boot over.
log "settling ${SETTLE}s before launching anything"
sleep "$SETTLE"

# ── 4. circuit breaker — the actual loop protection ────────────────────────────
# Records every time this script reaches the launch point, on the PVC so it survives
# restarts. Three launches inside 30 minutes means the pod is rolling far faster than
# a healthy dev-env ever does (a normal day is 0–2), and #2824's failure mode was
# exactly that: each restart minting one more remote-control session + worktree.
# When that is happening the RIGHT behaviour is to leave the pod clean for a human.
mkdir -p "$(dirname "$LAUNCH_LOG")" 2>/dev/null
now="$(date -u +%s)"
printf '%s\n' "$now" >> "$LAUNCH_LOG"
if tail -n 50 "$LAUNCH_LOG" > "$LAUNCH_LOG.tmp" 2>/dev/null; then
  mv -f "$LAUNCH_LOG.tmp" "$LAUNCH_LOG"
else
  rm -f "$LAUNCH_LOG.tmp"
fi
recent=0
while read -r ts; do
  case "$ts" in ''|*[!0-9]*) continue ;; esac
  if [ "$(( now - ts ))" -le "$LOOP_WINDOW" ]; then recent=$(( recent + 1 )); fi
done < "$LAUNCH_LOG"
if [ "$recent" -ge "$LOOP_MAX" ]; then
  log "loop suspected ($recent launches in 30m) — not starting sessions; investigate with kubectl describe pod ${POD:-<pod>} -n $NS"
  codex_state="skipped (circuit breaker: $recent launches in 30m)"
  standby_state="$codex_state"
  finish
fi
log "circuit breaker clear ($recent launch(es) in the last 30m, limit $LOOP_MAX) — $LAUNCH_LOG"

if [ ! -x "$AGENT_RUN" ]; then
  codex_state="skipped (no $AGENT_RUN — did dev-init run?)"
  standby_state="$codex_state"
  log "WARN $AGENT_RUN missing or not executable — launching nothing"
  finish
fi

# ── 5. codex remote-control (pod-level phone control) ──────────────────────────
# Exactly the call #2824 made from dev-init, with its gating: the standalone codex
# (the npm codex cannot do remote-control) and a real ~/.codex/auth.json. `up` is
# idempotent and starts the supervisor in tmux session `codex-remote`; the enrolment
# lives in ~/.codex's state DB on the PVC, so a roll needs no new pairing code.
# Non-fatal: a failure costs phone control this boot, nothing else.
if [ -x "$HOME/.local/bin/codex" ] && [ -s "$HOME/.codex/auth.json" ]; then
  log "starting codex remote-control (agent-run codex-remote up)…"
  if "$AGENT_RUN" codex-remote up >>/tmp/post-ready.log 2>&1; then
    codex_state="up (supervised; updates ride CODEX_VERSION at boot)"
  else
    codex_state="FAILED — retry by hand: agent-run codex-remote up"
  fi
else
  codex_state="skipped (no standalone codex at ~/.local/bin/codex, or empty ~/.codex/auth.json)"
fi
log "codex remote-control: $codex_state"

# ── 6. claude standby session (phone/web-drivable, on haynes-ops) ──────────────
# A pod roll ends every claude session and only a shell inside the pod can start one
# (Tom, 2026-09-10: "no sessions for claude code will be active, nothing will be able
# to start one"), so boot one: agent-run's `both` mode = terminal TUI + `claude
# --remote-control`, registered at claude.ai/code, on the ops repo at the interactive
# model/effort. Idle it costs nothing; `agent-run prune` reaps an unused worktree.
# IDEMPOTENT FIRST: if a live standby is already here (a task-haynes-ops-* tmux
# session whose pane is really running `claude --remote-control <id>`), leave it
# alone — duplicate standbys are exactly what #2824's restart loop produced.
existing=""
while read -r sess; do
  [ -n "$sess" ] || continue
  sid="${sess#task-}"
  # pgrep proves the pane is running the remote-control client rather than sitting at
  # a dead shell; without pgrep, treat the session as live (conservative: better to
  # skip a launch than to pile a second one on).
  if command -v pgrep >/dev/null 2>&1; then
    pgrep -f "remote-control $sid" >/dev/null 2>&1 || continue
  fi
  existing="$sid"; break
done < <(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep '^task-haynes-ops-' || true)

if [ -n "$existing" ]; then
  standby_state="skipped (live standby already here: task-$existing → agent-run attach $existing)"
elif [ ! -s "$HOME/.claude/.credentials.json" ] || [ ! -d "$HOME/repos/haynes-ops/.git" ]; then
  # The Max login is required: the env CLAUDE_CODE_OAUTH_TOKEN cannot register a
  # /v1/code/sessions remote session (A/B-proven in-pod 2026-08-29).
  standby_state="skipped (no Max login at ~/.claude/.credentials.json, or no ~/repos/haynes-ops)"
else
  # The gh-refresher sidecar mints /creds/gh_token concurrently and agent-run fetches
  # the repo with it — wait up to 90s rather than racing it.
  for _ in $(seq 1 45); do [ -s /creds/gh_token ] && break; sleep 2; done
  if [ ! -s /creds/gh_token ]; then
    standby_state="skipped (no /creds/gh_token after 90s — gh-refresher sidecar down?)"
  else
    log "starting the claude standby session ($DEV_ENV_CLAUDE_MODEL, effort xhigh)…"
    out="$("$AGENT_RUN" --repo haynes-ops --agent claude --interactive \
            --model "$DEV_ENV_CLAUDE_MODEL" --effort xhigh </dev/null 2>&1)"; rc=$?
    printf '%s\n' "$out" | sed 's/^/post-ready:   | /'
    sid="$(printf '%s\n' "$out" | tail -n 1)"   # agent-run prints the task id on stdout
    case "$sid" in
      haynes-ops-[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]) ;;
      *) sid="" ;;
    esac
    if [ "$rc" -eq 0 ] && [ -n "$sid" ]; then
      standby_state="up: task-$sid ($DEV_ENV_CLAUDE_MODEL xhigh) → agent-run attach $sid"
      # claude prints its claude.ai URL into the pane once the remote session
      # registers (~30s). Best-effort, purely so the log carries the link.
      url=""
      for _ in $(seq 1 12); do
        url="$(tmux capture-pane -p -t "task-$sid" 2>/dev/null | grep -oE 'https://claude\.ai[^[:space:]]+' | tail -n 1)"
        [ -n "$url" ] && break
        sleep 5
      done
      if [ -n "$url" ]; then
        standby_state="$standby_state  url=$url"
      else
        log "no claude.ai URL in the pane after 60s — check: tmux capture-pane -pt task-$sid"
      fi
    else
      standby_state="FAILED (rc=$rc) — retry by hand: agent-run --repo haynes-ops --agent claude --interactive --model $DEV_ENV_CLAUDE_MODEL --effort xhigh"
    fi
  fi
fi
log "claude standby: $standby_state"

finish
