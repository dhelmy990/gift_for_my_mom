import pandas as pd
import pytest

from company_names.models import Group, NameRecord, ReviewBoard
from company_names.repository import ExportRow, RepositoryUnavailableError
from company_names.review_session import clear_final_results
from company_names.service import (
    AuthAttemptState,
    PreparedReview,
    ensure_submission_identity,
    export_backup_csv,
    password_matches,
    submit_review_authorized,
)


class Embedder:
    def __init__(self, fail=False):
        self.fail = fail

    def embed(self, texts):
        if self.fail:
            raise RuntimeError("secret embedding detail")
        return [[1.0] * 384 for _ in texts]


class Repository:
    def __init__(self, responses=None, export=None):
        self.responses = None if responses is None else list(responses)
        self.submissions = []
        self.export = export or []
        self.export_calls = 0

    def submit(self, payload):
        self.submissions.append(payload)
        response = (
            {group["id"]: "22222222-2222-4222-8222-222222222222" for group in payload.groups if not group["existing"]}
            if self.responses is None
            else self.responses.pop(0)
        )
        if isinstance(response, Exception):
            raise response
        return response

    def export_rows(self):
        self.export_calls += 1
        if isinstance(self.export, Exception):
            raise self.export
        return self.export


def prepared_review(*, grouped=True):
    group_id = "new-local"
    board = ReviewBoard(
        {group_id: Group(group_id, "Acme Group", False)},
        {
            "Acme": NameRecord(
                "Acme", group_id if grouped else None, "unknown", selected=True
            )
        },
    )
    rows = pd.DataFrame([{"cleaned_name": "Acme", "rns": 2.0, "revenue": 8.0}])
    return PreparedReview(board, {}, {}, rows, [], "11111111-1111-4111-8111-111111111111")


def test_password_matches_is_constant_time_compatible_and_non_strings_are_false():
    assert password_matches("correct", "correct") is True
    assert password_matches("wrong", "correct") is False
    assert password_matches(None, "correct") is False
    assert password_matches("correct", None) is False


def test_wrong_or_missing_password_prevents_repository_call():
    for candidate, expected in [("wrong", "correct"), ("correct", None)]:
        repo = Repository()
        outcome = submit_review_authorized(
            prepared_review(), repo, Embedder(), candidate, expected
        )
        assert not outcome.success
        assert outcome.result is None
        assert len(repo.submissions) == 0


def test_authorized_working_tray_returns_actionable_error_without_repository_call():
    prepared = prepared_review(grouped=False)
    prepared.board.groups["blank"] = Group("blank", "", False)
    prepared.board.names["Other"] = NameRecord("Other", None, "unknown", selected=True)
    repo = Repository()

    outcome = submit_review_authorized(prepared, repo, Embedder(), "pw", "pw")

    assert not outcome.success
    assert outcome.error == (
        "Resolve 2 names in the working tray: create a combined group or "
        "return them to Separate companies."
    )
    assert repo.submissions == []


def test_authorized_valid_submission_is_one_rpc_and_returns_totals():
    prepared = prepared_review()
    repo = Repository([{"new-local": "22222222-2222-4222-8222-222222222222"}])

    outcome = submit_review_authorized(prepared, repo, Embedder(), "pw", "pw")

    assert outcome.success
    assert len(repo.submissions) == 1
    assert outcome.result.to_dict("records") == [
        {"TRAVEL AGENT": "Acme Group", "Sum of RNS": 2.0, "Sum of R REVENUE": 8.0}
    ]


@pytest.mark.parametrize("response", [
    {},
    {"new-local": "22222222-2222-4222-8222-222222222222", "extra": "33333333-3333-4333-8333-333333333333"},
    {"new-local": "NOT-A-UUID"},
])
def test_committed_response_must_exactly_resolve_new_groups_without_mutation(response):
    prepared = prepared_review()
    board = prepared.board
    request_id = prepared.pending_request_id
    outcome = submit_review_authorized(prepared, Repository([response]), Embedder(), "pw", "pw")

    assert not outcome.success
    assert outcome.error == "Submission committed but response could not be reconciled; retry."
    assert prepared.board is board
    assert prepared.board.names["Acme"].group_id == "new-local"
    assert prepared.original_mappings == {}
    assert prepared.pending_request_id == request_id


