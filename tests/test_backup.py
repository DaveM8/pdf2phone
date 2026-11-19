"""Backups: the database copy, and a restore never bringing a deleted account back."""

import shutil
import sqlite3
import time

import pytest

pytest.importorskip("argon2")

from pdf2phone import cli  # noqa: E402
from pdf2phone.web import backup  # noqa: E402
from pdf2phone.web.users import BACKUP_REQUEST, DB_DIR, DB_FILE, USERS_DIR, UserStore, email_hash  # noqa: E402

KEY = "k" * 40
TODAY = time.strftime("%Y-%m-%d", time.gmtime())
PW = "a long enough password"


def make_live(tmp_path):
    store = UserStore(tmp_path / "live")
    admin = store.create("owner@example.com", PW, is_admin=True)
    ann = store.create("ann@example.com", PW)
    bob = store.create("bob@example.com", PW)
    for u in (ann, bob):
        store.root(u.id).mkdir(parents=True, exist_ok=True)
        (store.root(u.id) / "doc.txt").write_text(f"{u.email}'s private document")
    return store, admin, ann, bob


def take_backup(store, dest):
    """What the nightly backup keeps: a database copy plus the document folders."""
    dest.mkdir()
    backup.copy_database(store.library, dest / DB_FILE, KEY)
    shutil.copytree(store.library / USERS_DIR, dest / USERS_DIR)


def restore(snapshot, lib):
    """What the restore script puts back."""
    (lib / DB_DIR).mkdir(parents=True)
    shutil.copy(snapshot / DB_FILE, lib / DB_DIR / DB_FILE)
    shutil.copytree(snapshot / USERS_DIR, lib / USERS_DIR)
    return UserStore(lib)


def test_database_copy_is_complete_and_standalone(tmp_path):
    store, admin, ann, bob = make_live(tmp_path)
    dest = tmp_path / "copy.db"
    backup.copy_database(store.library, dest, KEY)  # while the live store is still open
    with sqlite3.connect(dest) as db:
        emails = {r[0] for r in db.execute("SELECT email FROM users")}
    assert emails == {"owner@example.com", "ann@example.com", "bob@example.com"}
    assert not (tmp_path / "copy.db-wal").exists()
    with pytest.raises(FileNotFoundError):
        backup.copy_database(tmp_path / "nothing-here", tmp_path / "x.db", KEY)


def test_deleting_an_account_asks_for_a_backup(tmp_path):
    store, admin, ann, bob = make_live(tmp_path)
    marker = store.library / DB_DIR / BACKUP_REQUEST
    assert not marker.exists()
    store.delete_account(ann.id, by="user", key=KEY)
    assert marker.read_text().strip().isdigit()  # a timestamp, nothing about the person


def test_restoring_an_older_backup_erases_accounts_deleted_since(tmp_path):
    live, admin, ann, bob = make_live(tmp_path)
    take_backup(live, tmp_path / "snapshot")      # last night's backup still has Ann
    live.delete_account(ann.id, by="user", key=KEY)  # today Ann deletes her account
    newest = tmp_path / "newest.db"               # ...and the backup that follows records it
    backup.copy_database(live.library, newest, KEY)

    restored = restore(tmp_path / "snapshot", tmp_path / "restored")
    assert restored.by_email("ann@example.com") is not None  # she'd be back without the next step
    erased = backup.reapply_deletions(restored, backup.deletion_records(newest), since=TODAY, key=KEY)

    assert erased == 1
    assert restored.by_email("ann@example.com") is None
    assert not restored.root(ann.id).exists()
    assert restored.by_email("bob@example.com") is not None and restored.root(bob.id).exists()
    assert restored.by_email("owner@example.com") is not None
    assert [r["email_hash"] for r in restored.deletion_records()] == [email_hash(KEY, "ann@example.com")]
    files = b"".join(p.read_bytes() for p in restored.library.rglob("*") if p.is_file())
    assert b"ann@example.com" not in files

    # Running it twice changes nothing and doesn't duplicate the record.
    assert backup.reapply_deletions(restored, backup.deletion_records(newest), since=TODAY, key=KEY) == 0
    assert len(restored.deletion_records()) == 1


