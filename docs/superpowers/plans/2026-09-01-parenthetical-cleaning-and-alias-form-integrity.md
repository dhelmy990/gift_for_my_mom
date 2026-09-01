# Parenthetical Cleaning and Alias Form Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove complete parenthesized company-name segments and guarantee that distinct saved aliases remain separate, editable, prepopulated rename rows until aggregation after save.

**Architecture:** Extend deterministic cleanup with a focused, repeatable parenthetical-segment removal pass before the existing legal-suffix pass. Keep `PreparedAliases.review_rows` as the editor source of truth and `PreparedAliases.rows` as the pre-aggregation measures; add runtime coverage proving that 145 saved aliases, including two aliases targeting one canonical name, stay distinct in the form and combine only in the saved totals. Improve final-name validation so a hidden blank value identifies its cleaned alias.

**Tech Stack:** Python 3.10+, `re`, pandas 2.3.3, Streamlit 1.55.0, pytest 9.0.2, Streamlit `AppTest`

## Global Constraints

- Remove every complete parenthesized segment, including multiple and nested segments, wherever it occurs in a raw company name.
- Preserve unmatched opening or closing parentheses for the existing cleanup rules.
- Build the rename form from distinct cleaned report aliases before aggregation.
- Prepopulate an exact saved alias with its saved canonical destination and keep the field editable.
- Filters, searches, and pagination only affect visibility; saving submits every current-report alias.
- Aggregate aliases sharing a destination only after validation and persistence.
- Do not read, extract, print, summarize, or expose any content from `temporary_pdfs/`.
- Preserve the untracked user-owned `company_name_normalization_finetuning.csv` and `temporary_pdfs/` files.

## File Structure

- Modify `company_names/cleaning.py`: remove complete parenthesized segments before legal-suffix cleanup.
- Modify `company_names/service.py`: name missing or blank cleaned aliases in final-name validation.
- Modify `tests/test_cleaning.py`: specify parenthetical cleanup and malformed-parenthesis behavior.
- Modify `tests/test_service.py`: specify actionable missing/blank validation messages.
- Create `tests/fixtures/shared_destination_alias_app.py`: controlled 145-row saved-alias Streamlit app.
- Modify `tests/test_streamlit_smoke.py`: prove saved aliases remain separate and editable through filtering, pagination, and save.

---

### Task 1: Remove Complete Parenthesized Segments

**Files:**
- Modify: `tests/test_cleaning.py`
- Modify: `company_names/cleaning.py`

**Interfaces:**
- Consumes: `clean_company_name(raw_name: str) -> str`
- Produces: the same public signature with parenthetical removal occurring before `_SUFFIX_RE` matching

- [ ] **Step 1: Replace the old `(S)` preservation case and add focused cleanup tests**

In `tests/test_cleaning.py`, change the existing Hong Thai expectation and add these tests after `test_clean_company_name`:

```python
@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        ("ACME (Singapore) Travel (Wholesale)", "ACME Travel"),
        ("ACME (Regional (Singapore)) Travel", "ACME Travel"),
        ("Wendy Tour (S) P/L (Formerly SMI)", "Wendy Tour P/L"),
    ],
)
def test_clean_company_name_removes_complete_parenthesized_segments(
    raw_name: str, expected: str
) -> None:
    assert clean_company_name(raw_name) == expected


@pytest.mark.parametrize("raw_name", ["ACME (Singapore", "ACME Singapore)"])
def test_clean_company_name_preserves_unmatched_parentheses(raw_name: str) -> None:
    assert clean_company_name(raw_name) == raw_name


def test_clean_company_name_rejects_parenthesized_only_name() -> None:
    with pytest.raises(ValueError, match="empty"):
        clean_company_name("(Formerly ACME)")
```

In the existing parametrized `test_clean_company_name`, change:

```python
("Hong Thai Travel Services (S) Pte", "Hong Thai Travel Services (S)"),
```

to:

```python
("Hong Thai Travel Services (S) Pte", "Hong Thai Travel Services"),
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_cleaning.py::test_clean_company_name \
  tests/test_cleaning.py::test_clean_company_name_removes_complete_parenthesized_segments \
  tests/test_cleaning.py::test_clean_company_name_rejects_parenthesized_only_name -v
```

