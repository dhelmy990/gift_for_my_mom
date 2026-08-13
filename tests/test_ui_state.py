from copy import deepcopy

import pytest

from company_names.models import Group, NameRecord, ReviewBoard
from company_names.review import validate_board
from company_names.ui import (
    SEMANTIC_PILL_CSS,
    _board_revision,
    _bind_admin_session,
    _component_containers,
    _display_groups,
    _sortable_groups,
    _restore_container_ids,
    _review_styles,
    _move_record,
    _move_to_tray_callback,
    _return_to_separate_callback,
    _semantic_pill_preview,
    name_status,
    review_widget_key,
    search_options,
    separate_company_names,
    add_selected_names,
    apply_group_titles,
    apply_sort_result,
    apply_sort_result_changed,
    board_location_revision,
    create_group,
    create_combined_group,
    group_creation_error,
    group_title_errors,
    matching_group_for_title,
    move_to_tray,
    move_tray_to_group,
    review_summary,
    review_errors,
    semantic_pill,
    return_to_separate,
    return_to_inventory,
    sortable_containers,
)


def test_review_styles_render_css_braces_without_python_interpolation():
    styles = _review_styles()

    assert ".name-review, .name-review * { color: #000 !important; }" in styles
    assert SEMANTIC_PILL_CSS.strip() in styles


def test_admin_session_unlock_is_bound_to_configured_password_digest():
    session = {}
    attempts = _bind_admin_session(session, "first-secret")
    session["mapping_admin_unlocked"] = True
    attempts.record_failure(0.0)

    same = _bind_admin_session(session, "first-secret")
    assert same is attempts
    assert session["mapping_admin_unlocked"] is True

    changed = _bind_admin_session(session, "rotated-secret")
    assert session["mapping_admin_unlocked"] is False
    assert changed.failure_count == 0
    assert "first-secret" not in repr(session)
    assert "rotated-secret" not in repr(session)


def board() -> ReviewBoard:
    return ReviewBoard(
        groups={
            "existing": Group("existing", "Alpha Group", True),
            "new-old": Group("new-old", "New Group", False),
        },
        names={
            "Alpha": NameRecord("Alpha", "existing", "exact", True),
            "alpha": NameRecord("alpha", None, "suggested", False),
            "Beta": NameRecord("Beta", None, "unknown", False),
            "Gamma": NameRecord("Gamma", None, "unknown", True),
        },
    )


def containers_by_header(value):
    return {container["header"]: container for container in value}


def test_selecting_inventory_adds_each_name_to_working_tray_once():
    state = board()

    add_selected_names(state, ["Beta", "Beta", "alpha"])

    assert state.names["Beta"].selected is True
    assert state.names["alpha"].selected is True
    assert state.names["Beta"].group_id is None
    assert state.names["Beta"].excluded is False
    assert [item["name"] for item in sortable_containers(state)[0]["items"]] == [
        "alpha",
        "Beta",
        "Gamma",
    ]


def test_move_to_tray_resets_separate_grouped_and_excluded_names_idempotently():
    state = board()
    state.names["Beta"].persisted_name = "Beta-Ltd"
    state.names["Gamma"].excluded = True

    move_to_tray(state, ["Beta", "Alpha", "Gamma", "Alpha"])
    move_to_tray(state, ["Beta", "Alpha", "Gamma"])

    for name in ["Beta", "Alpha", "Gamma"]:
        record = state.names[name]
        assert (record.selected, record.group_id, record.excluded) == (True, None, False)
    assert state.names["Beta"].persisted_name == "Beta-Ltd"


def test_move_to_tray_validates_every_key_before_mutating():
    state = board()
    before = deepcopy(state)

    with pytest.raises(KeyError, match="Missing"):
        move_to_tray(state, ["Beta", "Missing"])

    assert state == before


def test_return_to_separate_preserves_identity_metadata():
    state = board()
    state.names["Alpha"].persisted_name = "Alpha-Ltd"

    return_to_separate(state, "Alpha")

    assert (
        state.names["Alpha"].selected,
        state.names["Alpha"].group_id,
        state.names["Alpha"].excluded,
    ) == (False, None, False)
    assert state.names["Alpha"].source == "exact"
    assert state.names["Alpha"].persisted_name == "Alpha-Ltd"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("   ", "Enter the final company name."),
        ("Ltd.", "Enter a usable final company name."),
    ],
)
def test_group_creation_error_validates_title(title, expected):
    state = board()
    move_to_tray(state, ["Beta", "Gamma"])

    assert group_creation_error(state, title) == expected


