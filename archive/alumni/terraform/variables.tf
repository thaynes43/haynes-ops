variable "project_id" {
  description = "GCP project ID where alumni infra lives. Eval: robust-fin-495718-a9. Prod: TBD."
  type        = string
}

variable "region" {
  description = "Primary GCP region for the VM and supporting resources."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Zone within region for the VM. Pick one in the region's free-tier list if using e2-micro."
  type        = string
  default     = "us-central1-a"
}

variable "domain" {
  description = "Apex domain for the alumni org."
  type        = string
  default     = "sigoalumni.org"
}

variable "wiki_subdomain" {
  description = "Subdomain where Outline is reachable (combined with var.domain)."
  type        = string
  default     = "wiki"
}

variable "admin_email" {
  description = "Email address Caddy uses for Let's Encrypt registration."
  type        = string
  default     = "admin@sigoalumni.org"
}

variable "vm_machine_type" {
  description = "Compute Engine machine type. e2-micro = always-free in us-{central1,east1,west1} (1 instance/account); e2-small ~ $13/mo with headroom."
  type        = string
  default     = "e2-small"
}

variable "vm_disk_size_gb" {
  description = "Boot disk size. 30GB stays inside the always-free standard-PD allowance."
  type        = number
  default     = 30
}
