"""Quality, log, spelling, and PDF helpers for the LaTeX MCP server.

External tools (chktex, latexindent, hunspell, aspell, pdftoppm) are optional;
the tools report availability clearly instead of failing the server. PDF reading
uses pypdf when installed.
"""

import asyncio
import os
import re
import shutil
import signal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

try:  # Optional dependency, used for PDF metadata and text extraction.
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - minimal installs
    PdfReader = None

try:  # Optional, used only to report rendered image dimensions.
    from PIL import Image as PILImage
except ImportError:  # pragma: no cover - minimal installs
    PILImage = None


class WordCountResult(BaseModel):
    """Visible-text statistics for a LaTeX file."""

    path: str
    words: int
    characters: int
    lines: int
    sections: int


class TodoItem(BaseModel):
    """One TODO/FIXME-style marker."""

    line: int
    kind: str
    text: str


class TodoResult(BaseModel):
    """All TODO-style markers in a file."""

    path: str
    items: list[TodoItem] = Field(default_factory=list)


class LintResult(BaseModel):
    """Result of chktex and built-in validation."""

    path: str
    tool: str = "chktex"
    available: bool = False
    issues: list[str] = Field(default_factory=list)
    output: str = ""
    message: str = ""


class FormatResult(BaseModel):
    """Result of latexindent formatting."""

    path: str
    tool: str = "latexindent"
    available: bool = False
    changed: bool = False
    content: str | None = None
    message: str = ""


class SpellCheckResult(BaseModel):
    """Result of a hunspell or aspell spelling pass."""

    path: str
    tool: str = ""
    available: bool = False
    misspelled: list[str] = Field(default_factory=list)
    message: str = ""


class LogAnalysisResult(BaseModel):
    """Structured analysis of a LaTeX .log file."""

    path: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    overfull: list[str] = Field(default_factory=list)
    underfull: list[str] = Field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0


class PdfInfoResult(BaseModel):
    """Basic PDF metadata and page count."""

    path: str
    success: bool
    pages: int = 0
    title: str | None = None
    author: str | None = None
    subject: str | None = None
    creator: str | None = None
    producer: str | None = None
    encrypted: bool = False
    size_bytes: int = 0
    text_preview: str = ""
    message: str = ""


class PdfTextResult(BaseModel):
    """Extracted PDF text."""

    path: str
    success: bool
    pages: int = 0
    text: str = ""
    truncated: bool = False
    message: str = ""


class PdfRenderResult(BaseModel):
    """Rendered PDF page image."""

    path: str
    success: bool
    page: int = 1
    image_path: str | None = None
    width: int | None = None
    height: int | None = None
    message: str = ""


_TODO_RE = re.compile(
    r"\b(TODO|FIXME|XXX|HACK|NOTE)\b\s*[:\-]?\s*(.*)", re.IGNORECASE
)
_WORD_RE = re.compile(r"\b[\w'’\-]+\b", re.UNICODE)


def _server():
    from . import server

    return server


def _resolve_pdf(file_path: str) -> Path:
    server = _server()
    path = server.get_safe_path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"Not a file: {path}")
    return path


