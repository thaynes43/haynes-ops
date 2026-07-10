# PENDING — native-account MFA (PLAN-011 Phase 2, NOT ACTIVE)

`40-hnet-mfa.yaml` is a **drafted, un-applied** Authentik blueprint. It is excluded from
discovery: it is NOT listed in `../../kustomization.yaml`'s `configMapGenerator`, so it is
never mounted onto the worker. Nothing here affects a running login until an owner
activates it.

## What it does (once active)

1. Creates group `mfa-exempt` containing `hnet-e2e` + `hnet-e2e-member` (automation).
2. Adds expression policy `hnet-mfa-exempt-skip` (skips MFA for exempt members;
   **fail-closed** — enforces MFA if the pending user can't be resolved).
3. Reconfigures the existing order-30 authenticator-validation stage of
   `default-authentication-flow`: `not_configured_action: skip → configure`,
   `device_classes: [totp, webauthn]`, `configuration_stages` = the WebAuthn + TOTP
   setup stages (enroll on first challenge).
4. Sets that order-30 stage-binding's `policy_engine_mode: any → all` so the shipped
   webauthn-passwordless skip policy AND the exempt-skip policy must both pass to run.
5. Binds the exempt-skip policy to the order-30 binding.

**Plex-source logins are unaffected** — they authenticate through
`default-source-authentication` (a login-only flow with no password/MFA stage). This
blueprint only touches `default-authentication-flow` (the native username+password path).
Note: a native user who signs in via the *Plex button* also bypasses MFA (that is the
source path); MFA bites the username+password path. This matches owner requirement #2.

## Activation runbook (owner-present)

> Lockout safety first — prove the cycle on a THROWAWAY native test account before the
> stage covers real accounts. Keep an `ak` shell (`kubectl exec … -- ak shell`) open as
> break-glass.

```bash
CTX=haynes-ops ; NS=network
```

1. **Prevent flip-flop:** edit `../20-hnet-flows.yaml` and REMOVE the two entries this
   file takes ownership of — the `authentik_stages_authenticator_validate…` stage
   `default-authentication-mfa-validation` and the order-30 `authentik_flows.flowstagebinding`
   (pk `3640815a-…`). Otherwise the baseline (skip) and this blueprint (configure) fight
   on each reconcile.
2. **Promote the file into discovery.** Move it up one level and add it to the generator:
   ```bash
   git mv pending/40-hnet-mfa.yaml 40-hnet-mfa.yaml
   # then add `- blueprints/40-hnet-mfa.yaml` to app/kustomization.yaml configMapGenerator.files
   ```
   (No helmrelease change — it rides the same `authentik-hnet-blueprints` ConfigMap.)
3. **Dry-run / apply:** merge to `main`; Flux updates the ConfigMap; the worker discovers
   `40-hnet-mfa.yaml`. Watch it apply and check for errors:
   ```bash
   kubectl --context $CTX -n $NS logs deploy/authentik-worker -c worker --tail=200 | grep -i blueprint
   # confirm the objects: group mfa-exempt, policy hnet-mfa-exempt-skip, stage reconfigured
   ```
4. **Prove on the test account** (native, NOT in mfa-exempt): first login forces WebAuthn/
   TOTP enrollment, then challenges on subsequent logins; no session without a factor.
5. **Enroll `thaynes`:** 1Password passkey (WebAuthn) + backup TOTP; verify login.
6. **Verify exemptions & pass-through:** `hnet-e2e` Playwright sign-in stays green (no
   challenge); a real Plex-source login sees NO challenge.

## Rollback

- **Fast (live):** unbind via `ak` shell / API — flip the order-30 stage
  `not_configured_action` back to `skip`, or delete the exempt policy binding; the SSO
  session flow returns to pre-MFA behavior immediately.
- **Config:** revert the promotion commit (move the file back to `pending/`, remove it
  from `configMapGenerator`, restore the two entries in `20-hnet-flows.yaml`).

## Open questions carried to Phase 2

- **Q-05 / Q-10:** akadmin repair — the 1Password `AUTHENTIK_BOOTSTRAP_PASSWORD` is
  stale; rotate via `ak` shell so akadmin is a recoverable break-glass account (with MFA,
  or interactive-login disabled — owner's call).
