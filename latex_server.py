#!/usr/bin/env python3
"""Compatibility shim for the former flat layout.

New integrations should use the ``mcp-latex-server`` console script or
``python -m mcp_latex_server``. This file remains so existing MCP client
configurations that launch ``latex_server.py`` continue to work.
"""

import sys
from pathlib import Path

try:
    from mcp_latex_server.server import main, mcp  # noqa: F401
except ModuleNotFoundError:
    # Support running directly from a source checkout without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
    from mcp_latex_server.server import main, mcp  # noqa: F401


if __name__ == "__main__":
    main()
