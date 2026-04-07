# MCP LaTeX Server

A Model Context Protocol (MCP) server for LaTeX file creation, editing, validation, and compilation. Built with [FastMCP](https://github.com/modelcontextprotocol/python-sdk) and Pydantic for type-safe, structured output.

## Features

- **Create** LaTeX documents from parameters or bundled templates (article, beamer, report)
- **Edit** files with replace, insert, append, and prepend operations
- **Read** and **list** `.tex` files within a secure base directory
- **Validate** syntax — braces, environments, references, required declarations
- **Compile** to PDF with `pdflatex`, `xelatex`, or `lualatex`
- **Resources** — browse and retrieve bundled templates via `latex://` URIs

## Prerequisites

- **Python 3.10+**
- **LaTeX distribution** (for compilation):
  - Windows: [MiKTeX](https://miktex.org/download) or [TeX Live](https://www.tug.org/texlive/)
  - macOS: [MacTeX](https://www.tug.org/mactex/) (`brew install --cask mactex`)
  - Linux: `sudo apt install texlive-full` (Debian/Ubuntu) or `sudo dnf install texlive-scheme-full` (Fedora)

## Installation

### Using uv (recommended)

```bash
git clone https://github.com/RobertoDure/mcp-latex-server
cd mcp-latex-server
uv pip install -e .
```

### Using pip

```bash
git clone <repository-url>
cd mcp-latex-server
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
pip install -e .
```

### Quick setup (Windows)

```bash
python quick_setup.py
```

This checks Python version, installs dependencies, verifies the server imports correctly, and optionally configures Claude Desktop.

## Configuration

The server uses a single environment variable:

| Variable | Description | Default |
|---|---|---|
| `LATEX_SERVER_BASE_PATH` | Root directory for all file operations | `.` (current directory) |

All file paths passed to tools are resolved relative to this base directory. Access outside it is denied.

## MCP client configuration

### Claude Desktop

Add to your `claude_desktop_config.json`:

- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "latex-server": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/mcp-latex-server",
        "run",
        "latex_server.py"
      ],
      "env": {
        "LATEX_SERVER_BASE_PATH": "/path/to/your/latex/files"
      }
    }
  }
}
```

### VS Code (GitHub Copilot)

Add to `.vscode/mcp.json` in your workspace:

```json
{
  "servers": {
    "latex-server": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/mcp-latex-server",
        "run",
        "latex_server.py"
      ],
      "env": {
        "LATEX_SERVER_BASE_PATH": "${workspaceFolder}"
      }
    }
  }
}
```

## Tools

### `create_latex_file`

Create a new LaTeX document from parameters.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | `str` | *required* | Path for the new `.tex` file |
| `document_type` | `article\|report\|book\|letter\|beamer\|minimal` | `article` | Document class |
| `title` | `str` | `""` | Document title |
| `author` | `str` | `""` | Document author |
| `date` | `str` | `\today` | Document date |
| `content` | `str` | `""` | Body content |
| `packages` | `list[str]` | `[]` | Extra LaTeX packages |
| `geometry` | `str` | `""` | Geometry settings (e.g. `margin=1in`) |

### `create_from_template`

Create a document from a bundled template.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | `str` | *required* | Path for the new `.tex` file |
| `template` | `article\|beamer\|report` | `article` | Template name |

### `edit_latex_file`

Edit an existing LaTeX file.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | `str` | *required* | Path to the file |
| `operation` | `replace\|insert_before\|insert_after\|append\|prepend` | *required* | Edit operation |
| `new_text` | `str` | *required* | Text to insert or replace with |
| `search_text` | `str\|null` | `null` | Text to find (required for replace/insert) |
| `line_number` | `int\|null` | `null` | 1-based line number (alternative to `search_text`) |

### `read_latex_file`

Read and return the contents of a `.tex` file.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | `str` | *required* | Path to the file |

### `list_latex_files`

List all `.tex` files in a directory.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `directory_path` | `str` | `.` | Directory to search |
| `recursive` | `bool` | `false` | Search subdirectories |

### `validate_latex`

Check LaTeX syntax: required declarations, balanced braces, environment matching, undefined references.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | `str` | *required* | Path to the file |

### `get_latex_structure`

Extract document structure: class, title, author, packages, and section hierarchy.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | `str` | *required* | Path to the file |

### `compile_latex`

Compile a `.tex` file to PDF (runs the engine twice for references/TOC).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | `str` | *required* | Path to the file |
| `engine` | `pdflatex\|xelatex\|lualatex` | `pdflatex` | LaTeX engine |

## Resources

| URI | Description |
|---|---|
| `latex://templates` | List available bundled templates |
| `latex://template/{name}` | Get the content of a specific template |

## Testing

Test with the MCP Inspector:

```bash
uv run mcp dev latex_server.py
```

Or run the included test suite:

```bash
python test_server.py
```

## Troubleshooting

**Server won't start** — Verify Python 3.10+ (`python --version`) and that `mcp` is installed (`pip list | grep mcp`).

**Compilation fails** — Ensure a LaTeX distribution is on your PATH (`pdflatex --version`). Install MiKTeX or TeX Live if missing.

**"Access denied" errors** — The requested file path resolves outside `LATEX_SERVER_BASE_PATH`. Use relative paths or adjust the env variable.

**Claude can't connect** — Double-check file paths in your MCP config, restart Claude Desktop, and verify with `uv run mcp dev latex_server.py`.

## License

MIT