def test_group_creation_error_requires_two_tray_names_before_normalizing_title():
    state = board()

    assert group_creation_error(state, "Ltd.") == "Add at least two names to the working tray."


def test_group_creation_error_reports_ambiguous_normalized_title_conflict():
    state = board()
    state.groups["aaa"] = Group("aaa", "ALPHA_GROUP PTE LTD", False)
    move_to_tray(state, ["Beta", "Gamma"])

    assert group_creation_error(state, " alpha group ltd. ") == (
        "More than one existing group uses that final company name."
    )


def test_hidden_matching_group_can_receive_every_tray_name_directly():
    state = board()
    state.groups["hidden"] = Group("hidden", "Hidden Destination", True)
    move_to_tray(state, ["Beta", "Gamma"])

    match = matching_group_for_title(state, " hidden destination pte ltd ")

    assert match is state.groups["hidden"]
    assert group_creation_error(state, "hidden destination") == (
        "A group named ‘Hidden Destination’ already exists. "
        "Move these names into that group instead."
    )
    assert "hidden" not in {
        group.id for group in _display_groups(state, {"existing"})
    }
    move_tray_to_group(state, match.id)
    assert all(state.names[name].group_id == "hidden" for name in ["Beta", "Gamma"])
    assert review_summary(state)["tray"] == 0


def test_matching_group_for_title_rejects_ambiguous_normalized_matches():
    state = board()
    state.groups["duplicate"] = Group("duplicate", "Alpha Group Ltd", True)

    with pytest.raises(ValueError, match="Multiple groups"):
        matching_group_for_title(state, "alpha group")


def test_move_tray_to_group_validates_destination_before_mutating():
    state = board()
    move_to_tray(state, ["Beta", "Gamma"])
    before = deepcopy(state)

    with pytest.raises(KeyError, match="missing"):
        move_tray_to_group(state, "missing")

    assert state == before


def test_group_title_errors_reports_blank_unusable_and_both_duplicate_titles():
    state = board()
    state.groups["other"] = Group("other", "Other", False)

    assert group_title_errors(state, {"existing": " "}) == {
        "existing": "Enter a final company name.",
    }

    state.names["Beta"].selected = True
    state.names["Beta"].group_id = "new-old"
    assert group_title_errors(
        state, {"existing": "Alpha Group", "new-old": "Ltd."}
    ) == {"new-old": "Enter a usable final company name."}
    errors = group_title_errors(state, {"existing": "Same Pte Ltd", "new-old": " same "})
    assert errors == {
        "existing": "Another group uses the same final company name.",
        "new-old": "Another group uses the same final company name.",
    }


def test_every_local_group_title_error_also_blocks_board_validation():
    state = board()
    state.names["Beta"].selected = True
    state.names["Beta"].group_id = "new-old"

    for proposed in (
        {"existing": "", "new-old": "New Group"},
        {"existing": "Ltd.", "new-old": "New Group"},
        {"existing": "Same", "new-old": "same Pte Ltd"},
    ):
        local_errors = group_title_errors(state, proposed)
        candidate = deepcopy(state)
        apply_group_titles(candidate, proposed)

        assert local_errors
        assert validate_board(candidate)


def test_empty_existing_group_title_error_is_in_review_save_errors():
    state = board()
    move_to_tray(state, ["Alpha"])
    proposed = {"existing": ""}
    local_errors = group_title_errors(state, proposed)
    apply_group_titles(state, proposed)

    errors = review_errors(state, local_errors)

    assert local_errors == {"existing": "Enter a final company name."}
    assert any("Enter a final company name." in error for error in errors)


def test_direct_member_callbacks_move_between_tray_and_separate():
    state = board()

    _move_to_tray_callback(state, "Alpha")
    assert name_status(state, "Alpha") == "Alpha — Working tray"

    _return_to_separate_callback(state, "Alpha")
    assert name_status(state, "Alpha") == "Alpha — Separate company"


def test_create_combined_group_moves_all_tray_names_and_preserves_metadata():
    state = board()
    state.names["Beta"].persisted_name = "Beta-Ltd"
    move_to_tray(state, ["Beta", "Gamma"])

    group = create_combined_group(state, "  Beta Gamma Holdings  ")

    assert group.id.startswith("new-")
    assert state.groups[group.id] is group
    assert group.canonical_title == "Beta Gamma Holdings"
    assert group.existing is False
    assert all(state.names[name].group_id == group.id for name in ["Beta", "Gamma"])
    assert state.names["Beta"].persisted_name == "Beta-Ltd"
    assert review_summary(state)["tray"] == 0


