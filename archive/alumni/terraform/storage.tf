resource "google_storage_bucket" "backups" {
  name          = "${var.project_id}-outline-backups"
  location      = var.region
  storage_class = "STANDARD"

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  # NO age-based lifecycle. Retention is count-based and managed by backup.sh
  # (keep N most recent). Reason: if backups silently stop, age-based deletion
  # would empty the bucket exactly when we need it. Count-based freezes the
  # last N until cron resumes. As a safety net, keep noncurrent versions for
  # 30 days so accidental overwrites/deletes are recoverable.
  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 30
    }
    action {
      type = "Delete"
    }
  }

  labels = local.labels
}

resource "google_storage_bucket_iam_member" "vm_backup_writer" {
  bucket = google_storage_bucket.backups.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.outline_vm.email}"
}

# Attachments live on the VM disk via FILE_STORAGE=local — no separate uploads bucket
# or HMAC key needed. The backups bucket above receives nightly tarballs of
# /var/lib/outline/files via scripts/backup.sh.

# ---------------------------------------------------------------------
# Drive cold-tier backup: SA JSON key for rclone → Workspace Shared Drive.
# Why: GCS is the hot tier (90d, lifecycle, fast restore). Drive is the
# cold-tier insurance — if this GCP project ever gets neglected/suspended,
# the org Shared Drive (tied to actively-monitored Workspace billing)
# still has the most recent backups.
# ---------------------------------------------------------------------

resource "google_service_account_key" "outline_vm_drive" {
  service_account_id = google_service_account.outline_vm.name
}

resource "google_secret_manager_secret" "outline_drive_sa_key" {
  secret_id = "outline-drive-sa-key"
  replication {
    auto {}
  }
  labels = local.labels
}

resource "google_secret_manager_secret_version" "outline_drive_sa_key" {
  secret      = google_secret_manager_secret.outline_drive_sa_key.id
  secret_data = base64decode(google_service_account_key.outline_vm_drive.private_key)
}

resource "google_secret_manager_secret_iam_member" "vm_drive_sa_key" {
  secret_id = google_secret_manager_secret.outline_drive_sa_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.outline_vm.email}"
}
