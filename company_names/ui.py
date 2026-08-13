"""Streamlit review board and testable review-state transformations."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from uuid import uuid4

import pandas as pd

from .models import Group, ReviewBoard
from .review import validate_board
from .service import PreparedReview


WORKING = "working"
EXCLUDED = "excluded"


def _item_id(cleaned_name: str) -> str:
    digest = hashlib.sha256(cleaned_name.encode("utf-8")).hexdigest()
    return f"name-{digest}"


def _item(record) -> dict[str, str]:
    icon = "🔵" if record.source == "exact" else "🟡" if record.source == "suggested" else "⚪"
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
        if destination in by_id:
            by_id[destination]["items"].append(_item(record))
    return result


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
    for container in containers:
        destination = container.get("id")
        if destination not in valid_destinations:
            raise ValueError("Sortable result contains an unknown destination")
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
    if not isinstance(result, list) or len(result) != len(source):
        raise ValueError("Sortable result changed the board containers")
    return [
        {"id": original["id"], "header": returned.get("header"), "items": returned.get("items", [])}
        for original, returned in zip(source, result)
    ]


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


def render_name_review(prepared: PreparedReview, repository, embedder) -> pd.DataFrame | None:
    """Render the searchable board. Persistence is intentionally deferred to Task 8."""
    import streamlit as st

    del repository, embedder
    board = prepared.board
    init_key = f"name_review_initialized_{prepared.pending_request_id or id(prepared)}"
    if not st.session_state.get(init_key):
        for record in board.names.values():
            if record.source != "exact" and record.group_id is None:
                record.selected = False
                record.excluded = False
        st.session_state[init_key] = True

    for warning in prepared.warnings:
        st.warning(warning)

    st.markdown(
        """<style>
        .name-review, .name-review * { color: #000 !important; }
        .name-review { background: #fff; border: 3px solid #000; padding: 1rem; }
        .name-review-legend { border: 2px solid #000; padding: .5rem; }
        </style><div class="name-review"><h2>Review company names</h2></div>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="name-review-legend" aria-label="Name match legend">'
        "🔵 Exact database match &nbsp; 🟡 Suggested match &nbsp; ⚪ Unmatched; "
        "the exclusion area has a dashed boundary.</div>",
        unsafe_allow_html=True,
    )
    inventory = sorted(name for name, record in board.names.items() if not record.selected)
    search_key = f"name_search_{prepared.pending_request_id}"
    st.multiselect(
        "Find names from this report",
        options=inventory,
        placeholder="Search cleaned company names",
        key=search_key,
        on_change=_add_from_search,
        args=(board, search_key),
    )

    ordered_groups = [
        board.groups[container["id"].removeprefix("group:")]
        for container in sortable_containers(board)
        if str(container["id"]).startswith("group:")
    ]
    for group in ordered_groups:
        group.canonical_title = st.text_input(
            "Canonical title",
            value=group.canonical_title,
            key=f"group_title_{group.id}",
        )
    if st.button("Create group", key=f"create_group_{prepared.pending_request_id}"):
        create_group(board)
        st.rerun()

    containers = sortable_containers(board)
    try:
        from streamlit_sortables import sort_items

        result = sort_items(
            _component_containers(containers),
            multi_containers=True,
            key=f"name_board_{prepared.pending_request_id}_{_board_revision(board)}",
            custom_style="""
              .sortable-component { color:#000 !important; border:2px solid #000 !important; }
              .sortable-container:last-child { border:3px dashed #000 !important; }
              .sortable-item { color:#000 !important; background:#fff !important; border:2px solid #000 !important; }
            """,
        )
        apply_sort_result(board, _restore_container_ids(result, containers))
    except ImportError:
        st.warning("Drag-and-drop is unavailable; use the accessible move controls below.")
    except ValueError:
        st.warning("The board returned an incomplete update. No names were moved; please try again.")

    with st.expander("Accessible name movement controls"):
        chosen = st.selectbox("Name", options=[""] + sorted(board.names))
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
            )
            if st.button("Move name", key=f"move_{prepared.pending_request_id}"):
                _move_record(board, chosen, destination)
                st.rerun()

    errors = validate_board(board)
    if errors:
        st.error("Review is not ready: " + " ".join(errors))
    else:
        st.success("Review ready. Final authorization and submission are added in the next step.")
    st.button("Submit final review", disabled=True, help="Authorization is added in Task 8")
    return None
