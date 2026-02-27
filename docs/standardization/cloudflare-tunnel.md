## Cloudflare Tunnel (cloudflared) plan — Traefik external ingress

Goal: eliminate intermittent Cloudflare `523 Origin Unreachable` events by **removing inbound WAN reachability from the equation**. We’ll do this by running a **Cloudflare Tunnel** (`cloudflared`) inside Kubernetes and routing public traffic through it to `traefik-external`.

This document is written as a step-by-step plan so it can be copied into Cursor Plan Mode later.

### Status (2026-02-27)

- **Implemented**: tunnel connector deployed in-cluster, stable DNS target created (`ingress-ext.haynesnetwork.com`), and public app hostnames repointed to `ingress-ext` via External-DNS.
- **Key learning**: Cloudflare “wizard” style tunnels (`docker run ... --token ...`) and “Published application” routes can make the tunnel **remotely managed** and override local `config.yaml` ingress rules (including forcing `http_status:404`). The working pattern is a **locally-managed token** built from **account tag + tunnel id + tunnel secret** (the same pattern used in the example repos).

---

### High-level decisions (what we’re optimizing for)

- **Single URL per app**: keep `https://<app>.haynesnetwork.com` as the only URL (no “-internal” duplicates).
- **Phase 1 (simpler)**: *no split DNS* — even LAN clients will go out to Cloudflare and come back through the tunnel. This makes validation straightforward.
- **Phase 2**: add monitoring so we can safely introduce split DNS later.
- **Phase 3**: split DNS — LAN clients resolve the *same* public hostnames to `traefik-external` directly (bypassing Cloudflare), while WAN continues through the tunnel.

---

### Background / current state (relevant to the 523 issue)

- External services use `IngressRoute` objects with:
  - `kubernetes.io/ingress.class: traefik-external`
  - `external-dns.alpha.kubernetes.io/target: haynesnetwork.com`
- `external-dns-cloudflare` is configured with `sources: ["crd", "ingress", "traefik-proxy"]` and `--cloudflare-proxied`.
- This causes records like `authentik.haynesnetwork.com` to be published as `CNAME haynesnetwork.com`.
- When Cloudflare returns `523`, it means the selected Cloudflare POP couldn’t reach the origin (today: your WAN IP path).

With a tunnel, Cloudflare does **not** need to initiate a connection to your WAN IP. Cloudflare connects to a tunnel that your `cloudflared` pods keep open outbound.

---

## Architecture overview

### Without split DNS (Phase 1)

- Client (LAN or WAN) → Cloudflare → Cloudflare Tunnel → `cloudflared` (in-cluster) → `traefik-external` → app Service

### With split DNS (Phase 3)

- WAN client → Cloudflare → tunnel → `traefik-external` → app Service
- LAN client → LAN DNS override → `traefik-external` directly → app Service

Same hostname, different DNS answer depending on where you are.

---

## References (read-up)

Cloudflare:
- Cloudflare Tunnel (concepts + setup): `https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/`
- `cloudflared` configuration (`config.yaml`, ingress rules): `https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/configure-tunnels/local-management/configuration-file/`
- Public hostname routing for tunnels: `https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/routing-to-tunnel/`
- Routes UI terminology (“Published application”, “Private hostname”, etc): `https://developers.cloudflare.com/cloudflare-one/networks/routes/`

Traefik:
- Forwarded headers / real client IP concepts (Traefik proxy behavior): `https://doc.traefik.io/traefik/routing/entrypoints/#forwarded-headers`

GitOps examples in this workspace:
- `onedr0p-home-ops` Cloudflare tunnel app:
  - `kubernetes/apps/network/cloudflare-tunnel/app/helmrelease.yaml`
  - `kubernetes/apps/network/cloudflare-tunnel/app/externalsecret.yaml`
  - `kubernetes/apps/network/cloudflare-tunnel/app/dnsendpoint.yaml`
- `bjw-s-labs-home-ops` cloudflared app:
  - `kubernetes/apps/network/cloudflared/app/helmrelease.yaml`
  - `kubernetes/apps/network/cloudflared/app/externalsecret.yaml`
  - `kubernetes/apps/network/cloudflared/app/dnsEndpoint.yaml`

---

## Phase 0 — prerequisites (mostly manual / external systems)

### 0.1 Cloudflare prerequisites (manual)

You need:
- A Cloudflare Zone for `haynesnetwork.com`
- Cloudflare Zero Trust enabled (for Tunnel management)
- A Tunnel created (name it something like `haynes-ops-main`)

Outputs we will use in Git:
- **Tunnel ID** (UUID)
- **Tunnel secret** (base64-ish secret used to build the token)
- **Account tag** (Cloudflare account identifier)

