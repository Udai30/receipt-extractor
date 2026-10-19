# syntax=docker/dockerfile:1.7
# Multi-stage image: uv resolves the locked runtime dependencies in a builder stage; the
# final stage holds only the virtualenv, migrations and a non-root user (no dev deps, no uv).

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

# --- builder ------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# Dependencies first (cached layer until pyproject.toml / uv.lock change).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# Then the project itself, installed non-editable so the runtime stage needs no source tree.
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# --- runtime ------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app

# Owned by root and read-only for `app`: the process cannot modify its own code.
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER app:app
EXPOSE 8000
# The FastAPI app and uvicorn arrive with M0.5.
CMD ["uvicorn", "receipt_extractor.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
