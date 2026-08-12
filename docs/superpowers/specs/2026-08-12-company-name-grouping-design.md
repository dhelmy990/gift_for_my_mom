# Company Name Cleanup, Retrieval, and Persistent Grouping Design

Date: 2026-08-12

## Objective

Upgrade the Streamlit collation pipeline so it produces one clean final company name, suggests previously validated canonical groups, lets a human review and change those suggestions, permanently remembers submitted mappings, and aggregates room nights and revenue under each canonical title.

## Scope

The first release will:

- Remove approved corporate suffixes and erroneous text concatenated after them.
- Use FastEmbed and explainable string signals to suggest validated groups.
- Store validated groups, member mappings, and embeddings in Supabase.
- Provide a searchable, draggable pill-based review interface.
- Allow report-only exclusions and new groups with freely typed canonical titles.
- Automatically persist the reviewed state on an authorized submission.
- Permanently unmap a validated member removed from its group before submission.
- Combine and sum room nights and revenue under canonical titles.
- Protect permanent writes with a shared admin password.
- Provide a downloadable mapping backup.

The first release will not:

- Delete permanent groups.
- Persist report exclusions.
- Automatically accept similarity suggestions.
- Use groupings made in the current report as retrieval evidence before submission.
- Expose raw extracted names in the review interface.

## Terminology

- **Raw name:** Text extracted from a PDF. It is an internal intermediate value.
- **Cleaned name:** The authoritative final string produced by deterministic cleanup. This is the only name shown in the UI.
- **Canonical group:** A stable permanent group with a user-editable title.
- **Validated mapping:** A persistent relationship from one cleaned name to exactly one canonical group.
- **Suggestion:** An advisory candidate group retrieved for an unmapped cleaned name. It has no permanent effect until submission.
- **Exclusion:** A name omitted only from the current report.

## Architecture and Data Flow

The collation path will be divided into focused components:

1. The existing PDF extractor returns company names and numeric measures.
2. A name cleaner converts every raw name into one final cleaned string.
3. A mapping repository retrieves exact validated mappings from Supabase.
4. Unknown cleaned names go to the retrieval service.
5. The retrieval service embeds the name with FastEmbed and ranks validated groups using vector similarity plus fuzzy, token, and acronym signals.
6. The Streamlit review state initializes exact mappings as already placed and exposes suggestions for human review.
7. The user searches the current report's name inventory, spawns pills, and arranges them into groups or the report-only exclusion area.
8. An authorized submission validates the complete board and sends one atomic change set to Supabase.
9. The aggregator sums room nights and revenue for included members under each canonical title.

Exact validated mappings are the source of truth and always take priority. Retrieval never silently overrides them or creates permanent relationships.

## Name Cleanup

Cleanup returns a single string and will normalize whitespace and separators before removing the approved corporate suffix and anything erroneously attached after it.

The initial suffix vocabulary is:

- `Pte`
- `Pte Ltd`
- `Ltd`
- `Limited`
- `Co`
- `Co Ltd`
- `Co., Ltd`
- `Sdn Bhd`
- `GmbH`

Matching is case-insensitive and supports suffixes concatenated with trailing address or report text. The supplied CSV will inspire regression cases such as:

- `Kake Hotels Marketing Co.,LtdRoom` → `Kake Hotels Marketing`
- `Miki Travel LtdVintners Place` → `Miki Travel`
- `Within Earth Holidays Sdn BhdSuite` → `Within Earth Holidays`
- `Betoptop GmbHBüro Kornwestheim Stammheimer Straße` → `Betoptop`
- `Hong Thai Travel Services (S) Pte` → `Hong Thai Travel Services (S)`

Only cleaned names enter mapping, retrieval, review, persistence, and aggregation. Raw names are not displayed.

## Retrieval and Ranking

FastEmbed will run in the Streamlit process using `BAAI/bge-small-en-v1.5`, producing 384-dimensional embeddings. Supabase `pgvector` will store vectors for canonical groups and member names.

For an unknown cleaned name, candidate generation searches only previously validated permanent data. Ranking combines:

- Vector similarity to validated member names and canonical titles.
- Character-level fuzzy similarity.
- Normalized token overlap.
- Acronym and compact-code similarity.

The service returns a small ranked candidate list with the evidence needed to label it as a suggestion. Current unsubmitted report groupings do not affect retrieval. If embedding generation or vector search is unavailable, exact mapping continues to work and retrieval falls back to fuzzy, token, and acronym scoring.

## Review Interface

The implementation will closely follow the approved `search-and-place-v2` visual mockup and use high-contrast black text.

