"""Persistence boundary for validated company-name mappings."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from collections.abc import Callable
from typing import Any, Protocol
from uuid import UUID

from .cleaning import normalize_lookup_key
from .matching import Candidate
from .models import SubmissionPayload


class RepositoryConfigurationError(ValueError):
    """The repository was not supplied usable connection settings."""


class RepositoryUnavailableError(RuntimeError):
    """The backing repository could not complete an operation."""


@dataclass(frozen=True)
class GroupRecord:
    id: str
    canonical_title: str
    title_embedding: tuple[float, ...] | None


@dataclass(frozen=True)
class ExactMapping:
    group_id: str
    canonical_title: str
    member_name: str
    vector: tuple[float, ...] | None


@dataclass(frozen=True)
class ExportRow:
    canonical_title: str
    cleaned_name: str


class MappingRepository(Protocol):
    def list_groups(self) -> list[GroupRecord]: ...

    def get_exact_mappings(self, cleaned_names: list[str]) -> dict[str, ExactMapping]: ...

    def list_candidates(self) -> list[Candidate]: ...

    def submit(self, payload: SubmissionPayload) -> dict[str, str]: ...

    def export_rows(self) -> list[ExportRow]: ...


def _vector(value: object) -> tuple[float, ...] | None:
    if value is None:
        return None
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, (list, tuple)):
        raise TypeError("vector must be a sequence")
    result = tuple(float(item) for item in parsed)
    return result if all(math.isfinite(item) for item in result) else None


class SupabaseMappingRepository:
    """Supabase implementation whose public failures never expose client details."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_credentials(cls, url: str, service_key: str) -> "SupabaseMappingRepository":
        if not isinstance(url, str) or not url.strip():
            raise RepositoryConfigurationError("Supabase URL is required")
        if not isinstance(service_key, str) or not service_key.strip():
            raise RepositoryConfigurationError("Supabase service key is required")
        try:
            from supabase import create_client

            return cls(create_client(url.strip(), service_key.strip()))
        except RepositoryConfigurationError:
            raise
        except Exception as exc:
            raise RepositoryUnavailableError("Repository unavailable while connecting") from None

    def _execute(self, operation: str, query_factory: Callable[[], Any]) -> Any:
        try:
            return query_factory().execute().data
        except (RepositoryConfigurationError, RepositoryUnavailableError):
            raise
        except Exception as exc:
            raise RepositoryUnavailableError(
                f"Repository unavailable during {operation}"
            ) from None

    def _parse(self, operation: str, parser: Callable[[], Any]) -> Any:
        try:
            return parser()
        except (RepositoryConfigurationError, RepositoryUnavailableError):
            raise
        except Exception:
            raise RepositoryUnavailableError(
                f"Repository unavailable during {operation}"
            ) from None

    @staticmethod
    def _rows(data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise TypeError("response data must be a list of objects")
        return data

    @staticmethod
    def _text(value: Any) -> str:
        if not isinstance(value, str):
            raise TypeError("response field must be text")
        return value

    def list_groups(self) -> list[GroupRecord]:
        data = self._execute(
            "list groups",
            lambda: self._client.table("name_groups")
            .select("id,canonical_title,title_embedding").order("canonical_title"),
        )
        return self._parse("list groups", lambda: [
            GroupRecord(self._text(row["id"]), self._text(row["canonical_title"]),
                        _vector(row.get("title_embedding")))
            for row in self._rows(data)
        ])

    def get_exact_mappings(self, cleaned_names: list[str]) -> dict[str, ExactMapping]:
        requested: dict[str, list[str]] = {}
        for name in cleaned_names:
            requested.setdefault(normalize_lookup_key(name), []).append(name)
        if not requested:
            return {}
        data = self._execute(
            "get exact mappings",
            lambda: self._client.table("name_mappings")
            .select("cleaned_name,lookup_key,group_id,member_embedding,name_groups(id,canonical_title)")
            .in_("lookup_key", list(requested)),
        )
        def parse() -> dict[str, ExactMapping]:
            result: dict[str, ExactMapping] = {}
            for row in self._rows(data):
                lookup_key = self._text(row["lookup_key"])
                originals = requested.get(lookup_key)
                if originals is None:
                    continue
                group = row["name_groups"]
                if not isinstance(group, dict):
                    raise TypeError("nested group must be an object")
                for original in originals:
                    result[original] = ExactMapping(
                        self._text(row["group_id"]), self._text(group["canonical_title"]),
                        self._text(row["cleaned_name"]), _vector(row.get("member_embedding")),
                    )
            return result
        return self._parse("get exact mappings", parse)

    def list_candidates(self) -> list[Candidate]:
        groups = self.list_groups()
        data = self._execute(
            "list candidates",
            lambda: self._client.table("name_mappings")
            .select("group_id,cleaned_name,member_embedding"),
        )
        def parse() -> list[Candidate]:
            group_titles = {group.id: group.canonical_title for group in groups}
            rows = [
                Candidate(
                    group.id, group.canonical_title, group.canonical_title,
                    group.title_embedding,
                )
                for group in groups
            ]
            for row in self._rows(data):
                group_id = self._text(row["group_id"])
                rows.append(Candidate(
                    group_id, group_titles[group_id],
                    self._text(row["cleaned_name"]), _vector(row.get("member_embedding")),
                ))
            return rows
        return self._parse("list candidates", parse)

    def submit(self, payload: SubmissionPayload) -> dict[str, str]:
        result = self._execute(
            "submit review",
            lambda: self._client.rpc("submit_name_review", {"payload": asdict(payload)}),
        )
        def parse() -> dict[str, str]:
            if not isinstance(result, dict):
                raise TypeError("submission result must be an object")
            resolved: dict[str, str] = {}
            for temp_id, group_id in result.items():
                temp_id = self._text(temp_id)
                group_id = self._text(group_id)
                if not temp_id:
                    raise ValueError("empty temporary group id")
                UUID(group_id)
                resolved[temp_id] = group_id
            return resolved
        return self._parse("submit review", parse)

    def export_rows(self) -> list[ExportRow]:
        data = self._execute(
            "export mappings",
            lambda: self._client.table("name_mappings")
            .select("cleaned_name,name_groups(canonical_title)"),
        )
        def parse() -> list[ExportRow]:
            rows = []
            for row in self._rows(data):
                group = row["name_groups"]
                if not isinstance(group, dict):
                    raise TypeError("nested group must be an object")
                rows.append(ExportRow(
                    self._text(group["canonical_title"]), self._text(row["cleaned_name"])
                ))
            return sorted(rows, key=lambda row: (row.canonical_title, row.cleaned_name))
        return self._parse("export mappings", parse)
