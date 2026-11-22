"""Multi-file LaTeX project analysis and compilation.

The module is imported by latex_server; it imports server helpers lazily inside
functions so that the tool registration module remains the single owner of the
base path, path-security policy, and analysis routines.
"""

import asyncio
import os
import re
import shutil
import signal
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class ProjectFileInfo(BaseModel):
    """One source file in a project graph."""

    path: str
    role: Literal["main", "included"] = "included"
    size_bytes: int = 0
    includes: list[str] = Field(default_factory=list)


class ProjectIssue(BaseModel):
    """A project-level problem."""

    file: str | None = None
    line: int | None = None
    message: str
    severity: Literal["error", "warning"] = "error"


class ProjectInfo(BaseModel):
    """The dependency graph and cross-file metadata for a project."""

    main_file: str
    files: list[ProjectFileInfo] = Field(default_factory=list)
    missing_files: list[str] = Field(default_factory=list)
    labels: dict[str, list[str]] = Field(default_factory=dict)
    references: dict[str, list[str]] = Field(default_factory=dict)
    undefined_references: list[str] = Field(default_factory=list)
    duplicate_labels: dict[str, list[str]] = Field(default_factory=dict)
    bibliography_files: list[str] = Field(default_factory=list)
    bibliography_tool: str | None = None
    issues: list[ProjectIssue] = Field(default_factory=list)


class ProjectValidationResult(BaseModel):
    """Validation summary for a whole project."""

    main_file: str
    valid: bool
    issues: list[ProjectIssue] = Field(default_factory=list)


class ProjectCompileResult(BaseModel):
    """Result of compiling a project's main file."""

    main_file: str
    success: bool
    pdf_path: str | None = None
    log_path: str | None = None
    log_output: str = ""
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    project_files: list[str] = Field(default_factory=list)
    bibliography_tool: str | None = None
    bibliography_runs: int = 0
    engine_passes: int = 0
    compiler: str = "engine"
    duration_seconds: float | None = None


