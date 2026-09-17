from __future__ import annotations

from dataclasses import dataclass

import pytest

from company_names.repository import (
    AliasMapping,
    RepositoryUnavailableError,
    SupabaseAliasRepository,
)


@dataclass
class Response:
    data: object = None


class RecordingQuery:
    def __init__(self, client: "RecordingClient", table: str) -> None:
        self._client = client
        self._client.calls.append(("table", table))

    def select(self, columns: str) -> "RecordingQuery":
        self._client.calls.append(("select", columns))
        return self

    def order(self, column: str) -> "RecordingQuery":
        self._client.calls.append(("order", column))
        return self

    def upsert(
        self, rows: list[dict[str, str]], on_conflict: str
    ) -> "RecordingQuery":
        self._client.calls.append(("upsert", rows, on_conflict))
        return self

    def execute(self) -> Response:
        self._client.calls.append(("execute",))
        if self._client.error is not None:
            raise self._client.error
        return Response(self._client.data)


class RecordingClient:
    def __init__(self, data: object = None, error: Exception | None = None) -> None:
        self.data = data
        self.error = error
        self.calls: list[tuple[object, ...]] = []

    def table(self, name: str) -> RecordingQuery:
        return RecordingQuery(self, name)


def test_list_aliases_reads_only_the_alias_table() -> None:
    client = RecordingClient([{
        "cleaned_alias": "HKTRM",
        "alias_key": "hktrm",
        "canonical_name": "Hong Kong TUYI Business Travel Limited",
    }])

    result = SupabaseAliasRepository(client).list_aliases()

    assert result == [AliasMapping(
        "HKTRM", "hktrm", "Hong Kong TUYI Business Travel Limited"
    )]
    assert client.calls == [
        ("table", "company_aliases"),
        ("select", "cleaned_alias,alias_key,canonical_name"),
        ("order", "alias_key"),
        ("execute",),
    ]


def test_upsert_aliases_uses_alias_key_conflict() -> None:
    client = RecordingClient()

    SupabaseAliasRepository(client).upsert_aliases([
        AliasMapping("HKTRM", "hktrm", "Hong Kong TUYI Business Travel Limited")
    ])

    assert client.calls == [
        ("table", "company_aliases"),
        ("upsert", [{
            "cleaned_alias": "HKTRM",
            "alias_key": "hktrm",
            "canonical_name": "Hong Kong TUYI Business Travel Limited",
        }], "alias_key"),
        ("execute",),
    ]


def test_upsert_aliases_skips_client_for_an_empty_batch() -> None:
    client = RecordingClient()

    SupabaseAliasRepository(client).upsert_aliases([])

    assert client.calls == []


@pytest.mark.parametrize("url", ["https://project.supabase.co", "https://project.supabase.co/"])
def test_from_credentials_normalizes_trailing_slash(monkeypatch, url: str) -> None:
    calls: list[tuple[str, str]] = []
    client = RecordingClient()

    def create_client(recorded_url: str, key: str) -> RecordingClient:
        calls.append((recorded_url, key))
        return client

    monkeypatch.setattr("company_names.repository.create_client", create_client)

    repository = SupabaseAliasRepository.from_credentials(f" {url} ", " service-key ")

    assert repository._client is client
    assert calls == [("https://project.supabase.co", "service-key")]


