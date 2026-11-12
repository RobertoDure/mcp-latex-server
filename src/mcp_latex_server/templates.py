"""Template discovery and rendering for the LaTeX MCP server.

Bundled templates live in the package templates directory. Additional template
directories can be supplied through LATEX_SERVER_TEMPLATE_DIRS, and a templates
directory inside the configured base path is included automatically. Template
files may start with YAML front matter:

---
name: my-article
description: A short article scaffold
variables:
  title: Untitled
  author: Anonymous
required: [title]
---

\\documentclass{article}
\\title{{{title}}}
...

Placeholders use double braces, for example {{title}}.
"""

import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

try:  # PyYAML is an optional convenience dependency.
    import yaml
except ImportError:  # pragma: no cover - exercised only in minimal installs
    yaml = None


TEMPLATE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_FRONT_MATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(?P<meta>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL
)


class TemplateInfo(BaseModel):
    """Metadata for one available template."""

    name: str
    description: str = ""
    path: str
    builtin: bool = False
    variables: list[str] = Field(default_factory=list)
    defaults: dict[str, str] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)


class TemplateListResult(BaseModel):
    """All templates visible to the server."""

    templates: list[TemplateInfo] = Field(default_factory=list)
    custom_directories: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class TemplateRender(BaseModel):
    """A rendered template ready to write."""

    name: str
    path: str
    content: str
    variables_used: list[str] = Field(default_factory=list)
    defaults_applied: dict[str, str] = Field(default_factory=dict)


def custom_template_dirs(base_path: Path) -> list[Path]:
    """Return configured custom template directories, de-duplicated."""
    candidates: list[Path] = []
    raw = os.environ.get("LATEX_SERVER_TEMPLATE_DIRS", "")
    for entry in raw.split(os.pathsep):
        entry = entry.strip()
        if not entry:
            continue
        candidates.append(Path(entry).expanduser())

    # A templates directory inside the configured workspace is a natural place
    # for project-specific templates.
    candidates.append(base_path / "templates")

    result: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:  # pragma: no cover - unusual path failures
            continue
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def _parse_metadata(raw: str, path: Path) -> tuple[dict[str, Any], str]:
    """Split optional YAML front matter from a template body."""
    if not raw.startswith("---"):
        return {}, raw

    match = _FRONT_MATTER_RE.match(raw)
    if match is None:
        raise ValueError(f"Unclosed YAML front matter in template: {path}")

    if yaml is None:
        raise ValueError(
            "Template front matter requires PyYAML. Install it with: pip install pyyaml"
        )
    try:
        metadata = yaml.safe_load(match.group("meta")) or {}
    except Exception as exc:  # yaml.YAMLError, but keep this dependency-optional
        raise ValueError(f"Invalid YAML front matter in template {path}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValueError(f"Template front matter must be a mapping: {path}")
    return metadata, raw[match.end() :]


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value]


def _template_name_from_path(path: Path) -> str:
    name = path.stem
    if name.endswith("_template"):
        name = name[: -len("_template")]
    return name


def _load_template_info(path: Path, builtin: bool) -> TemplateInfo:
    raw = path.read_text(encoding="utf-8")
    metadata, _ = _parse_metadata(raw, path)

    declared_name = str(metadata.get("name") or _template_name_from_path(path)).strip()
    if not TEMPLATE_NAME_RE.fullmatch(declared_name):
        raise ValueError(
            f"Invalid template name {declared_name!r} in {path}. "
            "Use letters, digits, '-' or '_'."
        )

    variables_value = metadata.get("variables", [])
    defaults: dict[str, str] = {}
    if isinstance(variables_value, dict):
        variables = [str(key) for key in variables_value.keys()]
        defaults.update(_string_map(variables_value))
    else:
        variables = _string_list(variables_value)

    defaults.update(_string_map(metadata.get("defaults")))
    required = _string_list(metadata.get("required"))

    return TemplateInfo(
        name=declared_name,
        description=str(metadata.get("description") or ""),
        path=str(path),
        builtin=builtin,
        variables=variables,
        defaults=defaults,
        required=required,
    )


