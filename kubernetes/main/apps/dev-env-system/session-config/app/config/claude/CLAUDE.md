# You are in a dev-env v2 session pod

This pod runs one agent session and nothing else. The dev-env operator built it from an
`AgentSession` in namespace `dev-agents`, on a worker node, with its own volume. Other
agents run in other pods. The design is DESIGN-001 in
[thaynes43/dev-env](https://github.com/thaynes43/dev-env) (saga
`.agents/sagas/distributed-dev-env/`).

This file is GitOps-managed (DESIGN-001 D-49). Edit it in haynes-ops
`kubernetes/main/apps/dev-env-system/session-config/app/config/claude/CLAUDE.md`, never
here: `~/.claude/CLAUDE.md` is a link to a read-only mount. Codex reads the same text in
`~/.codex/AGENTS.md`.

Some commands below are marked with a plan, such as *(plan 02)*. They arrive with that
plan of the dev-env saga. If one is not in your pod yet, do not improvise a stand-in.

## Ground rules

- **Worktree per task.** agentd cloned your repo to `~/repos/<repo>` and made your
  worktree `~/work/<session>` on branch `agent/<session>`. Work there. A second task in
  the same session gets its own worktree:
  `git -C ~/repos/<repo> worktree add ~/work/<task-slug> -b agent/<task-slug>`. Never
  work in `~/repos/<repo>` itself. For another repo, clone it under `~/repos` first
  (see *Your volume*), so rescue finds it.
- **GitOps strictly** for the haynes-ops repo: cluster changes go through git and Flux.
  Your Kubernetes identity allows reads and a few runtime writes only (see *Kubernetes
  access*). Deploys go through git. Do not fight an RBAC or admission denial: it is the
  design. A `kubectl` suspend of a Flux-managed CronJob lasts only until Flux's next
  reconcile of its Kustomization, because kustomize-controller takes over the field
  (2026-09-29, within 3 minutes). To hold a suspend, `flux suspend kustomization` that
  app as well, or set `spec.suspend: true` in git.
- **Never push to main, but DO merge your own PRs.** Branch and PR, always, then
  squash-merge it yourself once the required checks are green. "Never push to main"
  forbids direct pushes. It has never meant "wait for Tom to click merge". A green,
  unmerged PR is work you blocked. This applies to every repo you can write to.
- **Finish work in flight.** Sessions end: a task session is suspended an hour after its
  task ends, and an idle interactive one after 3 days. Nobody reads a closing "here is
  what I left open" message. Anything not merged *and deployed* when the session ends
  is lost, and the next agent rediscovers it cold. So a defect you find is yours: the
  same bug in sibling files too, and the stale instruction you followed to get there. A
  review finding gets fixed, or a concrete reason on the PR why it is wrong; never
  "polish, merging anyway". Merged is not done when the repo has a deploy chain. "Worth
  a follow-up", "out of scope here", "leaving open" and "if you want" are a tripwire: do
  it, or park it where it survives you, as a repo `backlog/` item or a GitHub issue with
  cold-start context, and only for work that needs a design decision. Chat text, PR
  comments and "Not verified" lines report; they do not hand off. (Tom, 2026-09-09;
  full rule in hass-sandbox `.agents/rules/finish-in-flight-work.md`.)
- **Every repo gets the Claude Code PR reviewer.** When you create a repo, or work in one
  that lacks them, add the Claude Code review and `@claude` workflows and the
  `CLAUDE_CODE_OAUTH_TOKEN` secret, by haynes-ops `.agents/runbooks/new-repo-setup.md`.
  The review is advisory: read the findings before merging, and fix each one or answer
  it with a concrete reason.
- **Only questions wait on the owner.** Push each one to Tom's phone with the
  **AskUserQuestion tool, one at a time**, at the moment it arises. Never batch them and
  never leave a prose "open questions" list in your final message: he does not receive
  it, and the work stalls. Writing a `Q-NN` entry into an ADR or design is the record,
  not the ask: do both, then fold the answer back in as a dated ruling. Check a
  question's premise before you spend one. In a headless task (`-p`) nobody answers
  AskUserQuestion: put the question, with its options and your recommendation, in the
  PR body, and finish everything it does not block.
  - **The one exception: PRs that restart the v1 pod.** Everything under haynes-ops
    `kubernetes/main/apps/dev/dev-env/app/resources/**` is mounted into the v1 dev-env
    pod, which restarts when it changes and kills the sessions running there. Open
    those as **held drafts**, say why, and let Tom merge them. The v2 config
    (`apps/dev-env-system/session-config/`, this file included) and `dev-env-templates`
    restart nothing: merge them as usual.
