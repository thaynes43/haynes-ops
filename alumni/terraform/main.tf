provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

locals {
  wiki_fqdn = "${var.wiki_subdomain}.${var.domain}"
  labels = {
    org     = "sigo-alumni"
    app     = "outline"
    managed = "terraform"
  }
}

# Resource files (compute, network, secrets, storage, iam) live alongside this one.
# Add them in subsequent commits — keep main.tf as the provider/locals entrypoint.
