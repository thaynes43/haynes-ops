# dev-env — your in-cluster agent workspace

You're in a 24/7 pod in the `main` cluster (namespace `dev`). Everything here is GitOps-managed: this README lives at `kubernetes/main/apps/dev/dev-env/app/resources/config/home/README.md` in **haynes-ops** — edit it there, not here (a pod restart overwrites this copy).

> **Tip:** `Ctrl+Shift+V` renders this markdown. `` Ctrl+` `` opens a terminal.

## Quick start

Open a terminal and run:

```bash
agent-run run
```

That's the whole command. Everything you don't specify is asked with an arrow-key picker: **repo** (your GitHub repos, newest activity first — no need to remember exact names), **agent** (`claude` or `codex`), **mode** (interactive session, or type a task prompt for fire-and-forget), **how to drive an interactive claude session** (a *remote-control host* you drive from your phone/web — the default — or a *local TUI* you type to in the terminal), and **effort** for claude runs (default `xhigh`). The agent then starts in a **fresh git worktree** on its own branch, inside tmux.

Know what you want? Flags skip the pickers, and `agent-run run <repo>` is a positional shorthand:

```bash
agent-run run haynesnetwork --agent claude --interactive
agent-run run --repo haynes-ops --agent claude --effort max -p "Bump the coredns chart and open a PR"
```

**Two ways to drive an interactive claude session.** The default is a **Remote Control host** — headless in the pod, driven from the Claude mobile app or claude.ai/code (`agent-run attach <id>` shows the status screen with the QR code + URL). The alternative is a **classic in-terminal TUI you type to directly** — pass the **`--local`** flag, or answer *n* to the menu's `remote-control host? [Y/n]` question. (Codex is always a local TUI — it has no remote-control host.)

**Permissions are skipped by default** (`claude --dangerously-skip-permissions` / `codex --dangerously-bypass-approvals-and-sandbox` — you don't type those). The pod *is* the sandbox: isolated worktree, scoped ServiceAccount, default-deny egress. Add **`--safe`** to keep the normal permission prompts for a task you want to babysit.

**Effort defaults to `xhigh`** for claude runs — deterministically, in every mode. Override with `--effort low|medium|high|xhigh|max` (or the picker), or mid-session with `/effort`. Details in [Model & effort](#model--effort) below.

## tmux — the only 3 keys you need

Agents run inside **tmux** so they survive you closing the browser. tmux uses a "prefix" key: you tap **Ctrl+B**, *release*, then press one more key.

| Keys | What it does |
|---|---|
| **Ctrl+B**, then **d** | **Detach.** The agent keeps working. Close the tab, shut the laptop. |
| **Ctrl+B**, then **[** | **Scroll back.** Arrows/PageUp to read history, **q** to stop scrolling. *(Mouse scroll won't work — this is the one that trips everyone.)* |
| **Ctrl+B**, then **c** | New window (a second shell in the same session). |

> **⚠️ code-server steals Ctrl+B** (it's VS Code's "toggle sidebar" shortcut), so in the integrated terminal here the detach keystroke usually never reaches tmux. Detach from any *other* terminal instead: **`agent-run detach`** (picker) or `tmux detach-client -s task-<task-id>`. Closing the browser tab also just detaches — the agent keeps running either way. The only thing that actually *kills* a session is `exit`/`Ctrl+D` at its shell prompt (or `agent-run reap`).

## Managing tasks

```bash
agent-run list               # what's running + start times + the attach command to copy
agent-run attach [task-id]   # jump back into a live session (scrollback intact)
agent-run detach [task-id]   # kick attached clients off — the session keeps running
agent-run reap [task-id]     # kill it + delete the worktree (the log is kept)
agent-run prune              # bulk-clean EVERY stranded worktree (dry-run; add --yes to do it)
```

**Skip the id and you get an arrow-key picker** (↑/↓ or j/k, Enter selects, `q` cancels) showing each task's start time. `reap`'s picker also lists **stranded worktrees** — a pod restart kills tmux but the PVC keeps the worktree — and asks `y/N` before deleting.

`list` prints the **task id** (e.g. `haynesnetwork-0714-010301`) and the ready-to-paste `agent-run attach …` line under it. If you paste the tmux session name (`task-…`) by mistake, `attach` strips the prefix for you. `attach` is nested-tmux aware: from inside another task's session it switches you over instead of erroring.

**Task logs** live at `~/work/<task-id>.log` even after a reap; Remote-Control host diagnostics land in `~/work/<task-id>.rc.log`.

---

# Reference

## agent-run flag reference

```
agent-run run [<repo>] [flags]              # every omitted choice becomes a picker/prompt
    --repo <name>                           # same as the positional <repo>
    --agent claude|codex
    --base <ref>                            # branch the worktree off this (default: origin/HEAD)
    -p "<task>"                             # fire-and-forget; log at ~/work/<id>.log
    --interactive                           # interactive session; default = Remote Control host (drive from phone/claude.ai/code)
    --local                                 # classic in-terminal TUI you type to directly, instead of the RC host
    --safe                                  # keep permission prompts (default: skipped)
    --effort low|medium|high|xhigh|max      # claude only; default xhigh
    --model <m>                             # -p/--local only (an RC host always runs the pod default)