Notes:
- Prefer creating the tunnel via `cloudflared tunnel create --credentials-file cloudflare-tunnel.json ...` so you can reliably extract `AccountTag`, `TunnelID`, and `TunnelSecret`.
- Avoid relying on the “run with Docker token” wizard token for GitOps; it leads to **remotely-managed tunnel config** which can override `cloudflared` ingress rules from `config.yaml`.

### 0.2 Secret management prerequisites (manual)

This repo uses External Secrets (1Password Connect). Store tunnel values in 1Password under item `cloudflared`:

- `CLOUDFLARE_ACCOUNT_TAG`
- `CLOUDFLARE_TUNNEL_ID`
- `CLOUDFLARE_TUNNEL_SECRET`

Optional (not used by the locally-managed pattern):

- `CLOUDFLARED_TUNNEL_TOKEN` (the dashboard/docker run token)
- `CLOUDFLARED_API_TOKEN` (useful for read-only inspection; not required for the in-cluster connector)

An agent cannot create or read these secrets safely without your secret store access.

### 0.3 Certificate issuance prerequisites (important)

If you plan to **remove inbound port-forwards** (80/443) as part of the tunnel migration, certificate issuance/renewal must **not** depend on inbound HTTP reachability (ACME **HTTP-01**).

- This cluster already satisfies that requirement: `cert-manager` uses **ACME DNS-01 via Cloudflare** for Let’s Encrypt.
- Warning: if you later change `cert-manager` Issuers to HTTP-01 (or otherwise remove/break Cloudflare DNS-01), it can silently become a dependency again and renewals may fail once ports are closed.

---

## Phase 1 — deploy the tunnel in-cluster (no split DNS yet)

### 1.1 Add a `cloudflared` app to the `network` namespace (GitOps)

Pattern to follow: the `onedr0p-home-ops` `cloudflare-tunnel` app.

Create a new app under `kubernetes/main/apps/network/` (proposed structure):
- `kubernetes/main/apps/network/cloudflare-tunnel/ks.yaml`
- `kubernetes/main/apps/network/cloudflare-tunnel/app/helmrelease.yaml`
- `kubernetes/main/apps/network/cloudflare-tunnel/app/externalsecret.yaml`
- `kubernetes/main/apps/network/cloudflare-tunnel/app/dnsendpoint.yaml` (Phase 1.3)
- optionally `GrafanaDashboard` + ServiceMonitor like the examples

Implementation details (recommended defaults):
- **Replicas**: 2 (avoid single-pod tunnel outages)
- **Health checks**: `/ready` on metrics port (`TUNNEL_METRICS`)
- **Metrics**: enable scrape via ServiceMonitor
- **Ingress rules**:
  - Start with a wildcard for your external domain:
    - `hostname: "*.haynesnetwork.com"`
    - `service: https://traefik-external.network.svc.cluster.local:443`
  - Default: `http_status:404`

Important gotcha (Traefik redirect loop):

- Routing to Traefik over `http://...:80` causes **redirect loops** (`ERR_TOO_MANY_REDIRECTS`) because `traefik-external` redirects `web` → `websecure`.
- Prefer `https://...:443` and configure `originRequest` appropriately:
  - simplest bring-up: `noTLSVerify: true`
  - better end-state: set `originServerName: ingress-ext.haynesnetwork.com` and remove `noTLSVerify` once validated

### 1.2 Reconcile and verify `cloudflared` is healthy (manual commands)

You can validate using `kubectl` locally; cluster state can also be inspected via Kubernetes MCP (pods/logs/events) when available.

- Pods ready:
  - `kubectl -n network get pods -l app.kubernetes.io/name=cloudflare-tunnel`
- Logs show “connected” / no auth errors:
  - `kubectl -n network logs -l app.kubernetes.io/name=cloudflare-tunnel --tail=200 -f`
- Metrics endpoint responds (optional):
  - `kubectl -n network port-forward svc/cloudflare-tunnel 8080:8080`
  - then browse `http://localhost:8080/ready`

Success criteria:
- Two pods Running/Ready
- No repeated reconnect/auth failures

Rollback:
- revert the Git commits that add the app, reconcile Flux

### 1.3 Create a stable DNS target name for external-dns (GitOps)

Today, many `IngressRoute`s publish `CNAME haynesnetwork.com` because of:
`external-dns.alpha.kubernetes.io/target: haynesnetwork.com`

With a tunnel, we want external routes to publish:
- `CNAME ingress-ext.haynesnetwork.com` (example name)

Then we create **one** record:
- `ingress-ext.haynesnetwork.com` → `${TUNNEL_ID}.cfargotunnel.com` (CNAME)

