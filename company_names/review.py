"""Validation, persistence payloads, and reporting for review boards."""

import pandas as pd

from .cleaning import normalize_lookup_key
from .models import Group, ReviewBoard, SubmissionPayload


def validate_board(board: ReviewBoard) -> list[str]:
    """Return deterministic descriptions of invalid review state."""
    errors: list[tuple[str, str]] = []
    populated_group_ids = {
        record.group_id
        for record in board.names.values()
        if record.group_id is not None
    }

    for cleaned_name in sorted(board.names):
        record = board.names[cleaned_name]
        if record.excluded and record.group_id is not None:
            errors.append((cleaned_name, f"{cleaned_name} is both excluded and grouped"))
        if record.group_id is not None and record.group_id not in board.groups:
            errors.append(
                (cleaned_name, f"{cleaned_name} references unknown group {record.group_id}")
            )
        if record.selected and not record.excluded and record.group_id is None:
            errors.append((cleaned_name, f"{cleaned_name} is included but ungrouped"))

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
        groups_by_title.setdefault(normalize_lookup_key(group.canonical_title), []).append(group)
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

    return [message for _, message in sorted(errors, key=lambda item: item[0])]


def build_submission(
    board: ReviewBoard, original_mappings: dict[str, str]
) -> SubmissionPayload:
    """Build a persistence payload, rejecting invalid state and direct remaps."""
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
    mappings = [
        {"cleaned_name": cleaned_name, "group_id": record.group_id}
        for cleaned_name, record in sorted(board.names.items())
        if record.selected and not record.excluded and record.group_id is not None
    ]
    unmap_names = [
        cleaned_name
        for cleaned_name, record in sorted(board.names.items())
        if cleaned_name in original_mappings and not record.selected
    ]
    return SubmissionPayload(groups, mappings, unmap_names)


def aggregate_by_group(rows: pd.DataFrame, board: ReviewBoard) -> pd.DataFrame:
    """Aggregate included input rows under their groups' canonical titles."""
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
