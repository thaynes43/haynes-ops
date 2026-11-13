# dev-env agent rules: rationale and history

This file holds the reasons, incident narratives and long detail cut from the v2
common rules
(`kubernetes/main/apps/dev-env-system/session-config/app/config/claude/CLAUDE.md`)
when OpenProject story #499 shortened them on 2026-11-09. The rules file links here
once. Agents look things up here when a rule's reason matters; nothing tells them to
read it in full. The complete earlier text is in git: that path at commit
`0ac79ab4a3490d0ccdd13b7312d51df353d6a90b`.

## Owner rulings changed on 2026-11-09

Tom chose "Merge after your review (Recommended)" by phone prompt on 2026-11-09 for
two changes. Nothing else of substance in his dated rules changed.

- **Stop boundary.** The earlier text said: "At a stop boundary, preserve work and a
  durable checkpoint, then end the relevant work. Do not automatically start another
  story, review or test cycle." and "A new story gets a fresh bounded session and its
  handoff, not an ever-growing chapter context." Now an owner session that finishes
  a story may take the next ready story in the same epic, within its budget, once
  that story has its own tracker record. An explicit pause, hold, pending question
  or budget threshold still stops it, and it still starts no extra review or test
  cycle on its own.
- **Docs first** (dev-env CLAUDE.md, not the common rules). A design document is
  required only for changes to access or auth, data lifecycle or retention, and API
  or CRD contracts. Other behaviour changes are described in the PR body. An
  Accepted ADR is still never edited.

## Added on 2026-11-13: summoned sessions