agent-run list
agent-run attach [<task-id>]                # no id → picker
agent-run detach [<task-id>]                # no id → picker (attached sessions only)
agent-run reap   [<task-id>] [--force]      # no id → picker (incl. stranded worktrees)
agent-run prune  [--yes] [--force]          # bulk-clean stranded worktrees (dry-run without --yes)
```

The repo picker asks GitHub for your repos (newest-pushed first) via the bot token; if that's unreachable it falls back to the local clones in `~/repos`, most recently fetched first.

## Model & effort

- **Model:** the pod default is **Fable** (`~/.claude/settings.json` on the PVC). `--model` overrides it for `-p` and `--local` runs only — the `claude remote-control` subcommand takes no top-level flags, so an RC host always runs the pod default.
- **Effort:** `agent-run` pins claude runs to **`xhigh`** unless you choose otherwise. `-p`/`--local` runs get the top-level `--effort` flag; an RC host gets `CLAUDE_CODE_EFFORT_LEVEL` exported into its environment, which the sessions it hosts inherit. Precedence (high → low): in-session `/effort` → `--effort` flag → env var → settings.json → model default. `--effort ultracode` is not a launch value — ultracode is a harness session-mode; set it with `/effort` in-session.
- **History:** before 2026-07 nothing set effort at all — no flag reached RC hosts and no settings key existed — so every session silently ran at the model default (`high` for Fable). It's now deterministically `xhigh`.

## How isolation works

- `~/repos/<name>` — the **canonical clone**. Fetch-only. *Never work here directly.*
- `~/work/<task-id>` — a **git worktree** per task, on branch `agent/<task-id>`.

Every agent gets its own worktree, so two agents in the same repo never step on each other. Agents are told (via `~/.claude/CLAUDE.md`) to stay in their worktree, **never push to `main`**, and open a PR when done.

## Cleanup: reap, prune, and branch retirement

**When the backlog piles up** (an agent that spun up its own per-PR worktrees, or a wall of stranded ones after a pod bounce), `agent-run prune` clears them all at once. It prints a **dry-run plan** first; re-run with `--yes` to execute. Both `reap` and `prune` resolve the owning repo from git itself (so any worktree name works, in either repo) and are **safe by construction**: a worktree is removed only when it has **no uncommitted _tracked_ changes** — untracked scratch (`.claude/` locks, `node_modules`, build dirs) is discarded, but real WIP is kept and listed. A worktree that still holds tracked edits is skipped unless you add `--force` (`reap <id> --force` / `prune --yes --force`). Agents commit and open a PR before finishing, so a stranded worktree is essentially always safe to prune.

**Branches are retired conservatively.** After removing a worktree, the branch is deleted only when git can prove _offline_ it holds nothing beyond the base (`git branch -d`). A branch with its own local commits — genuinely unpushed WIP, **or** a squash-merged branch whose remote was deleted (git can't tell these apart offline, and it never trusts a branch _name_ to mean "merged") — is **kept**, and prune prints the exact `git branch -D …` to drop it once you've confirmed it landed. So a clean prune may leave a few merged branch refs behind; that's the price of never orphaning an un-pushed commit. Sweep them when you're sure with `git -C ~/repos/<name> branch -D <branch>`.

## What's installed, and what's authenticated

| Tool | Auth | Notes |
|---|---|---|
| `claude` | ✅ Max plan | credential on the PVC, self-refreshing |
| `codex` | ✅ ChatGPT plan | ditto |
| `git` / `gh` | ✅ `haynes-dev-bot` | App token, **all 23 repos**, re-minted every 40 min. Commits/PRs author as the bot. |
| `kubectl` / `flux` | ✅ in-cluster SA | **operator tier**: read everything (no secrets), plus pod delete, rollout restart, flux reconcile, Jobs, cronjob suspend. No exec, no secrets, no RBAC/node changes. |
| `helm`, `kustomize`, `yq`, `jq`, `sops`, `age`, `task`, `tofu`, `restic` | — | installed |
| `node` 24 + `pnpm` 11 + `tsx`, `python3` + `uv` | — | covers haynesnetwork, hass-sandbox, haynes-ops |
| `talosctl` / `omnictl` | ❌ absent | node/Omni work happens on the Mac |
| SOPS **age key** | ❌ deliberately absent | never leaves your machine without an explicit decision |

**MCP servers** (already wired into `claude`, no setup): `home-assistant`, `grafana-mcp`, `mcp-unifi`, `playwright` (headless chromium — it can drive your own apps at `https://<app>.haynesops.com` for UI testing).

