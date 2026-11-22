# MCP LaTeX Server

A Model Context Protocol (MCP) server for LaTeX authoring: create and edit documents, analyze multi-file projects, validate structure and references, run quality checks, inspect PDFs, and compile to PDF. Built with FastMCP/MCPServer and Pydantic for structured tool output.

## Highlights

- **Author** article, report, book, letter, beamer, and minimal documents.
- **Transact** multiple edits atomically, snapshot and restore files, and edit named sections.
- **Build** custom templates with YAML metadata and double-brace variables.
- **Analyze** multi-file projects: includes, labels, references, bibliographies, and duplicate labels.
- **Compile** single files or whole projects with bibtex/biber sequencing, conditional reruns, timeouts, and optional latexmk.
- **Check** braces, environments, references, word counts, TODOs, chktex, latexindent, and spelling.
- **Inspect** PDF metadata, page counts, text, and optional page renders.
- **Support** MCP Python SDK 1.x and 2.x.

## Requirements

- **Python 3.11.9+**
- **MCP Python SDK** 1.x or 2.x: mcp[cli]>=1.9.4
- **LaTeX distribution** for compilation only:
  - Windows: MiKTeX or TeX Live
  - macOS: MacTeX
  - Linux: texlive-full or texlive-scheme-full
- **Optional external tools**, detected at runtime:
  - chktex, latexindent, hunspell or aspell
  - latexmk, bibtex, biber
  - poppler utilities (pdftoppm) for PDF page rendering
- Python dependencies: pydantic, pyyaml, pypdf.

Editing, reading, listing, validation, project analysis, word counts, and PDF reading do not require LaTeX to be installed. Tools that need an external binary report available=false or a clear install hint instead of failing the server.

## Installation

### Using uv

```bash
git clone https://github.com/RobertoDure/mcp-latex-server
cd mcp-latex-server
uv venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"
```

### Using pip

```bash
git clone https://github.com/RobertoDure/mcp-latex-server
cd mcp-latex-server
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
pip install -e ".[dev]"
```

### Quick setup

```bash
python scripts/quick_setup.py
```

The setup script checks Python, installs dependencies, verifies the server, and optionally writes a Claude Desktop configuration. Existing configs are backed up and malformed JSON is left untouched.

### Entry points

After installation, launch the server with any of:

```bash
mcp-latex-server
python -m mcp_latex_server
python latex_server.py    # compatibility shim
```

The canonical packaged entry point is **mcp-latex-server**. The root
**latex_server.py** stays as a compatibility shim for existing MCP client
configurations.

### Docker

A container image is included for the server runtime. It does not include a TeX
distribution; extend the image or mount a TeX installation if you need
compilation inside the container.

```bash
docker build -t mcp-latex-server .
docker run --rm -i \
  -e LATEX_SERVER_BASE_PATH=/workspace \
  -v "$PWD/documents:/workspace" \
  mcp-latex-server
```

The container communicates over stdio, so keep the interactive stdin flag
(`-i`) when connecting it to an MCP client.

## Configuration

| Variable | Description | Default |
|---|---|---|
| `LATEX_SERVER_BASE_PATH` | Root directory for all file operations | . (current directory) |
| `LATEX_SERVER_MAX_READ_BYTES` | Maximum file size a read tool may load | 5000000 |
| `LATEX_SERVER_MAX_PDF_BYTES` | Maximum PDF size returned by `read_pdf_file` | 50000000 |
| `LATEX_SERVER_LOG_LEVEL` | Logging level used by the entry point | INFO |
| `LATEX_SERVER_TEMPLATE_DIRS` | os.pathsep-separated custom template directories | empty |
| `LATEX_SERVER_SNAPSHOT_DIR` | Snapshot directory inside the base path | .latex-mcp/snapshots |

All tool paths resolve relative to the base directory. Paths that escape it, including symlinks that resolve outside, are denied. A templates directory inside the base path is treated as a custom template directory automatically.

## MCP client configuration

### Claude Desktop

Config locations:

- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "latex-server": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/mcp-latex-server",
        "run",
        "mcp-latex-server"
      ],
      "env": {
        "LATEX_SERVER_BASE_PATH": "/path/to/your/latex/files"
      }
    }
  }
}
```

### VS Code (GitHub Copilot)

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "latex-server": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/mcp-latex-server",
        "run",
        "mcp-latex-server"
      ],
      "env": {
        "LATEX_SERVER_BASE_PATH": "${workspaceFolder}"
      }
    }
  }
}
```

## Core tools

### `create_latex_file`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | `str` | required | Path for the new .tex file |
| `document_type` | `article\|report\|book\|letter\|beamer\|minimal` | `article` | Document class |
| `title` | `str` | `""` | Title (recipient for letters) |
| `author` | `str` | `""` | Author (signature for letters) |
| `date` | `str` | `\today` | Date; empty string writes `\date{}` |
| `content` | `str` | `""` | Body content (raw LaTeX) |
| `packages` | `list[str]` | `[]` | Extra packages; comma-separated names accepted |
| `geometry` | `str` | `""` | Geometry options, e.g. `margin=1in` |
| `overwrite` | `bool` | `false` | Replace an existing file |