- **Secrets stay out of git.** haynes-ops and other repos are public. Never commit or
  paste into a PR, issue or log: tokens, `CLAUDE_CODE_OAUTH_TOKEN`, Claude or Codex
  login files, login URLs and codes, GitHub App keys and minted tokens, 1Password
  values, SSH keys, Proxmox tokens. Never write one under `~/.shared`, which every
  session reads. A secret you must keep in a file goes in `$XDG_RUNTIME_DIR` (tmpfs),
  not in a worktree: rescue copies untracked files.

## Your pod

- **One session.** The pod is named after your session. `AGENTD_SESSION` holds the
  session as JSON (repo, base, agent, mode, model, effort, prompt, limits).
  `agentd ctl status` prints what agentd reports to the operator.
- **Processes.** `tini` is PID 1. agentd rendered this config at boot and runs your
  agent in tmux session `agent`. A task's readable log is `~/work/<session>.log`.
- **Where it runs.** On a worker node, never a control-plane node, at a low priority
  that never preempts anything. The scheduler may preempt your pod. If it does, a new
  pod starts on the same volume.
- **Its size.** The size class (S, M or L; M by default) sets CPU and memory requests
  and limits. `DEV_ENV_CPU_LIMIT` is the CPU limit in whole CPUs. The kernel kills
  processes over the memory limit.
- **Its filesystem.** The root filesystem is read-only. You run as uid 1000 with no
  capabilities, no `sudo` and no package manager for the system. Writable: `/home/dev`
  (your volume), `/tmp` and `$XDG_RUNTIME_DIR` (`/dev/shm/run-1000`). `/tmp` is 8Gi of
  node disk, lost with the pod. Past 8Gi the kubelet evicts the pod and the session
  fails, so large scratch goes under `~`. Install tools under `~/.local`;
  `~/.local/bin` leads `PATH`.
- **No approval prompts** (D-23). Claude runs with `--dangerously-skip-permissions` and
  Codex with approval `never`. The boundary is the platform: what your identity may do,
  where the pod may connect, and what it holds.
- **No ingress.** Nothing can connect to your pod. A dev server you start is reachable
  from inside the pod only: test it with the playwright MCP server at `localhost`.
- **Other sessions are other pods.** ListAgents and SendMessage see only the sessions
  and subagents in this pod. Two Remote Control sessions reach each other natively.
  Anything else goes through `agent-run msg <id> "<text>"` *(plan 02)*. A headless
  task is not addressable; it reports through its log and its PR.
- **Not here, compared with the v1 pod:** code-server, the post-ready standby, the
  worktree sweeper, the shared home on one PVC, the hw-ssh key and the Proxmox operator
  token (Q-07), and the Max `/login` file.

## Your volume

- **`/home/dev` is your own volume**, `home-<session>`: 20Gi, one writer, on
  `gasha01-rbd`. It holds `~/repos/<repo>`, `~/work/<session>`, `~/.claude`,
  `~/.codex` and caches. It survives pod restarts, drains and suspend. Archive deletes
  it.
- **Clones are partial** (`git clone --filter=blob:none`). git fetches old file
  contents on demand, so `git log -p` or `git blame` over old history is slower and
  needs the network. Clone another repo the same way:
  `git clone --filter=blob:none https://github.com/thaynes43/<name> ~/repos/<name>`.
- **`~/.shared` is one CephFS volume that every session mounts.** `memory/` is Claude's
  memory per repo, shared by every session on that repo, as in v1. `rescue/` holds
  rescue bundles, and `logs/` task logs. Keep it small and keep secrets out of it.

## Suspend, rescue, resume, archive

| When | What happens |
|---|---|
| a task ends | suspended after 1 hour |
| an interactive or remote session is idle | suspended after 3 days *(plan 02)* |
| suspended | archived after 7 days |
| the image or `dev-env-templates` changes | drained at its next idle moment: a new pod on the same volume, and the conversation resumes on the new version *(plan 04)* |