def test_create_combined_group_rejects_invalid_state_without_mutation():
    state = board()
    before = deepcopy(state)

    with pytest.raises(ValueError, match="at least two"):
        create_combined_group(state, "New title")

    assert state == before


def test_review_summary_counts_locations_and_only_referenced_groups():
    state = board()
    state.names["Gamma"].excluded = True
    move_to_tray(state, ["Beta"])

    assert review_summary(state) == {
        "separate": 1,
        "combined_groups": 1,
        "combined_names": 1,
        "tray": 1,
        "excluded": 1,
    }


def test_sortable_uses_opaque_unique_ids_for_case_differing_labels():
    state = board()
    add_selected_names(state, ["alpha"])

    containers = sortable_containers(state)
    items = [item for container in containers for item in container["items"]]

    assert {item["name"] for item in items} >= {"Alpha", "alpha"}
    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids))
    assert not any(item["id"] in {"Alpha", "alpha"} for item in items)
    assert next(item for item in items if item["name"] == "Alpha")["label"].startswith("🟦")
    assert next(item for item in items if item["name"] == "alpha")["label"].startswith("🟨")


def test_sortable_moves_names_between_tray_group_and_exclusion():
    state = board()
    containers = sortable_containers(state)
    by_header = containers_by_header(containers)
    gamma = by_header["Working tray"]["items"].pop()
    by_header["Alpha Group"]["items"].append(gamma)
    alpha = by_header["Alpha Group"]["items"].pop(0)
    by_header["Left out of this report"]["items"].append(alpha)

    apply_sort_result(state, containers)

    assert state.names["Gamma"].group_id == "existing"
    assert state.names["Gamma"].excluded is False
    assert state.names["Alpha"].group_id is None
    assert state.names["Alpha"].excluded is True


def test_location_revision_is_immutable_sorted_and_tracks_only_placement():
    state = board()

    revision = board_location_revision(state)
    state.groups["existing"].canonical_title = "Renamed"

    assert board_location_revision(state) == revision
    assert revision == tuple(sorted(revision))
    state.names["Gamma"].excluded = True
    assert board_location_revision(state) != revision


def test_apply_sort_result_changed_reports_a_new_placement():
    state = board()
    containers = sortable_containers(state)
    gamma = containers[0]["items"].pop()
    containers[1]["items"].append(gamma)

    assert apply_sort_result_changed(state, containers) is True
    assert state.names["Gamma"].group_id == "existing"


def test_apply_sort_result_changed_ignores_reordering_with_same_placements():
    state = board()
    add_selected_names(state, ["Beta"])
    containers = sortable_containers(state)
    containers[0]["items"].reverse()

    assert apply_sort_result_changed(state, containers) is False


def test_missing_sortable_item_is_rejected_without_state_corruption():
    state = board()
    before = deepcopy(state)
    containers = sortable_containers(state)
    containers[0]["items"].clear()

    with pytest.raises(ValueError, match="every selected name exactly once"):
        apply_sort_result(state, containers)

    assert state == before


def test_duplicate_or_unknown_sortable_item_is_rejected():
    state = board()
    containers = sortable_containers(state)
    containers[0]["items"].append(containers[1]["items"][0])

    with pytest.raises(ValueError, match="every selected name exactly once"):
        apply_sort_result(state, containers)


def test_apply_sort_result_requires_each_container_exactly_once():
    state = board()
    before = deepcopy(state)
    containers = sortable_containers(state)
    containers.pop(2)  # Empty new group can still not disappear transiently.

    with pytest.raises(ValueError, match="board containers"):
        apply_sort_result(state, containers)

    assert state == before


def test_return_to_inventory_is_explicit_and_clears_all_placement_state():
    state = board()

    return_to_inventory(state, "Alpha")

    record = state.names["Alpha"]
    assert record.selected is False
    assert record.group_id is None
    assert record.excluded is False


def test_sortable_board_does_not_render_empty_group_containers():
    state = board()

    first = create_group(state)
    second = create_group(state)

    assert first.id.startswith("new-")
    assert second.id.startswith("new-")
    assert first.id != second.id
    assert first.canonical_title == ""
    headers = [item["header"] for item in sortable_containers(state)]
    assert headers == [
        "Working tray",
        "Alpha Group",
        "Left out of this report",
    ]


