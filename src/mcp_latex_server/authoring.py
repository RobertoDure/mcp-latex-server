"""Authoring features: transactions, snapshots, section edits, and builders.

All functions resolve paths through latex_server so the configured base path and
path-security policy remain authoritative. The server module is imported lazily
inside functions to keep tool registration acyclic.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class SnapshotInfo(BaseModel):
    """One stored snapshot."""

    id: str
    source: str
    created_at: str
    size_bytes: int
    label: str | None = None


class SnapshotListResult(BaseModel):
    """Snapshots for one source file."""

    source: str
    directory: str
    snapshots: list[SnapshotInfo] = Field(default_factory=list)


class EditSpec(BaseModel):
    """One operation in a transactional edit batch."""

    operation: Literal["replace", "insert_before", "insert_after", "append", "prepend"]
    new_text: str
    search_text: str | None = None
    line_number: int | None = None
    replace_all: bool = False


class AuthoringResult(BaseModel):
    """Result of an authoring operation."""

    path: str
    success: bool
    message: str
    applied: int = 0
    snapshot: SnapshotInfo | None = None
    content: str | None = None
    issues: list[str] = Field(default_factory=list)


_SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_LABEL_RE = re.compile(r"^[^,{}\s]+$")
_CITATION_KEY_RE = re.compile(r"^[A-Za-z0-9:._-]+$")
_LEVEL_ORDER = {
    "part": 0,
    "chapter": 1,
    "section": 2,
    "subsection": 3,
    "subsubsection": 4,
    "paragraph": 5,
    "subparagraph": 6,
}


def _server():
    from . import server

    return server


def _relative(path: Path) -> str:
    server = _server()
    try:
        return str(path.relative_to(server.BASE_PATH))
    except ValueError:
        return str(path)


def _snapshot_root() -> Path:
    server = _server()
    raw = os.environ.get("LATEX_SERVER_SNAPSHOT_DIR", "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = server.BASE_PATH / candidate
        root = server.get_safe_path(str(candidate))
    else:
        root = server.get_safe_path(
            str(server.BASE_PATH / ".latex-mcp" / "snapshots")
        )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _snapshot_metadata_path(root: Path, snapshot_id: str) -> Path:
    return root / f"{snapshot_id}.json"


def _snapshot_content_path(root: Path, snapshot_id: str) -> Path:
    return root / f"{snapshot_id}.tex.snap"


def create_snapshot(file_path: str, label: str | None = None) -> SnapshotInfo:
    """Store an immutable copy of the current file content."""
    server = _server()
    path = server.get_safe_path(file_path)
    server._require_file(path)
    content = server._read_text_file(path)

    root = _snapshot_root()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    snapshot_id = f"{timestamp}-{uuid.uuid4().hex[:8]}"
    source = _relative(path)
    created_at = datetime.now(timezone.utc).isoformat()

    server._atomic_write_text(
        _snapshot_content_path(root, snapshot_id), content
    )
    metadata = {
        "id": snapshot_id,
        "source": source,
        "created_at": created_at,
        "size_bytes": len(content.encode("utf-8")),
        "label": label,
    }
    server._atomic_write_text(
        _snapshot_metadata_path(root, snapshot_id),
        json.dumps(metadata, indent=2) + "\n",
    )
    return SnapshotInfo(**metadata)


def list_snapshots(file_path: str, limit: int = 100) -> SnapshotListResult:
    """List snapshots for one source file, newest first."""
    server = _server()
    path = server.get_safe_path(file_path)
    root = _snapshot_root()
    source = _relative(path)

    snapshots: list[SnapshotInfo] = []
    metadata_files = sorted(root.glob("*.json"), reverse=True)
    for metadata_path in metadata_files:
        if len(snapshots) >= max(1, int(limit)):
            break
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("source") != source:
            continue
        try:
            snapshots.append(SnapshotInfo(**payload))
        except Exception:
            continue

    return SnapshotListResult(
        source=source,
        directory=str(root),
        snapshots=snapshots,
    )


def _load_snapshot(snapshot_id: str) -> tuple[SnapshotInfo, str]:
    server = _server()
    if not _SNAPSHOT_ID_RE.fullmatch(snapshot_id or ""):
        raise ValueError(f"Invalid snapshot id: {snapshot_id!r}")
    root = _snapshot_root()
    metadata_path = _snapshot_metadata_path(root, snapshot_id)
    content_path = _snapshot_content_path(root, snapshot_id)
    if not metadata_path.is_file() or not content_path.is_file():
        raise FileNotFoundError(f"Snapshot not found: {snapshot_id}")
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    info = SnapshotInfo(**payload)
    content = server._read_text_file(content_path)
    return info, content


def restore_snapshot(
    file_path: str, snapshot_id: str, backup_current: bool = True
) -> AuthoringResult:
    """Restore a snapshot into its original file."""
    server = _server()
    path = server.get_safe_path(file_path)
    if path.exists() and path.is_dir():
        raise IsADirectoryError(f"Not a file: {path}")
    info, content = _load_snapshot(snapshot_id)
    expected_source = _relative(path)
    if info.source != expected_source:
        raise ValueError(
            f"Snapshot {snapshot_id} belongs to {info.source}, not {expected_source}"
        )

    backup = None
    if backup_current and path.is_file():
        backup = create_snapshot(file_path, label=f"before restore {snapshot_id}")

    server._atomic_write_text(path, content)
    return AuthoringResult(
        path=str(path),
        success=True,
        message=f"Restored snapshot {snapshot_id}",
        applied=1,
        snapshot=backup,
        content=content,
    )


def apply_edits(
    file_path: str,
    edits: list[EditSpec],
    snapshot: bool = True,
    validate_after: bool = False,
) -> AuthoringResult:
    """Apply a batch of edits transactionally.

    All edits are applied in memory first. The file is written only if every
    edit succeeds, so a failing edit cannot leave a partially edited file.
    """
    server = _server()
    path = server.get_safe_path(file_path)
    server._require_file(path)
    content = server._read_text_file(path)

    if not edits:
        return AuthoringResult(
            path=str(path),
            success=True,
            message="No edits supplied",
            applied=0,
            content=content,
        )

    saved = create_snapshot(file_path, label="before transaction") if snapshot else None
    working = content
    for index, spec in enumerate(edits, start=1):
        try:
            working, _message = server._apply_edit_to_text(
                working,
                spec.operation,
                spec.new_text,
                search_text=spec.search_text,
                line_number=spec.line_number,
                replace_all=spec.replace_all,
            )
        except Exception as exc:
            raise ValueError(
                f"Edit {index} failed; no changes were written: {exc}"
            ) from exc

    issues = (
        server._validate_text(working, check_references=False)
        if validate_after
        else []
    )
    if validate_after and issues:
        return AuthoringResult(
            path=str(path),
            success=False,
            message="Edits rejected because validation failed",
            applied=0,
            snapshot=saved,
            content=working,
            issues=issues,
        )

    server._atomic_write_text(path, working)
    return AuthoringResult(
        path=str(path),
        success=True,
        message=f"Applied {len(edits)} edit(s)",
        applied=len(edits),
        snapshot=saved,
        content=working,
    )


def _section_spans(content: str) -> list[dict]:
    """Return section heading spans with body boundaries."""
    server = _server()
    analysis = server._prepare_analysis(content)
    end_matches = list(server._END_DOCUMENT_RE.finditer(analysis))
    document_end = end_matches[-1].start() if end_matches else len(analysis)

    headings: list[dict] = []
    for match in server._SECTION_RE.finditer(analysis):
        title, argument_end = server._read_braced(analysis, match.end() - 1)
        if title is None:
            continue
        line_end = analysis.find("\n", argument_end)
        body_start = line_end + 1 if line_end != -1 else len(analysis)
        headings.append(
            {
                "level_name": match.group(1),
                "level": _LEVEL_ORDER[match.group(1)],
                "title": title.strip(),
                "start": match.start(),
                "body_start": body_start,
                "line": server._line_number(analysis, match.start()),
            }
        )

    for index, heading in enumerate(headings):
        body_end = document_end
        for later in headings[index + 1 :]:
            if later["level"] <= heading["level"]:
                body_end = later["start"]
                break
        heading["body_end"] = body_end

    return [heading for heading in headings if heading["start"] < document_end]


def _find_section(content: str, title: str, level: str | None = None) -> dict:
    matches = [
        heading
        for heading in _section_spans(content)
        if heading["title"].casefold() == title.strip().casefold()
        and (level is None or heading["level_name"] == level)
    ]
    if not matches:
        available = ", ".join(
            f"{heading['level_name']} '{heading['title']}'"
            for heading in _section_spans(content)
        ) or "none"
        raise ValueError(
            f"Section not found: {title!r}. Available: {available}"
        )
    if len(matches) > 1:
        locations = ", ".join(
            f"{heading['level_name']} at line {heading['line']}"
            for heading in matches
        )
        raise ValueError(
            f"Section {title!r} is ambiguous ({locations}); pass section_level."
        )
    return matches[0]


def _insert_at_section_end(content: str, heading: dict, snippet: str) -> str:
    prefix = content[: heading["body_end"]].rstrip("\n")
    suffix = content[heading["body_end"] :]
    body = snippet.strip("\n")
    insertion = "\n" + body + "\n"
    if suffix and not suffix.startswith("\n"):
        insertion += "\n"
    return prefix + insertion + suffix


def _insert_snippet(
    content: str, snippet: str, section: str | None, section_level: str | None
) -> str:
    server = _server()
    if section:
        heading = _find_section(content, section, section_level)
        return _insert_at_section_end(content, heading, snippet)
    return server._append_inside_document(content, snippet)


def edit_section(
    file_path: str,
    section: str,
    operation: Literal["replace", "append", "prepend", "delete"],
    new_text: str = "",
    section_level: str | None = None,
    snapshot: bool = True,
) -> AuthoringResult:
    """Edit a named section's body while preserving its heading."""
    server = _server()
    path = server.get_safe_path(file_path)
    server._require_file(path)
    content = server._read_text_file(path)
    heading = _find_section(content, section, section_level)

    saved = (
        create_snapshot(file_path, label=f"before section {operation}")
        if snapshot
        else None
    )
    body = new_text.strip("\n")

    if operation == "replace":
        replacement = (body + "\n\n") if body else ""
        updated = (
            content[: heading["body_start"]]
            + replacement
            + content[heading["body_end"] :]
        )
    elif operation == "append":
        if body:
            updated = _insert_at_section_end(content, heading, body)
        else:
            updated = content
    elif operation == "prepend":
        insertion = (body + "\n\n") if body else ""
        updated = (
            content[: heading["body_start"]]
            + insertion
            + content[heading["body_start"] :]
        )
    elif operation == "delete":
        head = content[: heading["start"]].rstrip("\n")
        tail = content[heading["body_end"] :].lstrip("\n")
        updated = (head + "\n" if head else "") + tail
    else:  # pragma: no cover - Literal validation normally prevents this
        raise ValueError(f"Unsupported section operation: {operation!r}")

    server._atomic_write_text(path, updated)
    return AuthoringResult(
        path=str(path),
        success=True,
        message=f"Section '{section}' {operation} applied",
        applied=1,
        snapshot=saved,
        content=updated,
    )


