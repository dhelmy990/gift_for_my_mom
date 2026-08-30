# Simple Company Alias Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the FastEmbed/RAG group-review system with deterministic cleanup, one-table Supabase alias persistence, RapidFuzz spelling suggestions, and a compact editable Streamlit mapping table.

**Architecture:** Perform a functional rollback rather than resetting Git history: retain the current cleaner and unrelated PDF/parser fixes, then replace the RAG/group modules with focused alias repository, matching, service, and UI modules. Exact Supabase aliases resolve names authoritatively; RapidFuzz only proposes unsaved corrections; aggregation uses the resolved final string.

**Tech Stack:** Python 3, Streamlit 1.55, pandas 2.3, RapidFuzz 3.14, Supabase Python 2.28, PostgreSQL, pytest 9, Streamlit AppTest

## Global Constraints

- Supabase must contain exactly one application table used by the new runtime: `public.company_aliases`.
- The application must not use FastEmbed, embeddings, vector search, RAG, persistent groups, a submission ledger, or the former group-review board.
- RapidFuzz suggestions use a threshold of `90.0` and must never be applied or saved without explicit user confirmation.
- Exact aliases always override fuzzy suggestions.
- Canonical names are stored exactly as entered after trimming surrounding whitespace.
- Permanent writes require the existing administrator password.
- Supabase service-role credentials remain server-side in Streamlit secrets.
- Supabase failure must not prevent PDF extraction, deterministic cleanup, or viewing cleaned totals.
- Do not drop legacy Supabase objects automatically; stop using them and document optional manual cleanup separately.
- Preserve deterministic stripping, including `COMPASS TRAVEL & TOUR PTE LTD` becoming `COMPASS TRAVEL & TOUR`.
- Preserve unrelated PDF extraction, malformed-block reporting, floating-point aggregation, and source-row diagnostics.
- The 24 rows in `company_name_normalization_finetuning.csv` must resolve to their exact `target_text` after seeding.
- Use test-driven development for every behavior change and commit after every task.

---

## File Structure

The completed implementation has these focused units:

- `company_names/cleaning.py` — deterministic cleanup and normalized alias keys; retain rather than rewrite.
- `company_names/aliases.py` — alias value objects and pure RapidFuzz suggestion selection.
- `company_names/repository.py` — one-table repository protocol and Supabase implementation only.
- `company_names/service.py` — extracted-row normalization, alias resolution, persistence orchestration, and final aggregation.
- `company_names/ui.py` — compact mapping editor and save/retry behavior.
- `app.py` — PDF extraction and Streamlit page orchestration.
- `scripts/seed_name_aliases.py` — repeatable CSV-to-Supabase upsert command.
- `supabase/schema.sql` — one-table schema, trigger, RLS, and service-role grants.
- `tests/fixtures/simple_alias_app.py` — runtime Streamlit fixture.
- `tests/test_aliases.py`, `tests/test_repository.py`, `tests/test_service.py`, `tests/test_ui.py`, `tests/test_streamlit_smoke.py`, `tests/test_seed_name_aliases.py` — focused replacement test suite.

Delete obsolete runtime modules after their callers have been migrated:

- `company_names/csv_safety.py`
- `company_names/matching.py`
- `company_names/models.py`
- `company_names/review.py`
- `company_names/review_session.py`
- `scripts/seed_name_mappings.py`
- `tests/fixtures/singleton_review_app.py`
- the old group/review/submission test files superseded by the focused tests above.

Keep the historical specs and plans as decision history. Do not rewrite or delete them.

---

### Task 1: Freeze the Cleanup Contract and Seed Corpus

**Files:**
- Retain: `company_names/cleaning.py`
- Modify: `tests/test_cleaning.py`
- Create: `tests/fixtures/company_name_aliases.csv`
- Test: `tests/test_cleaning.py`

**Interfaces:**
- Consumes: `clean_company_name(raw_name: str) -> str` and `normalize_lookup_key(name: str) -> str` from `company_names.cleaning`.
- Produces: a committed 24-row test fixture with columns `input_text,target_text,remarks`; a locked regression contract used by Tasks 3 and 6.

- [ ] **Step 1: Copy the session CSV into a committed test fixture without changing its contents**

Run:

```bash
cp company_name_normalization_finetuning.csv tests/fixtures/company_name_aliases.csv
cmp company_name_normalization_finetuning.csv tests/fixtures/company_name_aliases.csv
```

Expected: `cmp` exits `0`. Leave the original untracked root file untouched.

- [ ] **Step 2: Add the failing corpus-characterization test**

Add to `tests/test_cleaning.py`:

```python
import csv
from pathlib import Path


SEED_FIXTURE = Path("tests/fixtures/company_name_aliases.csv")


def test_seed_fixture_has_the_approved_24_alias_pairs() -> None:
    with SEED_FIXTURE.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 24
    assert list(rows[0]) == ["input_text", "target_text", "remarks"]
    assert (rows[0]["input_text"], rows[0]["target_text"]) == (
        "HOTELBEDS101",
        "HOTELBEDS",
    )
    assert any(
        row["input_text"] == "HKTRM"
        and row["target_text"] == "Hong Kong TUYI Business Travel Limited"
        for row in rows
    )


def test_cleanup_preserves_compass_and_strips_its_legal_suffix() -> None:
    assert clean_company_name("COMPASS TRAVEL & TOUR PTE LTD") == (
        "COMPASS TRAVEL & TOUR"
    )
```

