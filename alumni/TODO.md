# Alumni Wiki TODO

Tracking what's left to do *if* evaluation decides Outline is the right tool. Items are roughly ordered by phase, not priority within phase.

## Phase 1 — Evaluation (now)

- [x] Confirm `admin@sigoalumni.org` can sign in via Google OAuth — works
- [x] Confirm a Cloud Identity Free board member can sign in — works on Internal user type
- [ ] Have 1–2 board members actually create docs in Outline; gather their UX feedback
- [ ] Decide: keep Outline, or pivot to another tool (Slite/Notion/Google Sites/etc.)
- [ ] Test the export → import migration to Outline Cloud as a dry run (the safety valve must actually work, not just exist)

## Phase 2 — Production migration (after Phase 1 says "yes, Outline")

Currently running on the `admin@sigoalumni.org` $300 free trial in project `robust-fin-495718-a9` ("My First Project"). Need to move to a clean org-owned project before that credit expires (~90 days from trial start).

- [ ] Create production GCP project (suggested name: `sigo-alumni-prod`) under the org's billing account
- [ ] Create new state bucket: `sigo-alumni-prod-tf-state`
- [ ] Update `terraform/backend.tf` with new bucket; run `tofu init -migrate-state`
- [ ] Update `terraform/terraform.tfvars` and `compose/.env`/`load-secrets.sh` references with new project ID
- [ ] Re-apply infra in prod project (creates fresh VM, IP, secrets, bucket)
- [ ] Re-populate all 10 secrets in the new project's Secret Manager
- [ ] Create new OAuth client in the new project (redirect URI same: `https://wiki.sigoalumni.org/auth/google.callback`)
- [ ] Export Outline data from eval VM, import to prod VM
- [ ] Update Cloudflare A record to new VM's static IP
- [ ] Tear down eval project (keep backups bucket for 90 days as insurance)
- [ ] Set up GCP **billing budget + alert** on the prod project (email at 50%, 90%, 100% of monthly target)

## Phase 3 — Operational hardening

- [ ] Wire `scripts/backup.sh` into cron on the VM:
      `echo "0 3 * * * root /opt/outline/scripts/backup.sh >> /var/log/outline-backup.log 2>&1" | sudo tee /etc/cron.d/outline-backup`
- [ ] Test the backup → restore flow end-to-end (restore to a scratch VM, confirm data integrity)
- [ ] Pin the Outline image to a specific version (currently `:latest` — risky for unattended self-hosting)
- [ ] Investigate the "unhealthy" docker healthcheck on Outline (cosmetic now, but pollutes monitoring later)
- [ ] Add Cloud Monitoring **uptime check** for `https://wiki.sigoalumni.org` with email alerts to `admin@sigoalumni.org`
- [ ] Configure SMTP for Outline notifications (password resets, mentions, share notifications). Options: SendGrid free tier, Postmark, Workspace SMTP relay
- [ ] Populate `outline-smtp-password` secret once SMTP provider is chosen
- [ ] Document and store the **break-glass admin recovery flow** (what to do if `admin@sigoalumni.org` loses access)

## Phase 4 — Security & governance

- [ ] Move Cloudflare zone (if currently personal) to org-owned Cloudflare account
- [ ] Create scoped Cloudflare API token + store in Secret Manager
- [ ] Add Cloudflare provider to Terraform; manage the wiki A record as code
- [ ] Optional: enable Cloudflare proxy + switch Caddy to DNS-01 challenge (gives you Cloudflare WAF + hides VM IP)
- [ ] Enforce 2FA on all `@sigoalumni.org` accounts via Workspace admin policy
- [ ] Review GCP IAM bindings; remove any access beyond `admin@sigoalumni.org` and the VM service account
- [ ] Document onboarding/offboarding checklist for board members (CIF account, Workspace mailbox if role-holder, Outline access)

## Phase 5 — Content + adoption

- [ ] Set up Outline workspace structure using the role-playbook pattern (President / Secretary / Treasurer / Admin playbooks + Bylaws + Annual Calendar + Decision Log)
- [ ] Migrate any existing institutional knowledge from the legacy Gmail account → Outline
- [ ] Onboard board members; demo the search and editing flow
- [ ] Establish a quarterly "is this still working?" review cadence

## Phase 6 — Eventual succession

- [ ] Annual: re-test the Outline → Outline Cloud migration path so it stays a 1-hour task, not an aspiration
- [ ] When Tom is ~6 months from rotating off: execute migration to Outline Cloud OR identify successor admin willing to take over self-hosting
- [ ] Hand off the GCP project, Cloudflare zone, and 1Password vault to successor admin or wind down infra cleanly
