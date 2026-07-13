# Saga: dev-env — a 24/7 in-cluster agent development environment

**Status:** executing (2026-07-13). All scoping decisions are recorded in the
[Decision log](#decision-log).

**Operating mode (Tom, 2026-07-13):** autonomous — the agent authors, self-reviews,
merges, and verifies saga PRs end-to-end without waiting for human approval. Tom
watches the PR stream remotely. Pause only for genuinely new decisions outside the
recorded ones, or destructive/irreversible actions.

## Vision

A long-running "workhorse" pod (or pods) in the `main` cluster that hosts a full
development environment for AI coding agents and for Tom:

- **Agent CLIs**: Claude Code, Codex CLI (later: Gemini CLI), authenticated against
  Tom's *subscription plans* (Claude Max / ChatGPT), not per-token API keys.
- **Ops toolchain**: kubectl, helm, flux, kustomize, sops + age, git + gh, jq/yq,
  opentofu, go-task, plus language SDKs (python/node/go).
- **Browser IDE**: code-server (VS Code in the browser) behind Traefik + Authentik.
- **In-cluster kubectl** via `kubernetes.default.svc` + a ServiceAccount — eliminates
  the recurring Omni kubeconfig/OIDC-expiry pain for day-to-day cluster work.
- **GitOps-managed agent configs**: `/home/dev/.claude` and `/home/dev/.codex`
  (settings, MCP servers, model defaults) rendered from ConfigMaps + ExternalSecrets,
  with mutable state (auth tokens, history) on a PVC.
- **Dispatchable**: a human can `kubectl exec`/code-server-terminal in and run
  `claude --dangerously-skip-permissions` or `codex --yolo` in a cloned repo (then
  drive it from a phone via `/remote-control`); the **Shepherd becomes a
  coordinator/dispatcher** that hands work to this pod instead of burning API tokens
  itself.
- **Worktree-per-task by default**: concurrent agents in the same repo never collide —
  every dispatched task gets its own `git worktree`.

The strategic goal: move recurring agent workloads off metered API spend (Shepherd's
$50/mo cap) onto the monthly subscription plans, and give agents a stable, always-warm
environment with cluster-local credentials instead of laptop-bound ones.

## How the Shepherd is organized (the prior art)

The upgrade-shepherd is the closest existing thing and the pattern to steal from —
**but note it was designed for the opposite trust model** (see Hard news #1).

```
kubernetes/main/apps/upgrade-agent/           # domain dir = namespace `upgrade-agent`
├── kustomization.yaml                        #   lists namespace.yaml + each app's ks.yaml
├── namespace.yaml
└── shepherd/
    ├── ks.yaml                               # Flux Kustomization (dependsOn, wait, no postBuild —
    │                                         #   envsubst would mangle shell vars in the scripts)
    └── app/
        ├── kustomization.yaml                # resources + configMapGenerator for resources/*.sh
        ├── helmrelease.yaml                  # bjw-s app-template; 2 cronjob controllers (shepherd,
        │                                     #   triage); initContainer mints short-lived gh token;
        │                                     #   env carries MODE/MODEL/RAMP/PROMPT (auditable in git)
        ├── externalsecret.yaml               # TWO secrets: bot PEM (init container ONLY) vs
        │                                     #   ANTHROPIC_API_KEY (LLM container ONLY)
        ├── rbac.yaml                         # read-only ClusterRole binding + a tiny namespace Role
        │                                     #   for its two runtime-state ConfigMaps
        ├── networkpolicy.yaml                # CiliumNetworkPolicy: default-deny egress; DNS names
        │                                     #   enumerated (github, anthropic, cluster-read ONLY)
        └── resources/
            ├── run-shepherd.sh               # entrypoint: spend guard, deterministic pre-filter,
            │                                 #   mode→tool-allowlist mapping, `claude -p` invocation
            ├── triage.sh                     # no-LLM triage that summons remediate mode
            └── silence.sh                    # bounded Alertmanager silences (hardcoded matchers)

scripts/upgrade-shepherd/Dockerfile           # image: node:24-slim + kubectl/flux/gh/claude-code,
                                              #   Renovate-pinned ARG versions
.github/workflows/upgrade-shepherd-build.yml  # build+push ghcr.io/thaynes43/upgrade-shepherd,
                                              #   keyless cosign sign (Kyverno-verified in-cluster)
.agents/runbooks/upgrade-shepherd.md          # the operating manual the agent itself reads
```

Key shepherd design moves worth reusing directly:

- **Scripts in a ConfigMap, not the image** — the prompt + tool allowlist show up in PR
  diffs, auditable.
- **Secret bifurcation** — the credential that can do the most damage (bot PEM) never
  enters the LLM container; an initContainer mints a short-lived token.
- **Renovate-pinned tool versions** as Dockerfile ARGs with datasource comments.
- **Cosign keyless signing** + the `verify-thaynes43-images` Kyverno policy.
- **Runtime state in non-Flux-managed ConfigMaps** (spend counter) so Flux doesn't
  revert counters.

## Proposed layout for this saga

```
kubernetes/main/apps/dev/                     # NEW domain + namespace `dev`
├── kustomization.yaml
├── namespace.yaml
└── dev-env/
    ├── ks.yaml                               # one per instance if/when we split flavors
    └── app/
        ├── kustomization.yaml
        ├── helmrelease.yaml                  # app-template; Deployment (Recreate) + PVC home
        ├── externalsecret.yaml               # CLAUDE_CODE_OAUTH_TOKEN, git/gh creds, MCP env
        ├── rbac.yaml                         # SA + the chosen ClusterRole tier (see Decision log)
        ├── networkpolicy.yaml                # broad-but-enumerated egress (see backlog 02)
        ├── ingressroute.yaml                 # code-server via Traefik + Authentik middleware
        └── resources/
            ├── dev-init.sh                   # render/link GitOps configs into the PVC home
            ├── agent-run.sh                  # worktree-per-task dispatch wrapper (backlog 06)
            ├── auth-check.sh                 # CLI auth freshness probe (backlog 04)
            └── config/
                ├── claude/settings.json, mcp.json, CLAUDE.md
                └── codex/config.toml

scripts/dev-env/Dockerfile                    # ghcr.io/thaynes43/dev-env
.github/workflows/dev-env-build.yml           # build + cosign sign (add to Kyverno policy)
.agents/sagas/dev-env/                        # this saga
```

## Hard news (read before falling in love)

1. **This inverts the containment story we spent months building.** The shepherd's
   whole design is *containment of a prompt-injectable LLM*: read-only SA, default-deny
   egress to exactly GitHub+Anthropic, tool allowlists, no long-lived credential in the
   LLM container, $50/mo cap. The dev-env pod is the opposite on every axis: yolo-mode
   agents, broad egress (package registries, arbitrary docs sites — agents doing real
   dev work need them), long-lived subscription tokens and git credentials sitting on a
   PVC, a powerful ServiceAccount, running 24/7. A prompt-injected agent in this pod
   can read the token that spends Tom's Max plan, push code as Tom's git identity, and
   touch the cluster with whatever RBAC we grant. Mitigations (own namespace, enumerated
   egress, Authentik-only ingress, scoped SA, Kyverno) reduce but do not remove this.
   The decision is what blast radius we accept, not whether there is one.
2. **Subscription dispatch is rate-limit contention, not free compute.** Claude Max
   shares its 5-hour and weekly windows across *all* uses of the account. A Shepherd
   that dispatches background work into the plan can exhaust the window right before
   Tom sits down to use Claude interactively. We need a dispatch policy (what classes
   of work go to the plan vs. stay on the API key) and ideally a "remaining window"
   check before dispatch. The API-key path should remain as fallback.
3. **Claude Code headless auth is a one-time browser ceremony + a long-lived token.**
   `claude setup-token` (run interactively once, e.g. on the Mac) mints a long-lived
   OAuth token consumed headlessly via `CLAUDE_CODE_OAUTH_TOKEN`. It expires (~1 year)
   and can be revoked; there is no in-pod refresh. Codex device-code auth can be done
   entirely in-pod and its `auth.json` self-refreshes on the PVC. So the "when do I
   need to bash in and re-auth" problem is real but small: an auth-check probe +
   Pushover page covers it (backlog 04).
4. **`kubernetes.default.svc` only solves the main cluster from inside the main
   cluster.** Edge-cluster kubectl, `talosctl`/`omnictl`, and Omni-template work still
   need Omni credentials (the existing [omni-service-account runbook](../../runbooks/omni-service-account.md)
   is the headless answer there). And when the main cluster itself is broken, the
   in-cluster dev-env is broken with it — the Mac + Omni path stays the break-glass.
5. **Whole-directory GitOps of `~/.claude` / `~/.codex` doesn't work.** Those dirs mix
   declarative config (settings.json, .mcp.json, config.toml) with mutable state
   (credentials, history, session db). The pattern is: ConfigMaps for declarative
   files + ExternalSecret-fed env vars referenced from MCP config, PVC for state, an
   init script that links/copies the rendered configs into `$HOME` on boot.
6. **New image = new Kyverno + Renovate obligations.** `ghcr.io/thaynes43/dev-env`
   must be cosign-signed by its workflow and added to `verify-thaynes43-images`; the
   powerful SA binding may need a `restrict-rbac-escalation` exception. The kitchen-sink
   image will be multi-GB — pin versions, expect slow pulls on roll.
7. **Worktree-by-default has to be enforced by the dispatcher, not hoped for.** A raw
   `claude` in a shared clone will collide with other agents. The `agent-run` wrapper
   owns `git worktree add`/cleanup; interactive humans get a convention + helper, but
   nothing stops a human from working in the main clone.

## Decision log

| # | Decision | Status | Outcome |
|---|----------|--------|---------|
| 1 | ServiceAccount power tier (cluster-admin vs read-all + targeted writes vs read-only) | **DECIDED** 2026-07-13 | **Operator tier**: read-all + targeted writes (delete pods, rollout-restart patch, Flux reconcile/suspend annotations, create Jobs, suspend CronJobs); no secrets read, no exec, no RBAC/node mutation. See backlog 05. |
| 2 | Which subscription plans are in play, and the Shepherd dispatch policy (plan vs API) | **DECIDED** 2026-07-13 | **Plan-first, API fallback**: scheduled/background dispatch runs on the Max plan; on rate-window exhaustion or auth failure fall back to ANTHROPIC_API_KEY under the existing $50/mo spend guard. Time-critical remediate goes straight to the API key. |
| 3 | Shepherd → dev-env dispatch mechanism (exec vs in-pod HTTP API vs Jobs) | **DECIDED** 2026-07-13 | **In-pod HTTP dispatch API**: POST /tasks wraps agent-run (guardrails enforced pod-side); Shepherd CNP gains one in-cluster egress rule, no new RBAC. Same API later powers the LAN control plane (backlog 09). |
| 4 | PoC shape: one kitchen-sink pod vs per-agent pods from day 1 | **DECIDED** 2026-07-13 | **One pod first** (Claude Code + Codex; Gemini deferred to backlog 08). Manifests written substitution-ready so the flavor split is mechanical later. |
| 5 | Git identity agents push as (personal key vs haynes-ops-bot vs new machine user) | **DECIDED** 2026-07-13 | **haynes-ops-bot everywhere**: install the App on all agent-touched repos; an in-pod refresher re-mints the 1h ghs_ token continuously; PEM never enters the agent container (shepherd pattern). No personal creds in the pod. |
| 6 | Access path: Traefik+Authentik ingress vs LAN-only; where /remote-control fits | **DECIDED** 2026-07-13 | **traefik-internal + Authentik only** (no Cloudflare Tunnel exposure). Browser interaction with running agents is REQUIRED: `/remote-control` for Claude (outbound-only via claude.ai — CNP must allow it, verify from the pod early in the PoC). CAVEAT: Codex has no true /remote-control equivalent — its browser story is the code-server terminal attached to the task's tmux session; revisit if OpenAI ships one. |
| 7 | Home PVC size/class + VolSync backup or not | **DECIDED** 2026-07-13 | **50Gi Ceph block RWO, prune-protected, NO VolSync** — repos/caches regenerate; keeping credentials out of S3 is a feature. PVC loss costs ~2 min of re-auth ceremonies. |
| 8 | Language SDKs baked into the image | **DECIDED** 2026-07-13 | Must cover **haynesnetwork (Node/TypeScript — tsx), haynes-ops (bash/python-makejinja + k8s toolchain), hass-sandbox (Python/pytest)** out of the gate → Node 24 from base + tsx/pnpm, Python 3 + uv/venv/pytest. Go/Rust/.NET deferred until a concrete need. |

## Plan backlog

Rough order; ↓ means depends on the item above it, ∥ means parallelizable with its
neighbors once prerequisites are met.

| Plan | Depends on | Parallel? |
|------|-----------|-----------|
| [01 — container image + signed build](backlog/01-image.md) | — | with 02 authoring |
| [02 — PoC deployment](backlog/02-poc-deploy.md) | 01 (image exists) | |
| [03 — GitOps agent configs](backlog/03-agent-configs.md) | 02 | ∥ 04, 05 |
| [04 — CLI auth + expiry watch](backlog/04-auth.md) | 02 | ∥ 03, 05 |
| [05 — kubectl RBAC tiers](backlog/05-rbac.md) | 02 | ∥ 03, 04 |
| [06 — agent-run wrapper + worktrees](backlog/06-agent-run.md) | 03, 04 | |
| [07 — Shepherd dispatch integration](backlog/07-shepherd-dispatch.md) | 06 | |
| [08 — multi-instance flavors](backlog/08-multi-instance.md) | 06 | ∥ 07 |
| [09 — LAN control plane / UI](backlog/09-control-plane.md) | 07 | last |
| [10 — Taskfile overhaul](backlog/10-taskfile-overhaul.md) | — | ∥ anytime; ideally before 02 so pod agents inherit trustworthy tasks |
