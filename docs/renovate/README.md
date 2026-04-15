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
| 0 | `github-actions` minor/patch | auto-merge | ✅ live (existing rule) |
| 1 | `flux-local` PR gate | required check on all Renovate PRs | ⬜ **next** |
| 2 | Trusted packages: own `ghcr.io/thaynes43/*` digests, `kube-prometheus-stack` chart | auto-merge after Tier 1 | ⬜ planned |
| 3 | Grouped multi-component apps: `home-assistant` (HA + code-server + ha-mcp), Z2M | weekly batch, dashboard-approval | ⬜ planned |
| 4 | `rook-ceph`, `cnpg`, Talos, Flux | dashboard-approval + post-reconcile health-gate agent | ⬜ planned |

Tier 0 already works. Everything else is the roadmap.

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
  observability has been quiet here for months.
- **Use `ignoreTests: false`** on every rule so flux-local actually gates
  the merge. (Our existing GH-actions auto-merge rule sets `ignoreTests:
  true` because there's no test today — once Tier 1 lands we should flip
  it.)
- **`minimumReleaseAge: 3 days`** on third-party packages, `1 minute` on our
  own images.

**Exit criteria:** four consecutive weeks with no auto-merge regression
traced to a Tier 2 rule. Each new package added to the trust list resets the
clock for that package only, not the tier.

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

For HA specifically we need two layers:

```json5
// Layer 1: ban standalone bumps for the satellites
// (use dependencyDashboardApproval for an escape hatch instead of enabled:false
//  if you ever want to manually pull in a code-server-only bump)
{
  description: "code-server and ha-mcp must never ship without home-assistant",
  matchPackageNames: ["/coder/code-server/", "/ha-mcp/"],
  matchFileNames: ["kubernetes/main/apps/home-automation/**"],
  dependencyDashboardApproval: true,
}
// Layer 2: when HA bumps, sweep the satellites in
{
  description: "Home Assistant group",
  groupName: "home-assistant",
  matchPackageNames: [
    "/home-assistant/home-assistant/",
    "/coder/code-server/",
    "/ha-mcp/",
  ],
  matchFileNames: ["kubernetes/main/apps/home-automation/**"],
}
```

`code-server` and `ha-mcp` then never open their own PRs without manual
approval, but the moment HA itself gets a bump the group rule fires and
sweeps in any pending satellite updates as a single PR.

A second group for `zigbee2mqtt` on its own (no companions, but the
HA-restart-on-Z2M-change race means it should land on a known schedule
where we can babysit it). Renovate `schedule: ["before 6am on Monday"]` is
the bjw-s pattern for high-cadence-but-needs-attention.

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

**Open question:** does the agent run as a GitHub Action, an in-cluster
CronJob, or a Claude Code scheduled trigger? The trigger is fastest to
prototype; the in-cluster job is the right long-term home.

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

## Open questions

- **Renovate runtime** — three options, decide at Tier 2:
  1. **Hosted Renovate app** (current). Zero ops, least control.
  2. **GitHub Actions self-hosted** (onedr0p pattern). Hourly cron, your
     own logs, runs on GH-hosted runners. Easy migration.
  3. **In-cluster `renovate-operator`** (bjw-s pattern). GitOps-managed
     HelmRelease, lives with the rest of the cluster. Heavier setup but
     the bot is in the same lifecycle as everything else it manages.
- Tier 4 health-gate agent: GitHub Action, in-cluster CronJob, or Claude
  Code scheduled trigger? Decide when we get there.
- Document the rollback procedure for each Tier 4 component in
  `docs/observability/` so the agent (and a human at 2am) has a runbook.

## Changelog

- **2026-04-14** — Roadmap created. No config changes yet — Tier 1
  (flux-local) is the precondition for the auto-merge tiers and lands first.
