from company_names.auth import authenticate, is_authenticated, log_out


def test_correct_password_authenticates_without_storing_plaintext() -> None:
    state: dict[str, object] = {}

    assert authenticate(state, "correct", "correct") is True
    assert is_authenticated(state, "correct") is True
    assert "correct" not in state.values()


def test_wrong_password_does_not_authenticate() -> None:
    state: dict[str, object] = {}

    assert authenticate(state, "wrong", "correct") is False
    assert is_authenticated(state, "correct") is False


def test_changing_configured_password_invalidates_session() -> None:
    state: dict[str, object] = {}
    authenticate(state, "old", "old")

    assert is_authenticated(state, "new") is False


def test_logout_clears_the_entire_app_session() -> None:
    state: dict[str, object] = {"prepared_aliases": object(), "alias_search": "Acme"}
    authenticate(state, "correct", "correct")

    log_out(state)

    assert state == {}