This is exactly what the example repos do with a `DNSEndpoint` CR:
- `onedr0p-home-ops`: `external.turbo.ac` → `${CLOUDFLARE_TUNNEL_ID}.cfargotunnel.com`
- `bjw-s-labs-home-ops`: `ingress-ext.bjw-s.dev` → `<tunnel_id>.cfargotunnel.com`

In this repo:
- Add a `DNSEndpoint` in `network` namespace (or wherever your `external-dns-cloudflare` watches CRDs) that creates:
  - `dnsName: ingress-ext.haynesnetwork.com`
  - `recordType: CNAME`
  - `targets: ["<tunnel_id>.cfargotunnel.com"]`

Success criteria:
- Cloudflare DNS shows the CNAME for `ingress-ext.haynesnetwork.com`

Important gotcha (Error 1033):

- If you delete/recreate the tunnel, the **Tunnel ID changes**.
- If `ingress-ext.haynesnetwork.com` still points at the old `<tunnel_id>.cfargotunnel.com`, Cloudflare returns **Error 1033** (“unable to resolve”).
- Fix by updating the `DNSEndpoint` target to the new tunnel id and letting External-DNS reconcile.

### 1.4 Update external `IngressRoute` targets to point at `ingress-ext`

For each external `IngressRoute` currently using:
`external-dns.alpha.kubernetes.io/target: haynesnetwork.com`

Change to:
`external-dns.alpha.kubernetes.io/target: ingress-ext.haynesnetwork.com`

Known locations (non-exhaustive; expand as you implement):
- `kubernetes/main/apps/network/authentik/app/ingressroute.yaml`
- `kubernetes/main/apps/photos/immich/server/ingressroute.yaml`
- `kubernetes/main/apps/office/paperless-ngx/app/ingressroute.yaml`
- `kubernetes/main/apps/network/traefik/config/ingress-routes/traefik-external/*`

Success criteria:
- External-DNS records for apps become `CNAME ingress-ext.haynesnetwork.com` (not `haynesnetwork.com`)

Rollback:
- revert the annotation changes (records will revert)

### 1.5 “No split DNS” validation (manual)

From LAN:
- confirm `https://<app>.haynesnetwork.com` works
- optionally verify Cloudflare headers present (means you went through Cloudflare)

From WAN/cellular:
- confirm the same URLs work

Optional: temporarily remove/disable your router port forwards for 80/443 (after you are confident).

---

## Phase 2 — Monitoring strategy for tunnel-first + split DNS later

We want to detect two classes of failure:

1) **Public path** down (Cloudflare/tunnel/external)
2) **LAN path** down while public is up (or vice-versa) once split DNS is introduced

### 2.1 Gatus checks: Public (Cloudflare/tunnel) path

Add (or keep) endpoints like:
- `url: https://authentik.haynesnetwork.com/`

These validate:
- Cloudflare edge
- tunnel connectivity
- Traefik routing
- app health (at least HTTP-level)

### 2.2 Gatus checks: “LAN direct” path (even before split DNS)

Because Gatus runs **inside the cluster**, it won’t automatically use your LAN DNS overrides later.
So for “LAN path” checks, don’t rely on DNS — test the internal routing path directly.

Recommended approach:
- Send HTTP to the in-cluster Traefik service
- Set the `Host` header to the public hostname so Traefik routes the same as external

Example pattern (conceptual):
- `url: http://traefik-external.network.svc.cluster.local/`
- `headers: { Host: authentik.haynesnetwork.com }`
- condition: status code is 200/302/etc depending on the app

This validates:
- Traefik is reachable
- Traefik is routing that hostname correctly
- Upstream service is reachable

It does *not* validate:
- Cloudflare edge behavior
- tunnel connectivity

### 2.3 DNS monitoring for split DNS (future)

Once you introduce split DNS via Unifi:
- Add Gatus DNS endpoints that query Unifi DNS and assert:
  - `authentik.haynesnetwork.com` resolves to `192.168.40.206` (Traefik external VIP)
- Add Gatus DNS endpoints that query a public resolver (e.g. `1.1.1.1`) and assert:
  - `authentik.haynesnetwork.com` resolves to the tunnel target (CNAME chain to `cfargotunnel.com`)

This gives quick visibility:
- “Public DNS correct?”
- “LAN DNS override correct?”

Agent constraint:
- configuring Unifi DNS is outside cluster GitOps unless you manage it via `external-dns-unifi` (see Phase 3).

---

## Phase 3 — Introduce split DNS (after tunnel is proven stable)

### 3.1 Decide how to manage Unifi DNS overrides