def _validate_label(label: str | None) -> str | None:
    if label is None:
        return None
    label = label.strip()
    if not label or not _LABEL_RE.fullmatch(label):
        raise ValueError(
            f"Invalid label: {label!r}. Labels cannot contain spaces, commas, or braces."
        )
    return label


def _validate_placement(placement: str) -> str:
    if not re.fullmatch(r"[htbp!]+", placement):
        raise ValueError(
            f"Invalid float placement: {placement!r}. Use characters from h, t, b, p, !."
        )
    return placement


def _write_generated(
    file_path: str,
    snippet: str,
    section: str | None,
    section_level: str | None,
    snapshot: bool,
    message: str,
) -> AuthoringResult:
    server = _server()
    path = server.get_safe_path(file_path)
    server._require_file(path)
    content = server._read_text_file(path)
    updated = _insert_snippet(content, snippet, section, section_level)
    saved = create_snapshot(file_path, label=message) if snapshot else None
    server._atomic_write_text(path, updated)
    return AuthoringResult(
        path=str(path),
        success=True,
        message=message,
        applied=1,
        snapshot=saved,
        content=updated,
    )


def insert_table(
    file_path: str,
    headers: list[str],
    rows: list[list[str]],
    caption: str = "",
    label: str | None = None,
    placement: str = "htbp",
    section: str | None = None,
    section_level: str | None = None,
    snapshot: bool = True,
) -> AuthoringResult:
    """Insert a table environment, optionally at the end of a named section."""
    if not headers:
        raise ValueError("headers must contain at least one column")
    width = len(headers)
    if any(len(row) != width for row in rows):
        raise ValueError("Every row must have the same number of cells as headers")
    placement = _validate_placement(placement)
    label = _validate_label(label)

    lines = [f"\\begin{{table}}[{placement}]", "\\centering"]
    if caption:
        lines.append(f"\\caption{{{caption}}}")
    if label:
        lines.append(f"\\label{{{label}}}")
    lines.append("\\begin{tabular}{" + "|" + "|".join("l" for _ in headers) + "|}")
    lines.append("\\hline")
    lines.append(" & ".join(headers) + " \\\\")
    lines.append("\\hline")
    for row in rows:
        lines.append(" & ".join(row) + " \\\\")
    lines.append("\\hline")
    lines.append("\\end{tabular}")
    lines.append("\\end{table}")
    return _write_generated(
        file_path,
        "\n".join(lines),
        section,
        section_level,
        snapshot,
        "Table inserted",
    )


