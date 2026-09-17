import pytest

from company_names import configuration
from company_names.repository import RepositoryUnavailableError


def settings(values):
    assert hasattr(configuration, "repository_settings"), "API configuration selection is not implemented"
    return configuration.repository_settings(values.get)


def test_api_configuration_takes_priority_over_legacy():
    assert settings({"ALIAS_API_URL": "https://aliases.example", "ALIAS_API_TOKEN": "token",
                     "SUPABASE_URL": "old", "SUPABASE_SERVICE_KEY": "old"}) == (
                         "api", "https://aliases.example", "token")


@pytest.mark.parametrize("partial", [{"ALIAS_API_URL": "https://aliases.example"},
                                     {"ALIAS_API_TOKEN": "token"}])
def test_partial_api_configuration_does_not_fall_back(partial):
    with pytest.raises(RepositoryUnavailableError, match="both"):
        settings({**partial, "SUPABASE_URL": "old", "SUPABASE_SERVICE_KEY": "old"})


def test_legacy_configuration_and_unconfigured_app():
    assert settings({}) is None
    assert settings({"SUPABASE_URL": "old", "SUPABASE_SERVICE_KEY": "key"}) == ("supabase", "old", "key")


def test_real_app_prepares_aliases_from_home_server(monkeypatch, tmp_path):
    import httpx
    import pandas as pd
    import app
    from alias_server.app import create_app
    from company_names.http_repository import HttpAliasRepository
    from company_names.repository import AliasMapping

    token = "test-token-with-at-least-32-characters"
    api = create_app(tmp_path / "db", token)
    real_repository = HttpAliasRepository("http://localhost", token,
                                         transport=httpx.WSGITransport(app=api))
    real_repository.upsert_aliases([AliasMapping("ACME", "acme", "Saved Company")])
    monkeypatch.setattr(app, "_secret", {"ALIAS_API_URL": "http://localhost",
                                      "ALIAS_API_TOKEN": token}.get)
    calls = []
    def get_repository(url, key, backend):
        calls.append((url, key, backend))
        return real_repository
    monkeypatch.setattr(app, "get_alias_repository", get_repository)
    frame = pd.DataFrame([{"TRAVEL AGENT": "ACME", "Sum of RNS": 2,
                           "Sum of R REVENUE": 150}])
    result = app._prepare_collation_aliases([frame])
    assert result.database_available
    assert result.review_rows[0].final_name == "SAVED COMPANY"
    assert calls == [("http://localhost", token, "api")]
