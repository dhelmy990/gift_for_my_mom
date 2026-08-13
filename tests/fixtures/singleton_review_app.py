"""Controlled Streamlit app used by the runtime UI smoke test."""

import pandas as pd
import streamlit as st

from company_names.models import Group, NameRecord, ReviewBoard
from company_names.service import PreparedReview, admin_password_digest
from company_names.ui import render_name_review


class NoCallRepository:
    def __getattr__(self, name):
        raise AssertionError(f"Smoke render unexpectedly used repository.{name}")


class NoCallEmbedder:
    def __getattr__(self, name):
        raise AssertionError(f"Smoke render unexpectedly used embedder.{name}")


def prepared_review(request_id: str) -> PreparedReview:
    group = Group("stored-group", "Existing", True)
    board = ReviewBoard(
        groups={group.id: group},
        names={
            "Alpha": NameRecord("Alpha", None, "unknown"),
            "Beta": NameRecord("Beta", None, "suggested"),
            "Tray Name": NameRecord(
                "Tray Name", None, "exact", selected=True
            ),
            "Stored Alias": NameRecord(
                "Stored Alias", group.id, "exact", selected=True
            ),
        },
    )
    rows = pd.DataFrame(
        [
            {"cleaned_name": "Alpha", "rns": 1.0, "revenue": 10.0},
            {"cleaned_name": "Beta", "rns": 2.0, "revenue": 20.0},
            {"cleaned_name": "Tray Name", "rns": 1.0, "revenue": 5.0},
            {"cleaned_name": "Stored Alias", "rns": 3.0, "revenue": 30.0},
        ]
    )
    return PreparedReview(
        board=board,
        original_mappings={"Stored Alias": group.id},
        suggestions={},
        rows=rows,
        warnings=[],
        pending_request_id=request_id,
    )


repository = NoCallRepository()
embedder = NoCallEmbedder()
password = "fixture-only-password"

# Locked rendering executes the password form without submitting it.
render_name_review(
    prepared_review("11111111-1111-4111-8111-111111111111"),
    repository,
    embedder,
    password,
)

# A second isolated rendering executes the collapsed backup declaration without
# clicking its repository-backed preparation button.
st.session_state["mapping_admin_password_digest"] = admin_password_digest(password)
st.session_state["mapping_admin_unlocked"] = True
render_name_review(
    prepared_review("22222222-2222-4222-8222-222222222222"),
    repository,
    embedder,
    password,
)
