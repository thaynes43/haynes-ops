# .agents/sagas — multi-plan initiatives

A **saga** is a long-running initiative too big for one runbook or one PR: a vision doc
plus an enumerated **plan backlog** that agents (and humans) execute over many sessions.

Conventions:

- One directory per saga: `.agents/sagas/<saga-name>/`.
- `README.md` — the saga doc: vision, architecture, hard trade-offs, decision log, and
  a backlog index. This is the first thing an agent should read when working the saga.
- `backlog/NN-<slug>.md` — individual plans, numbered in rough execution order. Some
  can run in parallel; each plan declares its dependencies. A plan is the unit of work
  an agent picks up in a session.
- Decisions made during discussion get recorded in the saga README's **Decision log**
  (with date), so later sessions don't re-litigate them.
- When a plan completes, mark it `Status: done` in its header and note the completing
  commit/PR. Don't delete it — the backlog doubles as history.

Active sagas:

- [dev-env](dev-env/README.md) — 24/7 in-cluster agent development environment
  (Claude Code / Codex / code-server workhorse pod; Shepherd becomes a dispatcher).
- [haynesnetwork-ha](haynesnetwork-ha/README.md) — replicas + node-failure resilience for the
  haynesnetwork.com front page, uptime as a measured metric (Gatus SLI), and a front-page
  uptime badge.
