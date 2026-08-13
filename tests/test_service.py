import pandas as pd
import pytest

from company_names.matching import Candidate
from company_names.models import Group, NameRecord, ReviewBoard
from company_names.repository import ExactMapping, GroupRecord
from company_names.service import (
    ServiceValidationError,
    collate_extracted_rows,
    normalize_extracted_rows,
    prepare_review,
    submit_prepared_review,
    submit_review,
)


class FakeRepository:
    def __init__(self, *, exact=None, candidates=None, groups=None):
        self.exact = exact or {}
        self.candidates = candidates or []
        self.groups = groups or []
        self.candidate_calls = 0
        self.submissions = []

    def get_exact_mappings(self, names):
        return {name: self.exact[name] for name in names if name in self.exact}

    def list_candidates(self):
        self.candidate_calls += 1
        return self.candidates

    def list_groups(self):
        return self.groups

    def submit(self, payload):
        self.submissions.append(payload)
        return {"temp": "11111111-1111-4111-8111-111111111111"}


class FakeEmbedder:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def embed(self, texts):
        self.calls.append(texts)
        if self.fail:
            raise RuntimeError("model unavailable")
        return [[float(index + 1)] * 384 for index, _ in enumerate(texts)]


def extracted_rows():
    return pd.DataFrame(
        {
            "TRAVEL AGENT": ["Acme Pte Ltd", "Acme", "Unknown Co Ltd"],
            "Sum of RNS": [1, 2.5, 4],
            "Sum of R REVENUE": [10.25, 20, 40],
        }
    )


def test_normalize_cleans_and_sums_duplicate_names_as_floats():
    result = normalize_extracted_rows(extracted_rows())

    assert result.to_dict("records") == [
        {"cleaned_name": "Acme", "rns": 3.5, "revenue": 30.25},
        {"cleaned_name": "Unknown", "rns": 4.0, "revenue": 40.0},
    ]


def test_collate_extracted_rows_groups_agent_column_not_dataframe_indexes():
    first = pd.DataFrame(
        [{"TRAVEL AGENT": "Acme", "Sum of RNS": 2, "Sum of R REVENUE": 10}]
    )
    second = pd.DataFrame(
        [{"TRAVEL AGENT": "Acme", "Sum of RNS": 3, "Sum of R REVENUE": 20}]
    )

    result = collate_extracted_rows([first, second])

    assert result.to_dict("records") == [
        {
            "TRAVEL AGENT": "Acme",
            "Sum of RNS": 5.0,
            "Sum of R REVENUE": 30.0,
        }
    ]


@pytest.mark.parametrize("value", ["bad", float("nan"), float("inf")])
def test_normalize_rejects_invalid_numeric_values(value):
    rows = pd.DataFrame(
        {"agent_name": ["Acme"], "rns": [value], "revenue": [1]}
    )
    with pytest.raises(ServiceValidationError, match="numeric"):
        normalize_extracted_rows(rows)


def test_prepare_places_exact_names_and_leaves_unknown_suggestions_in_tray():
    repo = FakeRepository(
        exact={"Acme": ExactMapping("g1", "Acme Group", "Acme", None)},
        candidates=[Candidate("g1", "Acme Group", "Acme", tuple([1.0] * 384))],
        groups=[GroupRecord("g1", "Acme Group", None)],
    )
    embedder = FakeEmbedder()

    prepared = prepare_review(extracted_rows(), repo, embedder)

    assert prepared.board.groups == {"g1": Group("g1", "Acme Group", True)}
    assert prepared.board.names["Acme"] == NameRecord(
        "Acme", "g1", "exact", selected=True, persisted_name="Acme"
    )
    assert prepared.board.names["Unknown"] == NameRecord(
        "Unknown", None, "suggested", selected=True
    )
    assert prepared.suggestions["Unknown"][0].group_id == "g1"
    assert prepared.original_mappings == {"Acme": "g1"}
    assert embedder.calls == [["Unknown"]]


def test_prepare_keeps_report_identity_separate_from_persisted_exact_alias():
    repo = FakeRepository(
        exact={
            "Miki Travel": ExactMapping("g1", "Miki", "Miki-Travel", None)
        },
        groups=[GroupRecord("g1", "Miki", None)],
    )
    rows = pd.DataFrame(
        {"agent_name": ["Miki Travel"], "rns": [2], "revenue": [8]}
    )

    prepared = prepare_review(rows, repo, FakeEmbedder())

    assert list(prepared.board.names) == ["Miki Travel"]
    assert prepared.board.names["Miki Travel"].persisted_name == "Miki-Travel"


