# Cold-start adversarial review — Tier-4 hands-off upgrade automation

You are a skeptical, senior security + platform engineer brought in to **red-team an
entire body of work before it goes fully hands-off.** Another agent (a capable one, but
*trust nothing it concluded*) spent a long session building and "verifying" a system that
lets Renovate + an in-cluster LLM shepherd propose, edit, validate, and (soon) **auto-merge
and auto-rollback** Kubernetes upgrades on a production home-lab cluster, with a machine
guardrail stack instead of human review. Your job is to **prove it wrong.**

Three mandates, in order:
1. **Poke holes.** Find the bypass, the false-negative, the config that doesn't do what
   its comment claims, the emergent interaction between two "safe" pieces. Prefer a
   working exploit over a worry.
2. **Harden.** For each real hole, land the fix (or write the exact patch) and re-prove it.
3. **Propose totally different approaches.** Don't just tune what's here. Question the
   whole paradigm — the trust model, the tool choices, the architecture — and lay out
   alternatives you'd genuinely prefer, with honest trade-offs. The prior agent was
   inside the box; you are not.

Be adversarial, not agreeable. If the design is sound in places, say so briefly and move
on — spend your effort where it's weak. **Empirical over rhetorical:** craft the malicious
diff and run it; create the throwaway violating resource and watch admission; dry-run the
API change against a scratch value. A claim you can't demonstrate is a hypothesis, label
it as such.

---

## 0. Orient (read first, in this order)

1. **Agent memory** — `~/.claude/projects/-home-thaynes-workspace-haynes-ops/memory/renovate-automation-roadmap.md`. The full chronological build log incl. this session's Part-1 audit → Phase B → Phase C → Phase D groundwork → cosign. **This is the prior agent's own account — read it as the thing you are auditing, not as ground truth.**
2. `docs/renovate/tier4-audit-2026-07-02.md` — the prior agent's Part-1 hardening report (its self-assessment, incl. items it marked "verified OK" — re-verify those first, that's where confirmation bias hides).
3. `docs/renovate/README.md` (tiers, holds registry, changelog) + `docs/renovate/tier4-bot-setup.md` (the bot App threat model).
4. The runbooks: `.agents/runbooks/upgrade-shepherd.md` (modes, triage, spend guard, kill switch), `upgrade-health-gate.md`, `tier4-component-playbooks.md`.
5. `CLAUDE.md` (repo rules: GitOps-only; change-authorization guardrail; the AppDaemon cross-repo flow).

## 1. What is live (the attack surface — verify each exists as described)

- **`haynes-ops-bot` GitHub App** — bot identity; token minted from a PEM in 1Password (`github-bot`/HaynesKube) by `scripts/github-app-token.sh` (contents:write, pull_requests:write, checks:read; 1h TTL).
- **`diff-scope.sh` + `.github/workflows/diff-scope.yml`** — the PRIMARY gate. Author-scoped (only *enforces* for `haynes-ops-bot[bot]`), runs the base-branch copy (tamper-proof). GATE A = sensitive-path/kind denylist on added lines; GATE B = masked-multiset equality (only a pure version/digest/chart bump passes). **Now a REQUIRED check on `main`.**
- **Push protection** — Main ruleset `14013135` (require-PR, 0 approvals, admin RepoRole-5 bypass) + new Edge ruleset `18431432`. Both required checks (`Flux Local - Success`, `Diff Scope - Success`) on main.
- **Kyverno 3.8.1**, `kubernetes/main/apps/kyverno/` — 3 policies now **ENFORCE** (`restrict-image-registries`, `restrict-rbac-escalation`, `pod-security-baseline`) + `verify-thaynes43-images` (**Audit**), all `failurePolicy: Ignore`. Exceptions in `policies/app/exceptions/`.
- **Health gate** — `kubernetes/main/apps/upgrade-agent/health-gate/`, deterministic LLM-free CronJob (`*/30`), read-only SA, egress CNP, pages Pushover directly.
- **Shepherd** — `kubernetes/main/apps/upgrade-agent/shepherd/`, an LLM (claude-code) with modes dryrun/shepherd/**auto**/**remediate**; a **$50/mo spend guard** (ConfigMap `upgrade-shepherd-spend`); a suspended `upgrade-shepherd-triage` auto-summon CronJob. **Both CronJobs suspended (inert).**
- **cosign** — both self-built image builds (`upgrade-agent`, `upgrade-shepherd`) keyless-sign (cosign v2.4.1, legacy `.sig`); `verify-thaynes43-images` verifies them against the GH-OIDC identity. appdaemon (hass-sandbox) NOT yet signed.

