import pandas as pd
import pytest

from company_names.models import Group, NameRecord, ReviewBoard, SubmissionPayload
from company_names.review import (
    aggregate_by_group,
    build_submission,
    materialize_singletons,
    singleton_group_id,
    validate_board,
    validate_submission,
)


def board(*, groups: list[Group], names: list[NameRecord]) -> ReviewBoard:
    return ReviewBoard(
        groups={group.id: group for group in groups},
        names={name.cleaned_name: name for name in names},
    )


def test_validate_accepts_separate_and_working_tray_names() -> None:
    review = board(
        groups=[],
        names=[
            NameRecord("MTL", None, "unknown", selected=True),
            NameRecord("Inventory", None, "unknown"),
        ],
    )

    assert validate_board(review) == []


def test_singleton_group_id_is_a_stable_sha256_identifier() -> None:
    assert singleton_group_id("Alpha Travel") == (
        "new-singleton-"
        "59ca1eb4da3e7e4a0ac2143c4ce27bef3a147201fea8282043df6051ad16646d"
    )


def test_materialize_singletons_returns_a_distinct_deterministic_board() -> None:
    review = board(
        groups=[],
        names=[
            NameRecord("Zulu Travel", None, "unknown"),
            NameRecord("Alpha Travel", None, "unknown"),
        ],
    )

    first = materialize_singletons(review)
    second = materialize_singletons(review)
    alpha_id = singleton_group_id("Alpha Travel")
    zulu_id = singleton_group_id("Zulu Travel")

    assert first is not review
    assert first.names["Alpha Travel"] is not review.names["Alpha Travel"]
    assert review.groups == {}
    assert review.names["Alpha Travel"].selected is False
    assert review.names["Alpha Travel"].group_id is None
    assert first == second
    assert materialize_singletons(first) == first
    assert list(first.groups) == [alpha_id, zulu_id]
    assert first.groups == {
        alpha_id: Group(alpha_id, "Alpha Travel", False),
        zulu_id: Group(zulu_id, "Zulu Travel", False),
    }
    assert first.names["Alpha Travel"].selected is True
    assert first.names["Alpha Travel"].group_id == alpha_id
    assert first.names["Zulu Travel"].selected is True
    assert first.names["Zulu Travel"].group_id == zulu_id


def test_materialize_singletons_leaves_grouped_and_excluded_names_unchanged() -> None:
    review = board(
        groups=[Group("exact", "Exact Group", True)],
        names=[
            NameRecord("Exact Alias", "exact", "exact", selected=True),
            NameRecord("Excluded", None, "unknown", selected=True, excluded=True),
        ],
    )

    materialized = materialize_singletons(review)

    assert materialized == review
    assert materialized is not review
    assert materialized.groups["exact"] is not review.groups["exact"]
    assert materialized.names["Exact Alias"] is not review.names["Exact Alias"]


def test_materialize_singletons_rejects_a_conflicting_derived_group_id() -> None:
    singleton_id = singleton_group_id("Alpha Travel")
    review = board(
        groups=[Group(singleton_id, "Different Company", False)],
        names=[NameRecord("Alpha Travel", None, "unknown")],
    )

    with pytest.raises(
        ValueError,
        match=(
            "Cannot materialize singleton for Alpha Travel: derived group ID .* "
            "conflicts with an existing group"
        ),
    ):
        materialize_singletons(review)

    assert review.groups[singleton_id].canonical_title == "Different Company"
    assert review.names["Alpha Travel"].selected is False
    assert review.names["Alpha Travel"].group_id is None


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (
            [NameRecord("Alpha", None, "unknown", selected=True)],
            "Resolve 1 name in the working tray: create a combined group or "
            "return them to Separate companies.",
        ),
        (
            [
                NameRecord("Zulu", None, "unknown", selected=True),
                NameRecord("Alpha", None, "unknown", selected=True),
            ],
            "Resolve 2 names in the working tray: create a combined group or "
            "return them to Separate companies.",
        ),
    ],
)
def test_validate_submission_rejects_names_in_working_tray(
    names: list[NameRecord], expected: str
) -> None:
    assert validate_submission(board(groups=[], names=names)) == [expected]


def test_validate_submission_accepts_an_empty_working_tray() -> None:
    review = board(
        groups=[Group("g", "Combined", False)],
        names=[
            NameRecord("Separate", None, "unknown"),
            NameRecord("Grouped", "g", "suggested", selected=True),
            NameRecord("Excluded", None, "unknown", selected=True, excluded=True),
        ],
    )

    assert validate_submission(review) == []


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


def test_build_submission_reuses_an_explicit_request_id() -> None:
    review = board(
        groups=[Group("new", "New Group", False)],
        names=[NameRecord("Alias", "new", "suggested", selected=True)],
    )
    first = build_submission(review, {})
    retry = build_submission(review, {}, request_id=first.request_id)

    assert retry == first


@pytest.mark.parametrize("request_id", ["", "not-a-uuid", "11111111-1111-4111-8111-11111111111z"])
def test_submission_payload_rejects_invalid_request_id(request_id: str) -> None:
    with pytest.raises(ValueError, match="request_id must be a UUID"):
        SubmissionPayload([], [], [], request_id)


