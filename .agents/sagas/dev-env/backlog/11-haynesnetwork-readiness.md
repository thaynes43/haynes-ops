# 11 — haynesnetwork build/run/test readiness in the pod

**Status:** verified except e2e (2026-07-13). Tom's gate before letting agents loose on
haynesnetwork backlog items: *"make sure the environment is equipped to build / run /
test haynesnetwork."*

## Verified live in-pod (image 0.4.0)

| Step | Result |
|---|---|
| `pnpm install --frozen-lockfile` | ✅ 5.3s (needed pnpm **11.9.0** — see below) |
| `pnpm build` (`pnpm -r build`, Next.js app + packages) | ✅ |
| `pnpm typecheck` | ✅ all workspaces |
| `pnpm test` (vitest × **embedded Postgres 16**) | ✅ **1292 tests** passed (db 76, domain 474, auth 70, sync 75, api 340, web 257) |
| `pnpm e2e` (Playwright) | ⏳ pending — browser download needed the CNP fix in PR #2048 |

## What it took (each found by running it, not guessing)

1. **pnpm 11.9.0** — the repo pins `packageManager: pnpm@11.9.0` + `engines.pnpm >=11`;
   pnpm 10 *hard-refuses* to install. Image ARG bumped (PR #2045).
2. **Playwright browsers must live on the PVC.** The repo pins `@playwright/test`
   1.61.x, which wants a *different* chromium revision than the MCP's — and it cannot
   install one into an image path under `readOnlyRootFilesystem`.
   `PLAYWRIGHT_BROWSERS_PATH=~/.cache/ms-playwright`, seeded on boot from an image
   staging dir so the MCP still works with zero downloads (PR #2045).
3. **`cdn.playwright.dev` REDIRECTS the browser zip to `storage.googleapis.com`** —
   the download died `EAI_AGAIN` until both were allowlisted (PR #2048).
4. **8Gi OOMKilled the test suite.** Parallel vitest × embedded Postgres across the
   monorepo blew the limit; the OOMKill also takes the **tmux server** with it, which
   would kill every in-flight agent session. Raised to 24Gi (nodes carry ~122Gi;
   a limit is a ceiling, not a reservation) — PR #2047.
5. **embedded-postgres needs no extra egress** — the binaries ship as npm packages, so
   the existing npm allowlist covers it. (Nice: no Docker, no external DB, as the
   repo's hard rule #1 requires.)

## Remaining

- Re-run `npx playwright install chromium` + `pnpm e2e` once PR #2048's CNP applies
  (Flux picks it up on its 30m interval; no manual step needed).
- `pnpm dev` (run the app) not yet exercised — it needs `.env.local` (OIDC creds, DB
  URL). Decide whether agents get a scratch Postgres + a dev env file, or whether
  "run" means `pnpm build` + tests only. **Ask Tom** before wiring app secrets into
  the dev pod (it would put real Authentik/DB creds next to yolo agents).

## Incident worth remembering (2026-07-13)

Mid-verification, the **Mac's Omni OIDC token expired** and the browser re-auth was
impossible (Tom away) — the *exact* pain this saga exists to eliminate. The in-cluster
pod was unaffected. The headless fallback is the Omni service-account key
(`.agents/runbooks/omni-service-account.md`), but reading it from 1Password needs the
`op` CLI **unlocked** — which also needs Tom. Worth closing that loop: a long-lived SA
kubeconfig on disk (key sourced at use time) would make the workstation independent of
both the browser flow and an unlocked vault.
