"""Controlled Streamlit app for runtime alias-editor tests."""

from pathlib import Path
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from company_names.repository import AliasMapping, RepositoryUnavailableError
from company_names.service import prepare_aliases
from company_names.ui import render_alias_editor


class FixtureRepository:
    def __init__(self) -> None:
        self.aliases = [AliasMapping(
            "HKTRM", "hktrm", "Hong Kong TUYI Business Travel Limited"
        )]
        self.saved: list[AliasMapping] = []

    def list_aliases(self) -> list[AliasMapping]:
        return list(self.aliases)

    def upsert_aliases(self, mappings: list[AliasMapping]) -> None:
        if st.session_state.get("fixture_fail_next_save"):
            raise RepositoryUnavailableError("network unavailable")
        self.saved.extend(mappings)


if "fixture_repository" not in st.session_state:
    st.session_state["fixture_repository"] = FixtureRepository()

st.checkbox("Fail next save", key="fixture_fail_next_save")
repository = st.session_state["fixture_repository"]
prepared = prepare_aliases(pd.DataFrame([
    {"agent_name": "HKTRM", "rns": 2, "revenue": 100},
    {"agent_name": "HKTRMs", "rns": 3, "revenue": 50},
]), repository)
result = render_alias_editor(prepared, repository)
if result is not None:
    st.session_state["fixture_result"] = result
    st.dataframe(result)
