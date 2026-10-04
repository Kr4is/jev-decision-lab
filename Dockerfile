# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# dependencies first, so source edits do not invalidate this layer
COPY pyproject.toml uv.lock ./
COPY jev-vs-llm/pyproject.toml jev-vs-llm/
COPY decision-patterns/pyproject.toml decision-patterns/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package jev-vs-llm --extra web --no-install-workspace

# editable install on purpose: the labeled dataset lives next to the package (jev-vs-llm/data)
COPY jev-vs-llm jev-vs-llm
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package jev-vs-llm --extra web


FROM python:3.12-slim-bookworm
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=build --chown=app /app /app
USER app
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8765/healthz', timeout=2)"
# 0.0.0.0 is the container's own interface: publish the port deliberately (see docker-compose.yml)
CMD ["jevllm-web", "--host", "0.0.0.0", "--port", "8765"]
