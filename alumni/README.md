# SIGO Alumni Association Infrastructure

Self-hosted infrastructure for the SIGO 501(c)(7) fraternity alumni association. Lives in `haynes-ops` for convenience but **everything in this directory belongs to the alumni org, not Tom personally.**

## Ownership Boundary

| Resource | Owner |
| --- | --- |
| GCP project + billing | `admin@sigoalumni.org` (org account) |
| Domain `sigoalumni.org` | Org's Cloudflare account |
| OAuth credentials | Org's GCP project |
| All app secrets | GCP Secret Manager in the org's project |
| Terraform state bucket | Org's GCP project (GCS) |
| This subdirectory in haynes-ops | Org content; code only, no resource ownership |

All application secrets (database passwords, OAuth client secret, Outline app secret, etc.) live exclusively in GCP Secret Manager — no SOPS, no 1Password, no `.env` files in git. The VM's service account reads them at runtime via IAM.

If Tom ever rotates off the board, this entire folder must be extractable as a unit (`git filter-repo --path alumni/`) and the GCP project + Cloudflare zone transfer ownership without touching haynes-ops.

## Stack

- **Compute Engine VM** running Docker Compose: Outline + Postgres + Redis + Caddy
- **Attachments**: Outline's built-in `FILE_STORAGE=local` — files at `/var/lib/outline/files` on the VM disk
- **GCP Secret Manager** for all app secrets (no SOPS here — that's a Flux pattern)
- **Cloudflare** DNS (zone managed there, not in GCP)
- **Google OAuth** as Outline's auth provider — board members sign in with their `name@sigoalumni.org` Cloud Identity
- **Dual-tier nightly backup** (`pg_dump` + tar of `files/`):
  - **Hot**: GCS `*-outline-backups` bucket, 90-day lifecycle, fast restore
  - **Cold**: Workspace Shared Drive `Backups/Outline/`, 30-day retention, insurance against GCP project neglect (Workspace bills get attention because mailboxes go silent)

> **Why not S3/MinIO/GCS for attachments?** Tried both. MinIO worked but adds a container + browser-side subdomain + CORS. GCS via S3-interop hit cascading GCP org-policy constraints (HMAC creation, UBLA) and AWS SDK v3 quirks. For a 5-user docs wiki, `FILE_STORAGE=local` is the right architectural call: zero S3 complexity, files backed up nightly. Worst-case data loss is 24h of attachments. Wiki content is in Postgres which gets dumped on the same schedule.

## Migration Path (the safety valve)

If Tom rotates off or the org outgrows self-hosting:

1. `docker compose exec outline yarn db:export` → produces a portable archive
2. Sign up for [Outline Cloud](https://www.getoutline.com/pricing) at $10/user/mo
3. Import the archive
4. Update DNS to point at Outline Cloud
5. Tear down the GCP VM (keep the GCS backup bucket for 6 months as insurance)

This migration must be a tested 1-hour task, not an aspiration. Re-test annually.

## Current State (2026-05-08)

**Mode: evaluation.** Using the `admin@sigoalumni.org` $300 free trial under project `robust-fin-495718-a9` ("My First Project") to validate Outline. Production migration to a fresh project (`sigo-alumni-prod`) happens after evaluation succeeds.

## First-Time Setup

We use **OpenTofu** (`tofu` CLI) — drop-in for Terraform, MPL-licensed.

Manual one-time steps before `tofu apply`:

```bash
# Set vars
export PROJECT_ID="robust-fin-495718-a9"   # eval; replace for prod
export REGION="us-central1"
export TF_STATE_BUCKET="${PROJECT_ID}-tf-state"

# Confirm auth
gcloud auth list
gcloud config set project "$PROJECT_ID"

# Enable APIs
gcloud services enable \
  compute.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  iam.googleapis.com \
  iap.googleapis.com \
  drive.googleapis.com    # for Drive cold-tier backups

# Create the OpenTofu state bucket (idempotent — chicken-and-egg)
gcloud storage buckets create "gs://${TF_STATE_BUCKET}" \
  --project="$PROJECT_ID" \
  --location="$REGION" \
  --uniform-bucket-level-access \
  --public-access-prevention
gcloud storage buckets update "gs://${TF_STATE_BUCKET}" --versioning

# Then:
cd terraform/
cp terraform.tfvars.example terraform.tfvars  # edit values
tofu init
tofu plan
tofu apply   # ~$13/mo VM cost meter starts here
```

Once the VM is up, add a Cloudflare A record `wiki.sigoalumni.org → <vm_static_ip>` (output by terraform). Then SSH/SCP the compose stack and start it (covered separately in `docs/runbook.md`).

## Layout

```
alumni/
├── README.md          # this file
├── terraform/         # GCP infra (VM, network, secrets, IAM, GCS, DNS notes)
├── compose/           # Docker Compose stack for Outline + deps
├── scripts/           # bootstrap, secret-loader, backup
└── docs/
    ├── runbook.md     # day-to-day ops: deploy, restore, rotate secrets
    └── secrets.md     # which secrets exist and how to set/rotate them
```

## Out of Scope (for v0)

- Cloudflare DNS in Terraform (manual A record for now; add the `cloudflare` provider later if useful)
- HA / multi-zone (single VM is right for a 5-user board)
- Authentik or other centralized SSO (Google OAuth direct is sufficient)
- Monitoring beyond a simple uptime check (add Cloud Monitoring uptime check later)
