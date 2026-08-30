"""Session authentication for the private Streamlit application."""

from __future__ import annotations

from collections.abc import MutableMapping
import hashlib
import hmac


_AUTH_STATE_KEY = "_authenticated_admin_password"


def _password_binding(password: str) -> str:
    return hmac.new(
        b"company-report-login-v1",
        password.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def is_authenticated(state: MutableMapping[str, object], configured: object) -> bool:
    """Return whether this session is bound to the configured admin password."""
    if not isinstance(configured, str) or not configured:
        return False
    binding = state.get(_AUTH_STATE_KEY)
    return isinstance(binding, str) and hmac.compare_digest(
        binding, _password_binding(configured)
    )


def authenticate(
    state: MutableMapping[str, object], candidate: object, configured: object
) -> bool:
    """Authenticate this session without retaining the entered password."""
    valid = (
        isinstance(candidate, str)
        and isinstance(configured, str)
        and bool(configured)
        and hmac.compare_digest(candidate, configured)
    )
    if valid:
        state[_AUTH_STATE_KEY] = _password_binding(configured)
    else:
        state.pop(_AUTH_STATE_KEY, None)
    return valid


def log_out(state: MutableMapping[str, object]) -> None:
    """Clear authentication and all report data from the browser session."""
    for key in list(state):
        del state[key]
