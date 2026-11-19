#!/usr/bin/env bash
# Encrypted backup of pdf2phone to the Storage Box (docs/BACKUP.md).
# Run as root by pdf2phone-backup.service: nightly at 03:30, and within a minute
# of an account being deleted (pdf2phone-backup.path). Settings: /etc/pdf2phone-backup.env
set -euo pipefail
umask 077

APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
STAGE=/var/tmp/pdf2phone-backup   # fixed: the restore script expects the database copy at this path
TAG="${BACKUP_TAG:-nightly}"
cd "$APP_DIR"

# One backup or restore at a time (the restore script holds this lock and sets PDF2PHONE_BACKUP_LOCKED).
if [ -z "${PDF2PHONE_BACKUP_LOCKED:-}" ]; then
  exec 9>/run/pdf2phone-backup.lock
  flock 9
fi

VOL="$(docker volume inspect pdf2phone_library --format '{{ .Mountpoint }}')"
trap 'rm -rf "$STAGE"' EXIT
rm -rf "$STAGE"
install -d -m 700 -o 1000 -g 1000 "$STAGE"   # 1000 = the app user inside the image

# 1. A consistent copy of the account database. The live file may be mid-write,
#    so it's left out below and this copy is backed up instead.
docker compose run --rm --no-deps -T -v "$STAGE:/stage" pdf2phone pdf2phone backup-db /stage/pdf2phone.db

# 2. Documents and that copy, encrypted by restic, to the Storage Box.
#    _jobs (conversion records) and _backups (the one-off migration archive) aren't needed.
restic backup --host pdf2phone --tag "$TAG" --no-scan \
  --exclude "$VOL/_db" --exclude "$VOL/_jobs" --exclude "$VOL/_backups" \
  "$VOL" "$STAGE"

# 3. Keep about 30 days: the privacy note on the sign-up page promises this.
#    --max-unused 0: really remove deleted data, instead of leaving a few % in place to save work.
#    Skipped for the safety copy at the start of a restore: it could remove the backup being restored.
if [ "$TAG" != pre-restore ]; then
  restic forget --host pdf2phone --keep-within-daily 14d --keep-within-weekly 30d --prune --max-unused 0
fi

# 4. Sundays: read back a sample of what's stored, to catch damage early.
if [ "$TAG" = nightly ] && [ "$(date +%u)" = 7 ]; then
  restic check --read-data-subset=5%
fi
echo "pdf2phone backup done ($TAG)."
