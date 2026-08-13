"""Orchestration for preparing and persisting a company-name review."""

from __future__ import annotations

from dataclasses import dataclass
import math
from uuid import uuid4

import pandas as pd

from .cleaning import clean_company_name
from .matching import EMBEDDING_DIMENSION, EmbeddingProvider, Suggestion, rank_candidates
from .models import Group, NameRecord, ReviewBoard
from .repository import MappingRepository
from .review import build_submission

EMBEDDING_BATCH_SIZE = 128


class ServiceValidationError(ValueError):
    """The extracted report rows cannot form an actionable review."""


@dataclass
class PreparedReview:
    board: ReviewBoard
    original_mappings: dict[str, str]
    suggestions: dict[str, list[Suggestion]]
    rows: pd.DataFrame
    warnings: list[str]
    pending_request_id: str | None = None


def normalize_extracted_rows(rows: pd.DataFrame) -> pd.DataFrame:
    """Clean names, validate measures, and combine duplicate cleaned names."""
    column_sets = (
        ("TRAVEL AGENT", "Sum of RNS", "Sum of R REVENUE"),
        ("agent_name", "rns", "revenue"),
        ("cleaned_name", "rns", "revenue"),
    )
    columns = next((item for item in column_sets if set(item) <= set(rows.columns)), None)
    if columns is None:
        raise ServiceValidationError(
            "Rows must contain agent name, room nights, and revenue columns"
        )

    name_column, rns_column, revenue_column = columns
    normalized: list[dict[str, object]] = []
    for row_number, (_, row) in enumerate(rows.iterrows(), start=1):
        raw_name = row[name_column]
        if not isinstance(raw_name, str):
            raise ServiceValidationError(f"Row {row_number} has an invalid company name")
        try:
            cleaned_name = clean_company_name(raw_name)
        except ValueError:
            raise ServiceValidationError(
                f"Row {row_number} has an invalid company name"
            ) from None
        try:
            rns = float(row[rns_column])
            revenue = float(row[revenue_column])
        except (TypeError, ValueError):
            raise ServiceValidationError(
                f"Row {row_number} has an invalid numeric value"
            ) from None
        if not math.isfinite(rns) or not math.isfinite(revenue):
            raise ServiceValidationError(
                f"Row {row_number} has an invalid numeric value"
            )
        normalized.append(
            {"cleaned_name": cleaned_name, "rns": rns, "revenue": revenue}
        )

    if not normalized:
        raise ServiceValidationError("No actionable company rows were extracted")
    result = (
        pd.DataFrame(normalized)
        .groupby("cleaned_name", as_index=False, sort=False)[["rns", "revenue"]]
        .sum()
        .astype({"rns": float, "revenue": float})
    )
    if not result[["rns", "revenue"]].map(math.isfinite).all().all():
        raise ServiceValidationError("Grouped aggregate contains a non-finite numeric value")
    return result


def collate_extracted_rows(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Combine duplicate-safe extractor frames for the legacy result display."""
    if not frames:
        return pd.DataFrame(
            columns=["TRAVEL AGENT", "Sum of RNS", "Sum of R REVENUE"]
        )
    combined = pd.concat(frames, ignore_index=True)
    normalized = normalize_extracted_rows(combined)
    return (
        normalized.rename(
            columns={
                "cleaned_name": "TRAVEL AGENT",
                "rns": "Sum of RNS",
                "revenue": "Sum of R REVENUE",
            }
        )
        .sort_values("Sum of R REVENUE", ascending=False, kind="stable")
        .reset_index(drop=True)
    )


def prepare_review(
    rows: pd.DataFrame,
    repository: MappingRepository,
    embedder: EmbeddingProvider,
) -> PreparedReview:
    """Create review state from extracted rows and validated mappings."""
    normalized_rows = normalize_extracted_rows(rows)
    names = normalized_rows["cleaned_name"].tolist()
    exact = repository.get_exact_mappings(names)
    groups = {
        item.id: Group(item.id, item.canonical_title, True)
        for item in repository.list_groups()
    }
    for mapping in exact.values():
        groups.setdefault(
            mapping.group_id, Group(mapping.group_id, mapping.canonical_title, True)
        )

    unknown_names = [name for name in names if name not in exact]
    candidates = repository.list_candidates() if unknown_names else []
    query_vectors: list[list[float] | None] = [None] * len(unknown_names)
    warnings: list[str] = []
    if unknown_names:
        try:
            embedded = _embed_batched(embedder, unknown_names)
            query_vectors = embedded
        except Exception:
            warnings.append(
                "Embedding retrieval was unavailable; suggestions use text matching only."
            )

    suggestions = {
        name: rank_candidates(name, candidates, vector)
        for name, vector in zip(unknown_names, query_vectors)
    }
    records: dict[str, NameRecord] = {}
    original_mappings: dict[str, str] = {}
    for name in names:
        mapping = exact.get(name)
        if mapping is not None:
            records[name] = NameRecord(name, mapping.group_id, "exact", selected=True)
            original_mappings[name] = mapping.group_id
        else:
            records[name] = NameRecord(
                name, None, "suggested" if suggestions[name] else "unknown", selected=True
            )

    return PreparedReview(
        ReviewBoard(groups, records), original_mappings, suggestions,
        normalized_rows, warnings, str(uuid4()),
    )


def _embed_batched(embedder: EmbeddingProvider, texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[start : start + EMBEDDING_BATCH_SIZE]
        embedded = embedder.embed(batch)
        if len(embedded) != len(batch):
            raise ValueError("embedding provider returned the wrong number of vectors")
        for vector in embedded:
            if len(vector) != EMBEDDING_DIMENSION or not all(math.isfinite(value) for value in vector):
                raise ValueError("embedding provider returned an invalid vector")
        vectors.extend(embedded)
    return vectors


def submit_review(
    board: ReviewBoard,
    original_mappings: dict[str, str],
    repository: MappingRepository,
    embedder: EmbeddingProvider,
    request_id: str | None = None,
) -> dict[str, str]:
    """Embed changed data and persist one atomic review payload."""
    if request_id is None:
        raise ServiceValidationError(
            "A stable request_id is required; use submit_prepared_review for prepared state"
        )
    payload = build_submission(board, original_mappings, request_id=request_id)
    persisted_groups = {group.id: group for group in repository.list_groups()}
    changed_groups = [
        group for group in payload.groups
        if not group["existing"]
        or group["id"] not in persisted_groups
        or persisted_groups[group["id"]].canonical_title != group["canonical_title"]
    ]
    changed_mappings = [
        mapping for mapping in payload.mappings
        if original_mappings.get(mapping["cleaned_name"]) != mapping["group_id"]
    ]
    texts = [str(group["canonical_title"]) for group in changed_groups] + [
        str(mapping["cleaned_name"]) for mapping in changed_mappings
    ]
    vectors = _embed_batched(embedder, texts) if texts else []
    split = len(changed_groups)
    for group, vector in zip(changed_groups, vectors[:split]):
        group["title_embedding"] = vector
    for mapping, vector in zip(changed_mappings, vectors[split:]):
        mapping["member_embedding"] = vector
    return repository.submit(payload)


def submit_prepared_review(
    prepared: PreparedReview,
    repository: MappingRepository,
    embedder: EmbeddingProvider,
) -> dict[str, str]:
    """Submit prepared state with its stable idempotency key."""
    if not prepared.pending_request_id:
        raise ServiceValidationError("Prepared review has no pending request id")
    return submit_review(
        prepared.board,
        prepared.original_mappings,
        repository,
        embedder,
        request_id=prepared.pending_request_id,
    )
