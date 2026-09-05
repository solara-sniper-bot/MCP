"""Solana Sniper Bot MCP Server package.

The MCP server is loaded lazily so strategy configuration can be imported by
the bot and GUI without constructing a FastMCP server as a side effect.

Version is kept in sync with the Windows application release version.

The unified server exposes 208 tools covering Meme, Perpetuals, and Spot
trading modes in a single FastMCP instance.
"""

__version__ = "4.0.3"

__all__ = ["mcp", "main", "__version__"]


def __getattr__(name):
    if name in __all__:
        from .server import main, mcp
        return {"mcp": mcp, "main": main}[name]
    raise AttributeError(name)
