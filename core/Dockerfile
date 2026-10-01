# Digest-pinned for reproducible, supply-chain-safe builds (python:3.14.7-slim).
# Pin the multi-arch *index* digest, not a per-arch manifest: a manifest digest locks the
# build to one architecture, so arm64 hosts silently get an emulated amd64 image. The
# index still pins exact content — Docker just resolves the right platform from it.
# 3.14 (not the library's 3.11 floor) because it's the oldest line with no fixable High
# advisories against the CPython interpreter itself (IMG-1); the wheel still supports >=3.11.
FROM python@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d

# Debian security updates newer than the pinned base (e.g. OpenSSL), and drop the base
# image's system pip: the app runs from the uv venv and never uses it.
RUN apt-get update && apt-get -y upgrade && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y pip

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
