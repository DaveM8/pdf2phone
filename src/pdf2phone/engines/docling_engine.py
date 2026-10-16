"""High-accuracy engine: IBM Docling (layout models; slower, better on papers)."""

from __future__ import annotations

import io
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import ImageRefMode, PictureItem

from . import Document, title_from_pdf

_converters: dict[bool, DocumentConverter] = {}


def _get_converter(formulas: bool) -> DocumentConverter:
    # Building a converter loads models; do it once per process.
    if formulas not in _converters:
        opts = PdfPipelineOptions()
        # Formulas -> LaTeX uses a much heavier model: a 12-page paper took
        # ~16s without it and several minutes with it, so it's opt-in.
        opts.do_formula_enrichment = formulas
        opts.generate_picture_images = True
        opts.images_scale = 2.0
        _converters[formulas] = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
    return _converters[formulas]


def extract(pdf_path: Path, formulas: bool = False) -> Document:
    result = _get_converter(formulas).convert(str(pdf_path))
    doc = result.document

    images: dict[str, bytes] = {}
    for item, _level in doc.iterate_items():
        if isinstance(item, PictureItem):
            img = item.get_image(doc)
            if img is not None:
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                images[f"figure-{len(images) + 1}.png"] = buf.getvalue()

    md = doc.export_to_markdown(image_mode=ImageRefMode.PLACEHOLDER)
    # Replace each "<!-- image -->" placeholder with the matching picture, in order.
    for name in images:
        md = md.replace("<!-- image -->", f"![]({name})", 1)
    md = md.replace("<!-- image -->", "")
    return Document(markdown=md, title=title_from_pdf(pdf_path), images=images)
