# 03 — GitOps-managed agent configs (.claude / .codex)

**Status:** in review (branch `dev-env/03-configs-deploy`; image 0.3.0 with
playwright+chromium merged via PR #2035)
**Depends on:** 02
**Parallel with:** 04, 05

**MCP first wave (Tom-approved 2026-07-13):** home-assistant (cluster-local
ha-mcp.home-automation.svc:8086, secret path via the existing `ha-mcp` 1P item),
grafana-mcp (cluster-local mcp-grafana.observability.svc:8000/mcp), playwright
(headless chromium baked into image 0.3.0 — for UI/UX testing of cluster apps via
traefik-internal). **mcp-unifi**: approved but STUBBED — the exact server
package/config Tom previously used is unrecoverable from this Mac; do not guess a
community package (supply-chain risk) — needs Tom's pointer, then wire like the
others. **outline**: deferred (not selected). Codex MCP parity: follow-up.

## Goal

The *declarative* parts of Claude Code and Codex configuration live in git and roll
out via Flux; the *mutable* parts (auth, history, session state) live on the PVC and
never fight Flux. Secrets referenced by MCP servers come from ExternalSecrets as env
vars, never as literals in git.

## Design

The split (saga Hard news #5):

| Path | Kind | Source |
|------|------|--------|
| `~/.claude/settings.json` | declarative | ConfigMap |
| `~/.claude/CLAUDE.md` | declarative | ConfigMap |
| `~/.mcp.json` (or per-repo `.mcp.json`) | declarative | ConfigMap |
| `~/.claude/.credentials.json`, history, todos | state | PVC (untouched) |
| `~/.codex/config.toml` | declarative | ConfigMap |
| `~/.codex/auth.json` | state | PVC (untouched) |

- `configMapGenerator` over `resources/config/**` (same auditable-in-PR-diffs move as
  the shepherd's scripts).
- ConfigMaps mount at `/opt/dev-env/config/`; `dev-init.sh` (initContainer or
  entrypoint prelude) symlinks/copies them to their `$HOME` destinations on every
  boot. Symlink where the CLI tolerates it (config becomes live-updatable via
  reloader), copy where it insists on writing.
- MCP server env: `.mcp.json` supports `${VAR}` expansion — reference env vars fed by
  the ExternalSecret (e.g. `GRAFANA_SA_TOKEN`, `HA_TOKEN`). `config.toml` MCP blocks
  get env the same way. Decide the initial MCP set: grafana, home-assistant are the
  obvious cluster-local ones (both reachable in-cluster — extend the CNP for their
  in-cluster ports rather than public FQDNs).
- A repo-level `CLAUDE.md` for the pod (worktree convention, "you are in the dev-env
  pod", dispatch etiquette) — this is where agent behavioral defaults live.

## Acceptance

- Editing `resources/config/claude/settings.json` in git → commit → reconcile →
  reloader (or next pod roll) → the running pod sees the change; auth state survives.
- `claude mcp list` / Codex MCP equivalents show the GitOps-defined servers, with
  secrets resolved from env, in a fresh pod with no manual steps.