def test_older_deletions_dont_erase_a_later_sign_up(tmp_path):
    live, admin, ann, bob = make_live(tmp_path)
    # Ann deleted an account long ago, then signed up again; the restored backup has the new one.
    old = [{"email_hash": email_hash(KEY, "ann@example.com"), "removed": "2026-01-01", "removed_by": "user"}]
    take_backup(live, tmp_path / "snapshot")
    restored = restore(tmp_path / "snapshot", tmp_path / "restored")
    assert backup.reapply_deletions(restored, old, since=TODAY, key=KEY) == 0
    assert restored.by_email("ann@example.com") is not None
    assert len(restored.deletion_records()) == 1  # the record itself is kept


def test_reapply_refuses_without_the_record_key(tmp_path):
    live, *_ = make_live(tmp_path)
    with pytest.raises(RuntimeError):
        backup.reapply_deletions(live, [], since=TODAY, key="")


def test_cli_commands(tmp_path, monkeypatch, capsys):
    live, admin, ann, bob = make_live(tmp_path)
    take_backup(live, tmp_path / "snapshot")
    live.delete_account(bob.id, by="admin", key=KEY)
    monkeypatch.setenv("PDF2PHONE_RECORD_KEY", KEY)
    assert cli.main(["backup-db", str(tmp_path / "newest.db"), "-l", str(live.library)]) == 0
    restore(tmp_path / "snapshot", tmp_path / "restored").close()

    args = ["reapply-deletions", "--newer", str(tmp_path / "newest.db"), "-l", str(tmp_path / "restored")]
    assert cli.main([*args, "--since", "10/11/2026"]) == 2
    # A different record key couldn't recognise Bob, so the restore must stop (site stays offline).
    monkeypatch.setenv("PDF2PHONE_RECORD_KEY", "x" * 40)
    assert cli.main([*args, "--since", TODAY]) == 1
    assert UserStore(tmp_path / "restored").by_email("bob@example.com") is not None
    monkeypatch.setenv("PDF2PHONE_RECORD_KEY", KEY)
    assert cli.main([*args, "--since", TODAY]) == 0
    out = capsys.readouterr().out
    assert "Accounts erased again: 1." in out and "bob" not in out
    assert UserStore(tmp_path / "restored").by_email("bob@example.com") is None

    assert cli.main(["backup-db", str(tmp_path / "x.db"), "-l", str(tmp_path / "empty")]) == 1


def test_email_admin_only_sends_fixed_subjects(monkeypatch):
    with pytest.raises(SystemExit):
        cli.main(["email-admin", "anything a visitor typed"])
    sent = []
    monkeypatch.setattr("pdf2phone.web.mail.Mailer.send", lambda self, to, subject, body: sent.append((to, subject, body)))
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("restic: connection refused"))
    for k, v in {"PDF2PHONE_SECRET": "s" * 40, "PDF2PHONE_ADMIN_EMAIL": "owner@example.com",
                 "PDF2PHONE_RECORD_KEY": KEY, "PDF2PHONE_SMTP_HOST": "smtp.example.com",
                 "PDF2PHONE_SMTP_USER": "u", "PDF2PHONE_SMTP_PASSWORD": "p",
                 "PDF2PHONE_MAIL_FROM": "pdf2phone <noreply@example.com>",
                 "PDF2PHONE_PUBLIC_URL": "https://read.example.com"}.items():
        monkeypatch.setenv(k, v)
    assert cli.main(["email-admin", "pdf2phone backup FAILED"]) == 0
    assert sent == [("owner@example.com", "pdf2phone backup FAILED", "restic: connection refused")]
