import io
from pathlib import Path

import pytest
from uuid import UUID

import scripts.seed_name_mappings as seed_module
from scripts.seed_name_mappings import EMBEDDING_BATCH_SIZE, SeedValidationError, _with_embeddings, load_seed_rows, run


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


@pytest.mark.parametrize("body", ["input_text,target_text,remarks\n", ""])
def test_load_seed_rows_rejects_empty_mapping_set(tmp_path: Path, body: str) -> None:
    with pytest.raises(SeedValidationError):
        load_seed_rows(csv_file(tmp_path, body))


@pytest.mark.parametrize("body", [
    "target_text,input_text,remarks\nOne,Acme,x\n",
    "input_text,target_text,remarks\nAcme,One,x,extra\n",
    "input_text,target_text,remarks\nAcme\n",
])
def test_load_seed_rows_rejects_malformed_headers_and_rows(tmp_path: Path, body: str) -> None:
    with pytest.raises(SeedValidationError):
        load_seed_rows(csv_file(tmp_path, body))


def test_reordered_logical_csv_builds_byte_equivalent_payload(tmp_path: Path) -> None:
    first = csv_file(tmp_path, "input_text,target_text,remarks\nBeta,Two,a\nAcme,One,b\nGamma,Two,c\n")
    second = tmp_path / "other.csv"
    second.write_text("input_text,target_text,remarks\nGamma,Two,z\nAcme,One,different\nBeta,Two,q\n", encoding="utf-8")
    payloads = []
    class Embedder:
        def embed(self, texts): return [[float(i)] * 384 for i, _ in enumerate(texts)]
    class Repo:
        def submit(self, payload): payloads.append(payload)
    kwargs = dict(apply=True, environ={"SUPABASE_URL": "u", "SUPABASE_SERVICE_KEY": "k"}, repository_factory=lambda *_: Repo(), embedding_factory=Embedder, stdout=io.StringIO())
    run(first, **kwargs); run(second, **kwargs)
    assert payloads[0] == payloads[1]
    UUID(payloads[0].request_id)


def test_changed_mapping_changes_request_identity(tmp_path: Path) -> None:
    ids = []
    class Embedder:
        def embed(self, texts): return [[0.0] * 384 for _ in texts]
    class Repo:
        def submit(self, payload): ids.append(payload.request_id)
    kwargs = dict(apply=True, environ={"SUPABASE_URL": "u", "SUPABASE_SERVICE_KEY": "k"}, repository_factory=lambda *_: Repo(), embedding_factory=Embedder, stdout=io.StringIO())
    run(csv_file(tmp_path, "input_text,target_text,remarks\nAcme,One,x\n"), **kwargs)
    other = tmp_path / "changed.csv"; other.write_text("input_text,target_text,remarks\nAcme,Two,x\n", encoding="utf-8")
    run(other, **kwargs)
    assert ids[0] != ids[1]


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


def test_embedding_calls_are_bounded_and_assignment_order_is_exact(tmp_path: Path) -> None:
    rows = "".join(f"Name {i},Group {i},x\n" for i in range(EMBEDDING_BATCH_SIZE + 2))
    path = csv_file(tmp_path, "input_text,target_text,remarks\n" + rows)
    calls = []; payloads = []
    class Embedder:
        def embed(self, texts):
            calls.append(list(texts)); return [[float(len(calls))] * 384 for _ in texts]
    class Repo:
        def submit(self, payload): payloads.append(payload)
    run(path, apply=True, environ={"SUPABASE_URL":"u","SUPABASE_SERVICE_KEY":"k"}, repository_factory=lambda *_: Repo(), embedding_factory=Embedder, stdout=io.StringIO())
    assert all(len(call) <= EMBEDDING_BATCH_SIZE for call in calls)
    assert len(calls) > 2
    flattened = [text for call in calls for text in call]
    assert flattened == [g["canonical_title"] for g in payloads[0].groups] + [m["cleaned_name"] for m in payloads[0].mappings]


@pytest.mark.parametrize("vectors", [[], [[0.0] * 383], [["x"] * 384], [[True] * 384], [[float("nan")] * 384], [[float("inf")] * 384]])
def test_embedding_vectors_are_strictly_validated(vectors) -> None:
    from company_names.models import SubmissionPayload
    payload = SubmissionPayload([{"id":"g","canonical_title":"G","existing":False}], [], [], "11111111-1111-4111-8111-111111111111")
    with pytest.raises(SeedValidationError):
        _with_embeddings(payload, vectors)


def test_embedding_vectors_are_snapshotted_as_floats() -> None:
    from company_names.models import SubmissionPayload
    vector = [1] * 384
    payload = SubmissionPayload([{"id":"g","canonical_title":"G","existing":False}], [], [], "11111111-1111-4111-8111-111111111111")
    result = _with_embeddings(payload, [vector])
    vector[0] = 9
    assert result.groups[0]["title_embedding"][0] == 1.0


def test_cli_redacts_credentials_from_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secret = "super-secret-service-key"
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", secret)
    monkeypatch.setattr(seed_module, "run", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(f"failure {secret}")))
    error = io.StringIO()
    assert seed_module.main([str(tmp_path / "seed.csv")], stderr=error) == 1
    assert secret not in error.getvalue()


def test_repository_failure_prints_no_submission_success(tmp_path: Path) -> None:
    path = csv_file(tmp_path, "input_text,target_text,remarks\nAcme,One,x\n")
    output = io.StringIO()
    class Embedder:
        def embed(self, texts): return [[0.0] * 384 for _ in texts]
    class Repo:
        def submit(self, payload): raise RuntimeError("lost response")
    with pytest.raises(RuntimeError, match="lost response"):
        run(path, apply=True, environ={"SUPABASE_URL":"u","SUPABASE_SERVICE_KEY":"k"}, repository_factory=lambda *_: Repo(), embedding_factory=Embedder, stdout=output)
    assert "Submitted" not in output.getvalue()
