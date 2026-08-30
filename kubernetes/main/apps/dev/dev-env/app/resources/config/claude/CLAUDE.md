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
- **Never push to main — but DO merge your own PRs.** Branch + PR, always, then
  **squash-merge it yourself** once required checks are green. "Never push to main"
  forbids *direct pushes*; it has never meant "wait for Tom to click merge". A green,
  unmerged PR is not finished work, it is work you blocked. This applies to every
  repo you have write access to (haynes-ops, haynesnetwork, libretto, …).
- **Only QUESTIONS wait on the owner.** Push each one to his phone with the
  **AskUserQuestion tool, ONE at a time**, at the moment it arises — never batched,
  never as a prose "open questions" list in your final message (he does not receive
  those, and the work stalls). Writing a `Q-NN` entry into an ADR/design is the
  *record*, not the *ask* — do both, then fold the answer back in as a ruling.
  Verify a question's premise before spending one: an ask built on an unchecked
  inference wastes his attention and can smuggle a false premise into a signed-off
  decision.
  - **The one exception — dev-env PRs that bounce this pod.** Everything under
    `apps/dev/dev-env/app/resources/**` (the scripts, this CLAUDE.md, `mcp.json`,
    codex config) is mounted from a ConfigMap on a workload annotated
    `reloader.stakater.com/auto: "true"`, so **merging restarts the pod and kills the
    running session mid-turn**. Open those as **held drafts**, say plainly why, and
    let Tom merge at a natural break. This is the *only* category that waits.
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
- `outline` — the sigoalumni wiki (stdio, `uvx mcp-outline`)
- `vexa` — meeting bot: transcripts, recordings (cluster-local)
- `cigar-journal` — the prod journal/catalog MCP at
  `https://cigars.haynesnetwork.com/mcp`. **Currently 401s**: dev-init expands
  `${CIGAR_JOURNAL_TOKEN}` with envsubst against the POD environment, and that
  variable is not there — it lives only in the PVC `~/.bashrc`, which dev-init
  never sources — so the header registers as an empty `Bearer ` (visible as a
  whitespace warning in `claude mcp list`). Fix pending in haynes-ops#2673.
  Until it lands, reach the API directly with
  `curl -H "Authorization: Bearer $CIGAR_JOURNAL_TOKEN"` from a shell (which
  DOES source `.bashrc`); it speaks streamable-HTTP MCP, so send `initialize`
  first and carry the returned `Mcp-Session-Id`, or you get `400 no valid
  session`. A 400 about sessions means auth PASSED.

A placeholder only resolves if the variable is in the POD environment
(ExternalSecret-fed, visible in `/proc/1/environ`) — dev-init's envsubst runs
before any shell profile. A value exported from `~/.bashrc` reaches your shell
and never reaches the MCP registration.

## Sessions

**Your own:** run inside tmux (session `main`) so work survives disconnects. For
phone-driven work, start `claude` and use `/remote-control`.

**Dispatching another: `agent-run`.** It is the only supported way to start one —
it creates and branches the worktree, pins model + effort, and wires the tmux
session. Bare `agent-run` walks every choice; flags skip the walkthrough.

| mode | flag | what you get |
|---|---|---|
| task | `-p "<task>"` | headless, fire-and-forget; log at `~/work/<id>.log` |
| local | `--local` | a terminal TUI in this pod only |
| both | `--interactive` | that TUI **and** a phone/claude.ai-drivable session |

```bash
agent-run --repo <name> --agent claude --interactive \
  --model 'claude-fable-5[1m]' --effort xhigh
# -> task <repo>-<mmdd-HHMMSS>, tmux session task-<id>
```

Quote the model id — `claude-fable-5[1m]` carries glob metacharacters. Prefer the
id over a bare alias (`fable`), which resolves CLIENT-side against the pinned CLI
and can silently serve an older tier; see the freshness contract below.

**`-p` cannot combine with `--interactive`/`--local`** — agent-run rejects the
contradiction rather than guessing. So an interactive session starts with an
empty prompt, and you hand it its first instruction by typing into its pane:

```bash
tmux send-keys -t task-<id> -l "<the whole prompt, ONE line>"
tmux send-keys -t task-<id> Enter
```

`-l` sends the text literally; without it tmux interprets the payload. One line
matters: an embedded newline is an Enter, which submits early and strands the
rest of your prompt as a second turn.

**Confirm it started before handing it work** — a dispatch can come up dead and
look fine from the outside:

```bash
tmux capture-pane -p -t task-<id> | tail -20
```

Expect the banner (model, effort, `Claude Max`) and, for `both`, the
`/remote-control is active` line with its claude.ai URL. `out of usage credits`
with `Worked for 0s` is the plan's Fable wall, not an agent-run bug — redispatch
on `claude-opus-5` and tell Tom.

Managing them: `agent-run list` · `attach [<id>]` · `detach` · `reap [<id>]
[--force]` · `prune [--yes]` (bulk-clean stranded worktrees; dry-run without
`--yes`). Reap when a task is done — a stranded worktree outlives its session.

Two behaviours worth knowing before they surprise you:

- `both` deliberately strips `CLAUDE_CODE_OAUTH_TOKEN` and falls back to
  `~/.claude/.credentials.json`. The long-lived env token cannot register
  `/v1/code/sessions`, so a session started with it silently never appears on the
  phone/web list. Keep the `/login` ceremony current or `both` breaks while
  `task` and `local` keep working.
- Cross-session messaging is OFF in this pod: `/tmp` is world-writable without
  the sticky bit, so the socket directory cannot be created. Sessions coordinate
  through git, work orders, and the PVC — not by messaging each other.

## Declare disruptive work (avoid false escalations)

An autonomous remediation agent (`dev-env-ops`, rem-* lane) now watches critical
alerts and **fixes** them silently. If YOUR work trips an alert — restarting a
stateful app, suspending Flux, draining a node, deleting pods, rolling storage —
it can look like a real incident and pull that agent (or Tom) in for nothing.

**Before disruptive work, declare it:**

```bash
# --scope is REQUIRED: the namespaces/apps/nodes your work can disturb.
# --ttl defaults to 45m, caps at 8h (2h for the `cluster` wildcard).
declare-activity start "restarting z2m + emqx (broker migration test)" \
  --scope home-automation,zigbee2mqtt,emqx --ttl 45m
# -> declared act-142317-91 ... (the id is printed, and `list` reprints it)
# ... do the work ...
declare-activity end act-142317-91   # ALWAYS end early when you finish
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