- **Rescue runs before every suspend.** agentd commits each worktree's tracked edits
  and new untracked files to a local branch `rescue/<worktree>-<stamp>`. Gitignored
  files are not kept. It leaves your worktree, index and branch as they were. Then it
  writes a git bundle of every local ref that origin lacks to
  `~/.shared/rescue/<session>/`. Rescue branches and bundles are never pushed to
  GitHub.
- **Some worktrees are refused:** one with a merge or rebase in progress, an untracked
  nested repo, more than 50 MiB untracked, or a submodule with work of its own. A
  refusal keeps the volume and blocks archive until a human decides.
- **Archive deletes the volume** once a verified bundle covers every unpushed ref, or
  agentd proved every repo clean and pushed. A bundle is kept 30 days.
- **Resume** *(plan 02)*: `agent-run resume <id>` starts a new pod on the same volume
  and continues the conversation. `agent-run rescue restore <bundle>` starts a new
  session from a bundle after archive.
- **A task runs once.** If the container restarts mid-task, agentd reports the task
  `interrupted` and does not start it again, because a second run could open a second
  PR.
- **Background processes do not survive** a drain or resume. Start dev servers and
  watchers again.

So the work that counts is pushed: commit, push your branch, open the PR, merge it.
Rescue is a safety net for a session that dies. It is not a way to hand work on.

## agent-run (v2)

`agent-run` is one static binary. It talks to the operator's API with your pod's own
token. A session you create is your child: the API records you as its parent, limits
how many children and how deep, and gives a child no wider profile than yours.

| Command | Does | Ships in |
|---|---|---|
| `agent-run -p "<task>" [--repo r] [--model <full id>] [--effort e] [--size S\|M\|L] [--profile p]` | a task session in its own pod; prints its name. `--prompt-file <file>` (`-` for stdin) carries a long work order | plan 01 |
| `agent-run list` | sessions and their phase | plan 01 |
| `agent-run show <id>` | one session: its task, its last heartbeat, how the task ended | plan 01 |
| `agent-run reap <id>...` | ends sessions: rescue, then delete | plan 01 |
| `agent-run fleet` | sessions, nodes, and what the reaper will do next | plan 01 |
| `--local`, `suspend`, `resume`, `msg <id> "<text>"`, `rescue list\|restore`, `declare-activity` | interactive sessions and the lifecycle | plan 02 |
| `--interactive` (TUI and Remote Control), `auth status` | phone sessions; the Max login's state | plan 03 |
| `restart <id>`, `--agent codex`, `codex-remote` | drain on demand; Codex sessions and the codex hub | plan 04 |
| `grant request\|list\|use\|release`, `breakglass` | access beyond the baseline, approved by Tom or a policy | plan 07 |
| `tools list\|attach\|release\|get\|put` | tool pods (Blender, audio and others) | plan 08 |
| `lease`, `gpu`, `--agent opencode` | GPUs, LLM leases, local models | plan 09 |

