"""Streamlit PDF extraction and company-name collation application."""

import os
import tempfile

import pandas as pd
import streamlit as st

from company_names.matching import FastEmbeddingProvider
from company_names.repository import SupabaseMappingRepository
from company_names.review_session import (
    compute_upload_fingerprint as identify_uploads,
    merge_custom_excluded_agent,
    reconcile_prepared_review,
)
from company_names.service import (
    ServiceValidationError,
    prepare_review,
)
from company_names.ui import render_name_review
from plumber import extract_all_tables, extract_last_table_as_df, two_tablify


DEFAULT_EXCLUDED_AGENTS = [
    "Grand Total", "TRAVELOKAOne Fullerton", "BBUTTON", "KLOOK", "WALK IN",
    "RTX Rakuten Tower", "HIS_International", "GENARESSM", "walkin",
    "BOOKINGCOM", "AGODA30", "EXPEDIA59", "TRIPCtrip.com Ltd",
    "GOIBIBOGood Earth City Centre,", "Booking.com", "KLOOK12-13",
    "BOOKING.COM (VCC )", "RTXRakuten Tower", "TIKET10230",
    "Booking.com ( Pax Account", "Expedia.com",
]


@st.cache_resource
def get_mapping_repository(url: str, service_key: str) -> SupabaseMappingRepository:
    """Create one server-side repository client per credential pair."""
    return SupabaseMappingRepository.from_credentials(url, service_key)


@st.cache_resource
def get_embedding_provider() -> FastEmbeddingProvider:
    """Reuse the lazily loaded embedding model across Streamlit reruns."""
    return FastEmbeddingProvider()


def _secret(name: str) -> str | None:
    try:
        value = st.secrets.get(name)
    except Exception:
        return None
    return value if isinstance(value, str) and value.strip() else None


def _add_custom_excluded_agent() -> None:
    options, selected = merge_custom_excluded_agent(
        st.session_state["excluded_agent_options"],
        st.session_state.get("excluded_agents", []),
        st.session_state.get("new_excluded_agent", ""),
    )
    st.session_state["excluded_agent_options"] = options
    st.session_state["excluded_agents"] = selected
    st.session_state["new_excluded_agent"] = ""


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


def _prepare_collation_review(frames: list[pd.DataFrame], upload_fingerprint: str) -> None:
    url = _secret("SUPABASE_URL")
    service_key = _secret("SUPABASE_SERVICE_KEY")
    if not url or not service_key:
        st.session_state.pop("prepared_name_review", None)
        st.error("Database not configured. Add SUPABASE_URL and SUPABASE_SERVICE_KEY to Streamlit secrets.")
        st.markdown("[Open the Supabase setup guide](./docs/SUPABASE_SETUP.md)")
        return
    try:
        repository = get_mapping_repository(url, service_key)
        embedder = get_embedding_provider()
        # Use raw extracted rows so normalization happens at the review boundary.
        prepared = prepare_review(pd.concat(frames, ignore_index=True), repository, embedder)
    except ServiceValidationError:
        raise
    except Exception:
        st.session_state.pop("prepared_name_review", None)
        st.error("Database connection unavailable. Check the Supabase configuration and tables, then retry.")
        return
    st.session_state["prepared_name_review"] = prepared
    st.session_state["prepared_name_review_fingerprint"] = upload_fingerprint
    st.success("Database connected. Name review is ready.")


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
    upload_fingerprint = identify_uploads(mode, uploaded_files or [])
    prepared = reconcile_prepared_review(
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
        # Processing is the deliberate reset boundary; ordinary reruns retain the board.
        reconcile_prepared_review(st.session_state, False, upload_fingerprint)
        with st.spinner("Processing..."):
            try:
                if mode:
                    frames = _extract_collation(uploaded_files, excluded_agents)
                    if frames:
                        _prepare_collation_review(frames, upload_fingerprint)
                else:
                    _process_extractor(uploaded_files, int(k))
            except ServiceValidationError as exc:
                st.error(str(exc))

    prepared = reconcile_prepared_review(st.session_state, mode, upload_fingerprint)
    if prepared is not None:
        url = _secret("SUPABASE_URL")
        service_key = _secret("SUPABASE_SERVICE_KEY")
        if url and service_key:
            st.success("Database connected.")
            render_name_review(
                prepared,
                get_mapping_repository(url, service_key),
                get_embedding_provider(),
            )


if __name__ == "__main__":
    main()
