"""A folder of converted documents plus an index page.

<library>/
  index.html                  list of documents (newest first, with progress)
  _static/                    reader.css, reader.js, fonts/
  <doc_id>/index.html         the reader page
  <doc_id>/<images>
  <doc_id>/source.md          engine markdown, for debugging / re-rendering
  <doc_id>/source.pdf         the original (kept so it can be re-converted)
  <doc_id>/meta.json
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from . import page
from .engines import Document

DEFAULT_LIBRARY = Path.home() / "pdf2phone-library"

_env = Environment(
    loader=PackageLoader("pdf2phone", "templates"),
    autoescape=select_autoescape(["html", "j2"]),
)


# Top-level names the web app uses for itself; never used as document ids.
RESERVED = {"_static", "_jobs", "_users", "_db", "_backups", "login", "logout", "account",
            "admin", "signup", "api", "d", "upload", "healthz",
            "manifest.webmanifest", "apple-touch-icon.png", "icon-512.png", "index.html"}


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", name.lower()).strip()
    s = re.sub(r"[\s_]+", "-", s)[:60] or "document"
    return f"{s}-doc" if s in RESERVED or s.startswith("_") else s


def ensure_static(library: Path) -> None:
    """Copy (refresh) shared assets so edits to the package show up."""
    dest = library / page.STATIC_DIR
    src = page.static_files()
    (dest / "fonts").mkdir(parents=True, exist_ok=True)
    for name in ("reader.css", "reader.js"):
        (dest / name).write_text((src / name).read_text())
    for f in (src / "fonts").iterdir():
        target = dest / "fonts" / f.name
        if not target.exists():
            target.write_bytes(f.read_bytes())


def add(library: Path, doc: Document, source: Path, doc_id: str | None = None,
        source_name: str | None = None, write_idx: bool = True, static: bool = True) -> Path:
    """Write ``doc`` into the library; returns the doc's index.html path.

    Re-adding an existing ``doc_id`` replaces the old version but keeps its
    ``source.pdf`` and original "added" date.  ``static=False`` skips copying
    the shared CSS/fonts (the web app keeps one shared copy for all users)."""
    library.mkdir(parents=True, exist_ok=True)
    if static:
        ensure_static(library)
    doc_id = doc_id or slugify(source.stem)
    source_name = source_name or source.name
    folder = library / doc_id
    added = time.time()
    pdf_bytes = source.read_bytes()
    if (folder / "meta.json").exists():
        added = json.loads((folder / "meta.json").read_text()).get("added", added)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir()
    (folder / "source.pdf").write_bytes(pdf_bytes)

    for name, data in doc.images.items():
        (folder / name).write_bytes(data)
    (folder / "source.md").write_text(doc.markdown)
    html = page.build(doc, doc_id=doc_id, source_name=source_name, mode="library", library_url="../")
    (folder / "index.html").write_text(html)
    (folder / "meta.json").write_text(json.dumps({
        "title": doc.title,
        "source": source_name,
        "engine": doc.engine,
        "added": added,
    }, indent=2))
    if write_idx:
        write_index(library)
    return folder / "index.html"


def list_docs(library: Path) -> list[dict]:
    docs = []
    for meta_path in library.glob("*/meta.json"):
        meta = json.loads(meta_path.read_text())
        meta["id"] = meta_path.parent.name
        meta["date"] = time.strftime("%d %b %Y", time.localtime(meta.get("added", 0)))
        docs.append(meta)
    docs.sort(key=lambda m: m.get("added", 0), reverse=True)
    return docs


def render_index(library: Path, web: dict | None = None) -> str:
    """Library page.  ``web`` (engines, jobs, ...) switches on the upload form
    and per-document controls used by the web app."""
    return _env.get_template("library.html.j2").render(docs=list_docs(library), static=page.STATIC_DIR, web=web)


def write_index(library: Path) -> None:
    ensure_static(library)
    (library / "index.html").write_text(render_index(library))
