# Company Name Grouping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic company-name cleanup, validated-group retrieval, a searchable draggable review board, permanent Supabase mappings, and grouped collation totals to the Streamlit app.

**Architecture:** Keep PDF extraction in `plumber.py` and introduce a focused `company_names` package for cleanup, review state, retrieval, persistence, and aggregation. Streamlit owns session orchestration and rendering; Supabase owns durable state and performs each reviewed submission through one transactional PostgreSQL RPC.

**Tech Stack:** Python 3.10+, Streamlit, pandas, pdfplumber, FastEmbed with `BAAI/bge-small-en-v1.5` (384 dimensions), RapidFuzz, Supabase Python, PostgreSQL/pgvector, streamlit-sortables, pytest.

## Global Constraints

- Previously validated mappings are the source of truth; similarity never auto-validates or overrides them.
- Show and persist only the cleaned final string, not the raw extracted name.
- Remove `Pte`, `Pte Ltd`, `Ltd`, `Limited`, `Co`, `Co Ltd`, `Co., Ltd`, `Sdn Bhd`, and `GmbH`, including erroneous text concatenated after the suffix.
- Suggestions use only previously validated permanent groups, never current unsubmitted groupings.
- Exclusions affect only the current report and are never persisted.
- Submitting automatically persists reviewed groups; removing a validated member permanently unmaps it.
- Canonical titles may be freely typed and rename an existing group globally.
- Do not implement permanent group deletion.
- Require a shared admin password for permanent writes and backup export.
- Use high-contrast black text and follow the approved search → pill tray → groups mockup closely.
- Supabase credentials and the admin password must exist only in Streamlit Secrets.

## File Structure

- `company_names/models.py`: immutable domain records and review payload types.
- `company_names/cleaning.py`: deterministic raw-name-to-final-string cleanup.
- `company_names/review.py`: pure review-board transitions and submission validation.
- `company_names/matching.py`: FastEmbed adapter and hybrid candidate ranking.
- `company_names/repository.py`: Supabase reads, vector search, RPC submission, and export.
- `company_names/service.py`: initialize review data, submit reviewed state, and aggregate rows.
- `company_names/ui.py`: Streamlit search, sortable board, title editing, authorization, and status UI.
- `supabase/schema.sql`: pgvector tables, search RPC, and atomic submission RPC.
- `scripts/seed_name_mappings.py`: validated CSV importer.
- `tests/`: unit and integration tests mirroring package responsibilities.
- `app.py`: call the new collation review flow after PDF extraction.
- `plumber.py`: return duplicate extracted rows safely instead of overwriting them by index.
- `requirements.txt`: pin runtime and test dependencies.
- `.gitignore`: exclude Streamlit secrets, brainstorming artifacts, Python caches, and office lock files.

---

### Task 1: Test Harness and Deterministic Name Cleanup

**Files:**
- Create: `company_names/__init__.py`
- Create: `company_names/cleaning.py`
- Create: `tests/test_cleaning.py`
- Modify: `requirements.txt`
- Create: `.gitignore`

**Interfaces:**
- Produces: `clean_company_name(raw_name: str) -> str`
- Produces: `normalize_lookup_key(name: str) -> str`

- [ ] **Step 1: Add pinned dependencies and safe ignore rules**

Set `requirements.txt` to include the existing PDF dependency plus explicit app dependencies:

```text
streamlit==1.55.0
pandas==2.3.3
pdfplumber==0.11.9
fastembed==0.7.4
rapidfuzz==3.14.3
supabase==2.28.0
streamlit-sortables==0.3.1
pytest==9.0.2
```

Create `.gitignore`:

```gitignore
.streamlit/secrets.toml
.superpowers/
__pycache__/
.pytest_cache/
*.py[cod]
.~lock.*#
```

- [ ] **Step 2: Write failing cleanup tests**

Create `tests/test_cleaning.py`:

