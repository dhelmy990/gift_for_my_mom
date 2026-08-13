"""Validation, persistence payloads, and reporting for review boards."""

from copy import deepcopy
from hashlib import sha256

import pandas as pd

from .cleaning import normalize_lookup_key
from .models import Group, ReviewBoard, SubmissionPayload


def _name_key_errors(board: ReviewBoard) -> list[tuple[str, str]]:
    return [
        (
            name_key,
            f"Name key {name_key!r} does not match "
            f"NameRecord.cleaned_name {record.cleaned_name!r}",
        )
        for name_key, record in board.names.items()
        if name_key != record.cleaned_name
    ]


def singleton_group_id(cleaned_name: str) -> str:
    """Return the deterministic group ID for an implicit singleton."""
    digest = sha256(cleaned_name.encode("utf-8")).hexdigest()
    return f"new-singleton-{digest}"


def materialize_singletons(board: ReviewBoard) -> ReviewBoard:
    """Copy a board and turn Separate-company records into singleton groups."""
    materialized = deepcopy(board)
    for cleaned_name in sorted(materialized.names):
        record = materialized.names[cleaned_name]
        if not record.selected and not record.excluded and record.group_id is None:
            group_id = singleton_group_id(cleaned_name)
            materialized.groups[group_id] = Group(group_id, cleaned_name, False)
            record.selected = True
            record.group_id = group_id
    return materialized


def validate_board(board: ReviewBoard) -> list[str]:
    """Return deterministic descriptions of invalid review state."""
    errors = _name_key_errors(board)
    populated_group_ids = {
        record.group_id
        for record in board.names.values()
        if record.group_id is not None
    }

    for cleaned_name in sorted(board.names):
        record = board.names[cleaned_name]
        if not record.selected and (record.group_id is not None or record.excluded):
            errors.append(
                (
                    cleaned_name,
                    f"{cleaned_name} is inventory but still has grouping or exclusion state",
                )
            )
        if record.excluded and record.group_id is not None:
            errors.append((cleaned_name, f"{cleaned_name} is both excluded and grouped"))
        if record.group_id is not None and record.group_id not in board.groups:
            errors.append(
                (cleaned_name, f"{cleaned_name} references unknown group {record.group_id}")
            )
    populated_groups = [
        group
        for group_id, group in board.groups.items()
        if group_id in populated_group_ids
    ]
    titled_groups = []
    for group in populated_groups:
        if not group.canonical_title.strip():
            errors.append(
                (group.canonical_title, f"Group {group.id} has a blank canonical title")
            )
        else:
            titled_groups.append(group)

    groups_by_title: dict[str, list[Group]] = {}
    for group in titled_groups:
        try:
            normalized_title = normalize_lookup_key(group.canonical_title)
        except ValueError:
            errors.append(
                (
                    group.canonical_title,
                    f"Group {group.id} title {group.canonical_title} "
                    "cannot form a lookup key",
                )
            )
            continue
        groups_by_title.setdefault(normalized_title, []).append(group)
    for normalized_title in sorted(groups_by_title):
        duplicates = groups_by_title[normalized_title]
        if len(duplicates) > 1:
            titles = sorted(group.canonical_title for group in duplicates)
            errors.append(
                (
                    normalized_title,
                    f"Duplicate populated group title: {' / '.join(titles)}",
                )
            )

    return [message for _, message in sorted(errors, key=lambda item: (item[0], item[1]))]


def validate_submission(board: ReviewBoard) -> list[str]:
    """Validate a board for submission, including an empty working tray."""
    errors = validate_board(board)
    working_tray_count = sum(
        record.selected and not record.excluded and record.group_id is None
        for record in board.names.values()
    )
    if working_tray_count:
        name_label = "name" if working_tray_count == 1 else "names"
        errors.append(
            f"Resolve {working_tray_count} {name_label} in the working tray: create a "
            "combined group or return them to Separate companies."
        )
    return errors


def build_submission(
    board: ReviewBoard,
    original_mappings: dict[str, str],
    request_id: str | None = None,
) -> SubmissionPayload:
    """Build a payload, optionally preserving the request ID for a retry."""
    errors = validate_board(board)
    for cleaned_name in sorted(board.names):
        record = board.names[cleaned_name]
        original_group_id = original_mappings.get(cleaned_name)
        if (
            original_group_id is not None
            and record.selected
            and not record.excluded
            and record.group_id is not None
            and record.group_id != original_group_id
        ):
            errors.append(
                f"{cleaned_name} is already mapped to {original_group_id} "
                f"and cannot be remapped to {record.group_id}"
            )
    if errors:
        raise ValueError("\n".join(errors))

    populated_group_ids = {
        record.group_id
        for record in board.names.values()
        if record.selected and not record.excluded and record.group_id is not None
    }
    groups = [
        {
            "id": group.id,
            "canonical_title": group.canonical_title,
            "existing": group.existing,
        }
        for group in sorted(board.groups.values(), key=lambda group: group.id)
        if group.existing or group.id in populated_group_ids
    ]
    mappings_by_name: dict[str, dict[str, object]] = {}
    for cleaned_name, record in sorted(board.names.items()):
        if record.selected and not record.excluded and record.group_id is not None:
            storage_name = record.persisted_name or cleaned_name
            mapping = {"cleaned_name": storage_name, "group_id": record.group_id}
            prior = mappings_by_name.get(storage_name)
            if prior is not None and prior != mapping:
                raise ValueError(f"Persisted name {storage_name} has conflicting groups")
            mappings_by_name[storage_name] = mapping
    mappings = list(mappings_by_name.values())
    unmap_names = sorted({
        record.persisted_name or cleaned_name
        for cleaned_name, record in sorted(board.names.items())
        if cleaned_name in original_mappings and not record.selected
    })
    if request_id is None:
        return SubmissionPayload(groups, mappings, unmap_names)
    return SubmissionPayload(groups, mappings, unmap_names, request_id)


def aggregate_by_group(rows: pd.DataFrame, board: ReviewBoard) -> pd.DataFrame:
    """Aggregate included input rows under their groups' canonical titles."""
    name_key_errors = _name_key_errors(board)
    if name_key_errors:
        raise ValueError(
            "\n".join(
                message
                for _, message in sorted(
                    name_key_errors, key=lambda item: (item[0], item[1])
                )
            )
        )
    included = {
        cleaned_name: board.groups[record.group_id].canonical_title
        for cleaned_name, record in board.names.items()
        if record.selected
        and not record.excluded
        and record.group_id is not None
        and record.group_id in board.groups
    }
    selected_rows = rows[rows["cleaned_name"].isin(included)].copy()
    selected_rows["TRAVEL AGENT"] = selected_rows["cleaned_name"].map(included)
    result = (
        selected_rows.groupby("TRAVEL AGENT", as_index=False, sort=False)[
            ["rns", "revenue"]
        ]
        .sum()
        .rename(columns={"rns": "Sum of RNS", "revenue": "Sum of R REVENUE"})
        .sort_values("Sum of R REVENUE", ascending=False, kind="stable")
        .reset_index(drop=True)
    )
    result[["Sum of RNS", "Sum of R REVENUE"]] = result[
        ["Sum of RNS", "Sum of R REVENUE"]
    ].astype(float)
    return result[["TRAVEL AGENT", "Sum of RNS", "Sum of R REVENUE"]]
