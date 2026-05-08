resource "google_service_account" "outline_vm" {
  account_id   = "outline-vm"
  display_name = "Outline VM service account"
  description  = "Runtime identity for the Outline Compute Engine VM. Reads secrets, writes backups."
}

resource "google_project_iam_member" "outline_vm_logwriter" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.outline_vm.email}"
}

resource "google_project_iam_member" "outline_vm_metricwriter" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.outline_vm.email}"
}