```python
import pytest

from company_names.cleaning import clean_company_name, normalize_lookup_key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Kake Hotels Marketing Co.,LtdRoom", "Kake Hotels Marketing"),
        ("Miki Travel LtdVintners Place", "Miki Travel"),
        ("Within Earth Holidays Sdn BhdSuite", "Within Earth Holidays"),
        ("Betoptop GmbHBüro Kornwestheim Stammheimer Straße", "Betoptop"),
        ("Hong Thai Travel Services (S) Pte", "Hong Thai Travel Services (S)"),
        ("TRVCTravco Corporation Limited Travco House,", "TRVCTravco Corporation"),
        ("MMK SG PTE", "MMK SG"),
        ("  DNATA__Travel   Group  ", "DNATA Travel Group"),
    ],
)
def test_clean_company_name(raw: str, expected: str) -> None:
    assert clean_company_name(raw) == expected


def test_clean_company_name_rejects_empty_result() -> None:
    with pytest.raises(ValueError, match="empty"):
        clean_company_name("Pte Ltd")


def test_normalize_lookup_key_is_case_and_punctuation_insensitive() -> None:
    assert normalize_lookup_key("Kake Hotels-Marketing") == "kake hotels marketing"
```

- [ ] **Step 3: Run tests and verify the expected failure**

Run: `python3 -m pytest tests/test_cleaning.py -v`

Expected: FAIL during import because `company_names.cleaning` does not exist.

- [ ] **Step 4: Implement the minimal deterministic cleaner**

Create `company_names/__init__.py` and `company_names/cleaning.py`:

```python
import re

_SUFFIXES = (
    r"co\s*\.?,?\s*ltd",
    r"pte\s+ltd",
    r"sdn\s+bhd",
    r"limited",
    r"gmbh",
    r"ltd",
    r"pte",
    r"co",
)
_SUFFIX_AND_TRAILING = re.compile(
    rf"(?i)(?<![a-z])(?:{'|'.join(_SUFFIXES)})(?:\b|(?=[A-ZÀ-ÖØ-Þ])).*$"
)


def clean_company_name(raw_name: str) -> str:
    value = re.sub(r"[_|]+", " ", str(raw_name)).strip()
    value = re.sub(r"\s+", " ", value)
    value = _SUFFIX_AND_TRAILING.sub("", value).strip(" ,.-_")
    value = re.sub(r"\s+", " ", value)
    if not value:
        raise ValueError("company name is empty after cleanup")
    return value


def normalize_lookup_key(name: str) -> str:
    value = clean_company_name(name).casefold()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()
```

- [ ] **Step 5: Run cleanup tests**

Run: `python3 -m pytest tests/test_cleaning.py -v`

Expected: all tests PASS. If a regex case fails, change only `cleaning.py` and add the failing raw string to the parameterized table before continuing.

- [ ] **Step 6: Commit**

```bash
git add .gitignore requirements.txt company_names/__init__.py company_names/cleaning.py tests/test_cleaning.py
git commit -m "feat: add deterministic company name cleanup"
```

---

### Task 2: Review Domain Model and Aggregation

**Files:**
- Create: `company_names/models.py`
- Create: `company_names/review.py`
- Create: `tests/test_review.py`

**Interfaces:**
- Consumes: `normalize_lookup_key(name: str) -> str`
- Produces: `Group`, `NameRecord`, `ReviewBoard`, and `SubmissionPayload` dataclasses.
- Produces: `validate_board(board: ReviewBoard) -> list[str]`
- Produces: `build_submission(board: ReviewBoard, original_mappings: dict[str, str]) -> SubmissionPayload`
- Produces: `aggregate_by_group(rows: pd.DataFrame, board: ReviewBoard) -> pd.DataFrame`

- [ ] **Step 1: Write failing domain tests**

Create `tests/test_review.py` with tests that build two groups and assert:

