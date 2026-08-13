# Singleton-First Company Name Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every included report name an automatic singleton by default so users only use the working tray to combine exceptions, then present the workflow in plain language.

**Architecture:** Keep `ReviewBoard` as the persisted Streamlit review state: `selected=False` means an included separate company, `selected=True/group_id=None` means working tray, `selected=True/group_id=<id>` means a combined group, and `excluded=True` means report-only exclusion. At submission, create a deep-copied materialized board that deterministically adds singleton groups; build, embed, submit, reconcile, and aggregate that copy without changing Supabase. Replace the current wall-of-containers renderer with small pure UI projections and task-oriented Streamlit sections while retaining accessible click controls and optional drag-and-drop for tray/groups.

**Tech Stack:** Python 3.10, Streamlit 1.55, pandas 2.3, streamlit-sortables 0.3.1, Supabase/PostgREST, pytest 9.

## Global Constraints

- Do not modify `supabase/schema.sql` or database privileges, functions, tables, indexes, and transaction behavior.
- Do not change company-name cleaning, FastEmbed model/configuration, matching weights, authentication rules, backup CSV format, or numeric aggregation semantics.
- Existing exact mappings remain authoritative until a user deliberately moves a member.
- Exclusion remains report-only and must not mutate permanent mappings.
- Every drag operation must have a clear keyboard/click alternative.
- User-facing copy must use **Separate companies**, **Working tray**, **Combined groups**, and **Left out of this report**; do not expose inventory, provisional group, canonical key, or payload terminology.
- All user-facing text must remain high contrast and must not depend on color alone.
- Preserve stable request IDs, retry-ledger behavior, atomic submission, complete resolution validation, and stale-result clearing.
- Leave `company_name_normalization_finetuning.csv` untracked and untouched.

## File Map

- Modify `company_names/review.py`: validate the singleton-first state, materialize deterministic singleton groups on a copy, build atomic mapping/unmap payloads, and aggregate singleton rows.
- Modify `company_names/service.py`: submit and reconcile the materialized copy while retaining the original board on failure.
- Modify `company_names/ui.py`: add pure tray/group movement and summary helpers; render the simplified three-step review.
- Modify `tests/test_review.py`: domain, payload, direct-remap, and aggregation regression tests.
- Modify `tests/test_service.py`: embedding, response reconciliation, retry, and successful singleton persistence tests.
- Modify `tests/test_ui_state.py`: pure UI state, naming, movement, creation, summary, and accessible-action tests.
- Modify `tests/test_streamlit_smoke.py` or create it if absent: execute the real renderer against a controlled Streamlit test surface to catch runtime-only failures.
- Modify `docs/SUPABASE_SETUP.md`: update only the user-facing review and backup instructions; schema setup remains unchanged.

---

### Task 1: Model Separate Companies and Deterministic Singleton Materialization

**Files:**
- Modify: `company_names/review.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Produces: `singleton_group_id(cleaned_name: str) -> str`
- Produces: `materialize_singletons(board: ReviewBoard) -> ReviewBoard`
- Produces: `validate_submission(board: ReviewBoard) -> list[str]`
- Changes: `validate_board(board)` accepts a non-excluded `selected=False/group_id=None` record as a valid separate company and accepts `selected=True/group_id=None` as a working-tray record.
- Consumes: existing `normalize_lookup_key`, `Group`, `NameRecord`, and `ReviewBoard`.

- [ ] **Step 1: Write failing state and materialization tests**

Add focused tests demonstrating the complete state vocabulary:

```python
def test_validate_accepts_separate_company_and_working_tray():
    review = board(
        groups=[],
        names=[
            NameRecord("Separate", None, "unknown", selected=False),
            NameRecord("Tray", None, "suggested", selected=True),
        ],
    )
    assert validate_board(review) == []


def test_materialize_singletons_returns_a_copy_with_stable_groups():
    review = board(
        groups=[],
        names=[
            NameRecord("Zulu Travel", None, "unknown"),
            NameRecord("Alpha Travel", None, "suggested"),
        ],
    )
    first = materialize_singletons(review)
    second = materialize_singletons(review)

    assert first is not review
    assert review.groups == {}
    assert [first.names[name].group_id for name in ("Alpha Travel", "Zulu Travel")] == [
        singleton_group_id("Alpha Travel"), singleton_group_id("Zulu Travel")
    ]
    assert first.groups == second.groups
    assert all(group.canonical_title in {"Alpha Travel", "Zulu Travel"}
               for group in first.groups.values())


