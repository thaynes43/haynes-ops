# 01 — dev-env container image + signed build workflow

**Status:** backlog
**Depends on:** nothing (first mover)
**Parallel with:** 02 can be authored against the expected image name while this bakes

## Goal

`ghcr.io/thaynes43/dev-env` — a kitchen-sink development image the pod (and later,
dispatched Jobs) runs. Modeled on `scripts/upgrade-shepherd/Dockerfile` but fatter.

## Contents

- Base: Debian-family (glibc — kubectl/claude are glibc-linked). Candidate bases:
  `node:24-slim` + install code-server, or `codercom/code-server` + install node.
  Decide during implementation; node base + code-server standalone install is closest
  to the shepherd pattern.
- Agent CLIs: `@anthropic-ai/claude-code`, `@openai/codex` (Renovate-pinned ARGs).
  Gemini CLI deferred to backlog 08.
- Ops: kubectl, helm, flux, kustomize, sops, age, gh, jq, yq, opentofu, go-task,
  restic (volsync debugging), git, curl, openssl — all checksum-verified where the
  upstream publishes checksums, Renovate-pinned ARG versions with datasource comments.
- SDKs (Decision #8 — must cover haynesnetwork, haynes-ops, hass-sandbox out of the
  gate): Node 24 from base + tsx/pnpm (haynesnetwork is TypeScript), Python 3 +
  uv/venv/pytest (hass-sandbox, makejinja). Go/Rust/.NET deferred until a concrete
  need. Keep the list additive — image rebuilds are cheap, pod rolls are not.
- tmux + a `.tmux.conf` sane default (dispatched agents and /remote-control sessions
  live in tmux).
- Non-root `dev` user, uid 1000, `HOME=/home/dev` (the PVC mountpoint — see 02).

## Build workflow

Copy `.github/workflows/upgrade-shepherd-build.yml`:

- Trigger on `scripts/dev-env/**` + the workflow file, push to main + dispatch.
- Build context = repo root, `.dockerignore` already excludes secrets/.git.
- Tag `MAJOR.MINOR.PATCH` + `latest`, push, **cosign keyless sign by digest**.
- Add `ghcr.io/thaynes43/dev-env*` to `verify-thaynes43-images` Kyverno policy
  (Audit first, Enforce once the pod runs a signed digest — same ramp the shepherd
  image followed).

## Acceptance

- Image pulls, `claude --version` / `codex --version` / `kubectl version --client` /
  `code-server --version` all work as uid 1000 with a mounted empty HOME.
- Cosign verification passes against the workflow OIDC identity.
- Renovate opens PRs for tool ARG bumps (verify one datasource comment resolves).
