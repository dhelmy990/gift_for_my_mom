from pathlib import Path

from company_names.aliases import AliasSuggestion
from company_names.service import AliasReviewRow
from company_names.ui import (
    alias_widget_token,
    edited_final_names,
    reset_alias_editor_state,
    stage_save_password_attempt,
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


def test_widget_tokens_distinguish_cleaned_names_with_the_same_alias_key() -> None:
    assert alias_widget_token("A&B") != alias_widget_token("A B")
    assert alias_widget_token("A&B") == alias_widget_token("A&B")


def test_reset_clears_all_alias_editor_widget_state() -> None:
    state = {
        "alias_search": "A",
        f"alias_final_{alias_widget_token('A&B')}": "stale one",
        f"alias_final_{alias_widget_token('A B')}": "stale two",
        f"accept_alias_{alias_widget_token('A&B')}": True,
        "alias_admin_password": "secret",
        "save_aliases": True,
        "unrelated": "keep",
    }

    reset_alias_editor_state(state)

    assert state == {"unrelated": "keep"}


def test_save_attempt_stages_and_immediately_clears_password() -> None:
    state = {"alias_admin_password": "secret"}

    stage_save_password_attempt(state)

    assert state["alias_admin_password"] == ""
    assert state["_alias_save_password_attempt"] == "secret"


def test_final_name_inputs_have_row_specific_labels() -> None:
    source = Path("company_names/ui.py").read_text()

    assert 'f"Final company name for {row.cleaned_name}"' in source
