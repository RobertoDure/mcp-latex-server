#!/usr/bin/env python3
"""
MCP LaTeX Server - A Model Context Protocol server for LaTeX file creation,
editing, validation, and compilation.

Uses FastMCP for automatic schema generation from type hints.
"""

import asyncio
import base64
import logging
import os
import re
import shutil
import signal
import site
import stat
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

try:  # MCP 1.x
    from mcp.server.fastmcp import Context, FastMCP
except ModuleNotFoundError:  # pragma: no cover - exercised with MCP 2.x
    # MCP 2.x renamed FastMCP to MCPServer; the decorator and context APIs used
    # by this server are otherwise compatible.
    from mcp.server.mcpserver import Context, MCPServer as FastMCP

from mcp.types import BlobResourceContents, EmbeddedResource
from pydantic import BaseModel, Field

from . import authoring as latex_authoring
from . import project as latex_project
from . import quality as latex_quality
from . import templates as latex_templates
from .templates import TemplateListResult

logger = logging.getLogger(__name__)


# --- Configuration ---


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """Read an integer environment variable, falling back to default."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %d", name, raw, default)
        return default
    if value < minimum:
        logger.warning("Ignoring out-of-range %s=%r; using %d", name, raw, default)
        return default
    return value


def _find_templates_dir() -> Path:
    """Locate the bundled templates for source, wheel, and user installs."""
    module_dir = Path(__file__).resolve().parent
    bases = [Path(sys.prefix), Path(sys.base_prefix)]
    user_base = getattr(site, "USER_BASE", None)
    if user_base:
        bases.append(Path(user_base))

    candidates = [
        module_dir / "resources" / "templates",
        module_dir / "templates",
    ]
    seen: set[Path] = set()
    for base in bases:
        if base in seen:
            continue
        seen.add(base)
        candidates.append(base / "share" / "mcp-latex-server" / "templates")
    candidates.append(Path.cwd() / "templates")

    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    return (module_dir / "resources" / "templates").resolve()


BASE_PATH = Path(os.environ.get("LATEX_SERVER_BASE_PATH") or ".").expanduser().resolve()
TEMPLATES_DIR = _find_templates_dir()

#: Maximum size of a file that may be read into memory by a tool.
MAX_READ_BYTES = _env_int("LATEX_SERVER_MAX_READ_BYTES", 5_000_000)
#: Maximum amount of compiler log text returned in a tool response.
MAX_LOG_OUTPUT_CHARS = 4_000
#: Maximum PDF size returned by read_pdf_file.
MAX_PDF_BYTES = _env_int("LATEX_SERVER_MAX_PDF_BYTES", 50_000_000)

DOCUMENT_TYPES = ("article", "report", "book", "letter", "beamer", "minimal")

#: Environments whose contents must not participate in brace/environment checks.
VERBATIM_ENVIRONMENTS = (
    "verbatim*",
    "verbatim",
    "Verbatim",
    "lstlisting",
    "minted",
    "comment",
    "filecontents*",
    "filecontents",
)

#: Files that indicate a second LaTeX pass may be useful.
SECOND_PASS_SUFFIXES = (
    ".aux",
    ".toc",
    ".lof",
    ".lot",
    ".out",
    ".nav",
    ".snm",
    ".bbl",
    ".bcf",
    ".run.xml",
)

#: Auxiliary files removed by compile_latex(clean_aux=True).
AUX_SUFFIXES = (
    ".aux",
    ".log",
    ".out",
    ".toc",
    ".lof",
    ".lot",
    ".nav",
    ".snm",
    ".fls",
    ".fdb_latexmk",
    ".synctex.gz",
    ".run.xml",
    ".bcf",
    ".blg",
)


# --- Structured Output Models ---


class FileResult(BaseModel):
    """Result of a file operation."""

    path: str = Field(description="Absolute path to the file")
    success: bool
    message: str
    content: str | None = Field(default=None, description="File content, when available")


class ValidationResult(BaseModel):
    """Result of LaTeX validation."""

    path: str
    valid: bool
    issues: list[str] = Field(default_factory=list)


class StructureInfo(BaseModel):
    """Extracted structure of a LaTeX document."""

    path: str
    document_class: str | None = None
    title: str | None = None
    author: str | None = None
    date: str | None = None
    packages: list[str] = Field(default_factory=list)
    sections: list[str] = Field(
        default_factory=list,
        description="Section headings in document order, formatted as 'Level: title'",
    )


class CompileResult(BaseModel):
    """Result of LaTeX compilation."""

    path: str
    success: bool
    pdf_path: str | None = None
    log_path: str | None = None
    log_output: str = ""
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    passes: int = 0
    duration_seconds: float | None = None


class FileInfo(BaseModel):
    """Information about a LaTeX file."""

    path: str
    size_bytes: int
    modified_at: str | None = None


class FileListResult(BaseModel):
    """Result of listing LaTeX files."""

    directory: str
    files: list[FileInfo] = Field(default_factory=list)
    total: int = 0
    truncated: bool = False


# --- Path Security ---


class PathSecurityError(ValueError):
    """Raised when a requested path escapes the configured base directory."""


def _is_within_base(path: Path) -> bool:
    """Return True when path is inside BASE_PATH."""
    try:
        path.relative_to(BASE_PATH)
    except ValueError:
        return False
    return True


def get_safe_path(file_path: str) -> Path:
    """Resolve a file path, ensuring it stays within BASE_PATH."""
    raw = (file_path or "").strip()
    if not raw:
        raise ValueError("file_path must not be empty")
    if "\x00" in raw:
        raise ValueError("file_path must not contain NUL bytes")

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = BASE_PATH / path
    resolved = path.resolve()
    if not _is_within_base(resolved):
        raise PathSecurityError(
            f"Access denied: '{file_path}' resolves outside the allowed directory ({BASE_PATH})"
        )
    return resolved


def _require_file(path: Path) -> None:
    """Raise a precise error when path is missing or not a regular file."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"Not a file: {path}")


def _require_tex_suffix(path: Path) -> None:
    """Reject output paths that are not .tex files."""
    if path.suffix.lower() != ".tex":
        raise ValueError(
            f"Expected a .tex file, got '{path.name}'. "
            "LaTeX source files must use the .tex extension."
        )


def _ensure_writable_target(path: Path, overwrite: bool) -> None:
    """Refuse to clobber an existing file unless overwrite is set."""
    if not path.exists():
        return
    if path.is_dir():
        raise IsADirectoryError(f"Path is a directory, not a file: {path}")
    if not overwrite:
        raise FileExistsError(
            f"File already exists: {path}. Pass overwrite=True to replace it."
        )


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically so a failed write cannot truncate the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, mode)
        except OSError:  # pragma: no cover - not supported on every platform
            pass
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _read_text_file(path: Path) -> str:
    """Read a UTF-8 text file with a size guard and a helpful error."""
    _require_file(path)
    size = path.stat().st_size
    if size > MAX_READ_BYTES:
        raise ValueError(
            f"File is too large ({size:,} bytes); the configured limit is "
            f"{MAX_READ_BYTES:,} bytes. Set LATEX_SERVER_MAX_READ_BYTES to raise it."
        )
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"File is not valid UTF-8: {path}. Convert it to UTF-8 first."
        ) from exc


