# 09 — LAN control plane: CLI / API / UI for the dev-env

**Status:** backlog — deliberately last; PoC interaction is exec + code-server + /remote-control
**Depends on:** 07 (the dispatch API it generalizes)

## Goal

Make starting/watching/steering agents pleasant from any device on the LAN without
`kubectl exec`: a small API (already born as the dispatch API in backlog 07) plus a
thin client — either a standalone CLI on the Mac, a lightweight web UI, or a panel in
haynesnetwork (`kubernetes/main/apps/frontend/haynesnetwork`).

## Candidate surface

- `POST /tasks` (repo, agent, prompt, yolo?, branch) — from 07.
- `GET /tasks`, `GET /tasks/<id>` (state, tmux session, log tail, worktree, branch).
- `POST /tasks/<id>/reap`.
- `GET /auth` — the auth-check status (backlog 04) surfaced as data, so the UI shows
  "Codex auth expires in N days" instead of only paging on failure.
- Auth: Authentik-fronted like everything else; NOT exposed beyond LAN/tunnel.

## Options for the human surface (pick later)

1. **haynesnetwork panel** — it already has ingress, Authentik, a database, and is
   the "home portal"; a dev-env card fits its purpose.
2. Standalone minimal web UI in the dev-env pod itself (htmx-grade, no build step).
3. Mac-side CLI (`devenv dispatch ...`) hitting the API through the ingress —
   cheapest, no UI work, scriptable.

Phone-driven steering of a RUNNING agent stays `/remote-control` (claude.ai) — this
control plane handles start/observe/stop, not mid-task conversation.

## Non-goals

- Multi-user auth/tenancy (it's Tom).
- Replacing tmux/code-server for deep interactive work.
- Building this before the dispatch API exists and has proven its shape.

## Acceptance

- From a phone on the LAN: start a yolo task in a repo, watch its log tail, reap it.
- Auth freshness visible at a glance.
