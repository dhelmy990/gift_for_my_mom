import streamlit as st
import os
import tempfile
import pandas as pd
from plumber import extract_last_table_as_df, two_tablify, extract_all_tables

# Default agents to exclude in collation mode
DEFAULT_EXCLUDED_AGENTS = ["Grand Total", "TRAVELOKAOne Fullerton", "BBUTTON", "KLOOK", "WALK IN", "RTX Rakuten Tower", "HIS_International", "GENARESSM", "walkin",
                           "BOOKINGCOM", "AGODA30", "EXPEDIA59", "TRIPCtrip.com Ltd", "GOIBIBOGood Earth City Centre,", "Booking.com", "KLOOK12-13", "BOOKING.COM (VCC )",
                           "RTXRakuten Tower", "TIKET10230", "Booking.com ( Pax Account", "Expedia.com"]

st.title("PDF Room Data Extractor")

# Shared PDF upload control
uploaded_files = st.file_uploader(
    "Upload PDF files",
    type="pdf",
    accept_multiple_files=True
)

# Toggle switch for mode selection
mode = st.toggle("Collation across agents", value=False, help="Off = PDF Extractor, On = Collation across agents")

# Mode-specific controls
if mode:
    # Collation across agents mode - list input for excluded agents
    excluded_agents = st.multiselect(
        "Agents to exclude",
        options=DEFAULT_EXCLUDED_AGENTS,
        default=DEFAULT_EXCLUDED_AGENTS,
        help="Select agents to exclude from collation. You can also type to add new agents."
    )

    # Allow adding custom agents
    new_agent = st.text_input("Add new agent to exclude")
    if new_agent and st.button("Add Agent"):
        if new_agent not in excluded_agents:
            excluded_agents.append(new_agent)
            st.rerun()

    st.write(f"Currently excluding: {', '.join(excluded_agents) if excluded_agents else 'None'}")
else:
    # PDF Extractor mode - k columns input
    k = st.number_input("Keep rightmost k month columns (0 = Total only)", min_value=0, value=2)

# Process button and logic
if uploaded_files:
    if st.button("Process PDFs"):
        with st.spinner("Processing..."):
            if mode:
                # Collation mode - extract all agent tables
                all_dfs = []
                for uploaded_file in uploaded_files:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                        tmp.write(uploaded_file.read())
                        tmp_path = tmp.name

                    exclude_lowercase =  [agent.lower() for agent in excluded_agents]

                    df = extract_all_tables(exclude_lowercase, tmp_path)
                    os.unlink(tmp_path)

                    if df is not None and not df.empty:
                        all_dfs.append(df)
                    else:
                        st.warning(f"Could not extract data from {uploaded_file.name}")

                if all_dfs:
                    # Combine and sum across all PDFs
                    combined_df = pd.concat(all_dfs)
                    result_df = combined_df.groupby(combined_df.index).sum()

                    # Sort by revenue descending
                    result_df = result_df.sort_values('Sum of R REVENUE', ascending=False)

                    # Reset index and rename columns
                    result_df = result_df.reset_index()
                    result_df.columns = ['TRAVEL AGENT', 'Sum of RNS', 'Sum of R REVENUE']

                    # Add NO column (1-indexed ranking by revenue)
                    result_df.insert(0, 'NO', range(1, len(result_df) + 1))

                    st.subheader("Agent Collation Results")
                    st.dataframe(result_df, use_container_width=True, hide_index=True)
            else:
                # PDF Extractor mode
                data = {}
                for uploaded_file in uploaded_files:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                        tmp.write(uploaded_file.read())
                        tmp_path = tmp.name

                    hotel_name = uploaded_file.name.split()[0]
                    df = extract_last_table_as_df(tmp_path, k=k)
                    os.unlink(tmp_path)

                    if df is not None:
                        data[hotel_name] = df
                    else:
                        st.warning(f"Could not extract data from {uploaded_file.name}")

                if data:
                    trn_df, rr_df = two_tablify(data)

                    st.subheader("Total Room Nights")
                    st.dataframe(trn_df, use_container_width=True)

                    st.subheader("Room Revenue")
                    st.dataframe(rr_df, use_container_width=True)
