"""FastAPI app.

Run (development):
  PDF2PHONE_ADMIN_EMAIL=you@example.com PDF2PHONE_PASSWORD=... PDF2PHONE_SECRET=... \\
  PDF2PHONE_LIBRARY=./library PDF2PHONE_INSECURE_COOKIES=1 \\
  uv run uvicorn --factory pdf2phone.web.app:create_app --reload

Everything except the login page, health check and shared static assets
(CSS, fonts, icons — nothing private) requires a logged-in user, and every
document route works only inside that user's own folder (<lib>/_users/<id>/).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import re
import secrets
import shutil
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from jinja2 import Environment, PackageLoader, select_autoescape
from starlette.background import BackgroundTask
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .. import engines, library, page
from . import migrate
from .auth import GLOBAL_DELAY_SECONDS, RateLimit, Throttle
from .jobs import ENGINE_LABELS, JobQueue
from .mail import Mailer, MailError
from .settings import Settings
from .users import (_DUMMY_HASH, CONFIRM_HOURS, EMAIL, MIN_PASSWORD_LENGTH, NAME_MAX, NOTE_MAX, User, UserStore,
                    _hasher, normalise_email, password_problem, session_version)

log = logging.getLogger("pdf2phone")

PUBLIC_EXACT = {"/login", "/signup", "/signup/confirm", "/goodbye", "/forgot", "/reset", "/healthz", "/manifest.webmanifest",
                "/apple-touch-icon.png", "/icon-512.png"}
# Reachable by an admin who still has to choose a personal password.
SET_PASSWORD_PATHS = {"/account", "/account/password", "/logout"}
PUBLIC_PREFIX = ("/_static/",)
DOC_ID = re.compile(r"^[\w-]{1,80}$")
ARGON2_CONCURRENCY = 2
# Sign-up bot checks: a person takes a few seconds to fill the form in, and
# never fills the hidden "website" box.  Each address may sign up 5 times an hour.
MIN_FORM_SECONDS = 3
MAX_FORM_SECONDS = 86400
SIGNUPS_PER_HOUR = 5
RESETS_PER_HOUR = 5        # per address asking, and per account (so nobody's inbox can be flooded)
RESET_HOURS = 1

# Sent with every response.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}
# Reader pages are built from PDF text, so no inline script may run there even
# if something slips through the renderer.  They use only ../_static/reader.js.
READER_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
              "img-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'self'; "
              "frame-ancestors 'none'")

# Messages shown after a redirect; looked up by key so nothing user-supplied is echoed.
FLASH = {
    "queued": "Converting your PDF. It will appear below when it's ready.",
    "deleted": "Deleted.",
    "notpdf": "That file isn't a PDF.",
    "toobig": "That file is too big.",
    "empty": "No file was chosen.",
    "busy": "That document is being converted right now; try again when it's done.",
    "pwset": "Password saved. Your other devices have been logged out.",
    "setpw": "Welcome! Please choose your own password to finish setting up your account.",
    "approved": "Approved. They've been sent an email saying they can log in.",
    "approved_nomail": "Approved, but the email to them couldn't be sent. Let them know yourself.",
    "denied": "Request turned down. Everything they entered has been deleted.",
    "gone": "That request isn't waiting any more.",
    "removed": "Removed. Their documents and details have been deleted.",
    "remove_busy": "They have a PDF converting right now. Try again when it's finished.",
    "nokey": "Removing people isn't set up yet: add PDF2PHONE_RECORD_KEY to .env.",
    "notactive": "That person doesn't have an account any more.",
    "reset": "Password saved. You can log in with it now.",
}

_env = Environment(loader=PackageLoader("pdf2phone", "templates"), autoescape=select_autoescape(["html", "j2"]))
_env.filters["date"] = lambda t: time.strftime("%d %b %Y", time.localtime(t))


def create_app(settings: Settings | None = None, mailer: Mailer | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    lib = settings.library
    lib.mkdir(parents=True, exist_ok=True)
    if migrate.legacy_docs(lib):
        raise RuntimeError(
            "This library is still in the old shared layout. Run the one-off migration first "
            "(docs/UPGRADE-ACCOUNTS.md): pdf2phone migrate-accounts")
    library.ensure_static(lib)
    users = UserStore(lib)
    admin = users.ensure_admin(settings.admin_email)
    if admin.needs_password and not settings.password:
        raise RuntimeError("The admin account has no password yet. Set PDF2PHONE_PASSWORD for the first login.")
    users.erase_expired_signups()
    queue = JobQueue(lib, users.root, workers=settings.workers)
    throttle = Throttle()
    signup_limit = RateLimit(SIGNUPS_PER_HOUR, 3600)
    reset_limit = RateLimit(RESETS_PER_HOUR, 3600)          # by address asking
    reset_account_limit = RateLimit(RESETS_PER_HOUR, 3600)  # by email asked about
    if mailer is None and settings.smtp_host:
        mailer = Mailer(settings.smtp_host, settings.smtp_port, settings.smtp_user, settings.smtp_password,
                        settings.mail_from)
    signup_open = mailer is not None and bool(settings.public_url)  # also gates password-reset emails
    # Each Argon2 check takes ~64 MB for a moment.  Cap how many run at once so a
    # flood of logins can't push the container (which also runs Docling) out of memory.
    argon2_slots = asyncio.Semaphore(ARGON2_CONCURRENCY)

    async def hashing(fn, *args):
        async with argon2_slots:
            return await asyncio.to_thread(fn, *args)

    def version(user: User) -> str:
        return session_version(settings.secret, user, settings.password)

    def device_mark(user: User) -> str:
        return f"{user.id}:{version(user)}"
    available = engines.available_engines()
    engine_options = [{"name": n, "label": ENGINE_LABELS.get(n, n)} for n in available]
    default_engine = settings.default_engine if settings.default_engine in available else available[0]
    static_root = Path(str(page.static_files()))

    @contextlib.asynccontextmanager
    async def lifespan(app):
        yield
        queue.close()  # stop the converter processes with the app

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.queue = queue  # tests use these
    app.state.throttle = throttle
    app.state.users = users
    app.state.mailer = mailer

    # ------------------------------------------------------------ auth gate
    class RequireLogin(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            path = request.url.path
            if path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIX):
                return await call_next(request)
            uid = request.session.get("uid")
            user = users.get(uid) if isinstance(uid, int) else None
            if (user is None or user.status != "active"
                    or not hmac.compare_digest(str(request.session.get("v", "")), version(user))):
                if request.method == "GET" and not path.startswith("/api/"):
                    return RedirectResponse(f"/login?next={quote(path)}", status_code=303)
                return PlainTextResponse("Please log in.", status_code=401)
            if user.needs_password and path not in SET_PASSWORD_PATHS:
                return RedirectResponse("/account?msg=setpw", status_code=303)
            request.state.user = user
            return await call_next(request)

    class SecurityHeaders(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)
            for k, v in SECURITY_HEADERS.items():
                response.headers.setdefault(k, v)
            return response

    app.add_middleware(RequireLogin)
    app.add_middleware(SecurityHeaders)
    app.add_middleware(  # added last = runs first, so the session exists for RequireLogin
        SessionMiddleware,
        secret_key=settings.secret,
        session_cookie="pdf2phone",
        max_age=settings.session_days * 86400,
        same_site="lax",
        https_only=settings.secure_cookies,
    )

    # ------------------------------------------------------------ login
    def render(name: str, status: int = 200, **ctx) -> HTMLResponse:
        html = _env.get_template(name).render(static=page.STATIC_DIR, source_url=settings.source_url,
                                              signup_open=signup_open, **ctx)
        return HTMLResponse(html, status_code=status, headers={"Cache-Control": "no-store"})

    def login_page(error: str = "", next_url: str = "/", status: int = 200, email: str = "",
                   flash: str = "") -> HTMLResponse:
        return render("login.html.j2", status, error=error, next=next_url, email=email, flash=flash)

    def check_password(user: User | None, email: str, password: str) -> User | None:
        """Blocking (Argon2): run in a thread."""
        if user is not None and user.needs_password:
            # Bootstrap admin: the old shared password works until they set their own.
            try:
                _hasher.verify(_DUMMY_HASH, password)  # same timing as a real check
            except Exception:
                pass
            ok = bool(settings.password) and hmac.compare_digest(password.encode(), settings.password.encode())
            return user if ok else None
        return users.check(email, password)  # any status; login() turns away inactive ones

    def safe_next(next_url: str) -> str:
        # Only same-site paths, never "//evil.com" or absolute URLs.
        return next_url if next_url.startswith("/") and not next_url.startswith("//") else "/"

    @app.get("/login")
    def login_form(next: str = "/", msg: str = ""):
        return login_page(next_url=safe_next(next), flash=FLASH.get(msg, ""))

    @app.post("/login")
    async def login(request: Request, email: str = Form(""), password: str = Form(""), next: str = Form("/")):
        ip = request.client.host if request.client else "unknown"
        # Counted now, before any await, so parallel guesses can't all pass the check.
        wait = throttle.begin(ip)
        if wait:
            return login_page(f"Too many wrong tries. Try again in {wait // 60 + 1} minutes.",
                              safe_next(next), 429, email)

        candidate = users.by_email(email)
        # Signed, set only by a past successful login of this user, and tied to
        # their current password: changing it revokes the mark.
        known_device = candidate is not None and hmac.compare_digest(
            str(request.session.get("dev", "")), device_mark(candidate))
        if throttle.under_attack() and not known_device:
            # One unknown-device attempt at a time, each delayed: caps guessing
            # at ~1 per GLOBAL_DELAY_SECONDS overall without refusing anyone.
            async with throttle.serial:
                await asyncio.sleep(GLOBAL_DELAY_SECONDS)
                user = await hashing(check_password, candidate, email, password)
        else:
            user = await hashing(check_password, candidate, email, password)

        if user is None:
            # Same message whether the email or the password was wrong.
            return login_page("That email or password isn't right.", safe_next(next), 401, email)
        throttle.succeed(ip)
        if user.status != "active":
            # Only said to someone who knows the password, so it reveals nothing.
            why = ("Your account is waiting for the owner's approval. You'll get an email when it's ready."
                   if user.status == "pending" else
                   "Please confirm your email address first: open the link in the email we sent you.")
            return login_page(why, safe_next(next), 403, email)
        request.session.clear()
        request.session["uid"] = user.id
        request.session["v"] = version(user)
        request.session["dev"] = device_mark(user)
        if user.needs_password:
            return RedirectResponse("/account?msg=setpw", status_code=303)
        return RedirectResponse(safe_next(next), status_code=303)

    @app.get("/logout")
    def logout(request: Request):
        # Log out but remember this is a known device (skips the slow queue during
        # attacks).  Only reachable when logged in: RequireLogin guards /logout.
        user: User = request.state.user
        request.session.clear()
        request.session["dev"] = device_mark(user)
        return RedirectResponse("/login", status_code=303)

    # ------------------------------------------------------------ account
    def account_page(request: Request, msg: str = "", error: str = "", status: int = 200) -> HTMLResponse:
        return render("account.html.j2", status, user=request.state.user, flash=FLASH.get(msg, ""), error=error,
                      min_len=MIN_PASSWORD_LENGTH)

    @app.get("/account")
    def account(request: Request, msg: str = ""):
        return account_page(request, msg)

    @app.post("/account/password")
    async def change_password(request: Request, current: str = Form(""), new: str = Form(""),
                              confirm: str = Form("")):
        user: User = request.state.user
        if not user.needs_password:
            # Same per-address limit as logging in, so a stolen session can't be
            # used to guess the current password and take the account over.
            ip = request.client.host if request.client else "unknown"
            if wait := throttle.begin(ip):
                return account_page(request, error=f"Too many wrong tries. Try again in {wait // 60 + 1} minutes.",
                                    status=429)
            if await hashing(users.verify, user.email, current) is None:
                return account_page(request, error="Your current password isn't right.", status=400)
            throttle.succeed(ip)
        if new != confirm:
            return account_page(request, error="The two new passwords don't match.", status=400)
        if problem := password_problem(new):
            return account_page(request, error=problem, status=400)
        user = await hashing(users.set_password, user.id, new)
        # Keep this device logged in; every other session's version is now stale.
        request.session["v"] = version(user)
        request.session["dev"] = device_mark(user)
        return RedirectResponse("/account?msg=pwset", status_code=303)

    # ------------------------------------------------------------ sign-up
    def form_stamp() -> str:
        """When the sign-up form was shown, signed so a bot can't fake it."""
        now = str(int(time.time()))
        return f"{now}.{hmac.new(settings.secret.encode(), f'signup:{now}'.encode(), hashlib.sha256).hexdigest()[:20]}"

    def looks_human(stamp: str, trap: str) -> bool:
        if trap:  # the hidden box people never see
            return False
        when, _, sig = stamp.partition(".")
        good = hmac.new(settings.secret.encode(), f"signup:{when}".encode(), hashlib.sha256).hexdigest()[:20]
        if not when.isdigit() or not hmac.compare_digest(sig, good):
            return False
        return MIN_FORM_SECONDS <= time.time() - int(when) <= MAX_FORM_SECONDS

    def signup_page(state: str = "form", status: int = 200, error: str = "", **ctx) -> HTMLResponse:
        if not signup_open and state == "form":
            state = "closed"
        return render("signup.html.j2", status, state=state, error=error, stamp=form_stamp(),
                      min_len=MIN_PASSWORD_LENGTH, name_max=NAME_MAX, note_max=NOTE_MAX,
                      confirm_hours=CONFIRM_HOURS, **ctx)

    async def send(to: str, subject: str, body: str) -> bool:
        try:
            await asyncio.to_thread(mailer.send, to, subject, body)
            return True
        except MailError as e:
            log.error("sending email failed: %s", e)  # no address: logs shouldn't hold personal data
            return False

    @app.get("/signup")
    def signup_form():
        return signup_page()

    @app.post("/signup")
    async def signup(request: Request, first_name: str = Form(""), last_name: str = Form(""),
                     email: str = Form(""), password: str = Form(""), confirm: str = Form(""),
                     note: str = Form(""), website: str = Form(""), stamp: str = Form("")):
        if not signup_open:
            return signup_page("closed", 404)
        ip = request.client.host if request.client else "unknown"
        if not signup_limit.allow(ip):
            return signup_page(error="Too many sign-ups from this connection. Please try again in an hour.",
                               status=429)
        if not looks_human(stamp, website):
            # Looks exactly like success, so a bot learns nothing; nothing is stored or sent.
            log.info("sign-up rejected as a bot")
            return signup_page("check_email")

        first_name, last_name, note = first_name.strip(), last_name.strip(), note.strip()
        email = normalise_email(email)
        keep = dict(first_name=first_name, last_name=last_name, email=email, note=note)
        if not first_name or not last_name:
            error = "Please fill in your first and last name."
        elif len(first_name) > NAME_MAX or len(last_name) > NAME_MAX:
            error = f"Names can be up to {NAME_MAX} characters."
        elif not EMAIL.match(email):
            error = "That doesn't look like an email address."
        elif len(note) > NOTE_MAX:
            error = f"The note can be up to {NOTE_MAX} characters."
        elif password != confirm:
            error = "The two passwords don't match."
        else:
            error = password_problem(password) or ""
        if error:
            return signup_page(error=error, status=400, **keep)

        users.erase_expired_signups()
        existing = users.by_email(email)
        if existing is not None and existing.status != "unconfirmed":
            # Same page as a new sign-up, so the form can't be used to find out who has an account.
            return signup_page("check_email")
        if existing is not None:
            users.erase(existing.id)  # signing up again replaces an unconfirmed attempt
        try:
            user = await hashing(lambda: users.create(email, password, status="unconfirmed", first_name=first_name,
                                                      last_name=last_name, note=note))
        except sqlite3.IntegrityError:  # the same email sent twice at once
            return signup_page("check_email")
        token = users.new_token(user.id, "confirm")
        sent = await send(email, "Please confirm your email address", _env.get_template("email_confirm.txt").render(
            user=user, link=f"{settings.public_url}/signup/confirm?t={token}", hours=CONFIRM_HOURS))
        if not sent:
            users.erase(user.id)
            return signup_page(error="We couldn't send the email just now. Please try again later.",
                               status=503, **keep)
        return signup_page("check_email")

    @app.get("/signup/confirm")
    def confirm_link(t: str = ""):
        # Email scanners open links by themselves, so opening one only shows a
        # button; confirming needs a real click (POST).
        user = users.peek_token(t, "confirm") if t else None
        if user is None or user.status != "unconfirmed":
            return signup_page("expired", 404)
        return signup_page("confirm", token=t)

    @app.post("/signup/confirm")
    async def confirm(t: str = Form("")):
        user = users.use_token(t, "confirm") if t else None
        if user is None or user.status != "unconfirmed":
            return signup_page("expired", 404)
        user = users.set_status(user.id, "pending")
        await send(settings.admin_email, "Someone asked for a pdf2phone account",
                   _env.get_template("email_new_request.txt").render(user=user, link=f"{settings.public_url}/admin"))
        return signup_page("confirmed")

    # ------------------------------------------------------------ forgot password
    def reset_page(state: str = "form", status: int = 200, error: str = "", **ctx) -> HTMLResponse:
        if not signup_open and state == "form":
            state = "closed"
        return render("forgot.html.j2", status, state=state, error=error, stamp=form_stamp(),
                      min_len=MIN_PASSWORD_LENGTH, hours=RESET_HOURS, **ctx)

    async def send_reset(user: User) -> None:
        token = users.new_token(user.id, "reset", hours=RESET_HOURS)  # replaces any earlier link
        await send(user.email, "Choose a new password", _env.get_template("email_reset.txt").render(
            user=user, link=f"{settings.public_url}/reset?t={token}", hours=RESET_HOURS))

    @app.get("/forgot")
    def forgot_form():
        return reset_page()

    @app.post("/forgot")
    async def forgot(request: Request, email: str = Form(""), website: str = Form(""), stamp: str = Form("")):
        if not signup_open:
            return reset_page("closed", 404)
        ip = request.client.host if request.client else "unknown"
        if not reset_limit.allow(ip):
            return reset_page(error="Too many requests from this connection. Please try again in an hour.",
                              status=429)
        email = normalise_email(email)
        sent_page = reset_page("sent")
        if not looks_human(stamp, website) or not EMAIL.match(email):
            return sent_page
        user = users.by_email(email)
        # The same page, at the same speed, whether or not the email has an
        # account: the email goes out after the response, so this form can't
        # be used to find out who uses the reader.
        if user is not None and user.status == "active" and reset_account_limit.allow(email):
            sent_page.background = BackgroundTask(send_reset, user)
        return sent_page

    @app.get("/reset")
    def reset_form(t: str = ""):
        # Only shows the form; the link is used up when the new password is saved.
        user = users.peek_token(t, "reset") if t else None
        if user is None or user.status != "active":
            return reset_page("expired", 404)
        return reset_page("choose", token=t)

    @app.post("/reset")
    async def reset(t: str = Form(""), new: str = Form(""), confirm: str = Form("")):
        user = users.peek_token(t, "reset") if t else None
        if user is None or user.status != "active":
            return reset_page("expired", 404)
        if new != confirm:
            return reset_page("choose", 400, "The two passwords don't match.", token=t)
        if problem := password_problem(new):
            return reset_page("choose", 400, problem, token=t)
        if users.use_token(t, "reset") is None:  # used at the same moment in another tab
            return reset_page("expired", 404)
        # A new password changes the session version: every device is logged out.
        await hashing(users.set_password, user.id, new)
        return RedirectResponse("/login?msg=reset", status_code=303)

    # ------------------------------------------------------------ admin
    def csrf_token(request: Request) -> str:
        if "csrf" not in request.session:
            request.session["csrf"] = secrets.token_urlsafe(24)
        return request.session["csrf"]

    def admin_only(request: Request, csrf: str | None = None) -> Response | None:
        """A refusal for anyone but the admin (or a missing form token), else None."""
        if not request.state.user.is_admin:
            return render("admin.html.j2", 403, allowed=False)
        if csrf is not None and not hmac.compare_digest(csrf, str(request.session.get("csrf", ""))):
            return PlainTextResponse("This page was out of date. Go back, reload it, and try again.", status_code=400)
        return None

    @app.get("/admin")
    def admin_page(request: Request, msg: str = ""):
        if refused := admin_only(request):
            return refused
        return render("admin.html.j2", allowed=True, flash=FLASH.get(msg, ""), csrf=csrf_token(request),
                      pending=users.with_status("pending"), active=users.with_status("active"))

    def waiting(user_id: int) -> User | None:
        user = users.get(user_id)
        return user if user is not None and user.status == "pending" and not user.is_admin else None

    @app.post("/admin/users/{user_id}/approve")
    async def approve(request: Request, user_id: int, csrf: str = Form("")):
        if refused := admin_only(request, csrf):
            return refused
        if (user := waiting(user_id)) is None:
            return RedirectResponse("/admin?msg=gone", status_code=303)
        user = users.set_status(user.id, "active")
        users.root(user.id).mkdir(parents=True, exist_ok=True)
        sent = await send(user.email, "Your pdf2phone account is ready",
                          _env.get_template("email_approved.txt").render(user=user,
                                                                         link=f"{settings.public_url}/login"))
        return RedirectResponse(f"/admin?msg={'approved' if sent else 'approved_nomail'}", status_code=303)

    @app.post("/admin/users/{user_id}/deny")
    def deny(request: Request, user_id: int, csrf: str = Form("")):
        if refused := admin_only(request, csrf):
            return refused
        if (user := waiting(user_id)) is None:
            return RedirectResponse("/admin?msg=gone", status_code=303)
        users.erase(user.id)
        return RedirectResponse("/admin?msg=denied", status_code=303)

    @app.post("/admin/users/{user_id}/remove")
    async def remove(request: Request, user_id: int, csrf: str = Form("")):
        if refused := admin_only(request, csrf):
            return refused
        user = users.get(user_id)
        if user is None or user.is_admin or user.status != "active":
            return RedirectResponse("/admin?msg=notactive", status_code=303)
        if len(settings.record_key) < 32:
            return RedirectResponse("/admin?msg=nokey", status_code=303)
        if queue.busy(user.id):
            # A running conversion would write its files back after the removal.
            return RedirectResponse("/admin?msg=remove_busy", status_code=303)
        queue.forget_user(user.id)
        await asyncio.to_thread(users.delete_account, user.id, by="admin", key=settings.record_key)
        return RedirectResponse("/admin?msg=removed", status_code=303)

    @app.post("/account/delete")
    async def delete_account(request: Request, password: str = Form("")):
        user: User = request.state.user
        if user.is_admin:
            return account_page(request, error="The owner's account can't be deleted.", status=400)
        if len(settings.record_key) < 32:
            return account_page(request, error="Deleting accounts isn't set up on this server yet. "
                                               "Ask the owner.", status=503)
        # Asks for the password, rate-limited like logging in, so a phone left
        # unlocked (or a stolen session) can't be used to wipe the account.
        ip = request.client.host if request.client else "unknown"
        if wait := throttle.begin(ip):
            return account_page(request, error=f"Too many wrong tries. Try again in {wait // 60 + 1} minutes.",
                                status=429)
        if await hashing(users.verify, user.email, password) is None:
            return account_page(request, error="That password isn't right, so nothing was deleted.", status=400)
        throttle.succeed(ip)
        if queue.busy(user.id):
            # A running conversion would write its files back after the delete.
            return account_page(request, error="A PDF is still being converted. Try again when it's finished.",
                                status=409)
        queue.forget_user(user.id)
        await asyncio.to_thread(users.delete_account, user.id, by="user", key=settings.record_key)
        request.session.clear()
        return RedirectResponse("/goodbye", status_code=303)

    @app.get("/goodbye")
    def goodbye():
        return signup_page("deleted")

    @app.get("/healthz")
    def healthz():
        return PlainTextResponse("ok")

    # ------------------------------------------------------------ library
    def root_of(request: Request) -> Path:
        return users.root(request.state.user.id)

    @app.get("/")
    def index(request: Request, msg: str = ""):
        user: User = request.state.user
        web = {
            "engines": engine_options,
            "default_engine": default_engine,
            "jobs": [j.view() for j in queue.recent(user.id)],
            "busy": queue.busy(user.id),
            "flash": FLASH.get(msg, ""),
            "max_mb": settings.max_mb,
            "user": user,
            "waiting": len(users.with_status("pending")) if user.is_admin else 0,
            "source_url": settings.source_url,
        }
        root = root_of(request)
        root.mkdir(parents=True, exist_ok=True)
        return HTMLResponse(library.render_index(root, web=web), headers={"Cache-Control": "no-store"})

    @app.get("/api/jobs")
    def jobs(request: Request):
        uid = request.state.user.id
        return JSONResponse({"busy": queue.busy(uid), "jobs": [j.view() for j in queue.recent(uid)]})

    @app.post("/upload")
    async def upload(request: Request, pdf: UploadFile | None = None, engine: str = Form("")):
        if pdf is None or not pdf.filename:
            return RedirectResponse("/?msg=empty", status_code=303)
        engine = engine if engine in available else default_engine
        limit = settings.max_mb * 1024 * 1024
        dest = queue.upload_path()
        size = 0
        with dest.open("wb") as out:
            first = await pdf.read(1024)
            if not first.startswith(b"%PDF-"):
                out.close()
                dest.unlink(missing_ok=True)
                return RedirectResponse("/?msg=notpdf", status_code=303)
            out.write(first)
            size = len(first)
            while chunk := await pdf.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    out.close()
                    dest.unlink(missing_ok=True)
                    return RedirectResponse("/?msg=toobig", status_code=303)
                out.write(chunk)
        name = Path(pdf.filename).name
        doc_id = f"{library.slugify(Path(name).stem)[:50]}-{secrets.token_hex(3)}"
        queue.submit(user_id=request.state.user.id, doc_id=doc_id, source_name=name, engine=engine, upload=dest)
        return RedirectResponse("/?msg=queued", status_code=303)

    def existing_doc(request: Request, doc_id: str) -> Path | None:
        """The document folder, only if it's in the logged-in user's own library."""
        if not DOC_ID.match(doc_id) or doc_id.startswith("_"):
            return None
        folder = root_of(request) / doc_id
        return folder if (folder / "meta.json").exists() else None

    @app.post("/d/{doc_id}/reconvert")
    def reconvert(request: Request, doc_id: str, engine: str = Form("")):
        uid = request.state.user.id
        folder = existing_doc(request, doc_id)
        if folder is None or not (folder / "source.pdf").exists():
            return PlainTextResponse("Not found", status_code=404)
        if queue.doc_busy(uid, doc_id):
            return RedirectResponse("/?msg=busy", status_code=303)
        meta = library.list_docs(root_of(request))
        source_name = next((m["source"] for m in meta if m["id"] == doc_id), "document.pdf")
        queue.submit(user_id=uid, doc_id=doc_id, source_name=source_name,
                     engine=engine if engine in available else default_engine, upload=None)
        return RedirectResponse("/?msg=queued", status_code=303)

    @app.post("/d/{doc_id}/delete")
    def delete(request: Request, doc_id: str):
        folder = existing_doc(request, doc_id)
        if folder is None:
            return PlainTextResponse("Not found", status_code=404)
        if queue.doc_busy(request.state.user.id, doc_id):
            return RedirectResponse("/?msg=busy", status_code=303)
        shutil.rmtree(folder)
        return RedirectResponse("/?msg=deleted", status_code=303)

    @app.get("/d/{doc_id}/source.pdf")
    def original(request: Request, doc_id: str):
        folder = existing_doc(request, doc_id)
        if folder is None or not (folder / "source.pdf").exists():
            return PlainTextResponse("Not found", status_code=404)
        meta = next((m for m in library.list_docs(root_of(request)) if m["id"] == doc_id), {})
        return FileResponse(folder / "source.pdf", media_type="application/pdf",
                            filename=meta.get("source", "document.pdf"))

    # ------------------------------------------------------------ app icons
    @app.get("/manifest.webmanifest")
    def manifest():
        return JSONResponse({
            "name": "pdf2phone reader", "short_name": "Reader", "start_url": "/", "display": "standalone",
            "background_color": "#fbf5e6", "theme_color": "#fbf5e6",
            "icons": [{"src": "/apple-touch-icon.png", "sizes": "180x180", "type": "image/png"},
                      {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}],
        }, media_type="application/manifest+json")

    @app.get("/apple-touch-icon.png")
    def touch_icon():
        return FileResponse(static_root / "icons/apple-touch-icon.png", media_type="image/png")

    @app.get("/icon-512.png")
    def icon_512():
        return FileResponse(static_root / "icons/icon-512.png", media_type="image/png")

    # ------------------------------------------------------------ library files
    @app.get("/{path:path}")
    def files(request: Request, path: str):
        # Shared CSS/fonts come from <lib>/_static (public, see PUBLIC_PREFIX);
        # everything else only from the logged-in user's own folder.
        if path.startswith("_static/"):
            root = (lib / "_static").resolve()
            target = (lib / path).resolve()
        else:
            root = root_of(request).resolve()
            target = (root / path).resolve()
        if not target.is_relative_to(root):
            return PlainTextResponse("Not found", status_code=404)
        if target.is_dir():
            if path and not path.endswith("/"):
                # Reader pages use relative links (images, ../_static), so they need the slash.
                return RedirectResponse(f"/{path}/", status_code=301)
            target = target / "index.html"
        if not target.is_file():
            return PlainTextResponse("Not found", status_code=404)
        headers = {}
        if target.suffix == ".html":
            headers["Cache-Control"] = "no-cache"
            if target.parent != root:  # a document's reader page
                headers["Content-Security-Policy"] = READER_CSP
        return FileResponse(target, headers=headers)

    return app
