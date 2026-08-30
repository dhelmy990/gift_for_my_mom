"""Compact Streamlit editor for company alias mappings."""

from __future__ import annotations

import hashlib
from collections.abc import MutableMapping

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
        "alias_admin_password",
        "save_aliases",
        "_alias_save_password_attempt",
    }
    for key in list(state):
        if (
            key in exact_keys
            or key.startswith("alias_final_")
            or key.startswith("accept_alias_")
        ):
            del state[key]


def reconcile_alias_report_scope(
    state: MutableMapping[str, object], mode: bool, upload_fingerprint: str
) -> bool:
    """Keep report state only while the current upload scope is unchanged."""
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
            ("current_alias_aggregate", "current_alias_aggregate_fingerprint"),
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


def stage_save_password_attempt(state: MutableMapping[str, object]) -> None:
    """Capture a password for this save rerun and clear the visible widget."""
    state["_alias_save_password_attempt"] = state.get("alias_admin_password", "")
    state["alias_admin_password"] = ""


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
        widget_token = alias_widget_token(row.cleaned_name, prepared.review_rows)
        final_key = f"alias_final_{widget_token}"
        if final_key not in st.session_state:
            st.session_state[final_key] = row.final_name

        name_column, final_column, status_column = st.columns((2, 3, 1))
        name_column.markdown(row.cleaned_name)
        final_column.text_input(
            f"Final company name for {row.cleaned_name}",
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
                key=f"accept_alias_{widget_token}",
                on_click=_accept_suggestion,
                args=(final_key, row.suggestion.canonical_name),
            )

    edits = {
        row.cleaned_name: st.session_state.get(
            f"alias_final_{alias_widget_token(row.cleaned_name, prepared.review_rows)}",
            row.final_name,
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
        on_click=stage_save_password_attempt,
        args=(st.session_state,),
    )

    if prepared.database_error:
        st.error(prepared.database_error)
    elif repository is None:
        st.info("Company alias storage is unavailable")
    elif not configured_admin_password:
        st.info("Admin password is not configured")

    if not save_requested:
        return None

    attempted_password = st.session_state.pop(
        "_alias_save_password_attempt", candidate
    )
    try:
        password_error = validate_save_password(
            attempted_password, configured_admin_password
        )
        if password_error is not None:
            st.error(password_error)
            return None
        return save_alias_changes(
            prepared,
            edited_final_names(prepared.review_rows, edits),
            repository,
        )
    except (RepositoryUnavailableError, ServiceValidationError) as error:
        st.error(str(error))
        return None
    finally:
        st.session_state.pop("_alias_save_password_attempt", None)