- [ ] **Step 3: Run the cleanup tests**

Run: `pytest tests/test_cleaning.py -v`

Expected: PASS. If the corpus count or regression fails, stop; do not modify cleaning rules merely to force alias translations through the stripper.

- [ ] **Step 4: Record the current stripping limitation explicitly**

Add this test to `tests/test_cleaning.py`:

```python
def test_business_aliases_are_not_inferred_by_deterministic_cleanup() -> None:
    assert clean_company_name("HKTRM") == "HKTRM"
    assert clean_company_name("MTLVintners Place") == "MTLVintners Place"
```

Run: `pytest tests/test_cleaning.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the frozen cleanup and corpus**

```bash
git add tests/test_cleaning.py tests/fixtures/company_name_aliases.csv
git commit -m "test: freeze company alias cleanup corpus"
```

---

### Task 2: Replace the Supabase Schema and Repository with One Alias Table

**Files:**
- Replace: `company_names/repository.py`
- Replace: `supabase/schema.sql`
- Replace: `tests/test_repository.py`
- Test: `tests/test_repository.py`

**Interfaces:**
- Consumes: `normalize_lookup_key(name: str) -> str` from `company_names.cleaning`.
- Produces:
  - `AliasMapping(cleaned_alias: str, alias_key: str, canonical_name: str)`.
  - `AliasRepository.list_aliases() -> list[AliasMapping]`.
  - `AliasRepository.upsert_aliases(mappings: list[AliasMapping]) -> None`.
  - `SupabaseAliasRepository.from_credentials(url: str, service_key: str) -> SupabaseAliasRepository`.
  - `RepositoryUnavailableError` with a safe user-readable message.

- [ ] **Step 1: Replace repository tests with a failing one-table contract**

Create a small recording client in `tests/test_repository.py` and assert the exact query boundary:

```python
class Response:
    data: list[dict[str, str]] = []


class RecordingQuery:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.response = Response()

    def table(self, name: str) -> "RecordingQuery":
        self.calls.append(("table", name))
        return self

    def select(self, columns: str) -> "RecordingQuery":
        self.calls.append(("select", columns))
        return self

    def order(self, column: str) -> "RecordingQuery":
        self.calls.append(("order", column))
        return self

    def upsert(
        self, rows: list[dict[str, str]], on_conflict: str
    ) -> "RecordingQuery":
        self.calls.append(("upsert", rows, on_conflict))
        return self

    def execute(self) -> Response:
        self.calls.append(("execute",))
        return self.response


@pytest.fixture
def recording_client() -> RecordingQuery:
    return RecordingQuery()


from company_names.repository import AliasMapping, SupabaseAliasRepository


def test_list_aliases_reads_only_the_alias_table(recording_client) -> None:
    recording_client.response.data = [
        {
            "cleaned_alias": "HKTRM",
            "alias_key": "hktrm",
            "canonical_name": "Hong Kong TUYI Business Travel Limited",
        }
    ]

    result = SupabaseAliasRepository(recording_client).list_aliases()

    assert result == [
        AliasMapping(
            "HKTRM",
            "hktrm",
            "Hong Kong TUYI Business Travel Limited",
        )
    ]
    assert recording_client.calls == [
        ("table", "company_aliases"),
        ("select", "cleaned_alias,alias_key,canonical_name"),
        ("order", "alias_key"),
        ("execute",),
    ]


def test_upsert_aliases_uses_alias_key_conflict(recording_client) -> None:
    repository = SupabaseAliasRepository(recording_client)

    repository.upsert_aliases(
        [AliasMapping("HKTRM", "hktrm", "Hong Kong TUYI Business Travel Limited")]
    )

    assert ("upsert", [
        {
            "cleaned_alias": "HKTRM",
            "alias_key": "hktrm",
            "canonical_name": "Hong Kong TUYI Business Travel Limited",
        }
    ], "alias_key") in recording_client.calls
```

- [ ] **Step 2: Run repository tests and verify the obsolete API fails the contract**

Run: `pytest tests/test_repository.py -v`

Expected: FAIL because `AliasMapping` and `SupabaseAliasRepository` do not yet exist.

- [ ] **Step 3: Implement the minimal repository**

Replace `company_names/repository.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

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
    def __init__(self, client: object) -> None:
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
            raise RepositoryUnavailableError(f"Could not create Supabase client: {error}") from error

    def list_aliases(self) -> list[AliasMapping]:
        try:
            response = (
                self._client.table("company_aliases")
                .select("cleaned_alias,alias_key,canonical_name")
                .order("alias_key")
                .execute()
            )
            return [AliasMapping(**row) for row in (response.data or [])]
        except Exception as error:
            raise RepositoryUnavailableError(f"Could not read company aliases: {error}") from error

    def upsert_aliases(self, mappings: list[AliasMapping]) -> None:
        if not mappings:
            return
        rows = [
            {
                "cleaned_alias": item.cleaned_alias,
                "alias_key": item.alias_key,
                "canonical_name": item.canonical_name,
            }
            for item in mappings
        ]
        try:
            self._client.table("company_aliases").upsert(
                rows, on_conflict="alias_key"
            ).execute()
        except Exception as error:
            raise RepositoryUnavailableError(f"Could not save company aliases: {error}") from error