def test_materialize_does_not_change_grouped_or_excluded_names():
    review = board(
        groups=[Group("g", "Existing", True)],
        names=[
            NameRecord("Mapped", "g", "exact", selected=True),
            NameRecord("Excluded", None, "unknown", selected=True, excluded=True),
        ],
    )
    result = materialize_singletons(review)
    assert result.names["Mapped"].group_id == "g"
    assert result.names["Excluded"].group_id is None
    assert list(result.groups) == ["g"]
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  tests/test_review.py::test_validate_accepts_separate_company_and_working_tray \
  tests/test_review.py::test_materialize_singletons_returns_a_copy_with_stable_groups \
  tests/test_review.py::test_materialize_does_not_change_grouped_or_excluded_names -v
```

Expected: failures because working-tray records are rejected and the materialization functions do not exist.

- [ ] **Step 3: Implement deterministic copy materialization**

In `company_names/review.py`, use a SHA-256-derived non-UUID temporary ID so repeated builds of the same unchanged board are byte-equivalent:

```python
from copy import deepcopy
import hashlib


def singleton_group_id(cleaned_name: str) -> str:
    digest = hashlib.sha256(cleaned_name.encode("utf-8")).hexdigest()
    return f"new-singleton-{digest}"


def materialize_singletons(board: ReviewBoard) -> ReviewBoard:
    result = deepcopy(board)
    for cleaned_name, record in sorted(result.names.items()):
        if not record.selected and not record.excluded and record.group_id is None:
            group_id = singleton_group_id(cleaned_name)
            result.groups[group_id] = Group(group_id, cleaned_name, False)
            record.selected = True
            record.group_id = group_id
    return result
```

Update `validate_board` so the two valid ungrouped states are no longer errors. Retain errors for these impossible states:

- `selected=False` with a group or exclusion;
- `excluded=True` with a group;
- a missing referenced group;
- blank/non-normalizable populated titles;
- duplicate normalized populated titles.

Add `validate_submission`, which starts with `validate_board` and adds one actionable error when any working-tray records remain:

```python
def validate_submission(board: ReviewBoard) -> list[str]:
    errors = validate_board(board)
    tray_count = sum(
        record.selected and not record.excluded and record.group_id is None
        for record in board.names.values()
    )
    if tray_count:
        errors.append(
            f"Resolve {tray_count} name{'s' if tray_count != 1 else ''} in the working tray: "
            "create a combined group or return them to Separate companies."
        )
    return errors
```

`validate_board` supports valid intermediate editing state; `validate_submission` prevents tray names from being silently omitted at Save.

- [ ] **Step 4: Run Task 1 tests and the existing review suite**

Run:

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_review.py -v
```

Expected: all review tests pass after updating obsolete tests that asserted included-but-ungrouped was invalid. Do not weaken the remaining state-invariant assertions.

- [ ] **Step 5: Commit Task 1**

```bash
git add company_names/review.py tests/test_review.py
git commit -m "feat: materialize implicit singleton groups"
```

---

### Task 2: Build Atomic Singleton, Unmap, and Direct-Remap Payloads

**Files:**
- Modify: `company_names/review.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: `materialize_singletons(board) -> ReviewBoard` from Task 1.
- Changes: `build_submission(board, original_mappings, request_id=None)` materializes singleton groups internally unless passed an already materialized board; output remains `SubmissionPayload`.
- Changes: deliberate movement from an original group to a singleton or another group produces both an unmap of the persisted identity and a mapping to the new group.

- [ ] **Step 1: Write failing payload tests**

```python
def test_build_submission_materializes_all_unseen_separate_companies():
    review = board(
        groups=[],
        names=[NameRecord("Alpha", None, "unknown"), NameRecord("Beta", None, "unknown")],
    )
    payload = build_submission(review, {}, request_id="11111111-1111-4111-8111-111111111111")
    assert [group["canonical_title"] for group in payload.groups] == ["Alpha", "Beta"]
    assert {mapping["cleaned_name"] for mapping in payload.mappings} == {"Alpha", "Beta"}
    assert payload.unmap_names == []


