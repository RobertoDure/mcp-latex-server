# Contributing

## Development setup

~~~bash
git clone https://github.com/RobertoDure/mcp-latex-server
cd mcp-latex-server
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
~~~

## Layout

- src/mcp_latex_server: importable package and MCP server
- tests: pytest suite and shared fixtures
- examples: client demo and example MCP configuration
- scripts: maintenance helpers such as quick_setup.py
- docs/reports: generated report artifacts (ignored by Git)

## Checks

Run these before opening a pull request:

~~~bash
ruff check src tests scripts examples latex_server.py
python -m pytest
~~~

Compilation tests use fake engines so they do not require a TeX installation.
Optional external tools such as chktex, latexindent, hunspell, and pdftoppm are
tested both through fake binaries and through their unavailable paths.

## Code conventions

- Python 3.11+ syntax, four-space indentation, and a 100-character line limit.
- Keep all filesystem access inside the path-security helpers.
- Keep MCP tool functions small; put reusable logic in the package modules.
- Add or update tests for every behavior change.
- Preserve the public tool names and result models unless a breaking change is
  explicitly documented in CHANGELOG.md.

## Pull requests

Describe the behavior change, list the checks you ran, and call out any new
external tool or dependency. Do not commit generated build directories,
virtual environments, cache directories, generated reports, or test temporary
files.
