#!/usr/bin/env bash
# Nightly backup: pg_dump + MinIO data → GCS.
# Cron suggestion (run on VM as root):
#   0 3 * * * /opt/outline/scripts/backup.sh >> /var/log/outline-backup.log 2>&1
set -euo pipefail

PROJECT=robust-fin-495718-a9
BUCKET=robust-fin-495718-a9-outline-backups
APP_DIR=/opt/outline
TS=$(date -u +%Y%m%dT%H%M%SZ)

cd "$APP_DIR"

# Postgres dump (streamed, never lands on disk except inside the container)
docker compose exec -T postgres pg_dump -U outline -d outline -Fc \
  | gcloud storage cp - "gs://${BUCKET}/postgres/outline-${TS}.dump" --project="$PROJECT"

# MinIO data — tar from host bind mount
tar -czf - -C /var/lib/outline minio \
  | gcloud storage cp - "gs://${BUCKET}/minio/minio-${TS}.tar.gz" --project="$PROJECT"

echo "[$TS] backup complete"
