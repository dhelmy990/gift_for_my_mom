"""Controlled Streamlit app for pagination runtime tests."""

from pathlib import Path
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from company_names.repository import AliasMapping
from company_names.service import prepare_aliases
from company_names.ui import render_alias_editor


class EmptyRepository:
    def list_aliases(self) -> list[AliasMapping]:
        return []

    def upsert_aliases(self, mappings: list[AliasMapping]) -> None:
        pass


repository = EmptyRepository()
prepared = prepare_aliases(
    pd.DataFrame([
        {"agent_name": f"Company {index:02d}", "rns": 1, "revenue": index}
        for index in range(25)
    ]),
    repository,
)
render_alias_editor(prepared, repository, "correct")
