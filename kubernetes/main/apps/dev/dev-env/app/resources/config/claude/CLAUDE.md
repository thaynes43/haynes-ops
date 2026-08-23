# You are in the haynes-ops dev-env pod

A 24/7 in-cluster agent workhorse (namespace `dev`, saga: `.agents/sagas/dev-env/`
in the haynes-ops repo). This file is GitOps-managed — edit it in
`kubernetes/main/apps/dev/dev-env/app/resources/config/claude/CLAUDE.md`, never here.

## Ground rules

- **Worktree per task.** Never work directly in `~/repos/<name>` (canonical clones).
  Create `git worktree add ~/work/<task-slug> -b agent/<task-slug>` and work there.
  Multiple agents share this pod; the canonical clones are fetch-only.
- **GitOps strictly** for the haynes-ops repo: cluster changes go through git + Flux.
  The kubectl ServiceAccount is OPERATOR tier (saga plan 05): broad read minus
  secrets, plus targeted runtime writes only (pod delete / rollout restart, flux
  reconcile + suspend, batch Jobs, CronJob suspend, and PVC delete **scoped to the
  `database` namespace** for CNPG destroy+re-clone). Deploys still go through git
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
| kubectl / flux | ✅ in-cluster SA | OPERATOR tier: read all-but-secrets; writes limited to pod delete, **pod exec**, rollout restart, flux reconcile/suspend, Jobs, CronJob suspend + PVC delete in `database` only (`kubectl cnpg destroy`, plugin at `~/.local/bin/kubectl-cnpg`). No secrets/RBAC (exec into a secret-mounting pod can read that pod's secrets — accepted, 2026-08-06) |
| gh / git push | ✅ haynes-dev-bot | App token, all repos, refreshed every 40min; commits/PRs author as the dev bot |
| sops / age | ❌ deliberately absent | the age key never enters this pod without an explicit decision |
| omnictl | ✅ READ-ONLY (Reader SA, proxied) | Omni `Reader` service account via `$OMNI_ENDPOINT` + `$OMNI_SERVICE_ACCOUNT_KEY` (SaaS `haynes.na-west-1.omni.siderolabs.io`); `omnictl get clusters/machinestatus` etc. Mutations denied by the Reader role — don't test by attempting them |
| talosctl | ✅ READ-ONLY (via Omni-proxied talosconfig) | No LAN-direct cert (SaaS tier denies break-glass). Fetch a proxied config at runtime: `omnictl talosconfig /tmp/tc` then `talosctl --talosconfig /tmp/tc -n <node-ip> dmesg/logs`. Read-only either way; traffic routes through Omni (WAN) — backlog 10 |
| terraform/tofu providers | ✅ GCP only (ADC = dev-env-agent@sigo-alumni-prod, roles/owner) | plan+apply against sigo-alumni-prod (multiple applies proven 2026-08-14/15); other clouds still credential-less |

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

## Declare disruptive work (avoid false escalations)

An autonomous remediation agent (`dev-env-ops`, rem-* lane) now watches critical
alerts and **fixes** them silently. If YOUR work trips an alert — restarting a
stateful app, suspending Flux, draining a node, deleting pods, rolling storage —
it can look like a real incident and pull that agent (or Tom) in for nothing.

**Before disruptive work, declare it:**

```bash
declare-activity start "restarting z2m + emqx (broker migration test)" 45m
# ... do the work ...
declare-activity end
```

Declarations are **scoped and TTL'd** on this pod's PVC; the remediation session
reads them and treats a matching alert as dev-caused rather than a fault. They
are a hint, not a mute — an alert outside your declared scope still gets handled,
and nothing suppresses a real incident. Keep the scope honest and the TTL tight.

## Model policy (Tom, 2026-08-23) — which model runs where

| Surface | Model | Why |
|---|---|---|
| **Automated agents** — alert-responder, upgrade-shepherd, dev-env-ops (both lanes) | **latest Opus**, pinned explicitly (`claude-opus-5` today) | They merge upgrades and touch production unattended; being wrong costs more than the quota. Pinned not aliased — alias repoints lag a launch by days. |
| **Tom's interactive dev-env work** | **latest Fable** when available | This is the surface Fable's plan quota is reserved for. |
| **Subagents dispatched from a dev-env session** | **Opus** | Keeps Fable headroom for the driving session. |
| **ANY pay-per-token API-key call** | **Sonnet 5** (`claude-sonnet-5`) | **NEVER Fable on API pricing, and never Opus.** Sonnet 5 is near-Opus at a fraction of the cost — and it can dispatch a pod claude-code agent (plan-served) for heavy lifting instead of billing tokens. |

**Bump procedure on a new Opus/Fable launch:** probe first
(`claude --model <full-id> -p 'reply with your model id'`), then update the pinned
ids in `upgrade-agent/{alert-responder,shepherd,dev-env-ops}` HRs + their scripts'
defaults. Agents are the tripwire — see the freshness contract below.

**Quota exhaustion is a real failure mode:** on 2026-08-23 the plan's Fable
credits ran out; sessions silently drifted to Opus and a fresh Fable dispatch
refused its first turn (`out of usage credits` + `Worked for 0s`). That is a
credit wall, not an agent-run bug — dispatch on `claude-opus-5` and tell Tom.

## Model pickers (agent-run) — freshness contract

`agent-run`'s pickers self-update wherever a machine-readable source exists:
codex model + effort rows come live from `~/.codex/models_cache.json`, claude
effort levels from `claude --help`, and the claude model *values* are aliases
(`opus` resolves server-side to the latest Opus — an alias is never stale, only
its label can be). Two surfaces still rot, and **agents are the tripwire for
both**:

- **Claude row labels** (no local manifest exists): if your own system prompt or
  in-session `/model` shows a model family newer than the picker labels, the
  labels are stale — never conclude the newer model is unavailable, and never
  "fix" the alias values. **This cuts both ways:** a label can also be newer
  than *your training data* — a prior agent may have refreshed it after a
  launch you don't know about. Never revert a label downward to match your
  priors; verify live first (`claude --model <full-id> -p 'model id?'` — cheap
  and definitive). An agent wrongly reverted Opus 5→4.8 this way on 2026-08-06.
  Also: alias repoints can lag a launch by days (`opus` served 4.8 while
  `claude-opus-5` was already live), so during a rollout window the label may
  legitimately be one tier ahead of what the alias serves.
- **Codex fallback rows**: `agent-run` prints a WARN when they drift from the
  live cache.

Either way the fix is the same: open a standard held-draft dev-env PR editing
`kubernetes/main/apps/dev/dev-env/app/resources/agent-run.sh` (labels/fallbacks
only). Draft because merging bounces this pod.
