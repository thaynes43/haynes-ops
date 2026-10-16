# Cursor My Machines on dev-env v1

The worker is the phone/web executor. Ordinary `agent` is a terminal session.
It runs as the existing `dev` user and inherits the pod's current credentials,
RBAC, mounts and Cilium policy. The isolated checkout is not a security sandbox.
The model loop runs on Cursor's backend. No new API billing credential is needed
for the existing browser login.

## Startup and persistence

`post-ready.sh` requests both worker profiles only after code-server readiness,
settle and the existing boot-loop circuit breaker. Installation and recovery run
in detached tmux session `cursor-worker-v1`, never in the synchronous boot path.
The installer pins the official Linux x64 archive to `2026.10.14-0f9edde` and
SHA-256 `74fac931fe3578980d070701872b7dab54eb87a9abf9b6e2e6be19beeb9167a7`.
It verifies the archive and executable version before replacing the two official
symlinks. A failed download preserves an existing installation. It does not run
the moving `cursor.com/install` script at every boot.

Persistent paths on the existing home PVC:

| Path | Purpose |
| --- | --- |
| `~/.config/cursor/auth.json` | Vendor browser login, observed mode 0600; never commit, copy or print it |
| `~/.local/share/cursor-agent/` | Pinned binaries and persisted worker identity |
| `~/cursor/haynes-ops` | Isolated anchor checkout outside the `~/work` sweeper |
| `~/.local/state/dev-env-cursor/worker.log` | Restricted supervisor log |
| `~/.local/state/dev-env-cursor/disabled` | Persistent operator stop-on-next-boot marker |
| `~/.local/share/cursor-agent-dev-env/` | Existing separate `dev-env` machine identity; do not replace |
| `~/repos/dev-env` | Tom's explicitly selected existing dev-env checkout; never reset or fetch at worker boot |
| `~/.local/state/cursor-worker-dev-env/` | Independent dev-env supervisor lock, log and disabled marker |

The default profile remains `dev-env-v1-haynes-ops` in tmux `cursor-worker-v1`.
The `dev-env` profile runs the separate machine named `dev-env` in tmux
`cursor-worker-dev-env`, retaining the data directory used for its initial
registration. It reuses the existing browser login without copying credentials.
Each profile exposes exactly one repository. The dev-env checkout must already
exist; a missing checkout fails before launching a worker. Both roots receive
the existing staged rules/MCP preparation without activating pending MCPs.
The dev-env profile intentionally uses the requested canonical checkout rather
than moving the already registered machine to a new anchor. Tom drives this
workspace; implementation work must still follow the repo's task-worktree rules.

An existing anchor is never reset, fetched, or replaced. A missing anchor is
created as a detached worktree from the canonical clone's existing `origin/main`;
the canonical clone must already exist. Work must use separate task branches and
worktrees. One supervisor lock prevents duplicate managed workers; the tmux name
also protects the original live pilot from replacement. My Machines itself can
accept concurrent assignments: this implementation does not serialize tasks.

The supervisor retries failures with 15/30/45/60 second delays, stops after five
short failures, and resets that count only after a ten-minute run. A failed
`agent status` check only consumes that retry budget (it may be a network blip);
a missing login file stops recovery and requires the human browser login flow.
A failed status check at boot is treated the same way. SIGTERM stops the worker
child at once, including during a backoff delay. Past 10 MiB the log is
copy-truncated to `worker.log.1`, checked at `up`, before each worker start and
every five minutes while the worker runs. No automation,
model task, MCP discovery, fleet, desktop share, or new credential is started.

```sh
bash /opt/dev-env/scripts/cursor-worker.sh status
bash /opt/dev-env/scripts/cursor-worker.sh status dev-env
tail -n 50 ~/.local/state/dev-env-cursor/worker.log
# After diagnosing a stopped supervisor:
bash /opt/dev-env/scripts/cursor-worker.sh up
bash /opt/dev-env/scripts/cursor-worker.sh up dev-env
```

An existing tmux session is left alone; inspect and stop it deliberately before
switching the pilot to the managed supervisor. Do not kill a claimed worker.

## Rules and MCP adapters

`cursor-config.py` renders `.cursor/rules/dev-env-shared.mdc` with `alwaysApply`
and the exact shared `config/claude/CLAUDE.md` suffix. It also instructs Cursor to
read repository CLAUDE.md and applicable AGENTS.md. Existing repository
instructions are preserved. Generated files are locally excluded from Git.
The receipt records source hashes and an explicit unverified parity state.
Rules are instructions, not an OS-enforced security boundary.

All ten MCP registrations are rendered to `.cursor/dev-env-mcp.pending.json` as
local commands. This is intentionally not an automatically loaded `mcp.json`.
The stdio wrapper uses the same canonical shared MCP source as Claude/Codex and
expands environment references only in the local process. URL-based MCPs on
Cursor My Machines run from Cursor's backend; copying private URLs directly
would fail. The adapter uses the pinned official MCP SDK to relay JSON-RPC over
Streamable HTTP, retaining IDs, schemas, pagination and session cleanup. It does
not open a listener. HTTP logging is disabled, exception text is suppressed,
TLS verification stays enabled and cross-origin redirects are refused by the SDK.