Gone from v1: `attach` and `detach` for agents (you have no exec in `dev-agents`; Tom
attaches from the workbench), `prune` and `sweep` (the operator's reaper does that),
and handing a session its first instruction with `tmux send-keys` (use `msg`).

The defaults are v1's: agent `claude`, model `$DEV_ENV_CLAUDE_MODEL`, effort `xhigh`
(or the highest level the model takes). agent-run refuses an alias and an effort level
the model would not honour before it sends anything. `-p` cannot combine with
`--interactive` or `--local`. To follow a session you started, use `agent-run show`;
you cannot read another pod's pane.

## Kubernetes access

`kubectl` and `flux` run as your pod's ServiceAccount, `dev-agents/dev-env-agent`.

- **Reads:** everything cluster-wide except Secrets, plus the `dev-env.haynesops.com`
  resources.
- **Writes:** v1's runtime verbs, each narrowed by the admission policy
  `dev-env-agent-guard`:
  - pod delete, and `pods/exec` except into pods whose ServiceAccount is on the
    privileged list (headlamp, the Flux controllers, external-secrets, the CSI
    provisioners);
  - `kubectl rollout restart` (only the `restartedAt` annotation changes);
  - `flux reconcile` and `flux suspend|resume` (only those fields);
  - Jobs that run as their namespace's `default` ServiceAccount or a listed one, mount
    no unlisted Secret, and are not privileged (no hostPath, host network or host PID);
  - CronJob `spec.suspend`, ExternalSecret `force-sync`;
  - PVC delete in `database` only, for a CNPG destroy and re-clone
    (`kubectl cnpg destroy`).
- **Nothing in `dev-env-system`, `dev-agents` or `dev-tools`:** no write, delete or
  exec, your own pod included. Act on sessions through `agent-run` only.
- **More is a grant** *(plan 07)*: `agent-run grant request` names a role from the
  grant catalog, the namespaces, a TTL and the reason, and waits for Tom's approval or
  a standing policy. Until plan 07, ask Tom. Never borrow a privileged identity, such
  as headlamp's: break-glass replaces that path.

## Egress

Cilium enforces three tiers by pod label; Hubble records every lookup and flow.

- **Web** (profiles `full` and `dev`): DNS for any name, and TCP 80 and 443 to any
  public address. Not the LAN (`192.168.0.0/16`), other private ranges, in-cluster
  addresses, or any other port. git over SSH does not work: use HTTPS.
- **Platform** (every profile): the Kubernetes API, the operator's API, the shared
  in-cluster MCP services, and tool pods you attached *(plan 08)*.
- **Controlled:** LAN hosts, other in-cluster services, other ports and SSH, only by an
  egress grant *(plan 07)* or a standing policy in git.
- Profile `ops` (summoned sessions) gets a short DNS list instead of the web tier.

A fetch that fails to a public host on 80 or 443 is worth a retry. Anything else needs
a grant or a policy change in haynes-ops through git. Never look for a proxy, tunnel or
other workaround.

## Credentials in this pod

What a pod holds is set by its profile in `dev-env-templates` (label
`dev-env.haynesops.com/profile`). If something below is missing, the profile does not
carry it. Do not look for another path.

| Tool | What you have |
|---|---|
| claude | Task and local sessions: the static token `CLAUDE_CODE_OAUTH_TOKEN`. It serves inference only and cannot register Remote Control. Remote sessions *(plan 03)* run on access tokens the keeper writes. The keeper alone refreshes the Max login: **never run `/login` or `claude auth login` in a session pod**, because a second refresher revokes the whole token family (2026-08-29). Tom renews the login on the console. |
| codex | *(plan 04)* The keeper writes an access-token-only `~/.codex/auth.json`. Never `codex login` here, for the same reason. |
| gh, git push | The haynes-dev-bot token at `/creds/gh_token`, re-minted by the keeper every 40 minutes; each lasts 60. `gh` is agentd's wrapper (`~/.local/bin/gh`) and git's credential helper reads the file: both read it at every call. Do not export `GH_TOKEN`. Any other tool reads the file inline: `curl -H "Authorization: Bearer $(cat /creds/gh_token)" …`. Profile `ops` gets the haynes-ops-bot token instead: haynes-ops only, no workflows. |
| kubectl, flux | *Kubernetes access* above. |
| pve | The read tier (PVEAuditor token), where the profile carries it. The Proxmox API is on the LAN, so `pve` needs an egress grant even to read. The operator tier comes only as a short-lived credential grant *(plan 07)*. |
| hw-ssh | Not in any session pod (Q-07). Plan 07 issues a short-lived SSH certificate by credential grant. |
| omnictl, talosctl | Read-only, where the profile carries `OMNI_SERVICE_ACCOUNT_KEY`: `omnictl get clusters`, and `omnictl talosconfig /tmp/tc` then `talosctl --talosconfig /tmp/tc -n <node-ip> dmesg`. Never test the Reader role by attempting a mutation. |
| sops, age | Absent by design. |

## MCP servers

agentd registers these with Claude at boot from one GitOps list, haynes-ops
`kubernetes/main/apps/dev-env-system/session-config/app/config/claude/mcp.json`, copied
to `~/.config/dev-env/mcp.json`, and renders the same list into Codex's
`[mcp_servers.*]`. Change a server in that file, never with `claude mcp add` or
`codex mcp add`: the next boot overwrites both. Codex speaks streamable HTTP and stdio
only, so a networked server needs a `/mcp` endpoint.

A `${VAR}` placeholder resolves only from the pod's environment, which the profile's
Secrets fill. agentd expands it at boot. A variable you export in a shell never reaches
the registration. An in-cluster server that will not connect usually means its network
policy does not admit `dev-agents` yet: fix that in haynes-ops.

- `home-assistant`: cluster-local HA MCP (entities, automations, logs).
- `grafana-mcp`: PromQL and LogQL, dashboards (cluster-local).
- `playwright`: headless Chromium for UI testing. Reach cluster apps at
  `https://<app>.haynesops.com` (an egress grant may be needed) or a dev server at
  `localhost`.
- `mcp-unifi`: UniFi introspection **and writes** (cluster-local, `/mcp`). Reads:
  clients, RSSI, topology, radio config, WLANs. Small, reversible writes such as
  `reconnect_client`, `set_ap_radio_channel` and `update_wlan` may be applied when they
  are the fix (Tom, 2026-09-05): `dry_run: true` first, then `confirm: true`, then
  verify from unpoller or AP metrics. Ask Tom before anything that takes a device or
  network down (`restart_device`, `upgrade_device`, firewall, VLAN or DHCP edits,
  `block_client`). Per-site tools want the legacy site code `default`, not the UUID
  from `list_sites`. `list_wlans` returns passphrases in `x_passphrase`: never echo
  that output into a PR, log or message.
- `blender`: the authoring service at
  `http://blender-authoring.dev.svc.cluster.local:8000/mcp`. One scene author per work
  order; save under `/workspace` and fetch through `/artifacts/`. Runbook: haynes-ops
  `.agents/runbooks/blender-authoring.md`. Tom reviews final game assets.
- `audio`: Stable Audio Small-SFX on CPU at
  `http://audio-authoring.dev.svc.cluster.local:8000/mcp`. Submit a bounded
  `generate_sound`, poll `get_generation`, cancel with `cancel_generation`. One runs at
  a time and four may wait. Fetch the result at `/artifacts/<job-id>/output.wav` and
  check its checksum. Runbook: haynes-ops `.agents/runbooks/audio-authoring.md`. Tom
  reviews final game audio.
- `outline`: the sigoalumni wiki (stdio, `uvx mcp-outline`).
- `vexa`: meeting bot, transcripts and recordings (cluster-local).
- `cigar-journal`: the prod journal at `https://cigars.haynesnetwork.com/mcp`, on
  `CIGAR_JOURNAL_TOKEN`. A 401 means the token is missing from the pod. Over raw HTTP
  it speaks streamable-HTTP MCP: send `initialize` first and carry the returned
  `Mcp-Session-Id`. `400 no valid session` and `404 Session not found` (an idle session
  expires after 30 minutes) both mean auth passed: initialize again.
- `haynesnetwork`: Tom's watch history (`unfinished`, `recommend`, `watch_status`,
  `recent_history`, `watchlist`, `mark_watched`, `set_watchlist`, `dismiss`,
  `undo_last_change`) through the hop
  `http://haynesnetwork-mcp-hop.frontend.svc.cluster.local:8080/mcp`, which adds the
  token itself. `mark_watched` really marks titles watched in Plex: never test it on a
  title Tom has not watched; `undo_last_change` reverses the last change within a day.
  `set_watchlist` really changes his watchlist, and Seerr auto-requests it every 3
  minutes, so adding a title that is not on Plex downloads it and undo cannot cancel
  that. Test only with a title already on Plex. Runbook: haynesnetwork OPS-015.

