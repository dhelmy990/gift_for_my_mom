"""Persistence boundary for company-name aliases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from supabase import create_client

from .diagnostics import RepositoryUnavailableError, storage_failure


@dataclass(frozen=True)
class AliasMapping:
    cleaned_alias: str
    alias_key: str
    canonical_name: str


class AliasRepository(Protocol):
    def list_aliases(self) -> list[AliasMapping]: ...

    def upsert_aliases(self, mappings: list[AliasMapping]) -> None: ...


def parse_alias_rows(data, *, backend, location) -> list[AliasMapping]:
    """Identify a malformed stored row without dumping company data to logs."""
    summary = ("Could not read company aliases" if backend == "supabase"
               else "Alias API returned an invalid response")

    def invalid(reason, row=None, field=None):
        return storage_failure(f"{summary}: {reason}.", backend=backend,
                               operation="read", stage="validate_rows", location=location,
                               row=row, field=field)

    if not isinstance(data, list):
        raise invalid("expected an alias list")
    fields = ("cleaned_alias", "alias_key", "canonical_name")
    mappings, seen = [], set()
    for index, row in enumerate(data, start=1):
        if not isinstance(row, dict):
            raise invalid("expected an alias object", index)
        for field in fields:
            if field not in row:
                raise invalid("required field is missing", index, field)
            if not isinstance(row[field], str) or not row[field].strip():
                raise invalid("field must be nonblank text", index, field)
        if set(row) != set(fields):
            raise invalid("unexpected fields in alias object", index)
        if row["alias_key"] in seen:
            raise invalid("duplicate alias key", index, "alias_key")
        seen.add(row["alias_key"])
        mappings.append(AliasMapping(**row))
    return mappings


class SupabaseAliasRepository:
    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_credentials(cls, url: str, service_key: str) -> "SupabaseAliasRepository":
        if not isinstance(url, str) or not url.strip():
            raise RepositoryUnavailableError("SUPABASE_URL is missing")
        if not isinstance(service_key, str) or not service_key.strip():
            raise RepositoryUnavailableError("SUPABASE_SERVICE_KEY is missing")
        try:
            return cls(create_client(url.strip().rstrip("/"), service_key.strip()))
        except Exception as error:
            raise storage_failure("Could not create Supabase client", backend="supabase",
                                  operation="configure", stage="create_client",
                                  location="company_names.repository.SupabaseAliasRepository.from_credentials",
                                  error=error) from None

    def list_aliases(self) -> list[AliasMapping]:
        try:
            response = (
                self._client.table("company_aliases")
                .select("cleaned_alias,alias_key,canonical_name")
                .order("alias_key")
                .execute()
            )
        except Exception as error:
            raise storage_failure("Could not read company aliases; database request failed before company matching.",
                                  backend="supabase", operation="read", stage="request",
                                  location="company_names.repository.SupabaseAliasRepository.list_aliases",
                                  error=error) from None
        return parse_alias_rows(getattr(response, "data", None), backend="supabase",
                                location="company_names.repository.SupabaseAliasRepository.list_aliases")

    def upsert_aliases(self, mappings: list[AliasMapping]) -> None:
        if not mappings:
            return
        rows = [
            {
                "cleaned_alias": mapping.cleaned_alias,
                "alias_key": mapping.alias_key,
                "canonical_name": mapping.canonical_name,
            }
            for mapping in mappings
        ]
        try:
            (
                self._client.table("company_aliases")
                .upsert(rows, on_conflict="alias_key")
                .execute()
            )
        except Exception as error:
            raise storage_failure("Could not save company aliases; batch save was not confirmed.",
                                  backend="supabase", operation="save", stage="request",
                                  location="company_names.repository.SupabaseAliasRepository.upsert_aliases",
                                  error=error) from None
