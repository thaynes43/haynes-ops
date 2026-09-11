# Codex in the haynes-ops dev-env pod

You are **Codex**, running in the same 24/7 in-cluster pod as Claude Code. Everything
below the horizontal rule is the pod's shared ground rules — one document for both
agents, written in Claude Code's vocabulary. Read it as binding, with these
translations:

| Claude Code term | For you (Codex) |
|---|---|
| `AskUserQuestion` — push a question to Tom's phone | your `request_user_input` tool: ONE question at a time, at the moment it arises, never a prose "open questions" list |
| `ListAgents` / `SendMessage` — peer sessions | your native `collaboration.list_agents`, `collaboration.send_message`, `collaboration.followup_task`, and related tools for the current task tree; independently launched CLI sessions still coordinate through git, work orders, and the PVC |
| `claude --remote-control`, `/remote-control` | the pod-level daemon `agent-run codex-remote` (see Sessions) |
| Fable / Opus / Sonnet rows, `--effort` | your driving model remains `gpt-6-astra` at `max` (config.toml); your native subagents use exact model `gpt-5.6-sol` at `xhigh` |
| Claude Code's Opus subagent rule | use native Codex collaboration instead: `collaboration.spawn_agent` with `fork_turns: "none"`, `model: "gpt-5.6-sol"`, `reasoning_effort: "xhigh"`, and a self-contained work order |
| `~/.claude/CLAUDE.md`, agent memory | your instruction chain is this file + each repo's `AGENTS.md`, or its `CLAUDE.md` where there is none (config.toml `project_doc_fallback_filenames`) |

Codex-specific traps:

- **Native subagents are not `agent-run` sessions.** Use the collaboration tools
  for ordinary delegation within this conversation. `agent-run` starts a separate
  CLI session and is only for that explicit need, including when Tom explicitly
  requests a Claude Code session. Do not use Claude Code as the default fallback
  for Codex work.
- **Phone threads start in a scratch directory** (`~/Documents/Codex/<date>/<slug>`).
  For anything touching a repo, follow the worktree rule first: `git worktree add
  ~/work/<task-slug> -b agent/<task-slug>` from `~/repos/<name>`, then work there —
  never in the scratch dir, never in `~/repos/<name>` itself.
- **Your MCP servers are the same as Claude's**, rendered at boot from one GitOps
  `mcp.json`. If one is missing or fails, the fix is in that file (haynes-ops,
  `kubernetes/main/apps/dev/dev-env/app/resources/config/claude/mcp.json`) —
  never `codex mcp add`, which the next boot overwrites.

This file is generated at boot by dev-init from `config/codex/AGENTS.header.md` +
`config/claude/CLAUDE.md` in haynes-ops. Edit those, never this copy.

---
