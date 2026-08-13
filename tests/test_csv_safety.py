import csv
import io

import pytest

from company_names.csv_safety import CSV_SAFE_PREFIX, csv_safe_cell, csv_unsafe_cell


@pytest.mark.parametrize(
    "value",
    [
        "=SUM(A1:A2)",
        "+cmd",
        "-10+20",
        "@lookup",
        "  =leading whitespace",
        "\t+tab",
        "normal apostrophe's",
        "'=already spreadsheet safe",
        "雪だるま ☃",
        CSV_SAFE_PREFIX + "original reserved prefix",
    ],
)
def test_csv_cell_encoding_round_trips_without_ambiguity(value):
    assert csv_unsafe_cell(csv_safe_cell(value)) == value


@pytest.mark.parametrize("value", ["Acme", "normal apostrophe's", "'=safe", "雪だるま ☃"])
def test_non_dangerous_cells_remain_readable(value):
    assert csv_safe_cell(value) == value


def test_dangerous_and_reserved_cells_use_spreadsheet_safe_prefix():
    for value in ("=formula", "  +formula", CSV_SAFE_PREFIX + "original"):
        encoded = csv_safe_cell(value)
        assert encoded.startswith(CSV_SAFE_PREFIX)
        assert encoded.lstrip()[0] not in "=+-@"


@pytest.mark.parametrize(
    "malformed",
    [CSV_SAFE_PREFIX, CSV_SAFE_PREFIX + "%%%", CSV_SAFE_PREFIX + "_", CSV_SAFE_PREFIX + "abc"],
)
def test_malformed_reserved_encoding_is_left_unchanged(malformed):
    assert csv_unsafe_cell(malformed) == malformed


def test_csv_encoding_preserves_exactly_two_columns():
    output = io.StringIO(newline="")
    csv.writer(output).writerow([csv_safe_cell("=one"), csv_safe_cell("+two")])
    assert len(next(csv.reader(io.StringIO(output.getvalue())))) == 2
