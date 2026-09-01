from pathlib import Path

from company_names.aliases import AliasSuggestion
from company_names.cleaning import normalize_lookup_key
from company_names.service import AliasReviewRow
from company_names.ui import (
    ALIAS_FILTER_OPTIONS,
    PAGE_SIZE_OPTIONS,
    alias_widget_token,
    edited_final_names,
    filter_review_rows,
    paginate_review_rows,
    reconcile_alias_report_scope,
    reset_alias_editor_state,
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
    assert "Admin password" not in source


def test_search_filters_current_report_rows_case_insensitively() -> None:
    assert [
        row.cleaned_name for row in visible_review_rows(ROWS, "hktr")
    ] == ["HKTRM", "HKTRMs"]


def test_suggestion_is_not_applied_until_explicit_accept() -> None:
    values = edited_final_names(ROWS, {})

    assert values["HKTRMs"] == "HKTRMs"


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
        "alias_status_filter": "Already saved",
        "alias_page_size": 50,
        "alias_page": 3,
        "alias_edits": {"A&B": "stale"},
        "alias_previous_top": True,
        "alias_next_bottom": True,
        f"alias_final_{alias_widget_token('A&B', collisions)}": "stale one",
        f"alias_final_{alias_widget_token('A B', collisions)}": "stale two",
        f"accept_alias_{alias_widget_token('A&B', collisions)}": True,
        "save_aliases": True,
        "unrelated": "keep",
    }

    reset_alias_editor_state(state)

    assert state == {"unrelated": "keep"}


def test_scope_change_actively_discards_report_and_editor_state() -> None:
    state = {
        "prepared_aliases": object(),
        "prepared_aliases_fingerprint": "upload-a",
        "prepared_aliases_mode": True,
        "saved_alias_aggregate": object(),
        "saved_alias_aggregate_fingerprint": "upload-a",
        "current_alias_aggregate": object(),
        "current_alias_aggregate_fingerprint": "upload-a",
        "final_alias_results": object(),
        "final_alias_results_fingerprint": "upload-a",
        "alias_final_acme": "typed edit",
        "alias_search": "acme",
        "unrelated": "keep",
    }

    assert reconcile_alias_report_scope(state, True, "upload-b") is False

    assert state == {"unrelated": "keep"}
    assert reconcile_alias_report_scope(state, True, "upload-a") is False


def test_unchanged_scope_preserves_saved_report_and_typed_edits() -> None:
    prepared = object()
    aggregate = object()
    state = {
        "prepared_aliases": prepared,
        "prepared_aliases_fingerprint": "upload-a",
        "prepared_aliases_mode": True,
        "saved_alias_aggregate": aggregate,
        "saved_alias_aggregate_fingerprint": "upload-a",
        "current_alias_aggregate": object(),
        "current_alias_aggregate_fingerprint": "upload-a",
        "alias_final_acme": "typed edit",
    }

    assert reconcile_alias_report_scope(state, True, "upload-a") is True

    assert state["prepared_aliases"] is prepared
    assert state["saved_alias_aggregate"] is aggregate
    assert "current_alias_aggregate" not in state
    assert "current_alias_aggregate_fingerprint" not in state
    assert state["alias_final_acme"] == "typed edit"


def test_matching_scope_discards_saved_totals_with_a_stale_fingerprint() -> None:
    state = {
        "prepared_aliases": object(),
        "prepared_aliases_fingerprint": "upload-a",
        "prepared_aliases_mode": True,
        "saved_alias_aggregate": object(),
        "saved_alias_aggregate_fingerprint": "upload-b",
    }

    assert reconcile_alias_report_scope(state, True, "upload-a") is True

    assert "saved_alias_aggregate" not in state
    assert "saved_alias_aggregate_fingerprint" not in state


def test_mode_change_clears_state_even_with_same_supplied_fingerprint() -> None:
    state = {
        "prepared_aliases": object(),
        "prepared_aliases_fingerprint": "same",
        "prepared_aliases_mode": True,
        "alias_final_acme": "typed edit",
    }

    assert reconcile_alias_report_scope(state, False, "same") is False

    assert state == {}


def test_final_name_inputs_have_row_specific_labels() -> None:
    source = Path("company_names/ui.py").read_text()

    assert 'f"Final company name for {row.cleaned_name}"' in source


def test_mapping_layout_has_headers_and_row_scoped_suggestions() -> None:
    source = Path("company_names/ui.py").read_text()

    assert 'name_header.markdown("**Old name**")' in source
    assert 'final_header.markdown("**New / final name**")' in source
    assert 'status_header.markdown("**Status**")' in source
    assert "status_column.caption(row.status.title())" in source
    assert 'final_column.caption(\n                f"Suggested from' in source
    assert 'final_column.button(\n                "Use this suggestion"' in source


def test_filter_options_and_page_sizes_match_the_approved_ui() -> None:
    assert ALIAS_FILTER_OPTIONS == (
        "Needs review",
        "New names",
        "Suggestions",
        "Already saved",
        "All names",
    )
    assert PAGE_SIZE_OPTIONS == (10, 20, 50, 100)


def test_status_filters_select_the_expected_rows() -> None:
    assert [row.cleaned_name for row in filter_review_rows(ROWS, "Needs review", "")] == [
        "HKTRMs",
        "Miki Travel",
    ]
    assert [row.cleaned_name for row in filter_review_rows(ROWS, "New names", "")] == [
        "Miki Travel"
    ]
    assert [row.cleaned_name for row in filter_review_rows(ROWS, "Suggestions", "")] == [
        "HKTRMs"
    ]
    assert [row.cleaned_name for row in filter_review_rows(ROWS, "Already saved", "")] == [
        "HKTRM"
    ]
    assert filter_review_rows(ROWS, "All names", "") == ROWS


def test_search_applies_after_status_filter_and_uses_current_edits() -> None:
    result = filter_review_rows(
        ROWS,
        "Needs review",
        "tuyi",
        {"HKTRMs": "Hong Kong TUYI Business Travel"},
    )

    assert [row.cleaned_name for row in result] == ["HKTRMs"]


def test_paginate_rows_clamps_pages_and_reports_visible_range() -> None:
    rows = [AliasReviewRow(f"Name {index}", f"Name {index}", "new", None) for index in range(47)]

    first = paginate_review_rows(rows, page=1, page_size=20)
    last = paginate_review_rows(rows, page=99, page_size=20)

    assert first.page == 1
    assert first.total_pages == 3
    assert first.start == 1
    assert first.end == 20
    assert len(first.rows) == 20
    assert last.page == 3
    assert last.start == 41
    assert last.end == 47
    assert len(last.rows) == 7


def test_paginate_empty_rows_has_no_page_controls_range() -> None:
    result = paginate_review_rows([], page=4, page_size=20)

    assert result.page == 1
    assert result.total_pages == 0
    assert result.start == 0
    assert result.end == 0
    assert result.rows == []


def test_hidden_page_edits_are_included_in_complete_save_values() -> None:
    rows = [AliasReviewRow(f"Name {index}", f"Name {index}", "new", None) for index in range(25)]
    edits = {"Name 24": "Canonical Last Name"}

    assert edited_final_names(rows, edits)["Name 24"] == "Canonical Last Name"
