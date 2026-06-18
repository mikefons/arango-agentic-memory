"""ArangoDB Agentic Memory — Python core."""

from importlib.metadata import PackageNotFoundError, version

try:  # single source of truth: the version declared in pyproject.toml
    __version__ = version("arango-memory")
except PackageNotFoundError:  # raw checkout without an install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
