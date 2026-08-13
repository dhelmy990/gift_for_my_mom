import io
from pathlib import Path

import pytest

from scripts.seed_name_mappings import SeedValidationError, load_seed_rows, run


def csv_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "seed.csv"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_seed_rows_cleans_inputs_and_trims_targets(tmp_path: Path) -> None:
    path = csv_file(tmp_path, "\ufeffinput_text,target_text,remarks\nAcme Pte Ltd,  Acme Group  ,ok\n")
    assert load_seed_rows(path) == [("Acme", "Acme Group")]


def test_load_seed_rows_rejects_blank_target_with_csv_line(tmp_path: Path) -> None:
    path = csv_file(tmp_path, "input_text,target_text,remarks\nAcme Ltd, ,missing\n")
    with pytest.raises(SeedValidationError, match=r"^row 2 has a blank target_text$"):
        load_seed_rows(path)


def test_load_seed_rows_rejects_contradictory_duplicate(tmp_path: Path) -> None:
    path = csv_file(tmp_path, "input_text,target_text,remarks\nAcme Ltd,One,x\nACME,Two,y\n")
    with pytest.raises(SeedValidationError, match="contradictory"):
        load_seed_rows(path)


def test_load_seed_rows_deduplicates_same_normalized_mapping(tmp_path: Path) -> None:
    path = csv_file(tmp_path, "input_text,target_text,remarks\nAcme Ltd,One,x\nACME,One!,y\n")
    assert load_seed_rows(path) == [("Acme", "One")]


def test_dry_run_uses_no_external_factories(tmp_path: Path) -> None:
    path = csv_file(tmp_path, "input_text,target_text,remarks\nAcme Ltd,One,x\nBeta,ONE!,y\n")
    output = io.StringIO()
    assert run(path, stdout=output, repository_factory=lambda *_: pytest.fail("repo"), embedding_factory=lambda: pytest.fail("embed")) == 0
    assert "2 mappings" in output.getvalue()
    assert "1 groups" in output.getvalue()


def test_apply_requires_supabase_environment(tmp_path: Path) -> None:
    path = csv_file(tmp_path, "input_text,target_text,remarks\nAcme Ltd,One,x\n")
    with pytest.raises(SeedValidationError, match="SUPABASE_URL"):
        run(path, apply=True, environ={})


def test_apply_embeds_cleaned_names_and_one_title_then_submits_once(tmp_path: Path) -> None:
    path = csv_file(tmp_path, "input_text,target_text,remarks\nAcme Pte Ltd,One,x\nBeta,ONE!,y\n")
    calls = []
    class Embedder:
        def embed(self, texts):
            calls.append(list(texts))
            return [[float(index)] * 384 for index in range(1, len(texts) + 1)]
    class Repo:
        def __init__(self): self.payloads = []
        def submit(self, payload): self.payloads.append(payload); return {"seed-1": "00000000-0000-4000-8000-000000000001"}
    repo = Repo()
    output = io.StringIO()
    result = run(path, apply=True, environ={"SUPABASE_URL": "url", "SUPABASE_SERVICE_KEY": "secret"}, repository_factory=lambda url, key: repo, embedding_factory=Embedder, stdout=output)
    assert result == 0
    assert calls == [["One", "Acme", "Beta"]]
    assert len(repo.payloads) == 1
    payload = repo.payloads[0]
    assert len(payload.groups) == 1
    assert payload.groups[0]["canonical_title"] == "One"
    assert payload.groups[0]["title_embedding"] == [1.0] * 384
    assert [item["cleaned_name"] for item in payload.mappings] == ["Acme", "Beta"]
    assert [item["member_embedding"][0] for item in payload.mappings] == [2.0, 3.0]
    assert payload.request_id
    assert "secret" not in output.getvalue()
