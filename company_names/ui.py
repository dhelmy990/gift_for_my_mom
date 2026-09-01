"""Compact Streamlit editor for company alias mappings."""

from __future__ import annotations

import hashlib
from collections.abc import MutableMapping
from dataclasses import dataclass
import math

import pandas as pd
import streamlit as st

from .cleaning import normalize_lookup_key
from .repository import AliasRepository, RepositoryUnavailableError
from .service import (
    AliasReviewRow,
    PreparedAliases,
    ServiceValidationError,
    save_alias_changes,
)


ALIAS_FILTER_OPTIONS = (
    "Needs review",
    "New names",
    "Suggestions",
    "Already saved",
    "All names",
)
PAGE_SIZE_OPTIONS = (10, 20, 50, 100)
_FILTER_STATUSES = {
    "Needs review": {"new", "suggested"},
    "New names": {"new"},
    "Suggestions": {"suggested"},
    "Already saved": {"saved"},
    "All names": {"new", "suggested", "saved"},
}


@dataclass(frozen=True)
class PaginatedRows:
    rows: list[AliasReviewRow]
    page: int
    total_pages: int
    start: int
    end: int
    total_rows: int


def alias_widget_token(
    cleaned_name: str, rows: list[AliasReviewRow]
) -> str:
    """Return the plain alias key unless current rows make it ambiguous."""
    alias_key = normalize_lookup_key(cleaned_name)
    colliding_names = {
        row.cleaned_name
        for row in rows
        if normalize_lookup_key(row.cleaned_name) == alias_key
    }
    if len(colliding_names) <= 1:
        return alias_key
    digest = hashlib.sha256(cleaned_name.encode("utf-8")).hexdigest()[:8]
    return f"{alias_key}_{digest}"


def reset_alias_editor_state(state: MutableMapping[str, object]) -> None:
    """Remove alias-editor widget values at a new processing boundary."""
    exact_keys = {
        "alias_search",
        "alias_status_filter",
        "alias_page_size",
        "alias_page",
        "alias_edits",
        "save_aliases",
    }
    for key in list(state):
        if (
            key in exact_keys
            or key.startswith("alias_final_")
            or key.startswith("accept_alias_")
            or key.startswith("alias_previous_")
            or key.startswith("alias_next_")
        ):
            del state[key]


def reconcile_alias_report_scope(
    state: MutableMapping[str, object], mode: bool, upload_fingerprint: str
) -> bool:
    """Keep report state only while the current upload scope is unchanged."""
    for key in (
        "current_alias_aggregate",
        "current_alias_aggregate_fingerprint",
    ):
        state.pop(key, None)
    stored_fingerprint = state.get("prepared_aliases_fingerprint")
    stored_mode = state.get("prepared_aliases_mode")
    has_prepared_scope = (
        "prepared_aliases" in state
        or stored_fingerprint is not None
        or stored_mode is not None
    )
    scope_matches = (
        has_prepared_scope
        and stored_fingerprint == upload_fingerprint
        and stored_mode is mode
    )
    if has_prepared_scope and not scope_matches:
        for key in (
            "prepared_aliases",
            "prepared_aliases_fingerprint",
            "prepared_aliases_mode",
            "saved_alias_aggregate",
            "saved_alias_aggregate_fingerprint",
            "current_alias_aggregate",
            "current_alias_aggregate_fingerprint",
            "final_alias_results",
            "final_alias_results_fingerprint",
        ):
            state.pop(key, None)
        reset_alias_editor_state(state)
        return False

    if scope_matches:
        scoped_pairs = (
            ("saved_alias_aggregate", "saved_alias_aggregate_fingerprint"),
            ("final_alias_results", "final_alias_results_fingerprint"),
        )
        for value_key, fingerprint_key in scoped_pairs:
            if (
                value_key in state
                and state.get(fingerprint_key) != upload_fingerprint
            ):
                state.pop(value_key, None)
                state.pop(fingerprint_key, None)
    return scope_matches


def visible_review_rows(
    rows: list[AliasReviewRow], query: str
) -> list[AliasReviewRow]:
    """Filter current report rows by their cleaned or final company name."""
    needle = query.strip().casefold()
    if not needle:
        return list(rows)
    return [
        row
        for row in rows
        if needle in row.cleaned_name.casefold() or needle in row.final_name.casefold()
    ]


def filter_review_rows(
    rows: list[AliasReviewRow],
    status_filter: str,
    query: str,
    final_names: dict[str, str] | None = None,
) -> list[AliasReviewRow]:
    """Apply the selected status filter, then search the current values."""
    try:
        statuses = _FILTER_STATUSES[status_filter]
    except KeyError:
        raise ValueError(f"Unknown alias filter: {status_filter}") from None
    filtered = [row for row in rows if row.status in statuses]
    needle = query.strip().casefold()
    if not needle:
        return filtered
    current = final_names or {}
    return [
        row
        for row in filtered
        if needle in row.cleaned_name.casefold()
        or needle in current.get(row.cleaned_name, row.final_name).casefold()
    ]


def paginate_review_rows(
    rows: list[AliasReviewRow], page: int, page_size: int
) -> PaginatedRows:
    """Return one clamped page and its one-based display range."""
    if page_size not in PAGE_SIZE_OPTIONS:
        raise ValueError(f"Unsupported alias page size: {page_size}")
    total_rows = len(rows)
    if total_rows == 0:
        return PaginatedRows([], 1, 0, 0, 0, 0)
    total_pages = math.ceil(total_rows / page_size)
    clamped_page = min(max(int(page), 1), total_pages)
    offset = (clamped_page - 1) * page_size
    page_rows = rows[offset : offset + page_size]
    return PaginatedRows(
        page_rows,
        clamped_page,
        total_pages,
        offset + 1,
        offset + len(page_rows),
        total_rows,
    )


