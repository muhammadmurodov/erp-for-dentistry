# syntax=docker/dockerfile:1

# ---- builder: resolve and install dependencies into a venv -----------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml ./
# Dependencies first so a source-only change does not reinstall the world.
RUN uv venv /opt/venv && VIRTUAL_ENV=/opt/venv uv pip install --no-cache -r pyproject.toml

# ---- runtime ---------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl \
 && rm -rf /var/lib/apt/lists/* \
 # Run as a non-root user: a compromised process should not own the image.
 && groupadd --gid 1000 erp \
 && useradd --uid 1000 --gid erp --create-home erp

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=erp:erp alembic.ini ./
COPY --chown=erp:erp alembic ./alembic
COPY --chown=erp:erp app ./app

USER erp
EXPOSE 8000

# The orchestrator decides whether the instance is in rotation; the readiness
# probe reports the database, not just the process.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/ready || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
