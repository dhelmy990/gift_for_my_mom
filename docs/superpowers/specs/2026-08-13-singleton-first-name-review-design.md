# Singleton-First Company Name Review Design

## Objective

Replace the current group-first review board with a simple workflow in which every included report name is treated as its own canonical company by default. Users should work only on exceptions: names that need to be combined, moved out of an existing validated group, or excluded from the current report.

The interface must explain the task in ordinary language. It must not require users to understand implementation terms such as inventory, mappings, canonical keys, provisional groups, or submission payloads.

## Scope

This redesign is primarily a UI change. One small domain/service change is required: included names that remain outside an explicit combined group must be materialized as singleton groups when the user saves.

The redesign must not change:

- company-name cleaning rules;
- FastEmbed configuration or matching weights;
- exact validated mappings as the source of truth;
- Supabase tables, functions, security, or transaction behavior;
- authentication or authorization rules;
- room-night and revenue aggregation;
- retry and idempotency guarantees;
- backup file format or restoration behavior.

No Supabase migration is required.

## Core Mental Model

Every included name has one of four visible locations:

1. **Separate companies** — names that will remain individual singleton groups.
2. **Working tray** — temporary workspace for names the user is actively considering combining.
3. **Combined groups** — explicitly named groups containing two or more related names.
4. **Left out of this report** — names excluded from the current report only.

The ordinary flow is:

```text
Separate company
       │
       ▼
Working tray
       │
       ▼
Combined group

Every movement is reversible before Save.
```

Removing a name from the working tray means **return it to a separate company**. It never means deletion or exclusion.

## Initial State

### Previously unseen names

A cleaned report name with no exact permanent mapping starts under **Separate companies**. It is implicitly a singleton and does not require the user to create or title a group.

### Previously validated names

A name with an exact permanent mapping starts in its validated combined group. Existing validated mappings remain authoritative unless the user deliberately moves the name elsewhere.

### Suggestions

Similarity suggestions may help users discover possible combinations, but suggestions must not assign names automatically. Suggested names remain separate until the user moves them into the working tray or a group.

## Screen Structure

The review screen uses three clearly marked steps on one page:

```text
1. Find names  →  2. Combine duplicates  →  3. Review and save
```

The page must prioritize the first two steps and keep administrative utilities out of the main workflow.

## Find Names

The search field covers every cleaned name in the current report, regardless of location.

```text
Search company names
┌──────────────────────────────────────────────────────────────┐
│ Type a company name…                                         │
└──────────────────────────────────────────────────────────────┘
```

Each result shows its current location in plain language:

```text
Miki Travel                  Separate company
Miki-Travel                  Group: Miki Global
MIKI TRAVEL SG               Working tray
Miki Test Account            Left out of this report
```

Selecting or clicking a result moves it to the working tray. Selecting a name already in the tray is idempotent. A name may be moved to the tray from a separate company, a combined group, or the exclusion area.

The UI must not render every report name as a large initial wall of controls. Separate companies may be searchable and summarized, with a compact expandable list if needed.

## Working Tray

The working tray is the temporary workspace for possible duplicates.

```text
WORKING TRAY

[Miki Travel ×]  [Miki-Travel ×]  [MIKI TRAVEL SG ×]
```

Each tray pill has a visible and accessible action to return that name to **Separate companies**. Dragging or moving a name from the tray to a combined group is also supported.

The tray itself is never persisted as a group.

## Creating a Combined Group

A user creates a group only after placing at least two names in the working tray.

```text
Final company name
┌──────────────────────────────────────────────────────────────┐
│ Type the final company name                                  │
└──────────────────────────────────────────────────────────────┘

[Create combined group]
```

The final company name:

- is required before creation;
- may be a new value that did not appear in the report;
- is stored as the group's final canonical title;
- must remain editable after creation;
- must not conflict with another group after existing title normalization.

The create button is disabled until the tray contains at least two names and the title is valid. Validation messages must state how to resolve the problem.

If the normalized title already belongs to an existing group, the UI directs the user to move the tray names into that group instead of creating a duplicate.

After successful creation, all current tray names move into the new group and the tray becomes empty.

## Editing Combined Groups

Each group displays its final title and member names together:

```text
GROUP: Miki Travel                                  [Edit title]

[Miki Travel] [Miki-Travel] [MIKI TRAVEL SG]
```

Users can:

