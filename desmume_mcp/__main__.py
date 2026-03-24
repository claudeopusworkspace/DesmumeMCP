"""CLI entry point: python -m desmume_mcp"""

from __future__ import annotations

import os
import sys


def main() -> None:
    # Set headless SDL drivers before any library loading
    if "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    # MCP stdio transport uses stdout for JSON-RPC.
    # Redirect stdout to stderr during setup to prevent corruption.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        from desmume_mcp.server import create_server

        server = create_server()
    finally:
        sys.stdout = real_stdout

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
