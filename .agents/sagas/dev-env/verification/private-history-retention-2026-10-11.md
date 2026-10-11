# Private history: guarded operator/schema promotion

This installs dev-env #174's first retention guard, source
`af80cfc8bc2bcb4cb28290ac110eff924308be76` from PR #183. It preserves private
HOME PVCs and discoverable AgentSession identity on reap/autoarchive. A persisted
latch survives flag disable; original binding and one-way executor intent cannot
be erased or rebound. Missing attempted executors remain PreservationUnknown.
No stop proof, context export, retention TTL or replacement launch is invented.

Only the operator image and explicit retention flag change, alongside its
byte-exact generated AgentSession schema. Signed operator digest:
`def7dac13c94da7a31b943f4b8944c327aa0508e3cbbb9423bfe0c32021dc120`;
[publication 38155510294](https://github.com/thaynes43/dev-env/actions/runs/38155510294)
passed signing and exact-source verification. CRD SHA256:
`5d70cfe1e57297ce182d070429aa796da1b3d0e88e52a5ae63f42b9fe8ea09e8`.
Its existing prune-disabled policy and operator dependency remain intact.
Apply the schema before the operator. After retention latches exist, preserve
both this schema and a retention-aware operator. An older schema could prune
their fields; an operator predating the guard lacks its HOME/finalizer protection.
Disabling the new flag preserves existing latches. Downgrading to a pre-guard
image is not established safe.

All other operator flags, replica count and native/budget/helper/keeper pins
remain unchanged. Private-project admission stays false. V1, agent templates,
existing sessions, keeper credentials, native fixtures and original ledgers
are not changed. Only two operator replicas may roll. Existing protected
Pod UID/image/restart counts, terminal Job bytes/UIDs and ledger hash must match
a fresh baseline after rollout. Zero current AgentSessions is not an installed
retention test or provider-history acceptance.

Narrow operator/CRD renders, schema equality and semantic preservation checks
passed. Required CI and native independent review govern merge. Claude reviews
are optional/down; record unavailable/skipped outcomes and read any emitted
findings without retrying authentication. Declare scoped activity before
reconciliation and end it after preservation verification.

Installed acceptance requires the generated schema established, two Ready
operator replicas using the signed image/retention flag, and unchanged protected
runtime evidence. A disposable bounded retention/context pilot remains a
separate #174 gate. Existing API metadata retains its current ACL; this does not
grant cross-parent transcript reads. Provider adapters, durable session directory
and authorized stopped-context reads remain open on #174 and the #91 resume index.
No model turn, memory stress, local image build, credential copy, v1 restart or
retired storage investigation belongs in this deployment.
