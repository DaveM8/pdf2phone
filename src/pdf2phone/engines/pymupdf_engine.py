"""Default engine: pymupdf4llm (uses PyMuPDF's layout model when installed)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pymupdf4llm

from . import Document, title_from_pdf


def extract(pdf_path: Path) -> Document:
    with tempfile.TemporaryDirectory() as tmp:
        md = pymupdf4llm.to_markdown(
            str(pdf_path),
            header=False,  # drop running page headers
            footer=False,  # ...and footers / page numbers
            write_images=True,
            image_path=tmp,
            image_format="png",
            show_progress=False,
        )
        images = {}
        for p in Path(tmp).iterdir():
            images[p.name] = p.read_bytes()
            # The markdown references the absolute temp path; make it relative.
            md = md.replace(str(p), p.name)
    return Document(markdown=md, title=title_from_pdf(pdf_path), images=images)
