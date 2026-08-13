"""Streamlit review board and testable review-state transformations."""

from __future__ import annotations

from collections import Counter
import hashlib
import html
import json
import time
from uuid import uuid4

import pandas as pd

from .cleaning import normalize_lookup_key
from .models import Group, ReviewBoard
from .review import validate_submission
from .review_session import clear_final_results
from .service import (
    PreparedReview,
    AuthAttemptState,
    admin_password_digest,
    export_backup_csv,
    password_matches,
    submit_review_authorized,
)


WORKING = "working"
EXCLUDED = "excluded"
SEMANTIC_PILL_CSS = """
.semantic-pill.source-exact { background:#8FC5FF !important; border-color:#1769aa !important; }
.semantic-pill.source-suggested { background:#FFD166 !important; border-color:#9a6700 !important; }
.semantic-pill.source-unknown { background:#e9ecef !important; border-color:#555 !important; }
"""


def _review_styles() -> str:
    return """<style>
        .name-review, .name-review * { color: #000 !important; }
        .name-review { background: #fff; border: 3px solid #000; padding: 1rem; }
        .name-review-legend { border: 2px solid #000; padding: .5rem; }
        .semantic-pill { color:#000 !important; border:2px solid #000; border-radius:999px;
                         display:inline-block; margin:.25rem; padding:.25rem .6rem; font-weight:700; }
        """ + SEMANTIC_PILL_CSS + """
        </style><div class="name-review"><h2>Review company names</h2></div>"""


def _item_id(cleaned_name: str) -> str:
    digest = hashlib.sha256(cleaned_name.encode("utf-8")).hexdigest()
    return f"name-{digest}"


def _item(record) -> dict[str, str]:
    icon = "🟦" if record.source == "exact" else "🟨" if record.source == "suggested" else "⬜"
    return {
        "id": _item_id(record.cleaned_name),
        "name": record.cleaned_name,
        "label": f"{icon} {record.cleaned_name}",
    }


def _sortable_groups(board: ReviewBoard) -> list[Group]:
    """Return only groups that currently contain a selected report name."""
    referenced = {
        record.group_id
        for record in board.names.values()
        if record.selected and not record.excluded and record.group_id is not None
    }
    return [group for group in board.groups.values() if group.id in referenced]


def _display_groups(
    board: ReviewBoard, original_group_ids: set[str] | None = None
) -> list[Group]:
    """Return populated or originally referenced groups relevant to this report."""
    relevant_ids = {group.id for group in _sortable_groups(board)}
    relevant_ids.update(original_group_ids or set())
    return [group for group in board.groups.values() if group.id in relevant_ids]


def sortable_containers(board: ReviewBoard) -> list[dict[str, object]]:
    """Project selected board records into stable sortable containers."""
    result: list[dict[str, object]] = [{"id": WORKING, "header": "Working tray", "items": []}]
    ordered_groups = sorted(
        enumerate(_sortable_groups(board)),
        key=lambda item: (not item[1].existing, item[0]),
    )
    for _, group in ordered_groups:
        result.append(
            {
                "id": f"group:{group.id}",
                "header": group.canonical_title.strip() or "Untitled group",
                "items": [],
            }
        )
    result.append(
        {"id": EXCLUDED, "header": "Left out of this report", "items": []}
    )

    by_id = {container["id"]: container for container in result}
    for name in sorted(board.names, key=lambda value: (value.casefold(), value)):
        record = board.names[name]
        if not record.selected:
            continue
        destination = (
            EXCLUDED
            if record.excluded
            else f"group:{record.group_id}"
            if record.group_id is not None
            else WORKING
        )
        if destination not in by_id:
            raise ValueError(
                f"Selected name {record.cleaned_name!r} references missing group "
                f"{record.group_id!r}"
            )
        by_id[destination]["items"].append(_item(record))
    return result


