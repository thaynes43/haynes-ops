# Phase D — auto-merge + auto-summon: cold-start handoff (2026-07-03)

**Mission:** flip the Tier-4 upgrade automation from "safe stuff auto-merges, everything
risky floods the operator's PR list" to **hands-off for the manual-tier upgrades too** —
the shepherd (an in-cluster LLM) vets, edits, merges, and verifies them, and auto-remediates
regressions, consulting a human only when a rollback needs real investigation.

You are a **fresh agent** picking this up cold. Everything you need is committed. The
build is ~90% done and **inert by design** — your job is the careful *enablement* + proof,
not a from-scratch build. **Do not trust this document's claims blindly — verify state on
the live cluster as you go** (commands are given). The final auto-merge enablement and any
Kyverno Enforce flip are **operator-gated** (see §4).

---

## Problem statement

Renovate auto-merges the safe, stateless, minor/patch **leaf-app** updates today
(Tiers 0–3 — proven: ~5 auto-merge per active day). What it deliberately does **not**
touch is the **manual tier**: database operators (cnpg, dragonfly, emqx), storage
(rook-ceph, ceph-csi), CNI/DNS (cilium, coredns, multus), ingress/SSO (traefik, authentik),
Flux itself, device-plugins — plus every **major** version bump and the **home-assistant
pod group**. Those generate a steady stream of PRs (typically 8–12 open at any time) that
the operator must read release notes for, hand-edit, merge one-at-a-time, and babysit the
reconcile. **That residual toil is what overruns the operator, and it is exactly what
Phase D exists to remove.**

Be clear-eyed: the manual tier is really **three** sub-tiers, and Phase D handles them
differently —

| Sub-tier | Example | Phase-D treatment |
|---|---|---|
| **Pure version/chart bump, no supporting edit** | coredns/traefik/flux patch, ceph-csi patch | **auto-merge** (the bulk of the relief) |
| **Needs a supporting `values` edit** | app-template v5 → `automountServiceAccountToken:true`; rook v1.20 → `ceph-csi-drivers` dependsOn + pin `cephVersion`; kube-prometheus-stack → bump `prometheus-operator-crds` first | shepherd **authors** the edited PR; **human clicks merge** (see the diff-scope note in §2) |
| **One-way / stateful major** | cnpg PG major, Ceph daemon major, cilium eBPF, authentik migration, emqx | shepherd **shepherds one-at-a-time** with post-reconcile verify + rollback; human stays in the loop |

**"Done" =** the pure-bump sub-tier auto-merges end-to-end unattended and safely; the
supporting-edit sub-tier arrives as a ready-to-merge, pre-vetted, pre-edited PR (one click,
not an investigation); regressions auto-summon the shepherd to remediate; and a human is
paged only for a rollback that won't converge from git.

---

## 0. Orientation — read these first, in order

1. **Agent memory** `~/.claude/projects/-home-thaynes-workspace-haynes-ops/memory/renovate-automation-roadmap.md` — the full chronological build log (bot → gate → shepherd → Kyverno enforce → Phase D groundwork → cosign → alerts). Primary source of truth for history.
2. `docs/renovate/README.md` — the tiered roadmap + holds registry + changelog.
3. `.agents/runbooks/upgrade-shepherd.md` — **the operating manual.** Read the **"Phase 4b.3 — auto-merge & auto-summon (operating)"** section especially: the four `UPGRADE_AGENT_MODE`s, the triage CronJob, the spend guard, the kill switch, the summon commands.
4. `.agents/runbooks/tier4-component-playbooks.md` — per-component supporting-edits + **rollback-risk table** (the "clean" rows are your first auto-merge subset).
5. `docs/renovate/tier4-audit-2026-07-02.md` — the adversarial security audit of the guardrails (diff-scope, Kyverno, the bot). Understand the threat model before enabling auto-merge.
6. `.agents/runbooks/kyverno-enforce-verify.md` — you will run this (or `/kyverno-verify`) as Step 1.
7. `CLAUDE.md` — repo rules (GitOps only; the change-authorization guardrail).

**Access caveats:** `kubectl`/`flux`/`gh` work from the workstation (admin). MCP servers
`grafana`/`home-assistant`/`mcp-unifi` are wired for live introspection. **`op` (1Password
CLI) is NOT in the agent Bash env** — you cannot mint the bot token yourself; the shepherd's
in-cluster init-container mints it. `helm`, `jq`, `git`, `python3`, `perl` available.
**Flux is poll-only ~30 min** (no webhook) — merges/reverts apply within ≤30 min, not
seconds. Timezone trap: AppDaemon/Z2M logs = ET; Prometheus/Loki/kubectl = UTC.