```python
import pandas as pd

from company_names.models import Group, NameRecord, ReviewBoard
from company_names.review import aggregate_by_group, build_submission, validate_board


def sample_board() -> ReviewBoard:
    return ReviewBoard(
        groups={
            "g-dnata": Group("g-dnata", "DNATA", existing=True),
            "new-miki": Group("new-miki", "Miki Travel", existing=False),
        },
        names={
            "DNATA Travel Group": NameRecord("DNATA Travel Group", "g-dnata", "exact", selected=True),
            "DNATA_TRAVEL_GROUP": NameRecord("DNATA_TRAVEL_GROUP", "g-dnata", "suggested", selected=True),
            "MTL": NameRecord("MTL", "new-miki", "suggested", selected=True),
            "Noise": NameRecord("Noise", None, "unknown", selected=True, excluded=True),
        },
    )


def test_validate_board_requires_all_included_names_grouped() -> None:
    board = sample_board()
    board.names["MTL"].group_id = None
    assert validate_board(board) == ["MTL is included but ungrouped"]


def test_build_submission_records_permanent_unmapping() -> None:
    board = sample_board()
    board.names["Old Alias"] = NameRecord("Old Alias", None, "exact", selected=False)
    payload = build_submission(board, {"DNATA Travel Group": "g-dnata", "Old Alias": "g-dnata"})
    assert payload.unmap_names == ["Old Alias"]


def test_aggregate_by_group_sums_numeric_values() -> None:
    rows = pd.DataFrame(
        [
            {"cleaned_name": "DNATA Travel Group", "rns": 2.0, "revenue": 100.0},
            {"cleaned_name": "DNATA_TRAVEL_GROUP", "rns": 3.0, "revenue": 150.0},
            {"cleaned_name": "Noise", "rns": 99.0, "revenue": 999.0},
        ]
    )
    result = aggregate_by_group(rows, sample_board())
    assert result.to_dict("records") == [
        {"TRAVEL AGENT": "DNATA", "Sum of RNS": 5.0, "Sum of R REVENUE": 250.0}
    ]
```

Also test blank titles, duplicate normalized titles, duplicate name placement, and an empty group being permitted.

- [ ] **Step 2: Run domain tests and verify failure**

Run: `python3 -m pytest tests/test_review.py -v`

Expected: FAIL because the domain modules do not exist.

- [ ] **Step 3: Implement typed review records**

In `company_names/models.py`, define mutable `Group`, mutable `NameRecord`, `ReviewBoard`, and immutable `SubmissionPayload` dataclasses with these exact fields:

```python
@dataclass
class Group:
    id: str
    canonical_title: str
    existing: bool

@dataclass
class NameRecord:
    cleaned_name: str
    group_id: str | None
    source: Literal["exact", "suggested", "unknown"]
    selected: bool = False
    excluded: bool = False

@dataclass
class ReviewBoard:
    groups: dict[str, Group]
    names: dict[str, NameRecord]

@dataclass(frozen=True)
class SubmissionPayload:
    groups: list[dict[str, object]]
    mappings: list[dict[str, object]]
    unmap_names: list[str]
```

- [ ] **Step 4: Implement pure validation, payload construction, and aggregation**

In `company_names/review.py`:

- Return deterministic validation messages sorted by cleaned name.
- Treat empty groups as valid and omit them from the payload unless `existing=True`.
- Reject two populated groups whose titles share the same `normalize_lookup_key`.
- Treat `selected=False` as inventory, `selected=True/group_id=None/excluded=False` as the working tray, `selected=True/group_id!=None` as grouped, and `selected=True/excluded=True` as report-only exclusion.
- Calculate `unmap_names` from originally mapped records deliberately returned to inventory (`selected=False`). A report-only exclusion must not cause an unmap, and moving an existing mapping directly to a different group must produce a conflict error.
- Aggregate only non-excluded grouped names, rename output columns to the existing collation labels, and sort revenue descending.

- [ ] **Step 5: Run domain tests**

Run: `python3 -m pytest tests/test_review.py -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add company_names/models.py company_names/review.py tests/test_review.py
git commit -m "feat: model company name review state"
```

---

### Task 3: FastEmbed Adapter and Hybrid Suggestions

**Files:**
- Create: `company_names/matching.py`
- Create: `tests/test_matching.py`

**Interfaces:**
- Consumes: `normalize_lookup_key(name: str) -> str`
- Produces: `EmbeddingProvider.embed(texts: list[str]) -> list[list[float]]`
- Produces: `FastEmbeddingProvider(model_name: str = "BAAI/bge-small-en-v1.5")`
- Produces: `rank_candidates(query: str, candidates: list[Candidate], query_vector: list[float] | None, limit: int = 5) -> list[Suggestion]`

- [ ] **Step 1: Write failing ranking tests without loading a model**

Create fake 3-dimensional vectors in `tests/test_matching.py` and test:

```python
def test_exact_candidate_always_ranks_first() -> None:
    candidates = [
        Candidate("g1", "Miki Travel", "MTL", [0.0, 1.0, 0.0]),
        Candidate("g2", "MTL", "Other Alias", [1.0, 0.0, 0.0]),
    ]
    result = rank_candidates("MTL", candidates, [0.0, 1.0, 0.0])
    assert result[0].group_id == "g2"
    assert result[0].reason == "exact"


def test_acronym_supports_code_to_title_matching() -> None:
    result = rank_candidates(
        "MTL",
        [Candidate("g1", "Miki Travel Limited", "Miki Travel", None)],
        None,
    )
    assert result[0].group_id == "g1"
    assert result[0].acronym_score == 1.0
```