async def _run_tool(
    command: list[str],
    cwd: Path,
    timeout: int,
    stdin_text: str | None = None,
) -> tuple[int, str, str]:
    """Run an optional helper process with a timeout and clean shutdown."""
    options: dict[str, Any] = {
        "cwd": str(cwd),
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if stdin_text is not None:
        options["stdin"] = asyncio.subprocess.PIPE
    if os.name != "nt":
        options["start_new_session"] = True

    process = await asyncio.create_subprocess_exec(*command, **options)
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(stdin_text.encode("utf-8") if stdin_text else None),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        killed = False
        if os.name != "nt":
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                killed = True
            except (ProcessLookupError, PermissionError):
                pass
        if not killed:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass
        return -1, "", f"Command timed out after {timeout} second(s)"

    return (
        process.returncode if process.returncode is not None else -1,
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
    )


def word_count(file_path: str) -> WordCountResult:
    """Count visible words, characters, lines, and headings."""
    server = _server()
    path = server.get_safe_path(file_path)
    server._require_file(path)
    content = server._read_text_file(path)
    analysis = server._prepare_analysis(content)
    sections = len(list(server._SECTION_RE.finditer(analysis)))
    text = re.sub(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?", " ", analysis)
    text = re.sub(r"[{}$&#^_~%]", " ", text)
    words = _WORD_RE.findall(text)
    return WordCountResult(
        path=str(path),
        words=len(words),
        characters=len(content),
        lines=content.count("\n") + 1,
        sections=sections,
    )


def find_todos(file_path: str) -> TodoResult:
    """Find TODO/FIXME/XXX/HACK/NOTE markers with line numbers."""
    server = _server()
    path = server.get_safe_path(file_path)
    server._require_file(path)
    content = server._read_text_file(path)
    items: list[TodoItem] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        match = _TODO_RE.search(line)
        if match:
            items.append(
                TodoItem(
                    line=line_number,
                    kind=match.group(1).upper(),
                    text=match.group(2).strip(),
                )
            )
    return TodoResult(path=str(path), items=items)


def analyze_log(file_path: str) -> LogAnalysisResult:
    """Parse errors, warnings, and bad box reports from a .log file."""
    server = _server()
    path = server.get_safe_path(file_path)
    server._require_file(path)
    content = server._read_text_file(path)
    errors, warnings = server._parse_latex_log(content)
    overfull: list[str] = []
    underfull: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith(("Overfull \\hbox", "Overfull \\vbox")):
            overfull.append(stripped)
        elif stripped.startswith(("Underfull \\hbox", "Underfull \\vbox")):
            underfull.append(stripped)
    return LogAnalysisResult(
        path=str(path),
        errors=errors,
        warnings=warnings,
        overfull=overfull[:100],
        underfull=underfull[:100],
        error_count=len(errors),
        warning_count=len(warnings),
    )


async def lint_latex(file_path: str, timeout_seconds: int = 60) -> LintResult:
    """Run chktex when available, always including built-in validation issues."""
    server = _server()
    path = server.get_safe_path(file_path)
    server._require_file(path)
    content = server._read_text_file(path)
    builtin = server._validate_text(content, check_references=False)
    chktex = shutil.which("chktex")
    if not chktex:
        message = "chktex not found; returning built-in validation issues only"
        return LintResult(
            path=str(path),
            available=False,
            issues=builtin,
            message=message,
        )

    returncode, stdout, stderr = await _run_tool(
        [chktex, "-q", "-v0", path.name],
        path.parent,
        max(1, int(timeout_seconds)),
    )
    external = [
        line.strip()
        for line in stdout.splitlines()
        if line.strip() and not line.startswith("ChkTeX")
    ]
    output = (stdout + stderr).strip()
    return LintResult(
        path=str(path),
        available=True,
        issues=list(dict.fromkeys(builtin + external)),
        output=output,
        message=f"chktex exited with code {returncode}",
    )


async def format_latex(
    file_path: str,
    in_place: bool = False,
    timeout_seconds: int = 60,
    snapshot: bool = True,
) -> FormatResult:
    """Run latexindent when available and optionally write the result back."""
    server = _server()
    path = server.get_safe_path(file_path)
    server._require_file(path)
    content = server._read_text_file(path)
    latexindent = shutil.which("latexindent")
    if not latexindent:
        return FormatResult(
            path=str(path),
            available=False,
            content=content,
            message="latexindent not found",
        )

    returncode, stdout, stderr = await _run_tool(
        [latexindent, "-s", path.name],
        path.parent,
        max(1, int(timeout_seconds)),
    )
    if returncode != 0:
        return FormatResult(
            path=str(path),
            available=True,
            content=content,
            message=f"latexindent exited with code {returncode}: {stderr.strip()[-500:]}",
        )
    formatted = stdout if stdout.strip() else content
    changed = formatted != content
    if in_place and changed:
        if snapshot:
            try:
                from .authoring import create_snapshot

                create_snapshot(file_path, label="before latexindent")
            except Exception:
                pass
        server._atomic_write_text(path, formatted)
    return FormatResult(
        path=str(path),
        available=True,
        changed=changed,
        content=formatted,
        message=(
            f"Formatted content {'written' if in_place else 'returned'}"
            if changed
            else "No formatting changes"
        ),
    )


async def spell_check(
    file_path: str, language: str = "en_US", timeout_seconds: int = 60
) -> SpellCheckResult:
    """Run hunspell or aspell against visible prose when available."""
    server = _server()
    path = server.get_safe_path(file_path)
    server._require_file(path)
    content = server._read_text_file(path)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", language):
        raise ValueError(f"Invalid spelling language: {language!r}")

    text = server._prepare_analysis(content)
    text = re.sub(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?", " ", text)
    words = sorted({word for word in _WORD_RE.findall(text) if len(word) > 1})
    if not words:
        return SpellCheckResult(path=str(path), available=True, message="No words to check")

    hunspell = shutil.which("hunspell")
    aspell = shutil.which("aspell")
    if hunspell:
        command = [hunspell, "-l", "-d", language]
        tool = "hunspell"
    elif aspell:
        command = [aspell, "list", f"--lang={language}"]
        tool = "aspell"
    else:
        return SpellCheckResult(
            path=str(path),
            available=False,
            message="hunspell and aspell are not installed",
        )

    returncode, stdout, stderr = await _run_tool(
        command,
        path.parent,
        max(1, int(timeout_seconds)),
        stdin_text="\n".join(words) + "\n",
    )
    misspelled = list(dict.fromkeys(line.strip() for line in stdout.splitlines() if line.strip()))
    message = f"{tool} exited with code {returncode}"
    if stderr.strip() and returncode != 0:
        message += f": {stderr.strip()[-500:]}"
    return SpellCheckResult(
        path=str(path),
        tool=tool,
        available=True,
        misspelled=misspelled[:1000],
        message=message,
    )


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:  # pragma: no cover - file vanished
        return 0


def pdf_info(file_path: str, text_preview_chars: int = 500) -> PdfInfoResult:
    """Read PDF metadata, page count, and a short text preview."""
    path = _resolve_pdf(file_path)
    if PdfReader is None:
        return PdfInfoResult(
            path=str(path),
            success=False,
            size_bytes=_file_size(path),
            message="pypdf is not installed; install it with: pip install pypdf",
        )
    try:
        reader = PdfReader(str(path))
        encrypted = bool(reader.is_encrypted)
        if encrypted:
            try:
                reader.decrypt("")
            except Exception:
                pass
        pages = len(reader.pages)
        metadata = reader.metadata or {}
        preview = ""
        if pages and text_preview_chars > 0:
            try:
                preview = (reader.pages[0].extract_text() or "")[:text_preview_chars]
            except Exception:
                preview = ""
        return PdfInfoResult(
            path=str(path),
            success=True,
            pages=pages,
            title=str(metadata.get("/Title")) if metadata.get("/Title") else None,
            author=str(metadata.get("/Author")) if metadata.get("/Author") else None,
            subject=str(metadata.get("/Subject")) if metadata.get("/Subject") else None,
            creator=str(metadata.get("/Creator")) if metadata.get("/Creator") else None,
            producer=str(metadata.get("/Producer")) if metadata.get("/Producer") else None,
            encrypted=encrypted,
            size_bytes=_file_size(path),
            text_preview=preview,
        )
    except Exception as exc:
        return PdfInfoResult(
            path=str(path),
            success=False,
            size_bytes=_file_size(path),
            message=f"Could not read PDF: {exc}",
        )


def pdf_text(
    file_path: str,
    page_start: int = 1,
    page_end: int | None = None,
    max_chars: int = 50_000,
) -> PdfTextResult:
    """Extract text from a page range of a PDF."""
    path = _resolve_pdf(file_path)
    if PdfReader is None:
        return PdfTextResult(
            path=str(path), success=False, message="pypdf is not installed; install it with: pip install pypdf"
        )
    try:
        reader = PdfReader(str(path))
        pages = len(reader.pages)
        if pages == 0:
            return PdfTextResult(path=str(path), success=True, pages=0, message="PDF has no pages")
        start = max(1, int(page_start))
        end = pages if page_end is None else min(pages, int(page_end))
        if start > end:
            raise ValueError(f"Invalid page range: {start}..{end}")

        chunks: list[str] = []
        for page_number in range(start, end + 1):
            text = reader.pages[page_number - 1].extract_text() or ""
            chunks.append(f"--- page {page_number} ---\n{text}")
        combined = "\n".join(chunks)
        limit = max(1, int(max_chars))
        truncated = len(combined) > limit
        return PdfTextResult(
            path=str(path),
            success=True,
            pages=pages,
            text=combined[:limit],
            truncated=truncated,
        )
    except Exception as exc:
        return PdfTextResult(path=str(path), success=False, message=f"Could not extract text: {exc}")


async def render_pdf_page(
    file_path: str,
    page: int = 1,
    dpi: int = 150,
    timeout_seconds: int = 60,
) -> PdfRenderResult:
    """Render one PDF page to PNG using pdftoppm when available."""
    path = _resolve_pdf(file_path)
    page = max(1, int(page))
    dpi = min(600, max(36, int(dpi)))
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        return PdfRenderResult(
            path=str(path),
            success=False,
            page=page,
            message="pdftoppm is not installed (install poppler-utils)",
        )

    prefix = path.parent / f"{path.stem}-page-{page}"
    image_path = prefix.with_name(prefix.name + ".png")
    returncode, stdout, stderr = await _run_tool(
        [
            pdftoppm,
            "-png",
            "-r",
            str(dpi),
            "-f",
            str(page),
            "-l",
            str(page),
            "-singlefile",
            str(path),
            str(prefix),
        ],
        path.parent,
        max(1, int(timeout_seconds)),
    )
    if returncode != 0 or not image_path.is_file():
        return PdfRenderResult(
            path=str(path),
            success=False,
            page=page,
            message=f"pdftoppm failed with code {returncode}: {stderr.strip()[-500:]}",
        )

    width = height = None
    if PILImage is not None:
        try:
            with PILImage.open(image_path) as image:
                width, height = image.size
        except Exception:
            pass
    return PdfRenderResult(
        path=str(path),
        success=True,
        page=page,
        image_path=str(image_path),
        width=width,
        height=height,
        message="Page rendered",
    )