# --- Templates and Packages ---

_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _custom_template_dirs() -> list[Path]:
    """Return configured custom template directories for the current base path."""
    return latex_templates.custom_template_dirs(BASE_PATH)


def _normalize_packages(packages: list[str] | str | None) -> list[str]:
    """Validate, split on commas, and de-duplicate package names."""
    if packages is None:
        return []
    if isinstance(packages, str):
        packages = [packages]

    normalized: list[str] = []
    for raw in packages:
        for package in str(raw).split(","):
            package = package.strip()
            if not package:
                continue
            if not _PACKAGE_NAME_RE.fullmatch(package):
                raise ValueError(
                    f"Invalid LaTeX package name: {package!r}. "
                    "Package names may contain letters, digits, '.', '_' and '-'."
                )
            if package not in normalized:
                normalized.append(package)
    return normalized


def _validate_geometry(geometry: str) -> None:
    """Reject geometry options that could break out of the optional argument."""
    forbidden = set("[]{}\n\r%\\")
    bad = sorted({character for character in geometry if character in forbidden})
    if bad:
        raise ValueError(
            "Invalid geometry options; these characters are not allowed: "
            + ", ".join(repr(character) for character in bad)
        )


# --- Document Generation ---


def _ensure_newline(text: str) -> str:
    """Return text with exactly one trailing newline."""
    return text if text.endswith("\n") else text + "\n"


def _beamer_body(content: str) -> list[str]:
    """Wrap plain beamer content in a frame when it does not define its own."""
    body = content.strip("\n")
    if not body:
        return ["% Your content here"]
    if re.search(
        r"\\begin\{frame\}|\\frame\b|\\frametitle\b|\\section\b|\\subsection\b",
        body,
    ):
        return [body]
    return ["\\begin{frame}", body, "\\end{frame}"]


def _letter_body(content: str, recipient: str) -> list[str]:
    """Build a valid letter environment body."""
    body = content.strip("\n")
    if "\\begin{letter}" in body:
        return [body]
    lines = [
        f"\\begin{{letter}}{{{recipient}}}",
        "\\opening{Dear Recipient,}",
        "",
    ]
    lines.append(body or "% Your letter content here")
    lines.extend(["", "\\closing{Sincerely,}", "", "\\end{letter}"])
    return lines


def _minimal_body(title: str, author: str, date: str, content: str) -> list[str]:
    """Render a title block for the minimal class, which lacks \\maketitle."""
    lines: list[str] = []
    if title or author:
        lines.append("\\begin{center}")
        if title:
            lines.append(f"{{\\Large {title}}}\\\\[1em]")
        if author:
            lines.append(f"{author}\\\\")
        if date:
            lines.append(date)
        lines.extend(["\\end{center}", ""])
    lines.append(content.strip("\n") or "% Your content here")
    return lines


def create_latex_template(
    document_type: str,
    title: str,
    author: str,
    date: str,
    content: str,
    packages: list[str] | str | None,
    geometry: str,
) -> str:
    """Create a LaTeX document from parameters.

    The generated document is class-aware: beamer content is wrapped in a frame,
    letter content is wrapped in a letter environment, and the minimal class
    gets a hand-built title block because it does not define \\maketitle.
    """
    if document_type not in DOCUMENT_TYPES:
        raise ValueError(
            f"Unsupported document type: {document_type!r}. "
            f"Choose one of: {', '.join(DOCUMENT_TYPES)}"
        )

    title = title or ""
    author = author or ""
    date = date or ""
    content = content or ""
    packages = _normalize_packages(packages)
    geometry = (geometry or "").strip()
    _validate_geometry(geometry)

    lines: list[str] = [f"\\documentclass{{{document_type}}}"]

    if geometry:
        lines.append(f"\\usepackage[{geometry}]{{geometry}}")

    if document_type == "minimal":
        # babel is not needed for minimal documents and can add surprising output.
        lines.append("\\usepackage[utf8]{inputenc}")
        lines.append("\\usepackage[T1]{fontenc}")
    else:
        lines.append("\\usepackage[utf8]{inputenc}")
        lines.append("\\usepackage[T1]{fontenc}")
        lines.append("\\usepackage[english]{babel}")

    for package in packages:
        if package == "geometry" and geometry:
            continue  # already loaded with options above
        lines.append(f"\\usepackage{{{package}}}")

    if document_type == "beamer":
        if title:
            lines.append(f"\\title{{{title}}}")
        if author:
            lines.append(f"\\author{{{author}}}")
        # An empty date must be written explicitly to suppress the class default.
        lines.append(f"\\date{{{date}}}")
    elif document_type == "letter":
        if author:
            lines.append(f"\\signature{{{author}}}")
    elif document_type == "minimal":
        pass
    else:
        if title:
            lines.append(f"\\title{{{title}}}")
        if author:
            lines.append(f"\\author{{{author}}}")
        lines.append(f"\\date{{{date}}}")

    lines.extend(["", "\\begin{document}"])

    if document_type == "beamer":
        if title:
            lines.append("\\frame{\\titlepage}")
        lines.extend(_beamer_body(content))
    elif document_type == "letter":
        lines.extend(_letter_body(content, title.strip() or "Recipient"))
    elif document_type == "minimal":
        lines.extend(_minimal_body(title, author, date, content))
    else:
        if title:
            lines.append("\\maketitle")
        lines.extend(["", content.strip("\n") or "% Your content here"])

    lines.extend(["", "\\end{document}", ""])
    return "\n".join(lines)


# --- Analysis Helpers ---


_VERBATIM_BEGIN_RE = re.compile(
    r"\\begin\{(?P<env>"
    + "|".join(re.escape(name) for name in VERBATIM_ENVIRONMENTS)
    + r")\}"
)
_VERBATIM_END_RES = {
    name: re.compile(rf"\\end\{{{re.escape(name)}\}}")
    for name in VERBATIM_ENVIRONMENTS
}
_INLINE_VERB_RE = re.compile(r"\\verb\*?(?P<delimiter>[^A-Za-z\s])(?P<body>[^\n]*?)(?P=delimiter)")
_INLINE_LSTINLINE_RE = re.compile(
    r"\\lstinline(?:\[[^\]]*\])?(?P<delimiter>[^A-Za-z\s])(?P<body>[^\n]*?)(?P=delimiter)"
)


def _blank_span(result: list[str], content: str, start: int, end: int) -> None:
    """Replace a character range with spaces, preserving newlines."""
    for index in range(start, end):
        if content[index] != "\n":
            result[index] = " "


