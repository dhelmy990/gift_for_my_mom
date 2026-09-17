# Canonical company formatting implementation plan

**Goal:** Eliminate formatting-only mapping collisions and reject invalid edits.
**Architecture:** Shared pure formatting/validation functions in cleaning.py,
consumed by source cleanup, suggestion generation, service preparation/save,
CSV import, and the existing Streamlit editor.

1. Add failing normalization and real Xiang conflict regression tests. Cover case,
   whitespace, Unicode compatibility, invisible artifacts, typography, blank,
   nontext, punctuation-only and oversized values; require idempotence.
2. Implement formatting in cleaning.py; apply it to suggested/saved/default values
   and CSV targets. Keep canonical legal suffixes. Give aliases sharing an existing
   lookup key a common deterministic default, without changing the key definition.
3. Validate final values at the save boundary and editor. Preserve invalid input
   for correction, show per-row errors and a summary across hidden pages, disable
   Save, and retain server validation for bypassed UI. Include both conflicting
   source names in genuine alias-key collision errors.
4. Update prior assertions to the newly required uppercase output while retaining
   mixed-case/dirty inputs. Run Streamlit interaction tests for invalid edits,
   correction, suggestion acceptance, and pagination.
5. Rerun the local ex PDF reproduction with cloned aliases; check immediate saves
   from both editor pages succeed and room nights/revenue remain conserved. Review,
   run the complete suite, and publish the verified app change to main.

## Verification results

- Formatting regressions failed before implementation, then passed, including
  the real mixed-case Xiang collision and strict save rejection.
- Streamlit interaction tests cover invalid input, correction, disabled saving,
  suggestions, and invalid edits retained across hidden pages.
- Review identified historical Unicode keys left stale after edits. Added failing
  save/reload regressions, then preserved these keys in the same upsert batch,
  including displaced companies absent from the current report.
- Replayed all 661 extracted rows from the five local example PDFs with a local
  alias snapshot: 300 cleaned rows, 295 final rows; immediate save and reload pass.
  All five PDFs individually pass. Room nights and revenue are conserved.
- The first two physical pages per PDF produce 46 source rows and 25 final rows;
  immediate save and reload pass. Real editor saves from pages 1 and 2 also pass.
- Reproduction artifacts remain ignored under `.superpowers/repros/ex-alias-save/`;
  no PDF contents, database snapshot, or company totals are committed.
- Complete suite: `248 passed`; `git diff --check` is clean.
- Independent final review: no remaining findings within scope; historical
  key preservation also covers chained Unicode displacement.