## Rules the agents follow (and you should too)

- **GitOps.** Cluster changes go through a PR to `haynes-ops` and Flux applies them. The kubectl SA can restart and reconcile things, but it can't deploy — that's on purpose.
- **Never push to `main`.** Branch + PR.
- **Egress is a default-deny allowlist.** If a `curl`/`npm install` hangs, the domain probably isn't allowlisted — that's the CiliumNetworkPolicy doing its job, not a network glitch. Fix it in git (`kubernetes/main/apps/dev/dev-env/app/networkpolicy.yaml`), don't hunt for a proxy.

## Gotchas worth knowing

- **A pod restart kills tmux** (image roll, node drain, OOM). Worktrees, branches, and logs survive on the PVC — only the live pane is lost. `agent-run list` shows what's left (and a count of stranded worktrees), and `agent-run prune` clears the whole backlog in one shot.
- **Remote Control host mechanics (the `--interactive` default):** registration uses the standalone `claude remote-control` path (api.anthropic.com environments API), which is reliable; fresh worktrees are pre-trusted by agent-run so nothing stops at a dialog, and the host launches `--spawn=same-dir` so it registers immediately (no spawn-mode prompt) with phone/web sessions reusing the task's worktree — press `w` in the host to switch to per-session worktrees. If a host fails to connect, the reason is in `~/work/<id>.rc.log`.
- **Prefer a classic terminal TUI you type to directly?** Add `--local` (or answer *n* to the menu's `remote-control host? [Y/n]` question). You can still type `/remote-control` inside it, but the *in-TUI* path is flaky server-side (intermittent 401s on the code-session endpoints — anthropics/claude-code#30093 #30102 — and after 3 failed attempts that process disables RC until restarted). The RC-host default exists precisely so your workflow never depends on it. Historical silent-failure traps, all handled now: bridge egress (`bridge.claudeusercontent.com` allowed in the CNP since 2026-07-17) and permission flags at launch disabling in-TUI RC (`--local` launches flag-free; bypass comes from settings).
- **haynesnetwork is pre-warmed**: `pnpm install`, `build`, `typecheck`, and all **1,292 tests** (embedded Postgres) pass in this pod. Playwright browsers are installed.
- **Memory is 24Gi.** The monorepo test suite OOM'd at 8Gi — if a build dies mysteriously, check `kubectl top pod -n dev` before blaming the code.

## Where things live

| | |
|---|---|
| This pod's manifests | `haynes-ops` → `kubernetes/main/apps/dev/dev-env/` |
| Agent config + MCP servers | `.../app/resources/config/claude/` |
| `agent-run`, boot script | `.../app/resources/` |
| The saga (design, decisions, backlog) | `.agents/sagas/dev-env/` |
