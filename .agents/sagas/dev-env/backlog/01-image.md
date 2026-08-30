# 01 — dev-env container image + signed build workflow

**Status:** done (PR #2031, merged 2026-07-13; image `ghcr.io/thaynes43/dev-env:0.1.0@sha256:95e9919f3f10724b31cf4586227ae5aefd5fc9adbc7c49675a1c290eeca296e8`, cosign-signed, Kyverno policy live in-cluster)
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

## Post-mortem 2026-08-29 — the ARG pins were never Renovate-managed

The last acceptance box above ("Renovate opens PRs for tool ARG bumps — verify one
datasource comment resolves") was **ticked without being checked, and it was false
from the first build (2026-07-13) until 2026-08-29.** Renovate's built-in `dockerfile` manager reads FROM/image
refs only; an `ARG FOO_VERSION=` pin needs a **regex custom manager**, and the two
custom managers this repo had were both scoped `managerFilePatterns:
kubernetes/**/*.yaml`. Dockerfiles were never scanned. Proof, from the Dependency
Dashboard: `scripts/dev-env/Dockerfile (2)` — `docker/dockerfile 1` and `node
24-slim`, the two FROM lines. The 20 `# renovate:` annotations under them were
decoration.

So every baked CLI froze at whatever a human last typed. `claude-code` stuck at
2.1.217 from 2026-07-22 (35 releases); `scripts/upgrade-shepherd/Dockerfile` is still
back on 2.1.197.

**Why it wasn't merely cosmetic.** claude resolves the `opus`/`sonnet`/`haiku`/`fable`
model **aliases client-side**, against a table compiled into the binary. A pinned CLI
therefore pins the *alias meaning* too. On 2.1.217, `--model opus` served
`claude-opus-4-8` while `--model claude-opus-5` served `claude-opus-5` on the same
binary — nothing in the banner, status line, or logs flags the downgrade. Two consumers
were silently on the old tier: `agent-run`'s model picker (whose comment asserted
aliases "track new releases on their own") and the dev-env-ops **cigar-catalog curation
lane**, which had been moved onto the `opus` alias hours earlier in #2663 specifically
to get "latest Opus with no bump chore".

**Fixed by (this PR):**

1. `.renovate/customManagers.json5` — a Dockerfile ARG manager. Verified 25/25
   annotated pins across `scripts/*/Dockerfile` now match. It deliberately does *not*
   anchor on `# renovate:` at line start (the omnictl pin trails its comment mid-line)
   and tolerates comment lines between the annotation and the ARG (pnpm has two).
2. `.renovate/groups.json5` — one grouped, never-auto-merged PR per image, so the
   newly-visible backlog arrives reviewable instead of as ~25 singles, and because a
   baked toolchain ships as one image anyway.
3. `scripts/dev-env/Dockerfile` — `CLAUDE_CODE_VERSION` 2.1.217 → 2.1.251 (the manual
   catch-up for the missed window).
4. `agent-run.sh` — the model picker offers **pinned IDs, not aliases**, so a stale
   image can never quietly downgrade a session again.

**Standing rule:** an ARG bump is a **two-step**, and step 2 is what deploys. Merging
the bump builds + signs a new image tag; **re-pinning `tag:`/digest in the consuming
helmrelease is what rolls pods.** Nothing reaches a running pod on step 1 alone.

**Verify the manager actually works** (don't tick this box on faith): after the next
Renovate run, the Dependency Dashboard's `scripts/dev-env/Dockerfile` entry must list
~20 deps, not 2.