def test_prepare_uses_only_repository_candidates_not_provisional_board_groups():
    repo = FakeRepository(candidates=[])
    prepared = prepare_review(extracted_rows().iloc[[2]], repo, FakeEmbedder())

    prepared.board.groups["temporary"] = Group("temporary", "Unknown", False)
    assert repo.candidate_calls == 1
    assert prepared.suggestions["Unknown"] == []
    assert prepared.board.names["Unknown"].source == "unknown"


def test_prepare_falls_back_to_fuzzy_suggestions_when_embedding_fails():
    repo = FakeRepository(
        candidates=[Candidate("g1", "Unknown Travel", "Unknown Travel", None)]
    )
    prepared = prepare_review(extracted_rows().iloc[[2]], repo, FakeEmbedder(fail=True))

    assert prepared.suggestions["Unknown"][0].group_id == "g1"
    assert prepared.warnings
    assert "embedding" in prepared.warnings[0].lower()


def test_prepare_batches_unknown_embeddings_and_preserves_order():
    rows = pd.DataFrame({"agent_name": [f"Name {i} X" for i in range(257)], "rns": 1, "revenue": 1})
    embedder = FakeEmbedder()
    prepare_review(rows, FakeRepository(), embedder)
    assert [len(call) for call in embedder.calls] == [128, 128, 1]
    assert [name for call in embedder.calls for name in call] == [f"Name {i} X" for i in range(257)]


def test_prepare_falls_back_for_invalid_embedding_dimension():
    class BadEmbedder(FakeEmbedder):
        def embed(self, texts):
            self.calls.append(texts)
            return [[1.0] for _ in texts]
    prepared = prepare_review(extracted_rows().iloc[[2]], FakeRepository(), BadEmbedder())
    assert prepared.warnings
    assert prepared.suggestions["Unknown"] == []


def test_submit_prepared_review_reuses_request_id_after_ambiguous_failure():
    class AmbiguousRepository(FakeRepository):
        def submit(self, payload):
            self.submissions.append(payload)
            if len(self.submissions) == 1:
                raise RuntimeError("response lost")
            return {}
    prepared = prepare_review(extracted_rows().iloc[[2]], FakeRepository(), FakeEmbedder())
    prepared.board.groups["temp"] = Group("temp", "Unknown", False)
    prepared.board.names["Unknown"].group_id = "temp"
    repo = AmbiguousRepository()
    with pytest.raises(RuntimeError, match="response lost"):
        submit_prepared_review(prepared, repo, FakeEmbedder())
    submit_prepared_review(prepared, repo, FakeEmbedder())
    assert prepared.pending_request_id
    assert [p.request_id for p in repo.submissions] == [prepared.pending_request_id] * 2


def test_normalize_rejects_nonfinite_grouped_totals():
    rows = pd.DataFrame({"agent_name": ["Acme", "Acme Ltd"], "rns": [1e308, 1e308], "revenue": [1, 1]})
    with pytest.raises(ServiceValidationError, match="aggregate"):
        normalize_extracted_rows(rows)


def test_submit_embeds_only_changed_titles_and_members_in_one_batch_and_reuses_id():
    board = ReviewBoard(
        groups={
            "old": Group("old", "Renamed", True),
            "temp": Group("temp", "New Group", False),
        },
        names={
            "Known": NameRecord("Known", "old", "exact", selected=True),
            "New Alias": NameRecord("New Alias", "temp", "unknown", selected=True),
        },
    )
    repo = FakeRepository(groups=[GroupRecord("old", "Old Title", None)])
    embedder = FakeEmbedder()
    request_id = "22222222-2222-4222-8222-222222222222"

    resolved = submit_review(
        board, {"Known": "old"}, repo, embedder, request_id=request_id
    )

    assert embedder.calls == [["Renamed", "New Group", "New Alias"]]
    assert len(repo.submissions) == 1
    payload = repo.submissions[0]
    assert payload.request_id == request_id
    assert payload.groups[0]["title_embedding"][0] == 1.0
    assert payload.groups[1]["title_embedding"][0] == 2.0
    assert "member_embedding" not in payload.mappings[0]
    assert payload.mappings[1]["member_embedding"][0] == 3.0
    assert resolved == {"temp": "11111111-1111-4111-8111-111111111111"}
