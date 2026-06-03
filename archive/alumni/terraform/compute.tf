data "google_compute_image" "ubuntu" {
  family  = "ubuntu-2204-lts"
  project = "ubuntu-os-cloud"
}

resource "google_compute_instance" "outline" {
  name         = "outline"
  machine_type = var.vm_machine_type
  zone         = var.zone
  tags         = ["outline-vm"]
  labels       = local.labels

  boot_disk {
    initialize_params {
      image = data.google_compute_image.ubuntu.self_link
      size  = var.vm_disk_size_gb
      type  = "pd-standard"
    }
  }

  network_interface {
    network = data.google_compute_network.default.self_link
    access_config {
      nat_ip = google_compute_address.outline.address
    }
  }

  service_account {
    email  = google_service_account.outline_vm.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  metadata_startup_script = file("${path.module}/../scripts/bootstrap-vm.sh")

  allow_stopping_for_update = true

  lifecycle {
    ignore_changes = [
      metadata["ssh-keys"],
      # Bootstrap script content is baked into the VM at creation. Changes to the
      # script file should NOT trigger VM replacement (which would lose data).
      # To update bootstrap behavior, recreate the VM intentionally:
      #   tofu apply -replace=google_compute_instance.outline
      metadata_startup_script,
    ]
  }
}
