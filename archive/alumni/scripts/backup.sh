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

# Retention is COUNT-based, not age-based. Important: if backups silently stop,
# age-based pruning would empty both tiers exactly when we need them. Count-based
# keeps the most recent N regardless of how old the newest is.
KEEP_GCS=90    # ~90 nightly backups
KEEP_DRIVE=30  # ~30 nightly backups (cold tier insurance)

# --- Hot tier: GCS ---
gcloud storage cp "$PG_DUMP" "gs://${BUCKET}/postgres/" --project="$PROJECT"
gcloud storage cp "$FILES_TGZ" "gs://${BUCKET}/files/" --project="$PROJECT"

# Keep newest KEEP_GCS in each prefix; delete the rest.
prune_gcs() {
  local prefix="$1" keep="$2"
  gcloud storage ls "gs://${BUCKET}/${prefix}/" --project="$PROJECT" 2>/dev/null \
    | sort -r | tail -n +$((keep + 1)) \
    | xargs -r -I{} gcloud storage rm "{}" --project="$PROJECT" --quiet
}
prune_gcs postgres "$KEEP_GCS"
prune_gcs files    "$KEEP_GCS"

# --- Cold tier: Workspace Shared Drive "Backups" → Outline/ ---
RCLONE_OPTS=(--drive-service-account-file="$DRIVE_SA_FILE" --drive-team-drive="$DRIVE_TEAM_ID")
rclone copy "$PG_DUMP" :drive:Outline/ "${RCLONE_OPTS[@]}"
rclone copy "$FILES_TGZ" :drive:Outline/ "${RCLONE_OPTS[@]}"

# Keep newest KEEP_DRIVE files matching each pattern; delete the rest.
prune_drive() {
  local pattern="$1" keep="$2"
  rclone lsf :drive:Outline/ --include "$pattern" "${RCLONE_OPTS[@]}" 2>/dev/null \
    | sort -r | tail -n +$((keep + 1)) \
    | xargs -r -I{} rclone deletefile ":drive:Outline/{}" "${RCLONE_OPTS[@]}"
}
prune_drive 'outline-postgres-*.dump'  "$KEEP_DRIVE"
prune_drive 'outline-files-*.tar.gz'   "$KEEP_DRIVE"

echo "[$TS] backup complete (GCS + Drive)"