def insert_figure(
    file_path: str,
    image_path: str,
    caption: str = "",
    label: str | None = None,
    width: str = "0.8\\textwidth",
    placement: str = "htbp",
    section: str | None = None,
    section_level: str | None = None,
    snapshot: bool = True,
) -> AuthoringResult:
    """Insert a figure environment referencing an image."""
    if not image_path.strip() or any(character in image_path for character in "{}"):
        raise ValueError("image_path must be a non-empty path without braces")
    if any(character in width for character in "[]{}\n\r"):
        raise ValueError("width contains characters that can break the optional argument")
    placement = _validate_placement(placement)
    label = _validate_label(label)

    lines = [
        f"\\begin{{figure}}[{placement}]",
        "\\centering",
        f"\\includegraphics[width={width}]{{{image_path}}}",
    ]
    if caption:
        lines.append(f"\\caption{{{caption}}}")
    if label:
        lines.append(f"\\label{{{label}}}")
    lines.append("\\end{figure}")
    return _write_generated(
        file_path,
        "\n".join(lines),
        section,
        section_level,
        snapshot,
        "Figure inserted",
    )


def insert_citation(
    file_path: str,
    keys: list[str],
    command: Literal[
        "cite", "citep", "citet", "parencite", "textcite", "autocite"
    ] = "cite",
    section: str | None = None,
    section_level: str | None = None,
    snapshot: bool = True,
) -> AuthoringResult:
    """Insert a citation command."""
    cleaned = [key.strip() for key in keys if key.strip()]
    if not cleaned:
        raise ValueError("At least one citation key is required")
    for key in cleaned:
        if not _CITATION_KEY_RE.fullmatch(key):
            raise ValueError(f"Invalid citation key: {key!r}")
    snippet = f"\\{command}{{{','.join(cleaned)}}}"
    return _write_generated(
        file_path,
        snippet,
        section,
        section_level,
        snapshot,
        "Citation inserted",
    )
