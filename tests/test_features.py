#!/usr/bin/env python3
"""Tests for the expanded project, template, authoring, quality, and PDF features."""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

import pytest

from mcp_latex_server import server
from mcp_latex_server.authoring import EditSpec


def run(coro):
    return asyncio.run(coro)


def write_tool(root: Path, name: str, body: str) -> None:
    path = root / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def empty_path(monkeypatch, tmp_path):
    directory = tmp_path / "empty-bin"
    directory.mkdir()
    monkeypatch.setenv("PATH", str(directory))


# --- Templates ---


def test_custom_template_metadata_variables_and_defaults(workspace):
    templates = workspace / "templates"
    templates.mkdir()
    (templates / "paper.tex").write_text(
        "---\n"
        "name: paper\n"
        "description: A custom paper\n"
        "variables:\n"
        "  title: Default title\n"
        "  author: Anonymous\n"
        "required: [title]\n"
        "---\n"
        "\\documentclass{article}\n"
        "\\title{{{title}}}\n"
        "\\author{{{author}}}\n"
        "\\begin{document}\n"
        "{{content}}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )

    listing = run(server.list_template_details())
    by_name = {info.name: info for info in listing.templates}
    assert "paper" in by_name
    assert by_name["paper"].builtin is False
    assert by_name["paper"].description == "A custom paper"
    assert by_name["paper"].defaults["title"] == "Default title"

    result = run(
        server.create_from_template(
            "out.tex",
            template="paper",
            variables={"title": "My Paper", "content": "Body"},
        )
    )
    assert result.success
    assert "\\title{My Paper}" in result.content
    assert "\\author{Anonymous}" in result.content
    assert "Body" in result.content

    with pytest.raises(ValueError):
        run(
            server.create_from_template(
                "missing.tex",
                template="paper",
                variables={"title": "No Content"},
            )
        )


def test_custom_template_overrides_bundled_and_resource_body(workspace):
    templates = workspace / "templates"
    templates.mkdir()
    (templates / "article.tex").write_text(
        "CUSTOM ARTICLE {{title}}\n", encoding="utf-8"
    )

    listing = run(server.list_template_details())
    by_name = {info.name: info for info in listing.templates}
    assert by_name["article"].builtin is False

    result = run(
        server.create_from_template(
            "out.tex", template="article", variables={"title": "Override"}
        )
    )
    assert result.content == "CUSTOM ARTICLE Override\n"

    body = run(server.get_template("article"))
    assert body == "CUSTOM ARTICLE {{title}}\n"


def test_template_listing_reports_invalid_metadata(workspace):
    templates = workspace / "templates"
    templates.mkdir()
    (templates / "broken.tex").write_text(
        "---\nname: [\n---\nbody\n", encoding="utf-8"
    )
    listing = run(server.list_template_details())
    assert listing.errors
    assert "broken.tex" in listing.errors[0]


# --- Project analysis and compilation ---


def test_project_analysis_collects_graph_references_and_bibliography(workspace):
    sections = workspace / "sections"
    sections.mkdir()
    (workspace / "refs.bib").write_text("@article{x, title={X}}\n", encoding="utf-8")
    (workspace / "main.tex").write_text(
        "\\documentclass{article}\n"
        "\\usepackage{biblatex}\n"
        "\\addbibresource{refs.bib}\n"
        "\\begin{document}\n"
        "\\input{sections/intro}\n"
        "\\include{sections/methods}\n"
        "\\section{Main}\\label{main}\n"
        "See \\ref{main} and \\ref{intro-label} and \\eqref{missing}.\n"
        "\\printbibliography\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    (sections / "intro.tex").write_text(
        "\\section{Intro}\\label{intro-label}\nText.\n", encoding="utf-8"
    )
    (sections / "methods.tex").write_text(
        "\\section{Methods}\\label{main}\nMore.\n", encoding="utf-8"
    )

    info = run(server.analyze_project("main.tex"))
    assert [item.path for item in info.files] == [
        "main.tex",
        "sections/intro.tex",
        "sections/methods.tex",
    ]
    assert info.bibliography_tool == "biber"
    assert info.bibliography_files == ["refs.bib"]
    assert "missing" in info.undefined_references
    assert "main" in info.duplicate_labels
    assert any("Duplicate label" in issue.message for issue in info.issues)
    assert any("Undefined reference: missing" in issue.message for issue in info.issues)

    validation = run(server.validate_project("main.tex"))
    assert validation.valid is False
    assert any(issue.severity == "error" for issue in validation.issues)


def test_project_validation_can_skip_references(workspace):
    (workspace / "main.tex").write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\ref{not-there}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    strict = run(server.validate_project("main.tex"))
    assert strict.valid is False
    relaxed = run(server.validate_project("main.tex", check_references=False))
    assert relaxed.valid is True


def test_project_missing_include_reports_line(workspace):
    (workspace / "main.tex").write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\input{missing}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    info = run(server.analyze_project("main.tex"))
    assert info.missing_files == ["missing"]
    issue = next(issue for issue in info.issues if "Included file not found" in issue.message)
    assert issue.line == 3

    result = run(server.compile_project("main.tex"))
    assert result.success is False
    assert any("Missing included file" in error for error in result.errors)


def test_project_compile_with_bibliography(workspace, tool_bin):
    write_tool(
        tool_bin,
        "pdflatex",
        "#!/bin/sh\n"
        "for last; do :; done\n"
        "base=$(basename \"$last\" .tex)\n"
        ": > \"$base.log\"\n"
        ": > \"$base.pdf\"\n"
        "exit 0\n",
    )
    write_tool(tool_bin, "biber", "#!/bin/sh\nexit 0\n")

    (workspace / "chapter.tex").write_text("Chapter text\n", encoding="utf-8")
    (workspace / "refs.bib").write_text("@article{x, title={X}}\n", encoding="utf-8")
    (workspace / "main.tex").write_text(
        "\\documentclass{article}\n"
        "\\usepackage{biblatex}\n"
        "\\addbibresource{refs.bib}\n"
        "\\begin{document}\n"
        "\\input{chapter}\n"
        "\\cite{x}\n"
        "\\printbibliography\n"
        "\\end{document}\n",
        encoding="utf-8",
    )

    result = run(server.compile_project("main.tex"))
    assert result.success, result.errors
    assert result.bibliography_tool == "biber"
    assert result.bibliography_runs == 1
    assert "chapter.tex" in result.project_files
    assert result.pdf_path is not None
    assert Path(result.pdf_path).is_file()
    assert result.engine_passes >= 2


def test_project_compile_latexmk(workspace, tool_bin):
    write_tool(
        tool_bin,
        "latexmk",
        "#!/bin/sh\n"
        "for last; do :; done\n"
        "base=$(basename \"$last\" .tex)\n"
        ": > \"$base.log\"\n"
        ": > \"$base.pdf\"\n"
        "exit 0\n",
    )
    (workspace / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nHi\n\\end{document}\n",
        encoding="utf-8",
    )
    result = run(server.compile_project("main.tex", use_latexmk=True))
    assert result.success, result.errors
    assert result.compiler == "latexmk"


# --- Transactions, snapshots, and section authoring ---


def test_apply_edits_is_transactional_and_snapshots(workspace):
    target = workspace / "doc.tex"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    result = run(
        server.apply_edits(
            "doc.tex",
            [
                EditSpec(operation="replace", search_text="alpha", new_text="ALPHA"),
                EditSpec(operation="append", new_text="gamma"),
            ],
        )
    )
    assert result.success
    assert result.applied == 2
    assert result.snapshot is not None
    assert target.read_text(encoding="utf-8") == "ALPHA\nbeta\ngamma\n"
    snapshots = run(server.list_snapshots("doc.tex"))
    assert len(snapshots.snapshots) == 1


def test_apply_edits_failure_leaves_file_untouched(workspace):
    target = workspace / "doc.tex"
    original = "alpha\nbeta\n"
    target.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError):
        run(
            server.apply_edits(
                "doc.tex",
                [
                    EditSpec(operation="replace", search_text="alpha", new_text="ALPHA"),
                    EditSpec(operation="replace", search_text="missing", new_text="X"),
                ],
            )
        )
    assert target.read_text(encoding="utf-8") == original