def test_emptied_original_group_remains_displayed_but_not_sortable():
    state = board()

    move_to_tray(state, ["Alpha"])

    assert [group.id for group in _display_groups(state, {"existing"})] == ["existing"]
    assert _sortable_groups(state) == []
    containers = sortable_containers(state)
    assert "Alpha Group" not in [container["header"] for container in containers]

    _move_record(state, "Alpha", "group:existing")
    assert state.names["Alpha"].group_id == "existing"
    assert state.names["Alpha"].selected is True


def test_empty_new_group_is_hidden_but_empty_existing_group_is_visible():
    state = board()
    empty_new = create_group(state)
    move_to_tray(state, ["Alpha"])

    displayed_ids = [group.id for group in _display_groups(state, {"existing"})]

    assert "existing" in displayed_ids
    assert "new-old" not in displayed_ids
    assert empty_new.id not in displayed_ids


def test_unrelated_existing_candidate_is_not_displayed_for_current_report():
    state = board()
    state.groups["unrelated"] = Group("unrelated", "Unrelated", True)

    assert [group.id for group in _display_groups(state, {"existing"})] == ["existing"]


def test_populated_group_is_sortable_even_when_not_an_original_exact_group():
    state = board()
    state.names["Beta"].selected = True
    state.names["Beta"].group_id = "new-old"

    assert [group.id for group in _sortable_groups(state)] == ["existing", "new-old"]


def test_unknown_selection_and_inventory_moves_are_rejected():
    state = board()

    with pytest.raises(KeyError):
        add_selected_names(state, ["Missing"])
    with pytest.raises(KeyError):
        return_to_inventory(state, "Missing")


def test_component_container_ids_remain_unique_when_titles_match():
    state = board()
    state.groups["existing"].canonical_title = "Same"
    state.groups["new-old"].canonical_title = "Same"
    state.names["Beta"].selected = True
    state.names["Beta"].group_id = "new-old"

    rendered = _component_containers(sortable_containers(state))

    assert rendered[1]["header"].startswith("Same")
    assert rendered[2]["header"].startswith("Same")
    assert rendered[1]["header"] != rendered[2]["header"]


def test_board_revision_changes_for_non_drag_mutations():
    state = board()
    initial = _board_revision(state)
    add_selected_names(state, ["Beta"])

    assert _board_revision(state) != initial

    selected = _board_revision(state)
    state.groups["existing"].canonical_title = "Renamed"
    assert _board_revision(state) != selected


def test_selected_name_with_missing_group_is_rejected_actionably():
    state = board()
    state.names["Alpha"].group_id = "deleted-group"

    with pytest.raises(ValueError, match="Alpha.*deleted-group"):
        sortable_containers(state)


def test_returned_containers_are_restored_by_opaque_header_not_position():
    state = board()
    source = sortable_containers(state)
    returned = list(reversed(_component_containers(source)))

    restored = _restore_container_ids(returned, source)

    assert [container["id"] for container in restored] == list(
        reversed([container["id"] for container in source])
    )
    apply_sort_result(state, restored)
    assert state.names["Alpha"].group_id == "existing"
    assert state.names["Gamma"].group_id is None


@pytest.mark.parametrize("mutation", ["tampered", "visible_tampered", "duplicate", "missing"])
def test_invalid_returned_container_headers_do_not_mutate_board(mutation):
    state = board()
    before = deepcopy(state)
    source = sortable_containers(state)
    returned = _component_containers(source)
    if mutation == "tampered":
        returned[0]["header"] += "x"
    elif mutation == "visible_tampered":
        returned[0]["header"] = "Wrong title" + returned[0]["header"][len("Working tray") :]
    elif mutation == "duplicate":
        returned[0]["header"] = returned[1]["header"]
    else:
        returned.pop()

    with pytest.raises(ValueError, match="board containers"):
        restored = _restore_container_ids(returned, source)
        apply_sort_result(state, restored)

    assert state == before


def test_semantic_preview_uses_blue_exact_and_gold_suggested_pills():
    state = board()
    add_selected_names(state, ["alpha"])

    preview = _semantic_pill_preview(state)

    assert 'background:#8FC5FF' in preview
    assert 'aria-label="Exact match: Alpha"' in preview
    assert 'background:#FFD166' in preview
    assert 'aria-label="Suggested match: alpha"' in preview


