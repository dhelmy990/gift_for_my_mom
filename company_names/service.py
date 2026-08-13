"""Orchestration for preparing and persisting a company-name review."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import csv
import hashlib
import hmac
import io
import json
import math
from uuid import UUID, uuid4

import pandas as pd

from .cleaning import clean_company_name
from .csv_safety import csv_safe_cell
from .matching import EMBEDDING_DIMENSION, EmbeddingProvider, Suggestion, rank_candidates
from .models import Group, NameRecord, ReviewBoard, SubmissionPayload
from .repository import MappingRepository, RepositoryUnavailableError
from .review import (
    _build_submission_from_materialized,
    aggregate_by_group,
    materialize_singletons,
    validate_submission,
)

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
    submission_fingerprint: str | None = None
    initial_group_titles: dict[str, str] | None = None


@dataclass(frozen=True)
class SubmissionOutcome:
    success: bool
    result: pd.DataFrame | None = None
    error: str | None = None
    warning: str | None = None


@dataclass(frozen=True)
class BackupOutcome:
    data: bytes | None = None
    error: str | None = None


@dataclass
class AuthAttemptState:
    """Per-session failed-login throttle with a deterministic clock boundary."""

    failure_count: int = 0
    locked_until: float = 0.0

    def retry_after(self, now: float) -> int:
        return max(0, math.ceil(self.locked_until - now))

    def record_failure(self, now: float) -> int:
        self.failure_count += 1
        if self.failure_count >= 5:
            self.locked_until = max(self.locked_until, now + 60.0)
        return self.retry_after(now)

    def record_success(self) -> None:
        self.failure_count = 0
        self.locked_until = 0.0


def admin_password_digest(password: object) -> str | None:
    """Return a non-reversible binding for the currently configured password."""
    if not isinstance(password, str) or not password:
        return None
    return hmac.new(
        b"company-name-admin-session-v1", password.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def password_matches(candidate: object, expected: object) -> bool:
    """Compare configured admin credentials without retaining the candidate."""
    if not isinstance(candidate, str) or not isinstance(expected, str):
        return False
    return hmac.compare_digest(candidate, expected)


def submission_fingerprint(board: ReviewBoard) -> str:
    value = {
        "groups": sorted(
            (group.id, group.canonical_title, group.existing)
            for group in board.groups.values()
        ),
        "names": sorted(
            (
                key, record.cleaned_name, record.group_id, record.selected,
                record.excluded, record.persisted_name,
            )
            for key, record in board.names.items()
        ),
    }
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def ensure_submission_identity(prepared: PreparedReview) -> str:
    """Reuse an idempotency key only while its exact board payload is unchanged."""
    fingerprint = submission_fingerprint(prepared.board)
    if not prepared.pending_request_id or (
        prepared.submission_fingerprint is not None
        and prepared.submission_fingerprint != fingerprint
    ):
        prepared.pending_request_id = str(uuid4())
    prepared.submission_fingerprint = fingerprint
    return prepared.pending_request_id


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
        source = row.get("_source_file")
        location = f"Row {row_number}"
        if isinstance(source, str) and source.strip():
            location += f" in {source.strip()}"
        if not isinstance(raw_name, str):
            raise ServiceValidationError(
                f"{location} has an invalid company name: expected text, "
                f"got {type(raw_name).__name__}"
            )
        try:
            cleaned_name = clean_company_name(raw_name)
        except ValueError as error:
            preview = raw_name.strip().replace("\n", " ")[:120]
            raise ServiceValidationError(
                f"{location} has an invalid company name {preview!r}: {error}"
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

    unknown_names = [name for name in names if name not in exact]
    candidates = repository.list_candidates() if unknown_names else []
    if unknown_names:
        groups = {
            candidate.group_id: Group(
                candidate.group_id, candidate.canonical_title, True
            )
            for candidate in candidates
        }
    else:
        groups = {
            item.id: Group(item.id, item.canonical_title, True)
            for item in repository.list_groups()
        }
    for mapping in exact.values():
        groups.setdefault(
            mapping.group_id, Group(mapping.group_id, mapping.canonical_title, True)
        )

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
            records[name] = NameRecord(
                name, mapping.group_id, "exact", selected=True,
                persisted_name=mapping.member_name,
            )
            original_mappings[name] = mapping.group_id
        else:
            records[name] = NameRecord(
                name, None, "suggested" if suggestions[name] else "unknown", selected=True
            )

    prepared = PreparedReview(
        ReviewBoard(groups, records), original_mappings, suggestions,
        normalized_rows, warnings, str(uuid4()),
        initial_group_titles={group_id: group.canonical_title for group_id, group in groups.items()},
    )
    prepared.submission_fingerprint = submission_fingerprint(prepared.board)
    return prepared


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
    known_group_titles: dict[str, str] | None = None,
) -> dict[str, str]:
    """Embed changed data and persist one atomic review payload."""
    if request_id is None:
        raise ServiceValidationError(
            "A stable request_id is required; use submit_prepared_review for prepared state"
        )
    if known_group_titles is None:
        known_group_titles = {
            group.id: group.canonical_title for group in repository.list_groups()
        }
    payload, _, _ = _prepare_submission_payload(
        board,
        original_mappings,
        embedder,
        request_id,
        known_group_titles,
    )
    return repository.submit(payload)


def _prepare_submission_payload(
    board: ReviewBoard,
    original_mappings: dict[str, str],
    embedder: EmbeddingProvider,
    request_id: str,
    known_group_titles: dict[str, str] | None,
) -> tuple[SubmissionPayload, bool, ReviewBoard]:
    """Build one payload, falling back cleanly when optional embeddings fail."""
    errors = validate_submission(board)
    if errors:
        raise ServiceValidationError("\n".join(errors))
    materialized = materialize_singletons(board, original_mappings)
    payload = _build_submission_from_materialized(
        materialized, original_mappings, request_id=request_id
    )
    persisted_groups = known_group_titles or {}
    changed_groups = [group for group in payload.groups if (
        not group["existing"]
        or group["id"] not in persisted_groups
        or persisted_groups[group["id"]] != group["canonical_title"]
    )]
    unchanged_storage_mappings = {
        (record.persisted_name or record.cleaned_name, original_mappings[report_name])
        for report_name, record in materialized.names.items()
        if report_name in original_mappings
    }
    changed_mappings = [
        mapping for mapping in payload.mappings
        if (mapping["cleaned_name"], mapping["group_id"])
        not in unchanged_storage_mappings
    ]
    texts = [str(group["canonical_title"]) for group in changed_groups] + [
        str(mapping["cleaned_name"]) for mapping in changed_mappings
    ]
    embedding_failed = False
    try:
        vectors = _embed_batched(embedder, texts) if texts else []
    except Exception:
        vectors = []
        embedding_failed = bool(texts)
    split = len(changed_groups)
    for group, vector in zip(changed_groups, vectors[:split]):
        group["title_embedding"] = vector
    for mapping, vector in zip(changed_mappings, vectors[split:]):
        mapping["member_embedding"] = vector
    return payload, embedding_failed, materialized


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
        known_group_titles=prepared.initial_group_titles or {},
    )


def _validate_resolved_group_ids(
    board: ReviewBoard, payload: SubmissionPayload, resolved: dict[str, str]
) -> None:
    expected = {str(group["id"]) for group in payload.groups if not group["existing"]}
    if set(resolved) != expected:
        raise ValueError("resolution keys did not match newly submitted groups")
    values: list[str] = []
    for value in resolved.values():
        if not isinstance(value, str):
            raise ValueError("resolved group id was not a UUID string")
        try:
            parsed = UUID(value)
        except (ValueError, AttributeError, TypeError):
            raise ValueError("resolved group id was not a UUID string") from None
        if str(parsed) != value:
            raise ValueError("resolved group id was not a canonical UUID string")
        values.append(value)
    if len(values) != len(set(values)):
        raise ValueError("multiple groups resolved to the same UUID")
    existing_ids = {group.id for group in board.groups.values() if group.existing}
    if set(values) & existing_ids:
        raise ValueError("a new group resolved to an existing board group")


def _apply_resolved_group_ids(prepared: PreparedReview, resolved: dict[str, str]) -> None:
    """Replace temporary group IDs after a committed response, preserving all state."""
    for temporary_id, persisted_id in resolved.items():
        group = prepared.board.groups.get(temporary_id)
        existing = prepared.board.groups.get(persisted_id)
        if group is not None and temporary_id != persisted_id and existing is not None and existing is not group:
            raise ValueError(f"Resolved group {persisted_id} conflicts with an existing group")
    for temporary_id, persisted_id in resolved.items():
        group = prepared.board.groups.get(temporary_id)
        if group is None or temporary_id == persisted_id:
            continue
        del prepared.board.groups[temporary_id]
        group.id = persisted_id
        group.existing = True
        prepared.board.groups[persisted_id] = group
        for record in prepared.board.names.values():
            if record.group_id == temporary_id:
                record.group_id = persisted_id


def _refresh_original_mappings(prepared: PreparedReview) -> None:
    for name, record in prepared.board.names.items():
        if not record.selected:
            prepared.original_mappings.pop(name, None)
        elif not record.excluded and record.group_id is not None:
            prepared.original_mappings[name] = record.group_id
            if record.persisted_name is None:
                record.persisted_name = record.cleaned_name


def submit_review_authorized(
    prepared: PreparedReview,
    repository: MappingRepository,
    embedder: EmbeddingProvider,
    candidate_password: object,
    expected_password: object,
) -> SubmissionOutcome:
    """Authorize, validate, atomically submit, and finalize a prepared review."""
    if not isinstance(expected_password, str) or not expected_password:
        return SubmissionOutcome(False, error="Admin password is not configured; permanent actions are disabled.")
    if not password_matches(candidate_password, expected_password):
        return SubmissionOutcome(False, error="Authorization failed.")
    errors = validate_submission(prepared.board)
    if errors:
        return SubmissionOutcome(False, error="\n".join(errors))

    ensure_submission_identity(prepared)
    warning = None
    try:
        payload, embedding_failed, materialized_board = _prepare_submission_payload(
            prepared.board,
            prepared.original_mappings,
            embedder,
            prepared.pending_request_id,
            prepared.initial_group_titles or {},
        )
        if embedding_failed:
            warning = "Embeddings were unavailable; mappings were saved without vectors."
        resolved = repository.submit(payload)
    except RepositoryUnavailableError:
        return SubmissionOutcome(False, error="Database submission unavailable. Your review was kept; retry safely.")
    except ValueError as exc:
        return SubmissionOutcome(False, error=str(exc))
    except Exception:
        return SubmissionOutcome(False, error="Submission failed. Your review was kept; retry safely.")

    try:
        _validate_resolved_group_ids(materialized_board, payload, resolved)
        candidate = deepcopy(prepared)
        candidate.board = materialized_board
        _apply_resolved_group_ids(candidate, resolved)
        _refresh_original_mappings(candidate)
        result = aggregate_by_group(candidate.rows, candidate.board)
    except Exception:
        return SubmissionOutcome(
            False,
            error="Submission committed but response could not be reconciled; retry.",
        )
    prepared.board = candidate.board
    prepared.original_mappings = candidate.original_mappings
    prepared.pending_request_id = str(uuid4())
    prepared.submission_fingerprint = submission_fingerprint(prepared.board)
    prepared.initial_group_titles = {
        group_id: group.canonical_title for group_id, group in prepared.board.groups.items()
    }
    return SubmissionOutcome(True, result=result, warning=warning)


def export_backup_csv(
    repository: MappingRepository,
    candidate_password: object,
    expected_password: object,
) -> BackupOutcome:
    """Return an authorized, stable UTF-8 mapping backup with no vector data."""
    if not isinstance(expected_password, str) or not expected_password:
        return BackupOutcome(error="Admin password is not configured; permanent actions are disabled.")
    if not password_matches(candidate_password, expected_password):
        return BackupOutcome(error="Authorization failed.")
    try:
        rows = sorted(
            repository.export_rows(),
            key=lambda row: (row.cleaned_name, row.canonical_title),
        )
    except Exception:
        return BackupOutcome(error="Database export unavailable. Please retry.")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["cleaned_name", "canonical_title"])
    writer.writerows(
        (csv_safe_cell(row.cleaned_name), csv_safe_cell(row.canonical_title))
        for row in rows
    )
    return BackupOutcome(data=output.getvalue().encode("utf-8"))
