# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A private web app (plus CLI) that converts PDFs into large-print reader pages for a **dyslexic** owner who reads on an iPhone, now gaining accounts so a few friends can use it. Reading comfort is the product. See the hard rules below before touching the reader. Current plan and milestones: `GOALS.md` (signed off; don't build outside it without asking).

## Commands

```sh
uv sync --extra web --extra docling --extra markitdown   # full dev environment (Python 3.12)
uv run pytest                                           # all tests (~6 s)
uv run pytest tests/test_web.py -k upload               # one test
uv run python scripts/make_icons.py                     # regenerate home-screen icons

# web app, local (cookies need https unless PDF2PHONE_INSECURE_COOKIES=1)
PDF2PHONE_ADMIN_EMAIL=you@example.com PDF2PHONE_PASSWORD=testpass1234 PDF2PHONE_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(32))') \
PDF2PHONE_LIBRARY=./library PDF2PHONE_INSECURE_COOKIES=1 \
uv run uvicorn --factory pdf2phone.web.app:create_app --reload

uv run pdf2phone convert|compare|serve ...               # CLI (src/pdf2phone/cli.py)
uv run pdf2phone migrate-accounts -l LIB [--dry-run]    # one-off: shared library -> admin account
uv run pdf2phone set-password EMAIL -l LIB              # emergency password reset
```

Deployment is Docker on the user's Hetzner server, behind their existing nginx. See `docs/DEPLOY.md`, `Dockerfile`, `docker-compose.yml` and `deploy/nginx-pdf2phone.conf`. Docker isn't installed on the dev Mac, so the image can't be built locally.

## Architecture

Pipeline: `engines.extract(pdf, engine)` → `Document(markdown, title, images)` → `render.render()` → `page.build()` → `library.add()`.

- `engines/`: one module per converter (`pymupdf` default, `docling`, `docling-math`, `markitdown`), registered in `engines/__init__.py:ENGINES`. Optional ones are imported lazily; `available_engines()` reports what is installed. `extract()` falls back to the first heading for the title when the PDF has no metadata title.
- `render.py`: `clean_markdown()` removes running headers/footers (by frequency), page numbers, pymupdf "picture text" blocks, bold-wrapped headings and lowercase fragment headings. When an engine emits flat headings (docling), they're nested by their section numbers. Then markdown-it renders with **`html: False`**, so text from a PDF is never treated as markup. Tables and display maths get a `.scroll` wrapper; LaTeX → MathML via latex2mathml, with strict `$` rules so prices aren't parsed as maths.
- `page.py`: Jinja `templates/reader.html.j2`. `mode="single"` inlines CSS, JS, fonts and images (base64); `mode="library"` links `../_static/`.
- `library.py`: folder layout `<lib>/<doc_id>/{index.html, images, source.md, source.pdf, meta.json}` plus `<lib>/_static/`. `render_index(web=...)` renders `templates/library.html.j2`. With `web` it adds the upload form, the jobs list and the per-document ⋯ menu. `RESERVED` names are never used as doc ids.
- Web library layout: `<lib>/_users/<user_id>/<doc_id>/...` (one CLI-style library per user), shared `<lib>/_static/`, `<lib>/_db/pdf2phone.db` (SQLite), `<lib>/_jobs/`, `<lib>/_backups/`. Doc URLs stay `/<doc_id>/` with **no user in the URL**: the server resolves them inside the logged-in user's folder, which is what makes libraries private (and keeps localStorage reading positions valid across the migration).
- `web/users.py`: `UserStore` (SQLite, one lock-guarded connection), Argon2id via argon2-cffi, emails lower-cased as login names, `verify()` always does one Argon2 check (dummy hash for unknown emails) so timing doesn't reveal accounts. `session_version()` = HMAC(secret, user id + pw_hash), so a password change logs out that user's other sessions.
- `web/migrate.py`: one-off move from the old shared layout. Writes and reads back a tar.gz in `_backups/` before moving anything. `create_app` refuses to start while `legacy_docs()` finds old-layout documents.
- `web/app.py`: `create_app(Settings)` factory (uvicorn `--factory`).
  - `RequireLogin` middleware gates **everything** except `PUBLIC_EXACT`/`PUBLIC_PREFIX` (login, healthz, `_static`, icons, manifest), checks `session["uid"]` + `session["v"]` against the user, and sets `request.state.user`. Nothing private may be added to the public lists.
  - The first admin is created from `PDF2PHONE_ADMIN_EMAIL` with no password. They log in once with the old shared `PDF2PHONE_PASSWORD`, then `SET_PASSWORD_PATHS` is all they can reach until they choose their own.
  - Every document route goes through `existing_doc(request, ...)` or `root_of(request)`. Never build a path from `settings.library` plus a doc id directly.
  - The catch-all `GET /{path}` serves `_static/` from `<lib>/_static`, and everything else from the user's own folder, with a resolve + `is_relative_to` check.
  - Flash messages are looked up by key; user input is never echoed.
