import sys

import pytest

from company_names.matching import (
    Candidate,
    FastEmbeddingProvider,
    rank_candidates,
)


def candidate(
    group_id: str,
    canonical_title: str,
    member_name: str | None = None,
    vector: list[float] | None = None,
) -> Candidate:
    return Candidate(
        group_id=group_id,
        canonical_title=canonical_title,
        member_name=member_name or canonical_title,
        vector=vector,
    )


def test_exact_member_match_wins_over_better_vector_match() -> None:
    candidates = [
        candidate("exact", "Miki Group", "Miki Travel Ltd", [0.0, 1.0]),
        candidate("vector", "Unrelated Company", vector=[1.0, 0.0]),
    ]

    suggestions = rank_candidates("Miki Travel", candidates, [1.0, 0.0])

    assert suggestions[0].group_id == "exact"
    assert suggestions[0].score == 1.0
    assert suggestions[0].reason == "exact"


def test_exact_canonical_match_is_recognized_after_normalization() -> None:
    result = rank_candidates(
        "Kake Hotels-Marketing",
        [candidate("kake", "Kake Hotels Marketing", vector=None)],
        None,
    )

    assert result[0].reason == "exact"
    assert result[0].score == 1.0


def test_acronym_uses_meaningful_tokens_before_legal_suffix_cleanup() -> None:
    result = rank_candidates(
        "MTL",
        [candidate("miki", "Miki Travel Limited", "Miki Travel")],
        None,
    )[0]

    assert result.acronym_score == 1.0
    expected = (
        0.25 * result.fuzzy_score
        + 0.15 * result.token_score
        + 0.10 * result.acronym_score
    ) / 0.50
    assert result.score == pytest.approx(expected)


def test_cosine_similarity_contributes_half_the_nonexact_score() -> None:
    orthogonal = rank_candidates(
        "query", [candidate("g", "different", vector=[0.0, 1.0])], [1.0, 0.0]
    )[0]
    aligned = rank_candidates(
        "query", [candidate("g", "different", vector=[2.0, 0.0])], [1.0, 0.0]
    )[0]

    assert aligned.vector_score == pytest.approx(1.0)
    assert aligned.score - orthogonal.score == pytest.approx(0.5)


@pytest.mark.parametrize("vector", [[1.0], [0.0, 0.0]])
def test_invalid_candidate_vector_has_no_vector_signal(vector: list[float]) -> None:
    result = rank_candidates(
        "alpha", [candidate("g", "beta", vector=vector)], [1.0, 0.0]
    )[0]

    assert result.vector_score == 0.0
    assert 0.0 <= result.score <= 1.0


def test_zero_query_vector_has_no_vector_signal() -> None:
    result = rank_candidates(
        "alpha", [candidate("g", "beta", vector=[1.0, 0.0])], [0.0, 0.0]
    )[0]

    assert result.vector_score == 0.0


def test_missing_vector_renormalizes_text_weights() -> None:
    result = rank_candidates(
        "alpha holiday", [candidate("g", "alpha tours", vector=None)], None
    )[0]

    expected = (0.25 * result.fuzzy_score + 0.15 * result.token_score) / 0.50
    assert result.score == pytest.approx(expected)
    assert result.token_score == pytest.approx(1 / 3)


def test_returns_best_member_per_group_with_stable_tie_order_and_limit() -> None:
    candidates = [
        candidate("first", "First Group", "zzz"),
        candidate("first", "First Group", "target travel"),
        candidate("second", "Second Group", "target travel"),
        candidate("third", "Third Group", "target travel"),
    ]

    result = rank_candidates("target", candidates, None, limit=2)

    assert [item.group_id for item in result] == ["first", "second"]
    assert len(result) == 2
    assert result[0].canonical_title == "First Group"


def test_nonpositive_limit_returns_empty_list() -> None:
    assert rank_candidates("query", [candidate("g", "title")], None, limit=0) == []


def test_provider_constructs_model_only_when_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    constructed = []

    class FakeModel:
        def __init__(self, model_name: str) -> None:
            constructed.append(model_name)

        def embed(self, texts: list[str]):
            return [[0.0] * 384 for _ in texts]

    monkeypatch.setitem(
        sys.modules, "fastembed", type("Module", (), {"TextEmbedding": FakeModel})
    )
    provider = FastEmbeddingProvider("fake-model")
    assert constructed == []

    vectors = provider.embed(["hello"])

    assert constructed == ["fake-model"]
    assert isinstance(vectors[0], list)
    assert len(vectors[0]) == 384


def test_provider_rejects_wrong_embedding_dimension(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeModel:
        def __init__(self, model_name: str) -> None:
            pass

        def embed(self, texts: list[str]):
            return [[0.0] * 12]

    monkeypatch.setitem(
        sys.modules, "fastembed", type("Module", (), {"TextEmbedding": FakeModel})
    )

    with pytest.raises(ValueError, match="384"):
        FastEmbeddingProvider().embed(["hello"])


@pytest.mark.integration
def test_real_fastembed_dimension() -> None:
    vectors = FastEmbeddingProvider().embed(["company name"])

    assert len(vectors) == 1
    assert len(vectors[0]) == 384
