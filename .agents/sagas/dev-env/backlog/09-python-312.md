# 09 — Python ≥3.12 in the dev-env image (workstation venv parity)

**Status:** VOID (2026-09-22) — the premise is gone, no action needed
**Depends on:** none
**Recorded:** 2026-07-24 (renovate-saga audit follow-up)

## Resolution — 2026-09-22

Closed as moot, not fixed. The repo-root `requirements.txt` and the
`task workstation:venv` target were **deleted** in the retire-edge pass: makejinja
was their only consumer, and the cluster-template bootstrap targets that invoked it
went in the 2026-09-22 taskfile audit (#3097). There is no repo venv left to build
in or out of the pod, so the pod's Python version no longer has a parity
requirement to meet. Do not bump the image's Python for this reason — if a future
in-pod workflow needs ≥3.12, record that need on its own merits.

The original entry is kept below for context.

## Problem (historical)

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
