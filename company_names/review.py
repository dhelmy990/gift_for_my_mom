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


def materialize_singletons(
    board: ReviewBoard,
    original_mappings: dict[str, str] | None = None,
) -> ReviewBoard:
    """Copy a board and turn Separate-company records into singleton groups."""
    materialized = deepcopy(board)
    persisted_origins = original_mappings or {}
    group_ids_by_title: dict[str, list[str]] = {}
    for group_id, group in materialized.groups.items():
        try:
            normalized_title = normalize_lookup_key(group.canonical_title)
        except ValueError:
            continue
        group_ids_by_title.setdefault(normalized_title, []).append(group_id)

    for cleaned_name in sorted(materialized.names):
        record = materialized.names[cleaned_name]
        if not record.selected and not record.excluded and record.group_id is None:
            all_matching_group_ids = sorted(
                group_ids_by_title.get(normalize_lookup_key(cleaned_name), [])
            )
            original_group_id = persisted_origins.get(cleaned_name)
            if original_group_id in all_matching_group_ids:
                raise ValueError(
                    f"Cannot return {cleaned_name} to Separate companies because its "
                    "current group has the same title. Rename the existing group "
                    "before separating this name."
                )
            matching_group_ids = [
                group_id
                for group_id in all_matching_group_ids
                if group_id != original_group_id
            ]
            if len(matching_group_ids) > 1:
                matches = ", ".join(matching_group_ids)
                raise ValueError(
                    f"Cannot materialize singleton for {cleaned_name}: normalized "
                    f"title matches multiple groups: {matches}"
                )
            if matching_group_ids:
                record.selected = True
                record.group_id = matching_group_ids[0]
                continue

            group_id = singleton_group_id(cleaned_name)
            existing_group = materialized.groups.get(group_id)
            if existing_group is None:
                materialized.groups[group_id] = Group(group_id, cleaned_name, False)
                normalized_title = normalize_lookup_key(cleaned_name)
                group_ids_by_title.setdefault(normalized_title, []).append(group_id)
            elif (
                existing_group.id != group_id
                or existing_group.canonical_title != cleaned_name
                or existing_group.existing
            ):
                raise ValueError(
                    f"Cannot materialize singleton for {cleaned_name}: derived group ID "
                    f"{group_id} conflicts with an existing group"
                )
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
    """Build atomic mapping mutations from a materialized copy of the board."""
    errors = validate_submission(board)
    if errors:
        raise ValueError("\n".join(errors))

    materialized = materialize_singletons(board, original_mappings)
    return _build_submission_from_materialized(
        materialized, original_mappings, request_id=request_id
    )


def _build_submission_from_materialized(
    materialized: ReviewBoard,
    original_mappings: dict[str, str],
    request_id: str | None = None,
) -> SubmissionPayload:
    """Build a payload from a previously materialized, independent board."""
    errors = validate_board(materialized)
    if errors:
        raise ValueError("\n".join(errors))

    populated_group_ids = {
        record.group_id
        for record in materialized.names.values()
        if record.selected and not record.excluded and record.group_id is not None
    }
    groups = [
        {
            "id": group.id,
            "canonical_title": group.canonical_title,
            "existing": group.existing,
        }
        for group in sorted(materialized.groups.values(), key=lambda group: group.id)
        if group.existing or group.id in populated_group_ids
    ]
    mappings_by_name: dict[str, dict[str, object]] = {}
    unmap_names: set[str] = set()
    for cleaned_name, record in sorted(materialized.names.items()):
        if record.selected and not record.excluded and record.group_id is not None:
            storage_name = record.persisted_name or cleaned_name
            mapping = {"cleaned_name": storage_name, "group_id": record.group_id}
            prior = mappings_by_name.get(storage_name)
            if prior is not None and prior != mapping:
                raise ValueError(f"Persisted name {storage_name} has conflicting groups")
            mappings_by_name[storage_name] = mapping
            original_group_id = original_mappings.get(cleaned_name)
            if original_group_id is not None and original_group_id != record.group_id:
                unmap_names.add(storage_name)
    mappings = list(mappings_by_name.values())
    if request_id is None:
        return SubmissionPayload(groups, mappings, sorted(unmap_names))
    return SubmissionPayload(groups, mappings, sorted(unmap_names), request_id)


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
    included: dict[str, str] = {}
    for cleaned_name, record in board.names.items():
        if record.excluded:
            continue
        if (
            record.selected
            and record.group_id is not None
            and record.group_id in board.groups
        ):
            included[cleaned_name] = board.groups[record.group_id].canonical_title
        elif not record.selected and record.group_id is None:
            included[cleaned_name] = cleaned_name
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
