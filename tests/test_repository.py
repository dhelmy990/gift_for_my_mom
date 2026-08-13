from __future__ import annotations

from pathlib import Path

import pytest

from company_names.models import SubmissionPayload
from company_names.repository import (
    RepositoryConfigurationError,
    RepositoryUnavailableError,
    SupabaseMappingRepository,
)


class Result:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.filters = []
        self.ordering = []

    def select(self, columns):
        self.columns = columns
        return self

    def in_(self, column, values):
        self.filters.append((column, values))
        return self

    def order(self, column):
        self.ordering.append(column)
        return self

    def execute(self):
        if self.client.error:
            raise self.client.error
        return Result(self.client.rows[self.table])


class RpcQuery:
    def __init__(self, client):
        self.client = client

    def execute(self):
        self.client.rpc_execute_count += 1
        if self.client.error:
            raise self.client.error
        return Result({"temp-1": "00000000-0000-0000-0000-000000000001"})


class FakeClient:
    def __init__(self, rows=None, error=None):
        self.rows = rows or {}
        self.error = error
        self.rpc_calls = []
        self.rpc_execute_count = 0

    def table(self, name):
        return Query(self, name)

    def rpc(self, name, arguments):
        self.rpc_calls.append((name, arguments))
        return RpcQuery(self)


def repository(rows=None, error=None):
    return SupabaseMappingRepository(FakeClient(rows, error))


@pytest.mark.parametrize("url,key", [("", "key"), ("url", ""), ("  ", "key")])
def test_credentials_are_required_without_disclosing_them(url, key):
    with pytest.raises(RepositoryConfigurationError) as caught:
        SupabaseMappingRepository.from_credentials(url, key)
    assert key not in str(caught.value) if key else True


def test_get_exact_mappings_normalizes_lookup_and_returns_group_data():
    repo = repository({"name_mappings": [{
        "cleaned_name": "M.T.L.", "lookup_key": "mtl", "group_id": "g1",
        "member_embedding": "[1, 2.5]",
        "name_groups": {"id": "g1", "canonical_title": "MTL Travel"},
    }]})
    result = repo.get_exact_mappings(["MTL"])
    assert result["MTL"].group_id == "g1"
    assert result["MTL"].canonical_title == "MTL Travel"
    assert result["MTL"].member_name == "M.T.L."
    assert result["MTL"].vector == (1.0, 2.5)


def test_get_exact_mappings_returns_each_input_with_the_same_normalized_key():
    repo = repository({"name_mappings": [{
        "cleaned_name": "MTL", "lookup_key": "mtl", "group_id": "g1",
        "member_embedding": None,
        "name_groups": {"id": "g1", "canonical_title": "MTL Travel"},
    }]})
    result = repo.get_exact_mappings(["mtl", "MTL"])
    assert set(result) == {"mtl", "MTL"}


def test_list_groups_and_candidates_parse_vectors():
    repo = repository({
        "name_groups": [{"id": "g1", "canonical_title": "Beta", "title_embedding": [1, 2]}],
        "name_mappings": [{"group_id": "g1", "cleaned_name": "Beta Ltd", "member_embedding": "[3,4]", "name_groups": {"canonical_title": "Beta"}}],
    })
    assert repo.list_groups()[0].title_embedding == (1.0, 2.0)
    assert repo.list_candidates()[0].vector == (3.0, 4.0)


def test_invalid_vector_becomes_none():
    repo = repository({"name_groups": [{"id": "g1", "canonical_title": "Beta", "title_embedding": "[NaN]"}]})
    assert repo.list_groups()[0].title_embedding is None


def test_submit_uses_one_rpc_call_with_exact_payload():
    client = FakeClient()
    repo = SupabaseMappingRepository(client)
    payload = SubmissionPayload([{"id": "temp-1"}], [], [])
    result = repo.submit(payload)
    assert client.rpc_calls == [("submit_name_review", {"payload": {"groups": [{"id": "temp-1"}], "mappings": [], "unmap_names": []}})]
    assert client.rpc_execute_count == 1
    assert result == {"temp-1": "00000000-0000-0000-0000-000000000001"}


def test_export_rows_are_stably_sorted():
    repo = repository({"name_mappings": [
        {"cleaned_name": "Zulu", "name_groups": {"canonical_title": "Alpha"}},
        {"cleaned_name": "Able", "name_groups": {"canonical_title": "Beta"}},
        {"cleaned_name": "Able", "name_groups": {"canonical_title": "Alpha"}},
    ]})
    assert [(r.canonical_title, r.cleaned_name) for r in repo.export_rows()] == [
        ("Alpha", "Able"), ("Alpha", "Zulu"), ("Beta", "Able")]


def test_client_errors_are_safely_wrapped():
    repo = repository(error=RuntimeError("service-key-secret backend exploded"))
    with pytest.raises(RepositoryUnavailableError) as caught:
        repo.list_groups()
    assert "list groups" in str(caught.value)
    assert "service-key-secret" not in str(caught.value)
    assert "exploded" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_schema_contains_security_vector_and_atomic_review_contract():
    sql = (Path(__file__).parents[1] / "supabase" / "schema.sql").read_text().lower()
    assert "create extension if not exists vector" in sql
    assert "create extension if not exists pgcrypto" in sql
    assert sql.count("vector(384)") >= 2
    assert sql.count("enable row level security") >= 2
    assert "revoke all on table public.name_groups from anon, authenticated" in sql
    assert "revoke all on table public.name_mappings from anon, authenticated" in sql
    assert sql.count("using hnsw") >= 2 and sql.count("vector_cosine_ops") >= 2
    assert "create or replace function public.submit_name_review(payload jsonb)" in sql
    assert "security invoker" in sql and "set search_path = pg_catalog, public" in sql
    assert "jsonb_typeof(payload) <> 'object'" in sql
    assert "not (payload ? 'groups')" in sql
    assert "unmap_names" in sql and "on conflict (cleaned_name) do update" in sql
    assert "delete from public.name_mappings" in sql
    assert "delete from public.name_groups" not in sql
    assert "return temp_map" in sql
    assert "revoke execute on function public.submit_name_review(jsonb) from public, anon, authenticated" in sql
    assert "grant execute on function public.submit_name_review(jsonb) to service_role" in sql
    assert "grant execute on function public.valid_review_embedding(jsonb) to service_role" in sql
    assert "regexp_replace" in sql
    assert "function public.review_lookup_key(name text)" in sql
    assert "replace(lower" in sql and "'ß', 'ss'" in sql
    assert "public.review_lookup_key(coalesce" in sql
    assert "member_embedding = case when mapping_item ? 'member_embedding'" in sql
