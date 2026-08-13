"""Streamlit review board and testable review-state transformations."""

from __future__ import annotations

from collections import Counter
import hashlib
import html
import json
from uuid import uuid4

import pandas as pd

from .models import Group, ReviewBoard
from .review import validate_board
from .review_session import clear_final_results
from .service import (
    PreparedReview,
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


def sortable_containers(board: ReviewBoard) -> list[dict[str, object]]:
    """Project selected board records into stable sortable containers."""
    result: list[dict[str, object]] = [{"id": WORKING, "header": "Working tray", "items": []}]
    ordered_groups = sorted(
        enumerate(board.groups.values()),
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
        {"id": EXCLUDED, "header": "Excluded from this report", "items": []}
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
    valid_destinations = {WORKING, EXCLUDED, *(f"group:{group_id}" for group_id in board.groups)}
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


def add_selected_names(board: ReviewBoard, selected: list[str]) -> None:
    """Move inventory records into the working tray, idempotently."""
    for name in dict.fromkeys(selected):
        if name not in board.names:
            raise KeyError(name)
        record = board.names[name]
        if not record.selected:
            record.selected = True
            record.group_id = None
            record.excluded = False


def search_options(board: ReviewBoard) -> list[str]:
    """Return every report name; status formatting supplies its current location."""
    return sorted(board.names, key=lambda value: (value.casefold(), value))


def name_status(board: ReviewBoard, cleaned_name: str) -> str:
    record = board.names[cleaned_name]
    if not record.selected:
        status = "In inventory"
    elif record.excluded:
        status = "Excluded"
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


def return_to_inventory(board: ReviewBoard, cleaned_name: str) -> None:
    """Explicitly remove one name from all review-board containers."""
    if cleaned_name not in board.names:
        raise KeyError(cleaned_name)
    record = board.names[cleaned_name]
    record.selected = False
    record.group_id = None
    record.excluded = False


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
    styles = {
        "exact": ("#8FC5FF", "Exact match"),
        "suggested": ("#FFD166", "Suggested match"),
        "unknown": ("#e9ecef", "Unmatched"),
    }
    sections = []
    for container in sortable_containers(board):
        pills = []
        for item in container["items"]:
            name = item["name"]
            record = board.names[name]
            style = styles.get(record.source)
            if style is None:
                raise ValueError(
                    f"Selected name {name!r} has invalid source {record.source!r}"
                )
            color, meaning = style
            escaped = html.escape(name, quote=True)
            pills.append(
                f'<span class="semantic-pill source-{record.source}" style="background:{color}" '
                f'aria-label="{meaning}: {escaped}">{escaped}</span>'
            )
        sections.append(
            '<section class="semantic-container" '
            f'data-container="{html.escape(str(container["id"]), quote=True)}">'
            f'<strong>{html.escape(str(container["header"]))}</strong>'
            f'{"".join(pills)}</section>'
        )
    return "".join(sections)


def _move_record(board: ReviewBoard, name: str, destination: str) -> None:
    if destination == "Inventory":
        return_to_inventory(board, name)
        return
    record = board.names[name]
    record.selected = True
    record.excluded = destination == "Excluded from this report"
    record.group_id = None
    if destination not in {"Working tray", "Excluded from this report"}:
        record.group_id = destination.removeprefix("group:")


def _add_from_search(board: ReviewBoard, widget_key: str) -> None:
    """Streamlit callback that consumes selections without stale widget values."""
    import streamlit as st

    add_selected_names(board, list(st.session_state.get(widget_key, [])))
    st.session_state[widget_key] = []


def _unlock_admin(expected_password: str | None, password_key: str) -> None:
    """Consume the candidate password and retain only the authorization decision."""
    import streamlit as st

    candidate = st.session_state.pop(password_key, None)
    if password_matches(candidate, expected_password):
        st.session_state["mapping_admin_unlocked"] = True
        st.session_state.pop("mapping_admin_auth_error", None)
    else:
        st.session_state["mapping_admin_unlocked"] = False
        st.session_state["mapping_admin_auth_error"] = "Incorrect admin password."


def render_name_review(
    prepared: PreparedReview,
    repository,
    embedder,
    admin_password: str | None = None,
) -> pd.DataFrame | None:
    """Render, authorize, and atomically persist the searchable review board."""
    import streamlit as st

    board = prepared.board
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

    st.markdown(
        f"""<style>
        .name-review, .name-review * { color: #000 !important; }
        .name-review { background: #fff; border: 3px solid #000; padding: 1rem; }
        .name-review-legend { border: 2px solid #000; padding: .5rem; }
        .semantic-pill { color:#000 !important; border:2px solid #000; border-radius:999px;
                         display:inline-block; margin:.25rem; padding:.25rem .6rem; font-weight:700; }
        {SEMANTIC_PILL_CSS}
        </style><div class="name-review"><h2>Review company names</h2></div>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="name-review-legend" aria-label="Name match legend">'
        "🟦 Exact database match &nbsp; 🟨 Suggested match &nbsp; ⬜ Unmatched; "
        "the exclusion area has a dashed boundary.</div>",
        unsafe_allow_html=True,
    )
    search_key = review_widget_key(request_id, "search")
    st.multiselect(
        "Find names from this report",
        options=search_options(board),
        format_func=lambda name: name_status(board, name),
        placeholder="Search cleaned company names",
        key=search_key,
        on_change=_add_from_search,
        args=(board, search_key),
    )

    ordered_groups = [
        group
        for _, group in sorted(
            enumerate(board.groups.values()),
            key=lambda item: (not item[1].existing, item[0]),
        )
    ]
    title_values = {
        group.id: st.text_input(
            "Canonical title",
            value=group.canonical_title,
            key=review_widget_key(request_id, "group_title", group.id),
        )
        for group in ordered_groups
    }
    apply_group_titles(board, title_values)
    if st.button("Create group", key=review_widget_key(request_id, "create_group")):
        create_group(board)
        st.rerun()

    try:
        projected_containers = sortable_containers(board)
    except ValueError as exc:
        st.error(f"Review state is invalid: {exc}")
        st.button(
            "Submit final review",
            key=review_widget_key(request_id, "invalid_board_submit"),
            disabled=True,
        )
        return None

    try:
        preview = _semantic_pill_preview(board)
    except ValueError as exc:
        st.error(f"Review state is invalid: {exc}")
        st.button(
            "Submit final review",
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
        st.warning("Drag-and-drop is unavailable; use the accessible move controls below.")
    except ValueError:
        st.warning("The board returned an incomplete update. No names were moved; please try again.")

    with st.expander("Accessible name movement controls"):
        chosen = st.selectbox(
            "Name",
            options=[""] + search_options(board),
            key=review_widget_key(request_id, "move_name"),
        )
        if chosen:
            destinations = ["Inventory", "Working tray"] + [
                f"group:{group.id}" for group in board.groups.values()
            ] + ["Excluded from this report"]
            labels = {
                f"group:{group.id}": group.canonical_title.strip() or "Untitled group"
                for group in board.groups.values()
            }
            destination = st.selectbox(
                f"Move {chosen} to",
                destinations,
                format_func=lambda value: labels.get(value, value),
                key=review_widget_key(request_id, "move_destination"),
            )
            if st.button("Move name", key=review_widget_key(request_id, "move")):
                _move_record(board, chosen, destination)
                st.rerun()

    errors = validate_board(board)
    if errors:
        st.error("Review is not ready:\n\n" + "\n\n".join(f"- {error}" for error in errors))

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
                "Unlock permanent actions",
                on_click=_unlock_admin,
                args=(admin_password, password_key),
            )
        auth_error = st.session_state.pop("mapping_admin_auth_error", None)
        if auth_error:
            st.error(auth_error)

    unlocked = bool(st.session_state.get("mapping_admin_unlocked"))
    if unlocked and admin_password:
        if st.button("Prepare mappings backup", key=review_widget_key(request_id, "backup")):
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
        "Submit final review",
        key=review_widget_key(request_id, "submit"),
        disabled=bool(errors) or not unlocked or not bool(admin_password),
        help="Resolve every included name and unlock permanent actions first.",
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
