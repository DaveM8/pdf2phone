"""Configuration from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .. import engines


@dataclass(frozen=True)
class Settings:
    password: str
    secret: str
    library: Path
    max_mb: int = 100
    default_engine: str = engines.DEFAULT_ENGINE
    secure_cookies: bool = True  # False only for local http testing
    session_days: int = 180

    @classmethod
    def from_env(cls) -> "Settings":
        password = os.environ.get("PDF2PHONE_PASSWORD", "")
        secret = os.environ.get("PDF2PHONE_SECRET", "")
        if len(password) < 8:
            raise RuntimeError("Set PDF2PHONE_PASSWORD (at least 8 characters).")
        if len(secret) < 32:
            raise RuntimeError(
                "Set PDF2PHONE_SECRET to a long random string, e.g. the output of: "
                "python3 -c 'import secrets; print(secrets.token_hex(32))'"
            )
        engine = os.environ.get("PDF2PHONE_DEFAULT_ENGINE", engines.DEFAULT_ENGINE)
        if engine not in engines.ENGINES:
            raise RuntimeError(f"PDF2PHONE_DEFAULT_ENGINE must be one of {', '.join(engines.ENGINES)}")
        return cls(
            password=password,
            secret=secret,
            library=Path(os.environ.get("PDF2PHONE_LIBRARY", "/data/library")),
            max_mb=int(os.environ.get("PDF2PHONE_MAX_MB", "100")),
            default_engine=engine,
            secure_cookies=os.environ.get("PDF2PHONE_INSECURE_COOKIES", "") != "1",
        )
