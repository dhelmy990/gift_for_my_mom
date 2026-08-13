import ast
from pathlib import Path


def test_app_has_no_debug_print_and_parses():
    source = Path("app.py").read_text()

    ast.parse(source)
    assert "DEBUG:" not in source
    assert "print(" not in source
    assert "render_name_review" in source
    assert "prepare_review" in source