def discover_templates(
    builtin_dir: Path, custom_dirs: list[Path] | None = None
) -> tuple[list[TemplateInfo], list[str]]:
    """Discover templates, with custom directories overriding bundled ones."""
    templates: list[TemplateInfo] = []
    errors: list[str] = []
    seen_names: set[str] = set()

    directories: list[tuple[Path, bool]] = []
    try:
        builtin_resolved = builtin_dir.resolve()
    except OSError:  # pragma: no cover - unusual path failure
        builtin_resolved = builtin_dir
    for directory in custom_dirs or []:
        try:
            if directory.resolve() == builtin_resolved:
                continue
        except OSError:  # pragma: no cover - unusual path failure
            pass
        directories.append((directory, False))
    directories.append((builtin_dir, True))

    for directory, builtin in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.tex")):
            if not path.is_file() or path.name.startswith("."):
                continue
            try:
                info = _load_template_info(path, builtin)
            except (OSError, UnicodeError, ValueError) as exc:
                errors.append(f"{path}: {exc}")
                continue
            if info.name in seen_names:
                continue
            seen_names.add(info.name)
            templates.append(info)

    templates.sort(key=lambda item: item.name)
    return templates, errors


def _resolve_template(
    name: str, builtin_dir: Path, custom_dirs: list[Path] | None = None
) -> tuple[TemplateInfo, dict[str, Any], str]:
    cleaned = (name or "").strip()
    if not TEMPLATE_NAME_RE.fullmatch(cleaned):
        raise ValueError(
            f"Invalid template name: {name!r}. Use letters, digits, '-' or '_'."
        )

    templates, errors = discover_templates(builtin_dir, custom_dirs)
    for info in templates:
        if info.name != cleaned:
            continue
        path = Path(info.path)
        raw = path.read_text(encoding="utf-8")
        metadata, body = _parse_metadata(raw, path)
        return info, metadata, body

    available = ", ".join(info.name for info in templates) or "none"
    detail = f" Template discovery errors: {'; '.join(errors)}" if errors else ""
    raise FileNotFoundError(
        f"Template '{cleaned}' not found. Available: {available}.{detail}"
    )


def render_template(
    name: str,
    builtin_dir: Path,
    custom_dirs: list[Path] | None = None,
    variables: dict[str, str] | None = None,
) -> TemplateRender:
    """Render a template after substituting double-brace placeholders."""
    info, _metadata, body = _resolve_template(name, builtin_dir, custom_dirs)
    provided = {str(key): str(value) for key, value in (variables or {}).items()}
    merged = {**info.defaults, **provided}

    used = list(dict.fromkeys(_PLACEHOLDER_RE.findall(body)))
    missing = sorted({variable for variable in used if variable not in merged})
    missing.extend(
        variable for variable in info.required if variable not in merged
    )
    missing = sorted(set(missing))
    if missing:
        raise ValueError(
            "Missing template variable(s): "
            + ", ".join(missing)
            + ". Supply them through the variables argument or template defaults."
        )

    def replace(match: re.Match[str]) -> str:
        return merged[match.group(1)]

    rendered = _PLACEHOLDER_RE.sub(replace, body)
    defaults_applied = {key: value for key, value in info.defaults.items() if key in used}
    return TemplateRender(
        name=info.name,
        path=info.path,
        content=rendered,
        variables_used=used,
        defaults_applied=defaults_applied,
    )


def template_body(
    name: str, builtin_dir: Path, custom_dirs: list[Path] | None = None
) -> tuple[TemplateInfo, str]:
    """Return template metadata and body without rendering placeholders."""
    info, _metadata, body = _resolve_template(name, builtin_dir, custom_dirs)
    return info, body


def build_template_listing(
    builtin_dir: Path, custom_dirs: list[Path] | None = None
) -> TemplateListResult:
    """Build the structured template listing used by the list tool."""
    templates, errors = discover_templates(builtin_dir, custom_dirs)
    return TemplateListResult(
        templates=templates,
        custom_directories=[str(directory) for directory in (custom_dirs or [])],
        errors=errors,
    )
