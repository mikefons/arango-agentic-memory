"""Entry point: `python -m arango_memory.mcp` runs the MCP server (stdio by default;
`--transport http` for Streamable HTTP — see `server.main`)."""

from .server import main

main()
