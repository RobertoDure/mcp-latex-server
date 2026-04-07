#!/usr/bin/env python3
"""
MCP LaTeX Server v2 - A Model Context Protocol server for LaTeX file creation,
editing, validation, and compilation.

Uses FastMCP for automatic schema generation from type hints.
"""

import asyncio
import logging
import os
import re
import subprocess
import shutil
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP, Context
from pydantic import BaseModel, Field

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration ---

BASE_PATH = Path(os.environ.get("LATEX_SERVER_BASE_PATH", ".")).resolve()
TEMPLATES_DIR = Path(__file__).parent / "templates"

# --- Structured Output Models ---


class FileResult(BaseModel):
    """Result of a file operation."""
    path: str = Field(description="Absolute path to the file")
    success: bool
    message: str
    content: str | None = Field(default=None, description="File content if applicable")


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
    packages: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)


class CompileResult(BaseModel):
    """Result of LaTeX compilation."""
    path: str
    success: bool
    pdf_path: str | None = None
    log_output: str = ""
    errors: list[str] = Field(default_factory=list)


class FileInfo(BaseModel):
    """Information about a LaTeX file."""
    path: str
    size_bytes: int


class FileListResult(BaseModel):
    """Result of listing LaTeX files."""
    directory: str
    files: list[FileInfo] = Field(default_factory=list)


# --- Path Security ---


def get_safe_path(file_path: str) -> Path:
    """Resolve a file path, ensuring it stays within BASE_PATH."""
    path = Path(file_path)
    if not path.is_absolute():
        path = BASE_PATH / path
    resolved = path.resolve()
    try:
        resolved.relative_to(BASE_PATH)
    except ValueError:
        raise ValueError(
            f"Access denied: '{file_path}' resolves outside the allowed directory ({BASE_PATH})"
        )
    return resolved


# --- Template Generation ---


def create_latex_template(
    document_type: str,
    title: str,
    author: str,
    date: str,
    content: str,
    packages: list[str],
    geometry: str,
) -> str:
    """Create a LaTeX document from parameters."""
    parts: list[str] = []
    parts.append(f"\\documentclass{{{document_type}}}")

    # Core packages
    if geometry:
        parts.append(f"\\usepackage[{geometry}]{{geometry}}")
    parts.append("\\usepackage[utf8]{inputenc}")
    parts.append("\\usepackage[T1]{fontenc}")
    parts.append("\\usepackage[english]{babel}")

    for pkg in packages:
        parts.append(f"\\usepackage{{{pkg}}}")

    if title:
        parts.append(f"\\title{{{title}}}")
    if author:
        parts.append(f"\\author{{{author}}}")
    if date:
        parts.append(f"\\date{{{date}}}")

    parts.append("")
    parts.append("\\begin{document}")
    if title:
        parts.append("\\maketitle")
    parts.append("")
    parts.append(content if content else "% Your content here")
    parts.append("")
    parts.append("\\end{document}")

    return "\n".join(parts)


# --- Server Setup ---

mcp = FastMCP(
    "latex-server",
    instructions=(
        "LaTeX document server. Use tools to create, edit, read, validate, "
        "compile, and list LaTeX files. All file paths are relative to the "
        "configured base directory."
    ),
)


# --- Tools ---


@mcp.tool()
async def create_latex_file(
    file_path: str = Field(description="Path for the new .tex file (relative to base dir)"),
    document_type: Literal["article", "report", "book", "letter", "beamer", "minimal"] = "article",
    title: str = "",
    author: str = "",
    date: str = "\\today",
    content: str = "",
    packages: list[str] = Field(default_factory=list, description="Extra LaTeX packages to include"),
    geometry: str = Field(default="", description="Geometry settings, e.g. 'margin=1in'"),
) -> FileResult:
    """Create a new LaTeX document with specified content and structure."""
    path = get_safe_path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    latex_content = create_latex_template(
        document_type, title, author, date, content, packages, geometry
    )
    path.write_text(latex_content, encoding="utf-8")

    return FileResult(path=str(path), success=True, message="File created", content=latex_content)


@mcp.tool()
async def create_from_template(
    file_path: str = Field(description="Path for the new .tex file"),
    template: Literal["article", "beamer", "report"] = "article",
) -> FileResult:
    """Create a LaTeX document from a bundled template file."""
    path = get_safe_path(file_path)
    template_file = TEMPLATES_DIR / f"{template}_template.tex"

    if not template_file.exists():
        raise ValueError(f"Template '{template}' not found at {template_file}")

    template_content = template_file.read_text(encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template_content, encoding="utf-8")

    return FileResult(path=str(path), success=True, message=f"Created from '{template}' template", content=template_content)