def test_unchanged_exact_mapping_is_not_recreated_as_singleton():
    review = board(
        groups=[Group("old", "Miki", True)],
        names=[NameRecord("Miki Travel", "old", "exact", True, persisted_name="Miki-Travel")],
    )
    payload = build_submission(review, {"Miki Travel": "old"})
    assert not any(group["canonical_title"] == "Miki Travel" for group in payload.groups)
    assert payload.unmap_names == []


def test_exact_member_returned_to_separate_company_unmaps_then_remaps():
    review = board(
        groups=[Group("old", "Old Group", True)],
        names=[NameRecord("Alias", None, "exact", persisted_name="Stored-Alias")],
    )
    payload = build_submission(review, {"Alias": "old"})
    singleton = singleton_group_id("Alias")
    assert payload.unmap_names == ["Stored-Alias"]
    assert {"cleaned_name": "Stored-Alias", "group_id": singleton} in payload.mappings


def test_deliberate_direct_remap_emits_unmap_and_mapping_atomically():
    review = board(
        groups=[Group("old", "Old", True), Group("new", "New", True)],
        names=[NameRecord("Alias", "new", "exact", True, persisted_name="Stored")],
    )
    payload = build_submission(review, {"Alias": "old"})
    assert payload.unmap_names == ["Stored"]
    assert payload.mappings == [{"cleaned_name": "Stored", "group_id": "new"}]
```

- [ ] **Step 2: Run the four tests and verify RED**

Run the four node IDs with `pytest -v`. Expected: missing singleton payloads and the existing direct-remap rejection.

- [ ] **Step 3: Implement payload derivation from one materialized copy**

At the beginning of `build_submission`, call `validate_submission(board)` before materialization, then create `submission_board = materialize_singletons(board)` and run payload generation against it. Replace the old direct-remap rejection with deliberate unmap derivation:

```python
changed_originals = {
    record.persisted_name or cleaned_name
    for cleaned_name, record in submission_board.names.items()
    if cleaned_name in original_mappings
    and not record.excluded
    and record.group_id != original_mappings[cleaned_name]
}
```

Union those identities with names deliberately left out of persistence according to the established original-mapping rules. Do not add excluded records to `unmap_names`. Preserve deterministic group, mapping, and unmap ordering and persisted alias identity.

- [ ] **Step 4: Run payload/review tests**

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_review.py -v
```

Expected: all tests pass, including retry request-ID equality, identity mismatch checks, empty-group behavior, and exact alias storage tests.

- [ ] **Step 5: Commit Task 2**

```bash
git add company_names/review.py tests/test_review.py
git commit -m "feat: submit singleton and remap mutations atomically"
```

---

### Task 3: Reconcile Materialized Singletons and Preserve Numeric Results

**Files:**
- Modify: `company_names/service.py`
- Modify: `company_names/review.py`
- Test: `tests/test_service.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Changes: `_prepare_submission_payload(...) -> tuple[SubmissionPayload, bool, ReviewBoard]`; the third item is the exact materialized board used to derive the payload.
- Changes: `aggregate_by_group(rows, board)` treats separate companies as their cleaned title before successful materialization and grouped names as their group title.
- Preserves: public `submit_review`, `submit_prepared_review`, and `submit_review_authorized` signatures.

- [ ] **Step 1: Write failing service and aggregation tests**

```python
def test_aggregate_includes_separate_companies_under_their_cleaned_names():
    review = board(groups=[], names=[NameRecord("Alpha", None, "unknown")])
    rows = pd.DataFrame([{"cleaned_name": "Alpha", "rns": 2, "revenue": 10}])
    assert aggregate_by_group(rows, review).to_dict("records") == [{
        "TRAVEL AGENT": "Alpha", "Sum of RNS": 2.0, "Sum of R REVENUE": 10.0
    }]


def test_successful_submit_reconciles_singleton_to_resolved_uuid():
    prepared = prepared_review_with_one_unseen_separate_name("Alpha")
    repository = FakeRepository(
        resolved={singleton_group_id("Alpha"): "11111111-1111-4111-8111-111111111111"}
    )
    outcome = submit_review_authorized(prepared, repository, FakeEmbedder(), "admin", "admin")
    assert outcome.success
    assert prepared.board.names["Alpha"].group_id == "11111111-1111-4111-8111-111111111111"
    assert prepared.board.groups["11111111-1111-4111-8111-111111111111"].canonical_title == "Alpha"
    assert prepared.original_mappings["Alpha"] == "11111111-1111-4111-8111-111111111111"


