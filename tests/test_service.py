from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

from company_names.repository import AliasMapping, RepositoryUnavailableError
from company_names.service import (
    AliasReviewRow,
    ServiceValidationError,
    aggregate_resolved_rows,
    collate_extracted_rows,
    normalize_extracted_rows,
    prepare_aliases,
    save_alias_changes,
)


class FakeAliasRepository:
    def __init__(self, aliases: list[AliasMapping]) -> None:
        self.aliases = aliases
        self.saved: list[AliasMapping] = []

    def list_aliases(self) -> list[AliasMapping]:
        return list(self.aliases)

    def upsert_aliases(self, mappings: list[AliasMapping]) -> None:
        self.saved.extend(mappings)


class FailingAliasRepository(FakeAliasRepository):
    def __init__(self, message: str) -> None:
        super().__init__([])
        self.message = message

    def list_aliases(self) -> list[AliasMapping]:
        raise RepositoryUnavailableError(self.message)


def extracted_rows(values=None) -> pd.DataFrame:
    values = values or [
        ("Acme Pte Ltd", 1, 10.25),
        ("Acme", 2.5, 20),
        ("Unknown Co Ltd", 4, 40),
    ]
    return pd.DataFrame(values, columns=[
        "TRAVEL AGENT", "Sum of RNS", "Sum of R REVENUE"
    ])


def test_normalize_cleans_and_sums_duplicate_names_as_floats() -> None:
    assert normalize_extracted_rows(extracted_rows()).to_dict("records") == [
        {"cleaned_name": "Acme", "rns": 3.5, "revenue": 30.25},
        {"cleaned_name": "Unknown", "rns": 4.0, "revenue": 40.0},
    ]


@pytest.mark.parametrize(
    ("columns", "values"),
    [
        (("agent_name", "rns", "revenue"), ("Acme", 2, 10)),
        (("cleaned_name", "rns", "revenue"), ("Acme", 2, 10)),
    ],
)
def test_normalize_accepts_alternate_column_sets(columns, values) -> None:
    result = normalize_extracted_rows(pd.DataFrame([values], columns=columns))
    assert result.to_dict("records") == [
        {"cleaned_name": "Acme", "rns": 2.0, "revenue": 10.0}
    ]


def test_collate_extracted_rows_groups_agent_column_not_dataframe_indexes() -> None:
    result = collate_extracted_rows([
        extracted_rows([("Acme", 2, 10)]),
        extracted_rows([("Acme", 3, 20)]),
    ])
    assert result.to_dict("records") == [{
        "TRAVEL AGENT": "Acme",
        "Sum of RNS": 5.0,
        "Sum of R REVENUE": 30.0,
    }]


def test_collate_extracted_rows_returns_expected_empty_columns() -> None:
    assert list(collate_extracted_rows([]).columns) == [
        "TRAVEL AGENT", "Sum of RNS", "Sum of R REVENUE"
    ]


@pytest.mark.parametrize("value", ["bad", float("nan"), float("inf")])
def test_normalize_rejects_invalid_numeric_values(value) -> None:
    rows = pd.DataFrame({"agent_name": ["Acme"], "rns": [value], "revenue": [1]})
    with pytest.raises(ServiceValidationError, match="numeric"):
        normalize_extracted_rows(rows)


def test_normalize_identifies_source_and_value_for_suffix_only_company_name() -> None:
    rows = extracted_rows([("Pte Ltd", 2, 10)]).assign(_source_file="hotel-report.pdf")
    with pytest.raises(ServiceValidationError) as caught:
        normalize_extracted_rows(rows)
    message = str(caught.value)
    assert "Row 1" in message
    assert "hotel-report.pdf" in message
    assert "Pte Ltd" in message
    assert "empty after cleanup" in message


def test_normalize_rejects_nonfinite_grouped_totals() -> None:
    rows = pd.DataFrame({
        "agent_name": ["Acme", "Acme Ltd"],
        "rns": [1e308, 1e308],
        "revenue": [1, 1],
    })
    with pytest.raises(ServiceValidationError, match="aggregate"):
        normalize_extracted_rows(rows)


def test_exact_alias_is_authoritative() -> None:
    repository = FakeAliasRepository([
        AliasMapping("HKTRM", "hktrm", "Hong Kong TUYI Business Travel Limited")
    ])
    prepared = prepare_aliases(extracted_rows([("HKTRM", 2, 100)]), repository)
    assert prepared.review_rows == [AliasReviewRow(
        "HKTRM", "Hong Kong TUYI Business Travel Limited", "saved", None
    )]


def test_exact_alias_uses_normalized_key() -> None:
    repository = FakeAliasRepository([
        AliasMapping("H K T R M", "h k t r m", "Canonical")
    ])
    prepared = prepare_aliases(extracted_rows([("h k t r m", 2, 100)]), repository)
    assert prepared.review_rows[0].final_name == "Canonical"
    assert prepared.review_rows[0].status == "saved"


