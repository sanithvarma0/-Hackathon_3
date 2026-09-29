# MemoryOps backend (FastAPI + simulator + agent). docs/DEPLOY.md
FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# Dependencies first so code changes don't reinstall them
COPY pyproject.toml uv.lock README.md ./
# Optional build secret "ca": a corporate / sandbox TLS proxy CA. Unused on Render.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi; \
    uv sync --frozen --no-dev --no-install-project

COPY backend ./backend
COPY docs/eval ./docs/eval
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi; \
    uv sync --frozen --no-dev

# State lives on a mounted disk: the incident log, event bus and spend ledger survive redeploys
ENV SQLITE_PATH=/data/memoryops.db USAGE_DB_PATH=/data/usage.db PORT=8000
# Runs as root: Render mounts the persistent disk root-owned
RUN mkdir -p /data
EXPOSE 8000
# One worker: the simulator, event bus and agent run in-process
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
