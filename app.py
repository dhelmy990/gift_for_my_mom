"""Streamlit PDF extraction and company-name collation application."""

import os
import tempfile
import logging
import hashlib

import pandas as pd
import streamlit as st

from company_names.repository import RepositoryUnavailableError, SupabaseAliasRepository
from company_names.service import (
    PreparedAliases,
    ServiceValidationError,
    aggregate_resolved_rows,
    prepare_aliases,
)
from company_names.ui import (
    reconcile_alias_report_scope,
    render_alias_editor,
    reset_alias_editor_state,
)
from plumber import extract_all_tables, extract_last_table_as_df, two_tablify


DEFAULT_EXCLUDED_AGENTS = [
    "Grand Total", "TRAVELOKAOne Fullerton", "BBUTTON", "KLOOK", "WALK IN",
    "RTX Rakuten Tower", "HIS_International", "GENARESSM", "walkin",
    "BOOKINGCOM", "AGODA30", "EXPEDIA59", "TRIPCtrip.com Ltd",
    "GOIBIBOGood Earth City Centre,", "Booking.com", "KLOOK12-13",
    "BOOKING.COM (VCC )", "RTXRakuten Tower", "TIKET10230",
    "Booking.com ( Pax Account", "Expedia.com",
]

logger = logging.getLogger(__name__)


@st.cache_resource
def get_alias_repository(url: str, service_key: str) -> SupabaseAliasRepository:
    """Create one server-side repository client per credential pair."""
    return SupabaseAliasRepository.from_credentials(url, service_key)


def _secret(name: str) -> str | None:
    try:
        value = st.secrets.get(name)
    except Exception:
        return None
    return value if isinstance(value, str) and value.strip() else None


def _add_custom_excluded_agent() -> None:
    options = list(st.session_state["excluded_agent_options"])
    selected = list(st.session_state.get("excluded_agents", []))
    new_agent = st.session_state.get("new_excluded_agent", "").strip()
    if new_agent and new_agent not in options:
        options.append(new_agent)
    if new_agent and new_agent not in selected:
        selected.append(new_agent)
    st.session_state["excluded_agent_options"] = options
    st.session_state["excluded_agents"] = selected
    st.session_state["new_excluded_agent"] = ""


def _upload_fingerprint(mode: bool, uploaded_files) -> str:
    """Identify the current upload selection without moving file cursors."""
    digest = hashlib.sha256(b"collation" if mode else b"extractor")
    for uploaded_file in uploaded_files:
        digest.update(uploaded_file.name.encode("utf-8", errors="replace"))
        digest.update(uploaded_file.getvalue())
    return digest.hexdigest()


def _extract_collation(uploaded_files, excluded_agents: list[str]) -> list[pd.DataFrame]:
    frames = []
    exclude_lowercase = [agent.lower() for agent in excluded_agents]
    for uploaded_file in uploaded_files:
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                uploaded_file.seek(0)
                tmp.write(uploaded_file.read())
                tmp_path = tmp.name
            frame = extract_all_tables(exclude_lowercase, tmp_path)
        finally:
            if tmp_path is not None:
                os.unlink(tmp_path)
        issue_count = len(frame.attrs.get("parse_issues", [])) if frame is not None else 0
        if issue_count:
            st.warning(f"{uploaded_file.name}: skipped {issue_count} malformed data block(s).")
        if frame is not None and not frame.empty:
            frame = frame.copy()
            frame["_source_file"] = uploaded_file.name
            frames.append(frame)
        else:
            st.warning(f"Could not extract data from {uploaded_file.name}")
    return frames


def _process_extractor(uploaded_files, k: int) -> None:
    data = {}
    for uploaded_file in uploaded_files:
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                uploaded_file.seek(0)
                tmp.write(uploaded_file.read())
                tmp_path = tmp.name
            frame = extract_last_table_as_df(tmp_path, k=k)
        finally:
            if tmp_path is not None:
                os.unlink(tmp_path)
        if frame is not None:
            data[uploaded_file.name.split()[0]] = frame
        else:
            st.warning(f"Could not extract data from {uploaded_file.name}")
    if data:
        trn_df, rr_df = two_tablify(data)
        st.subheader("Total Room Nights")
        st.dataframe(trn_df, use_container_width=True)
        st.subheader("Room Revenue")
        st.dataframe(rr_df, use_container_width=True)


