# Authentik blueprints — haynesnetwork (PLAN-011, config-as-code)

These YAML files are **Authentik blueprints** (not Kubernetes manifests). They are
delivered to the Authentik worker as a ConfigMap and applied declaratively by
Authentik's blueprint engine.

## Delivery

`app/kustomization.yaml` bundles the baseline files into a ConfigMap
(`authentik-hnet-blueprints`, `disableNameSuffixHash: true`). `app/helmrelease.yaml`
lists that ConfigMap under `values.blueprints.configMaps`, so the goauthentik chart
mounts it onto the **worker** at `/blueprints/mounted/cm-authentik-hnet-blueprints`.
Authentik discovers every `*.yaml` key there and reconciles it on an interval.

```
configMapGenerator (kustomize)  ->  ConfigMap authentik-hnet-blueprints
        |                                   |
        |  files: 10/20/30-*.yaml           |  values.blueprints.configMaps: [authentik-hnet-blueprints]
        v                                   v
   git (this dir)                     worker /blueprints/mounted/cm-.../  -> blueprint apply
```

## Baseline files (discovered — drift-zero reproduction of the live estate)

| File | Concern | Model(s) | Notes |
|---|---|---|---|
| `10-hnet-brand.yaml` | Brand | `authentik_brands.brand` | Title `haynesnetwork`, logo/favicon/background media paths (`hnet/…`), and the full ~70 KB brand custom CSS. Keyed on `brand_uuid` (the default brand). |
| `20-hnet-flows.yaml` | Flows + stages + bindings + flow policies | `authentik_flows.*`, `authentik_stages_*`, `authentik_policies*` | The four login-surface flows: `default-authentication-flow`, `default-source-authentication`, `default-source-enrollment`, `default-invalidation-flow`. Titles + full stage/policy graph, verbatim from each flow's `…/export/`. |
| `30-hnet-sources.yaml` | Sources | `authentik_sources_plex.plexsource` | The Plex OAuth source `HaynesTower`. Non-secret fields only; `plex_token` is omitted so the partial-update never touches the live secret. |

**Drift-zero:** every attr in the baseline mirrors what is live as of 2026-07-10, so
merging to `main` and letting Flux/Authentik apply them changes nothing observable. The
only delta ever measured against the branding apply-seed was a single trailing newline in
the brand CSS (Authentik strips it on save); the committed CSS matches the *live* value.

### Where is the "groups + policies" baseline file?

The live estate has **no haynesnetwork-specific standalone groups or policies** to
reproduce. The login flows' expression policies are intrinsic to the flow graph and live
in `20-hnet-flows.yaml`; the only net-new group + policy (`mfa-exempt` + the skip policy)
belong to the MFA change and are scoped into `pending/40-hnet-mfa.yaml`. The system
groups (`authentik Admins`, `authentik Read-only`, `grafana_admin`) are deliberately left
unmanaged.

### Deferred (Q-11): OIDC provider + application

`Provider for haynesnetwork` (pk 109) and its application are **not** blueprinted here —
whether to adopt them into GitOps is the plan's open **Q-11** (provider secrets stay in
1Password regardless). A sanitized snapshot lives in `../../exports/` for reference.

## Pending file (NOT discovered)

`pending/40-hnet-mfa.yaml` — the drafted native-account MFA blueprint. It is **not** in
the `configMapGenerator` list, so it is never mounted or applied. See
`pending/README.md` for the owner-present Phase 2 activation runbook.

## Phase 2 — apply / verify / rollback (owner-present)

Blueprints are validated by the worker on apply. There is no separate offline `ak`
validator in this image, so the supported dry run is the worker's own apply against a
mounted copy, watched via the API.

```bash
CTX=haynes-ops ; NS=network
# 0) confirm the ConfigMap merged and mounted
kubectl --context $CTX -n $NS get cm authentik-hnet-blueprints -o jsonpath='{.data}' | tr ',' '\n' | grep -o '"[0-9].*\.yaml"'
kubectl --context $CTX -n $NS exec deploy/authentik-worker -c worker -- ls /blueprints/mounted/cm-authentik-hnet-blueprints

# 1) watch the worker apply the discovered blueprints (status + any errors)
kubectl --context $CTX -n $NS logs deploy/authentik-worker -c worker --tail=200 | grep -i blueprint
#    Authentik lists them under /api/v3/managed/blueprints/ with a `status` (successful|error)
#    and `last_applied_hash`. A drift-zero baseline applies with status=successful, no diffs.

# 2) rollback of the BASELINE = revert this commit. Because it is drift-zero, reverting
#    also changes nothing live; the objects simply stop being managed as code.
```

The brand/flow **content** rollback (back to stock Authentik) remains the API payloads in
`haynesnetwork:docs/ops/authentik-apply-seed/` (`brand-rollback.json`,
`flow-titles-rollback.json`).