```

The implementation may factor response validation into a private helper, but it must not add group or vector APIs.

- [ ] **Step 4: Replace the schema with the one-table migration**

Replace `supabase/schema.sql` with idempotent SQL equivalent to:

```sql
create table if not exists public.company_aliases (
  alias_key text primary key check (btrim(alias_key) <> ''),
  cleaned_alias text not null check (btrim(cleaned_alias) <> ''),
  canonical_name text not null check (btrim(canonical_name) <> ''),
  updated_at timestamptz not null default now()
);

alter table public.company_aliases enable row level security;
revoke all on table public.company_aliases from public, anon, authenticated;
grant select, insert, update on table public.company_aliases to service_role;

create or replace function public.set_company_alias_updated_at()
returns trigger language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists company_aliases_set_updated_at on public.company_aliases;
create trigger company_aliases_set_updated_at
before update on public.company_aliases
for each row execute function public.set_company_alias_updated_at();

revoke all on function public.set_company_alias_updated_at()
from public, anon, authenticated;
```

Do not include `create extension vector`, legacy table drops, or legacy RPC definitions.

- [ ] **Step 5: Run repository and schema assertions**

Run:

```bash
pytest tests/test_repository.py -v
! rg -n "name_groups|name_mappings|submission_ledger|vector\(" supabase/schema.sql company_names/repository.py
```

Expected: repository tests PASS and `rg` finds no obsolete runtime schema terms.

- [ ] **Step 6: Commit the one-table persistence boundary**

```bash
git add company_names/repository.py supabase/schema.sql tests/test_repository.py
git commit -m "refactor: reduce name persistence to one alias table"
```

---

### Task 3: Implement Pure Spelling Suggestions

**Files:**
- Create: `company_names/aliases.py`
- Create: `tests/test_aliases.py`
- Test: `tests/test_aliases.py`

**Interfaces:**
- Consumes: `AliasMapping` from `company_names.repository` and `normalize_lookup_key` from `company_names.cleaning`.
- Produces:
  - `FUZZY_THRESHOLD = 90.0`.
  - `AliasSuggestion(saved_alias: str, canonical_name: str, score: float)`.
  - `suggest_alias(cleaned_name: str, aliases: list[AliasMapping], threshold: float = FUZZY_THRESHOLD) -> AliasSuggestion | None`.

- [ ] **Step 1: Write failing tests for high scores, low scores, and ties**

Create `tests/test_aliases.py`:

```python
from company_names.aliases import FUZZY_THRESHOLD, AliasSuggestion, suggest_alias
from company_names.repository import AliasMapping


HKTRM = AliasMapping(
    cleaned_alias="HKTRM",
    alias_key="hktrm",
    canonical_name="Hong Kong TUYI Business Travel Limited",
)


def test_close_spelling_variant_suggests_saved_destination() -> None:
    suggestion = suggest_alias("HKTRMs", [HKTRM])

    assert suggestion is not None
    assert suggestion.saved_alias == "HKTRM"
    assert suggestion.canonical_name == "Hong Kong TUYI Business Travel Limited"
    assert suggestion.score >= FUZZY_THRESHOLD


def test_low_similarity_is_hidden() -> None:
    assert suggest_alias("Miki Travel", [HKTRM]) is None


def test_equal_best_scores_are_left_unresolved() -> None:
    aliases = [
        AliasMapping("HKTRM A", "hktrm a", "Company A"),
        AliasMapping("HKTRM B", "hktrm b", "Company B"),
    ]

    assert suggest_alias("HKTRM C", aliases, threshold=80.0) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_aliases.py -v`

Expected: FAIL because `company_names.aliases` does not exist.

- [ ] **Step 3: Implement deterministic best-only matching**

Create `company_names/aliases.py`:

```python
from dataclasses import dataclass

from rapidfuzz.fuzz import ratio

from .cleaning import normalize_lookup_key
from .repository import AliasMapping


FUZZY_THRESHOLD = 90.0


@dataclass(frozen=True)
class AliasSuggestion:
    saved_alias: str
    canonical_name: str
    score: float


def suggest_alias(
    cleaned_name: str,
    aliases: list[AliasMapping],
    threshold: float = FUZZY_THRESHOLD,
) -> AliasSuggestion | None:
    query = normalize_lookup_key(cleaned_name)
    scored = [(float(ratio(query, item.alias_key)), item) for item in aliases]
    eligible = [(score, item) for score, item in scored if score >= threshold]
    if not eligible:
        return None
    best_score = max(score for score, _ in eligible)
    winners = [item for score, item in eligible if score == best_score]
    if len(winners) != 1:
        return None
    winner = winners[0]
    return AliasSuggestion(winner.cleaned_alias, winner.canonical_name, best_score)