def _prepare_collation_aliases(frames: list[pd.DataFrame]) -> PreparedAliases:
    rows = pd.concat(frames, ignore_index=True)
    url = _secret("SUPABASE_URL")
    service_key = _secret("SUPABASE_SERVICE_KEY")
    if not url or not service_key:
        return prepare_aliases(rows, None)
    try:
        repository = get_alias_repository(url, service_key)
    except RepositoryUnavailableError as error:
        logger.warning("Supabase alias repository unavailable: %s", error)
        prepared = prepare_aliases(rows, None)
        prepared.database_error = str(error)
        return prepared
    return prepare_aliases(rows, repository)


def _initial_alias_aggregate(prepared: PreparedAliases) -> pd.DataFrame:
    return aggregate_resolved_rows(
        prepared.rows,
        {row.cleaned_name: row.final_name for row in prepared.review_rows},
    )


def main() -> None:
    st.title("PDF Room Data Extractor")
    uploaded_files = st.file_uploader(
        "Upload PDF files", type="pdf", accept_multiple_files=True
    )
    mode = st.toggle(
        "Collation across agents",
        value=False,
        help="Off = PDF Extractor, On = Collation across agents",
    )
    upload_fingerprint = _upload_fingerprint(mode, uploaded_files or [])
    prepared_matches = reconcile_alias_report_scope(
        st.session_state, mode, upload_fingerprint
    )

    if mode:
        options_key = "excluded_agent_options"
        selected_key = "excluded_agents"
        if options_key not in st.session_state:
            st.session_state[options_key] = list(DEFAULT_EXCLUDED_AGENTS)
        excluded_agents = st.multiselect(
            "Agents to exclude",
            options=st.session_state[options_key],
            default=DEFAULT_EXCLUDED_AGENTS,
            key=selected_key,
        )
        new_agent = st.text_input("Add new agent to exclude", key="new_excluded_agent")
        st.button(
            "Add Agent",
            key="add_excluded_agent",
            disabled=not bool(new_agent.strip()),
            on_click=_add_custom_excluded_agent,
        )
        st.write(
            f"Currently excluding: {', '.join(excluded_agents) if excluded_agents else 'None'}"
        )
    else:
        k = st.number_input(
            "Keep rightmost k month columns (0 = Total only)", min_value=0, value=2
        )

    if uploaded_files and st.button("Process PDFs"):
        # A deliberate new extraction starts a fresh editor; ordinary reruns retain edits.
        reset_alias_editor_state(st.session_state)
        for key in (
            "prepared_aliases",
            "prepared_aliases_fingerprint",
            "prepared_aliases_mode",
            "current_alias_aggregate",
            "current_alias_aggregate_fingerprint",
            "final_alias_results",
            "final_alias_results_fingerprint",
        ):
            st.session_state.pop(key, None)
        with st.spinner("Processing..."):
            try:
                if mode:
                    frames = _extract_collation(uploaded_files, excluded_agents)
                    if frames:
                        prepared = _prepare_collation_aliases(frames)
                        st.session_state["prepared_aliases"] = prepared
                        st.session_state["prepared_aliases_fingerprint"] = upload_fingerprint
                        st.session_state["prepared_aliases_mode"] = mode
                        st.session_state["current_alias_aggregate"] = _initial_alias_aggregate(prepared)
                        st.session_state["current_alias_aggregate_fingerprint"] = upload_fingerprint
                        prepared_matches = True
                else:
                    _process_extractor(uploaded_files, int(k))
            except ServiceValidationError as exc:
                st.error(str(exc))

    prepared = st.session_state.get("prepared_aliases")
    if prepared_matches and mode and isinstance(prepared, PreparedAliases):
        aggregate = st.session_state.get("current_alias_aggregate")
        if (
            isinstance(aggregate, pd.DataFrame)
            and st.session_state.get("current_alias_aggregate_fingerprint")
            == upload_fingerprint
        ):
            st.subheader("Company totals")
            st.dataframe(aggregate, use_container_width=True)

        url = _secret("SUPABASE_URL")
        service_key = _secret("SUPABASE_SERVICE_KEY")
        repository = None
        if url and service_key and prepared.database_available:
            try:
                repository = get_alias_repository(url, service_key)
            except RepositoryUnavailableError as error:
                logger.warning("Supabase alias editor unavailable: %s", error)
                st.error(str(error))
        result = render_alias_editor(
            prepared,
            repository,
            _secret("ADMIN_PASSWORD"),
        )
        if result is not None:
            st.session_state["current_alias_aggregate"] = result
            st.session_state["current_alias_aggregate_fingerprint"] = upload_fingerprint
            st.rerun()


if __name__ == "__main__":
    main()
