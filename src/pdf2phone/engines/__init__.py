"""PDF -> Markdown engines.

Every engine exposes ``extract(pdf_path) -> Document`` so the rest of the
pipeline (and the future web service) doesn't care which one ran.
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass, field
from pathlib import Path

# name -> (module, pip extra that provides it)
ENGINES = {
    "pymupdf": ("pdf2phone.engines.pymupdf_engine", None),
    "docling": ("pdf2phone.engines.docling_engine", "docling"),
    "docling-math": ("pdf2phone.engines.docling_math_engine", "docling"),
    "markitdown": ("pdf2phone.engines.markitdown_engine", "markitdown"),
}
DEFAULT_ENGINE = "pymupdf"


@dataclass
class Document:
    markdown: str
    title: str
    # Image file name (as referenced in the markdown) -> raw bytes.
    images: dict[str, bytes] = field(default_factory=dict)
    engine: str = ""


class EngineUnavailable(RuntimeError):
    pass


def get_engine(name: str):
    if name not in ENGINES:
        raise ValueError(f"Unknown engine {name!r}; choose from {', '.join(ENGINES)}")
    module, extra = ENGINES[name]
    try:
        return importlib.import_module(module)
    except ImportError as e:
        hint = f" Install it with: uv sync --extra {extra}" if extra else ""
        raise EngineUnavailable(f"Engine {name!r} is not installed ({e}).{hint}") from e


def available_engines() -> list[str]:
    out = []
    for name in ENGINES:
        try:
            get_engine(name)
            out.append(name)
        except EngineUnavailable:
            pass
    return out


def extract(pdf_path: str | Path, engine: str = DEFAULT_ENGINE) -> Document:
    pdf_path = Path(pdf_path)
    doc = get_engine(engine).extract(pdf_path)
    doc.engine = engine
    if doc.title == _file_title(pdf_path):
        doc.title = _first_heading(doc.markdown) or doc.title
    return doc


def _file_title(pdf_path: Path) -> str:
    return pdf_path.stem.replace("_", " ").replace("-", " ")


def _first_heading(markdown: str) -> str | None:
    """The first top-level-ish heading near the start, if the engine found one."""
    for line in markdown.splitlines()[:40]:
        m = re.match(r"^#{1,2}\s+(.+?)\s*$", line)
        if m:
            text = re.sub(r"[*_`]", "", m.group(1)).strip()
            if 3 < len(text) < 200 and not text[:1].islower():
                return text
    return None


def title_from_pdf(pdf_path: Path) -> str:
    """PDF metadata title if it looks real, else the file name."""
    try:
        import pymupdf

        with pymupdf.open(pdf_path) as d:
            t = (d.metadata or {}).get("title", "").strip()
        # Many PDFs carry junk titles like "untitled" or "Microsoft Word - x.docx".
        if t and len(t) > 3 and not t.lower().startswith(("untitled", "microsoft word")):
            return t
    except Exception:
        pass
    return _file_title(pdf_path)