---

## 1. Current state — what is live, what is inert (verify each)

**Live and load-bearing:**
- **Push protection** (Phase B): `Diff Scope - Success` + `Flux Local - Success` required on `main`; Main ruleset `14013135` require-PR (0 approvals, admin RepoRole-5 bypass); Edge ruleset `18431432`. Proven: the bot **cannot** direct-push to main.
- **diff-scope** (`scripts/diff-scope.sh` + `.github/workflows/diff-scope.yml`): the PRIMARY gate. **Author-scoped** — it *enforces* only for `haynes-ops-bot[bot]`, and *passes* Renovate/human PRs. It blocks any bot diff beyond a pure image-tag/digest + chart-version bump, or touching a sensitive path. **This is the crux of the auto-merge design — see §2.**
- **Kyverno enforce** (Phase C): `restrict-image-registries`, `restrict-rbac-escalation`, `pod-security-baseline` all **Enforce** (fail-open). `verify-thaynes43-images` in **Audit**. Exceptions in `kubernetes/main/apps/kyverno/policies/app/exceptions/`.
- **Guardrail alerts** (`kubernetes/main/apps/kyverno/kyverno/app/prometheusrule.yaml`): `kyverno-guardrail.rules` — OOM / controller-down / stuck-PVC / repeated-enforce-denial, all severity=critical → Pushover. The always-on safety net; **this is what makes Phase D safe to run unattended** (two silent failures the week of 2026-07-02 — OpenEBS provisioning denied, reports-controller OOM — are why these exist).
- **Health gate** (`kubernetes/main/apps/upgrade-agent/health-gate/`): deterministic, read-only, pages Pushover on a persisted regression. Independent of the shepherd.

**Built and INERT (this is what you enable):**
- **Shepherd** (`kubernetes/main/apps/upgrade-agent/shepherd/`): CronJob **suspended**, `UPGRADE_AGENT_MODE` defaults `dryrun`. Modes: `dryrun` (read-only plan) / `shepherd` (edit+PR, human-merge) / **`auto`** (edit+PR + `gh pr merge --auto`) / **`remediate`** (Mode-2 diagnose→rollback/forward-fix). `gh pr merge` is allowlisted ONLY in auto/remediate.
- **$50/mo spend guard** in `run-shepherd.sh`: ConfigMap `upgrade-shepherd-spend` (runtime state, not git-managed) blocks an unattended (auto/remediate) run once month-to-date + per-run cap ($5) would exceed `UPGRADE_AGENT_MONTHLY_CAP_USD`. Fails open (the Anthropic account balance is the hard backstop). Check: `kubectl -n upgrade-agent get cm upgrade-shepherd-spend -o jsonpath='{.data}'`.
- **`upgrade-shepherd-triage` CronJob** (`suspend: true`): deterministic, LLM-free. On a schedule it checks "recent merge to main AND a regression now" and only then `exec`s `run-shepherd.sh` MODE=remediate. Healthy path = $0. Decoupled from the health gate.

**Verify inert state:**
```bash
kubectl -n upgrade-agent get cronjob     # both SUSPEND=True
kubectl -n upgrade-agent get externalsecret   # both bot + llm SecretSynced=True
```

**Hard prerequisites for Phase D (BOTH now MET):** diff-scope required on main+edge, and
the security-critical Kyverno policies in Enforce. Do not proceed if either regresses.

---

## 2. The design decisions you must make (with recommendations)

### 2a. THE KEY CONSTRAINT: diff-scope blocks supporting-edit PRs from auto-merging

diff-scope enforces (for the bot) that a PR is *only* a pure version/digest/chart-version
bump. A shepherd PR that adds a supporting `values` edit (e.g. `automountServiceAccountToken`)
is **not** a pure bump → **diff-scope fails → it cannot auto-merge → it falls to human
review.** This is by design and is *good* (it's the safety property that stops a
prompt-injected shepherd from merging a malicious values edit). The consequence:

- **Pure-bump manual-tier PRs → auto-merge.** (The relief.)
- **Supporting-edit PRs → shepherd authors them, human clicks merge.** (Still a huge help:
  the human gets a pre-vetted, pre-edited, release-notes-read PR — one click, not an
  investigation.)

**Do NOT** try to widen diff-scope's allowed shape to auto-merge supporting-edit PRs in the
first iteration — that weakens the primary security gate. Revisit only after the pure-bump
path has soaked.