- edit the final title;
- move a member back to the working tray;
- move a tray name into the group;
- move names between groups through the tray or an equivalent clear control.

The UI does not provide group deletion in this version. An empty newly created group may remain invisible or be omitted from submission according to existing empty-group behavior. Existing permanent groups are never deleted.

## Separate Companies

Names outside an explicit group and outside the tray/exclusion area appear as separate companies.

They require no action. The UI explains this explicitly:

> Names left here will be saved as separate companies automatically.

A tray pill's remove action returns it here. A name removed from a previously validated group and returned here represents a deliberate permanent unmap from its old group. On Save, it becomes its own singleton group rather than silently returning to the old mapping.

## Exclusion

The exclusion destination is labeled **Left out of this report**.

Exclusion remains report-only:

- excluded rows do not contribute to final grouped totals;
- exclusion does not delete or change a permanent mapping;
- an excluded name can be returned to the working tray or to separate companies before Save.

Exclusion must remain visually and semantically distinct from returning a tray name to a singleton.

## Submission Behavior

When the user selects **Save mappings and show totals**, the service builds one atomic submission containing:

- all explicit combined groups and their included members;
- one new singleton group for each included, previously unseen name left under Separate companies;
- deliberate unmaps/remaps for members moved out of previously validated groups;
- no mapping mutation for report-only excluded names.

The singleton canonical title and member name both use the cleaned final string visible in the report.

The system must avoid recreating an unchanged existing exact mapping as a new singleton. Existing validated mappings that the user did not move remain unchanged.

The existing stable request identity, retry ledger, complete resolution validation, atomic RPC, and post-success aggregation rules continue to apply to the expanded payload.

## Save Review

Before saving, the UI shows a concise summary:

```text
Ready to save

182 separate companies
5 combined groups
12 combined names
2 names left out of this report
```

The primary action is labeled:

```text
[Save mappings and show totals]
```

The admin-password control appears immediately beside this final action and uses task-oriented language. Internal phrases such as “unlock permanent actions” should be replaced with plain language such as **Enter admin password to save**.

Validation errors appear near the relevant tray, group title, or save control. The page must not rely on a long generic error list when a local actionable message is possible.

## Backup and Recovery

Mapping backup is an administrative utility, not a review step. It moves into a collapsed section at the bottom:

```text
▸ Backup and recovery

  Download a CSV copy of all permanent company-name mappings.
  [Prepare backup file]
```

The wording must explain that the button reads the permanent mappings from Supabase and prepares a downloadable CSV. The existing authorization requirement, backup data, format, safety encoding, and download behavior do not change.

## Accessibility and Movement Controls

Drag and drop may remain available, but every movement must also have a clear click/select alternative. A user must be able to complete the entire workflow without dragging.

All controls use black or otherwise high-contrast readable text. Location and match status must not be conveyed by color alone. Existing exact/suggested indicators may remain as secondary hints, but the primary wording describes the name's current location and required action.

## State and Reruns

All review state remains scoped to the current prepared report/request. Streamlit reruns must preserve:

- separate, tray, group, and exclusion locations;
- typed but not yet submitted group titles;
- explicit title edits;
- the pending idempotency identity for an unchanged save attempt.

Changing uploads, changing mode, or processing a new report clears the prior review according to the existing fingerprint behavior. A drag or click update must be reflected consistently across every representation in the same visible rerun.

## Testing Requirements

Tests must cover:

- a report containing many distinct names without requiring manual group creation;
- untouched unseen names becoming singleton groups at submission;
- unchanged exact mappings not becoming duplicate singleton groups;
- a member deliberately removed from an exact group becoming a singleton and permanently unmapping from the former group;
- tray movement from every location and return to singleton;
- exclusion remaining report-only;
- group creation requiring at least two tray names and a typed valid title;
- a typed title not present in the report;
- duplicate normalized title rejection with an actionable message;
- moving tray names into an existing group;
- complete keyboard/click movement without drag and drop;
- backup controls remaining authorized but visually separated from review;
- unchanged atomic submission, retry, aggregation, and result behavior;
- Streamlit render smoke coverage sufficient to catch runtime-only UI failures.

## Success Criteria

A first-time user viewing a report of mostly distinct companies can understand that no action is required for most names. They can search for possible duplicates, collect them in the working tray, type one final title, create a combined group, undo any movement, exclude report-only rows, and save without manually creating singleton groups or learning database terminology.