Expected: FAIL because `(S)`, `(Singapore)`, and other complete segments are still preserved, and `"(Formerly ACME)"` does not yet raise the empty-name error.

- [ ] **Step 3: Implement repeatable innermost-segment removal**

In `company_names/cleaning.py`, add the compiled expression beside the existing cleanup expressions:

```python
_PARENTHESIZED_RE = re.compile(r"\([^()]*\)")
```

Add this private helper before `clean_company_name`:

```python
def _remove_parenthesized_segments(name: str) -> str:
    """Remove every complete parenthesized segment, including nested ones."""
    while True:
        stripped = _PARENTHESIZED_RE.sub(" ", name)
        if stripped == name:
            return name
        name = stripped
```

Change the first line of `clean_company_name` from:

```python
name = _WHITESPACE_RE.sub(" ", _SEPARATOR_RE.sub(" ", raw_name)).strip()
```

to:

```python
name = _SEPARATOR_RE.sub(" ", raw_name)
name = _remove_parenthesized_segments(name)
name = _WHITESPACE_RE.sub(" ", name).strip()
```

This ordering ensures middle-of-name removal does not leave doubled spaces and ensures a legal suffix exposed by removal still uses the existing suffix logic.

- [ ] **Step 4: Run the complete cleaning suite and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_cleaning.py -v
```

Expected: all cleaning tests PASS, including unmatched-parenthesis preservation and the existing `normalize_lookup_key("()")` rejection.

- [ ] **Step 5: Commit the cleanup behavior**

```bash
git add company_names/cleaning.py tests/test_cleaning.py
git commit -m "feat: strip parenthesized company name text"
```

---

### Task 2: Identify Invalid Final-Name Rows

**Files:**
- Modify: `tests/test_service.py`
- Modify: `company_names/service.py`

**Interfaces:**
- Consumes: `_validated_final_names(cleaned_names: list[str], final_names: dict[str, str]) -> dict[str, str]`
- Produces: the same return type; raises `ServiceValidationError` containing the affected cleaned aliases when required values are absent, non-text, or blank

- [ ] **Step 1: Add failing validation-detail tests**

Add these tests beside the existing aggregate validation tests in `tests/test_service.py`:

```python
def test_aggregate_names_every_missing_or_blank_final_name() -> None:
    rows = pd.DataFrame([
        {"cleaned_name": "A", "rns": 1.0, "revenue": 10.0},
        {"cleaned_name": "B", "rns": 1.0, "revenue": 20.0},
        {"cleaned_name": "C", "rns": 1.0, "revenue": 30.0},
    ])

    with pytest.raises(ServiceValidationError) as caught:
        aggregate_resolved_rows(rows, {"A": "Final", "B": "  ", "C": None})

    assert str(caught.value) == (
        "Every cleaned company name needs a final company name. "
        "Missing or blank: B, C"
    )


def test_save_names_a_missing_alias_before_repository_write() -> None:
    prepared = prepare_aliases(
        extracted_rows([("A", 1, 10), ("B", 1, 20)]), None
    )
    repository = FakeAliasRepository([])

    with pytest.raises(ServiceValidationError, match=r"Missing or blank: B$"):
        save_alias_changes(prepared, {"A": "Final"}, repository)

    assert repository.saved == []


def test_save_rejects_an_unexpected_alias_before_repository_write() -> None:
    prepared = prepare_aliases(extracted_rows([("A", 1, 10)]), None)
    repository = FakeAliasRepository([])

    with pytest.raises(
        ServiceValidationError,
        match=r"unexpected cleaned names: Stale alias$",
    ):
        save_alias_changes(
            prepared,
            {"A": "Final", "Stale alias": "Old value"},
            repository,
        )

    assert repository.saved == []
```

The `None` value intentionally exercises runtime validation even though the public type annotation expects strings.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_service.py::test_aggregate_names_every_missing_or_blank_final_name \
  tests/test_service.py::test_save_names_a_missing_alias_before_repository_write \
  tests/test_service.py::test_save_rejects_an_unexpected_alias_before_repository_write -v
```

Expected: FAIL because the current message is only `Every cleaned company name needs a final company name`.

- [ ] **Step 3: Collect and report every invalid cleaned alias**