### 2b. Auto-merge mechanism — recommendation

For a **pure-bump** manual-tier PR, Renovate has *already opened* a PR. Two options:
- **(A, recommended) Shepherd vets, then enables auto-merge on the existing Renovate PR:**
  `gh pr merge <renovatePR#> --auto --squash --delete-branch`. The Renovate PR already has
  diff-scope=pass (trusted author) + flux-local green; `--auto` merges it server-side when
  green. The shepherd's *vetting* (read release notes, check `.renovate/holds.json5`, confirm
  no supporting edit needed) is the added safety. Simplest, no duplicate PR.
- **(B) Shepherd opens its own identical bump PR** (diff-scope enforced on the bot) and
  auto-merges that. Cleaner gate story, but a duplicate PR + closes the Renovate one.

Recommend **(A)** for pure bumps, **(B)**/`shepherd`-mode for anything needing an edit.
Whichever you pick, the safety boundary is **server-side**: the bot is non-admin + non-bypass,
so `--auto` only *queues* — GitHub merges only when Flux Local + Diff Scope are both green,
and `--admin` (skip-checks) fails for it.

### 2c. First auto-merge scope — start narrow, ramp

Start with the **"clean" rollback-risk** components from the playbook table (fully stateless,
HR auto-rolls a failed upgrade, revert is safe): **coredns, traefik, multus, device-plugins,
flux** — **pure version/chart bumps only**. Explicitly EXCLUDE at first: cnpg, rook-ceph,
cilium, authentik, emqx, dragonfly (stateful/one-way), and all **majors** and the HA pod
group. Expand the set one component at a time as each proves quiet (mirror the Tier-2 ramp
discipline).

---

## 3. Implementation plan (each step validated with proof — no "trust me")

### Step 1 — Confirm the base is quiet (gate; do first)
Run `/kyverno-verify` (or `.agents/runbooks/kyverno-enforce-verify.md`). Require: 0 enforce
blocks, no stuck PVCs, Kyverno controllers 0-restart/no-OOM, 4 guardrail alerts loaded &
`firing=0`. Also `flux get kustomizations -A | grep -v True` (empty) and
`scripts/checkHealth.sh`. **If anything is unhealthy, stop and fix before enabling autonomy.**

### Step 2 — Re-prove the shepherd works E2E (dry-run, ~$0.50, read-only)
```bash
kubectl -n upgrade-agent create job shep-$(date +%s) --from=cronjob/upgrade-shepherd
kubectl -n upgrade-agent logs -f job/<name> -c app     # expect: surveys open PRs, triages, $ cost, NO changes
```
Confirms init-container token mint + clone + LLM + tool allowlist still work.

