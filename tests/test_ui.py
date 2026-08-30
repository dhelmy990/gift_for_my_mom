from pathlib import Path

from company_names.aliases import AliasSuggestion
from company_names.service import AliasReviewRow
from company_names.ui import (
    edited_final_names,
    validate_save_password,
    visible_review_rows,
)


ROWS = [
    AliasReviewRow("HKTRM", "Hong Kong TUYI Business Travel", "saved", None),
    AliasReviewRow(
        "HKTRMs",
        "HKTRMs",
        "suggested",
        AliasSuggestion("HKTRM", "Hong Kong TUYI Business Travel", 90.91),
    ),
    AliasReviewRow("Miki Travel", "Miki Travel", "new", None),
]


def test_ui_contains_plain_mapping_copy_and_no_group_board_copy() -> None:
    source = Path("company_names/ui.py").read_text()

    assert "Company name mappings" in source
    assert "Save all changes and update totals" in source
    assert "Suggested from" in source
    assert "Working tray" not in source
    assert "Combined groups" not in source
    assert "Prepare mapping backup" not in source


def test_search_filters_current_report_rows_case_insensitively() -> None:
    assert [
        row.cleaned_name for row in visible_review_rows(ROWS, "hktr")
    ] == ["HKTRM", "HKTRMs"]


def test_suggestion_is_not_applied_until_explicit_accept() -> None:
    values = edited_final_names(ROWS, {})

    assert values["HKTRMs"] == "HKTRMs"


def test_save_password_validation_is_explicit() -> None:
    assert validate_save_password("wrong", "correct") == "Incorrect admin password"
    assert validate_save_password("correct", "correct") is None
