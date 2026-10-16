"""Build the final reader page.

mode="single":  one self-contained .html (fonts, CSS, JS, images inlined) —
                for AirDrop / iCloud Drive / the iOS Files app.
mode="library": page lives in <library>/<doc_id>/index.html and shares
                <library>/_static/ — for `pdf2phone serve` and the future web app.
"""

from __future__ import annotations

import base64
import mimetypes
import re
from importlib import resources

from jinja2 import Environment, PackageLoader, select_autoescape

from .engines import Document
from .render import render

STATIC_DIR = "_static"

_env = Environment(
    loader=PackageLoader("pdf2phone", "templates"),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def static_files() -> resources.abc.Traversable:
    return resources.files("pdf2phone") / "static"


def _data_uri(data: bytes, name: str) -> str:
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if name.endswith(".woff2"):
        mime = "font/woff2"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _inline_css() -> str:
    css = (static_files() / "reader.css").read_text()
    fonts = static_files() / "fonts"

    def repl(m: re.Match) -> str:
        name = m.group(1)
        return f'url("{_data_uri((fonts / name).read_bytes(), name)}")'

    return re.sub(r'url\("fonts/([^"]+)"\)', repl, css)


def build(
    doc: Document,
    *,
    doc_id: str,
    source_name: str,
    mode: str = "single",
    library_url: str | None = None,
) -> str:
    if mode not in ("single", "library"):
        raise ValueError(mode)
    inline = mode == "single"

    if inline:
        image_url = lambda name: _data_uri(doc.images[name], name) if name in doc.images else name  # noqa: E731
    else:
        image_url = lambda name: name  # noqa: E731  images sit next to index.html

    rendered = render(doc.markdown, image_url=image_url)
    ctx = dict(
        title=doc.title,
        doc_id=doc_id,
        body=rendered.html,
        toc=rendered.toc,
        engine=doc.engine or "unknown engine",
        source_name=source_name,
        inline=inline,
        library_url=library_url,
        static=f"../{STATIC_DIR}",
    )
    if inline:
        ctx["css"] = _inline_css()
        ctx["js"] = (static_files() / "reader.js").read_text()
    return _env.get_template("reader.html.j2").render(**ctx)
