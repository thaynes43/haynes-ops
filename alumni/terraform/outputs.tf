output "vm_external_ip" {
  description = "Static external IP of the Outline VM. Point a Cloudflare A record for the wiki FQDN at this."
  value       = google_compute_address.outline.address
}

output "wiki_url" {
  description = "Final URL once DNS is in place."
  value       = "https://${local.wiki_fqdn}"
}

output "vm_service_account" {
  description = "Email of the VM service account; used by gcloud secrets IAM bindings."
  value       = google_service_account.outline_vm.email
}

output "backup_bucket" {
  description = "GCS bucket for nightly Postgres + MinIO backups."
  value       = google_storage_bucket.backups.name
}

output "secret_names" {
  description = "Names of Secret Manager secrets that must be populated before the stack will boot."
  value       = sort([for s in google_secret_manager_secret.outline : s.secret_id])
}

output "ssh_command" {
  description = "Convenience: SSH to the VM via IAP tunnel."
  value       = "gcloud compute ssh outline --zone=${var.zone} --tunnel-through-iap"
}
