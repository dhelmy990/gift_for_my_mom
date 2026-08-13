import pandas as pd
import pytest

from company_names.models import Group, NameRecord, ReviewBoard, SubmissionPayload
from company_names.review import aggregate_by_group, build_submission, validate_board


def board(*, groups: list[Group], names: list[NameRecord]) -> ReviewBoard:
    return ReviewBoard(
        groups={group.id: group for group in groups},
        names={name.cleaned_name: name for name in names},
    )


def test_validate_requires_included_names_to_be_grouped_but_ignores_inventory() -> None:
    review = board(
        groups=[],
        names=[
            NameRecord("MTL", None, "unknown", selected=True),
            NameRecord("Inventory", None, "unknown"),
        ],
    )

    assert validate_board(review) == ["MTL is included but ungrouped"]


def test_validate_rejects_mismatched_name_keys_deterministically() -> None:
    first = NameRecord("Zulu record", None, "unknown")
    second = NameRecord("Alpha record", None, "unknown")
    forward = ReviewBoard(groups={}, names={"Zulu key": first, "Alpha key": second})
    reverse = ReviewBoard(groups={}, names={"Alpha key": second, "Zulu key": first})

    expected = [
        "Name key 'Alpha key' does not match NameRecord.cleaned_name 'Alpha record'",
        "Name key 'Zulu key' does not match NameRecord.cleaned_name 'Zulu record'",
    ]
    assert validate_board(forward) == expected
    assert validate_board(reverse) == expected


def test_build_submission_rejects_a_mismatched_name_key() -> None:
    review = ReviewBoard(
        groups={"g": Group("g", "Group", True)},
        names={
            "Dictionary identity": NameRecord(
                "Record identity", "g", "exact", selected=True
            )
        },
    )

    with pytest.raises(ValueError, match="Dictionary identity.*Record identity"):
        build_submission(review, {})


def test_aggregate_rejects_a_mismatched_name_key() -> None:
    review = ReviewBoard(
        groups={"g": Group("g", "Group", True)},
        names={
            "Dictionary identity": NameRecord(
                "Record identity", "g", "exact", selected=True
            )
        },
    )
    rows = pd.DataFrame(
        [{"cleaned_name": "Dictionary identity", "rns": 1, "revenue": 2}]
    )

    with pytest.raises(ValueError, match="Dictionary identity.*Record identity"):
        aggregate_by_group(rows, review)


@pytest.mark.parametrize(
    "record",
    [
        NameRecord("Inventory", "stale-group", "exact"),
        NameRecord("Inventory", None, "exact", excluded=True),
    ],
)
def test_validate_rejects_stale_state_on_inventory_records(record: NameRecord) -> None:
    review = board(groups=[Group("stale-group", "Stale", True)], names=[record])

    assert validate_board(review) == [
        "Inventory is inventory but still has grouping or exclusion state"
    ]


def test_validate_allows_empty_groups_but_requires_titles_for_populated_groups() -> None:
    review = board(
        groups=[Group("populated", "  ", False), Group("empty", "", False)],
        names=[NameRecord("Alias", "populated", "suggested", selected=True)],
    )

    assert validate_board(review) == ["Group populated has a blank canonical title"]


def test_validate_orders_blank_populated_group_errors_deterministically() -> None:
    groups = [
        Group("group-b", "  ", False),
        Group("group-a", "  ", False),
    ]
    names = [
        NameRecord("First", "group-a", "suggested", selected=True),
        NameRecord("Second", "group-b", "suggested", selected=True),
    ]

    forward_errors = validate_board(board(groups=groups, names=names))
    reverse_errors = validate_board(board(groups=list(reversed(groups)), names=names))

    assert forward_errors == reverse_errors == [
        "Group group-a has a blank canonical title",
        "Group group-b has a blank canonical title",
    ]


def test_validate_rejects_unknown_group_and_excluded_grouped_names_in_name_order() -> None:
    review = board(
        groups=[Group("known", "Known", False)],
        names=[
            NameRecord("Zulu", "missing", "suggested", selected=True),
            NameRecord("Alpha", "known", "suggested", selected=True, excluded=True),
        ],
    )

    assert validate_board(review) == [
        "Alpha is both excluded and grouped",
        "Zulu references unknown group missing",
    ]


def test_validate_reports_both_errors_for_excluded_name_with_unknown_group() -> None:
    review = board(
        groups=[],
        names=[NameRecord("Alias", "missing", "suggested", selected=True, excluded=True)],
    )

    assert validate_board(review) == [
        "Alias is both excluded and grouped",
        "Alias references unknown group missing",
    ]


def test_validate_rejects_duplicate_populated_titles_by_normalized_title() -> None:
    review = board(
        groups=[
            Group("one", "Kake Hotels-Marketing", False),
            Group("two", "kake hotels marketing", False),
            Group("empty_duplicate", "KAKE HOTELS MARKETING", False),
        ],
        names=[
            NameRecord("First", "one", "suggested", selected=True),
            NameRecord("Second", "two", "suggested", selected=True),
        ],
    )

    assert validate_board(review) == [
        "Duplicate populated group title: Kake Hotels-Marketing / kake hotels marketing"
    ]


def test_validate_reports_a_populated_title_that_cannot_form_a_lookup_key() -> None:
    review = board(
        groups=[Group("suffix", "Ltd", False)],
        names=[NameRecord("Alias", "suffix", "suggested", selected=True)],
    )

    assert validate_board(review) == [
        "Group suffix title Ltd cannot form a lookup key"
    ]