def test_failed_singleton_submit_leaves_visible_board_unchanged_for_retry():
    prepared = prepared_review_with_one_unseen_separate_name("Alpha")
    before = deepcopy(prepared.board)
    outcome = submit_review_authorized(prepared, FailingRepository(), FakeEmbedder(), "admin", "admin")
    assert not outcome.success
    assert prepared.board == before
```

Use existing test factories where possible; extend `FakeRepository` with an explicit resolved map rather than introducing a second incompatible fake.

- [ ] **Step 2: Run focused tests and verify RED**

Expected: separate aggregation is empty, and successful reconciliation cannot find implicit groups on the original board.

- [ ] **Step 3: Return and reconcile the exact materialized board**

Change `_prepare_submission_payload` to materialize once, build the payload from that board, compute changed mappings using that board, and return it:

```python
submission_board = materialize_singletons(board)
payload = build_submission(submission_board, original_mappings, request_id=request_id)
# enrich payload as today
return payload, embedding_failed, submission_board
```

In `submit_review_authorized`, retain the materialized board only in a local variable until repository submission and resolution validation succeed. Then:

```python
candidate = deepcopy(prepared)
candidate.board = submission_board
_apply_resolved_group_ids(candidate, resolved)
_refresh_original_mappings(candidate)
result = aggregate_by_group(candidate.rows, candidate.board)
```

Change the authorized preflight from `validate_board` to `validate_submission` so unresolved tray names make zero repository calls. On every error path, leave `prepared.board`, `prepared.original_mappings`, and `pending_request_id` unchanged. Update other callers to unpack the third return value without changing their public behavior.

Update `aggregate_by_group` so a record that is neither excluded nor explicitly in the tray maps to its cleaned name when it has no group. Ensure tray state cannot reach final aggregation before materialization in the authorized path.

- [ ] **Step 4: Verify service, submission, and review suites**

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  tests/test_service.py tests/test_submission.py tests/test_review.py -v
```

Expected: all pass, including malformed resolution retry, vector batching, persisted alias reconciliation, exclusions, and revenue ordering.

- [ ] **Step 5: Commit Task 3**

```bash
git add company_names/service.py company_names/review.py tests/test_service.py tests/test_review.py
git commit -m "feat: reconcile submitted singleton groups"
```

---

### Task 4: Add Pure Singleton-First UI State Operations

**Files:**
- Modify: `company_names/ui.py`
- Test: `tests/test_ui_state.py`

**Interfaces:**
- Produces: `move_to_tray(board: ReviewBoard, names: list[str]) -> None`
- Produces: `return_to_separate(board: ReviewBoard, cleaned_name: str) -> None`
- Produces: `create_combined_group(board: ReviewBoard, title: str) -> Group`
- Produces: `group_creation_error(board: ReviewBoard, title: str) -> str | None`
- Produces: `review_summary(board: ReviewBoard) -> dict[str, int]`
- Changes: `name_status` uses the four approved location labels.

- [ ] **Step 1: Write failing movement, creation, and summary tests**

```python
def test_move_to_tray_works_from_every_location():
    state = singleton_first_board()
    move_to_tray(state, ["Separate", "Grouped", "Excluded"])
    for name in ("Separate", "Grouped", "Excluded"):
        record = state.names[name]
        assert record.selected and record.group_id is None and not record.excluded


def test_return_to_separate_clears_only_location_state():
    state = singleton_first_board()
    original_persisted_name = state.names["Grouped"].persisted_name
    move_to_tray(state, ["Grouped"])
    return_to_separate(state, "Grouped")
    assert state.names["Grouped"].selected is False
    assert state.names["Grouped"].group_id is None
    assert state.names["Grouped"].persisted_name == original_persisted_name


def test_create_combined_group_requires_two_tray_names_and_typed_title():
    state = singleton_first_board()
    move_to_tray(state, ["Separate"])
    assert group_creation_error(state, "") == "Enter the final company name."
    assert group_creation_error(state, "New Final Name") == "Add at least two names to the working tray."


def test_create_combined_group_accepts_title_not_in_report():
    state = singleton_first_board()
    move_to_tray(state, ["Separate", "Grouped"])
    group = create_combined_group(state, "New Final Name")
    assert group.canonical_title == "New Final Name"
    assert all(state.names[name].group_id == group.id for name in ("Separate", "Grouped"))


def test_duplicate_normalized_title_directs_user_to_existing_group():
    state = singleton_first_board()
    move_to_tray(state, ["Separate", "Excluded"])
    assert group_creation_error(state, "existing-group") == (
        "A group named ‘Existing Group’ already exists. Move these names into that group instead."
    )


def test_summary_counts_each_visible_location():
    assert review_summary(singleton_first_board()) == {
        "separate": 1, "combined_groups": 1, "combined_names": 1,
        "tray": 0, "excluded": 1,
    }
```

