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


def test_http_error_names_operation_status_and_location(caplog):
    repo = repository(httpx.MockTransport(lambda request: httpx.Response(503, text=TOKEN)))
    with pytest.raises(RepositoryUnavailableError) as caught:
        repo.list_aliases()
    message = str(caught.value)
    for text in ("backend=home-api", "operation=read", "stage=response", "HTTP 503",
                 "HttpAliasRepository.list_aliases"):
        assert text in message
    assert "not confirmed saved" not in message
    assert TOKEN not in message + caplog.text


def test_http_invalid_row_reports_index_and_field_without_dumping_response(caplog):
    payload = {"aliases": [
        {"cleaned_alias": "Acme", "alias_key": "acme", "canonical_name": "Acme"},
        {"cleaned_alias": "Other", "alias_key": "other", "canonical_name": None},
    ]}
    repo = repository(httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    with pytest.raises(RepositoryUnavailableError) as caught:
        repo.list_aliases()
    for text in ("stage=validate_rows", "row=2", "field=canonical_name"):
        assert text in str(caught.value)
        assert text in caplog.text


def test_http_decode_and_timeout_failures_have_distinct_stages(caplog):
    repo = repository(httpx.MockTransport(lambda request: httpx.Response(200, text=TOKEN)))
    with pytest.raises(RepositoryUnavailableError, match="stage=decode_json"):
        repo.list_aliases()

    def timeout(request):
        raise httpx.ReadTimeout(TOKEN, request=request)
    repo = repository(httpx.MockTransport(timeout))
    with pytest.raises(RepositoryUnavailableError) as caught:
        repo.upsert_aliases([MAPPING])
    for text in ("operation=save", "stage=request", "ReadTimeout", "not confirmed saved"):
        assert text in str(caught.value)
    assert TOKEN not in str(caught.value) + caplog.text