```

- [ ] **Step 4: Run alias tests**

Run: `pytest tests/test_aliases.py -v`

Expected: all tests PASS. Confirm the reported `HKTRMs` score is at least `90.0`; do not lower the global threshold to make the test pass.

- [ ] **Step 5: Commit pure spelling suggestions**

```bash
git add company_names/aliases.py tests/test_aliases.py
git commit -m "feat: suggest aliases by spelling similarity"
```

---

### Task 4: Replace Group Review Services with Alias Resolution and Aggregation

**Files:**
- Replace: `company_names/service.py`
- Replace: `tests/test_service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `AliasRepository`, `AliasMapping`, `AliasSuggestion`, `suggest_alias`, `clean_company_name`, and `normalize_lookup_key`.
- Produces:
  - `ServiceValidationError(ValueError)`.
  - `AliasReviewRow(cleaned_name: str, final_name: str, status: Literal["saved", "suggested", "new"], suggestion: AliasSuggestion | None)`.
  - `PreparedAliases(rows: pd.DataFrame, review_rows: list[AliasReviewRow], database_available: bool, database_error: str | None)`.
  - `normalize_extracted_rows(rows: pd.DataFrame) -> pd.DataFrame` retained with its present diagnostics.
  - `prepare_aliases(rows: pd.DataFrame, repository: AliasRepository | None) -> PreparedAliases`.
  - `save_alias_changes(prepared: PreparedAliases, final_names: dict[str, str], repository: AliasRepository) -> pd.DataFrame`.
  - `aggregate_resolved_rows(rows: pd.DataFrame, final_names: dict[str, str]) -> pd.DataFrame`.
  - `password_matches(candidate: object, expected: object) -> bool` retained.

- [ ] **Step 1: Preserve normalization tests and replace group-specific service tests**

Keep the existing tests for invalid names, source filenames, finite numeric values, duplicate cleaned names, and floating totals. Add:

```python
class FakeAliasRepository:
    def __init__(self, aliases: list[AliasMapping]) -> None:
        self.aliases = aliases
        self.saved: list[AliasMapping] = []

    def list_aliases(self) -> list[AliasMapping]:
        return list(self.aliases)

    def upsert_aliases(self, mappings: list[AliasMapping]) -> None:
        self.saved.extend(mappings)


class FailingAliasRepository(FakeAliasRepository):
    def __init__(self, message: str) -> None:
        super().__init__([])
        self.message = message

    def list_aliases(self) -> list[AliasMapping]:
        raise RepositoryUnavailableError(self.message)


def extracted_rows(values: list[tuple[str, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(values, columns=[
        "TRAVEL AGENT", "Sum of RNS", "Sum of R REVENUE"
    ])


def test_exact_alias_is_authoritative() -> None:
    rows = extracted_rows([("HKTRM", 2, 100)])
    repository = FakeAliasRepository([
        AliasMapping("HKTRM", "hktrm", "Hong Kong TUYI Business Travel Limited")
    ])

    prepared = prepare_aliases(rows, repository)

    assert prepared.review_rows == [
        AliasReviewRow(
            "HKTRM",
            "Hong Kong TUYI Business Travel Limited",
            "saved",
            None,
        )
    ]


def test_unknown_name_defaults_to_cleaned_name_with_suggestion() -> None:
    rows = extracted_rows([("HKTRMs Pte Ltd", 2, 100)])
    repository = FakeAliasRepository([
        AliasMapping("HKTRM", "hktrm", "Hong Kong TUYI Business Travel Limited")
    ])

    prepared = prepare_aliases(rows, repository)

    assert prepared.review_rows[0].cleaned_name == "HKTRMs"
    assert prepared.review_rows[0].final_name == "HKTRMs"
    assert prepared.review_rows[0].status == "suggested"
    assert prepared.review_rows[0].suggestion.canonical_name == (
        "Hong Kong TUYI Business Travel Limited"
    )


def test_database_failure_keeps_cleaned_rows_available() -> None:
    rows = extracted_rows([("Miki Travel Pte Ltd", 2, 100)])
    repository = FailingAliasRepository("table missing")

    prepared = prepare_aliases(rows, repository)

    assert prepared.database_available is False
    assert prepared.database_error == "table missing"
    assert prepared.review_rows[0].final_name == "Miki Travel"
```

- [ ] **Step 2: Add failing aggregation and save tests**

```python
def test_resolved_names_combine_and_sum() -> None:
    rows = pd.DataFrame([
        {"cleaned_name": "HKTRM", "rns": 2.0, "revenue": 100.0},
        {"cleaned_name": "HKTRMs", "rns": 3.5, "revenue": 50.25},
    ])

    result = aggregate_resolved_rows(rows, {
        "HKTRM": "Hong Kong TUYI Business Travel Limited",
        "HKTRMs": "Hong Kong TUYI Business Travel Limited",
    })

    assert result.to_dict("records") == [{
        "TRAVEL AGENT": "Hong Kong TUYI Business Travel Limited",
        "Sum of RNS": 5.5,
        "Sum of R REVENUE": 150.25,
    }]


def test_save_trims_titles_upserts_aliases_and_returns_updated_totals() -> None:
    prepared = prepare_aliases(extracted_rows([("HKTRMs", 2, 100)]), None)
    repository = FakeAliasRepository([])

    result = save_alias_changes(
        prepared,
        {"HKTRMs": "  Hong Kong TUYI Business Travel Limited  "},
        repository,
    )

    assert repository.saved == [AliasMapping(
        "HKTRMs", "hktrms", "Hong Kong TUYI Business Travel Limited"
    )]
    assert result["TRAVEL AGENT"].tolist() == [
        "Hong Kong TUYI Business Travel Limited"
    ]


def test_empty_final_name_is_rejected_before_write() -> None:
    prepared = prepare_aliases(extracted_rows([("HKTRM", 2, 100)]), None)
    repository = FakeAliasRepository([])

    with pytest.raises(ServiceValidationError, match="final company name"):
        save_alias_changes(prepared, {"HKTRM": "   "}, repository)

    assert repository.saved == []
```

