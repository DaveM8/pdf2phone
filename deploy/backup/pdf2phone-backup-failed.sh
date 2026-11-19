#!/usr/bin/env bash
# Emails the owner the end of the backup's log (pdf2phone-backup-failed.service,
# started by systemd when pdf2phone-backup.service fails). Uses the app's own
# email settings from .env.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
{
  echo "The pdf2phone backup on $(hostname) failed at $(date '+%Y-%m-%d %H:%M %Z')."
  echo "Your documents are safe on the server; only the copy to the Storage Box didn't happen."
  echo "To see more:  sudo journalctl -u pdf2phone-backup.service -n 100"
  echo
  journalctl -u pdf2phone-backup.service -n 40 --no-pager -o cat
} | docker compose run --rm --no-deps -T pdf2phone pdf2phone email-admin "pdf2phone backup FAILED"