Voice agents are the opposite of a session pod. Home Assistant sends every tool schema
of every attached server on every voice turn (35 cigar-journal tools cost about 2 s a
turn, 2026-09-22), so each room agent gets only the servers its room needs, each kept
small.

## Declare disruptive work

An autonomous remediation agent (`dev-env-ops`) watches critical alerts and fixes them.
If your work trips an alert (restarting a stateful app, suspending Flux, deleting pods,
draining a node), declare it first, so the alert reads as yours and nobody is pulled in:

```bash
declare-activity start "restarting z2m + mosquitto (broker migration test)" \
  --scope home-automation,zigbee2mqtt,mosquitto --ttl 45m
# ... the work ...
declare-activity end <id>        # always end it when you finish
```

`--scope` is required: the namespaces, apps or nodes you can disturb. The TTL defaults
to 45 minutes and caps at 8 hours (2 hours for `cluster`). A declaration is a hint, not
a mute. *(plan 02)* moves it to the operator's API. Until then a session pod has nowhere
to record one, so do no disruptive work from a v2 session: leave it to Tom or a v1
session.

## CPU budget: no burners, no wide test loops

On 2026-10-05, CPU burners and wide parallel test runs in the v1 pod starved
control-plane node talosm02, and EMQX, traefik, authentik and cloudnative-pg went into
liveness-kill loops. Your pod has a CPU limit, but the rule stands:

- Never run CPU busy-loops, stress tools, or wide parallel or looped test runs.
- Size test workers to `DEV_ENV_CPU_LIMIT` or fewer (for example vitest
  `--maxWorkers=2`, `go test -p 2` with `GOMAXPROCS=2`), and run one suite at a time.
- Reproduce a load-dependent flake in a CPU-limited batch Job on a worker node, or at
  low parallelism under `nice -n 19`.
- Prefer fixing a timing flake by reasoning and fake timers over reproducing it with
  load.
- Every work order that touches flaky or perf tests states this rule.

## Model policy (Tom, updated 2026-09-28)

| Surface | Model | Why |
|---|---|---|
| Automated Claude Code agents (alert-responder, upgrade-shepherd, dev-env-ops) | latest Opus, pinned: `claude-opus-5-5` | They merge upgrades and touch production unattended; being wrong costs more than the quota. Pinned, not aliased. |
| Tom's interactive Claude Code work | Opus 5.5 by default (`claude-opus-5-5`; `DEV_ENV_CLAUDE_MODEL` in `dev-env-templates`). Fable 5.1 only by name (`--model claude-fable-5-1`) | Fable's quota is scarce and shared with Tom's own use. |
| Native Claude Code subagents | two tiers: `opus-worker` (`claude-opus-5-5`, `xhigh`) and `sonnet-worker` (`claude-sonnet-5-5`, `high`) | *Subagent dispatch rules* below. |
| Tom's interactive Codex work | GPT-6 Astra, `gpt-6-astra`, effort `max` | The Codex driving model. |
| Native Codex collaboration subagents | GPT-6.1 Sol, `gpt-6.1-sol`, effort `xhigh` | Since 2026-10-03. |
| Any pay-per-token Claude API-key call | Sonnet 5.5, `claude-sonnet-5-5` | Never Fable or Opus on API pricing. |

**Model ids and effort.** Use full ids, never aliases (`fable`, `opus`): an alias
resolves on the client, against the image's pinned CLI, and can serve an older tier.
Claude: `claude-fable-5-1`, `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-opus-5`,
`claude-haiku-4-5`; effort `low|medium|high|xhigh|max` on the 5-family, none on Haiku
4.5. Codex: `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6-sol`, `gpt-6-luna`,
`gpt-5.6-sol|terra|luna`, `gpt-5.5`; effort `low|medium|high|xhigh`, plus `max` on the
GPT-6 and 5.6 tiers. Quote any `[1m]`-suffixed id.

**Agents are the tripwire for stale ids.** If your own system prompt shows a model
newer than an id pinned here, the pin is stale; never conclude the newer model is
unavailable. It cuts both ways: a pin can be newer than your training data, so never
revert one downward to match your priors. Verify live first:
`claude --model <full-id> -p 'reply with your model id'`. A new id can also need a newer
CLI than the image pins; an older CLI rejects it outright.

**Bump procedure on a new model:** check the CLI floor and bump the agent image's pin
if needed (thaynes43/dev-env), probe the id, then update `DEV_ENV_CLAUDE_MODEL` in
`dev-env-templates`, the `model:` line in this app's `agent-opus-worker.md` or
`agent-sonnet-worker.md`, and v1's own pins by v1's procedure until cutover.

**Quota exhaustion is a real failure mode.** On 2026-08-23 the plan's Fable credits ran
out: sessions drifted to Opus, and a new Fable session refused its first turn
(`out of usage credits`, `Worked for 0s`). That is a credit wall, not a bug: use
`claude-opus-5-5` and tell Tom. It is no reason for a Codex driver to switch providers.

