"""The app's side of backups (the server side is deploy/backup/; see docs/BACKUP.md).

copy_database()     a consistent copy of the account database for the nightly
                    backup, made while the app may be writing to it.
reapply_deletions() after a restore, erase again everyone who deleted their
                    account after the restored backup was made, using the
                    deletion records from the newest backup.  The restore script
                    runs it before the app starts, so a restore never brings a
                    deleted account back.
"""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
from contextlib import closing
from pathlib import Path

from .users import DB_DIR, DB_FILE, UserStore, email_hash


def key_check(key: str) -> str:
    """A fingerprint of PDF2PHONE_RECORD_KEY (it can't be turned back into the key)."""
    return hmac.new(key.encode(), b"pdf2phone record key check", hashlib.sha256).hexdigest()


def copy_database(library: Path, dest: Path, key: str) -> None:
    """VACUUM INTO gives a consistent, compact copy (no leftover free pages that
    might still hold erased rows), even while the app is writing.  The copy also
    records which PDF2PHONE_RECORD_KEY made its deletion records: a restore with
    a different key couldn't recognise deleted accounts, so it refuses."""
    if len(key) < 32:
        raise RuntimeError("PDF2PHONE_RECORD_KEY isn't set.")
    src = library / DB_DIR / DB_FILE
    if not src.is_file():
        raise FileNotFoundError(f"No account database at {src}")
    dest.unlink(missing_ok=True)
    with closing(sqlite3.connect(src)) as db:
        db.execute("VACUUM INTO ?", (str(dest),))
    with closing(sqlite3.connect(dest)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS backup_info (key_check TEXT NOT NULL)")
        db.execute("DELETE FROM backup_info")
        db.execute("INSERT INTO backup_info (key_check) VALUES (?)", (key_check(key),))
        db.commit()


def check_key(db_path: Path, key: str) -> None:
    """Raise unless ``db_path`` (a backup's database copy) was made with this key."""
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as db:
        has = db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'backup_info'").fetchone()
        row = db.execute("SELECT key_check FROM backup_info").fetchone() if has else None
    if row is None or not hmac.compare_digest(row[0], key_check(key)):
        raise RuntimeError("This PDF2PHONE_RECORD_KEY isn't the one the backups were made with, so deleted "
                           "accounts couldn't be recognised. Put the original key in .env and try again.")


def deletion_records(db_path: Path) -> list[dict]:
    """The deletion records in another copy of the database (read-only)."""
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'deletions'").fetchone():
            return []
        return [dict(r) for r in db.execute("SELECT email_hash, removed, removed_by FROM deletions ORDER BY id")]


def reapply_deletions(store: UserStore, newer: list[dict], since: str, key: str) -> int:
    """Erase every account in the (restored) ``store`` whose owner deleted it on
    or after ``since``, the UTC date (YYYY-MM-DD) the restored backup was made,
    and copy across the deletion records it doesn't have.  Returns how many
    accounts were erased.

    Older records are copied but never acted on: an account with that email in
    the restored backup must then be a later sign-up by the same person.  (Only
    a sign-up on the very day of a deletion, before that night's backup, is
    ambiguous; it's erased, because bringing back a deleted account is worse.)"""
    if len(key) < 32:
        raise RuntimeError("PDF2PHONE_RECORD_KEY isn't set, so deleted accounts can't be recognised.")
    have = {(r["email_hash"], r["removed"], r["removed_by"]) for r in store.deletion_records()}
    for r in newer:
        rec = (r["email_hash"], r["removed"], r["removed_by"])
        if rec not in have:
            store.add_deletion_record(*rec)
            have.add(rec)
    by_hash = {email_hash(key, u.email): u for u in store.all_users() if not u.is_admin}
    erased = 0
    for r in newer:
        user = by_hash.pop(r["email_hash"], None) if r["removed"] >= since else None
        if user is not None:
            store.erase(user.id)  # the record is already there (copied above)
            erased += 1
    return erased