Also test cosine similarity, token overlap, fuzzy fallback, one best result per group, descending score order, and `limit`.

- [ ] **Step 2: Run matching tests and verify failure**

Run: `python3 -m pytest tests/test_matching.py -v`

Expected: FAIL because `company_names.matching` does not exist.

- [ ] **Step 3: Implement the provider and ranking types**

Define immutable `Candidate` and `Suggestion` dataclasses. Implement `FastEmbeddingProvider` with lazy model construction so importing the app does not download a model:

```python
class FastEmbeddingProvider:
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self.model_name = model_name
        self._model = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(model_name=self.model_name)
        vectors = [vector.tolist() for vector in self._model.embed(texts)]
        if any(len(vector) != 384 for vector in vectors):
            raise ValueError("FastEmbed returned a non-384-dimensional vector")
        return vectors
```

Use RapidFuzz `fuzz.WRatio`, normalized token Jaccard overlap, acronym equality, and cosine similarity. Exact lookup-key equality receives score `1.0` and reason `exact`. Otherwise combine available signals with weights `0.50 vector + 0.25 fuzzy + 0.15 token + 0.10 acronym`, renormalizing when the vector is absent.

- [ ] **Step 4: Run matching tests**

Run: `python3 -m pytest tests/test_matching.py -v`

Expected: all tests PASS without downloading FastEmbed weights.

- [ ] **Step 5: Add one opt-in embedding smoke test**

Mark the real model test `@pytest.mark.integration` and assert `len(provider.embed(["Miki Travel"])[0]) == 384`. Register the marker in `pytest.ini`; normal test runs exclude it with `-m "not integration"`.

- [ ] **Step 6: Commit**

```bash
git add company_names/matching.py tests/test_matching.py pytest.ini
git commit -m "feat: rank validated name group suggestions"
```

---

### Task 4: Supabase Schema and Repository

**Files:**
- Create: `supabase/schema.sql`
- Create: `company_names/repository.py`
- Create: `tests/test_repository.py`

**Interfaces:**
- Produces: `MappingRepository` protocol.
- Produces: `SupabaseMappingRepository.from_credentials(url: str, service_key: str)`.
- Produces: `list_groups()`, `get_exact_mappings(cleaned_names)`, `list_candidates()`, `submit(payload)`, and `export_rows()`.

- [ ] **Step 1: Write repository contract tests with a fake Supabase client**

Test that:

- Missing URL/key raises `RepositoryConfigurationError` without logging their values.
- `get_exact_mappings(["MTL"])` returns group ID/title/member data.
- `submit(payload)` calls `client.rpc("submit_name_review", {"payload": ...}).execute()` exactly once.
- `export_rows()` returns rows ordered by canonical title then cleaned name.
- Client exceptions become `RepositoryUnavailableError` with a safe message.

- [ ] **Step 2: Run repository tests and verify failure**

Run: `python3 -m pytest tests/test_repository.py -v`

Expected: FAIL because `company_names.repository` does not exist.

- [ ] **Step 3: Create the exact Supabase schema**

Create `supabase/schema.sql` containing:

```sql
create extension if not exists vector;
create extension if not exists pgcrypto;

create table if not exists public.name_groups (
  id uuid primary key default gen_random_uuid(),
  canonical_title text not null,
  canonical_key text not null unique,
  title_embedding vector(384),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (btrim(canonical_title) <> '')
);

create table if not exists public.name_mappings (
  cleaned_name text primary key,
  lookup_key text not null unique,
  group_id uuid not null references public.name_groups(id),
  member_embedding vector(384),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (btrim(cleaned_name) <> '')
);

alter table public.name_groups enable row level security;
alter table public.name_mappings enable row level security;
```

Add indexes on `group_id`, `canonical_key`, `lookup_key`, and HNSW cosine indexes for both vectors. Revoke all table and function access from `anon` and `authenticated`.

