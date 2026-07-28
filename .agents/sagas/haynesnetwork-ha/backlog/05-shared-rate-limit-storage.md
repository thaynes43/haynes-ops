# 05 — Rate-limit storage: in-memory → database

**Status:** planned
**Repo:** haynesnetwork (`packages/auth/src/config.ts`)
**Depends on:** nothing (correct before or after 02; window where limits are ×2 is accepted)
**Parallel with:** 01, 03, 06

## Goal

Better Auth rate limiting stays correct when replicas multiply.

## Why

The `rateLimit` block sets window/max/customRules but no `storage`, so better-auth defaults to
in-memory buckets — each replica keeps its own, multiplying effective limits and making
throttling inconsistent per client (fail-open). Design doc 002 and backlog-recon O-5 flagged
exactly this for the day the app scales past one replica. That day is plan 02.

## Approach (high level)

Point better-auth's rate limiter at shared storage (`storage: 'database'` with its model in
`@hnet/db` schema + migration, per better-auth docs current at implementation time). Verify the
auth flows' limits behave identically under the embedded-Postgres test harness, and that the
added per-request write is acceptable on the hot paths (better-auth only touches it on
rate-limited routes).

## Acceptance

- Two app processes sharing one DB enforce ONE combined limit in a test.
- No regression in sign-in flow latency worth caring about (spot-check, not a benchmark).
