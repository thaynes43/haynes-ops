# Authentik live-estate export (PLAN-011 Phase 1 — read-only snapshot)

Sanitized `GET`-only capture of the live Authentik estate
(`authentik.haynesnetwork.com`, 2026.5.3) taken 2026-07-10. It is the source-of-truth the
`app/blueprints/` baseline was authored from, and the reference for the deferred Q-11
provider/application adoption. **Nothing here is applied** — this directory sits outside
the Flux Kustomization path (`app/`), so it is never rendered into the cluster.

## Sanitization

Secrets/tokens are replaced with `1P://HaynesKube/<item>/<field>` references; no
credential is committed:

- `providers-oauth2.json` — every `client_secret` **and** `client_id` → 1P reference.
- `sources-plex.json` — `plex_token` → 1P reference (`client_id` is Authentik's built-in
  public default Plex application id, left as-is).
- `groups.json` — embedded user objects reduced to `{pk, username}` (emails/uid hashes stripped).
- `users.json` is **not** committed (external-user PII).

## Inventory

| File | Contents |
|---|---|
| `brands.json` | The single default brand (uuid `de1b7109-…`) incl. the full brand custom CSS. |
| `flows.json` | All 14 flow instances (pk, slug, designation, title). |
| `stages.json` | All 18 stages (pk, name, component). |
| `stage-bindings.json` | All 20 flow-stage bindings (order, target, stage). |
| `policies.json` / `policy-bindings.json` | All policies + bindings. |
| `groups.json` | The 3 groups. |
| `sources-all.json` / `sources-plex.json` | Sources incl. the Plex OAuth source. |
| `providers-oauth2.json` | All 6 OAuth2 providers (incl. pk 109 haynesnetwork). |
| `applications.json` | All 7 applications. |
| `propertymappings-scope.json` | Provider scope mappings (incl. the `plex_user_id` mapping). |
| `flow-exports/*.export.json` | Per-flow blueprint exports for the four login-surface flows (the literal inputs to `20-hnet-flows.yaml`). |

## Re-export

Read-only, curl (Cloudflare bans the python UA — error 1010):

```bash
TOKEN=$(kubectl --context haynes-ops -n frontend get secret homepage-secret \
  -o jsonpath='{.data.HOMEPAGE_VAR_AUTHENTIK_API_TOKEN}' | base64 -d)   # never echo it
B=https://authentik.haynesnetwork.com/api/v3 ; UA='User-Agent: curl/8.5.0'
curl -sS "$B/core/brands/?page_size=100"      -H "Authorization: Bearer $TOKEN" -H "$UA"
curl -sS "$B/flows/instances/<slug>/export/"  -H "Authorization: Bearer $TOKEN" -H "$UA"
```
