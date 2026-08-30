"""Persistence boundary for company-name aliases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from supabase import create_client


class RepositoryUnavailableError(RuntimeError):
    """Supabase could not complete an alias operation."""


@dataclass(frozen=True)
class AliasMapping:
    cleaned_alias: str
    alias_key: str
    canonical_name: str


class AliasRepository(Protocol):
    def list_aliases(self) -> list[AliasMapping]: ...

    def upsert_aliases(self, mappings: list[AliasMapping]) -> None: ...


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
            return cls(create_client(url.strip(), service_key.strip()))
        except Exception:
            raise RepositoryUnavailableError("Could not create Supabase client") from None

    def list_aliases(self) -> list[AliasMapping]:
        try:
            response = (
                self._client.table("company_aliases")
                .select("cleaned_alias,alias_key,canonical_name")
                .order("alias_key")
                .execute()
            )
            if not isinstance(response.data, list):
                raise TypeError("alias response data must be a list")
            return [AliasMapping(**row) for row in response.data]
        except Exception:
            raise RepositoryUnavailableError("Could not read company aliases") from None

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
        except Exception:
            raise RepositoryUnavailableError("Could not save company aliases") from None
