"""User accounts, stored in SQLite inside the library volume.

<library>/_db/pdf2phone.db    users
<library>/_users/<id>/        that user's private library (same layout as a CLI library)

Passwords are Argon2id hashes.  Emails are stored lower-cased; they are the
login name.  Milestone 2 adds sign-up/approval (``status``); milestone 3 adds
deletion records.
"""

from __future__ import annotations

import hashlib
import hmac
import re
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
"""


@dataclass(frozen=True)
class User:
    id: int
    email: str
    pw_hash: str | None
    is_admin: bool
    status: str
    created: float

    @property
    def needs_password(self) -> bool:
        return self.pw_hash is None


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
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------ queries
    def _one(self, sql: str, *args) -> User | None:
        with self._lock:
            row = self._db.execute(sql, args).fetchone()
        return User(row["id"], row["email"], row["pw_hash"], bool(row["is_admin"]),
                    row["status"], row["created"]) if row else None

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
    def create(self, email: str, password: str | None, *, is_admin: bool = False) -> User:
        email = normalise_email(email)
        if not EMAIL.match(email):
            raise ValueError("That doesn't look like an email address.")
        if password is not None and (problem := password_problem(password)):
            raise ValueError(problem)
        pw_hash = _hasher.hash(password) if password is not None else None
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO users (email, pw_hash, is_admin, created) VALUES (?, ?, ?, ?)",
                (email, pw_hash, int(is_admin), time.time()),
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

    def ensure_admin(self, email: str) -> User:
        """The first admin comes from PDF2PHONE_ADMIN_EMAIL, without a password:
        they log in once with the old shared password, then must set their own."""
        existing = self.admin()
        if existing:
            return existing
        return self.create(email, None, is_admin=True)

    # ------------------------------------------------------------ login
    def verify(self, email: str, password: str) -> User | None:
        """The active user with this email and password, or None.  Always does
        one Argon2 verification, so timing doesn't reveal whether the email exists."""
        user = self.by_email(email)
        stored = user.pw_hash if user and user.pw_hash else _DUMMY_HASH
        try:
            _hasher.verify(stored, password)
            ok = user is not None and user.pw_hash is not None
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            ok = False
        if ok and user.status == "active":
            if _hasher.check_needs_rehash(user.pw_hash):
                user = self.set_password(user.id, password)
            return user
        return None

    # ------------------------------------------------------------ files
    def root(self, user_id: int) -> Path:
        return self.library / USERS_DIR / str(int(user_id))


def session_version(secret: str, user: User, bootstrap_password: str = "") -> str:
    """Stored in the session cookie.  Changes when the user's password (or the
    app secret) changes, which logs out that user's other devices."""
    material = user.pw_hash or f"bootstrap:{bootstrap_password}"
    return hmac.new(secret.encode(), f"{user.id}:{material}".encode(), hashlib.sha256).hexdigest()[:24]
