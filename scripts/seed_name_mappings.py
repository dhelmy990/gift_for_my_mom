#!/usr/bin/env python3
"""Validate and optionally import reviewed company-name mappings from CSV."""

from __future__ import annotations

import argparse
import csv
from dataclasses import replace
import hashlib
import json
import math
from numbers import Real
import os
from pathlib import Path
import sys
from typing import Callable, Mapping, TextIO
from uuid import NAMESPACE_URL, uuid5

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company_names.cleaning import clean_company_name, normalize_lookup_key
from company_names.models import Group, NameRecord, ReviewBoard, SubmissionPayload
from company_names.review import build_submission


EXPECTED_HEADER = ["input_text", "target_text", "remarks"]
BACKUP_HEADER = ["cleaned_name", "canonical_title"]
EMBEDDING_BATCH_SIZE = 64


class SeedValidationError(ValueError):
    """The seed file or import configuration is invalid."""


def load_seed_rows(path: Path) -> list[tuple[str, str]]:
    """Load, validate, clean, and deduplicate seed mappings."""
    try:
        source = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise SeedValidationError(f"cannot read seed CSV: {exc}") from None

    with source:
        reader = csv.DictReader(source)
        if reader.fieldnames not in (EXPECTED_HEADER, BACKUP_HEADER):
            actual = ",".join(reader.fieldnames or []) or "(missing)"
            raise SeedValidationError(
                "CSV header must be exactly input_text,target_text,remarks or "
                "cleaned_name,canonical_title "
                f"(found {actual})"
            )

        input_column, target_column = (
            ("input_text", "target_text")
            if reader.fieldnames == EXPECTED_HEADER
            else ("cleaned_name", "canonical_title")
        )

        unique: dict[str, tuple[str, str, str, int]] = {}
        for row_number, row in enumerate(reader, start=2):
            raw_input = row[input_column]
            raw_target = row[target_column]
            if raw_input is None or raw_target is None or None in row:
                raise SeedValidationError(f"row {row_number} has malformed CSV fields")
            raw_input = _unescape_backup_cell(raw_input)
            target = _unescape_backup_cell(raw_target).strip()
            if not raw_input.strip():
                raise SeedValidationError(f"row {row_number} has a blank input_text")
            if not target:
                raise SeedValidationError(f"row {row_number} has a blank target_text")
            try:
                cleaned = clean_company_name(raw_input)
            except ValueError:
                raise SeedValidationError(
                    f"row {row_number} input_text cannot form a company name"
                ) from None
            try:
                input_key = normalize_lookup_key(cleaned)
                target_key = normalize_lookup_key(target)
            except ValueError:
                raise SeedValidationError(
                    f"row {row_number} target_text cannot form a lookup key"
                ) from None

            previous = unique.get(input_key)
            if previous is not None:
                if previous[2] != target_key:
                    raise SeedValidationError(
                        f"row {row_number} has a contradictory mapping with row {previous[3]} "
                        f"for input_text {cleaned!r}"
                    )
                continue
            unique[input_key] = (cleaned, target, target_key, row_number)

    rows = [(cleaned, target) for cleaned, target, _, _ in unique.values()]
    if not rows:
        raise SeedValidationError("seed CSV contains no mappings")
    return sorted(rows, key=lambda item: (normalize_lookup_key(item[1]), normalize_lookup_key(item[0])))


def _unescape_backup_cell(value: str) -> str:
    """Reverse exactly the apostrophe prefix created by spreadsheet-safe export."""
    if value.startswith("'") and value[1:].lstrip().startswith(("=", "+", "-", "@")):
        return value[1:]
    return value


