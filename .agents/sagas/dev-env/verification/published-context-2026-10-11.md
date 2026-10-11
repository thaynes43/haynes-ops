# Published context: protected coordination prerequisites

Source PR #190 merged as `5cfcf0e02f9d16d11b3a9d92bd9f56fde748d3d2`;
release #192 shipped v2.17.0. DESIGN-013 retains explicit bounded context
publications with original Session identity and separately authorized payload
reads, including after the source stops or disappears. It accepts a publisher's
explicit excerpt; provider capture and complete history remain unaccepted.

This promotion adds the exact generated PublishedContext CRD to the existing
prune-disabled inventory and a separate namespaced operator-only Role/Binding.
Only get/create/list on publishedcontexts in the protected coordination group
is granted. Worker grants are unchanged. The signed operator image is
`sha-5cfcf0e@sha256:9f951a59298752ed33fb7d2f9026ceb51a10c3ae5e6f05e937629041afd14969`.
Publish run 38161917707 verified the exact image against publish.yml on main.
The only feature delta is enable-published-contexts=true and explicit
context-payload-read-projects=dev-env. Claims and retention stay enabled;
private-project admission stays disabled. All existing schemas, RBAC, templates,
keeper and retained fixture pins remain unchanged.

Focused render, exact schema and preservation checks, native independent review
and required hosted CI gate merge. Claude reviews are optional while down. Read
the available outcome and findings without retries or authentication repair.
The stopped local schema-host route in Q-25 remains stopped; this promotion
does not claim that check passed or authorize a new lookup.

Before merge, capture the exact current operator Deployment identity and all
other Pods, terminal Jobs, schema/Role identities, catalog/template hashes and
the failed native ledger. Declare the scoped operator rollout. Verify the
new CRD is Established and exact, the Role/Binding is narrow and exact, both
operator replicas run the signed digest with only the declared flag changes,
and Flux records the reviewed merge revision. Every other protected identity
and restart count must match. End the activity after verification.

Installation and operator preservation are separate from acceptance. No
provider task, credential copy, history read, v1 change or session restart is
part of this promotion. Actual publication, authorized stopped-source reads,
refusals across unrelated identities, durable provider history, session
directory and the concurrent owner pilot remain #174/#91 acceptance. Keep both
parent issues open. Retention-aware schema and operator must survive rollback
once a private history latch exists; disabling a flag cannot erase a latch.
