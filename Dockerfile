FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /uvx /usr/local/bin/

RUN useradd --create-home --shell /bin/bash app
WORKDIR /app

COPY --chown=app:app pyproject.toml uv.lock* ./
COPY --chown=app:app src ./src

USER app
RUN uv sync --frozen --no-dev || uv sync --no-dev

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "adv_lens.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
