#!/usr/bin/env python3
"""
Test script for latex_server — exercises all tools directly via FastMCP.
"""

import asyncio
import sys
from pathlib import Path

# Ensure we can import the server
sys.path.insert(0, str(Path(__file__).parent))


async def run_tests():
    from latex_server import (
        mcp,
        create_latex_file,
        create_from_template,
        edit_latex_file,
        read_latex_file,
        list_latex_files,
        validate_latex,
        get_latex_structure,
        compile_latex,
        BASE_PATH,
    )

    test_dir = BASE_PATH / "_test_output"
    test_dir.mkdir(exist_ok=True)
    passed = 0
    failed = 0

    def ok(name):
        nonlocal passed
        passed += 1
        print(f"  PASS  {name}")

    def fail(name, err):
        nonlocal failed
        failed += 1
        print(f"  FAIL  {name}: {err}")

    # ── 1. create_latex_file ──
    try:
        result = await create_latex_file(
            file_path=str(test_dir / "test_create.tex"),
            document_type="article",
            title="Test Title",
            author="Test Author",
            content="Hello world.",
            packages=["amsmath"],
        )
        assert result.success and Path(result.path).exists()
        assert "\\usepackage{amsmath}" in result.content
        ok("create_latex_file")
    except Exception as e:
        fail("create_latex_file", e)

    # ── 2. create_from_template ──
    try:
        result = await create_from_template(
            file_path=str(test_dir / "test_template.tex"),
            template="article",
        )
        assert result.success
        assert "\\documentclass{article}" in result.content
        ok("create_from_template")
    except Exception as e:
        fail("create_from_template", e)

    # ── 3. read_latex_file ──
    try:
        result = await read_latex_file(file_path=str(test_dir / "test_create.tex"))
        assert result.success and "Hello world." in result.content
        ok("read_latex_file")
    except Exception as e:
        fail("read_latex_file", e)

    # ── 4. edit_latex_file — replace ──
    try:
        result = await edit_latex_file(
            file_path=str(test_dir / "test_create.tex"),
            operation="replace",
            search_text="Hello world.",
            new_text="Replaced content.",
        )
        assert result.success
        text = Path(result.path).read_text(encoding="utf-8")
        assert "Replaced content." in text and "Hello world." not in text
        ok("edit_latex_file (replace)")
    except Exception as e:
        fail("edit_latex_file (replace)", e)

    # ── 5. edit_latex_file — append ──
    try:
        result = await edit_latex_file(
            file_path=str(test_dir / "test_create.tex"),
            operation="append",
            new_text="% appended line",
        )
        assert result.success
        text = Path(result.path).read_text(encoding="utf-8")
        assert "% appended line" in text
        ok("edit_latex_file (append)")
    except Exception as e:
        fail("edit_latex_file (append)", e)

    # ── 6. edit_latex_file — insert_after by line ──
    try:
        result = await edit_latex_file(
            file_path=str(test_dir / "test_create.tex"),
            operation="insert_after",
            new_text="% inserted after line 1",
            search_text=None,
            line_number=1,
        )
        assert result.success
        lines = Path(result.path).read_text(encoding="utf-8").splitlines()
        assert lines[1] == "% inserted after line 1"
        ok("edit_latex_file (insert_after by line)")
    except Exception as e:
        fail("edit_latex_file (insert_after by line)", e)

    # ── 7. list_latex_files ──
    try:
        result = await list_latex_files(
            directory_path=str(test_dir), recursive=False
        )
        assert len(result.files) >= 2
        ok("list_latex_files")
    except Exception as e:
        fail("list_latex_files", e)

    # ── 8. validate_latex — valid file ──
    try:
        result = await validate_latex(file_path=str(test_dir / "test_create.tex"))
        assert result.valid, f"Expected valid, got issues: {result.issues}"
        ok("validate_latex (valid)")
    except Exception as e:
        fail("validate_latex (valid)", e)

    # ── 9. validate_latex — invalid file ──
    try:
        bad_file = test_dir / "test_bad.tex"
        bad_file.write_text(
            "\\documentclass{article}\n\\begin{document}\n\\begin{itemize}\n\\end{document}\n",
            encoding="utf-8",
        )
        result = await validate_latex(file_path=str(bad_file))
        assert not result.valid
        assert any("itemize" in i for i in result.issues)
        ok("validate_latex (invalid)")
    except Exception as e:
        fail("validate_latex (invalid)", e)

    # ── 10. validate_latex — escaped braces should not trigger false positive ──
    try:
        escaped_file = test_dir / "test_escaped.tex"
        escaped_file.write_text(
            "\\documentclass{article}\n\\begin{document}\nUse \\{ and \\} for sets.\n\\end{document}\n",
            encoding="utf-8",
        )
        result = await validate_latex(file_path=str(escaped_file))
        assert result.valid, f"False positive: {result.issues}"
        ok("validate_latex (escaped braces)")
    except Exception as e:
        fail("validate_latex (escaped braces)", e)

    # ── 11. get_latex_structure ──
    try:
        result = await get_latex_structure(file_path=str(test_dir / "test_template.tex"))
        assert result.document_class == "article"
        assert result.title is not None
        assert len(result.packages) > 0
        assert len(result.sections) > 0
        ok("get_latex_structure")
    except Exception as e:
        fail("get_latex_structure", e)

    # ── 12. path traversal blocked ──
    try:
        await read_latex_file(file_path="../../etc/passwd")
        fail("path traversal block", "should have raised ValueError")
    except ValueError:
        ok("path traversal block")
    except Exception as e:
        fail("path traversal block", e)

    # ── 13. file not found raises ──
    try:
        await read_latex_file(file_path=str(test_dir / "nonexistent.tex"))
        fail("file not found", "should have raised")
    except FileNotFoundError:
        ok("file not found raises FileNotFoundError")
    except Exception as e:
        fail("file not found", e)

    # ── 14. compile_latex (skip if no pdflatex) ──
    import shutil
    if shutil.which("pdflatex"):
        try:
            simple = test_dir / "test_compile.tex"
            simple.write_text(
                "\\documentclass{article}\n\\begin{document}\nHello PDF.\n\\end{document}\n",
                encoding="utf-8",
            )
            result = await compile_latex(file_path=str(simple))
            assert result.success, f"Compile failed: {result.errors}"
            assert result.pdf_path and Path(result.pdf_path).exists()
            ok("compile_latex")
        except Exception as e:
            fail("compile_latex", e)
    else:
        print("  SKIP  compile_latex (pdflatex not on PATH)")

    # ── Cleanup ──
    import shutil as sh
    sh.rmtree(test_dir, ignore_errors=True)

    # ── Summary ──
    print(f"\n{'='*40}")
    print(f"  {passed} passed, {failed} failed")
    print(f"{'='*40}")
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
