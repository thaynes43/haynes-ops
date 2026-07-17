# dev-env — your in-cluster agent workspace

You're in a 24/7 pod in the `main` cluster (namespace `dev`). Everything here is
GitOps-managed: this README lives at
`kubernetes/main/apps/dev/dev-env/app/resources/config/home/README.md` in **haynes-ops** —
edit it there, not here (a pod restart overwrites this copy).

> **Tip:** `Ctrl+Shift+V` renders this markdown. `` Ctrl+` `` opens a terminal.

---

## 1. Start an agent (the 30-second version)

Open a terminal and run:

```bash
agent-run run --repo haynesnetwork --agent claude --interactive
```

That drops you into a live Claude session inside a **fresh git worktree** on its own branch.
Talk to it like you would anywhere else. Swap `--agent codex` for the ChatGPT plan.

**Permissions are skipped by default** — `claude --dangerously-skip-permissions` /
`codex --dangerously-bypass-approvals-and-sandbox`. You don't need to type those; the pod
*is* the sandbox (isolated worktree, scoped ServiceAccount, default-deny egress), so
approving every edit inside it would be theater. Add **`--safe`** if you want the normal
permission prompts back for a particular task.

To fire off a task and walk away instead:

```bash
agent-run run --repo haynes-ops --agent claude -p "Bump the coredns chart and open a PR"
```

---

## 2. tmux — the only 3 keys you need

Agents run inside **tmux** so they survive you closing the browser. tmux uses a "prefix"
key: you tap **Ctrl+B**, *release*, then press one more key.

| Keys | What it does |
|---|---|
| **Ctrl+B**, then **d** | **Detach.** The agent keeps working. Close the tab, shut the laptop. |
| **Ctrl+B**, then **[** | **Scroll back.** Arrows/PageUp to read history, **q** to stop scrolling. *(Mouse scroll won't work — this is the one that trips everyone.)* |
| **Ctrl+B**, then **c** | New window (a second shell in the same session). |

That's it. Everything else is optional.

> **⚠️ code-server steals Ctrl+B** (it's VS Code's "toggle sidebar" shortcut), so in the
> integrated terminal here the detach keystroke usually never reaches tmux. Detach from
> any *other* terminal instead: **`agent-run detach`** (picker) or
> `tmux detach-client -s task-<task-id>`. Closing the browser tab also just detaches —
> the agent keeps running either way. The only thing that actually *kills* a session is
> `exit`/`Ctrl+D` at its shell prompt (or `agent-run reap`).

---

## 3. Managing tasks

```bash
agent-run list               # what's running + start times + the attach command to copy
agent-run attach [task-id]   # jump back into a live session (scrollback intact)
agent-run detach [task-id]   # kick attached clients off — the session keeps running
agent-run reap [task-id]     # kill it + delete the worktree (the log is kept)
```

**Skip the id and you get an arrow-key picker** (↑/↓ or j/k, Enter selects, `q` cancels)
showing each task's start time. `reap`'s picker also lists **stranded worktrees** — a pod
restart kills tmux but the PVC keeps the worktree, so reap those leftovers before they
balloon the PVC (it asks `y/N` before deleting).

`list` prints the **task id** (e.g. `haynesnetwork-0714-010301`) and the ready-to-paste
`agent-run attach …` line under it. If you paste the tmux session name (`task-…`) by
mistake, `attach` strips the prefix for you. `attach` is nested-tmux aware: from inside
another task's session it switches you over instead of erroring.

`reap` refuses to delete a worktree with uncommitted work — add `--force` if you mean it.

**Task logs** live at `~/work/<task-id>.log` even after a reap.

---

## 4. How isolation works (why you won't get collisions)

- `~/repos/<name>` — the **canonical clone**. Fetch-only. *Never work here directly.*
- `~/work/<task-id>` — a **git worktree** per task, on branch `agent/<task-id>`.

Every agent gets its own worktree, so two agents in the same repo never step on each other.
Agents are told (via `~/.claude/CLAUDE.md`) to stay in their worktree, **never push to `main`**,
and open a PR when done.

---

## 5. What's installed, and what's authenticated

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

**MCP servers** (already wired into `claude`, no setup): `home-assistant`, `grafana-mcp`,
`mcp-unifi`, `playwright` (headless chromium — it can drive your own apps at
`https://<app>.haynesops.com` for UI testing).

---

## 6. Rules the agents follow (and you should too)

- **GitOps.** Cluster changes go through a PR to `haynes-ops` and Flux applies them. The
  kubectl SA can restart and reconcile things, but it can't deploy — that's on purpose.
- **Never push to `main`.** Branch + PR.
- **Egress is a default-deny allowlist.** If a `curl`/`npm install` hangs, the domain probably
  isn't allowlisted — that's the CiliumNetworkPolicy doing its job, not a network glitch. Fix it
  in git (`kubernetes/main/apps/dev/dev-env/app/networkpolicy.yaml`), don't hunt for a proxy.

---

## 7. Gotchas worth knowing

- **A pod restart kills tmux** (image roll, node drain, OOM). Worktrees, branches, and logs
  survive on the PVC — only the live pane is lost. `agent-run list` shows what's left, and
  the `agent-run reap` picker flags the stranded worktrees so you can clean them up.
- **haynesnetwork is pre-warmed**: `pnpm install`, `build`, `typecheck`, and all **1,292 tests**
  (embedded Postgres) pass in this pod. Playwright browsers are installed.
- **Memory is 24Gi.** The monorepo test suite OOM'd at 8Gi — if a build dies mysteriously,
  check `kubectl top pod -n dev` before blaming the code.
- **Driving an agent from your phone (the default):** `agent-run run --repo <r> --agent
  claude --interactive` starts the task as a **Remote Control host** — connect from the
  Claude mobile app or claude.ai/code; `agent-run attach <id>` shows the status screen
  with the QR code + URL. This uses the standalone `claude remote-control` registration
  path (api.anthropic.com environments API), which is reliable; fresh worktrees are
  pre-trusted by agent-run so nothing stops at a dialog. If a host ever fails to
  connect, the reason is in `~/work/<id>.rc.log` — no more silent failures.
- **Prefer a classic terminal TUI?** Add `--local`. You can still type `/remote-control`
  inside it, but know that the *in-TUI* path is flaky server-side (intermittent 401s on
  the code-session endpoints — anthropics/claude-code#30093 #30102 — and after 3 failed
  attempts that process disables RC until restarted). The RC-host default exists
  precisely so your workflow never depends on it. Historical silent-failure traps, all
  handled now: bridge egress (`bridge.claudeusercontent.com` allowed in the CNP since
  2026-07-17) and permission flags at launch disabling in-TUI RC (`--local` launches
  flag-free; bypass comes from settings).

---

## 8. Where things live

| | |
|---|---|
| This pod's manifests | `haynes-ops` → `kubernetes/main/apps/dev/dev-env/` |
| Agent config + MCP servers | `.../app/resources/config/claude/` |
| `agent-run`, boot script | `.../app/resources/` |
| The saga (design, decisions, backlog) | `.agents/sagas/dev-env/` |
