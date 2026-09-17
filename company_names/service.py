"""Normalize report rows and resolve company names through saved aliases."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Literal

import pandas as pd

from .aliases import AliasSuggestion, suggest_alias
from .cleaning import (
    clean_company_name,
    final_name_error,
    normalize_company_text,
    normalize_lookup_key,
)
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
    historical_aliases: dict[str, AliasMapping] = field(default_factory=dict)


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

    exact: dict[str, AliasMapping] = {}
    saved_locations: dict[str, str] = {}
    historical_aliases: dict[str, AliasMapping] = {}
    for index, item in enumerate(aliases, start=1):
        try:
            saved = AliasMapping(
                clean_company_name(item.cleaned_alias),
                normalize_lookup_key(item.cleaned_alias),
                normalize_company_text(item.canonical_name),
            )
        except ValueError as error:
            raise ServiceValidationError(
                f"Saved alias row {index} is invalid: {error}"
            ) from None
        previous = exact.get(saved.alias_key)
        location = (
            f"database row {index}: old name {item.cleaned_alias!r}, "
            f"stored key {item.alias_key!r}, final name {item.canonical_name!r}"
        )
        if previous is not None and previous.canonical_name != saved.canonical_name:
            raise ServiceValidationError(
                f"Saved aliases conflict for key {saved.alias_key!r}: "
                f"{previous.canonical_name!r} and {saved.canonical_name!r}.\n\n"
                f"- {saved_locations[saved.alias_key]}\n"
                f"- {location}\n\n"
                "These duplicate mappings are already in the database. "
                "Their final company names must agree before this report can load."
            )
        exact[saved.alias_key] = saved
        saved_locations[saved.alias_key] = location
        if item.alias_key != saved.alias_key:
            historical_aliases[item.alias_key] = saved
    aliases = list(exact.values())
    defaults: dict[str, str] = {}
    for name in sorted(normalized["cleaned_name"]):
        defaults.setdefault(normalize_lookup_key(name), name)
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
            defaults[normalize_lookup_key(cleaned_name)],
            "suggested" if suggestion is not None else "new",
            suggestion,
        ))

    return PreparedAliases(
        normalized.copy(deep=True),
        list(review_rows),
        database_available,
        database_error,
        historical_aliases,
    )


def aggregate_resolved_rows(
    rows: pd.DataFrame, final_names: dict[str, str]
) -> pd.DataFrame:
    """Aggregate normalized measures by their resolved final company name."""
    resolved = rows.copy()
    cleaned_names = resolved["cleaned_name"].tolist()
    validated = _validated_final_names(cleaned_names, final_names)
    resolved["final_name"] = resolved["cleaned_name"].map(validated)
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
    *,
    page_size: int = 20,
) -> pd.DataFrame:
    """Validate, persist, and aggregate a complete edited alias mapping."""
    rows = prepared.rows.copy(deep=True)
    cleaned_names = rows["cleaned_name"].tolist()
    validated = _validated_final_names(cleaned_names, final_names)
    cleaned_name_set = set(cleaned_names)
    unexpected = [name for name in final_names if name not in cleaned_name_set]
    if unexpected:
        raise ServiceValidationError(
            "Final company name mapping contains unexpected cleaned names: "
            + ", ".join(map(str, unexpected))
        )

    mappings_by_key: dict[str, AliasMapping] = {}
    for cleaned_name in cleaned_names:
        alias_key = normalize_lookup_key(cleaned_name)
        final_name = validated[cleaned_name]
        existing = mappings_by_key.get(alias_key)
        if existing is not None and existing.canonical_name != final_name:
            duplicates = [
                f"- Page {position // page_size + 1}, row {position % page_size + 1} "
                f"(overall row {position + 1}): old name {name!r} "
                f"→ final name {validated[name]!r}"
                for position, name in enumerate(cleaned_names)
                if normalize_lookup_key(name) == alias_key
            ]
            raise ServiceValidationError(
                f"Duplicate company mappings use the same alias key {alias_key!r} "
                "but have different final names.\n\n"
                f"To find them, select 'All names', clear the search, and use "
                f"{page_size} rows per page:\n\n"
                + "\n".join(duplicates)
                + "\n\nSet every listed row to the same final company name. "
                "Save checks all pages, including rows outside your current view."
            )
        if existing is None:
            mappings_by_key[alias_key] = AliasMapping(
                cleaned_name, alias_key, final_name
            )

    # Keep older storage keys consistent in the same batch. A current mapping
    # may legitimately reuse an old key (e.g. decomposed CAFÉ previously used
    # "cafe"); in that case it replaces that legacy row under its own name.
    persisted = dict(mappings_by_key)
    for legacy_key, historical in prepared.historical_aliases.items():
        mapping = mappings_by_key.get(historical.alias_key)
        if mapping is not None:
            persisted.setdefault(legacy_key, AliasMapping(
                mapping.cleaned_alias, legacy_key, mapping.canonical_name
            ))
    # A report containing only CAFE can reuse the old key of CAFÉ. Preserve
    # that displaced company's reviewed mapping even when absent from this report.
    pending = list(persisted)
    canonical_keys = set(mappings_by_key)
    while pending:
        displaced = prepared.historical_aliases.get(pending.pop())
        if displaced is not None and displaced.alias_key not in canonical_keys:
            # A canonical entry takes precedence over a redundant legacy mirror.
            persisted[displaced.alias_key] = displaced
            canonical_keys.add(displaced.alias_key)
            pending.append(displaced.alias_key)
    repository.upsert_aliases(list(persisted.values()))
    return aggregate_resolved_rows(rows, validated)


def _aggregate_without_aliases(rows: pd.DataFrame) -> pd.DataFrame:
    return aggregate_resolved_rows(
        rows, dict(zip(rows["cleaned_name"], rows["cleaned_name"]))
    )


def _validated_final_names(
    cleaned_names: list[str], final_names: dict[str, str]
) -> dict[str, str]:
    invalid: list[str] = []
    seen_invalid: set[str] = set()
    for cleaned_name in cleaned_names:
        final_name = final_names.get(cleaned_name)
        if (
            not isinstance(final_name, str) or not final_name.strip()
        ) and cleaned_name not in seen_invalid:
            invalid.append(cleaned_name)
            seen_invalid.add(cleaned_name)
    if invalid:
        raise ServiceValidationError(
            "Every cleaned company name needs a final company name. "
            "Missing or blank: " + ", ".join(invalid)
        )

    format_errors = []
    validated: dict[str, str] = {}
    for cleaned_name in cleaned_names:
        value = final_names[cleaned_name]
        issue = final_name_error(value)
        if issue:
            format_errors.append(f"{cleaned_name!r}: {issue}")
        validated[cleaned_name] = value
    if format_errors:
        raise ServiceValidationError(
            "Invalid final company names. " + "; ".join(format_errors)
        )
    return validated
