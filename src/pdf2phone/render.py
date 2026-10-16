"""Markdown -> reader HTML fragment + table of contents.

Engine output is cleaned first (repeated page headers/footers, bare page
numbers, bold-wrapped headings), then rendered with markdown-it.  Raw HTML in
the markdown is NOT passed through: PDF text is data, never markup.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

import latex2mathml.converter
from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.footnote import footnote_plugin


@dataclass
class TocEntry:
    level: int
    text: str
    anchor: str


@dataclass
class Rendered:
    html: str
    toc: list[TocEntry] = field(default_factory=list)


# ---------------------------------------------------------------- clean-up

_PAGE_NUMBER = re.compile(r"^\s*(page\s+)?\d{1,4}(\s*(/|of)\s*\d{1,4})?\s*$", re.I)
_BOLD_HEADING = re.compile(r"^(#{1,6})\s+\*\*(.+?)\*\*\s*$")


_PICTURE_TEXT = re.compile(r"<!-- Start of picture text -->.*?<!-- End of picture text -->", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def clean_markdown(md: str) -> str:
    md = md.replace("\r\n", "\n")
    # pymupdf4llm dumps axis labels etc. from inside figures; the figure image
    # already shows them.  Other engine comments are noise too.
    md = _PICTURE_TEXT.sub("", md)
    md = _HTML_COMMENT.sub("", md)
    lines = [ln.rstrip() for ln in md.split("\n")]

    # A short line that stands alone as a paragraph and repeats on 3+ pages is
    # a running header/footer (journal name, author, "Confidential", ...).
    def standalone(i: int) -> bool:
        before = i == 0 or not lines[i - 1].strip()
        after = i == len(lines) - 1 or not lines[i + 1].strip()
        return before and after

    counts = Counter(
        ln.strip()
        for i, ln in enumerate(lines)
        if ln.strip() and standalone(i) and len(ln.strip()) < 80
    )
    out = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s and standalone(i) and not s.startswith(("#", "|", "!")):
            if _PAGE_NUMBER.match(s) or counts[s] >= 3:
                continue
        if s.startswith("-----") and set(s) == {"-"}:
            continue  # page separators some engines emit
        m = _BOLD_HEADING.match(ln)
        if m:
            ln = f"{m.group(1)} {m.group(2)}"
        m = re.match(r"^#{1,6}\s+(.*)$", ln)
        if m and m.group(1)[:1].islower():
            # A sentence carried over from the previous column/page that the
            # engine mistook for a heading ("greatly benefited from ...").
            ln = m.group(1)
        if s.startswith("|"):
            ln = re.sub(r"<br\s*/?>", " ", ln)  # line breaks inside table cells
        out.append(ln)

    text = "\n".join(_nest_flat_headings(out))
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


_NUMBERED = re.compile(r"^(#{1,6})\s+((?:\d+|[A-Z])(?:\.\d+)*)\.?\s")


def _nest_flat_headings(lines: list[str]) -> list[str]:
    """Some engines (docling) emit every heading at one level.  If so, use
    section numbering for depth: "3." -> level 1, "3.1." -> 2, "3.1.2" -> 3;
    unnumbered headings ("Abstract", "References") stay at the top level."""
    levels = {len(m.group(1)) for ln in lines if (m := re.match(r"^(#{1,6})\s", ln))}
    if len(levels) != 1:
        return lines
    base = levels.pop()
    if not any(_NUMBERED.match(ln) and "." in _NUMBERED.match(ln).group(2) for ln in lines):
        return lines  # no sub-numbering to go on
    out = []
    for ln in lines:
        m = _NUMBERED.match(ln)
        if m:
            depth = m.group(2).count(".") + 1
            ln = "#" * min(6, base + depth - 1) + ln[len(m.group(1)):]
        out.append(ln)
    return out


# ---------------------------------------------------------------- rendering

def _slug(text: str, used: set[str]) -> str:
    base = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    base = re.sub(r"[\s_]+", "-", base)[:60] or "section"
    slug, n = base, 2
    while slug in used:
        slug, n = f"{base}-{n}", n + 1
    used.add(slug)
    return slug


def _math(latex: str, options: dict) -> str:
    display = "block" if options.get("display_mode") else "inline"
    try:
        mathml = latex2mathml.converter.convert(latex, display=display)
    except Exception:
        mathml = f"<code>{html.escape(latex)}</code>"
    if display == "block":
        return f'<div class="scroll math">{mathml}</div>\n'
    return mathml


def _build_parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": True})
    md.enable(["table", "strikethrough"])
    md.use(footnote_plugin)
    # Strict dollar rules so "$5 and $10" in ordinary documents isn't maths.
    md.use(dollarmath_plugin, allow_space=False, allow_digits=False, renderer=_math)

    def table_open(self, tokens, idx, options, env):
        return '<div class="scroll"><table>\n'

    def table_close(self, tokens, idx, options, env):
        return "</table></div>\n"

    md.add_render_rule("table_open", table_open)
    md.add_render_rule("table_close", table_close)
    return md


_parser = _build_parser()


def render(markdown: str, image_url: Callable[[str], str] = lambda name: name) -> Rendered:
    """Render cleaned markdown.  ``image_url`` maps an engine image name to
    the URL to use in the page (relative path, or a data: URI)."""
    md = clean_markdown(markdown)
    tokens = _parser.parse(md)

    toc: list[TocEntry] = []
    used: set[str] = set()
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open":
            inline = tokens[i + 1]
            text = "".join(c.content for c in (inline.children or []) if c.type in ("text", "code_inline"))
            text = text.strip() or inline.content.strip()
            anchor = _slug(text, used)
            tok.attrSet("id", anchor)
            toc.append(TocEntry(level=int(tok.tag[1]), text=text, anchor=anchor))
        elif tok.type == "inline":
            for child in tok.children or []:
                if child.type == "image":
                    src = child.attrGet("src") or ""
                    if not re.match(r"^(https?:|data:)", src):
                        child.attrSet("src", image_url(src))
                    child.attrSet("loading", "lazy")

    body = _parser.renderer.render(tokens, _parser.options, {})
    return Rendered(html=body, toc=_normalise_toc(toc))


def _normalise_toc(toc: list[TocEntry]) -> list[TocEntry]:
    """Shift levels so the shallowest heading is level 1 (engines often start at ##)."""
    if not toc:
        return toc
    top = min(e.level for e in toc)
    for e in toc:
        e.level = e.level - top + 1
    return toc
