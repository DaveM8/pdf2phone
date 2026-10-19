"""User accounts, stored in SQLite inside the library volume.

<library>/_db/pdf2phone.db    users
<library>/_users/<id>/        that user's private library (same layout as a CLI library)

Passwords are Argon2id hashes.  Emails are stored lower-cased; they are the
login name.

Deleting an account erases everything about the person.  One row is kept in
``deletions`` as proof that it happened: an HMAC of their email (keyed with
PDF2PHONE_RECORD_KEY, which lives in .env, not here), the date, and who did it.
Without the key, the row can't even be matched against a known address.

Account status:
  unconfirmed  signed up, hasn't clicked the link in the confirmation email yet
               (deleted after CONFIRM_HOURS, so fake addresses never reach the owner)
  pending      email confirmed, waiting for the owner to approve
  active       can log in
Denying a request deletes the row, so nothing about the person is kept.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_PASSWORD_LENGTH = 12
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERS_DIR = "_users"
DB_DIR = "_db"
CONFIRM_HOURS = 24
NAME_MAX = 60
NOTE_MAX = 500

_hasher = PasswordHasher()  # Argon2id with argon2-cffi's current recommended parameters
# Verified when the email doesn't exist, so a wrong email takes as long as a wrong password.
_DUMMY_HASH = _hasher.hash("pdf2phone-timing-equaliser")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY,
    email      TEXT NOT NULL UNIQUE,
    pw_hash    TEXT,               -- NULL until the bootstrap admin sets a password
    is_admin   INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL DEFAULT 'active',
    created    REAL NOT NULL
);
-- Links emailed to people.  Only a SHA-256 of each token is stored, so the
-- database alone can't be used to confirm anyone's address.
CREATE TABLE IF NOT EXISTS tokens (
    hash       TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose    TEXT NOT NULL,
    expires    REAL NOT NULL
);
-- Proof that an account was deleted, without saying whose (see module docstring).
CREATE TABLE IF NOT EXISTS deletions (
    id         INTEGER PRIMARY KEY,
    email_hash TEXT NOT NULL,
    removed    TEXT NOT NULL,          -- date only, YYYY-MM-DD
    removed_by TEXT NOT NULL CHECK (removed_by IN ('user', 'admin'))
);
"""
# Columns added after the first release; added to older databases on start.
ADDED_COLUMNS = {"first_name": "TEXT NOT NULL DEFAULT ''", "last_name": "TEXT NOT NULL DEFAULT ''",
                 "note": "TEXT NOT NULL DEFAULT ''"}


@dataclass(frozen=True)
class User:
    id: int
    email: str
    pw_hash: str | None
    is_admin: bool
    status: str
    created: float
    first_name: str = ""
    last_name: str = ""
    note: str = ""

    @property
    def needs_password(self) -> bool:
        return self.pw_hash is None

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


def normalise_email(email: str) -> str:
    return email.strip().lower()


