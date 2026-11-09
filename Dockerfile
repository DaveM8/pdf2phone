# pdf2phone web app — CPU-only image with Docling and its models baked in.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    HOME=/home/app \
    PDF2PHONE_LIBRARY=/data/library

COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /usr/local/bin/uv

# Libraries the PDF/image stack (OpenCV, layout models) loads at runtime.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 app \
 && mkdir -p /data/library /opt/venv \
 && chown -R app:app /data /opt/venv

USER app
WORKDIR /home/app/src
RUN uv venv /opt/venv

# CPU-only PyTorch first: the default wheels pull in gigabytes of GPU libraries.
# Keep these two versions the same as in uv.lock.
RUN uv pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cpu

# Everything else at exactly the versions in uv.lock (the ones the tests ran
# against), not whatever is newest on the day of the build. torch and its GPU
# packages are left out: the CPU-only torch above already covers them.
COPY --chown=app:app pyproject.toml uv.lock README.md ./
RUN uv export --frozen --no-dev --no-emit-project --extra web --extra docling --no-hashes -o /tmp/all.txt \
 && grep -v -E '^(torch|torchvision|triton|nvidia-[a-z0-9-]+|cuda-[a-z-]+)==' /tmp/all.txt > /tmp/locked.txt \
 && uv pip install -r /tmp/locked.txt \
 && rm /tmp/all.txt /tmp/locked.txt

COPY --chown=app:app src ./src
RUN uv pip install --no-deps .

# Download all converter models into the image (several minutes, ~1-2 GB).
COPY --chown=app:app scripts/warmup.py tests/fixtures/sample.pdf /tmp/warmup/
RUN python /tmp/warmup/warmup.py /tmp/warmup/sample.pdf && rm -rf /tmp/warmup /data/library/*

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# --proxy-headers: take the client address from X-Forwarded-For. Only nginx on the
# host can reach this port (compose publishes it on 127.0.0.1), and nginx must
# OVERWRITE that header with $remote_addr (deploy/nginx-pdf2phone.conf) — uvicorn
# trusting "*" uses the first entry, which a visitor could otherwise forge.
CMD ["uvicorn", "--factory", "pdf2phone.web.app:create_app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