@pytest.mark.parametrize(
    ("url", "service_key", "message"),
    [
        ("", "service-key", "SUPABASE_URL is missing"),
        ("https://project.supabase.co", "", "SUPABASE_SERVICE_KEY is missing"),
        (None, "service-key", "SUPABASE_URL is missing"),
    ],
)
def test_from_credentials_rejects_missing_values(url, service_key, message: str) -> None:
    with pytest.raises(RepositoryUnavailableError, match=f"^{message}$"):
        SupabaseAliasRepository.from_credentials(url, service_key)


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (lambda repository: repository.list_aliases(), "Could not read company aliases"),
        (
            lambda repository: repository.upsert_aliases([
                AliasMapping("Alias", "alias", "Canonical")
            ]),
            "Could not save company aliases",
        ),
    ],
)
def test_client_failures_are_translated_without_leaking_details(operation, expected) -> None:
    repository = SupabaseAliasRepository(
        RecordingClient(error=RuntimeError("service-key-secret backend exploded"))
    )

    with pytest.raises(RepositoryUnavailableError, match=f"^{expected}") as caught:
        operation(repository)

    assert caught.value.__cause__ is None
    assert "service-key-secret" not in str(caught.value)


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        [None],
        [{"alias_key": "incomplete"}],
        [{"cleaned_alias": 123, "alias_key": "alias", "canonical_name": "Canonical"}],
        [{"cleaned_alias": "Alias", "alias_key": " ", "canonical_name": "Canonical"}],
        [{
            "cleaned_alias": "Alias",
            "alias_key": "alias",
            "canonical_name": "Canonical",
            "unexpected": "field",
        }],
    ],
)
def test_malformed_list_response_is_safely_translated(data: object) -> None:
    with pytest.raises(
        RepositoryUnavailableError, match="^Could not read company aliases"
    ) as caught:
        SupabaseAliasRepository(RecordingClient(data)).list_aliases()

    assert caught.value.__cause__ is None


def test_supabase_request_failure_identifies_backend_and_code_without_secret(caplog):
    repository = SupabaseAliasRepository(
        RecordingClient(error=RuntimeError("private-key-and-response-body"))
    )
    with pytest.raises(RepositoryUnavailableError) as caught:
        repository.list_aliases()
    message = str(caught.value)
    for text in ("backend=supabase", "operation=read", "stage=request",
                 "SupabaseAliasRepository.list_aliases", "RuntimeError", "ALIAS_API_URL"):
        assert text in message
        assert text in caplog.text
    assert "private-key-and-response-body" not in message + caplog.text
    assert "row=" not in message  # No company has been read yet.


def test_supabase_invalid_row_identifies_row_and_field(caplog):
    valid = {"cleaned_alias": "Acme", "alias_key": "acme", "canonical_name": "Acme"}
    invalid = {"cleaned_alias": "Other", "alias_key": "other", "canonical_name": " "}
    with pytest.raises(RepositoryUnavailableError) as caught:
        SupabaseAliasRepository(RecordingClient([valid, invalid])).list_aliases()
    for text in ("backend=supabase", "stage=validate_rows", "row=2", "field=canonical_name"):
        assert text in str(caught.value)
    assert "row=2" in caplog.text


def test_supabase_api_error_retains_error_code_but_not_raw_details(caplog):
    from postgrest.exceptions import APIError
    error = APIError({"code": "PGRST205", "message": "private-api-key",
                      "details": "private-database-details", "hint": "private-hint"})
    with pytest.raises(RepositoryUnavailableError) as caught:
        SupabaseAliasRepository(RecordingClient(error=error)).list_aliases()
    assert "error_code=PGRST205" in str(caught.value)
    assert "private-" not in str(caught.value) + caplog.text


def test_unrecognized_error_code_is_not_exposed(caplog):
    from postgrest.exceptions import APIError
    error = APIError({"code": "private-secret-value", "message": "private-message"})
    with pytest.raises(RepositoryUnavailableError) as caught:
        SupabaseAliasRepository(RecordingClient(error=error)).list_aliases()
    assert "error_code=" not in str(caught.value)
    assert "private-" not in str(caught.value) + caplog.text


def test_unexpected_field_names_and_company_values_are_not_exposed(caplog):
    row = {"cleaned_alias": "private-company-value", "alias_key": "alias",
           "canonical_name": "Company", "private-secret-field": "private-value"}
    with pytest.raises(RepositoryUnavailableError) as caught:
        SupabaseAliasRepository(RecordingClient([row])).list_aliases()
    assert "unexpected fields" in str(caught.value)
    assert "row=1" in str(caught.value)
    assert "private-" not in str(caught.value) + caplog.text