Generation is class-aware: beamer uses title frames, letter uses a `letter` environment, and minimal builds its own title block.

### `create_from_template`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | `str` | required | Path for the new .tex file |
| `template` | `str` | `article` | Bundled or custom template name |
| `variables` | `dict[str,str]\|null` | `null` | Values for `{{placeholder}}` variables |
| `overwrite` | `bool` | `false` | Replace an existing file |

Custom templates may use YAML front matter:

```latex
---
name: paper
description: Article scaffold
variables:
  title: Untitled
  author: Anonymous
required: [title]
---
\documentclass{article}
\title{{{title}}}
\author{{{author}}}
\begin{document}
{{content}}
\end{document}
```

Placeholders without defaults raise an error instead of silently producing broken LaTeX.

### `edit_latex_file`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | `str` | required | Path to the file |
| `operation` | `replace\|insert_before\|insert_after\|append\|prepend` | required | Edit operation |
| `new_text` | `str` | required | Replacement or inserted text |
| `search_text` | `str\|null` | `null` | Text to find |
| `line_number` | `int\|null` | `null` | 1-based line alternative |
| `replace_all` | `bool` | `false` | Replace every occurrence |

`replace` changes the first match by default. `append` inserts before `\end{document}` and `prepend` inserts after `\begin{document}`. Empty non-replace edits are no-ops.

### `read_latex_file` and `list_latex_files`

Read UTF-8 files within the base path. Listing supports `recursive` and `max_results` and returns `total` and `truncated` fields. Symlinks that escape the base are skipped.

### `validate_latex`

Checks declarations, braces, environment nesting, content after `\end{document}`, and references. Validation ignores comments and verbatim-like content, including `verbatim`, `Verbatim`, `lstlisting`, `minted`, `comment`, `filecontents`, `\verb`, and `\lstinline`. Set `check_references=false` for labels that live in other files.

### `get_latex_structure`

Returns document class, title, author, date, packages, and headings in document order. Starred headings, nested title braces, and comma-separated package lists are supported.

### `compile_latex`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | `str` | required | Path to the file |
| `engine` | `pdflatex\|xelatex\|lualatex` | `pdflatex` | LaTeX engine |
| `timeout_seconds` | `int` | `120` | Per-pass timeout, 1-600 |
| `clean_aux` | `bool` | `false` | Remove auxiliary files |
| `shell_escape` | `bool` | `false` | Enable `\write18` for trusted documents |

A second pass runs only when the first pass produced auxiliary files. Errors are parsed from the `.log` with line numbers; warnings are returned separately. Timeouts kill the compiler process group on POSIX.

## Project tools

### `analyze_project`

Analyzes a main file and recursively resolves `\input`, `\include`, `\subfile`, `\import`, `\subimport`, `\inputfrom`, and `\includefrom`. Returns:

- project files and include relationships,
- missing include targets,
- labels and references with file:line locations,
- undefined references and duplicate labels,
- bibliography files and whether `bibtex` or `biber` is needed,
- validation issues for the main and included files.

### `validate_project`

Validates the whole project. Set `check_references=false` to ignore unresolved references when labels are supplied by external sources. Missing includes are errors; missing bibliography files and some project problems are warnings.

### `compile_project`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | `str` | required | Main .tex file |
| `engine` | `pdflatex\|xelatex\|lualatex` | `pdflatex` | Engine |
| `timeout_seconds` | `int` | `120` | Timeout per compiler or bibliography step |
| `clean_aux` | `bool` | `false` | Remove auxiliary files |
| `shell_escape` | `bool` | `false` | Enable shell escape for trusted documents |
| `run_bibliography` | `bool` | `true` | Run bibtex/biber and recompile |
| `use_latexmk` | `bool` | `false` | Use latexmk when available |

If a bibliography is detected, the server compiles, runs the appropriate bibliography tool, and recompiles. If latexmk is requested but unavailable, it falls back to direct engine passes and reports a warning.

## Authoring tools

### `apply_edits`

Applies an ordered list of edit specifications transactionally. All edits are applied in memory first; the file is written only if every edit succeeds. `snapshot` defaults to true, and `validate_after` can reject a batch that produces invalid LaTeX.

Each edit has `operation`, `new_text`, optional `search_text`/`line_number`, and `replace_all`.

### `edit_section`

Edits a named section body while preserving its heading. Operations: `replace`, `append`, `prepend`, `delete`. Section bodies include subsections. Use `section_level` to disambiguate duplicate titles.

### Snapshots

- `create_snapshot` stores a content snapshot with an optional label.
- `list_snapshots` lists snapshots for a file, newest first.
- `restore_snapshot` restores a snapshot and, by default, snapshots the current content first.

Snapshots are stored under `.latex-mcp/snapshots` inside the base path (or `LATEX_SERVER_SNAPSHOT_DIR`). Snapshot files use `.tex.snap` so they are not listed as documents.

### Builders

