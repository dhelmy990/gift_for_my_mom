from __future__ import annotations

from pathlib import Path

import pytest

from company_names.models import SubmissionPayload
from company_names.models import Group, NameRecord, ReviewBoard
from company_names.review import build_submission
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
        return Result(self.client.rpc_result)


_DEFAULT_RPC_RESULT = object()


class FakeClient:
    def __init__(self, rows=None, error=None, rpc_result=_DEFAULT_RPC_RESULT):
        self.rows = rows or {}
        self.error = error
        self.rpc_calls = []
        self.rpc_execute_count = 0
        self.rpc_result = ({"temp-1": "00000000-0000-0000-0000-000000000001"}
                           if rpc_result is _DEFAULT_RPC_RESULT else rpc_result)

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
    payload = SubmissionPayload([{"id": "temp-1"}], [], [], "11111111-1111-4111-8111-111111111111")
    result = repo.submit(payload)
    assert client.rpc_calls == [("submit_name_review", {"payload": {"groups": [{"id": "temp-1"}], "mappings": [], "unmap_names": [], "request_id": "11111111-1111-4111-8111-111111111111"}})]
    assert client.rpc_execute_count == 1
    assert result == {"temp-1": "00000000-0000-0000-0000-000000000001"}


def test_lost_response_retry_rebuild_serializes_the_same_request_id():
    board = ReviewBoard(
        {"temp-1": Group("temp-1", "Group", False)},
        {"Alias": NameRecord("Alias", "temp-1", "suggested", selected=True)},
    )
    first = build_submission(board, {})
    failed_client = FakeClient(error=TimeoutError("response lost after commit"))
    with pytest.raises(RepositoryUnavailableError):
        SupabaseMappingRepository(failed_client).submit(first)

    retry = build_submission(board, {}, request_id=first.request_id)
    retry_client = FakeClient()
    SupabaseMappingRepository(retry_client).submit(retry)

    assert failed_client.rpc_calls[0][1]["payload"] == retry_client.rpc_calls[0][1]["payload"]
    assert retry_client.rpc_calls[0][1]["payload"]["request_id"] == first.request_id


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


@pytest.mark.parametrize(
    "method,rows",
    [
        (lambda repo: repo.list_groups(), {"name_groups": None}),
        (lambda repo: repo.list_groups(), {"name_groups": [None]}),
        (lambda repo: repo.list_groups(), {"name_groups": [{"id": "g", "canonical_title": "G", "title_embedding": ["wrong"]}]}),
        (lambda repo: repo.get_exact_mappings(["A"]), {"name_mappings": [{"lookup_key": "a", "group_id": "g", "cleaned_name": "A", "name_groups": None}]}),
        (lambda repo: repo.list_candidates(), {"name_mappings": [{"group_id": "g", "cleaned_name": "A", "name_groups": {}}]}),
        (lambda repo: repo.export_rows(), {"name_mappings": "wrong"}),
    ],
)
def test_malformed_query_responses_are_safely_wrapped(method, rows):
    with pytest.raises(RepositoryUnavailableError, match="Repository unavailable") as caught:
        method(repository(rows))
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("result", [None, [], {"temp-1": None}, {"temp-1": ["bad"]}])
def test_malformed_submit_responses_are_safely_wrapped(result):
    repo = SupabaseMappingRepository(FakeClient(rpc_result=result))
    payload = SubmissionPayload([], [], [], "11111111-1111-4111-8111-111111111111")
    with pytest.raises(RepositoryUnavailableError, match="Repository unavailable during submit review") as caught:
        repo.submit(payload)
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
    assert "member_embedding = case when mapping_item.raw ? 'member_embedding'" in sql
    assert "create table if not exists public.submission_ledger" in sql
    assert "request_id" in sql and "payload_fingerprint" in sql and "result jsonb" in sql
    assert "alter table public.submission_ledger enable row level security" in sql
    assert "revoke all on table public.submission_ledger from public, anon, authenticated" in sql
    assert "pg_advisory_xact_lock(hashtextextended" in sql
    assert "function public.purge_name_submission_ledger" in sql
    assert "interval '90 days'" in sql
    assert "revoke execute on function public.purge_name_submission_ledger(interval) from public, anon, authenticated" in sql
    assert "grant execute on function public.purge_name_submission_ledger(interval) to service_role" in sql
    assert "grant select, insert, delete on table public.submission_ledger to service_role" in sql


