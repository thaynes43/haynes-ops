# Omni service-account kubeconfig (headless, non-interactive cluster access)

## Why this exists

`kubectl`/`flux`/`task` access to the `haynes-ops` cluster authenticates through
**Omni's interactive OIDC flow** (browser login). The exec credential in the default
kubeconfig (`./kubeconfig`) opens a browser to `https://haynes.omni.siderolabs.io`.
That token **expires on long sessions**, and renewing it requires a browser — which
means it can only be refreshed **from home**, and an automation/agent can never
complete it at all.

Symptom when the token is dead (any `kubectl`/`flux`/`task` cluster call):

```
get-token: authentication error: authcode-browser error: ... authorization code flow
error: oauth2 error: ... context deadline exceeded
✗ failed to get API group resources: unable to retrieve the complete list of server
  APIs: ... getting credentials: exec: executable kubectl failed with exit code 1
```

This blocks **verification** (reading pod/HelmRelease/flux state) while away — note
that GitOps changes still apply on their own (Flux reconciles in-cluster regardless),
so the gap is *observing* the cluster, not *changing* it.

**Fix:** an **Omni service account** — a long-lived, non-interactive credential
designed for automation/CI. It produces a kubeconfig that auths with a key instead of
the browser, so `kubectl` works **from anywhere, no browser, no per-session expiry**
(until the key's TTL).

## Facts for this cluster

- Omni endpoint: `https://haynes.omni.siderolabs.io`
- Cluster name: `haynes-ops`
- `omnictl` is installed (`brew "omnictl"` / Archfile); `OMNICONFIG` →
  `kubernetes/main/bootstrap/omni/haynes-ops-omniconfig.yaml` (set by `.envrc`).
- `omnictl` is already authenticated when running from home.

## Setup (run once, from home)

Exact flags vary slightly by Omni version — confirm with
`omnictl serviceaccount create --help` and `omnictl kubeconfig --help`.

```bash
# 1) Create a READ-ONLY (Reader) service account with a long-lived key.
#    Reader = read-only cluster access: fixes the verification gap with minimal
#    blast radius. (See "Role choice" below before picking a higher role.)
omnictl serviceaccount create --use-user-role=false --role Reader --ttl 8760h haynes-ops-agent
#    -> prints:
#         OMNI_ENDPOINT=https://haynes.omni.siderolabs.io
#         OMNI_SERVICE_ACCOUNT_KEY=<base64 key>
#    SAVE THE KEY IN 1PASSWORD (e.g. item "omni" / field OMNI_SERVICE_ACCOUNT_KEY).
#    It is a credential — do not commit it or paste it into chat/tickets.

# 2) Generate a kubeconfig that auths via the service account (no browser).
export OMNI_ENDPOINT=https://haynes.omni.siderolabs.io
export OMNI_SERVICE_ACCOUNT_KEY=<key from step 1>
omnictl kubeconfig ./kubeconfig-haynes-sa --cluster haynes-ops --service-account
```

## Use it

Point the workstation/agent at the SA kubeconfig with the key in the environment
(load the key from 1Password via `.envrc`/direnv so it's never on disk in clear text):

```bash
export OMNI_ENDPOINT=https://haynes.omni.siderolabs.io
export OMNI_SERVICE_ACCOUNT_KEY=op://<vault>/omni/OMNI_SERVICE_ACCOUNT_KEY   # via op run / direnv
export KUBECONFIG=./kubeconfig-haynes-sa
kubectl get pods -A          # works headless, from anywhere
flux get kustomizations -A   # read-only with a Reader SA
```

The exec plugin in `kubeconfig-haynes-sa` is non-interactive: as long as
`OMNI_ENDPOINT` + `OMNI_SERVICE_ACCOUNT_KEY` are in the environment, no browser opens.

## Role choice (security tradeoff)

- **`Reader` (recommended start):** read-only. Covers all *verification* — pods,
  logs, `flux get`, HelmRelease status — which is the actual gap when away.
  `flux reconcile` (annotates a resource = a mutation) will be **denied**, but Flux
  self-reconciles on its interval anyway, so forced reconcile is a convenience, not a
  necessity.
- **`Operator`/higher:** also allows mutations (forced reconciles, `kubectl apply`).
  Only grant if we deliberately want the agent to drive changes; a long-lived
  higher-privilege key is a bigger blast radius. Per repo policy, persistent changes
  go through Git + Flux anyway, so prefer keeping this key read-only.

## Rotation / revocation

```bash
omnictl serviceaccount list
omnictl serviceaccount renew haynes-ops-agent   # before TTL expiry
omnictl serviceaccount destroy haynes-ops-agent # revoke (e.g. if the key leaks)
```
Rotate the key in 1Password whenever you renew. Destroying the SA immediately
invalidates the key everywhere it's used.

## Related gap (do later)

Flux's own `gotk_*` controller metrics are **not** scraped by Prometheus here, so
HelmRelease/Kustomization reconcile **errors** aren't visible remotely (only inferable
from pod restart-times). Wire up the Flux ServiceMonitors / PodMonitors so reconcile
health is observable without `kubectl`.