def _prepare_analysis(content: str) -> str:
    """Blank comments and verbatim content in one pass.

    Scanning in source order avoids the false negative caused by masking a
    commented-out \begin{verbatim} before comments are removed. Character
    offsets and newlines are preserved so callers can locate matches in the
    original content.
    """
    result = list(content)
    length = len(content)
    index = 0
    while index < length:
        character = content[index]

        if character == "%":
            end = index
            while end < length and content[end] not in "\r\n":
                result[end] = " "
                end += 1
            index = end
            continue

        if character == "\\":
            begin = _VERBATIM_BEGIN_RE.match(content, index)
            if begin is not None:
                environment = begin.group("env")
                end_match = _VERBATIM_END_RES[environment].search(
                    content, begin.end()
                )
                end = end_match.end() if end_match else length
                _blank_span(result, content, index, end)
                index = end
                continue

            inline_match = _INLINE_VERB_RE.match(content, index)
            if inline_match is None:
                inline_match = _INLINE_LSTINLINE_RE.match(content, index)
            if inline_match is not None:
                _blank_span(result, content, index, inline_match.end())
                index = inline_match.end()
                continue

            index += 2  # skip the escaped character
            continue

        index += 1

    return "".join(result)

def _line_number(text: str, position: int) -> int:
    """Return the 1-based line number for a character offset."""
    return text.count("\n", 0, position) + 1


def _count_braces(text: str) -> tuple[int, int]:
    """Count unescaped opening and closing braces."""
    opens = closes = 0
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if character == "{":
            opens += 1
        elif character == "}":
            closes += 1
        index += 1
    return opens, closes


_ENV_EVENT_RE = re.compile(r"\\(begin|end)\s*\{([^{}]+)\}")


def _check_environments(text: str, issues: list[str]) -> None:
    """Validate environment nesting with order-aware, line-numbered errors."""
    stack: list[tuple[str, int]] = []
    for match in _ENV_EVENT_RE.finditer(text):
        kind, name = match.group(1), match.group(2).strip()
        line = _line_number(text, match.start())
        if kind == "begin":
            stack.append((name, line))
            continue

        if not stack:
            issues.append(
                f"Line {line}: \\end{{{name}}} without matching \\begin{{{name}}}"
            )
        elif stack[-1][0] == name:
            stack.pop()
        elif name in {entry[0] for entry in stack}:
            while stack and stack[-1][0] != name:
                unclosed, opened_at = stack.pop()
                issues.append(
                    f"Line {line}: unclosed environment '{unclosed}' "
                    f"(opened on line {opened_at}) before \\end{{{name}}}"
                )
            if stack:
                stack.pop()
        else:
            issues.append(
                f"Line {line}: \\end{{{name}}} without matching \\begin{{{name}}}"
            )

    for name, line in stack:
        issues.append(f"Line {line}: unclosed environment '{name}'")


def _read_braced(text: str, start: int) -> tuple[str | None, int]:
    """Read a balanced brace group starting at start."""
    if start >= len(text) or text[start] != "{":
        return None, start

    depth = 0
    index = start
    while index < len(text):
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
        index += 1
    return None, start


def _find_command_arg(text: str, command: str) -> str | None:
    """Return the argument of the first command invocation."""
    pattern = re.compile(
        rf"\\{re.escape(command)}(?![A-Za-z])\*?\s*(?:\[[^\]]*\])?\s*\{{"
    )
    match = pattern.search(text)
    if not match:
        return None
    argument, _ = _read_braced(text, match.end() - 1)
    return argument


_SECTION_LEVELS = {
    "part": "Part",
    "chapter": "Chapter",
    "section": "Section",
    "subsection": "Subsection",
    "subsubsection": "Subsubsection",
    "paragraph": "Paragraph",
    "subparagraph": "Subparagraph",
}
_SECTION_RE = re.compile(
    r"\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph)"
    r"(?![A-Za-z])(\*)?\s*(?:\[[^\]]*\])?\s*\{"
)
_PACKAGE_RE = re.compile(
    r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}"
)
_DOCUMENTCLASS_RE = re.compile(
    r"\\documentclass(?![A-Za-z])\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}"
)
_BEGIN_DOCUMENT_RE = re.compile(r"\\begin\s*\{document\}")
_END_DOCUMENT_RE = re.compile(r"\\end\s*\{document\}")
_LABEL_RE = re.compile(r"\\label\s*\{([^{}]+)\}")
_REFERENCE_RE = re.compile(
    r"\\(?:ref|eqref|pageref|autoref|nameref|vref|cref|Cref)"
    r"(?![A-Za-z])\s*\{([^{}]+)\}"
)


# --- Server Setup ---

mcp = FastMCP(
    "latex-server",
    instructions=(
        "LaTeX document server. Use tools to create, edit, read, validate, "
        "inspect, and compile LaTeX files. All file paths must stay within the "
        "configured base directory. Destructive replacement and compilation with "
        "shell escape are opt-in. Prefer validate_latex before compile_latex."
    ),
)


# --- Tools ---


@mcp.tool()
async def create_latex_file(
    file_path: Annotated[str, Field(description="Path for the new .tex file (relative to base dir)")],
    document_type: Literal["article", "report", "book", "letter", "beamer", "minimal"] = "article",
    title: Annotated[str, Field(description="Document title")] = "",
    author: Annotated[str, Field(description="Document author")] = "",
    date: Annotated[str, Field(description="Document date; use an empty string to omit it")] = "\\today",
    content: Annotated[str, Field(description="Initial body content (raw LaTeX)")] = "",
    packages: Annotated[
        list[str],
        Field(description="Extra LaTeX packages; comma-separated names are also accepted"),
    ] = [],
    geometry: Annotated[
        str,
        Field(description="Geometry package options, e.g. 'margin=1in'"),
    ] = "",
    overwrite: Annotated[
        bool,
        Field(description="Replace the file if it already exists (default: refuse)"),
    ] = False,
) -> FileResult:
    """Create a new LaTeX document with specified content and structure."""
    path = get_safe_path(file_path)
    _require_tex_suffix(path)
    _ensure_writable_target(path, overwrite)

    latex_content = create_latex_template(
        document_type, title, author, date, content, packages, geometry
    )
    _atomic_write_text(path, latex_content)

    return FileResult(
        path=str(path), success=True, message="File created", content=latex_content
    )


@mcp.tool()
async def create_from_template(
    file_path: Annotated[str, Field(description="Path for the new .tex file")],
    template: Annotated[
        str,
        Field(description="Template name (bundled or custom)"),
    ] = "article",
    variables: Annotated[
        dict[str, str] | None,
        Field(description="Values for {{placeholder}} variables in the template"),
    ] = None,
    overwrite: Annotated[
        bool,
        Field(description="Replace the file if it already exists (default: refuse)"),
    ] = False,
) -> FileResult:
    """Create a LaTeX document from a bundled or custom template.

    Custom templates can be discovered through LATEX_SERVER_TEMPLATE_DIRS or a
    templates directory inside the base path. YAML front matter can declare
    placeholders, defaults, and required variables.
    """
    path = get_safe_path(file_path)
    _require_tex_suffix(path)
    _ensure_writable_target(path, overwrite)

    rendered = latex_templates.render_template(
        template,
        TEMPLATES_DIR,
        _custom_template_dirs(),
        variables=variables,
    )
    _atomic_write_text(path, rendered.content)

    detail = f" ({len(rendered.variables_used)} variable(s))" if rendered.variables_used else ""
    return FileResult(
        path=str(path),
        success=True,
        message=f"Created from '{rendered.name}' template{detail}",
        content=rendered.content,
    )