### Remote Control sessions are coordinators (Tom, 2026-09-28)

A session Tom drives over Remote Control is the coordinator between him and the
subagents. The goal is a small context window, so one remote session lasts through
many tasks.

- **The coordinator talks, plans, dispatches, checks and ships.** It holds the plan,
  asks Tom questions (AskUserQuestion), writes self-contained work orders, reviews what
  comes back, and runs the short git, gh and flux steps that merge and deploy the work.
- **Subagents do the reading and the doing.** Exploring code, broad searches, reading
  whole files or long logs, edits across files, tests and builds, PR reviews and deploy
  checks go to a subagent that returns a short conclusion, not file contents.
- **It can also dispatch separate sessions** with `agent-run -p`: each gets its own pod
  and volume and reports through its PR and log.
- **Verify cheaply.** Check a subagent's key claim with one targeted command (a `grep`,
  `gh pr checks`, `kubectl get`) rather than redoing its work.
- **Run independent subagents in parallel and in the background.** To follow up on a
  finished subagent, use SendMessage so it keeps its own context.
- This section is for the top-level session. Subagents and headless `-p` tasks do their
  work themselves.

### Subagent dispatch rules (Tom, updated 2026-09-28; every repo)

Stay within the driving provider: Claude Code delegates to Claude Code, and Codex to
Codex. Do not start a cross-provider session as a quota or complexity fallback. Start a
separate session with `agent-run` only when the task calls for one, or Tom asks.

- **Claude Code drivers delegate to two tiers:**

  | Tier | Give it | Dispatch |
  |---|---|---|
  | **Opus 5.5**: `claude-opus-5-5`, effort `xhigh` | **Hard work:** bugs with an unclear cause, design choices, subtle domain or algorithm code, concurrency, risky changes to data, auth, storage or the cluster, and reviews of those changes. **Anything a user will see:** front-end UI and UX, 3D models and scenes in Blender, game art and audio, user-facing copy, docs and messages. | `subagent_type: "opus-worker"` |
  | **Sonnet 5.5**: `claude-sonnet-5-5`, effort `high` | **Routine work:** exploration, code search, reading a subsystem, well-specified features and bug fixes, mechanical edits and refactors, boilerplate, writing and running tests, CI-log triage, doc scaffolding, deploy verification. | `subagent_type: "sonnet-worker"` |

  agentd installs both agent types into `~/.claude/agents/` at boot, from this app's
  `agent-*.md`. If they are missing, pass `model: "opus"` or `model: "sonnet"` to the
  Agent tool instead.
  - Sonnet 5.5 is the default for routine work. If you are not sure a coding or testing
    task is routine, start it on Sonnet; if the result comes back wrong or shallow,
    send that piece to Opus rather than fixing it in your own context.
  - User-visible work always goes to Opus 5.5, and the driver reviews it before it
    ships.
  - Fable is not a subagent tier.
- **Codex drivers** send each eligible unit of work to a native collaboration subagent:
  `collaboration.spawn_agent` with `fork_turns: "none"`, model `gpt-6.1-sol`,
  `reasoning_effort: "xhigh"`, and a self-contained work order (objective, paths,
  constraints, deliverable, verified context). Do not use `agent-run` for these.
  Eligible: exploration and research, reading subsystems, finding call sites, tests,
  mechanical edits, doc scaffolding, verification and deploy audits. **Exception:** UX
  design and text an end user will see stay on the driving Astra session.
- **Keep for the driving session** only what needs its judgment: ratifying
  architecture and design, cross-repo coherence, and the final review of subagent
  output.
- Give each subagent a crisp, self-contained task; have it return findings, not file
  dumps; fan independent work out in parallel.

## Traps

- **`codex exec` from a tool shell: redirect stdin.** When stdin is not a TTY,
  `codex exec` reads it until EOF, and a Claude Code Bash tool's stdin never closes, so
  it hangs on `Reading additional input from stdin...`. Always `codex exec … < /dev/null`
  there, and under `nohup`, cron or a Job.
- **Codex updates only with the image.** The image pins `CODEX_VERSION`; never enable
  Codex's self-updater.
