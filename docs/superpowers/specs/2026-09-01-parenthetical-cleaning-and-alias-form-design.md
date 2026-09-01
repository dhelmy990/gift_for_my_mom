# Parenthetical Cleaning and Alias Form Integrity Design

## Objective

Make two focused corrections to company-name handling:

1. Remove every complete parenthesized segment from a raw company name during deterministic cleanup.
2. Guarantee that the rename editor is built from distinct cleaned report aliases before aggregation, with each alias prepopulated from its saved destination and remaining editable.

## Parenthetical Cleanup

The deterministic cleaner removes every complete parenthesized segment wherever it appears in a company name. It then applies the existing whitespace, legal-suffix, punctuation, and empty-name validation rules.

Examples:

- `Hong Thai Travel Services (S) Pte` becomes `Hong Thai Travel Services`.
- `ACME (Singapore) Travel (Wholesale)` becomes `ACME Travel`.
- `Wendy Tour (S) P/L (Formerly SMI)` becomes `Wendy Tour P/L`.

Multiple and nested complete parenthesized segments are removed. An unmatched opening or closing parenthesis is not treated as a complete segment and remains subject only to the existing cleanup rules. If removal and the existing cleanup pipeline leave no company name, cleanup raises the existing empty-name error.

Because the rule applies to all complete parenthesized text, markers such as `(VCC)`, `(POA)`, `(B2B)`, and `(Singapore)` are also removed. Business aliases and canonical destinations remain repository data rather than deterministic cleanup rules.

## Rename Form Source and Identity

The rename form is built from the cleaned, normalized report rows before final-name aggregation. It contains exactly one editable row for each distinct cleaned alias in the current report.

For every row:

- the row identity and label are the cleaned report alias;
- an exact saved mapping prepopulates the editable field with its saved canonical destination;
- a new alias prepopulates the field with itself;
- a fuzzy suggestion remains advisory until explicitly accepted;
- filtering, searching, and pagination only control visibility and never change row identity or values.

For saved mappings:

```text
A -> C
B -> C
```

the form must show two independently editable rows:

```text
A  [C]
B  [C]
```

It must never use already aggregated totals to construct the form, because doing so would collapse both aliases into an incorrect single `C [C]` row.

## Save and Aggregation Flow

The save action submits a complete mapping for every cleaned alias in the current report, including aliases hidden by the active filter, search, or page. It validates every destination before writing.

Only after validation and persistence does aggregation replace each cleaned alias with its final destination and combine numeric totals. Thus `A -> C` and `B -> C` stay separate throughout review and combine only in the final totals under `C`.

Blank or invalid destinations remain errors. The validation message should identify the affected cleaned aliases so a hidden invalid value can be found without implying that the active filter caused the mapping to collapse.

## Editor State

The editor retains one staged value per cleaned alias across ordinary Streamlit reruns, filters, searches, and page navigation. Loading `Already saved` displays saved rows with their canonical destinations prepopulated and editable. It does not self-map aliases, erase values in other views, or scope the save operation to visible rows.

Starting a deliberately new report continues to clear prior editor state at the existing processing boundary.

## Testing

Automated tests will cover:

- removal of one, multiple, and nested complete parenthesized segments;
- whitespace normalization after middle-of-name removal;
- existing legal-suffix cleanup after parenthetical removal;
- rejection when cleanup leaves an empty name;
- two distinct cleaned aliases saved to one canonical destination remaining two editable, prepopulated review rows;
- aggregation combining those aliases only after final-name resolution;
- an `Already saved` view with many rows preserving values across filtering and pagination;
- saving all aliases, including hidden rows, without collapsing alias identity;
- validation identifying any cleaned aliases whose final values are blank.

The full existing test suite must continue to pass.
