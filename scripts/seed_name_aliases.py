#!/usr/bin/env python3
"""Import reviewed company-name aliases from CSV."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
import sys
from typing import Mapping, TextIO

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company_names.cleaning import clean_company_name, normalize_lookup_key
from company_names.repository import (
    AliasMapping,
    AliasRepository,
    SupabaseAliasRepository,
)


EXPECTED_HEADER = ["input_text", "target_text", "remarks"]


class SeedValidationError(ValueError):
    """The alias seed CSV is invalid."""


def load_alias_rows(path: Path) -> list[AliasMapping]:
    """Load validated aliases in deterministic alias-key order."""
    try:
        source = Path(path).open("r", encoding="utf-8-sig", newline="")
    except OSError:
        raise SeedValidationError("cannot read alias CSV") from None

    with source:
        reader = csv.DictReader(source)
        if reader.fieldnames != EXPECTED_HEADER:
            raise SeedValidationError(
                "CSV header must be exactly input_text,target_text,remarks"
            )

        mappings_by_key: dict[str, tuple[AliasMapping, int]] = {}
        for row_number, row in enumerate(reader, start=2):
            if None in row or any(row.get(field) is None for field in EXPECTED_HEADER):
                raise SeedValidationError(f"row {row_number} has malformed CSV fields")

            raw_input = row["input_text"]
            raw_target = row["target_text"]
            if not raw_input.strip():
                raise SeedValidationError(f"row {row_number} has an empty input_text value")
            canonical_name = raw_target.strip()
            if not canonical_name:
                raise SeedValidationError(f"row {row_number} has an empty target_text value")

            try:
                cleaned_alias = clean_company_name(raw_input)
                alias_key = normalize_lookup_key(cleaned_alias)
            except (TypeError, ValueError):
                raise SeedValidationError(
                    f"row {row_number} has an invalid input_text value"
                ) from None

            mapping = AliasMapping(cleaned_alias, alias_key, canonical_name)
            previous = mappings_by_key.get(alias_key)
            if previous is not None:
                previous_mapping, previous_row = previous
                if previous_mapping.canonical_name != canonical_name:
                    raise SeedValidationError(
                        f"row {row_number} conflicts with row {previous_row} "
                        f"for alias key {alias_key!r}"
                    )
                mappings_by_key[alias_key] = (
                    min(previous_mapping, mapping, key=lambda item: item.cleaned_alias),
                    min(previous_row, row_number),
                )
                continue
            mappings_by_key[alias_key] = (mapping, row_number)

    if not mappings_by_key:
        raise SeedValidationError("alias CSV contains no mappings")
    return [mappings_by_key[key][0] for key in sorted(mappings_by_key)]


def seed_aliases(path: Path, repository: AliasRepository) -> int:
    """Upsert one deterministic alias batch and return its size."""
    mappings = load_alias_rows(path)
    repository.upsert_aliases(mappings)
    return len(mappings)


def main(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] = os.environ,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path, dest="csv_path")
    parser.add_argument("--supabase-url")
    parser.add_argument("--supabase-service-key")
    args = parser.parse_args(argv)

    url = args.supabase_url or environ.get("SUPABASE_URL", "")
    key = args.supabase_service_key or environ.get("SUPABASE_SERVICE_KEY", "")
    try:
        repository = SupabaseAliasRepository.from_credentials(url, key)
        count = seed_aliases(args.csv_path, repository)
    except SeedValidationError as exc:
        print(f"error: {exc}", file=stderr)
        return 1
    except Exception:
        print("error: could not import company aliases", file=stderr)
        return 1

    print(count, file=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
