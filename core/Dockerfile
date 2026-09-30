# Digest-pinned for reproducible, supply-chain-safe builds (python:3.11.16-slim).
# Pin the multi-arch *index* digest, not a per-arch manifest: a manifest digest locks the
# build to one architecture, so arm64 hosts silently get an emulated amd64 image. The
# index still pins exact content — Docker just resolves the right platform from it.
FROM python@sha256:da047cb8f9d1d98e5c070f5300ba9f7274e33b8fc0e5be5ed88740aed1b95ba9

# uv for fast, reproducible installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

# The `mcp` extra lets this one image also run the MCP server over Streamable HTTP
# (a different command; see docs/adapters/mcp.md). It adds ~9 small packages.
RUN uv sync --no-dev --extra mcp

# Import from src directly so uvicorn finds the package regardless of the
# editable-install state (uv's src-layout editable .pth can be flaky).
ENV PYTHONPATH=/app/src
# The app venv first on PATH, so `python -m arango_memory.mcp …` (the MCP role) and any
# ad-hoc `python` run inside the container use the synced environment, not the base image's.
ENV PATH=/app/.venv/bin:$PATH

# Run as an unprivileged user (least privilege); own the app dir + venv.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8080

# Liveness: the process answers /health (always 200 when up; not gated on the DB).
# Orchestrators should additionally wire a readiness probe to /ready (core only).
# HEALTHCHECK_URL points it elsewhere when this image runs the MCP server instead
# (e.g. http://localhost:8000/health).
ENV HEALTHCHECK_URL=http://localhost:8080/health
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen(os.environ['HEALTHCHECK_URL'], timeout=2).status==200 else 1)"

CMD ["uv", "run", "--no-sync", "uvicorn", "arango_memory.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