- [ ] **Step 3: Run the service tests and verify failure**

Run: `pytest tests/test_service.py -v`

Expected: FAIL because the alias-service types and functions do not exist.

- [ ] **Step 4: Implement the focused service while retaining normalization diagnostics**

Replace the group preparation/submission/backup code in `company_names/service.py`. Retain the current implementation of `normalize_extracted_rows`, including `_source_file` diagnostics and finite-number checks. Implement exact resolution by indexing `repository.list_aliases()` on `alias_key`; call `suggest_alias` only for unknown keys; catch only `RepositoryUnavailableError` at the database boundary and return a cleaned fallback review.

Use this aggregation shape:

```python
def aggregate_resolved_rows(
    rows: pd.DataFrame, final_names: dict[str, str]
) -> pd.DataFrame:
    resolved = rows.copy()
    resolved["final_name"] = resolved["cleaned_name"].map(final_names)
    if resolved["final_name"].isna().any():
        raise ServiceValidationError("Every cleaned company name needs a final company name")
    grouped = (
        resolved.groupby("final_name", as_index=False, sort=False)[["rns", "revenue"]]
        .sum()
        .rename(columns={
            "final_name": "TRAVEL AGENT",
            "rns": "Sum of RNS",
            "revenue": "Sum of R REVENUE",
        })
        .sort_values("Sum of R REVENUE", ascending=False, kind="stable")
        .reset_index(drop=True)
    )
    return grouped
```

Do not retain embedding, group, board, ledger, backup, UUID, or submission-fingerprint imports.

- [ ] **Step 5: Run the focused domain suite**

Run:

```bash
pytest tests/test_cleaning.py tests/test_aliases.py tests/test_repository.py tests/test_service.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit alias resolution and aggregation**

```bash
git add company_names/service.py tests/test_service.py
git commit -m "refactor: resolve report names through aliases"
```

---

### Task 5: Replace the Seed Tool and Prove All 24 CSV Mappings

**Files:**
- Delete: `scripts/seed_name_mappings.py`
- Create: `scripts/seed_name_aliases.py`
- Delete: `tests/test_seed_name_mappings.py`
- Create: `tests/test_seed_name_aliases.py`
- Test: `tests/test_seed_name_aliases.py`

**Interfaces:**
- Consumes: `clean_company_name`, `normalize_lookup_key`, `AliasMapping`, and `AliasRepository.upsert_aliases`.
- Produces:
  - `load_alias_rows(path: Path) -> list[AliasMapping]`.
  - `seed_aliases(path: Path, repository: AliasRepository) -> int`.
  - CLI arguments `--csv`, `--supabase-url`, and `--supabase-service-key`, with environment fallbacks `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`.

- [ ] **Step 1: Write failing parsing, idempotency, and corpus-resolution tests**

Create `tests/test_seed_name_aliases.py` with:

```python
import csv
from pathlib import Path

from company_names.cleaning import clean_company_name, normalize_lookup_key
from company_names.repository import AliasMapping
from scripts.seed_name_aliases import load_alias_rows, seed_aliases


FIXTURE = Path("tests/fixtures/company_name_aliases.csv")


class FakeAliasRepository:
    def __init__(self) -> None:
        self.calls: list[list[AliasMapping]] = []

    def list_aliases(self) -> list[AliasMapping]:
        return []

    def upsert_aliases(self, mappings: list[AliasMapping]) -> None:
        self.calls.append(list(mappings))


def test_loads_all_24_pairs_as_alias_mappings() -> None:
    mappings = load_alias_rows(FIXTURE)

    assert len(mappings) == 24
    hktrm = next(item for item in mappings if item.cleaned_alias == "HKTRM")
    assert hktrm.alias_key == "hktrm"
    assert hktrm.canonical_name == "Hong Kong TUYI Business Travel Limited"


def test_seed_is_one_repeatable_upsert() -> None:
    repository = FakeAliasRepository()

    first_count = seed_aliases(FIXTURE, repository)
    second_count = seed_aliases(FIXTURE, repository)

    assert first_count == second_count == 24
    assert len(repository.calls) == 2
    assert repository.calls[0] == repository.calls[1]


def test_all_fixture_inputs_resolve_to_exact_targets_after_seed() -> None:
    mappings = load_alias_rows(FIXTURE)
    exact = {item.alias_key: item.canonical_name for item in mappings}

    with FIXTURE.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    assert [
        exact[normalize_lookup_key(clean_company_name(row["input_text"]))]
        for row in rows
    ] == [row["target_text"].strip() for row in rows]
