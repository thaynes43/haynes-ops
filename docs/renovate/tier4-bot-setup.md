# Tier 4 — `haynes-ops-bot` GitHub App setup

The keystone credential for the Tier 4 upgrade-shepherd agent. This doc is the
verified, least-privilege setup procedure (researched + adversarially verified
against GitHub's official docs and live `gh api` checks on this repo,
2026-06-30). It is the click-by-click guide for registration plus the
maintainer's wiring reference.

## Why a GitHub App (and not a PAT)

The Tier 4 shepherd runs **unattended** (a Claude Code cloud routine and/or an
in-cluster CronJob) and must push branches, open PRs, merge green PRs, and push
rollback reverts.

- **It must not run as a human.** An unattended bot should have its own scoped,
  revocable, non-human identity — not your personal account's reach.
- **Its pushes/PRs must trigger `flux-local`.** Any non-`GITHUB_TOKEN` identity
  (an App installation token, a PAT, *or* your own `gh` in the supervised phase)
  triggers the `pull_request` workflow. The `GITHUB_TOKEN`-doesn't-trigger
  restriction only applies to the token *inside* a GitHub Actions run, which is
  not how the agent runs — so this is about *identity*, not *triggering*.
- **App installation token > PAT** for this job: 1-hour TTL, single-repo scope,
  isolated ~5k req/hr budget, clean bot attribution (separable from `app/renovate`
  and from you), and a non-human identity that survives operator-account rotation.
  The only durable secret is the App private key; the tokens it mints auto-expire.

The cluster-verification credential is **separate and unrelated**: a read-only
Omni service-account kubeconfig (Reader role, see
[`.agents/runbooks/omni-service-account.md`](../../.agents/runbooks/omni-service-account.md)).
This two-credential split is load-bearing — neither single leak yields
write-to-cluster.

## Permission matrix (least privilege)

