# Setting up backups like pdf2phone's

**For Claude (or a person) working on another app on the owner's server.** pdf2phone has a backup system that works well. This file describes it so your app can have the same kind of backups. Copy the ideas, but give your app its own files, names and keys. Don't share or change pdf2phone's.

Read `deploy/SHARED-SERVER.md` first (same folder as this file). It has the server's shared rules: ports, memory, the busy 02:00–02:15 window, and keeping 15% of the disk free.

The owner is dyslexic and reads on an iPhone. Instructions for them should be short and numbered, with one command per line, and should say which machine each command runs on.

Before you write any code, agree a short plan with the owner: what gets backed up, when, how long copies are kept, and how a restore works.

pdf2phone's working version, for reference (in the pdf2phone repository):

- `docs/BACKUP.md`: the owner's setup and restore guide. Copy its style.
- `deploy/backup/`: the scripts and systemd units.
- `src/pdf2phone/web/backup.py`: the app's part (database copy, re-erasing deleted accounts after a restore).

---

## What the owner gets

- An encrypted backup every night, sent to another data centre, with no help from them.
- An email if a backup fails. No email means it's working.
- Old copies deleted after a fixed time (30 days for pdf2phone), so a privacy promise stays true.
- One restore command that is safe to run, and stops with the site left offline if anything goes wrong.
- A written guide (`docs/BACKUP.md`) for setting it up once (about 30 minutes) and for restoring.

---

## How it's built

### 1. Where the backups go

- **restic**, run by root on the host (`apt-get install restic`). It encrypts, removes duplicates, and lets you keep or delete copies by date.
- **A Hetzner Storage Box (BX11) in Helsinki.** The server is in Falkenstein, so one data centre's problem can't hit both. The owner already has this box.
- **Each app has its own sub-account** with its own folder. pdf2phone's folder is `pdf2phone`. Make one for your app (for example `yesdeputy`), so neither app can read or delete the other's backups.
- **Each app has its own:**
  - SSH key, for example `/root/.ssh/storagebox-<app>`.
  - Host alias in root's `~/.ssh/config`, using port 23 and `IdentitiesOnly yes`. pdf2phone's alias is `bubox`; pick a different one.
  - restic password file, for example `/root/.config/restic/<app>-password`, made with `openssl rand -base64 32`.
  - Settings file, for example `/etc/<app>-backup.env` (mode 600), with `RESTIC_REPOSITORY`, `RESTIC_PASSWORD_FILE` and `RESTIC_CACHE_DIR=/var/cache/restic-<app>`.
- The owner keeps a copy of the restic password in their password manager. Without it, nobody can open the backups. Say this clearly in the guide.
- **Hetzner snapshots of the Storage Box** run every day at 05:00, keeping 7. Hetzner makes them, not the server, so even someone who broke into the server couldn't delete them. These are set for the whole box, so they already cover your sub-account. Check with the owner.

### 2. Never back up a live database file

A database that's being written to can be copied half-finished. Make a consistent copy first, put it in a staging folder, and back up that copy instead of the live files:

- **SQLite:** `VACUUM INTO '/path/copy.db'`. It's consistent while the app is writing, and it's compact, so erased rows don't survive in free pages. pdf2phone does this in `backup.py:copy_database`.
- **PostgreSQL:** `pg_dump -Fc`.
- **Elasticsearch:** register a filesystem snapshot repository (`path.repo`) and take a snapshot through the API, then let restic back up the snapshot folder. Don't copy the data directory while Elasticsearch is running. For a big index, also back up the source files you'd rebuild it from, and check disk space first (rule 9 in `SHARED-SERVER.md`).

Put the staging folder **outside** the app's data folder (pdf2phone uses `/var/tmp/pdf2phone-backup`), make it mode 700, and delete it when the script exits (`trap 'rm -rf "$STAGE"' EXIT`). Leave out anything you don't need, such as caches, job records and logs.

Don't back up `.env`. The owner keeps it in their password manager. Say in the guide which secrets a restore needs.

### 3. The backup script (`deploy/backup/<app>-backup.sh`)

In order:

1. `set -euo pipefail` and `umask 077`.
2. Take a lock (`flock` on `/run/<app>-backup.lock`), so a backup and a restore never run at once.
3. Make the consistent database copy (step 2 above).
4. `restic backup --host <app> --tag nightly ...` on the data folder and the staging folder, with `--exclude` for the live database and anything not needed.
5. Delete old copies: `restic forget --host <app> --keep-within-daily 14d --keep-within-weekly 30d --prune --max-unused 0`. `--max-unused 0` really removes deleted data instead of leaving a few percent behind. Change the days to suit your app, and keep them the same as anything your app promises its users.
6. On Sundays: `restic check --read-data-subset=5%`, to catch damage early.

