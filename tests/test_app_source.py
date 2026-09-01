from pathlib import Path


def test_app_uses_alias_pipeline_without_embeddings_or_groups() -> None:
    source = Path("app.py").read_text()

    assert "SupabaseAliasRepository" in source
    assert "prepare_aliases" in source
    assert "aggregate_resolved_rows" not in source
    assert "render_alias_editor" in source
    assert "reset_alias_editor_state" in source
    assert "FastEmbeddingProvider" not in source
    assert "render_name_review" not in source
    assert "ReviewBoard" not in source


def test_app_requires_login_before_rendering_upload_controls() -> None:
    source = Path("app.py").read_text()

    assert "render_login_gate" in source
    assert source.index("render_login_gate") < source.index("st.file_uploader")
    assert "Log out" in source


def test_runtime_has_no_obsolete_module_imports() -> None:
    runtime = "\n".join(
        path.read_text()
        for path in [Path("app.py"), *Path("company_names").glob("*.py")]
    )
    for obsolete in (
        "fastembed",
        "streamlit_sortables",
        "name_groups",
        "name_mappings",
        "submission_ledger",
    ):
        assert obsolete not in runtime


def test_processing_state_is_scoped_and_editor_resets_only_on_process() -> None:
    source = Path("app.py").read_text()

    assert "prepared_aliases_fingerprint" in source
    assert "saved_alias_aggregate_fingerprint" in source
    assert 'if uploaded_files and st.button("Process PDFs")' in source
    assert "reset_alias_editor_state(st.session_state)" in source


def test_app_only_stores_resolved_totals_after_alias_save() -> None:
    source = Path("app.py").read_text()
    assignment = 'st.session_state["saved_alias_aggregate"] = result'

    assert "_initial_alias_aggregate" not in source
    assert source.count('st.session_state["saved_alias_aggregate"] =') == 1
    assert assignment in source
    assert source.index("result = render_alias_editor(") < source.index(assignment)
    assert 'st.session_state.get("current_alias_aggregate")' not in source
    assert 'st.session_state["current_alias_aggregate"] =' not in source
    assert "Last saved company totals" in source
