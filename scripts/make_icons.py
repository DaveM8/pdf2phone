"""Regenerate the home-screen icons ("Aa" in OpenDyslexic on cream).

    uv run python scripts/make_icons.py
"""

import tempfile
import urllib.request
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1] / "src/pdf2phone/static"
# MuPDF can't read woff2, so fetch the .otf of the same font from the official repo.
OTF_URL = "https://raw.githubusercontent.com/antijingoist/opendyslexic/main/compiled/OpenDyslexic-Bold.otf"
FONT = Path(tempfile.gettempdir()) / "OpenDyslexic-Bold.otf"
if not FONT.exists():
    urllib.request.urlretrieve(OTF_URL, FONT)


def make(size: int, out: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=100, height=100)
    page.draw_rect(page.rect, color=None, fill=(0.984, 0.961, 0.902))  # #fbf5e6
    page.draw_rect(pymupdf.Rect(0, 88, 100, 100), color=None, fill=(0.761, 0.255, 0.047))  # accent bar
    font = pymupdf.Font(fontbuffer=FONT.read_bytes())
    fs = 44
    width = font.text_length("Aa", fontsize=fs)
    tw = pymupdf.TextWriter(page.rect)
    tw.append(((100 - width) / 2, 62), "Aa", font=font, fontsize=fs)
    tw.write_text(page, color=(0.114, 0.106, 0.094))
    page.get_pixmap(matrix=pymupdf.Matrix(size / 100, size / 100), alpha=False).save(out)


make(180, ROOT / "icons/apple-touch-icon.png")
make(512, ROOT / "icons/icon-512.png")
print("ok")
