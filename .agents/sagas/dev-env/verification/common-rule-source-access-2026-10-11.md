# Common rule source access: separate guarded prerequisite

Source dev-env #195 merged as `f1bb6ca26937557f6c823fc74b0ba385661f360a`.
DESIGN-015 retains exact common instruction bytes and named source provenance
before new private managed canaries. Its source does not authorize a trial or
establish provider rule loading. This PR delivers only the required read grant.

Add Role and RoleBinding dev-env-common-rule-sources in dev-agents. Its sole
subject is dev-env-system/dev-env-operator. The single rule permits GET only on
named nonsecret ConfigMaps dev-env-config-claude and dev-env-config-codex in
dev-agents. There is no list/watch/create, Secret, worker, impersonation or
clusterwide grant. No operator image/flag, template, schema, catalog, common
content, network, admission or keeper change is included. Private admission,
managed canaries, common snapshots and managed capture remain disabled.

The exact two manifest files passed four bounded renders and source preservation
checks over 140 protected paths. Forty-six offline negative observer checks
refuse changed trust inputs before GETs. Independent native review and required
Flux Local/Diff Scope CI gate squash merge. Claude review is optional while down;
retain/read its outcome or substantive findings without retry/auth repair.

New immutable baseline SHA256
`988a779b63440dadbefeb9d4712d940f2a296efdbdbdbe8b5b96403c531b9c84`
is compared with the accepted Inbox #3820 observation. It binds all thirteen
Pods, including both operator replicas, four retained Jobs, nine complete CRD
specs, sixty existing RBAC records, admission/network guards, catalog/templates,
public common-source identities/content hashes and the original failed native
ledger. Zero AgentSessions existed. The frozen packet SHA256 is
`e1a1ed7eccf465903e60adeef2d6a9865eff5b7e197970a54bf16e7d69ccdf34`.
The GET-only observer verifies its baseline SHA before any runtime read and
permits only the exact new Role/Binding plus Flux status at the reviewed full
merge revision. No operator Pod rollout is permitted.

After normal Flux reconciliation, one finite observation must confirm exact
named-source authority and dev-env-rbac Ready at that merge, preserving every
existing Pod UID/restart count and all protected specs/identities. No workload
restart, source Pod exec, private HOME/Secret read, auth probe or provider turn
is needed. This does not touch v1 mounted resources and requires no held draft.
A failure preserves its receipt; never overwrite the baseline or restart a trial.

Installation accepts only this required read prerequisite. Signed agent/source
promotion, exact policy/catalog/profile/network bindings, actual loaded common
and repository markers, fresh private task pins, authorized claims/messages/
stopped-history reads and live-provider operator-upgrade preservation remain
separate acceptance in dev-env #173/#174/#175/#91. Q-24 native routing is pending;
no fleet UX, cloned credentials/enrollment or complete MVP is claimed. Preserve
native170 failures/ledger and unchanged bootstrap2/roots2/Q25 stopped counters.