def test_unknown_name_defaults_to_cleaned_name_with_suggestion() -> None:
    repository = FakeAliasRepository([
        AliasMapping("HKTRM", "hktrm", "Hong Kong TUYI Business Travel Limited")
    ])
    prepared = prepare_aliases(
        extracted_rows([("HKTRMs Pte Ltd", 2, 100)]), repository
    )
    row = prepared.review_rows[0]
    assert row.cleaned_name == "HKTRMs"
    assert row.final_name == "HKTRMs"
    assert row.status == "suggested"
    assert row.suggestion is not None
    assert row.suggestion.canonical_name == "Hong Kong TUYI Business Travel Limited"


def test_unknown_without_suggestion_is_new() -> None:
    prepared = prepare_aliases(
        extracted_rows([("Miki Travel", 2, 100)]), FakeAliasRepository([])
    )
    assert prepared.review_rows == [AliasReviewRow(
        "Miki Travel", "Miki Travel", "new", None
    )]


def test_database_failure_keeps_cleaned_rows_available() -> None:
    prepared = prepare_aliases(
        extracted_rows([("Miki Travel Pte Ltd", 2, 100)]),
        FailingAliasRepository("table missing"),
    )
    assert prepared.database_available is False
    assert prepared.database_error == "table missing"
    assert prepared.review_rows == [AliasReviewRow(
        "Miki Travel", "Miki Travel", "new", None
    )]


def test_no_repository_is_a_cleaned_database_unavailable_fallback() -> None:
    prepared = prepare_aliases(extracted_rows([("Miki Travel", 2, 100)]), None)
    assert prepared.database_available is False
    assert prepared.database_error is None
    assert prepared.review_rows[0].final_name == "Miki Travel"


def test_alias_review_rows_are_frozen() -> None:
    row = AliasReviewRow("Alias", "Final", "new", None)
    with pytest.raises(FrozenInstanceError):
        row.final_name = "Changed"  # type: ignore[misc]


def test_prepared_review_rows_support_intentional_session_edits() -> None:
    prepared = prepare_aliases(extracted_rows([("Acme", 2, 100)]), None)

    prepared.review_rows.append(AliasReviewRow("Other", "Other", "new", None))

    assert [row.cleaned_name for row in prepared.review_rows] == ["Acme", "Other"]


def test_resolved_names_combine_and_sum() -> None:
    rows = pd.DataFrame([
        {"cleaned_name": "HKTRM", "rns": 2.0, "revenue": 100.0},
        {"cleaned_name": "HKTRMs", "rns": 3.5, "revenue": 50.25},
    ])
    result = aggregate_resolved_rows(rows, {
        "HKTRM": "Hong Kong TUYI Business Travel Limited",
        "HKTRMs": "Hong Kong TUYI Business Travel Limited",
    })
    assert result.to_dict("records") == [{
        "TRAVEL AGENT": "Hong Kong TUYI Business Travel Limited",
        "Sum of RNS": 5.5,
        "Sum of R REVENUE": 150.25,
    }]


def test_aggregate_requires_complete_mapping() -> None:
    rows = pd.DataFrame([{"cleaned_name": "HKTRM", "rns": 2.0, "revenue": 100.0}])
    with pytest.raises(ServiceValidationError, match="Every cleaned company name"):
        aggregate_resolved_rows(rows, {})


@pytest.mark.parametrize("invalid", ["", "   ", None, 7])
def test_aggregate_rejects_invalid_final_names(invalid) -> None:
    rows = pd.DataFrame([
        {"cleaned_name": "HKTRM", "rns": 2.0, "revenue": 100.0}
    ])
    with pytest.raises(ServiceValidationError, match="final company name"):
        aggregate_resolved_rows(rows, {"HKTRM": invalid})


def test_aggregate_names_every_missing_or_blank_final_name() -> None:
    rows = pd.DataFrame([
        {"cleaned_name": "A", "rns": 1.0, "revenue": 10.0},
        {"cleaned_name": "B", "rns": 1.0, "revenue": 20.0},
        {"cleaned_name": "C", "rns": 1.0, "revenue": 30.0},
    ])
    with pytest.raises(ServiceValidationError) as caught:
        aggregate_resolved_rows(rows, {"A": "Final", "B": "  ", "C": None})
    assert str(caught.value) == (
        "Every cleaned company name needs a final company name. "
        "Missing or blank: B, C"
    )


def test_aggregate_names_deduplicates_missing_or_blank_final_name() -> None:
    rows = pd.DataFrame([
        {"cleaned_name": "A", "rns": 1.0, "revenue": 10.0},
        {"cleaned_name": "A", "rns": 2.0, "revenue": 20.0},
    ])
    with pytest.raises(ServiceValidationError) as caught:
        aggregate_resolved_rows(rows, {})
    assert str(caught.value) == (
        "Every cleaned company name needs a final company name. "
        "Missing or blank: A"
    )


