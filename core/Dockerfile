# Digest-pinned for reproducible, supply-chain-safe builds (python:3.11.15-slim).
# Pin the multi-arch *index* digest, not a per-arch manifest: a manifest digest locks the
# build to one architecture, so arm64 hosts silently get an emulated amd64 image. The
# index still pins exact content — Docker just resolves the right platform from it.
FROM python@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93

# uv for fast, reproducible installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN uv sync --no-dev

# Import from src directly so uvicorn finds the package regardless of the
# editable-install state (uv's src-layout editable .pth can be flaky).
ENV PYTHONPATH=/app/src

# Run as an unprivileged user (least privilege); own the app dir + venv.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8080

# Liveness: the process answers /health (always 200 when up; not gated on the DB).
# Orchestrators should additionally wire a readiness probe to /ready.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health', timeout=2).status==200 else 1)"

CMD ["uv", "run", "--no-sync", "uvicorn", "arango_memory.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
