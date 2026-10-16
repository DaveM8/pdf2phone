"""Single-password login with per-IP throttling."""

from __future__ import annotations

import hmac
import threading
import time
from dataclasses import dataclass, field

MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60


def password_ok(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode(), expected.encode())


@dataclass
class Throttle:
    """After MAX_FAILURES wrong passwords from one address, refuse that
    address for LOCKOUT_SECONDS.  In memory: a restart clears it, which is fine
    for a one-person app."""

    failures: dict[str, list[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def locked_for(self, ip: str) -> int:
        """Seconds until ``ip`` may try again (0 = may try now)."""
        now = time.time()
        with self._lock:
            recent = [t for t in self.failures.get(ip, []) if now - t < LOCKOUT_SECONDS]
            self.failures[ip] = recent
            if len(recent) >= MAX_FAILURES:
                return int(LOCKOUT_SECONDS - (now - recent[-MAX_FAILURES])) + 1
            return 0

    def fail(self, ip: str) -> None:
        with self._lock:
            self.failures.setdefault(ip, []).append(time.time())

    def succeed(self, ip: str) -> None:
        with self._lock:
            self.failures.pop(ip, None)