## 2. Highest-value targets (attack these hard — plus find your own)

Ranked by blast radius. For each, the prior agent believes it's safe — assume it isn't.

1. **`diff-scope.sh` GATE B masked-multiset + `normalize()`.** This bash regex is the single control that must stop a prompt-injected shepherd from auto-merging a malicious manifest. Try to defeat it: can a payload be shaped to normalize identically to a benign removed line? YAML flow-scalars, anchors/aliases, multi-doc files, block scalars (`|`/`>`), unicode, whitespace, a value that *looks* like `vX.Y.Z`? Can a net-new dangerous line be smuggled by also removing a line that masks to the same token? GATE A is line-anchored (`^\+\s*`) — find sensitive content it can't see that GATE B also misses. **Build the exploit diff and run `DIFF_SCOPE_RANGE=… bash scripts/diff-scope.sh` on it.**
2. **Kyverno exceptions = the soft underbelly of enforce.** The prior agent excepted whole-namespace `rook-ceph`, name-globs (`cilium*`, `comfyui*`, `volsync-src-*`…), and a `volsync` label. **Concrete suspicion to confirm or kill:** does a pod merely *named* `volsync-src-evil` (or `cilium-x`, in the right ns) with `privileged: true` get exempted from `pod-security-baseline`? Who can create such a pod (RBAC)? Is the whole-ns rook-ceph exception a hole? Are the `restrict-rbac-escalation` name-exceptions abusable (bind to `cluster-admin` under an excepted name)? **Create throwaway violating resources and watch admission (server-dry-run).**
3. **The prompt-injection → auto-merge chain.** The shepherd reads attacker-influenceable upstream release notes and, in `auto`/`remediate` mode, holds `gh pr merge`. Red-team the full path: injection → what can it actually get merged past diff-scope + Kyverno + the required checks? Is "merge is server-side, non-admin can't pass a red check" airtight, or is there a check-bypass (e.g. a PR that makes flux-local *skip* → green, combined with diff-scope passing)? Recall flux-local passes on **skipped** jobs.
4. **The spend guard & the shepherd's one cluster write.** The shepherd SA can `get/update` the `upgrade-shepherd-spend` ConfigMap. Can a rogue/injected shepherd zero its own counter, or is it blocked by the tool-allowlist vs RBAC distinction? Does fail-open (on kubectl error) let an attacker disable the cap by breaking the read? Is `$50/mo` even enforceable given the per-run cap is advisory to the LLM?
5. **verifyImages keyless.** The attestor subject is a **wildcard** `…/haynes-ops/.github/workflows/*`. Is that too permissive (any workflow, incl. a future/injected one)? It's **Audit** today (so the backdoored-image gap is still open). Admission depends on Fulcio/Rekor egress with `failurePolicy: Ignore` — is "sigstore down → fail-open → unsigned image admitted" acceptable? cosign is pinned to an **older v2.4.1** for Kyverno compat — supply-chain implications?
6. **Trust roots you can't out-guardrail.** diff-scope *trusts* Renovate PRs (author-scoped). A compromised Renovate/Mend, or a malicious upstream chart/image on an *allowed* registry path (a pure tag bump), sails through. Weigh `minimumReleaseAge`, digest pinning, and cosign-for-third-party as mitigations — or argue the residual risk is acceptable and why.
7. **Blast-radius & timing.** Flux here is **poll-only ~30 min** (no webhook). A bad auto-merge applies within ~30 min and a revert takes ~another 30. Is that exposure window acceptable for the manual-tier (rook/cnpg/cilium/authentik) the endgame targets? Should auto-merge be scoped far narrower than "manual-tier," or land on `edge`/behind a Flux `dependsOn` health-gate first?
8. **The triage auto-summon.** Merge-correlation uses *all* `main` commits (docs commits included) AND'd with a regression; it then hands an LLM `git revert` + auto-merge. Can a benign commit + an unrelated flake trigger an autonomous rollback of the wrong thing? Is the deterministic check a faithful subset of the gate, or can it miss/mis-fire?

