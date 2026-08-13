from dataclasses import dataclass

from company_names.review_session import (
    clear_final_results,
    compute_upload_fingerprint,
    merge_custom_excluded_agent,
    reconcile_final_results,
    reconcile_prepared_review,
)


@dataclass
class Upload:
    name: str
    size: int
    file_id: str


def test_upload_fingerprint_is_stable_and_changes_for_mode_or_file_identity():
    files = [Upload("a.pdf", 10, "one"), Upload("b.pdf", 20, "two")]
    first = compute_upload_fingerprint(True, files)

    assert compute_upload_fingerprint(True, files) == first
    assert compute_upload_fingerprint(False, files) != first
    assert compute_upload_fingerprint(True, files[:-1]) != first
    assert compute_upload_fingerprint(True, [Upload("a.pdf", 10, "replacement")]) != compute_upload_fingerprint(True, [Upload("a.pdf", 10, "one")])


def test_fingerprint_fallback_hashes_content_without_consuming_file_pointer():
    import io

    upload = io.BytesIO(b"pdf content")
    upload.name = "report.pdf"
    upload.seek(3)

    fingerprint = compute_upload_fingerprint(True, [upload])

    assert upload.tell() == 3
    upload2 = io.BytesIO(b"different")
    upload2.name = "report.pdf"
    assert compute_upload_fingerprint(True, [upload2]) != fingerprint


def test_reconciliation_hides_and_clears_stale_review_state():
    state = {
        "prepared_name_review": object(),
        "prepared_name_review_fingerprint": "old",
        "final_results": object(),
        "final_results_fingerprint": "old-board",
        "name_review:old-request:group_title:g": "stale",
        "unrelated": "keep",
    }

    assert reconcile_prepared_review(state, True, "new") is None
    assert state == {"unrelated": "keep"}


def test_reconciliation_returns_only_matching_collation_review():
    prepared = object()
    state = {
        "prepared_name_review": prepared,
        "prepared_name_review_fingerprint": "same",
    }

    assert reconcile_prepared_review(state, True, "same") is prepared
    assert reconcile_prepared_review(state, False, "same") is None


def test_custom_excluded_agent_persists_as_option_and_selection():
    options, selected = merge_custom_excluded_agent(["Default"], ["Default"], "My Agent")

    assert options == ["Default", "My Agent"]
    assert selected == ["Default", "My Agent"]
    assert merge_custom_excluded_agent(options, selected, " My Agent ") == (options, selected)


def test_final_results_survive_unchanged_passive_rerun():
    result = object()
    state = {
        "final_results": result,
        "final_results_review_fingerprint": "upload-a",
        "final_results_mutation_fingerprint": "board-a",
    }

    assert reconcile_final_results(state, "upload-a", "board-a") is result
    assert state["final_results"] is result


def test_mutated_results_are_cleared_and_do_not_reappear_after_revert():
    result = object()
    state = {
        "final_results": result,
        "final_results_review_fingerprint": "upload-a",
        "final_results_mutation_fingerprint": "board-a",
    }

    assert reconcile_final_results(state, "upload-a", "board-b") is None
    assert reconcile_final_results(state, "upload-a", "board-a") is None
    assert "final_results" not in state


def test_clear_final_results_removes_result_and_all_binding_metadata():
    state = {
        "final_results": object(),
        "final_results_review_fingerprint": "upload-a",
        "final_results_mutation_fingerprint": "board-a",
        "unrelated": "keep",
    }

    clear_final_results(state)

    assert state == {"unrelated": "keep"}