Define `public.submit_name_review(payload jsonb) returns jsonb`, `security invoker`, with explicit validation and deterministic conflict exceptions. Parse `groups`, resolve temporary IDs to UUIDs, update existing groups, insert new groups, upsert mappings, delete `unmap_names`, and return the resolved IDs. Because it is one PostgreSQL function call, any exception rolls back the transaction.

- [ ] **Step 4: Implement the Supabase repository**

Use `create_client(url, service_key)`. Keep all Supabase-specific response parsing in `repository.py`; return domain records to callers. Call the transaction with:

```python
self._client.rpc(
    "submit_name_review",
    {"payload": asdict(payload)},
).execute()
```

- [ ] **Step 5: Run repository tests and statically check SQL invariants**

Run: `python3 -m pytest tests/test_repository.py -v`

Run: `rg -n "vector\(384\)|enable row level security|revoke all|submit_name_review" supabase/schema.sql`

Expected: repository tests PASS and every required SQL control appears.

- [ ] **Step 6: Manually validate the schema in Supabase before app integration**

Run the complete file in the Supabase SQL Editor, then run it a second time to prove it is idempotent. Confirm both executions succeed and both tables appear in Table Editor.

- [ ] **Step 7: Commit**

```bash
git add supabase/schema.sql company_names/repository.py tests/test_repository.py
git commit -m "feat: persist validated name mappings in Supabase"
```

---

### Task 5: CSV Seed Import

**Files:**
- Create: `scripts/seed_name_mappings.py`
- Create: `tests/test_seed_name_mappings.py`
- Modify: `docs/SUPABASE_SETUP.md`

**Interfaces:**
- Consumes: cleaner, embedding provider, repository submission payload.
- Produces: `load_seed_rows(path: Path) -> list[tuple[str, str]]`
- Produces CLI: `python3 scripts/seed_name_mappings.py company_name_normalization_finetuning.csv`

- [ ] **Step 1: Write failing importer tests**

Use temporary CSV files to assert:

- `input_text,target_text,remarks` parses successfully.
- A blank target raises `SeedValidationError("row N has a blank target_text")`.
- Contradictory duplicate input names raise before repository calls.
- Equivalent canonical titles create one group payload.
- Inputs are cleaned before embedding and persistence.

- [ ] **Step 2: Run importer tests and verify failure**

Run: `python3 -m pytest tests/test_seed_name_mappings.py -v`

Expected: FAIL because the script does not exist.

- [ ] **Step 3: Implement dry-run-first import**

Use `argparse` with required CSV path and optional `--apply`. Without `--apply`, print the number of cleaned members/groups and validation errors without connecting to Supabase. With `--apply`, require secrets from environment variables, embed titles/members in batches, and call one repository submission.

- [ ] **Step 4: Run tests and validate the real CSV in dry-run mode**

Run: `python3 -m pytest tests/test_seed_name_mappings.py -v`

Run: `python3 scripts/seed_name_mappings.py company_name_normalization_finetuning.csv`

Expected: tests PASS; dry run reports the current nonblank rows and no blank target errors.

- [ ] **Step 5: Update setup instructions with the exact seed command**

Add an optional seed section that instructs the user to export the three secrets as environment variables and run the CLI first without, then with, `--apply`. Do not put real secret values in the command examples.

- [ ] **Step 6: Commit**

```bash
git add scripts/seed_name_mappings.py tests/test_seed_name_mappings.py docs/SUPABASE_SETUP.md
git commit -m "feat: import validated company name mappings"
```

---

### Task 6: Review Service and Duplicate-Safe Extraction

**Files:**
- Create: `company_names/service.py`
- Create: `tests/test_service.py`
- Modify: `plumber.py`
- Create: `tests/test_plumber.py`

**Interfaces:**
- Consumes: repository, embedding provider, ranker, cleaner, review payload, aggregator.
- Produces: `prepare_review(rows: pd.DataFrame, repository, embedder) -> PreparedReview`.
- Produces: `submit_review(board, original_mappings, repository, embedder) -> dict[str, str]`.
- Changes: `extract_all_tables(...)` returns one row per extracted agent occurrence with a `TRAVEL AGENT` column instead of overwriting duplicate index entries.

- [ ] **Step 1: Write failing service tests**

Test with fake repository/embedder objects that:

- Duplicate cleaned names have their numeric values summed for review.
- Exact mappings initialize directly in their permanent group.
- Unknown names receive candidates but remain in the working tray.
- Current-report provisional groups are never passed to the candidate source.
- Embedder failure returns fuzzy suggestions and a warning.
- Submission embeds changed titles/members and calls the repository once.

- [ ] **Step 2: Write a failing duplicate extraction regression test**

Extract the rectangle parsing into a pure helper accepting text blocks. Feed two blocks with the same agent name and assert two rows survive so later aggregation can sum them.

- [ ] **Step 3: Run service/extractor tests and verify failure**

Run: `python3 -m pytest tests/test_service.py tests/test_plumber.py -v`

Expected: FAIL because the service and extraction helper do not exist.

- [ ] **Step 4: Implement orchestration and duplicate-safe rows**

Create `PreparedReview` with `board`, `original_mappings`, `suggestions`, `rows`, and `warnings`. Clean every extracted name before grouping. Refactor `extract_all_tables` to accumulate records and return a DataFrame rather than assigning `df.loc[name]`, preserving every numeric row.

- [ ] **Step 5: Run service/extractor tests**

Run: `python3 -m pytest tests/test_service.py tests/test_plumber.py -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add company_names/service.py tests/test_service.py plumber.py tests/test_plumber.py
git commit -m "feat: prepare company names for human review"
```

---

### Task 7: Searchable Draggable Streamlit Board

**Files:**
- Create: `company_names/ui.py`
- Create: `tests/test_ui_state.py`
- Modify: `app.py`

**Interfaces:**
- Consumes: `PreparedReview`, review validation, service submission, aggregation.
- Produces: `render_name_review(prepared: PreparedReview, repository, embedder) -> pd.DataFrame | None`.
- Produces pure helpers: `sortable_containers(board)`, `apply_sort_result(board, containers)`, and `add_selected_names(board, selected)`.

- [ ] **Step 1: Write failing pure UI-state tests**

Assert that:

- Selecting a search result moves it from inventory to the working tray.
- Selecting the same name twice does not create a duplicate pill.
- Sortable output moves a pill between tray, group, and exclusion.
- Returning a pill to inventory clears group and exclusion state.
- Creating a group uses a stable `new-<uuid>` ID.
- Existing exact mappings render in their saved container.
- Pill labels remain unique even if display names differ only by case.

- [ ] **Step 2: Run UI-state tests and verify failure**

Run: `python3 -m pytest tests/test_ui_state.py -v`

Expected: FAIL because `company_names.ui` does not exist.

- [ ] **Step 3: Implement the pure board adapter**

Encode each sortable item as a stable opaque ID plus cleaned label, and decode component output through a lookup table rather than treating display strings as keys. Always emit containers in this order: `Working tray`, populated/existing groups, new groups, `Excluded from this report`.

- [ ] **Step 4: Implement the approved Streamlit layout**

Use:

```python
selected = st.multiselect(
    "Find names from this report",
    options=searchable_inventory,
    placeholder="Search cleaned company names",
)
```

Feed the selected names into the working tray, then render `sort_items(containers, multi_containers=True, custom_style=BOARD_CSS, key=...)`. Set every text selector in `BOARD_CSS` to black, use blue for exact mappings, gold for suggestions, and a dashed neutral exclusion container. Render canonical-title inputs keyed by stable group ID and a separate `Create empty group` action.

Because `streamlit-sortables` owns the cross-container drag surface, title inputs render immediately above the board in matching group order; rerunning after an edit updates the container header. The component must remain usable without dragging by providing selectboxes labeled `Move <name> to` as an accessibility fallback inside an expander.

- [ ] **Step 5: Integrate review after PDF collation**

In `app.py`, replace immediate `groupby(combined_df.index).sum()` display with:

1. Normalize extracted rows into `cleaned_name`, `rns`, and `revenue`.
2. Initialize Supabase and FastEmbed through `st.cache_resource`.
3. Store `PreparedReview` in `st.session_state` after PDF processing.
4. Render the board on subsequent reruns without re-reading uploaded PDFs.
5. Show the final aggregated DataFrame only after successful submission.

Preserve the existing non-collation PDF extractor mode.

- [ ] **Step 6: Run tests and launch a manual UI smoke test**

Run: `python3 -m pytest tests/test_ui_state.py -v`

Run: `streamlit run app.py`

Manually verify black text, searchable results, pill spawning, cross-group dragging, exclusion, title editing, new-group creation, and the accessibility fallback.

