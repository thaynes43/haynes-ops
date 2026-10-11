# Session directory and typed usage: install before enrollment

This bounded #174/#176 prerequisite PR follows installed #3822. Promote the
final merged D17/D18 schemas and D19 private-Pod preservation guard in the signed
operator/agent release, plus a narrow
operator-only directory Role and write guard. Keep all current flags unchanged;
directory/common/canary activation and global private admission remain off.
Create no SessionDirectory instance, fixed provider record or task. The initial
source audit is `2b56e5950eea75777a720e2bc11556357feca0c3c7b0f0e5e947027cb03242bf`.

## Exact source and authority

Use generated schemas from the final merged source containing D17 and D18, and
require the signed operator source to include D19 / PRIVATE-POD-R1 (#202).
Neither separate branch AgentSession schema contains both additions; installing
either alone would prune the other. Preserve the root retained-directory witness
guard, immutable server-selected Session enrollment/confirmation receipts, typed
usage presence/identity/replay guards and every unrelated installed field.
D19 refuses protected private Pod removal before rescue, exec or Delete, including
partial/unknown history and disabled retention flags. The existing volume guard
alone did not protect terminal Pods; preserve that corrected source finding and
its pre-fix failure fixture. This guard supplies preservation, not original-process
stop proof or native history acceptance.
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
deletes and every other writer. Keep the six existing policies and six bindings
(twelve guard objects) and all existing authority unchanged. Before enrollment,
require status.observedGeneration equal to the current metadata.generation and
present status.typeChecking without expressionWarnings. Saved installed policies
use this representation and have no Accepted condition; absent/stale checking
is not an acceptance receipt.

Discovery has an explicit global project grant to all unchanged exact configured
trusted human/client identities and freshly verified configured coordinator
hosts. It is not a per-ServiceAccount project map. Live Session readers also
prove their original owned Pod/Session and accepted own project. Directory
discovery grants no context payload, claim write, inbox content or lifecycle
authority. The install PR leaves discovery/enrollment flags and scopes absent;
startup requires trusted canary records before directory enablement.

Source #199, #200 and #202 are merged; #201 released v2.20.0 at
3aab27cebf66dcb44d8e50c02eddd00154e8c722. The combined AgentSession schema
SHA-256 is 32b2c3e3f40258694e3693a3f5ba3631b71fa729b1708a3cfb6f9e4828155703;
the directory schema is 7afdd4259b6909c302b77e54671a9d07b1580a98351c1b047977d41ea1c12724.
Operator source a44b5b1e0b92b4c1a146254e03202617a58b7a01 was published by
[38176418666](https://github.com/thaynes43/dev-env/actions/runs/38176418666):
ghcr.io/thaynes43/dev-env-operator:sha-a44b5b1@sha256:d0463c21efa3a46f5f6ee94623b587aef9f0d9bc6d6632f567d018172dc43447.
Agent tag v2.20.0/source 3aab was published by
[38176859402](https://github.com/thaynes43/dev-env/actions/runs/38176859402):
ghcr.io/thaynes43/dev-env:2.20.0@sha256:823e015567fdb34951c4e6ce7e39891e3cc8a1e2d1d219bee127fe537cb31303.
Protected finite publication receipts are 8d10b82f43f4fdf1fee09650cf6e54d7a15ecdd9c3f04038ea3d2953e2f3def3
and a09735fb748f364c58b2cfdefe0521b2f1625d3af37a4e8c3a5615974a86c1da.
They verify exact hosted keyless cosign workflow identities and issuer; no
independent registry reread is claimed. PUBLICATION-R1's original HOLD and helper
are preserved; native-reviewed R2 records attempts before execution and keeps
partial timeout output. Root's one missing-output-parent preparation failure
occurred before any remote command and remains recorded; counters were not reset.
Publication is complete. Installation and actual provider acceptance remain pending.
The eight manifests are frozen at fa40c8bf0ee9638acca60d1f69278e9586b8e07a.
The official offline helper computes template revision 2.20.0-ec26880d2a;
catalog, project, common and instruction-source revisions remain unchanged.
Final checkpoint 9b980b0af54896ee3afb4b38c477b7fb96395f4e12780a15b60f676220efb3ee
binds the three material renders, reused guard cases/RBAC render, exact generated
schemas, image-only comparisons and all unrelated source preservation. The original
CEL-list ordering comparison failure and two compiler-path preparation failures
remain retained; one corrected comparison and one actual cached compiler invocation
passed. No extra tests, DNS schema route, local image or runtime calls were run. Change only operator image and future template
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
observer. Reused capture/GET functions remain exact; new exclusive artifacts use
0600 and fsync, while the earlier helper additionally chmodded artifacts to 0400.
Hash-bind the final helper and plan and preserve every prior receipt. Preserve all eleven nonoperator Pod UIDs/restarts/specs, four Jobs and
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
is stopped after three failures; #170 source fix is not a runtime pass. When the
owner asked for clarification, root explained that no decision is needed for this
increment: use the already authorized hosted CI and real-API verification; do
not start a DNS investigation or repeat the stopped route. This clarification
is not approval for a new investigation. Q-24 native routing remains
unestablished/pending and no fleet UX is selected. Stop
after three failures at one blocker or sixty minutes without evidenced progress
and ask one actionable native owner question. Keep #91/#173/#174/#175/#176 open;
source tests and installation do not establish the working MVP.
