"""Configuration from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .. import engines
from .users import EMAIL, MIN_PASSWORD_LENGTH, normalise_email

EXAMPLE_PASSWORD = "change-me-to-something-long"


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
        engine = os.environ.get("PDF2PHONE_DEFAULT_ENGINE", engines.DEFAULT_ENGINE)
        if engine not in engines.ENGINES:
            raise RuntimeError(f"PDF2PHONE_DEFAULT_ENGINE must be one of {', '.join(engines.ENGINES)}")
        return cls(
            secret=secret,
            library=Path(os.environ.get("PDF2PHONE_LIBRARY", "/data/library")),
            admin_email=admin_email,
            password=password,
            max_mb=int(os.environ.get("PDF2PHONE_MAX_MB", "100")),
            default_engine=engine,
            secure_cookies=os.environ.get("PDF2PHONE_INSECURE_COOKIES", "") != "1",
        )