def test_review_rpc_stages_trimmed_identity_fields_before_checks_and_writes():
    sql = (Path(__file__).parents[1] / "supabase" / "schema.sql").read_text().lower()
    rpc = sql.split("create or replace function public.submit_name_review(payload jsonb)", 1)[1]
    rpc = rpc.split("revoke all on function public.set_updated_at()", 1)[0]
    assert "create temporary table _review_groups" in rpc
    assert "create temporary table _review_mappings" in rpc
    assert "create temporary table _review_unmaps" in rpc
    assert "btrim(group_value->>'id')" in rpc
    assert "btrim(group_value->>'canonical_title')" in rpc
    assert "btrim(mapping_value->>'cleaned_name')" in rpc
    assert "btrim(mapping_value->>'group_id')" in rpc
    assert "btrim(unmap_value #>> '{}')" in rpc
    assert "group by cleaned_name having count(*) > 1" in rpc
    assert "group by temp_id having count(*) > 1" in rpc
    assert "where n.cleaned_name = mapping_item.cleaned_name" in rpc
    assert "where cleaned_name = unmap_item.cleaned_name" in rpc
    assert "group by m->>'cleaned_name'" not in rpc
    assert "where n.cleaned_name = mapping_item->>'cleaned_name'" not in rpc
    assert "where cleaned_name = unmap_item #>> '{}'" not in rpc


def test_review_rpc_validates_raw_json_field_types_before_staging():
    sql = (Path(__file__).parents[1] / "supabase" / "schema.sql").read_text().lower()
    rpc = sql.split("create or replace function public.submit_name_review(payload jsonb)", 1)[1]
    rpc = rpc.split("revoke all on function public.set_updated_at()", 1)[0]
    group_checks = (
        "jsonb_typeof(group_value->'id') <> 'string'",
        "jsonb_typeof(group_value->'canonical_title') <> 'string'",
        "jsonb_typeof(group_value->'existing') <> 'boolean'",
        "jsonb_typeof(group_value->'canonical_key') <> 'string'",
        "not public.valid_review_embedding(group_value->'title_embedding')",
    )
    mapping_checks = (
        "jsonb_typeof(mapping_value->'cleaned_name') <> 'string'",
        "jsonb_typeof(mapping_value->'group_id') <> 'string'",
        "jsonb_typeof(mapping_value->'lookup_key') <> 'string'",
        "not public.valid_review_embedding(mapping_value->'member_embedding')",
    )
    for check in (*group_checks, *mapping_checks):
        assert check in rpc
    assert "not (group_value ? 'id')" in rpc
    assert "not (group_value ? 'canonical_title')" in rpc
    assert "not (group_value ? 'existing')" in rpc
    assert "not (mapping_value ? 'cleaned_name')" in rpc
    assert "not (mapping_value ? 'group_id')" in rpc
    assert "jsonb_typeof(unmap_value) <> 'string'" in rpc
    assert "jsonb_object_keys(payload)" in rpc
    assert "jsonb_object_keys(group_value)" in rpc
    assert "jsonb_object_keys(mapping_value)" in rpc
    assert "field_name not in ('groups', 'mappings', 'unmap_names', 'request_id')" in rpc
    assert "field_name not in ('id', 'canonical_title', 'canonical_key', 'existing', 'title_embedding')" in rpc
    assert "field_name not in ('cleaned_name', 'lookup_key', 'group_id', 'member_embedding')" in rpc
    first_staging_insert = rpc.index("insert into _review_groups")
    assert all(rpc.index(check) < first_staging_insert for check in (*group_checks, *mapping_checks))
    assert rpc.index("jsonb_typeof(unmap_value) <> 'string'") < first_staging_insert


def test_embedding_validator_guards_type_before_array_and_numeric_operations():
    sql = (Path(__file__).parents[1] / "supabase" / "schema.sql").read_text().lower()
    helper = sql.split("create or replace function public.valid_review_embedding(value jsonb)", 1)[1]
    helper = helper.split("create or replace function public.review_lookup_key", 1)[0]
    assert "case" in helper
    assert "when jsonb_typeof(value) <> 'array' then false" in helper
    assert "when jsonb_array_length(value) <> 384 then false" in helper
    assert "when jsonb_typeof(item) <> 'number' then true" in helper
    assert "abs((item #>> '{}')::numeric) > 3.402823466e38" in helper
