from copy import deepcopy

import pytest

from company_names.models import Group, NameRecord, ReviewBoard
from company_names.ui import (
    _board_revision,
    _component_containers,
    add_selected_names,
    apply_sort_result,
    create_group,
    return_to_inventory,
    sortable_containers,
)


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
    assert next(item for item in items if item["name"] == "Alpha")["label"].startswith("🔵")
    assert next(item for item in items if item["name"] == "alpha")["label"].startswith("🟡")


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
