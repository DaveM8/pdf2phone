"""Docling with formula recognition (equations -> LaTeX -> MathML). Slow."""

from __future__ import annotations

from pathlib import Path

from . import Document
from . import docling_engine


def extract(pdf_path: Path) -> Document:
    return docling_engine.extract(pdf_path, formulas=True)
