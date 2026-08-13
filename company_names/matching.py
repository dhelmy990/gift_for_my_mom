"""Hybrid matching for previously validated company-name groups."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
import threading
from typing import Protocol

from rapidfuzz.fuzz import WRatio

from .cleaning import normalize_lookup_key


EMBEDDING_DIMENSION = 384
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


class EmbeddingProvider(Protocol):
    """Produces one embedding for each supplied text."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in input order."""


class FastEmbeddingProvider:
    """Lazy adapter for FastEmbed's 384-dimensional text embeddings."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self.model_name = model_name
        self._model = None
        self._model_lock = threading.Lock()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    self._model = TextEmbedding(self.model_name)

        vectors: list[list[float]] = []
        for raw_vector in self._model.embed(texts):
            vector = raw_vector.tolist() if hasattr(raw_vector, "tolist") else list(raw_vector)
            if len(vector) != EMBEDDING_DIMENSION:
                raise ValueError(
                    f"expected {EMBEDDING_DIMENSION}-dimensional embedding, got {len(vector)}"
                )
            vectors.append(vector)
        if len(vectors) != len(texts):
            raise ValueError(
                f"expected {len(texts)} embeddings, got {len(vectors)}"
            )
        return vectors


@dataclass(frozen=True)
class Candidate:
    group_id: str
    canonical_title: str
    member_name: str
    vector: tuple[float, ...] | None

    def __post_init__(self) -> None:
        if self.vector is not None and not isinstance(self.vector, tuple):
            object.__setattr__(self, "vector", tuple(self.vector))


@dataclass(frozen=True)
class Suggestion:
    group_id: str
    canonical_title: str
    score: float
    reason: str
    vector_score: float
    fuzzy_score: float
    token_score: float
    acronym_score: float


def _acronym(name: str) -> str:
    raw_tokens = _WORD_RE.findall(name)
    if len(raw_tokens) == 1:
        return raw_tokens[0].casefold()
    return "".join(token[0] for token in raw_tokens).casefold()


def _cosine(
    left: list[float] | tuple[float, ...] | None,
    right: list[float] | tuple[float, ...] | None,
) -> float | None:
    if left is None or right is None or len(left) != len(right) or not left:
        return None
    if not all(math.isfinite(value) for value in (*left, *right)):
        return None
    left_norm = math.hypot(*left)
    right_norm = math.hypot(*right)
    if left_norm == 0.0 or right_norm == 0.0:
        return None
    cosine = math.fsum(
        (a / left_norm) * (b / right_norm) for a, b in zip(left, right)
    )
    return min(1.0, max(0.0, (cosine + 1.0) / 2.0))


def _exact_priority(query_key: str, candidate: Candidate) -> int:
    if query_key == normalize_lookup_key(candidate.canonical_title):
        return 2
    if query_key == normalize_lookup_key(candidate.member_name):
        return 1
    return 0


def _text_scores(query_key: str, candidate_key: str) -> tuple[float, float]:
    fuzzy = WRatio(query_key, candidate_key) / 100.0
    query_tokens = set(query_key.split())
    candidate_tokens = set(candidate_key.split())
    union = query_tokens | candidate_tokens
    token = len(query_tokens & candidate_tokens) / len(union) if union else 0.0
    return fuzzy, token


def _score_candidate(
    query: str, candidate: Candidate, query_vector: list[float] | None
) -> Suggestion:
    query_key = normalize_lookup_key(query)
    canonical_key = normalize_lookup_key(candidate.canonical_title)
    member_key = normalize_lookup_key(candidate.member_name)
    if query_key in (canonical_key, member_key):
        return Suggestion(
            candidate.group_id, candidate.canonical_title, 1.0, "exact", 0.0, 1.0, 1.0, 0.0
        )

    text_values = [_text_scores(query_key, key) for key in (canonical_key, member_key)]
    fuzzy_score = max(value[0] for value in text_values)
    token_score = max(value[1] for value in text_values)
    query_acronym = _acronym(query)
    acronym_score = float(
        any(
            query_acronym == _acronym(name)
            for name in (candidate.canonical_title, candidate.member_name)
        )
    )

    cosine = _cosine(query_vector, candidate.vector)
    vector_score = cosine if cosine is not None else 0.0
    weighted = 0.25 * fuzzy_score + 0.15 * token_score + 0.10 * acronym_score
    available_weight = 0.50
    if cosine is not None:
        weighted += 0.50 * cosine
        available_weight += 0.50

    return Suggestion(
        group_id=candidate.group_id,
        canonical_title=candidate.canonical_title,
        score=min(1.0, max(0.0, weighted / available_weight)),
        reason="hybrid",
        vector_score=vector_score,
        fuzzy_score=fuzzy_score,
        token_score=token_score,
        acronym_score=acronym_score,
    )


def rank_candidates(
    query: str,
    candidates: list[Candidate],
    query_vector: list[float] | None,
    limit: int = 5,
) -> list[Suggestion]:
    """Rank validated candidates, retaining the best member of each group."""
    if limit <= 0:
        return []

    query_key = normalize_lookup_key(query)
    best_by_group: dict[str, tuple[Suggestion, int, int]] = {}
    for index, candidate in enumerate(candidates):
        suggestion = _score_candidate(query, candidate, query_vector)
        exact_priority = _exact_priority(query_key, candidate)
        current = best_by_group.get(candidate.group_id)
        if current is None or (suggestion.score, exact_priority) > (
            current[0].score,
            current[2],
        ):
            best_by_group[candidate.group_id] = (
                suggestion,
                index,
                exact_priority,
            )

    ranked = sorted(
        best_by_group.values(),
        key=lambda item: (-item[0].score, -item[2], item[1]),
    )
    return [suggestion for suggestion, _, _ in ranked[:limit]]
