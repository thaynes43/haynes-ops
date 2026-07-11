# Kavita — authentication posture & break-glass

Kavita (0.9.0.2) authenticates users via **Authentik OIDC** (Authentik provider pk 110,
Authority `https://authentik.haynesnetwork.com/application/o/kavita/`). As of **2026-07-11**
local password login is disabled for non-admin users (OIDC-only), with an admin break-glass
that Kavita enforces and cannot be turned off.

## Where the auth config actually lives (NOT in this repo)

Kavita's OIDC behaviour is **runtime state on the `kavita` PVC**, not GitOps:

| Setting | Storage | Authoritative | Change needs restart? |
| --- | --- | --- | --- |
| Authority / ClientId / Secret | `/kavita/config/appsettings.json` (`OpenIdConnectSettings`) | this file | **yes** (wired into the auth handler at startup) |
| `ProviderName`, `DisablePasswordAuthentication`, `AutoLogin`, `ProvisionAccounts`, roles/libs | SQLite `kavita.db` → `ServerSetting` row `Key=40` (`OidcConfiguration`, a JSON blob) | the DB | **no** — read fresh from the DB per request, effective immediately |

Because these live on the PVC, **a PVC rebuild resets them to defaults** (button label back to
"OpenID Connect", password auth re-enabled). Re-apply via the admin UI (Server Settings → OpenID
Connect) after any restore-from-scratch.

## Data Protection keys (why OIDC survives restarts)

Kavita persists ASP.NET Core Data-Protection keys **in the DB** (`DataProtectionKeys` table,
`PersistKeysToDbContext` + a stable `SetApplicationName`). The OIDC `state`/`correlation`/`nonce`
cookies are protected by that key ring, so a completed handshake unprotects correctly across pod
restarts. A one-off `Unable to unprotect the message.State` error only affects a login that was
**in flight across a pod restart** (its state cookie was minted by a key ring that changed mid-
flow); it does not recur for logins started after the pod is stable. There is nothing to fix — do
not add a filesystem keys dir; keys are in `kavita.db` on the PVC.

## Current settings (2026-07-11)

```
ProviderName                  = "Log in with Haynesnetwork"   # OIDC button label
DisablePasswordAuthentication = true                          # OIDC-only for non-admins
AutoLogin                     = false
Enabled                       = true
```

`DisablePasswordAuthentication=true` (a) hides the username/password form on the login page and
(b) rejects `POST /api/Account/login` for **non-admins**. **Admins are exempt** — this is the
built-in break-glass and Kavita does not allow disabling it. API-key (OPDS) logins also bypass it.

## BREAK-GLASS — recover admin / re-enable password login

The Kavita admin account is `hnetadmin` (roles Admin+Login). Its password is in the cluster
secret `kavita-secret` key `KAVITA_ADMIN_PASS` (namespace `media`):

```sh
kubectl get secret -n media kavita-secret -o jsonpath='{.data.KAVITA_ADMIN_PASS}' | base64 -d
```

### 1. Preferred — no DB edit, works even if OIDC is completely down

Admins are exempt from `DisablePasswordAuthentication`, so you can always password-login:

1. Browse to `https://kavita.haynesnetwork.com/login?forceShowPassword=true&skipAutoLogin=true`
   (`forceShowPassword=true` reveals the hidden password form; `skipAutoLogin=true` stops the
   OIDC auto-redirect).
2. Log in as `hnetadmin` with `KAVITA_ADMIN_PASS`.
3. To re-enable password login for everyone: Server Settings → OpenID Connect → uncheck
   **Disable Password Authentication** → Save. Effective on the next request (no restart).

### 2. Last resort — raw SQLite edit of the ServerSetting blob

Only if the UI/API is unreachable. The pod image has **no `sqlite3` and no `python3`**, and the DB
is live in WAL mode, so do NOT edit it in-place while Kavita is running (corruption risk). Safe
procedure — scale down, edit via a throwaway sqlite pod that mounts the PVC, scale up:

```sh
# a) stop Kavita so the DB is quiescent
kubectl -n media scale deploy/kavita --replicas=0
kubectl -n media rollout status deploy/kavita --timeout=120s   # waits for 0

# b) run the exact UPDATE against the DB on the PVC (surgical string replace on the JSON blob)
kubectl -n media run kavita-dbfix --rm -i --restart=Never \
  --image=keinos/sqlite3:latest \
  --overrides='{"spec":{"containers":[{"name":"kavita-dbfix","image":"keinos/sqlite3:latest",
    "command":["sh","-c","sqlite3 /kavita/config/kavita.db \"UPDATE ServerSetting SET Value = replace(Value, '\''\"DisablePasswordAuthentication\":true'\'', '\''\"DisablePasswordAuthentication\":false'\'') WHERE Key = 40;\""],
    "volumeMounts":[{"name":"cfg","mountPath":"/kavita/config"}]}],
    "volumes":[{"name":"cfg","persistentVolumeClaim":{"claimName":"kavita"}}]}}'

# c) bring Kavita back
kubectl -n media scale deploy/kavita --replicas=1
kubectl -n media rollout status deploy/kavita --timeout=240s
```

The bare SQL (for reference / any sqlite3 you have that can open the PVC copy):

```sql
UPDATE ServerSetting
SET Value = replace(Value,
      '"DisablePasswordAuthentication":true',
      '"DisablePasswordAuthentication":false')
WHERE Key = 40;   -- Key 40 == OidcConfiguration
```

To also restore the default button label, replace
`"ProviderName":"Log in with Haynesnetwork"` with `"ProviderName":"OpenID Connect"` the same way.

## OIDC → admin linkage (open item for the owner)

Kavita links an OIDC login to a local account by **`sub` (stored as `AspNetUsers.OidcId`) then by
email claim**. Today `hnetadmin`'s email is `manofoz@gmail.com`, which no Authentik user has — the
owner's Authentik identity is `thaynes` / `admin@haynesnetwork.com`. So clicking **Log in with
Haynesnetwork** as the owner lands in a **separate, non-admin** account, not `hnetadmin`.

This is safe (admin access is preserved via the password break-glass above), but if you want the
OIDC button to log the owner straight into the admin account, set `hnetadmin`'s Kavita email to the
owner's Authentik email so the first OIDC login auto-links:

- Server Settings is not where user email lives — edit it under Users, or on next OIDC login the
  email-match links automatically once the addresses agree.
- Alternative: keep the emails as-is, log in once via OIDC to provision the account, then promote
  that account to Admin.
