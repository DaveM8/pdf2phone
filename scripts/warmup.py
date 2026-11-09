"""Download every converter's models now (used while building the Docker image),
so the first real conversion on the server isn't slow.

    python scripts/warmup.py tests/fixtures/sample.pdf
"""

import sys
import time

from pdf2phone import engines

# The image must offer these; if one won't load, fail the build rather than ship
# an upload form with a converter silently missing.
REQUIRED = ("pymupdf", "docling", "docling-math")

pdf = sys.argv[1]
problems = engines.engine_problems()
broken = [f"  {name}: {problems[name]}" for name in REQUIRED if problems.get(name)]
if broken:
    sys.exit("warmup: required converters failed to load:\n" + "\n".join(broken))
for name in engines.available_engines():
    t = time.time()
    doc = engines.extract(pdf, name)
    print(f"warmup {name}: {len(doc.markdown)} chars in {time.time() - t:.0f}s", flush=True)
