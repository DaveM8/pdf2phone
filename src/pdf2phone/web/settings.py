"""Configuration from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from email.utils import parseaddr
from pathlib import Path
from urllib.parse import urlsplit

from .. import engines
from .jobs import DEFAULT_WORKERS, MAX_WORKERS
from .users import EMAIL, MIN_PASSWORD_LENGTH, normalise_email

EXAMPLE_PASSWORD = "change-me-to-something-long"
SOURCE_URL = "https://github.com/DaveM8/pdf2phone"
SMTP_VARS = ("PDF2PHONE_SMTP_HOST", "PDF2PHONE_SMTP_USER", "PDF2PHONE_SMTP_PASSWORD", "PDF2PHONE_MAIL_FROM")


@dataclass(frozen=True)
class Settings:
    secret: str
    library: Path
    admin_email: str
    # The old shared password.  Only used for the admin's first login, after
    # which they set a personal password and this is ignored.
    password: str = ""
    max_mb: int = 100
    default_engine: str = engines.DEFAULT_ENGINE
    secure_cookies: bool = True  # False only for local http testing
    session_days: int = 30
    # Email (sign-up).  Without it the sign-up page says sign-up is closed.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = ""
    # Where the app is reached, e.g. https://read.example.com.  Links in emails
    # are built from this, never from the request's Host header, which a
    # visitor controls and could point at their own site.
    public_url: str = ""
    source_url: str = SOURCE_URL  # AGPL: where users can get the code
    # Keys the email hash in deletion records.  Kept apart from the database
    # (and from the secret, which may be changed to log everyone out).
    record_key: str = ""
    # Conversions that may run at once (each Docling one needs ~3 GB).
    workers: int = DEFAULT_WORKERS

    @classmethod
    def from_env(cls) -> "Settings":
        password = os.environ.get("PDF2PHONE_PASSWORD", "")
        secret = os.environ.get("PDF2PHONE_SECRET", "")
        admin_email = normalise_email(os.environ.get("PDF2PHONE_ADMIN_EMAIL", ""))
        if password == EXAMPLE_PASSWORD:
            raise RuntimeError("PDF2PHONE_PASSWORD is still the example value from .env.example; choose your own.")
        if password and len(password) < MIN_PASSWORD_LENGTH:
            raise RuntimeError(
                f"PDF2PHONE_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters (a few random words is ideal).")
        if len(secret) < 32:
            raise RuntimeError(
                "Set PDF2PHONE_SECRET to a long random string, e.g. the output of: "
                "python3 -c 'import secrets; print(secrets.token_hex(32))'"
            )
        if not EMAIL.match(admin_email):
            raise RuntimeError("Set PDF2PHONE_ADMIN_EMAIL to your email address (your admin login).")
        record_key = os.environ.get("PDF2PHONE_RECORD_KEY", "")
        if len(record_key) < 32:
            raise RuntimeError(
                "Set PDF2PHONE_RECORD_KEY to a long random string (it keys the records of deleted accounts), "
                "e.g. the output of: python3 -c 'import secrets; print(secrets.token_hex(32))'")
        mail = {k: os.environ.get(k, "").strip() for k in SMTP_VARS}
        public_url = os.environ.get("PDF2PHONE_PUBLIC_URL", "").strip().rstrip("/")
        if any(mail.values()):
            if missing := [k for k, v in mail.items() if not v]:
                raise RuntimeError(f"Email is half set up: also set {', '.join(missing)} (see .env.example).")
            if "@" not in parseaddr(mail["PDF2PHONE_MAIL_FROM"])[1]:
                raise RuntimeError("PDF2PHONE_MAIL_FROM should look like: pdf2phone <noreply@yourdomain.com>")
            url = urlsplit(public_url)
            if url.scheme != "https" or not url.netloc or url.path or url.query:
                raise RuntimeError("Set PDF2PHONE_PUBLIC_URL to the app's address, e.g. https://read.yourdomain.com")
        engine = os.environ.get("PDF2PHONE_DEFAULT_ENGINE", engines.DEFAULT_ENGINE)
        if engine not in engines.ENGINES:
            raise RuntimeError(f"PDF2PHONE_DEFAULT_ENGINE must be one of {', '.join(engines.ENGINES)}")
        workers = os.environ.get("PDF2PHONE_WORKERS", str(DEFAULT_WORKERS)).strip()
        if not workers.isdigit() or not 1 <= int(workers) <= MAX_WORKERS:
            raise RuntimeError(f"PDF2PHONE_WORKERS must be a number from 1 to {MAX_WORKERS}")
        return cls(
            secret=secret,
            library=Path(os.environ.get("PDF2PHONE_LIBRARY", "/data/library")),
            admin_email=admin_email,
            password=password,
            max_mb=int(os.environ.get("PDF2PHONE_MAX_MB", "100")),
            default_engine=engine,
            secure_cookies=os.environ.get("PDF2PHONE_INSECURE_COOKIES", "") != "1",
            smtp_host=mail["PDF2PHONE_SMTP_HOST"],
            smtp_port=int(os.environ.get("PDF2PHONE_SMTP_PORT", "587")),
            smtp_user=mail["PDF2PHONE_SMTP_USER"],
            smtp_password=mail["PDF2PHONE_SMTP_PASSWORD"],
            mail_from=mail["PDF2PHONE_MAIL_FROM"],
            public_url=public_url,
            record_key=record_key,
            workers=int(workers),
        )