| Repository permission | Access | Decision |
|---|---|---|
| **Contents** | **Read & write** | GRANT — push branches (`POST /git/refs`) + revert/rollback commits |
| **Pull requests** | **Read & write** | GRANT — open PRs (`POST /pulls`) **and merge** them (`PUT /pulls/{n}/merge` is bundled here) |
| **Checks** | **Read-only** | GRANT — read the `Flux Local - Success` gate (it's an Actions **check run**, read via `GET /commits/{ref}/check-runs`) |
| **Metadata** | **Read-only** | GRANT (auto — force-selected the moment any other repo perm is set; can't be deselected) |
| Commit statuses | Read-only | OPTIONAL/defensive only — the gate is a check run, not a legacy status, so Checks:read already covers it. Skip. |
| **Workflows** | Write | **OMIT** (default). Only needed to merge/author commits touching `.github/workflows`. The kubernetes/** mission never does; Renovate's action-SHA bumps are merged by Mend platformAutomerge, not the bot. Granting it would let a leaked bot **edit/disable its own CI gate** — the single most dangerous over-grant. |
| Issues | Read | OMIT (default). Only needed to parse the Dependency Dashboard issue or **read PR conversation comments** (the `flux-local` sticky diff comment + Renovate's comment are issue comments). One-click add later if the shepherd needs to read them. |
| Administration | — | **NEVER** — would let the bot drop branch protection / disable its own gate. |
| Actions, Secrets, Environments, Deployments, Packages, Pages, Webhooks | — | **NEVER** — out of scope, pure attack surface. |
| All Account / Organization permissions | — | **NEVER** — single-repo, personal account. |

**Recommended grant: exactly the four GRANT rows.** Omit Workflows and Issues to
start; both are a one-click add later if the shepherd's duties expand.

## Registration — click by click

> On `github.com`, signed in as `thaynes43`.

1. **Settings → Developer settings** (bottom of left sidebar) **→ GitHub Apps →
   New GitHub App**.
2. **Name** = `haynes-ops-bot` (commits will attribute to `haynes-ops-bot[bot]`;
   names are globally unique, so confirm it's free). **Homepage URL** (required) =
   `https://github.com/thaynes43/haynes-ops`. Leave Description / Callback / Setup
   URL blank. Leave **"Request user authorization (OAuth)"** and **"Enable Device
   Flow"** UNCHECKED (server-to-server app, not user-facing OAuth).
3. **Webhook → UNCHECK "Active".** This removes the otherwise-required Webhook URL.
   The bot consumes no inbound events (it's driven by a schedule). Disabling the
   webhook does **not** affect its ability to trigger `flux-local`.
4. **Repository permissions** — set the four GRANT rows:
   Contents = **Read & write**, Pull requests = **Read & write**,
   Checks = **Read-only**. Metadata = Read-only auto-selects. Leave **everything
   else "No access"** — do not touch Administration, Actions, Workflows, Secrets,
   Issues.
5. **Account permissions** — leave all "No access".
6. **Where can this GitHub App be installed?** → **Only on this account** →
   **Create GitHub App**.
7. On the App's **General** page, record the **App ID** (small integer) and the
   **Client ID** (`Iv23…`).
8. **Private keys → Generate a private key.** A `.pem` downloads once (GitHub keeps
   only the public half — lose it and you must regenerate). Move it **straight into
   1Password**; don't leave it in Downloads / on disk.
9. **Install App → Install** next to `thaynes43` → **Only select repositories** →
   `thaynes43/haynes-ops` (NOT "All repositories") → confirm.
10. You land at `github.com/settings/installations/<INSTALLATION_ID>` — record that
    **Installation ID** (needed for the out-of-Actions JWT→token mint).

## Save to 1Password

One item (suggested name **`github-bot`**), in the vault your cluster's
`onepassword` ClusterSecretStore can read (so the in-cluster CronJob can sync it
later):

| Field | Value |
|---|---|
| `GITHUB_BOT_APP_ID` | the numeric App ID |
| `GITHUB_BOT_APP_CLIENT_ID` | the `Iv23…` Client ID |
| `GITHUB_BOT_APP_INSTALLATION_ID` | the numeric Installation ID |
| `GITHUB_BOT_APP_PRIVATE_KEY` | the full PEM incl. `BEGIN`/`END` lines — **the only long-lived secret** |

The PEM is the crown jewel. Never commit it, never SOPS it, never a plaintext
Actions secret, never on the laptop. Installation tokens (`ghs_…`) are minted from
it at runtime and expire in exactly 1 hour — they are never stored.

## Verified facts (no extra wiring needed)

- **No branch-protection change.** Live config: `strict=false`, single required
  check `Flux Local - Success`, no required reviews, `enforce_admins=false`, push
  restrictions disabled, no required signatures. An App-merged green PR satisfies
  the gate with just Contents + Pull requests write — no admin, no bypass, no
  allowlist entry.
- **No `flux-local.yaml` change.** Its sticky-comment step uses the job's own
  `GITHUB_TOKEN` (the diff job already has `pull-requests: write`). The App only
  supplies the agent's out-of-band push/PR/merge identity. Keep bot branches
  **in-repo** (not forks) so CI keeps comment-write + secrets.
- **Repo has zero Actions secrets today** — the only one you'd ever add is
  `OP_SERVICE_ACCOUNT_TOKEN`, and *only* if you later choose to mint the App token
  inside a GitHub Actions workflow (we don't need that for the cloud-routine /
  in-cluster runtimes).

## Token mint (for the later wiring phase — FYI now)

`gh` can't authenticate *as* an App; you mint an installation token, then hand it
to `gh`/`git`:

- **Out of Actions** (cloud routine, in-cluster CronJob): sign an RS256 JWT
  (`iss` = App/Client ID, `exp ≤ 10 min`) with the PEM → `POST
  /app/installations/<INSTALLATION_ID>/access_tokens` → receive a `ghs_…` token
  (1 h) → `export GH_TOKEN=ghs_…` and/or
  `git remote set-url origin https://x-access-token:ghs_…@github.com/thaynes43/haynes-ops.git`.
  Down-scope the mint to `contents:write, pull_requests:write, checks:read`. Re-mint
  before the 1-hour expiry on long runs.
- **Inside Actions** (if ever): `actions/create-github-app-token` (pin a SHA) —
  auto-discovers the installation; no Installation ID needed.

## Rollback note (read-only Omni SA stays sufficient)

Rollback = git revert / chart re-pin → push → Flux's in-cluster controllers apply
it (via the push Receiver or the poll interval). The bot needs **no** kubectl
write. Two caveats: the agent must **not** call `flux reconcile --with-source`
(it annotates the Kustomization = a write the Reader role is denied — rely on the
Receiver/interval instead), and reverts that hit immutable fields / wedged
HelmReleases / stuck finalizers won't converge from git alone and are a
break-glass escalation.

## Security posture

- Scope to exactly {Contents:write, Pull requests:write, Checks:read}, single-repo
  install, no account perms, no webhook. The most dangerous misconfig is granting
  Workflows:write or Administration, or installing org-wide.
- Keep the agent on the **PR path** — even a leaked token can't push to `main`
  without a green `flux-local`. Never add the App to a push-restriction/bypass list.
- **CI-gate gap to respect:** `Flux Local - Success` passes on *skipped* jobs, and
  test/diff only run on `kubernetes/**` changes — so a bot PR touching only
  `.github/**` or repo root earns a trivially-green check. Constrain the agent to
  `kubernetes/**` edits; this is a second reason to never grant Workflows:write.
- **Rotation:** rotate the PEM quarterly / on suspicion. The App supports multiple
  active keys — generate new, update the one 1Password item, then delete the old.
- **Leak response:** delete the private key in App settings (kills minting) and/or
  uninstall the App (invalidates all outstanding tokens); any minted `ghs_` dies
  within 1 h regardless.
