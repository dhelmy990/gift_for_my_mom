import pytest

from company_names.cleaning import clean_company_name, normalize_lookup_key


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


def test_clean_company_name_removes_suffix_wrapper_punctuation() -> None:
    assert clean_company_name("Acme (Pte Ltd)") == "Acme"


def test_normalize_lookup_key() -> None:
    assert normalize_lookup_key("Kake Hotels-Marketing") == "kake hotels marketing"


def test_normalize_lookup_key_rejects_empty_key() -> None:
    with pytest.raises(ValueError, match="empty"):
        normalize_lookup_key("()")
