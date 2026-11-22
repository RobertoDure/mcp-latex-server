#!/usr/bin/env python3
"""Tests for the MCP LaTeX server.

Run with:

    python -m pytest tests/test_core.py -v
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

from mcp_latex_server import server

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    """Run a coroutine from a synchronous test."""
    return asyncio.run(coro)


def create(workspace: Path, name: str = "doc.tex", **kwargs):
    """Create a document through the public tool function."""
    return run(server.create_latex_file(str(workspace / name), **kwargs))


# --- Creation ---


def test_create_article_with_defaults(workspace):
    result = create(workspace, title="Title", author="Author", content="Hello")
    assert result.success
    assert "\\documentclass{article}" in result.content
    assert "\\usepackage[utf8]{inputenc}" in result.content
    assert "\\maketitle" in result.content
    assert "FieldInfo" not in result.content
    assert (workspace / "doc.tex").read_text(encoding="utf-8") == result.content


def test_create_beamer_wraps_plain_content(workspace):
    result = create(
        workspace,
        "talk.tex",
        document_type="beamer",
        title="Talk",
        content="A plain bullet",
    )
    assert "\\frame{\\titlepage}" in result.content
    assert "\\begin{frame}" in result.content
    assert "\\end{frame}" in result.content
    assert "\\maketitle" not in result.content


def test_create_letter_uses_letter_environment(workspace):
    result = create(
        workspace,
        "letter.tex",
        document_type="letter",
        title="Prof. X",
        author="Author",
        content="Body",
    )
    assert "\\begin{letter}{Prof. X}" in result.content
    assert "\\opening{Dear Recipient,}" in result.content
    assert "\\closing{Sincerely,}" in result.content
    assert "\\end{letter}" in result.content


def test_create_minimal_does_not_use_maketitle(workspace):
    result = create(
        workspace, "minimal.tex", document_type="minimal", title="T", content="Body"
    )
    assert "\\maketitle" not in result.content
    assert "\\begin{center}" in result.content

    untitled = create(workspace, "minimal2.tex", document_type="minimal", content="Body")
    assert "\\begin{center}" not in untitled.content


def test_create_packages_and_geometry(workspace):
    result = create(
        workspace,
        packages=["amsmath,amssymb", "geometry"],
        geometry="margin=1in",
    )
    assert "\\usepackage{amsmath}" in result.content
    assert "\\usepackage{amssymb}" in result.content
    assert "\\usepackage[margin=1in]{geometry}" in result.content
    assert result.content.count("\\usepackage{geometry}") == 0
    assert result.content.count("\\usepackage{amsmath}") == 1


def test_create_rejects_invalid_packages_and_geometry(workspace):
    with pytest.raises(ValueError):
        create(workspace, "bad.tex", packages=["not a package"])
    with pytest.raises(ValueError):
        create(workspace, "bad.tex", geometry="margin=1in]")
    with pytest.raises(ValueError):
        create(workspace, "bad.tex", packages=["name}"])


def test_create_requires_tex_suffix(workspace):
    with pytest.raises(ValueError):
        create(workspace, "notes.txt")


def test_create_refuses_to_overwrite_by_default(workspace):
    target = workspace / "existing.tex"
    target.write_text("keep me", encoding="utf-8")

    with pytest.raises(FileExistsError):
        create(workspace, "existing.tex")

    result = create(workspace, "existing.tex", overwrite=True)
    assert result.success
    assert "keep me" not in target.read_text(encoding="utf-8")


def test_create_from_template(workspace):
    result = run(
        server.create_from_template(str(workspace / "tpl.tex"), template="report")
    )
    assert result.success
    assert "\\documentclass{report}" in result.content
    with pytest.raises(FileExistsError):
        run(server.create_from_template(str(workspace / "tpl.tex"), template="report"))


def test_template_traversal_is_blocked(workspace):
    with pytest.raises(ValueError):
        run(server.get_template("../../etc/passwd"))
    with pytest.raises(ValueError):
        run(
            server.create_from_template(
                str(workspace / "evil.tex"), template="../../etc/passwd"
            )
        )


def test_list_templates_resource():
    text = run(server.list_templates())
    assert "article" in text
    assert "beamer" in text
    assert "report" in text


# --- Reading and listing ---


def test_read_file_and_missing_file(workspace):
    create(workspace, content="Hello")
    result = run(server.read_latex_file(str(workspace / "doc.tex")))
    assert result.success
    assert result.content == (workspace / "doc.tex").read_text(encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        run(server.read_latex_file(str(workspace / "missing.tex")))
    with pytest.raises(ValueError):
        run(server.read_latex_file("../../etc/passwd"))


def test_list_files_recursive_limit_and_symlink_escape(workspace):
    create(workspace, "one.tex")
    create(workspace, "sub/two.tex", content="Nested")

    flat = run(server.list_latex_files("."))
    assert flat.total == 1
    assert [item.path for item in flat.files] == ["one.tex"]

    recursive = run(server.list_latex_files(".", recursive=True))
    assert recursive.total == 2
    assert "sub/two.tex" in [item.path for item in recursive.files]

    limited = run(server.list_latex_files(".", recursive=True, max_results=1))
    assert limited.truncated is True
    assert limited.total == 2
    assert len(limited.files) == 1

    outside = workspace.parent / "outside.tex"
    outside.write_text("secret", encoding="utf-8")
    link = workspace / "link.tex"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available on this platform")

    listing = run(server.list_latex_files("."))
    assert "link.tex" not in [item.path for item in listing.files]
    with pytest.raises(ValueError):
        run(server.read_latex_file(str(link)))


# --- Editing ---


def test_edit_replace_first_then_all(workspace):
    target = workspace / "edit.tex"
    target.write_text("foo foo\n", encoding="utf-8")

    first = run(server.edit_latex_file(str(target), "replace", "bar", "foo"))
    assert target.read_text(encoding="utf-8") == "bar foo\n"
    assert "1 more occurrence" in first.message

    run(server.edit_latex_file(str(target), "replace", "bar", "foo", replace_all=True))
    assert target.read_text(encoding="utf-8") == "bar bar\n"


def test_edit_replace_missing_text_raises(workspace):
    target = workspace / "edit.tex"
    target.write_text("foo\n", encoding="utf-8")
    with pytest.raises(ValueError):
        run(server.edit_latex_file(str(target), "replace", "bar", "missing"))
    with pytest.raises(ValueError):
        run(server.edit_latex_file(str(target), "replace", "bar"))


def test_edit_insert_by_line_and_by_search(workspace):
    target = workspace / "edit.tex"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")

    run(server.edit_latex_file(str(target), "insert_before", "new", line_number=2))
    assert target.read_text(encoding="utf-8") == "line1\nnew\nline2\nline3\n"

    run(server.edit_latex_file(str(target), "insert_after", "after", line_number=1))
    assert target.read_text(encoding="utf-8") == "line1\nafter\nnew\nline2\nline3\n"

    run(server.edit_latex_file(str(target), "insert_before", "before", search_text="line2"))
    assert "before\nline2" in target.read_text(encoding="utf-8")

    run(server.edit_latex_file(str(target), "insert_after", "post", search_text="line1"))
    text = target.read_text(encoding="utf-8")
    assert "line1\npost\nafter" in text


def test_edit_insert_validation(workspace):
    target = workspace / "edit.tex"
    target.write_text("line1\nline2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        run(
            server.edit_latex_file(
                str(target),
                "insert_before",
                "x",
                search_text="line1",
                line_number=1,
            )
        )
    with pytest.raises(ValueError):
        run(server.edit_latex_file(str(target), "insert_after", "x", line_number=99))
    with pytest.raises(ValueError):
        run(server.edit_latex_file(str(target), "insert_after", "x"))


def test_edit_append_and_prepend_stay_inside_document(workspace):
    target = workspace / "edit.tex"
    target.write_text(
        "\\documentclass{article}\n\\begin{document}\nBody\n\\end{document}\n",
        encoding="utf-8",
    )

    run(server.edit_latex_file(str(target), "append", "New"))
    text = target.read_text(encoding="utf-8")
    assert "Body\nNew\n\\end{document}" in text

    run(server.edit_latex_file(str(target), "prepend", "Top"))
    text = target.read_text(encoding="utf-8")
    assert "\\begin{document}\nTop\nBody" in text


def test_edit_append_ignores_verbatim_end_marker(workspace):
    target = workspace / "verbatim_append.tex"
    target.write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\begin{verbatim}\n"
        "\\end{document}\n"
        "\\end{verbatim}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    run(server.edit_latex_file(str(target), "append", "New"))
    text = target.read_text(encoding="utf-8")
    assert "\\end{verbatim}\nNew\n\\end{document}" in text

def test_edit_append_without_document_environment(workspace):
    target = workspace / "plain.tex"
    target.write_text("plain", encoding="utf-8")
    run(server.edit_latex_file(str(target), "append", "more"))
    assert target.read_text(encoding="utf-8") == "plain\nmore\n"


# --- Validation ---


def test_validate_valid_document(workspace):
    create(workspace, title="T", content="Hello")
    result = run(server.validate_latex(str(workspace / "doc.tex")))
    assert result.valid, result.issues


def test_validate_detects_unclosed_environment(workspace):
    target = workspace / "bad.tex"
    target.write_text(
        "\\documentclass{article}\n\\begin{document}\n\\begin{itemize}\n\\end{document}\n",
        encoding="utf-8",
    )
    result = run(server.validate_latex(str(target)))
    assert not result.valid
    assert any("itemize" in issue for issue in result.issues)


def test_validate_ignores_verbatim_content(workspace):
    target = workspace / "verbatim.tex"
    target.write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\begin{verbatim}\n"
        "\\begin{itemize}\n"
        "{ unbalanced\n"
        "\\end{document}\n"
        "\\end{verbatim}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    result = run(server.validate_latex(str(target)))
    assert result.valid, result.issues


def test_validate_commented_verbatim_opener_does_not_mask_document(workspace):
    target = workspace / "commented_verbatim.tex"
    target.write_text(
        "% \\begin{verbatim}\n"
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "Body\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    result = run(server.validate_latex(str(target)))
    assert result.valid, result.issues

def test_validate_ignores_inline_verb_and_escaped_braces(workspace):
    target = workspace / "inline.tex"
    target.write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "Use \\{ and \\} for sets. \\verb|{| is literal.\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    result = run(server.validate_latex(str(target)))
    assert result.valid, result.issues


def test_validate_comment_after_double_backslash(workspace):
    target = workspace / "comment.tex"
    target.write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "line\\\\% this comment has an unbalanced {\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    result = run(server.validate_latex(str(target)))
    assert result.valid, result.issues


def test_validate_detects_content_after_end_document(workspace):
    target = workspace / "trailing.tex"
    target.write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "X\n"
        "\\end{document}\n"
        "Stray text\n",
        encoding="utf-8",
    )
    result = run(server.validate_latex(str(target)))
    assert any("after \\end{document}" in issue for issue in result.issues)
    assert any("(line 5)" in issue for issue in result.issues)


def test_validate_references(workspace):
    target = workspace / "refs.tex"
    target.write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\eqref{missing}\n"
        "\\label{present}\n"
        "\\ref{present}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    result = run(server.validate_latex(str(target)))
    assert any("missing" in issue for issue in result.issues)
    assert not any("present" in issue for issue in result.issues)

    without_refs = run(server.validate_latex(str(target), check_references=False))
    assert without_refs.valid, without_refs.issues


def test_validate_missing_documentclass(workspace):
    target = workspace / "noclass.tex"
    target.write_text("\\begin{document}\nX\n\\end{document}\n", encoding="utf-8")
    result = run(server.validate_latex(str(target)))
    assert any("documentclass" in issue for issue in result.issues)


# --- Structure ---


def test_structure_order_nested_title_packages_and_starred_sections(workspace):
    target = workspace / "structure.tex"
    target.write_text(
        "\\documentclass[12pt]{article}\n"
        "\\title{A {Nested} Title}\n"
        "\\author{Author}\n"
        "\\date{\\today}\n"
        "\\usepackage{amsmath,amssymb}\n"
        "\\begin{document}\n"
        "\\section{One}\n"
        "\\subsection{Two}\n"
        "\\subsubsection{Deep}\n"
        "\\chapter{Chap}\n"
        "\\section*{Star}\n"
        "% \\section{Commented Out}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    structure = run(server.get_latex_structure(str(target)))
    assert structure.document_class == "article"
    assert structure.title == "A {Nested} Title"
    assert structure.author == "Author"
    assert structure.date == "\\today"
    assert structure.packages == ["amsmath", "amssymb"]
    assert structure.sections == [
        "Section: One",
        "Subsection: Two",
        "Subsubsection: Deep",
        "Chapter: Chap",
        "Section*: Star",
    ]


# --- Compilation ---


SUCCESS_ENGINE = r"""#!/bin/sh
for last; do :; done
base=$(basename "$last" .tex)
printf 'This is pdfTeX\n' >&2
: > "$base.log"
: > "$base.pdf"
exit 0
"""

FAILURE_ENGINE = r"""#!/bin/sh
for last; do :; done
base=$(basename "$last" .tex)
{
  printf '! Undefined control sequence.\n'
  printf 'l.3 \\foo\n'
} > "$base.log"
cat "$base.log" >&2
exit 1
"""

SECOND_PASS_ENGINE = r"""#!/bin/sh
for last; do :; done
base=$(basename "$last" .tex)
printf 'aux\n' > "$base.aux"
: > "$base.log"
: > "$base.pdf"
exit 0
"""

CLEAN_ENGINE = r"""#!/bin/sh
for last; do :; done
base=$(basename "$last" .tex)
printf 'aux\n' > "$base.aux"
printf 'log\n' > "$base.log"
: > "$base.pdf"
exit 0
"""

ARGS_ENGINE = r"""#!/bin/sh
printf '%s\n' "$@" > args.txt
for last; do :; done
base=$(basename "$last" .tex)
: > "$base.log"
: > "$base.pdf"
exit 0
"""

TIMEOUT_ENGINE = r"""#!/bin/sh
sleep 5
"""


@pytest.fixture
def fake_engines(monkeypatch):
    """Create a writable, executable directory for fake LaTeX engines."""
    root = Path(tempfile.mkdtemp(prefix=".tmp_engines-", dir=str(PROJECT_ROOT)))
    monkeypatch.setenv("PATH", str(root) + os.pathsep + os.environ.get("PATH", ""))
    yield root
    shutil.rmtree(root, ignore_errors=True)


def write_engine(root: Path, name: str, body: str) -> None:
    path = root / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def make_document(workspace: Path, name: str = "compile.tex") -> Path:
    create(workspace, name, content="Hello")
    return workspace / name


@pytest.mark.skipif(os.name == "nt", reason="fake engines are POSIX shell scripts")
def test_compile_success(workspace, fake_engines):
    write_engine(fake_engines, "pdflatex", SUCCESS_ENGINE)
    target = make_document(workspace)
    result = run(server.compile_latex(str(target)))
    assert result.success, result.errors
    assert result.pdf_path is not None
    assert Path(result.pdf_path).is_file()
    assert result.passes == 1


@pytest.mark.skipif(os.name == "nt", reason="fake engines are POSIX shell scripts")
def test_compile_failure_reports_line(workspace, fake_engines):
    write_engine(fake_engines, "xelatex", FAILURE_ENGINE)
    target = make_document(workspace)
    result = run(server.compile_latex(str(target), engine="xelatex"))
    assert not result.success
    assert any("Undefined control sequence" in error for error in result.errors)
    assert any("line 3" in error for error in result.errors)


@pytest.mark.skipif(os.name == "nt", reason="fake engines are POSIX shell scripts")
def test_compile_second_pass_and_clean_aux(workspace, fake_engines):
    write_engine(fake_engines, "lualatex", SECOND_PASS_ENGINE)
    target = make_document(workspace)
    result = run(server.compile_latex(str(target), engine="lualatex", clean_aux=True))
    assert result.success, result.errors
    assert result.passes == 2
    assert (workspace / "compile.pdf").is_file()
    assert not (workspace / "compile.aux").exists()
    assert not (workspace / "compile.log").exists()
    assert result.log_path is None


@pytest.mark.skipif(os.name == "nt", reason="fake engines are POSIX shell scripts")
def test_compile_shell_escape_is_opt_in(workspace, fake_engines):
    write_engine(fake_engines, "pdflatex", ARGS_ENGINE)
    target = make_document(workspace)
    args_file = workspace / "args.txt"

    result = run(server.compile_latex(str(target)))
    assert result.success, result.errors
    assert "-no-shell-escape" in args_file.read_text(encoding="utf-8")

    result = run(server.compile_latex(str(target), shell_escape=True, clean_aux=True))
    assert result.success, result.errors
    assert "-shell-escape" in args_file.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="fake engines are POSIX shell scripts")
def test_compile_timeout(workspace, fake_engines):
    write_engine(fake_engines, "pdflatex", TIMEOUT_ENGINE)
    target = make_document(workspace)
    started = time.monotonic()
    result = run(server.compile_latex(str(target), timeout_seconds=1))
    elapsed = time.monotonic() - started
    assert not result.success
    assert any("timed out" in error for error in result.errors)
    assert elapsed < 4


@pytest.mark.skipif(os.name == "nt", reason="fake engines are POSIX shell scripts")
def test_compile_missing_engine(workspace, fake_engines, monkeypatch):
    monkeypatch.setenv("PATH", str(fake_engines))
    target = make_document(workspace)
    with pytest.raises(RuntimeError):
        run(server.compile_latex(str(target), engine="pdflatex"))


# --- Setup helper ---


def test_quick_setup_version_comparison(monkeypatch):
    from scripts import quick_setup

    monkeypatch.setattr(quick_setup.sys, "version_info", (3, 11, 9))
    assert quick_setup.check_python() is True

    monkeypatch.setattr(quick_setup.sys, "version_info", (3, 11, 8))
    assert quick_setup.check_python() is False

    monkeypatch.setattr(quick_setup.sys, "version_info", (3, 12, 0))
    assert quick_setup.check_python() is True


def test_quick_setup_windows_config_path(monkeypatch, tmp_path):
    from scripts import quick_setup

    monkeypatch.setattr(quick_setup.platform, "system", lambda: "Windows")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert quick_setup.get_claude_config_path() == (
        tmp_path / "Claude" / "claude_desktop_config.json"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