```

Add a malformed-row test asserting the error contains the one-based CSV row number and the invalid value.

- [ ] **Step 2: Run the seed tests and verify failure**

Run: `pytest tests/test_seed_name_aliases.py -v`

Expected: FAIL because the replacement script does not exist.

- [ ] **Step 3: Implement the repeatable seed script**

Read with `csv.DictReader(..., encoding="utf-8-sig")`, require the exact headers, clean `input_text`, trim `target_text`, reject empty values, reject two rows that normalize to one alias key but disagree on canonical name, and call `repository.upsert_aliases(mappings)` once.

The CLI must construct `SupabaseAliasRepository.from_credentials`, call `seed_aliases`, print only the imported count, and return a nonzero exit code with a safe message on validation or repository failure.

- [ ] **Step 4: Run the seed and cleanup suites**

Run:

```bash
pytest tests/test_seed_name_aliases.py tests/test_cleaning.py -v
```

Expected: all tests PASS, including exact resolution for all 24 rows.

- [ ] **Step 5: Commit the replacement seed path**

```bash
git add scripts/seed_name_aliases.py tests/test_seed_name_aliases.py
git rm scripts/seed_name_mappings.py tests/test_seed_name_mappings.py
git commit -m "refactor: seed the simple company alias table"
```

---

### Task 6: Replace the Group Board with a Compact Mapping Editor

**Files:**
- Replace: `company_names/ui.py`
- Create: `tests/test_ui.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `PreparedAliases`, `AliasReviewRow`, `AliasRepository`, `save_alias_changes`, and `password_matches`.
- Produces:
  - `visible_review_rows(rows: list[AliasReviewRow], query: str) -> list[AliasReviewRow]`.
  - `edited_final_names(rows: list[AliasReviewRow], edits: dict[str, str]) -> dict[str, str]`.
  - `validate_save_password(candidate: object, configured: object) -> str | None`, returning an error message or `None`.
  - `render_alias_editor(prepared: PreparedAliases, repository: AliasRepository | None, configured_admin_password: str | None) -> pd.DataFrame | None`.

- [ ] **Step 1: Write source-level and state-helper tests for the simplified interface**

Create `tests/test_ui.py`:

```python
from pathlib import Path

from company_names.aliases import AliasSuggestion
from company_names.service import AliasReviewRow
from company_names.ui import (
    edited_final_names,
    validate_save_password,
    visible_review_rows,
)


ROWS = [
    AliasReviewRow("HKTRM", "Hong Kong TUYI Business Travel", "saved", None),
    AliasReviewRow(
        "HKTRMs",
        "HKTRMs",
        "suggested",
        AliasSuggestion("HKTRM", "Hong Kong TUYI Business Travel", 90.91),
    ),
    AliasReviewRow("Miki Travel", "Miki Travel", "new", None),
]


def test_ui_contains_plain_mapping_copy_and_no_group_board_copy() -> None:
    source = Path("company_names/ui.py").read_text()

    assert "Company name mappings" in source
    assert "Save all changes and update totals" in source
    assert "Suggested from" in source
    assert "Working tray" not in source
    assert "Combined groups" not in source
    assert "Prepare mapping backup" not in source


def test_search_filters_current_report_rows_case_insensitively() -> None:
    assert [row.cleaned_name for row in visible_review_rows(
        ROWS, "hktr"
    )] == ["HKTRM", "HKTRMs"]


def test_suggestion_is_not_applied_until_explicit_accept() -> None:
    values = edited_final_names(ROWS, {})

    assert values["HKTRMs"] == "HKTRMs"


def test_save_password_validation_is_explicit() -> None:
    assert validate_save_password("wrong", "correct") == "Incorrect admin password"
    assert validate_save_password("correct", "correct") is None
```

- [ ] **Step 2: Run UI tests and verify failure**

Run: `pytest tests/test_ui.py -v`

Expected: FAIL because the old group-board UI has no alias editor helpers.

- [ ] **Step 3: Implement the compact editor**

Replace `company_names/ui.py` with a renderer that:

1. Shows `Company name mappings` and a search input.
2. Iterates only filtered `prepared.review_rows`.
3. Displays the cleaned report name, editable final-name text input, and `Saved`, `Suggested`, or `New` status.
4. For suggestions, displays `Suggested from {saved_alias} ({score:.0f}%): {canonical_name}` and an explicit `Use this suggestion` button.
5. Stores edits in `st.session_state` under `alias_final_{alias_key}`; suggestion buttons use `accept_alias_{alias_key}`; the password uses `alias_admin_password`; and the save button uses `save_aliases`.
6. Does not write when a suggestion button is clicked; it only changes the editable final-name value.
7. Shows the admin-password input beside `Save all changes and update totals`.
8. Disables saves when `prepared.database_available` is false, the repository is absent, or the configured admin password is absent.
9. On a valid password, calls `save_alias_changes`; on `RepositoryUnavailableError`, shows its safe message and retains edits for retry.
10. Returns the updated aggregate only after a successful save.

Use ordinary Streamlit widgets. Do not use injected HTML/CSS, draggable components, pills, or browser-side JavaScript.

- [ ] **Step 4: Verify password validation stays at the write boundary**

Run: `pytest tests/test_ui.py::test_save_password_validation_is_explicit -v`

Expected: PASS. Inspect `render_alias_editor` and confirm it calls
`validate_save_password` immediately before `save_alias_changes`; it must not call the
repository from any suggestion or text-input callback. Task 7 adds runtime AppTest
coverage once the complete fixture exists.

- [ ] **Step 5: Run UI and service tests**

Run:

```bash
pytest tests/test_ui.py tests/test_service.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit the simplified UI**

```bash
git add company_names/ui.py tests/test_ui.py
git commit -m "refactor: replace group board with alias editor"
```

---

### Task 7: Rewire Streamlit and Remove the RAG/Group Runtime

**Files:**
- Modify: `app.py`
- Modify: `company_names/__init__.py`
- Retain: `plumber.py`
- Replace: `tests/test_app_source.py`
- Replace: `tests/fixtures/singleton_review_app.py` with `tests/fixtures/simple_alias_app.py`
- Replace: `tests/test_streamlit_smoke.py`
- Delete: `company_names/csv_safety.py`
- Delete: `company_names/matching.py`
- Delete: `company_names/models.py`
- Delete: `company_names/review.py`
- Delete: `company_names/review_session.py`
- Delete obsolete tests: `tests/test_csv_safety.py`, `tests/test_matching.py`, `tests/test_review.py`, `tests/test_review_session.py`, `tests/test_submission.py`, `tests/test_ui_state.py`
- Test: `tests/test_app_source.py`, `tests/test_streamlit_smoke.py`, `tests/test_plumber.py`

**Interfaces:**
- Consumes: `SupabaseAliasRepository`, `prepare_aliases`, `aggregate_resolved_rows`, and `render_alias_editor`.
- Produces: a Streamlit collation flow that works with or without Supabase and has no import path to the old runtime.

- [ ] **Step 1: Write failing source-boundary tests**

Replace `tests/test_app_source.py` with:

```python
from pathlib import Path


def test_app_uses_alias_pipeline_without_embeddings_or_groups() -> None:
    source = Path("app.py").read_text()

    assert "SupabaseAliasRepository" in source
    assert "prepare_aliases" in source
    assert "render_alias_editor" in source
    assert "FastEmbeddingProvider" not in source
    assert "render_name_review" not in source
    assert "ReviewBoard" not in source


def test_runtime_has_no_obsolete_module_imports() -> None:
    runtime = "\n".join(
        path.read_text()
        for path in [Path("app.py"), *Path("company_names").glob("*.py")]
    )
    for obsolete in (
        "fastembed",
        "streamlit_sortables",
        "name_groups",
        "name_mappings",
        "submission_ledger",
    ):
        assert obsolete not in runtime
```

- [ ] **Step 2: Run source tests and verify failure**

Run: `pytest tests/test_app_source.py -v`

Expected: FAIL because `app.py` still imports the embedding/group stack.

- [ ] **Step 3: Rewire collation preparation in `app.py`**

Keep `_extract_collation`, `_process_extractor`, exclusion controls, upload handling, parse warnings, and temporary-file cleanup. Replace `_prepare_collation_review` with an alias preparation boundary:

```python
def _prepare_collation_aliases(frames: list[pd.DataFrame]) -> PreparedAliases:
    rows = pd.concat(frames, ignore_index=True)
    url = _secret("SUPABASE_URL")
    service_key = _secret("SUPABASE_SERVICE_KEY")
    if not url or not service_key:
        return prepare_aliases(rows, None)
    try:
        repository = get_alias_repository(url, service_key)
    except RepositoryUnavailableError as error:
        prepared = prepare_aliases(rows, None)
        prepared.database_error = str(error)
        return prepared
    return prepare_aliases(rows, repository)
```

Store the `PreparedAliases` object in session state only for the current upload fingerprint. Display its cleaned aggregate immediately. If the database is available, render the alias editor below it; replace the displayed result after a successful save.

Do not make database configuration a prerequisite for processing reports.

- [ ] **Step 4: Create a runtime Streamlit smoke fixture**

Create `tests/fixtures/simple_alias_app.py` with two rows (`HKTRM`, `HKTRMs`), an in-memory alias repository containing only `HKTRM`, and configured admin password `correct`. Render `render_alias_editor` directly.

The fixture repository must expose its saved rows through
`st.session_state["fixture_repository"]`. Add a test-only `Fail next save` checkbox
with key `fixture_fail_next_save`; when selected, the repository's next
`upsert_aliases` call raises `RepositoryUnavailableError("network unavailable")`.

Replace the smoke test with AppTest assertions:

```python
def test_simple_alias_editor_renders_without_exception() -> None:
    app = AppTest.from_file("tests/fixtures/simple_alias_app.py").run()

    assert not app.exception
    assert "Company name mappings" in [item.value for item in app.subheader]
    assert any("HKTRMs" in item.value for item in app.markdown)
    assert any("Suggested from HKTRM" in item.value for item in app.caption)
```

Add a second AppTest flow that accepts the suggestion, enters the correct password, saves, and observes one aggregated canonical row.

Add these runtime boundary tests using the stable widget keys from Task 6:

```python
def test_wrong_password_does_not_save_aliases() -> None:
    app = AppTest.from_file("tests/fixtures/simple_alias_app.py").run()
    app.text_input(key="alias_final_hktrms").input(
        "Hong Kong TUYI Business Travel Limited"
    )
    app.text_input(key="alias_admin_password").input("wrong")
    app.button(key="save_aliases").click().run()

    assert app.session_state["fixture_repository"].saved == []
    assert any("Incorrect admin password" in item.value for item in app.error)


def test_failed_save_retains_typed_final_name_for_retry() -> None:
    app = AppTest.from_file("tests/fixtures/simple_alias_app.py").run()
    desired = "Hong Kong TUYI Business Travel Limited"
    app.text_input(key="alias_final_hktrms").input(desired)
    app.text_input(key="alias_admin_password").input("correct")
    app.checkbox(key="fixture_fail_next_save").check()
    app.button(key="save_aliases").click().run()

    assert app.text_input(key="alias_final_hktrms").value == desired
    assert any("network unavailable" in item.value for item in app.error)