def _append_inside_document(content: str, new_text: str) -> str:
    """Append content before the end of the document when present."""
    body = new_text.rstrip("\n")
    analysis = _prepare_analysis(content)
    end_matches = list(_END_DOCUMENT_RE.finditer(analysis))
    if not end_matches:
        if not content:
            return body + ("\n" if body else "")
        return content.rstrip("\n") + ("\n" + body if body else "") + "\n"

    end_index = end_matches[-1].start()
    head = content[:end_index].rstrip("\n")
    tail = content[end_index:]
    if not body:
        return (head + "\n" if head else "") + tail
    return (head + "\n" if head else "") + body + "\n" + tail


def _prepend_inside_document(content: str, new_text: str) -> str:
    """Prepend content after the start of the document when present."""
    body = new_text.rstrip("\n")
    analysis = _prepare_analysis(content)
    match = _BEGIN_DOCUMENT_RE.search(analysis)
    if match is None:
        return (body + "\n" if body else "") + content

    head = content[: match.end()]
    tail = content[match.end() :].lstrip("\n")
    insertion = body + "\n" if body else ""
    return head + "\n" + insertion + tail


def _apply_edit_to_text(
    content: str,
    operation: str,
    new_text: str,
    search_text: str | None = None,
    line_number: int | None = None,
    replace_all: bool = False,
) -> tuple[str, str]:
    """Apply one edit operation to text and return (new_content, message)."""
    message = f"Operation '{operation}' applied"

    if operation != "replace" and not new_text:
        return content, f"Operation '{operation}' skipped: new_text is empty"

    if operation == "replace":
        if not search_text:
            raise ValueError("search_text is required for replace operation")
        occurrences = content.count(search_text)
        if occurrences == 0:
            raise ValueError(f"search_text not found in file: {search_text!r}")
        content = content.replace(search_text, new_text, -1 if replace_all else 1)
        replaced = occurrences if replace_all else 1
        message += f" ({replaced} occurrence(s) replaced)"
        if not replace_all and occurrences > 1:
            message += (
                f"; {occurrences - 1} more occurrence(s) left unchanged "
                "- set replace_all=True to replace all"
            )

    elif operation in ("insert_before", "insert_after"):
        if search_text and line_number is not None:
            raise ValueError(
                "Provide either search_text or line_number for insert operations, not both"
            )

        if search_text:
            index = content.find(search_text)
            if index == -1:
                raise ValueError(f"search_text not found in file: {search_text!r}")
            end = index + len(search_text)
            insertion = _ensure_newline(new_text)
            if operation == "insert_before":
                content = content[:index] + insertion + content[index:]
            else:
                before, after = content[:end], content[end:]
                body = new_text.rstrip("\n")
                prefix = "" if before.endswith(("\n", "\r")) else "\n"
                suffix = "" if after.startswith(("\n", "\r")) else "\n"
                content = before + prefix + body + suffix + after
        elif line_number is not None:
            lines = content.splitlines(keepends=True)
            if not (1 <= line_number <= len(lines)):
                raise ValueError(f"line_number {line_number} out of range (1-{len(lines)})")
            position = line_number - 1 if operation == "insert_before" else line_number
            if position > 0 and not lines[position - 1].endswith(("\n", "\r")):
                lines[position - 1] += "\n"
            lines.insert(position, _ensure_newline(new_text))
            content = "".join(lines)
        else:
            raise ValueError(
                "search_text or line_number is required for insert operations"
            )

    elif operation == "append":
        content = _append_inside_document(content, new_text)

    elif operation == "prepend":
        content = _prepend_inside_document(content, new_text)

    else:  # pragma: no cover - Literal validation normally prevents this
        raise ValueError(f"Unsupported operation: {operation!r}")

    return content, message



@mcp.tool()
async def edit_latex_file(
    file_path: Annotated[str, Field(description="Path to the .tex file to edit")],
    operation: Annotated[
        Literal["replace", "insert_before", "insert_after", "append", "prepend"],
        Field(description="Type of edit operation"),
    ],
    new_text: Annotated[str, Field(description="Text to insert or replace with")],
    search_text: Annotated[
        str | None,
        Field(description="Text to search for (required for replace/insert_before/insert_after)"),
    ] = None,
    line_number: Annotated[
        int | None,
        Field(ge=1, description="1-based line number (alternative to search_text for insert operations)"),
    ] = None,
    replace_all: Annotated[
        bool,
        Field(description="For 'replace', replace every occurrence instead of only the first"),
    ] = False,
) -> FileResult:
    """Edit an existing LaTeX file by replacing, inserting, appending, or prepending content.

    replace changes the first occurrence by default; set replace_all=True to
    change every occurrence. append and prepend operate inside the document body
    when the document environment is present.
    """
    path = get_safe_path(file_path)
    _require_file(path)
    content = _read_text_file(path)
    content, message = _apply_edit_to_text(
        content,
        operation,
        new_text,
        search_text=search_text,
        line_number=line_number,
        replace_all=replace_all,
    )
    _atomic_write_text(path, content)
    return FileResult(path=str(path), success=True, message=message, content=content)


@mcp.tool()
async def read_latex_file(
    file_path: Annotated[str, Field(description="Path to the .tex file to read")],
) -> FileResult:
    """Read and return the contents of a LaTeX file."""
    path = get_safe_path(file_path)
    content = _read_text_file(path)
    return FileResult(path=str(path), success=True, message="File read", content=content)


@mcp.tool()
async def list_latex_files(
    directory_path: Annotated[str, Field(description="Directory to search")] = ".",
    recursive: Annotated[bool, Field(description="Search subdirectories")] = False,
    max_results: Annotated[
        int,
        Field(ge=1, le=100_000, description="Maximum number of files to return"),
    ] = 1000,
) -> FileListResult:
    """List .tex files in a directory, skipping symlinks that escape the base path."""
    dir_path = get_safe_path(directory_path)
    if not dir_path.exists():
        raise FileNotFoundError(f"Directory not found: {dir_path}")
    if not dir_path.is_dir():
        raise NotADirectoryError(f"Not a directory: {dir_path}")

    pattern = "**/*.tex" if recursive else "*.tex"
    files: list[FileInfo] = []
    for candidate in dir_path.glob(pattern):
        if not candidate.is_file():
            continue
        try:
            resolved = candidate.resolve()
        except OSError:  # pragma: no cover - broken loops are platform-specific
            continue
        if not _is_within_base(resolved):
            continue
        try:
            relative = candidate.relative_to(BASE_PATH)
        except ValueError:  # pragma: no cover - guarded by the containment check
            relative = candidate
        try:
            stat_result = candidate.stat()
        except OSError:  # pragma: no cover - file vanished during listing
            continue
        modified = datetime.fromtimestamp(
            stat_result.st_mtime, tz=timezone.utc
        ).isoformat()
        files.append(
            FileInfo(
                path=str(relative),
                size_bytes=stat_result.st_size,
                modified_at=modified,
            )
        )

    files.sort(key=lambda item: item.path)
    total = len(files)
    limit = max(1, int(max_results))
    return FileListResult(
        directory=str(dir_path),
        files=files[:limit],
        total=total,
        truncated=total > limit,
    )


