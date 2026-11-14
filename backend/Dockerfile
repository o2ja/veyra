# syntax=docker/dockerfile:1
# Backend image (DEPLOYMENT.md §2.5). Targets: `dev` (dev deps, source bind-mounted) and `runtime`.
FROM ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv

FROM python:3.15.0-slim-trixie@sha256:f5b92f2a669a4c347044f29367f31983280b648250ca94dea66f98c53bc87e58 AS base
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
