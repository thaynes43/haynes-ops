# Durable inbox: protected coordination prerequisites

Source PR #194 merged as `f02d0ad276d007105795dd8611d0725c5176869b`.
Release #193 packages it in v2.18.0. DESIGN-014 retains immutable messages and
separate read acknowledgements in the protected coordination API. Sender UID
and caller key bind one intent; retries cannot retarget it. The original
recipient identity can read and acknowledge accepted messages after stopping,
when it has an authorized current executor. Acknowledgement proves an API
receipt, not model consumption or a complete provider conversation.

This promotion installs the exact source InboxMessage and InboxReadAck schemas
and the additive AgentSession canary schema. Canary admission remains disabled.
It extends the existing prune-disabled inventory and adds a separate namespaced
operator-only Role/Binding granting get/create/list on the two inbox kinds.
Existing worker grants, claim and context authority are unchanged. The signed
operator is `sha-f02d0ad@sha256:2c41dc9c5efd0cba5e64aba25bdf3c967b076cfaf04eb07cc094a1eeaaf9b499`.
Publish run 38164417101 verified it against publish.yml on main. The only flag
additions are enable-durable-inbox=true, inbox-projects=dev-env and
inbox-coordinator-read-projects=dev-env. Retention, claims and published context
remain enabled. Private admission, managed canaries and retained common inputs
remain disabled. Templates, keeper, failed fixtures and their pins stay intact.

Four bounded renders, exact schema comparisons, semantic preservation, native
independent review and required hosted CI gate merge. Claude reviews are
optional while the service is down. Read available receipts and findings;
no retry or authentication repair is needed. Q-25's stopped local schema-host
route remains stopped; this promotion does not claim that check passed.

The pre-merge baseline binds eleven other Pods, four terminal Jobs, seven
existing schemas, authority identities, catalog and templates, and the failed
native lifecycle ledger. Declare the scoped operator upgrade. After Flux
applies the reviewed merge, require both replicas to run the signed digest
with exactly the declared flag additions, new schemas Established and exact,
and the narrow Role/Binding exact. Preserve every protected Pod identity and
restart count, existing schema and authority, and all retained fixture receipts.
End the activity after the finite observation succeeds.

Installation is separate from acceptance. At this baseline there are zero
AgentSessions, so workload preservation does not prove an upgrade during live
provider sessions. This promotion starts no provider task, copies no credentials,
reads no private HOME, and changes no v1 resources. Actual isolated claim
contention, durable messages, authorized stopped-recipient access and refusals,
offline inbox polling, model consumption and the concurrent owner pilot remain
#175/#91 acceptance. Keep the parent issues open. Retention-aware schemas and
operator must survive rollback once private history is latched.