In `company_names/service.py`, remove the early exact-key-set check at the start of `save_alias_changes`:

```python
if set(final_names) != set(cleaned_names):
    raise ServiceValidationError(
        "Every cleaned company name needs a final company name"
    )
```

Call `_validated_final_names` first, then reject unexpected keys without obscuring missing required rows:

```python
trimmed = _validated_final_names(cleaned_names, final_names)
cleaned_name_set = set(cleaned_names)
unexpected = [name for name in final_names if name not in cleaned_name_set]
if unexpected:
    raise ServiceValidationError(
        "Final company name mapping contains unexpected cleaned names: "
        + ", ".join(unexpected)
    )
```

Replace the loop-time generic failure in `_validated_final_names` with collection followed by validation:

```python
invalid = [
    cleaned_name
    for cleaned_name in cleaned_names
    if not isinstance(final_names.get(cleaned_name), str)
    or not final_names[cleaned_name].strip()
]
if invalid:
    raise ServiceValidationError(
        "Every cleaned company name needs a final company name. "
        "Missing or blank: " + ", ".join(invalid)
    )

trimmed: dict[str, str] = {}
for cleaned_name in cleaned_names:
    trimmed[cleaned_name] = final_names[cleaned_name].strip()
return trimmed
```

The repository call remains after both validations, so invalid input cannot produce a partial write.

- [ ] **Step 4: Run service tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_service.py -v
```

Expected: all service tests PASS; the existing broad message matches continue to match the more specific prefix.

- [ ] **Step 5: Commit actionable validation**

```bash
git add company_names/service.py tests/test_service.py
git commit -m "fix: identify aliases missing final names"
```

---

### Task 3: Lock Separate Editable Alias Rows Before Aggregation

**Files:**
- Create: `tests/fixtures/shared_destination_alias_app.py`
- Modify: `tests/test_streamlit_smoke.py`

**Interfaces:**
- Consumes: `prepare_aliases(rows: pd.DataFrame, repository: AliasRepository | None) -> PreparedAliases`
- Consumes: `render_alias_editor(prepared: PreparedAliases, repository: AliasRepository | None) -> pd.DataFrame | None`
- Produces: a runtime regression contract in which aliases `A` and `B` both render with editable value `C`, while the saved result contains one aggregated `C` totals row

- [ ] **Step 1: Add a runtime test referencing the not-yet-created fixture**

At the top of `tests/test_streamlit_smoke.py`, add:

```python
SHARED_DESTINATION_FIXTURE_APP = (
    Path(__file__).parent / "fixtures" / "shared_destination_alias_app.py"
)
```

Add this test after the existing pagination runtime test:

```python
def test_saved_aliases_sharing_a_destination_stay_separate_until_save() -> None:
    app = AppTest.from_file(
        SHARED_DESTINATION_FIXTURE_APP, default_timeout=10
    ).run()
    assert not app.exception

    app.selectbox(key="alias_status_filter").select("Already saved").run()

    assert app.text_input(key="alias_final_a").value == "C"
    assert app.text_input(key="alias_final_b").value == "C"
    assert app.text_input(key="alias_final_a").disabled is False
    assert app.text_input(key="alias_final_b").disabled is False
    assert app.session_state["alias_edits"]["A"] == "C"
    assert app.session_state["alias_edits"]["B"] == "C"

    app.button(key="alias_next_top").click().run()
    assert app.session_state["alias_page"] == 2
    app.button(key="alias_previous_top").click().run()
    assert app.text_input(key="alias_final_a").value == "C"
    assert app.text_input(key="alias_final_b").value == "C"

    app.button(key="save_aliases").click().run()

    assert not app.error
    assert len(app.session_state["fixture_repository"].saved) == 145
    shared = app.session_state["fixture_result"].query(
        "`TRAVEL AGENT` == 'C'"
    )
    assert shared.to_dict("records") == [{
        "TRAVEL AGENT": "C",
        "Sum of RNS": 5.0,
        "Sum of R REVENUE": 50.0,
    }]
```

- [ ] **Step 2: Run the runtime test and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_streamlit_smoke.py::test_saved_aliases_sharing_a_destination_stay_separate_until_save -v
```

Expected: FAIL because `tests/fixtures/shared_destination_alias_app.py` does not exist yet.

