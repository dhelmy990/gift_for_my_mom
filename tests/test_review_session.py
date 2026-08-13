from dataclasses import dataclass

from company_names.review_session import (
    compute_upload_fingerprint,
    merge_custom_excluded_agent,
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
