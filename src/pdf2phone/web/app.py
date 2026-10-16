"""FastAPI app.

Run (development):
  PDF2PHONE_PASSWORD=... PDF2PHONE_SECRET=... PDF2PHONE_LIBRARY=./library \\
  PDF2PHONE_INSECURE_COOKIES=1 uv run uvicorn --factory pdf2phone.web.app:create_app --reload

Everything except the login page, health check and shared static assets
(CSS, fonts, icons — nothing private) requires a logged-in session.
"""

from __future__ import annotations

import asyncio
import hashlib
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
from .auth import GLOBAL_DELAY_SECONDS, Throttle, password_ok
from .jobs import ENGINE_LABELS, JobQueue
from .settings import Settings

PUBLIC_EXACT = {"/login", "/healthz", "/manifest.webmanifest", "/apple-touch-icon.png", "/icon-512.png"}
PUBLIC_PREFIX = ("/_static/",)
DOC_ID = re.compile(r"^[\w-]{1,80}$")

# Messages shown after a redirect; looked up by key so nothing user-supplied is echoed.
FLASH = {
    "queued": "Converting your PDF. It will appear below when it's ready.",
    "deleted": "Deleted.",
    "notpdf": "That file isn't a PDF.",
    "toobig": "That file is too big.",
    "empty": "No file was chosen.",
    "busy": "That document is being converted right now; try again when it's done.",
}

_env = Environment(loader=PackageLoader("pdf2phone", "templates"), autoescape=select_autoescape(["html", "j2"]))


