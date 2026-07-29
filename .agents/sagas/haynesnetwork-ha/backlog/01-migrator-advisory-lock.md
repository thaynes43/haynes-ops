# 01 — Migrator advisory lock (serialize concurrent migrate init-containers)

**Status:** done — haynesnetwork PLAN-062 / [PR #499](https://github.com/thaynes43/haynesnetwork/pull/499),
released v0.90.4 (deployed with the v0.90.5 roll). Session-level `pg_advisory_lock` on key
0x686e6574 ("hnet") around the drizzle migrator; concurrency test proven a real guard (lock
removed → the `CREATE SCHEMA drizzle` race reproduces); deploy runbook amended.
**Repo:** haynesnetwork (mints a numbered plan in `.agents/plans/` per that repo's process)
**Depends on:** nothing
**Parallel with:** 03, 05, 06

## Goal

Make the per-pod `migrate` init-container safe under ANY multi-pod scheduling event, so replica
count is purely a scheduling decision.

## Why

`packages/db/src/migrate.ts` runs drizzle's node-postgres migrator with no cross-process lock, and
the 71 migrations are not idempotent (bare `CREATE TABLE`). Rolling updates serialize via
maxSurge=1 and are safe, but a cold multi-replica start or a simultaneous reschedule after node
loss races two migrators: the loser exits `Init:Error` and retries into success (hash-guarded, no
corruption) — a self-healing crashloop we should not ship as a design.

## Approach (high level)

Wrap the migrate call in `pg_advisory_lock` on a fixed app-scoped key (acquire → migrate →
release; the lock dies with the session on crash). Losers block until the winner finishes, then
see the migrations hash table already satisfied and no-op. One small change in
`packages/db/src/migrate.ts` + a concurrency test against embedded Postgres (two racing migrators,
both exit 0, schema applied once).

## Acceptance

- Two concurrent `migrate.ts` runs against a fresh DB: both exit 0, no `relation already exists`.
- Documented in the migrator script comment why the lock exists (this saga, plan 01).
