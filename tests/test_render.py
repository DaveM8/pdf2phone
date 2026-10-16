from pdf2phone.render import clean_markdown, render


def test_toc_levels_start_at_one_and_anchors_are_unique():
    r = render("## Intro\n\ntext\n\n### Part\n\n## Intro\n")
    assert [(e.level, e.text) for e in r.toc] == [(1, "Intro"), (2, "Part"), (1, "Intro")]
    assert [e.anchor for e in r.toc] == ["intro", "part", "intro-2"]
    assert 'id="intro-2"' in r.html


def test_bold_wrapped_heading_is_unwrapped():
    assert clean_markdown("# **1 Introduction** \n").strip() == "# 1 Introduction"


def test_lowercase_heading_fragment_becomes_text():
    out = clean_markdown("#### greatly benefited from very deep models.\n")
    assert out.strip() == "greatly benefited from very deep models."


def test_picture_text_and_comments_removed():
    md = "a\n\n<!-- Start of picture text -->\naxis<br>label\n<!-- End of picture text -->\n\nb <!-- x -->\n"
    out = clean_markdown(md)
    assert "axis" not in out and "<!--" not in out


def test_running_headers_and_page_numbers_removed():
    pages = "\n\n".join(f"Journal of Things\n\nBody text {i}.\n\n{i}" for i in range(1, 5))
    out = clean_markdown(pages)
    assert "Journal of Things" not in out
    assert "Body text 3." in out
    assert "\n3\n" not in f"\n{out}\n"


def test_raw_html_from_pdf_is_escaped():
    html = render("Hello <script>alert(1)</script>\n").html
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_tables_are_wrapped_in_a_scroll_box():
    html = render("| a | b |\n|---|---|\n| 1 | 2 |\n").html
    assert '<div class="scroll"><table>' in html and "</table></div>" in html


def test_latex_becomes_mathml_but_prices_do_not():
    assert "<math" in render("Energy $E=mc^2$ here.\n").html
    html = render("It costs $5 and $10 today.\n").html
    assert "<math" not in html and "$5 and $10" in html


def test_image_urls_are_mapped():
    html = render("![](fig.png)\n", image_url=lambda n: "data:x/" + n).html
    assert 'src="data:x/fig.png"' in html and 'loading="lazy"' in html


def test_flat_headings_nest_by_section_number():
    md = "## Abstract\n\n## 3. Method\n\n## 3.1. Part\n\n## 3.1.2 Detail\n\n## References\n"
    assert [(e.level, e.text) for e in render(md).toc] == [
        (1, "Abstract"), (1, "3. Method"), (2, "3.1. Part"), (3, "3.1.2 Detail"), (1, "References"),
    ]


def test_already_nested_headings_are_left_alone():
    md = "# Title\n\n## 3.1 Part\n"
    assert [e.level for e in render(md).toc] == [1, 2]
