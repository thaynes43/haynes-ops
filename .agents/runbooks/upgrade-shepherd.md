# Tier-4 upgrade-shepherd

The master operating manual for the Tier-4 upgrade-shepherd: **one agent, three
invocation modes**, the thing that finally takes the irreducible-manual upgrades
off your hands. The design — why one agent, why these three modes, why a GitHub
App + a read-only cluster SA — is locked in
[`docs/renovate/README.md` → Tier 4](../../docs/renovate/README.md#tier-4--stateful-operators-with-a-health-gate);
this runbook is *how to run it*.

| Mode | Trigger | Job | Writes? |
|---|---|---|---|
| **1 — scheduled health gate** | cron / `/loop`, post-reconcile | run health checks, page on regression | read + page only |
| **2 — summoned remediation** | alert fires / stuck `Kustomization` / spotted regression | diagnose → attempt documented rollback → page if it won't converge | git revert/re-pin (Flux applies) |
| **3 — breaking-change shepherd** | manual-tier Renovate PR sits in the queue | read notes → make supporting edits → merge one-at-a-time → reconcile → gate | git edits/PR/merge (Flux applies) |

The shepherd never blind-merges a stateful upgrade. Its whole value is the work a
pre-merge PR check structurally cannot do: reading release notes, grepping our
usage, making the supporting `values` edits, and watching the *post-reconcile*
failure modes that only show up after Flux applies.

---

## Credentials & guardrails

Two credentials, deliberately split — neither single leak yields write-to-cluster
([rationale](../../docs/renovate/tier4-bot-setup.md#why-a-github-app-and-not-a-pat)).

### Push / PR / merge identity = `haynes-ops-bot`

Mint a 1-hour installation token from the App private key and hand it to `gh`/`git`:

```bash
export GH_TOKEN="$(scripts/github-app-token.sh)"           # reads GITHUB_BOT_APP_* from env (1Password item 'github-bot', vault HaynesKube)
git remote set-url origin "https://x-access-token:${GH_TOKEN}@github.com/thaynes43/haynes-ops.git"
gh pr list                                                  # now acts as haynes-ops-bot[bot]
```

Re-mint before the 1-hour expiry on long runs. Creds live at
`op://HaynesKube/github-bot/<FIELD>`; in-cluster they sync via the
`onepassword-connect` ClusterSecretStore. Setup + threat model:
[`tier4-bot-setup.md`](../../docs/renovate/tier4-bot-setup.md).

**Why the App and not `GITHUB_TOKEN`:** identity, not triggering. The
`GITHUB_TOKEN`-doesn't-fire-downstream-workflows restriction only applies *inside*
an Actions run — but the point is the bot needs a **non-human, scoped, revocable**
identity whose pushes/PRs trigger the `Flux Local - Success` check. The App is
that identity (Contents:write + Pull requests:write + Checks:read, single-repo,
no webhook). It is **not** in any branch-protection bypass list — even a leaked
token can't reach `main` without a green flux-local.

### Verify on the read-only Omni Reader kubeconfig

All cluster reads (`kubectl get/logs/describe`, `flux get`, `scripts/checkHealth.sh`)
run on the read-only **Omni Reader** SA kubeconfig
([runbook](./omni-service-account.md)) — headless, no browser-OIDC dance.

- **NEVER `flux reconcile … --with-source`.** It annotates the Kustomization = a
  write the Reader role is denied. After a merge, rollback push, or value edit,
  **rely on the GitRepository poll** to pull it. Flux here is **poll-only on a
  30-min interval**: a `Receiver` + ingress exist (`flux-webhook.haynesops.com`)
  but GitHub has **no webhook configured** and that ingress is LAN-internal (a
  deliberate lockdown — nothing flux is exposed to the internet), so pushes are
  *not* push-triggered. **Budget up to 30 min** for `main` to be pulled, then the
  leaf-KS intervals apply it; the Reader can't accelerate it (`flux reconcile …
  --with-source` is a denied write). (The GitRepository `ignore`s everything
  outside `/kubernetes`, so docs/`.renovate`/`scripts` commits don't move its
  revision at all.)
- **Any cluster WRITE is BREAK-GLASS / human.** `kubectl delete/scale/annotate`,
  `flux suspend/resume`, finalizer edits, PVC/pod deletion, reseeds — all live
  outside the Reader SA. The shepherd's recovery path is **git → Flux**, full
  stop. When git-alone won't converge (immutable fields, wedged HelmReleases,
  stuck finalizers), it **pages with the diagnosis + runbook link** and stops.

### Scope & cadence

- **Edit only `kubernetes/**`.** The flux-local gate's test/diff jobs run *only*
  on `kubernetes/**` changes, and `Flux Local - Success` passes green on a
  *skipped* job — so a bot PR touching `.github/**` or repo root earns a
  trivially-green check. Constrain every shepherd edit to `kubernetes/**`. (The
  one exception is `.renovate/holds.json5` for the holds protocol below — that's a
  config write, not a cluster change, and never claims a flux-local pass.)
- **All changes via Git → Flux.** No `kubectl apply` for persistent state. Repo is
  source of truth.
- **One PR at a time** for the manual tier. Merge, reconcile, verify *green*, then
  the next. Lowest blast radius first; must-move-together pairs together (below).

### Operating rules injected into every run (`--append-system-prompt`)

`run-shepherd.sh` composes a `SAFETY_PROMPT` that is appended to **every** mode's
system prompt — so these hold even under the scheduled-ramp `UPGRADE_AGENT_PROMPT`
override in the HR env. The main rules beyond the cluster-read-only / merge-safety line
(the GH-auth and vet-marker rules live in `run-shepherd.sh` next to these):

1. **PR authoring — body as a FILE, never a heredoc.** The shepherd authors the PR
   body with the `Write` tool to `/tmp/pr-body.md`, then
   `gh pr create --title "…" --body-file /tmp/pr-body.md`. A `--body "$(cat <<EOF…)"`
   heredoc is a compound command that the `dontAsk` allowlist auto-denies — it silently
   blocked the shepherd from opening immich #1966 (a human had to open it). The
   file-then-`--body-file` shape stays inside the allowlisted `Write` + `gh pr create`
   tools.
2. **Backup gate (stateful upgrades).** Before merging / auto-merging / forward-fixing
   any component with durable state (cnpg·postgres, rook-ceph, dragonfly, immich,
   authentik, paperless, the `*arr` apps…), the shepherd first confirms a **recent
   successful backup** exists — read-only, within its SA: `kubectl get backup -n database`
   (newest `.status.phase=completed` <24h, or the Cluster `.status.lastSuccessfulBackup`)
   for cnpg; `kubectl get replicationsource -A` (`.status.lastSyncTime` within schedule)
   for volsync-backed apps. No healthy backup <24h → it **HOLDs** (`HOLD: backup safety
   net compromised for <component>`) rather than proceeding, and the existing
   `CNPGBackupFailed`/`CNPGBackupStale`/VolSync backup-failure alerts page the human. This
   is the in-code version of *"see a backup within 24h and move forward, else a human is
   needed"* — it keeps the SA read-only (verify, don't trigger) and pages only when the
   safety net is actually broken.
3. **Autonomy.** The Job runs unattended on a schedule — no human answers a mid-task
   question. The shepherd finishes the allowlisted work or puts a structured verdict line
   at the top of its summary; it never ends a turn with an un-executed plan or an "I'll
   open the PR next" that then never happens.
4. **Verdicts, chosen by who has to act next (2026-09-30, #3287).**

   | Verdict | Meaning | Pages? |
   |---|---|---|
   | `DEFER: #<N> waits for #<M> — <why>` | The PR is fine and a **later run of this job** clears the wait by itself: the previous PR of a must-move-together set has not merged and rolled Ready yet, the drain rule's one stateful unit is spent this run, Kometa is running or about to start, a split component's first phase is unverified, a queued auto-merge has not landed. `waits for <condition>` when the blocker is not a PR. One line per waiting PR. **No vet marker**, so the next run re-reads it. | No, unless the same wait is still there after ~8h (below) |
   | `HOLD: <why>` / `HOLD NEEDED: …` | A **human** must act before the PR can move: backup safety net compromised, an allowlisted command denied, a failed work-order hand-off, a state the shepherd cannot explain, a `.renovate/holds.json5` entry needed. | Yes |
   | `BREAK-GLASS: <why>` | Recovery needs a cluster write the Reader SA does not have. | Yes |

   The 2026-09-30 08:01Z page (`esc-shepherd-0c7a9806`) is why DEFER exists: the second
   half of the rook v1.20.8 pair came out as a `HOLD` while the first half's auto-merge was
   still queued, and a `HOLD` always pages.

---

## Mode 3 — breaking-change shepherd (the core loop)

The manual-tier set that will never blind-auto-merge: database operators
(`cnpg`, `dragonfly-operator`), `cilium`, `coredns`, `traefik`,
`authentik`, `multus`, `device-plugins`, `rook-ceph`, `flux`. (Talos/k8s is its
own beast — it's *not* a Flux/Renovate flow; see
[talos-version-upgrade.md](./talos-version-upgrade.md).) These open **ordinary
Renovate PRs** (no allowlist, never auto-merge) — that is the shepherd's input
queue.

This is [`renovate-upgrade-batches.md` → Tier 3](./renovate-upgrade-batches.md#tier-3--breaking-changes-one-at-a-time)
made on-call. Per-component specifics (release-note URLs, the exact grep, the
required `values` edit, health queries, rollback steps) live in
[`tier4-component-playbooks.md`](tier4-component-playbooks.md) —
**open the component's section before touching its PR**.

### The loop

1. **Survey the queue.** Lowest blast radius first.
   ```bash
   gh pr list --limit 200 --json number,title,labels,mergeable,statusCheckRollup \
     --jq 'sort_by(.number) | .[] | "\(.number)\t\(.mergeable)\t\(.title)"'
   ```
   Note `type/major|minor|patch` labels. Pick the next PR by the
   [merge-order table](#component-playbooks--rollback) — coredns/traefik/multus
   before cilium/rook/cnpg/authentik; storage (rook) and Talos last.

2. **CONSULT the holds registry FIRST.**
   [`.renovate/holds.json5`](../../.renovate/holds.json5) is the final word — it
   `extends` last, so a hold beats any allowlist.
   ```bash
   grep -A6 "$PKG" .renovate/holds.json5    # read the HELD/Reason/Issue/Resume lines
   ```
   - **Held** (PR matches an `allowedVersions` exclusion) → **skip it**, don't
     re-investigate. The WHY is right there (e.g. the cloudnative-pg chart hold).
   - **Newly blocked** (you discover this release is broken and there's no
     same-package fix yet) → **[ADD a hold](#holds-protocol)** and move on. Don't
     leave the PR to churn.

3. **Read the release notes.** Chart `UPGRADING` / GitHub release:
   ```bash
   gh release view <tag> --repo <org/repo> --json body --jq .body
   ```
   The component playbook lists the exact notes to read (`readReleaseNotes[]`).

4. **Grep our usage + make the supporting edits.** The playbook's `grepOurUsage`
   and `knownBreakingPatterns` tell you the feature → file → required edit. Make
   the `helmrelease.yaml` / `helm-values.yaml` / CR edits in the same branch as
   the bump. Known landmines (see the playbook for the full set):
   - **app-template v5** flips `automountServiceAccountToken:false` — any
     app-template HR with a `serviceAccount:` (multus, dragonfly operator, gatus
     sidecars) must set it back to `true`.
   - **rook v1.20** needs the third `ceph-csi-drivers` chart wired `dependsOn`
     between operator and cluster, and `cephVersion` **pinned** (chart default
     jumps Ceph a major — one-way).
   - **traefik v40+** moved `service` values under `service.spec`.
   - **kube-prometheus-stack** CRDs live in the separate `prometheus-operator-crds`
     chart — bump it **first**.

5. **Branch, commit, push as the bot, open the PR.**
   ```bash
   git switch -c shepherd/<pkg>-<version>
   git add kubernetes/...                 # kubernetes/** ONLY
   git commit -m "feat(<pkg>): upgrade to <version>"
   git push -u origin HEAD
   gh pr create --fill
   ```

6. **Wait for the gate.** `gh pr checks <N> --watch` → require
   **`Flux Local - Success`**. Read the sticky rendered-diff comment; it's the
   pre-merge sanity check.

7. **Merge — one at a time.**
   ```bash
   gh pr merge <N> --squash --delete-branch
   ```
   **Must-move-together pairs merge together** (one PR / branch, never split):

   | Component | Move as a unit |
   |---|---|
   | `rook-ceph` | operator → `ceph-csi-drivers` → cluster (`dependsOn` order) |
   | `cnpg` | operator first, then the `Cluster` CRs it manages |
   | `device-plugins` | each plugin + its `dependsOn` NFD (NodeFeatureRule PCI IDs) |

   The scheduled `auto` run moves separate rook PRs as **one drain unit**, in order:
   it enables auto-merge on the next PR the first run that finds the previous one merged
   with its HelmRelease Ready on the new version and Ceph `HEALTH_OK`, and until then ends
   with `DEFER: #<next> waits for #<previous>`. Same-run chaining is not possible: Flux
   pulls `main` on a 30-minute poll, the Reader SA cannot `--with-source`, and a run is
   capped at 20 minutes. So on the 4-hour cadence a pair takes up to about 4 hours
   end to end.

8. **Reconcile = wait for Flux.** Do **not** `--with-source` (Reader can't write).
   Flux is **poll-only (~30 min)** here — no active GitHub webhook — so budget up
   to 30 min for `main` to be pulled. Watch read-only:
   ```bash
   flux get kustomization <ks> -n <ns>          # Ready=True + revision == your commit
   ```

9. **Run the health gate.** [`upgrade-health-gate.md`](./upgrade-health-gate.md)
   **plus the component's own `healthChecks`** from the playbook (CNPG cluster
   health, `ceph -s`, MQTT broker (`mosquitto-0`) up, cilium connectivity, HA Zigbee availability,
   etc.).
   ```bash
   scripts/checkHealth.sh                        # non-Ready Flux ks/hr cluster-wide
   ```
   - **GREEN** → next PR.
   - **REGRESSION** → **roll back** via the component's rollback section
     (git revert / re-pin the OCIRepository `ref.tag` → push as the bot → Flux
     applies) **and page**. If the documented rollback won't converge (one-way
     major, wedged HR, reseed needed) it's **break-glass** — page with the
     diagnosis, don't improvise a cluster write.

> **Blast-radius discipline:** never have two stateful upgrades in flight. Verify
> one *green* before merging the next. A bad CNI/DNS push can sever the very
> networking Flux needs to apply your revert — which is exactly why these are
> Tier-4 and exactly why you go one at a time.

---

## Mode 2 — summoned remediation

Invoked when an upgrade *already failed*: an alert fired, a `Kustomization` is
stuck `not Ready`, or a post-merge regression got spotted (chat session,
page-reply, or a `RemoteTrigger` hook). This is the **Rollback** section of
[`renovate-upgrade-batches.md`](./renovate-upgrade-batches.md#rollback), automated
and on-call.

1. **Diagnose** (read-only, Reader SA):
   ```bash
   flux get kustomization -A | grep -v 'True'
   flux get helmrelease -A   | grep -v 'True'
   kubectl describe kustomization <ks> -n <ns>           # what's wedged + why
   kubectl -n <ns> get pods; kubectl -n <ns> logs <pod> --tail=100
   ```
   Cross-check the component playbook's `healthChecks[].regression` for the signal.
2. **Attempt the documented rollback** from the component's playbook section —
   git revert the bump / re-pin the prior OCIRepository tag → push as the bot →
   let Flux apply (no `--with-source`). Re-run the health gate to confirm
   convergence.
3. **If it doesn't converge → page (break-glass).** Reverts that hit immutable
   fields, crash-looping source/kustomize-controllers, stuck finalizers, one-way
   majors (Ceph daemon, PG major, cilium eBPF map, authentik forward-only
   migration), or a reseed all need a cluster write the Reader is denied. **Do not
   improvise it.** Page with: what regressed, the diagnosis, the rollback you
   tried, and the link to the component playbook + the relevant runbook
   ([volsync-unlock](./volsync-unlock.md), [talos-version-upgrade](./talos-version-upgrade.md),
   [flux-pvc-prune-safety](../rules/flux-pvc-prune-safety.md)).

---

## Mode 1 — scheduled health gate

The phase-4a watch-and-page loop. Full procedure:
**[`upgrade-health-gate.md`](./upgrade-health-gate.md).** In brief: cron / `/loop`
trigger runs the post-reconcile health checks (Flux Kustomization status, CNPG /
Rook-Ceph / MQTT broker health, HA Zigbee availability) on the Reader SA and **pages on
regression**. **Rollback stays human in 4a** — the gate's job is to guarantee a
bad merge never goes unnoticed, not yet to act on it. (Automated rollback is the
phase-4b endgame, in-cluster CronJob.)

---

## Phase 4b.3 — auto-merge & auto-summon (operating)

The in-cluster hands-off endgame. Everything ships **inert** (both CronJobs suspended);
enabling it is a deliberate flip. `run-shepherd.sh` gained two modes and a spend guard;
a second CronJob (`upgrade-shepherd-triage`) does the auto-summon.

### `UPGRADE_AGENT_MODE` — the four modes

| Mode | Tools | Merge? | Model | Use |
|---|---|---|---|---|
| `dryrun` (default) | read-only | no | Opus 5.5 | report a plan, make nothing |
| `shepherd` | + edit/PR | no (human merges) | Opus 5.5 | 4b.1 supervised PR authoring |
| `auto` | + `gh pr merge --auto` | **queues** (server-side) | Opus 5.5 | 4b.3 hands-off auto-merge |
| `remediate` | + edit/PR/merge | queues | **Opus 5.5** (`UPGRADE_AGENT_REMEDIATE_MODEL`; its own knob since 2026-07-06) | Mode-2 regression fix/rollback — rare, \$5-capped, diagnosis-bound |

The Model column is the plan path (`UPGRADE_AGENT_MODEL`, `claude-opus-5-5`). Every
mode falls back to `claude-sonnet-5-5` on the metered API key
(`UPGRADE_AGENT_FALLBACK_MODEL`, Sonnet 5.5 since 2026-09-28), never Fable or Opus.

**Durable verdicts (2026-07-06):** every run leaves its final summary line at
`/tmp/shepherd-summary.txt`; after a remediate, triage records it as `note` on the
coordination-state entry and the **gate appends it to its page body**
(`… | shepherd: BREAK-GLASS: <reason>`), so the WHY reaches the phone without
digging Job logs.

**Merge safety is server-side, not the allowlist.** The bot is non-admin + non-bypass,
so `gh pr merge --auto` only *queues*; GitHub merges **only when Flux Local *and* Diff
Scope are both green**. It cannot merge past a red/pending check, and `--admin`
(skip-checks) fails for a non-admin. So `auto`/`remediate` are safe to allowlist `gh pr
merge` — the Phase-B required checks are the boundary.

**Stalled auto-merge fallback (2026-09-30, #3287).** A queued auto-merge fires on a
check-completion event. Queue one on a PR whose checks finished hours earlier and GitHub
may never act: `gh` queues rather than merges when it reads `mergeStateStatus` as
`UNKNOWN` (computed lazily), and no further check event arrives. #3280 sat `CLEAN`, 7/7
green, auto-merge set, from 04:07Z to 08:09Z. So after the LLM's turn in `auto` mode the
**launcher** (`automerge_fallback` in `run-shepherd.sh`, not the LLM) watches each PR
whose auto-merge the shepherd bot enabled during that run, for up to
`UPGRADE_AGENT_AUTOMERGE_WATCH_S` (360 s). Once one has been `CLEAN` for
`UPGRADE_AGENT_AUTOMERGE_GRACE_S` (120 s), with every check green and both required
contexts present, it runs a plain
`gh pr merge <N> --squash --delete-branch --match-head-commit <sha>`. It never uses
`--admin`, and branch protection still binds it. It leaves alone a PR whose auto-merge
someone switched off, and anything Renovate or a human queued. A PR still waiting on checks
when the watch ends stays with GitHub, because a check that completes after auto-merge was
queued is the event that fires it. If the plain merge is refused and the PR is still
unmerged, it escalates (`esc-shepherd-*`, keyed on PR + SHA). `remediate` does not run the
fallback.

**What escalates (MODE=auto).** A terminal `HOLD`/`BREAK-GLASS` verdict or `rc≠0` files
an `esc-shepherd-*` entry at once. dev-env-ops opens a joinable session and pages with its
name. `DEFER` does not. `defer_track` keeps each wait's streak in the
`upgrade-shepherd-defers` ConfigMap (runtime state, not in git). The key is the PR numbers
on the DEFER line, so the key stays the same when the wording changes. A wait that is not
deferred again ends its streak. A wait still deferred after
`UPGRADE_AGENT_DEFER_ESCALATE_MINUTES` (450, the third consecutive deferral on the 4-hour
cadence, about 8 hours) escalates once per streak, as `DEFER stalled: …`, under a stable
signature. That is the fatal backstop: a wait that does not clear is a real stall, and the
drain queue does not trip it because its blocker changes every run.

```bash
kubectl -n upgrade-agent get cm upgrade-shepherd-defers -o jsonpath='{.data.state}' | jq .   # live waits
```

### The scheduled-run pre-filter (cost control — LLM only when there's in-scope work)

The scheduled `auto` run is deterministically pre-filtered so a **quiet day costs \$0**.
Before any clone or LLM call, `run-shepherd.sh` (`prefilter_should_run`) lists open
**Renovate** PRs and checks each one's changed files against the ramp path prefixes.
If **no** open Renovate PR touches a ramp path (or every in-scope one is already
vetted, below) it logs `pre-filter: … skipping LLM ($0)` and exits `0` — no clone, no
LLM, no spend record. Only when unvetted ramp work exists does it fall through to the
(paid) survey+vet.

- **It never decides mergeability.** The LLM still fully vets every in-scope PR (holds,
  release notes, supporting-edit detection). The filter only decides *whether there is
  work worth looking at* — the conservative split: deterministic gate on the *look*,
  LLM owns the *judgement*.
- **Fails OPEN.** Any `gh`/`jq` error → it returns "run the LLM" (never silently skips
  real work); the identical old behaviour on error.
- **The ramp is ONE env var (2026-07-06):** `UPGRADE_AGENT_RAMP` — a component list in
  [`shepherd/app/helmrelease.yaml`](../../kubernetes/main/apps/upgrade-agent/shepherd/app/helmrelease.yaml).
  The component→path map lives in `run-shepherd.sh` (`ramp_globs_for`) and derives the
  filter globs; at run start a consistency check asserts every ramp component is also
  named in `UPGRADE_AGENT_PROMPT` and has a path mapping — **any drift makes the
  scheduled run REFUSE to vet (fail closed, exit 2) and the gate pages
  `shepherd/ramp-mismatch`** (proven able to fire 2026-07-06). Widen the ramp by adding
  the component to `UPGRADE_AGENT_RAMP` + naming it in the prompt (+ a map row for a
  brand-new component), then `/kyverno-verify`. (`UPGRADE_AGENT_PREFILTER_GLOBS` still
  exists as an explicit override for tests.)
- **VET-ONCE markers (2026-07-06):** after fully vetting a PR (any verdict), the
  shepherd posts a PR comment whose first line is `<!-- shepherd-vet sha=<head> -->`;
  the pre-filter skips in-scope PRs already vetted at their **current** head SHA, so a
  PR baking through `minimumReleaseAge` is paid for once, not daily. A Renovate rebase
  moves the SHA and re-arms the vet automatically.
- **Never conclude "Renovate owns it" without checking the carve-outs (2026-07-24).**
  A `left-for-renovate` / "Tier-2 — Renovate auto-merges this, leave it" verdict is a
  **merge decision** — treat it like one. Before recording it, grep the actual policy:
  `grep -nA3 "$PKG" .renovate/autoMerge.json5`. Renovate applies `packageRules` per-dep
  with **last-rule-wins**, so a name-matched `automerge:false` carve-out **overrides**
  the broad `downloads/**` · `media/**` · "our own images" leaf-tiers — and the update
  **type** matters (qbittorrent **patch** auto-merges, but **minor/major/digest** are
  manual; plex and dev-env are `automerge:false` for **all** types). If Renovate will
  NOT auto-merge this package+type, it needs a **human** → record `manual-only`, never
  `left-for-renovate`. This is the qbittorrent **#2132** miss: a `left-for-renovate` vet
  on a PATCH sat 5 days green while the then-current whole-package `automerge:false` meant
  Renovate never would — a human had to notice and merge by hand. The deterministic
  disposition labeler mirrors these same carve-outs
  (`.github/workflows/pr-disposition.yml` → `CARVEOUTS`).
- **Orphan-PR report (2026-07-06):** every scheduled run also writes a deterministic
  digest of open Renovate PRs >7 days old to the `upgrade-orphan-report` ConfigMap;
  the **gate** pages it weekly (`renovate/orphans`, warning) so nothing ages silently.
- **Gating:** the filter runs only when `MODE=auto` **and** a ramp is configured. A
  manual/targeted summon must **clear `UPGRADE_AGENT_RAMP`** (and
  `UPGRADE_AGENT_PREFILTER_GLOBS` if set) to bypass it — see the summon recipe.

### Summon (manual, any mode)

```bash
# dryrun (read-only, free-ish):
kubectl -n upgrade-agent create job shep-$(date +%s) --from=cronjob/upgrade-shepherd
# a specific mode (create-job can't set env; inject via jq). NOTE: clear
# UPGRADE_AGENT_RAMP so a TARGETED summon is never pre-filtered out (else a
# PR outside the ramp — e.g. a cnpg negative-proof test — would be skipped before the LLM):
kubectl -n upgrade-agent create job shep-$(date +%s) --from=cronjob/upgrade-shepherd \
  --dry-run=client -o json \
  | jq '.spec.template.spec.containers |= map(if .name=="app"
        then .env |= (map(if .name=="UPGRADE_AGENT_MODE" then .value="auto"
                          elif .name=="UPGRADE_AGENT_RAMP" then .value="" else . end)
                      + [{name:"UPGRADE_AGENT_PROMPT",value:"<task>"}]) else . end)' \
  | kubectl apply -f -
```

### The triage auto-summon (`upgrade-shepherd-triage`)

Deterministic, **no-LLM on the healthy path**. On each run `triage.sh`: (1) checks for a
merge to `main` in the last `TRIAGE_MERGE_LOOKBACK_HOURS` (default 3) via the GitHub API;
(2) checks for a regression *now* (Flux NotReady>10m, firing severity=critical, persisted
crashloop); (3) only if **both** → `exec run-shepherd.sh` with `MODE=remediate`. On a
healthy cluster it exits in a couple of curls (\$0). **Deliberately decoupled from the
health-gate** — the gate stays the independent read-only Pushover tripwire; do not couple
them. (Merge-correlation uses all `main` commits, so a docs-only commit + an unrelated
pre-existing regression could summon remediate once — harmless, bounded by the
regression AND + the spend guard; tighten to the Flux GitRepository revision if it ever
misfires.)

### The monthly spend guard

`run-shepherd.sh` tracks month-to-date bot spend in a ConfigMap (`upgrade-shepherd-spend`,
runtime state, **not** git-managed) and **refuses an unattended (`auto`/`remediate`) run**
once month-to-date + the per-run cap (`--max-budget-usd`, \$5) would exceed
`UPGRADE_AGENT_MONTHLY_CAP_USD` (default **\$50**). Manual modes record but are never
blocked. Fails **open** on a kubectl error — the Anthropic **account balance is the hard
backstop** (set a low balance / a per-workspace \$50/mo console limit for a server-side
ceiling).

```bash
kubectl -n upgrade-agent get cm upgrade-shepherd-spend -o jsonpath='{.data}'   # check
kubectl -n upgrade-agent delete cm upgrade-shepherd-spend                       # reset (recreated next run)
```

### The final flip (enable) — and the kill switch

Enable = unsuspend + give a real schedule (do the two CronJobs separately, watch between):

```bash
# kill switch (instant, no git) — re-suspend either CronJob:
kubectl -n upgrade-agent patch cronjob upgrade-shepherd-triage -p '{"spec":{"suspend":true}}'
```

Prefer enabling via **git** (edit `suspend`/`schedule`/`MODE` in the shepherd HR → Flux):
`upgrade-shepherd` MODE=auto on a slow cadence for the safe manual-tier set first, then
widen; `upgrade-shepherd-triage` on ~`*/30`. A bad auto-merge is undone by
`git revert` → Flux (Flux polls ~30 min here), and the triage's remediate mode is the
autonomous version of that.

---

## Holds protocol

A hold is how the shepherd records "this release is broken and I can't fix it
right now" so the PR stops churning and the WHY lives next to the enforcement —
not in a human's head. Full spec:
[README → Holds registry](../../docs/renovate/README.md#holds-registry--reasoned-release-blacklist).
Each hold is a `packageRule` in [`.renovate/holds.json5`](../../.renovate/holds.json5)
that **enforces** via `allowedVersions` and **documents** via a structured
`description` (`HELD` / `Reason:` / `Issue:` / `Resume:` / `Recorded:`).

**ADD a hold** (when you hit an un-fixable-right-now blocker):

1. Append a `packageRule` per the in-file convention; set the **tightest**
   `allowedVersions` that excludes the bad release.
   - Fix is a later version of the *same* package → auto-resume range:
     `'<2.3.2 || >=3.0.0'` (skips 2.3.2–2.x, re-opens at 3.0.0).
   - Resume condition is something else (e.g. "after a *different* component
     upgrades") → plain upper bound (`'<6.2.1'`), lifted by hand.
2. Validate + commit (this is a `.renovate/` config write, not a `kubernetes/**`
   change):
   ```bash
   npx --yes --package renovate renovate-config-validator .renovate/holds.json5
   git commit -m "renovate(holds): hold <pkg> <range> — <reason>"
   ```

**LIFT a hold:** delete the rule (or widen `allowedVersions`) in a commit
referencing the upstream fix; Renovate re-proposes. (A rule whose resume condition is a *different* package's version, as the
retired EMQX broker hold was, cannot be encoded in `allowedVersions` and needs a
**manual** lift.)

---

## Component playbooks & rollback

Merge lowest-risk first. Each row links its full section in
[`tier4-component-playbooks.md`](tier4-component-playbooks.md)
(release notes, grep targets, breaking patterns, health checks, rollback steps).
**Rollback-risk** is the one-word "what bites if you have to undo it":

| Order | Component | ns | Rollback risk | One-line caveat |
|---|---|---|---|---|
| 1 | [`coredns`](tier4-component-playbooks.md#coredns) | kube-system | **clean** | fully stateless; HR auto-rolls a failed upgrade. Risk is a DNS-resolution gap during churn, not data. |
| 2 | [`traefik`](tier4-component-playbooks.md#traefik) | network | **clean** | stateless; revert the `lbipam.cilium.io/ips` annotation alongside the chart. Do `traefik-internal` before `-external`. |
| 3 | [`multus`](tier4-component-playbooks.md#multus) | network | **clean / node-wide blast** | stateless DaemonSet, but `multusConfigFile:auto` makes it the node's primary CNI → a break stops **all** new pods on that node. If breakage came from the shared **app-template** chart, revert *that* OCIRepository, not the multus tag. |
| 4 | [`device-plugins`](tier4-component-playbooks.md#device-plugins) | kube-system | **clean / Plex-unschedulable** | stateless; HRs auto-roll a *failed* upgrade. intel failure → Plex `Unschedulable` (HW transcode dies). GPU off the PCI bus = host reboot (break-glass). |
| 5 | [`flux`](tier4-component-playbooks.md#flux) | flux-system | **clean / CRD-wedge** | stateless (state is CRs in etcd). A CRD storage-version downgrade can wedge the git-only revert; a crash-looping source/kustomize-controller can't apply its own revert → break-glass. |
| 6 | [`dragonfly-operator`](tier4-component-playbooks.md#dragonfly-operator) | database | **in-memory flush** | control-plane (operator/CRD/rbac) revert is clean **if** the old operator doesn't re-roll the STS. Any data-plane restart flushes the entire keyspace (replicas:1, no PVC) → drops immich BullMQ + paperless Celery in-flight jobs (re-enqueue on restart). HAND-MAINTAINED ClusterRole in `app/rbac.yaml`. |
| 7 | [`cilium`](tier4-component-playbooks.md#cilium) | kube-system | **one-way eBPF / break-glass** | no data, but eBPF map-layout migration is one-directional (old agent crash-loops; recovery = a brief `cleanBpfState=true` value edit = datapath wipe). CRD storage-version (`CiliumLoadBalancerIPPool` v2alpha1→v2) can strand LB-IP announcement. A bad CNI push severs networking Flux needs to self-heal → out-of-band. |
| 8 | [`authentik`](tier4-component-playbooks.md#authentik) | network | **migrations / PITR** | **patch within same `YYYY.M` = clean.** A `YYYY.M` **major** runs forward-only DB migrations with no down-migrations → old image crashes on the migrated schema; recovery = CNPG PITR, which is **all-tenant** (one barmanObjectStore covers authentik+grafana+…) and loses every write since the upgrade. **Bias to forward-fix.** |
| 9 | [`cnpg`](tier4-component-playbooks.md#cnpg) | database | **reseed** | operator-chart revert is low-risk (data plane untouched), but CNPG doesn't officially support operator **downgrade**. PG **major** = one-way (`pg_upgrade`); reverting `imageName` against migrated PGDATA only crash-loops → restore from S3 (~600 GB egress). **`postgres16-pgvecto` is single-instance** — `delete pvc` = total loss (no replica source); the "delete pvc to reseed" trick is **only** for the 3-instance `postgres16` replicas. Bias to forward-fix. |
| 10 | [`rook-ceph`](tier4-component-playbooks.md#rook-ceph) | rook-ceph | **one-way major (storage)** | **always last.** Chart-only bump is revertible (HRs carry `strategy:rollback`); any bump that moved a Ceph daemon **major** is effectively one-way — old binaries refuse migrated metadata. Operator → `ceph-csi-drivers` → cluster as a unit. **Pin `cephVersion`** so the chart bump never silently carries a Ceph major. Break-glass OSD/daemon surgery can destroy a replica set — page instead. |
| — | [`talos-kubernetes`](./talos-version-upgrade.md) | *(below Flux)* | **break-glass (Omni admin)** | **NOT a Flux/Renovate flow** — applied by `task omni:sync` (omnictl, Omni **admin** identity), so a git revert does nothing and the Reader SA can't roll it back. Talos patch/minor downgrades in-place (preserves `/var`); **k8s minor downgrade is unsupported** → forward-fix only. Entire rollback escalates to a supervised `OMNICONFIG` session. Follow [talos-version-upgrade.md](./talos-version-upgrade.md) + [talos-omni-gotchas](../reference/talos-omni-gotchas.md). |

### Cross-references

- Alert enrichment (ANY critical, not just upgrades — the Track-B1 responder): [`alert-responder.md`](./alert-responder.md)
- Batch process & per-component patterns: [`renovate-upgrade-batches.md`](./renovate-upgrade-batches.md)
- Read-only cluster access: [`omni-service-account.md`](./omni-service-account.md)
- Bot identity / threat model: [`tier4-bot-setup.md`](../../docs/renovate/tier4-bot-setup.md)
- Scheduled gate: [`upgrade-health-gate.md`](./upgrade-health-gate.md)
- PVC prune safety (renaming/moving Kustomizations): [`flux-pvc-prune-safety.md`](../rules/flux-pvc-prune-safety.md)
- VolSync stale-lock fallout after operator rolls: [`volsync-unlock.md`](./volsync-unlock.md)
