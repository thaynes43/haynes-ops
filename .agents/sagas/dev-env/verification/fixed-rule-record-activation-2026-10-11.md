# Fixed rule record activation — 2026-10-11

This bounded change adds six operator arguments. It enables retained common
rules, exactly two trusted project-rule canaries, and prospective directory/
metadata discovery for project dev-env. General private-project admission stays
false. Existing catalog/common/template/profile/network/RBAC/schema/keeper and
all v1 files remain unchanged. Installing these flags creates no Session or
provider attempt. The current dev-env pod does not restart.

## Exact record contract

Only `dev/dev-env` may call the ID-only route for:

| Permanent ID | Provider request | Policy digest | Deterministic Session name |
| --- | --- | --- | --- |
| rule-canary-claude-1011 | claude-opus-5-5 / xhigh | bcd90e0032e5f57d29f8daa2183e7c750dac4a1f1aecfe052d8a45a5c3ec64e9 | private-canary-fc5935c175c6fc15f842b051bf85aa8d |
| rule-canary-codex-1011 | gpt-6-astra / max | 93741899e363cb35023ab5a09f9f51a871b083383de3df740542040f6b758625 | private-canary-3bfe28755095977c9462c269cc24dee3 |

Both use signed agent v2.20.0
`823e015567fdb34951c4e6ce7e39891e3cc8a1e2d1d219bee127fe537cb31303`
and template `2.20.0-ec26880d2a`, catalog revision
`9c5cc110d56f9afcd360a76947523d1b73cfa8bc685556c0c47f8e00c80d6d29`,
rules `1cebe4524e65483d4854960c34a31d3f75f1cfc0acb991f0fd490b10bbc98e2b`
and common `3b9b829cf224ea4bc5b2b481ff3b7c1b4f0b173e1b4e304d84d4e3ecf4e2ca61`.
The exact 227-byte prompt has SHA256
`5a3da07a5ecae40a03fb754921297835b75d9a9a784a7d42647fadd33b127510`.
It requests only loaded instruction markers, contains neither opaque marker,
and forbids tools or fetching files. Capture is enabled for the original managed
invocation. Native model/cost/stop/history remain Unknown without evidence.

Each provider has one permanent idempotency key and one initial attempt, with
180s provider timeout, 600s Pod deadline, RestartPolicy Never and five-second
termination grace. Separate private HOME/PVC, requests100mCPU/1Gi and limits
2CPU/4Gi; no init/sidecar/ephemeral executor. Claude maxTurns1, Codex maxTurns0.
Use the subscription-backed CLIs. Do not copy enrollment/refresh credentials,
substitute paid APIs, rearm a receipt, delete a failed fixture or choose fleet UX.

## Installation and first-create gates

Required hosted Diff/Flux checks and exact native independent review precede
merge. Claude reviews are optional while unavailable; preserve/read any advisory
findings or error receipts without retries. Before merge, freeze one finite
preservation baseline; after normal Flux rollout verify exact reviewed flags and
unchanged peers, Jobs, failed-native ledger, old grants/guards/schemas, catalog/
common identities, keeper and v1 Pod. Changes affect only operator arguments,
with no existing managed-session restart.

Directory cardinality is Unknown. The earlier direct Kubernetes list was
Forbidden under intended operator-only authority; its empty List stdout is not
evidence. Preserve that observer and logical campaign counter. Do not retry it,
switch identity or add broad Kubernetes reads.

Before EACH first fixed canary Session create, a fresh authorized project-scoped
operator/API directory page is required. The pre-D20 shipped CLI has no Directory
verb; do not invent one, because unknown commands can reach creation. The bounded
source D20 client adds `directory list --project dev-env --limit 20 --json --once`
using existing authentication with no HTTP retry or redirect. Its merge, bound
client build, native review and required CI precede invocation.

Require a complete page with empty continuation and every existing record bound
to real approved original namespace/name/UID and immutable project/task/source/
provider/rule/image receipts. Before the second provider, include only an actually
confirmed first original receipt. Unknown/foreign/mismatched/duplicate or existing
same fixed identity refuses a new executor. Exact retained originals may be
reported through existing authorized reads, never replaced or rearmed. Record
actual authorized evidence instead of inferring zero. Config readiness alone
proves neither provider-plan readiness nor loaded rules.

Actual acceptance requires each original native response to report both loaded
common and native pinned-repository markers, with marker values absent from the
prompt/catalog/developer wrapper. Preserve capture, fresh selected-repository
fetch/immutable task pin, original HOME/Pod UID and actual placement receipts.
Resume preserves WIP. Public dev-env inside a private HOME does not establish
genuine private GitHub repository authorization.

This change accepts only trusted configuration installation. Actual rules,
directory/history, claims/messages/stopped payloads, truthful usage, whole-task
private budgets/native recovery and concurrent owner pilot remain separate.
Q-24's routing choice remains pending. Local inference waits for GPU readiness
and household reservations. No stress test or memory exhaustion is permitted.
