# Renovate Automation Roadmap

The goal: stop hand-merging every Renovate PR from
[issue #1](https://github.com/thaynes43/haynes-ops/issues/1) and only spend
attention on the updates that actually break things.

This is a staged rollout. We expand the auto-merge blast radius one trust
boundary at a time, only after the prior tier has been quiet for long enough
to trust it.

## Why not auto-merge everything today

Three concrete failure modes:

1. **Sidecar coupling.** `home-assistant` runs `code-server` as a sidecar in
   the same pod, so a `code-server` patch bumps and restarts Home Assistant.
   Any rule that touches `home-automation/` has to treat the whole pod as one
   unit.
2. **Apps that must move together.** `ha-mcp` is meaningless without a
   matching `home-assistant` version — bumping it alone is a footgun. Z2M
   updates can broadcast devices that HA misses on startup, leaving Zigbee
   entities `unavailable` until HA is restarted.
3. **Stateful operators drift on upgrade.** `rook-ceph` and `cnpg` upgrades
   regularly need hands-on recovery (PVC/pod deletion to re-seed CNPG
   replication, CRD/deprecation fixes for Rook). These must stay manual until
   we have a health gate that can detect drift and either roll back or page.

There is also no PR-gating CI today (`.github/workflows/` only builds
peloton-scraper and publishes mkdocs). So even if we flipped `automerge:
true` right now, nothing would actually validate the change before it lands.
**Fixing the CI gap is the precondition for everything below.**

## What we're borrowing from `onedr0p/home-ops` and `bjw-s-labs/home-ops`

Two well-respected home-ops repos have solved most of this. We pull from
both — onedr0p where the patterns are conservative and battle-tested,
bjw-s where they show what "more aggressive once you trust it" looks like.
The pieces worth porting:

- **`flux-local` PR action** (from onedr0p's
  `.github/workflows/flux-local.yaml`). Runs
  [`flux-local test`](https://github.com/allenporter/flux-local) on every PR
  to validate that all HelmReleases and Kustomizations actually render, and
  runs `flux-local diff helmrelease/kustomization` to post the rendered diff
  as a sticky PR comment. This is the real auto-merge gate — without it,
  `automerge: true` is uninspected. We use onedr0p's `docker://` invocation
  pattern (no runner pre-install needed) rather than bjw-s's shell-command
  pattern (which assumes flux-local is on the runner image).
- **Trusted-package auto-merge**, not path-based. Both repos do this. The
  shape: whitelist specific packages by name/prefix, never auto-merge an
  entire directory. onedr0p uses `home-operations/*` digests + a handful of
  charts; bjw-s uses a broader prefix list (`ghcr.io/home-operations`,
  `ghcr.io/onedr0p`, `ghcr.io/bjw-s`, `ghcr.io/bjw-s-labs`). For us:
  start narrow with `ghcr.io/thaynes43/*` digests + `kube-prometheus-stack`
  minor/patch, expand later.
  **Deliberate deviation (owned 2026-06-09):** our Tier 2 also trusts whole
  *leaf-app domains* by path (`media`, `ai`, `downloads`, `frontend`,
  `office`, `photos`) — we trust the *domain-placement decision* itself:
  those directories only hold stateless single-pod apps. The cost is that
  the trust is **implicit for future deps**: any new app or sidecar added
  under those paths is auto-merge-trusted from day one. **Standing rule:**
  when adding an app to an allowlisted domain, decide at that moment whether
  it needs an Immich-style carve-out in `.renovate/autoMerge.json5`.
- **`groupName` + `minimumGroupSize`** for must-move-together components.
  Used by both repos for kubernetes (5), flux-operator (3), rook-ceph (2),
  talos (2). **Important semantic gotcha** — see "Tier 3" below.
- **`minimumReleaseAge: 3 days`** (onedr0p) to bake third-party tags before
  auto-merging. bjw-s has dropped this — they've earned the trust. We start
  with onedr0p's bake time and revisit later.
- **Renovate runtime options:** onedr0p self-hosts Renovate via a GitHub
  Actions cron (`.github/workflows/renovate.yaml`); bjw-s runs Renovate
  in-cluster as `renovate-operator` (a GitOps-managed HelmRelease at
  `kubernetes/apps/renovate/renovate-operator/`). Both beat the hosted
  Renovate app for control and logs. Not Tier 1; revisit at Tier 2 or
  later. The in-cluster option is interesting because it puts the bot in
  the same lifecycle as the rest of the cluster.
- **Split config into `.renovate/*.json5`** files (`autoMerge.json5`,
  `groups.json5`, `customManagers.json5`, etc.) extended from the root.
  Both repos do this. Makes future tier work land as small reviewable diffs
  against individual files.

## Tiers

| Tier | Scope | Mode | Status |
|------|-------|------|--------|
| 0 | `github-actions` minor/patch | auto-merge | ✅ live |
| 1 | `flux-local` PR gate | required check on all Renovate PRs | ✅ live (`.github/workflows/flux-local.yaml`) |
| 2 | Curated allowlist: own `ghcr.io/thaynes43/*`, `kube-prometheus-stack`, safe stateless leaf-app domains (`media`, `ai`, `downloads`, `frontend`, `office`, `photos`, **`observability`** since 2026-06-15) + curated cluster-infra leaves by subpath (`kube-system/{metrics-server,reloader,reflector,k8tz,spegel}`, `network/cloudflare-ddns`) on minor/patch | auto-merge, flux-local-gated + bake | ✅ live (`.renovate/autoMerge.json5`) — **trust clock starts 2026-06-08** (see exit criteria) |
| 3 | Grouped multi-component apps: `home-assistant` (HA + code-server + ha-mcp), Z2M | symmetric dashboard-approval groups (manual phase) | ✅ live 2026-06-09 (`.renovate/groups.json5`) |
| 4 | `rook-ceph`, `cnpg`, Talos, Flux | dashboard-approval + post-reconcile health-gate agent | 🔶 designed — decisions locked 2026-06-09, build pending |

> **Resuming this roadmap (next session):** Tiers 0–3 are live; Tier 3 runs in
> its **manual dashboard-approval phase** (updates for the HA pod and Z2M show
> up as checkboxes on the Dependency Dashboard, not as auto-opened PRs — tick
> to release them as one group PR). Next milestones: **(a)** Tier 2 promotion
> review on/after **2026-07-06** (four quiet weeks from the 2026-06-08
> deadlock fix), **(b)** build the Tier 4 phase-4a health-gate agent — its
> runtime/credential decisions are locked in the
> [Tier 4 section](#tier-4--stateful-operators-with-a-health-gate).

Tiers 0–2 are live. **Tier 2 carve-out:** `immich-app/*` is excluded from
auto-merge (breaking schema/DB migrations even on minor) despite living in
`photos/`. Ramp the allowlist by adding packages/domains as each proves quiet.
The end state is **100% hands-off**, with a Tier 4 health-gate agent
shepherding the risky merges (the manual merge→reconcile→verify→rollback loop,
automated). That agent needs GitHub Actions (a `GITHUB_TOKEN`-merged PR does
not fire downstream workflows) + a scoped cluster credential.

## Tier 1 — flux-local PR gate (next)

**Why first:** Without this, every other tier is auto-merging unchecked
YAML. With it, even the manually-merged PRs get a rendered diff comment,
which makes review faster.

**What to port from onedr0p:**

- `.github/workflows/flux-local.yaml`, with two adaptations for this repo:
  - **Two flux roots**, not one. Main lives at `kubernetes/main/flux`
    (Kustomization `cluster-apps` → `./kubernetes/main/apps`). Edge lives at
    `kubernetes/edge/flux` with the same shape. Both go in a matrix so each
    PR validates both clusters. Edge can be in the matrix even while it's
    powered off — `flux-local test` only validates that the YAML renders, it
    doesn't talk to the cluster.
  - **Token strategy** — see below. Start with `GITHUB_TOKEN` to avoid
    blocking on App registration; flip to a GitHub App once Tier 1 is
    proven.
- The `bjw-s-labs/action-changed-files` filter step is what makes this cheap
  — only runs when `kubernetes/**` actually changed.

**Token strategy: `GITHUB_TOKEN` vs GitHub App**

onedr0p uses a GitHub App (`BOT_APP_ID` / `BOT_APP_PRIVATE_KEY`) instead of
the default `GITHUB_TOKEN`. The reasons that matter for haynes-ops:

| | `GITHUB_TOKEN` | GitHub App |
|---|---|---|
| Commits trigger downstream workflows | ❌ blocked by GH | ✅ |
| Comment author identity | `github-actions[bot]` | your bot's name |
| Cross-repo install | one repo only | many repos, one credential |
| Rate limit | 1k/hr/repo | 5k/hr/install |
| Setup cost | none | ~10 min app registration |

The killer feature is #1: with `GITHUB_TOKEN`, when Renovate auto-merges a
PR, the resulting merge commit on `main` will *not* fire any push-triggered
workflow (GitHub blocks this to prevent loops). With an App token, the
merge looks like a real user push and downstream workflows run normally.
For one cluster it's annoying-but-livable; for `main + edge + future
expansion` it gets painful.

**Plan:** ship Tier 1 with `GITHUB_TOKEN` so we're not blocked on app
registration, then register a `haynes-ops-bot` GitHub App and flip the
secrets in once flux-local is proven green. Document the app registration
steps in this file when we do it.

**Exit criteria:** the action runs green on at least one real Renovate PR
on each of `main` and `edge` clusters, and the diff comment is useful
enough to make the merge decision from the PR page alone. Then Tier 2.

## Tier 2 — Trusted-package auto-merge

After Tier 1 is green, mirror onedr0p's `autoMerge.json5` with a haynes-ops
twist:

- **Own images on digest:** `automerge: true` for `docker` digests where
  `matchPackageNames: ["/thaynes43/"]`. Rationale: when we bump our own image
  tag (like today's appdaemon 1.0.1), we tested it before pushing — Renovate
  picking up the digest is a no-brainer.
- **`kube-prometheus-stack` on minor/patch.** Direct lift from onedr0p,
  observability has been quiet here for months. **Datasource gotcha:** it's
  sourced via `OCIRepository` (`oci://ghcr.io/.../charts/kube-prometheus-stack`),
  which Renovate tracks as the **`docker`** datasource — *not* `helm`. The rule
  must `matchDatasources: ['docker']` (we use `['docker', 'helm']` to be
  source-agnostic); a `['helm']`-only match silently never fires. This applies
  to any OCI-sourced chart added to the allowlist later.
- **Use `ignoreTests: false`** on every rule so flux-local actually gates
  the merge. (Our existing GH-actions auto-merge rule sets `ignoreTests:
  true` because there's no test today — once Tier 1 lands we should flip
  it.)
- **`minimumReleaseAge: 3 days`** on third-party packages, `1 minute` on our
  own images.

**Exit criteria:** four consecutive weeks with no auto-merge regression
traced to a Tier 2 rule. Each new package added to the trust list resets the
clock for that package only, not the tier.

**Trust clock starts 2026-06-08, not 2026-06-04.** The rules existed from
06-04 but were deadlocked (branch-mode automerge vs a PR-only flux-local
check — see the note atop `autoMerge.json5`) and never fired until the
06-08 fix; first confirmed auto-merge was #1829 on 06-09. Four quiet weeks
of *rules actually firing* puts the promotion review at **≥ 2026-07-06**.

**Allowlist expansion 2026-06-15 — observability domain + curated infra
leaves.** Added `observability/**` as a whole leaf domain (every app is a
plain Deployment — no StatefulSets, no operator CRs; grafana state lives in
external CNPG, loki's only PVC is local log storage) and a *second* rule for
curated cluster-infra leaves matched by **explicit subpath**:
`kube-system/{metrics-server,reloader,reflector,k8tz,spegel}` and
`network/cloudflare-ddns`. The subpath rule is deliberate: `kube-system` and
`network` are **not** safe as whole domains — each holds a cluster-killer
(cilium/coredns, traefik, authentik, multus, device-plugins) that GitOps
can't self-heal if a bad push lands overnight, so only the stateless
single-pod utilities are listed by name. Two things to keep an eye on as
this bakes: `prometheus-operator-crds` (CRD bumps — additive on minor/patch,
which is all the rule allows) and the meta-risk that observability is the
safety net for the *other* Tier 2 merges (mitigated by the independent
Gatus/Pushover/watchdog alert path). Per-package trust clocks for the new
entries start 2026-06-15. **Still manual** (the irreducible core, → Tier 4):
database operators (CNPG/dragonfly/EMQX), cilium, coredns, traefik,
authentik, multus, device-plugins.

## Tier 3 — Grouped multi-component apps

Home automation can't tier up by namespace because of sidecar coupling and
companion-image coupling. The unit of update is the *pod*, not the file or
the namespace.

**Important semantic gotcha:** `minimumGroupSize` does **not** mean "always
bundle these together." It means "only form the group PR if N+ matching
deps have updates available in the same Renovate scan." If only one
matches, it ships as an **individual PR under the normal rules**. Both
onedr0p and bjw-s use it, and that's fine for things like
kubernetes-component bumps where the components naturally release
together. It is the *wrong* tool for sidecar coupling.

**Why the original two-layer design was scrapped (2026-06-09).** The first
draft put `dependencyDashboardApproval: true` on the satellites only
(code-server, ha-mcp) plus a `groupName` across all three, expecting an HA
bump to "sweep in" pending satellite updates. It can't work: package rules
merge **per-dependency**, so each satellite ends up with *both* the
approval flag and the group name — and Renovate holds approval-gated deps
**out of the group branch** until each is individually approved. The group
PR would ship HA alone while the satellites strand behind un-ticked
dashboard checkboxes; worst case, ha-mcp silently drifts incompatibly
behind HA — the exact footgun the group exists to prevent.

**Locked design (live in [`.renovate/groups.json5`](../../.renovate/groups.json5)):
one group, symmetric approval.** Every member of the HA pod group —
`home-assistant`, `coder/code-server`, `homeassistant-ai/ha-mcp` — carries
`dependencyDashboardApproval: true` and an explicit `automerge: false`.

Day-to-day workflow: member updates accumulate as **checkboxes on the
Dependency Dashboard** instead of auto-opening PRs. Tick what you want to
release → Renovate opens **one** group PR with everything ticked →
flux-local renders the diff → manual merge → the HA pod restarts once with
all bumps.

Accepted edge case: approving a satellite-only update produces a group PR
containing just that satellite — one HA pod restart for a code-server bump.
That's a restart-churn cost, not a compatibility risk, and a human chooses
when to take it. (Two non-issues verified 2026-06-09: `ha-mcp` is
third-party `ghcr.io/homeassistant-ai/ha-mcp`, *not* ours, so the Tier 2
`thaynes43/*` wildcard can't catch it; and the Tier 3 rules live in
`groups.json5`, which `extends` *after* `autoMerge.json5` — later rules win
per-dependency — with `automerge: false` set explicitly as a belt-and-braces
guard.)

**Promotion rule — all-or-none.** When Tier 3 graduates from manual
approval to automation (after the Tier 4 health gate exists), remove
`dependencyDashboardApproval` from **all group members in the same commit**
and add a `schedule`. Never go asymmetric (HA automated, satellites gated):
that recreates the strand problem the two-layer design died of.

**Zigbee2MQTT** gets its own approval-gated rule — no group (it has no
companions). The risk is the HA-restart race: a Z2M update can broadcast
devices while HA misses them on startup, leaving Zigbee entities
`unavailable` until HA restarts. During the manual phase, **approval is the
schedule** — you tick the box when you can babysit the reconcile; a
`schedule:` would only add delay after an explicit approval, so it joins at
the automation phase instead (`before 6am on Monday`, the bjw-s pattern).
**Post-merge check (manual now, exactly what the Tier 4 gate automates
later):** verify Zigbee entities are available; restart HA if not.

Candidate for the same babysat pattern later: `zwave-js-ui` (HA's zwave-js
integration has a server-schema compatibility window). Left manual-by-default
for now — it's in no allowlist, so it still opens ordinary PRs.

Even with grouping, Tier 3 stays **dashboard-approval** until the Tier 4
health gate exists — the Z2M/HA race is exactly the kind of thing the gate
needs to catch automatically.

## Tier 4 — Stateful operators with a health gate

`rook-ceph`, `cnpg`, Talos, and Flux itself never auto-merge on tag alone.
The plan is:

1. Renovate opens the PR with `dependencyDashboardApproval: true` (no
   automatic merge ever).
2. flux-local renders the diff in the PR comment, human approves the merge.
3. A scheduled agent (cron trigger or `/loop`) watches Flux Kustomization
   status, `cnpg` cluster health, `rook-ceph` health, and HA Zigbee entity
   availability after every reconcile.
4. On regression, the agent either rolls the HelmRelease back to the prior
   chart version or pages via the existing notification path.

Pre-merge gating cannot solve this — the failure modes only show up *after*
reconcile. The agent is doing the work that no PR check can.

**Decisions locked 2026-06-09:**

- **Phase 4a — watch + page (build first):** a **Claude Code scheduled
  agent** (cron trigger). Fastest to stand up, and the cluster-credential
  question is already solved: the read-only **Omni service-account
  kubeconfig** ([runbook](../../.agents/runbooks/omni-service-account.md))
  gives it headless cluster access with no browser-OIDC dance. It runs the
  post-reconcile health checks above and pages (Pushover, the existing
  notification path) on regression. **Rollback stays human in this phase** —
  the agent's job is to make sure a bad merge never goes unnoticed, not yet
  to act on it.
- **Phase 4b — the hands-off endgame:** once the checks have proven
  themselves, move the loop **in-cluster (CronJob)** and automate the
  rollback (revert commit or chart re-pin, pushed for Flux to reconcile).
  Automated rollback requires the **`haynes-ops-bot` GitHub App** — a
  `GITHUB_TOKEN`-pushed revert fires no downstream workflows (the
  long-standing Tier 1 token-strategy item), so the bot's pushes must look
  like user pushes.
- **Preconditions for 4a:** Tier 3 quiet in manual mode, and the
  **alert-noise cleanup done** — the gate's signal *is* the alert/metric
  stream, and a noisy phone channel is how a regression gets missed. (This
  is already true today: Alertmanager is the de-facto runtime health gate
  for Tier 2's unsupervised overnight auto-merges.)

### The upgrade-shepherd agent — three invocation modes

The Tier 4 agent is not *only* the scheduled health gate. It is **one agent**
with a shared toolset — read-only cluster access via the Omni service-account
kubeconfig ([runbook](../../.agents/runbooks/omni-service-account.md)), `gh`,
repo read/write, `flux-local`, the Pushover page path, and the
[`renovate-upgrade-batches`](../../.agents/runbooks/renovate-upgrade-batches.md)
runbook — invoked three ways:

1. **Scheduled gate (phase 4a, build first).** Cron / `/loop` trigger. After
   every reconcile it runs the post-merge health checks (Flux Kustomization
   status, CNPG / Rook-Ceph / EMQX health, HA Zigbee availability) and pages
   on regression. Rollback stays human in 4a, automated in 4b.

2. **Summoned remediation (on-demand).** When an upgrade *fails* — an alert
   fires, a Kustomization is stuck `not Ready`, or a post-merge regression is
   spotted — the agent is invoked directly (a chat session, or a page-reply /
   `RemoteTrigger` hook) to diagnose, attempt the documented remediation
   (revert the HelmRelease to the prior chart, re-pin a version, delete-and-
   reseed per the component runbook), and report back. This is the manual
   **Rollback** section of `renovate-upgrade-batches.md`, automated and
   on-call. This is the "summoned if an upgrade failed" capability.

3. **Breaking-change shepherd (the manual-tier upgrades).** For the
   irreducible-manual set that will never blind-auto-merge — database
   operators, traefik, authentik, cilium, Talos, Flux, Rook/Ceph — the agent
   runs the `renovate-upgrade-batches.md` **Tier 3** process: read the release
   notes / `UPGRADING` guide, grep our usage for the affected features, make
   the required `values` edits in a commit, merge **one at a time**,
   reconcile, verify, move on. This is the "reading release notes and making
   other changes to support breaking changes" capability — work a pre-merge PR
   check structurally cannot do.

Modes 2 and 3 are the second reason (besides automated rollback) the
`haynes-ops-bot` **GitHub App** is required: the agent must push commits
(reverts, value edits) that fire downstream workflows, which a
`GITHUB_TOKEN`-merged PR does not. Until the App exists, modes 2/3 run as a
**supervised Claude Code session** (the human is the push identity, exactly as
in this 2026-06-15 backlog sweep); only the scheduled gate (mode 1) is purely
read+page, so it can ship on the Omni SA kubeconfig alone.

## Decisions made

- **Tier 1 starts with `GITHUB_TOKEN`**, swap to a GitHub App after
  flux-local is proven. App registration is the second step, not the first.
- **Renovate config will be split** into `.renovate/*.json5` files (mirror
  onedr0p / bjw-s) as a pure refactor — but in a **follow-up PR after Tier
  1 merges**, not in the Tier 1 PR itself. Reason: Renovate's `extends`
  resolves referenced files from the *default branch* of the repo, so
  adding both the new files and the `extends` pointing at them in the same
  PR breaks Renovate until merge. Phase order: Tier 1 PR → merge → Phase
  1.5 split PR → merge → Tier 2.
- **Edge cluster goes in the flux-local matrix from day one**, even while
  it's powered off — validation is YAML-only.
- **Branch protection stays `strict: false`** (2026-06-09, conscious
  trade-off). PRs don't have to be up-to-date with `main` to merge, so two
  PRs can each pass flux-local against an older base and merge in sequence
  with the combined state never rendered. For independent image bumps the
  risk is near zero, and the fix (`strict: true` + `rebaseWhen:
  behind-base-branch`) would serialize every merge behind a rebase→re-check
  cycle — a latency tax shaped suspiciously like the deadlock we just
  escaped. Revisit only if a merge-order incident actually happens.
- **Auto-merges deploy unsupervised overnight — by design** (2026-06-09).
  The `schedule` gates PR *creation*; GitHub platform auto-merge fires
  whenever the required check goes green, and Flux applies within the hour
  — typically while we're asleep. Acceptable for the Tier 2 allowlist
  precisely because it's stateless single-pod apps; Alertmanager is the
  safety net (hence the alert-noise cleanup being a Tier 4 precondition).
  `automergeSchedule` exists if this ever needs to change.
- **Tier 3 uses symmetric dashboard-approval** (2026-06-09): every HA-pod
  group member approval-gated, promotion to automation is all-or-none. The
  asymmetric two-layer design is unimplementable — see Tier 3.

## Open questions

- **Renovate runtime** — *urgency downgraded 2026-06-09*: `platformAutomerge`
  decoupled merging from Mend's hosted run cadence (GitHub merges the moment
  the check passes), which was the main pressure to self-host. The remaining
  motives are logs/control only. Stay on the hosted app; revisit only if its
  limits actually bite. (Options if they do: GitHub Actions self-hosted —
  onedr0p pattern; in-cluster `renovate-operator` — bjw-s pattern.)
- Document the rollback procedure for each Tier 4 component in
  `docs/observability/` so the agent (and a human at 2am) has a runbook.
  Do this as part of building phase 4a — the agent's page should link the
  runbook for the failing component.

## Changelog

- **2026-06-15** — **Tier 2 allowlist expansion + Tier 4 agent scope
  codified.** Added `observability/**` as a whole leaf domain and a curated
  cluster-infra-leaves rule (`kube-system/{metrics-server,reloader,reflector,
  k8tz,spegel}`, `network/cloudflare-ddns`) matched by explicit subpath — see
  the Tier 2 "Allowlist expansion 2026-06-15" note for the why (and why
  `kube-system`/`network` are *not* safe whole-domain). Per-package trust
  clocks for the new entries start today. Codified the Tier 4 **upgrade-
  shepherd agent's three invocation modes** (scheduled health gate / summoned
  on-failure remediation / breaking-change shepherd) — the "summon on failure"
  and "read release notes + make supporting edits" capabilities now have a
  home in the Tier 4 section. Cleared the standing 17-PR manual backlog in
  risk-tiered batches (home-automation first under supervision → safe leaves →
  shared restic → network infra → database operators).
- **2026-06-09** — **Tier 3 design locked + implemented (manual-approval
  phase)** in `.renovate/groups.json5`: scrapped the unimplementable
  two-layer sweep design for the symmetric-approval HA-pod group + a
  babysat Z2M rule (see Tier 3 for the post-mortem and the all-or-none
  promotion rule). **Tier 4 decisions locked**: phase 4a = Claude Code
  scheduled agent with the read-only Omni service-account kubeconfig
  (watch + page, rollback stays human); phase 4b = in-cluster CronJob +
  `haynes-ops-bot` GitHub App for automated rollback. Owned the Tier 2
  path-based-domains deviation (+ standing carve-out rule for new apps);
  restarted the Tier 2 trust clock at 2026-06-08; recorded the
  `strict: false` and overnight-merge trade-offs. Fixed the restic
  duplicate-PR pair (#1830/#1831 — `# renovate:` hints made the regex
  manager double-track images the `kubernetes` manager already covers;
  hints removed). Cleared the 9-PR manual backlog in risk batches
  (leaf/infra → restic → HA pod as one unit → traefik last, verified
  end-to-end).
- **2026-06-08** — **Fixed the auto-merge deadlock**: Tier 0/2 rules used
  branch-mode automerge, but flux-local runs `on: pull_request` only, so no
  branch ever went green and every eligible PR sat unmerged (e.g. #1816).
  Flipped to `automergeType: 'pr'` + `platformAutomerge: true` — GitHub
  merges on check-pass, independent of Mend's run cadence. First confirmed
  live auto-merge: #1829 (2026-06-09).
- **2026-06-04** — Tiers 1 + 2 landed. Tier 1 (`flux-local` PR gate) was
  already live. Split config into `.renovate/*.json5` (the Phase 1.5 refactor,
  superseding the stale PR #1673), fixed the `ignorePaths` `.archive` glob
  (Renovate had been scanning archived apps), flipped the github-actions
  auto-merge to `ignoreTests: false` now that the gate exists, and added the
  Tier 2 curated-allowlist auto-merge (own images + `kube-prometheus-stack` +
  safe leaf-app domains, minor/patch, `minimumReleaseAge` bake, `immich-app/*`
  carved out). Decision: start with the curated allowlist and ramp.
- **2026-04-14** — Roadmap created. No config changes yet — Tier 1
  (flux-local) is the precondition for the auto-merge tiers and lands first.