def board_location_revision(
    board: ReviewBoard,
) -> tuple[tuple[str, bool, str | None, bool], ...]:
    """Return an immutable, order-independent snapshot of name placements."""
    return tuple(
        (
            name,
            record.selected,
            record.group_id,
            record.excluded,
        )
        for name, record in sorted(board.names.items())
    )


def _decode_item(value: object, lookup: dict[str, str]) -> str | None:
    if isinstance(value, dict):
        item_id = value.get("id")
    elif isinstance(value, str):
        item_id = value.rsplit("\u2063", 1)[-1]
    else:
        return None
    return lookup.get(item_id) if isinstance(item_id, str) else None


def apply_sort_result(board: ReviewBoard, containers: list[dict[str, object]]) -> None:
    """Apply a complete sortable result atomically; reject lossy component output."""
    selected = {name for name, record in board.names.items() if record.selected}
    lookup = {_item_id(name): name for name in selected}
    placements: list[tuple[str, str]] = []
    valid_destinations = {
        WORKING,
        EXCLUDED,
        *(f"group:{group.id}" for group in _sortable_groups(board)),
    }
    if not isinstance(containers, list):
        raise ValueError("Sortable result changed the board containers")
    destinations = [
        container.get("id") if isinstance(container, dict) else None
        for container in containers
    ]
    if (
        not all(isinstance(destination, str) for destination in destinations)
        or
        len(destinations) != len(valid_destinations)
        or set(destinations) != valid_destinations
        or len(set(destinations)) != len(destinations)
    ):
        raise ValueError("Sortable result changed the board containers")
    for container in containers:
        destination = container.get("id")
        items = container.get("items")
        if not isinstance(items, list):
            raise ValueError("Sortable result must contain item lists")
        for item in items:
            name = _decode_item(item, lookup)
            if name is None:
                raise ValueError("Sortable result must contain every selected name exactly once")
            placements.append((name, destination))
    if Counter(name for name, _ in placements) != Counter(selected):
        raise ValueError("Sortable result must contain every selected name exactly once")

    for name, destination in placements:
        record = board.names[name]
        record.selected = True
        record.excluded = destination == EXCLUDED
        record.group_id = destination.removeprefix("group:") if destination.startswith("group:") else None


def apply_sort_result_changed(
    board: ReviewBoard, containers: list[dict[str, object]]
) -> bool:
    """Apply sortable output and report whether any name changed location."""
    before = board_location_revision(board)
    apply_sort_result(board, containers)
    return board_location_revision(board) != before


def move_to_tray(board: ReviewBoard, names: list[str]) -> None:
    """Move known report names from any location into the working tray."""
    unique_names = list(dict.fromkeys(names))
    for name in unique_names:
        if name not in board.names:
            raise KeyError(name)

    for name in unique_names:
        record = board.names[name]
        record.selected = True
        record.group_id = None
        record.excluded = False


def add_selected_names(board: ReviewBoard, selected: list[str]) -> None:
    """Compatibility helper for moving search selections into the tray."""
    move_to_tray(board, selected)


def search_options(board: ReviewBoard) -> list[str]:
    """Return every report name; status formatting supplies its current location."""
    return sorted(board.names, key=lambda value: (value.casefold(), value))


def separate_company_names(board: ReviewBoard) -> list[str]:
    """Return automatic singleton names without creating per-name UI state."""
    return sorted(
        (
            name
            for name, record in board.names.items()
            if not record.selected and record.group_id is None and not record.excluded
        ),
        key=lambda value: (value.casefold(), value),
    )


def name_status(board: ReviewBoard, cleaned_name: str) -> str:
    record = board.names[cleaned_name]
    if not record.selected:
        status = "Separate company"
    elif record.excluded:
        status = "Left out of this report"
    elif record.group_id is None:
        status = "Working tray"
    else:
        group = board.groups.get(record.group_id)
        status = (
            f"Group: {group.canonical_title.strip() or 'Untitled group'}"
            if group
            else "Unknown group"
        )
    return f"{cleaned_name} — {status}"