def test_apply_edits_validate_after_rejects_write(workspace):
    target = workspace / "doc.tex"
    original = (
        "\\documentclass{article}\n\\begin{document}\nBody\n\\end{document}\n"
    )
    target.write_text(original, encoding="utf-8")
    result = run(
        server.apply_edits(
            "doc.tex",
            [EditSpec(operation="append", new_text="{ unbalanced")],
            validate_after=True,
        )
    )
    assert result.success is False
    assert result.issues
    assert target.read_text(encoding="utf-8") == original


def test_snapshot_restore_round_trip(workspace):
    target = workspace / "doc.tex"
    target.write_text("original\n", encoding="utf-8")
    snapshot = run(server.create_snapshot("doc.tex", label="baseline"))
    target.write_text("changed\n", encoding="utf-8")

    result = run(server.restore_snapshot("doc.tex", snapshot.id))
    assert result.success
    assert target.read_text(encoding="utf-8") == "original\n"

    listed = run(server.list_snapshots("doc.tex"))
    assert any(item.id == snapshot.id for item in listed.snapshots)


def test_snapshot_directory_cannot_escape_base(workspace, tmp_path, monkeypatch):
    target = workspace / "doc.tex"
    target.write_text("body\n", encoding="utf-8")
    monkeypatch.setenv("LATEX_SERVER_SNAPSHOT_DIR", str(tmp_path / "outside"))
    with pytest.raises(ValueError):
        run(server.create_snapshot("doc.tex"))