_INCLUDE_RE = re.compile(r"\\(?:input|include|subfile)\s*\{([^{}]+)\}")
_IMPORT_RE = re.compile(
    r"\\(?:import|subimport|inputfrom|includefrom)\s*\{([^{}]+)\}\s*\{([^{}]+)\}"
)
_BIBLIOGRAPHY_RE = re.compile(r"\\bibliography\s*\{([^{}]+)\}")
_ADDBIBRESOURCE_RE = re.compile(
    r"\\addbibresource\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}"
)
_BIBLATEX_RE = re.compile(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{([^{}]*biblatex[^{}]*)\}")
_BIBLATEX_BACKEND_BIBTEX_RE = re.compile(
    r"\\usepackage\s*\[[^\]]*backend\s*=\s*bibtex", re.IGNORECASE
)
_REFERENCE_RE = re.compile(
    r"\\(?:ref|eqref|pageref|autoref|nameref|vref|cref|Cref)"
    r"(?![A-Za-z])\s*\{([^{}]+)\}"
)
_LABEL_RE = re.compile(r"\\label\s*\{([^{}]+)\}")
_LINE_ISSUE_RE = re.compile(r"^Line (\d+): (.*)$")

MAX_PROJECT_FILES = 500
MAX_INCLUDE_DEPTH = 50


def _server():
    """Import the server module lazily to avoid a circular import."""
    from . import server

    return server


def _relative(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _issue_from_message(message: str, file: str | None = None) -> ProjectIssue:
    match = _LINE_ISSUE_RE.match(message)
    if match:
        return ProjectIssue(file=file, line=int(match.group(1)), message=match.group(2))
    return ProjectIssue(file=file, message=message)


def _validation_issues(content: str, file: str, *, main_file: bool) -> list[ProjectIssue]:
    server = _server()
    if main_file:
        messages = server._validate_text(content, check_references=False)
        return [_issue_from_message(message, file) for message in messages]

    analysis = server._prepare_analysis(content)
    issues: list[ProjectIssue] = []
    opens, closes = server._count_braces(analysis)
    if opens != closes:
        issues.append(
            ProjectIssue(
                file=file,
                message=f"Unbalanced braces: {opens} opening vs {closes} closing",
            )
        )
    environment_issues: list[str] = []
    server._check_environments(analysis, environment_issues)
    for message in environment_issues:
        issues.append(_issue_from_message(message, file))
    return issues


def _include_targets(content: str) -> list[tuple[str, str | None, int]]:
    """Return (target, import_directory, line) entries from a source file."""
    server = _server()
    analysis = server._prepare_analysis(content)
    targets: list[tuple[str, str | None, int]] = []
    for match in _IMPORT_RE.finditer(analysis):
        targets.append(
            (
                match.group(2).strip(),
                match.group(1).strip(),
                server._line_number(analysis, match.start()),
            )
        )
    for match in _INCLUDE_RE.finditer(analysis):
        targets.append(
            (match.group(1).strip(), None, server._line_number(analysis, match.start()))
        )
    return targets


def _resolve_include(
    source: Path, target: str, import_directory: str | None
) -> tuple[Path | None, str | None]:
    """Resolve an include target, returning (path, error message)."""
    server = _server()
    if not target:
        return None, "Empty include target"
    if any(character in target for character in "\\#{}$"):
        return None, f"Dynamic include target is not resolved: {target}"

    target_path = Path(target)
    if import_directory:
        target_path = Path(import_directory) / target_path
    base = target_path if target_path.is_absolute() else source.parent / target_path

    candidates = [base]
    if base.suffix.lower() != ".tex":
        # \include appends .tex to the given name; never replace an existing
        # non-.tex suffix such as "chapter.v2".
        candidates.append(base.with_name(base.name + ".tex"))

    last_error: str | None = None
    for candidate in candidates:
        try:
            safe = server.get_safe_path(str(candidate))
        except ValueError as exc:
            last_error = str(exc)
            continue
        if safe.is_file():
            return safe, None
    if last_error:
        return None, last_error
    return None, f"Included file not found: {target}"


def _collect_bibliography(
    content: str, source: Path, main_dir: Path
) -> tuple[list[Path], str | None]:
    """Find bibliography resources and decide whether bibtex or biber is needed."""
    server = _server()
    analysis = server._prepare_analysis(content)
    resources: list[Path] = []
    tool: str | None = None

    for match in _ADDBIBRESOURCE_RE.finditer(analysis):
        raw = match.group(1).strip()
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = main_dir / candidate
        resources.append(candidate)
        tool = "biber"

    for match in _BIBLIOGRAPHY_RE.finditer(analysis):
        for raw in match.group(1).split(","):
            raw = raw.strip()
            if not raw:
                continue
            candidate = Path(raw)
            if candidate.suffix.lower() != ".bib":
                candidate = candidate.with_suffix(".bib")
            if not candidate.is_absolute():
                candidate = main_dir / candidate
            resources.append(candidate)
            if tool is None:
                tool = "bibtex"

    if tool == "biber" and _BIBLATEX_BACKEND_BIBTEX_RE.search(analysis):
        tool = "bibtex"

    if tool is None and _BIBLATEX_RE.search(analysis):
        tool = "bibtex" if _BIBLATEX_BACKEND_BIBTEX_RE.search(analysis) else "biber"

    return resources, tool


def analyze_project(file_path: str) -> ProjectInfo:
    """Analyze a main .tex file and recursively resolve its includes."""
    server = _server()
    main_path = server.get_safe_path(file_path)
    server._require_tex_suffix(main_path)
    server._require_file(main_path)
    base = server.BASE_PATH
    main_relative = _relative(main_path, base)

    files: list[ProjectFileInfo] = []
    missing_files: list[str] = []
    issues: list[ProjectIssue] = []
    labels: dict[str, list[str]] = {}
    references: dict[str, list[str]] = {}
    bibliography_paths: list[Path] = []
    bibliography_tool: str | None = None

    queue: list[tuple[Path, Literal["main", "included"], int]] = [
        (main_path, "main", 0)
    ]
    visited: set[Path] = set()

    while queue:
        path, role, depth = queue.pop(0)
        if path in visited:
            continue
        if len(files) >= MAX_PROJECT_FILES:
            issues.append(
                ProjectIssue(
                    file=main_relative,
                    message=f"Project exceeds {MAX_PROJECT_FILES} files; analysis stopped",
                    severity="warning",
                )
            )
            break
        if depth > MAX_INCLUDE_DEPTH:
            issues.append(
                ProjectIssue(
                    file=_relative(path, base),
                    message=f"Include depth exceeds {MAX_INCLUDE_DEPTH}; branch skipped",
                    severity="warning",
                )
            )
            continue

        visited.add(path)
        relative = _relative(path, base)
        try:
            content = server._read_text_file(path)
        except (OSError, ValueError) as exc:
            issues.append(ProjectIssue(file=relative, message=str(exc)))
            continue

        file_info = ProjectFileInfo(
            path=relative,
            role=role,
            size_bytes=path.stat().st_size,
        )

        issues.extend(_validation_issues(content, relative, main_file=(role == "main")))

        analysis = server._prepare_analysis(content)
        for match in _LABEL_RE.finditer(analysis):
            for label in match.group(1).split(","):
                label = label.strip()
                if not label:
                    continue
                location = f"{relative}:{server._line_number(analysis, match.start())}"
                labels.setdefault(label, []).append(location)

        for match in _REFERENCE_RE.finditer(analysis):
            for reference in match.group(1).split(","):
                reference = reference.strip()
                if not reference:
                    continue
                location = f"{relative}:{server._line_number(analysis, match.start())}"
                references.setdefault(reference, []).append(location)

        for target, import_directory, line in _include_targets(content):
            resolved, error = _resolve_include(path, target, import_directory)
            if error or resolved is None:
                message = error or f"Included file not found: {target}"
                dynamic = message.startswith("Dynamic include target")
                issues.append(
                    ProjectIssue(
                        file=relative,
                        line=line,
                        message=message,
                        severity="warning" if dynamic else "error",
                    )
                )
                if not dynamic:
                    missing_files.append(target)
                file_info.includes.append(target)
                continue

            included_relative = _relative(resolved, base)
            file_info.includes.append(included_relative)
            if resolved in visited:
                continue
            queue.append((resolved, "included", depth + 1))

        resources, resource_tool = _collect_bibliography(content, path, main_path.parent)
        bibliography_paths.extend(resources)
        if resource_tool == "biber":
            bibliography_tool = "biber"
        elif resource_tool and bibliography_tool is None:
            bibliography_tool = "bibtex"

        files.append(file_info)

    bibliography_files: list[str] = []
    seen_bibliography: set[Path] = set()
    for raw_path in bibliography_paths:
        try:
            safe = server.get_safe_path(str(raw_path))
        except ValueError as exc:
            issues.append(ProjectIssue(message=str(exc), severity="warning"))
            continue
        if safe in seen_bibliography:
            continue
        seen_bibliography.add(safe)
        relative_bib = _relative(safe, base)
        bibliography_files.append(relative_bib)
        if not safe.is_file():
            issues.append(
                ProjectIssue(
                    file=relative_bib,
                    message=f"Bibliography file not found: {relative_bib}",
                    severity="warning",
                )
            )

    duplicate_labels = {
        label: locations for label, locations in labels.items() if len(locations) > 1
    }
    for label, locations in sorted(duplicate_labels.items()):
        issues.append(
            ProjectIssue(
                message=f"Duplicate label '{label}' at {', '.join(locations)}",
            )
        )

    undefined_references = sorted(set(references) - set(labels))
    for reference in undefined_references:
        issues.append(
            ProjectIssue(
                message=f"Undefined reference: {reference} "
                f"({', '.join(references[reference])})",
            )
        )

    return ProjectInfo(
        main_file=main_relative,
        files=files,
        missing_files=missing_files,
        labels=labels,
        references=references,
        undefined_references=undefined_references,
        duplicate_labels=duplicate_labels,
        bibliography_files=bibliography_files,
        bibliography_tool=bibliography_tool,
        issues=issues,
    )


def validate_project(file_path: str, check_references: bool = True) -> ProjectValidationResult:
    """Validate a whole project, optionally ignoring unresolved references."""
    info = analyze_project(file_path)
    issues = list(info.issues)
    if not check_references:
        issues = [
            issue
            for issue in issues
            if not issue.message.startswith("Undefined reference:")
        ]
    valid = not any(issue.severity == "error" for issue in issues)
    return ProjectValidationResult(
        main_file=info.main_file,
        valid=valid,
        issues=issues,
    )


async def _run_process(
    command: list[str], cwd: Path, timeout: int
) -> tuple[int, str, str]:
    """Run a helper process with a timeout and process-group cleanup."""
    options: dict[str, Any] = {
        "cwd": str(cwd),
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if os.name != "nt":
        options["start_new_session"] = True

    process = await asyncio.create_subprocess_exec(*command, **options)
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
        return -1, "", f"Command timed out after {timeout} second(s): {' '.join(command)}"

    return (
        process.returncode if process.returncode is not None else -1,
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
    )


def _clean_auxiliary(main_path: Path) -> None:
    server = _server()
    for auxiliary in server._auxiliary_paths(main_path):
        auxiliary.unlink(missing_ok=True)


async def _compile_with_latexmk(
    main_path: Path,
    engine: str,
    timeout: int,
    shell_escape: bool,
    clean_aux: bool,
) -> ProjectCompileResult | None:
    """Compile with latexmk when available; return None to request fallback."""
    server = _server()
    latexmk = shutil.which("latexmk")
    if not latexmk:
        return None

    engine_flag = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}[engine]
    command = [
        latexmk,
        engine_flag,
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
        "-shell-escape" if shell_escape else "-no-shell-escape",
        main_path.name,
    ]
    started = time.monotonic()
    returncode, stdout, stderr = await _run_process(command, main_path.parent, timeout)
    log_path = main_path.with_suffix(".log")
    log_text = server._read_log_file(log_path, stdout)
    errors, warnings = server._parse_latex_log(log_text + "\n" + stderr)
    pdf_path = main_path.with_suffix(".pdf")
    success = returncode == 0 and pdf_path.is_file()
    if not success and not errors:
        errors = [f"latexmk failed with exit code {returncode}"]
    log_path_value = str(log_path) if log_path.is_file() else None
    if clean_aux and success:
        _clean_auxiliary(main_path)
        log_path_value = None
    return ProjectCompileResult(
        main_file=_relative(main_path, server.BASE_PATH),
        success=success,
        pdf_path=str(pdf_path) if success else None,
        log_path=log_path_value,
        log_output=log_text[-server.MAX_LOG_OUTPUT_CHARS :],
        errors=errors,
        warnings=warnings,
        project_files=[],
        bibliography_tool=None,
        bibliography_runs=0,
        engine_passes=0,
        compiler="latexmk",
        duration_seconds=round(time.monotonic() - started, 3),
    )


async def compile_project(
    file_path: str,
    engine: str = "pdflatex",
    timeout_seconds: int = 120,
    clean_aux: bool = False,
    shell_escape: bool = False,
    run_bibliography: bool = True,
    use_latexmk: bool = False,
) -> ProjectCompileResult:
    """Compile a project main file, including bibliography and reruns."""
    if engine not in ("pdflatex", "xelatex", "lualatex"):
        raise ValueError(
            f"Unsupported engine: {engine!r}. Choose pdflatex, xelatex, or lualatex."
        )
    server = _server()
    main_path = server.get_safe_path(file_path)
    info = analyze_project(file_path)
    project_files = [item.path for item in info.files]
    started = time.monotonic()
    timeout = max(1, int(timeout_seconds))

    if info.missing_files:
        return ProjectCompileResult(
            main_file=info.main_file,
            success=False,
            errors=[f"Missing included file(s): {', '.join(info.missing_files)}"],
            project_files=project_files,
            duration_seconds=round(time.monotonic() - started, 3),
        )

    if use_latexmk:
        latexmk_result = await _compile_with_latexmk(
            main_path, engine, timeout, shell_escape, clean_aux
        )
        if latexmk_result is not None:
            latexmk_result.project_files = project_files
            latexmk_result.bibliography_tool = info.bibliography_tool
            latexmk_result.warnings = list(
                dict.fromkeys(
                    [
                        issue.message
                        for issue in info.issues
                        if issue.severity == "warning"
                    ]
                    + latexmk_result.warnings
                )
            )
            latexmk_result.duration_seconds = round(time.monotonic() - started, 3)
            return latexmk_result

    first = await server.compile_latex(
        str(main_path),
        engine=engine,
        timeout_seconds=timeout,
        clean_aux=clean_aux and not (run_bibliography and info.bibliography_tool),
        shell_escape=shell_escape,
    )
    total_passes = first.passes
    warnings = [
        issue.message
        for issue in info.issues
        if issue.severity == "warning"
    ]
    warnings.extend(first.warnings)
    warnings = list(dict.fromkeys(warnings))
    if use_latexmk:
        warnings.append("latexmk not found; fell back to direct engine passes")

    if not first.success:
        return ProjectCompileResult(
            main_file=info.main_file,
            success=False,
            pdf_path=None,
            log_path=first.log_path,
            log_output=first.log_output,
            errors=first.errors,
            warnings=warnings,
            project_files=project_files,
            engine_passes=total_passes,
            compiler="engine",
            duration_seconds=round(time.monotonic() - started, 3),
        )

    bibliography_runs = 0
    if run_bibliography and info.bibliography_tool:
        tool = shutil.which(info.bibliography_tool)
        if tool is None:
            warnings.append(
                f"{info.bibliography_tool} not found; citations may be unresolved"
            )
        else:
            returncode, stdout, stderr = await _run_process(
                [tool, main_path.stem], main_path.parent, timeout
            )
            bibliography_runs = 1
            combined_output = (stdout + "\n" + stderr).strip()
            if returncode != 0:
                warnings.append(
                    f"{info.bibliography_tool} exited with code {returncode}"
                    + (f": {combined_output[-500:]}" if combined_output else "")
                )

    final = first
    if bibliography_runs:
        final = await server.compile_latex(
            str(main_path),
            engine=engine,
            timeout_seconds=timeout,
            clean_aux=clean_aux,
            shell_escape=shell_escape,
        )
        total_passes += final.passes
        warnings = list(dict.fromkeys(warnings + final.warnings))
        if not final.success:
            return ProjectCompileResult(
                main_file=info.main_file,
                success=False,
                pdf_path=None,
                log_path=final.log_path,
                log_output=final.log_output,
                errors=final.errors,
                warnings=warnings,
                project_files=project_files,
                bibliography_tool=info.bibliography_tool,
                bibliography_runs=bibliography_runs,
                engine_passes=total_passes,
                compiler="engine",
                duration_seconds=round(time.monotonic() - started, 3),
            )
    elif clean_aux and first.success:
        _clean_auxiliary(main_path)

    log_path = final.log_path
    if clean_aux:
        log_path = None

    return ProjectCompileResult(
        main_file=info.main_file,
        success=final.success,
        pdf_path=final.pdf_path,
        log_path=log_path,
        log_output=final.log_output,
        errors=final.errors,
        warnings=warnings,
        project_files=project_files,
        bibliography_tool=info.bibliography_tool,
        bibliography_runs=bibliography_runs,
        engine_passes=total_passes,
        compiler="engine",
        duration_seconds=round(time.monotonic() - started, 3),
    )
