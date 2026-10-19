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
import hmac
import re
import secrets
import shutil
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from jinja2 import Environment, PackageLoader, select_autoescape
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .. import engines, library, page
from . import migrate
from .auth import GLOBAL_DELAY_SECONDS, Throttle
from .jobs import ENGINE_LABELS, JobQueue
from .settings import Settings
from .users import _DUMMY_HASH, MIN_PASSWORD_LENGTH, User, UserStore, _hasher, password_problem, session_version

PUBLIC_EXACT = {"/login", "/healthz", "/manifest.webmanifest", "/apple-touch-icon.png", "/icon-512.png"}
# Reachable by an admin who still has to choose a personal password.
SET_PASSWORD_PATHS = {"/account", "/account/password", "/logout"}
PUBLIC_PREFIX = ("/_static/",)
DOC_ID = re.compile(r"^[\w-]{1,80}$")

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
}

_env = Environment(loader=PackageLoader("pdf2phone", "templates"), autoescape=select_autoescape(["html", "j2"]))


def create_app(settings: Settings | None = None) -> FastAPI:
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
    queue = JobQueue(lib, users.root)
    throttle = Throttle()

    def version(user: User) -> str:
        return session_version(settings.secret, user, settings.password)

    def device_mark(user: User) -> str:
        return f"{user.id}:{version(user)}"
    available = engines.available_engines()
    engine_options = [{"name": n, "label": ENGINE_LABELS.get(n, n)} for n in available]
    default_engine = settings.default_engine if settings.default_engine in available else available[0]
    static_root = Path(str(page.static_files()))

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.queue = queue  # tests use these
    app.state.throttle = throttle
    app.state.users = users

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
    def login_page(error: str = "", next_url: str = "/", status: int = 200, email: str = "") -> HTMLResponse:
        html = _env.get_template("login.html.j2").render(error=error, next=next_url, email=email,
                                                         static=page.STATIC_DIR)
        return HTMLResponse(html, status_code=status)

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
        return users.verify(email, password)

    def safe_next(next_url: str) -> str:
        # Only same-site paths, never "//evil.com" or absolute URLs.
        return next_url if next_url.startswith("/") and not next_url.startswith("//") else "/"

    @app.get("/login")
    def login_form(next: str = "/"):
        return login_page(next_url=safe_next(next))

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
                user = await asyncio.to_thread(check_password, candidate, email, password)
        else:
            user = await asyncio.to_thread(check_password, candidate, email, password)

        if user is None:
            # Same message whether the email or the password was wrong.
            return login_page("That email or password isn't right.", safe_next(next), 401, email)
        throttle.succeed(ip)
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
        user: User = request.state.user
        html = _env.get_template("account.html.j2").render(
            user=user, flash=FLASH.get(msg, ""), error=error, static=page.STATIC_DIR,
            min_len=MIN_PASSWORD_LENGTH)
        return HTMLResponse(html, status_code=status, headers={"Cache-Control": "no-store"})

    @app.get("/account")
    def account(request: Request, msg: str = ""):
        return account_page(request, msg)

    @app.post("/account/password")
    async def change_password(request: Request, current: str = Form(""), new: str = Form(""),
                              confirm: str = Form("")):
        user: User = request.state.user
        if not user.needs_password:
            ok = await asyncio.to_thread(users.verify, user.email, current)
            if ok is None:
                return account_page(request, error="Your current password isn't right.", status=400)
        if new != confirm:
            return account_page(request, error="The two new passwords don't match.", status=400)
        if problem := password_problem(new):
            return account_page(request, error=problem, status=400)
        user = await asyncio.to_thread(users.set_password, user.id, new)
        # Keep this device logged in; every other session's version is now stale.
        request.session["v"] = version(user)
        request.session["dev"] = device_mark(user)
        return RedirectResponse("/account?msg=pwset", status_code=303)

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
