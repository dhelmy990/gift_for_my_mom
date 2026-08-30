from dataclasses import FrozenInstanceError

import pytest

from company_names.aliases import FUZZY_THRESHOLD, AliasSuggestion, suggest_alias
from company_names.repository import AliasMapping


HKTRM = AliasMapping(
    cleaned_alias="HKTRM",
    alias_key="hktrm",
    canonical_name="Hong Kong TUYI Business Travel Limited",
)


def test_close_spelling_variant_suggests_saved_destination() -> None:
    suggestion = suggest_alias("HKTRMs", [HKTRM])

    assert suggestion is not None
    assert suggestion.saved_alias == "HKTRM"
    assert suggestion.canonical_name == "Hong Kong TUYI Business Travel Limited"
    assert suggestion.score >= FUZZY_THRESHOLD


def test_low_similarity_is_hidden() -> None:
    assert suggest_alias("Miki Travel", [HKTRM]) is None


def test_equal_best_scores_are_left_unresolved_regardless_of_order() -> None:
    aliases = [
        AliasMapping("HKTRM A", "hktrm a", "Company A"),
        AliasMapping("HKTRM B", "hktrm b", "Company B"),
    ]

    assert suggest_alias("HKTRM C", aliases, threshold=80.0) is None
    assert suggest_alias("HKTRM C", list(reversed(aliases)), threshold=80.0) is None


def test_exact_alias_is_not_returned_as_a_suggestion() -> None:
    assert suggest_alias("hktrm", [HKTRM]) is None


def test_suggestion_is_frozen() -> None:
    suggestion = suggest_alias("HKTRMs", [HKTRM])

    assert suggestion is not None
    with pytest.raises(FrozenInstanceError):
        suggestion.score = 0.0  # type: ignore[misc]


@pytest.mark.parametrize("threshold", [-0.1, 100.1, float("nan"), float("inf")])
def test_threshold_must_be_a_finite_percentage(threshold: float) -> None:
    with pytest.raises(ValueError, match="threshold must be between 0 and 100"):
        suggest_alias("HKTRMs", [HKTRM], threshold=threshold)
