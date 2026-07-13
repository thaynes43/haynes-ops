# 08 — multi-instance flavors (claude / codex / gemini pods)

**Status:** backlog — blocked on Decision log #4 (may be mooted if one pod suffices)
**Depends on:** 06
**Parallel with:** 07

## Goal

Optionally run separate dev-env pods per agent ecosystem — e.g. `dev-env-claude`,
`dev-env-codex`, `dev-env-gemini` — when isolation (auth blast radius, rate-limit
pools, resource contention, differing MCP sets) justifies the overhead.

## Design sketch

- One image serves all flavors (all CLIs installed); flavor = which auth secrets +
  configs mount and which egress rules apply. Avoid per-flavor images unless size
  demands it.
- Repo shape: `dev-env/` splits into per-instance `ks.yaml`s sharing one `app/` via
  `postBuild.substitute` (APP, FLAVOR, PVC size), the same way shared components are
  reused elsewhere — or per-instance thin dirs if values diverge beyond what
  substitution handles cleanly. Decide when concrete.
- Per-flavor CNP deltas: a codex-only pod doesn't need anthropic egress and vice
  versa — tighter than the kitchen-sink pod's union.
- Gemini CLI joins here: image ARG, `NO_BROWSER` paste-URL auth ceremony into its PVC
  state dir, config file into the GitOps config set (backlog 03 pattern).
- `agent-run` grows `--host <flavor>` (or the dispatch API routes by agent type) so
  the Shepherd can target a flavor.

## Default recommendation

Start with ONE kitchen-sink pod (PoC through backlog 07). Split only when a concrete
pain shows up: rate-limit crosstalk between dispatched and interactive work, one
agent's crash-looping tooling starving another, or wanting a tighter CNP per flavor.
The manifests from 02 should be written substitution-ready so the split is mechanical.

## Acceptance (if/when executed)

- Two flavor pods run concurrently with disjoint auth secrets (Claude token absent
  from the codex pod's env/PVC and vice versa).
- Dispatch routes to the right flavor; `agent-run --list` per pod shows only its own.