def test_committed_response_rejects_duplicate_or_existing_group_ids_atomically():
    prepared = prepared_review()
    prepared.board.groups["other-local"] = Group("other-local", "Other", False)
    prepared.board.groups["44444444-4444-4444-8444-444444444444"] = Group(
        "44444444-4444-4444-8444-444444444444", "Existing", True
    )
    duplicate = "22222222-2222-4222-8222-222222222222"
    for response in (
        {"new-local": duplicate, "other-local": duplicate},
        {"new-local": "44444444-4444-4444-8444-444444444444", "other-local": duplicate},
    ):
        outcome = submit_review_authorized(prepared, Repository([response]), Embedder(), "pw", "pw")
        assert not outcome.success
        assert set(prepared.board.groups) == {
            "new-local", "other-local", "44444444-4444-4444-8444-444444444444"
        }


def test_bad_committed_response_retries_same_request_and_recovers():
    prepared = prepared_review()
    resolved = "22222222-2222-4222-8222-222222222222"
    repo = Repository([{}, {"new-local": resolved}])
    first = submit_review_authorized(prepared, repo, Embedder(), "pw", "pw")
    second = submit_review_authorized(prepared, repo, Embedder(), "pw", "pw")
    assert not first.success and second.success
    assert repo.submissions[0].request_id == repo.submissions[1].request_id


def test_auth_attempt_state_locks_after_five_failures_and_success_clears():
    state = AuthAttemptState()
    for now in range(4):
        assert state.record_failure(float(now)) == 0
    assert state.record_failure(4.0) == 60
    assert state.retry_after(34.0) == 30
    assert state.retry_after(64.0) == 0
    state.record_success()
    assert state.failure_count == 0 and state.retry_after(64.0) == 0


def test_failure_preserves_board_result_and_request_id_for_unchanged_retry():
    prepared = prepared_review()
    before = prepared.board.names["Acme"].group_id
    request_id = prepared.pending_request_id
    repo = Repository([RepositoryUnavailableError("database password leaked"), {"new-local": "22222222-2222-4222-8222-222222222222"}])

    failed = submit_review_authorized(prepared, repo, Embedder(), "pw", "pw")
    assert prepared.board.names["Acme"].group_id == before
    retried = submit_review_authorized(prepared, repo, Embedder(), "pw", "pw")

    assert not failed.success and failed.result is None
    assert "password" not in failed.error
    assert [payload.request_id for payload in repo.submissions] == [request_id, request_id]
    assert retried.success


def test_failed_resubmission_does_not_leave_prior_final_results():
    state = {
        "final_results": pd.DataFrame([{"TRAVEL AGENT": "Old"}]),
        "final_results_review_fingerprint": "upload",
        "final_results_mutation_fingerprint": "old-board",
    }
    repo = Repository([RepositoryUnavailableError("lost")])

    clear_final_results(state)
    outcome = submit_review_authorized(
        prepared_review(), repo, Embedder(), "pw", "pw"
    )

    assert not outcome.success
    assert "final_results" not in state


def test_changed_board_after_failure_gets_new_request_id():
    prepared = prepared_review()
    repo = Repository([RepositoryUnavailableError("lost"), {"new-local": "22222222-2222-4222-8222-222222222222"}])
    submit_review_authorized(prepared, repo, Embedder(), "pw", "pw")
    old_id = prepared.pending_request_id

    prepared.board.groups["new-local"].canonical_title = "Renamed"
    submit_review_authorized(prepared, repo, Embedder(), "pw", "pw")

    assert repo.submissions[0].request_id == old_id
    assert repo.submissions[1].request_id != old_id


def test_ensure_submission_identity_reuses_unchanged_and_rotates_changed_board():
    prepared = prepared_review()
    first = ensure_submission_identity(prepared)
    assert ensure_submission_identity(prepared) == first
    prepared.board.names["Acme"].selected = False
    assert ensure_submission_identity(prepared) != first


