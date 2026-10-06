# ADR-001: dev-env v2 lives in thaynes43/dev-env; v1 stays here until cutover

- **Status:** Accepted (Tom, 2026-10-05)
- **Date:** 2026-10-05
- **Deciders:** Tom Haynes

## Context and problem statement

dev-env v1 is one pod, built and deployed entirely from this repo: the image from
`scripts/dev-env/Dockerfile`, the manifests and every script under
`kubernetes/main/apps/dev/dev-env/`.

dev-env v2 is a distributed redesign: one pod per agent session, run by an operator
with an API, a credential keeper, an in-pod supervisor, an `agent-run` CLI that runs
anywhere, and the agent image. That is several pieces of software with their own
tests, releases and images. This repo is a public GitOps repo for the cluster, not a
home for application code.

## Decision outcome

**v2's saga and code live in a new private repository, thaynes43/dev-env** (Tom,
2026-10-05). It keeps the image name `ghcr.io/thaynes43/dev-env`. The v2 design is
its saga: `.agents/sagas/distributed-dev-env/` (ADR-001 and DESIGN-001 there, status
Proposed).

**v1 stays here and keeps running, maintained as it is today, until Tom approves the
cutover** (v2 phase 5). v2 work never edits `kubernetes/main/apps/dev/dev-env/app/resources/**`,
because that restarts the v1 pod and every session in it.

**What stays in haynes-ops, as GitOps:**

- every manifest: the v1 app, and v2's namespaces, CRDs, operator and keeper
  HelmReleases, RBAC, CiliumNetworkPolicies, ExternalSecrets, quota, LimitRange and
  PriorityClass;
- the config agent pods read: `CLAUDE.md`, `mcp.json`, Codex `config.toml` and
  `requirements.toml`, subagent definitions, and v2's session templates (image
  digest, sizes, profiles), so every change that reaches an agent shows up in a PR
  diff here;
- dev-env-ops, the Kyverno image policy, and Renovate's config for the deployed tags.

**What moves to thaynes43/dev-env, as code:** the image build (Dockerfile and
workflow), `agent-run`, the boot and config scripts (`dev-init.sh`, `post-ready.sh`
and friends, which become the in-pod supervisor), `declare-activity`, the auth and
token scripts (which become the keeper), and the `pve` and `hw-ssh` helpers.

**Migration order** (detail in the v2 design, section 10):

1. v2 builds its own `2.x` image line from a copy of the Dockerfile. The v1
   Dockerfile and `dev-env-build.yml` stay here, frozen except for tool and security
   bumps, building v1's `0.6.x`.
2. Renovate holds the v1 HelmRelease below `2.0.0`, and Kyverno's
   `verify-thaynes43-images` gains the `thaynes43/dev-env` workflow identity for
   `dev-env*` images.
3. v2's manifests land here as new apps beside v1.
4. At cutover, the v1 app, its Dockerfile, its build workflow and its Renovate
   carve-outs are removed here.

### Consequences

| ID | Consequence |
|----|-------------|
| C-01 | Good: v2 code gets its own CI, review, releases and images, without growing this repo's application code. |
| C-02 | Good: deploys stay GitOps; nothing about how the cluster changes moves out of this repo. |
| C-03 | Bad: until cutover, two repos build images named `ghcr.io/thaynes43/dev-env` (tag lines `0.6.x` here, `2.x` there). The Renovate hold and the tag lines keep them apart. |
| C-04 | Bad: a v2 change is two PRs, code there and a pin bump here, the same pattern as appdaemon (hass-sandbox → this repo). |
| C-05 | Neutral: backlog 08 (multi-instance flavors) and 09 (LAN control plane) are folded into the v2 design and are not pursued here. |

## More information

- v2 saga: <https://github.com/thaynes43/dev-env/tree/main/.agents/sagas/distributed-dev-env>
  (private).
- This saga's decision log: [README](../README.md#decision-log), row 9.
