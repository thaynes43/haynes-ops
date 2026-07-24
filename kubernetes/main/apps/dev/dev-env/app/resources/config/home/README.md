# dev-env

24/7 pod in cluster `main`, namespace `dev`. GitOps-managed: this file is `kubernetes/main/apps/dev/dev-env/app/resources/config/home/README.md` in **haynes-ops** — edit it there, not here (a pod restart overwrites this copy). `Ctrl+Shift+V` renders markdown; `` Ctrl+` `` opens a terminal.

## Synopsis

```
agent-run [<repo>] [flags]               dispatch an agent in a fresh worktree
agent-run list                           live tasks + attach commands
agent-run attach [<task-id>]             enter a session (no id → picker)
agent-run detach [<task-id>]             disconnect clients; session runs on
agent-run reap   [<task-id>] [--force]   kill session + remove worktree
agent-run prune  [--yes] [--force]       bulk-remove stranded worktrees
agent-run codex-remote [stop]            pod-level codex phone/web control
```

Bare `agent-run` prompts for every choice it isn't given, **tool-first**: repo → agent (claude|codex) → mode → model → effort. Each step after the agent shows only that tool's options. Flags skip the matching prompt; a fully-flagged call runs unattended. `agent-run <repo>` is positional shorthand for `--repo`. (`agent-run run …` still works as a hidden alias.)

## Dispatch flags

```
--repo <name>       same as the positional <repo>
--agent claude|codex
--base <ref>        worktree base (default: origin/HEAD)
-p "<task>"         fire-and-forget (mode=task); output → ~/work/<id>.log
--interactive       claude → both (TUI here + phone/web); codex → local TUI
--local             terminal TUI here only, no remote (implies --interactive)
--safe              keep permission prompts (default: bypassed)
--model <m>         claude alias/id (fable|opus|sonnet|haiku) or codex slug (gpt-5.6-sol|…)
--effort <level>    claude low|medium|high|xhigh|max; codex adds ultra; default xhigh
```

The repo picker lists the bot's GitHub repos by last push, falling back to `~/repos` by fetch time when offline. Permissions are bypassed by default (`--dangerously-skip-permissions` / `--dangerously-bypass-approvals-and-sandbox`): the pod is the sandbox — isolated worktree, scoped ServiceAccount, default-deny egress. `--safe` restores prompts.

## Interactive modes

The mode picker offers, per tool:

- **`both`** (claude, default) — `claude --remote-control`: a terminal TUI you type to in the tmux pane **and** a session drivable from the Claude app / claude.ai/code at once (registers via the reliable api.anthropic.com/v1/code/sessions path). Detach the tmux session for headless-local, or drive it from your phone — so `both` covers every claude use; there's no separate headless mode.
- **`local`** — the same terminal TUI, no remote session registered.
- **`task`** — fire-and-forget (`-p`): runs headless, output to `~/work/<id>.log`.

**Codex** interactive is `local`/`task` only — codex has no per-session remote like claude's `--remote-control`. Its phone/web control is a **pod-level** daemon: **`agent-run codex-remote`** starts it and prints a manual pairing code for the ChatGPT app / Codex web. It's one daemon per pod (the phone picks the working dir), so it bypasses the per-worktree isolation; **`agent-run codex-remote stop`** shuts it down.

## Models & effort

Model is picked first; effort then offers only that model's levels. Default effort is **`xhigh`** in every mode (valid for all current models). Unset model → the tool's own default. `/model` and `/effort` override in-session.

- **claude** — `fable` (pod default `claude-fable-5[1m]`, 1M context), `opus`, `sonnet`, `haiku`. Effort `low|medium|high|xhigh|max`, uniform across models. Passed as top-level `--model`/`--effort` flags, which compose with `--remote-control`, so they apply in **all** claude modes.
- **codex** — `gpt-5.6-sol` (default), `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`, `gpt-5.2`. Effort differs by model: all take `low|medium|high|xhigh`; `max` only on gpt-5.6; `ultra` only on sol/terra. Passed as `-m <model> -c model_reasoning_effort=<level>` (codex has no `--effort` flag or `/effort` command — it folds effort into `/model`).

`ultracode` is a harness session-mode, not a launch value — set it with `/effort` in-session.

## tmux

Sessions run under tmux and survive a disconnect. Prefix is **Ctrl+B** (tap, release, then a key):

```
Ctrl+B d    detach (agent keeps running)
Ctrl+B [    scroll — arrows/PageUp, q to exit copy-mode (mouse scroll won't)
Ctrl+B c    new window
```

code-server intercepts Ctrl+B (sidebar toggle) in its integrated terminal, so detach from another terminal: `agent-run detach`, or `tmux detach-client -s task-<id>`. Closing the tab detaches; only `exit`/`Ctrl+D` at the prompt (or `reap`) kills a session.

## Task management

`list` prints each task id (e.g. `haynesnetwork-0714-010301`) with its paste-ready `attach` line and a stranded-worktree count. Omitting the id on `attach`/`detach`/`reap` opens a picker (↑/↓ or j/k, Enter, `q`). `attach` tolerates a `task-` prefix and switches clients when run from inside another session. Logs persist at `~/work/<id>.log` (kept through reap). The `both` mode's session is inspectable by attaching; `agent-run codex-remote`'s daemon runs in the `codex-remote` tmux session (`tmux attach -t codex-remote`).

## Isolation

- `~/repos/<name>` — canonical clone, fetch-only. Never work here.
- `~/work/<id>` — per-task worktree on branch `agent/<id>`.

One worktree per task, so concurrent agents in a repo never collide. Agents are instructed (`~/.claude/CLAUDE.md`) to stay in their worktree, never push `main`, and open a PR.

## Cleanup

`reap <id>` kills the session and removes its worktree; `prune` bulk-removes stranded worktrees (dead session, worktree left on the PVC) — dry-run by default, `--yes` executes. Both resolve the owning repo from git and remove a worktree only when it has **no uncommitted tracked changes**: untracked scratch is discarded, tracked WIP is kept and reported (`--force` overrides). A worktree's branch is deleted only when git proves offline it holds nothing beyond base (`git branch -d`); a branch with its own commits — unpushed WIP, or squash-merged with the remote gone — is kept, with the exact `git branch -D` printed. Sweep confirmed-merged refs with `git -C ~/repos/<name> branch -D <branch>`.

Standalone clones made directly under `~/work` (not worktrees) are skipped by `prune` and must be removed by hand after checking for unpushed commits.

## Toolchain & auth

```
claude              Max plan         credential on PVC, self-refreshing
codex               ChatGPT plan     credential on PVC, self-refreshing
git / gh            haynes-dev-bot   app token, all repos, re-minted ~40m; commits author as the bot
kubectl / flux      in-cluster SA    operator tier: read (no secrets), pod delete, rollout restart,
                                     flux reconcile, jobs, cronjob suspend. No exec/secrets/RBAC/node.
helm kustomize yq jq sops age task tofu restic    installed
node 24 / pnpm 11 / tsx / python3 / uv            installed
talosctl / omnictl  READ-ONLY        node triage: talosctl dmesg/logs/get (os:reader), omnictl status
                                     (Omni Reader SA). No mutate — reboot/reset/apply denied by role.
SOPS age key        absent           deliberate — never leaves the Mac
```

MCP servers wired into claude (no setup): `home-assistant`, `grafana-mcp`, `mcp-unifi`, `playwright` (headless chromium; drives apps at `https://<app>.haynesops.com`).

Tool versions are pinned at image build (`scripts/dev-env/Dockerfile`, Renovate-tracked). Updating is a version bump → image rebuild → helmrelease digest re-pin → redeploy; a pod restart alone does **not** update them.

## Rules

- Cluster changes go through a PR to `haynes-ops`; Flux applies them. The SA can restart/reconcile, not deploy.
- Never push `main`. Branch + PR.
- Egress is a default-deny allowlist. A hanging `curl`/`npm install` usually means the domain isn't allowlisted (CiliumNetworkPolicy) — fix `kubernetes/main/apps/dev/dev-env/app/networkpolicy.yaml`, don't hunt for a proxy.

## Gotchas

- A pod restart (image roll, node drain, OOM) kills tmux; worktrees, branches, and logs persist on the PVC. `list` shows survivors and the stranded count; `prune` clears them.
- Memory is 24Gi — the monorepo suite OOM'd at 8Gi. Check `kubectl top pod -n dev` before blaming a build.
- haynesnetwork is pre-warmed: `pnpm install`/`build`/`typecheck` and its 1,292-test suite (embedded Postgres) pass here; Playwright browsers installed.

## Layout

```
kubernetes/main/apps/dev/dev-env/              manifests
    app/resources/                             agent-run, boot script, bashrc
    app/resources/config/{claude,codex,home}/  agent + MCP config, this README
.agents/sagas/dev-env/                         design, decisions, backlog
```