- `web/jobs.py`: `JobQueue` with **one** worker thread shared by all users (Docling uses ~3 GB of memory). Every job has a `user_id`, and `recent`/`busy`/`doc_busy` are filtered by it. Job records are kept in `<lib>/_jobs/*.json`; queued/running jobs found at startup are marked `interrupted`.
- `web/auth.py`: `Throttle` brute-force protection. Every attempt is counted atomically in `begin()` before any await, and cleared by `succeed()`; 5 per IP in 15 min → lockout. When ≥30 attempts fail globally, logins from *unknown* devices are serialised through `throttle.serial` with a 2 s async sleep, and never refused. Devices whose signed session has `dev == "<uid>:<session version>"` for the account being logged into (set at login, kept on logout, revoked by that user's password change) skip that queue. Memory is bounded (LRU of IPs, deques with maxlen). Passwords must be ≥12 characters (`users.password_problem`), and the `.env.example` placeholder is rejected. Sessions last 30 days. Client IPs come from uvicorn `--proxy-headers --forwarded-allow-ips *`. That's safe only because compose publishes on 127.0.0.1 **and** nginx overwrites `X-Forwarded-For` with `$remote_addr`. Never switch it back to `$proxy_add_x_forwarded_for`.

## Hard rules

- **Horizontal spacing in `static/reader.css` is px only** (gutter, list/quote indents, cell padding), never em/rem, so A+ never reduces words per line. This is the user's main requirement, and `tests/test_page.py::test_horizontal_spacing_never_scales_with_text` guards it. Vertical spacing may use em.
- The reader must stay readable **without JavaScript** (the iOS Files preview may not run scripts): CSS defaults are big text and OpenDyslexic, and JS only adds controls.
- Reading position is stored per document as "block index + fraction" (`reader.js` `topBlock`/`restoreBlock`), not as a scroll percentage, so it survives text-size changes.
- Settings and positions live in `localStorage` under the keys `pdf2phone:settings` and `pdf2phone:pos:<doc_id>`; the library page reads them.
- PDF text is hostile input. `render._safe_mathml` rebuilds every formula from a MathML allowlist (latex2mathml copies `\text{}` verbatim and emits `\href`/`\style` attributes); only plain engine image names (`figure-1.png`) become `<img>`. Library reader pages are served with `READER_CSP` (`script-src 'self'`), so **never add inline `<script>` or `on…=` handlers to the library-mode reader** — put behaviour in `reader.js`.
- `docling-math` is opt-in: formula recognition took minutes on a 12-page paper versus ~16 s without it. `compare` skips it unless it's named.
- New web routes must be behind login, and new files under the library must be served through the path-checked catch-all.
- Test fixture: `tests/fixtures/sample.pdf` (from markitdown's MIT test files). The `markitdown/` folder in the parent directory is an unrelated clone and is git-ignored.

## Testing UI changes

There's no browser on the Mac except Safari. For iPhone-sized checks, the scratchpad approach used so far is Playwright WebKit (`pw.devices["iPhone 13"]`): measure the paragraph's left/right inset at `--size` 24 and 40px (both should be the gutter, 10px), and check that `scrollWidth - innerWidth == 0`.
