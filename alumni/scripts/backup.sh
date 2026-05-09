#!/usr/bin/env bash
# Backup Outline: postgres dump + files tarball, dual-tier.
# - HOT tier: GCS (90-day lifecycle, primary, fast restore via gcloud)
# - COLD tier: Workspace Shared Drive "Backups" (30-day, org-visible, insurance against
#   GCP project neglect — Workspace bills get attention because mailboxes go silent)
#
# Cron suggestion (run on VM as root):
#   0 3 * * * /opt/outline/scripts/backup.sh >> /var/log/outline-backup.log 2>&1
set -euo pipefail

PROJECT=robust-fin-495718-a9
BUCKET=robust-fin-495718-a9-outline-backups
DRIVE_TEAM_ID=0AB-lOEPrwe4jUk9PVA            # Shared Drive: "Backups"
DRIVE_SA_FILE=/opt/outline/drive-sa-key.json # Written by load-secrets.sh
APP_DIR=/opt/outline
TS=$(date -u +%Y%m%dT%H%M%SZ)

cd "$APP_DIR"
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# Snapshot to local tempfiles so we can ship to two destinations without dumping twice.
PG_DUMP="$TMPDIR/outline-postgres-${TS}.dump"
docker compose exec -T postgres pg_dump -U outline -d outline -Fc > "$PG_DUMP"

FILES_TGZ="$TMPDIR/outline-files-${TS}.tar.gz"
tar -czf "$FILES_TGZ" -C /var/lib/outline files

# --- Hot tier: GCS ---
gcloud storage cp "$PG_DUMP" "gs://${BUCKET}/postgres/" --project="$PROJECT"
gcloud storage cp "$FILES_TGZ" "gs://${BUCKET}/files/" --project="$PROJECT"

# --- Cold tier: Workspace Shared Drive "Backups" → Outline/ ---
RCLONE_OPTS=(--drive-service-account-file="$DRIVE_SA_FILE" --drive-team-drive="$DRIVE_TEAM_ID")
rclone copy "$PG_DUMP" :drive:Outline/ "${RCLONE_OPTS[@]}"
rclone copy "$FILES_TGZ" :drive:Outline/ "${RCLONE_OPTS[@]}"
# Prune older than 30 days from Drive (GCS prunes via bucket lifecycle).
rclone delete --min-age 30d :drive:Outline/ "${RCLONE_OPTS[@]}"

echo "[$TS] backup complete (GCS + Drive)"
