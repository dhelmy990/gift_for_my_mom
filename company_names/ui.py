"""Compact Streamlit editor for company alias mappings."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .cleaning import normalize_lookup_key
from .repository import AliasRepository, RepositoryUnavailableError
from .service import (
    AliasReviewRow,
    PreparedAliases,
    ServiceValidationError,
    password_matches,
    save_alias_changes,
)


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


def edited_final_names(
    rows: list[AliasReviewRow], edits: dict[str, str]
) -> dict[str, str]:
    """Overlay user edits without implicitly accepting fuzzy suggestions."""
    return {
        row.cleaned_name: edits.get(row.cleaned_name, row.final_name)
        for row in rows
    }


def validate_save_password(candidate: object, configured: object) -> str | None:
    """Return a user-facing password error, or ``None`` when authorized."""
    if not password_matches(candidate, configured):
        return "Incorrect admin password"
    return None


def _accept_suggestion(widget_key: str, canonical_name: str) -> None:
    st.session_state[widget_key] = canonical_name


def render_alias_editor(
    prepared: PreparedAliases,
    repository: AliasRepository | None,
    configured_admin_password: str | None,
) -> pd.DataFrame | None:
    """Render mappings for the current report and return totals after a save."""
    st.subheader("Company name mappings")
    query = st.text_input("Search current rows", key="alias_search")

    for row in visible_review_rows(prepared.review_rows, query):
        alias_key = normalize_lookup_key(row.cleaned_name)
        final_key = f"alias_final_{alias_key}"
        if final_key not in st.session_state:
            st.session_state[final_key] = row.final_name

        name_column, final_column, status_column = st.columns((2, 3, 1))
        name_column.markdown(row.cleaned_name)
        final_column.text_input(
            "Final company name",
            key=final_key,
            label_visibility="collapsed",
        )
        status_column.markdown(row.status.title())

        if row.suggestion is not None:
            st.caption(
                f"Suggested from {row.suggestion.saved_alias} "
                f"({row.suggestion.score:.0f}%): {row.suggestion.canonical_name}"
            )
            st.button(
                "Use this suggestion",
                key=f"accept_alias_{alias_key}",
                on_click=_accept_suggestion,
                args=(final_key, row.suggestion.canonical_name),
            )

    edits = {
        row.cleaned_name: st.session_state.get(
            f"alias_final_{normalize_lookup_key(row.cleaned_name)}", row.final_name
        )
        for row in prepared.review_rows
    }
    save_disabled = (
        not prepared.database_available
        or repository is None
        or not configured_admin_password
    )
    password_column, save_column = st.columns((2, 3))
    candidate = password_column.text_input(
        "Admin password",
        type="password",
        key="alias_admin_password",
        disabled=save_disabled,
    )
    save_requested = save_column.button(
        "Save all changes and update totals",
        key="save_aliases",
        disabled=save_disabled,
    )

    if prepared.database_error:
        st.error(prepared.database_error)
    elif repository is None:
        st.info("Company alias storage is unavailable")
    elif not configured_admin_password:
        st.info("Admin password is not configured")

    if not save_requested:
        return None

    password_error = validate_save_password(candidate, configured_admin_password)
    if password_error is not None:
        st.error(password_error)
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