def password_problem(password: str) -> str | None:
    """Why a new password is unacceptable, or None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters. A few random words works well."
    return None


class UserStore:
    def __init__(self, library: Path):
        self.library = library
        (library / DB_DIR).mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(library / DB_DIR / "pdf2phone.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            # Deleted rows are overwritten with zeros, not just marked free, so
            # erasing a person really removes their details from the file.
            self._db.execute("PRAGMA secure_delete=ON")
            self._db.executescript(SCHEMA)
            have = {r["name"] for r in self._db.execute("PRAGMA table_info(users)")}
            for col, decl in ADDED_COLUMNS.items():
                if col not in have:
                    self._db.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
            self._db.commit()

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------ queries
    def _one(self, sql: str, *args) -> User | None:
        with self._lock:
            row = self._db.execute(sql, args).fetchone()
        return self._user(row) if row else None

    @staticmethod
    def _user(row) -> User:
        return User(row["id"], row["email"], row["pw_hash"], bool(row["is_admin"]), row["status"],
                    row["created"], row["first_name"], row["last_name"], row["note"])

    def with_status(self, status: str) -> list[User]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM users WHERE status = ? ORDER BY created", (status,)).fetchall()
        return [self._user(r) for r in rows]

    def get(self, user_id: int) -> User | None:
        return self._one("SELECT * FROM users WHERE id = ?", user_id)

    def by_email(self, email: str) -> User | None:
        return self._one("SELECT * FROM users WHERE email = ?", normalise_email(email))

    def admin(self) -> User | None:
        return self._one("SELECT * FROM users WHERE is_admin = 1 ORDER BY id LIMIT 1")

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    # ------------------------------------------------------------ changes
    def create(self, email: str, password: str | None, *, is_admin: bool = False, status: str = "active",
               first_name: str = "", last_name: str = "", note: str = "") -> User:
        email = normalise_email(email)
        if not EMAIL.match(email):
            raise ValueError("That doesn't look like an email address.")
        if password is not None and (problem := password_problem(password)):
            raise ValueError(problem)
        pw_hash = _hasher.hash(password) if password is not None else None
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO users (email, pw_hash, is_admin, status, created, first_name, last_name, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (email, pw_hash, int(is_admin), status, time.time(), first_name, last_name, note),
            )
            self._db.commit()
            user_id = cur.lastrowid
        self.root(user_id).mkdir(parents=True, exist_ok=True)
        return self.get(user_id)

    def set_password(self, user_id: int, password: str) -> User:
        if problem := password_problem(password):
            raise ValueError(problem)
        with self._lock:
            self._db.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (_hasher.hash(password), user_id))
            self._db.commit()
        return self.get(user_id)

    def set_status(self, user_id: int, status: str) -> User:
        with self._lock:
            self._db.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
            self._db.commit()
        return self.get(user_id)

    def erase(self, user_id: int, *, record: tuple[str, str] | None = None) -> None:
        """Remove the person completely: their row, links and files.  ``record``
        (email hash, who) also writes the deletion record, in the same transaction."""
        with self._lock:
            if record is not None:
                self._db.execute("INSERT INTO deletions (email_hash, removed, removed_by) VALUES (?, ?, ?)",
                                 (record[0], time.strftime("%Y-%m-%d", time.gmtime()), record[1]))
            self._db.execute("DELETE FROM users WHERE id = ?", (user_id,))  # tokens go too (cascade)
            self._db.commit()
            # Older copies of the row can sit in the write-ahead log; fold it in and empty it.
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        shutil.rmtree(self.root(user_id), ignore_errors=True)

    def delete_account(self, user_id: int, *, by: str, key: str) -> None:
        """Delete an account for good, keeping only the deletion record."""
        if by not in ("user", "admin"):
            raise ValueError(by)
        if len(key) < 32:
            raise RuntimeError("PDF2PHONE_RECORD_KEY isn't set, so deletion records can't be made.")
        user = self.get(user_id)
        if user is None:
            return
        if user.is_admin:
            raise ValueError("The admin account can't be deleted.")
        self.erase(user_id, record=(email_hash(key, user.email), by))

    def deletion_records(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM deletions ORDER BY id")]

    def erase_expired_signups(self) -> int:
        """Delete sign-ups whose confirmation link was never clicked."""
        cutoff = time.time() - CONFIRM_HOURS * 3600
        with self._lock:
            ids = [r["id"] for r in self._db.execute(
                "SELECT id FROM users WHERE status = 'unconfirmed' AND created < ?", (cutoff,))]
        for user_id in ids:
            self.erase(user_id)
        return len(ids)

    # ------------------------------------------------------------ emailed links
    def new_token(self, user_id: int, purpose: str, hours: float = CONFIRM_HOURS) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._db.execute("DELETE FROM tokens WHERE user_id = ? AND purpose = ?", (user_id, purpose))
            self._db.execute("INSERT INTO tokens (hash, user_id, purpose, expires) VALUES (?, ?, ?, ?)",
                             (_token_hash(token), user_id, purpose, time.time() + hours * 3600))
            self._db.commit()
        return token

    def peek_token(self, token: str, purpose: str) -> User | None:
        """The user an unexpired token belongs to, without using it up."""
        with self._lock:
            row = self._db.execute("SELECT user_id FROM tokens WHERE hash = ? AND purpose = ? AND expires > ?",
                                   (_token_hash(token), purpose, time.time())).fetchone()
        return self.get(row["user_id"]) if row else None

    def use_token(self, token: str, purpose: str) -> User | None:
        """Like peek_token, but the token stops working (one use only)."""
        with self._lock:
            row = self._db.execute(
                "DELETE FROM tokens WHERE hash = ? AND purpose = ? AND expires > ? RETURNING user_id",
                (_token_hash(token), purpose, time.time())).fetchone()
            self._db.commit()
        return self.get(row["user_id"]) if row else None

    def ensure_admin(self, email: str) -> User:
        """The first admin comes from PDF2PHONE_ADMIN_EMAIL, without a password:
        they log in once with the old shared password, then must set their own."""
        existing = self.admin()
        if existing:
            return existing
        return self.create(email, None, is_admin=True)

    # ------------------------------------------------------------ login
    def verify(self, email: str, password: str) -> User | None:
        """The active user with this email and password, or None."""
        user = self.check(email, password)
        return user if user and user.status == "active" else None

    def check(self, email: str, password: str) -> User | None:
        """The user with this email and password, whatever their status, or None.
        Always does one Argon2 verification, so timing doesn't reveal whether
        the email exists."""
        user = self.by_email(email)
        stored = user.pw_hash if user and user.pw_hash else _DUMMY_HASH
        try:
            _hasher.verify(stored, password)
            ok = user is not None and user.pw_hash is not None
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            ok = False
        if ok:
            if _hasher.check_needs_rehash(user.pw_hash):
                user = self.set_password(user.id, password)
            return user
        return None

    # ------------------------------------------------------------ files
    def root(self, user_id: int) -> Path:
        return self.library / USERS_DIR / str(int(user_id))


def email_hash(key: str, email: str) -> str:
    """Keyed, so someone with the database but not the key can't test guesses."""
    return hmac.new(key.encode(), normalise_email(email).encode(), hashlib.sha256).hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_version(secret: str, user: User, bootstrap_password: str = "") -> str:
    """Stored in the session cookie.  Changes when the user's password (or the
    app secret) changes, which logs out that user's other devices."""
    material = user.pw_hash or f"bootstrap:{bootstrap_password}"
    return hmac.new(secret.encode(), f"{user.id}:{material}".encode(), hashlib.sha256).hexdigest()[:24]
