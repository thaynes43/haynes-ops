# Secret RESOURCES only — values are set out-of-band via:
#   gcloud secrets versions add <name> --data-file=-
# This keeps secret values out of Terraform state.
#
# See docs/secrets.md for what each secret is for and how to rotate it.

locals {
  outline_secrets = [
    "outline-secret-key",      # SECRET_KEY: 32-byte hex; randomBytes(32).toString('hex')
    "outline-utils-secret",    # UTILS_SECRET: 32-byte hex; collaborative editing token signing
    "outline-postgres-password",
    "outline-redis-password",
    "outline-minio-root-password",
    "outline-minio-access-key",
    "outline-minio-secret-key",
    "outline-oauth-client-id",
    "outline-oauth-client-secret",
    "outline-smtp-password",   # for outbound email; populate when SMTP is configured
  ]
}

resource "google_secret_manager_secret" "outline" {
  for_each  = toset(local.outline_secrets)
  secret_id = each.key

  replication {
    auto {}
  }

  labels = local.labels
}

resource "google_secret_manager_secret_iam_member" "vm_accessor" {
  for_each  = google_secret_manager_secret.outline
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.outline_vm.email}"
}
