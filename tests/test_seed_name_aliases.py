import csv
import io
from pathlib import Path

import pytest

from company_names.cleaning import clean_company_name, normalize_lookup_key
from company_names.repository import AliasMapping
import scripts.seed_name_aliases as seed_module
from scripts.seed_name_aliases import SeedValidationError, load_alias_rows, seed_aliases


FIXTURE = Path("tests/fixtures/company_name_aliases.csv")


class FakeAliasRepository:
    def __init__(self) -> None:
        self.calls: list[list[AliasMapping]] = []

    def list_aliases(self) -> list[AliasMapping]:
        return []

    def upsert_aliases(self, mappings: list[AliasMapping]) -> None:
        self.calls.append(list(mappings))


def csv_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "aliases.csv"
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_all_24_pairs_as_alias_mappings() -> None:
    mappings = load_alias_rows(FIXTURE)

    assert len(mappings) == 24
    hktrm = next(item for item in mappings if item.cleaned_alias == "HKTRM")
    assert hktrm.alias_key == "hktrm"
    assert hktrm.canonical_name == "Hong Kong TUYI Business Travel Limited"


def test_seed_is_one_repeatable_upsert() -> None:
    repository = FakeAliasRepository()

    first_count = seed_aliases(FIXTURE, repository)
    second_count = seed_aliases(FIXTURE, repository)

    assert first_count == second_count == 24
    assert len(repository.calls) == 2
    assert repository.calls[0] == repository.calls[1]


def test_all_fixture_inputs_resolve_to_exact_targets_after_seed() -> None:
    mappings = load_alias_rows(FIXTURE)
    exact = {item.alias_key: item.canonical_name for item in mappings}

    with FIXTURE.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    assert [
        exact[normalize_lookup_key(clean_company_name(row["input_text"]))]
        for row in rows
    ] == [row["target_text"].strip() for row in rows]


def test_cleans_input_and_trims_but_preserves_target_text(tmp_path: Path) -> None:
    path = csv_file(
        tmp_path,
        "\ufeffinput_text,target_text,remarks\nAcme Pte Ltd,  ACME & Sons  ,reviewed\n",
    )

    assert load_alias_rows(path) == [AliasMapping("Acme", "acme", "ACME & Sons")]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("target_text,input_text,remarks\nOne,Acme,x\n", "header"),
        ("input_text,target_text\nAcme,One\n", "header"),
        ("input_text,target_text,remarks\nAcme,One,x,extra\n", "row 2"),
        ("input_text,target_text,remarks\nAcme\n", "row 2"),
        ("input_text,target_text,remarks\n,One,x\n", "row 2"),
        ("input_text,target_text,remarks\nAcme, ,x\n", "row 2"),
    ],
)
def test_rejects_invalid_csv_with_one_based_row_diagnostics(
    tmp_path: Path, body: str, message: str
) -> None:
    with pytest.raises(SeedValidationError, match=message):
        load_alias_rows(csv_file(tmp_path, body))


def test_invalid_value_diagnostic_is_csv_safe(tmp_path: Path) -> None:
    dangerous = "=HYPERLINK(\"https://example.invalid\")"
    path = csv_file(
        tmp_path,
        f'input_text,target_text,remarks\n"{dangerous.replace(chr(34), chr(34) * 2)}",,x\n',
    )

    with pytest.raises(SeedValidationError) as error:
        load_alias_rows(path)

    assert "row 2" in str(error.value)
    assert dangerous not in str(error.value)


def test_conflicting_normalized_alias_key_is_rejected(tmp_path: Path) -> None:
    path = csv_file(
        tmp_path,
        "input_text,target_text,remarks\nAcme Ltd,First,x\nACME,Second,y\n",
    )

    with pytest.raises(SeedValidationError, match=r"row 3.*row 2.*alias key 'acme'"):
        load_alias_rows(path)


def test_identical_duplicates_are_coalesced_deterministically(tmp_path: Path) -> None:
    first = csv_file(
        tmp_path,
        "input_text,target_text,remarks\nBeta,Two,x\nAcme Ltd,One,x\nACME,One,y\n",
    )
    second = tmp_path / "reordered.csv"
    second.write_text(
        "input_text,target_text,remarks\nACME,One,y\nAcme Ltd,One,x\nBeta,Two,x\n",
        encoding="utf-8",
    )

    assert load_alias_rows(first) == load_alias_rows(second) == [
        AliasMapping("ACME", "acme", "One"),
        AliasMapping("Beta", "beta", "Two"),
    ]


def test_cli_uses_arguments_over_environment_and_prints_only_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, str]] = []
    repository = FakeAliasRepository()
    monkeypatch.setenv("SUPABASE_URL", "environment-url")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "environment-secret")
    monkeypatch.setattr(
        seed_module.SupabaseAliasRepository,
        "from_credentials",
        lambda url, key: captured.append((url, key)) or repository,
    )
    output = io.StringIO()

    assert seed_module.main(
        [
            "--csv", str(FIXTURE),
            "--supabase-url", "argument-url",
            "--supabase-service-key", "argument-secret",
        ],
        stdout=output,
    ) == 0
    assert captured == [("argument-url", "argument-secret")]
    assert output.getvalue() == "24\n"


def test_cli_uses_environment_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, str]] = []
    repository = FakeAliasRepository()
    monkeypatch.setenv("SUPABASE_URL", "environment-url")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "environment-secret")
    monkeypatch.setattr(
        seed_module.SupabaseAliasRepository,
        "from_credentials",
        lambda url, key: captured.append((url, key)) or repository,
    )

    assert seed_module.main(["--csv", str(FIXTURE)], stdout=io.StringIO()) == 0
    assert captured == [("environment-url", "environment-secret")]


def test_cli_returns_nonzero_with_safe_error(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "super-secret-service-key"
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", secret)
    monkeypatch.setattr(
        seed_module.SupabaseAliasRepository,
        "from_credentials",
        lambda url, key: (_ for _ in ()).throw(RuntimeError(f"failed with {key}")),
    )
    output = io.StringIO()
    error = io.StringIO()

    assert seed_module.main(["--csv", str(FIXTURE)], stdout=output, stderr=error) != 0
    assert output.getvalue() == ""
    assert secret not in error.getvalue()
    assert error.getvalue().startswith("error:")
