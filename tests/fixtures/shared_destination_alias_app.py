"""Controlled app proving aliases stay distinct before final aggregation."""

from pathlib import Path
import sys

import pandas as pd
import streamlit as st


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from company_names.repository import AliasMapping
from company_names.service import prepare_aliases
from company_names.ui import render_alias_editor


class SharedDestinationRepository:
    def __init__(self) -> None:
        self.aliases = [
            AliasMapping("A", "a", "C"),
            AliasMapping("B", "b", "C"),
            *[
                AliasMapping(
                    f"Alias {index:03d}",
                    f"alias {index:03d}",
                    f"Canonical {index:03d}",
                )
                for index in range(2, 145)
            ],
        ]
        self.saved: list[AliasMapping] = []

    def list_aliases(self) -> list[AliasMapping]:
        return list(self.aliases)

    def upsert_aliases(self, mappings: list[AliasMapping]) -> None:
        self.saved.extend(mappings)


if "fixture_repository" not in st.session_state:
    st.session_state["fixture_repository"] = SharedDestinationRepository()

repository = st.session_state["fixture_repository"]
rows = pd.DataFrame([
    {"agent_name": "A", "rns": 2, "revenue": 20},
    {"agent_name": "B", "rns": 3, "revenue": 30},
    *[
        {"agent_name": f"Alias {index:03d}", "rns": 1, "revenue": index}
        for index in range(2, 145)
    ],
])
prepared = prepare_aliases(rows, repository)
saved_aggregate = st.session_state.get("saved_alias_aggregate")
if isinstance(saved_aggregate, pd.DataFrame):
    st.dataframe(saved_aggregate, use_container_width=True)
result = render_alias_editor(prepared, repository)
if result is not None:
    st.session_state["fixture_result"] = result
    st.session_state["saved_alias_aggregate"] = result
    st.rerun()