def _validate_text(content: str, *, check_references: bool = True) -> list[str]:
    """Validate LaTeX text and return human-readable issues."""
    analysis = _prepare_analysis(content)
    issues: list[str] = []

    # Document-level declarations.
    if not re.search(r"\\documentclass(?![A-Za-z])", analysis):
        issues.append("Missing \\documentclass declaration")
    begin_document = list(_BEGIN_DOCUMENT_RE.finditer(analysis))
    end_document = list(_END_DOCUMENT_RE.finditer(analysis))
    if not begin_document:
        issues.append("Missing \\begin{document}")
    if not end_document:
        issues.append("Missing \\end{document}")
    if len(begin_document) > 1:
        issues.append(f"Found {len(begin_document)} \\begin{{document}} declarations")
    if len(end_document) > 1:
        issues.append(f"Found {len(end_document)} \\end{{document}} declarations")

    documentclass_match = re.search(r"\\documentclass(?![A-Za-z])", analysis)
    if (
        documentclass_match
        and begin_document
        and documentclass_match.start() > begin_document[0].start()
    ):
        issues.append("\\documentclass appears after \\begin{document}")

    # Balanced braces (escaped braces are ignored).
    opens, closes = _count_braces(analysis)
    if opens != closes:
        issues.append(f"Unbalanced braces: {opens} opening vs {closes} closing")

    # Environment matching.
    _check_environments(analysis, issues)

    # Content after \end{document} is silently ignored by LaTeX and usually a bug.
    if end_document:
        tail_start = end_document[-1].end()
        tail = analysis[tail_start:]
        if tail.strip():
            first_content = tail_start + len(tail) - len(tail.lstrip())
            issues.append(
                f"Content found after \\end{{document}} "
                f"(line {_line_number(analysis, first_content)})"
            )

    # Undefined references.
    if check_references:
        references: set[str] = set()
        for match in _REFERENCE_RE.finditer(analysis):
            for reference in match.group(1).split(","):
                reference = reference.strip()
                if reference:
                    references.add(reference)
        labels = {
            match.group(1).strip()
            for match in _LABEL_RE.finditer(analysis)
            if match.group(1).strip()
        }
        for reference in sorted(references - labels):
            issues.append(f"Undefined reference: {reference}")

    return issues



@mcp.tool()
async def validate_latex(
    file_path: Annotated[str, Field(description="Path to the .tex file to validate")],
    check_references: Annotated[
        bool,
        Field(
            description=(
                "Check that ref-family commands point to a label in this file. "
                "Disable for multi-file projects whose labels live elsewhere."
            )
        ),
    ] = True,
) -> ValidationResult:
    """Perform LaTeX syntax validation (structure, braces, environments, references)."""
    path = get_safe_path(file_path)
    _require_file(path)
    content = _read_text_file(path)
    issues = _validate_text(content, check_references=check_references)
    return ValidationResult(path=str(path), valid=len(issues) == 0, issues=issues)


@mcp.tool()
async def get_latex_structure(
    file_path: Annotated[str, Field(description="Path to the .tex file to analyze")],
) -> StructureInfo:
    """Extract document structure: class, title, author, date, packages, and section hierarchy."""
    path = get_safe_path(file_path)
    _require_file(path)
    content = _read_text_file(path)
    analysis = _prepare_analysis(content)

    document_class: str | None = None
    class_match = _DOCUMENTCLASS_RE.search(analysis)
    if class_match:
        document_class = class_match.group(1).strip()

    packages: list[str] = []
    for match in _PACKAGE_RE.finditer(analysis):
        for package in match.group(1).split(","):
            package = package.strip()
            if package and package not in packages:
                packages.append(package)

    sections: list[str] = []
    for match in _SECTION_RE.finditer(analysis):
        title, _ = _read_braced(analysis, match.end() - 1)
        if title is None:
            continue
        level = _SECTION_LEVELS[match.group(1)]
        marker = "*" if match.group(2) else ""
        sections.append(f"{level}{marker}: {title.strip()}")

    return StructureInfo(
        path=str(path),
        document_class=document_class,
        title=_find_command_arg(analysis, "title"),
        author=_find_command_arg(analysis, "author"),
        date=_find_command_arg(analysis, "date"),
        packages=packages,
        sections=sections,
    )


_FILE_LINE_ERROR_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):\s*(?P<message>.+)$")
_BANG_ERROR_RE = re.compile(r"^!\s*(?P<message>.+)$")
_LOG_LINE_RE = re.compile(r"^l\.(?P<line>\d+)\s*(?P<context>.*)$")


def _dedupe(items: list[str]) -> list[str]:
    """Remove duplicates while preserving order."""
    return list(dict.fromkeys(items))


def _parse_latex_log(log_text: str) -> tuple[list[str], list[str]]:
    """Extract errors and warnings, with line numbers when available."""
    errors: list[str] = []
    warnings: list[str] = []
    lines = log_text.splitlines()

    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue

        file_line = _FILE_LINE_ERROR_RE.match(line)
        if file_line and not line.startswith("!") and "Warning:" not in line:
            errors.append(
                f"{file_line.group('file')}:{file_line.group('line')}: "
                f"{file_line.group('message').strip()}"
            )
            continue

        bang = _BANG_ERROR_RE.match(line)
        if bang:
            message = bang.group("message").strip()
            line_number: str | None = None
            for following in lines[index + 1 : index + 8]:
                context_match = _LOG_LINE_RE.match(following.strip())
                if context_match:
                    line_number = context_match.group("line")
                    break
            errors.append(
                f"{message} (line {line_number})" if line_number else message
            )

        if "Warning:" in line and not line.startswith("!"):
            warnings.append(line)

    return _dedupe(errors)[:50], _dedupe(warnings)[:50]


def _read_log_file(log_path: Path, fallback: str) -> str:
    """Read the compiler log file, falling back to captured stdout."""
    if log_path.is_file():
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
            if text.strip():
                return text
        except OSError:  # pragma: no cover - protected by is_file
            pass
    return fallback


def _needs_second_pass(tex_path: Path) -> bool:
    """Return True when the first pass produced files that a rerun can resolve."""
    for suffix in SECOND_PASS_SUFFIXES:
        candidate = tex_path.parent / (tex_path.stem + suffix)
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return True
        except OSError:  # pragma: no cover - file vanished
            continue
    return False


def _auxiliary_paths(tex_path: Path) -> list[Path]:
    """Return auxiliary file paths for a compiled document."""
    return [tex_path.parent / (tex_path.stem + suffix) for suffix in AUX_SUFFIXES]


