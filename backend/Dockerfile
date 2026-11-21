# syntax=docker/dockerfile:1
# Backend image (DEPLOYMENT.md §2.5). Targets: `dev` (dev deps, source bind-mounted) and `runtime`.
FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv

FROM python:3.15.2-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH
WORKDIR /app
# libmagic: magic-byte detection for uploads (python-magic, SECURITY.md §4)
# ocrmypdf + tesseract-ocr-eng: OCR fallback for scanned PDF pages (TECH_STACK.md, S6; resource-limited subprocess)
RUN apt-get update && apt-get install -y --no-install-recommends libmagic1 ocrmypdf tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*

FROM base AS deps
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# dev/test: all deps incl. dev tools; the source is bind-mounted at /app and imported via PYTHONPATH.
FROM deps AS dev
RUN uv sync --frozen --no-install-project
ENV PYTHONPATH=/app/src:/app

FROM deps AS builder
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM base AS runtime
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini ./
COPY migrations ./migrations
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=2)"]
CMD ["gunicorn", "rebate.api.asgi:app", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--workers", "2"]