def test_semantic_pill_escapes_name_and_exposes_source_without_color_alone():
    record = NameRecord('<script>"Alias"</script>', None, "exact", True)

    rendered = semantic_pill(record)

    assert "<script>" not in rendered
    assert "&lt;script&gt;&quot;Alias&quot;&lt;/script&gt;" in rendered
    assert 'class="semantic-pill source-exact"' in rendered
    assert 'aria-label="Exact match: &lt;script&gt;&quot;Alias&quot;&lt;/script&gt;"' in rendered


def test_semantic_preview_rejects_invalid_source_actionably():
    state = board()
    state.names["Alpha"].source = "future-value"

    with pytest.raises(ValueError, match="Alpha.*future-value"):
        _semantic_pill_preview(state)


def test_every_report_name_is_searchable_with_current_status():
    state = board()
    state.names["Gamma"].excluded = True
    state.names["Gamma"].selected = True

    assert search_options(state) == ["Alpha", "alpha", "Beta", "Gamma"]
    assert name_status(state, "Alpha") == "Alpha — Group: Alpha Group"
    assert name_status(state, "alpha") == "alpha — Separate company"
    assert name_status(state, "Beta") == "Beta — Separate company"
    assert name_status(state, "Gamma") == "Gamma — Left out of this report"


def test_accessible_move_path_reaches_every_plain_language_destination():
    state = board()

    _move_record(state, "Beta", "Working tray")
    assert name_status(state, "Beta") == "Beta — Working tray"
    _move_record(state, "Beta", "group:existing")
    assert name_status(state, "Beta") == "Beta — Group: Alpha Group"
    _move_record(state, "Beta", "Left out of this report")
    assert name_status(state, "Beta") == "Beta — Left out of this report"
    _move_record(state, "Beta", "Separate companies")
    assert name_status(state, "Beta") == "Beta — Separate company"


def test_separate_company_names_returns_196_names_sorted_without_widgets():
    names = [f"Company {number:03d}" for number in range(196, 0, -1)]
    state = ReviewBoard(
        groups={},
        names={name: NameRecord(name, None, "unknown", False) for name in names},
    )

    assert separate_company_names(state) == sorted(names)


def test_separate_company_names_only_contains_automatic_singletons():
    state = board()
    state.names["Gamma"].excluded = True

    assert separate_company_names(state) == ["alpha", "Beta"]


def test_searching_already_placed_names_moves_them_to_tray_without_duplicates():
    state = board()
    state.names["Gamma"].excluded = True

    add_selected_names(state, ["Alpha", "Gamma", "Beta"])

    assert state.names["Alpha"].group_id is None
    assert state.names["Gamma"].group_id is None
    assert state.names["Gamma"].excluded is False
    items = [
        item["name"]
        for container in sortable_containers(state)
        for item in container["items"]
    ]
    assert items.count("Alpha") == 1


def test_review_widget_keys_are_namespaced_by_request_and_parts():
    assert (
        review_widget_key("request-1", "group_title", "same-group")
        == "name_review:request-1:group_title:same-group"
    )
    assert review_widget_key(
        "request-2", "group_title", "same-group"
    ) != review_widget_key("request-1", "group_title", "same-group")


def test_semantic_preview_is_per_container_and_escapes_user_names():
    state = board()
    state.names["<script>alert(1)</script>"] = NameRecord(
        "<script>alert(1)</script>", None, "suggested", True
    )

    preview = _semantic_pill_preview(state)

    assert 'data-container="working"' in preview
    assert 'class="semantic-pill source-exact"' in preview
    assert 'class="semantic-pill source-suggested"' in preview
    assert "<script>" not in preview
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in preview


def test_semantic_pill_css_colors_each_source_class():
    assert ".source-exact" in SEMANTIC_PILL_CSS
    assert "#8FC5FF" in SEMANTIC_PILL_CSS
    assert ".source-suggested" in SEMANTIC_PILL_CSS
    assert "#FFD166" in SEMANTIC_PILL_CSS
    assert ".source-unknown" in SEMANTIC_PILL_CSS


def test_applying_group_titles_before_preview_uses_the_new_container_title():
    state = board()

    apply_group_titles(state, {"existing": "Renamed Group"})
    preview = _semantic_pill_preview(state)

    assert "Renamed Group" in preview
    assert "Alpha Group" not in preview