```

- [ ] **Step 5: Delete the obsolete runtime and superseded tests**

Run:

```bash
git rm company_names/csv_safety.py company_names/matching.py company_names/models.py company_names/review.py company_names/review_session.py
git rm tests/test_csv_safety.py tests/test_matching.py tests/test_review.py tests/test_review_session.py tests/test_submission.py tests/test_ui_state.py
git rm tests/fixtures/singleton_review_app.py
```

Do not delete `company_names/cleaning.py`, parser tests, historical docs, or the new alias modules.

- [ ] **Step 6: Remove orphaned dependencies and prove imports are clean**

Remove these exact lines from `requirements.txt` after runtime deletion:

```text
fastembed==0.7.4
streamlit-sortables==0.3.1
```

Keep:

```text
rapidfuzz==3.14.3
supabase==2.28.0
```

Run:

```bash
python3 -m compileall -q app.py company_names scripts
pytest tests/test_app_source.py tests/test_plumber.py tests/test_streamlit_smoke.py -v
! rg -n "FastEmbed|EmbeddingProvider|ReviewBoard|render_name_review|streamlit_sortables" app.py company_names scripts
```

Expected: compilation succeeds, tests PASS, and the obsolete-symbol search returns no matches.

- [ ] **Step 7: Commit the functional rollback**

```bash
git add app.py company_names requirements.txt tests
git commit -m "refactor: remove RAG company grouping workflow"
```

---

### Task 8: Update Deployment Documentation and Run End-to-End Verification

**Files:**
- Replace: `docs/SUPABASE_SETUP.md`
- Modify: `README.md`
- Modify: `.streamlit/secrets.example.toml`
- Test: full suite and seed corpus

**Interfaces:**
- Consumes: the final schema, seed CLI, Streamlit secret names, and alias-editor behavior.
- Produces: human-readable one-table setup and migration instructions.

- [ ] **Step 1: Rewrite the Supabase guide around the one-table setup**

Document these exact steps:

1. Create or open the Supabase project.
2. Run `supabase/schema.sql` in the SQL editor.
3. Verify `public.company_aliases` exists with four fields and RLS enabled.
4. Configure `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, and `ADMIN_PASSWORD` in Streamlit secrets.
5. Run the seed command locally:

```bash
python3 scripts/seed_name_aliases.py \
  --csv tests/fixtures/company_name_aliases.csv \
  --supabase-url "$SUPABASE_URL" \
  --supabase-service-key "$SUPABASE_SERVICE_KEY"
```

6. Verify the command reports `24` imported aliases.
7. Explain that the URL may end in `.co` or `.co/`; the repository normalizes the slash.
8. Explain that old vector/group tables may remain but are unused.
9. Put optional destructive cleanup SQL in a clearly marked appendix and require the user to confirm a backup before running it; do not execute it from the app.

- [ ] **Step 2: Update README and example secrets**

Describe the runtime in this order: upload PDFs, deterministic cleanup, exact alias mapping, optional spelling suggestion, password-protected save, combined totals. Remove instructions for vector extensions, FastEmbed model warmup, group backups, and drag-and-drop review.

Keep `.streamlit/secrets.example.toml` limited to:

```toml
SUPABASE_URL = "https://your-project.supabase.co"
SUPABASE_SERVICE_KEY = "your-service-role-key"
ADMIN_PASSWORD = "choose-a-strong-password"
```

- [ ] **Step 3: Run the complete test suite freshly**

Run:

```bash
pytest -v
```

Expected: all collected tests PASS with zero failures and zero errors.

- [ ] **Step 4: Verify the seed corpus and runtime dependency boundary**

Run:

```bash
pytest tests/test_cleaning.py tests/test_seed_name_aliases.py -v
python3 -m compileall -q app.py company_names scripts
! rg -n "fastembed|streamlit-sortables" requirements.txt app.py company_names scripts
! rg -n "title_embedding|member_embedding|vector_cosine_ops|submit_name_review" app.py company_names scripts supabase/schema.sql
git diff --check
git status --short
```

Expected:

- all cleanup and 24-row seed assertions PASS;
- compilation succeeds;
- no embedding, drag-board, or legacy RPC terms remain in runtime/schema files;
- no whitespace errors;
- the only permissible unrelated untracked file is the original `company_name_normalization_finetuning.csv`.

- [ ] **Step 5: Perform a local Streamlit smoke run**

Run:

```bash
streamlit run app.py --server.headless true --server.port 8501
```

Manually confirm from the startup log that the app reaches `http://localhost:8501` without import errors, then stop the process. Use the AppTest suite—not manual clicking—as the acceptance evidence for editor behavior.

- [ ] **Step 6: Commit documentation and final verification state**

```bash
git add README.md docs/SUPABASE_SETUP.md .streamlit/secrets.example.toml
git commit -m "docs: explain simple alias deployment"
```

- [ ] **Step 7: Review the complete change against the approved design**

Run:

```bash
git diff --stat 2c5b353..HEAD
git log --oneline 2c5b353..HEAD
```

Confirm every retained runtime file has one purpose, every global constraint is represented by tests or explicit searches, and no commit includes the untracked root CSV or secrets.
