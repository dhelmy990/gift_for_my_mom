# Paginated Alias Editor Design

## Objective

Make the company alias editor manageable for large reports by showing actionable names first and dividing rows into pages. The change is UI-only: it must not change cleanup, matching, persistence, authentication, aggregation, or Supabase behavior.

## Filter Controls

The editor begins with a `Show` dropdown containing:

1. `Needs review` — default; includes `new` and `suggested` rows.
2. `New names` — includes only `new` rows.
3. `Suggestions` — includes only `suggested` rows.
4. `Already saved` — includes only `saved` rows.
5. `All names` — includes every row in the current report.

Each option displays its current row count where Streamlit supports formatted option labels. Filtering uses the row's existing status and never changes that status or its saved destination.

The existing search field remains available. Search applies within the selected status filter and matches cleaned report names and current final company names case-insensitively.

Changing the status filter or search text resets the current page to page 1.

## Pagination Controls

A `Rows per page` dropdown offers `10`, `20`, `50`, and `100`, defaulting to `20`. Changing the page size resets the current page to page 1.

After filtering and searching, the editor calculates the total page count. It displays:

```text
Showing 1–20 of 47

[Previous]          Page 1 of 3          [Next]
```

`Previous` is disabled on the first page. `Next` is disabled on the final page. If there are multiple pages, page controls appear above and below the rows. If the current filter has no matching rows, the editor displays a clear empty-state message and no page controls.

The UI clamps stale page values to the valid range when the available row count changes.

## Edit State

Text edits are stored by the existing collision-safe alias widget key and survive:

- moving to another page;
- changing the status filter;
- searching;
- changing page size;
- ordinary Streamlit reruns.

Accepting a fuzzy suggestion updates the same persistent edit value. No fuzzy suggestion is applied automatically.

Saving submits final values for every report row, including rows hidden by the active filter, search, or page. Pagination must never cause hidden values to be omitted or reset.

Starting a new report retains the existing state-isolation behavior and clears filter, search, pagination, typed aliases, and staged password state.

## Layout

The compact layout is:

```text
Company name mappings

Show: [Needs review v]    Search: [________________]
Rows per page: [20 v]

Showing 1–20 of 47
[Previous]          Page 1 of 3          [Next]

Report name          Final company name          Status
HKTRMs               [HKTRMs________________]    Suggested
New Travel           [New Travel_____________]   New

[Previous]          Page 1 of 3          [Next]

Admin password: [________________]
[Save all changes and update totals]
```

The editor continues using ordinary Streamlit widgets with no custom HTML, CSS, JavaScript, drag-and-drop, pills, trays, or group board.

## Error and Edge Behavior

- A failed save keeps edits on all pages intact for retry.
- An incorrect password does not change the current page or filters, though the password value is still cleared after the attempt.
- Database-unavailable mode displays the existing diagnostic and keeps permanent saving disabled.
- A report containing only saved names defaults to `Needs review`, shows the empty-state explanation, and allows the user to select `Already saved` or `All names`.
- Counts and pagination reflect only the current report, never aliases that exist only in Supabase.

## Testing

Tests must cover:

- `Needs review` as the default filter;
- each filter's status membership and count;
- search after status filtering;
- page sizes of 10, 20, 50, and 100;
- correct page slicing, summary text, and disabled boundaries;
- resetting to page 1 after filter, search, or page-size changes;
- clamping a stale page after results shrink;
- edits surviving page and filter changes;
- saving values from visible and hidden pages together;
- suggestion acceptance surviving navigation;
- new-report state reset including pagination keys;
- an empty default view when all report names are already saved;
- Streamlit runtime rendering and page navigation without exceptions.

The existing full suite and opaque-PDF QA loop must continue to pass. Automated QA may upload files from `temporary_pdfs`, but must not inspect, extract, print, summarize, or otherwise expose their contents outside the application under test.