- `insert_table` inserts a table environment with headers and rows.
- `insert_figure` inserts a figure with `\includegraphics`.
- `insert_citation` inserts `\cite`, `\citep`, `\citet`, `\parencite`, `\textcite`, or `\autocite`.

Builders accept an optional `section` and `section_level` and can snapshot before writing.

## Quality and PDF tools

| Tool | Purpose |
|---|---|
| `count_words` | Visible word, line, character, and section counts |
| `find_todos` | TODO, FIXME, XXX, HACK, and NOTE markers with line numbers |
| `lint_latex` | chktex plus built-in validation issues |
| `format_latex` | latexindent output, optionally written back with a snapshot |
| `spell_check` | hunspell or aspell misspelling list |
| `analyze_log` | Errors, warnings, overfull and underfull box reports |
| `get_pdf_info` | Page count, metadata, size, and first-page preview |
| `extract_pdf_text` | Text extraction for a page range |
| `render_pdf_page` | PNG render of one page via pdftoppm |
| `read_pdf_file` | Return a PDF directly as an MCP embedded `application/pdf` resource |

External-tool results include `available` and a message explaining how to enable the feature. `read_pdf_file` returns the PDF bytes in an embedded resource, so a client can save or display the document without reading a path.

## Resources

| URI | Description |
|---|---|
| `latex://templates` | List bundled and custom templates with origin and description |
| `latex://template/{name}` | Get a template body without rendering placeholders |

The `list_template_details` tool returns structured template metadata, variables, defaults, origins, and discovery errors.

## Safety notes

- Every path is resolved and checked against `LATEX_SERVER_BASE_PATH`.
- Creation and template copies refuse to overwrite by default.
- Writes use a temporary file plus atomic replace.
- Transactions are all-or-nothing.
- Compilation disables shell escape unless explicitly requested.
- Compiler and helper processes have timeouts and process-group cleanup on POSIX.
- Snapshot and template names are validated; custom template directories can be configured but are read-only inputs.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for module responsibilities,
request flow, trust boundaries, and extension points.

## Development

Install the editable development environment:

```bash
uv pip install -e ".[dev]"
```

Run the standard checks:

```bash
ruff check src tests scripts examples latex_server.py
python -m pytest
```

The package follows a src layout. The importable namespace is
**mcp_latex_server**, tests live under **tests**, maintenance scripts under
**scripts**, and bundled template data under
**src/mcp_latex_server/resources/templates**.

## Testing

```bash
pip install -e ".[dev]"
python -m pytest -q
```

The suite covers all document classes, validation edge cases, path containment, project graphs, bibliography sequencing, transactions, snapshots, section editing, builders, optional quality tools, PDF metadata/text, and fake-engine compilation. Compilation tests are skipped on Windows because the fake engines are POSIX shell scripts. CI runs the suite against MCP 1.x and 2.x on Python 3.11-3.13.

## Troubleshooting

**Server won't start** - Verify Python 3.11.9+ and the MCP SDK:

```bash
python -c "import mcp; print(mcp.__file__)"
```

**MCP 1.x or 2.x** - Both are supported. If an older environment fails on MCP internals, upgrade the SDK and pydantic together.

**Compilation fails** - Ensure an engine is on PATH (`pdflatex --version`). Check `analyze_log` for line-numbered errors.

**Compilation timed out** - Increase `timeout_seconds` or simplify the document.

**chktex/latexindent/spell checking unavailable** - Install the corresponding binary. The tool reports `available=false` rather than failing.

**PDF rendering unavailable** - Install poppler-utils for `pdftoppm`. PDF metadata and text extraction use pypdf and do not require poppler.

**Access denied** - The requested path resolves outside `LATEX_SERVER_BASE_PATH`. Use relative paths or adjust the environment variable.

**File already exists** - Pass `overwrite=true` or choose another path.

## Project layout

```text
pyproject.toml                  Packaging, dependencies, tools, and test settings
src/mcp_latex_server/
  __init__.py                   Package metadata
  __main__.py                   python -m mcp_latex_server
  server.py                     MCP tools, resources, models, and entry point
  templates.py                  Template discovery, metadata, and rendering
  project.py                    Multi-file graph, validation, and compilation
  authoring.py                  Transactions, snapshots, sections, and builders
  quality.py                    Quality, logs, spelling, and PDF helpers
  resources/templates/          Bundled article, beamer, and report templates
tests/
  conftest.py                   Shared pytest fixtures
  test_core.py                  Core test suite
  test_features.py              Expanded feature test suite
examples/                       Client demo and example MCP configuration
scripts/quick_setup.py          Cross-platform setup helper
docs/reports/                   Generated LaTeX report and PDF
latex_server.py                 Backward-compatible entry-point shim
.github/workflows/              CI matrix for MCP 1.x/2.x and Python 3.11-3.13
CHANGELOG.md                    Release notes
CONTRIBUTING.md                 Development workflow
SECURITY.md                     Vulnerability reporting
CODE_OF_CONDUCT.md              Community expectations
Dockerfile                      Container build without a TeX distribution
LICENSE                         MIT license
```

## License

[MIT](LICENSE)