def review_widget_key(request_id: str, *parts: str) -> str:
    """Prevent a new prepared review from inheriting prior widget values."""
    return ":".join(("name_review", str(request_id), *(str(part) for part in parts)))


def apply_group_titles(board: ReviewBoard, values: dict[str, str]) -> None:
    """Apply canonical-title widget values before projecting the review board."""
    for group_id, title in values.items():
        board.groups[group_id].canonical_title = title


def group_title_errors(board: ReviewBoard, values: dict[str, str]) -> dict[str, str]:
    """Validate proposed group titles together so duplicate errors are symmetric."""
    errors: dict[str, str] = {}
    normalized: dict[str, str] = {}
    relevant_ids = set(values)
    effective_titles = {
        group_id: values.get(group_id, group.canonical_title)
        for group_id, group in board.groups.items()
        if group_id in relevant_ids
    }
    for group_id in values:
        if group_id not in board.groups:
            raise KeyError(group_id)
    for group_id, title in effective_titles.items():
        if not title.strip():
            if group_id in values:
                errors[group_id] = "Enter a final company name."
            continue
        try:
            normalized[group_id] = normalize_lookup_key(title.strip())
        except ValueError:
            if group_id in values:
                errors[group_id] = "Enter a usable final company name."

    ids_by_title: dict[str, list[str]] = {}
    for group_id, normalized_title in normalized.items():
        ids_by_title.setdefault(normalized_title, []).append(group_id)
    for group_ids in ids_by_title.values():
        if len(group_ids) > 1:
            for group_id in group_ids:
                if group_id in values:
                    errors[group_id] = "Another group uses the same final company name."
    return errors


def return_to_separate(board: ReviewBoard, cleaned_name: str) -> None:
    """Return one report name to the separate-company list."""
    if cleaned_name not in board.names:
        raise KeyError(cleaned_name)
    record = board.names[cleaned_name]
    record.selected = False
    record.group_id = None
    record.excluded = False


def return_to_inventory(board: ReviewBoard, cleaned_name: str) -> None:
    """Compatibility alias for returning a name to separate companies."""
    return_to_separate(board, cleaned_name)


def _tray_names(board: ReviewBoard) -> list[str]:
    return [
        name
        for name, record in board.names.items()
        if record.selected and record.group_id is None and not record.excluded
    ]


def group_creation_error(board: ReviewBoard, title: str) -> str | None:
    """Return the first actionable error for creating a combined group."""
    trimmed_title = title.strip()
    if not trimmed_title:
        return "Enter the final company name."
    if len(_tray_names(board)) < 2:
        return "Add at least two names to the working tray."
    try:
        normalize_lookup_key(trimmed_title)
    except ValueError:
        return "Enter a usable final company name."

    try:
        matching_group = matching_group_for_title(board, trimmed_title)
    except ValueError:
        return "More than one existing group uses that final company name."
    if matching_group is not None:
        return (
            f"A group named ‘{matching_group.canonical_title}’ already exists. "
            "Move these names into that group instead."
        )
    return None


def _normalized_group_title(title: str) -> str | None:
    try:
        return normalize_lookup_key(title)
    except ValueError:
        return None


def matching_group_for_title(board: ReviewBoard, title: str) -> Group | None:
    """Return the unique group matching a proposed title, including hidden candidates."""
    normalized_title = normalize_lookup_key(title.strip())
    matches = sorted(
        (
            group
            for group in board.groups.values()
            if _normalized_group_title(group.canonical_title) == normalized_title
        ),
        key=lambda group: group.id,
    )
    if len(matches) > 1:
        raise ValueError("Multiple groups use the same final company name")
    return matches[0] if matches else None