- [ ] **Step 3: Create the controlled 145-alias Streamlit fixture**

Create `tests/fixtures/shared_destination_alias_app.py` with:

```python
"""Controlled app proving aliases stay distinct before final aggregation."""

from pathlib import Path
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from company_names.repository import AliasMapping
from company_names.service import prepare_aliases
from company_names.ui import render_alias_editor


class SharedDestinationRepository:
    def __init__(self) -> None:
        self.aliases = [
            AliasMapping("A", "a", "C"),
            AliasMapping("B", "b", "C"),
            *[
                AliasMapping(
                    f"Alias {index:03d}",
                    f"alias {index:03d}",
                    f"Canonical {index:03d}",
                )
                for index in range(2, 145)
            ],
        ]
        self.saved: list[AliasMapping] = []

    def list_aliases(self) -> list[AliasMapping]:
        return list(self.aliases)

    def upsert_aliases(self, mappings: list[AliasMapping]) -> None:
        self.saved.extend(mappings)


if "fixture_repository" not in st.session_state:
    st.session_state["fixture_repository"] = SharedDestinationRepository()

repository = st.session_state["fixture_repository"]
rows = pd.DataFrame([
    {"agent_name": "A", "rns": 2, "revenue": 20},
    {"agent_name": "B", "rns": 3, "revenue": 30},
    *[
        {
            "agent_name": f"Alias {index:03d}",
            "rns": 1,
            "revenue": index,
        }
        for index in range(2, 145)
    ],
])
prepared = prepare_aliases(rows, repository)
result = render_alias_editor(prepared, repository)
if result is not None:
    st.session_state["fixture_result"] = result
```

The fixture deliberately passes pre-aggregation report rows into `prepare_aliases`; it never reconstructs review rows from the aggregated result.

- [ ] **Step 4: Run the runtime regression and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_streamlit_smoke.py::test_saved_aliases_sharing_a_destination_stay_separate_until_save -v
```

Expected: PASS with two editable first-page fields `A [C]` and `B [C]`, 145 saved mappings, and only the final totals combining A and B under C.

If this test exposes a production-code failure instead of passing once the fixture exists, stop and apply the `systematic-debugging` workflow to the observed failure before changing `company_names/ui.py` or `company_names/service.py`.

- [ ] **Step 5: Run all UI and service integration coverage**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_ui.py tests/test_service.py tests/test_streamlit_smoke.py -v
```

Expected: all tests PASS with no Streamlit exceptions or validation errors.

- [ ] **Step 6: Commit the alias-form regression contract**

```bash
git add tests/fixtures/shared_destination_alias_app.py tests/test_streamlit_smoke.py
git commit -m "test: preserve aliases sharing a destination"
```

---

### Task 4: Full Verification

**Files:**
- Verify only; no expected modifications

**Interfaces:**
- Consumes: all behavior produced by Tasks 1–3
- Produces: evidence that deterministic cleaning, alias persistence, Streamlit state, PDF plumbing, authentication, and aggregation remain compatible

- [ ] **Step 1: Run formatting and whitespace validation**

Run:

```bash
git diff --check HEAD~3..HEAD
```

Expected: exit code 0 and no output.

- [ ] **Step 2: Run the full automated suite**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -v
```

Expected: every test PASS with no unexpected warnings or exceptions.

- [ ] **Step 3: Inspect the final scoped diff**

Run:

```bash
git status --short
git diff HEAD~3..HEAD -- \
  company_names/cleaning.py \
  company_names/service.py \
  tests/test_cleaning.py \
  tests/test_service.py \
  tests/test_streamlit_smoke.py \
  tests/fixtures/shared_destination_alias_app.py
```

Expected: only the planned production and test changes appear; the pre-existing untracked CSV and PDF directory remain untouched.

- [ ] **Step 4: Record final verification evidence**

In the implementation handoff, report:

```text
- Parenthetical cleanup examples verified
- A [C] and B [C] rendered as separate editable rows
- 145 saved aliases survived filter and page navigation
- A and B aggregated under C only after save
- Invalid final-name diagnostics identify cleaned aliases
- Full pytest result: copy the exact passing summary printed by Step 2
```

Do not create an empty verification-only commit.