@mcp.tool()
async def compile_latex(
    file_path: Annotated[str, Field(description="Path to the .tex file to compile")],
    engine: Literal["pdflatex", "xelatex", "lualatex"] = "pdflatex",
    timeout_seconds: Annotated[
        int,
        Field(ge=1, le=600, description="Per-pass compilation timeout in seconds"),
    ] = 120,
    clean_aux: Annotated[
        bool,
        Field(description="Remove auxiliary files after a successful build"),
    ] = False,
    shell_escape: Annotated[
        bool,
        Field(
            description=(
                "Enable write18 shell escape. Disabled by default for safety; "
                "only enable for trusted documents."
            )
        ),
    ] = False,
    # FastMCP 1.9.x detects context parameters by class annotation; keep this
    # non-optional and default to None for direct Python callers.
    ctx: Context = None,  # type: ignore[assignment]
) -> CompileResult:
    """Compile a LaTeX file to PDF using the specified engine.

    The engine runs a second pass only when the first pass produced auxiliary
    files (references, table of contents, hyperlinks). Shell escape is disabled
    by default.
    """
    if engine not in ("pdflatex", "xelatex", "lualatex"):
        raise ValueError(
            f"Unsupported engine: {engine!r}. Choose pdflatex, xelatex, or lualatex."
        )

    path = get_safe_path(file_path)
    _require_tex_suffix(path)
    _require_file(path)

    engine_path = shutil.which(engine)
    if not engine_path:
        raise RuntimeError(
            f"'{engine}' not found on PATH. Install a LaTeX distribution "
            "(TeX Live, MiKTeX, or MacTeX)."
        )

    timeout = max(1, int(timeout_seconds))
    source = path.name
    if source.startswith("-"):
        # Prevent a file name from being interpreted as an engine option.
        source = f"./{source}"

    command = [
        engine_path,
        "-interaction=nonstopmode",
        "-file-line-error",
        "-halt-on-error",
        "-shell-escape" if shell_escape else "-no-shell-escape",
        source,
    ]
    log_path = path.parent / f"{path.stem}.log"
    try:
        # Avoid parsing a stale log if the compiler dies before writing a new one.
        log_path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - unusual filesystem permissions
        pass

    async def report(progress: int, total: int) -> None:
        if ctx is None:
            return
        try:
            # Keep the two-argument form for compatibility with older MCP SDKs.
            await ctx.report_progress(progress, total)
        except Exception:  # pragma: no cover - client may not support progress
            logger.debug("Progress reporting failed", exc_info=True)

    if ctx is not None:
        try:
            await ctx.info(f"Compiling {path.name} with {engine}...")
        except Exception:  # pragma: no cover - client may not support logging
            logger.debug("Logging failed", exc_info=True)
    await report(0, 2)

    started = time.monotonic()
    all_warnings: list[str] = []
    last_log = ""
    passes = 0

    for pass_number in (1, 2):
        passes = pass_number
        try:
            subprocess_options: dict[str, object] = {
                "cwd": str(path.parent),
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
            }
            if os.name != "nt":
                # Put the compiler in its own process group so a timeout also
                # kills helper processes that inherited the output pipes.
                subprocess_options["start_new_session"] = True
            process = await asyncio.create_subprocess_exec(
                *command, **subprocess_options
            )
        except OSError as exc:
            return CompileResult(
                path=str(path),
                success=False,
                log_output=last_log[-MAX_LOG_OUTPUT_CHARS:],
                errors=[f"Failed to start {engine}: {exc}"],
                warnings=all_warnings,
                passes=pass_number,
                duration_seconds=round(time.monotonic() - started, 3),
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            killed = False
            if os.name != "nt":
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    killed = True
                except (ProcessLookupError, PermissionError):  # pragma: no cover
                    pass
            if not killed:
                try:
                    process.kill()
                except ProcessLookupError:  # pragma: no cover - already exited
                    pass
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):  # pragma: no cover
                pass
            timed_out_log = _read_log_file(log_path, last_log)
            return CompileResult(
                path=str(path),
                success=False,
                log_path=str(log_path) if log_path.is_file() else None,
                log_output=timed_out_log[-MAX_LOG_OUTPUT_CHARS:],
                errors=[f"Compilation timed out after {timeout} second(s)"],
                warnings=all_warnings,
                passes=pass_number,
                duration_seconds=round(time.monotonic() - started, 3),
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        log_text = _read_log_file(log_path, stdout)
        last_log = log_text
        errors, warnings = _parse_latex_log(log_text + "\n" + stderr)
        all_warnings = _dedupe(all_warnings + warnings)

        await report(pass_number, 2)

        if process.returncode != 0:
            if not errors:
                errors = [
                    f"Compilation failed with exit code {process.returncode}"
                ]
            return CompileResult(
                path=str(path),
                success=False,
                log_path=str(log_path) if log_path.is_file() else None,
                log_output=log_text[-MAX_LOG_OUTPUT_CHARS:],
                errors=errors,
                warnings=all_warnings,
                passes=pass_number,
                duration_seconds=round(time.monotonic() - started, 3),
            )

        if pass_number == 1 and not _needs_second_pass(path):
            break

    pdf_path = path.with_suffix(".pdf")
    duration = round(time.monotonic() - started, 3)

    if not pdf_path.is_file():
        return CompileResult(
            path=str(path),
            success=False,
            log_path=str(log_path) if log_path.is_file() else None,
            log_output=last_log[-MAX_LOG_OUTPUT_CHARS:],
            errors=["Compilation finished but no PDF was produced"],
            warnings=all_warnings,
            passes=passes,
            duration_seconds=duration,
        )

    log_path_value: str | None = str(log_path) if log_path.is_file() else None
    if clean_aux:
        for auxiliary in _auxiliary_paths(path):
            auxiliary.unlink(missing_ok=True)
        log_path_value = None

    return CompileResult(
        path=str(path),
        success=True,
        pdf_path=str(pdf_path),
        log_path=log_path_value,
        log_output=last_log[-MAX_LOG_OUTPUT_CHARS:],
        warnings=all_warnings,
        passes=passes,
        duration_seconds=duration,
    )


# --- Project Tools ---


@mcp.tool()
async def analyze_project(
    file_path: Annotated[str, Field(description="Path to the project main .tex file")],
) -> latex_project.ProjectInfo:
    """Analyze a multi-file project: includes, labels, references, and bibliography."""
    return latex_project.analyze_project(file_path)


@mcp.tool()
async def validate_project(
    file_path: Annotated[str, Field(description="Path to the project main .tex file")],
    check_references: Annotated[
        bool,
        Field(description="Check references against labels collected across the project"),
    ] = True,
) -> latex_project.ProjectValidationResult:
    """Validate a whole project, including cross-file references and includes."""
    return latex_project.validate_project(file_path, check_references=check_references)


@mcp.tool()
async def compile_project(
    file_path: Annotated[str, Field(description="Path to the project main .tex file")],
    engine: Literal["pdflatex", "xelatex", "lualatex"] = "pdflatex",
    timeout_seconds: Annotated[
        int,
        Field(ge=1, le=600, description="Timeout for each compiler or bibliography step"),
    ] = 120,
    clean_aux: Annotated[
        bool,
        Field(description="Remove auxiliary files after a successful build"),
    ] = False,
    shell_escape: Annotated[
        bool,
        Field(description="Enable write18 shell escape for trusted documents"),
    ] = False,
    run_bibliography: Annotated[
        bool,
        Field(description="Run bibtex or biber and recompile when a bibliography is detected"),
    ] = True,
    use_latexmk: Annotated[
        bool,
        Field(description="Use latexmk when available, otherwise fall back to direct engine passes"),
    ] = False,
    ctx: Context = None,  # type: ignore[assignment]
) -> latex_project.ProjectCompileResult:
    """Compile a multi-file project with bibliography and rerun sequencing."""
    if engine not in ("pdflatex", "xelatex", "lualatex"):
        raise ValueError(
            f"Unsupported engine: {engine!r}. Choose pdflatex, xelatex, or lualatex."
        )
    if ctx is not None:
        try:
            await ctx.info(f"Compiling project {file_path} with {engine}...")
        except Exception:  # pragma: no cover - optional client capability
            logger.debug("Project logging failed", exc_info=True)
    result = await latex_project.compile_project(
        file_path,
        engine=engine,
        timeout_seconds=timeout_seconds,
        clean_aux=clean_aux,
        shell_escape=shell_escape,
        run_bibliography=run_bibliography,
        use_latexmk=use_latexmk,
    )
    if ctx is not None:
        try:
            await ctx.report_progress(1, 1)
        except Exception:  # pragma: no cover - optional client capability
            logger.debug("Project progress failed", exc_info=True)
    return result



# --- Authoring Tools ---


@mcp.tool()
async def apply_edits(
    file_path: Annotated[str, Field(description="Path to the .tex file to edit")],
    edits: Annotated[
        list[latex_authoring.EditSpec],
        Field(description="Ordered edit operations applied as one transaction"),
    ],
    snapshot: Annotated[
        bool,
        Field(description="Save a snapshot before writing"),
    ] = True,
    validate_after: Annotated[
        bool,
        Field(description="Reject the batch if the edited document fails validation"),
    ] = False,
) -> latex_authoring.AuthoringResult:
    """Apply multiple edits atomically; a failing edit leaves the file untouched."""
    return latex_authoring.apply_edits(
        file_path,
        edits,
        snapshot=snapshot,
        validate_after=validate_after,
    )


@mcp.tool()
async def edit_section(
    file_path: Annotated[str, Field(description="Path to the .tex file to edit")],
    section: Annotated[str, Field(description="Section title to locate")],
    operation: Annotated[
        Literal["replace", "append", "prepend", "delete"],
        Field(description="How to modify the section body"),
    ],
    new_text: Annotated[str, Field(description="Replacement or inserted text")] = "",
    section_level: Annotated[
        Literal[
            "part",
            "chapter",
            "section",
            "subsection",
            "subsubsection",
            "paragraph",
            "subparagraph",
        ]
        | None,
        Field(description="Disambiguate duplicate section titles"),
    ] = None,
    snapshot: Annotated[
        bool,
        Field(description="Save a snapshot before writing"),
    ] = True,
) -> latex_authoring.AuthoringResult:
    """Edit a named section body without touching its heading."""
    return latex_authoring.edit_section(
        file_path,
        section,
        operation,
        new_text=new_text,
        section_level=section_level,
        snapshot=snapshot,
    )


@mcp.tool()
async def create_snapshot(
    file_path: Annotated[str, Field(description="Path to the .tex file to snapshot")],
    label: Annotated[str | None, Field(description="Optional snapshot label")] = None,
) -> latex_authoring.SnapshotInfo:
    """Store a snapshot of a file that can be restored later."""
    return latex_authoring.create_snapshot(file_path, label=label)


@mcp.tool()
async def list_snapshots(
    file_path: Annotated[str, Field(description="Path to the .tex file")],
    limit: Annotated[int, Field(ge=1, le=1000, description="Maximum snapshots to return")] = 100,
) -> latex_authoring.SnapshotListResult:
    """List snapshots for one file, newest first."""
    return latex_authoring.list_snapshots(file_path, limit=limit)


@mcp.tool()
async def restore_snapshot(
    file_path: Annotated[str, Field(description="Path to the .tex file to restore")],
    snapshot_id: Annotated[str, Field(description="Snapshot id returned by list_snapshots")],
    backup_current: Annotated[
        bool,
        Field(description="Snapshot the current content before restoring"),
    ] = True,
) -> latex_authoring.AuthoringResult:
    """Restore a previously saved snapshot."""
    return latex_authoring.restore_snapshot(
        file_path, snapshot_id, backup_current=backup_current
    )


@mcp.tool()
async def insert_table(
    file_path: Annotated[str, Field(description="Path to the .tex file")],
    headers: Annotated[list[str], Field(description="Table column headers")],
    rows: Annotated[list[list[str]], Field(description="Table rows")],
    caption: Annotated[str, Field(description="Table caption")] = "",
    label: Annotated[str | None, Field(description="LaTeX label")] = None,
    placement: Annotated[str, Field(description="Float placement, e.g. htbp")] = "htbp",
    section: Annotated[str | None, Field(description="Section to insert into")] = None,
    section_level: Annotated[
        Literal[
            "part",
            "chapter",
            "section",
            "subsection",
            "subsubsection",
            "paragraph",
            "subparagraph",
        ]
        | None,
        Field(description="Disambiguate duplicate section titles"),
    ] = None,
    snapshot: Annotated[bool, Field(description="Save a snapshot before writing")] = True,
) -> latex_authoring.AuthoringResult:
    """Insert a booktabs-free tabular table environment."""
    return latex_authoring.insert_table(
        file_path,
        headers,
        rows,
        caption=caption,
        label=label,
        placement=placement,
        section=section,
        section_level=section_level,
        snapshot=snapshot,
    )


@mcp.tool()
async def insert_figure(
    file_path: Annotated[str, Field(description="Path to the .tex file")],
    image_path: Annotated[str, Field(description="LaTeX path to the image file")],
    caption: Annotated[str, Field(description="Figure caption")] = "",
    label: Annotated[str | None, Field(description="LaTeX label")] = None,
    width: Annotated[str, Field(description="includegraphics width")] = "0.8\\textwidth",
    placement: Annotated[str, Field(description="Float placement, e.g. htbp")] = "htbp",
    section: Annotated[str | None, Field(description="Section to insert into")] = None,
    section_level: Annotated[
        Literal[
            "part",
            "chapter",
            "section",
            "subsection",
            "subsubsection",
            "paragraph",
            "subparagraph",
        ]
        | None,
        Field(description="Disambiguate duplicate section titles"),
    ] = None,
    snapshot: Annotated[bool, Field(description="Save a snapshot before writing")] = True,
) -> latex_authoring.AuthoringResult:
    """Insert a figure environment and includegraphics command."""
    return latex_authoring.insert_figure(
        file_path,
        image_path,
        caption=caption,
        label=label,
        width=width,
        placement=placement,
        section=section,
        section_level=section_level,
        snapshot=snapshot,
    )


@mcp.tool()
async def insert_citation(
    file_path: Annotated[str, Field(description="Path to the .tex file")],
    keys: Annotated[list[str], Field(description="Citation keys")],
    command: Annotated[
        Literal["cite", "citep", "citet", "parencite", "textcite", "autocite"],
        Field(description="Citation command to emit"),
    ] = "cite",
    section: Annotated[str | None, Field(description="Section to insert into")] = None,
    section_level: Annotated[
        Literal[
            "part",
            "chapter",
            "section",
            "subsection",
            "subsubsection",
            "paragraph",
            "subparagraph",
        ]
        | None,
        Field(description="Disambiguate duplicate section titles"),
    ] = None,
    snapshot: Annotated[bool, Field(description="Save a snapshot before writing")] = True,
) -> latex_authoring.AuthoringResult:
    """Insert a citation command, optionally inside a section."""
    return latex_authoring.insert_citation(
        file_path,
        keys,
        command=command,
        section=section,
        section_level=section_level,
        snapshot=snapshot,
    )



# --- Quality and PDF Tools ---


@mcp.tool()
async def count_words(
    file_path: Annotated[str, Field(description="Path to the .tex file")],
) -> latex_quality.WordCountResult:
    """Count visible words, lines, characters, and sections."""
    return latex_quality.word_count(file_path)


@mcp.tool()
async def find_todos(
    file_path: Annotated[str, Field(description="Path to the .tex file")],
) -> latex_quality.TodoResult:
    """Find TODO, FIXME, XXX, HACK, and NOTE markers."""
    return latex_quality.find_todos(file_path)


@mcp.tool()
async def lint_latex(
    file_path: Annotated[str, Field(description="Path to the .tex file")],
    timeout_seconds: Annotated[int, Field(ge=1, le=600, description="chktex timeout")] = 60,
) -> latex_quality.LintResult:
    """Run chktex when installed and always include built-in validation issues."""
    return await latex_quality.lint_latex(file_path, timeout_seconds=timeout_seconds)


@mcp.tool()
async def format_latex(
    file_path: Annotated[str, Field(description="Path to the .tex file")],
    in_place: Annotated[
        bool,
        Field(description="Write the formatted content back (default: only return it)"),
    ] = False,
    timeout_seconds: Annotated[int, Field(ge=1, le=600, description="latexindent timeout")] = 60,
    snapshot: Annotated[
        bool,
        Field(description="Snapshot before writing formatted content"),
    ] = True,
) -> latex_quality.FormatResult:
    """Run latexindent when installed, optionally writing the result back."""
    return await latex_quality.format_latex(
        file_path,
        in_place=in_place,
        timeout_seconds=timeout_seconds,
        snapshot=snapshot,
    )


@mcp.tool()
async def spell_check(
    file_path: Annotated[str, Field(description="Path to the .tex file")],
    language: Annotated[str, Field(description="Dictionary language, e.g. en_US")] = "en_US",
    timeout_seconds: Annotated[int, Field(ge=1, le=600, description="Spell checker timeout")] = 60,
) -> latex_quality.SpellCheckResult:
    """Run hunspell or aspell against visible prose when installed."""
    return await latex_quality.spell_check(
        file_path, language=language, timeout_seconds=timeout_seconds
    )


@mcp.tool()
async def analyze_log(
    file_path: Annotated[str, Field(description="Path to a LaTeX .log file")],
) -> latex_quality.LogAnalysisResult:
    """Parse errors, warnings, and overfull/underfull box reports from a log."""
    return latex_quality.analyze_log(file_path)


@mcp.tool()
async def get_pdf_info(
    file_path: Annotated[str, Field(description="Path to the PDF")],
    text_preview_chars: Annotated[
        int,
        Field(ge=0, le=10_000, description="Characters of first-page text to preview"),
    ] = 500,
) -> latex_quality.PdfInfoResult:
    """Read PDF metadata, page count, and a short first-page preview."""
    return latex_quality.pdf_info(file_path, text_preview_chars=text_preview_chars)


@mcp.tool()
async def extract_pdf_text(
    file_path: Annotated[str, Field(description="Path to the PDF")],
    page_start: Annotated[int, Field(ge=1, description="First page (1-based)")] = 1,
    page_end: Annotated[int | None, Field(ge=1, description="Last page, inclusive")] = None,
    max_chars: Annotated[int, Field(ge=1, le=1_000_000, description="Maximum characters")] = 50_000,
) -> latex_quality.PdfTextResult:
    """Extract text from a PDF page range."""
    return latex_quality.pdf_text(
        file_path,
        page_start=page_start,
        page_end=page_end,
        max_chars=max_chars,
    )


@mcp.tool()
async def render_pdf_page(
    file_path: Annotated[str, Field(description="Path to the PDF")],
    page: Annotated[int, Field(ge=1, description="Page number to render")] = 1,
    dpi: Annotated[int, Field(ge=36, le=600, description="Raster resolution")] = 150,
    timeout_seconds: Annotated[int, Field(ge=1, le=600, description="pdftoppm timeout")] = 60,
) -> latex_quality.PdfRenderResult:
    """Render one PDF page to a PNG next to the PDF using pdftoppm."""
    return await latex_quality.render_pdf_page(
        file_path, page=page, dpi=dpi, timeout_seconds=timeout_seconds
    )


@mcp.tool()
async def read_pdf_file(
    file_path: Annotated[
        str,
        Field(description="Path to a PDF inside the base directory"),
    ],
) -> Any:
    """Return a PDF directly as an MCP embedded resource with MIME type application/pdf."""
    path = get_safe_path(file_path)
    _require_file(path)
    if path.suffix.lower() != ".pdf":
        raise ValueError(
            f"Expected a .pdf file, got '{path.name}'. "
            "Use compile_latex or compile_project to create a PDF first."
        )

    size = path.stat().st_size
    if size > MAX_PDF_BYTES:
        raise ValueError(
            f"PDF is too large ({size:,} bytes); the configured limit is "
            f"{MAX_PDF_BYTES:,} bytes. Set LATEX_SERVER_MAX_PDF_BYTES to raise it."
        )

    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return EmbeddedResource(
        type="resource",
        resource=BlobResourceContents(
            uri=path.as_uri(),
            mimeType="application/pdf",
            blob=encoded,
        ),
    )


# --- Resources ---


@mcp.resource("latex://templates")
async def list_templates() -> str:
    """List available LaTeX templates."""
    listing = latex_templates.build_template_listing(
        TEMPLATES_DIR, _custom_template_dirs()
    )
    if not listing.templates:
        message = "No templates available."
        if listing.errors:
            message += " Errors: " + "; ".join(listing.errors)
        return message

    lines = ["Available templates:"]
    for template in listing.templates:
        origin = "bundled" if template.builtin else "custom"
        description = f" - {template.description}" if template.description else ""
        lines.append(f"- {template.name} ({origin}){description}")
    if listing.errors:
        lines.append("Template errors: " + "; ".join(listing.errors))
    return "\n".join(lines)


@mcp.resource("latex://template/{name}")
async def get_template(name: str) -> str:
    """Get the content of a specific template without rendering placeholders."""
    _info, body = latex_templates.template_body(
        name, TEMPLATES_DIR, _custom_template_dirs()
    )
    return body


@mcp.tool()
async def list_template_details() -> TemplateListResult:
    """List template names, descriptions, variables, and origins."""
    return latex_templates.build_template_listing(
        TEMPLATES_DIR, _custom_template_dirs()
    )


# --- Entry Point ---


def main() -> None:
    """Configure logging and run the server."""
    level_name = os.environ.get("LATEX_SERVER_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logger.info("Base directory: %s", BASE_PATH)
    logger.info("Templates directory: %s", TEMPLATES_DIR)
    mcp.run()


if __name__ == "__main__":
    main()
