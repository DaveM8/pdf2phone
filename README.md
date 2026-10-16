# pdf2phone

A private web app that turns PDFs into large-print, dyslexia-friendly pages for reading on a phone.

Upload a PDF from your phone. It's converted to Markdown, then into a reader page with:

- **Big text by default** (24px) with A−/A+, and **side margins fixed in pixels**, so bigger text never means fewer words per line
- **OpenDyslexic built into the page**, so nothing needs installing on iOS. Atkinson Hyperlegible, Lexend and the system font are also available.
- A contents menu, a reading progress bar, and your place remembered in each document. Your place also survives text-size changes.
- Cream, white, dark or automatic colours, with adjustable line, word and letter spacing
- Wide tables and maths scroll sideways on their own; the page never does

## Documentation

- **[docs/DEPLOY.md](docs/DEPLOY.md)**: putting it on your server (Hetzner/Ubuntu, Docker, behind your existing nginx, HTTPS)
- **[docs/USING.md](docs/USING.md)**: everyday use from the phone
- Both also exist as large-print reader pages: `docs/DEPLOY.html`, `docs/USING.html`

## Converters

| name | on the upload form | speed | good for |
|---|---|---|---|
| `pymupdf` | Fast | seconds | most documents, including two-column papers; headings, images; removes page headers/footers |
| `docling` | Careful (Docling) | ~15–60 s | complex layouts, tables, scans |
| `docling-math` | Careful + equations | minutes | maths-heavy papers (equations become real maths) |
| `markitdown` | — (CLI only) | fast | comparison only: no headings or images |

## Running locally (Mac)

```sh
uv sync --extra web --extra docling      # add --extra markitdown for the comparison engine

# Web app on http://localhost:8000
PDF2PHONE_PASSWORD=some-password PDF2PHONE_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(32))') \
PDF2PHONE_LIBRARY=./library PDF2PHONE_INSECURE_COOKIES=1 \
uv run uvicorn --factory pdf2phone.web.app:create_app --reload

# Or the command line, without the web app
uv run pdf2phone convert paper.pdf                     # into ~/pdf2phone-library
uv run pdf2phone convert paper.pdf -s -o ~/Desktop     # one self-contained .html
uv run pdf2phone compare paper.pdf                     # every converter side by side
uv run pdf2phone serve                                 # library over home Wi-Fi

uv run pytest
```

## Licences

- Code: yours.
- Fonts: SIL Open Font License; sources are listed in `src/pdf2phone/static/fonts/README`.
- pymupdf4llm/PyMuPDF are AGPL. That's fine for a private, personal service, but making the service available to other people would bring AGPL obligations.
