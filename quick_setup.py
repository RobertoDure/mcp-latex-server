#!/usr/bin/env python3
"""
Quick setup script for MCP LaTeX Server.
Installs dependencies, verifies the server, and configures Claude Desktop.
"""

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).parent.resolve()
SERVER_FILE = SERVER_DIR / "latex_server.py"


def get_claude_config_path() -> Path:
    """Return the Claude Desktop config path for the current OS."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json"
    elif system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif system == "Linux":
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    raise RuntimeError(f"Unsupported OS: {system}")


def check_python():
    """Verify Python >= 3.11.9."""
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 11.9):
        print(f"  Python 3.11.9+ required (found {major}.{minor})")
        return False
    print(f"  Python {major}.{minor}")
    return True


def check_uv() -> bool:
    """Check if uv is available."""
    try:
        subprocess.run(["uv", "--version"], capture_output=True, check=True)
        print("  uv is available")
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("  uv not found — will use pip")
        return False


def install_deps(has_uv: bool):
    """Install project dependencies."""
    print("\nInstalling dependencies...")
    if has_uv:
        subprocess.run(["uv", "pip", "install", "-e", str(SERVER_DIR)], check=True)
    else:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(SERVER_DIR)],
            check=True,
        )
    print("  Dependencies installed")


def verify_server():
    """Import the server module to verify it loads."""
    print("\nVerifying server...")
    result = subprocess.run(
        [sys.executable, "-c", "from latex_server import mcp; print(mcp.name)"],
        capture_output=True,
        text=True,
        cwd=str(SERVER_DIR),
        timeout=15,
    )
    if result.returncode != 0:
        print(f"  Server verification failed:\n{result.stderr}")
        return False
    print(f"  Server '{result.stdout.strip()}' loads OK")
    return True


def configure_claude():
    """Add the latex-server entry to Claude Desktop config."""
    print("\nConfiguring Claude Desktop...")
    try:
        config_path = get_claude_config_path()
    except RuntimeError as e:
        print(f"  Skipped: {e}")
        return

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config: dict = {}
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))

    config.setdefault("mcpServers", {})
    config["mcpServers"]["latex-server"] = {
        "command": "uv",
        "args": ["--directory", str(SERVER_DIR), "run", "latex_server.py"],
        "env": {"LATEX_SERVER_BASE_PATH": str(SERVER_DIR)},
    }

    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"  Config written to {config_path}")
    print("  Restart Claude Desktop for changes to take effect")


def main():
    print("MCP LaTeX Server — Setup")
    print("=" * 40)

    print("\nChecking requirements...")
    if not check_python():
        sys.exit(1)
    has_uv = check_uv()

    install_deps(has_uv)

    if not verify_server():
        sys.exit(1)

    configure_claude()

    print("\n" + "=" * 40)
    print("Setup complete!")
    print("\nNext steps:")
    print("  1. Restart Claude Desktop (if using it)")
    print("  2. Or add the config from mcp_config.json to your MCP client")
    print("  3. Set LATEX_SERVER_BASE_PATH env var to your LaTeX working directory")
    print("  4. Test: uv run mcp dev latex_server.py")


if __name__ == "__main__":
    main()
