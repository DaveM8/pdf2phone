#!/usr/bin/env bash
# Installs and starts the backup timers (docs/BACKUP.md, step 6). Safe to run again.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run it with sudo."; exit 1; }
[ -f /etc/pdf2phone-backup.env ] || { echo "Make /etc/pdf2phone-backup.env first (docs/BACKUP.md, step 5)."; exit 1; }
command -v restic >/dev/null || { echo "Install restic first: sudo apt-get install -y restic"; exit 1; }
APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
VOL="$(docker volume inspect pdf2phone_library --format '{{ .Mountpoint }}')"
for unit in pdf2phone-backup.service pdf2phone-backup.timer pdf2phone-backup.path pdf2phone-backup-failed.service; do
  sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@VOLUME@|$VOL|g" "$APP_DIR/deploy/backup/$unit" > "/etc/systemd/system/$unit"
done
chmod 600 /etc/pdf2phone-backup.env
systemctl daemon-reload
systemctl enable --now pdf2phone-backup.timer pdf2phone-backup.path
systemctl list-timers pdf2phone-backup.timer --no-pager
echo "Installed. Next backup is shown above; run one now with: sudo systemctl start pdf2phone-backup.service"