- [ ] **Step 2: Run focused UI-state tests and verify RED**

Expected: imports fail because the pure functions do not exist and old status labels differ.

- [ ] **Step 3: Implement the pure operations**

Use existing state fields without adding database-facing model fields:

```python
def move_to_tray(board, names):
    for name in dict.fromkeys(names):
        record = board.names[name]
        record.selected = True
        record.group_id = None
        record.excluded = False


def return_to_separate(board, cleaned_name):
    record = board.names[cleaned_name]
    record.selected = False
    record.group_id = None
    record.excluded = False
```

`create_combined_group` must call `group_creation_error`, raise `ValueError` with the returned actionable text if invalid, create a `new-<uuid4>` group with the trimmed typed title, and move every current tray record into it. Preserve `persisted_name` so later payload derivation can explicitly unmap old aliases.

The save UI consumes `validate_submission`, not only `validate_board`, so a non-empty tray disables Save and displays the actionable resolve-or-return message.

Use `normalize_lookup_key` for title conflicts and handle suffix-only/non-normalizable text as `Enter a usable final company name.` Do not change cleaning rules.

- [ ] **Step 4: Run the complete UI-state suite**

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_ui_state.py -v
```

Expected: all pass after replacing old “Inventory”/“Excluded” assertions with “Separate company”/“Left out of this report.”

- [ ] **Step 5: Commit Task 4**

```bash
git add company_names/ui.py tests/test_ui_state.py
git commit -m "feat: add singleton-first review actions"
```

---

### Task 5: Render the Plain-Language Three-Step Review

**Files:**
- Modify: `company_names/ui.py`
- Test: `tests/test_ui_state.py`
- Test: `tests/test_app_source.py`

**Interfaces:**
- Consumes: Task 4 movement, creation, validation, summary, and status helpers.
- Preserves: `render_name_review(prepared, repository, embedder, admin_password=None) -> pd.DataFrame | None`.

- [ ] **Step 1: Add failing copy and projection tests**

Add assertions against small pure render/projection helpers rather than brittle full HTML snapshots:

```python
def test_separate_company_projection_is_compact_and_sorted():
    state = many_singleton_board(196)
    names = separate_company_names(state)
    assert len(names) == 196
    assert names == sorted(names, key=lambda value: (value.casefold(), value))


def test_approved_plain_language_is_present_and_old_terms_are_absent():
    source = Path("company_names/ui.py").read_text()
    for phrase in (
        "1. Find names", "2. Combine duplicates", "3. Review and save",
        "Separate companies", "Working tray", "Combined groups",
        "Left out of this report", "Save mappings and show totals",
        "Backup and recovery",
    ):
        assert phrase in source
    for phrase in ("In inventory", "Canonical title", "Unlock permanent actions"):
        assert phrase not in source
```

Add pure tests proving the search callback always calls `move_to_tray`, including grouped and excluded names, and the click fallback can move tray → separate, tray → group, group → tray, and any location → excluded.

- [ ] **Step 2: Run tests and verify RED**

Expected: missing projections and old copy remains.

- [ ] **Step 3: Replace the initial wall with the approved structure**

Render these sections in order:

```text
Review company names
1. Find names  →  2. Combine duplicates  →  3. Review and save

Search company names
Names left under Separate companies will be saved separately automatically.

Working tray
[tray pills with Return to separate actions]

Final company name [text input]
[Create combined group]

Combined groups
[title + member pills + Move to tray controls]

