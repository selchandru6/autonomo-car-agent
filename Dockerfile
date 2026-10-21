# syntax=docker/dockerfile:1.7
# Multi-stage build. Runtime image carries Chromium (Playwright) for the browser fallback,
# runs as a non-root user, and holds no secrets: they arrive via environment at run time.

FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    CARSHOP_DATA_DIR=/data \
    CARSHOP_WEB_HOST=0.0.0.0 \
    TZ=UTC
RUN apt-get update && apt-get install -y --no-install-recommends tzdata sqlite3 cron ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /opt/venv /opt/venv
# Chromium plus its system libraries, installed by Playwright's own tool so versions match.
RUN playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*
RUN groupadd -r carshop && useradd -r -g carshop -d /home/carshop -m carshop \
    && mkdir -p /data && chown -R carshop:carshop /data /ms-playwright
WORKDIR /app
COPY --chown=carshop:carshop alembic.ini ./
COPY --chown=carshop:carshop migrations ./migrations
COPY --chown=carshop:carshop deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
USER carshop
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)" || exit 1
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["serve"]