def test_build_submission_rejects_title_that_cannot_form_a_lookup_key() -> None:
    review = board(
        groups=[Group("suffix", "Ltd", False)],
        names=[NameRecord("Alias", "suffix", "suggested", selected=True)],
    )

    with pytest.raises(
        ValueError, match="Group suffix title Ltd cannot form a lookup key"
    ):
        build_submission(review, {})


def test_build_submission_retains_existing_empty_groups_and_omits_new_empty_groups() -> None:
    review = board(
        groups=[
            Group("existing", "Existing", True),
            Group("new-empty", "New Empty", False),
            Group("new-used", "New Used", False),
        ],
        names=[NameRecord("Alias", "new-used", "suggested", selected=True)],
    )

    payload = build_submission(review, {})
    assert payload.groups == [
        {"id": "existing", "canonical_title": "Existing", "existing": True},
        {"id": "new-used", "canonical_title": "New Used", "existing": False},
    ]
    assert payload.mappings == [{"cleaned_name": "Alias", "group_id": "new-used"}]
    assert payload.unmap_names == []
    assert isinstance(payload.request_id, str)
    assert payload.request_id


def test_submission_payload_keeps_one_request_id_for_retries() -> None:
    payload = SubmissionPayload(
        groups=[
            {"id": "new-used", "canonical_title": "New Used", "existing": False},
        ],
        mappings=[{"cleaned_name": "Alias", "group_id": "new-used"}],
        unmap_names=[],
        request_id="11111111-1111-4111-8111-111111111111",
    )
    assert payload.request_id == "11111111-1111-4111-8111-111111111111"


def test_build_submission_only_unmaps_original_names_deliberately_returned_to_inventory() -> None:
    review = board(
        groups=[Group("g", "Group", True)],
        names=[
            NameRecord("Inventory", None, "exact"),
            NameRecord("Excluded", None, "exact", selected=True, excluded=True),
            NameRecord("Included", "g", "exact", selected=True),
        ],
    )

    payload = build_submission(
        review, {"Inventory": "g", "Excluded": "g", "Included": "g"}
    )

    assert payload.mappings == [{"cleaned_name": "Included", "group_id": "g"}]
    assert payload.unmap_names == ["Inventory"]


def test_build_submission_rejects_direct_remapping() -> None:
    review = board(
        groups=[Group("old", "Old", True), Group("new", "New", True)],
        names=[NameRecord("Alias", "new", "exact", selected=True)],
    )

    with pytest.raises(
        ValueError, match="Alias is already mapped to old and cannot be remapped to new"
    ):
        build_submission(review, {"Alias": "old"})


def test_build_submission_rejects_an_invalid_board() -> None:
    review = board(
        groups=[], names=[NameRecord("MTL", None, "unknown", selected=True)]
    )

    with pytest.raises(ValueError, match="MTL is included but ungrouped"):
        build_submission(review, {})


def test_aggregate_included_aliases_by_canonical_group_and_sort_by_revenue() -> None:
    review = board(
        groups=[
            Group("dnata", "DNATA", True),
            Group("other", "Other", True),
            Group("unused", "Unused", True),
        ],
        names=[
            NameRecord("DNATA Travel Group", "dnata", "exact", selected=True),
            NameRecord("DNATA_TRAVEL_GROUP", "dnata", "suggested", selected=True),
            NameRecord("Other Alias", "other", "exact", selected=True),
            NameRecord("Noise", None, "unknown", selected=True, excluded=True),
            NameRecord("Inventory", None, "unknown"),
        ],
    )
    rows = pd.DataFrame(
        [
            {"cleaned_name": "DNATA Travel Group", "rns": 2.0, "revenue": 100.0},
            {"cleaned_name": "DNATA_TRAVEL_GROUP", "rns": 3.0, "revenue": 150.0},
            {"cleaned_name": "Other Alias", "rns": 1.5, "revenue": 20.5},
            {"cleaned_name": "Noise", "rns": 99.0, "revenue": 999.0},
            {"cleaned_name": "Inventory", "rns": 50.0, "revenue": 500.0},
            {"cleaned_name": "DNATA Travel Group", "rns": 0.5, "revenue": 10.0},
        ]
    )

    result = aggregate_by_group(rows, review)

    expected = pd.DataFrame(
        [
            {"TRAVEL AGENT": "DNATA", "Sum of RNS": 5.5, "Sum of R REVENUE": 260.0},
            {"TRAVEL AGENT": "Other", "Sum of RNS": 1.5, "Sum of R REVENUE": 20.5},
        ]
    )
    pd.testing.assert_frame_equal(result, expected)


def test_aggregate_integer_inputs_produce_floating_totals() -> None:
    review = board(
        groups=[Group("dnata", "DNATA", True)],
        names=[
            NameRecord("DNATA Travel Group", "dnata", "exact", selected=True),
            NameRecord("DNATA_TRAVEL_GROUP", "dnata", "suggested", selected=True),
        ],
    )
    rows = pd.DataFrame(
        [
            {"cleaned_name": "DNATA Travel Group", "rns": 2, "revenue": 100},
            {"cleaned_name": "DNATA_TRAVEL_GROUP", "rns": 3, "revenue": 150},
        ]
    )

    result = aggregate_by_group(rows, review)

    assert result.loc[0, "Sum of RNS"] == 5.0
    assert result.loc[0, "Sum of R REVENUE"] == 250.0
    assert pd.api.types.is_float_dtype(result["Sum of RNS"])
    assert pd.api.types.is_float_dtype(result["Sum of R REVENUE"])
