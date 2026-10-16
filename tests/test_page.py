import re
from pathlib import Path

from pdf2phone import engines, library, page
from pdf2phone.engines import Document

SAMPLE_PDF = Path(__file__).parent / "fixtures/sample.pdf"


def _doc():
    return Document(markdown="# Title\n\n## One\n\nHello.\n\n![](a.png)\n", title="T", images={"a.png": b"\x89PNG"}, engine="test")


def test_single_file_is_self_contained():
    html = page.build(_doc(), doc_id="t", source_name="t.pdf", mode="single")
    assert 'url("fonts/' not in html
    assert "data:font/woff2;base64," in html
    assert 'src="data:image/png;base64,' in html
    assert "<link" not in html and "<script src" not in html


def test_library_mode_references_shared_static():
    html = page.build(_doc(), doc_id="t", source_name="t.pdf", mode="library", library_url="../")
    assert 'href="../_static/reader.css"' in html and 'src="a.png"' in html


def test_horizontal_spacing_never_scales_with_text():
    """The user's key requirement: margins/indents are px, so A+ doesn't steal words per line."""
    css = (page.static_files() / "reader.css").read_text()
    assert "--gutter: 10px" in css
    offenders = re.findall(r"(padding|margin)(-left|-right|-inline[-\w]*)?\s*:[^;]*\d(r?em)\b[^;]*;", css)
    horizontal = [o for o in offenders if o[1]]  # shorthand margins are vertical-only here
    assert not horizontal, horizontal
    for shorthand in re.findall(r"article[^{]*\{[^}]*\b(?:padding|margin):\s*([^;]+);", css):
        parts = shorthand.split()
        if len(parts) >= 2:  # second value is left/right
            assert not re.search(r"r?em$", parts[1]) or parts[1] in ("auto",), shorthand


def test_convert_sample_pdf_into_library(tmp_path):
    doc = engines.extract(SAMPLE_PDF, "pymupdf")
    assert "Introduction" in doc.markdown
    index = library.add(tmp_path, doc, SAMPLE_PDF)
    assert index.exists()
    assert (tmp_path / "_static/fonts/OpenDyslexic-Regular.woff2").exists()
    assert "sample.pdf" in (tmp_path / "index.html").read_text()
