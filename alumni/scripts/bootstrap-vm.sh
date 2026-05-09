#!/usr/bin/env bash
# VM startup script: install Docker + compose plugin, prep app dir.
# Idempotent — re-runs on every boot. Keep heavy app deploy out of here.
set -euo pipefail

APP_DIR=/opt/outline
DATA_DIR=/var/lib/outline

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi

mkdir -p "$APP_DIR" "$DATA_DIR"/{postgres,redis,files}
# Outline runs as uid 1001 inside its container — give it ownership of the files dir.
chown -R 1001:1001 "$DATA_DIR/files"
chown -R root:root "$APP_DIR"
chown -R root:root "$DATA_DIR/postgres" "$DATA_DIR/redis"

# Install gcloud (already present on most GCP images, but ensure)
if ! command -v gcloud >/dev/null 2>&1; then
  apt-get update
  apt-get install -y google-cloud-cli
fi

# rclone — used by backup.sh to push cold-tier copies to the Workspace Shared Drive.
if ! command -v rclone >/dev/null 2>&1; then
  apt-get install -y rclone
fi

echo "Bootstrap complete. SCP the compose stack to ${APP_DIR}, then run scripts/load-secrets.sh and 'docker compose up -d'."