Options:
- **Option A (manual)**: create Unifi DNS host overrides for selected external hostnames
- **Option B (GitOps)**: extend/adjust `external-dns-unifi` so it can manage `haynesnetwork.com` LAN overrides
  - currently it is scoped to `haynesops.com` and excludes `haynesnetwork.com`
  - changing this requires care to avoid record ownership collisions

### 3.2 Implement split DNS (manual or GitOps)

For each external hostname you want “LAN direct”:
- set Unifi DNS `A` record to `192.168.40.206` (Traefik external VIP)

### 3.3 Verify split DNS with the Phase 2 monitoring in place

Success criteria:
- Public Gatus checks remain green
- LAN-direct (Traefik service + Host header) checks remain green
- DNS checks show:
  - public: tunnel route
  - LAN: VIP route

Rollback:
- remove Unifi overrides (LAN returns to public DNS/tunnel)

---

## Implementation notes / gotchas (Traefik + Cloudflare specifics)

- **Client IPs**:
  - With tunnel, Traefik will see the immediate client as `cloudflared`.
  - Apps should rely on `CF-Connecting-IP` / `X-Forwarded-For` for real client IP.
  - You may want to configure Traefik forwarded header trust appropriately (don’t blindly trust all in-cluster sources).

- **TLS between cloudflared → Traefik**:
  - Prefer HTTPS to Traefik (`:443`) to avoid redirect loops.
  - Bring-up can use `noTLSVerify: true`.
  - Harden by setting `originServerName: ingress-ext.haynesnetwork.com` (SNI) once validated.

- **external-dns record shape**:
  - The easiest migration is changing the `external-dns .../target` annotation from `haynesnetwork.com` to `ingress-ext.haynesnetwork.com` so every app record repoints cleanly.

---

## Work breakdown (copy/paste into Plan Mode)

### Phase 1: tunnel deployed, no split DNS

- Create `cloudflare-tunnel` app manifests (HelmRelease + ExternalSecret + config.yaml)
- Add `DNSEndpoint` for `ingress-ext.haynesnetwork.com` → `<tunnel_id>.cfargotunnel.com`
- Update external `IngressRoute` annotations to target `ingress-ext.haynesnetwork.com`
- Reconcile Flux and validate from LAN + WAN
- Optionally close router 80/443 port-forwards after confidence

### What we actually changed in `haynes-ops` (implemented)

- Added `kubernetes/main/apps/network/cloudflare-tunnel/` (Flux `Kustomization` + app manifests)
- Added `DNSEndpoint` for `ingress-ext.haynesnetwork.com`
- Repointed public app records by changing `external-dns.alpha.kubernetes.io/target` from `haynesnetwork.com` → `ingress-ext.haynesnetwork.com` (Plex, Authentik, Immich, Paperless, Open WebUI, and Traefik external ingressroutes)
- Added `/cloudflare-tunnel.json` to `.gitignore` (local credentials artifact)

### Phase 2: monitoring foundation

- Add Gatus endpoints for public URLs (tunnel path)
- Add Gatus endpoints that hit Traefik service directly with `Host:` header (LAN-direct path)
- Add DNS assertions (public resolver vs Unifi resolver) once split DNS is introduced

### Phase 3: split DNS

- Choose manual vs GitOps for Unifi DNS overrides
- Implement overrides for selected hostnames
- Validate with Gatus (public + lan-direct + DNS)

---

## Remaining TODOs (validation + WAN cleanup)

### Validate tunnel viability (before removing rollback options)

- Verify from WAN/cellular:
  - `https://authentik.haynesnetwork.com`
  - `https://ai.haynesnetwork.com`
  - `https://immich.haynesnetwork.com`
- Confirm `cloudflare-tunnel` pods stay connected (no flapping) and External-DNS remains stable.
- Add/verify Gatus checks (public URL checks + “LAN direct” Traefik service checks with Host headers).

### WAN / router cleanup (recommended: disable first, delete later)

- Disable (don’t delete yet) UniFi port forwards for inbound 80/443 (and any other “public ingress” forwards).
- After 24–48h of confidence, delete the forwards.

### Cloudflare DNS cleanup (WAN public IP records)

- Decide what to do with existing A records that point to your WAN IP (e.g. apex `haynesnetwork.com`, `www`):
  - keep temporarily for rollback
  - or remove/disable once tunnel is proven
  - or repoint to the tunnel pattern (Cloudflare supports proxied CNAME/flattening)
- Review `kubernetes/main/apps/network/cloudflare-ddns/`:
  - If you remove WAN ingress, `cloudflare-ddns` may no longer be needed for public app traffic.
  - Decide whether to keep it for other records, or disable it to prevent it from continuing to update WAN-IP A records unnecessarily.

