"""Single-password login with brute-force protection.

Three layers:
1. Per address: every attempt is counted *when it starts* (atomically), so
   parallel requests can't slip past the check.  MAX_FAILURES attempts in
   LOCKOUT_SECONDS without a success locks that address out.
2. Globally, when many attempts fail from anywhere (e.g. a botnet or forged
   addresses): logins from unknown devices are checked one at a time with a
   GLOBAL_DELAY_SECONDS pause, capping guessing at ~1 per 2 s overall.
   Devices that logged in before (signed "known device" mark in the session)
   skip that queue, so a flood can't lock the owner out.
3. All bookkeeping is bounded in memory.
"""

from __future__ import annotations

import asyncio
import hmac
import threading
import time
from collections import OrderedDict, deque

MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
GLOBAL_MAX_FAILURES = 30
GLOBAL_DELAY_SECONDS = 2.0
MAX_TRACKED_IPS = 10_000


def password_ok(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode(), expected.encode())


class Throttle:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._per_ip: OrderedDict[str, deque[float]] = OrderedDict()
        self._global: deque[float] = deque(maxlen=GLOBAL_MAX_FAILURES)
        self.serial = asyncio.Semaphore(1)  # used by the app while under attack

    @staticmethod
    def _recent(times: deque[float], now: float) -> int:
        while times and now - times[0] >= LOCKOUT_SECONDS:
            times.popleft()
        return len(times)

    def _wait(self, ip: str, now: float) -> int:
        times = self._per_ip.get(ip)
        if times is not None and self._recent(times, now) >= MAX_FAILURES:
            return int(LOCKOUT_SECONDS - (now - times[0])) + 1
        return 0

    def begin(self, ip: str) -> int:
        """Start a login attempt.  Returns 0 and records the attempt, or the
        number of seconds ``ip`` must wait (attempt not recorded)."""
        now = time.time()
        with self._lock:
            wait = self._wait(ip, now)
            if wait:
                return wait
            times = self._per_ip.get(ip)
            if times is None:
                times = self._per_ip[ip] = deque(maxlen=MAX_FAILURES)
                if len(self._per_ip) > MAX_TRACKED_IPS:
                    self._per_ip.popitem(last=False)  # forget the stalest address
            self._per_ip.move_to_end(ip)
            times.append(now)
            self._global.append(now)
            return 0

    def succeed(self, ip: str) -> None:
        with self._lock:
            self._per_ip.pop(ip, None)
            if self._global:
                self._global.pop()  # the pre-counted attempt wasn't a failure

    def under_attack(self) -> bool:
        with self._lock:
            return self._recent(self._global, time.time()) >= GLOBAL_MAX_FAILURES

    def locked_for(self, ip: str) -> int:
        """Seconds until ``ip`` may try again (0 = may try now).  Read-only."""
        with self._lock:
            return self._wait(ip, time.time())
