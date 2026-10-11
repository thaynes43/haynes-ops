# Session directory and typed usage: install before enrollment

This bounded #174/#176 prerequisite PR follows installed #3822. Promote the
final merged D17/D18 schemas and signed operator/agent release, plus a narrow
operator-only directory Role and write guard. Keep all current flags unchanged;
directory/common/canary activation and global private admission remain off.
Create no SessionDirectory instance, fixed provider record or task. The initial
source audit is `2b56e5950eea75777a720e2bc11556357feca0c3c7b0f0e5e947027cb03242bf`.

## Exact source and authority

Use generated schemas from the final merged source containing both D17 and D18.
Neither separate branch AgentSession schema contains both additions; installing
either alone would prune the other. Preserve the root retained-directory witness
guard, immutable server-selected Session enrollment/confirmation receipts, typed
usage presence/identity/replay guards and every unrelated installed field.
Copy the exact generated SessionDirectory schema with its status subresource.
Its original metadata has no owner reference, expiry or cleanup policy. Preserve
the installed CRD UIDs and observed conversion defaults without inventing defaults
in the generated source. The new CRD remains protected from Flux pruning.

Grant only dev-env-system/dev-env-operator in dev-env-system:
coordination.dev-env.haynesops.com sessiondirectories get/create/list, and
sessiondirectories/status update. No watch, patch, delete, main-resource update,
worker grant, Secret access, new client identity or coordinator host.

Add one Fail/Deny ValidatingAdmissionPolicy and binding matching only directory
main/status CREATE/UPDATE/DELETE. Require dev-env-system and the exact operator
identity. Permit main-resource CREATE and status UPDATE only; deny spec updates,
deletes and every other writer. Keep all twelve existing policies and all
existing authority unchanged. Confirm server type checking/Accepted status with
no evaluation warnings before any future enrollment.

Discovery has an explicit global project grant to all unchanged exact configured
trusted human/client identities and freshly verified configured coordinator
hosts. It is not a per-ServiceAccount project map. Live Session readers also
prove their original owned Pod/Session and accepted own project. Directory
discovery grants no context payload, claim write, inbox content or lifecycle
authority. The install PR leaves discovery/enrollment flags and scopes absent;
startup requires trusted canary records before directory enablement.

Use exact signed publication receipts for the final operator source and agent
release. Version/digests remain pending until publication, rather than inferred
from an intermediate build. Change only operator image and future template
version/default image; preserve all profiles, flags, mounts, keeper pins and
fixed 2.11 budget/native fixture images. Existing session Pods never restart
solely for reporting or a template change. D18 omits unqualified raw Usage in
new API responses and new outgoing heartbeats, while private/stored legacy data
and ordinary heartbeat/budget enforcement remain preserved. Actual served
model, billing/quota and full-session aggregate remain Unknown.

## Affected paths and acceptance

Under kubernetes/main/apps/dev-env-system, change only:

- crds/app/agentsessions.yaml and exact new generated directory CRD;
- crds/app/sessiondirectories-operator.yaml and its kustomization references;
- rbac/app/session-directory-guard.yaml and its kustomization reference;
- operator/app/helmrelease.yaml image and templates/app/templates.yaml image/version.

The verification contract is the only additional documentation path. No v1
mounted resource, policy network change, catalog/common source edit, credential,
enrollment, provider HOME, new workload or storage investigation belongs here.

Validate bounded renders and exact signed/schema/authority comparisons, meaningful
guard allow/refuse cases, and reproducible final schema generation. Use nice19,
GOMAXPROCS2 and low parallelism; no burners, stress, wide/looped tests or local
image builds. Required CI and native independent review gate squash merge.
Claude review is optional/down per owner: read failed/skipped receipts and any
available findings, without retries/authentication repair.

Before merge freeze a new immutable GET-only baseline and independently reviewed
observer. Preserve all eleven nonoperator Pod UIDs/restarts/specs, four Jobs and
the failed-native ledger, nine existing CRD UIDs/complete unrelated schemas,
sixty-two existing complete authority objects, twelve old admission guards,
fifteen policies, keeper/shelf/fixture pins and catalog/common UID/RV/content.
Allow only the exact combined AgentSession schema, one directory CRD, one Role/
binding, one guard/binding, reviewed template change and signed operator image.
Declare the operator upgrade, require two Ready replicas and exact affected
Flux merge revisions, run the finite observer, then end the scoped activity.

Accepted by installation: reviewed schemas, signed image/template, narrow access
and guard, and explicit preservation checks. Actual prospective directory,
provider usage, loaded rules, claims/messages/stopped context and managed live
upgrade preservation remain the disposable owner pilot. Separate fixed-record
activation must follow verified prerequisites and bind fresh source revisions.
Directory enrollment is prospective; no backfill, replacement attempt or new
idempotency key can repair a refused/uncertain first provider attempt.

Keep original fixtures, ledgers, reviews and logical counters. Q-25 schema route
is stopped after three failures; #170 source fix is not a runtime pass. Q-24
native routing remains unestablished/pending and no fleet UX is selected. Stop
after three failures at one blocker or sixty minutes without evidenced progress
and ask one actionable native owner question. Keep #91/#173/#174/#175/#176 open;
source tests and installation do not establish the working MVP.