def test_edit_section_operations_and_ambiguity(workspace):
    target = workspace / "doc.tex"
    target.write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\section{Intro}\n"
        "Hello\n"
        "\\subsection{Detail}\n"
        "Detail\n"
        "\\section{Methods}\n"
        "Methods text\n"
        "\\end{document}\n",
        encoding="utf-8",
    )

    assert run(server.edit_section("doc.tex", "Intro", "append", "Added")).success
    # A section body includes its subsections; append targets the end of the body.
    assert "Detail\nAdded" in target.read_text(encoding="utf-8")

    assert run(server.edit_section("doc.tex", "Intro", "prepend", "Top")).success
    assert "\\section{Intro}\nTop" in target.read_text(encoding="utf-8")

    assert run(server.edit_section("doc.tex", "Intro", "replace", "Replaced")).success
    assert "Replaced" in target.read_text(encoding="utf-8")
    assert "Detail" not in target.read_text(encoding="utf-8")

    assert run(server.edit_section("doc.tex", "Methods", "delete")).success
    assert "\\section{Methods}" not in target.read_text(encoding="utf-8")

    target.write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\section{Same}\nA\n"
        "\\subsection{Same}\nB\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        run(server.edit_section("doc.tex", "Same", "append", "X"))
    assert run(
        server.edit_section("doc.tex", "Same", "append", "X", section_level="section")
    ).success


