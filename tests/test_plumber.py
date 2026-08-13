from company_names.service import normalize_extracted_rows
from plumber import parse_agent_text_blocks, parse_agent_text_blocks_with_issues


def block(name, rns="2", revenue="$10.50"):
    return f"{name} 123\nDetail\nRoom Nights {rns}\nMore Detail\nRevenue {revenue}"


def test_parser_preserves_duplicate_agent_occurrences():
    result = parse_agent_text_blocks([block("Acme"), block("Acme", "3", "$20")], [])

    assert result.to_dict("records") == [
        {"TRAVEL AGENT": "Acme", "Sum of RNS": 2.0, "Sum of R REVENUE": 10.5},
        {"TRAVEL AGENT": "Acme", "Sum of RNS": 3.0, "Sum of R REVENUE": 20.0},
    ]
    assert normalize_extracted_rows(result).to_dict("records") == [
        {"cleaned_name": "Acme", "rns": 5.0, "revenue": 30.5}
    ]


def test_parser_excludes_case_insensitively_and_reports_malformed_blocks():
    parsed = parse_agent_text_blocks_with_issues(
        [block("Internal Staff"), "too short", block("Public Travel")],
        ["INTERNAL"],
    )

    assert parsed.rows["TRAVEL AGENT"].tolist() == ["Public Travel"]
    assert [(issue.block_index, issue.reason) for issue in parsed.issues] == [
        (1, "expected at least three lines")
    ]


def test_parser_preserves_negative_currency_and_accounting_values():
    parsed = parse_agent_text_blocks_with_issues(
        [block("Refund Co", "-1,234.5", "($2,345.60)")], []
    )

    assert parsed.rows.iloc[0].to_dict() == {
        "TRAVEL AGENT": "Refund Co",
        "Sum of RNS": -1234.5,
        "Sum of R REVENUE": -2345.6,
    }
    assert parsed.issues == []


def test_parser_reports_malformed_and_nonfinite_numbers():
    parsed = parse_agent_text_blocks_with_issues(
        [block("Bad", "--2", "$10"), block("Infinite", "2", "inf")], []
    )
    assert parsed.rows.empty
    assert [issue.block_index for issue in parsed.issues] == [0, 1]
