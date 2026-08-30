import csv
from pathlib import Path

import pytest

from company_names.cleaning import clean_company_name, normalize_lookup_key


ALIAS_FIXTURE = Path(__file__).parent / "fixtures" / "company_name_aliases.csv"


def test_company_alias_fixture_freezes_source_corpus() -> None:
    with ALIAS_FIXTURE.open(newline="", encoding="utf-8") as fixture:
        reader = csv.DictReader(fixture)
        rows = list(reader)

    assert reader.fieldnames == ["input_text", "target_text", "remarks"]
    assert len(rows) == 24
    assert (rows[0]["input_text"], rows[0]["target_text"]) == (
        "HOTELBEDS101",
        "HOTELBEDS",
    )
    assert next(
        row["target_text"] for row in rows if row["input_text"] == "HKTRM"
    ) == "Hong Kong TUYI Business Travel Limited"


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        ("Kake Hotels Marketing Co.,LtdRoom", "Kake Hotels Marketing"),
        ("Miki Travel LtdVintners Place", "Miki Travel"),
        ("Within Earth Holidays Sdn BhdSuite", "Within Earth Holidays"),
        (
            "Betoptop GmbHBüro Kornwestheim Stammheimer Straße",
            "Betoptop",
        ),
        ("Hong Thai Travel Services (S) Pte", "Hong Thai Travel Services (S)"),
        (
            "TRVCTravco Corporation Limited Travco House,",
            "TRVCTravco Corporation",
        ),
        ("MMK SG PTE", "MMK SG"),
        ("  DNATA__Travel   Group  ", "DNATA Travel Group"),
    ],
)
def test_clean_company_name(raw_name: str, expected: str) -> None:
    assert clean_company_name(raw_name) == expected


def test_clean_company_name_rejects_suffix_only_name() -> None:
    with pytest.raises(ValueError, match="empty"):
        clean_company_name("Pte Ltd")


def test_clean_company_name_removes_lowercase_non_ascii_trailing_text() -> None:
    assert clean_company_name("Betoptop GmbHüber den Dächern") == "Betoptop"


def test_clean_company_name_preserves_suffix_like_hyphenated_word() -> None:
    assert clean_company_name("Acme co-op") == "Acme co-op"


def test_clean_company_name_removes_lowercase_ascii_trailing_text() -> None:
    assert clean_company_name("Acme LTDroom 12") == "Acme"


@pytest.mark.parametrize("name", ["cobalt", "company", "co-op"])
def test_clean_company_name_preserves_ambiguous_co_words(name: str) -> None:
    assert clean_company_name(name) == name


def test_clean_company_name_does_not_treat_compass_prefix_as_co_suffix() -> None:
    assert (
        clean_company_name("COMPASS TRAVEL & TOUR PTE LTD")
        == "COMPASS TRAVEL & TOUR"
    )


@pytest.mark.parametrize("name", ["HKTRM", "MTLVintners Place"])
def test_clean_company_name_does_not_infer_aliases(name: str) -> None:
    assert clean_company_name(name) == name


def test_clean_company_name_removes_suffix_wrapper_punctuation() -> None:
    assert clean_company_name("Acme (Pte Ltd)") == "Acme"


def test_normalize_lookup_key() -> None:
    assert normalize_lookup_key("Kake Hotels-Marketing") == "kake hotels marketing"


def test_normalize_lookup_key_rejects_empty_key() -> None:
    with pytest.raises(ValueError, match="empty"):
        normalize_lookup_key("()")