def _build_payload(rows: list[tuple[str, str]]) -> SubmissionPayload:
    groups: dict[str, Group] = {}
    group_ids: dict[str, str] = {}
    names: dict[str, NameRecord] = {}
    for cleaned, target in sorted(rows, key=lambda item: (normalize_lookup_key(item[1]), normalize_lookup_key(item[0]))):
        target_key = normalize_lookup_key(target)
        group_id = group_ids.get(target_key)
        if group_id is None:
            group_id = "seed-" + hashlib.sha256(target_key.encode("utf-8")).hexdigest()
            group_ids[target_key] = group_id
            groups[group_id] = Group(group_id, target, False)
        names[cleaned] = NameRecord(cleaned, group_id, "exact", selected=True)
    provisional = build_submission(ReviewBoard(groups, names), {})
    logical = {"groups": provisional.groups, "mappings": provisional.mappings, "unmap_names": provisional.unmap_names}
    canonical = json.dumps(logical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return replace(provisional, request_id=str(uuid5(NAMESPACE_URL, canonical)))


def _with_embeddings(payload: SubmissionPayload, vectors: list[list[float]]) -> SubmissionPayload:
    expected = len(payload.groups) + len(payload.mappings)
    if len(vectors) != expected:
        raise SeedValidationError(f"embedding provider returned {len(vectors)} vectors; expected {expected}")
    snapshots: list[list[float]] = []
    for vector in vectors:
        if not isinstance(vector, (list, tuple)):
            raise SeedValidationError("embedding provider returned a non-vector value")
        if len(vector) != 384:
            raise SeedValidationError(f"embedding provider returned a {len(vector)}-dimensional vector; expected 384")
        if any(isinstance(value, bool) or not isinstance(value, Real) for value in vector):
            raise SeedValidationError("embedding provider returned a non-numeric vector component")
        snapshot = [float(value) for value in vector]
        if not all(math.isfinite(value) for value in snapshot):
            raise SeedValidationError("embedding provider returned a non-finite vector component")
        snapshots.append(snapshot)
    split = len(payload.groups)
    groups = [dict(group, title_embedding=vector) for group, vector in zip(payload.groups, snapshots[:split])]
    mappings = [dict(mapping, member_embedding=vector) for mapping, vector in zip(payload.mappings, snapshots[split:])]
    return replace(payload, groups=groups, mappings=mappings)


def run(
    path: Path,
    *,
    apply: bool = False,
    environ: Mapping[str, str] = os.environ,
    repository_factory: Callable[[str, str], object] | None = None,
    embedding_factory: Callable[[], object] | None = None,
    stdout: TextIO = sys.stdout,
) -> int:
    """Validate a seed file and, when requested, persist it in one submission."""
    rows = load_seed_rows(Path(path))
    payload = _build_payload(rows)
    print(f"Validated {len(payload.mappings)} mappings in {len(payload.groups)} groups.", file=stdout)
    if not apply:
        print("Dry run only; no mappings were submitted.", file=stdout)
        return 0

    url = environ.get("SUPABASE_URL", "").strip()
    key = environ.get("SUPABASE_SERVICE_KEY", "").strip()
    missing = [name for name, value in (("SUPABASE_URL", url), ("SUPABASE_SERVICE_KEY", key)) if not value]
    if missing:
        raise SeedValidationError(f"--apply requires {', '.join(missing)}")

    if repository_factory is None:
        from company_names.repository import SupabaseMappingRepository
        repository_factory = SupabaseMappingRepository.from_credentials
    if embedding_factory is None:
        from company_names.matching import FastEmbeddingProvider
        embedding_factory = FastEmbeddingProvider

    texts = [str(group["canonical_title"]) for group in payload.groups]
    texts.extend(str(mapping["cleaned_name"]) for mapping in payload.mappings)
    embedder = embedding_factory()
    vectors = []
    for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        vectors.extend(embedder.embed(texts[start : start + EMBEDDING_BATCH_SIZE]))
    payload = _with_embeddings(payload, vectors)
    repository_factory(url, key).submit(payload)
    print(f"Submitted request {payload.request_id}.", file=stdout)
    return 0


def main(argv: list[str] | None = None, *, stderr: TextIO = sys.stderr) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="CSV file to validate and import")
    parser.add_argument("--apply", action="store_true", help="persist the validated mappings")
    args = parser.parse_args(argv)
    try:
        return run(args.path, apply=args.apply)
    except Exception as exc:
        message = str(exc)
        for name in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
            value = os.environ.get(name, "")
            if value:
                message = message.replace(value, "[REDACTED]")
        print(f"error: {message}", file=stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
