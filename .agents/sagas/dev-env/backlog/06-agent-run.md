# 06 — agent-run wrapper: worktree-per-task dispatch

**Status:** done (PR #2041, 2026-07-13) — smoke-tested end-to-end: yolo claude
dispatched via `agent-run run --repo haynes-ops --agent claude -p …` in an isolated
worktree on the Max plan; guard prompt held (no commit/push); list/attach/reap
verified. Interactive mode + codex path implemented; `--reap` keeps logs.
**Depends on:** 03 (configs), 04 (auth)

## Goal

One command that starts an agent on a task with isolation guaranteed by construction:

```
agent-run --repo haynes-ops --agent claude [--yolo] [--branch <name>] -p "prompt..."
agent-run --list / --attach <task-id> / --reap <task-id>
```

Worktrees are not optional (saga Hard news #7): concurrent agents share repos and
must not collide. The wrapper owns the lifecycle so neither humans nor the Shepherd
have to remember.

## Design

- Repos live at `/home/dev/repos/<name>` (canonical clones, kept fresh by the wrapper
  with `git fetch` — never worked in directly by agents).
- Per task: `git worktree add /home/dev/work/<task-id> -b agent/<task-id>` from
  `origin/main` (or `--branch`), where `<task-id>` = short slug + timestamp.
- The agent runs inside a **named tmux session** (`task-<task-id>`) so it survives
  exec disconnects and a human can `--attach` from code-server or kubectl exec, or
  take over with `/remote-control`.
- Agent invocation:
  - claude: `claude --dangerously-skip-permissions -p` (unattended) or interactive
    (no `-p`) for human-driven; `--output-format json` capture to
    `/home/dev/work/<task-id>.log` for unattended runs.
  - codex: `codex --yolo ...` equivalent flags.
- Guardrails even in yolo mode: the wrapper injects an append-system-prompt (claude)
  / instructions (codex) stating the worktree boundary, the push-branch-only rule
  (never push main — mirror the shepherd's SAFETY prompt), and the task's scope.
- `--reap`: kill tmux session, `git worktree remove`, delete branch if unmerged-and-
  abandoned (prompt first), archive the log.
- A `dev-env` CLAUDE.md section (from backlog 03) documents the convention for
  interactive humans.

## Acceptance

- Two simultaneous `agent-run` tasks against the same repo produce two worktrees, two
  tmux sessions, zero interference; both can push their own branches.
- `agent-run --list` shows live tasks; `--attach` drops into the tmux session.
- Killing the pod mid-task loses the tmux session but leaves the worktree + log on
  the PVC recoverable (`--reap` cleans up cleanly after restart).
