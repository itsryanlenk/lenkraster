#!/usr/bin/env python3
"""Compatibility launcher for running the packaged MCP server from a checkout."""
from pathlib import Path
import sys


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from lenkraster.mcp_server import PROTOCOL, TOOLS, _handle, main  # noqa: E402,F401


if __name__ == "__main__":
    main()