## 3. Propose different approaches (required, not optional)

Don't just harden — say what you'd build instead, with trade-offs:
- **Admission engine:** Kyverno vs native **ValidatingAdmissionPolicy** (CEL, GA on this k8s 1.35 — no extra controller) vs Gatekeeper. Is the report-then-enforce workflow worth Kyverno's footprint?
- **The scope gate:** `diff-scope.sh` (bash regex) vs a policy on the *rendered* manifest (Conftest/CEL/kyverno-json) vs signing the *diff*. Is a regex the right tool for a security boundary?
- **The trust paradigm itself:** is "LLM opens a PR that auto-merges on green checks" the right model, or is something like *deterministic-verifier-gates-LLM-proposal* / *typed-transformation-with-provenance* / *human-approval-for-a-risk-class* fundamentally safer for the same hands-off-ness?
- **Egress posture:** the cluster has almost no NetworkPolicies (only the two agent CNPs). Weigh a baseline default-deny egress vs the effort/breakage.
- **Runtime:** the shepherd is a one-shot headless `claude -p`. For where the operator wants to go (conversational cluster-bot: "why did Pushover fire", "add an HA automation"), is a suspended CronJob the right chassis, or should this be a durable, bidirectional agent — and does that change the security model? Should the agent runtime become its own repo (hass-sandbox→appdaemon shape) with its own CI/tags?

## 4. Access, tools, method

- **Live cluster:** `kubectl` + `flux` work (admin, via the workstation kubeconfig — the read-only-SA note in memory is about the *shepherd's* identity, not yours). `gh` is authed as `thaynes43` (admin, `repo`+`workflow` scopes) — you can inspect/modify rulesets and re-run workflows. **MCP:** grafana (PromQL/LogQL), home-assistant, unifi are wired for live introspection.
- **Not in env:** `op` (1Password CLI) — you can't mint the bot token yourself. To empirically test the bot identity against a ruleset, spawn a one-off Job from the shepherd CronJob (its init-container mints the token) and override the container command via `jq` — see how the prior agent proved "bot direct-push rejected" (memory + the pushtest pattern).
- **Available:** `helm`, `jq`, `git`, `openssl`, `python3`, `perl`. No local `cosign`.
- **Timezone trap:** AppDaemon/Z2M logs = ET; Prometheus/Loki/kubectl = UTC.

## 5. Rules of engagement

- **Read-only by default.** Investigation, dry-runs, `--dry-run=server`, and throwaway resources you immediately delete are fine and encouraged. **Do NOT** flip any Kyverno policy to/from Enforce, enable auto-merge, unsuspend a CronJob, change a ruleset for real, or push cluster-mutating changes **without the operator's explicit go** — those are his calls. Propose + show proof, then wait.
- **Land clear, low-risk fixes** (a diff-scope patch, a tightened exception, a policy correction) via the normal GitOps flow *if* they're unambiguous and reversible — but surface anything that widens attack surface or could break a workload.
- **Clean up every test artifact** (Jobs, throwaway pods/bindings, scratch branches, probe triggers). Don't leave the cluster or the repo dirty.
- **Don't trust "verified."** Re-run the prior agent's proofs. Where it said "0 fails," check `result==error` too. Where it said "the bot can't," make the bot try.

## 6. Deliverable

A single ranked report:
- **Findings**, most-severe first. Each: one-line defect → concrete failure scenario (inputs/state → wrong outcome) → whether you *confirmed* it (with the command/output) or it's *plausible* → the fix (patch or exact steps).
- **Alternatives** worth adopting, each with the trade-off and a recommendation.
- **What you re-verified and it held** (brief — so the operator knows what's actually trustworthy).
- The **open calls** that are the operator's to make.

Land the clear fixes, show proof not claims, and give the operator the ranked report before proposing any Enforce/auto-merge flip. Start with §2.1 and §2.2 — that's where a real hole, if it exists, most likely is.
