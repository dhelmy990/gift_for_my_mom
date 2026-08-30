from pathlib import Path

from company_names.aliases import AliasSuggestion
from company_names.cleaning import normalize_lookup_key
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


def test_widget_token_preserves_plain_alias_key_when_it_is_unique() -> None:
    assert alias_widget_token("HKTRMs", ROWS) == "hktrms"


def test_widget_tokens_suffix_colliding_alias_keys_deterministically() -> None:
    collisions = [
        AliasReviewRow("A&B", "A&B", "new", None),
        AliasReviewRow("A B", "A B", "new", None),
    ]

    first = alias_widget_token("A&B", collisions)
    second = alias_widget_token("A B", collisions)

    expected_prefix = f"{normalize_lookup_key('A&B')}_"
    assert first.startswith(expected_prefix)
    assert second.startswith(expected_prefix)
    assert first != second
    assert alias_widget_token("A&B", collisions) == first


def test_reset_clears_all_alias_editor_widget_state() -> None:
    collisions = [
        AliasReviewRow("A&B", "A&B", "new", None),
        AliasReviewRow("A B", "A B", "new", None),
    ]
    state = {
        "alias_search": "A",
        f"alias_final_{alias_widget_token('A&B', collisions)}": "stale one",
        f"alias_final_{alias_widget_token('A B', collisions)}": "stale two",
        f"accept_alias_{alias_widget_token('A&B', collisions)}": True,
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
