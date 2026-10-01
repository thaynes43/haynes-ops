# New repo setup: Claude Code PR review

Standing rule (Tom, 2026-09-30): **every repo gets the advisory Claude Code PR
reviewer and the `@claude` handler.** Do this when you create a repo, and when you
work in one that lacks `.github/workflows/claude-code-review.yml`. It applies to
Claude Code and Codex agents alike. No other new-repo checklist exists in haynes-ops;
add further steps here rather than starting a second document.

Reference implementations: `thaynes43/hass-sandbox` and this repo's own
`.github/workflows/claude-code-review.yml` + `claude.yml`; the 2026-09-30 rollout PRs
(`agent/claude-review` in libretto, sigoalumni-org, haynesnetwork, ...) are the
final adapted shape the template below is built from.

## 1. Add both workflows

Copy the templates and adapt them:

```bash
mkdir -p .github/workflows
cp ~/repos/haynes-ops/.agents/templates/github/claude-code-review.yml .github/workflows/
cp ~/repos/haynes-ops/.agents/templates/github/claude.yml            .github/workflows/
grep -n '<<' .github/workflows/claude*.yml    # every marker must be resolved
```

- **Pinning** (`<<PINNING>>`): match the repo's existing action-pin style, a tag
  (`actions/checkout@v5`) or a full SHA with a `# vX.Y.Z` comment. Renovate/dependabot
  then keeps it current. `anthropics/claude-code-action` stays on `@v1` unless the repo
  pins everything to SHAs.
- **Model**: `--model claude-opus-5-5`, a full id and never an alias (aliases resolve
  client-side and lag launches). It follows the pod's automated-surface model policy;
  when the policy bumps Opus, every repo's two workflows need the same bump.
- **Fixed on purpose**, do not loosen: plain `pull_request` (never `pull_request_target`),
  skip drafts, forks, dependabot and renovate; the secret-presence gate step, so the job
  finishes green when the secret is missing; `@claude` limited to write-access actors
  (no `allowed_non_write_users`); `allowed_bots: "haynes-dev-bot"` so agent PRs get
  reviewed. If the repo triggers on specific branches, add `branches:` to the review
  workflow as haynes-ops does.
- **Advisory, never required.** Do not add the review job to branch protection or a
  required-checks list; do not rename it to look like a gate.

## 2. Write the repo-specific review prompt

The `prompt:` block is the part that makes the review worth reading. Write it from the
repo's own `CLAUDE.md` / `AGENTS.md` and rule files, not from a generic checklist:

1. One or two lines on what the repo is and its stack; its directory layout.
2. **CRITICAL** (blocks merge): secrets/PII, data-loss and auth rules, the repo's
   hard "never do X" rules, workflow changes that weaken security.
3. **HIGH**: conventions the repo enforces (version bumps, README or doc updates,
   test coverage, migration rules).
4. **MEDIUM**: lower-stakes items. **Ignore entirely**: formatting and anything a
   linter already enforces.
5. Keep Step 1 (read the diff and context) and Step 3 (inline comments + one summary
   comment, "No findings." when clean) from the template unchanged.

## 3. Tell agents how to treat the review

Add a short section to the repo's `AGENTS.md` / `CLAUDE.md` (or README if neither
exists), modelled on libretto's:

> ## Automated PR review (agents read this)
> Every non-draft PR gets an advisory review from Claude Code
> (`.github/workflows/claude-code-review.yml`); `@claude` mentions are handled by
> `claude.yml`. The review is **advisory**: it is not a required check. Read its
> findings before merging. Fix each one, or answer it on the PR with a concrete reason
> it is wrong; never "merging anyway". Both workflows need the Claude GitHub App on the
> repo and the `CLAUDE_CODE_OAUTH_TOKEN` repo secret, otherwise they skip green and
> review nothing.

## 4. Set the secret

The Claude GitHub App is installed on all of Tom's repos. The secret is the long-lived
OAuth token already in this pod's PID 1 environment (ExternalSecret from 1Password
`dev-env`). Set it without ever printing it:

```bash
tr '\0' '\n' < /proc/1/environ | sed -n 's/^CLAUDE_CODE_OAUTH_TOKEN=//p' \
  | gh secret set CLAUDE_CODE_OAUTH_TOKEN -R thaynes43/<repo>
gh secret list -R thaynes43/<repo> | grep CLAUDE_CODE_OAUTH_TOKEN   # name only, no value
```

Needs the haynes-dev-bot GitHub App's repository permission "Secrets: Read and write".
A `403` means that permission is missing: ask Tom (AskUserQuestion), do not look for
another route. Never echo, log, commit or paste the token; it is the same credential
headless sessions use.

## 5. Verify on the first PR

Merge the workflow PR first, then open any small PR (the action refuses to run a
workflow file that differs from the default branch's copy, so the PR that *adds* the
workflow is not a valid test).

```bash
gh pr checks <n> -R thaynes43/<repo>                 # "Claude advisory review" is pass, not skipped
gh run list -R thaynes43/<repo> --workflow "Claude Code Review" --limit 3
gh pr view <n> -R thaynes43/<repo> --comments | head -40   # sticky summary + inline findings
```

"Pass" with a `::notice:: ... not set` annotation means the secret is missing: step 4.
A failed run that says the action could not exchange the OIDC token means the Claude
App lacks access to the repo. The review has run only when a summary comment (or
"No findings.") from the Claude app is on the PR. Also check that `@claude` is quiet
on that PR (it only fires on a mention).
