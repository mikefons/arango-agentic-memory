FROM python:3.11-slim

# uv for fast, reproducible installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN uv sync --no-dev

EXPOSE 8080
CMD ["uv", "run", "uvicorn", "arango_memory.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
