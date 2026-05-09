resource "google_storage_bucket" "backups" {
  name          = "${var.project_id}-outline-backups"
  location      = var.region
  storage_class = "STANDARD"

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 90
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