def test_aggregate_trims_final_names_before_grouping() -> None:
    rows = pd.DataFrame([
        {"cleaned_name": "First", "rns": 2.0, "revenue": 100.0},
        {"cleaned_name": "Second", "rns": 3.0, "revenue": 50.0},
    ])
    result = aggregate_resolved_rows(
        rows, {"First": " Canonical ", "Second": "Canonical"}
    )
    assert result.to_dict("records") == [{
        "TRAVEL AGENT": "Canonical",
        "Sum of RNS": 5.0,
        "Sum of R REVENUE": 150.0,
    }]


def test_save_trims_titles_upserts_aliases_and_returns_updated_totals() -> None:
    prepared = prepare_aliases(extracted_rows([("HKTRMs", 2, 100)]), None)
    repository = FakeAliasRepository([])
    result = save_alias_changes(
        prepared,
        {"HKTRMs": "  Hong Kong TUYI Business Travel Limited  "},
        repository,
    )
    assert repository.saved == [AliasMapping(
        "HKTRMs", "hktrms", "Hong Kong TUYI Business Travel Limited"
    )]
    assert result["TRAVEL AGENT"].tolist() == [
        "Hong Kong TUYI Business Travel Limited"
    ]


def test_save_coalesces_matching_normalized_alias_keys_in_report_order() -> None:
    prepared = prepare_aliases(
        extracted_rows([("Acme", 2, 100), ("ACME", 3, 50)]), None
    )
    repository = FakeAliasRepository([])

    result = save_alias_changes(
        prepared, {"Acme": " Canonical ", "ACME": "Canonical"}, repository
    )

    assert repository.saved == [AliasMapping("Acme", "acme", "Canonical")]
    assert result.to_dict("records") == [{
        "TRAVEL AGENT": "Canonical",
        "Sum of RNS": 5.0,
        "Sum of R REVENUE": 150.0,
    }]


def test_save_rejects_conflicting_targets_for_one_normalized_alias_key() -> None:
    prepared = prepare_aliases(
        extracted_rows([("Acme", 2, 100), ("ACME", 3, 50)]), None
    )
    repository = FakeAliasRepository([])

    with pytest.raises(ServiceValidationError, match="same alias key"):
        save_alias_changes(
            prepared, {"Acme": "First", "ACME": "Second"}, repository
        )

    assert repository.saved == []


def test_save_names_a_missing_alias_before_repository_write() -> None:
    prepared = prepare_aliases(extracted_rows([("A", 1, 10), ("B", 1, 20)]), None)
    repository = FakeAliasRepository([])
    with pytest.raises(ServiceValidationError, match=r"Missing or blank: B$"):
        save_alias_changes(prepared, {"A": "Final"}, repository)
    assert repository.saved == []


def test_save_rejects_an_unexpected_alias_before_repository_write() -> None:
    prepared = prepare_aliases(extracted_rows([("A", 1, 10)]), None)
    repository = FakeAliasRepository([])
    with pytest.raises(
        ServiceValidationError,
        match=r"unexpected cleaned names: Stale alias$",
    ):
        save_alias_changes(
            prepared,
            {"A": "Final", "Stale alias": "Old value"},
            repository,
        )
    assert repository.saved == []


def test_save_rejects_non_text_unexpected_alias_before_repository_write() -> None:
    prepared = prepare_aliases(extracted_rows([("A", 1, 10)]), None)
    repository = FakeAliasRepository([])
    with pytest.raises(
        ServiceValidationError,
        match=r"unexpected cleaned names: 7, Older$",
    ):
        save_alias_changes(
            prepared, {"A": "Final", 7: "Stale", "Older": "Old"}, repository
        )
    assert repository.saved == []


def test_prepared_rows_do_not_share_mutable_state_with_normalized_input() -> None:
    normalized = pd.DataFrame([
        {"cleaned_name": "Acme", "rns": 2.0, "revenue": 100.0}
    ])
    prepared = prepare_aliases(normalized, None)

    normalized.loc[0, "rns"] = 999.0

    assert prepared.rows.loc[0, "rns"] == 2.0


@pytest.mark.parametrize("final_names", [{}, {"HKTRM": "   "}, {"HKTRM": 4}])
def test_invalid_or_incomplete_final_name_is_rejected_before_write(final_names) -> None:
    prepared = prepare_aliases(extracted_rows([("HKTRM", 2, 100)]), None)
    repository = FakeAliasRepository([])
    with pytest.raises(ServiceValidationError, match="final company name"):
        save_alias_changes(prepared, final_names, repository)
    assert repository.saved == []