def edited_final_names(
    rows: list[AliasReviewRow], edits: dict[str, str]
) -> dict[str, str]:
    """Overlay user edits without implicitly accepting fuzzy suggestions."""
    return {
        row.cleaned_name: edits.get(row.cleaned_name, row.final_name)
        for row in rows
    }


def _store_alias_edit(cleaned_name: str, widget_key: str) -> None:
    edits = dict(st.session_state.get("alias_edits", {}))
    edits[cleaned_name] = st.session_state.get(widget_key, "")
    st.session_state["alias_edits"] = edits


def _accept_suggestion(
    cleaned_name: str, widget_key: str, canonical_name: str
) -> None:
    st.session_state[widget_key] = canonical_name
    edits = dict(st.session_state.get("alias_edits", {}))
    edits[cleaned_name] = canonical_name
    st.session_state["alias_edits"] = edits


def _reset_alias_page() -> None:
    st.session_state["alias_page"] = 1


def _move_alias_page(delta: int, total_pages: int) -> None:
    current = int(st.session_state.get("alias_page", 1))
    st.session_state["alias_page"] = min(max(current + delta, 1), total_pages)


def _render_page_controls(page: PaginatedRows, location: str) -> None:
    previous, label, following = st.columns((1, 2, 1))
    previous.button(
        "Previous",
        key=f"alias_previous_{location}",
        disabled=page.page <= 1,
        on_click=_move_alias_page,
        args=(-1, page.total_pages),
    )
    label.markdown(f"Page {page.page} of {page.total_pages}")
    following.button(
        "Next",
        key=f"alias_next_{location}",
        disabled=page.page >= page.total_pages,
        on_click=_move_alias_page,
        args=(1, page.total_pages),
    )


def render_alias_editor(
    prepared: PreparedAliases,
    repository: AliasRepository | None,
) -> pd.DataFrame | None:
    """Render mappings for the current report and return totals after a save."""
    st.subheader("Company name mappings")
    stored_edits = dict(st.session_state.get("alias_edits", {}))
    for row in prepared.review_rows:
        stored_edits.setdefault(row.cleaned_name, row.final_name)
    st.session_state["alias_edits"] = stored_edits

    counts = {
        option: sum(
            row.status in _FILTER_STATUSES[option] for row in prepared.review_rows
        )
        for option in ALIAS_FILTER_OPTIONS
    }
    filter_column, search_column = st.columns((2, 3))
    status_filter = filter_column.selectbox(
        "Show",
        ALIAS_FILTER_OPTIONS,
        key="alias_status_filter",
        format_func=lambda option: f"{option} ({counts[option]})",
        on_change=_reset_alias_page,
    )
    query = search_column.text_input(
        "Search current rows", key="alias_search", on_change=_reset_alias_page
    )
    page_size = st.selectbox(
        "Rows per page",
        PAGE_SIZE_OPTIONS,
        index=1,
        key="alias_page_size",
        on_change=_reset_alias_page,
    )
    if "alias_page" not in st.session_state:
        st.session_state["alias_page"] = 1

    current_final_names = dict(st.session_state["alias_edits"])
    filtered_rows = filter_review_rows(
        prepared.review_rows, status_filter, query, current_final_names
    )
    page = paginate_review_rows(
        filtered_rows, int(st.session_state["alias_page"]), int(page_size)
    )
    st.session_state["alias_page"] = page.page

    if page.total_rows:
        st.caption(f"Showing {page.start}–{page.end} of {page.total_rows}")
        if page.total_pages > 1:
            _render_page_controls(page, "top")
    else:
        st.info("No company names match this view. Choose another filter or search.")

    for row in page.rows:
        widget_token = alias_widget_token(row.cleaned_name, prepared.review_rows)
        final_key = f"alias_final_{widget_token}"
        st.session_state[final_key] = stored_edits[row.cleaned_name]

        name_column, final_column, status_column = st.columns((2, 3, 1))
        name_column.markdown(row.cleaned_name)
        final_column.text_input(
            f"Final company name for {row.cleaned_name}",
            key=final_key,
            label_visibility="collapsed",
            on_change=_store_alias_edit,
            args=(row.cleaned_name, final_key),
        )
        status_column.markdown(row.status.title())

        if row.suggestion is not None:
            st.caption(
                f"Suggested from {row.suggestion.saved_alias} "
                f"({row.suggestion.score:.0f}%): {row.suggestion.canonical_name}"
            )
            st.button(
                "Use this suggestion",
                key=f"accept_alias_{widget_token}",
                on_click=_accept_suggestion,
                args=(row.cleaned_name, final_key, row.suggestion.canonical_name),
            )

    if page.total_pages > 1:
        _render_page_controls(page, "bottom")

    edits = dict(st.session_state["alias_edits"])
    save_disabled = (
        not prepared.database_available
        or repository is None
    )
    save_requested = st.button(
        "Save all changes and update totals",
        key="save_aliases",
        disabled=save_disabled,
    )

    if prepared.database_error:
        st.error(prepared.database_error)
    elif repository is None:
        st.info("Company alias storage is unavailable")
    if not save_requested:
        return None

    try:
        return save_alias_changes(
            prepared,
            edited_final_names(prepared.review_rows, edits),
            repository,
        )
    except (RepositoryUnavailableError, ServiceValidationError) as error:
        st.error(str(error))
        return None
