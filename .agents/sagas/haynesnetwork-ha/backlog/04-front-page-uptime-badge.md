# 04 — Front-page uptime badge (app dashboard)

**Status:** planned
**Repo:** haynesnetwork (mints a numbered plan in `.agents/plans/`; docs-first — likely a small
ADR/design note since it adds a dashboard surface + a new upstream read)
**Depends on:** 03 (the Gatus endpoint must exist and have history)
**Parallel with:** —

## Goal

The front page wears its own uptime: a small, token-themed badge/tile on the dashboard showing
current status + uptime percentage (e.g. 30d), sourced from the real external SLI.

## Why

Uptime is the owner's headline metric for this app, and the homepage is sparse — this is the first
deliberate new element (further homepage content is the future separate saga, decision 6).

## Approach (high level)

Server-side only: the app reads the in-cluster Gatus API (uptime + current status for the apex
endpoint key) through a small read client, surfaced via tRPC with a short cache; a new `@hnet/ui`
badge component renders it with `--color-*` tokens (no raw hex, hard rule 2), no layout reflow on
state change (ADR-015). When Gatus is unreachable the badge renders an honest "unmeasured" state —
never fake green. No public exposure of gatus; no iframe/third-party badge (decision 4).

UX design is the Fable lane per the coordinator division of labor; implementation dispatches like
any app plan. Check the frontend namespace's egress posture to `observability` (netpol) during
implementation — an allow rule may be the cross-repo touch.

## Acceptance

- Dashboard shows the badge for all signed-in users (read-only, no grant needed — it is public
  pride, not admin telemetry); mobile-fit per the resize matrix.
- Badge reflects a real Gatus outage window (verified during plan 07's drill).
- Hex-lint, reorient rule, and e2e stay green (stub Gatus in the dev:local harness).