def test_builders_insert_valid_environments(workspace):
    target = workspace / "doc.tex"
    target.write_text(
        "\\documentclass{article}\n\\begin{document}\n"
        "\\section{Results}\nText\n\\end{document}\n",
        encoding="utf-8",
    )

    table = run(
        server.insert_table(
            "doc.tex",
            ["A", "B"],
            [["1", "2"]],
            caption="Caption",
            label="tab:one",
            section="Results",
        )
    )
    assert table.success
    assert "\\begin{table}[htbp]" in table.content
    assert "\\label{tab:one}" in table.content

    figure = run(
        server.insert_figure(
            "doc.tex",
            "figure.png",
            caption="Figure",
            label="fig:one",
            section="Results",
        )
    )
    assert figure.success
    assert "\\includegraphics" in figure.content

    citation = run(
        server.insert_citation("doc.tex", ["one", "two"], command="citep")
    )
    assert citation.success
    assert "\\citep{one,two}" in citation.content

    with pytest.raises(ValueError):
        run(server.insert_table("doc.tex", ["A"], [["1", "2"]]))
    with pytest.raises(ValueError):
        run(server.insert_table("doc.tex", ["A"], [["1"]], placement="bad"))
    with pytest.raises(ValueError):
        run(server.insert_figure("doc.tex", "x.png", width="1{0}"))
    with pytest.raises(ValueError):
        run(server.insert_citation("doc.tex", ["bad key"]))


def test_project_dynamic_include_is_a_warning(workspace):
    (workspace / "main.tex").write_text(
        "\\documentclass{article}\n"
        "\\newcommand{\\myinput}[1]{\\input{#1}}\n"
        "\\begin{document}\n"
        "\\myinput{whatever}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    info = run(server.analyze_project("main.tex"))
    assert info.missing_files == []
    assert any(
        issue.severity == "warning" and "Dynamic include" in issue.message
        for issue in info.issues
    )
    assert run(server.validate_project("main.tex")).valid is True


def test_project_biblatex_backend_bibtex(workspace):
    (workspace / "refs.bib").write_text("@article{x, title={X}}\n", encoding="utf-8")
    (workspace / "main.tex").write_text(
        "\\documentclass{article}\n"
        "\\usepackage[backend=bibtex]{biblatex}\n"
        "\\addbibresource{refs.bib}\n"
        "\\begin{document}\nX\n\\end{document}\n",
        encoding="utf-8",
    )
    info = run(server.analyze_project("main.tex"))
    assert info.bibliography_tool == "bibtex"


# --- Quality, logs, spelling, and PDF ---


def test_word_count_and_todos(workspace):
    target = workspace / "doc.tex"
    target.write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\section{One}\n"
        "Hello world TODO: check this.\n"
        "\\section{Two}\n"
        "More text. % FIXME later\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    count = run(server.count_words("doc.tex"))
    assert count.words > 0
    assert count.sections == 2
    assert count.lines == 8

    todos = run(server.find_todos("doc.tex"))
    assert [item.kind for item in todos.items] == ["TODO", "FIXME"]
    assert todos.items[0].line == 4


def test_analyze_log(workspace):
    target = workspace / "doc.log"
    target.write_text(
        "! Undefined control sequence.\n"
        "l.3 \\foo\n"
        "LaTeX Warning: Reference x undefined.\n"
        "Overfull \\hbox (10pt too wide) in paragraph at lines 5--6\n"
        "Underfull \\hbox (badness 10000) in paragraph at lines 7--8\n",
        encoding="utf-8",
    )
    result = run(server.analyze_log("doc.log"))
    assert result.error_count == 1
    assert result.warning_count == 1
    assert result.overfull
    assert result.underfull


def test_lint_without_chktex(workspace, tmp_path, monkeypatch):
    empty_path(monkeypatch, tmp_path)
    target = workspace / "doc.tex"
    target.write_text(
        "\\documentclass{article}\n\\begin{document}\nX\n\\end{document}\n",
        encoding="utf-8",
    )
    result = run(server.lint_latex("doc.tex"))
    assert result.available is False
    assert "chktex not found" in result.message


def test_lint_with_fake_chktex(workspace, tool_bin):
    write_tool(
        tool_bin,
        "chktex",
        "#!/bin/sh\nprintf 'doc.tex:2:1: Fake warning\\n'\nexit 0\n",
    )
    target = workspace / "doc.tex"
    target.write_text(
        "\\documentclass{article}\n\\begin{document}\nX\n\\end{document}\n",
        encoding="utf-8",
    )
    result = run(server.lint_latex("doc.tex"))
    assert result.available is True
    assert any("Fake warning" in issue for issue in result.issues)


def test_format_without_latexindent(workspace, tmp_path, monkeypatch):
    empty_path(monkeypatch, tmp_path)
    target = workspace / "doc.tex"
    target.write_text("body\n", encoding="utf-8")
    result = run(server.format_latex("doc.tex"))
    assert result.available is False
    assert result.content == "body\n"


def test_format_with_fake_latexindent(workspace, tool_bin):
    write_tool(tool_bin, "latexindent", "#!/bin/sh\nprintf 'FORMATTED\\n'\nexit 0\n")
    target = workspace / "doc.tex"
    target.write_text("body\n", encoding="utf-8")
    result = run(server.format_latex("doc.tex", in_place=True))
    assert result.available is True
    assert result.changed is True
    assert target.read_text(encoding="utf-8") == "FORMATTED\n"


def test_spell_check_with_fake_hunspell(workspace, tool_bin):
    write_tool(
        tool_bin,
        "hunspell",
        "#!/bin/sh\ncat > /dev/null\nprintf 'helo\\n'\nexit 0\n",
    )
    target = workspace / "doc.tex"
    target.write_text(
        "\\documentclass{article}\n\\begin{document}\nhello world\n\\end{document}\n",
        encoding="utf-8",
    )
    result = run(server.spell_check("doc.tex"))
    assert result.available is True
    assert "helo" in result.misspelled


def make_pdf(path: Path) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=300)
    writer.add_blank_page(width=200, height=300)
    writer.add_metadata({"/Title": "Test PDF", "/Author": "Me"})
    with open(path, "wb") as handle:
        writer.write(handle)


