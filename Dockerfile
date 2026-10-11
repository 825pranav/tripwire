# One image for every Python service; compose picks the command.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY sql ./sql
COPY tripwire ./tripwire
RUN uv sync --frozen --no-dev
ENV PATH=/app/.venv/bin:$PATH
