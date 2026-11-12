# Changelog

## Unreleased

### Changed

- Reorganized the project into a standard src layout with the
  mcp_latex_server package.
- Moved tests to tests/, quick_setup to scripts/, the example MCP config to
  examples/, and generated report artifacts to docs/reports/.
- Added package entry points for mcp-latex-server and python -m mcp_latex_server.
- Added LICENSE, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, Dockerfile, and
  .editorconfig.
- Switched CI linting to ruff, added wheel builds, and made pytest fixtures
  shared through tests/conftest.py.
- Removed requirements.txt in favor of pyproject.toml dependencies.

## 3.0.0

### Added

- Multi-file project analysis and validation: recursive include resolution,
  cross-file labels/references, duplicate-label detection, missing include
  reporting, and bibliography discovery.
- Project compilation with bibtex/biber sequencing and optional latexmk builds.
- Custom template discovery through LATEX_SERVER_TEMPLATE_DIRS and a base-path
  templates directory, with YAML front matter, variable substitution, defaults,
  and required variables.
- Transactional edit batches with atomic rollback, snapshots, restore, and
  section-aware editing.
- Table, figure, and citation builders.
- Word count, TODO/FIXME scanning, chktex and latexindent integration,
  hunspell/aspell spelling, and compile-log analysis.
- PDF metadata, page-count, text extraction, and optional pdftoppm page rendering.
- read_pdf_file returns PDF bytes directly as an MCP embedded application/pdf
  resource.
- MCP 2.x support while retaining MCP 1.x compatibility.
- CI matrix, coverage configuration, pre-commit hook, examples, and changelog.

### Fixed

- MCP dependency range no longer excludes the current SDK major version.
- Python 3.11.x version check in quick_setup.
- FieldInfo defaults passed to direct tool calls.
- Validation false positives for verbatim-like environments and escaped percent
  signs.
- Content-after-end-document detection.
- Section extraction order, starred sections, nested titles, and package lists.
- Silent overwrites on file creation.
- Replace-all and insert-all editing semantics.
- Append/prepend edits escaping the document environment.
- Compiler hangs, stale logs, orphaned processes, and missing error/warning detail.
- Template and symlink path containment gaps.
- Missing build system and template data in packaging.

## 2.1.0

### Fixed

- FastMCP/Pydantic type defaults, validation, compilation, packaging, and setup
  issues corrected as part of the initial hardening pass.