def _session_version(settings: Settings) -> str:
    # Changing the password changes this, which logs out every device.
    return hashlib.sha256(f"{settings.secret}:{settings.password}".encode()).hexdigest()[:16]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    lib = settings.library
    lib.mkdir(parents=True, exist_ok=True)
    library.ensure_static(lib)
    queue = JobQueue(lib)
    throttle = Throttle()
    version = _session_version(settings)
    available = engines.available_engines()
    engine_options = [{"name": n, "label": ENGINE_LABELS.get(n, n)} for n in available]
    default_engine = settings.default_engine if settings.default_engine in available else available[0]
    static_root = Path(str(page.static_files()))

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.queue = queue  # tests use this

    # ------------------------------------------------------------ auth gate
    class RequireLogin(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            path = request.url.path
            if path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIX):
                return await call_next(request)
            if request.session.get("v") != version:
                if request.method == "GET" and not path.startswith("/api/"):
                    return RedirectResponse(f"/login?next={quote(path)}", status_code=303)
                return PlainTextResponse("Please log in.", status_code=401)
            return await call_next(request)

    app.add_middleware(RequireLogin)
    app.add_middleware(  # added last = runs first, so the session exists for RequireLogin
        SessionMiddleware,
        secret_key=settings.secret,
        session_cookie="pdf2phone",
        max_age=settings.session_days * 86400,
        same_site="lax",
        https_only=settings.secure_cookies,
    )

    # ------------------------------------------------------------ login
    def login_page(error: str = "", next_url: str = "/", status: int = 200) -> HTMLResponse:
        html = _env.get_template("login.html.j2").render(error=error, next=next_url, static=page.STATIC_DIR)
        return HTMLResponse(html, status_code=status)

    def safe_next(next_url: str) -> str:
        # Only same-site paths, never "//evil.com" or absolute URLs.
        return next_url if next_url.startswith("/") and not next_url.startswith("//") else "/"

    @app.get("/login")
    def login_form(next: str = "/"):
        return login_page(next_url=safe_next(next))

    @app.post("/login")
    async def login(request: Request, password: str = Form(""), next: str = Form("/")):
        ip = request.client.host if request.client else "unknown"
        wait = throttle.locked_for(ip)
        if wait:
            return login_page(f"Too many wrong tries. Try again in {wait // 60 + 1} minutes.", safe_next(next), 429)
        if throttle.under_attack():
            # async sleep: slows guessing without tying up a worker thread
            await asyncio.sleep(GLOBAL_DELAY_SECONDS)
        if not password_ok(password, settings.password):
            throttle.fail(ip)
            return login_page("That password isn't right.", safe_next(next), 401)
        throttle.succeed(ip)
        request.session.clear()
        request.session["v"] = version
        return RedirectResponse(safe_next(next), status_code=303)

    @app.get("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/healthz")
    def healthz():
        return PlainTextResponse("ok")

    # ------------------------------------------------------------ library
    @app.get("/")
    def index(msg: str = ""):
        web = {
            "engines": engine_options,
            "default_engine": default_engine,
            "jobs": [j.view() for j in queue.recent()],
            "busy": queue.busy(),
            "flash": FLASH.get(msg, ""),
            "max_mb": settings.max_mb,
        }
        return HTMLResponse(library.render_index(lib, web=web), headers={"Cache-Control": "no-store"})

    @app.get("/api/jobs")
    def jobs():
        return JSONResponse({"busy": queue.busy(), "jobs": [j.view() for j in queue.recent()]})

    @app.post("/upload")
    async def upload(pdf: UploadFile | None = None, engine: str = Form("")):
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
        queue.submit(doc_id=doc_id, source_name=name, engine=engine, upload=dest)
        return RedirectResponse("/?msg=queued", status_code=303)

    def existing_doc(doc_id: str) -> Path | None:
        if not DOC_ID.match(doc_id) or doc_id.startswith("_"):
            return None
        folder = lib / doc_id
        return folder if (folder / "meta.json").exists() else None

    def doc_busy(doc_id: str) -> bool:
        return any(j.doc_id == doc_id and j.state in ("queued", "running") for j in queue.jobs.values())

    @app.post("/d/{doc_id}/reconvert")
    def reconvert(doc_id: str, engine: str = Form("")):
        folder = existing_doc(doc_id)
        if folder is None or not (folder / "source.pdf").exists():
            return PlainTextResponse("Not found", status_code=404)
        if doc_busy(doc_id):
            return RedirectResponse("/?msg=busy", status_code=303)
        meta = library.list_docs(lib)
        source_name = next((m["source"] for m in meta if m["id"] == doc_id), "document.pdf")
        queue.submit(doc_id=doc_id, source_name=source_name,
                     engine=engine if engine in available else default_engine, upload=None)
        return RedirectResponse("/?msg=queued", status_code=303)

    @app.post("/d/{doc_id}/delete")
    def delete(doc_id: str):
        folder = existing_doc(doc_id)
        if folder is None:
            return PlainTextResponse("Not found", status_code=404)
        if doc_busy(doc_id):
            return RedirectResponse("/?msg=busy", status_code=303)
        shutil.rmtree(folder)
        return RedirectResponse("/?msg=deleted", status_code=303)

    @app.get("/d/{doc_id}/source.pdf")
    def original(doc_id: str):
        folder = existing_doc(doc_id)
        if folder is None or not (folder / "source.pdf").exists():
            return PlainTextResponse("Not found", status_code=404)
        meta = next((m for m in library.list_docs(lib) if m["id"] == doc_id), {})
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
    def files(path: str):
        target = (lib / path).resolve()
        root = lib.resolve()
        if not target.is_relative_to(root) or target.is_relative_to(root / "_jobs"):
            return PlainTextResponse("Not found", status_code=404)
        if target.is_dir():
            if path and not path.endswith("/"):
                # Reader pages use relative links (images, ../_static), so they need the slash.
                return RedirectResponse(f"/{path}/", status_code=301)
            target = target / "index.html"
        if not target.is_file():
            return PlainTextResponse("Not found", status_code=404)
        headers = {"Cache-Control": "no-cache"} if target.suffix == ".html" else {}
        return FileResponse(target, headers=headers)

    return app
