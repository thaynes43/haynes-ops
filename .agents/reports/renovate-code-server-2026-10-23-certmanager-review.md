# Story 182 / Epic 173 — cert-manager v1.21.3 source triage

Bounded read-only review, 2026-10-23 20:43–20:45 UTC. No repository edits, runtime queries, cluster actions, credentials, Secret payloads, tests or new workers. Driver retains sole runtime ownership and original 21:02Z deadline. Disposition per driver: leave candidate UNREQUESTED at working v1.21.2 because complete late-arriving delivery/verification does not fit the remaining original budget; this is not a finding of incompatibility or a new held work item.

## Current public source

`kubernetes/main/apps/cert-manager/cert-manager/app/ocirepository.yaml`: `oci://quay.io/jetstack/charts/cert-manager`, tag `v1.21.2`. HelmRelease enables chart-managed CRDs (`crds.enabled:true`), 3 controller replicas, public recursive DNS overrides; no image/proxy/feature-gate overrides. Cloudflare DNS-01 ACME ClusterIssuers and wildcard Certificates are public manifests; credential payloads were not read. Runtime deployed version was intentionally not independently queried.

Requested `.agents/runbooks/tier4-component-playbooks.md` exists but has no cert-manager section (checked headings and cert-manager matches). CLAUDE.md required aggregate checks: `Flux Local - Success` and `Diff Scope - Success`; advisory AI findings must be read/addressed, AI availability itself is optional.

## Publication/provenance proof and correction

Public Git tag `v1.21.3` resolves to immutable commit `925c22283de556172d48d96a165dc2fa27f7a6ff`; prior tag is `922a06aa49ee4bb802db268ef72a174af70edd32`.