@mcp.tool()
async def edit_latex_file(
    file_path: str = Field(description="Path to the .tex file to edit"),
    operation: Literal["replace", "insert_before", "insert_after", "append", "prepend"] = Field(
        description="Type of edit operation"
    ),
    new_text: str = Field(description="Text to insert or replace with"),
    search_text: str | None = Field(
        default=None, description="Text to search for (required for replace/insert_before/insert_after)"
    ),
    line_number: int | None = Field(
        default=None, description="1-based line number (alternative to search_text for insert operations)"
    ),
) -> FileResult:
    """Edit an existing LaTeX file by replacing, inserting, or appending content."""
    path = get_safe_path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    content = path.read_text(encoding="utf-8")

    if operation == "replace":
        if not search_text:
            raise ValueError("search_text is required for replace operation")
        if search_text not in content:
            raise ValueError(f"search_text not found in file")
        content = content.replace(search_text, new_text)

    elif operation in ("insert_before", "insert_after"):
        if search_text:
            if search_text not in content:
                raise ValueError("search_text not found in file")
            if operation == "insert_before":
                content = content.replace(search_text, f"{new_text}\n{search_text}")
            else:
                content = content.replace(search_text, f"{search_text}\n{new_text}")
        elif line_number is not None:
            lines = content.splitlines()
            if not (1 <= line_number <= len(lines)):
                raise ValueError(f"line_number {line_number} out of range (1-{len(lines)})")
            idx = line_number - 1 if operation == "insert_before" else line_number
            lines.insert(idx, new_text)
            content = "\n".join(lines)
        else:
            raise ValueError("search_text or line_number is required for insert operations")

    elif operation == "append":
        content = content.rstrip("\n") + "\n" + new_text + "\n"

    elif operation == "prepend":
        content = new_text + "\n" + content

    path.write_text(content, encoding="utf-8")
    return FileResult(path=str(path), success=True, message=f"Operation '{operation}' applied")


@mcp.tool()
async def read_latex_file(
    file_path: str = Field(description="Path to the .tex file to read"),
) -> FileResult:
    """Read and return the contents of a LaTeX file."""
    path = get_safe_path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    content = path.read_text(encoding="utf-8")
    return FileResult(path=str(path), success=True, message="File read", content=content)


@mcp.tool()
async def list_latex_files(
    directory_path: str = Field(default=".", description="Directory to search"),
    recursive: bool = Field(default=False, description="Search subdirectories"),
) -> FileListResult:
    """List all .tex files in a directory."""
    dir_path = get_safe_path(directory_path)
    if not dir_path.is_dir():
        raise NotADirectoryError(f"Not a directory: {dir_path}")

    pattern = "**/*.tex" if recursive else "*.tex"
    files = [
        FileInfo(
            path=str(f.relative_to(BASE_PATH)),
            size_bytes=f.stat().st_size,
        )
        for f in sorted(dir_path.glob(pattern))
        if f.is_file()
    ]
    return FileListResult(directory=str(dir_path), files=files)


@mcp.tool()
async def validate_latex(
    file_path: str = Field(description="Path to the .tex file to validate"),
) -> ValidationResult:
    """Perform LaTeX syntax validation (structure, braces, environments, references)."""
    path = get_safe_path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    content = path.read_text(encoding="utf-8")
    issues: list[str] = []

    # Strip comments before analysis
    stripped = re.sub(r"(?<!\\)%.*", "", content)

    if "\\documentclass" not in stripped:
        issues.append("Missing \\documentclass declaration")
    if "\\begin{document}" not in stripped:
        issues.append("Missing \\begin{document}")
    if "\\end{document}" not in stripped:
        issues.append("Missing \\end{document}")

    # Balanced braces (ignore escaped braces)
    clean = re.sub(r"\\[{}]", "", stripped)  # remove \{ and \}
    # Also remove verbatim-like content
    clean = re.sub(r"\\begin\{verbatim\}.*?\\end\{verbatim\}", "", clean, flags=re.DOTALL)
    opens = clean.count("{")
    closes = clean.count("}")
    if opens != closes:
        issues.append(f"Unbalanced braces: {opens} opening vs {closes} closing")

    # Environment matching (order-aware with a stack)
    begin_iter = list(re.finditer(r"\\begin\{([^}]+)\}", stripped))
    end_iter = list(re.finditer(r"\\end\{([^}]+)\}", stripped))

    events: list[tuple[int, str, str]] = []
    for m in begin_iter:
        events.append((m.start(), "begin", m.group(1)))
    for m in end_iter:
        events.append((m.start(), "end", m.group(1)))
    events.sort()

    stack: list[str] = []
    for _, kind, name in events:
        if kind == "begin":
            stack.append(name)
        else:
            if not stack:
                issues.append(f"\\end{{{name}}} without matching \\begin")
            elif stack[-1] != name:
                issues.append(f"Expected \\end{{{stack[-1]}}}, found \\end{{{name}}}")
                stack.pop()
            else:
                stack.pop()
    for leftover in stack:
        issues.append(f"Unclosed environment: {leftover}")

    # Undefined references
    refs = set(re.findall(r"\\ref\{([^}]+)\}", stripped))
    labels = set(re.findall(r"\\label\{([^}]+)\}", stripped))
    for r in sorted(refs - labels):
        issues.append(f"Undefined reference: {r}")

    return ValidationResult(path=str(path), valid=len(issues) == 0, issues=issues)


