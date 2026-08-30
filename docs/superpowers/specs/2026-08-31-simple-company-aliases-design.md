# Simple Company Alias Mapping Design

## Objective

Replace the FastEmbed/RAG grouping system and its multi-table Supabase model with a deterministic company-name cleaner, a one-table persistent alias dictionary, and optional spelling-based suggestions. The user must be able to correct mappings in the Streamlit app, and approved corrections must persist automatically across sessions and deployments.

The final collated report must group rows by the resolved final company name and sum their numeric totals.

## Scope

Retain:

- deterministic legal-suffix and trailing-corruption cleanup;
- the regression fix that preserves names beginning with `Co`, including `COMPASS TRAVEL & TOUR PTE LTD`;
- automatic aggregation of rows sharing one final company name;
- Supabase and the existing administrator password solely for durable mapping storage;
- Streamlit Community Cloud deployment.

Remove:

- FastEmbed and embedding-model downloads;
- vector generation and vector similarity;
- the `pgvector` dependency and vector indexes;
- RAG and hybrid-ranking logic;
- persistent group objects and group membership records;
- the group board, working tray, pills, and drag-and-drop workflow;
- the submission ledger and complex group-submission payloads;
- mapping-backup controls and terminology.

The rollback must preserve unrelated PDF extraction and collation behavior unless a small change is required to apply resolved company names before aggregation.

## Name Resolution Pipeline

Each raw report name follows one ordered pipeline:

```text
Raw report name
      |
      v
Deterministic cleanup
      |
      v
Exact alias lookup in Supabase
      |
      +-- exact match ----------> saved canonical name
      |
      +-- no exact match
             |
             v
RapidFuzz comparison with saved aliases
             |
             v
Suggestion requiring user confirmation,
or cleaned name as the default final value
```

An exact saved mapping is authoritative. A fuzzy result is advisory and must never be saved or permanently applied without explicit user confirmation.

## Deterministic Cleanup

The existing cleaner remains responsible for:

- replacing `_` and `|` separators with spaces;
- normalizing whitespace;
- removing an approved legal suffix and corrupted content following that suffix;
- trimming wrapper and trailing punctuation;
- rejecting an empty result;
- preserving ambiguous words beginning with `Co` rather than treating their prefix as a suffix.

Cleanup is intentionally not responsible for business aliases such as `HKTRM` to `Hong Kong TUYI Business Travel Limited`. Those belong in the alias table.

## Persistent Data Model

Supabase contains one application table:

```text
company_aliases
+-----------------+-----------------+--------------------------+
| cleaned_alias   | alias_key       | canonical_name           |
+-----------------+-----------------+--------------------------+
| HKTRM           | hktrm           | Hong Kong TUYI Business… |
| HKTRMs          | hktrms          | Hong Kong TUYI Business… |
| MTLVintners ... | mtlvintners ... | Miki Travel              |
+-----------------+-----------------+--------------------------+
```

Required fields:

- `alias_key`: normalized case- and punctuation-insensitive lookup key and primary key;
- `cleaned_alias`: cleaned display value last saved for the alias;
- `canonical_name`: final company name, stored exactly as entered after trimming surrounding whitespace;
- `updated_at`: database timestamp refreshed on update.

The app accesses this table through the Supabase service-role key stored only in Streamlit secrets. Row-level access by anonymous browser clients remains disabled. Permanent writes additionally require the existing app administrator password.

Saving an existing `alias_key` updates that row. Therefore reassignment is an ordinary upsert, and concurrent changes use last-write-wins behavior.

## Exact and Fuzzy Matching

Exact lookup uses the cleaner's normalized lookup key. Exact aliases always take precedence over suggestions.

For unresolved names, RapidFuzz compares the new normalized alias with previously saved normalized aliases. The initial implementation uses a conservative threshold of 90. Scores below the threshold are hidden. When multiple candidates tie for the best score, the app does not choose between them automatically and leaves resolution to the user.

A suggestion displays:

- the similar saved alias;
- its canonical destination;
- its similarity score.

Accepting a suggestion copies its canonical destination into the editable final-name field. The mapping becomes permanent only when the user saves changes.

## Review Interface

After processing the uploaded reports, the app presents the collated results and a compact mapping editor for names in the current report.