def test_response_loss_retry_uses_same_id_and_can_receive_committed_result():
    class CommitThenLose(Repository):
        def __init__(self):
            super().__init__()
            self.committed = {}

        def submit(self, payload):
            self.submissions.append(payload)
            if payload.request_id not in self.committed:
                self.committed[payload.request_id] = {
                    "new-local": "22222222-2222-4222-8222-222222222222"
                }
                raise RepositoryUnavailableError("response lost")
            return self.committed[payload.request_id]

    prepared = prepared_review()
    repo = CommitThenLose()
    first = submit_review_authorized(prepared, repo, Embedder(), "pw", "pw")
    second = submit_review_authorized(prepared, repo, Embedder(), "pw", "pw")
    assert not first.success and second.success
    assert repo.submissions[0].request_id == repo.submissions[1].request_id


def test_inventory_removes_original_mapping_but_exclusion_does_not():
    prepared = prepared_review()
    prepared.original_mappings = {"Acme": "old"}
    prepared.board.groups = {"old": Group("old", "Acme Group", True)}
    record = prepared.board.names["Acme"]
    record.selected = False
    record.group_id = None
    repo = Repository()
    assert submit_review_authorized(prepared, repo, Embedder(), "pw", "pw").success
    assert repo.submissions[0].unmap_names == ["Acme"]

    prepared = prepared_review()
    prepared.original_mappings = {"Acme": "new-local"}
    record = prepared.board.names["Acme"]
    record.excluded = True
    record.group_id = None
    repo = Repository()
    assert submit_review_authorized(prepared, repo, Embedder(), "pw", "pw").success
    assert repo.submissions[0].unmap_names == []


def test_backup_requires_authorization_and_has_stable_safe_csv():
    repo = Repository(export=[ExportRow("Zulu", "z"), ExportRow("Alpha", "a")])
    denied = export_backup_csv(repo, "bad", "pw")
    assert denied.data is None and repo.export_calls == 0

    allowed = export_backup_csv(repo, "pw", "pw")
    assert allowed.error is None
    assert allowed.data.decode("utf-8") == (
        "cleaned_name,canonical_title\r\na,Alpha\r\nz,Zulu\r\n"
    )
    assert repo.export_calls == 1


def test_backup_neutralizes_spreadsheet_formulas_including_leading_whitespace():
    import csv
    import io

    from company_names.csv_safety import CSV_SAFE_PREFIX, csv_unsafe_cell

    repo = Repository(export=[ExportRow(" =SUM(A1:A2)", "+cmd")])
    allowed = export_backup_csv(repo, "pw", "pw")
    rows = list(csv.reader(io.StringIO(allowed.data.decode("utf-8"))))
    assert rows[0] == ["cleaned_name", "canonical_title"]
    assert len(rows[1]) == 2
    assert all(cell.startswith(CSV_SAFE_PREFIX) for cell in rows[1])
    assert [csv_unsafe_cell(cell) for cell in rows[1]] == ["+cmd", " =SUM(A1:A2)"]


def test_backup_repository_failure_is_sanitized():
    repo = Repository(export=RepositoryUnavailableError("service key leaked"))
    outcome = export_backup_csv(repo, "pw", "pw")
    assert outcome.data is None
    assert "key" not in outcome.error


def test_success_resolves_temporary_ids_without_losing_names_or_groups():
    prepared = prepared_review()
    prepared.board.groups["existing"] = Group("existing", "Other", True)
    resolved = "22222222-2222-4222-8222-222222222222"
    outcome = submit_review_authorized(
        prepared, Repository([{"new-local": resolved}]), Embedder(), "pw", "pw"
    )
    assert outcome.success
    assert set(prepared.board.groups) == {resolved, "existing"}
    assert prepared.board.groups[resolved].id == resolved
    assert prepared.board.names["Acme"].group_id == resolved
    assert prepared.original_mappings["Acme"] == resolved


def test_success_marks_a_new_alias_with_its_persisted_identity():
    prepared = prepared_review()
    resolved = "22222222-2222-4222-8222-222222222222"

    outcome = submit_review_authorized(
        prepared, Repository([{"new-local": resolved}]), Embedder(), "pw", "pw"
    )

    assert outcome.success
    assert prepared.board.names["Acme"].persisted_name == "Acme"


def test_embedding_failure_submits_without_vectors_with_warning():
    prepared = prepared_review()
    repo = Repository()
    outcome = submit_review_authorized(prepared, repo, Embedder(fail=True), "pw", "pw")
    assert outcome.success
    assert outcome.warning
    assert "title_embedding" not in repo.submissions[0].groups[0]
