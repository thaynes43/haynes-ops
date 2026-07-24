# You are in the haynes-ops dev-env pod

A 24/7 in-cluster agent workhorse (namespace `dev`, saga: `.agents/sagas/dev-env/`
in the haynes-ops repo). This file is GitOps-managed — edit it in
`kubernetes/main/apps/dev/dev-env/app/resources/config/claude/CLAUDE.md`, never here.

## Ground rules

- **Worktree per task.** Never work directly in `~/repos/<name>` (canonical clones).
  Create `git worktree add ~/work/<task-slug> -b agent/<task-slug>` and work there.
  Multiple agents share this pod; the canonical clones are fetch-only.
- **GitOps strictly** for the haynes-ops repo: cluster changes go through git + Flux.
  The kubectl ServiceAccount here is read-only (operator tier pending, saga plan 05)
  — do not fight RBAC denials, they are the design.
- **Never push to main.** Branch + PR, always.
- Egress is a default-deny allowlist (CiliumNetworkPolicy `dev-env`). If a fetch
  times out, the domain probably isn't allowlisted — propose adding it via git, do
  not look for proxies/workarounds.

## Tool auth status (know before you reach)

| Tool | Auth | Notes |
|---|---|---|
| claude | ✅ Max plan | credential on PVC, self-refreshes |
| codex | ✅ ChatGPT plan | `~/.codex/auth.json`, self-refreshes |
| kubectl / flux | ✅ in-cluster SA | READ-ONLY (get/list/watch) |
| gh / git push | ✅ haynes-dev-bot | App token, all repos, refreshed every 40min; commits/PRs author as the dev bot |
| sops / age | ❌ deliberately absent | the age key never enters this pod without an explicit decision |
| talosctl | ✅ READ-ONLY | `os:reader` talosconfig at `$TALOSCONFIG`; direct-to-node `dmesg`/`logs`/`get`/`version` for hardware triage. Mutations (reboot/reset/apply/upgrade) are denied by the cert role — don't test by attempting them |
| omnictl | ✅ READ-ONLY | Omni `Reader` SA (`OMNI_ENDPOINT` + `OMNI_SERVICE_ACCOUNT_KEY`); machine/cluster status. NOTE: main nodes are on SaaS Omni, self-hosted `omni.haynesops.com` is separate (backlog 10). Admin Omni ops stay elsewhere (omni-service-account runbook) |
| terraform/tofu providers | ❌ no cloud creds | plan/validate only |

## MCP servers (GitOps-managed, `~/.config/dev-env/mcp.json`)

- `home-assistant` — cluster-local HA MCP (entities, automations, logs)
- `grafana-mcp` — PromQL/LogQL, dashboards (cluster-local)
- `playwright` — headless chromium for UI/UX testing of cluster apps
  (reach them via their `https://<app>.haynesops.com` internal ingress)
- `mcp-unifi` — read-only UniFi/UDM introspection (clients, RSSI, topology;
  cluster-local SSE). Gotcha: per-site tools want the legacy site code `default`
  (`internalReference`), not the UUID from `list_sites`.

## Sessions

Run inside tmux (session `main`) so work survives disconnects. For phone-driven
sessions, start `claude` and use `/remote-control`.
