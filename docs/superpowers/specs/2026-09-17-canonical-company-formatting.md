# Canonical company formatting

The user requests uppercase company names, removal of edge spaces in the cleaning
function, normalization of suggestions, and rejection of formatting errors
reintroduced through the editor. The real ex PDFs reproduce a collision between
Xiang Long Holidays and Xiang long Holidays; saving from editor pages 1 and 2
submits hidden rows too. The first two physical PDF pages alone save successfully.

Use one shared formatting function in company_names/cleaning.py: Unicode NFKC,
uppercase, single ordinary spaces, no edge whitespace, ordinary equivalents of
typographic quotes/hyphens, and removal of zero-width space/BOM/word-joiner paste
artifacts. Reject remaining control/format/surrogate characters, blank or
punctuation-only names, and names over 2,000 characters. Keep accents, numbers,
non-Latin letters, ampersands, and meaningful punctuation; do not guess spelling
or merge lookalike letters from different scripts.

Source cleanup calls this function before existing suffix/parenthesis stripping.
Reviewed canonical destinations use formatting only, retaining legal suffixes.
Normalize historical saved aliases and suggestions in memory. Identical lookup
keys get a deterministic common default; genuinely conflicting reviewed saved
destinations must raise an explicit error rather than silently overwrite a value.

The native Streamlit text fields validate on edit submission (Enter/blur).
Invalid edits remain visible for correction, with a row-specific error and disabled
Save, including errors on hidden pages. The save boundary independently rejects
the same formatting errors. Thus lowercase cannot be accepted or persisted;
this does not claim to intercept every browser keystroke.

Importer normalizes both aliases and canonical targets. Do not rewrite the live
database wholesale; report saves persist the normalized reviewed rows normally.
If normalization changes a historical storage key, retain its association and
update that row in the same save batch as the normalized key. This avoids leaving
conflicting historical destinations behind after a reviewed edit. Current report
mappings take priority if they legitimately reuse a historical key, with the
displaced historical company preserved under its normalized key even if absent
from the current report.
Prove the full ex dataset saves unchanged defaults locally and conserves totals.