### Step 3 — Money-shot: ONE real auto-merge, SUPERVISED (operator-gated — get the go-ahead)
Pick one open **pure-bump "clean"-tier** PR (e.g. a coredns/traefik/flux/multus/device-plugins
patch — check `gh pr list`). Summon the shepherd in **auto** mode targeted at it (create-job
can't set env → inject via jq):
```bash
kubectl -n upgrade-agent create job shepauto-$(date +%s) --from=cronjob/upgrade-shepherd --dry-run=client -o json \
 | jq '.spec.template.spec.containers |= map(if .name=="app" then .env |=
     (map(if .name=="UPGRADE_AGENT_MODE" then .value="auto" else . end)
      + [{name:"UPGRADE_AGENT_PROMPT",value:"Vet Renovate PR #<N> (<pkg> <from>-><to>): confirm it is a pure version bump needing NO supporting values edit (read the release notes + tier4-component-playbooks.md + .renovate/holds.json5). If safe, enable auto-merge with: gh pr merge <N> --auto --squash --delete-branch. If it needs a supporting edit or is held, do NOT merge — report why."}]) else . end)' \
 | kubectl apply -f -
```
**Watch the whole chain and prove each link:** shepherd vets → `gh pr merge --auto` queued →
GitHub merges when green → Flux applies (≤30 min) → run the health gate + `/kyverno-verify` →
component healthy. **This is the proof to show the operator.** Then do a **negative** proof:
summon it at a supporting-edit or major PR and confirm it does NOT merge (diff-scope blocks /
it declines).

### Step 4 — Enable scheduled auto-merge for the narrow subset (operator-gated final flip)
Via **git** (edit the shepherd HR → Flux): set the `upgrade-shepherd` controller
`UPGRADE_AGENT_MODE: auto`, `cronjob.suspend: false`, and a slow schedule (e.g. once/day in a
window). Constrain scope in the default prompt to the clean-tier subset. Commit → reconcile →
watch the first few unattended cycles. Kill switch: `kubectl -n upgrade-agent patch cronjob
upgrade-shepherd -p '{"spec":{"suspend":true}}'`.

### Step 5 — Enable the auto-summon (triage)
Unsuspend `upgrade-shepherd-triage` with a `*/30` schedule (git → Flux). Validate: induce a
**benign** regression (e.g. scale a non-critical Deployment to a bad image in a throwaway ns,
or use a scratch PVC that will be denied) *right after* a merge, confirm triage detects
"recent merge + regression" and summons `remediate`, then clean up. Confirm a *healthy* cycle
stays $0 (no LLM call).

### Step 6 — Ramp
Add one component at a time to the auto-merge subset as each proves quiet. Track in
`docs/renovate/README.md`. Re-run `/kyverno-verify` after each widening.

**Phase-D-adjacent, optional:** `verify-thaynes43-images` is in Audit and proven working
(all agent images signed). Flipping it to Enforce closes the backdoored-image gap for the
self-built images. Low risk, operator-gated (it's a Kyverno Enforce flip). Independent of
auto-merge; do it whenever.

---

## 4. Safety model, hard rules, consult gates

- **OPERATOR-GATED (get an explicit go-ahead, show proof not claims):** the first real
  auto-merge (Step 3), the scheduled-auto-merge flip (Step 4), any widening of the
  auto-merge scope, any Kyverno Enforce flip. Everything else you may drive autonomously.
- **The safety boundary is server-side + the guardrails, not the LLM's good behaviour.**
  The bot is non-admin/non-bypass (can't merge past a red check, can't `--admin`); diff-scope
  blocks non-pure-bump bot PRs; Kyverno denies dangerous manifests at apply; read-only cluster
  SA (git→Flux is the only write path); egress CNP (GitHub/Anthropic/cluster-read only); PEM
  isolated in the init-container; $50/mo spend guard; the guardrail alerts + health gate page
  independently.
- **Never** grant the bot Administration/Workflows scope, add it to a ruleset bypass list, set
  `required_approving_review_count ≥ 1` (deadlocks every bot/Renovate PR), or set a Kyverno
  policy `background: false` to enable `subjects:` exceptions (verified forbidden + blinds the
  audit — 2026-07-03).
- **When git-alone won't converge** (immutable field, wedged HR, stuck finalizer, one-way
  major, Ceph/PG major) it is **break-glass** — the shepherd pages with a diagnosis and STOPS;
  do not improvise a cluster write. Rollback of a bad merge = `git revert` → Flux (≤30 min).
- **Kill switch:** `kubectl -n upgrade-agent patch cronjob <upgrade-shepherd|upgrade-shepherd-triage> -p '{"spec":{"suspend":true}}'`.

---

## 5. Validation summary (what to show the operator)
1. **A real safe upgrade auto-merged unattended** (the PR, the merge, the reconcile, the
   health check) — Step 3.
2. **A supporting-edit / major / held PR did NOT auto-merge** (diff-scope blocked or the
   shepherd declined) — the negative proof.
3. **A regression auto-summoned remediation** (triage → remediate opened a forward-fix/rollback
   PR, or paged for break-glass) — Step 5.
4. **Spend guard + kill switch demonstrated.**

## 6. Known traps (bit us during the build — don't repeat)
- Flux `postBuild.substitute` mangles shell in ConfigMaps — the shepherd/gate ks.yaml have no postBuild; keep it that way.
- Never put brace/quote/apostrophe content inside a bash `${VAR:-default}` — split the default onto its own line.
- `kubectl create job --from=cronjob` can't set env — inject via `jq` on `--dry-run=client -o json`.
- Kyverno `subjects` in a PolicyException is forbidden under `background:true`; `exclude.subjects` in a ClusterPolicy likewise. Scope exceptions by namespace/name only.
- The reports-controller OOMs on the chart-default 128Mi — it's pinned to 512Mi now; if you add policies/watches, watch its memory (`kyverno-guardrail.rules` will page).
- Renovate's 3-day `minimumReleaseAge` bake holds safe PRs open for a few days — that's not a bug, it's the safety delay.

---
*Prior context: this file + the memory + the runbooks. Start at §0, then §3 Step 1. The
groundwork is real and inert; your job is the careful enablement with proof. Do NOT flip the
scheduled auto-merge or any Enforce policy without the operator's explicit go-ahead.*
