#!/usr/bin/env bash
# Put a backup of pdf2phone back (docs/BACKUP.md, "Restoring").
#
#   sudo ~/pdf2phone/deploy/backup/pdf2phone-restore.sh            # the newest backup
#   sudo ~/pdf2phone/deploy/backup/pdf2phone-restore.sh 1a2b3c4d   # a chosen one
#
# The app stays stopped until everyone who deleted their account after that
# backup was made has been erased again. If any step fails, it stays stopped.
set -euo pipefail
umask 077
[ "$(id -u)" = 0 ] || { echo "Run it with sudo."; exit 1; }

APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
STAGE=/var/tmp/pdf2phone-backup   # where the backup script keeps the database copy
WORK=/var/tmp/pdf2phone-restore
WANT="${1:-latest}"
cd "$APP_DIR"
set -a; . /etc/pdf2phone-backup.env; set +a

exec 9>/run/pdf2phone-backup.lock   # no backups while restoring
flock 9
export PDF2PHONE_BACKUP_LOCKED=1

trap 'rm -rf "$WORK"' EXIT
trap 'echo; echo "RESTORE STOPPED at the step above. Nothing after it was done. If pdf2phone was already stopped, it has NOT been restarted, on purpose."' ERR
rm -rf "$WORK"
install -d -m 700 "$WORK"

# Which backup, when it was made (UTC date), and where the volume was on that server.
restic snapshots --host pdf2phone --json > "$WORK/snapshots.json"
read -r SNAP SINCE OLDVOL < <(python3 - "$WORK/snapshots.json" "$WANT" "$STAGE" <<'EOF'
import datetime, json, re, sys
snaps, want, stage = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
snaps.sort(key=lambda s: s["time"])
pick = snaps[-1:] if want == "latest" else [s for s in snaps if s["id"].startswith(want)]
if len(pick) != 1:
    sys.exit(f"No single backup matches {want!r}. List them with: sudo ~/pdf2phone/deploy/backup/restic.sh snapshots")
s = pick[0]
t = datetime.datetime.fromisoformat(re.sub(r"\.\d+", "", s["time"]))
vol = [p for p in s["paths"] if p != stage]
print(s["id"], t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d"), vol[0])
EOF
)
[ -n "${OLDVOL:-}" ] || { echo "Couldn't read the list of backups; nothing was changed."; exit 1; }
echo "Restoring backup ${SNAP:0:8}, made on $SINCE (UTC)."

docker compose create >/dev/null   # makes the volume on a new server
VOL="$(docker volume inspect pdf2phone_library --format '{{ .Mountpoint }}')"
docker compose stop

# 1. Back up what's there now first (if anything), so a wrong restore can be undone.
#    This also makes the newest backup hold the newest list of deleted accounts.
if [ -f "$VOL/_db/pdf2phone.db" ]; then
  BACKUP_TAG=pre-restore "$APP_DIR/deploy/backup/pdf2phone-backup.sh"
fi

# 2. The newest list of deleted accounts, from the newest backup.
install -d -m 700 -o 1000 -g 1000 "$WORK/newest"
NEWEST="$(restic snapshots --host pdf2phone --json | python3 -c \
  'import json, sys; print(sorted(json.load(sys.stdin), key=lambda s: s["time"])[-1]["id"])')"
restic dump "$NEWEST" "$STAGE/pdf2phone.db" > "$WORK/newest/pdf2phone.db"
chown 1000:1000 "$WORK/newest/pdf2phone.db"

# 3. Put the chosen backup in place.
restic restore "$SNAP" --target "$WORK/files"
rsync -a --delete --exclude /_db --exclude /_jobs --exclude /_backups "$WORK/files$OLDVOL/" "$VOL/"
install -d "$VOL/_db"
rm -f "$VOL/_db/pdf2phone.db" "$VOL/_db/pdf2phone.db-wal" "$VOL/_db/pdf2phone.db-shm"
cp "$WORK/files$STAGE/pdf2phone.db" "$VOL/_db/pdf2phone.db"
rm -rf "$VOL/_jobs"
chown -R 1000:1000 "$VOL"

# 4. Erase again everyone who deleted their account after this backup was made.
docker compose run --rm --no-deps -T -v "$WORK/newest:/newest:ro" pdf2phone \
  pdf2phone reapply-deletions --newer /newest/pdf2phone.db --since "$SINCE"

# 5. Only now does the site come back.
docker compose up -d
echo "Restore done. Check: docker compose ps (wait for healthy)."
