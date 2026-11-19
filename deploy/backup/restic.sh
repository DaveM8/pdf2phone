#!/usr/bin/env bash
# restic with pdf2phone's backup settings loaded, e.g.:
#   sudo ~/pdf2phone/deploy/backup/restic.sh snapshots
#   sudo ~/pdf2phone/deploy/backup/restic.sh check
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run it with sudo."; exit 1; }
set -a; . /etc/pdf2phone-backup.env; set +a
exec restic "$@"
