# Story 183 / Epic 173 — cert-manager v1.21.3 source review

Review: 2026-10-23 20:57–21:02 UTC. Reviewer: native source-review worker; sole runtime/merge/tracker owner remains the driver. No repository mutations, cluster reads or writes, Secret payloads, production issuance, key rotation, denied-data routes, or additional workers.

## Decision and exact candidate

No source, values-adaptation, publication, or rendered-security blocker found for the bounded v1.21.2 → v1.21.3 patch. This is source approval, conditional on required exact-head checks and the driver's runtime invariants; it is not issuance/renewal acceptance.

[PR #4258](https://github.com/thaynes43/haynes-ops/pull/4258), head `3689667267f76cd30436a300314882533100ab32`, base `4b3d9d010bb93648188fbad7bb707916a02f8321`: exactly one file and one value, the cert-manager OCIRepository tag. No config, permissions, identities, runtime holds, or frozen work changed. Public HelmRelease values at the base are the render input; no valuesFrom, release-name overrides or variable placeholders exist in this HelmRelease. Kustomization target namespace is cert-manager; common label is unchanged.

## Release and published artifacts

The [official v1.21.3 release](https://github.com/cert-manager/cert-manager/releases/tag/v1.21.3) is now published: `2026-10-23T20:47:13Z`, non-draft, non-prerelease. Both `cert-manager.yaml` and `cert-manager.crds.yaml` download successfully. This supersedes the previous review's earlier publication-time 404 observations. Exact source tag remains `925c22283de556172d48d96a165dc2fa27f7a6ff`.

Official notes identify fixes to stale ARI renewal data and private-key Secret ownership, proxy rendering, and trust/security dependencies; they recommend upgrading. The changed trust bundle removes the compromised emSign ECC Root CA - G3; Go 1.26.10 also rejects affected certificate chains. That can intentionally affect outbound TLS to services relying on that trust; no current public values require adaptation. The [1.21 documentation page](https://cert-manager.io/docs/releases/release-notes/release-notes-1.21/) still describes the minor line and earlier patches; the newly published release body supplies patch-specific notes.

Anonymous OCI requests explicitly accepted OCI index/manifest and Docker manifest-list/single-manifest media types. Both chart tarballs and provenance blobs were downloaded by manifest digest and SHA256 matched. `helm verify` verified both chart archive hashes and maintainer signatures against the official public GPG keyring, fingerprint `1020CF3C033D4F35BAE1C19E1226061C665DF13E`.

| Chart | OCI manifest SHA256 | Chart archive SHA256 |
| --- | --- | --- |
| v1.21.2 | `634dce9c13b56677a2c05e2ab76c312d0be2664022d5dd05815da67e1fd5f610` | `73a56e1728edd6c99f1f31082618c3259d279a76b7ebd3d4bdc5475c2442d34a` |
| v1.21.3 | `e482675aec32862d323a9218293ff7de81192337998615e4378c56498473f240` | `9ec67938b2981d8b333af61b3fcf53e26b4f6f5deb7ccda3de4ea116bb373a17` |

All five image references rendered by the chart resolve at v1.21.3. Every index advertises linux amd64, arm/v7, arm64/v8, ppc64le and s390x; linux/amd64 child manifests and configs were downloaded and hash-checked. Other child platforms and image filesystem layers were not downloaded. Configs identify cert-manager's source repo and expected entrypoints but lack explicit version/revision labels; no binary was executed.

| Image at quay.io/jetstack/ | Published v1.21.3 index SHA256 |
| --- | --- |
| cert-manager-controller | `9f4f96a33c7774cef24466fcd85031997531a03a8d117f151126baa8d1290dcc` |
| cert-manager-cainjector | `083d0e906f8eb44e255fe4ff817ae54e49f9ed82e22f1011bb4e9e4f4b8a641f` |
| cert-manager-webhook | `055ac8e9de6a0ba6763794838fe38944acc650f68ebe1b01dd8be079e580f458` |
| cert-manager-acmesolver | `c8c1225bb2fe8ab37a330b77f0d8c2ee6a41210fcb54fc07ca606ab9a022b8b7` |
| cert-manager-startupapicheck | `1c519d4afca1413d1e4ccd78bdc756184418ab55a34569a3b3de39d32f9974e1` |

Actual image cryptographic scope: downloaded each digest's legacy Cosign simple-signing payload/signature, verified SHA512 using OpenSSL and [cert-manager's official PEM](https://cert-manager.io/public-keys/cert-manager-pubkey-2021-09-20.pem), and asserted its critical repository identity and image digest match the v1.21.3 tag result. All five returned Verified OK. This independently verifies signatures over the image manifest identities/digests; it does not verify transparency-log inclusion, build attestations, reproducible builds, runtime binary versions, or all filesystem contents. The official [signature instructions](https://cert-manager.io/docs/installation/code-signing/) likewise describe this key and SHA512 and omit transparency-log checking.

## Rendered security/API comparison

Helm v4.3.0 rendered each downloaded chart locally in upgrade mode with the exact public `.spec.values`, release/namespace cert-manager, Kubernetes capability version 1.35.4 (the repository line; not a runtime query). No Secret resource was rendered or read. This is a local static chart comparison; it does not replace Flux's required CI render or live admission checks.

Both renders have exactly the same 50 resource identities: 6 CRDs, 3 Deployments, 3 Services, 4 ServiceAccounts, 13 ClusterRoles, 10 ClusterRoleBindings, 4 Roles, 4 RoleBindings, 2 admission webhook configurations and 1 hook Job. After only replacing exact `v1.21.2` strings with `v1.21.3`, the entire semantic diff is one addition: optional string `Certificate.status.acme.ari.certID`. A raw normalized YAML diff confirms the same six inserted lines.

Consequently no changes occur to RBAC rules/binding subjects, admission match rules/failure policies/service references, API groups/names/scopes, served or storage versions, selectors, security contexts, resource requests/limits, replicas, DNS settings, or non-image args. All six CRDs still serve/store only v1, without conversion changes, and retain `helm.sh/resource-policy: keep`. All component image tags, including the HTTP01 solver arg, track v1.21.3. Proxy quoting has no rendered effect because current values do not set proxy values.

Immutable [controller feature source](https://github.com/cert-manager/cert-manager/blob/925c22283de556172d48d96a165dc2fa27f7a6ff/internal/controller/feature/features.go#L236) sets ACMEUseARI default=false (Alpha). Rendered args/config do not enable it, so the ARI renewal path is inactive under this public configuration. Private-key ownership hardening affects future issuance/rotation irrespective of whether ARI is enabled and remains operationally unexercised by this review.

The startupapicheck Job/RBAC annotations are `post-install`, not `post-upgrade`. A fresh startupapicheck Job is therefore not expected for this chart upgrade; its absence does not fail this patch. An older successful install Job does not prove current webhook admission.

## Runtime acceptance and rollback limits

The driver reports preflight 11 Certificates, 2 ClusterIssuers and 5 Issuers Ready; the wildcard was issued October 21 at revision 7, so no fresh issuance is expected today. Those are driver-provided facts, not independently queried here. A bounded patch rollout can reasonably be accepted on unchanged issuance-facing configuration, the narrow rendered delta, current/new controller image health, healthy webhook/API and established expected CRDs, unchanged Ready/expiry/renewal metadata, and permitted bounded logs showing no new reconcile/ownership/renewal error pattern. Compare against preflight; do not force production renewal or rotate keys to manufacture proof.

That establishes installation and preservation of existing certificate/issuer health. It does not prove fresh ACME issuance, renewal, ARI rollover, private-key recovery, or direct Order/Challenge progression. The driver's denied Orders/Challenges list remains denied: no retry, alternate route, namespace variation, identity change or policy change is requested or needed. Any unmet runtime invariant remains the driver's stop/rollback decision.

Rollback is a GitOps tag revert to v1.21.2, retaining Certificates, Secrets and CRDs. Because chart-managed CRDs are ordinary templates with crds.enabled=true, the older chart can also restore its earlier Certificate schema. Optional-field removal can prune certID on read/write under [Kubernetes CRD pruning](https://kubernetes.io/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definitions/#field-pruning); unchanged v1 storage and an optional status-only addition suggest no schema migration barrier, but this is an inference, not a tested downgrade guarantee. `crds.keep` protects deletion only, not schema downgrade. Do not promise rollback itself re-fetches ARI: v1.21.2 lacks the new certID tracking and restores the prior renewal-loop/ownership bugs. Rollback does not undo issued certificates or external ACME actions. Verify controller/webhook/API and certificate/issuer invariants again if reverted.

## Exact-head CI and artifacts

At the saved 21:01 UTC snapshot: exact head unchanged; `Diff Scope - Success` passed, Flux Local test/diff jobs were still running and its success aggregate had not yet reported; Claude Review advisory was skipped. No inline review or issue comments/findings existed when read. Driver must require the actual `Flux Local - Success` and `Diff Scope - Success` aggregates on this same head; individual legs/advisory skip are insufficient. No merge was attempted by this worker.

Safe evidence under `/tmp/renovate-183-certmanager/`: `publication.json`, `image-signatures.json`, `semantic-review.json`, `rendered-version-normalized.diff`, `rendered.diff`, both rendered YAMLs, exact values, verified chart/provenance artifacts, release JSON/static manifests, and PR exact-head diff/check snapshot. No credentials or Secret payloads are included. Source worktree remains clean.