The section "Summoned sessions" is new (OpenProject story #808, slice d of #746).
It is not a ruling by Tom. dev-env DESIGN-064 5.6 asks for it: "One short paragraph
... says what does not apply to a summoned session (the tracker, phone prompts, the
worktree-per-story rule) and that its contract and `agent-run report` govern." That
is the ops-profile coordinator's ruling Q-08 with its conditions (DESIGN-064 10.1),
made inside gate T-1, which Tom answered on 2026-11-11 ("each automation gets a rule
in git that fixes what it may start, and v2 writes what the session is told",
DESIGN-064 8.1).

- **Why it is needed.** An automation starts a summoned session (cigar curation
  first, then remediation, escalation and upgrade work orders). Nobody opened a
  tracker story for it, no coordinator waits for its messages, and a daily job must
  not push a question to Tom's phone. Without the paragraph the common rules tell it
  to do all three.
- **Why the platform's mark defines it.** A summoned session reads text an attacker
  can influence (alert annotations, release notes, catalog data). If a sentence in
  that text could switch rules off, it would. So the test is a fact the session
  cannot write: the Session's `caller`, which the operator's API sets from the
  authenticated caller, and the report route, which answers only such a session.
- **What stays.** Everything else: secrets, a denial is a stop, running sessions are
  sacred, GitOps, the CPU budget, credentials and the MCP cautions. The contract may
  ask for a PR; then the branch and PR rules apply to it.

## Incidents behind the hard rules

- **2026-08-23, Fable credit wall.** The plan's Fable credits ran out. Sessions
  drifted to Opus, and a new Fable session refused its first turn
  (`out of usage credits`, `Worked for 0s`). It is a credit wall, not a bug: use
  `claude-opus-5-5` and tell Tom. It is no reason for a Codex driver to switch
  providers.
- **2026-08-29, token family revoked.** Two processes refreshed the same Claude Max
  login, and the provider revoked the whole token family. Hence one owner per
  rotating refresh token: the keeper alone refreshes the Max login and the Codex
  login, and no session pod runs `/login`, `claude auth login` or `codex login`.
- **2026-09-29, CronJob suspend undone.** A `kubectl` suspend of a Flux-managed
  CronJob lasted under 3 minutes: kustomize-controller took the field back at its
  next reconcile. Hold a suspend with `flux suspend kustomization` for that app, or
  `spec.suspend: true` in git.
- **2026-10-05, CPU starvation.** CPU burners and wide parallel test runs in the v1
  pod, which had no CPU limit, pushed control-plane node talosm02 to load 222 on 20
  cores from 23:42 to 00:12Z. EMQX, traefik, authentik and cloudnative-pg went into
  liveness-kill loops. The kubelet was not starved; BestEffort pods with CPU weight 1
  got no CPU on the saturated node.
- **2026-11-09, blanket pause.** A "wait for Meatball" instruction was read as a
  pause on all work and stalled the session indefinitely (OpenProject #481). The
  rule now says "wait for X" pauses only the work that depends on X.
- **2026-11-09, long socket paths.** Tests that bind a Unix socket (path limit 108
  bytes) failed in every session pod. First read: a long `TMPDIR`. Measured the same
  day (OpenProject #506): the agent image sets `GOTMPDIR=/home/dev/.cache/go-tmp`, and
  in the image's Go (1.27.3) `t.TempDir()` is made under `GOTMPDIR` when it is set,
  whatever `TMPDIR` says (`src/testing/testing.go`: `os.MkdirTemp(os.Getenv("GOTMPDIR"),
  pattern)`; older Go versions used `TMPDIR` only). With `GOTMPDIR` set, `t.TempDir()` was `/home/dev/.cache/go-tmp/<Test>.../001`
  while `os.TempDir()` was `/tmp`; with it unset, `/tmp/<Test>.../001`. The image keeps
  the setting, which holds compiler scratch off the capped `/tmp`; a test that binds a
  socket makes its own short directory (dev-env `internal/testsock`, dev-env#1015).

## Detail cut from the rules

### Work definitions and tracker

- The common policy applies to v1, v2 and every harness and provider: Claude Code,
  Codex, Gemini, local models and future harnesses. Each harness receives it through
  its supported instruction mechanism before taking a story. Source changes alone do
  not prove that a running agent has loaded them.
- Independence means a clear owned outcome once the stated prerequisites are met; it
  does not mean there are no dependencies. A story is not an entire epic or an
  open-ended chapter.
- Comparing Jira or another tracker is optional future work in its own bounded story
  and implies no installation or account.
- Images older than 2.33.0 lack `opwork`; there, run
  `python3 ~/repos/haynes-ops/scripts/openproject/opwork.py` from a haynes-ops
  checkout.
- opwork says when Accepted is left to an authorized accepter.

### Pod and volume

- `tini` is PID 1. agentd renders the config at boot and runs the agent in tmux
  session `agent`.
- The pod runs on a worker node at a low priority that never preempts anything. The
  scheduler may preempt it; a new pod then starts on the same volume.
- Size class S, M or L (M by default) sets CPU and memory requests and limits. The
  kernel kills processes over the memory limit.
- No approval prompts (DESIGN-001 D-23): Claude runs with
  `--dangerously-skip-permissions`, Codex with approval `never`. The boundary is the
  platform: identity, egress and what the pod holds.
- The volume is `home-<session>`, typically 20Gi, one writer, on `gasha01-rbd`.
- The full capacity procedure: read the `/tmp` cap from your own pod's `spec.volumes`
  entry `tmp` at `emptyDir.sizeLimit`; if that is unavailable the cap is unknown and
  the coordinator qualifies it before a heavy job. Measure `/tmp` usage (`du -sx`)
  separately from filesystem space and inodes (`df -h`, `df -i`). Choose a per-job
  byte and inode budget plus a reserve. Caps and measurements are not reservations.
  Preserve all changed and untracked source, and verify its hashes, before a heavy
  job. `dev-env-storage-run` (plan and run, explicit sources, budget, reserve and
  timeout) and `dev-env-test-run` are cooperative helpers, not permission
  boundaries; neither was in the 2.95.0 image.
- Keep `GOCACHE` and `GOMODCACHE` under `~`, and leave `TMPDIR` at its default. The
  image sets `GOTMPDIR` under `~` for compiler work directories; on the image's Go it
  also decides where `t.TempDir()` lands (see 2026-11-09 above).
- Clones are partial (`--filter=blob:none`), so `git log -p` or `git blame` over old
  history fetches over the network. Clone other repos under `~/repos` so rescue
  finds them.
- `~/.shared` holds `memory/` (Claude's memory per repo), `rescue/` and `logs/`.
- Rescue branches and bundles are never pushed. A task never reruns after a
  container restart, because a second run could open a second PR.
- Removed as untrue in a 2.95.0 session pod: "`AGENTD_SESSION` holds the session as
  JSON" and "`agentd ctl status` prints what agentd reports" (the agent's shell has
  neither variable set, and `agentd ctl status` exits 1).

### agent-run, access and egress

- `agent-run` was planned in numbered plans; commands that shipped lost their plan
  markers on 2026-11-09. Not built then: `tools`, `lease`, `gpu`, `restart`,
  `codex-remote`.
- agent-run refuses an alias and an effort level the model would not honour before
  it sends anything. Defaults: agent `claude`, model `$DEV_ENV_CLAUDE_MODEL`, effort
  `xhigh` or the highest level below it the model takes.
- Gone from v1: `attach` and `detach` for agents from an ordinary session, `prune`
  and `sweep` (the operator's reaper does that), and handing a session its first
  instruction with `tmux send-keys` (use `msg`).
- The privileged ServiceAccounts that `pods/exec` excludes: headlamp, the Flux
  controllers, external-secrets and the CSI provisioners. Allowed Jobs run as their
  namespace's `default` or a listed ServiceAccount, mount no unlisted Secret, and
  use no hostPath, host network or host PID. Break-glass replaces borrowing a
  privileged identity.
- Cilium enforces the tiers by pod label, and Hubble records every lookup and flow.
  Profile `ops` has only the platform tier until its own tier is built.
- The gh token lasts 60 minutes. The managed `gh` wrapper and git's credential helper
  are the delivery route. A connected GitHub app is a separate identity; its denial
  does not mean the wrapper is missing or forbidden. Do not repair that app or switch
  routes.
- Proxmox: the PVEAuditor read token rides the profile; the operator tier comes only
  as the `credential-proxmox` grant. The `hw-ssh` credential grant leases the one
  shared SSH key into the pod's tmpfs, where anything in the pod can copy it while it
  is installed; removal is not revocation.
- Omni: `omnictl talosconfig "$XDG_RUNTIME_DIR/tc"`, then
  `talosctl --talosconfig "$XDG_RUNTIME_DIR/tc" -n <node-ip> dmesg`. A separate Omni
  Reader account for routine reads was proposed and, on 2026-11-09, not provisioned.

### MCP servers

- agentd copies `mcp.json` to `~/.config/dev-env/mcp.json` and renders the same list
  into Codex's `[mcp_servers.*]`. Codex speaks streamable HTTP and stdio only, so a
  networked server needs a `/mcp` endpoint. A `${VAR}` placeholder resolves only from
  the pod's environment, which the profile's Secrets fill; a variable exported in a
  shell never reaches the registration. An in-cluster server that will not connect
  usually means its network policy does not admit `dev-agents` yet.
- `playwright` reaches cluster apps at `https://<app>.haynesops.com` (an egress grant
  may be needed).
- `mcp-unifi` per-site tools want the legacy site code `default`, not the UUID from
  `list_sites`. Verify a write from unpoller or AP metrics. `list_wlans` returns
  passphrases in `x_passphrase`.
- `blender`: one scene author per work order; save under `/workspace` and fetch
  through `/artifacts/`. Runbook `.agents/runbooks/blender-authoring.md`.
- `audio`: Stable Audio Small-SFX on CPU. One generation runs and four may wait;
  fetch `/artifacts/<job-id>/output.wav` and check its checksum. Runbook
  `.agents/runbooks/audio-authoring.md`.
- `cigar-journal`: the production journal at `https://cigars.haynesnetwork.com/mcp`.
  An idle MCP session expires after 30 minutes.
- `haynesnetwork`: reached through a hop that adds the token. Seerr auto-requests the
  watchlist every 3 minutes. `undo_last_change` reverses the last change within a
  day. Runbook: haynesnetwork OPS-015.
- Voice agents are the opposite of a session pod. Home Assistant sends every tool
  schema of every attached server on every voice turn (35 cigar-journal tools cost
  about 2 s a turn, 2026-09-22), so each room agent gets only the servers its room
  needs, each kept small.

### Model policy

| Surface | Model | Why |
|---|---|---|
| Automated agents (alert-responder, upgrade-shepherd, dev-env-ops) | `claude-opus-5-5`, pinned | They merge upgrades and touch production unattended; being wrong costs more than the quota. |
| Tom's interactive Claude Code work | `claude-opus-5-5`; Fable only by name | Fable's quota is scarce and shared with Tom's own use. |
| Native Claude Code subagents | `opus-worker` (`claude-opus-5-5`, `xhigh`), `sonnet-worker` (`claude-sonnet-5-5`, `high`) | Two tiers by difficulty and visibility. |
| Tom's interactive Codex work | `gpt-6-astra`, effort `max` | The Codex driving model. |
| Native Codex collaboration subagents | `gpt-6.1-sol`, effort `xhigh` | Since 2026-10-03. |
| Pay-per-token Claude API-key calls | `claude-sonnet-5-5` | Never Fable or Opus on API pricing. |

- Ids on 2026-11-09. Claude: `claude-fable-5-2` (Claude Code 2.1.321 or later),
  `claude-fable-5-1`, `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-opus-5`,
  `claude-haiku-4-5`; effort `low|medium|high|xhigh|max` on the 5-family, none on
  Haiku 4.5. Codex: `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6-sol`, `gpt-6-luna`,
  `gpt-5.6-sol|terra|luna`, `gpt-5.5`; effort `low|medium|high|xhigh`, plus `max` on
  the GPT-6 and 5.6 tiers. Quote any `[1m]`-suffixed id.
- An alias resolves on the client, against the image's pinned CLI, and can serve an
  older tier.
- Bump procedure: check the CLI floor and bump the agent image's pin if needed
  (thaynes43/dev-env), probe the id, then update `DEV_ENV_CLAUDE_MODEL` in
  `dev-env-templates`, the `model:` line in `agent-opus-worker.md` or
  `agent-sonnet-worker.md`, and v1's pins by v1's procedure until cutover.
- Subagent detail: Sonnet is the default for routine work; if unsure, start there.
  User-visible work always goes to Opus, and the driver reviews it before it ships.
  Without the installed agent types, pass `model: "opus"` or `model: "sonnet"` to the
  Agent tool. Codex drivers use `collaboration.spawn_agent` with
  `fork_turns: "none"`, `gpt-6.1-sol`, `reasoning_effort: "xhigh"` and a
  self-contained work order; UX design and end-user text stay on the driving Astra
  session.
- Remote Control coordinators: coordination has its own bounded story, budget and
  handoff and does not keep a whole saga in one context. A small direct coordinator
  task needs no fleet.