Left out of this report
[excluded pills]
```

Do not render every separate company as a top-level widget. Show a count and place the sorted names inside `st.expander("View separate companies (N)")`; search remains the primary route.

Keep streamlit-sortables only for Working tray, populated combined groups, and Left out of this report. Do not create 196 draggable singleton containers. Update the opaque container validation to match exactly the displayed destination set.

- [ ] **Step 4: Add typed group creation and local validation**

Use a request-scoped `final_company_name` widget key. Disable **Create combined group** when `group_creation_error` is not `None`; display the returned message directly beneath the field. On click, call `create_combined_group`, clear the field using a callback-safe session-state transition, and rerun.

Render existing group title edits before projections, as the current synchronization fix requires. Show duplicate/non-normalizable title messages beside the relevant input and disable final save if unresolved.

- [ ] **Step 5: Replace the generic movement expander with plain click controls**

The non-drag path must say:

```text
Move a company name
Name: [search/select]
Move to: [Separate companies | Working tray | Group: ... | Left out of this report]
[Move]
```

Call the same pure movement functions used by drag and group buttons. Never use “Inventory.”

- [ ] **Step 6: Run UI and source tests**

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  tests/test_ui_state.py tests/test_app_source.py -v
```

Expected: all pass, including opaque identity, HTML escaping, synchronized semantic preview, changed-only reruns, request-scoped widget state, and high-contrast CSS.

- [ ] **Step 7: Commit Task 5**

```bash
git add company_names/ui.py tests/test_ui_state.py tests/test_app_source.py
git commit -m "feat: simplify company name review workflow"
```

---

### Task 6: Simplify Save, Password, Summary, and Backup Copy

**Files:**
- Modify: `company_names/ui.py`
- Test: `tests/test_ui_state.py`
- Test: `tests/test_submission.py`

**Interfaces:**
- Consumes: `review_summary(board)` from Task 4.
- Preserves: existing `_unlock_admin`, authorization throttle, password-digest rotation, `export_backup_csv`, `clear_final_results`, and `submit_review_authorized` behavior.

- [ ] **Step 1: Write failing summary and administrative-layout assertions**

```python
def test_save_summary_uses_plain_language_counts():
    assert save_summary_lines(singleton_first_board()) == [
        "1 separate company",
        "1 combined group",
        "1 name combined",
        "1 name left out of this report",
    ]


def test_backup_explanation_says_what_is_downloaded():
    source = Path("company_names/ui.py").read_text()
    assert 'st.expander("Backup and recovery")' in source
    assert "Download a CSV copy of all permanent company-name mappings." in source
```

Add or retain tests that a locked session cannot submit/export, a bad password makes zero repository calls, and a valid save makes exactly one repository submission.

- [ ] **Step 2: Run focused tests and verify RED**

Expected: missing summary helper and old backup placement/copy.

- [ ] **Step 3: Render the save section in task order**

Immediately above the password/save controls, render:

```text
3. Review and save
N separate companies
N combined groups
N names combined
N names left out of this report
```

Replace **Unlock permanent actions** with **Enter admin password to save**. Replace **Submit final review** with **Save mappings and show totals**. Keep the existing password comparison, throttle, session digest binding, button-disable rules, request identity, and submission callback unchanged.

- [ ] **Step 4: Move backup into a collapsed bottom expander**

Render after the save section:

```python
with st.expander("Backup and recovery"):
    st.write("Download a CSV copy of all permanent company-name mappings.")
    # existing authorized prepare/download flow
```

Retain authorization, exact CSV bytes, formula-safe codec, and filename. Rename **Prepare mappings backup** to **Prepare backup file**.

- [ ] **Step 5: Verify UI, authorization, submission, and backup suites**

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  tests/test_ui_state.py tests/test_submission.py tests/test_csv_safety.py -v
```

Expected: all pass with no changed security or backup data behavior.

- [ ] **Step 6: Commit Task 6**

```bash
git add company_names/ui.py tests/test_ui_state.py tests/test_submission.py
git commit -m "fix: clarify saving and mapping backups"
```

---

### Task 7: Add Runtime Streamlit Smoke Coverage

**Files:**
- Create or Modify: `tests/test_streamlit_smoke.py`
- Modify: `tests/test_app_source.py` only if needed for a small injectable seam
- Modify: `company_names/ui.py` only if needed for that seam

**Interfaces:**
- Exercises: the real `render_name_review` execution path far enough to render styles, search, tray, group creation controls, save summary, and backup expander.
- Must not contact Supabase, download FastEmbed, expose secrets, or submit mutations.

- [ ] **Step 1: Write a failing runtime renderer smoke test**

Use Streamlit's supported app-testing interface when available (`streamlit.testing.v1.AppTest`) or a narrowly scoped fake `streamlit` module that executes every renderer expression. The test must call the renderer rather than merely parse source:

```python
def test_review_renderer_executes_without_runtime_interpolation_errors():
    prepared = prepared_review_for_render(
        separate=["Alpha", "Beta"],
        grouped={"Existing": ["Stored Alias"]},
    )
    app = render_test_app(prepared, FakeRepository(), FakeEmbedder())
    app.run(timeout=10)
    assert not app.exception
    assert "Review company names" in rendered_text(app)
    assert "Separate companies" in rendered_text(app)
    assert "Working tray" in rendered_text(app)
