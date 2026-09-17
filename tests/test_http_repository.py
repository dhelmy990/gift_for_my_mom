import httpx
import pytest

from alias_server.app import create_app
from company_names import http_repository as module
from company_names.repository import AliasMapping, RepositoryUnavailableError

TOKEN = "test-token-with-at-least-32-characters"
MAPPING = AliasMapping("Alias", "alias", "Canonical")


def repository(transport):
    assert hasattr(module, "HttpAliasRepository"), "HTTP repository is not implemented"
    return module.HttpAliasRepository("http://localhost", TOKEN, transport=transport)


def test_repository_round_trip_through_real_api(tmp_path):
    repo = repository(httpx.WSGITransport(app=create_app(tmp_path / "db", TOKEN)))
    assert repo.list_aliases() == []
    repo.upsert_aliases([MAPPING])
    assert repo.list_aliases() == [MAPPING]
    repo.upsert_aliases([AliasMapping("Alias", "alias", "Changed")])
    assert repo.list_aliases()[0].canonical_name == "Changed"


@pytest.mark.parametrize("url", ["http://public.example", "https://user:password@example.com",
                                   "https://example.com?token=secret", "ftp://example.com", "",
                                   "https://example.com:bad", "https://example.com:99999",
                                   "https://example.com/\x00bad", "https://example.com/\nbad"])
def test_unsafe_urls_rejected(url):
    assert hasattr(module, "HttpAliasRepository")
    with pytest.raises(RepositoryUnavailableError):
        module.HttpAliasRepository(url, TOKEN)


@pytest.mark.parametrize("status, message", [(401, "authentication"), (403, "authentication"),
                                           (503, "unavailable"), (302, "redirect")])
def test_status_errors_are_actionable_without_leaking_secrets(status, message):
    repo = repository(httpx.MockTransport(lambda request: httpx.Response(
        status, text="private-database-detail", headers={"location": "https://elsewhere.invalid"})))
    with pytest.raises(RepositoryUnavailableError, match=message) as error:
        repo.list_aliases()
    assert "private-database-detail" not in str(error.value)
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize("payload", [None, {}, {"aliases": [None]},
                                     {"aliases": [{"alias_key": "missing-fields"}]},
                                     {"aliases": [{"alias_key": " ", "cleaned_alias": "A", "canonical_name": "A"}]}])
def test_malformed_response_is_rejected(payload):
    repo = repository(httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    with pytest.raises(RepositoryUnavailableError, match="invalid response"):
        repo.list_aliases()


def test_timeouts_are_safe_and_empty_writes_do_not_contact_server():
    calls = []
    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret detail", request=request)
    repo = repository(httpx.MockTransport(timeout))
    repo.upsert_aliases([])
    assert calls == []
    with pytest.raises(RepositoryUnavailableError, match="unreachable"):
        repo.list_aliases()


def test_save_requires_matching_acknowledgement():
    repo = repository(httpx.MockTransport(lambda request: httpx.Response(200, json={"saved": 0})))
    with pytest.raises(RepositoryUnavailableError, match="invalid response"):
        repo.upsert_aliases([MAPPING])
