#!/usr/bin/env python3
"""
Quick setup script for MCP LaTeX Server.
Installs dependencies, verifies the server, and configures Claude Desktop.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_PYTHON = (3, 11, 9)


def get_claude_config_path() -> Path:
    """Return the Claude Desktop config path for the current OS."""
    system = platform.system()
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        root = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return root / "Claude" / "claude_desktop_config.json"
    if system == "Darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    if system == "Linux":
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    raise RuntimeError(f"Unsupported OS: {system}")


def check_python() -> bool:
    """Verify Python >= 3.11.9."""
    current = sys.version_info[:3]
    if current < REQUIRED_PYTHON:
        required = ".".join(str(part) for part in REQUIRED_PYTHON)
        found = ".".join(str(part) for part in current)
        print(f"  Python {required}+ required (found {found})")
        return False
    print(f"  Python {'.'.join(str(part) for part in current)}")
    return True


def check_uv() -> bool:
    """Check if uv is available."""
    if shutil.which("uv"):
        print("  uv is available")
        return True
    print("  uv not found - will use pip")
    return False


def install_deps(has_uv: bool) -> bool:
    """Install project dependencies, trying uv first when available."""
    print("\nInstalling dependencies...")
    commands = []
    if has_uv:
        commands.append(["uv", "pip", "install", "-e", str(PROJECT_ROOT)])
    commands.append([sys.executable, "-m", "pip", "install", "-e", str(PROJECT_ROOT)])

    for command in commands:
        try:
            subprocess.run(command, check=True)
        except FileNotFoundError:
            continue
        except subprocess.CalledProcessError:
            print(f"  Command failed: {' '.join(command)}")
            continue
        print("  Dependencies installed")
        return True

    print("  Could not install dependencies.")
    print("  Hint: create and activate a virtual environment first:")
    print("    python -m venv .venv")
    print("    .venv\\Scripts\\activate   (Windows)")
    print("    source .venv/bin/activate  (macOS/Linux)")
    return False


def verify_server() -> bool:
    """Import the server module to verify it loads."""
    print("\nVerifying server...")
    code = (
        "try:\n"
        "    from mcp.server.fastmcp import FastMCP\n"
        "except ModuleNotFoundError:\n"
        "    from mcp.server.mcpserver import MCPServer as FastMCP\n"
        "from mcp_latex_server.server import mcp\n"
        "print(mcp.name)\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        print("  Server verification timed out")
        return False

    if result.returncode != 0:
        print(f"  Server verification failed:\n{result.stderr}")
        if "No module named 'mcp'" in result.stderr:
            print("  Hint: install the MCP SDK:")
            print("    pip install 'mcp[cli]>=1.9.4'")
        return False
    print(f"  Server '{result.stdout.strip()}' loads OK")
    return True


def _client_command(has_uv: bool) -> tuple[str, list[str]]:
    """Return the most reliable command for an MCP client config."""
    if has_uv and shutil.which("uv"):
        return "uv", ["--directory", str(PROJECT_ROOT), "run", "mcp-latex-server"]
    return sys.executable, ["-m", "mcp_latex_server"]


def configure_claude(has_uv: bool) -> bool:
    """Add the latex-server entry to Claude Desktop config."""
    print("\nConfiguring Claude Desktop...")
    try:
        config_path = get_claude_config_path()
    except RuntimeError as exc:
        print(f"  Skipped: {exc}")
        return False

    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists() and not config_path.is_file():
        print(f"  Config path is not a file: {config_path}")
        return False

    config: dict = {}
    if config_path.exists():
        raw = config_path.read_text(encoding="utf-8")
        if raw.strip():
            try:
                loaded = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(f"  Could not parse {config_path}: {exc}")
                print("  Leaving the existing file unchanged.")
                return False
            if not isinstance(loaded, dict):
                print(f"  Unexpected config format in {config_path}; leaving it unchanged.")
                return False
            config = loaded
        backup = config_path.with_name(config_path.name + ".bak")
        shutil.copy2(config_path, backup)
        print(f"  Backup written to {backup}")

    if not isinstance(config.get("mcpServers"), dict):
        config["mcpServers"] = {}

    command, args = _client_command(has_uv)
    config["mcpServers"]["latex-server"] = {
        "command": command,
        "args": args,
        "env": {"LATEX_SERVER_BASE_PATH": str(PROJECT_ROOT)},
    }

    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"  Config written to {config_path}")
    print("  Restart Claude Desktop for changes to take effect")
    return True


def main() -> int:
    print("MCP LaTeX Server - Setup")
    print("=" * 40)

    print("\nChecking requirements...")
    if not check_python():
        return 1
    has_uv = check_uv()

    if not install_deps(has_uv):
        return 1

    if not verify_server():
        return 1

    configure_claude(has_uv)

    print("\n" + "=" * 40)
    print("Setup complete!")
    print("\nNext steps:")
    print("  1. Restart Claude Desktop (if using it)")
    print("  2. Or add the config from examples/mcp_config.json to your MCP client")
    print("  3. Set LATEX_SERVER_BASE_PATH to your LaTeX working directory")
    print("  4. Test: uv run mcp-latex-server")
    print("     or inspect with: uv run mcp dev latex_server.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