def move_tray_to_group(board: ReviewBoard, group_id: str) -> None:
    """Move every current working-tray name into one validated group."""
    if group_id not in board.groups:
        raise KeyError(group_id)
    for name in _tray_names(board):
        record = board.names[name]
        record.selected = True
        record.group_id = group_id
        record.excluded = False


def create_combined_group(board: ReviewBoard, title: str) -> Group:
    """Create a group from every name currently in the working tray."""
    error = group_creation_error(board, title)
    if error:
        raise ValueError(error)
    tray_names = _tray_names(board)
    group = Group(f"new-{uuid4()}", title.strip(), False)
    board.groups[group.id] = group
    for name in tray_names:
        record = board.names[name]
        record.selected = True
        record.group_id = group.id
        record.excluded = False
    return group


def review_summary(board: ReviewBoard) -> dict[str, int]:
    """Return stable counts for the singleton-first review locations."""
    grouped_names = [
        record
        for record in board.names.values()
        if record.selected and record.group_id is not None and not record.excluded
    ]
    return {
        "separate": sum(not record.selected for record in board.names.values()),
        "combined_groups": len({record.group_id for record in grouped_names}),
        "combined_names": len(grouped_names),
        "tray": len(_tray_names(board)),
        "excluded": sum(record.excluded for record in board.names.values()),
    }


def review_errors(
    board: ReviewBoard, title_errors: dict[str, str]
) -> list[str]:
    """Combine domain submission errors with errors for submitted existing groups."""
    errors = validate_submission(board)
    errors.extend(
        f"{board.groups[group_id].canonical_title or 'Untitled group'}: {error}"
        for group_id, error in title_errors.items()
    )
    return errors


def create_group(board: ReviewBoard) -> Group:
    """Append a stable, empty client-side group."""
    group = Group(f"new-{uuid4()}", "", False)
    board.groups[group.id] = group
    return group


def _component_containers(containers: list[dict[str, object]]) -> list[dict[str, object]]:
    def invisible_id(value: str) -> str:
        # streamlit-sortables uses the header as its internal container ID. Encode a
        # unique suffix with zero-width characters while keeping the visible title.
        bits = "".join(f"{byte:08b}" for byte in value.encode("utf-8"))
        return "\u2063" + "".join("\u200b" if bit == "0" else "\u200c" for bit in bits)

    return [
        {
            "header": f"{container['header']}{invisible_id(str(container['id']))}",
            "items": [f"{item['label']}\u2063{item['id']}" for item in container["items"]],
        }
        for container in containers
    ]


def _decode_container_id(header: object) -> str:
    if not isinstance(header, str) or "\u2063" not in header:
        raise ValueError("Sortable result changed the board containers")
    encoded = header.rsplit("\u2063", 1)[1]
    if not encoded or any(character not in {"\u200b", "\u200c"} for character in encoded):
        raise ValueError("Sortable result changed the board containers")
    bits = "".join("0" if character == "\u200b" else "1" for character in encoded)
    if len(bits) % 8:
        raise ValueError("Sortable result changed the board containers")
    try:
        return bytes(int(bits[start : start + 8], 2) for start in range(0, len(bits), 8)).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        raise ValueError("Sortable result changed the board containers") from None


def _board_revision(board: ReviewBoard) -> str:
    """Hash server-side state so external mutations remount the React component."""
    value = {
        "groups": [
            (group.id, group.canonical_title, group.existing)
            for group in board.groups.values()
        ],
        "names": [
            (name, record.group_id, record.selected, record.excluded)
            for name, record in sorted(board.names.items())
        ],
    }
    return hashlib.sha256(json.dumps(value).encode("utf-8")).hexdigest()[:16]


