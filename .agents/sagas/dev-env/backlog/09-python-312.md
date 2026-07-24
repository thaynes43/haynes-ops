# 09 — Python ≥3.12 in the dev-env image (workstation venv parity)

**Status:** backlog — latent, nothing broken in-pod today
**Depends on:** none
**Recorded:** 2026-07-24 (renovate-saga audit follow-up)

## Problem

The pod ships Python 3.11.2, but `makejinja==2.8.2` in the repo-root
`requirements.txt` requires Python ≥3.12, so `task workstation:venv` (repo
templating tooling) cannot build its venv inside the dev-env pod.

This is latent, not broken: the dev-env image never installs
`requirements.txt` (confirmed 2026-07-24 — the Dockerfile has no COPY/install
of it), and no in-pod workflow currently needs makejinja. It only bites if an
agent is ever asked to run the makejinja templating from inside the pod.

## Fix sketch

Bump the image's Python to ≥3.12 (base image bump or an added toolchain).
Remember the rollout constraint from the Renovate carve-out: the dev-env image
is **manual merge only** — a pod roll kills every in-flight agent session, so
whoever merges picks the moment and drains/warns sessions first.

## Alternatives considered

- Pinning `makejinja` back to a 3.11-compatible release fights Renovate and
  penalizes real workstations that already have 3.12 — rejected.
- Environment markers in `requirements.txt` split the lockstep between
  workstation and pod — rejected as churn for a latent issue.
