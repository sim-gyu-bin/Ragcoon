FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY app ./app
COPY evaluation ./evaluation

FROM base AS test
RUN uv sync --locked --group dev --no-install-project
COPY tests ./tests
CMD [".venv/bin/python", "-m", "pytest", "-q", "tests"]

FROM base AS runtime
CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
