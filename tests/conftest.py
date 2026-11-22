"""Shared pytest fixtures for the MCP LaTeX Server test suite."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from mcp_latex_server import server

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point the server at an isolated temporary base directory."""
    base = tmp_path / "workspace"
    base.mkdir()
    monkeypatch.setattr(server, "BASE_PATH", base.resolve())
    return base


@pytest.fixture
def tool_bin(monkeypatch):
    """Create an executable directory for fake external tools."""
    root = Path(tempfile.mkdtemp(prefix=".tmp_engines-", dir=str(PROJECT_ROOT)))
    monkeypatch.setenv("PATH", str(root) + os.pathsep + os.environ.get("PATH", ""))
    yield root
    shutil.rmtree(root, ignore_errors=True)
