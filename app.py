import streamlit as st
import os
import tempfile
from plumber import extract_last_table_as_df, two_tablify

st.title("PDF Room Data Extractor")

uploaded_files = st.file_uploader(
    "Upload PDF files",
    type="pdf",
    accept_multiple_files=True
)

k = st.number_input("Keep rightmost k month columns (0 = Total only)", min_value=0, value=2)

if uploaded_files:
    if st.button("Process PDFs"):
        data = {}

        with st.spinner("Processing..."):
            for uploaded_file in uploaded_files:
                # Save to temp file
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(uploaded_file.read())
                    tmp_path = tmp.name

                # Extract hotel name from filename
                hotel_name = uploaded_file.name.split()[0]

                # Process
                df = extract_last_table_as_df(tmp_path, k=k)

                # Cleanup temp file
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
