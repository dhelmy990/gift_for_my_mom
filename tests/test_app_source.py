import ast
from pathlib import Path


def test_app_has_no_debug_print_and_parses():
    source = Path("app.py").read_text()

    ast.parse(source)
    assert "DEBUG:" not in source
    assert "print(" not in source
    assert "render_name_review" in source
    assert "prepare_review" in source


def test_app_surfaces_sanitized_repository_operation_for_diagnostics():
    source = Path("app.py").read_text()

    assert "except RepositoryUnavailableError as exc:" in source
    assert "st.error(f\"Database request failed: {exc}\")" in source
    assert "logger.warning(\"Supabase preparation failed: %s\", exc)" in source


def test_name_review_uses_plain_language_singleton_first_copy():
    source = Path("company_names/ui.py").read_text()

    for phrase in (
        "1. Find names  →  2. Combine duplicates  →  3. Review and save",
        "Separate companies",
        "Names left under Separate companies will be saved separately automatically.",
        "View separate companies (",
        "Working tray",
        "Combined groups",
        "Left out of this report",
        "Final company name",
        '"Name"',
        '"Move to"',
        '"Move"',
        "Move a company name",
        "Save mappings and show totals",
        "Backup and recovery",
    ):
        assert phrase in source

    for old_phrase in (
        '"In inventory"',
        '"Canonical title"',
        '"Unlock permanent actions"',
        '"Submit final review"',
        '"Excluded from this report"',
        '"Accessible name movement controls"',
    ):
        assert old_phrase not in source

    assert 'review_widget_key(request_id, "final_company_name")' in source
    assert 'review_widget_key(request_id, "new_group_title")' not in source
    assert '"Return to separate"' in source
    assert '"Move to tray"' in source
    assert 'f"Move tray names to {matching_group.canonical_title}"' in source
    assert source.count(
        "columns[0].markdown(semantic_pill(board.names[name]), unsafe_allow_html=True)"
    ) == 2
