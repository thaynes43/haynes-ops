# Task claims: protected coordination prerequisites

The first #175 source contract is DESIGN-010 in thaynes43/dev-env. Immutable
explicit project/repo/task identity selects one atomic owner, with original
Session UID/actor/catalog/rules preserved across retries and stopped owners.
No expiry, release, transfer, prompt/body export or automatic dispatch gate is
implemented. Source review and tests do not establish installed contention.

The prerequisite candidate adds its exact generated TaskClaim CRD to the
existing prune-disabled inventory, plus a namespaced operator-only Role/Binding.
Only get/create/list on taskclaims in coordination.dev-env.haynesops.com is
granted. Existing worker grants cover the old group, not this protected group.
All existing CRDs/RBAC and the operator's CRD dependency remain unchanged.
Schema SHA256:
`39b673e0ef4a7da40d301faf19888bb0992dfa75a47de1b6312ccb126a9ec96c`.

Four narrow renders, semantic preservation checks, source-schema equality and
Role/Binding schema checks passed. The source has prior real-API schema evidence.
Required hosted Flux CI and native independent review remain gates. The optional
local remote-schema lookup failed and its route is stopped, with no workaround
or new allowlist/permission. Do not report a local full-CRD schema pass.

The combined candidate binds signed source #187, merge
`d95e8fbe7b3e1c07eed75959819cd35f90a155c4`, publication `38159542551`, to digest
`56fe9e27fa4d3ba73a105cf069aa3655559613e463193a643d40fc3bad2bc1e6`.
The workflow verified the exact image's keyless signature. The only flag delta
is claims=true and an explicit dev-env metadata read scope. Existing private-project
admission stays false and retention stays enabled. Preserve all current sessions,
terminal fixtures and original ledgers. A disposable two-parent contention proof,
durable offline inbox/read acknowledgements and budget/handoff integration remain
parent acceptance. No provider turn or MVP claim follows from prerequisites.

## Q-25: Future local schema validation route

OPEN, asked once through the native question tool on 2026-10-11 UTC. The optional
schema-host lookup gave up after three internal attempts; exact unique network
request count and invocation timestamps were not captured. Combined output says
DNS server misbehaving; Cilium denial is not established. Private receipts retain
the commands, output hashes and bounded observation times. No further local
schema fetch is permitted in this stopped route.

The question offers required hosted CI plus existing real-API schema evidence
(recommended), or a separate DNS investigation for future checks. No owner answer
or phone delivery is inferred. Existing required CI is independently authorized;
the question does not add a merge-approval gate or authorize a fourth retry.
Q-24's fleet UX ruling remains separately pending.
