"""MCP server adapter (DESIGN.md §21) — exposes the core's /v1 API as MCP tools."""

from .server import build_server, main

__all__ = ["build_server", "main"]
