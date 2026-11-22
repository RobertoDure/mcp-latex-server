#!/usr/bin/env python3
"""Minimal MCP stdio client for the LaTeX server.

Usage:
    LATEX_SERVER_BASE_PATH=/path/to/docs python examples/client_demo.py
"""

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_latex_server"],
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("Tools:", ", ".join(tool.name for tool in tools.tools))

            await session.call_tool(
                "create_latex_file",
                {"file_path": "demo.tex", "title": "MCP Demo", "content": "Hello."},
            )
            validation = await session.call_tool(
                "validate_latex", {"file_path": "demo.tex"}
            )
            words = await session.call_tool("count_words", {"file_path": "demo.tex"})
            print("Validation:", validation.content[0].text if validation.content else "")
            print("Word count:", words.content[0].text if words.content else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