def test_read_pdf_file_returns_embedded_pdf(workspace):
    make_pdf(workspace / "test.pdf")
    (workspace / "doc.tex").write_text("not a pdf\n", encoding="utf-8")

    result = run(server.read_pdf_file("test.pdf"))
    assert result.type == "resource"
    mime = getattr(
        result.resource, "mimeType", getattr(result.resource, "mime_type", None)
    )
    assert mime == "application/pdf"
    data = base64.b64decode(result.resource.blob)
    assert data.startswith(b"%PDF")

    with pytest.raises(ValueError):
        run(server.read_pdf_file("doc.tex"))


def test_pdf_info_and_text(workspace):
    make_pdf(workspace / "test.pdf")
    info = run(server.get_pdf_info("test.pdf"))
    assert info.success
    assert info.pages == 2
    assert info.title == "Test PDF"

    text = run(server.extract_pdf_text("test.pdf"))
    assert text.success
    assert text.pages == 2


def test_pdf_render_without_pdftoppm(workspace, tmp_path, monkeypatch):
    empty_path(monkeypatch, tmp_path)
    make_pdf(workspace / "test.pdf")
    result = run(server.render_pdf_page("test.pdf"))
    assert result.success is False
    assert "pdftoppm" in result.message


def test_pdf_render_with_fake_pdftoppm(workspace, tool_bin):
    write_tool(
        tool_bin,
        "pdftoppm",
        "#!/bin/sh\nfor last; do :; done\nprintf 'PNG' > \"$last.png\"\nexit 0\n",
    )
    make_pdf(workspace / "test.pdf")
    result = run(server.render_pdf_page("test.pdf", page=1))
    assert result.success
    assert result.image_path is not None
    assert Path(result.image_path).is_file()


def test_pdf_path_traversal_blocked(workspace):
    with pytest.raises(ValueError):
        run(server.get_pdf_info("../../outside.pdf"))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
