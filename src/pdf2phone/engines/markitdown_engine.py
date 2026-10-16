"""Comparison engine: Microsoft markitdown (plain text, no headings)."""

from __future__ import annotations

from pathlib import Path

from markitdown import MarkItDown

from . import Document, title_from_pdf


def extract(pdf_path: Path) -> Document:
    result = MarkItDown().convert_local(str(pdf_path))
    return Document(markdown=result.markdown, title=title_from_pdf(pdf_path))