@mcp.tool()
async def get_latex_structure(
    file_path: str = Field(description="Path to the .tex file to analyze"),
) -> StructureInfo:
    """Extract document structure: class, title, author, packages, and section hierarchy."""
    path = get_safe_path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    content = path.read_text(encoding="utf-8")

    doc_class = None
    m = re.search(r"\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}", content)
    if m:
        doc_class = m.group(1)

    title = None
    m = re.search(r"\\title\{([^}]+)\}", content)
    if m:
        title = m.group(1)

    author = None
    m = re.search(r"\\author\{([^}]+)\}", content)
    if m:
        author = m.group(1)

    packages = list(dict.fromkeys(re.findall(r"\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}", content)))

    section_patterns = [
        (r"\\part\{([^}]+)\}", "Part"),
        (r"\\chapter\{([^}]+)\}", "Chapter"),
        (r"\\section\{([^}]+)\}", "Section"),
        (r"\\subsection\{([^}]+)\}", "Subsection"),
        (r"\\subsubsection\{([^}]+)\}", "Subsubsection"),
    ]
    sections: list[str] = []
    for pattern, level in section_patterns:
        for m in re.finditer(pattern, content):
            sections.append(f"{level}: {m.group(1)}")

    return StructureInfo(
        path=str(path),
        document_class=doc_class,
        title=title,
        author=author,
        packages=packages,
        sections=sections,
    )


@mcp.tool()
async def compile_latex(
    file_path: str = Field(description="Path to the .tex file to compile"),
    engine: Literal["pdflatex", "xelatex", "lualatex"] = "pdflatex",
    ctx: Context | None = None,
) -> CompileResult:
    """Compile a LaTeX file to PDF using the specified engine."""
    path = get_safe_path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    engine_path = shutil.which(engine)
    if not engine_path:
        raise RuntimeError(
            f"'{engine}' not found on PATH. Install a LaTeX distribution (TeX Live, MiKTeX)."
        )

    if ctx:
        await ctx.info(f"Compiling {path.name} with {engine}...")
        await ctx.report_progress(0, 2)

    # Run twice for references/TOC
    errors: list[str] = []
    log_output = ""
    for pass_num in range(1, 3):
        proc = await asyncio.create_subprocess_exec(
            engine_path,
            "-interaction=nonstopmode",
            "-halt-on-error",
            str(path.name),
            cwd=str(path.parent),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        log_output = stdout.decode("utf-8", errors="replace")

        if ctx:
            await ctx.report_progress(pass_num, 2)

        if proc.returncode != 0:
            # Extract error lines from log
            for line in log_output.splitlines():
                if line.startswith("!"):
                    errors.append(line)
            return CompileResult(
                path=str(path),
                success=False,
                log_output=log_output[-2000:],  # last 2000 chars
                errors=errors,
            )

    pdf_path = path.with_suffix(".pdf")
    return CompileResult(
        path=str(path),
        success=True,
        pdf_path=str(pdf_path) if pdf_path.exists() else None,
        log_output=log_output[-1000:],
    )


# --- Resources ---


@mcp.resource("latex://templates")
async def list_templates() -> str:
    """List available LaTeX templates."""
    templates = [f.stem.replace("_template", "") for f in TEMPLATES_DIR.glob("*_template.tex")]
    return "Available templates: " + ", ".join(sorted(templates))


@mcp.resource("latex://template/{name}")
async def get_template(name: str) -> str:
    """Get the content of a specific template."""
    template_file = TEMPLATES_DIR / f"{name}_template.tex"
    if not template_file.exists():
        raise ValueError(f"Template '{name}' not found")
    return template_file.read_text(encoding="utf-8")


# --- Entry Point ---

if __name__ == "__main__":
    mcp.run()