def _restore_container_ids(result, source):
    if not isinstance(result, list):
        raise ValueError("Sortable result changed the board containers")
    expected = {str(container["id"]) for container in source}
    expected_headers = {
        str(container["id"]): rendered["header"]
        for container, rendered in zip(source, _component_containers(source))
    }
    restored = []
    for returned in result:
        if not isinstance(returned, dict):
            raise ValueError("Sortable result changed the board containers")
        container_id = _decode_container_id(returned.get("header"))
        if returned.get("header") != expected_headers.get(container_id):
            raise ValueError("Sortable result changed the board containers")
        restored.append(
            {"id": container_id, "header": returned.get("header"), "items": returned.get("items", [])}
        )
    returned_ids = [container["id"] for container in restored]
    if len(returned_ids) != len(expected) or set(returned_ids) != expected or len(set(returned_ids)) != len(returned_ids):
        raise ValueError("Sortable result changed the board containers")
    return restored


def _semantic_pill_preview(board: ReviewBoard) -> str:
    """Render colored, per-container pills beside the string-only drag component."""
    sections = []
    for container in sortable_containers(board):
        pills = []
        for item in container["items"]:
            name = item["name"]
            record = board.names[name]
            pills.append(semantic_pill(record))
        sections.append(
            '<section class="semantic-container" '
            f'data-container="{html.escape(str(container["id"]), quote=True)}">'
            f'<strong>{html.escape(str(container["header"]))}</strong>'
            f'{"".join(pills)}</section>'
        )
    return "".join(sections)


def semantic_pill(record) -> str:
    """Return an escaped, high-contrast pill with a textual match meaning."""
    styles = {
        "exact": ("#8FC5FF", "Exact match"),
        "suggested": ("#FFD166", "Suggested match"),
        "unknown": ("#e9ecef", "Unmatched"),
    }
    style = styles.get(record.source)
    if style is None:
        raise ValueError(
            f"Selected name {record.cleaned_name!r} has invalid source {record.source!r}"
        )
    color, meaning = style
    escaped = html.escape(record.cleaned_name, quote=True)
    return (
        f'<span class="semantic-pill source-{record.source}" style="background:{color}" '
        f'aria-label="{meaning}: {escaped}">{escaped}</span>'
    )


def _move_record(board: ReviewBoard, name: str, destination: str) -> None:
    if destination == "Separate companies":
        return_to_separate(board, name)
        return
    record = board.names[name]
    record.selected = True
    record.excluded = destination == "Left out of this report"
    record.group_id = None
    if destination not in {"Working tray", "Left out of this report"}:
        record.group_id = destination.removeprefix("group:")


def _add_from_search(board: ReviewBoard, widget_key: str) -> None:
    """Streamlit callback that consumes selections without stale widget values."""
    import streamlit as st

    add_selected_names(board, list(st.session_state.get(widget_key, [])))
    st.session_state[widget_key] = []


def _create_group_from_widget(board: ReviewBoard, widget_key: str) -> None:
    """Create a named group in a widget callback and clear request-local input."""
    import streamlit as st

    create_combined_group(board, str(st.session_state.get(widget_key, "")))
    st.session_state[widget_key] = ""


def _return_to_separate_callback(board: ReviewBoard, cleaned_name: str) -> None:
    return_to_separate(board, cleaned_name)


def _move_to_tray_callback(board: ReviewBoard, cleaned_name: str) -> None:
    move_to_tray(board, [cleaned_name])


def _move_tray_to_group_callback(
    board: ReviewBoard, group_id: str, title_key: str
) -> None:
    import streamlit as st

    move_tray_to_group(board, group_id)
    st.session_state[title_key] = ""


def _bind_admin_session(session_state, expected_password: str | None) -> AuthAttemptState:
    """Invalidate authorization when configuration changes and return its throttle."""
    digest = admin_password_digest(expected_password)
    if session_state.get("mapping_admin_password_digest") != digest:
        session_state["mapping_admin_unlocked"] = False
        session_state["mapping_admin_password_digest"] = digest
        session_state["mapping_admin_attempts"] = AuthAttemptState()
    attempts = session_state.get("mapping_admin_attempts")
    if not isinstance(attempts, AuthAttemptState):
        attempts = AuthAttemptState()
        session_state["mapping_admin_attempts"] = attempts
    return attempts


