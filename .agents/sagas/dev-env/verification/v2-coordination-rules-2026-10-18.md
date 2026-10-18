# V2 trusted coordination and instruction chronology — 2026-10-18

Saga85 / Epic91 / existing Story94 owns this bounded defect (the canonical
OpenProject record includes the owner's clarification). One existing Codex chat
owns implementation in `agent/v2-coordination-rules-1018`, based on
haynes-ops `38b4f88c`. Scope is the v2 rule input and offline contract checks;
no credentials, access, workload, flag, image, v1 source or live session changes.
Target: one 45-minute source/test/PR checkpoint, at most one driver and one
read-only reviewer, no provider actors or paid API calls. Stop at reviewed delivery,
a real dependency, explicit stop or the bounded checkpoint; preserve PR/evidence.

## Defect and source boundary

An old direct Standby was attributed to a newer human message without a verified
ID/time. The owner then explicitly corrected that attribution and directed
continuation. This is an instruction-interpretation error, not evidence of a
platform replay. A blanket peer-message rule from v1 was also applied without
reconciling the existing trusted Meatball coordination exception.

Authoritative v2 input:
`kubernetes/main/apps/dev-env-system/session-config/app/config/claude/CLAUDE.md`.
The shared policy block already permits verified supported coordinator directions;
its bytes remain identical to v1. V2 has no blanket peer-refusal clause. The new
section clarifies application in v2 rather than changing the shared hierarchy,
provider headers or the protected v1 input. It requires verified designated
coordinator provenance and actual source chronology, preserves newer direct user
stops, and leaves safety, access, scope and action-specific approval controls intact.
The five decision cases are explicit instruction examples, not an executable
permission engine or a new trust service.

`session-config/app/kustomization.yaml` puts the common text into
`dev-env-config-claude`. Agentd's existing `renderClaudeMD` links that source for
Claude; `renderCodexAgentsMD` appends the same text to the Codex header. With
retained common rules enabled, `internal/agentd/common_rules.go` in dev-env
instead projects the original retained source for both providers. Existing retained
sessions do not acquire a new policy merely because Git changed. No renderer or
retained digest is bypassed or patched here.

V1's authoritative input is separately mounted from
`apps/dev/dev-env/app/resources/config/claude/CLAUDE.md`; dev-init concatenates it
with its Codex header. Editing it would restart the protected pod. This change
edits neither that input nor any live generated `~/.claude`/`~/.codex` file.
V2 session-config has no Reloader/restart hook; its existing ConfigMap names and
Flux Kustomization scope remain unchanged. No v1 companion PR is required for an
unchanged shared policy block.

## Acceptance evidence

- Five standard-library offline tests pass: shared block equality, same common
  input for both provider paths, verified-coordinator handling, actual chronology
  and newer direct stops, forged-source refusal and unchanged approval/access limits.
- Real `kubectl kustomize` rendered exactly the amended common text into the
  existing v2 ConfigMap. Output contains four existing ConfigMaps in `dev-agents`,
  no v1 workload or restart annotation. No cluster write was performed.
- `git diff --check` passes. A path-filtered CI fixture job guards the instruction
  contract; normal Flux and diff-scope required checks still apply.
- Native independent review and PR/current-head CI evidence are recorded on the PR.
  Source checks preserve the contract and generation inputs; they do not prove a
  model follows the rules in a live conversation. No runtime provider test is run.

## Delivery limits and next safe step

This record describes source/offline evidence. Confirm current-head required CI,
resolve available review findings, then squash merge within the authorized v2-only
scope. Record the actual PR, commit and status in Story94 and the parent checkpoint.
A merged source/config delivery does not prove running retained sessions loaded it
or accept native/phone parity. Any later model-compliance probe must use an explicitly
bounded supported Luna/Haiku actor and respect all existing runtime gates.
Release369, H7, guarded-access proof, live cohort, drafts and v1 retirement remain held.
