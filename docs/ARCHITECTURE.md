# Architecture

## Package layout

~~~text
src/mcp_latex_server/
  server.py       MCP registration, path security, core tools, resources, main()
  templates.py    Template discovery, YAML metadata, variable rendering
  project.py      Multi-file project graph, validation, and compilation
  authoring.py    Transactions, snapshots, section edits, builders
  quality.py      Word counts, logs, external tools, spelling, PDF helpers
  resources/      Bundled template data
~~~

The package is installed with a src layout. The console script
**mcp-latex-server** points at **mcp_latex_server.server:main**. The compatibility
file **latex_server.py** at the repository root imports the package so older
client configurations keep working.

## Request flow

1. The MCP SDK discovers tools and resources registered by **server.py**.
2. Every filesystem tool resolves its path through the shared path-security
   helper and the configured **LATEX_SERVER_BASE_PATH**.
3. Core text tools use the shared analysis scanner, which masks comments and
   verbatim content while preserving offsets and line numbers.
4. Project tools resolve includes recursively and aggregate labels, references,
   bibliography resources, and validation issues.
5. Compilation tools run engines or latexmk as subprocesses, apply timeouts,
   parse logs, and optionally clean auxiliary files.
6. PDF tools read metadata and text with pypdf and optionally render pages with
   pdftoppm. **read_pdf_file** returns PDF bytes as an MCP embedded resource.

## Trust boundaries

- The configured base directory is the filesystem trust boundary.
- Custom template directories are trusted configuration inputs.
- Shell escape is disabled for compilation unless explicitly enabled.
- External quality tools are executed as separate processes and are not
  sandboxed by this package.
- Snapshots and transactions write through the same path-security helpers and
  atomic-write helper as ordinary edits.

## Extension points

- Add a tool by registering it in **server.py** and delegating reusable logic to
  the appropriate package module.
- Add a template by placing a .tex file in
  **src/mcp_latex_server/resources/templates** or a configured custom directory.
- Add a project-aware check in **project.py** and return a ProjectIssue.
- Add an optional external-tool integration in **quality.py**, reporting
  available=False when the binary is absent.

## Quality gates

- ruff linting across src, tests, scripts, examples, and the compatibility shim
- pytest suite under tests/
- coverage configuration for the mcp_latex_server package
- wheel build in CI
- dual MCP SDK matrix: MCP 1.x and MCP 2.x