def _unlock_admin(
    expected_password: str | None, password_key: str, now: float | None = None
) -> None:
    """Consume the candidate password and retain only the authorization decision."""
    import streamlit as st

    candidate = st.session_state.pop(password_key, None)
    attempts = _bind_admin_session(st.session_state, expected_password)
    current = time.monotonic() if now is None else now
    retry_after = attempts.retry_after(current)
    if retry_after:
        st.session_state["mapping_admin_unlocked"] = False
        st.session_state["mapping_admin_auth_error"] = (
            f"Too many failed attempts. Retry in {retry_after} seconds."
        )
        return
    if password_matches(candidate, expected_password):
        attempts.record_success()
        st.session_state["mapping_admin_unlocked"] = True
        st.session_state.pop("mapping_admin_auth_error", None)
    else:
        st.session_state["mapping_admin_unlocked"] = False
        retry_after = attempts.record_failure(current)
        st.session_state["mapping_admin_auth_error"] = (
            f"Too many failed attempts. Retry in {retry_after} seconds."
            if retry_after
            else "Incorrect admin password."
        )


def render_name_review(
    prepared: PreparedReview,
    repository,
    embedder,
    admin_password: str | None = None,
) -> pd.DataFrame | None:
    """Render, authorize, and atomically persist the searchable review board."""
    import streamlit as st

    board = prepared.board
    auth_attempts = _bind_admin_session(st.session_state, admin_password)
    auth_retry_after = auth_attempts.retry_after(time.monotonic())
    request_id = str(prepared.pending_request_id or id(prepared))
    init_key = review_widget_key(request_id, "initialized")
    if not st.session_state.get(init_key):
        for record in board.names.values():
            if record.source != "exact" and record.group_id is None:
                record.selected = False
                record.excluded = False
        st.session_state[init_key] = True

    for warning in prepared.warnings:
        st.warning(warning)

    st.markdown(_review_styles(), unsafe_allow_html=True)
    st.markdown("**1. Find names  →  2. Combine duplicates  →  3. Review and save**")
    st.write(
        "Most company names should stay separate. Find only the duplicates you want "
        "to combine, then move them into the Working tray."
    )
    st.markdown(
        '<div class="name-review-legend" aria-label="Name match legend">'
        "🟦 Exact database match &nbsp; 🟨 Suggested match &nbsp; ⬜ Unmatched; "
        "every name also shows its current location.</div>",
        unsafe_allow_html=True,
    )

    st.subheader("1. Find names")
    search_key = review_widget_key(request_id, "search")
    st.multiselect(
        "Search every company name in this report",
        options=search_options(board),
        format_func=lambda name: name_status(board, name),
        placeholder="Type a company name",
        key=search_key,
        on_change=_add_from_search,
        args=(board, search_key),
    )

    separate_names = separate_company_names(board)
    st.markdown(f"### Separate companies ({len(separate_names)})")
    st.write("Names left under Separate companies will be saved separately automatically.")
    with st.expander(f"View separate companies ({len(separate_names)})"):
        if separate_names:
            escaped_names = "".join(
                f'<span class="semantic-pill source-{board.names[name].source}">'
                f'{html.escape(name, quote=True)}</span>'
                for name in separate_names
            )
            st.markdown(escaped_names, unsafe_allow_html=True)
        else:
            st.write("No names are currently separate.")

    original_group_ids = set(prepared.original_mappings.values())
    ordered_groups = [
        group
        for _, group in sorted(
            enumerate(_display_groups(board, original_group_ids)),
            key=lambda item: (not item[1].existing, item[0]),
        )
    ]
    st.subheader("2. Combine duplicates")
    tray_names = sorted(_tray_names(board), key=lambda value: (value.casefold(), value))
    st.markdown(f"### Working tray ({len(tray_names)})")
    if not tray_names:
        st.write("Search for duplicate names to add them here.")
    for name in tray_names:
        columns = st.columns([4, 1])
        columns[0].markdown(semantic_pill(board.names[name]), unsafe_allow_html=True)
        columns[1].button(
            "Return to separate",
            key=review_widget_key(request_id, "return_to_separate", _item_id(name)),
            on_click=_return_to_separate_callback,
            args=(board, name),
        )

    st.markdown("### Combined groups")
    title_values = {}
    title_error_slots = {}
    for group in ordered_groups:
        title_values[group.id] = st.text_input(
            f"Final company name for {group.canonical_title.strip() or 'untitled group'}",
            value=group.canonical_title,
            key=review_widget_key(request_id, "group_title", group.id),
        )
        title_error_slots[group.id] = st.empty()
        member_names = sorted(
            (
                name
                for name, record in board.names.items()
                if record.selected and not record.excluded and record.group_id == group.id
            ),
            key=lambda value: (value.casefold(), value),
        )
        for name in member_names:
            columns = st.columns([4, 1])
            columns[0].markdown(semantic_pill(board.names[name]), unsafe_allow_html=True)
            columns[1].button(
                "Move to tray",
                key=review_widget_key(request_id, "move_to_tray", group.id, _item_id(name)),
                on_click=_move_to_tray_callback,
                args=(board, name),
            )
    title_errors = group_title_errors(board, title_values)
    for group_id, error in title_errors.items():
        title_error_slots[group_id].error(error)
    apply_group_titles(board, title_values)

    title_key = review_widget_key(request_id, "final_company_name")
    new_title = st.text_input(
        "Final company name",
        placeholder="Type the name this combined group should use",
        key=title_key,
    )
    creation_error = group_creation_error(board, new_title)
    if creation_error:
        st.caption(creation_error)
    matching_group = None
    if new_title and creation_error:
        try:
            matching_group = matching_group_for_title(board, new_title)
        except ValueError:
            st.error("More than one existing group uses that name. Correct the group titles first.")
    if matching_group is not None and len(_tray_names(board)) >= 2:
        st.button(
            f"Move tray names to {matching_group.canonical_title}",
            key=review_widget_key(
                request_id, "move_tray_to_group", matching_group.id
            ),
            on_click=_move_tray_to_group_callback,
            args=(board, matching_group.id, title_key),
        )
    st.button(
        "Create combined group",
        key=review_widget_key(request_id, "create_combined_group"),
        disabled=creation_error is not None,
        on_click=_create_group_from_widget,
        args=(board, title_key),
    )

    try:
        projected_containers = sortable_containers(board)
    except ValueError as exc:
        st.error(f"Review state is invalid: {exc}")
        st.button(
            "Save mappings and show totals",
            key=review_widget_key(request_id, "invalid_board_submit"),
            disabled=True,
        )
        return None

    try:
        preview = _semantic_pill_preview(board)
    except ValueError as exc:
        st.error(f"Review state is invalid: {exc}")
        st.button(
            "Save mappings and show totals",
            key=review_widget_key(request_id, "invalid_source_submit"),
            disabled=True,
        )
        return None
    st.markdown(
        f'<div aria-label="Selected name color preview">{preview}</div>',
        unsafe_allow_html=True,
    )

    containers = projected_containers
    try:
        from streamlit_sortables import sort_items

        result = sort_items(
            _component_containers(containers),
            multi_containers=True,
            key=review_widget_key(request_id, "board", _board_revision(board)),
            custom_style="""
              .sortable-component { color:#000 !important; border:2px solid #000 !important; }
              .sortable-container:last-child { border:3px dashed #000 !important; }
              .sortable-item { color:#000 !important; background:#e9ecef !important; border:2px solid #555 !important; }
            """,
        )
        if apply_sort_result_changed(
            board, _restore_container_ids(result, containers)
        ):
            st.rerun()
    except ImportError:
        st.warning("Drag-and-drop is unavailable; use Move a company name below.")
    except ValueError:
        st.warning("The board returned an incomplete update. No names were moved; please try again.")

    with st.expander("Move a company name", expanded=True):
        chosen = st.selectbox(
            "Name",
            options=[""] + search_options(board),
            key=review_widget_key(request_id, "move_name"),
        )
        if chosen:
            destinations = ["Separate companies", "Working tray"] + [
                f"group:{group.id}"
                for group in _display_groups(board, original_group_ids)
            ] + ["Left out of this report"]
            labels = {
                f"group:{group.id}": f"Group: {group.canonical_title.strip() or 'Untitled group'}"
                for group in _display_groups(board, original_group_ids)
            }
            destination = st.selectbox(
                "Move to",
                destinations,
                format_func=lambda value: labels.get(value, value),
                key=review_widget_key(request_id, "move_destination"),
            )
            if st.button("Move", key=review_widget_key(request_id, "move")):
                _move_record(board, chosen, destination)
                st.rerun()

    errors = review_errors(board, title_errors)
    if errors:
        st.error("Review is not ready:\n\n" + "\n\n".join(f"- {error}" for error in errors))

    st.subheader("3. Review and save")
    summary = review_summary(board)
    st.write(
        f"{summary['separate']} separate companies · "
        f"{summary['combined_groups']} combined groups · "
        f"{summary['excluded']} left out"
    )

    unlocked = bool(st.session_state.get("mapping_admin_unlocked"))
    if not admin_password:
        st.error(
            "Admin password is not configured. Add ADMIN_PASSWORD to Streamlit secrets; "
            "submission and backup export are disabled."
        )
    elif not unlocked:
        password_key = review_widget_key(request_id, "admin_password")
        with st.form(review_widget_key(request_id, "admin_unlock")):
            st.text_input("Admin password", type="password", key=password_key)
            st.form_submit_button(
                "Confirm admin password",
                on_click=_unlock_admin,
                args=(admin_password, password_key),
                disabled=bool(auth_retry_after),
            )
        if auth_retry_after:
            st.warning(f"Too many failed attempts. Retry in {auth_retry_after} seconds.")
        auth_error = st.session_state.pop("mapping_admin_auth_error", None)
        if auth_error:
            st.error(auth_error)

    unlocked = bool(st.session_state.get("mapping_admin_unlocked"))
    if unlocked and admin_password:
        with st.expander("Backup and recovery"):
            st.write("Download a CSV copy of all permanent company-name mappings.")
            if st.button("Prepare backup file", key=review_widget_key(request_id, "backup")):
                backup = export_backup_csv(repository, admin_password, admin_password)
                if backup.error:
                    st.error(backup.error)
                elif backup.data is not None:
                    st.download_button(
                        "Download mappings backup",
                        data=backup.data,
                        file_name="company_name_mappings.csv",
                        mime="text/csv; charset=utf-8",
                        key=review_widget_key(request_id, "backup_download"),
                    )

    clicked = st.button(
        "Save mappings and show totals",
        key=review_widget_key(request_id, "submit"),
        disabled=bool(errors) or not unlocked or not bool(admin_password),
        help="Resolve names in the Working tray and confirm the admin password first.",
    )
    if clicked:
        # A retry is a new display attempt even when its database request ID is
        # intentionally reused. Never leave prior totals visible if it fails.
        clear_final_results(st.session_state)
        outcome = submit_review_authorized(
            prepared, repository, embedder, admin_password, admin_password
        )
        if outcome.warning:
            st.warning(outcome.warning)
        if outcome.error:
            st.error(outcome.error)
            return None
        if outcome.result is not None:
            st.success("Mappings saved.")
            return outcome.result
    return None