```

Do not make network calls. If `AppTest` cannot inject the prepared object cleanly, create a tiny test-only Streamlit script under `tests/fixtures/singleton_review_app.py` that imports and renders controlled fakes.

- [ ] **Step 2: Run the smoke test and verify RED if the seam is absent**

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_streamlit_smoke.py -v
```

Expected: fail because the runtime fixture/seam is not yet available, not because of network or credentials.

- [ ] **Step 3: Add the minimum injectable test seam**

Keep production signatures unchanged. The seam may be a fixture script that constructs `PreparedReview`, `ReviewBoard`, fake repository/embedder objects, and calls `render_name_review`. Do not add debug flags or test-only branches to production behavior.

- [ ] **Step 4: Verify the runtime smoke test**

Run the command from Step 2. Expected: pass with zero uncaught Streamlit exceptions and no Supabase/FastEmbed calls.

- [ ] **Step 5: Commit Task 7**

```bash
git add tests/test_streamlit_smoke.py tests/fixtures/singleton_review_app.py company_names/ui.py tests/test_app_source.py
git commit -m "test: execute singleton review Streamlit UI"
```

Stage only files that exist and changed.

---

### Task 8: Update Operator Guidance and Run Release Verification

**Files:**
- Modify: `docs/SUPABASE_SETUP.md`
- Test: entire repository

**Interfaces:**
- Documents the implemented singleton-first review without changing setup/schema instructions.

- [ ] **Step 1: Update the deployed-app verification instructions**

Replace the old review-board checklist with these concrete steps:

```text
1. Process a small report.
2. Confirm untouched names appear as separate companies and require no group creation.
3. Search for two aliases and move them to the working tray.
4. Type a final company name that does not need to appear in the report.
5. Create the combined group, move one name back to Separate companies, and add it again.
6. Enter the admin password and select Save mappings and show totals.
7. Reboot the app, process the names again, and confirm the saved grouping returns.
8. Open Backup and recovery and confirm its authorized CSV download still works.
```

Do not change schema commands, credential handling, backup format, or restoration warnings.

- [ ] **Step 2: Run focused static and compilation checks**

```bash
.venv/bin/python -m compileall -q app.py company_names tests
git diff --check
rg -n "In inventory|Canonical title|Unlock permanent actions|Submit final review" company_names/ui.py
```

Expected: compilation and diff checks succeed; `rg` returns no obsolete user-facing copy.

- [ ] **Step 3: Run all non-integration tests**

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -m 'not integration' -q
```

Expected: all selected tests pass; exactly the real FastEmbed integration test remains deselected.

- [ ] **Step 4: Run the real FastEmbed integration smoke**

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  -m integration tests/test_matching.py -q
```

Expected: the real model test passes and returns 384-dimensional embeddings. If network/cache is unavailable, report that external limitation rather than changing matching behavior.

- [ ] **Step 5: Inspect the final change boundary**

```bash
git status --short
git diff --stat HEAD
git diff -- supabase/schema.sql company_names/cleaning.py company_names/matching.py company_names/csv_safety.py
```

Expected: no diff in protected database, cleaning, matching, or backup-codec files; the user CSV remains the only unrelated untracked file.

- [ ] **Step 6: Commit documentation**

```bash
git add docs/SUPABASE_SETUP.md
git commit -m "docs: explain singleton-first review workflow"
```

- [ ] **Step 7: Request final code review**

Use `superpowers:requesting-code-review` against the full implementation range. Require explicit review of singleton materialization, persisted alias unmapping, direct remap atomicity, lost-response retry identity, report-only exclusions, runtime Streamlit execution, and the protected-file diff.