- [ ] **Step 7: Commit**

```bash
git add company_names/ui.py tests/test_ui_state.py app.py
git commit -m "feat: add searchable draggable name review board"
```

---

### Task 8: Authorization, Atomic Submit, Backup, and Failure UX

**Files:**
- Modify: `company_names/ui.py`
- Modify: `company_names/service.py`
- Create: `tests/test_submission.py`
- Modify: `app.py`

**Interfaces:**
- Consumes: `ADMIN_PASSWORD` from `st.secrets` and repository methods.
- Produces: `password_matches(candidate: str, expected: str) -> bool` using `hmac.compare_digest`.
- Produces: authorized submit and CSV export UI.

- [ ] **Step 1: Write failing submission tests**

Test that:

- A wrong password prevents every repository write.
- A correct password plus invalid board prevents writes and returns all validation errors.
- A correct valid submission makes one RPC call.
- RPC failure preserves the board and does not produce final totals.
- Returning a validated member to inventory with its × action produces `unmap_names`; dragging it to report-only exclusion does not.
- Backup export requires authorization and contains `cleaned_name,canonical_title` in stable order.

- [ ] **Step 2: Run submission tests and verify failure**

Run: `python3 -m pytest tests/test_submission.py -v`

Expected: FAIL because authorization/submission behavior is absent.

- [ ] **Step 3: Implement secure session authorization**

Compare passwords with `hmac.compare_digest`. Store only `st.session_state["mapping_admin_unlocked"] = True`, never the password. If `ADMIN_PASSWORD` is absent, show a configuration error and disable permanent actions.

- [ ] **Step 4: Implement submission and errors**

Render errors next to the final action. On success, clear repository read caches, store returned group IDs, aggregate totals, and show a success message. Catch `RepositoryUnavailableError` and embedding warnings without clearing `PreparedReview`.

- [ ] **Step 5: Implement authorized backup download**

Build CSV bytes with `pandas.DataFrame(repository.export_rows()).to_csv(index=False).encode("utf-8")` and render `st.download_button` only for the unlocked session.

- [ ] **Step 6: Run submission tests and the full unit suite**

Run: `python3 -m pytest -m "not integration" -v`

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add company_names/ui.py company_names/service.py tests/test_submission.py app.py
git commit -m "feat: secure mapping submission and backups"
```

---

### Task 9: Deployment Verification and Documentation Finish

**Files:**
- Modify: `README.md`
- Modify: `docs/SUPABASE_SETUP.md`
- Create: `.streamlit/secrets.example.toml`

**Interfaces:**
- Documents the final setup and verification workflow; no new runtime interface.

- [ ] **Step 1: Add a safe example secrets file**

Create `.streamlit/secrets.example.toml` with placeholder URL, service key, and admin password. Ensure only `.streamlit/secrets.toml`, not the example, is ignored.

- [ ] **Step 2: Replace the README placeholder content**

Document both app modes, Python setup, `pip install -r requirements.txt`, `streamlit run app.py`, unit tests, optional integration test, schema installation, CSV dry run/import, and the Supabase guide link.

- [ ] **Step 3: Reconcile the setup guide with the implemented schema and UI**

Follow every instruction in `docs/SUPABASE_SETUP.md` against a clean Supabase project. Correct menu names, commands, secret names, and verification expectations based on what actually works.

- [ ] **Step 4: Run fresh final verification**

Run:

```bash
python3 -m pytest -m "not integration" -v
python3 -m pytest -m integration tests/test_matching.py -v
python3 -m compileall app.py plumber.py company_names scripts
git diff --check
```

Expected: zero failed tests, successful compilation, and no whitespace errors.

- [ ] **Step 5: Verify deployed persistence**

Deploy to Streamlit Community Cloud with the documented secrets, submit one test mapping, reboot the app, upload the same input, and confirm the exact saved group is restored. Remove that member, submit, reboot again, and confirm it is now unknown/suggested rather than exactly mapped.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/SUPABASE_SETUP.md .streamlit/secrets.example.toml
git commit -m "docs: document name grouping deployment"
```

- [ ] **Step 7: Request final code review**

Invoke `requesting-code-review`, compare the completed branch against `docs/superpowers/specs/2026-08-12-company-name-grouping-design.md`, resolve findings, rerun Step 4, and only then prepare the branch for integration.