```text
+- Company name mappings ---------------------------------------+
| Search: [____________________________________________]        |
|                                                               |
| Report name          Final company name            Status     |
| HKTRM                Hong Kong TUYI Business ...    Saved      |
| HKTRMs               [Hong Kong TUYI Business... ] Suggested  |
| New Travel           [New Travel________________ ] New        |
|                                                               |
|                                   [Save all changes]          |
+---------------------------------------------------------------+
```

Behavior by row:

- An exact saved alias displays its canonical name and `Saved` status.
- A high-confidence fuzzy candidate displays as an unconfirmed suggestion.
- A name without an exact mapping defaults to its cleaned name and displays `New` status.
- The final-name field accepts a title that never appeared in the report.
- The user may replace any final name, including an existing saved mapping.
- Setting the final name equal to the cleaned report name restores a self-mapping.

Only aliases present in the current report appear in the editor. The search field filters those rows. There are no groups, trays, pills, drag targets, or separate group-submission concepts.

The primary action is `Save all changes and update totals`. It validates the administrator password, upserts changed mappings, re-resolves the report, combines rows sharing the same canonical name, sums their numeric totals, and shows the updated result.

## Seed CSV

The untracked session file `company_name_normalization_finetuning.csv` has columns:

```text
input_text,target_text,remarks
```

It contains 24 alias examples. At design time, the deterministic cleaner alone matched 6 of the 24 target values case-insensitively. The remaining 18 contain abbreviations, translations, codes, or other business knowledge that deterministic suffix stripping cannot infer.

A repeatable administrative seed/import command will translate each row as follows:

- clean `input_text` to obtain `cleaned_alias`;
- normalize `cleaned_alias` to obtain `alias_key`;
- trim surrounding whitespace from `target_text` and preserve the remaining string exactly as `canonical_name`;
- upsert the resulting row into `company_aliases`.

After seeding, all 24 CSV inputs must resolve exactly to their specified `target_text` values. Re-running the import updates existing aliases without creating duplicates.

The CSV must not become a runtime database or silently override edits made in the app. It is seed and validation data only.

## Aggregation

Resolution occurs before aggregation:

1. Clean every extracted company name.
2. Resolve exact aliases from Supabase.
3. Use the cleaned name when no exact mapping has been saved.
4. Group rows by the resulting final company-name string.
5. Sum all numeric report columns using the existing floating-point-safe behavior.
6. Display the aggregated report.

Fuzzy suggestions do not affect totals until the user confirms and saves them.

## Failure Handling

- If Supabase is unavailable, PDF extraction and deterministic stripping continue. The app uses cleaned names, displays the underlying safe connection diagnostic, and disables permanent mapping edits.
- A failed write leaves the current report and the user's edited field values visible so the user can retry.
- Empty aliases and empty canonical names are rejected with a message beside the relevant action.
- Invalid seed rows report their CSV row number and value and do not produce a partial silent import.
- Ambiguous fuzzy ties remain unresolved rather than being chosen arbitrarily.
- Supabase credentials and administrator-password values are never displayed or sent to browser-side code.

## Database Migration

The replacement migration creates `public.company_aliases`, its `updated_at` trigger, and service-role-only permissions. It does not require the vector extension.

Legacy RAG/group tables and functions are no longer used by the application. The implementation must first stop all runtime references to them. Destructive deletion of legacy Supabase objects is outside this change; optional cleanup SQL may be documented separately so rollback remains possible.

## Dependency Changes

Retain `rapidfuzz` and the Supabase client. Remove `fastembed` and any dependency used solely by the old drag-and-drop or vector workflow after verifying it has no remaining imports.

## Verification Requirements

Automated tests must cover:

- every seed CSV input resolving to its exact target after import;
- legal-suffix and corrupted-trailing-text cleanup;
- preservation of `COMPASS TRAVEL & TOUR PTE LTD` as `COMPASS TRAVEL & TOUR`;
- exact alias precedence over fuzzy suggestions;
- a close spelling variant such as `HKTRMs` suggesting the saved `HKTRM` destination;
- scores below the configured threshold remaining hidden;
- ambiguous best-score ties remaining unresolved;
- accepting, editing, and permanently remapping an alias;
- restoring a self-mapping;
- rows with one canonical destination combining and summing correctly;
- Supabase read and write failures retaining usable report state;
- repeatable seed import behavior;
- a Streamlit runtime smoke test of the simplified editor.

The implementation is complete only when the application has no runtime dependency on embeddings, vector search, group persistence, or the former review board.
