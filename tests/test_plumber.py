from company_names.service import normalize_extracted_rows
from plumber import parse_agent_text_blocks


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


def test_parser_excludes_case_insensitively_and_skips_malformed_blocks():
    result = parse_agent_text_blocks(
        [block("Internal Staff"), "too short", block("Public Travel")],
        ["INTERNAL"],
    )

    assert result["TRAVEL AGENT"].tolist() == ["Public Travel"]