Always pass `--host <app>` so the commands only ever touch your app's copies.

### 4. Running it automatically (systemd, as root)

- `<app>-backup.service`: `Type=oneshot`, `EnvironmentFile=/etc/<app>-backup.env`, `OnFailure=<app>-backup-failed.service`, `Nice=10`, `IOSchedulingClass=idle`, and a generous `TimeoutStartSec`.
- `<app>-backup.timer`: `OnCalendar` at a quiet time, `RandomizedDelaySec=10min`, `Persistent=true` (so a backup missed while the server was off runs when it starts).
  - **Not 02:00–02:15** (Yes Deputy's nightly pipeline).
  - **Not 03:20–03:45** (pdf2phone's backup). For example 04:15.
  - Write the time in your app's section of `SHARED-SERVER.md`.
- `<app>-backup-failed.service`: emails the owner the last 40 lines of the backup's log (`journalctl -u <app>-backup.service`), using the app's own email settings. The subject is fixed, for example "<app> backup FAILED". Never put anything a visitor typed into an email header.
- `deploy/backup/install.sh`: copies the units into `/etc/systemd/system/` (filling in paths with `sed`), runs `systemctl daemon-reload`, enables the timer, and shows when the next backup will run. It checks first that restic and the settings file exist, and it's safe to run again.
- `deploy/backup/restic.sh`: a small wrapper that loads the settings file and runs restic, so the owner can type `sudo .../restic.sh snapshots`.

Email: the Brevo account is shared by both apps (300 a day in total). A failure email now and then is fine.

### 5. Restoring (`deploy/backup/<app>-restore.sh`)

One script, run with `sudo`, taking `latest` or the start of a snapshot ID. In order:

1. Take the same lock as the backup.
2. Stop the app.
3. **Back up what's there now first**, with a different tag (`pre-restore`) and **without** the `forget` step, so it can't delete the backup you're about to restore. This way a wrong restore can be undone.
4. `restic restore` into a work folder, then put the files in place with `rsync -a --delete`, and the database copy back in place of the live database. Fix file ownership.
5. Any checks your app needs before it goes back online (see 6 below).
6. Start the app again.

Use `trap ... ERR` to print a clear "RESTORE STOPPED" message, and **leave the app stopped** if any step fails. Stopping is safer than coming back half-restored.

Test a restore at least once, on a copy or with a rehearsal, before the guide says it works.

### 6. If your app lets people delete their account

pdf2phone promises that a deleted account is gone for good, so a restore must never bring one back. If your app makes a similar promise, copy this:

- When an account is deleted, keep one small record: an HMAC of the email (with a key kept only in `.env`, never the email itself), the date, and who deleted it.
- The app also touches a file (`_db/backup-requested`). A systemd `.path` unit (`PathChanged=`) sees it and runs a backup within a minute, so the newest backup always has the newest list of deletions.
- On restore, read the deletion records from the **newest** backup, and erase again everyone whose deletion is dated on or after the restored backup's UTC date, before the app starts (`backup.py:reapply_deletions`). Print how many were erased, never who.
- The database copy stores a fingerprint of the HMAC key, so a restore with the wrong key refuses instead of silently missing deleted accounts.
- Keep the retention days and any privacy wording in step. pdf2phone's says "about 30 days" (restic) and "within about 6 weeks" (adding the 7 daily Storage Box snapshots).

### 7. Tests and docs

- Test the app's part (the database copy, re-erasing deleted accounts) with the app's normal tests. Shell scripts can't be tested on the Mac, so keep them short and plain.
- Write `docs/BACKUP.md` for the owner, like pdf2phone's:
  - What it does, in a few bullets.
  - Setup steps, numbered, one command per line, starting `new$` for the server.
  - How to check on it now and then.
  - How to restore, and how to restore on a brand-new server.
  - "If something goes wrong": common errors and what to do.
- Add your app's backup details to its section of `deploy/SHARED-SERVER.md`: units, time, settings file paths, SSH alias and sub-account. Add a line to its change log.

---

## Don'ts

- Don't reuse pdf2phone's key, alias, password file, settings file, lock file, staging folder or unit names.
- Don't run restic commands on another app's repository or sub-account.
- Don't schedule backups in 02:00–02:15 or on top of pdf2phone's 03:30.
- Don't restore pdf2phone's data by hand. Only its own restore script may do that.
- Don't let staging copies fill the disk. Keep 15% free (rule 9).
