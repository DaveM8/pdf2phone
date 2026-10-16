# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A private, single-user web app (plus CLI) that converts PDFs into large-print reader pages for a **dyslexic** user who reads on an iPhone. Reading comfort is the product. See the hard rules below before touching the reader.

## Commands

```sh
uv sync --extra web --extra docling --extra markitdown   # full dev environment (Python 3.12)
uv run pytest                                           # all tests (~6 s)
uv run pytest tests/test_web.py -k upload               # one test
uv run python scripts/make_icons.py                     # regenerate home-screen icons

# web app, local (cookies need https unless PDF2PHONE_INSECURE_COOKIES=1)
PDF2PHONE_PASSWORD=testpass1234 PDF2PHONE_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(32))') \
PDF2PHONE_LIBRARY=./library PDF2PHONE_INSECURE_COOKIES=1 \
uv run uvicorn --factory pdf2phone.web.app:create_app --reload

uv run pdf2phone convert|compare|serve ...               # CLI (src/pdf2phone/cli.py)
```

Deployment is Docker on the user's Hetzner server, behind their existing nginx. See `docs/DEPLOY.md`, `Dockerfile`, `docker-compose.yml` and `deploy/nginx-pdf2phone.conf`. Docker isn't installed on the dev Mac, so the image can't be built locally.

## Architecture

Pipeline: `engines.extract(pdf, engine)` → `Document(markdown, title, images)` → `render.render()` → `page.build()` → `library.add()`.

- `engines/`: one module per converter (`pymupdf` default, `docling`, `docling-math`, `markitdown`), registered in `engines/__init__.py:ENGINES`. Optional ones are imported lazily; `available_engines()` reports what is installed. `extract()` falls back to the first heading for the title when the PDF has no metadata title.
- `render.py`: `clean_markdown()` removes running headers/footers (by frequency), page numbers, pymupdf "picture text" blocks, bold-wrapped headings and lowercase fragment headings. When an engine emits flat headings (docling), they're nested by their section numbers. Then markdown-it renders with **`html: False`**, so text from a PDF is never treated as markup. Tables and display maths get a `.scroll` wrapper; LaTeX → MathML via latex2mathml, with strict `$` rules so prices aren't parsed as maths.
- `page.py`: Jinja `templates/reader.html.j2`. `mode="single"` inlines CSS, JS, fonts and images (base64); `mode="library"` links `../_static/`.
- `library.py`: folder layout `<lib>/<doc_id>/{index.html, images, source.md, source.pdf, meta.json}` plus `<lib>/_static/`. `render_index(web=...)` renders `templates/library.html.j2`. With `web` it adds the upload form, the jobs list and the per-document ⋯ menu. `RESERVED` names are never used as doc ids.
- `web/app.py`: `create_app(Settings)` factory (uvicorn `--factory`).
  - `RequireLogin` middleware gates **everything** except `PUBLIC_EXACT`/`PUBLIC_PREFIX` (login, healthz, `_static`, icons, manifest). Nothing private may be added to those.
  - Signed session cookie (`SessionMiddleware`). The session value is a hash of secret+password, so changing either logs everyone out.
  - The catch-all `GET /{path}` serves library files, with a resolve + `is_relative_to` check that also blocks `_jobs/`.
  - Flash messages are looked up by key; user input is never echoed.
- `web/jobs.py`: `JobQueue` with **one** worker thread (Docling uses ~3 GB of memory). Job records are kept in `<lib>/_jobs/*.json`; queued/running jobs found at startup are marked `interrupted`.
- `web/auth.py`: `hmac.compare_digest` password check, a per-IP throttle (5 failures → 15 min lockout) and a global backstop that doesn't rely on IPs: after 30 failures from anywhere, every attempt is *delayed* 2 s (async sleep) but never refused, so attackers can't lock the user out. The password must be ≥12 characters. Client IPs come from uvicorn `--proxy-headers --forwarded-allow-ips *`. That's safe only because compose publishes the port on 127.0.0.1 **and** the nginx config overwrites `X-Forwarded-For` with `$remote_addr`. Never switch it back to `$proxy_add_x_forwarded_for`.

## Hard rules

- **Horizontal spacing in `static/reader.css` is px only** (gutter, list/quote indents, cell padding), never em/rem, so A+ never reduces words per line. This is the user's main requirement, and `tests/test_page.py::test_horizontal_spacing_never_scales_with_text` guards it. Vertical spacing may use em.
- The reader must stay readable **without JavaScript** (the iOS Files preview may not run scripts): CSS defaults are big text and OpenDyslexic, and JS only adds controls.
- Reading position is stored per document as "block index + fraction" (`reader.js` `topBlock`/`restoreBlock`), not as a scroll percentage, so it survives text-size changes.
- Settings and positions live in `localStorage` under the keys `pdf2phone:settings` and `pdf2phone:pos:<doc_id>`; the library page reads them.
- `docling-math` is opt-in: formula recognition took minutes on a 12-page paper versus ~16 s without it. `compare` skips it unless it's named.
- New web routes must be behind login, and new files under the library must be served through the path-checked catch-all.
- Test fixture: `tests/fixtures/sample.pdf` (from markitdown's MIT test files). The `markitdown/` folder in the parent directory is an unrelated clone and is git-ignored.

## Testing UI changes

There's no browser on the Mac except Safari. For iPhone-sized checks, the scratchpad approach used so far is Playwright WebKit (`pw.devices["iPhone 13"]`): measure the paragraph's left/right inset at `--size` 24 and 40px (both should be the gutter, 10px), and check that `scrollWidth - innerWidth == 0`.
