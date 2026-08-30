"""Normalize report rows and resolve company names through saved aliases."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal

import pandas as pd

from .aliases import AliasSuggestion, suggest_alias
from .cleaning import clean_company_name, normalize_lookup_key
from .repository import (
    AliasMapping,
    AliasRepository,
    RepositoryUnavailableError,
)


class ServiceValidationError(ValueError):
    """The extracted report rows cannot form an actionable alias review."""


@dataclass(frozen=True)
class AliasReviewRow:
    cleaned_name: str
    final_name: str
    status: Literal["saved", "suggested", "new"]
    suggestion: AliasSuggestion | None


@dataclass
class PreparedAliases:
    """Alias review state; ``rows`` and ``review_rows`` are mutable session data."""

    rows: pd.DataFrame
    review_rows: list[AliasReviewRow]
    database_available: bool
    database_error: str | None


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
    """Combine duplicate-safe extractor frames for the report display."""
    if not frames:
        return pd.DataFrame(
            columns=["TRAVEL AGENT", "Sum of RNS", "Sum of R REVENUE"]
        )
    normalized = normalize_extracted_rows(pd.concat(frames, ignore_index=True))
    return _aggregate_without_aliases(normalized)


def prepare_aliases(
    rows: pd.DataFrame, repository: AliasRepository | None
) -> PreparedAliases:
    """Normalize extracted rows and prepare saved, suggested, or new aliases."""
    normalized = normalize_extracted_rows(rows)
    aliases: list[AliasMapping] = []
    database_available = repository is not None
    database_error: str | None = None
    if repository is not None:
        try:
            aliases = repository.list_aliases()
        except RepositoryUnavailableError as error:
            database_available = False
            database_error = str(error)

    exact = {item.alias_key: item for item in aliases}
    review_rows: list[AliasReviewRow] = []
    for cleaned_name in normalized["cleaned_name"]:
        mapping = exact.get(normalize_lookup_key(cleaned_name))
        if mapping is not None:
            review_rows.append(AliasReviewRow(
                cleaned_name, mapping.canonical_name, "saved", None
            ))
            continue
        suggestion = suggest_alias(cleaned_name, aliases) if aliases else None
        review_rows.append(AliasReviewRow(
            cleaned_name,
            cleaned_name,
            "suggested" if suggestion is not None else "new",
            suggestion,
        ))

    return PreparedAliases(
        normalized.copy(deep=True),
        list(review_rows),
        database_available,
        database_error,
    )


def aggregate_resolved_rows(
    rows: pd.DataFrame, final_names: dict[str, str]
) -> pd.DataFrame:
    """Aggregate normalized measures by their resolved final company name."""
    resolved = rows.copy()
    cleaned_names = resolved["cleaned_name"].tolist()
    trimmed = _validated_final_names(cleaned_names, final_names)
    resolved["final_name"] = resolved["cleaned_name"].map(trimmed)
    return (
        resolved.groupby("final_name", as_index=False, sort=False)[["rns", "revenue"]]
        .sum()
        .rename(columns={
            "final_name": "TRAVEL AGENT",
            "rns": "Sum of RNS",
            "revenue": "Sum of R REVENUE",
        })
        .astype({"Sum of RNS": float, "Sum of R REVENUE": float})
        .sort_values("Sum of R REVENUE", ascending=False, kind="stable")
        .reset_index(drop=True)
    )


def save_alias_changes(
    prepared: PreparedAliases,
    final_names: dict[str, str],
    repository: AliasRepository,
) -> pd.DataFrame:
    """Validate, persist, and aggregate a complete edited alias mapping."""
    rows = prepared.rows.copy(deep=True)
    cleaned_names = rows["cleaned_name"].tolist()
    if set(final_names) != set(cleaned_names):
        raise ServiceValidationError(
            "Every cleaned company name needs a final company name"
        )

    trimmed = _validated_final_names(cleaned_names, final_names)
    mappings_by_key: dict[str, AliasMapping] = {}
    for cleaned_name in cleaned_names:
        alias_key = normalize_lookup_key(cleaned_name)
        final_name = trimmed[cleaned_name]
        existing = mappings_by_key.get(alias_key)
        if existing is not None and existing.canonical_name != final_name:
            raise ServiceValidationError(
                "Names with the same alias key need the same final company name"
            )
        if existing is None:
            mappings_by_key[alias_key] = AliasMapping(
                cleaned_name, alias_key, final_name
            )

    repository.upsert_aliases(list(mappings_by_key.values()))
    return aggregate_resolved_rows(rows, trimmed)


def _aggregate_without_aliases(rows: pd.DataFrame) -> pd.DataFrame:
    return aggregate_resolved_rows(
        rows, dict(zip(rows["cleaned_name"], rows["cleaned_name"]))
    )


def _validated_final_names(
    cleaned_names: list[str], final_names: dict[str, str]
) -> dict[str, str]:
    trimmed: dict[str, str] = {}
    for cleaned_name in cleaned_names:
        final_name = final_names.get(cleaned_name)
        if not isinstance(final_name, str) or not final_name.strip():
            raise ServiceValidationError(
                "Every cleaned company name needs a final company name"
            )
        trimmed[cleaned_name] = final_name.strip()
    return trimmed
