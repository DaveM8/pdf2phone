"""Download every converter's models now (used while building the Docker image),
so the first real conversion on the server isn't slow.

    python scripts/warmup.py tests/fixtures/sample.pdf
"""

import sys
import time

from pdf2phone import engines

pdf = sys.argv[1]
for name in engines.available_engines():
    t = time.time()
    doc = engines.extract(pdf, name)
    print(f"warmup {name}: {len(doc.markdown)} chars in {time.time() - t:.0f}s", flush=True)
