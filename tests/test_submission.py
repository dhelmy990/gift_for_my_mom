import pandas as pd

from company_names.models import Group, NameRecord, ReviewBoard
from company_names.repository import ExportRow, RepositoryUnavailableError
from company_names.service import (
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
        self.responses = list(responses or [{}])
        self.submissions = []
        self.export = export or []
        self.export_calls = 0

    def submit(self, payload):
        self.submissions.append(payload)
        response = self.responses.pop(0)
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


def test_authorized_invalid_board_returns_all_errors_without_repository_call():
    prepared = prepared_review(grouped=False)
    prepared.board.groups["blank"] = Group("blank", "", False)
    prepared.board.names["Other"] = NameRecord("Other", None, "unknown", selected=True)
    repo = Repository()

    outcome = submit_review_authorized(prepared, repo, Embedder(), "pw", "pw")

    assert not outcome.success
    assert "Acme is included but ungrouped" in outcome.error
    assert "Other is included but ungrouped" in outcome.error
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


def test_failure_preserves_board_result_and_request_id_for_unchanged_retry():
    prepared = prepared_review()
    before = prepared.board.names["Acme"].group_id
    request_id = prepared.pending_request_id
    repo = Repository([RepositoryUnavailableError("database password leaked"), {}])

    failed = submit_review_authorized(prepared, repo, Embedder(), "pw", "pw")
    retried = submit_review_authorized(prepared, repo, Embedder(), "pw", "pw")

    assert not failed.success and failed.result is None
    assert "password" not in failed.error
    assert prepared.board.names["Acme"].group_id == before
    assert [payload.request_id for payload in repo.submissions] == [request_id, request_id]
    assert retried.success


def test_changed_board_after_failure_gets_new_request_id():
    prepared = prepared_review()
    repo = Repository([RepositoryUnavailableError("lost"), {}])
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


def test_embedding_failure_submits_without_vectors_with_warning():
    prepared = prepared_review()
    repo = Repository()
    outcome = submit_review_authorized(prepared, repo, Embedder(fail=True), "pw", "pw")
    assert outcome.success
    assert outcome.warning
    assert "title_embedding" not in repo.submissions[0].groups[0]
