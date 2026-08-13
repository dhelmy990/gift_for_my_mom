from copy import deepcopy

import pytest

from company_names.models import Group, NameRecord, ReviewBoard
from company_names.ui import (
    SEMANTIC_PILL_CSS,
    _board_revision,
    _bind_admin_session,
    _component_containers,
    _restore_container_ids,
    _semantic_pill_preview,
    name_status,
    review_widget_key,
    search_options,
    add_selected_names,
    apply_group_titles,
    apply_sort_result,
    apply_sort_result_changed,
    board_location_revision,
    create_group,
    return_to_inventory,
    sortable_containers,
)


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
    by_header["Excluded from this report"]["items"].append(alpha)

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


def test_create_group_uses_stable_new_prefix_and_preserves_empty_groups():
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
        "New Group",
        "Untitled group",
        "Untitled group",
        "Excluded from this report",
    ]


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
    assert name_status(state, "alpha") == "alpha — In inventory"
    assert name_status(state, "Beta") == "Beta — In inventory"
    assert name_status(state, "Gamma") == "Gamma — Excluded"


def test_searching_already_placed_names_does_not_move_or_duplicate_them():
    state = board()

    add_selected_names(state, ["Alpha", "Gamma", "Beta"])

    assert state.names["Alpha"].group_id == "existing"
    assert state.names["Gamma"].group_id is None
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
