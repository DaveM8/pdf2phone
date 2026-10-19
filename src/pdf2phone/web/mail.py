"""Sending email (sign-up confirmation, approval notices) over SMTP.

Any SMTP service works; the settings come from .env (PDF2PHONE_SMTP_*).  Bodies
are plain text, and nothing a visitor typed ever goes into a header, so a
sign-up form can't be used to inject headers or HTML.
"""

from __future__ import annotations

import smtplib
import ssl
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

# The free Brevo plan allows 300 a day; stay well under it, and stop the
# sign-up form being used to flood other people's inboxes.
DAILY_LIMIT = 100


class MailError(Exception):
    pass


@dataclass
class Mailer:
    host: str
    port: int
    user: str
    password: str
    sender: str  # "Name <address>"
    timeout: float = 20
    _sent: deque = field(default_factory=lambda: deque(maxlen=DAILY_LIMIT), repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _reserve(self) -> None:
        now = time.time()
        with self._lock:
            if len(self._sent) == DAILY_LIMIT and now - self._sent[0] < 86400:
                raise MailError("daily email limit reached")
            self._sent.append(now)

    def message(self, to: str, subject: str, body: str) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=parseaddr(self.sender)[1].rpartition("@")[2] or None)
        msg.set_content(body)
        return msg

    def send(self, to: str, subject: str, body: str) -> None:
        """Blocking; raises MailError.  Run it in a thread from async code."""
        msg = self.message(to, subject, body)
        self._reserve()
        try:
            context = ssl.create_default_context()
            if self.port == 465:
                server = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
                server.starttls(context=context)
            with server:
                server.login(self.user, self.password)
                server.send_message(msg)
        except (OSError, smtplib.SMTPException) as e:
            raise MailError(f"{type(e).__name__}: {e}") from e


class FakeMailer(Mailer):
    """Keeps messages in ``outbox`` instead of sending them (tests, local runs)."""

    def __init__(self, sender: str = "pdf2phone <noreply@example.com>"):
        super().__init__(host="", port=0, user="", password="", sender=sender)
        self.outbox: list[EmailMessage] = []
        self.fail = False

    def send(self, to: str, subject: str, body: str) -> None:
        msg = self.message(to, subject, body)
        self._reserve()
        if self.fail:
            raise MailError("pretend failure")
        self.outbox.append(msg)
