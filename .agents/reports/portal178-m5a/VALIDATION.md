# M5A validation receipt

2026-10-23 UTC. Marker PORTAL178-M5A-CLIENT-GITOPS-20261023-01.
Source checks only. Exact pushed head/PR, CI and independent native review are
recorded in the PR and canonical Story178; this receipt does not pre-approve them.

## Baseline and scope

Initial clean/fresh origin/main:
`cde225d0937761c4d6a3dd71728199d44682c34a`. Before publishing, fetched and
fast-forwarded to `104f47ed3206cb5988ee77ba8f2aaa35d9f9c4b0`; changes were
disjoint. Open PR file metadata rechecked: no other portal/Auth path owner.
No dev-env source, private Cursor state, user-owned session, V1 mounted resources,
shared flow/outpost/application or shared Traefik edits.

Actual live read-only checks:

- Original owner's session identity privately verified, profile full; raw
  AgentSession UID omitted from this public receipt.
- Authentik server/worker 2026.8.5, three ready replicas each.
- Zero portal ExternalSecrets; onepassword-connect Ready=True, vault HaynesKube.
- Public schema read from existing worker with
  `kubectl exec -n network deploy/authentik-worker -c worker -- cat /blueprints/schema.json`.
  No credential or database value was read.
- Authenticated provider/reference preflight: **pending** a documented token-only
  helper/selected root readback. Root supplied zero provider/application at
  18:54:53 UTC; no independent live identity integration claim.

## Historical M5A offline validation (d82f6fbc; 13 mutations)

Python 3.11.2, PyYAML 6.0.3, jsonschema 4.25.1, Kustomize 5.8.3.
One suite at a time; no stress, parallel load or wide test loop.

```bash
kustomize build kubernetes/main/apps/network/authentik/app \
  > /home/dev/work/portal178-m5a/rendered.yaml
/home/dev/work/portal178-m5a/venv/bin/python \
  scripts/tests/portal178-m5a/validate.py \
  --schema /home/dev/work/portal178-m5a/deployed-schema.json \
  --rendered /home/dev/work/portal178-m5a/rendered.yaml
git diff --check
```

Historical PASS at d82f6fbc: pinned actual blueprint schema; parsed/tagged configuration constraints;
**13 rejected unsafe mutations** (any-policy, public client, refresh grant, email
scope, regex callback, username subject, source secret, absent user/group,
disabled binding, permissive deny expression, broad secret item, dataFrom).
The guard substitutes **synthetic typed references** for schema validation; it
does not execute policies or resolve actual FK/directory values. It is a
configuration guard, not an identity integration test.

Reproduce using `uv run --no-project scripts/tests/portal178-m5a/validate.py`
with the same `--schema` and `--rendered` arguments (PEP723 pins direct
dependencies). Obtain the public schema from the deployed image or
[pinned upstream](https://raw.githubusercontent.com/goauthentik/authentik/4ec0d19b98da3c329781894cd550da95d3033954/blueprints/schema.json);
the guard rejects any bytes whose SHA256 is not
`b59476bcbaf92dc1a5bd31d98936d44348bc2b048deae7e891227597c74e97e8`.

PASS: compared parsed Kustomize output with archived initial main for the
Authentik app. Both render **11 resources**, identical identities. Only
`ConfigMap/authentik-hnet-blueprints` changes, with exactly one additional
`97-dev-env-v2-portal-oidc.yaml` data key. Removing that key makes the whole
parsed resource set identical. No HelmRelease/workload/route delta.

PASS: pending ExternalSecret parsed and validated with Draft7Validator against
the live `externalsecrets.external-secrets.io` CRD's `v1` OpenAPI structural
schema. Saved canonical sorted/indented JSON SHA256:
`43b9d7f8a624619d894d434555cc347b840565b407346a46a7d7dfe02a63dae1`.
Kubernetes CEL/admission and ESO execution were **not** performed.
Projection remains unregistered and unmounted. Relative README link checked.

Pinned source analysis (not executed integration): upstream
`4ec0d19b98da3c329781894cd550da95d3033954` confirms importer atomicity,
partial-update secret preservation, missing Find -> None plus binding
serializer failure, all-policy user/group conjunction, directory-deletion
binding cascades, optional plain/S256 PKCE and challenge/verifier validation.
See [release packet](RELEASE.md) for authoritative source links and limitations.

## Gates still pending at source preparation

Required aggregates `Flux Local - Success` and `Diff Scope - Success`;
emitted advisory findings read/disposition; root-assigned independent native
security review of actual final head; authenticated reference/duplicate
readback; reviewed merge and blueprint installation. Provider remains staged
denied. No Tom 1Password copy is requested before reviewed provider existence.

Runtime component integration, production shared store, metadata projection,
human/resource authorization, token/MFA/logout/live membership behavior, backend
trust/isolation, route deployment and **all Gate A runtime evidence** remain
unfulfilled. Public ASVS5 L2 is separate and unfulfilled.

## M5C successor correction receipt

Public PR4246 frozen `d82f6fbc2c12eb8ca7b4c8e4922982644b88c62e` was open at
pickup. Successor `haynes-ops-1023-202021` freshly fetched public main/PR source
and merged it on the managed local branch. No original private home/history read.
Story178 journal1095 supplies the native PASS-with-minors/zero-blocker review of
that historical head; a fresh native recheck of the corrected head is required.

F1: exact blueprint metadata, provider/application attrs, projection metadata and
refresh/target allowlists; **34** unsafe mutations each assert their explicit
`SecurityViolation` reason. `KeyError` and arbitrary schema/programming failures
are not counted. Guard is included in required `Flux Local - Success` dependency.
F2: both JWT federation lists explicitly empty (supported by pinned actual schema).
F3: nullable-signer/HS256 downgrade documented; selected non-null expected RSA
signer and matching JWKS RS256 readback plus BFF RS256-only pin are release gates.
F4/F5: effective-policy-user MIRROR/COPY caveats, exact-sub pin, disabled/deleted
conjunct widening, zero-binding default access, blueprint error alert/readback,
exact three enabled bindings and explicit deny `state: absent` activation recorded.
N-2: public session UID removed; fingerprint preimage specified precisely.

Local serial `nice -n 19` pinned guard and Kustomize mounted-content validation
PASS with 34 intentional rejections. Required corrected-head CI/native review,
fresh authenticated reference preflight, actual import/signer/JWKS readback remain
pending. No client installation/login/runtime acceptance claimed.

### Native recheck follow-up N-1 to N-5

Current guard count remains **34**, distinct from the historical 13 above.
CI now installs all direct/transitive dependencies with hashes in an isolated
venv and runs Python `-I -B`. A checksum-pinned Kustomize5.8.3 build is passed
through `--rendered`. The aggregate requires successful filter execution and
fails on any dependency failure/cancellation; legitimate no-change skips remain
allowed. Full selected readback includes provider type/lifetimes/claims and
safe omitted-attribute defaults. Exact final-head CI and fresh native delta
review remain required; no installation or runtime claim follows from this edit.
