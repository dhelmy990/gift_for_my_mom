from pathlib import Path


def test_app_uses_alias_pipeline_without_embeddings_or_groups() -> None:
    source = Path("app.py").read_text()

    assert "SupabaseAliasRepository" in source
    assert "prepare_aliases" in source
    assert "aggregate_resolved_rows" in source
    assert "render_alias_editor" in source
    assert "reset_alias_editor_state" in source
    assert "FastEmbeddingProvider" not in source
    assert "render_name_review" not in source
    assert "ReviewBoard" not in source


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
    assert "current_alias_aggregate_fingerprint" in source
    assert 'if uploaded_files and st.button("Process PDFs")' in source
    assert "reset_alias_editor_state(st.session_state)" in source
