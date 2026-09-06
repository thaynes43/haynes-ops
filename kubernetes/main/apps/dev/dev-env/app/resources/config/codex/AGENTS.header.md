# Codex in the haynes-ops dev-env pod

You are **Codex**, running in the same 24/7 in-cluster pod as Claude Code. Everything
below the horizontal rule is the pod's shared ground rules — one document for both
agents, written in Claude Code's vocabulary. Read it as binding, with these
translations:

| Claude Code term | For you (Codex) |
|---|---|
| `AskUserQuestion` — push a question to Tom's phone | your `request_user_input` tool: ONE question at a time, at the moment it arises, never a prose "open questions" list |
| `ListAgents` / `SendMessage` — peer sessions | not available to you; coordinate through git, work orders and the PVC |
| `claude --remote-control`, `/remote-control` | the pod-level daemon `agent-run codex-remote` (see Sessions) |
| Fable / Opus / Sonnet rows, `--effort` | your default here is `gpt-6-astra` at `max` (config.toml); dispatch with `agent-run --agent codex --model <slug> --effort <level>` |
| "Fable budget is scarce — default work to Opus 5 subagents" | the same shape applies to you: Tom's ChatGPT plan quota is small. For heavy multi-step work, dispatch a Claude Code session (`agent-run --agent claude --model claude-opus-5 -p "<task>"`) instead of grinding through it yourself |
| `~/.claude/CLAUDE.md`, agent memory | your instruction chain is this file + each repo's `AGENTS.md`, or its `CLAUDE.md` where there is none (config.toml `project_doc_fallback_filenames`) |

Two Codex-specific traps:

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