def test_build_submission_materializes_unseen_separate_names_deterministically() -> None:
    review = board(
        groups=[],
        names=[
            NameRecord("Beta", None, "unknown"),
            NameRecord("Alpha", None, "unknown"),
        ],
    )

    payload = build_submission(review, {})

    alpha_id = singleton_group_id("Alpha")
    beta_id = singleton_group_id("Beta")
    assert payload.groups == sorted(
        [
            {"id": alpha_id, "canonical_title": "Alpha", "existing": False},
            {"id": beta_id, "canonical_title": "Beta", "existing": False},
        ],
        key=lambda group: group["id"],
    )
    assert payload.mappings == [
        {"cleaned_name": "Alpha", "group_id": alpha_id},
        {"cleaned_name": "Beta", "group_id": beta_id},
    ]
    assert payload.unmap_names == []
    assert review.groups == {}


def test_build_submission_keeps_unchanged_exact_mapping_without_unmapping() -> None:
    review = board(
        groups=[Group("g", "Group", True)],
        names=[NameRecord("Miki", "g", "exact", selected=True)],
    )

    payload = build_submission(review, {"Miki": "g"})

    assert payload.mappings == [{"cleaned_name": "Miki", "group_id": "g"}]
    assert payload.unmap_names == []


def test_build_submission_preserves_persisted_identity_for_an_exact_alias() -> None:
    review = board(
        groups=[Group("g", "Miki", True)],
        names=[
            NameRecord(
                "Miki Travel", "g", "exact", selected=True,
                persisted_name="Miki-Travel",
            )
        ],
    )

    payload = build_submission(review, {"Miki Travel": "g"})

    assert payload.mappings == [{"cleaned_name": "Miki-Travel", "group_id": "g"}]


def test_build_submission_remaps_exact_alias_to_singleton_atomically() -> None:
    review = board(
        groups=[Group("g", "Stored", True)],
        names=[NameRecord("Stored Alias", None, "exact", persisted_name="Stored-Alias")],
    )

    payload = build_submission(review, {"Stored Alias": "g"})

    singleton_id = singleton_group_id("Stored Alias")
    assert payload.groups == [
        {"id": "g", "canonical_title": "Stored", "existing": True},
        {
            "id": singleton_id,
            "canonical_title": "Stored Alias",
            "existing": False,
        },
    ]
    assert payload.mappings == [
        {"cleaned_name": "Stored-Alias", "group_id": singleton_id}
    ]
    assert payload.unmap_names == ["Stored-Alias"]


def test_aggregate_uses_report_identity_when_exact_alias_has_persisted_identity() -> None:
    review = board(
        groups=[Group("g", "Miki", True)],
        names=[
            NameRecord(
                "Miki Travel", "g", "exact", selected=True,
                persisted_name="Miki-Travel",
            )
        ],
    )
    rows = pd.DataFrame(
        [{"cleaned_name": "Miki Travel", "rns": 2.0, "revenue": 8.0}]
    )

    result = aggregate_by_group(rows, review)

    assert result.to_dict("records") == [
        {"TRAVEL AGENT": "Miki", "Sum of RNS": 2.0, "Sum of R REVENUE": 8.0}
    ]


def test_build_submission_remaps_directly_between_groups_atomically() -> None:
    review = board(
        groups=[Group("old", "Old", True), Group("new", "New", True)],
        names=[
            NameRecord(
                "Stored Alias",
                "new",
                "exact",
                selected=True,
                persisted_name="Stored-Alias",
            )
        ],
    )

    payload = build_submission(review, {"Stored Alias": "old"})

    assert payload.mappings == [
        {"cleaned_name": "Stored-Alias", "group_id": "new"}
    ]
    assert payload.unmap_names == ["Stored-Alias"]


def test_build_submission_exclusion_is_report_only_for_an_exact_alias() -> None:
    review = board(
        groups=[Group("old", "Old", True)],
        names=[
            NameRecord(
                "Stored Alias",
                None,
                "exact",
                selected=True,
                excluded=True,
                persisted_name="Stored-Alias",
            )
        ],
    )

    payload = build_submission(review, {"Stored Alias": "old"})

    assert payload.mappings == []
    assert payload.unmap_names == []


def test_build_submission_retry_is_byte_equivalent_after_singleton_remap() -> None:
    review = board(
        groups=[Group("old", "Old", True)],
        names=[
            NameRecord(
                "Stored Alias", None, "exact", persisted_name="Stored-Alias"
            ),
            NameRecord("Beta", None, "unknown"),
        ],
    )

    first = build_submission(review, {"Stored Alias": "old"})
    retry = build_submission(
        review, {"Stored Alias": "old"}, request_id=first.request_id
    )

    assert retry == first


def test_build_submission_rejects_an_invalid_board() -> None:
    review = board(
        groups=[], names=[NameRecord("MTL", "missing", "unknown", selected=True)]
    )

    with pytest.raises(ValueError, match="MTL references unknown group missing"):
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