### Searchable inventory

- A search field filters cleaned names present in the current report.
- Clicking a search result creates a draggable pill in the working tray.
- Search results show whether a name is unselected, in the tray, grouped, or excluded.
- A cleaned name can have only one pill.

### Working tray

- The tray shows selected names not yet placed in a group.
- Validated and suggested names have distinct, accessible styles.
- Removing a pill returns it to the searchable inventory.

### Groups and exclusion

- Pills can move from the tray into groups, between groups, back to inventory, or into exclusion.
- Each group exposes an editable canonical-title field.
- Users can create empty groups and type a title not found in the report.
- Previously validated names initially appear in their saved groups.
- Exclusion is a visually separate drop area and applies only to this report.
- Group deletion is not exposed.

### Submission

- Submission is a separate, visually prominent final action.
- It is disabled while any included name remains ungrouped.
- The shared admin password is required for permanent writes and remains unlocked only for the browser session.

## Permanent Data Model

Supabase will use PostgreSQL with the `vector` extension.

### `name_groups`

- Stable UUID primary key.
- Unique, nonblank canonical title.
- A 384-dimensional title embedding.
- Creation and update timestamps.

Changing a title updates the one group record, so the new title applies globally to all members.

### `name_mappings`

- Cleaned member name as a unique key.
- Foreign key to one canonical group.
- A 384-dimensional member embedding.
- Creation and update timestamps.

The unique member key prevents a name from belonging to two groups. Empty groups remain stored because permanent group deletion is outside this release.

Row-level security will be enabled and public client access will not receive table policies. The deployed Streamlit server will use a Supabase service-role key stored only in Streamlit Secrets. Permanent writes additionally require the app's shared admin password.

## Seed CSV

`company_name_normalization_finetuning.csv` is validation and seed input:

- `input_text` is cleaned and stored as a member.
- `target_text` becomes the canonical group title.
- Nonblank left-side names must resolve to their right-side titles.
- Blank target values are unfinished data and are rejected, never treated as exclusions.
- Duplicate or contradictory mappings fail import with a useful error.

## Atomic Submission Semantics

The board is validated before any mutation:

- Every included name belongs to exactly one group.
- Every populated group has a nonblank title.
- Canonical titles are unique after normalization.
- Existing mappings cannot silently move to conflicting groups.
- The shared admin password is correct.

The application sends one reviewed change set to a PostgreSQL function. In one transaction, the function:

1. Creates new groups.
2. Renames existing groups globally.
3. Adds or updates approved mappings.
4. Deletes mappings for previously validated members deliberately removed from their original group.
5. Updates group and member embeddings.

Any error rolls back the full submission. Excluded names are omitted from the change set and never persisted as exclusions. The in-session board remains available so the user can correct or retry.

After a successful transaction, aggregation combines all included member rows and sums `RNS` and revenue under the canonical title, then sorts the final report by revenue as the existing collation does.

## Conflict Handling

If a submission would assign an already mapped name to a different permanent group without the reviewed board clearly removing it from the old group, submission is blocked. The UI identifies the conflicting pill and asks the user to move or remove it. There is no separate conflict-resolution wizard in the first release.

## Operational Behavior

- The UI shows whether Supabase is connected.
- Read or write failures produce actionable messages without discarding review state.
- FastEmbed model-load failures degrade to non-vector retrieval.
- An authorized mapping export provides a recoverable CSV backup.
- Local filesystem files are not treated as durable storage on Streamlit Community Cloud.

## Testing and Acceptance Criteria

Automated tests will cover:

- Suffix cleanup and CSV-inspired concatenated trailing text.
- Exact mapping priority.
- Vector, fuzzy, token, acronym, and fallback ranking.
- Search inventory and unique pill state transitions.
- Placement, movement, exclusion, and validation rules.
- Group creation and global rename.
- Permanent unmapping of a removed validated member.
- Atomic rollback on submission failure.
- Correct grouped sums for room nights and revenue.
- Rejection of blank seed targets.
- Authorization of permanent writes.

Acceptance scenarios include:

- `Miki Travel LtdVintners Place` becomes `Miki Travel`.
- `MTLVintners Place` can be suggested for the validated `Miki Travel` group.
- A user can search, spawn pills, arrange groups, create a clean custom title, and submit.
- Submitted mappings survive Streamlit restart and redeployment because Supabase stores them.
- Removing a validated mapping and submitting permanently unmaps it.
- Excluding a name affects only the current report.
- Final room-night and revenue totals combine under canonical titles.