Activation remains a separate decision: an earlier authenticated discovery
operation was denied by automatic approval review. Do not enable or test around
that denial. After explicit authorization for the endpoint/credential pairings,
review the generated manifest, write the exact canonical source SHA-256 into
`~/.config/cursor/dev-env-mcp-approved.sha256`, and configure the reviewed commands
through the supported Cursor interface. Do not overwrite existing project MCP
settings. Confirm worker-side selection/loading separately from terminal loading.
The wrapper refuses to run (before package installation or secret expansion)
when that marker is absent or stale. This marker is an operational safeguard,
not a new security boundary against the same user who owns the files.

| Server | Exact existing endpoint template | Existing authentication |
| --- | --- | --- |
| home-assistant | `http://ha-mcp.home-automation.svc.cluster.local:8086${HA_MCP_SECRET_PATH}` | Existing secret path appended locally |
| grafana-mcp | `http://mcp-grafana.observability.svc.cluster.local:8000/mcp` | None in shared source |
| mcp-unifi | `http://mcp-unifi.observability.svc.cluster.local:3000/mcp` | None in current shared source; new bearer migration is separate |
| haynesnetwork | `http://haynesnetwork-mcp-hop.frontend.svc.cluster.local:8080/mcp` | None in shared source |
| vexa | `http://vexa-vexa-gateway.ai.svc.cluster.local:8000/mcp` | `X-API-Key: ${VEXA_API_KEY}` |
| blender | `http://blender-authoring.dev.svc.cluster.local:8000/mcp` | None in shared source |
| audio | `http://audio-authoring.dev.svc.cluster.local:8000/mcp` | None in shared source |
| cigar-journal | `https://cigars.haynesnetwork.com/mcp` | `Authorization: Bearer ${CIGAR_JOURNAL_TOKEN}` |
| playwright | `playwright-mcp --browser chromium --headless` | No explicit credential |
| outline | `uvx mcp-outline` | Existing `OUTLINE_API_KEY`; API URL `https://wiki.sigoalumni.org/api` |

The denied read-only HTTP check requested `initialize`,
`notifications/initialized`, paginated `tools/list`, and session `DELETE` only.
It requested no tool invocation, credential creation, rotation, or dashboard copy.
No request executed. Five of the eight HTTP entries had no explicit auth; three
used the exact existing local references shown above. The pending adapters have
not been tried against production and do not establish catalog/schema parity.

Provider-native Codex/Claude tools, account connectors, image generation,
desktop control, voice, scheduling and provider subagents are not made portable
by this config. Skills may refer to unavailable tools. Record these as unavailable
or unverified; do not claim identical capabilities from matching server names.

## Bounded restart acceptance

Before any ConfigMap merge, remember Reloader will restart the Recreate pod.
Coordinate from the Mac, not from an agent inside the pod. Confirm the sessions
are idle and let them shut down cleanly; work that continues afterwards gets a
short cold-start handoff prompt (goal, branch/PR, next step) rather than a
resumed session. The supported `claude --resume` / `codex resume` stay available
as a fallback. Do not build custom preservation or resume tooling: committed and
pushed work is the durable state, and the existing boot sweeper's `rescue/`
branches cover stray uncommitted edits. Record the old Git revision and image
digest and keep the home PVC.

Acceptance after the GitOps rollout:

1. Replacement pod Ready; code-server healthy; no restart loop; post-ready
   circuit breaker clear. Claude/Codex existing remote paths recover.
2. `agent --version` matches the pin. `agent status --format json` reports
   authenticated when parsed to a boolean only. Never print auth file contents.
3. `agent worker --name dev-env-v1-haynes-ops --worker-dir ~/cursor/haynes-ops
   --idle-release-timeout 0 debug --json` reports the retained worker ID and
   the haynes-ops registration. Also run diagnostics with `--name dev-env
   --worker-dir ~/repos/dev-env --data-dir ~/.local/share/cursor-agent-dev-env`:
   retain machine ID `e215ce95-8cd3-408b-939c-064554a22c39`, repo `thaynes43/dev-env`,
   and confirm both machine names in the server's visibility response. Check
   only nonsecret fields; local argument parsing alone is not visibility proof.
4. In native Cursor select repository dev-env → environment dev-env. For
   haynes-ops, retain My Machines → dev-env-v1-haynes-ops.
   Select a model actually listed by that account (observed `grok-4.7-low`). Run
   one read-only prompt: report `id -u`, `pwd`, and paths for git, gh, kubectl,
   claude, codex and agent; print no environment or credentials, edit nothing,
   call no MCPs and stop. Record run metadata and shell output. A terminal run
   is not a substitute for the remote worker acceptance.
5. Verify the shared-rule hash and the CLI inventory from the worker. After
   separately authorized MCP activation, compare normalized tool names/input
   schemas and approved harmless reads in both terminal and worker paths.

Rollback: stop the worker only after releasing any active claim; place the
persistent disabled marker; revert the GitOps startup change and reconcile from
the Mac. Retain auth, identity, logs and worktrees for recovery. Never delete or
recreate the PVC to repair worker startup.

Sources: [My Machines](https://cursor.com/docs/cloud-agent/self-hosted/my-machines),
[mobile](https://cursor.com/docs/cloud-agent/mobile),
[rules](https://cursor.com/docs/rules), [MCP](https://cursor.com/docs/mcp).