- [Exact source tree](https://github.com/cert-manager/cert-manager/tree/925c22283de556172d48d96a165dc2fa27f7a6ff)
- [Immutable comparison, 42 commits](https://github.com/cert-manager/cert-manager/compare/922a06aa49ee4bb802db268ef72a174af70edd32...925c22283de556172d48d96a165dc2fa27f7a6ff)

Initial Quay request without explicit OCI Accept returned 404; corrected explicit OCI Accept returned 200. Therefore the initial registry 404 is NOT absence evidence. OCI manifest created `2026-10-23T20:35:32Z`:

- Manifest `sha256:e482675aec32862d323a9218293ff7de81192337998615e4378c56498473f240`
- Chart layer `sha256:9ec67938b2981d8b333af61b3fcf53e26b4f6f5deb7ccda3de4ea116bb373a17`
- Provenance layer `sha256:92c767331c38926678a4951a9444da3f04c8e4845aa74504072846ba1984e89c`
- [Immutable OCI manifest](https://quay.io/v2/jetstack/charts/cert-manager/manifests/sha256:e482675aec32862d323a9218293ff7de81192337998615e4378c56498473f240)
- [Immutable chart blob](https://quay.io/v2/jetstack/charts/cert-manager/blobs/sha256:9ec67938b2981d8b333af61b3fcf53e26b4f6f5deb7ccda3de4ea116bb373a17)
- [Immutable provenance blob](https://quay.io/v2/jetstack/charts/cert-manager/blobs/sha256:92c767331c38926678a4951a9444da3f04c8e4845aa74504072846ba1984e89c)

Downloaded chart SHA256 matched. `gpgv` verified Good signature from cert-manager Maintainers using official public keyring, key fingerprint `1020CF3C033D4F35BAE1C19E1226061C665DF13E`. Provenance declares matching chart hash, version/appVersion v1.21.3, kubeVersion >=1.22.0-0. Chart defaults set `crds.keep:true`. Component images derive their v1.21.3 tags from Chart.AppVersion; image registry publication/digests/signatures were NOT checked within this bounded review.

GitHub release page/API and static `releases/download/v1.21.3/cert-manager.yaml` returned 404. Official release notes and install examples still describe v1.21.2. This can be a release in publication; no 404 retry loop was run. It limits complete release/upgrade-note evidence, not proof the signed OCI chart is absent.

## Concrete changes / adaptation

Not a no-schema-change patch:

1. Optional `status.acme.ari.certID` string added to Certificates CRD and API to reject stale ACME Renewal Information after cert replacement. Existing served/storage API version remains unchanged in this compare; addition is optional. [CRD at exact commit](https://github.com/cert-manager/cert-manager/blob/925c22283de556172d48d96a165dc2fa27f7a6ff/deploy/charts/cert-manager/templates/crd-cert-manager.io_certificates.yaml), [ARI implementation commit](https://github.com/cert-manager/cert-manager/commit/21fdd53beab14629d2ff3c41e0920213a6221e17).
2. Security hardening: only consume next-private-key Secrets with matching Certificate controller ownership and label; generate fresh names rather than trusting writable status. Recovery reconciles foreign Secret references. This directly affects issuance/rotation; do not prove it only with pod readiness. [Exact fix](https://github.com/cert-manager/cert-manager/commit/5c9a70e44dba014d3e055d2a7c96ee8b9caf69f4).
3. Helm HTTP_PROXY/HTTPS_PROXY/NO_PROXY env values quoted; our source config does not set these. [Exact fix](https://github.com/cert-manager/cert-manager/commit/9f71dc9bb709be9af56f01f21044675a76da1ca9).
4. Security dependency/base-image changes include Go 1.26.10 (`crypto/x509` GO-2026-6809 reachability stated in upstream commit), x/net v0.60.0 and OTEL trace exporter v1.45.0. [Go update](https://github.com/cert-manager/cert-manager/commit/cfd6d18b661ae766d4211583b56b0347fce6ca8e).

No concrete required values adaptation found for current public config; full publication and runtime acceptance remain unverified. Official supported-release matrix lists 1.21 supporting Kubernetes1.33–1.36; current repository bootstrap pin1.35 is within it, but no runtime Kubernetes query was made.

## Minimum evidence for a later authorized delivery

Before merge: verify publication complete, all chart-managed image tags/digests available, signed chart proof retained, old/new rendered chart diff limited to expected images/labels/optional certID schema/proxy quoting; required aggregates green; investigate any unexpected RBAC, webhook, API or resource changes. Confirm target source revision/digest, HelmRelease/chart readiness, controller/cainjector/webhook version and health, startup API check and webhook admission trust, CRDs Established with expected served/storage versions and optional certID field. Read-only issuer/Certificate Ready, expiry/renewal status and Orders/Challenges progression are needed, together with errors/metrics showing issuance/rotation recovery; existing Ready Certificates/TLS endpoints alone do not exercise fresh issuance. Any staging issuance/renewal canary must be separately within the driver's existing authorized scope and must not expose key material or consume production issuance unnecessarily.

Rollback: GitOps version-only revert to v1.21.2 restores chart/controller images; do not uninstall, delete CRDs or Certificates, or assume rollback reverses issued certificates/external ACME actions. Chart CRDs are templated and enabled, so a Helm downgrade can restore the older schema and drop optional ARI certID status on later writes, causing ARI refetch; this is inference from the exact optional-schema change, not a tested rollback guarantee. Assess preserving newer compatible CRD schema if needed, and verify webhook/API, issuance and certificate readiness after reverting. `crds.keep:true` is deletion retention, not protection against schema downgrade. No new storage version was observed in source compare.

[Official Helm/signature/CRD guidance](https://cert-manager.io/docs/installation/helm/), [upgrade guidance](https://cert-manager.io/docs/installation/upgrade/), [supported versions](https://cert-manager.io/docs/releases/), [API admission readiness check](https://cert-manager.io/docs/installation/kubectl/).

Review stopped at requested boundary; root owns tracker/handoff and later decision.
