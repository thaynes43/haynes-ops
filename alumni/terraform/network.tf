data "google_compute_network" "default" {
  name = "default"
}

resource "google_compute_address" "outline" {
  name         = "outline-static-ip"
  region       = var.region
  address_type = "EXTERNAL"
  description  = "Static external IP for the Outline VM. Pin a Cloudflare A record at this address."
}

# HTTP/HTTPS open to the world — Caddy terminates TLS with Let's Encrypt.
resource "google_compute_firewall" "outline_web" {
  name        = "outline-allow-web"
  network     = data.google_compute_network.default.self_link
  description = "Public HTTP/HTTPS to the Outline VM."

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["outline-vm"]
}

# SSH only via IAP tunnel. No public 22.
# Use: gcloud compute ssh outline --tunnel-through-iap
resource "google_compute_firewall" "outline_iap_ssh" {
  name        = "outline-allow-iap-ssh"
  network     = data.google_compute_network.default.self_link
  description = "SSH via IAP only — no public port 22 exposure."

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["35.235.240.0/20"] # IAP source range, fixed by Google
  target_tags   = ["outline-vm"]
}
