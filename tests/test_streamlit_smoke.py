"""Runtime coverage for the real Streamlit alias editor."""

from pathlib import Path

from streamlit.testing.v1 import AppTest


FIXTURE_APP = Path(__file__).parent / "fixtures" / "simple_alias_app.py"
PAGINATED_FIXTURE_APP = Path(__file__).parent / "fixtures" / "paginated_alias_app.py"
REAL_APP = Path(__file__).parents[1] / "app.py"
CANONICAL = "Hong Kong TUYI Business Travel Limited"


def _app() -> AppTest:
    return AppTest.from_file(FIXTURE_APP, default_timeout=10).run()


def test_simple_alias_editor_renders_without_exception() -> None:
    app = _app()

    assert not app.exception
    assert "Company name mappings" in [item.value for item in app.subheader]
    assert any("HKTRMs" in item.value for item in app.markdown)
    assert any("Suggested from HKTRM" in item.value for item in app.caption)


def test_accepting_suggestion_does_not_write_automatically() -> None:
    app = _app()

    app.button(key="accept_alias_hktrms").click().run()

    assert app.text_input(key="alias_final_hktrms").value == CANONICAL
    assert app.session_state["fixture_repository"].saved == []


def test_correct_save_returns_one_aggregated_canonical_row() -> None:
    app = _app()
    app.button(key="accept_alias_hktrms").click().run()

    app.button(key="save_aliases").click().run()

    saved = app.session_state["fixture_repository"].saved
    assert len(saved) == 2
    result = app.session_state["fixture_result"]
    assert result.to_dict("records") == [{
        "TRAVEL AGENT": CANONICAL,
        "Sum of RNS": 5.0,
        "Sum of R REVENUE": 150.0,
    }]


def test_failed_save_retains_typed_final_name_for_retry() -> None:
    app = _app()
    app.text_input(key="alias_final_hktrms").input(CANONICAL)
    app.checkbox(key="fixture_fail_next_save").check()

    app.button(key="save_aliases").click().run()

    assert app.text_input(key="alias_final_hktrms").value == CANONICAL
    assert app.session_state["fixture_repository"].saved == []
    assert any("network unavailable" in item.value for item in app.error)


def test_paginated_editor_navigates_without_losing_first_page_edit() -> None:
    app = AppTest.from_file(PAGINATED_FIXTURE_APP, default_timeout=10).run()
    assert not app.exception
    assert app.selectbox(key="alias_status_filter").value == "Needs review"
    assert app.selectbox(key="alias_page_size").value == 20
    assert app.session_state["alias_page"] == 1

    app.text_input(key="alias_final_company 00").input("Edited Company").run()
    app.button(key="alias_next_top").click().run()

    assert not app.exception
    assert app.session_state["alias_page"] == 2
    app.button(key="alias_previous_top").click().run()
    assert app.text_input(key="alias_final_company 00").value == "Edited Company"


def test_real_app_login_gates_uploads_and_supports_logout() -> None:
    app = AppTest.from_file(REAL_APP, default_timeout=10)
    app.secrets = {"ADMIN_PASSWORD": "correct"}
    app.run()

    assert not app.exception
    assert len(app.get("file_uploader")) == 0
    assert app.text_input[0].label == "Password"

    app.text_input[0].input("wrong")
    next(button for button in app.button if button.label == "Log in").click().run()
    assert len(app.get("file_uploader")) == 0
    assert any("Incorrect password" in item.value for item in app.error)

    app.text_input[0].input("correct")
    next(button for button in app.button if button.label == "Log in").click().run()
    assert not app.exception
    assert len(app.get("file_uploader")) == 1

    app.button(key="log_out").click().run()
    assert len(app.get("file_uploader")) == 0


def test_real_app_password_change_invalidates_authenticated_session() -> None:
    app = AppTest.from_file(REAL_APP, default_timeout=10)
    app.secrets = {"ADMIN_PASSWORD": "old"}
    app.run()
    app.text_input[0].input("old")
    next(button for button in app.button if button.label == "Log in").click().run()
    assert len(app.get("file_uploader")) == 1

    app.secrets = {"ADMIN_PASSWORD": "new"}
    app.run()

    assert len(app.get("file_uploader")) == 0
    assert app.text_input[0].label == "Password"
